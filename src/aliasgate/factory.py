"""Builds a ready Pseudonymizer from the configuration."""

from aliasgate.config import Settings, load_terms
from aliasgate.detectors.dictionary import DictionaryDetector
from aliasgate.detectors.rules import RuleDetector
from aliasgate.engine import Pseudonymizer
from aliasgate.types import Detector


def build_pseudonymizer(settings: Settings) -> Pseudonymizer:
    """Wire detectors in this order: dictionary (if any terms), rules. ML is added in slice 09."""
    detection = settings.detection
    if detection.ml.enabled:
        raise NotImplementedError("ML detection arrives in slice 09")
    terms = [term for path in detection.dictionary.files for term in load_terms(path)]
    detectors: list[Detector] = []
    if terms:
        detectors.append(DictionaryDetector(terms))
    detectors.append(
        RuleDetector(
            enabled_types=detection.rules.enabled_types,
            phone_regions=detection.rules.phone_regions,
        )
    )
    return Pseudonymizer(
        detectors,
        allow_list=detection.allow_list,
        propagate=detection.propagate,
        min_propagation_length=detection.min_propagation_length,
    )
