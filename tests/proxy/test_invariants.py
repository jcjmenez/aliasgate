"""One test per security invariant of the threat model, named after it (docs/threat-model.md)."""

import asyncio
import json
import logging
import pickle
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml
from hygiene import VALUES, assert_clean, capture_everything, gateway_records
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from payloads import CHAT, OPENAI, ClientFactory, chat_response

from aliasgate.config import Settings, load_settings
from aliasgate.dialects.anthropic_messages import AnthropicMessagesDialect
from aliasgate.dialects.base import PreparedRequest
from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.engine import Pseudonymizer
from aliasgate.mapping import Mapping
from aliasgate.proxy import app as app_module
from aliasgate.proxy.app import create_app
from aliasgate.terms import build_term_pattern
from fakes import FailingDetector

ANTHROPIC = "https://api.anthropic.com"
MESSAGES = "/v1/messages"
TEXT = "Write to Juan Pérez (juan.perez@example.com) at ACME Corporation."
SSE = {"content-type": "text/event-stream; charset=utf-8"}


@dataclass(frozen=True)
class Dialect:
    """What a test needs to talk to one route of the gateway and to fake its upstream."""

    name: str
    path: str
    upstream_url: str
    dialect_class: type[Any]
    headers: dict[str, str] = field(default_factory=dict)

    def request(self, *contents: object, **fields: Any) -> dict[str, Any]:
        messages = [{"role": "user", "content": content} for content in contents]
        if self.name == "openai":
            return {"model": "gpt-example", "messages": messages, **fields}
        return {"model": "claude-example", "max_tokens": 64, "messages": messages, **fields}

    def image(self) -> dict[str, Any]:
        if self.name == "openai":
            part = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
        else:
            part = {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
        return self.request([part])

    def malformed(self) -> dict[str, Any]:
        return {"model": "example", "messages": "Juan Pérez"}

    def reply(self, text: str) -> dict[str, Any]:
        if self.name == "openai":
            return chat_response(text)
        return {
            "id": "msg_example01",
            "type": "message",
            "role": "assistant",
            "model": "claude-example",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }

    def reply_text(self, body: dict[str, Any]) -> str:
        if self.name == "openai":
            content: str = body["choices"][0]["message"]["content"]
            return content
        text: str = body["content"][0]["text"]
        return text

    def stream(self, text: str) -> bytes:
        if self.name == "openai":
            choice = {"index": 0, "delta": {"content": text}, "finish_reason": None}
            chunk = {
                "id": "c",
                "object": "chat.completion.chunk",
                "model": "m",
                "choices": [choice],
            }
            return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": text}
        events: list[dict[str, Any]] = [
            {"type": "message_start", "message": {**self.reply(""), "content": []}},
            {"type": "content_block_start", "index": 0, "content_block": block},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_stop"},
        ]
        return b"".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
        )


OPENAI_ROUTE = Dialect("openai", CHAT, OPENAI + CHAT, OpenAIChatDialect)
ANTHROPIC_ROUTE = Dialect(
    "anthropic",
    MESSAGES,
    ANTHROPIC + MESSAGES,
    AnthropicMessagesDialect,
    {"x-api-key": "sk-" + "x" * 24, "anthropic-version": "2023-06-01"},
)
DIALECTS = [pytest.param(OPENAI_ROUTE, id="openai"), pytest.param(ANTHROPIC_ROUTE, id="anthropic")]


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    """A mocked upstream with no base URL: routes name full URLs, anything else fails."""
    with respx.mock(assert_all_called=False) as mock:
        yield mock


# Invariant 1: no request is forwarded unless every slot went through the engine without error.

FAILURES = [
    "too-large",
    "invalid-json",
    "not-an-object",
    "malformed",
    "unsupported-content",
    "prepare-raises",
    "detector-raises",
    "apply-raises",
    "slots-not-written",
    "audit-raises",
    "unencodable",
]


@pytest.mark.parametrize("failure", FAILURES)
@pytest.mark.parametrize("dialect", DIALECTS)
async def test_no_request_is_forwarded_when_pseudonymisation_fails(
    make_client: ClientFactory,
    settings: Settings,
    upstream: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    dialect: Dialect,
    failure: str,
) -> None:
    upstream.post(dialect.upstream_url).respond(json=dialect.reply("ok"))
    body: object = dialect.request(TEXT)
    custom: Settings | None = None
    pseudonymizer: Pseudonymizer | None = None
    content: bytes | None = None

    def boom(*args: object, **kwargs: object) -> Any:
        raise RuntimeError(TEXT)

    if failure == "too-large":
        data = settings.model_dump()
        data["listen"]["max_request_bytes"] = 16
        custom = Settings.model_validate(data)
    elif failure == "invalid-json":
        content = b'{"messages": [{"role": "user", "content": "Juan P\xc3\xa9rez"'
    elif failure == "not-an-object":
        body = [dialect.request(TEXT)]
    elif failure == "malformed":
        body = dialect.malformed()
    elif failure == "unsupported-content":
        body = dialect.image()
    elif failure == "prepare-raises":
        monkeypatch.setattr(dialect.dialect_class, "prepare_request", boom)
    elif failure == "detector-raises":
        pseudonymizer = Pseudonymizer([FailingDetector()])
    elif failure == "apply-raises":
        monkeypatch.setattr(PreparedRequest, "apply", boom)
    elif failure == "slots-not-written":
        # As if a dialect forgot every field: the leak audit must stop the request.
        monkeypatch.setattr(PreparedRequest, "apply", lambda self, texts: None)
    elif failure == "audit-raises":
        monkeypatch.setattr(app_module, "audit_outbound", boom)
    elif failure == "unencodable":
        # An escaped lone surrogate parses, but cannot be sent as UTF-8.
        content = json.dumps(dialect.request(TEXT + chr(0xD800))).encode()

    async with make_client(custom, pseudonymizer=pseudonymizer) as client:
        if content is None:
            content = json.dumps(body).encode()
        response = await client.post(
            dialect.path,
            content=content,
            headers={**dialect.headers, "content-type": "application/json"},
        )

    assert response.status_code >= 400
    assert upstream.calls.call_count == 0
    assert "Juan" not in response.text


