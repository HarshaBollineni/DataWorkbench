import { useEffect, useMemo, useRef, useState } from "react";
import { History, Lock, RefreshCw, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  buildDiagnosticManifestV2, closeIssueV2, getDiagnosticResultsV2, getDiagnosticRunHistoryV2, getDiagnosticsBoardV2, promoteDiagnosticResultV2,
  getItemsV2, getResumableDiagnosticDraftV2, refreshAssetV2, dispositionFindingV2,
} from "@/api/client";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import CoverageBoard from "./testlab/CoverageBoard";
import ScopeGate from "./testlab/ScopeGate";
import RunConsole from "./testlab/RunConsole";
import FindingsPanel from "./testlab/FindingsPanel";
import { PsiJourneySummary } from "./testlab/PopulationStabilityResults";
import ScorePanel from "./testlab/ScorePanel";
import SupportingInvestigations from "./testlab/SupportingInvestigations";
import { ArtifactRepositoryCard, IssueReviewCard, SavedInventoryViewer } from "./testlab/TestLabOverview";

// Phase 6 (0.4.0) rewrite — testlab-redesign-0.4.0.md §3/§5.4: the
// four-step plan/execute/recommend/rollup wizard retires (D-16). One page,
// one item, three re-enterable panes: Coverage (the register board — no
// decisions) -> Run (scope gate, human decision #1, then SSE execution) ->
// Findings (by decision type, human decision #2, + score & report).

const TABS = [
  { key: "coverage", label: "Coverage" },
];
const legacyPanes = false;

const rememberedRunKey = (itemId) => `testlab:last-completed-run:${itemId}`;

function rememberCompletedRun(itemId, runId) {
  if (itemId && runId) sessionStorage.setItem(rememberedRunKey(itemId), runId);
}

