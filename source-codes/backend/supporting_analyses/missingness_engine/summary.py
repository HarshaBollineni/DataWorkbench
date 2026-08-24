"""Dataset-level synthesis of column missingness findings."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

CRITICAL_ROLES = {"identifier", "target", "mandatory"}


def _fitness_assessment(
    total: int,
    breached: list[dict[str, Any]],
    critical: list[dict[str, Any]],
    random_like: list[dict[str, Any]],
    untestable: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a conservative, transparent completeness-only fitness rubric.

    Args:
        total: Number of assessed columns.
        breached: Findings above their role tolerance.
        critical: Breaches affecting identifier, target, or mandatory roles.
        random_like: Validly searched findings with no explainable pattern.
        untestable: Breaches for which no honest tree could be fitted.

    Returns:
        Status, display label, tone, rationale, and the rubric statement.
    """
    breach_share = len(breached) / total if total else 0
    random_share = len(random_like) / total if total else 0
    if critical:
        status, label, tone = "not_ready", "Not yet fit for general use", "high"
        rationale = f"{len(critical)} critical-role field(s) exceed their completeness tolerance."
    elif untestable:
        status, label, tone = "not_ready", "Not yet fit for general use", "high"
        rationale = f"{len(untestable)} field(s) exceed tolerance but could not be tested honestly."
    elif breach_share > 0.50:
        status, label, tone = "not_ready", "Not yet fit for general use", "high"
        rationale = f"{len(breached)} of {total} fields ({breach_share:.0%}) exceed tolerance."
    elif random_share > 0.25:
        status, label, tone = "not_ready", "Not yet fit for general use", "high"
        rationale = (
            f"No-pattern findings affect {len(random_like)} of {total} fields "
            f"({random_share:.0%})."
        )
    elif breached:
        status, label, tone = "conditional", "Fit only with targeted controls", "medium"
        rationale = f"{len(breached)} field(s) need remediation, exclusion, or use-case controls."
    else:
        status, label, tone = "ready", "Fit for use on completeness", "low"
        rationale = "Every assessed field is within its role-based completeness tolerance."
    return {
        "status": status,
        "label": label,
        "tone": tone,
        "rationale": rationale,
        "rubric": (
            "Not ready when a critical role breaches tolerance, any breach is untestable, "
            "more than half of fields breach tolerance, or more than 25% have no "
            "discovered pattern."
        ),
    }


