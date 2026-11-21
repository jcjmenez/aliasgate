# Slice 14: rule coverage gaps

## Goal

The rule detector finds identifiers that v0.2.0 sends to the provider unchanged. These are the
gaps listed in backlog §9, plus the ones found while reproducing them:

- Spanish identifiers written with a separator or with dots: `B-58818501`, `B 58818501`,
  `B-58.818.501`, `12.345.678-Z`, `X-1.234.567-L`.
- Email addresses with letters outside ASCII (`josé@example.com`, `juan@bücher.example`) or an
  apostrophe (`o'connor@example.com`, of which v0.2.0 sends `o'` unchanged).
- A US SSN without hyphens (`078051120`, `078 05 1120`), when a keyword such as `SSN` stands
  right before it.
- A PEM private key pasted without its `-----END … PRIVATE KEY-----` line.

Two type conflicts between rules are also settled. An SSN whose nine digits also form a valid
Spanish number (`782-77-8477`) is replaced as `NATIONAL_ID`, not `PHONE`. With `URL` enabled, a
URL wins over an email, a secret or any other rule span that lies inside it, so the rest of the
URL is no longer forwarded. Everything else the rules detect is detected as before, with the
same offsets.

## Depends on

Slice 13 (v0.2.0 released). Every earlier slice is merged.

## Decisions taken with the maintainer before implementation (2026-11-21)

1. **Scope: the §9 gaps and the forms found next to them.** The dotted and spaced Spanish
   identifiers and the email forms are in the same rules, and each one leaks today. Other
   countries' identifiers, passports, licence plates and new secret formats stay in §9.
2. **A bare SSN needs a keyword.** Nine bare digits are too ambiguous to replace everywhere.
   One of the keywords `SSN`, `SSNs`, `SS#` or `social security` must stand on the same line,
   at most 25 characters before the number, with no digit in between. The span covers the
   number only. Rejected: leaving bare SSNs undetected.
3. **SSN against phone: by score.** The SSN score goes from 0.85 to 0.95, above `PHONE` (0.9).
   Rejected: dropping phone matches that equal a plausible SSN in the detector, which needs
   more code for the same result.
4. **URL against what lies inside it: by containment, in the detector.** With `URL` enabled,
   the rule detector drops every span of another rule that lies entirely inside a URL span.
   The `URL` score stays 0.9, so where a value only partly overlaps a URL, the value still
   wins, as today. First decided as a `URL` score of 1.0, then changed with the maintainer the
   same day: with equal scores the longer span wins (architecture §3.3), so in
   `https://example.com/pay/4111 1111 1111 1111` the URL would win and `1111 1111 1111` of the
   card would be forwarded, where v0.2.0 replaces the whole card. The resolver does not change.
5. **A PEM key without its END line ends where its base64 lines end.** The span runs from the
   BEGIN line over the header lines of an encrypted key and over each following line made only
   of base64 characters. The text after the key is kept. Rejected: a span to the end of the
   text, which would hide everything the user wrote after the key.

## Decisions made while implementing

- **Context groups.** `_Rule.group` names the pattern group that is the candidate. Only the
  SSN keyword rule uses it. A rejected candidate resumes the search one character after the
  start of the whole match, so a keyword followed by an implausible number does not stop the
  scan.
- **PEM linear time.** The END line is searched for only until it is missing once. The
  existing case of 24,000 BEGIN lines did not catch a regression to searching after every
  BEGIN line: the literal search is fast enough that the quadratic version took 4 s locally,
  under the 15 s bound. A case of 100,000 BEGIN lines takes 0.06 s, against 68 s for the
  quadratic version, and the test now catches it.
- **Tests beyond the list.** `SSNs:` and a keyword exactly 25 characters before the number are
  detected, and `ssnrecord` is not. A PEM key without END line at the very end of the text, a
  `TAX_ID` adversarial case (`"B-58.818." * 20_000`), and `https://` among the pieces of the
  arbitrary-text property. The URL property has its own generator (URL starts, emails, IP
  addresses, cards, query separators), because random pieces rarely form a URL with a value
  inside. Checked: 448 of 3,000 generated texts have a span dropped inside a URL.
