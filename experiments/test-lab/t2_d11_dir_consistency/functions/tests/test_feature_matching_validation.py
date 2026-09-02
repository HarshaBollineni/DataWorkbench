from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from functions import feature_matching_validation as validation_module
from functions.feature_matching_validation import (
    compact_evaluation_output,
    compare_validation_outputs,
    error_analysis_tables,
    load_validation_dataset,
    overall_validation_summary,
    performance_by_category,
    run_validation,
    score_distribution_summary,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
INPUTS_DIR = EXPERIMENT_ROOT / "inputs"


def _matcher_result(
    *,
    name: str,
    status: str,
    candidates: list[tuple[str, float, str]],
) -> dict:
    top1 = candidates[0][1] if candidates else None
    top2 = candidates[1][1] if len(candidates) > 1 else None
    return {
        "original_feature_name": name,
        "match_status": status,
        "match_method": "rapidfuzz_tfidf" if status != "no_match" else "no_lexical_evidence",
        "processed_feature_text": name.lower(),
        "deterministic_match": None,
        "top_candidates": [
            {
                "rank": rank,
                "canonical_feature": feature,
                "combined_similarity_score": score,
                "rapidfuzz_score": score,
                "tfidf_cosine_score": score,
                "best_matching_kb_text": feature,
                "best_matching_kb_text_source": source,
                "representation_evidence": (
                    "inverse" if source == "inverse_representation" else "normal"
                ),
            }
            for rank, (feature, score, source) in enumerate(candidates, start=1)
        ],
        "top_1_minus_top_2_score_gap": (
            round(top1 - top2, 6) if top1 is not None and top2 is not None else None
        ),
        "unresolved_ambiguous_terminology": [],
    }


def test_load_validation_dataset_uses_actual_schema_and_preserves_blank_no_matches():
    frame = load_validation_dataset(INPUTS_DIR / "feature_matching_validation_v0_1.csv")
    assert frame.shape == (75, 6)
    assert list(frame.columns) == list(validation_module.VALIDATION_REQUIRED_COLUMNS)
    no_matches = frame[frame["expected_match_type"].eq("no_match")]
    assert len(no_matches) == 11
    assert no_matches["expected_canonical_feature"].eq("").all()


def test_run_validation_enforces_two_field_matcher_input(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "feature_name": "UNKNOWN_X",
                "description": "Unrelated identifier",
                "expected_canonical_feature": "",
                "expected_match_type": "no_match",
                "expected_representation_type": "",
                "test_category": "out_of_kb",
            }
        ]
    )
    calls = []

    def spy_matcher(**kwargs):
        calls.append(kwargs)
        return _matcher_result(name=kwargs["feature_name"], status="no_match", candidates=[])

    monkeypatch.setattr(validation_module, "match_feature_to_kb", spy_matcher)
    prepared = object()
    monkeypatch.setattr(validation_module, "prepare_feature_matcher", lambda *_: prepared)
    evaluation, details = run_validation(frame, kb={"private": "kb"}, terminology={})

    assert set(calls[0]) == {
        "feature_name",
        "feature_description",
        "kb",
        "terminology",
        "top_n",
        "prepared_matcher",
    }
    assert calls[0]["feature_name"] == "UNKNOWN_X"
    assert calls[0]["feature_description"] == "Unrelated identifier"
    assert calls[0]["prepared_matcher"] is prepared
    assert evaluation.loc[0, "no_match_correct"]
    assert len(details) == 1


def test_metric_and_error_tables(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "feature_name": "A",
                "description": "",
                "expected_canonical_feature": "alpha",
                "expected_match_type": "match",
                "expected_representation_type": "same_orientation",
                "test_category": "easy",
            },
            {
                "feature_name": "B",
                "description": "",
                "expected_canonical_feature": "beta",
                "expected_match_type": "match",
                "expected_representation_type": "same_orientation",
                "test_category": "related_concepts",
            },
            {
                "feature_name": "C",
                "description": "",
                "expected_canonical_feature": "",
                "expected_match_type": "no_match",
                "expected_representation_type": "",
                "test_category": "out_of_kb",
            },
        ]
    )
    results = iter(
        [
            _matcher_result(
                name="A", status="candidate_match", candidates=[("alpha", 0.8, "canonical")]
            ),
            _matcher_result(
                name="B",
                status="candidate_match",
                candidates=[
                    ("gamma", 0.6, "representation"),
                    ("beta", 0.55, "representation"),
                ],
            ),
            _matcher_result(name="C", status="no_match", candidates=[("gamma", 0.1, "canonical")]),
        ]
    )
    monkeypatch.setattr(validation_module, "match_feature_to_kb", lambda **_: next(results))
    monkeypatch.setattr(validation_module, "prepare_feature_matcher", lambda *_: object())
    evaluation, _ = run_validation(frame, kb={}, terminology={})

    summary = overall_validation_summary(evaluation).set_index("metric")
    assert summary.loc["top1_correct", "count"] == 1
    assert summary.loc["top3_correct", "count"] == 2
    assert summary.loc["no_match_correct", "count"] == 1

    categories = performance_by_category(evaluation).set_index("test_category")
    assert categories.loc["out_of_kb", "no_match_accuracy"] == 1.0
    errors = error_analysis_tables(evaluation)
    assert list(errors["incorrect_top1"]["feature_name"]) == ["B"]
    assert list(errors["top1_wrong_top3_correct"]["feature_name"]) == ["B"]
    assert errors["expected_missing_top3"].empty
    distributions = score_distribution_summary(evaluation)
    assert set(distributions["group"]) == {
        "correct_top1",
        "incorrect_top1",
        "expected_no_match",
    }


def test_csv_comparison_reports_only_real_deviations():
    current = pd.DataFrame(
        [
            {
                "feature_name": "A",
                "description": "test",
                "test_category": "easy",
                "combined_score": 0.5,
                "top_3_features": ["alpha", "beta"],
                "matcher_result": {"large": "nested"},
                "top_3_candidates": [{"rank": 1}],
            }
        ]
    )
    compact = compact_evaluation_output(current)
    previous = pd.read_csv(
        StringIO(compact.to_csv(index=False)),
        keep_default_na=False,
    )
    assert compare_validation_outputs(previous, compact).empty

    changed = compact.copy()
    changed.loc[0, "combined_score"] = 0.4
    deviations = compare_validation_outputs(previous, changed)
    assert len(deviations) == 1
    assert deviations.loc[0, "column"] == "combined_score"


def test_validation_loader_reports_missing_columns():
    invalid = INPUTS_DIR / "test_fixtures" / "invalid_validation.csv"
    with pytest.raises(ValueError, match="missing required column"):
        load_validation_dataset(invalid)
