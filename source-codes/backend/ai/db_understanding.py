"""Plan 3 / D2.6 — Database-Understanding Agent (call-name 'Newton').

Replays its understanding of an ingested logical DB: a structured summary +
assumptions + open questions, produced with EffortPolicy(high). Persisted to
``ingested_databases.ai_summary`` and to a system-prompt .md (AI memory) so the
understanding can be reviewed/edited and reloaded next run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .discovery_state import DiscoveryState, compute_discovery_state
from .effort import EffortPolicy
from .skills import SKILLS_DIR as _SKILLS_DIR  # persistent-volume aware (see skills.py)

_SYSTEM = (
    "You are Newton, a meticulous data-domain analyst for a model-risk data "
    "quality platform. Given a logical database's tables, columns, field "
    "descriptions and basic statistics, replay your understanding of the data in "
    "PLAIN ENGLISH prose. Be precise and domain-grounded; never invent columns.\n"
    "Respond as STRICT JSON with keys: stats_paragraph (string), "
    "profiling_paragraph (string).\n"
    "- `stats_paragraph`: ONE paragraph of EXACTLY 2-3 COMPLETE sentences. "
    "TARGET: 280-380 characters. HARD MAXIMUM: 400 characters. Cover: the "
    "database's business domain and subject matter, total tables and fields, "
    "total record count, key tables and their grain, primary analytical or "
    "regulatory purpose.\n"
    "- `profiling_paragraph`: ONE paragraph of EXACTLY 3-4 COMPLETE sentences. "
    "TARGET: 450-600 characters. HARD MAXIMUM: 650 characters. Cover: notable "
    "completeness and null patterns across key fields, distributions and value "
    "ranges for important numeric columns, cardinality observations for identifier "
    "and categorical fields, how tables logically relate, any derived or calculated "
    "fields worth flagging, and the most material data-quality risks to prioritise.\n"
    "Both paragraphs are REQUIRED and must be non-empty, self-contained prose "
    "(never a fragment, never a bullet, never empty). Fill the target range — "
    "do not pad, but do not cut short.\n"
    "STYLE: plain, reading-friendly business English. EMPHASISE the most important "
    "keywords with **bold** in every paragraph — key figures, notable risks, and "
    "any specific field you reference (written as **table.field**). Bold only what "
    "matters (a few terms per paragraph), and keep the surrounding sentence natural "
    "and easy to read (no telegraphic fragments). Do NOT use headings, bullet "
    "lists, or backticks.\n"
    "NUMBER STYLE (applies to every report): write whole numbers below ten as "
    "words — e.g. 'three tables', 'nine fields'. Use numerals for 10 and above and "
    "ALWAYS for statistics, percentages, decimals, years and identifiers. Leave "
    "proper nouns and standards unchanged (e.g. 'IFRS 9', 'Basel III')."
)

# Step-3 first-stab description (a short free-text blurb for the metadata form).
_DESCRIBE_SYSTEM = (
    "You are Newton, a data-domain analyst. Given a database's tables and "
    "columns, write a concise 2–3 sentence plain-English description of what "
    "this database contains and its likely business purpose. Plain prose only — "
    "NO markdown, NO bullet points, NO backticks, and do not print raw column "
    "names as code. Respond as STRICT JSON: {\"description\": string}."
)


_PARA_CAPS = {"stats": 400, "profiling": 650, "usage": 320}


def _cap_paragraph(text: str, max_chars: int) -> str:
    """Hard-cap a paragraph at max_chars, cutting at the last sentence boundary.

    Guards against LLMs ignoring the character-count instructions in the prompt.
    Finds the last '.', '!' or '?' before the cap so the truncated text ends
    on a complete sentence rather than mid-word.
    """
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for i in range(len(cut) - 1, max(len(cut) // 3, 0), -1):
        if cut[i] in ".!?" and (i + 1 >= len(cut) or cut[i + 1] == " "):
            return cut[: i + 1].strip()
    return cut.rstrip()


def _as_text(v) -> str:
    """Coerce a summary value to a newline string (lists -> bullet lines)."""
    if isinstance(v, list):
        return "\n".join(
            str(x) if str(x).lstrip().startswith("-") else f"- {x}" for x in v)
    return str(v or "")


def _as_list(v) -> list[str]:
    """Coerce assumptions/open_questions to a flat list of strings."""
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)] if v else []


def md_path(logical_db: str) -> Path:
    return _SKILLS_DIR / f"db_understanding_{logical_db}.md"


def discovery_state_path(logical_db: str) -> Path:
    return _SKILLS_DIR / f"db_discovery_{logical_db}.json"


def _build_user(logical_db: str, tables: list[dict], feedback: str | None) -> str:
    total_records = sum(int(t.get("row_count") or 0) for t in tables)
    total_fields = sum(len(t.get("columns") or []) for t in tables)
    lines = [
        f"LOGICAL DATABASE: {logical_db}",
        f"BASIC STATISTICS: {len(tables)} table(s), {total_fields} field(s), "
        f"{total_records} total record(s).",
        "",
    ]
    for t in tables:
        lines.append(f"## Table: {t.get('table')} "
                     f"(rows={t.get('row_count')}, pk={t.get('pk')}, date={t.get('date_col')})")
        descs = t.get("descriptions") or {}
        for col in (t.get("columns") or []):
            d = descs.get(col, "")
            lines.append(f"  - {col}: {d}")
        lines.append("")
    if feedback:
        lines.append(f"USER FEEDBACK to incorporate: {feedback}")
    return "\n".join(lines)


# ── Number style (Feedback R7): single-digit whole numbers spelled out ─────────
_NUM_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine"}


def num_word(n, cap: bool = False) -> str:
    """Whole numbers below ten as words (one..nine); numerals for 10+. Used in the
    deterministic report text so it obeys the same number style as the AI prose."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 0 <= n <= 9:
        w = _NUM_WORDS[n]
        return w.capitalize() if cap else w
    return str(n)


