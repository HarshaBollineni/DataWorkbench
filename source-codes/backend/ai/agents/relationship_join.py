"""Relationship Architect agent (call-name 'Codd') + deterministic evidence harness.

Feedback Round 4.1 (2026-06-29): the Data Sourcing wizard needs cross-table ERD /
join relationships filled in by AI first (human overwrites). The existing
relationship lineage (Poincaré -> Fermat -> Euler) is scoped to within-table
DQ-check candidates for the Gauss test loop, NOT ERD join inference — so Codd is
a small, single-responsibility agent for join discovery. Named for E.F. Codd, the
father of the relational model.

HALLUCINATION CONTROL: the agent never invents joins from thin air. A
deterministic harness computes real EVIDENCE per candidate column pair (dtype
compatibility, distinct-value containment/overlap, key uniqueness, row counts)
and is the GATE — Codd may only annotate/confirm evidenced candidates, never add
unevidenced ones. Tiny tables (< MIN_ROWS rows) are flagged "insufficient data"
and default to disabled so we never declare a relationship we cannot support.
This same harness validates an uploaded relationships .json for logical
consistency.
"""
from __future__ import annotations

import json
import re

from ..effort import EffortPolicy

# A column whose name looks like an identifier/key — a join candidate.
KEY_RE = re.compile(r"(^id$|_id$|_key$|key$|_code$|code$|_no$|_num$|number$|_ref$)", re.I)
MIN_ROWS = 30          # below this, evidence is too thin to trust — flag + default OFF
HIGH_OVERLAP = 0.90    # containment thresholds for confidence banding
MED_OVERLAP = 0.50

JOIN_TYPES = ["inner", "left_join", "right_join", "full_outer"]
MAPPING_TYPES = ["one_to_one", "one_to_many", "many_to_one", "many_to_many"]

_SYSTEM = (
    "You are Codd, a relational-modelling expert. You are given a database's "
    "tables/columns and a table of DETERMINISTIC EVIDENCE for candidate join "
    "relationships (value overlap, key uniqueness, row counts). Your job is to "
    "CONFIRM or REJECT each evidenced candidate using relational + credit-risk "
    "domain sense and write a one-line plain-English description of how the two "
    "tables relate. You MUST NOT invent relationships that are not in the "
    "evidence; you may only annotate, confirm, reject, or down-rank what is "
    "given. Prefer fewer, well-supported relationships over many weak ones.\n"
    "Respond as STRICT JSON: {\"relationships\": [{\"left_table\": str, "
    "\"right_table\": str, \"left_key\": str, \"right_key\": str, \"join_type\": "
    "one of " + str(JOIN_TYPES) + ", \"relationship_type\": one of " + str(MAPPING_TYPES) + ", "
    "\"confidence\": one of [\"high\",\"medium\",\"low\"], \"keep\": bool, "
    "\"description\": str}]}. Echo left_key/right_key exactly as in the evidence "
    "row. Set keep=false for candidates you judge spurious."
)


def _dtype_kind(series) -> str:
    import pandas as pd
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return "text"


def _is_keyish(col: str, pk) -> bool:
    return col == pk or bool(KEY_RE.search(col or ""))


def _load(logical_db: str, table: str):
    from database import load_table
    try:
        return load_table(table, logical_db)
    except Exception:  # noqa: BLE001 — a bad table just yields no evidence
        return None


def _band(containment: float, insufficient: bool) -> str:
    if insufficient:
        return "low"
    if containment >= HIGH_OVERLAP:
        return "high"
    if containment >= MED_OVERLAP:
        return "medium"
    return "low"


