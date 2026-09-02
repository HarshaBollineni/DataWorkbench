"""Baseline validation harness for the existing feature matcher.

Ground-truth fields are deliberately separated from matcher inputs. This
module evaluates the current implementation; it does not tune or alter it.
"""
from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .feature_matching import match_feature_to_kb, prepare_feature_matcher


VALIDATION_REQUIRED_COLUMNS = (
    "feature_name",
    "description",
    "expected_canonical_feature",
    "expected_match_type",
    "expected_representation_type",
    "test_category",
)


def load_validation_dataset(path: str | Path) -> pd.DataFrame:
    """Load and validate labelled feature-matching cases without imputing labels."""
    validation_path = Path(path)
    if not validation_path.is_file():
        raise FileNotFoundError(f"Feature-matching validation CSV not found: {validation_path}")
    try:
        frame = pd.read_csv(validation_path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.ParserError) as exc:
        raise ValueError(f"Could not read validation CSV {validation_path}: {exc}") from exc

    missing = [column for column in VALIDATION_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Validation CSV is missing required column(s): {', '.join(missing)}")
    frame = frame.loc[:, VALIDATION_REQUIRED_COLUMNS].copy()
    for column in VALIDATION_REQUIRED_COLUMNS:
        frame[column] = frame[column].astype(str).str.strip()

    if frame.empty:
        raise ValueError("Validation CSV must contain at least one case")
    blank_names = frame.index[frame["feature_name"].eq("")].tolist()
    if blank_names:
        raise ValueError(f"Validation feature_name is blank at row(s): {blank_names}")

    allowed_match_types = {"match", "no_match"}
    invalid_match_types = sorted(set(frame["expected_match_type"]) - allowed_match_types)
    if invalid_match_types:
        raise ValueError(
            "expected_match_type must be 'match' or 'no_match'; found: "
            + ", ".join(repr(value) for value in invalid_match_types)
        )
    missing_match_labels = frame[
        frame["expected_match_type"].eq("match")
        & frame["expected_canonical_feature"].eq("")
    ]
    if not missing_match_labels.empty:
        raise ValueError("Expected match rows require expected_canonical_feature")
    labelled_no_matches = frame[
        frame["expected_match_type"].eq("no_match")
        & frame["expected_canonical_feature"].ne("")
    ]
    if not labelled_no_matches.empty:
        raise ValueError("Expected no_match rows must have blank expected_canonical_feature")

    allowed_representation_types = {"", "same_orientation", "inverse"}
    invalid_representation_types = sorted(
        set(frame["expected_representation_type"]) - allowed_representation_types
    )
    if invalid_representation_types:
        raise ValueError(
            "Unexpected expected_representation_type value(s): "
            + ", ".join(repr(value) for value in invalid_representation_types)
        )
    return frame


def _predicted_candidate_details(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    deterministic = result["deterministic_match"]
    if deterministic:
        return [
            {
                "rank": 1,
                "canonical_feature": deterministic["canonical_feature"],
                "combined_similarity_score": 1.0,
                "rapidfuzz_score": 1.0,
                "tfidf_cosine_score": 1.0,
                "best_matching_kb_text": deterministic["matched_kb_text"],
                "best_matching_kb_text_source": deterministic["match_source"],
                "representation_evidence": (
                    "inverse"
                    if deterministic["is_inverse_representation"]
                    else "normal"
                ),
            }
        ]
    return list(result["top_candidates"])


def _prediction_row(result: Mapping[str, Any]) -> dict[str, Any]:
    candidates = _predicted_candidate_details(result)
    first = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None
    source = first["best_matching_kb_text_source"] if first else None
    representation_type = None
    if first:
        representation_type = (
            "inverse"
            if first["representation_evidence"] == "inverse"
            else "same_orientation"
        )
    return {
        "match_status": result["match_status"],
        "match_method": result["match_method"],
        "processed_feature_text": result["processed_feature_text"],
        "top_candidate": first["canonical_feature"] if first else None,
        "combined_score": first["combined_similarity_score"] if first else None,
        "second_candidate": second["canonical_feature"] if second else None,
        "second_score": second["combined_similarity_score"] if second else None,
        "score_gap": result["top_1_minus_top_2_score_gap"],
        "match_source_type": source,
        "predicted_representation_type": representation_type,
        "unresolved_ambiguities": [
            item["abbreviation"]
            for item in result["unresolved_ambiguous_terminology"]
        ],
        "top_3_candidates": candidates[:3],
        "top_3_features": [candidate["canonical_feature"] for candidate in candidates[:3]],
        "matcher_result": dict(result),
    }


def run_validation(
    validation: pd.DataFrame,
    kb: Mapping[str, Any],
    terminology: Mapping[str, Any],
    *,
    top_n: int = 3,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Run the matcher with only feature_name/description, then attach labels."""
    missing = [column for column in VALIDATION_REQUIRED_COLUMNS if column not in validation]
    if missing:
        raise ValueError(f"Validation data is missing required column(s): {', '.join(missing)}")

    # This is the intentional leakage barrier. Expected-answer fields never
    # enter matcher_inputs or a matcher call.
    matcher_inputs = validation.loc[:, ["feature_name", "description"]].copy()
    matcher_results: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    prepared_matcher = prepare_feature_matcher(kb, terminology)
    for row in matcher_inputs.itertuples(index=False):
        result = match_feature_to_kb(
            feature_name=row.feature_name,
            feature_description=row.description,
            kb=kb,
            terminology=terminology,
            top_n=top_n,
            prepared_matcher=prepared_matcher,
        )
        matcher_results.append(result)
        prediction_rows.append(_prediction_row(result))

    ground_truth = validation.loc[:, VALIDATION_REQUIRED_COLUMNS].reset_index(drop=True)
    predictions = pd.DataFrame(prediction_rows)
    evaluation = pd.concat([ground_truth, predictions], axis=1)

    expected_match = evaluation["expected_match_type"].eq("match")
    canonical_correct = evaluation["top_candidate"].eq(
        evaluation["expected_canonical_feature"]
    )
    evaluation["top1_correct"] = expected_match & canonical_correct
    evaluation["expected_in_top3"] = [
        bool(is_match and expected in candidates)
        for is_match, expected, candidates in zip(
            expected_match,
            evaluation["expected_canonical_feature"],
            evaluation["top_3_features"],
            strict=True,
        )
    ]
    expected_no_match = evaluation["expected_match_type"].eq("no_match")
    evaluation["no_match_correct"] = expected_no_match & evaluation["match_status"].eq(
        "no_match"
    )
    has_representation_expectation = evaluation["expected_representation_type"].ne("")
    evaluation["representation_type_correct"] = (
        expected_match
        & canonical_correct
        & has_representation_expectation
        & evaluation["predicted_representation_type"].eq(
            evaluation["expected_representation_type"]
        )
    )
    return evaluation, matcher_results


def _safe_rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def overall_validation_summary(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Return the requested overall baseline metrics as a compact table."""
    matchable = evaluation["expected_match_type"].eq("match")
    expected_no_match = evaluation["expected_match_type"].eq("no_match")
    exact = evaluation["match_status"].eq("exact_match")
    inverse = evaluation["expected_representation_type"].eq("inverse")

    matchable_count = int(matchable.sum())
    top1_count = int(evaluation.loc[matchable, "top1_correct"].sum())
    top3_count = int(evaluation.loc[matchable, "expected_in_top3"].sum())
    exact_count = int(exact.sum())
    exact_correct = int((exact & evaluation["top1_correct"]).sum())
    no_match_count = int(expected_no_match.sum())
    no_match_correct = int(evaluation.loc[expected_no_match, "no_match_correct"].sum())
    false_matches = no_match_count - no_match_correct
    inverse_count = int(inverse.sum())
    inverse_correct = int(evaluation.loc[inverse, "representation_type_correct"].sum())

    values = [
        ("validation_cases", len(evaluation), None),
        ("matchable_cases", matchable_count, None),
        ("top1_correct", top1_count, _safe_rate(top1_count, matchable_count)),
        ("top3_correct", top3_count, _safe_rate(top3_count, matchable_count)),
        ("deterministic_exact_matches", exact_count, None),
        ("deterministic_exact_correct", exact_correct, _safe_rate(exact_correct, exact_count)),
        ("expected_no_match_cases", no_match_count, None),
        ("no_match_correct", no_match_correct, _safe_rate(no_match_correct, no_match_count)),
        ("false_match_count", false_matches, _safe_rate(false_matches, no_match_count)),
        ("inverse_cases", inverse_count, None),
        ("inverse_correct", inverse_correct, _safe_rate(inverse_correct, inverse_count)),
    ]
    return pd.DataFrame(values, columns=["metric", "count", "rate"])


def performance_by_category(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Summarize match retrieval and no-match behavior by labelled category."""
    rows = []
    for category, group in evaluation.groupby("test_category", sort=True):
        matchable = group["expected_match_type"].eq("match")
        expected_no_match = group["expected_match_type"].eq("no_match")
        matchable_count = int(matchable.sum())
        top1 = int(group.loc[matchable, "top1_correct"].sum())
        top3 = int(group.loc[matchable, "expected_in_top3"].sum())
        no_match_count = int(expected_no_match.sum())
        no_match_correct = int(group.loc[expected_no_match, "no_match_correct"].sum())
        rows.append(
            {
                "test_category": category,
                "cases": len(group),
                "matchable_cases": matchable_count,
                "top1_correct": top1,
                "top1_accuracy": _safe_rate(top1, matchable_count),
                "top3_correct": top3,
                "top3_accuracy": _safe_rate(top3, matchable_count),
                "expected_no_match_cases": no_match_count,
                "no_match_correct": no_match_correct,
                "no_match_accuracy": _safe_rate(no_match_correct, no_match_count),
            }
        )
    return pd.DataFrame(rows)


def error_analysis_tables(evaluation: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build focused error tables without mutating the evaluation frame."""
    expected_match = evaluation["expected_match_type"].eq("match")
    incorrect_top1_mask = expected_match & ~evaluation["top1_correct"]
    top3_recovery_mask = incorrect_top1_mask & evaluation["expected_in_top3"]
    missing_top3_mask = expected_match & ~evaluation["expected_in_top3"]
    false_match_mask = (
        evaluation["expected_match_type"].eq("no_match")
        & ~evaluation["no_match_correct"]
    )
    wrong_exact_mask = evaluation["match_status"].eq("exact_match") & (
        ~expected_match | ~evaluation["top1_correct"]
    )
    wrong_inverse_mask = evaluation["expected_representation_type"].eq("inverse") & (
        ~evaluation["representation_type_correct"]
    )

    common = [
        "feature_name",
        "description",
        "expected_canonical_feature",
        "top_candidate",
        "combined_score",
        "second_candidate",
        "second_score",
        "score_gap",
        "test_category",
    ]
    return {
        "incorrect_top1": evaluation.loc[incorrect_top1_mask, common].reset_index(drop=True),
        "top1_wrong_top3_correct": evaluation.loc[
            top3_recovery_mask, [*common, "top_3_candidates"]
        ].reset_index(drop=True),
        "expected_missing_top3": evaluation.loc[
            missing_top3_mask, [*common, "top_3_candidates"]
        ].reset_index(drop=True),
        "false_matches": evaluation.loc[
            false_match_mask,
            [
                "feature_name",
                "description",
                "match_status",
                "top_candidate",
                "combined_score",
                "second_candidate",
                "second_score",
                "score_gap",
            ],
        ].reset_index(drop=True),
        "wrong_deterministic_exact": evaluation.loc[
            wrong_exact_mask,
            [*common, "match_source_type"],
        ].reset_index(drop=True),
        "incorrect_inverse": evaluation.loc[
            wrong_inverse_mask,
            [
                "feature_name",
                "description",
                "expected_canonical_feature",
                "top_candidate",
                "expected_representation_type",
                "predicted_representation_type",
                "match_source_type",
                "combined_score",
            ],
        ].reset_index(drop=True),
    }


def score_distribution_summary(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Describe scores for correct, incorrect, and expected-no-match groups."""
    groups = {
        "correct_top1": evaluation[
            evaluation["expected_match_type"].eq("match") & evaluation["top1_correct"]
        ],
        "incorrect_top1": evaluation[
            evaluation["expected_match_type"].eq("match") & ~evaluation["top1_correct"]
        ],
        "expected_no_match": evaluation[evaluation["expected_match_type"].eq("no_match")],
    }
    rows = []
    for group_name, group in groups.items():
        for metric in ("combined_score", "score_gap"):
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            rows.append(
                {
                    "group": group_name,
                    "metric": metric,
                    "count": int(values.count()),
                    "minimum": values.min() if not values.empty else None,
                    "p25": values.quantile(0.25) if not values.empty else None,
                    "median": values.median() if not values.empty else None,
                    "p75": values.quantile(0.75) if not values.empty else None,
                    "maximum": values.max() if not values.empty else None,
                    "mean": values.mean() if not values.empty else None,
                }
            )
    return pd.DataFrame(rows)


def compact_evaluation_output(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Return the stable, flat row-level table written by the notebook."""
    return (
        evaluation.drop(columns=["matcher_result", "top_3_candidates"], errors="ignore")
        .sort_values(["test_category", "feature_name", "description"])
        .reset_index(drop=True)
    )


def _csv_canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize values exactly as the CSV boundary represents them."""
    return pd.read_csv(StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False)


def compare_validation_outputs(
    previous: pd.DataFrame,
    current: pd.DataFrame,
) -> pd.DataFrame:
    """Return one row per CSV-level deviation between two validation runs."""
    columns = [
        "deviation_type",
        "feature_name",
        "description",
        "column",
        "previous_value",
        "current_value",
    ]
    previous_csv = _csv_canonical_frame(previous)
    current_csv = _csv_canonical_frame(current)
    key_columns = ["feature_name", "description"]

    missing_keys = [
        key for key in key_columns if key not in previous_csv or key not in current_csv
    ]
    if missing_keys:
        raise ValueError(f"Comparison data is missing key column(s): {', '.join(missing_keys)}")
    if previous_csv.duplicated(key_columns).any() or current_csv.duplicated(key_columns).any():
        raise ValueError("Comparison keys feature_name + description must be unique")

    deviations: list[dict[str, str]] = []
    previous_columns = list(previous_csv.columns)
    current_columns = list(current_csv.columns)
    for column in sorted(set(previous_columns) ^ set(current_columns)):
        deviations.append(
            {
                "deviation_type": "schema",
                "feature_name": "",
                "description": "",
                "column": column,
                "previous_value": "present" if column in previous_csv else "missing",
                "current_value": "present" if column in current_csv else "missing",
            }
        )

    merged = previous_csv.merge(
        current_csv,
        how="outer",
        on=key_columns,
        suffixes=("__previous", "__current"),
        indicator=True,
    )
    for values in merged.to_dict(orient="records"):
        membership = values["_merge"]
        if membership != "both":
            deviations.append(
                {
                    "deviation_type": "row",
                    "feature_name": values["feature_name"],
                    "description": values["description"],
                    "column": "__row__",
                    "previous_value": "present" if membership == "left_only" else "missing",
                    "current_value": "present" if membership == "right_only" else "missing",
                }
            )
            continue
        for column in sorted(set(previous_columns) & set(current_columns) - set(key_columns)):
            previous_value = values[f"{column}__previous"]
            current_value = values[f"{column}__current"]
            if previous_value != current_value:
                deviations.append(
                    {
                        "deviation_type": "value",
                        "feature_name": values["feature_name"],
                        "description": values["description"],
                        "column": column,
                        "previous_value": previous_value,
                        "current_value": current_value,
                    }
                )
    return pd.DataFrame(deviations, columns=columns)


def validate_feature_matcher(
    validation_path: str | Path,
    kb: Mapping[str, Any],
    terminology: Mapping[str, Any],
    *,
    top_n: int = 3,
) -> dict[str, Any]:
    """Run the complete baseline evaluation and return notebook-ready artifacts."""
    validation = load_validation_dataset(validation_path)
    evaluation, matcher_results = run_validation(
        validation,
        kb,
        terminology,
        top_n=top_n,
    )
    return {
        "validation": validation,
        "evaluation": evaluation,
        "matcher_results": matcher_results,
        "overall_summary": overall_validation_summary(evaluation),
        "category_summary": performance_by_category(evaluation),
        "errors": error_analysis_tables(evaluation),
        "score_distributions": score_distribution_summary(evaluation),
    }


__all__ = [
    "VALIDATION_REQUIRED_COLUMNS",
    "compact_evaluation_output",
    "compare_validation_outputs",
    "error_analysis_tables",
    "load_validation_dataset",
    "overall_validation_summary",
    "performance_by_category",
    "run_validation",
    "score_distribution_summary",
    "validate_feature_matcher",
]
