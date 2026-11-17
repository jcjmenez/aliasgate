from collections.abc import Callable, Iterable

import pytest

from aliasgate.proxy.headers import filter_request_headers, filter_response_headers

_HOP_BY_HOP = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
]
_REQUEST_ONLY = ["host", "content-length", "accept-encoding"]
_RESPONSE_ONLY = ["content-length", "content-encoding"]

type _Filter = Callable[[Iterable[tuple[str, str]]], list[tuple[str, str]]]


def _cases(names: list[str]) -> list[str]:
    return [variant for name in names for variant in (name, name.upper(), name.title())]


@pytest.mark.parametrize("name", _cases(_HOP_BY_HOP + _REQUEST_ONLY))
def test_request_drops_each_listed_header_in_any_letter_case(name: str) -> None:
    headers = [("x-before", "1"), (name, "value"), ("x-after", "2")]
    assert filter_request_headers(headers) == [("x-before", "1"), ("x-after", "2")]


@pytest.mark.parametrize("name", _cases(_HOP_BY_HOP + _RESPONSE_ONLY))
def test_response_drops_each_listed_header_in_any_letter_case(name: str) -> None:
    headers = [("x-before", "1"), (name, "value"), ("x-after", "2")]
    assert filter_response_headers(headers) == [("x-before", "1"), ("x-after", "2")]


def test_request_keeps_credentials_and_provider_headers_in_order() -> None:
    token = "Bearer " + "t" * 24
    headers = [
        ("authorization", token),
        ("content-type", "application/json"),
        ("x-api-key", "k" * 16),
        ("OpenAI-Organization", "org-example"),
        ("anthropic-version", "2023-06-01"),
        ("user-agent", "example-client/1.0"),
        ("x-custom", "a"),
        ("x-custom", "b"),
    ]
    assert filter_request_headers(headers) == headers


def test_response_keeps_other_headers_including_repeated_ones_in_order() -> None:
    headers = [
        ("content-type", "application/json"),
        ("retry-after", "7"),
        ("set-cookie", "a=1"),
        ("set-cookie", "b=2"),
        ("x-request-id", "req_example"),
    ]
    assert filter_response_headers(headers) == headers


def test_response_keeps_host_and_accept_encoding_which_are_request_only() -> None:
    headers = [("host", "example.com"), ("accept-encoding", "gzip")]
    assert filter_response_headers(headers) == headers


@pytest.mark.parametrize("filter_headers", [filter_request_headers, filter_response_headers])
def test_headers_named_in_connection_are_dropped(filter_headers: _Filter) -> None:
    headers = [
        ("Connection", "keep-alive, X-Hop , x-other"),
        ("x-hop", "1"),
        ("X-Other", "2"),
        ("x-kept", "3"),
    ]
    assert filter_headers(headers) == [("x-kept", "3")]


def test_empty_tokens_in_connection_are_ignored() -> None:
    headers = [("connection", " , ,"), ("x-kept", "1")]
    assert filter_request_headers(headers) == [("x-kept", "1")]


def test_filters_accept_any_iterable() -> None:
    assert filter_request_headers(iter([("x-a", "1"), ("host", "h")])) == [("x-a", "1")]
