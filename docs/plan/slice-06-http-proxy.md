# Slice 06: HTTP proxy (non-streaming)

## Goal

A running gateway. A client pointed at `http://127.0.0.1:8080/v1` instead of the OpenAI API gets
correct, restored answers for non-streaming chat completions, while the upstream only receives
placeholders. This is the first end-to-end product.

## Depends on

Slices 04 and 05.

## Dependencies to add

```bash
uv add starlette httpx uvicorn
uv add --dev respx pytest-asyncio
```

Set `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`.

## Files

| File | Action |
|---|---|
| `src/aliasgate/proxy/__init__.py` | create |
| `src/aliasgate/proxy/app.py` | create |
| `src/aliasgate/proxy/headers.py` | create |
| `src/aliasgate/proxy/errors.py` | create |
| `src/aliasgate/__main__.py` | create |
| `tests/proxy/conftest.py`, `tests/proxy/test_app.py`, `tests/proxy/test_headers.py`, `tests/proxy/test_logging_hygiene.py` | create |

## Specification

Read architecture §7 first.

### `app.py`

```python
def create_app(settings: Settings, *, pseudonymizer: Pseudonymizer | None = None,
               http_client: httpx.AsyncClient | None = None) -> Starlette:
    """Build the ASGI app. The optional arguments exist for tests."""
```

- Use Starlette's lifespan to create the `httpx.AsyncClient` (timeout from settings,
  `follow_redirects=False`) and close it on shutdown, unless one was injected.
- Build the pseudonymizer once at start-up with `build_pseudonymizer(settings)`.

Routes:

| Route | Behaviour |
|---|---|
| `GET /healthz` | `200 {"status": "ok"}`. No upstream call. |
| `POST /v1/chat/completions` | the flow below, dialect `openai_chat`, upstream `settings.upstreams.openai.base_url` |
| a `METHOD path` listed in `passthrough_paths` | forwarded unchanged to the OpenAI upstream, response returned unchanged |
| anything else | `404` in the OpenAI error shape, code `unknown_endpoint` |

Request flow for a dialect route. Each numbered failure returns the listed status with
`dialect.error_body(...)` and **does not call the upstream**.

1. Read the body. Larger than `max_request_bytes` → `413`, code `request_too_large`. Enforce the
   limit while reading the stream, not after buffering everything.
2. Parse JSON. Failure or a non-object body → `400`, code `invalid_json`.
3. If `body.get("stream")` is truthy → `501`, code `streaming_not_supported` (until slice 07).
4. `dialect.prepare_request(body, policy)`:
   `UnsupportedContentError` → `422`, code `unsupported_content`, message names the kind only;
   `MalformedRequestError` → `400`, code `malformed_request`.
5. Run `pseudonymizer.pseudonymize(prepared.texts())` in a worker thread
   (`anyio.to_thread.run_sync`). `DetectionError` or any other exception → `500`, code
   `pseudonymization_failed`.
6. `prepared.apply(batch.texts)`.
7. Forward: `POST {base_url}{path}` with the query string, filtered headers and the re-serialised
   body (`json.dumps(body, ensure_ascii=False).encode()`). `Settings` stores `base_url` without
   a trailing slash (slice 04), so plain concatenation is correct.
   (Found in slice 05: `json.loads` turns an escaped lone surrogate such as `"\ud800"` into a
   `str` that `.encode()` rejects with `UnicodeEncodeError`. Decide how such a body fails, for
   example `400 invalid_json` at step 2, and test it; it must not reach the upstream.)
   `httpx.TimeoutException` → `504`, code `upstream_timeout`. Other `httpx.HTTPError` → `502`,
   code `upstream_unreachable`.
8. Upstream response:
   - JSON body and status < 400 → `dialect.restore_response(body, mapping)`. (Found in slice
     05: for OpenAI it restores only the documented model-text fields and does nothing to a
     body without `choices`.)
   - JSON body and status ≥ 400 → `restore_all_strings(body, mapping)`.
   - non-JSON body → returned unchanged.
   - an exception while restoring → `502`, code `restoration_failed`. Never return the
     unrestored body in this case.
9. Respond with the upstream status, filtered response headers, the new body, and the header
   `x-aliasgate-request-id`.

A top-level exception handler turns anything unexpected into `500`, code `internal_error`, with
a fixed message. Exception text is never sent to the client, because it could contain data.

### `headers.py`

```python
def filter_request_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]: ...
def filter_response_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]: ...
```

- Request: drop `host`, `content-length`, `accept-encoding`, `connection`, `keep-alive`,
  `proxy-authenticate`, `proxy-authorization`, `te`, `trailer`, `transfer-encoding`, `upgrade`,
  and any header named in the `connection` header's value. Keep everything else, including
  `authorization`, `x-api-key`, `openai-*`, `anthropic-*`.
- Response: drop the hop-by-hop set above plus `content-length` and `content-encoding` (httpx
  has already decoded the body and the gateway re-serialises it).
- Comparison is case-insensitive. Order of kept headers is preserved.

### `errors.py`

A small `GatewayError(status, code, message)` exception and the mapping to responses. Messages
are fixed strings chosen by the gateway.

### Logging

- One structured line per request at INFO, via the standard `logging` module with a JSON
  formatter you write (no new dependency): `request_id`, `route`, `status`, `duration_ms`,
  `slots`, `entities` (dict of entity type → count), `unresolved_placeholders`.
- `request_id` is a `uuid4().hex` generated per request.
- Nothing else about the request is logged at any level. Exceptions are logged with
  `logger.error("...", exc_info=False)` plus the exception **class name** only; a traceback can
  contain local variables' representations in some setups and is not worth the risk.
