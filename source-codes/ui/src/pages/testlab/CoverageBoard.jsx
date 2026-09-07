import { useState } from "react";
import { CheckCircle2, CircleDot, Clock, Eye, History, Lock, MinusCircle, RotateCw, Trash2 } from "lucide-react";

import { getDiagnosticRunHistoryV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// testlab-redesign-0.4.0.md §3 Step 1 — the Coverage board is a READING
// surface: the user decides nothing here. Exactly 9 diagnostic cards (D-17),
// grouped under their six test-area headers, plus one area-level GAP strip.
// Every card renders the same 8 fields in the same order, no free text, no
// per-card variation. `can_run` (server-computed) is the ONLY gate for a run
// affordance — false for anything but a `ready` chip, so a workflow-pending
// (or not_applicable / blocked) card renders with zero run-affordance
// elements, never a disabled button (6-T10).

const CHIP_STYLE = {
  ready: { variant: "success", Icon: CheckCircle2, label: "READY" },
  not_applicable: { variant: "secondary", Icon: MinusCircle, label: "NOT APPLICABLE" },
  blocked: { variant: "warning", Icon: Lock, label: "BLOCKED" },
  workflow_pending: { variant: "outline", Icon: Clock, label: "WORKFLOW PENDING" },
};

// This stable register shape lets the complete layout paint in the same
// render as dataset selection. The API remains authoritative for card state.
const BOARD_SKELETON = [
  [2, "T1", "Feature-to-target leakage", "Single-feature target separation"],
  [4, "T2", "KB-driven data validation (rules, distributional & relationship)", "Cross-field business rule"],
  [6, "T2", "KB-driven data validation (rules, distributional & relationship)", "Row-completeness reconciliation"],
  [8, "T2", "KB-driven data validation (rules, distributional & relationship)", "Value-semantics classification"],
  [11, "T2", "KB-driven data validation (rules, distributional & relationship)", "Directional / monotonic consistency (segmented)"],
  [12, "T3", "Target definition & label consistency", "Label-consistency rule"],
  [14, "T4", "Portfolio / population representativeness", "Population Stability Index (PSI)"],
  [17, "T5", "Outcome window / censoring completeness (incl. seasoning/maturity)", "Resolution / maturity rate by vintage"],
  [20, "T6", "KB relationship set (SME generated + AI proposed, SME validated)", "AI-proposal workflow"],
].map(([diagnostic_id, area, area_name, name]) => ({ diagnostic_id, area, area_name, name, loading: true }));

function StatusChip({ chip }) {
  const style = CHIP_STYLE[chip.status] || { variant: "outline", Icon: CircleDot, label: chip.status };
  const { Icon } = style;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5" data-testid="chip-status" data-status={chip.status}>
      <Badge variant={style.variant} className="gap-1">
        <Icon className="h-3 w-3" /> {style.label}
      </Badge>
      {chip.reason && <p className="text-[11px] leading-4 text-slate-500">{chip.reason}</p>}
    </div>
  );
}

function rollupText(rollup) {
  if (!rollup) return "Completed results are ready to review";
  const verdicts = ["PASS", "VIOLATION", "NOT-APPLICABLE"]
    .filter((k) => rollup[k])
    .map((k) => `${rollup[k]} ${k}`)
    .join(" · ");
  if (verdicts) return verdicts;
  if (rollup.completed != null) return `${rollup.completed} completed · ${rollup.contextual_findings || 0} review candidate(s)`;
  if (rollup.features != null) return `${rollup.features} assessed · ${rollup.candidates || 0} candidate(s)`;
  return "Completed results are ready to review";
}

