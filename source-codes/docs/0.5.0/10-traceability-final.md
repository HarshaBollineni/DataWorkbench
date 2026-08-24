# 0.5.0 final traceability disposition

This index closes the requirements document without silently dropping an ID.
`Implemented` means code and a behavioural/structural check exist in this
worktree; `Adopted` records a plan decision taken without requester
confirmation; `Deferred` or `Out of scope` records an explicit boundary and
reason. The detailed per-step acceptance tables in
`archimedes-0.5.0-plan.md` remain the evidence index.

## Requirement families

| IDs | Final disposition |
|---|---|
| RET-01, RET-02, RET-03, RET-04, RET-05, RET-06 | Implemented: retirement census, fixed-point reachability gate, named keep/wire outcomes, and before/after verification. |
| ADM-01, ADM-02, ADM-03, ADM-04, ADM-05, ADM-06, ADM-07 | Implemented by the existing admin/reset/version-history surfaces; ADM-06/07 remain the durable asset/reset audit boundary. A-Q06 is adopted for the bounded history scope. |
| TAX-01 | Implemented: CRE is Product-only; the Portfolio dimension remains unchanged. A-Q05 is adopted. |
| AST-01, AST-02, AST-03, AST-04, AST-05, AST-06, AST-07, AST-08, AST-09, AST-10, AST-11, AST-12, AST-13, AST-14, AST-15 | Implemented: asset identity, alias, version/snapshot model, immutable basis, ordering, active selection, retained superseded sets, schema history, migration seam, and snapshot-keyed ingestion records. |
| AST-16, AST-17, AST-19 | Implemented: shared schema comparator, reordered evidence, three-tier metadata diff, fingerprint join, fixed latest-snapshot basis, no-data-read/O(columns) tests, and one shared `VersionDiff` mounted in picker, restore, and catalogue. |
| AST-18 | Deferred with reason: bound-only `row_level` refusal; no row digest was captured and row-level diff is OOS-13/default-view scope. |
| AST-20, AST-21 | Implemented/adopted: dictionary is independently versioned on the asset track, snapshots retain the binding, and human dictionary-state labels are shown. A-Q02 is adopted. |
| AST-22 | Implemented: one immutable `period` or `none` basis per asset. |
| UPL-01, UPL-02, UPL-03, UPL-04, UPL-05, UPL-06, UPL-07, UPL-08, UPL-09, UPL-10, UPL-11, UPL-12, UPL-13, UPL-14, UPL-15, UPL-16, UPL-17, UPL-18, UPL-19, UPL-20, UPL-21, UPL-22, UPL-23, UPL-24, UPL-25, UPL-26, UPL-27, UPL-28, UPL-29 | Implemented: the seven-step flow, file/dictionary handling, profiling, schema and period warnings, confirmations, version intent, completion summary, active/superseded rules, and Database per-table contract. |
| SRC-01, SRC-02, SRC-03, SRC-04, SRC-05, SRC-06, SRC-07, SRC-08, SRC-09, SRC-10, SRC-11, SRC-12, SRC-13 | Implemented: asset catalogue/picker read models, lifecycle labels, selection rules, and re-upload recovery. |
| CTX-01, CTX-02, CTX-03, CTX-04, CTX-05, CTX-06 | Implemented: context defaults, staleness, refresh request capture, asset-level target/use-case ownership, and intent-conditional carry-forward/clear behaviour. |
| ANL-01, ANL-02, ANL-03, ANL-04, ANL-05, ANL-06 | Implemented: five typed ID families including deterministic variables, append-only usage events, closed vocabulary, pure log measures, and routed catalogue. |
| ANL-07 | Implemented as capture/measure capability only; no presentation surface is shipped, per the plan's capture-only decision and OOS-12 boundary. |

## Adopted plan answers and explicit boundaries

| Decision/boundary | Disposition |
|---|---|
| A-Q01 | Adopted without requester confirmation: schema and shape tiers are MUST; distribution is SHOULD but captured during profiling and rendered when available. |
| A-Q02 | Adopted without requester confirmation: dictionary versions are independent and asset-bound. |
| A-Q03 | Adopted without requester confirmation: one Database workbook is one snapshot with per-table detail. |
| A-Q04 | Adopted without requester confirmation: only asset, version, snapshot, variable, and dictionary-version IDs ship; issue/run IDs cannot be minted. |
| A-Q05 | Adopted without requester confirmation: CRE is Product-only. |
| A-Q06 | Adopted without requester confirmation: admin history covers assets and resets until a separate user/taxonomy audit design exists. |
| C-38 through C-49 | Implemented through the replacement asset model, upload flow, lifecycle labels, and context/refresh seams; each ID is exercised by the corresponding step tests. |
| D-16, D-20, D-23, D-24, D-25, D-26, D-27, D-28, D-29, D-30, D-31 | Implemented or explicitly bounded by the model decisions: migration/reset epoch, asset/snapshot seam, capture-only analytics, retained rollback, and immutable basis. |
| FNC-01, FNC-02, FNC-03, FNC-04 | Implemented by the functional upload, replacement, restore, and downstream-refresh flows. |
| Q-01, Q-02, Q-03, Q-04, Q-05, Q-06 | Adopted as A-Q01..A-Q06 above, without requester confirmation. |
| OOS-11 | Out of scope: no unrequested object-family ID expansion beyond the five A-Q04 families. |
| OOS-12 | Out of scope: no analytics presentation surface, chart, export, or dashboard. Measures remain queryable from `usage_events`. |
| OOS-13 | Out of scope/deferred: no row-level diff; bound-only refusal is shipped. |
| OOS-14 | Out of scope: no new module-specific control-plane expansion. |
| OOS-15 | Out of scope: no normal deletion/cleanup of superseded snapshots; unbounded retention is accepted. |
| OOS-16 | Out of scope: no upload-time period splitting; period is metadata and snapshot granularity remains one file. |
| OOS-17, OOS-18 | Out of scope: no unrelated redesign or deployment/tagging/push work in this developer pass. |

All requirement IDs listed in the requirements document—RET, ADM, TAX, AST,
UPL, SRC, CTX, ANL, C/D/FNC/Q/OOS—therefore have an implemented, adopted,
deferred-with-reason, or out-of-scope disposition.
