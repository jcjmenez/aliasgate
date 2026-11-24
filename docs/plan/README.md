# Implementation plan

The work is cut into **vertical slices**. Each slice is one pull request, leaves `main` green,
and adds behaviour that can be tested on its own. Slices are written to be implemented by a
coding agent with no memory of earlier sessions: every slice file states its inputs, the exact
interfaces to create, the behaviour, the tests, and what is out of scope.

Read [AGENTS.md](../../AGENTS.md) first. Its rules apply to every slice.

## Slices

| # | File | Deliverable | Depends on | Status |
|---|---|---|---|---|
| 00 | (this repository) | Scaffold, tooling, CI | – | done |
| 01 | [slice-01-core-types.md](slice-01-core-types.md) | Spans, placeholder codec, mapping | 00 | done |
| 02 | [slice-02-rule-detectors.md](slice-02-rule-detectors.md) | Regex and checksum detectors | 01 | done |
| 03 | [slice-03-engine.md](slice-03-engine.md) | Overlap resolution, engine, `restore` | 01 | done |
| 04 | [slice-04-config-and-dictionary.md](slice-04-config-and-dictionary.md) | Configuration, company dictionary, allow list | 02, 03 | done |
| 05 | [slice-05-openai-chat-dialect.md](slice-05-openai-chat-dialect.md) | OpenAI Chat Completions payloads (non-streaming) | 03 | done |
| 06 | [slice-06-http-proxy.md](slice-06-http-proxy.md) | Working gateway, non-streaming | 04, 05 | done |
| 07 | [slice-07-streaming.md](slice-07-streaming.md) | Stream restorer, SSE, OpenAI streaming | 06 | done |
| 08 | [slice-08-anthropic-dialect.md](slice-08-anthropic-dialect.md) | Anthropic Messages, streaming included | 07 | done |
| 09 | [slice-09-ml-detector.md](slice-09-ml-detector.md) | GLiNER detector, detection cache | 04 | done |
| 10 | [slice-10-audit-policy-metrics.md](slice-10-audit-policy-metrics.md) | Leak audit, metrics, logging invariant | 08 | done |
| 11 | [slice-11-evaluation.md](slice-11-evaluation.md) | Evaluation harness and CI recall gate | 04 | done |
| 12 | [slice-12-cli-docker-release.md](slice-12-cli-docker-release.md) | CLI, Docker image, usage docs, v0.1.0 | 09, 10, 11 | done |
| 13 | [slice-13-dictionary-aliases.md](slice-13-dictionary-aliases.md) | Dictionary aliases, variant placeholders | 12 | done |
| 14 | [slice-14-rule-gaps.md](slice-14-rule-gaps.md) | Rule coverage gaps | 13 | done |
| 15 | [slice-15-person-evaluation.md](slice-15-person-evaluation.md) | Person mentions evaluation set, link scores | 14 | done |
| 16 | [slice-16-person-tokens.md](slice-16-person-tokens.md) | Person name tokens and linking | 15 | done |
| 17 | [slice-17-person-forms.md](slice-17-person-forms.md) | Lower-case, one-word, hyphenated and `Don` person forms | 16 | done |
| 18 | [slice-18-org-evaluation.md](slice-18-org-evaluation.md) | Organisation mentions evaluation set | 17 | done |
| 19 | [slice-19-org-forms.md](slice-19-org-forms.md) | Organisation name forms and linking | 18 | done |
| 20 | [slice-20-request-fields.md](slice-20-request-fields.md) | Request fields forwarded unchanged | 19 | planned |

Slices 02 and 03 can be done in either order. Slices 09 and 11 can be done any time after 04.
The first usable end-to-end product exists after slice 06; the first one worth deploying, after
slice 10.

Slices after 12 are cut from [backlog.md](backlog.md), one item at a time.

## Slice file format

Every slice file has the same sections:

- **Goal**: one paragraph, what exists after the slice that did not exist before.
- **Depends on**: slices that must be merged first.
- **Dependencies to add**: the only packages you may add, with the exact `uv add` command.
- **Files**: the files to create or change. Do not create others without a reason you can state.
- **Specification**: interfaces and behaviour. Signatures are binding. Regular expressions given
  as "starting point" may be changed as long as the tests pass.
- **Tests**: the minimum set. Each bullet is at least one test. Add more.
- **Acceptance checklist**: copy it into the pull request description and tick every item.
- **Out of scope**: things you must not do in this slice, even if they look easy.

## Definition of done (every slice)

- [ ] All tests listed in the slice file exist and pass.
- [ ] `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` and
      `uv run pytest --cov -m "not ml"` pass locally and in CI.
- [ ] Coverage stays at or above the threshold in `pyproject.toml` (90%, branch).
- [ ] No rule in AGENTS.md is broken. In particular: synthetic data only, no text in logs.
- [ ] Public functions and classes have a docstring that states what they guarantee.
- [ ] `docs/architecture.md` still describes the code. If the slice changed a contract, the
      document is updated in the same pull request.
- [ ] Slice status set to `done` here and in the roadmap table of the root `README.md`.

## Shared test data

Use these synthetic values across slices so tests read consistently. All checksums below are
valid unless the column says otherwise.

| Type | Valid | Invalid (same shape, wrong checksum or range) |
|---|---|---|
| `EMAIL` | `juan.perez@example.com`, `a.b+tag@mail.example.org`, `josé@example.com`, `o'connor@example.com`, `juan@bücher.example` | `juan.perez@`, `@example.com` |
| `IBAN` | `ES91 2100 0418 4502 0005 1332`, `ES9121000418450200051332`, `DE89 3704 0044 0532 0130 00`, `GB82 WEST 1234 5698 7654 32` | `ES91 2100 0418 4502 0005 1333` |
| `CREDIT_CARD` | `4111 1111 1111 1111`, `5555-5555-5555-4444`, `378282246310005` | `4111 1111 1111 1112` |
| `NATIONAL_ID` (ES DNI) | `12345678Z`, `12.345.678-Z` | `12345678A`, `12.345.678-A` |
| `NATIONAL_ID` (ES NIE) | `X1234567L`, `X-1.234.567-L` | `X1234567A`, `X-1.234.567-A` |
| `NATIONAL_ID` (US SSN) | `078-05-1120`, and `078051120` after the keyword `SSN` | `000-12-3456`, `666-12-3456`, `900-12-3456` |
| `TAX_ID` (ES CIF) | `A58818501`, `B-58818501`, `B 58818501`, `B-58.818.501`, `P2800000H` | `A58818502`, `B-58.818.502` |
| `PHONE` | `+34 612 345 678`, `+1 415 555 2671` | `+34 12`, `12345` |
| `IP_ADDRESS` | `192.0.2.44`, `2001:db8::8a2e:370:7334` | `999.1.1.1` |
| `PERSON` (for fakes and dictionary tests) | `Juan Pérez`, `Alice Johnson`, `María-José O'Connor` | – |
| `ORG` | `ACME Corporation`, `Globex S.L.` | – |

**Secrets.** Never write a string shaped like a real credential in a source file, because secret
scanners will block the push. Build such test values at run time, for example
`"ghp_" + "a" * 36`.
