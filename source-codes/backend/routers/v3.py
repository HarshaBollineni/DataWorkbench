"""RCA Stage 1/2 — taxonomy + Knowledge Base API.
See docs/rca/00-contracts.md §6, §7, §13.

Additive: mounted alongside v2, never replaces it. Every mutating call
requires a valid session (routers.auth.current_user via tenancy.resolve_
principal) — a stricter bar than v2's currently-unauthenticated handlers,
adopted here per the migration plan's "critical controls delivered earlier
when their features first appear" guidance rather than deferred to Stage 6.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

import kb
import rca
import system_db as s
import taxonomy
import tenancy
from kb_convert import ConversionError

router = APIRouter(prefix="/api/v3", tags=["rca"])


def _principal(authorization: str | None) -> dict:
    return tenancy.resolve_principal(authorization)


class TagAssignIn(BaseModel):
    value_keys: list[str]


class TagRemoveIn(BaseModel):
    reason: str


@router.get("/taxonomy/dimensions")
def list_dimensions(authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return taxonomy.list_dimensions(p["tenant_id"])


@router.get("/items/{item_id}/tags")
def get_item_tags(item_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return taxonomy.get_tags(p["tenant_id"], "dq_item", item_id)


@router.post("/items/{item_id}/tags")
def assign_item_tags(item_id: str, body: TagAssignIn,
                     authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        created = taxonomy.assign_tags(p["tenant_id"], "dq_item", item_id,
                                       body.value_keys, p["username"], origin="user_added")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"created": len(created), "tags": taxonomy.get_tags(p["tenant_id"], "dq_item", item_id)}


@router.delete("/items/{item_id}/tags/{assignment_id}")
def remove_item_tag(item_id: str, assignment_id: str, body: TagRemoveIn,
                    authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        taxonomy.remove_tag(p["tenant_id"], assignment_id, body.reason, p["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"tags": taxonomy.get_tags(p["tenant_id"], "dq_item", item_id)}


@router.get("/tests/{row_id}/tags")
def get_test_tags(row_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return taxonomy.get_tags(p["tenant_id"], "plan_v2", row_id)


@router.get("/results/{result_id}/tags")
def get_result_tags(result_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return taxonomy.get_tags(p["tenant_id"], "results_v2", result_id)


@router.get("/issues/{issue_row_id}/tags")
def get_issue_tags(issue_row_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return taxonomy.get_tags(p["tenant_id"], "issues_v2", issue_row_id)


# --- Knowledge Base (Stage 2) -------------------------------------------------

class PublishIn(BaseModel):
    category: str
    related_tables: list[str] = []
    related_columns: list[str] = []
    trust_level: str = "human_confirmed"
    shelf_life_months: int | None = None
    owner: str | None = None


class ArchiveIn(BaseModel):
    reason: str


class SubmitForReviewIn(BaseModel):
    category: str | None = None


class DiagnosticPackageActivationIn(BaseModel):
    reason: str


class ReusableKnowledgeProposalIn(BaseModel):
    reusable_lesson: str
    applicability_scope: str
    generalization_reason: str
    supporting_evidence_ids: list[str]
    related_tables: list[str] = []
    related_diagnostic_id: int | None = None


def _require_kb_editor(p: dict) -> None:
    if "kb_editor" not in p["authz_roles"]:
        raise HTTPException(status_code=403, detail="Requires the 'kb_editor' role.")


def _require_kb_reviewer(p: dict) -> None:
    if "kb_reviewer" not in p["authz_roles"]:
        raise HTTPException(status_code=403, detail="Requires the 'kb_reviewer' role.")


@router.get("/knowledge/diagnostic-packages/6")
def list_row_completeness_packages(authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    from dq_diagnostics import row_completeness_knowledge as knowledge
    return knowledge.list_package_versions(p["tenant_id"])


@router.get("/knowledge/diagnostic-packages/6/template")
def download_row_completeness_template(authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    from dq_diagnostics import row_completeness_knowledge as knowledge
    payload = json.dumps(knowledge.editable_template(p["tenant_id"]), indent=2,
                         ensure_ascii=False).encode("utf-8")
    return Response(content=payload, media_type="application/json", headers={
        "Content-Disposition": "attachment; filename=row-completeness-rules-editable.json",
    })


@router.post("/knowledge/diagnostic-packages/6/upload")
async def upload_row_completeness_package(file: UploadFile = File(...),
                                          authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_editor(p)
    from dq_diagnostics import row_completeness_knowledge as knowledge
    try:
        return knowledge.upload_package_draft(
            await file.read(), file.filename or "row-completeness-rules.json",
            p["username"], p["tenant_id"])
    except knowledge.RowCompletenessKnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/knowledge/diagnostic-packages/6/drafts")
def create_row_completeness_package_draft(body: dict,
                                          authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_editor(p)
    from dq_diagnostics import row_completeness_knowledge as knowledge
    try:
        return knowledge.upload_package_draft(
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            "row-completeness-live-editor.json", p["username"], p["tenant_id"])
    except knowledge.RowCompletenessKnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/knowledge/diagnostic-packages/6/{version_id}/activate")
def activate_row_completeness_package(version_id: str, body: DiagnosticPackageActivationIn,
                                      authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_reviewer(p)
    from dq_diagnostics import row_completeness_knowledge as knowledge
    try:
        return knowledge.activate_package(
            version_id, body.reason, p["username"], p["tenant_id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except knowledge.RowCompletenessKnowledgeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/knowledge/documents")
def list_kb_documents(authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return kb.list_documents(p["tenant_id"])


@router.get("/knowledge/learning-candidates")
def list_kb_learning_candidates(authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return kb.list_learning_candidates(p["tenant_id"])


# --- KB-02 — taxonomy dimension tags on a document (C-35). Same generic
# taxonomy.py used for dq_item tags, wired to object_type="kb_document"; no
# change needed in taxonomy.py itself (it already supports any object_type).
@router.get("/knowledge/documents/{document_id}/tags")
def get_kb_document_tags(document_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    return taxonomy.get_tags(p["tenant_id"], "kb_document", document_id)


@router.post("/knowledge/documents/{document_id}/tags")
def assign_kb_document_tags(document_id: str, body: TagAssignIn,
                            authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        created = taxonomy.assign_tags(p["tenant_id"], "kb_document", document_id,
                                       body.value_keys, p["username"], origin="user_added")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"created": len(created), "tags": taxonomy.get_tags(p["tenant_id"], "kb_document", document_id)}


@router.delete("/knowledge/documents/{document_id}/tags/{assignment_id}")
def remove_kb_document_tag(document_id: str, assignment_id: str, body: TagRemoveIn,
                           authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        taxonomy.remove_tag(p["tenant_id"], assignment_id, body.reason, p["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"tags": taxonomy.get_tags(p["tenant_id"], "kb_document", document_id)}


@router.post("/knowledge/documents")
async def upload_kb_document(category_hint: str | None = Form(default=None),
                             file: UploadFile = File(...),
                             authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_editor(p)
    try:
        return kb.upload_document(p["tenant_id"], file.filename or "upload.bin",
                                  file.content_type or "", await file.read(),
                                  category_hint, p["username"])
    except ConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge/documents/{document_id}")
def get_kb_document(document_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return kb.get_document(p["tenant_id"], document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge/documents/{document_id}/versions")
async def upload_kb_version(document_id: str, file: UploadFile = File(...),
                            authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_editor(p)
    try:
        return kb.upload_new_version(p["tenant_id"], document_id, file.filename or "upload.bin",
                                     file.content_type or "", await file.read(), p["username"])
    except ConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/knowledge/versions/{version_id}/preview")
def preview_kb_version(version_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return kb.get_version_preview(p["tenant_id"], version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge/versions/{version_id}/submit-for-review")
def submit_kb_version(version_id: str, body: SubmitForReviewIn,
                      authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_editor(p)
    try:
        return kb.submit_for_review(p["tenant_id"], version_id, p["username"], body.category)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except kb.KbError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/knowledge/versions/{version_id}/playback")
def get_kb_playback(version_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return kb.playback_summary(p["tenant_id"], version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/knowledge/versions/{version_id}/parse-report")
def get_kb_parse_report(version_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return kb.parse_report(p["tenant_id"], version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/knowledge/rules")
def list_kb_rules(lifecycle_state: str | None = None, category: str | None = None,
                  binding_status: str | None = None,
                  authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    filters = {"tenant_id": p["tenant_id"]}
    if lifecycle_state:
        filters["lifecycle_state"] = lifecycle_state
    if category:
        filters["category"] = category
    if binding_status:
        filters["binding_status"] = binding_status
    candidate_versions = kb.learning_candidate_version_ids(p["tenant_id"])
    return [r for r in s.query("kb_rules", order_by="updated_at DESC", **filters)
            if not r.get("is_synthetic")
            and not (r.get("version_id") in candidate_versions
                     and r.get("lifecycle_state") != "published")]


@router.post("/knowledge/rules/{rule_id}/publish")
def publish_kb_rule(rule_id: str, body: PublishIn, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return kb.publish_rule(p["tenant_id"], rule_id, p["username"], p["authz_roles"],
                               body.category, body.related_tables, body.related_columns,
                               body.trust_level, body.shelf_life_months, body.owner)
    except kb.ForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, kb.KbError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/knowledge/rules/{rule_id}/archive")
def archive_kb_rule(rule_id: str, body: ArchiveIn, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return kb.archive_rule(p["tenant_id"], rule_id, p["username"], p["authz_roles"], body.reason)
    except kb.ForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge/retrieve")
def retrieve_kb_rules(agent: str, categories: str | None = None, require_bound: bool = False,
                      authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    cats = categories.split(",") if categories else None
    return kb.list_eligible_rules(p["tenant_id"], agent, categories=cats, require_bound=require_bound)


# --- RCA (Stage 3 vertical slice) -----------------------------------------
# Every step is a distinct HITL-visible action (no auto-advance) so the
# staged UI can show the case moving through the state machine one
# deterministic step at a time, matching the workflow's evidence-earned
# design (docs/rca/00-contracts.md §3).

def _rca_error_map(exc: Exception) -> HTTPException:
    """Stage 6 shared error sanitizer: every RCA route funnels its exception
    through here rather than letting FastAPI's default handler decide.
    Known, hand-authored exception types get their real (short, non-
    leaking) message and a precise status code; anything else is logged
    server-side with its real type/message for operators, but the client
    only ever sees a generic detail — never a raw exception string, stack
    trace, or file path (a raw sqlite3.Error or AttributeError message can
    otherwise mention table/column internals)."""
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, rca.TransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (rca.RcaError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    print(f"[rca] unhandled {type(exc).__name__} in a v3 route: {exc}", flush=True)
    return HTTPException(status_code=500, detail="Internal error — please retry or contact support.")


@router.post("/issues/{issue_row_id}/rca/case")
def create_rca_case(issue_row_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        case = rca.create_case_from_issue(issue_row_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case["case_id"], p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.get("/rca/cases/{case_id}")
def get_rca_case(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


class ConclusionApprovalIn(BaseModel):
    conclusion_type: str
    root_cause: str = ""
    confidence: str = "Moderate"
    supporting_evidence_ids: list[str]
    limiting_evidence: str = ""
    alternatives_considered: str
    affected_scope: str = ""
    related_failures: str = ""
    owner: str = ""
    approval_rationale: str


class ReturnToInvestigationIn(BaseModel):
    reason: str


@router.post("/rca/cases/{case_id}/conclusion/approve")
def approve_rca_conclusion(case_id: str, body: ConclusionApprovalIn,
                           authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return rca.approve_conclusion(
            case_id, p["username"], body.model_dump(), tenant_id=p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/knowledge-proposal")
def propose_rca_reusable_knowledge(case_id: str, body: ReusableKnowledgeProposalIn,
                                   authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    _require_kb_editor(p)
    try:
        return rca.propose_reusable_knowledge(
            case_id, p["username"], body.model_dump(), tenant_id=p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/conclusion/return")
def return_rca_to_investigation(case_id: str, body: ReturnToInvestigationIn,
                                authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        return rca.return_to_investigation(
            case_id, p["username"], body.reason, tenant_id=p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/opening-look")
def run_rca_opening_look(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.run_opening_look(case_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/planner-look")
def run_rca_planner_look(case_id: str, kill_target_suspect_id: str | None = None,
                             authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        proposed = rca.planner_propose_look(case_id, p["username"], tenant_id=p["tenant_id"],
                                                kill_target_suspect_id=kill_target_suspect_id)
        if proposed.get("dead_end"):
            rca.handle_dead_end(case_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/looks/{look_id}/run")
def run_rca_look(look_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        result = rca.runner_execute(look_id, p["username"], tenant_id=p["tenant_id"])
        rca.reader_interpret(result["execution_id"], p["username"], tenant_id=p["tenant_id"])
        look = s.query_one("rca_looks", look_id=look_id)
        return rca.get_case(look["case_id"], p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/compose")
def compose_rca_hypothesis(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.compose_hypothesis(case_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/checks/{check_id}/run")
def run_rca_confirmation_check(check_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.run_confirmation_check(check_id, p["username"], tenant_id=p["tenant_id"])
        check = s.query_one("rca_confirmation_checks", check_id=check_id)
        hyp = s.query_one("rca_hypotheses", hypothesis_id=check["hypothesis_id"])
        return rca.get_case(hyp["case_id"], p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/hypotheses/{hypothesis_id}/propose-fix")
def propose_rca_fix(hypothesis_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        proposal = rca.propose_fix(hypothesis_id, p["username"], tenant_id=p["tenant_id"])
        hyp = s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id)
        return {"proposal": proposal, "case": rca.get_case(hyp["case_id"], p["tenant_id"])}
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/fix-proposals/{proposal_id}/approve")
def approve_rca_fix(proposal_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        approval = rca.approve_fix(proposal_id, p["username"], tenant_id=p["tenant_id"])
        proposal = s.query_one("rca_fix_proposals", id=proposal_id)
        hyp = s.query_one("rca_hypotheses", hypothesis_id=proposal["hypothesis_id"])
        return {"approval": approval, "case": rca.get_case(hyp["case_id"], p["tenant_id"])}
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/fix-approvals/{approval_id}/confirm-applied")
def confirm_rca_fix_applied(approval_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.confirm_fix_applied(approval_id, p["username"], tenant_id=p["tenant_id"])
        approval = s.query_one("rca_fix_approvals", id=approval_id)
        proposal = s.query_one("rca_fix_proposals", id=approval["fix_proposal_id"])
        hyp = s.query_one("rca_hypotheses", hypothesis_id=proposal["hypothesis_id"])
        return rca.get_case(hyp["case_id"], p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/close")
def close_rca_case(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        result = rca.close_case(case_id, p["username"], tenant_id=p["tenant_id"])
        return {"closed": result["closed"], "outcome": result.get("outcome"),
               "rerun_result": result.get("rerun_result"), "case": rca.get_case(case_id, p["tenant_id"])}
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


class ReviveIn(BaseModel):
    reason: str


@router.post("/rca/suspects/{suspect_id}/revive")
def revive_rca_suspect(suspect_id: str, body: ReviveIn, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        suspect = rca.revive_suspect(suspect_id, body.reason, p["username"], tenant_id=p["tenant_id"])
        case = s.query_one("rca_suspects", suspect_id=suspect_id)
        return {"suspect": suspect, "case": rca.get_case(case["case_id"], p["tenant_id"])}
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/coverage-challenge/pass1")
def run_rca_coverage_pass1(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.coverage_challenge_pass1(case_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/coverage-challenge/pass2")
def run_rca_coverage_pass2(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.coverage_challenge_pass2(case_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/reopened-kill-attempt")
def run_rca_reopened_kill_attempt(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        rca.run_reopened_kill_attempt(case_id, p["username"], tenant_id=p["tenant_id"])
        return rca.get_case(case_id, p["tenant_id"])
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc


@router.post("/rca/cases/{case_id}/second-chance")
def start_rca_second_chance(case_id: str, authorization: str | None = Header(default=None)):
    p = _principal(authorization)
    try:
        result = rca.start_second_chance(case_id, p["username"], tenant_id=p["tenant_id"])
        return {"second_chance_granted": result["second_chance_granted"], "outcome": result.get("outcome"),
               "case": rca.get_case(case_id, p["tenant_id"])}
    except Exception as exc:  # noqa: BLE001
        raise _rca_error_map(exc) from exc
