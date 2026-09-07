# Data Workbench presentation validation

**Generated:** 07 September 2026. **Basis:** repository working tree, application baseline 0.5.2 (including pre-existing uncommitted changes).

## Deliverables

- [Editable PowerPoint](DataWorkbench_Product_Overview_and_Diagnostics.pptx) — **87 slides**, 16:9.
- [PDF preview](DataWorkbench_Product_Overview_and_Diagnostics.pdf) — native PowerPoint export; the PowerPoint is the editable master.
- [Slide-by-slide traceability CSV](DataWorkbench_Presentation_Traceability.csv) — titles, sources, notes and qualifications.
- This validation report.

**Editability:** all slide content uses native PowerPoint text and shapes, including card layouts, table cells, line icons and arrows. No flattened slide images, screenshot slides or embedded object dependencies. Tables are editable shapes/text rather than raster images. Thirteen native PowerPoint sections support navigation and reorganizing the deck.

## Sources reviewed

All four specified files exist and were read successfully. Both workbooks were opened with openpyxl; both decks were inspected as Office Open XML (OOXML) packages, including text, shapes, geometry, themes, layouts and available notes.

| Primary source | Assessment |
| --- | --- |
| DQ Framework_Analytics and Modeling.xlsx | Framework_Overview (A1:B8); Detailed_DQ_Framework (A1:K12); Model_Family_Mapping (A1:J25); Dev_Plan (A1:E22); Test Plan (A1:N24). No native workbook tables, charts or embedded images found. |
| MVP_Test Plan_DA.xlsx | Plan A - single upload (A1:J7); Plan B - multi upload (A1:K7); Diagnostics detail (A1:N50); Framework coverage map (A1:G18); Decisions & rationale (A1:A23). No native workbook tables, charts or embedded images found. |
| AI and Accelerators_Risk.pptx | 4 slides; 52 layouts; theme, font/color, shape and note XML reviewed. |
| DataQuality_Framework.pptx | 5 slides; 11 layouts; theme, font/color, shape and note XML reviewed. |

Repository review covered recursive README/documentation discovery; current user/technical guides; six diagnostic packages and their manifest/runner/engine contracts; current register/readiness/dispatch; issue/RCA UI and service; admin/authentication/KB roles; AAR schemas and persistence; sourcing/profiling; supporting missingness; relevant unit/integration test evidence; retired-test disposition; and experimental-to-production provenance.

**Source access limitation:** the two supplied decks could not be opened by this environment’s headless PowerPoint API, including from temporary copies. Their content and visual structure were accessible in OOXML and reviewed. No specified source was wholly inaccessible. The final generated deck opened and reopened normally in PowerPoint. Original source presentations were not modified; byte hashes match the assessment copies.

## Diagnostic inventory and implementation status

Classification is based on code and configuration, not a new execution or deployment acceptance. “Implemented” means current production package, manifest/runner, UI integration and test evidence exist. Availability is separately reported from the current register.

| ID | Diagnostic | Classification / availability | Dedicated slides |
| --- | --- | --- | --- |
| T1-D02 | Single-feature target separation | Implemented • enabled | 27–31 |
| T2-D04 | Cross-field business rule | Implemented • paused | 32–36 |
| T2-D06 | Row-completeness reconciliation | Implemented • enabled | 37–41 |
| T2-D08 | Value-semantics classification | Implemented • enabled | 42–46 |
| T2-D11 | Directional / monotonic consistency | Implemented • enabled | 47–51 |
| T4-D14 | Population Stability Index (PSI) | Implemented • enabled | 52–56 |
| T3-D12 | Label-consistency rule | Planned; registered workflow pending | 60 |
| T5-D17 | Resolution / maturity rate by vintage | Planned; registered workflow pending | 61 |
| T6-D20 | AI-proposal workflow | Planned; registered workflow pending | 62 |
| T1-D01 | Post-outcome field / look-ahead present | Planned; deferred, not registered | 63 |
| T1-D03 | Categorical leakage (association strength / purity) | Planned; deferred, not registered | 64 |
| T2-D05 | Derivation identity | Planned; deferred, not registered | 65 |
| T2-D07 | Disguised-missing / staleness | Planned; deferred, not registered | 66 |
| T2-D09 | Statistical outlier (robust-Z) | Planned; deferred, not registered | 67 |
| T2-D10 | Boundary / pile-up detection | Planned; deferred, not registered | 68 |
| T3-D13 | Target-rate break (change-point) | Planned; deferred, not registered | 69 |
| T4-D15 | KS distance | Planned; deferred, not registered | 70 |
| T4-D16 | Segment coverage | Planned; deferred, not registered | 71 |
| T5-D18 | Resolved-only vs all differential | Planned; deferred, not registered | 72 |
| T5-D19 | Seasoning / maturity profile | Planned; deferred, not registered | 73 |
| SUP-MISS | Missingness Mechanism | Implemented supporting investigation; outside the nine-row register | 57–59 |

