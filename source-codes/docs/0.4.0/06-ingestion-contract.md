# 0.4.0 Data ingestion contract (ING-01…ING-10, D-20) — implemented

Replaces the shipped Data Sourcing flow (create item → upload per role → finalize → profile button
→ inventory grid). Redesigned on first principles: *simple and minimalist… good field mapping,
data dictionary validation*. Principles informed by `missingness-atlas`; nothing copied.

## 1. Three moments, one flow (ING-01/ING-02)

```
DROP ──────────────→ REVIEW ─────────────→ READY
file lands;          one screen, the only   consumable by the
parse + profile      decision surface       Test Lab (ING-07)
start immediately
```

- **Drop.** Dropping a dataset starts parsing and profiling immediately (SSE progress) — no
  finalize step, no profile button. Dropping a dictionary triggers automatic inspection and
  mapping. Use case + target are asked **once, inline, defaulted** (ING-02). Dataset required;
  everything else optional and labelled so. Minimum happy path: drop dataset → glance at Review →
  Ready.
- **Review.** One screen — per column: inferred classification, role, dictionary declaration, any
  discrepancy — **editable in place** (ING-06). Everything not touched keeps its default.
- **Ready.** Derived status; the Test Lab consumes only `ready` items.

## 2. Confidence-tiered field mapping (ING-03)

Dictionary headers map to canonical fields by:

| Tier | Match | Behaviour |
|---|---|---|
| high | exact / alias | **pre-applied silently** |
| fuzzy ≥ floor | fuzzy similarity above the floor | shown as **"confirm suggestion"** |
| below floor | — | **never guessed**; column left unmapped |

One-to-one enforced: a source column consumed by one mapping is unavailable to others (4-T3).
The floor and tier boundaries live in the semantic layer, not code constants.

## 3. Dictionary validation — structured, non-blocking (ING-04)

Findings surface as `{column, code, message}` warnings, collapsed by default. Warning codes:

| Code | Meaning |
|---|---|
| `dict_var_not_in_dataset` | dictionary declares a variable the dataset lacks |
| `dict_field_unused` | dictionary content present but not consumed — framed **"preserved, not used"**, never silently discarded |
| `type_conflict` | declared vs inferred type disagree |
| `unsupported_value` | unsupported type/role value in the dictionary |

None blocks Ready. **Hard-fail only on structural corruption** (e.g. duplicate dictionary rows),
with a clear message (4-T3/4-T4).

## 4. Dictionary states and graceful degradation (ING-05)

Item-level dictionary state: **`yes` / `thin` / `absent`** — where `thin` includes a dictionary
that looked complete but still left columns to inference (the looked-complete-but-thin downgrade).
Without a dictionary: types inferred conservatively; affected columns marked **`provisional`**.
Both states are visible wherever the item is consumed (Review, board, manifest).

## 5. Status machine (ING-07)

`uploading → profiling → needs_review → ready | failed(reason)` — **derived from events that
actually happened, never set by a button.** The Test Lab consumes only `ready`.

## 6. Persisted records — the downstream substrate (ING-08)

Ingestion persists with the item, as records the Test Lab manifest reads (contract-fixture-tested,
4-T6 = the producing half of Phase 6's consuming manifest):

| Record | Shape |
|---|---|
| confirmed mapping | per column: `{source_column, canonical_field, tier, score, confirmed_by (auto/user), dtype}` |
| dictionary state | `yes/thin/absent` + per-column `provisional` flags + dictionary descriptions |
| warnings | list of `{column, code, message}` |
| profile snapshot | per column: dtype, cardinality, null share, patterns — generic signals only |

Cross-field role resolution (CFR-05) reads the same dictionary and mapping records — the
dictionary-description ladder tier (0.88) is fed by these records, not by re-parsing files.

## 7. Replacement and history (ING-09)

Replacement is a **new delivery, never mutation**: a re-upload of the same logical dataset
increments the PLT-02 delivery record (`dataset_family_id`, `delivery_seq`, `as_of_date`,
nullable `baseline_delivery_id`). The old item is not mutated (4-T7).

## 8. No hardcoded schema knowledge (ING-10)

`TYPE_PRIORITY` (the name-hint table at `ai/v2/service.py:40-48`) and every literal column name
(`facility_id`, `origination_date`, …) do not survive. Inference uses **generic signals** (dtype,
cardinality, value patterns) plus the dictionary and KB — data, not code. A source-inspection test
asserts no schema literal remains in inference (4-T8).

## 9. Migration (plan 4.8)

Existing profiled items map onto the new records without loss (defaults: single delivery,
dictionary state derived from what was uploaded, mapping records from the existing inventory);
re-profiling optional; run-twice idempotent (4-T9).
