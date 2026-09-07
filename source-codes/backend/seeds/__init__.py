"""Plan 3 — seed orchestration. ``seed_all()`` (idempotent) re-creates static /
master data after a demo reset: default user, test_library, pre-ingested logical
DBs + table_metadata, and (when their phases land) monitoring. Tickets are
work-product only — no historical data is seeded; the table starts empty on a
fresh product (schema lives in system_db.py)."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SEEDS_DATA = Path(__file__).resolve().parent / "data"


def _users_config() -> dict:
    return json.loads((_SEEDS_DATA / "users_config.json").read_text(encoding="utf-8"))


def seed_users() -> int:
    import system_db as s
    for u in _users_config()["users"]:
        s.upsert("users", u)
    return len(_users_config()["users"])


def backfill_authz() -> int:
    """One-time migration: populate authz_roles for pre-existing user rows that
    predate the authz column. Configured users get their configured roles; any
    other existing user defaults to ['user']. Idempotent (only fills NULLs)."""
    import system_db as s
    cfg = {u["username"]: (u.get("authz_roles") or ["user"])
           for u in _users_config()["users"]}
    n = 0
    for u in s.query("users"):
        if not u.get("authz_roles"):
            s.update("users", {"username": u["username"]},
                     {"authz_roles": cfg.get(u["username"], ["user"])})
            n += 1
    return n


def seed_framework_register() -> int:
    """0.4.0 (FWK-04): seed the 9-diagnostic register, the L1/L2 taxonomy,
    the test areas and the default thresholds from
    knowledge_base/dq_framework_data.json. Replaces the retired Galileo
    framework seeds (galileo_seed.py, test_library_seed.py — FWK-13)."""
    from dq_diagnostics.register import seed_register
    return seed_register()


def seed_ingested_databases() -> int:
    """Fetch-first landing: NO databases are pre-ingested. Every logical DB is
    offered as fetchable through the Data Source wizard; the landing app
    (Inventory / Tickets / Monitoring) stays empty until the user fetches one.
    Kept as a function (returns 0) so seed_all()'s contract is unchanged."""
    import system_db as s
    pre: set[str] = set()
    if not pre:
        return 0
    n = 0
    for db in []:  # unreachable; kept as scaffold if pre-seeding is re-enabled
        if db["logical_db"] not in pre:
            continue
        # Idempotent: clear any prior rows for this logical DB before re-seeding
        # (seed_all may run more than once; insert-without-dedupe would duplicate).
        s.delete("ingested_databases", logical_db=db["logical_db"])
        s.delete("table_metadata", logical_db=db["logical_db"])
        s.insert("ingested_databases", {
            "logical_db": db["logical_db"],
            "display_name": db["display_name"],
            "description": db["description"],
            "access_role": db["access_role"],
            "dictionary": {t["table"]: t["descriptions"] for t in db["tables"]},
            "ai_summary": "",
            "created_at": s.now_ist(),
        })
        for t in db["tables"]:
            s.insert("table_metadata", {
                "logical_db": db["logical_db"], "table": t["table"],
                "pk": t["pk"], "date_col": t["date_col"],
                "columns": t["columns"], "datatypes": t["datatypes"],
                "descriptions": t["descriptions"],
                "row_count": t["row_count"], "col_count": t["col_count"],
                "selected_for_analysis": 1,
            })
        n += 1
    return n


def seed_agents() -> int:
    """Register/refresh the F6 agent topology (idempotent — safe to call every
    boot). Writes call_name/role/parent/seq so the canonical ordering and the
    Plan 6 Lovelace->Newton rename land even on a pre-existing system_state.db.
    The skill .md is seeded with the agent's REAL default prompt lazily on its
    first run (never a stub that would override it)."""
    import system_db as s
    from ai.skills import AGENTS, SKILLS_DIR
    # Retire renamed/removed agents: drop their DB row + stale .md.
    #  · lovelace (Plan 6 rename) · fisher (Newton's DiscoveryState supersedes its
    #    single-table screening; the pandas profiler survives only as a utility).
    for _dead in ("lovelace", "fisher"):
        s.delete("agent_skills", agent_key=_dead)
        _md = SKILLS_DIR / f"{_dead}.md"
        if _md.exists():
            _md.unlink()
    for a in AGENTS:
        s.upsert("agent_skills", {**a, "skill_md_path": f"skills/{a['agent_key']}.md"})
    return len(AGENTS)


