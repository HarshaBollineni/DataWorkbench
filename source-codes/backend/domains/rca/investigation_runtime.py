"""Library-first execution and guarded generated-code runtime for RCA."""
from __future__ import annotations
from domains.rca import progress

import ast
import hashlib
import inspect
import json
import math
import os
import subprocess
import sys
from time import perf_counter
from pathlib import Path
from typing import Any

import pandas as pd
from ai import sandbox_capabilities

from domains.rca import analysis_helpers, feature_states


MAX_CODE_CHARS = 12_000


def _sandbox_timeout_seconds() -> float:
    raw = os.getenv("RCA_SANDBOX_TIMEOUT_SECONDS", "60")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("RCA_SANDBOX_TIMEOUT_SECONDS must be numeric") from exc
    if not math.isfinite(value) or not 5 <= value <= 300:
        raise ValueError("RCA_SANDBOX_TIMEOUT_SECONDS must be between 5 and 300 seconds")
    return value


SANDBOX_TIMEOUT_SECONDS = _sandbox_timeout_seconds()
MAX_RESULT_CHARS = 100_000
MAX_DOWNLOAD_RESULT_CHARS = 5_000_000
MAX_EVIDENCE_ROWS = 12
MAX_METRICS = 24


def helper_catalog(test_family: str | None, table: str | None,
                   columns: list[str]) -> list[dict[str, Any]]:
    family = str(test_family or "").lower()
    selected = sorted({str(value).split(".")[-1] for value in columns if value})
    return [{
        **helper.public(),
        "applicability": {
            "test_family_match": any(token in family for token in helper.supported_test_types),
            "selected_columns": selected, "table": table,
        },
    } for helper in analysis_helpers.HELPERS.values()]


@progress.phase("Finding an approved helper")
def search_helpers(plan: dict[str, Any], catalog: list[dict[str, Any]],
                   *, has_retained_psi: bool, available_columns: set[str],
                   has_population_context: bool = False) -> dict[str, Any]:
    preferred = [str(value) for value in plan.get("preferred_helper_ids") or []]
    params = plan.get("helper_params") or {}
    plan_text = " ".join(str(plan.get(key) or "") for key in (
        "analysis_kind", "question", "rationale", "supports_hypothesis_when",
        "rejects_hypothesis_when",
    )).lower()
    requires_frozen_population = has_population_context and any(token in plan_text for token in (
        "frozen population", "frozen-population", "split_snapshot",
        "baseline/current allocation", "baseline and current membership",
    ))
    matches = []
    for helper in catalog:
        helper_id = helper["helper_id"]
        required = set((helper.get("param_schema") or {}).get("required") or [])
        applicable = True
        reasons = []
        if "diagnostic" in required and not has_retained_psi:
            applicable, reasons = False, ["retained PSI bins are unavailable"]
        if "population_context" in required and not has_population_context:
            applicable = False
            reasons.append("frozen diagnostic population context is unavailable")
        if requires_frozen_population and "population_context" not in required:
            applicable = False
            reasons.append("helper does not preserve the frozen diagnostic population allocation")
        requested_columns = {
            str(value).split(".")[-1] for key, value in params.items()
            if key in {"column", "metric_col", "segment_col", "date_col", "key_col", "target_col"}
            and isinstance(value, str)
        }
        missing_columns = requested_columns - available_columns
        if missing_columns:
            applicable = False
            reasons.append(f"unknown columns: {sorted(missing_columns)}")
        if helper_id in preferred:
            matches.append({"helper_id": helper_id, "applicable": applicable, "reasons": reasons})
    selected = next((row["helper_id"] for row in matches if row["applicable"]), None)
    return {
        "searched_helper_count": len(catalog), "preferred_helper_ids": preferred,
        "matches": matches, "selected_helper_id": selected,
        "decision": "reuse_existing_helper" if selected else "generate_fresh_code",
    }


