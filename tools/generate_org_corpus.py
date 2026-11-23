"""Generate the organisation mentions corpus in tests/eval/corpus/ (slice 18).

Each example names one or two organisations, by their full name with its legal form and by the
shorter forms people write: the name without its legal form, one distinctive word, the same word
in lower case. It also holds the cases where replacing or linking a word would go wrong: two
organisations with the same generic word, a generic word used on its own, and a distinctive word
that is also a common word. Gold spans carry entity ids, so the evaluation can tell whether the
mentions of one organisation share a placeholder number. The dictionary file holds the first
full-name mention of every organisation, so that CI finds it and measures the shorter forms on
their own.

Deterministic: the fixed seeds below produce byte-identical files for a given Faker version.
Every name is synthetic: distinctive words come from Faker's surnames or from a fixed list of
common words, and the other words from the fixed lists below. This generator shares no code
with the other two, so changing one cannot change the files of another.

    uv run python tools/generate_org_corpus.py           # write the files
    uv run python tools/generate_org_corpus.py --check   # exit 1 if the committed files differ
"""

import argparse
import dataclasses
import json
import random
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from faker import Faker

from aliasgate.common_words import COMMON_WORDS
from aliasgate.mapping import normalize_key

DEFAULT_OUT: Final = Path(__file__).resolve().parent.parent / "tests" / "eval" / "corpus"
LANGUAGES: Final = {"en": ("en_US", 1801), "es": ("es_ES", 1802)}  # locale, seed
TERMS_FILE: Final = "orgs.terms.yaml"
RENDERS: Final = 10  # per template
MAX_ATTEMPTS: Final = 1000  # draws per example before giving up

# What an organisation does or is. Slice 19's engine list must contain them all.
GENERIC_WORDS: Final = {
    "en": (
        "Analytics", "Capital", "Consulting", "Engineering", "Freight", "Holdings",
        "Logistics", "Marine", "Partners", "Software", "Solutions", "Systems",
    ),
    "es": (
        "Consultoría", "Construcciones", "Distribuciones", "Grupo", "Ingeniería",
        "Inversiones", "Logística", "Servicios", "Soluciones", "Suministros", "Talleres",
        "Transportes",
    ),
}  # fmt: skip
LEGAL_FORMS: Final = {
    "en": ("Ltd", "LLC", "PLC", "Inc.", "Corp."),
    "es": ("S.L.", "S.A.", "S.L.U.", "S.A.U.", "S.Coop."),
}
# The legal forms of the shape with several distinctive words (category `multi`).
SEVERAL_LEGAL_FORMS: Final = {"en": ("LLP",), "es": LEGAL_FORMS["es"]}
CONNECTORS: Final = frozenset(("and", "y", "e", "of", "the", "de", "del", "la", "las", "los", "el"))
# Common words that are also names, the distinctive words of the category `common`.
COMMON_NAMES: Final = {
    "en": ("Stone", "King", "Bell", "Brown", "Rice", "Young"),
    "es": ("Mesa", "Plaza", "Torre", "Luna", "Vega", "Rubio"),
}
# A word that no distinctive word may be: a generic word, a legal form or a connector, as keys
# without dots.
_RESERVED: Final = frozenset(
    normalize_key(word).replace(".", "")
    for words in (*GENERIC_WORDS.values(), *LEGAL_FORMS.values(), ("LLP",), CONNECTORS)
    for word in words
)
# Spanish writes "e" instead of "y" before a word starting with these sounds.
_E_BEFORE: Final = re.compile(r"h?[ií]", re.IGNORECASE)

# Templates -------------------------------------------------------------------------------------
# Slots: {a} and {b} are the full names of two organisations, {a.bare} the name without its
# legal form, {a.word}, {a.word2} and {a.word3} its first, second and third distinctive words,
# and {a.word|lower} a word in lower case. Each of these is an ORG gold span. {a.generic} is the
# generic word of {a} on its own, which is not a span. In `multi`, {a} has several distinctive
# words and no generic word; in `shared`, {a} and {b} have the same generic word. A full name is
# always followed by ",", ":" or a space and a lower-case letter, so that the final "." of a
# legal form never ends a sentence.

