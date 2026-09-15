"""User-facing read model for the Knowledge Base Library.

The library deliberately lists governed knowledge bases, not every diagnostic.
Roadmap entries are returned separately and can never be mistaken for active,
executable knowledge.
"""
from __future__ import annotations

from typing import Any

import kb
import system_db as db
from dq_diagnostics import register as diagnostic_register


_KNOWLEDGE_BASES = (
    {
        "knowledge_base_id": "kbdoc_t2d6_row_completeness",
        "name": "Row Completeness",
        "purpose": "Defines the governed rules, defaults, and user guidance used to assess whether required rows are present.",
        "consumers": [{"id": "T2-D06", "name": "Row completeness"}],
        "consumption_stages": ["Setup", "Execution", "Results interpretation"],
        "content_label": "Rules and configuration",
        "management_mode": "library_managed",
    },
    {
        "knowledge_base_id": "kbdoc_t2d11_directionality",
        "name": "Expected Risk Direction",
        "purpose": "Records the expected economic direction of risk features before empirical monotonicity is assessed.",
        "consumers": [{"id": "T2-D11", "name": "Directional monotonic consistency"}],
        "consumption_stages": ["Setup", "Execution", "Results interpretation"],
        "content_label": "Feature expectations",
        "management_mode": "source_managed",
    },
    {
        "knowledge_base_id": "kbdoc_t2d08_value_semantics",
        "name": "Value Semantics",
        "purpose": "Defines semantic roles and classification rules used to interpret special values in context.",
        "consumers": [{"id": "T2-D08", "name": "Value semantics"}],
        "consumption_stages": ["Setup", "Execution", "Results interpretation"],
        "content_label": "Semantic roles and rules",
        "management_mode": "source_managed",
    },
    {
        "knowledge_base_id": "kbdoc_credit_risk_terminology",
        "name": "Credit Risk Terminology",
        "purpose": "Provides shared abbreviations and terminology used to resolve business meaning consistently.",
        "consumers": [
            {"id": "T2-D08", "name": "Value semantics"},
            {"id": "T2-D11", "name": "Directional monotonic consistency"},
        ],
        "consumption_stages": ["Setup"],
        "content_label": "Terms and representations",
        "management_mode": "source_managed",
    },
)

_IMPLEMENTED_DIAGNOSTICS = {6, 8, 11}

