# Diagnostic 14 - Population Stability Index (PSI)

This is the governed PSI workflow contract and its relationship with Diagnostic 2
(single-feature target separation / IV binning). The arithmetic is in this directory;
routing and artifact governance are in `manifest_population_stability.py` and
`binning_reviews.py`.

## Simplified workflow

There is no user option to search for or match bins. Population mode and target availability
determine the route. When Data Sourcing has no governed target, PSI automatically uses the
target-free contract; target absence is not a blocker and requires no user confirmation. When
a saved target exists, the user may explicitly use it or continue target-free.

```mermaid
flowchart TB
  classDef system fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e
  classDef user fill:#fef3c7,stroke:#b45309,color:#78350f
  classDef artifact fill:#ede9fe,stroke:#6d28d9,color:#4c1d95
  classDef execution fill:#dcfce7,stroke:#15803d,color:#14532d
  classDef exception fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d

  subgraph POP[Population selection]
    P0[Select PSI]:::user --> P1{Population mode?}:::system
    P1 -->|One snapshot| P2[User defines Baseline predicate;<br/>Current is its complement]:::user
    P1 -->|Two snapshots| P3[System fixes first as Baseline<br/>and second as Current]:::system
    P2 --> P4[Scan split column for preview;<br/>store predicate for reproducibility]:::system
    P3 --> P5[Require same feature name and<br/>compatible analytical type]:::system
  end

  subgraph FINE[Fine-bin selection]
    F0{Use saved governed target?}:::system
    F0 -->|No| F1{Feature type?}:::system
    F1 -->|Numeric/date| F2[Deterministic Baseline deciles;<br/>collapse duplicate cuts]:::system
    F1 -->|Categorical/text| F3[Top 49 Baseline values by frequency;<br/>remainder is OTHER_BASELINE]:::system
    F0 -->|Yes| F0A[Resolve target type and effective<br/>binary positive/event class]:::system
    F0A --> F4{One or two snapshots?}:::system
    F4 -->|One snapshot split| F5[Always run Diagnostic 2<br/>on split Baseline]:::system
    F4 -->|Two snapshots| F6{Explicit universal definition for exact snapshot +<br/>table + feature + target type + positive class?}:::system
    F6 -->|Yes| F7[Load Diagnostic 2 fine/coarse/IV chain]:::artifact
    F6 -->|No| F8[Run Diagnostic 2 on complete Baseline;<br/>save diagnostic-specific chain]:::system
    F5 --> F9[Numeric IV fine bins are foundation]:::artifact
    F8 --> F9
    F5 --> F10[Categorical: up to 49 value bins;<br/>then one atomic Other fine bin]:::artifact
    F8 --> F10
  end

  subgraph COARSE[Coarse-bin selection]
    C0[Diagnostic 2 optimizer creates coarse bins<br/>from its fine foundation]:::system
    C1[Numeric coarse cuts must be IV fine edges]:::system
    C2[Categorical coarse groups partition<br/>Baseline-exact fine values]:::system
    C3[Target-free groups already follow PSI contract]:::system
    C4{Rare PSI-only override?}:::user
    C4 -->|No| C5[Keep automatic proposal]:::user
    C4 -->|Yes - numeric| C6[Enter special values CSV and cuts CSV;<br/>missing remains mandatory]:::user
    C6 --> C7[Validate exact token positions;<br/>rescan only Baseline feature]:::system
    C7 --> C8[Create PSI-specific definition;<br/>never change universal Diagnostic 2]:::exception
  end

  subgraph GOV[Review, approval and AAR]
    G0[Review labels, Baseline counts,<br/>missing/special guards and warnings]:::user
    G0 --> G1{Approve and freeze for PSI?}:::user
    G1 -->|No| G0
    G1 -->|Yes| G2[Save immutable frozen PSI bins]:::artifact
    G3{Complete two-snapshot target-aware<br/>promotion candidate?}:::system
    G3 -->|Yes| G4{Promote as universal?}:::user
    G4 -->|No| G5[Retain diagnostic-specific version]:::artifact
    G4 -->|Yes| G6[Validate chain, optimizer status<br/>and count reconciliation]:::system
    G6 --> G7[Save universal fine/coarse/IV;<br/>retain prior revisions]:::artifact
    G3 -->|Split Baseline or PSI override| G8[Not universal-eligible]:::artifact
  end

  subgraph EXEC[PSI execution and reruns]
    E0[Apply same frozen rules to Baseline and Current]:::execution
    E0 --> E1[Missing -> MISSING;<br/>declared specials -> separate bins]:::execution
    E1 --> E2[Numeric -> right-closed intervals<br/>with full range coverage]:::execution
    E1 --> E3[Known category -> declared group;<br/>Current-only -> UNSEEN]:::execution
    E2 --> E4[Count and calculate proportions by bin]:::execution
    E3 --> E4
    E4 --> E5[Sum PSI contributions]:::execution
    E5 --> E6[Stable / watch / investigate;<br/>contextual finding only]:::execution
    E7[Future PSI rerun]:::user --> E8[Re-evaluate automatic route;<br/>never match old PSI overrides]:::system
    E8 --> F0
  end

  P4 --> F0
  P5 --> F0
  F2 --> C3
  F3 --> C3
  F7 --> C0
  F9 --> C0
  F10 --> C0
  C0 --> C1
  C0 --> C2
  C1 --> C4
  C2 --> C4
  C3 --> C4
  C5 --> G0
  C8 --> G0
  G2 --> E0
  F8 --> G3
```

