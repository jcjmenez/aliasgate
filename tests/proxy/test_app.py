import asyncio
import gzip
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from payloads import CHAT, OPENAI, ClientFactory, chat_request, chat_response

from aliasgate.config import ListenSettings, PolicySettings, Settings
from aliasgate.dialects.base import PreparedRequest
from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.engine import Pseudonymizer
from aliasgate.proxy import app as app_module
from aliasgate.proxy.app import create_app
from fakes import FailingDetector

VALUES = ("Juan Pérez", "juan.perez@example.com", "ACME Corporation")
HAPPY_TEXT = "Write to Juan Pérez (juan.perez@example.com) at ACME Corporation."


def _error(response: httpx.Response) -> dict[str, Any]:
    error: dict[str, Any] = response.json()["error"]
    return error


def _sent(route: respx.Route) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


def _content(response: httpx.Response) -> Any:
    return response.json()["choices"][0]["message"]["content"]


async def test_health_endpoint_answers_without_calling_the_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert upstream.calls.call_count == 0


async def test_happy_path_sends_placeholders_and_returns_real_values(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1] of [ORG_1]"))
    response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))

    assert response.status_code == 200
    assert _content(response) == "Dear Juan Pérez of ACME Corporation"
    raw = route.calls.last.request.content.decode()
    for value in (*VALUES, "Pérez", "juan.perez"):
        assert value not in raw
    assert _sent(route)["messages"][0]["content"] == "Write to [PERSON_1] ([EMAIL_1]) at [ORG_1]."
    assert response.json()["usage"] == chat_response(None)["usage"]


async def test_tool_call_arguments_are_restored_into_valid_json(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "send_email", "arguments": '{"email":"[EMAIL_1]"}'},
    }
    upstream.post(CHAT).respond(json=chat_response(None, tool_calls=[call]))
    response = await client.post(CHAT, json=chat_request("Email juan.perez@example.com"))
    arguments = response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"email": "juan.perez@example.com"}