_EXPERIENCE = {
    "kbdoc_t2d6_row_completeness": {
        "objective": "Determine whether every expected entity-period row is present once, at the declared reporting grain.",
        "inputs": [
            {"name": "Profiled dataset snapshot", "source": "Data Sourcing", "requirement": "Required"},
            {"name": "Entity and temporal bindings", "source": "DSC or explicit local decision", "requirement": "Required"},
            {"name": "Segment binding", "source": "DSC or explicit local decision", "requirement": "Optional"},
            {"name": "Reporting grain and coverage floor", "source": "KB default or explicit local decision", "requirement": "Required"},
        ],
        "roles": [
            {"name": "facility_id", "meaning": "Entity whose expected reporting history is assessed", "requirement": "Required"},
            {"name": "period", "meaning": "Reporting-period axis", "requirement": "Required"},
            {"name": "segment", "meaning": "Optional population grouping", "requirement": "Optional"},
        ],
        "dsc_requirements": [
            {"facet": "Default entity binding", "use": "Proposes the facility identifier", "requirement": "Required or explicit local selection"},
            {"facet": "Default temporal binding", "use": "Proposes the reporting period", "requirement": "Required or explicit local selection"},
            {"facet": "Expected cadence", "use": "Proposes monthly, quarterly, semiannual, or annual grain", "requirement": "Advisory"},
            {"facet": "Row grain", "use": "Provides structural interpretation evidence", "requirement": "Optional"},
        ],
        "flow": ["Resolve DSC structure", "Freeze KB rules and local decisions", "Build expected entity-period grid", "Compare observed rows", "Produce findings and coverage metrics"],
        "example": {
            "title": "Expected-versus-observed rows",
            "inputs": ["100 facilities", "12 monthly periods", "95% required-row floor"],
            "calculation": "1,200 expected rows → 1,140 valid observed rows",
            "outcome": "95% coverage: passes the floor; duplicate and missing-period rules remain independently reported.",
        },
    },
    "kbdoc_t2d11_directionality": {
        "objective": "Compare observed feature-risk relationships with economic expectations recorded before analysis.",
        "inputs": [
            {"name": "Confirmed target and positive-class orientation", "source": "Data Sourcing / local confirmation", "requirement": "Required"},
            {"name": "Numeric analytical features", "source": "Profiled snapshot", "requirement": "Required"},
            {"name": "Expected risk directions", "source": "Expected Risk Direction KB", "requirement": "Required per selected feature"},
            {"name": "Optional segment", "source": "Profile and local decision", "requirement": "Optional"},
        ],
        "roles": [
            {"name": "reference", "meaning": "Confirmed risk outcome or target", "requirement": "Required"},
            {"name": "feature", "meaning": "Independent variable assessed against the target", "requirement": "One or more"},
            {"name": "segment", "meaning": "Optional population split for a subsequent analysis", "requirement": "Optional"},
        ],
        "dsc_requirements": [
            {"facet": "Default entity binding", "use": "Excludes entity identifiers from analytical features", "requirement": "Advisory"},
            {"facet": "Default temporal binding", "use": "Excludes time axes from analytical features and segments", "requirement": "Advisory"},
            {"facet": "Row grain", "use": "Records the structural basis of the analysis", "requirement": "Optional"},
        ],
        "flow": ["Resolve structural exclusions", "Match features to KB concepts", "Confirm expected directions", "Measure observed relationships", "Compare expectation with evidence"],
        "example": {
            "title": "Loan-to-value direction",
            "inputs": ["Feature: loan_to_value", "KB expectation: higher value → higher risk", "Observed bins: risk rises consistently"],
            "calculation": "Expected INCREASING ↔ observed INCREASING",
            "outcome": "Consistent. A contrary result becomes a finding; it never rewrites the KB automatically.",
        },
    },
    "kbdoc_t2d08_value_semantics": {
        "objective": "Classify special values using business meaning, row context, and declared observation windows.",
        "inputs": [
            {"name": "Profiled fields and confirmed special values", "source": "Data Sourcing", "requirement": "Required"},
            {"name": "Semantic-role bindings", "source": "KB match, DSC, or explicit local decision", "requirement": "Required by rule"},
            {"name": "Runtime business declarations", "source": "Explicit local decision", "requirement": "Required by rule"},
            {"name": "Entity and temporal structure", "source": "DSC", "requirement": "Required for entity/period rules"},
        ],
        "roles": [
            {"name": "entity_id", "meaning": "Entity used for longitudinal assessment", "requirement": "Conditional"},
            {"name": "period", "meaning": "Observation period used for windows and ordering", "requirement": "Conditional"},
            {"name": "segment", "meaning": "Population grouping for segment-level rules", "requirement": "Conditional"},
            {"name": "business semantic roles", "meaning": "Outcome, exposure, arrears, maturity, commitment, and other governed concepts", "requirement": "Rule-specific"},
        ],
        "dsc_requirements": [
            {"facet": "Default entity binding", "use": "Binds entity_id for longitudinal rules", "requirement": "Required where used"},
            {"facet": "Default temporal binding", "use": "Binds period for ordering and observation windows", "requirement": "Required where used"},
            {"facet": "Expected cadence", "use": "Interprets declared monitoring and update cycles", "requirement": "Advisory"},
            {"facet": "Row grain", "use": "Records the structural basis for grouped assessments", "requirement": "Optional"},
        ],
        "flow": ["Resolve structural roles", "Bind business semantic roles", "Check rule prerequisites", "Execute eligible rules", "Resolve competing tags by precedence"],
        "example": {
            "title": "Value classification",
            "inputs": ["Entity and period confirmed", "Exposure field bound", "Sentinel and observation-window declarations supplied"],
            "calculation": "Eligible rules assess each cell and its entity/period context",
            "outcome": "CENSORED, STALE_FROZEN, or NOT_APPLICABLE; missing evidence remains UNCLASSIFIED rather than being guessed.",
        },
    },
    "kbdoc_credit_risk_terminology": {
        "objective": "Resolve abbreviations and representations consistently before diagnostic-specific concepts are matched.",
        "inputs": [{"name": "Field names, business names, and descriptions", "source": "Profiled snapshot", "requirement": "Required"}],
        "roles": [{"name": "term", "meaning": "Canonical credit-risk concept and its accepted representations", "requirement": "As available"}],
        "dsc_requirements": [{"facet": "DSC structural roles", "use": "Separates structural meaning from business terminology", "requirement": "Consumer-specific"}],
        "flow": ["Read field metadata", "Normalize abbreviations", "Offer canonical representations", "Pass bounded candidates to the consuming diagnostic"],
        "example": {"title": "Shared terminology", "inputs": ["Column: ltv_ratio"], "calculation": "LTV → loan-to-value", "outcome": "D08 and D11 can match the same representation consistently."},
    },
}


