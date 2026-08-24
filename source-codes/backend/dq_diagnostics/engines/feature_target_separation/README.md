# Diagnostic 2 - Single-feature target separation and IV binning

Diagnostic 2 measures how individual features separate the governed target. Its binning
output is also the governed target-aware foundation used by PSI. Fine bins preserve the
available detail; coarse bins are optimized from that fixed foundation using the run's
constraints and event/non-event evidence.

## Binning workflow

```mermaid
flowchart TB
  classDef system fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e
  classDef user fill:#fef3c7,stroke:#b45309,color:#78350f
  classDef artifact fill:#ede9fe,stroke:#6d28d9,color:#4c1d95
  classDef execution fill:#dcfce7,stroke:#15803d,color:#14532d

  S0[Select features and confirm target route]:::user --> S1[Read selected feature and governed target]:::system
  S1 --> T{Analytical feature type?}:::system

  subgraph FINE[Fine-bin foundation]
    T -->|Numeric| N0{Advanced numeric override?}:::user
    N0 -->|No| N1[Generate automatic numeric fine bins]:::system
    N0 -->|Yes| N2[Enter optional special-value CSV<br/>and required cut CSV]:::user
    N2 --> N3[Reject blank, nonnumeric, infinite,<br/>duplicate, unordered or out-of-range cuts]:::system
    N3 --> N4[Read the evaluated feature and target again;<br/>cuts become the new fine-bin edges]:::system
    T -->|Categorical or text| C0[Order regular values by frequency;<br/>lexical tie-break]:::system
    C0 --> C1{At most 49 regular values?}:::system
    C1 -->|Yes| C2[One fine bin per observed value]:::artifact
    C1 -->|No| C3[Top 49 fine bins plus one Other fine bin]:::artifact
    N1 --> F0[Fine foundation with target counts]:::artifact
    N4 --> F0
    C2 --> F0
    C3 --> F0
    M0[Missing is always separate;<br/>confirmed and added specials are protected]:::system --> F0
  end

  subgraph COARSE[Coarse-bin optimization]
    F0 --> O0[Run Diagnostic 2 optimizer with<br/>the run's user specifications]:::system
    O0 --> O1[Numeric boundaries may use only fine edges]:::system
    O0 --> O2[Categorical groups merge complete fine bins;<br/>Other remains atomic]:::system
    O1 --> O3[Calculate event/non-event, WOE and IV]:::execution
    O2 --> O3
  end

  subgraph REVIEW[Review and governance]
    O3 --> R0[Review fine and coarse definitions,<br/>counts, WOE/IV, guards and warnings]:::user
    R0 --> R1{Accept or refine coarse bins?}:::user
    R1 -->|Refine| R2[Edit using the same fine foundation]:::user
    R2 --> O3
    R1 -->|Accept| R3[Save immutable fine, coarse and IV chain]:::artifact
    R3 --> R4{Promote eligible complete-snapshot<br/>definition to universal?}:::user
    R4 -->|No| R5[Keep diagnostic-specific revision]:::artifact
    R4 -->|Yes| R6[Validate lineage, optimizer status<br/>and row-count reconciliation]:::system
    R6 --> R7[Save universal revision for exact governed route]:::artifact
  end
```

Blue nodes are automated operations, yellow nodes are user decisions, purple nodes are
immutable AAR artifacts and green nodes are calculations.

## Categorical contract

Regular Baseline values are ranked by descending frequency with a lexical tie-break. Up to
49 values receive individual fine bins. If more values exist, every remaining value is stored
as the membership of a single fine bin displayed as `Other`. Missing and confirmed special
values are separate protected bins and do not consume the 49-value allowance.

`Other` is a governed fine bin, not merely a display shortcut. The optimizer may combine it
with other complete fine bins, but it cannot split its member values. The persisted definition
retains the exact member list so it can be applied reproducibly. Values absent from that
definition are handled by the consuming diagnostic's unseen-value rule; PSI, for example,
assigns Current-only values to `UNSEEN`.

## Numeric advanced override

The normal route generates the numeric fine foundation automatically. The advanced route lets
the user replace it with comma-separated cuts such as `10,25,50,100` and optionally add
numeric special values. Missing remains a mandatory separate bin. Confirmed Data Sourcing
specials are retained; additions belong to this new definition and do not silently alter an
older universal definition.

Cuts must be finite numeric values, unique, strictly increasing and inside the observed regular
range. `NA`, `Null`, `Infinity`, blank tokens and other text are rejected with the failing token
position. Because arbitrary new boundaries cannot be calculated exactly from an older set of
fine-bin aggregates, confirming the override performs one additional vectorized read of the
evaluated feature and governed target.

The entered cuts are fine-bin edges, not final coarse bins. Diagnostic 2 then runs its normal
optimizer using the run's user specifications. This preserves the event/non-event design:
every optimized numeric coarse boundary is one of the entered fine edges, and WOE/IV are
calculated on the resulting coarse bins.

## Storage, reuse and approval

The manifest stores the run selections and constraints. AAR stores immutable fine-bin,
coarse-bin and IV payloads, their lineage and any promotion receipt. An override produces a
new complete artifact chain; it does not mutate the automatic chain.

Diagnostic-specific revisions remain evidence for that diagnostic. Eligible complete-snapshot
revisions may be promoted explicitly to universal use. Promotion is never inferred from being
the first run because it changes which exact target-aware route Diagnostic 2 and PSI may reuse.
After a completed run, the existing variable-results table follows the PSI review pattern:
eligible rows have checkboxes, all are checked initially, and inline **Select all eligible** and
**Promote selected** controls sit in the table header. Unchecked features remain
diagnostic-specific; each feature can still be reviewed, refined and promoted individually
from its bin workspace. No separate card or browser pop-up is used.
The exact binary route includes the target column, target type and effective positive/event
class, so reversing the positive class cannot load the opposite IV definition.

Fine and coarse counts reconcile when both totals equal the number of evaluated rows after
missing target values are excluded. Persisted definitions must also retain full lineage,
valid interval/group coverage, protected missing/special bins and a recorded optimizer success
or deterministic fallback status.
## Progressive run review

Diagnostic 2 publishes a read-only feature preview after each IV/coarse result completes. The
preview combines the already-completed ROC result with AUC, Gini, IV, category, fine/coarse bin
counts, optimizer status and warnings. These SSE payloads improve visibility only: formal
`diag_results`, findings, promotion, bin editing and universal governance remain unavailable
until the full run is finalized.

Running history entries expose **View progress**. Re-entry attaches to the existing server-owned
execution and replays its buffered feature previews; it never launches a duplicate run.
