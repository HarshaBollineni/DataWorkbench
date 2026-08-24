"""Galileo v2 on-demand PDF reports — spec §10.

One report per item, generated fresh at download time from the current DB
state (score + latest issue statuses). Stage 1 (database) groups results per
table; Stage 2 (dataset) is per test with use case + target variable. Reuses
the Latin-1-safe helpers from ai.report_pdf so a stray Unicode character can
never break the download.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from fpdf import FPDF

import system_db as db
from ai.report_pdf import _ascii
from ai.v2 import service

_INK = (24, 28, 35)          # Genpact Midnight Black
_ACCENT = (255, 173, 40)     # Genpact Sunset Orange
_FAIL = (255, 79, 89)        # Genpact Coral
_GREY = (107, 114, 128)
_LIGHT = (249, 250, 251)
_LINE = (224, 224, 218)

_READY_STATUSES = {"testlab_step4", "complete"}


def _loads(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def _fit(pdf: FPDF, text: str, width: float) -> str:
    text = _ascii(text)
    while text and pdf.get_string_width(text) > width - 2:
        text = text[:-2]
    return text


def _heading(pdf: FPDF, title: str) -> None:
    pdf.ln(3)
    pdf.set_draw_color(*_LINE)
    pdf.set_line_width(0.3)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*_INK)
    pdf.cell(0, 6, _ascii(title).upper(), new_x="LMARGIN", new_y="NEXT")


def _table(pdf: FPDF, headers: list[str], widths: list[float], rows: list[list[str]],
           status_col: int | None = None) -> None:
    pdf.set_fill_color(*_LIGHT)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*_GREY)
    for h, w in zip(headers, widths):
        pdf.cell(w, 5.5, _ascii(h).upper(), fill=True)
    pdf.ln(5.5)
    pdf.set_font("Helvetica", "", 7.5)
    for row in rows:
        if pdf.get_y() > 270:
            pdf.add_page()
        for i, (value, w) in enumerate(zip(row, widths)):
            if i == status_col:
                v = str(value).lower()
                color = _FAIL if v == "fail" else ((16, 122, 72) if v == "pass" else _GREY)
                pdf.set_text_color(*color)
                pdf.set_font("Helvetica", "B", 7.5)
            else:
                pdf.set_text_color(*_INK)
                pdf.set_font("Helvetica", "", 7.5)
            pdf.cell(w, 5, _fit(pdf, str(value), w))
        pdf.ln(5)
    pdf.set_text_color(*_INK)


def _fmt_threshold(value) -> str:
    if value is None:
        return "-"
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def _result_rows(item_id: str) -> list[dict]:
    rows = db.execute(
        "SELECT r.*, p.columns_json AS plan_columns FROM results_v2 r "
        "LEFT JOIN plan_v2 p ON p.row_id = r.row_id "
        "WHERE r.item_id=? AND r.scope IN ('framework','incremental') "
        "ORDER BY r.table_name, r.test_name, r.run_at", [item_id])
    for r in rows:
        # Per-execution columns first (univariate tests report one row per
        # variable — feedback 09-07 4.1); plan columns for legacy rows.
        r["columns"] = _loads(r.get("columns_json"), None) or _loads(r.get("plan_columns"), [])
        r["threshold"] = _loads(r.get("threshold_json"), None)
    return rows


def build_report(item_id: str) -> tuple[bytes, str]:
    item = service.require_item(item_id)
    if item.get("status") not in _READY_STATUSES:
        raise ValueError("Report is available once this item has completed Test Lab.")
    stage2 = item["kind"] == "dataset"
    score = service.score(item_id)
    results = _result_rows(item_id)
    issues = db.query("issues_v2", item_id=item_id)
    tracked = {t["issue_id"]: t for t in db.query("tracked_issues_v2")}

    now = datetime.now()
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=18, top=18, right=18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # Header bar — Midnight Black with the report identity.
    pdf.set_fill_color(*_INK)
    pdf.rect(18, 18, 174, 16, style="F")
    pdf.set_xy(21, 20)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(100, 6, "AEGIS LABS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(21, 26)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.cell(100, 4, "Data Quality Assessment - Galileo 1.0")
    pdf.set_xy(108, 20)
    pdf.set_font("Helvetica", "", 7)
    # Every upload runs the full two-stage framework (feedback 2.2); the kind
    # only changes the layout below, not the assessment scope.
    label = "Full Framework Assessment Report"
    pdf.cell(82, 4, label, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(108, 24)
    pdf.cell(82, 4, now.strftime("%d %b %Y  -  %H:%M"), align="R")

    pdf.set_y(38)
    pdf.set_text_color(*_INK)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(120, 9, _fit(pdf, item.get("name") or item_id, 120))
    final = score.get("final")
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*(_ACCENT if final is None or final >= 60 else _FAIL))
    pdf.cell(54, 9, "-" if final is None else f"{final:.1f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*_GREY)
    pdf.cell(120, 4, "")
    pdf.cell(54, 4, "HEALTH SCORE", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    meta = [("Uploaded", item.get("created_at") or "-")]
    if stage2:
        meta += [("Use case", item.get("use_case") or "-"),
                 ("Target variable", item.get("target_variable") or "-")]
    pdf.set_font("Helvetica", "", 8)
    for k, v in meta:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*_GREY)
        pdf.cell(30, 4.8, k)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_INK)
        pdf.cell(0, 4.8, _ascii(str(v)), new_x="LMARGIN", new_y="NEXT")

    # Results — per table for Stage 1, one flat table for Stage 2.
    ran = [r for r in results if r["status"] in {"pass", "fail"}]
    headers = ["Test", "Status", "Threshold", "Value", "Viol.", "Affected columns"]
    widths = [48, 13, 32, 15, 11, 55]

    def row_of(r: dict) -> list[str]:
        metric = r.get("metric")
        return [r["test_name"], r["status"], _fmt_threshold(r.get("threshold")),
                "-" if metric is None else f"{float(metric):.4f}",
                str(r.get("violation_count") or 0), ", ".join(r.get("columns") or [])]

    if stage2:
        _heading(pdf, "Test results")
        _table(pdf, headers, widths, [row_of(r) for r in ran], status_col=1)
    else:
        for table_name in sorted({r["table_name"] for r in ran}):
            _heading(pdf, f"Table: {table_name}")
            _table(pdf, headers, widths,
                   [row_of(r) for r in ran if r["table_name"] == table_name], status_col=1)

    # Could not assess — visibility only, never counted as failures.
    nr = [r for r in results if r["status"] == "not_runnable"]
    _heading(pdf, "Could not assess")
    if nr:
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*_INK)
        for r in nr:
            reason = (_loads(r.get("evidence_json"), {}) or {}).get("reason") or "Not runnable."
            pdf.cell(0, 4.6, _fit(pdf, f"- {r['test_name']} ({r['table_name']}): {reason}", 174),
                     new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*_GREY)
        pdf.cell(0, 4.6, "All planned tests were assessable.", new_x="LMARGIN", new_y="NEXT")

    # Issue register — current statuses at download time.
    _heading(pdf, "Issue register")
    if issues:
        reg_headers = ["Status", "Test", "Table", "Crit.", "Viol.", "Resolution / Tracked issue"]
        reg_widths = [16, 42, 26, 13, 11, 66]
        reg_rows = []
        for i in sorted(issues, key=lambda x: (x.get("status") or "", x.get("test_name") or "")):
            detail = ""
            if i.get("status") == "Closed":
                detail = i.get("resolution_rationale") or ""
            elif i.get("tracked_issue_id"):
                t = tracked.get(i["tracked_issue_id"], {})
                detail = (f"{i['tracked_issue_id']} - {t.get('owner') or 'unassigned'} - "
                          f"{t.get('priority') or '-'} - due {t.get('target_date') or '-'}")
            reg_rows.append([i.get("status") or "Open", i.get("test_name") or "",
                             i.get("table_name") or "",
                             i.get("criticality") or "-",
                             str(i.get("violation_count") or 0), detail])
        _table(pdf, reg_headers, reg_widths, reg_rows)
    else:
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*_GREY)
        pdf.cell(0, 4.6, "No issues - all tests passed.", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_draw_color(*_LINE)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(*_GREY)
    pdf.cell(0, 4, _ascii("Generated on demand by Aegis Labs - reflects scores and issue "
                          "statuses at download time. AI-assisted content may be inaccurate; "
                          "review before relying on it."))

    safe = re.sub(r"[^A-Za-z0-9]+", "-", item.get("name") or item_id).strip("-").lower()
    return bytes(pdf.output()), f"{safe or 'item'}-dq-report.pdf"
