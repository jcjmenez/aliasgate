import string
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.detectors.checksums import es_cif_valid, es_dni_valid, luhn_valid, us_ssn_plausible
from aliasgate.detectors.rules import DEFAULT_RULE_TYPES, RuleDetector
from aliasgate.resolve import resolve_overlaps
from aliasgate.types import PRIORITY_RULE, Span

DETECTOR = RuleDetector()
WITH_URL = RuleDetector(enabled_types=DEFAULT_RULE_TYPES | {"URL"})

PEM_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
PEM_END = "-----END " + "PRIVATE KEY-----"
PEM_BODY = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldY"
RSA_PEM_BEGIN = "-----BEGIN " + "RSA PRIVATE KEY-----"


def _pem(begin: str = PEM_BEGIN, end: str = PEM_END) -> str:
    return f"{begin}\n{PEM_BODY}\n{end}"


# Secret-shaped values are assembled at run time so that no credential-like literal exists in
# the source tree (docs/plan/README.md, "Secrets").
SECRETS = [
    "sk-" + "proj_" + "Ab1-" * 6,
    "gh" + "p_" + "a1B2" * 9,
    "gh" + "s_" + "Z9y8" * 10,
    "github" + "_pat_" + "11AB_" * 11,
    "AK" + "IA" + "Z2" * 8,
    "AS" + "IA" + "Q7" * 8,
    "xo" + "xb-" + "1234567890-abcdef",
    "ey" + "JhbGciOi" + ".ey" + "JzdWIiOi" + ".c2lnbmF0dXJl",
    _pem(),
    _pem("-----BEGIN " + "RSA PRIVATE KEY-----", "-----END " + "RSA PRIVATE KEY-----"),
]

VALID = [
    ("EMAIL", "juan.perez@example.com"),
    ("EMAIL", "a.b+tag@mail.example.org"),
    ("EMAIL", "jos\u00e9@example.com"),
    ("EMAIL", "jose\u0301@example.com"),
    ("EMAIL", "o'connor@example.com"),
    ("EMAIL", "n\u00fa\u00f1ez@example.org"),
    ("EMAIL", "juan@b\u00fccher.example"),
    ("IBAN", "ES91 2100 0418 4502 0005 1332"),
    ("IBAN", "ES9121000418450200051332"),
    ("IBAN", "DE89 3704 0044 0532 0130 00"),
    ("IBAN", "GB82 WEST 1234 5698 7654 32"),
    ("CREDIT_CARD", "4111 1111 1111 1111"),
    ("CREDIT_CARD", "5555-5555-5555-4444"),
    ("CREDIT_CARD", "378282246310005"),
    ("NATIONAL_ID", "12345678Z"),
    ("NATIONAL_ID", "X1234567L"),
    ("NATIONAL_ID", "078-05-1120"),
    ("NATIONAL_ID", "12.345.678-Z"),
    ("NATIONAL_ID", "12.345.678Z"),
    ("NATIONAL_ID", "12.345.678 Z"),
    ("NATIONAL_ID", "X-1.234.567-L"),
    ("NATIONAL_ID", "X1.234.567L"),
    ("TAX_ID", "A58818501"),
    ("TAX_ID", "B-58818501"),
    ("TAX_ID", "B 58818501"),
    ("TAX_ID", "B-58.818.501"),
    ("TAX_ID", "P-2800000H"),
    ("PHONE", "+34 612 345 678"),
    ("PHONE", "+1 415 555 2671"),
    ("IP_ADDRESS", "192.0.2.44"),
    ("IP_ADDRESS", "2001:db8::8a2e:370:7334"),
    *(("SECRET", secret) for secret in SECRETS),
]

