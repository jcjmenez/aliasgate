# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, a minor version may
change the configuration or the command line.

## [Unreleased]

### Changed

- The keys of tool-call arguments (OpenAI `arguments`, Anthropic `tool_use` inputs), Anthropic
  tool `input_examples` and OpenAI `metadata` are pseudonymised, each key numbered right before
  its value. This renumbers requests whose keys hold detected values; a request whose keys hold
  none is forwarded as before.
- The ML layer does not read these keys: it took ordinary field names (`owner`, `company`) for
  names. The dictionary and the rules read them, and propagation and name linking reach them,
  so a name detected in the text is replaced in a key too.
- Two keys of one object that would become the same placeholder (`ACME` and `Acme`) are
  rejected with 400 `malformed_request`, naming the object's position and no key.

### Fixed

- A detected value in a key left the gateway unchanged, because the leak audit did not check
  keys: `{"Juan Pérez": "accepted"}` as an OpenAI `metadata` or Anthropic `tool_use` input or
  `input_examples` key. The audit now searches every key, so a detected value in a key the
  gateway does not change (a schema property name) rejects the request with
  `leak_audit_failed`.
- Non-streamed Anthropic answers returned placeholders in `tool_use` input keys to the client.
  They are restored now, as streamed answers and OpenAI answers already were.

## [0.7.0] - 2026-11-24

Request fields that 0.6.0 forwarded without reading. Message names, metadata values, stop
sequences, the cache key and output schema descriptions are pseudonymised, and the end user's
location is no longer given to hosted web search unless `policy.strip_user_location` is off.

### Added

- `policy.strip_user_location` (`ALIASGATE_POLICY__STRIP_USER_LOCATION`, on by default) removes
  the end user's approximate location given to hosted web search: `user_location` in OpenAI
  `web_search_options` and in Anthropic's web search tools. `aliasgate check` prints
  `strip_user_location=on` or `off`.

### Changed

- The end user's location is no longer forwarded by default, so hosted web search is not
  localised; `web_search_options` is kept.
- A message `name` that holds a detected value is numbered before the message's content, and
  the new slots of stop sequences, metadata values and the cache key after the tool
  definitions, which changes the numbering of such requests. A request without these fields,
  or whose fields hold no detected value, is pseudonymised as before.

### Fixed

- Request fields that carry text were forwarded unchanged when a value appeared only there,
  and made the leak audit reject the whole request when a value detected elsewhere appeared
  there too (a stop sequence naming a person in the conversation): OpenAI `messages[].name`,
  `metadata` values, `prompt_cache_key` and `stop`, Anthropic `stop_sequences`, the
  descriptions of output schemas (OpenAI `response_format`, Anthropic `output_config`) and the
  strings of Anthropic tool `input_examples`. They are pseudonymised now, and the
  `stop_sequence` an Anthropic answer repeats is restored.

## [0.6.0] - 2026-11-24

Shorter forms of an organisation's name. The name without its legal form and each distinctive
word on its own, which 0.5.0 forwarded unchanged, are replaced as variants of the organisation.

### Added

- An organisation mentions corpus for the evaluation harness (`tests/eval/corpus/orgs.*.jsonl`,
  180 examples per language in 9 categories, written by `tools/generate_org_corpus.py`, with a
  dictionary of its full names loaded by `orgs.yaml`): full names with their legal form, the
  name without it, distinctive words alone and in lower case, two organisations with the same
  generic word, a generic word on its own, and a distinctive word that is also a common word.
  Its baseline in `docs/evaluation.md` shows that when a dictionary finds an organisation's full
  name, every shorter form of it is still forwarded unchanged.

### Changed

- A shorter form of an organisation's name that a detector found (`Brightwater` or
  `Brightwater Logistics` after `Brightwater Logistics Ltd`) is now a variant of the full name
  (`[ORG_1_B]`) instead of getting a number of its own, which changes the numbering.
  `detection.org_tokens: false` (`ALIASGATE_DETECTION__ORG_TOKENS=false`) turns organisation
  name linking off, and `aliasgate check` prints `org tokens: on` or `off`.