- **Measured.** The five Hypothesis properties of `test_rules.py` passed 20,000 examples each.
  Each new condition was broken on purpose and a test failed: the URL filter, the SSN score,
  the dot stripping, both dotted-number lookarounds, the 25-character gap and its no-digit
  rule, the base64 line end, the escaped line breaks, the encrypted-key headers, an
  apostrophe at either end of the local part, combining marks, `(?<!\w')` (the apostrophe
  case then takes over 15 s) and the single END search. The rules-only baseline gave exactly
  the numbers expected below.

## Dependencies to add

None.

## Files

| File | Action |
|---|---|
| `src/aliasgate/detectors/rules.py` | change: the patterns, the SSN score and keyword rule, spans inside a URL, the PEM body |
| `tests/detectors/test_rules.py` | change |
| `tests/test_engine.py` | change: a URL with an email inside it, through the engine |
| `docs/usage.md` | change: §6.1 |
| `docs/threat-model.md` | change: residual risk 1 |
| `docs/evaluation.md` | change: the rules-only baseline |
| `docs/plan/slice-02-rule-detectors.md` | change: a "Changed in slice 14" note |
| `docs/plan/README.md` | change: shared test data, slice status |
| `docs/plan/backlog.md` | change: §9 |
| `README.md` | change: roadmap status |
| `CHANGELOG.md` | change: an `Unreleased` entry |

`src/aliasgate/detectors/checksums.py` does not change. Its functions strip spaces and hyphens,
not dots, and they are public. The rules remove the dots before they call them.

## Specification

Read slice 02 and architecture §3 before starting. No public interface changes: `RuleDetector`,
`DEFAULT_RULE_TYPES` and the checksum functions keep their signatures. The patterns below are
starting points, as in slice 02. Each one must keep the scan linear (see Tests). In the table,
`\|` is the regular expression `|`, escaped for Markdown.

### Spanish identifiers

| Rule | Candidate pattern (starting point) | Validation |
|---|---|---|
| DNI | `(?<![A-Za-z0-9])(?<!\d\.)(?:\d{8}\|\d{2}\.\d{3}\.\d{3})[- ]?[A-Za-z](?![A-Za-z0-9])` | `es_dni_valid` without the dots |
| NIE | `(?<![A-Za-z0-9])[XYZxyz][- ]?(?:\d{7}\|\d\.\d{3}\.\d{3})[- ]?[A-Za-z](?![A-Za-z0-9])` | `es_nie_valid` without the dots |
| CIF | `(?<![A-Za-z0-9])[A-Za-z][- ]?(?:\d{7}[0-9A-Ja-j]\|\d{2}\.\d{3}\.\d{3})(?![A-Za-z0-9])(?!\.\d)` | `es_cif_valid` without the dots |

- The dots group the digits as Spanish numbers are written: `12.345.678` for a DNI,
  `1.234.567` for the digits of an NIE, `58.818.501` for a CIF whose control character is a
  digit. A CIF with a control letter has no dotted form. Other groupings are not identifiers.
- `(?<!\d\.)` and `(?!\.\d)` keep the dotted forms from matching inside a longer dotted number
  (`9.12.345.678-Z`).
- A CIF may now have one hyphen or one space after its letter, as a DNI already may before its
  letter. The checksum still filters words that have the shape. A letter, a space and eight
  digits that pass the checksum by chance become a `TAX_ID`, which costs utility but leaks
  nothing.
- The score stays 1.0.

### Email

Starting point, with `M` standing for the combining diacritical marks `̀-ͯ`:

```
(?<![\w.%+-])(?<![M])(?<!\w')
(?:[\w.%+-]|[M])+(?:'(?:[\w.%+-]|[M])+)*
@(?:[^\W_]|[M]|-)+(?:\.(?:[^\W_]|[M]|-)+)+
(?![\w-])(?![M])
```

- The local part takes letters and digits of any script (`\w`), plus `.`, `%`, `+`, `-` and
  `_` as before. An apostrophe may stand between two of these characters, never at either end,
  so `'juan.perez@example.com'` gives `juan.perez@example.com`. `(?<!\w')` keeps the scan from
  starting again after every apostrophe, and so keeps it linear.
- Domain labels take letters and digits of any script and hyphens. No underscore, as before.
- Combining marks count as letters: Python's `\w` does not match them, and a decomposed `é`
  (`e` followed by U+0301) must not cut the address.
