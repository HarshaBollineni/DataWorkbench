"""
derivation.py - shared derivation layer for the completeness and resolutionrate KBs.

Nothing in here judges anything. It only:
  1. knows what the 12 semantic roles are,
  2. predicts which column fills each role,
  3. builds the derived quantities every rule reads
     (period index, granularity, expected grid, case identity, vintage, maturity).

The rules themselves live in run_tests.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# ----------------------------------------------------------------------------
# 1. ROLE CATALOG
# ----------------------------------------------------------------------------
# 12 roles, after the two decisions taken during design:
#   - partition column and segment were MERGED into one role
#   - status was SPLIT into default_status / workout_status / closure_marker

ROLES: dict[str, dict] = {
    "facility_id": dict(
        label="facility id", kind="id",
        patterns=[r"facility.*id", r"^fac.*(id|ref|no|num)", r"loan.*(id|no|num|ref)",
                  r"account.*id", r"contract.*id", r"deal.*id", r"^id$"],
    ),
    "period": dict(
        label="period", kind="period",
        patterns=[r"report(ing)?.*(date|quarter|month|period)", r"as.?of.*(date|dt)",
                  r"snapshot", r"observ.*(date|dt|month|period|time)", r"^period$",
                  r"^quarter$", r"^month$", r"^yyyymm$", r"^date$", r"cob.*date"],
    ),
    "origination_date": dict(
        label="origination date", kind="date",
        patterns=[r"originat", r"start.*date", r"disburse", r"funding.*date", r"open.*date"],
    ),
    "default_status": dict(
        label="default status", kind="status",
        patterns=[r"default.*(flag|ind|status|stage|reg|marker|state|bucket)",
                  r"^default$", r"npl", r"non.?perform", r"stage$", r"ifrs.?9.*stage"],
    ),
    "workout_status": dict(
        label="workout status", kind="status",
        patterns=[r"workout", r"recovery.*status", r"cure.*(status|flag)", r"case.*(state|status)",
                  r"wf.?stage", r"resolution.*status", r"outcome"],
    ),
    "closure_marker": dict(
        label="closure marker", kind="status",
        patterns=[r"paid.?off", r"(^|_)closed", r"(^|_)closure", r"redeem", r"matur.*(ind|flag)",
                  r"terminat", r"exit.*(flag|ind)"],
    ),
    "cure_marker": dict(
        label="cure marker", kind="status",
        patterns=[r"cure.*(flag|ind|marker|status)", r"^cured?$"],
    ),
    "segment": dict(
        label="segment / partition", kind="categorical",
        patterns=[r"^segment$", r"portfolio", r"^partition$", r"property.*type", r"asset.*class",
                  r"sector", r"^region$", r"^state$", r"^country$", r"sub.?portfolio"],
    ),
    "default_date": dict(
        label="default date", kind="date",
        patterns=[r"default.*date", r"date.*default", r"npl.*date", r"event.*date"],
    ),
    "realised_loss": dict(
        label="realised loss", kind="amount",
        patterns=[r"realis?z?ed.*loss", r"loss.*(amt|amount|value)", r"write.?off.*(amt|amount)",
                  r"^loss", r"lgd.*realis"],
    ),
    "recovery_amount": dict(
        label="recovery amount", kind="amount",
        patterns=[r"recover(y|ies).*(amt|amount|value|total)", r"^recovery", r"cash.*recover"],
    ),
    "resolution_date": dict(
        label="resolution date", kind="date",
        patterns=[r"resolution.*date", r"closed?.*date", r"workout.*end", r"cure.*date",
                  r"final.*(recovery|cashflow).*date"],
    ),
    "term_maturity_date": dict(
        label="term or maturity date", kind="date",
        patterns=[r"matur.*date", r"^term$", r"term.*(month|year)", r"end.*date", r"expiry"],
    ),
}

# Maps the prose in each rule's look_at field onto roles. Longest phrase first so
# "default date" is matched before "default", and "origination date" before "date".
LOOK_AT_MAP: list[tuple[str, list[str]]] = [
    ("term or maturity date", ["term_maturity_date"]),
    ("origination date", ["origination_date"]),
    ("resolution date", ["resolution_date"]),
    ("recovery amount", ["recovery_amount"]),
    ("closure marker", ["closure_marker"]),
    ("cure marker", ["cure_marker"]),
    ("partition column", ["segment"]),
    ("workout status", ["workout_status", "default_status"]),  # split during design
    ("realised loss", ["realised_loss"]),
    ("default date", ["default_date"]),
    ("facility id", ["facility_id"]),
    ("segment", ["segment"]),
    ("period", ["period"]),
]


def roles_for_rule(rule: dict) -> list[str]:
    """Read a rule's look_at prose and return the roles it needs."""
    text = rule.get("look_at", "").lower()
    found: list[str] = []
    for phrase, roles in LOOK_AT_MAP:
        if phrase in text:
            for r in roles:
                if r not in found:
                    found.append(r)
            text = text.replace(phrase, "")  # consume, so "period" isn't re-hit
    return found


