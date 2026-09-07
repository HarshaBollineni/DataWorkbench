# New diagnostic experiment blueprint

Copy the templates in this folder into a new
`experiments/test-lab/{{DIAGNOSTIC_KEY}}/` directory and replace every `{{TOKEN}}` before analytical
work begins. Do not modify these canonical templates for one diagnostic.

To create the complete named scaffold from the workspace root:

```powershell
powershell -File experiments/test-lab/_blueprints/new-diagnostic/New-DiagnosticExperiment.ps1 `
  -TestId 2 -DiagnosticId 8 -Slug value_semantics -Title "Value Semantics" `
  -Owner data-quality -Version 0.1.0
```

Add `-WhatIf` to preview the target without writing. The command refuses to overwrite an existing
diagnostic directory.

## Required tokens

| Token | Example | Meaning |
|---|---|---|
| `{{DIAGNOSTIC_KEY}}` | `t2_d08_value_semantics` | Stable experiment/backend identifier |
| `{{FRONTEND_KEY}}` | `t2-d08-value-semantics` | Stable UI feature identifier |
| `{{TEST_ID}}` | `2` | Framework test number |
| `{{DIAGNOSTIC_ID}}` | `8` | Framework diagnostic number |
| `{{SLUG}}` | `value_semantics` | Snake-case capability name |
| `{{TITLE}}` | `Value Semantics` | User-facing title |
| `{{OWNER}}` | `data-quality` | Accountable owner/team |
| `{{VERSION}}` | `0.1.0` | Initial experiment contract version |

## Required experiment structure

```text
{{DIAGNOSTIC_KEY}}/
  README.md
  intake.yaml
  experiment-contract.yaml
  process-record.md
  kb/
    archive/
  prompts/
  schemas/
  src/
    engine.py
    summaries.py
    actions.py
  inputs/
    test_fixtures/
  tests/
    test_engine.py
    test_invariants.py
    test_contract.py
  notebooks/
  output/                         generated; not a source of truth
  promotion/
    readiness.yaml
    artifact-inventory.md
    known-limitations.md
```

Use these templates:

| Destination | Template |
|---|---|
| `intake.yaml` | [`intake.template.yaml`](intake.template.yaml) |
| `experiment-contract.yaml` | [`experiment-contract.template.yaml`](experiment-contract.template.yaml) |
| `process-record.md` | [`process-record.template.md`](process-record.template.md) |
| `promotion/readiness.yaml` | [`promotion-readiness.template.yaml`](promotion-readiness.template.yaml) |
| `promotion/artifact-inventory.md` | [`artifact-inventory.template.md`](artifact-inventory.template.md) |
| `promotion/known-limitations.md` | [`known-limitations.template.md`](known-limitations.template.md) |

## Working sequence

1. Complete intake, including the user question, intended decision, general-context behavior, and
   non-goals.
2. Freeze the experiment contract version before producing acceptance evidence.
3. Implement deterministic rules and fixtures. Keep AI matching/adjudication separate from rule
   correctness.
4. Record each meaningful evidence run in `process-record.md`.
5. Complete the artifact inventory and known limitations.
6. Mark a readiness gate `true` only when its evidence reference exists and is reproducible.
7. Hand the completed promotion directory and contract to the production blueprint. Nothing under
   `output/` is part of the handoff.

The full cross-stage policy is in the
[`canonical lifecycle contract`](../../../../source-codes/docs/diagnostics/_blueprints/diagnostic-lifecycle.md).
