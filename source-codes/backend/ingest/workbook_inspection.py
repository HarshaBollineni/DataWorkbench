"""Lightweight, deterministic discovery for sourcing files.

This is intentionally an inspection seam, not ingestion: it samples workbook
tabs so the user can review or override the proposed data, dictionary and
context roles before DataWorkbench writes its normal staged tables.
"""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd


_DICTIONARY_HEADERS = {
    "column", "column_name", "variable", "variable_name", "field", "field_name",
    "type", "data_type", "logical_type", "role", "description", "definition",
    "missing_value_codes", "special_values", "valid_values", "business_context",
}


def _normalise(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold())).strip("_")


def _dictionary_header(raw: pd.DataFrame) -> tuple[int, list[str]]:
    best_row, best_headers, best_score = 0, [], -1
    for index, row in raw.iterrows():
        headers = [str(value).strip() for value in row if not pd.isna(value) and str(value).strip()]
        normalised = {_normalise(value) for value in headers}
        matches = normalised & _DICTIONARY_HEADERS
        score = len(matches) + (3 if matches & {"column", "column_name", "variable", "variable_name", "field", "field_name"} else 0)
        if score > best_score:
            best_row, best_headers, best_score = int(index), headers, score
    return best_row, best_headers


def _overview_score(name: str, raw: pd.DataFrame) -> int:
    score = 8 if any(token in _normalise(name) for token in ("overview", "context", "about", "metadata", "readme")) else 0
    populated = raw.dropna(how="all")
    if 2 <= len(raw.columns) <= 3 and len(populated) >= 2:
        paired = sum(1 for _, row in populated.iterrows() if row.notna().sum() >= 2)
        if paired / len(populated) >= 0.6:
            score += 4
    return score


def _context_text(raw: pd.DataFrame) -> str:
    lines = []
    for _, row in raw.iterrows():
        values = [str(value).strip() for value in row if not pd.isna(value) and str(value).strip()]
        if values:
            lines.append(": ".join(values))
    return "\n".join(lines)


def inspect_excel(path: str | Path | BytesIO, *, role: str = "data",
                  workbook_name: str | None = None) -> dict[str, Any]:
    source = Path(path) if isinstance(path, (str, Path)) else path
    display_name = workbook_name or (Path(path).name if isinstance(path, (str, Path)) else "workbook.xlsx")
    workbook = pd.ExcelFile(source)
    raw_by_sheet = {
        sheet: pd.read_excel(workbook, sheet_name=sheet, header=None, nrows=40)
        for sheet in workbook.sheet_names
    }
    profiles = {}
    for sheet, raw in raw_by_sheet.items():
        header_row, headers = _dictionary_header(raw)
        dictionary_score = len({_normalise(value) for value in headers} & _DICTIONARY_HEADERS)
        if any(token in _normalise(sheet) for token in ("dictionary", "schema")):
            dictionary_score += 4
        profiles[sheet] = {
            "header_row": header_row,
            "headers": headers,
            "dictionary_score": dictionary_score,
            "overview_score": _overview_score(sheet, raw),
            "data_score": int(raw.notna().sum().sum()) + len(raw.columns) * 3,
        }

    dictionary_sheet = max(workbook.sheet_names, key=lambda name: profiles[name]["dictionary_score"], default=None)
    if dictionary_sheet and profiles[dictionary_sheet]["dictionary_score"] < 2:
        dictionary_sheet = workbook.sheet_names[0] if role == "dictionary" else None
    overview_candidates = [name for name in workbook.sheet_names if name != dictionary_sheet]
    overview_sheet = max(overview_candidates, key=lambda name: profiles[name]["overview_score"], default=None)
    if overview_sheet and profiles[overview_sheet]["overview_score"] < 4:
        overview_sheet = None
    data_candidates = [name for name in workbook.sheet_names if name not in {dictionary_sheet, overview_sheet}]
    data_sheet = max(data_candidates, key=lambda name: profiles[name]["data_score"], default=None)
    if role == "dictionary":
        data_sheet = None
    elif data_sheet is None and workbook.sheet_names:
        data_sheet = workbook.sheet_names[0]

    sheets = []
    for name in workbook.sheet_names:
        suggested = "data" if name == data_sheet else "dictionary" if name == dictionary_sheet else "file_context" if name == overview_sheet else "unclassified"
        raw = raw_by_sheet[name]
        sheets.append({
            "name": name, "suggested_role": suggested,
            "row_sample_count": int(len(raw.dropna(how="all"))),
            "column_count": int(len(raw.columns)),
        })
    return {
        "workbook_name": display_name,
        "data_sheet": data_sheet,
        "dictionary_sheet": dictionary_sheet,
        "dictionary_header_row": profiles[dictionary_sheet]["header_row"] if dictionary_sheet else None,
        "dictionary_headers": profiles[dictionary_sheet]["headers"] if dictionary_sheet else [],
        "overview_sheet": overview_sheet,
        "file_context": _context_text(raw_by_sheet[overview_sheet]) if overview_sheet else "",
        "sheets": sheets,
    }


def inspect_delimited(path: str | Path | BytesIO, *, role: str = "data",
                      delimiter: str | None = None, workbook_name: str | None = None) -> dict[str, Any]:
    source = Path(path) if isinstance(path, (str, Path)) else path
    display_name = workbook_name or (Path(path).name if isinstance(path, (str, Path)) else "source.csv")
    frame = pd.read_csv(source, sep=delimiter or None, engine="python", nrows=40, keep_default_na=False)
    return {
        "workbook_name": display_name,
        "data_sheet": None, "dictionary_sheet": None,
        "dictionary_header_row": 0 if role == "dictionary" else None,
        "dictionary_headers": list(map(str, frame.columns)) if role == "dictionary" else [],
        "overview_sheet": None, "file_context": "",
        "sheets": [{"name": Path(display_name).stem, "suggested_role": role,
                    "row_sample_count": int(len(frame)), "column_count": int(len(frame.columns))}],
    }
