# Contributing

Thank you for helping. This is a security-sensitive project, so the process is strict on a few
points and relaxed on the rest.

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync
```

## Checks

All of these run in CI and must pass:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov -m "not ml"
```

## Rules

The working rules in [AGENTS.md](AGENTS.md) apply to every contribution, whoever or whatever
writes it. The most important ones:

- synthetic data only, in tests, examples and issues;
- no request or response text in logs;
- fail closed;
- one slice or one focused change per pull request, with tests.

## Pull requests

- Branch from `main`, use [Conventional Commits](https://www.conventionalcommits.org/).
- Describe what changed, why, and how you tested it.
- If the change touches detection, include before and after numbers from the evaluation harness
  once it exists (slice 11).
- If the change touches a security invariant listed in
  [docs/threat-model.md](docs/threat-model.md), say so explicitly.

## Reporting a detection miss

A value that should have been pseudonymised and was not is a bug. Open an issue with a
**synthetic** example that reproduces it. Never paste real data into an issue.

## Security vulnerabilities

Do not open a public issue. See [SECURITY.md](SECURITY.md).