def _usages_phrase(use_cases, model_families) -> str:
    """A readable 'x, y and z' phrase of the intended usages for the hypotheses
    lead sentence (Feedback R7: link the tests back to the potential usages)."""
    items: list[str] = []
    for x in (model_families or []):
        if x and x not in items:
            items.append(str(x))
    if not items:
        for x in (use_cases or []):
            if x and x not in items:
                items.append(str(x))
    if not items:
        return "the analytical uses identified above"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def compose_summary(stats_paragraph: str, profiling_paragraph: str,
                    usage_paragraph: str, manual_paragraph: str = "") -> str:
    """Plain (header-less) join of the paragraphs — used internally to feed the
    downstream credit-risk agent a clean structural read (no markdown noise)."""
    paras = [stats_paragraph, profiling_paragraph, usage_paragraph, manual_paragraph]
    return "\n\n".join(p.strip() for p in paras if p and p.strip())


def compose_summary_md(stats_paragraph: str, profiling_paragraph: str,
                       usage_paragraph: str, manual_paragraph: str = "") -> str:
    """The summary shown/edited in the wizard and rendered in the report.

    Feedback R7: give the first two paragraphs (structure + profiling) a
    **Findings** header and the usage paragraph a **Potential Usages** header,
    both as markdown so keywords the agents bolded render properly.
    """
    lines: list[str] = []
    findings = [p.strip() for p in (stats_paragraph, profiling_paragraph) if p and p.strip()]
    if findings:
        lines.append("## Findings")
        lines.append("")
        lines.append("\n\n".join(findings))
    if usage_paragraph and usage_paragraph.strip():
        if lines:
            lines.append("")
        lines.append("## Potential Usages")
        lines.append("")
        lines.append(usage_paragraph.strip())
    if manual_paragraph and manual_paragraph.strip():
        if lines:
            lines.append("")
        lines.append(f"_{manual_paragraph.strip()}_")
    return "\n".join(lines)


def _build_manual_inputs_paragraph(use_cases, model_families, dictionary) -> str:
    """Deterministic ¶4 — only emitted when at least one manual input was provided.
    Phrases the analyst's contextual signals in business English."""
    clauses: list[str] = []
    if use_cases:
        uc = (", ".join(use_cases[:-1]) + " and " + use_cases[-1]
              if len(use_cases) > 1 else use_cases[0])
        clauses.append(f"use-case alignment with {uc}")
    if model_families:
        mf = (", ".join(model_families[:-1]) + " and " + model_families[-1]
              if len(model_families) > 1 else model_families[0])
        clauses.append(f"model-family coverage spanning {mf}")
    if isinstance(dictionary, dict):
        total = sum(len(v) for v in dictionary.values() if isinstance(v, dict))
        if total > 0:
            clauses.append(
                f"a structured data dictionary covering {total} "
                f"field description{'s' if total != 1 else ''}")
    if not clauses:
        return ""
    if len(clauses) == 1:
        highlights = clauses[0]
    elif len(clauses) == 2:
        highlights = f"{clauses[0]} and {clauses[1]}"
    else:
        highlights = ", ".join(clauses[:-1]) + f", and {clauses[-1]}"
    return (
        f"Further manual inputs from the analyst highlight {highlights} — "
        "these contextual signals have been factored into the understanding above."
    )


