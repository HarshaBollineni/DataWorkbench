"""Pure analytics for DQ Studio custom expectations.

All heavy statistical logic lives here as dependency-light pandas/numpy
functions so it can be unit-tested and calibrated WITHOUT Great Expectations.
The GX custom-expectation classes are thin wrappers that call these.

Calibrated 2026-06-24 against portfolio_alt_master.db. Locked thresholds and
windowing are documented per function; do not change them without re-calibrating
against the planted-issue answer key (_planted_issues).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---- Locked defaults (calibrated against portfolio_alt_master.db) -----------
PSI_REF_FRAC = 0.4          # reference = earliest 40% by date_col; current = rest
PSI_BINS = 10               # quantile bins from the reference distribution
PSI_EPS = 1e-6
PSI_THRESHOLD = 0.20        # FAIL if PSI > 0.20 (re-calibrated on RAW tables, Plan 3 F3:
                            #   I1 bureau_score=0.287 FAIL, I3 amount=0.205 FAIL,
                            #   clean obligors.leverage=0.016 PASS)
MISSINGNESS_TOL = 0.01      # FAIL a missingness test if null-rate in scope > 1%
KS_THRESHOLD = 0.20         # FAIL if max-segment KS > 0.20
REGIME_MIN_COVERAGE = 0.05  # require >=5% downturn rows WHEN window overlaps downturn
VINTAGE_MIN_MONTHS = 24     # require >=24 months observation depth
TARGET_RATE_MAX_RANGE = 0.15  # FAIL if quarterly target-rate range > 0.15
REL_MIN_STRENGTH = 0.10     # |Spearman rho| must clear this to confirm a relationship


def _sorted_by_date(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    return df.sort_values(date_col, kind="mergesort").reset_index(drop=True)


def compute_psi(
    df: pd.DataFrame,
    feature: str,
    date_col: str,
    ref_frac: float = PSI_REF_FRAC,
    bins: int = PSI_BINS,
    eps: float = PSI_EPS,
) -> float:
    """Population Stability Index of ``feature`` between an early reference
    vintage (earliest ``ref_frac`` of rows by ``date_col``) and the current
    window (the remaining rows). Bins are reference quantiles.

    Calibrated: decisioning/bureau_score -> 0.287 (FAIL); irb/leverage -> 0.015 (PASS).
    """
    d = _sorted_by_date(df[[feature, date_col]].dropna(subset=[feature]), date_col)
    n = len(d)
    cut = int(n * ref_frac)
    ref = d[feature].iloc[:cut].to_numpy()
    cur = d[feature].iloc[cut:].to_numpy()
    if len(ref) == 0 or len(cur) == 0:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    u = np.histogram(cur, edges)[0] / len(cur)
    r = np.clip(r, eps, None)
    u = np.clip(u, eps, None)
    return float(np.sum((u - r) * np.log(u / r)))


def _ks_2samp(a: np.ndarray, b: np.ndarray) -> float:
    a = np.sort(np.asarray(a, dtype=float))
    b = np.sort(np.asarray(b, dtype=float))
    if len(a) == 0 or len(b) == 0:
        return 0.0
    allv = np.concatenate([a, b])
    cdfa = np.searchsorted(a, allv, side="right") / len(a)
    cdfb = np.searchsorted(b, allv, side="right") / len(b)
    return float(np.max(np.abs(cdfa - cdfb)))


def compute_ks_by_segment(df: pd.DataFrame, feature: str, segment_col: str) -> dict:
    """Max KS statistic of ``feature`` between the largest segment (baseline)
    and every other segment of ``segment_col``. Representativeness proxy.

    Calibrated: irb/leverage by sector -> 0.068 (PASS at 0.20).
    """
    sub = df[[feature, segment_col]].dropna()
    if sub.empty or sub[segment_col].nunique() < 2:
        return {"ks": 0.0, "segment_col": segment_col, "baseline": None}
    counts = sub[segment_col].value_counts()
    base_name = counts.index[0]
    base = sub.loc[sub[segment_col] == base_name, feature].to_numpy()
    ks = 0.0
    worst = None
    for name in counts.index[1:]:
        other = sub.loc[sub[segment_col] == name, feature].to_numpy()
        k = _ks_2samp(base, other)
        if k > ks:
            ks, worst = k, name
    return {"ks": ks, "segment_col": segment_col, "baseline": base_name, "worst": worst}


def missingness_rate(
    df: pd.DataFrame,
    column: str,
    segment_col: str | None = None,
    segment_value: str | None = None,
    date_col: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
) -> dict:
    """Null-rate of ``column``, optionally restricted to a segment and/or a
    date window (ISO 'YYYY-MM' bounds, inclusive). Powers the missingness-
    mechanism test.

    Calibrated: retail_accounts.balance overall -> 0.069 (FAIL at 0.01);
    segment channel=Online, 2018-06..2018-12 -> 1.000 (FAIL, I2);
    a clean column returns ~0.0 (PASS).
    """
    sub = df
    if segment_col and segment_value is not None and segment_col in sub.columns:
        sub = sub[sub[segment_col] == segment_value]
    if date_col and date_col in sub.columns and (date_start or date_end):
        months = pd.to_datetime(sub[date_col]).dt.strftime("%Y-%m")
        lo = date_start or "0000-00"
        hi = date_end or "9999-99"
        sub = sub[months.between(lo, hi)]
    n = len(sub)
    rate = float(sub[column].isna().mean()) if n else 0.0
    return {"rate": rate, "n": n}


def detect_leakage(df: pd.DataFrame, post_outcome_cols: list[str]) -> dict:
    """A column leaks if it is present AND not entirely null.

    Calibrated: decisioning [collections_contact_flag] -> leaked=True (FAIL, I4);
    irb [] -> leaked=False (PASS).
    """
    present = [c for c in post_outcome_cols if c in df.columns and df[c].notna().any()]
    return {"leaked": len(present) > 0, "leaked_columns": present}


def downturn_regime_coverage(
    df: pd.DataFrame,
    date_col: str,
    downturn_start: str,
    downturn_end: str,
) -> dict:
    """Fraction of rows whose ``date_col`` month falls in the downturn window.

    ``applicable`` is False when the dataset's observed date range does not
    overlap the downturn window at all (e.g. irb is 2021-2023, downturn is
    2020) — in that case the expectation passes vacuously (N/A).

    Calibrated: decisioning -> coverage 0.427 applicable=True (PASS);
    irb -> applicable=False (vacuous PASS).
    """
    months = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m")
    dmin, dmax = months.min(), months.max()
    applicable = not (dmax < downturn_start or dmin > downturn_end)
    in_dt = months.between(downturn_start, downturn_end)
    coverage = float(in_dt.mean()) if len(months) else 0.0
    return {"applicable": applicable, "coverage": coverage}


def observation_window_months(df: pd.DataFrame, date_col: str) -> int:
    """Inclusive span in whole months between min and max ``date_col``.

    Calibrated: decisioning -> 48; irb -> 36 (both PASS at 24).
    """
    dt = pd.to_datetime(df[date_col]).dropna()
    if dt.empty:
        return 0
    lo, hi = dt.min(), dt.max()
    return (hi.year - lo.year) * 12 + (hi.month - lo.month)


def target_rate_quarterly_range(df: pd.DataFrame, target_col: str, date_col: str) -> dict:
    """Range (max-min) of the mean target rate across calendar quarters.

    Calibrated: irb/default_flag -> range 0.044 (PASS at 0.15).
    """
    sub = df[[target_col, date_col]].dropna()
    q = pd.PeriodIndex(pd.to_datetime(sub[date_col]), freq="Q")
    rates = sub.groupby(q)[target_col].mean()
    if rates.empty:
        return {"range": 0.0, "std": 0.0}
    return {"range": float(rates.max() - rates.min()), "std": float(rates.std(ddof=0))}


def conditional_spearman(df: pd.DataFrame, col_a: str, col_b: str) -> float:
    """Spearman rank correlation between two columns (pandas, no scipy)."""
    sub = df[[col_a, col_b]].dropna()
    if len(sub) < 3:
        return 0.0
    return float(sub[col_a].rank().corr(sub[col_b].rank()))


def relationship_holds(rho: float, relationship: str, min_strength: float = REL_MIN_STRENGTH) -> bool:
    """Whether a Spearman ``rho`` confirms the expected ``relationship``.

    Calibrated: rating_num~interest_coverage rho=-0.318 confirms 'inverse';
    rating_num~leverage rho=+0.435 confirms 'monotone_increasing'.
    """
    rel = relationship.lower()
    if rel in {"inverse", "monotone_decreasing"}:
        return rho <= -min_strength
    if rel == "monotone_increasing":
        return rho >= min_strength
    raise ValueError(f"Unknown relationship: {relationship}")
