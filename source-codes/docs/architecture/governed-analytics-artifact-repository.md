# Governed Analytics Artifact Repository

## Foundation decision record

DataWorkbench's existing `analysis_artifacts` SQLite catalogue and immutable
JSON payload directory are the one canonical evidence layer. No asset IDs,
snapshot IDs, authentication, or API family were replaced. The repository owns
identity, payload integrity, lifecycle, summaries, reuse and lineage; producers
own payload schemas and registered display projections.

| Producer | Previous evidence | Consumer | Governed path |
| --- | --- | --- | --- |
| Data Sourcing inventory | `variable_inventory.profile_json` | inventory/Test Lab | compatibility source; profile artifacts can be added incrementally |
| Missingness investigation | run result JSON plus artifact | Supporting Investigations, Issues | `missingness_report`, diagnostic-local |
| Feature Target Separation | diagnostic result metrics plus artifacts | Test Lab #2, bin review | universal ROC, fine-bin, coarse-bin and IV artifacts |
| Binning review | `diag_binning_revisions` plus artifacts | #2 results | versioned immutable coarse-bin/IV artifacts |

The initial gaps were: no canonical full identity fingerprint; no database
uniqueness backstop; payloads embedded in some run results; no bounded summary
contract; no type registry; and only direct impact traversal. This increment
adds those seams while leaving legacy profile storage readable during staged
migration. It never fabricates historical evidence and does not enable PSI.

## Taxonomy and extension

Registered vocabulary includes Data Sourcing profiles/distributions/governance
references; missingness reports; feature profiles; fine/coarse bins; ROC, Gini,
IV, and reusable diagnostic evidence. PSI is intentionally not registered or
calculated. Unknown old or future types remain inspectable with generic
metadata/summary rendering.

To add a future diagnostic artifact:

1. Define a versioned JSON payload contract and validator.
2. Register an `ArtifactTypeDescriptor` with metrics and a bounded summary adapter.
3. Supply every meaning-changing input through `identity_inputs`, including
   frozen settings, dictionary/schema bindings and source artifacts.
4. Resolve exact candidates through the repository before calculating.
5. Save through `save`; persist source artifact roles and attach IDs to the
   frozen run manifest/result.
6. Add reuse, conflict, lineage and UI-summary tests.

The persistence and lineage core has no artifact-type conditional. A synthetic
test-only type can be registered with `register_artifact_type` and flows through
validation, exact reuse, listing, summaries and generic UI fallback.

## Compatibility and rollback

Schema changes are additive. Historical artifacts lacking the new fingerprint
remain readable and use legacy exact matching until backfilled. `profile_json`
is not rewritten. Payload files are written atomically before metadata insert;
an insert failure removes only that newly written file. Supersession updates
catalogue state and records an event; it never changes payload, provenance or
identity. Rollback is therefore code rollback plus retention of additive
columns/tables; no historical analytical content needs conversion.

## Guarantees and limits

Canonical JSON fingerprints ignore map order. The active-identity partial unique
index prevents concurrent duplicate governed writes. A same-identity/different-
payload write is a conflict for governed scopes. Payload hashes are verified on
detail reads and bounded audits detect missing, tampered, invalid-path and
orphan files. List APIs return metadata and bounded summaries, never payloads.
Lineage traversal is bounded and tracks ancestors/dependants safely.

Frozen-bin PSI is registered through the producer descriptor seam as `psi_bins`
and `psi`; no PSI-specific repository or repository-core conditional is used.
`psi_bins` stores draft and reviewed immutable definitions, while executable PSI
manifests reference only reviewed/frozen versions and their payload hashes.
`psi` stores feature-level totals and per-bin contributions. Its exact identity
includes ordered populations, frozen-bin lineage and payload hash, thresholds,
epsilon, methodology/engine versions, and manifest fingerprint. Repository status
remains `active|superseded`, and lineage/impact use the existing graph APIs.

Segmented PSI is deferred because the accepted diagnostic scope contract does not
currently expose a governed segment selector.
