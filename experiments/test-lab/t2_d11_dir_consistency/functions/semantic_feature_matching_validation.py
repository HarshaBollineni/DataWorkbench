"""Leakage-safe evaluation for the sentence-embedding challenger."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .feature_matching_validation import load_validation_dataset
from .semantic_feature_matching import SemanticFeatureIndex, match_features_semantic


def run_semantic_validation(
    validation: pd.DataFrame,
    kb: dict[str, Any],
    terminology: dict[str, Any],
    semantic_index: SemanticFeatureIndex,
    *,
    top_n: int = 3,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Run only input fields through the challenger, then attach labels."""
    matcher_inputs = validation.loc[:, ["feature_name", "description"]].copy()
    results = match_features_semantic(
        matcher_inputs,
        kb,
        terminology,
        semantic_index,
        top_n=top_n,
    )
    prediction_rows = []
    for result in results:
        exact = result["deterministic_match"]
        prediction_rows.append(
            {
                "challenger_match_status": result["match_status"],
                "challenger_match_method": result["match_method"],
                "processed_feature_text": result["processed_feature_text"],
                "challenger_top_candidate": result["top_1_canonical_feature"],
                "challenger_top1_cosine": result["top_1_cosine_similarity"],
                "challenger_second_candidate": result["top_2_canonical_feature"],
                "challenger_top2_cosine": result["top_2_cosine_similarity"],
                "challenger_third_candidate": result["top_3_canonical_feature"],
                "challenger_top3_cosine": result["top_3_cosine_similarity"],
                "challenger_score_gap": result["top_1_minus_top_2_similarity_gap"],
                "challenger_match_source_type": (
                    exact["match_source"] if exact else "semantic_document"
                ),
                "challenger_predicted_representation_type": (
                    "inverse"
                    if exact and exact["is_inverse_representation"]
                    else "same_orientation"
                    if exact
                    else "not_determined"
                ),
                "unresolved_ambiguities": json.dumps(
                    [
                        item["abbreviation"]
                        for item in result["unresolved_ambiguous_terminology"]
                    ]
                ),
                "challenger_top3_candidates": json.dumps(
                    result["top_3_candidates"], sort_keys=True
                ),
                "embedding_only_top_candidate": result["embedding_only_top_1"],
                "embedding_only_top1_cosine": result["embedding_only_top_1_score"],
                "embedding_only_second_candidate": result["embedding_only_top_2"],
                "embedding_only_top2_cosine": result["embedding_only_top_2_score"],
                "embedding_only_third_candidate": result["embedding_only_top_3"],
                "embedding_only_top3_cosine": result["embedding_only_top_3_score"],
                "embedding_only_score_gap": result["embedding_only_score_gap"],
                "embedding_only_top3_candidates": json.dumps(
                    result["embedding_only_top_3_candidates"], sort_keys=True
                ),
                "semantic_model": result["semantic_model"],
                "embedding_dimension": result["embedding_dimension"],
            }
        )

    evaluation = pd.concat(
        [validation.reset_index(drop=True), pd.DataFrame(prediction_rows)], axis=1
    )
    expected_match = evaluation["expected_match_type"].eq("match")
    expected_no_match = evaluation["expected_match_type"].eq("no_match")
    evaluation["challenger_top1_correct"] = expected_match & evaluation[
        "challenger_top_candidate"
    ].eq(evaluation["expected_canonical_feature"])
    evaluation["challenger_expected_in_top3"] = [
        bool(
            is_match
            and expected
            in {result["top_1_canonical_feature"], result["top_2_canonical_feature"], result["top_3_canonical_feature"]}
        )
        for is_match, expected, result in zip(
            expected_match,
            evaluation["expected_canonical_feature"],
            results,
            strict=True,
        )
    ]
    evaluation["challenger_no_match_correct"] = expected_no_match & evaluation[
        "challenger_match_status"
    ].eq("no_match")
    evaluation["challenger_representation_type_correct"] = (
        expected_match
        & evaluation["challenger_top_candidate"].eq(
            evaluation["expected_canonical_feature"]
        )
        & evaluation["challenger_predicted_representation_type"].eq(
            evaluation["expected_representation_type"]
        )
    )
    evaluation["embedding_only_top1_correct"] = expected_match & evaluation[
        "embedding_only_top_candidate"
    ].eq(evaluation["expected_canonical_feature"])
    evaluation["embedding_only_expected_in_top3"] = [
        bool(
            is_match
            and expected
            in {
                row.embedding_only_top_candidate,
                row.embedding_only_second_candidate,
                row.embedding_only_third_candidate,
            }
        )
        for is_match, expected, row in zip(
            expected_match,
            evaluation["expected_canonical_feature"],
            evaluation.itertuples(index=False),
            strict=True,
        )
    ]
    return evaluation, results


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def semantic_overall_summary(evaluation: pd.DataFrame) -> pd.DataFrame:
    matchable = evaluation["expected_match_type"].eq("match")
    no_match = evaluation["expected_match_type"].eq("no_match")
    exact = evaluation["challenger_match_status"].eq("exact_match")
    inverse = evaluation["expected_representation_type"].eq("inverse")
    values = []
    for metric, mask, correct_column in (
        ("top1", matchable, "challenger_top1_correct"),
        ("top3", matchable, "challenger_expected_in_top3"),
        ("embedding_only_top1", matchable, "embedding_only_top1_correct"),
        ("embedding_only_top3", matchable, "embedding_only_expected_in_top3"),
        ("exact", exact, "challenger_top1_correct"),
        ("no_match", no_match, "challenger_no_match_correct"),
        ("inverse", inverse, "challenger_representation_type_correct"),
    ):
        denominator = int(mask.sum())
        numerator = int(evaluation.loc[mask, correct_column].sum())
        values.append(
            {
                "metric": metric,
                "correct": numerator,
                "cases": denominator,
                "accuracy": _rate(numerator, denominator),
            }
        )
    return pd.DataFrame(values)