Blue nodes are automated decisions, yellow nodes are user decisions, purple nodes are
immutable AAR artifacts, green nodes are execution, and red is the exceptional PSI override.

## Route matrix

| Population | Target | Initial run and rerun behavior |
|---|---|---|
| One snapshot split | Target-free | Always generate deterministic PSI-contract bins from the split Baseline. |
| One snapshot split | Target-aware | Always run Diagnostic 2 IV binning on the split Baseline. The artifacts are diagnostic-specific and cannot become universal. |
| Two snapshots | Target-free | Always generate deterministic PSI-contract bins from the complete Baseline. |
| Two snapshots | Target-aware | Use the active explicitly promoted Diagnostic 2 chain for the exact complete Baseline identity; if absent, generate diagnostic-specific fine/coarse/IV artifacts and offer explicit promotion. |

An old frozen PSI definition or a definition created from another Baseline is never searched
for as a candidate. A frozen definition remains part of its original run's reproducibility
record, but a future run follows the matrix again.

## Exactness and what is matched

Only two-snapshot target-aware PSI performs a lookup. It is an exact governed Diagnostic 2
lookup, not a user-visible matching exercise. The key is immutable Baseline snapshot ID,
internal table (sheet/relation), exact feature name, compatible analytical type, governed
target column, resolved target type, effective binary positive/event class, and an explicit
universal-promotion receipt. Numeric and string forms of the same displayed binary label (for
example `1` and `"1"`) canonicalize to the same route. Reversing the event class creates a
different route, so both universal definitions can coexist and never cross-match.

Population fingerprints, transformations, missing policy, optimizer constraints and a
user-visible methodology version are not lookup choices. A split predicate is stored for
reproducibility, but one-snapshot PSI always regenerates. Missing is a mandatory common
policy. Technical payload/schema versions remain internal compatibility controls.

There is no longer a "created from another Baseline" applicability path. Approximate matching
and projection were deliberately removed from the normal PSI journey. Those artifacts remain
historical AAR evidence only.

## Alignment with Diagnostic 2

- Target-aware numeric PSI uses Diagnostic 2 IV fine bins as its governed foundation. Coarse
  optimization and ordinary refinement can retain only fine-bin edges.
- A universal coarse definition is loaded with its own fine foundation; coarse cuts never
  replace or bypass that foundation.
- Diagnostic 2 categorical fine binning keeps up to 49 observed regular values separately.
  Higher cardinality produces those 49 bins plus one atomic `Other` fine bin containing the
  remaining Baseline values. Coarse groups must merge complete fine bins and cannot split
  `Other`.
- Target-free numeric PSI uses deterministic Baseline deciles.
- Target-free categorical PSI keeps the 49 most frequent Baseline values separately and puts
  the remainder in `OTHER_BASELINE`; frequency ties are lexical.
- When no exact universal IV chain exists, PSI runs Diagnostic 2 and stores a new
  diagnostic-specific chain. It never reuses a coarse artifact alone.

## Missing, special, unseen and manual numeric overrides

Missing is always separate and not configurable. The UI says: "Missing values are kept in a
separate bin. Includes blank cells and values recognized as missing during Data Sourcing, such
as NA or Null."

Confirmed Data Sourcing special values are defaults. The advanced numeric override accepts
optional comma-separated PSI-specific special values and comma-separated cuts such as
`10,25,50,100`. It rejects blank tokens, NA, Null, Infinity, non-numeric text, duplicates and
non-increasing cuts with the exact token position. Thousands separators are unsupported.
Current-only categories use `UNSEEN`; numeric intervals cover the full range.

Arbitrary cuts cannot be derived exactly from the fixed profile histogram or IV fine
aggregates. The UI warns that this exceptional action performs one vectorized Baseline rescan
of the required feature. The resulting coarse definition applies only to the current PSI run,
does not change Diagnostic 2, and is ignored on future reruns.

## User review and approvals

