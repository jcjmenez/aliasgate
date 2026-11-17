# Slice 07: streaming restoration

## Goal

Streamed chat completions work through the gateway. Placeholders split across chunks are
restored correctly, in message text and in tool-call arguments, and the user sees text appear
progressively.

## Depends on

Slice 06.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/stream.py` | create |
| `src/aliasgate/dialects/sse.py` | create |
| `src/aliasgate/dialects/openai_chat.py` | change: implement `stream_transformer` |
| `src/aliasgate/proxy/app.py` | change: streaming branch, remove the 501 |
| `tests/test_stream.py`, `tests/dialects/test_sse.py`, `tests/dialects/test_openai_chat_stream.py`, `tests/proxy/test_app_stream.py` | create |

## Before you start

Check the current OpenAI reference for the streaming chunk object of Chat Completions
(`chat.completion.chunk`) and record the date in the pull request.

## Specification

### `stream.py`

Implement `StreamRestorer` exactly as in architecture §5.4. The algorithm there is verified; do
not redesign it. Reference implementation of the hold computation:

```python
def _hold_position(buffer: str) -> int:
    i = buffer.rfind("[")
    if i != -1 and "]" not in buffer[i:] and len(buffer) - i <= MAX_TOLERANT_LENGTH:
        return i - 1 if i > 0 and buffer[i - 1] == "\\" else i
    if buffer.endswith("\\"):
        return len(buffer) - 1
    return len(buffer)
```

Add `StreamRestorer.unresolved` (int): the running count of tolerant matches left unrestored in
emitted text.

### `sse.py`

```python
@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str | None      # value of the "event:" field, if any
    data: str              # "data:" lines joined with "\n"
    raw_fields: tuple[tuple[str, str], ...] = ()   # other fields (id, retry), kept in order

    def encode(self) -> bytes: ...   # "event: x\ndata: ...\n\n", one "data:" line per line of data

class SSEParser:
    def feed(self, chunk: bytes) -> list[SSEEvent]: ...
    def end(self) -> list[SSEEvent]:
        """Return a final event if the stream ended without a blank line; else []."""
```

- Decode with `codecs.getincrementaldecoder("utf-8")(errors="strict")` so a multi-byte character
  split across network chunks is handled.
- Accept `\n`, `\r\n` and `\r` line endings. An event ends at an empty line.
- Lines starting with `:` are comments: drop them (providers send them as keep-alives; dropping
  is acceptable, the connection stays active through real events).
- A field line is `name: value` with one optional space after the colon removed.
- Events with no `data` field and no `event` field are not emitted.

### `openai_chat.py`: `OpenAIChatStreamTransformer`

```python
class StreamTransformer(Protocol):           # already declared in base.py
    def feed(self, event: SSEEvent) -> list[SSEEvent]: ...
    def end(self) -> list[SSEEvent]: ...
    unresolved: int
