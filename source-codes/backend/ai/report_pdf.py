"""PDF report generator for the Database Understanding report.

Generates a clean, vector-quality A4 PDF using fpdf2 (pure-Python, no system
dependencies). Called by POST /api/datasource/report in datasource.py.

Feedback R7 (2026-07-01): the report is now MARKDOWN-DRIVEN — it renders exactly
the three sections the analyst sees and edits on screen (Summary with Findings /
Potential Usages, Key Anomalies, Potential Test Hypotheses). It opens with a
small headline infographic (tables + total records). Every string is run through
``_ascii`` before it reaches the core Helvetica font, which is Latin-1 only:
the AI prose routinely contains em-dashes, curly quotes, middle-dots and arrows,
and an un-encodable character used to crash the whole download (the "download
button does nothing" bug). ``_ascii`` maps the common punctuation to ASCII and
replaces anything still out of range, so a report can never fail to render.
"""
from __future__ import annotations

import re
from datetime import datetime

from fpdf import FPDF

_PURPLE = (75, 30, 120)
_GREY = (107, 114, 128)
_LIGHT_GREY = (249, 250, 251)
_DARK = (30, 30, 46)
_DIVIDER = (221, 214, 254)

# Common Unicode punctuation the LLM emits -> ASCII (core Helvetica is Latin-1).
_SUBST = {
    "—": "-", "–": "-", "‒": "-", "―": "-", "‐": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "…": "...", "•": "-", "·": "-", "‧": "-",
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>",
    "≥": ">=", "≤": "<=", "≠": "!=", "≈": "~", "×": "x",
    "′": "'", "″": '"', " ": " ", " ": " ", " ": " ",
    "​": "", "﻿": "", "–": "-",
}


def _ascii(text) -> str:
    """Make a string safe for the core Helvetica font (Latin-1). Never raises."""
    if text is None:
        return ""
    out = "".join(_SUBST.get(ch, ch) for ch in str(text))
    return out.encode("latin-1", "replace").decode("latin-1")


# Inline markdown: **bold** and _italic_ spans (the same subset the on-screen
# renderer supports, so the PDF is WYSIWYG with the editor).
_INLINE = re.compile(r"\*\*(.+?)\*\*|_(.+?)_")


def _tokenize_inline(text: str):
    """Split text into (content, bold, italic) runs on **..** / _.._ markers."""
    out: list[tuple[str, bool, bool]] = []
    i = 0
    for m in _INLINE.finditer(text or ""):
        if m.start() > i:
            out.append((text[i:m.start()], False, False))
        if m.group(1) is not None:
            out.append((m.group(1), True, False))
        else:
            out.append((m.group(2), False, True))
        i = m.end()
    if i < len(text or ""):
        out.append((text[i:], False, False))
    return out or [(text or "", False, False)]


def _section_heading(pdf: FPDF, title: str) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_PURPLE)
    pdf.cell(0, 6, _ascii(title).upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)


def _divider(pdf: FPDF) -> None:
    pdf.ln(2)
    pdf.set_draw_color(*_DIVIDER)
    pdf.set_line_width(0.3)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(4)


def _write_runs(pdf: FPDF, text: str, size: float, h: float) -> None:
    """Flow inline-styled text with automatic wrapping via pdf.write()."""
    for content, bold, italic in _tokenize_inline(text):
        style = ("B" if bold else "") + ("I" if italic else "")
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(*(_GREY if italic else _DARK))
        pdf.write(h, _ascii(content))
    pdf.set_text_color(*_DARK)


def _render_paragraph(pdf: FPDF, text: str) -> None:
    pdf.set_x(18)
    _write_runs(pdf, text, 9, 5)
    pdf.ln(6)


def _render_subheading(pdf: FPDF, text: str) -> None:
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*_DARK)
    pdf.multi_cell(0, 5, _ascii(text))


def _render_bullet(pdf: FPDF, text: str) -> None:
    pdf.set_left_margin(24)  # so wrapped lines indent under the bullet text
    pdf.set_x(20)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_DARK)
    pdf.write(4.8, "- ")
    _write_runs(pdf, text, 9, 4.8)
    pdf.ln(5.2)
    pdf.set_left_margin(18)


def _render_markdown(pdf: FPDF, md: str) -> None:
    """Render a markdown block (## / ### headings, - bullets, **bold**/_italic_).
    A '## ' heading opens a new report section (divider + purple heading)."""
    if not md or not md.strip():
        return
    for raw in md.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            _divider(pdf)
            _section_heading(pdf, line[3:].strip())
        elif line.startswith("### "):
            _render_subheading(pdf, line[4:].strip())
        elif line.startswith("# "):
            _divider(pdf)
            _section_heading(pdf, line[2:].strip())
        elif line.startswith("- ") or line.startswith("* "):
            _render_bullet(pdf, line[2:].strip())
        else:
            _render_paragraph(pdf, line.strip())


