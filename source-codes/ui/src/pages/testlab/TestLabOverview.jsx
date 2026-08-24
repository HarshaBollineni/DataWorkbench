import { useEffect, useState } from "react";
import { AlertCircle, ChevronDown, Database, Table2 } from "lucide-react";
import { Link } from "react-router-dom";

import { getAnalysisArtifactOverviewV2, getItemIssuesV2 } from "@/api/client";
import { Button } from "@/components/ui/button";
import VariableInventory from "./VariableInventory";

function Stat({ label, value }) {
  return <div className="rounded-md bg-white px-3 py-2"><div className="text-lg font-semibold text-slate-900">{value ?? 0}</div><div className="text-xs text-slate-500">{label}</div></div>;
}

export function ArtifactRepositoryCard({ item }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer;
    getAnalysisArtifactOverviewV2({ asset_id: item.asset_id || item.dataset_family_id, snapshot_id: item.item_id })
      .then((value) => {
        if (alive) {
          setSummary(value);
          setError("");
          if (value.backfill_status === "pending") timer = window.setTimeout(() => setRefresh((current) => current + 1), 500);
        }
      })
      .catch((reason) => {
        if (alive) {
          setSummary(null);
          setError(reason.message);
        }
      });
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [item.item_id, item.asset_id, item.dataset_family_id, refresh]);

  const href = `/test-lab/artifacts?item=${encodeURIComponent(item.item_id)}&asset=${encodeURIComponent(item.asset_id || item.dataset_family_id || "")}`;
  return (
    <section className="rounded-lg border border-violet-200 bg-violet-50 p-5" aria-labelledby="artifact-repository-title">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 id="artifact-repository-title" className="flex items-center gap-2 text-base font-semibold text-slate-900"><Database className="h-4 w-4 text-dq-purple" /> Analytics Artifact Repository</h2>
          <p className="mt-1 text-sm text-slate-600">Immutable reusable evidence for snapshot {item.snapshot_label || item.name}. Exact matches are reused; near matches are not.</p>
        </div>
        <Button asChild><Link to={href}>Open repository</Link></Button>
      </div>
      {!summary && !error && <p className="mt-3 text-sm text-slate-500">Loading repository summary…</p>}
      {error && <p className="mt-3 text-sm text-red-700">Repository summary is unavailable: {error}</p>}
      {summary && <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3 xl:grid-cols-5">
        <Stat label="Active" value={summary.active_artifacts} /><Stat label="Universal" value={summary.universal_artifacts} />
        <Stat label="Diagnostic local" value={summary.diagnostic_local_artifacts} />
        <Stat label="Features" value={summary.represented_features} />
        <Stat label="Integrity warnings" value={summary.integrity_warnings} />
      </div>}
      {summary?.backfill_status === "pending" && <p className="mt-3 text-xs text-slate-500">Preparing retained Data Sourcing evidence…</p>}
    </section>
  );
}

export function IssueReviewCard({ item, board }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getItemIssuesV2(item.item_id)
      .then((payload) => {
        if (alive) {
          setSummary(payload.summary || null);
          setError("");
        }
      })
      .catch((reason) => {
        if (alive) {
          setSummary(null);
          setError(reason.message);
        }
      });
    return () => { alive = false; };
  }, [item.item_id, board]);

  const href = `/issues?item=${encodeURIComponent(item.item_id)}`;
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50/60 p-5" aria-labelledby="review-issues-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="review-issues-title" className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <AlertCircle className="h-4 w-4 text-amber-600" /> Review &amp; issues
          </h2>
          <p className="mt-1 text-sm text-slate-600">Current status for {item.name}.</p>
        </div>
        <Button asChild variant="outline"><Link to={href}>View issues</Link></Button>
      </div>
      {!summary && !error && <p className="mt-3 text-sm text-slate-500">Loading issue summary&hellip;</p>}
      {error && <p className="mt-3 text-sm text-red-700">Issue summary is unavailable: {error}</p>}
      {summary && <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <Stat label="Review needed" value={summary.review_needed} />
        <Stat label="Open" value={summary.open} />
        <Stat label="In review" value={summary.in_review} />
        <Stat label="Closed" value={summary.closed} />
      </div>}
    </section>
  );
}

export function SavedInventoryViewer({ items, currentItemId }) {
  const [open, setOpen] = useState(false);
  const [viewId, setViewId] = useState("");
  const chosenId = viewId || currentItemId;
  const chosen = items.find((row) => row.item_id === chosenId);

  return (
    <section className="mb-5 rounded-lg border border-slate-200 bg-white">
      <button type="button" onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-5 py-3 text-left">
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <Table2 className="h-4 w-4 text-dq-purple" /> Variable inventory of saved data
        </span>
        <span className="flex items-center gap-2 text-xs text-slate-500">
          {open ? "Hide" : "Show"}
          <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      {open && (
        <div className="border-t border-slate-100 p-5">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-sm text-slate-600">Saved dataset / database:</span>
            <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm"
              value={chosenId} onChange={(event) => setViewId(event.target.value)}>
              {items.map((row) => (
                <option key={row.item_id} value={row.item_id}>
                  {row.name} ({row.kind})
                </option>
              ))}
            </select>
          </div>
          {chosen && <VariableInventory item={chosen} readOnly />}
        </div>
      )}
    </section>
  );
}
