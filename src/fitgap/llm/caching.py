"""Prompt-caching helpers.

Caching is a *prefix* match: the request is rendered as tools -> system ->
messages, so a ``cache_control`` breakpoint on the last system block caches
the tool definitions and the system prompt together. Everything that varies
per call (the batch payload, the claim under verification) lives in
``messages``, after the breakpoint, so it never invalidates the prefix.

Caching cannot change what the model returns — it only changes what the
prefix costs (cache writes ~1.25x input, reads ~0.1x). It is therefore safe
to leave on by default.

The one trap is the **minimum cacheable prefix**: a prefix shorter than the
model's minimum is silently not cached — no error, just no savings. The
minimum is not monotonic across generations (512 on the newest models, 4096
on Haiku 4.5), so it has to be looked up per model rather than assumed.
"""

from __future__ import annotations

#: Minimum cacheable prefix, in tokens, keyed by model-id prefix.
#: See https://platform.claude.com/docs/en/build-with-claude/prompt-caching
CACHE_MINIMUM_TOKENS: dict[str, int] = {
    "claude-fable-5": 512,
    "claude-mythos-5": 512,
    "claude-opus-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-sonnet-4-5": 1024,
    "claude-opus-4-1": 1024,
    "claude-opus-4-0": 1024,
    "claude-sonnet-4-0": 1024,
    "claude-opus-4-7": 2048,
    "claude-opus-4-6": 4096,
    "claude-opus-4-5": 4096,
    "claude-haiku-4-5": 4096,
}

#: Fallback when the model is unknown to the table above — the most common
#: minimum, so the advice we print is right more often than not.
DEFAULT_CACHE_MINIMUM = 1024


def cache_minimum(model: str) -> int:
    """Minimum cacheable prefix for ``model``, matched by id prefix so dated
    snapshots (claude-haiku-4-5-20251001) resolve like their alias."""
    if model in CACHE_MINIMUM_TOKENS:
        return CACHE_MINIMUM_TOKENS[model]
    for key, minimum in CACHE_MINIMUM_TOKENS.items():
        if model.startswith(key):
            return minimum
    return DEFAULT_CACHE_MINIMUM


def cached_system(system: str, enabled: bool):
    """Return the ``system`` parameter, with a cache breakpoint when enabled.

    The breakpoint goes on the last (only) system block, which caches the
    tool definitions rendered before it as well.
    """
    if not enabled:
        return system
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
