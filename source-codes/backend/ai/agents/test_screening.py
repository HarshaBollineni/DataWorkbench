"""Plan 7 — Hypatia library screening with explicit Search / Deep Search modes."""
from __future__ import annotations

import json
from itertools import combinations
from typing import Any

from ..effort import EffortPolicy

SEARCH_EMPTY = "No high-confidence library test matched the selected field(s)."
DEEP_EMPTY = (
    "Deep Search found no defensible test in the existing library for the "
    "selected fields and available table metadata."
)
PAIR_EMPTY = "No defensible multivariate field pair matched the existing library."
DEEP_SEARCH_MSG = "No high-confidence match. Deep Search reviewed the full library."
DEEP_SEARCH_LIMIT = 3
PAIR_LIMIT = 6
PAIR_KEYWORD_WEIGHTS = {
    "target": 6,
    "default": 6,
    "bad": 6,
    "pd": 6,
    "rating": 5,
    "score": 4,
    "risk": 4,
    "leverage": 6,
    "coverage": 6,
    "debt": 5,
    "utilization": 5,
    "delinquency": 5,
    "exposure": 4,
    "ltv": 4,
    "income": 2,
    "balance": 2,
    "revenue": 1,
}
PAIR_PRIMARY_KEYWORDS = ("target", "default", "bad", "pd", "rating", "score", "risk")
PAIR_DRIVER_KEYWORDS = (
    "leverage", "coverage", "debt", "income", "utilization", "delinquency",
    "balance", "exposure", "ltv", "revenue",
)


def _kind(datatype: str | None) -> str:
    text = str(datatype or "").lower()
    if any(k in text for k in ("date", "time")):
        return "date"
    if any(k in text for k in ("int", "real", "float", "double", "numeric", "decimal")):
        return "numeric"
    if any(k in text for k in ("bool", "flag")):
        return "target"
    return "categorical"


def _compatible(expected: list[str], actual: str, is_target: bool) -> bool:
    if not expected:
        return True
    normalized = {str(v).lower() for v in expected}
    if actual in normalized:
        return True
    if is_target and "target" in normalized:
        return True
    return False


def _search(library: list[dict], fields: list[str], context: dict[str, Any],
            table: str = "") -> list[dict]:
    """Return only deterministic, high-confidence applicability matches."""
    datatypes = context.get("datatypes") or {}
    date_col = context.get("date_col")
    target = context.get("target_variable")
    target_table = context.get("target_variable_table")
    selected = [str(f) for f in fields]
    out = []
    for test in library:
        spec = test.get("input_spec") or {}
        n_columns = int(spec.get("n_columns", 0) or 0)
        if n_columns != len(selected):
            continue
        if spec.get("needs_date_col") and not date_col:
            continue
        expected = list(spec.get("types") or [])
        if any(not _compatible(
            expected,
            _kind(datatypes.get(field)),
            field == target and (not target_table or target_table == table),
        )
               for field in selected):
            continue
        field_text = ", ".join(selected) if selected else "table-level metadata"
        out.append({
            **test,
            "reason": (
                f"High-confidence applicability match for {field_text}: "
                f"required input count and datatype/date requirements are satisfied."
            ),
            "confidence": "high",
        })
    return out


