"""Framework Context — the single bridge between a database's declared
(model_families, use_cases) and the static DQ Framework taxonomy.

This is the abstraction that "activates" the use-case / model-family the user
picks during Data Sourcing (today they are stored but never read). All three
consumers call the same helper so there is one source of truth and no drift:

  * Newton   — tags each deterministic Key Anomaly to its framework area and the
               family priority (via ANOMALY_AREA_MAP + area_priority).
  * Merton   — grounds Suggested Test Hypotheses in the prioritized in-scope
               areas + their in-scope diagnostics for the DB's family.
  * Poincaré — injects the same prioritized slice so relationship/test discovery
               favours what the framework says matters most for this family.

Reads the seeded `dq_framework_areas` / `dq_framework_families` tables; returns
plain dicts (JSON-serialisable) ready to drop into a prompt or an SSE frame.
"""
from __future__ import annotations

import system_db as s
from seeds.dq_framework_seed import FAMILY_NAMES, FAMILY_ORDER

# Importance ranking for sorting + cross-family aggregation.
_RANK = {"Critical": 3, "High": 2, "Medium": 1, "": 0}

# Runnable test_library categories per L1 theme (see seeds/test_library_seed.py:
# C1 Sample&Representativeness, C2 Target&Outcome, C3 Feature&Predictor,
# C4 Dataset Stability&Monitoring). Lets agents prefer areas we can actually run.
_THEME_CATEGORY: dict[str, str] = {
    "Sample & Representativeness": "C1",
    "Target & Outcome Integrity": "C2",
    "Feature & Predictor Quality": "C3",
    "Dataset Stability & Monitoring": "C4",
}

# Deterministic-anomaly code (leading token emitted by discovery_state) -> area.
# Legacy aliases (all_null/high_null) keep older serialized DiscoveryState JSON
# mapping correctly after the Phase-3 retiering.
ANOMALY_AREA_MAP: dict[str, str] = {
    "null_critical": "missingness-data-availability",
    "null_high": "missingness-data-availability",
    "null_elevated": "missingness-data-availability",
    "all_null": "missingness-data-availability",
    "high_null": "missingness-data-availability",
    "constant_value": "feature-stability-behavioral-consistency",
    "negative_values": "outliers-extremes-plausibility",
    "extreme_outliers": "outliers-extremes-plausibility",
    "range_violation": "outliers-extremes-plausibility",
}

# Severity ranking (higher = more severe) for selecting the top-N Key Anomalies.
ANOMALY_SEVERITY: dict[str, int] = {
    "null_critical": 3, "all_null": 3,
    "constant_value": 2, "null_high": 2, "high_null": 2,
    "negative_values": 2, "range_violation": 2,
    "null_elevated": 1, "extreme_outliers": 1,
}
_SEVERITY_LABEL = {3: "Critical", 2: "Warning", 1: "Info"}


def anomaly_severity(anomaly: str) -> int:
    """Severity rank (3=Critical, 2=Warning, 1=Info) by the anomaly's lead token."""
    token = (anomaly or "").strip().split()[0] if anomaly else ""
    return ANOMALY_SEVERITY.get(token, 1)


def anomaly_severity_label(anomaly: str) -> str:
    return _SEVERITY_LABEL.get(anomaly_severity(anomaly), "Info")

