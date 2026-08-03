"""Provider-agnostic LLM access layer.

The pipeline stages (transcript extraction, classification, verification)
talk to a single :class:`LLMClient` interface and never import a provider
SDK directly. Concrete clients live next to this module; the provider
registry maps ``provider/model`` strings onto them.
"""

from fitgap.llm.base import (
    LLMClient,
    StructuredOutputError,
    UnsupportedFeatureError,
    as_llm_client,
)

__all__ = [
    "LLMClient",
    "StructuredOutputError",
    "UnsupportedFeatureError",
    "as_llm_client",
]
