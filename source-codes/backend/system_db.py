"""Plan 3 / F1 — System State Database (SQLite, FSM-style state store).

A new SQLite file beside the physical warehouse, holding ALL mutable platform
state (users, FSM, ingestion, test library/plans, runs, scores, tickets, agents,
audit). Master/physical data (`portfolio_alt_master.db`, schema JSONs) is never
touched here.

Accessed with the same raw-sqlite3 + dict style as ``database.py``. Helpers are
thin and typed: ``get_conn``, ``init_schema``, ``reset_demo``, ``insert``,
``update``, ``query``, ``query_one``, ``execute``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Mutable-state DB location. Defaults to the ignored local-runtime boundary. In
# containerized deploys set SYSTEM_DB_PATH to a mounted persistent volume
# (e.g. /data/system_state.db on an Azure File Share) so state survives restarts.
SYS_DB_PATH = Path(os.environ.get("SYSTEM_DB_PATH")
                   or (Path(__file__).resolve().parent / ".runtime" / "system_state.db"))
SYS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
ANALYSIS_ARTIFACT_ROOT = Path(os.environ.get("ANALYSIS_ARTIFACT_DIR")
                              or (Path(__file__).resolve().parent / "analysis_artifacts")).resolve()

# Off-box durability. Azure Files (SMB) CANNOT host a live SQLite file — its CIFS
# mount rejects the byte-range locks SQLite needs ("database is locked" on the
# first write). So the live DB stays on fast local disk (SYS_DB_PATH) and is
# mirrored to a persistent volume: restored on boot, then snapshotted
# periodically + on shutdown. Set SYSTEM_DB_BACKUP_PATH to a path on the mounted
# share (e.g. /data/system_state.db) to enable it; unset => pure local (dev).
_BACKUP_ENV = os.environ.get("SYSTEM_DB_BACKUP_PATH")
SYS_DB_BACKUP_PATH = Path(_BACKUP_ENV) if _BACKUP_ENV else None

# JSON-typed columns per table — encoded on insert/update, decoded on read.
_JSON_COLS: dict[str, set[str]] = {
    "app_fsm": {"context"},
    "users": {"authz_roles"},
    "ingested_databases": {"dictionary", "metadata", "relations"},
    "table_metadata": {"columns", "datatypes", "descriptions"},
    "test_library": {"applicability", "thresholds", "input_spec"},
    "test_plan": {"test_ref", "operands"},
    "test_dossiers": {"dialogue"},
    "test_versions": {"snapshot"},
    "design_workbench": {"scope", "plan_ids"},
    "schedules": {"plan_ids", "recipients"},
    "notifications": {"recipients", "summary"},
    "monitoring": {"fields", "history", "actions"},
    "tickets": {"issue_context", "linkage", "resolution_path", "resolution_updates"},
    "hitl_decisions": {"payload"},
    "transaction_log": {"payload"},
    "health_scores": {"category_scores"},
    "object_contexts": {"tags"},
    # DQ Framework (static reference taxonomy — seeded every boot, reset-preserved).
    "dq_framework_areas": {"key_risks", "diagnostics", "evidence", "remediation",
                           "importance_by_family"},
    "dq_item_tables": {"columns"},
    "variable_inventory": {"discrepancies", "profile_json", "missing_value_codes_json"},
    # Phase 4 — ingestion redesign (ING-08 persisted substrate).
    "dq_item_mappings": {"missing_value_codes_json"},
    "dq_item_warnings": set(),
    "fw_areas": {"stage1_tests_json", "stage2_tests_json"},
    "fw_tests": {"supporting_columns_json", "key_parameters_json"},
    "plan_v2": {"columns_json", "params_json", "crossval_json"},
    "results_v2": {"threshold_json", "evidence_json", "columns_json"},
    "scores_v2": {"breakdown_json"},
    "issues_v2": {"columns_json", "threshold_json", "column_details_json"},
    "kb_document_versions": {"conversion_warnings_json", "conversion_report_json"},
    "kb_rules": {"related_tables_json", "related_columns_json", "related_tables_schema_hash_json",
                "semantic_roles_json", "encoded_exceptions_json", "binding_params_json",
                "parse_hazards_json", "proposal_metadata_json"},
    "kb_retrieval_manifests": {"rule_ids_json", "eligibility_reasons_json"},
    "diagnostic_kb_packages": {"package_json", "validation_json"},
    "rca_cases": {"tag_snapshot_json"},
    "rca_state_transitions": {"evidence_ids_json"},
    "rca_failure_groups": {"two_signal_evidence_json"},
    "rca_case_files": {"checklist_json", "schema_snapshot_json", "tags_snapshot_json", "complaint_json"},
    "rca_looks": {"fork_json"},
    "rca_look_executions": {"summary_json"},
    "rca_suspects": {"member_cause_ids_json"},
    "rca_coverage_passes": {"raw_evidence_ref_json", "history_nominations_json"},
    "rca_hypotheses": {"evidence_look_ids_json", "confirm_check_json", "reject_condition_json"},
    "rca_fix_proposals": {"options_json"},
    "rca_audit_events": {"before_json", "after_json"},
    # Phase 3 — diagnostic register + semantic layer (FWK-04/06; docs/0.4.0/00-framework.md).
    "diagnostic_register": {"l2_areas_json"},
    "framework_taxonomy": {"coverage_diagnostics_json"},
    "threshold_settings": {"value_json"},
    # Phase 6 — Test Lab run records (testlab-redesign-0.4.0.md §5.2, CFR-12/14).
    "diag_runs": {"manifest_json", "engine_versions_json"},
    "diag_run_decisions": {"payload_json"},
    "diag_inference_events": {"payload_json"},
    "diag_results": {"metrics_json", "thresholds_used_json", "scope_counts_json"},
    "diag_findings": {"exceptions_json", "evidence_json", "resolved_roles_json"},
    "diag_dispositions": set(),
    # 0.5.0 Step 3 (AST) — the asset/version/snapshot model (PLT-02 seam).
    "dq_items": {"column_type_map_json", "dictionary_header_mapping_json",
                 "dictionary_value_mapping_json", "source_parsing_options_json",
                 "file_context_json"},
    "dq_asset_versions": {"reference_schema_json"},
    "dq_asset_dictionaries": {"parsed_json"},
    "dq_asset_events": {"detail_json"},
    "usage_events": {"detail_json"},
    "analysis_artifacts": {"source_artifact_ids_json", "identity_json", "summary_json",
                           "source_artifacts_json"},
    "diag_binning_revisions": {"definition_json", "bins_json", "metrics_json",
                                "warnings_json", "governance_json"},
    "analysis_manifests": {"manifest_json", "readiness_json", "result_json", "artifact_ids_json"},
    "analysis_observations": {"payload_json"},
    "analysis_artifact_events": {"detail_json"},
}

# ---- DDL (F1 table list) ----------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, password TEXT, name TEXT, email TEXT,
    function TEXT, role TEXT, salutation TEXT, call_name TEXT,
    ai_personality TEXT, theme TEXT, authz_roles TEXT
);
CREATE TABLE IF NOT EXISTS app_fsm (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT,
    state TEXT, context TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ingested_databases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, logical_db TEXT, display_name TEXT,
    description TEXT, access_role TEXT, dictionary TEXT, ai_summary TEXT,
    metadata TEXT, relations TEXT, fetched_at TEXT, record_count INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS table_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT, logical_db TEXT, "table" TEXT,
    pk TEXT, date_col TEXT, columns TEXT, datatypes TEXT, descriptions TEXT,
    row_count INTEGER, col_count INTEGER, selected_for_analysis INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS test_library (
    test_id TEXT PRIMARY KEY, category TEXT, name TEXT, description_en TEXT,
    applicability TEXT, input_spec TEXT, python_code TEXT, thresholds TEXT,
    criticality TEXT, background TEXT, rationale TEXT, usability TEXT,
    interpretation TEXT, source TEXT, version TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS test_plan (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT, "table" TEXT, test_ref TEXT,
    status TEXT, criticality TEXT, created_at TEXT, instance_key TEXT,
    operands TEXT
);
-- Test Lab Phase 1: DB<->test mapping catalogue. One row per (test, operand DB)
-- so a multivariate test spanning two DBs links to both. Drives cascade delete
-- (a DB delete removes every test referencing it) and the catalogue views.
CREATE TABLE IF NOT EXISTS test_db_links (
    link_id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, logical_db TEXT,
    created_at TEXT
);
-- Test Lab Phase 2A: the Dossier for a designed test (one row per plan_id) — the
-- well-formatted box (rationale -> outcome -> contextualization -> strategy ->
-- monitoring cadence) plus the HITL shuttle dialogue. current_version points into
-- test_versions for undo/redo.
CREATE TABLE IF NOT EXISTS test_dossiers (
    dossier_id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER,
    current_version INTEGER, rationale TEXT, quantitative_outcome TEXT,
    contextualization TEXT, strategy TEXT, monitor_recommended INTEGER DEFAULT 0,
    monitor_frequency TEXT, dialogue TEXT, status TEXT DEFAULT 'designing',
    created_at TEXT, updated_at TEXT
);
-- Test Lab Phase 2A: append-only version snapshots powering undo/redo + the HITL
-- history. snapshot holds {operands, params, thresholds, python_code, dossier}.
CREATE TABLE IF NOT EXISTS test_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, seq INTEGER,
    parent_version INTEGER, author TEXT, change_note TEXT, snapshot TEXT,
    created_at TEXT
);
-- Test Lab Phase 4: the Design Workbench — a named, resumable working session that
-- parks a scope (dbs + families) plus the set of in-progress design plan_ids, so a
-- multi-day design effort can be saved and picked back up. Additive; drives the
-- "Workbench" save/resume surface. plan_ids are soft references into test_plan.
CREATE TABLE IF NOT EXISTS design_workbench (
    workbench_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner TEXT,
    scope TEXT, plan_ids TEXT, notes TEXT, status TEXT DEFAULT 'open',
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS monitoring (
    monitoring_id TEXT PRIMARY KEY, logical_db TEXT, "table" TEXT, fields TEXT,
    test_id TEXT, frequency TEXT, run_started TEXT, last_run TEXT,
    history TEXT, actions TEXT
);
-- Test Lab Phase 5: a monitoring SCHEDULE over one or more designed tests (plan_ids
-- soft-refs into test_plan) at a cadence, with a delivery-channel seam (download
-- today; email stubbed — no SMTP wired). There is NO background runner: schedules
-- are executed on demand via run/tick (config + manual-run now, per the proposal).
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, plan_ids TEXT,
    frequency TEXT, delivery_channel TEXT DEFAULT 'download', recipients TEXT,
    enabled INTEGER DEFAULT 1, next_run TEXT, last_run TEXT, last_status TEXT,
    created_at TEXT, updated_at TEXT
);
-- Test Lab Phase 5: the design-only notification OUTBOX. A schedule run on the
-- 'email' channel COMPOSES a notification here (subject + markdown body) but it is
-- never actually sent (status stays 'drafted'); the seam is modelled so wiring SMTP
-- later is config, not code.
CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER,
    subject TEXT, body_md TEXT, channel TEXT DEFAULT 'email', recipients TEXT,
    summary TEXT, status TEXT DEFAULT 'drafted', created_at TEXT
);
CREATE TABLE IF NOT EXISTS run_results (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, "table" TEXT, test_id TEXT,
    passed INTEGER, observed TEXT, expected TEXT, detail TEXT, criticality TEXT,
    category TEXT, run_at TEXT, plan_id INTEGER, instance_key TEXT
);
CREATE TABLE IF NOT EXISTS health_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT, scope_id TEXT,
    category_scores TEXT, final_score REAL, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_no TEXT PRIMARY KEY, title TEXT, description TEXT, issue_context TEXT,
    mitigation_plan TEXT, owner TEXT, status TEXT, priority TEXT, open_date TEXT,
    close_date TEXT, category TEXT, test_criteria TEXT, linkage TEXT,
    resolution_path TEXT, resolution_updates TEXT
);
CREATE TABLE IF NOT EXISTS hitl_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow TEXT, entity_id TEXT,
    decision TEXT, payload TEXT, user TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS agent_skills (
    agent_key TEXT PRIMARY KEY, call_name TEXT, role TEXT, parent_key TEXT,
    skill_md_path TEXT, effort_tier TEXT, descriptor TEXT
);
CREATE TABLE IF NOT EXISTS transaction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, event TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS object_contexts (
    context_id TEXT PRIMARY KEY, scope TEXT, logical_db TEXT,
    object_type TEXT, object_key TEXT, context_type TEXT, title TEXT,
    content TEXT, source TEXT, confidence REAL, token_estimate INTEGER,
    priority INTEGER, tags TEXT, expires_at TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS context_links (
    link_id TEXT PRIMARY KEY, from_context_id TEXT, to_object_type TEXT,
    to_object_key TEXT, relation TEXT, weight REAL
);
CREATE TABLE IF NOT EXISTS dq_framework_areas (
    area_id TEXT PRIMARY KEY, l1_theme TEXT, l2_area TEXT, objective TEXT,
    why_it_matters TEXT, key_risks TEXT, diagnostics TEXT, evidence TEXT,
    thresholds TEXT, analytical_impact TEXT, remediation TEXT,
    importance_by_family TEXT, in_scope INTEGER DEFAULT 1, seq INTEGER
);
CREATE TABLE IF NOT EXISTS dq_framework_families (
    family_id TEXT PRIMARY KEY, name TEXT, most_critical_areas TEXT,
    retail_considerations TEXT, wholesale_considerations TEXT,
    typical_risks TEXT, priority_diagnostics TEXT, analytical_impacts TEXT,
    in_scope INTEGER DEFAULT 1, seq INTEGER
);
CREATE TABLE IF NOT EXISTS dq_items (
    item_id TEXT PRIMARY KEY, kind TEXT, name TEXT, status TEXT,
    module_tag TEXT, target_variable TEXT, use_case TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS dq_item_files (
    file_id TEXT PRIMARY KEY, item_id TEXT, role TEXT, filename TEXT,
    path TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS dq_item_tables (
    item_id TEXT, table_name TEXT, row_count INTEGER, col_count INTEGER,
    columns TEXT, PRIMARY KEY (item_id, table_name)
);
CREATE TABLE IF NOT EXISTS variable_inventory (
    item_id TEXT, table_name TEXT, column_name TEXT, classification TEXT,
    data_type TEXT, description TEXT, discrepancies TEXT, notes TEXT,
    role TEXT, profile_json TEXT,
    updated_at TEXT, PRIMARY KEY (item_id, table_name, column_name)
);
-- Phase 4 (0.4.0) — ingestion redesign (ING-08 persisted substrate,
-- docs/0.4.0/06-ingestion-contract.md §6). One row per dictionary-declared
-- column per table: the confidence-tiered mapping onto an actual dataset
-- column (ING-03). Field names match the contract exactly — Phase 6's Test
-- Lab manifest / CFR-05 role resolution read these (ingest/records.py).
CREATE TABLE IF NOT EXISTS dq_item_mappings (
    item_id TEXT, table_name TEXT, canonical_field TEXT,
    source_column TEXT, tier TEXT, score REAL, status TEXT,
    confirmed_by TEXT, dtype TEXT, definition TEXT, declared_type TEXT,
    updated_at TEXT, PRIMARY KEY (item_id, table_name, canonical_field)
);
-- Phase 4 — structured, non-blocking dictionary-validation findings (ING-04).
-- column_name avoids the "column" reserved word on disk; ingest/records.py
-- exposes it back out as {column, code, message} per the contract.
CREATE TABLE IF NOT EXISTS dq_item_warnings (
    warning_id TEXT PRIMARY KEY, item_id TEXT, table_name TEXT,
    column_name TEXT, code TEXT, message TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS fw_areas (
    area_id TEXT PRIMARY KEY, l1_theme TEXT, l2_area TEXT, objective TEXT,
    why_it_matters TEXT, key_risks TEXT, diagnostics TEXT, evidence TEXT,
    thresholds TEXT, analytical_impact TEXT, remediation TEXT,
    applicable_families TEXT, stage TEXT, level_evaluated TEXT,
    stage1_tests_json TEXT, stage2_tests_json TEXT, seq INTEGER
);
CREATE TABLE IF NOT EXISTS fw_tests (
    test_name TEXT PRIMARY KEY, stage TEXT, what_it_tests TEXT, runs_on TEXT,
    not_suitable_for TEXT, supporting_columns_json TEXT, key_parameters_json TEXT,
    output_evidence TEXT, caveats TEXT
);
CREATE TABLE IF NOT EXISTS fw_family_weights (
    area_id TEXT, family TEXT, criticality TEXT, PRIMARY KEY (area_id, family)
);
CREATE TABLE IF NOT EXISTS plan_v2 (
    row_id TEXT PRIMARY KEY, item_id TEXT, table_name TEXT, scope TEXT,
    test_name TEXT, area_id TEXT, origin TEXT, status TEXT, columns_json TEXT,
    params_json TEXT, reason TEXT, snippet_code TEXT, approved INTEGER DEFAULT 0,
    trigger TEXT, crossval_json TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS results_v2 (
    result_id TEXT PRIMARY KEY, row_id TEXT, item_id TEXT, table_name TEXT,
    scope TEXT, test_name TEXT, status TEXT, metric REAL, threshold_json TEXT,
    violation_count INTEGER, evidence_json TEXT, columns_json TEXT,
    not_runnable_reason TEXT, watch_note INTEGER DEFAULT 0,
    origin TEXT, area_id TEXT, run_at TEXT
);
CREATE TABLE IF NOT EXISTS scores_v2 (
    item_id TEXT, scope TEXT, provisional REAL, final REAL, breakdown_json TEXT,
    stage TEXT, computed_at TEXT, PRIMARY KEY (item_id, scope)
);
CREATE TABLE IF NOT EXISTS issues_v2 (
    issue_row_id TEXT PRIMARY KEY, item_id TEXT, table_name TEXT,
    test_name TEXT, area_id TEXT, criticality TEXT,
    columns_json TEXT, violation_count INTEGER, threshold_json TEXT,
    column_details_json TEXT,
    metric REAL, status TEXT DEFAULT 'Open',
    resolution_rationale TEXT, tracked_issue_id TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tracked_issues_v2 (
    issue_id TEXT PRIMARY KEY,
    issue_row_id TEXT, title TEXT, description TEXT,
    owner TEXT, priority TEXT, target_date TEXT,
    status TEXT DEFAULT 'Open', created_at TEXT
);
-- RCA Stage 0/1 — see docs/rca/00-contracts.md. Bootstrap tenant model
-- (one row today) + a config-row feature-flag table so rollout is
-- controlled per-flag rather than by environment variable / redeploy.
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY, name TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS feature_flags (
    key TEXT NOT NULL, tenant_id TEXT NOT NULL, enabled INTEGER DEFAULT 0,
    updated_at TEXT, PRIMARY KEY (key, tenant_id)
);
-- RCA Stage 1 — governed business taxonomy (contracts.md §7). Dimensions/
-- values are data, never hardcoded frontend constants; every assignment
-- records its taxonomy version + origin so downstream snapshots never rewrite
-- history when a source tag later changes.
CREATE TABLE IF NOT EXISTS tag_dimensions (
    dimension_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, key TEXT NOT NULL,
    label TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS tag_values (
    value_id TEXT PRIMARY KEY, dimension_id TEXT NOT NULL, key TEXT NOT NULL,
    label TEXT NOT NULL, deprecated INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS tag_aliases (
    alias_id TEXT PRIMARY KEY, value_id TEXT NOT NULL, alias_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_taxonomy_versions (
    version_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, seq INTEGER NOT NULL,
    published_at TEXT
);
CREATE TABLE IF NOT EXISTS tag_assignments (
    assignment_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    object_type TEXT NOT NULL, object_id TEXT NOT NULL, value_id TEXT NOT NULL,
    taxonomy_version_id TEXT NOT NULL,
    origin TEXT NOT NULL, source_object_type TEXT, source_object_id TEXT,
    actor TEXT, ts TEXT,
    removed_at TEXT, removed_reason TEXT, removed_by TEXT
);
-- RCA Stage 2 — governed Knowledge Base (contracts.md §6). Hierarchy:
-- KnowledgeDocument -> KnowledgeDocumentVersion -> KnowledgeSection ->
-- KnowledgeRule. No LLM ever writes here directly (backend/kb.py is the only
-- writer; publish/archive require the configured human role).
CREATE TABLE IF NOT EXISTS kb_documents (
    document_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT,
    category_hint TEXT, is_synthetic INTEGER DEFAULT 0,
    created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_document_versions (
    version_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version_seq INTEGER,
    original_filename TEXT, original_media_type TEXT, original_sha256 TEXT,
    original_bytes_ref TEXT, original_size INTEGER,
    converted_markdown TEXT, converted_markdown_sha256 TEXT,
    converter_name TEXT, converter_version TEXT, conversion_warnings_json TEXT,
    conversion_report_json TEXT,
    review_state TEXT DEFAULT 'draft', reviewer TEXT, reviewed_at TEXT,
    created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_sections (
    section_id TEXT PRIMARY KEY, version_id TEXT NOT NULL, heading TEXT,
    body_markdown TEXT, order_seq INTEGER
);
CREATE TABLE IF NOT EXISTS kb_rules (
    rule_id TEXT PRIMARY KEY, section_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL, version_id TEXT NOT NULL,
    rule_hash TEXT, rule_text TEXT, category TEXT,
    trust_level TEXT DEFAULT 'inferred', lifecycle_state TEXT DEFAULT 'draft',
    effective_date TEXT, last_confirmed_date TEXT, shelf_life_months INTEGER,
    owner TEXT, reviewer TEXT, approver TEXT,
    related_tables_json TEXT, related_columns_json TEXT,
    related_tables_schema_hash_json TEXT,
    superseded_by_rule_id TEXT, under_suspicion_reason TEXT,
    proposal_kind TEXT, proposal_subject_key TEXT, proposal_decision_hash TEXT,
    based_on_rule_id TEXT, proposal_metadata_json TEXT,
    is_synthetic INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_rule_proposal_evidence (
    evidence_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL, source_item_id TEXT,
    canonical_feature TEXT, expected_direction TEXT,
    representation_orientation TEXT, rationale TEXT,
    decision_hash TEXT, evidence_state TEXT DEFAULT 'supporting',
    actor TEXT, created_at TEXT,
    UNIQUE(rule_id, source_run_id)
);
CREATE TABLE IF NOT EXISTS kb_shelf_life_defaults (
    category TEXT PRIMARY KEY, months INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_retrieval_manifests (
    manifest_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    requesting_agent TEXT, case_id TEXT,
    rule_ids_json TEXT, eligibility_reasons_json TEXT, ranked INTEGER DEFAULT 0,
    created_at TEXT
);
-- RCA Stage 3 — case/evidence/hypothesis/closure record model
-- (contracts.md §3). Frozen at Stage 0; Stage 3 populates cases/
-- state_transitions/failure_groups/case_files/looks/look_executions/
-- suspects/suspect_history/hypotheses/confirmation_checks/judge_decisions/
-- fix_proposals/fix_approvals/closures/audit_events. coverage_passes/
-- human_questions/symptom_accounting/attached_failures are schema-ready for
-- Stage 4/5, which add the logic that writes them.
CREATE TABLE IF NOT EXISTS rca_cases (
    case_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    issue_row_id TEXT NOT NULL, item_id TEXT, table_name TEXT,
    state TEXT NOT NULL, part TEXT,
    tag_snapshot_json TEXT, complaint_text TEXT,
    created_by TEXT, created_at TEXT, updated_at TEXT, closed_at TEXT,
    contract_version TEXT DEFAULT '1'
);
CREATE TABLE IF NOT EXISTS rca_state_transitions (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, prev_state TEXT, new_state TEXT,
    actor TEXT, reason TEXT, evidence_ids_json TEXT, ts TEXT,
    workflow_version TEXT DEFAULT 'rca', contract_version TEXT DEFAULT '1'
);
CREATE TABLE IF NOT EXISTS rca_failure_groups (
    group_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, case_id TEXT NOT NULL,
    representative_run_id INTEGER, two_signal_evidence_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_attached_failures (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL, run_id INTEGER,
    reconciliation_status TEXT DEFAULT 'pending', reconciled_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_case_files (
    case_id TEXT PRIMARY KEY, checklist_json TEXT, schema_snapshot_json TEXT,
    tags_snapshot_json TEXT, complaint_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_looks (
    look_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, seq INTEGER,
    kind TEXT, proposed_by TEXT, fork_json TEXT, sql_or_helper_ref TEXT,
    budget_counted INTEGER DEFAULT 1, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_look_executions (
    execution_id TEXT PRIMARY KEY, look_id TEXT NOT NULL, status TEXT,
    summary_json TEXT, crashed INTEGER DEFAULT 0, retried INTEGER DEFAULT 0,
    executed_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_suspects (
    suspect_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, kind TEXT DEFAULT 'single',
    member_cause_ids_json TEXT, status TEXT, origin TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_suspect_history (
    id TEXT PRIMARY KEY, suspect_id TEXT NOT NULL, prev_status TEXT, new_status TEXT,
    reason TEXT, look_id TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS rca_human_questions (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, question TEXT, asked_by_look_id TEXT,
    answer TEXT, answered_by TEXT, answered_at TEXT, knowledge_rule_id_out TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_coverage_passes (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, pass INTEGER,
    raw_evidence_ref_json TEXT, history_nominations_json TEXT,
    added_suspect_id TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS rca_hypotheses (
    hypothesis_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, suspect_id TEXT,
    statement TEXT, label TEXT, tier TEXT,
    evidence_look_ids_json TEXT, confirm_check_json TEXT, reject_condition_json TEXT,
    owner TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_confirmation_checks (
    check_id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL, order_rank INTEGER,
    cost_hint REAL, status TEXT DEFAULT 'pending', executed_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_judge_decisions (
    id TEXT PRIMARY KEY, check_id TEXT NOT NULL, verdict TEXT,
    refined INTEGER DEFAULT 0, reasoning TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS rca_symptom_accounting (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, total_symptom_size REAL,
    explained_size REAL, remaining_size REAL, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_fix_proposals (
    id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL, options_json TEXT,
    routed_to TEXT, label TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_fix_approvals (
    id TEXT PRIMARY KEY, fix_proposal_id TEXT NOT NULL,
    approved_by TEXT, approved_at TEXT,
    applied_confirmed_by TEXT, applied_confirmed_at TEXT,
    time_box_deadline TEXT, escalated_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_closures (
    case_id TEXT PRIMARY KEY, outcome TEXT, rerun_run_id TEXT,
    frozen_snapshot INTEGER DEFAULT 1, fresh_snapshot_warning TEXT,
    knowledge_draft_id TEXT, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS rca_audit_events (
    event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, actor TEXT, event_type TEXT,
    object_type TEXT, object_id TEXT, before_json TEXT, after_json TEXT,
    reason TEXT, ts TEXT
);
-- Phase 3 (0.4.0) — the diagnostic register + framework taxonomy + test areas
-- (docs/0.4.0/00-framework.md, FWK-04/17/18). Seeded as data by
-- dq_diagnostics.register.seed_register(); this is THE register, not a cache
-- of one — every later phase reads it instead of re-parsing the JSON.
CREATE TABLE IF NOT EXISTS diagnostic_register (
    diagnostic_id INTEGER PRIMARY KEY, area TEXT, mode TEXT, name TEXT,
    what_it_computes TEXT, metric TEXT, threshold_based TEXT,
    threshold_rule_default TEXT, det_stat TEXT, decision_type TEXT,
    stage TEXT, kb_dependency TEXT, workflow_status TEXT,
    l2_areas_json TEXT, enabled_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS framework_taxonomy (
    l2_id TEXT PRIMARY KEY, l1_theme TEXT, name TEXT, objective TEXT,
    why_it_matters TEXT, coverage_status TEXT, coverage_reason TEXT,
    coverage_diagnostics_json TEXT
);
CREATE TABLE IF NOT EXISTS framework_test_areas (
    area_id TEXT PRIMARY KEY, name TEXT, stage TEXT, kb_dependency TEXT,
    plan_a_dimensions TEXT
);
-- Phase 3 — the semantic layer (FWK-06): live threshold values, never a
-- Python constant. scope='default' rows come from seed_register(); scope in
-- ('dimension','engagement') rows are written by dq_diagnostics.thresholds.
-- set_threshold(), each paired with its own transaction_log audit row.
CREATE TABLE IF NOT EXISTS threshold_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, diagnostic_id INTEGER, key TEXT,
    value_json TEXT, scope TEXT CHECK(scope IN ('default','dimension','engagement')),
    scope_ref TEXT, actor TEXT, ts TEXT
);
-- Phase 6 (0.4.0) — the Test Lab's run records (testlab-redesign-0.4.0.md
-- §5.2). The manifest IS the provenance: what the user approved is frozen
-- here at Run and every result is re-derivable from it alone (6-T12/CFR-16).
CREATE TABLE IF NOT EXISTS diag_runs (
    run_id TEXT PRIMARY KEY, item_id TEXT, diagnostic_id INTEGER,
    manifest_json TEXT, status TEXT, engine_versions_json TEXT,
    created_at TEXT, started_at TEXT, finished_at TEXT,
    artifact_origin TEXT NOT NULL DEFAULT 'unclassified'
);
-- CFR-12 — every scope-gate edit (and every documented default an
-- unattended run fell back on) is an append-only decision record (PLT-05).
CREATE TABLE IF NOT EXISTS diag_run_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
    kind TEXT CHECK(kind IN ('role_override','threshold_tune','scope_exclusion',
                             'default_applied','role_verification_change')),
    payload_json TEXT, actor TEXT, ts TEXT
);
-- Diagnostic AI/deterministic-inference provenance. This is append-only at the
-- service boundary. verdict_influenced is constrained to false because model
-- output may advise pre-freeze bindings but may never determine a diagnostic
-- metric or verdict.
CREATE TABLE IF NOT EXISTS diag_inference_events (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK(event_kind IN ('deterministic','llm')),
    stage TEXT NOT NULL, purpose TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('recorded','skipped','succeeded','failed')),
    invoked INTEGER NOT NULL DEFAULT 0 CHECK(invoked IN (0,1)),
    provider TEXT, model TEXT, provider_api_version TEXT,
    prompt_template_id TEXT, prompt_template_version TEXT, prompt_hash TEXT,
    input_hash TEXT, response_hash TEXT, provider_request_id TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER,
    row_level_data_included INTEGER NOT NULL DEFAULT 0 CHECK(row_level_data_included IN (0,1)),
    verdict_influenced INTEGER NOT NULL DEFAULT 0 CHECK(verdict_influenced = 0),
    payload_json TEXT NOT NULL, actor TEXT NOT NULL, ts TEXT NOT NULL
);
-- FWK-07 — decision-type-shaped, never pass/fail-shaped. One row per
-- diagnostic x scope unit; #4 in slice 1 emits one per run (the whole
-- cross-field pass over the dataset), with the roll-up in metrics_json.
CREATE TABLE IF NOT EXISTS diag_results (
    result_id TEXT PRIMARY KEY, run_id TEXT, diagnostic_id INTEGER,
    entity_or_table TEXT, decision_type TEXT, verdict TEXT, review_state TEXT,
    metrics_json TEXT, thresholds_used_json TEXT, scope_counts_json TEXT,
    na_reason TEXT, created_at TEXT
);
-- CFR-07..CFR-10 — the per-rule detail. EVERY evaluated rule gets a row
-- (PASS / VIOLATION / NOT-APPLICABLE) so the accounting is queryable, not
-- just the failures. Columns after regulatory_ref are additive context the
-- findings panel and the issue hand-off read.
CREATE TABLE IF NOT EXISTS diag_findings (
    finding_id TEXT PRIMARY KEY, result_id TEXT, rule_id TEXT, severity TEXT,
    violation_count INTEGER, rate REAL, tolerance REAL, exceptions_json TEXT,
    evidence_json TEXT, pattern TEXT, regulatory_ref TEXT,
    run_id TEXT, kb_rule_id TEXT, outcome TEXT, rule_text TEXT, rule_type TEXT,
    framework TEXT, entity TEXT, resolved_roles_json TEXT, tables_used TEXT,
    scope_rows_evaluated INTEGER, scope_rows_skipped INTEGER,
    na_reason TEXT, pattern_detail TEXT, review_state TEXT, seq INTEGER,
    created_at TEXT
);
-- The SME decision (human decision #2). Mandatory reason on dismiss,
-- enforced server-side. Append-only (PLT-05).
CREATE TABLE IF NOT EXISTS diag_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, target_type TEXT, target_id TEXT,
    action TEXT CHECK(action IN ('confirm_issue','dismiss')),
    reason TEXT, actor TEXT, ts TEXT
);
-- 0.5.0 Step 3 (AST-01..04, AST-22, A-Q02, A-Q04). asset_id is the SAME value
-- as dq_items.dataset_family_id (P-04) — the PLT-02 seam, re-read as identity.
-- Never a second identity: the seam consumer map in docs/0.5.0/02-asset-model.md
-- confirms delivery.py is untouched and stays the sole writer of that column.
CREATE TABLE IF NOT EXISTS dq_assets (
    asset_id TEXT PRIMARY KEY,          -- == dq_items.dataset_family_id (AST-10)
    system_id TEXT NOT NULL,            -- AST-01/P-02: DS0007 / DB0003, UNIQUE index below
    alias TEXT NOT NULL,                -- AST-02: [A-Za-z0-9_-]+, user-editable (AST-04)
    display_name TEXT NOT NULL,         -- P-03: '<system_id>-<alias>', derived, never parsed apart
    kind TEXT NOT NULL,                 -- 'database' | 'dataset'
    time_basis TEXT NOT NULL,           -- AST-22: 'period' | 'none'. IMMUTABLE (D-30)
    current_version_no INTEGER NOT NULL,
    lifecycle_status TEXT,              -- C-43: the EXISTING lifecycle vocabulary, no new values
    target_variable TEXT,               -- CTX-04/06 live here at asset level
    use_case TEXT,
    current_dictionary_version_id TEXT, -- AST-20
    created_by TEXT, created_at TEXT, updated_at TEXT
);
-- AST-06/07/14. One row per reference-schema generation.
CREATE TABLE IF NOT EXISTS dq_asset_versions (
    version_id TEXT PRIMARY KEY,        -- typed ID, A-Q04
    asset_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    status TEXT NOT NULL,               -- 'current' | 'superseded'
    reference_schema_json TEXT,         -- AST-07: {tables:{<t>:{columns:[...], column_count:n,
                                        --   types:{col:type}}}} — per-table for a Database (A-Q03)
    created_from_snapshot_id TEXT,      -- the snapshot whose confirmed map became this schema
    supersedes_version_no INTEGER,      -- AST-14: what this generation replaced
    restored_from_version_no INTEGER,   -- AST-09: set when this row exists because of a restore
    created_by TEXT, created_at TEXT,
    superseded_at TEXT
);
-- AST-20/21 (A-Q02). The dictionary versions on its OWN track.
CREATE TABLE IF NOT EXISTS dq_asset_dictionaries (
    dictionary_version_id TEXT PRIMARY KEY,   -- typed ID, A-Q04
    asset_id TEXT NOT NULL,
    dict_version_no INTEGER NOT NULL,
    source_file_id TEXT,                      -- dq_item_files.file_id it was parsed from
    parsed_json TEXT,                         -- _parse_dictionary's output, frozen
    created_by TEXT, created_at TEXT
);
-- ADM-06/07, AST-04/08/09 audit. The asset lifecycle audit-of-record (P-08) —
-- deliberately separate from the future ANL-03 usage-event log (P-08): this
-- one is "what happened, when, by whom" over live objects.
CREATE TABLE IF NOT EXISTS dq_asset_events (
    event_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL, version_no INTEGER, snapshot_id TEXT,
    event_type TEXT NOT NULL,   -- asset_created | alias_renamed | snapshot_added |
                                -- version_created | version_superseded | version_restored |
                                -- schema_override | dictionary_version_bound
    actor TEXT, at TEXT NOT NULL,
    summary TEXT,               -- ADM-06's one-line "what changed", human language (M-2),
                                -- written at write time — never composed from enums at read time
    detail_json TEXT
);
-- AST-01 / D-29 / P-10. The human-quotable ID counter, per scope.
CREATE TABLE IF NOT EXISTS id_sequences (
    scope TEXT PRIMARY KEY,     -- asset_dataset | asset_database | snapshot | version |
                                -- dictionary_version   (exactly five — A-Q04)
    next_value INTEGER NOT NULL,
    epoch_started_at TEXT
);
-- AST-17/P-01. One immutable distribution fingerprint per snapshot column.
CREATE TABLE IF NOT EXISTS dq_snapshot_fingerprints (
    snapshot_id TEXT, table_name TEXT, column_name TEXT,
    dtype TEXT, confirmed_type TEXT,
    null_rate REAL, distinct_count INTEGER,
    min_value TEXT, max_value TEXT, mean_value REAL, stddev_value REAL,
    histogram_json TEXT, distinct_set_hash TEXT, top_k_json TEXT,
    computed_at TEXT,
    PRIMARY KEY (snapshot_id, table_name, column_name)
);
-- Governed diagnostic-package registry. Uploaded packages remain drafts until
-- a KB reviewer explicitly activates a fully validated version. The partial
-- unique index guarantees one runtime authority per tenant and diagnostic.
CREATE TABLE IF NOT EXISTS diagnostic_kb_packages (
    package_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    diagnostic_id INTEGER NOT NULL, document_id TEXT NOT NULL,
    version_id TEXT NOT NULL UNIQUE, version_seq INTEGER NOT NULL,
    based_on_version_id TEXT, package_hash TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN
        ('installed','draft','active','superseded','rejected')),
    package_json TEXT NOT NULL, validation_json TEXT NOT NULL,
    change_summary TEXT, created_by TEXT, created_at TEXT,
    reviewed_by TEXT, reviewed_at TEXT, activation_reason TEXT,
    activated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_diagnostic_kb_active
    ON diagnostic_kb_packages(tenant_id, diagnostic_id)
    WHERE lifecycle_state = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS ux_diagnostic_kb_sequence
    ON diagnostic_kb_packages(tenant_id, diagnostic_id, version_seq);
-- Phase 1B analytics foundation. Payloads remain immutable JSON files; this
-- table stores their searchable identity, reproducibility key, and lineage.
CREATE TABLE IF NOT EXISTS analysis_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    comparison_snapshot_id TEXT,
    population_fingerprint TEXT NOT NULL,
    target_fingerprint TEXT,
    feature TEXT,
    methodology_fingerprint TEXT NOT NULL,
    scope TEXT NOT NULL,
    workflow_id TEXT,
    payload_path TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    source_artifact_ids_json TEXT,
    run_id TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    superseded_by_artifact_id TEXT
);
-- Append-only catalogue events deliberately sit beside immutable artifact
-- metadata: lifecycle changes are auditable without changing analytical content.
CREATE TABLE IF NOT EXISTS analysis_artifact_events (
    event_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    detail_json TEXT,
    created_at TEXT NOT NULL
);
-- Normalized dependency edges make impact traversal index-backed. JSON source
-- fields remain on analysis_artifacts for API and historical compatibility.
CREATE TABLE IF NOT EXISTS analysis_artifact_sources (
    artifact_id TEXT NOT NULL,
    source_artifact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (artifact_id, source_artifact_id)
);
CREATE TABLE IF NOT EXISTS diag_binning_revisions (
    revision_id TEXT PRIMARY KEY,
    result_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    feature TEXT NOT NULL,
    scope TEXT NOT NULL,
    revision_no INTEGER NOT NULL,
    fine_artifact_id TEXT NOT NULL,
    coarse_artifact_id TEXT NOT NULL,
    iv_artifact_id TEXT NOT NULL,
    supersedes_revision_id TEXT,
    definition_json TEXT NOT NULL,
    bins_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    warnings_json TEXT,
    governance_json TEXT,
    actor TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_manifests (
    run_id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    capability_version TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_fingerprint TEXT NOT NULL,
    readiness_json TEXT,
    status TEXT NOT NULL,
    result_json TEXT,
    artifact_ids_json TEXT,
    error_message TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS analysis_observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    feature TEXT,
    classification TEXT,
    review_state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    issue_row_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_observation_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    actor TEXT NOT NULL,
    ts TEXT NOT NULL
);
-- ANL-03: capture-only usage log. Normal writes go through
-- analytics.events.record_event; factory reset has one named deletion
-- exception so history leaves with the objects it describes.
CREATE TABLE IF NOT EXISTS usage_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    workflow_context TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_events_type_at ON usage_events(event_type, at);
CREATE INDEX IF NOT EXISTS ix_usage_events_object ON usage_events(object_type, object_id);
CREATE INDEX IF NOT EXISTS ix_usage_events_actor ON usage_events(actor, at);
"""

