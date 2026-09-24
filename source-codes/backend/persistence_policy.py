"""Authoritative ownership and full-wipe lifecycle for SQLite tables.

Every application table must appear exactly once.  The policy is intentionally
independent of ``system_db`` so schema/reset tests can detect an unclassified
table without creating an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableLifecycle:
    owner: str
    full_wipe: str  # clear | preserve | preserve_sessions | reseed


PERSISTENCE_TABLE_POLICY: dict[str, TableLifecycle] = {}


def _declare(owner: str, full_wipe: str, names: tuple[str, ...]) -> None:
    for name in names:
        if name in PERSISTENCE_TABLE_POLICY:
            raise RuntimeError(f"duplicate persistence policy for {name}")
        PERSISTENCE_TABLE_POLICY[name] = TableLifecycle(owner, full_wipe)


_declare("identity_and_audit", "preserve", (
    "users", "transaction_log", "feature_flags",
))
_declare("identity_and_audit", "preserve_sessions", ("app_fsm",))

_declare("platform_reference", "reseed", (
    "agent_skills", "dq_framework_areas", "dq_framework_families",
    "diagnostic_register", "framework_taxonomy", "framework_test_areas",
    "threshold_settings", "tenants", "tag_dimensions", "tag_values",
    "tag_aliases", "tag_taxonomy_versions",
))

_declare("retired_workbench", "clear", (
    "test_library", "test_plan", "test_db_links", "test_dossiers",
    "test_versions", "design_workbench", "monitoring", "schedules",
    "notifications", "run_results", "health_scores", "tickets",
    "hitl_decisions", "fw_areas", "fw_tests", "fw_family_weights",
))

_declare("data_sourcing", "clear", (
    "ingested_databases", "table_metadata", "dq_assets", "dq_asset_versions",
    "dq_asset_dictionaries", "dq_asset_events", "id_sequences", "dq_items",
    "dq_item_files", "dq_item_tables", "variable_inventory", "dq_item_mappings",
    "dq_item_warnings", "dq_snapshot_fingerprints",
    "dataset_structure_materialization_jobs",
    "dataset_structure_profile_publications",
    "dataset_structure_profile_publication_completions",
    "dataset_structure_backfill_runs", "technical_row_id_transforms",
    "dataset_structure_materialization_reconcile_cursor",
    "dataset_structure_staged_reviews",
    "dataset_structure_review_states", "dataset_structure_review_drafts",
    "dataset_structure_review_idempotency",
    "dataset_structure_review_decision_batches",
))

_declare("governed_taxonomy", "clear", ("tag_assignments",))
_declare("context_memory", "clear", ("object_contexts", "context_links"))

_declare("knowledge_base", "preserve", (
    "kb_documents", "kb_document_versions", "kb_sections", "kb_rules",
    "kb_rule_proposal_evidence", "kb_shelf_life_defaults",
    "diagnostic_kb_packages",
))
_declare("knowledge_usage", "clear", ("kb_retrieval_manifests",))

_declare("legacy_analysis_compatibility", "clear", (
    "plan_v2", "results_v2", "scores_v2", "issues_v2", "tracked_issues_v2",
))

_declare("root_cause_analysis", "clear", (
    "rca_cases", "rca_state_transitions", "rca_failure_groups",
    "rca_attached_failures", "rca_case_files", "rca_looks",
    "rca_look_executions", "rca_suspects", "rca_suspect_history",
    "rca_human_questions", "rca_coverage_passes", "rca_hypotheses",
    "rca_confirmation_checks", "rca_judge_decisions",
    "rca_symptom_accounting", "rca_fix_proposals", "rca_fix_approvals",
    "rca_closures", "rca_audit_events", "rca_aar_links",
))

_declare("test_lab", "clear", (
    "diag_runs", "diag_run_decisions", "diag_inference_events", "diag_results",
    "diag_findings", "diag_dispositions", "d06_dsc_cadence_shadow_audit",
    "diagnostic_execution_context", "diag_binning_revisions",
    "analysis_manifests", "analysis_observations",
    "analysis_observation_dispositions",
))

_declare("analysis_artifact_repository", "clear", (
    "analysis_artifacts", "analysis_artifact_events", "analysis_artifact_sources",
))
_declare("product_analytics", "clear", ("usage_events",))


def tables_for_full_wipe(disposition: str) -> tuple[str, ...]:
    return tuple(sorted(
        name for name, lifecycle in PERSISTENCE_TABLE_POLICY.items()
        if lifecycle.full_wipe == disposition
    ))
