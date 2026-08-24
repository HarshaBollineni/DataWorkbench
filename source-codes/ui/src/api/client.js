// Public endpoint catalog. Cross-cutting URL, authentication, and JSON request
// behavior live in transport.js so this module stays focused on API contracts.
import { API_BASE, authHeaders, getToken, readErrorDetail, request as req, setToken } from "./transport";

export { getToken } from "./transport";

// --- Auth + Admin (Plan 3 P1) ------------------------------------------------
export async function login(username, password) {
  const res = await req("/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setToken(res.token);
  return res.user;
}
export async function logout() {
  try {
    await req("/logout", { method: "POST" });
  } finally {
    setToken(null);
  }
}
export const me = () => req("/me");
export const updateProfile = (body) =>
  req("/me", { method: "PUT", body: JSON.stringify(body) });
// F16 — reset is now gated by the admin session (no password).

// --- Admin user management (Plan 4 F15) --------------------------------------
export const listUsers = () => req("/admin/users");
export const createUser = (body) =>
  req("/admin/users", { method: "POST", body: JSON.stringify(body) });
export const deleteUser = (username) =>
  req(`/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" });

// --- Admin factory reset (WSP-08 / D-19, plan Phase 2.9) ---------------------
// grade: "surgical" (items + derived artefacts; users/taxonomy/KB survive) or
// "wipe" (blank slate; only users/sessions/audit trail/feature flags survive).
// confirm must exactly match the phrase the server expects for that grade
// ("RESET" / "WIPE EVERYTHING") — enforced server-side regardless of the UI.
export const factoryReset = (grade, confirm, legacyAssetIds = [], legacyRunIds = [], diagnostics = {}) =>
  req("/admin/factory-reset", {
    method: "POST",
    body: JSON.stringify({
      grade, confirm,
      legacy_asset_ids: legacyAssetIds,
      legacy_run_ids: legacyRunIds,
      diagnostic_run_ids: diagnostics.runIds || [],
      wipe_all_diagnostics: Boolean(diagnostics.all),
    }),
  });
export const getDevelopmentArtifactCandidates = () =>
  req("/admin/development-artifacts");
export const getDiagnosticRuns = () => req("/admin/diagnostic-runs");

// --- Admin asset version history (0.5.0 ADM-06/07, plan Step 3c) ------------
// The picker's data source (unfiltered — an audit surface, unlike the
// workflow-selection "View Existing" dropdown Step 5 builds) and the
// chronological version/snapshot/reset history for one asset.
export const getAdminAssetsList = () => req("/admin/assets");
export const getAdminAssetHistory = (assetId) =>
  req(`/admin/assets/${encodeURIComponent(assetId)}/history`);
export const getNextAssetIdV2 = (kind) =>
  req(`/v2/assets/next-id?kind=${encodeURIComponent(kind)}`);
export const getAssetsV2 = (kind) =>
  req(`/v2/assets${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`);
export const createUploadTargetV2 = (body) =>
  req("/v2/assets/upload-target", { method: "POST", body: JSON.stringify(body) });
export const getSupersedePreviewV2 = (assetId) =>
  req(`/v2/assets/${encodeURIComponent(assetId)}/supersede-preview`);
export const refreshAssetV2 = (assetId) =>
  req(`/v2/assets/${encodeURIComponent(assetId)}/refresh`, { method: "POST" });
export const processSnapshotV2 = (snapshotId, body) =>
  req(`/v2/items/${encodeURIComponent(snapshotId)}/process`, {
    method: "POST", body: JSON.stringify(body),
  });
export const restoreAssetVersionV2 = (assetId, versionNo) =>
  req(`/v2/assets/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionNo)}/restore`, { method: "POST" });
export const selectAssetV2 = (assetId) =>
  req(`/v2/assets/${encodeURIComponent(assetId)}/select`, { method: "POST" });
export const getVersionDiffV2 = (assetId, versionA, versionB) =>
  req(`/v2/assets/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionA)}/diff/${encodeURIComponent(versionB)}`);
export const getCatalogueV2 = () => req("/v2/catalogue");

// --- Galileo v2 --------------------------------------------------------------
// Feedback 2.3 — background upload with a live progress bar + ETA. XHR (not
// fetch) because upload progress events are only exposed there. onProgress
// receives {loaded, total, pct}.
export async function inspectSourceV2(file, role = "data", signal) {
  const form = new FormData();
  form.append("role", role);
  form.append("file", file);
  const res = await fetch(`${API_BASE}/v2/sources/inspect`, {
    method: "POST", body: form, signal,
    headers: authHeaders(),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    const message = typeof detail === "object" ? detail.message || JSON.stringify(detail) : detail;
    const error = new Error(`${res.status}: ${message}`);
    error.status = res.status;
    error.detail = detail;
    throw error;
  }
  return res.json();
}
export function uploadSourceBundleV2WithProgress(id, {
  dataFile, dictionaryFile, parsingOptions, fileContext, dictionaryHeaderMapping,
}, onProgress) {
  return new Promise((resolve, reject) => {
    const token = getToken();
    const form = new FormData();
    form.append("data_file", dataFile);
    if (dictionaryFile) form.append("dictionary_file", dictionaryFile);
    form.append("parsing_options", JSON.stringify(parsingOptions || {}));
    form.append("file_context", fileContext || "");
    form.append("dictionary_header_mapping", JSON.stringify(dictionaryHeaderMapping || {}));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/v2/items/${encodeURIComponent(id)}/source-bundle`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress({ loaded: event.loaded, total: event.total, pct: event.total ? event.loaded / event.total : 0 });
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
      } else {
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* non-JSON */ }
        reject(new Error(`${xhr.status}: ${detail}`));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed — network error."));
    xhr.send(form);
  });
}
// Sets the (optional) target variable / use case (ING-01/ING-02: asked once,
// inline, defaulted — never a gate). Safe to call any time, including after
// the item is already profiled (it reclassifies the newly-chosen target).
export const patchItemV2 = (id, body) =>
  req(`/v2/items/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
// ING-06/07/08 — the Review screen's combined status/mapping/warnings feed.
export const getIngestV2 = (id, { table } = {}) =>
  req(`/v2/items/${encodeURIComponent(id)}/ingest${table ? `?table=${encodeURIComponent(table)}` : ""}`);
// ING-09 — replacement is a new delivery, never a mutation of the old item.
export const getItemsV2 = (kind) =>
  req(`/v2/items${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`);
export const getItemTablesV2 = (id) => req(`/v2/items/${encodeURIComponent(id)}/tables`);
export const profileStreamUrlV2 = (id) => `${API_BASE}/v2/items/${encodeURIComponent(id)}/profile/stream`;
export const getInventoryV2 = (id, { table } = {}) =>
  req(`/v2/items/${encodeURIComponent(id)}/inventory${table ? `?table=${encodeURIComponent(table)}` : ""}`);
export const putInventoryV2 = (id, rows, { table } = {}) =>
  req(`/v2/items/${encodeURIComponent(id)}/inventory${table ? `?table=${encodeURIComponent(table)}` : ""}`, {
    method: "PUT",
    body: JSON.stringify(rows),
  });
// Legacy (pre-Phase-6) results read: still served by the backend for the old
// results_v2 table (IssueManagement.jsx / IssueRca.jsx use it for whatever
// residual rows exist); the wizard's plan/snippet/execute/recommend/score
// family that used to sit alongside it retired with the Test Lab rebuild
// (Phase 6 / D-16) — those routes no longer exist server-side.
export const getResultsV2 = (id, scope = "framework") =>
  req(`/v2/items/${encodeURIComponent(id)}/results?scope=${encodeURIComponent(scope)}`);
// Issue Management + Reports (spec 9-10). Tracking only: close/raise record a
// decision, never mutate data or re-run tests.
export const getItemIssuesV2 = (id) => req(`/v2/items/${encodeURIComponent(id)}/issues`);
export const getIssueRegisterV2 = (filters = {}) => {
  const qs = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v)
  ).toString();
  return req(`/v2/issues/register${qs ? `?${qs}` : ""}`);
};
export const getIssueV2 = (issueRowId) => req(`/v2/issues/${encodeURIComponent(issueRowId)}`);
export const rcaStreamUrlV2 = (issueRowId) =>
  `${API_BASE}/v2/issues/${encodeURIComponent(issueRowId)}/rca/stream`;
export const createIssueAnalysesV2 = (issueRowId, analyses) =>
  req(`/v2/issues/${encodeURIComponent(issueRowId)}/analysis`, {
    method: "POST", body: JSON.stringify({ analyses }),
  });
export const closeIssueV2 = (issueRowId, rationale) =>
  req(`/v2/issues/${encodeURIComponent(issueRowId)}/close`, {
    method: "POST", body: JSON.stringify({ rationale }),
  });
export const raiseIssueV2 = (issueRowId, body) =>
  req(`/v2/issues/${encodeURIComponent(issueRowId)}/raise`, {
    method: "POST", body: JSON.stringify(body),
  });
// Feedback 5.1 — AI summary + interpretation of executed additional analyses.
export const interpretIssueAnalysesV2 = (issueRowId) =>
  req(`/v2/issues/${encodeURIComponent(issueRowId)}/analysis/interpret`, { method: "POST" });
// Feedback 5.3 — update details of a raised tracked issue (ISS-xxxx).
export const patchTrackedIssueV2 = (issueId, body) =>
  req(`/v2/issues/tracked/${encodeURIComponent(issueId)}`, { method: "PATCH", body: JSON.stringify(body) });
// PDF is generated on demand server-side; fetch as blob so 4xx surfaces a message.
export async function downloadReportV2(id) {
  const res = await fetch(`${API_BASE}/v2/items/${encodeURIComponent(id)}/report`);
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] || "dq-report.pdf";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
export const getFrameworkOverviewV2 = () => req("/v2/framework/overview");
export const getFrameworkAreasV2 = () => req("/v2/framework/areas");
export const getFrameworkTestsV2 = () => req("/v2/framework/tests");
export const getFrameworkMatrixV2 = () => req("/v2/framework/matrix");

// --- Test Lab diagnostics (Phase 6 — register-driven Coverage/Scope/Run/
// Findings/Score; testlab-redesign-0.4.0.md §5.3) -----------------------------
// Coverage board: 9 register cards + readiness chips + the area-level GAP strip.
export const getDiagnosticsBoardV2 = (itemId) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/diagnostics/board`);
// Build the scope-gate manifest for one diagnostic (draft; not yet frozen).
export const buildDiagnosticManifestV2 = (itemId, body) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/diagnostics/manifest`, {
    method: "POST", body: JSON.stringify(body),
  });
export const getDiagnosticManifestV2 = (runId) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}`);
// One scope-gate edit -> one decision record (role override / threshold tune /
// scope exclusion / role-verification change). Refused once frozen.
export const patchDiagnosticManifestV2 = (runId, body) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}`, {
    method: "PATCH", body: JSON.stringify(body),
  });
// Freeze + run. {stream:true} returns the SSE URL to open; omitted/false runs
// synchronously and returns the final "done" frame directly.
export const runDiagnosticManifestV2 = (runId, body = { stream: true }) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/run`, {
    method: "POST", body: JSON.stringify(body),
  });
