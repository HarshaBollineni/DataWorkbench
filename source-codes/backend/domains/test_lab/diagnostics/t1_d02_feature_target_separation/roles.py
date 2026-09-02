"""Diagnostic-local role intelligence for feature-target separation.

Data Sourcing roles are useful defaults, not execution permissions.  This
diagnostic can attempt any column other than its confirmed target; roles and
types only determine the initial recommendation shown at the scope gate.
"""
from __future__ import annotations

from typing import Any

from ingest.roles import infer_inventory_role


EXCLUDED_ROLES = {"target", "identifier", "period", "date", "weight", "group", "ignore"}
RECOMMENDED_ROLES = {"feature", "score"}
KNOWN_ROLES = EXCLUDED_ROLES | RECOMMENDED_ROLES
TEMPORAL_TYPES = {"date", "datetime", "period", "timestamp"}


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _profile_value(row: dict[str, Any], *keys: str) -> Any:
    profile = row.get("profile_json") or {}
    summary = profile.get("summary") or {}
    for source in (row, profile, summary):
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _profile_warning(row: dict[str, Any]) -> str | None:
    levels = _profile_value(
        row, "n_levels", "distinct_count", "cardinality", "unique_count")
    stddev = _profile_value(
        row, "stddev", "std", "stddev_value", "standard_deviation")
    try:
        one_level = levels is not None and int(levels) <= 1
    except (TypeError, ValueError):
        one_level = False
    try:
        zero_variance = stddev is not None and float(stddev) == 0.0
    except (TypeError, ValueError):
        zero_variance = False
    if one_level and zero_variance:
        return ("Not recommended: the saved profile has 1 observed level and std = 0, "
                "so this feature has no separation power. You can still select it.")
    if one_level:
        return ("Not recommended: the saved profile has 1 observed level, so this feature "
                "has no separation power. You can still select it.")
    if zero_variance:
        return ("Not recommended: the saved profile has std = 0, so this feature has no "
                "separation power. You can still select it.")
    return None


def effective_role(row: dict[str, Any]) -> str:
    """Resolve role from the saved schema, with fallbacks only when unset."""
    role = _normalized(row.get("role"))
    dictionary_role = _normalized(row.get("dictionary_role"))
    if role:
        return role
    if dictionary_role in KNOWN_ROLES:
        return dictionary_role
    if not dictionary_role:
        profile = row.get("profile_json") or {}
        non_null = profile.get("count") or (profile.get("summary") or {}).get("count")
        distinct = profile.get("distinct_count", profile.get("unique_count"))
        unique_text = (
            str(row.get("classification") or "").lower() in {"text", "categorical"}
            and non_null is not None
            and distinct is not None
            and int(non_null) >= 20
            and int(distinct) == int(non_null)
        )
        inferred = infer_inventory_role(
            row.get("column_name"), row.get("classification"), unique_text=unique_text,
        )
        if inferred != "Feature":
            return inferred.lower()
    return dictionary_role or "feature"


def is_eligible_feature(row: dict[str, Any]) -> bool:
    """Return whether the diagnostic can accept the column when selected.

    The confirmed target is removed by the caller. No saved schema role is a
    hard execution gate.
    """
    return bool(str(row.get("column_name") or "").strip())


def is_recommended_feature(row: dict[str, Any]) -> bool:
    if (not is_eligible_feature(row) or _profile_warning(row)
            or effective_role(row) not in RECOMMENDED_ROLES):
        return False
    classification = _normalized(row.get("classification") or row.get("logical_type"))
    data_type = _normalized(row.get("data_type") or row.get("observed_type"))
    return not any(token in classification or token in data_type for token in TEMPORAL_TYPES)


def recommendation_reason(row: dict[str, Any]) -> str | None:
    """Explain a diagnostic recommendation without preventing an override."""
    profile_warning = _profile_warning(row)
    if profile_warning:
        return profile_warning
    if is_recommended_feature(row):
        return None
    role = effective_role(row)
    classification = _normalized(row.get("classification") or row.get("logical_type"))
    data_type = _normalized(row.get("data_type") or row.get("observed_type"))
    if any(token in classification or token in data_type for token in TEMPORAL_TYPES):
        return ("Not recommended: this diagnostic treats temporal values as categories, "
                "which may produce unstable separation. You can still select it.")
    return (f"Not recommended: the saved schema role is {role.title()}. This role is "
            "advisory for this diagnostic, so you can still select it.")
