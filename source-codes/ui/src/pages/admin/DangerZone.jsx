import { useCallback, useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { factoryReset, getDevelopmentArtifactCandidates, getDiagnosticRuns } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { summarizeReset } from "@/lib/resetLabels";

function ResetAction({ grade, phrase, title, description, actionLabel, completeLabel, legacyAssetIds = [], legacyRunIds = [], diagnostics = {}, ready = true, onComplete }) {
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const run = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      const response = await factoryReset(grade, confirmText, legacyAssetIds, legacyRunIds, diagnostics);
      setResult(response); setConfirmText(""); onComplete?.(response);
    }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return <div className="space-y-3 rounded-lg border border-red-200 bg-white p-4">
    <h3 className="text-sm font-semibold text-slate-800">{title}</h3><p className="text-sm text-slate-600">{description}</p>
    {error && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}
    {result && <div className="rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">{completeLabel} {summarizeReset(result.deleted).sentence}</div>}
    <div className="flex items-end gap-3"><div className="space-y-1.5"><Label htmlFor={`${grade}-confirm-input`} className="text-xs font-semibold text-slate-600">Type {phrase} to confirm</Label><Input id={`${grade}-confirm-input`} value={confirmText} onChange={(event) => setConfirmText(event.target.value)} placeholder={phrase} className="max-w-xs" /></div><Button variant="destructive" disabled={confirmText !== phrase || busy || !ready} onClick={run}>{busy ? (grade === "wipe" ? "Wiping…" : "Resetting…") : actionLabel}</Button></div>
  </div>;
}

function DevelopmentResetAction() {
  const [assets, setAssets] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedAssets, setSelectedAssets] = useState([]);
  const [selectedRuns, setSelectedRuns] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const load = useCallback(() => getDevelopmentArtifactCandidates()
    .then((response) => {
      setAssets(response.assets || []); setRuns(response.runs || []);
      setSelectedAssets([]); setSelectedRuns([]); setLoadError(null);
    })
    .catch((reason) => setLoadError(reason.message)), []);
  useEffect(() => { load(); }, [load]);
  const legacyAssets = assets.filter((asset) => asset.artifact_origin === "unclassified");
  const legacyRuns = runs.filter((run) => run.artifact_origin === "unclassified");
  const markedAssets = assets.filter((asset) => asset.artifact_origin === "development").length;
  const markedRuns = runs.filter((run) => run.artifact_origin === "development").length;
  const toggleAsset = (assetId) => setSelectedAssets((current) => current.includes(assetId)
    ? current.filter((id) => id !== assetId)
    : [...current, assetId]);
  const toggleRun = (runId) => setSelectedRuns((current) => current.includes(runId)
    ? current.filter((id) => id !== runId)
    : [...current, runId]);

  return <div className="space-y-3">
    {(markedAssets > 0 || markedRuns > 0 || legacyAssets.length > 0 || legacyRuns.length > 0) && <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-slate-700">
      <p className="font-semibold">Development cleanup review</p>
      {(markedAssets > 0 || markedRuns > 0) && <p className="mt-1">Ready to remove automatically: {markedAssets} development {markedAssets === 1 ? "asset" : "assets"} and {markedRuns} development {markedRuns === 1 ? "run" : "runs"}.</p>}
      {legacyAssets.length > 0 && <><p className="mt-3 font-medium">Legacy assets</p><p className="mt-1">Select only known backend fixtures; leave genuine user uploads unchecked.</p>
        <div className="mt-2 max-h-48 space-y-1 overflow-y-auto rounded border border-amber-200 bg-white p-2">
          {legacyAssets.map((asset) => <label key={asset.asset_id} className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 hover:bg-amber-50">
            <input type="checkbox" className="mt-1" checked={selectedAssets.includes(asset.asset_id)} onChange={() => toggleAsset(asset.asset_id)} />
            <span><span className="font-medium">{asset.display_name}</span><span className="ml-2 text-xs text-slate-500">{asset.system_id}</span></span>
          </label>)}
        </div>
      </>}
      {legacyRuns.length > 0 && <><p className="mt-3 font-medium">Legacy diagnostic runs</p><p className="mt-1">Select the development PSI runs to remove. Their source asset remains protected.</p>
        <div className="mt-2 max-h-64 space-y-1 overflow-y-auto rounded border border-amber-200 bg-white p-2">
          {legacyRuns.map((run) => <label key={run.run_id} className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 hover:bg-amber-50">
            <input type="checkbox" className="mt-1" checked={selectedRuns.includes(run.run_id)} onChange={() => toggleRun(run.run_id)} />
            <span><span className="font-medium">{run.run_id}</span><span className="ml-2 text-xs text-slate-500">Diagnostic #{run.diagnostic_id} · {run.status} · {run.asset_name || run.system_id || "Unknown asset"} · {run.created_at || "Unknown time"}</span></span>
          </label>)}
        </div>
      </>}
    </div>}
    {loadError && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">Could not load development cleanup candidates: {loadError}</div>}
    <ResetAction grade="development" phrase="WIPE DEVELOPMENT" title="Wipe development artifacts" actionLabel="Wipe development artifacts" completeLabel="Development cleanup complete." description="Removes provenance-marked development assets and diagnostic runs plus any legacy fixtures selected above. Source assets for development-only runs remain protected." legacyAssetIds={selectedAssets} legacyRunIds={selectedRuns} onComplete={load} />
  </div>;
}