def roles_for_kb(kb: dict) -> list[str]:
    """Union of roles across every rule in the KB, in catalog order."""
    needed: set[str] = set()
    for rule in kb.get("rules", []):
        needed.update(roles_for_rule(rule))
    return [r for r in ROLES if r in needed]


# ----------------------------------------------------------------------------
# 2. LOADING
# ----------------------------------------------------------------------------

def load_kbs(folder: str | Path) -> dict[str, dict]:
    """Every *_kb_*.json in the folder, keyed by kb_id."""
    out = {}
    for p in sorted(Path(folder).glob("*kb*.json")):
        kb = json.loads(p.read_text())
        if "kb_id" in kb and "rules" in kb:
            kb["_source_file"] = p.name
            out[kb["kb_id"]] = kb
    return out


def load_dataset(folder: str | Path, filename: str | None = None) -> tuple[pd.DataFrame, str]:
    """First .xlsx/.csv in the folder unless one is named."""
    folder = Path(folder)
    if filename:
        path = folder / filename
    else:
        cands = [p for p in sorted(folder.iterdir())
                 if p.suffix.lower() in (".xlsx", ".xlsm", ".csv") and not p.name.startswith("~$")]
        if not cands:
            raise FileNotFoundError(f"no .xlsx or .csv found in {folder}")
        path = cands[0]
    df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    return df, path.name


# ----------------------------------------------------------------------------
# 3. COLUMN PROFILING AND ROLE PREDICTION
# ----------------------------------------------------------------------------

@dataclass
class ColumnProfile:
    name: str
    dtype: str
    n_distinct: int
    null_frac: float
    is_datetime: bool
    is_numeric: bool
    looks_like_period: bool
    sample: list
    is_datelike: bool = False   # parses as calendar dates (e.g. "2020-01-01"), though not a period label

    def describe(self) -> str:
        return (f"{self.dtype}, {self.n_distinct} distinct, "
                f"{self.null_frac:.0%} null, e.g. {self.sample[:2]}")


PERIOD_RE = [
    re.compile(r"^\d{4}-?Q[1-4]$", re.I),
    re.compile(r"^\d{4}-\d{2}$"),
    re.compile(r"^\d{6}$"),
]


def profile(df: pd.DataFrame) -> dict[str, ColumnProfile]:
    profiles = {}
    for c in df.columns:
        s = df[c]
        nonnull = s.dropna()
        sample = [str(v) for v in nonnull.head(3).tolist()]
        looks_period = False
        datelike = False
        if not nonnull.empty and pd.api.types.is_string_dtype(nonnull):
            # Sample a bounded window and allow a small share of stray values, so a
            # single messy row neither creates nor destroys the signal.
            probe = nonnull.astype(str).head(200)
            looks_period = any(
                probe.map(lambda v, rx=rx: bool(rx.match(v))).mean() >= 0.95
                for rx in PERIOD_RE)
            # Date-likeness is a separate, weaker signal than period-likeness: full
            # calendar dates (origination, default, resolution ...) parse as dates but
            # are NOT period labels, so they must not be treated as a period.
            if not looks_period:
                parsed = pd.to_datetime(probe, errors="coerce", format="mixed")
                datelike = parsed.notna().mean() >= 0.95
        profiles[c] = ColumnProfile(
            name=c,
            dtype=str(s.dtype),
            n_distinct=int(nonnull.nunique()),
            null_frac=float(s.isna().mean()),
            is_datetime=pd.api.types.is_datetime64_any_dtype(s),
            is_numeric=pd.api.types.is_numeric_dtype(s),
            looks_like_period=looks_period,
            sample=sample,
            is_datelike=datelike,
        )
    return profiles


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


