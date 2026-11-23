"""The organisation mentions corpus (slice 18): its generator, its files and their shape."""

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import generate_org_corpus as generator
from aliasgate.common_words import COMMON_WORDS
from aliasgate.config import load_settings, load_terms
from aliasgate.eval.corpus import Example, GoldSpan, load_corpus
from aliasgate.eval.metrics import Report, evaluate
from aliasgate.factory import build_pseudonymizer
from aliasgate.mapping import normalize_key

CORPUS = Path(__file__).parent / "corpus"
LANGUAGES = ("en", "es")
CATEGORIES = ("full", "bare", "word", "before", "multi", "shared", "generic", "lower", "common")
_ID = re.compile(r"(en|es)-([a-z]+)-(\d{3})")
_CONNECTORS = {"and", "y"}


@pytest.fixture(scope="module")
def corpora() -> dict[str, list[Example]]:
    return {lang: load_corpus(CORPUS / f"orgs.{lang}.jsonl") for lang in LANGUAGES}


def _category(example: Example) -> str:
    match = _ID.fullmatch(example.id)
    assert match is not None, example.id
    return match[2]


def _number(example: Example) -> int:
    match = _ID.fullmatch(example.id)
    assert match is not None, example.id
    return int(match[3])


def _surface(example: Example, span: GoldSpan) -> str:
    return example.text[span.start : span.end]


def _legal_forms(lang: str) -> set[str]:
    return {*generator.LEGAL_FORMS[lang], *generator.SEVERAL_LEGAL_FORMS[lang]}


@dataclass(frozen=True, slots=True)
class _Parsed:
    """An organisation of an example, read back from its full-name mention."""

    full: str
    bare: str
    words: tuple[str, ...]  # distinctive words
    generic: str
    legal: str
    mentions: tuple[GoldSpan, ...]


def _is_full(lang: str, surface: str) -> bool:
    return surface.split()[-1] in _legal_forms(lang)


def _orgs(example: Example) -> dict[str, _Parsed]:
    """The organisations of an example, by entity id, in order of first mention."""
    spans: dict[str, list[GoldSpan]] = {}
    for span in example.spans:
        assert span.entity is not None, example.id
        spans.setdefault(span.entity, []).append(span)
    parsed = {}
    for entity, mentions in spans.items():
        fulls = [
            _surface(example, s) for s in mentions if _is_full(example.lang, _surface(example, s))
        ]
        assert fulls, example.id
        full = fulls[0]
        legal = full.split()[-1]
        bare = full[: -len(legal)].rstrip()
        content = [word.rstrip(",") for word in bare.split() if word not in _CONNECTORS]
        generics = [word for word in content if word in generator.GENERIC_WORDS[example.lang]]
        words = tuple(word for word in content if word not in generics)
        assert len(generics) <= 1, example.id
        parsed[entity] = _Parsed(full, bare, words, "".join(generics), legal, tuple(mentions))
    return parsed


def _outside_gold(example: Example, word: str) -> Iterator[str]:
    """Each occurrence of `word` (whole, in any case) outside every gold span, as written."""
    pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
    for match in pattern.finditer(example.text):
        if not any(s.start <= match.start() and match.end() <= s.end for s in example.spans):
            yield match[0]


def _template_pattern(template: str) -> re.Pattern[str]:
    parts = generator._SLOT.split(template)
    # split() returns the text between slots and, for each slot, its three groups.
    literal = parts[::4]
    return re.compile("(.+?)".join(map(re.escape, literal)), re.DOTALL)


# The generator --------------------------------------------------------------------------------


def test_check_reports_whether_the_files_differ_from_a_fresh_generation(tmp_path: Path) -> None:
    assert generator.main(["--out", str(tmp_path)]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "orgs.en.jsonl",
        "orgs.es.jsonl",
        "orgs.terms.yaml",
    ]
    assert generator.main(["--check", "--out", str(tmp_path)]) == 0
    for name in ("orgs.en.jsonl", "orgs.terms.yaml"):
        path = tmp_path / name
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "\n", encoding="utf-8")
        assert generator.main(["--check", "--out", str(tmp_path)]) == 1
        path.unlink()
        assert generator.main(["--check", "--out", str(tmp_path)]) == 1
        path.write_text(original, encoding="utf-8")
        assert generator.main(["--check", "--out", str(tmp_path)]) == 0


