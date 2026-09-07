# Diagnostic 8 — experiment-to-production promotion record

This is a completed reference implementation of the
[production diagnostic blueprint](../_blueprints/production-diagnostic/README.md), paired with the
[experiment blueprint](../../../../experiments/test-lab/_blueprints/new-diagnostic/README.md).

## Source

The promotion source is `experiments/test-lab/t2_d08_Value_semantics`, including its README, v0.1/v0.2 KB packages, terminology package, role matcher, deterministic executor, structured role-adjudication boundary, fixtures, tests, benchmark and notebook evidence.

## Carried into production

- Value Semantics KB v0.1 and v0.2;
- enhanced credit-risk terminology as active v0.3;
- role-adjudication prompt v0.2 and structured response contract;
- terminology normalisation, exact-first matching and role implications;
- deterministic cell-rule executor, summaries and action grouping;
- methodology explanations and known-boundary material from the experiment README.

## Production additions

- mandatory, reopenable intended-use information page;
- General/unspecified default plus optional non-enforcing PD/LGD/EAD context;
- resumable tenant-aware draft, patch, coverage preview and immutable freeze;
- explicit human confirmation for non-exact and AI role proposals;
- Test Lab launch/progress/results integration;
- authenticated PDF/text report downloads;
- JSON and Parquet AAR artifact types, integrity, lineage and download support;
- KB-section documents with active-version and history retention;
- grouped finding disposition and shared issue/RCA handoff;
- deployment README and reusable lifecycle blueprint.

## Production corrections

- lower-frequency review/update declarations now control their execution windows;
- panel exclusions must be executable structured predicates;
- different competing tags require a decisive KB precedence relationship;
- sentinel evidence can run independently when variance prerequisites are absent;
- unclassified coverage is configuration/SME review rather than automatic RCA evidence.

## Excluded from runtime

Notebooks, generated outputs, checkpoints, synthetic fixture data and benchmark harnesses remain experiment evidence. They are not imported by the production service. Secrets and live-provider responses are not copied.

## Storage and compatibility

The AAR schema adds backward-compatible `payload_media_type` and `payload_filename` metadata. Existing JSON artifacts default to `application/json`; D08 cell outputs use Parquet. Historical artifact and diagnostic records are not rewritten.

## Acceptance evidence

Verified on 07 Sep 2026:

- D08 unit/integration/API suite: all tests passed, covering all three tags, no-tag/unscoped behavior, General context, information acknowledgement, declaration validation, role ambiguity, run execution, reports, KB history, AAR Parquet read/download and zero automatic issues;
- combined D08 + AAR + D11 + register/framework gate: **92 passed, 2 subtests passed**;
- affected AAR, KB and D11 regression slice before final consolidation: **86 passed**;
- frontend: ESLint passed with zero warnings, **56 unit tests passed**, and the Vite production build passed;
- full backend observation: **771 passed, 1 skipped**; five unrelated suite-harness cwd/shared-state failures all passed when rerun in their expected backend directory and isolated state (**7 passed**). No D08 test failed in the full run.

The active-resource loaders, Python compilation, framework JSON parsing and `git diff --check` also passed.

## Enablement decision

Diagnostic 8 is `executable` with `enabled_by = T2D8 production acceptance gate (07 Sep 2026)`. The source of truth now describes only the three governed tags and explicitly separates `NO_TAG`, `UNCLASSIFIED` and `UNSCOPED` coverage outcomes.

## Rollback

Set only D08's register status back to `workflow_pending` to prevent new launches. Do not remove the additive AAR columns, delete KB history, or modify frozen manifests/artifacts. Completed runs remain readable.
