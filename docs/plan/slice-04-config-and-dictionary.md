# Slice 04: configuration, company dictionary, allow list

## Goal

The gateway's behaviour is described by one validated configuration object, loadable from a YAML
file and environment variables. A `DictionaryDetector` matches user-provided terms exactly. A
factory builds a ready `Pseudonymizer` from the configuration.

## Depends on

Slices 02 and 03.

## Dependencies to add

```bash
uv add pydantic pydantic-settings pyyaml
uv add --dev types-PyYAML
```

## Files

| File | Action |
|---|---|
| `src/aliasgate/config.py` | create (settings and `load_terms`) |
| `src/aliasgate/detectors/dictionary.py` | create (`Term`, `DictionaryDetector`; standard library only) |
| `src/aliasgate/factory.py` | create |
| `examples/aliasgate.example.yaml`, `examples/dictionary.example.yaml` | create |
| `tests/test_config.py`, `tests/detectors/test_dictionary.py`, `tests/test_factory.py` | create |

## Specification

### Configuration schema (`config.py`)

Pydantic v2 models, all with `model_config = ConfigDict(extra="forbid", frozen=True)`, so a
misspelled key is a start-up error rather than a silently ignored setting.

```yaml
listen:
  host: 127.0.0.1          # default; binding to 0.0.0.0 must be an explicit choice
  port: 8080
  max_request_bytes: 10485760

upstreams:
  openai:
    base_url: https://api.openai.com
  anthropic:
    base_url: https://api.anthropic.com
  timeout_seconds: 600
  passthrough_paths: []     # e.g. ["GET /v1/models"]

detection:
  rules:
    enabled_types: [EMAIL, PHONE, IBAN, CREDIT_CARD, NATIONAL_ID, TAX_ID, IP_ADDRESS, SECRET]
    phone_regions: [ES, US]
  dictionary:
    files: []               # paths, relative to the config file's directory
  allow_list: []            # terms never pseudonymised
  ml:
    enabled: false          # fields below are defined in slice 09; accept and ignore-validate now
    model: null
    threshold: 0.4
    labels: {}
  propagate: true
  min_propagation_length: 3

policy:
  unsupported_content: block      # block | pass
  strip_user_identifiers: true
  tool_definitions: true          # pseudonymise "description" values in tool definitions

logging:
  level: INFO
```

```python
class Settings(BaseModel): ...      # root model with the sections above as nested models

def load_settings(path: Path | None, env: Mapping[str, str] = os.environ) -> Settings:
    """Load YAML (if a path is given), overlay ALIASGATE_* environment variables, validate."""
```

- Environment overrides use the prefix `ALIASGATE_` and `__` between levels:
  `ALIASGATE_LISTEN__PORT=9000`. Lists are given as JSON (`ALIASGATE_DETECTION__ALLOW_LIST='["ACME"]'`).
  Use `pydantic-settings` for this; verify its current API for combining a YAML source with
  environment variables, and make environment values win over the file.
- Validation: base URLs must be `http` or `https` with a host and may end in a base path (changed
  after merge, see "Base paths in `base_url`" below); entity types pass
  `validate_entity_type`; `phone_regions` are two upper-case letters; `threshold` in `[0, 1]`;
  `min_propagation_length >= 1`; `passthrough_paths` entries match `^(GET|POST|DELETE) /\S*$`.
- Missing file → `FileNotFoundError` with the path. Invalid content → `pydantic.ValidationError`
  (let it propagate; the CLI formats it in slice 12).
- Dictionary file paths are resolved relative to the configuration file.
- `Settings` contains no secrets. The gateway forwards client credentials; it stores none.

### Base paths in `base_url` (changed after merge, 2026-11-18)

Slice 04 first rejected any path beyond `/`. That made Azure OpenAI unreachable: it serves the
OpenAI-compatible API only under `/openai/v1/` (`GET /v1/models` answers 404), and the gateway
forwards `POST /v1/chat/completions` to `{base_url}/v1/chat/completions`. The slice 07 manual
check had to use a throwaway local relay that rewrote the path. `base_url` may now end in a base
path, for example `https://<resource>.openai.azure.com/openai`, and the proxy still builds the
upstream URL by plain concatenation (architecture §7). The validation guarantees that
`base_url + request path` is exactly the URL httpx sends:

- No `?` or `#` anywhere, not even an empty query or fragment (before this change
  `https://api.example.com?` was accepted and moved the request path into the query string).
  No whitespace or control characters, because `urlsplit` silently drops tabs and newlines.
- No credentials; scheme `http` or `https`; a host; a valid port. Unchanged.
- Trailing slashes are removed: `/openai/`, `/openai//` and `/openai` are the same base path.
- Every remaining segment is non-empty and written in RFC 3986 path characters (letters,
  digits, `-._~!$&'()*+,;=:@`) and `%XX` escapes. Escapes are kept and sent as written,
  including their case and an encoded slash (`%2F`); the gateway never decodes or re-encodes
  them. Anything else (a space, a non-ASCII letter, `\`, `{}`, `[]`, `|`, `"`, an invalid
  escape such as `%zz`) is rejected: write it percent-encoded.
- No `.` or `..` segment, plain or percent-encoded (`%2e%2E`): httpx resolves the plain form,
  and upstream servers may resolve the encoded one, so the request would not go where the
  configuration says.