def compute_evidence(logical_db: str, tables_meta: list[dict]) -> list[dict]:
    """Deterministic candidate-join evidence across every table pair.

    Returns one row per surviving candidate column pair (those with non-trivial
    value overlap), best-overlap first. Each row carries the numbers the UI shows
    and the LLM is grounded on.
    """
    frames: dict[str, object] = {}
    pks: dict[str, object] = {}
    for t in tables_meta:
        name = t["table"]
        frames[name] = _load(logical_db, name)
        pks[name] = t.get("pk")

    names = [t["table"] for t in tables_meta]
    out: list[dict] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            da, db_ = frames.get(a), frames.get(b)
            if da is None or db_ is None or da.empty or db_.empty:
                continue
            insufficient = len(da) < MIN_ROWS or len(db_) < MIN_ROWS
            for ca in da.columns:
                for cb in db_.columns:
                    # Candidate joins must be NAME-grounded, not value-coincident:
                    # an exact column-name match, or a column whose name IS the
                    # other table's primary key (a foreign key by name). This is
                    # deliberately conservative — differently-named numeric IDs
                    # (e.g. customer_id vs account_id) overlap by accident, so we
                    # would rather miss than declare a wrong join.
                    name_match = ca.lower() == cb.lower()
                    fk_to_pk = (pks[b] and ca == pks[b]) or (pks[a] and cb == pks[a])
                    if not (name_match or fk_to_pk):
                        continue
                    # A join key should look like a key — skip free-text coincidences.
                    if not (_is_keyish(ca, pks[a]) or _is_keyish(cb, pks[b])):
                        continue
                    sa, sb = da[ca], db_[cb]
                    if _dtype_kind(sa) != _dtype_kind(sb):
                        continue
                    va = set(sa.dropna().unique().tolist())
                    vb = set(sb.dropna().unique().tolist())
                    if not va or not vb:
                        continue
                    inter = len(va & vb)
                    if inter == 0:
                        continue
                    containment = inter / min(len(va), len(vb))
                    left_unique = len(va) == len(sa.dropna())
                    right_unique = len(vb) == len(sb.dropna())
                    # Orient parent (unique key) -> child (FK). Default many_to_one.
                    if left_unique and right_unique:
                        rel_type = "one_to_one"
                    elif left_unique and not right_unique:
                        rel_type = "one_to_many"   # a (one) -> b (many)
                    elif right_unique and not left_unique:
                        rel_type = "many_to_one"   # a (many) -> b (one)
                    else:
                        rel_type = "many_to_many"
                    out.append({
                        "left_table": a, "right_table": b,
                        "left_key": ca, "right_key": cb,
                        "overlap_count": inter, "containment": round(containment, 3),
                        "left_distinct": len(va), "right_distinct": len(vb),
                        "left_unique": left_unique, "right_unique": right_unique,
                        "n_left": int(len(da)), "n_right": int(len(db_)),
                        "join_type": "inner", "relationship_type": rel_type,
                        "confidence": _band(containment, insufficient),
                        "insufficient_data": insufficient,
                    })
    # Best evidence first; keep at most the strongest candidate per table pair so
    # the form is not flooded with near-duplicate key guesses.
    out.sort(key=lambda r: (r["containment"], r["overlap_count"]), reverse=True)
    best: dict[tuple, dict] = {}
    for r in out:
        key = (r["left_table"], r["right_table"])
        if key not in best:
            best[key] = r
    return list(best.values())


def discover(logical_db: str, tables_meta: list[dict], dictionary: dict | None = None,
             on_event=None) -> dict:
    """AI-first relationship fill: deterministic evidence -> Codd annotation.

    The evidence is authoritative for which pairs EXIST; Codd confirms/rejects and
    writes the description. Returns {relationships, evidence} where each
    relationship carries confidence + an enabled-by-default flag (high/medium ON,
    low/insufficient OFF — better not to declare than declare wrong).
    """
    evidence = compute_evidence(logical_db, tables_meta)
    if not evidence:
        return {"relationships": [], "evidence": []}

    by_pair = {(e["left_table"], e["right_table"], e["left_key"], e["right_key"]): e
               for e in evidence}
    schema = {t["table"]: (t.get("columns") or []) for t in tables_meta}
    user = (
        f"TABLES:\n{json.dumps(schema)[:2500]}\n\n"
        f"DICTIONARY:\n{json.dumps(dictionary or {})[:1500]}\n\n"
        f"DETERMINISTIC EVIDENCE (candidate joins):\n{json.dumps(evidence)[:4000]}"
    )
    annotated: dict[tuple, dict] = {}
    try:
        from ..skills import get_system_prompt
        policy = EffortPolicy(agent_key="codd")
        raw = policy.run(
            [{"role": "system", "content": get_system_prompt("codd", _SYSTEM)},
             {"role": "user", "content": user}], json_mode=True, on_event=on_event)
        for r in json.loads(raw).get("relationships", []):
            k = (r.get("left_table"), r.get("right_table"), r.get("left_key"), r.get("right_key"))
            if k in by_pair:  # GATE — ignore anything not in the evidence
                annotated[k] = r
    except Exception:  # noqa: BLE001 — degrade to pure-evidence suggestions
        annotated = {}

    rels = []
    for e in evidence:
        k = (e["left_table"], e["right_table"], e["left_key"], e["right_key"])
        ann = annotated.get(k, {})
        if ann.get("keep") is False:
            continue
        conf = ann.get("confidence") or e["confidence"]
        rels.append({
            "left_table": e["left_table"], "right_table": e["right_table"],
            "left_key": e["left_key"], "right_key": e["right_key"],
            "join_type": ann.get("join_type") or e["join_type"],
            "relationship_type": ann.get("relationship_type") or e["relationship_type"],
            "confidence": conf,
            "enabled": conf in ("high", "medium") and not e["insufficient_data"],
            "insufficient_data": e["insufficient_data"],
            "containment": e["containment"], "overlap_count": e["overlap_count"],
            "description": ann.get("description")
                or f"{e['left_table']} relates to {e['right_table']} on {e['left_key']}.",
        })
    return {"relationships": rels, "evidence": evidence}


