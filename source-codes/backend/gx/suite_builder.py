"""Build the list of GX expectation instances for a use case.

Maps selected test IDs + the use-case contract (and optional AI-generated rules)
to concrete expectation instances, each carrying display metadata in ``meta``
that ``parse_gx_result`` surfaces to the frontend.
"""
from __future__ import annotations

import great_expectations.expectations as gxe
import pandas as pd

from .custom_expectations.conditional_relationship import ExpectConditionalColumnRelationship
from .custom_expectations.ks_test import ExpectColumnKSBySegmentToBeBelowThreshold
from .custom_expectations.leakage import ExpectPostOutcomeColumnsToBeClean
from .custom_expectations.missingness import ExpectColumnMissingnessToBeBelowTolerance
from .custom_expectations.psi import ExpectColumnPSIToBeBelowThreshold
from .custom_expectations.regime import ExpectDownturnRegimeCoverageToMeetMinimum
from .custom_expectations.target_stability import ExpectTargetRateToBeStructurallyStable
from .custom_expectations.vintage import ExpectObservationWindowDepthToMeetMinimum

# id -> display metadata (criticality is a sensible default; UI may refine per family).
CATALOG = {
    "missing":     {"name": "Missingness", "l1_theme": "Data Completeness", "l2_area": "Missingness Mechanism", "criticality": "High"},
    "uniqueness":  {"name": "PK Uniqueness", "l1_theme": "Dataset Stability & Monitoring", "l2_area": "Pipeline Stability", "criticality": "High"},
    "outlier":     {"name": "Outliers & Extreme Values", "l1_theme": "Data Completeness", "l2_area": "Outliers & Extreme Values", "criticality": "Medium"},
    "freshness":   {"name": "Freshness", "l1_theme": "Dataset Stability & Monitoring", "l2_area": "Pipeline Stability", "criticality": "Medium"},
    "psi":         {"name": "PSI (Population Stability Index)", "l1_theme": "Dataset Stability & Monitoring", "l2_area": "Dataset Drift & Degradation", "criticality": "Critical"},
    "ks":          {"name": "KS by Segment", "l1_theme": "Representativeness", "l2_area": "Portfolio Representativeness", "criticality": "High"},
    "leakage":     {"name": "Post-Outcome Leakage", "l1_theme": "Temporal Integrity", "l2_area": "Temporal Integrity & Feature Leakage", "criticality": "Critical"},
    "regime":      {"name": "Downturn Regime Coverage", "l1_theme": "Representativeness", "l2_area": "Downturn & Regime Coverage", "criticality": "High"},
    "vintage":     {"name": "Observation Window Depth", "l1_theme": "Representativeness", "l2_area": "Historical Depth & Observation Windows", "criticality": "Medium"},
    "target":      {"name": "Target Rate Stability", "l1_theme": "Temporal Integrity", "l2_area": "Target Definition & Label Consistency", "criticality": "High"},
    "conditional_rel": {"name": "Conditional Relationship", "l1_theme": "Bivariate / Multivariate Integrity", "l2_area": "Bivariate / Multivariate Integrity", "criticality": "High"},
}


def _meta(test_id: str, **extra) -> dict:
    m = {"id": test_id, **CATALOG.get(test_id, {"name": test_id, "l1_theme": "", "l2_area": "", "criticality": "Medium"})}
    m.update(extra)
    return m


def _pk(df: pd.DataFrame) -> str:
    for c in df.columns:
        if c.endswith("_id"):
            return c
    return df.columns[0]


def _segment_col(df: pd.DataFrame) -> str | None:
    for c in ("channel", "region", "sector", "country", "segment"):
        if c in df.columns:
            return c
    return None


# --- Plan 3: test-instance config builder (logical_db.table.field model) ------
# A test instance binds: {test_id, table, fields:[...], params:{...},
# criticality, category}. The calibrated metric functions are reused unchanged;
# only the *source* of parameters moves from contract rows to instance configs.

