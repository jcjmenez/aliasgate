"""The upstream URL the gateway calls when `base_url` carries a base path (architecture §7)."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from payloads import CHAT, ClientFactory, chat_request, chat_response

from aliasgate.config import Settings, load_settings

ORIGIN = "https://openai-resource.example.com"
BASE_URL = f"{ORIGIN}/openai"
EMAIL = "juan.perez@example.com"
SSE = {"content-type": "text/event-stream"}

# configured base_url → the URL the gateway must call for POST /v1/chat/completions
BASE_URLS = [
    (BASE_URL, f"{ORIGIN}/openai/v1/chat/completions"),
    (f"{BASE_URL}/", f"{ORIGIN}/openai/v1/chat/completions"),
    (f"{ORIGIN}/tenant%2Fa/openai", f"{ORIGIN}/tenant%2Fa/openai/v1/chat/completions"),
    (f"{ORIGIN}/caf%c3%a9", f"{ORIGIN}/caf%c3%a9/v1/chat/completions"),
]
BASE_URL_IDS = ["path", "path-with-trailing-slash", "encoded-slash", "encoded-lower-case"]


def _settings(base_url: str) -> Settings:
    env = {
        "ALIASGATE_UPSTREAMS__OPENAI__BASE_URL": base_url,
        "ALIASGATE_UPSTREAMS__PASSTHROUGH_PATHS": '["openai GET /v1/models"]',
    }
    return load_settings(None, env=env)


@pytest.fixture
def settings() -> Settings:
    """Default settings with an OpenAI upstream under a base path, and one passthrough path."""
    return _settings(BASE_URL)


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    """A mocked upstream with no base URL: routes name full URLs, anything else fails."""
    with respx.mock(assert_all_called=False) as mock:
        yield mock


def _sse(*contents: str) -> bytes:
    envelope = {"id": "chatcmpl-s", "object": "chat.completion.chunk", "created": 1, "model": "m"}
    events = []
    for content in contents:
        choice = {"index": 0, "delta": {"content": content}, "finish_reason": None}
        events.append(f"data: {json.dumps({**envelope, 'choices': [choice]})}\n\n")
    return ("".join(events) + "data: [DONE]\n\n").encode()


def _streamed_content(body: bytes) -> str:
    chunks = [
        json.loads(line.removeprefix("data: "))
        for line in body.decode().splitlines()
        if line.startswith("data: {")
    ]
    return "".join(chunk["choices"][0]["delta"]["content"] for chunk in chunks)


def _sent(route: respx.Route) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


@pytest.mark.parametrize(("base_url", "expected"), BASE_URLS, ids=BASE_URL_IDS)
async def test_a_chat_request_goes_to_the_base_path_followed_by_the_route_path(
    make_client: ClientFactory, upstream: respx.MockRouter, base_url: str, expected: str
) -> None:
    route = upstream.post(url=expected).respond(json=chat_response("Write to [EMAIL_1]"))
    async with make_client(_settings(base_url)) as client:
        response = await client.post(CHAT, json=chat_request(f"Mail {EMAIL}"))

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == f"Write to {EMAIL}"
    sent = route.calls.last.request
    assert str(sent.url) == expected
    assert sent.url.raw_path == expected.removeprefix(ORIGIN).encode("ascii")
    assert sent.headers["host"] == "openai-resource.example.com"
    assert _sent(route)["messages"][0]["content"] == "Mail [EMAIL_1]"
    assert upstream.calls.call_count == 1


@pytest.mark.parametrize(("base_url", "expected"), BASE_URLS, ids=BASE_URL_IDS)
async def test_a_streaming_chat_request_goes_to_the_base_path_followed_by_the_route_path(
    make_client: ClientFactory, upstream: respx.MockRouter, base_url: str, expected: str
) -> None:
    route = upstream.post(url=expected).respond(
        headers=SSE, content=_sse("Write to [EMA", "IL_1] today")
    )
    async with make_client(_settings(base_url)) as client:
        response = await client.post(CHAT, json=chat_request(f"Mail {EMAIL}", stream=True))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _streamed_content(response.content) == f"Write to {EMAIL} today"
    sent = route.calls.last.request
    assert sent.url.raw_path == expected.removeprefix(ORIGIN).encode("ascii")
    assert _sent(route)["stream"] is True
    assert EMAIL not in sent.content.decode()
    assert upstream.calls.call_count == 1


async def test_the_query_string_follows_the_base_path_and_the_route_path(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(url=f"{BASE_URL}{CHAT}").respond(json=chat_response("ok"))
    response = await client.post(f"{CHAT}?api-version=preview", json=chat_request("hello"))
    assert response.status_code == 200
    assert str(route.calls.last.request.url) == f"{BASE_URL}{CHAT}?api-version=preview"


async def test_a_passthrough_path_goes_to_the_base_path_followed_by_the_path(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    listing = {"object": "list", "data": [{"id": "gpt-example"}]}
    route = upstream.get(url=f"{BASE_URL}/v1/models").respond(json=listing)
    response = await client.get("/v1/models?limit=2")
    assert response.status_code == 200
    assert response.json() == listing
    assert str(route.calls.last.request.url) == f"{BASE_URL}/v1/models?limit=2"


async def test_a_passthrough_request_matches_on_its_decoded_path_and_sends_the_entry_path(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.get(url=f"{BASE_URL}/v1/models").respond(json={"data": []})
    response = await client.get("/v1/mod%65ls")
    assert response.status_code == 200
    assert route.calls.last.request.url.raw_path == b"/openai/v1/models"


async def test_a_request_outside_the_base_path_is_never_made(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    without_base_path = upstream.post(url=f"{ORIGIN}{CHAT}").respond(json=chat_response("no"))
    with_base_path = upstream.post(url=f"{BASE_URL}{CHAT}").respond(json=chat_response("ok"))
    response = await client.post(CHAT, json=chat_request("hello"))
    assert response.status_code == 200
    assert (without_base_path.call_count, with_base_path.call_count) == (0, 1)
