import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronDown, FlaskConical, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  createAnalysisManifestV2,
  dispositionAnalysisObservationV2,
  getAnalysisCatalogV2,
  getAnalysisResultsV2,
  runAnalysisManifestV2,
} from "@/api/client";

const CAPABILITY_ID = "missingness_explanation";

export default function SupportingInvestigations({ itemId }) {
  const [catalog, setCatalog] = useState(null);
  const [table, setTable] = useState("");
  const [periodColumn, setPeriodColumn] = useState("");
  const [defaultTolerance, setDefaultTolerance] = useState(5);
  const [treeDepth, setTreeDepth] = useState(3);
  const [run, setRun] = useState(null);
  const [investigationOpen, setInvestigationOpen] = useState(false);
  const [latestRunOpen, setLatestRunOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const capability = useMemo(
    () => catalog?.capabilities?.find((row) => row.capability_id === CAPABILITY_ID),
    [catalog],
  );
  const tableNames = catalog?.snapshot?.tables || [];

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getAnalysisCatalogV2(itemId),
      getAnalysisResultsV2(itemId, CAPABILITY_ID),
    ]).then(([nextCatalog, history]) => {
      if (cancelled) return;
      setCatalog(nextCatalog);
      setTable(nextCatalog.snapshot?.tables?.[0] || "");
      setRun(history.runs?.[0] || null);
    }).catch((nextError) => !cancelled && setError(nextError.message));
    return () => { cancelled = true; };
  }, [itemId]);

  const execute = async () => {
    setBusy(true);
    setError("");
    try {
      const manifest = await createAnalysisManifestV2(itemId, {
        capability_id: CAPABILITY_ID,
        table,
        period_column: periodColumn || null,
        parameters: { tolerance_default: defaultTolerance / 100, tree_depth: Number(treeDepth) },
      });
      setRun(await runAnalysisManifestV2(manifest.run.run_id));
      setLatestRunOpen(true);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  const dispose = async (observation, action) => {
    let reason = null;
    if (action === "dismiss") {
      reason = window.prompt("Why should this observation be dismissed?");
      if (!reason?.trim()) return;
    }
    try {
      await dispositionAnalysisObservationV2(observation.observation_id, { action, reason });
      const history = await getAnalysisResultsV2(itemId, CAPABILITY_ID);
      setRun(history.runs?.find((row) => row.run.run_id === run.run.run_id) || history.runs?.[0] || null);
    } catch (nextError) {
      setError(nextError.message);
    }
  };

  if (!catalog) {
    return <div className={`rounded-lg border bg-white p-5 text-sm ${error ? "border-red-200 text-red-700" : "border-slate-200 text-slate-500"}`}>
      {error || "Loading supporting investigations…"}
    </div>;
  }
  if (!capability) return null;

  const observations = run?.result?.observations || [];
  const metrics = run?.result?.summary?.metrics;
  const status = run?.run?.status?.replaceAll("_", " ");

  return (
    <section data-testid="supporting-investigations">
      <div className="mb-2 flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-indigo-600" />
        <h2 className="font-semibold text-slate-950">Supporting investigations</h2>
      </div>

      <article className="overflow-hidden rounded-lg border border-indigo-200 bg-white">
        <button
          type="button"
          onClick={() => setInvestigationOpen((current) => !current)}
          className="flex w-full items-start justify-between gap-4 p-5 text-left hover:bg-slate-50"
          aria-expanded={investigationOpen}
          aria-controls="missingness-investigation-body"
        >
          <div>
            <h3 className="font-semibold text-slate-950">{capability.name}</h3>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">{capability.description}</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="hidden rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 sm:inline">Supporting evidence · review required</span>
            <ChevronDown className={`h-5 w-5 text-slate-500 transition-transform ${investigationOpen ? "rotate-180" : ""}`} />
          </div>
        </button>

        {investigationOpen && (
          <div id="missingness-investigation-body" className="border-t border-indigo-100 p-5">
            <p className="flex items-start gap-1.5 text-xs text-amber-700">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {capability.caveat}
            </p>

            <div className="mt-4 grid gap-3 rounded-md bg-slate-50 p-4 sm:grid-cols-2 lg:grid-cols-5">
              <label className="text-xs font-medium text-slate-600">Table
                <select value={table} onChange={(event) => { setTable(event.target.value); setPeriodColumn(""); }}
                  className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm">
                  {tableNames.map((name) => <option key={name}>{name}</option>)}
                </select>
              </label>
              <label className="text-xs font-medium text-slate-600">Default tolerance (%)
                <input type="number" min="0" max="100" step="0.5" value={defaultTolerance}
                  onChange={(event) => setDefaultTolerance(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm" />
              </label>
              <label className="text-xs font-medium text-slate-600">Tree depth
                <select value={treeDepth} onChange={(event) => setTreeDepth(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm">
                  {[2, 3, 4, 5].map((value) => <option key={value}>{value}</option>)}
                </select>
              </label>
              <label className="text-xs font-medium text-slate-600">Period column (optional)
                <input value={periodColumn} onChange={(event) => setPeriodColumn(event.target.value)}
                  placeholder="e.g. reporting_date"
                  className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm" />
              </label>
              <div className="flex items-end"><Button className="w-full" disabled={busy || !table} onClick={execute}>
                {busy ? "Running…" : "Run investigation"}
              </Button></div>
            </div>

            {error && <p className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
            {run?.run && (
              <div className="mt-4 overflow-hidden rounded-md border border-slate-200">
                <button
                  type="button"
                  onClick={() => setLatestRunOpen((current) => !current)}
                  className="flex w-full items-center justify-between gap-3 bg-slate-50 px-4 py-3 text-left hover:bg-slate-100"
                  aria-expanded={latestRunOpen}
                  aria-controls="missingness-latest-run"
                >
                  <span className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-slate-800">Latest run: {status}</span>
                    {metrics && <span className="text-slate-500">{metrics.breached_fields} above tolerance · {metrics.shared_blocks} shared block(s)</span>}
                  </span>
                  <ChevronDown className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${latestRunOpen ? "rotate-180" : ""}`} />
                </button>

                {latestRunOpen && observations.length > 0 && (
                  <div id="missingness-latest-run" className="overflow-x-auto border-t border-slate-200">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-white text-slate-500"><tr>
                        <th className="px-3 py-2">Column</th><th className="px-3 py-2">Missing</th>
                        <th className="px-3 py-2">Observation</th><th className="px-3 py-2">Review</th>
                      </tr></thead>
                      <tbody>{observations.map((observation) => (
                        <tr key={observation.observation_id || observation.column} className="border-t border-slate-100 align-top">
                          <td className="px-3 py-2 font-medium text-slate-800">{observation.column}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-slate-600">{(100 * observation.missing_share).toFixed(1)}% / {(100 * observation.tolerance).toFixed(1)}%</td>
                          <td className="max-w-xl px-3 py-2 text-slate-600"><span className="font-medium text-slate-800">{observation.classification}</span><br />{observation.rationale}</td>
                          <td className="px-3 py-2">
                            {observation.review_state === "open" ? <div className="flex gap-1">
                              <Button size="sm" variant="outline" onClick={() => dispose(observation, "confirm_issue")}><Check /> Confirm issue</Button>
                              <Button size="sm" variant="ghost" onClick={() => dispose(observation, "dismiss")}><X /> Dismiss</Button>
                            </div> : <span className="capitalize text-slate-500">{observation.review_state.replaceAll("_", " ")}{observation.issue_row_id ? ` · ${observation.issue_row_id}` : ""}</span>}
                          </td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}
                {latestRunOpen && observations.length === 0 && (
                  <p id="missingness-latest-run" className="border-t border-slate-200 p-4 text-sm text-slate-500">This run has no column observations.</p>
                )}
              </div>
            )}
          </div>
        )}
      </article>
    </section>
  );
}
