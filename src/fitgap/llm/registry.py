"""The provider registry — adding a provider is a new entry here, not code.

``known_models`` is advisory: ``fitgap model use`` warns (never blocks) when
a model string is not listed, because new models ship faster than
registries update. Every provider except Anthropic is driven through its
OpenAI-compatible chat-completions endpoint via ``base_url`` switching.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str                # registry key, used in provider/model strings
    label: str               # human-readable name for messages and compliance output
    env_var: str             # environment variable holding the API key
    sdk: str                 # "anthropic" or "openai-compat"
    base_url: str | None     # None = SDK default
    known_models: tuple[str, ...]
    default_model: str
    native_structured: bool  # native structured output (tool/function calling)
    supports_learn_search: bool  # live Microsoft Learn search (MCP/web-search)
    keys_url: str            # where to create an API key
    # OpenAI's newest models reject max_tokens in favour of max_completion_tokens.
    uses_max_completion_tokens: bool = False


PROVIDERS: dict[str, ProviderSpec] = {
    spec.name: spec
    for spec in (
        ProviderSpec(
            name="anthropic",
            label="Anthropic",
            env_var="ANTHROPIC_API_KEY",
            sdk="anthropic",
            base_url=None,
            known_models=(
                "claude-sonnet-4-6",
                "claude-opus-4-6",
                "claude-haiku-4-5",
            ),
            default_model="claude-sonnet-4-6",
            native_structured=True,
            supports_learn_search=True,
            keys_url="https://console.anthropic.com/settings/keys",
        ),
        ProviderSpec(
            name="openai",
            label="OpenAI",
            env_var="OPENAI_API_KEY",
            sdk="openai-compat",
            base_url=None,  # SDK default is api.openai.com
            known_models=("gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-4.1"),
            default_model="gpt-5.1",
            native_structured=True,
            supports_learn_search=False,
            keys_url="https://platform.openai.com/api-keys",
            uses_max_completion_tokens=True,
        ),
        ProviderSpec(
            name="deepseek",
            label="DeepSeek",
            env_var="DEEPSEEK_API_KEY",
            sdk="openai-compat",
            base_url="https://api.deepseek.com",
            known_models=("deepseek-chat", "deepseek-reasoner"),
            default_model="deepseek-chat",
            native_structured=True,
            supports_learn_search=False,
            keys_url="https://platform.deepseek.com/api_keys",
        ),
        ProviderSpec(
            name="kimi",
            label="Kimi (Moonshot AI)",
            env_var="MOONSHOT_API_KEY",
            sdk="openai-compat",
            base_url="https://api.moonshot.ai/v1",
            known_models=("kimi-k2-0905-preview", "kimi-k2-turbo-preview", "kimi-latest"),
            default_model="kimi-latest",
            native_structured=True,
            supports_learn_search=False,
            keys_url="https://platform.moonshot.ai/console/api-keys",
        ),
        ProviderSpec(
            name="gemini",
            label="Google Gemini",
            env_var="GEMINI_API_KEY",
            sdk="openai-compat",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            known_models=("gemini-2.5-pro", "gemini-2.5-flash"),
            default_model="gemini-2.5-pro",
            native_structured=True,
            supports_learn_search=False,
            keys_url="https://aistudio.google.com/apikey",
        ),
        ProviderSpec(
            name="mistral",
            label="Mistral",
            env_var="MISTRAL_API_KEY",
            sdk="openai-compat",
            base_url="https://api.mistral.ai/v1",
            known_models=(
                "mistral-large-latest",
                "mistral-medium-latest",
                "mistral-small-latest",
            ),
            default_model="mistral-large-latest",
            native_structured=True,
            supports_learn_search=False,
            keys_url="https://console.mistral.ai/api-keys",
        ),
        ProviderSpec(
            name="xai",
            label="xAI",
            env_var="XAI_API_KEY",
            sdk="openai-compat",
            base_url="https://api.x.ai/v1",
            known_models=("grok-4", "grok-4-fast", "grok-3-mini"),
            default_model="grok-4",
            native_structured=True,
            supports_learn_search=False,
            keys_url="https://console.x.ai",
        ),
    )
}


class UnknownProviderError(KeyError):
    def __init__(self, name: str):
        self.provider = name
        super().__init__(
            f"Unknown provider '{name}'. Supported: {', '.join(sorted(PROVIDERS))}."
        )


def get_provider(name: str) -> ProviderSpec:
    try:
        return PROVIDERS[name.lower().strip()]
    except KeyError:
        raise UnknownProviderError(name) from None


def parse_model_string(value: str) -> tuple[ProviderSpec, str]:
    """Parse 'provider/model' (e.g. anthropic/claude-sonnet-4-6)."""
    provider, _, model = value.partition("/")
    provider, model = provider.strip(), model.strip()
    if not provider or not model:
        raise ValueError(
            f"Expected <provider>/<model> (e.g. anthropic/claude-sonnet-4-6), got '{value}'."
        )
    return get_provider(provider), model
