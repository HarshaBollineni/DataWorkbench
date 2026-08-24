"""Deterministic, spike-only data and contracts for retired parity scripts.

These frames intentionally model the former planted-truth oracle without
depending on a deleted development database or any production upload state.
They are not imported by application code.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


_CONTRACTS = {
    "decisioning": {
        "table": "retail_accounts",
        "post_outcome_cols": ["collections_contact_flag"],
    },
    "wholesale_irb": {
        "table": "obligors",
        "post_outcome_cols": [],
    },
}


# This is the former seeded PSI library rule expressed as a self-contained
# sandbox payload.  The spike deliberately exercises the same sandbox path,
# but cannot import the deleted production seed module.
PSI_LIBRARY_CODE = """
feature = columns[0]
date_col = params["date_col"]
threshold = params["threshold"]
d = df[[feature, date_col]].dropna(subset=[feature]).sort_values(date_col, kind="mergesort").reset_index(drop=True)
cut = int(len(d) * 0.4)
ref = d[feature].iloc[:cut].to_numpy()
cur = d[feature].iloc[cut:].to_numpy()
if len(ref) == 0 or len(cur) == 0:
    psi = 0.0
else:
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, 11)))
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.clip(np.histogram(ref, edges)[0] / len(ref), 1e-6, None)
    u = np.clip(np.histogram(cur, edges)[0] / len(cur), 1e-6, None)
    psi = float(np.sum((u - r) * np.log(u / r)))
result = {"metric": psi, "threshold": threshold, "passed": psi <= threshold}
"""


def get_contract(name: str) -> dict:
    """Return a copy so a spike cannot contaminate a later spike's contract."""
    try:
        contract = _CONTRACTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown spike contract: {name}") from exc
    return {key: list(value) if isinstance(value, list) else value
            for key, value in contract.items()}


def load_table(name: str) -> pd.DataFrame:
    """Return a fresh deterministic raw-table frame for a named spike target."""
    tables = {
        "retail_accounts": _retail_accounts,
        "transactions": _transactions,
        "obligors": _obligors,
    }
    try:
        return tables[name]()
    except KeyError as exc:
        raise KeyError(f"Unknown spike table: {name}") from exc


def _shifted_values(size: int) -> np.ndarray:
    """A stable early population followed by an intentionally shifted one."""
    cut = int(size * 0.4)
    return np.concatenate((np.linspace(500.0, 600.0, cut),
                           np.linspace(800.0, 900.0, size - cut)))


def _retail_accounts() -> pd.DataFrame:
    dates = pd.date_range("2018-01-01", periods=120, freq="MS")
    online_h2_2018 = ((dates.year == 2018) & (dates.month >= 6) & (dates.month <= 12))
    return pd.DataFrame({
        "account_id": [f"acct-{i:04d}" for i in range(len(dates))],
        "open_date": dates,
        "bureau_score": _shifted_values(len(dates)),
        "channel": np.where(online_h2_2018, "Online", "Branch"),
        "balance": np.where(online_h2_2018, np.nan, 1000.0),
        "collections_contact_flag": np.where(np.arange(len(dates)) % 2, 1, 0),
    })


def _transactions() -> pd.DataFrame:
    dates = pd.date_range("2018-01-01", periods=120, freq="MS")
    return pd.DataFrame({
        "transaction_id": [f"txn-{i:04d}" for i in range(len(dates))],
        "txn_datetime": dates,
        "amount": _shifted_values(len(dates)),
    })


def _obligors() -> pd.DataFrame:
    # 100 rows makes the PSI 40/60 split contain whole copies of this ten-row
    # pattern.  Each sector receives the same leverage distribution.
    pattern = np.repeat(np.arange(1.0, 6.0), 2)
    leverage = np.tile(pattern, 10)
    dates = pd.date_range("2021-01-01", "2023-12-01", periods=len(leverage))
    return pd.DataFrame({
        "obligor_id": [f"obl-{i:04d}" for i in range(len(leverage))],
        "reporting_date": dates,
        "leverage": leverage,
        "sector": np.tile(["corporate", "retail"], len(leverage) // 2),
        "default_flag": np.zeros(len(leverage), dtype=int),
        "rating_num": leverage,
        "interest_coverage": 6.0 - leverage,
    })