# F16 — the single demo-removable logical DB. Reset is surgical: it removes only
# this DB's inventory rows + the demo work-product, never the pre-seeded DBs,
# the static test_library, or user accounts (preserving F13 profile edits).
DEMO_DB = "retail_risk_db"

# Demo work-product transaction tables — only ever hold demo-built artefacts
# post-seed, so they are cleared wholesale on reset. WSP-08/D-19 — issues_v2 /
# tracked_issues_v2 joined this list so the admin factory reset (both grades)
# actually clears every derived-issue artefact, not just plans/results/scores.
_WORKPRODUCT_TABLES = [
    "test_plan", "test_db_links", "test_dossiers", "test_versions", "design_workbench",
    "schedules", "notifications", "run_results", "health_scores", "hitl_decisions",
    "monitoring", "tickets",
    "dq_items", "dq_item_files", "dq_item_tables", "variable_inventory",
    "plan_v2", "results_v2", "scores_v2", "issues_v2", "tracked_issues_v2",
    # Phase 4 — ingestion redesign persisted substrate (ING-08).
    "dq_item_mappings", "dq_item_warnings",
    # Phase 6 — Test Lab diagnostic runs and everything derived from them.
    "diag_runs", "diag_run_decisions", "diag_inference_events", "diag_results", "diag_findings",
    "diag_dispositions",
    "analysis_artifacts",
    "analysis_artifact_events",
    "analysis_artifact_sources",
    "diag_binning_revisions",
    "analysis_manifests", "analysis_observations", "analysis_observation_dispositions",
]
# app_fsm entity_types cleared on reset (active `session` rows are preserved so
# the admin is not logged out mid-demo).
_FSM_RESET_ENTITY_TYPES = ["ingestion", "rca", "test_generation", "test_plan"]


