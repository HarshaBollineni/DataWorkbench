"""Governed text and PDF reporting for Diagnostic #14."""
from __future__ import annotations

import re
from typing import Any

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from domains.aar.repository import AnalysisArtifactRepository
from domains.test_lab.shared.run_state import get_run


REPORT_ARTIFACT_TYPE = "population_stability_report"
REPORT_RENDERER_VERSION = "2"


def _workflow_state(finding: dict[str, Any] | None) -> str:
    if not finding:
        return "no_review_required"
    issue = finding.get("existing_issue") or {}
    if issue.get("status") == "Closed":
        return "closed"
    if issue or finding.get("review_state") == "confirmed":
        return "promoted"
    if finding.get("review_state") == "dismissed":
        return "dismissed"
    return "awaiting_review"


def _number(value: Any, digits: int = 4) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"


def _percent(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.2f}%"


def _population_text(count: Any, share: Any) -> str:
    text = f"{int(count or 0):,} rows"
    return f"{text} ({_percent(share)})" if share is not None else text


def _bin_label(value: Any) -> str:
    """Render numeric interval labels legibly with PDF-safe infinity text."""
    label = str(value or "-").replace("−∞", "-infinity").replace("-∞", "-infinity")
    label = label.replace("+∞", "+infinity").replace("∞", "+infinity")
    if not (label.startswith(("(", "[")) and "," in label):
        return label
    return re.sub(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", lambda match: (
        f"{float(match.group(0)):.2f}"
    ), label)


def _predicate_text(definition: dict[str, Any]) -> str:
    feature = definition.get("split_feature") or "selected field"
    expression = definition.get("expression") or {}
    operator = expression.get("operator")
    if operator == "in":
        criterion = f"{feature} is one of {', '.join(map(str, expression.get('values') or []))}"
    elif operator == "range":
        left = "inclusive" if expression.get("lower_inclusive", True) else "exclusive"
        right = "inclusive" if expression.get("upper_inclusive", True) else "exclusive"
        criterion = (f"{feature} is between {expression.get('lower')} ({left}) and "
                     f"{expression.get('upper')} ({right})")
    elif operator:
        criterion = f"{feature} {operator} {expression.get('value')}"
    else:
        criterion = "not recorded"
    return criterion


def _bin_method_text(method: str | None, kind: str | None, count: int) -> str:
    descriptions = {
        "baseline_quantiles": (
            "deterministic Baseline quantiles (decile candidates), producing approximately equal "
            "Baseline populations before the reviewed boundaries were frozen"
        ),
        "baseline_unique_values": "ordered Baseline values because fewer than ten distinct values were available",
        "baseline_only_deterministic_v1": "the deterministic target-free Baseline-only PSI contract",
        "diagnostic_2_target_aware_iv": "target-aware Diagnostic 2 IV/coarse-bin evidence fitted on Baseline",
        "reviewed_diagnostic_2_coarse_bins": "reviewed target-aware Diagnostic 2 coarse bins",
        "constant_baseline_exact_value_guard": "the exact Baseline constant value plus guard bins",
        "operator_defined_baseline_groups": "operator-reviewed categorical groups from complete Baseline values",
        "psi_numeric_operator_override": "a reviewed PSI-specific numeric override fitted to Baseline",
    }
    basis = descriptions.get(method, str(method or "a reviewed Baseline-derived definition").replace("_", " "))
    return f"{basis}; {count} {kind or 'feature'} bins were then applied unchanged to Current"


def _profile_summary(profile: dict[str, Any] | None) -> dict[str, Any]:
    value = profile or {}
    return {key: value.get(key) for key in (
        "data_type", "classification", "total_count", "regular_value_count",
        "non_null_count", "effective_missing_count", "null_count", "distinct_count",
        "min", "max", "mean", "stddev", "median", "q1", "q3", "percentiles", "top_k",
    )}


