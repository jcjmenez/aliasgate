"""No request or response text, surface or mapping reaches a log record (architecture §7.1)."""

import json
import logging
import sys
from collections.abc import Iterator

import httpx
import pytest
import respx
from payloads import CHAT, ClientFactory, chat_request, chat_response

from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.engine import Pseudonymizer
from aliasgate.proxy.app import JsonFormatter, configure_logging
from fakes import FailingDetector

VALUES = (
    "Juan Pérez",
    "Pérez",
    "P\\u00e9rez",
    "juan.perez@example.com",
    "juan.perez",
    "ACME Corporation",
    "ACME",
)
GATEWAY = "http://gateway.test"
TEXT = "Write to Juan Pérez (juan.perez@example.com) at ACME Corporation."
LOGGERS = (None, "aliasgate", "httpx", "httpcore", "uvicorn", "uvicorn.error", "starlette")
INFO_KEYS = {
    "request_id",
    "route",
    "status",
    "duration_ms",
    "slots",
    "entities",
    "unresolved_placeholders",
}


@pytest.fixture
def capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture every record of every logger at DEBUG."""
    for name in LOGGERS:
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


def _gateway_records(capture: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Every record except the test client's own httpx request lines, which name the URL the
    test called and are not produced by the gateway."""
    return [
        record
        for record in capture.records
        if not (
            record.name == "httpx"
            and isinstance(record.args, tuple)
            and str(record.args[1]).startswith(GATEWAY)
        )
    ]


def _assert_clean(records: list[logging.LogRecord]) -> None:
    assert records, "nothing was logged"
    formatter = JsonFormatter()
    for record in records:
        rendered = (repr(vars(record)), record.getMessage(), formatter.format(record))
        for text in rendered:
            for value in VALUES:
                assert value not in text, f"{record.name} logged a synthetic value"


def _request_lines(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    lines = [
        record.fields  # type: ignore[attr-defined]
        for record in records
        if record.name == "aliasgate.proxy" and record.levelno == logging.INFO
    ]
    assert all(isinstance(line, dict) for line in lines)
    return lines


async def test_happy_path_logs_one_info_line_with_counts_only(
    client: httpx.AsyncClient, upstream: respx.MockRouter, capture: pytest.LogCaptureFixture
) -> None:
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1] of [ORG_1], [PERSON_7]"))
    response = await client.post(CHAT, json=chat_request(TEXT))
    assert response.status_code == 200

    records = _gateway_records(capture)
    _assert_clean(records)
    (line,) = _request_lines(records)
    assert set(line) == INFO_KEYS
    assert line["request_id"] == response.headers["x-aliasgate-request-id"]
    assert line["route"] == "POST /v1/chat/completions"
    assert line["status"] == 200
    assert line["slots"] == 1
    assert line["entities"] == {"PERSON": 1, "EMAIL": 1, "ORG": 1}
    assert line["unresolved_placeholders"] == 1
    duration = line["duration_ms"]
    assert isinstance(duration, float)
    assert duration >= 0


async def test_failing_detector_path_logs_the_class_and_detector_name_only(
    make_client: ClientFactory, upstream: respx.MockRouter, capture: pytest.LogCaptureFixture
) -> None:
    upstream.post(CHAT).respond(json=chat_response("ok"))
    async with make_client(pseudonymizer=Pseudonymizer([FailingDetector()])) as client:
        response = await client.post(CHAT, json=chat_request(TEXT))
    assert response.status_code == 500

    records = _gateway_records(capture)
    _assert_clean(records)
    for record in records:
        assert not record.exc_info
        assert record.stack_info is None
        assert "cannot process" not in repr(vars(record))
    errors = [record for record in records if record.levelno == logging.ERROR]
    assert [record.fields for record in errors] == [  # type: ignore[attr-defined]
        {
            "request_id": response.headers["x-aliasgate-request-id"],
            "stage": "pseudonymize",
            "error": "DetectionError",
            "detector": "failing",
        }
    ]
    (line,) = _request_lines(records)
    assert line["status"] == 500
    assert line["entities"] == {}


async def test_rejected_and_failed_requests_log_no_text(
    client: httpx.AsyncClient,
    upstream: respx.MockRouter,
    capture: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = {"type": "image_url", "image_url": {"url": "https://example.com/juan.perez.png"}}
    upstream.post(CHAT).respond(
        429, json={"error": {"message": "Too many requests for [PERSON_1] of [ORG_1]"}}
    )
    await client.post(CHAT, json=chat_request(TEXT))
    await client.post(CHAT, json={"messages": [{"role": "user", "content": [image]}]})
    await client.post(CHAT, json={"messages": [{"role": "user", "content": {"t": TEXT}}]})
    await client.post(CHAT, content=b'{"messages": "Juan P\xc3\xa9rez" ')
    await client.post("/v1/Juan Pérez/juan.perez@example.com", json=chat_request(TEXT))

    def broken_restore(self: object, body: object, mapping: object) -> None:
        raise RuntimeError(TEXT)

    monkeypatch.setattr(OpenAIChatDialect, "restore_response", broken_restore)
    upstream.post(CHAT).respond(json=chat_response("Dear [PERSON_1]"))
    restored_failure = await client.post(CHAT, json=chat_request(TEXT))
    assert restored_failure.status_code == 502

    records = _gateway_records(capture)
    _assert_clean(records)
    statuses = [line["status"] for line in _request_lines(records)]
    assert statuses == [429, 422, 400, 400, 404, 502]
    assert _request_lines(records)[4]["route"] == "unknown"


def test_the_formatter_renders_fields_and_never_a_traceback() -> None:
    try:
        raise RuntimeError(TEXT)
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "aliasgate.proxy", logging.ERROR, __file__, 1, "failed", (), exc_info
    )
    record.fields = {"request_id": "abc", "error": "RuntimeError"}
    record.stack_info = "Stack (most recent call last): " + TEXT
    line = json.loads(JsonFormatter().format(record))
    assert line["level"] == "ERROR"
    assert line["logger"] == "aliasgate.proxy"
    assert line["message"] == "failed"
    assert line["request_id"] == "abc"
    assert line["exception"] == "RuntimeError"
    assert "time" in line
    assert TEXT not in json.dumps(line, ensure_ascii=False)


def test_the_formatter_keeps_its_own_keys_when_fields_collide() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("there",), None)
    record.fields = {"message": "other", "level": "DEBUG", "count": 2}
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello there"
    assert line["level"] == "INFO"
    assert line["count"] == 2


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    quiet = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    yield
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]
    for name, level in quiet.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize("level", ["DEBUG", "INFO", "ERROR"])
def test_configure_logging_installs_json_lines_and_quiets_http_client_loggers(level: str) -> None:
    configure_logging(level)
    root = logging.getLogger()
    assert root.level == logging.getLevelName(level)
    (handler,) = root.handlers
    assert isinstance(handler.formatter, JsonFormatter)
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
        assert logging.getLogger(name).level >= root.level
