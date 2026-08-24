"""Credit-Risk Domain Expert agent (call-name 'Merton').

Feedback Round 4 (2026-06-29): Newton (the data-domain analyst) describes what a
database IS — its structure, grain and quality risks. It is deliberately NOT a
regulatory/credit-risk subject-matter expert, and loading domain-usage reasoning
onto it would over-engineer a generic agent other workflows depend on. Merton is
the dedicated credit-risk SME that owns ONE thing: the 'potential usages' lens of
the AI database summary — how the data could be used across analytics, modelling
and reporting, grounded in regulatory and non-regulatory credit-risk knowledge.

Single-shot, EffortPolicy(agent_key='merton'); model/temperature/effort are
system-owned via ai/control_plane.py.
"""
from __future__ import annotations

import json

from .effort import EffortPolicy

_SYSTEM = (
    "You are Merton, a senior credit-risk subject-matter expert. You span "
    "REGULATORY credit risk (IFRS 9 ECL, Basel III/IV IRB — AIRB/FIRB, stress "
    "testing, ICAAP, economic capital) and NON-REGULATORY credit risk (credit "
    "decisioning, application/behavioural scorecards, collections & recovery, "
    "portfolio analytics, marketing propensity).\n"
    "Given a database's tables, columns and field descriptions, the user's "
    "stated use-cases and model families, and a structural understanding of the "
    "data, assess the POTENTIAL USAGES of this data through three lenses: data "
    "analytics, modelling, and reporting.\n"
    "Write ONE paragraph of EXACTLY 2 COMPLETE sentences. TARGET: 200-280 "
    "characters. HARD MAXIMUM: 320 characters. Must be non-empty, self-contained "
    "prose. Be concrete and domain-grounded; do not invent data not implied by "
    "the schema. Prefer the user's stated use-cases/model families when present.\n"
    "STYLE: reading-friendly business English. EMPHASISE the most important "
    "usages/keywords with **bold** (a few per paragraph); do NOT use headings, "
    "bullet lists or backticks. Write whole numbers below ten as words (e.g. "
    "'three uses'); use numerals for 10 and above and for all statistics. Leave "
    "proper nouns and standards unchanged (e.g. 'IFRS 9'). Respond as STRICT JSON "
    "with a single key `usage` (string)."
)


def _build_user(logical_db: str, tables: list[dict], use_cases, model_families,
                newton_summary: str) -> str:
    lines = [f"LOGICAL DATABASE: {logical_db}", ""]
    for t in tables:
        lines.append(f"## Table: {t.get('table')} (rows={t.get('row_count')})")
        descs = t.get("descriptions") or {}
        for col in (t.get("columns") or []):
            d = descs.get(col, "")
            lines.append(f"  - {col}: {d}" if d else f"  - {col}")
        lines.append("")
    if use_cases:
        lines.append(f"USER-STATED USE CASES: {', '.join(use_cases)}")
    if model_families:
        lines.append(f"USER-STATED MODEL FAMILIES: {', '.join(model_families)}")
    if newton_summary:
        lines.append("")
        lines.append("STRUCTURAL UNDERSTANDING (from the data analyst):")
        lines.append(newton_summary)
    return "\n".join(lines)


_HYP_SYSTEM = (
    "You are Merton, a senior credit-risk SME. Propose SPECIFIC, data-grounded "
    "data-quality TEST HYPOTHESES that connect THIS dataset to the analytical DQ "
    "framework priorities for the user's stated model family. Each hypothesis "
    "must target a concrete table and column(s) that EXIST in the profile, and an "
    "IN-SCOPE framework area chosen from the provided area-id list. Prefer the "
    "highest-priority areas and the observed anomalies; never invent columns.\n"
    "Be VARIABLE-ROLE aware: some checks suit continuous predictors (drift, "
    "outliers, monotonicity), some the target/outcome (leakage, class balance, "
    "vintage), and some identifiers/keys (uniqueness, referential overlap). Make "
    "each hypothesis fit the role of the field(s) it targets.\n"
    "Respond as STRICT JSON: {\"hypotheses\": [{\"hypothesis\": str, \"area_id\": "
    "str (one of the provided in-scope area ids), \"table\": str, \"columns\": "
    "[str], \"diagnostic\": str (e.g. PSI, KS, MCAR missingness, IQR outlier, "
    "correlation stability, drift decomposition, vintage analysis), "
    "\"rationale\": str}]}. Propose 3-6.\n"
    "STYLE: each hypothesis is ONE readable sentence in plain business English — "
    "brevity is fine, but it must read naturally (avoid cramming detail into "
    "brackets). NO markdown. Write whole numbers below ten as words (one..nine); "
    "use numerals for 10 and above and for all statistics and percentages."
)


