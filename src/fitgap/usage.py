"""Track Anthropic API token usage and compute the cost of a run.

Every API response carries exact token counts in ``response.usage``; stages
record them here and the CLI prints a per-stage + total cost summary. Prices
are list prices per million tokens — update PRICING_PER_MTOK when Anthropic
pricing changes (see https://platform.claude.com/docs/en/pricing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: (input $/MTok, output $/MTok). Cache reads bill at ~0.1x input,
#: cache writes at ~1.25x input (5-minute TTL).
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


def lookup_pricing(model: str) -> tuple[float, float] | None:
    if model in PRICING_PER_MTOK:
        return PRICING_PER_MTOK[model]
    for key, prices in PRICING_PER_MTOK.items():
        if model.startswith(key):
            return prices
    return None


@dataclass
class StageUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add_usage(self, usage) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += (
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )

    def merge(self, other: "StageUsage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    def cost_usd(self, model: str) -> float | None:
        prices = lookup_pricing(model)
        if prices is None:
            return None
        input_price, output_price = prices
        return (
            self.input_tokens * input_price
            + self.cache_read_tokens * input_price * CACHE_READ_MULTIPLIER
            + self.cache_write_tokens * input_price * CACHE_WRITE_MULTIPLIER
            + self.output_tokens * output_price
        ) / 1_000_000

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


class UsageTracker:
    """Accumulates per-stage usage across the API calls of one CLI invocation."""

    def __init__(self) -> None:
        self.stages: dict[str, StageUsage] = {}

    def record(self, stage: str, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.stages.setdefault(stage, StageUsage()).add_usage(usage)

    def merge(self, other: "UsageTracker") -> None:
        for stage, stage_usage in other.stages.items():
            self.stages.setdefault(stage, StageUsage()).merge(stage_usage)

    def total(self) -> StageUsage:
        total = StageUsage()
        for stage_usage in self.stages.values():
            total.merge(stage_usage)
        return total

    def summary_lines(self, model: str) -> list[str]:
        """Human-readable cost summary; one line per stage plus a total."""
        if not self.stages:
            return []
        lines = []
        for stage, stage_usage in self.stages.items():
            cost = stage_usage.cost_usd(model)
            cost_text = f"${cost:,.4f}" if cost is not None else "cost unknown"
            lines.append(
                f"  {stage}: {cost_text}  ({stage_usage.calls} call(s), "
                f"{stage_usage.total_input:,} in / {stage_usage.output_tokens:,} out tokens)"
            )
        if len(self.stages) > 1:
            total = self.total()
            cost = total.cost_usd(model)
            cost_text = f"${cost:,.4f}" if cost is not None else "cost unknown"
            lines.append(
                f"  TOTAL: {cost_text}  ({total.calls} call(s), "
                f"{total.total_input:,} in / {total.output_tokens:,} out tokens)"
            )
        if lookup_pricing(model) is None:
            lines.append(
                f"  (no pricing table entry for '{model}' — token counts only)"
            )
        return lines
