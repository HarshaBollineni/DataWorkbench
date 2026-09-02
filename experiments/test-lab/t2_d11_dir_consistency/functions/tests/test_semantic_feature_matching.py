from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from functions.feature_matching import load_kb, load_terminology
from functions.semantic_feature_matching import (
    build_semantic_index,
    match_feature_semantic,
    match_features_semantic,
)
from functions.semantic_feature_matching_validation import (
    attach_baseline,
    embedding_score_distributions,
    run_semantic_validation,
    semantic_overall_summary,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
KB_DIR = EXPERIMENT_ROOT / "kb"


class FakeSentenceTransformer:
    """Deterministic normalized test embeddings without model downloads."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        rows = []
        for text in texts:
            lowered = text.lower()
            vector = np.array(
                [
                    lowered.count("debt") + lowered.count("income"),
                    lowered.count("delinquency") + lowered.count("past due"),
                    lowered.count("property") + lowered.count("occupancy"),
                    lowered.count("growth") + lowered.count("change"),
                ],
                dtype=np.float32,
            )
            if not vector.any():
                vector[-1] = 0.1
            vector /= np.linalg.norm(vector)
            rows.append(vector)
        return np.vstack(rows)


@pytest.fixture(scope="module")
def resources():
    kb = load_kb(KB_DIR / "pd_directionality_kb_v0_2.yaml")
    terminology = load_terminology(KB_DIR / "credit_risk_abbreviations_v0_2.yaml")
    return kb, terminology


def test_semantic_index_encodes_kb_once_and_reuses_it(resources):
    kb, terminology = resources
    model = FakeSentenceTransformer()
    index = build_semantic_index(kb, terminology, model=model, model_name="fake")
    assert index.embeddings.shape == (39, 4)
    assert len(model.calls) == 1
    assert len(model.calls[0]) == 39

    results = match_features_semantic(
        pd.DataFrame(
            [
                {"feature_name": "CURRENT_LTV", "description": ""},
                {"feature_name": "UNKNOWN_DEBT_METRIC", "description": "Debt divided by income"},
            ]
        ),
        kb,
        terminology,
        index,
    )
    assert len(model.calls) == 2
    assert len(model.calls[1]) == 2
    assert results[0]["match_status"] == "exact_match"
    assert results[0]["top_1_canonical_feature"] == "loan_to_value"
    assert results[1]["match_status"] == "semantic_candidate"
    assert len(results[1]["top_3_candidates"]) == 3


def test_single_semantic_match_preserves_ambiguity(resources):
    kb, terminology = resources
    index = build_semantic_index(
        kb, terminology, model=FakeSentenceTransformer(), model_name="fake"
    )
    result = match_feature_semantic(
        "CLTV",
        "Combined first and second lien balances divided by current property value",
        kb,
        terminology,
        index,
    )
    assert result["match_status"] == "semantic_candidate"
    assert result["unresolved_ambiguous_terminology"][0]["abbreviation"] == "cltv"
    assert "possible meaning current loan to value" in result["processed_feature_text"]


def test_validation_joins_ground_truth_only_after_matching(resources):
    kb, terminology = resources
    index = build_semantic_index(
        kb, terminology, model=FakeSentenceTransformer(), model_name="fake"
    )
    validation = pd.DataFrame(
        [
            {
                "feature_name": "CURRENT_LTV",
                "description": "",
                "expected_canonical_feature": "loan_to_value",
                "expected_match_type": "match",
                "expected_representation_type": "same_orientation",
                "test_category": "easy",
            },
            {
                "feature_name": "RANDOM_X",
                "description": "",
                "expected_canonical_feature": "",
                "expected_match_type": "no_match",
                "expected_representation_type": "",
                "test_category": "out_of_kb",
            },
        ]
    )
    evaluation, results = run_semantic_validation(
        validation, kb, terminology, index
    )
    assert len(results) == 2
    assert evaluation.loc[0, "challenger_top1_correct"]
    assert not evaluation.loc[1, "challenger_no_match_correct"]
    summary = semantic_overall_summary(evaluation).set_index("metric")
    assert summary.loc["exact", "accuracy"] == 1.0
    assert summary.loc["no_match", "accuracy"] == 0.0
    assert len(embedding_score_distributions(evaluation)) == 6


def test_baseline_join_requires_one_to_one_identity():
    challenger = pd.DataFrame(
        [
            {
                "feature_name": "A",
                "description": "test",
                "challenger_top_candidate": "alpha",
                "challenger_top1_correct": True,
            }
        ]
    )
    baseline = pd.DataFrame(
        [
            {
                "feature_name": "A",
                "description": "test",
                "match_status": "candidate_match",
                "top_candidate": "beta",
                "combined_score": 0.5,
                "second_candidate": "alpha",
                "second_score": 0.4,
                "score_gap": 0.1,
                "match_source_type": "representation",
                "top1_correct": False,
                "expected_in_top3": True,
                "no_match_correct": False,
                "representation_type_correct": False,
            }
        ]
    )
    combined = attach_baseline(challenger, baseline)
    assert combined.loc[0, "top1_disagreement"]
    assert combined.loc[0, "disagreement_outcome"] == "embedding_correct_baseline_wrong"
