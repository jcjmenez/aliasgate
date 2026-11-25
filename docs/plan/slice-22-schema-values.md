# Slice 22: schema values

## Goal

The values written inside tool and output schemas are pseudonymised: `enum`, `const`,
`default`, `examples`, `title` and `$comment`. This closes what remains of backlog §10 after
[slice 21](slice-21-object-keys.md), apart from the option to forward a field unchanged.

Today a value that appears only in a schema reaches the provider unchanged. A value detected
elsewhere in the request makes the leak audit reject the request, as with
`enum: ["Juan Pérez", "Ana Ruiz"]` when the text names Juan Pérez. Since slice 20, an Anthropic
tool whose `input_examples` repeat a detected `enum` value is rejected even when the text never
names it. The example is pseudonymised and the `enum` is not, so the audit finds the value in
the `enum`.

The values of `enum`, `const`, `default` and `examples` become label slots (slice 21). They are
detected by the dictionary and the rules, and replaced by propagation and linking, but not read
by the ML layer. `title` and `$comment` are prose, like `description`, and become ordinary text
slots. The model then sees and emits placeholders, which the answer restores as it restores
every tool call. A request whose schemas hold no detected value is forwarded exactly as before.

## Depends on

Slice 21. Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-25)

1. **Every schema value is read, `enum` and `const` included, under `policy.tool_definitions`.**
   `enum`, `const`, `default` and `examples` are values: label slots, without the ML layer.
   `title` and `$comment` are text, like `description`. The model emits the placeholder and the
   answer restores it, so strict decoding still yields a value of the client's `enum`. Rejected:
   leaving `enum` and `const` exact, which keeps the audit rejections above and leaves
   `input_examples` inconsistent with their schema. Also rejected: running the ML layer on
   `enum` values. It took `admin`, `viewer` and `Engineering` for entities, and the model could
   no longer tell `[PERSON_2]` from `[PERSON_3]`.
2. **Label slots come from slice 21.** This slice adds no engine change.
3. **Two slices.** Keys and label slots came first (slice 21). The option to forward a field
   unchanged stays in backlog §10.

## Decisions made while writing this spec

These follow from the decisions above or from the existing contracts. They are binding.

- **The walk knows JSON Schema.** It starts at each schema root and follows only the keywords
  that hold subschemas. A property named `enum`, `title` or `default` is a property, not a
  keyword. A walk on key names alone would make its subschema's `type` and `pattern` slots.
- **What is never a slot:** property names, `required`, `type`, `format`, `pattern`, `$ref`,
  `$id`, `$schema`, `$anchor`, `contentEncoding`, `contentMediaType`, numbers, booleans and
  `null`, and any key inside a value. The audit still checks all of them (slice 21).
- **Strings under `description` are not slots again.** Since slices 05 and 08, every
  `description` string anywhere in the tool definitions is already a slot. Inside a `default`,
  `examples`, `enum` or `const` value, such a string is skipped. A `description` keyword is
  never walked twice.
- **Slot order: after every slot the tool definitions have today.** OpenAI: descriptions in
  `tools`, then in `functions`, then in `response_format` (as today), then the schema values of
  each `tools[i].function.parameters` in order, of each `functions[i].parameters` in order,
  then of `response_format.json_schema.schema`. Anthropic: tool descriptions, input examples
  and output schema descriptions (as today), then the schema values of each
  `tools[i].input_schema` in order, then of `output_config.format.schema`. The request fields
  stay last, so the slots of v0.7.0 stay a prefix of the tool definitions' slots.
- **A schema of the wrong shape is skipped, never an error.** The provider validates schemas,
  and a boolean schema (`true`) is valid JSON Schema. A non-object under a subschema keyword
  holds no text to miss. A `title` or `$comment` that is not a string is left as it is.
- **Equal placeholders are accepted.** Two `enum` values with the same key (`ACME`, `Acme`)
  both become `[ORG_1]`, and both are restored as the first form (architecture §4.2). The
  provider may reject an `enum` with duplicate items. Nothing leaks.
- **No new setting.** `policy.tool_definitions` covers schema values. Its documentation names
  them.