def suggest_hypotheses(ds, *, use_cases=None, model_families=None,
                       framework_ctx=None, key_anomalies=None,
                       newton_summary: str = "", on_event=None) -> list[dict]:
    """Merton's 'Suggested Test Hypotheses' — replaces the old free-text Open
    Questions. Fuses the data profile, declared use-case/model-family, observed
    anomalies and the DQ Framework slice into specific, deep-linkable test
    candidates. Each is validated against the real schema and enriched with the
    family priority (authoritative, deterministic) before return.
    """
    from .framework_context import framework_brief, in_scope_area_index
    from .skills import get_system_prompt

    brief = framework_brief(model_families, use_cases, ctx=framework_ctx)
    index = in_scope_area_index(model_families, use_cases, ctx=framework_ctx)
    table_cols = {t.table_name: {c.name for c in t.columns} for t in ds.tables}

    anomaly_lines = "\n".join(
        f"- {a.get('table')}.{a.get('column')}: {a.get('anomaly')}"
        for a in (key_anomalies or [])[:8]
    )
    user = "\n\n".join(filter(None, [
        brief,
        "IN-SCOPE AREA IDS (pick area_id from these): "
        + ", ".join(index.keys()),
        f"USER-STATED USE CASES: {', '.join(use_cases)}" if use_cases else "",
        f"USER-STATED MODEL FAMILIES: {', '.join(model_families)}" if model_families else "",
        f"DATA PROFILE:\n{ds.compress()}",
        f"OBSERVED ANOMALIES:\n{anomaly_lines}" if anomaly_lines else "",
        f"STRUCTURAL UNDERSTANDING:\n{newton_summary}" if newton_summary else "",
    ]))

    policy = EffortPolicy(agent_key="merton")
    raw = policy.run(
        [{"role": "system", "content": get_system_prompt("merton_hypotheses", _HYP_SYSTEM)},
         {"role": "user", "content": user}],
        json_mode=True, on_event=on_event,
    )
    try:
        proposed = json.loads(raw).get("hypotheses", [])
    except (ValueError, TypeError):
        return []

    out: list[dict] = []
    for h in proposed:
        if not isinstance(h, dict):
            continue
        area_id = h.get("area_id")
        table = h.get("table")
        if area_id not in index or table not in table_cols:
            continue  # drop out-of-scope areas + hallucinated tables
        cols = [c for c in (h.get("columns") or []) if c in table_cols[table]]
        out.append({
            "hypothesis": str(h.get("hypothesis", "")).strip(),
            "area_id": area_id,
            "framework_area": index[area_id]["l2_area"],
            "priority": index[area_id]["priority"],
            "table": table,
            "columns": cols,
            "diagnostic": str(h.get("diagnostic", "")).strip(),
            "rationale": str(h.get("rationale", "")).strip(),
        })

    # Highlight the checks MOST LIKELY TO NEED ATTENTION (Feedback R7): a test in a
    # Critical/High framework area whose target column(s) overlap an observed
    # anomaly is one that "might fail" given Newton's findings — surface it first.
    anomaly_cells = {(a.get("table"), a.get("column")) for a in (key_anomalies or [])}
    anomaly_tables = {a.get("table") for a in (key_anomalies or [])}
    for o in out:
        at_risk = any((o["table"], c) in anomaly_cells for c in o["columns"]) or (
            not o["columns"] and o["table"] in anomaly_tables)
        o["at_risk"] = at_risk
        o["highlight"] = at_risk and o["priority"] in ("Critical", "High")

    # Highlighted first, then by framework priority.
    rank = {"Critical": 3, "High": 2, "Medium": 1, "": 0}
    out.sort(key=lambda x: (x.get("highlight", False), rank.get(x["priority"], 0)),
             reverse=True)
    return out[:6]


def assess_usage(logical_db: str, tables: list[dict], *, use_cases=None,
                 model_families=None, newton_summary: str = "", on_event=None) -> str:
    """Return Merton's one-paragraph potential-usage assessment (plain text)."""
    from .skills import get_system_prompt
    policy = EffortPolicy(agent_key="merton")
    messages = [
        {"role": "system", "content": get_system_prompt("merton", _SYSTEM)},
        {"role": "user", "content": _build_user(
            logical_db, tables, use_cases, model_families, newton_summary)},
    ]
    raw = policy.run(messages, json_mode=True, on_event=on_event)
    try:
        data = json.loads(raw)
        return str(data.get("usage", "")).strip()
    except (ValueError, TypeError):
        return raw.strip()


