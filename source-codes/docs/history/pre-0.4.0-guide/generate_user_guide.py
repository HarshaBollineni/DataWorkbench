# Generate the Aegis Labs Functional User Guide as a Word document.
# Run from the repo root:
#   .venv\Scripts\python.exe docs\history\pre-0.4.0-guide\generate_user_guide.py
# Output: docs\history\pre-0.4.0-guide\Aegis_Labs_User_Guide.docx

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import os

OUT_PATH = os.path.join(os.path.dirname(__file__), "Aegis_Labs_User_Guide.docx")

# ── colour palette ────────────────────────────────────────────────────────────
DARK_TEAL   = RGBColor(0x00, 0x4C, 0x6E)   # headings
MID_TEAL    = RGBColor(0x00, 0x7A, 0xA5)   # sub-headings / accents
LIGHT_TEAL  = RGBColor(0xE0, 0xF3, 0xFA)   # shaded header rows (table)
AMBER_BG    = RGBColor(0xFF, 0xF3, 0xCD)   # HITL / warning rows
GRAY_BG     = RGBColor(0xF2, 0xF2, 0xF2)   # screenshot placeholder
WHITE       = RGBColor(0xFF, 0xFF, 0xFF)
BLACK       = RGBColor(0x00, 0x00, 0x00)


# ── low-level XML helpers ─────────────────────────────────────────────────────

def set_cell_bg(cell, rgb: RGBColor):
    """Fill a table cell with a solid background colour."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    hex_color = str(rgb)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def set_cell_borders(cell, border_color="AAAAAA", size=4):
    """Apply thin borders to a table cell."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        border = OxmlElement(f"w:{side}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), str(size))
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), border_color)
        tcBorders.append(border)
    tcPr.append(tcBorders)


def set_table_borders(table, border_color="AAAAAA"):
    for row in table.rows:
        for cell in row.cells:
            set_cell_borders(cell, border_color)


def remove_table_borders(table):
    """Remove all visible borders from a table (used for screenshot boxes)."""
    tbl = table._tbl
    tblPr = tbl.find(qn("w:tblPr"))
    if tblPr is None:
        tblPr = OxmlElement("w:tblPr")
        tbl.insert(0, tblPr)
    tblBorders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{side}")
        border.set(qn("w:val"), "none")
        tblBorders.append(border)
    old = tblPr.find(qn("w:tblBorders"))
    if old is not None:
        tblPr.remove(old)
    tblPr.append(tblBorders)


def bold_run(para, text, color=None, size=None):
    r = para.add_run(text)
    r.bold = True
    if color:
        r.font.color.rgb = color
    if size:
        r.font.size = Pt(size)
    return r


def normal_run(para, text, italic=False, size=None, color=None):
    r = para.add_run(text)
    r.italic = italic
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = color
    return r


# ── document-level helpers ────────────────────────────────────────────────────

def add_heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        run.font.color.rgb = DARK_TEAL if level == 1 else MID_TEAL
    return p


