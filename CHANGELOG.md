# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, a minor version may
change the configuration or the command line.

## [Unreleased]

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
