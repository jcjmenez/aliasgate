"""Checks that the gateway's log records hold no synthetic value (architecture §7.1)."""

import logging

import pytest

from aliasgate.proxy.app import JsonFormatter

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
LOGGERS = (None, "aliasgate", "httpx", "httpcore", "uvicorn", "uvicorn.error", "starlette")


def capture_everything(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture every record of every logger at DEBUG."""
    for name in LOGGERS:
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


def gateway_records(capture: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
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


def assert_clean(records: list[logging.LogRecord]) -> None:
    """No record, in any rendering, contains one of VALUES."""
    assert records, "nothing was logged"
    formatter = JsonFormatter()
    for record in records:
        rendered = (repr(vars(record)), record.getMessage(), formatter.format(record))
        for text in rendered:
            for value in VALUES:
                assert value not in text, f"{record.name} logged a synthetic value"