def _instance_meta(inst: dict, **extra) -> dict:
    base = CATALOG.get(inst.get("test_id", ""), {})
    m = {
        "id": inst.get("test_id"),
        "name": inst.get("name") or base.get("name", inst.get("test_id")),
        "l1_theme": base.get("l1_theme", ""),
        "l2_area": base.get("l2_area", ""),
        "category": inst.get("category"),
        "criticality": inst.get("criticality") or base.get("criticality", "Medium"),
        "table": inst.get("table"),
    }
    m.update(extra)
    return m


def build_from_instances(instances: list[dict], df: pd.DataFrame) -> list:
    """Build GX expectation instances from test-instance configs for ONE table.

    Each instance: {test_id, table, fields, params, criticality, category}.
    ``fields[0]`` is the primary target column; ``params`` carries thresholds,
    date_col, segment, window, post_outcome_cols, target_col, etc.
    """
    exps: list = []
    for inst in instances:
        tid = inst.get("test_id")
        fields = inst.get("fields") or []
        col = fields[0] if fields else inst.get("params", {}).get("column", "")
        p = inst.get("params", {}) or {}
        date_col = p.get("date_col")
        meta = _instance_meta(inst, column=col)

        if tid in ("missing", "missingness"):
            exps.append(ExpectColumnMissingnessToBeBelowTolerance(
                column=col, segment_col=p.get("segment_col", "") or "",
                segment_value=str(p.get("segment_value", "") or ""),
                date_col=p.get("date_col", "") or "",
                date_start=p.get("date_start", "") or "",
                date_end=p.get("date_end", "") or "",
                tol=float(p.get("tol", 0.01)),
                meta={**meta, "expected": f"null-rate <= {p.get('tol', 0.01)}"}))
        elif tid == "uniqueness":
            pk = col or _pk(df)
            exps.append(gxe.ExpectColumnValuesToBeUnique(
                column=pk, meta={**meta, "column": pk, "expected": "all unique"}))
        elif tid == "outlier":
            s = pd.to_numeric(df[col], errors="coerce")
            mu, sd = float(s.mean()), float(s.std(ddof=0))
            sig = float(p.get("sigma", 5))
            exps.append(gxe.ExpectColumnValuesToBeBetween(
                column=col, min_value=mu - sig * sd, max_value=mu + sig * sd,
                mostly=float(p.get("mostly", 0.999)),
                meta={**meta, "expected": f"within mean +/-{sig:g}sigma"}))
        elif tid == "freshness":
            mx = str(pd.to_datetime(df[date_col]).max().date())
            exps.append(gxe.ExpectColumnMaxToBeBetween(
                column=date_col, max_value=mx,
                meta={**meta, "column": date_col, "expected": "no future-dated records"}))
        elif tid == "psi":
            exps.append(ExpectColumnPSIToBeBelowThreshold(
                column=col, date_col=date_col, threshold=float(p.get("threshold", 0.20)),
                meta={**meta, "expected": f"PSI <= {p.get('threshold', 0.20)}"}))
        elif tid == "ks":
            seg = p.get("segment_col") or _segment_col(df)
            if seg:
                exps.append(ExpectColumnKSBySegmentToBeBelowThreshold(
                    column=col, segment_col=seg,
                    meta={**meta, "expected": f"KS <= {p.get('threshold', 0.20)}"}))
        elif tid == "leakage":
            post = p.get("post_outcome_cols") or fields
            exps.append(ExpectPostOutcomeColumnsToBeClean(
                post_outcome_cols=post,
                meta={**meta, "expected": "no post-outcome columns"}))
        elif tid == "regime":
            exps.append(ExpectDownturnRegimeCoverageToMeetMinimum(
                date_col=date_col,
                meta={**meta, "column": date_col, "expected": "downturn coverage >= 5% (or N/A)"}))
        elif tid == "vintage":
            exps.append(ExpectObservationWindowDepthToMeetMinimum(
                date_col=date_col,
                meta={**meta, "column": date_col, "expected": ">= 24 months"}))
        elif tid == "target":
            target = col or p.get("target_col")
            exps.append(ExpectTargetRateToBeStructurallyStable(
                target_col=target, date_col=date_col,
                meta={**meta, "column": target, "expected": "quarterly rate range <= 0.15"}))
        elif tid == "conditional_rel":
            exps.append(ExpectConditionalColumnRelationship(
                column_A=p.get("column_A", col), column_B=p.get("column_B", ""),
                relationship=p.get("relationship", "inverse"),
                min_strength=float(p.get("min_strength", 0.10)),
                meta={**meta, "expected": f"{p.get('column_A', col)} {p.get('relationship','')} {p.get('column_B','')}"}))
    return exps


