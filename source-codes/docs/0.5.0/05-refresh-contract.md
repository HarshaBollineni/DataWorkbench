# 0.5.0 refresh contract

This is the contract for consequences of changing an asset's active snapshot
set. It is intentionally split into exactly two classes. The first class is
deterministic and may be recomputed. The second class contains a human
decision or a run outcome and must only become stale at read time; it is never
silently recomputed.

The table names below were checked against `backend/system_db.py`'s `_SCHEMA`
and live `rca_%` discovery helper. `dictionary_state` is not a table in this
repository: it is the `dq_items.dictionary_state` column, so it is listed as
a column-level derived value. Likewise, `dq_snapshot_fingerprints` is a real
table, while read-model summaries are projections rather than stored tables.

## RECOMPUTE

These values are deterministic functions of the current active snapshots and
their stored table caches. `backend.assets.refresh.refresh_derived` is the
single implementation used both automatically after asset-set changes and by
the manual Refresh endpoint.

| Stored table / projection | Derived contents | Refresh rule |
|---|---|---|
| `variable_inventory` | Classification, role, profile JSON, samples, null/cardinality statistics, and confirmed type observations per active snapshot column | Re-read the snapshot's local stored table cache and replace the rows for that snapshot; never read or rewrite the original upload file |
| `dq_item_mappings` | Dictionary-to-column mapping, confidence tier, score, status, and dtype per active snapshot table | Recompute from the persisted mapping declarations and current cached columns; replace only the snapshot's rows |
| `dq_item_warnings` | Structured mapping, type, null, mixed-type, parse, and unsupported-value warnings | Recompute for the current cached columns; replace only the snapshot's rows |
| `dq_snapshot_fingerprints` | Per-column distribution and shape fingerprints | Recompute from cached values and upsert deterministically; rows for active snapshots not present in the cache are not fabricated |
| `dq_items.dictionary_state` | `absent`, `thin`, or `yes` state for each active snapshot | Re-derive from the recomputed dictionary coverage |
| `dq_items.ingest_status` | Derived ingest status for each active snapshot | Keep an active snapshot `ready` only when its stored cache and deterministic derived records are available; otherwise preserve the truthful non-ready state |
| Read-model summaries | Asset active snapshot count, current version summary, table/column counts, last upload, selectable status, inventory summaries, and diagnostic coverage summaries computed from the rows above | Recomputed by the existing read models on their next read; no summary cache is independently maintained |

No row in the MARK STALE class is written by this operation. In particular,
Refresh does not rerun diagnostics, recreate issues, change dispositions, or
rewrite RCA evidence.

## MARK STALE

These are decision-bearing or run-outcome artefacts. Their staleness is a
read-time derivation through `backend.assets.staleness`: an artefact is stale
when any bound `item_id`/snapshot reference resolves to a `dq_items` row whose
`snapshot_status` is `superseded`. A stale read model reports the source
snapshot label and superseded date and offers Recompute. No stored stale flag
is added, and no table in this list is recomputed by Refresh.

| Actual table | Why it is MARK STALE | Binding used by the read-time helper |
|---|---|---|
| `diag_runs` | Frozen diagnostic run outcome and manifest | `item_id` |
| `diag_run_decisions` | Scope-gate human decisions | `run_id` -> `diag_runs.item_id` |
| `diag_results` | Diagnostic outcome and verdict | `run_id` -> `diag_runs.item_id` |
| `diag_findings` | Finding evidence and review outcome | `run_id` -> `diag_runs.item_id` |
| `diag_dispositions` | SME disposition decision | `target_id` -> finding/result/run binding |
| `plan_v2` | Approved test-plan decisions | `item_id` |
| `results_v2` | Test execution results | `item_id` |
| `scores_v2` | Scored run outcome | `item_id` |
| `issues_v2` | Opened issue derived from a finding, with workflow state | `item_id` |
| `tracked_issues_v2` | Human issue-tracking record | `issue_row_id` -> `issues_v2.item_id` |
| `rca_cases` | RCA case state and human workflow | `issue_row_id` -> `issues_v2.item_id` |
| `rca_state_transitions` | RCA state decisions | `case_id` -> `rca_cases` |
| `rca_failure_groups` | Human-curated RCA failure grouping | `case_id` -> `rca_cases` |
| `rca_attached_failures` | RCA evidence attachments | `case_id` -> `rca_cases` |
| `rca_case_files` | RCA intake and evidence snapshot | `case_id` -> `rca_cases` |
| `rca_looks` | RCA investigation plan | `case_id` -> `rca_cases` |
| `rca_look_executions` | RCA look execution outcome | `look_id` -> `rca_looks` |
| `rca_suspects` | RCA suspect decision state | `case_id` -> `rca_cases` |
| `rca_suspect_history` | Suspect decision history | `suspect_id` -> `rca_suspects` |
| `rca_human_questions` | RCA human questions and answers | `case_id` -> `rca_cases` |
| `rca_coverage_passes` | RCA evidence-coverage outcome | `case_id` -> `rca_cases` |
| `rca_hypotheses` | RCA hypothesis decisions | `case_id` -> `rca_cases` |
| `rca_confirmation_checks` | RCA confirmation decision | `hypothesis_id` -> `rca_hypotheses` |
| `rca_judge_decisions` | RCA judge decision | `check_id` -> `rca_confirmation_checks` |
| `rca_symptom_accounting` | RCA symptom accounting outcome | `case_id` -> `rca_cases` |
| `rca_fix_proposals` | RCA remediation proposal | `hypothesis_id` -> `rca_hypotheses` |
| `rca_fix_approvals` | Human remediation approval | `proposal_id` -> `rca_fix_proposals` |
| `rca_closures` | RCA closure decision | `case_id` -> `rca_cases` |
| `rca_audit_events` | RCA audit history | `case_id`/object binding -> RCA case graph |
| `tag_assignments` | Taxonomy assignment and removal decisions | `object_type`/`object_id` -> bound work product |

The plan's compact `rca_*` notation therefore expands to the concrete tables
above. There is no `dictionary_state` table and no new `bound_snapshot_id`
column: the existing snapshot identity seam is sufficient for read-time
derivation.