# use_case keyword -> family_id (UI USE_CASES are finer/regulatory than the 9
# families; matched case-insensitively as substrings, first hit wins per family).
_USE_CASE_KEYWORDS: list[tuple[str, str]] = [
    ("ifrs", "ifrs9_cecl"), ("cecl", "ifrs9_cecl"),
    ("airb", "irb_basel"), ("firb", "irb_basel"), ("irb", "irb_basel"),
    ("basel", "irb_basel"), ("standardised", "irb_basel"), ("standardized", "irb_basel"),
    ("stress", "stress_testing"), ("icaap", "stress_testing"),
    ("ccar", "stress_testing"), ("dfast", "stress_testing"),
    ("decision", "credit_decisioning"), ("originat", "credit_decisioning"),
    ("underwrit", "credit_decisioning"), ("scorecard", "credit_decisioning"),
    ("treasury", "treasury_alm"), ("alm", "treasury_alm"),
    ("irrbb", "treasury_alm"), ("liquidit", "treasury_alm"),
    ("fraud", "fraud_aml"), ("aml", "fraud_aml"), ("financial crime", "fraud_aml"),
    ("collection", "collections_recovery"), ("recovery", "collections_recovery"),
    ("workout", "collections_recovery"),
    ("marketing", "marketing_propensity"), ("propensity", "marketing_propensity"),
    ("campaign", "marketing_propensity"), ("cross-sell", "marketing_propensity"),
    ("machine learning", "ai_ml"), ("ai/ml", "ai_ml"), (" ml", "ai_ml"), ("ai ", "ai_ml"),
]


def family_label(family_id: str) -> str:
    return FAMILY_NAMES.get(family_id, family_id)


def _match_family_string(text: str) -> str | None:
    """Map one model-family or use-case string to a family_id."""
    t = (text or "").strip().lower()
    if not t:
        return None
    # Exact display-name match first (UI MODEL_FAMILIES use these labels).
    for fid, label in FAMILY_NAMES.items():
        if t == label.lower():
            return fid
    for kw, fid in _USE_CASE_KEYWORDS:
        if kw in t:
            return fid
    return None


def resolve_families(model_families: list[str] | None,
                     use_cases: list[str] | None) -> list[str]:
    """Resolve the DB's declared model_families/use_cases to framework family_ids.

    model_families take precedence; use_cases are the fallback. Returns a
    de-duplicated list in canonical FAMILY_ORDER. Empty list => caller treats it
    as "no family declared" and aggregates across all families.
    """
    found: set[str] = set()
    for src in (model_families or []):
        fid = _match_family_string(src)
        if fid:
            found.add(fid)
    if not found:
        for src in (use_cases or []):
            fid = _match_family_string(src)
            if fid:
                found.add(fid)
    return [fid for fid in FAMILY_ORDER if fid in found]


def area_priority(area: dict, families: list[str]) -> str:
    """Strongest importance an area carries across the DB's families (Critical >
    High > Medium). With no families, aggregate across ALL families so the most
    universally-critical areas still surface first."""
    imp = area.get("importance_by_family") or {}
    keys = families or FAMILY_ORDER
    best = max((_RANK.get(imp.get(f, ""), 0) for f in keys), default=0)
    for label, rank in (("Critical", 3), ("High", 2), ("Medium", 1)):
        if rank == best:
            return label
    return ""


def area_for_anomaly(anomaly: str) -> str | None:
    """Map a deterministic anomaly string to its framework area_id by leading
    token (anomalies look like 'null_elevated (34%)' or 'constant_value')."""
    token = (anomaly or "").strip().split()[0] if anomaly else ""
    return ANOMALY_AREA_MAP.get(token)


