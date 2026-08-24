import { useMemo, useState } from "react";
import { AlertTriangle, Database, LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

function Distribution({ histogram = [] }) {
  const maximum = Math.max(...histogram.map((row) => Number(row.count || 0)), 1);
  const total = histogram.reduce((sum, row) => sum + Number(row.count || 0), 0);
  if (!histogram.length) return null;
  return <div className="mt-3">
    <div className="flex h-16 items-end gap-1" aria-label="Profile distribution">
      {histogram.map((row, index) => <div key={`${row.start}-${index}`} className="min-w-2 flex-1 rounded-t bg-dq-purple/35"
        style={{ height: `${Math.max(6, Number(row.count || 0) / maximum * 100)}%` }}
        title={`Minimum: ${row.start}\nMaximum: ${row.end}\nCount: ${Number(row.count || 0).toLocaleString()}\nShare: ${total ? (Number(row.count || 0) / total * 100).toFixed(1) : "0.0"}%`} />)}
    </div>
    <div className="mt-1 flex justify-between text-[10px] text-slate-400"><span>{histogram[0]?.start}</span><span>{histogram.at(-1)?.end}</span></div>
  </div>;
}

function SplitLogic({ definition, baselineLabel, currentLabel }) {
  if (!definition?.split_feature || !definition?.expression) return null;
  const feature = definition.split_feature;
  const expression = definition.expression;
  let baselineRule;
  let currentRule;
  if (expression.operator === "in") {
    const values = (expression.values || []).join(", ");
    baselineRule = `${feature} is one of: ${values}`;
    currentRule = `${feature} is any other value`;
  } else if (expression.operator === "range") {
    baselineRule = `${feature} is between ${expression.lower} and ${expression.upper} (inclusive)`;
    currentRule = `${feature} is outside that range`;
  } else {
    const symbol = { "<=": "≤", "<": "<", "=": "=", ">=": "≥", ">": ">" }[expression.operator] || expression.operator;
    const complement = { "<=": ">", "<": "≥", "=": "≠", ">=": "<", ">": "≤" }[expression.operator] || "does not match";
    baselineRule = `${feature} ${symbol} ${expression.value}`;
    currentRule = `${feature} ${complement} ${expression.value}`;
  }
  const nullRule = definition.null_policy === "baseline" ? baselineLabel
    : definition.null_policy === "current" ? currentLabel : "Excluded";
  const specialRule = definition.special_policy === "baseline" ? baselineLabel
    : definition.special_policy === "current" ? currentLabel : "Excluded";
  return <div className="mb-3 rounded border border-emerald-200 bg-emerald-50 p-3">
    <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700">Confirmed split logic</p>
    <div className="mt-2 grid gap-2 sm:grid-cols-2"><p><strong>{baselineLabel}:</strong> {baselineRule}</p><p><strong>{currentLabel}:</strong> {currentRule}</p></div>
    <p className="mt-2 text-[11px] text-emerald-800">Null values: {nullRule}{definition.special_values?.length ? ` · Confirmed special values (${definition.special_values.join(", ")}): ${specialRule}` : ""}</p>
  </div>;
}

export default function PopulationBuilder({ features, disabled, busy, preview, loadOptions, onPreview,
  confirmedDefinition, baselineLabel = "Baseline", currentLabel = "Current" }) {
  const [feature, setFeature] = useState("");
  const [options, setOptions] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedValues, setSelectedValues] = useState([]);
  const [search, setSearch] = useState("");
  const [numericMode, setNumericMode] = useState("cutoff");
  const [direction, setDirection] = useState("up_to");
  const [boundary, setBoundary] = useState("");
  const [lower, setLower] = useState("");
  const [upper, setUpper] = useState("");
  const nullPolicy = "baseline";
  const [specialPolicy, setSpecialPolicy] = useState("exclude");

  const chooseFeature = async (name) => {
    setFeature(name); setOptions(null); setSelectedValues([]); setBoundary(""); setError("");
    if (!name) return;
    setLoading(true);
    try {
      const value = await loadOptions(name);
      setOptions(value);
      if (value.suggestions?.length) setBoundary(String(value.suggestions[Math.floor(value.suggestions.length / 2)]).slice(0, value.input_kind === "date" ? 10 : undefined));
    } catch (reason) {
      setError(reason.message || "Unable to load population options.");
    } finally { setLoading(false); }
  };
  const values = useMemo(() => (options?.exact_values || []).filter((row) =>
    !search || row.value.toLowerCase().includes(search.toLowerCase())), [options, search]);
  const categorical = options?.strategy === "category_groups";
  const temporal = options?.strategy === "temporal_cutoff";
  const expression = () => {
    if (categorical) return { operator: "in", values: selectedValues };
    if (numericMode === "range" && !temporal) return { operator: "range", lower, upper,
      lower_inclusive: true, upper_inclusive: true };
    return { operator: direction === "up_to" ? "<=" : ">", value: boundary };
  };
  const canPreview = feature && options && (categorical ? selectedValues.length > 0
    : numericMode === "range" && !temporal ? lower !== "" && upper !== "" : boundary !== "");

  return <div className="grid gap-3">
    <div className="grid gap-2 md:grid-cols-[minmax(15rem,1fr)_auto]">
      <select className="h-9 rounded border border-slate-200 bg-white px-2 text-xs" value={feature}
        disabled={disabled} onChange={(event) => chooseFeature(event.target.value)}>
        <option value="">Select a split variable…</option>
        {features.map((row) => <option key={row.column} value={row.column}>{row.column} · {row.feature_kind} · {row.distinct_count} values</option>)}
      </select>
      <div className="flex h-9 items-center rounded border border-slate-200 bg-slate-50 px-3 text-xs text-slate-600">Nulls retained in {baselineLabel} (separate missing-value bin)</div>
    </div>

    {loading && <p className="flex items-center gap-2 text-xs text-slate-500"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />Loading governed profile and exact options…</p>}
    {error && <p className="text-xs text-red-700">{error}</p>}
    {options && <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><strong className="text-xs text-slate-900">{options.recommendation}</strong>
          <p className="mt-1 text-[11px] text-slate-500">{options.distinct_count} distinct · {options.null_count} null · range {String(options.min ?? "—")} to {String(options.max ?? "—")}</p></div>
        <Badge variant="outline"><Database className="mr-1 h-3 w-3" />{options.source?.artifact_id ? "Artifact guided" : "Profile fallback"}</Badge>
      </div>

      {categorical ? <div className="mt-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-slate-600">Select values for {baselineLabel}; all remaining values form {currentLabel}.</p>
          <input className="h-8 rounded border border-slate-200 bg-white px-2 text-xs" placeholder="Search values" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
        <div className="max-h-56 overflow-auto rounded border border-slate-200 bg-white p-2">
          <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">{values.map((row) => <label key={row.value} className="flex items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-slate-50">
            <input type="checkbox" checked={selectedValues.includes(row.value)} onChange={() => setSelectedValues((current) => current.includes(row.value) ? current.filter((value) => value !== row.value) : [...current, row.value])} />
            <span className="min-w-0 flex-1 truncate">{row.value}</span><span className="text-slate-400">{row.count} · {(row.share * 100).toFixed(1)}%</span>
          </label>)}</div>
        </div>
        {!options.exact_values_complete && <p className="mt-2 flex items-center gap-1 text-[11px] text-amber-700"><AlertTriangle className="h-3 w-3" />Showing the first {options.lookup_limit} values from the exact snapshot scan.</p>}
        {!!options.special_values?.length && <p className="mt-2 text-[11px] text-slate-500">Confirmed special values are kept separate: {options.special_values.join(", ")}.</p>}
      </div> : <div className="mt-3">
        {!temporal && <div className="mb-2 flex gap-2"><Button type="button" size="sm" variant={numericMode === "cutoff" ? "secondary" : "outline"} onClick={() => setNumericMode("cutoff")}>Cutoff</Button><Button type="button" size="sm" variant={numericMode === "range" ? "secondary" : "outline"} onClick={() => setNumericMode("range")}>Range</Button></div>}
        {numericMode === "range" && !temporal ? <div className="grid gap-2 sm:grid-cols-2"><input className="h-9 rounded border px-2 text-xs" type="number" placeholder="Baseline lower bound" value={lower} onChange={(event) => setLower(event.target.value)} /><input className="h-9 rounded border px-2 text-xs" type="number" placeholder="Baseline upper bound" value={upper} onChange={(event) => setUpper(event.target.value)} /></div> : <div className="grid gap-2 sm:grid-cols-[auto_1fr]">
          <select className="h-9 rounded border px-2 text-xs" value={direction} onChange={(event) => setDirection(event.target.value)}><option value="up_to">{baselineLabel} up to and including</option><option value="after">{baselineLabel} after</option></select>
          <input className="h-9 rounded border px-2 text-xs" type={temporal && options.input_kind === "date" ? "date" : temporal ? "text" : "number"} value={boundary} onChange={(event) => setBoundary(event.target.value)} placeholder="Boundary" />
        </div>}
        {numericMode === "cutoff" && !!options.suggestions?.length && <div className="mt-2 flex flex-wrap items-center gap-1"><span className="text-[11px] text-slate-500">Suggested:</span>{options.suggestions.map((value) => <Button key={String(value)} type="button" size="sm" variant="outline" onClick={() => setBoundary(String(value).slice(0, options.input_kind === "date" ? 10 : undefined))}>{String(value).slice(0, 16)}</Button>)}</div>}
        <Distribution histogram={options.histogram} />
      </div>}
      {!!options.special_values?.length && <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3"><span className="text-xs text-slate-600">Confirmed special values:</span><select className="h-8 rounded border border-slate-200 bg-white px-2 text-xs" value={specialPolicy} onChange={(event) => setSpecialPolicy(event.target.value)}><option value="exclude">Exclude from both populations</option><option value="baseline">Include in {baselineLabel}</option><option value="current">Include in {currentLabel}</option></select></div>}
    </div>}

    <Button className="w-fit" size="sm" disabled={disabled || busy || !canPreview}
      onClick={() => onPreview({ feature, expression: expression(), nullPolicy, specialPolicy })}>Preview populations</Button>

    {preview && <div className="rounded-md border border-slate-200 bg-white p-3 text-xs">
      <SplitLogic definition={confirmedDefinition} baselineLabel={baselineLabel} currentLabel={currentLabel} />
      <div className="grid gap-2 sm:grid-cols-5"><div><strong>{preview.baseline_count.toLocaleString()}</strong><span className="block text-slate-500">{baselineLabel} · {(preview.baseline_share * 100).toFixed(1)}%</span></div><div><strong>{preview.current_count.toLocaleString()}</strong><span className="block text-slate-500">{currentLabel} · {(preview.current_share * 100).toFixed(1)}%</span></div><div><strong>{preview.excluded_count.toLocaleString()}</strong><span className="block text-slate-500">Excluded</span></div><div><strong>{preview.null_count.toLocaleString()}</strong><span className="block text-slate-500">Null values</span></div><div><strong>{Number(preview.special_count || 0).toLocaleString()}</strong><span className="block text-slate-500">Special values</span></div></div>
      {!!preview.warnings?.length && <ul className="mt-2 text-amber-700">{preview.warnings.map((warning) => <li key={warning} className="flex items-center gap-1"><AlertTriangle className="h-3 w-3" />{warning}</li>)}</ul>}
    </div>}
  </div>;
}
