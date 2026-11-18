"""The gateway's ASGI application: routing, the dialect request flow and logging (architecture §7).

Fail closed: every failure before forwarding produces an error response and no upstream call.
Logs carry ids, counts, entity types, durations and status codes, never text.
"""

import json
import logging
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import anyio
import anyio.to_thread
import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from aliasgate.config import PolicySettings, Settings
from aliasgate.dialects.anthropic_messages import AnthropicMessagesDialect
from aliasgate.dialects.base import (
    Dialect,
    MalformedRequestError,
    Policy,
    StreamTransformer,
    UnsupportedContentError,
    restore_all_strings,
    slots_for_json_strings,
)
from aliasgate.dialects.openai_chat import OpenAIChatDialect
from aliasgate.dialects.sse import SSEParser
from aliasgate.engine import DetectionError, Pseudonymizer, count_unresolved
from aliasgate.factory import build_pseudonymizer
from aliasgate.mapping import Mapping
from aliasgate.proxy import errors
from aliasgate.proxy.errors import GatewayError
from aliasgate.proxy.headers import filter_request_headers, filter_response_headers

REQUEST_ID_HEADER: Final = "x-aliasgate-request-id"

_logger: Final = logging.getLogger("aliasgate.proxy")
# Every method reaches the gateway's own dispatch; Starlette answers any other with 405, which
# is mapped to the same 404 as an unknown path.
_METHODS: Final = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_HEALTH: Final = ("GET", "/healthz")
_CHAT_COMPLETIONS: Final = "/v1/chat/completions"
_MESSAGES: Final = "/v1/messages"
_COUNT_TOKENS: Final = "/v1/messages/count_tokens"
# A request carrying one of these headers comes from an Anthropic client: errors raised before
# a route is chosen (an unknown endpoint) use the Anthropic error shape.
_ANTHROPIC_HEADERS: Final = ("anthropic-version", "x-api-key")
_UNKNOWN_ROUTE: Final = "unknown"
# httpx logs every request URL at INFO, query string included; httpcore logs headers at DEBUG.
_QUIET_LOGGERS: Final = ("httpx", "httpcore")
_NOT_JSON: Final = object()
_EVENT_STREAM: Final = "text/event-stream"


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, then the record's `fields`.

    Tracebacks and stack information are never rendered, only an exception's class name,
    because a traceback can show request text.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                entry.setdefault(str(key), value)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """Send every log record to stderr as one JSON line, at `level`. The httpx and httpcore
    loggers are kept at WARNING or above, because they log URLs and headers."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))


@dataclass(slots=True)
class _RequestLog:
    """What the logs may say about one request: its id, route, counts and entity types."""

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    route: str = _UNKNOWN_ROUTE
    started: float = field(default_factory=time.perf_counter)
    slots: int = 0
    entities: dict[str, int] = field(default_factory=dict)
    unresolved_placeholders: int = 0
    deferred: bool = False  # a streamed response writes the line itself, when the stream ends

    def emit(self, status: int) -> None:
        fields = {
            "request_id": self.request_id,
            "route": self.route,
            "status": status,
            "duration_ms": round((time.perf_counter() - self.started) * 1000, 3),
            "slots": self.slots,
            "entities": self.entities,
            "unresolved_placeholders": self.unresolved_placeholders,
        }
        _logger.info("request", extra={"fields": fields})

    def failure(self, stage: str, exc: BaseException) -> None:
        """Log a failure by exception class name only: its message and cause may hold text."""
        fields = {"request_id": self.request_id, "stage": stage, "error": type(exc).__name__}
        if isinstance(exc, DetectionError):
            fields["detector"] = exc.detector_name
        _logger.error("request failed", extra={"fields": fields}, exc_info=False)


@dataclass(frozen=True, slots=True)
class _DialectRoute:
    """A route the gateway pseudonymises. With `request_only`, the body's `stream` is not
    honoured and a successful answer is returned unchanged (it holds no text)."""

    dialect: Dialect
    base_url: str
    path: str
    request_only: bool = False


@dataclass(frozen=True, slots=True)
class _PassthroughRoute:
    """A route forwarded unchanged to one upstream; its errors use that upstream's dialect."""

    dialect: Dialect
    base_url: str


