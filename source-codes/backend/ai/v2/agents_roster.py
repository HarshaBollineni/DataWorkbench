"""Galileo v2 agent roster — single source of truth for the AI Agents module.

The `key` values match the `agent` field emitted on the v2 SSE streams
(routers/v2.py), so the frontend can label live console events and the
roster page from the same registry. Purely descriptive: nothing here
changes runtime behaviour.
"""
from __future__ import annotations

STAGES = [
    {"key": "sourcing", "title": "Data Sourcing", "route": "/data-sourcing"},
    {"key": "testlab", "title": "Test Lab", "route": "/test-lab"},
    {"key": "issues", "title": "Issue Management", "route": "/issues"},
    {"key": "reporting", "title": "Reporting", "route": "/test-lab"},
]

# autonomy: autonomous | human_gated | on_demand
# engine:   deterministic | ai_assisted
AGENTS = [
    {
        "key": "data_profiling",
        "name": "Data Profiler",
        "stage": "sourcing",
        "seq": 1,
        "autonomy": "autonomous",
        "engine": "deterministic",
        "role": "Reads every uploaded table and data dictionary, then classifies each variable into the inventory that drives all downstream testing.",
        "reads": [
            "Uploaded tables (CSV / Excel)",
            "Data dictionaries",
            "Target variable and use case (dataset uploads)",
        ],
        "produces": [
            "Variable inventory: type, role and profile per column",
            "Per-table row and column statistics",
        ],
        "guardrails": [
            "Read-only on your data — profiling never alters a single value",
            "Recomputed from source on demand, never stale",
        ],
        "checkpoint": "You review the variable inventory and finalize the item before any testing begins.",
        "meet": "Data Sourcing wizard",
    },
    {
        "key": "test_planning",
        "name": "Test Planner",
        "stage": "testlab",
        "seq": 2,
        "autonomy": "human_gated",
        "engine": "deterministic",
        "role": "Maps the governed DQ framework onto your actual tables — choosing the right columns and thresholds for every applicable test.",
        "reads": [
            "The DQ framework test registry",
            "Variable inventory from the Data Profiler",
        ],
        "produces": [
            "A per-table test plan with chosen columns, params and thresholds",
            "A transparent not-applicable list with reasons",
        ],
        "guardrails": [
            "Framework-driven: only tests defined in the governed framework are planned",
            "Column and parameter choices stay editable until you finalize",
        ],
        "checkpoint": "You adjust columns and parameters, then finalize the plan.",
        "meet": "Test Lab — Step 1",
    },
    {
        "key": "snippet_generation",
        "name": "Snippet Author",
        "stage": "testlab",
        "seq": 3,
        "autonomy": "human_gated",
        "engine": "deterministic",
        "role": "Writes the executable pandas snippet for each planned test, ready for your review.",
        "reads": [
            "The finalized test plan",
            "Variable inventory (column names and types)",
        ],
        "produces": [
            "One editable code snippet per planned test",
        ],
        "guardrails": [
            "Sandbox-safe imports only: pandas, numpy, scipy, math, statistics",
            "Nothing runs until you approve it",
            "Your hand-edits are preserved — never silently regenerated",
        ],
        "checkpoint": "You review, edit and approve every snippet before execution.",
        "meet": "Test Lab — Step 2",
    },
    {
        "key": "snippet_execution",
        "name": "Sandbox Executor",
        "stage": "testlab",
        "seq": 4,
        "autonomy": "autonomous",
        "engine": "deterministic",
        "role": "Runs your approved snippets table by table inside a locked-down sandbox and records pass, fail or could-not-assess for each test.",
        "reads": [
            "Approved snippets only",
            "The uploaded data",
        ],
        "produces": [
            "Test results with metric vs. threshold per test",
            "Violation counts and could-not-assess reasons",
        ],
        "guardrails": [
            "Restricted sandbox: import allowlist and safe builtins only",
            "One table failing never stops the next table",
            "Never mutates your data",
        ],
        "checkpoint": None,
        "meet": "Test Lab — Step 2",
    },
    {
        "key": "recommendation",
        "name": "Recommendation Agent",
        "stage": "testlab",
        "seq": 5,
        "autonomy": "human_gated",
        "engine": "deterministic",
        "role": "Checks the framework results against CRE and use-case business rules, cross-validates its own suggestions, and proposes incremental tests worth adding.",
        "reads": [
            "Framework test results",
            "The business-rule library (CRE, IFRS 9 / IRB stress-test rules)",
        ],
        "produces": [
            "Prioritized incremental test recommendations",
            "A cross-validation note per recommendation",
        ],
        "checkpoint": "You accept or reject each recommendation before it joins the plan.",
        "guardrails": [
            "Every suggestion is cross-validated before you see it",
            "Rejected recommendations never enter the plan",
        ],
        "meet": "Test Lab — Step 3",
    },
    {
        "key": "rca",
        "name": "RCA Agent",
        "stage": "issues",
        "seq": 6,
        "autonomy": "human_gated",
        "engine": "ai_assisted",
        "role": "Diagnoses each failed test: the likely cause, remediation options, and further analyses that would sharpen the picture.",
        "reads": [
            "The failing test result (metric, threshold, columns)",
            "The variable profile of the affected columns",
            "Framework remediation guidance and caveats",
        ],
        "produces": [
            "A likely cause citing your actual columns and numbers",
            "Solutions tagged Framework-guidance or AI-generated",
            "Suggested additional analyses",
        ],
        "guardrails": [
            "Framework remediation guidance is the primary source; AI fallbacks are explicitly tagged",
            "Regenerated fresh on every open — never a stale cache",
            "Never mutates data or re-runs tests",
            "Additional analyses go through the full snippet approval flow",
        ],
        "checkpoint": "You close the issue with a rationale, or raise a tracked issue.",
        "meet": "Issue Management — RCA screen",
    },
    {
        "key": "report",
        "name": "Report Builder",
        "stage": "reporting",
        "seq": 7,
        "autonomy": "on_demand",
        "engine": "deterministic",
        "role": "Assembles the assessment PDF on demand — full-framework results, laid out per table for databases — reflecting the exact state at the moment you download.",
        "reads": [
            "Current test results and DQ score",
            "The issue register and could-not-assess list",
        ],
        "produces": [
            "A downloadable PDF snapshot of the assessment",
        ],
        "guardrails": [
            "Pure snapshot: builds from current state, persists and alters nothing",
        ],
        "checkpoint": None,
        "meet": "Test Lab — Step 4 and Issue screens",
    },
]


def roster() -> dict:
    return {"stages": STAGES, "agents": AGENTS}
