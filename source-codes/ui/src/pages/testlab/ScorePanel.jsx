import { useEffect, useState } from "react";
import { Download, FileText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { diagnosticReportUrlV2, downloadDiagnosticReportV2, getDiagnosticsCoverageSummaryV2 } from "@/api/client";

// testlab-redesign-0.4.0.md §3 Step 5 — score & report, coverage-honest
// (FWK-16). 6-T16: no weighted/synthesized health-score number anywhere here
// — the API payload has no such field at all (never a zero, never a null to
// render as one), so the only numbers on this panel are the literal
// verdict_rollup counts and the executable/pending split. `coverage_statement`
// (server-authored) is rendered verbatim as the honest summary text.

function Stat({ label, value, variant }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50/60 px-3 py-2 text-center">
      <div className="text-2xl font-bold text-slate-900">{value ?? 0}</div>
      <div className="mt-0.5 text-[11px] font-medium uppercase text-slate-500">{label}</div>
      {variant && <div className="mt-1"><Badge variant={variant}>{label}</Badge></div>}
    </div>
  );
}

export default function ScorePanel({ itemId, runId }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const [reportError, setReportError] = useState("");

  useEffect(() => {
    if (!itemId) return;
    getDiagnosticsCoverageSummaryV2(itemId).then(setSummary).catch((e) => setError(e.message));
  }, [itemId]);

  if (error) return <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>;
  if (!summary) return null;

  const rollup = summary.verdict_rollup || {};

  return (
    <section data-testid="score-panel" className="rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="font-semibold text-slate-950">Score</h2>
      <p className="mt-1 text-sm text-slate-700">{summary.coverage_statement}</p>

      <div className="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-5">
        <Stat label="PASS" value={rollup.PASS} />
        <Stat label="VIOLATION" value={rollup.VIOLATION} />
        <Stat label="NOT-APPLICABLE" value={rollup["NOT-APPLICABLE"]} />
        <Stat label="executable" value={summary.executable_total} />
        <Stat label="workflow pending" value={summary.registered_total - summary.executable_total} />
      </div>

      {summary.workflow_pending?.length > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          Pending: {summary.workflow_pending.map((d) => `${d.name} (#${d.diagnostic_id})`).join(", ")}
        </p>
      )}
      {summary.not_run?.length > 0 && (
        <p className="mt-1 text-xs text-slate-500">
          Executable but not yet run: {summary.not_run.map((d) => `${d.name} (#${d.diagnostic_id})`).join(", ")}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
        <Button size="sm" variant="outline" disabled={!runId}
          onClick={async () => {
            setReportError("");
            try {
              const blob = await downloadDiagnosticReportV2(runId);
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `cross-field-${runId}.pdf`;
              a.click();
              URL.revokeObjectURL(url);
            } catch (e) {
              setReportError(e.message);
            }
          }}>
          <Download className="h-4 w-4" /> Download PDF report
        </Button>
        <Button size="sm" variant="outline" disabled={!runId}
          onClick={() => window.open(diagnosticReportUrlV2(runId, "text"), "_blank")}>
          <FileText className="h-4 w-4" /> View text report
        </Button>
        {!runId && <span className="text-xs text-slate-400">Run diagnostic #4 to enable the report.</span>}
        {reportError && <span className="text-xs text-red-600">{reportError}</span>}
      </div>
    </section>
  );
}