INVALID = [
    ("EMAIL", "juan.perez@"),
    ("EMAIL", "@example.com"),
    ("IBAN", "ES91 2100 0418 4502 0005 1333"),
    ("CREDIT_CARD", "4111 1111 1111 1112"),
    ("NATIONAL_ID", "12345678A"),
    ("NATIONAL_ID", "X1234567A"),
    ("NATIONAL_ID", "000-12-3456"),
    ("NATIONAL_ID", "666-12-3456"),
    ("NATIONAL_ID", "900-12-3456"),
    ("TAX_ID", "A58818502"),
    ("TAX_ID", "B-58818502"),
    ("TAX_ID", "B-58.818.502"),
    ("NATIONAL_ID", "12.345.678-A"),
    ("NATIONAL_ID", "X-1.234.567-A"),
    ("PHONE", "+34 12"),
    ("PHONE", "12345"),
    ("IP_ADDRESS", "999.1.1.1"),
]

NO_IDENTIFIERS = [
    "La reunión del 12/05/2024 se aplazó hasta nuevo aviso.",
    "El presupuesto total asciende a 1.250,00 € para el año 2025.",
    "Hemos recibido 37 solicitudes durante el primer trimestre de 2024.",
    "El pedido 4521 saldrá del almacén el lunes a las 10:30.",
    "La tasa de conversión subió un 3,5 % respecto al mes anterior.",
    "The meeting on 12/05/2024 was moved to next week.",
    "Our budget for 2025 is $1,250.00 per team member.",
    "We published task-specific-fine-tuning recipes in version 2.3.",
    "Please review the report before 5:30 pm on Friday.",
    "Revenue grew 12% year over year, reaching 4.5 million in Q3 2024.",
    "Order ORD-2013-015180 and invoice 2024-001234 were paid.",
    "The ISBN of the book is 978-0-00-000004-0.",
]


def _found(detector: RuleDetector, text: str, entity_type: str | None = None) -> list[str]:
    return [
        text[span.start : span.end]
        for span in detector.detect(text)
        if entity_type is None or span.entity_type == entity_type
    ]


@pytest.mark.parametrize(
    ("entity_type", "value"), VALID, ids=[f"{t}-{i}" for i, (t, _) in enumerate(VALID)]
)
def test_valid_vector_in_a_sentence_yields_one_span_with_the_exact_surface(
    entity_type: str, value: str
) -> None:
    assert _found(DETECTOR, f"Contact: {value}.", entity_type) == [value]


@pytest.mark.parametrize(
    ("entity_type", "value"), INVALID, ids=[f"{t}-{i}" for i, (t, _) in enumerate(INVALID)]
)
def test_invalid_vector_yields_no_span_of_its_type(entity_type: str, value: str) -> None:
    assert _found(DETECTOR, f"Contact: {value}.", entity_type) == []


def test_spans_carry_source_priority_and_the_rule_score() -> None:
    text = "Mail juan.perez@example.com, SSN 078-05-1120, IP 192.0.2.44, tel +34 612 345 678."
    scores = {span.entity_type: span.score for span in DETECTOR.detect(text)}
    assert scores == {"EMAIL": 1.0, "NATIONAL_ID": 0.95, "IP_ADDRESS": 0.9, "PHONE": 0.9}
    assert all(
        (span.source, span.priority) == ("rules", PRIORITY_RULE) for span in DETECTOR.detect(text)
    )


@pytest.mark.parametrize(
    ("entity_type", "value"),
    [("CREDIT_CARD", "4111111111111112"), ("IBAN", "ES9121000418450200051333")],
)
def test_rejected_candidate_without_separators_yields_no_span(entity_type: str, value: str) -> None:
    assert _found(DETECTOR, f"Contact: {value}.", entity_type) == []


def test_email_iban_and_phone_in_one_text_yield_three_spans_with_correct_offsets() -> None:
    email, iban, phone = (
        "juan.perez@example.com",
        "ES91 2100 0418 4502 0005 1332",
        "+34 612 345 678",
    )
    text = f"Write to {email}, pay {iban} or call {phone}."
    expected = [
        Span(text.index(value), text.index(value) + len(value), entity_type, score, "rules", 60)
        for entity_type, value, score in [
            ("EMAIL", email, 1.0),
            ("IBAN", iban, 1.0),
            ("PHONE", phone, 0.9),
        ]
    ]
    assert DETECTOR.detect(text) == expected


