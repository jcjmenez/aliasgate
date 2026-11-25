# Slice 21: object keys and label slots

## Goal

The keys of the objects that carry data are pseudonymised, and the leak audit checks every key.
These objects are the arguments of OpenAI tool calls, the inputs of Anthropic `tool_use` blocks,
Anthropic tool `input_examples` and OpenAI `metadata`. Keys are part of backlog §10.

Today the audit reads string values only. A value detected in the conversation still reaches
the provider unchanged when it is a key: `Juan Pérez` becomes `[PERSON_1]` in the message text
and stays `Juan Pérez` in `{"attendees": {"Juan Pérez": "accepted"}}`. This is a silent leak,
and the threat model and the usage guide wrongly say the audit catches it. Measured below.

Keys are short labels with no sentence around them, and the ML layer reads them badly: it took
22 of 59 common field names for entities (`owner`, `company`, `customer_email`). So the slice
also adds **label slots**. A label slot is detected by the dictionary and the rules, and is
replaced by propagation and person or organisation linking like any slot. Detectors that need
surrounding text, which today means the ML layer, do not run on it. [Slice 22](slice-22-schema-values.md)
uses label slots for schema values.

Non-streamed Anthropic answers start restoring `tool_use` input keys. Streamed answers and
OpenAI answers already restore them. A request whose keys hold no detected value is forwarded
exactly as before.

## Depends on

Slice 20 (v0.7.0 released). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-25)

1. **The audit checks every key, and the keys of data objects are pseudonymised.** Data
   objects are tool-call arguments and `tool_use` inputs in the history, Anthropic
   `input_examples` and OpenAI `metadata`. Their keys go through the dictionary, the rules,
   propagation and linking, but not the ML layer. Every other key is checked by the audit
   only: schema property names, keywords and message fields. Rejected: auditing keys without
   pseudonymising any. Then a tool call shaped like a map (`{"Juan Pérez": "accepted"}`) gets a
   500 on every turn after the model makes it. Also rejected: running the ML layer on keys,
   which renames field names, and Anthropic rejects an example that no longer matches its
   schema.
2. **Label slots in the engine.** Each slot says whether it is a label. The proxy passes the
   label indexes to the engine, and the engine skips the contextual detectors (GLiNER) on
   them. Propagation, linking, the allow list and the audit do not change. Rejected: labels
   with no detector at all, only propagation, because an email that appears only in a key
   would still leak.
3. **Two slices, specified together.** Slice 21 (this one) covers keys, label slots and the
   audit, so the silent leak closes first. Slice 22 covers schema values and uses label slots.
   An option to forward a field unchanged stays in backlog §10.

## Decisions made while writing this spec

These follow from the decisions above or from the existing contracts. They are binding.

- **A key comes right before its value** in slot order, depth-first, keys in insertion order.
  A key that holds no detected value numbers nothing, so the numbering of a request changes
  only when a key holds a detected value. The conversation stays append-stable.
- **Metadata: each key, then its value, in key order.** Shapes are checked as in slice 20.
  Error messages still name a metadata value by its position, never by its key.
- **Every key of a data object is a slot**, whatever its value: `"urgent": true` gives a slot
  for `urgent`. In `input_examples`, a string directly under a `description` key is already a
  tool description slot (slice 20), so it is not a slot again. The key `description` itself
  is a key slot.
- **Renaming keeps order and never merges.** Keys are renamed in place, after every slot is
  set and before tool-call arguments are re-serialised. Two keys of one object can become the
  same text, as `ACME` and `Acme` both become `[ORG_1]`. Then the request is rejected with 400
  `malformed_request`, naming the object's position and nothing of its keys. Merging the two
  would lose a value.
- **Restoring a key never merges either.** In a restored object, a key is left as it is when
  its restored form is already one of the object's keys, or the restored form of an earlier
  key.
- **Unresolved placeholders in keys are counted** like those in values, in non-streamed
  answers and in the `tool_use` input of a streamed `content_block_start`.
- **No new setting.** Keys of `input_examples` follow `policy.tool_definitions`, like the
  examples' values. All other key slots are always on.
- **The contextual detectors are the ML detector.** The factory passes it as `contextual`, and
  its place in the run order does not change. The evaluation harness, `aliasgate demo` and
  `aliasgate check` pass no labels, so their output does not change.

## Found while specifying (prototype of 2026-11-25, outside the repository, synthetic data)

