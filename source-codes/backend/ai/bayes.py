"""Bayes — Test Lab HITL feedback adjudicator (the "shuttle").

Given a designed test's current state and one human feedback note, Bayes weighs
the feedback on its merits and returns a strict-JSON verdict: ``accommodate``
(with concrete proposed changes) or ``push_back`` (with a compassionate, reasoned
explanation). The router applies accommodated changes as a new version; Bayes
itself holds no state and calls no tools.

Mirrors the agent patterns in ``ai/rca_checker.py`` (locked system contract +
editable .md overlay, EffortPolicy run, json.loads + repair/fallback).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .effort import EffortPolicy
from .prompts import BAYES_SYSTEM
from .skills import get_system_prompt

_ALLOWED_CHANGE_KEYS = {"python_code", "params", "thresholds", "dossier_updates",
                        "monitor_frequency"}
_DOSSIER_KEYS = {"rationale", "quantitative_outcome", "contextualization", "strategy"}


def _system_prompt() -> str:
    """Locked BAYES_SYSTEM contract + the editable bayes.md overlay (RCA pattern)."""
    overlay = get_system_prompt("bayes", BAYES_SYSTEM)
    if overlay.strip() == BAYES_SYSTEM.strip():
        return BAYES_SYSTEM
    return f"{BAYES_SYSTEM}\n\nEditable Bayes skill overlay:\n{overlay}"


def _build_user(test_state: dict, feedback_text: str, db_context: str = "",
                framework_brief: str = "") -> str:
    code = (test_state.get("python_code") or "")[:3000]
    dossier = test_state.get("dossier") or {}
    dossier_brief = {k: (str(dossier.get(k) or "")[:600]) for k in _DOSSIER_KEYS}
    return "\n".join(filter(None, [
        f"TEST: {test_state.get('name') or test_state.get('test_id')}  "
        f"(category {test_state.get('category')})",
        f"OPERANDS: {json.dumps(test_state.get('operands') or [])[:800]}",
        f"PARAMS: {json.dumps(test_state.get('params') or {})[:800]}",
        f"THRESHOLDS: {json.dumps(test_state.get('thresholds') or {})[:400]}",
        f"CURRENT DOSSIER: {json.dumps(dossier_brief)}",
        f"PYTHON CODE:\n{code}",
        f"--- UPSTREAM DB CONTEXT (weigh the feedback against what we already know) ---\n{db_context}"
        if db_context else "",
        framework_brief or "",
        f"HUMAN FEEDBACK:\n{feedback_text.strip()}",
        "Adjudicate this feedback against the test AND the DB context above. "
        "Return ONLY the strict JSON verdict.",
    ]))


def _clean_changes(raw: Any) -> dict:
    """Keep only recognised change keys; drop empties so the router applies the
    smallest real diff."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _ALLOWED_CHANGE_KEYS or v in (None, "", {}, []):
            continue
        if k == "dossier_updates" and isinstance(v, dict):
            du = {dk: dv for dk, dv in v.items() if dk in _DOSSIER_KEYS and dv}
            if du:
                out[k] = du
        else:
            out[k] = v
    return out


def adjudicate(test_state: dict, feedback_text: str, db_context: str = "",
               framework_brief: str = "",
               on_event: Callable[[dict], None] | None = None) -> dict:
    """Run one HITL adjudication turn. ``db_context`` carries the upstream
    Newton/Merton understanding of the DB and ``framework_brief`` the in-scope DQ
    areas, so Bayes weighs feedback against what we already know. Returns
    {verdict, message, reasoning, proposed_changes}. Never raises on a bad LLM
    response — falls back to a safe push_back."""
    if not (feedback_text or "").strip():
        return {"verdict": "push_back", "message": "I didn't catch any feedback — "
                "tell me what you'd like to change and I'll weigh it.",
                "reasoning": "Empty feedback.", "proposed_changes": {}}

    policy = EffortPolicy(agent_key="bayes")
    raw = policy.run(
        [{"role": "system", "content": _system_prompt()},
         {"role": "user", "content": _build_user(test_state, feedback_text,
                                                 db_context, framework_brief)}],
        json_mode=True, on_event=on_event,
    )
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"verdict": "push_back",
                "message": "I want to make sure I get this right — could you "
                           "restate what you'd like changed?",
                "reasoning": "Adjudicator output was unparseable.",
                "proposed_changes": {}, "note": str(raw)[:300]}

    verdict = "accommodate" if str(data.get("verdict")).lower() == "accommodate" else "push_back"
    changes = _clean_changes(data.get("proposed_changes")) if verdict == "accommodate" else {}
    # An accommodate with no concrete change is really a push_back.
    if verdict == "accommodate" and not changes:
        verdict = "push_back"
    return {
        "verdict": verdict,
        "message": str(data.get("message") or "").strip()
                   or ("I've updated the test as you asked." if verdict == "accommodate"
                       else "I hear you — here's my thinking on this."),
        "reasoning": str(data.get("reasoning") or "").strip(),
        "proposed_changes": changes,
    }
