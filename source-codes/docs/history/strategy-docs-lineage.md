# `strategy-docs` lineage and retirement

## Decision

`strategy-docs/` is historical Galileo planning material, sample data, and
test evidence. It is not a runtime dependency of Archimedes and is safe to
remove with its two developer-only generators. It is deliberately absent from
the Azure image and deployment stage.

## Historical lineage

```
strategy-docs source artifacts
  ├─ framework workbooks and rule text
  │    └─ curated into backend/knowledge_base/dq_framework_data.json
  │       and backend/knowledge_base/business_rules.json
  │         └─ boot seeders -> framework tables -> v2 API -> UI
  ├─ users_config.json
  │    └─ copied into backend/seeds/data/users_config.json
  │         └─ users/authentication seed
  ├─ old warehouse/schema files and demo evidence
  │    └─ retired Galileo-only routes and documentation; no active path
  └─ CRE verification fixtures
       └─ backend/tools/run_cre_e2e.py and make_cre_verification_data.py
          (developer-only scripts, never called by the product or deployment)
```

## Active Archimedes path

```
User upload
  -> UPLOAD_DIR (Azure: /data/uploads)
  -> local, regenerable per-item execution cache
  -> persisted system-state metadata and framework knowledge base
  -> v2 profiling, Test Lab, Issue Management, RCA, and PDF report
```

The framework and rule data used by this path are versioned in
`backend/knowledge_base/`; the seeders do not read `strategy-docs/` at boot.
The UI's Chromium workflow fixtures are also independent of it.

## Impact of removal

| Area | Impact |
|---|---|
| Azure API, frontend, deployment | None: `strategy-docs/` is excluded from the container and deployment context. |
| Framework, recommendations, reports | None: their active data is in `backend/knowledge_base/` and seeded into the system database. |
| User sign-in seed | None: its JSON is already in `backend/seeds/data/`. |
| v2 upload, profile, test, issue, RCA and report workflows | None: these use user uploads and persisted state only. |
| `backend/tools/run_cre_e2e.py` and `make_cre_verification_data.py` | They would fail, so they are retired together with the folder. |
| Historical planning/evidence documents | No product impact; stale references are removed from current documentation. |

## Transition rule

Do not recreate `strategy-docs/` as a runtime folder. New product reference
data belongs in a versioned runtime location such as `backend/knowledge_base/`;
new test fixtures belong beside the tests that consume them.