def get_conn() -> sqlite3.Connection:
    # timeout/busy_timeout: on a network filesystem (Azure Files SMB) lock
    # acquisition can momentarily contend; without a busy timeout SQLite fails
    # instantly with "database is locked". Waiting-and-retrying serialises the
    # single writer cleanly. journal_mode is pinned to the rollback journal:
    # WAL is unsupported over SMB, so we must never let it flip to WAL.
    conn = sqlite3.connect(str(SYS_DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def restore_from_backup() -> bool:
    """Seed the local DB from the persistent-volume backup on a fresh container.

    Called once at startup BEFORE init_schema(). A container's local disk is
    ephemeral, so on every restart SYS_DB_PATH is absent and — if a backup exists
    on the mounted share — we copy it into place (plain file copy: no SQLite
    locking, works fine over SMB). No-op when backups are disabled, no backup
    exists, or a non-empty local DB is already present (never clobber live data).
    """
    if not SYS_DB_BACKUP_PATH or not SYS_DB_BACKUP_PATH.exists():
        return False
    if SYS_DB_PATH.exists() and SYS_DB_PATH.stat().st_size > 0:
        return False
    import shutil
    SYS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SYS_DB_BACKUP_PATH, SYS_DB_PATH)
    return True


def backup_to_volume() -> bool:
    """Snapshot the live DB to the persistent volume. No-op if disabled.

    Uses SQLite's online-backup API into a LOCAL temp file (a consistent snapshot
    even with writes in flight, and locking stays on local disk), then copies that
    snapshot to the SMB share as plain bytes and renames into place. Backing up
    *through* sqlite3 directly onto the share would re-trigger the SMB lock
    failure, hence the local-snapshot-then-copy two-step.
    """
    if not SYS_DB_BACKUP_PATH:
        return False
    backup_to_path(SYS_DB_BACKUP_PATH)
    return True


def backup_to_path(destination: Path) -> None:
    """Write a consistent SQLite snapshot to ``destination``.

    The snapshot is first made on the local disk using SQLite's online-backup
    API, then copied and atomically renamed at the destination.  Keeping this
    primitive separate from the optional periodic-volume policy lets a
    one-time destructive migration require a restorable snapshot too.

    Raises on failure; callers which are about to delete data must treat that
    as a hard stop rather than proceeding without a recovery point.
    """
    if destination.resolve() == SYS_DB_PATH.resolve():
        raise ValueError("SQLite snapshot destination must not be the live system database")
    import shutil
    local_tmp = SYS_DB_PATH.with_name(SYS_DB_PATH.name + ".snapshot.tmp")
    local_tmp.unlink(missing_ok=True)
    src = sqlite3.connect(str(SYS_DB_PATH), timeout=30.0)
    try:
        dst = sqlite3.connect(str(local_tmp), timeout=30.0)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        share_tmp = destination.with_name(destination.name + ".tmp")
        shutil.copy2(local_tmp, share_tmp)
        os.replace(share_tmp, destination)  # atomic rename within destination
    finally:
        local_tmp.unlink(missing_ok=True)


def now_ist() -> str:
    """Return an IST timestamp with explicit +05:30 offset."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()


# RCA Stage 3 reused these table names for an incompatible new shape (the
# retired legacy RCA module — ai/rca_cases.py, ai/rca_agent.py — used the same
# names with unrelated columns). `CREATE TABLE IF NOT EXISTS` is a silent
# no-op against a pre-existing table, so on a database that already ran the
# legacy schema, the table keeps its old shape and every later reference to a
# new-only column (e.g. `rca_cases.issue_row_id`) fails. Keyed table ->
# a column that exists only in the new shape; if present without it, the
# on-disk table is the legacy one and gets renamed out of the way (data kept,
# not dropped) so the new CREATE TABLE can build the real one fresh.
_RCA_V10_LEGACY_DISCRIMINATOR: dict[str, str] = {
    "rca_cases": "issue_row_id",
    "rca_hypotheses": "suspect_id",
}


def _rename_legacy_rca_tables(conn: sqlite3.Connection) -> None:
    for table, new_only_col in _RCA_V10_LEGACY_DISCRIMINATOR.items():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if cols and new_only_col not in cols:
            conn.execute(f'ALTER TABLE {table} RENAME TO {table}_legacy_v1')


# Lightweight additive migrations for existing DB files (ALTER is idempotent-
# guarded by an introspection check). Keyed table -> {column: DDL type}.
_MIGRATIONS: dict[str, dict[str, str]] = {
    "users": {"authz_roles": "TEXT",
             # RCA Stage 0/1 — bootstrap tenant model (contracts.md §1).
             # Existing rows backfill to the single bootstrap tenant; IDs unchanged.
             "tenant_id": "TEXT NOT NULL DEFAULT 'bootstrap'"},
    "test_library": {"input_spec": "TEXT"},
    "ingested_databases": {"metadata": "TEXT", "relations": "TEXT",
                           "fetched_at": "TEXT", "record_count": "INTEGER",
                           # Feature 4 — bring-your-own source handle (NULL => bundled warehouse).
                           "source_kind": "TEXT", "source_path": "TEXT",
                           # Feedback R6 — editable markdown understanding blocks saved
                           # with the DB object (retrieved by the test module later).
                           "key_anomalies_md": "TEXT", "suggested_hypotheses_md": "TEXT"},
    "agent_skills": {"seq": "INTEGER",  # Plan 6 — explicit topology ordering
                     "descriptor": "TEXT"},  # Feedback R4.1 — UI label "Agent X (descriptor)"
    "test_plan": {"instance_key": "TEXT", "operands": "TEXT"},
    "run_results": {"plan_id": "INTEGER", "instance_key": "TEXT"},
    # Phase 4 — ING-05: per-column flag, true when the column did NOT get a
    # confident (high-tier, type-resolved) dictionary mapping — i.e. its
    # classification came from generic inference, not the dictionary.
    "variable_inventory": {"role": "TEXT", "profile_json": "TEXT", "provisional": "INTEGER",
                           "dictionary_role": "TEXT", "business_context": "TEXT",
                           "missing_value_codes_json": "TEXT",
                           "missing_codes_confirmed": "INTEGER DEFAULT 0"},
    "dq_item_mappings": {"role": "TEXT", "missing_value_codes_json": "TEXT",
                         "business_context": "TEXT"},
    "plan_v2": {"crossval_json": "TEXT"},
    # Feedback 09-07 4.1 — univariate tests store one result per column, each
    # carrying the exact column(s) that execution ran on.
    "results_v2": {"columns_json": "TEXT", "not_runnable_reason": "TEXT", "watch_note": "INTEGER DEFAULT 0"},
    # Feedback 10-07 5.1 — one issue per failed test carries the full detail
    # (metric / threshold / violations) for EVERY failing column, not the worst.
    # RCA Stage 1 — workflow_version labels legacy vs the new case workflow
    # (contracts.md §2); existing rows default to legacy so the current
    # heuristic RCA path is untouched.
    # Phase 6 — the natural key is extended to (item_id, diagnostic_id,
    # rule_id) for diagnostic-sourced issues (testlab-redesign §5.2), which
    # fixes §1.7's mutation bug: a NEW failing rule can no longer quietly
    # rewrite an existing issue's history. Legacy issues (all NULL here)
    # keep the old (item, table, test) key and behaviour untouched.
    "issues_v2": {"column_details_json": "TEXT",
                 "workflow_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
                 "diagnostic_id": "INTEGER", "run_id": "TEXT",
                 "finding_id": "TEXT", "rule_id": "TEXT",
                 "superseded_by_issue_row_id": "TEXT"},
    # Governed artifact repository hardening.  Existing rows remain readable;
    # their extended identity is reconstructed lazily from the legacy columns.
    "analysis_artifacts": {
        "identity_fingerprint": "TEXT",
        "identity_json": "TEXT",
        "schema_version": "INTEGER NOT NULL DEFAULT 1",
        "summary_json": "TEXT",
        "summary_adapter_version": "TEXT",
        "owner_id": "TEXT",
        "source_artifacts_json": "TEXT",
        "integrity_status": "TEXT NOT NULL DEFAULT 'unknown'",
        "integrity_checked_at": "TEXT",
    },
    # Phase 3 — PLT-02 delivery/family seam (docs/0.4.0/00-framework.md).
    # Existing rows are backfilled (dataset_family_id=item_id, delivery_seq=1)
    # by _backfill_delivery_defaults so every pre-existing item behaves as a
    # single-item family of its own, unchanged in observable behaviour.
    # Phase 4 — ING-07 status machine + ING-05 item-level dictionary state.
    # Existing rows are backfilled by _backfill_ingest_defaults (derived from
    # what actually happened: has a data file been uploaded, is it already
    # profiled, was it flagged requires_reupload) so every pre-existing item
    # lands on a valid new status without a human touching it.
    # 0.5.0 Step 3 (AST) — the asset/version/snapshot model, layered onto the
    # SAME PLT-02 columns above (dataset_family_id/delivery_seq/as_of_date/
    # baseline_delivery_id are untouched — see docs/0.5.0/02-asset-model.md
    # §Seam). snapshot_status is a NEW AXIS (P-07): active/superseded
    # selectability, never a replacement for status (lifecycle) or
    # ingest_status (the ingest machine), which keep their exact current
    # meanings. Deliberately NOT added here (P-06): no snapshot_id column
    # (item_id IS the snapshot id), no uploaded_at column (created_at is it),
    # no asset_id column (dataset_family_id is it), no asset_name column
    # (name mirrors dq_assets.display_name — P-05). Backfilled by
    # _backfill_asset_model, called from init_schema() right after
    # _backfill_ingest_defaults — only rows never backfilled are touched, so
    # a second call is a no-op (rule 3).
    "dq_items": {"dataset_family_id": "TEXT", "delivery_seq": "INTEGER",
                "as_of_date": "TEXT", "baseline_delivery_id": "TEXT",
                "ingest_status": "TEXT", "ingest_fail_reason": "TEXT", "dictionary_state": "TEXT",
                "version_no": "INTEGER", "snapshot_status": "TEXT", "snapshot_label": "TEXT",
                "start_date": "TEXT", "end_date": "TEXT", "has_time_period": "INTEGER",
                "period_column": "TEXT", "column_type_map_json": "TEXT",
                "row_count": "INTEGER", "column_count": "INTEGER", "file_name": "TEXT",
                "intent": "TEXT", "schema_override_flag": "INTEGER", "uploaded_by": "TEXT",
                "dictionary_version_id": "TEXT", "dictionary_header_mapping_json": "TEXT",
                "dictionary_value_mapping_json": "TEXT",
                "dictionary_mapping_confirmed": "INTEGER DEFAULT 0", "superseded_at": "TEXT",
                "superseded_by_version_no": "INTEGER", "product": "TEXT",
                "source_parsing_options_json": "TEXT", "file_context_json": "TEXT",
                "artifact_origin": "TEXT NOT NULL DEFAULT 'unclassified'"},
    # Data_Sourcing_11 — "product" joins target_variable/use_case as a
    # denormalised asset-level decision (same mirror pattern, P-05).
    "dq_assets": {"product": "TEXT",
                  "artifact_origin": "TEXT NOT NULL DEFAULT 'unclassified'"},
    # Run provenance is independent of asset provenance: development can run
    # a diagnostic against a user-owned asset without making that asset
    # disposable. Cleanup follows this run root and its descendants only.
    "diag_runs": {"artifact_origin": "TEXT NOT NULL DEFAULT 'unclassified'"},
    # Phase 5 — KB-07 rule-record fields (table-aware parse) + KB-08/09 binding
    # status (docs/0.4.0/04-kb-contract.md). Every column is nullable/defaulted
    # so pre-existing rows (prose-parsed under 0.3.0) read back as
    # binding_status='unparsed' with everything else None — honest, since
    # those rows never went through table-aware extraction either.
    "kb_rules": {
        "source_rule_id": "TEXT", "severity": "TEXT", "rule_type": "TEXT", "entity": "TEXT",
        "semantic_roles_json": "TEXT", "regulatory_ref": "TEXT", "encoded_exceptions_json": "TEXT",
        "source_page": "INTEGER", "binding_status": "TEXT NOT NULL DEFAULT 'unparsed'",
        "binding_primitive": "TEXT", "binding_params_json": "TEXT",
        "parse_hazards_json": "TEXT", "framework": "TEXT",
        "proposal_kind": "TEXT", "proposal_subject_key": "TEXT",
        "proposal_decision_hash": "TEXT", "based_on_rule_id": "TEXT",
        "proposal_metadata_json": "TEXT",
    },
}


def _backfill_plan_instance_keys(conn: sqlite3.Connection) -> None:
    """Give legacy plan rows stable identities without destroying user state."""
    rows = conn.execute(
        'SELECT plan_id, "table", test_ref, instance_key FROM test_plan ORDER BY plan_id'
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        if row["instance_key"]:
            seen.add(row["instance_key"])
            continue
        try:
            ref = json.loads(row["test_ref"] or "{}")
        except (TypeError, ValueError):
            ref = {}
        test_id = ref.get("test_id") or ref.get("library_id") or f"plan_{row['plan_id']}"
        fields = sorted({str(v) for v in (ref.get("fields") or [])})
        base = f"{row['table']}|{test_id}|{json.dumps(fields, separators=(',', ':'))}"
        key = base if base not in seen else f"{base}#legacy-{row['plan_id']}"
        conn.execute("UPDATE test_plan SET instance_key=? WHERE plan_id=?",
                     (key, row["plan_id"]))
        seen.add(key)


def _backfill_delivery_defaults(conn: sqlite3.Connection) -> None:
    """PLT-02 — give every pre-existing dq_items row a delivery identity: a
    single-item family of its own (dataset_family_id=item_id, delivery_seq=1),
    so items created before this column existed behave exactly as they did
    before register_delivery() existed. Only touches rows that have never
    been backfilled (dataset_family_id IS NULL) — idempotent, and it never
    overwrites a family a later register_delivery() call actually built."""
    rows = conn.execute(
        "SELECT item_id FROM dq_items WHERE dataset_family_id IS NULL"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE dq_items SET dataset_family_id=?, delivery_seq=1 WHERE item_id=?",
            (row["item_id"], row["item_id"]),
        )


# Phase 4 (0.4.0) — ING-05 dictionary-state coverage floor, mirrored from
# ingest/dictionary_state.py (COVERAGE_FLOOR). Duplicated as a literal here
# (rather than imported) because system_db.py must stay import-cheap and
# dependency-free at module load — every other module imports IT, never the
# other way around; the value is small and stable enough that keeping this
# one number in sync by hand is a reasonable trade.
_INGEST_DICTIONARY_COVERAGE_FLOOR = 0.7


def _backfill_ingest_defaults(conn: sqlite3.Connection) -> None:
    """Phase 4 (ING-*, plan 4.8) — map every pre-existing dq_items row onto
    the new ingest_status/dictionary_state vocabulary without loss, and
    synthesize a best-effort dq_item_mappings row per already-profiled
    column from its existing variable_inventory record (a mapping the old
    exact-match-only dictionary lookup effectively already made, now made
    explicit and readable by Phase 6).

    Status is derived from what actually happened, matching
    ingest.status.derive()'s rule: already has variable_inventory rows =>
    'ready' (profiling completed; nothing in ING-04 blocks ready); flagged
    requires_reupload => 'failed' (its source file is gone); has an
    uploaded data file but never profiled => 'profiling'; nothing uploaded
    yet => 'uploading'.

    Only touches rows that have never been backfilled (ingest_status IS
    NULL) — idempotent (4-T9), and never overwrites a status a later real
    ingestion run already computed.
    """
    items = conn.execute(
        "SELECT item_id, status FROM dq_items WHERE ingest_status IS NULL"
    ).fetchall()
    for item in items:
        item_id = item["item_id"]
        inv_rows = conn.execute(
            "SELECT table_name, column_name, data_type, description "
            "FROM variable_inventory WHERE item_id=?", (item_id,)
        ).fetchall()
        has_data_file = conn.execute(
            "SELECT 1 FROM dq_item_files WHERE item_id=? AND role='data' LIMIT 1", (item_id,)
        ).fetchone() is not None
        has_dict_file = conn.execute(
            "SELECT 1 FROM dq_item_files WHERE item_id=? AND role='dictionary' LIMIT 1", (item_id,)
        ).fetchone() is not None

        if item["status"] == "requires_reupload":
            ingest_status = "failed"
            fail_reason = "Source file is missing after a storage migration — re-upload required."
        elif inv_rows:
            ingest_status, fail_reason = "ready", None
        elif has_data_file:
            ingest_status, fail_reason = "profiling", None
        else:
            ingest_status, fail_reason = "uploading", None

        if not inv_rows:
            dictionary_state = "absent" if not has_dict_file else "thin"
        elif not has_dict_file:
            dictionary_state = "absent"
        else:
            declared_covered = sum(1 for r in inv_rows if (r["description"] or "").strip())
            ratio = declared_covered / len(inv_rows)
            dictionary_state = "yes" if ratio >= _INGEST_DICTIONARY_COVERAGE_FLOOR else "thin"

        conn.execute(
            "UPDATE dq_items SET ingest_status=?, ingest_fail_reason=?, dictionary_state=? WHERE item_id=?",
            (ingest_status, fail_reason, dictionary_state, item_id),
        )
        now = now_ist()
        for row in inv_rows:
            declared = bool((row["description"] or "").strip())
            conn.execute(
                "INSERT OR IGNORE INTO dq_item_mappings "
                "(item_id, table_name, canonical_field, source_column, tier, score, status, "
                "confirmed_by, dtype, definition, declared_type, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, row["table_name"], row["column_name"],
                 row["column_name"] if declared else None,
                 "high" if declared else "below_floor",
                 1.0 if declared else None,
                 "applied" if declared else "unmapped",
                 "auto" if declared else None,
                 row["data_type"], row["description"] or "", "", now),
            )
            conn.execute(
                "UPDATE variable_inventory SET provisional=? "
                "WHERE item_id=? AND table_name=? AND column_name=? AND provisional IS NULL",
                (0 if declared else 1, item_id, row["table_name"], row["column_name"]),
            )


def _sanitize_alias_for_migration(raw: str | None) -> str:
    """AST-10 backfill only — turn an arbitrary pre-0.5.0 item name into a
    valid alias: spaces become dashes, everything outside [A-Za-z0-9_-] is
    dropped, and an empty result falls back to 'asset' (task 3.4 step 1;
    adversarial case 10 — a whitespace-only name must never produce an empty
    alias). Duplicated (not imported) from assets.identity's own copy of
    this rule deliberately: system_db.py must stay import-cheap and
    dependency-free at module load (see the comment on
    _INGEST_DICTIONARY_COVERAGE_FLOOR above for the same discipline), and
    this one small regex is cheaper to keep in sync by hand than to add a
    module-load-time dependency for."""
    import re  # noqa: PLC0415
    text = (raw or "").strip().replace(" ", "-")
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", text)
    return cleaned or "asset"


def _backfill_asset_model(conn: sqlite3.Connection) -> None:
    """AST-10/PLT-06 (0.5.0 Step 3a) — give every pre-existing
    dataset_family_id in dq_items exactly one dq_assets row (+ its v1
    dq_asset_versions row + an asset_created dq_asset_events row), and mark
    every dq_items row of that family as an active v1 snapshot.

    Only acts on families with NO dq_assets row yet — idempotent, a second
    call is a no-op for already-migrated families (rule 3). For a family
    that already HAS a dq_assets row, this instead performs R-04's drift
    repair: re-assert dq_items.name == dq_assets.display_name, every call —
    itself idempotent by construction (a no-op once the names already
    match).

    Uses the SAME connection/transaction init_schema() already has open —
    never system_db.insert/update/query, which each open their OWN new
    connection via get_conn() and would deadlock against this function's
    still-uncommitted outer transaction (the same reason
    _backfill_delivery_defaults/_backfill_ingest_defaults use raw
    conn.execute throughout). assets.identity.allocate() is called WITH this
    same conn for exactly that reason.
    """
    from assets import identity  # noqa: PLC0415 — deferred; see docstring above

    now = now_ist()
    family_ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT dataset_family_id FROM dq_items WHERE dataset_family_id IS NOT NULL"
    ).fetchall()]

    for family_id in family_ids:
        existing_asset = conn.execute(
            "SELECT display_name FROM dq_assets WHERE asset_id=?", (family_id,)
        ).fetchone()
        if existing_asset is not None:
            # R-04 drift repair: an alias rename (S3b) mirrors display_name
            # onto every dq_items row of the asset; re-assert it here too so
            # a stray write from anywhere else self-heals on the next boot.
            conn.execute(
                "UPDATE dq_items SET name=? "
                "WHERE dataset_family_id=? AND (name IS NULL OR name != ?)",
                (existing_asset[0], family_id, existing_asset[0]),
            )
            continue

        rows = conn.execute(
            "SELECT * FROM dq_items WHERE dataset_family_id=? "
            "ORDER BY delivery_seq, item_id",
            (family_id,),
        ).fetchall()
        if not rows:
            continue
        origin = rows[0]  # lowest delivery_seq; item_id breaks a tied seq deterministically

        has_period = conn.execute(
            "SELECT 1 FROM dq_items WHERE dataset_family_id=? AND as_of_date IS NOT NULL LIMIT 1",
            (family_id,),
        ).fetchone() is not None
        time_basis = "period" if has_period else "none"
        has_time_period = 1 if time_basis == "period" else 0

        alias = _sanitize_alias_for_migration(origin["name"])
        scope = identity.scope_for_kind(origin["kind"])
        system_id = identity.allocate(scope, conn=conn)
        display_name = identity.compose_display_name(system_id, alias)

        conn.execute(
            "INSERT INTO dq_assets (asset_id, system_id, alias, display_name, kind, "
            "time_basis, current_version_no, lifecycle_status, target_variable, use_case, "
            "current_dictionary_version_id, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (family_id, system_id, alias, display_name, origin["kind"], time_basis, 1,
             origin["status"], origin["target_variable"], origin["use_case"],
             None, None, now, now),
        )

        # AST-07: reconstruct the origin snapshot's reference schema from
        # dq_item_tables.columns + variable_inventory.data_type. Leave it
        # NULL — never invent one — when the origin was never profiled
        # (task 3.4 step 3: "a never-profiled item has no reference schema").
        origin_id = origin["item_id"]
        table_rows = conn.execute(
            "SELECT table_name, columns, col_count FROM dq_item_tables WHERE item_id=?",
            (origin_id,),
        ).fetchall()
        inv_rows = conn.execute(
            "SELECT table_name, column_name, data_type FROM variable_inventory WHERE item_id=?",
            (origin_id,),
        ).fetchall()
        reference_schema_json = None
        if inv_rows:
            type_map: dict[str, dict[str, str]] = {}
            for inv in inv_rows:
                type_map.setdefault(inv["table_name"], {})[inv["column_name"]] = inv["data_type"]
            tables_schema = {}
            for t in table_rows:
                try:
                    cols = json.loads(t["columns"]) if t["columns"] else []
                except (TypeError, ValueError):
                    cols = []
                tables_schema[t["table_name"]] = {
                    "columns": cols,
                    "column_count": t["col_count"] if t["col_count"] is not None else len(cols),
                    "types": type_map.get(t["table_name"], {}),
                }
            reference_schema_json = json.dumps({"tables": tables_schema})

        version_id = identity.allocate("version", conn=conn)
        conn.execute(
            "INSERT INTO dq_asset_versions (version_id, asset_id, version_no, status, "
            "reference_schema_json, created_from_snapshot_id, supersedes_version_no, "
            "restored_from_version_no, created_by, created_at, superseded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (version_id, family_id, 1, "current", reference_schema_json, origin_id,
             None, None, None, now, None),
        )

        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO dq_asset_events (event_id, asset_id, version_no, snapshot_id, "
            "event_type, actor, at, summary, detail_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (event_id, family_id, 1, origin_id, "asset_created", None, now,
             "Migrated from the 0.4.0 item model.", None),
        )

        # Every family member becomes an active v1 snapshot. The origin's
        # own upload is 'fresh'; every other pre-existing sibling delivery
        # reads honestly as 'add_period' (task 3.4 step 2 — reading them as
        # replacements would retroactively supersede data the user can
        # still see today).
        for position, row in enumerate(rows, start=1):
            item_id = row["item_id"]
            intent = "fresh" if item_id == origin_id else "add_period"
            # The fallback label is derived from POSITION in the
            # deterministic origin-sort (ORDER BY delivery_seq, item_id
            # above), not delivery_seq itself: delivery_seq is normally
            # gapless and unique per family (register_delivery guarantees
            # it), so position and delivery_seq coincide in the well-formed
            # case — but a hostile/malformed pre-existing family with a TIED
            # delivery_seq (adversarial case 10) must still get distinct
            # labels, or this UPDATE would collide against
            # ux_dq_items_label_in_family for every row after the first.
            snapshot_label = row["as_of_date"] or f"Delivery {position}"

            totals = conn.execute(
                "SELECT COALESCE(SUM(row_count),0) AS rc, COALESCE(SUM(col_count),0) AS cc, "
                "COUNT(*) AS n FROM dq_item_tables WHERE item_id=?",
                (item_id,),
            ).fetchone()
            row_count = totals["rc"] if totals["n"] else None
            column_count = totals["cc"] if totals["n"] else None

            file_row = conn.execute(
                "SELECT filename FROM dq_item_files WHERE item_id=? AND role='data' "
                "ORDER BY completed_at DESC, file_id DESC LIMIT 1",
                (item_id,),
            ).fetchone()
            file_name = file_row["filename"] if file_row else None

            conn.execute(
                "UPDATE dq_items SET version_no=1, snapshot_status='active', intent=?, "
                "has_time_period=?, start_date=?, snapshot_label=?, name=?, "
                "row_count=?, column_count=?, file_name=?, uploaded_by=? WHERE item_id=?",
                (intent, has_time_period, row["as_of_date"], snapshot_label, display_name,
                 row_count, column_count, file_name, None, item_id),
            )


def init_schema() -> None:
    """Create all F1 tables if absent + apply additive migrations (idempotent)."""
    with get_conn() as conn:
        _rename_legacy_rca_tables(conn)
        conn.executescript(_SCHEMA)
        for table, cols in _MIGRATIONS.items():
            existing = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {ddl}')
        _backfill_plan_instance_keys(conn)
        _backfill_delivery_defaults(conn)
        _backfill_ingest_defaults(conn)
        _backfill_asset_model(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_test_plan_instance_key "
            "ON test_plan(instance_key) WHERE instance_key IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_test_db_links_db "
            "ON test_db_links(logical_db)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_test_db_links_plan "
            "ON test_db_links(plan_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_test_dossiers_plan "
            "ON test_dossiers(plan_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_test_versions_plan_seq "
            "ON test_versions(plan_id, seq)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_object_contexts_lookup "
            "ON object_contexts(logical_db, object_type, object_key, context_type, scope, priority)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_object_contexts_tags "
            "ON object_contexts(object_type, source, scope)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_context_links_object "
            "ON context_links(to_object_type, to_object_key, relation)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_dq_items_kind_status "
            "ON dq_items(kind, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_plan_v2_item_scope "
            "ON plan_v2(item_id, scope, table_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_results_v2_item_scope "
            "ON results_v2(item_id, scope, table_name)"
        )
        # Phase 4 — ingestion persisted substrate lookups (ING-08).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_dq_item_mappings_item "
            "ON dq_item_mappings(item_id, table_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_dq_item_warnings_item "
            "ON dq_item_warnings(item_id, table_name)"
        )
        # RCA Stage 1 — taxonomy lookups: values-by-dimension, and the
        # active-assignments-by-object lookup every tag read/inherit call makes.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_tag_values_dimension "
            "ON tag_values(dimension_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_tag_aliases_value "
            "ON tag_aliases(value_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_tag_assignments_object "
            "ON tag_assignments(tenant_id, object_type, object_id, removed_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_tag_assignments_value "
            "ON tag_assignments(value_id)"
        )
        # RCA Stage 2 — Knowledge Base lookups.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_document_versions_document "
            "ON kb_document_versions(document_id, version_seq)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_sections_version "
            "ON kb_sections(version_id, order_seq)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_rules_retrieval "
            "ON kb_rules(tenant_id, lifecycle_state, category, is_synthetic)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_rules_document "
            "ON kb_rules(document_id, version_id)"
        )
        # Phase 5 — KB-16 retrieval filters on binding_status (cross-field's
        # bound-only view) in addition to the existing lifecycle/category index.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_rules_binding "
            "ON kb_rules(tenant_id, lifecycle_state, binding_status)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_kb_open_proposal_subject "
            "ON kb_rules(tenant_id, proposal_kind, proposal_subject_key) "
            "WHERE proposal_kind IS NOT NULL "
            "AND lifecycle_state IN ('draft', 'pending_review')"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_proposal_evidence_rule "
            "ON kb_rule_proposal_evidence(rule_id, created_at)"
        )
        # RCA Stage 3 — case/evidence lookups.
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_cases_issue ON rca_cases(issue_row_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_cases_tenant_state ON rca_cases(tenant_id, state)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_state_transitions_case ON rca_state_transitions(case_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_looks_case ON rca_looks(case_id, seq)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_look_executions_look ON rca_look_executions(look_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_suspects_case ON rca_suspects(case_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_suspect_history_suspect ON rca_suspect_history(suspect_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_hypotheses_case ON rca_hypotheses(case_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_confirmation_checks_hyp ON rca_confirmation_checks(hypothesis_id, order_rank)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_judge_decisions_check ON rca_judge_decisions(check_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_fix_proposals_hyp ON rca_fix_proposals(hypothesis_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_fix_approvals_proposal ON rca_fix_approvals(fix_proposal_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_audit_events_object ON rca_audit_events(object_type, object_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_rca_audit_events_case ON rca_audit_events(tenant_id, ts)")
        # Plan 6 — retire the old 'lovelace' agent row (renamed to 'newton');
        # seed_agents re-inserts 'newton'. Idempotent and cheap.
        conn.execute("DELETE FROM agent_skills WHERE agent_key='lovelace'")
        # Phase 3 — diagnostic register + framework taxonomy + semantic layer
        # lookups (docs/0.4.0/00-framework.md).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_diagnostic_register_workflow_status "
            "ON diagnostic_register(workflow_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_framework_taxonomy_coverage_status "
            "ON framework_taxonomy(coverage_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_threshold_settings_lookup "
            "ON threshold_settings(diagnostic_id, key, scope, scope_ref)"
        )
        # Phase 3 — PLT-02 delivery/family seam lookup (family_deliveries()).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_dq_items_family "
            "ON dq_items(dataset_family_id, delivery_seq)"
        )
        # Phase 6 — Test Lab run/result/finding lookups.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_diag_runs_item "
            "ON diag_runs(item_id, diagnostic_id, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_diag_run_decisions_run ON diag_run_decisions(run_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_diag_inference_events_run ON diag_inference_events(run_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_diag_results_run ON diag_results(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_diag_findings_result ON diag_findings(result_id, seq)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_diag_findings_run ON diag_findings(run_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_diag_dispositions_target "
            "ON diag_dispositions(target_type, target_id, ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_issues_v2_diagnostic "
            "ON issues_v2(item_id, diagnostic_id, rule_id)"
        )
        # 0.5.0 Step 3 (AST) — asset/version/snapshot lookups + the belt-to-
        # the-counter's-braces uniqueness backstop (AST-01, plan §9.2).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_assets_system_id "
            "ON dq_assets(system_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_asset_versions_no "
            "ON dq_asset_versions(asset_id, version_no)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_asset_dict_no "
            "ON dq_asset_dictionaries(asset_id, dict_version_no)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_dq_asset_events_asset "
            "ON dq_asset_events(asset_id, at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_artifacts_lookup "
            "ON analysis_artifacts(asset_id, snapshot_id, artifact_type, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_artifacts_page "
            "ON analysis_artifacts(asset_id, snapshot_id, status, created_at DESC, artifact_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_artifacts_reuse "
            "ON analysis_artifacts(snapshot_id, population_fingerprint, "
            "methodology_fingerprint, scope, status)"
        )
        # Persistence-layer backstop for concurrent exact writes.  Legacy rows
        # have a NULL fingerprint and are intentionally left untouched.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_analysis_artifacts_active_identity "
            "ON analysis_artifacts(identity_fingerprint) "
            "WHERE status='active' AND identity_fingerprint IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_artifact_events_artifact "
            "ON analysis_artifact_events(artifact_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_artifact_sources_source "
            "ON analysis_artifact_sources(source_artifact_id, artifact_id)"
        )
        # Idempotently normalize dependencies from existing governed and legacy
        # JSON metadata. New writes populate this table transactionally.
        conn.execute(
            "INSERT OR IGNORE INTO analysis_artifact_sources "
            "(artifact_id, source_artifact_id, role) "
            "SELECT a.artifact_id, "
            "CASE WHEN j.type='object' THEN json_extract(j.value, '$.artifact_id') "
            "ELSE CAST(j.value AS TEXT) END, "
            "CASE WHEN j.type='object' THEN COALESCE(json_extract(j.value, '$.role'), 'source') "
            "ELSE 'source' END "
            "FROM analysis_artifacts AS a, "
            "json_each(COALESCE(a.source_artifacts_json, a.source_artifact_ids_json, '[]')) AS j "
            "WHERE CASE WHEN j.type='object' THEN json_extract(j.value, '$.artifact_id') "
            "ELSE CAST(j.value AS TEXT) END IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_diag_binning_revisions_result "
            "ON diag_binning_revisions(result_id, scope, revision_no)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_manifests_snapshot "
            "ON analysis_manifests(snapshot_id, capability_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_observations_run "
            "ON analysis_observations(run_id, review_state)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_analysis_observation_dispositions_target "
            "ON analysis_observation_dispositions(observation_id, ts)"
        )
        # AST-22 + UPL-14: a snapshot label must be unique WITHIN an asset
        # when the asset's basis is 'none'. Partial index on the family, not
        # global (AST-03 — the alias/name never carries the uniqueness
        # burden, and neither does the label).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_items_label_in_family "
            "ON dq_items(dataset_family_id, snapshot_label) WHERE snapshot_label IS NOT NULL"
        )
        conn.commit()


def _encode(table: str, row: dict) -> dict:
    jc = _JSON_COLS.get(table, set())
    return {k: (json.dumps(v) if k in jc and not isinstance(v, str) else v)
            for k, v in row.items()}


def _decode(table: str, row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    jc = _JSON_COLS.get(table, set())
    out = {}
    for k in row.keys():
        v = row[k]
        if k in jc and isinstance(v, str):
            try:
                v = json.loads(v)
            except (ValueError, TypeError):
                pass
        out[k] = v
    return out


def current_artifact_origin() -> str:
    """Return the provenance assigned to newly-created asset roots.

    Production/API work defaults to ``user``. Automated test runs are marked
    ``development`` without relying on fixture names or fake usernames, and
    local tooling can opt in explicitly with ``ARCHIMEDES_ARTIFACT_ORIGIN``.
    Unknown values fail closed instead of creating a cleanup-eligible row.
    """
    configured = os.environ.get("ARCHIMEDES_ARTIFACT_ORIGIN", "").strip().lower()
    if configured:
        if configured not in {"user", "development"}:
            raise ValueError(
                "ARCHIMEDES_ARTIFACT_ORIGIN must be 'user' or 'development'"
            )
        return configured
    return "development" if os.environ.get("PYTEST_CURRENT_TEST") else "user"


def _with_root_provenance(table: str, row: dict) -> dict:
    """Stamp only lifecycle roots; descendants are selected through the root."""
    if table not in {"dq_assets", "dq_items", "diag_runs"} or "artifact_origin" in row:
        return row
    return {**row, "artifact_origin": current_artifact_origin()}


def insert(table: str, row: dict, *, conn: sqlite3.Connection | None = None) -> int | str:
    """Insert a row, optionally joining a caller-owned transaction.

    JSON columns are encoded automatically.  A supplied connection is never
    committed or closed here; the caller owns that transaction boundary.
    """
    data = _encode(table, _with_root_provenance(table, row))
    cols = ", ".join(f'"{c}"' for c in data)
    ph = ", ".join("?" for _ in data)
    if conn is not None:
        cur = conn.execute(f'INSERT INTO {table} ({cols}) VALUES ({ph})',
                           list(data.values()))
        return cur.lastrowid
    with get_conn() as conn:
        cur = conn.execute(f'INSERT INTO {table} ({cols}) VALUES ({ph})',
                           list(data.values()))
        conn.commit()
        return cur.lastrowid


def insert_analysis_artifact(row: dict, refs: list[dict[str, str]]) -> None:
    """Atomically insert artifact metadata and its normalized dependency edges."""
    data = _encode("analysis_artifacts", row)
    cols = ", ".join(f'"{column}"' for column in data)
    placeholders = ", ".join("?" for _ in data)
    with get_conn() as conn:
        conn.execute(
            f'INSERT INTO analysis_artifacts ({cols}) VALUES ({placeholders})',
            list(data.values()),
        )
        conn.executemany(
            "INSERT INTO analysis_artifact_sources "
            "(artifact_id, source_artifact_id, role) VALUES (?,?,?)",
            [(row["artifact_id"], ref["artifact_id"], ref["role"]) for ref in refs],
        )
        conn.commit()


def upsert(table: str, row: dict) -> None:
    """INSERT OR REPLACE — for PK-keyed tables (test_library, monitoring, ...)."""
    data = _encode(table, _with_root_provenance(table, row))
    cols = ", ".join(f'"{c}"' for c in data)
    ph = ", ".join("?" for _ in data)
    with get_conn() as conn:
        conn.execute(f'INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})',
                     list(data.values()))
        conn.commit()


def update(table: str, where: dict, changes: dict, *,
           conn: sqlite3.Connection | None = None) -> int:
    """Update matching rows, optionally joining a caller-owned transaction."""
    data = _encode(table, changes)
    set_clause = ", ".join(f'"{c}" = ?' for c in data)
    wc, wv = _where(where)
    if conn is not None:
        cur = conn.execute(f'UPDATE {table} SET {set_clause}{wc}',
                           list(data.values()) + wv)
        return cur.rowcount
    with get_conn() as conn:
        cur = conn.execute(f'UPDATE {table} SET {set_clause}{wc}',
                           list(data.values()) + wv)
        conn.commit()
        return cur.rowcount


def _where(filters: dict) -> tuple[str, list]:
    if not filters:
        return "", []
    clause = " WHERE " + " AND ".join(f'"{k}" = ?' for k in filters)
    return clause, list(filters.values())


def query(table_name: str, order_by: str | None = None, *,
          conn: sqlite3.Connection | None = None, **filters) -> list[dict]:
    wc, wv = _where(filters)
    ob = f" ORDER BY {order_by}" if order_by else ""
    if conn is not None:
        rows = conn.execute(f"SELECT * FROM {table_name}{wc}{ob}", wv).fetchall()
        return [_decode(table_name, row) for row in rows]
    with get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM {table_name}{wc}{ob}", wv).fetchall()
    return [_decode(table_name, r) for r in rows]


_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def query_page(table_name: str, *, limit: int, offset: int,
               order_by: tuple[tuple[str, str], ...],
               contains: dict[str, str] | None = None, **filters) -> tuple[list[dict], int]:
    """Return one decoded SQL page plus its filtered total."""
    identifiers = [table_name, *filters, *(contains or {}), *(key for key, _direction in order_by)]
    if any(not _SQL_IDENTIFIER.fullmatch(value) for value in identifiers):
        raise ValueError("invalid SQL identifier")
    clauses, values = [], []
    for key, value in filters.items():
        clauses.append(f'"{key}" = ?')
        values.append(value)
    for key, value in (contains or {}).items():
        clauses.append(f'LOWER(COALESCE("{key}", \'\')) LIKE ?')
        values.append(f"%{value.lower()}%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    ordering = ", ".join(
        f'"{key}" {"DESC" if direction.upper() == "DESC" else "ASC"}'
        for key, direction in order_by
    )
    with get_conn() as conn:
        total = int(conn.execute(
            f'SELECT COUNT(*) FROM "{table_name}"{where}', values,
        ).fetchone()[0])
        rows = conn.execute(
            f'SELECT * FROM "{table_name}"{where} ORDER BY {ordering} LIMIT ? OFFSET ?',
            [*values, limit, offset],
        ).fetchall()
    return [_decode(table_name, row) for row in rows], total


def query_one(table_name: str, *, conn: sqlite3.Connection | None = None,
              **filters) -> dict | None:
    rows = query(table_name, conn=conn, **filters)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | list = ()) -> list[dict]:
    """Escape hatch for joins/aggregates. Returns list of plain dicts."""
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        if cur.description is None:
            return []
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete(table_name: str, **filters) -> int:
    wc, wv = _where(filters)
    with get_conn() as conn:
        cur = conn.execute(f"DELETE FROM {table_name}{wc}", wv)
        conn.commit()
        return cur.rowcount


# --- WSP-08 / D-19 — admin factory reset shared helpers -----------------------
# Item-lifecycle tag_assignments — the object_types actually used when tagging
# an item and everything derived from it (routers/v3.py, ai/v2/service.py,
# ai/v2/issues.py: "dq_item", "plan_v2", "results_v2", "issues_v2"). When every
# row in those four tables is wiped wholesale (as both reset grades do), any
# tag_assignments row pointing at one of them is orphaned and must go too.
# Dimensions/values/aliases/taxonomy versions are governed reference data, not
# per-item work-product, and are NEVER touched by a reset.
_ITEM_TAG_OBJECT_TYPES = ["dq_item", "plan_v2", "results_v2", "issues_v2"]


def _rca_tables(conn: sqlite3.Connection) -> list[str]:
    """Every rca_* table that actually exists in this DB, read live off the
    schema (not hardcoded) so a reset never silently misses a table a later
    RCA stage adds."""
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rca_%'"
    ).fetchall()]


def _upload_root() -> Path:
    """Mirrors ai/v2/service.py's UPLOAD_ROOT resolution (UPLOAD_DIR env, else
    backend/uploads) so a reset wipes wherever items actually save their
    files, in every environment (e2e sandboxing included)."""
    return Path(os.environ.get("UPLOAD_DIR")
                or (Path(__file__).resolve().parent / "uploads")).resolve()


def _kb_storage_root() -> Path:
    """Mirrors kb.py's KB_STORAGE_ROOT resolution (KB_STORAGE_DIR env, else
    backend/kb_storage)."""
    return Path(os.environ.get("KB_STORAGE_DIR")
                or (Path(__file__).resolve().parent / "kb_storage")).resolve()


def _analysis_artifact_root() -> Path:
    """Mirror analysis_runtime's configurable payload-storage location."""
    return ANALYSIS_ARTIFACT_ROOT