export default function TestLab() {
  const [items, setItems] = useState([]);
  const [itemId, setItemId] = useState("");
  const [tab, setTab] = useState("coverage");

  const [board, setBoard] = useState(null);
  const [boardError, setBoardError] = useState("");

  const [activeRunId, setActiveRunId] = useState("");
  const [viewingResults, setViewingResults] = useState(false);
  const [activeRunStatus, setActiveRunStatus] = useState("");
  const [activeDiagnosticId, setActiveDiagnosticId] = useState(null);
  const [completedRunId, setCompletedRunId] = useState("");
  const [results] = useState([]);
  const [resultsLoading] = useState(false);
  const [resultsError] = useState("");
  const [message, setMessage] = useState("");
  const [draftChoice, setDraftChoice] = useState(null);
  const [launchBusy, setLaunchBusy] = useState(false);
  const initialAsset = useRef(new URLSearchParams(window.location.search).get("asset"));
  const initialSelectionDone = useRef(false);

  // ING-07 — the Test Lab consumes only items that have reached the derived
  // `ready` ingest status; nothing profiling/needs_review/failed appears here.
  const load = () => getItemsV2().then((rows) => {
    const ready = rows.filter((r) => r.ingest_status === "ready" && r.snapshot_status === "active");
    setItems(ready);
    if (initialSelectionDone.current) {
      setItemId((current) => ready.some((r) => r.item_id === current) ? current : "");
      return;
    }
    initialSelectionDone.current = true;
    if (!initialAsset.current) {
      setItemId("");
      return;
    }
    const match = ready.find((row) => row.dataset_family_id === initialAsset.current || row.asset_id === initialAsset.current);
    if (match) {
      setMessage("");
      setItemId(match.item_id);
      return;
    }
    setItemId("");
    setMessage("The requested asset has no selectable ready active snapshot; choose an asset here or return to Data Sourcing.");
  });
  useEffect(() => { load(); }, []);

  const item = useMemo(() => items.find((r) => r.item_id === itemId), [items, itemId]);
  const activeDiagnosticName = useMemo(() => board?.cards?.find(
    (card) => card.diagnostic_id === activeDiagnosticId)?.name || "",
  [board, activeDiagnosticId]);
  const locked = !items.length;

  // No separate "loading" flag: `board` itself is the loading signal (null
  // until the fetch resolves for THIS item — changeItem clears it up front
  // so a stale previous item's board is never shown mid-switch).
  const loadBoard = () => {
    if (!itemId) return;
    getDiagnosticsBoardV2(itemId).then((data) => {
      setBoard(data);
      setBoardError("");
      const completedRuns = data.cards.flatMap((card) => card.last_run
        ? [{ ...card.last_run, diagnostic_id: card.diagnostic_id }] : []);
      const remembered = sessionStorage.getItem(rememberedRunKey(itemId));
      const restored = completedRuns.find((run) => run.run_id === remembered)
        || (remembered ? { run_id: remembered } : null)
        || [...completedRuns].sort((left, right) => String(right.finished_at || "").localeCompare(String(left.finished_at || "")))[0];
      if (restored?.run_id) {
        setCompletedRunId(restored.run_id);
        if (restored.diagnostic_id != null) setActiveDiagnosticId(restored.diagnostic_id);
      }
    }).catch((e) => setBoardError(e.message));
  };
  useEffect(() => { loadBoard(); }, [itemId]); // eslint-disable-line react-hooks/exhaustive-deps

  // The item picker is the only place itemId changes by user action — reset
  // the run/findings/board state that belongs to the PREVIOUS item right
  // here (loadBoard's own effect re-fetches the new item's board).
  const changeItem = (id) => {
    setItemId(id);
    setBoard(null);
    setBoardError("");
    setActiveRunId("");
    setActiveRunStatus("");
    setActiveDiagnosticId(null);
    setCompletedRunId("");
    setTab("coverage");
  };

  // No runId -> the item's latest run (backend default), so switching to
  // Findings directly (not via a card's [view] link) still shows something
  // — always re-enterable, per testlab-redesign-0.4.0.md §3's diagram.
  const activateWorkflow = async (runId, diagnosticId) => {
    const previous = await getDiagnosticResultsV2(itemId, undefined, diagnosticId).catch(() => null);
    setActiveDiagnosticId(diagnosticId);
    if (previous?.run?.run_id) {
      setCompletedRunId(previous.run.run_id);
      rememberCompletedRun(itemId, previous.run.run_id);
    }
    setActiveRunId(runId);
    setActiveRunStatus("draft");
    setViewingResults(false);
    setDraftChoice(null);
    setTab("coverage");
  };

  const createAndOpenScope = async (diagnosticId, startAfresh = false) => {
    setLaunchBusy(true);
    setMessage("");
    try {
      const manifest = await buildDiagnosticManifestV2(itemId, {
        diagnostic_id: diagnosticId, start_afresh: startAfresh,
      });
      await activateWorkflow(manifest.run_id, diagnosticId);
    } catch (e) {
      setMessage(e.message);
    } finally {
      setLaunchBusy(false);
    }
  };

  const openScope = async (diagnosticId, boardDraft) => {
    setMessage("");
    if (diagnosticId === 14 && boardDraft) {
      setDraftChoice({ diagnosticId, draft: boardDraft });
      return;
    }
    setLaunchBusy(true);
    try {
      // Undefined means an older board response did not carry draft state.
      // Null is authoritative and proceeds directly to clean draft creation.
      if (diagnosticId === 14 && boardDraft === undefined) {
        const response = await getResumableDiagnosticDraftV2(itemId, diagnosticId);
        if (response.draft) {
          setDraftChoice({ diagnosticId, draft: response.draft });
          return;
        }
      }
      const manifest = await buildDiagnosticManifestV2(itemId, { diagnostic_id: diagnosticId });
      await activateWorkflow(manifest.run_id, diagnosticId);
    } catch (e) {
      setMessage(e.message);
    } finally {
      setLaunchBusy(false);
    }
  };

  const resumeDraft = async () => {
    if (!draftChoice) return;
    setLaunchBusy(true);
    try {
      await activateWorkflow(draftChoice.draft.run_id, draftChoice.diagnosticId);
    } catch (e) {
      setMessage(e.message);
    } finally {
      setLaunchBusy(false);
    }
  };

  const viewRun = (runId, diagnosticId, status = "done") => {
    if (diagnosticId != null) setActiveDiagnosticId(diagnosticId);
    setCompletedRunId(runId);
    rememberCompletedRun(itemId, runId);
    setActiveRunId(runId);
    setActiveRunStatus(status);
    setViewingResults(status !== "running");
    setTab("coverage");
  };

  const onRunStarted = () => {}; // ScopeGate already froze; RunConsole takes over rendering.

  const onRunDone = (event) => {
    const finishedRunId = event.run_id || activeRunId;
    setCompletedRunId(finishedRunId);
    rememberCompletedRun(itemId, finishedRunId);
    setActiveRunId(finishedRunId);
    setActiveRunStatus("done");
    setViewingResults(true);
    loadBoard(); // refresh Coverage's last_run rollup
  };

  const disposition = async () => {};


  const refreshDerived = async () => {
    if (!item?.dataset_family_id) return;
    const result = await refreshAssetV2(item.dataset_family_id);
    setMessage(`Refreshed ${result.recomputed?.length || 0} active snapshot(s).`);
    load();
  };

  if (!locked && item && activeRunId) {
    return <DiagnosticWorkflowPage item={item} runId={activeRunId}
      diagnosticName={activeDiagnosticName}
      onBack={() => { setActiveRunId(""); loadBoard(); }}
      onRunStarted={() => {}}
      onDone={onRunDone} onSelectRun={viewRun} viewResults={viewingResults}
      liveRun={activeRunStatus === "running"} />;
  }

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-950">Test Lab</h1>
          <p className="mt-1 text-sm text-slate-500">Diagnostic coverage for the 9-diagnostic register.</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm" value={itemId} onChange={(e) => changeItem(e.target.value)}>
            <option value="">Choose a ready asset</option>
            {items.map((row) => <option key={row.item_id} value={row.item_id}>{row.name}</option>)}
          </select>
          <Button variant="outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</Button>
        </div>
      </div>

      {locked && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-amber-900">
          <div className="flex items-center gap-2 font-semibold"><Lock className="h-4 w-4" /> Test Lab locked</div>
          <p className="mt-1 text-sm">Upload a dataset or database in Data Sourcing and reach Ready.</p>
        </div>
      )}

      {!locked && !item && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-600">Choose a ready active asset from the picker above to open its coverage board. Test Lab does not auto-select on left-navigation entry.</div>
      )}

      {!locked && item && (
        <>
          {!activeRunId && <>
            <SavedInventoryViewer items={items} currentItemId={itemId} />
            <div className="mb-5 grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]">
              <ArtifactRepositoryCard item={item} />
              <IssueReviewCard item={item} board={board} />
            </div>
          </>}

          <div className="mb-5 flex flex-wrap gap-2">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => {
                  setTab(t.key);
                }}
                className={`rounded-full border px-3 py-1.5 text-sm font-medium ${tab === t.key ? "border-dq-purple bg-dq-purple text-dq-dark" : "border-slate-200 bg-white text-slate-600"}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          {message && <p className="mb-3 text-sm text-red-600">{message}</p>}

          {activeRunId && <div className="mb-6 grid gap-4">
            <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3">
              <p className="text-sm font-semibold text-slate-900">Diagnostic workflow</p>
              <Button size="sm" variant="outline" onClick={() => setActiveRunId("")}>Back to Test Lab</Button>
            </div>
            <RunTab key={activeRunId} runId={activeRunId} onRunStarted={onRunStarted} onDone={onRunDone} />
          </div>}

          <div className="grid gap-6">
            <CoverageBoard board={board} loading={!board && !boardError} error={boardError} onOpenScope={openScope} onViewRun={viewRun} />
            <SupportingInvestigations key={itemId} itemId={itemId} />
          </div>

          {legacyPanes && (
            activeRunId ? (
              <div className="grid gap-4">
                {completedRunId && completedRunId !== activeRunId && (
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
                    <div>
                      <p className="text-sm font-semibold text-emerald-900">Previous completed results are available</p>
                      <p className="text-xs text-emerald-800">This scope is a newer draft. The saved AUC, Gini and IV results remain attached to run {completedRunId}.</p>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => viewRun(completedRunId)}>
                      View last completed results
                    </Button>
                  </div>
                )}
                {/* Keyed by runId: a fresh RunTab for each draft or run. */}
                <RunTab key={activeRunId} runId={activeRunId} onRunStarted={onRunStarted} onDone={onRunDone} />
              </div>
            ) : (
              <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
                Select a Ready diagnostic on the Coverage board and click “Open scope” to start a run.
              </div>
            )
          )}

          {legacyPanes && (
            <div className="grid gap-5">
              <FindingsPanel results={results} loading={resultsLoading} error={resultsError} onDisposition={disposition} onRecompute={refreshDerived} />
              <ScorePanel itemId={itemId} runId={completedRunId || activeRunId} />
            </div>
          )}
        </>
      )}

      <Dialog open={!!draftChoice} onOpenChange={(open) => !open && !launchBusy && setDraftChoice(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Continue saved PSI setup?</DialogTitle>
            <DialogDescription>
              An unfinished PSI setup is saved for this asset. Choose whether to resume it or begin with clean selections.
            </DialogDescription>
          </DialogHeader>
          {draftChoice?.draft && <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
            <div className="flex items-center gap-2 font-semibold text-slate-900"><History className="h-4 w-4" /> Saved draft</div>
            <p className="mt-2 text-slate-600">{draftChoice.draft.completed_steps} of {draftChoice.draft.total_steps} steps complete · {draftChoice.draft.selected_feature_count} variables selected · {draftChoice.draft.frozen_bin_count} bins frozen</p>
            <p className="mt-1 text-xs text-slate-500">Last saved {new Date(draftChoice.draft.last_saved_at).toLocaleString()}</p>
          </div>}
          <p className="text-xs text-slate-500">Starting afresh removes the saved setup from the active workflow. Its discarded audit record is retained, but its selections will not carry forward.</p>
          <DialogFooter>
            <Button variant="outline" disabled={launchBusy} onClick={() => createAndOpenScope(draftChoice?.diagnosticId, true)}>
              <RotateCcw className="h-4 w-4" /> Start afresh
            </Button>
            <Button disabled={launchBusy} onClick={resumeDraft}>
              <History className="h-4 w-4" /> Resume saved setup
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}

function DiagnosticWorkflowPage({ item, runId, diagnosticName, onBack, onRunStarted, onDone, onSelectRun, viewResults, liveRun }) {
  return <main className="min-h-screen bg-slate-50 p-8">
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-700">Test Lab diagnostic</p>
        <h1 className="mt-1 text-2xl font-bold text-slate-950">
          Diagnostic workflow{diagnosticName ? ` — ${diagnosticName}` : ""}
        </h1>
        <p className="mt-1 text-sm text-slate-500">{item.name} · review scope and run the selected diagnostic.</p>
      </div>
      <Button variant="outline" onClick={onBack}>Back to Test Lab</Button>
    </div>
    {viewResults ? <DiagnosticResults itemId={item.item_id} runId={runId} onSelectRun={onSelectRun} />
      : liveRun ? <RunConsole key={runId} runId={runId} onDone={onDone} />
        : <RunTab key={runId} runId={runId} onRunStarted={onRunStarted} onDone={onDone} />}
  </main>;
}

function DiagnosticResults({ itemId, runId, onSelectRun }) {
  const [payload, setPayload] = useState(null);
  const [runHistory, setRunHistory] = useState([]);
  const [error, setError] = useState("");
  const reload = () => getDiagnosticResultsV2(itemId, runId).then(setPayload).catch((e) => setError(e.message));
  // reload is intentionally re-created with the current route identifiers.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [itemId, runId]);
  useEffect(() => {
    const diagnosticId = payload?.run?.diagnostic_id;
    if (diagnosticId == null) return;
    getDiagnosticRunHistoryV2(itemId, diagnosticId).then((history) => setRunHistory(history.runs || [])).catch(() => setRunHistory([]));
  }, [itemId, payload?.run?.diagnostic_id]);
  const disposition = async (findingId, action, reason) => {
    await dispositionFindingV2(findingId, { action, reason });
    await reload();
  };
  const promote = async (resultId, reason, overwriteExisting = false) => {
    await promoteDiagnosticResultV2(resultId, reason, overwriteExisting);
    await reload();
  };
  const closeIssue = async (issueRowId, reason) => {
    await closeIssueV2(issueRowId, reason);
    await reload();
  };
  return <div className="grid gap-4">
    {runHistory.length > 1 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div><p className="text-xs font-semibold text-slate-800">Saved diagnostic runs</p><p className="text-[11px] text-slate-500">Viewing one immutable run; select another to review its original results and decisions.</p></div>
      <select className="h-9 max-w-full rounded-md border border-slate-200 bg-white px-3 text-xs" value={runId}
        onChange={(event) => onSelectRun(event.target.value, payload.run.diagnostic_id)}>
        {runHistory.map((run) => <option key={run.run_id} value={run.run_id}>{run.status.toUpperCase()} · {new Date(run.finished_at || run.created_at).toLocaleString()} · {run.run_id}</option>)}
      </select>
    </div>}
    {payload?.manifest?.manifest_kind === "population_stability_index" && <PsiJourneySummary manifest={payload.manifest} />}
    <h2 className="text-lg font-semibold">Findings</h2>
    <FindingsPanel results={payload?.results || []} loading={!payload && !error} error={error}
      onDisposition={disposition} onPromote={promote} onCloseIssue={closeIssue} onRecompute={() => {}} />
  </div>;
}

// The Run pane hosts the scope gate until the manifest is frozen, then the
// SSE run console — same tab, always re-enterable (testlab-redesign-0.4.0.md
// §3's three-pane diagram: RUN = "scope gate -> execute").
function RunTab({ runId, onRunStarted, onDone }) {
  const [running, setRunning] = useState(false);
  return running
    ? <RunConsole runId={runId} onDone={onDone} />
    : <ScopeGate runId={runId} onRunStarted={() => { setRunning(true); onRunStarted(); }} />;
}

