# Domain folder and naming convention

**Status:** Current
**Scope:** Production source, verification code, documentation, and experiments
**Behavioral effect:** None; this record governs navigation and future refactoring.

## 1. Organizing principle

Backend and frontend remain separate deployables, but both use the same product-domain map:

1. Data Sourcing.
2. Asset Catalogue.
3. Test Lab.
4. Issue Management.
5. Root Cause Analysis (RCA).
6. Knowledge Base.
7. Analytics Artifact Repository (AAR).
8. Administration.
9. Shared platform services.

In this repository, **AAR** means **Analytics Artifact Repository**. Product diagnostics are
distinct from software tests: a diagnostic evaluates governed data-quality behavior, while a
unit, integration, contract, or end-to-end test verifies application software.

## 2. Diagnostic identifiers

Every diagnostic has a test-family code and a diagnostic number. The canonical navigation key is
`T<test>-D<two-digit diagnostic>`, for example `T2-D06`. Existing persisted identifiers and rule
IDs, including `diagnostic_id = 6` and `T2D6-01`, remain unchanged.

| Context | Convention | Example |
| --- | --- | --- |
| User-facing label | `Tn · Dnn — Name` | `T2 · D06 — Row-completeness reconciliation` |
| Documentation and non-Python folder | lowercase kebab-case | `t2-d06-row-completeness` |
| Python package | lowercase snake_case | `t2_d06_row_completeness` |
| React component | PascalCase | `RowCompletenessResults.jsx` |
| Python module | lowercase snake_case | `row_completeness_results.py` |
| Unit or contract test | `test_<subject>` | `test_manifest.py` |
| End-to-end test | `<workflow>.spec.js` | `row-completeness.spec.js` |

MVP status is catalog metadata and is not encoded into directory names. A diagnostic receives a
production folder only when implementation or current documentation exists; the catalog represents
unimplemented diagnostics without empty placeholder directories.

## 3. Diagnostic catalog

| Test | Test family | Diagnostic | Human-readable name | MVP |
| --- | --- | --- | --- | --- |
| T1 | Feature-to-target leakage | D01 | Post-outcome field look-ahead present | No |
| T1 | Feature-to-target leakage | D02 | Single-feature target separation | Yes |
| T1 | Feature-to-target leakage | D03 | Categorical leakage — association strength / purity | No |
| T2 | KB-driven data validation | D04 | Cross-field business rule | Yes |
| T2 | KB-driven data validation | D05 | Derivation identity | No |
| T2 | KB-driven data validation | D06 | Row-completeness reconciliation | Yes |
| T2 | KB-driven data validation | D07 | Disguised-missing / staleness | No |
| T2 | KB-driven data validation | D08 | Value-semantics classification | Yes |
| T2 | KB-driven data validation | D09 | Statistical outlier (robust-z) | No |
| T2 | KB-driven data validation | D10 | Boundary / pile-up detection | No |
| T2 | KB-driven data validation | D11 | Directional monotonic consistency | Yes |
| T3 | Target consistency | D12 | Label-consistency rule | Yes |
| T3 | Target consistency | D13 | Target-rate break (change-point) | No |
| T4 | Population representativeness | D14 | Population Stability Index (PSI) | Yes |
| T4 | Population representativeness | D15 | KS distance | No |
| T4 | Population representativeness | D16 | Segment coverage | No |
| T5 | Outcome window completeness | D17 | Resolution / maturity rate by vintage | Yes |
| T5 | Outcome window completeness | D18 | Resolved-only vs all differential | No |
| T5 | Outcome window completeness | D19 | Seasoning / maturity profile | No |
| T6 | KB relationship SME+AI | D20 | AI-proposal workflow | Yes |

The nine MVP entries are the product's governed Test Lab register. Their executable or
`workflow_pending` state remains runtime data and is not inferred from the folder structure.

## 4. Production boundary

Production code lives under `source-codes/`. Domain-specific engines, manifests, runners, knowledge,
presentation, and verification should converge on matching domain paths. Cross-domain facilities
such as AAR contracts, persistence, configuration, and authentication remain shared or platform
services with explicit owners.

Folder moves must not change public routes, serialized contracts, database identifiers, artifact
types, runtime configuration, or user-visible behavior. Compatibility exports may preserve old
Python imports during staged migrations.

