# `demo-db` lineage and retirement

## Retired dependency path

`demo-db/*.db` was copied into the container by `Dockerfile` and staged by
`deploy.ps1`. The old `database.py` catalogue then supplied those files to the
Galileo ingestion, datasource, and RCA routes. The same catalogue fed demo
reset/seeding behavior and older AI helpers.

Those routes are no longer mounted in Archimedes 0.2.0. The database files are
not in the repository, deployment stage, or container image.

## Supported Archimedes path

```
Browser Data Sourcing page
  -> /api/v2/items and file upload
  -> UPLOAD_DIR/<item-id>/ source files + item.db
  -> system_state.db metadata, profile, plan, results, issues and score
  -> Test Lab, Issue Management, RCA and PDF report
```

`UPLOAD_DIR` defaults to `backend/uploads` locally. Azure configures it as
`/data/uploads` on the Archimedes Azure File Share, alongside the persisted
system-state backup. This makes every active assessment independent of bundled
sample data and durable across a container restart.

## Transition rule

Pre-0.2.0 records whose saved file path is absent or outside the durable upload
directory are labelled `requires_reupload`. Their metadata is retained; using
the same name in Data Sourcing resumes that item and replaces its source files.