The user reviews proposed fine/coarse labels and boundaries/groups, Baseline counts and
proportions, fine/coarse reconciliation, missing/special handling, range/unseen guards, and
optimizer warnings or deterministic fallback.

Freezing is explicit because it fixes the rule applied to both populations. Universal
promotion is a separate explicit choice because it changes the default for future Diagnostic 2
and target-aware two-snapshot PSI on the same immutable identity. Promotion requires the
complete unsplit Baseline, governed target, readable schema-valid fine/coarse/IV chain,
documented optimizer status/fallback, and reconciled counts.

Counts reconcile when summed fine rows equal summed coarse rows and both equal evaluated
Baseline rows. Missing-target rows are excluded from the target-aware evaluated count and
reported separately.

## AAR, scans and cached aggregates

The manifest retains run selections, predicates, review progress and artifact references. AAR
stores immutable profiles, Diagnostic 2 fine/coarse/IV versions, PSI draft/frozen definitions,
lineage, promotion receipts and PSI results. "Diagnostic-specific" is owned by that diagnostic;
"universal" is explicitly promoted for an exact full-snapshot identity.

A one-file split scans the split column for preview. Automatic generation reads selected
Baseline columns once as a batch (plus target when used). Exact universal definitions carry
cached fine/coarse aggregates; coarse refinement on existing fine edges uses those aggregates.
Only arbitrary numeric cuts require the exceptional Baseline feature rescan.

During batch preparation, the UI updates the prepared-definition count from live completion
events rather than waiting for the final manifest reload. Target-aware generation also displays
the actual Diagnostic 2 optimization mode, per-variable solver cap and concurrent worker count,
so the expected cost is visible while work is running.

### Execution ownership and reconnects

PSI draft generation and diagnostic execution are owned by a server background thread, not by
the browser's SSE connection. SSE is observation-only. Closing the tab, navigating away, a
development React remount, or an EventSource reconnect does not cancel the computation or skip
artifact/result/status persistence. A second connection attaches to the existing run and
replays buffered progress instead of starting another optimizer batch. Heartbeat frames keep an
otherwise idle connection open while a solver is working; the UI deliberately ignores them.

After a server-process restart the in-memory observer buffer is gone, but durable state remains
authoritative: completed runs replay persisted results, Diagnostic 2 `running` runs can resume,
and PSI draft generation recalculates only definitions that are not already saved. This also
means an abandoned client can no longer leave the PSI batch lock held by an unconsumed response
generator.

All diagnostic workflows share a fair FIFO execution queue. By default one diagnostic workflow
is active at a time (`DWB_DIAGNOSTIC_JOB_CAPACITY=1`), while that workflow may use up to four
shared feature-optimizer slots (`DWB_OPTIMIZER_CAPACITY=4`). Starting another diagnostic is
accepted immediately and displays its queue position; it begins automatically when the earlier
workflow reaches a terminal event. This applies across Diagnostic 2, PSI preparation, PSI
execution and the other diagnostic runners—not only to IV-related work.

AAR mutations additionally pass through one ordered writer. Computation remains parallel inside
the admitted workflow, but artifact JSON publication and its SQLite catalogue transaction cannot
compete with another artifact writer. The database busy timeout remains a last-resort safeguard,
not the normal concurrency mechanism.

PSI preparation and final PSI execution publish a read-only variable preview as each feature
finishes. Preparation previews show fine/coarse counts, metric, method, warnings or failure;
execution previews show PSI, classification, bin count and population counts. The workspace
replaces its editable review controls with this preview while the batch is active. Approval,
freezing, refinement and universal promotion become available only after the terminal event and
authoritative manifest reload.

Navigating away does not discard these previews. Reopening a running entry through **View
progress** attaches to the same server execution and replays buffered feature events before
continuing live. If the run completed while unattended, the UI opens its persisted results
instead of reconstructing an editable state from preliminary events.

## PSI calculation

The same frozen definition assigns every Baseline and Current row. The engine calculates each
bin's population share and sums `(current_share - baseline_share) * ln(current_share /
baseline_share)`, using the governed epsilon where a share is zero. Bin counts and
contributions must reconcile. Thresholds are stable below 0.10, watch from 0.10, and investigate
from 0.25. PSI remains contextual and never creates a violation automatically.

## Compatibility note

Historical manifests/artifacts with the old repository/generate choice and internal
`diagnostic_local` / `workflow_local` strings remain readable. New UI copy says
"diagnostic-specific" and never presents bin matching. The legacy patch action remains
temporarily API-readable; automatic routing overwrites it after feature selection.

`target_fingerprint` now identifies the target column, resolved target type, and effective
binary positive/event class. Diagnostic 2 and PSI persist the same canonical route in AAR
metadata and payload lineage. Historical column-only artifacts remain readable, but are not
treated as exact matches for a newly governed binary route because their event class cannot be
proven.