def test_iban_followed_by_upper_case_words_detects_only_the_iban() -> None:
    text = "ES9121000418450200051332 IS MINE"
    assert DETECTOR.detect(text) == [Span(0, 24, "IBAN", 1.0, "rules", PRIORITY_RULE)]


def test_two_ibans_separated_by_a_space_are_both_detected() -> None:
    first, second = "ES91 2100 0418 4502 0005 1332", "GB82 WEST 1234 5698 7654 32"
    assert _found(DETECTOR, f"{first} {second}", "IBAN") == [first, second]


def test_iban_after_an_iban_shaped_word_is_detected() -> None:
    iban = "ES91 2100 0418 4502 0005 1332"
    assert _found(DETECTOR, f"Ref XX12 {iban}", "IBAN") == [iban]


@pytest.mark.parametrize(
    "text",
    [
        "Card 4111 1111 1111 1111 12/27",
        "Card 4111 1111 1111 1111 123",
        "Card 4111-1111-1111-1111-99",
    ],
    ids=["expiry", "cvv", "hyphenated-suffix"],
)
def test_card_followed_by_more_digit_groups_is_detected(text: str) -> None:
    assert [value.replace("-", " ") for value in _found(DETECTOR, text, "CREDIT_CARD")] == [
        "4111 1111 1111 1111"
    ]


def test_card_after_an_unrelated_digit_group_is_detected() -> None:
    assert _found(DETECTOR, "Ref 98 4111 1111 1111 1111", "CREDIT_CARD") == ["4111 1111 1111 1111"]


def test_digits_followed_by_a_word_are_not_a_national_id() -> None:
    assert _found(DETECTOR, "order 12345678 Zebra", "NATIONAL_ID") == []


def test_enabled_types_restricts_the_output() -> None:
    text = "juan.perez@example.com ES91 2100 0418 4502 0005 1332 +34 612 345 678 192.0.2.44"
    spans = RuleDetector(enabled_types={"EMAIL"}).detect(text)
    assert [(span.entity_type, text[span.start : span.end]) for span in spans] == [
        ("EMAIL", "juan.perez@example.com")
    ]


def test_url_is_off_by_default_and_excludes_trailing_punctuation_when_enabled() -> None:
    text = "See https://example.com/a?b=1."
    assert "URL" not in DEFAULT_RULE_TYPES
    assert _found(DETECTOR, text, "URL") == []
    assert _found(WITH_URL, text, "URL") == ["https://example.com/a?b=1"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Docs at http://example.org/x?!", "http://example.org/x"),
        ("(see https://example.com/path)", "https://example.com/path"),
        ('href="https://example.com/a"', "https://example.com/a"),
        ("Go to https://example.com/a;b, then", "https://example.com/a;b"),
    ],
)
def test_url_boundaries(text: str, expected: str) -> None:
    assert _found(WITH_URL, text, "URL") == [expected]


def test_unknown_type_in_enabled_types_raises_without_echoing_it() -> None:
    with pytest.raises(ValueError, match="unknown") as excinfo:
        RuleDetector(enabled_types={"EMAIL", "PASSPORT"})
    assert "PASSPORT" not in str(excinfo.value)


@pytest.mark.parametrize("regions", [(), ("XX",), "ES"], ids=["empty", "unknown", "bare-string"])
def test_invalid_phone_regions_raise(regions: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="region"):
        RuleDetector(phone_regions=regions)


def test_phone_regions_may_be_empty_when_phone_is_disabled() -> None:
    detector = RuleDetector(enabled_types={"EMAIL"}, phone_regions=())
    assert _found(detector, "Call 612 345 678 or juan.perez@example.com") == [
        "juan.perez@example.com"
    ]