def _wipe_dir_contents(root: Path) -> None:
    """Delete every child of ``root`` (files and subdirectories) but leave the
    directory itself in place, ready for new writes."""
    if root.exists():
        import shutil  # noqa: PLC0415
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    root.mkdir(parents=True, exist_ok=True)


def _clear_item_pipeline(conn: sqlite3.Connection) -> dict[str, int]:
    """Delete every row belonging to an item's lifecycle: the F1 work-product
    tables (_WORKPRODUCT_TABLES — now including issues_v2/tracked_issues_v2),
    every rca_* case/evidence table, and the tag_assignments rows keyed to an
    item-lifecycle object. Shared by reset_demo (surgical) and
    wipe_all_items (full). Returns per-table delete counts.
    """
    counts: dict[str, int] = {}
    for t in _WORKPRODUCT_TABLES:
        counts[t] = conn.execute(f"DELETE FROM {t}").rowcount
    # AST-17 fingerprints are immutable children of snapshots and therefore
    # leave with the reset's snapshot work product; keep this auxiliary table
    # out of the human-facing count vocabulary.
    conn.execute("DELETE FROM dq_snapshot_fingerprints")
    # ANL-03 exception: reset removes the asset objects, so it legitimately
    # removes their usage history too.  This named helper is the only event
    # deletion path; ordinary flows never UPDATE or DELETE usage_events.
    from analytics.events import delete_for_factory_reset  # noqa: PLC0415
    delete_for_factory_reset(conn)
    for t in _rca_tables(conn):
        counts[t] = conn.execute(f"DELETE FROM {t}").rowcount
    ph = ",".join("?" for _ in _ITEM_TAG_OBJECT_TYPES)
    counts["tag_assignments"] = conn.execute(
        f"DELETE FROM tag_assignments WHERE object_type IN ({ph})",
        _ITEM_TAG_OBJECT_TYPES).rowcount
    return counts