def _document_summary(tenant_id: str, definition: dict[str, Any]) -> dict[str, Any] | None:
    document = db.query_one("kb_documents", document_id=definition["knowledge_base_id"])
    if not document or document.get("tenant_id") != tenant_id:
        return None
    versions = db.query(
        "kb_document_versions", document_id=document["document_id"],
        order_by="version_seq DESC",
    )
    active_package = None
    if definition["knowledge_base_id"] == "kbdoc_t2d6_row_completeness":
        active_package = db.query_one(
            "diagnostic_kb_packages", tenant_id=tenant_id,
            diagnostic_id=6, lifecycle_state="active",
        )
    active = next(
        (version for version in versions if version["version_id"] == (active_package or {}).get("version_id")),
        next((version for version in versions
              if (version.get("conversion_report_json") or {}).get("lifecycle") == "active"),
             versions[0] if versions else None),
    )
    drafts = [version for version in versions if version.get("review_state") == "draft"]
    report = (active or {}).get("conversion_report_json") or {}
    version_label = report.get("kb_version") or report.get("terminology_version")
    if active_package:
        version_label = str(active_package["version_seq"])
    if not version_label and active:
        version_label = str(active.get("version_seq"))
    return {
        **definition,
        "document_title": document.get("title"),
        "active_version": version_label,
        "active_version_id": (active or {}).get("version_id"),
        "version_count": len(versions),
        "draft_count": len(drafts),
        "lifecycle_state": "active" if active else "working_draft",
        "updated_at": (active or {}).get("created_at") or document.get("created_at"),
    }


def _upcoming_enhancements() -> list[dict[str, Any]]:
    diagnostics = []
    for row in diagnostic_register.list_register():
        diagnostic_id = int(row["diagnostic_id"])
        if diagnostic_id in _IMPLEMENTED_DIAGNOSTICS:
            continue
        diagnostics.append({
            "id": f"{row.get('area')}-D{diagnostic_id:02d}" if row.get("area") else f"D{diagnostic_id:02d}",
            "name": row.get("name") or f"Diagnostic {diagnostic_id}",
        })
    return [
        {
            "enhancement_id": "additional-diagnostic-consumers",
            "name": "Additional diagnostic knowledge",
            "status": "work_in_progress",
            "description": "A KB card will appear only when reusable governed knowledge has been created and connected to a diagnostic.",
            "items": diagnostics,
        },
        {
            "enhancement_id": "rca-reusable-knowledge",
            "name": "Root Cause Analysis reusable knowledge",
            "status": "work_in_progress",
            "description": "Confirmed reusable learning will enter Proposed Changes first; a KB card will appear only for a governed collection with named consumers.",
            "items": [],
        },
        {
            "enhancement_id": "role-based-access-control",
            "name": "Role-Based Access Control (RBAC)",
            "status": "future_enhancement",
            "description": "Role-specific authoring and approval controls are planned after the MVP. Actor and action history is retained now.",
            "items": [],
        },
    ]


def get_library(tenant_id: str) -> dict[str, Any]:
    knowledge_bases = [
        summary for definition in _KNOWLEDGE_BASES
        if (summary := _document_summary(tenant_id, definition)) is not None
    ]
    proposed_change_count = len(kb.list_learning_candidates(tenant_id))
    return {
        "knowledge_bases": knowledge_bases,
        "upcoming_enhancements": _upcoming_enhancements(),
        "summary": {
            "knowledge_base_count": len(knowledge_bases),
            "active_count": sum(item["lifecycle_state"] == "active" for item in knowledge_bases),
            "working_draft_count": sum(item["draft_count"] for item in knowledge_bases),
            "proposed_change_count": proposed_change_count,
        },
    }


def _version_id_for_label(document_id: str, label: Any) -> str | None:
    for version in db.query("kb_document_versions", document_id=document_id):
        report = version.get("conversion_report_json") or {}
        current = report.get("kb_version") or report.get("terminology_version") or version.get("version_seq")
        if str(current) == str(label):
            return version["version_id"]
    return None