### Fixed

- The shorter forms of a detected organisation's name were forwarded unchanged: each
  distinctive word standing alone (`Brightwater`, `Ventisquero`, `Ledesma` after
  `Albarrán y Ledesma S.A.`, in lower case too) and the name without its legal form
  (`Brightwater Logistics`). Legal forms, connectors, generic words (`Logistics`, `Grupo`) and
  common words (`Stone`) never stand for an organisation on their own, and a word shared with a
  person goes to the person.

## [0.5.0] - 2026-11-23

More forms of a person's name. Lower-case, one-word, hyphenated and `Don` forms that 0.4.0
forwarded unchanged are replaced as variants of the person, and `Don't` no longer matches the
term `Don`.

### Added

- Three categories in the person corpus (`single`, `hyphen`, `don`, 60 examples per language
  appended after the existing ones, which are unchanged): a surname after a title or an
  initial, one part of a hyphenated name, and the English first name `Don` next to `Don't`
  and the Spanish title.

### Changed

- `Don't`, `can't` and `won't` no longer match the terms `Don`, `Can` and `Won` in
  propagation, the dictionary and the leak audit: a whole term is never followed by `'t` or
  `’t` that ends a word. `and` is no longer a name word.

### Fixed

- With person name linking on, these shorter forms of a detected name were forwarded
  unchanged: a lower-case shorter form (`thanks larry`), unless the word is on a fixed list of
  633 common English and Spanish words; the surname after a title or an initial (`Holloway`
  after `Mr. J. R. Holloway`, `Pérez` after `Sr. Pérez`); one part of a hyphenated name
  (`Anne` for `Anne-Marie Fowler`); and the English first name `Don` (`Don` and `Draper` after
  `Don Draper`). They are now replaced as variants of the person.

## [0.4.0] - 2026-11-22

Person name linking. The shorter forms of a detected person's name, which 0.3.0 forwarded
unchanged, are replaced as variants of that person, which changes the numbering.

### Added

- Link scores in the evaluation harness. Gold spans may name their entity with an optional
  `entity` id. The harness then counts, per type, pairs of mentions of one entity that share a
  placeholder number (`link_recall`) and pairs of mentions of two entities that share one
  (wrong merges, in `link_precision`). The variant letter is ignored. The table gains a linking
  table, JSON gains `linking`, `wrong_merge_pairs` and `wrong_merges`, and
  `--show-wrong-merges N` lists wrong merges by example id and offsets. Entity ids are never
  printed.
- A synthetic person corpus (`tests/eval/corpus/persons.*.jsonl`, 280 examples per language in
  14 categories) with full names and the shorter forms people use, its generator
  `tools/generate_person_corpus.py`, and a dictionary of its full names for CI. Its baseline
  shows that every shorter form of a detected full name (a first name, a surname, a title with
  a surname) is forwarded unchanged today.

### Changed

- A shorter form of a person's name that a detector found (`Pérez` or `Juan Pérez` after
  `Juan Pérez García`) is now a variant of the full name (`[PERSON_1_B]`) instead of getting a
  number of its own, which changes the numbering. `detection.person_tokens: false` turns person
  name linking off.

### Fixed

- Shorter forms of a detected person's name, such as a first name, a surname or a title with a
  surname (`Alice`, `Johnson`, `Ms. Johnson`, `Sr. Pérez` after `Alice Johnson` or
  `Juan Pérez García`), were forwarded unchanged. They are now replaced as variants of the
  person where they start with an upper-case letter (`detection.person_tokens`, on by default).
  On the person corpus with a dictionary of full names, the leak rate fell from 0.3625 to
  0.0425 in English and from 0.2735 to 0.0339 in Spanish.

## [0.3.0] - 2026-11-21

Rule coverage gaps. The rules find identifiers that 0.2.0 forwarded unchanged, and with `URL`
enabled a URL is replaced whole.

### Changed

