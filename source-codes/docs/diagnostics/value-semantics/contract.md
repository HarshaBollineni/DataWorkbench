# Diagnostic 8 — Value Semantics production contract

**Status:** Production / executable  
**Framework location:** Test 2, Diagnostic 8  
**Source experiment:** `experiments/test-lab/t2_d08_Value_semantics`

## Decision and scope

D08 classifies cell states that need value-semantic treatment before ordinary missingness or static-value conclusions are drawn. It never edits values and does not certify overall quality.

Version 1 runs against one immutable table snapshot and a user-selected set of fields. It supports General/unspecified use without a declared model purpose. PD, LGD and EAD are optional, multi-select context labels used for explanation only; they do not filter applicable rules.

The only output tags are `CENSORED`, `STALE_FROZEN` and `NOT_APPLICABLE`. `NO_TAG`, `UNCLASSIFIED` and `UNSCOPED` are reported coverage outcomes.

## Preconditions and user decisions

Launch requires:

- acknowledgement of the information page;
- a valid immutable snapshot and table;
- at least one selected field;
- a confirmed semantic role on at least one selected field that maps to a KB target role;
- valid visible runtime declarations;
- no unresolved selected role candidate.

Unique exact deterministic matches may be accepted with their evidence. Non-exact and AI candidates require human confirmation. Missing optional related roles/declarations do not block the whole run; affected entries appear as `UNSCOPED` in the preview and result.

Field applicability is separate from execution scope and cell classification. `field_scope` deselection
means `excluded`, not Not applicable. `field_applicability` with `value=not_applicable` requires a
nonblank reason of at most 2,000 characters. It clears confirmed roles, excludes the field, and records
the actor, timestamp and available AI evidence. `value=review` reopens it without including it.
Scope inclusion cannot bypass that decision; a new explicit role confirmation supersedes it.
All transitions are audited. No-match AI output never sets applicability automatically.

## Frozen identity

The canonical manifest freezes tenant, item, snapshot, table, selected fields, role bindings and provenance, declarations, optional context, inference audit, Value Semantics KB v0.2, terminology v0.3 and engine/methodology versions. Run ID and mutable timestamps are excluded from the semantic fingerprint.

## Rule execution

Every confirmed target role fans out to every applicable KB entry. Each entry declares its related-role and runtime-declaration prerequisites. Ready entries execute at their declared grain. Missing prerequisites are explicit and never converted to a pass.

The engine produces claims, then applies the KB precedence graph. Different tags with unresolved precedence fail closed. Source values are never altered. Persisted cell evidence excludes raw input values and uses stable masked row references.

Structured panel exclusions require a row predicate; unstructured declarations remain `UNSCOPED`. Review/update frequency declarations control the relevant observation window. Sentinel and variance evidence are independently reportable when only one path is runnable.

## AI contract

No AI call occurs by default. A user may request bounded semantic-role adjudication before freeze, including one sequential batch action for every unresolved eligible field. Only dictionary metadata and a bounded role catalog are sent. Structured output is schema- and catalog-validated, with at most three requested catalog-expansion passes. Role proposals, no-match, ambiguity, insufficient-context and retryable failure outcomes are displayed per field. A proposed role cannot be applied without a separate confirmation. AI cannot execute rules, assign tags, calculate metrics/verdicts, create findings/issues or launch RCA.

Ask AI is exposed per field only when `review_required=true`. Deterministic exact, human-confirmed,
and confirmed Not applicable decisions are ineligible at both UI and API boundaries. A user must
first reopen the decision, which clears the binding, deselects the field and restores Confirmation
required status. Batch review uses the same eligibility boundary. Setup navigation and other field
operations are disabled while either AI action is in progress.