TEMPLATES: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "en": {
        "full": (
            "{a} and {b} signed the deal. {b} pays first, then {a} ships the goods.",
            "Email thread: {a} wrote to {b}, asking for the new rates. {b} replied on Friday, "
            "and {a} accepted them.",
        ),
        "bare": (
            "We signed with {a} last week. {a.bare} will send the invoice on Monday.",
            "CRM note: {a} renewed the support plan. The contact at {a.bare} asked for a discount.",
        ),
        "word": (
            "Ticket from {a}: the parcel is late. {a.word} wants a refund, so please call "
            "{a.word} today.",
            "Chat: {a} has not paid the last invoice. Reminder sent to {a.word}; {a.word}'s "
            "accounts team will reply by Friday.",
        ),
        "before": (
            "{a.word} called on Monday about the order. Later, {a} sent the signed form.",
            "Support chat: {a.word} is still waiting for the replacement. Account owner: {a}, "
            "since last year.",
        ),
        "multi": (
            "{a} sent the draft. {a.word2} asked for comments, and {a.word3} will sign it.",
            "Email from {a}: the contract is ready. {a.word} reviewed it, and {a.word3} wants a "
            "call before Friday.",
        ),
        "shared": (
            "{a} and {b} both sent a bid. {a.word} offered a lower price than {b.word}.",
            "Vendor review: {a} delivered late, while {b} was on time. Next quarter we keep "
            "{b.word} and drop {a.word}.",
        ),
        "generic": (
            "{a} sent the quarterly report. {a.generic} costs rose this quarter, and {a.word} "
            "expects more.",
            "Category: {a.generic}. Ticket from {a}: the invoice is wrong, and {a.word} wants a "
            "new one.",
        ),
        "lower": (
            "{a} confirmed the order. thanks, {a.word|lower} will call back tomorrow",
            "chat: {a} joined the channel. ok so {a.word|lower} needs the invoice by friday, "
            "ping me",
        ),
        "common": (
            "{a} shipped the parts. {a.word} says the order is complete.",
            "Ticket from {a}: two boxes arrived damaged. {a.word} wants a replacement.",
        ),
    },
    # Spanish templates avoid "y" and "o" before a slot, which would become "e" and "u" before
    # some names, and keep every word that agrees with a gender out of the fixed text.
    "es": {
        "full": (
            "{a} firmó el acuerdo con {b} ayer. Primero paga {b}, luego {a} envía la mercancía.",
            "Hilo de correo: {a} escribió a {b} para pedir las nuevas tarifas. {b} respondió el "
            "viernes, así que {a} las aceptó.",
        ),
        "bare": (
            "Firmamos con {a} la semana pasada. {a.bare} enviará la factura el lunes.",
            "Nota CRM: {a} renovó el plan de soporte. El contacto de {a.bare} pidió un descuento.",
        ),
        "word": (
            "Incidencia de {a}: el paquete llega tarde. {a.word} quiere el reembolso, así que "
            "llama hoy a {a.word}, por favor.",
            "Chat: {a} no ha pagado la última factura. Recordatorio enviado a {a.word}; desde "
            "{a.word} dicen que contestarán mañana.",
        ),
        "before": (
            "{a.word} llamó el lunes por el pedido. Después, {a} envió el formulario firmado.",
            "Chat de soporte: {a.word} sigue esperando la sustitución. Titular de la cuenta: "
            "{a}, desde el año pasado.",
        ),
        "multi": (
            "{a} reclama el pago. {a.word2} quiere la factura; {a.word} pide una copia.",
            "Correo de {a}: el contrato está listo. Lo revisó {a.word2}, pero {a.word} quiere "
            "una llamada antes del viernes.",
        ),
        "shared": (
            "{a} envió una oferta; {b} mandó otra. {a.word} ofreció un precio menor que {b.word}.",
            "Revisión de proveedores: {a} entregó tarde, mientras que {b} cumplió el plazo. El "
            "próximo trimestre seguimos con {b.word}, no con {a.word}.",
        ),
        "generic": (
            "{a} envió el informe trimestral. {a.generic}: el gasto subió este trimestre, pero "
            "{a.word} espera más.",
            "Categoría: {a.generic}. Incidencia de {a}: la factura está mal, así que {a.word} "
            "quiere otra.",
        ),
        "lower": (
            "{a} confirmó el pedido. gracias, {a.word|lower} llamará mañana",
            "chat: {a} entró en el canal. vale, {a.word|lower} necesita la factura el viernes, "
            "luego me dices",
        ),
        "common": (
            "{a} envió las piezas. {a.word} dice que el pedido está completo.",
            "Incidencia de {a}: llegaron dos cajas dañadas. {a.word} pide una sustitución.",
        ),
    },
}

