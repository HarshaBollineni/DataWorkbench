# Production diagnostic deployment blueprint

Use this pack only after the experiment's `promotion/readiness.yaml` is complete and approved. Copy
the templates to `source-codes/docs/diagnostics/{{SLUG}}/`, replace every `{{TOKEN}}`, and keep the
documents with the production implementation.

To create the production package, test, UI, and process-document skeletons from the workspace root:

```powershell
powershell -File source-codes/docs/diagnostics/_blueprints/production-diagnostic/New-DiagnosticDeployment.ps1 `
  -TestId 2 -DiagnosticId 8 -Slug value_semantics -Title "Value Semantics" `
  -Owner data-quality -Version 1.0.0 -ComponentName ValueSemantics
```

Add `-WhatIf` to preview the destinations. The command refuses to run when any target directory
already exists, does not modify the framework register, and does not copy experiment runtime code.

## Required production placement

```text
source-codes/
  backend/
    domains/test_lab/diagnostics/{{DIAGNOSTIC_KEY}}/
      __init__.py
      README.md
      manifest.py
      runner.py
      engine.py
      resources.py
      summaries.py
      actions.py
      knowledge.py                 when a KB applies
      adjudication.py              when an AI seam applies
      adjudication_contract.py     when structured AI output applies
    knowledge_base/                versioned shared/published KB files
    ai/agents/                     versioned prompts when applicable
    tests/unit/test_lab/diagnostics/{{DIAGNOSTIC_KEY}}/
    tests/integration/test_lab/diagnostics/{{DIAGNOSTIC_KEY}}/
  ui/src/features/test-lab/diagnostics/{{FRONTEND_KEY}}/
    {{COMPONENT_NAME}}ScopeGate.jsx
    {{COMPONENT_NAME}}Results.jsx
  docs/diagnostics/{{SLUG}}/
    contract.md
    implementation-checklist.md
    acceptance-evidence.md
    promotion-record.md
```

## Template mapping

| Destination | Template |
|---|---|
| `contract.md` | [`deployment-contract.template.md`](deployment-contract.template.md) |
| `implementation-checklist.md` | [`implementation-checklist.template.md`](implementation-checklist.template.md) |
| `acceptance-evidence.md` | [`acceptance-evidence.template.md`](acceptance-evidence.template.md) |
| `promotion-record.md` | [`promotion-record.template.md`](promotion-record.template.md) |
| Optional machine-readable mapping | [`deployment-map.template.yaml`](deployment-map.template.yaml) |

## Promotion sequence

1. Verify the experiment path, contract version, evidence hashes, and readiness approval.
2. Complete `deployment-map.yaml`; identify every file that is carried, rewritten, replaced,
   archived, or excluded.
3. Freeze the production contract before wiring the runner.
4. Implement backend manifest/execution/result/report/artifact/action boundaries.
5. Implement the matching Test Lab scope and results components.
6. Publish active and historical KB versions when knowledge applies.
7. Complete security, LLM, AAR, issue/RCA, process, and regression gates.
8. Record exact validation commands/results and known limitations.
9. Enable the framework register entry only after all blocking gates pass.
10. Retain a narrow rollback that disables new launches without deleting historical evidence.

Production code must not import from the experiment. Reuse occurs through accepted contracts,
versioned resources, and reviewed reimplementation.