def _headline(pdf: FPDF, table_count: int, total_records: int) -> None:
    """Small infographic: two stat tiles (Tables / Total records) — the report
    opens with the scale of the database (Feedback R7)."""
    y = pdf.get_y()
    tile_w, gap, h = 84, 6, 18
    tiles = [("TABLES", f"{int(table_count):,}"),
             ("TOTAL RECORDS", f"{int(total_records):,}")]
    for i, (label, value) in enumerate(tiles):
        x = 18 + i * (tile_w + gap)
        pdf.set_fill_color(*_LIGHT_GREY)
        pdf.set_draw_color(*_DIVIDER)
        pdf.set_line_width(0.3)
        pdf.rect(x, y, tile_w, h, style="DF")
        pdf.set_xy(x + 4, y + 3)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*_GREY)
        pdf.cell(tile_w - 8, 4, label)
        pdf.set_xy(x + 4, y + 8.5)
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(*_PURPLE)
        pdf.cell(tile_w - 8, 8, value)
    pdf.set_text_color(*_DARK)
    pdf.set_y(y + h + 2)


def generate_pdf(
    *,
    logical_db: str,
    display_name: str,
    description: str,
    criticality: str,
    use_cases: list[str],
    model_families: list[str],
    table_count: int,
    total_records: int,
    table_counts: list[dict],
    summary_md: str = "",
    anomalies_md: str = "",
    hypotheses_md: str = "",
    attribution_label: str = "Aegis Labs AI",
    attribution_provider: str = "Azure OpenAI",
    attribution_model: str = "gpt-4.1",
    disclaimer: str = (
        "AI-generated content may be inaccurate or incomplete. "
        "Please review and edit before relying on it."
    ),
) -> bytes:
    now = datetime.now()
    date_str = now.strftime("%d %b %Y")
    time_str = now.strftime("%H:%M")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=18, top=18, right=18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # ── Header bar ────────────────────────────────────────────────────────────
    pdf.set_fill_color(*_PURPLE)
    pdf.rect(18, 18, 174, 16, style="F")

    pdf.set_xy(21, 20)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(90, 6, "AEGIS LABS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(21, 26)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.cell(90, 4, "Agentic Feature Validation & Guardrail Workspace")

    pdf.set_xy(108, 20)
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(82, 4, "Database Understanding Report", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(108, 24)
    pdf.cell(82, 4, f"{date_str}  -  {time_str}", align="R")

    # ── Title ─────────────────────────────────────────────────────────────────
    pdf.set_y(38)
    pdf.set_text_color(*_DARK)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, _ascii(display_name or logical_db), new_x="LMARGIN", new_y="NEXT")

    if description:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_GREY)
        pdf.multi_cell(0, 5, _ascii(description))
        pdf.set_text_color(*_DARK)

    pdf.ln(3)

    # ── Headline infographic (Tables / Total records) ─────────────────────────
    _headline(pdf, table_count, total_records)
    pdf.ln(2)

    # ── Metadata grid (two columns) ───────────────────────────────────────────
    col_w = 87
    meta_items: list[tuple[str, str]] = [
        ("Database ID", logical_db),
        ("Criticality", criticality or "-"),
    ]
    if use_cases:
        meta_items.append(("Use cases", ", ".join(use_cases)))
    if model_families:
        meta_items.append(("Model families", ", ".join(model_families)))

    row_y = pdf.get_y()
    for i, (label, value) in enumerate(meta_items):
        col = i % 2
        if col == 0 and i > 0:
            row_y += 5
        x = 18 + col * col_w
        pdf.set_xy(x, row_y)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*_GREY)
        pdf.cell(30, 5, label)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_DARK)
        pdf.cell(col_w - 30, 5, _ascii(str(value))[:70])
    pdf.set_y(row_y + 5)

    # ── Attribution line ──────────────────────────────────────────────────────
    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*_GREY)
    pdf.cell(
        0, 4,
        f"AI understanding by {attribution_label}  -  {attribution_provider} {attribution_model}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*_DARK)

    # ── Body sections (rendered from the same markdown shown on screen) ────────
    _render_markdown(pdf, summary_md)
    _render_markdown(pdf, anomalies_md)
    _render_markdown(pdf, hypotheses_md)

    # ── Table record counts ───────────────────────────────────────────────────
    if table_counts:
        _divider(pdf)
        _section_heading(pdf, "Table Record Counts")
        pdf.set_fill_color(*_LIGHT_GREY)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_GREY)
        pdf.cell(130, 6, "Table", fill=True)
        pdf.cell(44, 6, "Records", align="R", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_DARK)
        for row in table_counts:
            pdf.cell(130, 5.5, _ascii(str(row.get("table", ""))))
            cnt = row.get("record_count") or 0
            pdf.cell(44, 5.5, f"{int(cnt):,}", align="R", new_x="LMARGIN", new_y="NEXT")

    # ── Scope & Attribution (closing section) ─────────────────────────────────
    _divider(pdf)
    _section_heading(pdf, "Scope & Attribution")
    scope_items: list[tuple[str, str]] = []
    if model_families:
        scope_items.append(("Model families", ", ".join(model_families)))
    if use_cases:
        scope_items.append(("Use cases", ", ".join(use_cases)))
    scope_items.append(("Generated by", f"{attribution_label} - {attribution_provider} {attribution_model}"))
    scope_items.append(("Generated on", f"{date_str} {time_str}"))
    pdf.set_font("Helvetica", "", 8)
    for label, value in scope_items:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*_GREY)
        pdf.cell(30, 4.6, label)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(144, 4.6, _ascii(str(value)))

    pdf.ln(3)
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.2)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*_GREY)
    pdf.multi_cell(0, 4, _ascii(disclaimer))

    return bytes(pdf.output())
