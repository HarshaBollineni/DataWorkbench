"""Plan 8 - seed common object-context memory."""
from __future__ import annotations

import json

import system_db as s
from ai.context_memory import upsert_context


def _short(value, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text[:limit]


def load() -> int:
    s.init_schema()
    count = 0
    for db in s.query("ingested_databases"):
        logical_db = db.get("logical_db")
        if not logical_db:
            continue
        upsert_context({
            "scope": "common",
            "logical_db": logical_db,
            "object_type": "database",
            "object_key": logical_db,
            "context_type": "business",
            "title": db.get("display_name") or logical_db,
            "content": _short(db.get("description") or db.get("ai_summary") or logical_db),
            "source": "seed",
            "priority": 2,
            "tags": ["seed", "database"],
        })
        count += 1
    for tm in s.query("table_metadata"):
        logical_db = tm.get("logical_db")
        table = tm.get("table")
        if not table:
            continue
        descriptions = tm.get("descriptions") or {}
        upsert_context({
            "scope": "common",
            "logical_db": logical_db,
            "object_type": "table",
            "object_key": f"{logical_db}.{table}" if logical_db else table,
            "context_type": "schema",
            "title": f"{table} schema summary",
            "content": _short({
                "pk": tm.get("pk"),
                "date_col": tm.get("date_col"),
                "columns": tm.get("columns") or [],
                "descriptions": descriptions,
            }),
            "source": "seed",
            "priority": 2,
            "tags": ["seed", "table"],
        })
        count += 1
        for column, desc in list(descriptions.items())[:40]:
            upsert_context({
                "scope": "common",
                "logical_db": logical_db,
                "object_type": "variable",
                "object_key": f"{logical_db}.{table}.{column}" if logical_db else f"{table}.{column}",
                "context_type": "schema",
                "title": f"{table}.{column}",
                "content": _short(desc),
                "source": "seed",
                "priority": 3,
                "tags": ["seed", "variable"],
            })
            count += 1
    for test in s.query("test_library"):
        test_id = test.get("test_id")
        if not test_id:
            continue
        upsert_context({
            "scope": "common",
            "logical_db": None,
            "object_type": "test",
            "object_key": f"test_library.{test_id}",
            "context_type": "dq_rule",
            "title": test.get("name") or test_id,
            "content": _short({
                "category": test.get("category"),
                "description": test.get("description_en") or test.get("background"),
                "thresholds": test.get("thresholds") or {},
                "input_spec": test.get("input_spec") or {},
            }),
            "source": "seed",
            "priority": 2,
            "tags": ["seed", "test"],
        })
        count += 1
    return count

