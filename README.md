# aliasgate

[![CI](https://github.com/jcjmenez/aliasgate/actions/workflows/ci.yml/badge.svg)](https://github.com/jcjmenez/aliasgate/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/jcjmenez/aliasgate/blob/main/LICENSE)

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

> **Status: alpha (0.5.0).** The gateway pseudonymises OpenAI Chat Completions and Anthropic
> Messages requests, streaming and tool calls included, with rules, company dictionaries and an
> optional local ML model, and restores the answers. It has not run in production yet. Start
> with the [usage guide](https://github.com/jcjmenez/aliasgate/blob/main/docs/usage.md).

## Quick start

```bash
pip install aliasgate
echo "{}" > aliasgate.yaml   # every key is optional: rules only, port 8080 on loopback
aliasgate check --config aliasgate.yaml
aliasgate serve --config aliasgate.yaml
export OPENAI_BASE_URL=http://localhost:8080/v1 ANTHROPIC_BASE_URL=http://localhost:8080
```

`serve` keeps running. In another shell, the last line points OpenAI and Anthropic clients at
the gateway, which forwards their usual API key. Dictionaries for your customers' names, the ML model, Docker images, Azure
OpenAI, Claude in Microsoft Foundry and tuning are in [docs/usage.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/usage.md).

## Why

Many companies forbid sending customer data to external LLMs, and their staff still need LLMs to
work on documents full of customer data. aliasgate is a technical control for that conflict: you
point your OpenAI or Anthropic client at the gateway instead of the provider, and nothing else
changes.

## Design in brief

- **Four detection layers.** Rules with checksum validation (emails, IBANs, cards, national IDs,
  phones, secrets), a company dictionary (your customers, projects, codenames and their
  aliases), a pluggable local ML model for names, organisations and addresses, and propagation
  of every detected value to all its other occurrences.
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
regulation. Read the [threat model](https://github.com/jcjmenez/aliasgate/blob/main/docs/threat-model.md) before relying on it.

## Documentation

| Document | Content |
|---|---|
| [docs/usage.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/usage.md) | Install, configure, point clients at the gateway, tune detection, error codes, metrics |
| [docs/deployment.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/deployment.md) | Network placement, container, sizing, monitoring, deployment checklist |
| [docs/evaluation.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/evaluation.md) | Measuring detection on your own samples |
| [CHANGELOG.md](https://github.com/jcjmenez/aliasgate/blob/main/CHANGELOG.md) | Changes by version |
| [docs/state-of-the-art.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/state-of-the-art.md) | Existing tools, detection models, research evidence, legal context, feasibility |
| [docs/architecture.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/architecture.md) | Components, algorithms, data flow, decisions |
| [docs/threat-model.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/threat-model.md) | What is protected, from whom, and the residual risks |
| [docs/plan/README.md](https://github.com/jcjmenez/aliasgate/blob/main/docs/plan/README.md) | Implementation plan in vertical slices |
| [AGENTS.md](https://github.com/jcjmenez/aliasgate/blob/main/AGENTS.md) | Working rules for coding agents and human contributors |

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
| 08 | Anthropic Messages dialect | done |
| 09 | ML detector (GLiNER) | done |
| 10 | Leak audit, policy, metrics | done |
| 11 | Evaluation harness | done |
| 12 | CLI, Docker image, first release | done |
| 13 | Dictionary aliases (entity linking, part 1) | done |
| 14 | Rule coverage gaps | done |
| 15 | Person mentions evaluation set (entity linking, part 2) | done |
| 16 | Person name tokens and linking (entity linking, part 3) | done |
| 17 | Lower-case, one-word, hyphenated and `Don` person forms (entity linking, part 4) | done |
| 18 | Organisation mentions evaluation set (entity linking, part 5) | planned |
| 19 | Organisation name forms and linking (entity linking, part 6) | planned |

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest --cov -m "not ml"
```

See [CONTRIBUTING.md](https://github.com/jcjmenez/aliasgate/blob/main/CONTRIBUTING.md).

## License

[Apache-2.0](https://github.com/jcjmenez/aliasgate/blob/main/LICENSE).
