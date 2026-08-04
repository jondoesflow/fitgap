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
    #: Model that actually served these calls, read back from the response.
    #: Stages can run on different models (see VerifyConfig.model), so pricing
    #: must follow the stage rather than one run-wide model. None when unknown
    #: or when a stage mixed models, in which case the caller's model is used.
    model: str | None = None

    def add_usage(self, usage, model: str | None = None) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += (
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        self._note_model(model)

    def _note_model(self, model: str | None) -> None:
        if model is None:
            return
        if self.model is None and self.calls <= 1:
            self.model = model
        elif self.model != model:
            self.model = None  # mixed models — fall back to the caller's

    def merge(self, other: "StageUsage") -> None:
        merged_model = other.model if self.calls == 0 else self.model
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.model = merged_model if merged_model == other.model else None

    def cost_usd(self, model: str) -> float | None:
        prices = lookup_pricing(self.model or model)
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


def _cache_note(usage: "StageUsage") -> str:
    """Cache detail for a summary line: reads are the saving, writes are the
    ~1.25x premium paid to create the entry — showing both makes it obvious
    whether caching is working or just costing."""
    parts = []
    if usage.cache_read_tokens:
        parts.append(f"{usage.cache_read_tokens:,} cached")
    if usage.cache_write_tokens:
        parts.append(f"{usage.cache_write_tokens:,} cache-write")
    return f" ({', '.join(parts)})" if parts else ""


class UsageTracker:
    """Accumulates per-stage usage across the API calls of one CLI invocation."""

    def __init__(self) -> None:
        self.stages: dict[str, StageUsage] = {}

    def record(self, stage: str, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.stages.setdefault(stage, StageUsage()).add_usage(
            usage, getattr(response, "model", None)
        )

    def merge(self, other: "UsageTracker") -> None:
        for stage, stage_usage in other.stages.items():
            self.stages.setdefault(stage, StageUsage()).merge(stage_usage)

    def total(self) -> StageUsage:
        total = StageUsage()
        for stage_usage in self.stages.values():
            total.merge(stage_usage)
        return total

    def _cache_diagnostics(self, model: str) -> list[str]:
        """Explain prompt caching when it did not pay off.

        Caching is easy to enable and easy to have silently do nothing: a
        prefix below the model's minimum is not cached, with no error. Rather
        than leave that invisible, say which case a stage is in.
        """
        from fitgap.llm.caching import cache_minimum

        lines = []
        for stage, usage in self.stages.items():
            if usage.calls < 2 or usage.cache_read_tokens:
                continue  # nothing to reuse yet, or caching already working
            served = usage.model or model
            if usage.cache_write_tokens:
                lines.append(
                    f"  ({stage}: wrote {usage.cache_write_tokens:,} cache token(s) "
                    "but read none back — something changes the prefix between "
                    "calls)"
                )
            else:
                lines.append(
                    f"  ({stage}: no prompt caching over {usage.calls} calls — the "
                    f"cached prefix is likely below {served}'s "
                    f"{cache_minimum(served):,}-token minimum)"
                )
        return lines

    def summary_lines(self, model: str) -> list[str]:
        """Human-readable cost summary; one line per stage plus a total."""
        if not self.stages:
            return []
        lines = []
        costs = []
        for stage, stage_usage in self.stages.items():
            cost = stage_usage.cost_usd(model)
            costs.append(cost)
            cost_text = f"${cost:,.4f}" if cost is not None else "cost unknown"
            # Name the model only when the stage did not use the run's model.
            served_by = stage_usage.model
            suffix = f" [{served_by}]" if served_by and served_by != model else ""
            lines.append(
                f"  {stage}: {cost_text}  ({stage_usage.calls} call(s), "
                f"{stage_usage.total_input:,} in{_cache_note(stage_usage)} / "
                f"{stage_usage.output_tokens:,} out tokens){suffix}"
            )
        if len(self.stages) > 1:
            total = self.total()
            # Sum per-stage costs: stages may run on different models, so the
            # merged token counts cannot be priced with a single rate.
            cost = None if any(c is None for c in costs) else sum(costs)
            cost_text = f"${cost:,.4f}" if cost is not None else "cost unknown"
            lines.append(
                f"  TOTAL: {cost_text}  ({total.calls} call(s), "
                f"{total.total_input:,} in{_cache_note(total)} / "
                f"{total.output_tokens:,} out tokens)"
            )
        lines.extend(self._cache_diagnostics(model))
        if any(c is None for c in costs):
            unpriced = sorted(
                {u.model or model for u, c in zip(self.stages.values(), costs) if c is None}
            )
            lines.append(
                f"  (no pricing table entry for {', '.join(repr(m) for m in unpriced)}"
                " — token counts only)"
            )
        return lines
