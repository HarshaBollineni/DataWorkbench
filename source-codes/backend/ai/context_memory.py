"""Plan 8 - object-scoped context memory.

Stores compact context on system-owned objects and retrieves a bounded bundle for
agents. Domain databases remain read-only; this module only writes system_state.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from typing import Any

import system_db as s

USAGE_REQUIREMENTS = [
    "Use object context memory as supporting evidence, not ground truth.",
    "Prefer live run output and read-only data probes when they conflict with memory.",
    "Cite supporting_context_ids when memory materially influences the output.",
]

_TYPE_CAP_GROUPS = {
    "database": {"database", "global", "relationship"},
    "table": {"table"},
    "variable_test": {"variable", "test", "test_instance"},
    "recent": {"run_result", "rca", "ticket"},
}
_TYPE_CAPS = {"database": 2, "table": 3, "variable_test": 4, "recent": 3}


def _estimate_tokens(content: str) -> int:
    return max(1, len(content or "") // 4)


def _content_hash(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()


def _semantic_match(row: dict) -> dict | None:
    matches = [
        r for r in s.query("object_contexts")
        if r.get("scope") == row.get("scope", "demo")
        and r.get("logical_db") == row.get("logical_db")
        and r.get("object_type") == row.get("object_type")
        and r.get("object_key") == row.get("object_key")
        and r.get("context_type") == row.get("context_type")
        and r.get("title") == row.get("title", "")
    ]
    return matches[0] if matches else None


def upsert_context(row: dict) -> dict:
    """Insert or update one context row and return the decoded row."""
    s.init_schema()
    now = s.now_ist()
    content = str(row.get("content") or "")
    context_id = row.get("context_id")
    data = {
        "context_id": context_id or uuid.uuid4().hex,
        "scope": row.get("scope") or "demo",
        "logical_db": row.get("logical_db"),
        "object_type": row.get("object_type") or "global",
        "object_key": row.get("object_key") or "global",
        "context_type": row.get("context_type") or "human_note",
        "title": row.get("title") or "",
        "content": content,
        "source": row.get("source") or "agent",
        "confidence": float(row.get("confidence", 1.0)),
        "token_estimate": int(row.get("token_estimate") or _estimate_tokens(content)),
        "priority": int(row.get("priority") or 3),
        "tags": row.get("tags") or [],
        "expires_at": row.get("expires_at"),
        "created_at": row.get("created_at") or now,
        "updated_at": now,
    }
    existing = s.query_one("object_contexts", context_id=context_id) if context_id else _semantic_match(data)
    if existing:
        data["context_id"] = existing["context_id"]
        data["created_at"] = existing.get("created_at") or data["created_at"]
        s.update("object_contexts", {"context_id": data["context_id"]}, data)
    else:
        s.insert("object_contexts", data)
    return s.query_one("object_contexts", context_id=data["context_id"]) or data


def link_context(
    from_context_id: str,
    *,
    to_object_type: str,
    to_object_key: str,
    relation: str,
    weight: float = 1.0,
) -> dict:
    link_id = uuid.uuid4().hex
    row = {
        "link_id": link_id,
        "from_context_id": from_context_id,
        "to_object_type": to_object_type,
        "to_object_key": to_object_key,
        "relation": relation,
        "weight": float(weight),
    }
    s.insert("context_links", row)
    return row


def _object_keys(
    logical_db: str | None,
    table: str | None,
    columns: list[str] | None,
    test_id: str | None,
    instance_key: str | None,
    run_id: int | None,
    ticket_no: str | None,
) -> set[str]:
    keys = set()
    if logical_db:
        keys.add(logical_db)
    if table:
        keys.add(f"{logical_db}.{table}" if logical_db else table)
    if table and columns:
        for col in columns:
            clean = str(col).split(".")[-1]
            keys.add(f"{logical_db}.{table}.{clean}" if logical_db else f"{table}.{clean}")
    if test_id:
        keys.add(f"test_library.{test_id}")
    if instance_key:
        keys.add(instance_key)
    if run_id is not None:
        keys.add(f"run_result.{run_id}")
    if ticket_no:
        keys.add(f"ticket.{ticket_no}")
    return {k for k in keys if k}


def _score(row: dict, keys: set[str], logical_db: str | None, table: str | None) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    object_key = str(row.get("object_key") or "")
    if object_key in keys:
        score += 40
        reasons.append("exact_object")
    linked = s.execute(
        "SELECT weight FROM context_links WHERE from_context_id=? AND to_object_key IN "
        f"({','.join('?' for _ in keys)})",
        [row.get("context_id"), *list(keys)],
    ) if keys else []
    if linked:
        score += 25
        reasons.append("linked_object")
    if table and f".{table}" in object_key or (table and object_key == table):
        score += 15
        reasons.append("same_table")
    if logical_db and row.get("logical_db") == logical_db:
        score += 8
        reasons.append("same_logical_db")
    priority = int(row.get("priority") or 3)
    score += (6 - priority) * 3
    reasons.append(f"priority_{priority}")
    if row.get("scope") == "demo":
        score += 5
        reasons.append("recent_demo")
    return score, reasons


def _cap_group(object_type: str) -> str:
    for group, members in _TYPE_CAP_GROUPS.items():
        if object_type in members:
            return group
    return "recent"


def get_context_bundle(
    *,
    logical_db: str | None = None,
    table: str | None = None,
    columns: list[str] | None = None,
    test_id: str | None = None,
    instance_key: str | None = None,
    run_id: int | None = None,
    ticket_no: str | None = None,
    max_tokens: int = 1800,
) -> dict:
    """Return a compact, scored context bundle."""
    s.init_schema()
    keys = _object_keys(logical_db, table, columns, test_id, instance_key, run_id, ticket_no)
    candidates = []
    for row in s.query("object_contexts"):
        score, reasons = _score(row, keys, logical_db, table)
        if score <= 0:
            continue
        candidates.append((score, reasons, row))
    candidates.sort(key=lambda x: (x[0], str(x[2].get("updated_at") or "")), reverse=True)

    selected: list[dict[str, Any]] = []
    group_counts: dict[str, int] = defaultdict(int)
    seen = set()
    tokens = 0
    top_reasons = []
    for score, reasons, row in candidates:
        digest = (row.get("object_type"), row.get("object_key"), row.get("context_type"),
                  row.get("title"), _content_hash(row.get("content") or ""))
        if digest in seen:
            continue
        group = _cap_group(str(row.get("object_type") or ""))
        if group_counts[group] >= _TYPE_CAPS.get(group, 3):
            continue
        cost = int(row.get("token_estimate") or _estimate_tokens(row.get("content") or ""))
        if tokens + cost > max_tokens:
            continue
        selected.append(row)
        seen.add(digest)
        group_counts[group] += 1
        tokens += cost
        if len(top_reasons) < 5:
            top_reasons.append({
                "context_id": row.get("context_id"),
                "score": score,
                "reasons": reasons,
            })

    by_object_type = Counter(str(r.get("object_type") or "") for r in selected)
    by_context_type = Counter(str(r.get("context_type") or "") for r in selected)
    return {
        "object_keys": sorted(keys),
        "contexts": selected,
        "omitted_count": max(0, len(candidates) - len(selected)),
        "token_estimate": tokens,
        "usage_requirements": USAGE_REQUIREMENTS,
        "retrieval_evidence": {
            "selected_by_object_type": dict(by_object_type),
            "selected_by_context_type": dict(by_context_type),
            "omitted_count": max(0, len(candidates) - len(selected)),
            "token_estimate": tokens,
            "top_scoring_reasons": top_reasons,
        },
    }


def compact_bundle_text(bundle: dict, max_chars: int = 3500) -> str:
    parts = [
        "CONTEXT MEMORY USAGE REQUIREMENTS:",
        *[f"- {x}" for x in bundle.get("usage_requirements") or USAGE_REQUIREMENTS],
        "SELECTED CONTEXTS:",
    ]
    for ctx in bundle.get("contexts") or []:
        parts.append(
            f"- [{ctx.get('context_id')}] {ctx.get('object_type')} {ctx.get('object_key')} "
            f"{ctx.get('context_type')}: {ctx.get('title')} - {ctx.get('content')}"
        )
    parts.append("RETRIEVAL EVIDENCE:")
    parts.append(json.dumps(bundle.get("retrieval_evidence") or {}, default=str)[:800])
    return "\n".join(parts)[:max_chars]


def purge_demo_contexts() -> dict:
    s.init_schema()
    deleted_contexts = s.delete("object_contexts", scope="demo")
    deleted_links = s.execute(
        "DELETE FROM context_links WHERE from_context_id NOT IN "
        "(SELECT context_id FROM object_contexts)"
    )
    return {"object_contexts": deleted_contexts, "context_links": len(deleted_links)}