## Found while specifying (prototype of 2026-11-25, outside the repository, synthetic data)

Provider documents read on 2026-11-25:

- OpenAI's structured outputs guide (developers.openai.com) uses `enum` throughout and says
  enum values follow the schema supplied. It limits the number and the total length of `enum`
  values and of property names, and says nothing about duplicate `enum` items.
- Anthropic's structured outputs page (platform.claude.com) supports `enum`, `const` and
  `default` (with strings, numbers, booleans or nulls only) and returns 400 for unsupported
  features. It does not guarantee the capitalisation of string `enum` and `const` values,
  which the tolerant placeholder pattern absorbs.
- Anthropic's tool definition page says each input example must be valid against the tool's
  `input_schema` and that the API usually returns 400 otherwise.

Parts of the first two pages were read through a summary. The optional manual check below
covers what matters here.

Reproduction through the gateway: dictionary with `Juan Pérez` and `ACME Corporation`, default
rules, mocked upstream.

| Case | v0.7.0 | Prototype |
|---|---|---|
| OpenAI tool `enum: ["Juan Pérez", "Ana Ruiz"]`, text without him | forwarded | `["[PERSON_1]", "Ana Ruiz"]` |
| the same, text naming him | 500 `leak_audit_failed` | `["[PERSON_1]", "Ana Ruiz"]` |
| `const`, `default` or `examples` holding `maria.lopez@example.org` | forwarded | `[EMAIL_1]` |
| `title` or `$comment` holding the email in a sentence | forwarded | `Mail [EMAIL_1]` |
| OpenAI `response_format` schema `enum: ["Juan Pérez"]`, text naming him | 500 `leak_audit_failed` | `["[PERSON_1]"]` |
| Anthropic `input_schema` `enum: ["Juan Pérez"]`, text naming him | 500 `leak_audit_failed` | `["[PERSON_1]"]` |
| Anthropic `enum: ["Juan Pérez", "Ana Ruiz"]` with `input_examples: [{"owner": "Juan Pérez"}]` | 500 `leak_audit_failed` | both `[PERSON_1]` |
| Anthropic `output_config` schema `enum: ["Juan Pérez"]`, text naming him | 500 `leak_audit_failed` | `["[PERSON_1]"]` |

**With the ML layer** (GLiNER, default model), a tool with `enum: ["admin", "viewer",
"Engineering"]` and `enum: ["Ana Ruiz", "Luis Gil"]` was measured three ways:

- With text `Crea el usuario.`, both `enum`s are forwarded unchanged with label slots. Without
  label slots, all five values became placeholders.
- When a `tool_use` input in the history holds `"role": "admin"`, that value is an ordinary text
  slot. The ML layer takes it for a person, as it does today, and propagation replaces it in the
  `enum` too: `["[PERSON_2]", "viewer", "Engineering"]`. The round trip still holds. The model
  loses the word, which today it loses in the history alone. Under v0.7.0 the audit rejects
  such a request, since the `enum` keeps the word the ML layer detected.
- With text `Asigna el ticket a Ana Ruiz.`, the second `enum` becomes
  `["[PERSON_1]", "Luis Gil"]`. `Luis Gil` appears only in the label, which the ML layer does not
  read, so it is forwarded. This is a case of residual risk 1, documented in slice 21.

**Test changes.** With the prototype applied on its own, 7 of the 2394 tests changed, all of
them expectations of decision 1. They are the OpenAI and Anthropic slot-order and
tool-description tests, the OpenAI test of fields outside the slots, the OpenAI
`response_format` test, and the Anthropic end-to-end test that expects the fixture's `enum`
value `Alice Johnson` to stay. Three of them also change in slice 21. They are listed under
Tests.

