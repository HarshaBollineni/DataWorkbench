"""Plan 3 / F4 / D0.2 — the five DQ categories + test->category mapping.

Every test maps to exactly ONE category. AI recommendations are classified into
one of these before scoring (the only AI input to scoring is the criticality
ranking; categorisation here is rule-based).
"""
from __future__ import annotations

# Five categories (ordered C1..C5).
CATEGORIES: list[dict] = [
    {"code": "C1", "name": "Sample & Representativeness"},
    {"code": "C2", "name": "Target & Outcome Integrity"},
    {"code": "C3", "name": "Feature & Predictor Quality"},
    {"code": "C4", "name": "Dataset Stability & Monitoring"},
    {"code": "C5", "name": "Upstream Dataset & Pipeline Integrity"},
]
CATEGORY_CODES = [c["code"] for c in CATEGORIES]
CATEGORY_NAME = {c["code"]: c["name"] for c in CATEGORIES}


def roll_up_results(results: list[dict]) -> list[dict]:
    """Convert issue-grain results into one explicit score unit per test plan.

    Per-column evidence remains untouched in ``results_v2`` and Issue
    Management.  A 40-column test therefore contributes one averaged unit,
    not forty accidental score weights.  A generated recommendation that
    could not run is retained as a zero-valued unit so it cannot disappear
    silently from the assessment.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in results:
        key = (row.get("table_name"), row.get("scope"), row.get("row_id") or row.get("test_name"), row.get("test_name"))
        groups.setdefault(key, []).append(row)
    units = []
    for _, rows in groups.items():
        usable = [r for r in rows if r.get("status") in {"pass", "fail"}]
        forced_not_run = any(r.get("status") == "not_runnable" and r.get("origin") == "AI_recommended" for r in rows)
        if not usable and not forced_not_run:
            continue
        base = rows[0]
        passed_fraction = (sum(r.get("status") == "pass" for r in usable) / len(usable)) if usable else 0.0
        units.append({**base, "passed_fraction": passed_fraction, "status": "pass" if passed_fraction == 1 else "fail",
                      "result_count": len(rows), "not_runnable": not bool(usable)})
    return units

# Authoritative mapping of library test ids -> category (Plan 5: aligned to the
# finalized DQ framework's L1 themes — Detailed_DQ_Framework col A).
_TEST_CATEGORY: dict[str, str] = {
    # C1 Sample & Representativeness: PSI / KS / vintage / maturity / regime
    "psi": "C1", "ks": "C1", "vintage": "C1", "maturity": "C1", "regime": "C1",
    # C2 Target & Outcome Integrity: target-rate trend
    "trend": "C2", "target": "C2",
    # C3 Feature & Predictor Quality: missingness / outlier / feature stability
    "missing": "C3", "missingness": "C3", "completeness": "C3", "outlier": "C3",
    "corr_stability": "C3", "feature_drift": "C3", "monotonicity": "C3",
    "conditional_rel": "C3",
    # C4 Dataset Stability & Monitoring: drift decomposition
    "drift_decomp": "C4",
}

# Keyword fallback against an L1 theme / free-text label.
_THEME_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("represent", "sampl", "psi", "ks", "vintage", "maturity", "regime", "downturn"), "C1"),
    (("target", "outcome", "label", "trend", "rate stab"), "C2"),
    (("complet", "missing", "mcar", "outlier", "iqr", "predictor", "feature stab",
      "correlation", "monoton", "feature drift"), "C3"),
    (("drift decomp", "stabilit", "monitor", "degrad"), "C4"),
]


def category_of(test_id_or_theme: str | None) -> str:
    """Resolve a test id or an L1 theme/label to a category code. Defaults C3."""
    if not test_id_or_theme:
        return "C3"
    key = str(test_id_or_theme).strip().lower()
    if key in _TEST_CATEGORY:
        return _TEST_CATEGORY[key]
    for kws, code in _THEME_KEYWORDS:
        if any(k in key for k in kws):
            return code
    return "C3"
