"""The person mentions corpus (slice 15): its generator, its files and their shape."""

import random
import re
import unicodedata
from pathlib import Path

import pytest

import generate_person_corpus as generator
from aliasgate.config import load_settings, load_terms
from aliasgate.eval.corpus import Example, GoldSpan, load_corpus
from aliasgate.eval.metrics import Report, evaluate
from aliasgate.factory import build_pseudonymizer
from aliasgate.mapping import normalize_key

CORPUS = Path(__file__).parent / "corpus"
LANGUAGES = ("en", "es")
CATEGORIES = (
    "full",
    "first",
    "last",
    "title",
    "before",
    "run",
    "possessive",
    "shared-last",
    "shared-first",
    "other",
    "word",
    "sentence",
    "lower",
    "org",
)
_ID = re.compile(r"(en|es)-([a-z]+(?:-[a-z]+)?)-(\d{3})")
_NAME_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)?")


@pytest.fixture(scope="module")
def corpora() -> dict[str, list[Example]]:
    return {lang: load_corpus(CORPUS / f"persons.{lang}.jsonl") for lang in LANGUAGES}


def _category(example: Example) -> str:
    match = _ID.fullmatch(example.id)
    assert match is not None, example.id
    return match[2]


def _surface(example: Example, span: GoldSpan) -> str:
    return example.text[span.start : span.end]


def _persons(example: Example) -> dict[str, list[str]]:
    """The surfaces of each person, by entity id, in order of mention."""
    persons: dict[str, list[str]] = {}
    for span in example.spans:
        if span.entity_type == "PERSON":
            assert span.entity is not None, example.id
            persons.setdefault(span.entity, []).append(_surface(example, span))
    return persons


def _words(surfaces: list[str]) -> set[str]:
    return {word for surface in surfaces for word in normalize_key(surface).split()}


def _full_names(example: Example) -> list[str]:
    """The first full-name mention of each person who has one, in order of position: the
    first of its mentions with the most words, if that has at least two."""
    found: list[tuple[int, str]] = []
    for entity in _persons(example):
        spans = [s for s in example.spans if s.entity_type == "PERSON" and s.entity == entity]
        longest = max(len(_surface(example, span).split()) for span in spans)
        if longest >= 2:
            span = next(s for s in spans if len(_surface(example, s).split()) == longest)
            found.append((span.start, _surface(example, span)))
    return [surface for _, surface in sorted(found)]


def _outside_gold(example: Example, word: str) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
    return any(
        not any(span.start <= match.start() and match.end() <= span.end for span in example.spans)
        for match in pattern.finditer(example.text)
    )


# The generator --------------------------------------------------------------------------------


def test_check_reports_whether_the_files_differ_from_a_fresh_generation(tmp_path: Path) -> None:
    assert generator.main(["--out", str(tmp_path)]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "persons.en.jsonl",
        "persons.es.jsonl",
        "persons.terms.yaml",
    ]
    assert generator.main(["--check", "--out", str(tmp_path)]) == 0
    for name in ("persons.es.jsonl", "persons.terms.yaml"):
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


class _FixedNames:
    """Stands in for the generator's name source: always the same persons."""

    def __init__(self, persons: dict[str, generator._Person]) -> None:
        self.lang = "en"
        self.rng = random.Random(0)  # noqa: S311 - test data
        self._persons = persons

    def persons(self, category: str, labels: set[str]) -> dict[str, generator._Person]:
        return dict(self._persons)


def _drawn(category: str, template: str, **persons: generator._Person) -> object:
    names = _FixedNames(persons)
    return generator._example(category, 0, template, names)  # type: ignore[arg-type]


