"""Contracts for the composed Feature Target Separation workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from domains.test_lab.shared.binning.models import FeatureBinningFailure, FeatureBinningResult
from .roc_gini.models import FeatureResult, LocalizedLeakageScan, TargetResult

SeparationCategory = Literal["suspicious", "strong", "medium", "weak"]


class SeparationThresholds(BaseModel):
    """User-tunable boundaries for the mandated precedence classification."""

    suspicious_auc: float = Field(0.90, ge=0.5, le=1.0)
    strong_auc: float = Field(0.70, ge=0.5, le=1.0)
    medium_auc: float = Field(0.55, ge=0.5, le=1.0)
    suspicious_iv: float = Field(0.50, ge=0.0)
    strong_iv: float = Field(0.30, ge=0.0)
    medium_iv: float = Field(0.05, ge=0.0)

    @model_validator(mode="after")
    def validate_ordering(self) -> SeparationThresholds:
        if not self.medium_auc <= self.strong_auc <= self.suspicious_auc:
            raise ValueError("AUC thresholds must be ordered medium <= strong <= suspicious.")
        if not self.medium_iv <= self.strong_iv <= self.suspicious_iv:
            raise ValueError("IV thresholds must be ordered medium <= strong <= suspicious.")
        return self


class SeparationFeatureResult(BaseModel):
    feature: str
    auc: float | None = None
    iv: float | None = None
    category: SeparationCategory
    leakage_status: Literal["candidate", "clear", "not_available"]
    roc: FeatureResult | None = None
    binning: FeatureBinningResult | None = None
    errors: list[str] = Field(default_factory=list)


class FeatureTargetSeparationResponse(BaseModel):
    status: Literal["complete", "partial", "action_required", "invalid"]
    target: TargetResult
    localized_leakage_scan: LocalizedLeakageScan | None = None
    results: list[SeparationFeatureResult] = Field(default_factory=list)
    binning_failures: list[FeatureBinningFailure] = Field(default_factory=list)
    classification_thresholds: SeparationThresholds
    methodology: dict[str, object]
