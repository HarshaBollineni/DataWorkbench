"""Read-only access to immutable DataWorkbench snapshot data."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd

import system_db as db
from assets.reads import snapshot_read_model
from .contracts import SnapshotRef


class SnapshotNotReadyError(ValueError):
    pass


class SnapshotLoader:
    """The sole data-access boundary expected by supporting analyses."""

    def __init__(self, table_reader: Callable[..., pd.DataFrame] | None = None):
        if table_reader is None:
            from ai.v2.service import read_snapshot_table
            table_reader = read_snapshot_table
        self._table_reader = table_reader

    def reference(self, snapshot_id: str, *, require_ready: bool = True,
                  allow_historical: bool = False) -> SnapshotRef:
        item = db.query_one("dq_items", item_id=snapshot_id)
        if item is None:
            raise KeyError(f"Unknown snapshot: {snapshot_id}")
        if require_ready and item.get("ingest_status") != "ready":
            raise SnapshotNotReadyError(
                f"Snapshot {snapshot_id!r} is not ready (ingest_status={item.get('ingest_status')!r})"
            )
        if not allow_historical and item.get("snapshot_status") != "active":
            raise SnapshotNotReadyError(
                f"Snapshot {snapshot_id!r} is not active "
                f"(snapshot_status={item.get('snapshot_status')!r})"
            )
        model = snapshot_read_model(snapshot_id)
        asset = db.query_one("dq_assets", asset_id=model["asset_id"])
        if asset is None:
            raise ValueError(f"Snapshot {snapshot_id!r} has no asset record")
        table_names = tuple(row["table_name"] for row in db.query(
            "dq_item_tables", item_id=snapshot_id, order_by="table_name"
        ))
        return SnapshotRef(
            snapshot_id=snapshot_id,
            asset_id=asset["asset_id"],
            system_id=asset["system_id"],
            asset_name=asset["display_name"],
            kind=asset["kind"],
            version_no=int(model.get("version_no") or 1),
            snapshot_label=model.get("snapshot_label"),
            snapshot_status=model.get("status") or "unknown",
            ingest_status=item.get("ingest_status") or "unknown",
            tables=table_names,
        )

    def load_table(self, snapshot_id: str, table: str,
                   columns: Sequence[str] | None = None, *,
                   require_ready: bool = True,
                   allow_historical: bool = False) -> pd.DataFrame:
        ref = self.reference(snapshot_id, require_ready=require_ready,
                             allow_historical=allow_historical)
        if table not in ref.tables:
            raise KeyError(f"Unknown table {table!r} for snapshot {snapshot_id!r}")
        return self._table_reader(snapshot_id, table, columns=columns)