function RunStatus({ status }) {
  const style = status === "done" ? "bg-emerald-100 text-emerald-800"
    : status === "failed" ? "bg-red-100 text-red-800"
      : status === "running" ? "bg-blue-100 text-blue-800"
        : status === "draft" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600";
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${style}`}>{status}</span>;
}

function DiagnosticCard({ card, itemId, onOpenScope, onResumeDraft, onDiscardDraft, discardedRunIds, onViewRun }) {
  const hasResults = Boolean(card.last_run);
  const activeRun = (card.recent_runs || []).find((run) => run.status === "running");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState(card.recent_runs || []);
  const [historyError, setHistoryError] = useState("");
  const visibleHistory = history.filter((run) => !discardedRunIds.includes(run.run_id));
  const toggleHistory = async () => {
    const opening = !historyOpen;
    setHistoryOpen(opening);
    if (!opening) return;
    try {
      const payload = await getDiagnosticRunHistoryV2(itemId, card.diagnostic_id);
      setHistory(payload.runs || []); setHistoryError("");
    } catch (error) { setHistoryError(error.message); }
  };
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-slate-200 bg-white p-3 shadow-sm shadow-slate-200/50"
      data-testid="diagnostic-card" data-diagnostic-id={card.diagnostic_id} data-chip-status={card.chip.status}>
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-sm font-semibold leading-5 text-slate-950">{card.name}</h4>
        <span className="shrink-0 text-[11px] font-medium text-slate-400">#{card.diagnostic_id}</span>
      </div>
      <p className="text-[11px] leading-4 text-slate-500">
        {card.mode} · Stage: {card.stage} · {card.det_stat} · Decision: {card.decision_type} · KB dependency: {card.kb_dependency}
      </p>
      <StatusChip chip={card.chip} />
      {card.run_count > 0 && <div className="flex flex-wrap gap-1 text-[10px] text-slate-500">
        <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium">{card.run_count} run{card.run_count === 1 ? "" : "s"}</span>
        {(card.run_counts?.done || 0) > 0 && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700">{card.run_counts.done} completed</span>}
        {(card.run_counts?.draft || 0) > 0 && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700">{card.run_counts.draft} draft</span>}
        {(card.run_counts?.running || 0) > 0 && <span className="rounded-full bg-blue-50 px-2 py-0.5 text-blue-700">{card.run_counts.running} running</span>}
        {(card.run_counts?.failed || 0) > 0 && <span className="rounded-full bg-red-50 px-2 py-0.5 text-red-700">{card.run_counts.failed} failed</span>}
      </div>}
      {hasResults && <div className="mt-0.5 rounded-md border border-emerald-100 bg-emerald-50/60 px-2.5 py-2" data-testid="available-results">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <p className="text-xs font-semibold text-slate-900">Results available</p>
          <p className="text-[11px] text-slate-500">Completed {new Date(card.last_run.finished_at).toLocaleString()}</p>
        </div>
        <p className="mt-1 text-xs font-medium text-slate-700">{rollupText(card.last_run.rollup)}</p>
      </div>}
      <div className="mt-auto flex flex-wrap gap-1.5 pt-0.5">
        {activeRun && <Button size="sm" className="rounded-full"
          onClick={() => onViewRun(activeRun.run_id, card.diagnostic_id, "running")}>
          <Eye className="h-4 w-4" /> View progress
        </Button>}
        {hasResults && <Button size="sm" className="rounded-full"
          onClick={() => onViewRun(card.last_run.run_id, card.diagnostic_id, "done")}>
          <Eye className="h-4 w-4" /> View results
        </Button>}
        {card.can_run && hasResults && <Button size="sm" variant="outline" className="rounded-full"
          onClick={() => onOpenScope(card.diagnostic_id, card.open_draft)}>
          <RotateCw className="h-4 w-4" /> Re-run
        </Button>}
        {card.can_run && !hasResults && <Button size="sm" className="w-full"
          onClick={() => onOpenScope(card.diagnostic_id, card.open_draft)}>
          Launch workflow
        </Button>}
        {card.run_count > 0 && <Button size="sm" variant="ghost" className="rounded-full" onClick={toggleHistory}>
          <History className="h-4 w-4" /> {historyOpen ? "Hide history" : "Run history"}
        </Button>}
      </div>
      {historyOpen && <div className="mt-0.5 grid gap-1.5 border-t border-slate-100 pt-2" data-testid="diagnostic-run-history">
        {visibleHistory.map((run, index) => <div key={run.run_id} className="rounded-md border border-slate-200 bg-slate-50 p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2"><RunStatus status={run.status} />{index === 0 && <span className="text-[10px] font-medium text-slate-400">MOST RECENT</span>}<span className="ml-auto text-[10px] text-slate-400">{new Date(run.finished_at || run.created_at).toLocaleString()}</span></div>
          <p className="mt-1 truncate font-mono text-[10px] text-slate-500" title={run.run_id}>{run.run_id}</p>
          {run.status_detail?.message && run.status !== "running" && <p className="mt-1 text-[11px] text-slate-600">{run.status_detail.message}</p>}
          {run.rollup && <p className="mt-1 text-[11px] font-medium text-slate-700">{rollupText(run.rollup)}</p>}
          {(run.status === "running" || run.has_results) && <Button size="sm" variant="outline" className="mt-2" onClick={() => onViewRun(run.run_id, card.diagnostic_id, run.status)}><Eye className="h-3.5 w-3.5" /> {run.status === "running" ? "View progress" : `View ${run.status === "done" ? "results" : "available evidence"}`}</Button>}
          {run.status === "draft" && card.can_run && <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => onResumeDraft(run.run_id, card.diagnostic_id)}><History className="h-3.5 w-3.5" /> Continue setup</Button>
            <Button size="sm" variant="outline" onClick={() => onOpenScope(card.diagnostic_id, { ...run, last_saved_at: run.created_at })}><RotateCw className="h-3.5 w-3.5" /> Start new…</Button>
            <Button size="sm" variant="ghost" className="text-red-700 hover:text-red-800" onClick={() => onDiscardDraft(run.run_id, card.diagnostic_id)}><Trash2 className="h-3.5 w-3.5" /> Discard draft</Button>
          </div>}
        </div>)}
        {!visibleHistory.length && !historyError && <p className="text-xs text-slate-500">No saved runs.</p>}
        {historyError && <p className="text-xs text-red-600">{historyError}</p>}
      </div>}
    </div>
  );
}

function DiagnosticCardSkeleton({ card }) {
  return <div className="flex min-h-32 flex-col gap-2 rounded-lg border border-slate-200 bg-white p-3 shadow-sm shadow-slate-200/50"
    data-testid="diagnostic-card" data-diagnostic-id={card.diagnostic_id} data-chip-status="loading" aria-busy="true">
    <div className="flex items-start justify-between gap-2">
      <h4 className="text-sm font-semibold leading-5 text-slate-700">{card.name}</h4>
      <span className="shrink-0 text-[11px] font-medium text-slate-400">#{card.diagnostic_id}</span>
    </div>
    <div className="h-3 w-4/5 animate-pulse rounded bg-slate-100" />
    <div className="h-5 w-28 animate-pulse rounded-full bg-slate-100" />
    <div className="mt-auto h-8 w-full animate-pulse rounded-md bg-slate-100" />
    <span className="sr-only">Loading diagnostic status</span>
  </div>;
}

function DiagnosticCardError({ card }) {
  return <div className="flex min-h-32 flex-col gap-2 rounded-lg border border-red-200 bg-white p-3"
    data-testid="diagnostic-card" data-diagnostic-id={card.diagnostic_id} data-chip-status="error">
    <div className="flex items-start justify-between gap-2">
      <h4 className="text-sm font-semibold leading-5 text-slate-900">{card.name}</h4>
      <span className="shrink-0 text-[11px] font-medium text-slate-400">#{card.diagnostic_id}</span>
    </div>
    <p className="text-xs font-medium text-red-700">Diagnostic status is unavailable.</p>
    <p className="text-[11px] leading-4 text-slate-500">Other diagnostics remain available. Use Refresh to retry this card.</p>
  </div>;
}

export default function CoverageBoard({ board, loading, error, onOpenScope, onResumeDraft, onDiscardDraft, discardedRunIds = [], onViewRun }) {
  if (error) return <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-700">{error}</div>;
  const displayBoard = board || (loading ? {
    item_id: "", cards: BOARD_SKELETON, gap_areas: [],
    coverage: { executable: [], workflow_pending: [] },
  } : null);
  if (!displayBoard) return null;

  const areas = [...new Set(displayBoard.cards.map((c) => c.area))].sort();
  const gapAreas = displayBoard.gap_areas || [];
  const resolvedCount = displayBoard.cards.filter((card) => !card.loading).length;
  const isLoading = displayBoard.cards.some((card) => card.loading);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {isLoading && <span className="rounded-full bg-blue-50 px-2.5 py-0.5 font-medium text-blue-700" role="status">
          Loading diagnostics {resolvedCount}/{displayBoard.cards.length}
        </span>}
        {!isLoading && <>
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 font-medium text-slate-700">
            {displayBoard.coverage.executable.length} executable
          </span>
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 font-medium text-slate-700">
            {displayBoard.coverage.workflow_pending.length} workflow pending
          </span>
        </>}
        {displayBoard.item?.use_case && <span>Use case: {displayBoard.item.use_case}</span>}
      </div>

      {areas.map((areaId) => {
        const cards = displayBoard.cards.filter((c) => c.area === areaId);
        return (
          <section key={areaId} className="rounded-xl border border-slate-200 bg-slate-100/70 p-2.5"
            aria-labelledby={`diagnostic-area-${areaId}`} data-testid="diagnostic-area" data-area-id={areaId}>
            <h3 id={`diagnostic-area-${areaId}`} className="mb-2 flex items-center gap-2">
              <span className="inline-flex min-w-9 justify-center rounded-md bg-slate-900 px-2 py-1 text-xs font-bold tracking-wide text-white">
                {areaId}
              </span>
              <span className="text-sm font-semibold text-slate-800">{cards[0]?.area_name || areaId}</span>
            </h3>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {cards.map((card) => card.loading
                ? <DiagnosticCardSkeleton key={card.diagnostic_id} card={card} />
                : card.load_error
                  ? <DiagnosticCardError key={card.diagnostic_id} card={card} />
                  : <DiagnosticCard key={card.diagnostic_id} card={card} itemId={displayBoard.item_id}
                    onOpenScope={onOpenScope} onResumeDraft={onResumeDraft}
                    onDiscardDraft={onDiscardDraft} discardedRunIds={discardedRunIds}
                    onViewRun={onViewRun} />)}
            </div>
          </section>
        );
      })}

      {gapAreas.length > 0 && (
        <section className="rounded-lg border border-dashed border-slate-300 bg-slate-50/60 p-3">
          <h3 className="mb-1.5 text-sm font-semibold text-slate-700">GAP by design — L2 areas no registered diagnostic reaches</h3>
          <ul className="grid gap-1 sm:grid-cols-2">
            {gapAreas.map((a) => (
              <li key={a.l2_id} className="text-xs text-slate-600">
                <span className="font-medium text-slate-800">{a.name}</span>
                {a.l1_theme && <span className="text-slate-400"> ({a.l1_theme})</span>}
                {a.reason && <span> — {a.reason}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
