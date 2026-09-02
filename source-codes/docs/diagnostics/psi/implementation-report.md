# Diagnostic 14 PSI implementation report

## Current design

PSI uses a deterministic route selected from population mode and target availability. If no
governed target exists, target-free PSI is automatic and target absence is neither a blocker
nor an approval step. If a saved target exists, the user chooses whether to use it. The previous
user choice between repository matching and new generation has been removed from the UI and
new manifests route automatically:

- one snapshot, with target: regenerate Diagnostic 2 IV fine and optimized coarse bins on the
  split Baseline;
- one snapshot, without target: regenerate PSI-contract bins on the split Baseline;
- two snapshots, without target: regenerate PSI-contract bins on the complete Baseline; and
- two snapshots, with target: load the explicitly promoted Diagnostic 2 chain for the exact
  complete Baseline identity, or create a new diagnostic-specific chain and offer promotion.

The detailed Mermaid workflow and operational contract are maintained in
`backend/domains/test_lab/diagnostics/t4_d14_population_stability/README.md`.

## Binning contracts

Numeric target-free features use Baseline deciles (duplicate cuts collapse). Categorical
target-free features use the top 49 Baseline values ordered by frequency, lexical tie-breaks,
and one `OTHER_BASELINE` group. Missing, confirmed special values and Current-only `UNSEEN`
values remain separate.

Target-aware PSI is a Diagnostic 2 producer. Numeric coarse cuts must be IV fine edges.
Categorical fine bins keep up to 49 observed regular Baseline values separately and place all
remaining values in one atomic `Other` fine bin; coarse groups merge complete fine bins and
cannot split `Other`. Fresh target-aware PSI writes diagnostic-specific fine/coarse/IV
artifacts; only an explicit full-Baseline promotion writes a universal chain.

## Governance

The first completed run is not automatically universal. Promotion validates that the run uses
the complete Baseline and governed target, that the fine/coarse/IV chain is readable and
schema-valid, that optimizer/fallback status is recorded, and that fine/coarse row totals
reconcile. Prior immutable revisions are retained.

Every PSI execution still requires explicit review and freezing. The rare arbitrary numeric
override accepts strict numeric CSV cuts and optional special values, performs one warned
Baseline feature rescan, writes a PSI-specific definition, and is intentionally ignored on
future reruns.

## Execution

The same frozen definition is applied to Baseline and Current. All rows reconcile to regular,
missing, special or unseen bins. Contributions reconcile to total PSI. Results remain
contextual with stable/watch/investigate thresholds of 0.10 and 0.25 and never create issues
automatically.

Long-running PSI preparation and diagnostic runs are server-owned background executions. SSE
connections only observe replayable progress: disconnecting or reconnecting cannot cancel
final persistence, and duplicate observers attach to one execution. Ten-second heartbeat frames
prevent an idle solver interval from being mistaken for a dead connection. Durable manifests,
artifacts and results remain the recovery source after a server restart.

The execution broker also provides an application-wide FIFO queue for every diagnostic stream.
The default admits one workflow at a time and reports queued work to the user; the admitted
workflow retains bounded feature-level parallelism. A shared four-slot optimizer ceiling protects
direct and future IV/coarse callers, and a single ordered AAR writer prevents concurrent artifact
catalogue transactions from contending on SQLite.

## Backward compatibility

Historical manifests and internal legacy scope strings remain readable. The deprecated
bin-source patch shape remains accepted for stored clients during migration, but the current UI
does not call it and saved feature selection restores the automatic route.

The shared target contract now fingerprints the target column, resolved target type and
effective binary positive/event class. Opposite event definitions can coexist as distinct
universal lookup routes and cannot cross-match. Numeric/string-equivalent displayed labels are
canonicalized consistently. Historical column-only artifacts remain readable but are not exact
binary matches because their event class cannot be proven.
