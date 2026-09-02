"""T2-D11 Directional / monotonic consistency diagnostic."""

from .engine import (
    DirectionalityThresholds, EvidenceStrength, ObservedDirection,
    analyze_directionality, compare_expected_observed,
)

__all__ = ["DirectionalityThresholds", "EvidenceStrength", "ObservedDirection",
           "analyze_directionality", "compare_expected_observed"]
