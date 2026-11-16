# Slice 05: OpenAI Chat Completions payloads (non-streaming)

## Goal

Pure functions that take an OpenAI Chat Completions request body, expose every string that must
be pseudonymised as an ordered list of slots, and restore a non-streamed response body. No HTTP.

## Depends on

Slice 03.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/dialects/__init__.py` | create |
| `src/aliasgate/dialects/base.py` | create |
| `src/aliasgate/dialects/openai_chat.py` | create |
| `tests/dialects/test_base.py`, `tests/dialects/test_openai_chat.py` | create |
| `tests/dialects/fixtures/openai_chat/*.json` | create (synthetic request and response bodies) |

## Before you start

Open the current OpenAI API reference for `POST /v1/chat/completions` and compare it with the
shapes below. Record the date you checked in the pull request. If the API has a field carrying
user-authored text that is not listed here, add it and say so. If it has a new content part
type, it falls under "unknown part type" below, which is already safe.

## Specification

Read architecture §6 first.

### `base.py`

```python
@dataclass(frozen=True, slots=True)
class Policy:
    unsupported_content: Literal["block", "pass"] = "block"
    strip_user_identifiers: bool = True
    tool_definitions: bool = True

@dataclass(slots=True)
class Slot:
    get: Callable[[], str]
    set: Callable[[str], None]

class PreparedRequest:
    def __init__(self, slots: list[Slot], finalizers: list[Callable[[], None]]) -> None: ...
    def texts(self) -> list[str]: ...
    def apply(self, new_texts: Sequence[str]) -> None:
        """Set every slot in order, then run finalizers. Raise ValueError on length mismatch."""

class UnsupportedContentError(Exception):
    """Content the gateway cannot inspect. `kind` is a short label such as "image_url"."""
    kind: str

class MalformedRequestError(Exception):
    """The body does not have the shape this dialect requires. Message has no user text."""

def slots_for_json_strings(container: dict[str, Any] | list[Any]) -> list[Slot]:
    """A slot for every string VALUE reachable in the container, depth-first, dicts in insertion
    order, lists in index order. Keys are never slots."""

def key_slot(obj: dict[str, Any], key: str) -> Slot: ...
def index_slot(arr: list[Any], index: int) -> Slot: ...
```

Also define the `Dialect` and `StreamTransformer` protocols from architecture §6 here.
`stream_transformer` is implemented in slice 07; in this slice `OpenAIChatDialect.stream_transformer`
raises `NotImplementedError`.

### `openai_chat.py`: request

`OpenAIChatDialect.prepare_request(body, policy)` mutates `body` only through slots and through
identifier stripping. Slot order:

1. For each message in `body["messages"]`, in order:
   1. `content`:
      - a string → one slot;
      - `None` or absent → nothing;
      - a list of parts → for each part by `type`:
        - `"text"` → slot on `part["text"]`;
        - `"refusal"` → slot on `part["refusal"]`;
        - `"image_url"`, `"input_audio"`, `"file"`, or any other type → if policy is `block`,
          raise `UnsupportedContentError(kind=<type>)`; if `pass`, skip.
   2. `tool_calls` (assistant messages), for each call in order:
      - `type == "function"`: `function.arguments` is a JSON string. Try `json.loads`. If it
        parses to a dict or list: add `slots_for_json_strings` over the parsed value, and a
        finalizer that writes back
        `json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))`. If it parses to a bare
        string, number, boolean or null, or fails to parse: one slot on the raw string.
      - `type == "custom"`: one slot on `custom.input`.
      - other types: `block` → `UnsupportedContentError`; `pass` → skip.
   3. Legacy `function_call.arguments`: same treatment as function arguments.
2. `body["prediction"]["content"]` if present: string → one slot; list of text parts → slots.
3. If `policy.tool_definitions`: for each entry of `body["tools"]`, every string value found
   under a key named exactly `"description"` anywhere inside the entry, depth-first. Then the
   same for legacy `body["functions"]`.

Roles need no special handling: `system`, `developer`, `user`, `assistant` and `tool` messages
all follow rule 1.

Identifier stripping when `policy.strip_user_identifiers`: delete top-level `user` and
`safety_identifier` if present. Do not touch `metadata` (it is developer-defined and may be
needed), but note it in the usage documentation in slice 12.

Malformed input: `messages` missing or not a list, or a message that is not a dict, raises
`MalformedRequestError`. Do not attempt to validate the rest of the API; the provider does that.

Fields never touched: `model`, `messages[].role`, `messages[].name`, `tool_call_id`, tool and
function names, parameter schemas apart from descriptions, `response_format`, `stop`, `logit_bias`,
sampling parameters, `stream`, `stream_options`.

### `openai_chat.py`: response

`restore_response(body, mapping)` mutates the body in place. For each choice:

- `message.content` (string) → `restore`;
- `message.refusal` (string) → `restore`;
- `message.tool_calls[].function.arguments` → `restore(..., json_escape=True)` on the raw
  string (do not parse and re-serialise; keep the model's formatting);
- `message.tool_calls[].custom.input` → `restore`;
- `message.function_call.arguments` → `restore(..., json_escape=True)`;
- `message.annotations`, `logprobs`, `usage`, ids → untouched.

A body without `choices` (for example an error object) is handled by a separate helper:

```python
def restore_all_strings(value: Any, mapping: Mapping) -> Any:
    """Return a copy of a JSON value with `restore` applied to every string value."""
```

Put it in `base.py`; the proxy uses it for upstream error bodies.

### `error_body`

```python
{"error": {"message": message, "type": "aliasgate_error", "param": None, "code": code}}
```

## Tests

Build fixtures as JSON files with synthetic content and load them in tests. Use
`WordListDetector` or the real factory from slice 04 if merged; do not depend on slice 04.

Request side
- string content in system, user and assistant messages → one slot each, in order.
- list content with two text parts → two slots; writing through `apply` changes the body.
- image part: `block` raises with `kind == "image_url"`; `pass` leaves the part untouched and
  still returns the text slots.
- unknown part type behaves like an image part.
- assistant tool call with arguments `{"to":"juan.perez@example.com","cc":["a@example.org"]}` →
  two slots; after `apply` with placeholders the arguments string is valid JSON with compact
  separators and both placeholders.
- arguments that are not valid JSON → one slot on the raw string, no finalizer effect.
- arguments containing non-ASCII (`"José"`) are written back unescaped.
- tool result message (role `tool`) with string and with list content.
- tool definitions: descriptions at function level and nested in `parameters.properties.*` are
  slots; `name`, `enum` values and property names are not. With `tool_definitions=False` no
  tool slot exists.
- slot order: messages first, then prediction, then tools. Assert the exact list of `texts()`
  for a fixture that has all of them.
- `user` and `safety_identifier` removed by default, kept with `strip_user_identifiers=False`.
- `messages` missing → `MalformedRequestError`.
- `apply` with the wrong number of texts raises `ValueError`.
- end-to-end with the engine: pseudonymise a fixture conversation where the same name appears in
  the system prompt, a user message, a tool-call argument and a tool result; all four carry the
  same placeholder.

Response side
- content, refusal and tool-call arguments are restored; with two choices both are restored.
- a tool-call argument whose restored value contains `"` produces valid JSON.
- `logprobs` and `usage` are byte-for-byte unchanged (compare deep copies).
- `restore_all_strings` restores nested strings and leaves numbers, booleans and nulls alone.

Property
- for any JSON-like structure (Hypothesis recursive strategy of dicts, lists and strings),
  `slots_for_json_strings` returns one slot per string value, and applying the identity leaves
  the structure equal to the original.

## Acceptance checklist

- [ ] API reference checked; date recorded in the pull request.
- [ ] `dialects/` imports nothing from `proxy` and performs no I/O.
- [ ] Every unknown content type is rejected under `block`.
- [ ] Slot order is asserted by a test.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Streaming (slice 07).
- The Responses API, Assistants, embeddings, audio, images, files, batch (backlog).
- Validating requests against the OpenAI schema.