def _declared_dsc_requirements(knowledge_base_id: str,
                               tenant_id: str) -> list[dict[str, Any]] | None:
    execution_context = None
    if knowledge_base_id == "kbdoc_t2d6_row_completeness":
        active = db.query_one(
            "diagnostic_kb_packages", tenant_id=tenant_id,
            diagnostic_id=6, lifecycle_state="active",
        )
        execution_context = ((active or {}).get("package_json") or {}).get("execution_context")
    elif knowledge_base_id == "kbdoc_t2d08_value_semantics":
        from domains.test_lab.diagnostics.t2_d08_value_semantics import knowledge
        execution_context = knowledge.resources()[0].get("execution_context")
    elif knowledge_base_id == "kbdoc_t2d11_directionality":
        from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import knowledge
        execution_context = knowledge.resources()[0].get("execution_context")
    contract = (execution_context or {}).get("dataset_structure_context")
    if not isinstance(contract, dict):
        return None
    labels = {
        "table.structure/default_entity_binding": "Default entity binding",
        "table.temporal/default_temporal_binding": "Default temporal binding",
        "table.temporal/expected_cadence": "Expected cadence",
        "table.structure/row_grain": "Row grain",
    }
    return [
        {
            "facet": labels.get(row.get("predicate"), row.get("predicate")),
            "use": f"Provides {str(row.get('maps_to') or 'diagnostic context').replace('.', ' â†’ ')}",
            "requirement": str(row.get("requirement") or "advisory").capitalize(),
            "accepted_resolution_states": row.get("accepted_resolution_states") or ["confirmed"],
        }
        for row in contract.get("selectors") or []
    ]


def _legacy_references(manifest: dict[str, Any], diagnostic_id: int) -> list[dict[str, Any]]:
    if diagnostic_id == 6 and isinstance(manifest.get("kb"), dict):
        value = manifest["kb"]
        if not value.get("document_id") or not value.get("version_id"):
            return []
        return [{"knowledge_base_id": value.get("document_id"),
                 "version_id": value.get("version_id"), "version_label": None}]
    knowledge = manifest.get("knowledge") or {}
    if diagnostic_id == 8:
        references = []
        value_version = knowledge.get("value_semantics_version")
        if value_version:
            references.append({
                "knowledge_base_id": knowledge.get("value_semantics_document_id") or "kbdoc_t2d08_value_semantics",
                "version_id": _version_id_for_label("kbdoc_t2d08_value_semantics", value_version),
                "version_label": value_version,
            })
        terminology_version = knowledge.get("terminology_version")
        if terminology_version:
            references.append({
                "knowledge_base_id": knowledge.get("terminology_document_id") or "kbdoc_credit_risk_terminology",
                "version_id": _version_id_for_label("kbdoc_credit_risk_terminology", terminology_version),
                "version_label": terminology_version,
            })
        return references
    if diagnostic_id == 11:
        references = []
        directionality_version = knowledge.get("version")
        if directionality_version:
            references.append({
                "knowledge_base_id": "kbdoc_t2d11_directionality",
                "version_id": _version_id_for_label("kbdoc_t2d11_directionality", directionality_version),
                "version_label": directionality_version,
            })
        terminology_version = knowledge.get("terminology_version")
        if terminology_version:
            references.append({
                "knowledge_base_id": "kbdoc_credit_risk_terminology",
                "version_id": _version_id_for_label("kbdoc_credit_risk_terminology", terminology_version),
                "version_label": terminology_version,
            })
        return references
    return []


def _usage_history(tenant_id: str, knowledge_base_id: str) -> list[dict[str, Any]]:
    history = []
    for run in db.query("diag_runs", order_by="created_at DESC"):
        manifest = run.get("manifest_json") or {}
        run_tenant = manifest.get("tenant_id")
        if not run_tenant:
            item = db.query_one("dq_items", item_id=run.get("item_id"))
            run_tenant = (item or {}).get("sourcing_tenant_id") or "bootstrap"
        if str(run_tenant) != tenant_id:
            continue
        references = manifest.get("knowledge_references") or _legacy_references(
            manifest, int(run.get("diagnostic_id") or 0),
        )
        reference = next(
            (value for value in references
             if value.get("knowledge_base_id") == knowledge_base_id),
            None,
        )
        if not reference:
            continue
        dsc = manifest.get("dataset_structure_context") or {}
        history.append({
            "run_id": run["run_id"], "item_id": run.get("item_id"),
            "item_name": manifest.get("item_name") or run.get("item_id"),
            "diagnostic_id": run.get("diagnostic_id"),
            "consumer_id": reference.get("consumer_id") or f"diagnostic:{run.get('diagnostic_id')}",
            "version_id": reference.get("version_id"),
            "version_label": reference.get("version_label"),
            "reference_type": "pinned" if manifest.get("knowledge_references") else "legacy_version_match",
            "status": run.get("status"), "created_at": run.get("created_at"),
            "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
            "dsc_state": dsc.get("state") or "not_recorded",
            "dsc_context_version": dsc.get("context_version"),
            "dsc_context_ref": dsc.get("context_ref"),
        })
        if len(history) == 50:
            break
    return history