def test_the_generator_rejects_a_name_word_used_outside_the_gold_spans() -> None:
    will = generator._Person("male", "Will", "Stone")
    assert _drawn("first", "{a} sent it. {a.first} called.", a=will) is not None
    assert _drawn("first", "{a} sent it. Will you call?", a=will) is None
    assert _drawn("first", "{a} sent it. the stone floor.", a=will) is None
    # The listed name of `sentence` may be used as a common word; its other words may not.
    assert _drawn("sentence", "{a} sent it. {common} {a.first} called.", a=will) is not None
    assert _drawn("sentence", "{a} sent it. {common} Stone tiles.", a=will) is None


def test_the_generator_rejects_persons_sharing_a_word_the_category_does_not_allow() -> None:
    alice = generator._Person("female", "Alice", "Smith")
    bob, other_alice = (
        generator._Person("male", "Bob", "Smith"),
        generator._Person("female", "Alice", "Brown"),
    )
    template = "{a} and {b} called. {a.first} paid."
    assert _drawn("first", template, a=alice, b=bob) is None
    assert _drawn("shared-last", template, a=alice, b=bob) is not None
    assert _drawn("shared-last", template, a=alice, b=other_alice) is None
    assert _drawn("shared-first", template, a=alice, b=other_alice) is not None
    assert _drawn("first", template, a=alice, b=generator._Person("male", "Bob", "Bob")) is None


def test_the_generator_rejects_a_shorter_form_that_contains_a_full_name_of_the_corpus() -> None:
    corpus = generator._Corpus()
    corpus.add(generator._Example("", (), ("Alice Johnson",), ("Alice",)))
    run = generator._Example("", (), ("Alice Mary Johnson",), ("ALICE  johnson", "Mary"))
    assert not corpus.accepts(run)
    assert corpus.accepts(generator._Example("", (), ("Alice Mary Johnson",), ("Mary Johnson",)))
    corpus.add(generator._Example("", (), ("Bob Ray Lee",), ("Ray Lee",)))
    assert not corpus.accepts(generator._Example("", (), ("Ray Lee",), ()))
    assert not corpus.accepts(generator._Example("", (), ("Ann Cole",), ("Ann Cole Moore",)))
    assert corpus.accepts(generator._Example("", (), ("Ann Cole",), ("Ann",)))
    assert list(corpus.terms.values()) == ["Alice Johnson", "Bob Ray Lee"]


