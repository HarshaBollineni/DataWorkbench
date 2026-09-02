"""
guards.py - KB-driven guard executor.

The guard *logic* lives here once, as small reusable primitives. The KB decides
*which* guards apply, in what order, and with what thresholds, via a machine-
readable "guard_config" block. The executor reads that block and runs the named
primitives, so adding a guard or retuning a threshold is a KB edit - not a code
change - and the KB and the engine can no longer drift apart.

A primitive takes (derived, params, config) and returns (ok, note):
  ok    False means this is a pre-grain guard that BLOCKS the whole KB.
  note  a human-readable line to show, or None. A note is shown whether or not
        the guard blocks.

guard_config shape (per KB):
  "guard_config": {
    "shared": [
      {"code": "CG1 PRE-GRAIN", "primitive": "period_required",
       "params": {"message": "..."}},
      {"code": "CG3 granularity", "primitive": "granularity",
       "params": {"threshold": 0.8}},
      ...
    ]
  }
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuardResult:
    may_proceed: bool
    notes: list[str] = field(default_factory=list)
    block_reason: str | None = None


# ----------------------------------------------------------------------------
# PRIMITIVES  (registered by name; referenced from the KB's guard_config)
# ----------------------------------------------------------------------------

def _period_required(d, params, cfg):
    """Grid-grained KBs need a period role before any expected count can be built."""
    if not d.col("period"):
        return False, params.get("message",
                                 "no period role bound; expected counts cannot be built")
    return True, None


def _case_required(d, params, cfg):
    """Case-grained KBs need the roles that form case identity (e.g. facility id + default date)."""
    missing = [r for r in params.get("roles", []) if not d.col(r)]
    if missing:
        labels = ", ".join(missing)
        return False, params.get("message",
                                 f"case grain needs {labels}; no case can be formed"
                                 ).replace("{missing}", labels)
    return True, None


def _granularity(d, params, cfg):
    """Only meaningful once a period exists; blocks when the panel spacing is irregular."""
    if not d.col("period"):
        return True, None
    threshold = params.get("threshold", 0.80)
    if not d.granularity.applicable:
        return False, (f"granularity IRREGULAR (modal gap holds for only "
                       f"{d.granularity.share:.0%} of gaps, threshold {threshold:.0%}); "
                       f"expected counts cannot be built")
    return True, (f"granularity {d.granularity.label} "
                  f"({d.granularity.share:.0%} of gaps agree)")


def _as_of_sanity(d, params, cfg):
    """Warn (never block) if the as-of date sits oddly relative to the last period."""
    if d.as_of is None or "_period_ts" not in d.df:
        return True, None
    last = d.df["_period_ts"].max()
    gap = (last.year - d.as_of.year) * 12 + (last.month - d.as_of.month)
    step = d.granularity.months or 1
    if gap > 0:
        return True, (f"as-of {d.as_of:%Y-%m} precedes the last period {last:%Y-%m} "
                      f"(warn only, not blocking)")
    if -gap > step:
        return True, (f"as-of {d.as_of:%Y-%m} follows the last period by more than one "
                      f"period (warn only, not blocking)")
    return True, None


def _runoff_convention(d, params, cfg):
    """Warn when the run-off convention is undeclared; downstream results are boundary-approximate."""
    if cfg.get("runoff_convention") is None:
        return True, params.get("message",
                                "run-off convention undeclared; window_end falls back to each "
                                "facility's last row and right-edge truncation is undetectable; "
                                "results are boundary-approximate")
    return True, None


REGISTRY = {
    "period_required": _period_required,
    "case_required": _case_required,
    "granularity": _granularity,
    "as_of_sanity": _as_of_sanity,
    "runoff_convention": _runoff_convention,
}


# ----------------------------------------------------------------------------
# EXECUTOR
# ----------------------------------------------------------------------------

def run_shared_guards(kb: dict, d, cfg: dict) -> GuardResult:
    """
    Run the KB's declared shared guards in order. The first blocking guard stops
    the KB and its message becomes the NOT-APPLICABLE reason for every rule.
    A KB with no guard_config proceeds with no guards (nothing to enforce).
    """
    specs = kb.get("guard_config", {}).get("shared", [])
    notes: list[str] = []
    for spec in specs:
        code = spec.get("code", spec.get("primitive", "GUARD"))
        prim = REGISTRY.get(spec.get("primitive"))
        if prim is None:
            notes.append(f"{code}: no primitive registered ('{spec.get('primitive')}'), skipped")
            continue
        ok, note = prim(d, spec.get("params", {}), cfg)
        if note:
            notes.append(f"{code}: {note}")
        if not ok:
            return GuardResult(False, notes, f"{code}: {note}")
    return GuardResult(True, notes)