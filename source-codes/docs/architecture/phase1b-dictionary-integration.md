# Phase 1B Slice 3 — Dictionary integration

## First increment

Data Sourcing remains the owning workflow. The existing upload, profiling,
variable-inventory, schema review, and snapshot commit screens are extended;
no second dictionary wizard or authentication flow is introduced.

The increment adds:

1. guided mapping from supplied dictionary headers to the canonical fields
   `column_name`, `description`, `logical_type`, `role`,
   `missing_value_codes`, and `business_context`;
2. bounded profiling and explicit normalization of supplied logical-type and
   role vocabulary;
3. explicit accept/dismiss decisions for fuzzy dictionary-to-data column
   matches;
4. explicit confirmation before special values are treated as missing;
5. immutable, asset-bound dictionary versions linked to the snapshot; and
6. automatic inclusion of confirmed missing-value codes in the frozen
   Missingness Mechanism manifest.

## Decision boundaries

- High-confidence normalized header/column matches may be applied
  deterministically.
- Fuzzy mappings are suggestions only until a user accepts them.
- Missing-value codes are metadata suggestions only until confirmed per
  variable. Unconfirmed values never alter missingness calculations.
- Replacing a dictionary creates a new dictionary version. Existing snapshots
  and analysis manifests retain their original bindings.
- Dictionary context cannot silently change analysis parameters or diagnostic
  verdicts.

## Reuse

The canonical header/value vocabulary and inspection approach are adapted from
`missingChecks`. The richer reconciliation and schema-governance concepts in
`tempTestPath` remain the source for later increments (valid values/ranges,
reviewed constraints, compatibility classification, and explicit promotion).
DataWorkbench identity, persistence, upload, and review contracts remain
authoritative.