export const diagnosticRunStreamUrlV2 = (runId) =>
  `${API_BASE}/v2/diagnostics/runs/${encodeURIComponent(runId)}/stream`;
// Decision-type-shaped results + findings. With no runId, diagnosticId can
// select that diagnostic's latest completed run rather than the item's latest.
export const getDiagnosticResultsV2 = (itemId, runId, diagnosticId) => {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (diagnosticId != null) params.set("diagnostic_id", diagnosticId);
  const query = params.toString();
  return req(`/v2/items/${encodeURIComponent(itemId)}/diagnostics/results${query ? `?${query}` : ""}`);
};
export const getDiagnosticRunHistoryV2 = (itemId, diagnosticId) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/diagnostics/${encodeURIComponent(diagnosticId)}/runs`);
// SME disposition (human decision #2): confirm_issue | dismiss. The backend
// enforces a non-blank reason on dismiss (400) rather than a disabled button.
export const dispositionFindingV2 = (findingId, body) =>
  req(`/v2/diagnostics/findings/${encodeURIComponent(findingId)}/disposition`, {
    method: "POST", body: JSON.stringify(body),
  });
// Coverage-honest roll-up for the Score panel — no health-score field, ever.
export const getDiagnosticsCoverageSummaryV2 = (itemId) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/diagnostics/coverage-summary`);