def report_payload(run_id: str) -> tuple[dict[str, Any], Any, bool]:
    """Build and retain the immutable source used by both report formats."""
    from .runner import run_results

    projected = run_results(run_id)
    run_row = get_run(run_id)
    manifest = run_row.get("manifest_json") or {}
    repo = AnalysisArtifactRepository()
    features: list[dict[str, Any]] = []
    source_refs: dict[str, str] = {}
    finding_actions: list[dict[str, Any]] = []
    classifications = {"investigate": 0, "watch": 0, "stable": 0}
    workflow_counts = {"awaiting_review": 0, "promoted": 0, "closed": 0, "dismissed": 0}
    summary_metrics: dict[str, Any] = {}

    for row in projected.get("results") or []:
        metrics = row.get("metrics_json") or {}
        if metrics.get("result_kind") == "psi_run_summary":
            summary_metrics = metrics
            continue
        if metrics.get("result_kind") != "psi_feature":
            continue
        artifact_id = metrics.get("artifact_id")
        profile_id = metrics.get("profile_artifact_id")
        if artifact_id:
            source_refs[artifact_id] = "psi_evidence"
        if profile_id:
            source_refs[profile_id] = "baseline_feature_profile"
        bin_method, bin_kind = None, None
        try:
            _bin_meta, bin_definition = repo.get(metrics.get("bin_artifact_id"))
            bin_method = bin_definition.get("creation_methodology")
            bin_kind = bin_definition.get("kind") or bin_definition.get("logical_type")
        except (KeyError, OSError, TypeError):
            bin_definition = {}
        findings = row.get("findings") or []
        finding = findings[0] if findings else None
        state = _workflow_state(finding)
        if state in workflow_counts:
            workflow_counts[state] += 1
        classification = metrics.get("classification")
        if classification in classifications:
            classifications[classification] += 1
        recommendation = (
            "promotion recommended" if classification == "investigate"
            else "review and consider" if classification == "watch"
            else "promotion not recommended"
        )
        for finding_row in findings:
            dispositions = finding_row.get("dispositions") or []
            issue = finding_row.get("existing_issue") or {}
            finding_actions.append({
                "feature": metrics.get("feature"),
                "finding_id": finding_row.get("finding_id"),
                "state": _workflow_state(finding_row),
                "recommendation": recommendation,
                "action": dispositions[-1].get("action") if dispositions else None,
                "rationale": dispositions[-1].get("reason") if dispositions else None,
                "issue_row_id": issue.get("issue_row_id"),
                "issue_status": issue.get("status"),
            })
        features.append({
            "feature": metrics.get("feature"), "psi": metrics.get("psi"),
            "classification": classification, "review_state": state,
            "recommendation": recommendation,
            "baseline_count": metrics.get("baseline_count"),
            "current_count": metrics.get("current_count"),
            "thresholds": metrics.get("thresholds") or manifest.get("thresholds") or {},
            "profile_artifact_id": profile_id,
            "profile": _profile_summary(metrics.get("data_profile")),
            "bin_artifact_id": metrics.get("bin_artifact_id"),
            "psi_artifact_id": artifact_id,
            "binning_method": bin_method,
            "binning_basis": _bin_method_text(
                bin_method, bin_kind, len(metrics.get("bins") or [])),
            "bins": [{**bin_row, "display_label": _bin_label(
                bin_row.get("bin_label") or bin_row.get("bin"))}
                for bin_row in metrics.get("bins") or []],
        })

    if not source_refs:
        raise RuntimeError("a PSI report requires retained feature evidence")
    definition = manifest.get("population_definition") or {}
    preview = manifest.get("population_preview") or {}
    first_feature = features[0] if features else {}
    baseline_count = preview.get("baseline_count", first_feature.get("baseline_count"))
    current_count = preview.get("current_count", first_feature.get("current_count"))
    total_count = preview.get("total_count")
    if total_count is None:
        total_count = (baseline_count or 0) + (current_count or 0) + (preview.get("excluded_count") or 0)
    method = definition.get("method")
    if method == "split_snapshot":
        population_explanation = (
            f"This run split one source snapshot using '{_predicate_text(definition)}'. Rows satisfying "
            "the criterion formed Baseline and the remaining eligible rows formed Current. "
            f"Null values were assigned to {definition.get('null_policy') or 'Baseline'} and declared "
            f"special values were assigned to {definition.get('special_policy') or 'excluded'}."
        )
    else:
        population_explanation = (
            "This run compared two governed snapshots. The complete Baseline snapshot supplied the "
            "reference distributions and reviewed bin definitions; those definitions were applied "
            "unchanged to the Current snapshot."
        )
    binning_explanation = (
        "For each feature, the Baseline distribution establishes the reviewed bins. Target-free numeric "
        "features normally use deterministic Baseline quantile boundaries, giving approximately equal "
        "Baseline counts when sufficient distinct values exist. Categorical features retain frequent "
        "Baseline values or reviewed groups. Once approved, bin boundaries and groups are frozen: Current "
        "observations are assigned to those same bins, so Current bin counts are allowed to differ. Missing, "
        "special, unseen, underflow, and overflow observations remain visible in explicit guard bins."
    )
    report = {
        "artifact_kind": REPORT_ARTIFACT_TYPE, "schema_version": 1,
        "report_id": f"rpt_{run_id}", "run_id": run_id,
        "generated_at": run_row.get("finished_at") or db.now_ist(),
        "executor": manifest.get("frozen_by") or manifest.get("created_by") or "system",
        "analysis_overview": [
            ("Population Stability Index (PSI) measures how much a feature's distribution moved between "
             "a reference Baseline population and a comparison Current population. Each feature is divided "
             "into common bins. The report compares the Baseline and Current share in every bin, calculates "
             "a contribution from the difference in those shares and their logarithmic ratio, and sums the "
             "contributions to obtain the feature-level PSI. A small epsilon is used only to keep empty-bin "
             "calculations finite."),
            binning_explanation,
            population_explanation,
            ("The watch and investigate boundaries classify the magnitude of movement. They are review "
             "signals, not automatic pass/fail decisions: business context is still required before evidence "
             "is promoted to an issue."),
        ],
        "introduction": " ".join((
            "Population Stability Index compares Baseline and Current feature distributions using reviewed frozen bins.",
            population_explanation,
        )),
        "scope": {
            "item_id": run_row.get("item_id"), "table": manifest.get("table"),
            "baseline_table": manifest.get("baseline_table") or manifest.get("table"),
            "current_table": manifest.get("current_table") or manifest.get("table"),
            "baseline_snapshot_id": ((manifest.get("baseline") or {}).get("snapshot") or {}).get("snapshot_id"),
            "current_snapshot_id": ((manifest.get("current") or {}).get("snapshot") or {}).get("snapshot_id"),
            "population_method": method,
            "population_definition": definition,
            "split_criterion": _predicate_text(definition) if method == "split_snapshot" else None,
            "null_policy": definition.get("null_policy"),
            "special_policy": definition.get("special_policy"),
            "baseline_count": baseline_count, "current_count": current_count,
            "total_count": total_count, "excluded_count": preview.get("excluded_count", 0),
            "null_count": preview.get("null_count"), "special_count": preview.get("special_count"),
            "baseline_share": preview.get("baseline_share"),
            "current_share": preview.get("current_share"),
        },
        "summary": {
            "features_selected": len(manifest.get("selected_features") or []),
            "features_completed": len(features),
            "features_failed": (summary_metrics.get("rollup") or {}).get("failed", 0),
            "drift_candidates": classifications["investigate"] + classifications["watch"],
            "classifications": classifications,
            "awaiting_review": workflow_counts["awaiting_review"],
            "issues_promoted": workflow_counts["promoted"],
            "issues_closed": workflow_counts["closed"],
            "findings_dismissed": workflow_counts["dismissed"],
        },
        "features": features, "finding_actions": finding_actions,
        "decision_actions": projected.get("decisions") or [],
        "thresholds": manifest.get("thresholds") or {},
        "threshold_sources": manifest.get("threshold_sources") or {},
        "epsilon": manifest.get("epsilon"),
        "methodology": manifest.get("methodology_version"),
        "limitations": [
            "PSI identifies distribution movement; it does not determine whether the movement is harmful.",
            "PSI magnitude depends on the reviewed bin definition and the sizes of both populations.",
            "The feature profile describes the retained Baseline snapshot profile, while bin rows compare Baseline with Current.",
            "Issue status and recorded rationales are reported as of report generation time.",
        ],
        "source_artifact_ids": list(source_refs),
    }

    metadata = [repo.get_metadata(value) for value in source_refs]
    identity_inputs = {
        "run_id": run_id, "renderer_version": REPORT_RENDERER_VERSION,
        "source_hashes": [value.payload_hash for value in metadata],
        "finding_actions_hash": stable_fingerprint(finding_actions),
        "decision_actions_hash": stable_fingerprint(report["decision_actions"]),
    }
    baseline = (manifest.get("baseline") or {}).get("snapshot") or {}
    current = (manifest.get("current") or {}).get("snapshot") or {}
    saved = repo.save(
        report, artifact_type=REPORT_ARTIFACT_TYPE,
        asset_id=baseline.get("asset_id") or metadata[0].asset_id,
        snapshot_id=baseline.get("snapshot_id") or metadata[0].snapshot_id,
        comparison_snapshot_id=current.get("snapshot_id") or metadata[0].comparison_snapshot_id,
        population_fingerprint=manifest.get("population_fingerprint") or metadata[0].population_fingerprint,
        target_fingerprint=None, feature=None,
        methodology_fingerprint=stable_fingerprint(identity_inputs),
        scope="diagnostic_local", owner_id="diagnostic:14", table=manifest.get("table"),
        features=tuple(sorted(str(row["feature"]) for row in features)),
        source_artifacts=tuple({"artifact_id": artifact_id, "role": role}
                               for artifact_id, role in source_refs.items()),
        identity_inputs=identity_inputs, run_id=run_id,
        created_by=manifest.get("frozen_by") or "system",
    )
    return report, saved.artifact, saved.outcome == "reused"