**Found, not fixed here.** A structured output answer is JSON text: OpenAI `message.content`
with a `json_schema` `response_format`, or an Anthropic text block under `output_config`. It is
restored with plain `restore`, not with JSON escaping. A surface holding `"` or `\`
(`Ana "La Jefa" Ruiz`) then yields invalid JSON, on both routes. This predates this slice, but
placeholders in `enum` values make it likelier to show. The dialects' `restore_response` does
not know the request's output format, so a fix changes that contract. It is recorded in
backlog §10 as its own item.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/dialects/base.py` | change: `slots_for_schema_values` |
| `src/aliasgate/dialects/openai_chat.py` | change: schema value slots |
| `src/aliasgate/dialects/anthropic_messages.py` | change: schema value slots |
| `tests/dialects/test_base.py`, `tests/dialects/test_openai_chat.py`, `tests/dialects/test_anthropic_messages.py` | change |
| `tests/proxy/test_app.py`, `tests/proxy/test_app_anthropic.py` | change |
| `docs/architecture.md` | change: §6 slot order, §6.1 table |
| `docs/usage.md` | change: "Fields forwarded unchanged", `leak_audit_failed` example |
| `docs/threat-model.md` | change: residual risk 9 |
| `examples/aliasgate.example.yaml` | change: the `tool_definitions` comment |
| `docs/plan/slice-05-openai-chat-dialect.md`, `docs/plan/slice-08-anthropic-dialect.md`, `docs/plan/slice-20-request-fields.md` | change: a "Changed in slice 22" note each |
| `docs/plan/README.md`, `docs/plan/backlog.md`, `README.md` | change: status, backlog §10 |
| `CHANGELOG.md` | change: `Unreleased` entries |

The engine, the detectors, the audit, the proxy, the mapping, the configuration and the
evaluation corpora do not change.

## Specification

Read slices 05, 08, 20 and 21 and architecture §6 before starting.

### The schema walk (`base.py`)

```python
def slots_for_schema_values(schema: object) -> list[Slot]: ...
```

The function returns the slots of one JSON Schema. It walks iteratively, depth-first, and reads
each schema object's keys in insertion order. For each key of a schema object:

| Key | Value | Slots |
|---|---|---|
| `title`, `$comment` | a string | one text slot |
| `enum`, `const`, `default`, `examples` | a string | one label slot |
| `enum`, `const`, `default`, `examples` | an object or an array | a label slot for every string value inside, depth-first, except strings stored directly under a `description` key; keys are never slots |
| `properties`, `patternProperties`, `$defs`, `definitions`, `dependentSchemas` | an object | walk each value, in key order, as a schema |
| `items`, `additionalItems`, `additionalProperties`, `unevaluatedItems`, `unevaluatedProperties`, `contains`, `propertyNames`, `not`, `if`, `then`, `else` | an object | walk it as a schema |
| `items`, `prefixItems`, `allOf`, `anyOf`, `oneOf` | an array | walk each item, in order, as a schema |
| anything else, or a value of another type | | none |

`schema` itself, and any subschema, that is not an object gives no slot. The docstring says
that keys and structural keywords are never slots.

### OpenAI Chat Completions (`openai_chat.py`)

With `policy.tool_definitions`, after the `description` slots of `tools`, `functions` and
`response_format`, these come in order:

1. for each entry of `tools` that is an object whose `function` is an object: the schema value
   slots of `function.parameters`;
2. for each entry of `functions` that is an object: those of its `parameters`;
3. when `response_format.json_schema` is an object: those of its `schema`.

Custom tools have no `parameters` and give none. The response side does not change: an `enum`
value the model emits in tool-call arguments or in content is a placeholder like any other.

### Anthropic Messages (`anthropic_messages.py`)

With `policy.tool_definitions`, after the tool descriptions, the input examples and the
`output_config.format` descriptions:

1. for each entry of `tools` that is an object: the schema value slots of its `input_schema`
   (server tools have none);
2. when `output_config.format` is an object: those of its `schema`.

`count_tokens` uses the same `prepare_request`. The response side does not change.

### Architecture (`docs/architecture.md`)

- §6, slot order: "then tool definitions, their input examples, output schemas and schema
  values". Keep the reason the tool definitions come after the conversation.
- §6.1: the tool definitions row says that the `description`, `title` and `$comment` strings,
  and as label slots the `enum`, `const`, `default` and `examples` values, are pseudonymised
  with policy `tool_definitions`. Names, property names and every other keyword are never
  changed. The output schemas row says the same for `response_format` and `output_config`.

