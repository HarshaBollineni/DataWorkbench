"""Governed RCA statistical-probe library (deterministic pandas/numpy helpers).

Belongs in this module: small, deterministic, dependency-light functions of the
shape ``fn(df: pd.DataFrame, params: dict) -> dict`` that can be pointed at ANY
already-loaded DataFrame to produce a structured RCA "evidence" result
(``summary`` / ``metrics`` / ``evidence_rows`` / ``interpretation_hints`` /
``recommended_followups``) — see ``OUTPUT_SCHEMA``. These are vetted probes an
agent (or a human) can call instead of writing fragile ad hoc pandas snippets.

Does NOT belong in this module (Phase 2 / PLT-08 declutter):
  * Table/warehouse-specific configuration. Until 0.4.0 Phase 2 this module
    imported a hardcoded per-table "grain key" dictionary
    (``database.TABLE_KEYS``) to guess a table's date column. ``database.py``
    was deleted at 0.2.0, which broke this import outright — but the deeper
    problem was the dictionary itself: baking one fixed demo warehouse's schema
    into shared helper code is exactly the coupling this phase removes. Any
    grain-key info a caller has (e.g. read live from
    ``system_db.table_metadata.date_col``) is passed in explicitly via
    ``params["date_col"]`` / ``params["keys"]`` / ``params["key_col"]`` —
    this module never looks such config up itself, and falls back to a
    conservative column-name heuristic (never a guess baked into source).
  * Registry/catalogue concerns — the governed catalogue that federates these
    helpers alongside gx metrics and tool_registry tools is ``ai/test_kit.py``.
  * LLM prompting — tool descriptions for the model live in ``ai/prompts.py``.

The ``RCAHelper`` dataclass/``HELPERS`` dict below is this module's own richer
metadata (supported_test_types, input_roles, param/output schema) for the 12
probes; ``ai/test_kit.py`` imports ``HELPERS`` and wraps each into its own
lightweight ``Helper`` so there is still exactly one catalogue platform-wide.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd


HelperFn = Callable[[pd.DataFrame, dict[str, Any]], dict[str, Any]]


def _json_safe(value: Any) -> Any:
    """Coerce one scalar to a JSON-serializable value (numpy/NaT/NaN aware)."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return None
    return value


