"""Build the concrete LLMClient for the active provider/model."""

from __future__ import annotations

from fitgap.llm.base import LLMClient
from fitgap.llm.registry import get_provider


def make_client(provider: str, model: str, api_key: str) -> LLMClient:
    spec = get_provider(provider)
    if spec.sdk == "anthropic":
        from fitgap.llm.anthropic_client import AnthropicClient

        return AnthropicClient(model=model, api_key=api_key)
    from fitgap.llm.openai_compat import OpenAICompatClient

    return OpenAICompatClient(spec, model, api_key=api_key)
