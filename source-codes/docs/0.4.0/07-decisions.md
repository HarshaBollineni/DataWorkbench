# 0.4.0 Decision record (D-01…D-22)

A default silently assumed is a defect — every row is confirmed or explicitly adopted with its
date. Verbatim from requirements-0.4.0.md §11; execution-time decisions taken by the implementer
are appended in §2 with their rationale (plan §13 discipline).

| ID | Decision | Status |
|---|---|---|
| D-01 | The SME can edit a candidate at the gate — a third action alongside accept and reject. | Confirmed |
| D-02 | An SME edit does not consume one of the 3 attempts; recorded `authored_by: sme`. | Confirmed |
| D-03 | An accepted-but-non-executable candidate is parked as a build request with provenance, not discarded. | Confirmed |
| D-04 | Key uniqueness: deleted as a diagnostic, retained as a profiling precondition (#6's anti-join needs a unique grain key). | Adopted 29 Jul 2026 |
| D-05 | ~~The "material fields only" filter is dropped.~~ | **Superseded by D-18** (30 Jul evening) |
| D-06 | KB parse failures are surfaced to the uploader; the parse report is a first-class artifact. | Confirmed |
| D-07 | Shared datasets consume every user's allowance; admin can raise limits per user or all. | Confirmed |
| D-08 | Cross-field LLM role verification is off by default, opt-in per run, every mapping change recorded. | Adopted 29 Jul 2026 |
| D-09 | ~~Categorical leakage in core; core = 10.~~ | **Superseded by D-14** |
| D-10 | Closure-generated and loop-persisted KB objects are drafts requiring human approval, never auto-published. | Adopted 29 Jul 2026 |
| D-11 | Plan A only in 0.4.0; Plan B diagnostics deferred pending SME input; delivery/baseline data model lands now (PLT-02). | Confirmed 30 Jul 2026 |
| D-12 | Autonomous runs: conclusion approval required by default (waiving is explicit per-workspace); remediation approval never waivable. | Adopted 29 Jul 2026 |
| D-13 | Target version 0.4.0, breaking; framework/Test Lab/RCA changes called out in the release note. | Confirmed 30 Jul 2026 |
| D-14 | Categorical leakage returns to Phase-2 (S8 re-issue). Core = 9, deferred = 11, adapters = 8. Supersedes D-09. | Adopted 30 Jul 2026 |
| D-15 | First build slice: only #4 Cross-field business rule is executable; the other 8 registered workflow-pending (FWK-17/18). | Adopted 30 Jul 2026 — requester instruction |
| D-16 | The Test Lab module is replaced, not patched — wizard retires for the register-driven workflow (testlab-redesign-0.4.0.md). | Adopted 30 Jul 2026 — requester instruction; tranche-1 dead-code deletion executed 30 Jul |
| D-17 | The register holds only the 9 core diagnostics; the 11 S8 defer rows are documentation, not product scope. Supersedes the rev-2/3 "registered but deferred" reading. | Adopted 30 Jul 2026 — requester correction |
| D-18 | The material-fields filter is reinstated with the data-driven FWK-09 definition (KB-role-mapped ∨ declared feature/target ∨ dictionary-flagged); never a hardcoded list; exclusions reported. Supersedes D-05. | Adopted 30 Jul 2026 (evening) |
| D-19 | Admin factory reset restored (WSP-08): surgical reset + full wipe, admin-only, type-to-confirm, audited. | Adopted 30 Jul 2026 (evening) — requester instruction |
| D-20 | The data ingestion workflow is redesigned per §8b — Drop → Review → Ready. | Adopted 30 Jul 2026 (evening) — requester instruction |
| D-21 | The AI helper/tool layer is decluttered first (PLT-08), upfront before feature work. | Adopted 30 Jul 2026 (evening) — requester instruction |
| D-22 | Controlled scaling gate: the second diagnostic is defined and built only after the requester declares satisfaction with the cross-field flow; one-at-a-time enablement with explicit sign-off per diagnostic. Before **each** backlog pull, record the requester-satisfaction/D-22 decision and a bounded phase plan. | Adopted 30 Jul 2026 (evening) — requester instruction; forward control clarified 31 Jul |

## 2. Execution-time decisions (implementer, per plan §13)

| ID | Decision | Rationale | Date |
|---|---|---|---|
| DX-01 | **Historical baseline fact:** `spike_recalibrate.py`, `spike_gx.py`, and `spike_integration.py` were broken because `backend/database.py` had been deleted at 0.2.0 (`5ba1b13`). **Resolved 31 Jul 2026:** they now use deterministic, spike-only `backend/spike_fixtures.py` and exit 0. | The repaired spikes remain PSI parity evidence only; they neither define slice-1 behaviour nor enable PSI/B2. The original baseline finding is retained rather than rewritten. | 30 Jul 2026; resolved 31 Jul 2026 |
| DX-02 | Notes for backlog pulls, from the S1–S4 fidelity check: (i) S3 lists 8 cross-field primitives; CFR-06's nine match S5's actual code — S5 governs (verified: `ineq, dateorder, identity, dom_range, dom_set, presence, eq_cond, in_cond, present_number`). (ii) APL-11's "PSI (features/periods)" conflates S3's "PSI has features / completeness has segment × period" — when B6 is pulled, hand-validate the pack against PSI's *feature* shape. (iii) D-01/D-02 resolve questions S3 left open (S4 draws no edit branch) — they are decisions, not S3 restatements. (iv) RCA-36's deterministic Judge knowingly reverses S1 §11 "Agent 8 — Judge (LLM)". | Fidelity findings recorded so later readers do not "correct" the consolidation backwards. | 30 Jul 2026 |
| DX-04 | **S5's `ROLE_VOCAB` (a module-level dict of ~35 role names → hardcoded `entity`/`dtype`/`synonyms`, `cross_field_rule_engine.py:148-249`) does not port as-is — it is domain knowledge in code (KB-01), the same class of violation as `build_generic_kb()`'s 49 rules. Phase 6 must derive the role vocabulary entirely from the published KB and the ingested dictionary, never from a hardcoded table.** Resolution, binding on the Phase-6 implementer: (1) **role identity + entity** come from each in-scope rule's own `semantic_roles_json`/`entity` fields (extracted by Phase 5's parser from the document's Roles/Entity columns) — the KB *is* the vocabulary; a role used by only one rule's entity resolves to that entity, no cross-rule ambiguity-merging needed for slice 1. (2) **the "dictionary-description phrase match" tier (0.88)** reads the *ingested dataset's* dictionary descriptions (Phase 4's `ingest` records) — generic and per-dataset, never a hardcoded synonym list. (3) **the "synonym" tier (0.92)** covers normalized-form variants of the role's own name as recorded by the KB (e.g. a PDF extraction hazard's collapsed form vs. its human-confirmed recovery, KB-11) — not a curated business synonym table; if no such variant exists for a given role this tier simply never fires, which is correct, not a gap to fill with domain content. (4) **the dtype gate** is inferred structurally from how a role is used in its own rule(s) (e.g. `date_ordering` → date-like; an `inequality`/`domain` bound against a numeric literal → number-like; a `domain` set of two values → flag/category-like) at KB-binding time (KB-08, when the rule's predicate parameters are captured) — never a hardcoded per-role dtype table. **What DOES port verbatim (CFR-05's "exactly as implemented"):** the scoring *algorithm* — exact 1.00 / synonym 0.92 / dict-desc 0.88 / token-subset 0.80 / fuzzy 0.55–0.75 / partial-Jaccard 0.35–0.60 (an S5 tier not named in the S10 chart's 5-tier summary — verified present in the actual code at `cross_field_rule_engine.py:729-736`; port it too, the chart's list is illustrative, the code is authoritative per CFR-05) — plus the **0.70 unresolved threshold** (`resolve_roles`, line ~757: a best score below 0.70 is reported unresolved, not auto-accepted). A source-inspection test at Phase 6 (mirroring 5-T8) must assert no per-role synonym/entity/dtype literal survives in the ported engine. | Adopted 31 Jul 2026 — implementer decision during Phase-6 prep, per plan §13's "material ambiguity the documents do not resolve" | 31 Jul 2026 |
| DX-03 | Phase 0 record inventory: no populated system DB exists in the baseline tree; Phase-3 pre-drop counts are recorded at Phase-3 time from the boot-seeded DB. | `system_state.db` is boot-created and gitignored; see start-gate report §3. | 30 Jul 2026 |
| DX-05 | The three retained PSI parity spikes (`spike_recalibrate.py`, `spike_gx.py`, `spike_integration.py`) are ported: each uses deterministic spike-only `spike_fixtures.py` and exits 0. They are now archived under `experiments/archive/pre-domain-gx-parity/`. | They remain parity evidence only; the production T4-D14 implementation has its own domain tests. | 31 Jul 2026; archived 25 Aug 2026 |
| DX-06 | The requester explicitly authorized implementation and conditional enablement of Diagnostic #14, Population Stability Index, under D-22. Enablement remains conditional on the acceptance gates in `docs/diagnostics/psi/phase-plan.md`; implementation alone is not enablement. | This is the required one-at-a-time D-22 authorization. Diagnostic #14 remains `workflow_pending` until all regression, contract, API, UI, artifact, and documentation gates pass. | 19 Aug 2026 |
| DX-07 | Diagnostic #4, Cross-field Business Rule, is returned to `workflow_pending`. | The workflow requires additional testing and refinement; register gating must refuse new manifests and runs until it is explicitly re-enabled. | 2 Sep 2026 |

## 3. Forward controls for backlog pulls (R-08 / R-10)

- **R-08 — B1 candidate flags.** If B1 is explicitly pulled, rerun the negative
  candidate-flag rendering check before enabling it: a candidate flag must remain a
  review outcome and must never render as a verdict violation. The current proof is
  `ui/e2e/testlab-rendering.spec.js`.
- **R-10 — every backlog item.** Do not begin a backlog item merely because its
  predecessor is technically complete. First record requester satisfaction and the
  D-22 enablement decision, then approve a bounded phase plan naming the diagnostic,
  owner, acceptance tests, rollback consideration, and the next decision point.
