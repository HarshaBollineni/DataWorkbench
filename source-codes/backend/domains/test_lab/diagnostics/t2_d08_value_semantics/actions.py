"""Action-focused interpretation of value-semantics diagnostic evidence."""
from __future__ import annotations

from typing import Mapping

import pandas as pd

from .engine import CellExecutionResult


ACTION_COLUMNS = [
    "priority", "user_decision", "scope", "input_variable", "matched_role", "tag",
    "reason_code", "affected_cells", "affected_rows", "affected_share", "sample_row_references",
    "issue_statement", "recommended_next_step", "rca_warranted", "evidence_reference",
]
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


def _sample(values: pd.Series, limit: int = 5) -> str:
    return ";".join(str(value) for value in values.dropna().astype(str).drop_duplicates().head(limit))


def _tag_interpretation(tag: str, reason: str, populated: bool) -> tuple[str, str, str, str, bool]:
    if tag == "STALE_FROZEN":
        if reason == "declared_field_sentinel":
            return (
                "HIGH", "RAISE_ISSUE_FOR_RCA",
                "A declared unavailable/sentinel value is present in a field expected to carry analytical information.",
                "Raise a source-data issue. Confirm the sentinel mapping, identify why the value was unavailable, and assess affected downstream use.",
                True,
            )
        return (
            "HIGH", "RAISE_ISSUE_FOR_RCA",
            "The value did not change at the KB-declared monitoring grain despite sufficient observations.",
            "Raise an RCA on the source refresh and transformation path; confirm whether the field is genuinely stable or its updates are frozen.",
            True,
        )
    if tag == "NOT_APPLICABLE" and populated:
        return (
            "HIGH", "RAISE_ISSUE_FOR_RCA",
            "A value is populated where the KB says the field is not applicable for this row context.",
            "Raise a data-treatment issue. Determine whether the value is a placeholder, mapping leakage, or a valid policy exception before modelling use.",
            True,
        )
    if tag == "NOT_APPLICABLE":
        return (
            "INFO", "APPLY_EXPECTED_TREATMENT",
            "The field is not applicable in this row context and is not unexpectedly populated.",
            "Retain the NOT_APPLICABLE classification and exclude the cell from ordinary missing-value or model-feature treatment.",
            False,
        )
    return (
        "INFO", "APPLY_EXPECTED_TREATMENT",
        "The outcome is not observable within the declared data window or event state.",
        "Apply the CENSORED treatment in downstream analysis; raise an RCA only if the declared observation boundary or event state is incorrect.",
        False,
    )


