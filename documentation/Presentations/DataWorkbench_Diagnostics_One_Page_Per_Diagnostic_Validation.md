# Data Workbench — condensed diagnostic deck validation

Prepared 07 September 2026, using the same repository assessment and release baseline (0.5.2) as the full product deck.

## Deliverables and scope

- `DataWorkbench_Diagnostics_One_Page_Per_Diagnostic.pptx`: separate, editable 16:9 presentation; 22 slides.
- `DataWorkbench_Diagnostics_One_Page_Per_Diagnostic.pdf`: 22-page viewing / sharing copy.
- `DataWorkbench_Diagnostics_One_Page_Per_Diagnostic_Traceability.csv`: diagnostic, status, corresponding full-deck slides and source files.
- One directory slide, one slide for each of D01–D20, and one slide for the supporting Missingness Mechanism analysis.
- Existing slides and source materials were preserved. The full 87-slide deck remains the detailed product reference.

The condensed pages retain purpose, user / trigger, inputs, outputs / evidence, processing flow, finding-to-issue gate, RCA / action and a key scope boundary. Fuller original content and sources are retained in editable speaker notes. Each diagnostic footer points to its original slide or slide range.

The nine broader reference method families and fourteen retired library names remain in the full presentation; they are not added as duplicate diagnostic pages here.

## Editing and design

All 1,244 slide objects are native PowerPoint text, shapes or connectors. There are zero picture shapes, media parts or embedded objects. Text, pills, cards and workflows can be edited individually in PowerPoint. Funnel Sans is the presentation theme font. Diagnostic body copy is 12.5–13.25 pt, with smaller labels and larger titles; speaker notes retain detail that would overcrowd a single page.

The PDF is a preview export; use the PowerPoint for editing. The original cream, coral and charcoal design language is retained.

## Validation performed

- Opened in Microsoft PowerPoint 16.0 without repair; saved and reopened successfully.
- Confirmed 22 PowerPoint slides and 22 PDF pages, with five native navigation sections.
- Verified D01–D20 occur exactly once as dedicated diagnostic pages, plus one supporting analysis page.
- Measured every text box in native PowerPoint: zero overflow candidates and zero text overlaps (2 pt overflow / 3 pt intersection tolerances).
- Rendered all slides at 1920 × 1080. Visually reviewed every page in contact sheets and inspected the directory, implemented and deferred templates at full resolution.
- Confirmed all 22 slides have speaker notes with source references.
- Compared SHA-256 hashes for all 8 pre-existing files in the presentation folder: all are unchanged.

Full original PPTX SHA-256: `f38dade1194289affd4344c77c8021664a4034734216dd9af813156d63816abb`.

## Status and content qualifications

- D02, D06, D08, D11 and D14 are enabled. D04 is implemented but paused by the register; its code path is identified explicitly.
- D12, D17 and D20 are registered but planned (`workflow_pending`). The eleven remaining numbered diagnostics are planned / deferred and are not registered executors.
- Planned processing, output and RCA descriptions are explicitly labeled proposed; they do not claim deployed capability.
- Missingness Mechanism is implemented supporting analysis, separate from the diagnostic register; its evidence is descriptive, not proof of MCAR / MAR / MNAR or causality.
- Current RCA closes with an approved root cause or an unresolved result. Closure does not certify a source correction. Optional remediation tracking is independent; new data upload and rerun are manual.
- Implementation status comes from repository code / configuration review, not a deployment acceptance test. Detailed source qualifications from the full presentation are carried into the notes.

## Slide map

| Page | Diagnostic | Status | Full deck |
| --- | --- | --- | --- |
| 2 | T1-D02 — Single-feature target separation | Enabled | 27–31 |
| 3 | T2-D04 — Cross-field business rule | Implemented · paused | 32–36 |
| 4 | T2-D06 — Row-completeness reconciliation | Enabled | 37–41 |
| 5 | T2-D08 — Value-semantics classification | Enabled | 42–46 |
| 6 | T2-D11 — Directional / monotonic consistency | Enabled | 47–51 |
| 7 | T4-D14 — Population Stability Index (PSI) | Enabled | 52–56 |
| 8 | SUP-MISS — Missingness Mechanism | Implemented · supporting | 57–59 |
| 9 | T3-D12 — Label-consistency rule | Planned · registered | 60 |
| 10 | T5-D17 — Resolution / maturity rate by vintage | Planned · registered | 61 |
| 11 | T6-D20 — AI-proposal workflow | Planned · registered | 62 |
| 12 | T1-D01 — Post-outcome / look-ahead field | Planned · deferred | 63 |
| 13 | T1-D03 — Categorical leakage: association / purity | Planned · deferred | 64 |
| 14 | T2-D05 — Derivation identity | Planned · deferred | 65 |
| 15 | T2-D07 — Disguised missingness / staleness | Planned · deferred | 66 |
| 16 | T2-D09 — Statistical outlier: robust Z | Planned · deferred | 67 |
| 17 | T2-D10 — Boundary / pile-up detection | Planned · deferred | 68 |
| 18 | T3-D13 — Target-rate break / change-point | Planned · deferred | 69 |
| 19 | T4-D15 — KS distance | Planned · deferred | 70 |
| 20 | T4-D16 — Segment coverage | Planned · deferred | 71 |
| 21 | T5-D18 — Resolved-only vs all differential | Planned · deferred | 72 |
| 22 | T5-D19 — Seasoning / maturity profile | Planned · deferred | 73 |
