import { useEffect, useState } from "react";

import VersionDiff from "@/components/VersionDiff";
import { getCatalogueV2, restoreAssetVersionV2 } from "@/api/client";

export default function AssetCatalogue() {
  const [assets, setAssets] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [compare, setCompare] = useState(null);
  const [error, setError] = useState("");
  const load = () => getCatalogueV2().then(setAssets).catch((err) => setError(err.message));
  useEffect(() => { load(); }, []);
  const restore = async (assetId, version) => {
    try { await restoreAssetVersionV2(assetId, version); await load(); }
    catch (err) { setError(err.message); }
  };
  return <main className="min-h-screen bg-slate-50 p-8" data-testid="asset-catalogue">
    <div className="mb-6"><h1 className="text-2xl font-bold text-slate-950">Asset catalogue</h1><p className="mt-1 text-sm text-slate-600">Reusable assets, retained history and captured usage summaries.</p></div>
    {error && <p className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <div className="overflow-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[1050px] text-left text-sm"><thead className="bg-slate-100 text-xs uppercase text-slate-500"><tr><th className="px-3 py-3">Asset</th><th className="px-3 py-3">Kind</th><th className="px-3 py-3">Version</th><th className="px-3 py-3">Snapshots</th><th className="px-3 py-3">Dictionary</th><th className="px-3 py-3">Last upload</th><th className="px-3 py-3">Lifecycle</th><th className="px-3 py-3">Usage summary</th></tr></thead>
        <tbody>{assets.map((asset) => <tr key={asset.asset_id} className="border-t border-slate-100 align-top"><td className="px-3 py-3"><button type="button" className="font-semibold text-dq-purple underline" onClick={() => setExpanded(expanded === asset.asset_id ? null : asset.asset_id)}>{asset.display_name}</button>{expanded === asset.asset_id && <div className="mt-3 space-y-3 text-xs">{asset.versions.map((version) => <section key={version.version_id} className="rounded border border-slate-200 p-2"><p className="font-semibold">v{version.version_no}: {version.status_label}</p>{version.snapshots.map((snapshot) => <div key={snapshot.item_id} className="mt-2 border-t border-slate-100 pt-2"><p>{snapshot.snapshot_label || "Unnamed snapshot"} · {snapshot.snapshot_status_label}</p><p className="text-slate-500">{snapshot.completion_summary.rows_loaded ?? "—"} rows · uploaded {snapshot.created_at ? new Date(snapshot.created_at).toLocaleDateString() : "—"}</p><p className="text-slate-600">Completion summary is retrievable for this snapshot.</p></div>)}</section>)}{asset.versions.length > 1 && <button type="button" className="text-dq-purple underline" onClick={() => setCompare(compare?.assetId === asset.asset_id ? null : { assetId: asset.asset_id, old: asset.versions[0].version_no, current: asset.current_version_no })}>Open version diff</button>}{compare?.assetId === asset.asset_id && <VersionDiff assetId={asset.asset_id} versionA={compare.old} versionB={compare.current} />}</div>}</td><td className="px-3 py-3">{asset.kind_label}</td><td className="px-3 py-3">v{asset.current_version}</td><td className="px-3 py-3">{asset.active_snapshot_count} active · {asset.superseded_snapshot_count} superseded</td><td className="px-3 py-3">{asset.dictionary_state_label}</td><td className="px-3 py-3">{asset.last_upload ? new Date(asset.last_upload).toLocaleDateString() : "—"}</td><td className="px-3 py-3">{asset.lifecycle_status_label}</td><td className="px-3 py-3"><table className="text-xs"><tbody><tr><td className="pr-3">Selected</td><td>{asset.usage_summary.times_selected}</td></tr><tr><td className="pr-3">Snapshots added</td><td>{asset.usage_summary.snapshots_added}</td></tr><tr><td className="pr-3">Last activity</td><td>{asset.usage_summary.last_activity ? new Date(asset.usage_summary.last_activity).toLocaleDateString() : "—"}</td></tr><tr><td className="pr-3">Restores</td><td>{asset.usage_summary.restore_count}</td></tr></tbody></table>{asset.versions.filter((version) => version.status !== "current").map((version) => <button key={version.version_id} type="button" className="mt-2 block text-xs text-dq-purple underline" onClick={() => restore(asset.asset_id, version.version_no)}>Restore v{version.version_no}</button>)}</td></tr>)}</tbody>
      </table>
      {!assets.length && <p className="p-5 text-sm text-slate-500">No assets yet. Add a new Dataset or Database from Data Sourcing.</p>}
    </div>
  </main>;
}
