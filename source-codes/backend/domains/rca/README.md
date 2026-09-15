# Root Cause Analysis

This domain owns the governed RCA case lifecycle, deterministic analysis-helper catalogue, and
effective-challenge adapter. Taxonomy, Knowledge Base, issue management, authentication, and the
Analysis Artifact Repository remain separate capabilities because they serve workflows beyond RCA.

| File | Ownership |
| --- | --- |
| `service.py` | Cases, evidence, hypotheses, confirmation, fixes, closure, and reusable-knowledge hand-off |
| `initial_review.py` | Structured LLM interpretation of bounded deterministic opening evidence |
| `analysis_helpers.py` | Vetted deterministic RCA probes and generated-helper validation |
| `effective_challenge.py` | Noether effective-challenge adapter |

Shared provider configuration and primary/fallback execution live in `ai/model_registry.py` and
`ai/model_runtime.py`. RCA uses GPT-5.6 Sol as primary and GPT-5.4 Mini as its single fallback for
configured retryable deployment failures. Endpoint and key values remain backend-only; AAR retains
the selected deployment metadata, every attempt, timestamps, response identifier, usage, outcome,
and structured review result. Initial Review sends bounded metadata and aggregates, never source
rows, and deterministic evidence remains usable when the model review fails.

Former `rca`, `ai.rca_helpers`, and `ai.rca_checker` imports are exact compatibility aliases.