def get_framework_context(model_families: list[str] | None = None,
                          use_cases: list[str] | None = None) -> dict:
    """Return the framework slice relevant to a database's declared family/usage.

    Shape (all JSON-serialisable):
        {
          "families": [family_id, ...], "family_labels": [str, ...],
          "resolved": bool,                      # False => aggregated across all
          "prioritized_areas": [ {area_id, l1_theme, l2_area, priority,
              objective, thresholds, analytical_impact, runnable_category,
              diagnostics_in_scope: [str], diagnostics_roadmap: [str]}, ... ],
          "out_of_scope_areas": [ {area_id, l2_area, analytical_impact}, ... ],
          "deep_dive": [ {family deep-dive row}, ... ],
        }
    Prioritized areas are IN-SCOPE only, sorted Critical->High->Medium then seq.
    """
    families = resolve_families(model_families, use_cases)
    areas = s.query("dq_framework_areas", order_by="seq ASC")
    fams = s.query("dq_framework_families", order_by="seq ASC")

    prioritized: list[dict] = []
    out_of_scope: list[dict] = []
    for a in areas:
        if not a.get("in_scope"):
            out_of_scope.append({
                "area_id": a["area_id"], "l2_area": a["l2_area"],
                "l1_theme": a["l1_theme"],
                "analytical_impact": a.get("analytical_impact", ""),
            })
            continue
        dx = a.get("diagnostics") or []
        prioritized.append({
            "area_id": a["area_id"], "l1_theme": a["l1_theme"],
            "l2_area": a["l2_area"], "priority": area_priority(a, families),
            "objective": a.get("objective", ""),
            "thresholds": a.get("thresholds", ""),
            "analytical_impact": a.get("analytical_impact", ""),
            "runnable_category": _THEME_CATEGORY.get(a["l1_theme"]),
            "diagnostics_in_scope": [d["name"] for d in dx if d.get("in_scope")],
            "diagnostics_roadmap": [d["name"] for d in dx if not d.get("in_scope")],
        })
    prioritized.sort(key=lambda x: (-_RANK.get(x["priority"], 0),
                                    _area_seq(areas, x["area_id"])))

    deep_dive = [f for f in fams if (not families or f["family_id"] in families)]

    return {
        "families": families,
        "family_labels": [family_label(f) for f in families],
        "resolved": bool(families),
        "prioritized_areas": prioritized,
        "out_of_scope_areas": out_of_scope,
        "deep_dive": deep_dive,
    }


def _area_seq(areas: list[dict], area_id: str) -> int:
    for a in areas:
        if a["area_id"] == area_id:
            return a.get("seq") or 99
    return 99


def in_scope_area_index(model_families: list[str] | None = None,
                        use_cases: list[str] | None = None,
                        *, ctx: dict | None = None) -> dict[str, dict]:
    """{area_id: {l2_area, priority}} for the in-scope areas — used to validate
    and enrich agent-proposed hypotheses against the DB's family priorities."""
    ctx = ctx or get_framework_context(model_families, use_cases)
    return {a["area_id"]: {"l2_area": a["l2_area"], "priority": a["priority"]}
            for a in ctx["prioritized_areas"]}


def framework_brief(model_families: list[str] | None = None,
                    use_cases: list[str] | None = None,
                    *, ctx: dict | None = None, max_areas: int = 9) -> str:
    """Compact prompt block of the framework slice for a DB's family — shared by
    Merton (Suggested Test Hypotheses) and Poincaré (test discovery) so both
    favour what the framework says matters most for this model family."""
    ctx = ctx or get_framework_context(model_families, use_cases)
    fams = ", ".join(ctx["family_labels"]) if ctx["resolved"] \
        else "general (no specific model family declared)"
    lines = [
        f"DQ FRAMEWORK PRIORITIES — model family: {fams}",
        "Propose tests ONLY for the in-scope areas below. The platform executes "
        "test categories C1 (representativeness), C2 (target integrity), "
        "C3 (feature quality) and C4 (dataset stability).",
    ]
    for a in ctx["prioritized_areas"][:max_areas]:
        dx = ", ".join(a["diagnostics_in_scope"]) or \
            "(listed diagnostics are roadmap — use the C1-C4 equivalent)"
        thr = f" | thresholds: {a['thresholds']}" if a.get("thresholds") else ""
        lines.append(f"- [{a['priority']}] {a['l2_area']} — runnable: {dx}{thr}")
    if ctx["out_of_scope_areas"]:
        oos = "; ".join(o["l2_area"] for o in ctx["out_of_scope_areas"])
        lines.append(f"OUT OF PLATFORM SCOPE (manual review only — do NOT "
                     f"auto-suggest tests for these): {oos}")
    return "\n".join(lines)
