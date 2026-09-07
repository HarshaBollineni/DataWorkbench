"""Coverage, checkpointing, prediction, and evaluation for the 75-case benchmark."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .llm_role_adjudication import RoleAdjudicationCall, RoleAdjudicator
from .role_adjudication_contract import (
    RoleAdjudicationOutput, RoleDecision, build_adjudication_input,
    expand_adjudication_input, validate_role_adjudication,
)
from .kb_loader import expand_implied_roles
from .role_matching import prepare_role_matcher, match_variable_to_roles


def _role_set(primary: Any, secondary: Any = "") -> set[str]:
    values = [primary, *(str(secondary or "").split(";"))]
    return {str(value).strip() for value in values if str(value).strip()}


def benchmark_role_coverage(benchmark: pd.DataFrame, kb: Mapping[str, Any]) -> pd.DataFrame:
    """Count primary and secondary ground-truth appearances for every catalog role."""
    primary = benchmark["expected_primary_role"].fillna("").value_counts()
    secondary_values = benchmark["expected_secondary_roles"].fillna("").str.split(";").explode().str.strip()
    secondary = secondary_values[secondary_values.ne("")].value_counts()
    return pd.DataFrame([
        {
            "semantic_role": role["role"],
            "role_kind": role["role_kind"],
            "primary_case_count": int(primary.get(role["role"], 0)),
            "secondary_case_count": int(secondary.get(role["role"], 0)),
            "total_case_count": int(primary.get(role["role"], 0) + secondary.get(role["role"], 0)),
        }
        for role in kb["semantic_roles"]
    ])


def validate_benchmark_ground_truth(benchmark: pd.DataFrame, kb: Mapping[str, Any]) -> None:
    """Reject internally contradictory or out-of-catalog benchmark labels."""
    if benchmark["case_id"].duplicated().any():
        raise ValueError("Benchmark case_id values must be unique")
    catalog = {role["role"] for role in kb["semantic_roles"]}
    for row in benchmark.to_dict(orient="records"):
        decision = row["expected_decision"]
        roles = _role_set(row.get("expected_primary_role", ""), row.get("expected_secondary_roles", ""))
        secondary = _role_set("", row.get("expected_secondary_roles", ""))
        if decision == "MATCH" and (len(roles) != 1 or secondary):
            raise ValueError(f"{row['case_id']}: MATCH requires exactly one primary role")
        if decision == "MULTI_ROLE_MATCH" and (not row.get("expected_primary_role") or not secondary):
            raise ValueError(f"{row['case_id']}: MULTI_ROLE_MATCH requires primary and secondary roles")
        if decision not in {"MATCH", "MULTI_ROLE_MATCH"} and roles:
            raise ValueError(f"{row['case_id']}: non-match decision cannot contain roles")
        if not roles <= catalog:
            raise ValueError(f"{row['case_id']}: unknown expected roles {sorted(roles - catalog)}")


class AdjudicationCheckpoint:
    """Append-only cache; an interrupted trailing record is safely ignored."""
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self.calls: dict[str, RoleAdjudicationCall] = {}
        if self.path and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    self.calls[record["key"]] = RoleAdjudicationCall(
                        output=RoleAdjudicationOutput.model_validate(record["output"]),
                        response_id=record.get("response_id"),
                        response_model=record.get("response_model"),
                        input_tokens=record.get("input_tokens"),
                        output_tokens=record.get("output_tokens"),
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue

    def get(self, key: str) -> RoleAdjudicationCall | None:
        return self.calls.get(key)

    def put(self, key: str, call: RoleAdjudicationCall) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"schema_version": 2, "key": key, "response_id": call.response_id,
                  "response_model": call.response_model, "input_tokens": call.input_tokens,
                  "output_tokens": call.output_tokens,
                  "output": call.output.model_dump(mode="json")}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.calls[key] = call


def adjudication_checkpoint_key(input_: Any, adjudicator: RoleAdjudicator, kb: Mapping[str, Any]) -> str:
    prompt = str(getattr(adjudicator, "prompt", ""))
    payload = {"input": input_.model_dump(mode="json"), "model": adjudicator.model,
               "prompt_version": adjudicator.prompt_version, "contract_version": adjudicator.contract_version,
               "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
               "kb_version": kb["metadata"]["version"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def run_benchmark_predictions(benchmark: pd.DataFrame, kb: Mapping[str, Any], terminology: Mapping[str, Any],
                              adjudicator: RoleAdjudicator, *, checkpoint_path: str | Path | None = None,
                              progress: Callable[[int, int, str, bool], None] | None = None) -> pd.DataFrame:
    validate_benchmark_ground_truth(benchmark, kb)
    prepared = prepare_role_matcher(kb, terminology)
    checkpoint = AdjudicationCheckpoint(checkpoint_path)
    rows: list[dict[str, Any]] = []
    cases = benchmark.to_dict("records")
    for index, case in enumerate(cases, 1):
        match = match_variable_to_roles(case, prepared)
        base = {"case_id": case["case_id"], "column_name": case["column_name"],
                "match_status": match["match_status"], "adjudication_used": False,
                "detailed_candidate_count": len(match["detailed_candidates"]),
                "detailed_candidate_roles": ";".join(item["role"] for item in match["detailed_candidates"]),
                "checkpoint_hits": 0, "expansion_passes": 0, "api_call_succeeded": True,
                "contract_valid": True, "error": ""}
        if match["match_status"] == "exact_match":
            direct = [match["exact_match"]["role"]]
            expanded = expand_implied_roles(direct, kb)
            rows.append({**base, "final_decision": "MULTI_ROLE_MATCH" if len(expanded) > 1 else "MATCH",
                         "primary_role": direct[0], "secondary_roles": ";".join(expanded[1:]),
                         "reason": "deterministic exact-match bypass with role implications"})
            if progress:
                progress(index, len(cases), case["column_name"], False)
            continue
        input_ = build_adjudication_input(match, dict(kb))
        base["adjudication_used"] = True
        try:
            while True:
                key = adjudication_checkpoint_key(input_, adjudicator, kb)
                call = checkpoint.get(key)
                if call:
                    base["checkpoint_hits"] += 1
                else:
                    call = adjudicator.adjudicate(input_)
                output = validate_role_adjudication(input_, call.output)
                if not checkpoint.get(key):
                    checkpoint.put(key, call)
                if output.decision is not RoleDecision.CANDIDATE_SET_INCOMPLETE:
                    break
                base["expansion_passes"] += 1
                if base["expansion_passes"] > 3:
                    raise RuntimeError("Role adjudication exceeded three candidate-expansion passes")
                input_ = expand_adjudication_input(input_, output.requested_expansion_roles, dict(kb))
            direct_roles = ([output.primary_role] if output.primary_role else []) + list(output.secondary_roles)
            expanded_roles = expand_implied_roles(direct_roles, kb)
            decision = output.decision.value
            if decision == "MATCH" and len(expanded_roles) > 1:
                decision = "MULTI_ROLE_MATCH"
            rows.append({**base, "final_decision": decision,
                         "primary_role": expanded_roles[0] if expanded_roles else "",
                         "secondary_roles": ";".join(expanded_roles[1:]), "reason": output.reason,
                         "response_id": call.response_id or "", "response_model": call.response_model or "",
                         "input_tokens": call.input_tokens, "output_tokens": call.output_tokens})
        except Exception as exc:
            rows.append({**base, "api_call_succeeded": False, "contract_valid": False,
                         "final_decision": "", "primary_role": "", "secondary_roles": "",
                         "reason": "", "error": f"{type(exc).__name__}: {exc}"})
        if progress:
            progress(index, len(cases), case["column_name"], True)
    return pd.DataFrame(rows)


def attach_ground_truth(predictions: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    truth_columns = ["case_id", "expected_decision", "expected_primary_role", "expected_secondary_roles",
                     "case_category", "suggested_context"]
    result = predictions.merge(benchmark[truth_columns], on="case_id", validate="one_to_one")
    result["decision_correct"] = result["final_decision"].eq(result["expected_decision"])
    result["role_set_correct"] = result.apply(
        lambda row: _role_set(row["primary_role"], row["secondary_roles"])
        == _role_set(row["expected_primary_role"], row["expected_secondary_roles"]), axis=1)
    result["case_correct"] = result["decision_correct"] & result["role_set_correct"]
    return result


def adjudication_metrics(evaluation: pd.DataFrame) -> pd.DataFrame:
    total = len(evaluation)
    llm = evaluation.loc[evaluation["adjudication_used"]]
    definitions = {
        "overall_case_accuracy": (int(evaluation["case_correct"].sum()), total),
        "decision_accuracy": (int(evaluation["decision_correct"].sum()), total),
        "role_set_accuracy": (int(evaluation["role_set_correct"].sum()), total),
        "llm_case_accuracy": (int(llm["case_correct"].sum()), len(llm)),
        "contract_compliance_rate": (int(llm["contract_valid"].sum()), len(llm)),
        "candidate_expansion_case_rate": (int(llm["expansion_passes"].gt(0).sum()), len(llm)),
    }
    rows = [{"metric": name, "count": count, "total": denominator,
                          "rate": count / denominator if denominator else None}
                         for name, (count, denominator) in definitions.items()]
    for column, prefix in (("expected_decision", "decision"), ("case_category", "category")):
        for value, group in evaluation.groupby(column):
            rows.append({"metric": f"{prefix}_accuracy:{value}", "count": int(group["case_correct"].sum()),
                         "total": len(group), "rate": float(group["case_correct"].mean())})
    return pd.DataFrame(rows)


def retrieval_metrics(benchmark: pd.DataFrame, kb: Mapping[str, Any], terminology: Mapping[str, Any]) -> pd.DataFrame:
    prepared = prepare_role_matcher(kb, terminology)
    rows = []
    for case in benchmark.to_dict(orient="records"):
        expected = str(case.get("expected_primary_role") or "")
        if not expected:
            continue
        match = match_variable_to_roles(case, prepared)
        ranked = [match["exact_match"]["role"]] if match["exact_match"] else [item["role"] for item in match["detailed_candidates"]]
        rows.append({"case_id": case["case_id"], "candidate_count": len(ranked),
                     "recall_at_5": expected in ranked[:5], "recall_at_12": expected in ranked[:12],
                     "retrieved": expected in ranked})
    detail = pd.DataFrame(rows)
    return pd.DataFrame([
        {"metric": "retrieval_recall_at_5", "value": detail["recall_at_5"].mean()},
        {"metric": "retrieval_recall_at_12", "value": detail["recall_at_12"].mean()},
        {"metric": "retrieval_recall_detailed_subset", "value": detail["retrieved"].mean()},
        {"metric": "mean_detailed_candidate_count", "value": detail["candidate_count"].mean()},
        {"metric": "maximum_detailed_candidate_count", "value": detail["candidate_count"].max()},
    ])
