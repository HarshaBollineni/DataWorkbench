from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from functions.empirical_directionality import (
    EmpiricalThresholds,
    EvidenceStatus,
    ObservedDirection,
    ReferenceKind,
    analyze_empirical_directionality,
)


def _binary_sample(sign: int = 1) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(42)
    feature = np.linspace(-3, 3, 1000)
    probability = 1 / (1 + np.exp(-(sign * 1.4 * feature)))
    target = rng.binomial(1, probability)
    return pd.Series(feature), pd.Series(target)


@pytest.mark.parametrize(
    ("sign", "expected"),
    [(1, ObservedDirection.INCREASING), (-1, ObservedDirection.DECREASING)],
)
def test_binary_logistic_direction(sign, expected):
    feature, target = _binary_sample(sign)
    result = analyze_empirical_directionality(
        feature, target, reference_kind=ReferenceKind.BINARY
    )
    assert result.observed_direction is expected
    assert result.regression.model == "univariate_logistic_standardized"
    assert result.regression.material_direction == expected.value
    assert result.regression.intercept is not None
    assert result.regression.feature_std is not None


def test_uses_only_finite_paired_observations():
    feature, target = _binary_sample()
    feature.iloc[0] = np.nan
    feature.iloc[1] = np.inf
    target.iloc[2] = np.nan
    result = analyze_empirical_directionality(
        feature, target, reference_kind=ReferenceKind.BINARY
    )
    assert result.n_input == 1000
    assert result.n_paired == 997
    assert result.n_dropped == 3


def test_metadata_confirmed_special_values_are_excluded():
    feature, target = _binary_sample()
    feature.iloc[:20] = -999
    result = analyze_empirical_directionality(
        feature,
        target,
        reference_kind=ReferenceKind.BINARY,
        feature_missing_values=["-999"],
    )
    assert result.n_paired == 980
    assert result.n_dropped == 20
    assert result.n_special_value_dropped == 20


def test_categorical_feature_is_not_applicable():
    result = analyze_empirical_directionality(
        pd.Series(["a", "b"] * 20),
        pd.Series([0, 1] * 20),
        reference_kind=ReferenceKind.BINARY,
    )
    assert result.observed_direction is ObservedDirection.NOT_APPLICABLE
    assert result.pearson.status is EvidenceStatus.NOT_APPLICABLE


def test_constant_feature_is_insufficient():
    result = analyze_empirical_directionality(
        pd.Series([1.0] * 50),
        pd.Series([0, 1] * 25),
        reference_kind=ReferenceKind.BINARY,
    )
    assert result.observed_direction is ObservedDirection.INSUFFICIENT_EVIDENCE


def test_binary_reference_requires_two_well_represented_classes():
    result = analyze_empirical_directionality(
        pd.Series(range(50)),
        pd.Series([0] * 49 + [1]),
        reference_kind=ReferenceKind.BINARY,
    )
    assert result.observed_direction is ObservedDirection.INSUFFICIENT_EVIDENCE
    assert result.class_counts == {"0": 49, "1": 1}


def test_non_monotonic_binned_pattern_is_retained():
    feature = pd.Series(np.arange(500, dtype=float))
    # Symmetric U-shape: little global linear evidence, material two-way bin movement.
    target = pd.Series(((feature < 80) | (feature >= 420)).astype(int))
    result = analyze_empirical_directionality(
        feature, target, reference_kind=ReferenceKind.BINARY
    )
    assert result.observed_direction is ObservedDirection.NON_MONOTONIC
    assert result.binned.trend == "non_monotonic"


def test_weak_relationship_is_not_forced_to_a_direction():
    rng = np.random.default_rng(7)
    feature = pd.Series(rng.normal(size=2000))
    target = pd.Series(rng.binomial(1, 0.5, size=2000))
    strict = EmpiricalThresholds(
        min_abs_correlation=0.20,
        min_abs_standardized_logit_coefficient=0.50,
        min_bin_reference_change=0.10,
        min_bin_reference_range=0.30,
    )
    result = analyze_empirical_directionality(
        feature, target, reference_kind=ReferenceKind.BINARY, thresholds=strict
    )
    assert result.observed_direction is ObservedDirection.WEAK_NO_RELATIONSHIP


@pytest.mark.parametrize(
    ("sign", "expected"),
    [(1, ObservedDirection.INCREASING), (-1, ObservedDirection.DECREASING)],
)
def test_continuous_reference_uses_standardized_linear_model(sign, expected):
    rng = np.random.default_rng(21)
    feature = pd.Series(np.linspace(-4, 4, 1000))
    reference = pd.Series(sign * 2.5 * feature + rng.normal(0, 0.5, len(feature)))
    result = analyze_empirical_directionality(
        feature, reference, reference_kind=ReferenceKind.CONTINUOUS
    )
    assert result.observed_direction is expected
    assert result.event_value is None
    assert result.class_counts is None
    assert result.regression.model == "univariate_linear_standardized"
    assert result.regression.material_direction == expected.value
    assert result.regression.reference_mean is not None
    assert result.regression.reference_std is not None
    assert result.binned.reference_range_standardized is not None


def test_invalid_reference_kind_is_rejected():
    with pytest.raises(ValueError, match="ReferenceKind"):
        analyze_empirical_directionality(
            pd.Series(range(40)), pd.Series([0, 1] * 20), reference_kind="BINARY"
        )
