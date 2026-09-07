import { Fragment, useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";

import { getAssetsV2, selectAssetV2 } from "@/api/client";
import VersionDiff from "@/components/VersionDiff";

const LIFECYCLE_LABEL = {
  sourcing: "Sourcing", complete: "Complete", archived: "Archived",
  requires_reupload: "Requires re-upload",
};

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString();
}

/**
 * Compact existing-asset selector. `excludeRequiresReupload` is deliberately
 * required: each call site must state whether the recovery path is in scope.
 */
export default function AssetPicker({ kind, value, onChange, onResume, excludeRequiresReupload, allowResumable = false }) {
  const [assets, setAssets] = useState([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [compare, setCompare] = useState(null);

  useEffect(() => {
    let live = true;
    getAssetsV2(kind).then((rows) => {
      if (live) setAssets(rows || []);
    }).catch((err) => {
      if (live) setError(err.message);
    });
    return () => { live = false; };
  }, [kind]);

  const visible = useMemo(() => assets.filter((asset) => {
    // Completed/archived assets are never workflow targets. The deliberate
    // prop controls only the requires-reupload recovery carve-out.
    if (["complete", "archived"].includes(asset.lifecycle_status)) return false;
    if (excludeRequiresReupload && asset.lifecycle_status === "requires_reupload") return false;
    const haystack = `${asset.display_name} ${asset.lifecycle_status}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  }), [assets, excludeRequiresReupload, query]);

  return (
    <div className="rounded-md border border-slate-200 bg-white p-3" data-testid="asset-picker">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
        <input aria-label="Search existing assets" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search assets" className="h-9 w-full rounded-md border border-slate-200 pl-9 pr-3 text-sm" />
      </div>
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
      <div className="mt-2 max-h-64 overflow-auto">
        <table className="w-full min-w-[760px] text-left text-xs">
          <thead className="sticky top-0 bg-slate-100 text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-2 py-2">Asset</th><th className="px-2 py-2">Version</th>
              <th className="px-2 py-2">Lifecycle</th><th className="px-2 py-2">Active snapshots</th>
              <th className="px-2 py-2">Last upload</th><th className="px-2 py-2">Size</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((asset) => {
              const continuing = Boolean(allowResumable && asset.resumable);
              const selectable = Boolean(asset.selectable);
              const actionable = Boolean(selectable || continuing);
              const needsReupload = asset.lifecycle_status === "requires_reupload";
              const chooseAsset = (event) => {
                selectAssetV2(asset.asset_id).catch(() => {});
                const resumeRequested = event.currentTarget.getAttribute("aria-label")?.startsWith("Continue sourcing");
                (resumeRequested ? onResume || onChange : onChange)(asset);
              };
              return (
                <Fragment key={asset.asset_id}>
                <tr className={`border-t border-slate-100 ${actionable ? "hover:bg-dq-purple/5" : "opacity-70"}`}>
                  <td className="px-2 py-2">
                    <button type="button" disabled={!selectable} onClick={chooseAsset}
                      className="text-left font-medium text-slate-800 hover:text-dq-purple hover:underline disabled:cursor-not-allowed disabled:no-underline">
                      {asset.display_name}
                    </button>
                    {continuing && <button type="button" onClick={chooseAsset} className="ml-2 rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-semibold text-indigo-700 hover:bg-indigo-200 hover:underline" aria-label={`Continue sourcing ${asset.display_name}`}>Continue sourcing · {String(asset.resume_ingest_status || "incomplete").replaceAll("_", " ")}</button>}
                    {needsReupload && <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">Needs re-upload</span>}
                    {!selectable && <div className="mt-0.5 text-[10px] text-slate-500">No active ready snapshot</div>}
                    {continuing && !asset.selectable && <div className="mt-0.5 text-[10px] text-slate-500">Not available in Test Lab until sourcing is completed.</div>}
                    {asset.superseded_versions?.length > 0 && <button type="button" className="mt-1 block text-[11px] text-dq-purple underline" onClick={() => setCompare(compare?.assetId === asset.asset_id ? null : { assetId: asset.asset_id, old: asset.superseded_versions[0], current: asset.current_version_no })}>Compare versions</button>}
                  </td>
                  <td className="px-2 py-2 text-slate-600">v{asset.current_version_no}</td>
                  <td className="px-2 py-2 text-slate-600">{LIFECYCLE_LABEL[asset.lifecycle_status] || "Unknown"}</td>
                  <td className="px-2 py-2 text-slate-600">{asset.active_snapshot_count ?? asset.snapshot_count ?? 0}</td>
                  <td className="px-2 py-2 text-slate-600">{formatDate(asset.last_upload_date || asset.last_upload_at)}</td>
                  <td className="px-2 py-2 text-slate-600">{asset.kind_count_label || "—"}</td>
                </tr>
                {compare?.assetId === asset.asset_id && <tr><td colSpan="6" className="bg-slate-50 px-2 py-3"><VersionDiff assetId={asset.asset_id} versionA={compare.old} versionB={compare.current} /></td></tr>}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {!visible.length && <p className="px-2 py-4 text-sm text-slate-500">No matching assets.</p>}
      </div>
      {value && assets.some((asset) => asset.asset_id === value.asset_id) && (
        <p className="mt-2 text-xs text-dq-purple">Selected: {value.display_name}</p>
      )}
    </div>
  );
}