async def test_authorization_reaches_the_upstream_and_host_is_the_upstreams(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    token = "Bearer " + "x" * 32
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    await client.post(
        CHAT,
        json=chat_request("hello"),
        headers={"authorization": token, "connection": "x-secret", "x-secret": "1"},
    )
    sent = route.calls.last.request
    assert sent.headers["authorization"] == token
    assert sent.headers["host"] == "api.openai.com"
    assert "x-secret" not in sent.headers


async def test_query_string_is_forwarded(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    await client.post(f"{CHAT}?api-version=2026-01-01&note=a%20b", json=chat_request("hello"))
    assert route.calls.last.request.url.query == b"api-version=2026-01-01&note=a%20b"
    assert str(route.calls.last.request.url).startswith(f"{OPENAI}{CHAT}?")


async def test_a_request_without_query_string_is_forwarded_without_one(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    await client.post(CHAT, json=chat_request("hello"))
    assert str(route.calls.last.request.url) == f"{OPENAI}{CHAT}"


async def test_end_user_identifiers_are_absent_from_the_upstream_body(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    body = chat_request("hello", user="end-user-0001", safety_identifier="hash-0001")
    await client.post(CHAT, json=body)
    assert "user" not in _sent(route)
    assert "safety_identifier" not in _sent(route)


async def test_the_forwarded_body_is_json_even_without_a_client_content_type(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    await client.post(CHAT, content=json.dumps(chat_request("hello")).encode())
    assert route.calls.last.request.headers["content-type"] == "application/json"


async def test_stream_true_is_not_supported_yet(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    response = await client.post(CHAT, json=chat_request(HAPPY_TEXT, stream=True))
    assert response.status_code == 501
    assert _error(response)["code"] == "streaming_not_supported"
    assert upstream.calls.call_count == 0


def _image_request() -> dict[str, Any]:
    parts = [
        {"type": "text", "text": "Describe this photo of Juan Pérez"},
        {"type": "image_url", "image_url": {"url": "https://example.com/photo.png"}},
    ]
    return {"model": "gpt-example", "messages": [{"role": "user", "content": parts}]}


async def test_image_content_is_rejected_without_calling_the_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    response = await client.post(CHAT, json=_image_request())
    assert response.status_code == 422
    assert _error(response) == {
        "message": "Unsupported content: image_url",
        "type": "aliasgate_error",
        "param": None,
        "code": "unsupported_content",
    }
    assert upstream.calls.call_count == 0


async def test_image_content_is_forwarded_unchanged_with_policy_pass(
    make_client: ClientFactory, settings: Settings, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    custom = settings.model_copy(update={"policy": PolicySettings(unsupported_content="pass")})
    async with make_client(custom) as client:
        response = await client.post(CHAT, json=_image_request())
    assert response.status_code == 200
    parts = _sent(route)["messages"][0]["content"]
    assert parts[0]["text"] == "Describe this photo of [PERSON_1]"
    assert parts[1] == _image_request()["messages"][0]["content"][1]


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b'{"model": "gpt-example", "messages": [', "invalid_json"),
        (b"", "invalid_json"),
        (b'[{"role": "user", "content": "Juan P\xc3\xa9rez"}]', "invalid_json"),
        (b'"Juan P\xc3\xa9rez"', "invalid_json"),
        (b'{"messages": "Juan"}\xff', "invalid_json"),
        (b'{"model": "gpt-example"}', "malformed_request"),
        (b'{"messages": [{"role": "user", "content": 7}]}', "malformed_request"),
        (
            b'{"messages": [{"role": "user", "content": "Juan \\ud800 P\\u00e9rez"}]}',
            "invalid_json",
        ),
        (b"[" * 100_000 + b"]" * 100_000, "invalid_json"),
    ],
)
async def test_bad_request_bodies_get_400_without_calling_the_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter, content: bytes, code: str
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    response = await client.post(
        CHAT, content=content, headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert _error(response)["code"] == code
    assert "Juan" not in response.text
    assert upstream.calls.call_count == 0


async def test_malformed_request_message_names_the_position_only(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": ["Juan Pérez"]}]}]}
    response = await client.post(CHAT, json=body)
    assert response.status_code == 400
    assert _error(response)["message"] == (
        "Malformed request: messages[0].content[0].text: expected a string"
    )


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def test_a_body_over_the_limit_gets_413_without_calling_the_upstream(
    make_client: ClientFactory, settings: Settings, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    custom = settings.model_copy(update={"listen": ListenSettings(max_request_bytes=1000)})
    big = json.dumps(chat_request("Juan Pérez " * 200)).encode()
    small = json.dumps(chat_request("hello")).encode()
    async with make_client(custom) as client:
        declared = await client.post(CHAT, content=big)
        streamed = await client.post(CHAT, content=_chunks(big, 100))
        streamed_small = await client.post(CHAT, content=_chunks(small, 10))
    for response in (declared, streamed):
        assert response.status_code == 413
        assert _error(response)["code"] == "request_too_large"
    assert streamed_small.status_code == 200
    assert upstream.calls.call_count == 1


async def test_a_failing_detector_gets_500_without_calling_the_upstream(
    make_client: ClientFactory, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    async with make_client(pseudonymizer=Pseudonymizer([FailingDetector()])) as client:
        response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))
    assert response.status_code == 500
    assert _error(response)["code"] == "pseudonymization_failed"
    for value in (*VALUES, "cannot process", "failing"):
        assert value not in response.text
    assert upstream.calls.call_count == 0


async def test_a_failure_while_writing_the_slots_gets_500_without_calling_the_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_apply(self: object, new_texts: object) -> None:
        raise RuntimeError("Juan Pérez")

    monkeypatch.setattr(PreparedRequest, "apply", broken_apply)
    upstream.post(CHAT).respond(json=chat_response("ok"))
    response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))
    assert response.status_code == 500
    assert _error(response)["code"] == "pseudonymization_failed"
    assert "Juan" not in response.text
    assert upstream.calls.call_count == 0


async def test_upstream_error_bodies_are_restored_and_headers_kept(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(
        429,
        json={"error": {"message": "Too many requests about [PERSON_1]", "code": "rate_limit"}},
        headers={"retry-after": "7"},
    )
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert _error(response) == {
        "message": "Too many requests about Juan Pérez",
        "code": "rate_limit",
    }


async def test_an_upstream_json_array_is_restored_string_by_string(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(400, json=["[PERSON_1]", {"detail": "[PERSON_1]"}, 3])
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert response.json() == ["Juan Pérez", {"detail": "Juan Pérez"}, 3]


async def test_a_json_media_type_with_suffix_is_restored(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(
        400,
        content=json.dumps({"detail": "bad [PERSON_1]"}).encode(),
        headers={"content-type": "application/problem+json; charset=utf-8"},
    )
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert response.json() == {"detail": "bad Juan Pérez"}
    assert response.headers["content-type"] == "application/problem+json; charset=utf-8"


@pytest.mark.parametrize(
    ("side_effect", "status", "code"),
    [
        (httpx.ReadTimeout("timed out"), 504, "upstream_timeout"),
        (httpx.ConnectTimeout("timed out"), 504, "upstream_timeout"),
        (httpx.ConnectError("refused"), 502, "upstream_unreachable"),
        (httpx.RemoteProtocolError("bad"), 502, "upstream_unreachable"),
    ],
)
async def test_upstream_failures_map_to_gateway_errors(
    client: httpx.AsyncClient,
    upstream: respx.MockRouter,
    side_effect: Exception,
    status: int,
    code: str,
) -> None:
    upstream.post(CHAT).mock(side_effect=side_effect)
    response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))
    assert response.status_code == status
    assert _error(response)["code"] == code
    assert "Juan" not in response.text


@pytest.mark.parametrize(
    "headers",
    [{"content-type": "text/plain; charset=utf-8"}, {"content-type": "application/json"}, {}],
)
async def test_non_json_upstream_bodies_are_passed_through_unchanged(
    client: httpx.AsyncClient, upstream: respx.MockRouter, headers: dict[str, str]
) -> None:
    upstream.post(CHAT).respond(200, content=b"plain [PERSON_1] text", headers=headers)
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert response.status_code == 200
    assert response.content == b"plain [PERSON_1] text"
    assert response.headers.get("content-type") == headers.get("content-type")


async def test_compressed_upstream_bodies_are_decoded_and_sent_uncompressed(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    payload = json.dumps(chat_response("Hi [PERSON_1]")).encode()
    upstream.post(CHAT).respond(
        200,
        content=gzip.compress(payload),
        headers={"content-type": "application/json", "content-encoding": "gzip"},
    )
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == len(response.content)
    assert _content(response) == "Hi Juan Pérez"


async def test_upstream_redirects_are_returned_not_followed(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(307, headers={"location": "https://example.com/elsewhere"})
    response = await client.post(CHAT, json=chat_request("hello"))
    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/elsewhere"
    assert upstream.calls.call_count == 1


async def test_a_failure_while_restoring_gets_502_and_never_the_unrestored_body(
    client: httpx.AsyncClient, upstream: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_restore(self: object, body: object, mapping: object) -> None:
        raise RuntimeError("Juan Pérez")

    monkeypatch.setattr(OpenAIChatDialect, "restore_response", broken_restore)
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1]"))
    response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))
    assert response.status_code == 502
    assert _error(response)["code"] == "restoration_failed"
    assert "[PERSON_1]" not in response.text
    assert "Juan" not in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/embeddings"),
        ("POST", "/v1/responses"),
        ("GET", CHAT),
        ("POST", "/v1/chat/completions/"),
        ("POST", "/v1/chat/%63ompletions/extra"),
        ("DELETE", "/v1/models"),
        ("POST", "/healthz"),
        ("PROPFIND", "/"),
    ],
)
async def test_unknown_endpoints_get_404_without_calling_the_upstream(
    client: httpx.AsyncClient, upstream: respx.MockRouter, method: str, path: str
) -> None:
    response = await client.request(method, path, json=chat_request(HAPPY_TEXT))
    assert response.status_code == 404
    assert _error(response)["code"] == "unknown_endpoint"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-aliasgate-request-id"])
    assert upstream.calls.call_count == 0


async def test_a_passthrough_path_is_forwarded_unchanged(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    listing = {"object": "list", "data": [{"id": "gpt-example", "owned_by": "[PERSON_1]"}]}
    route = upstream.get("/v1/models").respond(json=listing, headers={"x-request-id": "req_1"})
    token = "Bearer " + "y" * 32
    response = await client.get("/v1/models?limit=2", headers={"authorization": token})
    assert response.status_code == 200
    assert response.json() == listing
    assert response.headers["x-request-id"] == "req_1"
    sent = route.calls.last.request
    assert sent.url.query == b"limit=2"
    assert sent.headers["authorization"] == token
    assert sent.content == b""


async def test_a_passthrough_failure_maps_to_a_gateway_error(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.get("/v1/models").mock(side_effect=httpx.ReadTimeout("timed out"))
    response = await client.get("/v1/models")
    assert response.status_code == 504
    assert _error(response)["code"] == "upstream_timeout"


async def test_unresolved_placeholders_are_returned_as_they_are(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("[PERSON_1] and [PERSON_7]"))
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert _content(response) == "Juan Pérez and [PERSON_7]"


async def test_every_response_carries_a_fresh_request_id(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    responses = [
        await client.post(CHAT, json=chat_request("hello")),
        await client.post(CHAT, json=chat_request("hello", stream=True)),
        await client.get("/healthz"),
    ]
    ids = [response.headers["x-aliasgate-request-id"] for response in responses]
    assert all(re.fullmatch(r"[0-9a-f]{32}", request_id) for request_id in ids)
    assert len(set(ids)) == 3


async def test_concurrent_requests_do_not_share_mappings(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    arrived = 0
    both_in_flight = asyncio.Event()

    async def echo(request: httpx.Request) -> httpx.Response:
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_in_flight.set()
        await asyncio.wait_for(both_in_flight.wait(), timeout=10)
        text = json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json=chat_response(f"You said: {text}"))

    upstream.post(CHAT).mock(side_effect=echo)
    first, second = await asyncio.gather(
        client.post(CHAT, json=chat_request("Contact juan.perez@example.com")),
        client.post(CHAT, json=chat_request("Contact a.b+tag@mail.example.org")),
    )
    assert _content(first) == "You said: Contact juan.perez@example.com"
    assert _content(second) == "You said: Contact a.b+tag@mail.example.org"
    sent = [json.loads(call.request.content) for call in upstream.calls]
    assert all(body["messages"][0]["content"] == "Contact [EMAIL_1]" for body in sent)


async def test_unexpected_errors_get_a_fixed_500_message(
    client: httpx.AsyncClient, upstream: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_filter(headers: object) -> list[tuple[str, str]]:
        raise RuntimeError("Juan Pérez juan.perez@example.com")

    monkeypatch.setattr(app_module, "filter_request_headers", broken_filter)
    upstream.post(CHAT).respond(json=chat_response("ok"))
    response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))
    assert response.status_code == 500
    assert _error(response) == {
        "message": "Internal gateway error.",
        "type": "aliasgate_error",
        "param": None,
        "code": "internal_error",
    }
    assert upstream.calls.call_count == 0


async def test_without_lifespan_or_injected_client_requests_fail_closed(
    settings: Settings, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.post(CHAT, json=chat_request(HAPPY_TEXT))
    assert response.status_code == 500
    assert _error(response)["code"] == "internal_error"
    assert upstream.calls.call_count == 0


async def test_the_lifespan_creates_and_closes_the_upstream_client(
    settings: Settings, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1]"))
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        async with app.router.lifespan_context(app):
            response = await client.post(CHAT, json=chat_request("Write to Juan Pérez"))
        after_shutdown = await client.post(CHAT, json=chat_request("Write to Juan Pérez"))
    assert response.status_code == 200
    assert _content(response) == "Dear Juan Pérez"
    assert after_shutdown.status_code == 500
    assert upstream.calls.call_count == 1


async def test_the_lifespan_leaves_an_injected_client_open(
    settings: Settings, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    async with httpx.AsyncClient() as http_client:
        app = create_app(settings, http_client=http_client)
        async with app.router.lifespan_context(app):
            pass
        assert not http_client.is_closed
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            response = await client.post(CHAT, json=chat_request("hello"))
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("upstream_body", "expected"),
    [('"Hi [PERSON_1]"', "Hi Juan Pérez"), ("42", 42), ("null", None)],
)
async def test_upstream_json_scalars_are_restored_or_returned(
    client: httpx.AsyncClient, upstream: respx.MockRouter, upstream_body: str, expected: object
) -> None:
    upstream.post(CHAT).respond(
        200, content=upstream_body.encode(), headers={"content-type": "application/json"}
    )
    response = await client.post(CHAT, json=chat_request("Ask about Juan Pérez"))
    assert response.json() == expected