T2-D06 Row Completeness is the first production pilot. Its backend implementation is under
`backend/domains/test_lab/diagnostics/t2_d06_row_completeness`, and its UI implementation is under
`ui/src/features/test-lab/diagnostics/t2-d06-row-completeness`. Former module paths remain
compatibility surfaces while consumers migrate.

T1-D02 Single-feature Target Separation is the second production pilot. Its backend diagnostic
package is `backend/domains/test_lab/diagnostics/t1_d02_feature_target_separation`, and its UI is
`ui/src/features/test-lab/diagnostics/t1-d02-feature-target-separation`. Information Value/binning
is intentionally placed in `backend/domains/test_lab/shared/binning` and
`ui/src/features/test-lab/shared/binning` because T4-D14 and RCA also consume that governed evidence.

T4-D14 Population Stability Index is the third production slice. Its backend package is
`backend/domains/test_lab/diagnostics/t4_d14_population_stability`, and its UI package is
`ui/src/features/test-lab/diagnostics/t4-d14-population-stability`. PSI-specific population,
workflow, manifest, and execution code remain in that diagnostic package; reusable binning and
bin-label presentation remain under the Test Lab shared binning packages.

T2-D04 Cross-field Business Rule is the fourth production slice. Its backend package is
`backend/domains/test_lab/diagnostics/t2_d04_cross_field_business_rule`, and its UI package is
`ui/src/features/test-lab/diagnostics/t2-d04-cross-field-business-rule`. Generic run-state and
append-only decision access are intentionally placed in `backend/domains/test_lab/shared/run_state.py`.
Former cross-field Python paths remain exact aliases; route-level UI files remain dispatchers.

T2-D11 Directional and Monotonic Consistency is the fifth production slice. Its backend package is
`backend/domains/test_lab/diagnostics/t2_d11_directional_monotonic_consistency`, and its UI package
is `ui/src/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency`. The diagnostic
owns its KB v0.3 resources, semantic/manual feature adjudication, empirical direction engine,
manifest, runner, and chart evidence. Generic run state, AAR persistence, and issue promotion stay
in their shared owners; route-level scope and result files remain diagnostic dispatchers.

RCA is a top-level business capability rather than a Test Lab diagnostic. Backend ownership is
under `backend/domains/rca`, and frontend ownership is under `ui/src/features/rca`. Taxonomy,
Knowledge Base, Issue Management, authentication, and shared Test Lab evidence remain outside RCA.

The Analysis Artifact Repository is under `backend/domains/aar` and `ui/src/features/aar`.
`analysis_runtime` retains shared analytical contracts, snapshot loading, target resolution, and
supporting-capability registration; AAR owns repository persistence, artifact types, lineage,
integrity, and governed Data Sourcing projections.

The existing `backend/assets` package remains the shared Data Sourcing and asset-lifecycle
capability. Upload, refresh, profiling, Test Lab, RCA, and AAR all consume that boundary, so it is
intentionally not nested under any one product domain. New domain-specific sourcing behavior stays
with its owning domain; only genuinely reusable asset identity, storage, and refresh behavior belongs
in `backend/assets`.

Ignored mutable local state lives under `backend/.runtime`; production deployments continue to use
explicit environment paths such as `SYSTEM_DB_PATH`. Historical verification scripts and retired
prototypes belong in versioned documentation or the workspace-root `experiments/archive`, not beside
the backend entry point.

## 5. Experiment boundary

Experiments live under the workspace-root `experiments/`, outside `source-codes/`:

- production code must never import an experiment;
- deployment packages must never include experiments;
- experiments may use public production contracts but cannot become runtime dependencies;
- source, documentation, small synthetic fixtures, and conclusions may be version controlled;
- credentials, private data, generated output, caches, and virtual environments are ignored;
- promotion means implementing accepted behavior in the production domain with production tests,
  rather than importing the experimental module.

`source-codes/.venv` remains the production/local-development environment because application and CI
scripts resolve that path. An experiment may execute with its interpreter when no packages are added.
Experiments requiring additional or conflicting dependencies use `experiments/.venv` or their own
environment and declare those dependencies separately.
