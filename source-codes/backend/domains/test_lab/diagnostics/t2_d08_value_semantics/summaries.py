"""Summaries for cell-level value-semantics execution results."""
from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from .engine import CellExecutionResult


def _roles(bindings: Mapping[str, list[str]], column: str) -> str:
    return ";".join(bindings.get(column, []))


def summarize_value_semantics(data: pd.DataFrame, dictionary: pd.DataFrame,
                              bindings: Mapping[str, list[str]], kb: Mapping[str, Any],
                              result: CellExecutionResult) -> dict[str, pd.DataFrame]:
    """Build non-overlapping cell summaries plus rule-assessment summaries."""
    ledger = result.assessment_ledger
    tags = result.resolved_cell_tags
    target_roles = {entry["target_role"] for rule in kb["rules"] for entry in rule["entries"]}
    target_fields = {column for column, roles in bindings.items() if set(roles) & target_roles}

    field_rows = []
    for column in data.columns:
        column_ledger = ledger.loc[ledger["input_variable"].eq(column)]
        column_tags = tags.loc[tags["input_variable"].eq(column)]
        assessed_refs = set(column_ledger.loc[column_ledger["row_reference"].ne(""), "row_reference"])
        counts = column_tags["tag"].value_counts()
        field_rows.append({
            "input_variable": column,
            "semantic_roles": _roles(bindings, column),
            "total_rows": len(data),
            "assessed_cells": len(assessed_refs),
            "tagged_cells": len(column_tags),
            "censored_count": int(counts.get("CENSORED", 0)),
            "stale_frozen_count": int(counts.get("STALE_FROZEN", 0)),
            "not_applicable_count": int(counts.get("NOT_APPLICABLE", 0)),
            "no_tag_cells": max(0, len(assessed_refs) - len(column_tags)),
            "unclassified_assessments": int(column_ledger["status"].eq("UNCLASSIFIED").sum()),
            "unscoped_entries": int(column_ledger["status"].eq("UNSCOPED").sum()),
            "tag_rate": len(column_tags) / len(assessed_refs) if assessed_refs else None,
            "examination_status": (
                "NO_SEMANTIC_ROLE" if column not in bindings else
                "NO_RULE_ENTRY" if column not in target_fields else
                "ASSESSED" if assessed_refs else "UNSCOPED"
            ),
        })
    field_summary = pd.DataFrame(field_rows)

    rule_rows = []
    if not ledger.empty:
        for (rule, entry, field), group in ledger.groupby(["rule", "entry", "input_variable"], dropna=False):
            counts = group["status"].value_counts()
            row_assessments = group["row_reference"].ne("").sum()
            rule_rows.append({
                "rule": rule, "entry": entry, "input_variable": field,
                "matched_role": group["matched_role"].iloc[0],
                "assessment_grain": group["assessment_grain"].iloc[0],
                "row_assessments": int(row_assessments),
                "tagged": int(counts.get("APPLIED_TAG", 0)),
                "no_tag": int(counts.get("NO_TAG", 0)),
                "unclassified": int(counts.get("UNCLASSIFIED", 0)),
                "unscoped": int(counts.get("UNSCOPED", 0)),
                "tag_rate": int(counts.get("APPLIED_TAG", 0)) / row_assessments if row_assessments else None,
            })
    rule_summary = pd.DataFrame(rule_rows)

    groups = result.group_assessments
    if groups.empty:
        group_summary = pd.DataFrame(columns=["entry", "input_variable", "candidate_groups", "assessed_groups",
                                              "tagged_groups", "below_minimum_groups", "assessed_group_share"])
    else:
        group_rows = []
        for (entry, field), group in groups.groupby(["entry", "input_variable"]):
            candidate = len(group)
            assessed = int(group["assessed"].sum())
            group_rows.append({
                "entry": entry, "input_variable": field, "candidate_groups": candidate,
                "assessed_groups": assessed, "tagged_groups": int(group["tagged"].sum()),
                "below_minimum_groups": int(group["outcome"].eq("BELOW_MINIMUM").sum()),
                "assessed_group_share": assessed / candidate if candidate else None,
            })
        group_summary = pd.DataFrame(group_rows)

    if result.raw_cell_claims.empty:
        precedence_summary = pd.DataFrame(columns=["row_reference", "input_variable", "claim_count",
                                                   "winning_tag", "suppressed_tags"])
    else:
        overlaps = result.resolved_cell_tags.loc[result.resolved_cell_tags["claim_count"].gt(1)]
        precedence_summary = overlaps[["row_reference", "input_variable", "claim_count", "tag", "suppressed_tags"]].rename(
            columns={"tag": "winning_tag"}).reset_index(drop=True)

    populated_na = tags.loc[tags["tag"].eq("NOT_APPLICABLE") & tags["is_populated"]].copy()
    populated_na = populated_na[["row_reference", "input_variable", "matched_role", "entry", "input_value"]]

    unexamined = field_summary.loc[
        field_summary["examination_status"].isin(["NO_SEMANTIC_ROLE", "NO_RULE_ENTRY", "UNSCOPED"]),
        ["input_variable", "semantic_roles", "examination_status", "unscoped_entries"],
    ].reset_index(drop=True)

    status_counts = ledger["status"].value_counts() if not ledger.empty else pd.Series(dtype=int)
    tag_counts = tags["tag"].value_counts() if not tags.empty else pd.Series(dtype=int)
    examined_fields = int(field_summary["examination_status"].eq("ASSESSED").sum())
    verdict = "PARTIALLY_CLASSIFIED" if (
        status_counts.get("UNCLASSIFIED", 0) or status_counts.get("UNSCOPED", 0)
    ) else "FULLY_CLASSIFIED"
    dataset_summary = pd.DataFrame([{
        "input_rows": len(data), "input_columns": len(data.columns),
        "columns_with_roles": len(bindings), "examined_fields": examined_fields,
        "unexamined_fields": len(unexamined),
        "ready_rule_routes": int(result.execution_plan["routing_status"].eq("READY_FOR_EVALUATION").sum()),
        "unscoped_rule_routes": int(result.execution_plan["routing_status"].eq("UNSCOPED").sum()),
        "rule_assessments": int(ledger["row_reference"].ne("").sum()) if not ledger.empty else 0,
        "distinct_tagged_cells": len(tags),
        "censored_cells": int(tag_counts.get("CENSORED", 0)),
        "stale_frozen_cells": int(tag_counts.get("STALE_FROZEN", 0)),
        "not_applicable_cells": int(tag_counts.get("NOT_APPLICABLE", 0)),
        "unclassified_assessments": int(status_counts.get("UNCLASSIFIED", 0)),
        "unscoped_entries": int(status_counts.get("UNSCOPED", 0)),
        "multi_claim_cells": len(precedence_summary),
        "populated_where_not_applicable": len(populated_na),
        "run_verdict": verdict,
    }])

    assessment_outcomes = (
        ledger.groupby(["input_variable", "rule", "entry", "status"], dropna=False).size()
        .rename("count").reset_index()
    ) if not ledger.empty else pd.DataFrame(columns=["input_variable", "rule", "entry", "status", "count"])

    return {
        "dataset_summary": dataset_summary,
        "field_summary": field_summary,
        "rule_summary": rule_summary,
        "group_summary": group_summary,
        "assessment_outcomes": assessment_outcomes,
        "precedence_summary": precedence_summary,
        "populated_where_not_applicable": populated_na.reset_index(drop=True),
        "unexamined_fields": unexamined,
    }
