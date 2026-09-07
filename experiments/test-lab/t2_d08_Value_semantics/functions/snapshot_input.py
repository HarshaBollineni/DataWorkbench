"""Load value-semantics inputs from an Analysis Artifact Repository snapshot."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = WORKSPACE_ROOT / "source-codes" / "backend"
D11_EXTRACTOR = WORKSPACE_ROOT / "experiments" / "test-lab" / "t2_d11_dir_consistency" / "functions" / "extract_saved_schema.py"


def _schema_module():
    module_name = "_t2_d11_saved_schema_for_value_semantics"
    if module_name in sys.modules:
        return sys.modules[module_name]
    specification = importlib.util.spec_from_file_location(module_name, D11_EXTRACTOR)
    if specification is None or specification.loader is None:
        raise ImportError(f"Unable to load saved-schema extractor: {D11_EXTRACTOR}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


def _serialized_values(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, dict):
        values = list(values)
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return "|".join(str(value) for value in values if str(value).strip())


def build_snapshot_input(*, data: pd.DataFrame, schema: dict[str, Any], table: str,
                         snapshot_id: str = "",
                         eligible_roles: Iterable[str] | None = None,
                         selected_columns: Iterable[str] | None = None,
                         include_dictionary_metadata: bool = True) -> dict[str, Any]:
    """Adapt already-loaded snapshot artifacts into value-semantics inputs."""
    data = data.copy()
    table_schema = next(item for item in schema["tables"] if item["name"] == table)
    rows = []
    for column in table_schema["columns"]:
        profile = column.get("profile") or {}
        dictionary_values = profile.get("declared_special_values") or profile.get("special_values") or []
        confirmed = bool(profile.get("special_values_confirmed"))
        rows.append({
            "column_name": column["name"],
            "business_name": "",
            "description": column.get("description") or profile.get("description") or "",
            "data_type": column.get("data_type") or "",
            "role": str(column.get("role") or "ignore").strip().lower(),
            "allowed_values": "",
            "sentinel_value": _serialized_values(dictionary_values) if confirmed else "",
            "sentinel_meaning": ("confirmed snapshot special value"
                                 if confirmed and dictionary_values else ""),
            "source_system": "Analysis Artifact Repository",
            "notes": f"column_profile_artifact_id={column.get('column_profile_artifact_id') or ''}",
        })
    aar_sourced_dictionary = pd.DataFrame(rows)

    # Preserve a stable technical reference without assigning it a semantic role.
    row_reference_column = "ROW_ID" if "ROW_ID" in data.columns else "__ROW_REFERENCE__"
    if row_reference_column not in data.columns:
        data.insert(0, row_reference_column, [f"{table}:{index}" for index in range(len(data))])
        aar_sourced_dictionary = pd.concat([pd.DataFrame([{
            "column_name": row_reference_column, "business_name": "Technical row reference",
            "description": "Generated stable row ordinal for experiment output only.",
            "data_type": "string", "role": "ignore", "allowed_values": "",
            "sentinel_value": "", "sentinel_meaning": "",
            "source_system": "value-semantics experiment", "notes": "not a semantic entity identifier",
        }]), aar_sourced_dictionary], ignore_index=True)

    dictionary = dictionary_evidence_view(
        aar_sourced_dictionary, include_dictionary_metadata=include_dictionary_metadata,
    )

    role_filter = {str(role).strip().casefold() for role in (eligible_roles or ()) if str(role).strip()}
    column_filter = {str(column) for column in (selected_columns or ()) if str(column).strip()}
    selected = dictionary.loc[~dictionary["column_name"].eq(row_reference_column)].copy()
    if role_filter:
        selected = selected.loc[selected["role"].str.casefold().isin(role_filter)]
    if column_filter:
        missing = sorted(column_filter - set(dictionary["column_name"]))
        if missing:
            raise KeyError(f"Selected columns are absent from snapshot table: {missing}")
        selected = selected.loc[selected["column_name"].isin(column_filter)]
    return {
        "source": "snapshot", "snapshot_id": snapshot_id, "table": table,
        "asset_id": schema["asset_id"], "schema": schema, "data": data,
        "aar_sourced_dictionary": aar_sourced_dictionary,
        "dictionary": dictionary, "selected_dictionary": selected.reset_index(drop=True),
        "row_reference_column": row_reference_column,
        "include_dictionary_metadata": include_dictionary_metadata,
    }


def load_snapshot_input(*, snapshot_id: str, table: str,
                        eligible_roles: Iterable[str] | None = None,
                        selected_columns: Iterable[str] | None = None,
                        include_dictionary_metadata: bool = True) -> dict[str, Any]:
    """Load a repository snapshot and return full plus role-filtered dictionaries."""
    extractor = _schema_module()
    repository = extractor._load_repository()
    schema = extractor.extract_saved_schema(repository, snapshot_id=snapshot_id, table=table)

    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    from analysis_runtime.snapshots import SnapshotLoader

    data = SnapshotLoader().load_table(snapshot_id=snapshot_id, table=table)
    return build_snapshot_input(
        data=data, schema=schema, table=table, snapshot_id=snapshot_id,
        eligible_roles=eligible_roles, selected_columns=selected_columns,
        include_dictionary_metadata=include_dictionary_metadata,
    )


def dictionary_evidence_view(dictionary: pd.DataFrame, *, include_dictionary_metadata: bool) -> pd.DataFrame:
    """Return matching metadata with dictionary evidence either included or withheld."""
    result = dictionary.copy()
    if not include_dictionary_metadata:
        for column in ("business_name", "description", "allowed_values", "sentinel_value", "sentinel_meaning"):
            if column in result:
                result[column] = ""
    return result