def build_key_anomalies(ds, ctx: dict, limit: int = 6) -> list[dict]:
    """Deterministic 'Key Anomalies' — replaces the old LLM 'Assumptions'. Drawn
    straight from DiscoveryState's column anomaly flags (NO LLM), each tagged to
    its framework area and weighted by that area's priority for the DB's declared
    model family. Sorted by anomaly severity, then family priority; top-N kept.
    """
    from .framework_context import (anomaly_severity, anomaly_severity_label,
                                    area_for_anomaly)
    area_meta = {a["area_id"]: a for a in ctx.get("prioritized_areas", [])}
    rank_pri = {"Critical": 3, "High": 2, "Medium": 1, "": 0}
    items: list[dict] = []
    for t in ds.tables:
        for c in t.columns:
            for a in c.anomalies:
                aid = area_for_anomaly(a)
                meta = area_meta.get(aid, {})
                items.append({
                    "table": t.table_name, "column": c.name, "anomaly": a,
                    "severity": anomaly_severity_label(a),
                    "area_id": aid, "framework_area": meta.get("l2_area", ""),
                    "family_priority": meta.get("priority", ""),
                    "_rank": (anomaly_severity(a),
                              rank_pri.get(meta.get("priority", ""), 0)),
                })
    items.sort(key=lambda x: x["_rank"], reverse=True)
    for it in items:
        it.pop("_rank", None)
    return items[:limit]


def _humanize_anomaly(anomaly: str) -> str:
    """Turn a deterministic anomaly CODE (e.g. 'null_high (73% null)') into a
    reading-friendly clause that follows the bold table.field (Feedback R7:
    "the sentences following TABLE.FIELD are not reading friendly")."""
    a = (anomaly or "").strip()
    token = a.split()[0] if a else ""
    m = re.search(r"\((.*)\)", a)
    detail = (m.group(1).strip() if m else "")
    if token == "null_critical":
        return "is completely empty — every value is missing, so the field carries no usable signal"
    if token == "null_high":
        return f"is heavily incomplete ({detail})" if detail else "is heavily incomplete"
    if token == "null_elevated":
        return f"has elevated missingness ({detail})" if detail else "has elevated missingness"
    if token == "constant_value":
        val = detail.lstrip("=").strip()
        return (f"holds a single constant value ({val}) across every row, offering no variation to analyse"
                if val else "holds a single constant value across every row, offering no variation to analyse")
    if token == "negative_values":
        d = detail.replace("min=", "min ") if detail else ""
        return f"contains implausible negative values ({d})" if d else "contains implausible negative values"
    if token == "range_violation":
        return f"falls outside its expected range ({detail})" if detail else "falls outside its expected range"
    if token == "extreme_outliers":
        d = detail.replace("n=", "").strip() if detail else ""
        return f"shows extreme outliers ({d})" if d else "shows extreme outliers"
    return a or "shows an anomaly worth reviewing"


def render_anomalies_md(key_anomalies: list[dict]) -> str:
    """Feedback R6/R7 — render the deterministic, framework-tagged anomalies as an
    editable markdown SECTION. R7: bullets lead with the **table.field** (bold),
    then a reading-friendly clause; the severity is conveyed by wording and by a
    subtle framework tag at the END — no bullet starts with '[Info]'/'[Critical]'."""
    n = len(key_anomalies or [])
    if n == 0:
        return ("## Key Anomalies\n\n"
                "No material anomalies were flagged from the data profile.")
    lead = (f"{num_word(n, cap=True)} material "
            f"anomal{'y was' if n == 1 else 'ies were'} flagged from the data "
            "profile, ordered by severity and by how much each matters for the "
            "intended model use.")
    lines = ["## Key Anomalies", "", lead, ""]
    for a in key_anomalies:
        target = f"{a.get('table', '')}.{a.get('column', '')}"
        sentence = _humanize_anomaly(a.get("anomaly", ""))
        area = a.get("framework_area") or ""
        pri = a.get("family_priority")
        tag = (f" _({area}{f' · {pri} priority for this model family' if pri else ''})_"
               if area else "")
        lines.append(f"- **{target}** {sentence}.{tag}")
    return "\n".join(lines)