def _select_ids_any(conn: sqlite3.Connection, table: str, result_column: str,
                    filters: dict[str, set[str]]) -> set[str]:
    """Select IDs using bounded, parameterized ``column IN (...)`` clauses."""
    clauses: list[str] = []
    values: list[str] = []
    for column, candidates in filters.items():
        if not candidates:
            continue
        clauses.append(f'"{column}" IN ({",".join("?" for _ in candidates)})')
        values.extend(sorted(candidates))
    if not clauses:
        return set()
    rows = conn.execute(
        f'SELECT "{result_column}" FROM "{table}" WHERE ' + " OR ".join(clauses),
        values,
    ).fetchall()
    return {str(row[0]) for row in rows if row[0] is not None}


def _delete_ids_any(conn: sqlite3.Connection, table: str,
                    filters: dict[str, set[str]]) -> int:
    clauses: list[str] = []
    values: list[str] = []
    for column, candidates in filters.items():
        if not candidates:
            continue
        clauses.append(f'"{column}" IN ({",".join("?" for _ in candidates)})')
        values.extend(sorted(candidates))
    if not clauses:
        return 0
    return conn.execute(
        f'DELETE FROM "{table}" WHERE ' + " OR ".join(clauses), values,
    ).rowcount


def wipe_development_artifacts() -> dict:
    """Remove only asset trees explicitly marked ``development``.

    Rows migrated from older databases remain ``unclassified`` and are
    deliberately protected: fixture-looking names and ``system`` actors are
    not reliable proof that a record is disposable. The asset/snapshot roots
    provide the trust boundary; all descendants are selected from their real
    relational keys and the ID sequence is never rewound.
    """
    init_schema()
    counts: dict[str, int] = {}
    payload_paths: set[str] = set()
    with get_conn() as conn:
        asset_ids = {str(row[0]) for row in conn.execute(
            "SELECT asset_id FROM dq_assets WHERE artifact_origin='development'"
        )}
        item_rows = conn.execute(
            "SELECT item_id, dataset_family_id FROM dq_items "
            "WHERE artifact_origin='development' OR dataset_family_id IN "
            "(SELECT asset_id FROM dq_assets WHERE artifact_origin='development')"
        ).fetchall()
        item_ids = {str(row[0]) for row in item_rows}

        plan_ids = _select_ids_any(conn, "plan_v2", "row_id", {"item_id": item_ids})
        result_ids_v2 = _select_ids_any(
            conn, "results_v2", "result_id", {"item_id": item_ids, "row_id": plan_ids}
        )
        run_ids = {str(row[0]) for row in conn.execute(
            "SELECT run_id FROM diag_runs WHERE artifact_origin='development'"
        )}
        run_ids |= _select_ids_any(conn, "diag_runs", "run_id", {"item_id": item_ids})
        run_ids |= _select_ids_any(
            conn, "analysis_manifests", "run_id", {"snapshot_id": item_ids}
        )
        diag_result_ids = _select_ids_any(
            conn, "diag_results", "result_id", {"run_id": run_ids}
        )
        finding_ids = _select_ids_any(
            conn, "diag_findings", "finding_id",
            {"run_id": run_ids, "result_id": diag_result_ids},
        )
        issue_ids = _select_ids_any(
            conn, "issues_v2", "issue_row_id",
            {"item_id": item_ids, "run_id": run_ids, "finding_id": finding_ids},
        )
        case_ids = _select_ids_any(
            conn, "rca_cases", "case_id", {"item_id": item_ids, "issue_row_id": issue_ids}
        )

        artifact_ids = _select_ids_any(
            conn, "analysis_artifacts", "artifact_id",
            {"asset_id": asset_ids, "snapshot_id": item_ids, "run_id": run_ids},
        )
        while artifact_ids:
            downstream = _select_ids_any(
                conn, "analysis_artifact_sources", "artifact_id",
                {"source_artifact_id": artifact_ids},
            ) - artifact_ids
            if not downstream:
                break
            artifact_ids |= downstream
        if artifact_ids:
            payload_paths = _select_ids_any(
                conn, "analysis_artifacts", "payload_path", {"artifact_id": artifact_ids}
            )

        observation_ids = _select_ids_any(
            conn, "analysis_observations", "observation_id",
            {"run_id": run_ids, "snapshot_id": item_ids, "artifact_id": artifact_ids,
             "issue_row_id": issue_ids},
        )

        # RCA is a multi-level graph below case_id; collect its intermediate IDs.
        suspect_ids = _select_ids_any(conn, "rca_suspects", "suspect_id", {"case_id": case_ids})
        look_ids = _select_ids_any(conn, "rca_looks", "look_id", {"case_id": case_ids})
        hypothesis_ids = _select_ids_any(
            conn, "rca_hypotheses", "hypothesis_id",
            {"case_id": case_ids, "suspect_id": suspect_ids},
        )
        check_ids = _select_ids_any(
            conn, "rca_confirmation_checks", "check_id", {"hypothesis_id": hypothesis_ids}
        )
        proposal_ids = _select_ids_any(
            conn, "rca_fix_proposals", "id", {"hypothesis_id": hypothesis_ids}
        )
        group_ids = _select_ids_any(
            conn, "rca_failure_groups", "group_id", {"case_id": case_ids}
        )
        rca_object_ids = (case_ids | suspect_ids | look_ids | hypothesis_ids |
                          check_ids | proposal_ids | group_ids)

        deletion_plan = [
            ("analysis_observation_dispositions", {"observation_id": observation_ids}),
            ("analysis_observations", {"observation_id": observation_ids}),
            ("analysis_artifact_events", {"artifact_id": artifact_ids}),
            ("analysis_artifact_sources", {"artifact_id": artifact_ids,
                                           "source_artifact_id": artifact_ids}),
            ("analysis_artifacts", {"artifact_id": artifact_ids}),
            ("diag_binning_revisions", {"item_id": item_ids, "run_id": run_ids,
                                        "result_id": diag_result_ids}),
            ("diag_dispositions", {"target_id": run_ids | diag_result_ids | finding_ids}),
            ("diag_findings", {"finding_id": finding_ids, "run_id": run_ids}),
            ("diag_results", {"result_id": diag_result_ids, "run_id": run_ids}),
            ("diag_inference_events", {"run_id": run_ids}),
            ("diag_run_decisions", {"run_id": run_ids}),
            ("analysis_manifests", {"run_id": run_ids, "snapshot_id": item_ids}),
            ("diag_runs", {"run_id": run_ids, "item_id": item_ids}),
            ("rca_attached_failures", {"group_id": group_ids, "run_id": run_ids}),
            ("rca_judge_decisions", {"check_id": check_ids}),
            ("rca_confirmation_checks", {"check_id": check_ids}),
            ("rca_fix_approvals", {"fix_proposal_id": proposal_ids}),
            ("rca_fix_proposals", {"id": proposal_ids}),
            ("rca_look_executions", {"look_id": look_ids}),
            ("rca_suspect_history", {"suspect_id": suspect_ids, "look_id": look_ids}),
            ("rca_hypotheses", {"hypothesis_id": hypothesis_ids}),
            ("rca_human_questions", {"case_id": case_ids, "asked_by_look_id": look_ids}),
            ("rca_looks", {"look_id": look_ids}),
            ("rca_suspects", {"suspect_id": suspect_ids}),
            ("rca_failure_groups", {"group_id": group_ids}),
            ("rca_case_files", {"case_id": case_ids}),
            ("rca_closures", {"case_id": case_ids}),
            ("rca_coverage_passes", {"case_id": case_ids}),
            ("rca_state_transitions", {"case_id": case_ids}),
            ("rca_symptom_accounting", {"case_id": case_ids}),
            ("rca_audit_events", {"object_id": rca_object_ids}),
            ("rca_cases", {"case_id": case_ids}),
            ("tracked_issues_v2", {"issue_row_id": issue_ids}),
            ("issues_v2", {"issue_row_id": issue_ids, "item_id": item_ids}),
            ("results_v2", {"result_id": result_ids_v2, "item_id": item_ids,
                            "row_id": plan_ids}),
            ("plan_v2", {"row_id": plan_ids, "item_id": item_ids}),
            ("scores_v2", {"item_id": item_ids}),
        ]
        for table, filters in deletion_plan:
            counts[table] = _delete_ids_any(conn, table, filters)

        tag_object_ids = item_ids | plan_ids | result_ids_v2 | issue_ids
        counts["tag_assignments"] = _delete_ids_any(
            conn, "tag_assignments", {"object_id": tag_object_ids}
        )
        counts["dq_snapshot_fingerprints"] = _delete_ids_any(
            conn, "dq_snapshot_fingerprints", {"snapshot_id": item_ids}
        )
        for table in ("dq_item_warnings", "dq_item_mappings", "variable_inventory",
                      "dq_item_tables", "dq_item_files"):
            counts[table] = _delete_ids_any(conn, table, {"item_id": item_ids})
        counts["dq_items"] = _delete_ids_any(conn, "dq_items", {"item_id": item_ids})
        for table in ("dq_asset_dictionaries", "dq_asset_events", "dq_asset_versions"):
            counts[table] = _delete_ids_any(conn, table, {"asset_id": asset_ids})
        counts["dq_assets"] = _delete_ids_any(conn, "dq_assets", {"asset_id": asset_ids})
        counts["app_fsm"] = _delete_ids_any(
            conn, "app_fsm", {"entity_id": item_ids | asset_ids | run_ids | case_ids}
        )
        conn.commit()

    upload_root = _upload_root()
    for item_id in item_ids:
        item_path = (upload_root / item_id).resolve()
        if item_path.parent == upload_root and item_path.exists():
            import shutil  # noqa: PLC0415
            shutil.rmtree(item_path)
    artifact_root = _analysis_artifact_root().resolve()
    for payload_path in payload_paths:
        candidate = (artifact_root / payload_path).resolve()
        if candidate.is_relative_to(artifact_root) and candidate.is_file():
            candidate.unlink(missing_ok=True)

    protected = {
        "assets": len(query("dq_assets")) - len(query("dq_assets", artifact_origin="development")),
        "unclassified_assets": len(query("dq_assets", artifact_origin="unclassified")),
        "runs": len(query("diag_runs")) - len(query("diag_runs", artifact_origin="development")),
        "unclassified_runs": len(query("diag_runs", artifact_origin="unclassified")),
    }
    return {"deleted": counts, "protected": protected}