def validate_relations(logical_db: str, tables_meta: list[dict],
                       relations: list[dict]) -> dict:
    """Validate an uploaded relationships list for logical consistency.

    Errors (block) vs warnings (allow, but flag). Returns
    {ok, errors, warnings, normalized} where normalized is the cleaned list ready
    to populate the on-screen form.
    """
    cols_by_table = {t["table"]: set(t.get("columns") or []) for t in tables_meta}
    known = set(cols_by_table)
    # Evidence keyed by (lt,rt,lk,rk) and its reverse, for an overlap sanity check.
    ev = {(e["left_table"], e["right_table"], e["left_key"], e["right_key"]): e
          for e in compute_evidence(logical_db, tables_meta)}

    errors: list[str] = []
    warnings: list[str] = []
    normalized: list[dict] = []
    seen: set[tuple] = set()

    if not isinstance(relations, list):
        return {"ok": False, "errors": ["Top-level JSON must be a list of relationships "
                                        "(or {\"relationships\": [...] })."],
                "warnings": [], "normalized": []}

    for idx, r in enumerate(relations):
        tag = f"Relationship #{idx + 1}"
        if not isinstance(r, dict):
            errors.append(f"{tag}: must be an object."); continue
        lt, rt = r.get("left_table"), r.get("right_table")
        lk, rk = r.get("left_key"), r.get("right_key")
        if not lt or not rt:
            errors.append(f"{tag}: missing left_table/right_table."); continue
        if lt not in known:
            errors.append(f"{tag}: unknown table '{lt}'."); continue
        if rt not in known:
            errors.append(f"{tag}: unknown table '{rt}'."); continue
        if lt == rt:
            warnings.append(f"{tag}: self-relationship on '{lt}'.")
        if lk and lk not in cols_by_table[lt]:
            errors.append(f"{tag}: column '{lk}' not in table '{lt}'."); continue
        if rk and rk not in cols_by_table[rt]:
            errors.append(f"{tag}: column '{rk}' not in table '{rt}'."); continue
        jt = r.get("join_type") or "inner"
        if jt not in JOIN_TYPES:
            errors.append(f"{tag}: invalid join_type '{jt}' (allowed: {JOIN_TYPES})."); continue
        mt = r.get("relationship_type") or "many_to_one"
        if mt not in MAPPING_TYPES:
            errors.append(f"{tag}: invalid relationship_type '{mt}' (allowed: {MAPPING_TYPES})."); continue
        dup = (lt, rt, lk, rk)
        if dup in seen:
            warnings.append(f"{tag}: duplicate of an earlier relationship — ignored."); continue
        seen.add(dup)
        # Logical-consistency check: do the joined columns actually share values?
        e = ev.get((lt, rt, lk, rk)) or ev.get((rt, lt, rk, lk))
        if lk and rk:
            if e is None:
                warnings.append(f"{tag}: '{lt}.{lk}' and '{rt}.{rk}' share no overlapping "
                                "values in the data — this relationship may be wrong.")
            elif e["insufficient_data"]:
                warnings.append(f"{tag}: very few rows to verify '{lt}.{lk}' ↔ '{rt}.{rk}' — "
                                "confirm manually.")
        normalized.append({
            "left_table": lt, "right_table": rt, "left_key": lk or "", "right_key": rk or "",
            "join_type": jt, "relationship_type": mt,
            "description": r.get("description") or f"{lt} ↔ {rt}",
            "confidence": (e or {}).get("confidence", "medium") if (lk and rk) else "medium",
            "enabled": True, "source": "upload",
        })
    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings,
            "normalized": normalized}
