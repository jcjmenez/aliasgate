# Slice 20: request fields forwarded unchanged

## Goal

The dialects read the request fields that v0.6.0 forwards to the provider without anyone
looking at them, which is backlog §10:

- OpenAI `messages[].name`, the participant name of a system, developer, user or assistant
  message;
- OpenAI `metadata` values, `prompt_cache_key` and `stop`, and Anthropic `stop_sequences`;
- the descriptions of an output schema (OpenAI `response_format`, Anthropic `output_config`),
  and the `input_examples` of an Anthropic tool;
- the end user's approximate location (`user_location`) given to hosted web search.

Each of these is pseudonymised like message text, except `user_location`, which is removed by
default (`policy.strip_user_location`). The Anthropic response field that repeats the stop
sequence it met (`stop_sequence`) is restored.

Today a value that appears only in one of these fields reaches the provider unchanged, and a
value that a detector found elsewhere in the request makes the leak audit reject the whole
request. Both are measured below. A request whose new fields hold no detected value is
forwarded exactly as before, apart from the location.

## Depends on

Slice 19 (v0.6.0 released). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-24)

1. **`messages[].name` is pseudonymised.** It is one slot per message, before the message's
   content, like any other text the model reads. The model keeps the distinction between
   participants. There is no new setting: a name no detector finds is a missed detection, as
   in content. Rejected: removing `name` by default, which leaks nothing whatever the detectors
   but loses the participant distinction.
2. **`prompt_cache_key` is pseudonymised.** A detected value (an email) becomes a placeholder,
   and a key that holds no data (`feature-summary-v2`) is unchanged. Only keys that were
   replaced lose cache affinity. Rejected: removing it together with `user` under
   `strip_user_identifiers`, which costs every request its cache affinity.
3. **`user_location` is removed by default, with a new setting.** `policy.strip_user_location`
   (default `true`) removes the location from OpenAI `web_search_options` and from every
   Anthropic tool. Detectors rarely find a town name, so pseudonymising it would protect
   nothing. Hosted search loses localisation, but it already sees only placeholders (threat
   model, residual risk 5). Rejected: forwarding it by default, and removing it under
   `strip_user_identifiers`, because a location is not an identifier.
4. **JSON Schema values other than `description` stay in the backlog.** `enum`, `const`,
   `default`, `examples` and `title` in tool and output schemas are still forwarded unchanged.
   `enum` and `const` must stay exact for strict decoding, and two values with the same key
   would become two equal placeholders. They need their own design.

## Decisions made while writing this spec

These follow from the decisions above or from the existing contracts. They are binding.

- **One slice.** The fields share one change to each dialect and one test fixture, and none of
  them needs the engine.
- **Where the new slots go.** A message's `name` comes first in that message, so that the
  conversation stays append-stable: later turns never renumber it. Output schema descriptions
  and input examples come right after the tool descriptions. Stop sequences, metadata values
  and the cache key come last, because they belong to the request, not to the conversation.
- **Output schemas and input examples follow `policy.tool_definitions`.** Like tool
  descriptions, they are written by the developer, and an operator who turned tool definitions
  off turned these off too. The setting keeps its name. Its documentation says that it covers
  tool definitions, their input examples and output schemas.
- **Stop sequences, metadata values and the cache key have no setting.** They are always
  pseudonymised.
- **Keys are never slots.** Metadata keys are forwarded unchanged, as the keys of tool-call
  arguments already are (architecture §6.1). Error messages name a metadata value by its
  position, never by its key.
- **The function role's `name` is not a slot.** It names a function, like tool names, which
  are never changed.
- **`web_search_options` is kept when its location is removed**, even when it becomes empty,
  because its presence turns web search on.
- **Where a walked field has the wrong shape, `MalformedRequestError` names its position**, as
  in slices 05 and 08: a value the dialect could not read is never forwarded.

## Found while specifying (prototype of 2026-11-24, outside the repository, synthetic data)

