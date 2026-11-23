"""Organisation words: the words of an ORG surface that can name the organisation on their own
(architecture §4.3)."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from aliasgate.common_words import COMMON_WORDS
from aliasgate.mapping import normalize_key
from aliasgate.names import PARTICLES, unpunctuated

# Legal forms, as keys with every "." removed: "S.L.", "S.L" and "SL" are one legal form.
_LEGAL_FORMS: Final = """
ag bv cia cía co corp corporation company gmbh inc incorporated limited llc llp lp ltd ltda nv
plc pty sa sad sarl sas sat sau scom scoop scp sl sll slne slu srl
"""

# Words that say what an organisation does or is. A missing word counts as distinctive, which
# over-replaces and never leaks. The Spanish group holds every word of Faker's es_ES company
# provider ("farmaceútica" is its spelling).
_GENERIC_ENGLISH: Final = """
accounting advisors advisory agency airlines airways analytics apparel architects associates
automotive aviation bakery bancorp bank bankers brands brewery brothers builders capital
chemicals clinic communications construction consultants consulting design developers
development digital distribution electric electronics energy engineering enterprises
entertainment equipment estates farms finance financial fitness foods foundation freight
furniture games global group health healthcare holdings homes hospitality hotel hotels
industries institute insurance international investments laboratories labs legal lighting
logistics management manufacturing marine marketing media medical metals mining motors networks
partners pharma pharmaceuticals plastics printing productions properties publishing realty
records research resources restaurants retail robotics security services shipping software
solutions sons studio studios supplies supply systems technologies technology telecom textiles
trading transport travel trust university ventures wholesale
"""
_GENERIC_SPANISH: Final = """
abogados academia agencia alimentación alimentos arquitectos asesores asesoría asociación
asociados automoción avanzadas banca banco bodegas castellana centro clínica colegio comercial
comercializadora compañía construcción construcciones consultores consultoría cooperativa
corporación desarrollo desarrollos despacho distribuciones distribuidora ediciones editorial
electricidad energía energías española españolas español españoles estudio fábrica familia
farmacéutica farmaceútica finanzas fundación gestión gestoría global globales grupo hermanos
hnos hotel hoteles iberia ibérica ibéricos industrias informática infraestructuras ingeniería
inmobiliaria instalaciones integrales inteligentes internacional internacionales inversiones
laboratorios levante logística manufacturas mediterráneo minería noroeste norte privada
producciones promociones restauración seguros servicios sistemas sociedad soluciones suministros
supermercados sur talleres tecnologías telecomunicaciones transportes viajes
"""

LEGAL_FORMS: Final[frozenset[str]] = frozenset(_LEGAL_FORMS.split())
GENERIC_WORDS: Final[frozenset[str]] = frozenset(
    normalize_key(word) for word in (*_GENERIC_ENGLISH.split(), *_GENERIC_SPANISH.split())
)
CONNECTORS: Final[frozenset[str]] = PARTICLES | {"of", "the", "for", "und", "et"}


class OrgWordKind(Enum):
    """What a word of an organisation's name is."""

    DISTINCTIVE = auto()
    GENERIC = auto()
    COMMON = auto()
    LEGAL = auto()
    OTHER = auto()  # empty, a connector, or shorter than the minimum


_CONTENT: Final = frozenset((OrgWordKind.DISTINCTIVE, OrgWordKind.GENERIC, OrgWordKind.COMMON))


@dataclass(frozen=True, slots=True)
class OrgWord:
    """One word of an ORG surface: its key, the word as written without the punctuation at its
    ends, and its kind."""

    key: str
    word: str  # as written, without the punctuation at its ends
    kind: OrgWordKind

    @property
    def content(self) -> bool:
        """Whether the word is distinctive, generic or common."""
        return self.kind in _CONTENT


def org_words(surface: str, *, min_length: int) -> tuple[OrgWord, ...]:
    """Every word of an ORG surface, in order, with its key, the word as written without the
    punctuation at its ends, and its kind."""
    return tuple(word for word, _, _ in _words(surface, min_length))


def bare_name(surface: str, *, min_length: int) -> str | None:
    """The surface from its first to its last content word, as written, without the punctuation
    at the outer ends of those two words, when it has at least two content words; else None."""
    content = [(start, end) for word, start, end in _words(surface, min_length) if word.content]
    if len(content) < 2:
        return None
    return surface[content[0][0] : content[-1][1]]


def _words(surface: str, min_length: int) -> list[tuple[OrgWord, int, int]]:
    """Each word of `surface` with the start and end of its unpunctuated part in `surface`."""
    words = []
    position = 0
    for written in surface.split():
        position = surface.index(written, position)
        start, end = unpunctuated(written)
        word = written[start:end]
        words.append(
            (
                OrgWord(normalize_key(word), word, _kind(normalize_key(word), min_length)),
                position + start,
                position + end,
            )
        )
        position += len(written)
    return words


def _kind(key: str, min_length: int) -> OrgWordKind:
    if not key:
        return OrgWordKind.OTHER
    if key.replace(".", "") in LEGAL_FORMS:
        return OrgWordKind.LEGAL
    if key in CONNECTORS or len(key) < min_length:
        return OrgWordKind.OTHER
    if key in GENERIC_WORDS:
        return OrgWordKind.GENERIC
    if key in COMMON_WORDS:
        return OrgWordKind.COMMON
    return OrgWordKind.DISTINCTIVE