_SLOT: Final = re.compile(r"\{(a|b)(?:\.([a-z0-9]+))?(?:\|(lower))?\}")
_WORD_FORMS: Final = {"word": 0, "word2": 1, "word3": 2}


@dataclass(frozen=True, slots=True)
class _Org:
    """An organisation of one example. Without a generic word, it has several distinctive
    words."""

    lang: str
    words: tuple[str, ...]  # distinctive words, in order
    generic: str
    legal: str

    def bare(self) -> str:
        """The name without its legal form."""
        if self.generic:
            (word,) = self.words
            return f"{word} {self.generic}" if self.lang == "en" else f"{self.generic} {word}"
        if self.lang == "en":
            *head, last = self.words
            return f"{', '.join(head)} and {last}"
        return " y ".join(self.words)

    def full(self) -> str:
        return f"{self.bare()} {self.legal}"

    def form(self, name: str) -> str:
        if name == "":
            return self.full()
        if name == "bare":
            return self.bare()
        if name == "generic" and self.generic:
            return self.generic
        if name in _WORD_FORMS and _WORD_FORMS[name] < len(self.words):
            return self.words[_WORD_FORMS[name]]
        raise ValueError(f"a template asks for the form {name!r} of an organisation without it")

    def keys(self) -> list[str]:
        """The keys of its distinctive words, its generic word and its legal form."""
        return [normalize_key(word) for word in (*self.words, self.generic, self.legal) if word]


@dataclass(frozen=True, slots=True)
class _Example:
    text: str
    spans: tuple[tuple[int, int, str, str], ...]  # start, end, type, entity id
    terms: tuple[str, ...]  # the first full-name mention of each organisation, in text order
    words: tuple[str, ...]  # keys of its distinctive words that must be unique in the corpus


def _distinctive(word: str) -> bool:
    key = normalize_key(word)
    return len(word) >= 3 and word.isalpha() and key not in COMMON_WORDS and key not in _RESERVED


class _Source(Protocol):
    lang: str

    def orgs(self, category: str, labels: set[str]) -> dict[str, _Org]: ...


class _Names:
    """Draws organisations for one language from Faker and its own seeded random generator."""

    def __init__(self, lang: str) -> None:
        locale, seed = LANGUAGES[lang]
        self.lang = lang
        self.rng = random.Random(seed)  # noqa: S311 - reproducible test data, not secrets
        self._fake = Faker(locale)
        self._fake.seed_instance(seed)

    def _word(self) -> str:
        while True:
            word = self._fake.last_name()
            if _distinctive(word):
                return word

    def org(self, category: str) -> _Org:
        """An organisation of the shape `category` asks for, before any check."""
        if category == "multi":
            count = 3 if self.lang == "en" else 2
            words = tuple(self._word() for _ in range(count))
            return _Org(self.lang, words, "", self.rng.choice(SEVERAL_LEGAL_FORMS[self.lang]))
        word = self.rng.choice(COMMON_NAMES[self.lang]) if category == "common" else self._word()
        generic = self.rng.choice(GENERIC_WORDS[self.lang])
        return _Org(self.lang, (word,), generic, self.rng.choice(LEGAL_FORMS[self.lang]))

    def orgs(self, category: str, labels: set[str]) -> dict[str, _Org]:
        """The organisations a template of `category` names, before any check. `labels` holds
        the template's organisations: `a`, and `b` if it names two."""
        orgs = {"a": self.org(category)}
        if "b" in labels:
            b = self.org(category)
            if category == "shared":
                b = dataclasses.replace(b, generic=orgs["a"].generic)
            orgs["b"] = b
        return orgs