### Documentation

- `docs/usage.md`, "Fields forwarded unchanged": schema values move from forwarded to
  pseudonymised. Add a short note that a model constrained to an `enum` emits the placeholder,
  which the answer restores, and that two `enum` values differing only in case become one
  placeholder. The `leak_audit_failed` row needs a new example, since a schema `enum` no longer
  causes it. Use a schema property name holding a detected value.
- `docs/threat-model.md`, residual risk 9: schema values move to the pseudonymised list. Still
  forwarded: tool and function names, property names, ids, and the keys of objects that are
  not data.
- `examples/aliasgate.example.yaml`: the `tool_definitions` comment names schema values.
- Slices 05, 08 and 20: a "Changed in slice 22" note where they say `enum`, `const`,
  `default`, `examples` and `title` are never changed (slice 20, decision 4).
- `CHANGELOG.md`, under `Unreleased`:
  - `Changed`: the `enum`, `const`, `default`, `examples`, `title` and `$comment` values of tool
    and output schemas are pseudonymised under `policy.tool_definitions`, which renumbers
    requests whose schemas hold detected values.
  - `Fixed`: a schema value holding a value detected elsewhere made the leak audit reject the
    request, including every Anthropic tool whose `input_examples` repeat an `enum` value; and
    a value that appeared only in a schema was forwarded unchanged.
- Backlog §10: the paragraphs added with this spec (slices 21 and 22, and structured output
  answers restored without JSON escaping) still describe what was built; correct them where the
  implementation differs.

## Tests

Synthetic values only: `Juan Pérez` and `ACME Corporation` from the proxy dictionary,
`Alice Johnson` from the fixtures, `maria.lopez@example.org`, `Ana Ruiz`, and plain words
(`admin`, `viewer`). Write non-ASCII characters with `\u` escapes.

### Changed expectations (decision 1; the pull request names each)

These are stated on top of slice 21's expectations.

`test_openai_chat.py`

- `test_slot_order_is_messages_then_prediction_then_tools_then_request_fields`: `enum value`
  comes right after `response format description`.
- `test_tool_descriptions_are_slots_and_names_enums_and_property_names_are_not` becomes
  `test_tool_descriptions_then_schema_values_are_slots_and_names_and_property_names_are_not`:
  the list ends with `enum value`.
- `test_fields_outside_the_slots_are_never_touched`: the property's `enum` holds its slot's
  text. Property names, `required`, `type`, `strict` and the custom tool's `format` are still
  unchanged.
- `test_response_format_descriptions_are_slots_at_any_depth`: texts `outer`, `inner`, `x`.
  After `apply`, the `enum` holds the third text.

`test_anthropic_messages.py`

- `test_slot_order_is_system_then_messages_then_tools`: `Alice Johnson` (the fixture's `enum`
  value) comes last.
- `test_tool_descriptions_are_slots_and_names_and_schema_property_names_are_not`: the last three
  texts are the tool description, the property description and `Alice Johnson`. After
  `apply`, the `enum` holds the third.
- `test_end_to_end_the_same_name_gets_one_placeholder_everywhere`: the `enum` becomes
  `["[PERSON_1]"]`, the placeholder of `Alice Johnson` in the system prompt.

### New tests

`test_base.py` (`slots_for_schema_values`)

- `title` and `$comment` are text slots. String `enum`, `const`, `default` and `examples`
  values are label slots. Non-string values are untouched: numbers, booleans, `null`, and a
  `title` of 5.
- Values inside `properties`, `patternProperties`, `$defs`, `definitions`, `dependentSchemas`,
  `items` (object and array forms), `prefixItems`, `additionalProperties`, `anyOf`, `allOf`,
  `oneOf`, `not`, `if`, `then`, `else`, `contains` and `propertyNames` are found, in
  depth-first key order.
- A property named `enum`, `title`, `default` or `description` is walked as a schema: its
  `type`, `pattern` and `format` are never slots.
- Inside a `default` object, a string under `description` is not a slot and a string under
  `title` is a label slot. Keys inside `default` and `examples` are never slots.
- A boolean schema, a `properties` that is a list, or a root that is not an object gives no
  slot and raises nothing.