def test_national_phone_format_needs_the_region() -> None:
    text = "Call 612 345 678 now"
    assert _found(RuleDetector(phone_regions=("ES",)), text, "PHONE") == ["612 345 678"]
    assert _found(RuleDetector(phone_regions=("US",)), text, "PHONE") == []


def test_phone_is_found_after_more_invalid_candidates_than_the_library_default_budget() -> None:
    text = "1;" * 70_000 + " +34 612 345 678"
    detector = RuleDetector(enabled_types={"PHONE"}, phone_regions=("ES",))
    assert _found(detector, text) == ["+34 612 345 678"]


def test_pem_block_spanning_three_lines_is_one_secret() -> None:
    pem = _pem()
    text = f"key:\n{pem}\nthanks"
    assert pem.count("\n") == 2
    assert _found(DETECTOR, text) == [pem]


def test_each_pem_block_ends_at_its_own_end_line() -> None:
    text = f"{_pem()}\nbetween\n{_pem()}"
    assert _found(DETECTOR, text, "SECRET") == [_pem(), _pem()]


def test_pem_key_without_end_line_is_a_secret_up_to_its_last_base64_line() -> None:
    # Slice 14 reverses the v0.2.0 expectation, which forwarded such a key unchanged.
    assert _found(DETECTOR, f"{PEM_BEGIN}\n{PEM_BODY}\n", "SECRET") == [f"{PEM_BEGIN}\n{PEM_BODY}"]


@pytest.mark.parametrize(
    "text",
    [
        "task-specific-fine-tuning-approach",
        "risk-assessment-framework-v2",
        "desk-sharing_policy_2024",
    ],
)
def test_key_prefix_inside_a_word_is_not_a_secret(text: str) -> None:
    assert _found(DETECTOR, text, "SECRET") == []


@pytest.mark.parametrize(
    "text",
    [
        "Use std::vector in C++.",
        "map :: Int -> Int",
        "Doors open at 10:30:45.",
        "MAC 00:1A:2B:3C:4D:5E",
        "Version 1.2.3.4.5 is out.",
        "Ratio 1:2:3 and A:B:C",
    ],
)
def test_ip_lookalikes_are_not_ip_addresses(text: str) -> None:
    assert _found(DETECTOR, text, "IP_ADDRESS") == []


@pytest.mark.parametrize(
    "value", ["::1", "fe80::1ff:fe23:4567:890a", "::ffff:192.0.2.1", "1:2:3:4:5:6:7::"]
)
def test_ipv6_forms_are_detected(value: str) -> None:
    assert value in _found(DETECTOR, f"Host {value}, ok", "IP_ADDRESS")


@pytest.mark.parametrize("text", NO_IDENTIFIERS)
def test_sentences_without_identifiers_produce_no_spans(text: str) -> None:
    assert DETECTOR.detect(text) == []
    assert WITH_URL.detect(text) == []


def test_detect_is_deterministic() -> None:
    text = " ".join(value for _, value in VALID)
    assert DETECTOR.detect(text) == DETECTOR.detect(text)


