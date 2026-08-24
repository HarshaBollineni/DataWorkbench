"""PLT-02 — the delivery/family seam on dq_items.

Multiple uploads of "the same" dataset over time (a monthly refresh, a
re-cut of the same population) share a `dataset_family_id` and get an
ordered `delivery_seq` within it, so a later phase (drift monitoring,
dataset-vs-dataset comparison) can reason about "this family's history"
without re-deriving identity from scratch every time.

Every pre-existing dq_items row is backfilled into a single-item family of
its own by system_db.init_schema() (see `_backfill_delivery_defaults`);
`ensure_delivery_defaults` here is the same operation exposed as a
callable for anyone who wants to invoke it directly.
"""
from __future__ import annotations

from typing import Any

import system_db as s


def ensure_delivery_defaults() -> int:
    """Backfill helper — give every dq_items row that has never been
    assigned a family (dataset_family_id IS NULL) a single-item family of
    its own: dataset_family_id = item_id, delivery_seq = 1. Idempotent —
    already-backfilled rows, and rows already joined to a real family by
    `register_delivery`, are left untouched. Returns the number of rows
    updated by this call.
    """
    updated = 0
    for row in s.query("dq_items"):
        if row.get("dataset_family_id") is None:
            s.update("dq_items", {"item_id": row["item_id"]},
                     {"dataset_family_id": row["item_id"], "delivery_seq": 1})
            updated += 1
    return updated


def family_deliveries(dataset_family_id: str) -> list[dict[str, Any]]:
    """Every dq_items row belonging to `dataset_family_id`, ordered by
    delivery_seq (oldest delivery first)."""
    return s.query("dq_items", order_by="delivery_seq", dataset_family_id=dataset_family_id)


def register_delivery(
    item_id: str, dataset_family_id: str, as_of_date: str | None = None
) -> dict[str, Any]:
    """Join `item_id` to `dataset_family_id` as its next delivery.

    delivery_seq is 1 if the family has no OTHER members yet, otherwise
    ``1 + max(existing delivery_seq among the family's other members)`` —
    a later delivery is numbered after every prior one and never
    renumbers them; only `item_id`'s own row is written.
    """
    existing = family_deliveries(dataset_family_id)
    other_seqs = [row["delivery_seq"] or 0 for row in existing if row["item_id"] != item_id]
    next_seq = (max(other_seqs) + 1) if other_seqs else 1
    changes: dict[str, Any] = {"dataset_family_id": dataset_family_id, "delivery_seq": next_seq}
    if as_of_date is not None:
        changes["as_of_date"] = as_of_date
    s.update("dq_items", {"item_id": item_id}, changes)
    return {"item_id": item_id, "dataset_family_id": dataset_family_id,
            "delivery_seq": next_seq, "as_of_date": as_of_date}