- A US SSN whose nine digits also form a valid Spanish number (`782-77-8477`) is replaced as
  `NATIONAL_ID`, not `PHONE`: the SSN rule's score is now 0.95, above the phone score.
- With `URL` enabled, a URL is replaced whole even when an email, a token, credentials or
  another rule value lies inside it. Until now the inner value was replaced and the rest of the
  URL was forwarded.

### Fixed

Values that the rules missed and forwarded unchanged:

- Spanish DNI, NIE and CIF with the digits grouped by dots (`12.345.678-Z`, `X-1.234.567-L`,
  `B-58.818.501`), and a CIF with a hyphen or a space after its letter (`B-58818501`).
- Email addresses with letters outside ASCII (`josé@example.com`, `juan@bücher.example`) or an
  apostrophe in the local part (`o'connor@example.com`, of which only `connor@example.com` was
  replaced).
- A US SSN without hyphens (`078051120`, `078 05 1120`) right after the keyword `SSN`, `SSNs`,
  `SS#` or `social security`.
- A PEM private key pasted without its END line, now replaced up to its last base64 line.

## [0.2.0] - 2026-11-21

Dictionary aliases. A dictionary without aliases gives the same placeholders and answers as
0.1.0.

### Added

- Dictionary aliases: an entry may list other names under `aliases`. The term and its aliases
  share one entity number and get variant placeholders in order of appearance (`[ORG_1]`,
  `[ORG_1_B]`, ...), each restored as written. Without aliases, the output is unchanged.
  Restoration also accepts the variant forms (`[org_1 b]`, `[ORG\_1\_B]`).
- Library: `Span.link`, `Entity.variant`, the `link` argument of `Mapping.assign`, the `variant`
  arguments of `Mapping.lookup` and `format_placeholder`, and `Term.aliases`.

### Changed

- Library: `parse_match` returns `(type, number, variant)` instead of `(type, number)`.

## [0.1.0] - 2026-11-20

First release. Alpha: the gateway works end to end and its safety properties are tested, but
it has not run in production yet. Read the threat model before relying on it.

### Added

- Gateway for OpenAI Chat Completions (`POST /v1/chat/completions`) and Anthropic Messages
  (`POST /v1/messages`, `POST /v1/messages/count_tokens`), whole and streamed, with tool calls.
  Upstreams may have a base path, for Azure OpenAI and Claude in Microsoft Foundry.
- Detection in layers: placeholder literals already in the text, company dictionaries, rules
  with checksum validation (email, phone, IBAN, card, Spanish DNI/NIE and US SSN, Spanish CIF,
  IP address, secrets, optional URL), an optional local GLiNER model (`ml` extra), an allow
  list, and propagation of detected values to their other occurrences.
- Typed, numbered placeholders (`[TYPE_N]`), deterministic per request and stable when a
  conversation grows; restoration that tolerates common model alterations, including
  placeholders split across streamed chunks.
- Fail-closed policy: unknown endpoints and uninspectable content (images, audio, files) are
  rejected by default, end-user identifiers are removed, and a leak audit rejects any request
  whose outbound body still holds a detected value.
- Prometheus metrics, JSON logs without text, and the `x-aliasgate-request-id` and
  `x-aliasgate-entities` response headers.
- `aliasgate` command: `serve`, `check`, `demo` and `version`.
- Evaluation harness, `python -m aliasgate.eval`, with synthetic English and Spanish corpora
  and a recall gate in CI.
- Docker images `ghcr.io/jcjmenez/aliasgate:0.1.0` and `:0.1.0-ml` (model baked in, runs
  offline), non-root, with no configuration inside.
- Documentation: usage, deployment, architecture, threat model, evaluation.

[Unreleased]: https://github.com/jcjmenez/aliasgate/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/jcjmenez/aliasgate/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/jcjmenez/aliasgate/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/jcjmenez/aliasgate/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/jcjmenez/aliasgate/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jcjmenez/aliasgate/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jcjmenez/aliasgate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jcjmenez/aliasgate/releases/tag/v0.1.0
