"""Registry resolution and provider routing — no live calls anywhere."""

import json

import pytest
from fakes import FakeOpenAI, chat_text_message, chat_tool_call_message

from fitgap.llm import StructuredOutputError, UnsupportedFeatureError
from fitgap.llm.factory import make_client
from fitgap.llm.openai_compat import OpenAICompatClient
from fitgap.llm.registry import (
    PROVIDERS,
    UnknownProviderError,
    get_provider,
    parse_model_string,
)
from fitgap.usage import UsageTracker

LAUNCH_PROVIDERS = ("anthropic", "openai", "deepseek", "kimi", "gemini", "mistral", "xai")

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no"]},
    },
    "required": ["answer"],
}


def test_all_launch_providers_registered():
    assert set(LAUNCH_PROVIDERS) == set(PROVIDERS)


@pytest.mark.parametrize("provider", LAUNCH_PROVIDERS)
def test_model_strings_parse_and_route(provider):
    spec = PROVIDERS[provider]
    parsed_spec, model = parse_model_string(f"{provider}/{spec.default_model}")
    assert parsed_spec is spec
    assert model == spec.default_model


def test_parse_rejects_bad_strings():
    with pytest.raises(ValueError, match="provider"):
        parse_model_string("claude-sonnet-4-6")  # missing provider/
    with pytest.raises(UnknownProviderError, match="grok"):
        parse_model_string("grok/grok-4")  # provider is 'xai', not 'grok'


def test_get_provider_is_case_insensitive():
    assert get_provider("OpenAI").name == "openai"


@pytest.mark.parametrize(
    "provider", [p for p in LAUNCH_PROVIDERS if p != "anthropic"]
)
def test_factory_routes_compat_providers_to_their_base_url(provider):
    spec = PROVIDERS[provider]
    client = make_client(provider, spec.default_model, api_key="test-key-0000")
    assert isinstance(client, OpenAICompatClient)
    assert client.provider == provider
    assert client.model == spec.default_model
    if spec.base_url:  # constructing the SDK client makes no network calls
        assert str(client.sdk.base_url).rstrip("/") == spec.base_url.rstrip("/")


def test_factory_routes_anthropic_to_anthropic_sdk():
    from fitgap.llm.anthropic_client import AnthropicClient

    client = make_client("anthropic", "claude-sonnet-4-6", api_key="test-key-0000")
    assert isinstance(client, AnthropicClient)


def test_compat_structured_output_via_forced_function_call():
    spec = PROVIDERS["deepseek"]
    fake = FakeOpenAI(lambda kwargs: chat_tool_call_message({"answer": "yes"}))
    tracker = UsageTracker()
    client = OpenAICompatClient(spec, "deepseek-chat", sdk_client=fake)
    result = client.structured(
        system="sys",
        user="question",
        tool_name="record_answer",
        tool_description="d",
        schema=SIMPLE_SCHEMA,
        stage="classify",
        tracker=tracker,
    )
    assert result == {"answer": "yes"}
    call = fake.calls[0]
    assert call["tool_choice"]["function"]["name"] == "record_answer"
    assert call["tools"][0]["function"]["parameters"] == SIMPLE_SCHEMA
    assert call["max_tokens"] == 8192
    assert tracker.stages["classify"].input_tokens == 1000


def test_openai_uses_max_completion_tokens():
    spec = PROVIDERS["openai"]
    fake = FakeOpenAI(lambda kwargs: chat_tool_call_message({"answer": "yes"}))
    OpenAICompatClient(spec, "gpt-5.1", sdk_client=fake).structured(
        system="s", user="u", tool_name="t", tool_description="d", schema=SIMPLE_SCHEMA
    )
    assert "max_completion_tokens" in fake.calls[0]
    assert "max_tokens" not in fake.calls[0]


def test_compat_falls_back_to_json_in_content():
    spec = PROVIDERS["mistral"]
    fake = FakeOpenAI(
        lambda kwargs: chat_text_message('Here you go: {"answer": "no"} thanks')
    )
    result = OpenAICompatClient(spec, "mistral-large-latest", sdk_client=fake).structured(
        system="s", user="u", tool_name="t", tool_description="d", schema=SIMPLE_SCHEMA
    )
    assert result == {"answer": "no"}


def test_compat_repair_retry_then_success():
    spec = PROVIDERS["kimi"]
    answers = iter([{"answer": "maybe"}, {"answer": "yes"}])  # invalid enum, then valid
    fake = FakeOpenAI(lambda kwargs: chat_tool_call_message(next(answers)))
    result = OpenAICompatClient(spec, "kimi-latest", sdk_client=fake).structured(
        system="s", user="u", tool_name="t", tool_description="d", schema=SIMPLE_SCHEMA
    )
    assert result == {"answer": "yes"}
    assert len(fake.calls) == 2
    assert "failed validation" in fake.calls[1]["messages"][-1]["content"]


def test_compat_persistent_garbage_fails_naming_the_model():
    spec = PROVIDERS["xai"]
    fake = FakeOpenAI(lambda kwargs: chat_text_message("I will not comply."))
    with pytest.raises(StructuredOutputError, match="xai/grok-4"):
        OpenAICompatClient(spec, "grok-4", sdk_client=fake).structured(
            system="s", user="u", tool_name="t", tool_description="d", schema=SIMPLE_SCHEMA
        )
    assert len(fake.calls) == 2  # original + exactly one repair retry


@pytest.mark.parametrize(
    "provider", [p for p in LAUNCH_PROVIDERS if p != "anthropic"]
)
def test_learn_search_unsupported_outside_anthropic(provider):
    spec = PROVIDERS[provider]
    client = OpenAICompatClient(
        spec, spec.default_model, sdk_client=FakeOpenAI(lambda k: chat_text_message(""))
    )
    with pytest.raises(UnsupportedFeatureError, match="skip-verify"):
        client.learn_search(system="s", user="u", mode="mcp")


def test_classifier_works_through_a_compat_provider():
    """Full classify batch through the OpenAI-compatible path, no Anthropic."""
    from test_classify import RULES, classification_entry, make_workspace

    from fitgap.classify import Classifier
    from fitgap.config import Config

    def responder(kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"].split(":\n\n", 1)[1])
        return chat_tool_call_message(
            {
                "classifications": [
                    classification_entry(item["requirement_id"]) for item in payload
                ]
            }
        )

    config = Config.model_validate(
        {"llm": {"provider": "deepseek", "model": "deepseek-chat"}}
    )
    client = OpenAICompatClient(
        PROVIDERS["deepseek"], "deepseek-chat", sdk_client=FakeOpenAI(responder)
    )
    workspace = make_workspace(3)
    classified, missing = Classifier(config, RULES, client).classify_workspace(workspace)
    assert classified == 3
    assert missing == []
