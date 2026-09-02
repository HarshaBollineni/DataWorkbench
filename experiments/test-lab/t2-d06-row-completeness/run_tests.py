"""
run_tests.py - executes the rules of both KBs against the dataset.

Reads binding.json (written by bind_cli.py), runs the shared guards, then each
rule, and prints one verdict per rule.

Usage
-----
    python run_tests.py --folder .
    python run_tests.py --folder . --product CRE
    python run_tests.py --folder . --json findings.json

Two things this file deliberately does NOT do:
  - it never substitutes a default for a missing floor or tolerance. Guard CG4 /
    G8 say to compute the measure and return NO-VERDICT instead.
  - it never reports NOT-APPLICABLE for a rule the engine simply hasn't
    implemented. That is NOT-IMPLEMENTED, and it is an engine gap, not a
    statement about the data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# See README.md: the product interpreter is intentionally isolated, so this
# standalone entry point declares its own sibling-module root.
_EXPERIMENT_ROOT = str(Path(__file__).resolve().parent)
if _EXPERIMENT_ROOT not in sys.path:
    sys.path.insert(0, _EXPERIMENT_ROOT)

import derivation as D
import guards as G
from derivation import Finding, Verdict

# ----------------------------------------------------------------------------
# CONFIG - values the KBs declare, None where the KB says "unsupplied, no
# default proposed". None always routes to NO-VERDICT.
# ----------------------------------------------------------------------------
CONFIG = {
    "portfolio_coverage_floor": 0.99,          # CMG-01
    "cell_coverage_floor": 0.95,               # CMU-01
    "peer_tolerance": 0.02,                    # CMU-01, 2 percentage points
    "minimum_cell_facilities": None,           # CMU-01, unsupplied
    "churn_tolerance": None,                   # C-E, unsupplied
    "runoff_convention": None,                 # CMG-01, undeclared -> guard CG5
    "horizon_months": {"CRE": 96, "MORTGAGE": 60},
    "resolution_floor": {"CRE": 0.80, "MORTGAGE": 0.90},
    "minimum_cases_per_vintage": 20,
    "open_share_tolerance": 0.10,              # RRU-02, KB default
    "mix_tolerance": None,                     # R-A, unsupplied
    "overage_multiple": None,                  # R-B, unsupplied
    "spike_tolerance": None,                   # R-C, unsupplied
    "shift_tolerance_months": None,            # R-D, unsupplied
    "minimum_cell_cases": None,                # R-E, unsupplied
    "redefault_window_months": None,           # R-F, unsupplied
    "redefault_tolerance": None,               # R-F, unsupplied
    "seasoning_expectation_months": None,      # R-H, unsupplied
    "observed_share_floor": None,              # R-H, unsupplied
    "downturn_periods": [],                    # R-G, from the external workflow
}


def pct(x) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:.4f}"


# ----------------------------------------------------------------------------
# SHARED GUARDS
# ----------------------------------------------------------------------------
# The shared pre-grain guards are no longer hardcoded here. Each KB declares
# which guards apply, in what order, and with what thresholds, in its
# "guard_config" block; guards.run_shared_guards() executes them. This keeps the
# rulebook the single source of truth and lets a grid-grained KB require a period
# while a case-grained KB requires only case identity.


# ----------------------------------------------------------------------------
# COMPLETENESS RULES
# ----------------------------------------------------------------------------

def cmg_01(d: D.Derived) -> Finding:
    """Every facility has exactly one row per period in its expected window."""
    if not d.has("facility_id"):
        return Finding("CMG-01", "completeness", Verdict.NOT_APPLICABLE,
                       detail="facility id unbound")
    g = D.expected_grid(d, runoff=CONFIG["runoff_convention"] or "last_row")
    floor = CONFIG["portfolio_coverage_floor"]
    coverage = g.observed.sum() / g.expected.sum() if g.expected.sum() else None

    detail = (f"{len(g):,} facilities; interior gaps on {int((g.interior_gaps > 0).sum()):,}; "
              f"origination-after-first-row contradictions (CG7) "
              f"{int(g.orig_after_first.sum()):,}; boundary-approximate")
    if not d.has("origination_date"):
        detail += "; CG6: origination absent, left-edge check NOT-APPLICABLE"

    if floor is D.UNSUPPLIED:
        return Finding("CMG-01", "completeness", Verdict.NO_VERDICT, pct(coverage), detail)
    v = Verdict.PASS if coverage >= floor else Verdict.FAIL
    return Finding("CMG-01", "completeness", v,
                   f"coverage {pct(coverage)} vs floor {floor}", detail)


def cmg_02(d: D.Derived) -> Finding:
    """No facility-period pair appears more than once."""
    if not d.has("facility_id"):
        return Finding("CMG-02", "completeness", Verdict.NOT_APPLICABLE,
                       detail="facility id unbound")
    sizes = d.df.groupby([d.col("facility_id"), "_pidx"]).size()
    dups = int((sizes > 1).sum())
    v = Verdict.PASS if dups == 0 else Verdict.FAIL
    return Finding("CMG-02", "completeness", v, f"{dups:,} duplicated pairs",
                   f"max rows in one pair {int(sizes.max())}")


def cmu_01(d: D.Derived) -> Finding:
    """Per partition and period: coverage vs the peer median, and vs the absolute cell floor."""
    if not d.has("facility_id", "segment"):
        return Finding("CMU-01", "completeness", Verdict.NOT_APPLICABLE,
                       detail="facility id or segment unbound")
    if CONFIG["minimum_cell_facilities"] is D.UNSUPPLIED:
        return Finding("CMU-01", "completeness", Verdict.NO_VERDICT,
                       detail="minimum facilities per cell unsupplied and no default proposed; "
                              "cells cannot be screened for thinness (guard CG4)")

    seg, fid = d.col("segment"), d.col("facility_id")
    live = D.expected_grid(d)  # per-facility windows
    obs = d.df.groupby([seg, "_pidx"])[fid].nunique().rename("present")
    denom = d.df.groupby(seg)[fid].nunique().rename("total")
    cell = obs.reset_index().merge(denom, on=seg)
    cell["coverage"] = cell.present / cell.total
    cell = cell[cell.present >= CONFIG["minimum_cell_facilities"]]

    med = cell.groupby("_pidx").coverage.median().rename("peer_median")
    cell = cell.merge(med, on="_pidx")
    below_floor = cell[cell.coverage < CONFIG["cell_coverage_floor"]]
    below_peer = cell[cell.coverage < cell.peer_median - CONFIG["peer_tolerance"]]

    n = len(below_floor.index.union(below_peer.index))
    v = Verdict.PASS if n == 0 else Verdict.FAIL
    return Finding("CMU-01", "completeness", v, f"{n:,} failing cells of {len(cell):,}",
                   f"{len(below_floor):,} below the {CONFIG['cell_coverage_floor']} cell floor; "
                   f"{len(below_peer):,} below peer median less {CONFIG['peer_tolerance']}")


def c_c(d: D.Derived) -> Finding:
    """No null, blank, whitespace or case variant facility identifiers."""
    if not d.has("facility_id"):
        return Finding("C-C", "completeness", Verdict.NOT_APPLICABLE,
                       detail="facility id unbound")
    s = d.s("facility_id")
    blank = int(s.isna().sum() + s.astype(str).str.strip().eq("").sum())
    raw, norm = s.nunique(), s.astype(str).str.strip().str.lower().nunique()
    v = Verdict.PASS if blank == 0 and raw == norm else Verdict.FAIL
    return Finding("C-C", "completeness", v,
                   f"{blank:,} blank, {raw - norm:,} case/whitespace collisions",
                   f"{raw:,} distinct raw, {norm:,} distinct normalised")


def c_e(d: D.Derived) -> Finding:
    """Churn against the prior extract. Needs a prior extract, which we do not have."""
    return Finding("C-E", "completeness", Verdict.NOT_APPLICABLE,
                   detail="no prior extract supplied; this rule is a two-extract comparison"
                          + ("" if CONFIG["churn_tolerance"] else
                             " (churn tolerance is also unsupplied)"))


def c_f(d: D.Derived) -> Finding:
    """Reference series must span the panel range at a compatible granularity."""
    return Finding("C-F", "completeness", Verdict.NOT_APPLICABLE,
                   detail="no reference series supplied to a workflow")


# ----------------------------------------------------------------------------
# RESOLUTIONRATE RULES
# ----------------------------------------------------------------------------

def _case_frame(d: D.Derived, tags: dict) -> pd.DataFrame | None:
    """One row per case, with vintage and a finished flag. None if not derivable."""
    if "_case" not in d.df:
        return None
    ws = d.col("workout_status")
    agg = {"_vintage": ("_vintage", "first")}
    if ws:
        agg["status"] = (ws, "last")
    if d.has("resolution_date"):
        agg["resolution_date"] = ("_resolution_date", "max")
    if d.has("realised_loss"):
        agg["loss"] = (d.col("realised_loss"), "last")
    if d.has("recovery_amount"):
        agg["recovery"] = (d.col("recovery_amount"), "max")
    if d.has("segment"):
        agg["segment"] = (d.col("segment"), "last")
    if d.has("cure_marker"):
        agg["cure_val"] = (d.col("cure_marker"), "max")
    cf = d.df.groupby("_case").agg(**agg)
    cf["default_date"] = d.df.groupby("_case")["_default_date"].first()
    cf["facility"] = d.df.groupby("_case")[d.col("facility_id")].first()
    if ws:
        bucket = cf.status.astype(str).map(tags)
        cf["untagged"] = bucket.isna()
        cf["bucket"] = bucket
        cf["finished"] = bucket.isin(["cure", "write-off", "payoff", "closure"])
    # Cure is a distinct signal from "finished": a dedicated cure marker takes
    # precedence, else a status value tagged 'cure', else cure is unknown (False).
    if "cure_val" in cf:
        cf["cured"] = cf["cure_val"].fillna(0).astype(float).eq(1)
    elif ws:
        cf["cured"] = cf["bucket"].eq("cure")
    else:
        cf["cured"] = False
    return cf


def _no_cases(rule_id: str, missing: str) -> Finding:
    return Finding(rule_id, "resolutionrate", Verdict.NOT_APPLICABLE,
                   detail=f"{missing}; case identity is facility id + default date, "
                          f"so no case can be formed")


def resolutionrate_rules(d: D.Derived, tags: dict, product: str) -> list[Finding]:
    ids = ["RRU-01", "RRU-02", "R-A", "R-B", "R-C", "R-D", "R-E", "R-F", "R-G", "R-H"]

    if not d.has("default_date"):
        out = [_no_cases(r, "default date unbound") for r in ids if r != "R-H"]
        out.append(r_h(d, product))
        return sorted(out, key=lambda f: ids.index(f.rule_id))

    cf = _case_frame(d, tags)
    horizon = CONFIG["horizon_months"].get(product)
    floor = CONFIG["resolution_floor"].get(product)
    mature = D.mature_vintages(d, horizon) if horizon else []
    minc = CONFIG["minimum_cases_per_vintage"]

    if not d.col("workout_status"):
        na = [Finding(r, "resolutionrate", Verdict.NOT_APPLICABLE,
                      detail="workout status unbound; finished cannot be determined")
              for r in ids if r not in ("R-G", "R-H")]
        return sorted(na + [r_g(d), r_h(d, product)], key=lambda f: ids.index(f.rule_id))

    if cf.untagged.any():
        vals = cf.loc[cf.untagged, "status"].unique()[:5]
        halt = Finding("", "", Verdict.NOT_APPLICABLE,
                       detail=f"G3: status values outside the saved list halt the rule: "
                              f"{list(vals)}")
        return [Finding(r, "resolutionrate", Verdict.NOT_APPLICABLE, detail=halt.detail)
                for r in ids]

    mat = cf[cf._vintage.isin(mature)]
    out: list[Finding] = []

    # RRU-01 finished / all, per mature vintage
    rows, fails = [], 0
    for v, grp in mat.groupby("_vintage"):
        if len(grp) < minc:
            rows.append(f"{int(v)}: {len(grp)} cases NOT-ASSESSABLE (below minimum {minc})")
            continue
        ratio = grp.finished.mean()
        ok = ratio >= floor
        fails += 0 if ok else 1
        rows.append(f"{int(v)}: {len(grp)} cases, ratio {ratio:.3f} vs floor {floor} "
                    f"{'PASS' if ok else 'FAIL'}")
    if not mature:
        out.append(Finding("RRU-01", "resolutionrate", Verdict.NOT_ASSESSABLE,
                           detail=f"no vintage is mature at horizon {horizon} months for {product}"))
    else:
        out.append(Finding("RRU-01", "resolutionrate",
                           Verdict.FAIL if fails else Verdict.PASS,
                           f"{fails} failing vintages of {len(mature)}", "; ".join(rows)))

    # RRU-02 loss with a censoring stamp
    if not d.has("realised_loss"):
        out.append(Finding("RRU-02", "resolutionrate", Verdict.NOT_APPLICABLE,
                           detail="realised loss unbound"))
    else:
        rows = []
        for v, grp in mat.groupby("_vintage"):
            fin = grp[grp.finished]
            open_share = 1 - grp.finished.mean()
            stamp = " CENSORED" if open_share > CONFIG["open_share_tolerance"] else ""
            rows.append(f"{int(v)}: mean {fin.loss.mean():.4f}, median {fin.loss.median():.4f}, "
                        f"open share {open_share:.3f}{stamp}")
        out.append(Finding("RRU-02", "resolutionrate", Verdict.PASS if rows else Verdict.NOT_ASSESSABLE,
                           "reported", "; ".join(rows) or "no mature vintage"))

    # R-A cure mix. Needs cure detection; without a cure marker or a status value
    # tagged 'cure', cures cannot be told apart from other closures, so the rule
    # returns NOT-APPLICABLE rather than reporting every share as zero.
    cure_detectable = d.has("cure_marker") or "cure" in (tags or {}).values()
    if not cure_detectable:
        out.append(Finding("R-A", "resolutionrate", Verdict.NOT_APPLICABLE,
                           detail="cure cannot be detected: bind a cure marker or tag a "
                                  "workout-status value as 'cure'"))
    elif CONFIG["mix_tolerance"] is D.UNSUPPLIED:
        shares = {int(v): round(g[g.finished].cured.mean(), 4)
                  for v, g in mat.groupby("_vintage") if g.finished.sum() >= minc}
        out.append(Finding("R-A", "resolutionrate", Verdict.NO_VERDICT, str(shares),
                           "mix tolerance unsupplied and no default proposed (CG4/G8)"))
    else:
        tol = CONFIG["mix_tolerance"]
        pooled = mat.loc[mat.finished, "cured"].mean()
        rows, bad = [], 0
        for v, g in mat.groupby("_vintage"):
            fin = g[g.finished]
            if len(fin) < minc:
                rows.append(f"{int(v)}: {len(fin)} finished NOT-ASSESSABLE (below minimum {minc})")
                continue
            share = fin.cured.mean()
            gap = share - pooled
            over = abs(gap) > tol
            bad += 1 if over else 0
            rows.append(f"{int(v)}: cure share {share:.3f} vs pooled {pooled:.3f} "
                        f"(gap {gap:+.3f}) {'FAIL' if over else 'PASS'}")
        out.append(Finding("R-A", "resolutionrate", Verdict.FAIL if bad else Verdict.PASS,
                           f"{bad} vintages beyond mix tolerance {tol}", "; ".join(rows)))

    # R-B stale open cases
    open_cases = cf[~cf.finished]
    age = ((d.as_of - open_cases.default_date).dt.days / 30.44) if len(open_cases) else pd.Series(dtype=float)
    if CONFIG["overage_multiple"] is D.UNSUPPLIED:
        out.append(Finding("R-B", "resolutionrate", Verdict.NO_VERDICT,
                           f"{len(open_cases):,} open cases, max age "
                           f"{age.max():.0f} months" if len(age) else "no open cases",
                           "overage multiple unsupplied and no default proposed (CG4/G8)"))
    else:
        flagged = int((age > horizon * CONFIG["overage_multiple"]).sum())
        out.append(Finding("R-B", "resolutionrate",
                           Verdict.FAIL if flagged else Verdict.PASS,
                           f"{flagged:,} cases beyond horizon x "
                           f"{CONFIG['overage_multiple']}"))

    # R-C zero-recovery spike
    if not d.has("recovery_amount"):
        out.append(Finding("R-C", "resolutionrate", Verdict.NOT_APPLICABLE,
                           detail="recovery amount unbound"))
    elif CONFIG["spike_tolerance"] is D.UNSUPPLIED:
        fin = mat[mat.finished & mat.recovery.notna()]
        shares = {int(v): round(g.recovery.eq(0).mean(), 4)
                  for v, g in fin.groupby("_vintage") if len(g) >= minc}
        out.append(Finding("R-C", "resolutionrate", Verdict.NO_VERDICT, str(shares),
                           "spike tolerance unsupplied and no default proposed. G19: null is "
                           "not zero, nulls excluded from the denominator"))
    else:
        out.append(Finding("R-C", "resolutionrate", Verdict.NOT_IMPLEMENTED))

    # R-D time to resolution
    if not d.has("resolution_date"):
        out.append(Finding("R-D", "resolutionrate", Verdict.NOT_APPLICABLE,
                           detail="resolution date unbound; G10 also requires its declared basis "
                                  "to be case closure or final recovery cashflow"))
    elif CONFIG["shift_tolerance_months"] is D.UNSUPPLIED:
        fin = mat[mat.finished]
        dur = (fin.resolution_date - fin.default_date).dt.days / 30.44
        meds = {int(v): round(dur[fin._vintage == v].median(), 1)
                for v in fin._vintage.unique() if (fin._vintage == v).sum() >= minc}
        out.append(Finding("R-D", "resolutionrate", Verdict.NO_VERDICT, str(meds),
                           "shift tolerance unsupplied and no default proposed"))
    else:
        tol = CONFIG["shift_tolerance_months"]
        fin = mat[mat.finished].copy()
        fin["dur"] = (fin.resolution_date - fin.default_date).dt.days / 30.44
        pooled = fin.dur.median()
        rows, bad = [], 0
        for v, g in fin.groupby("_vintage"):
            if len(g) < minc:
                rows.append(f"{int(v)}: {len(g)} finished NOT-ASSESSABLE (below minimum {minc})")
                continue
            med = g.dur.median()
            gap = med - pooled
            over = abs(gap) > tol
            bad += 1 if over else 0
            rows.append(f"{int(v)}: median {med:.1f}m vs pooled {pooled:.1f}m "
                        f"(gap {gap:+.1f}) {'FAIL' if over else 'PASS'}")
        out.append(Finding("R-D", "resolutionrate", Verdict.FAIL if bad else Verdict.PASS,
                           f"{bad} vintages beyond shift tolerance {tol} months", "; ".join(rows)))

    # R-E vintage x segment
    if not d.has("segment"):
        out.append(Finding("R-E", "resolutionrate", Verdict.NOT_APPLICABLE,
                           detail="segment unbound"))
    elif CONFIG["minimum_cell_cases"] is D.UNSUPPLIED:
        out.append(Finding("R-E", "resolutionrate", Verdict.NO_VERDICT,
                           detail="minimum cell cases unsupplied and no default proposed; "
                                  "thin cells cannot be screened (G22)"))
    else:
        bad = 0
        for (v, s), g in mat.groupby(["_vintage", "segment"]):
            if len(g) >= CONFIG["minimum_cell_cases"] and g.finished.mean() < floor:
                bad += 1
        out.append(Finding("R-E", "resolutionrate", Verdict.FAIL if bad else Verdict.PASS,
                           f"{bad} cells below the RRU-01 floor"))

    # R-F re-default after cure
    cure_detectable = d.has("cure_marker") or "cure" in (tags or {}).values()
    if not cure_detectable:
        out.append(Finding("R-F", "resolutionrate", Verdict.NOT_APPLICABLE,
                           detail="cure cannot be detected: bind a cure marker or tag a "
                                  "workout-status value as 'cure'"))
    elif CONFIG["redefault_window_months"] is D.UNSUPPLIED or CONFIG["redefault_tolerance"] is D.UNSUPPLIED:
        out.append(Finding("R-F", "resolutionrate", Verdict.NO_VERDICT,
                           f"{int(mat.cured.sum()):,} cured cases in mature vintages",
                           "re-default window and tolerance both unsupplied, no default proposed"))
    else:
        window = int(CONFIG["redefault_window_months"])
        tol = CONFIG["redefault_tolerance"]
        cured_cases = mat[mat.cured]
        if len(cured_cases) < minc:
            out.append(Finding("R-F", "resolutionrate", Verdict.NOT_ASSESSABLE,
                               f"{len(cured_cases)} cured cases in mature vintages "
                               f"(below minimum {minc})"))
        else:
            # A cure re-defaults if the same facility has a later default dated after the
            # cure completes and within the window. Anchor on the resolution date where
            # present, else the cure case's own default date. Compared against ALL cases
            # so a re-default landing in a non-mature vintage still counts.
            all_defaults: dict = {}
            for fac, dd in zip(cf["facility"], cf["default_date"]):
                all_defaults.setdefault(fac, []).append(dd)
            has_res = "resolution_date" in cured_cases.columns
            redef = 0
            for _, row in cured_cases.iterrows():
                anchor = row["resolution_date"] if has_res and pd.notna(row.get("resolution_date")) \
                    else row["default_date"]
                if pd.isna(anchor):
                    continue
                end = anchor + pd.DateOffset(months=window)
                if any(pd.notna(dd) and anchor < dd <= end
                       for dd in all_defaults.get(row["facility"], [])):
                    redef += 1
            share = redef / len(cured_cases)
            out.append(Finding("R-F", "resolutionrate",
                               Verdict.FAIL if share > tol else Verdict.PASS,
                               f"re-default share {share:.3f} vs tolerance {tol}",
                               f"{redef:,} of {len(cured_cases):,} cured cases re-defaulted "
                               f"within {window} months"))

    out.append(r_g(d))
    out.append(r_h(d, product))
    return sorted(out, key=lambda f: ids.index(f.rule_id))


def r_g(d: D.Derived) -> Finding:
    """Downturn coverage. Depends on the external downturn-periods workflow."""
    if not CONFIG["downturn_periods"]:
        return Finding("R-G", "resolutionrate", Verdict.NOT_APPLICABLE,
                       detail="G17: no confirmed downturn periods. The downturn-periods "
                              "workflow runs once per database and must complete before R-G")
    return Finding("R-G", "resolutionrate", Verdict.NOT_IMPLEMENTED)


def r_h(d: D.Derived, product: str) -> Finding:
    """Observation length in the longest-term segment. The only rule not needing a default."""
    if not d.has("facility_id", "segment", "origination_date"):
        return Finding("R-H", "resolutionrate", Verdict.NOT_APPLICABLE,
                       detail="facility id, segment or origination date unbound")
    if not d.has("term_maturity_date"):
        return Finding("R-H", "resolutionrate", Verdict.NOT_APPLICABLE,
                       detail="G28: no term column, so the longest-term segment cannot be selected")

    fid, seg = d.col("facility_id"), d.col("segment")
    f = d.df.groupby(fid).agg(segment=(seg, "last"), orig=("_origination_date", "min"),
                              mat=("_term_maturity_date", "max"), last=("_period_ts", "max"))
    f["term_months"] = (f.mat - f.orig).dt.days / 30.44
    med = f.groupby("segment").term_months.median()
    counts = f.segment.value_counts()
    eligible = med[counts.reindex(med.index).fillna(0) >= 20]
    if eligible.empty:
        return Finding("R-H", "resolutionrate", Verdict.NOT_ASSESSABLE,
                       detail="no segment reaches the 20-facility minimum")
    target = eligible.idxmax()

    panel_start = d.df["_period_ts"].min()
    sub = f[f.segment == target].copy()
    start = sub.orig.where(sub.orig > panel_start, panel_start)
    end = sub.last.where(sub.last < d.as_of, d.as_of)
    sub["observed_months"] = (end - start).dt.days / 30.44

    measure = (f"longest-term segment '{target}' (median term "
               f"{eligible.max():.0f} months), {len(sub):,} facilities, "
               f"median observed {sub.observed_months.median():.0f} months")
    if CONFIG["seasoning_expectation_months"] is D.UNSUPPLIED or CONFIG["observed_share_floor"] is D.UNSUPPLIED:
        return Finding("R-H", "resolutionrate", Verdict.NO_VERDICT, measure,
                       "seasoning expectation and observed-share floor both unsupplied, "
                       "no default proposed (G8)")
    share = (sub.observed_months >= CONFIG["seasoning_expectation_months"]).mean()
    v = Verdict.PASS if share >= CONFIG["observed_share_floor"] else Verdict.FAIL
    return Finding("R-H", "resolutionrate", v,
                   f"observed share {share:.3f} vs floor {CONFIG['observed_share_floor']}", measure)


COMPLETENESS = {"CMG-01": cmg_01, "CMG-02": cmg_02, "CMU-01": cmu_01,
                "C-C": c_c, "C-E": c_e, "C-F": c_f}


# ----------------------------------------------------------------------------
# DRIVER
# ----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=".")
    ap.add_argument("--binding", default=None)
    ap.add_argument("--product", default="CRE", choices=["CRE", "MORTGAGE"])
    ap.add_argument("--framework", default="IFRS9", choices=["IRB", "IFRS9"],
                    help="regulatory use-case; a rule runs only if it declares this framework")
    ap.add_argument("--json", default=None, help="also write findings to this path")
    args = ap.parse_args()

    folder = Path(args.folder)
    bpath = Path(args.binding) if args.binding else folder / "binding.json"
    if not bpath.exists():
        raise SystemExit(f"{bpath} not found - run bind_cli.py first")
    binding = json.loads(bpath.read_text())

    kbs = D.load_kbs(folder)
    df, name = D.load_dataset(folder, binding.get("_dataset"))

    print("=" * 78)
    print(f"dataset {name}   {len(df):,} rows    product: {args.product}    framework: {args.framework}")
    print("=" * 78)

    findings: list[Finding] = []

    for kb_id, kb in kbs.items():
        entry = binding["kbs"].get(kb_id, {})
        roles = entry.get("roles", {})
        tags = entry.get("status_tags", {})
        d = D.build(df, roles, binding.get("_as_of"))

        print(f"\n--- {kb_id}: {kb.get('diagnostic','')}")
        bound = {r: c for r, c in roles.items() if c}
        print(f"    bound   {len(bound)}/{len(roles)} roles: "
              f"{', '.join(f'{r}={c}' for r, c in bound.items()) or 'none'}")
        unbound = [r for r, c in roles.items() if not c]
        if unbound:
            print(f"    unbound {', '.join(unbound)}")
        for n in d.notes:
            print(f"    note    {n}")

        # framework scope: a rule runs only if it declares the requested use-case
        scope = {r["id"] for r in kb["rules"]
                 if args.framework in r.get("use_case", [args.framework])}
        n_out = len(kb["rules"]) - len(scope)
        print(f"    scope   framework {args.framework}: {len(scope)}/{len(kb['rules'])} rules in scope"
              + (f" ({n_out} out of scope)" if n_out else ""))

        def _scoped(findings_list):
            return [f if f.rule_id in scope
                    else Finding(f.rule_id, kb_id, Verdict.NOT_APPLICABLE,
                                 detail=f"rule not in scope for framework {args.framework}")
                    for f in findings_list]

        gr = G.run_shared_guards(kb, d, CONFIG)
        for n in gr.notes:
            print(f"    guard   {n}")
        if not gr.may_proceed:
            findings += [Finding(r["id"], kb_id, Verdict.NOT_APPLICABLE, detail=gr.block_reason)
                         for r in kb["rules"]]
            continue

        if kb_id == "completeness":
            findings += _scoped([COMPLETENESS[r["id"]](d) for r in kb["rules"] if r["id"] in COMPLETENESS])
        elif kb_id == "resolutionrate":
            findings += _scoped(resolutionrate_rules(d, tags, args.product))

    print("\n" + "=" * 78)
    print(f"{'KB':<15}{'RULE':<9}{'VERDICT':<17}MEASURE")
    print("-" * 78)
    for f in findings:
        print(f"{f.kb_id:<15}{f.rule_id:<9}{f.verdict:<17}{f.measure[:36]}")
        if f.detail:
            for line in [f.detail[i:i + 68] for i in range(0, min(len(f.detail), 272), 68)]:
                print(f"{'':<41}{line}")

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    print("-" * 78)
    print("  ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    if args.json:
        Path(args.json).write_text(json.dumps(
            [dict(kb=f.kb_id, rule=f.rule_id, verdict=f.verdict,
                  measure=f.measure, detail=f.detail) for f in findings], indent=2))
        print(f"\nwritten: {args.json}")


if __name__ == "__main__":
    main()
