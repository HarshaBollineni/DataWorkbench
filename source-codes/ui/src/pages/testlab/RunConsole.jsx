import { useEffect, useMemo, useRef } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import AgentConsole from "@/components/AgentConsole";
import { diagnosticRunStreamUrlV2 } from "@/api/client";
import { useAgentStream } from "./stream";

const FEATURE_STAGES = [
  ["initialize", "Initialize"],
  ["roc", "ROC / AUC / Gini"],
  ["binning", "IV / WOE binning"],
  ["persist", "Persist results"],
  ["complete", "Complete"],
];

const RULE_STAGES = [
  ["initialize", "Initialize"],
  ["rules", "Evaluate rules"],
  ["persist", "Persist results"],
  ["complete", "Complete"],
];

const ROW_COMPLETENESS_STAGES = [
  ["initialize", "Freeze configuration"],
  ["rules", "Evaluate 6 rules"],
  ["persist", "Persist artifact and issues"],
  ["complete", "Complete"],
];

function useProgressState(events, running) {
  return useMemo(() => {
    const prog = [...events].reverse().find((event) => event.phase === "progress");
    const start = events.find((event) => event.phase === "start");
    const doneEvent = events.find((event) => event.phase === "done");
    const errorEvent = events.find((event) => event.phase === "error");
    const total = Number(prog?.total || start?.total || 0);
    const completed = Number(prog?.done || (doneEvent ? total : 0));
    const percent = doneEvent ? 100 : total ? Math.round((completed / total) * 100) : 0;
    const featureRun = events.some((event) => event.agent === "feature_target_separation_engine");
    const rowCompletenessRun = events.some((event) => event.agent === "row_completeness_engine");
    const stages = featureRun ? FEATURE_STAGES : rowCompletenessRun ? ROW_COMPLETENESS_STAGES : RULE_STAGES;
    let activeKey = "initialize";
    if (doneEvent) activeKey = "complete";
    else if (prog?.stage === "roc") activeKey = "roc";
    else if (prog?.stage === "binning") activeKey = "binning";
    else if (prog) activeKey = "rules";
    if (running && total > 0 && completed >= total) activeKey = "persist";
    return { prog, total, completed, percent, stages, activeKey, doneEvent, errorEvent };
  }, [events, running]);
}