@pytest.mark.parametrize(
    ("entity_type", "text"),
    [
        # The two cases that were quadratic with the starting-point patterns are sized so that a
        # regression takes well over the bound (about 30 s locally, longer in CI).
        ("EMAIL", "%" * 300_000),
        ("EMAIL", "a." * 50_000),
        ("EMAIL", "a@" * 50_000),
        ("EMAIL", "a'" * 50_000),
        ("EMAIL", "\u00e9." * 50_000),
        ("EMAIL", "\u00e9@" * 50_000),
        # Validated rules retry every candidate start, so they get smaller inputs.
        ("IBAN", "ES12 " * 8_000),
        ("CREDIT_CARD", "1 " * 20_000),
        ("CREDIT_CARD", "1-" * 20_000),
        ("NATIONAL_ID", "12.345." * 20_000),
        ("NATIONAL_ID", "ssn " * 30_000),
        ("NATIONAL_ID", "SSN" + " " * 100_000),
        ("TAX_ID", "B-58.818." * 20_000),
        ("IP_ADDRESS", "1." * 50_000),
        ("IP_ADDRESS", "a:" * 50_000),
        ("SECRET", (PEM_BEGIN + "\n") * 24_000),
        # Sized so that searching for an END line again after every BEGIN line, which is
        # quadratic, takes over a minute locally.
        ("SECRET", (PEM_BEGIN + "\n") * 100_000),
        ("SECRET", PEM_BEGIN + "\nA" * 100_000),
        ("SECRET", (PEM_BEGIN + "\nAAAA ") * 20_000),
        ("SECRET", "sk-" * 33_000),
        ("SECRET", "ey" + "J." * 50_000),
        ("URL", "http://" * 15_000),
    ],
    ids=lambda value: value if len(value) < 20 else repr(value[:6]),
)
def test_adversarial_input_is_scanned_in_linear_time(entity_type: str, text: str) -> None:
    detector = RuleDetector(enabled_types={entity_type}, phone_regions=())
    started = time.perf_counter()
    detector.detect(text)
    # Linear scanning takes at most about 2 s here on a slow CI runner with coverage tracing (the
    # card cases); a quadratic scan takes over 30 s. The bound sits between the two.
    assert time.perf_counter() - started < 15.0


_PIECES = st.sampled_from(
    [value for _, value in VALID + INVALID]
    + [" ", ".", "-", "\n", "@", ":", "'", "\u00e9", "SSN ", PEM_BEGIN, "\\n", "https://"]
)
_TEXTS = st.one_of(st.text(), st.lists(_PIECES | st.text(max_size=3), max_size=8).map("".join))


@given(_TEXTS)
def test_detect_returns_well_formed_sorted_spans_for_arbitrary_text(text: str) -> None:
    spans = WITH_URL.detect(text)
    assert spans == sorted(spans, key=lambda span: (span.start, span.end))
    for span in spans:
        assert 0 <= span.start < span.end <= len(text)
        assert (span.source, span.priority) == ("rules", PRIORITY_RULE)
        assert span.entity_type in DEFAULT_RULE_TYPES | {"URL"}


# Found in slice 11, with the evaluation corpus -----------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "es91 2100 0418 4502 0005 1332",
        "es9121000418450200051332",
        "gb82 west 1234 5698 7654 32",
        "De89 3704 0044 0532 0130 00",
    ],
)
def test_iban_in_lower_or_mixed_case_is_detected(value: str) -> None:
    assert _found(DETECTOR, f"Contact: {value}.", "IBAN") == [value]


def test_lower_case_iban_with_wrong_checksum_is_not_detected() -> None:
    assert _found(DETECTOR, "Contact: es91 2100 0418 4502 0005 1333.", "IBAN") == []


def test_lower_case_words_after_a_lower_case_iban_are_not_part_of_it() -> None:
    text = "es91 2100 0418 4502 0005 1332 para pagar esto"
    assert _found(DETECTOR, text, "IBAN") == ["es91 2100 0418 4502 0005 1332"]


def test_iban_shaped_lower_case_words_are_not_an_iban() -> None:
    assert _found(DETECTOR, "ab12 this that with them", "IBAN") == []


@pytest.mark.parametrize("isbn", ["978-0-00-000004-0", "9790000000049", "979 0 00 000004 9"])
def test_isbn_13_that_passes_luhn_is_not_a_card(isbn: str) -> None:
    assert luhn_valid(isbn)
    assert _found(DETECTOR, f"ISBN {isbn}.", "CREDIT_CARD") == []


@pytest.mark.parametrize("value", ["4222222222222", "9780000000000007"])
def test_other_13_digit_cards_and_long_cards_with_an_isbn_prefix_are_cards(value: str) -> None:
    assert _found(DETECTOR, f"Card {value}.", "CREDIT_CARD") == [value]