def wipe_diagnostics(selected_run_ids: set[str] | None = None) -> dict:
    """Clear diagnostic execution state while preserving Data Sourcing.

    This reset grade deliberately keeps every asset, snapshot, uploaded file,
    dictionary, inventory row, fingerprint, and Data-Sourcing-owned AAR
    profile. It removes all ``diag_runs`` and their relational descendants,
    plus their run-linked AAR artifacts. In all-runs mode it also removes AAR
    types owned by diagnostics/supporting analyses, including shared run-less
    bin drafts; selected-run mode preserves those because another run may
    reuse them. Diagnostic-created issues and RCA cases are removed as
    descendants so no orphaned human workflow remains.
    """
    init_schema()
    counts: dict[str, int] = {}
    payload_paths: set[str] = set()
    from domains.aar.types import list_artifact_types  # noqa: PLC0415
    diagnostic_types = {
        row["artifact_type"] for row in list_artifact_types()
        if row.get("owner") != "Data Sourcing"
    }
    with get_conn() as conn:
        available_run_ids: set[str] = set()
        for table in ("diag_runs", "diag_results", "diag_findings",
                      "diag_run_decisions", "diag_inference_events", "diag_binning_revisions",
                      "analysis_manifests", "analysis_observations"):
            available_run_ids |= {str(row[0]) for row in conn.execute(
                f'SELECT run_id FROM "{table}" WHERE run_id IS NOT NULL'
            )}
        wipe_all = selected_run_ids is None
        run_ids = available_run_ids if wipe_all else set(selected_run_ids)
        diag_result_ids = _select_ids_any(
            conn, "diag_results", "result_id", {"run_id": run_ids}
        )
        finding_ids = _select_ids_any(
            conn, "diag_findings", "finding_id",
            {"run_id": run_ids, "result_id": diag_result_ids},
        )
        issue_ids = _select_ids_any(
            conn, "issues_v2", "issue_row_id",
            {"run_id": run_ids, "finding_id": finding_ids},
        )
        case_ids = _select_ids_any(
            conn, "rca_cases", "case_id", {"issue_row_id": issue_ids}
        )
        artifact_ids = _select_ids_any(
            conn, "analysis_artifacts", "artifact_id",
            {"run_id": run_ids,
             "artifact_type": diagnostic_types if wipe_all else set()},
        )
        while artifact_ids:
            downstream = _select_ids_any(
                conn, "analysis_artifact_sources", "artifact_id",
                {"source_artifact_id": artifact_ids},
            ) - artifact_ids
            if not downstream:
                break
            artifact_ids |= downstream
        if artifact_ids:
            payload_paths = _select_ids_any(
                conn, "analysis_artifacts", "payload_path", {"artifact_id": artifact_ids}
            )
        observation_ids = _select_ids_any(
            conn, "analysis_observations", "observation_id",
            {"run_id": run_ids, "artifact_id": artifact_ids, "issue_row_id": issue_ids},
        )

        suspect_ids = _select_ids_any(conn, "rca_suspects", "suspect_id", {"case_id": case_ids})
        look_ids = _select_ids_any(conn, "rca_looks", "look_id", {"case_id": case_ids})
        hypothesis_ids = _select_ids_any(
            conn, "rca_hypotheses", "hypothesis_id",
            {"case_id": case_ids, "suspect_id": suspect_ids},
        )
        check_ids = _select_ids_any(
            conn, "rca_confirmation_checks", "check_id", {"hypothesis_id": hypothesis_ids}
        )
        proposal_ids = _select_ids_any(
            conn, "rca_fix_proposals", "id", {"hypothesis_id": hypothesis_ids}
        )
        group_ids = _select_ids_any(
            conn, "rca_failure_groups", "group_id", {"case_id": case_ids}
        )
        rca_object_ids = (case_ids | suspect_ids | look_ids | hypothesis_ids |
                          check_ids | proposal_ids | group_ids)

        deletion_plan = [
            ("analysis_observation_dispositions", {"observation_id": observation_ids}),
            ("analysis_observations", {"observation_id": observation_ids}),
            ("analysis_artifact_events", {"artifact_id": artifact_ids}),
            ("analysis_artifact_sources", {"artifact_id": artifact_ids,
                                           "source_artifact_id": artifact_ids}),
            ("analysis_artifacts", {"artifact_id": artifact_ids}),
            ("diag_binning_revisions", {"run_id": run_ids,
                                        "result_id": diag_result_ids}),
            ("diag_dispositions", {"target_id": run_ids | diag_result_ids | finding_ids}),
            ("diag_findings", {"finding_id": finding_ids, "run_id": run_ids}),
            ("diag_results", {"result_id": diag_result_ids, "run_id": run_ids}),
            ("diag_inference_events", {"run_id": run_ids}),
            ("diag_run_decisions", {"run_id": run_ids}),
            ("analysis_manifests", {"run_id": run_ids}),
            ("rca_attached_failures", {"group_id": group_ids, "run_id": run_ids}),
            ("rca_judge_decisions", {"check_id": check_ids}),
            ("rca_confirmation_checks", {"check_id": check_ids}),
            ("rca_fix_approvals", {"fix_proposal_id": proposal_ids}),
            ("rca_fix_proposals", {"id": proposal_ids}),
            ("rca_look_executions", {"look_id": look_ids}),
            ("rca_suspect_history", {"suspect_id": suspect_ids, "look_id": look_ids}),
            ("rca_hypotheses", {"hypothesis_id": hypothesis_ids}),
            ("rca_human_questions", {"case_id": case_ids, "asked_by_look_id": look_ids}),
            ("rca_looks", {"look_id": look_ids}),
            ("rca_suspects", {"suspect_id": suspect_ids}),
            ("rca_failure_groups", {"group_id": group_ids}),
            ("rca_case_files", {"case_id": case_ids}),
            ("rca_closures", {"case_id": case_ids}),
            ("rca_coverage_passes", {"case_id": case_ids}),
            ("rca_state_transitions", {"case_id": case_ids}),
            ("rca_symptom_accounting", {"case_id": case_ids}),
            ("rca_audit_events", {"object_id": rca_object_ids}),
            ("rca_cases", {"case_id": case_ids}),
            ("tracked_issues_v2", {"issue_row_id": issue_ids}),
            ("issues_v2", {"issue_row_id": issue_ids}),
            ("diag_runs", {"run_id": run_ids}),
        ]
        for table, filters in deletion_plan:
            counts[table] = _delete_ids_any(conn, table, filters)
        counts["tag_assignments"] = _delete_ids_any(
            conn, "tag_assignments", {"object_id": issue_ids | finding_ids | diag_result_ids}
        )
        counts["app_fsm"] = _delete_ids_any(
            conn, "app_fsm", {"entity_id": run_ids | case_ids}
        )
        conn.commit()

    artifact_root = _analysis_artifact_root().resolve()
    for payload_path in payload_paths:
        candidate = (artifact_root / payload_path).resolve()
        if candidate.is_relative_to(artifact_root) and candidate.is_file():
            candidate.unlink(missing_ok=True)
    return {"deleted": counts, "scope": "all" if wipe_all else "selected",
            "selected_run_ids": sorted(run_ids), "preserved": {
        "assets": len(query("dq_assets")),
        "snapshots": len(query("dq_items")),
        "sourcing_artifacts": len([
            row for row in query("analysis_artifacts")
            if row.get("artifact_type") not in diagnostic_types
        ]),
    }}