For a rerun, successful inference is keyed by tenant, immutable snapshot/item, table, column metadata,
active Value Semantics and terminology versions, prompt version/hash, and structured-contract version.
When the identity matches, the draft copies the validated advisory output and stores `reused_from`
with source run/event IDs and the reuse fingerprint. A skipped, non-invoked inference event records
that lineage; `llm_call_count` remains zero and `reused_inference_count` increases. The provider is
not called again, even through a direct repeat request. Failed, malformed, cross-tenant, changed-field,
changed-KB, changed-prompt, or changed-contract results cannot be reused. An unreviewed new field must
be explicitly submitted through its per-field or pending-batch action. Reuse never carries human
confirmation into the new run; the user confirms the suggestion or Not applicable decision again.

## Outputs

The compact result includes executive action, tag rollup, field/rule coverage, unclassified/unscoped outcomes, artifact references and safe inference disclosure. The UI exposes authenticated PDF and text report downloads.

The AAR persists:

- `value_semantics_bindings` JSON;
- `value_semantics_tags` Parquet;
- `value_semantics_assessment_ledger` Parquet;
- `value_semantics_report` JSON.

All artifacts are immutable, hash-verified, versioned and linked to the frozen snapshot/manifest. Large row/cell evidence is not embedded in the diagnostic result payload.

New and refreshed drafts use additive `field_decisions_version=1`. The compact result, bindings
artifact and structured report include `field_scope_decisions` for every profiled field: included,
excluded or not_applicable, with confirmed roles, reason, actor/time and bounded AI evidence.
Bindings/report artifacts index every reviewed field and fingerprint these decisions. The results
page and PDF/text downloads distinguish this human field decision from rule-generated cell tags.
Unselected fields never enter the cell metrics or findings. Historical completed artifacts are
unchanged. A draft with no selected target field remains a draft; it cannot generate a cell-run report.

## Findings and RCA

Stale/frozen groups and populated not-applicable groups may be candidate findings. Unclassified/unscoped coverage normally prompts configuration or SME review. No issue is automatically created. A human disposition with rationale controls issue creation/linkage; confirmed issues enter the shared RCA workflow with diagnostic/run/result/rule/artifact traceability.

## KB publication

The KB section exposes Value Semantics v0.2 as active with v0.1 retained, and shared credit-risk terminology v0.3 as active with v0.2 retained. Historical runs keep their frozen version references.

## Security and failure behavior

Draft and run actions and diagnostic report retrieval are tenant-bound and authenticated. Frozen manifests are immutable. Binary artifact retrieval uses the product's existing shared AAR access boundary. Raw row values, secrets and unrestricted AI inputs/outputs are excluded from D08 reports/artifacts.

A failed runner produces no successful result. Open drafts can be resumed or discarded. Register rollback prevents new launches without deleting historical manifests, results, reports or artifacts.

## Acceptance criteria

- mandatory About step is visible and reopenable;
- General context launches without PD/LGD/EAD confirmation;
- exact match and human confirmation boundaries are enforced;
- batch AI review preserves per-field progress and displays every validated outcome, including no-match;
- per-field and batch AI controls are limited to Confirmation required fields;
- confirmed deterministic, manual and Not applicable decisions require an explicit reopen before AI;
- same-snapshot reruns reuse matching validated AI inference with source lineage and zero new calls;
- changed or newly included fields require an explicit new AI request and failed responses are not reused;
- Not applicable requires a human reason, supports reopening, and is distinct from unchecked Include;
- all field scope decisions survive freeze into results, AAR bindings/report JSON and PDF/text reports;
- runnable and `UNSCOPED` rule coverage are visible before freeze;
- all three tags and no-tag/unclassified/unscoped outcomes are tested;
- precedence and invalid predicate handling fail closed;
- four AAR artifacts persist with verified hashes/media types and Parquet downloads;
- PDF/text reports are authenticated and reproducible;
- KB versions/history are visible;
- findings require human disposition before issue/RCA;
- D08 API, tenancy, UI dispatch/build and existing diagnostic regression gates pass;
- register enablement occurs only after all blocking gates pass.
