"""The Anthropic routes of the gateway: POST /v1/messages and POST /v1/messages/count_tokens."""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from payloads import CHAT, OPENAI, ClientFactory, chat_request, chat_response

from aliasgate.config import Settings, load_settings
from aliasgate.dialects.sse import SSEParser
from aliasgate.engine import Pseudonymizer
from fakes import WordListDetector

ANTHROPIC = "https://api.anthropic.com"
MESSAGES = "/v1/messages"
COUNT_TOKENS = "/v1/messages/count_tokens"
VALUES = ("Juan Pérez", "Pérez", "juan.perez@example.com", "juan.perez", "ACME Corporation", "ACME")
TEXT = "Write to Juan Pérez (juan.perez@example.com) at ACME Corporation."
HEADERS = {
    "x-api-key": "sk-" + "x" * 24,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "example-beta-2026-01-01",
}
SSE = {"content-type": "text/event-stream; charset=utf-8"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The default test settings, with one Anthropic passthrough path instead of OpenAI's."""
    data = settings.model_dump()
    data["upstreams"]["passthrough_paths"] = ["anthropic GET /v1/models"]
    return Settings.model_validate(data)


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    """A mocked upstream with no base URL: routes name full URLs, anything else fails."""
    with respx.mock(assert_all_called=False) as mock:
        yield mock


def _request(*contents: str, **fields: Any) -> dict[str, Any]:
    messages = [{"role": "user", "content": content} for content in contents]
    return {"model": "claude-example", "max_tokens": 256, "messages": messages, **fields}


def _message(*content: dict[str, Any], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_example01",
        "type": "message",
        "role": "assistant",
        "model": "claude-example",
        "content": list(content),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 10},
    }


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _sent(route: respx.Route) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(
        f"event: {data['type']}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
        for data in events
    )


def _start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _delta(index: int, kind: str, value: str) -> dict[str, Any]:
    key = "text" if kind == "text_delta" else "partial_json"
    return {"type": "content_block_delta", "index": index, "delta": {"type": kind, key: value}}


def _stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


MESSAGE_START = {"type": "message_start", "message": {**_message(), "content": []}}
MESSAGE_STOP = {"type": "message_stop"}


def _client_events(body: bytes) -> list[tuple[str | None, dict[str, Any]]]:
    parser = SSEParser()
    return [(event.event, json.loads(event.data)) for event in parser.feed(body) + parser.end()]


def _streamed(body: bytes, index: int, key: str) -> str:
    return "".join(
        data["delta"].get(key) or ""
        for _, data in _client_events(body)
        if data["type"] == "content_block_delta" and data["index"] == index
    )


