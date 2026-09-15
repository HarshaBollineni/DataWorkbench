# T2-D06 Row Completeness

**Status:** MVP production workflow

**Framework location:** Test 2, Diagnostic 6

**Knowledge Base:** Row Completeness YAML schema v2

**Methodology:** `row-completeness-observed-span-v1`

T2-D06 determines whether required facility-period rows are present and uniquely assignable within
each facility's observed reporting span. It calculates deterministic coverage and independently
reports key-assignability, calendar, duplicate, period, facility and optional segment findings.

## Package ownership

| Module | Responsibility |
| --- | --- |
| `api.py` | API-facing request and response contracts |
| `engine.py` | Pure deterministic calculation |
| `models.py` | Versioned domain and artifact payloads |
| `periods.py` | Reporting-period parsing and canonicalization |
| `manifest.py` | Draft, review, DSC resolution, freeze and provenance workflow |
| `runner.py` | Execution, persistence, AAR reuse and reporting |
| `knowledge.py` | YAML loading, closed-contract validation, editing and activation |

Shared Test Lab registration, readiness, thresholds, inference audit, result promotion, Dataset
Structure Context (DSC) resolution and Analytics Artifact Repository (AAR) services remain outside
this package.

## Knowledge Base contract

The source-controlled baseline is
`backend/knowledge_base/row_completeness_v2.yaml`. D06 does not load or accept JSON Knowledge Base
packages in the MVP. YAML uploads larger than 2 MB, malformed YAML, and documents whose root is not
a mapping are rejected.

The schema-v2 envelope contains:

- `metadata`: identity, purpose, scope, version and change note;
- `governance`: lifecycle authority, separation of approval and activation, change summary and
  supersession reference;
- `execution_context`: DSC dependencies, local-change policy and manifest-pinning requirement;
- `diagnostic` and `methodology`: user-facing purpose and supported calculation boundary;
- `configuration`: reporting-grain and continuity-floor defaults;
- `rules`: the six governed rules and their closed Python primitive bindings.

YAML declares configuration and dependencies; it cannot supply executable code. Validation requires
exactly `T2D6-01` through `T2D6-06`, their supported order, roles, optionality, floor usage and
registered primitives. The checked-in baseline is installed as an immutable version. Internally,
the parsed package continues to use the existing JSON database column.

The guided editor exposes only supported wording, severity and default changes. Saving creates a
working draft. Approval and activation remain separate actions: a validated draft does not affect
new runs until a reviewer activates it. Activation never changes an already frozen run.

## Dataset Structure Context dependency

The active YAML declares the DSC request used by `manifest.py`; the request is not duplicated as a
separate diagnostic-local inference contract. The shared
`domains/test_lab/shared/knowledge_provenance.py` helper projects that declaration into the bounded
DSC request and constructs the immutable KB reference.

| Selector | DSC predicate | Requirement | Accepted state | Diagnostic use |
| --- | --- | --- | --- | --- |
| `default-entity` | `table.structure/default_entity_binding` | Required | `confirmed` | Proposes `roles.facility_id` |
| `default-temporal` | `table.temporal/default_temporal_binding` | Required | `confirmed` | Proposes `roles.period` |
| `expected-cadence` | `table.temporal/expected_cadence` | Advisory | `confirmed` | Proposes a supported reporting grain |
| `row-grain` | `table.structure/row_grain` | Optional | `observed`, `confirmed` | Records structural execution context |

At manifest creation and whenever the selected table changes, D06:

1. reads the DSC declaration from the active KB;
2. binds the selectors to the selected table;
3. resolves and pins the DSC artifact assertions;
4. applies confirmed entity and temporal defaults when both are usable;
5. maps supported cadence values to monthly, quarterly, semiannual or annual reporting grain; and
6. persists the projected `dataset_structure_context` in the run manifest.

DSC is authoritative structural context, not a completeness verdict. It does not select a segment,
change the continuity floor, generate a result or modify the KB. The user reviews the proposed scope
before execution. If required DSC is unavailable, D06 keeps the visible local configuration path;
those decisions apply only to that run.

## Run and provenance boundary

Every new manifest freezes:

- snapshot and selected-table identity;
- entity, period and optional segment bindings;
- reporting grain, coverage floor and other local run settings;
- the resolved DSC context reference, selector outcomes and immutable artifact pins;
- KB document ID, version ID, version label, content hash and retrieval-manifest ID;
- rule IDs and hashes, engine version and methodology version; and
- actor-attributed decisions and AI-use disclosure.

The KB defines reusable rules and defaults. DSC supplies governed dataset structure. The manifest
combines both with explicit local choices. A local override never updates the DSC assertion or KB.

## Execution and outcomes

After scope confirmation and freeze, the deterministic engine evaluates:

1. facility-period key assignability;
2. panel calendar continuity;
3. period-level row coverage;
4. duplicate facility-period pairs;
5. facility-level observed-span coverage; and
6. optional segment-period coverage.

The portfolio coverage metric is kept separate from the independent rule outcomes. Failed evidence
can be promoted into the existing finding, issue and Root Cause Analysis workflow only through the
normal human disposition boundary.

## Governed report

PDF and text downloads follow the same analysis-report hierarchy as T2-D11 while preserving D06's
row-level grain. They distinguish aggregate decision units from retained evidence rows, show masked
row examples only for failed rules and include KB/DSC provenance.

Optional AI is limited to pre-freeze semantic-role advice over bounded column metadata. It never
receives row-level data, applies a mapping automatically, calculates completeness metrics, changes
DSC or KB content, or influences the verdict.

## Verification

Focused validation is located under:

```text
backend/tests/unit/test_lab/diagnostics/t2_d06_row_completeness/
backend/tests/contract/test_lab/diagnostics/t2_d06_row_completeness/
backend/tests/integration/test_lab/diagnostics/t2_d06_row_completeness/
```
