# Plan 2 — Master Execution Plan (Chief Architect)

> GX + AI backend for DQ Studio over `portfolio_alt_master.db`. Single database throughout.
> Status: **Non-AI backend COMPLETE & verified** (2026-06-24).
> Spikes: `spike_metrics.py` 12/12 · `spike_gx.py` PASS · `spike_integration.py` 11/11.
> Smoke (TestClient through FastAPI): wholesale_irb all-PASS · decisioning psi+leakage FAIL, rest PASS.
> Done: W0 (db/metrics), W1 (gx context/runner/leakage), W2 (6 wrappers + routers, delegated+integrated),
> W3 (suite_builder + tests router + main.py), **W4 AI layers** (Layer 1 rule_generator + Layer 2 rca_agent
> with HITL/sandbox/SQL-guard; lazy OpenAI client — backend boots w/o key, AI endpoints 503 gracefully).
> 12 endpoints live (verified via OpenAPI + TestClient). Sandbox blocks imports; SQL guard blocks writes.
> **Pending:** live AI test once OPENAI_API_KEY is provided (LLM output quality unverified); **W5 frontend retrofit**.
> Run: `cd backend && set OPENAI_API_KEY=sk-... && uvicorn main:app --reload --port 8000`.

## Locked calibration (verified 2026-06-24 against the planted-issue answer key)

| Test | Engine | Locked rule | decisioning | wholesale_irb (clean) |
|---|---|---|---|---|
| PSI | `compute_psi` | ref=early 40% by date_col, current=rest, 10 quantile bins, eps 1e-6, FAIL if >0.25 | 0.287 **FAIL** (I1) | 0.016 PASS |
| leakage | `detect_leakage` | FAIL if any `post_outcome_cols` present & non-null | **FAIL** (I4, collections_contact_flag) | PASS |
| regime | `downturn_regime_coverage` | N/A→PASS if window ∌ 2020-03..2020-12; else ≥5% | 42.7% PASS | N/A PASS |
| vintage | `observation_window_months` | ≥24 months | 48 PASS | 36 PASS |
| KS | `compute_ks_by_segment` | max-segment KS ≤0.20 | — | 0.068 PASS |
| conditional | `conditional_spearman`+`relationship_holds` | \|rho\|≥0.10 in stated direction | — | inverse −0.318 / inc +0.435 PASS |
| target | `target_rate_quarterly_range` | quarterly rate range ≤0.15 | — | 0.044 PASS |
| missing | native GX | `not_be_null` on **psi_feature+target+date_col only** | PASS | PASS |
| uniqueness | native GX | `be_unique` on PK | PASS | PASS |
| freshness | native GX | `column_max_between(max=today)` — not future-dated | PASS | PASS |
| outlier | native GX | `column_values_between(mean±5σ)` on psi_feature | PASS | PASS |

**Contract correction (authoritative):** decisioning `post_outcome_cols = collections_contact_flag`
(the plan prose said recovery_rate — that is ifrs9's). Always read `_use_case_contracts`, never prose.

## Division of labour

**RETAINED (orchestrator — linchpin / cross-component / stateful):**
`database.py` ✅, `gx/metrics.py` ✅, `spike_metrics.py` ✅, then `gx/context.py`,
`gx/custom_expectations/leakage.py` (reference wrapper = GX spike), `gx/suite_builder.py`,
`gx/runner.py` (+`parse_gx_result`), `routers/tests.py`, `ai/prompts.py`,
`ai/rule_generator.py`, `ai/rca_agent.py`, `routers/ai_rules.py`, `routers/rca.py`, `main.py`,
frontend `context/WizardContext.jsx`, `api/client.js`, `App.jsx` wrap, `pages/RCA.jsx` (HITL).

**DELEGATED (myopic sub-agents — each owns ONE file, zero overlap):**
SA-W1..W6 = 6 custom-expectation wrappers (thin wrappers over verified `metrics.py`);
SA-R = `routers/use_cases.py` + `routers/preview.py`; SA-F1 = `NewAssessment.jsx`;
SA-F2 = `DefineTestPlan.jsx`; SA-F3 = `RunValidations.jsx`.

## Dependency-ordered waves

- **W0 (done):** requirements, database.py, metrics.py, spike → verified.
- **W1 (retained, blocking):** `pip install -r requirements.txt`; build `context.py` + `leakage.py`
  reference wrapper + `runner.py`/`parse_gx_result`; GX spike: run leakage via GX, confirm FAIL +
  correct JSON. **Emit the frozen wrapper pattern** (the contract W2 agents code against).
- **W2 (parallel):** SA-W1..W6 (6 wrappers) ‖ SA-R (routers). Each consumes the W1 pattern + a
  metrics function signature only.
- **W3 (retained):** `suite_builder.py` + `routers/tests.py`; smoke-test wholesale_irb (all PASS)
  then decisioning (psi+leakage FAIL).
- **W4 (retained, needs OpenAI key):** AI Layer 1 (`prompts.py`,`rule_generator.py`,`ai_rules.py`),
  AI Layer 2 (`rca_agent.py`,`rca.py`); `main.py`.
- **W5:** frontend — retained `WizardContext`/`client.js`/`App.jsx`/`RCA.jsx`; parallel SA-F1/F2/F3.
