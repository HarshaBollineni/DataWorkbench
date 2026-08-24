# 0.4.0 Test disposition — all 14 shipped tests accounted for (FWK-13)

Governed by FWK-13 and D-17. **Delete means delete**: the code, the registry entry, the param spec
and the downstream records. Under D-17, "DEFER → #n" means: *the shipped test is deleted, and its
successor #n exists only as S8 documentation* — nothing is registered in the product for it.
Executed in Phase 3 (registry/records) — the 14 `dq_tests/stage1+stage2` shim modules were already
deleted in tranche 1 (30 Jul, verified unreachable; committed in Phase 2.1).

## Disposition table

| # | Shipped test (registry id) | Disposition | Where it goes | Rationale |
|---|---|---|---|---|
| 1 | PSI (Population Stability Index) | **KEEP** → #14 | Register row 14, `workflow_pending` (executable at backlog B2 with re-verification against `spike_recalibrate.py`) | Core, unchanged in intent |
| 2 | Leakage detection (single-feature AUC) | **KEEP** → #2 | Register row 2, `workflow_pending` | Core, unchanged in intent |
| 3 | Plausibility rule check | **ABSORB** into T2 hard | KB domain/bound rules (26 of S9's 49) executed by the cross-field engine (#4) | Univariate plausibility becomes KB rules — never code literals |
| 4 | Date-ordering / event-sequence check | **ABSORB** into T2 hard | KB date-ordering rules (3 of 49) executed by #4 | Same |
| 5 | Completeness / missing-rate profile | **REPLACE** → #6 | Register row 6, `workflow_pending`; missing-rate survives as **profiling input** only | Different computation: expected-vs-actual anti-join by segment × period, not per-column missing rate |
| 6 | Post-outcome field inventory | **DEFER** → #1 | S8 documentation only (D-17) | Phase-2 row |
| 7 | Maturity profile analysis | **DEFER** → #19 | S8 documentation only | Phase-2 row |
| 8 | MCAR test (Little's) | **DEFER** | S8 documentation / OOS-05 | Honest output is a caveated escalation; must run only after value-semantics tagging or it mislabels censoring as MNAR |
| 9 | Missingness-vs-target (MNAR) test | **DEFER** | Same | Same |
| 10 | Robust z-score outlier test (MAD) | **DEFER** → #9 | S8 documentation only | Phase-2 within T2 |
| 11 | Key uniqueness check | **DELETE as diagnostic, RETAIN as profiling precondition** | Grain-key uniqueness check inside profiling; consumed by #6's readiness when it ships (D-04) | No home in the new framework, but #6's anti-join needs a unique grain key |
| 12 | Tail analysis | **DELETE** | — | Nearest is #10 boundary pile-up (defer row); different computation |
| 13 | Correlation stability analysis | **DELETE** | — | Nearest is #11 (segmented sign check — different) and Plan-B feature drift |
| 14 | Drift decomposition | **DELETE** | — | Plan-B delivery-to-delivery drift; multi-upload only (D-11) |

**Net: keep 2 · absorb 3 · replace 1 · defer 5 · delete 3 + 1 demoted.**

## Record classes dropped in Phase 3 (C-18 — accepted per instruction)

Dropped where keyed to retired tests: `plan_v2` rows, `results_v2`, `scores_v2`, `issues_v2` (and
tracked issues / RCA cases keyed to them), the 14-test registry, param specs, `test_library` rows,
`fw_areas`/`fw_tests` content. **Preserved:** datasets (`dq_items`, `dq_item_files`,
`dq_item_tables`), `variable_inventory`, users/roles/sessions, taxonomy dimensions/values/aliases/
assignments, knowledge documents/versions/published rules. No compatibility layer, no dual-read, no
remap — the Galileo version is retained separately.

## Pre-drop counts (from Phase 0.6)

**The baseline working tree carries no populated system DB** — `backend/system_state.db` does not
exist at baseline (created and seeded at boot; gitignored). Actual pre-drop counts per table are
therefore recorded at Phase-3 execution time in the Phase-3 commit message, per plan 3.1, from the
DB as it exists then. On a boot-seeded instance the only rows at risk are platform seeds
(`fw_areas` 11, `fw_tests` 14, `test_library` content from `dq_framework_data.json`), which are
exactly what the replacement retires. No user data exists on this instance (start-gate report §3).
