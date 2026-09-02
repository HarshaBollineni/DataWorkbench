# Root Cause Analysis

This domain owns the governed RCA case lifecycle, deterministic analysis-helper catalogue, and
effective-challenge adapter. Taxonomy, Knowledge Base, issue management, authentication, and the
Analysis Artifact Repository remain separate capabilities because they serve workflows beyond RCA.

| File | Ownership |
| --- | --- |
| `service.py` | Cases, evidence, hypotheses, confirmation, fixes, closure, and reusable-knowledge hand-off |
| `analysis_helpers.py` | Vetted deterministic RCA probes and generated-helper validation |
| `effective_challenge.py` | Noether effective-challenge adapter |

Former `rca`, `ai.rca_helpers`, and `ai.rca_checker` imports are exact compatibility aliases.