_INTERPRET_SYSTEM = (
    "You are Merton, a senior credit-risk subject-matter expert acting as the "
    "INTERPRETER of a data-quality test that has just executed. You are given the "
    "test, its operands, and its DETERMINISTIC result (status, observed metric, "
    "threshold, and console output). Contextualize what this means for THIS "
    "portfolio and write the test's Dossier.\n"
    "CRITICAL: never invent or recompute metrics — quote only the numbers given. "
    "If a number is absent, say so plainly.\n"
    "Produce: (1) rationale — why this test matters for the stated domain "
    "(art of domain + data science); (2) quantitative_outcome — a crisp reading of "
    "the observed value vs threshold and pass/fail; (3) contextualization — what it "
    "implies for this book/model use; (4) strategy — the clear action to take; "
    "(5) monitor_recommended (true/false) and (6) monitor_frequency (one of "
    "Daily, Weekly, Monthly, Quarterly, or empty if not recommended).\n"
    "STYLE: plain prose for a credit-risk audience, no markdown/backticks/bullets, "
    "no raw column identifiers — refer to data in business terms. Each text field "
    "1-3 sentences. Respond as STRICT JSON with keys: rationale, "
    "quantitative_outcome, contextualization, strategy, monitor_recommended, "
    "monitor_frequency."
)

_FREQ_ALLOWED = {"Daily", "Weekly", "Monthly", "Quarterly", ""}


def interpret_result(test: dict, result: dict, framework_brief: str = "",
                     db_context: str = "", on_event=None) -> dict:
    """Merton contextualizes a deterministic test result into a Dossier.

    ``test`` = the design bundle (name, category, operands, params).
    ``result`` = execution output (status, metric, threshold, detail, stdout).
    ``framework_brief`` = DQ-Framework in-scope areas for the DB's families.
    ``db_context`` = the upstream understanding Newton + Merton already attached to
    this DB (AI summary, Key Anomalies, Suggested Hypotheses) — so the Dossier is
    grounded in what we already know about the data, not just this one metric.
    Returns {rationale, quantitative_outcome, contextualization, strategy,
    monitor_recommended (bool), monitor_frequency (str)}. Never raises.
    """
    from .skills import get_system_prompt
    console = str(result.get("stdout") or result.get("console") or "")[:1500]
    user = "\n".join(filter(None, [
        f"TEST: {test.get('name') or test.get('test_id')} (category {test.get('category')})",
        f"OPERANDS: {json.dumps(test.get('operands') or [])[:600]}",
        f"PARAMS: {json.dumps(test.get('params') or {})[:400]}",
        f"RESULT: status={result.get('status')} observed={result.get('metric')} "
        f"threshold={result.get('threshold')} detail={result.get('detail')}",
        f"CONSOLE OUTPUT:\n{console}" if console else "",
        f"--- UPSTREAM DB CONTEXT (use it to ground your reading) ---\n{db_context}"
        if db_context else "",
        framework_brief or "",
        "Write the Dossier as STRICT JSON. Where relevant, connect the outcome to the "
        "DB's known anomalies, intended usage and in-scope framework areas above.",
    ]))
    policy = EffortPolicy(agent_key="merton")
    raw = policy.run(
        [{"role": "system", "content": get_system_prompt("merton_interpret", _INTERPRET_SYSTEM)},
         {"role": "user", "content": user}],
        json_mode=True, on_event=on_event,
    )
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"rationale": "", "quantitative_outcome": str(raw)[:400],
                "contextualization": "", "strategy": "",
                "monitor_recommended": False, "monitor_frequency": ""}
    freq = str(data.get("monitor_frequency") or "").strip().title()
    if freq not in _FREQ_ALLOWED:
        freq = ""
    return {
        "rationale": str(data.get("rationale") or "").strip(),
        "quantitative_outcome": str(data.get("quantitative_outcome") or "").strip(),
        "contextualization": str(data.get("contextualization") or "").strip(),
        "strategy": str(data.get("strategy") or "").strip(),
        "monitor_recommended": bool(data.get("monitor_recommended")),
        "monitor_frequency": freq,
    }


