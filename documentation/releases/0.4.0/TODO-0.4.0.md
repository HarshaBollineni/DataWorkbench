# Archimedes release checklist

> **Archived 19 Aug 2026.** This completed checklist was moved from
> `source-codes/TODO.md`. Its unqualified paths describe the original 0.4.0
> workspace layout; use this directory's release records for current links.

## 0.4.0 (landed on `dev`) — slice 1

Scope contract: `docs/0.4.0/` (frozen 30 Jul 2026). Requirements authority:
`requirements-0.4.0.md` rev 5; build order: `archimedes-0.4.0-plan.md` rev 4 (8 phases).

**Two breaking changes, called out for the release note (D-13):**

1. **The DQ framework is replaced, not extended.** The shipped 11-area / 14-test framework
   retires in favour of the S8 register: 6 L1 themes → 11 L2 areas → 6 test areas → **9 core
   diagnostics** (D-17), of which exactly one — #4 Cross-field business rule — is executable in
   slice 1 (D-15); the other 8 are registered `workflow_pending` (FWK-17/18). Old test records
   (plans, results, scores, issues keyed to retired tests) are dropped with counts recorded (C-18).
2. **The Test Lab module is replaced, not patched** (D-16). The four-step plan/snippet/approve
   wizard retires for the register-driven Coverage → Scope → Run → Findings workflow
   (`testlab-redesign-0.4.0.md`). No user-editable code on any run path.

**Slice-1 scope (everything else is backlog, pulled one at a time under D-22):**

- [x] P1 Contracts frozen in `docs/0.4.0/`.
- [x] P2 Helper-layer declutter (PLT-08, D-21); admin factory reset (WSP-08, D-19); dead-code sweep.
- [x] P3 9-diagnostic register as data; threshold semantic layer; staged-execution skeleton;
      delivery/baseline seam (PLT-02); `verify_plan8.py` rebuilt against the new register.
- [x] P4 Data ingestion redesign: Drop → Review → Ready (ING, D-20), including same-family replacement as a new delivery.
- [x] P5 Knowledge Base: upload → tag → table-aware parse → playback → parse report → binding
      (KB; S9 round-trips to 49 governed rules).
- [x] P6 Cross-field engine as a service + rebuilt Test Lab (CFR; wizard deleted at cutover).
- [x] P7 Slice-1 acceptance and release documentation. Local commits only — no push, no deploy.

Backlog (§10 of the plan; each item needs a recorded requester-satisfaction/D-22 decision and a
bounded phase plan before work starts): B1 value-semantics
(#8) · B2 remaining core diagnostics · B3 report commentary (seam b) · B4 workspaces/quotas ·
B5 RCA corrections · B6 AI Proposal Loop (#20).

## Carried forward from 0.2.x/0.3.0

- [x] Remove the remaining unmounted legacy source modules and obsolete reset helpers —
      tranche 1 executed 30 Jul 2026 (28 files, redesign §4.1); remainder lands in P2 sweep with
      reachability evidence per file.
- [ ] Make the deployment wrapper use a unique temporary staging directory and avoid the Azure CLI
      Windows log-rendering limitation. (Deployment is out of scope for 0.4.0 — OOS-11.)

## 0.2.x/0.3.0 history (done)

- [x] Git workflow: `dev` from `main`, protection hook, release tags.
- [x] Product independence: demo DB removal, legacy route retirement, durable uploads with
      local per-item cache rebuild (Azure Files fix, 0.2.1).
- [x] Local quality gates: project-local Playwright/Chromium, `ci-local.ps1`.
- [x] 0.3.0: RCA v10 stages 1–7 (governed taxonomy, KB, case workflow, security, migration).