@dataclass
class Candidate:
    column: str
    score: int
    reason: str


def predict(role: str, df: pd.DataFrame, profiles: dict[str, ColumnProfile]) -> list[Candidate]:
    """Rank columns for a role. Name match scores highest, value shape breaks ties."""
    spec = ROLES[role]
    kind = spec["kind"]
    out: list[Candidate] = []

    for col, p in profiles.items():
        score, why = 0, []
        n = _norm(col)

        for i, pat in enumerate(spec["patterns"]):
            if re.search(pat, n):
                # Patterns are listed most-canonical-first, so an earlier hit
                # outranks a later one. This is what breaks the tie between
                # paid_off_indicator and matured_balloon_indicator.
                score += 60 - 2 * i
                why.append("name match")
                break

        # A role is only a candidate if the NAME matches. Value shape ranks the
        # name matches against each other; it never nominates a column on its own.
        # Without this, "realised loss" happily binds to the first numeric column
        # it sees. A role with no name match must predict nothing, so that an
        # absent role surfaces as unbound rather than as a wrong binding.
        if score == 0 and not (kind == "period" and p.looks_like_period):
            continue

        # Type sanity: a name match is not enough if the value type contradicts
        # the role. A date role cannot be a purely numeric measure, and a status
        # marker is a categorical state, never a point in time. Rejecting these
        # here keeps a coincidental name overlap from producing a wrong binding.
        if kind == "date" and p.is_numeric and not p.is_datetime:
            continue
        if kind == "status" and p.is_datetime:
            continue

        if kind == "date" and (p.is_datetime or p.is_datelike):
            score += 25; why.append("date type")
        elif kind == "date" and score:
            score -= 20; why.append("not a date type")

        if kind == "period":
            if p.looks_like_period:
                score += 30; why.append("period-shaped values")
            elif p.is_datetime:
                score += 20; why.append("date type")

        if kind == "id":
            if p.null_frac == 0:
                score += 10; why.append("no nulls")
            if 1 < p.n_distinct < len(df):
                score += 15; why.append("repeats across rows")

        # A forward-looking label (default_12m, default_next_q) is a modelling
        # target, not the current status of the case. Demote it.
        if kind == "status" and re.search(r"(_|^)(\d+m|\d+q|fwd|forward|next|pred)(_|$)", n):
            score -= 50; why.append("looks forward-looking, not current status")

        # A constant column marks nothing. It cannot be the column that says
        # "this facility closed" if it never changes, so demote it below any
        # column that actually varies.
        if kind == "status" and p.n_distinct <= 1:
            score -= 15; why.append("constant - marks nothing")
        elif kind == "status" and p.n_distinct <= 12:
            score += 20; why.append(f"{p.n_distinct} distinct values")
        elif kind == "status" and p.n_distinct > 30 and score:
            score -= 25; why.append("too many values for a status")

        if kind == "amount":
            if p.is_numeric:
                score += 20; why.append("numeric")
            elif score:
                score -= 25; why.append("not numeric")

        if kind == "categorical" and 1 < p.n_distinct <= 60:
            score += 20; why.append(f"{p.n_distinct} categories")

        if score > 0:
            out.append(Candidate(col, score, ", ".join(why)))

    return sorted(out, key=lambda c: (-c.score, c.column))


