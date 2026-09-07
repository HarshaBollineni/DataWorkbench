import { useEffect, useState } from "react";
import { AlertCircle, Database } from "lucide-react";
import { Link } from "react-router-dom";

import { getAnalysisArtifactOverviewV2, getItemIssuesV2 } from "@/api/client";
import { Button } from "@/components/ui/button";

function Stat({ label, value }) {
  return <div className="flex shrink-0 items-baseline gap-1.5 whitespace-nowrap rounded-md border border-slate-200/70 bg-white/80 px-2 py-1">
    <dt className="text-[11px] text-slate-500">{label}</dt>
    <dd className="text-sm font-semibold tabular-nums text-slate-900">{value ?? 0}</dd>
  </div>;
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
    <section className="rounded-lg border border-violet-200 bg-violet-50/70 px-3 py-2.5" aria-labelledby="artifact-repository-title">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-[14rem] flex-1">
          <h2 id="artifact-repository-title" className="flex items-center gap-2 text-sm font-semibold leading-5 text-slate-900"><Database className="h-4 w-4 text-dq-purple" /> Analytics Artifact Repository</h2>
          <p className="mt-0.5 text-xs leading-4 text-slate-600">Immutable reusable evidence for snapshot {item.snapshot_label || item.name}. Exact matches are reused; near matches are not.</p>
        </div>
        <Button asChild size="sm"><Link to={href}>Open repository</Link></Button>
      </div>
      {!summary && !error && <p className="mt-2 text-xs text-slate-500">Loading repository summary…</p>}
      {error && <p className="mt-2 text-xs text-red-700">Repository summary is unavailable: {error}</p>}
      {summary && <dl className="mt-2 flex flex-wrap gap-1.5">
        <Stat label="Active" value={summary.active_artifacts} /><Stat label="Universal" value={summary.universal_artifacts} />
        <Stat label="Diagnostic local" value={summary.diagnostic_local_artifacts} />
        <Stat label="Features" value={summary.represented_features} />
        <Stat label="Integrity warnings" value={summary.integrity_warnings} />
      </dl>}
      {summary?.backfill_status === "pending" && <p className="mt-1.5 text-[11px] text-slate-500">Preparing retained Data Sourcing evidence…</p>}
    </section>
  );
}

export function IssueReviewCard({ item }) {
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
  }, [item.item_id]);

  const href = `/issues?item=${encodeURIComponent(item.item_id)}`;
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2.5" aria-labelledby="review-issues-title">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-[12rem] flex-1">
          <h2 id="review-issues-title" className="flex items-center gap-2 text-sm font-semibold leading-5 text-slate-900">
            <AlertCircle className="h-4 w-4 text-amber-600" /> Review &amp; issues
          </h2>
          <p className="mt-0.5 text-xs leading-4 text-slate-600">Current status for {item.name}.</p>
        </div>
        <Button asChild size="sm" variant="outline"><Link to={href}>View issues</Link></Button>
      </div>
      {!summary && !error && <p className="mt-2 text-xs text-slate-500">Loading issue summary&hellip;</p>}
      {error && <p className="mt-2 text-xs text-red-700">Issue summary is unavailable: {error}</p>}
      {summary && <dl className="mt-2 flex flex-wrap gap-1.5">
        <Stat label="Review needed" value={summary.review_needed} />
        <Stat label="Open" value={summary.open} />
        <Stat label="In review" value={summary.in_review} />
        <Stat label="Closed" value={summary.closed} />
      </dl>}
    </section>
  );
}
