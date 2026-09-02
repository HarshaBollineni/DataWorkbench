import { useCallback, useEffect, useState } from "react";

import {
  getDiagnosticManifestV2, patchDiagnosticManifestV2, runDiagnosticManifestV2,
} from "@/api/client";
import FeatureTargetScopeGate from "@/features/test-lab/diagnostics/t1-d02-feature-target-separation/FeatureTargetScopeGate";
import CrossFieldScopeGate from "@/features/test-lab/diagnostics/t2-d04-cross-field-business-rule/CrossFieldScopeGate";
import RowCompletenessScopeGate from "@/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessScopeGate";
import PopulationStabilityScopeGate from "@/features/test-lab/diagnostics/t4-d14-population-stability/PopulationStabilityScopeGate";
import DirectionalityScopeGate from "@/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency/DirectionalityScopeGate";

export default function ScopeGate({ runId, onRunStarted }) {
  const [run, setRun] = useState(null);
  const [manifest, setManifest] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => getDiagnosticManifestV2(runId).then((data) => {
    setRun(data.run);
    setManifest(data.manifest);
    setDecisions(data.decisions || []);
    setError("");
  }).catch((requestError) => {
    setError(requestError.message);
    throw requestError;
  }), [runId]);
  useEffect(() => { reload(); }, [reload]);

  if (error && (!manifest || !run)) return <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>;
  if (!manifest || !run) return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading scope gate…</div>;

  const patch = async (body) => {
    setBusy(true);
    try {
      const updated = await patchDiagnosticManifestV2(runId, body);
      if (manifest.manifest_kind === "population_stability_index" && updated?.manifest_kind) {
        setManifest(updated);
        setError("");
      } else {
        await reload();
      }
      return updated;
    } catch (requestError) {
      setError(requestError.message);
      throw requestError;
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    setBusy(true);
    setError("");
    try {
      await runDiagnosticManifestV2(runId, { stream: true });
      onRunStarted(runId);
    } catch (requestError) {
      setError(requestError.message);
      setBusy(false);
    }
  };

  const diagnosticError = error && (
    <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
  );
  if (manifest.manifest_kind === "feature_target_separation") {
    return <div className="grid gap-4">{diagnosticError}<FeatureTargetScopeGate run={run} manifest={manifest} busy={busy} patch={patch} runNow={runNow} /></div>;
  }
  if (manifest.manifest_kind === "population_stability_index") {
    return <div className="grid gap-4">{diagnosticError}<PopulationStabilityScopeGate run={run} manifest={manifest} busy={busy} patch={patch} runNow={runNow} reload={reload} acceptManifest={(updated) => { setManifest(updated); setError(""); }} /></div>;
  }
  if (manifest.manifest_kind === "row_completeness") {
    return <div className="grid gap-4">{diagnosticError}<RowCompletenessScopeGate run={run} manifest={manifest} busy={busy} patch={patch} runNow={runNow} /></div>;
  }
  if (manifest.manifest_kind === "directional_monotonic_consistency") {
    return <div className="grid gap-4">{diagnosticError}<DirectionalityScopeGate run={run} manifest={manifest} busy={busy} patch={patch} runNow={runNow} /></div>;
  }
  return <CrossFieldScopeGate runId={runId} onRunStarted={onRunStarted} run={run} manifest={manifest}
    decisions={decisions} error={error} busy={busy} patch={patch} runNow={runNow} />;
}