@pytest.mark.parametrize(
    ("region", "text"),
    [
        ("US", "Order ORD-2013-015180 has shipped."),
        ("US", "Factura 2024-001234 pagada."),
        ("US", "Call 415 55 52 671 now"),
        ("ES", "Llama al 6123 456 78 ya"),
    ],
)
def test_national_number_grouped_against_its_country_format_is_not_a_phone(
    region: str, text: str
) -> None:
    detector = RuleDetector(enabled_types={"PHONE"}, phone_regions=(region,))
    assert detector.detect(text) == []


@pytest.mark.parametrize(
    ("region", "value"),
    [
        ("ES", "612 345 678"),
        ("ES", "612 34 56 78"),
        ("ES", "612-345-678"),
        ("ES", "612.34.56.78"),
        ("ES", "612345678"),
        ("ES", "91 123 45 67"),
        ("ES", "911 234 567"),
        ("US", "(415) 555-2671"),
        ("US", "(415)555-2671"),
        ("US", "415-555-2671"),
        ("US", "415.555.2671"),
        ("US", "415 555 2671"),
        ("US", "4155552671"),
        ("US", "1-415-555-2671"),
    ],
)
def test_national_number_grouped_as_its_country_writes_it_is_a_phone(
    region: str, value: str
) -> None:
    detector = RuleDetector(enabled_types={"PHONE"}, phone_regions=(region,))
    assert _found(detector, f"Tel {value} ok") == [value]


@pytest.mark.parametrize(
    ("region", "value"),
    [
        ("ES", "+33 123 456 789"),
        ("ES", "+34 61 234 56 78"),
        ("ES", "+49 3012 345678"),
        ("ES", "0034 6123 45678"),
        ("ES", "+1 415 5552671"),
        ("US", "011 33 123 456 789"),
    ],
)
def test_number_with_an_international_prefix_is_a_phone_in_any_grouping(
    region: str, value: str
) -> None:
    detector = RuleDetector(enabled_types={"PHONE"}, phone_regions=(region,))
    assert _found(detector, f"Tel {value} ok") == [value]


# Slice 14: rule coverage gaps -------------------------------------------------------------------


@pytest.mark.parametrize("text", ["9.12.345.678-Z", "112.345.678-Z", "12.3456.78-Z"])
def test_digits_inside_a_longer_dotted_number_are_not_a_national_id(text: str) -> None:
    assert _found(DETECTOR, f"Ref {text} ok", "NATIONAL_ID") == []


@pytest.mark.parametrize("text", ["B-58.818.501.2", "B-5881.8501"])
def test_cif_with_dots_elsewhere_is_not_a_tax_id(text: str) -> None:
    assert _found(DETECTOR, f"Ref {text} ok", "TAX_ID") == []


@given(st.integers(0, 99_999_999), st.sampled_from(string.ascii_uppercase))
def test_dotted_dni_with_a_hyphen_is_detected_only_with_its_control_letter(
    number: int, letter: str
) -> None:
    digits = f"{number:08d}"
    value = f"{digits[:2]}.{digits[2:5]}.{digits[5:]}-{letter}"
    expected = [value] if es_dni_valid(digits + letter) else []
    assert _found(DETECTOR, f"DNI {value}.", "NATIONAL_ID") == expected


@given(
    st.integers(0, 9_999_999),
    st.sampled_from("ABCDEFGHJNPQRSUVW"),
    st.sampled_from(["", "-", " "]),
)
def test_cif_with_its_control_digit_is_detected_in_every_written_form(
    number: int, letter: str, separator: str
) -> None:
    digits = f"{number:07d}"
    control = next(digit for digit in string.digits if es_cif_valid(letter + digits + digit))
    plain = digits + control
    for value in (
        f"{letter}{separator}{plain}",
        f"{letter}{separator}{plain[:2]}.{plain[2:5]}.{plain[5:]}",
    ):
        assert _found(DETECTOR, f"CIF {value}.", "TAX_ID") == [value]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Write to 'juan.perez@example.com' now", "juan.perez@example.com"),
        ("See juan.perez@example.com's inbox", "juan.perez@example.com"),
    ],
)
def test_quotes_and_apostrophes_around_an_email_are_not_part_of_it(
    text: str, expected: str
) -> None:
    assert _found(DETECTOR, text, "EMAIL") == [expected]


