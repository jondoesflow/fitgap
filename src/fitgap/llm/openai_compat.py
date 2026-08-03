"""OpenAI-compatible implementation of LLMClient.

Covers OpenAI, DeepSeek, Kimi (Moonshot), Google Gemini, Mistral and xAI —
they all speak the OpenAI chat-completions protocol, differing only in
``base_url`` and API key. Structured output uses forced function calling
where the provider supports it natively, otherwise a strict
JSON-instruction prompt; either way the response is validated against the
schema, with one repair retry.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fitgap.llm.base import LLMClient, StructuredOutputError, UnsupportedFeatureError
from fitgap.llm.registry import ProviderSpec
from fitgap.llm.schema import validate_instance


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        spec: ProviderSpec,
        model: str,
        api_key: str | None = None,
        sdk_client=None,
    ):
        self.spec = spec
        self.provider = spec.name
        self.model = model
        if sdk_client is None:
            from openai import OpenAI

            kwargs: dict = {"api_key": api_key}
            if spec.base_url:
                kwargs["base_url"] = spec.base_url
            sdk_client = OpenAI(**kwargs)
        self.sdk = sdk_client

    # ------------------------------------------------------------- interface

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
    ) -> dict:
        prompt = user
        errors: list[str] = []
        for attempt in range(2):
            response = self._create(
                system=system,
                user=prompt,
                tool_name=tool_name,
                tool_description=tool_description,
                schema=schema,
                max_tokens=max_tokens,
            )
            self._record(stage, tracker, response)
            payload = self._extract_payload(response)
            if payload is None:
                errors = [f"response contained no parseable '{tool_name}' JSON payload"]
            else:
                errors = validate_instance(payload, schema)
                if not errors:
                    return payload
            if attempt == 0:
                prompt = (
                    user
                    + "\n\nYour previous attempt failed validation:\n- "
                    + "\n- ".join(errors)
                    + "\n\nRespond again with corrected, schema-conformant input."
                )
        raise StructuredOutputError(self.provider, self.model, tool_name, errors)

    def learn_search(self, *, system, user, verify, max_tokens=2048, stage="verify", tracker=None) -> str:
        raise UnsupportedFeatureError(
            f"Live Microsoft Learn verification requires Anthropic-only API "
            f"features (MCP connector / web search); the active provider is "
            f"'{self.provider}'. Switch with 'fitgap model use anthropic/<model>' "
            f"for the verify stage, or run with --skip-verify."
        )

    # ------------------------------------------------------------- internals

    def _create(self, *, system, user, tool_name, tool_description, schema, max_tokens):
        messages: list[dict] = [{"role": "system", "content": system}]
        kwargs: dict = {"model": self.model, "messages": messages}
        if self.spec.native_structured:
            messages.append({"role": "user", "content": user})
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": schema,
                    },
                }
            ]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": tool_name}}
        else:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        user
                        + "\n\nRespond with ONLY a JSON object (no prose, no code "
                        "fences) conforming to this JSON schema:\n"
                        + json.dumps(schema)
                    ),
                }
            )
        if self.spec.uses_max_completion_tokens:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        return self.sdk.chat.completions.create(**kwargs)

    @staticmethod
    def _extract_payload(response) -> dict | None:
        choice = response.choices[0]
        message = choice.message
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            try:
                parsed = json.loads(tool_calls[0].function.arguments)
            except (json.JSONDecodeError, TypeError):
                return None
            return parsed if isinstance(parsed, dict) else None
        text = (getattr(message, "content", None) or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _record(stage: str, tracker, response) -> None:
        if tracker is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        # Normalise OpenAI-style usage onto the Anthropic-style field names
        # the UsageTracker understands; carry the served model through so
        # per-stage pricing can follow it.
        tracker.record(
            stage,
            SimpleNamespace(
                model=getattr(response, "model", None),
                usage=SimpleNamespace(
                    input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                ),
            ),
        )
