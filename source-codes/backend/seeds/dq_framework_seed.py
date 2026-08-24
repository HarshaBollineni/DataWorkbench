"""DQ Framework — static reference taxonomy seed.

Curated from the original framework workbook and baked into code so the boot
path never depends on a spreadsheet file. The
content is the source-of-truth domain taxonomy; the *presentation* is rethought
in the UI (Explorer / Family Lens / Importance Matrix), so this module keeps the
substance faithful but stores it as clean, structured fields.

Two reference tables, seeded idempotently on EVERY boot (existing system_state.db
files already have users, so cold-start `seed_all` would otherwise skip them) and
preserved across the surgical demo reset (they are not work-product):

  * dq_framework_areas    — 11 L2 assessment areas under 6 L1 themes, each with
                            an importance-by-family map and red-font scope flags.
  * dq_framework_families — 9 model-family deep-dive profiles.

Scope flags (from the workbook's red font, verified per-cell):
  * area `in_scope=False`  → rows 11 (Pipeline) & 12 (Third-party) — whole themes
    outside the platform's analytical-DQ remit.
  * diagnostic `in_scope=False` → the listed diagnostics for Temporal Integrity,
    Dataset Drift, Pipeline and Third-party are roadmap (manual review), even
    where the theme itself is in scope. Agents gate on the AREA flag and cross-
    reference the runnable C1-C4 test_library catalog for what can actually run.
"""
from __future__ import annotations

# --- Model families (matches ui MODEL_FAMILIES + matrix column order) --
FAMILY_ORDER: list[str] = [
    "irb_basel", "ifrs9_cecl", "stress_testing", "credit_decisioning",
    "treasury_alm", "fraud_aml", "ai_ml", "collections_recovery",
    "marketing_propensity",
]
FAMILY_NAMES: dict[str, str] = {
    "irb_basel": "IRB / Basel",
    "ifrs9_cecl": "IFRS9 / CECL",
    "stress_testing": "Stress Testing",
    "credit_decisioning": "Credit Decisioning",
    "treasury_alm": "Treasury / ALM",
    "fraud_aml": "Fraud / AML",
    "ai_ml": "AI / ML",
    "collections_recovery": "Collections & Recovery",
    "marketing_propensity": "Marketing / Propensity",
}


def _imp(*vals: str) -> dict[str, str]:
    """Build an importance-by-family map from 9 values in FAMILY_ORDER."""
    assert len(vals) == len(FAMILY_ORDER), "importance row must have 9 values"
    return dict(zip(FAMILY_ORDER, vals))


def _dx(in_scope: bool, *names: str) -> list[dict]:
    """Diagnostics list; all diagnostics in one workbook cell share its scope."""
    return [{"name": n, "in_scope": in_scope} for n in names]


# --- Framework overview (Framework_Overview sheet) ---------------------------
FRAMEWORK_OVERVIEW: dict[str, str] = {
    "objective": (
        "Assess whether datasets are analytically consumable and fit-for-purpose "
        "for model development, forecasting, stress testing and advanced analytics."
    ),
    "scope": (
        "Contextual, statistically grounded data-quality assessment for "
        "analytics-ready and model-ready datasets."
    ),
    "in_scope": (
        "Representativeness, historical adequacy, temporal integrity, leakage "
        "prevention, target consistency, feature stability, dataset drift and "
        "analytical suitability."
    ),
    "out_of_scope": (
        "Model validation methodologies, enterprise BCBS239 transformation, AI "
        "governance and ML-Ops implementation."
    ),
    "primary_users": (
        "Model development, analytics, risk transformation, data, model-risk and "
        "portfolio analytics teams."
    ),
    "key_differentiator": (
        "Focuses on analytical fitness-for-purpose rather than only operational "
        "correctness and generic enterprise DQ."
    ),
    "target_use_cases": (
        "IRB, IFRS9, stress testing, decisioning, treasury analytics, "
        "wholesale/obligor models, fraud/AML, AI/ML analytics, collections and "
        "marketing analytics."
    ),
}


