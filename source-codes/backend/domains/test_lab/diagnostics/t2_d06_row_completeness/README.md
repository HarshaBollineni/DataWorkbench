# T2-D06 Row Completeness backend

This package owns the production implementation of **T2 · D06 — Row-completeness
reconciliation**.

| Module | Responsibility |
| --- | --- |
| `api.py` | API-facing request and response contracts |
| `engine.py` | Pure deterministic calculation |
| `models.py` | Versioned domain and artifact payloads |
| `periods.py` | Reporting-period parsing and canonicalization |
| `manifest.py` | Draft, review, freeze, and provenance workflow |
| `runner.py` | Execution, persistence, AAR reuse, and reporting |
| `knowledge.py` | Governed T2-D06 knowledge-package lifecycle |

Shared Test Lab registration, readiness, thresholds, inference audit, result promotion, and AAR
services remain outside this package. Compatibility aliases under `backend/dq_diagnostics` preserve
the former import paths during staged migration.

Verification mirrors this package under `backend/tests/unit`, `backend/tests/contract`, and
`backend/tests/integration`, using the same `test_lab/diagnostics/t2_d06_row_completeness` path.

## Governed report

The PDF and text downloads follow the same analysis-report hierarchy as T2-D11 while preserving
D06's row-level grain. They separate the portfolio coverage metric from the independent key,
calendar, duplicate, period, and segment rationales; show masked row examples only for failed
rules; and distinguish each rule's aggregate decision unit from its retained evidence-row count.

The report records finding workflow state and optional AI role-review provenance. AI is limited to
pre-freeze semantic-role advice over bounded column metadata. It never receives row-level data,
applies a mapping automatically, calculates completeness metrics, or influences the verdict.