def render_text(payload: dict[str, Any]) -> str:
    summary, scope = payload["summary"], payload["scope"]
    classes = summary["classifications"]
    lines = ["POPULATION STABILITY INDEX ANALYSIS REPORT", "", "1. ANALYSIS OVERVIEW"]
    for paragraph in payload.get("analysis_overview") or [payload["introduction"]]:
        lines.extend([paragraph, ""])
    lines.extend(["Execution details",
             f"- Report timestamp: {payload['generated_at']}", f"- Executor: {payload['executor']}",
             f"- Run ID: {payload['run_id']}", f"- Table: {scope.get('table')}",
             f"- Baseline snapshot: {scope.get('baseline_snapshot_id')}",
             f"- Current snapshot: {scope.get('current_snapshot_id')}",
             f"- Baseline table: {scope.get('baseline_table')}",
             f"- Current table: {scope.get('current_table')}",
             f"- Population method: {scope.get('population_method')}",
             f"- Split criterion: {scope.get('split_criterion') or 'Not applicable - separate snapshots compared'}",
             f"- Null / special-value allocation: {scope.get('null_policy') or 'snapshot-defined'} / "
             f"{scope.get('special_policy') or 'snapshot-defined'}",
             f"- Baseline population: {_population_text(scope.get('baseline_count'), scope.get('baseline_share'))}",
             f"- Current population: {_population_text(scope.get('current_count'), scope.get('current_share'))}",
             f"- Excluded from comparison: {scope.get('excluded_count') or 0} rows",
             f"- Source total: {scope.get('total_count') or 0} rows; null: {scope.get('null_count') or 0}; "
             f"declared special: {scope.get('special_count') or 0}", "", "Thresholds",
             "| Watch | Investigate | Epsilon |", "|---:|---:|---:|",
             f"| {payload['thresholds'].get('watch')} | {payload['thresholds'].get('investigate')} | {payload.get('epsilon')} |",
             "", "2. OUTCOME SUMMARY",
             "| Completed | Failed | Investigate | Watch | Stable | Awaiting | Promoted | Closed |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|",
             f"| {summary['features_completed']} | {summary['features_failed']} | {classes['investigate']} | "
             f"{classes['watch']} | {classes['stable']} | {summary['awaiting_review']} | "
             f"{summary['issues_promoted']} | {summary['issues_closed']} |", "", "3. FEATURE EVIDENCE"])
    for feature in payload["features"]:
        profile = feature.get("profile") or {}
        regular = profile.get("regular_value_count")
        missing = profile.get("effective_missing_count")
        lines.extend(["", f"### {feature['feature']}",
                      f"Issue decision: {feature['recommendation']} ({feature['review_state']})",
                      f"PSI {_number(feature.get('psi'))} | Classification {feature.get('classification')} | "
                      f"Baseline {feature.get('baseline_count') or 0} | Current {feature.get('current_count') or 0}",
                      f"Binning basis: {feature.get('binning_basis')}",
                      "", "Baseline feature profile",
                      f"- Type: {profile.get('classification') or profile.get('data_type') or '-'}",
                      f"- Rows: {profile.get('total_count') or 0}; regular: "
                      f"{regular if regular is not None else profile.get('non_null_count') or 0}; missing: "
                      f"{missing if missing is not None else profile.get('null_count') or 0}; distinct: {profile.get('distinct_count') or 0}",
                      f"- Min: {_number(profile.get('min'), 2)}; max: {_number(profile.get('max'), 2)}; "
                      f"mean: {_number(profile.get('mean'), 2)}; std. deviation: {_number(profile.get('stddev'), 2)}",
                      "", "Bin contributions",
                      "| Bin | Baseline | Current | Population profile | Contribution |",
                      "|---|---:|---:|---|---:|"])
        percentiles = profile.get("percentiles") or {}
        top_values = profile.get("top_k") or {}
        if percentiles:
            lines.insert(len(lines) - 3, "- Percentiles: " + "; ".join(
                f"{key}: {_number(value, 2)}" for key, value in list(percentiles.items())[:8]))
        if top_values:
            lines.insert(len(lines) - 3, "- Top values: " + "; ".join(
                f"{key}: {value}" for key, value in list(top_values.items())[:8]))
        for row in feature.get("bins") or []:
            lines.append(f"| {row.get('display_label') or _bin_label(row.get('bin_label') or row.get('bin'))} | "
                         f"{row.get('baseline_count') or 0} ({_percent(row.get('baseline_proportion'))}) | "
                         f"{row.get('current_count') or 0} ({_percent(row.get('current_proportion'))}) | "
                         f"{_percent(row.get('baseline_proportion'))} -> {_percent(row.get('current_proportion'))} | "
                         f"{_number(row.get('contribution'), 6)} |")
        lines.append(f"Artifacts: profile={feature.get('profile_artifact_id') or '-'}; "
                     f"bins={feature.get('bin_artifact_id') or '-'}; PSI={feature.get('psi_artifact_id') or '-'}")
        lines.extend(["", "------------------------------------------------------------"])
    lines.extend(["", "4. FINDINGS AND ISSUES"])
    if payload["finding_actions"]:
        for action in payload["finding_actions"]:
            lines.append(f"- {action['feature']}: {action['recommendation']}; {action['state']}; "
                         f"issue {action.get('issue_row_id') or '-'}; rationale {action.get('rationale') or '-'}")
    else:
        lines.append("- No feature finding required a user decision.")
    lines.extend(["", "5. INTERPRETATION LIMITS"])
    lines.extend(f"- {value}" for value in payload["limitations"])
    return "\n".join(lines)