def _clean(obj: Any) -> Any:
    """Recursively apply :func:`_json_safe` across dict/list/tuple containers."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, tuple):
        return [_clean(v) for v in obj]
    return _json_safe(obj)


def _result(summary: str, metrics: dict[str, Any], evidence_rows: list[dict[str, Any]],
            interpretation_hints: list[str], recommended_followups: list[str]) -> dict[str, Any]:
    """Assemble the standard RCA-helper output shape (see ``OUTPUT_SCHEMA``)."""
    return _clean({
        "summary": summary,
        "metrics": metrics,
        "evidence_rows": evidence_rows[:25],
        "interpretation_hints": interpretation_hints,
        "recommended_followups": recommended_followups,
    })


def _column(params: dict[str, Any], *names: str) -> str | None:
    """Resolve a single column name from the first present key in ``names``,
    else the first entry of ``params["columns"]``. Strips a ``table.`` prefix."""
    for name in names:
        value = params.get(name)
        if isinstance(value, str) and value:
            return value.split(".")[-1]
    columns = params.get("columns") or []
    if columns:
        return str(columns[0]).split(".")[-1]
    return None


def _date_col(params: dict[str, Any], df: pd.DataFrame) -> str | None:
    """Resolve the date/time column used to split "early" vs "recent" windows.

    Resolution order (conservative — no table-specific knowledge is hardcoded
    in this module):
      1. an explicit ``params["date_col"]`` that names a real column of ``df``;
      2. an explicit ``params["keys"]["date_col"]`` — grain-key metadata the
         CALLER may supply (e.g. read live from
         ``system_db.table_metadata.date_col``); this module never fetches it;
      3. a heuristic: the first column whose name contains "date"/"time"/"month".

    Returns ``None`` (never a guess) when none of the above resolves; callers
    that require a date column then raise ``ValueError``.
    """
    explicit = params.get("date_col")
    if explicit and explicit in df.columns:
        return explicit
    configured = (params.get("keys") or {}).get("date_col")
    if configured and configured in df.columns:
        return configured
    for col in df.columns:
        if any(token in str(col).lower() for token in ("date", "time", "month")):
            return col
    return None


def _numeric(series: pd.Series) -> pd.Series:
    """Coerce a series to numeric, turning unparseable values into NaN."""
    return pd.to_numeric(series, errors="coerce")


def _split_by_date(df: pd.DataFrame, params: dict[str, Any]
                    ) -> tuple[str, pd.Timestamp, pd.Series, pd.Series]:
    """Split ``df`` into an early/recent window at the ``split_quantile``
    (default 0.4) of its resolved date column. Returns
    ``(date_col, cutoff, early_mask, recent_mask)``. Raises ``ValueError`` when
    no date column can be resolved (see :func:`_date_col`)."""
    date_col = _date_col(params, df)
    if not date_col:
        raise ValueError("date_col is required for this helper.")
    q = float(params.get("split_quantile", 0.4))
    dates = pd.to_datetime(df[date_col], errors="coerce")
    cutoff = dates.quantile(q)
    early_mask = dates <= cutoff
    recent_mask = dates > cutoff
    return date_col, cutoff, early_mask, recent_mask


def _time_window_drift(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Compare a numeric column's mean/median between an early and recent window.

    Inputs: ``params["column"]`` (or ``metric_col``/``columns[0]``) — required
    numeric column; optional ``date_col``/``keys.date_col``/``split_quantile``
    (see :func:`_date_col`, :func:`_split_by_date`).
    Outputs: early/recent count, mean, median and their deltas.
    Failure modes: ``ValueError`` if the column is missing/not in ``df``, or if
    no date column can be resolved.
    """
    col = _column(params, "column", "metric_col")
    if not col or col not in df.columns:
        raise ValueError("A valid numeric column is required.")
    date_col, cutoff, early_mask, recent_mask = _split_by_date(df, params)
    early = _numeric(df.loc[early_mask, col]).dropna()
    recent = _numeric(df.loc[recent_mask, col]).dropna()
    metrics = {
        "column": col,
        "date_col": date_col,
        "cutoff": cutoff,
        "early_count": len(early),
        "recent_count": len(recent),
        "early_mean": early.mean() if len(early) else None,
        "recent_mean": recent.mean() if len(recent) else None,
        "mean_delta": (recent.mean() - early.mean()) if len(early) and len(recent) else None,
        "early_median": early.median() if len(early) else None,
        "recent_median": recent.median() if len(recent) else None,
        "median_delta": (recent.median() - early.median()) if len(early) and len(recent) else None,
    }
    rows = [
        {"window": "early", "count": metrics["early_count"],
         "mean": metrics["early_mean"], "median": metrics["early_median"]},
        {"window": "recent", "count": metrics["recent_count"],
         "mean": metrics["recent_mean"], "median": metrics["recent_median"]},
    ]
    return _result(
        f"{col} shifted by {metrics['mean_delta']:.4f} in recent records by {date_col}."
        if metrics["mean_delta"] is not None else f"{col} time-window comparison could not compute a delta.",
        metrics,
        rows,
        ["Large mean/median deltas support time-based population drift.",
         "Compare with segment attribution before assigning an upstream owner."],
        ["Run segment_attribution on key categorical fields.",
         "Run psi_ks_decomposition for distribution-range contribution."],
    )


