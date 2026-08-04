"""The LLMClient interface every provider implementation satisfies."""

from __future__ import annotations

from abc import ABC, abstractmethod


class StructuredOutputError(RuntimeError):
    """The model could not produce schema-conformant output, even after one
    repair retry. The message names the provider and model so a weaker model
    fails loudly and identifiably."""

    def __init__(self, provider: str, model: str, tool_name: str, errors: list[str]):
        self.provider = provider
        self.model = model
        detail = "; ".join(errors) if errors else "unknown validation failure"
        super().__init__(
            f"{provider}/{model} failed to produce valid structured output for "
            f"'{tool_name}' after a repair retry: {detail}"
        )


class UnsupportedFeatureError(RuntimeError):
    """The active provider cannot perform the requested operation."""


class LLMClient(ABC):
    """Uniform interface over chat-completion providers.

    Both methods accept an optional ``tracker`` (fitgap.usage.UsageTracker)
    and a ``stage`` label so token usage keeps flowing into the existing
    cost summary regardless of provider.
    """

    provider: str
    model: str

    @abstractmethod
    def structured(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        schema: dict,
        max_tokens: int = 8192,
        stage: str = "llm",
        tracker=None,
        cache_prompt: bool = True,
    ) -> dict:
        """Return a dict conforming to ``schema``.

        ``cache_prompt`` requests prompt caching of the fixed tools+system
        prefix where the provider takes an explicit breakpoint (Anthropic);
        providers that cache automatically ignore it. Caching never changes
        what the model returns — only what the prefix costs.

        Implementations must validate the response against the schema, make
        exactly one repair retry on failure, and raise StructuredOutputError
        (naming the model) if the retry also fails.
        """

    @abstractmethod
    def learn_search(
        self,
        *,
        system: str,
        user: str,
        verify,  # fitgap.config.VerifyConfig (mode + cost knobs)
        max_tokens: int = 2048,
        stage: str = "verify",
        tracker=None,
    ) -> str:
        """Run the Microsoft-Learn-grounded verification call and return the
        model's final text. ``verify`` carries the mode (mcp/web_search) and
        the cost knobs (cache_prompt, search_only, model override,
        max_searches). Raises UnsupportedFeatureError on providers without
        live Learn search support (currently everything except Anthropic,
        whose API offers the MCP connector / web-search tools)."""


def as_llm_client(client, model: str) -> LLMClient:
    """Accept either an LLMClient or a raw Anthropic SDK client (or test
    double exposing ``.messages.create``) and return an LLMClient."""
    if isinstance(client, LLMClient):
        return client
    from fitgap.llm.anthropic_client import AnthropicClient

    return AnthropicClient(model=model, sdk_client=client)