def _render(
    template: str, orgs: dict[str, _Org]
) -> tuple[str, list[tuple[int, int, str, str]], list[tuple[str, str]]]:
    """Fill a template; return the text, its gold spans and each ORG mention as (form,
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
        value = orgs[label].form(form)
        if case == "lower":
            value = value.lower()
        if form != "generic":
            entity = ids.setdefault(label, f"o{len(ids) + 1}")
            spans.append((length, length + len(value), "ORG", entity))
            mentions.append((form, value))
        pieces.append(value)
        length += len(value)
    pieces.append(template[position:])
    return "".join(pieces), spans, mentions


def _inside(spans: Sequence[tuple[int, int, str, str]], start: int, end: int) -> bool:
    return any(span[0] <= start and end <= span[1] for span in spans)


def _example(category: str, template: str, source: _Source) -> _Example | None:
    """One drawing for `template`, or None if its organisations break a rule of the corpus."""
    labels = {match[1] for match in _SLOT.finditer(template)}
    orgs = source.orgs(category, labels)
    if any(len(set(org.keys())) != len(org.keys()) for org in orgs.values()):
        return None
    if source.lang == "es" and any(
        not org.generic and _E_BEFORE.match(org.words[-1]) for org in orgs.values()
    ):
        return None  # the word after "y" would need "e"
    if "b" in orgs:
        a, b = orgs["a"], orgs["b"]
        allowed = {normalize_key(a.generic)} if category == "shared" else set()
        if set(a.keys()) & set(b.keys()) != allowed:
            return None
    text, spans, mentions = _render(template, orgs)
    # A distinctive word occurs only inside gold spans.
    for org in orgs.values():
        for word in org.words:
            pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
            if any(not _inside(spans, m.start(), m.end()) for m in pattern.finditer(text)):
                return None
    terms = tuple(dict.fromkeys(surface for form, surface in mentions if form == ""))
    words = (
        ()
        if category == "common"
        else tuple(normalize_key(word) for org in orgs.values() for word in org.words)
    )
    return _Example(text, tuple(spans), terms, words)


class _Corpus:
    """Both languages: the full names and the distinctive words drawn so far, which an example
    may not repeat."""

    def __init__(self) -> None:
        self.terms: dict[str, str] = {}  # key -> full name as written
        self.words: set[str] = set()

    def accepts(self, example: _Example) -> bool:
        if any(normalize_key(term) in self.terms for term in example.terms):
            return False
        return not self.words & set(example.words)

    def add(self, example: _Example) -> None:
        for term in example.terms:
            self.terms.setdefault(normalize_key(term), term)
        self.words.update(example.words)


def _draw(category: str, template: str, source: _Source, corpus: _Corpus) -> _Example:
    """The first drawing for `template` that keeps every rule of the corpus."""
    drawings = (_example(category, template, source) for _ in range(MAX_ATTEMPTS))
    example = next(
        (drawn for drawn in drawings if drawn is not None and corpus.accepts(drawn)), None
    )
    if example is None:
        raise RuntimeError(f"no valid names for a {category} example in {MAX_ATTEMPTS} draws")
    return example


def _lines(names: _Source, corpus: _Corpus) -> list[str]:
    """The JSON lines of one language, in order."""
    lang = names.lang
    lines = []
    for category, templates in TEMPLATES[lang].items():
        for number in range(1, 2 * RENDERS + 1):
            example = _draw(category, templates[(number - 1) % 2], names, corpus)
            corpus.add(example)
            example_id = f"{lang}-{category}-{number:03d}"
            spans = [
                {"start": start, "end": end, "type": kind, "entity": entity}
                for start, end, kind, entity in example.spans
            ]
            line = {"id": example_id, "lang": lang, "text": example.text, "spans": spans}
            lines.append(json.dumps(line, ensure_ascii=False))
    return lines


def _terms(corpus: _Corpus) -> str:
    lines = [
        "# Generated by tools/generate_org_corpus.py; do not edit. The first full-name mention",
        "# of every organisation of orgs.en.jsonl and orgs.es.jsonl, in order of first",
        "# appearance, for the dictionary of orgs.yaml. Synthetic values only.",
        "terms:",
    ]
    for name in corpus.terms.values():
        lines += [f"  - term: {json.dumps(name, ensure_ascii=False)}", "    type: ORG"]
    return "\n".join(lines) + "\n"


def generate() -> dict[str, str]:
    """The content of every file of the organisation corpus, by file name: English first, then
    Spanish, each language with its own random generator."""
    corpus = _Corpus()
    files = {
        f"orgs.{lang}.jsonl": "\n".join(_lines(_Names(lang), corpus)) + "\n" for lang in LANGUAGES
    }
    files[TERMS_FILE] = _terms(corpus)
    return files


def main(argv: Sequence[str] | None = None) -> int:
    """Write the files, or with --check report whether the committed ones differ."""
    parser = argparse.ArgumentParser(description="Generate the organisation mentions corpus.")
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