def semantic_performance_by_category(evaluation: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for category, group in evaluation.groupby("test_category", sort=True):
        matchable = group["expected_match_type"].eq("match")
        no_match = group["expected_match_type"].eq("no_match")
        top1 = int(group.loc[matchable, "challenger_top1_correct"].sum())
        top3 = int(group.loc[matchable, "challenger_expected_in_top3"].sum())
        no_match_correct = int(group.loc[no_match, "challenger_no_match_correct"].sum())
        rows.append(
            {
                "test_category": category,
                "cases": len(group),
                "matchable_cases": int(matchable.sum()),
                "top1_correct": top1,
                "top1_accuracy": _rate(top1, int(matchable.sum())),
                "top3_correct": top3,
                "top3_accuracy": _rate(top3, int(matchable.sum())),
                "expected_no_match_cases": int(no_match.sum()),
                "no_match_correct": no_match_correct,
                "no_match_accuracy": _rate(no_match_correct, int(no_match.sum())),
            }
        )
    return pd.DataFrame(rows)


def attach_baseline(
    challenger: pd.DataFrame, baseline: pd.DataFrame
) -> pd.DataFrame:
    """Attach selected baseline predictions by input identity after matching."""
    baseline_columns = [
        "feature_name",
        "description",
        "match_status",
        "top_candidate",
        "combined_score",
        "second_candidate",
        "second_score",
        "score_gap",
        "match_source_type",
        "top1_correct",
        "expected_in_top3",
        "no_match_correct",
        "representation_type_correct",
    ]
    missing = [column for column in baseline_columns if column not in baseline]
    if missing:
        raise ValueError(f"Baseline results are missing column(s): {', '.join(missing)}")
    selected = baseline.loc[:, baseline_columns].rename(
        columns={
            column: f"baseline_{column}"
            for column in baseline_columns
            if column not in {"feature_name", "description"}
        }
    )
    combined = challenger.merge(
        selected,
        on=["feature_name", "description"],
        how="left",
        validate="one_to_one",
    )
    if combined["baseline_top_candidate"].isna().any():
        raise ValueError("One or more challenger cases did not join to baseline results")
    combined["top1_disagreement"] = combined["baseline_top_candidate"].ne(
        combined["challenger_top_candidate"]
    )
    combined["disagreement_outcome"] = [
        "embedding_correct_baseline_wrong"
        if challenger_correct and not baseline_correct
        else "baseline_correct_embedding_wrong"
        if baseline_correct and not challenger_correct
        else "both_wrong_different"
        if disagreement and not baseline_correct and not challenger_correct
        else "same_outcome"
        for baseline_correct, challenger_correct, disagreement in zip(
            combined["baseline_top1_correct"],
            combined["challenger_top1_correct"],
            combined["top1_disagreement"],
            strict=True,
        )
    ]
    return combined


def baseline_challenger_summary(evaluation: pd.DataFrame) -> pd.DataFrame:
    matchable = evaluation["expected_match_type"].eq("match")
    no_match = evaluation["expected_match_type"].eq("no_match")
    inverse = evaluation["expected_representation_type"].eq("inverse")
    exact_baseline = evaluation["baseline_match_status"].eq("exact_match")
    exact_challenger = evaluation["challenger_match_status"].eq("exact_match")
    rows = []
    for matcher, prefix, exact in (
        ("baseline_rapidfuzz_tfidf", "baseline", exact_baseline),
        ("challenger_sentence_embedding", "challenger", exact_challenger),
    ):
        top1 = int(evaluation.loc[matchable, f"{prefix}_top1_correct"].sum())
        top3_column = (
            "baseline_expected_in_top3"
            if prefix == "baseline"
            else "challenger_expected_in_top3"
        )
        top3 = int(evaluation.loc[matchable, top3_column].sum())
        exact_correct = int(evaluation.loc[exact, f"{prefix}_top1_correct"].sum())
        no_match_correct = int(
            evaluation.loc[no_match, f"{prefix}_no_match_correct"].sum()
        )
        inverse_correct = int(
            evaluation.loc[
                inverse, f"{prefix}_representation_type_correct"
            ].sum()
        )
        rows.append(
            {
                "matcher": matcher,
                "top1_accuracy": _rate(top1, int(matchable.sum())),
                "top3_accuracy": _rate(top3, int(matchable.sum())),
                "exact_accuracy": _rate(exact_correct, int(exact.sum())),
                "no_match_accuracy": _rate(no_match_correct, int(no_match.sum())),
                "inverse_accuracy": _rate(inverse_correct, int(inverse.sum())),
            }
        )
    return pd.DataFrame(rows)


def embedding_score_distributions(evaluation: pd.DataFrame) -> pd.DataFrame:
    expected_match = evaluation["expected_match_type"].eq("match")
    groups = {
        "correct_embedding_top1": evaluation[
            expected_match & evaluation["embedding_only_top1_correct"]
        ],
        "incorrect_embedding_top1": evaluation[
            expected_match & ~evaluation["embedding_only_top1_correct"]
        ],
        "expected_no_match": evaluation[
            evaluation["expected_match_type"].eq("no_match")
        ],
    }
    rows = []
    for group_name, group in groups.items():
        for metric in ("embedding_only_top1_cosine", "embedding_only_score_gap"):
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            rows.append(
                {
                    "group": group_name,
                    "metric": metric,
                    "count": int(values.count()),
                    "minimum": values.min() if not values.empty else None,
                    "p25": values.quantile(0.25) if not values.empty else None,
                    "median": values.median() if not values.empty else None,
                    "mean": values.mean() if not values.empty else None,
                    "p75": values.quantile(0.75) if not values.empty else None,
                    "maximum": values.max() if not values.empty else None,
                }
            )
    return pd.DataFrame(rows)


def validate_semantic_challenger(
    validation_path: str,
    baseline_results_path: str,
    kb: dict[str, Any],
    terminology: dict[str, Any],
    semantic_index: SemanticFeatureIndex,
) -> dict[str, Any]:
    validation = load_validation_dataset(validation_path)
    challenger, matcher_results = run_semantic_validation(
        validation, kb, terminology, semantic_index
    )
    baseline = pd.read_csv(baseline_results_path, keep_default_na=False)
    evaluation = attach_baseline(challenger, baseline)
    disagreements = evaluation[evaluation["top1_disagreement"]].copy()
    no_match_cases = evaluation[evaluation["expected_match_type"].eq("no_match")].copy()
    focus_features = evaluation[
        evaluation["feature_name"].isin(
            ["DTI_CURR", "MONTHLY_DEBT_INC", "MAX_DPD_24M", "CNT_DLQ_12M", "MTH_SINCE_DLQ"]
        )
    ].copy()
    return {
        "evaluation": evaluation,
        "matcher_results": matcher_results,
        "challenger_summary": semantic_overall_summary(evaluation),
        "category_summary": semantic_performance_by_category(evaluation),
        "baseline_comparison": baseline_challenger_summary(evaluation),
        "disagreements": disagreements,
        "score_distributions": embedding_score_distributions(evaluation),
        "expected_no_match_cases": no_match_cases,
        "focus_features": focus_features,
    }


__all__ = [
    "attach_baseline",
    "baseline_challenger_summary",
    "embedding_score_distributions",
    "run_semantic_validation",
    "semantic_overall_summary",
    "semantic_performance_by_category",
    "validate_semantic_challenger",
]