```

(Found in slice 05: `SSEEvent` did not exist yet, so `base.py` declares `feed` and `end` with
`Any` event types. Narrow them to `SSEEvent` here; the change only tightens types.)

State: a dict of `StreamRestorer` keyed by `(choice_index, channel)`, where channel is
`"content"`, `"refusal"`, `("tool", tool_index)` or `"function_call"`. Tool and function-call
restorers use `json_escape=True`. Also keep the envelope (`id`, `object`, `created`, `model`,
`system_fingerprint` if present) of the last chunk seen.

`feed(event)`:

1. `event.data == "[DONE]"` → return `end_events + [event]` where `end_events` are synthetic
   chunks for any restorer that still holds text (see below). Normally there are none.
2. Parse `event.data` as JSON. If it fails, or the object has no `choices` list, return
   `[event]` unchanged.
3. For each choice, with `delta = choice.get("delta") or {}`:
   - `delta["content"]` is a string → replace with `restorer.feed(value)`;
   - `delta["refusal"]` is a string → same on the refusal channel;
   - each item of `delta["tool_calls"]` with `function.arguments` a string → feed the restorer
     for `("tool", item["index"])` and replace; `custom.input` likewise without JSON escaping;
   - `delta["function_call"]["arguments"]` → same on its channel.
4. If the choice has a non-null `finish_reason`: flush every restorer of that choice index. For
   each non-empty flush, build a synthetic chunk **before** the current one: the saved envelope
   plus `"choices": [{"index": i, "delta": <delta carrying the flushed text in the same field
   it belongs to>, "finish_reason": null}]`. For a tool channel the delta is
   `{"tool_calls": [{"index": tool_index, "function": {"arguments": flushed}}]}`.
5. Re-serialise the (modified) chunk with `json.dumps(obj, ensure_ascii=False,
   separators=(",", ":"))` into a new `SSEEvent` with the same `event` field and return the
   synthetic events followed by it.

A delta whose text becomes `""` because everything is held back is still emitted with `""`. It
keeps the implementation simple and clients handle empty deltas.

`end()` flushes all restorers and returns synthetic chunks for the non-empty ones. It is called
when the upstream closes without `[DONE]`.

### Proxy changes

In the request flow of slice 06, replace step 3. When `body.get("stream")` is truthy:

- Send the upstream request with `client.send(request, stream=True)`.
- If the upstream status is ≥ 400 or the content type is not `text/event-stream`: read the whole
  body and handle it as in the non-streaming flow (step 8).
- Otherwise return a `StreamingResponse` (media type `text/event-stream`, upstream status,
  filtered headers plus `cache-control: no-cache` and `x-aliasgate-request-id`) whose generator:
  1. iterates `response.aiter_bytes()`, feeds `SSEParser`, feeds each event to the transformer,
     yields `event.encode()` for each output event;
  2. at the end, feeds `parser.end()` events, then yields `transformer.end()` events;
  3. always closes the upstream response in a `finally` block;
  4. on any exception mid-stream: log the class name, stop the generator. Do **not** emit the
     held-back buffer raw. The client sees a truncated stream, which is the correct signal.
- If the client disconnects, Starlette cancels the generator; the `finally` block must close the
  upstream so the provider stops generating.
- The log line for the request is written when the stream ends, with the transformer's
  `unresolved` count. (Found in slice 06: `_Gateway.dispatch` emits the line when the handler
  returns. A streamed response must skip that and emit from the generator's `finally`.)

### Decisions made while implementing (slice 07)

- The API check (openai-python 3.52.0 types, read 2026-11-17) found `delta.audio.transcript`.
  Slice 05 restores `audio.transcript` in whole responses, so streams restore it too, on an
  `"audio"` channel. Streamed deltas have no `custom` tool calls today. `custom.input` is still
  handled, on its own `("custom", tool_index)` channel, because its escaping differs from
  `("tool", tool_index)`.
- A chunk without `choices` but with an `error` key (how providers report errors mid-stream)
  goes through `restore_all_strings`, as error bodies do in slice 06. Every other chunk that
  changes nothing is returned as the original event, so its bytes are kept.
- `_RelayResponse(StreamingResponse)` in `proxy/app.py` owns the cleanup. In a `finally`, inside
  `anyio.CancelScope(shield=True)`, it closes the body generator and the upstream, then logs
  the request. The shield matters when an enclosing anyio cancel scope is cancelled: without
  it, the awaits in the cleanup are cancelled too (a test fails without it). Starlette raises
  `ClientDisconnect` for ASGI 2.4 servers, and it is swallowed. Any other send failure is
  logged by class name. uvicorn announces ASGI 2.3, so a disconnect there cancels the generator.
- The stream response replaces the upstream `content-type` and `cache-control` with
  `text/event-stream` and `no-cache`, and keeps the other filtered headers.
- If creating the transformer fails, the upstream is closed and the client gets
  `500 internal_error`. The `streaming_not_supported` code is gone.
- The SSE parser also strips a leading byte order mark and treats a line without a colon as a
  field with an empty value, as the HTML standard says.
- Tests cover a client disconnect by calling the ASGI app directly with `spec_version` 2.3 and
  2.4, and an enclosing cancel scope with an anyio task group.

## Tests

`test_stream.py` (mapping: `[PERSON_1]` → `Juan "JP" Pérez`, `[ORG_1]` → `ACME`)
- a placeholder in one chunk is restored.
- split at every possible position of `"Hi [PERSON_1], welcome to [ORG_1]."`: for each cut
  index, two chunks give the same result as one.
- one character per chunk gives the same result.
- text with `[` that never closes is emitted after `flush`, unchanged.
- an unclosed `[` followed by more than `MAX_TOLERANT_LENGTH` characters is released without
  waiting for `flush`.
- array-like text `"[1, 2, 3]"` passes through unchanged.
- escaped form `\[PERSON_1\]` split between `\` and `[` is restored.
- `json_escape=True` escapes the quote characters in the surface.
- `unresolved` counts `[PERSON_9]` once.
- **property**: for text drawn from an alphabet of placeholders (resolvable, unresolvable,
  tolerant variants), `[`, `]`, `\`, `_`, digits, letters, spaces and newlines, and for any
  list of cut points, `"".join(feed(chunk) for chunk in chunks) + flush() == restore(text)`.
  Run at least 1,000 examples.
- property: nothing emitted by `feed` is ever retracted, i.e. the concatenation so far is always
  a prefix of `restore(text)`.

`test_sse.py`
- two events in one chunk; one event split across three chunks; `\r\n` endings.
- a multi-byte character (`é`, an emoji) split across chunks.
- multi-line `data` is joined with `\n` and re-encoded as several `data:` lines.
- comment lines are dropped; `event:` field is kept.
- `encode` followed by `feed` returns an equal event (property over generated events).

`test_openai_chat_stream.py` (fixtures: recorded-shape synthetic streams as lists of JSON)
- content split as `"Dear [PERS"`, `"ON_1]"`, then a finish chunk, then `[DONE]`: the
  concatenated content deltas equal `"Dear Juan …"` and the finish chunk comes last before
  `[DONE]`.
- placeholder still held when the finish chunk arrives: a synthetic chunk with the flushed text
  precedes the finish chunk, and has `finish_reason: null`.
- tool-call arguments streamed in fragments `{"em`, `ail":"[EMA`, `IL_1]"}`: concatenated
  arguments parse as JSON with the real address.
- two choices (`n=2`) interleaved keep independent buffers.
- two parallel tool calls (indexes 0 and 1) keep independent buffers.
- a usage-only final chunk (`choices: []`) passes through unchanged.
- a non-JSON data line passes through unchanged.
- `end()` without `[DONE]` flushes held text.
- property: for a random text and random chunking into content deltas, the concatenation of all
  output content deltas equals `restore(text)`.

`test_app_stream.py`
- end-to-end with respx returning an SSE byte stream cut at awkward byte positions (inside a
  JSON chunk and inside a multi-byte character): the client reconstructs the restored text.
- the upstream request body has `stream: true` and placeholders only.
- upstream 401 with a JSON body on a streaming request → client gets a normal JSON 401.
- an exception raised mid-stream by a faulty transformer (inject one) ends the response without
  emitting buffered text, and the upstream response is closed.
- response content type is `text/event-stream`.

## Acceptance checklist

- [ ] Both `StreamRestorer` properties pass with ≥ 1,000 examples.
- [ ] No code path emits un-restored held-back text on error.
- [ ] Upstream response is closed on completion, error and client disconnect.
- [ ] Manual check against a real provider with a streaming client, using synthetic data only;
      say in the pull request which client and model were used.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Anthropic streaming (slice 08).
- Restoring log probabilities in streamed chunks.
- Backpressure tuning and performance work. Correctness first.