function ProgressBar({ state, running }) {
  if (!running && !state.prog && !state.doneEvent) return null;
  const message = state.doneEvent ? "Diagnostic complete" : state.activeKey === "persist"
    ? "Persisting results and findings..." : state.prog?.thought || "Preparing diagnostic scope...";
  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <span className="flex items-center gap-2 text-sm text-slate-600">
          <Loader2 className={`h-4 w-4 text-dq-purple ${running ? "animate-spin" : ""}`} />
          {message}
        </span>
        <span className="flex items-baseline gap-3">
          <span className="text-xs font-medium text-slate-500">
            {state.total ? `${state.completed}/${state.total}` : "Starting"}
          </span>
          <strong className="text-2xl text-dq-purple">{state.percent}%</strong>
        </span>
      </div>
      <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full transition-all duration-300 ${state.errorEvent ? "bg-red-500" : "bg-dq-purple"}`}
          style={{ width: `${Math.max(running ? 3 : 0, state.percent)}%` }} />
      </div>
    </div>
  );
}

function ProgressPills({ state }) {
  const activeIndex = state.stages.findIndex(([key]) => key === state.activeKey);
  return (
    <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-3" data-testid="diagnostic-progress-stages">
      {state.stages.map(([key, label], index) => {
        const complete = Boolean(state.doneEvent) || index < activeIndex;
        const active = !state.doneEvent && index === activeIndex;
        return (
          <span key={key} className={`rounded-full border px-3 py-1 text-xs ${complete
            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
            : active
              ? `${state.errorEvent ? "border-red-300 text-red-700" : "border-dq-purple text-dq-purple"} bg-white font-semibold`
              : "border-slate-200 bg-slate-50 text-slate-500"}`}>
            {label}
          </span>
        );
      })}
    </div>
  );
}

const metric = (value, digits = 4) => value == null ? "—" : Number(value).toFixed(digits);

function ProgressiveFeatureResults({ events, done }) {
  const previews = useMemo(() => {
    const ordered = new Map();
    events.forEach((event) => {
      const preview = event.preview;
      if (preview?.feature) ordered.set(preview.feature, preview);
    });
    return [...ordered.values()];
  }, [events]);
  if (!previews.length) return null;
  const isPsi = previews.some((row) => row.psi != null || row.classification);
  return <section className="overflow-hidden rounded-lg border border-slate-200 bg-white" data-testid="progressive-feature-results">
    <header className="border-b border-slate-200 bg-slate-50 px-4 py-3">
      <h3 className="text-sm font-semibold text-slate-900">Completed variable results · {previews.length}</h3>
      <p className="mt-1 text-xs text-slate-500">{done
        ? "The run is finalized; opening findings loads the authoritative persisted results."
        : "Preliminary and read-only until every selected variable finishes. Editing, approval and promotion remain unavailable."}</p>
    </header>
    <div className="overflow-x-auto"><table className="w-full text-left text-xs">
      <thead className="bg-white text-slate-500"><tr><th className="px-4 py-2">Variable</th><th className="px-3 py-2">Status</th>{isPsi
        ? <><th className="px-3 py-2 text-right">PSI</th><th className="px-3 py-2">Classification</th><th className="px-3 py-2 text-right">Bins</th></>
        : <><th className="px-3 py-2 text-right">AUC</th><th className="px-3 py-2 text-right">Gini</th><th className="px-3 py-2 text-right">IV</th><th className="px-3 py-2">Category</th><th className="px-3 py-2 text-right">Fine / coarse</th></>}</tr></thead>
      <tbody>{previews.map((row) => <tr key={row.feature} className="border-t border-slate-100">
        <td className="px-4 py-2 font-medium text-slate-900">{row.feature}</td>
        <td className={`px-3 py-2 ${row.status === "failed" ? "text-red-700" : row.status === "roc_complete" ? "text-amber-700" : "text-emerald-700"}`}>{row.status === "roc_complete" ? "ROC complete · IV pending" : row.status}{row.error ? `: ${row.error}` : ""}</td>
        {isPsi ? <><td className="px-3 py-2 text-right font-mono">{metric(row.psi)}</td><td className="px-3 py-2">{row.classification || "—"}</td><td className="px-3 py-2 text-right">{row.bin_count ?? "—"}</td></>
          : <><td className="px-3 py-2 text-right font-mono">{metric(row.auc)}</td><td className="px-3 py-2 text-right font-mono">{metric(row.gini)}</td><td className="px-3 py-2 text-right font-mono">{metric(row.iv)}</td><td className="px-3 py-2">{row.category || "—"}</td><td className="px-3 py-2 text-right">{row.fine_bin_count ?? "—"} / {row.coarse_bin_count ?? "—"}</td></>}
      </tr>)}</tbody>
    </table></div>
  </section>;
}

export default function RunConsole({ runId, onDone }) {
  const stream = useAgentStream();
  const started = useRef(null);

  useEffect(() => {
    if (started.current === runId) return undefined;
    started.current = runId;
    stream.run(diagnosticRunStreamUrlV2(runId), (event) => {
      if (event.phase === "done") onDone?.(event);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const done = stream.events.find((event) => event.phase === "done");
  const errored = stream.events.find((event) => event.phase === "error");
  const progress = useProgressState(stream.events, stream.running);

  return (
    <div className="grid gap-4">
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="font-semibold text-slate-950">Run console</h2>
        <p className="text-xs text-slate-500">Read-only against the dataset; deterministic; nothing here is user-editable code.</p>
        <div className="mt-3">
          <ProgressBar state={progress} running={stream.running} />
          <AgentConsole events={stream.events} running={stream.running} />
          <ProgressPills state={progress} />
        </div>
      </div>

      <ProgressiveFeatureResults events={stream.events} done={done} />

      {errored && (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4" /> {errored.thought || "The run failed."}
        </div>
      )}

      {done && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4">
          <div className="flex items-center gap-2 text-sm text-emerald-900">
            <CheckCircle2 className="h-4 w-4" />
            Run complete - {done.rollup?.PASS || 0} PASS · {done.rollup?.VIOLATION || 0} VIOLATION · {done.rollup?.["NOT-APPLICABLE"] || 0} NOT-APPLICABLE
            {done.rollup?.["NOT-ASSESSABLE"] ? ` · ${done.rollup["NOT-ASSESSABLE"]} NOT-ASSESSABLE` : ""}
            {done.issues?.created?.length ? ` · ${done.issues.created.length} issue(s) opened` : ""}
          </div>
          <Button size="sm" onClick={() => onDone?.(done)}>
            View findings <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
