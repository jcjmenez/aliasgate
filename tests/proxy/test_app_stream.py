import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx
import pytest
import respx
from payloads import CHAT, chat_request
from starlette.applications import Starlette
from starlette.types import Message

from aliasgate.config import Settings
from aliasgate.dialects.base import StreamTransformer
from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.dialects.sse import SSEEvent, SSEParser
from aliasgate.mapping import Mapping
from aliasgate.proxy.app import create_app

VALUES = ("Juan Pérez", "Pérez", "juan.perez@example.com", "ACME Corporation", "José")
ENVELOPE = {"id": "chatcmpl-s", "object": "chat.completion.chunk", "created": 1, "model": "m"}
SSE = {"content-type": "text/event-stream; charset=utf-8"}


class UpstreamStream(httpx.AsyncByteStream):
    """An upstream body delivered in the given byte chunks; records whether it was closed."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        fail_at: int | None = None,
        stall_at: int | None = None,
    ) -> None:
        self.chunks = chunks
        self.fail_at = fail_at
        self.stall_at = stall_at
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for number, chunk in enumerate(self.chunks):
            if number == self.fail_at:
                raise httpx.ReadError("connection lost")
            if number == self.stall_at:
                await asyncio.Event().wait()
            yield chunk

    async def aclose(self) -> None:
        await asyncio.sleep(0)  # closing a real connection suspends; cancellation can land here
        self.closed = True


def _delta(index: int = 0, finish: str | None = None, **delta: Any) -> bytes:
    chunk = {**ENVELOPE, "choices": [{"index": index, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()


DONE = b"data: [DONE]\n\n"


def _sse_body(*contents: str) -> bytes:
    parts = [_delta(role="assistant", content="")]
    parts += [_delta(content=content) for content in contents]
    return b"".join([*parts, _delta(finish="stop"), DONE])


def _client_events(body: bytes) -> list[Any]:
    parser = SSEParser()
    events = parser.feed(body) + parser.end()
    return [event.data if event.data == "[DONE]" else json.loads(event.data) for event in events]


def _client_content(body: bytes) -> str:
    return "".join(
        choice["delta"].get("content") or ""
        for chunk in _client_events(body)
        if isinstance(chunk, dict)
        for choice in chunk["choices"]
    )


def _stream_response(
    stream: UpstreamStream, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(200, headers=headers or SSE, stream=stream)


async def test_a_stream_cut_at_awkward_byte_positions_is_restored(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    body = _sse_body("Dear [PERS", "ON_1] of [ORG_1]. Saludos, José.")
    accent = body.index("é".encode()) + 1
    inside_json = body.index(b"ON_1]") - 3
    cuts = sorted({5, inside_json, accent, len(body) - 4})
    chunks = [body[start:end] for start, end in zip([0, *cuts], [*cuts, len(body)], strict=True)]
    stream = UpstreamStream(chunks)
    route = upstream.post(CHAT).mock(return_value=_stream_response(stream))

    response = await client.post(
        CHAT, json=chat_request("Write to Juan Pérez at ACME Corporation", stream=True)
    )

    assert response.status_code == 200
    assert _client_content(response.content) == (
        "Dear Juan Pérez of ACME Corporation. Saludos, José."
    )
    assert _client_events(response.content)[-1] == "[DONE]"
    sent = json.loads(route.calls.last.request.content)
    assert sent["stream"] is True
    assert sent["messages"][0]["content"] == "Write to [PERSON_1] at [ORG_1]"
    assert "Juan" not in route.calls.last.request.content.decode()
    assert stream.closed


async def test_the_stream_response_headers(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    headers = {**SSE, "x-request-id": "req_1", "cache-control": "private"}
    upstream.post(CHAT).mock(return_value=_stream_response(UpstreamStream([DONE]), headers))
    response = await client.post(CHAT, json=chat_request("hi", stream=True))
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-request-id"] == "req_1"
    assert len(response.headers["x-aliasgate-request-id"]) == 32
    assert "content-length" not in response.headers


async def test_an_upstream_error_on_a_streaming_request_is_a_normal_json_response(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    error = {"error": {"message": "Invalid key for [PERSON_1]", "code": "invalid_api_key"}}
    stream = UpstreamStream([json.dumps(error).encode()])
    upstream.post(CHAT).mock(
        return_value=httpx.Response(
            401, headers={"content-type": "application/json"}, stream=stream
        )
    )
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez", stream=True))
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "error": {"message": "Invalid key for Juan Pérez", "code": "invalid_api_key"}
    }
    assert stream.closed


async def test_a_json_answer_to_a_streaming_request_is_restored_like_a_normal_one(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    answer = {"choices": [{"index": 0, "message": {"content": "Hi [PERSON_1]"}}]}
    upstream.post(CHAT).respond(200, json=answer)
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez", stream=True))
    assert response.json()["choices"][0]["message"]["content"] == "Hi Juan Pérez"


class _FailingStream(UpstreamStream):
    """An upstream body that breaks after its first byte with the given error."""

    def __init__(self, error: Exception) -> None:
        super().__init__([b"{"])
        self.error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        raise self.error


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (httpx.ReadError("connection lost"), 502, "upstream_unreachable"),
        (httpx.ReadTimeout("timed out"), 504, "upstream_timeout"),
    ],
)
async def test_a_failure_reading_an_error_body_maps_to_a_gateway_error(
    client: httpx.AsyncClient,
    upstream: respx.MockRouter,
    error: Exception,
    status: int,
    code: str,
) -> None:
    stream = _FailingStream(error)
    upstream.post(CHAT).mock(
        return_value=httpx.Response(
            500, headers={"content-type": "application/json"}, stream=stream
        )
    )
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez", stream=True))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert stream.closed


async def test_the_upstream_is_closed_when_the_transformer_cannot_be_created(
    client: httpx.AsyncClient, upstream: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: OpenAIChatDialect, mapping: Mapping) -> StreamTransformer:
        raise RuntimeError("Juan Pérez")

    monkeypatch.setattr(OpenAIChatDialect, "stream_transformer", broken)
    stream = UpstreamStream([DONE])
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    response = await client.post(CHAT, json=chat_request("Write to Juan Pérez", stream=True))
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "Juan" not in response.text
    assert stream.closed


async def test_a_timeout_before_the_stream_starts_is_a_504(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).mock(side_effect=httpx.ReadTimeout("timed out"))
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez", stream=True))
    assert response.status_code == 504


class _FaultyTransformer:
    """Delegates to the real transformer, then raises on the given event."""

    def __init__(self, inner: StreamTransformer, fail_at: int) -> None:
        self._inner = inner
        self._fail_at = fail_at
        self._seen = 0
        self.unresolved = 0

    def feed(self, event: SSEEvent) -> list[SSEEvent]:
        self._seen += 1
        if self._seen == self._fail_at:
            raise RuntimeError("Juan Pérez")
        return self._inner.feed(event)

    def end(self) -> list[SSEEvent]:
        return self._inner.end()


async def test_a_failure_mid_stream_ends_the_stream_without_emitting_held_text(
    client: httpx.AsyncClient,
    upstream: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real = OpenAIChatDialect.stream_transformer

    def faulty(self: OpenAIChatDialect, mapping: Mapping) -> StreamTransformer:
        return _FaultyTransformer(real(self, mapping), fail_at=3)

    monkeypatch.setattr(OpenAIChatDialect, "stream_transformer", faulty)
    # "x" joins the held "[PERS", so nothing after "Dear " may reach the client.
    stream = UpstreamStream(
        [_delta(content="Dear [PERS"), _delta(content="x"), _delta(content="]")]
    )
    upstream.post(CHAT).mock(return_value=_stream_response(stream))

    response = await client.post(CHAT, json=chat_request("Write to Juan Pérez", stream=True))

    assert response.status_code == 200
    assert _client_content(response.content) == "Dear "
    assert "PERS" not in response.text
    assert "[DONE]" not in response.text
    assert stream.closed
    failures = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert [record.fields["error"] for record in failures] == ["RuntimeError"]  # type: ignore[attr-defined]


async def test_an_upstream_read_error_mid_stream_truncates_and_closes(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    stream = UpstreamStream([_delta(content="Dear [PERS"), _delta(content="ON_1]")], fail_at=1)
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    response = await client.post(CHAT, json=chat_request("Write to Juan Pérez", stream=True))
    assert _client_content(response.content) == "Dear "
    assert "PERS" not in response.text
    assert stream.closed


async def test_a_stream_ending_without_done_flushes_held_text(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    stream = UpstreamStream([_delta(content="Dear [PERSON_1] [PERS")])
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    response = await client.post(CHAT, json=chat_request("Write to Juan Pérez", stream=True))
    assert _client_content(response.content) == "Dear Juan Pérez [PERS"


async def test_a_last_event_without_a_blank_line_is_still_relayed(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    stream = UpstreamStream([_delta(content="Dear [PERSON_1]"), b"data: [DONE]"])
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    response = await client.post(CHAT, json=chat_request("Write to Juan Pérez", stream=True))
    assert _client_events(response.content)[-1] == "[DONE]"
    assert _client_content(response.content) == "Dear Juan Pérez"


async def test_the_request_line_is_logged_once_at_the_end_of_the_stream_without_text(
    client: httpx.AsyncClient, upstream: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stream = UpstreamStream([_sse_body("Dear [PERSON_1] and [PERSON_7], José")])
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    response = await client.post(CHAT, json=chat_request("Write to Juan Pérez", stream=True))

    lines = [
        record.fields  # type: ignore[attr-defined]
        for record in caplog.records
        if record.name == "aliasgate.proxy" and record.levelno == logging.INFO
    ]
    assert len(lines) == 1
    assert lines[0]["status"] == 200
    assert lines[0]["unresolved_placeholders"] == 1
    assert lines[0]["request_id"] == response.headers["x-aliasgate-request-id"]
    for record in caplog.records:
        if record.name == "httpx" and "gateway.test" in str(record.args):
            continue
        for value in VALUES:
            assert value not in repr(vars(record))


def _scope(spec_version: str, body: bytes) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": CHAT,
        "raw_path": CHAT.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"gateway.test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("gateway.test", 80),
    }


async def _disconnecting_client(app: Starlette, spec_version: str) -> list[Message]:
    """Call the app directly; the client goes away after the first body chunk."""
    body = json.dumps(chat_request("Write to Juan Pérez", stream=True)).encode()
    gone = asyncio.Event()
    received: list[Message] = []
    requested = False

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if gone.is_set():
            raise OSError("client went away")
        received.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            gone.set()

    await asyncio.wait_for(app(_scope(spec_version, body), receive, send), timeout=10)
    return received


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_the_upstream_is_closed_when_the_client_disconnects(
    settings: Settings, upstream: respx.MockRouter, spec_version: str
) -> None:
    chunks = [_delta(role="assistant", content="Dear "), _delta(content="x"), _delta(content="y")]
    stream = UpstreamStream(chunks, stall_at=1 if spec_version == "2.3" else None)
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    async with httpx.AsyncClient() as http_client:
        app = create_app(settings, http_client=http_client)
        received = await _disconnecting_client(app, spec_version)
    assert received[0]["type"] == "http.response.start"
    assert received[0]["status"] == 200
    bodies = [message["body"] for message in received if message["type"] == "http.response.body"]
    assert len(bodies) == 1
    assert stream.closed


async def test_a_send_failure_other_than_a_disconnect_is_logged_and_the_upstream_closed(
    settings: Settings, upstream: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    stream = UpstreamStream([_delta(content="Dear "), _delta(content="x")])
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    body = json.dumps(chat_request("Write to Juan Pérez", stream=True)).encode()
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and len(sent) > 1:
            raise RuntimeError("Juan Pérez")
        sent.append(message)

    async with httpx.AsyncClient() as http_client:
        app = create_app(settings, http_client=http_client)
        await asyncio.wait_for(app(_scope("2.4", body), receive, send), timeout=10)
    assert stream.closed
    errors = [record.fields for record in caplog.records if record.levelno == logging.ERROR]  # type: ignore[attr-defined]
    assert [(error["stage"], error["error"]) for error in errors] == [("stream", "RuntimeError")]
    assert "Juan" not in repr([vars(record) for record in caplog.records])


async def test_the_upstream_is_closed_when_an_enclosing_cancel_scope_is_cancelled(
    settings: Settings, upstream: respx.MockRouter
) -> None:
    stream = UpstreamStream(
        [_delta(role="assistant", content="Dear "), _delta(content="x")], stall_at=1
    )
    upstream.post(CHAT).mock(return_value=_stream_response(stream))
    body = json.dumps(chat_request("Write to Juan Pérez", stream=True)).encode()
    first_body = anyio.Event()
    requested = False

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.Event().wait()  # the client never disconnects by itself
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()

    async with httpx.AsyncClient() as http_client:
        app = create_app(settings, http_client=http_client)
        with anyio.fail_after(10):
            async with anyio.create_task_group() as group:
                group.start_soon(app, _scope("2.3", body), receive, send)
                await first_body.wait()
                # Level-triggered: every later await in this scope is cancelled too.
                group.cancel_scope.cancel()
    assert stream.closed
