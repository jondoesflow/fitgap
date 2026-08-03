"""Test doubles for the Anthropic client used by classify/verify/transcript stages."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable


def tool_use_block(name: str, tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input)


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


class FakeMessages:
    def __init__(self, responder: Callable[[dict], list[Any]]):
        self.responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=self.responder(kwargs),
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=200,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


class FakeChatCompletions:
    """Test double for the OpenAI-compatible chat.completions surface.

    ``responder`` receives the request kwargs and returns the assistant
    message namespace (use :func:`chat_tool_call_message` /
    :func:`chat_text_message`).
    """

    def __init__(self, responder: Callable[[dict], SimpleNamespace]):
        self.responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self.responder(kwargs))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
        )


class FakeOpenAI:
    """Mimics the slice of openai.OpenAI the OpenAICompatClient touches."""

    def __init__(self, responder: Callable[[dict], SimpleNamespace]):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(responder))

    @property
    def calls(self) -> list[dict]:
        return self.chat.completions.calls


def chat_tool_call_message(arguments: dict | str) -> SimpleNamespace:
    import json as _json

    if isinstance(arguments, dict):
        arguments = _json.dumps(arguments)
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(function=SimpleNamespace(arguments=arguments))
        ],
    )


def chat_text_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text, tool_calls=None)


class FakeAnthropic:
    """Mimics the slice of anthropic.Anthropic the pipeline touches.

    ``responder`` receives the request kwargs and returns the content blocks
    of the fake response. Both ``client.messages`` and ``client.beta.messages``
    (used for MCP-connector calls) share the same recorder.
    """

    def __init__(self, responder: Callable[[dict], list[Any]]):
        self.messages = FakeMessages(responder)
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def calls(self) -> list[dict]:
        return self.messages.calls
