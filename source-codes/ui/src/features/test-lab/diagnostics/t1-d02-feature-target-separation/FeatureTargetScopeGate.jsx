import { useState } from "react";
import { Check, ChevronDown, Lock, Play, SlidersHorizontal, Target } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

function NumberSetting({ label, settingKey, spec, onSave, min, max, step = 0.01, disabled }) {
  const [value, setValue] = useState(spec?.value ?? "");
  return (
    <label className="grid gap-1 text-xs text-slate-600">
      <span>{label}</span>
      <input type="number" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onBlur={() => Number(value) !== Number(spec?.value) && onSave(settingKey, Number(value))}
        className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900" />
      <span className="text-[10px] text-slate-400">{spec?.source}</span>
    </label>
  );
}

export default function FeatureTargetScopeGate({ run, manifest, busy, patch, runNow }) {
  const frozen = run.status !== "draft";
  const [selectionDraft, setSelectionDraft] = useState(null);
  const [positiveClass, setPositiveClass] = useState(manifest.target.positive_class ?? "");
  const features = manifest.scope.feature_metadata || [];
  const eligible = manifest.scope.eligible_features || [];
  const selected = selectionDraft || manifest.scope.selected_features || [];
  const recommended = manifest.scope.recommended_features || eligible;
  const completed = manifest.execution?.completed_exact || {};
  const runnableSelected = selected.filter((name) => !completed[name]);

  const toggle = (feature) => setSelectionDraft((draft) => {
    const current = draft || manifest.scope.selected_features || [];
    return current.includes(feature)
    ? current.filter((name) => name !== feature)
    : [...current, feature];
  });
  const changed = JSON.stringify(selected) !== JSON.stringify(manifest.scope.selected_features || []);
  const saveFeatures = async () => {
    await patch({ kind: "feature_selection", features: selected });
    setSelectionDraft(null);
  };
  const tuneThreshold = (key, value) => patch({ kind: "threshold_tune", key, value });
  const tuneParameter = (key, value) => patch({ kind: "parameter_tune", key, value });

  return (
    <div className="grid gap-4" data-testid="feature-target-scope">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <h2 className="font-semibold text-slate-950">{manifest.diagnostic.name} · scope gate</h2>
          <p className="text-xs text-slate-500">{manifest.item_name} · run {manifest.run_id}</p>
        </div>
        {frozen ? (
          <Badge variant="secondary" className="gap-1"><Lock className="h-3 w-3" /> {run.status}</Badge>
        ) : (
          <Button onClick={runNow} disabled={busy || !runnableSelected.length || changed}>
            <Play className="h-4 w-4" /> Run diagnostic
          </Button>
        )}
      </div>

      <details defaultOpen className="group rounded-lg border border-slate-200 bg-white p-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <Target className="h-4 w-4 text-dq-purple" /> Target and analysis settings
          </span>
          <ChevronDown className="h-4 w-4 text-slate-500 transition-transform group-open:rotate-180" />
        </summary>
        <div className="mt-4 grid gap-5 border-t border-slate-100 pt-4">
          <div className="grid gap-3 md:grid-cols-4">
          <div className="rounded-md border border-slate-100 bg-slate-50 p-3">
            <p className="text-[11px] uppercase text-slate-400">Confirmed target</p>
            <p className="mt-1 text-sm font-semibold text-slate-900">{manifest.target.table}.{manifest.target.column}</p>
            <p className="mt-1 text-[11px] text-slate-500">{manifest.target.source}</p>
          </div>
          <label className="grid gap-1 text-xs text-slate-600">
            <span>Target interpretation</span>
            <select value={manifest.target.target_type} disabled={frozen || busy}
              onChange={(event) => tuneParameter("target_type", event.target.value)}
              className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900">
              <option value="auto">Auto-detect</option>
              <option value="binary">Binary</option>
              <option value="continuous">Continuous</option>
              <option value="multinomial">Multinomial</option>
            </select>
          </label>
          <label className="grid gap-1 text-xs text-slate-600">
            <span>Positive/event class</span>
            <input value={positiveClass} disabled={frozen || busy}
              placeholder="Auto: higher sorted label"
              onChange={(event) => setPositiveClass(event.target.value)}
              onBlur={() => {
                const value = positiveClass.trim() || null;
                if (value !== manifest.target.positive_class) tuneParameter("positive_class", value);
              }}
              className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900" />
            <span className="text-[10px] text-slate-400">For binary targets. Reversing this class creates a distinct governed AAR route.</span>
          </label>
          <label className="grid gap-1 text-xs text-slate-600">
            <span>Missing target rows</span>
            <select value={manifest.target.missing_target_action} disabled={frozen || busy}
              onChange={(event) => tuneParameter("missing_target_action", event.target.value)}
              className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900">
              <option value="drop">Exclude and report</option>
              <option value="prompt">Require review</option>
            </select>
          </label>
        </div>
          <div>
            <div className="mb-2 flex items-center gap-2">
              <SlidersHorizontal className="h-4 w-4 text-slate-500" />
              <h4 className="text-xs font-semibold uppercase text-slate-500">Review thresholds</h4>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <NumberSetting key={`leakage_auc-${manifest.thresholds.leakage_auc.value}`} label="Leakage AUC" settingKey="leakage_auc" spec={manifest.thresholds.leakage_auc} min={0.5} max={1} onSave={tuneThreshold} disabled={frozen || busy} />
              <NumberSetting key={`leakage_iv-${manifest.thresholds.leakage_iv.value}`} label="Leakage IV" settingKey="leakage_iv" spec={manifest.thresholds.leakage_iv} min={0} onSave={tuneThreshold} disabled={frozen || busy} />
              <NumberSetting key={`poor_auc-${manifest.thresholds.poor_auc.value}`} label="Poor AUC" settingKey="poor_auc" spec={manifest.thresholds.poor_auc} min={0.5} max={1} onSave={tuneThreshold} disabled={frozen || busy} />
              <NumberSetting key={`poor_iv-${manifest.thresholds.poor_iv.value}`} label="Poor IV" settingKey="poor_iv" spec={manifest.thresholds.poor_iv} min={0} onSave={tuneThreshold} disabled={frozen || busy} />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">Feature groups</h4>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {["suspicious_auc", "suspicious_iv", "strong_auc", "strong_iv", "medium_auc", "medium_iv"].map((key) => (
                <NumberSetting key={`${key}-${manifest.thresholds[key].value}`} label={key.replaceAll("_", " ")} settingKey={key}
                  spec={manifest.thresholds[key]} min={key.endsWith("auc") ? 0.5 : 0}
                  max={key.endsWith("auc") ? 1 : undefined} onSave={tuneThreshold} disabled={frozen || busy} />
              ))}
            </div>
          </div>
        </div>
      </details>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Independent variables</h3>
            <p className="text-xs text-slate-500">{selected.length} selected · {runnableSelected.length} require execution · target automatically excluded</p>
          </div>
          {!frozen && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setSelectionDraft(recommended)}>Recommended</Button>
              <Button size="sm" variant="outline" onClick={() => setSelectionDraft(eligible)}>All</Button>
              <Button size="sm" variant="outline" onClick={() => setSelectionDraft([])}>Clear</Button>
              <Button size="sm" variant="outline" disabled={!changed || !selected.length || busy} onClick={saveFeatures}>Save selection</Button>
            </div>
          )}
        </div>
        <p className="mb-2 text-xs text-slate-500">
          Schema roles set the diagnostic recommendation only. Every variable below remains selectable; hover a non-recommended feature for its reason.
        </p>
        <div className="grid max-h-72 gap-2 overflow-y-auto rounded-md border border-slate-200 bg-slate-50 p-2 sm:grid-cols-2 xl:grid-cols-4">
          {features.map((feature) => {
            const checked = selected.includes(feature.column);
            const prior = feature.completed_exact;
            return (
              <label key={feature.column} title={feature.recommendation_reason || "Recommended for this diagnostic"}
                className={`flex min-h-16 min-w-0 cursor-pointer items-center gap-2 rounded-md border p-3 ${checked ? "border-dq-purple bg-dq-purple/5" : "border-slate-200 bg-white"}`}>
                <input type="checkbox" checked={checked} disabled={frozen || busy}
                  onChange={() => toggle(feature.column)} className="sr-only" />
                <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${checked ? "border-dq-purple bg-dq-purple text-white" : "border-slate-300"}`}>
                  {checked && <Check className="h-3 w-3" />}
                </span>
                <span className="min-w-0"><strong className="block break-words text-xs text-slate-800">{feature.column}</strong><small className="text-[10px] text-slate-500">{feature.classification || feature.data_type || "Unknown"} · {feature.role}{prior ? " · complete for these settings" : ""}</small></span>
              </label>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-slate-500">{eligible.length} eligible of {features.length} available variables{!runnableSelected.length && selected.length ? " · selected results already exist; change settings or view Findings" : ""}</p>
      </section>

    </div>
  );
}