def render_pdf(payload: dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError:  # pragma: no cover
        return render_text(payload).encode("utf-8")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(12, 12, 12); pdf.set_auto_page_break(auto=True, margin=12); pdf.add_page()

    def safe(value: Any) -> str:
        return ("-" if value is None else str(value)).encode("latin-1", "replace").decode("latin-1")

    def section(title: str) -> None:
        pdf.ln(2); pdf.set_fill_color(240, 253, 250); pdf.set_text_color(15, 118, 110)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(0, 7, safe(title), fill=True, new_x="LMARGIN", new_y="NEXT"); pdf.ln(1)

    def paragraph(value: Any, *, bold: bool = False) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 8.2); pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(0, 4.2, safe(value), new_x="LMARGIN", new_y="NEXT")

    def feature_heading(index: int, total: int, title: str) -> None:
        if pdf.get_y() > pdf.h - 45:
            pdf.add_page()
        pdf.ln(3); pdf.set_fill_color(204, 251, 241); pdf.set_text_color(15, 118, 110)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, safe(f"Feature {index} of {total} - {title}"), fill=True,
                 new_x="LMARGIN", new_y="NEXT"); pdf.ln(1)

    def feature_separator() -> None:
        pdf.ln(2); pdf.set_draw_color(13, 148, 136); pdf.set_line_width(0.6)
        pdf.line(12, pdf.get_y(), 198, pdf.get_y()); pdf.ln(5)

    def wraps(value: Any, width: float) -> list[str]:
        words = safe(value).split(); output, current = [], ""
        if not words: return ["-"]
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and pdf.get_string_width(candidate) > width - 3:
                output.append(current); current = word
            else:
                current = candidate
        return [*output, current]

    def table(headers: list[str], rows: list[list[Any]], widths: list[float],
              fill: tuple[int, int, int] | None = None) -> None:
        line_height = 3.8
        def draw(values: list[Any], header: bool = False) -> None:
            pdf.set_font("Helvetica", "B" if header else "", 7)
            cells = [wraps(value, width) for value, width in zip(values, widths)]
            height = max(len(value) for value in cells) * line_height + 1.4
            if pdf.get_y() + height > pdf.h - 12: pdf.add_page()
            start_x, start_y = pdf.get_x(), pdf.get_y()
            for index, (cell, width) in enumerate(zip(cells, widths)):
                x = start_x + sum(widths[:index]); pdf.set_xy(x, start_y)
                pdf.set_fill_color(*((226, 232, 240) if header else fill or (255, 255, 255)))
                pdf.rect(x, start_y, width, height, style="DF"); pdf.set_xy(x + 1.1, start_y + .7)
                pdf.multi_cell(width - 2.2, line_height, "\n".join(cell))
            pdf.set_xy(start_x, start_y + height)
        draw(headers, True)
        for row in rows: draw(row)
        pdf.ln(1.5)

    summary, scope = payload["summary"], payload["scope"]
    classes = summary["classifications"]
    pdf.set_text_color(15, 23, 42); pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Population Stability Index Analysis", new_x="LMARGIN", new_y="NEXT")
    paragraph("Governed contextual diagnostic report", bold=True)
    section("1. Analysis overview")
    for value in payload.get("analysis_overview") or [payload["introduction"]]:
        paragraph(value); pdf.ln(1)
    execution_rows = [
        ["Report timestamp", payload["generated_at"]], ["Executor", payload["executor"]],
        ["Run ID", payload["run_id"]], ["Baseline snapshot", scope.get("baseline_snapshot_id")],
        ["Current snapshot", scope.get("current_snapshot_id")],
        ["Baseline table", scope.get("baseline_table")], ["Current table", scope.get("current_table")],
        ["Population method", scope.get("population_method")],
        ["Split criterion", scope.get("split_criterion") or "Not applicable - separate snapshots compared"],
        ["Null / special allocation", f"{scope.get('null_policy') or 'snapshot-defined'} / "
         f"{scope.get('special_policy') or 'snapshot-defined'}"],
        ["Baseline population", f"{scope.get('baseline_count') or 0} rows"
         + (f" ({_percent(scope.get('baseline_share'))})" if scope.get("baseline_share") is not None else "")],
        ["Current population", f"{scope.get('current_count') or 0} rows"
         + (f" ({_percent(scope.get('current_share'))})" if scope.get("current_share") is not None else "")],
        ["Excluded rows", scope.get("excluded_count") or 0],
        ["Source / null / special rows", f"{scope.get('total_count') or 0} / "
         f"{scope.get('null_count') or 0} / {scope.get('special_count') or 0}"],
    ]
    table(["Execution detail", "Value"], [
        *execution_rows,
    ], [48, 138])
    table(["Watch", "Investigate", "Epsilon", "Methodology"], [[
        payload["thresholds"].get("watch"), payload["thresholds"].get("investigate"),
        payload.get("epsilon"), payload.get("methodology"),
    ]], [32, 35, 32, 87])
    section("2. Outcome summary")
    table(["Completed", "Failed", "Investigate", "Watch", "Stable", "Awaiting", "Promoted", "Closed"], [[
        summary["features_completed"], summary["features_failed"], classes["investigate"], classes["watch"],
        classes["stable"], summary["awaiting_review"], summary["issues_promoted"], summary["issues_closed"],
    ]], [23.25] * 8)
    section("3. Feature evidence")
    total_features = len(payload["features"])
    for index, feature in enumerate(payload["features"], 1):
        profile = feature.get("profile") or {}
        regular = profile.get("regular_value_count")
        missing = profile.get("effective_missing_count")
        feature_heading(index, total_features, feature["feature"])
        table(["Issue decision", "PSI", "Classification", "Baseline", "Current"], [[
            f"{feature['recommendation']} ({feature['review_state']})", _number(feature.get("psi")),
            feature.get("classification"), feature.get("baseline_count"), feature.get("current_count"),
        ]], [66, 26, 34, 30, 30], fill=(255, 251, 235))
        paragraph(f"Binning basis: {feature.get('binning_basis')}")
        paragraph("Baseline feature profile", bold=True)
        table(["Type", "Rows", "Regular", "Missing", "Distinct", "Min", "Max", "Mean", "Std. dev."], [[
            profile.get("classification") or profile.get("data_type"), profile.get("total_count"),
            regular if regular is not None else profile.get("non_null_count"),
            missing if missing is not None else profile.get("null_count"), profile.get("distinct_count"),
            _number(profile.get("min"), 2), _number(profile.get("max"), 2),
            _number(profile.get("mean"), 2), _number(profile.get("stddev"), 2),
        ]], [24, 18, 20, 20, 19, 21, 21, 21, 22])
        top_values = profile.get("top_k") or {}
        percentiles = profile.get("percentiles") or {}
        if percentiles:
            paragraph("Percentiles: " + "; ".join(
                f"{key}: {_number(value, 2)}" for key, value in list(percentiles.items())[:8]))
        if top_values:
            paragraph("Top values: " + "; ".join(f"{key}: {value}" for key, value in list(top_values.items())[:8]))
        paragraph("Bin contributions", bold=True)
        table(["Bin", "Baseline", "Current", "Population profile", "Contribution"], [[
            row.get("display_label") or _bin_label(row.get("bin_label") or row.get("bin")),
            f"{row.get('baseline_count') or 0} ({_percent(row.get('baseline_proportion'))})",
            f"{row.get('current_count') or 0} ({_percent(row.get('current_proportion'))})",
            f"{_percent(row.get('baseline_proportion'))} -> {_percent(row.get('current_proportion'))}",
            _number(row.get("contribution"), 6),
        ] for row in feature.get("bins") or []], [58, 34, 34, 38, 22])
        paragraph(f"Artifacts: profile={feature.get('profile_artifact_id') or '-'}; "
                  f"bins={feature.get('bin_artifact_id') or '-'}; PSI={feature.get('psi_artifact_id') or '-'}")
        feature_separator()
    section("4. Findings and issues")
    if payload["finding_actions"]:
        table(["Feature", "Recommendation", "State", "Issue", "Rationale"], [[
            row["feature"], row["recommendation"], row["state"], row.get("issue_row_id") or "-",
            row.get("rationale") or "Awaiting a recorded disposition",
        ] for row in payload["finding_actions"]], [34, 42, 30, 34, 46])
    else:
        paragraph("No feature finding required a user decision.")
    section("5. Interpretation limits")
    for value in payload["limitations"]: paragraph(f"- {value}")
    return bytes(pdf.output())


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    payload, artifact, reused = report_payload(run_id)
    return render_pdf(payload), {
        "run_id": run_id,
        "report_artifact": {"artifact_id": artifact.artifact_id,
                            "artifact_type": artifact.artifact_type,
                            "payload_hash": artifact.payload_hash, "reused": reused},
        "filename": f"population-stability-{run_id}.pdf",
    }
