# aliasgate

[![CI](https://github.com/jcjmenez/aliasgate/actions/workflows/ci.yml/badge.svg)](https://github.com/jcjmenez/aliasgate/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

A self-hosted gateway that sits between your users and an LLM API. It replaces customer data
with placeholders before the request leaves your network and puts the real values back in the
answer. **The model never sees real data. The user never sees placeholders.**

```text
 user / app                    aliasgate                         LLM provider
 ───────────                   ─────────                         ────────────
 "Email Juan Pérez      ──▶    detect + replace      ──▶   "Email [PERSON_1]
  (juan@acme.es) about                                       ([EMAIL_1]) about
  the ACME contract"                                         the [ORG_1] contract"

 "Dear Juan Pérez, ..." ◀──    restore               ◀──   "Dear [PERSON_1], ..."
```

> **Status: pre-alpha.** The repository currently contains the research, the architecture and a
> detailed implementation plan. No gateway functionality is implemented yet. See the
> [roadmap](#roadmap).

## Why

Many companies forbid sending customer data to external LLMs, and their staff still need LLMs to
work on documents full of customer data. aliasgate is a technical control for that conflict: you
point your OpenAI or Anthropic client at the gateway instead of the provider, and nothing else
changes.

## Design in brief

- **Four detection layers.** Rules with checksum validation (emails, IBANs, cards, national IDs,
  phones, secrets), a company dictionary (your customers, projects, codenames), a pluggable local
  ML model for names, organisations and addresses, and propagation of every detected value to
  all its other occurrences.
- **Deterministic mapping.** Inside a request the same value always becomes the same placeholder,
  and placeholders are numbered so the model can tell entities apart.
- **Reversible, including streams and tool calls.** Placeholders split across streamed chunks are
  buffered and restored correctly. This is verified with property tests.
- **Nothing reversible at rest.** The placeholder-to-value mapping exists only in memory while a
  request is being served. There is no vault to steal.
- **Fail closed.** If detection fails, or the request carries content the gateway cannot inspect,
  the request is rejected instead of forwarded.
- **Self-hosted.** No data is sent anywhere except to the LLM provider you configure, and only
  after pseudonymisation.

## What it does not guarantee

No detector finds everything, and text without any name can still identify a person through
context. aliasgate greatly reduces the personal and confidential data that reaches a provider.
It does not make data anonymous and it does not, by itself, make a deployment compliant with any
regulation. Read the [threat model](docs/threat-model.md) before relying on it.

## Documentation

| Document | Content |
|---|---|
| [docs/state-of-the-art.md](docs/state-of-the-art.md) | Existing tools, detection models, research evidence, legal context, feasibility |
| [docs/architecture.md](docs/architecture.md) | Components, algorithms, data flow, decisions |
| [docs/threat-model.md](docs/threat-model.md) | What is protected, from whom, and the residual risks |
| [docs/plan/README.md](docs/plan/README.md) | Implementation plan in vertical slices |
| [AGENTS.md](AGENTS.md) | Working rules for coding agents and human contributors |

## Roadmap

| Slice | Deliverable | Status |
|---|---|---|
| 00 | Repository scaffold, tooling, CI | done |
| 01 | Core types, placeholder codec, mapping | done |
| 02 | Rule-based detectors with checksums | done |
| 03 | Pseudonymisation engine and restoration | done |
| 04 | Configuration and company dictionary | done |
| 05 | OpenAI Chat Completions payload handling | done |
| 06 | HTTP proxy (non-streaming) | done |
| 07 | Streaming restoration | done |
| 08 | Anthropic Messages dialect | planned |
| 09 | ML detector (GLiNER) | planned |
| 10 | Leak audit, policy, metrics | planned |
| 11 | Evaluation harness | planned |
| 12 | CLI, Docker image, first release | planned |

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest --cov -m "not ml"
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE).