def _structured_rules(knowledge_base_id: str, tenant_id: str) -> list[dict[str, Any]]:
    if knowledge_base_id == "kbdoc_t2d6_row_completeness":
        active = db.query_one("diagnostic_kb_packages", tenant_id=tenant_id,
                              diagnostic_id=6, lifecycle_state="active")
        return [{
            "id": rule["rule_id"], "name": rule["title"],
            "description": rule.get("user_help"), "primitive": rule.get("primitive"),
            "required_roles": rule.get("required_roles") or [],
            "execution_state": "Executable when required roles are bound",
        } for rule in ((active or {}).get("package_json") or {}).get("rules") or []]
    if knowledge_base_id == "kbdoc_t2d08_value_semantics":
        from domains.test_lab.diagnostics.t2_d08_value_semantics import knowledge
        return [{
            "id": rule["rule"], "name": rule.get("tag_assigned") or rule["rule"],
            "description": rule.get("definition") or rule.get("description"),
            "primitive": "value_semantics_rule",
            "required_roles": sorted({
                role for entry in rule.get("entries") or []
                for role in [entry.get("target_role"), *(entry.get("indicator_roles") or [])]
                if role
            }),
            "prerequisites": sorted({
                value for entry in rule.get("entries") or []
                for value in [*(entry.get("required_declarations") or []),
                              *(entry.get("indicator_declarations") or [])]
            }),
            "entry_count": len(rule.get("entries") or []),
            "execution_state": "Generated per eligible target role when all prerequisites are present",
        } for rule in knowledge.resources()[0]["rules"]]
    if knowledge_base_id == "kbdoc_t2d11_directionality":
        from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import knowledge
        return [{
            "id": rule["rule_id"], "name": rule["feature"].replace("_", " ").title(),
            "concept_key": rule["feature"],
            "description": rule.get("rationale") or rule.get("definition"),
            "primitive": "expected_direction_comparison",
            "required_roles": ["reference", "feature"],
            "expected_direction": rule.get("expected_direction"),
            "execution_state": "Generated when a selected field is matched and its expectation is confirmed",
        } for rule in knowledge.resources()[0]["feature_rules"]]
    if knowledge_base_id == "kbdoc_credit_risk_terminology":
        from domains.test_lab.diagnostics.t2_d08_value_semantics import knowledge
        terminology = knowledge.resources()[1]
        return [{
            "id": section, "name": section.replace("_", " ").title(),
            "description": f"{len(values)} governed terms and representations",
            "primitive": "terminology_resolution", "required_roles": [],
            "execution_state": "Supplies bounded candidates to consuming diagnostics",
        } for section, values in terminology.items()
            if section not in {"metadata", "parsing_guidance"} and isinstance(values, dict)]
    return []


def get_knowledge_base(tenant_id: str, knowledge_base_id: str) -> dict[str, Any]:
    definition = next(
        (item for item in _KNOWLEDGE_BASES if item["knowledge_base_id"] == knowledge_base_id),
        None,
    )
    summary = _document_summary(tenant_id, definition) if definition else None
    if not summary:
        raise KeyError("Unknown knowledge base")
    document = kb.get_document(tenant_id, knowledge_base_id)
    versions = []
    for version in document["versions"]:
        report = version.get("conversion_report_json") or {}
        versions.append({
            "version_id": version["version_id"],
            "version_seq": version["version_seq"],
            "version_label": report.get("kb_version") or report.get("terminology_version") or str(version["version_seq"]),
            "lifecycle_state": report.get("lifecycle") or version.get("review_state"),
            "review_state": version.get("review_state"),
            "original_filename": version.get("original_filename"),
            "created_by": version.get("created_by"),
            "created_at": version.get("created_at"),
        })
    experience = dict(_EXPERIENCE[knowledge_base_id])
    declared_dsc = _declared_dsc_requirements(knowledge_base_id, tenant_id)
    if declared_dsc is not None:
        experience["dsc_requirements"] = declared_dsc
    return {
        **summary, **experience, "versions": versions,
        "rules": _structured_rules(knowledge_base_id, tenant_id),
        "usage_history": _usage_history(tenant_id, knowledge_base_id),
    }
