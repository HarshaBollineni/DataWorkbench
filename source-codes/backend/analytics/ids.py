"""The bounded five-family typed-ID contract for ANL-01/02.

Asset, version and dictionary IDs already have human-quotable prefixes in the
asset model.  Snapshot rows retain the 0.4.0 internal item key for compatibility;
usage events expose that key as a stable ``SN:<item_id>`` typed reference.  A
variable is naturally identified by its asset system ID, table and column, so
it is deterministic and does not need a counter.
"""
from __future__ import annotations

import re

import system_db as s

FAMILIES = ("asset", "version", "snapshot", "variable", "dictionary_version")
_PREFIXES = {
    "asset": ("DS", "DB"), "version": ("VR",), "snapshot": ("SN:",),
    "variable": ("VAR:",), "dictionary_version": ("DV", "DV:"),
}


def variable_id(asset_system_id: str, table: str, column: str) -> str:
    """Return the deterministic fifth-family ID, never a counter allocation."""
    if not asset_system_id or not table or not column:
        raise ValueError("asset system ID, table and column are required for a variable ID")
    return f"VAR:{asset_system_id}:{table}:{column}"


def typed_id(object_type: str, value: str, *, table: str | None = None,
             column: str | None = None) -> str:
    """Validate an already-addressable ID; reject scope expansion explicitly."""
    if object_type not in FAMILIES:
        raise ValueError(
            f"Typed IDs in 0.5.0 are limited to {', '.join(FAMILIES)}; "
            f"{object_type!r} is out of scope."
        )
    if object_type == "variable":
        if table is not None or column is not None:
            if table is None or column is None:
                raise ValueError("variable IDs require both table and column")
            return variable_id(value, table, column)
        if not re.fullmatch(r"VAR:(?:DS|DB)\d{4,}:[^:]+:[^:]+", value):
            raise ValueError(f"{value!r} is not a typed variable ID")
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"A non-empty {object_type} ID is required")
    if not any(value.startswith(prefix) for prefix in _PREFIXES[object_type]):
        raise ValueError(f"{value!r} is not a typed {object_type} ID")
    return value


def public_id(object_type: str, internal_id: str, *, table: str | None = None,
              column: str | None = None) -> str:
    """Resolve a model key to the stable typed value used in usage_events."""
    if object_type == "asset":
        row = s.query_one("dq_assets", asset_id=internal_id)
        return typed_id("asset", row["system_id"] if row else internal_id)
    if object_type == "version":
        row = s.query_one("dq_asset_versions", version_id=internal_id)
        return typed_id("version", row["version_id"] if row else internal_id)
    if object_type == "snapshot":
        # The item key remains the physical primary key (P-06).  Prefixing it
        # makes the event reference typed without changing every 0.4.0 join.
        return typed_id("snapshot", internal_id if internal_id.startswith("SN:") else f"SN:{internal_id}")
    if object_type == "dictionary_version":
        row = s.query_one("dq_asset_dictionaries", dictionary_version_id=internal_id)
        return typed_id("dictionary_version", row["dictionary_version_id"] if row else internal_id)
    if object_type == "variable":
        asset = s.query_one("dq_assets", asset_id=internal_id)
        if not asset:
            raise ValueError(f"Unknown asset {internal_id!r} for variable ID")
        return typed_id("variable", asset["system_id"], table=table, column=column)
    raise ValueError(f"Unsupported typed-ID family: {object_type!r}")


def asset_system_id(asset_id: str) -> str:
    return public_id("asset", asset_id)
