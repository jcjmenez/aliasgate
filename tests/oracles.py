"""Independent oracles for the engine's round-trip property (slice 03, shared since slice 11)."""

from collections.abc import Sequence

from aliasgate.engine import PseudonymizedBatch
from aliasgate.placeholders import TOLERANT
from aliasgate.terms import build_term_pattern


def propagation_oracle(
    texts: Sequence[str], batch: PseudonymizedBatch, *, min_propagation_length: int = 3
) -> list[str]:
    """Original texts with every propagatable case variant rendered as its canonical surface."""
    entities = sorted(
        (
            entity
            for entity in batch.mapping.entities()
            if entity.entity_type != "LITERAL" and len(entity.key) >= min_propagation_length
        ),
        key=lambda entity: len(entity.surface),
        reverse=True,
    )
    expected = []
    for text in texts:
        claimed = [(match.start(), match.end()) for match in TOLERANT.finditer(text)]
        replacements = []
        for entity in entities:
            for match in build_term_pattern(entity.surface).finditer(text):
                if all(match.end() <= start or end <= match.start() for start, end in claimed):
                    claimed.append((match.start(), match.end()))
                    replacements.append((match.start(), match.end(), entity.surface))
        for start, end, surface in sorted(replacements, reverse=True):
            text = text[:start] + surface + text[end:]
        expected.append(text)
    return expected