Tests: `tests/test_config.py` (accepted and rejected URLs, the reason each rejection names, and a
property: an accepted base URL plus a route path parses in httpx to the same host and the same
raw path, with no query or fragment) and `tests/proxy/test_upstream_base_path.py` (the URL
called upstream for streaming and non-streaming chat requests and for a passthrough path).

### Dictionary file format

```yaml
terms:
  - term: ACME Corporation
    type: ORG
  - term: Project Falcon
    type: PROJECT           # custom entity types are allowed
```

### `detectors/dictionary.py`

```python
@dataclass(frozen=True, slots=True)
class Term:
    term: str
    entity_type: str

class DictionaryDetector:
    name = "dictionary"
    def __init__(self, terms: Sequence[Term]) -> None: ...
    def detect(self, text: str) -> list[Span]: ...
```

- Matching is whole-term, case-insensitive, whitespace-flexible: the same semantics as
  `build_term_pattern`.
- Implementation: one compiled alternation of all terms, **longest term first**, each term's
  tokens escaped and joined with `\s+`, wrapped once in `(?<!\w)(?:…)(?!\w)` with
  `re.IGNORECASE`. Keep a dict from `normalize_key(term)` to entity type to find the type of a
  match. With zero terms, `detect` returns `[]` without compiling an empty alternation.
- `re.IGNORECASE` matches `I`/`i` with `İ` (U+0130) and `ı` (U+0131), which `normalize_key`
  keeps apart, so for such a match the key lookup misses. Then take the first term, in
  alternation order, with the same token lengths whose own pattern fully matches the surface.
  Never drop such a match (a missed detection) and never let the lookup fail (every request
  with that name would be rejected). Do not use one capture group per term to find the term:
  it makes scanning about 500 times slower.
- Spans: `source="dictionary"`, `priority=PRIORITY_DICTIONARY`, `score=1.0`.
- Duplicate terms (same key) with the same type are deduplicated; with different types raise
  `ValueError` naming the 1-based entry indexes, not the term text.
- Terms with an empty key raise `ValueError`.
- `Term.__repr__` does not show the term (terms are confidential).

### `load_terms` (in `config.py`)

```python
def load_terms(path: Path) -> list[Term]:
    """Parse and validate a dictionary file."""
```

It lives in `config.py`, not in `detectors/dictionary.py`, because it parses YAML: detectors
are core modules, and the core has no third-party dependency except `phonenumbers`
(architecture §1).

- Error messages from `load_terms` refer to entry positions, never to term content (terms are
  confidential). A YAML syntax error is reported by line and column only, because PyYAML's own
  message quotes the offending line.

### `factory.py`

```python
def build_pseudonymizer(settings: Settings) -> Pseudonymizer:
    """Wire detectors in this order: dictionary (if any terms), rules. ML is added in slice 09."""
```

If `settings.detection.ml.enabled` is true, raise `NotImplementedError("ML detection arrives in
slice 09")` so a configuration that expects ML never silently runs without it.

### Examples

`examples/aliasgate.example.yaml` is the schema above with comments. `examples/dictionary.example.yaml`
has three fictional terms. The `.gitignore` already excludes real `aliasgate.yaml` and
`dictionary*.yaml` files outside `examples/`.

## Tests

`test_config.py`
- defaults: `load_settings(None, env={})` returns the defaults listed above.
- a YAML file overriding the port is honoured; an environment variable overrides the file.
- unknown key anywhere → `ValidationError`.
- invalid values each fail: bad URL scheme, URL with a query (a URL with a path was in this list
  until base paths were allowed, see above), lower-case entity type,
  three-letter region, threshold 1.5, `unsupported_content: maybe`.
- relative dictionary path is resolved against the config file directory (use `tmp_path`).
- the example file in `examples/` loads without error.
- `load_terms` on the example file returns three terms; a file missing `type` raises with the
  entry index.

`test_dictionary.py`
- a term is found case-insensitively and across a line break between its tokens.
- whole-term only: `ACME` is not found in `ACMEX` nor in `MACME`.
- longest wins: with `ACME` and `ACME Corporation`, the text `ACME Corporation` yields one span
  covering both words.
- a term with regex metacharacters (`C++ Team (EU)`) is matched literally.
- custom type is reported as given.
- zero terms → no spans.
- duplicates: same type deduplicated; different types raise; the message does not contain the
  term.
- property: every span returned lies within the text and its surface's key equals a term key
  (for texts without `İ` and `ı`, which have their own test above).
- `İvan Petrov` and `ıvan petrov` are found and typed like the term `Ivan Petrov`.

`test_factory.py`
- with a dictionary containing `Juan Pérez` (PERSON) and default rules, the batch
  `["Juan Pérez <juan.perez@example.com>", "juan pérez again"]` becomes
  `["[PERSON_1] <[EMAIL_1]>", "[PERSON_1] again"]` and restores to canonical forms.
- dictionary beats rules on overlap: a dictionary term equal to an email address is typed as the
  dictionary says.
- allow list from settings is applied.
- `ml.enabled: true` raises `NotImplementedError`.

## Acceptance checklist

- [ ] `extra="forbid"` on every model; a typo in any section fails.
- [ ] No error message produced in this slice contains a dictionary term.
- [ ] Example files load in tests.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Aliases that share one placeholder (backlog: entity linking).
- Hot reload of configuration or dictionaries.
- Aho–Corasick or other automata. The alternation is enough for tens of thousands of terms;
  measure before optimising.
- Per-user or per-tenant configuration.
