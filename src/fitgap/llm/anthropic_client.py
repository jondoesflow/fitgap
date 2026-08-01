"""Anthropic implementation of LLMClient.

Structured output uses forced tool choice — the model must call the tool,
whose input_schema mirrors the expected shape. Learn search uses the
Anthropic-only MCP connector (Microsoft Learn MCP server) or the API
web-search tool restricted to learn.microsoft.com.
"""

from __future__ import annotations

from fitgap.llm.base import LLMClient, StructuredOutputError
from fitgap.llm.schema import validate_instance

LEARN_MCP_URL = "https://learn.microsoft.com/api/mcp"
MCP_BETA = "mcp-client-2025-04-04"


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, sdk_client=None):
        self.model = model
        if sdk_client is None:
            import anthropic

            sdk_client = (
                anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
            )
        self.sdk = sdk_client

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
        tool = {
            "name": tool_name,
            "description": tool_description,
            "input_schema": schema,
        }
        prompt = user
        errors: list[str] = []
        for attempt in range(2):
            response = self.sdk.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
            )
            if tracker:
                tracker.record(stage, response)
            tool_input = None
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    tool_input = block.input
                    break
            if tool_input is None:
                errors = [f"response contained no '{tool_name}' tool call"]
            else:
                errors = validate_instance(tool_input, schema)
                if not errors:
                    return tool_input
            if attempt == 0:
                prompt = (
                    user
                    + "\n\nYour previous attempt failed validation:\n- "
                    + "\n- ".join(errors)
                    + f"\n\nCall the {tool_name} tool again with corrected, "
                    "schema-conformant input."
                )
        raise StructuredOutputError(self.provider, self.model, tool_name, errors)

    def learn_search(
        self,
        *,
        system: str,
        user: str,
        mode: str,
        max_tokens: int = 2048,
        stage: str = "verify",
        tracker=None,
    ) -> str:
        if mode == "mcp":
            response = self.sdk.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                mcp_servers=[
                    {
                        "type": "url",
                        "url": LEARN_MCP_URL,
                        "name": "microsoft-learn",
                    }
                ],
                betas=[MCP_BETA],
            )
        else:  # web_search fallback, locked to learn.microsoft.com
            response = self.sdk.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 5,
                        "allowed_domains": ["learn.microsoft.com"],
                    }
                ],
            )
        if tracker:
            tracker.record(stage, response)
        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = block.text  # last text block wins
        return text
