"""Which headers cross the gateway in each direction (architecture §7)."""

from collections.abc import Iterable
from typing import Final

_HOP_BY_HOP: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# httpx sets host and content-length itself and negotiates its own encoding.
_REQUEST_DROPPED: Final = _HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
# httpx has already decoded the body, and the gateway re-serialises it.
_RESPONSE_DROPPED: Final = _HOP_BY_HOP | {"content-length", "content-encoding"}


def _filter(headers: Iterable[tuple[str, str]], dropped: frozenset[str]) -> list[tuple[str, str]]:
    pairs = list(headers)
    named = {
        token.strip().lower()
        for name, value in pairs
        if name.lower() == "connection"
        for token in value.split(",")
    }
    return [
        (name, value)
        for name, value in pairs
        if name.lower() not in dropped and name.lower() not in named
    ]


def filter_request_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Client headers to forward upstream: hop-by-hop headers, any header named in `connection`,
    `host`, `content-length` and `accept-encoding` removed, case-insensitively; the rest kept in
    order, credentials included."""
    return _filter(headers, _REQUEST_DROPPED)


def filter_response_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Upstream headers to return: hop-by-hop headers, any header named in `connection`,
    `content-length` and `content-encoding` removed, case-insensitively; the rest kept in
    order."""
    return _filter(headers, _RESPONSE_DROPPED)