def _deep_search(library: list[dict], table: str, fields: list[str],
                 description: str, context: dict[str, Any],
                 effort: EffortPolicy | None, on_event=None) -> tuple[list[dict], str]:
    """Use Hypatia to rank existing library rows; never author a new test."""
    policy = effort or EffortPolicy(agent_key="hypatia")
    catalog = [{
        "test_id": t["test_id"],
        "name": t["name"],
        "category": t["category"],
        "input_spec": t.get("input_spec") or {},
        "applicability": t.get("applicability"),
        "description": t.get("description_en"),
    } for t in library]
    system = (
        "You are Hypatia. Select only from the supplied library. MODE is deep. "
        "Return STRICT JSON with exactly: "
        "{\"recommended\":[{\"test_id\":\"str\",\"reason\":\"str\","
        "\"confidence\":\"low|medium|high\"}],\"reason\":\"str\"}. "
        "Never invent a test. Empty recommendations require a specific reason."
    )
    user = (
        f"MODE: deep\nTABLE: {table}\nFIELDS: {json.dumps(fields)}\n"
        f"DESCRIPTION: {description}\nCONTEXT: {json.dumps(context, default=str)[:2000]}\n"
        f"LIBRARY: {json.dumps(catalog, default=str)[:6000]}"
    )
    from ..skills import get_system_prompt
    raw = policy.run(
        [{"role": "system", "content": get_system_prompt("hypatia", system)},
         {"role": "user", "content": user}],
        json_mode=True,
        on_event=on_event,
    )
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    by_id = {t["test_id"]: t for t in library}
    recommended = []
    seen = set()
    for item in parsed.get("recommended") or []:
        test_id = item.get("test_id")
        if test_id not in by_id or test_id in seen:
            continue
        confidence = str(item.get("confidence") or "low").lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        recommended.append({
            **by_id[test_id],
            "reason": reason,
            "confidence": confidence,
        })
        seen.add(test_id)
        if len(recommended) >= DEEP_SEARCH_LIMIT:
            break
    reason = str(parsed.get("reason") or "").strip()
    if not recommended and not reason:
        reason = DEEP_EMPTY
    return recommended, reason


def _multivariate_tests(library: list[dict]) -> list[dict]:
    return [
        test for test in library
        if int((test.get("input_spec") or {}).get("n_columns", 0) or 0) > 1
    ]


def _candidate_pairs(table: str, library: list[dict], context: dict[str, Any]) -> list[dict]:
    datatypes = context.get("datatypes") or {}
    date_col = context.get("date_col")
    target = context.get("target_variable")
    target_table = context.get("target_variable_table")

    def eligible_field(field: str, dtype: str | None) -> bool:
        name = str(field).lower()
        if field == date_col:
            return False
        if name == "id" or name.endswith("_id") or name.endswith("id"):
            return False
        if _kind(dtype) != "numeric":
            return False
        if field == target and (not target_table or target_table == table):
            return False
        return True

    numeric = [
        field for field, dtype in datatypes.items()
        if eligible_field(field, dtype)
    ]

    def field_score(field: str) -> int:
        name = str(field).lower()
        return sum(weight for keyword, weight in PAIR_KEYWORD_WEIGHTS.items() if keyword in name)

    def pair_score(a: str, b: str, test: dict) -> tuple[int, int]:
        names = f"{a} {b}".lower()
        score = field_score(a) + field_score(b)
        if any(k in names for k in PAIR_PRIMARY_KEYWORDS) and any(k in names for k in PAIR_DRIVER_KEYWORDS):
            score += 5
        if "corr" in str(test.get("test_id") or "").lower():
            score += 1
        return (score, -len(names))

    out = []
    for a, b in combinations(numeric, 2):
        for test in _multivariate_tests(library):
            spec = test.get("input_spec") or {}
            n_columns = int(spec.get("n_columns", 0) or 0)
            if n_columns != 2:
                continue
            if spec.get("needs_date_col") and not date_col:
                continue
            expected = list(spec.get("types") or [])
            if not all(_compatible(expected, _kind(datatypes.get(f)), False) for f in (a, b)):
                continue
            out.append({
                **test,
                "fields": [a, b],
                "_pair_score": pair_score(a, b, test),
                "reason": (
                    f"Multivariate candidate for {table}: {test['name']} can evaluate "
                    f"{a} and {b}; required input count, datatype, and date requirements are satisfied."
                ),
                "confidence": "high",
            })
    out.sort(key=lambda item: item.get("_pair_score", (0, 0)), reverse=True)
    for item in out:
        item.pop("_pair_score", None)
    return out[:50]


