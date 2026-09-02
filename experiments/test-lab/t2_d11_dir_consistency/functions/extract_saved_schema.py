"""Print a saved Data Sourcing schema from the Analysis Artifact Repository.

The production application persists one ``table_profile`` and one
``column_profile`` per confirmed table/column.  This experiment turns those
artifacts into a schema and profiling document suitable as input to later
agents. Each column's complete saved profile payload is retained losslessly.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


# This module lives under <workspace>/experiments/test-lab/<experiment>/functions.
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = WORKSPACE_ROOT / "source-codes" / "backend"


def _load_repository() -> Any:
    """Import the production AAR without depending on the caller's directory."""
    sys.path.insert(0, str(BACKEND_ROOT))
    # Load the runtime package first. Importing the domain repository first in
    # a fresh standalone process otherwise meets the compatibility re-export
    # in analysis_runtime.__init__ while the repository is only half loaded.
    import analysis_runtime.contracts  # noqa: F401
    from domains.aar.repository import AnalysisArtifactRepository

    return AnalysisArtifactRepository()


def _table_name(metadata: Any) -> str | None:
    return (metadata.identity or {}).get("table")


def extract_saved_schema(
    repository: Any,
    *,
    asset_id: str | None = None,
    snapshot_id: str | None = None,
    table: str | None = None,
) -> dict[str, Any]:
    """Return the latest matching active schema and profiles as a JSON dict."""
    table_profiles = repository.list(
        asset_id=asset_id,
        snapshot_id=snapshot_id,
        artifact_type="table_profile",
        status="active",
    )
    available_tables = sorted(
        table_name
        for item in table_profiles
        if (table_name := _table_name(item)) is not None
    )
    if table is not None:
        table_profiles = [item for item in table_profiles if _table_name(item) == table]
    if not table_profiles:
        filters = {"asset_id": asset_id, "snapshot_id": snapshot_id, "table": table}
        selected = ", ".join(f"{key}={value!r}" for key, value in filters.items() if value)
        suffix = f" for {selected}" if selected else ""
        available = (
            f" Available tables: {', '.join(repr(name) for name in available_tables)}."
            if available_tables
            else ""
        )
        raise LookupError(
            f"No active table-profile schema artifacts found{suffix}.{available}"
        )

    # Repository.list() is newest-first. If no snapshot was requested, use the
    # newest table profile to identify the latest saved schema generation.
    selected_snapshot = snapshot_id or table_profiles[0].snapshot_id
    selected_asset = asset_id or table_profiles[0].asset_id
    table_profiles = [
        item
        for item in table_profiles
        if item.snapshot_id == selected_snapshot and item.asset_id == selected_asset
    ]

    column_profiles = repository.list(
        asset_id=selected_asset,
        snapshot_id=selected_snapshot,
        artifact_type="column_profile",
        status="active",
    )
    columns_by_table: dict[str | None, dict[str, tuple[Any, dict[str, Any]]]] = {}
    for metadata in column_profiles:
        column_table = _table_name(metadata)
        if table is not None and column_table != table:
            continue
        _, payload = repository.get(metadata.artifact_id)
        columns_by_table.setdefault(column_table, {})[metadata.feature] = (metadata, payload)

    tables: list[dict[str, Any]] = []
    for table_metadata in sorted(table_profiles, key=lambda item: _table_name(item) or ""):
        table_name = _table_name(table_metadata)
        _, table_payload = repository.get(table_metadata.artifact_id)
        available = columns_by_table.get(table_name, {})
        declared_order = list(table_payload.get("columns") or [])
        extra_names = sorted(set(available) - set(declared_order))
        column_names = declared_order + extra_names

        columns = []
        for name in column_names:
            column_metadata, payload = available.get(name, (None, {}))
            columns.append(
                {
                    "name": name,
                    "data_type": payload.get("data_type"),
                    "classification": payload.get("classification"),
                    "role": payload.get("role"),
                    "description": payload.get("description") or "",
                    "column_profile_artifact_id": (
                        column_metadata.artifact_id if column_metadata else None
                    ),
                    # Keep the complete immutable AAR payload. This includes
                    # counts, missingness, ranges, moments, percentiles,
                    # histograms, top values and special-value evidence when
                    # those measurements were saved during Data Sourcing.
                    "profile": payload,
                }
            )

        tables.append(
            {
                "name": table_name,
                "row_count": table_payload.get("row_count"),
                "column_count": table_payload.get("column_count", len(columns)),
                "table_profile_artifact_id": table_metadata.artifact_id,
                "columns": columns,
            }
        )

    return {
        "source": "Analysis Artifact Repository",
        "asset_id": selected_asset,
        "snapshot_id": selected_snapshot,
        "tables": tables,
    }


