"""Prompt caching: breakpoint placement, provider handling, and reporting."""

import json
from types import SimpleNamespace

from fakes import FakeAnthropic, FakeOpenAI, chat_tool_call_message, tool_use_block
from test_classify import RULES, classification_entry, echo_responder, make_workspace

from fitgap.classify import Classifier
from fitgap.config import Config
from fitgap.llm.caching import cache_minimum, cached_system
from fitgap.llm.openai_compat import OpenAICompatClient
from fitgap.llm.registry import PROVIDERS
from fitgap.usage import UsageTracker

SIMPLE_SCHEMA = {"type": "object", "properties": {}, "required": []}


# ------------------------------------------------------------ minimum lookup


def test_cache_minimum_per_model_family():
    assert cache_minimum("claude-opus-5") == 512
    assert cache_minimum("claude-sonnet-4-6") == 1024
    assert cache_minimum("claude-opus-4-7") == 2048
    assert cache_minimum("claude-haiku-4-5") == 4096


def test_cache_minimum_matches_dated_snapshots_and_falls_back():
    assert cache_minimum("claude-haiku-4-5-20251001") == 4096
    assert cache_minimum("some-future-model") == 1024  # documented default


def test_cached_system_is_a_no_op_when_disabled():
    assert cached_system("prompt", False) == "prompt"


# ------------------------------------------------------- breakpoint placement


def test_classify_caches_the_tools_and_system_prefix():
    fake = FakeAnthropic(echo_responder)
    Classifier(Config(), RULES, fake).classify_workspace(make_workspace(3))
    system = fake.calls[0]["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # The breakpoint sits after the tools in render order, so the tool schema
    # is cached with it; the varying payload stays in messages, after it.
    assert fake.calls[0]["tools"][0]["name"] == "record_classifications"
    assert "cache_control" not in json.dumps(fake.calls[0]["messages"])


def test_classify_caching_can_be_switched_off():
    config = Config.model_validate({"llm": {"cache_prompt": False}})
    fake = FakeAnthropic(echo_responder)
    Classifier(config, RULES, fake).classify_workspace(make_workspace(2))
    assert isinstance(fake.calls[0]["system"], str)


def test_batches_share_one_prefix_so_the_cache_can_be_reused():
    """Every batch must send a byte-identical cached prefix."""
    fake = FakeAnthropic(echo_responder)
    Classifier(Config(), RULES, fake, batch_size=2).classify_workspace(
        make_workspace(6)
    )
    assert len(fake.calls) == 3
    prefixes = {
        json.dumps([call["system"], call["tools"]], sort_keys=True)
        for call in fake.calls
    }
    assert len(prefixes) == 1  # identical prefix => cache hits from call 2 on
    # ...while the per-batch payload does differ, as it must.
    assert len({json.dumps(c["messages"]) for c in fake.calls}) == 3


def test_transcript_extraction_caches_its_prefix(tmp_path):
    from fitgap.ingest.transcript import TranscriptExtractor
    from fitgap.redact import RedactionRules

    path = tmp_path / "workshop.txt"
    path.write_text(
        "[00:01:00] Ops Lead: We need approvals on large quotes.\n", encoding="utf-8"
    )
    fake = FakeAnthropic(
        lambda kwargs: [tool_use_block("record_extracted_requirements", {"requirements": []})]
    )
    TranscriptExtractor(Config(), RedactionRules(), fake).extract(path)
    assert fake.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_compat_providers_never_receive_anthropic_cache_control():
    """OpenAI-compatible endpoints cache automatically and would reject it."""
    fake = FakeOpenAI(lambda kwargs: chat_tool_call_message({}))
    OpenAICompatClient(PROVIDERS["deepseek"], "deepseek-chat", sdk_client=fake).structured(
        system="s",
        user="u",
        tool_name="t",
        tool_description="d",
        schema=SIMPLE_SCHEMA,
        cache_prompt=True,
    )
    assert isinstance(fake.calls[0]["messages"][0]["content"], str)
    assert "cache_control" not in json.dumps(fake.calls[0])


# ----------------------------------------------------------------- reporting


def openai_response(prompt_tokens, cached, completion_tokens=10):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=chat_tool_call_message({}))],
        model="gpt-5.1",
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


def test_compat_cached_tokens_are_reported_without_double_counting():
    tracker = UsageTracker()
    OpenAICompatClient._record("classify", tracker, openai_response(1000, 800))
    stage = tracker.stages["classify"]
    assert stage.cache_read_tokens == 800
    assert stage.input_tokens == 200  # prompt_tokens counts cached inside it
    assert stage.total_input == 1000  # so the total still equals the prompt


def test_compat_usage_without_cache_details_still_records():
    tracker = UsageTracker()
    response = openai_response(500, 0)
    response.usage = SimpleNamespace(prompt_tokens=500, completion_tokens=10)
    OpenAICompatClient._record("classify", tracker, response)
    assert tracker.stages["classify"].input_tokens == 500
    assert tracker.stages["classify"].cache_read_tokens == 0


def anthropic_response(model, input_tokens=0, cache_read=0, cache_write=0):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=10,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def test_summary_reports_cache_reads_and_writes():
    tracker = UsageTracker()
    tracker.record("classify", anthropic_response("claude-sonnet-4-6", 100, cache_write=900))
    tracker.record("classify", anthropic_response("claude-sonnet-4-6", 100, cache_read=900))
    line = tracker.summary_lines("claude-sonnet-4-6")[0]
    assert "900 cached" in line
    assert "900 cache-write" in line


def test_summary_explains_when_caching_never_engaged():
    """The silent no-op that cost us an eval run must now be visible."""
    tracker = UsageTracker()
    for _ in range(3):
        tracker.record("verify", anthropic_response("claude-haiku-4-5", 5000))
    note = "\n".join(tracker.summary_lines("claude-haiku-4-5"))
    assert "no prompt caching over 3 calls" in note
    assert "4,096-token minimum" in note  # names the model's actual threshold


def test_summary_flags_a_prefix_that_keeps_being_invalidated():
    tracker = UsageTracker()
    for _ in range(3):
        tracker.record(
            "classify", anthropic_response("claude-sonnet-4-6", 100, cache_write=900)
        )
    note = "\n".join(tracker.summary_lines("claude-sonnet-4-6"))
    assert "read none back" in note


def test_no_diagnostic_when_caching_works_or_on_a_single_call():
    working = UsageTracker()
    working.record("classify", anthropic_response("claude-opus-5", 100, cache_write=900))
    working.record("classify", anthropic_response("claude-opus-5", 100, cache_read=900))
    assert not any("no prompt caching" in line for line in working.summary_lines("claude-opus-5"))

    single = UsageTracker()
    single.record("classify", anthropic_response("claude-sonnet-4-6", 100))
    assert not any("no prompt caching" in line for line in single.summary_lines("claude-sonnet-4-6"))
