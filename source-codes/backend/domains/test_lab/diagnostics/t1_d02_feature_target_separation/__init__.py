"""T1-D02: Single-feature target separation."""

from .models import FeatureTargetSeparationResponse, SeparationFeatureResult, SeparationThresholds
from .orchestration import assess_feature_target_separation, classify_feature
from .adapter import FeatureTargetSeparationOutcome, assess_snapshot

__all__ = [
    "FeatureTargetSeparationResponse",
    "FeatureTargetSeparationOutcome",
    "SeparationFeatureResult",
    "SeparationThresholds",
    "assess_feature_target_separation",
    "assess_snapshot",
    "classify_feature",
]
