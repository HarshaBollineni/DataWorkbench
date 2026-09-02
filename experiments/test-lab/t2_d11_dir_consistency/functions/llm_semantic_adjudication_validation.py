"""Leakage-safe 75-case harness for the first LLM adjudication baseline."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from .feature_matching import match_feature_to_kb, prepare_feature_matcher
from .feature_matching_validation import VALIDATION_REQUIRED_COLUMNS
from .llm_semantic_adjudication import (
    AdjudicationCall,
    SemanticAdjudicator,
    StructuredAdjudicationResponseError,
    build_adjudication_input,
)
from .semantic_adjudication_contract import (
    AdjudicationDecision,
    AdjudicationOutput,
    AdjudicationResultValidationError,
    determine_review_required,
    validate_adjudication_result,
)


_CHECKPOINT_SCHEMA_VERSION = 1


class _AdjudicationCheckpoint:
    """Append-only cache of successful structured adjudication calls."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path is not None else None
        self._calls: dict[str, AdjudicationCall] = {}
        if self.path is not None and self.path.is_file():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                if record.get("schema_version") != _CHECKPOINT_SCHEMA_VERSION:
                    continue
                key = str(record["key"])
                output = AdjudicationOutput.model_validate(record["output"])
                self._calls[key] = AdjudicationCall(
                    output=output,
                    response_id=record.get("response_id"),
                )
            except (KeyError, TypeError, ValueError):
                # A process interruption can leave one incomplete trailing line.
                # Ignore malformed records and continue with the valid prefix.
                continue

    def get(self, key: str) -> AdjudicationCall | None:
        return self._calls.get(key)

    def put(self, key: str, call: AdjudicationCall) -> None:
        if self.path is None:
            return
        record = {
            "schema_version": _CHECKPOINT_SCHEMA_VERSION,
            "key": key,
            "response_id": call.response_id,
            "output": call.output.model_dump(mode="json"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._calls[key] = call


def _stable_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkpoint_key(
    adjudication_input: Any,
    adjudicator: SemanticAdjudicator,
    kb: Mapping[str, Any],
) -> str:
    prompt = getattr(adjudicator, "prompt", None)
    identity = {
        "schema_version": _CHECKPOINT_SCHEMA_VERSION,
        "adjudication_input": adjudication_input.model_dump(mode="json"),
        "model": adjudicator.model,
        "prompt_version": adjudicator.prompt_version,
        "prompt_fingerprint": (
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if isinstance(prompt, str)
            else None
        ),
        "contract_version": adjudicator.contract_version,
        "kb_fingerprint": _stable_fingerprint(kb),
    }
    return _stable_fingerprint(identity)


def _candidate_columns(matcher_result: Mapping[str, Any]) -> dict[str, Any]:
    candidates = list(matcher_result.get("top_candidates") or [])[:3]
    values: dict[str, Any] = {}
    for rank in range(1, 4):
        candidate = candidates[rank - 1] if len(candidates) >= rank else None
        values[f"python_candidate_{rank}"] = (
            candidate["canonical_feature"] if candidate else None
        )
        values[f"python_candidate_{rank}_score"] = (
            candidate["combined_similarity_score"] if candidate else None
        )
    return values


def _exact_prediction_row(
    *, feature_name: str, description: str, matcher_result: Mapping[str, Any]
) -> dict[str, Any]:
    deterministic = matcher_result["deterministic_match"]
    orientation = "INVERSE" if deterministic["is_inverse_representation"] else "SAME"
    source = deterministic["match_source"]
    return {
        "feature_name": feature_name,
        "description": description,
        "python_match_status": matcher_result["match_status"],
        "python_match_method": matcher_result["match_method"],
        "python_candidate_1": deterministic["canonical_feature"],
        "python_candidate_1_score": 1.0,
        "python_candidate_2": None,
        "python_candidate_2_score": None,
        "python_candidate_3": None,
        "python_candidate_3_score": None,
        "adjudication_used": False,
        "checkpoint_hit": False,
        "api_call_seconds": None,
        "model_identifier": None,
        "prompt_version": None,
        "adjudication_contract_version": None,
        "supplied_candidate_names": "[]",
        "response_id": None,
        "llm_decision": None,
        "llm_selected_candidate": None,
        "llm_representation_orientation": None,
        "llm_reason": None,
        "structured_adjudication_result": None,
        "api_call_succeeded": False,
        "contract_valid": True,
        "contract_validation_result": "not_applicable",
        "validation_error": None,
        "invalid_structured_output": False,
        "invented_candidate": False,
        "user_review_required": determine_review_required(source, False),
        "final_decision": "MATCH",
        "final_canonical_feature": deterministic["canonical_feature"],
        "final_representation_orientation": orientation,
    }


def _adjudicated_prediction_row(
    *,
    feature_name: str,
    description: str,
    matcher_result: Mapping[str, Any],
    kb: Mapping[str, Any],
    adjudicator: SemanticAdjudicator,
    adjudication_call: AdjudicationCall | None = None,
) -> tuple[dict[str, Any], AdjudicationCall | None]:
    adjudication_input = build_adjudication_input(
        feature_name, description, matcher_result, kb
    )
    candidate_names = [candidate.canonical_feature for candidate in adjudication_input.candidates]
    row: dict[str, Any] = {
        "feature_name": feature_name,
        "description": description,
        "python_match_status": matcher_result["match_status"],
        "python_match_method": matcher_result["match_method"],
        **_candidate_columns(matcher_result),
        "adjudication_used": True,
        "checkpoint_hit": adjudication_call is not None,
        "api_call_seconds": 0.0 if adjudication_call is not None else None,
        "model_identifier": adjudicator.model,
        "prompt_version": adjudicator.prompt_version,
        "adjudication_contract_version": adjudicator.contract_version,
        "supplied_candidate_names": json.dumps(candidate_names),
        "response_id": None,
        "llm_decision": None,
        "llm_selected_candidate": None,
        "llm_representation_orientation": None,
        "llm_reason": None,
        "structured_adjudication_result": None,
        "api_call_succeeded": False,
        "contract_valid": False,
        "contract_validation_result": "invalid",
        "validation_error": None,
        "invalid_structured_output": False,
        "invented_candidate": False,
        "user_review_required": determine_review_required("semantic_adjudication", True),
        "final_decision": None,
        "final_canonical_feature": None,
        "final_representation_orientation": None,
    }
    started = time.perf_counter() if adjudication_call is None else None
    try:
        call = adjudication_call or adjudicator.adjudicate(adjudication_input)
        if started is not None:
            row["api_call_seconds"] = round(time.perf_counter() - started, 6)
        row["api_call_succeeded"] = True
    except StructuredAdjudicationResponseError as exc:
        if started is not None:
            row["api_call_seconds"] = round(time.perf_counter() - started, 6)
        row["invalid_structured_output"] = True
        row["validation_error"] = str(exc)
        return row, None
    except Exception as exc:  # API/network errors are retained, not hidden or repaired.
        if started is not None:
            row["api_call_seconds"] = round(time.perf_counter() - started, 6)
        row["validation_error"] = f"{type(exc).__name__}: {exc}"
        return row, None

    output = call.output
    row.update(
        {
            "response_id": call.response_id,
            "llm_decision": output.decision.value,
            "llm_selected_candidate": output.selected_candidate,
            "llm_representation_orientation": (
                output.representation_orientation.value
                if output.representation_orientation is not None
                else None
            ),
            "llm_reason": output.reason,
            "structured_adjudication_result": json.dumps(output.model_dump(mode="json")),
        }
    )
    row["invented_candidate"] = bool(
        output.decision is AdjudicationDecision.MATCH
        and output.selected_candidate not in candidate_names
    )
    try:
        validate_adjudication_result(adjudication_input, output)
    except AdjudicationResultValidationError as exc:
        row["validation_error"] = str(exc)
        return row, call

    row["contract_valid"] = True
    row["contract_validation_result"] = "valid"
    row["final_decision"] = output.decision.value
    row["final_canonical_feature"] = output.selected_candidate
    row["final_representation_orientation"] = row["llm_representation_orientation"]
    return row, call


def run_adjudication_predictions(
    feature_metadata: pd.DataFrame,
    kb: Mapping[str, Any],
    terminology: Mapping[str, Any],
    adjudicator: SemanticAdjudicator,
    *,
    progress: Callable[[int, int, str, bool], None] | None = None,
    max_concurrency: int = 4,
    checkpoint_path: str | Path | None = None,
) -> pd.DataFrame:
    """Run matching and bounded concurrent LLM adjudication.

    Successful, contract-valid calls are appended to ``checkpoint_path`` and
    revalidated before reuse. API failures and invalid outputs are never cached.
    Returned rows always retain the same order as ``feature_metadata``.
    """

    missing = [column for column in ("feature_name", "description") if column not in feature_metadata]
    if missing:
        raise ValueError(f"Feature metadata is missing required column(s): {', '.join(missing)}")
    if (
        not isinstance(max_concurrency, int)
        or isinstance(max_concurrency, bool)
        or max_concurrency <= 0
    ):
        raise ValueError("max_concurrency must be a positive integer")
    matcher_inputs = feature_metadata.loc[:, ["feature_name", "description"]].copy()
    kb_version = str(kb.get("metadata", {}).get("version") or "unknown")
    prepared = prepare_feature_matcher(kb, terminology)
    checkpoint = _AdjudicationCheckpoint(checkpoint_path)
    total = len(matcher_inputs)
    rows: list[dict[str, Any] | None] = [None] * total
    live_cases: list[tuple[int, str, str, Mapping[str, Any], str | None]] = []
    completed = 0

    def record(index: int, row: dict[str, Any]) -> None:
        nonlocal completed
        row["knowledge_base_version"] = kb_version
        rows[index] = row
        completed += 1
        if progress is not None:
            progress(
                completed,
                total,
                str(row["feature_name"]),
                bool(row["adjudication_used"]),
            )

    for index, case in enumerate(matcher_inputs.itertuples(index=False)):
        feature_name = str(case.feature_name)
        description = "" if case.description is None else str(case.description)
        matcher_result = match_feature_to_kb(
            feature_name,
            description,
            kb,
            terminology,
            top_n=3,
            prepared_matcher=prepared,
        )
        if matcher_result["match_status"] == "exact_match":
            row = _exact_prediction_row(
                feature_name=feature_name,
                description=description,
                matcher_result=matcher_result,
            )
            record(index, row)
        else:
            key = None
            cached_call = None
            if checkpoint_path is not None:
                adjudication_input = build_adjudication_input(
                    feature_name, description, matcher_result, kb
                )
                key = _checkpoint_key(adjudication_input, adjudicator, kb)
                cached_call = checkpoint.get(key)
            if cached_call is not None:
                row, _ = _adjudicated_prediction_row(
                    feature_name=feature_name,
                    description=description,
                    matcher_result=matcher_result,
                    kb=kb,
                    adjudicator=adjudicator,
                    adjudication_call=cached_call,
                )
                if row["contract_valid"]:
                    record(index, row)
                    continue
            live_cases.append((index, feature_name, description, matcher_result, key))

    if live_cases:
        workers = min(max_concurrency, len(live_cases))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="semantic-adjudication",
        ) as executor:
            futures: dict[
                Future[tuple[dict[str, Any], AdjudicationCall | None]],
                tuple[int, str | None],
            ] = {}
            for index, feature_name, description, matcher_result, key in live_cases:
                future = executor.submit(
                    _adjudicated_prediction_row,
                    feature_name=feature_name,
                    description=description,
                    matcher_result=matcher_result,
                    kb=kb,
                    adjudicator=adjudicator,
                )
                futures[future] = (index, key)

            for future in as_completed(futures):
                index, key = futures[future]
                row, call = future.result()
                if key is not None and call is not None and row["contract_valid"]:
                    checkpoint.put(key, call)
                record(index, row)

    if any(row is None for row in rows):
        raise RuntimeError("Adjudication runner did not produce every requested row")
    return pd.DataFrame(rows)


def _expected_decision(row: pd.Series) -> str:
    """Derive the documented baseline rubric without changing source labels."""

    if row["expected_match_type"] == "match":
        return "MATCH"
    if not str(row["description"]).strip():
        return "INSUFFICIENT_CONTEXT"
    if row["feature_name"] == "RR_METRIC":
        return "NO_CANDIDATE_MATCH"
    return "NOT_DIRECTIONAL"


def attach_adjudication_ground_truth(
    predictions: pd.DataFrame, validation: pd.DataFrame
) -> pd.DataFrame:
    """Join labels only after inference, with row identity checks."""

    missing = [column for column in VALIDATION_REQUIRED_COLUMNS if column not in validation]
    if missing:
        raise ValueError(f"Validation data is missing required column(s): {', '.join(missing)}")
    if len(predictions) != len(validation):
        raise ValueError("Predictions and validation labels have different row counts")
    labels = validation.loc[:, VALIDATION_REQUIRED_COLUMNS].reset_index(drop=True)
    predictions = predictions.reset_index(drop=True)
    for column in ("feature_name", "description"):
        if not predictions[column].fillna("").equals(labels[column].fillna("")):
            raise ValueError(f"Prediction rows do not align with validation {column}")
    evaluation = pd.concat(
        [predictions, labels.drop(columns=["feature_name", "description"])], axis=1
    )
    evaluation["evaluation_expected_decision"] = evaluation.apply(_expected_decision, axis=1)
    expected_orientation = evaluation["expected_representation_type"].map(
        {"same_orientation": "SAME", "inverse": "INVERSE", "": None}
    )
    evaluation["canonical_correct"] = (
        evaluation["expected_match_type"].eq("match")
        & evaluation["contract_valid"]
        & evaluation["final_decision"].eq("MATCH")
        & evaluation["final_canonical_feature"].eq(evaluation["expected_canonical_feature"])
    )
    evaluation["rejection_correct"] = (
        evaluation["expected_match_type"].eq("no_match")
        & evaluation["contract_valid"]
        & evaluation["final_decision"].isin(
            ["NO_CANDIDATE_MATCH", "NOT_DIRECTIONAL", "INSUFFICIENT_CONTEXT"]
        )
    )
    evaluation["decision_correct"] = (
        evaluation["contract_valid"]
        & evaluation["final_decision"].eq(evaluation["evaluation_expected_decision"])
    )
    evaluation["orientation_correct"] = (
        evaluation["canonical_correct"]
        & expected_orientation.notna()
        & evaluation["final_representation_orientation"].eq(expected_orientation)
    )
    return evaluation


def _metric(metric: str, count: int, total: int) -> dict[str, Any]:
    return {
        "metric": metric,
        "count": count,
        "total": total,
        "rate": round(count / total, 6) if total else None,
    }


def adjudication_metrics(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Calculate the requested baseline routing, accuracy, and compliance metrics."""

    llm = evaluation["adjudication_used"]
    exact = ~llm
    expected_match = evaluation["expected_match_type"].eq("match")
    expected_no_match = evaluation["expected_match_type"].eq("no_match")
    llm_matches = llm & expected_match
    expected_not_directional = evaluation["evaluation_expected_decision"].eq("NOT_DIRECTIONAL")
    expected_insufficient = evaluation["evaluation_expected_decision"].eq(
        "INSUFFICIENT_CONTEXT"
    )
    orientation_cases = expected_match & evaluation["expected_representation_type"].ne("")
    rows = [
        _metric("deterministic_cases_bypassing_llm", int(exact.sum()), len(evaluation)),
        _metric("llm_calls", int(llm.sum()), len(evaluation)),
        _metric(
            "overall_canonical_feature_accuracy",
            int(evaluation.loc[expected_match, "canonical_correct"].sum()),
            int(expected_match.sum()),
        ),
        _metric(
            "llm_adjudicated_match_accuracy",
            int(evaluation.loc[llm_matches, "canonical_correct"].sum()),
            int(llm_matches.sum()),
        ),
        _metric(
            "expected_no_match_rejection_accuracy",
            int(evaluation.loc[expected_no_match, "rejection_correct"].sum()),
            int(expected_no_match.sum()),
        ),
        _metric(
            "decision_taxonomy_accuracy",
            int(evaluation["decision_correct"].sum()),
            len(evaluation),
        ),
        _metric(
            "not_directional_accuracy",
            int(evaluation.loc[expected_not_directional, "decision_correct"].sum()),
            int(expected_not_directional.sum()),
        ),
        _metric(
            "insufficient_context_accuracy",
            int(evaluation.loc[expected_insufficient, "decision_correct"].sum()),
            int(expected_insufficient.sum()),
        ),
        _metric(
            "orientation_accuracy",
            int(evaluation.loc[orientation_cases, "orientation_correct"].sum()),
            int(orientation_cases.sum()),
        ),
        _metric(
            "contract_compliance_rate",
            int(evaluation.loc[llm, "contract_valid"].sum()),
            int(llm.sum()),
        ),
        _metric(
            "invalid_structured_output_count",
            int(evaluation.loc[llm, "invalid_structured_output"].sum()),
            int(llm.sum()),
        ),
        _metric(
            "invented_candidate_count",
            int(evaluation.loc[llm, "invented_candidate"].sum()),
            int(llm.sum()),
        ),
        _metric(
            "api_failure_count",
            int((llm & ~evaluation["api_call_succeeded"]).sum()),
            int(llm.sum()),
        ),
        _metric(
            "selected_python_candidate_2_count",
            int(
                (
                    llm
                    & evaluation["contract_valid"]
                    & evaluation["llm_selected_candidate"].eq(
                        evaluation["python_candidate_2"]
                    )
                ).sum()
            ),
            int(llm.sum()),
        ),
        _metric(
            "selected_python_candidate_3_count",
            int(
                (
                    llm
                    & evaluation["contract_valid"]
                    & evaluation["llm_selected_candidate"].eq(
                        evaluation["python_candidate_3"]
                    )
                ).sum()
            ),
            int(llm.sum()),
        ),
    ]
    return pd.DataFrame(rows)


def adjudication_error_tables(evaluation: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return the requested focused review tables."""

    common = [
        "feature_name",
        "description",
        "python_candidate_1",
        "python_candidate_2",
        "python_candidate_3",
        "llm_decision",
        "llm_selected_candidate",
        "llm_representation_orientation",
        "llm_reason",
        "expected_canonical_feature",
        "expected_match_type",
        "expected_representation_type",
        "evaluation_expected_decision",
    ]
    expected_match = evaluation["expected_match_type"].eq("match")
    expected_no_match = evaluation["expected_match_type"].eq("no_match")
    return {
        "incorrect_canonical_selections": evaluation.loc[
            expected_match & ~evaluation["canonical_correct"], common
        ].reset_index(drop=True),
        "expected_matches_rejected": evaluation.loc[
            expected_match & evaluation["contract_valid"] & evaluation["final_decision"].ne("MATCH"),
            common,
        ].reset_index(drop=True),
        "expected_no_match_incorrectly_matched": evaluation.loc[
            expected_no_match & evaluation["final_decision"].eq("MATCH"), common
        ].reset_index(drop=True),
        "incorrect_not_directional": evaluation.loc[
            evaluation["evaluation_expected_decision"].eq("NOT_DIRECTIONAL")
            & ~evaluation["decision_correct"],
            common,
        ].reset_index(drop=True),
        "incorrect_insufficient_context": evaluation.loc[
            evaluation["evaluation_expected_decision"].eq("INSUFFICIENT_CONTEXT")
            & ~evaluation["decision_correct"],
            common,
        ].reset_index(drop=True),
        "orientation_errors": evaluation.loc[
            expected_match
            & evaluation["expected_representation_type"].ne("")
            & ~evaluation["orientation_correct"],
            common,
        ].reset_index(drop=True),
        "contract_validation_failures": evaluation.loc[
            evaluation["adjudication_used"] & ~evaluation["contract_valid"],
            [*common, "contract_validation_result", "validation_error"],
        ].reset_index(drop=True),
        "selected_python_candidate_2": evaluation.loc[
            evaluation["contract_valid"]
            & evaluation["llm_selected_candidate"].eq(evaluation["python_candidate_2"]),
            common,
        ].reset_index(drop=True),
        "selected_python_candidate_3": evaluation.loc[
            evaluation["contract_valid"]
            & evaluation["llm_selected_candidate"].eq(evaluation["python_candidate_3"]),
            common,
        ].reset_index(drop=True),
    }


KNOWN_DIFFICULT_CASES = (
    "DTI_CURR",
    "MONTHLY_DEBT_INC",
    "RR_METRIC",
    "PROPERTY_TYPE",
    "LOAN_PURPOSE",
    "INDUSTRY_CODE",
    "CURRENCY_CODE",
    "ORIGINATION_CHANNEL",
    "RANDOM_VAR_X",
    "RENT_UNCOLLECTED_PCT",
    "RR_SCORE",
)


def known_difficult_cases(evaluation: pd.DataFrame) -> pd.DataFrame:
    """Return detailed results for the explicitly requested baseline cases."""

    columns = [
        "feature_name",
        "description",
        "python_candidate_1",
        "python_candidate_2",
        "python_candidate_3",
        "llm_decision",
        "llm_selected_candidate",
        "llm_representation_orientation",
        "llm_reason",
        "expected_canonical_feature",
        "expected_match_type",
        "expected_representation_type",
        "canonical_correct",
        "orientation_correct",
    ]
    order = {name: index for index, name in enumerate(KNOWN_DIFFICULT_CASES)}
    selected = evaluation[evaluation["feature_name"].isin(KNOWN_DIFFICULT_CASES)].copy()
    selected["_order"] = selected["feature_name"].map(order)
    return selected.sort_values("_order").loc[:, columns].reset_index(drop=True)


def combined_error_export(error_tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Flatten named review tables into one CSV-friendly artifact."""

    frames = []
    for analysis_type, frame in error_tables.items():
        if not frame.empty:
            item = frame.copy()
            item.insert(0, "analysis_type", analysis_type)
            frames.append(item)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


COMPARISON_METRICS = (
    "overall_canonical_feature_accuracy",
    "llm_adjudicated_match_accuracy",
    "decision_taxonomy_accuracy",
    "expected_no_match_rejection_accuracy",
    "orientation_accuracy",
    "contract_compliance_rate",
    "api_failure_count",
    "invented_candidate_count",
    "selected_python_candidate_2_count",
    "selected_python_candidate_3_count",
)


def compare_adjudication_versions(
    baseline_v0_1: pd.DataFrame,
    challenger_v0_2: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare v0.1 and v0.2 metrics and classify every changed decision."""

    keys = ["feature_name", "description"]
    if baseline_v0_1.duplicated(keys).any() or challenger_v0_2.duplicated(keys).any():
        raise ValueError("Version comparison requires unique feature_name/description pairs")
    baseline_keys = baseline_v0_1.loc[:, keys].sort_values(keys).reset_index(drop=True)
    challenger_keys = challenger_v0_2.loc[:, keys].sort_values(keys).reset_index(drop=True)
    if not baseline_keys.equals(challenger_keys):
        raise ValueError("v0.1 and v0.2 result cases do not align")

    baseline_metrics = adjudication_metrics(baseline_v0_1).set_index("metric")
    challenger_metrics = adjudication_metrics(challenger_v0_2).set_index("metric")
    summary_rows = []
    for metric in COMPARISON_METRICS:
        old = baseline_metrics.loc[metric]
        new = challenger_metrics.loc[metric]
        summary_rows.append(
            {
                "metric": metric,
                "v0_1_count": int(old["count"]),
                "v0_1_total": int(old["total"]),
                "v0_1_rate": old["rate"],
                "v0_2_count": int(new["count"]),
                "v0_2_total": int(new["total"]),
                "v0_2_rate": new["rate"],
                "count_change": int(new["count"] - old["count"]),
            }
        )

    old_columns = [
        *keys,
        "final_decision",
        "final_canonical_feature",
        "final_representation_orientation",
        "llm_reason",
        "decision_correct",
        "canonical_correct",
        "orientation_correct",
    ]
    new_columns = old_columns
    merged = baseline_v0_1.loc[:, old_columns].merge(
        challenger_v0_2.loc[:, new_columns],
        on=keys,
        how="inner",
        suffixes=("_v0_1", "_v0_2"),
        validate="one_to_one",
    )
    changed = merged[
        merged["final_decision_v0_1"].fillna("")
        != merged["final_decision_v0_2"].fillna("")
    ].copy()

    def classify(row: pd.Series) -> str:
        expected = challenger_v0_2.loc[
            challenger_v0_2["feature_name"].eq(row["feature_name"])
            & challenger_v0_2["description"].eq(row["description"]),
            "evaluation_expected_decision",
        ].iloc[0]
        if (
            row["final_decision_v0_1"] == "NO_CANDIDATE_MATCH"
            and row["final_decision_v0_2"] == "NOT_DIRECTIONAL"
            and expected == "NOT_DIRECTIONAL"
        ):
            return "intended taxonomy correction"
        if bool(row["decision_correct_v0_1"]) and not bool(row["decision_correct_v0_2"]):
            return "regression"
        if bool(row["canonical_correct_v0_1"]) and not bool(row["canonical_correct_v0_2"]):
            return "regression"
        if bool(row["orientation_correct_v0_1"]) and not bool(row["orientation_correct_v0_2"]):
            return "regression"
        return "other change"

    changed["change_classification"] = changed.apply(classify, axis=1)
    return pd.DataFrame(summary_rows), changed.reset_index(drop=True)