def build_agent_input(
    schema: dict[str, Any], *, roles: Iterable[str] | None = None,
    include_statistics: bool = False,
) -> list[dict[str, Any]]:
    """Create an LLM-readable list containing only column summaries.

    Descriptions are copied exactly as saved. They remain empty when the
    selected snapshot did not persist a confirmed dictionary description.
    When supplied, ``roles`` is matched case-insensitively. Profiling statistics
    are omitted unless ``include_statistics`` is explicitly enabled.
    """
    selected_roles = {
        str(role).strip().casefold() for role in (roles or ()) if str(role).strip()
    }

    def is_string_profile(column_type: Any) -> bool:
        normalized = str(column_type or "").strip().casefold()
        return normalized in {"str", "string", "object", "category", "categorical", "text"}

    def frequency_statistics(profile: dict[str, Any]) -> dict[str, Any]:
        top_k = profile.get("top_k") or {}
        non_null_count = int(profile.get("non_null_count") or 0)
        values = [
            {
                "value": value,
                "frequency": int(frequency),
                "share": round(int(frequency) / non_null_count, 6) if non_null_count else None,
            }
            for value, frequency in sorted(
                top_k.items(), key=lambda item: (-int(item[1]), str(item[0]))
            )
        ]
        distinct_count = profile.get("distinct_count")
        return {
            "total_count": profile.get("total_count"),
            "non_null_count": profile.get("non_null_count"),
            "null_count": profile.get("null_count"),
            "null_share": profile.get("null_share"),
            "distinct_count": distinct_count,
            "mode": profile.get("mode"),
            "mode_frequency": profile.get("mode_count"),
            "mode_share": profile.get("mode_share"),
            "values_are_top_k": (
                distinct_count is not None and int(distinct_count) > len(values)
            ),
            "values": values,
        }

    columns = []
    for table in schema.get("tables", []):
        for column in table.get("columns", []):
            column_role = str(column.get("role") or "").strip()
            if selected_roles and column_role.casefold() not in selected_roles:
                continue
            profile = column.get("profile") or {}
            column_type = column.get("data_type")
            item = {
                "name": column.get("name"),
                "type": column_type,
                "role": column_role,
                "description": column.get("description") or "",
            }
            if include_statistics:
                if is_string_profile(column_type):
                    item["frequency_statistics"] = frequency_statistics(profile)
                else:
                    item["summary_statistics"] = {
                        "min": profile.get("min"),
                        "q1": profile.get("q1"),
                        "mean": profile.get("mean"),
                        "median": profile.get("median"),
                        "q3": profile.get("q3"),
                        "max": profile.get("max"),
                        "std": profile.get("stddev"),
                        "total_count": profile.get("total_count"),
                        "non_null_count": profile.get("non_null_count"),
                        "null_count": profile.get("null_count"),
                        "null_share": profile.get("null_share"),
                        "distinct_count": profile.get("distinct_count"),
                }
            columns.append(item)
    return columns


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract and print a saved schema from the Analysis Artifact Repository."
    )
    parser.add_argument("--asset-id", help="Select a specific Data Sourcing asset.")
    parser.add_argument("--snapshot-id", help="Select a specific saved snapshot.")
    parser.add_argument("--table", help="Select one table from the saved schema.")
    parser.add_argument(
        "--agent-input",
        action="store_true",
        help="Print only column summaries and statistics for an LLM agent.",
    )
    parser.add_argument(
        "--roles",
        nargs="+",
        metavar="ROLE",
        help="With --agent-input, include only columns having one of these roles.",
    )
    parser.add_argument(
        "--include-statistics",
        action="store_true",
        help="With --agent-input, include numeric summaries or string frequencies.",
    )
    parser.add_argument(
        "--compact", action="store_true", help="Print compact JSON instead of indented JSON."
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        schema = extract_saved_schema(
            _load_repository(),
            asset_id=args.asset_id,
            snapshot_id=args.snapshot_id,
            table=args.table,
        )
        if args.agent_input:
            schema = build_agent_input(
                schema,
                roles=args.roles,
                include_statistics=args.include_statistics,
            )
    except (LookupError, KeyError, OSError, ValueError) as exc:
        print(f"Unable to extract saved schema: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(schema, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