// Supporting investigations are intentionally separate from the nine-row
// diagnostic register. Missingness Mechanism is the first vertical slice.
export const getAnalysisCatalogV2 = (itemId) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/analyses/catalog`);
export const createAnalysisManifestV2 = (itemId, body) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/analyses/manifests`, {
    method: "POST", body: JSON.stringify(body),
  });
export const promotePsiResultV2 = (resultId, reason) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/psi-promotion`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
export const promoteFeatureTargetResultV2 = (resultId, reason, overwriteExisting = false) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/feature-target-promotion`, {
    method: "POST", body: JSON.stringify({ reason, overwrite_existing: overwriteExisting }),
  });
export const promoteDiagnosticResultV2 = (resultId, reason, overwriteExisting = false) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/promotion`, {
    method: "POST", body: JSON.stringify({ reason, overwrite_existing: overwriteExisting }),
  });
export const getResumableDiagnosticDraftV2 = (itemId, diagnosticId) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/diagnostics/${encodeURIComponent(diagnosticId)}/draft`);
export const createPsiBinDraftV2 = (runId, feature, generateNew = false) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/draft${generateNew ? "?generate_new=true" : ""}`, { method: "POST" });
export const psiBinDraftStreamUrlV2 = (runId) =>
  `${API_BASE}/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/draft-stream`;
export const getPsiBinReviewV2 = (runId, feature, artifactId) => {
  const params = new URLSearchParams();
  if (artifactId) params.set("artifact_id", artifactId);
  return req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/review?${params}`);
};
export const revisePsiBinsV2 = (runId, feature, body) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/revise`, {
    method: "POST", body: JSON.stringify(body),
  });
export const previewPsiBinsV2 = (runId, feature, body) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/preview`, {
    method: "POST", body: JSON.stringify(body),
  });
