import { useState } from "react";
import { CheckCircle2, CircleDot, Clock, Eye, History, Lock, MinusCircle, RotateCw } from "lucide-react";

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

function StatusChip({ chip }) {
  const style = CHIP_STYLE[chip.status] || { variant: "outline", Icon: CircleDot, label: chip.status };
  const { Icon } = style;
  return (
    <div data-testid="chip-status" data-status={chip.status}>
      <Badge variant={style.variant} className="gap-1">
        <Icon className="h-3 w-3" /> {style.label}
      </Badge>
      {chip.reason && <p className="mt-1 text-xs text-slate-500">{chip.reason}</p>}
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

function DiagnosticCard({ card, itemId, onOpenScope, onViewRun }) {
  const hasResults = Boolean(card.last_run);
  const activeRun = (card.recent_runs || []).find((run) => run.status === "running");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState(card.recent_runs || []);
  const [historyError, setHistoryError] = useState("");
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
    <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-4"
      data-testid="diagnostic-card" data-diagnostic-id={card.diagnostic_id} data-chip-status={card.chip.status}>
      <div className="flex items-start justify-between gap-2">
        <h4 className="font-semibold text-slate-950">{card.name}</h4>
        <span className="shrink-0 text-xs font-medium text-slate-400">#{card.diagnostic_id}</span>
      </div>
      <p className="text-xs text-slate-500">
        {card.mode} · Stage: {card.stage} · {card.det_stat} · Decision: {card.decision_type}
      </p>
      <p className="text-xs text-slate-500">KB dependency: {card.kb_dependency}</p>
      <StatusChip chip={card.chip} />
      {card.run_count > 0 && <div className="flex flex-wrap gap-1 text-[10px] text-slate-500">
        <span className="rounded-full bg-slate-100 px-2 py-1 font-medium">{card.run_count} run{card.run_count === 1 ? "" : "s"}</span>
        {(card.run_counts?.done || 0) > 0 && <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">{card.run_counts.done} completed</span>}
        {(card.run_counts?.draft || 0) > 0 && <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">{card.run_counts.draft} draft</span>}
        {(card.run_counts?.running || 0) > 0 && <span className="rounded-full bg-blue-50 px-2 py-1 text-blue-700">{card.run_counts.running} running</span>}
        {(card.run_counts?.failed || 0) > 0 && <span className="rounded-full bg-red-50 px-2 py-1 text-red-700">{card.run_counts.failed} failed</span>}
      </div>}
      {hasResults && <div className="mt-1 rounded-md border border-emerald-100 bg-emerald-50/60 p-3" data-testid="available-results">
        <p className="text-sm font-semibold text-slate-900">Results available</p>
        <p className="mt-0.5 text-xs text-slate-500">
          Completed {new Date(card.last_run.finished_at).toLocaleString()}
        </p>
        <p className="mt-2 text-sm font-medium text-slate-700">{rollupText(card.last_run.rollup)}</p>
      </div>}
      <div className="mt-auto flex flex-wrap gap-2 pt-1">
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
      {historyOpen && <div className="mt-1 grid gap-2 border-t border-slate-100 pt-3" data-testid="diagnostic-run-history">
        {history.map((run, index) => <div key={run.run_id} className="rounded-md border border-slate-200 bg-slate-50 p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2"><RunStatus status={run.status} />{index === 0 && <span className="text-[10px] font-medium text-slate-400">MOST RECENT</span>}<span className="ml-auto text-[10px] text-slate-400">{new Date(run.finished_at || run.created_at).toLocaleString()}</span></div>
          <p className="mt-1 truncate font-mono text-[10px] text-slate-500" title={run.run_id}>{run.run_id}</p>
          {run.rollup && <p className="mt-1 text-[11px] font-medium text-slate-700">{rollupText(run.rollup)}</p>}
          {(run.status === "running" || run.has_results) && <Button size="sm" variant="outline" className="mt-2" onClick={() => onViewRun(run.run_id, card.diagnostic_id, run.status)}><Eye className="h-3.5 w-3.5" /> {run.status === "running" ? "View progress" : `View ${run.status === "done" ? "results" : "available evidence"}`}</Button>}
        </div>)}
        {!history.length && !historyError && <p className="text-xs text-slate-500">No saved runs.</p>}
        {historyError && <p className="text-xs text-red-600">{historyError}</p>}
      </div>}
    </div>
  );
}

export default function CoverageBoard({ board, loading, error, onOpenScope, onViewRun }) {
  if (loading) return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading coverage board…</div>;
  if (error) return <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-700">{error}</div>;
  if (!board) return null;

  const areas = [...new Set(board.cards.map((c) => c.area))].sort();
  const gapAreas = board.gap_areas || [];

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
        <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
          {board.coverage.executable.length} executable
        </span>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
          {board.coverage.workflow_pending.length} workflow pending
        </span>
        {board.item?.use_case && <span>Use case: {board.item.use_case}</span>}
      </div>

      {areas.map((areaId) => {
        const cards = board.cards.filter((c) => c.area === areaId);
        return (
          <section key={areaId}>
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
              {areaId} · {cards[0]?.area_name || areaId}
            </h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {cards.map((card) => (
                <DiagnosticCard key={card.diagnostic_id} card={card} itemId={board.item_id} onOpenScope={onOpenScope} onViewRun={onViewRun} />
              ))}
            </div>
          </section>
        );
      })}

      {gapAreas.length > 0 && (
        <section className="rounded-lg border border-dashed border-slate-300 bg-slate-50/60 p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-700">GAP by design — L2 areas no registered diagnostic reaches</h3>
          <ul className="grid gap-1.5 sm:grid-cols-2">
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
