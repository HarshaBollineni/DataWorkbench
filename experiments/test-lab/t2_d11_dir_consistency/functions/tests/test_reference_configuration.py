from __future__ import annotations

import pytest

from functions.reference_configuration import (
    ReferenceConfig,
    ReferenceOrientation,
    ReferenceSelectionError,
    ReferenceType,
    select_reference_config,
)


TARGET = ReferenceConfig(
    variable="default_12",
    reference_type=ReferenceType.TARGET,
    orientation=ReferenceOrientation.HIGHER_IS_WORSE,
)
ANCHOR = ReferenceConfig(
    variable="rating",
    reference_type=ReferenceType.ANCHOR,
    orientation=ReferenceOrientation.HIGHER_IS_BETTER,
)


def test_valid_target_configuration():
    assert TARGET.variable == "default_12"
    assert TARGET.reference_type is ReferenceType.TARGET
    assert TARGET.orientation is ReferenceOrientation.HIGHER_IS_WORSE


def test_valid_anchor_configuration():
    assert ANCHOR.variable == "rating"
    assert ANCHOR.reference_type is ReferenceType.ANCHOR
    assert ANCHOR.orientation is ReferenceOrientation.HIGHER_IS_BETTER


def test_target_takes_precedence_when_available():
    metadata = [
        {"name": "default_12", "role": "Target"},
        {"name": "rating", "role": "Score"},
    ]

    selected = select_reference_config(metadata, target=TARGET, anchor=ANCHOR)

    assert selected is TARGET


def test_anchor_is_used_when_target_is_unavailable():
    metadata = [
        {"name": "rating", "role": "Score"},
        {"name": "income", "role": "Feature"},
    ]

    selected = select_reference_config(metadata, target=TARGET, anchor=ANCHOR)

    assert selected is ANCHOR


def test_failure_when_neither_target_nor_anchor_is_available():
    metadata = [{"name": "income", "role": "Feature"}]

    with pytest.raises(ReferenceSelectionError, match="no explicit ANCHOR"):
        select_reference_config(metadata)


@pytest.mark.parametrize("variable", [None, "", "   "])
def test_invalid_or_empty_variable(variable):
    with pytest.raises(ValueError, match="variable must be a non-empty string"):
        ReferenceConfig(
            variable=variable,
            reference_type=ReferenceType.TARGET,
            orientation=ReferenceOrientation.HIGHER_IS_WORSE,
        )


def test_invalid_reference_type():
    with pytest.raises(ValueError, match="reference_type must be a ReferenceType"):
        ReferenceConfig(
            variable="default_12",
            reference_type="TARGET",
            orientation=ReferenceOrientation.HIGHER_IS_WORSE,
        )


def test_invalid_orientation():
    with pytest.raises(ValueError, match="orientation must be a ReferenceOrientation"):
        ReferenceConfig(
            variable="default_12",
            reference_type=ReferenceType.TARGET,
            orientation="HIGHER_IS_WORSE",
        )


def test_target_must_be_explicitly_configured():
    metadata = [{"name": "default_12", "role": "TARGET"}]

    with pytest.raises(ReferenceSelectionError, match="explicit TARGET configuration"):
        select_reference_config(metadata, anchor=ANCHOR)


def test_configured_reference_must_exist_in_metadata():
    metadata = [{"name": "income", "role": "Feature"}]

    with pytest.raises(ReferenceSelectionError, match="not present"):
        select_reference_config(metadata, anchor=ANCHOR)