def resolve_conflicts(roles: dict, df: pd.DataFrame,
                      profiles: dict) -> tuple[dict, list[str]]:
    """
    Enforce that no column is bound to two roles. Where it is, keep the role whose
    predictor scores that column highest and unbind the others. Returns the
    adjusted binding and a note per resolved conflict. A column that legitimately
    serves two roles is a deliberate manual choice and is not auto-resolved here;
    this only cleans up automatic (unconfirmed) bindings.
    """
    by_column: dict[str, list[str]] = {}
    for role, col in roles.items():
        if col:
            by_column.setdefault(col, []).append(role)

    adjusted = dict(roles)
    notes: list[str] = []
    for col, claimers in by_column.items():
        if len(claimers) < 2:
            continue

        def score_for(role: str) -> int:
            for cand in predict(role, df, profiles):
                if cand.column == col:
                    return cand.score
            return -1

        winner = max(claimers, key=score_for)
        for role in claimers:
            if role != winner:
                adjusted[role] = None
                notes.append(f"'{col}' was bound to both {winner} and {role}; kept {winner} "
                             f"(stronger match) and left {role} unbound")
    return adjusted, notes


# ----------------------------------------------------------------------------
# 4. PERIOD PARSING AND GRANULARITY  (guard CG3 / G1)
# ----------------------------------------------------------------------------

