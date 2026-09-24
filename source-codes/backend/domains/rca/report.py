"""Conclusion-led completion report, rendered exclusively from retained evidence."""
from __future__ import annotations

from datetime import datetime, timezone

from fpdf import FPDF
from fpdf.fonts import FontFace

from ai.report_pdf import _ascii
from domains.rca import progress
from domains.rca.presentation import present_execution
from domains.rca.report_content import (
    ArtifactIndex, cell_value, elapsed, hypothesis_groups, interpretation_for,
    label, prose, run_status, technical_column, time_label, timeline,
)


class _Report(FPDF):
    def footer(self):
        self.set_y(-13)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(100, 116, 139)
        self.cell(0, 5, f"Aegis Labs | RCA completion report | {self.page_no()}/{{nb}}", align="R")


class _Writer:
    """Common PDF/text layout with bounded tables and repeated page headers."""
    def __init__(self):
        self.pdf = _Report(format="A4")
        self.pdf.set_margins(12, 12, 12)
        self.pdf.set_auto_page_break(True, 20)
        self.pdf.alias_nb_pages()
        self.pdf.add_page()
        self.text = []

    def heading(self, title, *, page=False):
        pdf = self.pdf
        if page or pdf.will_page_break(26):
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(240, 253, 250)
        pdf.set_text_color(15, 118, 110)
        pdf.multi_cell(0, 8, _ascii(title), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        self.text.append(title)

    def paragraph(self, title, value, *, highlight=False):
        if value is None or value == "" or value == []:
            return
        pdf = self.pdf
        if pdf.will_page_break(18):
            pdf.add_page()
        self.text.extend([title, prose(value)])
        pdf.set_text_color(30, 41, 59)
        if title:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.multi_cell(0, 5.5, _ascii(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B" if highlight else "", 10 if highlight else 9)
        pdf.set_fill_color(248, 250, 252)
        pdf.multi_cell(0, 5, _ascii(prose(value)), fill=highlight, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    def table(self, headers, rows, widths, *, numeric=()):
        if not rows:
            return
        pdf = self.pdf
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(30, 41, 59)
        pdf.set_draw_color(203, 213, 225)
        clipped = False
        with pdf.table(col_widths=widths, line_height=4.5, padding=1.7,
                       text_align="LEFT", v_align="TOP", repeat_headings=1,
                       headings_style=FontFace(emphasis="B", fill_color=(226, 232, 240)),
                       cell_fill_color=(248, 250, 252), cell_fill_mode="ROWS") as table:
            for index, values in enumerate([headers, *rows]):
                row = table.row()
                # Full values remain in text; PDF cells use explicit excerpts.
                self.text.append(" | ".join(prose(value) for value in values))
                for column, value in enumerate(values):
                    value = prose(value)
                    if len(value) > 360:
                        value = value[:360].rstrip() + "... [excerpt]"
                        clipped = True
                    row.cell(_ascii(value), align="RIGHT" if index and column in numeric else "LEFT")
        pdf.ln(3)
        if clipped:
            self.paragraph("Table note", "Cells marked [excerpt] are shortened for readability. Complete values remain in the retained evidence and text report.")


def _result(execution):
    summary = execution.get("summary_json") or {}
    return summary.get("result") or (summary.get("runtime") or {}).get("result") or {}


def _evidence_table(writer, result):
    all_rows = [row for row in result.get("evidence_rows") or [] if isinstance(row, dict)]
    rows = all_rows[:12]
    keys = list(dict.fromkeys(key for row in rows for key in row if not technical_column(key)))
    if not keys:
        return
    # Wide results become linked panels instead of shrinking the font to fit.
    identifiers = [key for key in keys if any(isinstance(row.get(key), str) for row in rows)][:2]
    values = [key for key in keys if key not in identifiers]
    size = 5 - max(1, len(identifiers))
    panels = [values[index:index + size] for index in range(0, len(values), size)] or [[]]
    for index, panel in enumerate(panels):
        columns = identifiers + panel
        headers = ([] if identifiers else ["Row"]) + [label(key) for key in columns]
        rendered = [([] if identifiers else [str(number)]) + [cell_value(row.get(key), key) for key in columns]
                    for number, row in enumerate(rows, 1)]
        numeric = [offset + (0 if identifiers else 1) for offset, key in enumerate(columns)
                   if all(row.get(key) is None or isinstance(row.get(key), (int, float)) for row in rows)]
        if len(panels) > 1:
            writer.paragraph(f"Evidence table {index + 1} of {len(panels)}", "Same retained rows; complementary measurements.")
        writer.table(headers, rendered, [1] * len(headers), numeric=numeric)
    if len(all_rows) > 12 or (result.get("truncation") or {}).get("evidence_rows_truncated"):
        writer.paragraph("Evidence coverage", "Showing the first 12 retained rows at most, not the full population. Full outputs, where retained, are indexed in the artifact appendix.")


def render_report(case: dict, fmt: str = "pdf") -> bytes:
    """No model calls, dataset access, persistence changes or inferred causal verdicts."""
    closure = case.get("closure") or {}
    if case.get("state") != "closed" or not closure:
        raise ValueError("The RCA completion report is available after workflow closure.")
    if fmt not in {"pdf", "text"}:
        raise ValueError("Report format must be pdf or text.")
    conclusion = case.get("conclusion") or {}
    checklist = (case.get("case_file") or {}).get("checklist_json") or {}
    events = case.get("aar_evidence") or []
    executions = case.get("executions") or {}
    groups = hypothesis_groups(case)
    artifacts = ArtifactIndex(events)
    writer = _Writer()
    pdf = writer.pdf
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 11, "Root Cause Analysis", new_x="LMARGIN", new_y="NEXT")
    writer.text.append("Root Cause Analysis")
    writer.paragraph("", f"Aegis Labs | {checklist.get('test_name') or 'Diagnostic investigation'}")

    writer.heading("1. Problem and conclusion")
    scope = conclusion.get("affected_scope") or checklist.get("table_name") or case.get("table_name") or "Scope not retained"
    writer.paragraph("Investigation objective", case.get("complaint_text") or
                     "Explain the reported diagnostic finding and determine whether retained evidence supports a root cause.")
    writer.paragraph("Affected scope", scope)
    observed = [(name, checklist.get(key)) for name, key in
                (("Reported metric", "metric"), ("Threshold", "threshold"), ("Reported violation count", "violation_count"))
                if checklist.get(key) is not None]
    if observed:
        writer.table(["Reported problem", "Retained value"], [(name, cell_value(value)) for name, value in observed], (60, 126))
    writer.paragraph("Approved outcome", conclusion.get("outcome_label") or label(closure.get("outcome")))
    writer.paragraph("Root-cause conclusion", conclusion.get("root_cause") or
        ("No root cause was established." if closure.get("outcome") == "unresolved" else
         "The detailed approved conclusion was not retained in this legacy record."), highlight=True)
    writer.paragraph("Why this conclusion", conclusion.get("approval_rationale") or "Supporting approval rationale was not retained.")
    writer.paragraph("Confidence", conclusion.get("confidence") or "Not recorded")
    writer.paragraph("Limitations / contradicting evidence", conclusion.get("limiting_evidence") or "No explicit limitation statement was retained.")
    writer.paragraph("How to read this outcome", "The approved conclusion is a human decision, not an additional causal test. Read it alongside the test assessments and limitations below. Closure does not confirm remediation; no analysis was rerun for this report.")
    approved_refs = [ref for ref in conclusion.get("supporting_evidence_ids") or [] if ref not in artifacts.excluded]
    artifacts.refs(approved_refs)
    if approved_refs:
        writer.paragraph("Evidence trail", f"{len(set(approved_refs))} non-telemetry references accompany the approval. Readable evidence references and additional artifacts are indexed in the appendix.")

    writer.heading("2. Hypotheses and findings")
    investigated = [group for group in groups if group["number"] is not None]
    if not investigated:
        writer.paragraph("Availability", "No linked hypothesis investigations were retained. Available analysis and the approved conclusion are shown separately.")
    look_labels = {}
    for group in investigated:
        number = group["number"]
        writer.paragraph(f"Hypothesis {number}", group.get("statement") or "Statement not retained", highlight=True)
        rows = []
        for index, look in enumerate(group["looks"], 1):
            fork = look.get("fork_json") or {}
            name = f"{number}.{index}"
            look_labels[look["look_id"]] = name
            stage = "Confirmation" if fork.get("combined_parent_look_id") else "Discovery + confirmation" if fork.get("combined_run_state") else "Discovery" if fork.get("kind") == "agent_driver_search" else "Hypothesis test"
            assessment = interpretation_for(look, events)
            rows.append([name, stage, run_status(look, executions, events), assessment.get("rationale") or "See the retained evidence; no assessment was recorded."])
        writer.table(["Test", "Investigation", "Assessment", "Reason"], rows, (14, 37, 34, 101))
        if not rows:
            writer.paragraph("Assessment", "Not run. This proposed explanation is not a tested finding.")
        contexts = [e for e in events if e.get("evidence_kind") == "human_context"
                    and (e.get("details") or {}).get("hypothesis_id") == group["hypothesis_id"]]
        for event in contexts:
            writer.paragraph(f"User context [{artifacts.add(event.get('artifact_id')) or 'reference unavailable'}]", (event.get("details") or {}).get("comment"))
    untested = [group for group in groups if group["number"] is None]
    if untested:
        writer.paragraph("Other proposed explanations (not investigated)", [group.get("statement") or "Statement not retained" for group in untested])
    writer.paragraph("Alternatives considered at approval", conclusion.get("alternatives_considered"))
    investigated_ids = {group["hypothesis_id"] for group in investigated}
    for event in events:
        if event.get("evidence_kind") == "human_context" and (event.get("details") or {}).get("hypothesis_id") not in investigated_ids:
            writer.paragraph("Case context supplied by the user", (event.get("details") or {}).get("comment"))

    writer.heading("3. Supporting evidence")
    if not executions:
        writer.paragraph("Availability", "No analysis executions were retained. Refer to the approved rationale and artifact appendix.")
    for look in case.get("looks") or []:
        execution = executions.get(look.get("look_id"))
        if not execution:
            continue
        fork = look.get("fork_json") or {}
        summary = execution.get("summary_json") or {}
        runtime = summary.get("runtime") or {}
        result = _result(execution)
        title = "Test " + look_labels[look["look_id"]] if look["look_id"] in look_labels else "Background evidence"
        reference = artifacts.add(execution.get("execution_id"), f"{title} - retained analysis result", "Investigation and decisions", execution.get("status"))
        writer.paragraph(f"{title} [{reference or 'reference unavailable'}]", (fork.get("plan") or {}).get("question") or label(look.get("sql_or_helper_ref")))
        if runtime.get("ok") is False or execution.get("status") in {"failed", "cancelled"}:
            writer.paragraph("No accepted analytical finding", runtime.get("error") or runtime.get("errors") or label(execution.get("status")))
            continue
        assessment = interpretation_for(look, events)
        writer.paragraph("Finding and its meaning", assessment.get("rationale") or result.get("summary") or "No narrative finding was retained.")
        writer.paragraph("Supporting observations", assessment.get("evidence_points"))
        writer.paragraph("Limits of this finding", assessment.get("limitations"))
        metrics = []
        for metric in present_execution(execution)["metrics"]:
            def formatted(value):
                return f"{value:.2%}" if metric["unit"] == "ratio" else cell_value(value)
            comparison = metric.get("comparison")
            metrics.append([metric["label"], f"{comparison['label']}: {formatted(comparison['value'])}" if comparison else "Not applicable",
                            f"{metric.get('value_label', 'Observed')}: {formatted(metric['value'])}"])
        writer.table(["Measurement", "Comparison", "Result"], metrics, (70, 58, 58))
        _evidence_table(writer, result)
        full_output = artifacts.add(result.get("download_artifact_id"), "Full analysis output", "Generated code and full outputs")
        if full_output:
            writer.paragraph("Additional output", f"See {full_output} in the artifact appendix.")

    writer.heading("4. Next steps and limitations")
    assessed_runs = [interpretation_for(look, events) for look in case.get("looks") or []
                     if not (look.get("fork_json") or {}).get("combined_parent_look_id")
                     and interpretation_for(look, events)]
    writer.paragraph("Last recorded follow-up question", (assessed_runs[-1] if assessed_runs else {}).get("next_question"))
    writer.paragraph("Related failures", conclusion.get("related_failures"))
    writer.paragraph("Recorded action / recommendation", conclusion.get("next_steps") or conclusion.get("recommended_action") or
                     "No explicit action plan was retained with the approval. This report does not create a remediation commitment.")
    writer.paragraph("Recorded owner", conclusion.get("owner") or "Not assigned in the retained conclusion")

    writer.heading("5. RCA closure timeline")
    started, milestones = timeline(case, groups)
    closed = closure.get("closed_at") or case.get("closed_at")
    writer.paragraph("Elapsed time to closure", elapsed(started, closed))
    writer.paragraph("Timeline basis", "Current-generation milestones only. Zoned timestamps are shown in UTC. Missing timestamps are explicit; elapsed time is wall-clock time, not model execution time.")
    writer.table(["When", "Milestone", "Outcome / decision", "Recorded by"],
                 [[time_label(row["at"]), row["milestone"], row["detail"], row["actor"] or "Not retained"] for row in milestones], (39, 43, 76, 28))

    turns = (case.get("data_chat") or {}).get("turns") or []
    if turns:
        writer.heading("Appendix A. Additional data-chat evidence", page=True)
        writer.paragraph("Purpose", "Supplementary questions and answers; chat does not replace the approved conclusion or the planned hypothesis assessments.")
        for index, turn in enumerate(turns, 1):
            writer.paragraph(f"Question {index}", turn.get("question"))
            writer.paragraph(f"Answer ({label(turn.get('status'))})", turn.get("answer") or "No answer was retained.")
            writer.paragraph("Limitations", turn.get("limitations"))
            writer.paragraph("Evidence references", artifacts.refs(turn.get("evidence_references")))
            artifacts.add((turn.get("execution") or {}).get("download_artifact_id"), "Full chat output", "Generated code and full outputs")

    writer.heading(f"Appendix {'B' if turns else 'A'}. Artifact index", page=True)
    writer.paragraph("How to retrieve supporting material", "Use these references in the retained RCA activity record / Analysis Artifact Repository (AAR). The index includes additional analysis, code and chat records where retained. Full payloads, code and integrity fingerprints remain in the AAR; they are not reproduced here. Internal timing checkpoints are excluded.")
    writer.table(["Report record", "Reference"], [
        ["RCA case", case.get("case_id")], ["Dataset snapshot", case.get("item_id")],
        ["Workflow generation", case.get("workflow_generation", 1)],
        ["Exported (UTC)", datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")],
    ], (48, 138))
    if investigated:
        writer.table(["Hypothesis", "Retained identity"], [[f"Hypothesis {group['number']}", group["hypothesis_id"]] for group in investigated], (48, 138))
    categories = list(dict.fromkeys(entry["category"] for entry in artifacts.entries.values()))
    for category in categories:
        writer.heading(category)
        writer.table(["Ref.", "Artifact / purpose", "Status", "Retained reference"],
                     [[entry["reference"], entry["title"], entry["status"], entry["identity"]]
                      for entry in artifacts.entries.values() if entry["category"] == category], (14, 72, 28, 72))
    if not categories:
        writer.paragraph("Availability", "No artifact references were retained in this legacy case.")
    return "\n\n".join(writer.text).encode("utf-8") if fmt == "text" else bytes(pdf.output())


@progress.action("Preparing report", retain=False)
def build_report(case_id: str, tenant_id: str, fmt: str = "pdf") -> tuple[bytes, str]:
    from domains.rca.service import get_case
    case = get_case(case_id, tenant_id)
    return render_report(case, fmt), f"rca-completion-report.{'txt' if fmt == 'text' else 'pdf'}"
