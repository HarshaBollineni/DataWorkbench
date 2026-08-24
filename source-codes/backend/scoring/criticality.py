"""Plan 3 / F4 — deterministic criticality assignment.

The ONLY AI input is the contextual Importance *ranking* (most->least important
test id). All arithmetic here is pure Python.

Rules (per assessment scope = a table's selected tests; T = count):
- T == 1  -> that test is High.
- Else assign top->High(5), next->Medium(3), rest->Low(1) in ranked order,
  subject to the hard constraint  count(High)+count(Medium) <= floor(T/2)
  (T=6 -> 3 ; T=5 -> 2). HITL edits are re-validated against the same constraint.
"""
from __future__ import annotations

import math

_LEVEL = {"High": 5, "Medium": 3, "Low": 1}


def floor_split(t: int) -> int:
    """Max number of High+Medium tests allowed for T tests."""
    return max(1, t // 2) if t > 1 else t


def assign(ranking: list[str]) -> dict[str, str]:
    """Map ranked test ids (most->least important) to criticality levels."""
    t = len(ranking)
    if t == 0:
        return {}
    if t == 1:
        return {ranking[0]: "High"}

    cap = floor_split(t)                  # how many may be High or Medium
    n_high = math.ceil(cap / 2)           # split the cap: more High than Medium
    n_med = cap - n_high
    out: dict[str, str] = {}
    for i, tid in enumerate(ranking):
        if i < n_high:
            out[tid] = "High"
        elif i < n_high + n_med:
            out[tid] = "Medium"
        else:
            out[tid] = "Low"
    return out


def framework(t: int) -> dict:
    """Expose the exact deterministic allocation for audit/UI explanation."""
    cap = floor_split(t)
    n_high = math.ceil(cap / 2) if t > 1 else t
    n_medium = cap - n_high if t > 1 else 0
    return {
        "total": t,
        "high": n_high,
        "medium": n_medium,
        "low": max(0, t - n_high - n_medium),
        "cap": cap,
    }


def validate(assignment: dict[str, str]) -> dict:
    """Re-check the floor constraint after a HITL edit. Returns {ok, ...}."""
    t = len(assignment)
    cap = floor_split(t)
    hm = sum(1 for v in assignment.values() if v in ("High", "Medium"))
    return {
        "ok": hm <= cap,
        "high_plus_medium": hm,
        "cap": cap,
        "warning": None if hm <= cap else
        f"{hm} High+Medium tests exceed the cap of {cap} for {t} tests.",
    }