def render_hypotheses_md(suggested_hypotheses: list[dict], *,
                         use_cases=None, model_families=None) -> str:
    """Feedback R6/R7 — render the framework-grounded test hypotheses as an
    editable markdown SECTION. R7: titled **Potential** Test Hypotheses; the lead
    starts from the potential usages and links the tests to them; bullets never
    start with a '[...]' bracket; the checks most likely to need attention (a
    critical/high framework area that overlaps an observed anomaly) surface first."""
    n = len(suggested_hypotheses or [])
    if n == 0:
        return ("## Potential Test Hypotheses\n\n"
                "No test hypotheses were identified for this database.")
    usages = _usages_phrase(use_cases, model_families)
    lead = (f"Because this data supports {usages}, the following {num_word(n)} "
            f"test{'' if n == 1 else 's'} {'is' if n == 1 else 'are'} recommended. "
            "Each can be turned into a test in the Test module.")
    lines = ["## Potential Test Hypotheses", "", lead, ""]

    def bullet(h: dict) -> str:
        cols = h.get("columns") or []
        tgt = h.get("table", "") + (f" ({', '.join(cols)})" if cols else "")
        area = h.get("framework_area") or ""
        diag = h.get("diagnostic") or ""
        meta = " · ".join([b for b in (diag, area) if b])
        suffix = f" _({meta})_" if meta else ""
        return f"- **{tgt.strip()}** — {h.get('hypothesis', '')}{suffix}"

    highlights = [h for h in suggested_hypotheses if h.get("highlight")]
    rest = [h for h in suggested_hypotheses if not h.get("highlight")]
    if highlights:
        lines.append("### Priority checks — most likely to need attention")
        lines += [bullet(h) for h in highlights]
        if rest:
            lines += ["", "### Other recommended checks"]
            lines += [bullet(h) for h in rest]
    else:
        lines += [bullet(h) for h in suggested_hypotheses]
    return "\n".join(lines)


def describe_db(logical_db: str, tables: list[dict]) -> str:
    """Step-3 first stab: a short 2–3 sentence plain-English description of the
    database for the metadata form. Cheap (low effort), no persistence. Uses a
    one-off prompt (not the governed Newton summary contract)."""
    policy = EffortPolicy(agent_key="newton", tier="low")
    messages = [
        {"role": "system", "content": _DESCRIBE_SYSTEM},
        {"role": "user", "content": _build_user(logical_db, tables, None)},
    ]
    raw = policy.run(messages, json_mode=True)
    try:
        return str(json.loads(raw).get("description", "")).strip()
    except (ValueError, TypeError):
        return raw.strip()