def _policy(settings: PolicySettings) -> Policy:
    return Policy(
        unsupported_content=settings.unsupported_content,
        strip_user_identifiers=settings.strip_user_identifiers,
        tool_definitions=settings.tool_definitions,
    )


def _parse_object(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        raise errors.invalid_json() from None
    if not isinstance(body, dict):
        raise errors.invalid_json()
    return body


def _encode(body: dict[str, Any]) -> bytes:
    try:
        return json.dumps(body, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        # An escaped lone surrogate ("\ud800") parses into a str that UTF-8 cannot encode.
        raise errors.invalid_json() from None


def _loads(content: bytes) -> object:
    try:
        return json.loads(content)
    except (ValueError, RecursionError):
        return _NOT_JSON


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _is_json(content_type: str | None) -> bool:
    media_type = _media_type(content_type)
    return media_type == "application/json" or media_type.endswith("+json")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict | list):
        return [slot.get() for slot in slots_for_json_strings(value)]
    return []


def _decoded(raw: Iterable[tuple[bytes, bytes]]) -> list[tuple[str, str]]:
    return [(name.decode("latin-1"), value.decode("latin-1")) for name, value in raw]


def _encoded(headers: Iterable[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    return [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers]


def _upstream_response(upstream: httpx.Response, content: bytes) -> Response:
    response = Response(content=content, status_code=upstream.status_code)
    response.raw_headers.extend(_encoded(filter_response_headers(_decoded(upstream.headers.raw))))
    return response


class _RelayResponse(StreamingResponse):
    """Relays an upstream event stream through a stream transformer.

    However the response ends (completion, a failure, a client disconnect), the body generator
    and the upstream response are closed and the request line is written once. On a failure the
    stream stops: the client sees a truncated stream, and text the transformer holds back is
    never sent.
    """

    def __init__(
        self, upstream: httpx.Response, transformer: StreamTransformer, log: _RequestLog
    ) -> None:
        self._upstream = upstream
        self._transformer = transformer
        self._log = log
        self._events = self._relay()
        log.deferred = True
        super().__init__(self._events, status_code=upstream.status_code, media_type=_EVENT_STREAM)
        kept = [
            (name, value)
            for name, value in filter_response_headers(_decoded(upstream.headers.raw))
            if name.lower() not in ("content-type", "cache-control")
        ]
        self.raw_headers.extend(_encoded([*kept, ("cache-control", "no-cache")]))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except ClientDisconnect:
            pass  # how servers implementing ASGI 2.4 report a client that went away
        except Exception as exc:
            self._log.failure("stream", exc)
        finally:
            # Shielded: after a disconnect the task is being cancelled, and the provider must
            # still be told to stop generating.
            with anyio.CancelScope(shield=True):
                await self._events.aclose()
                await self._upstream.aclose()
            self._log.unresolved_placeholders = self._transformer.unresolved
            self._log.emit(self.status_code)

    async def _relay(self) -> AsyncGenerator[bytes, None]:
        parser = SSEParser()
        try:
            async for chunk in self._upstream.aiter_bytes():
                for event in parser.feed(chunk):
                    for output in self._transformer.feed(event):
                        yield output.encode()
            for event in parser.end():
                for output in self._transformer.feed(event):
                    yield output.encode()
            for output in self._transformer.end():
                yield output.encode()
        except Exception as exc:
            self._log.failure("stream", exc)


class _Gateway:
    """Routes requests and runs the request flow; one instance per application."""

    def __init__(
        self,
        settings: Settings,
        pseudonymizer: Pseudonymizer,
        http_client: httpx.AsyncClient | None,
    ) -> None:
        self.client = http_client
        self._settings = settings
        self._pseudonymizer = pseudonymizer
        self._policy = _policy(settings.policy)
        self._openai = OpenAIChatDialect()
        self._anthropic = AnthropicMessagesDialect()
        upstreams = settings.upstreams
        openai_url, anthropic_url = upstreams.openai.base_url, upstreams.anthropic.base_url
        self._routes: dict[tuple[str, str], _DialectRoute] = {
            ("POST", _CHAT_COMPLETIONS): _DialectRoute(self._openai, openai_url, _CHAT_COMPLETIONS),
            ("POST", _MESSAGES): _DialectRoute(self._anthropic, anthropic_url, _MESSAGES),
            ("POST", _COUNT_TOKENS): _DialectRoute(
                self._anthropic, anthropic_url, _COUNT_TOKENS, request_only=True
            ),
        }
        by_upstream = {
            "openai": _PassthroughRoute(self._openai, openai_url),
            "anthropic": _PassthroughRoute(self._anthropic, anthropic_url),
        }
        self._passthrough: dict[tuple[str, str], _PassthroughRoute] = {}
        for entry in upstreams.passthrough_paths:
            upstream, method, path = entry.split(" ", 2)
            self._passthrough[(method, path)] = by_upstream[upstream]

    async def dispatch(self, request: Request) -> Response:
        """Answer one request. Never raises; every response carries the request id."""
        log = _RequestLog()
        key = (request.method, request.scope["path"])
        anthropic_client = any(name in request.headers for name in _ANTHROPIC_HEADERS)
        dialect: Dialect = self._anthropic if anthropic_client else self._openai
        try:
            if key == _HEALTH:
                log.route = " ".join(key)
                response: Response = JSONResponse({"status": "ok"})
            elif (route := self._routes.get(key)) is not None:
                log.route = " ".join(key)
                dialect = route.dialect
                response = await self._dialect_flow(request, route, log)
            elif (passthrough := self._passthrough.get(key)) is not None:
                log.route = " ".join(key)
                dialect = passthrough.dialect
                response = await self._passthrough_flow(request, passthrough, key[0], log)
            else:
                raise errors.unknown_endpoint()
        except GatewayError as error:
            response = errors.error_response(dialect, error)
        except Exception as exc:
            log.failure("internal", exc)
            response = errors.error_response(dialect, errors.internal_error())
        response.headers[REQUEST_ID_HEADER] = log.request_id
        if not log.deferred:
            log.emit(response.status_code)
        return response

    async def not_routed(self, request: Request, exc: Exception) -> Response:
        """Starlette's 404 and 405 go through the same dispatch, which answers 404."""
        return await self.dispatch(request)

    async def _dialect_flow(
        self, request: Request, route: _DialectRoute, log: _RequestLog
    ) -> Response:
        body = _parse_object(await self._read_body(request))
        streaming = not route.request_only and bool(body.get("stream"))
        try:
            prepared = route.dialect.prepare_request(body, self._policy)
        except UnsupportedContentError as exc:
            raise errors.unsupported_content(exc.kind) from None
        except MalformedRequestError as exc:
            raise errors.malformed_request(str(exc)) from None
        texts = prepared.texts()
        log.slots = len(texts)
        try:
            batch = await anyio.to_thread.run_sync(self._pseudonymizer.pseudonymize, texts)
            prepared.apply(batch.texts)
        except Exception as exc:
            log.failure("pseudonymize", exc)
            raise errors.pseudonymization_failed() from None
        log.entities = dict(Counter(entity.entity_type for entity in batch.mapping.entities()))
        url = route.base_url + route.path
        upstream = await self._forward(
            request, "POST", url, _encode(body), log, json_content=True, stream=streaming
        )
        if not streaming:
            if route.request_only and upstream.status_code < 400:
                return _upstream_response(upstream, upstream.content)
            return self._restored(upstream, route.dialect, batch.mapping, log)
        if (
            upstream.status_code < 400
            and _media_type(upstream.headers.get("content-type")) == _EVENT_STREAM
        ):
            try:
                transformer = route.dialect.stream_transformer(batch.mapping)
                return _RelayResponse(upstream, transformer, log)
            except Exception:
                await upstream.aclose()
                raise
        # An error, or an answer that is not a stream: handled like a non-streamed answer.
        try:
            await self._read(upstream, log)
        finally:
            await upstream.aclose()
        return self._restored(upstream, route.dialect, batch.mapping, log)

    async def _passthrough_flow(
        self, request: Request, route: _PassthroughRoute, method: str, log: _RequestLog
    ) -> Response:
        raw = await self._read_body(request)
        url = route.base_url + request.scope["path"]
        upstream = await self._forward(request, method, url, raw or None, log, json_content=False)
        return _upstream_response(upstream, upstream.content)

    async def _read_body(self, request: Request) -> bytes:
        """The whole body, refusing to read past `max_request_bytes`."""
        limit = self._settings.listen.max_request_bytes
        declared = request.headers.get("content-length", "")
        if declared.isascii() and declared.isdigit() and int(declared) > limit:
            raise errors.request_too_large()
        chunks: list[bytes] = []
        size = 0
        async with aclosing(request.stream()) as stream:
            async for chunk in stream:
                size += len(chunk)
                if size > limit:
                    raise errors.request_too_large()
                chunks.append(chunk)
        return b"".join(chunks)

    async def _forward(
        self,
        request: Request,
        method: str,
        url: str,
        content: bytes | None,
        log: _RequestLog,
        *,
        json_content: bool,
        stream: bool = False,
    ) -> httpx.Response:
        if self.client is None:
            raise RuntimeError("no upstream client: the application lifespan has not started")
        headers = filter_request_headers(_decoded(request.headers.raw))
        if json_content and not any(name.lower() == "content-type" for name, _ in headers):
            headers.append(("content-type", "application/json"))
        query: bytes = request.scope.get("query_string", b"")
        target = httpx.URL(url, query=query) if query else httpx.URL(url)
        outgoing = self.client.build_request(
            method, target, content=content, headers=_encoded(headers)
        )
        try:
            return await self.client.send(outgoing, stream=stream, follow_redirects=False)
        except httpx.TimeoutException as exc:
            log.failure("forward", exc)
            raise errors.upstream_timeout() from None
        except httpx.HTTPError as exc:
            log.failure("forward", exc)
            raise errors.upstream_unreachable() from None

    async def _read(self, upstream: httpx.Response, log: _RequestLog) -> None:
        """Read a streamed upstream body to the end, mapping transport errors like `_forward`."""
        try:
            await upstream.aread()
        except httpx.TimeoutException as exc:
            log.failure("forward", exc)
            raise errors.upstream_timeout() from None
        except httpx.HTTPError as exc:
            log.failure("forward", exc)
            raise errors.upstream_unreachable() from None

    def _restored(
        self, upstream: httpx.Response, dialect: Dialect, mapping: Mapping, log: _RequestLog
    ) -> Response:
        """The upstream response with placeholders restored; non-JSON bodies are unchanged."""
        content = upstream.content
        parsed = _loads(content) if _is_json(upstream.headers.get("content-type")) else _NOT_JSON
        if parsed is not _NOT_JSON:
            try:
                log.unresolved_placeholders = sum(
                    count_unresolved(text, mapping) for text in _strings(parsed)
                )
                if upstream.status_code < 400 and isinstance(parsed, dict):
                    dialect.restore_response(parsed, mapping)
                else:
                    parsed = restore_all_strings(parsed, mapping)
                content = json.dumps(parsed, ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                log.failure("restore", exc)
                raise errors.restoration_failed() from None
        return _upstream_response(upstream, content)


def create_app(
    settings: Settings,
    *,
    pseudonymizer: Pseudonymizer | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> Starlette:
    """Build the ASGI app. The optional arguments exist for tests."""
    if pseudonymizer is None:
        pseudonymizer = build_pseudonymizer(settings)
    gateway = _Gateway(settings, pseudonymizer, http_client)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if http_client is not None:
            yield
            return
        async with httpx.AsyncClient(
            timeout=settings.upstreams.timeout_seconds, follow_redirects=False
        ) as client:
            gateway.client = client
            try:
                yield
            finally:
                gateway.client = None

    return Starlette(
        routes=[Route("/{path:path}", gateway.dispatch, methods=_METHODS)],
        exception_handlers={404: gateway.not_routed, 405: gateway.not_routed},
        lifespan=lifespan,
    )