class ChunkedStream(httpx.AsyncByteStream):
    """An upstream body delivered in the given byte chunks; records whether it was closed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


async def test_a_message_is_sent_with_placeholders_and_returned_with_real_values(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    answer = _message(_text("Dear [PERSON_1] of [ORG_1], mail sent to [EMAIL_1]."))
    route = upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=answer)

    response = await client.post(
        MESSAGES, json=_request(TEXT, metadata={"user_id": "user-0001"}), headers=HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == [
        _text("Dear Juan Pérez of ACME Corporation, mail sent to juan.perez@example.com.")
    ]
    assert body["usage"] == answer["usage"]
    sent = route.calls.last.request
    for name, value in HEADERS.items():
        assert sent.headers[name] == value
    assert sent.headers["host"] == "api.anthropic.com"
    raw = sent.content.decode()
    for value in (*VALUES, "user-0001"):
        assert value not in raw
    assert _sent(route)["messages"][0]["content"] == "Write to [PERSON_1] ([EMAIL_1]) at [ORG_1]."
    assert "metadata" not in _sent(route)


async def test_a_stop_sequence_is_sent_pseudonymised_and_restored_in_the_answer(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    answer = {
        **_message(_text("Hello"), stop_reason="stop_sequence"),
        "stop_sequence": "[PERSON_1]:",
    }
    route = upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=answer)
    request = _request(TEXT, stop_sequences=["Juan P\u00e9rez:"])
    response = await client.post(MESSAGES, json=request, headers=HEADERS)

    assert response.status_code == 200
    assert _sent(route)["stop_sequences"] == ["[PERSON_1]:"]
    assert response.json()["stop_sequence"] == "Juan P\u00e9rez:"


async def test_a_streamed_stop_sequence_is_restored(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    delta = {"stop_reason": "stop_sequence", "stop_sequence": "[PERSON_1]:"}
    body = _sse(
        MESSAGE_START,
        _start(0, _text("")),
        _delta(0, "text_delta", "Hello"),
        _stop(0),
        {"type": "message_delta", "delta": delta, "usage": {"output_tokens": 1}},
        MESSAGE_STOP,
    )
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(headers=SSE, content=body)
    request = _request(TEXT, stream=True, stop_sequences=["Juan P\u00e9rez:"])
    response = await client.post(MESSAGES, json=request, headers=HEADERS)

    deltas = [
        data for _, data in _client_events(response.content) if data["type"] == "message_delta"
    ]
    assert deltas[0]["delta"]["stop_sequence"] == "Juan P\u00e9rez:"


async def test_count_tokens_forwards_a_web_search_tool_without_its_location(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(f"{ANTHROPIC}{COUNT_TOKENS}").respond(json={"input_tokens": 42})
    location = {"type": "approximate", "city": "Springfield"}
    tool = {"type": "web_search_20250305", "name": "web_search", "user_location": location}
    request = _request(TEXT, tools=[tool])
    request.pop("max_tokens")
    response = await client.post(COUNT_TOKENS, json=request, headers=HEADERS)

    assert response.status_code == 200
    assert _sent(route)["tools"] == [{"type": "web_search_20250305", "name": "web_search"}]


async def test_tool_use_input_is_restored_in_a_whole_response(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    tool_use = {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "send_email",
        "input": {"to": "[EMAIL_1]", "cc": ["[PERSON_1]"]},
    }
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=_message(tool_use, stop_reason="tool_use"))
    response = await client.post(MESSAGES, json=_request(TEXT), headers=HEADERS)
    assert response.json()["content"][0]["input"] == {
        "to": "juan.perez@example.com",
        "cc": ["Juan Pérez"],
    }


async def test_a_stream_cut_at_awkward_byte_positions_is_restored(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    body = _sse(
        MESSAGE_START,
        _start(0, _text("")),
        {"type": "ping"},
        _delta(0, "text_delta", "Dear [PERS"),
        _delta(0, "text_delta", "ON_1] of [ORG_1]. Saludos, José."),
        _stop(0),
        _start(1, {"type": "tool_use", "id": "toolu_1", "name": "send_email", "input": {}}),
        _delta(1, "input_json_delta", '{"to": "[EMA'),
        _delta(1, "input_json_delta", 'IL_1]", "who": "[PERSON_1]"}'),
        _stop(1),
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 9},
        },
        MESSAGE_STOP,
    )
    accent = body.index("é".encode()) + 1
    inside_json = body.index(b"ON_1]") - 3
    inside_event_name = body.index(b"event: content_block_stop") + 9
    cuts = sorted({5, inside_json, accent, inside_event_name, len(body) - 4})
    chunks = [body[start:end] for start, end in zip([0, *cuts], [*cuts, len(body)], strict=True)]
    stream = ChunkedStream(chunks)
    route = upstream.post(f"{ANTHROPIC}{MESSAGES}").mock(
        return_value=httpx.Response(200, headers=SSE, stream=stream)
    )

    response = await client.post(MESSAGES, json=_request(TEXT, stream=True), headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _streamed(response.content, 0, "text") == (
        "Dear Juan Pérez of ACME Corporation. Saludos, José."
    )
    assert json.loads(_streamed(response.content, 1, "partial_json")) == {
        "to": "juan.perez@example.com",
        "who": "Juan Pérez",
    }
    events = _client_events(response.content)
    assert all(name == data["type"] for name, data in events)
    assert events[-1] == ("message_stop", MESSAGE_STOP)
    sent = _sent(route)
    assert sent["stream"] is True
    assert sent["messages"][0]["content"] == "Write to [PERSON_1] ([EMAIL_1]) at [ORG_1]."
    assert "Juan" not in route.calls.last.request.content.decode()
    assert stream.closed


async def test_an_image_block_gets_422_in_the_anthropic_error_shape_without_calling_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    image = {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
    body = {
        "model": "claude-example",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": [_text("Who is Juan Pérez?"), image]}],
    }
    response = await client.post(MESSAGES, json=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Unsupported content: image (unsupported_content)",
        },
    }
    assert upstream.calls.call_count == 0


async def test_a_malformed_message_gets_400_in_the_anthropic_error_shape(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    body = {"model": "claude-example", "messages": [{"role": "user", "content": 12345}]}
    response = await client.post(MESSAGES, json=body, headers=HEADERS)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["message"] == (
        "Malformed request: messages[0].content: expected a string, a list or null"
        " (malformed_request)"
    )
    assert upstream.calls.call_count == 0


async def test_an_upstream_error_is_restored_and_keeps_its_status(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    error = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Bad mention of [PERSON_1]"},
        "request_id": "req_example",
    }
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(400, json=error)
    response = await client.post(MESSAGES, json=_request(TEXT, stream=True), headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Bad mention of Juan Pérez"
    assert response.json()["request_id"] == "req_example"


async def test_count_tokens_forwards_a_pseudonymised_body_and_returns_the_answer_unchanged(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    answer = b'{"input_tokens":  42}'
    route = upstream.post(f"{ANTHROPIC}{COUNT_TOKENS}").respond(
        content=answer, headers={"content-type": "application/json"}
    )
    request = _request(TEXT, system="You help ACME Corporation.")
    request.pop("max_tokens")
    response = await client.post(COUNT_TOKENS, json=request, headers=HEADERS)

    assert response.status_code == 200
    assert response.content == answer
    sent = _sent(route)
    assert sent["system"] == "You help [ORG_1]."
    assert sent["messages"][0]["content"] == "Write to [PERSON_1] ([EMAIL_1]) at [ORG_1]."
    for value in VALUES:
        assert value not in route.calls.last.request.content.decode()
    assert route.calls.last.request.headers["x-api-key"] == HEADERS["x-api-key"]


async def test_count_tokens_ignores_stream_and_restores_an_error(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "[ORG_1]?"}}
    route = upstream.post(f"{ANTHROPIC}{COUNT_TOKENS}").respond(400, json=error)
    response = await client.post(COUNT_TOKENS, json=_request(TEXT, stream=True), headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "ACME Corporation?"
    assert _sent(route)["stream"] is True


async def test_count_tokens_rejects_uninspectable_content_like_messages(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": [{"type": "document"}]}]}
    response = await client.post(COUNT_TOKENS, json=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["type"] == "error"
    assert upstream.calls.call_count == 0


@pytest.mark.parametrize(
    "headers",
    [{"anthropic-version": "2023-06-01"}, {"x-api-key": "sk-" + "x" * 24}, HEADERS],
    ids=["anthropic-version", "x-api-key", "both"],
)
async def test_an_unknown_path_with_anthropic_headers_gets_an_anthropic_shaped_404(
    client: httpx.AsyncClient, upstream: respx.MockRouter, headers: dict[str, str]
) -> None:
    response = await client.post("/v1/messages/batches", json=_request(TEXT), headers=headers)
    assert response.status_code == 404
    assert response.json() == {
        "type": "error",
        "error": {"type": "not_found_error", "message": "Unknown endpoint. (unknown_endpoint)"},
    }
    assert upstream.calls.call_count == 0


async def test_an_unknown_path_without_anthropic_headers_gets_an_openai_shaped_404(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    response = await client.post("/v1/messages/batches", json=_request(TEXT))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_endpoint"


async def test_get_on_the_messages_path_is_unknown(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    response = await client.get(MESSAGES, headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found_error"
    assert upstream.calls.call_count == 0


async def test_openai_and_anthropic_routes_go_to_their_own_upstream_base_urls(
    make_client: ClientFactory, upstream: respx.MockRouter
) -> None:
    custom = load_settings(
        None,
        env={
            "ALIASGATE_UPSTREAMS__OPENAI__BASE_URL": "https://openai.example.com/openai",
            "ALIASGATE_UPSTREAMS__ANTHROPIC__BASE_URL": "https://anthropic.example.com/anthropic",
        },
    )
    chat = upstream.post("https://openai.example.com/openai/v1/chat/completions").respond(
        json=chat_response("ok")
    )
    messages = upstream.post("https://anthropic.example.com/anthropic/v1/messages").respond(
        json=_message(_text("ok"))
    )
    count = upstream.post("https://anthropic.example.com/anthropic/v1/messages/count_tokens")
    count.respond(json={"input_tokens": 3})
    defaults = upstream.route(host__in=("api.openai.com", "api.anthropic.com")).respond(500)

    async with make_client(custom) as client:
        assert (await client.post(CHAT, json=chat_request("hi"))).status_code == 200
        assert (
            await client.post(MESSAGES, json=_request("hi"), headers=HEADERS)
        ).status_code == 200
        response = await client.post(COUNT_TOKENS, json=_request("hi"), headers=HEADERS)
        assert response.status_code == 200

    assert (chat.call_count, messages.call_count, count.call_count) == (1, 1, 1)
    assert defaults.call_count == 0


async def test_an_anthropic_passthrough_path_goes_to_the_anthropic_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    listing = {"data": [{"id": "claude-example", "type": "model"}], "has_more": False}
    route = upstream.get(f"{ANTHROPIC}/v1/models").respond(json=listing)
    openai = upstream.get(f"{OPENAI}/v1/models").respond(json={})
    response = await client.get("/v1/models?limit=2", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == listing
    assert str(route.calls.last.request.url) == f"{ANTHROPIC}/v1/models?limit=2"
    assert route.calls.last.request.headers["x-api-key"] == HEADERS["x-api-key"]
    assert openai.call_count == 0


async def test_an_anthropic_passthrough_failure_uses_the_anthropic_error_shape(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.get(f"{ANTHROPIC}/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    response = await client.get("/v1/models")
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "api_error"


async def test_a_streamed_message_logs_counts_and_never_text(
    client: httpx.AsyncClient, upstream: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    body = _sse(
        MESSAGE_START,
        _start(0, _text("")),
        _delta(0, "text_delta", "Dear [PERSON_1] and [PERSON_9]"),
        _stop(0),
        MESSAGE_STOP,
    )
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(headers=SSE, content=body)
    response = await client.post(MESSAGES, json=_request(TEXT, stream=True), headers=HEADERS)
    assert _streamed(response.content, 0, "text") == "Dear Juan Pérez and [PERSON_9]"

    records = [
        record
        for record in caplog.records
        if not (record.name == "httpx" and "gateway.test" in record.getMessage())
    ]
    for record in records:
        for value in VALUES:
            assert value not in record.getMessage()
            assert value not in repr(vars(record))
    lines = [
        getattr(record, "fields", None) for record in records if record.name == "aliasgate.proxy"
    ]
    assert lines == [
        {
            "request_id": response.headers["x-aliasgate-request-id"],
            "route": "POST /v1/messages",
            "status": 200,
            "duration_ms": lines[0]["duration_ms"],  # type: ignore[index]
            "slots": 1,
            "entities": {"PERSON": 1, "EMAIL": 1, "ORG": 1},
            "unresolved_placeholders": 1,
        }
    ]


# Keys of data objects (slice 21)


async def test_detected_values_in_tool_use_and_example_keys_are_pseudonymised(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=_message(_text("ok")))
    tool_use = {
        "type": "tool_use",
        "id": "toolu_example01",
        "name": "f",
        "input": {"attendees": {"Juan Pérez": "accepted", "maria.lopez@example.org": "x"}},
    }
    request = _request("Escribe a Juan Pérez sobre el pedido.")
    request["messages"].append({"role": "assistant", "content": [tool_use]})
    request["tools"] = [
        {
            "name": "f",
            "input_schema": {"type": "object"},
            "input_examples": [{"Juan Pérez": "accepted"}],
        }
    ]
    response = await client.post(MESSAGES, json=request, headers=HEADERS)

    assert response.status_code == 200
    sent = _sent(route)
    assert sent["messages"][0]["content"] == "Escribe a [PERSON_1] sobre el pedido."
    assert sent["messages"][1]["content"][0]["input"] == {
        "attendees": {"[PERSON_1]": "accepted", "[EMAIL_1]": "x"}
    }
    assert sent["tools"][0]["input_examples"] == [{"[PERSON_1]": "accepted"}]
    raw = route.calls.last.request.content.decode()
    for value in (*VALUES, "maria.lopez"):
        assert value not in raw


async def test_count_tokens_pseudonymises_tool_use_keys_too(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(f"{ANTHROPIC}{COUNT_TOKENS}").respond(json={"input_tokens": 42})
    tool_use = {"type": "tool_use", "id": "t", "name": "f", "input": {"Juan Pérez": "ok"}}
    request = _request("Escribe a Juan Pérez.")
    request.pop("max_tokens")
    request["messages"].append({"role": "assistant", "content": [tool_use]})
    response = await client.post(COUNT_TOKENS, json=request, headers=HEADERS)
    assert response.status_code == 200
    assert _sent(route)["messages"][1]["content"][0]["input"] == {"[PERSON_1]": "ok"}


async def test_a_tool_use_input_key_of_a_non_streamed_answer_reaches_the_client_restored(
    client: httpx.AsyncClient, upstream: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    tool_use = {
        "type": "tool_use",
        "id": "toolu_example01",
        "name": "f",
        "input": {"attendees": {"[PERSON_1]": "accepted", "[PERSON_9]": "pending"}},
    }
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=_message(tool_use))
    response = await client.post(MESSAGES, json=_request(TEXT), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["content"][0]["input"] == {
        "attendees": {"Juan Pérez": "accepted", "[PERSON_9]": "pending"}
    }
    lines = [
        getattr(record, "fields", {})
        for record in caplog.records
        if record.name == "aliasgate.proxy"
    ]
    assert [line["unresolved_placeholders"] for line in lines] == [1]


async def test_a_detected_value_in_a_tool_schema_property_name_gets_leak_audit_failed(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=_message(_text("ok")))
    schema = {"type": "object", "properties": {"Juan Pérez": {"type": "string"}}}
    request = _request("Escribe a Juan Pérez.", tools=[{"name": "f", "input_schema": schema}])
    response = await client.post(MESSAGES, json=request, headers=HEADERS)
    assert response.status_code == 500
    assert "leak_audit_failed" in response.json()["error"]["message"]
    assert "Juan" not in response.text
    assert upstream.calls.call_count == 0


async def test_two_tool_use_keys_that_become_the_same_get_400_and_nothing_is_forwarded(
    make_client: ClientFactory, upstream: respx.MockRouter, settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "orgs.yaml").write_text("terms:\n  - term: ACME\n    type: ORG\n", encoding="utf-8")
    data = settings.model_dump()
    data["detection"]["dictionary"]["files"] = [
        *data["detection"]["dictionary"]["files"],
        tmp_path / "orgs.yaml",
    ]
    custom = Settings.model_validate(data)
    upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=_message(_text("ok")))
    tool_use = {"type": "tool_use", "id": "t", "name": "f", "input": {"ACME": "a", "Acme": "b"}}
    request = _request("hello")
    request["messages"].append({"role": "assistant", "content": [tool_use]})
    async with make_client(custom) as client:
        response = await client.post(MESSAGES, json=request, headers=HEADERS)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["message"] == (
        "Malformed request: messages[1].content[0].input: two keys of one object would become"
        " the same (malformed_request)"
    )
    assert "ACME" not in response.text
    assert "Acme" not in response.text
    assert upstream.calls.call_count == 0


async def test_a_tool_use_key_that_only_a_contextual_detector_finds_is_forwarded_unchanged(
    make_client: ClientFactory, upstream: respx.MockRouter
) -> None:
    route = upstream.post(f"{ANTHROPIC}{MESSAGES}").respond(json=_message(_text("ok")))
    engine = Pseudonymizer([], contextual=[WordListDetector({"owner": "PERSON"}, name="context")])
    tool_use = {"type": "tool_use", "id": "t", "name": "f", "input": {"owner": "x"}}
    request = _request("hello")
    request["messages"].append({"role": "assistant", "content": [tool_use]})
    async with make_client(pseudonymizer=engine) as client:
        response = await client.post(MESSAGES, json=request, headers=HEADERS)
        assert response.status_code == 200
        assert _sent(route)["messages"][1]["content"][0]["input"] == {"owner": "x"}
        response = await client.post(MESSAGES, json=_request("the owner"), headers=HEADERS)
        assert _sent(route)["messages"][0]["content"] == "the [PERSON_1]"
