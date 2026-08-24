// ui/src/pages/admin/VersionHistory.jsx — 0.5.0 Step 3c (ADM-06/07).
//
// Admin's version-history view: pick any asset, see its full version
// sequence and, within each version, every snapshot it owns — active AND
// superseded (AST-08 — this is explicitly the one place superseded
// snapshots are meant to stay visible, never hidden) — each with timestamp,
// actor, intent and a one-line change summary. The two factory-reset
// actions (ADM-07) render inline in the SAME chronological list, as their
// own visually distinct row, so the reset-ID epoch boundary (D-29/R-08) is
// unmissable: any system ID appearing below a reset row belongs to a NEW
// asset, even if an asset above the row happened to carry the identical ID.
//
// Every raw status/intent token from the API (`snapshot_status`, version
// `status`, `intent`, `kind`) is rendered through `assetHistoryLabels.js`'s
// human labels — never the bare enum. The reset rows' delete counts are
// rendered through `summarizeReset()` from `../../lib/resetLabels.js` —
// Step 2's existing map, reused here rather than rebuilt (Task 3.4's brief).
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { getAdminAssetHistory, getAdminAssetsList } from "@/api/client";
import { summarizeReset } from "@/lib/resetLabels";
import {
  assetKindLabel,
  intentLabel,
  resetGradeLabel,
  snapshotStatusLabel,
  versionStatusLabel,
} from "@/lib/assetHistoryLabels";

function formatTimestamp(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function VersionGroup({ version }) {
  return (
    <div
      className="rounded-lg border border-slate-200 p-4 space-y-3"
      data-testid={`version-group-${version.version_no}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="text-sm font-semibold text-slate-800">Version {version.version_no}</span>
          <Badge
            variant={version.status === "current" ? "success" : "secondary"}
            className="ml-2"
          >
            {versionStatusLabel(version.status)}
          </Badge>
        </div>
        <span className="text-xs text-slate-400 whitespace-nowrap">
          {formatTimestamp(version.created_at)}
        </span>
      </div>
      {version.change_summary && (
        <p className="text-sm text-slate-600">{version.change_summary}</p>
      )}
      <p className="text-xs text-slate-500">{version.reference_schema_summary}</p>

      <div className="space-y-2 pt-1">
        {version.snapshots.map((snap) => (
          <div
            key={snap.snapshot_id}
            className="rounded border border-slate-100 bg-slate-50/70 px-3 py-2"
            data-testid={`snapshot-row-${snap.snapshot_id}`}
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium text-slate-800">
                {snap.snapshot_label || snap.snapshot_id}
              </span>
              <Badge
                variant={snap.snapshot_status === "active" ? "default" : "secondary"}
                data-testid={`snapshot-status-${snap.snapshot_id}`}
              >
                {snapshotStatusLabel(snap.snapshot_status)}
              </Badge>
            </div>
            <div className="text-xs text-slate-500 mt-1">
              {intentLabel(snap.intent)} · {formatTimestamp(snap.uploaded_at)}
              {snap.uploaded_by ? ` · ${snap.uploaded_by}` : ""}
            </div>
            <div className="text-xs text-slate-600 mt-1">{snap.change_summary}</div>
          </div>
        ))}
        {version.snapshots.length === 0 && (
          <p className="text-xs text-slate-400">No snapshots recorded for this version.</p>
        )}
      </div>
    </div>
  );
}

function ResetRow({ reset }) {
  const summary = summarizeReset(reset.deleted);
  return (
    <div
      className="rounded-lg border-2 border-red-300 bg-red-50/60 px-4 py-3 space-y-1"
      data-testid="reset-boundary-row"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-red-700">
          {resetGradeLabel(reset.grade)} — ID epoch boundary
        </span>
        <span className="text-xs text-slate-500 whitespace-nowrap">
          {formatTimestamp(reset.at)}
        </span>
      </div>
      <p className="text-sm text-slate-700">{summary.sentence}</p>
      <p className="text-xs text-slate-500">
        System IDs restart from a clean baseline at this point (
        {reset.actor ? `run by ${reset.actor}` : "reset"}). An ID that reappears after this row
        names a different asset than the same ID before it.
      </p>
    </div>
  );
}

export default function VersionHistory() {
  const [assets, setAssets] = useState([]);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getAdminAssetsList()
      .then(setAssets)
      .catch((err) => setError(err.message));
  }, []);

  const filteredAssets = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return assets;
    return assets.filter((a) =>
      [a.display_name, a.system_id, a.alias].some((v) => (v || "").toLowerCase().includes(q))
    );
  }, [assets, search]);

  async function selectAsset(assetId) {
    setSelectedId(assetId);
    setHistory(null);
    setError(null);
    setLoading(true);
    try {
      const payload = await getAdminAssetHistory(assetId);
      setHistory(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ADM-07/R-08: version groups and reset rows merged into ONE chronological
  // list, oldest first, so a reset's ID-epoch boundary reads inline exactly
  // where it happened relative to the asset's own version sequence.
  const timeline = useMemo(() => {
    if (!history) return [];
    const entries = [
      ...history.versions.map((v) => ({ kind: "version", at: v.created_at || "", version: v })),
      ...history.resets.map((r) => ({ kind: "reset", at: r.at || "", reset: r })),
    ];
    entries.sort((a, b) => a.at.localeCompare(b.at));
    return entries;
  }, [history]);

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold">Asset version history</h2>
        <p className="text-sm text-slate-600">
          Pick any asset to see its full version sequence, every snapshot it owns — active and
          superseded — and where factory resets fall in its timeline.
        </p>
      </div>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          {error}
        </div>
      )}

      <div className="max-w-md space-y-1.5">
        <Input
          placeholder="Search by name, alias or system ID…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="version-history-search"
        />
        <div className="border border-slate-200 rounded-md max-h-52 overflow-y-auto divide-y divide-slate-100">
          {filteredAssets.length === 0 && (
            <div className="px-3 py-2 text-sm text-slate-400">No assets found.</div>
          )}
          {filteredAssets.map((a) => (
            <button
              key={a.asset_id}
              type="button"
              data-testid={`version-history-pick-${a.asset_id}`}
              className={`block w-full text-left px-3 py-2 text-sm hover:bg-slate-50 ${
                selectedId === a.asset_id ? "bg-dq-purple/10 font-semibold" : ""
              }`}
              onClick={() => selectAsset(a.asset_id)}
            >
              {a.display_name}
              <span className="ml-2 text-xs text-slate-400">
                {assetKindLabel(a.kind)} · v{a.current_version_no} · {a.snapshot_count} active
                snapshot(s)
              </span>
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="text-sm text-slate-500">Loading history…</p>}

      {history && (
        <div className="space-y-4" data-testid="version-history-timeline">
          <div className="text-sm text-slate-700 border-b border-slate-100 pb-2">
            <span className="font-semibold">{history.asset.display_name}</span>
            {" — "}
            {assetKindLabel(history.asset.kind)}, current version {history.asset.current_version_no}
          </div>

          {timeline.length === 0 && (
            <p className="text-sm text-slate-400">No history recorded yet.</p>
          )}

          {timeline.map((entry) =>
            entry.kind === "reset" ? (
              <ResetRow key={`reset-${entry.reset.at}`} reset={entry.reset} />
            ) : (
              <VersionGroup key={`version-${entry.version.version_no}`} version={entry.version} />
            )
          )}
        </div>
      )}
    </div>
  );
}
