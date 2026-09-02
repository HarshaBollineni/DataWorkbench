"""Pure deterministic engine for Test 2, Diagnostic 6: Row Completeness."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Iterable

import pandas as pd

from .models import (
    RULE_IDS,
    RULE_TITLES,
    FindingEvidence,
    InferenceDisclosure,
    ReconciliationPayload,
    RoleBinding,
    RuleResult,
    RunScope,
)
from .periods import key_from_ordinal, parse_period


ENGINE_VERSION = "1.0.0"
METHODOLOGY_VERSION = "row-completeness-observed-span-v1"


class RowCompletenessInputError(ValueError):
    """The frozen scope cannot be evaluated against the supplied table."""


def _is_null(value: Any) -> bool:
    if value is None or value is pd.NaT:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _identifier(value: Any) -> str | None:
    if _is_null(value):
        return None
    text = str(value).strip()
    return text or None


def _canonical_cell(value: Any) -> str:
    if _is_null(value):
        return "<NULL>"
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _bounded(values: list[Any], limit: int) -> tuple[list[Any], bool]:
    return values[:limit], len(values) > limit


def _rule(
    rule_id: str,
    *,
    outcome: str,
    issue_count: int,
    explanation: str,
    total_findings: int = 0,
    evidence: list[FindingEvidence] | None = None,
    measure: float | None = None,
    floor: float | None = None,
    affected: dict[str, int] | None = None,
    metrics: dict[str, Any] | None = None,
    na_reason: str | None = None,
) -> RuleResult:
    retained = evidence or []
    return RuleResult(
        rule_id=rule_id,
        title=RULE_TITLES[rule_id],
        outcome=outcome,
        issue_count=issue_count,
        measure=measure,
        continuity_floor=floor,
        affected_population=affected or {},
        metrics=metrics or {},
        explanation=explanation,
        total_findings=total_findings,
        evidence=retained,
        evidence_truncated=total_findings > len(retained),
        na_reason=na_reason,
    )


def _require_columns(frame: pd.DataFrame, scope: RunScope) -> None:
    required = [scope.facility_id.column, scope.period.column]
    if scope.segment is not None:
        required.append(scope.segment.column)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise RowCompletenessInputError(
            "bound columns are absent from the selected table: " + ", ".join(sorted(missing))
        )


def _prepare(frame: pd.DataFrame, scope: RunScope) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    work = frame.copy(deep=False).reset_index(drop=True).copy()
    work["_row_position"] = range(1, len(work) + 1)
    work["_row_reference"] = work["_row_position"].map(lambda value: f"ROW-{value:08d}")
    facility_values, period_keys, period_ordinals, invalid = [], [], [], []
    facility_column, period_column = scope.facility_id.column, scope.period.column
    for position, (facility_raw, period_raw) in enumerate(
        zip(work[facility_column].tolist(), work[period_column].tolist()), start=1
    ):
        facility = _identifier(facility_raw)
        period_key, period_ordinal, reasons = None, None, []
        if facility is None:
            reasons.append("Facility identifier is null or blank.")
        try:
            parsed = parse_period(period_raw, scope.reporting_grain)
            period_key, period_ordinal = parsed.key, parsed.ordinal
        except ValueError as exc:
            reasons.append(str(exc).capitalize() + ".")
        facility_values.append(facility)
        period_keys.append(period_key)
        period_ordinals.append(period_ordinal)
        if reasons:
            invalid.append({
                "position": position,
                "row_reference": f"ROW-{position:08d}",
                "facility": facility,
                "period_key": period_key,
                "facility_invalid": facility is None,
                "period_invalid": period_ordinal is None,
                "reason": " ".join(reasons),
            })
    work["_facility"] = facility_values
    work["_period_key"] = period_keys
    work["_period_ordinal"] = pd.array(period_ordinals, dtype="Int64")
    work["_valid_key"] = work["_facility"].notna() & work["_period_ordinal"].notna()
    return work, invalid


def _duplicate_groups(
    work: pd.DataFrame, original_columns: list[str], segment: RoleBinding | None,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[int]] = defaultdict(list)
    for index, row in work[work["_valid_key"]].iterrows():
        groups[(row["_facility"], int(row["_period_ordinal"]))].append(index)
    duplicates = []
    for (facility, ordinal), indices in sorted(groups.items()):
        if len(indices) < 2:
            continue
        signatures = {
            tuple(_canonical_cell(work.at[index, column]) for column in original_columns)
            for index in indices
        }
        segment_values = ({_identifier(work.at[index, segment.column]) for index in indices}
                          if segment is not None else set())
        segment_values.discard(None)
        segment_label = (next(iter(segment_values)) if len(segment_values) == 1
                         else "(missing or mixed)") if segment is not None else None
        duplicates.append({
            "facility": facility,
            "ordinal": ordinal,
            "period": str(work.at[indices[0], "_period_key"]),
            "row_count": len(indices),
            "surplus_rows": len(indices) - 1,
            "classification": "exact" if len(signatures) == 1 else "conflicting",
            "row_reference": str(work.at[indices[0], "_row_reference"]),
            "segment": segment_label,
            "indices": indices,
        })
    return duplicates


def _required_grid(
    valid_pairs: set[tuple[str, int]], *, max_required_pairs: int,
) -> tuple[set[tuple[str, int]], dict[str, tuple[int, int]]]:
    by_facility: dict[str, list[int]] = defaultdict(list)
    for facility, ordinal in valid_pairs:
        by_facility[facility].append(ordinal)
    spans = {facility: (min(periods), max(periods)) for facility, periods in by_facility.items()}
    required_count = sum(last - first + 1 for first, last in spans.values())
    if required_count > max_required_pairs:
        raise RowCompletenessInputError(
            f"required continuity grid would contain {required_count:,} facility-period pairs, "
            f"above the operational limit of {max_required_pairs:,}"
        )
    required = {
        (facility, ordinal)
        for facility, (first, last) in spans.items()
        for ordinal in range(first, last + 1)
    }
    return required, spans


def _stable_segments(work: pd.DataFrame, segment: RoleBinding | None) -> tuple[dict[str, str], set[str]]:
    if segment is None:
        return {}, set()
    observations: dict[str, list[str | None]] = defaultdict(list)
    for _, row in work[work["_valid_key"]].iterrows():
        observations[row["_facility"]].append(_identifier(row[segment.column]))
    stable, unstable = {}, set()
    for facility, values in observations.items():
        distinct = {value for value in values if value is not None}
        if len(distinct) == 1 and all(value is not None for value in values):
            stable[facility] = next(iter(distinct))
        else:
            unstable.add(facility)
    return stable, unstable


def _period_rows(
    required: set[tuple[str, int]], received: set[tuple[str, int]], grain: str,
) -> list[dict[str, Any]]:
    required_by_period = Counter(ordinal for _, ordinal in required)
    received_by_period = Counter(ordinal for facility, ordinal in received
                                 if (facility, ordinal) in required)
    rows = []
    for ordinal in sorted(required_by_period):
        required_count = required_by_period[ordinal]
        received_count = received_by_period[ordinal]
        rows.append({
            "period": key_from_ordinal(ordinal, grain),
            "ordinal": ordinal,
            "required_facilities": required_count,
            "received_facilities": received_count,
            "missing_facilities": required_count - received_count,
            "coverage": received_count / required_count,
        })
    return rows


def evaluate_row_completeness(
    frame: pd.DataFrame,
    *,
    scope: RunScope | dict[str, Any],
    origin_run_id: str,
    calculation_inference_disclosure: InferenceDisclosure | dict[str, Any],
    manifest_fingerprint: str,
    source_artifact_references: Iterable[dict[str, str]] = (),
    evidence_limit: int = 10,
    aggregate_limit: int = 100,
    max_required_pairs: int = 5_000_000,
    methodology_version: str = METHODOLOGY_VERSION,
    engine_version: str = ENGINE_VERSION,
    rule_specs: Iterable[dict[str, Any]] | None = None,
) -> ReconciliationPayload:
    """Evaluate all six rules from one immutable in-memory table snapshot."""
    if not isinstance(frame, pd.DataFrame):
        raise RowCompletenessInputError("frame must be a pandas DataFrame")
    if evidence_limit < 0 or aggregate_limit < 1 or max_required_pairs < 1:
        raise RowCompletenessInputError(
            "evidence_limit must be non-negative; aggregate and grid limits must be positive"
        )
    resolved_scope = scope if isinstance(scope, RunScope) else RunScope.model_validate(scope)
    disclosure = (calculation_inference_disclosure
                  if isinstance(calculation_inference_disclosure, InferenceDisclosure)
                  else InferenceDisclosure.model_validate(calculation_inference_disclosure))
    _require_columns(frame, resolved_scope)
    work, invalid_rows = _prepare(frame, resolved_scope)
    valid = work[work["_valid_key"]]
    received_pairs = {
        (row["_facility"], int(row["_period_ordinal"])) for _, row in valid.iterrows()
    }
    required, spans = _required_grid(received_pairs, max_required_pairs=max_required_pairs)
    missing_pairs = sorted(required - received_pairs)
    duplicates = _duplicate_groups(work, list(frame.columns), resolved_scope.segment)
    stable_segments, unstable_segments = _stable_segments(work, resolved_scope.segment)

    invalid_evidence_all = [FindingEvidence(
        finding_type="invalid_key", facility_id=row["facility"], period=row["period_key"],
        row_reference=row["row_reference"], reason=row["reason"],
    ) for row in invalid_rows]
    invalid_evidence, _ = _bounded(invalid_evidence_all, evidence_limit)
    valid_share = (len(frame) - len(invalid_rows)) / len(frame) if len(frame) else None
    rule_01 = _rule(
        "T2D6-01", outcome="PASS" if not invalid_rows else "VIOLATION",
        issue_count=len(invalid_rows), measure=valid_share,
        explanation=("Every row has an assignable facility and reporting period."
                     if not invalid_rows else f"{len(invalid_rows):,} row(s) cannot be assigned to a valid facility-period key."),
        total_findings=len(invalid_rows), evidence=invalid_evidence,
        affected={"rows_evaluated": len(frame), "invalid_rows": len(invalid_rows)},
        metrics={"valid_rows": len(frame) - len(invalid_rows), "invalid_rows": len(invalid_rows),
                 "invalid_facility_rows": sum(row["facility_invalid"] for row in invalid_rows),
                 "invalid_period_rows": sum(row["period_invalid"] for row in invalid_rows),
                 "invalid_both_roles_rows": sum(row["facility_invalid"] and row["period_invalid"]
                                                for row in invalid_rows)},
    )

    parseable_ordinals = sorted({int(value) for value in work["_period_ordinal"].dropna().tolist()})
    absent_calendar_ordinals: list[int] = []
    if not parseable_ordinals:
        rule_02 = _rule(
            "T2D6-02", outcome="NOT-APPLICABLE", issue_count=0,
            explanation="No valid reporting periods are available to construct the panel calendar.",
            na_reason="No valid reporting periods are available.",
        )
    else:
        calendar = list(range(parseable_ordinals[0], parseable_ordinals[-1] + 1))
        observed_ordinals = set(parseable_ordinals)
        absent = [ordinal for ordinal in calendar if ordinal not in observed_ordinals]
        absent_calendar_ordinals = absent
        calendar_evidence_all = [FindingEvidence(
            finding_type="missing_pair", period=key_from_ordinal(ordinal, resolved_scope.reporting_grain),
            reason="The entire reporting period is absent from the selected table.",
        ) for ordinal in absent]
        calendar_evidence, _ = _bounded(calendar_evidence_all, evidence_limit)
        calendar_coverage = (len(calendar) - len(absent)) / len(calendar)
        absent_labels = [key_from_ordinal(value, resolved_scope.reporting_grain) for value in absent]
        rule_02 = _rule(
            "T2D6-02", outcome="PASS" if not absent else "VIOLATION",
            issue_count=len(absent), measure=calendar_coverage,
            explanation=("Every reporting period between the first and last observed periods is present."
                         if not absent else f"{len(absent):,} complete reporting period(s) are absent."),
            total_findings=len(absent), evidence=calendar_evidence,
            affected={"periods_required": len(calendar), "periods_missing": len(absent)},
            metrics={"first_period": key_from_ordinal(calendar[0], resolved_scope.reporting_grain),
                     "last_period": key_from_ordinal(calendar[-1], resolved_scope.reporting_grain),
                     "required_periods": len(calendar), "received_periods": len(calendar) - len(absent),
                     "missing_periods": absent_labels[:aggregate_limit],
                     "missing_periods_truncated": len(absent_labels) > aggregate_limit},
        )

    period_results = _period_rows(required, received_pairs, resolved_scope.reporting_grain) if required else []
    if not period_results:
        rule_03 = _rule(
            "T2D6-03", outcome="NOT-APPLICABLE", issue_count=0,
            explanation="No valid facility observed spans are available for period-level coverage.",
            na_reason="No valid facility-period pairs are available.",
        )
    else:
        failing_periods = [row for row in period_results if row["coverage"] < resolved_scope.continuity_floor]
        failing_ordinals = {row["ordinal"] for row in failing_periods}
        period_missing = [(facility, ordinal) for facility, ordinal in missing_pairs if ordinal in failing_ordinals]
        evidence_all = [FindingEvidence(
            finding_type="missing_pair", facility_id=facility,
            period=key_from_ordinal(ordinal, resolved_scope.reporting_grain),
            reason="This required facility row was not received in a period below the minimum.",
        ) for facility, ordinal in period_missing]
        evidence, _ = _bounded(evidence_all, evidence_limit)
        minimum_period_coverage = min(row["coverage"] for row in period_results)
        rule_03 = _rule(
            "T2D6-03", outcome="PASS" if not failing_periods else "VIOLATION",
            issue_count=len(failing_periods), measure=minimum_period_coverage,
            floor=resolved_scope.continuity_floor,
            explanation=(f"Every reporting period received at least {resolved_scope.continuity_floor:.0%} "
                         "of its required facility rows."
                         if not failing_periods else
                         f"{len(failing_periods):,} reporting period(s) received fewer than "
                         f"{resolved_scope.continuity_floor:.0%} of their required facility rows."),
            total_findings=len(period_missing), evidence=evidence,
            affected={"periods_assessed": len(period_results), "failing_periods": len(failing_periods)},
            metrics={"minimum_period_coverage": minimum_period_coverage,
                     "period_results": [{key: value for key, value in row.items() if key != "ordinal"}
                                        for row in period_results[:aggregate_limit]],
                     "period_results_truncated": len(period_results) > aggregate_limit},
        )

    duplicate_evidence_all = [FindingEvidence(
        finding_type="duplicate_pair", facility_id=row["facility"], period=row["period"],
        row_reference=row["row_reference"],
        reason=(f"{row['row_count']} rows share this key; classified as "
                f"{row['classification']} duplicate data."),
    ) for row in duplicates]
    duplicate_evidence, _ = _bounded(duplicate_evidence_all, evidence_limit)
    surplus_rows = sum(row["surplus_rows"] for row in duplicates)
    duplicate_periods = Counter(row["period"] for row in duplicates)
    duplicate_segments = Counter(row["segment"] for row in duplicates if row["segment"] is not None)
    exact_count = sum(row["classification"] == "exact" for row in duplicates)
    conflicting_count = len(duplicates) - exact_count
    rule_04 = _rule(
        "T2D6-04", outcome="PASS" if not duplicates else "VIOLATION",
        issue_count=len(duplicates),
        explanation=("No facility appears more than once in the same reporting period."
                     if not duplicates else f"{len(duplicates):,} repeated facility-period pair(s) were found."),
        total_findings=len(duplicates), evidence=duplicate_evidence,
        affected={"duplicate_pairs": len(duplicates), "rows_affected": sum(row["row_count"] for row in duplicates)},
        metrics={"duplicate_pairs": len(duplicates), "surplus_rows": surplus_rows,
                 "rows_affected": sum(row["row_count"] for row in duplicates),
                 "exact_duplicate_pairs": exact_count, "conflicting_duplicate_pairs": conflicting_count,
                 "maximum_rows_in_pair": max((row["row_count"] for row in duplicates), default=1),
                 "pairs_by_period": [{"period": key, "duplicate_pairs": value}
                                     for key, value in sorted(duplicate_periods.items())[:aggregate_limit]],
                 "pairs_by_period_truncated": len(duplicate_periods) > aggregate_limit,
                 "pairs_by_segment": [{"segment": key, "duplicate_pairs": value}
                                      for key, value in sorted(duplicate_segments.items())[:aggregate_limit]],
                 "pairs_by_segment_truncated": len(duplicate_segments) > aggregate_limit},
    )

    portfolio_coverage = len(received_pairs & required) / len(required) if required else None
    missing_evidence_all = [FindingEvidence(
        finding_type="missing_pair", facility_id=facility,
        period=key_from_ordinal(ordinal, resolved_scope.reporting_grain),
        reason="The period falls inside this facility's observed span but no row was received.",
    ) for facility, ordinal in missing_pairs]
    missing_evidence, _ = _bounded(missing_evidence_all, evidence_limit)
    facilities_with_gaps = {facility for facility, _ in missing_pairs}
    periods_with_gaps = {ordinal for _, ordinal in missing_pairs}
    if portfolio_coverage is None:
        rule_05 = _rule(
            "T2D6-05", outcome="NOT-APPLICABLE", issue_count=0,
            explanation="No valid facility observed spans are available for portfolio coverage.",
            na_reason="No valid facility-period pairs are available.",
        )
    else:
        portfolio_failed = portfolio_coverage < resolved_scope.continuity_floor
        rule_05 = _rule(
            "T2D6-05", outcome="VIOLATION" if portfolio_failed else "PASS",
            issue_count=len(missing_pairs) if portfolio_failed else 0,
            measure=portfolio_coverage, floor=resolved_scope.continuity_floor,
            explanation=(f"The portfolio received {portfolio_coverage:.1%} of required facility-period rows, "
                         f"at or above the {resolved_scope.continuity_floor:.0%} minimum."
                         if not portfolio_failed else
                         f"The portfolio received {portfolio_coverage:.1%} of required facility-period rows, "
                         f"below the {resolved_scope.continuity_floor:.0%} minimum."),
            total_findings=len(missing_pairs), evidence=missing_evidence,
            affected={"facilities_assessed": len(spans), "facilities_with_gaps": len(facilities_with_gaps),
                      "periods_with_gaps": len(periods_with_gaps)},
            metrics={"required_pairs": len(required), "received_required_pairs": len(received_pairs & required),
                     "missing_pairs": len(missing_pairs), "continuity_coverage": portfolio_coverage},
        )

    affected_segments: set[str] = set()
    if resolved_scope.segment is None:
        rule_06 = _rule(
            "T2D6-06", outcome="NOT-APPLICABLE", issue_count=0,
            explanation="Segment analysis was not requested because no segment role is bound.",
            na_reason="No segment role is bound.",
        )
    elif not stable_segments:
        rule_06 = _rule(
            "T2D6-06", outcome="NOT-ASSESSABLE", issue_count=0,
            explanation="No facility has a complete, stable segment assignment across its observed rows.",
            affected={"unstable_facilities": len(unstable_segments), "assessable_facilities": 0},
            metrics={"unstable_facilities": len(unstable_segments), "assessable_cells": 0},
        )
    else:
        segment_required = {(stable_segments[facility], facility, ordinal)
                            for facility, ordinal in required if facility in stable_segments}
        segment_received = {(stable_segments[facility], facility, ordinal)
                            for facility, ordinal in received_pairs if facility in stable_segments}
        cell_required: Counter[tuple[str, int]] = Counter((segment, ordinal)
            for segment, _, ordinal in segment_required)
        cell_received: Counter[tuple[str, int]] = Counter((segment, ordinal)
            for segment, _, ordinal in segment_received)
        cell_rows = []
        for (segment, ordinal), required_count in sorted(cell_required.items()):
            received_count = cell_received[(segment, ordinal)]
            coverage = received_count / required_count
            cell_rows.append({"segment": segment,
                              "period": key_from_ordinal(ordinal, resolved_scope.reporting_grain),
                              "ordinal": ordinal, "required_facilities": required_count,
                              "received_facilities": received_count,
                              "missing_facilities": required_count - received_count,
                              "coverage": coverage})
        failing_cells = [row for row in cell_rows if row["coverage"] < resolved_scope.continuity_floor]
        failing_keys = {(row["segment"], row["ordinal"]) for row in failing_cells}
        segment_missing = sorted(segment_required - segment_received)
        failing_missing = [(segment, facility, ordinal) for segment, facility, ordinal in segment_missing
                           if (segment, ordinal) in failing_keys]
        segment_evidence_all = [FindingEvidence(
            finding_type="segment_gap", facility_id=facility,
            period=key_from_ordinal(ordinal, resolved_scope.reporting_grain), segment=segment,
            reason="This segment-period cell is below the minimum and the required facility row is absent.",
        ) for segment, facility, ordinal in failing_missing]
        segment_evidence, _ = _bounded(segment_evidence_all, evidence_limit)
        affected_segments = {row["segment"] for row in failing_cells}
        rule_06 = _rule(
            "T2D6-06", outcome="PASS" if not failing_cells else "VIOLATION",
            issue_count=len(failing_cells),
            measure=min((row["coverage"] for row in cell_rows), default=None),
            floor=resolved_scope.continuity_floor,
            explanation=(f"Every assessable segment-period group received at least "
                         f"{resolved_scope.continuity_floor:.0%} of its required facility rows."
                         if not failing_cells else
                         f"{len(failing_cells):,} segment-period group(s) received fewer than "
                         f"{resolved_scope.continuity_floor:.0%} of their required facility rows."),
            total_findings=len(failing_missing), evidence=segment_evidence,
            affected={"assessable_facilities": len(stable_segments),
                      "unstable_facilities": len(unstable_segments),
                      "assessable_cells": len(cell_rows), "failing_cells": len(failing_cells)},
            metrics={"stable_segment_facilities": len(stable_segments),
                     "unstable_segment_facilities": len(unstable_segments),
                     "cell_results": [{key: value for key, value in row.items() if key != "ordinal"}
                                      for row in cell_rows[:aggregate_limit]],
                     "cell_results_truncated": len(cell_rows) > aggregate_limit},
        )

    rules = [rule_01, rule_02, rule_03, rule_04, rule_05, rule_06]
    assert tuple(rule.rule_id for rule in rules) == RULE_IDS
    if rule_specs is not None:
        titles = {spec["rule_id"]: spec["title"] for spec in rule_specs}
        if tuple(titles) != RULE_IDS:
            raise RowCompletenessInputError("frozen KB rules do not match the engine contract")
        rules = [rule.model_copy(update={"title": titles[rule.rule_id]}) for rule in rules]
    outcomes = {rule.outcome for rule in rules}
    if "VIOLATION" in outcomes:
        overall = "violation"
    elif outcomes & {"NO-VERDICT", "NOT-ASSESSABLE"}:
        overall = "inconclusive"
    elif outcomes == {"NOT-APPLICABLE"}:
        overall = "not_applicable"
    else:
        overall = "pass"

    invalid_facilities = {row["facility"] for row in invalid_rows if row["facility"] is not None}
    invalid_periods = {row["period_key"] for row in invalid_rows if row["period_key"] is not None}
    duplicate_facilities = {row["facility"] for row in duplicates}
    duplicate_period_labels = {row["period"] for row in duplicates}
    affected_facility_ids = facilities_with_gaps | duplicate_facilities | invalid_facilities
    affected_period_labels = ({key_from_ordinal(value, resolved_scope.reporting_grain)
                               for value in periods_with_gaps}
                              | {key_from_ordinal(value, resolved_scope.reporting_grain)
                                 for value in absent_calendar_ordinals}
                              | duplicate_period_labels | invalid_periods)
    required_ordinals = {ordinal for _, ordinal in required}
    uncovered_calendar_gaps = [ordinal for ordinal in absent_calendar_ordinals
                               if ordinal not in required_ordinals]
    issue_summary = {
        "invalid_key_rows": len(invalid_rows),
        "missing_reporting_periods": len(absent_calendar_ordinals),
        "calendar_gaps_without_required_pairs": len(uncovered_calendar_gaps),
        "missing_facility_period_pairs": len(missing_pairs),
        "duplicate_pairs": len(duplicates),
        "surplus_duplicate_rows": surplus_rows,
        "affected_facilities": len(affected_facility_ids),
        "affected_periods": len(affected_period_labels),
        "affected_segments": len(affected_segments),
        "primary_issue_instances": (len(invalid_rows) + len(uncovered_calendar_gaps)
                                    + len(missing_pairs) + surplus_rows),
    }
    return ReconciliationPayload(
        origin_run_id=origin_run_id,
        scope=resolved_scope,
        overall_verdict=overall,
        facilities_assessed=len(spans),
        periods_assessed=len(parseable_ordinals),
        required_facility_period_pairs=len(required),
        received_required_pairs=len(received_pairs & required),
        continuity_coverage=portfolio_coverage,
        issue_summary=issue_summary,
        rules=rules,
        calculation_inference_disclosure=disclosure,
        manifest_fingerprint=manifest_fingerprint,
        methodology_version=methodology_version,
        engine_version=engine_version,
        source_artifact_references=list(source_artifact_references),
    )
