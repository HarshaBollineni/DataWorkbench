import { useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Play } from "lucide-react";

import { getPsiSplitOptionsV2 } from "@/api/client";
import PopulationBuilder from "@/components/analysis/PopulationBuilder";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import PsiBinningWorkspace from "./PsiBinningWorkspace";

const readinessLabel = {
  ready: "Ready with frozen bins", confirmation_required: "Reviewed bins available",
  generation_available: "Draft bins can be generated", review_required: "Draft bins require review",
  decision_required: "Binning decision required", manual_required: "Manual grouping required",
  eligible: "Eligible for selection", source_required: "Binning source required",
  excluded: "Ineligible or excluded", blocked: "Waiting for scope",
};

const observedValuesLabel = (count) => count === 1
  ? "1 unique value observed · Constant in Baseline"
  : `${count} unique values observed`;

function Step({ number, title, complete, children, id }) {
  return <section id={id} tabIndex={id ? -1 : undefined} className="scroll-mt-4 rounded-lg border border-slate-200 bg-white p-4 outline-none">
    <header className="mb-3 flex items-center gap-3">
      <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${complete ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
        {complete ? <Check className="h-4 w-4" /> : number}
      </span>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
    </header>
    {children}
  </section>;
}

function MethodCard({ selected, disabled, title, description, note, onClick }) {
  return <button type="button" disabled={disabled} onClick={onClick}
    className={`rounded-lg border p-4 text-left disabled:cursor-not-allowed disabled:opacity-55 ${selected ? "border-dq-purple bg-dq-purple/5" : "border-slate-200 bg-white"}`}>
    <span className="flex items-center justify-between gap-3"><strong className="text-sm">{title}</strong>{selected && <Badge>Selected</Badge>}</span>
    <span className="mt-2 block text-xs text-slate-600">{description}</span>
    {note && <span className="mt-2 block text-[11px] text-slate-500">{note}</span>}
  </button>;
}

const plural = (count, singular, pluralValue = `${singular}s`) => count === 1 ? singular : pluralValue;
const schemaSide = (label, value) => value
  ? `${label}: ${value.analytical_type || "unknown"} · physical ${value.physical_type || "unknown"} · role ${value.role || "unspecified"}`
  : null;

function SchemaComparisonCard({ count, sentence, items }) {
  const content = <><strong className="text-base text-slate-900">{count}</strong><span className="mt-1 block leading-5 text-slate-600">{sentence}</span></>;
  if (!items.length) return <div className="rounded border border-slate-200 p-3">{content}</div>;
  return <details className="group rounded border border-slate-200 bg-white">
    <summary className="flex cursor-pointer list-none items-start justify-between gap-2 p-3">
      <span>{content}</span><ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-500 transition-transform group-open:rotate-90" />
    </summary>
    <div className="max-h-56 overflow-y-auto border-t border-slate-100 px-3 py-2">
      {items.map((item) => <div key={item.column} className="border-b border-slate-100 py-2 last:border-0">
        <strong className="block text-slate-900">{item.column}</strong>
        {[schemaSide("Baseline", item.baseline), schemaSide("Current", item.current)].filter(Boolean)
          .map((line) => <span key={line} className="block text-[11px] text-slate-500">{line}</span>)}
      </div>)}
    </div>
  </details>;
}

const comparisonItems = (comparison, detailKey, namesKey) => comparison?.[detailKey]?.length
  ? comparison[detailKey]
  : (comparison?.[namesKey] || []).map((column) => ({ column }));

export default function PopulationStabilityScopeGate({ run, manifest, busy, patch, runNow, reload, acceptManifest }) {
  const [comparison, setComparison] = useState("");
  const [pendingTarget, setPendingTarget] = useState(null);
  const [positiveClass, setPositiveClass] = useState(manifest.target_choice?.positive_class ?? "");
  const [selectionDraft, setSelectionDraft] = useState(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(
    manifest.binning_source_choice?.status === "confirmed" && (manifest.selected_features || []).length > 0,
  );
  const schemaComparison = manifest.schema_comparison || {};
  const frozen = run.status !== "draft";
  const context = manifest.governed_context || {};
  const target = context.target || {};
  const features = useMemo(() => manifest.feature_metadata || [], [manifest.feature_metadata]);
  const savedSelected = manifest.selected_features || [];
  const selected = selectionDraft ?? savedSelected;
  const changed = JSON.stringify(selected) !== JSON.stringify(savedSelected);
  const compareOptions = manifest.population_method?.options?.compare_snapshots?.snapshots || [];
  const availableCompareOptions = compareOptions.filter((item) => item.available);
  const routesKnown = manifest.population_ready && manifest.target_choice?.status === "confirmed";
  const effectiveTargetMode = pendingTarget || manifest.target_choice?.mode;
  const scopeFeatures = useMemo(() => features.filter((feature) => !(
    effectiveTargetMode === "saved_target" && target.column && feature.column === target.column
  )), [features, effectiveTargetMode, target.column]);
  const groups = useMemo(() => scopeFeatures.reduce((result, feature) => {
    const key = feature.bin_route?.readiness || "blocked";
    result[key] = [...(result[key] || []), feature]; return result;
  }, {}), [scopeFeatures]);
  const readinessCounts = useMemo(() => Object.fromEntries(
    Object.entries(groups).map(([key, rows]) => [key, rows.length]),
  ), [groups]);
  const featureSections = useMemo(() => {
    const eligible = scopeFeatures.filter((feature) => !["excluded", "blocked"].includes(feature.bin_route?.readiness));
    const excluded = scopeFeatures.filter((feature) => ["excluded", "blocked"].includes(feature.bin_route?.readiness));
    return [
      { key: "excluded", title: "Ineligible or excluded", rows: excluded, selectable: false },
      { key: "eligible", title: "Variables available for PSI", rows: eligible, selectable: true },
    ];
  }, [scopeFeatures]);
  const completedSteps = manifest.ready_to_run ? 6
    : manifest.binning_source_choice?.status === "confirmed" ? 5
      : savedSelected.length > 0 ? 4
        : manifest.target_choice?.status === "confirmed" ? 3
        : manifest.population_ready ? 2
          : manifest.population_method?.selected ? 1 : 0;

  const chooseMethod = async (method) => {
    const selectedMethod = manifest.population_method?.selected;
    await patch({ kind: "population_method", value: { method: selectedMethod === method ? null : method } });
    setComparison("");
  };
  const applyComparison = () => patch({ kind: "population_method", value: { method: "compare_snapshots", current_snapshot_id: comparison } });
  const previewSplit = ({ feature, expression, nullPolicy, specialPolicy }) => patch({
    kind: "population_split", feature, value: expression, null_policy: nullPolicy,
    special_policy: specialPolicy,
  });
  const confirmTarget = async (mode) => {
    setPendingTarget(mode);
    try { await patch({ kind: "target_choice", value: {
      mode, ...(mode === "saved_target" && positiveClass.trim() ? { positive_class: positiveClass.trim() } : {}),
    } }); }
    finally { setPendingTarget(null); }
  };
  const toggle = (feature) => setSelectionDraft((currentDraft) => {
    const current = currentDraft ?? savedSelected;
    return current.includes(feature) ? current.filter((name) => name !== feature) : [...current, feature];
  });
  const continueToBinning = async () => {
    if (!selected.length) return;
    const updated = changed ? await patch({ kind: "scope_selection", features: selected }) : manifest;
    setSelectionDraft(null);
    if (updated?.selected_features?.length) setWorkspaceOpen(true);
  };
  const continueToBinningSource = continueToBinning;
  const setEligibilityOverride = async (feature, enabled) => {
    const updated = await patch({ kind: "feature_eligibility_override", feature, enabled });
    const persisted = updated?.selected_features || [];
    setSelectionDraft(enabled
      ? [...new Set([...persisted, feature])]
      : persisted.filter((name) => name !== feature));
  };
  const setSplitFeatureOverride = async (feature, enabled) => {
    const updated = await patch({ kind: "split_feature_override", feature, enabled });
    const persisted = updated?.selected_features || [];
    setSelectionDraft(enabled
      ? [...new Set([...persisted, feature])]
      : persisted.filter((name) => name !== feature));
  };

  if (workspaceOpen && manifest.binning_source_choice?.status === "confirmed") {
    return <PsiBinningWorkspace manifest={manifest} busy={busy} patch={patch} reload={reload} acceptManifest={acceptManifest}
      onBack={() => setWorkspaceOpen(false)} />;
  }

  return <div className="grid gap-4" data-testid="psi-scope">
    {!frozen && <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-teal-200 bg-teal-50 px-4 py-3 text-xs text-teal-900">
      <span><strong>Draft · autosaved</strong> · {completedSteps} of 6 steps complete</span>
      <span className="text-teal-700">Last saved {new Date(manifest.updated_at || manifest.created_at || run.created_at).toLocaleString()}</span>
    </div>}
    {target.state === "unresolved" && <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">{target.message} Review the saved schema in Data Sourcing before continuing.</div>}

    <Step number="1" title="Define Baseline and Current populations" complete={manifest.population_ready}>
      <div className="grid gap-3 md:grid-cols-2">
        <MethodCard title="Split this snapshot" selected={manifest.population_method?.selected === "split_snapshot"}
          description="Create baseline and current populations from different rows in the same immutable snapshot."
          note={availableCompareOptions.length ? null : "No other Ready dataset is currently available."}
          disabled={frozen} onClick={() => chooseMethod("split_snapshot")} />
        <MethodCard title="Compare two snapshots" selected={manifest.population_method?.selected === "compare_snapshots"}
          description="Use one immutable snapshot as baseline and another as current."
          note={!availableCompareOptions.length ? "Source another dataset and reach Ready to enable this route." : `${availableCompareOptions.length} Ready current dataset candidate(s). Compatibility is checked after selection.`}
          disabled={frozen || !availableCompareOptions.length} onClick={() => chooseMethod("compare_snapshots")} />
      </div>
      {!frozen && manifest.population_method?.selected === "compare_snapshots" && availableCompareOptions.length > 0 && <div className="mt-3 flex flex-wrap gap-2">
        <div className="flex h-9 items-center rounded border border-slate-200 bg-slate-50 px-3 text-xs text-slate-600">Baseline: <strong className="ml-1 text-slate-900">{context.snapshot?.asset_name}</strong></div>
        <select className="h-9 min-w-72 rounded border border-slate-200 bg-white px-2 text-xs" value={comparison} onChange={(event) => setComparison(event.target.value)}>
          <option value="">Select current dataset…</option>{availableCompareOptions.map((item) => <option key={item.snapshot.snapshot_id} value={item.snapshot.snapshot_id}>{item.snapshot.asset_name} · {item.snapshot.snapshot_label || item.snapshot.snapshot_id}</option>)}
        </select><Button size="sm" variant="outline" disabled={!comparison || busy} onClick={applyComparison}>Compare schemas</Button>
      </div>}
    </Step>

    <Step number="2" title={manifest.population_method?.selected === "compare_snapshots" ? "Review schema comparison" : "Define a one-snapshot split"} complete={manifest.population_ready}>
      {!manifest.population_method?.selected ? <p className="text-xs text-slate-500">Choose a population method to continue.</p> : manifest.population_method?.selected === "compare_snapshots" && manifest.mode !== "two_snapshot" ? <p className="text-xs text-slate-500">Select the current dataset above, then compare schemas.</p> : manifest.mode === "one_snapshot" ? <PopulationBuilder
        features={features.filter((row) => row.role !== "Identifier" && row.role !== "Target")}
        disabled={frozen} busy={busy} preview={manifest.population_preview}
        confirmedDefinition={manifest.population_definition}
        loadOptions={(feature) => getPsiSplitOptionsV2(manifest.run_id, feature)} onPreview={previewSplit}
        baselineLabel="Baseline" currentLabel="Current" /> : <>
        <div className="mb-3 text-xs text-slate-600">Compared <strong>{schemaComparison.baseline_table}</strong> with <strong>{schemaComparison.current_table}</strong>. Expand any non-empty card to inspect its columns.</div>
        <div className="grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
          <SchemaComparisonCard count={schemaComparison.compatible?.length || 0}
            sentence={`${plural(schemaComparison.compatible?.length || 0, "column")} have matching names and compatible analytical types`}
            items={comparisonItems(schemaComparison, "compatible_details", "compatible")} />
          <SchemaComparisonCard count={schemaComparison.incompatible?.length || 0}
            sentence={`${plural(schemaComparison.incompatible?.length || 0, "column")} share names but have incompatible analytical types`}
            items={comparisonItems(schemaComparison, "incompatible_details", "incompatible")} />
          <SchemaComparisonCard count={schemaComparison.baseline_only?.length || 0}
            sentence={`${plural(schemaComparison.baseline_only?.length || 0, "column")} exist only in Baseline`}
            items={comparisonItems(schemaComparison, "baseline_only_details", "baseline_only")} />
          <SchemaComparisonCard count={schemaComparison.current_only?.length || 0}
            sentence={`${plural(schemaComparison.current_only?.length || 0, "column")} exist only in Current`}
            items={comparisonItems(schemaComparison, "current_only_details", "current_only")} />
        </div>
        <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
          <strong className="text-slate-800">How compatibility is determined.</strong> Column names must match exactly. Types are compared as numeric, categorical/text, boolean or date using the governed classification and physical data type—not Identifier, Feature or Period roles. Integer and decimal types are compatible; numeric and boolean are also accepted. Only compatible columns can be selected for PSI.
        </div>
        {!manifest.population_ready && <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800"><strong>PSI cannot run because no same-name compatible columns were found.</strong> Numeric-to-text and date-to-text overrides are not offered because failed conversions would prevent complete bin reconciliation. Correct the governed schema or choose another Current dataset.</div>}
        {schemaComparison.target_difference && <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800"><strong>Saved target roles differ.</strong> Roles do not determine type compatibility. The Baseline target governs target-aware bin generation. {manifest.target_choice?.mode === "saved_target" && !manifest.schema_difference_confirmed && <Button className="ml-2" size="sm" variant="outline" onClick={() => patch({ kind: "schema_difference_confirmation", enabled: true })}>Confirm baseline target</Button>}</div>}</>}
    </Step>

    <Step number="3" title="Target route" complete={manifest.target_choice?.status === "confirmed"}>
      {target.state === "present" && <label className="mb-3 grid max-w-md gap-1 text-xs text-slate-600">
        <span>Binary positive/event class (optional)</span>
        <input value={positiveClass} disabled={frozen || busy || !manifest.population_ready}
          placeholder="Leave blank for the deterministic default"
          onChange={(event) => setPositiveClass(event.target.value)}
          className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900" />
        <span className="text-[10px] text-slate-500">For a binary target, this choice becomes part of the exact AAR identity. Opposite event definitions never share bins.</span>
      </label>}
      {target.state === "present" ? <div className="grid gap-3 md:grid-cols-2">
        <MethodCard title={`Use saved target ${target.column}`} selected={(pendingTarget || manifest.target_choice?.mode) === "saved_target"} disabled={frozen || busy || !manifest.population_ready}
          description="Use target-aware IV binning when a compatible reviewed library is unavailable." note="This confirms the saved target; it does not change it." onClick={() => confirmTarget("saved_target")} />
        <MethodCard title="Continue without a target" selected={(pendingTarget || manifest.target_choice?.mode) === "target_free"} disabled={frozen || busy || !manifest.population_ready}
          description="Use deterministic baseline cardinality and quantile/decile binning." note="This run-level choice does not modify Data Sourcing." onClick={() => confirmTarget("target_free")} />
      </div> : target.state === "absent" ? <div className="rounded border border-emerald-200 bg-emerald-50 p-3"><p className="text-xs text-emerald-800"><strong>No saved target is required.</strong> PSI automatically uses deterministic Baseline deciles for numeric variables and the target-free categorical contract.</p></div> : null}
      {pendingTarget && <p className="mt-3 flex items-center gap-2 text-xs text-slate-500"><span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-dq-purple" />Updating bin readiness…</p>}
      {routesKnown && !pendingTarget && <div className="mt-3 flex flex-wrap gap-2">{Object.entries(readinessCounts).map(([key, value]) => <Badge key={key} variant="secondary">{readinessLabel[key] || key}: {value}</Badge>)}</div>}
    </Step>

    <Step number="4" title="Select variables for PSI" complete={savedSelected.length > 0}>
      {pendingTarget ? <p className="text-xs text-slate-500">Refreshing feature and bin recommendations for the selected route…</p> : !routesKnown ? <p className="text-xs text-slate-500">Feature routes appear after the population and target choices are confirmed.</p> : <>
        {!frozen && <div className="mb-3 flex flex-wrap items-center gap-2"><Button size="sm" variant="outline" onClick={() => setSelectionDraft(manifest.recommended_features || [])}>Suggested</Button><Button size="sm" variant="outline" onClick={() => setSelectionDraft([])}>Clear</Button><Button size="sm" disabled={!selected.length || busy} onClick={continueToBinningSource}>{changed ? "Save and continue to Step 5" : "Continue to Step 5"}<ChevronRight className="h-4 w-4" /></Button><span className={`text-xs ${changed ? "text-amber-700" : "text-emerald-700"}`}>{changed ? "Selection has unsaved changes" : `${savedSelected.length} selected and saved`}</span></div>}
        <div className="grid gap-3">{featureSections.map((section) => <section key={section.key} className="overflow-hidden rounded-md border border-slate-200 bg-slate-50"><header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2"><div><h4 className="text-xs font-semibold uppercase text-slate-600">{section.title} · {section.rows.length}</h4>{section.selectable && <p className="mt-0.5 text-[10px] text-slate-400">{section.rows.filter((feature) => selected.includes(feature.column)).length} selected</p>}</div></header><div className="max-h-80 overflow-y-auto p-2"><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{section.rows.map((feature) => {
          const route = feature.bin_route || {}; const checked = selected.includes(feature.column);
          const overrideable = route.override_available || route.workflow === "split_feature_excluded";
          const selectFeature = () => {
            if (section.selectable) toggle(feature.column);
            else if (route.workflow === "constant_feature_excluded") setEligibilityOverride(feature.column, true);
            else if (route.workflow === "split_feature_excluded") setSplitFeatureOverride(feature.column, true);
          };
          return <article key={feature.column} className={`rounded-md border p-3 text-xs ${checked ? "border-dq-purple bg-dq-purple/5" : "border-slate-200 bg-white"}`}>
            <label className={`flex items-start gap-2 ${section.selectable || overrideable ? "cursor-pointer" : "cursor-not-allowed"}`}><input type="checkbox" className="mt-0.5" checked={checked} disabled={frozen || (!section.selectable && !overrideable)} onChange={selectFeature} /><span className="min-w-0"><strong className="block truncate text-slate-900">{feature.column}</strong><span className="text-slate-500">{feature.logical_type} · {feature.role} · {observedValuesLabel(feature.distinct_count)}</span></span></label>
            {route.warning && <p className="mt-2 flex items-start gap-1 rounded border border-amber-200 bg-amber-50 p-2 text-amber-800"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{route.warning}</p>}
            {!frozen && <div className="mt-2 flex flex-wrap gap-1">
              {route.workflow === "constant_feature_excluded" && <Button size="sm" variant="outline" onClick={() => setEligibilityOverride(feature.column, true)}><AlertTriangle className="h-3 w-3" /> Include with warning</Button>}
              {route.eligibility_override && <Button size="sm" variant="ghost" onClick={() => setEligibilityOverride(feature.column, false)}>Remove override</Button>}
              {route.workflow === "split_feature_excluded" && <Button size="sm" variant="outline" onClick={() => setSplitFeatureOverride(feature.column, true)}><AlertTriangle className="h-3 w-3" /> Include with warning</Button>}
              {route.split_feature_override && <Button size="sm" variant="ghost" onClick={() => setSplitFeatureOverride(feature.column, false)}>Remove override</Button>}
            </div>}
          </article>;
        })}</div></div></section>)}</div>
        {!frozen && <div className="sticky bottom-3 z-20 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dq-purple/30 bg-white/95 px-4 py-3 shadow-lg backdrop-blur">
          <div><strong className="block text-sm text-slate-900">{selected.length} {plural(selected.length, "variable")} selected</strong><span className={`text-xs ${changed ? "text-amber-700" : "text-emerald-700"}`}>{changed ? "Save these changes to unlock the next step." : "Selection saved. You can continue to binning."}</span></div>
          <Button disabled={!selected.length || busy} onClick={continueToBinningSource}>{changed ? "Save and continue to Step 5" : "Continue to Step 5"}<ChevronRight className="h-4 w-4" /></Button>
        </div>}
      </>}
    </Step>

    <Step id="psi-step-5" number="5" title="Review automatically selected binning" complete={manifest.binning_source_choice?.status === "confirmed"}>
      {!savedSelected.length ? <p className="text-xs text-slate-500">Save at least one selected variable to prepare bins.</p> : <div className="rounded-lg border border-teal-200 bg-teal-50 p-4 text-xs text-teal-900">
        <strong className="block text-sm">No bin-matching choice is required</strong>
        <p className="mt-1">{manifest.binning_source_choice?.explanation}</p>
        <p className="mt-2 text-teal-700">Missing values remain separate. Target-free categoricals use one bin per value up to 49, then one OTHER_BASELINE bin; Current-only values use UNSEEN.</p>
        <Button className="mt-3" size="sm" disabled={busy} onClick={() => setWorkspaceOpen(true)}>Open bin review<ChevronRight className="h-4 w-4" /></Button>
      </div>}
    </Step>

    <Step number="6" title="Review and run" complete={manifest.ready_to_run}>
      <div className="grid gap-3 text-xs md:grid-cols-4"><div className="rounded border p-3"><span className="text-slate-500">Population method</span><strong className="mt-1 block">{manifest.population_method?.selected?.replaceAll("_", " ")}</strong></div><div className="rounded border p-3"><span className="text-slate-500">Target choice</span><strong className="mt-1 block">{manifest.target_choice?.mode?.replaceAll("_", " ") || "Pending"}</strong></div><div className="rounded border p-3"><span className="text-slate-500">Automatic binning route</span><strong className="mt-1 block">{manifest.binning_source_choice?.mode?.replaceAll("_", " ") || "Pending"}</strong></div><div className="rounded border p-3"><span className="text-slate-500">Variables</span><strong className="mt-1 block">{savedSelected.length} selected · {Object.keys(manifest.frozen_bins || {}).length} frozen</strong></div></div>
      <p className="mt-3 text-xs text-slate-600">Watch ≥ {manifest.thresholds.watch}; investigate ≥ {manifest.thresholds.investigate}; epsilon {manifest.epsilon}. PSI findings remain contextual and never create violations automatically.</p>
      {!!manifest.blockers?.length && <ul className="mt-3 grid gap-1 text-xs text-amber-700">{manifest.blockers.map((blocker, index) => <li key={`${blocker.code}-${blocker.feature || index}`} className="flex items-center gap-2"><ChevronRight className="h-3 w-3" />{blocker.message}</li>)}</ul>}
      {!frozen && <Button className="mt-4" disabled={busy || !manifest.ready_to_run} onClick={runNow}><Play className="h-4 w-4" /> Run PSI</Button>}
    </Step>
  </div>;
}