**Numbered inventory:** 20 workbook entries = 9 registered + 11 deferred. Registered entries = 5 enabled implementations + 1 paused implementation + 3 planned workflows. Each of the six current implementations has the same five-page sequence: purpose, approach, evidence, launch, issue/RCA. Each remaining numbered diagnostic has a dedicated concise page.

Broader references are cataloged separately, not counted as extra executable diagnostics:

| Reference | Classification |
| --- | --- |
| R01  Formal MCAR / MNAR testing | Planned / deferred; descriptive supporting analysis is available |
| R02  Downturn / regime identification | Referenced; no sufficient implementation contract |
| R03  Feature drift over vintages | Partially implemented: two-snapshot PSI; monitoring workflow planned |
| R04  Delivery drift / degradation | Partially implemented: version/profile comparison; full monitoring planned |
| R05  Survival / Kaplan–Meier analysis | Referenced; not registered or implemented as a diagnostic |
| R06  Portfolio migration / acceptance funnel | Referenced; insufficient method and workflow detail |
| R07  Tail / cluster investigation | Referenced; legacy tail test retired |
| R08  Snapshot / timestamp lineage checks | Referenced; overlaps deferred D01, no separate executor |
| R09  Data latency / feed monitoring | Referenced; no current monitored-alert workflow |

The fourteen retired library names are all mapped on slides 25–26: PSI; single-feature leakage; plausibility; date ordering; missing-rate profile; post-outcome inventory; maturity profile; Little’s MCAR test; missingness-vs-target; robust-Z; key uniqueness; tail analysis; correlation stability; drift decomposition. The current `dq_tests/registry.py` is empty. These historical names do not add fourteen current diagnostics.

Metric alternatives in workbook parentheses (such as mutual information, Jensen–Shannon divergence, Wasserstein distance, IQR fences and isolation forest) are treated as method alternatives, as the workbook instructs, rather than separate implemented diagnostics.

## Slide structure

| Slides | Coverage |
| --- | --- |
| 1–8 | Product, challenges, intended value, framework, personas and RBAC |
| 9–18 | End-to-end journey, stage controls, lifecycle, RCA, knowledge, evidence and architecture |
| 19–26 | Complete current/planned/deferred, broader-reference and historical catalogs |
| 27–56 | Six implemented diagnostic sections; five slides per diagnostic |
| 57–59 | Missingness Mechanism supporting investigation |
| 60–73 | Three planned and eleven deferred numbered diagnostic pages |
| 74–87 | Inventory, real repository fixture, source assessment, gaps, assumptions, terms, source matrix, slide index and validation items |

## Assumptions, conflicts and explicit qualifications

