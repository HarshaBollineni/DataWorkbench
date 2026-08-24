"""CFR-05 / DX-04 — role resolution: S5's ladder, a KB-derived vocabulary.

S5 scored every (table, column) pair against a module-level ``ROLE_VOCAB``
dict of ~35 roles -> ``{entity, dtype, synonyms}``. That table is domain
knowledge in code (KB-01) and **does not port** (docs/0.4.0/07-decisions.md
DX-04). What ports verbatim is the *algorithm*:

    1.00  column name IS the role name (exact canonical)
    0.92  column name exactly matches a recorded synonym
    0.88  the dataset dictionary's description mentions a synonym phrase
    0.80  a synonym's whole token set is contained in the column's tokens
    0.55-0.75  fuzzy character similarity (graded by SequenceMatcher ratio)
    0.35-0.60  partial token overlap (Jaccard), graded
    + a dtype plausibility gate that can veto to 0.0
    + a 0.70 floor: a best score below it is reported UNRESOLVED, never
      auto-accepted at a weak score

and what is *derived at runtime* is the vocabulary (:func:`derive_vocabulary`):

    role + entity  <- the in-scope KB rules' own ``semantic_roles_json`` /
                      ``entity`` (Phase 5's parser output). The KB is the
                      vocabulary.
    synonyms       <- normalized-form variants of the role's OWN name as
                      recorded by the KB itself: an alternate spelling that
                      appears in the rule text, or a parse-hazard's recorded
                      form (KB-11). A role with no recorded variant simply
                      never reaches tier 2 — correct, not a gap.
    dict-desc      <- the INGESTED dataset's dictionary definitions
                      (ING-08's mapping records), supplied per (table,
                      column) by the caller. Generic and per-dataset.
    dtype          <- inferred structurally at KB-binding time from how the
                      role is used in its own rule (a date_ordering role is
                      date-like; a role compared to a numeric literal is
                      number-like; a two-valued enumerated domain is
                      flag-like) and cached on ``kb_rules.binding_params_json``.

There is no per-role synonym/entity/dtype literal in this module, and a
source-inspection test (6-T8) asserts there never will be.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

# Resolution floor (S5 ``resolve_roles``): below this a role is UNRESOLVED.
RESOLVED_FLOOR = 0.70

# Structural dtype kinds a binding may claim for a role. Which role gets
# which is decided per-rule at binding time, never listed here.
DTYPE_KINDS = ("number", "date", "flag", "category")

# When one role is used with conflicting structural dtypes across rules,
# the weaker (less vetoing) claim wins so the gate never rejects a genuine
# column on a disagreement between two rules.
_DTYPE_PRECEDENCE = {"flag": 0, "date": 1, "number": 2, "category": 3}


@dataclass(frozen=True)
class RoleMatch:
    role: str
    table: str | None
    column: str | None
    score: float
    reason: str
    via: str          # "override" | "auto" | "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "table": self.table, "column": self.column,
                "score": self.score, "reason": self.reason, "via": self.via}


def canon(s: str) -> str:
    """Canonical form: lowercase, non-alphanumerics -> single space, stripped."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def tokens(s: str) -> set[str]:
    return set(canon(s).split())