Provider shapes were checked on 2026-11-24 against openai-python 3.58.0 and
anthropic-sdk-python 2.13.0 (main branches), and the OpenAI OpenAPI spec (`manual_spec`
branch, info.version 2.3.0). Fields that carry text, and what v0.6.0 does with them:

| API | Field | v0.6.0 |
|---|---|---|
| OpenAI | `messages[].name` (system, developer, user, assistant) | forwarded |
| OpenAI | `metadata` (up to 16 pairs, keys up to 64 characters, values up to 512) | forwarded |
| OpenAI | `prompt_cache_key` (documented as replacing `user`) | forwarded |
| OpenAI | `stop` (a string or up to 4 strings) | forwarded |
| OpenAI | `response_format.json_schema.description` and descriptions inside its `schema` | forwarded |
| OpenAI | `web_search_options.user_location.approximate` (`city`, `region`, `country`, `timezone`) | forwarded |
| Anthropic | `stop_sequences`; the response repeats the one it met in `stop_sequence`, and the stream in `message_delta.delta.stop_sequence` | forwarded |
| Anthropic | `tools[].input_examples` (any JSON) | forwarded |
| Anthropic | `output_config.format.schema` (descriptions inside it) | forwarded |
| Anthropic | `user_location` of the web search tools (`city`, `region`, `country`, `timezone`) | forwarded |
| both | tool names, parameter names, `enum`/`const`/`default`/`examples`/`title` in schemas | forwarded (decision 4) |
| both | ids: `tool_call_id`, `audio.id`, `container`, `user_profile_id`, `workspace_id`, `inference_geo` | forwarded, not user data |

The OpenAI response does not repeat `stop` or `metadata`. `count_tokens` accepts the same
tools as `messages`. Anthropic `metadata` has only `user_id`, which is already removed.

Reproduction through the gateway (dictionary with `Juan Pérez` and `ACME Corporation`, default
rules, mocked upstream, message text naming both and `juan.perez@example.com`):

| Case | v0.6.0 | Prototype |
|---|---|---|
| `name: "maria.lopez@example.org"` | forwarded | `[EMAIL_1]` |
| `name: "Juan Pérez"` | 500 `leak_audit_failed` | `[PERSON_1]` |
| `name: "maria_lopez"` | forwarded | forwarded (no detector without ML) |
| `metadata: {"customer_email": "maria.lopez@example.org"}` | forwarded | `[EMAIL_2]` |
| `metadata: {"customer": "Juan Pérez"}` | 500 `leak_audit_failed` | `[PERSON_1]` |
| `prompt_cache_key: "maria.lopez@example.org"` | forwarded | `[EMAIL_2]` |
| `stop: ["Juan Pérez:"]`, `stop_sequences: ["Juan Pérez:"]` | 500 `leak_audit_failed` | `["[PERSON_1]:"]` |
| `stop: ["maria.lopez@example.org"]` | forwarded | `["[EMAIL_2]"]` |
| `response_format` description `Reply to maria.lopez@example.org` | forwarded | `Reply to [EMAIL_2]` |
| `input_examples: [{"to": "maria.lopez@example.org"}]` | forwarded | `[{"to": "[EMAIL_2]"}]` |
| `output_config` schema description with the email | forwarded | replaced |
| `user_location` with `city: "Villarrobledo"` (both APIs) | forwarded | removed |
| tool schema `default: "maria.lopez@example.org"` | forwarded | forwarded (decision 4) |

The leak audit is what turns the second kind of case into a rejection. So a stop sequence
naming a person mentioned in the conversation, which is common in agent prompts, makes the
request fail today.

With the ML layer (GLiNER, default model), a `name` slot on its own was detected as `PERSON`
for `maria_lopez`, `MariaLopez`, `maria.lopez`, `Maria_Lopez`, `mlopez`, `Maria`, `María López`
and `Maria Lopez`, and not for `support_bot`. It was also detected for `assistant`, which costs
utility only. After `Soy María López ...` in the content, a `name` of `María` became
`[PERSON_1_B]` (person linking), and `maria_lopez` became `[PERSON_2]` (not linked).

