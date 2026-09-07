"""DQ Studio FastAPI app — GX backend (AI routers mounted when implemented)."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # load backend/.env (Azure OpenAI config) before anything reads it

import os  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app_version import APP_VERSION  # noqa: E402
import system_db  # noqa: E402
from routers import admin, analyses, auth, v2, v3  # noqa: E402
from ai.v2 import service as v2_service  # noqa: E402

# Restore prior state from the persistent-volume backup before touching the DB.
# A container's local disk is ephemeral; on every restart this copies the last
# snapshot from the mounted share back into place (no-op locally / on first boot).
system_db.restore_from_backup()

# Ensure the system DB exists and is fully seeded on first boot (idempotent).
# Cold start uses the full seed (users + pre-seeded DBs + static content); the
# demo *reset* action is surgical (see system_db.reset_demo) and does NOT reseed
# users, so it must not be used for the cold-start path.
system_db.init_schema()
# A RUNNING diagnostic is backed by a short persisted worker lease. Recover
# leases that expired while the API was offline before serving Test Lab state;
# active leases from another process are preserved.
from domains.test_lab.shared.run_state import recover_expired_runs  # noqa: E402

_diagnostic_recovery = recover_expired_runs()
if _diagnostic_recovery["recovered"]:
    print(
        f"[test-lab] recovered {_diagnostic_recovery['recovered']} interrupted diagnostic run(s)",
        flush=True,
    )
# Plan 6 — sync the agent topology on every boot (idempotent): backfills `seq`
# and the Lovelace->Newton rename even when users already exist (cold-start
# below would otherwise skip seeding on a pre-existing system_state.db).
from seeds import seed_agents, seed_dq_framework, seed_framework_register  # noqa: E402

seed_agents()
# DQ Framework static taxonomy — seeded every boot (idempotent), so pre-existing
# system_state.db files receive it even though cold-start seed_all is skipped.
seed_dq_framework()
# 0.4.0 framework (FWK-04): the 9-diagnostic register + L1/L2 taxonomy + test
# areas + default thresholds, seeded from knowledge_base/dq_framework_data.json
# every boot (idempotent). Replaces the retired Galileo framework seed; the
# one-time retirement migration below drops the old framework's records.
seed_framework_register()
from dq_diagnostics.migrate_040 import retire_old_framework  # noqa: E402

retire_old_framework()
# RCA Stage 0/1 — bootstrap tenant/feature-flags + governed business
# taxonomy, seeded every boot (idempotent) for the same reason as above.
from seeds import (  # noqa: E402
    seed_directionality_knowledge, seed_platform_and_taxonomy,
    seed_row_completeness_knowledge, seed_value_semantics_knowledge,
)

seed_platform_and_taxonomy()
seed_row_completeness_knowledge()
seed_directionality_knowledge()
seed_value_semantics_knowledge()
# Repair confirmed supporting-analysis observations whose issue rows were
# removed by the pre-fix retirement migration on an earlier restart.
from analysis_runtime.runs import repair_confirmed_observation_issues  # noqa: E402

repair_confirmed_observation_issues()
# Persisted v2 work is never cleared at startup.  Reconcile only identifies
# pre-0.2 records whose files lived on an ephemeral container disk.
v2_service.reconcile_persisted_items()
if not system_db.query_one("users"):
    from seeds import seed_all  # noqa: E402

    seed_all()
else:
    # Migrate pre-existing user rows to the new authz column (idempotent).
    from seeds import backfill_authz, backfill_kb_roles  # noqa: E402

    backfill_authz()
    # RCA Stage 2 — a pre-existing install predates kb_editor/kb_reviewer.
    backfill_kb_roles()

# Periodic + shutdown backup of the live local DB to the persistent volume.
# A daemon thread snapshots every BACKUP_INTERVAL seconds; the lifespan shutdown
# fires a final snapshot so a graceful stop/restart (SIGTERM) loses nothing. A
# hard crash loses at most one interval of writes. All no-ops when backups are
# disabled (SYSTEM_DB_BACKUP_PATH unset, i.e. local dev).
import threading  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

_BACKUP_INTERVAL = int(os.environ.get("SYSTEM_DB_BACKUP_INTERVAL", "60"))
_backup_stop = threading.Event()


def _backup_loop() -> None:
    while not _backup_stop.wait(_BACKUP_INTERVAL):
        try:
            system_db.backup_to_volume()
        except Exception as exc:  # never let a backup hiccup kill the thread
            print(f"[backup] periodic snapshot failed: {exc}", flush=True)


# RCA Stage 5 (WF §11 Agent 10, MP §10): "a case waiting on an owner's fix
# beyond its time-box is escalated, not left open forever" only holds in a
# running deployment if something actually sweeps for it periodically —
# rca.check_time_box_escalations() existing as a callable isn't enough.
_TIME_BOX_SWEEP_INTERVAL = int(os.environ.get("RCA_TIME_BOX_SWEEP_INTERVAL", "3600"))
_time_box_stop = threading.Event()

_DIAGNOSTIC_RUN_SWEEP_INTERVAL = max(
    10, int(os.environ.get("DIAGNOSTIC_RUN_SWEEP_INTERVAL", "30"))
)
_diagnostic_run_stop = threading.Event()


def _time_box_sweep_loop() -> None:
    from domains.rca import service as rca  # deferred: service imports system_db after boot setup

    while not _time_box_stop.wait(_TIME_BOX_SWEEP_INTERVAL):
        try:
            rca.sweep_all_tenants_time_box_escalations()
        except Exception as exc:  # never let a sweep hiccup kill the thread
            print(f"[rca] time-box sweep failed: {exc}", flush=True)


def _diagnostic_run_sweep_loop() -> None:
    """Finalize workers whose persisted lease expired without a terminal state."""
    while not _diagnostic_run_stop.wait(_DIAGNOSTIC_RUN_SWEEP_INTERVAL):
        try:
            outcome = recover_expired_runs()
            if outcome["recovered"]:
                print(
                    f"[test-lab] recovered {outcome['recovered']} interrupted diagnostic run(s)",
                    flush=True,
                )
        except Exception as exc:  # never let recovery stop the service
            print(f"[test-lab] diagnostic run recovery failed: {exc}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = None
    time_box_thread = None
    diagnostic_run_thread = None
    if system_db.SYS_DB_BACKUP_PATH:
        thread = threading.Thread(target=_backup_loop, name="db-backup", daemon=True)
        thread.start()
    time_box_thread = threading.Thread(target=_time_box_sweep_loop, name="rca-time-box-sweep", daemon=True)
    time_box_thread.start()
    diagnostic_run_thread = threading.Thread(
        target=_diagnostic_run_sweep_loop,
        name="diagnostic-run-recovery",
        daemon=True,
    )
    diagnostic_run_thread.start()
    try:
        yield
    finally:
        _backup_stop.set()
        _time_box_stop.set()
        _diagnostic_run_stop.set()
        if system_db.SYS_DB_BACKUP_PATH:
            try:
                system_db.backup_to_volume()  # final snapshot on graceful shutdown
            except Exception as exc:
                print(f"[backup] shutdown snapshot failed: {exc}", flush=True)


app = FastAPI(title="Archimedes API", version=APP_VERSION, lifespan=lifespan)

# Allowed browser origins. Local dev defaults are always present; production
# origins (the Static Web App URL) are appended via the CORS_ORIGINS env var
# (comma-separated) so the deployed URL never has to be hardcoded here.
_default_origins = ["http://localhost:5173", "http://localhost:5174", "http://localhost:5175"]
_env_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _env_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/config")
def app_config():
    """Central, frontend-consumable app config (shared AI disclaimer, the
    AI-summary character limit, AI attribution). Single source of truth lives in
    backend/app_config.py so backend and React never drift."""
    from app_config import public_config

    return public_config()


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(v2.router)
app.include_router(v3.router)
app.include_router(analyses.router)