def understand(logical_db: str, tables: list[dict], feedback: str | None = None,
               on_event=None, *, use_cases=None, model_families=None,
               dictionary=None) -> dict:
    """Produce + persist the AI understanding (three plain-English paragraphs).

    Phase 1 (deterministic): compute_discovery_state() profiles every column
    via pandas — null rates, distributions, cardinality, anomalies, join
    candidates. No LLM involved.

    Phase 2 (LLM): Newton writes paragraphs 1–2 from DiscoveryState.compress();
    Merton writes paragraph 3. Both streamed to the Agent Console via on_event.

    Returns the parsed dict including the composed ``summary`` string and the
    ``discovery_state`` DiscoveryState object.
    """
    from .skills import get_system_prompt

    def _tag(agent: str):
        """Wrap on_event so console frames know which agent emitted them."""
        if on_event is None:
            return None
        return lambda e: on_event({**e, "_agent": agent})

    # Phase 1 — pure Python, zero LLM.
    if on_event is not None:
        on_event({"_agent": "Newton", "phase": "profiling"})
    ds: DiscoveryState = compute_discovery_state(logical_db, tables)

    # Framework context for the DB's declared model family / use-cases — the
    # bridge that turns generic profiling into prioritized, family-aware output
    # (shared by Key Anomalies here and Merton's Suggested Test Hypotheses below).
    from .framework_context import get_framework_context
    framework_ctx = get_framework_context(model_families, use_cases)
    # Key Anomalies (deterministic, framework-tagged) — replaces LLM Assumptions.
    key_anomalies = build_key_anomalies(ds, framework_ctx)
    if on_event is not None:
        on_event({"_agent": "Newton", "phase": "key_anomalies",
                  "thought": f"{len(key_anomalies)} key anomaly(ies) flagged from the profile."})

    # Phase 2 — LLM receives the compressed, stats-grounded profile.
    user_content = ds.compress()
    if feedback:
        user_content += f"\n\nUSER FEEDBACK to incorporate: {feedback}"

    policy = EffortPolicy(agent_key="newton")
    messages = [
        {"role": "system", "content": get_system_prompt("newton", _SYSTEM)},
        {"role": "user", "content": user_content},
    ]
    raw = policy.run(messages, json_mode=True, on_event=_tag("Newton"))
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {"stats_paragraph": raw, "profiling_paragraph": "",
                "assumptions": [], "open_questions": []}

    # Normalise shapes (a model may return a paragraph as a list of lines),
    # then hard-cap each paragraph so the model cannot exceed the UI limit even
    # if it ignores the character-count instructions in its prompt.
    stats_p = _cap_paragraph(
        _as_text(data.get("stats_paragraph")).replace("\n", " ").strip(),
        _PARA_CAPS["stats"],
    )
    profiling_p = _cap_paragraph(
        _as_text(data.get("profiling_paragraph")).replace("\n", " ").strip(),
        _PARA_CAPS["profiling"],
    )
    data["stats_paragraph"] = stats_p
    data["profiling_paragraph"] = profiling_p
    data["newton_summary"] = compose_summary(stats_p, profiling_p, "")
    data["key_anomalies"] = key_anomalies
    # Retire the legacy LLM fields — a stored/older Newton prompt may still emit
    # them, but Key Anomalies (deterministic) and Suggested Test Hypotheses
    # (Merton) now supersede them. Drop so they never reach the UI/PDF/persistence.
    data.pop("assumptions", None)
    data.pop("open_questions", None)

    # Paragraph 3 — Merton (credit-risk domain expert). Streamed via on_event so
    # the console shows Merton picking up after Newton. A Merton failure must not
    # sink the whole summary — fall back to Newton's two paragraphs.
    usage_p = ""
    try:
        from .credit_risk_domain import assess_usage
        if on_event is not None:
            on_event({"_agent": "Merton", "phase": "handoff"})
        usage_p = assess_usage(
            logical_db, tables, use_cases=use_cases, model_families=model_families,
            newton_summary=data["newton_summary"], on_event=_tag("Merton"))
    except Exception:  # noqa: BLE001 — domain paragraph is best-effort
        usage_p = ""
    data["usage_paragraph"] = _cap_paragraph(usage_p, _PARA_CAPS["usage"])

    # Suggested Test Hypotheses — Merton, framework-grounded (replaces Open
    # Questions). Best-effort: a failure leaves an empty list, never sinks the
    # summary. Each hypothesis is schema-validated + deep-linkable into the plan.
    suggested_hypotheses: list[dict] = []
    try:
        from .credit_risk_domain import suggest_hypotheses
        if on_event is not None:
            on_event({"_agent": "Merton", "phase": "hypotheses"})
        suggested_hypotheses = suggest_hypotheses(
            ds, use_cases=use_cases, model_families=model_families,
            framework_ctx=framework_ctx, key_anomalies=key_anomalies,
            newton_summary=data["newton_summary"], on_event=_tag("Merton"))
    except Exception:  # noqa: BLE001 — hypotheses are best-effort
        suggested_hypotheses = []
    data["suggested_hypotheses"] = suggested_hypotheses

    # ¶4 — deterministic manual-inputs acknowledgement (only present when the
    # analyst provided use_cases, model_families, or a data dictionary).
    manual_p = _build_manual_inputs_paragraph(use_cases, model_families, dictionary)
    data["manual_paragraph"] = manual_p
    # R7: the displayed/persisted summary is markdown with Findings / Potential
    # Usages headers; the plain join is kept as newton_summary for the agents.
    data["summary"] = compose_summary_md(stats_p, profiling_p, usage_p, manual_p)

    # Feedback R6/R7 — editable markdown renderings of the two sections, saved with
    # the DB so the analyst can edit them and the test module can read them back
    # later. The structured lists remain for any programmatic consumer.
    data["key_anomalies_md"] = render_anomalies_md(key_anomalies)
    data["suggested_hypotheses_md"] = render_hypotheses_md(
        suggested_hypotheses, use_cases=use_cases, model_families=model_families)

    data["discovery_state"] = ds

    # Persist narrative to .md (AI memory / editable system prompt).
    md = [f"# Database Understanding — {logical_db}", "", "## Summary",
          data.get("summary", ""), "", "## Key Anomalies"]
    md += [f"- [{a.get('severity')}] {a.get('table')}.{a.get('column')} — "
           f"{a.get('anomaly')} ({a.get('framework_area') or 'unmapped'})"
           for a in data.get("key_anomalies", [])]
    md += ["", "## Suggested Test Hypotheses"]
    md += [f"- {h.get('hypothesis')} [{h.get('framework_area')} · "
           f"{h.get('priority')} · {h.get('diagnostic')}]"
           for h in data.get("suggested_hypotheses", [])]
    md_path(logical_db).write_text("\n".join(md), encoding="utf-8")

    # Persist DiscoveryState as machine-readable JSON for downstream agents.
    discovery_state_path(logical_db).write_text(
        ds.model_dump_json(indent=2), encoding="utf-8"
    )
    return data