# --- L2 assessment areas (Detailed_DQ_Framework sheet) -----------------------
AREAS: list[dict] = [
    {
        "area_id": "representativeness-selection-bias",
        "l1_theme": "Sample & Representativeness",
        "l2_area": "Portfolio Representativeness & Selection Bias",
        "objective": "Ensure datasets represent the intended analytical populations across segments, vintages, channels and geographies.",
        "why_it_matters": "Non-representative data biases calibration, weakens generalization and destabilizes analytical outcomes.",
        "key_risks": ["Reject bias", "Survivorship bias", "Channel distortion", "Geographic concentration bias", "Segment underrepresentation"],
        "diagnostics": _dx(True, "PSI", "KS test", "Vintage analysis", "Wasserstein distance", "Portfolio migration analysis", "Acceptance funnel analysis"),
        "evidence": ["Distribution comparison reports", "Vintage analysis", "Segment representativeness analysis"],
        "thresholds": "PSI < 0.10 preferred; PSI > 0.25 investigated.",
        "analytical_impact": "Biased calibration, inflated predictive power, weak out-of-time performance.",
        "remediation": ["Reweighting", "Reject inference", "Sample augmentation", "Segment balancing"],
        "importance_by_family": _imp("Critical", "Critical", "Critical", "Critical", "Medium", "High", "Critical", "High", "Critical"),
        "in_scope": 1, "seq": 1,
    },
    {
        "area_id": "historical-depth-observation-windows",
        "l1_theme": "Sample & Representativeness",
        "l2_area": "Historical Depth, Observation Windows & Censoring",
        "objective": "Ensure sufficient history, seasoning and observable outcomes for the intended analytical horizon.",
        "why_it_matters": "Thin history or incomplete observation windows weaken long-horizon analytics and forecasting.",
        "key_risks": ["Immature vintages", "Censored histories", "Insufficient downturn periods", "Short observation windows"],
        "diagnostics": _dx(True, "Vintage seasoning analysis", "Kaplan-Meier survival analysis", "Maturity profile analysis"),
        "evidence": ["Historical coverage maps", "Survival curves", "Observation-window analysis"],
        "thresholds": "Minimum seasoning windows defined per use case.",
        "analytical_impact": "Weak long-horizon forecasting, unstable lifetime estimates.",
        "remediation": ["Extend history", "Supplement external data", "Survival-analysis techniques"],
        "importance_by_family": _imp("Critical", "Critical", "Critical", "Medium", "High", "Medium", "High", "Critical", "Medium"),
        "in_scope": 1, "seq": 2,
    },
    {
        "area_id": "downturn-regime-coverage",
        "l1_theme": "Sample & Representativeness",
        "l2_area": "Downturn & Regime Coverage",
        "objective": "Ensure stressed and structurally different economic regimes are represented.",
        "why_it_matters": "Datasets concentrated in benign periods fail under stress conditions.",
        "key_risks": ["Missing downturn periods", "Structural regime gaps", "Limited stressed observations"],
        "diagnostics": _dx(True, "Regime segmentation", "Stress-period coverage analysis", "Change-point analysis"),
        "evidence": ["Stress-event inventories", "Macro overlays", "Regime analysis"],
        "thresholds": "Material downturn periods represented and documented.",
        "analytical_impact": "Weak stress sensitivity and understated downturn impacts.",
        "remediation": ["Stress augmentation", "Regime reweighting", "External historical supplementation"],
        "importance_by_family": _imp("Critical", "High", "Critical", "Medium", "High", "Medium", "High", "High", "Medium"),
        "in_scope": 1, "seq": 3,
    },
    {
        "area_id": "target-definition-label-consistency",
        "l1_theme": "Target & Outcome Integrity",
        "l2_area": "Target Definition & Label Consistency",
        "objective": "Ensure analytical targets and outcomes are consistently defined across systems and time.",
        "why_it_matters": "Inconsistent targets create artificial shifts and unstable analytical behavior.",
        "key_risks": ["Policy changes", "Override distortions", "Inconsistent tagging", "Event-timing inconsistencies"],
        "diagnostics": _dx(True, "Trend analysis", "Structural break analysis", "Reconciliation checks"),
        "evidence": ["Target lineage documentation", "Reconciliation reports", "Policy-change logs"],
        "thresholds": "No unexplained structural target shifts.",
        "analytical_impact": "Calibration instability and reduced predictive reliability.",
        "remediation": ["Relabeling", "Harmonization", "Override governance", "ETL fixes"],
        "importance_by_family": _imp("Critical", "Critical", "High", "Critical", "Medium", "Critical", "Critical", "Critical", "High"),
        "in_scope": 1, "seq": 4,
    },
    {
        "area_id": "missingness-data-availability",
        "l1_theme": "Feature & Predictor Quality",
        "l2_area": "Missingness Mechanism & Data Availability",
        "objective": "Assess the analytical implications of missing data beyond simple completeness.",
        "why_it_matters": "Non-random missingness materially impacts segmentation and calibration.",
        "key_risks": ["MNAR patterns", "Operational capture gaps", "Temporal missingness drift"],
        "diagnostics": _dx(True, "MCAR test", "Missingness heatmaps", "Missingness prediction models"),
        "evidence": ["Missingness dashboards", "Feature coverage reports"],
        "thresholds": "Critical features monitored for MNAR behavior.",
        "analytical_impact": "Biased segmentation and unstable calibration.",
        "remediation": ["Imputation", "Alternative feature sourcing", "Capture remediation"],
        "importance_by_family": _imp("High", "High", "High", "Critical", "Medium", "Critical", "Critical", "High", "High"),
        "in_scope": 1, "seq": 5,
    },
    {
        "area_id": "outliers-extremes-plausibility",
        "l1_theme": "Feature & Predictor Quality",
        "l2_area": "Outliers, Extreme Values & Plausibility",
        "objective": "Assess whether extreme values represent genuine behavior or data defects.",
        "why_it_matters": "Incorrect handling of extremes distorts analytical relationships.",
        "key_risks": ["Data-entry anomalies", "Structural shifts", "Incorrect exclusion of genuine tails"],
        "diagnostics": _dx(True, "Robust z-score analysis", "Tail analysis", "Cluster analysis", "IQR"),
        "evidence": ["Outlier inventories", "Tail-behavior analysis"],
        "thresholds": "Material tail populations assessed and justified.",
        "analytical_impact": "Distorted analytical relationships and weak stress robustness.",
        "remediation": ["Controlled capping", "Segmentation", "Source correction"],
        "importance_by_family": _imp("High", "High", "Critical", "High", "High", "Critical", "High", "High", "Medium"),
        "in_scope": 1, "seq": 6,
    },
    {
        "area_id": "temporal-integrity-feature-leakage",
        "l1_theme": "Feature & Predictor Quality",
        "l2_area": "Temporal Integrity & Feature Leakage",
        "objective": "Ensure predictors are temporally aligned and free from future-information leakage.",
        "why_it_matters": "Leakage artificially inflates analytical and model performance.",
        "key_risks": ["Future-information leakage", "Timestamp misalignment", "Delayed feature availability"],
        "diagnostics": _dx(False, "Snapshot reconstruction", "Timestamp validation", "Event-ordering analysis"),
        "evidence": ["As-of dataset documentation", "Timestamp lineage maps"],
        "thresholds": "Zero tolerance for material leakage.",
        "analytical_impact": "Artificially inflated predictive performance and weak production stability.",
        "remediation": ["Feature redesign", "Timestamp harmonization", "Feature exclusion"],
        "importance_by_family": _imp("Critical", "High", "High", "Critical", "Medium", "Critical", "Critical", "High", "High"),
        "in_scope": 1, "seq": 7,
    },
    {
        "area_id": "feature-stability-behavioral-consistency",
        "l1_theme": "Feature & Predictor Quality",
        "l2_area": "Feature Stability & Behavioral Consistency",
        "objective": "Assess whether predictor behavior remains stable across time and regimes.",
        "why_it_matters": "Unstable features reduce analytical reliability and model robustness.",
        "key_risks": ["Correlation instability", "Regime-sensitive behavior", "Unstable feature interactions"],
        "diagnostics": _dx(True, "Correlation stability analysis", "Feature drift analysis", "Monotonicity checks"),
        "evidence": ["Stability reports", "Regime comparison analysis"],
        "thresholds": "Material instability triggers review.",
        "analytical_impact": "Reduced robustness and weak out-of-time behavior.",
        "remediation": ["Feature redesign", "Segmentation", "Stability-based feature selection"],
        "importance_by_family": _imp("High", "High", "High", "Critical", "Medium", "High", "Critical", "High", "Critical"),
        "in_scope": 1, "seq": 8,
    },
    {
        "area_id": "dataset-drift-degradation",
        "l1_theme": "Dataset Stability & Monitoring",
        "l2_area": "Dataset Drift & Degradation Monitoring",
        "objective": "Monitor whether datasets remain representative and fit-for-purpose over time.",
        "why_it_matters": "Analytical datasets degrade due to portfolio, operational and macro changes.",
        "key_risks": ["Population drift", "Feature drift", "Operational process changes", "Missingness drift"],
        "diagnostics": _dx(False, "PSI monitoring", "Feature distribution tracking", "Drift decomposition analysis"),
        "evidence": ["Drift dashboards", "Trend reports", "Stability summaries"],
        "thresholds": "Drift thresholds calibrated by use case.",
        "analytical_impact": "Reduced analytical comparability and weak generalization.",
        "remediation": ["Dataset refresh", "Segmentation recalibration", "Pipeline remediation"],
        "importance_by_family": _imp("High", "High", "High", "Critical", "Medium", "Critical", "Critical", "High", "Critical"),
        "in_scope": 1, "seq": 9,
    },
    {
        "area_id": "pipeline-stability-schema-consistency",
        "l1_theme": "Upstream Dataset & Pipeline Integrity",
        "l2_area": "Pipeline Stability & Schema Consistency",
        "objective": "Assess whether ingestion and transformation processes remain stable.",
        "why_it_matters": "Operational changes silently degrade analytical datasets.",
        "key_risks": ["Schema drift", "Feed interruptions", "Transformation-logic changes", "Late-arriving data"],
        "diagnostics": _dx(False, "Schema-drift checks", "Reconciliation", "Data-latency analysis"),
        "evidence": ["Pipeline lineage maps", "Transformation logs", "Feed monitoring reports"],
        "thresholds": "Critical feeds monitored with alert thresholds.",
        "analytical_impact": "Inconsistent feature creation and reduced comparability.",
        "remediation": ["Pipeline controls", "Schema governance", "Automated alerts"],
        "importance_by_family": _imp("High", "High", "High", "High", "Critical", "Critical", "Critical", "Medium", "Medium"),
        "in_scope": 0, "seq": 10,
    },
    {
        "area_id": "external-vendor-data-robustness",
        "l1_theme": "Third-Party Data Quality & Controls",
        "l2_area": "External / Vendor Data Robustness & Change Control",
        "objective": "Assess whether externally sourced data is sufficiently representative, stable, transparent and fit-for-purpose.",
        "why_it_matters": "Vendor methodology changes, coverage gaps, inconsistent refresh and opaque derivations materially impact analytical outcomes.",
        "key_risks": ["Coverage gaps", "Methodology changes", "Historical instability", "Refresh / timeliness issues"],
        "diagnostics": _dx(False, "Coverage analysis", "Stability assessment", "Methodology review", "Cross-source validation"),
        "evidence": ["Vendor methodology document", "Coverage reports", "Refresh history", "Change logs / version releases"],
        "thresholds": "Vendor definition changes, material coverage reduction, behavior inconsistent with prior periods, or refresh delays.",
        "analytical_impact": "Hidden bias, model instability, reduced explainability.",
        "remediation": ["Alternative data sources", "Enhanced monitoring", "Feature redesign/replacement", "Usage restrictions until resolved"],
        "importance_by_family": _imp("High", "Critical", "Critical", "Critical", "High", "Critical", "Critical", "Medium", "High"),
        "in_scope": 0, "seq": 11,
    },
]