With the prototype applied, 7 of the 2341 tests changed, all of them dialect or proxy tests
(the prototype had no setting, so the configuration and CLI tests could not change). They are
the slot-order tests of the OpenAI dialect and the test of fields outside the slots, because
the slice 05 fixture already has `name`, `metadata`, `stop` and `response_format`. They also
include the two leak audit tests and the logging hygiene test, which used OpenAI `metadata` as
their example of a field no dialect reads. No Anthropic test changed.

A placeholder can be longer than the value it replaces (`Ana` becomes `[PERSON_1]`). A metadata
value close to OpenAI's 512-character limit can then exceed it, and the provider rejects the
request. Nothing leaks. The OpenAPI spec gives message `name` no pattern and no length limit;
whether the live API accepts brackets in it is checked manually (see Tests).

## Decisions made while implementing

- **A `description` inside `input_examples` is one slot, among the tool descriptions.** The
  tool walk of slice 08 already takes every `description` string anywhere in `tools`, those
  in `input_examples` included. So the examples add every other string, through the new
  `slots_for_strings_not_under(container, key)` in `base.py`. Walking all of them again would
  have made one value two slots, and the v0.6.0 slots stay a prefix of the new ones. A test
  pins it. (**Changed in [slice 21](slice-21-object-keys.md):** the examples are read by
  `slots_for_keys_and_strings(..., skip_under="description")`, and
  `slots_for_strings_not_under` was removed, since nothing used it any more.)
- **`slots_for_string_list(items, where)`** in `base.py` gives a slot per item and raises
  `MalformedRequestError` naming `where[i]`. OpenAI `stop` and Anthropic `stop_sequences` share
  it.
- **`output_config` is read even without `tools`**, and an `output_config` without `format`
  (`{"effort": "low"}`) adds no slot.
- **The stream handles `message_delta` before looking for an `index`**, which that event does
  not have. Unresolved placeholders in its `stop_sequence` are added to the settled count, and
  the event is re-serialised only when the restored value differs.
- **Tests beyond the list:** an `output_config` without `format`; tools that are not objects,
  skipped by the new walks; a `web_search_options` that is not an object, left to the
  provider; the location removed with `tool_definitions` off; a stop sequence without
  placeholders and malformed `message_delta` events, sent as the original event; and a proxy
  test with `strip_user_location: false`, which forwards the location.