def reset_demo() -> dict:
    """F16 / WSP-08 D-19 — surgical factory reset. Clears the demo-uploaded
    Retail Risk DB inventory, EVERY item-lifecycle artefact (the F1
    work-product tables, issues_v2/tracked_issues_v2, all rca_* case/evidence
    tables, item-keyed tag_assignments) and the uploads directory on disk;
    preserves the pre-seeded DBs, the static test_library, user accounts (and
    their F13 profile edits), active sessions, taxonomy dimensions/values/
    aliases/versions, and the knowledge base (kb_* tables + storage).
    Master/physical warehouse is never touched. Idempotent — a second call
    returns all-zero delete counts. Returns delete counts.
    """
    init_schema()
    counts: dict[str, int] = {}
    with get_conn() as conn:
        # 1. Remove ONLY the demo logical DB from the inventory.
        counts["ingested_databases"] = conn.execute(
            'DELETE FROM ingested_databases WHERE logical_db = ?', (DEMO_DB,)).rowcount
        counts["table_metadata"] = conn.execute(
            'DELETE FROM table_metadata WHERE logical_db = ?', (DEMO_DB,)).rowcount
        # 2. Clear the full item pipeline (work-product + issues + RCA cases +
        #    item-keyed tag assignments) — WSP-08 "items + ALL derived
        #    artefacts go" for the surgical grade.
        counts.update(_clear_item_pipeline(conn))
        # 0.5.0 Step 3 (AST-01/D-29/P-10): dq_assets/dq_asset_versions/
        # dq_asset_dictionaries/dq_asset_events are 1:1 derived from the
        # dq_items rows _clear_item_pipeline() JUST deleted above (every
        # dq_assets.asset_id IS a dataset_family_id) — they are NOT added to
        # _WORKPRODUCT_TABLES (that list feeds test_reset_language.py's
        # ADM-04 label-completeness gate, which Step 2 owns and this step
        # must not silently grow) but they must still go here, in the same
        # breath as the items they describe. Leaving them behind would strand
        # dq_assets rows that name a family with zero surviving snapshots —
        # AND, since the human-quotable id_sequences counters below reset to
        # a clean baseline while a stale system_id survived, the very next
        # allocate() would collide against it (ux_dq_assets_system_id fires
        # loudly, but only after the fact). A clean slate means genuinely
        # clean, not "the counter forgot, but the rows still remember."
        for t in ("dq_assets", "dq_asset_versions", "dq_asset_dictionaries", "dq_asset_events"):
            counts[t] = conn.execute(f"DELETE FROM {t}").rowcount
        # AST-01/D-29/P-10: the human-quotable ID counters restart clean too
        # — the exception AST-01 states verbatim. routers/admin.py reads the
        # pre-delete counters (assets.identity.epoch_snapshot()) BEFORE this
        # function runs and carries them into the post-delete audit row as
        # the id_epoch boundary (R-08).
        counts["id_sequences"] = conn.execute("DELETE FROM id_sequences").rowcount
        counts["object_contexts"] = conn.execute(
            "DELETE FROM object_contexts WHERE scope = 'demo'"
        ).rowcount
        counts["context_links"] = conn.execute(
            "DELETE FROM context_links WHERE from_context_id NOT IN "
            "(SELECT context_id FROM object_contexts)"
        ).rowcount
        # 3. Clear stale FSM rows but PRESERVE active sessions (no mid-demo logout).
        ph = ",".join("?" for _ in _FSM_RESET_ENTITY_TYPES)
        counts["app_fsm"] = conn.execute(
            f"DELETE FROM app_fsm WHERE entity_type IN ({ph})",
            _FSM_RESET_ENTITY_TYPES).rowcount
        # 4. users + test_library + the pre-seeded ingested_databases + taxonomy
        #    dimensions/values/aliases + the knowledge base are intentionally
        #    NOT touched here.
        conn.commit()
    # Every deleted item's source files are now orphaned garbage on disk —
    # wipe them (same disk logic wipe_all_items uses; UPLOAD_DIR-aware).
    _wipe_dir_contents(_upload_root())
    _wipe_dir_contents(_analysis_artifact_root())
    # Restore ONLY the static demo baseline cleared above (tickets + monitoring).
    # users, test_library, agents and the two pre-seeded DBs survive untouched,
    # so profile edits (F13) and pre-seeded inventory persist across the reset.
    from seeds import reseed_static  # noqa: PLC0415
    reseeded = reseed_static()
    return {"reset": True, "deleted": counts, "reseeded": reseeded}