function DiagnosticsResetAction() {
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState([]);
  const [wipeAll, setWipeAll] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const load = useCallback(() => getDiagnosticRuns()
    .then((response) => {
      setRuns(response.runs || []); setSelected([]); setWipeAll(false); setLoadError(null);
    })
    .catch((reason) => setLoadError(reason.message)), []);
  useEffect(() => { load(); }, [load]);
  const toggle = (runId) => setSelected((current) => current.includes(runId)
    ? current.filter((id) => id !== runId)
    : [...current, runId]);

  return <div className="space-y-3 rounded-lg border border-red-200 bg-white p-4">
    <div><h3 className="text-sm font-semibold text-slate-800">Wipe diagnostics</h3>
      <p className="mt-1 text-sm text-slate-600">Remove selected diagnostic runs or reset the whole diagnostics workspace. Data Sourcing assets, uploads, dictionaries, inventory, fingerprints, and sourcing-owned AAR profiles remain protected.</p></div>
    {loadError && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">Could not load diagnostic runs: {loadError}</div>}
    <label className="flex cursor-pointer items-start gap-2 rounded border border-red-200 bg-red-50 p-3 text-sm">
      <input type="checkbox" className="mt-1" checked={wipeAll} onChange={(event) => { setWipeAll(event.target.checked); setSelected([]); }} />
      <span><span className="font-semibold text-red-700">Wipe all diagnostics</span><span className="block text-slate-600">Also removes shared and run-less diagnostic AAR materials such as reusable bin drafts.</span></span>
    </label>
    {!wipeAll && <div>
      <div className="mb-2 flex items-center justify-between"><p className="text-sm font-medium text-slate-700">Or select specific runs</p>{runs.length > 0 && <button type="button" className="text-xs font-medium text-slate-600 underline" onClick={() => setSelected(selected.length === runs.length ? [] : runs.map((run) => run.run_id))}>{selected.length === runs.length ? "Clear selection" : "Select all runs"}</button>}</div>
      <div className="max-h-64 space-y-1 overflow-y-auto rounded border border-slate-200 p-2">
        {runs.length === 0 && <p className="px-2 py-1 text-sm text-slate-500">No diagnostic runs are currently stored.</p>}
        {runs.map((run) => <label key={run.run_id} className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 hover:bg-slate-50">
          <input type="checkbox" className="mt-1" checked={selected.includes(run.run_id)} onChange={() => toggle(run.run_id)} />
          <span className="text-sm"><span className="font-medium">{run.run_id}</span><span className="ml-2 text-xs text-slate-500">Diagnostic #{run.diagnostic_id} · {run.status} · {run.asset_name || run.system_id || "Unknown asset"} · {run.created_at || "Unknown time"}</span></span>
        </label>)}
      </div>
    </div>}
    <ResetAction grade="diagnostics" phrase="WIPE DIAGNOSTICS" title="Confirm diagnostics cleanup" actionLabel={wipeAll ? "Wipe all diagnostics" : `Wipe selected runs (${selected.length})`} completeLabel="Diagnostics cleanup complete." description={wipeAll ? "Every diagnostic run and all diagnostic-owned AAR material will be removed." : "Only selected runs and their run-linked evidence will be removed; shared diagnostic AAR material remains available to surviving runs."} diagnostics={{ runIds: selected, all: wipeAll }} ready={wipeAll || selected.length > 0} onComplete={load} />
  </div>;
}

export default function DangerZone() {
  return <Card className="mt-6 border-2 border-red-300 bg-red-50/40"><CardContent className="space-y-6 p-6">
    <div className="flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-red-600" /><h2 className="text-lg font-bold text-red-700">Danger zone</h2></div>
    <p className="text-sm text-slate-600">Cleanup and factory-reset actions. All are irreversible and enforced server-side — each button stays disabled until you type the exact confirmation phrase.</p>
    <DevelopmentResetAction />
    <DiagnosticsResetAction />
    <ResetAction grade="surgical" phrase="RESET" title="Surgical reset" actionLabel="Surgical reset" completeLabel="Surgical reset complete." description="Removes every assessment item and everything derived from it — test plans, results, scores, issues, RCA cases, item-level tags, and uploaded files. Preserves user accounts, the taxonomy, and the knowledge base." />
    <ResetAction grade="wipe" phrase="WIPE EVERYTHING" title="Full wipe" actionLabel="Full wipe" completeLabel="Full wipe complete." description="Blank slate: everything the surgical reset removes, plus the knowledge base, every ingested data source, and every platform reference table (immediately re-seeded, so the app stays usable). Only user accounts, active sessions, the audit trail, and feature flags survive." />
  </CardContent></Card>;
}
