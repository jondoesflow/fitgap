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
#: Newer MCP beta. Required for per-tool enablement, which needs an explicit
#: `mcp_toolset` entry in `tools` alongside `mcp_servers`.
MCP_TOOLSET_BETA = "mcp-client-2025-11-20"
#: Compact search over Learn: title + URL + excerpt, capped per chunk.
LEARN_SEARCH_TOOL = "microsoft_docs_search"


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

    @staticmethod
    def _learn_system(system: str, cache_prompt: bool):
        """System prompt, cached when enabled.

        Rendering order is tools -> system -> messages, so a breakpoint on the
        last system block caches the tool definitions too. That prefix is
        identical for every claim, so it is reused across requirements and
        across each round of the server-side tool loop.
        """
        if not cache_prompt:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def learn_search(
        self,
        *,
        system: str,
        user: str,
        verify,  # fitgap.config.VerifyConfig
        max_tokens: int = 2048,
        stage: str = "verify",
        tracker=None,
    ) -> str:
        # verify.model overrides the run model for this stage only —
        # verification is a constrained search-and-cite task, so a cheaper
        # (Anthropic) model may do; the URL liveness guard is unchanged.
        model = verify.model or self.model
        system_param = self._learn_system(system, verify.cache_prompt)
        messages = [{"role": "user", "content": user}]

        if verify.mode == "mcp":
            mcp_servers = [
                {"type": "url", "url": LEARN_MCP_URL, "name": "microsoft-learn"}
            ]
            if verify.search_only:
                # Per-tool enablement needs the newer beta, which in turn
                # requires an explicit mcp_toolset entry in `tools`. Withholding
                # microsoft_docs_fetch keeps whole Learn pages out of context.
                response = self.sdk.beta.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_param,
                    messages=messages,
                    mcp_servers=mcp_servers,
                    tools=[
                        {
                            "type": "mcp_toolset",
                            "mcp_server_name": "microsoft-learn",
                            "default_config": {"enabled": False},
                            "configs": [
                                {"name": LEARN_SEARCH_TOOL, "enabled": True}
                            ],
                        }
                    ],
                    betas=[MCP_TOOLSET_BETA],
                )
            else:
                response = self.sdk.beta.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_param,
                    messages=messages,
                    mcp_servers=mcp_servers,
                    betas=[MCP_BETA],
                )
        else:  # web_search fallback, locked to learn.microsoft.com
            response = self.sdk.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_param,
                messages=messages,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": verify.max_searches,
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