@pytest.mark.parametrize("text", ["Write to '@example.com", "Write to o'@example.com"])
def test_apostrophe_at_the_end_of_a_local_part_is_not_an_email(text: str) -> None:
    assert _found(DETECTOR, text, "EMAIL") == []


def test_email_between_words_of_a_script_written_without_spaces_is_detected() -> None:
    text = "\u30e1\u30fc\u30eb\u306fjuan@example.com\u3067\u3059"
    (surface,) = _found(DETECTOR, text, "EMAIL")
    assert "juan@example.com" in surface


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("SSN: 078051120", "078051120"),
        ("SSN 078 05 1120", "078 05 1120"),
        ('{"ssn": "078051120"}', "078051120"),
        ("Social Security number of the client: 078051120", "078051120"),
        ("ss# 078051120", "078051120"),
        ("user_ssn=078051120", "078051120"),
        ("SSNs: 078051120", "078051120"),
        ("SSN" + " " * 25 + "078051120", "078051120"),
    ],
)
def test_ssn_without_hyphens_after_a_keyword_is_a_national_id(text: str, number: str) -> None:
    spans = [span for span in DETECTOR.detect(text) if span.entity_type == "NATIONAL_ID"]
    assert [(text[span.start : span.end], span.score) for span in spans] == [(number, 0.95)]


@pytest.mark.parametrize(
    "text",
    [
        "078051120",
        "SSN (2024): 078051120",
        "SSN\n078051120",
        "SSN" + " " * 26 + "078051120",
        "classn 078051120",
        "ssnrecord 078051120",
        "SSN: 000051120",
        "SSN: 0780511201",
        "SSN: 078-051120",
    ],
)
def test_bare_nine_digits_without_a_keyword_right_before_are_not_a_national_id(text: str) -> None:
    assert _found(DETECTOR, text, "NATIONAL_ID") == []


def test_each_bare_ssn_needs_its_own_keyword() -> None:
    assert _found(DETECTOR, "SSNs: 078051120, 219099999", "NATIONAL_ID") == ["078051120"]


_PLAUSIBLE_SSNS = st.builds(
    lambda area, group, serial: f"{area:03d}{group:02d}{serial:04d}",
    st.integers(1, 899).filter(lambda area: area != 666),
    st.integers(1, 99),
    st.integers(1, 9999),
)


@given(_PLAUSIBLE_SSNS)
def test_any_plausible_ssn_after_the_keyword_is_detected(digits: str) -> None:
    assert us_ssn_plausible(digits)
    assert _found(DETECTOR, f"SSN: {digits}", "NATIONAL_ID") == [digits]


@pytest.mark.parametrize("text", ["Ref 782-77-8477 on file", "Ref (782-77-8477) on file"])
def test_ssn_that_is_also_a_spanish_phone_is_resolved_as_a_national_id(text: str) -> None:
    spans = DETECTOR.detect(text)
    assert "PHONE" in {span.entity_type for span in spans}
    assert [
        (span.entity_type, text[span.start : span.end]) for span in resolve_overlaps(spans)
    ] == [("NATIONAL_ID", "782-77-8477")]


_URL_WITH_A_SECRET = "https://example.com/hook?key=" + "sk-" + "proj_" + "Ab1-" * 6


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/reset?user=juan.perez@example.com&token=abc123",
        "https://juan:pw@example.com/x",
        _URL_WITH_A_SECRET,
        "http://192.0.2.44/status",
        "https://example.com/pay/4111-1111-1111-1111",
    ],
    ids=["email", "credentials", "secret", "ip", "card"],
)
def test_rule_spans_inside_a_url_are_dropped_when_url_is_enabled(url: str) -> None:
    text = f"See {url} now"
    assert [(span.entity_type, text[span.start : span.end]) for span in WITH_URL.detect(text)] == [
        ("URL", url)
    ]