# The corpus files -----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_each_language_has_20_examples_in_each_of_14_categories(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    examples = corpora[lang]
    assert len(examples) == 280
    assert {example.lang for example in examples} == {lang}
    assert [example.id for example in examples] == [
        f"{lang}-{category}-{number:03d}" for category in CATEGORIES for number in range(1, 21)
    ]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_person_span_has_an_id_and_covers_a_bare_name(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        persons = _persons(example)
        assert persons, example.id
        assert list(persons) == [f"p{number}" for number in range(1, len(persons) + 1)]
        for surface in (surface for surfaces in persons.values() for surface in surfaces):
            assert not unicodedata.category(surface[0]).startswith(("P", "Z")), example.id
            assert not unicodedata.category(surface[-1]).startswith(("P", "Z")), example.id
            if _category(example) != "lower":
                assert surface[0].isupper(), example.id
            for word in surface.split():
                assert _NAME_WORD.fullmatch(word), example.id
                assert len(word) >= 3, example.id
                assert word.casefold() not in generator.TITLES | generator.PARTICLES, example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_lower_has_a_shorter_form_in_lower_case(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        if _category(example) == "lower":
            assert any(surface.islower() for surface in _persons(example)["p1"]), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_persons_share_a_word_only_where_the_category_says_so(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        category, persons = _category(example), _persons(example)
        if category in {"shared-last", "shared-first", "other"}:
            assert len(persons) == 2, example.id
        shared = _words(persons["p1"]) & _words(persons.get("p2", []))
        if category in {"shared-last", "shared-first"}:
            assert shared, example.id
        elif category == "other":
            # The second person is named only by the first word of the first one's name.
            (word,) = _words(persons["p2"])
            assert word == normalize_key(persons["p1"][0]).split()[0], example.id
            assert shared == {word}, example.id
        else:
            assert not shared, example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_in_word_and_sentence_the_word_from_the_list_also_occurs_outside_every_gold_span(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    words = {*generator.WORD_SURNAMES[lang], *generator.WORD_FIRST_NAMES[lang]}
    for example in corpora[lang]:
        if _category(example) in {"word", "sentence"}:
            (word,) = words & set(" ".join(_persons(example)["p1"]).split())
            assert _outside_gold(example, word), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_only_org_has_an_organisation_and_it_holds_the_persons_surname(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    for example in corpora[lang]:
        orgs = [span for span in example.spans if span.entity_type != "PERSON"]
        if _category(example) != "org":
            assert not orgs, example.id
            continue
        (org,) = orgs
        assert (org.entity_type, org.entity) == ("ORG", "o1"), example.id
        surname = _surface(example, org).split()[0]
        assert surname in _persons(example)["p1"], example.id
        assert surname in _persons(example)["p1"][0].split(), example.id


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_name_word_occurs_outside_the_gold_spans_except_the_common_word(
    corpora: dict[str, list[Example]], lang: str
) -> None:
    common = {*generator.WORD_SURNAMES[lang], *generator.WORD_FIRST_NAMES[lang]}
    for example in corpora[lang]:
        for surfaces in _persons(example).values():
            for word in {word for surface in surfaces for word in surface.split()}:
                if _category(example) in {"word", "sentence"} and word in common:
                    continue
                assert not _outside_gold(example, word), example.id


def test_the_dictionary_holds_the_first_full_name_mention_of_every_person_once(
    corpora: dict[str, list[Example]],
) -> None:
    terms = load_terms(CORPUS / "persons.terms.yaml")
    assert {term.entity_type for term in terms} == {"PERSON"}
    assert all(term.aliases == () for term in terms)
    expected: dict[str, str] = {}
    for lang in LANGUAGES:
        for example in corpora[lang]:
            for surface in _full_names(example):
                expected.setdefault(normalize_key(surface), surface)
    assert [term.term for term in terms] == list(expected.values())
    assert (CORPUS / "persons.terms.yaml").read_text(encoding="utf-8").startswith("# ")


def test_no_shorter_form_of_several_words_contains_a_full_name_of_the_corpus(
    corpora: dict[str, list[Example]],
) -> None:
    full = {normalize_key(term.term) for term in load_terms(CORPUS / "persons.terms.yaml")}
    for lang in LANGUAGES:
        for example in corpora[lang]:
            names = set(map(normalize_key, _full_names(example)))
            for surfaces in _persons(example).values():
                for surface in surfaces:
                    words = normalize_key(surface).split()
                    if normalize_key(surface) in names:
                        continue
                    for size in range(2, len(words) + 1):
                        for start in range(len(words) - size + 1):
                            part = " ".join(words[start : start + size])
                            assert part not in full, example.id


# The harness on the corpus --------------------------------------------------------------------


@pytest.fixture(scope="module")
def reports(corpora: dict[str, list[Example]]) -> dict[str, Report]:
    """The corpus with its dictionary of full names, as CI evaluates it."""
    pseudonymizer = build_pseudonymizer(load_settings(CORPUS / "persons.yaml", {}))
    return {lang: evaluate(pseudonymizer, corpora[lang]) for lang in LANGUAGES}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_with_the_dictionary_there_is_no_round_trip_failure(
    reports: dict[str, Report], lang: str
) -> None:
    assert reports[lang].round_trip_failures == 0


@pytest.mark.parametrize("lang", LANGUAGES)
def test_with_the_dictionary_every_person_span_of_full_is_fully_covered(
    reports: dict[str, Report], lang: str
) -> None:
    misses = [miss for miss in reports[lang].misses if miss.example_id.startswith(f"{lang}-full-")]
    assert misses == []
    assert any(score.entity_type == "PERSON" for score in reports[lang].linking)