- **Measured.** The reproduction script, run against the implementation, gives the prototype
  column of the table above case by case. 37 mutants were tried: each new slot, the order of
  the request fields, each shape check, the metadata error naming the key, the location
  removal (kept, removed regardless of the policy, `web_search_options` deleted when empty),
  `stop_sequence` restoration in both paths, the unresolved count, re-serialising unchanged
  events, the defaults, `_policy` and the `check` line. All were killed. Only `_policy` not
  passing the setting first survived, because the `Policy` default is also on; the proxy test
  with the setting off closes it. The new property passed 20,000 examples.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/dialects/base.py` | change: `Policy.strip_user_location` |
| `src/aliasgate/dialects/openai_chat.py` | change: request slots, location |
| `src/aliasgate/dialects/anthropic_messages.py` | change: request slots, location, `stop_sequence` in the response and the stream |
| `src/aliasgate/config.py` | change: `PolicySettings.strip_user_location` |
| `src/aliasgate/proxy/app.py` | change: `_policy` passes the new setting |
| `src/aliasgate/cli.py` | change: the `check` policy line |
| `tests/dialects/test_openai_chat.py`, `tests/dialects/test_anthropic_messages.py`, `tests/dialects/test_anthropic_stream.py` | change |
| `tests/dialects/fixtures/openai_chat/request_all_fields.json` | change: add `prompt_cache_key` and `web_search_options` |
| `tests/proxy/test_app.py`, `tests/proxy/test_app_anthropic.py`, `tests/proxy/test_metrics.py`, `tests/proxy/test_invariants.py` | change |
| `tests/test_config.py`, `tests/test_cli.py` | change |
| `docs/architecture.md` | change: §6 slot order, §6.1 table |
| `docs/usage.md` | change: `check` output, "Fields forwarded unchanged", `leak_audit_failed` causes |
| `docs/threat-model.md` | change: a residual risk for fields forwarded unchanged |
| `examples/aliasgate.example.yaml` | change: the policy section |
| `docs/plan/slice-04-config-and-dictionary.md`, `docs/plan/slice-05-openai-chat-dialect.md`, `docs/plan/slice-08-anthropic-dialect.md` | change: a "Changed in slice 20" note each |
| `docs/plan/README.md`, `docs/plan/backlog.md`, `README.md` | change: status, backlog §10 |
| `CHANGELOG.md` | change: `Unreleased` entries |

The engine, the detectors, the audit, the mapping and the evaluation corpora do not change.

## Specification

Read slices 05 and 08 and architecture §6 before starting. Every new slot is an ordinary slot:
the engine, propagation, linking and the leak audit treat it as they treat message text.

### Policy and configuration

```python
@dataclass(frozen=True, slots=True)
class Policy:
    unsupported_content: Literal["block", "pass"] = "block"
    strip_user_identifiers: bool = True
    strip_user_location: bool = True
    tool_definitions: bool = True
