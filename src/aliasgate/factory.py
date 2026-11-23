"""Builds a ready Pseudonymizer from the configuration."""

from itertools import cycle, islice

from aliasgate.config import MLSettings, Settings, load_terms
from aliasgate.detectors.cache import CachedDetector
from aliasgate.detectors.dictionary import DictionaryDetector
from aliasgate.detectors.gliner import GlinerDetector, load_gliner
from aliasgate.detectors.rules import RuleDetector
from aliasgate.engine import Pseudonymizer
from aliasgate.types import Detector

# Synthetic words, each one model word, repeated to fill one whole window at warm-up.
_WARM_UP_WORDS = (
    "Alice",
    "Johnson",
    "from",
    "ACME",
    "Corporation",
    "met",
    "Juan",
    "Perez",
    "in",
    "Springfield",
)


def build_pseudonymizer(settings: Settings) -> Pseudonymizer:
    """Wire detectors in this order: dictionary (if any terms), rules, ML (if enabled).

    The ML model is loaded here, at start-up, and run once on a synthetic text of one full
    window, so that a missing extra, a broken model or a window the model would truncate stops
    the gateway before it serves a request.
    """
    detection = settings.detection
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
    if detection.ml.enabled:
        detectors.append(_ml_detector(detection.ml))
    return Pseudonymizer(
        detectors,
        allow_list=detection.allow_list,
        propagate=detection.propagate,
        min_propagation_length=detection.min_propagation_length,
        person_tokens=detection.person_tokens,
        org_tokens=detection.org_tokens,
    )


def _ml_detector(ml: MLSettings) -> CachedDetector:
    detector = GlinerDetector(
        load_gliner(ml.model, ml.revision),
        model_id=ml.model,
        revision=ml.revision,
        labels=ml.labels,
        threshold=ml.threshold,
        window_words=ml.window_words,
        overlap_words=ml.overlap_words,
    )
    detector.detect(" ".join(islice(cycle(_WARM_UP_WORDS), ml.window_words)))
    return CachedDetector(detector, fingerprint=detector.fingerprint, max_entries=ml.cache_entries)