def test_the_generator_is_deterministic() -> None:
    assert generator.generate() == generator.generate()


def test_the_generator_has_two_templates_per_category_and_language() -> None:
    for lang in LANGUAGES:
        assert tuple(generator.TEMPLATES[lang]) == CATEGORIES
        assert all(len(templates) == 2 for templates in generator.TEMPLATES[lang].values())


def test_the_generator_word_lists_are_those_of_the_slice_file() -> None:
    assert len(generator.GENERIC_WORDS["en"]) == len(generator.GENERIC_WORDS["es"]) == 12
    assert generator.LEGAL_FORMS == {
        "en": ("Ltd", "LLC", "PLC", "Inc.", "Corp."),
        "es": ("S.L.", "S.A.", "S.L.U.", "S.A.U.", "S.Coop."),
    }
    assert generator.SEVERAL_LEGAL_FORMS["en"] == ("LLP",)
    for lang in LANGUAGES:
        assert all(normalize_key(word) in COMMON_WORDS for word in generator.COMMON_NAMES[lang])
        assert not any(normalize_key(w) in COMMON_WORDS for w in generator.GENERIC_WORDS[lang])


class _FixedOrgs:
    """Stands in for the generator's name source: the given organisations, in turn."""

    def __init__(self, lang: str, *drawings: dict[str, generator._Org]) -> None:
        self.lang = lang
        self._drawings = list(drawings)
        self.calls = 0

    def orgs(self, category: str, labels: set[str]) -> dict[str, generator._Org]:
        drawing = self._drawings[min(self.calls, len(self._drawings) - 1)]
        self.calls += 1
        return dict(drawing)


def _org(lang: str, *words: str, generic: str = "", legal: str = "") -> generator._Org:
    legal = legal or {"en": "Ltd", "es": "S.L."}[lang]
    return generator._Org(lang, words, generic, legal)


BRIGHTWATER = _org("en", "Brightwater", generic="Logistics")
CORRIGAN = _org("en", "Corrigan", generic="Freight", legal="PLC")
VENTISQUERO = _org("es", "Ventisquero", generic="Transportes")


def _drawn(category: str, template: str, lang: str = "en", **orgs: generator._Org) -> object:
    return generator._example(category, template, _FixedOrgs(lang, orgs))


def test_the_generator_writes_the_full_name_bare_name_and_words_of_each_shape() -> None:
    assert BRIGHTWATER.full() == "Brightwater Logistics Ltd"
    assert BRIGHTWATER.bare() == "Brightwater Logistics"
    assert VENTISQUERO.full() == "Transportes Ventisquero S.L."
    hale = _org("en", "Hale", "Mercer", "Quill", legal="LLP")
    assert hale.full() == "Hale, Mercer and Quill LLP"
    assert [hale.form(name) for name in ("word", "word2", "word3")] == ["Hale", "Mercer", "Quill"]
    assert _org("es", "Albarrán", "Ledesma", legal="S.A.").full() == "Albarrán y Ledesma S.A."
    with pytest.raises(ValueError, match="'word2'"):
        BRIGHTWATER.form("word2")
    with pytest.raises(ValueError, match="'generic'"):
        hale.form("generic")


def test_the_generator_gives_spans_and_ids_in_order_of_first_mention() -> None:
    template = "{a.word} called. {b.word|lower} and {a}, {b.generic}. {b.bare} paid {b}, {a}."
    example = _drawn("before", template, a=BRIGHTWATER, b=CORRIGAN)
    assert isinstance(example, generator._Example)
    assert example.text == (
        "Brightwater called. corrigan and Brightwater Logistics Ltd, Freight. Corrigan Freight "
        "paid Corrigan Freight PLC, Brightwater Logistics Ltd."
    )
    assert [(example.text[s:e], entity) for s, e, _, entity in example.spans] == [
        ("Brightwater", "o1"),
        ("corrigan", "o2"),
        ("Brightwater Logistics Ltd", "o1"),
        ("Corrigan Freight", "o2"),
        ("Corrigan Freight PLC", "o2"),
        ("Brightwater Logistics Ltd", "o1"),
    ]
    # The full names, in text order, once each: no bare name and no word.
    assert example.terms == ("Brightwater Logistics Ltd", "Corrigan Freight PLC")
    assert example.words == ("brightwater", "corrigan")


