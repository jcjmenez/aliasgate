# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, a minor version may
change the configuration or the command line.

## [Unreleased]

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

[Unreleased]: https://github.com/jcjmenez/aliasgate/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jcjmenez/aliasgate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jcjmenez/aliasgate/releases/tag/v0.1.0
