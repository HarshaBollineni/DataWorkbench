"""Feature Target Separation orchestration over existing ROC and IV engines."""

from .models import (
    FeatureTargetSeparationResponse,
    SeparationFeatureResult,
    SeparationThresholds,
)
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