@progress.phase("Analysing with approved helper")
def run_helper(helper_id: str, frame: pd.DataFrame, params: dict[str, Any],
               diagnostic: dict[str, Any] | None,
               population_context: dict[str, Any] | None = None,
               declared_special_values: list[Any] | None = None,
               psi_bin_definition: dict[str, Any] | None = None,
               feature_state_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = dict(params or {})
    if helper_id == "psi_evidence_decomposition":
        resolved["diagnostic"] = diagnostic or {}
    if helper_id in {"population_segment_missingness", "greedy_driver_search"}:
        resolved["population_context"] = population_context or {}
        resolved["declared_special_values"] = declared_special_values or []
    if helper_id == "greedy_driver_search":
        resolved["diagnostic"] = diagnostic or {}
        resolved["psi_bin_definition"] = psi_bin_definition or {}
    resolved["feature_state_snapshot"] = feature_state_snapshot or {}
    governed_frame = frame
    if helper_id not in {"population_segment_missingness", "greedy_driver_search"}:
        governed_frame, state_context = feature_states.analysis_frame(
            frame, feature_state_snapshot
        )
        resolved["feature_state_context"] = state_context
    result = analysis_helpers.run_helper(helper_id, governed_frame, resolved)["result"]
    if "feature_state_reconciliation" not in result:
        result["feature_state_reconciliation"] = feature_states.reconciliation(
            frame, feature_state_snapshot
        )
    return result


def helper_execution_artifact(helper_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Freeze the governed implementation identity shown in the UI and AAR."""
    helper = analysis_helpers.HELPERS.get(helper_id)
    if not helper:
        raise KeyError(f"Unknown RCA helper: {helper_id}")
    source = inspect.getsource(helper.fn)
    if helper_id == "greedy_driver_search":
        # Bind the audit hash to the governed implementation as well as the
        # small catalogue wrapper that invokes it.
        from domains.rca import driver_search
        source = f"{source}\n\n{inspect.getsource(driver_search)}"
    return {
        "mode": "approved_helper", "language": "python",
        "helper_id": helper.helper_id, "helper_name": helper.name,
        "callable": f"domains.rca.analysis_helpers.{helper.fn.__name__}",
        "implementation_source": source,
        "implementation_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "parameters": dict(params or {}),
        "evidence_binding": (
            "The runtime injects the immutable retained PSI diagnostic as 'diagnostic'."
            if helper_id == "psi_evidence_decomposition" else
            "The runtime injects the frozen diagnostic population definition and governed special values."
            if helper_id in {"population_segment_missingness", "greedy_driver_search"} else
            "The runtime applies these parameters to a copied dataset snapshot."
        ),
    }


def generated_execution_artifact(code: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "generated_code_sandbox", "language": "python",
        "sandbox_contract": sandbox_capabilities.public_contract(),
        "implementation_source": code,
        "implementation_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "parameters": dict(params or {}), "sandbox_timeout_seconds": SANDBOX_TIMEOUT_SECONDS,
        "evidence_binding": (
            "The sandbox receives a copied snapshot, retained aggregate evidence, "
            "and frozen diagnostic context."
        ),
    }


def validate_generated_code(code: str) -> list[str]:
    errors = sandbox_capabilities.validate_capabilities(code, sandbox_capabilities.RCA_INPUT_NAMES)
    if len(code) > MAX_CODE_CHARS:
        errors.append(f"Code exceeds {MAX_CODE_CHARS} characters.")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"]
    blocked_data_access = {
        "read_csv", "read_excel", "read_feather", "read_fwf", "read_gbq",
        "read_hdf", "read_html", "read_json", "read_orc", "read_parquet",
        "read_pickle", "read_sas", "read_spss", "read_sql", "read_stata",
        "read_table", "to_clipboard", "to_csv", "to_excel", "to_feather",
        "to_gbq", "to_hdf", "to_json", "to_orc", "to_parquet", "to_pickle",
        "to_sql", "to_stata",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.AsyncFor)):
            errors.append("Unbounded loop constructs are not permitted.")
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
            func = node.iter.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            # Ordinary iteration over a DataFrame, params list, groupby, range,
            # dict items, or another retained collection is finite and remains
            # bounded by the isolated-process timeout.  Reject the standard
            # explicitly infinite iterator constructors instead of guessing
            # boundedness from the iterator's AST shape.
            if name in {"count", "cycle", "repeat"}:
                errors.append(f"Potentially unbounded iterator '{name}' is not permitted.")
        if isinstance(node, ast.Attribute) and node.attr in blocked_data_access:
            errors.append(f"External data access '{node.attr}' is not permitted.")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "://" in node.value:
            errors.append("Network locations are not permitted in generated code.")
    return list(dict.fromkeys(errors))


def _flatten_metrics(value: Any, *, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if not isinstance(value, dict) or depth > 3:
        return metrics
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            metrics[name] = item
        elif isinstance(item, dict):
            metrics.update(_flatten_metrics(item, prefix=name, depth=depth + 1))
        if len(metrics) >= MAX_METRICS:
            break
    return dict(list(metrics.items())[:MAX_METRICS])


def _evidence_lists(value: Any, *, section: str = "") -> list[tuple[str, list[dict[str, Any]]]]:
    found: list[tuple[str, list[dict[str, Any]]]] = []
    if not isinstance(value, dict):
        return found
    for key, item in value.items():
        name = f"{section}.{key}" if section else str(key)
        if isinstance(item, list) and item and all(isinstance(row, dict) for row in item):
            found.append((name, item))
        elif isinstance(item, dict):
            found.extend(_evidence_lists(item, section=name))
    return found


def normalize_generated_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert arbitrary generated output into the bounded RCA evidence contract."""
    if not isinstance(result, dict):
        raise TypeError("Generated analysis must return a dictionary.")
    metrics_source = (result.get("metrics") if isinstance(result.get("metrics"), dict)
                      else {key: value for key, value in result.items()
                            if key not in {"feature_state_context",
                                           "feature_state_reconciliation"}})
    metrics = _flatten_metrics(metrics_source)
    rows: list[dict[str, Any]] = []
    sources = _evidence_lists(
        {"evidence_rows": result.get("evidence_rows")}
        if isinstance(result.get("evidence_rows"), list) else result
    )
    # Prefer explicitly ranked/contributing evidence, then compact bin rows,
    # before large unranked detail collections such as joint_cells.
    sources.sort(key=lambda item: (
        0 if any(token in item[0] for token in ("contributing", "evidence_rows")) else
        1 if "bin" in item[0] else 2,
        item[0],
    ))
    total_available = sum(len(values) for _, values in sources)
    for section, values in sources:
        for source in values:
            row = {"section": section}
            for key, value in source.items():
                if value is None or isinstance(value, (str, int, float, bool)):
                    row[str(key)] = value
                if len(row) >= 12:
                    break
            rows.append(row)
            if len(rows) >= MAX_EVIDENCE_ROWS:
                break
        if len(rows) >= MAX_EVIDENCE_ROWS:
            break

    summary = str(result.get("summary") or "").strip()
    if not summary:
        population = result.get("population_reconciliation") or {}
        baseline = population.get("baseline_count")
        current = population.get("current_count")
        if baseline is not None and current is not None:
            summary = (
                f"Generated analysis reconciled {int(baseline):,} baseline rows and "
                f"{int(current):,} current rows. Key metrics and the most relevant "
                "bounded evidence rows are shown below."
            )
        else:
            summary = "Generated analysis completed; key metrics and bounded evidence rows are shown below."
    normalized = {
        "summary": summary,
        "metrics": metrics,
        "evidence_rows": rows,
        "interpretation_hints": list(result.get("interpretation_hints") or [])[:8],
        "recommended_followups": list(result.get("recommended_followups") or [])[:8],
        "truncation": {
            "evidence_rows_retained": len(rows),
            "evidence_rows_available": total_available,
            "evidence_rows_truncated": total_available > len(rows),
            "metrics_retained": len(metrics),
        },
    }
    if isinstance(result.get("feature_state_reconciliation"), dict):
        normalized["feature_state_reconciliation"] = result["feature_state_reconciliation"]
    if isinstance(result.get("feature_state_context"), dict):
        normalized["feature_state_context"] = result["feature_state_context"]
    return normalized


@progress.phase("Executing in sandbox")
def run_generated_code(code: str, frame: pd.DataFrame, params: dict[str, Any],
                       timeout_seconds: float = SANDBOX_TIMEOUT_SECONDS,
                       feature_state_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    outcome = _run_generated_code(code, frame, params, timeout_seconds, feature_state_snapshot)
    return {**outcome, **sandbox_capabilities.execution_metadata()}


def _run_generated_code(code: str, frame: pd.DataFrame, params: dict[str, Any],
                        timeout_seconds: float,
                        feature_state_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    errors = validate_generated_code(code)
    if errors:
        return {"status": "rejected", "ok": False, "errors": errors}
    governed_frame, state_context = feature_states.analysis_frame(
        frame, feature_state_snapshot
    )
    governed_params = {**(params or {}), "feature_state_context": state_context}
    payload = json.dumps({
        "code": code, "frame": governed_frame.to_json(orient="table", date_format="iso"),
        "params": governed_params,
    }, default=str)
    backend_root = Path(__file__).resolve().parents[2]
    process_started = perf_counter()
    try:
        process = subprocess.run(
            [sys.executable, "-m", "domains.rca.sandbox_worker"],
            input=payload, capture_output=True, text=True, timeout=timeout_seconds,
            cwd=str(backend_root), check=False,
        )
        process_seconds = perf_counter() - process_started
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "ok": False,
                "error": (
                    f"The generated analysis reached the sandbox's {timeout_seconds:g}-second "
                    "safety limit. No result was accepted; narrow the analysis or revise the "
                    "generated method before retrying."
                )}
    except OSError as exc:
        return {"status": "failed", "ok": False,
                "error": f"Sandbox process could not start: {type(exc).__name__}: {exc}"}
    try:
        outcome = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"status": "failed", "ok": False,
                "error": f"Sandbox exited with code {process.returncode} without a valid result."}
    if not outcome.get("ok"):
        return {"status": "failed", **outcome}
    raw_result = outcome.get("result_obj")
    if not isinstance(raw_result, dict):
        return {"status": "rejected", "ok": False,
                "error": "Generated analysis must assign a dict to result."}
    raw_result["feature_state_reconciliation"] = state_context["columns"]
    raw_result["feature_state_context"] = state_context
    raw_result_chars = len(json.dumps(raw_result, default=str))
    if raw_result_chars > MAX_DOWNLOAD_RESULT_CHARS:
        return {"status": "rejected", "ok": False,
                "error": "Sandbox result exceeds the governed downloadable-output limit."}
    result = normalize_generated_result(raw_result)
    if len(str(result)) > MAX_RESULT_CHARS:
        return {"status": "rejected", "ok": False, "error": "Sandbox result is too large."}
    analysis_seconds = outcome.get("analysis_seconds")
    timings = {"process_seconds": round(process_seconds, 3)}
    if isinstance(analysis_seconds, (int, float)) and 0 <= analysis_seconds <= process_seconds:
        timings.update(analysis_seconds=analysis_seconds,
                       process_overhead_seconds=round(process_seconds - analysis_seconds, 3))
    return {"status": "completed", "ok": True, "result": result, "timings": timings,
            "full_result": raw_result,
            "stdout": outcome.get("stdout") or ""}


__all__ = ["generated_execution_artifact", "helper_catalog", "helper_execution_artifact",
           "normalize_generated_result", "run_generated_code", "run_helper", "search_helpers",
           "validate_generated_code"]