```

`PolicySettings` gains `strip_user_location: bool = True` (environment
`ALIASGATE_POLICY__STRIP_USER_LOCATION`), placed after `strip_user_identifiers`. `_policy` in
`app.py` passes it. `aliasgate check` prints it after `strip_user_identifiers`:

```
policy: unsupported_content=block, strip_user_identifiers=on, strip_user_location=on, tool_definitions=on, leak_audit=on
```

### OpenAI Chat Completions request (`openai_chat.py`)

The slot order becomes (new items in bold):

1. each message in order: **its `name`, unless its `role` is `function`**, then its `content`
   (a string, or its `text` and `refusal` parts in order), then the assistant's `refusal`, then
   each of its `tool_calls`, then the legacy `function_call`;
2. `prediction.content`;
3. if `policy.tool_definitions`: every `description` string inside `tools`, then inside
   `functions`, **then inside `response_format`**, each depth-first;
4. **`stop`: the string, or each string of the list in order;**
5. **`metadata`: each value, in the order of its keys;**
6. **`prompt_cache_key`.**

`response_format` is read with `slots_for_key_strings` over the whole object, so it finds
`json_schema.description` and every `description` inside `json_schema.schema`. A
`response_format` of type `text` or `json_object` has none.

When `policy.strip_user_location` is on and `web_search_options` is an object, its
`user_location` key is deleted. `web_search_options` is kept, even when it becomes empty.

A missing or `null` field adds no slot. Shapes, each raising `MalformedRequestError` with the
message shown and nothing taken from the value:

| Field | Wrong shape | Message |
|---|---|---|
| `messages[i].name` | not a string | `messages[i].name: expected a string` |
| `stop` | not a string or a list | `stop: expected a string or a list` |
| `stop[j]` | not a string | `stop[j]: expected a string` |
| `metadata` | not an object | `metadata: expected an object` |
| a `metadata` value | not a string | `metadata: value j: expected a string`, with `j` the zero-based position in key order |
| `prompt_cache_key` | not a string | `prompt_cache_key: expected a string` |
| `response_format` (only with `tool_definitions`) | not an object | `response_format: expected an object` |

The response is unchanged: the OpenAI response repeats neither `stop` nor `metadata`.

### Anthropic Messages request (`anthropic_messages.py`)

The slot order becomes (new items in bold):

1. `system`;
2. each message's content, as in slice 08;
3. if `policy.tool_definitions`: every `description` string inside `tools`, depth-first;
   **then, for each tool in order that is an object with `input_examples`, every string value
   of `input_examples`, depth-first (keys are never slots); then every `description` string
   inside `output_config.format`, depth-first;**
4. **`stop_sequences`, each string in order.**

When `policy.strip_user_location` is on, the `user_location` key is deleted from every entry
of `tools` that is an object. This covers every version of the web search tool, and
`count_tokens`, which uses the same `prepare_request`.

Shapes, as above:

| Field | Wrong shape | Message |
|---|---|---|
| `tools[i].input_examples` (only with `tool_definitions`) | not a list | `tools[i].input_examples: expected a list` |
| `output_config` (only with `tool_definitions`) | not an object | `output_config: expected an object` |
| `output_config.format` (only with `tool_definitions`) | not an object | `output_config.format: expected an object` |
| `stop_sequences` | not a list | `stop_sequences: expected a list` |
| `stop_sequences[j]` | not a string | `stop_sequences[j]: expected a string` |

`metadata` handling is unchanged.

### Anthropic response and stream

- `restore_response` also restores `body["stop_sequence"]` when it is a string. `null` and any
  other value are left as they are.
- The stream transformer restores `delta.stop_sequence` of a `message_delta` event when it is a
  string, and sends the event re-serialised. A `message_delta` whose `stop_sequence` is `null`
  or missing is sent as the original event, unchanged, as every other event is.
- In the stream, unresolved placeholders in it are added to the transformer's `unresolved`
  count. A non-streamed answer needs nothing more: the proxy already counts them in every
  string of the restored body.

### Architecture (`docs/architecture.md`)

- §6, slot order: "system content first, then messages in array order, and inside a message
  its name and then its parts in array order, then tool definitions, their input examples and
  output schemas, then the request's stop sequences, metadata values and prompt cache key
  last". Keep the reason for tool definitions coming after the conversation.
- §6.1 table: new rows for message names (yes / n/a); stop sequences (yes / Anthropic
  `stop_sequence` restored); OpenAI `metadata` values and `prompt_cache_key` (yes, keys
  unchanged / n/a); output schema descriptions and tool input examples (with policy
  `tool_definitions` / n/a); end-user location `user_location` (removed by default, policy
  `strip_user_location` / n/a). The tool definitions row adds that `enum`, `const`, `default`,
  `examples` and `title` values are never changed.

### Documentation

- `docs/usage.md`: the `check` sample output; "Fields forwarded unchanged" lists what is still
  forwarded (tool and function names, parameter names, the schema values of decision 4, object
  keys including metadata keys, the function role's `name`, ids, `user_location` with
  `strip_user_location: false`, unsupported content under `pass`) and says what the gateway now
  reads; the `leak_audit_failed` row gives a schema `enum` value instead of OpenAI `metadata` as
  its example.
- `docs/threat-model.md`: a new residual risk at the end of the list, "Fields forwarded
  unchanged", naming the same fields and saying that the leak audit rejects a request when one
  of them holds a value detected elsewhere.
- `examples/aliasgate.example.yaml`: `strip_user_location: true` with a comment, the
  `tool_definitions` comment covering input examples and output schemas, and the `leak_audit`
  comment's example fields updated.
- Slices 04, 05 and 08: a short "Changed in slice 20" note where they list the policy, the slot
  order or the fields never touched.
- `CHANGELOG.md`: `Added` (`policy.strip_user_location`), `Changed` (`user_location` removed by
  default; a message `name` holding a detected value is numbered before its content), `Fixed`
  (the fields of the Goal were forwarded unchanged, or made the leak audit reject the request).
- Backlog §10: "Cut into slice 20", and what stays open: the schema values of decision 4,
  object keys, and an option to forward a field unchanged.

## Tests

Synthetic values only: `Juan Pérez` and `ACME Corporation` from the proxy dictionary,
`juan.perez@example.com`, `maria.lopez@example.org`, `T-0001`, and the town `Springfield` for
locations. Write non-ASCII characters with `\u` escapes.

`tests/dialects/fixtures/openai_chat/request_all_fields.json` gains
`"prompt_cache_key": "cache key"` and
`"web_search_options": {"search_context_size": "low", "user_location": {"type": "approximate", "approximate": {"city": "Springfield", "country": "US"}}}`.

`test_openai_chat.py`

- The slot order test expects the user message's name `customer` right before `user part`,
  `response format description` after `query description`, then `stop text`, `T-0001` and
  `cache key`. The function message's name `lookup` is not a slot.
- The tool descriptions test expects `response format description` last. With
  `tool_definitions` off, no slot holds `description`, and the last three texts are
  `stop text`, `T-0001`, `cache key`.
- The test of fields outside the slots no longer lists `metadata`, `stop` and
  `response_format` as untouched, and no longer expects the user message's `name` unchanged.
  It checks instead that metadata keys, `response_format.json_schema.name` and `schema` apart
  from descriptions, the function message's `name`, `web_search_options.search_context_size`
  and the other fields it lists are unchanged. These are the changed expectations of decision
  1 and of this slice; the pull request names them.
- Applying the texts unchanged changes nothing but the removed identifiers and the removed
  `user_location`.
- `stop` as a string is one slot; as a list, one slot per item in order; `null` adds none.
  `stop: 3` and `stop: ["a", 1]` raise with the messages of the table.
- `metadata` values are slots in key order and keys are unchanged after `apply`;
  `metadata: []` raises; `{"note": "x", "maria.lopez@example.org": 2}` raises a message that
  names `value 1` and does not contain the key.
- `prompt_cache_key: 5` raises; `null` adds no slot.
- `messages[0].name: 7` raises `messages[0].name: expected a string`; `name: null` adds no
  slot; a message of role `function` with a name adds no name slot.
- `response_format` of type `text` adds no slot; `response_format: "json"` raises with
  `tool_definitions` on, and is left as it is with it off.
- `user_location` is removed by default and `web_search_options` keeps its other keys; an
  empty `web_search_options` after removal is still present; with `strip_user_location` off the
  location is kept; with `strip_user_identifiers` off and `strip_user_location` on it is still
  removed, and the other way round.
- property: for any strings in `messages[*].name`, `stop`, `metadata` values and
  `prompt_cache_key` (Hypothesis text, any keys), applying the texts unchanged leaves the body
  equal to the original minus the removed fields, and replacing every slot with `[SLOT_n]`
  leaves none of those strings in the body.

`test_anthropic_messages.py`

- With a body holding `system`, one message, a tool with a description and
  `input_examples: [{"to": "maria.lopez@example.org", "n": 2, "cc": ["a", "b"]}]`, an
  `output_config` whose `format.schema` has a property with a description, and
  `stop_sequences: ["x", "y"]`: the slots are, in order, the system text, the message text,
  the tool description, `maria.lopez@example.org`, `a`, `b`, the schema description, `x`, `y`.
  After `apply`, keys and the number `2` are unchanged.
- With `tool_definitions` off, neither the examples nor the schema description is a slot, and
  the stop sequences still are.
- Each wrong shape of the table raises with its message.
- A `web_search_20250305` tool and a `web_search_20260318` tool lose `user_location` and keep
  their other keys; with `strip_user_location` off they keep it.
- `restore_response` restores `stop_sequence: "[PERSON_1]:"` to `Juan Pérez:`, and leaves
  `null` and `3` as they are.

`test_anthropic_stream.py`

- A `message_delta` with `delta.stop_sequence: "[PERSON_1]:"` comes out with `Juan Pérez:`.
- A `message_delta` with `stop_sequence: null`, and one without it, comes out as the original
  event, byte for byte.

Proxy tests (`test_app.py`, `test_app_anthropic.py`)

- OpenAI: content naming `Juan Pérez`, with `name: "Juan Pérez"`,
  `stop: ["Juan Pérez:"]`, `metadata: {"customer": "Juan Pérez", "mail":
  "maria.lopez@example.org"}`, `prompt_cache_key: "maria.lopez@example.org"` and a
  `web_search_options.user_location`, answers 200. The upstream body holds `[PERSON_1]` in the
  name, the stop sequence and the metadata, `[EMAIL_2]` in the metadata and the cache key, and
  `web_search_options` without `user_location`.
- Anthropic: content naming `Juan Pérez` with `stop_sequences: ["Juan Pérez:"]` answers
  200 with `[PERSON_1]:` upstream; the upstream answer's `stop_sequence: "[PERSON_1]:"` reaches
  the client as `Juan Pérez:`, in a non-streamed answer and in a streamed one.
- `count_tokens` with a web search tool forwards it without `user_location`.

Leak audit and hygiene (`test_metrics.py`, `test_invariants.py`)

- The two leak audit tests and the logging hygiene test use a field no dialect reads, an
  unknown top-level field `"x_note": "Juan Pérez"`, in place of `metadata`. They keep
  every other assertion. The pull request names this change.
- The hygiene test also sends one request with the new fields holding the dictionary values
  and checks that no log line or metric holds them.

Configuration and CLI

- `test_config.py`: the defaults dict gains `strip_user_location: True` (a changed
  expectation), and `ALIASGATE_POLICY__STRIP_USER_LOCATION=false` turns it off.
- `test_cli.py`: `check` prints `strip_user_location=on`, and `off` when the setting is off.

Evaluation: every gate in `tests/eval` passes unchanged, because the engine does not change.

Mutation: remove each new slot, the location removal, the `stop_sequence` restoration (both
paths) and each shape check in turn, and confirm that a test fails each time.

Manual check (optional, after the maintainer approves the spend, following the earlier
real-provider checks): through `aliasgate serve`, one OpenAI-route request (Azure gpt-4.1)
with a detected name in `messages[].name`, `metadata`, `prompt_cache_key` and `stop`, which
the provider must accept with the placeholders; and one Anthropic-route request (Claude on
Foundry) whose answer meets a pseudonymised stop sequence, which the client must receive
restored. Record the result in the slice file.

## Acceptance checklist

- [ ] Every field of the Goal is pseudonymised (or, for `user_location`, removed by default)
      on both routes, `count_tokens` included, and the reproduction table's prototype column
      holds for each case.
- [ ] Anthropic `stop_sequence` is restored in non-streamed and streamed answers.
- [ ] A request without the new fields, or whose new fields hold no detected value, is
      forwarded exactly as before, apart from `user_location`.
- [ ] Every pre-existing test passes unchanged, except the expectations listed under Tests,
      which the pull request names.
- [ ] Every wrong shape raises `MalformedRequestError` with a message that holds no value and
      no metadata key.
- [ ] The mutation checks are done and recorded in the pull request.
- [ ] Architecture, usage, threat model, example configuration, slices 04, 05 and 08, backlog
      §10 and CHANGELOG are updated in the same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- `enum`, `const`, `default`, `examples` and `title` values in tool and output schemas
  (decision 4, backlog §10).
- Object keys: metadata keys and the keys of tool-call arguments and input examples.
- An option to forward any of these fields unchanged.
- Pseudonymising `user_location` instead of removing it.
- A value that grows past a provider limit once pseudonymised (a 512-character metadata
  value). The provider rejects it; nothing leaks.
- Anthropic `document` and `search_result` blocks and their titles: they are unsupported
  content, rejected by default (backlog §6).
- The OpenAI stored completions API, the Responses API and other endpoints (backlog §4).
- Changes to the engine, the detectors, the audit, the mapping or the evaluation corpora.