def build_expectations(contract: dict, selected_tests: list[str], df: pd.DataFrame, ai_rules: list[dict] | None = None) -> list:
    """Return concrete expectation instances for the selected tests + AI rules."""
    feat = contract["psi_feature"]
    date_col = contract["date_col"]
    target = contract["target"]
    post = contract.get("post_outcome_cols") or []
    exps: list = []

    for tid in selected_tests:
        if tid == "missing":
            exps.append(gxe.ExpectColumnValuesToNotBeNull(column=feat, meta=_meta(tid, column=feat, expected="no nulls in model feature")))
        elif tid == "uniqueness":
            pk = _pk(df)
            exps.append(gxe.ExpectColumnValuesToBeUnique(column=pk, meta=_meta(tid, column=pk, expected="all unique")))
        elif tid == "outlier":
            s = pd.to_numeric(df[feat], errors="coerce")
            mu, sd = float(s.mean()), float(s.std(ddof=0))
            exps.append(gxe.ExpectColumnValuesToBeBetween(column=feat, min_value=mu - 5 * sd, max_value=mu + 5 * sd, mostly=0.999, meta=_meta(tid, column=feat, expected=f"within mean +/-5sigma")))
        elif tid == "freshness":
            mx = str(pd.to_datetime(df[date_col]).max().date())
            exps.append(gxe.ExpectColumnMaxToBeBetween(column=date_col, max_value=mx, meta=_meta(tid, column=date_col, expected="no future-dated records")))
        elif tid == "psi":
            exps.append(ExpectColumnPSIToBeBelowThreshold(column=feat, date_col=date_col, meta=_meta(tid, column=feat, expected="PSI <= 0.25")))
        elif tid == "ks":
            seg = _segment_col(df)
            if seg:
                exps.append(ExpectColumnKSBySegmentToBeBelowThreshold(column=feat, segment_col=seg, meta=_meta(tid, column=feat, expected="KS <= 0.20")))
        elif tid == "leakage":
            exps.append(ExpectPostOutcomeColumnsToBeClean(post_outcome_cols=post, meta=_meta(tid, expected="no post-outcome columns")))
        elif tid == "regime":
            exps.append(ExpectDownturnRegimeCoverageToMeetMinimum(date_col=date_col, meta=_meta(tid, column=date_col, expected="downturn coverage >= 5% (or N/A)")))
        elif tid == "vintage":
            exps.append(ExpectObservationWindowDepthToMeetMinimum(date_col=date_col, meta=_meta(tid, column=date_col, expected=">= 24 months")))
        elif tid == "target":
            exps.append(ExpectTargetRateToBeStructurallyStable(target_col=target, date_col=date_col, meta=_meta(tid, column=target, expected="quarterly rate range <= 0.15")))

    # AI-generated rules (Layer 1) — currently the conditional-relationship family.
    for rule in (ai_rules or []):
        if rule.get("expectation_type") == "ExpectConditionalColumnRelationship":
            kw = rule.get("kwargs", {})
            exps.append(ExpectConditionalColumnRelationship(
                column_A=kw.get("column_A", ""), column_B=kw.get("column_B", ""),
                relationship=kw.get("relationship", "inverse"),
                min_strength=float(kw.get("min_strength", 0.10)),
                meta=_meta("conditional_rel", column=kw.get("column_A", ""),
                           name=kw.get("description", "Conditional Relationship"),
                           expected=f"{kw.get('column_A','')} {kw.get('relationship','')} {kw.get('column_B','')}"),
            ))
    return exps