def _deep_pair_search(library: list[dict], table: str, description: str,
                      context: dict[str, Any], effort: EffortPolicy | None,
                      on_event=None) -> tuple[list[dict], str]:
    candidates = _candidate_pairs(table, library, context)
    if not candidates:
        return [], PAIR_EMPTY
    policy = effort or EffortPolicy(agent_key="hypatia")
    candidate_catalog = [{
        "candidate_id": f"{c['test_id']}::{','.join(c['fields'])}",
        "test_id": c["test_id"],
        "name": c["name"],
        "category": c.get("category"),
        "fields": c["fields"],
        "input_spec": c.get("input_spec") or {},
        "description": c.get("description_en"),
        "deterministic_reason": c.get("reason"),
    } for c in candidates]
    system = (
        "You are Hypatia. Rank candidate multivariate test-field pairs only from "
        "the supplied candidate_catalog. Do not invent tests or fields. Return STRICT JSON "
        "with exactly {\"recommended\":[{\"candidate_id\":\"str\",\"reason\":\"str\","
        "\"confidence\":\"low|medium|high\"}],\"reason\":\"str\"}."
    )
    user = (
        f"MODE: deep_pairs\nTABLE: {table}\nDESCRIPTION: {description}\n"
        f"CONTEXT: {json.dumps(context, default=str)[:2000]}\n"
        f"CANDIDATE_CATALOG: {json.dumps(candidate_catalog, default=str)[:7000]}"
    )
    from ..skills import get_system_prompt
    raw = policy.run(
        [{"role": "system", "content": get_system_prompt("hypatia", system)},
         {"role": "user", "content": user}],
        json_mode=True,
        on_event=on_event,
    )
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    by_id = {f"{c['test_id']}::{','.join(c['fields'])}": c for c in candidates}
    recommended = []
    seen = set()
    for item in parsed.get("recommended") or []:
        cid = item.get("candidate_id")
        if cid not in by_id or cid in seen:
            continue
        confidence = str(item.get("confidence") or "low").lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        reason = str(item.get("reason") or by_id[cid].get("reason") or "").strip()
        if not reason:
            continue
        recommended.append({**by_id[cid], "reason": reason, "confidence": confidence})
        seen.add(cid)
        if len(recommended) >= PAIR_LIMIT:
            break
    reason = str(parsed.get("reason") or "").strip()
    if not recommended:
        # Fall back to deterministic ranked candidates so the workflow remains usable.
        recommended = candidates[:PAIR_LIMIT]
        reason = reason or "AI returned no parseable pair ranking; deterministic pair candidates are shown."
    return recommended, reason


def screen(table: str, fields: list[str], description: str, library: list[dict],
           effort: EffortPolicy | None = None, on_event=None, *,
           mode: str = "deep", context: dict[str, Any] | None = None) -> dict:
    """Return ``{mode,recommended,reason,searched_count}``.

    ``search`` is deterministic and makes no LLM call. ``deep`` invokes Hypatia
    over the supplied library only.
    """
    normalized_mode = str(mode or "search").lower()
    if normalized_mode not in {"search", "deep", "pairs", "deep_pairs"}:
        raise ValueError("mode must be 'search', 'deep', 'pairs', or 'deep_pairs'")
    ctx = context or {}
    if normalized_mode == "search":
        recommended = _search(library, fields, ctx, table)
        return {
            "mode": "search",
            "recommended": recommended,
            "reason": "" if recommended else SEARCH_EMPTY,
            "searched_count": len(library),
            # Backward-compatible fields for the New Test Manager event text.
            "deep_search": False,
            "message": "",
        }
    if normalized_mode == "pairs":
        recommended = _candidate_pairs(table, library, ctx)[:PAIR_LIMIT]
        return {
            "mode": "pairs",
            "recommended": recommended,
            "reason": "" if recommended else PAIR_EMPTY,
            "searched_count": len(library),
            "deep_search": False,
            "message": "",
        }
    if normalized_mode == "deep_pairs":
        recommended, reason = _deep_pair_search(library, table, description, ctx, effort, on_event)
        return {
            "mode": "deep_pairs",
            "recommended": recommended,
            "reason": reason,
            "searched_count": len(library),
            "deep_search": True,
            "message": reason,
        }
    recommended, reason = _deep_search(
        library, table, fields, description, ctx, effort, on_event)
    return {
        "mode": "deep",
        "recommended": recommended,
        "reason": reason,
        "searched_count": len(library),
        "deep_search": True,
        "message": reason or DEEP_SEARCH_MSG,
    }
