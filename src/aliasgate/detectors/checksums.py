"""Checksum and plausibility checks for structured identifiers.

Every function ignores spaces and hyphens, accepts letters in either case, only accepts ASCII
digits and letters, returns False for malformed input and never raises.
"""

import re
from typing import Final

_DNI_LETTERS: Final = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIE_PREFIX_DIGIT: Final = {"X": "0", "Y": "1", "Z": "2"}
_CIF_CONTROL_LETTERS: Final = "JABCDEFGHI"

_CARD: Final = re.compile(r"[0-9]{13,19}")
_IBAN: Final = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}")
_DNI: Final = re.compile(r"([0-9]{8})([A-Z])")
_NIE: Final = re.compile(r"([XYZ])([0-9]{7})([A-Z])")
_CIF: Final = re.compile(r"[ABCDEFGHJNPQRSUVW]([0-9]{7})([0-9A-J])")
_SSN: Final = re.compile(r"([0-9]{3})([0-9]{2})([0-9]{4})")


def _compact(value: str) -> str:
    """Remove spaces and hyphens and upper-case; non-ASCII input becomes "" (always invalid)."""
    compact = value.replace(" ", "").replace("-", "")
    # Checked before upper-casing: "\u017f".upper() == "S" and "ß".upper() == "SS".
    return compact.upper() if compact.isascii() else ""


def luhn_valid(number: str) -> bool:
    """True if `number` has 13 to 19 digits and passes the Luhn mod 10 check."""
    digits = _compact(number)
    if _CARD.fullmatch(digits) is None:
        return False
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def iban_valid(iban: str) -> bool:
    """True if `iban` has 15 to 34 characters and its ISO 13616 mod-97 remainder is 1."""
    compact = _compact(iban)
    if _IBAN.fullmatch(compact) is None:
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def _dni_letter_matches(digits: str, letter: str) -> bool:
    return _DNI_LETTERS[int(digits) % 23] == letter


def es_dni_valid(value: str) -> bool:
    """True if `value` is 8 digits followed by the matching Spanish DNI control letter."""
    match = _DNI.fullmatch(_compact(value))
    return match is not None and _dni_letter_matches(match[1], match[2])


def es_nie_valid(value: str) -> bool:
    """True if `value` is X, Y or Z, 7 digits and the matching Spanish NIE control letter."""
    match = _NIE.fullmatch(_compact(value))
    return match is not None and _dni_letter_matches(
        _NIE_PREFIX_DIGIT[match[1]] + match[2], match[3]
    )


def es_cif_valid(value: str) -> bool:
    """True if `value` is a Spanish CIF whose control character is the matching digit or letter."""
    match = _CIF.fullmatch(_compact(value))
    if match is None:
        return False
    digits = [int(char) for char in match[1]]
    even = digits[1] + digits[3] + digits[5]
    odd = sum(sum(divmod(digit * 2, 10)) for digit in digits[0::2])
    control = (10 - (even + odd) % 10) % 10
    return match[2] in (str(control), _CIF_CONTROL_LETTERS[control])


def us_ssn_plausible(value: str) -> bool:
    """True if `value` is a 9-digit US SSN outside the never-issued areas, groups and serials."""
    match = _SSN.fullmatch(_compact(value))
    if match is None:
        return False
    area, group, serial = match.groups()
    return area not in ("000", "666") and area[0] != "9" and group != "00" and serial != "0000"