def seed_dq_framework() -> int:
    """Seed the static DQ Framework reference taxonomy (idempotent — safe every
    boot). Like seed_agents(), this runs unconditionally so existing
    system_state.db files (which already have users, so cold-start seed_all is
    skipped) still receive the framework tables. Reset-preserved (not
    work-product)."""
    import system_db as s
    from seeds.dq_framework_seed import seed_area_rows, seed_family_rows
    n = 0
    for r in seed_area_rows():
        s.upsert("dq_framework_areas", r)
        n += 1
    for r in seed_family_rows():
        s.upsert("dq_framework_families", r)
        n += 1
    return n


def backfill_kb_roles() -> int:
    """RCA Stage 2 — a pre-existing install's users table predates the
    kb_editor/kb_reviewer roles added to users_config.json. backfill_authz()
    only fills NULL authz_roles, so an already-seeded user (non-null roles)
    never picks up newly-configured roles through that path — union in any
    kb_editor/kb_reviewer the config now declares for a known username,
    without touching any other role a deployment may have assigned since.
    Idempotent (a no-op once every configured user already has them)."""
    import system_db as s
    cfg = {u["username"]: set(u.get("authz_roles") or []) & {"kb_editor", "kb_reviewer"}
          for u in _users_config()["users"]}
    n = 0
    for username, wanted in cfg.items():
        if not wanted:
            continue
        row = s.query_one("users", username=username)
        if not row:
            continue
        current = set(row.get("authz_roles") or [])
        if wanted - current:
            s.update("users", {"username": username}, {"authz_roles": sorted(current | wanted)})
            n += 1
    return n


def seed_platform_and_taxonomy() -> int:
    """RCA Stage 0/1 — bootstrap tenant/feature-flags + the governed
    taxonomy (idempotent, safe every boot; see seeds/taxonomy_seed.py)."""
    from seeds.taxonomy_seed import seed_platform, seed_taxonomy
    return seed_platform() + seed_taxonomy()


def seed_row_completeness_knowledge() -> int:
    """Publish the immutable built-in T2D6 package through the KB service."""
    from domains.test_lab.diagnostics.t2_d06_row_completeness.knowledge import seed_package
    return int(bool(seed_package().get("inserted")))


def seed_directionality_knowledge() -> int:
    """Expose the immutable T2D11 directionality KB in the document browser."""
    from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.knowledge import (
        seed_document,
    )
    return int(bool(seed_document().get("inserted")))


def seed_value_semantics_knowledge() -> int:
    """Expose D08 value-semantics and shared terminology version history."""
    from domains.test_lab.diagnostics.t2_d08_value_semantics.knowledge import seed_documents
    return int(bool(seed_documents().get("inserted")))


def _optional(modname: str, fn: str) -> int:
    """Call an optional seed (tickets/monitoring) if its phase module exists."""
    try:
        mod = __import__(f"seeds.{modname}", fromlist=[fn])
    except ImportError:
        return 0
    return int(getattr(mod, fn)())


def seed_all() -> dict:
    return {
        "users": seed_users(),
        "framework_register": seed_framework_register(),
        "ingested_databases": seed_ingested_databases(),
        "agent_skills": seed_agents(),
        "dq_framework": seed_dq_framework(),
        "platform_and_taxonomy": seed_platform_and_taxonomy(),
        "row_completeness_knowledge": seed_row_completeness_knowledge(),
        "directionality_knowledge": seed_directionality_knowledge(),
        "value_semantics_knowledge": seed_value_semantics_knowledge(),
        "monitoring": _optional("monitoring_seed", "load"),
        "context_memory": _optional("context_memory_seed", "load"),
    }


def reseed_dynamic() -> dict:
    """Fetch-first re-link: after a DB is fetched through the wizard, (re)load
    the static monitoring baseline so it binds to whatever logical DBs are now
    ingested. Self-gates to 0 when nothing is ingested, so this is safe to call
    after every fetch (idempotent via upsert)."""
    return {
        "monitoring": _optional("monitoring_seed", "load"),
    }


def reseed_static() -> dict:
    """F16 — restore ONLY the static demo baseline cleared by a surgical reset
    (monitoring). Deliberately does NOT seed users (preserves F13 profile
    edits) or ingested_databases (the two pre-seeded DBs survive the reset
    untouched; the demo DB returns via live re-ingestion)."""
    return {
        "monitoring": _optional("monitoring_seed", "load"),
        "context_memory": _optional("context_memory_seed", "load"),
    }