def test_ids_follow_the_first_mention_not_the_slot_name() -> None:
    example = _drawn("full", "{b.word} called {a}, then {b}.", a=BRIGHTWATER, b=CORRIGAN)
    assert isinstance(example, generator._Example)
    assert [entity for _, _, _, entity in example.spans] == ["o1", "o2", "o1"]
    assert example.terms == ("Brightwater Logistics Ltd", "Corrigan Freight PLC")


def test_the_dictionary_file_lists_the_full_names_as_org_terms(tmp_path: Path) -> None:
    corpus = generator._Corpus()
    corpus.add(generator._Example("", (), ("Brightwater Logistics Ltd", 'Quote "Inc."'), ()))
    text = generator._terms(corpus)
    assert text == (
        "# Generated by tools/generate_org_corpus.py; do not edit. The first full-name mention\n"
        "# of every organisation of orgs.en.jsonl and orgs.es.jsonl, in order of first\n"
        "# appearance, for the dictionary of orgs.yaml. Synthetic values only.\n"
        "terms:\n"
        '  - term: "Brightwater Logistics Ltd"\n'
        "    type: ORG\n"
        '  - term: "Quote \\"Inc.\\""\n'
        "    type: ORG\n"
    )
    path = tmp_path / "terms.yaml"
    path.write_text(text, encoding="utf-8")
    assert [(t.term, t.entity_type) for t in load_terms(path)] == [
        ("Brightwater Logistics Ltd", "ORG"),
        ('Quote "Inc."', "ORG"),
    ]


def test_the_generator_rejects_a_distinctive_word_used_outside_the_gold_spans() -> None:
    assert _drawn("word", "{a} sent it. {a.word} called.", a=BRIGHTWATER) is not None
    assert _drawn("word", "{a} sent it. The brightwater pipe.", a=BRIGHTWATER) is None
    # A generic word on its own is allowed.
    assert _drawn("generic", "{a} sent it. Logistics rose.", a=BRIGHTWATER) is not None


def test_the_generator_rejects_organisations_sharing_a_word_their_category_does_not_allow() -> None:
    pitts = _org("en", "Pitts", generic="Logistics", legal="LLC")
    template = "{a} and {b} called."
    assert _drawn("full", template, a=BRIGHTWATER, b=CORRIGAN) is not None
    assert _drawn("full", template, a=BRIGHTWATER, b=pitts) is None
    assert _drawn("shared", template, a=BRIGHTWATER, b=pitts) is not None
    assert _drawn("shared", template, a=BRIGHTWATER, b=CORRIGAN) is None
    same_legal = _org("en", "Pitts", generic="Logistics")
    assert _drawn("shared", template, a=BRIGHTWATER, b=same_legal) is None
    twice = _org("en", "Hale", "Hale", "Quill", legal="LLP")
    assert _drawn("multi", "{a} called.", a=twice) is None


@pytest.mark.parametrize("word", ["Ibarra", "Íñiguez", "Hidalgo", "HÍDEZ", "iriarte"])
def test_spanish_several_words_are_drawn_again_when_the_word_after_y_needs_e(word: str) -> None:
    bad = _org("es", "Albarrán", word, legal="S.A.")
    good = _org("es", "Albarrán", "Ledesma", legal="S.A.")
    assert _drawn("multi", "{a} reclama el pago.", "es", a=bad) is None
    assert _drawn("multi", "{a} reclama el pago.", "es", a=good) is not None
    # A word that needs "e" may come first, and English names have no such rule.
    assert _drawn("multi", "{a} paga.", "es", a=_org("es", word, "Ledesma")) is not None
    english = _org("en", "Hale", "Mercer", word, legal="LLP")
    assert _drawn("multi", "{a} paid.", "en", a=english) is not None
    source = _FixedOrgs("es", {"a": bad}, {"a": good})
    example = generator._draw("multi", "{a} reclama el pago.", source, generator._Corpus())
    assert example.terms == ("Albarrán y Ledesma S.A.",)
    assert source.calls == 2


def test_the_generator_gives_up_after_too_many_rejected_draws() -> None:
    bad = _org("es", "Albarrán", "Ibarra", legal="S.A.")
    source = _FixedOrgs("es", {"a": bad})
    with pytest.raises(RuntimeError, match="no valid names for a multi example"):
        generator._draw("multi", "{a} reclama el pago.", source, generator._Corpus())
    assert source.calls == generator.MAX_ATTEMPTS


