"""Generate the person mentions corpus in tests/eval/corpus/ (slices 15 and 17).

Each example names one or two persons, by their full name and by the shorter forms people use:
a first name, a surname, a title with a surname, part of a Spanish name with two surnames, one
part of a hyphenated name. Gold spans carry entity ids, so the evaluation can tell whether the
mentions of one person share a placeholder number and whether those of two persons do not. The
dictionary file holds the first full-name mention of every person (in `single`, the title or
initial with the surname), so that CI finds it and measures the shorter forms on their own.

The categories of slice 17 (`NEW_CATEGORIES`) are drawn after the other categories of both
languages, so that adding them left the earlier lines and dictionary entries as they were.

Deterministic: the fixed seeds below produce byte-identical files for a given Faker version.
Every name is synthetic: words come from Faker or from the fixed word lists below. This
generator shares no code with tools/generate_corpus.py, so changing one cannot change the
files of the other.

    uv run python tools/generate_person_corpus.py           # write the files
    uv run python tools/generate_person_corpus.py --check   # exit 1 if the committed files differ
"""

import argparse
import json
import random
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from faker import Faker

from aliasgate.mapping import normalize_key

DEFAULT_OUT: Final = Path(__file__).resolve().parent.parent / "tests" / "eval" / "corpus"
LANGUAGES: Final = {"en": ("en_US", 1201), "es": ("es_ES", 1202)}  # locale, seed
TERMS_FILE: Final = "persons.terms.yaml"
RENDERS: Final = 10  # per template
MAX_ATTEMPTS: Final = 1000  # draws per example before giving up
NEW_CATEGORIES: Final = ("single", "hyphen", "don")  # slice 17, drawn last

# A name word is drawn again if it is one of these (compared case-insensitively). Slice 16
# defines the engine's own lists; this generator keeps its own copy on purpose.
TITLES: Final = frozenset(
    (
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
        "prof",
        "sir",
        "sr",
        "sra",
        "srta",
        "don",
        "doña",
        "dña",
        "dra",
    )
)
PARTICLES: Final = frozenset(
    ("de", "del", "la", "las", "los", "y", "van", "von", "der", "den", "da", "dos", "san", "santa")
)

# Names that are also common words, for the categories `word` (surnames) and `sentence` (first
# names, with the gender their titles would take).
WORD_SURNAMES: Final = {
    "en": ("Brown", "Stone", "King", "Young", "Rice", "Bell"),
    "es": ("Mesa", "Plaza", "Torre", "Luna", "Vega", "Rubio"),
}
WORD_FIRST_NAMES: Final = {
    "en": {"Will": "male", "May": "female", "Mark": "male"},
    "es": {"Rosa": "female", "Luz": "female", "Paz": "female"},
}
# The common-word use of each listed name, for the first and the second template of its
# category: a noun phrase in lower case for `word`, a whole sentence starting with it for
# `sentence`.
COMMON: Final = {
    "Brown": ("the brown box", "the brown envelope"),
    "Stone": ("the stone counter", "the stone tiles"),
    "King": ("the king suite", "the king bed"),
    "Young": ("the young driver", "the young intern"),
    "Rice": ("the rice order", "the rice delivery"),
    "Bell": ("the bell at reception", "the bell on the gate"),
    "Mesa": ("la mesa de la sala tres", "la mesa del despacho"),
    "Plaza": ("la plaza de garaje", "la plaza del curso"),
    "Torre": ("la torre del servidor", "la torre de oficinas"),
    "Luna": ("la luna del escaparate", "la luna del coche"),
    "Vega": ("la finca de la vega", "el camino de la vega"),
    "Rubio": ("el chico rubio de recepción", "el repartidor rubio"),
    "Will": ("Will you check it?", "Will the courier come today?"),
    "May": ("May I ask for a copy?", "May we close the ticket?"),
    "Mark": ("Mark it as urgent.", "Mark the invoice as paid, please."),
    "Rosa": ("Rosa es el color de la portada.", "Rosa o azul, al cliente le da igual."),
    "Luz": ("Luz no hay en la segunda planta desde ayer.", "Luz verde para el envío, por cierto."),
    "Paz": (
        "Paz es lo que pide el vecino del tercero.",
        "Paz y paciencia, el pedido llega el lunes.",
    ),
}
ORG_SUFFIXES: Final = {
    "en": ("Logistics", "Consulting", "Partners", "Foods"),
    "es": ("Asesores", "Logística", "Consultores", "Hermanos"),
}
# Titles agree with the person's gender. Spanish ones carry their article, so Spanish
# templates use them inside a sentence only.
_TITLE_FORMS: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "en": {"female": ("Ms.", "Mrs.", "Dr."), "male": ("Mr.", "Dr.")},
    "es": {"female": ("la Sra.", "la Dra."), "male": ("el Sr.", "el Dr.")},
}
_GENDERS: Final = ("female", "male")
_NAME_WORD: Final = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)?")
_PLAIN_WORD: Final = re.compile(r"[^\W\d_]+")
# The English first name of the category `don`, which also starts the common word "Don't".
DON: Final = "Don"
_NOT_T: Final = "(?!['\u2019]t(?!\\w))"