- In scripts written without spaces, the span can take in neighbouring words
  (`メールはjuan@example.comです` is one span). v0.2.0 detects nothing there, so a wider span
  costs utility but leaks less.
- A trailing `.` is still not part of the span. The score stays 1.0.

### US SSN

- The hyphenated rule keeps its pattern, and its score becomes 0.95.
- A new `NATIONAL_ID` rule, score 0.95, for the forms without hyphens:
  `(?i)(?<![a-z0-9])(?:ssns?|ss\#|social[ \t]+security)(?![a-z])[^\d\r\n]{0,25}?(?<!\d)(\d{3}[ ]?\d{2}[ ]?\d{4})(?![\d-])`
  validated with `us_ssn_plausible`. The span is group 1, the number. The keyword is not
  replaced.
- Give the internal `_Rule` an optional group number, defaulting to 0 (the whole match), so
  that this stays a table entry. A rejected candidate resumes the search one character after
  the start of the whole match, as `_scan` does today.
- The keyword ends where the next character is not a letter (`(?![a-z])`), so `ssn_number`,
  `user_ssn=` and `"ssn":` count, and `ssnrecord` and `classn` do not. `social security` also
  covers `Social Security number:` and `Social Security No.`
- Each number needs its own keyword. In `SSNs: 078051120, 219099999` only the first is
  detected, because the second has digits between it and the keyword. A bare SSN in a table
  column, with the keyword only in the header row, is not detected.

### URL

The pattern and the score (0.9) do not change. With `URL` enabled, `detect` drops every span of
another rule (phones and PEM keys included) whose `start` and `end` both lie within one URL
span: `url.start <= span.start` and `span.end <= url.end`. URL spans do not overlap each other,
so a binary search over their starts finds the candidate. Then:

- An email, a secret, an IP address, a card or any other value inside a URL is replaced with
  the URL, as part of `[URL_1]`.
- A URL with credentials (`https://juan:pw@example.com/x`) is one URL span. v0.2.0 sent
  `https://juan:` and `/x` unchanged.
- A value that only partly overlaps a URL is not dropped. It wins the overlap with its higher
  score, as in v0.2.0, so the value is replaced whole and the rest of the URL is forwarded.
- With `URL` disabled (the default), nothing changes: an email inside a URL is replaced as an
  email.

Spans of other detectors are not affected: the rule detector only sees its own.

### PEM private keys

A BEGIN line still pairs with the first END line after it, and that span does not change. A
BEGIN line with no END line after it now gives a span that covers:

1. the BEGIN line;
2. the `Proc-Type:` and `DEK-Info:` header lines right after it (encrypted keys in the
   traditional format), each to the end of its line, and the one blank line after them;
3. each following line made only of base64 characters (`A-Za-z0-9+/=`), optionally followed by
   spaces or tabs. The line must end at a line break, at the end of the text, or right before a
   `"` or `'` (the end of a quoted string). The first line that does not qualify ends the body.

The span ends at the last base64 character, or at the end of the BEGIN line (or the last header
line) when no body line follows. Line breaks are `\n`, `\r\n`, and their JSON escapes, the
two characters `\n` and the four characters `\r\n`, because a key can sit inside a JSON
document that is itself one string (a tool result). In escaped text, a body line may also hold
`\/`, the JSON escape for `/`.

Once a BEGIN line has no END line after it, no later BEGIN line has one either. So the scan
takes the body of each later BEGIN line in turn, and stays linear.

The score stays 1.0.

### Documentation

- **Usage §6.1.** Name the forms: DNI, NIE and CIF with a hyphen, a space or dots; US SSNs with
  hyphens anywhere, without hyphens only after a keyword (list them); emails with non-ASCII
  letters and apostrophes; PEM keys with or without their END line. With `URL` enabled, a URL
  is replaced whole, including an email or a token inside it. Also say what is still not
  found: a bare SSN with no keyword (a table column, for example), and a private key whose
  lines were joined with spaces and that has no END line.
- **Threat model, risk 1.** Add that the rules recognise only the formats listed in usage §6.1:
  an identifier written another way is a missed detection.
