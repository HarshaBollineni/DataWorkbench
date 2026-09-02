"""Production-owned T2-D11 directionality knowledge resources."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import system_db as db

from .matching import load_kb, load_terminology, prepare_feature_matcher

BACKEND_DIR = Path(__file__).resolve().parents[4]
KB_PATH = BACKEND_DIR / "knowledge_base" / "pd_directionality_kb_v0_3.yaml"
TERMINOLOGY_PATH = BACKEND_DIR / "knowledge_base" / "credit_risk_abbreviations_v0_2.yaml"
PROMPT_PATH = BACKEND_DIR / "ai" / "agents" / "semantic_feature_adjudication_v0_2.txt"

PROPOSAL_KIND = "t2_d11_expected_direction"
OPEN_PROPOSAL_STATES = {"draft", "pending_review"}
INELIGIBLE_PROPOSAL_DIRECTIONS = {"NOT_APPLICABLE", "EXCLUDED"}
SYSTEM_DOCUMENT_ID = "kbdoc_t2d11_directionality"
SYSTEM_VERSION_ID = "kbver_t2d11_directionality_v0_3"

_DIRECTION_LABELS = {
    "increasing": "Higher feature value → Higher risk",
    "decreasing": "Higher feature value → Lower risk",
    "non_monotonic": "Non-monotonic risk relationship",
    "no_clear_direction": "No clear expected risk direction",
    "not_applicable": "Ordered directionality is not applicable",
}


@lru_cache(maxsize=1)
def resources() -> tuple[dict[str, Any], dict[str, Any], Any]:
    kb = load_kb(KB_PATH)
    terminology = load_terminology(TERMINOLOGY_PATH)
    return kb, terminology, prepare_feature_matcher(kb, terminology)


def rule_index() -> dict[str, dict[str, Any]]:
    return {row["feature"]: row for row in resources()[0]["feature_rules"]}


def prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _display_name(value: str) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()


def render_kb_markdown(kb: dict[str, Any]) -> str:
    """Render the source-controlled directionality KB for human review."""
    metadata = kb["metadata"]
    lines = [
        f"# Test 2, Diagnostic 11 — Expected Risk Direction Knowledge Base v{metadata['version']}",
        "",
        metadata["purpose"],
        "",
        f"**Risk outcome:** {metadata['risk_outcome']}",
        f"**Scope:** {metadata['scope']}",
        f"**Source:** `{KB_PATH.name}` (approved, source-controlled system knowledge)",
        "",
        "This knowledge records economic expectations before empirical analysis. "
        "Observed results never rewrite these expectations automatically.",
        "",
        "## Interpretation guidance",
        "",
    ]
    lines.extend(
        f"- {note}" for note in kb.get("conventions", {}).get("interpretation_notes", [])
    )
    for rule in kb["feature_rules"]:
        feature = rule["feature"]
        representations = ", ".join(rule.get("representations") or []) or "None listed"
        inverse = ", ".join(rule.get("inverse_representations") or [])
        lines.extend([
            "",
            f"## {_display_name(feature)} (`{feature}`)",
            "",
            f"**Feature family:** {_display_name(rule['feature_family'])}",
            f"**Expected relationship:** {_DIRECTION_LABELS[rule['expected_direction']]}",
            f"**Knowledge strength:** {_display_name(rule['knowledge_strength'])}",
            "",
            rule["definition"],
            "",
            f"**Economic rationale:** {rule['rationale']}",
            "",
            f"**Common representations:** {representations}",
        ])
        if inverse:
            lines.append(f"**Inverse representations:** {inverse}")
        if rule.get("notes"):
            lines.append(f"**Application note:** {rule['notes']}")
    return "\n".join(lines).strip() + "\n"


def seed_document(tenant_id: str = "bootstrap") -> dict[str, Any]:
    """Expose KB v0.3 as an approved read-only document in the KB UI."""
    import kb as kb_service

    source_bytes = KB_PATH.read_bytes()
    document = load_kb(KB_PATH)
    version = str(document["metadata"]["version"])
    return kb_service.ensure_system_reference_document(
        tenant_id=tenant_id, document_id=SYSTEM_DOCUMENT_ID,
        version_id=SYSTEM_VERSION_ID, version_seq=3,
        title="Test 2, Diagnostic 11 — Expected Risk Direction Knowledge Base",
        source_filename=KB_PATH.name, source_media_type="application/x-yaml",
        source_bytes=source_bytes, converted_markdown=render_kb_markdown(document),
        metadata={"diagnostic_id": 11, "kb_version": version},
    )


def _normalized_identifier(value: str) -> str:
    # Keep punctuation significant: ``risk-score`` and ``risk_score`` can be
    # different physical columns. Unicode compatibility + case normalization
    # is sufficient for stable identity without collapsing distinct names.
    return unicodedata.normalize("NFKC", str(value or "").strip()).casefold()


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _proposal_scope(item_id: str, table: str, feature: str,
                    tenant_id: str, *, conn=None) -> dict[str, str]:
    item = db.query_one("dq_items", conn=conn, item_id=item_id) or {}
    asset_id = str(item.get("dataset_family_id") or item_id)
    identity = {
        "tenant_id": tenant_id,
        "proposal_kind": PROPOSAL_KIND,
        "asset_id": asset_id,
        "table": _normalized_identifier(table),
        "feature": _normalized_identifier(feature),
    }
    return {**identity, "proposal_subject_key": _stable_hash(identity)}


def _decision(*, canonical_feature: str | None, expected_direction: str,
              representation_orientation: str | None) -> tuple[dict[str, Any], str]:
    value = {
        "canonical_feature": canonical_feature,
        "expected_direction": expected_direction,
        "representation_orientation": representation_orientation,
    }
    return value, _stable_hash(value)


def _proposal_text(*, table: str, feature: str, canonical_feature: str | None,
                   expected_direction: str, representation_orientation: str | None,
                   rationale: str) -> str:
    return (
        f"Directionality concept: {canonical_feature or feature}\n"
        f"Source table: {table}\n"
        f"Source representation: {feature}\n"
        f"Representation relationship: {representation_orientation or 'not linked'}\n"
        f"Expected risk direction: {expected_direction}\n"
        f"Rationale: {rationale}"
    )


def _subject_rules(scope: dict[str, str], tenant_id: str, *,
                   conn=None) -> list[dict[str, Any]]:
    return db.query(
        "kb_rules", tenant_id=tenant_id, proposal_kind=PROPOSAL_KIND,
        proposal_subject_key=scope["proposal_subject_key"], order_by="updated_at DESC",
        conn=conn,
    )


def _record_evidence(*, rule_id: str, run_id: str, item_id: str,
                     canonical_feature: str | None, expected_direction: str,
                     representation_orientation: str | None, rationale: str,
                     decision_hash: str, evidence_state: str, actor: str,
                     conn=None) -> None:
    if db.query_one(
        "kb_rule_proposal_evidence", conn=conn,
        rule_id=rule_id, source_run_id=run_id,
    ):
        return
    try:
        db.insert("kb_rule_proposal_evidence", {
            "evidence_id": f"kbpe_{uuid.uuid4().hex[:16]}", "rule_id": rule_id,
            "source_run_id": run_id, "source_item_id": item_id,
            "canonical_feature": canonical_feature,
            "expected_direction": expected_direction,
            "representation_orientation": representation_orientation,
            "rationale": rationale, "decision_hash": decision_hash,
            "evidence_state": evidence_state, "actor": actor,
            "created_at": db.now_ist(),
        }, conn=conn)
    except sqlite3.IntegrityError:
        # A concurrent retry of the same frozen run already attached this
        # evidence. The unique (rule, run) key makes that retry idempotent.
        if not db.query_one(
            "kb_rule_proposal_evidence", conn=conn,
            rule_id=rule_id, source_run_id=run_id,
        ):
            raise


def _update_draft(*, rule: dict[str, Any], text: str, metadata: dict[str, Any],
                  decision_hash: str, feature: str, conn=None) -> dict[str, Any]:
    now = db.now_ist()
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    db.update("kb_rules", {"rule_id": rule["rule_id"]}, {
        "rule_hash": text_hash, "rule_text": text,
        "proposal_decision_hash": decision_hash,
        "proposal_metadata_json": metadata,
        "related_columns_json": [feature], "updated_at": now,
    }, conn=conn)
    db.update("kb_sections", {"section_id": rule["section_id"]}, {
        "body_markdown": text,
    }, conn=conn)
    db.update("kb_document_versions", {"version_id": rule["version_id"]}, {
        "original_size": len(text), "converted_markdown": text,
        "converted_markdown_sha256": text_hash,
        "conversion_report_json": {"source": "diagnostic_proposal",
                                   "proposal": metadata},
        "review_state": "pending_review", "reviewer": None, "reviewed_at": None,
    }, conn=conn)
    return db.query_one("kb_rules", conn=conn, rule_id=rule["rule_id"])


def create_draft_proposal(*, run_id: str, table: str, feature: str,
                          expected_direction: str, rationale: str,
                          actor: str, item_id: str,
                          canonical_feature: str | None = None,
                          representation_orientation: str | None = None,
                          tenant_id: str, conn=None) -> dict[str, Any]:
    """Materialize one governed proposal per stable D11 feature subject.

    The caller invokes this only when a complete diagnostic manifest freezes.
    Repeated runs attach evidence to the same open rule instead of creating
    parallel reviewer cards. Publishing or archiving remains reviewer-only.
    """
    import kb as kb_service

    if conn is None:
        with db.get_conn() as owned_conn:
            proposal = create_draft_proposal(
                run_id=run_id, table=table, feature=feature,
                expected_direction=expected_direction, rationale=rationale,
                actor=actor, item_id=item_id,
                canonical_feature=canonical_feature,
                representation_orientation=representation_orientation,
                tenant_id=tenant_id, conn=owned_conn,
            )
            owned_conn.commit()
            return proposal
    if expected_direction in INELIGIBLE_PROPOSAL_DIRECTIONS:
        raise ValueError(f"{expected_direction} is a run-scope decision, not reusable knowledge")
    scope = _proposal_scope(item_id, table, feature, tenant_id, conn=conn)
    decision, decision_hash = _decision(
        canonical_feature=canonical_feature, expected_direction=expected_direction,
        representation_orientation=representation_orientation,
    )
    metadata = {
        "diagnostic_id": 11, **scope, **decision,
        "feature": feature, "source_run_id": run_id, "source_item_id": item_id,
        "proposal_kind": PROPOSAL_KIND,
        "proposal_subject_key": scope["proposal_subject_key"],
        "proposal_decision_hash": decision_hash,
    }
    text = _proposal_text(
        table=table, feature=feature, canonical_feature=canonical_feature,
        expected_direction=expected_direction,
        representation_orientation=representation_orientation, rationale=rationale,
    )
    rules = _subject_rules(scope, tenant_id, conn=conn)
    open_rule = next((rule for rule in rules
                      if rule["lifecycle_state"] in OPEN_PROPOSAL_STATES), None)
    if open_rule:
        same_decision = open_rule.get("proposal_decision_hash") == decision_hash
        evidence_state = "supporting" if same_decision else "conflicting"
        if not same_decision and open_rule["lifecycle_state"] == "draft":
            open_rule = _update_draft(
                rule=open_rule, text=text, metadata=metadata,
                decision_hash=decision_hash, feature=feature, conn=conn,
            )
            for evidence in db.query(
                "kb_rule_proposal_evidence", conn=conn,
                rule_id=open_rule["rule_id"],
            ):
                db.update(
                    "kb_rule_proposal_evidence",
                    {"evidence_id": evidence["evidence_id"]},
                    {"evidence_state": (
                        "supporting" if evidence.get("decision_hash") == decision_hash
                        else "conflicting"
                    )}, conn=conn,
                )
            evidence_state = "supporting"
            action = "updated"
        else:
            action = "reused" if same_decision else "conflict_retained"
        _record_evidence(
            rule_id=open_rule["rule_id"], run_id=run_id, item_id=item_id,
            canonical_feature=canonical_feature, expected_direction=expected_direction,
            representation_orientation=representation_orientation, rationale=rationale,
            decision_hash=decision_hash, evidence_state=evidence_state, actor=actor,
            conn=conn,
        )
        return {**open_rule, "proposal_action": action}

    published = next((rule for rule in rules if rule["lifecycle_state"] == "published"), None)
    if published and published.get("proposal_decision_hash") == decision_hash:
        _record_evidence(
            rule_id=published["rule_id"], run_id=run_id, item_id=item_id,
            canonical_feature=canonical_feature, expected_direction=expected_direction,
            representation_orientation=representation_orientation, rationale=rationale,
            decision_hash=decision_hash, evidence_state="supporting", actor=actor,
            conn=conn,
        )
        return {**published, "proposal_action": "already_governed"}

    predecessor = published or next((rule for rule in rules
                                     if rule["lifecycle_state"] == "archived"), None)
    metadata["based_on_rule_id"] = predecessor["rule_id"] if predecessor else None
    try:
        proposal = kb_service.draft_rule_from_case_closure(
            tenant_id, "domain_fact", text, [table], run_id, actor,
            proposal_metadata=metadata, conn=conn,
        )
    except sqlite3.IntegrityError:
        # A concurrent freeze won the unique subject key. Re-enter the normal
        # open-rule policy so a differing decision is updated or retained as
        # conflict according to the winner's lifecycle state.
        if not any(
            rule["lifecycle_state"] in OPEN_PROPOSAL_STATES
            for rule in _subject_rules(scope, tenant_id, conn=conn)
        ):
            raise
        return create_draft_proposal(
            run_id=run_id, table=table, feature=feature,
            expected_direction=expected_direction, rationale=rationale,
            actor=actor, item_id=item_id,
            canonical_feature=canonical_feature,
            representation_orientation=representation_orientation,
            tenant_id=tenant_id, conn=conn,
        )
    db.update("kb_rules", {"rule_id": proposal["rule_id"]}, {
        "related_columns_json": [feature],
    }, conn=conn)
    _record_evidence(
        rule_id=proposal["rule_id"], run_id=run_id, item_id=item_id,
        canonical_feature=canonical_feature, expected_direction=expected_direction,
        representation_orientation=representation_orientation, rationale=rationale,
        decision_hash=decision_hash, evidence_state="supporting", actor=actor,
        conn=conn,
    )
    db.insert("transaction_log", {
        "ts": db.now_ist(), "actor": actor,
        "event": "diagnostic_knowledge_proposal_materialized",
        "payload": {"diagnostic_id": 11, "run_id": run_id,
                    "rule_id": proposal["rule_id"],
                    "proposal_subject_key": scope["proposal_subject_key"]},
    }, conn=conn)
    return {**(db.query_one(
        "kb_rules", conn=conn, rule_id=proposal["rule_id"],
    ) or proposal),
            "proposal_action": "created"}


__all__ = [
    "KB_PATH", "PROMPT_PATH", "SYSTEM_DOCUMENT_ID", "SYSTEM_VERSION_ID",
    "TERMINOLOGY_PATH", "create_draft_proposal", "prompt", "render_kb_markdown",
    "resources", "rule_index", "seed_document",
]