# Templates -------------------------------------------------------------------------------------
# Slots: {a} and {b} are the full names of two persons; {a.first}, {a.last}, {a.middle},
# {a.last2} (Spanish second surname), {a.first_last}, {a.middle_last} and {a.surnames} (both
# Spanish surnames) are shorter forms; {a.first|lower} gives a form in lower case. Each of these
# is a PERSON gold span. {a.title} is the person's title, outside the span. {org} is an
# organisation named after the surname of {a} (an ORG gold span), and {common} is the
# common-word use of the listed name of the example. Slice 17 adds the PERSON forms
# {a.initial_last} (the first name's initial and the surname), {a.first_head} (the first part of
# a hyphenated first name) and {a.last_tail} (the last part of a hyphenated surname), the
# Spanish title {a.don} (`don` or `doña`, outside the span), and {x|capital}, which writes the
# first letter of a slot in upper case.

TEMPLATES: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "en": {
        "full": (
            "Refund request: {a} approved the refund for {b} on Friday.",
            "Meeting notes: {a} and {b} reviewed the renewal. Next step: {b} sends the signed "
            "copy to {a}.",
        ),
        "first": (
            "{a} opened the ticket on Monday. {a.first} will call back tomorrow.",
            "Hi team, {a} and {b} joined the call. {a.first} asked for the invoice, and "
            "{b.first} will send the form.",
        ),
        "last": (
            "{a} reported the issue. After the update, {a.last} confirmed the address.",
            "CRM note: spoke with {a} about the renewal. {a.last} wants a discount; {b} from "
            "procurement agrees, and {b.last} will sign.",
        ),
        "title": (
            "{a} wrote to support on Tuesday. {a.title} {a.last} says the invoice is wrong.",
            "Email from {a}, forwarded to billing. Reply sent: Dear {a.title} {a.last}, we have "
            "corrected the amount.",
        ),
        "before": (
            "{a.first} called on Monday about the delay. Later, {a} sent the form.",
            "Chat: {a.last} is still waiting for the refund. Ticket owner: {a}.",
        ),
        "run": (
            "{a} signed the lease. {a.middle_last} paid the deposit, and {a.first_last} picked "
            "up the keys.",
            "Contract for {a}. The bank lists the holder as {a.first_last}; the ID card says "
            "{a.middle_last}.",
        ),
        "possessive": (
            "{a} sent the audit last week. {a.last}'s report is attached.",
            "Ticket update: {a} called again. We still need {a.last}'s signature before Friday.",
        ),
        "shared-last": (
            "{a}, the owner, and {b}, the co-owner, called. {b.first} paid and {a.first} left "
            "early. The owner, {a.title} {a.last}, asked for a receipt.",
            "Account note: {a} (holder) and {b} (authorised user) share the account. {a.first} "
            "changed the address; {b.first} did not. The holder, {a.last}, must confirm it.",
        ),
        "shared-first": (
            "{a}, the team lead, and {b}, the new hire, joined on Monday. {a.last} runs the "
            "onboarding and {b.last} shadows the team. {b.first}, the new hire, starts on the "
            "support desk.",
            "Contacts at the client: {a} (finance) and {b} (legal). {b.last} sent the draft and "
            "{a.last} approved the budget. {a.first} from finance asked for a call.",
        ),
        "other": (
            "{a} signed the contract. {b} from accounting, a different person, will countersign.",
            "{a} called about the order. Note: {b} at the warehouse is not the customer; {b} "
            "only packed it.",
        ),
        "word": (
            "{a} sent the parcel on Friday. {a.last} says there is a problem with {common}.",
            "Front desk note: {a} checked in this morning. {a.last} asked about {common} twice.",
        ),
        "sentence": (
            "{a} sent the file this morning. {common} {a.first} said it is urgent.",
            "Chat log: {a} asked for a callback. {common} {a.first} is still waiting for an "
            "answer.",
        ),
        "lower": (
            "{a} wrote earlier about the refund. thanks {a.first|lower}, got it",
            "chat: {a} joined the channel. ok {a.last|lower} will handle it, ping me later",
        ),
        "org": (
            "{a} works at {org}. {a.last} will send the quote on Monday.",
            "CRM note: {org} renewed the contract. Our contact there is {a}; {a.last} prefers "
            "email.",
        ),
        "single": (
            "{a.title} {a.last} reviewed the claim. {a.last} asked for the invoice.",
            "Ticket from {a.initial_last}: the parcel is late. {a.last} wants a refund.",
        ),
        "hyphen": (
            "{a} opened the ticket. {a.first_head} replied with the order number.",
            "{a} renewed the plan. {a.last_tail} asked for a copy.",
        ),
        "don": (
            "Don't close the ticket yet: {a} called about the invoice. {a.first} wants a copy.",
            "{a} sent the form. Don't worry, {a.last} will sign it today.",
        ),
    },
    # Spanish templates avoid "y" and "o" before a name, which would become "e" and "u" before
    # some names, and keep every word that agrees with a gender out of the fixed text.
    "es": {
        "full": (
            "Solicitud de reembolso: {a} aprobó el reembolso de {b} el viernes.",
            "Acta de la reunión: {a} revisó la renovación con {b}. Próximo paso: {b} envía la "
            "copia firmada a {a}.",
        ),
        "first": (
            "{a} abrió la incidencia el lunes. {a.first} llamará mañana.",
            "Hola equipo: {a} entró en la llamada con {b}. {a.first} pidió la factura; "
            "{b.first} enviará el formulario.",
        ),
        "last": (
            "{a} informó del problema. Tras la actualización, {a.last} confirmó la dirección.",
            "Nota CRM: hablé con {a} sobre la renovación. {a.last} quiere un descuento; {b}, de "
            "compras, está de acuerdo, así que {b.last} firmará.",
        ),
        "title": (
            "{a} escribió a soporte el martes. Según {a.title} {a.last}, la factura está mal.",
            "Correo de {a}, reenviado a facturación. Respuesta enviada para {a.title} {a.last}: "
            "ya hemos corregido el importe.",
        ),
        "before": (
            "{a.last} llamó el lunes por el retraso. Después, {a} envió el formulario.",
            "Chat: {a.first} sigue esperando el reembolso. Titular de la incidencia: {a}.",
        ),
        "run": (
            "{a} firmó el contrato. {a.first_last} pagó la fianza; luego {a.surnames} recogió "
            "las llaves.",
            "Expediente de {a}. El banco tiene como titular a {a.first_last}; en el DNI pone "
            "{a.surnames}.",
        ),
        "possessive": (
            "{a} revisó la auditoría. El informe ({a.last}) va adjunto.",
            "Actualización: {a} volvió a llamar. Según {a.last}, falta la firma del anexo.",
        ),
        "shared-last": (
            "{a}, titular de la cuenta, llamó junto con {b}, cotitular. {b.first} pagó; "
            "{a.first} se fue antes. Pidió el recibo {a.title} {a.last}, titular de la cuenta.",
            "Nota de cuenta: {a} (titular) comparte la cuenta con {b} (cotitular). {a.first} "
            "cambió la dirección; {b.first} no. Debe confirmarla {a.last}, como titular.",
        ),
        "shared-first": (
            "{a} (finanzas) se incorporó el lunes, igual que {b} (legal). {a.last} revisa el "
            "presupuesto; {b.last}, el contrato. {a.first}, de finanzas, aprobó el plan.",
            "Contactos del cliente: {a} en compras, {b} en soporte. {b.last} abrió la "
            "incidencia; {a.last} pidió el presupuesto. {b.first}, de soporte, llamará mañana.",
        ),
        "other": (
            "{a} firmó el contrato. {b}, de contabilidad, que no tiene relación con quien firmó, "
            "firmará también.",
            "{a} llamó por el pedido. Ojo: {b}, del almacén, no es quien llamó; {b} solo lo "
            "empaquetó.",
        ),
        "word": (
            "{a} reservó la sala para el jueves. {a.last} dice que hay un problema con {common}.",
            "Nota de recepción: {a} pasó por la oficina esta mañana. {a.last} preguntó dos "
            "veces por {common}.",
        ),
        "sentence": (
            "{a} envió el archivo esta mañana. {common} {a.first} dice que es urgente.",
            "Chat: {a} pidió una llamada. {common} {a.first} sigue esperando respuesta.",
        ),
        "lower": (
            "{a} escribió antes por el reembolso. gracias {a.first|lower}, recibido",
            "chat: {a} entró en el canal. vale, {a.last|lower} se encarga, luego me dices",
        ),
        "org": (
            "{a} trabaja en {org}. {a.last} enviará el presupuesto el lunes.",
            "Nota CRM: {org} renovó el contrato. El contacto allí es {a}; {a.last} prefiere el "
            "correo.",
        ),
        "single": (
            "Hoy ha llamado {a.title} {a.last}. {a.last} pide la factura original.",
            "Incidencia de {a.initial_last}: el paquete llega tarde. {a.last} quiere el reembolso.",
        ),
        "hyphen": (
            "{a} abrió la incidencia. {a.first_head} respondió con el número de pedido.",
            "{a} firmó el contrato. {a.last_tail} pide una copia.",
        ),
        "don": (
            "Atendí a {a.don} {a} esta mañana. {a.first} pide la factura de marzo.",
            "{a.don|capital} {a} llamó ayer. {a.title|capital} {a.last} quiere el reembolso.",
        ),
    },
}

