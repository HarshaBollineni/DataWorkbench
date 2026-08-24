import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";

import { getAssetsV2, refreshAssetV2 } from "@/api/client";
import { Button } from "@/components/ui/button";
import { staleSourceText } from "@/lib/staleSources";
import { useWorkflowContext } from "@/lib/workflowContext";

const STATUS_LABEL = { sourcing: "In progress", complete: "Complete", archived: "Archived", requires_reupload: "Needs re-upload" };

export default function WorkflowContextBar({ onChange }) {
  const { asset, setAsset, clear } = useWorkflowContext();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [validatedAssetId, setValidatedAssetId] = useState(null);

  useEffect(() => {
    if (!asset?.asset_id) return undefined;
    const reread = () => getAssetsV2(asset.kind).then((rows) => {
      const current = rows.find((row) => row.asset_id === asset.asset_id);
      if (current) {
        setAsset(current);
        setValidatedAssetId(current.asset_id);
      } else {
        // Full wipe cannot clear browser sessionStorage itself. Do not render
        // a persisted workflow context unless the asset still exists server-side.
        clear();
        setValidatedAssetId(null);
      }
    }).catch(() => setValidatedAssetId(null));
    reread();
    window.addEventListener("focus", reread);
    return () => window.removeEventListener("focus", reread);
  }, [asset?.asset_id, asset?.kind, clear, setAsset]);

  if (!asset || validatedAssetId !== asset.asset_id) return null;

  const refresh = async () => {
    setBusy(true);
    setMessage("");
    try {
      const result = await refreshAssetV2(asset.asset_id);
      setMessage(`Refreshed ${result.recomputed?.length || 0} active snapshot(s).`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mb-6 rounded-lg border border-dq-purple/30 bg-dq-purple/5 p-4" data-testid="workflow-context-bar">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-dq-purple">Workflow context</p>
          <p className="truncate font-semibold text-slate-950" title={asset.display_name}>{asset.display_name}</p>
          <p className="text-sm text-slate-600">v{asset.current_version_no} · {asset.active_snapshot_count ?? asset.snapshot_count ?? 0} active snapshots · {STATUS_LABEL[asset.lifecycle_status] || "Status unavailable"}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={refresh} disabled={busy}><RefreshCw className="h-4 w-4" /> Refresh</Button>
          <Button size="sm" variant="outline" onClick={onChange}>Change</Button>
          <Button size="sm" variant="ghost" onClick={clear}>Clear</Button>
        </div>
      </div>
      {message && <p className="mt-2 text-xs text-slate-600" role="status">{message}</p>}
      {asset.stale_sources?.map((source) => <p key={source.snapshot_id} className="mt-2 text-xs text-amber-800">{staleSourceText(source)} · use Refresh to recompute.</p>)}
      <div className="mt-3 border-t border-dq-purple/20 pt-3 text-sm">
        <Link className="font-medium text-dq-purple hover:underline" to={`/test-lab?asset=${encodeURIComponent(asset.asset_id)}`}>Go to Test Lab</Link>
      </div>
    </section>
  );
}
