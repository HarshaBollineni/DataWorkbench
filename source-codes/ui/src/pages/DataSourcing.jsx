import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft, BookOpenText, CheckCircle2, ChevronDown, Database, FileSpreadsheet, Loader2, Settings2,
} from "lucide-react";

import AgentConsole from "@/components/AgentConsole";
import AssetPicker from "@/components/AssetPicker";
import VersionDiff from "@/components/VersionDiff";
import WorkflowContextBar from "@/components/WorkflowContextBar";
import { WorkflowContextProvider, useWorkflowContext } from "@/lib/workflowContext";
import { specialValueIssues } from "@/lib/specialValueValidation";
import UploadReviewInventory from "@/pages/testlab/UploadReviewInventory";
import SourcingValidationReview from "@/pages/testlab/SourcingValidationReview";
import { useAgentStream } from "@/pages/testlab/stream";
import {
  FilePicker, SourcingProgress, StepCard, Summary, WarningsPanel,
} from "@/pages/sourcing/SourcingPresentation";
import { DICTIONARY_HEADER_FIELDS, SOURCING_STAGES } from "@/pages/sourcing/constants";
import { highConfidenceHeaderMapping, inferTaxonomyValue, periodDate } from "@/pages/sourcing/workflowHelpers";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  createUploadTargetV2, getIngestV2, getNextAssetIdV2,
  getItemTablesV2, getSupersedePreviewV2, getTaxonomyDimensionsV3, processSnapshotV2,
  profileStreamUrlV2, restoreAssetVersionV2, inspectSourceV2, uploadSourceBundleV2WithProgress,
} from "@/api/client";

const KIND_CARDS = [
  { kind: "database", title: "Database", icon: Database, text: "A multi-table source workbook." },
  { kind: "dataset", title: "Dataset", icon: FileSpreadsheet, text: "A single model-ready table." },
];

