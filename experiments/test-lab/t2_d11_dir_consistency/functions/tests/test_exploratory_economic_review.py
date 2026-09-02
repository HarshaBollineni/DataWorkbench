from functions.exploratory_economic_review import conclusion, expected_reference_direction
from functions.reference_configuration import ReferenceOrientation


def test_higher_is_better_flips_directional_risk_expectation():
    assert expected_reference_direction(
        "increasing", ReferenceOrientation.HIGHER_IS_BETTER
    ) == "decreasing"
    assert expected_reference_direction(
        "decreasing", ReferenceOrientation.HIGHER_IS_BETTER
    ) == "increasing"


def test_non_directional_states_pass_through():
    assert expected_reference_direction(
        "non_monotonic", ReferenceOrientation.HIGHER_IS_BETTER
    ) == "non_monotonic"
    assert expected_reference_direction(
        "no_clear_direction", ReferenceOrientation.HIGHER_IS_BETTER
    ) == "no_clear_direction"


def test_review_conclusions_do_not_force_unclear_cases():
    assert conclusion("increasing", "increasing") == "agreement"
    assert conclusion("increasing", "decreasing") == "disagreement"
    assert conclusion("no_clear_direction", "increasing") == "no_economic_prior"
    assert (
        conclusion("non_monotonic", "decreasing")
        == "expected_non_monotonic_not_confirmed"
    )
