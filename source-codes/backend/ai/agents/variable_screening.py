"""Column profiling utility (no LLM).

Pure pandas profiling: per-column dtype / describe / info / null-rate /
cardinality. Returns json the downstream LLM agents reason over.

NB (2026-07-01): the 'Fisher' agent that this once backed has been RETIRED —
Newton's DiscoveryState owns profiling in the main flow. This module survives
only as a plain profiling utility: the DataSourcing browse-step preview
(GET /ingestion/{db}/profile) uses it before a DiscoveryState exists, and the
legacy test-generation loop uses it as a no-DiscoveryState fallback.
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
import subprocess

import numpy as np
import pandas as pd


_CHILD_SCRIPT = """
import sys, json, io
import pandas as pd

path, fmt = sys.argv[1], sys.argv[2]
df = pd.read_parquet(path) if fmt == "parquet" else pd.read_csv(path)

buf = io.StringIO()
df.info(buf=buf)

out = {
    "describe": df.describe(include="all").fillna("").astype(str).to_dict(),
    "info": buf.getvalue(),
}
print(json.dumps(out))
"""


def _screen_inprocess(df: pd.DataFrame) -> dict:
    out: dict[str, dict] = {}
    n = len(df)
    for col in df.columns:
        s = df[col]
        info = {
            "dtype": str(s.dtype),
            "null_rate": round(float(s.isna().mean()), 4),
            "cardinality": int(s.nunique(dropna=True)),
            "non_null": int(s.notna().sum()),
        }
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            d = s.describe()
            info["describe"] = {k: (None if pd.isna(v) else round(float(v), 4))
                                for k, v in d.items()}
        else:
            top = s.dropna().astype(str).value_counts().head(5)
            info["top_values"] = {str(k): int(v) for k, v in top.items()}
        out[col] = info
    return {"row_count": n, "col_count": len(df.columns), "per_column": out}


def screen(df: pd.DataFrame) -> dict:
    base = _screen_inprocess(df)

    fmt = "parquet"
    path = os.path.join(tempfile.gettempdir(),
                        f"profile_screen_{os.getpid()}.parquet")
    try:
        try:
            df.to_parquet(path)
        except Exception:
            fmt = "csv"
            path = os.path.join(tempfile.gettempdir(),
                                f"profile_screen_{os.getpid()}.csv")
            df.to_csv(path, index=False)

        proc = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT, path, fmt],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError(proc.stderr or "child process produced no output")

        child = json.loads(proc.stdout)
        base["describe"] = child["describe"]
        base["info"] = child["info"]
        base["subprocess"] = {
            "ok": True,
            "cmd": "python -c (df.describe/df.info)",
        }
    except Exception as err:
        base["subprocess"] = {"ok": False, "error": str(err)}
    finally:
        if os.path.exists(path):
            os.remove(path)

    return base


def _json_safe(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return o


def screen_from_discovery_state(table_name: str, discovery_state) -> dict | None:
    """Return Fisher-format screen() output from Newton's DiscoveryState.

    Gauss can call this before screen(df) to skip the DataFrame load when
    Newton's db_discovery_{logical_db}.json is already on disk.
    Returns None if ``table_name`` is not found in ``discovery_state``.
    """
    from ..discovery_state import from_discovery_state_for_fisher
    return from_discovery_state_for_fisher(discovery_state, table_name)
