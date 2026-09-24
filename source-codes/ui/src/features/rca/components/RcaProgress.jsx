import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { getRcaProgress } from "../../../api/client";

// Mounted only for a pending action. Sequential polling avoids overlapping reads.
export default function RcaProgress({ caseId, generation = 1, afterSequence = 0, operation, label = "Preparing RCA analysis", showOpening = false, pollEnabled = true }) {
  const [view, setView] = useState(null);
  const [seconds, setSeconds] = useState(0);
  const [unavailable, setUnavailable] = useState(false);
  const [superseded, setSuperseded] = useState(false);
  useEffect(() => {
    const started = Date.now();
    const controller = new AbortController();
    let timer;
    let stopped = false;
    const tick = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    const poll = async () => {
      try {
        const next = await getRcaProgress(caseId, controller.signal);
        if (stopped) return;
        if (next.workflow_generation !== generation) {
          setView(null);
          setSuperseded(true);
          stopped = true;
          clearInterval(tick);
          return;
        }
        setUnavailable(false);
        if (next.operation?.sequence_no > afterSequence && (!operation || next.operation.operation === operation)) setView(next);
      } catch (error) {
        if (stopped || error.name === "AbortError") return;
        setUnavailable(true);
      }
      if (!stopped) timer = setTimeout(poll, 1500);
    };
    if (pollEnabled) poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); clearInterval(tick); };
  }, [caseId, generation, afterSequence, operation, pollEnabled]);
  const active = view?.operation;
  const phase = active?.status === "running" ? active.phase : active?.status === "failed" ? "Analysis stopped; receiving details" : "Receiving results";
  return <div className="rounded-md border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-950" data-testid="rca-progress">
    <div className="flex items-center gap-2" role="status"><LoaderCircle aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" /><span>{superseded ? "This RCA was started afresh. Previous progress is no longer current." : unavailable ? "Progress updates unavailable; waiting for the request." : active ? phase : label}</span><span className="ml-auto whitespace-nowrap text-xs tabular-nums">{seconds}s elapsed</span></div>
    {showOpening && view?.opening_result && <div className="mt-2 border-t border-teal-200 pt-2 text-xs" data-testid="rca-opening-preview"><strong>Opening evidence is ready.</strong><p>Interpretation and proposed hypotheses are still being prepared.</p><details className="mt-1"><summary className="cursor-pointer">View retained findings</summary><OpeningPreview result={view.opening_result} /></details></div>}
  </div>;
}

function OpeningPreview({ result }) {
  const profile = result.profile || {};
  const values = [["Rows", profile.total_count], ["Distinct values", result.distinct], ["Mean", result.mean]];
  return <div className="mt-1 space-y-1"><p>{result.summary || result.profile_population_scope || "Retained diagnostic evidence reviewed."}</p>{values.filter(([, value]) => typeof value === "number").map(([name, value]) => <p key={name}>{name}: {value.toLocaleString()}</p>)}{typeof result.null_share === "number" && <p>Physical missing: {(result.null_share * 100).toFixed(2)}%</p>}{result.limitations?.length > 0 && <p>{result.limitations.join(" ")}</p>}</div>;
}