- Use the requested name **Data Workbench**. Repository aliases include Aegis Labs, DQ Studio and Archimedes. The baseline is 0.5.2 source state, not a claim about the deployed version.
- Use the current JSON register and dispatch over stale prose: D02/D06/D08/D11/D14 are executable; D04 is paused; D12/D17/D20 are pending. Older two/four-executable narratives and weighted health-score claims were not repeated.
- D06 is observed-span continuity, not authoritative expected-population reconciliation. It cannot find completely absent facilities or extend beyond observed endpoints. The stale contract header says pending; current code/configuration enables it.
- D08 produces three governed tags plus distinct coverage outcomes. It does not certify valid data, impute values or automatically open issues. Uniform consumption of its tags by every downstream diagnostic is **to be validated**.
- D11 uses binned shape, Spearman and regression; Pearson does not vote. Its current method replaces older median-trend/inversion language. MVP analytical floors require portfolio calibration.
- PSI uses inclusive `>= 0.10` / `>= 0.25` comparisons from the engine, rather than older workbook `>` prose. Two-snapshot comparison is implemented; full continuous monitoring is not inferred.
- The current RCA UI approves **root cause identified** or **unresolved** conclusions. Approval closes the investigation/managed issue without confirming a source fix. Historical fix/rerun services are not presented as fresh-data remediation validation.
- Sponsor and diagnostic-owner responsibilities are **Proposed** operating assignments, not additional RBAC roles. Recognized authorization roles are user, admin, kb_editor and kb_reviewer. Admin does not automatically inherit publication rights.
- Manual source correction and fresh upload/reassessment are clearly separated from automated behavior. An enforced fresh-retest gate for remediation completion is **Proposed**.
- Broader references without a concrete implementation/workflow contract are **Referenced but not sufficiently documented**. R03/R04 are **Partially implemented** only through enabling PSI/version/profile features; the complete monitoring workflows remain planned.
- The only numeric sample result is an existing, explicitly labeled **synthetic D06 test fixture**, not client data, an invented outcome or a newly executed diagnostic.

## Visual and file validation

- PowerPoint 16.0: final file opened through the standard open API without repair; fresh reopen returned 87 slides.
- All 87 slides rendered natively to 1920×1080 PNG for inspection; native PDF preview exported.
- 3,795 native slide shapes; **0 picture shapes**; 0 embedded objects. Visible font: Funnel Sans.
- Native PowerPoint text-bound checks: **0 overflow candidates** after layout refinement.
- Authoring geometry checks: 0 text boxes outside slide boundaries; 0 text intersection candidates (background/container overlap excluded).
- Package checks: 87 slide parts, 87 note parts, 87 notes containing sources; ZIP integrity passed.
- Visual inspection covers every rendered slide via contact sheets plus full-resolution checks of title, framework, catalogs, representative diagnostic pages, tables and fixture evidence. Cards and arrows preserve clear spacing and reading direction. No images require aspect-ratio correction.
- Every source key resolves to an existing repository file. Per-slide source references and detailed limitations are in speaker notes and the traceability CSV.
- This task did not change application source, runtime data or source presentations. Application test suites were not rerun: test files were reviewed as implementation evidence; validation focused on the presentation and source mapping.

## Design-reference reproduction

Reused slide 4’s Funnel Sans typography, cream `#FFF2DF`, coral `#FF4F59`, dark `#181C23` / `#282A27`, gray `#6D706B`, numbered stage cards, service bands, paired foundations and conclusion strip. Rebuilt these patterns with editable native objects and more readable spacing. The source deck’s agent counts, health-score assertions and monitoring promises were not copied. Exact pixel matching was not claimed because the supplied reference could not render natively here; no source image was required or flattened into the output.

## Product-owner confirmation items

1. Confirm the deployed register and acceptance of current working-tree D08/D11 changes; decide D04 re-enablement.
2. Confirm approved RCA wording, remediation ownership, and the evidence required to declare a source fix complete.
3. Validate enterprise identity/password handling, complete route authorization and any finer-grained asset access requirements against the intended deployment.
4. Calibrate diagnostic thresholds, expected directions and business knowledge to representative portfolios.
5. Confirm downstream D08 tag integration and the intended scope of continuous monitoring.

## Reproduction assets

Internal authoring and QA files are retained under `.runtime/presentation-build/` (relative to the repository root): `build_content.py`, `deck_engine.py`, `slide_manifest.json`, `source_assessment.json`, `native_validation.json`, `package_validation.json`, and rendered slides/contact sheets. The delivered `.pptx` needs no scripts to edit its content.