def to_timestamp(series: pd.Series) -> pd.Series:
    """Parse a period column into timestamps. Handles dates, 2006-Q1, 200603, 2006-03."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype(str).str.strip()
    if s.dropna().str.match(r"^\d{4}-?Q[1-4]$", case=False).all():
        return pd.PeriodIndex(s.str.upper().str.replace("-", "", regex=False),
                              freq="Q").to_timestamp(how="end").to_series(index=series.index)
    if s.dropna().str.match(r"^\d{6}$").all():
        return pd.to_datetime(s, format="%Y%m")
    return pd.to_datetime(series, errors="coerce")


@dataclass
class Granularity:
    months: int | None          # 1 monthly, 3 quarterly, 12 annual
    label: str                  # MONTHLY / QUARTERLY / ANNUAL / IRREGULAR
    share: float                # share of consecutive gaps agreeing with the mode
    applicable: bool

    LABELS = {1: "MONTHLY", 3: "QUARTERLY", 6: "SEMIANNUAL", 12: "ANNUAL"}


def detect_granularity(period_ts: pd.Series, threshold: float = 0.80) -> Granularity:
    """CG3: modal gap between consecutive distinct periods must hold for >=80% of gaps."""
    uniq = pd.Series(sorted(pd.Series(period_ts).dropna().unique()))
    if len(uniq) < 2:
        return Granularity(None, "IRREGULAR", 0.0, False)
    months = (uniq.dt.year * 12 + uniq.dt.month).diff().dropna().astype(int)
    mode = int(months.mode().iloc[0])
    share = float((months == mode).mean())
    if share < threshold or mode not in Granularity.LABELS:
        return Granularity(None, "IRREGULAR", share, False)
    return Granularity(mode, Granularity.LABELS[mode], share, True)


def period_index(period_ts: pd.Series, gran: Granularity) -> pd.Series:
    """Integer index so consecutive periods differ by exactly 1. Needs a valid granularity."""
    if not gran.applicable:
        raise ValueError("cannot index periods at IRREGULAR granularity")
    months = period_ts.dt.year * 12 + period_ts.dt.month
    return ((months - months.min()) // gran.months).astype("Int64")


# ----------------------------------------------------------------------------
# 5. THE DERIVED FRAME EVERY RULE READS
# ----------------------------------------------------------------------------

@dataclass
class Derived:
    """One object carrying the dataset plus everything derived from it."""
    df: pd.DataFrame
    binding: dict[str, str | None]
    granularity: Granularity
    as_of: pd.Timestamp | None = None
    notes: list[str] = field(default_factory=list)

    def col(self, role: str) -> str | None:
        return self.binding.get(role)

    def has(self, *roles: str) -> bool:
        return all(self.binding.get(r) for r in roles)

    def s(self, role: str) -> pd.Series:
        """The series for a role. Raises if unbound - callers guard with has() first."""
        c = self.col(role)
        if not c:
            raise KeyError(f"role '{role}' is unbound")
        return self.df[c]


def build(df: pd.DataFrame, binding: dict, as_of: str | None = None) -> Derived:
    """Attach derived columns: _period_ts, _pidx, and where possible _case, _vintage."""
    df = df.copy()
    gran = Granularity(None, "IRREGULAR", 0.0, False)

    if binding.get("period"):
        df["_period_ts"] = to_timestamp(df[binding["period"]])
        gran = detect_granularity(df["_period_ts"])
        if gran.applicable:
            df["_pidx"] = period_index(df["_period_ts"], gran)

    for role in ("origination_date", "default_date", "resolution_date", "term_maturity_date"):
        c = binding.get(role)
        if c:
            df[f"_{role}"] = pd.to_datetime(df[c], errors="coerce")

    # case identity = facility id + default date  (never panel rows)
    if binding.get("facility_id") and binding.get("default_date"):
        df["_case"] = (df[binding["facility_id"]].astype(str) + "|" +
                       df["_default_date"].dt.strftime("%Y-%m-%d"))
        df["_vintage"] = df["_default_date"].dt.year   # calendar year of default

    d = Derived(df=df, binding=binding, granularity=gran)

    if as_of:
        d.as_of = pd.Timestamp(as_of)
    elif "_period_ts" in df:
        d.as_of = df["_period_ts"].max()
        d.notes.append(f"as-of not declared; taken as latest period {d.as_of:%Y-%m-%d}")
    elif "_default_date" in df and df["_default_date"].notna().any():
        d.as_of = df["_default_date"].max()
        d.notes.append(f"as-of not declared and no period bound; taken as latest default date "
                       f"{d.as_of:%Y-%m-%d}")

    return d


def mature_vintages(d: Derived, horizon_months: int) -> list[int]:
    """A vintage is mature when as_of - 31 Dec of the vintage year >= horizon."""
    if "_vintage" not in d.df or d.as_of is None:
        return []
    out = []
    for v in sorted(d.df["_vintage"].dropna().unique()):
        end = pd.Timestamp(int(v), 12, 31)
        if (d.as_of.year - end.year) * 12 + (d.as_of.month - end.month) >= horizon_months:
            out.append(int(v))
    return out


def expected_grid(d: Derived, runoff: str = "last_row") -> pd.DataFrame:
    """
    Per facility: window start, window end, expected period count, observed count.
    runoff='last_row' is the CG5 fallback when no run-off convention was declared,
    which makes right-edge truncation undetectable - callers must stamp the result
    boundary-approximate.
    """
    fid = d.col("facility_id")
    g = d.df.groupby(fid).agg(first=("_pidx", "min"), last=("_pidx", "max"),
                              observed=("_pidx", "nunique"))
    panel_start = int(d.df["_pidx"].min())

    if d.has("origination_date"):
        orig = d.df.groupby(fid)["_origination_date"].min()
        om = orig.dt.year * 12 + orig.dt.month
        base = d.df["_period_ts"].dt.year * 12 + d.df["_period_ts"].dt.month
        g["orig_pidx"] = ((om - base.min()) // d.granularity.months).astype("Int64")
        g["window_start"] = g[["orig_pidx", "first"]].max(axis=1).clip(lower=panel_start)
        g["orig_after_first"] = g["orig_pidx"] > g["first"]   # CG7 contradiction
    else:
        g["window_start"] = g["first"]
        g["orig_after_first"] = False

    g["window_end"] = g["last"] if runoff == "last_row" else int(d.df["_pidx"].max())
    g["expected"] = (g["window_end"] - g["window_start"] + 1).clip(lower=0)
    g["interior_span"] = g["last"] - g["first"] + 1
    g["interior_gaps"] = (g["interior_span"] - g["observed"]).clip(lower=0)
    return g


# ----------------------------------------------------------------------------
# 6. VERDICTS
# ----------------------------------------------------------------------------

class Verdict:
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT-APPLICABLE"     # required input unbound, or a guard failed
    NOT_ASSESSABLE = "NOT-ASSESSABLE"     # population below the minimum count
    NO_VERDICT = "NO-VERDICT"             # measure computed, no floor supplied to judge it
    NOT_IMPLEMENTED = "NOT-IMPLEMENTED"   # engine gap, NOT a statement about the data


UNSUPPLIED = None  # a floor or tolerance that was never supplied -> NO-VERDICT, never a default


@dataclass
class Finding:
    rule_id: str
    kb_id: str
    verdict: str
    measure: str = ""
    detail: str = ""
    guard_notes: list[str] = field(default_factory=list)

    def row(self) -> tuple:
        return (self.kb_id, self.rule_id, self.verdict, self.measure, self.detail)