def build_action_focused_summary(
    data: pd.DataFrame,
    bindings: Mapping[str, list[str]],
    result: CellExecutionResult,
    summaries: Mapping[str, pd.DataFrame],
    *,
    binding_results: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Translate diagnostic evidence into explicit user decisions without inventing defects."""
    rows: list[dict] = []
    tags = result.resolved_cell_tags
    if not tags.empty:
        grouping = ["input_variable", "matched_role", "tag", "reason_code", "is_populated"]
        for keys, group in tags.groupby(grouping, dropna=False):
            field, role, tag, reason, populated = keys
            priority, decision, issue, next_step, rca = _tag_interpretation(
                str(tag), str(reason), bool(populated),
            )
            affected_rows = group["row_reference"].nunique()
            rows.append({
                "priority": priority, "user_decision": decision, "scope": "CELL_TAG",
                "input_variable": field, "matched_role": role, "tag": tag,
                "reason_code": reason, "affected_cells": len(group), "affected_rows": affected_rows,
                "affected_share": affected_rows / len(data) if len(data) else None,
                "sample_row_references": _sample(group["row_reference"]),
                "issue_statement": issue, "recommended_next_step": next_step,
                "rca_warranted": rca, "evidence_reference": "resolved_cell_tags",
            })

    unclassified = result.assessment_ledger.loc[result.assessment_ledger["status"].eq("UNCLASSIFIED")]
    for (field, role, entry), group in unclassified.groupby(["input_variable", "matched_role", "entry"], dropna=False):
        rows.append({
            "priority": "MEDIUM", "user_decision": "REVIEW_CONFIGURATION", "scope": "UNCLASSIFIED_EVIDENCE",
            "input_variable": field, "matched_role": role, "tag": "", "reason_code": "indeterminate_evidence",
            "affected_cells": len(group), "affected_rows": group["row_reference"].nunique(),
            "affected_share": group["row_reference"].nunique() / len(data) if len(data) else None,
            "sample_row_references": _sample(group["row_reference"]),
            "issue_statement": "The KB entry could not classify these rows because required evidence was invalid or outside its declared domain.",
            "recommended_next_step": "Ask a domain reviewer to distinguish a declaration gap from a real data defect; promote only a confirmed defect.",
            "rca_warranted": False, "evidence_reference": f"assessment_ledger:{entry}",
        })

    unscoped = result.execution_plan.loc[result.execution_plan["routing_status"].eq("UNSCOPED")]
    for item in unscoped.to_dict(orient="records"):
        missing = ";".join(value for value in [item.get("missing_roles", ""), item.get("missing_declarations", "")] if value)
        rows.append({
            "priority": "MEDIUM", "user_decision": "REVIEW_CONFIGURATION", "scope": "UNSCOPED_RULE",
            "input_variable": item["input_variable"], "matched_role": item["matched_role"], "tag": "",
            "reason_code": missing, "affected_cells": 0, "affected_rows": 0, "affected_share": None,
            "sample_row_references": "",
            "issue_statement": "An applicable KB entry was not executed because a role binding or runtime declaration is missing.",
            "recommended_next_step": "Supply or review the listed prerequisite. Do not interpret the absence of tags from this route as a pass.",
            "rca_warranted": False, "evidence_reference": f"execution_plan:{item['entry']}",
        })

    if binding_results is not None and not binding_results.empty:
        bound = set(bindings)
        unresolved = binding_results.loc[~binding_results["column_name"].isin(bound)]
        for item in unresolved.to_dict(orient="records"):
            rows.append({
                "priority": "MEDIUM", "user_decision": "REVIEW_CONFIGURATION", "scope": "UNRESOLVED_BINDING",
                "input_variable": item["column_name"], "matched_role": "", "tag": "",
                "reason_code": item.get("decision", ""), "affected_cells": 0, "affected_rows": 0,
                "affected_share": None, "sample_row_references": "",
                "issue_statement": "The selected column has no execution-ready semantic role binding.",
                "recommended_next_step": "Review the proposed candidates or LLM result and add an approved binding override before relying on coverage.",
                "rca_warranted": False, "evidence_reference": "column_binding_results",
            })

    action_queue = pd.DataFrame(rows, columns=ACTION_COLUMNS)
    if not action_queue.empty:
        action_queue["_priority"] = action_queue["priority"].map(PRIORITY_ORDER)
        action_queue = action_queue.sort_values(
            ["_priority", "user_decision", "input_variable", "reason_code"], na_position="last",
        ).drop(columns="_priority").reset_index(drop=True)

    field_summary = summaries["field_summary"]
    expected_path = field_summary.loc[
        field_summary["examination_status"].eq("ASSESSED") & field_summary["tagged_cells"].eq(0),
        ["input_variable", "semantic_roles", "assessed_cells", "no_tag_cells"],
    ].copy()
    if not expected_path.empty:
        expected_path["user_decision"] = "NO_ACTION_EXPECTED_PATH"
        expected_path["reason"] = "The field was assessed and no KB condition was met."

    counts = action_queue["user_decision"].value_counts() if not action_queue.empty else pd.Series(dtype=int)
    if counts.get("RAISE_ISSUE_FOR_RCA", 0):
        overall = "RAISE_ISSUE_FOR_RCA"
        statement = "One or more findings warrant an issue and root-cause analysis; also complete any configuration reviews."
    elif counts.get("REVIEW_CONFIGURATION", 0):
        overall = "REVIEW_CONFIGURATION"
        statement = "No RCA-triggering finding was identified, but coverage is incomplete until configuration or bindings are reviewed."
    elif counts.get("APPLY_EXPECTED_TREATMENT", 0):
        overall = "EXPECTED_PATH_APPLY_TREATMENT"
        statement = "Findings follow declared semantics; apply the indicated censoring or not-applicable treatment without opening an RCA."
    else:
        overall = "EXPECTED_PATH_NO_ACTION"
        statement = "Assessed data follows the expected path and no diagnostic action is required."
    executive = pd.DataFrame([{
        "overall_user_decision": overall,
        "plain_language_outcome": statement,
        "raise_issue_items": int(counts.get("RAISE_ISSUE_FOR_RCA", 0)),
        "configuration_review_items": int(counts.get("REVIEW_CONFIGURATION", 0)),
        "expected_treatment_items": int(counts.get("APPLY_EXPECTED_TREATMENT", 0)),
        "expected_no_action_fields": len(expected_path),
        "tagged_cells": len(tags),
        "important_note": "Counts indicate scope, not severity. Review the action queue and evidence before escalation.",
    }])
    rca_evidence = action_queue.loc[action_queue["rca_warranted"]].reset_index(drop=True) if not action_queue.empty else action_queue.copy()
    return {
        "executive_action_summary": executive,
        "action_queue": action_queue,
        "rca_evidence": rca_evidence,
        "expected_path_summary": expected_path.reset_index(drop=True),
    }