- **Evaluation.** Re-run the rules-only baseline on the committed corpora (they are not
  regenerated) and update the table. Expected, measured with a prototype: English
  `NATIONAL_ID` `recall_exact` goes from 0.733 to 1.000 and English `PHONE` `precision_exact`
  from 0.858 to 1.000. Every other number stays. Remove the sentence about the 16 SSNs replaced
  as `PHONE`.
- **Slice 02 file.** Under its rule table, a "Changed in slice 14" note that points here and
  gives the new SSN score and the rule for spans inside a URL.
- **Plan README, shared test data.** Add the new valid and invalid forms listed under Tests.
- **Backlog §9.** Remove the fixed gaps. Keep what is still open: the identifiers of other
  countries, bare SSNs without a keyword, a key joined with spaces without its END line, and
  the two found while writing this file (see Out of scope).
- **CHANGELOG.** `Unreleased` → `Fixed` for the missed forms, `Changed` for the two type
  conflicts.

## Tests

Synthetic values only. All of them are valid unless marked invalid: `A58818501` and
`B58818501` (a CIF's checksum ignores its letter), `P2800000H`, `12345678Z`, `X1234567L`,
`078-05-1120`, `078051120`, `782-77-8477` (the corpus case: a plausible SSN and a valid Spanish
number). Write non-ASCII characters with `\u` escapes in the tests, so that no editor
normalises them.

`test_rules.py`

- The `VALID` list gains `B-58818501`, `B 58818501`, `B-58.818.501`, `P-2800000H` (`TAX_ID`);
  `12.345.678-Z`, `12.345.678Z`, `12.345.678 Z`, `X-1.234.567-L`, `X1.234.567L`
  (`NATIONAL_ID`); `josé@example.com`, `josé@example.com`, `o'connor@example.com`,
  `núñez@example.org`, `juan@bücher.example` (`EMAIL`). The existing test then
  checks each one in a sentence with its exact surface.
- The `INVALID` list gains `B-58818502`, `B-58.818.502` (`TAX_ID`), `12.345.678-A`,
  `X-1.234.567-A` (`NATIONAL_ID`).
- No `NATIONAL_ID` in `9.12.345.678-Z`, `112.345.678-Z` or `12.3456.78-Z`. No `TAX_ID` in
  `B-58.818.501.2` or `B-5881.8501`.
- `'juan.perez@example.com'` gives the surface `juan.perez@example.com`, and
  `juan.perez@example.com's` the same surface. `'@example.com` and `o'@example.com` give no
  email.
- `メールはjuan@example.comです` (written with escapes) gives one `EMAIL` span whose surface
  contains `juan@example.com`.
- SSN keyword: `SSN: 078051120`, `SSN 078 05 1120`, `{"ssn": "078051120"}`,
  `Social Security number of the client: 078051120`, `ss# 078051120` and
  `user_ssn=078051120` each give one `NATIONAL_ID` whose surface is the number.
- No `NATIONAL_ID` in `078051120` alone, `SSN (2024): 078051120`, `SSN\n078051120`, a keyword
  with 26 characters between it and the number, `classn 078051120`, `SSN: 000051120`,
  `SSN: 0780511201` and `SSN: 078-051120`.
- The existing score test expects `NATIONAL_ID` 0.95 for `078-05-1120`. This is the change
  decided above, not a weakened test.
- `782-77-8477` and `(782-77-8477)`, after `resolve_overlaps`, give one span, a `NATIONAL_ID`
  whose surface is `782-77-8477`. `+1 415 555 2671` is still a `PHONE`.
- With `URL` enabled, `detect` itself (before any resolution) returns one span, the whole URL,
  for `https://example.com/reset?user=juan.perez@example.com&token=abc123`,
  `https://juan:pw@example.com/x`, a URL whose query holds a secret built at run time,
  `http://192.0.2.44/status` and `https://example.com/pay/4111-1111-1111-1111`. With `URL`
  disabled, the first gives one `EMAIL`.
- With `URL` enabled, `https://example.com/pay/4111 1111 1111 1111`, after `resolve_overlaps`,
  gives one `CREDIT_CARD` whose surface is the whole card number (partial overlap, unchanged).
- property: with `URL` enabled, no span other than a URL lies entirely inside a URL span, and
  every span `detect` returns without `URL` enabled that does not lie inside a URL is still
  returned with it.
- PEM: the existing `test_pem_header_without_end_line_is_not_a_span` is replaced by its
  opposite. `f"{PEM_BEGIN}\n{PEM_BODY}\n"` gives one `SECRET` whose surface is
  `f"{PEM_BEGIN}\n{PEM_BODY}"`. This is the change decided above, and the pull request lists
  it.
- PEM without END, each giving one `SECRET` with the stated surface:
  - three body lines, then `\nwhy does it fail?`: the surface stops at the last body line;
  - CRLF line breaks, with trailing spaces on one body line;
  - an RSA key with `Proc-Type:` and `DEK-Info:` lines and a blank line before the body;
  - inside a JSON document as one string, with `\n` escapes and a `\/` in the body, followed
    by `"}`: the surface stops before the `"`;
  - the BEGIN line followed by a prose line: the surface is the BEGIN line;
  - two keys without END lines, with prose between them: two spans.
- A BEGIN line without END, followed later by a complete key, is one span from the first BEGIN
  line to the END line (unchanged behaviour, now tested).
- Linear time: add `"a'" * 50_000`, `"é." * 50_000` and `"é@" * 50_000` (`EMAIL`),
  `"12.345." * 20_000` (`NATIONAL_ID`), `"ssn " * 30_000` and `"SSN" + " " * 100_000`
  (`NATIONAL_ID`), `PEM_BEGIN + "\nA" * 100_000` and `(PEM_BEGIN + "\nAAAA ") * 20_000`
  (`SECRET`) to the adversarial cases, under the same bound.
- property: for any eight digits with their computed DNI letter, the dotted form with a hyphen
  is detected with its exact surface, and the same with any other letter is not.
- property: for any seven digits with their computed CIF control digit and any first letter
  from `ABCDEFGHJNPQRSUVW`, the plain, hyphen, space and dotted forms are each detected with
  their exact surface.
- property: for any plausible SSN, `SSN: ` followed by its nine digits gives one span whose
  surface is the digits.
- property: the existing arbitrary-text property gains the pieces `'`, `é`, `SSN `,
  `PEM_BEGIN` and `\\n`.

`test_engine.py`

- With `URL` enabled, the text below gives `Reset at [URL_1] or write to [EMAIL_1].`, and
  `restore` gives the original text back. Propagation of the email does not reach inside the
  URL, because the URL span has the higher priority.

  ```
  Reset at https://example.com/r?u=juan.perez@example.com or write to juan.perez@example.com.
  ```

Evaluation: the recall gate (`tests/eval/test_recall_gate.py`) passes unchanged. Run the
Hypothesis properties of `test_rules.py` once locally with 20,000 examples and record the
result in the pull request.

## Acceptance checklist

- [ ] Every form in the Goal is detected with its exact surface, and the invalid forms are not.
- [ ] Every pre-existing test passes unchanged, except the two expectations listed above (the
      SSN score and the PEM without END line), which the pull request names.
- [ ] Every new pattern is in the linear-time test, under the existing bound.
- [ ] The rules-only baseline is re-measured and `docs/evaluation.md` updated. English
      `NATIONAL_ID` `recall_exact` and English `PHONE` `precision_exact` are 1.000, and no
      other number drops.
- [ ] Usage, threat model, evaluation, the slice 02 file, the shared test data, backlog §9 and
      CHANGELOG are updated in the same pull request.
- [ ] Definition of done in `docs/plan/README.md` is met.

## Out of scope

- Identifiers of other countries, passports, licence plates, more phone formats and new secret
  formats (backlog §9).
- A bare SSN without a keyword, and SSNs in table columns.
- A PEM key whose lines were joined with spaces and that has no END line.
- Found while writing this file, recorded in backlog §9:
  - A dictionary term inside a URL beats the URL, because the dictionary layer has a higher
    priority. With `URL` enabled, the rest of that URL is still forwarded.
  - A value that only partly overlaps a URL (a card number that starts at the end of a URL
    path and goes on after a space) wins, and the rest of the URL is forwarded. Replacing both
    needs the resolver to merge overlapping spans, which architecture §3.3 does not do.
- Changes to the resolver, the engine, the checksum functions or the evaluation corpora.