Reproduction through the gateway: dictionary with `Juan Pérez` and `ACME Corporation`, default
rules, mocked upstream, user text `Escribe a Juan Pérez sobre el pedido.`

| Case | v0.7.0 | Prototype |
|---|---|---|
| OpenAI `metadata: {"maria.lopez@example.org": "vip"}` | forwarded | `{"[EMAIL_1]": "vip"}` |
| OpenAI `metadata: {"Juan Pérez": "vip"}` | **forwarded, though `Juan Pérez` is `[PERSON_1]` in the text** | `{"[PERSON_1]": "vip"}` |
| OpenAI history `arguments: {"attendees": {"Juan Pérez": "accepted"}}` | 500 `leak_audit_failed` (the arguments are a string) | `{"attendees":{"[PERSON_1]":"accepted"}}` |
| Anthropic history `tool_use.input: {"attendees": {"Juan Pérez": "accepted"}}` | **forwarded** | `[PERSON_1]` key |
| Anthropic `input_examples: [{"Juan Pérez": "accepted"}]` | **forwarded** | `[PERSON_1]` key |
| tool schema with property `Juan Pérez`, listed in `required` | 500 `leak_audit_failed` (through `required`) | 500 `leak_audit_failed` |
| OpenAI answer, arguments `{"attendees": {"[PERSON_1]": ...}}` | restored | restored |
| Anthropic answer, `tool_use.input` with key `[PERSON_1]`, not streamed | **`[PERSON_1]` reaches the client** | `Juan Pérez` |
| the same answer, streamed | restored | restored |

The rows in bold are the gap: a detected value leaves in a key, or a placeholder reaches the
user. An Anthropic tool call shaped like a map shows both. After a streamed answer, the client
holds `Juan Pérez` as a key and sends it back on the next turn, where it leaves unchanged. After
a non-streamed answer, the client holds the placeholder instead, and the user sees it.

**The ML layer on keys** (GLiNER, default model, one key per slot): 22 of 59 common field names
were detected. Among them were `owner`, `recipient`, `customer`, `customer_email`, `assignee`,
`manager`, `user`, `company`, `organization`, `address`, `author`, `sender`, `client`, `vendor`,
`customerName` and `assistant`, while `to`, `subject`, `status`, `city` and `email` were not.
With label slots, a `tool_use` input
`{"owner": "Ana Ruiz", "recipient": "maria.lopez@example.org", "attendees": {"Ana Ruiz": "accepted", "customer": "pending"}}`,
after `Asigna el ticket a Ana Ruiz` in the text, keeps `owner`, `recipient`, `attendees` and
`customer` and becomes `{"owner": "[PERSON_1]", "recipient": "[EMAIL_1]", "attendees": {"[PERSON_1]": "accepted", "customer": "pending"}}`.
`Ana Ruiz` was detected by the ML layer in the text and replaced in the key by propagation.
Without label slots, every one of those keys became a placeholder.

**Test changes.** With the prototype applied, 18 of the 2394 tests changed, all of them
expectations that decision 1 changes. These are the dialect tests that list slots of tool-call
arguments, tool inputs, input examples or metadata, the base test of `restore_all_strings` with
a placeholder key, and `test_keys_are_not_checked` of the audit. They are listed under Tests.
Label slots changed no other test, and no proxy, engine, evaluation or configuration test
changed.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/dialects/base.py` | change: `Slot.label`, `PreparedRequest.labels`, `slots_for_keys_and_strings`, keys in `restore_all_strings` |
| `src/aliasgate/dialects/openai_chat.py` | change: argument and metadata keys |
| `src/aliasgate/dialects/anthropic_messages.py` | change: `tool_use` input and `input_examples` keys, finalizers, unresolved count in the stream |
| `src/aliasgate/engine.py` | change: `contextual`, `labels` |
| `src/aliasgate/factory.py` | change: the ML detector is contextual |
| `src/aliasgate/audit.py` | change: keys are searched |
| `src/aliasgate/proxy/app.py` | change: labels passed, 400 when `apply` raises `MalformedRequestError`, keys counted as unresolved |
| `tests/dialects/test_base.py`, `tests/dialects/test_openai_chat.py`, `tests/dialects/test_anthropic_messages.py`, `tests/dialects/test_anthropic_stream.py` | change |
| `tests/test_engine.py`, `tests/test_factory.py`, `tests/test_audit.py` | change |
| `tests/proxy/test_app.py`, `tests/proxy/test_app_anthropic.py`, `tests/proxy/test_invariants.py` | change |
| `docs/architecture.md` | change: §3.2, §4.1, §6, §6.1, §7, §8 |
| `docs/usage.md` | change: "Fields forwarded unchanged", `malformed_request` and `leak_audit_failed` causes |
| `docs/threat-model.md` | change: residual risk 9 |
| `docs/plan/slice-05-openai-chat-dialect.md`, `docs/plan/slice-08-anthropic-dialect.md`, `docs/plan/slice-09-ml-detector.md`, `docs/plan/slice-10-audit-policy-metrics.md` | change: a "Changed in slice 21" note each |
| `docs/plan/README.md`, `docs/plan/backlog.md`, `README.md` | change: status, backlog §10 |
| `CHANGELOG.md` | change: `Unreleased` entries |

The detectors, the mapping, the configuration and the evaluation corpora do not change.

## Specification

Read slices 05, 08, 10 and 20 and architecture §4 and §6 before starting.

### Label slots (`base.py`)

```python
@dataclass(slots=True)
class Slot:
    get: Callable[[], str]
    set: Callable[[str], None]
    label: bool = False
