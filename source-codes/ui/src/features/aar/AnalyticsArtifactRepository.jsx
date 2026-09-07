import { useDeferredValue, useEffect, useState } from "react";
import { AlertTriangle, ArrowLeft, Database } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import {
  downloadAnalysisArtifactV2, getAnalysisArtifactLineageV2, getAnalysisArtifactOverviewV2, getAnalysisArtifactPayloadV2,
  getAnalysisArtifactRunsV2, getAnalysisArtifactsV2,
} from "@/api/client";
import { Button } from "@/components/ui/button";
import { ARTIFACT_PAGE_SIZE, ARTIFACT_REPOSITORY_TABS } from "./constants";
import {
  ArtifactLineage, ArtifactList, RepositoryOverview, RetainedRuns, SavedSchema,
} from "./ArtifactRepositoryViews";

export default function AnalyticsArtifactRepository() {
  const [params] = useSearchParams();
  const snapshotId = params.get("item") || "";
  const assetId = params.get("asset") || "";
  const [tab, setTab] = useState("Overview");
  const [overview, setOverview] = useState(null);
  const [rows, setRows] = useState([]);
  const [schemaRows, setSchemaRows] = useState([]);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [artifactPage, setArtifactPage] = useState({ offset: 0, total: 0, snapshotId: "", query: "" });
  const [schemaPage, setSchemaPage] = useState({ offset: 0, total: 0, snapshotId: "" });
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailPayload, setDetailPayload] = useState(null);
  const [error, setError] = useState("");
  const [featureQuery, setFeatureQuery] = useState("");
  const [summaryRefresh, setSummaryRefresh] = useState(0);
  const deferredFeatureQuery = useDeferredValue(featureQuery);
  const artifactOffset = artifactPage.snapshotId === snapshotId && artifactPage.query === deferredFeatureQuery
    ? artifactPage.offset : 0;
  const schemaOffset = schemaPage.snapshotId === snapshotId ? schemaPage.offset : 0;

  useEffect(() => {
    if (!snapshotId) return undefined;
    let timer;
    getAnalysisArtifactOverviewV2({ asset_id: assetId, snapshot_id: snapshotId })
      .then((value) => {
        setOverview(value);
        setError("");
        if (value.backfill_status === "pending") {
          timer = window.setTimeout(() => setSummaryRefresh((current) => current + 1), 500);
        }
      })
      .catch((reason) => setError(reason.message));
    return () => window.clearTimeout(timer);
  }, [assetId, snapshotId, summaryRefresh]);

  useEffect(() => {
    if (!snapshotId || tab !== "Artifacts") return;
    getAnalysisArtifactsV2({
      asset_id: assetId, snapshot_id: snapshotId, status: "active",
      feature_query: deferredFeatureQuery, limit: ARTIFACT_PAGE_SIZE, offset: artifactOffset,
    })
      .then((value) => {
        setRows(value.artifacts || []);
        setArtifactPage({ offset: artifactOffset, total: value.total || 0, snapshotId, query: deferredFeatureQuery });
        setError("");
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setArtifactsLoading(false));
  }, [artifactOffset, assetId, deferredFeatureQuery, snapshotId, summaryRefresh, tab]);

  useEffect(() => {
    if (!snapshotId || tab !== "Saved Schema") return;
    getAnalysisArtifactsV2({
      asset_id: assetId, snapshot_id: snapshotId, artifact_type: "column_profile",
      status: "active", limit: ARTIFACT_PAGE_SIZE, offset: schemaOffset,
    })
      .then((value) => {
        setSchemaRows(value.artifacts || []);
        setSchemaPage({ offset: schemaOffset, total: value.total || 0, snapshotId });
        setError("");
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setSchemaLoading(false));
  }, [assetId, schemaOffset, snapshotId, summaryRefresh, tab]);

  useEffect(() => {
    if (!snapshotId || tab !== "Analytical Runs") return;
    getAnalysisArtifactRunsV2(snapshotId)
      .then((value) => setRuns(value.runs || []))
      .catch((reason) => setError(reason.message));
  }, [snapshotId, tab]);

  const openLineage = (artifact) => {
    setSelected(artifact);
    setTab("Lineage & Impact");
    setLineage(null);
    getAnalysisArtifactLineageV2(artifact.artifact_id)
      .then(setLineage)
      .catch((reason) => setError(reason.message));
  };

  const showDetails = (artifact) => {
    setDetail(artifact);
    setDetailPayload(null);
    if (artifact.payload_media_type !== "application/json") {
      setDetailPayload({
        format: "Parquet", media_type: artifact.payload_media_type,
        filename: artifact.payload_filename, payload_hash: artifact.payload_hash,
        record_summary: artifact.summary,
      });
      return;
    }
    getAnalysisArtifactPayloadV2(artifact.artifact_id)
      .then((value) => setDetailPayload(value.payload))
      .catch((reason) => setError(reason.message));
  };

  const downloadArtifact = async (artifact) => {
    try {
      const blob = await downloadAnalysisArtifactV2(artifact.artifact_id);
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = artifact.payload_filename || `${artifact.artifact_id}.parquet`;
      link.click(); URL.revokeObjectURL(url); setError("");
    } catch (reason) { setError(reason.message); }
  };

  const hideDetails = () => {
    setDetail(null);
    setDetailPayload(null);
  };

  const selectTab = (name) => {
    if (name === "Saved Schema") setSchemaLoading(true);
    if (name === "Artifacts") setArtifactsLoading(true);
    setTab(name);
  };

  return <main className="min-h-screen bg-slate-50 p-8">
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="flex items-center gap-2 text-2xl font-bold text-slate-950"><Database className="h-6 w-6 text-dq-purple" /> Analytics Artifact Repository</h1><p className="mt-1 text-sm text-slate-500">Read-only immutable evidence for snapshot {snapshotId || "not selected"}.</p></div>
      <Button variant="outline" asChild><Link to={`/test-lab?asset=${encodeURIComponent(assetId)}`}><ArrowLeft className="h-4 w-4" /> Return to Test Lab</Link></Button>
    </div>
    {!snapshotId && <p className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">Select a ready snapshot from Test Lab to open its repository.</p>}
    {error && <p className="mb-4 flex gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700"><AlertTriangle className="h-4 w-4" /> {error}</p>}
    <div className="mb-5 flex flex-wrap gap-2">{ARTIFACT_REPOSITORY_TABS.map((name) => <button key={name} type="button" onClick={() => selectTab(name)} className={`rounded-full border px-3 py-1.5 text-sm font-medium ${tab === name ? "border-dq-purple bg-dq-purple text-dq-dark" : "border-slate-200 bg-white text-slate-600"}`}>{name}</button>)}</div>
    {tab === "Overview" && <RepositoryOverview overview={overview} />}
    {tab === "Saved Schema" && <SavedSchema rows={schemaRows} loading={schemaLoading} page={{ ...schemaPage, offset: schemaOffset }} onPageChange={(offset) => { setSchemaLoading(true); setSchemaPage((current) => ({ ...current, offset, snapshotId })); }} />}
    {tab === "Artifacts" && <ArtifactList rows={rows} loading={artifactsLoading} featureQuery={featureQuery} onFeatureQueryChange={(value) => { setArtifactsLoading(true); setFeatureQuery(value); }} onOpen={openLineage} onDetails={showDetails} onDownload={downloadArtifact} detail={detail} detailPayload={detailPayload} onCloseDetails={hideDetails} page={{ ...artifactPage, offset: artifactOffset }} onPageChange={(offset) => { setArtifactsLoading(true); setArtifactPage((current) => ({ ...current, offset, snapshotId, query: deferredFeatureQuery })); }} />}
    {tab === "Analytical Runs" && <RetainedRuns rows={runs} />}
    {tab === "Lineage & Impact" && <ArtifactLineage selected={selected} lineage={lineage} onOpen={openLineage} />}
  </main>;
}