# --- Model-family deep-dive profiles (Model_Family_Mapping bottom table) ------
FAMILIES: list[dict] = [
    {
        "family_id": "irb_basel", "name": "IRB / Basel Models",
        "most_critical_areas": "Representativeness, downturn coverage, temporal integrity",
        "retail_considerations": "Behavioral scorecard populations, reject bias, bureau coverage",
        "wholesale_considerations": "Low-default portfolios, obligor hierarchy alignment, sector concentration",
        "typical_risks": "Survivorship bias, incomplete downturn observations",
        "priority_diagnostics": "PSI, downturn coverage analysis, leakage testing",
        "analytical_impacts": "Underestimated capital and unstable calibration",
        "in_scope": 1, "seq": 1,
    },
    {
        "family_id": "ifrs9_cecl", "name": "IFRS9 / CECL Models",
        "most_critical_areas": "Historical depth, stage consistency, observation windows, external/vendor data for macro sensitivity & scenario testing",
        "retail_considerations": "Vintage seasoning, cure behavior, macro overlays",
        "wholesale_considerations": "Financial-statement periodicity, sparse defaults, expert overlays",
        "typical_risks": "Immature vintages, overlay distortions",
        "priority_diagnostics": "Vintage seasoning, survival analysis",
        "analytical_impacts": "Biased lifetime loss estimates",
        "in_scope": 1, "seq": 2,
    },
    {
        "family_id": "stress_testing", "name": "Stress Testing Models",
        "most_critical_areas": "Regime coverage, stressed observations, valid scenarios and representative features",
        "retail_considerations": "Consumer stress sensitivity and unemployment-linked behavior",
        "wholesale_considerations": "Sector-specific stress transmission and concentration impacts",
        "typical_risks": "Benign-period concentration, structural breaks",
        "priority_diagnostics": "Stress-period analysis, change-point analysis",
        "analytical_impacts": "Weak stress sensitivity",
        "in_scope": 1, "seq": 3,
    },
    {
        "family_id": "credit_decisioning", "name": "Decisioning Models",
        "most_critical_areas": "Selection bias, feature availability, leakage, vendor bureau data",
        "retail_considerations": "Approved-only populations, channel bias, real-time features",
        "wholesale_considerations": "Manual override effects, relationship-managed onboarding",
        "typical_risks": "Operational capture gaps, feature-timing inconsistencies",
        "priority_diagnostics": "Reject-inference diagnostics, timestamp validation",
        "analytical_impacts": "Inflated predictive power",
        "in_scope": 1, "seq": 4,
    },
    {
        "family_id": "treasury_alm", "name": "Treasury / ALM Analytics",
        "most_critical_areas": "Historical continuity, aggregation consistency",
        "retail_considerations": "Deposit behavior stability, retail runoff assumptions",
        "wholesale_considerations": "Large-exposure concentration and funding-structure shifts",
        "typical_risks": "Reporting inconsistencies, regime shifts",
        "priority_diagnostics": "Time-series continuity checks",
        "analytical_impacts": "Weak forecasting reliability",
        "in_scope": 1, "seq": 5,
    },
    {
        "family_id": "fraud_aml", "name": "Fraud / AML Models",
        "most_critical_areas": "Feature drift, temporal integrity, latency, third-party scores and features",
        "retail_considerations": "Rapid transaction-behavior shifts, channel migration",
        "wholesale_considerations": "Cross-border transaction complexity and entity-linkage issues",
        "typical_risks": "Delayed tagging, rapidly evolving fraud patterns",
        "priority_diagnostics": "Drift analysis, latency monitoring",
        "analytical_impacts": "Reduced detection capability",
        "in_scope": 1, "seq": 6,
    },
    {
        "family_id": "ai_ml", "name": "AI / ML Analytical Models",
        "most_critical_areas": "Dataset stability, feature consistency, missingness",
        "retail_considerations": "Large-scale behavioral feature drift and sparse outcomes",
        "wholesale_considerations": "Sparse labels, relationship complexity, data heterogeneity",
        "typical_risks": "Dataset drift, hidden sampling bias",
        "priority_diagnostics": "Feature drift analysis, missingness diagnostics",
        "analytical_impacts": "Weak out-of-time performance",
        "in_scope": 1, "seq": 7,
    },
    {
        "family_id": "collections_recovery", "name": "Collections & Recovery Models",
        "most_critical_areas": "Behavioral history, cure/recovery consistency",
        "retail_considerations": "Collections treatment paths, restructuring patterns",
        "wholesale_considerations": "Workout-strategy changes, bespoke restructuring approaches",
        "typical_risks": "Treatment-selection bias, inconsistent recovery histories",
        "priority_diagnostics": "Recovery timeline analysis",
        "analytical_impacts": "Unstable recovery estimates",
        "in_scope": 1, "seq": 8,
    },
    {
        "family_id": "marketing_propensity", "name": "Marketing / Propensity Analytics",
        "most_critical_areas": "Selection bias, behavioral drift",
        "retail_considerations": "Campaign-response instability and channel migration",
        "wholesale_considerations": "Relationship-manager-driven acquisition patterns",
        "typical_risks": "Campaign distortion, customer selection bias",
        "priority_diagnostics": "Campaign-response stability analysis",
        "analytical_impacts": "Weak targeting effectiveness",
        "in_scope": 1, "seq": 9,
    },
]


def seed_area_rows() -> list[dict]:
    """Rows ready for `system_db.upsert('dq_framework_areas', ...)`."""
    return [dict(a) for a in AREAS]


def seed_family_rows() -> list[dict]:
    """Rows ready for `system_db.upsert('dq_framework_families', ...)`."""
    return [dict(f) for f in FAMILIES]