```

A label is a short value written as structure rather than prose. `PreparedRequest.labels()`
returns the indexes of the label slots in slot order, as a `frozenset[int]`. Its docstring says
that contextual detectors do not run on them.

```python
def slots_for_keys_and_strings(
    container: dict[str, Any] | list[Any],
    finalizers: list[Callable[[], None]],
    where: str,
    *,
    skip_under: str | None = None,
) -> list[Slot]: ...
```

The helper returns a slot for every key and every string value reachable in `container`. The
walk is depth-first, dicts in insertion order with each key right before its value, and lists in
index order. Key slots are labels and value slots are not. A string stored directly under the
key `skip_under` is not a slot, but its key is. For every object reached, the helper appends to
`finalizers` one step that renames that object's keys in place. The step keeps the key order and
does nothing when no key changed. When two keys of the object would become equal, it raises
`MalformedRequestError(f"{where}: two keys of one object would become the same")` and changes
nothing in that object. The walk is iterative, like `_string_slots`.

`restore_all_strings` restores keys as well as string values, keeping the key order. A key is
left as it is when its restored form is already a key of the same object, or the restored form
of an earlier key. Its docstring says so.

### Engine (`engine.py`)

```python
class Pseudonymizer:
    def __init__(self, detectors: Sequence[Detector], *, allow_list: Iterable[str] = (),
                 propagate: bool = True, min_propagation_length: int = 3,
                 person_tokens: bool = False, org_tokens: bool = False,
                 contextual: Sequence[Detector] = ()) -> None: ...
    def pseudonymize(self, texts: Sequence[str], *,
                     labels: Collection[int] = ()) -> PseudonymizedBatch: ...
    def analyze(self, texts: Sequence[str], *, labels: Collection[int] = ()) -> list[list[Span]]: ...
