"""Prometheus metrics, the entity-count header and the leak audit as the proxy uses them."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from payloads import CHAT, ClientFactory, chat_request, chat_response
from prometheus_client.parser import text_string_to_metric_families

from aliasgate.config import Settings
from aliasgate.detectors.cache import CachedDetector
from aliasgate.dialects.base import StreamTransformer
from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.dialects.sse import SSEEvent
from aliasgate.engine import Pseudonymizer
from aliasgate.mapping import Mapping
from aliasgate.proxy.app import ENTITIES_HEADER
from fakes import FailingDetector, WordListDetector

ROUTE = "POST /v1/chat/completions"
TEXT = "Write to Juan Pérez (juan.perez@example.com) at ACME Corporation."
SSE = {"content-type": "text/event-stream; charset=utf-8"}

type Samples = dict[tuple[str, frozenset[tuple[str, str]]], float]


async def _metrics(client: httpx.AsyncClient) -> Samples:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


def _value(samples: Samples, name: str, **labels: str) -> float | None:
    return samples.get((name, frozenset(labels.items())))


def _with(settings: Settings, section: str, **values: Any) -> Settings:
    data = settings.model_dump()
    data[section].update(values)
    return Settings.model_validate(data)


async def test_a_happy_path_request_is_counted_once_with_its_entities(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1]"))
    assert (await client.post(CHAT, json=chat_request(TEXT))).status_code == 200
    samples = await _metrics(client)
    requests = "aliasgate_requests_total"
    assert _value(samples, requests, route=ROUTE, status_class="2xx", outcome="ok") == 1
    entities = "aliasgate_entities_total"
    assert _value(samples, entities, entity_type="EMAIL", source="rules") == 1
    assert _value(samples, entities, entity_type="PERSON", source="dictionary") == 1
    assert _value(samples, entities, entity_type="ORG", source="dictionary") == 1


async def test_propagated_occurrences_are_counted_under_propagation(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    # The rules do not find an IBAN cut across lines; propagation does.
    iban = "ES91 2100 0418 4502 0005 1332"
    await client.post(CHAT, json=chat_request(iban, iban.replace(" ", "\n")))
    samples = await _metrics(client)
    entities = "aliasgate_entities_total"
    assert _value(samples, entities, entity_type="IBAN", source="rules") == 1
    assert _value(samples, entities, entity_type="IBAN", source="propagation") == 1


async def test_responses_carry_the_number_of_entities(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1]"))
    response = await client.post(CHAT, json=chat_request(TEXT, "and juan.perez@example.com"))
    assert response.headers[ENTITIES_HEADER] == "3"
    assert (await client.get("/healthz")).headers[ENTITIES_HEADER] == "0"
    assert (await client.post("/v1/unknown", json={})).headers[ENTITIES_HEADER] == "0"


async def test_a_rejected_request_is_counted_with_its_code(client: httpx.AsyncClient) -> None:
    image = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    response = await client.post(CHAT, json={"messages": [{"role": "user", "content": [image]}]})
    assert response.status_code == 422
    samples = await _metrics(client)
    assert _value(
        samples, "aliasgate_rejections_total", route=ROUTE, code="unsupported_content"
    ) == 1  # fmt: skip
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="4xx", outcome="rejected"
    ) == 1  # fmt: skip


async def test_an_unknown_endpoint_is_counted_under_the_unknown_route(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/v1/Juan Pérez", json=chat_request(TEXT))
    samples = await _metrics(client)
    rejections = "aliasgate_rejections_total"
    assert _value(samples, rejections, route="unknown", code="unknown_endpoint") == 1


async def test_unresolved_placeholders_are_counted(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1] and [PERSON_7] [ORG_9]"))
    await client.post(CHAT, json=chat_request(TEXT))
    samples = await _metrics(client)
    assert _value(samples, "aliasgate_unresolved_placeholders_total", route=ROUTE) == 2


async def test_an_upstream_error_answer_is_an_upstream_error(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(429, json={"error": {"message": "slow down"}})
    await client.post(CHAT, json=chat_request(TEXT))
    samples = await _metrics(client)
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="4xx",
        outcome="upstream_error",
    ) == 1  # fmt: skip


async def test_an_unreachable_upstream_is_an_upstream_error_with_its_code(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).mock(side_effect=httpx.ConnectError("refused"))
    assert (await client.post(CHAT, json=chat_request(TEXT))).status_code == 502
    samples = await _metrics(client)
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="5xx",
        outcome="upstream_error",
    ) == 1  # fmt: skip
    rejections = "aliasgate_rejections_total"
    assert _value(samples, rejections, route=ROUTE, code="upstream_unreachable") == 1


async def test_a_failed_pseudonymisation_is_a_gateway_error(
    make_client: ClientFactory, upstream: respx.MockRouter
) -> None:
    async with make_client(pseudonymizer=Pseudonymizer([FailingDetector()])) as client:
        assert (await client.post(CHAT, json=chat_request(TEXT))).status_code == 500
        samples = await _metrics(client)
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="5xx",
        outcome="gateway_error",
    ) == 1  # fmt: skip
    rejections = "aliasgate_rejections_total"
    assert _value(samples, rejections, route=ROUTE, code="pseudonymization_failed") == 1


async def test_a_value_in_a_field_no_dialect_processes_fails_the_leak_audit(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    # No dialect reads an unknown top-level field; the name in it was detected in the message.
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    body = chat_request(TEXT, x_note="Juan Pérez")
    response = await client.post(CHAT, json=body)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "leak_audit_failed"
    assert "Juan" not in response.text
    assert route.call_count == 0
    samples = await _metrics(client)
    failures = "aliasgate_leak_audit_failures_total"
    assert _value(samples, failures, route=ROUTE, entity_type="PERSON") == 1
    assert _value(samples, failures, route=ROUTE, entity_type="ORG") is None
    rejections = "aliasgate_rejections_total"
    assert _value(samples, rejections, route=ROUTE, code="leak_audit_failed") == 1
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="5xx", outcome="rejected"
    ) == 1  # fmt: skip


async def test_the_leak_audit_can_be_turned_off(
    make_client: ClientFactory, settings: Settings, upstream: respx.MockRouter
) -> None:
    route = upstream.post(CHAT).respond(json=chat_response("ok"))
    async with make_client(_with(settings, "policy", leak_audit=False)) as client:
        body = chat_request(TEXT, x_note="Juan Pérez")
        assert (await client.post(CHAT, json=body)).status_code == 200
    assert json.loads(route.calls.last.request.content)["x_note"] == "Juan Pérez"


async def test_disabled_metrics_are_not_served(
    make_client: ClientFactory, settings: Settings
) -> None:
    async with make_client(_with(settings, "metrics", enabled=False)) as client:
        response = await client.get("/metrics")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_endpoint"


async def test_two_apps_have_independent_registries(
    make_client: ClientFactory, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    async with make_client() as first, make_client() as second:
        await first.post(CHAT, json=chat_request(TEXT))
        await first.post(CHAT, json=chat_request(TEXT))
        first_samples = await _metrics(first)
        second_samples = await _metrics(second)
    requests = "aliasgate_requests_total"
    assert _value(first_samples, requests, route=ROUTE, status_class="2xx", outcome="ok") == 2
    assert _value(second_samples, requests, route=ROUTE, status_class="2xx", outcome="ok") is None


async def test_detection_and_gateway_overhead_are_timed(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    await client.post(CHAT, json=chat_request(TEXT))
    samples = await _metrics(client)
    for detector in ("literal", "dictionary", "rules"):
        assert _value(samples, "aliasgate_detection_seconds_count", detector=detector) == 1
    overhead = "aliasgate_request_overhead_seconds_count"
    assert _value(samples, overhead, route=ROUTE, phase="pseudonymize") == 1
    assert _value(samples, overhead, route=ROUTE, phase="restore") == 1


async def test_the_detection_cache_is_counted_when_there_is_one(
    make_client: ClientFactory, upstream: respx.MockRouter
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    cached = CachedDetector(
        WordListDetector({"Alice": "PERSON"}, name="words"), fingerprint="test", max_entries=8
    )
    async with make_client(pseudonymizer=Pseudonymizer([cached])) as client:
        await client.post(CHAT, json=chat_request("Alice wrote"))
        await client.post(CHAT, json=chat_request("Alice wrote"))
        samples = await _metrics(client)
    assert _value(samples, "aliasgate_detection_cache_total", result="hit") == 1
    assert _value(samples, "aliasgate_detection_cache_total", result="miss") == 1


async def test_without_a_cached_detector_there_is_no_cache_metric(
    client: httpx.AsyncClient,
) -> None:
    samples = await _metrics(client)
    assert not any(name.startswith("aliasgate_detection_cache") for name, _ in samples)


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], fail_at: int | None = None) -> None:
        self.chunks = chunks
        self.fail_at = fail_at

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for number, chunk in enumerate(self.chunks):
            if number == self.fail_at:
                raise httpx.ReadError("connection lost")
            await asyncio.sleep(0)
            yield chunk


def _delta(content: str) -> bytes:
    choice = {"index": 0, "delta": {"content": content}, "finish_reason": None}
    chunk = {"id": "c", "object": "chat.completion.chunk", "model": "m", "choices": [choice]}
    return f"data: {json.dumps(chunk)}\n\n".encode()


async def test_a_stream_is_counted_when_it_ends(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    stream = _Chunks([_delta("Dear [PERSON_1] [PERSON_8]"), b"data: [DONE]\n\n"])
    upstream.post(CHAT).mock(return_value=httpx.Response(200, headers=SSE, stream=stream))
    response = await client.post(CHAT, json=chat_request(TEXT, stream=True))
    assert response.headers[ENTITIES_HEADER] == "3"
    samples = await _metrics(client)
    requests = "aliasgate_requests_total"
    assert _value(samples, requests, route=ROUTE, status_class="2xx", outcome="ok") == 1
    assert _value(samples, "aliasgate_unresolved_placeholders_total", route=ROUTE) == 1
    overhead = "aliasgate_request_overhead_seconds_count"
    assert _value(samples, overhead, route=ROUTE, phase="restore") == 1


async def test_an_upstream_read_error_mid_stream_is_an_upstream_error(
    client: httpx.AsyncClient, upstream: respx.MockRouter
) -> None:
    stream = _Chunks([_delta("Dear "), _delta("[PERSON_1]")], fail_at=1)
    upstream.post(CHAT).mock(return_value=httpx.Response(200, headers=SSE, stream=stream))
    await client.post(CHAT, json=chat_request(TEXT, stream=True))
    samples = await _metrics(client)
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="2xx",
        outcome="upstream_error",
    ) == 1  # fmt: skip


class _BrokenTransformer:
    unresolved = 0

    def feed(self, event: SSEEvent) -> list[SSEEvent]:
        raise RuntimeError("Juan Pérez")

    def end(self) -> list[SSEEvent]:
        return []


async def test_a_gateway_failure_mid_stream_is_a_gateway_error(
    client: httpx.AsyncClient, upstream: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: OpenAIChatDialect, mapping: Mapping) -> StreamTransformer:
        return _BrokenTransformer()

    monkeypatch.setattr(OpenAIChatDialect, "stream_transformer", broken)
    stream = _Chunks([_delta("Dear "), b"data: [DONE]\n\n"])
    upstream.post(CHAT).mock(return_value=httpx.Response(200, headers=SSE, stream=stream))
    await client.post(CHAT, json=chat_request(TEXT, stream=True))
    samples = await _metrics(client)
    assert _value(
        samples, "aliasgate_requests_total", route=ROUTE, status_class="2xx",
        outcome="gateway_error",
    ) == 1  # fmt: skip


async def test_the_metrics_endpoint_counts_its_own_requests(client: httpx.AsyncClient) -> None:
    await _metrics(client)
    samples = await _metrics(client)
    requests = "aliasgate_requests_total"
    assert _value(samples, requests, route="GET /metrics", status_class="2xx", outcome="ok") == 1