def test_email_inside_a_url_is_an_email_when_url_is_disabled() -> None:
    text = "See https://example.com/reset?user=juan.perez@example.com&token=abc123 now"
    assert _found(DETECTOR, text) == ["juan.perez@example.com"]


def test_value_that_only_partly_overlaps_a_url_still_wins_the_overlap() -> None:
    text = "Card at https://example.com/pay/4111 1111 1111 1111 now"
    spans = resolve_overlaps(WITH_URL.detect(text))
    assert [(span.entity_type, text[span.start : span.end]) for span in spans] == [
        ("CREDIT_CARD", "4111 1111 1111 1111")
    ]


_URL_TEXTS = st.lists(
    st.sampled_from(
        [
            "https://example.com/",
            "juan.perez@example.com",
            "192.0.2.44",
            "4111 1111 1111 1111",
            "4111-1111-1111-1111",
            "?u=",
            "&",
            "/",
            " ",
            "\n",
        ]
    ),
    max_size=10,
).map("".join)


@given(_URL_TEXTS)
def test_with_url_enabled_only_spans_inside_a_url_are_dropped(text: str) -> None:
    with_url = WITH_URL.detect(text)
    urls = [span for span in with_url if span.entity_type == "URL"]

    def inside_a_url(span: Span) -> bool:
        return any(url.start <= span.start and span.end <= url.end for url in urls)

    others = [span for span in with_url if span.entity_type != "URL"]
    assert not any(inside_a_url(span) for span in others)
    assert others == [span for span in DETECTOR.detect(text) if not inside_a_url(span)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"{PEM_BEGIN}\n{PEM_BODY}\n{PEM_BODY}\nQUJD\nwhy does it fail?",
            f"{PEM_BEGIN}\n{PEM_BODY}\n{PEM_BODY}\nQUJD",
        ),
        (
            f"{PEM_BEGIN}\r\n{PEM_BODY}  \r\nQUJD\r\nThanks, bye",
            f"{PEM_BEGIN}\r\n{PEM_BODY}  \r\nQUJD",
        ),
        (
            f"{RSA_PEM_BEGIN}\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0A1B2C3D\n\n"
            f"{PEM_BODY}\nQUJD==\n\nThanks",
            f"{RSA_PEM_BEGIN}\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0A1B2C3D\n\n"
            f"{PEM_BODY}\nQUJD==",
        ),
        (
            '{"key": "' + PEM_BEGIN + "\\n" + PEM_BODY + "\\nQUJD\\/REVG" + '"}',
            PEM_BEGIN + "\\n" + PEM_BODY + "\\nQUJD\\/REVG",
        ),
        (f"{PEM_BEGIN}\nhello there\n{PEM_BODY}", PEM_BEGIN),
        (f"Key: {PEM_BEGIN}\n{PEM_BODY}", f"{PEM_BEGIN}\n{PEM_BODY}"),
    ],
    ids=["prose-after", "crlf", "encrypted-headers", "json-escaped", "prose-first", "end-of-text"],
)
def test_pem_key_without_end_line_ends_where_its_base64_lines_end(text: str, expected: str) -> None:
    assert _found(DETECTOR, text, "SECRET") == [expected]


def test_two_pem_keys_without_end_lines_are_two_secrets() -> None:
    text = f"{PEM_BEGIN}\n{PEM_BODY}\nsee above, and\n{PEM_BEGIN}\nQUJD"
    assert _found(DETECTOR, text, "SECRET") == [f"{PEM_BEGIN}\n{PEM_BODY}", f"{PEM_BEGIN}\nQUJD"]


def test_pem_begin_line_pairs_with_the_first_end_line_after_it() -> None:
    text = f"{PEM_BEGIN}\n{PEM_BODY}\nand the full one:\n{_pem()}"
    assert _found(DETECTOR, text, "SECRET") == [text]