def add_subheading(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(12)
    r.font.color.rgb = MID_TEAL
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    return p


def add_body(doc, text):
    p = doc.add_paragraph(text)
    p.paragraph_format.space_after = Pt(4)
    return p


def add_step(doc, number, what_to_do, you_will_see=None):
    """Add a numbered step with optional 'You will see' note."""
    p = doc.add_paragraph(style="List Number")
    bold_run(p, f"Step {number}: ", color=DARK_TEAL)
    normal_run(p, what_to_do)
    if you_will_see:
        q = doc.add_paragraph()
        q.paragraph_format.left_indent = Cm(1.5)
        q.paragraph_format.space_before = Pt(0)
        q.paragraph_format.space_after = Pt(6)
        normal_run(q, "You will see: ", italic=True, color=MID_TEAL)
        normal_run(q, you_will_see)
    return p


def add_screenshot_placeholder(doc, description):
    """Add a grey bordered box labelled as a screenshot placeholder."""
    doc.add_paragraph()
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = tbl.cell(0, 0)
    set_cell_bg(cell, GRAY_BG)
    set_cell_borders(cell, border_color="999999", size=6)
    cp = cell.paragraphs[0]
    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cp.paragraph_format.space_before = Pt(10)
    cp.paragraph_format.space_after = Pt(10)
    bold_run(cp, "[ SCREENSHOT ]", color=RGBColor(0x66, 0x66, 0x66))
    cp.add_run("\n")
    normal_run(cp, description, italic=True, color=RGBColor(0x55, 0x55, 0x55), size=9)
    doc.add_paragraph()


def add_hitl_note(doc, text):
    """Add an amber-shaded HITL / human decision box."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    cell = tbl.cell(0, 0)
    set_cell_bg(cell, AMBER_BG)
    set_cell_borders(cell, border_color="C8A200", size=6)
    cp = cell.paragraphs[0]
    bold_run(cp, "HUMAN DECISION REQUIRED  ", color=RGBColor(0x7A, 0x56, 0x00))
    normal_run(cp, text, size=10)
    doc.add_paragraph()


def add_info_note(doc, text):
    """Add a light-teal informational note box."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    cell = tbl.cell(0, 0)
    set_cell_bg(cell, LIGHT_TEAL)
    set_cell_borders(cell, border_color="007AA5", size=6)
    cp = cell.paragraphs[0]
    bold_run(cp, "NOTE  ", color=DARK_TEAL)
    normal_run(cp, text, size=10)
    doc.add_paragraph()


def add_agent_table(doc, rows):
    """
    rows: list of (Agent, Role summary, Inputs, Outputs)
    Header row is DARK_TEAL on white text.
    """
    headers = ["Agent", "Role", "Inputs", "Outputs"]
    tbl = doc.add_table(rows=1, cols=4)
    tbl.style = "Table Grid"
    # header row
    hdr = tbl.rows[0]
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        set_cell_bg(cell, DARK_TEAL)
        p = cell.paragraphs[0]
        r = p.add_run(h)
        r.bold = True
        r.font.color.rgb = WHITE
        r.font.size = Pt(10)
    # data rows
    for row_data in rows:
        row = tbl.add_row()
        for i, val in enumerate(row_data):
            cell = row.cells[i]
            p = cell.paragraphs[0]
            if i == 0:
                bold_run(p, val, color=DARK_TEAL, size=10)
            else:
                normal_run(p, val, size=10)
    set_table_borders(tbl)
    doc.add_paragraph()


def add_two_col_table(doc, header_left, header_right, rows):
    """Generic two-column table."""
    tbl = doc.add_table(rows=1, cols=2)
    tbl.style = "Table Grid"
    hdr = tbl.rows[0]
    for i, h in enumerate([header_left, header_right]):
        cell = hdr.cells[i]
        set_cell_bg(cell, DARK_TEAL)
        p = cell.paragraphs[0]
        r = p.add_run(h)
        r.bold = True
        r.font.color.rgb = WHITE
        r.font.size = Pt(10)
    for left, right in rows:
        row = tbl.add_row()
        normal_run(row.cells[0].paragraphs[0], left, size=10)
        normal_run(row.cells[1].paragraphs[0], right, size=10)
    set_table_borders(tbl)
    doc.add_paragraph()


# ── COVER PAGE ────────────────────────────────────────────────────────────────

def build_cover(doc):
    doc.add_paragraph()
    doc.add_paragraph()
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Aegis Labs")
    r.bold = True
    r.font.size = Pt(32)
    r.font.color.rgb = DARK_TEAL

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run("Agentic Feature Validation & Guardrail Workspace")
    r2.font.size = Pt(16)
    r2.font.color.rgb = MID_TEAL

    doc.add_paragraph()

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r3 = p3.add_run("Functional User Guide")
    r3.bold = True
    r3.font.size = Pt(22)
    r3.font.color.rgb = DARK_TEAL

    doc.add_paragraph()
    p4 = doc.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r4 = p4.add_run("Version 1.0  ·  July 2026  ·  Genpact DNA Accelerators")
    r4.font.size = Pt(11)
    r4.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()


# ── SECTION 1 — INTRODUCTION ──────────────────────────────────────────────────

def build_intro(doc):
    add_heading(doc, "1.  Introduction", 1)
    add_body(doc,
        "Aegis Labs is an agentic data-quality platform designed for analytical and model-feature "
        "data. It guides you through five connected workflows — from connecting a database to "
        "raising a remediation ticket — while AI agents do the heavy analytical lifting. "
        "You stay in control at every critical decision point through Human-in-the-Loop (HITL) gates."
    )

    add_subheading(doc, "1.1  The Five Workflows at a Glance")
    add_two_col_table(doc, "Workflow", "What happens", [
        ("① Data Sourcing",      "Connect a database, profile its tables, and get an AI-generated summary of its data landscape."),
        ("② Define Test Plan",   "Select or AI-generate data quality tests and attach them to specific table columns. Review and finalise the plan."),
        ("③ Run Validations",    "Execute the finalised test plan against the live data. Each test produces a pass/fail result and a health score."),
        ("④ Root Cause Analysis","Investigate why a test failed. An AI agent proposes diagnostic code; you approve it. The system then declares a root cause."),
        ("⑤ Issue Management",  "Raise a tracked ticket from a confirmed root cause and manage it through Raised → Under Review → Closed."),
    ])

    add_subheading(doc, "1.2  The Agent Console")
    add_body(doc,
        "Whenever an AI agent is working, a dark live panel called the Agent Console appears on "
        "screen. It streams the agent's reasoning and progress in real time. You do not need to "
        "interact with it — just read top to bottom to follow which agent is acting and why. "
        "It closes automatically when the agent finishes."
    )
    add_info_note(doc,
        "The Agent Console shows two types of messages: token frames (the agent's words appearing "
        "word-by-word, like a chatbot) and step frames (phase-transition markers between agents). "
        "Both are informational only."
    )
    add_screenshot_placeholder(doc,
        "The Agent Console panel streaming output during a Newton database-understanding run. "
        "Show the dark panel overlaid on the Data Sourcing screen, with visible token output."
    )

    add_subheading(doc, "1.3  Logging In")
    add_body(doc, "Open the application in your browser. You will see a login screen.")
    add_step(doc, 1, "Enter your username and password.")
    add_step(doc, 2, "Click Sign In.",
             "The main navigation loads. The top bar shows your username and role.")
    add_screenshot_placeholder(doc,
        "The login screen showing the Aegis Labs logo, username and password fields, and Sign In button."
    )

    doc.add_page_break()


# ── SECTION 2 — WORKFLOW 1: DATA SOURCING ─────────────────────────────────────

def build_data_sourcing(doc):
    add_heading(doc, "2.  Workflow ① — Data Sourcing", 1)
    add_body(doc,
        "Data Sourcing is the entry point. You connect a logical database, ingest its schema and "
        "metadata, and receive an AI-generated plain-English summary of the data landscape. "
        "Once you select which tables to analyse, the database is available to subsequent workflows."
    )

    add_subheading(doc, "2.1  Step-by-Step")
    add_step(doc, 1, "Navigate to Data Sourcing from the left-hand menu.",
             "A catalogue of available databases is displayed. Each entry shows a database name, "
             "domain, and status (Not Ingested / Ingested).")
    add_screenshot_placeholder(doc,
        "The Data Sourcing catalogue page showing database cards with name, domain, "
        "and ingestion status badges."
    )

    add_step(doc, 2, "Click on a database card marked 'Not Ingested' to select it.",
             "A details panel opens on the right showing the database description, "
             "available tables, and an Ingest button.")

    add_step(doc, 3, "Click Ingest.",
             "The system fetches the schema and metadata. A progress indicator appears. "
             "This takes a few seconds.")
    add_screenshot_placeholder(doc,
        "The Data Sourcing page mid-ingestion: progress spinner visible, status message "
        "reading 'Fetching schema and metadata…'"
    )

    add_step(doc, 4,
             "Wait for the Agent Console to open and stream the Newton agent's database summary.",
             "The Agent Console panel appears. Newton writes two paragraphs: a statistical "
             "overview and a data-quality profiling summary. You can read along in real time.")
    add_screenshot_placeholder(doc,
        "The Agent Console showing Newton's completed output — two paragraphs of plain-English "
        "database understanding text visible in the dark panel."
    )

    add_step(doc, 5,
             "Review Newton's summary. When the Agent Console closes, you will see the summary "
             "displayed on the page along with a table list.",
             "Each table is shown with its row count, column count, and a brief description.")

    add_step(doc, 6,
             "Select the tables you want to include in your analysis by ticking their checkboxes, "
             "then click Confirm Table Selection.",
             "The database status changes to Ingested. The selected tables are now available "
             "for test planning.")
    add_screenshot_placeholder(doc,
        "The table-selection panel with checkboxes next to each table name and a 'Confirm Selection' button."
    )

    add_subheading(doc, "2.2  Agents in This Workflow")
    add_agent_table(doc, [
        ("Newton",
         "Reads the profiled schema and writes a plain-English understanding of the database — "
         "its domain, structure, data-quality risks, and analytical purpose.",
         "Per-column statistics, null rates, cardinality, anomaly flags, row counts, and "
         "cross-table join candidates produced by the deterministic profiler (Phase 1).",
         "Two structured paragraphs: (1) Statistical overview — domain, tables, record counts, "
         "grain. (2) Profiling summary — completeness, distributions, relationships, top DQ risk. "
         "Also returns open questions and assumptions."),
        ("Merton",
         "Senior credit-risk subject-matter expert. Assesses the potential usages of the "
         "database across analytics, modelling, and regulatory reporting.",
         "Table schemas, column descriptions, and the Newton summary.",
         "One plain-English paragraph describing the database's potential usages "
         "through the lens of credit-risk analytics, modelling, and reporting."),
    ])

    add_info_note(doc,
        "Newton's output is deterministic — regenerating the summary on the same data will "
        "produce identical text. This is by design, not a bug."
    )

    doc.add_page_break()


# ── SECTION 3 — WORKFLOW 2: DEFINE TEST PLAN ──────────────────────────────────

def build_test_plan(doc):
    add_heading(doc, "3.  Workflow ② — Define Test Plan", 1)
    add_body(doc,
        "The Define Test Plan workflow lets you build a governed set of data-quality tests "
        "for a selected table and column. There are two paths: "
        "(a) pick from the existing test battery, or "
        "(b) ask the AI to recommend or author new tests. Both paths converge at a Finalise "
        "gate before execution."
    )

    add_subheading(doc, "3.1  Path A — Existing Test Battery (Hypatia Search)")
    add_step(doc, 1, "Navigate to Define Test Plan and select your ingested database and table.",
             "The table's columns are listed. A search bar and filter panel appear on the left.")
    add_screenshot_placeholder(doc,
        "The Define Test Plan page showing an ingested database selected, "
        "column list visible, and the 'Search Tests' panel open."
    )

    add_step(doc, 2, "Select a column, then click Search Tests.",
             "Hypatia deterministically matches the column's datatype and metadata against the "
             "test library and returns a list of applicable tests — no AI call is made.")

    add_step(doc, 3, "Review the recommended tests. Click a test name to see its description, "
             "category, and default threshold.",
             "A side panel shows the test details: name, category (C1–C4), default threshold, "
             "and a plain-English description.")
    add_screenshot_placeholder(doc,
        "Test details side panel showing test name, category badge (e.g. C1), "
        "threshold value, and description text."
    )

    add_step(doc, 4, "Click Add to Plan for each test you want to include.",
             "The test appears in the plan summary on the right with editable threshold and "
             "criticality fields.")

    add_step(doc, 5, "Adjust the threshold and criticality if needed, then click Save.",
             "The test is saved to the plan with status 'Under Review'.")

    add_subheading(doc, "3.2  Path B — AI-Suggested Tests (Deep Search + Gauss Authoring)")
    add_step(doc, 1,
             "Select a column and click Deep Search (instead of Search Tests).",
             "The Agent Console opens. Hypatia re-evaluates the library using the AI model "
             "to find medium- and low-confidence matches beyond the deterministic results.")

    add_step(doc, 2,
             "To request a brand-new test authored by AI, click New Test (Gauss). "
             "Enter a description of what you want to test and click Generate.",
             "The Agent Console streams the full Gauss pipeline: "
             "Poincaré → Fermat → Euler → Hypatia → Gauss. This takes 30–90 seconds.")
    add_screenshot_placeholder(doc,
        "The Agent Console showing the Gauss pipeline in progress: step frames "
        "marking Poincaré, Fermat, Euler, Hypatia, and Gauss transitions visible."
    )

    add_hitl_note(doc,
        "When Gauss finishes, you are shown the proposed test: its name, Python code, "
        "thresholds, rationale, and a dry-run result against real data. "
        "You must review and either Approve or Reject the proposed test before it can be "
        "added to the plan. If you reject it, you may provide feedback and request a revision."
    )
    add_screenshot_placeholder(doc,
        "The HITL review panel for a Gauss-authored test: test name, rationale text, "
        "dry-run result (pass/fail badge), and Approve / Reject buttons visible."
    )

    add_step(doc, 3,
             "After approving a test, click Add to Plan.",
             "The test appears in the plan summary, same as Path A.")

    add_subheading(doc, "3.3  Finalising the Plan (HITL Gate)")
    add_body(doc,
        "Before the plan can be executed, it must be finalised. This is a governance gate — "
        "it locks the plan and prevents unauthorised changes."
    )
    add_step(doc, 1,
             "Review all tests in the plan summary. Confirm thresholds and criticalities are correct.")
    add_step(doc, 2,
             "Click Finalise Plan.",
             "Pascal (the criticality-ranking agent) runs and assigns High / Medium / Low "
             "criticality to each test instance. The plan status changes from Under Review to "
             "Finalised.")
    add_hitl_note(doc,
        "Finalising is irreversible within a session. A finalised plan cannot be edited — "
        "only executed. If you need to change a test after finalising, you must reset the "
        "plan for that table and start again."
    )
    add_screenshot_placeholder(doc,
        "The plan summary in Finalised state: each test row showing its criticality badge "
        "(High/Medium/Low) and a locked padlock icon. The 'Run Validations' button is now active."
    )

    add_subheading(doc, "3.4  Agents in This Workflow")
    add_agent_table(doc, [
        ("Hypatia",
         "Test library screening. Matches the selected column and datatype against the test "
         "battery and returns ranked recommendations.",
         "Column metadata (datatype, nullable, description), test library catalogue, "
         "selected mode (Search or Deep Search).",
         "A ranked list of recommended tests with confidence (high/medium/low) "
         "and a plain-English reason for each recommendation."),
        ("Poincaré",
         "Relationship Discovery. Proposes candidate statistical relationships "
         "within the selected table to inform test authoring.",
         "Column schema, data dictionary, table statistics fetched via schema-stats tool.",
         "A list of candidate column-level relationships with confidence scores."),
        ("Fermat",
         "Relationship Challenger (within-table). Scrutinises each Poincaré candidate "
         "using within-table evidence and domain logic.",
         "Poincaré's candidate list, column metadata, data dictionary.",
         "A verdict (accept / reject / revise) with a challenge explanation for each candidate."),
        ("Euler",
         "Global Consistency Checker (cross-table). Challenges candidates for consistency "
         "across tables and the broader domain model.",
         "Poincaré's candidates, cross-table context, Fermat's perspective.",
         "A verdict (accept / reject) with a reason for each candidate."),
        ("Gauss",
         "New Test Manager (Orchestrator). Synthesises all prior signals and authors a single, "
         "executable DQ test as a self-contained Python function.",
         "Accepted relationship candidates from Fermat/Euler, Hypatia's library recommendations, "
         "table schema, selected fields, date column, thresholds.",
         "A complete test artifact: name, Python code, thresholds, rationale, usability notes, "
         "and interpretation guidance."),
        ("Pascal",
         "Criticality Ranker. Orders all test instances in the finalised plan by business "
         "criticality using regulatory, model-risk, and downstream-impact signals.",
         "Full set of test instances with their definitions, targets, scope, and context.",
         "An ordered ranking list. Deterministic code then converts the ranking to "
         "High / Medium / Low categories."),
        ("Bayes",
         "Test Feedback Adjudicator (HITL shuttle). When you provide feedback on a "
         "Gauss-authored test, Bayes evaluates the feedback and either accommodates "
         "it or pushes back with a constructive alternative.",
         "The current test design (code, params, dossier) plus the human feedback note.",
         "A verdict (accommodate / push back), message, reasoning, and proposed changes."),
    ])

    doc.add_page_break()


# ── SECTION 4 — WORKFLOW 3: RUN VALIDATIONS ───────────────────────────────────

def build_run_validations(doc):
    add_heading(doc, "4.  Workflow ③ — Run Validations", 1)
    add_body(doc,
        "Run Validations executes the finalised test plan against the live data. Each test is "
        "run in a secure sandbox. Results are recorded as pass or fail, with a numeric metric "
        "and threshold. A data-health score is then computed for the table."
    )

    add_subheading(doc, "4.1  Step-by-Step")
    add_step(doc, 1,
             "From the Define Test Plan page (or the navigation menu), click Run Validations.",
             "The page shows a list of test instances for the selected table, each with its "
             "column, test name, threshold, and criticality.")
    add_screenshot_placeholder(doc,
        "The Run Validations page before execution: test instance rows listed with "
        "column name, test name, threshold, and criticality badge. The 'Run All' button is visible."
    )

    add_step(doc, 2,
             "Click Run All to execute all test instances.",
             "The Agent Console opens and streams live progress. Each test transitions through "
             "Running → Pass or Fail. A progress bar shows overall completion.")
    add_screenshot_placeholder(doc,
        "The Run Validations page mid-run: Agent Console streaming output, "
        "individual test rows showing spinning indicators and then pass/fail badges."
    )

    add_step(doc, 3,
             "Wait for all tests to complete.",
             "The Agent Console closes. Each row now shows a Pass (green) or Fail (red) badge, "
             "the measured metric, and the threshold it was compared against.")

    add_step(doc, 4,
             "Review the results. The Data Health Score for the table is displayed at the top.",
             "The health score is a 0–100 number: 100 = all tests passed at full criticality weight. "
             "Per-category scores (C1–C4) are also shown.")
    add_screenshot_placeholder(doc,
        "The Run Validations results view: health score gauge at the top, "
        "per-category score bars, and the test results table with pass/fail badges and metric values."
    )

    add_step(doc, 5,
             "If any tests failed, click Investigate on a failed row to start Root Cause Analysis "
             "(Workflow ④).",
             "You are taken to the RCA page for that specific failed test.")

    add_subheading(doc, "4.2  Agents in This Workflow")
    add_agent_table(doc, [
        ("Laplace",
         "Health Score Calculator. Turns the recorded run results into a single data-health "
         "score using deterministic, criticality-weighted scoring — no AI involved.",
         "All run_results for the table: test instance IDs, pass/fail status, criticality weights.",
         "A final_score (0–100) and per-category breakdown (C1, C2, C3, C4)."),
    ])

    add_info_note(doc,
        "Laplace makes no AI calls. The same run results always produce the same score. "
        "The health score reflects both the number of failures and their criticality — "
        "a single High-criticality failure weighs more than several Low-criticality ones."
    )

    doc.add_page_break()


# ── SECTION 5 — WORKFLOW 4: ROOT CAUSE ANALYSIS ───────────────────────────────

def build_rca(doc):
    add_heading(doc, "5.  Workflow ④ — Root Cause Analysis (RCA)", 1)
    add_body(doc,
        "Root Cause Analysis investigates why a specific test failed. The Feynman agent "
        "proposes diagnostic code to probe the data. You review and approve that code before "
        "it runs. Feynman then declares a root cause and a remediation path. "
        "Finally, the Noether agent independently challenges the conclusion before any "
        "ticket can be raised."
    )

    add_subheading(doc, "5.1  Step-by-Step")
    add_step(doc, 1,
             "Click Investigate on a failed test (from the Run Validations results page), "
             "or navigate to RCA from the menu and select the failed test.",
             "The RCA page opens showing the failed test details: table, column, test name, "
             "expected value, and actual result.")
    add_screenshot_placeholder(doc,
        "The RCA page opening screen: failed test summary card at the top showing "
        "table name, column, test name, expected threshold, and actual metric (FAIL in red)."
    )

    add_step(doc, 2,
             "Click Start RCA.",
             "The Agent Console opens. Feynman begins its analysis: it reads the schema, "
             "row statistics, and prior run results, then hypothesises the most likely cause.")

    add_step(doc, 3,
             "Feynman will pause and present a proposed diagnostic code snippet for your review.",
             "The Agent Console highlights the proposed code. A side panel shows: the rationale "
             "(why Feynman wants to run this code), the code itself, and Approve / Reject buttons.")
    add_hitl_note(doc,
        "You must review the proposed diagnostic code before it runs against the data. "
        "Read the rationale and the code carefully. Click Approve to allow it to run, "
        "or Reject to ask Feynman to try a different approach. "
        "This step may repeat if Feynman needs more than one probe to confirm its hypothesis."
    )
    add_screenshot_placeholder(doc,
        "The HITL code-approval panel: rationale text on the left, proposed Python code "
        "block on the right, and Approve / Reject buttons at the bottom."
    )

    add_step(doc, 4,
             "After you approve the code, it runs in a secure sandbox and the output is "
             "fed back to Feynman.",
             "The Agent Console shows the diagnostic output and Feynman's updated reasoning.")

    add_step(doc, 5,
             "When Feynman has enough evidence, it declares the root cause.",
             "A structured finding is displayed: Root Cause (plain English), Evidence summary, "
             "and a Remediation path from a fixed set of options.")
    add_screenshot_placeholder(doc,
        "The RCA declared finding panel: 'Root Cause' heading with plain-English text, "
        "'Evidence' section, and 'Recommended Remediation' label with the selected path."
    )

    add_step(doc, 6,
             "Noether automatically reviews the declared root cause.",
             "The Agent Console briefly shows Noether's challenge. If Noether accepts it, "
             "a green 'RCA Verified' badge appears and the Raise Ticket button becomes active. "
             "If Noether requests revision, the cycle returns to Feynman.")
    add_screenshot_placeholder(doc,
        "The RCA page after Noether verification: 'RCA Verified ✓' badge visible, "
        "Noether's brief verdict text, and the 'Raise Ticket' button now active."
    )

    add_subheading(doc, "5.2  Agents in This Workflow")
    add_agent_table(doc, [
        ("Feynman",
         "Root Cause Analyst. Investigates a failed test by hypothesising a cause, "
         "drafting diagnostic code, running it in a sandbox, and declaring a root cause.",
         "Failed test details (table, column, test name, expected vs actual), "
         "table schema and statistics, prior run results.",
         "A root-cause hypothesis, evidence summary, and a remediation key from: "
         "re_baseline_reference, exclude_post_outcome_cols, extend_history, "
         "data_engineering_fix, segment_recalibration."),
        ("Noether",
         "Independent RCA Checker (effective challenge). Reviews Feynman's conclusion "
         "before any ticket is raised, accepting it or sending it back for revision.",
         "Feynman's root-cause analysis (proposed cause, evidence, reasoning), "
         "the original failed-test finding, and relevant data context.",
         "A verdict: accept (RCA is sound) or revise (with specific issues and a suggestion "
         "for Feynman to address)."),
    ])

    add_info_note(doc,
        "The HITL code-approval gate is a hard stop — Feynman cannot run any diagnostic "
        "code without your explicit approval. You may reject the proposed code as many times "
        "as needed. Feynman will adjust its approach each time."
    )

    doc.add_page_break()


# ── SECTION 6 — WORKFLOW 5: ISSUE MANAGEMENT ──────────────────────────────────

def build_issue_management(doc):
    add_heading(doc, "6.  Workflow ⑤ — Issue Management", 1)
    add_body(doc,
        "Once an RCA has been verified by Noether, you can raise a formal remediation ticket. "
        "The ticket carries an immutable context block (table, column, test, threshold) that "
        "cannot be altered after creation. You then manage the ticket through three statuses: "
        "Raised → Under Review → Closed."
    )

    add_subheading(doc, "6.1  Raising a Ticket")
    add_step(doc, 1,
             "From the verified RCA page, click Raise Ticket.",
             "A ticket-creation form appears, pre-filled with the root cause and remediation "
             "text from Feynman.")
    add_screenshot_placeholder(doc,
        "The Raise Ticket form: pre-filled fields for table, column, test name, root cause, "
        "and recommended remediation. An 'Owner' dropdown and 'Submit' button are visible."
    )

    add_step(doc, 2,
             "Assign an owner from the dropdown, add any notes, and click Submit.",
             "A ticket is created with a reference number (IDQ-NNN) and status Raised. "
             "The ticket appears in Issue Management.")

    add_subheading(doc, "6.2  Managing Tickets")
    add_step(doc, 1,
             "Navigate to Issue Management from the left-hand menu.",
             "A table lists all tickets with their ID, table, column, test, status, owner, and date.")
    add_screenshot_placeholder(doc,
        "The Issue Management table: columns showing Ticket ID (IDQ-NNN), Table, Column, "
        "Test, Status badge, Owner, and Date. Filter dropdowns visible at the top."
    )

    add_step(doc, 2,
             "Click a ticket row to open its details.",
             "The full ticket panel shows the immutable issue context (table, column, test, "
             "threshold), the root cause, remediation, and a transaction log of status changes.")

    add_step(doc, 3,
             "To advance the ticket status, click the appropriate action button: "
             "Move to Under Review or Mark as Closed.",
             "The status updates and a new entry appears in the transaction log.")
    add_screenshot_placeholder(doc,
        "The ticket detail panel: immutable context block at the top (greyed out), "
        "root cause and remediation text below, status action buttons, and transaction log at the bottom."
    )

    add_info_note(doc,
        "The issue context block (table, column, test, threshold) is locked at creation and "
        "cannot be edited after a ticket is raised. This preserves the audit trail."
    )

    doc.add_page_break()


# ── SECTION 7 — TEST LAB ──────────────────────────────────────────────────────

def build_test_lab(doc):
    add_heading(doc, "7.  Test Lab", 1)
    add_body(doc,
        "The Test Lab is a design workbench for authoring and managing reusable DQ tests "
        "independent of a specific validation run. It consists of five steps: "
        "Scope & Domains → Recommendations → Designer → Catalogue → Monitoring Schedules."
    )

    add_subheading(doc, "7.1  Step-by-Step Overview")
    add_step(doc, 1,
             "Navigate to Test Lab from the left-hand menu.",
             "The five-step wizard appears. Step 1 (Scope & Domains) is active.")
    add_screenshot_placeholder(doc,
        "The Test Lab five-step wizard header bar: steps labelled "
        "Scope & Domains, Recommendations, Designer, Catalogue, Monitoring Schedules. Step 1 active."
    )

    add_step(doc, 2,
             "Step 1 — Scope & Domains: Select the databases and DQ framework families "
             "you want to cover, then click Next.",
             "The selected scope is saved to a named Design Workbench session.")

    add_step(doc, 3,
             "Step 2 — Recommendations: Review AI-suggested tests for your scope. "
             "The AI runner surfaces tests from the library and optionally authors new ones via Gauss.",
             "Two panels appear: existing-library recommendations and AI-authored suggestions. "
             "A coverage indicator shows how many DQ categories are covered.")
    add_screenshot_placeholder(doc,
        "The Recommendations step: two-panel layout showing library matches on the left "
        "and AI-authored suggestions on the right, with category coverage bars."
    )
    add_hitl_note(doc,
        "AI-authored tests in the Recommendations step require your approval before they "
        "can be added to the catalogue. Click Review on any AI-authored test to see the "
        "code, rationale, and dry-run result, then Approve or Reject."
    )

    add_step(doc, 4,
             "Step 3 — Designer: Select a test from your recommendations and refine it. "
             "Adjust thresholds, parameters, and review the AI-generated Dossier.",
             "The Designer panel shows the test code, threshold sliders, and the Dossier: "
             "a structured document with rationale, quantitative outcome, monitoring recommendation, "
             "and interpretation guidance.")
    add_screenshot_placeholder(doc,
        "The Designer panel: test code editor on the left, Dossier sections on the right "
        "(Rationale, Quantitative Outcome, Strategy, Monitor Recommended)."
    )

    add_step(doc, 5,
             "Step 4 — Catalogue: Review all designed tests in a consolidated list. "
             "Download the PDF report for documentation or sign-off.",
             "The Catalogue shows each test with its dossier summary. "
             "A Download Report button generates a PDF of all tests.")
    add_screenshot_placeholder(doc,
        "The Catalogue step: table of designed tests with name, category, table/column, "
        "status, and a Download Report button at the top right."
    )

    add_step(doc, 6,
             "Step 5 — Monitoring Schedules: Configure how often the designed tests should run. "
             "Set frequency, delivery channel (download or email preview), and recipients.",
             "A schedule configuration form appears. Schedules are saved but not automatically "
             "executed — they must be triggered manually.")
    add_screenshot_placeholder(doc,
        "The Monitoring Schedules configuration form: frequency dropdown, delivery channel "
        "selector, recipient email field, and Enable/Save buttons."
    )

    doc.add_page_break()


# ── SECTION 8 — FULL AGENT REFERENCE ──────────────────────────────────────────

def build_agent_reference(doc):
    add_heading(doc, "8.  Full Agent Reference", 1)
    add_body(doc,
        "The table below lists every AI agent in Aegis Labs, the workflow it belongs to, "
        "and a concise description of its role. All agents run inside the Agent Console — "
        "you never interact with them directly except at HITL gates."
    )

    add_agent_table(doc, [
        ("Newton",    "Data Sourcing (①)",         "Reads profiled schema statistics and writes a plain-English database understanding summary.",           "Two structured paragraphs: statistical overview + DQ profiling summary."),
        ("Merton",    "Data Sourcing (①)",         "Credit-risk SME. Assesses potential usages of the database across analytics, modelling, and reporting.", "One paragraph of potential-usage assessment."),
        ("Poincaré",  "Define Test Plan (②)",       "Discovers candidate statistical relationships within the selected table.",                               "Candidate relationship list with confidence scores."),
        ("Fermat",    "Define Test Plan (②)",       "Challenges each Poincaré candidate using within-table evidence and domain logic.",                       "Accept / reject / revise verdict with challenge explanation per candidate."),
        ("Euler",     "Define Test Plan (②)",       "Challenges candidates for cross-table and global-domain consistency.",                                   "Accept / reject verdict with reason per candidate."),
        ("Hypatia",   "Define Test Plan (②)",       "Screens the test library to find and rank applicable tests for the selected column.",                    "Ranked test recommendations with confidence and reason."),
        ("Gauss",     "Define Test Plan (②)",       "Orchestrator and final test author. Synthesises all prior signals into one executable DQ test.",         "Complete test artifact: name, Python code, thresholds, rationale, interpretation."),
        ("Pascal",    "Define Test Plan (②)",       "Ranks test instances by business criticality (regulatory, model-risk, downstream impact).",              "Ordered ranking list; deterministic code converts to High / Medium / Low."),
        ("Bayes",     "Define Test Plan (②)",       "Adjudicates human feedback on a Gauss-authored test — accommodates valid feedback or pushes back.",      "Verdict, message, reasoning, and proposed changes."),
        ("Laplace",   "Run Validations (③)",        "Deterministic health scorer. No AI calls — converts run results to a criticality-weighted 0–100 score.", "final_score (0–100) and per-category breakdown."),
        ("Feynman",   "Root Cause Analysis (④)",    "Investigates failed tests: hypothesises cause, proposes diagnostic code (HITL gate), declares root cause.", "Root-cause hypothesis, evidence, and remediation key."),
        ("Noether",   "Root Cause Analysis (④)",    "Independent RCA checker. Challenges Feynman's conclusion before any ticket is raised.",                  "Verdict: accept or revise (with issues and suggestion)."),
        ("Codd",      "Data Sourcing / Test Plan",  "Relationship Architect. Infers cross-table joins and builds an ERD from schema evidence.",               "Cross-table join candidates and ERD structure."),
    ])

    doc.add_page_break()


# ── SECTION 9 — TIPS & TROUBLESHOOTING ────────────────────────────────────────

def build_tips(doc):
    add_heading(doc, "9.  Tips & Troubleshooting", 1)

    add_subheading(doc, "9.1  Common Questions")
    add_two_col_table(doc, "Question", "Answer", [
        ("Why does Newton produce the same summary every time I regenerate?",
         "This is intentional. Newton runs at temperature 0.0 — identical inputs always produce "
         "identical outputs. This ensures reproducibility and auditability."),
        ("Can I change a test after the plan is finalised?",
         "No. A finalised plan is locked. To make changes, reset the plan for that table, "
         "rebuild it, and finalise again."),
        ("How many times can I reject Feynman's proposed code?",
         "As many times as needed. Each rejection gives Feynman a chance to revise its approach. "
         "There is no hard limit, but if Feynman exhausts its diagnostic options it will declare "
         "the best available conclusion."),
        ("What does the health score mean?",
         "A score of 100 means every test passed. Scores below 100 reflect failures weighted by "
         "criticality — a single High-criticality failure has a larger impact than several Low ones."),
        ("Why can I not edit the issue context on a ticket?",
         "The issue context (table, column, test, threshold) is immutable by design to preserve "
         "the audit trail. Only the status, owner, and notes can change after creation."),
        ("What happens if the Agent Console stays open for a long time?",
         "Long AI operations (Gauss authoring, complex RCA) can take 1–2 minutes. The console "
         "will close automatically when the agent finishes. Do not navigate away while it is open."),
    ])

    add_subheading(doc, "9.2  Data Sourcing Tips")
    add_body(doc, "• Only databases listed in the catalogue can be ingested — you cannot connect an arbitrary database via the UI.")
    add_body(doc, "• You can re-ingest a database at any time to refresh its schema. This does not affect existing test plans.")
    add_body(doc, "• If the Agent Console appears stuck, wait at least 60 seconds before assuming an error. AI responses can be slow on first call.")

    add_subheading(doc, "9.3  Test Plan Tips")
    add_body(doc, "• Set thresholds conservatively on first use — you can always tighten them after reviewing real run results.")
    add_body(doc, "• The criticality assigned by Pascal can be overridden manually before finalising.")
    add_body(doc, "• Gauss-authored tests always run a dry-run against real data before presenting for HITL review. A dry-run failure means the authored code did not execute successfully — Gauss will substitute a verified fallback in that case.")

    add_subheading(doc, "9.4  RCA Tips")
    add_body(doc, "• Read Feynman's rationale carefully before approving diagnostic code. The rationale explains what the code is looking for and why.")
    add_body(doc, "• If Noether sends the RCA back for revision, the revised root cause will be re-checked automatically — you do not need to trigger Noether manually.")
    add_body(doc, "• You can raise at most one ticket per failed test instance. If you need to re-investigate, reset the RCA session.")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    doc = Document()

    # Page margins
    from docx.oxml.ns import qn as _qn
    from docx.oxml import OxmlElement as _OxmlElement
    for section in doc.sections:
        section.top_margin    = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    # Default body font
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    build_cover(doc)
    build_intro(doc)
    build_data_sourcing(doc)
    build_test_plan(doc)
    build_run_validations(doc)
    build_rca(doc)
    build_issue_management(doc)
    build_test_lab(doc)
    build_agent_reference(doc)
    build_tips(doc)

    doc.save(OUT_PATH)
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    main()
