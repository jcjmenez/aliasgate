"""Synthetic OpenAI payloads and constants shared by the proxy tests."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx

OPENAI = "https://api.openai.com"
CHAT = "/v1/chat/completions"

# make_client(settings=None, *, pseudonymizer=None): a gateway client as an async context manager.
type ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def chat_response(content: str | None, **message: Any) -> dict[str, Any]:
    """A minimal non-streamed chat completion with one choice."""
    return {
        "id": "chatcmpl-example",
        "object": "chat.completion",
        "created": 1763380800,
        "model": "gpt-example",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, **message},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def chat_request(*contents: str, **fields: Any) -> dict[str, Any]:
    """A chat request with one user message per content."""
    messages = [{"role": "user", "content": content} for content in contents]
    return {"model": "gpt-example", "messages": messages, **fields}
