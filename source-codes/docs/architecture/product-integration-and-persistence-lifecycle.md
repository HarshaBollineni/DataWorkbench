# Product integration and persistence lifecycle

**Status:** Current implementation
**Application version:** 0.5.2

## Product boundary

The product has three connected ownership planes: executable product code and
agents, governed knowledge, and execution evidence. Data Sourcing, the Analysis
Artifact Repository (AAR), Dataset Structure Context (DSC), Test Lab, Issue
Management, RCA, and the Knowledge Base participate in one governed lifecycle:

1. Data Sourcing owns the asset, immutable snapshot, retained source files,
   complete table/column inventory, and reviewed physical profiles.
2. AAR stores immutable analytical payloads and searchable identity, integrity,
   and lineage metadata for that snapshot.
3. DSC derives evidence-backed structure assertions from the complete governed
   profile set. It does not introduce another asset or snapshot identity.
4. Test Lab freezes the snapshot, DSC pins, KB provenance, scope, parameters,
   and methodology in a run manifest before deterministic execution.
5. A finding becomes an issue only through its diagnostic decision contract and
   required human review.
6. RCA opens from that issue and stores its bounded case context and evidence in
   AAR. Reusable learning returns through governed KB review rather than direct
   publication.

The KB is the canonical, independently versioned knowledge plane. Built-in
knowledge originates in source-controlled release assets; uploaded and edited
knowledge uses durable KB storage. AAR does not own either form. Run manifests
and AAR evidence pin the exact KB version, package/rule hashes, engine version,
and any run-local overrides used for reproducibility.

A production release upgrades this state in place. It does not require or
expect a historical wipe: idempotent seeders install new built-in versions,
existing versions remain addressable, and diagnostic runs continue to pin the
version they executed. AAR stores generic `governed_references` on each
immutable artifact. Today these references carry KB document/version identity,
content hash, consumer, and retrieval metadata from the frozen run manifest;
the same contract can add future governed reference types without a new
feature-specific linkage table.

## Deferred authorization boundary

Fine-grained AAR RBAC is deliberately deferred while the MVP permission model
is still evolving. The current AAR must therefore not be represented as an
authorization boundary for untrusted multi-tenant exposure. This is a declared
deployment limitation, not an implicit security guarantee.

The extension contract is fixed even though its policy is not:

- analytical identity remains `asset_id` + `snapshot_id` + artifact,
  population, target, methodology, scope, source, and governed-reference
  identity;
- usernames, roles, groups, and grants never enter an artifact fingerprint;
- `created_by` is provenance, while `owner_id` remains a workflow/reuse
  namespace and must not be interpreted as security ownership;
- the snapshot's sourcing tenant is the future authoritative tenant source,
  and access will inherit from tenant -> asset -> snapshot -> artifact;
- artifact-type/lineage sensitivity is a future policy input, not a role;
- current permissions will be evaluated outside immutable payloads so grants
  can be revoked without rewriting evidence; and
- any future implementation must use one product-wide authorization adapter,
  not feature-specific AAR, DSC, Test Lab, KB, Issue, or RCA ACL tables.

This keeps later adoption additive: persist tenant/security provenance, safely
backfill it from authoritative snapshots, and add principal-aware repository
and API guards. Existing artifact IDs, payloads, hashes, run manifests, source
lineage, and governed references must remain unchanged. A future security
review must define roles, asset/team/case grants, administrative scope, access
auditing, migration quarantine, and denial behavior before implementation.

## Knowledge audit contract

KB lifecycle events use a common versioned audit envelope containing tenant,
object type and identity, actor, exact document/version/rule or package hashes,
and the previous/new state where applicable. Upload, new-version upload,
review submission, publication, supersession, archive, executable binding,
suspicion, shelf-life expiry, schema invalidation, and case/RCA proposal
creation are append-only events in `transaction_log`.

When built-in knowledge is installed during an upgrade, and after every full
operational wipe, `knowledge_baseline_verified` records the complete installed
version/package set and a deterministic baseline fingerprint. This proves what
was available after recovery without making AAR the owner of KB content.

## SQLite lifecycle policy

`backend/persistence_policy.py` is the authoritative ownership and full-wipe
classification for every application table. A schema test fails if a table is
added without a policy. The classifications are:

- `clear`: sourced data, generated work, and execution traces removed by a full wipe;
- `reseed`: reference data cleared and recreated by the normal boot seeders;
- `preserve`: governed knowledge, users, feature flags, and append-only audit history;
- `preserve_sessions`: only active session rows survive so the initiating
  administrator is not logged out during the request.

`system_db.wipe_all_items()` verifies the clear-table postcondition inside its
transaction. If classified product state remains, the transaction fails rather
than reporting a false clean slate. Dataset uploads and AAR payloads are then
cleared from their configured durable roots. KB content and source bytes remain
intact; run-specific KB retrieval manifests are cleared with their runs.

A full wipe is intentionally a data reset, not a schema drop. It retains the
deployed schema so the application remains online, then runs the same reference
and built-in knowledge seeders as boot. The knowledge seeders are an idempotent
integrity check and recovery path, not a replacement for preserved user KBs.

## Compatibility and retirement

Names such as `plan_v2`, `results_v2`, `scores_v2`, and `issues_v2` are not a
second product database. They remain active compatibility contracts consumed by
Issue Management, supporting analyses, scoring, and RCA. The older `fw_*`
tables also remain readable during compatibility fallback even though the
nine-row `diagnostic_register` is authoritative.

The retired-workbench tables are explicitly classified and cleared by reset.
They are candidates for a future schema-removal migration only after mounted
API, rollback, and historical-database compatibility checks prove they are
unreachable. This release does not drop or rename tables merely to remove an
old suffix.

## Completeness and integrity gates

Profile publication compares `dq_item_tables` with `variable_inventory` before
writing any governed profile. Every retained table and column must have exactly
one matching inventory/profile row. An incomplete set cannot receive a DSC
publication completion, marker, or materialization job.

`GET /api/v2/analysis-artifacts/integrity-audit?complete=true` performs a
read-only full-catalogue check of payload presence, hashes, JSON validity,
lineage endpoints, and unreferenced files. The bounded default remains suitable
for routine UI checks. Operators should run the complete check before backup or
reset and again after a restore.

## Full-wipe acceptance

A wipe is complete only when:

- every `clear` table is empty before reseeding;
- configured dataset-upload and AAR payload roots are empty;
- governed KB documents, versions, rules, packages, and source bytes survive;
- built-in KB compatibility and active-package resolution pass without restart;
- reference tables contain only the normal boot seed set;
- users, active sessions, feature flags, and reset audit history survive;
- a second wipe is idempotent; and
- the focused Data Sourcing -> AAR/DSC -> Test Lab/KB -> Issue -> RCA journey passes
  against a throwaway store.