export const getPsiSplitOptionsV2 = (runId, feature, limit = 200) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-split-options/${encodeURIComponent(feature)}?limit=${encodeURIComponent(limit)}`);
export const freezePsiBinDraftV2 = (runId, feature, draftArtifactId) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/freeze`, {
    method: "POST", body: JSON.stringify({ draft_artifact_id: draftArtifactId }),
  });
export const approvePsiBinsBatchV2 = (runId, features) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/approve-batch`, {
    method: "POST", body: JSON.stringify({ features }),
  });
export const getPsiCandidateValuesV2 = (runId, feature) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/candidate-values`);
export const createManualPsiBinDraftV2 = (runId, feature, groups) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/manual`, {
    method: "POST", body: JSON.stringify({ groups }),
  });
export const createNumericPsiBinOverrideV2 = (runId, feature, body) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/numeric-override`, {
    method: "POST", body: JSON.stringify(body),
  });
export const promotePsiIvBinsV2 = (runId, feature, draftArtifactId) =>
  req(`/v2/diagnostics/manifests/${encodeURIComponent(runId)}/psi-bins/${encodeURIComponent(feature)}/promote-universal`, {
    method: "POST", body: JSON.stringify({ draft_artifact_id: draftArtifactId }),
  });
export const getDiagnosticBinningImpactV2 = (resultId) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/binning-impact`);
export const previewDiagnosticBinningV2 = (resultId, body) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/binning-preview`, {
    method: "POST", body: JSON.stringify(body),
  });
