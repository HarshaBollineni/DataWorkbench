"""Adapters for production-shaped CSV/YAML data dictionaries."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value == "":
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return True
    missing = pd.isna(value)
    return not bool(missing) if not hasattr(missing, "__len__") else True


def _clean(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if _is_present(value)}


def load_dictionary(path: str | Path) -> list[dict[str, Any]]:
    resource = Path(path)
    if resource.suffix.lower() == ".csv":
        records = pd.read_csv(resource, keep_default_na=False).to_dict(orient="records")
    elif resource.suffix.lower() in {".yaml", ".yml"}:
        document = yaml.safe_load(resource.read_text(encoding="utf-8"))
        records = document.get("columns", []) if isinstance(document, dict) else []
    else:
        raise ValueError(f"Unsupported dictionary format: {resource.suffix}")
    if not isinstance(records, list):
        raise ValueError("Dictionary must contain a list of columns")
    normalized = []
    for record in records:
        item = _clean(dict(record))
        item["name"] = item.get("name") or item.get("column_name")
        if not item["name"]:
            raise ValueError("Every dictionary row requires column_name or name")
        normalized.append(item)
    return normalized


def overlay_dictionary(base: pd.DataFrame, supplied: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Overlay populated external dictionary fields onto a table-aligned base dictionary."""
    result = base.copy()
    if "column_name" not in result:
        raise ValueError("Base dictionary requires column_name")
    records = supplied.to_dict(orient="records") if isinstance(supplied, pd.DataFrame) else supplied
    normalized = []
    for record in records:
        item = _clean(dict(record))
        name = str(item.get("column_name") or item.get("name") or "").strip()
        if not name:
            raise ValueError("Every supplied dictionary row requires column_name or name")
        item["column_name"] = name
        normalized.append(item)
    names = [item["column_name"] for item in normalized]
    if len(names) != len(set(names)):
        raise ValueError("Supplied dictionary column names must be unique")
    unknown = sorted(set(names) - set(result["column_name"].astype(str)))
    if unknown:
        raise KeyError(f"Supplied dictionary columns are absent from input data: {unknown}")
    editable = set(result.columns) - {"column_name"}
    for field in editable:
        result[field] = result[field].astype(object)
    result = result.set_index("column_name", drop=False)
    for item in normalized:
        name = item["column_name"]
        for field in editable & set(item):
            value = item[field]
            if _is_present(value):
                result.at[name, field] = str(value).strip().lower() if field == "role" else value
    return result.reset_index(drop=True)