function UploadFlow({ kind, onBack, initialAsset = null }) {
  const resumeSnapshotId = initialAsset?.resumable ? initialAsset.resume_snapshot_id : "";
  const [targetMode, setTargetMode] = useState(initialAsset ? "existing" : "fresh");
  const [alias, setAlias] = useState("");
  const [timeBasis, setTimeBasis] = useState("none");
  const [nextId, setNextId] = useState(null);
  const [selectedAsset, setSelectedAsset] = useState(initialAsset);
  const [itemId, setItemId] = useState(resumeSnapshotId);
  const [file, setFile] = useState(initialAsset?.resume_has_data ? {
    name: initialAsset.resume_file_name || "Retained source", size: 0, retained: true,
  } : null);
  const [summaries, setSummaries] = useState([]);
  const [progress, setProgress] = useState(null);
  const [ingest, setIngest] = useState(null);
  const [error, setError] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [snapshotLabel, setSnapshotLabel] = useState("");
  const [periodColumn, setPeriodColumn] = useState("");
  const [periodColumns, setPeriodColumns] = useState([]);
  const [intent, setIntent] = useState(initialAsset?.resume_intent || "add_period");
  const [schemaConfirmed, setSchemaConfirmed] = useState(false);
  const [replacementConfirmed, setReplacementConfirmed] = useState(false);
  const [supersedePreview, setSupersedePreview] = useState(null);
  const [completion, setCompletion] = useState(null);
  const [committedAssetId, setCommittedAssetId] = useState(null);
  const [restoreCompareVersion, setRestoreCompareVersion] = useState(null);
  const [dictFile, setDictFile] = useState(initialAsset?.resume_dictionary_file_name ? {
    name: initialAsset.resume_dictionary_file_name, size: 0, retained: true,
  } : null);
  const [workbookDiscovery, setWorkbookDiscovery] = useState({ data: null, dictionary: null });
  const [inspecting, setInspecting] = useState(false);
  const [fileContext, setFileContext] = useState("");
  const [dataSheet, setDataSheet] = useState("");
  const [dictionarySheet, setDictionarySheet] = useState("");
  const [overviewSheet, setOverviewSheet] = useState("");
  const [delimiter, setDelimiter] = useState("");
  const [dictionaryHeaderMapping, setDictionaryHeaderMapping] = useState({});
  const [targetVariable, setTargetVariable] = useState(initialAsset?.target_variable || "");
  const [targetSelectionReviewed, setTargetSelectionReviewed] = useState(Boolean(initialAsset?.target_variable));
  const [useCase, setUseCase] = useState(initialAsset?.use_case || "");
  const [product, setProduct] = useState(initialAsset?.product || "");
  const [taxonomyDimensions, setTaxonomyDimensions] = useState([]);
  const [taxonomyError, setTaxonomyError] = useState("");
  const [taxonomyLoading, setTaxonomyLoading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [restoring, setRestoring] = useState(Boolean(resumeSnapshotId));
  const [sourceOpen, setSourceOpen] = useState(!resumeSnapshotId);
  const [inventoryRows, setInventoryRows] = useState([]);
  const [metadataConfirmed, setMetadataConfirmed] = useState(false);
  const inventoryRef = useRef(null);
  const stream = useAgentStream();
  const receiveInventoryRows = useCallback((rows) => {
    setInventoryRows(rows);
    setError((current) => /atomic source codes|confirmed special or missing values/i.test(current) ? "" : current);
  }, []);

  useEffect(() => {
    if (targetMode !== "fresh") return undefined;
    getNextAssetIdV2(kind).then(setNextId).catch(() => setNextId(null));
    return undefined;
  }, [kind, targetMode]);

  useEffect(() => {
    if (!resumeSnapshotId) return undefined;
    let live = true;
    Promise.all([getIngestV2(resumeSnapshotId), getItemTablesV2(resumeSnapshotId)])
      .then(([savedIngest, savedTables]) => {
        if (!live) return;
        setIngest(savedIngest);
        setSummaries((savedTables || []).map((table) => ({
          tab: table.table_name, rows: table.row_count, columns: table.col_count,
        })));
        const options = savedIngest.source_parsing_options || {};
        setDataSheet(options.data_sheet || "");
        setDictionarySheet(options.dictionary_sheet || "");
        setOverviewSheet(options.overview_sheet || "");
        setDelimiter(options.delimiter || "");
        setFileContext(savedIngest.file_context?.text || "");
        setDictionaryHeaderMapping(savedIngest.dictionary_inspection?.active_mapping || {});
        if (savedIngest.dictionary_inspection) {
          const inspection = savedIngest.dictionary_inspection;
          setWorkbookDiscovery((current) => ({
            ...current,
            dictionary: {
              workbook_name: initialAsset?.resume_dictionary_file_name || "Retained dictionary",
              dictionary_sheet: inspection.sheet || options.dictionary_sheet || "",
              dictionary_inspection: inspection,
              sheets: inspection.sheet ? [{ name: inspection.sheet, suggested_role: "dictionary" }] : [],
            },
          }));
        }
      })
      .catch((err) => { if (live) setError(`The staged sourcing workflow could not be reopened: ${err.message}`); })
      .finally(() => { if (live) setRestoring(false); });
    return () => { live = false; };
  }, [resumeSnapshotId, initialAsset?.resume_dictionary_file_name]);

  useEffect(() => {
    if (!itemId) return undefined;
    getItemTablesV2(itemId).then((tables) => {
      setPeriodColumns((tables || []).flatMap((table) => (table.columns || []).map((column) => String(column))));
    }).catch(() => setPeriodColumns([]));
    return undefined;
    // itemId is set (via ensureTarget) before the file upload completes, so
    // dq_item_tables is still empty the first time this fires — re-running
    // once `summaries` lands (right after the upload response, by which
    // point the tables are already written) is what actually populates it.
  }, [itemId, summaries]);

  const loadTaxonomyDimensions = useCallback(async () => {
    setTaxonomyLoading(true);
    setTaxonomyError("");
    try {
      const dimensions = await getTaxonomyDimensionsV3();
      setTaxonomyDimensions(Array.isArray(dimensions) ? dimensions : []);
    } catch (err) {
      setTaxonomyDimensions([]);
      setTaxonomyError(err.message || "The business context choices could not be loaded.");
    } finally {
      setTaxonomyLoading(false);
    }
  }, []);

  useEffect(() => {
    if (kind !== "dataset") return undefined;
    loadTaxonomyDimensions();
    return undefined;
  }, [kind, loadTaxonomyDimensions]);

  useEffect(() => {
    if (targetMode !== "existing" || !selectedAsset?.asset_id) return undefined;
    getSupersedePreviewV2(selectedAsset.asset_id).then(setSupersedePreview).catch(() => setSupersedePreview(null));
    return undefined;
  }, [targetMode, selectedAsset]);

  const invalidMatch = alias.match(/[^A-Za-z0-9_-]/);
  const aliasError = invalidMatch ? `“${invalidMatch[0]}” is not allowed; use letters, digits, _ or -.` : "";
  const targetReady = targetMode === "fresh" ? Boolean(alias) && !aliasError : Boolean(selectedAsset);
  const busy = restoring || stream.running || inspecting || ["uploading", "processing"].includes(progress?.state);
  const basis = targetMode === "fresh" ? timeBasis : selectedAsset?.time_basis;
  const dateError = basis === "period" && ((!startDate && endDate) || (startDate && !endDate) || (startDate && endDate && startDate > endDate));
  const schemaMismatch = Boolean(ingest?.schema_check && !ingest.schema_check.is_match);
  const sourceComplete = Boolean(ingest && summaries.length);
  const resumingStaged = Boolean(resumeSnapshotId);
  const resumingFresh = resumingStaged && initialAsset?.resume_intent === "fresh";
  const inferredLabel = basis === "period" && startDate && endDate ? `${startDate} → ${endDate}` : snapshotLabel;
  const useCaseOptions = useMemo(() => taxonomyDimensions.find((dimension) => dimension.key === "use_case")?.values || [], [taxonomyDimensions]);
  const productOptions = useMemo(() => taxonomyDimensions.find((dimension) => dimension.key === "product")?.values || [], [taxonomyDimensions]);
  const missingMandatory = kind === "dataset" ? [
    !useCase && "a use case", !product && "a product",
  ].filter(Boolean) : [];
  const inventorySpecialValueIssues = specialValueIssues(inventoryRows);
  const processDisabledReasons = [
    dateError && "start and end dates must be valid",
    schemaMismatch && !schemaConfirmed && "confirm the schema differences above",
    intent === "full_replacement" && supersedePreview?.snapshot_count && !replacementConfirmed && "confirm the full-replacement action above",
    basis === "none" && !snapshotLabel && "a snapshot label is required",
    kind === "dataset" && !inventoryRows.length && "wait for the profiled column definitions",
    kind === "dataset" && inventorySpecialValueIssues.length && `correct ${inventorySpecialValueIssues.length} special-value ${inventorySpecialValueIssues.length === 1 ? "issue" : "issues"} in Step 3`,
    kind === "dataset" && !metadataConfirmed && "confirm the profiled dataset information",
    ...missingMandatory.map((field) => `select ${field}`),
  ].filter(Boolean);

  useEffect(() => {
    if (sourceComplete) setSourceOpen(false);
  }, [sourceComplete]);

  useEffect(() => {
    if (targetVariable || targetSelectionReviewed || !inventoryRows.length) return;
    const candidates = inventoryRows.filter((row) => String(row.role || row.dictionary_role || "").toLowerCase() === "target");
    if (candidates.length === 1) setTargetVariable(candidates[0].column_name);
  }, [inventoryRows, targetSelectionReviewed, targetVariable]);

  useEffect(() => {
    if (!inventoryRows.length) return;
    const candidates = inventoryRows
      .filter((row) => ["period", "date"].includes(String(row.role || row.dictionary_role || "").toLowerCase()))
      .sort((left, right) => {
        const score = (row) => (/reporting|quarter|period/i.test(row.column_name) ? 3 : 0) + (String(row.dictionary_role).toLowerCase() === "period" ? 2 : 0);
        return score(right) - score(left);
      });
    const period = candidates[0];
    if (!period) return;
    setPeriodColumn((current) => current || period.column_name);
    const profile = period.profile_json || {};
    const quarterLike = /quarter|\bq[1-4]\b/i.test(`${period.column_name} ${period.description || ""} ${(profile.sample_values || []).join(" ")}`);
    setStartDate((current) => current || profile.period_bounds?.start_date || periodDate(profile.min ?? profile.sample_values?.[0], false, quarterLike));
    setEndDate((current) => current || profile.period_bounds?.end_date || periodDate(profile.max ?? profile.sample_values?.at(-1), true, quarterLike));
  }, [inventoryRows]);

  useEffect(() => {
    if (!inventoryRows.length) return;
    const corpus = [alias, selectedAsset?.display_name, fileContext, ...inventoryRows.flatMap((row) => [row.column_name, row.description, row.business_context])].join(" ");
    if (!useCase) {
      const direct = inferTaxonomyValue(useCaseOptions, corpus);
      const ifrs = /\bifrs\s*9\b/i.test(corpus) ? useCaseOptions.find((option) => /ifrs\s*9/i.test(option.label))?.label : "";
      if (ifrs || direct) setUseCase(ifrs || direct);
    }
    if (!product) {
      const direct = inferTaxonomyValue(productOptions, corpus);
      const cre = /\bcre\b|commercial\s+real\s+estate/i.test(corpus)
        ? productOptions.find((option) => /\bcre\b|commercial\s+real\s+estate/i.test(option.label))?.label
        : "";
      if (cre || direct) setProduct(cre || direct);
    }
  }, [alias, fileContext, inventoryRows, product, productOptions, selectedAsset?.display_name, useCase, useCaseOptions]);

  const selectExistingAsset = (selected) => {
    setSelectedAsset(selected);
    setTargetVariable(selected?.target_variable || "");
    setTargetSelectionReviewed(Boolean(selected?.target_variable));
    setUseCase(selected?.use_case || "");
    setProduct(selected?.product || "");
  };

  const ensureTarget = async () => {
    if (itemId) return itemId;
    const target = await createUploadTargetV2(targetMode === "fresh"
      ? { kind, alias, time_basis: timeBasis }
      : { kind, asset_id: selectedAsset.asset_id });
    setItemId(target.item_id);
    return target.item_id;
  };

  const inspectSelected = async (nextFile, role) => {
    setError("");
    if (role === "data") setFile(nextFile); else setDictFile(nextFile);
    setInspecting(true);
    try {
      const inspection = await inspectSourceV2(nextFile, role);
      setWorkbookDiscovery((current) => ({ ...current, [role]: inspection }));
      const dictionaryInspection = inspection.dictionary_inspection;
      if (dictionaryInspection) {
        setDictionaryHeaderMapping(highConfidenceHeaderMapping(dictionaryInspection));
      } else if (role === "dictionary") {
        setDictionaryHeaderMapping({});
      }
      if (role === "data") {
        setDataSheet((current) => current || inspection.data_sheet || "");
        setDictionarySheet((current) => current || inspection.dictionary_sheet || "");
      } else {
        setDictionarySheet((current) => current || inspection.dictionary_sheet || "");
      }
      setOverviewSheet((current) => current || inspection.overview_sheet || "");
      setFileContext((current) => current || inspection.file_context || "");
    } catch (err) {
      setError(`The selected ${role === "data" ? "dataset" : "dictionary"} could not be inspected: ${err.message}`);
    } finally {
      setInspecting(false);
    }
  };

  const startSourcing = async () => {
    setError("");
    if (!targetReady || !file) {
      setError(!targetReady ? (targetMode === "fresh" ? "Enter a valid alias before starting." : "Select an existing asset before starting.") : "Choose an input dataset.");
      return;
    }
    try {
      const startedAt = Date.now();
      setProgress({ state: "processing", stage: "create_target", percent: 3, completed: 0,
        total: SOURCING_STAGES.length, message: "Preparing the selected asset and staged snapshot.", startedAt });
      const id = await ensureTarget();
      const embeddedDictionary = !dictFile && Boolean(workbookDiscovery.data?.dictionary_sheet);
      setProgress({ state: "uploading", stage: "upload", percent: 10, completed: 1,
        total: SOURCING_STAGES.length, message: "Uploading the selected source files.", startedAt });
      const response = await uploadSourceBundleV2WithProgress(id, {
        dataFile: file,
        dictionaryFile: dictFile || (embeddedDictionary ? file : null),
        parsingOptions: { delimiter, data_sheet: dataSheet, dictionary_sheet: dictionarySheet, overview_sheet: overviewSheet },
        fileContext,
        dictionaryHeaderMapping,
      }, ({ pct }) => {
        setProgress((previous) => ({ ...(previous || {}),
          state: pct >= 1 ? "processing" : "uploading",
          stage: pct >= 1 ? "read_data" : "upload",
          percent: pct >= 1 ? 31 : Math.max(10, Math.round(10 + pct * 20)),
          completed: pct >= 1 ? 2 : 1,
          message: pct >= 1 ? "Upload complete; reading and validating the selected data." : `Uploading source files — ${Math.round(pct * 100)}%.`,
        }));
      });
      setSummaries(response.summary || []);
      setProgress((previous) => ({ ...(previous || {}), state: "processing", stage: "normalize_dictionary",
        percent: 48, completed: 4, message: "Source tables retained; preparing dictionary metadata." }));
      const event = await stream.run(profileStreamUrlV2(id), undefined, (streamEvent) => {
        if (!streamEvent.progress) return;
        setProgress((previous) => ({ ...(previous || {}), state: streamEvent.phase === "done" ? "done" : "processing",
          ...streamEvent.progress }));
      });
      if (event.phase === "error") {
        setError(event.thought || "The data check could not complete.");
        setIngest((previous) => ({ ...(previous || {}), status: "failed", fail_reason: event.thought }));
        setProgress((previous) => ({ ...(previous || {}), state: "error", message: event.thought || "The data check could not complete." }));
      } else {
        setIngest(event.ingest || await getIngestV2(id));
        setProgress((previous) => ({ ...(previous || {}), state: "done", stage: "complete",
          percent: 100, completed: SOURCING_STAGES.length, message: "Data foundation is ready for review." }));
      }
    } catch (err) {
      setError(typeof err.detail === "string" ? err.detail : err.message);
      setProgress((previous) => ({ ...(previous || {}), state: "error", message: err.message }));
    }
  };
  const removeDictionary = () => {
    setDictFile(null);
    setWorkbookDiscovery((current) => ({ ...current, dictionary: null }));
    setDictionaryHeaderMapping(highConfidenceHeaderMapping(workbookDiscovery.data?.dictionary_inspection));
  };

  const process = async () => {
    if (!itemId || processDisabledReasons.length) return;
    setError("");
    setProcessing(true);
    try {
      const result = await processSnapshotV2(itemId, {
        intent: targetMode === "fresh" || resumingFresh ? "fresh" : intent,
        start_date: basis === "period" ? startDate : null,
        end_date: basis === "period" ? endDate : null,
        snapshot_label: inferredLabel, period_column: periodColumn || null,
        ...(kind === "dataset" ? {
          target_variable: targetVariable || null, use_case: useCase, product,
          // Step 3 is part of the final commit contract. Sending the reviewed
          // rows with Save and Proceed avoids depending on a separate request
          // (or a mounted child ref) to publish the user's schema decisions.
          inventory_rows: inventoryRows,
        } : {}),
        schema_override_confirmed: schemaConfirmed,
        full_replacement_confirmed: replacementConfirmed,
      });
      const ingestSummary = await getIngestV2(itemId);
      setCommittedAssetId(result.dataset_family_id || null);
      setCompletion(ingestSummary.completion_summary || null);
      setIngest((previous) => ({ ...(previous || {}), ...result, ...ingestSummary, status: "ready" }));
    } catch (err) {
      setError(typeof err.detail === "string" ? err.detail : err.message);
    } finally {
      setProcessing(false);
    }
  };

  const restore = async (versionNo) => {
    try { await restoreAssetVersionV2(selectedAsset.asset_id, versionNo); }
    catch (err) { setError(err.message); }
  };

  const card = KIND_CARDS.find((entry) => entry.kind === kind);
  const sourceDictionaryInspection = workbookDiscovery.dictionary?.dictionary_inspection
    || workbookDiscovery.data?.dictionary_inspection;
  const usedDictionaryHeaders = new Set(Object.values(dictionaryHeaderMapping).filter(Boolean));
  const dictionaryMappingRequired = Boolean(sourceDictionaryInspection && !dictionaryHeaderMapping.column_name);
  const selectedTargetProfile = inventoryRows.find((row) => row.column_name === targetVariable);
  const targetCandidates = inventoryRows.filter((row) => String(row.role || row.dictionary_role || "").toLowerCase() === "target");
  const selectedTargetValues = selectedTargetProfile
    ? Object.keys(selectedTargetProfile.profile_json?.top_values || selectedTargetProfile.profile_json?.top_k || {}).slice(0, 5)
    : [];
  const periodRoleRows = inventoryRows.filter((row) => String(row.role || row.dictionary_role || "").toLowerCase() === "period");
  const constantRows = inventoryRows.filter((row) => Number(row.distinct_count || 0) === 1);
  const periodOptions = periodRoleRows.length ? periodRoleRows.map((row) => row.column_name) : periodColumns;
  const choosePeriodColumn = (column) => {
    setPeriodColumn(column);
    const row = inventoryRows.find((candidate) => candidate.column_name === column);
    if (!row) return;
    const profile = row.profile_json || {};
    const quarterLike = /quarter|\bq[1-4]\b/i.test(`${row.column_name} ${row.description || ""} ${(profile.sample_values || []).join(" ")}`);
    setStartDate(profile.period_bounds?.start_date || periodDate(profile.min ?? profile.sample_values?.[0], false, quarterLike));
    setEndDate(profile.period_bounds?.end_date || periodDate(profile.max ?? profile.sample_values?.at(-1), true, quarterLike));
  };
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><div className="rounded-md bg-amber-100 p-2 text-amber-700"><card.icon className="h-5 w-5" /></div><div><h2 className="font-semibold text-slate-950">{card.title}</h2><p className="text-sm text-slate-500">One progressive surface: sections reveal as their inputs become available.</p></div></div><Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="h-4 w-4" /> Change kind</Button></div>

      <details open={sourceOpen} onToggle={(event) => setSourceOpen(event.currentTarget.open)} className="overflow-hidden rounded-lg border border-slate-200">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-5"><span><strong id="target-heading" className="block text-slate-950">STEP 1 — Sourcing data</strong><small className="text-slate-500">Choose the destination, files, parsing controls and context.</small></span><span className="flex items-center gap-2 text-sm text-slate-500">{sourceComplete && <span className="rounded-full bg-emerald-100 px-2.5 py-1 font-medium text-emerald-700">Profiling complete</span>}<ChevronDown className={`h-4 w-4 transition-transform ${sourceOpen ? "rotate-180" : ""}`} /></span></summary>
        <div className="border-t border-slate-200 p-5">
        {resumingStaged && <div className="mt-3 rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm text-indigo-800"><strong>Continuing staged sourcing.</strong> {restoring ? "Restoring the retained files and profiling results…" : "The retained files and profiling results have been restored. Complete the remaining review, intent and storage decisions below."}</div>}

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <button type="button" disabled={Boolean(itemId)} onClick={() => setTargetMode("fresh")} className={`rounded-md border p-3 text-left disabled:opacity-60 ${targetMode === "fresh" ? "border-dq-purple bg-dq-purple/5" : "border-slate-200"}`}><span className="font-semibold">Fresh Upload</span><span className="mt-1 block text-xs text-slate-500">Create a new asset when sourcing starts.</span></button>
          <button type="button" disabled={Boolean(itemId)} onClick={() => setTargetMode("existing")} className={`rounded-md border p-3 text-left disabled:opacity-60 ${targetMode === "existing" ? "border-dq-purple bg-dq-purple/5" : "border-slate-200"}`}><span className="font-semibold">Existing</span><span className="mt-1 block text-xs text-slate-500">Add a snapshot to an active asset.</span></button>
        </div>

        {targetMode === "fresh" ? (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div><Label htmlFor="asset-alias">Alias</Label><div className="mt-1 flex h-10 items-center rounded-md border border-slate-200 bg-white"><span className="border-r border-slate-200 bg-slate-50 px-3 font-mono text-sm text-slate-500" aria-label="System ID preview">{nextId?.system_id || "…"}-</span><Input id="asset-alias" className="border-0 shadow-none focus-visible:ring-0" value={alias} onChange={(e) => setAlias(e.target.value)} disabled={Boolean(itemId)} placeholder="e.g. q2_cre_source" /></div>{aliasError && <p className="mt-1 text-xs text-red-700" role="alert">{aliasError}</p>}<p className="mt-1 text-xs text-slate-400">Preview only; the allocated system ID is assigned when sourcing starts.</p></div>
            <div><Label htmlFor="time-basis">Time basis</Label><select id="time-basis" className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={timeBasis} onChange={(e) => setTimeBasis(e.target.value)} disabled={Boolean(itemId)}><option value="period">Period</option><option value="none">No time basis</option></select><p className="mt-1 text-xs text-slate-400">Set once for this asset.</p></div>
          </div>
        ) : (
          <div className="mt-4"><Label>Existing asset</Label><p className="mb-2 text-xs text-slate-400">Selection is required before sourcing starts.</p><AssetPicker kind={kind} value={selectedAsset} onChange={selectExistingAsset} excludeRequiresReupload={false} /></div>
        )}

        <div className="mt-6 grid gap-5 lg:grid-cols-2">
          <FilePicker required file={file} onChange={(next) => inspectSelected(next, "data")} onRemove={file && !itemId ? () => { setFile(null); setWorkbookDiscovery((current) => ({ ...current, data: null })); if (!dictFile) setDictionaryHeaderMapping({}); } : undefined} disabled={busy || sourceComplete} />
          {kind === "dataset" && <FilePicker label={workbookDiscovery.data?.dictionary_sheet && !dictFile ? "Data dictionary override" : "Data dictionary"} hint={workbookDiscovery.data?.dictionary_sheet && !dictFile ? `Detected “${workbookDiscovery.data.dictionary_sheet}” in the input workbook` : "Excel, CSV, TSV, pipe or text"} file={dictFile} onChange={(next) => inspectSelected(next, "dictionary")} onRemove={dictFile && !itemId ? removeDictionary : undefined} disabled={busy || sourceComplete} />}
        </div>

        {inspecting && <p className="mt-3 flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin text-dq-purple" /> Inspecting selected file structure…</p>}
        {(workbookDiscovery.data || workbookDiscovery.dictionary) && <div className="mt-3 flex flex-wrap gap-2">{[workbookDiscovery.data, workbookDiscovery.dictionary].filter(Boolean).flatMap((inspection) => inspection.sheets || []).map((sheet, index) => <span key={`${sheet.name}-${index}`} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600"><strong>{sheet.name}</strong> · {String(sheet.suggested_role).replaceAll("_", " ")}</span>)}</div>}

        <details className="mt-5 rounded-lg border border-slate-200 bg-slate-50/60">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4"><span className="flex items-center gap-3"><BookOpenText className="h-4 w-4 text-teal-700" /><span><strong className="block text-sm text-slate-900">File context</strong><small className="text-slate-500">Optional · extracted from a workbook when available, and always editable</small></span></span><ChevronDown className="h-4 w-4 text-slate-500" /></summary>
          <div className="border-t border-slate-200 p-4"><Label htmlFor="file-context">Editable context</Label><textarea id="file-context" rows={6} maxLength={20000} value={fileContext} onChange={(e) => setFileContext(e.target.value)} disabled={sourceComplete} className="mt-1 w-full rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-700 outline-none focus:border-dq-purple" placeholder="Purpose, population, reporting scope, ownership, or other source context…" /><p className="mt-1 text-xs text-slate-400">Context is retained with this snapshot and does not override deterministic dictionary mappings.</p></div>
        </details>

        <details className="mt-4 rounded-lg border border-slate-200 bg-slate-50/60">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4"><span className="flex items-center gap-3"><Settings2 className="h-4 w-4 text-teal-700" /><span><strong className="block text-sm text-slate-900">Advanced parsing controls</strong><small className="text-slate-500">Only needed when automatic discovery is insufficient</small></span></span><ChevronDown className="h-4 w-4 text-slate-500" /></summary>
          <div className="grid gap-3 border-t border-slate-200 p-4 md:grid-cols-2 xl:grid-cols-4">
            <div><Label htmlFor="data-sheet">Data sheet</Label><Input id="data-sheet" value={dataSheet} onChange={(e) => setDataSheet(e.target.value)} disabled={sourceComplete} placeholder="Auto-detect" /></div>
            <div><Label htmlFor="dictionary-sheet">Dictionary sheet</Label><Input id="dictionary-sheet" value={dictionarySheet} onChange={(e) => setDictionarySheet(e.target.value)} disabled={sourceComplete} placeholder="Auto-detect" /></div>
            <div><Label htmlFor="overview-sheet">Overview sheet</Label><Input id="overview-sheet" value={overviewSheet} onChange={(e) => setOverviewSheet(e.target.value)} disabled={sourceComplete} placeholder="Auto-detect" /></div>
            <div><Label htmlFor="delimiter">Text delimiter</Label><Input id="delimiter" value={delimiter} onChange={(e) => setDelimiter(e.target.value)} disabled={sourceComplete} placeholder="Auto, pipe, tab, ;" /></div>
          </div>
          <section className="border-t border-slate-200 p-4">
            <h4 className="text-sm font-semibold text-slate-900">Dictionary header overrides</h4>
            <p className="mt-1 text-xs text-slate-500">{sourceDictionaryInspection ? "Detected dictionary headers are available below. Confirm or correct the automatic mapping before sourcing." : "Available after a dictionary is selected or detected in the input workbook."}</p>
            <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {DICTIONARY_HEADER_FIELDS.map((field) => {
                const selected = dictionaryHeaderMapping[field.name] || "";
                const suggestion = sourceDictionaryInspection?.fields?.find((candidate) => candidate.name === field.name);
                return <label key={field.name} className="text-sm text-slate-700">
                  <span className="font-medium">{field.label}{field.required ? " *" : ""}</span>
                  <select aria-label={`Dictionary header for ${field.label}`} value={selected} disabled={!sourceDictionaryInspection || sourceComplete} onChange={(event) => setDictionaryHeaderMapping((current) => {
                    const next = { ...current };
                    if (event.target.value) next[field.name] = event.target.value;
                    else delete next[field.name];
                    return next;
                  })} className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm disabled:bg-slate-100 disabled:text-slate-400">
                    <option value="">{field.required ? "Select source header" : "Auto-map / not present"}</option>
                    {(sourceDictionaryInspection?.source_columns || []).map((header) => <option key={`${field.name}-${header}`} value={header} disabled={usedDictionaryHeaders.has(header) && header !== selected}>{header}</option>)}
                  </select>
                  {suggestion?.confidence === "medium" && suggestion.source_column && !selected && <span className="mt-1 block text-xs text-dq-purple">Suggested: {suggestion.source_column}</span>}
                  {field.note && <span className="mt-1 block text-xs text-slate-500">{field.note}</span>}
                </label>;
              })}
            </div>
            {dictionaryMappingRequired && <p className="mt-3 text-xs text-amber-700">Select the dictionary header containing the dataset column names, or remove the optional dictionary.</p>}
          </section>
        </details>

        <SourcingProgress progress={progress} fileName={file?.name} />

        <div className="mt-5 flex items-center justify-between gap-4 border-t border-slate-200 pt-4"><p className="text-xs text-slate-500"><Database className="mr-1 inline h-4 w-4" /> Files are retained through the existing DataWorkbench snapshot and dictionary-version model.</p><Button type="button" onClick={startSourcing} disabled={!targetReady || !file || dictionaryMappingRequired || busy || sourceComplete}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}{sourceComplete ? "Source profiling complete" : resumingStaged ? "Continue sourcing" : "Start sourcing"}</Button></div>
        </div>
      </details>

      <Summary kind={kind} summaries={summaries} />
      {sourceComplete && <StepCard step="2" title="Review data validations" subtitle="Deterministic reconciliation, generated rules and the observed profile."><SourcingValidationReview ingest={ingest} inventory={inventoryRows} summaries={summaries} />{schemaMismatch && <section className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4"><p className="font-semibold text-amber-950">Schema differences require confirmation</p><ul className="mt-2 list-disc pl-5 text-sm text-amber-900">{(ingest.schema_check.messages || []).map((message) => <li key={message}>{message}</li>)}</ul><p className="mt-3 text-sm text-amber-900">{intent === "full_replacement" ? "The replacement will establish a new reference schema for future uploads." : "Comparability checks may skip columns whose definitions differ."}</p><label className="mt-3 flex items-start gap-2 text-sm text-amber-950"><input type="checkbox" checked={schemaConfirmed} onChange={(event) => setSchemaConfirmed(event.target.checked)} />I confirm that I reviewed these schema differences.</label></section>}</StepCard>}

      {sourceComplete && <StepCard step="3" title="Normalize column definitions" subtitle="Review types, roles, valid values and special-value handling."><UploadReviewInventory ref={inventoryRef} item={{ item_id: itemId, kind }} mapping={ingest?.mapping || []} deferSave onRowsLoaded={receiveInventoryRows} onSaved={async () => setIngest(await getIngestV2(itemId))} /></StepCard>}

      {sourceComplete && <StepCard step="4" title="Confirm dataset information" subtitle="Dictionary and profiling evidence preloads the target, period and business context." testId="upl-step-4">
        {kind === "dataset" && <section className="rounded-lg border border-teal-200 bg-emerald-50/40 p-4"><h4 className="font-semibold text-teal-950">Dictionary and profile intelligence</h4><div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4"><div><span className="block text-xs uppercase text-slate-500">Target candidates</span><strong>{targetCandidates.length ? targetCandidates.map((row) => `${row.column_name} (${Number(row.distinct_count || 0).toLocaleString()} levels)`).join(", ") : "None identified"}</strong></div><div><span className="block text-xs uppercase text-slate-500">Observed period</span><strong>{periodColumn ? `${periodColumn} · ${startDate || "?"} → ${endDate || "?"}` : "None identified"}</strong></div><div><span className="block text-xs uppercase text-slate-500">Business context</span><strong>{[useCase, product].filter(Boolean).join(" · ") || "No taxonomy match"}</strong></div><div><span className="block text-xs uppercase text-slate-500">Constant features</span><strong>{constantRows.length ? `${constantRows.length} recommended Ignore` : "None"}</strong></div></div><p className="mt-3 text-xs text-teal-800">Suggestions combine dictionary roles and descriptions, retained file context, observed n-levels, unique values, and profiled date bounds. Review every selection below before confirming.</p></section>}
        {targetMode === "existing" && !resumingFresh && <div className="mt-3"><Label>Intent</Label><div className="mt-1 flex flex-wrap gap-4 text-sm"><label><input type="radio" name="intent" checked={intent === "add_period"} onChange={() => setIntent("add_period")} /> Add period</label><label><input type="radio" name="intent" checked={intent === "full_replacement"} onChange={() => setIntent("full_replacement")} /> Full replacement</label></div></div>}
        {basis === "period" ? <div className="mt-3 grid gap-3 md:grid-cols-3"><div><Label htmlFor="start-date">Start date</Label><Input id="start-date" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></div><div><Label htmlFor="end-date">End date</Label><Input id="end-date" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></div><div><Label htmlFor="snapshot-label">Snapshot label</Label><Input id="snapshot-label" value={snapshotLabel} onChange={(e) => setSnapshotLabel(e.target.value)} placeholder={startDate && endDate ? `${startDate} → ${endDate}` : "Period label"} /></div></div> : <div className="mt-3 max-w-md"><Label htmlFor="snapshot-label">Snapshot label</Label><Input id="snapshot-label" value={snapshotLabel} onChange={(e) => setSnapshotLabel(e.target.value)} placeholder="Required within this asset" /></div>}
        {dateError && <p className="mt-2 text-xs text-red-700" role="alert">Start and end dates are required together, and start must not be after end.</p>}
        <div className="mt-3 max-w-md"><Label htmlFor="period-column">Reporting period column (optional)</Label><select id="period-column" className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={periodColumn} onChange={(e) => choosePeriodColumn(e.target.value)}><option value="">None / Not applicable</option>{periodOptions.map((column) => <option key={column} value={column}>{column}</option>)}</select><p className="mt-1 text-xs text-slate-400">Columns assigned the Period role are prioritized. Observed quarter/year values populate the date range.</p></div>
        {kind === "dataset" && <><div className="mt-4 grid gap-3 md:grid-cols-3">
          <div><Label htmlFor="target-variable">Target variable (optional)</Label><select id="target-variable" className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={targetVariable} onChange={(e) => { setTargetVariable(e.target.value); setTargetSelectionReviewed(true); setMetadataConfirmed(false); }}><option value="">No target selected</option>{inventoryRows.map((row) => <option key={row.column_name} value={row.column_name}>{row.column_name} · {Number(row.distinct_count || 0).toLocaleString()} levels</option>)}</select><p className="mt-1 text-xs text-slate-400">{targetCandidates.length ? `${targetCandidates.length} optional target candidate${targetCandidates.length === 1 ? "" : "s"} identified from normalized roles. Clear the selection if no target should be confirmed.` : "No target role was detected. You can proceed without one and confirm it later if required by a diagnostic."}</p></div>
          <div><Label htmlFor="use-case">Use case</Label><Select value={useCase} onValueChange={(value) => { setUseCase(value); setMetadataConfirmed(false); }} disabled={taxonomyLoading || !useCaseOptions.length}><SelectTrigger id="use-case" className="mt-1 h-10" aria-label="Use case"><SelectValue placeholder={taxonomyLoading ? "Loading use cases…" : "Select a use case…"} /></SelectTrigger><SelectContent>{useCaseOptions.map((value) => <SelectItem key={value.key} value={value.label}>{value.label}</SelectItem>)}</SelectContent></Select></div>
          <div><Label htmlFor="product">Product</Label><Select value={product} onValueChange={(value) => { setProduct(value); setMetadataConfirmed(false); }} disabled={taxonomyLoading || !productOptions.length}><SelectTrigger id="product" className="mt-1 h-10" aria-label="Product"><SelectValue placeholder={taxonomyLoading ? "Loading products…" : "Select a product…"} /></SelectTrigger><SelectContent>{productOptions.map((value) => <SelectItem key={value.key} value={value.label}>{value.label}</SelectItem>)}</SelectContent></Select></div>
        </div>{taxonomyError && <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="alert"><span>Use Case and Product choices could not be loaded.</span><Button type="button" size="sm" variant="outline" onClick={loadTaxonomyDimensions} disabled={taxonomyLoading}>Retry</Button></div>}<div className="mt-4 rounded-lg border border-teal-200 bg-emerald-50/50 p-4">{selectedTargetProfile ? <div className="grid gap-3 text-sm sm:grid-cols-4"><div><span className="block text-xs uppercase text-slate-500">Target</span><strong>{selectedTargetProfile.column_name}</strong></div><div><span className="block text-xs uppercase text-slate-500">Observed type</span><strong>{selectedTargetProfile.inferred_type || selectedTargetProfile.classification}</strong></div><div><span className="block text-xs uppercase text-slate-500">N-levels</span><strong>{Number(selectedTargetProfile.distinct_count || 0).toLocaleString()}</strong></div><div><span className="block text-xs uppercase text-slate-500">Unique value sample</span><strong className="text-xs">{(selectedTargetValues.length ? selectedTargetValues : (selectedTargetProfile.sample_values || []).slice(0, 5).map(String)).join(", ") || "Not retained"}</strong></div></div> : <p className="text-sm text-teal-900"><strong>No target selected.</strong> The schema can still be saved and used in Test Lab; target-dependent diagnostics will remain unavailable until a target is confirmed.</p>}<label className="mt-4 flex items-start gap-2 text-sm text-teal-900"><input type="checkbox" checked={metadataConfirmed} onChange={(event) => setMetadataConfirmed(event.target.checked)} />I confirm this target selection (or no target), reporting period, use case, product, and their profiled interpretation for downstream assessment.</label></div></>}
        {intent === "full_replacement" && supersedePreview?.snapshot_count > 0 && <div className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm"><p className="font-semibold text-amber-900">This will supersede {supersedePreview.description}.</p><label className="mt-2 flex items-start gap-2"><input type="checkbox" checked={replacementConfirmed} onChange={(e) => setReplacementConfirmed(e.target.checked)} /> <span>I understand this distinct full-replacement action.</span></label></div>}
      </StepCard>}
      {sourceComplete && <StepCard step="5" title="Save and proceed" subtitle="Save normalized definitions and promote the snapshot to Test Lab." testId="upl-step-5"><p className="text-sm text-slate-500">This saves the normalized column definitions and storage decisions together, then makes the snapshot available in Test Lab.</p><div className="mt-4 flex flex-wrap items-center gap-3"><Button disabled={Boolean(completion) || Boolean(processDisabledReasons.length) || processing} onClick={process}>{processing && <Loader2 className="h-4 w-4 animate-spin" />}{processing ? "Saving and proceeding…" : completion ? "Saved and ready" : "Save and Proceed"}</Button>{completion && committedAssetId && <Link to={`/test-lab?asset=${encodeURIComponent(committedAssetId)}`} className="inline-flex h-10 items-center rounded-md bg-dq-purple px-4 text-sm font-medium text-white hover:bg-dq-purple/90">Go to Test Lab</Link>}</div>{!completion && !processing && processDisabledReasons.length > 0 && <p className="mt-2 text-xs text-slate-500">Before you can proceed: {processDisabledReasons.join("; ")}.</p>}{ingest.overlap_warnings?.map((warning) => <p key={warning} className="mt-2 text-sm text-amber-700">{warning}</p>)}</StepCard>}
      {completion && <details data-testid="completion-summary" className="mt-5 overflow-hidden rounded-md border border-emerald-200 bg-emerald-50"><summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4"><h3 className="font-semibold text-emerald-950">Completion summary</h3><span className="text-xs font-medium text-emerald-800">Stored successfully · View details</span></summary><pre className="border-t border-emerald-200 p-4 whitespace-pre-wrap text-xs text-emerald-900">{JSON.stringify(completion, null, 2)}</pre></details>}
      {selectedAsset?.superseded_snapshot_count > 0 && <div className="mt-4 text-sm"><span>View / restore previous version:</span>{(selectedAsset.superseded_versions || []).map((version) => <span key={version} className="ml-2"><button type="button" className="text-dq-purple underline" onClick={() => restore(version)}>Restore v{version}</button><button type="button" className="ml-2 text-dq-purple underline" onClick={() => setRestoreCompareVersion(restoreCompareVersion === version ? null : version)}>Compare</button></span>)}{restoreCompareVersion && <VersionDiff assetId={selectedAsset.asset_id} versionA={restoreCompareVersion} versionB={selectedAsset.current_version_no} />}</div>}
      {ingest && <WarningsPanel warnings={ingest.warnings} />}
      {error && <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <div className="mt-4"><AgentConsole events={stream.events} running={stream.running} /></div>
      {ingest?.status === "ready" && <p className="mt-4 flex items-center gap-2 text-sm text-emerald-700"><CheckCircle2 className="h-4 w-4" /> Ready — warnings remain reviewable and never block the data check.</p>}
    </section>
  );
}

function SourcingScreen() {
  const [kind, setKind] = useState("");
  const [pickerKind, setPickerKind] = useState("");
  const [startAsset, setStartAsset] = useState(null);
  const { asset, setAsset } = useWorkflowContext();
  const chooseExisting = (nextKind) => setPickerKind((current) => current === nextKind ? "" : nextKind);
  const changeContext = () => { setKind(""); setStartAsset(null); setPickerKind(asset?.kind || "dataset"); };
  const openAsset = (selected, nextKind) => {
    setAsset(selected);
    setStartAsset(selected);
    setKind(nextKind);
    setPickerKind("");
  };
  const addNew = (nextKind) => {
    setStartAsset(null);
    setKind(nextKind);
    setPickerKind("");
  };
  const backToKinds = () => {
    setKind("");
    setStartAsset(null);
  };
  return <main className="min-h-screen bg-slate-50 p-8"><WorkflowContextBar onChange={changeContext} /><div className="mb-8"><h1 className="text-2xl font-bold text-slate-950">Data Sourcing</h1><p className="mt-1 text-sm text-slate-500">Choose a source kind, then work down one progressive upload surface.</p></div>{!kind ? <div className="grid gap-5 xl:grid-cols-2">{KIND_CARDS.map(({ kind: nextKind, title, icon: Icon, text }) => <section key={nextKind} className="rounded-lg border border-slate-200 bg-white p-6"><div className="flex items-center gap-3"><div className="rounded-md bg-amber-100 p-2 text-amber-700"><Icon className="h-5 w-5" /></div><h2 className="text-lg font-semibold text-slate-950">{title}</h2></div><p className="mt-3 text-sm text-slate-600">{text}</p><div className="mt-5 flex flex-wrap gap-2"><button type="button" className="rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:border-dq-purple" onClick={() => chooseExisting(nextKind)}>View Existing</button><button type="button" className="rounded-md bg-dq-purple px-3 py-2 text-sm font-medium text-white hover:bg-dq-purple/90" onClick={() => addNew(nextKind)}>Add New</button></div>{pickerKind === nextKind && <div className="relative z-10 mt-3 rounded-md border border-dq-purple/30 bg-slate-50 p-2" data-testid="existing-popover"><AssetPicker kind={nextKind} value={asset?.kind === nextKind ? asset : null} onChange={(selected) => openAsset(selected, nextKind)} excludeRequiresReupload={true} allowResumable /></div>}</section>)}</div> : <UploadFlow key={`${kind}:${startAsset?.resume_snapshot_id || startAsset?.asset_id || "new"}`} kind={kind} initialAsset={startAsset} onBack={backToKinds} />}</main>;
}

export default function DataSourcing() {
  return <WorkflowContextProvider><SourcingScreen /></WorkflowContextProvider>;
}