_SLOT: Final = re.compile(r"\{(a|b|org|common)(?:\.([a-z_]+))?(?:\|(lower|capital))?\}")
_PERSON_FORMS: Final = frozenset(
    {
        "", "first", "last", "middle", "last2", "first_last", "middle_last", "surnames",
        "initial_last", "first_head", "last_tail",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class _Person:
    """A person of one example. A person with only `first` is named by one word."""

    gender: str
    first: str
    last: str = ""
    middle: str = ""
    last2: str = ""
    title: str = ""

    def form(self, name: str) -> str:
        if name == "":
            return self.full()
        parts = {
            "first": (self.first,),
            "last": (self.last,),
            "middle": (self.middle,),
            "last2": (self.last2,),
            "first_last": (self.first, self.last),
            "middle_last": (self.middle, self.last),
            "surnames": (self.last, self.last2),
            "title": (self.title,),
            "initial_last": (f"{self.first[:1]}.", self.last),
            "first_head": (self.first.split("-")[0] if "-" in self.first else "",),
            "last_tail": (self.last.split("-")[-1] if "-" in self.last else "",),
            "don": ("don" if self.gender == "male" else "doña",),
        }[name]
        if not all(parts):
            raise ValueError(f"a template asks for the form {name!r} of a person without it")
        return " ".join(parts)

    def full(self) -> str:
        return " ".join(part for part in (self.first, self.middle, self.last, self.last2) if part)

    def words(self) -> list[str]:
        return normalize_key(self.full()).split()

    def parts(self) -> list[str]:
        """The words of the full name, followed by the parts of each hyphenated word."""
        words = self.full().split()
        return words + [part for word in words if "-" in word for part in word.split("-")]


@dataclass(frozen=True, slots=True)
class _Example:
    text: str
    spans: tuple[tuple[int, int, str, str], ...]  # start, end, type, entity id
    # The dictionary term of each person, in text order: its first full-name mention, or in
    # `single`, its title or initial with the surname.
    terms: tuple[str, ...]
    shorter: tuple[str, ...]  # PERSON mentions that are not a term


def _usable(word: str) -> bool:
    return (
        len(word) >= 3
        and _NAME_WORD.fullmatch(word) is not None
        and word.casefold() not in TITLES | PARTICLES
    )


class _Names:
    """Draws persons for one language from Faker and its own seeded random generator."""

    def __init__(self, lang: str) -> None:
        locale, seed = LANGUAGES[lang]
        self.lang = lang
        self.rng = random.Random(seed)  # noqa: S311 - reproducible test data, not secrets
        self._fake = Faker(locale)
        self._fake.seed_instance(seed)

    def _draw(self, make: Callable[[], str]) -> str:
        while True:
            name = make()
            if all(_usable(word) for word in name.split()):
                return name

    def _first(self, gender: str) -> str:
        fake = self._fake
        return self._draw(fake.first_name_female if gender == "female" else fake.first_name_male)

    def _hyphenated(self, make: Callable[[], str]) -> str:
        """Two drawn words of one kind joined by a hyphen, each a usable word of letters."""

        def word() -> str:
            while True:
                drawn = self._draw(make)
                if _PLAIN_WORD.fullmatch(drawn):
                    return drawn

        return f"{word()}-{word()}"

    def person(
        self,
        *,
        gender: str | None = None,
        first: str | None = None,
        last: str | None = None,
        middle: bool = False,
    ) -> _Person:
        gender = gender or self.rng.choice(_GENDERS)
        first = first or self._first(gender)
        last = last or self._draw(self._fake.last_name)
        middle_name = self._first(gender) if middle else ""
        last2 = self._draw(self._fake.last_name) if self.lang == "es" else ""
        title = self.rng.choice(_TITLE_FORMS[self.lang][gender])
        return _Person(gender, first, last, middle_name, last2, title)

    def persons(self, category: str, labels: set[str]) -> dict[str, _Person]:
        """The persons a template of `category` names, before any check. `labels` holds the
        template's slots: `a`, `b`, `org` and `common`, and `a.form` for each form of `a`."""
        if category == "word":
            a = self.person(last=self.rng.choice(WORD_SURNAMES[self.lang]))
        elif category == "sentence":
            word = self.rng.choice(sorted(WORD_FIRST_NAMES[self.lang]))
            a = self.person(gender=WORD_FIRST_NAMES[self.lang][word], first=word)
        elif category == "hyphen" and "a.first_head" in labels:
            gender = self.rng.choice(_GENDERS)
            make = (
                self._fake.first_name_female if gender == "female" else self._fake.first_name_male
            )
            a = self.person(gender=gender, first=self._hyphenated(make))
        elif category == "hyphen":
            a = self.person(last=self._hyphenated(self._fake.last_name))
        elif category == "don" and self.lang == "en":
            a = self.person(gender="male", first=DON)
        else:
            a = self.person(middle=category == "run" and self.lang == "en")
        persons = {"a": a}
        if "b" in labels:
            if category == "shared-last":
                persons["b"] = self.person(last=a.last)
            elif category == "shared-first":
                persons["b"] = self.person(gender=a.gender, first=a.first)
            elif category == "other":
                persons["b"] = _Person(a.gender, a.first.split()[0])
            else:
                persons["b"] = self.person()
        return persons


def _allowed_shared(category: str, persons: dict[str, _Person]) -> set[str]:
    """The words the two persons of a category may share; none but in three categories."""
    a = persons["a"]
    if category == "shared-last":
        return set(normalize_key(a.last).split())
    if category == "shared-first":
        return set(normalize_key(a.first).split())
    if category == "other":
        return set(persons["b"].words())
    return set()


def _render(
    template: str, persons: dict[str, _Person], org: str, common: str
) -> tuple[str, list[tuple[int, int, str, str]], list[tuple[str, str]]]:
    """Fill a template; return the text, its gold spans and each PERSON mention as (form,
    surface)."""
    ids: dict[str, str] = {}
    pieces: list[str] = []
    spans: list[tuple[int, int, str, str]] = []
    mentions: list[tuple[str, str]] = []
    length = position = 0
    for match in _SLOT.finditer(template):
        pieces.append(template[position : match.start()])
        length += match.start() - position
        position = match.end()
        label, form, case = match[1], match[2] or "", match[3]
        if label == "org":
            value = org
            spans.append((length, length + len(value), "ORG", "o1"))
        elif label == "common":
            value = common
        else:
            value = persons[label].form(form)
            if case == "lower":
                value = value.lower()
            elif case == "capital":
                value = value[:1].upper() + value[1:]
            if form in _PERSON_FORMS:
                entity = ids.setdefault(label, f"p{len(ids) + 1}")
                spans.append((length, length + len(value), "PERSON", entity))
                mentions.append((form, value))
        pieces.append(value)
        length += len(value)
    pieces.append(template[position:])
    return "".join(pieces), spans, mentions


def _inside(spans: Sequence[tuple[int, int, str, str]], start: int, end: int) -> bool:
    return any(span[0] <= start and end <= span[1] for span in spans)


def _example(category: str, index: int, template: str, names: _Names) -> _Example | None:
    """One drawing for `template`, or None if its persons break a rule of the corpus."""
    slots = [(match[1], match[2]) for match in _SLOT.finditer(template)]
    labels = {label for label, _ in slots} | {f"{label}.{form}" for label, form in slots if form}
    persons = names.persons(category, labels)
    if any(len(set(person.words())) != len(person.words()) for person in persons.values()):
        return None
    new = category in NEW_CATEGORIES
    if new and any(
        len({normalize_key(part) for part in person.parts()}) != len(person.parts())
        for person in persons.values()
    ):
        return None
    if "b" in persons:
        shared = set(persons["a"].words()) & set(persons["b"].words())
        if shared != _allowed_shared(category, persons):
            return None
    a = persons["a"]
    listed = ""
    if category == "word":
        listed = a.last
    elif category == "sentence":
        listed = a.first
    org = f"{a.last} {names.rng.choice(ORG_SUFFIXES[names.lang])}" if "org" in labels else ""
    text, spans, mentions = _render(template, persons, org, COMMON[listed][index] if listed else "")
    # A name word occurs only inside gold spans, apart from the common use of a listed name.
    # In the new categories, so does each part of a hyphenated word, and `Don` may also start
    # the common word "Don't".
    for person in persons.values():
        for word in person.parts() if new else person.full().split():
            if word == listed:
                continue
            tail = _NOT_T if category == "don" and word == DON else ""
            pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w){tail}", re.IGNORECASE)
            if any(not _inside(spans, m.start(), m.end()) for m in pattern.finditer(text)):
                return None
    terms = tuple(
        dict.fromkeys(
            surface for form, surface in mentions if form == "" and len(surface.split()) >= 2
        )
    )
    if category == "single":
        term = a.form("initial_last") if "a.initial_last" in labels else ""
        terms = (term or f"{a.title.split()[-1]} {a.last}",)
    shorter = tuple(
        surface
        for form, surface in mentions
        if (form != "" or len(surface.split()) < 2) and surface not in terms
    )
    return _Example(text, tuple(spans), terms, shorter)


def _parts(surface: str) -> set[str]:
    """The keys of every run of at least two consecutive words of `surface`."""
    words = normalize_key(surface).split()
    return {
        " ".join(words[start : start + size])
        for size in range(2, len(words) + 1)
        for start in range(len(words) - size + 1)
    }


def _any_term(terms: Iterable[str]) -> re.Pattern[str] | None:
    """Any of the terms as a whole term, case-insensitively, with any whitespace inside."""
    bodies = [r"\s+".join(map(re.escape, term.split())) for term in terms]
    return re.compile(rf"(?<!\w)(?:{'|'.join(bodies)})(?!\w)", re.IGNORECASE) if bodies else None


class _Corpus:
    """Both languages, with the terms, the runs of shorter forms and the texts seen so far: the
    dictionary of terms applies to every example, so no shorter form may contain one."""

    def __init__(self) -> None:
        self.full_keys: set[str] = set()
        self.shorter_parts: set[str] = set()
        self.terms: dict[str, str] = {}  # key -> term as first written
        self.texts: list[str] = []

    def accepts(self, example: _Example) -> bool:
        full = {normalize_key(name) for name in example.terms}
        parts = {part for surface in example.shorter for part in _parts(surface)}
        return not (full & (self.shorter_parts | parts)) and not (parts & self.full_keys)

    def isolated(self, example: _Example) -> bool:
        """Whether every term matches only where it names its person: no earlier term occurs
        in the example's text, not even inside a word joined by a hyphen, and no term of the
        example occurs in an earlier text. Checked for the new categories only, so that the
        dictionary finds in the earlier lines exactly what it found before them."""
        earlier = _any_term(self.terms.values())
        if earlier is not None and earlier.search(example.text):
            return False
        own = _any_term(example.terms)
        return own is None or not any(own.search(text) for text in self.texts)

    def add(self, example: _Example) -> None:
        for name in example.terms:
            self.full_keys.add(normalize_key(name))
            self.terms.setdefault(normalize_key(name), name)
        self.shorter_parts.update(part for surface in example.shorter for part in _parts(surface))
        self.texts.append(example.text)


def _lines(names: _Names, categories: Sequence[str], corpus: _Corpus) -> list[str]:
    """The JSON lines of the given categories of one language, in order."""
    lang = names.lang
    lines = []
    for category in categories:
        templates = TEMPLATES[lang][category]
        new = category in NEW_CATEGORIES
        for number in range(1, 2 * RENDERS + 1):
            index = (number - 1) % 2
            example_id = f"{lang}-{category}-{number:03d}"
            drawings = (
                _example(category, index, templates[index], names) for _ in range(MAX_ATTEMPTS)
            )
            example = next(
                (
                    drawn
                    for drawn in drawings
                    if drawn is not None
                    and corpus.accepts(drawn)
                    and (not new or corpus.isolated(drawn))
                ),
                None,
            )
            if example is None:
                raise RuntimeError(f"no valid names for {example_id} in {MAX_ATTEMPTS} draws")
            corpus.add(example)
            spans = [
                {"start": start, "end": end, "type": kind, "entity": entity}
                for start, end, kind, entity in example.spans
            ]
            line = {"id": example_id, "lang": lang, "text": example.text, "spans": spans}
            lines.append(json.dumps(line, ensure_ascii=False))
    return lines


def _terms(corpus: _Corpus) -> str:
    lines = [
        "# Generated by tools/generate_person_corpus.py; do not edit. The term of every person",
        "# of persons.en.jsonl and persons.es.jsonl (the first full-name mention, or in `single`",
        "# the title or initial with the surname), in order of first appearance, for the",
        "# dictionary of persons.yaml. Synthetic values only.",
        "terms:",
    ]
    for name in corpus.terms.values():
        lines += [f"  - term: {json.dumps(name, ensure_ascii=False)}", "    type: PERSON"]
    return "\n".join(lines) + "\n"


def generate() -> dict[str, str]:
    """The content of every file of the person corpus, by file name. The categories of slice
    15 of both languages are drawn first, then the new ones, each language with its own
    random generator, so that the new categories leave every earlier line as it was."""
    corpus = _Corpus()
    lines: dict[str, list[str]] = {lang: [] for lang in LANGUAGES}
    names: dict[str, _Names] = {}
    for new in (False, True):
        for lang in LANGUAGES:
            source = names.setdefault(lang, _Names(lang))
            categories = [c for c in TEMPLATES[lang] if (c in NEW_CATEGORIES) == new]
            lines[lang] += _lines(source, categories, corpus)
    files = {f"persons.{lang}.jsonl": "\n".join(lines[lang]) + "\n" for lang in LANGUAGES}
    files[TERMS_FILE] = _terms(corpus)
    return files


def main(argv: Sequence[str] | None = None) -> int:
    """Write the files, or with --check report whether the committed ones differ."""
    parser = argparse.ArgumentParser(description="Generate the person mentions corpus.")
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="corpus directory")
    args = parser.parse_args(argv)
    differ = []
    for name, content in generate().items():
        path = args.out / name
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                differ.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    for path in differ:
        print(f"{path} differs from the generated corpus", file=sys.stderr)
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
