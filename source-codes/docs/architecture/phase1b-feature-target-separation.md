# Phase 1B - Single-feature target separation

Diagnostic 2 is executable through the Test Lab Coverage -> Run -> Findings lifecycle. Its
current detailed binning contract and Mermaid workflow are maintained in
`backend/dq_diagnostics/engines/feature_target_separation/README.md`.

## Scope and execution

- The target is the confirmed Data Sourcing target and cannot be replaced in Test Lab.
- The manifest freezes the selected features, target interpretation including the effective
  binary positive/event class, missing-target handling, thresholds and optimizer constraints.
- AUC/Gini and supervised IV/WOE run per feature. A failed metric remains a partial feature
  outcome instead of erasing the whole run.
- Numeric fine bins are automatic unless the user explicitly supplies an advanced cut CSV.
  Those cuts become a new fine foundation, after which the normal optimizer creates coarse
  bins from the allowed fine edges.
- Categorical fine bins use the top 49 regular values by frequency plus one atomic `Other`
  bin when necessary. Missing and confirmed specials remain protected outside that cap.
- ROC, fine-bin, coarse-bin and IV payloads are immutable reusable AAR artifacts tied to their
  snapshot, target route, population and methodology identity.

## Review and governance

- The user reviews both the fine foundation and optimized coarse result, including counts,
  WOE/IV, missing/special handling, reconciliation and optimizer warnings.
- Diagnostic-specific revisions do not alter universal definitions. Eligible complete-snapshot
  definitions require explicit promotion before Diagnostic 2 or target-aware PSI can reuse
  them as the universal route.
- Leakage and poor-discrimination results are candidate findings, never automatic violations.
- An issue is created only after an analyst selects **Promote to Issue Management**. Promotion
  is idempotent for the source finding; dismissal requires a reason.
- Neither execution nor review mutates assessed source data.

## Verification

Focused backend tests cover deterministic top-49-plus-`Other` membership, atomic categorical
coarse optimization, strict numeric override parsing, new fine-foundation persistence and
coarse-boundary alignment. PSI regression tests cover application of governed categorical,
missing, special and unseen rules.
