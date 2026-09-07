# {{TITLE}} promotion artifact inventory

| Experiment asset | Version/hash | Production treatment | Production destination | Reason |
|---|---|---|---|---|
| `README.md` | {{REFERENCE}} | rewrite | `backend/domains/test_lab/diagnostics/{{DIAGNOSTIC_KEY}}/README.md` | Convert evidence into operator/user documentation |
| `experiment-contract.yaml` | {{REFERENCE}} | translate | `docs/diagnostics/{{SLUG}}/contract.md` | Freeze the accepted deployable behavior |
| `src/engine.py` | {{REFERENCE}} | reimplement/review | `backend/domains/test_lab/diagnostics/{{DIAGNOSTIC_KEY}}/engine.py` | Remove experimental assumptions and imports |
| `kb/{{FILE}}` | {{REFERENCE}} | copy/version | `backend/knowledge_base/{{FILE}}` | Publish governed knowledge resource |
| `prompts/{{FILE}}` | {{REFERENCE}} | copy/version | `backend/ai/agents/{{FILE}}` | Preserve prompt identity |
| `schemas/{{FILE}}` | {{REFERENCE}} | copy/version | {{DESTINATION}} | Preserve structured contract |
| `tests/` | {{REFERENCE}} | translate/extend | `backend/tests/.../{{DIAGNOSTIC_KEY}}/` | Retain method evidence and add integration coverage |
| `notebooks/` | {{REFERENCE}} | exclude | experiment only | Evidence, never runtime |
| `output/` | generated | exclude | none | Not a source of truth |

## Inventory rules

Every asset must be marked `copy/version`, `reimplement/review`, `translate`, `archive`, or
`exclude`. Record a reason for exclusions and a content hash for every governed KB, prompt, schema,
or fixture package used as acceptance evidence.