```

- `contextual` detectors need the text around a value. They run after `detectors`, on every
  text whose index is not in `labels`. The literal detector and `detectors` run on every text.
  `detectors` (the property) returns the literal detector, then `detectors`, then
  `contextual`.
- An index in `labels` outside `range(len(texts))` raises `ValueError`, whose message holds no
  text.
- Everything after detection is unchanged: the allow list, overlap resolution, placeholder
  assignment, person and organisation linking, and propagation. So a value detected in a text
  slot is replaced in a label slot too.
- With no labels, or with no contextual detector, the output is the same as today.
- `analyze` and `pseudonymize` stay views of one analysis and take the same `labels`.

`build_pseudonymizer` passes the ML detector, wrapped in its cache, as `contextual` and no
longer in `detectors`. The run order is still dictionary, rules, ML.

### OpenAI Chat Completions (`openai_chat.py`)

- Tool-call `function.arguments` and legacy `function_call.arguments` that parse to an object
  or an array: the slots are `slots_for_keys_and_strings(parsed, finalizers, where)`, with
  `where` the position of the `arguments` field. The key renaming steps come before the step
  that re-serialises the arguments. Arguments that do not parse to an object or an array stay
  one slot on the raw string.
- `metadata`: the shape checks of slice 20 come first, then
  `slots_for_keys_and_strings(metadata, finalizers, "metadata")`, so each key comes before its
  value.
- The response side does not change. Arguments are restored on the raw JSON string, keys
  included.

### Anthropic Messages (`anthropic_messages.py`)

- The collector gains `finalizers`, which `prepare_request` passes to `PreparedRequest`.
- A `tool_use` block's `input` in the request:
  `slots_for_keys_and_strings(tool_input, finalizers, f"{where}.input")`.
- `input_examples` of tool `i` (with `policy.tool_definitions`):
  `slots_for_keys_and_strings(examples, finalizers, f"tools[{i}].input_examples", skip_under="description")`.
- `restore_response` and the stream's `content_block_start` restore `tool_use` input keys
  through `restore_all_strings`. The stream adds the unresolved placeholders of keys to its
  settled count.

### Leak audit (`audit.py`)

`audit_outbound` searches every key as well as every string value of the outbound payload, the
same two ways. The docstring and architecture §8 say "every string value and every key". A
detected value in a key that is not a slot (a schema property name, a `$defs` name, a keyword)
makes the request fail with 500 `leak_audit_failed`.

### Proxy (`proxy/app.py`)

- The engine is called with `labels=prepared.labels()`.
- `MalformedRequestError` raised by `prepared.apply` gives 400 `malformed_request` with its
  position message, like one raised by `prepare_request`. Any other exception from
  pseudonymisation still gives 500 `pseudonymization_failed`. Nothing is forwarded in either
  case.
- The unresolved count of a non-streamed JSON answer covers keys as well as string values.

### Architecture (`docs/architecture.md`)

- §3.2: the ML layer runs on text slots only, not on label slots.
- §4.1: the new signatures, what `contextual` and `labels` mean, and that propagation and
  linking reach label slots.
- §6: `Slot.label` and `PreparedRequest.labels()`; in the slot order, "inside tool-call
  arguments, tool inputs, input examples and metadata, each key comes right before its value".
- §6.1: the tool-call arguments row says "every key and string value"; the input examples and
  metadata rows say their keys are pseudonymised. A new row says that other keys (schema
  property names, keywords, message fields) are never changed and are covered by the audit.
- §7: the proxy passes the labels, and a key collision at `apply` gives 400.
- §8: the audit row says "every string value and every key anywhere in the outbound body".

### Documentation

- `docs/usage.md`, "Fields forwarded unchanged": the keys of tool-call arguments, tool inputs,
  input examples and metadata are pseudonymised, and the ML layer does not read keys. Forwarded
  unchanged are schema property names and the keys of other objects; a detected value in one of
  them makes the leak audit reject the request. The error table adds the key collision to
  `malformed_request`.
- `docs/threat-model.md`, residual risk 9: object keys move from "forwarded" to "pseudonymised
  where they hold data". The audit now covers keys, so the sentence about the audit becomes
  true. With the ML layer on, a name that only that layer would find in a key or a label is not
  found; this is a new, documented case of residual risk 1.
- Slices 05, 08, 09 and 10: a short "Changed in slice 21" note where they say keys are never
  slots, that ML runs on every slot, or that the audit does not check keys.
- `CHANGELOG.md`, under `Unreleased`:
  - `Changed`: keys of tool-call arguments, tool inputs, input examples and metadata are
    pseudonymised, which renumbers requests whose keys hold detected values; the ML layer does
    not read keys; a key collision is rejected with 400.
  - `Fixed`: a detected value in a key left the gateway unchanged, because the audit did not
    check keys; and non-streamed Anthropic answers returned placeholders in `tool_use` input
    keys.
- Backlog §10: the paragraph about slices 21 and 22, added with this spec, still describes what
  was built; correct it where the implementation differs.

## Tests

Synthetic values only: `Juan Pérez` and `ACME Corporation` from the proxy dictionary,
`Alice Johnson` and `juan.perez@example.com` from the fixtures, `maria.lopez@example.org`, and
`Ana Ruiz` for the fake contextual detector. Write non-ASCII characters with `\u` escapes.

### Changed expectations (decision 1; the pull request names each)

`test_openai_chat.py`

- `test_slot_order_is_messages_then_prediction_then_tools_then_request_fields`: after
  `assistant refusal` come `to`, `to argument`, `cc`, `cc argument`; after `tool result part`
  come `query`, `function call argument`; and the request fields end with `stop text`, `ticket`,
  `T-0001`, `cache key`.
- `test_with_tool_definitions_off_no_tool_slot_exists`: the last five texts are
  `prediction text`, `stop text`, `ticket`, `T-0001`, `cache key`.
- `test_fields_outside_the_slots_are_never_touched`: it no longer expects metadata keys
  unchanged. It expects the metadata key to hold its slot's text, and the argument keys too.
- `test_metadata_values_are_slots_in_key_order_and_keys_are_kept` becomes
  `test_metadata_keys_and_values_are_slots_in_key_order`: texts `b`, `second`, `a`, `first`.
  `apply` renames the keys and keeps their order.
- `test_request_fields_come_after_tool_definitions_in_a_fixed_order`: `text`, `tool`, `stop`,
  `m`, `meta`, `key`.
- `test_request_fields_are_slots_even_with_tool_definitions_off`: `s`, `m`, `v`.
- `test_assistant_tool_call_arguments_become_slots_and_are_reserialised_compactly`: texts
  `to`, `juan.perez@example.com`, `cc`, `a@example.org`.
- `test_escaped_strings_in_arguments_are_decoded_before_detection`: the key `name` comes
  first.
- `test_legacy_function_call_arguments_are_treated_like_tool_call_arguments`: the key `who`
  comes first.

`test_anthropic_messages.py`

- `test_slot_order_is_system_then_messages_then_tools`: after `Sending the email now.` come
  `to`, `juan.perez@example.com`, `body`, `greeting`, `Dear Juan Pérez`, `lines`, `Hello`,
  `Regards`, `urgent`, `copies`.
- `test_tool_use_input_strings_are_slots_in_depth_first_order_and_replaced_in_place`: the same
  ten texts at indexes 4 to 13. After `apply`, the keys hold the new texts and keep their
  order, and `true` and `2` are unchanged.
- `test_tool_use_keys_are_never_slots` becomes
  `test_tool_use_keys_are_label_slots_before_their_values`: for
  `{"Alice Johnson": "x", "y": {}}` the texts are `Alice Johnson`, `x`, `y`, and the labels
  are `{0, 2}`.
- `test_tool_result_with_a_list_of_text_blocks_gives_a_slot_per_block`: the same three texts,
  at indexes 14 to 16.
- `test_with_tool_definitions_off_no_tool_slot_exists`: 18 texts.
- `test_examples_schema_descriptions_and_stop_sequences_follow_the_tool_descriptions`: the
  example gives `to`, `maria.lopez@example.org`, `n`, `cc`, `a`, `b`.
- `test_a_description_inside_input_examples_is_one_slot_among_the_tool_descriptions`: `first`,
  `example description`, `second`, `description`, `to`, `to`.

`test_base.py`

- `test_restore_all_strings_restores_nested_strings_and_leaves_other_values_alone`: the key
  `[PERSON_1]` is restored to `Juan Pérez`.

`test_audit.py`

- `test_keys_are_not_checked` becomes `test_a_detected_value_in_a_key_is_found`: the same body
  reports 2 leaked entities.

### New tests

`test_base.py`

- `slots_for_keys_and_strings` gives keys before values, depth-first, and marks only key slots
  as labels. `skip_under` skips the value and keeps the key. Applying the texts unchanged
  changes nothing.
- Renaming keeps the key order, and leaves untouched an object whose keys did not change. Two
  keys that become equal raise `MalformedRequestError` naming `where`, and its message holds
  neither key.
- `restore_all_strings` does not merge keys. `{"[PERSON_1]": 1, "Juan Pérez": 2}` keeps both
  keys as they are, and `{"[PERSON_1]": 1, "[person_1]": 2}` restores the first key and keeps
  the second.
- `PreparedRequest.labels()` gives the indexes of the label slots.
- property: for any JSON object of Hypothesis text keys and values nested up to three levels,
  applying the texts unchanged leaves it equal. Replacing every slot with `[SLOT_n]` leaves none
  of its keys or strings, keeps the shape and the key order, and raises nothing.

`test_engine.py`

- A fake contextual detector that finds `Ana Ruiz` runs on text slots and not on label slots.
  A label `Ana Ruiz` alone is not replaced. With `Ana Ruiz` in a text slot too, the label is
  replaced by propagation with the same placeholder.
- The dictionary and the rules run on label slots: a label `maria.lopez@example.org` becomes
  `[EMAIL_1]`.
- A label outside the range raises `ValueError`. With no labels the output is the same as
  before for the existing engine tests (they pass unchanged).
- `detectors` lists the literal detector, then `detectors`, then `contextual`.
- `analyze` with the same labels gives the spans `pseudonymize` replaces.

`test_factory.py`

- With ML enabled (the `loaded` fixture, which replaces `load_gliner` with a fake), the ML
  detector is contextual: a label that the fake model would detect is not replaced, the same
  text in a text slot is, and `detectors` keeps the order literal, dictionary, rules, ML.

`test_openai_chat.py` and `test_anthropic_messages.py`

- Argument keys, `tool_use` input keys, example keys and metadata keys are label slots, and no
  other slot is.
- Two argument keys that become the same raise `MalformedRequestError` at `apply`, naming the
  arguments' position.
- Anthropic `restore_response` restores a `tool_use` input key.

`test_anthropic_stream.py`

- A `content_block_start` whose `tool_use` input has a key `[PERSON_1]` comes out with
  `Juan Pérez`, and an unknown placeholder in a key is counted as unresolved.

`test_audit.py`

- A key holding the folded form of an entity (`STRASSE` for `Straße`) is found, and a key
  shorter than the minimum length is skipped, as for values.

Proxy tests (`test_app.py`, `test_app_anthropic.py`)

- OpenAI: the text names `Juan Pérez`, `metadata` is
  `{"Juan Pérez": "vip", "maria.lopez@example.org": "x"}`, and an assistant tool call has
  arguments `{"attendees": {"Juan Pérez": "accepted"}}`. The request answers 200, and the
  upstream body holds `[PERSON_1]` and `[EMAIL_n]` keys and no dictionary value.
- Anthropic: the same with a `tool_use` input and an `input_examples` key. A non-streamed
  answer whose `tool_use` input has a key `[PERSON_1]` reaches the client with `Juan Pérez`.
- A tool schema whose property name is `Juan Pérez`, not listed in `required`, with the text
  naming him, answers 500 `leak_audit_failed`.
- With settings whose dictionary also holds the term `ACME` (type `ORG`),
  `metadata: {"ACME": "a", "Acme": "b"}` answers 400 `malformed_request`: both keys become
  `[ORG_1]` (checked in the prototype). Nothing is forwarded, and the error message holds
  neither key.
- The engine receives the labels: with a fake contextual detector that finds `owner`, a
  `tool_use` input key `owner` is forwarded unchanged.

`test_invariants.py`

- The logging hygiene test also sends a request whose keys hold the dictionary values, and
  checks that no log line or metric holds them.

Evaluation: every gate in `tests/eval` passes unchanged, because no evaluation text has
labels.

Mutation: in turn, make a key not a slot, mark key slots as text, run contextual detectors on
labels, drop the key renaming, merge colliding keys, stop restoring keys, stop auditing keys,
stop counting unresolved placeholders in keys, and map the collision to 500. Confirm that a
test fails each time.

## Acceptance checklist

- [ ] Every bold row of the reproduction table now behaves as the prototype column says, on
      both routes, `count_tokens` included.
- [ ] The audit checks every key; a detected value in a key that is not a slot gives 500
      `leak_audit_failed`.
- [ ] Contextual detectors never run on label slots, and propagation and linking reach them.
- [ ] A request whose keys hold no detected value is forwarded exactly as before.
- [ ] Every pre-existing test passes unchanged, except the expectations listed under Tests.
- [ ] Key collisions are rejected with 400 and a message that holds no key.
- [ ] The mutation checks are done and recorded in the pull request.
- [ ] Architecture, usage, threat model, slices 05, 08, 09 and 10, backlog §10 and CHANGELOG
      are updated in the same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Schema values (`enum`, `const`, `default`, `examples`, `title`, `$comment`): slice 22.
- Schema property names, `required`, `$defs` names and `$ref` paths. Renaming them would mean
  renaming tool-call keys in answers to match, and a detected value there is rejected by the
  audit.
- The keys of other objects (message fields, keywords, provider options).
- Marking other slots as labels (message names, stop sequences, tool-call argument values).
  Their behaviour does not change.
- An option to forward a field unchanged (backlog §10).
- Changes to the detectors, the mapping, the configuration or the evaluation corpora.