- This is not hypothetical (found in slice 03): `DetectionError` is raised `from` the
  detector's own exception, and that cause's message may contain request text, as
  `tests/fakes.py::FailingDetector` does on purpose. `str(DetectionError)` and its
  `detector_name` are safe to log; `__cause__`, `__context__` and tracebacks are not. Extend
  the logging hygiene test to the `FailingDetector` path.

### `__main__.py`

`python -m aliasgate --config path.yaml` loads settings and runs `uvicorn.run(create_app(settings),
host=..., port=..., log_config=None)`. The full CLI comes in slice 12; keep this to argument
parsing with `argparse` and one function.

### Decisions made while implementing (slice 06)

- The lone-surrogate case fails at step 7, before anything is sent. If `json.dumps(...).encode()`
  raises `UnicodeEncodeError`, the answer is `400 invalid_json`.
- `malformed_request` messages include the position given by `MalformedRequestError` (for
  example `messages[0].content`). That message is built from field names and indexes only.
- Routing: one catch-all Starlette route, then exact `(method, path)` matching in this order:
  `GET /healthz`, dialect routes, passthrough entries. A passthrough entry can never shadow a
  dialect route. A method Starlette does not route (405) gets the same 404 `unknown_endpoint`.
- Every response carries `x-aliasgate-request-id`, error responses included.
- The forwarded body gets `content-type: application/json` when the client sent none.
  Redirects are never followed, even with an injected client.
- An upstream body counts as JSON when its media type is `application/json` or ends with `+json`.
  If such a body does not parse, it is returned unchanged. A JSON list or scalar is restored
  with `restore_all_strings`. `unresolved_placeholders` counts tolerant matches that are not in
  the mapping, over every string of the upstream JSON body.
- Error log lines: `request failed` with `request_id`, `stage` (`pseudonymize`, `forward`,
  `restore`, `internal`), `error` (class name) and, for `DetectionError`, `detector`.
- `python -m aliasgate` runs uvicorn with `access_log=False`. `configure_logging` keeps
  `httpx` and `httpcore` at WARNING or above (architecture §7.1). `--config` is optional.
- pytest: `pythonpath = ["tests"]` so that `tests/proxy` can import `fakes`, and
  `asyncio_default_fixture_loop_scope = "function"`, because pytest-asyncio 1.5 warns when it
  is unset.
- Starlette 1.10 deprecates `starlette.testclient` with httpx. The tests call the app through
  `httpx.ASGITransport` and run the lifespan with `app.router.lifespan_context(app)`.

## Tests

Use `httpx.ASGITransport` to call the app in process and `respx` to mock the upstream. No real
network. `conftest.py` provides an app fixture built with a dictionary containing `Juan Pérez`
(PERSON) and `ACME Corporation` (ORG) and default rules.

`test_app.py`
- health endpoint returns 200 without upstream calls.
- happy path: request mentioning `Juan Pérez`, `juan.perez@example.com` and `ACME Corporation`;
  the body captured by respx contains `[PERSON_1]`, `[EMAIL_1]`, `[ORG_1]` and none of the
  values; the upstream mock answers `"Dear [PERSON_1] of [ORG_1]"`; the client receives
  `"Dear Juan Pérez of ACME Corporation"`.
- tool-call round trip: upstream returns a tool call with arguments
  `{"email":"[EMAIL_1]"}`; client receives the real address in valid JSON.
- `authorization` header reaches the upstream unchanged; `host` does not equal the client's.
- query string is forwarded.
- `user` field is absent from the upstream body.
- `stream: true` → 501 and zero upstream calls.
- image content → 422, zero upstream calls; with `unsupported_content: pass` the request is
  forwarded with the image part unchanged.
- invalid JSON → 400; array body → 400; missing `messages` → 400. Zero upstream calls each.
- body over the limit → 413, zero upstream calls.
- a `FailingDetector` injected → 500 `pseudonymization_failed`, zero upstream calls, and the
  response body does not contain any request text.
- upstream 429 with a JSON error that echoes `[PERSON_1]` → client gets 429 and the restored
  name; `retry-after` header is preserved.
- upstream timeout → 504; connection error → 502.
- upstream returns non-JSON 200 → passed through unchanged.
- unknown path → 404 without upstream call; a path in `passthrough_paths` is forwarded.
- unresolved placeholder `[PERSON_7]` in the answer is returned as is.
- response carries `x-aliasgate-request-id`.
- two concurrent requests with different names do not share mappings (run with
  `asyncio.gather`; each response contains only its own name).

`test_headers.py`
- each dropped header is dropped in any letter case; a header listed in `connection` is dropped;
  others are kept in order.

`test_logging_hygiene.py`
- with logging at DEBUG and `caplog` capturing all loggers (including `httpx` and `uvicorn`),
  run the happy path and the failing-detector path; assert that none of the synthetic values
  (`Juan Pérez`, the email, `ACME Corporation`) appears in any captured record's message or
  arguments, and that the INFO line has the expected keys.

## Acceptance checklist

- [ ] Every failure before step 7 results in zero upstream calls (asserted with respx call
      counts).
- [ ] No response produced by the gateway itself contains request text.
- [ ] Logging hygiene test passes with DEBUG enabled for all loggers.
- [ ] `python -m aliasgate --config examples/aliasgate.example.yaml` starts and `/healthz`
      answers (verify manually and say so in the pull request).
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Streaming (slice 07), Anthropic (slice 08), metrics and leak audit (slice 10).
- Authentication of clients to the gateway, TLS termination, rate limiting. Run it behind your
  existing ingress. Document this in slice 12.
- Retries. The client retries; a gateway that retries can duplicate side effects.
- Storing provider API keys in the gateway.