export const reviewDiagnosticBinningV2 = (resultId, body) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/binning-review`, {
    method: "POST", body: JSON.stringify(body),
  });
export const createNumericDiagnosticBinningOverrideV2 = (resultId, body) =>
  req(`/v2/diagnostics/results/${encodeURIComponent(resultId)}/numeric-binning-override`, {
    method: "POST", body: JSON.stringify(body),
  });
export const runAnalysisManifestV2 = (runId) =>
  req(`/v2/analyses/manifests/${encodeURIComponent(runId)}/run`, { method: "POST" });
export const getAnalysisResultsV2 = (itemId, capabilityId) =>
  req(`/v2/items/${encodeURIComponent(itemId)}/analyses/results${capabilityId ? `?capability_id=${encodeURIComponent(capabilityId)}` : ""}`);
export const dispositionAnalysisObservationV2 = (observationId, body) =>
  req(`/v2/analysis-observations/${encodeURIComponent(observationId)}/disposition`, {
    method: "POST", body: JSON.stringify(body),
  });
// Governed Analytics Artifact Repository: list responses contain metadata and
// bounded summaries only; full immutable payloads are an explicit detail read.
export const getAnalysisArtifactOverviewV2 = (filters = {}) => {
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();
  return req(`/v2/analysis-artifacts/overview${query ? `?${query}` : ""}`);
};
export const getAnalysisArtifactsV2 = (filters = {}) => {
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "" && value != null)).toString();
  return req(`/v2/analysis-artifacts${query ? `?${query}` : ""}`);
};
export const getAnalysisArtifactV2 = (artifactId) => req(`/v2/analysis-artifacts/${encodeURIComponent(artifactId)}`);
export const getAnalysisArtifactPayloadV2 = (artifactId) => req(`/v2/analysis-artifacts/${encodeURIComponent(artifactId)}/payload`);
export const getAnalysisArtifactLineageV2 = (artifactId) => req(`/v2/analysis-artifacts/${encodeURIComponent(artifactId)}/lineage`);
export const getAnalysisArtifactTypesV2 = () => req("/v2/analysis-artifacts/types");
export const getAnalysisArtifactRunsV2 = (snapshotId, capabilityId) =>
  req(`/v2/analysis-artifacts/runs?snapshot_id=${encodeURIComponent(snapshotId)}${capabilityId ? `&capability_id=${encodeURIComponent(capabilityId)}` : ""}`);
export const diagnosticReportUrlV2 = (runId, fmt = "pdf") =>
  `${API_BASE}/v2/diagnostics/runs/${encodeURIComponent(runId)}/report?fmt=${fmt}`;
// PDF report as a downloadable Blob (fetched, not navigated, so a 4xx surfaces
// a message instead of a browser error page).
export async function downloadDiagnosticReportV2(runId) {
  const res = await fetch(diagnosticReportUrlV2(runId, "pdf"), {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.blob();
}

// --- RCA Stage 1: governed business taxonomy + tags (v3) -----------------
// Dimensions/values come from the backend (never hardcoded here) so admins
// can extend the taxonomy without a frontend release.
export const getTaxonomyDimensionsV3 = () => req("/v3/taxonomy/dimensions");
export const getItemTagsV3 = (itemId) => req(`/v3/items/${encodeURIComponent(itemId)}/tags`);
export const setItemTagsV3 = (itemId, valueKeys) =>
  req(`/v3/items/${encodeURIComponent(itemId)}/tags`, {
    method: "POST", body: JSON.stringify({ value_keys: valueKeys }),
  });
export const removeItemTagV3 = (itemId, assignmentId, reason) =>
  req(`/v3/items/${encodeURIComponent(itemId)}/tags/${encodeURIComponent(assignmentId)}`, {
    method: "DELETE", body: JSON.stringify({ reason }),
  });
export const getIssueTagsV3 = (issueRowId) => req(`/v3/issues/${encodeURIComponent(issueRowId)}/tags`);
export const getResultTagsV3 = (resultId) => req(`/v3/results/${encodeURIComponent(resultId)}/tags`);
export const getTestTagsV3 = (rowId) => req(`/v3/tests/${encodeURIComponent(rowId)}/tags`);

// --- RCA Stage 2: Knowledge Base (v3) -------------------------------------
export const getKbDocumentsV3 = () => req("/v3/knowledge/documents");
export const getKbDocumentV3 = (documentId) => req(`/v3/knowledge/documents/${encodeURIComponent(documentId)}`);
export const getKbLearningCandidatesV3 = () => req("/v3/knowledge/learning-candidates");
export const getDiagnosticKbPackagesV3 = () => req("/v3/knowledge/diagnostic-packages/6");
export async function downloadDiagnosticKbTemplateV3() {
  const res = await fetch(`${API_BASE}/v3/knowledge/diagnostic-packages/6/template`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.blob();
}
export async function uploadDiagnosticKbPackageV3(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/v3/knowledge/diagnostic-packages/6/upload`, {
    method: "POST", headers: authHeaders(), body: form,
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}
export const createDiagnosticKbDraftV3 = (payload) =>
  req("/v3/knowledge/diagnostic-packages/6/drafts", {
    method: "POST", body: JSON.stringify(payload),
  });