def collapse(s: str) -> str:
    """Alphanumerics only — the form a PDF extraction collapses a multi-word
    role name into (KB-11: ``exposure_at_default`` -> ``exposureatdefault``)."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# ============================================================================
#  Vocabulary derivation (DX-04) — the KB is the vocabulary
# ============================================================================

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _variants_from_text(role: str, text: str) -> set[str]:
    """Alternate recorded spellings of ``role`` inside its own rule text.

    A token whose collapsed form equals the role's collapsed form but whose
    literal text differs IS a normalized-form variant recorded by the KB
    (e.g. a row whose Roles cell reads ``exposureatdefault`` while its Rule
    cell reads ``exposureat_default``). Purely structural: no dictionary of
    business synonyms is consulted, and nothing is guessed.
    """
    target = collapse(role)
    if not target:
        return set()
    out: set[str] = set()
    for word in _WORD_RE.findall(text or ""):
        if word != role and collapse(word) == target:
            out.add(word)
    return out


def derive_vocabulary(rule_rows: list[dict[str, Any]],
                      extra_roles: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    """Build ``{role: {entity, dtype, synonyms}}`` from in-scope KB rules.

    ``rule_rows`` are ``kb_rules`` dicts (published + bound). ``extra_roles``
    are roles the RUN needs that no rule mentions — the grain and segment
    roles from the semantic layer (FWK-06); they get entity ``"*"`` and no
    dtype gate, since no rule constrains their type.
    """
    entities: dict[str, Counter] = defaultdict(Counter)
    dtypes: dict[str, set[str]] = defaultdict(set)
    synonyms: dict[str, set[str]] = defaultdict(set)

    for row in rule_rows:
        roles = list(row.get("semantic_roles_json") or [])
        entity = row.get("entity")
        text = row.get("rule_text") or ""
        params = row.get("binding_params_json") or {}
        claimed = params.get("role_dtypes") or {}
        for hazard in (row.get("parse_hazards_json") or []):
            recorded = (hazard or {}).get("role_text")
            if recorded and recorded not in roles:
                for role in roles:
                    if collapse(role) == collapse(recorded):
                        synonyms[role].add(recorded)
        for role in roles:
            if entity:
                entities[role][entity] += 1
            kind = claimed.get(role)
            if kind in DTYPE_KINDS:
                dtypes[role].add(kind)
            synonyms[role] |= _variants_from_text(role, text)

    vocab: dict[str, dict[str, Any]] = {}
    for role in set(entities) | set(synonyms) | set(dtypes) | set(extra_roles):
        claims = sorted(dtypes.get(role, ()), key=lambda k: _DTYPE_PRECEDENCE.get(k, 9))
        # A single claim gates; disagreement falls back to the weakest claim
        # that all of them tolerate — never to a stricter one than any rule
        # actually asked for.
        dtype = claims[-1] if claims else None
        if dtype == "category":
            dtype = None  # a category claim carries no plausibility test
        ent = entities[role].most_common(1)[0][0] if entities.get(role) else "*"
        # The role's own name is always a matchable phrase: tiers 3-6 score
        # against phrases, and the role name is the only phrase the KB
        # guarantees. Tier 2 (0.92) therefore only ever fires for a genuine
        # recorded VARIANT, exactly as DX-04 requires.
        vocab[role] = {
            "entity": ent,
            "dtype": dtype,
            "synonyms": sorted({role} | synonyms.get(role, set())),
        }
    return vocab


# ============================================================================
#  SECTION 4.  Scoring ladder (S5 ``_score_column``) — ported verbatim
# ============================================================================

def score_column(role: str, meta: dict[str, Any], column: str, desc: str,
                 values: list[Any]) -> tuple[float, str]:
    """Return ``(score in [0,1], reason)`` for one (role, column) pair.

    Confidence genuinely varies by evidence quality so the number is
    informative rather than a disguised string equality. ``values`` is a
    bounded sample of the column's non-null values (the dtype gate reads at
    most 200 of them, as S5 does).
    """
    col_c = canon(column)
    col_tok = tokens(column)
    syns = meta.get("synonyms") or []

    best, why = 0.0, "no match"

    def consider(score: float, reason: str) -> None:
        nonlocal best, why
        if score > best:
            best, why = score, reason

    # 1.00 exact name == role
    if col_c == canon(role):
        consider(1.00, "column name is the role name (exact)")
    # 0.92 exact name == synonym
    for s in syns:
        if col_c == canon(s):
            consider(0.92, f"column name exactly matches synonym '{s}'")
    # 0.88 dictionary description contains a synonym phrase
    if desc:
        for s in syns:
            if canon(s) and canon(s) in canon(desc):
                consider(0.88, f"dictionary description mentions '{s}'")
    # 0.80 synonym token-set fully contained in column tokens
    for s in syns:
        st = tokens(s)
        if st and st.issubset(col_tok):
            consider(0.80, f"column tokens contain all of synonym '{s}'")
    # fuzzy character similarity, graded 0.55..0.75
    for s in syns:
        ratio = difflib.SequenceMatcher(None, col_c, canon(s)).ratio()
        if ratio >= 0.80:
            consider(0.55 + 0.20 * (ratio - 0.80) / 0.20,
                     f"name similar to '{s}' ({ratio:.2f})")
    # partial token overlap (Jaccard), graded 0.35..0.60
    for s in syns:
        st = tokens(s)
        if st and col_tok:
            jac = len(st & col_tok) / len(st | col_tok)
            if jac > 0:
                consider(0.35 + 0.25 * jac, f"partial token overlap with '{s}' ({jac:.2f})")

    # dtype plausibility gate (can veto)
    if best > 0:
        dt = meta.get("dtype")
        non_null = [v for v in values if not _is_null(v)]
        if non_null:
            if dt in ("number", "flag"):
                sample = non_null[:200]
                coerce = sum(1 for v in sample if _as_number(v) is not None)
                if coerce < 0.6 * len(sample):
                    return 0.0, "dtype mismatch (not numeric)"
            if dt == "flag":
                seen = []
                for v in non_null:
                    if v not in seen:
                        seen.append(v)
                    if len(seen) >= 12:
                        break
                vals = {str(x) for x in seen}
                if not vals.issubset({"0", "1", "0.0", "1.0", "True", "False"}):
                    return 0.0, "dtype mismatch (not binary)"
            # A 'date' claim is recorded on the manifest for provenance but
            # never vetoes: S5's gate tests numeric/binary plausibility only
            # (CFR-05 — "exactly as implemented"), and inventing a stricter
            # veto here would change which columns resolve.
    return round(best, 3), why


def _is_null(v: Any) -> bool:
    from .rules import is_missing
    return is_missing(v)


def _as_number(v: Any):
    from .rules import to_number
    return to_number(v)


# ============================================================================
#  SECTION 4b.  resolve_roles / bind_entities (S5, ported)
# ============================================================================

def resolve_roles(vocab: dict[str, dict[str, Any]],
                  columns_by_table: dict[str, list[str]],
                  samples: dict[tuple[str, str], list[Any]],
                  descriptions: dict[tuple[str, str], str] | None = None,
                  overrides: dict[str, str] | None = None) -> dict[str, RoleMatch]:
    """Resolve every role in ``vocab`` to a ``table.column`` or UNRESOLVED.

    An override always wins (score 1.0, ``via='override'``): either
    ``"table.column"`` or a bare column name looked up across the tables.
    Otherwise every (table, column) pair is scored and the best kept; a best
    below :data:`RESOLVED_FLOOR` is reported unresolved with its score.
    """
    descriptions = descriptions or {}
    overrides = overrides or {}
    resolved: dict[str, RoleMatch] = {}
    for role in sorted(vocab):
        meta = vocab[role]
        if role in overrides and overrides[role]:
            spec = str(overrides[role])
            if "." in spec:
                tbl, col = spec.split(".", 1)
            else:
                tbl, col = None, spec
                for tname, cols in columns_by_table.items():
                    if col in cols:
                        tbl = tname
                        break
            resolved[role] = RoleMatch(role, tbl, col, 1.0, "manual override", "override")
            continue
        best = RoleMatch(role, None, None, 0.0, "no candidate", "unresolved")
        for tname, cols in columns_by_table.items():
            for col in cols:
                desc = descriptions.get((tname, col), "")
                sc, why = score_column(role, meta, col, desc, samples.get((tname, col), []))
                if sc > best.score:
                    best = RoleMatch(role, tname, col, sc, why, "auto")
        if best.score < RESOLVED_FLOOR:
            best = RoleMatch(role, None, None, best.score,
                             f"best candidate too weak (score {best.score:.2f})", "unresolved")
        resolved[role] = best
    return resolved


def bind_entities(resolved: dict[str, RoleMatch],
                  vocab: dict[str, dict[str, Any]]) -> dict[str, str]:
    """S5 section 6 — each entity gets one vote per resolved role that
    belongs to it; the majority table wins. Roles whose entity is ``"*"``
    (grain/segment keys, which live in every table) never vote."""
    votes: dict[str, Counter] = defaultdict(Counter)
    for role, m in resolved.items():
        if not m.column:
            continue
        ent = (vocab.get(role) or {}).get("entity", "*")
        if ent == "*":
            continue
        votes[ent][m.table] += 1
    return {ent: ct.most_common(1)[0][0] for ent, ct in votes.items()}
