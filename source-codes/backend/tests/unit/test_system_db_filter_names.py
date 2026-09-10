"""Regression coverage for generic helper filters named like table columns."""
from __future__ import annotations

import system_db as db


def test_query_helpers_accept_table_and_table_name_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "state.db")
    db.init_schema()
    db.insert("table_metadata", {"logical_db": "fixture", "table": "orders"})
    db.insert("dq_item_tables", {
        "item_id": "snapshot", "table_name": "portfolio", "row_count": 1,
        "col_count": 1, "columns": ["score"],
    })

    assert db.query("table_metadata", table="orders")[0]["table"] == "orders"
    assert db.query_one("table_metadata", table="orders")["logical_db"] == "fixture"
    assert db.query("dq_item_tables", table_name="portfolio")[0]["table_name"] == "portfolio"
    assert db.query_one("dq_item_tables", table_name="portfolio")["item_id"] == "snapshot"
