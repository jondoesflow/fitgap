from types import SimpleNamespace

from fakes import FakeAnthropic
from test_classify import RULES, echo_responder, make_workspace

from fitgap.classify import Classifier
from fitgap.config import Config
from fitgap.usage import StageUsage, UsageTracker, lookup_pricing


def response(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        )
    )


def test_cost_math_haiku():
    tracker = UsageTracker()
    tracker.record("classify", response(input_tokens=1_000_000, output_tokens=100_000))
    # Haiku: $1/MTok in + $5/MTok out -> $1.00 + $0.50
    assert tracker.total().cost_usd("claude-haiku-4-5") == 1.50


def test_cache_tokens_priced_at_multipliers():
    tracker = UsageTracker()
    tracker.record(
        "classify",
        response(input_tokens=0, output_tokens=0, cache_read=1_000_000, cache_write=1_000_000),
    )
    # Sonnet input $3: read 0.1x = $0.30, write 1.25x = $3.75
    assert tracker.total().cost_usd("claude-sonnet-4-6") == 4.05


def test_dated_model_id_matches_by_prefix():
    assert lookup_pricing("claude-haiku-4-5-20251001") == (1.0, 5.0)


def response_from(model, input_tokens=0, output_tokens=0):
    r = response(input_tokens=input_tokens, output_tokens=output_tokens)
    r.model = model
    return r


def test_stage_priced_by_the_model_that_served_it():
    """verify can run on a cheaper model than the rest of the run."""
    tracker = UsageTracker()
    tracker.record("verify", response_from("claude-haiku-4-5", 1_000_000, 100_000))
    stage = tracker.stages["verify"]
    assert stage.model == "claude-haiku-4-5"
    # Priced as Haiku ($1.50) even though the run's model is Sonnet.
    assert stage.cost_usd("claude-sonnet-4-6") == 1.50


def test_mixed_models_in_one_stage_fall_back_to_run_model():
    tracker = UsageTracker()
    tracker.record("verify", response_from("claude-haiku-4-5", 1_000_000))
    tracker.record("verify", response_from("claude-sonnet-4-6", 0))
    assert tracker.stages["verify"].model is None
    assert tracker.stages["verify"].cost_usd("claude-sonnet-4-6") == 3.0


def test_total_sums_per_stage_costs_across_models():
    """A single blended rate would misprice a run that mixes models."""
    tracker = UsageTracker()
    tracker.record("classify", response_from("claude-sonnet-4-6", 1_000_000))
    tracker.record("verify", response_from("claude-haiku-4-5", 1_000_000))
    lines = tracker.summary_lines("claude-sonnet-4-6")
    total_line = [line for line in lines if "TOTAL" in line][0]
    # $3.00 (Sonnet classify) + $1.00 (Haiku verify), NOT 2M x Sonnet = $6.00
    assert "$4.0000" in total_line
    # The stage that differs from the run model is labelled.
    assert any("[claude-haiku-4-5]" in line for line in lines)


def test_unpriced_model_named_in_note():
    tracker = UsageTracker()
    tracker.record("verify", response_from("some-future-model", 1_000))
    lines = tracker.summary_lines("claude-sonnet-4-6")
    assert any("some-future-model" in line for line in lines)
    assert lookup_pricing("some-future-model") is None


def test_unknown_model_reports_tokens_without_cost():
    tracker = UsageTracker()
    tracker.record("classify", response(input_tokens=500, output_tokens=50))
    lines = tracker.summary_lines("some-future-model")
    assert any("cost unknown" in line for line in lines)
    assert any("no pricing table entry" in line for line in lines)


def test_missing_usage_is_tolerated():
    tracker = UsageTracker()
    tracker.record("verify", SimpleNamespace(content=[]))  # no usage attr
    assert tracker.stages == {}


def test_merge_and_stage_breakdown():
    a, b = UsageTracker(), UsageTracker()
    a.record("extract", response(input_tokens=100, output_tokens=10))
    b.record("classify", response(input_tokens=200, output_tokens=20))
    b.record("classify", response(input_tokens=200, output_tokens=20))
    a.merge(b)
    assert a.stages["extract"].calls == 1
    assert a.stages["classify"].calls == 2
    total = a.total()
    assert total.input_tokens == 500
    assert total.output_tokens == 50
    lines = a.summary_lines("claude-haiku-4-5")
    # Cost lines are the stages plus TOTAL; parenthetical notes (pricing gaps,
    # cache diagnostics) are advisory and may or may not be present.
    cost_lines = [line for line in lines if not line.lstrip().startswith("(")]
    assert len(cost_lines) == 3  # two stages + TOTAL
    assert cost_lines[-1].startswith("  TOTAL:")


def test_classifier_records_usage_per_batch():
    tracker = UsageTracker()
    workspace = make_workspace(12)
    Classifier(
        Config(), RULES, FakeAnthropic(echo_responder), batch_size=5, usage_tracker=tracker
    ).classify_workspace(workspace)
    stage = tracker.stages["classify"]
    assert stage.calls == 3
    assert stage.input_tokens == 3000  # fake returns 1000 in / 200 out per call
    assert stage.output_tokens == 600