def _psi_ks_decomposition(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Explain distribution movement between early/recent windows bin-by-bin.

    Inputs: ``params["column"]`` (numeric); optional ``date_col``/``keys``/
    ``split_quantile``/``bins`` (default 10).
    Outputs: total PSI, max KS gap, and per-bin PSI/KS contributions (sorted by
    absolute PSI contribution, largest first).
    Failure modes: ``ValueError`` if the column is invalid, no date column
    resolves, either window has fewer than 2 numeric values, or the reference
    window doesn't have enough distinct values to form bins.
    """
    col = _column(params, "column", "metric_col")
    if not col or col not in df.columns:
        raise ValueError("A valid numeric column is required.")
    bins = int(params.get("bins", 10))
    date_col, cutoff, early_mask, recent_mask = _split_by_date(df, params)
    ref = _numeric(df.loc[early_mask, col]).dropna()
    cur = _numeric(df.loc[recent_mask, col]).dropna()
    if len(ref) < 2 or len(cur) < 2:
        raise ValueError("Both windows need at least two numeric values.")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        raise ValueError("Reference distribution does not have enough distinct values.")
    edges[0], edges[-1] = -np.inf, np.inf
    ref_counts = pd.cut(ref, edges, include_lowest=True).value_counts(sort=False)
    cur_counts = pd.cut(cur, edges, include_lowest=True).value_counts(sort=False)
    eps = 1e-6
    ref_pct = ref_counts / max(len(ref), 1)
    cur_pct = cur_counts / max(len(cur), 1)
    rows = []
    psi_total = 0.0
    ks_max = 0.0
    ref_cum = cur_cum = 0.0
    for interval, rp, cp in zip(ref_counts.index.astype(str), ref_pct, cur_pct):
        contrib = float((cp - rp) * np.log((cp + eps) / (rp + eps)))
        psi_total += contrib
        ref_cum += float(rp)
        cur_cum += float(cp)
        ks_gap = abs(cur_cum - ref_cum)
        ks_max = max(ks_max, ks_gap)
        rows.append({"bin": interval, "reference_pct": rp, "current_pct": cp,
                     "psi_contribution": contrib, "ks_gap": ks_gap})
    rows.sort(key=lambda r: abs(r["psi_contribution"]), reverse=True)
    return _result(
        f"{col} PSI={psi_total:.4f}; largest KS gap={ks_max:.4f}.",
        {"column": col, "date_col": date_col, "cutoff": cutoff,
         "psi": psi_total, "ks_gap": ks_max, "bins": len(rows)},
        rows,
        ["Largest PSI contribution bins identify where the distribution moved.",
         "KS gap highlights the strongest cumulative separation point."],
        ["Inspect source-system or policy changes affecting the top shifted ranges.",
         "Run segment_attribution to find which segment drives the shifted bins."],
    )


def _segment_attribution(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Find which categorical segment carries the largest early/recent metric delta.

    Inputs: ``params["column"]`` (numeric); optional ``segment_col`` (else the
    first non-numeric, non-date-like column is used); optional ``date_col``/
    ``keys``/``split_quantile``.
    Outputs: per-segment early/recent count, mean and delta, sorted by
    ``|mean_delta|`` descending.
    Failure modes: ``ValueError`` if the column is invalid, no ``segment_col``
    can be resolved, or the resolved ``segment_col`` is not in ``df``.
    """
    col = _column(params, "column", "metric_col")
    segment_col = params.get("segment_col")
    if not col or col not in df.columns:
        raise ValueError("A valid numeric column is required.")
    if not segment_col:
        candidates = [
            c for c in df.columns
            if c != col and not pd.api.types.is_numeric_dtype(df[c])
            and "date" not in c.lower() and "time" not in c.lower()
        ]
        if not candidates:
            raise ValueError("segment_col is required when no categorical candidate is available.")
        segment_col = candidates[0]
    if segment_col not in df.columns:
        raise ValueError("segment_col is not in the dataframe.")
    _, _, early_mask, recent_mask = _split_by_date(df, params)
    frame = df[[segment_col, col]].copy()
    frame[col] = _numeric(frame[col])
    rows = []
    for value, group in frame.groupby(segment_col, dropna=False):
        early = group.loc[early_mask.reindex(group.index, fill_value=False), col].dropna()
        recent = group.loc[recent_mask.reindex(group.index, fill_value=False), col].dropna()
        if len(early) == 0 and len(recent) == 0:
            continue
        delta = (recent.mean() - early.mean()) if len(early) and len(recent) else None
        rows.append({"segment": str(value), "early_count": len(early), "recent_count": len(recent),
                     "early_mean": early.mean() if len(early) else None,
                     "recent_mean": recent.mean() if len(recent) else None,
                     "mean_delta": delta})
    rows.sort(key=lambda r: abs(r["mean_delta"] or 0), reverse=True)
    return _result(
        f"{segment_col} attribution computed for {col}; top segments show largest mean deltas.",
        {"column": col, "segment_col": segment_col, "segments": len(rows)},
        rows,
        ["A small set of high-delta segments suggests localized upstream or mix-shift drivers."],
        ["Compare top segments with ingestion/source-system changes.",
         "Create follow-up segment-level validation for the top shifted segment."],
    )


def _missingness_analysis(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Compare a column's null-rate between an early and a recent window.

    Inputs: ``params["column"]``; optional ``date_col``/``keys``/``split_quantile``.
    Outputs: early/recent missing rate and their delta.
    Failure modes: ``ValueError`` if the column is invalid or no date column
    can be resolved.
    """
    col = _column(params, "column")
    if not col or col not in df.columns:
        raise ValueError("A valid column is required.")
    date_col, cutoff, early_mask, recent_mask = _split_by_date(df, params)
    early_rate = float(df.loc[early_mask, col].isna().mean())
    recent_rate = float(df.loc[recent_mask, col].isna().mean())
    return _result(
        f"{col} missingness changed by {recent_rate - early_rate:.4f}.",
        {"column": col, "date_col": date_col, "cutoff": cutoff,
         "early_missing_rate": early_rate, "recent_missing_rate": recent_rate,
         "delta": recent_rate - early_rate},
        [{"window": "early", "missing_rate": early_rate},
         {"window": "recent", "missing_rate": recent_rate}],
        ["Missingness drift points to extract, mapping, or source coverage changes."],
        ["Run segment_attribution using a source/product field if available."],
    )


def _outlier_profile(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Profile IQR (1.5x) outliers for a numeric column.

    Inputs: ``params["column"]`` (numeric).
    Outputs: q1/q3/iqr, lower/upper bounds, outlier count and rate.
    Failure modes: ``ValueError`` if the column is missing/not in ``df``.
    """
    col = _column(params, "column")
    if not col or col not in df.columns:
        raise ValueError("A valid numeric column is required.")
    series = _numeric(df[col]).dropna()
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = series[(series < low) | (series > high)]
    return _result(
        f"{col} has {len(outliers)} IQR outliers.",
        {"column": col, "q1": q1, "q3": q3, "iqr": iqr,
         "lower_bound": low, "upper_bound": high,
         "outlier_count": len(outliers), "outlier_rate": len(outliers) / max(len(series), 1)},
        [{"bound": "lower", "value": low}, {"bound": "upper", "value": high}],
        ["Outlier spikes can explain PSI/KS movement without broad population shift."],
        ["Review top/bottom records and source limits for capped or miscoded values."],
    )


def _relationship_drift(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Compare two columns' correlation between an early and a recent window.

    Inputs: ``params["columns"]`` — exactly 2 column names; optional
    ``date_col``/``keys``/``split_quantile``.
    Outputs: early/recent Pearson correlation and their delta.
    Failure modes: ``ValueError`` if fewer than 2 columns are given, either
    column is not in ``df``, or no date column can be resolved.
    """
    columns = params.get("columns") or []
    if len(columns) < 2:
        raise ValueError("Two columns are required.")
    a, b = [str(c).split(".")[-1] for c in columns[:2]]
    if a not in df.columns or b not in df.columns:
        raise ValueError("Both relationship columns must exist.")
    date_col, cutoff, early_mask, recent_mask = _split_by_date(df, params)
    early = df.loc[early_mask, [a, b]].apply(_numeric).dropna()
    recent = df.loc[recent_mask, [a, b]].apply(_numeric).dropna()
    early_corr = early[a].corr(early[b]) if len(early) > 2 else None
    recent_corr = recent[a].corr(recent[b]) if len(recent) > 2 else None
    delta = (recent_corr - early_corr) if early_corr is not None and recent_corr is not None else None
    return _result(
        f"Correlation between {a} and {b} changed by {delta:.4f}."
        if delta is not None else f"Relationship drift for {a}/{b} could not compute correlation delta.",
        {"columns": [a, b], "date_col": date_col, "cutoff": cutoff,
         "early_corr": early_corr, "recent_corr": recent_corr, "corr_delta": delta},
        [{"window": "early", "count": len(early), "corr": early_corr},
         {"window": "recent", "count": len(recent), "corr": recent_corr}],
        ["Relationship drift can indicate changed enrichment logic or population mix."],
        ["Run segment_attribution for one of the relationship variables."],
    )


def _join_reference_integrity(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Profile a key column's nullness and duplication (join/reference health).

    Inputs: ``params["key_col"]`` (or ``column``) — explicit; this module never
    infers a table's primary key.
    Outputs: null_rate, duplicate_rate, distinct_count.
    Failure modes: ``ValueError`` if ``key_col`` is missing/not in ``df``.
    """
    key_col = params.get("key_col") or _column(params, "column")
    if not key_col or key_col not in df.columns:
        raise ValueError("key_col is required.")
    null_rate = float(df[key_col].isna().mean())
    duplicate_rate = float(df[key_col].duplicated(keep=False).mean())
    return _result(
        f"{key_col} null_rate={null_rate:.4f}, duplicate_rate={duplicate_rate:.4f}.",
        {"key_col": key_col, "null_rate": null_rate, "duplicate_rate": duplicate_rate,
         "distinct_count": int(df[key_col].nunique(dropna=True))},
        [{"check": "null_rate", "value": null_rate},
         {"check": "duplicate_rate", "value": duplicate_rate}],
        ["Key nulls or duplicates can cause join loss or relationship inflation."],
        ["Run cross-table reconciliation with the referenced table when available."],
    )


def _duplicate_key_integrity(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Find duplicated values of a key column and the top offending values.

    Inputs: ``params["key_col"]`` (or ``column``) — explicit, never inferred.
    Outputs: count of duplicated distinct values + duplicated row count; the
    top 10 duplicated values by frequency.
    Failure modes: ``ValueError`` if ``key_col`` is missing/not in ``df``.
    """
    key_col = params.get("key_col") or _column(params, "column")
    if not key_col or key_col not in df.columns:
        raise ValueError("key_col is required.")
    counts = df[key_col].value_counts(dropna=False)
    dup = counts[counts > 1].head(10)
    return _result(
        f"{key_col} has {int((counts > 1).sum())} duplicated key values.",
        {"key_col": key_col, "duplicated_values": int((counts > 1).sum()),
         "duplicated_rows": int(df[key_col].duplicated(keep=False).sum())},
        [{"key": str(k), "count": int(v)} for k, v in dup.items()],
        ["Duplicate keys can double-count records and distort validation metrics."],
        ["Validate source primary-key construction and ingestion deduplication."],
    )


def _freshness_gap_check(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Profile date coverage and the largest gaps between distinct dates.

    Inputs: optional ``date_col``/``keys`` (see :func:`_date_col`); falls back
    to the name heuristic when neither is given.
    Outputs: min/max date, distinct date count, max gap in days, top 10 gaps.
    Failure modes: ``ValueError`` if no date column can be resolved.
    """
    date_col = _date_col(params, df)
    if not date_col:
        raise ValueError("date_col is required.")
    dates = pd.to_datetime(df[date_col], errors="coerce").dropna().sort_values()
    gaps = dates.drop_duplicates().diff().dropna()
    top = gaps.sort_values(ascending=False).head(10)
    return _result(
        f"{date_col} ranges from {dates.min()} to {dates.max()} with max gap {top.iloc[0] if len(top) else None}.",
        {"date_col": date_col, "min_date": dates.min(), "max_date": dates.max(),
         "distinct_dates": int(dates.nunique()), "max_gap_days": top.iloc[0].days if len(top) else 0},
        [{"gap_days": gap.days} for gap in top],
        ["Large date gaps indicate missing batches or irregular ingestion cadence."],
        ["Compare row counts before and after the largest gap."],
    )


def _target_leakage_check(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Check a feature's correlation with a target/outcome column (leakage smell).

    Inputs: ``params["target_col"]`` and ``params["column"]``.
    Outputs: Pearson correlation between the two columns.
    Failure modes: ``ValueError`` if either column is missing/not in ``df``.
    """
    target = params.get("target_col")
    col = _column(params, "column")
    if not target or target not in df.columns or not col or col not in df.columns:
        raise ValueError("target_col and column are required.")
    x, y = _numeric(df[col]), _numeric(df[target])
    corr = x.corr(y)
    return _result(
        f"{col}/{target} correlation is {corr:.4f}." if corr is not None else "Correlation could not be computed.",
        {"column": col, "target_col": target, "correlation": corr},
        [{"column": col, "target_col": target, "correlation": corr}],
        ["Very high relationship with target may indicate leakage or post-outcome encoding."],
        ["Check feature availability timing and post-outcome field list."],
    )


def _cross_table_reconciliation(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Build a local key profile for a later cross-table reconciliation pass.

    Inputs: ``params["key_col"]`` (or ``column``) — explicit, never inferred.
    Outputs: row count, distinct key count, null key count (this side only —
    the referenced table's side is profiled separately with the same helper).
    Failure modes: ``ValueError`` if ``key_col`` is missing/not in ``df``.
    """
    key_col = params.get("key_col") or _column(params, "column")
    if not key_col or key_col not in df.columns:
        raise ValueError("key_col is required.")
    return _result(
        f"{key_col} local reconciliation profile created.",
        {"key_col": key_col, "rows": len(df), "distinct_keys": int(df[key_col].nunique(dropna=True)),
         "null_keys": int(df[key_col].isna().sum())},
        [{"check": "rows", "value": len(df)},
         {"check": "distinct_keys", "value": int(df[key_col].nunique(dropna=True))}],
        ["Use this with relation metadata to compare against the referenced table."],
        ["Run join_reference_integrity on both sides of the relationship."],
    )


def _schema_type_anomaly(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Detect a possible storage-type mismatch (e.g. numeric/date stored as text).

    Inputs: ``params["column"]``.
    Outputs: observed dtype, numeric-parse rate, date-parse rate, null rate.
    Failure modes: ``ValueError`` if the column is missing/not in ``df``.
    """
    col = _column(params, "column")
    if not col or col not in df.columns:
        raise ValueError("A valid column is required.")
    series = df[col]
    numeric = pd.to_numeric(series, errors="coerce")
    dates = pd.to_datetime(series, errors="coerce")
    metrics = {
        "column": col,
        "dtype": str(series.dtype),
        "numeric_parse_rate": float(numeric.notna().mean()),
        "date_parse_rate": float(dates.notna().mean()),
        "null_rate": float(series.isna().mean()),
    }
    return _result(
        f"{col} dtype={metrics['dtype']}; numeric_parse_rate={metrics['numeric_parse_rate']:.4f}; date_parse_rate={metrics['date_parse_rate']:.4f}.",
        metrics,
        [{"metric": k, "value": v} for k, v in metrics.items()],
        ["High parse rate with object dtype suggests storage type mismatch."],
        ["Normalize type before quantile, ordering, or relationship tests."],
    )


@dataclass(frozen=True)
class RCAHelper:
    """Rich metadata for one RCA probe (this module's own catalogue row).

    ``ai/test_kit.py`` wraps every entry of :data:`HELPERS` into its own
    lightweight ``Helper`` for the single platform-wide catalogue; the extra
    fields here (``supported_test_types``, ``input_roles``) are RCA-specific
    and stay local to this module.
    """
    helper_id: str
    name: str
    description: str
    supported_test_types: tuple[str, ...]
    input_roles: tuple[str, ...]
    param_schema: dict[str, Any]
    output_schema: dict[str, Any]
    fn: HelperFn

    def public(self) -> dict[str, Any]:
        return {
            "helper_id": self.helper_id,
            "name": self.name,
            "description": self.description,
            "supported_test_types": list(self.supported_test_types),
            "input_roles": list(self.input_roles),
            "param_schema": self.param_schema,
            "output_schema": self.output_schema,
        }


OUTPUT_SCHEMA = {
    "required": ["summary", "metrics", "evidence_rows", "interpretation_hints", "recommended_followups"]
}


HELPERS: dict[str, RCAHelper] = {
    h.helper_id: h for h in [
        RCAHelper("time_window_drift", "Time-window drift",
                  "Compare numeric field behavior between early and recent records.",
                  ("psi", "ks", "trend", "feature_drift"), ("date_col", "numeric_column"),
                  {"required": ["column"], "optional": ["date_col", "split_quantile"]}, OUTPUT_SCHEMA,
                  _time_window_drift),
        RCAHelper("psi_ks_decomposition", "PSI/KS decomposition",
                  "Explain distribution movement by quantile bin contribution.",
                  ("psi", "ks"), ("date_col", "numeric_column"),
                  {"required": ["column"], "optional": ["date_col", "split_quantile", "bins"]}, OUTPUT_SCHEMA,
                  _psi_ks_decomposition),
        RCAHelper("segment_attribution", "Segment attribution",
                  "Find segments with the largest early/recent metric deltas.",
                  ("psi", "ks", "missing", "outlier"), ("date_col", "numeric_column", "segment_col"),
                  {"required": ["column"], "optional": ["segment_col", "date_col", "split_quantile"]}, OUTPUT_SCHEMA,
                  _segment_attribution),
        RCAHelper("missingness_analysis", "Missingness analysis",
                  "Compare missingness between early and recent windows.",
                  ("missing", "psi", "ks"), ("date_col", "column"),
                  {"required": ["column"], "optional": ["date_col", "split_quantile"]}, OUTPUT_SCHEMA,
                  _missingness_analysis),
        RCAHelper("outlier_profile", "Outlier profile",
                  "Profile IQR outliers for a numeric field.",
                  ("outlier", "psi", "ks"), ("numeric_column",),
                  {"required": ["column"]}, OUTPUT_SCHEMA, _outlier_profile),
        RCAHelper("relationship_drift", "Relationship drift",
                  "Compare two-field correlation between early and recent windows.",
                  ("corr_stability", "monotonicity", "feature_drift"), ("date_col", "numeric_pair"),
                  {"required": ["columns"], "optional": ["date_col", "split_quantile"]}, OUTPUT_SCHEMA,
                  _relationship_drift),
        RCAHelper("join_reference_integrity", "Join/reference integrity",
                  "Profile key nullness and duplication for join-related failures.",
                  ("referential_integrity", "join", "relationship"), ("key_col",),
                  {"required": ["key_col"]}, OUTPUT_SCHEMA, _join_reference_integrity),
        RCAHelper("duplicate_key_integrity", "Duplicate/key integrity",
                  "Find duplicated key values and affected rows.",
                  ("duplicate", "uniqueness", "primary_key"), ("key_col",),
                  {"required": ["key_col"]}, OUTPUT_SCHEMA, _duplicate_key_integrity),
        RCAHelper("freshness_gap_check", "Freshness/gap check",
                  "Profile date coverage and largest gaps.",
                  ("freshness", "trend", "row_count"), ("date_col",),
                  {"required": [], "optional": ["date_col"]}, OUTPUT_SCHEMA, _freshness_gap_check),
        RCAHelper("target_leakage_check", "Target leakage/post-outcome check",
                  "Check relationship between a field and target column.",
                  ("leakage", "target", "feature_drift"), ("column", "target_col"),
                  {"required": ["column", "target_col"]}, OUTPUT_SCHEMA, _target_leakage_check),
        RCAHelper("cross_table_reconciliation", "Cross-table reconciliation",
                  "Create a local reconciliation profile for relation checks.",
                  ("reconciliation", "referential_integrity", "join"), ("key_col",),
                  {"required": ["key_col"]}, OUTPUT_SCHEMA, _cross_table_reconciliation),
        RCAHelper("schema_type_anomaly", "Schema/type anomaly",
                  "Detect parse/type mismatches that can break RCA probes.",
                  ("schema", "type", "psi", "ks"), ("column",),
                  {"required": ["column"]}, OUTPUT_SCHEMA, _schema_type_anomaly),
    ]
}


def list_helpers(failed_test: dict | None = None, table: str | None = None,
                 columns: list[str] | None = None) -> list[dict[str, Any]]:
    """This module's own descriptive listing (RCA-specific applicability
    metadata for a UI) — distinct from ``ai.test_kit.list_helpers``, the
    platform-wide catalogue that federates this dict alongside gx metrics and
    tool_registry tools."""
    test_id = str((failed_test or {}).get("test_id")
                  or (failed_test or {}).get("id") or "").lower()
    selected = {str(c).split(".")[-1] for c in (columns or []) if c}
    out = []
    for helper in HELPERS.values():
        test_match = not test_id or any(t in test_id for t in helper.supported_test_types)
        if not test_match and helper.helper_id not in {"schema_type_anomaly", "freshness_gap_check"}:
            continue
        row = helper.public()
        row["applicability"] = {
            "test_match": test_match,
            "selected_columns": sorted(selected),
            "table": table,
        }
        out.append(row)
    return out


def run_helper(helper_id: str, df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Look up ``helper_id`` in :data:`HELPERS` and run it against ``df``.

    Returns ``{"helper": <public metadata>, "result": <helper output>}``.
    Raises ``KeyError`` for an unknown id; propagates any ``ValueError`` the
    helper itself raises for invalid params.
    """
    helper = HELPERS.get(helper_id)
    if not helper:
        raise KeyError(f"Unknown RCA helper: {helper_id}")
    result = helper.fn(df, dict(params or {}))
    _validate_helper_output(result)
    return {"helper": helper.public(), "result": result}


def _validate_helper_output(result: dict[str, Any]) -> None:
    """Raise ``ValueError`` if ``result`` is missing a required output key or
    is not JSON-serializable."""
    missing = [k for k in OUTPUT_SCHEMA["required"] if k not in result]
    if missing:
        raise ValueError(f"Helper output missing keys: {missing}")
    json.dumps(result, default=str)


def validate_generated_helper(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate generated helper code without promoting it to the registry."""
    code = str(spec.get("python_code") or "")
    examples = spec.get("parameter_examples") or []
    errors: list[str] = []
    if len(examples) < 3:
        errors.append("At least 3 parameter_examples are required.")
    required = {"helper_id", "name", "python_code", "input_schema", "output_schema"}
    missing = sorted(k for k in required if not spec.get(k))
    if missing:
        errors.append(f"Missing required spec keys: {missing}.")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        errors.append(f"SyntaxError: {exc.msg} at line {exc.lineno}.")
        tree = None
    if tree is not None:
        blocked = {"os", "sys", "subprocess", "pathlib", "socket", "pickle", "open"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for name in names:
                    if (name or "").split(".")[0] in blocked:
                        errors.append(f"Blocked import: {name}.")
            if isinstance(node, ast.Name) and node.id in {"open", "eval", "exec", "__import__"}:
                errors.append(f"Blocked name: {node.id}.")
    if not errors:
        from ai import code_sandbox
        sample = pd.DataFrame({
            "a": [1.0, 2.0, 3.0, 4.0, 5.0],
            "b": [5.0, 4.0, 3.0, 2.0, 1.0],
            "date": pd.date_range("2020-01-01", periods=5),
            "segment": ["x", "x", "y", "y", "y"],
            "target": [0, 0, 1, 1, 0],
        })
        harness = (
            f"{code}\n\n"
            "try:\n"
            "    result = run_helper(df, params)\n"
            "except Exception:\n"
            "    try:\n"
            "        result = run(df, params)\n"
            "    except Exception as err:\n"
            "        result = {'error': 'Generated helper must define and run run_helper(df, params) or run(df, params).', 'detail': str(err)}\n"
        )
        for i, params in enumerate(examples[:3], start=1):
            out = code_sandbox.run(harness, sample, {"params": dict(params or {})})
            if not out.get("ok"):
                errors.append(f"parameter_examples[{i}] failed: {out.get('error')}.")
                continue
            result_obj = out.get("result_obj")
            if not isinstance(result_obj, dict):
                errors.append(f"parameter_examples[{i}] did not return a dict.")
                continue
            missing_output = [k for k in OUTPUT_SCHEMA["required"] if k not in result_obj]
            if missing_output:
                errors.append(f"parameter_examples[{i}] output missing keys: {missing_output}.")
    status = "valid" if not errors else "rejected"
    return {
        "status": status,
        "promoted": False,
        "errors": errors,
        "candidate": {
            "helper_id": spec.get("helper_id"),
            "name": spec.get("name"),
            "input_schema": spec.get("input_schema"),
            "output_schema": spec.get("output_schema"),
            "parameter_examples": examples[:5],
        },
    }