if __name__ == "__main__":
    init_schema()
    reset_demo()
    reset_demo()
    with get_conn() as c:
        tabs = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("tables:", tabs)
    print("idempotent reset OK")


def wipe_all_items() -> dict:
    """WSP-08 / D-19 — full factory wipe ("blank slate" grade). Clears
    everything reset_demo (surgical) clears PLUS every reference/platform
    table that isn't explicitly preserved: the knowledge base (kb_* rows +
    kb_storage on disk), tag_assignments entirely, ALL ingested inventory
    (not just the demo DB), object_contexts/context_links, and the static
    seed tables (test_library, agent_skills, dq_framework_areas/families,
    fw_areas/fw_tests/fw_family_weights, tenants, tag_dimensions/values/
    aliases/taxonomy_versions).

    PRESERVES ONLY: users + active sessions (the admin's own request must
    complete, and they must stay logged in), transaction_log (the audit
    trail must survive its own audit event), feature_flags (an operator's
    flag flips are never silently reset), and the schema itself.

    Platform seed data is then restored by calling the same seeders
    main.py's boot path runs on every boot (seed_agents, seed_dq_framework,
    seed_framework_register — the 0.4.0 register/taxonomy/thresholds —
    and seed_platform_and_taxonomy), so re-boot equivalence holds (2-T6)
    and the app is immediately usable — no separate restart required.
    (Phase 3: the retired Galileo framework seed and seed_test_library are
    gone with the 14-test framework, FWK-13.) Idempotent — a second call
    deletes nothing further (the seeders are themselves idempotent
    upserts). Returns delete + reseed counts.
    """
    init_schema()
    counts: dict[str, int] = {}
    with get_conn() as conn:
        counts.update(_clear_item_pipeline(conn))
        # 0.5.0 Step 3 (AST-01/D-29/P-10) — same reasoning as the surgical
        # grade above: the asset-lifecycle tables are 1:1 derived from the
        # dq_items rows _clear_item_pipeline() just deleted, and must go with
        # them or they strand orphaned rows that collide against a reset
        # id_sequences counter's freshly-reissued IDs.
        for t in ("dq_assets", "dq_asset_versions", "dq_asset_dictionaries", "dq_asset_events"):
            counts[t] = conn.execute(f"DELETE FROM {t}").rowcount
        # AST-01/D-29/P-10 — same epoch-reset rule as the surgical grade above.
        counts["id_sequences"] = conn.execute("DELETE FROM id_sequences").rowcount
        # tag_assignments entirely (not just item-keyed) — blank slate.
        counts["tag_assignments"] += conn.execute(
            "DELETE FROM tag_assignments").rowcount
        # ALL ingested inventory, not just the demo DB.
        counts["ingested_databases"] = conn.execute("DELETE FROM ingested_databases").rowcount
        counts["table_metadata"] = conn.execute("DELETE FROM table_metadata").rowcount
        counts["object_contexts"] = conn.execute("DELETE FROM object_contexts").rowcount
        counts["context_links"] = conn.execute("DELETE FROM context_links").rowcount
        # Knowledge base rows (bytes on disk wiped below).
        for t in ("diagnostic_kb_packages", "kb_retrieval_manifests",
                  "kb_rule_proposal_evidence", "kb_rules", "kb_sections",
                  "kb_document_versions", "kb_documents"):
            counts[t] = conn.execute(f"DELETE FROM {t}").rowcount
        # Static/reference platform tables — cleared then restored by the
        # seeders below (same idempotent calls main.py's boot path makes).
        # test_library / fw_* hold no seeded content since Phase 3 (FWK-13)
        # but are still cleared so a pre-0.4.0 DB wipes to a true blank slate.
        for t in ("test_library", "agent_skills", "dq_framework_areas",
                  "dq_framework_families", "fw_areas", "fw_tests", "fw_family_weights",
                  "diagnostic_register", "framework_taxonomy", "framework_test_areas",
                  "threshold_settings",
                  "tenants", "tag_dimensions", "tag_values", "tag_aliases",
                  "tag_taxonomy_versions"):
            counts[t] = conn.execute(f"DELETE FROM {t}").rowcount
        # Every FSM row except active sessions (admin stays logged in).
        counts["app_fsm"] = conn.execute(
            "DELETE FROM app_fsm WHERE entity_type != 'session'").rowcount
        # feature_flags, users, transaction_log intentionally untouched.
        conn.commit()
    _wipe_dir_contents(_upload_root())
    _wipe_dir_contents(_analysis_artifact_root())
    _wipe_dir_contents(_kb_storage_root())
    from seeds import (  # noqa: PLC0415
        seed_agents, seed_dq_framework, seed_framework_register,
        seed_platform_and_taxonomy,
    )
    reseeded = {
        "agent_skills": seed_agents(),
        "dq_framework": seed_dq_framework(),
        "framework_register": seed_framework_register(),
        "platform_and_taxonomy": seed_platform_and_taxonomy(),
    }
    return {"deleted": counts, "reseeded": reseeded}
