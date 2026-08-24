"""Newton Phase 1 — DiscoveryState: deterministic per-column profiling.

Pure Python/pandas; no LLM. Produces the machine-readable artifact that
downstream agents (Poincaré, Fermat) consume instead of re-running Fisher.
Stored as backend/skills/db_discovery_{logical_db}.json alongside Newton's .md.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Schema models
# ---------------------------------------------------------------------------

class ColumnMeta(BaseModel):
    name: str
    dtype: str
    null_rate: float = 0.0          # 0.0 – 1.0
    cardinality: int = 0
    non_null: int = 0
    # numeric only
    mean: float | None = None
    std: float | None = None
    min_val: float | None = None
    max_val: float | None = None
    q25: float | None = None
    q50: float | None = None
    q75: float | None = None
    # categorical only
    top_values: dict[str, int] | None = None
    # enrichment
    description: str = ""
    is_candidate_key: bool = False      # cardinality ≥ 95% of row_count
    is_likely_foreign_key: bool = False # cardinality ≥ 50% + _id/_code/_ref suffix
    anomalies: list[str] = Field(default_factory=list)


class CrossTableCandidate(BaseModel):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    confidence: str   # "high" | "medium"
    reason: str


class TableMeta(BaseModel):
    table_name: str
    row_count: int
    col_count: int
    columns: list[ColumnMeta]
    candidate_join_keys: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


class DiscoveryState(BaseModel):
    logical_db: str
    table_count: int
    total_rows: int
    tables: list[TableMeta]
    cross_table_candidates: list[CrossTableCandidate] = Field(default_factory=list)
    db_anomalies: list[str] = Field(default_factory=list)
    schema_hash: str = ""

    def compress(self) -> str:
        """Compact text for Newton's LLM prompt. No raw data, no PII."""
        lines = [
            f"DATABASE: {self.logical_db} — {self.total_rows:,} rows across "
            f"{self.table_count} table(s)",
            "",
        ]
        for t in self.tables:
            lines.append(f"TABLE: {t.table_name} (rows={t.row_count:,})")
            for c in t.columns:
                parts: list[str] = [f"{c.name}: {c.dtype}"]
                parts.append(f"{c.null_rate * 100:.0f}% null")
                parts.append(f"{c.cardinality:,} distinct")
                if c.mean is not None:
                    parts.append(
                        f"mean={c.mean:.4g}, std={c.std:.4g}, "
                        f"range=[{c.min_val:.4g}, {c.max_val:.4g}]"
                    )
                if c.top_values:
                    tv = ", ".join(
                        f"'{k}':{v}" for k, v in list(c.top_values.items())[:3]
                    )
                    parts.append(f"top=[{tv}]")
                tags: list[str] = []
                if c.is_candidate_key:
                    tags.append("ID/key")
                if c.is_likely_foreign_key:
                    tags.append("FK candidate")
                if tags:
                    parts.append(f"[{', '.join(tags)}]")
                if c.anomalies:
                    parts.append(f"ANOMALY: {'; '.join(c.anomalies)}")
                if c.description:
                    parts.append(f"— {c.description}")
                lines.append("  - " + ", ".join(parts))
            if t.anomalies:
                lines.append(f"  TABLE ANOMALIES: {'; '.join(t.anomalies)}")
            lines.append("")
        if self.cross_table_candidates:
            lines.append("Cross-table join candidates:")
            for cj in self.cross_table_candidates:
                lines.append(
                    f"  - {cj.left_table}.{cj.left_column} <-> "
                    f"{cj.right_table}.{cj.right_column} "
                    f"({cj.confidence}: {cj.reason})"
                )
        if self.db_anomalies:
            lines.append(f"\nDB-LEVEL ANOMALIES: {'; '.join(self.db_anomalies)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 1: deterministic computation
# ---------------------------------------------------------------------------

_FK_SUFFIXES = ("_id", "_key", "_code", "_num", "_ref", "_no", "_nbr")


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else round(f, 6)  # NaN → None
    except (TypeError, ValueError):
        return None


# Name hints for numeric plausibility checks (credit-risk vocabulary). A column
# whose name implies a non-negative quantity but carries negatives is a defect;
# probability/ratio columns are expected in [0,1] and percentages in [0,100].
# Substring hints for non-negative quantities (negatives here are a defect).
_NONNEG_HINTS = (
    "amount", "balance", "age", "count", "qty", "quantity", "num", "nbr", "units",
    "principal", "exposure", "income", "salary", "limit", "tenure", "duration",
    "days", "months", "years", "price", "value", "score", "weight", "volume",
    "revenue", "cost", "fee", "deposit", "loan", "ltv",
)
# Whole-token hints (matched on word boundaries, NOT substrings, so "dpd_count"
# does not collide with "pd"). Probability/LGD expected in [0,1]; pct in [0,100].
_UNIT_INTERVAL_TOKENS = ("pd", "lgd", "prob", "probability")  # expect [0,1]
_PERCENT_TOKENS = ("pct", "percent", "percentage")            # expect [0,100]


def _numeric_range_anomalies(col: str, s: pd.Series, cm: ColumnMeta) -> list[str]:
    """Deterministic numeric plausibility / range-violation checks. Each string
    starts with a stable code token (see ai.framework_context.ANOMALY_AREA_MAP):
    negative_values | range_violation | extreme_outliers."""
    out: list[str] = []
    name = str(col).lower()
    toks = set(re.split(r"[^a-z0-9]+", name))
    vals = s.dropna().astype(float)
    if vals.empty:
        return out
    mn = float(vals.min())
    mx = float(vals.max())
    is_unit = any(t in _UNIT_INTERVAL_TOKENS for t in toks)
    is_pct = any(t in _PERCENT_TOKENS for t in toks)
    # Implied-range violations (most specific wins; one per column).
    if is_unit and (mn < 0 or mx > 1.0):
        out.append(f"range_violation (expected [0,1], observed [{mn:.4g}, {mx:.4g}])")
    elif is_pct and (mn < 0 or mx > 100.0):
        out.append(f"range_violation (expected [0,100], observed [{mn:.4g}, {mx:.4g}])")
    elif mn < 0 and any(h in name for h in _NONNEG_HINTS):
        out.append(f"negative_values (min={mn:.4g})")
    # Extreme outliers via a far IQR fence (k=3), independent of the above.
    if cm.q25 is not None and cm.q75 is not None:
        iqr = cm.q75 - cm.q25
        if iqr > 0:
            lo, hi = cm.q25 - 3 * iqr, cm.q75 + 3 * iqr
            cnt = int(((vals < lo) | (vals > hi)).sum())
            if cnt > 0:
                out.append(f"extreme_outliers (n={cnt}, {cnt / len(vals) * 100:.1f}%)")
    return out


def _profile_column(col: str, s: pd.Series, row_count: int,
                    description: str) -> ColumnMeta:
    null_rate = round(float(s.isna().mean()), 6)
    cardinality = int(s.nunique(dropna=True))
    non_null = int(s.notna().sum())

    cm = ColumnMeta(
        name=str(col),
        dtype=str(s.dtype),
        null_rate=null_rate,
        cardinality=cardinality,
        non_null=non_null,
        description=description,
    )

    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        d = s.describe()
        cm.mean = _safe_float(d.get("mean"))
        cm.std = _safe_float(d.get("std"))
        cm.min_val = _safe_float(d.get("min"))
        cm.max_val = _safe_float(d.get("max"))
        cm.q25 = _safe_float(d.get("25%"))
        cm.q50 = _safe_float(d.get("50%"))
        cm.q75 = _safe_float(d.get("75%"))
    else:
        top = s.dropna().astype(str).value_counts().head(5)
        cm.top_values = {str(k): int(v) for k, v in top.items()}

    if row_count > 0:
        ratio = cardinality / row_count
        col_lower = str(col).lower()
        if ratio >= 0.95:
            cm.is_candidate_key = True
        elif ratio >= 0.50 and any(col_lower.endswith(sfx) for sfx in _FK_SUFFIXES):
            cm.is_likely_foreign_key = True

    # --- deterministic anomaly detection (tiered; codes are stable leading
    # tokens consumed by ai.framework_context for Newton's Key Anomalies) ---
    anomalies: list[str] = []
    # Missingness — single most-severe tier per column (Critical/Warn/Info).
    if null_rate >= 1.0:
        anomalies.append("null_critical (100% null)")
    elif null_rate >= 0.5:
        anomalies.append(f"null_high ({null_rate * 100:.0f}% null)")
    elif null_rate > 0.2:
        anomalies.append(f"null_elevated ({null_rate * 100:.0f}% null)")
    # Degenerate (constant) feature — no analytical signal.
    if cardinality == 1 and non_null > 0:
        try:
            anomalies.append(f"constant_value (={s.dropna().iloc[0]})")
        except Exception:  # noqa: BLE001 — value formatting must never break profiling
            anomalies.append("constant_value")
    # Numeric plausibility / range violations.
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and non_null > 0:
        anomalies.extend(_numeric_range_anomalies(col, s, cm))
    cm.anomalies = anomalies
    return cm


def _find_cross_table_candidates(
    tables: list[TableMeta],
) -> list[CrossTableCandidate]:
    """Heuristic: exact column-name match across tables where both sides are key/FK."""
    from collections import defaultdict
    col_map: dict[str, list[tuple[str, str, int, int]]] = defaultdict(list)
    for t in tables:
        for c in t.columns:
            if c.is_candidate_key or c.is_likely_foreign_key:
                col_map[c.name.lower()].append(
                    (t.table_name, c.name, c.cardinality, t.row_count)
                )

    candidates: list[CrossTableCandidate] = []
    for occurrences in col_map.values():
        if len(occurrences) < 2:
            continue
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                lt, lc, lcard, lrows = occurrences[i]
                rt, rc, rcard, rrows = occurrences[j]
                if lt == rt:
                    continue
                denom = max(lcard, rcard)
                card_ratio = min(lcard, rcard) / denom if denom else 0.0
                if card_ratio >= 0.80:
                    conf = "high"
                    reason = (
                        f"exact name match, cardinality match "
                        f"({lcard:,} ≈ {rcard:,})"
                    )
                elif card_ratio >= 0.40:
                    conf = "medium"
                    reason = (
                        f"exact name match, cardinality ratio "
                        f"{card_ratio:.0%}"
                    )
                else:
                    continue
                candidates.append(
                    CrossTableCandidate(
                        left_table=lt, left_column=lc,
                        right_table=rt, right_column=rc,
                        confidence=conf, reason=reason,
                    )
                )
    return candidates


def compute_discovery_state(
    logical_db: str,
    tables_meta: list[dict],
) -> DiscoveryState:
    """Newton Phase 1. Pure Python/pandas; zero LLM calls.

    tables_meta: list of dicts with keys:
        table (str), row_count (int), pk (str|None), date_col (str|None),
        columns (list[str]), descriptions (dict[str, str])
    Returns DiscoveryState ready for .json persistence and LLM prompt compression.
    """
    from database import load_table  # local import to avoid circular deps

    table_metas: list[TableMeta] = []
    total_rows = 0

    for t_info in tables_meta:
        table_name = str(t_info.get("table") or "")
        descriptions: dict[str, str] = t_info.get("descriptions") or {}
        row_count_hint = int(t_info.get("row_count") or 0)

        try:
            df: pd.DataFrame = load_table(table_name)
        except Exception:
            col_metas = [
                ColumnMeta(
                    name=c, dtype="unknown",
                    description=descriptions.get(c, ""),
                )
                for c in (t_info.get("columns") or [])
            ]
            table_metas.append(
                TableMeta(
                    table_name=table_name,
                    row_count=row_count_hint,
                    col_count=len(col_metas),
                    columns=col_metas,
                    anomalies=["table_not_loadable"],
                )
            )
            total_rows += row_count_hint
            continue

        row_count = len(df)
        total_rows += row_count
        col_metas = [
            _profile_column(col, df[col], row_count, descriptions.get(str(col), ""))
            for col in df.columns
        ]

        high_null = [c.name for c in col_metas if c.null_rate > 0.5]
        table_anomalies = (
            [f"high_null_columns: {', '.join(high_null)}"] if high_null else []
        )
        candidate_join_keys = [
            c.name for c in col_metas
            if c.is_candidate_key or c.is_likely_foreign_key
        ]

        table_metas.append(
            TableMeta(
                table_name=table_name,
                row_count=row_count,
                col_count=len(df.columns),
                columns=col_metas,
                candidate_join_keys=candidate_join_keys,
                anomalies=table_anomalies,
            )
        )

    cross = _find_cross_table_candidates(table_metas)

    schema_sig = json.dumps(
        [{"t": t.table_name, "cols": [c.name for c in t.columns]}
         for t in table_metas],
        sort_keys=True,
    )
    schema_hash = hashlib.sha1(schema_sig.encode()).hexdigest()[:12]

    db_anomalies = ["no_tables_found"] if not table_metas else []

    return DiscoveryState(
        logical_db=logical_db,
        table_count=len(table_metas),
        total_rows=total_rows,
        tables=table_metas,
        cross_table_candidates=cross,
        db_anomalies=db_anomalies,
        schema_hash=schema_hash,
    )


# ---------------------------------------------------------------------------
# Fisher shim: extract Fisher-format output from DiscoveryState
# ---------------------------------------------------------------------------

def from_discovery_state_for_fisher(ds: DiscoveryState, table_name: str) -> dict | None:
    """Convert one table's DiscoveryState entry into Fisher's screen() output format.

    Returns None if the table is not found in ds.
    Gauss can call this to skip Fisher's DataFrame load when Newton's .json exists.
    """
    for t in ds.tables:
        if t.table_name != table_name:
            continue
        per_column: dict[str, dict] = {}
        for c in t.columns:
            info: dict = {
                "dtype": c.dtype,
                "null_rate": c.null_rate,
                "cardinality": c.cardinality,
                "non_null": c.non_null,
            }
            if c.mean is not None:
                info["describe"] = {
                    "mean": c.mean, "std": c.std,
                    "min": c.min_val, "max": c.max_val,
                    "25%": c.q25, "50%": c.q50, "75%": c.q75,
                }
            if c.top_values is not None:
                info["top_values"] = c.top_values
            per_column[c.name] = info
        return {
            "row_count": t.row_count,
            "col_count": t.col_count,
            "per_column": per_column,
            "source": "discovery_state",
        }
    return None
