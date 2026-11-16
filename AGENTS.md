# Working rules for coding agents and contributors

Read this file completely before changing anything. It applies to humans too.

## What this project is

A security product. Its job is to stop customer data from reaching an LLM provider. A bug here is
not a wrong pixel; it is a data leak. Prefer the boring, obviously correct solution every time.

## Before you write code

1. Read [docs/architecture.md](docs/architecture.md) and [docs/threat-model.md](docs/threat-model.md).
2. Read [docs/plan/README.md](docs/plan/README.md), then the file of the slice you were asked to
   implement. Implement **one slice per pull request**, nothing more.
3. Check that every slice listed under "Depends on" is merged. If not, stop and say so.

## How to work on a slice

1. Create a branch `slice-NN-short-name` from `main`.
2. Write the tests listed in the slice file first. Run them and watch them fail.
3. Implement until they pass. Add tests for any behaviour you add that the slice file does not
   list.
4. Run the full check (below). All four commands must pass.
5. Tick the slice's acceptance checklist in the pull request description, set the slice status
   to `done` in `docs/plan/README.md` and in the roadmap table in `README.md`.
6. Commit with [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
   `test:`, `docs:`, `chore:`, `refactor:`). Open a pull request. Do not push to `main`.

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest --cov -m "not ml"
```

## Hard rules

These are not style preferences. A change that breaks one is rejected.

1. **Never use real personal data.** Tests, fixtures, examples and documentation use synthetic
   values only. Use the test vectors given in the slice files, `example.com` / `example.org`
   domains, and obviously fictional names.
2. **Never log text.** No request text, response text, detected value, or mapping in any log
   line, exception message, metric label or trace attribute. Log counts, types, durations, ids.
   When you raise an exception about a value, describe its position and type, not its content.
3. **Fail closed.** If something goes wrong before forwarding, reject the request. Never add a
   code path that forwards a request whose pseudonymisation did not complete.
4. **Nothing reversible on disk.** Do not persist mappings, caches containing text, or request
   bodies. In-memory only.
5. **Respect the layers.** Core modules do not import from `dialects` or `proxy`. Dialects do not
   import from `proxy` and do not perform I/O. See the table in architecture §1.
6. **Do not change public interfaces defined in the architecture or in a merged slice** without
   updating the architecture document in the same pull request and explaining why.
7. **Do not add a dependency that the slice file does not list.** If you believe one is needed,
   stop and ask.
8. **No network in tests.** Upstream providers are mocked (`respx`). Tests marked `ml` are the
   only ones allowed to download a model and they do not run in default CI.
9. **Do not weaken a test to make it pass.** If a specified test seems wrong, stop and report
   the contradiction instead of editing the expectation.

## Code style

- Python 3.12+, fully typed, `mypy --strict` clean. No `Any` in core modules; `Any` is
  acceptable for JSON payloads in dialects.
- `from __future__ import annotations` is not needed on 3.12; do not add it.
- Dataclasses are `frozen=True, slots=True` unless mutation is the point.
- Pure functions in the core. No global mutable state. No singletons.
- Compile regular expressions once at module level.
- Public functions and classes have a one-line docstring saying what they guarantee. Do not
  write comments that restate the code.
- Test names describe behaviour: `test_iban_with_wrong_checksum_is_not_detected`.
- Property tests (Hypothesis) are required wherever the slice file says "property".

## When you are unsure

If the slice file is ambiguous or contradicts the architecture, do not guess. Write down the
question, the options you see and your recommendation, and stop. A wrong guess in this codebase
can leak data.

## External facts to verify, not assume

Library APIs and model identifiers change. Before using any of the following, check the current
documentation and record the version you verified in the pull request description:

- provider request and response shapes (OpenAI Chat Completions, Anthropic Messages), including
  streaming event formats;
- the `gliner` package API and the chosen model's identifier and licence;
- `phonenumbers`, `httpx`, `starlette` and `respx` APIs.
