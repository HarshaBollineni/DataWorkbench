"""Galileo v2 deterministic workflow services."""
from __future__ import annotations

import json
import hashlib
import io
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import numpy as np

import system_db as db
from assets import identity as asset_identity
from assets import schema_check
from dq_tests.global_rules import is_period_axis
from dq_tests.param_specs import param_spec as _param_spec
from dq_tests.registry import get_impl_source
from ingest import classify as ing_classify
from ingest import dictionary_state as ing_dictionary_state
from ingest import headers as ing_headers
from ingest import mapping as ing_mapping
from ingest import records as ing_records
from ingest import status as ing_status
from ingest import warnings as ing_warnings
from ingest.errors import IngestCorruptionError
from ingest.roles import infer_inventory_role
from ingest.workbook_inspection import inspect_delimited, inspect_excel
from supporting_analyses.missingness_engine.dictionary_mapping import (
    FIELD_BY_NAME,
    VALUE_VOCABULARIES,
    canonicalize_dictionary,
    inspect_dictionary,
    suggest_dictionary_mapping,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
# Every uploaded source belongs to one assessment item.  Azure sets UPLOAD_DIR
# to its mounted File Share (/data/uploads); local development keeps the
# previous backend/uploads default.  Resolve once at process start so every
# workflow component reads the same durable location.
UPLOAD_ROOT = Path(os.environ.get("UPLOAD_DIR") or (BACKEND_ROOT / "uploads")).resolve()
# Azure Files is suitable for durable source files but not for an open SQLite
# database: its SMB implementation cannot provide SQLite's byte-range locks.
# Per-item databases are a regenerable execution cache, so keep them on the
# container's local disk and reconstruct them from the durable uploads after a
# restart.  ITEM_DB_DIR is configurable for local diagnostics/tests.
ITEM_DB_ROOT = Path(os.environ.get("ITEM_DB_DIR") or
                    (Path(tempfile.gettempdir()) / "archimedes-item-dbs")).resolve()

# AST-17/P-01: fixed, bounded fingerprint settings.  Keeping these named
# makes the O(columns) capture contract inspectable and stable across runs.
FINGERPRINT_HISTOGRAM_BINS = 10  # AST-17 fixed-bin numeric distribution fingerprint.
FINGERPRINT_DISTINCT_SET_MAX = 100  # AST-17 hash only low-cardinality sets.
PROFILE_TOP_K_LIMIT = 50  # Retain enough categorical evidence for non-identifier analysis.
TIME_BASIS_PERIOD = "period"
DICTIONARY_PERIOD_ROLE = TIME_BASIS_PERIOD
INVENTORY_ROLE_BY_DICTIONARY = {
    "feature": "Feature",
    "score": "Feature",
    "target": "Target",
    "identifier": "Identifier",
    DICTIONARY_PERIOD_ROLE: "Period",
    "date": "Date",
    "weight": "Weight",
    "group": "Group",
    "ignore": "Ignore",
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _usage_event(event_type: str, item_id: str, *, actor: str = "system",
                 detail: dict | None = None, object_type: str = "snapshot",
                 object_id: str | None = None, **kwargs: Any) -> None:
    """Record a usage event at an existing ingestion call site."""
    from analytics.events import event_object_id, record_event  # noqa: PLC0415
    item = db.query_one("dq_items", item_id=item_id) or {}
    asset = db.query_one("dq_assets", asset_id=item.get("dataset_family_id")) or {}
    raw_id = object_id or item_id
    record_event(event_type=event_type, actor=actor or "system", at=db.now_ist(),
                 object_type=object_type,
                 object_id=event_object_id(object_type, raw_id, **kwargs),
                 workflow_context=asset.get("system_id"), detail=detail)


def _item_dir(item_id: str) -> Path:
    path = UPLOAD_ROOT / item_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clear_item_work(item_id: str) -> None:
    """Discard derived state before a user re-uploads a missing source."""
    for issue in db.query("issues_v2", item_id=item_id):
        db.delete("tracked_issues_v2", issue_row_id=issue["issue_row_id"])
    for table in ("issues_v2", "results_v2", "scores_v2", "plan_v2",
                  "variable_inventory", "dq_item_tables", "dq_item_files"):
        db.delete(table, item_id=item_id)
    item_dir = UPLOAD_ROOT / item_id
    if item_dir.exists():
        shutil.rmtree(item_dir)
    work_dir = ITEM_DB_ROOT / item_id
    if work_dir.exists():
        shutil.rmtree(work_dir)


def reconcile_persisted_items() -> int:
    """Flag pre-0.2 items whose files were stored in an old container.

    Earlier releases kept v2 files below /app/backend/uploads, which disappears
    when an Azure revision is replaced.  Preserve the inventory record and make
    the remediation explicit instead of presenting an item that cannot run.
    """
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    changed = 0
    root = UPLOAD_ROOT.resolve()
    for item in db.query("dq_items"):
        files = db.query("dq_item_files", item_id=item["item_id"])
        if not files:
            continue
        valid = True
        for record in files:
            raw_path = record.get("path")
            try:
                path = Path(raw_path).resolve()
                path.relative_to(root)
            except (TypeError, ValueError):
                valid = False
                break
            if not path.is_file():
                valid = False
                break
        if not valid and item.get("status") != "requires_reupload":
            db.update("dq_items", {"item_id": item["item_id"]}, {
                "status": "requires_reupload", "module_tag": "Data Sourcing",
                "updated_at": db.now_ist(),
            })
            changed += 1
    return changed


def _item_db(item_id: str) -> Path:
    path = ITEM_DB_ROOT / item_id / "item.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _data_files(item_id: str) -> list[Path]:
    """Return every durable data source for an item, in upload order."""
    rows = db.query("dq_item_files", item_id=item_id, role="data")
    rows.sort(key=lambda row: row.get("completed_at") or "")
    return [Path(row["path"]) for row in rows if row.get("path")]


def _rebuild_item_db(item_id: str) -> None:
    """Recreate the local execution cache from durable uploaded sources."""
    for source in _data_files(item_id):
        if not source.is_file():
            continue
        for table, frame in _read_tabular(source, _source_options(item_id)).items():
            _write_table(item_id, table, frame)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _read_table(item_id: str, table: str) -> pd.DataFrame:
    import sqlite3
    item_db = _item_db(item_id)
    if not item_db.exists():
        _rebuild_item_db(item_id)
    with closing(sqlite3.connect(item_db)) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table}"', conn)


def read_snapshot_table(item_id: str, table: str,
                        columns: list[str] | tuple[str, ...] | None = None) -> pd.DataFrame:
    """Public, read-only table seam for analytics over a persisted snapshot.

    Table and column names are validated against ``dq_item_tables`` before the
    existing cache reader is called. Returning a copy prevents callers from
    confusing in-memory mutation with mutation of the stored snapshot.
    """
    require_item(item_id)
    available_tables = {row["table_name"]: row.get("columns") or []
                        for row in tables(item_id)}
    if table not in available_tables:
        raise KeyError(f"Unknown table {table!r} for snapshot {item_id!r}")
    selected = list(columns) if columns is not None else None
    if selected is not None:
        if len(set(selected)) != len(selected):
            raise ValueError("columns must not contain duplicates")
        missing = [column for column in selected if column not in available_tables[table]]
        if missing:
            raise KeyError(f"Unknown column(s) for {table!r}: {missing}")
    frame = _read_table(item_id, table)
    return frame.loc[:, selected].copy() if selected is not None else frame.copy()


def _write_table(item_id: str, table: str, df: pd.DataFrame) -> None:
    """0.5.0 Step 3b (AST-08/AST-13, rule 9) — ``item_id`` is now understood
    as a SNAPSHOT id; the ``(item_id, table_name)`` key is unchanged (P-06),
    but two things are new: (1) a superseded snapshot's cache is immutable —
    read the row first and refuse rather than silently overwriting stored
    content that AST-08 says is retained for audit and rollback; (2)
    contribute this table's row/column totals up to the snapshot's own
    ``dq_items`` row (workbook totals across every table the snapshot
    carries — A-Q03), the new S3a columns Step 4's upload flow will read.
    """
    import sqlite3
    item = db.query_one("dq_items", item_id=item_id)
    if item is not None and item.get("snapshot_status") == "superseded":
        raise ValueError(
            f"Cannot write to snapshot {item_id!r}: it has been superseded and its stored "
            "content is immutable (AST-08) — add a new snapshot instead of overwriting one."
        )
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", table).strip("_") or "dataset"
    with closing(sqlite3.connect(_item_db(item_id))) as conn:
        df.to_sql(clean, conn, if_exists="replace", index=False)
        conn.commit()
    db.upsert("dq_item_tables", {
        "item_id": item_id, "table_name": clean, "row_count": int(len(df)),
        "col_count": int(len(df.columns)), "columns": list(map(str, df.columns)),
    })
    if item is not None:
        totals = db.execute(
            "SELECT COALESCE(SUM(row_count),0) AS rc, COALESCE(SUM(col_count),0) AS cc "
            "FROM dq_item_tables WHERE item_id=?", [item_id])
        row_count = totals[0]["rc"] if totals else int(len(df))
        column_count = totals[0]["cc"] if totals else int(len(df.columns))
        db.update("dq_items", {"item_id": item_id},
                 {"row_count": row_count, "column_count": column_count})


def _delimiter_value(value: Any) -> str | None:
    raw = str(value or "").strip()
    aliases = {"comma": ",", "pipe": "|", "tab": "\t", "semicolon": ";"}
    return aliases.get(raw.casefold(), raw or None)


def _sheet_value(value: Any) -> str | int | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    return int(raw) if raw.isdigit() else raw


def _source_options(item_id: str) -> dict[str, Any]:
    item = require_item(item_id)
    return item.get("source_parsing_options_json") or {}


def _read_tabular(path: Path, options: dict[str, Any] | None = None) -> dict[str, pd.DataFrame]:
    options = options or {}
    suffix = path.suffix.lower()
    delimiter = _delimiter_value(options.get("delimiter"))
    selected_sheet = _sheet_value(options.get("data_sheet"))
    if suffix in {".csv", ".tsv", ".txt"}:
        if suffix == ".tsv" and not delimiter:
            delimiter = "\t"
        return {path.stem: pd.read_csv(path, sep=delimiter or None, engine="python")}
    if suffix in {".xlsx", ".xls"}:
        sheets = pd.read_excel(path, sheet_name=selected_sheet if selected_sheet is not None else None)
        if isinstance(sheets, pd.DataFrame):
            name = str(selected_sheet if selected_sheet is not None else path.stem)
            return {name: sheets}
        return {str(k): v for k, v in sheets.items()}
    if suffix == ".zip":
        out = {}
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    with zf.open(name) as fh:
                        out[Path(name).stem] = pd.read_csv(fh, sep=delimiter or None, engine="python")
        return out
    raise ValueError(f"Unsupported data file type: {suffix}")


def _norm_name(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[\s\-]+", "_", str(value or "").strip().casefold())).strip("_")


def _file_for_role(item_id: str, role: str) -> Path | None:
    rows = db.query("dq_item_files", item_id=item_id, role=role)
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("completed_at") or "")
    path = rows[-1].get("path")
    return Path(path) if path else None


def _classification_from_declared(value: Any) -> str | None:
    raw = str(value or "").strip().casefold()
    if not raw:
        return None
    if any(token in raw for token in ("date", "time", "timestamp")):
        return "datetime"
    if any(token in raw for token in ("int", "float", "double", "decimal", "numeric", "number", "rate", "ratio", "continuous")):
        return "numerical"
    if any(token in raw for token in ("bool", "binary", "flag", "indicator")):
        return "binary"
    if "ordinal" in raw or "grade" in raw or "rating" in raw:
        return "ordinal"
    if any(token in raw for token in ("category", "categorical", "enum")):
        return "categorical"
    if "id" == raw or raw.endswith("_id") or "identifier" in raw:
        return "identifier"
    if any(token in raw for token in ("str", "char", "text", "object")):
        return "text"
    return None


def _set_dict_entry(out: dict, table_norm: str, col_norm: str, entry: dict) -> None:
    """Record one dictionary row, hard-failing on structural corruption
    (ING-04): the SAME table declaring the SAME column twice (4-T3). The
    cross-table "*" catch-all merge stays additive-only (first-wins,
    ``setdefault``) — reusing a column name across two DIFFERENT declared
    tables is normal multi-table usage, not corruption."""
    bucket = out.setdefault(table_norm, {})
    if col_norm in bucket:
        where = f" for table '{table_norm}'" if table_norm != "*" else ""
        raise IngestCorruptionError(
            f"The data dictionary declares '{col_norm}'{where} more than once — "
            "duplicate column definitions are not allowed.")
    bucket[col_norm] = entry
    if table_norm != "*":
        out["*"].setdefault(col_norm, entry)


def _dictionary_missing_codes(value: Any) -> list[Any]:
    """Preserve each declared code once; the analysis engine adds match forms later."""
    if value is None or (not isinstance(value, (list, tuple, set)) and pd.isna(value)):
        return []
    values = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    out = []
    for raw in values:
        if isinstance(raw, (int, float, np.integer, np.floating)) and not pd.isna(raw):
            numeric = float(raw)
            item = int(numeric) if numeric.is_integer() else numeric
        else:
            item = str(raw).strip()
        if item != "" and item not in out:
            out.append(item)
    return out


def _parse_dictionary(item_id: str) -> dict[str, dict[str, dict[str, str]]]:
    """Return normalized table -> normalized column -> dictionary metadata.

    Raises ``IngestCorruptionError`` (ING-04 hard-fail) if the dictionary
    declares the same column twice for the same table — the only thing in
    the dictionary-validation contract that blocks ingestion; everything
    else becomes a non-blocking structured warning instead (see
    ``ingest.warnings``).
    """
    path = _file_for_role(item_id, "dictionary")
    if not path or not path.exists():
        return {"*": {}}
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _parse_json_dictionary(path)
    options = _source_options(item_id)
    delimiter = _delimiter_value(options.get("delimiter"))
    dictionary_sheet = _sheet_value(options.get("dictionary_sheet"))
    sheets: dict[str, pd.DataFrame]
    if suffix in {".csv", ".tsv", ".txt"}:
        if suffix == ".tsv" and not delimiter:
            delimiter = "\t"
        sheets = {"*": pd.read_csv(path, sep=delimiter or None, engine="python", keep_default_na=False)}
    elif suffix in {".xlsx", ".xls"}:
        loaded = pd.read_excel(path, sheet_name=dictionary_sheet if dictionary_sheet is not None else None)
        sheets = ({str(dictionary_sheet): loaded} if isinstance(loaded, pd.DataFrame)
                  else {str(name): frame for name, frame in loaded.items()})
    else:
        return {"*": {}}
    item = require_item(item_id)
    reviewed_mapping = item.get("dictionary_header_mapping_json") or {}
    value_mapping = item.get("dictionary_value_mapping_json") or {}
    mapping_confirmed = bool(item.get("dictionary_mapping_confirmed"))
    out: dict[str, dict[str, dict[str, str]]] = {"*": {}}
    for sheet, frame in sheets.items():
        lower_cols = {_norm_name(c): c for c in frame.columns}
        table_key = lower_cols.get("table") or lower_cols.get("table_name")
        suggestions = suggest_dictionary_mapping(list(frame.columns))
        header_mapping = reviewed_mapping if mapping_confirmed else {
            name: detail["source_column"] for name, detail in suggestions.items()
            if detail.get("confidence") == "high" and detail.get("source_column")
        }
        try:
            canonical = canonicalize_dictionary(frame, header_mapping).frame.fillna("")
        except ValueError:
            continue
        for index, rec in canonical.iterrows():
            col_norm = _norm_name(rec.get("column_name"))
            if not col_norm:
                continue
            raw_rec = frame.loc[index]
            table_norm = _norm_name(raw_rec.get(table_key)) if table_key else _norm_name(sheet)
            if table_norm in {"", "sheet1", "dictionary", "data_dictionary"}:
                table_norm = "*"
            raw_type = str(rec.get("logical_type") or "").strip()
            raw_role = str(rec.get("role") or "").strip()
            resolved_type = (value_mapping.get("logical_type") or {}).get(raw_type, raw_type)
            resolved_role = (value_mapping.get("role") or {}).get(raw_role, raw_role)
            entry = {
                "definition": str(rec.get("description") or "").strip(),
                "declared_type": str(resolved_type or "").strip(),
                "role": str(resolved_role or "").strip(),
                "missing_value_codes": _dictionary_missing_codes(
                    rec.get("missing_value_codes")),
                "valid_values": str(rec.get("valid_values") or "").strip(),
                "business_context": str(rec.get("business_context") or "").strip(),
            }
            _set_dict_entry(out, table_norm, col_norm, entry)
    return out


def dictionary_inspection(item_id: str) -> dict[str, Any] | None:
    """Inspect the staged dictionary without applying medium-confidence headers."""
    path = _file_for_role(item_id, "dictionary")
    if not path or not path.exists() or path.suffix.lower() == ".json":
        return None
    options = _source_options(item_id)
    delimiter = _delimiter_value(options.get("delimiter"))
    selected = _sheet_value(options.get("dictionary_sheet"))
    if path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        if path.suffix.lower() == ".tsv" and not delimiter:
            delimiter = "\t"
        sheets = {"*": pd.read_csv(path, sep=delimiter or None, engine="python", keep_default_na=False)}
    else:
        loaded = pd.read_excel(path, sheet_name=selected if selected is not None else None)
        sheets = ({str(selected): loaded} if isinstance(loaded, pd.DataFrame)
                  else {str(name): frame for name, frame in loaded.items()})
    item = require_item(item_id)
    reviewed = item.get("dictionary_header_mapping_json") or {}
    confirmed = bool(item.get("dictionary_mapping_confirmed"))
    candidates = []
    for sheet, frame in sheets.items():
        inspected = inspect_dictionary(frame, reviewed if confirmed else None)
        high_mapping = {
            field["name"]: field["source_column"] for field in inspected["fields"]
            if field.get("confidence") == "high" and field.get("source_column")
        }
        active = reviewed if confirmed else high_mapping
        inspected = inspect_dictionary(frame, active)
        inspected["sheet"] = str(sheet)
        inspected["active_mapping"] = active
        inspected["mapping_confirmed"] = confirmed
        candidates.append(inspected)
    if not candidates:
        return None
    return max(candidates, key=lambda value: (
        bool(value.get("active_mapping", {}).get("column_name")),
        len(value.get("active_mapping") or {}), value.get("row_count") or 0,
    ))


def _bind_dictionary_version(item_id: str, parsed: dict[str, Any],
                             actor: str = "system") -> str | None:
    """Create one immutable dictionary version per uploaded dictionary file."""
    files = db.query("dq_item_files", item_id=item_id, role="dictionary")
    if not files:
        return None
    files.sort(key=lambda row: row.get("completed_at") or "")
    source = files[-1]
    item = require_item(item_id)
    existing_id = item.get("dictionary_version_id")
    existing = (db.query_one("dq_asset_dictionaries", dictionary_version_id=existing_id)
                if existing_id else None)
    if (existing and existing.get("source_file_id") == source["file_id"]
            and existing.get("parsed_json") == parsed):
        return existing_id
    asset_id = item.get("dataset_family_id")
    asset = db.query_one("dq_assets", asset_id=asset_id)
    if not asset:
        return None
    versions = db.query("dq_asset_dictionaries", asset_id=asset_id)
    version_id = asset_identity.allocate("dictionary_version")
    version_no = max((int(row.get("dict_version_no") or 0) for row in versions), default=0) + 1
    now = db.now_ist()
    db.insert("dq_asset_dictionaries", {
        "dictionary_version_id": version_id, "asset_id": asset_id,
        "dict_version_no": version_no, "source_file_id": source["file_id"],
        "parsed_json": parsed, "created_by": actor, "created_at": now,
    })
    db.update("dq_assets", {"asset_id": asset_id}, {
        "current_dictionary_version_id": version_id, "updated_at": now,
    })
    db.update("dq_items", {"item_id": item_id}, {"dictionary_version_id": version_id})
    db.insert("dq_asset_events", {
        "event_id": _id("evt"), "asset_id": asset_id,
        "version_no": item.get("version_no"), "snapshot_id": item_id,
        "event_type": "dictionary_version_bound", "actor": actor, "at": now,
        "summary": f"Dictionary version {version_id} was bound to this snapshot.",
        "detail_json": {"dictionary_version_id": version_id,
                        "dictionary_version_no": version_no,
                        "source_file_id": source["file_id"]},
    })
    _usage_event("dictionary_version_created", item_id, actor=actor,
                 object_type="dictionary_version", object_id=version_id,
                 detail={"version_no": version_no, "source_file": source.get("filename")})
    _usage_event("dictionary_bound", item_id, actor=actor,
                 object_type="dictionary_version", object_id=version_id,
                 detail={"version_no": version_no})
    return version_id


_DICT_LEAF_KEYS = {"definition", "description", "declared_type", "type", "data_type",
                   "role", "missing_value_codes", "business_context"}


def _parse_json_dictionary(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """JSON dictionaries: {table:{col:...}}, flat {col:...}, or [{column,...}]."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"*": {}}

    def entry_of(value) -> dict[str, Any]:
        if isinstance(value, dict):
            return {
                "definition": str(value.get("definition") or value.get("description") or "").strip(),
                "declared_type": str(value.get("declared_type") or value.get("type") or value.get("data_type") or "").strip(),
                "role": str(value.get("role") or "").strip(),
                "missing_value_codes": _dictionary_missing_codes(
                    value.get("missing_value_codes")),
                "business_context": str(value.get("business_context") or "").strip(),
            }
        return {"definition": str(value or "").strip(), "declared_type": "",
                "role": "", "missing_value_codes": [], "business_context": ""}

    out: dict[str, dict[str, dict[str, str]]] = {"*": {}}
    if isinstance(data, list):
        for rec in data:
            if not isinstance(rec, dict):
                continue
            col = rec.get("column") or rec.get("column_name") or rec.get("field") or rec.get("name")
            if not col:
                continue
            entry = entry_of(rec)
            table_norm = _norm_name(rec.get("table") or rec.get("table_name") or "") or "*"
            _set_dict_entry(out, table_norm, _norm_name(col), entry)
        return out
    if not isinstance(data, dict):
        return out
    is_nested = bool(data) and all(
        isinstance(v, dict) and not (_DICT_LEAF_KEYS & {str(k).casefold() for k in v})
        for v in data.values()
    )
    if is_nested:
        for table, cols in data.items():
            for col, value in (cols or {}).items():
                entry = entry_of(value)
                out.setdefault(_norm_name(table), {})[_norm_name(col)] = entry
                out["*"].setdefault(_norm_name(col), entry)
    else:
        for col, value in data.items():
            out["*"][_norm_name(col)] = entry_of(value)
    return out


def _dict_entry(dictionary: dict, table: str, column: str) -> dict | None:
    table_norm = _norm_name(table)
    col_norm = _norm_name(column)
    return (dictionary.get(table_norm) or {}).get(col_norm) or (dictionary.get("*") or {}).get(col_norm)


def _dictionary_rows_for_table(dictionary: dict, table: str) -> list[tuple[str, dict]]:
    """The dictionary's declared columns for ``table``, in the order
    ``_parse_dictionary`` first saw them (Python dicts preserve insertion
    order) — the order ING-03's one-to-one mapping consumes them in. Falls
    back to the "*" catch-all bucket (the common single-sheet dictionary
    with no explicit table column)."""
    table_norm = _norm_name(table)
    rows = dictionary.get(table_norm) or {}
    if not rows:
        rows = dictionary.get("*") or {}
    return list(rows.items())


_CORE_PERCENTILES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)


def _special_value_list(value: Any) -> list[Any]:
    """Normalize API/SQLite JSON special-value shapes to one atomic list.

    Raw ``db.execute`` rows retain JSON columns as serialized strings, while
    ``db.query`` and API payloads normally expose lists. Profiling must never
    iterate the characters of the serialized representation.
    """
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            value = json.loads(stripped)
        except (TypeError, ValueError):
            value = stripped
    if value is None:
        return []
    if isinstance(value, set):
        candidates = sorted(value, key=lambda item: str(item))
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        candidates = [value]
    normalized: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if not candidate:
                continue
        identity = (type(candidate).__name__, str(candidate))
        if identity not in seen:
            seen.add(identity)
            normalized.append(candidate)
    return normalized


def _validate_confirmed_special_values(value: Any, logical_type: str | None = None) -> list[Any]:
    """Require confirmed declarations to be literal, atomic source values."""
    special_values = _special_value_list(value)
    descriptive = [str(code) for code in special_values
                   if re.search(r"\s+=\s+", str(code))]
    if descriptive:
        raise ValueError(
            "Confirmed special or missing values must contain only atomic source codes; "
            "move explanations to the column description. Invalid value(s): "
            + ", ".join(descriptive)
        )
    if str(logical_type or "").lower() == "numerical":
        invalid_numeric = []
        for code in special_values:
            try:
                numeric = float(str(code).strip())
            except (TypeError, ValueError):
                numeric = None
            if numeric is None or not np.isfinite(numeric):
                invalid_numeric.append(str(code))
        if invalid_numeric:
            raise ValueError(
                "Confirmed special or missing values for a Numeric column must be "
                "literal numeric source values, such as -999. Blank and NaN values "
                "are already counted as null. Invalid value(s): "
                + ", ".join(invalid_numeric)
            )
    return special_values


def _special_value_masks(series: pd.Series, special_values: list[Any] | None) -> tuple[pd.Series, dict[str, int]]:
    """Return an exact, type-tolerant sentinel mask and per-code row counts.

    Dictionary codes arrive as strings from CSV/Excel even when the observed
    column is numeric.  Match their stripped text form and, only when the
    complete code is numeric, their numeric form.  Descriptive declarations
    such as ``"-999 = missing"`` deliberately do not get guessed: Review must
    confirm atomic values so the retained evidence remains auditable.
    """
    mask = pd.Series(False, index=series.index, dtype=bool)
    counts: dict[str, int] = {}
    special_values = _special_value_list(special_values)
    if not special_values:
        return mask, counts
    text = series.astype("string").str.strip()
    numeric = pd.to_numeric(series, errors="coerce")
    numeric_series = pd.api.types.is_numeric_dtype(series)
    for raw in special_values:
        label = str(raw).strip()
        matched = text.eq(label).fillna(False)
        try:
            numeric_code = float(label) if numeric_series else None
        except (TypeError, ValueError):
            numeric_code = None
        if numeric_code is not None and np.isfinite(numeric_code):
            label = str(int(numeric_code)) if numeric_code.is_integer() else str(numeric_code)
            matched |= numeric.eq(numeric_code).fillna(False)
        if label in counts:
            continue
        matched &= series.notna()
        counts[label] = int(matched.sum())
        mask |= matched
    return mask, counts


def _regular_values(series: pd.Series, special_values: list[Any] | None,
                    special_values_confirmed: bool) -> pd.Series:
    special_values = _special_value_list(special_values)
    if not special_values_confirmed or not special_values:
        return series.dropna()
    special_mask, _ = _special_value_masks(series, special_values)
    return series.loc[~series.isna() & ~special_mask]


def _column_profile(series: pd.Series, special_values: list[Any] | None = None,
                    special_values_confirmed: bool = False,
                    logical_type: str | None = None) -> dict:
    """Profile snapshot (ING-08): dtype, cardinality, null share, value
    patterns — generic signals only, never a column name. ``cardinality``/
    ``null_share``/``patterns`` are the contract's §6 field names; the
    older ``unique_count``/``null_count``/``non_null_count``/``min``/``max``/
    ``mean``/``top_values`` keys stay alongside them for the existing
    frontend summary rendering (additive, not a breaking rename)."""
    declared_specials = _special_value_list(special_values)
    physical_null_mask = series.isna()
    special_mask, special_counts = _special_value_masks(series, declared_specials)
    apply_specials = bool(special_values_confirmed and declared_specials)
    excluded_specials = special_mask if apply_specials else pd.Series(False, index=series.index, dtype=bool)
    regular_mask = ~physical_null_mask & ~excluded_specials
    clean = series.loc[regular_mask]
    total = int(len(series))
    null_count = int(physical_null_mask.sum())
    special_row_count = int(special_mask.sum()) if apply_specials else 0
    regular_count = int(regular_mask.sum())
    raw_non_null = int(series.notna().sum())
    profile = {
        "dtype": str(series.dtype),
        "calculation_method": "exact",
        "profile_basis": "confirmed_regular_values" if apply_specials else (
            "provisional_all_non_null" if declared_specials else "all_non_null_no_specials_declared"),
        "special_values_confirmed": bool(special_values_confirmed),
        "declared_special_values": declared_specials,
        "normalized_special_values": list(special_counts),
        "total_count": total,
        "non_null_count": raw_non_null,
        "null_count": null_count,
        "physical_null_count": null_count,
        "physical_null_share": round(null_count / total, 4) if total else None,
        "special_value_row_count": special_row_count,
        "special_value_share": round(special_row_count / total, 4) if total else None,
        "special_value_counts": special_counts if apply_specials else {},
        "proposed_special_value_row_count": int(special_mask.sum()),
        "proposed_special_value_counts": special_counts,
        "unmatched_declared_special_values": [code for code, count in special_counts.items() if count == 0],
        "unmatched_special_values": ([code for code, count in special_counts.items() if count == 0]
                                     if apply_specials else []),
        "regular_value_count": regular_count,
        "regular_value_share": round(regular_count / total, 4) if total else None,
        "effective_missing_count": null_count + special_row_count,
        "effective_missing_share": round((null_count + special_row_count) / total, 4) if total else None,
        "raw_distinct_count": int(series.nunique(dropna=True)),
        "unique_count": int(clean.nunique(dropna=True)),
        "cardinality": int(clean.nunique(dropna=True)),
        "null_share": round(null_count / total, 4) if total else None,
    }
    if clean.empty:
        profile.update({"patterns": {}, "stddev": None, "variance": None,
                        "percentiles": {}, "histogram": [],
                        "distinct_set_hash": None, "top_k": None})
        return profile
    # Binary describes cardinality, not storage.  A two-level string feature
    # (for example "fixed"/"variable") must retain categorical frequencies;
    # native numeric binary columns still enter this branch through dtype.
    numeric_profile = (pd.api.types.is_numeric_dtype(series) or
                       logical_type in {"numerical", "ordinal"})
    datetime_profile = (pd.api.types.is_datetime64_any_dtype(series) or
                        logical_type == "datetime")
    quarter_values: list[tuple[int, int]] = []
    # Avoid the former Python regex pass over every numeric cell.  Quarter
    # recognition is relevant only to string-like columns.
    if not numeric_profile and not pd.api.types.is_datetime64_any_dtype(series):
        for value in clean:
            match = re.fullmatch(r"\s*(\d{4})\D*[Qq]([1-4])\s*", str(value))
            if not match:
                quarter_values = []
                break
            quarter_values.append((int(match.group(1)), int(match.group(2))))
    if quarter_values:
        first_year, first_quarter = min(quarter_values)
        last_year, last_quarter = max(quarter_values)
        last_month = last_quarter * 3
        last_day = pd.Timestamp(last_year, last_month, 1).days_in_month
        profile.update({
            "min": f"{first_year:04d}Q{first_quarter}",
            "max": f"{last_year:04d}Q{last_quarter}",
            "period_bounds": {
                "start_date": f"{first_year:04d}-{(first_quarter - 1) * 3 + 1:02d}-01",
                "end_date": f"{last_year:04d}-{last_month:02d}-{last_day:02d}",
                "format": "calendar_quarter",
            },
        })
        profile["top_values"] = {
            str(k): int(v)
            for k, v in clean.value_counts(dropna=True).head(PROFILE_TOP_K_LIMIT).items()
        }
        profile["patterns"] = {"top_values": profile["top_values"], "min": profile["min"],
                               "max": profile["max"], "period_bounds": profile["period_bounds"]}
    elif numeric_profile:
        parsed_numeric = pd.to_numeric(clean, errors="coerce")
        vals = parsed_numeric.dropna()
        numeric_array = vals.to_numpy(dtype=float, copy=False)
        finite = numeric_array[np.isfinite(numeric_array)]
        non_finite_count = int(numeric_array.size - finite.size)
        parse_failure_count = int(parsed_numeric.isna().sum())
        histogram = []
        percentile_values: dict[str, Any] = {}
        if finite.size:
            counts, edges = np.histogram(finite, bins=FINGERPRINT_HISTOGRAM_BINS)
            histogram = [{"start": _jsonable(edges[i]), "end": _jsonable(edges[i + 1]),
                          "count": int(counts[i])} for i in range(len(counts))]
            quantiles = np.quantile(finite, _CORE_PERCENTILES)
            percentile_values = {
                f"p{int(percentile * 100):02d}": _jsonable(value)
                for percentile, value in zip(_CORE_PERCENTILES, quantiles)
            }
            mean = float(np.mean(finite))
            centered = finite - mean
            variance = float(np.mean(centered ** 2))
            stddev = float(np.sqrt(variance))
            median = float(quantiles[3])
            mad = float(np.median(np.abs(finite - median)))
            m3 = float(np.mean(centered ** 3)) if finite.size >= 3 else None
            m4 = float(np.mean(centered ** 4)) if finite.size >= 4 else None
            skewness = (m3 / (variance ** 1.5) if m3 is not None and variance > 0 else None)
            excess_kurtosis = (m4 / (variance ** 2) - 3.0
                               if m4 is not None and variance > 0 else None)
        else:
            mean = variance = stddev = median = mad = skewness = excess_kurtosis = None
        profile.update({
            "numeric_value_count": int(numeric_array.size),
            "numeric_parse_failure_count": parse_failure_count,
            "numeric_parse_failure_share": round(parse_failure_count / regular_count, 4) if regular_count else None,
            "finite_value_count": int(finite.size),
            "non_finite_count": non_finite_count,
            "non_finite_share": round(non_finite_count / regular_count, 4) if regular_count else None,
            "zero_count": int(np.count_nonzero(finite == 0)),
            "zero_share": round(float(np.mean(finite == 0)), 4) if finite.size else None,
            "negative_count": int(np.count_nonzero(finite < 0)),
            "negative_share": round(float(np.mean(finite < 0)), 4) if finite.size else None,
            "min": _jsonable(np.min(finite)) if finite.size else None,
            "max": _jsonable(np.max(finite)) if finite.size else None,
            "mean": _jsonable(mean),
            "variance": _jsonable(variance),
            "stddev": _jsonable(stddev),
            "median": _jsonable(median),
            "percentiles": percentile_values,
            "q1": percentile_values.get("p25"),
            "q3": percentile_values.get("p75"),
            "iqr": (_jsonable(float(quantiles[4] - quantiles[2])) if finite.size else None),
            "mad": _jsonable(mad),
            "skewness": _jsonable(skewness),
            "excess_kurtosis": _jsonable(excess_kurtosis),
            "histogram": histogram,
        })
        profile["patterns"] = {key: profile.get(key) for key in (
            "min", "max", "mean", "variance", "stddev", "median", "q1", "q3", "iqr", "mad",
            "skewness", "excess_kurtosis", "zero_count", "negative_count", "non_finite_count",
        )}
        profile["patterns"].update({"percentiles": percentile_values, "histogram": histogram})
    elif datetime_profile or ing_classify.looks_like_datetime(clean):
        parsed_dates = pd.to_datetime(clean, errors="coerce")
        vals = parsed_dates.dropna()
        parse_failure_count = int(parsed_dates.isna().sum())
        profile.update({
            "min": vals.min().isoformat() if not vals.empty else None,
            "max": vals.max().isoformat() if not vals.empty else None,
            "date_parse_failure_count": parse_failure_count,
            "date_parse_failure_share": round(parse_failure_count / regular_count, 4) if regular_count else None,
        })
        profile["patterns"] = {"min": profile["min"], "max": profile["max"],
                               "date_parse_failure_count": parse_failure_count}
    else:
        value_counts = clean.value_counts(dropna=True)
        profile["top_values"] = {str(k): int(v) for k, v in value_counts.head(PROFILE_TOP_K_LIMIT).items()}
        profile["mode"] = _jsonable(value_counts.index[0]) if len(value_counts) else None
        profile["mode_count"] = int(value_counts.iloc[0]) if len(value_counts) else 0
        profile["mode_share"] = round(profile["mode_count"] / regular_count, 4) if regular_count else None
        profile["distinct_value_ratio"] = round(len(value_counts) / regular_count, 4) if regular_count else None
        profile["duplicate_value_count"] = regular_count - len(value_counts)
        profile["duplicate_value_share"] = round(
            profile["duplicate_value_count"] / regular_count, 4) if regular_count else None
        profile["patterns"] = {"top_values": profile["top_values"]}
    distinct = sorted((_jsonable(value) for value in clean.unique()), key=lambda value: str(value))
    profile["distinct_set_hash"] = (
        hashlib.sha256(json.dumps(distinct, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if len(distinct) <= FINGERPRINT_DISTINCT_SET_MAX else None
    )
    profile["top_k"] = profile.get("top_values")
    return profile


def _role_for(column: str, classification: str, target: str | None,
              series: pd.Series | None = None) -> str:
    unique_text = False
    if series is not None and not pd.api.types.is_numeric_dtype(series):
        non_null = int(series.notna().sum())
        unique_text = non_null >= 20 and int(series.nunique(dropna=True)) == non_null
    return infer_inventory_role(
        column, classification, target, unique_text=unique_text,
    )


def _load_schema(item_id: str) -> dict:
    path = _file_for_role(item_id, "schema")
    if not path or not path.exists() or path.suffix.lower() != ".json":
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _schema_table(schema: dict, table: str) -> dict:
    tables_spec = schema.get("tables") if isinstance(schema, dict) else {}
    if not isinstance(tables_spec, dict):
        return {}
    return tables_spec.get(table) or tables_spec.get(_norm_name(table)) or {}


def _schema_expected_types(schema: dict, table: str, frame: pd.DataFrame) -> dict:
    spec = _schema_table(schema, table)
    expected = spec.get("expected_types") or spec.get("types") or {}
    if isinstance(expected, list):
        expected = {str(x.get("column")): x.get("type") for x in expected if isinstance(x, dict)}
    if not expected and schema:
        expected = {str(c): str(frame[c].dtype) for c in frame.columns}
    return {str(k): str(v) for k, v in (expected or {}).items() if k}


def _schema_fk_map(schema: dict, table: str) -> dict:
    spec = _schema_table(schema, table)
    raw = spec.get("foreign_keys") or spec.get("fk") or {}
    out = {}
    if isinstance(raw, dict):
        for col, ref in raw.items():
            if isinstance(ref, str) and "." in ref:
                ref_table, ref_col = ref.split(".", 1)
                out[str(col)] = {"ref_table": ref_table, "ref_column": ref_col}
            elif isinstance(ref, dict):
                out[str(col)] = {"ref_table": ref.get("ref_table") or ref.get("table"),
                                 "ref_column": ref.get("ref_column") or ref.get("column")}
    elif isinstance(raw, list):
        for rec in raw:
            if isinstance(rec, dict):
                out[str(rec.get("column"))] = {
                    "ref_table": rec.get("ref_table") or rec.get("table"),
                    "ref_column": rec.get("ref_column") or rec.get("column_ref") or rec.get("ref"),
                }
    return {k: v for k, v in out.items() if v.get("ref_table") and v.get("ref_column")}


def _schema_declared(schema: dict, table: str, frame: pd.DataFrame, expected_types: dict) -> dict:
    spec = _schema_table(schema, table)
    cols = spec.get("columns") or list(expected_types) or (list(frame.columns) if schema else [])
    return {"columns": [str(c) for c in cols], "primary_key": spec.get("primary_key") or spec.get("key") or []}


def create_item(kind: str, name: str, time_basis: str = "none") -> dict:
    """0.5.0 Step 3b (AST-01/02/03/22) — creates a NEW asset
    (``assets.service.create_asset``) plus its first snapshot
    (``assets.service.add_snapshot``, ``intent='fresh'``), and returns
    ``{"item_id": <first snapshot's item_id>, ...}`` so every existing
    caller (``routers/v2.py``, ``tests/test_rca.py``,
    ``tests/test_testlab_diagnostics.py``) keeps working unchanged.

    ``name`` is the pre-0.5.0 free-text dataset name and is SANITISED into a
    valid AST-02 alias (``assets.identity.sanitize_alias_for_migration`` —
    the same lenient, never-raising rule ``system_db._backfill_asset_model``
    uses for historical data), never strictly validated here: strict
    inline-as-typed validation (``assets.identity.validate_alias``) belongs
    to Step 4's real "type an alias" UI entry point, which calls
    ``assets.service.create_asset`` directly. This is also why the OLD
    global case-insensitive name-uniqueness rejection is GONE, not
    narrowed: two calls with the SAME ``name`` now both succeed, each with
    its own distinct ``system_id`` (AST-03) — the ``requires_reupload``
    resume branch that used to live here is not recreated; reaching such an
    asset again is SRC-13's job (a future step), resolved by re-uploading
    against it via ``reupload_item``/``add_snapshot``, never by calling
    this function again with a matching name.

    ``time_basis`` defaults to 'none' (AST-22) because this two-argument
    entry point predates the concept entirely — Step 4's Fresh-Upload UI is
    where a real caller asks the question once and passes the answer here.
    """
    if kind not in {"database", "dataset"}:
        raise ValueError("kind must be database or dataset")
    if not (name or "").strip():
        raise ValueError("A name is required.")
    from assets import service as assets_service

    alias = asset_identity.sanitize_alias_for_migration(name)
    asset = assets_service.create_asset(kind, alias, time_basis, actor=None)
    snapshot = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None,
                                           _staged=(time_basis == TIME_BASIS_PERIOD))
    _item_dir(snapshot["item_id"])
    return {"item_id": snapshot["item_id"], "asset_id": asset["asset_id"],
            "system_id": asset["system_id"], "display_name": asset["display_name"]}


def reupload_item(existing_item_id: str, as_of_date: str | None = None,
                  intent: str = "add_period", snapshot_label: str | None = None,
                  end_date: str | None = None) -> dict:
    """C-40/D-25/AST-05/06/08/10 (0.5.0 Step 3b) — FNC-01's single most
    affected function. 0.4.0 called this "a new delivery": a SIBLING
    ``dq_items`` row with its own ``item_id`` and its own name, joined to
    the same family — which is precisely the defect ``Data_Sourcing_3``
    reports (dependent sections kept showing the OLD dataset's name and
    figures because the "new" delivery was, in every way that mattered, a
    different dataset). 0.5.0 corrects it: this inserts ONE new ``dq_items``
    row that is a SNAPSHOT of the SAME asset
    (``assets.service.add_snapshot``) — the asset's identity never forks.

    ``intent`` defaults to ``'add_period'`` — 0.4.0 never had a "full
    replacement" concept, so every existing caller of this function keeps
    its exact old observable behaviour (no version bump, the prior
    snapshot's own row untouched) unless it explicitly opts into
    ``'full_replacement'``.
    """
    existing = require_item(existing_item_id)
    family_id = existing.get("dataset_family_id") or existing_item_id
    from assets import service as assets_service

    result = assets_service.add_snapshot(
        family_id, intent=intent, actor=None,
        start_date=as_of_date, end_date=end_date, snapshot_label=snapshot_label,
    )
    _item_dir(result["item_id"])
    return result


def _support_file_summary(path: Path, options: dict[str, Any] | None = None) -> list[dict]:
    """Per-tab summary for dictionary/schema files (excel or json)."""
    suffix = path.suffix.lower()
    options = options or {}
    try:
        if suffix in {".xlsx", ".xls"}:
            sheets = pd.read_excel(path, sheet_name=None)
            return [{"tab": str(k), "rows": int(len(v)), "columns": int(len(v.columns))} for k, v in sheets.items()]
        if suffix in {".csv", ".tsv", ".txt"}:
            delimiter = _delimiter_value(options.get("delimiter"))
            if suffix == ".tsv" and not delimiter:
                delimiter = "\t"
            frame = pd.read_csv(path, sep=delimiter or None, engine="python")
            return [{"tab": path.stem, "rows": int(len(frame)), "columns": int(len(frame.columns))}]
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                tables_spec = data.get("tables")
                if isinstance(tables_spec, dict):
                    return [{"tab": str(t), "rows": None,
                             "columns": len((spec or {}).get("expected_types") or (spec or {}).get("columns") or [])}
                            for t, spec in tables_spec.items()]
                return [{"tab": path.stem, "rows": len(data), "columns": None}]
            if isinstance(data, list):
                return [{"tab": path.stem, "rows": len(data), "columns": None}]
    except Exception:
        return []
    return []


def save_file(item_id: str, role: str, filename: str, payload: bytes) -> dict:
    """ING-01: dropping the dataset file is the whole "start" — no separate
    finalize step exists here or anywhere downstream. A structurally corrupt
    or empty data file (ING-04 hard-fail) is rejected right here, at upload
    time, with a clear message, rather than silently accepted and only
    failing later during profiling.

    Parsing/profiling itself (ING-03/04/05/07) is NOT run inside this call —
    it stays the caller-driven ``profile_item``/the SSE ``/profile/stream``
    endpoint, exactly as before. What changes is that the frontend no longer
    waits for a manual "Finalize & Profile" click before making that call:
    it fires automatically the moment this upload response comes back
    (ui/src/pages/DataSourcing.jsx). Dropping a dictionary after the dataset
    is already profiled re-triggers the same stream so the new mapping is
    inspected immediately (ING-01's "dropping a dictionary triggers
    automatic inspection").
    """
    item = require_item(item_id)
    if role not in {"data", "dictionary", "schema"}:
        raise ValueError("role must be data, dictionary, or schema")
    filename = Path(filename).name
    path = _item_dir(item_id) / filename
    path.write_bytes(payload)
    summary: list[dict] = []
    if role == "data":
        try:
            options = _source_options(item_id)
            data_tables = _read_tabular(path, options)
            # UPL-12/P-09: read literal headers now so pandas cannot be the
            # only source of truth. Duplicate/blank headers are checked in
            # profile_item (STEP 3), after the staged file is visible to the
            # user; only unreadable/empty/headerless files fail this call.
            raw_headers = ing_headers.read_raw_headers(
                path, delimiter=_delimiter_value(options.get("delimiter")),
                sheet_name=_sheet_value(options.get("data_sheet")),
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as a clear structural hard-fail
            ing_records.set_status(item_id, "failed", f"The data file could not be read: {exc}")
            raise IngestCorruptionError(f"The data file could not be read: {exc}") from exc
        if item["kind"] == "dataset" and len(data_tables) > 1:
            first = next(iter(data_tables))
            data_tables = {first: data_tables[first]}
            raw_headers = {first: raw_headers.get(first, [])}
        if not data_tables or not raw_headers or any(frame.shape[1] == 0 for frame in data_tables.values()):
            reason = "The data file has no readable columns — it may be corrupt or empty."
            ing_records.set_status(item_id, "failed", reason)
            raise IngestCorruptionError(reason)
        if any(frame.shape[0] == 0 for frame in data_tables.values()):
            reason = "The data file is empty; it must contain a header row and at least one data row."
            ing_records.set_status(item_id, "failed", reason)
            raise IngestCorruptionError(reason)
        # A row made solely of numeric cells is the common headerless-file
        # shape (pandas otherwise promotes those values into column labels).
        if any(headers and all(_looks_numeric_header(value) for value in headers)
               for headers in raw_headers.values()):
            reason = "The data file has no readable header row."
            ing_records.set_status(item_id, "failed", reason)
            raise IngestCorruptionError(reason)
        for table, frame in data_tables.items():
            _write_table(item_id, table, frame)
            summary.append({"tab": table, "rows": int(len(frame)), "columns": int(len(frame.columns))})
    else:
        summary = _support_file_summary(path, _source_options(item_id))
    db.upsert("dq_item_files", {
        "file_id": _id("file"), "item_id": item_id, "role": role,
        "filename": filename, "path": str(path), "completed_at": db.now_ist(),
    })
    if role == "data" or (role == "dictionary" and tables(item_id)):
        ing_records.set_status(item_id, "profiling")
    if role == "data":
        _usage_event("upload_started", item_id, detail={"filename": filename})
        _usage_event("upload_step_reached", item_id, detail={"step": 2})
    return {"item_id": item_id, "role": role, "filename": filename,
            "summary": summary, "completed_at": db.now_ist()}


def inspect_source_upload(filename: str, payload: bytes, role: str = "data") -> dict[str, Any]:
    """Inspect a selected source without creating an asset or retaining the file."""
    if role not in {"data", "dictionary"}:
        raise ValueError("role must be data or dictionary")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".tsv", ".txt", ".xlsx", ".xls"}:
        raise ValueError("Unsupported inspection type. Use CSV, TSV, text, or Excel.")
    stream = io.BytesIO(payload)
    if suffix in {".xlsx", ".xls"}:
        result = inspect_excel(stream, role=role, workbook_name=Path(filename).name)
        dictionary_sheet = result.get("dictionary_sheet")
        if dictionary_sheet:
            stream.seek(0)
            frame = pd.read_excel(stream, sheet_name=dictionary_sheet)
            result["dictionary_inspection"] = inspect_dictionary(frame)
        return result
    result = inspect_delimited(
        stream, role=role, delimiter="\t" if suffix == ".tsv" else None,
        workbook_name=Path(filename).name,
    )
    if role == "dictionary":
        stream.seek(0)
        frame = pd.read_csv(stream, sep="\t" if suffix == ".tsv" else None,
                            engine="python", keep_default_na=False)
        result["dictionary_inspection"] = inspect_dictionary(frame)
    return result


def save_source_bundle(item_id: str, data_filename: str, data_payload: bytes,
                       dictionary_filename: str | None = None,
                       dictionary_payload: bytes | None = None, *,
                       parsing_options: dict[str, Any] | None = None,
                       file_context: str | None = None,
                       dictionary_header_mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """Stage one reviewed sourcing submission using the existing file/table model."""
    require_item(item_id)
    options = {key: value for key, value in (parsing_options or {}).items()
               if key in {"delimiter", "data_sheet", "dictionary_sheet", "overview_sheet"}
               and value not in {None, ""}}
    header_mapping = {str(key): str(value) for key, value in
                      (dictionary_header_mapping or {}).items() if str(value).strip()}
    unknown = sorted(set(header_mapping) - set(FIELD_BY_NAME))
    if unknown:
        raise ValueError("Unknown canonical dictionary fields: " + ", ".join(unknown))
    duplicates = sorted({value for value in header_mapping.values()
                         if list(header_mapping.values()).count(value) > 1})
    if duplicates:
        raise ValueError("Each dictionary source header may be selected only once.")
    if header_mapping and "column_name" not in header_mapping:
        raise ValueError("Select the dictionary header that contains column names.")
    db.update("dq_items", {"item_id": item_id}, {
        "source_parsing_options_json": options,
        "file_context_json": {"text": str(file_context or "").strip(),
                              "overview_sheet": options.get("overview_sheet")},
        "dictionary_header_mapping_json": header_mapping,
        "dictionary_mapping_confirmed": int(bool(header_mapping)),
        "updated_at": db.now_ist(),
    })
    dictionary = None
    if dictionary_filename and dictionary_payload is not None:
        dictionary = save_file(item_id, "dictionary", dictionary_filename, dictionary_payload)
    data = save_file(item_id, "data", data_filename, data_payload)
    return {"item_id": item_id, "data": data, "dictionary": dictionary,
            "summary": data.get("summary") or [], "parsing_options": options,
            "file_context": str(file_context or "").strip(),
            "dictionary_header_mapping": header_mapping}


def _looks_numeric_header(value: Any) -> bool:
    try:
        float(str(value).strip())
        return True
    except (TypeError, ValueError):
        return False


def require_item(item_id: str) -> dict:
    item = db.query_one("dq_items", item_id=item_id)
    if not item:
        raise KeyError(f"Unknown item: {item_id}")
    return item


def abandon_upload(item_id: str, step: int, actor: str = "system") -> dict:
    """Capture an abandoned staged flow without deleting its staged work."""
    item = require_item(item_id)
    _usage_event("upload_abandoned", item_id, actor=actor,
                 detail={"step": int(step), "staged": True})
    return {"item_id": item_id, "abandoned": True, "staged": True}


def discard_staged_upload(item_id: str, actor: str = "system") -> dict:
    """Permanently remove one explicitly staged, uncommitted sourcing draft.

    Completed snapshots are never eligible. If the draft is the asset's only
    snapshot, its empty asset/version shell is removed as well; otherwise the
    existing asset and all completed snapshots remain untouched.
    """
    item = require_item(item_id)
    draft_owner = item.get("sourcing_owner")
    draft_tenant_id = item.get("sourcing_tenant_id")
    processed = db.query_one("dq_asset_events", snapshot_id=item_id,
                             event_type="snapshot_processed")
    if processed or not str(item.get("snapshot_label") or "").startswith("__staged_"):
        raise ValueError("Only an uncommitted staged sourcing draft can be discarded.")

    asset_id = item.get("dataset_family_id")
    _usage_event("upload_abandoned", item_id, actor=actor,
                 detail={"step": "start_fresh", "staged": True, "discarded": True})

    dictionary_version_id = item.get("dictionary_version_id")
    if dictionary_version_id:
        db.delete("dq_asset_dictionaries", dictionary_version_id=dictionary_version_id)
    for table, key in (
        ("dq_snapshot_fingerprints", "snapshot_id"),
        ("dq_item_warnings", "item_id"),
        ("dq_item_mappings", "item_id"),
        ("variable_inventory", "item_id"),
        ("dq_item_tables", "item_id"),
        ("dq_item_files", "item_id"),
    ):
        db.delete(table, **{key: item_id})
    db.delete("dq_asset_events", snapshot_id=item_id)
    db.delete("dq_items", item_id=item_id)

    if draft_owner and draft_tenant_id:
        recovery = db.execute(
            "SELECT item_id FROM dq_items WHERE sourcing_tenant_id=? "
            "AND sourcing_owner=? AND sourcing_draft_state='recovery' "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            (draft_tenant_id, draft_owner),
        )
        if recovery:
            db.update("dq_items", {"item_id": recovery[0]["item_id"]}, {
                "sourcing_draft_state": "active", "updated_at": db.now_ist(),
            })

    removed_asset = False
    remaining_snapshot = db.query_one("dq_items", dataset_family_id=asset_id) if asset_id else None
    if asset_id and not remaining_snapshot:
        db.delete("dq_asset_dictionaries", asset_id=asset_id)
        db.delete("dq_asset_events", asset_id=asset_id)
        db.delete("dq_asset_versions", asset_id=asset_id)
        db.delete("dq_assets", asset_id=asset_id)
        removed_asset = True
    elif asset_id and dictionary_version_id:
        remaining_dictionaries = db.query(
            "dq_asset_dictionaries", asset_id=asset_id, order_by="created_at DESC"
        )
        db.update("dq_assets", {"asset_id": asset_id}, {
            "current_dictionary_version_id": (
                remaining_dictionaries[0]["dictionary_version_id"]
                if remaining_dictionaries else None
            ),
            "updated_at": db.now_ist(),
        })

    for root in (UPLOAD_ROOT, ITEM_DB_ROOT):
        candidate = (root / item_id).resolve()
        if candidate.parent == root.resolve() and candidate.exists():
            shutil.rmtree(candidate)
    return {"item_id": item_id, "discarded": True,
            "asset_id": asset_id, "asset_removed": removed_asset}


def list_items(kind: str | None = None) -> list[dict]:
    rows = db.query("dq_items", order_by="created_at DESC", **({"kind": kind} if kind else {}))
    for row in rows:
        row["id"] = row["item_id"]
        row["timestamps"] = {"created_at": row.get("created_at"), "updated_at": row.get("updated_at")}
        # Inventory columns (feedback 1.1/1.7): health score + open-issue count
        # from the workflows, and the target's business definition.
        score_row = db.query_one("scores_v2", item_id=row["item_id"], scope="all")
        row["health_score"] = score_row.get("final") if score_row else None
        counted = db.execute(
            "SELECT COUNT(*) AS n FROM issues_v2 WHERE item_id=? AND status IN ('Open','In RCA','Escalated')",
            [row["item_id"]])
        row["active_issues"] = counted[0]["n"] if counted else 0
        row["target_description"] = ""
        if row.get("target_variable"):
            found = db.execute(
                "SELECT description FROM variable_inventory WHERE item_id=? AND column_name=? LIMIT 1",
                [row["item_id"], row["target_variable"]])
            row["target_description"] = (found[0].get("description") or "") if found else ""
    return rows


def tables(item_id: str) -> list[dict]:
    return db.query("dq_item_tables", item_id=item_id, order_by="table_name")


def columns(item_id: str, table: str | None = None) -> list[str]:
    rows = tables(item_id)
    if table:
        rows = [r for r in rows if r["table_name"] == table]
    if not rows:
        return []
    return rows[0].get("columns") or []


def finalize_item(item_id: str, target_variable: str | None = None, use_case: str | None = None) -> dict:
    """Set the (optional) target variable / use case for a dataset item.

    ING-01/ING-02: target and use case are asked once, inline, and
    DEFAULTED — they are never a gate. Dropping the dataset file already
    started parsing and profiling on its own (see ``save_file``); this only
    records the two optional fields whenever the caller supplies them. If
    the item is already profiled and the target actually changed, profiling
    re-runs so the newly-chosen column immediately reclassifies as
    'target' — the dataset and dictionary are unchanged, so this is cheap
    and idempotent, not a second decision gate.

    0.5.0 Step 3b (AST-13) — target/use case are ASSET-level properties, not
    per-snapshot ones, so ``dq_assets`` (keyed by ``item_id``'s
    ``dataset_family_id``) is now the canonical write target. The value is
    ALSO mirrored onto this snapshot's own ``dq_items`` row, unconditionally,
    exactly as before — every existing 0.4.0 reader of
    ``item.get('target_variable')``/``item.get('use_case')``
    (``profile_item``, ``_supporting``, ``dq_diagnostics.readiness``,
    ``dq_diagnostics.manifest``) keeps reading a value that is correct for
    THIS snapshot without being rewritten itself (P-05's denormalised-mirror
    discipline, applied here the same way it already applies to
    ``dq_items.name``). The intent-conditional blank-on-fresh/
    full-replacement, carry-forward-on-add-period logic (CTX-04/CTX-06) is
    Step 6's job — this step only relocates the write target and keeps
    behaviour otherwise identical.
    """
    item = require_item(item_id)
    if not tables(item_id):
        raise ValueError("Upload at least one data file before setting the target/use case.")
    intent = item.get("intent")
    changes = {"updated_at": db.now_ist()}
    reclassify = False
    if item["kind"] == "dataset":
        if intent in {"fresh", "full_replacement"}:
            # CTX-04: these intents deliberately clear the asset-level
            # decision; neither a stale caller value nor a frontend default
            # may carry it into the new reference schema.
            changes.update({"target_variable": None, "use_case": None})
            reclassify = bool(item.get("target_variable"))
        elif intent == "add_period":
            # CTX-06: the asset's existing decision is canonical and is left
            # untouched. The staged snapshot already mirrors it.
            changes.update({"target_variable": item.get("target_variable"),
                            "use_case": item.get("use_case")})
        else:
            if target_variable and target_variable != item.get("target_variable"):
                changes["target_variable"] = target_variable
                reclassify = True
            if use_case:
                changes["use_case"] = use_case
    db.update("dq_items", {"item_id": item_id}, changes)
    asset_changes = {k: v for k, v in changes.items() if k != "updated_at"}
    family_id = item.get("dataset_family_id")
    if asset_changes and family_id and db.query_one("dq_assets", asset_id=family_id):
        db.update("dq_assets", {"asset_id": family_id}, {**asset_changes, "updated_at": db.now_ist()})
    if use_case and family_id:
        _usage_event("use_case_set", item_id, detail={"use_case": use_case},
                     object_type="asset", object_id=family_id)
    if reclassify and get_inventory(item_id):
        profile_item(item_id)
    return require_item(item_id)


def _classify(name: str, series: pd.Series, target: str | None = None) -> str:
    """The dataset's OBSERVED classification, independent of any dictionary
    (``profile_item`` separately resolves the dictionary's declared type and
    compares the two — that comparison is how ``type_conflict`` warnings and
    the legacy per-column discrepancy note get generated). ING-10: generic
    signals only — see ``ingest/classify.py``."""
    inferred = ing_classify.classify(series, is_target=bool(target) and name == target)
    # Generic value parsing is useful when CSV has supplied every cell as a
    # string: a majority numeric/date signal still infers a type, while the
    # residual failures are surfaced by UPL-11 rather than silently treated
    # as categorical text. No column-name literal participates here.
    clean = series.dropna()
    if len(clean) >= 2 and not pd.api.types.is_numeric_dtype(series):
        numeric_rate = float(pd.to_numeric(clean, errors="coerce").notna().mean())
        if numeric_rate >= 0.5:
            return "numerical"
        date_rate = float(pd.to_datetime(clean, errors="coerce").notna().mean())
        if date_rate >= 0.5:
            return "datetime"
    return inferred


def profile_item(item_id: str, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> list[dict]:
    """Drop -> profile, immediately (ING-01): parses every uploaded table,
    computes the confidence-tiered dictionary mapping (ING-03), the
    structured non-blocking warnings (ING-04), the item's dictionary state
    (ING-05) and the derived status (ING-07), and persists all of it
    (ING-08). No separate finalize/profile gate — this is called
    automatically by ``save_file`` the moment a data or dictionary file
    lands, and it is safe to call again any time (e.g. from
    ``finalize_item`` after the target changes, or directly by callers that
    manage their own tables — see ``backend/tests/test_rca.py``).

    Hard-fails ONLY on structural corruption (ING-04): a duplicate
    dictionary declaration (raised by ``_parse_dictionary``) or a table with
    no readable columns. Every table is validated BEFORE anything is
    written, so a hard-fail never leaves partial inventory/mapping state
    behind — the item's ingest_status becomes 'failed' with a reason and
    nothing else changes.
    """
    def report(stage: str, percent: int, completed: int, message: str) -> None:
        if progress_callback:
            progress_callback({"stage": stage, "percent": percent, "completed": completed,
                               "total": 9, "message": message})

    item = require_item(item_id)
    _usage_event("upload_step_reached", item_id, detail={"step": 3})
    has_dict_file = _file_for_role(item_id, "dictionary") is not None
    table_rows = tables(item_id)
    report("normalize_dictionary", 50, 4,
           "Interpreting the selected dictionary headers and metadata.")
    try:
        previous_dictionary_version_id = item.get("dictionary_version_id")
        dictionary = _parse_dictionary(item_id)
        dictionary_version_id = (_bind_dictionary_version(item_id, dictionary)
                                 if has_dict_file else None)
        dictionary_changed = bool(dictionary_version_id and
                                  dictionary_version_id != previous_dictionary_version_id)
        report("profile_data", 61, 5,
               "Dictionary metadata normalized; profiling observed columns and values.")
        prepared = []
        data_path = _file_for_role(item_id, "data")
        if data_path and data_path.exists():
            # UPL-12: pandas would expose id/id.1 and Unnamed: 0 here. The
            # raw-header validator sees the literal per-table names instead.
            options = _source_options(item_id)
            ing_headers.validate_raw_headers(ing_headers.read_raw_headers(
                data_path, delimiter=_delimiter_value(options.get("delimiter")),
                sheet_name=_sheet_value(options.get("data_sheet")),
            ))
        for t in table_rows:
            frame = _read_table(item_id, t["table_name"])
            if frame.shape[1] == 0:
                raise IngestCorruptionError(
                    f"Table '{t['table_name']}' has no readable columns — "
                    "the data file may be corrupt or empty.")
            dict_rows = _dictionary_rows_for_table(dictionary, t["table_name"])
            mapping_records = ing_mapping.compute_mapping(dict_rows, [str(c) for c in frame.columns])
            prepared.append((t["table_name"], frame, mapping_records))
    except IngestCorruptionError as exc:
        ing_records.set_status(item_id, "failed", str(exc))
        return get_inventory(item_id)

    out = []
    columns_total = 0
    declared_covered_total = 0
    for table_index, (table_name, frame, mapping_records) in enumerate(prepared):
        mapping_by_col = {m["source_column"]: m for m in mapping_records if m.get("source_column")}
        prior_inventory = {row["column_name"]: row for row in db.execute(
            "SELECT * FROM variable_inventory WHERE item_id=? AND table_name=?",
            [item_id, table_name],
        )}
        dtype_by_column = {str(c): str(frame[c].dtype) for c in frame.columns}
        table_warnings = ing_warnings.mapping_warnings(mapping_records, [str(c) for c in frame.columns])
        for col in frame.columns:
            col = str(col)
            columns_total += 1
            observed = _classify(col, frame[col], item.get("target_variable"))
            mrec = mapping_by_col.get(col)
            entry = mrec if mrec and mrec.get("status") == "applied" else None
            declared_type_raw = (entry or {}).get("declared_type") or ""
            declared = _classification_from_declared(declared_type_raw)
            classification = declared or observed
            confident = bool(mrec and mrec.get("status") == "applied" and declared)
            if confident:
                declared_covered_total += 1
            discrepancies = []
            if declared and declared != observed:
                discrepancies.append({
                    "field": "type",
                    "dictionary": (entry or {}).get("declared_type") or declared,
                    "observed": observed,
                    "resolution": "dictionary",
                })
                conflict = ing_warnings.type_conflict_warning(col, declared, observed)
                if conflict:
                    table_warnings.append(conflict)
            unsupported = ing_warnings.unsupported_value_warning(col, declared_type_raw, declared)
            if unsupported:
                table_warnings.append(unsupported)
            for warning in (
                ing_warnings.column_all_null_warning(col, frame[col]),
                ing_warnings.column_mixed_type_warning(col, frame[col]),
                ing_warnings.column_parse_failure_warning(col, frame[col], observed),
            ):
                if warning:
                    table_warnings.append(warning)
            # Feedback 2.6: notes start blank for the user; a missing dictionary
            # entry already shows as "No definition" in the description column.
            dictionary_role = str((entry or {}).get("role") or "").strip().lower()
            inventory_role = INVENTORY_ROLE_BY_DICTIONARY.get(dictionary_role) or _role_for(
                col, classification, item.get("target_variable"), frame[col])
            missing_codes = _special_value_list((entry or {}).get("missing_value_codes"))
            prior = prior_inventory.get(col) or {}
            codes_confirmed = bool(
                not dictionary_changed
                and prior.get("missing_codes_confirmed")
                and _special_value_list(prior.get("missing_value_codes_json")) == missing_codes
            )
            row = {
                "item_id": item_id, "table_name": table_name, "column_name": col,
                "classification": classification, "data_type": str(frame[col].dtype),
                "description": (entry or {}).get("definition", ""), "discrepancies": discrepancies, "notes": "",
                "role": inventory_role, "dictionary_role": dictionary_role,
                "business_context": (entry or {}).get("business_context") or "",
                "missing_value_codes_json": missing_codes,
                "missing_codes_confirmed": int(codes_confirmed),
                "profile_json": {
                    **_column_profile(frame[col], missing_codes, codes_confirmed, classification),
                    "inferred_type": observed,
                    "sample_values": [_jsonable(value) for value in _regular_values(
                        frame[col], missing_codes, codes_confirmed).head(5).tolist()],
                },
                "provisional": 0 if confident else 1,
                "updated_at": db.now_ist(),
            }
            _write_snapshot_fingerprint(item_id, table_name, col, row["profile_json"], classification)
            db.upsert("variable_inventory", row)
            out.append(row)
        ing_records.replace_mapping(item_id, table_name, mapping_records, dtype_by_column)
        ing_records.replace_warnings(item_id, table_name, table_warnings)
        report("profile_data", min(82, 61 + round(21 * (table_index + 1) / max(1, len(prepared)))),
               5, f"Profiled {table_index + 1} of {len(prepared)} data tables.")

    dict_state = ing_dictionary_state.compute(
        has_dictionary=has_dict_file, total_columns=columns_total, declared_covered=declared_covered_total)
    ing_records.set_dictionary_state(item_id, dict_state)
    _persist_confirmed_type_map(item_id)
    report("validate_schema", 86, 6,
           "Data profile complete; validating the incoming schema against its reference.")
    schema_result = _record_schema_check(item_id)
    # ING-07/UPL-03: a real schema confirmation is an outstanding fact, so a
    # mismatched staged upload remains needs_review until Step 5 records it.
    new_ingest_status = ing_status.derive(
        has_data_file=bool(table_rows), profiling_complete=True, fail_reason=None,
        outstanding_confirmations=(1 if _step4_confirmation_outstanding(item_id) or
                                   not schema_result.get("is_match", True) or
                                   any(row.get("missing_value_codes_json") and
                                       not row.get("missing_codes_confirmed") for row in out) else 0),
    )
    ing_records.set_status(item_id, new_ingest_status)
    db.update("dq_items", {"item_id": item_id}, {
        "status": "profiled", "module_tag": "Test Lab", "updated_at": db.now_ist(),
    })
    report("finalize", 96, 7,
           "Validation complete; preparing the review inventory and sourcing summary.")
    if new_ingest_status == "ready":
        _usage_event("snapshot_ready", item_id, detail={"status": new_ingest_status})
    return out


_CLASS_LABELS = {
    "numerical": "Numeric measure", "categorical": "Categorical field", "binary": "Binary flag",
    "ordinal": "Ordinal grade", "datetime": "Date field", "identifier": "Identifier",
    "target": "Target variable", "text": "Free-text field", "derived": "Derived field",
}


def _fmt_value(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number) and abs(number) < 1e15:
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _profile_summary(row: dict) -> str:
    """A presentable one-line summary of the observed column profile."""
    profile = row.get("profile_json") or {}
    parts = [_CLASS_LABELS.get(row.get("classification"), "Column")]
    total = (profile.get("non_null_count") or 0) + (profile.get("null_count") or 0)
    if total:
        null_pct = 100 * (profile.get("null_count") or 0) / total
        if null_pct == 0:
            parts.append("fully populated")
        elif null_pct < 1:
            parts.append("under 1% missing")
        else:
            parts.append(f"{null_pct:.0f}% missing")
    if profile.get("unique_count") is not None:
        parts.append(f"{profile['unique_count']:,} distinct values")
    if profile.get("min") is not None and profile.get("max") is not None:
        if row.get("classification") == "datetime":
            parts.append(f"spanning {str(profile['min'])[:10]} to {str(profile['max'])[:10]}")
        else:
            parts.append(f"ranging {_fmt_value(profile['min'])} to {_fmt_value(profile['max'])}")
    elif profile.get("top_values"):
        top = next(iter(profile["top_values"]), "")
        parts.append(f"most common value '{top}'")
    return ", ".join(parts) + "."


def _persist_confirmed_type_map(item_id: str) -> dict:
    """Persist the post-override type map on the snapshot and fresh schema."""
    item = require_item(item_id)
    schema_tables: dict[str, dict] = {}
    for row in get_inventory(item_id):
        table_name = row["table_name"]
        table_schema = schema_tables.setdefault(table_name, {"columns": [], "types": {}})
        table_schema["columns"].append(row["column_name"])
        table_schema["types"][row["column_name"]] = row.get("classification") or "text"
    for table_schema in schema_tables.values():
        table_schema["column_count"] = len(table_schema["columns"])
    schema = {"tables": schema_tables}
    encoded = json.dumps(schema, sort_keys=True)
    db.update("dq_items", {"item_id": item_id}, {"column_type_map_json": encoded})
    family_id = item.get("dataset_family_id")
    if family_id and item.get("intent") == "fresh":
        db.update(
            "dq_asset_versions",
            {"asset_id": family_id, "version_no": item.get("version_no") or 1},
            {"reference_schema_json": encoded, "created_from_snapshot_id": item_id},
        )
    return schema


def _write_snapshot_fingerprint(snapshot_id: str, table_name: str, column_name: str,
                                profile: dict, confirmed_type: str) -> None:
    """Write one fingerprint row using the profile already in memory."""
    db.upsert("dq_snapshot_fingerprints", {
        "snapshot_id": snapshot_id, "table_name": table_name, "column_name": column_name,
        "dtype": profile.get("dtype"), "confirmed_type": confirmed_type,
        "null_rate": profile.get("null_share"), "distinct_count": profile.get("cardinality"),
        "min_value": None if profile.get("min") is None else str(profile.get("min")),
        "max_value": None if profile.get("max") is None else str(profile.get("max")),
        "mean_value": profile.get("mean"), "stddev_value": profile.get("stddev"),
        "histogram_json": json.dumps(profile.get("histogram") or [], sort_keys=True),
        "distinct_set_hash": profile.get("distinct_set_hash"),
        "top_k_json": json.dumps(profile.get("top_k") or {}, sort_keys=True),
        "computed_at": db.now_ist(),
    })


def _record_schema_check(item_id: str) -> dict:
    item = require_item(item_id)
    incoming = item.get("column_type_map_json") or {}
    if isinstance(incoming, str):
        try:
            incoming = json.loads(incoming)
        except (TypeError, ValueError):
            incoming = {}
    asset_id = item.get("dataset_family_id")
    asset = db.query_one("dq_assets", asset_id=asset_id) if asset_id else None
    reference = None
    if asset:
        version = db.query_one("dq_asset_versions", asset_id=asset_id,
                               version_no=asset.get("current_version_no"))
        reference = (version or {}).get("reference_schema_json")
    # Fresh has just established the reference schema; it is therefore a
    # match.  Existing assets compare against the current version.
    result = schema_check.compare(reference, incoming, asset.get("kind", item.get("kind", "dataset")) if asset else item.get("kind", "dataset"))
    if item.get("intent") == "fresh" or reference is None:
        result = {**result, "is_match": True}
    detail = {"schema": result, "incoming_schema": incoming,
              "intent": item.get("intent"), "messages": schema_check.messages(result)}
    db.insert("dq_asset_events", {
        "event_id": _id("evt"), "asset_id": asset_id, "version_no": item.get("version_no"),
        "snapshot_id": item_id, "event_type": "schema_checked", "actor": None,
        "at": db.now_ist(), "summary": "Schema conflict check completed.", "detail_json": detail,
    })
    return result


def _step4_confirmation_outstanding(item_id: str) -> bool:
    item = require_item(item_id)
    # Only the explicit staging marker represents a new UI upload.  Legacy
    # service.create_item callers retain their already-complete compatibility
    # path; the real Fresh/Existing upload target remains needs_review until
    # its Step 4 fields are committed.
    return str(item.get("snapshot_label") or "").startswith("__staged_")


def schema_check_for_snapshot(item_id: str) -> dict:
    events = db.query("dq_asset_events", snapshot_id=item_id, event_type="schema_checked", order_by="at")
    if events:
        detail = events[-1].get("detail_json") or {}
        return detail.get("schema") or {}
    return {"tables_added": [], "tables_removed": [], "per_table": {},
            "column_count_change": {"from": 0, "to": 0}, "is_match": True}


def _schema_consequence(intent: str) -> str:
    if intent == "full_replacement":
        return ("This will REPLACE the dataset's reference schema. All future uploads will be validated "
                "against the new schema, and existing test configurations referencing removed or retyped "
                "columns will break.")
    return ("This snapshot will not be comparable to existing snapshots on the differing columns. Tests "
            "such as PSI are column-wise and will skip or fail on them.")


def _overlap_warnings(asset_id: str, start_date: str | None, end_date: str | None,
                      exclude_snapshot_id: str | None = None) -> list[str]:
    from assets import reads
    asset = db.query_one("dq_assets", asset_id=asset_id)
    if not asset or asset.get("time_basis") != "period" or not start_date or not end_date:
        return []
    warnings = []
    for snap in reads.ordered_snapshots(asset_id, only_active=True):
        if snap.get("item_id") == exclude_snapshot_id:
            continue
        if not snap.get("start_date") or not snap.get("end_date"):
            continue
        if str(start_date) <= str(snap["end_date"]) and str(snap["start_date"]) <= str(end_date):
            warnings.append(f"{snap.get('snapshot_label') or snap['item_id']} ({snap['start_date']} → {snap['end_date']})")
    return warnings


def process_snapshot(item_id: str, *, intent: str | None = None, start_date: str | None = None,
                     end_date: str | None = None, snapshot_label: str | None = None,
                     period_column: str | None = None, target_variable: str | None = None,
                     use_case: str | None = None, product: str | None = None,
                     inventory_rows: list[dict] | None = None,
                     schema_override_confirmed: bool = False,
                     full_replacement_confirmed: bool = False, uploaded_by: str | None = None) -> dict:
    """Server-side Step 4/5 confirmation and Step 6 commit for a staged row."""
    item = require_item(item_id)
    if item.get("ingest_status") == "failed":
        raise ValueError("A failed sourcing run cannot be saved and promoted to Test Lab.")
    # Step 3's reviewed rows are authoritative schema metadata. Persist them
    # as part of the final server-side commit so role/type decisions cannot be
    # lost if the UI's earlier inventory request was skipped or interrupted.
    if inventory_rows is not None:
        if not inventory_rows:
            raise ValueError("The reviewed column definitions cannot be empty.")
        put_inventory(item_id, inventory_rows)
        item = require_item(item_id)
    unconfirmed_specials = [
        f"{row['table_name']}.{row['column_name']}"
        for row in get_inventory(item_id)
        if row.get("missing_value_codes_json") and not row.get("missing_codes_confirmed")
    ]
    if unconfirmed_specials:
        raise ValueError(
            "Confirm the special or missing values before saving this snapshot: "
            + ", ".join(unconfirmed_specials)
        )
    uploaded_by = uploaded_by or "system"
    asset_id = item.get("dataset_family_id")
    asset = db.query_one("dq_assets", asset_id=asset_id)
    if not asset:
        raise ValueError("The snapshot has no asset context.")
    selected_intent = intent or item.get("intent") or "add_period"
    _usage_event("upload_step_reached", item_id, detail={"step": 4})
    result = schema_check_for_snapshot(item_id)
    if not result.get("is_match", True) and not schema_override_confirmed:
        raise ValueError("Schema differences require an explicit schema override confirmation.")
    if selected_intent == "full_replacement":
        from assets import service as assets_service
        preview = assets_service.preview_supersede(asset_id, asset["current_version_no"])
        if preview["snapshot_count"] and not full_replacement_confirmed:
            raise ValueError(f"Full replacement confirmation is required for {preview['description']}.")
    _usage_event("upload_step_reached", item_id, detail={"step": 5})
    incoming = item.get("column_type_map_json") or {}
    if isinstance(incoming, str):
        incoming = json.loads(incoming)
    from assets import service as assets_service
    committed = assets_service.finalize_staged_snapshot(
        asset_id, item_id, selected_intent, actor=uploaded_by,
        start_date=start_date, end_date=end_date, snapshot_label=snapshot_label,
        period_column=period_column, column_type_map_json=incoming,
        target_variable=target_variable, use_case=use_case, product=product,
        schema_override_flag=not result.get("is_match", True) and schema_override_confirmed,
        uploaded_by=uploaded_by,
    )
    overridden = []
    if not result.get("is_match", True) and schema_override_confirmed:
        overridden = schema_check.messages(result) + [_schema_consequence(selected_intent)]
        db.insert("dq_asset_events", {
            "event_id": _id("evt"), "asset_id": asset_id, "version_no": committed.get("version_no"),
            "snapshot_id": item_id, "event_type": "schema_override", "actor": uploaded_by,
            "at": db.now_ist(), "summary": "Schema differences were explicitly overridden.",
            "detail_json": {"overridden_warnings": overridden, "schema": result,
                            "consequence": _schema_consequence(selected_intent)},
        })
        _usage_event("schema_warning_overridden", item_id,
                     detail={"warnings": overridden, "intent": selected_intent})
    overlaps = _overlap_warnings(asset_id, start_date, end_date, item_id) if selected_intent == "add_period" else []
    if overlaps:
        db.insert("dq_asset_events", {
            "event_id": _id("evt"), "asset_id": asset_id, "version_no": committed.get("version_no"),
            "snapshot_id": item_id, "event_type": "period_overlap_warning", "actor": uploaded_by,
            "at": db.now_ist(), "summary": "The new period overlaps an active snapshot.",
            "detail_json": {"overlaps": overlaps},
        })
    ing_records.set_status(item_id, "ready")
    from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
    try:
        artifact_ids = persist_snapshot_profile_artifacts(item_id, actor=uploaded_by)
        if not artifact_ids:
            raise RuntimeError("The confirmed exact Data Sourcing profile was not saved to the AAR.")
    except Exception:
        # AAR evidence is part of readiness, not a best-effort side effect.
        ing_records.set_status(item_id, "needs_review")
        raise
    _usage_event("upload_step_reached", item_id, detail={"step": 6})
    _usage_event("snapshot_ready", item_id, detail={"status": "ready"})
    return {**committed, "schema_check": result, "overridden_warnings": overridden,
            "overlap_warnings": overlaps, "consequence": _schema_consequence(selected_intent) if not result.get("is_match", True) else None}


def get_inventory(item_id: str, table: str | None = None) -> list[dict]:
    params: list[Any] = [item_id]
    sql = "SELECT * FROM variable_inventory WHERE item_id=?"
    if table:
        sql += " AND table_name=?"
        params.append(table)
    rows = db.execute(sql, params)
    target = require_item(item_id).get("target_variable")
    for row in rows:
        for key in ("discrepancies", "profile_json", "missing_value_codes_json"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except ValueError:
                    pass
        row["missing_value_codes_json"] = _special_value_list(
            row.get("missing_value_codes_json"))
        row["role"] = row.get("role") or _role_for(row.get("column_name"), row.get("classification"), target)
        row["profile_summary"] = _profile_summary(row)
        profile = row.get("profile_json") or {}
        row["inferred_type"] = profile.get("inferred_type") or row.get("classification")
        row["sample_values"] = profile.get("sample_values") or []
        row["null_count"] = profile.get("null_count", 0)
        row["distinct_count"] = profile.get(
            "distinct_count",
            profile.get("cardinality", profile.get("unique_count", 0)),
        )
        row["missing_value_codes"] = row.get("missing_value_codes_json") or []
        row["missing_codes_confirmed"] = bool(row.get("missing_codes_confirmed"))
        # ING-05: true when this column did NOT get a confident (high-tier,
        # type-resolved) dictionary mapping — its classification came from
        # generic inference. Pre-Phase-4 rows read as provisional=None until
        # the migration backfill (or the next re-profile) sets it explicitly.
        row["provisional"] = bool(row.get("provisional"))
    # Preserve the original dataset column order (not alphabetical).
    order: dict[str, dict[str, int]] = {}
    for t in tables(item_id):
        order[t["table_name"]] = {str(c): i for i, c in enumerate(t.get("columns") or [])}
    rows.sort(key=lambda r: (r["table_name"], order.get(r["table_name"], {}).get(r["column_name"], 10**6)))
    return rows


def put_inventory(item_id: str, rows: list[dict], table: str | None = None) -> list[dict]:
    """ING-06: the Review screen's one decision surface. A row may also carry
    ``mapping_confirmed`` (true/false, optional) — set when the user accepts
    or dismisses a fuzzy-tier ("confirm suggestion") dictionary mapping for
    this column (ING-03). This is the ONLY way a suggestion is ever
    confirmed; there is no separate mapping-confirmation endpoint, per
    ING-06 ("nothing else to decide").
    """
    frame_cache: dict[str, pd.DataFrame] = {}
    item = require_item(item_id)
    for row in rows:
        table_name = row.get("table_name") or table
        col = row.get("column_name")
        if not table_name or not col:
            continue
        found = db.execute(
            "SELECT * FROM variable_inventory WHERE item_id=? AND table_name=? AND column_name=?",
            [item_id, table_name, col],
        )
        current = found[0] if found else {}
        current["missing_value_codes_json"] = _special_value_list(
            current.get("missing_value_codes_json"))
        accepted_mapping = None
        if row.get("mapping_confirmed") is not None:
            candidates = db.execute(
                "SELECT * FROM dq_item_mappings WHERE item_id=? AND table_name=? AND source_column=?",
                [item_id, table_name, col],
            )
            accepted_mapping = next((candidate for candidate in candidates
                                     if candidate.get("tier") == "fuzzy"
                                     and candidate.get("status") == "confirm_suggestion"), None)
            ing_records.confirm_mapping(item_id, table_name, col,
                                        accept=bool(row["mapping_confirmed"]))
            if not row["mapping_confirmed"]:
                accepted_mapping = None
        if accepted_mapping:
            declared = _classification_from_declared(accepted_mapping.get("declared_type"))
            if declared:
                row["classification"] = declared
            row["description"] = accepted_mapping.get("definition") or row.get("description")
            row["dictionary_role"] = accepted_mapping.get("role") or ""
            row["role"] = INVENTORY_ROLE_BY_DICTIONARY.get(
                str(accepted_mapping.get("role") or "").lower(),
                row.get("role") or current.get("role") or "Feature")
            row["business_context"] = accepted_mapping.get("business_context") or ""
            row["missing_value_codes_json"] = _special_value_list(
                accepted_mapping.get("missing_value_codes_json"))
            row["missing_codes_confirmed"] = False
        if current and row.get("classification") and row.get("classification") != current.get("classification"):
            _usage_event("type_override", item_id,
                         detail={"table": table_name, "column": col,
                                 "from": current.get("classification"),
                                 "to": row.get("classification")})
        special_values = _special_value_list(row.get(
            "missing_value_codes_json", current.get("missing_value_codes_json")))
        specials_confirmed = bool(row.get(
            "missing_codes_confirmed", current.get("missing_codes_confirmed", 0)))
        if specials_confirmed:
            try:
                special_values = _validate_confirmed_special_values(
                    special_values,
                    row.get("classification", current.get("classification")),
                )
            except ValueError as exc:
                raise ValueError(f"{table_name}.{col}: {exc}") from exc
        current.update({
            "item_id": item_id, "table_name": table_name, "column_name": col,
            "classification": row.get("classification", current.get("classification", "other")),
            "data_type": row.get("data_type", current.get("data_type", "")),
            "description": row.get("description", current.get("description", "")),
            "discrepancies": row.get("discrepancies", current.get("discrepancies", [])),
            "notes": row.get("notes", current.get("notes", "")),
            "role": row.get("role", current.get("role", "Feature")),
            "dictionary_role": row.get("dictionary_role", current.get("dictionary_role", "")),
            "business_context": row.get("business_context", current.get("business_context", "")),
            "missing_value_codes_json": special_values,
            "missing_codes_confirmed": int(specials_confirmed),
            "profile_json": row.get("profile_json", current.get("profile_json", {})),
            # Rows submitted from Step 3 are explicit user-reviewed schema
            # decisions. They must no longer remain low-confidence inferred
            # definitions downstream.
            "provisional": 0,
            "updated_at": db.now_ist(),
        })
        # A Review decision changes the analytical population. Re-read each
        # table once and regenerate exact evidence from the confirmed regular
        # values rather than retaining the provisional upload profile.
        if table_name not in frame_cache:
            frame_cache[table_name] = _read_table(item_id, table_name)
        frame = frame_cache[table_name]
        if col not in frame.columns:
            raise ValueError(f"Reviewed column '{col}' is not present in table '{table_name}'.")
        regular = _regular_values(frame[col], special_values, specials_confirmed)
        observed = _classify(col, regular, item.get("target_variable"))
        current["data_type"] = str(frame[col].dtype)
        current["profile_json"] = {
            **_column_profile(frame[col], special_values, specials_confirmed,
                              current.get("classification")),
            "inferred_type": observed,
            "sample_values": [_jsonable(value) for value in regular.head(5).tolist()],
        }
        db.upsert("variable_inventory", current)
        _write_snapshot_fingerprint(
            item_id, table_name, col, current["profile_json"], current["classification"])
    _persist_confirmed_type_map(item_id)
    inventory = get_inventory(item_id)
    unconfirmed_specials = any(
        row.get("missing_value_codes_json") and not row.get("missing_codes_confirmed")
        for row in inventory
    )
    schema_result = schema_check_for_snapshot(item_id)
    current_status = require_item(item_id).get("ingest_status")
    if current_status != "failed":
        ing_records.set_status(item_id, ing_status.derive(
            has_data_file=bool(tables(item_id)), profiling_complete=True, fail_reason=None,
            outstanding_confirmations=(1 if unconfirmed_specials or
                                       _step4_confirmation_outstanding(item_id) or
                                       not schema_result.get("is_match", True) else 0),
        ))
    from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
    try:
        persist_snapshot_profile_artifacts(item_id, actor="system")
    except Exception:
        if require_item(item_id).get("ingest_status") == "ready":
            ing_records.set_status(item_id, "needs_review")
        raise
    return get_inventory(item_id, table)


def ingest_summary(item_id: str, table: str | None = None) -> dict:
    """The Review screen's combined ingestion payload (ING-06/07/08):
    derived status, item-level dictionary state, the confidence-tiered
    mapping and the structured non-blocking warnings — all read back via
    ``ingest.records`` in the exact contract shape (docs/0.4.0/
    06-ingestion-contract.md §6)."""
    require_item(item_id)
    _usage_event("upload_step_reached", item_id, detail={"step": 7})
    summary = ing_records.get_ingest_summary(item_id)
    summary["mapping"] = ing_records.get_mapping(item_id, table)
    summary["warnings"] = ing_records.get_warnings(item_id, table)
    item = require_item(item_id)
    summary["dictionary_inspection"] = dictionary_inspection(item_id)
    dictionary_version = (db.query_one(
        "dq_asset_dictionaries", dictionary_version_id=item.get("dictionary_version_id"))
        if item.get("dictionary_version_id") else None)
    summary["dictionary_version"] = ({
        "dictionary_version_id": dictionary_version["dictionary_version_id"],
        "version_no": dictionary_version["dict_version_no"],
        "created_at": dictionary_version.get("created_at"),
    } if dictionary_version else None)
    asset = db.query_one("dq_assets", asset_id=item.get("dataset_family_id")) or {}
    schema_result = schema_check_for_snapshot(item_id)
    schema_events = db.query("dq_asset_events", snapshot_id=item_id,
                             event_type="schema_checked", order_by="at")
    schema_detail = (schema_events[-1].get("detail_json") or {}) if schema_events else {}
    override_events = db.query("dq_asset_events", snapshot_id=item_id,
                               event_type="schema_override", order_by="at")
    overridden = []
    if override_events:
        overridden = (override_events[-1].get("detail_json") or {}).get("overridden_warnings") or []
    overlap_events = db.query("dq_asset_events", snapshot_id=item_id,
                              event_type="period_overlap_warning", order_by="at")
    overlap_warnings = []
    if overlap_events:
        overlap_warnings = [f"Overlaps active snapshot {value}" for value in
                            ((overlap_events[-1].get("detail_json") or {}).get("overlaps") or [])]
    affected = schema_check.affected_columns(schema_result)
    configurations = []
    if affected:
        from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import manifest as manifest_mod
        configurations = manifest_mod.affected_configurations(item_id, affected)
    consequence = None
    if not schema_result.get("is_match", True):
        consequence = _schema_consequence(item.get("intent") or "add_period")
        if item.get("intent") == "full_replacement":
            if configurations:
                consequence += " Affected test configurations: " + ", ".join(configurations) + "."
            else:
                consequence += " no existing test configuration references the affected columns."
    columns = [{"table": row["table_name"], "column": row["column_name"],
                "confirmed_type": row.get("classification") or "text"}
               for row in get_inventory(item_id)]
    superseded = []
    processed = db.query("dq_asset_events", snapshot_id=item_id,
                         event_type="snapshot_processed", order_by="at")
    if processed:
        superseded = ((processed[-1].get("detail_json") or {}).get("superseded") or {}).get("snapshot_ids") or []
        if superseded:
            superseded = [{"snapshot_id": sid,
                           "label": (db.query_one("dq_items", item_id=sid) or {}).get("snapshot_label")}
                          for sid in superseded]
    completion = {
        "asset_name": asset.get("display_name") or item.get("name"),
        "snapshot_label": item.get("snapshot_label"),
        "period_covered": ({"start": item.get("start_date"), "end": item.get("end_date")}
                            if item.get("has_time_period") else "not applicable"),
        "rows_loaded": item.get("row_count"),
        "columns": columns,
        "schema_change_applied": (
            (["reference schema replaced"] + (schema_detail.get("messages") or []))
            if item.get("intent") == "full_replacement" and processed
            else ((schema_detail.get("messages") or []) if override_events else "none")
        ),
        "snapshots_superseded": superseded or "not applicable",
        "warnings_overridden": overridden,
    }
    summary.update({
        "asset_name": completion["asset_name"], "snapshot_label": completion["snapshot_label"],
        "period_covered": completion["period_covered"], "rows_loaded": completion["rows_loaded"],
        "columns": columns, "schema_check": {**schema_result, "messages": schema_detail.get("messages") or schema_check.messages(schema_result),
                                                "affected_test_configurations": configurations,
                                                "consequence": consequence},
        "schema_change_applied": completion["schema_change_applied"],
        "snapshots_superseded": completion["snapshots_superseded"],
        "warnings_overridden": overridden, "overlap_warnings": overlap_warnings,
        "source_parsing_options": item.get("source_parsing_options_json") or {},
        "file_context": item.get("file_context_json") or {},
        "completion_summary": completion,
    })
    return summary


def review_dictionary(item_id: str, header_mapping: dict[str, str],
                      value_mapping: dict[str, dict[str, str]]) -> dict:
    """Persist explicit dictionary semantics and re-run the existing profile pass."""
    inspection = dictionary_inspection(item_id)
    if inspection is None:
        raise ValueError("This snapshot has no inspectable CSV or Excel dictionary.")
    allowed_fields = {field["name"] for field in inspection["fields"]}
    unknown_fields = sorted(set(header_mapping) - allowed_fields)
    if unknown_fields:
        raise ValueError("Unknown canonical dictionary fields: " + ", ".join(unknown_fields))
    if not header_mapping.get("column_name"):
        raise ValueError("Map a dictionary field to Column name before saving.")
    sources = [source for source in header_mapping.values() if source]
    missing_sources = sorted(set(sources) - set(inspection["source_columns"]))
    if missing_sources:
        raise ValueError("Mapped dictionary fields are not present: " + ", ".join(missing_sources))
    if len(sources) != len(set(sources)):
        raise ValueError("Each dictionary source field may be mapped only once.")
    for field, mappings in value_mapping.items():
        if field not in {"logical_type", "role"}:
            raise ValueError(f"Unsupported dictionary value mapping field: {field}")
        allowed = set(VALUE_VOCABULARIES[field])
        invalid = sorted(set(mappings.values()) - allowed)
        if invalid:
            raise ValueError(f"Unsupported {field} mapping values: {', '.join(invalid)}")
    db.update("dq_items", {"item_id": item_id}, {
        "dictionary_header_mapping_json": header_mapping,
        "dictionary_value_mapping_json": value_mapping,
        "dictionary_mapping_confirmed": 1,
        "updated_at": db.now_ist(),
    })
    profile_item(item_id)
    return ingest_summary(item_id)


def _inventory_map(item_id: str, table: str) -> dict[str, str]:
    return {r["column_name"]: r["classification"] for r in get_inventory(item_id, table)}


def _supporting(frame: pd.DataFrame, item: dict, table_name: str | None = None) -> dict:
    cols = set(frame.columns)
    inventory = get_inventory(item["item_id"], table_name) if table_name else []
    period_candidates = [r["column_name"] for r in inventory if is_period_axis(frame, r)]
    date = next((c for c in ("observation_date", "reporting_period", "load_date", "as_of_date") if c in cols), None) or (period_candidates[0] if period_candidates else None)
    out = {"target": item.get("target_variable"), "date": date, "observation_date": date}
    for key in ("origination_date", "maturity_date", "feature_available_date", "segment", "property_type", "macro_regime"):
        if key in cols:
            out[key] = key
    if "facility_id" in cols:
        out["key"] = "facility_id"
    dictionary = _parse_dictionary(item["item_id"])
    entries = {**(dictionary.get("*") or {}),
               **((dictionary.get(_norm_name(table_name)) or {}) if table_name else {})}
    if entries:
        out["dictionary"] = entries
    if table_name:
        schema = _load_schema(item["item_id"])
        if schema:
            expected = _schema_expected_types(schema, table_name, frame)
            fk_map = _schema_fk_map(schema, table_name)
            declared = _schema_declared(schema, table_name, frame, expected)
            out.update({
                "schema": schema,
                "expected_types": expected,
                "fk_map": fk_map,
                "declared_schema": declared,
                "parent_frames": {},
            })
            for ref in fk_map.values():
                ref_table = ref.get("ref_table")
                if ref_table and ref_table not in out["parent_frames"]:
                    try:
                        out["parent_frames"][ref_table] = _read_table(item["item_id"], ref_table)
                    except Exception:
                        pass
            if declared.get("primary_key"):
                out["key"] = declared["primary_key"]
            if "observation_date" in frame.columns and "origination_date" in frame.columns:
                out["required_window"] = {
                    "start": pd.to_datetime(frame["observation_date"], errors="coerce").min().isoformat(),
                    "end": pd.to_datetime(frame["observation_date"], errors="coerce").max().isoformat(),
                }
    if "origination_date" in cols and "maturity_date" in cols:
        out["date_pairs"] = [["origination_date", "maturity_date"]]
    if "origination_date" in cols and date:
        out["observation_date"] = date
    return {k: v for k, v in out.items() if v}


# Shared low-level helpers a test implementation may call. Only the ones the
# inlined source actually uses are imported into the snippet, so everything
# statistical stays visible in the snippet itself.
_SNIPPET_HELPERS = ("_safe", "_pass", "_not", "_cols", "_date", "_target")


def results(item_id: str, scope: str = "framework") -> list[dict]:
    if scope == "all":
        # Combined Test Lab output only — RCA additional analyses (scope 'rca:*')
        # never enter the roll-up or the health score.
        rows = db.execute(
            "SELECT * FROM results_v2 WHERE item_id=? AND scope IN ('framework','incremental') "
            "ORDER BY table_name, run_at", [item_id])
    else:
        rows = db.execute("SELECT * FROM results_v2 WHERE item_id=? AND scope=? ORDER BY table_name, run_at", [item_id, scope])
    for row in rows:
        for key in ("threshold_json", "evidence_json", "columns_json"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except ValueError:
                    pass
        row["table"] = row.get("table_name")
        row["threshold"] = row.get("threshold_json")
        row["evidence"] = row.get("evidence_json") or {}
        if not isinstance(row["evidence"], dict):
            row["evidence"] = {"detail": str(row["evidence"])}
        row["not_runnable_reason"] = row.get("not_runnable_reason") or row["evidence"].get("not_runnable_reason") or row["evidence"].get("reason")
        row["watch_note"] = bool(row.get("watch_note"))
        # The result's own columns (the exact variable(s) that execution ran
        # on — feedback 09-07 4.1); legacy rows fall back to the plan row.
        cols = row.get("columns_json")
        if not cols:
            plan = db.query_one("plan_v2", row_id=row.get("row_id")) or {}
            cols = plan.get("columns_json")
            if isinstance(cols, str):
                try:
                    cols = json.loads(cols)
                except ValueError:
                    cols = []
        row["columns"] = cols or []
    return rows


def _rules_for(item: dict) -> list[dict]:  # noqa: ARG001
    """KB-01 (C-36): no domain rule set ships in code. The hardcoded
    ``knowledge_base/business_rules.json`` and its loader are deleted —
    this recommender step (itself retiring with the wizard at the Phase-6
    cutover, redesign §1.5) now has nothing to keyword-match against and
    always returns empty. Rule-driven diagnostics read the governed,
    uploaded KB instead (``kb.list_eligible_rules``, Phase 5)."""
    return []


# Only the highest-quality candidates become proposals (feedback 09-07 4.2).
TOP_RECOMMENDATIONS = 5


def _criticality(area_id: str | None, family: str) -> str:
    if not area_id:
        return "Medium"
    row = db.query_one("fw_family_weights", area_id=area_id, family=family)
    return (row or {}).get("criticality") or "Medium"


def score(item_id: str) -> dict:
    row = db.query_one("scores_v2", item_id=item_id, scope="all")
    return {
        "provisional": row.get("provisional") if row else None,
        "final": row.get("final") if row else None,
        "breakdown": row.get("breakdown_json") if row else [],
        "stage": row.get("stage") if row else None,
    }


def _register_rows() -> list[dict]:
    from dq_diagnostics import register as _register  # noqa: PLC0415 — avoid boot-order cycle
    return _register.list_register()


def framework_overview() -> dict:
    """The 0.4.0 framework reference (FWK-01): counts from the 9-diagnostic
    register and the seeded L2 taxonomy, replacing the retired fw_areas /
    fw_tests content. Stage semantics follow FWK-08: Stage 1 writes what
    Stage 2 consumes; 'Both' diagnostics count in each."""
    rows = _register_rows()
    tax = db.query("framework_taxonomy")

    def _reached(stage_key: str) -> set:
        touched: set = set()
        for r in rows:
            if r["stage"] in {stage_key, "Both"}:
                touched.update(r.get("l2_areas") or [])
        return touched

    def _count(stage_key: str) -> int:
        return sum(1 for r in rows if r["stage"] in {stage_key, "Both"})

    return {
        "stage1": {"title": "Stage 1 — structural / hard",
                   "desc": "Deterministic structural and rule diagnostics. Stage 1 runs first and produces what Stage 2 depends on (value-semantics tags when diagnostic #8 is enabled).",
                   "areas": len(_reached("Stage 1")), "tests": _count("Stage 1")},
        "stage2": {"title": "Stage 2 — statistical screens",
                   "desc": "Statistical screens producing candidate flags for SME judgement, never auto-failures (FWK-07). Guarded by class eligibility, material fields and the value-semantics gate (FWK-09); every screen is workflow-pending in this release.",
                   "areas": len(_reached("Stage 2")), "tests": _count("Stage 2")},
        "coverage_note": f"{len(tax)} assessment areas; "
                         f"{sum(1 for t in tax if t.get('coverage_status') == 'gap')} are honest GAPs (FWK-14).",
    }


STAGE_LABELS = {"stage1": "Systemic", "stage2": "Specific", "both": "Both"}


def framework_areas() -> list[dict]:
    """The 11 L2 assessment areas grouped under their 6 L1 themes, with the
    registered diagnostics (name + register id + workflow status) that reach
    each area per stage. GAP areas honestly list nothing (FWK-14/15)."""
    rows = _register_rows()

    def _diags_for(l2_id: str, stage_key: str) -> list[str]:
        out = []
        for r in sorted(rows, key=lambda x: x["diagnostic_id"]):
            if l2_id in (r.get("l2_areas") or []) and r["stage"] in {stage_key, "Both"}:
                suffix = "" if r["workflow_status"] == "executable" else " (workflow pending)"
                out.append(f"{r['name']} · #{r['diagnostic_id']}{suffix}")
        return out

    areas = []
    for t in db.query("framework_taxonomy", order_by="l2_id"):
        areas.append({
            "area_id": t["l2_id"], "l1_theme": t["l1_theme"], "l2_area": t["name"],
            "objective": t["objective"], "why_it_matters": t["why_it_matters"],
            "stage": "both", "stage_label": "Both",
            "coverage_status": t.get("coverage_status"),
            "coverage_reason": t.get("coverage_reason"),
            "stage1_tests": _diags_for(t["l2_id"], "Stage 1"),
            "stage2_tests": _diags_for(t["l2_id"], "Stage 2"),
        })
    return areas


def framework_tests() -> list[dict]:
    rows = db.query("fw_tests", order_by="stage, test_name")
    for row in rows:
        row["supporting_columns"] = row.pop("supporting_columns_json", "")
        row["key_parameters"] = row.pop("key_parameters_json", {})
        row["stage_label"] = STAGE_LABELS.get(row.get("stage"), row.get("stage"))
        row["param_spec"] = _param_spec(row.get("test_name") or "")
        row["python_code"] = get_impl_source(row.get("test_name") or "")
        row["column_rules"] = _column_rules(row.get("test_name") or "")
    return rows


def framework_matrix() -> dict:
    families = sorted({r["family"] for r in db.query("fw_family_weights")}, key=lambda x: [
        "IRB / Basel", "IFRS9", "Stress Testing", "Credit Decisioning", "Treasury / ALM",
        "Fraud / AML", "AI / ML", "Collections & Recovery", "Marketing / Propensity"].index(x))
    areas = framework_areas()
    ratings: dict[str, dict[str, str]] = {}
    for r in db.query("fw_family_weights"):
        ratings.setdefault(r["area_id"], {})[r["family"]] = r["criticality"]
    return {"areas": areas, "families": families, "ratings": ratings}