# Invariant 2: after pseudonymisation, no detected surface is present in what is sent upstream.

SURFACES = (
    "Juan Pérez",
    "ACME Corporation",
    "juan.perez@example.com",
    "a.b+tag@mail.example.org",
    "ES91 2100 0418 4502 0005 1332",
    "DE89 3704 0044 0532 0130 00",
)
FILLER = ("please", "call", "about", "the", "invoice", "Hello", ",", ".", "today", "and")
_text = st.lists(st.sampled_from(SURFACES + FILLER), min_size=1, max_size=8).map(" ".join)
_kinds = st.sampled_from(["system", "text", "parts", "tool_call", "tool_result"])
_conversation = st.lists(st.tuples(_kinds, _text), min_size=1, max_size=6)


def _openai_body(parts: list[tuple[str, str]]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for kind, text in parts:
        if kind == "system":
            messages.append({"role": "system", "content": text})
        elif kind == "text":
            messages.append({"role": "user", "content": text})
        elif kind == "parts":
            messages.append({"role": "user", "content": [{"type": "text", "text": text}]})
        elif kind == "tool_call":
            function = {"name": "send", "arguments": json.dumps({"to": text})}
            call = {"id": "call_1", "type": "function", "function": function}
            messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        else:
            messages.append({"role": "tool", "tool_call_id": "call_1", "content": text})
    return {"model": "gpt-example", "messages": messages}


def _anthropic_body(parts: list[tuple[str, str]]) -> dict[str, Any]:
    system: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for kind, text in parts:
        if kind == "system":
            system.append({"type": "text", "text": text})
        elif kind == "text":
            messages.append({"role": "user", "content": text})
        elif kind == "parts":
            messages.append({"role": "user", "content": [{"type": "text", "text": text}]})
        elif kind == "tool_call":
            block = {"type": "tool_use", "id": "toolu_1", "name": "send", "input": {"to": text}}
            messages.append({"role": "assistant", "content": [block]})
        else:
            block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": text}
            messages.append({"role": "user", "content": [block]})
    body: dict[str, Any] = {"model": "claude-example", "max_tokens": 64, "messages": messages}
    if system:
        body["system"] = system
    return body


@pytest.fixture(scope="module")
def dictionary_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    directory: Path = tmp_path_factory.mktemp("invariants")
    terms = [{"term": "Juan Pérez", "type": "PERSON"}, {"term": "ACME Corporation", "type": "ORG"}]
    (directory / "dictionary.yaml").write_text(
        yaml.safe_dump({"terms": terms}, allow_unicode=True), encoding="utf-8"
    )
    config = directory / "aliasgate.yaml"
    config.write_text("detection:\n  dictionary:\n    files: [dictionary.yaml]\n", encoding="utf-8")
    return load_settings(config, env={})


async def _sent_upstream(settings: Settings, dialect: Dialect, body: dict[str, Any]) -> str:
    """Send `body` through a fresh gateway; return what the upstream received, as text."""
    async with respx.mock(assert_all_called=False) as mock, httpx.AsyncClient() as http:
        route = mock.post(dialect.upstream_url).respond(json=dialect.reply("ok"))
        app = create_app(settings, http_client=http)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            response = await client.post(dialect.path, json=body, headers=dialect.headers)
        assert response.status_code == 200, response.text
        assert route.call_count == 1
        return route.calls.last.request.content.decode("utf-8")


@hypothesis_settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(conversation=_conversation, use_anthropic=st.booleans())
def test_no_detected_surface_reaches_the_upstream(
    dictionary_settings: Settings, conversation: list[tuple[str, str]], use_anthropic: bool
) -> None:
    dialect = ANTHROPIC_ROUTE if use_anthropic else OPENAI_ROUTE
    body = _anthropic_body(conversation) if use_anthropic else _openai_body(conversation)
    sent = asyncio.run(_sent_upstream(dictionary_settings, dialect, body))
    for surface in SURFACES:
        assert build_term_pattern(surface).search(sent) is None, "a detected value was sent"


# Invariant 3: the mapping is never written to disk, logged, or sent upstream.


def test_mapping_is_never_serialised() -> None:
    mapping = Mapping()
    entity = mapping.assign("PERSON", "Juan Pérez")
    for value in (mapping, entity):
        for name in ("to_json", "model_dump", "model_dump_json", "json", "dict", "to_dict"):
            assert not hasattr(value, name)
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(TypeError):
            value.__getstate__()
        with pytest.raises(TypeError):
            json.dumps(value)
        assert "Juan" not in repr(value)
        assert "Juan" not in str(value)


# Invariant 4: logs and metrics never contain request text, response text or surfaces.


@pytest.mark.parametrize("dialect", DIALECTS)
async def test_logs_and_metrics_contain_no_text(
    client: httpx.AsyncClient,
    upstream: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
    dialect: Dialect,
) -> None:
    capture = capture_everything(caplog)
    route = upstream.post(dialect.upstream_url)
    route.respond(json=dialect.reply("Dear [PERSON_1] of [ORG_1], [PERSON_7]"))
    headers = dialect.headers
    await client.post(dialect.path, json=dialect.request(TEXT), headers=headers)
    route.respond(200, headers=SSE, content=dialect.stream("Dear [PERSON_1] [PERSON_9]"))
    streaming = dialect.request(TEXT, stream=True)
    streamed = await client.post(dialect.path, json=streaming, headers=headers)
    assert "Juan Pérez" in streamed.text
    route.respond(429, json={"error": {"message": "Too many requests for [PERSON_1]"}})
    await client.post(dialect.path, json=dialect.request(TEXT), headers=headers)
    await client.post(dialect.path, json=dialect.image(), headers=headers)
    await client.post(dialect.path, json=dialect.request(TEXT, metadata={"note": "Juan Pérez"}),
                      headers=headers)  # fmt: skip
    await client.post("/v1/Juan Pérez/juan.perez@example.com", json=dialect.request(TEXT),
                      headers=headers)  # fmt: skip
    metrics = await client.get("/metrics")

    assert metrics.status_code == 200
    for value in VALUES:
        assert value not in metrics.text
    assert "unknown" in metrics.text
    assert_clean(gateway_records(capture))
    leak_lines = [
        record
        for record in gateway_records(capture)
        if record.name == "aliasgate.proxy" and record.getMessage() == "leak audit failed"
    ]
    assert len(leak_lines) == 1
    assert leak_lines[0].levelno == logging.ERROR
    fields = leak_lines[0].fields  # type: ignore[attr-defined]
    assert set(fields) == {"request_id", "leaked_entities", "leaked_types"}


# Invariant 5: unknown endpoints and uninspectable content are rejected by default.


@pytest.mark.parametrize("dialect", DIALECTS)
async def test_unknown_endpoints_and_content_are_rejected_by_default(
    make_client: ClientFactory, upstream: respx.MockRouter, dialect: Dialect
) -> None:
    upstream.route().respond(json=dialect.reply("ok"))
    async with make_client(Settings()) as client:
        image = await client.post(dialect.path, json=dialect.image(), headers=dialect.headers)
        unknown = await client.post("/v1/responses", json={}, headers=dialect.headers)
        models = await client.get("/v1/models", headers=dialect.headers)
        wrong_method = await client.get(dialect.path, headers=dialect.headers)
    assert image.status_code == 422
    assert [unknown.status_code, models.status_code, wrong_method.status_code] == [404] * 3
    assert upstream.calls.call_count == 0


# Invariant 6: restoration only replaces placeholders that exist in the same request's mapping.


@pytest.mark.parametrize("dialect", DIALECTS)
async def test_restoration_uses_only_the_request_mapping(
    client: httpx.AsyncClient, upstream: respx.MockRouter, dialect: Dialect
) -> None:
    both_arrived = asyncio.Event()
    arrived: list[str] = []
    answer = "Dear [PERSON_1], see [EMAIL_2] at [ORG_1]"

    async def respond(request: httpx.Request) -> httpx.Response:
        arrived.append(request.content.decode())
        if len(arrived) == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5)
        return httpx.Response(200, json=dialect.reply(answer))

    upstream.post(dialect.upstream_url).mock(side_effect=respond)
    only_a_person = dialect.request("Write to Juan Pérez")
    four_entities = dialect.request(
        "Juan Pérez, ACME Corporation, juan.perez@example.com, a.b+tag@mail.example.org"
    )
    first, second = await asyncio.gather(
        client.post(dialect.path, json=only_a_person, headers=dialect.headers),
        client.post(dialect.path, json=four_entities, headers=dialect.headers),
    )
    assert len(arrived) == 2
    assert dialect.reply_text(first.json()) == "Dear Juan Pérez, see [EMAIL_2] at [ORG_1]"
    assert dialect.reply_text(second.json()) == (
        "Dear Juan Pérez, see a.b+tag@mail.example.org at ACME Corporation"
    )
