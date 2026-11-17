from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aliasgate.detectors.checksums import (
    es_cif_valid,
    es_dni_valid,
    es_nie_valid,
    iban_valid,
    luhn_valid,
    us_ssn_plausible,
)

DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"

VECTORS: list[tuple[Callable[[str], bool], str, bool]] = [
    (iban_valid, "ES91 2100 0418 4502 0005 1332", True),
    (iban_valid, "ES9121000418450200051332", True),
    (iban_valid, "DE89 3704 0044 0532 0130 00", True),
    (iban_valid, "GB82 WEST 1234 5698 7654 32", True),
    (iban_valid, "ES91 2100 0418 4502 0005 1333", False),
    (luhn_valid, "4111 1111 1111 1111", True),
    (luhn_valid, "5555-5555-5555-4444", True),
    (luhn_valid, "378282246310005", True),
    (luhn_valid, "4111 1111 1111 1112", False),
    (es_dni_valid, "12345678Z", True),
    (es_dni_valid, "12345678A", False),
    (es_nie_valid, "X1234567L", True),
    (es_nie_valid, "X1234567A", False),
    (us_ssn_plausible, "078-05-1120", True),
    (us_ssn_plausible, "000-12-3456", False),
    (us_ssn_plausible, "666-12-3456", False),
    (us_ssn_plausible, "900-12-3456", False),
    (es_cif_valid, "A58818501", True),
    (es_cif_valid, "A58818502", False),
]

FUNCTIONS = [luhn_valid, iban_valid, es_dni_valid, es_nie_valid, es_cif_valid, us_ssn_plausible]


@pytest.mark.parametrize(
    ("function", "value", "expected"),
    VECTORS,
    ids=[f"{function.__name__}-{value}" for function, value, _ in VECTORS],
)
def test_shared_vector(function: Callable[[str], bool], value: str, expected: bool) -> None:
    assert function(value) is expected


@pytest.mark.parametrize("function", FUNCTIONS, ids=[f.__name__ for f in FUNCTIONS])
@pytest.mark.parametrize(
    "value",
    ["", "abc", "1" * 100, "A" * 100, " - ", "\u0661\u0662\u0663", "\u00b2" * 9],
    ids=["empty", "abc", "100-digits", "100-letters", "separators", "arabic-digits", "superscript"],
)
def test_malformed_input_returns_false_without_raising(
    function: Callable[[str], bool], value: str
) -> None:
    assert function(value) is False


@pytest.mark.parametrize(
    ("function", "value"),
    [
        (iban_valid, "es91 2100 0418 4502 0005 1332"),
        (iban_valid, "ES91-2100-0418-4502-0005-1332"),
        (luhn_valid, "4111-1111 1111-1111"),
        (es_dni_valid, "12345678z"),
        (es_dni_valid, "12345678-Z"),
        (es_nie_valid, "x-1234567-l"),
        (es_cif_valid, "a 5881850 1"),
        (us_ssn_plausible, "078 05 1120"),
        (us_ssn_plausible, "078051120"),
    ],
)
def test_spaces_hyphens_and_case_are_ignored(function: Callable[[str], bool], value: str) -> None:
    assert function(value) is True


@pytest.mark.parametrize(
    ("function", "value"),
    [
        (luhn_valid, "\uff14\uff11\uff11\uff11" * 4),
        (es_dni_valid, "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18Z"),
        (iban_valid, "ES91 2100 0418 4502 0005 133\u0662"),
        (iban_valid, "E\u017f91 2100 0418 4502 0005 1332"),
    ],
    ids=["full-width-card", "full-width-dni", "arabic-digit-iban", "long-s-iban"],
)
def test_non_ascii_lookalikes_are_rejected(function: Callable[[str], bool], value: str) -> None:
    assert function(value) is False


@pytest.mark.parametrize("value", ["Y1234567X", "Z1234567R"])
def test_nie_prefixes_y_and_z_map_to_one_and_two(value: str) -> None:
    assert es_nie_valid(value) is True


@pytest.mark.parametrize("value", ["P2800000H", "P28000008"])
def test_cif_control_may_be_a_letter_or_a_digit(value: str) -> None:
    assert es_cif_valid(value) is True


@pytest.mark.parametrize("value", ["P2800000J", "I2800000H", "P2800000K"])
def test_cif_with_wrong_control_or_first_letter_is_invalid(value: str) -> None:
    assert es_cif_valid(value) is False


@pytest.mark.parametrize("value", ["078-00-1120", "078-05-0000", "999-05-1120"])
def test_ssn_with_excluded_group_serial_or_area_is_not_plausible(value: str) -> None:
    assert us_ssn_plausible(value) is False


@pytest.mark.parametrize("length", [12, 20])
def test_luhn_rejects_lengths_outside_13_to_19(length: int) -> None:
    prefix = "4" + "0" * (length - 2)
    assert not any(luhn_valid(prefix + str(digit)) for digit in range(10))


def _make_iban(country: str, bban: str) -> str:
    rearranged = bban + country + "00"
    remainder = int("".join(str(int(char, 36)) for char in rearranged)) % 97
    return f"{country}{98 - remainder:02d}{bban}"


@pytest.mark.parametrize(
    ("bban_length", "expected"), [(10, False), (11, True), (30, True), (31, False)]
)
def test_iban_length_must_be_15_to_34(bban_length: int, expected: bool) -> None:
    assert iban_valid(_make_iban("XK", "1" * bban_length)) is expected


@given(st.integers(min_value=0, max_value=99_999_999), st.sampled_from(DNI_LETTERS))
def test_only_the_computed_dni_letter_validates(number: int, letter: str) -> None:
    digits = f"{number:08d}"
    expected = DNI_LETTERS[number % 23]
    assert es_dni_valid(digits + expected) is True
    assert es_dni_valid(digits + expected.lower()) is True
    assert es_dni_valid(digits + letter) is (letter == expected)


@given(st.text(alphabet="0123456789", min_size=15, max_size=15))
def test_luhn_accepts_exactly_one_final_digit_for_any_15_digit_prefix(prefix: str) -> None:
    assert sum(luhn_valid(prefix + str(digit)) for digit in range(10)) == 1


@given(
    st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=2, max_size=2),
    st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=11, max_size=30),
)
def test_iban_with_computed_check_digits_validates(country: str, bban: str) -> None:
    assert iban_valid(_make_iban(country, bban)) is True


@given(st.text(max_size=40))
def test_checksums_never_raise(value: str) -> None:
    for function in FUNCTIONS:
        assert isinstance(function(value), bool)