def test_the_generator_rejects_a_full_name_or_distinctive_word_drawn_before() -> None:
    corpus = generator._Corpus()
    first = generator._Example("", (), ("Brightwater Logistics Ltd",), ("brightwater",))
    assert corpus.accepts(first)
    corpus.add(first)
    assert not corpus.accepts(generator._Example("", (), ("BRIGHTWATER  logistics ltd",), ()))
    assert not corpus.accepts(
        generator._Example("", (), ("Brightwater Freight PLC",), ("brightwater",))
    )
    assert corpus.accepts(generator._Example("", (), ("Stone Logistics Ltd",), ()))
    assert list(corpus.terms.values()) == ["Brightwater Logistics Ltd"]


def test_a_distinctive_word_is_a_long_word_of_letters_that_no_list_holds() -> None:
    assert generator._distinctive("Brightwater")
    assert generator._distinctive("Peñalver")
    for word in ("Li", "O'Neil", "Stone", "Logistics", "Grupo", "Corp", "And", "Llp"):
        assert not generator._distinctive(word), word


# The corpus files -----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_each_language_has_20_examples_in_each_of_9_categories_alternating_two_templates(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    examples = corpora[lang]
    assert len(examples) == 180
    assert {example.lang for example in examples} == {lang}
    assert [example.id for example in examples] == [
        f"{lang}-{category}-{number:03d}" for category in CATEGORIES for number in range(1, 21)
    ]
    for example in examples:
        templates = generator.TEMPLATES[lang][_category(example)]
        pattern = _template_pattern(templates[(_number(example) - 1) % 2])
        assert pattern.fullmatch(example.text), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_span_is_an_org_with_an_id_in_order_of_first_mention(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        assert example.spans, example.id
        assert {span.entity_type for span in example.spans} == {"ORG"}, example.id
        ids = list(dict.fromkeys(span.entity for span in example.spans))
        assert ids == [f"o{number}" for number in range(1, len(ids) + 1)], example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_span_is_a_full_name_a_bare_name_or_a_distinctive_word(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        category = _category(example)
        for org in _orgs(example).values():
            for span in org.mentions:
                surface = _surface(example, span)
                assert surface == surface.strip(), example.id
                if category == "lower" and not _is_full(lang, surface):
                    assert surface in {word.lower() for word in org.words}, example.id
                    continue
                assert surface[0].isupper(), example.id
                assert surface in {org.full, org.bare, *org.words}, example.id
                if surface == org.full:
                    after = example.text[span.end : span.end + 2]
                    lower = after[:1] == " " and after[1:].islower()
                    assert after[:1] in {",", ":"} or lower, example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_each_category_names_the_forms_it_is_for(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        category, orgs = _category(example), _orgs(example)
        assert len(orgs) == (2 if category in {"full", "shared"} else 1), example.id
        for org in orgs.values():
            forms = [_surface(example, span) for span in org.mentions]
            full, shorter = forms.count(org.full), [form for form in forms if form != org.full]
            assert (len(org.words) > 1) == (category == "multi"), example.id
            assert bool(org.generic) == (category != "multi"), example.id
            if category == "full":
                assert (full, shorter) == (2, []), example.id
            elif category == "bare":
                assert (full, shorter) == (1, [org.bare]), example.id
            elif category == "word":
                assert (full, shorter) == (1, [org.words[0]] * 2), example.id
            elif category == "before":
                assert forms == [org.words[0], org.full], example.id
            elif category == "multi":
                assert full == 1, example.id
                assert len(set(shorter)) == 2, example.id
                assert set(shorter) <= set(org.words), example.id
                assert set(shorter) != {org.words[0]}, example.id
            elif category == "lower":
                assert (full, shorter) == (1, [org.words[0].lower()]), example.id
            else:
                assert (full, shorter) == (1, [org.words[0]]), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_distinctive_word_occurs_only_inside_the_gold_spans_of_its_example(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        for org in _orgs(example).values():
            for word in org.words:
                assert list(_outside_gold(example, word)) == [], example.id
                common = normalize_key(word) in COMMON_WORDS
                assert common == (_category(example) == "common"), example.id
                if common:
                    assert word in generator.COMMON_NAMES[lang], example.id


def test_distinctive_words_are_unique_outside_common_and_full_names_everywhere(
    corpora: dict[str, list[Example]],
) -> None:
    owners: dict[str, str] = {}
    fulls: Counter[str] = Counter()
    for lang in LANGUAGES:
        for example in corpora[lang]:
            for entity, org in _orgs(example).items():
                fulls[normalize_key(org.full)] += 1
                if _category(example) == "common":
                    continue
                for word in org.words:
                    owner = owners.setdefault(normalize_key(word), f"{example.id}/{entity}")
                    assert owner == f"{example.id}/{entity}", example.id
    assert len(fulls) == 2 * (180 + 40)
    assert set(fulls.values()) == {1}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_organisations_of_an_example_share_only_the_generic_word_of_shared(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        orgs = list(_orgs(example).values())
        if len(orgs) < 2:
            continue
        a, b = orgs
        words = [{normalize_key(w) for w in org.full.replace(",", "").split()} for org in (a, b)]
        if _category(example) == "shared":
            assert words[0] & words[1] == {normalize_key(a.generic)}, example.id
        else:
            assert not words[0] & words[1], example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_generic_uses_the_generic_word_on_its_own_outside_the_gold_spans_capitalised(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        if _category(example) == "generic":
            (org,) = _orgs(example).values()
            assert list(_outside_gold(example, org.generic)) == [org.generic], example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_generic_word_and_every_legal_form_is_used(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    orgs = [org for example in corpora[lang] for org in _orgs(example).values()]
    assert {org.generic for org in orgs} - {""} == set(generator.GENERIC_WORDS[lang])
    assert {org.legal for org in orgs} == _legal_forms(lang)


def test_in_spanish_multi_no_word_after_y_starts_with_the_sound_of_i(
    corpora: dict[str, list[Example]],
) -> None:
    for example in corpora["es"]:
        if _category(example) == "multi":
            (org,) = _orgs(example).values()
            assert " y " in org.bare, example.id
            after = org.bare.split(" y ")[1]
            assert not re.match(r"h?[ií]", after, re.IGNORECASE), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_rules_detect_nothing_in_the_corpus(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    pseudonymizer = build_pseudonymizer(load_settings(None, {}))
    found = pseudonymizer.analyze([example.text for example in corpora[lang]])
    assert found == [[] for _ in corpora[lang]]


def test_the_dictionary_holds_the_first_full_name_of_every_organisation_in_order(
    corpora: dict[str, list[Example]],
) -> None:
    terms = load_terms(CORPUS / "orgs.terms.yaml")
    assert {term.entity_type for term in terms} == {"ORG"}
    assert all(term.aliases == () for term in terms)
    expected = []
    for lang in LANGUAGES:
        for example in corpora[lang]:
            found = sorted(
                (span.start, _surface(example, span))
                for span in example.spans
                if _is_full(lang, _surface(example, span))
            )
            seen: set[str] = set()
            for _, surface in found:
                if surface not in seen:
                    seen.add(surface)
                    expected.append(surface)
    assert [term.term for term in terms] == expected
    assert len(expected) == 2 * (180 + 40)
    text = (CORPUS / "orgs.terms.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Generated by tools/generate_org_corpus.py")
    assert "Synthetic values only." in text.split("terms:")[0]


# The harness on the corpus --------------------------------------------------------------------


@pytest.fixture(scope="module")
def reports(corpora: dict[str, list[Example]]) -> dict[str, Report]:
    """The corpus with its dictionary of full names, as CI evaluates it."""
    pseudonymizer = build_pseudonymizer(load_settings(CORPUS / "orgs.yaml", {}))
    return {lang: evaluate(pseudonymizer, corpora[lang]) for lang in LANGUAGES}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_with_the_dictionary_there_is_no_round_trip_failure(
    reports: dict[str, Report], lang: str
) -> None:
    assert reports[lang].round_trip_failures == 0


@pytest.mark.parametrize("lang", LANGUAGES)
def test_with_the_dictionary_every_org_span_of_full_is_fully_covered(
    reports: dict[str, Report], lang: str
) -> None:
    misses = [miss for miss in reports[lang].misses if miss.example_id.startswith(f"{lang}-full-")]
    assert misses == []
    assert [score.entity_type for score in reports[lang].linking] == ["ORG"]