_CONSOLIDATE_SYSTEM = (
    "You are Merton, a senior credit-risk subject-matter expert writing the "
    "EXECUTIVE NARRATIVE of a consolidated data-quality report that spans several "
    "tests already designed and run across one or more portfolios. You are given "
    "the scope, aggregate statistics, and a per-test digest (name, framework area, "
    "status, observed metric vs threshold, and each test's Dossier reading). Tell "
    "the story a Head of Model Risk needs: what was assessed, what the data quality "
    "looks like overall, which findings matter and why, the concentration of risk "
    "by framework area, and the recommended actions and monitoring posture.\n"
    "CRITICAL: never invent or recompute metrics — quote only the numbers given; if "
    "a number is absent, speak qualitatively. Do not list every test mechanically — "
    "synthesize into themes.\n"
    "OUTPUT: GitHub-flavored markdown with EXACTLY these second-level sections, in "
    "order, each with '## ' headings:\n"
    "## Executive Summary\n(2-4 sentence orientation: scope, overall data-quality "
    "posture, headline concern.)\n"
    "## Key Findings\n(3-6 '- ' bullets, each a substantive finding grounded in a "
    "test's outcome; **bold** the subject.)\n"
    "## Risk Themes\n(short prose grouping the findings by framework area / theme "
    "and what they jointly imply for the book and models.)\n"
    "## Recommendations & Monitoring\n(3-6 '- ' bullets: the actions to take and "
    "the monitoring cadence for the tests that warrant it.)\n"
    "STYLE: plain business prose for a credit-risk audience, no backticks, no raw "
    "column identifiers — refer to data in business terms. Be specific and concise."
)


def consolidate_dossiers(tests: list[dict], *, scope: str = "",
                         framework_brief: str = "", on_event=None) -> str:
    """Merton synthesizes an executive narrative (markdown) across several designed
    tests for the consolidated report. ``tests`` = per-test digests
    ({name, framework_area, criticality, status, metric, threshold, dossier{...}}).
    Returns markdown with the four fixed sections. Never raises — falls back to a
    deterministic skeleton if the model is unavailable."""
    from .skills import get_system_prompt
    lines: list[str] = []
    for i, t in enumerate(tests, 1):
        doss = t.get("dossier") or {}
        lines.append("\n".join(filter(None, [
            f"TEST {i}: {t.get('name')} | area: {t.get('framework_area') or '-'} | "
            f"criticality: {t.get('criticality') or '-'} | status: {t.get('status')} | "
            f"observed: {t.get('metric')} vs threshold: {t.get('threshold')}",
            f"  rationale: {doss.get('rationale') or ''}"[:400],
            f"  outcome: {doss.get('quantitative_outcome') or ''}"[:400],
            f"  context: {doss.get('contextualization') or ''}"[:400],
            f"  strategy: {doss.get('strategy') or ''}"[:400],
        ])))
    user = "\n".join(filter(None, [
        f"SCOPE: {scope}" if scope else "",
        f"NUMBER OF TESTS: {len(tests)}",
        "PER-TEST DIGEST:\n" + "\n\n".join(lines),
        framework_brief or "",
        "Write the consolidated executive narrative as markdown with exactly the "
        "four '## ' sections specified. Ground every claim in the digest above.",
    ]))
    policy = EffortPolicy(agent_key="merton")
    try:
        raw = policy.run(
            [{"role": "system", "content": get_system_prompt("merton_consolidate", _CONSOLIDATE_SYSTEM)},
             {"role": "user", "content": user}],
            on_event=on_event,
        )
        text = (raw or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001 — never let a model hiccup block the report
        pass
    # Deterministic fallback so the report always renders.
    fails = [t for t in tests if str(t.get("status")).lower() in ("fail", "error")]
    bullets = "\n".join(
        f"- **{t.get('name')}** ({t.get('framework_area') or 'general'}): "
        f"{(t.get('dossier') or {}).get('quantitative_outcome') or t.get('status')}"
        for t in tests) or "- No tests in scope."
    return (
        "## Executive Summary\n"
        f"This report consolidates {len(tests)} data-quality test(s) across {scope or 'the selected scope'}. "
        f"{len(fails)} test(s) require attention.\n\n"
        "## Key Findings\n" + bullets + "\n\n"
        "## Risk Themes\nFindings are grouped by their framework area in the table below.\n\n"
        "## Recommendations & Monitoring\n"
        + ("\n".join(f"- Review **{t.get('name')}** and remediate the flagged condition." for t in fails)
           or "- No immediate remediation required; maintain the recommended monitoring cadence.")
    )