def _common_issues(
    critical: list[dict[str, Any]],
    random_like: list[dict[str, Any]],
    structured: list[dict[str, Any]],
    untestable: list[dict[str, Any]],
    shared_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the three most actionable issue themes in priority order."""
    issues: list[dict[str, Any]] = []
    if critical:
        issues.append({
            "title": "Critical fields exceed tolerance",
            "detail": "Identifier, target, or mandatory fields require priority remediation.",
            "count": len(critical),
            "severity": "high",
        })
    if untestable:
        issues.append({
            "title": "Incomplete but untestable",
            "detail": "Insufficient observations or independent predictors prevent a mechanism claim.",
            "count": len(untestable),
            "severity": "high",
        })
    if random_like:
        issues.append({
            "title": "Missingness unexplained by the shallow search",
            "detail": "No supported, pure, enriched full-data rule met the configured discovery guards.",
            "count": len(random_like),
            "severity": "medium",
        })
    if structured:
        issues.append({
            "title": "Explainable missingness patterns",
            "detail": "Specific segments or interactions concentrate missing values.",
            "count": len(structured),
            "severity": "medium",
        })
    if shared_blocks:
        linked = len({column for block in shared_blocks for column in block["columns"]})
        issues.append({
            "title": "Shared co-missing gaps",
            "detail": f"{linked} fields cluster into {len(shared_blocks)} likely shared-feed events.",
            "count": len(shared_blocks),
            "severity": "medium",
        })
    return issues[:3]


def _top_features(structured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank source features by how many structured findings cite them."""
    frequency: Counter[str] = Counter()
    coverages: dict[str, list[float]] = defaultdict(list)
    lifts: dict[str, list[float]] = defaultdict(list)
    examples: dict[str, list[str]] = defaultdict(list)
    for finding in structured:
        for feature in dict.fromkeys(finding.get("correlates_with", [])):
            frequency[feature] += 1
            if finding.get("rule_missing_coverage") is not None:
                coverages[feature].append(float(finding["rule_missing_coverage"]))
            if finding.get("max_rule_lift") is not None:
                lifts[feature].append(float(finding["max_rule_lift"]))
            if len(examples[feature]) < 3:
                examples[feature].append(finding["column"])
    ranked = sorted(frequency, key=lambda feature: (-frequency[feature], feature))[:3]
    return [
        {
            "feature": feature,
            "findings_explained": frequency[feature],
            "mean_missing_coverage": round(
                sum(coverages[feature]) / len(coverages[feature]), 4
            )
            if coverages[feature]
            else None,
            "mean_max_lift": round(sum(lifts[feature]) / len(lifts[feature]), 4)
            if lifts[feature]
            else None,
            "example_columns": examples[feature],
        }
        for feature in ranked
    ]


def _next_steps(
    critical: list[dict[str, Any]],
    random_like: list[dict[str, Any]],
    structured: list[dict[str, Any]],
    untestable: list[dict[str, Any]],
    shared_blocks: list[dict[str, Any]],
    top_features: list[dict[str, Any]],
    highest_risk: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build up to three prioritized actions from observed evidence."""
    steps: list[dict[str, str]] = []
    if critical or untestable:
        names = [item["column"] for item in (critical + untestable)[:3]]
        steps.append({
            "title": "Resolve blocking fields first",
            "detail": f"Confirm lineage, ownership, and collection rules for {', '.join(names)}.",
        })
    elif highest_risk:
        names = [item["column"] for item in highest_risk[:3]]
        steps.append({
            "title": "Triage the largest tolerance breaches",
            "detail": f"Agree remediate, exclude, or conditionally-use decisions for {', '.join(names)}.",
        })
    if structured:
        driver = top_features[0]["feature"] if top_features else "the reported tree segments"
        steps.append({
            "title": "Investigate repeatable drivers",
            "detail": f"Validate source/process behavior around {driver} and preserve missing indicators downstream.",
        })
    if random_like:
        steps.append({
            "title": "Investigate unexplained gaps",
            "detail": "Review additional variables, segments, lineage, and optional sensitivity diagnostics before treating these gaps as harmless.",
        })
    if shared_blocks and len(steps) < 3:
        steps.append({
            "title": "Remediate shared gaps as feed issues",
            "detail": "Assign one lineage investigation per co-missingness block rather than separate field tickets.",
        })
    if not steps:
        steps.append({
            "title": "Move to downstream fitness checks",
            "detail": "Assess validity, uniqueness, timeliness, drift, and use-case-specific business rules.",
        })
    return steps[:3]


def build_dataset_summary(
    findings: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    n_rows: int,
) -> dict[str, Any]:
    """Synthesize column findings into an executive completeness summary.

    Args:
        findings: Completed per-column result dictionaries.
        blocks: Co-missingness block definitions.
        n_rows: Dataset row count used for overall missing-cell share.

    Returns:
        Fitness assessment, key metrics, issue themes, top explanatory features,
        risk fields, next steps, and suggested further analyses.
    """
    total = len(findings)
    breached = [item for item in findings if item["exceeds_tolerance"]]
    acceptable = [item for item in findings if not item["exceeds_tolerance"]]
    critical = [item for item in breached if item["role"] in CRITICAL_ROLES]
    structured = [item for item in findings if item["verdict"] == "b"]
    random_like = [item for item in findings if item["verdict"] == "c"]
    untestable = [item for item in findings if item["verdict"] == "d"]
    shared_blocks = [block for block in blocks if block["size"] > 1]
    total_cells = n_rows * total
    missing_cells = sum(item["missing_count"] for item in findings)
    highest_risk = sorted(
        breached,
        key=lambda item: (item["missing_share"] - item["tolerance"], item["missing_share"]),
        reverse=True,
    )[:3]
    risk_projection = [
        {
            "column": item["column"],
            "missing_share": item["missing_share"],
            "tolerance": item["tolerance"],
            "verdict": item["verdict"],
        }
        for item in highest_risk
    ]
    top_features = _top_features(structured)
    return {
        "fitness": _fitness_assessment(total, breached, critical, random_like, untestable),
        "metrics": {
            "acceptable_fields": len(acceptable),
            "acceptable_field_share": round(len(acceptable) / total, 6) if total else 0,
            "breached_fields": len(breached),
            "overall_missing_cell_share": round(missing_cells / total_cells, 6)
            if total_cells
            else 0,
            "critical_breaches": len(critical),
            "shared_blocks": len(shared_blocks),
        },
        "common_issues": _common_issues(
            critical, random_like, structured, untestable, shared_blocks
        ),
        "top_explanatory_features": top_features,
        "highest_risk_fields": risk_projection,
        "recommended_next_steps": _next_steps(
            critical,
            random_like,
            structured,
            untestable,
            shared_blocks,
            top_features,
            risk_projection,
        ),
        "additional_analyses": [
            "Additional-variable and segment review for unexplained fields",
            "Optional period- or entity-based sensitivity of discovered rules",
            "Imputation and exclusion sensitivity on downstream outcomes",
            "Completeness drift monitoring by period, source, and segment",
        ],
        "scope_caveat": (
            "This fitness verdict covers completeness only. Final fitness for use also requires "
            "validity, accuracy, uniqueness, timeliness, representativeness, and use-case review."
        ),
    }