export const activateDiagnosticKbPackageV3 = (versionId, reason) =>
  req(`/v3/knowledge/diagnostic-packages/6/${encodeURIComponent(versionId)}/activate`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
export async function uploadKbDocumentV3(file, categoryHint) {
  const form = new FormData();
  form.append("file", file);
  if (categoryHint) form.append("category_hint", categoryHint);
  const res = await fetch(`${API_BASE}/v3/knowledge/documents`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}
export const getKbVersionPreviewV3 = (versionId) => req(`/v3/knowledge/versions/${encodeURIComponent(versionId)}/preview`);
export const submitKbVersionV3 = (versionId, category) =>
  req(`/v3/knowledge/versions/${encodeURIComponent(versionId)}/submit-for-review`, {
    method: "POST", body: JSON.stringify({ category: category ?? null }),
  });
export const getKbRulesV3 = ({ lifecycleState, category, bindingStatus } = {}) => {
  const q = new URLSearchParams();
  if (lifecycleState) q.set("lifecycle_state", lifecycleState);
  if (category) q.set("category", category);
  if (bindingStatus) q.set("binding_status", bindingStatus);
  const qs = q.toString();
  return req(`/v3/knowledge/rules${qs ? `?${qs}` : ""}`);
};
export const publishKbRuleV3 = (ruleId, body) =>
  req(`/v3/knowledge/rules/${encodeURIComponent(ruleId)}/publish`, {
    method: "POST", body: JSON.stringify(body),
  });
export const archiveKbRuleV3 = (ruleId, reason) =>
  req(`/v3/knowledge/rules/${encodeURIComponent(ruleId)}/archive`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
// Phase 5 (KB-02/03/10) — dimension tags on a document, playback summary,
// and the parse report. taxonomy.py is already generic; these just target
// object_type="kb_document" server-side.
export const getKbDocumentTagsV3 = (documentId) =>
  req(`/v3/knowledge/documents/${encodeURIComponent(documentId)}/tags`);
export const setKbDocumentTagsV3 = (documentId, valueKeys) =>
  req(`/v3/knowledge/documents/${encodeURIComponent(documentId)}/tags`, {
    method: "POST", body: JSON.stringify({ value_keys: valueKeys }),
  });
export const removeKbDocumentTagV3 = (documentId, assignmentId, reason) =>
  req(`/v3/knowledge/documents/${encodeURIComponent(documentId)}/tags/${encodeURIComponent(assignmentId)}`, {
    method: "DELETE", body: JSON.stringify({ reason }),
  });
export const getKbPlaybackV3 = (versionId) =>
  req(`/v3/knowledge/versions/${encodeURIComponent(versionId)}/playback`);
export const getKbParseReportV3 = (versionId) =>
  req(`/v3/knowledge/versions/${encodeURIComponent(versionId)}/parse-report`);

// --- RCA Stage 3: vertical slice case actions -----------------------------
export const createRcaCase = (issueRowId) =>
  req(`/v3/issues/${encodeURIComponent(issueRowId)}/rca/case`, { method: "POST" });
export const getRcaCase = (caseId) => req(`/v3/rca/cases/${encodeURIComponent(caseId)}`);
export const approveRcaConclusion = (caseId, body) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/conclusion/approve`, {
    method: "POST", body: JSON.stringify(body),
  });
export const proposeRcaReusableKnowledge = (caseId, body) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/knowledge-proposal`, {
    method: "POST", body: JSON.stringify(body),
  });
export const returnRcaToInvestigation = (caseId, reason) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/conclusion/return`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
export const runRcaOpeningLook = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/opening-look`, { method: "POST" });
export const runRcaPlannerLook = (caseId, killTargetSuspectId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/planner-look`
     + (killTargetSuspectId ? `?kill_target_suspect_id=${encodeURIComponent(killTargetSuspectId)}` : ""),
     { method: "POST" });
export const runRcaLook = (lookId) =>
  req(`/v3/rca/looks/${encodeURIComponent(lookId)}/run`, { method: "POST" });
export const composeRcaHypothesis = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/compose`, { method: "POST" });
export const runRcaConfirmationCheck = (checkId) =>
  req(`/v3/rca/checks/${encodeURIComponent(checkId)}/run`, { method: "POST" });
export const proposeRcaFix = (hypothesisId) =>
  req(`/v3/rca/hypotheses/${encodeURIComponent(hypothesisId)}/propose-fix`, { method: "POST" });
export const approveRcaFix = (proposalId) =>
  req(`/v3/rca/fix-proposals/${encodeURIComponent(proposalId)}/approve`, { method: "POST" });
export const confirmRcaFixApplied = (approvalId) =>
  req(`/v3/rca/fix-approvals/${encodeURIComponent(approvalId)}/confirm-applied`, { method: "POST" });
export const closeRcaCase = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/close`, { method: "POST" });
// RCA Stage 4: budgeted loop stop -> two-pass coverage challenge -> Composer.
export const runRcaCoveragePass1 = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/coverage-challenge/pass1`, { method: "POST" });
export const runRcaCoveragePass2 = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/coverage-challenge/pass2`, { method: "POST" });
export const runRcaReopenedKillAttempt = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/reopened-kill-attempt`, { method: "POST" });
export const reviveRcaSuspect = (suspectId, reason) =>
  req(`/v3/rca/suspects/${encodeURIComponent(suspectId)}/revive`, {
    method: "POST", body: JSON.stringify({ reason }),
  });
// RCA Stage 5: one-time second chance, or escalation to Unresolved if
// already used once.
export const startRcaSecondChance = (caseId) =>
  req(`/v3/rca/cases/${encodeURIComponent(caseId)}/second-chance`, { method: "POST" });