- Deep nesting (10,000 levels of `items`) does not exhaust the stack.
- property: for any schema built by Hypothesis from the keywords above with text values,
  applying the texts unchanged leaves it equal. Replacing every slot with `[SLOT_n]` leaves no
  original string under `title`, `$comment`, `enum`, `const`, `default` or `examples`, and every
  key and every other keyword's value unchanged.

`test_openai_chat.py`

- Schema values of `tools`, `functions` and `response_format` come in that order, after every
  description and before `stop`. With `tool_definitions` off, none is a slot.
- A tool whose `function` is not an object, or has no `parameters`, gives no schema slot.
- The `enum` slots are labels, and the `title` slots are not.

`test_anthropic_messages.py`

- Schema values of each `input_schema`, then of `output_config.format.schema`, come after the
  output schema descriptions and before `stop_sequences`. With `tool_definitions` off, none is
  a slot.
- A server tool without `input_schema` gives none.
- An `enum` value repeated in `input_examples` gets the same placeholder in both places
  (end-to-end with the dialect test engine).

Proxy tests (`test_app.py`, `test_app_anthropic.py`)

- OpenAI: the text names `Juan Pérez`, and a strict function tool has
  `enum: ["Juan Pérez", "Ana Ruiz"]` and `default: "maria.lopez@example.org"`. The request
  answers 200, and the upstream holds `[PERSON_1]` and `[EMAIL_1]` in the schema. The
  upstream answer's tool call with arguments `{"owner": "[PERSON_1]"}` reaches the client as
  `{"owner": "Juan Pérez"}`.
- OpenAI: `response_format` with `enum: ["Juan Pérez"]` and the text naming him answers 200,
  with `[PERSON_1]` upstream.
- Anthropic: a tool with `enum: ["Juan Pérez", "Ana Ruiz"]` and
  `input_examples: [{"owner": "Juan Pérez"}]` answers 200, with `[PERSON_1]` in both upstream;
  the same through `count_tokens`; and an `output_config` schema `enum` the same way.
- With a fake contextual detector that finds `admin`, an `enum` value `admin` is forwarded
  unchanged. When a text slot also holds `admin`, the `enum` value gets its placeholder.

Evaluation: every gate in `tests/eval` passes unchanged.

Mutation: in turn, make `title` a label, make `enum` a text slot, drop each subschema keyword,
walk a property named `enum` as a value, take `description` strings inside values again, put
the schema slots before the descriptions, and read schemas with `tool_definitions` off. Confirm
that a test fails each time.

Manual check (optional, after the maintainer approves the spend, following the earlier
real-provider checks): through `aliasgate serve`, send one OpenAI-route request (Azure gpt-4.1)
with a strict function tool whose `enum` holds a dictionary name the text also names, and check
that the provider accepts the placeholder `enum` and the client gets the real name in the tool
call. Send one Anthropic-route request (Claude on Foundry) with a tool whose `enum` and
`input_examples` hold that name, and check that the provider accepts both. Record the result in
this file.

## Acceptance checklist

- [ ] Every row of the reproduction table behaves as the prototype column says, on both
      routes, `count_tokens` included.
- [ ] `enum`, `const`, `default` and `examples` strings are label slots; `title` and `$comment`
      are text slots; nothing structural is a slot.
- [ ] A request whose schemas hold no detected value is forwarded exactly as before.
- [ ] Every pre-existing test passes unchanged, except the expectations listed under Tests.
- [ ] The mutation checks are done and recorded in the pull request.
- [ ] Architecture, usage, threat model, example configuration, slices 05, 08 and 20,
      backlog §10 and CHANGELOG are updated in the same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Restoring structured output answers with JSON escaping (found above; backlog §10).
- Property names, `required`, `$defs` names and `$ref` paths (slice 21, out of scope).
- Schemas outside tool definitions and output formats, such as Anthropic server tool options
  (`allowed_domains`) or MCP server settings.
- An option to forward a field unchanged (backlog §10).
- Changes to the engine, the detectors, the audit, the proxy, the mapping, the configuration or
  the evaluation corpora.
