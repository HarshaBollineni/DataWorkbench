import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, Minus, Plus, Redo2, RotateCcw, Trees, Undo2 } from "lucide-react";

import { createNumericDiagnosticBinningOverrideV2, getDiagnosticBinningImpactV2, previewDiagnosticBinningV2, reviewDiagnosticBinningV2 } from "@/api/client";
import { Button } from "@/components/ui/button";
import { formatBinRows } from "@/features/test-lab/shared/binning/binLabelDisplay";

const fmt = (value, digits = 4) => value == null ? "—" : Number(value).toFixed(digits);
const pct = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
const partitionKey = (definition) => JSON.stringify([
  definition?.numeric_splits || [], definition?.categorical_groups || [],
]);

function treeLayout(nodes) {
  const byId = new Map(nodes.map((node) => [node.node_id, node]));
  const root = nodes.find((node) => node.depth === 0) || nodes[0];
  const positioned = new Map();
  let leafPosition = 0;
  const place = (nodeId) => {
    const node = byId.get(nodeId);
    if (!node) return 110;
    const leaf = node.node_type === "leaf";
    const width = leaf ? 208 : 176;
    const height = leaf ? 180 : 88;
    let centerX;
    if (leaf) {
      centerX = 36 + width / 2 + leafPosition * 232;
      leafPosition += 1;
    } else {
      centerX = (place(node.left_child) + place(node.right_child)) / 2;
    }
    positioned.set(nodeId, { node, x: centerX - width / 2, y: 36 + node.depth * 195, width, height, centerX });
    return centerX;
  };
  place(root.node_id);
  const laidOut = [...positioned.values()];
  const edges = [];
  laidOut.filter(({ node }) => node.node_type === "split").forEach((parent) => {
    [[parent.node.left_child, parent.node.left_label], [parent.node.right_child, parent.node.right_label]].forEach(([childId, label]) => {
      const child = positioned.get(childId);
      if (!child) return;
      const startY = parent.y + parent.height;
      const middleY = startY + (child.y - startY) * 0.52;
      edges.push({ parent: parent.node.node_id, child: childId, label,
        labelX: parent.centerX + (child.centerX - parent.centerX) * 0.42,
        labelY: startY + (child.y - startY) * 0.38,
        path: `M ${parent.centerX} ${startY} C ${parent.centerX} ${middleY}, ${child.centerX} ${middleY}, ${child.centerX} ${child.y - 7}` });
    });
  });
  return { nodes: laidOut, edges,
    width: Math.max(280, leafPosition * 232 - 24 + 72),
    height: Math.max(...laidOut.map((item) => item.y + item.height), 220) + 36 };
}

export function DecisionTree({ result, target }) {
  const scrollRef = useRef(null);
  const [fitScale, setFitScale] = useState(1);
  const [customScale, setCustomScale] = useState(null);
  const layout = useMemo(() => treeLayout(result.primary_tree || []), [result.primary_tree]);
  const leaves = new Map((result.leaves || []).map((leaf, index) => [leaf.leaf_id, { ...leaf, number: index + 1 }]));
  const scores = [...leaves.values()].map((leaf) => Number(leaf.score));
  const minScore = Math.min(...scores, 0);
  const scoreRange = Math.max(...scores, 0) - minScore;
  const totalEvents = [...leaves.values()].reduce((sum, leaf) => sum + (leaf.target_events || 0), 0);
  const scale = customScale ?? fitScale;
  const markerId = `tree-arrow-${result.feature.replace(/[^A-Za-z0-9_-]/g, "-")}`;

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return undefined;
    const resize = () => setFitScale(Math.min(1, Math.max(0.45, (container.clientWidth - 24) / layout.width)));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [layout.width]);

  const outcome = (node) => result.target_type === "binary" && node.target_mean != null
    ? `Target rate ${pct(node.target_mean)}`
    : node.target_mean != null ? `Mean target ${fmt(node.target_mean)}` : null;
  const delta = (node) => node.parent_delta == null ? null
    : result.target_type === "binary" ? `Δ ${node.parent_delta >= 0 ? "+" : ""}${(node.parent_delta * 10000).toFixed(0)} bps vs parent`
      : `Δ ${node.parent_delta >= 0 ? "+" : ""}${fmt(node.parent_delta)} vs parent`;

  return <div className="dq-tree-shell">
    <div className="dq-tree-toolbar"><span>Tree scale <strong>{Math.round(scale * 100)}%</strong></span><div>
      <button type="button" aria-label="Zoom decision tree out" disabled={scale <= 0.45} onClick={() => setCustomScale(Math.max(0.45, scale - 0.1))}><Minus /></button>
      <button type="button" className={customScale == null ? "active" : ""} onClick={() => setCustomScale(null)}>Fit</button>
      <button type="button" aria-label="Zoom decision tree in" disabled={scale >= 1.3} onClick={() => setCustomScale(Math.min(1.3, scale + 0.1))}><Plus /></button>
    </div></div>
    <div ref={scrollRef} className="dq-tree-scroll" role="region" aria-label={`Primary decision tree for ${result.feature}`} tabIndex="0">
      <svg className="dq-tree-canvas" width={Math.round(layout.width * scale)} height={Math.round(layout.height * scale)} viewBox={`0 0 ${layout.width} ${layout.height}`}>
        <defs><marker id={markerId} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" /></marker></defs>
        <g className="dq-tree-connectors">{layout.edges.map((edge) => <g key={`${edge.parent}-${edge.child}`}><path d={edge.path} markerEnd={`url(#${markerId})`} /><rect x={edge.labelX - 34} y={edge.labelY - 11} width="68" height="20" rx="10" /><text x={edge.labelX} y={edge.labelY + 3}>{edge.label}</text></g>)}</g>
        {layout.nodes.map(({ node, x, y, width, height }) => {
          if (node.node_type === "split") return <foreignObject key={node.node_id} x={x} y={y} width={width} height={height}><div xmlns="http://www.w3.org/1999/xhtml" className="dq-tree-split"><span>Decision · {node.rows.toLocaleString()} records</span><strong>{node.prompt}</strong>{outcome(node) && <small><b>{outcome(node)}</b>{delta(node) && <em>{delta(node)}</em>}</small>}</div></foreignObject>;
          const leaf = leaves.get(node.node_id);
          if (!leaf) return null;
          const intensity = scoreRange ? (Number(leaf.score) - minScore) / scoreRange : 0;
          const makeup = [leaf.regular_rows && `${leaf.regular_rows.toLocaleString()} regular`, leaf.generic_missing_rows && `${leaf.generic_missing_rows.toLocaleString()} missing`, ...Object.entries(leaf.special_rows || {}).map(([value, count]) => `${count.toLocaleString()} special (${value})`)].filter(Boolean).join(" · ");
          return <foreignObject key={node.node_id} x={x} y={y} width={width} height={height}><div xmlns="http://www.w3.org/1999/xhtml" className="dq-tree-leaf" style={{ "--leaf-tint": 0.06 + intensity * 0.16 }}><div><span>Terminal leaf</span><strong>Leaf {leaf.number}</strong></div><section><label>Target <b>{leaf.target_events?.toLocaleString() ?? fmt(leaf.target_mean)}</b></label><label>Records <b>{leaf.rows.toLocaleString()}</b></label></section>{delta(node) && <small>{delta(node)}</small>}<section className="metrics"><label>Population <b>{pct(leaf.rows / result.rows_evaluated)}</b></label>{result.target_type === "binary" && <><label>Target rate <b>{pct(leaf.target_mean)}</b></label><label>All targets <b>{totalEvents ? pct(leaf.target_events / totalEvents) : "—"}</b></label></>}</section><footer><span>Data</span><b title={makeup}>{makeup || target}</b></footer></div></foreignObject>;
        })}
      </svg>
    </div>
  </div>;
}

function BinChart({ rows }) {
  const [metric, setMetric] = useState("woe");
  const width = Math.max(720, rows.length * 128);
  const height = 270, left = 54, right = 35, top = 36, bottom = 74;
  const maxShare = Math.max(...rows.map((row) => row.population_share || 0), 0.01);
  const values = rows.map((row) => Number(metric === "woe" ? row.woe : row.event_rate) || 0);
  const minValue = Math.min(...values, 0), maxValue = Math.max(...values, 0.01);
  const slot = (width - left - right) / rows.length;
  const y = (value) => top + (maxValue - value) / Math.max(maxValue - minValue, 0.01) * (height - top - bottom);
  const baseline = y(0);
  return <div className="dq-bin-chart"><div><span><strong>Bin profile</strong><small>Bars: population share · line: {metric === "woe" ? "WOE" : "target rate"} · protected bins are outlined</small></span><span><Button size="sm" variant={metric === "woe" ? "default" : "outline"} onClick={() => setMetric("woe")}>WOE</Button><Button size="sm" variant={metric === "rate" ? "default" : "outline"} onClick={() => setMetric("rate")}>Target rate</Button></span></div><svg viewBox={`0 0 ${width} ${height}`} role="img">
    <line x1={left} x2={width-right} y1={height-bottom} y2={height-bottom} className="axis" /><line x1={left} x2={width-right} y1={baseline} y2={baseline} className="zero" />
    {rows.map((row, index) => { const center = left + slot * (index + 0.5); const barHeight = (row.population_share || 0) / maxShare * (height-top-bottom); return <g key={row.bin_id}><rect x={center-slot*0.22} y={height-bottom-barHeight} width={slot*0.44} height={barHeight} className={row.kind === "regular" ? "bar" : "bar protected"} /><text x={center} y={height-bottom+20} transform={`rotate(-35 ${center} ${height-bottom+20})`} className="label">{row.label.length > 18 ? `${row.label.slice(0, 17)}…` : row.label}</text></g>; })}
    <polyline points={rows.map((row,index) => `${left+slot*(index+0.5)},${y(values[index])}`).join(" ")} className="line" />
    {rows.map((row,index) => <circle key={`p-${row.bin_id}`} cx={left+slot*(index+0.5)} cy={y(values[index])} r="5" className={row.kind === "regular" ? "" : "protected"}><title>{row.label}: {fmt(values[index])}</title></circle>)}
  </svg></div>;
}

function mergeDefinition(definition, index) {
  if (definition.feature_type === "numeric") return { ...definition, numeric_splits: definition.numeric_splits.filter((_, i) => i !== index) };
  return { ...definition, categorical_groups: definition.categorical_groups.reduce((groups, group, i) => { if (i === index) groups.push([...group, ...definition.categorical_groups[i + 1]]); else if (i !== index + 1) groups.push(group); return groups; }, []) };
}

function splitDefinition(definition, fineDefinition, group, selected) {
  const indexes = selected.map((id) => Number(id.replace(/^R/, ""))).sort((a, b) => a - b);
  if (definition.feature_type === "numeric") {
    const splits = [...definition.numeric_splits];
    if (indexes[0] > 0) splits.push(fineDefinition.numeric_splits[indexes[0] - 1]);
    if (indexes.at(-1) < fineDefinition.numeric_splits.length) splits.push(fineDefinition.numeric_splits[indexes.at(-1)]);
    return { ...definition, numeric_splits: [...new Set(splits)].sort((a, b) => a - b) };
  }
  const parent = definition.categorical_groups[group];
  const selectedValues = new Set(selected.flatMap((id) => fineDefinition.categorical_groups[Number(id.replace(/^R/, ""))] || []));
  return { ...definition, categorical_groups: definition.categorical_groups.flatMap((values, index) => index === group ? [parent.filter((value) => selectedValues.has(value)), parent.filter((value) => !selectedValues.has(value))].filter((values2) => values2.length) : [values]) };
}

export function BinningWorkspace({ resultId, initial, initialGovernance, onChanged,
  previewDefinition, saveDefinition, localOnly = false, saveButtonLabel = "Apply and save" }) {
  const [current, setCurrent] = useState(initial);
  const [governance, setGovernance] = useState(initialGovernance || {});
  const [automatic] = useState(initial.automatic_coarse_definition || initial.coarse_definition);
  const [past, setPast] = useState([]);
  const [future, setFuture] = useState([]);
  const [scope, setScope] = useState("diagnostic_specific");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState([]);
  const [selected, setSelected] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideCuts, setOverrideCuts] = useState("");
  const [overrideSpecials, setOverrideSpecials] = useState("");
  const [overrideRationale, setOverrideRationale] = useState("");
  const preview = async (definition, direction = "commit") => {
    setBusy(true); setError("");
    try {
      const reviewed = previewDefinition
        ? await previewDefinition(definition)
        : await previewDiagnosticBinningV2(resultId, { definition });
      if (direction === "undo") { setPast((items) => items.slice(0, -1)); setFuture((items) => [current.coarse_definition, ...items]); }
      else if (direction === "redo") { setPast((items) => [...items, current.coarse_definition]); setFuture((items) => items.slice(1)); }
      else { setPast((items) => [...items, current.coarse_definition]); setFuture([]); }
      const next = { ...current, coarse_definition: reviewed.definition, coarse_bins: reviewed.bins, coarse_metrics: reviewed.metrics, warnings: reviewed.warnings, coarse_groups: reviewed.coarse_groups };
      setCurrent(next); setSelected([]); setExpanded([]); setDirty(true);
    } catch (problem) { setError(problem.message); }
    finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setError("");
    try {
      if (saveDefinition) {
        const reviewed = await saveDefinition(current.coarse_definition);
        const next = { ...current, coarse_definition: reviewed.definition, coarse_bins: reviewed.bins,
          coarse_metrics: reviewed.metrics, warnings: reviewed.warnings, coarse_groups: reviewed.coarse_groups };
        setCurrent(next); setDirty(false);
        onChanged?.(next, governance, reviewed);
        return;
      }
      let confirmUniversal = false;
      if (scope === "universal") {
        const impact = await getDiagnosticBinningImpactV2(resultId);
        confirmUniversal = window.confirm(`Make this full-snapshot fine/coarse definition universal for ${initial.feature}?\n\nIt becomes the default for the same immutable snapshot, table, feature and governed target. ${impact.dependent_count || 0} dependent artifacts are recorded.`);
        if (!confirmUniversal) return;
      }
      const reviewed = await reviewDiagnosticBinningV2(resultId, { definition: current.coarse_definition, scope, confirm_universal: confirmUniversal });
      const next = { ...current, coarse_definition: reviewed.definition, coarse_bins: reviewed.bins, coarse_metrics: reviewed.metrics, warnings: reviewed.warnings, coarse_groups: reviewed.coarse_groups };
      setCurrent(next); setGovernance(reviewed.governance); setDirty(false);
      onChanged?.(next, reviewed.governance, reviewed);
    } catch (problem) { setError(problem.message); }
    finally { setBusy(false); }
  };
  const createNumericOverride = async () => {
    setBusy(true); setError("");
    try {
      let confirmUniversal = false;
      if (scope === "universal") {
        const impact = await getDiagnosticBinningImpactV2(resultId);
        confirmUniversal = window.confirm(`Create and promote a new numeric fine/coarse definition for ${initial.feature}?\n\nThe selected feature and governed target will be read again. ${impact.dependent_count || 0} dependent artifacts are recorded.`);
        if (!confirmUniversal) return;
      } else if (!window.confirm(`Create a new diagnostic-specific numeric fine foundation for ${initial.feature}?\n\nThe selected feature and governed target will be read again, then Diagnostic 2 will optimize coarse bins over the entered fine cuts.`)) return;
      const created = await createNumericDiagnosticBinningOverrideV2(resultId, {
        cuts: overrideCuts, special_values: overrideSpecials,
        rationale: overrideRationale, scope, confirm_universal: confirmUniversal,
      });
      const next = { ...created, automatic_coarse_definition: automatic };
      setCurrent(next); setGovernance(created.governance || {}); setPast([]); setFuture([]);
      setSelected([]); setExpanded([]); setDirty(false); setOverrideOpen(false);
      onChanged?.(next, created.governance, created);
    } catch (problem) { setError(problem.message); }
    finally { setBusy(false); }
  };
  const coarseDisplayRows = useMemo(() => formatBinRows(current.coarse_bins), [current.coarse_bins]);
  const fineDisplayRows = useMemo(() => formatBinRows(current.fine_bins), [current.fine_bins]);
  const protectedBins = coarseDisplayRows.filter((row) => ["missing", "special"].includes(row.kind));
  const regular = coarseDisplayRows.filter((row) => row.kind === "regular");
  const fineById = Object.fromEntries(fineDisplayRows.map((row) => [row.bin_id, row]));
  const selectedGroup = current.coarse_groups.findIndex((group) => selected.some((id) => group.includes(id)));
  const canSplit = selected.length && selectedGroup >= 0 && selected.length < current.coarse_groups[selectedGroup].length;
  const previewIv = current.coarse_metrics?.find((metric) => ["information_value", "maximum_one_vs_rest_iv"].includes(metric.name))?.value;

  return <details className="dq-bin-workspace" open><summary><span><small>Fine-bin review workspace</small><strong>{current.feature}</strong><em>Bin profile and editable coarse-bin review</em></span><span><b>{current.coarse_definition.solver_status}</b><ChevronRight /></span></summary><div className="content">
    {current.binning_profile && <p className="profile"><Check /> {current.binning_profile.execution_mode === "quick" ? "Quick adaptive" : "Full adaptive"} profile: {current.binning_profile.unique_regular_values?.toLocaleString()} unique regular values · requested {current.binning_profile.requested_max_prebins} fine / {current.binning_profile.requested_max_bins} coarse · effective {current.binning_profile.effective_max_prebins} fine / {current.binning_profile.effective_max_bins} coarse · {current.binning_profile.effective_solver_time_limit_seconds}s solver cap</p>}
    {current.warnings?.map((warning) => <p className="warning" key={warning}><AlertTriangle /> {warning}</p>)}
    {current.feature_type === "numeric" && <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
      <div className="flex flex-wrap items-center justify-between gap-2"><span><strong className="text-slate-800">Advanced numeric fine-bin override</strong><small className="mt-1 block">Entered cuts replace the automatic fine foundation. Diagnostic 2 then optimizes coarse bins using the run's governed constraints.</small></span><Button size="sm" variant="outline" disabled={busy} onClick={() => setOverrideOpen((value) => !value)}>{overrideOpen ? "Close" : "Enter cuts"}</Button></div>
      {overrideOpen && <div className="mt-3 grid gap-3 rounded border border-amber-200 bg-amber-50 p-3">
        <p className="text-amber-800"><strong>This reads the feature and governed target again.</strong> Missing values (blank cells and values recognized as missing during Data Sourcing, such as NA or Null) remain separate. Data Sourcing special values are retained and any additions apply only to this new Diagnostic 2 definition.</p>
        <label className="grid gap-1"><span>Special values, comma separated (optional)</span><input value={overrideSpecials} onChange={(event) => setOverrideSpecials(event.target.value)} placeholder="Example: -999,-888" className="h-9 rounded border border-slate-300 bg-white px-2" /><small>Only finite numeric values are accepted. Leave empty when not applicable.</small></label>
        <label className="grid gap-1"><span>Fine-bin cuts, comma separated</span><input value={overrideCuts} onChange={(event) => setOverrideCuts(event.target.value)} placeholder="Example: 10,25,50,100" className="h-9 rounded border border-slate-300 bg-white px-2" /><small>Cuts must be unique, increasing and strictly inside the observed regular range.</small></label>
        <label className="grid gap-1"><span>Rationale (optional)</span><input value={overrideRationale} onChange={(event) => setOverrideRationale(event.target.value)} placeholder="Why this fine foundation is required" className="h-9 rounded border border-slate-300 bg-white px-2" /></label>
        <div><Button size="sm" disabled={busy || !overrideCuts.trim()} onClick={createNumericOverride}>{busy ? "Optimizing…" : scope === "universal" ? "Create universal version" : "Create diagnostic-specific override"}</Button></div>
      </div>}
    </div>}
    <details open><summary>Bin Profile Chart</summary><BinChart rows={coarseDisplayRows} /></details>
    <details open><summary>Editable coarse bins · {current.coarse_bins.length}</summary><div className="editor">
      <div className="governance"><span><Button size="sm" variant="outline" disabled={busy || !past.length} onClick={() => preview(past.at(-1), "undo")}><Undo2 /> Undo</Button><Button size="sm" variant="outline" disabled={busy || !future.length} onClick={() => preview(future[0], "redo")}><Redo2 /> Redo</Button><Button size="sm" variant="outline" disabled={busy || partitionKey(current.coarse_definition) === partitionKey(automatic)} onClick={() => preview(automatic, "reset")}><RotateCcw /> Automatic</Button></span>{!localOnly && <label>Save destination <select value={scope} onChange={(event) => setScope(event.target.value)}><option value="diagnostic_specific">Diagnostic-specific version</option><option value="universal">Universal definition</option></select></label>}<span className="iv-values"><b>{localOnly ? "Baseline IV" : "Diagnostic-specific IV"} {fmt(governance.diagnostic_specific?.iv ?? governance.local?.iv)}</b>{!localOnly && <b>Universal IV {fmt(governance.universal?.iv)}</b>}</span><Button size="sm" disabled={busy || (!dirty && scope !== "universal")} onClick={save}>{busy ? "Working…" : scope === "universal" && !dirty ? "Make current bins universal" : saveButtonLabel}</Button></div>
      {dirty && <p className="draft-note">Preview IV {fmt(previewIv)} · changes are not persisted until Apply and save is selected.</p>}
      {error && <p className="warning"><AlertTriangle /> {error}</p>}
      {protectedBins.length > 0 && <div className="protected-panel"><strong>Protected missing and special bins</strong><small>Declared values remain separate and cannot be merged into regular intervals.</small><span>{protectedBins.map((row) => <article key={row.bin_id}><b>{row.label}</b><small>{row.rows.toLocaleString()} rows · {pct(row.population_share)} population · {pct(row.event_rate)} target · WOE {fmt(row.woe)}</small></article>)}</span></div>}
      <div className="dq-coarse-table"><div className="head"><span /><span>Bin</span><span>Feature average</span><span>Events</span><span>Total</span><span>% population</span><span>Target rate</span><span>WOE</span></div>{regular.map((row, index) => { const isOpen = expanded.includes(index); const group = current.coarse_groups[index] || []; return <div className="coarse" key={row.bin_id}><div className="row"><button type="button" aria-label={`Expand ${row.label}`} onClick={() => setExpanded((items) => items.includes(index) ? items.filter((i) => i !== index) : [...items, index])}>{isOpen ? <ChevronDown /> : <ChevronRight />}</button><span><b>{row.label}</b><small>regular · {group.length} fine bins</small>{index < regular.length - 1 && <Button size="sm" variant="outline" disabled={busy} onClick={() => preview(mergeDefinition(current.coarse_definition, index))}>Merge next</Button>}</span><b>{fmt(row.feature_mean, 2)}</b><span>{row.events?.toLocaleString() || "—"}</span><span>{row.rows.toLocaleString()}</span><span>{pct(row.population_share)}</span><span>{pct(row.event_rate)}</span><span>{fmt(row.woe)}</span></div>{isOpen && <div className="fine"><div>{group.map((id) => { const fine = fineById[id]; return fine && <label key={id}><input type="checkbox" checked={selected.includes(id)} onChange={() => setSelected((items) => { const sameGroup = items.every((item) => group.includes(item)); const base = sameGroup ? items : []; return base.includes(id) ? base.filter((item) => item !== id) : [...base, id]; })} /><span><b>{fine.label}</b><small>{fine.rows.toLocaleString()} rows · {pct(fine.population_share)} · target {pct(fine.event_rate)} · WOE {fmt(fine.woe)}</small></span></label>; })}</div><Button size="sm" disabled={busy || !canSplit} onClick={() => preview(splitDefinition(current.coarse_definition, current.fine_definition, index, selected))}>Split selected fine bins</Button></div>}</div>; })}</div>
    </div></details>
  </div></details>;
}

export default function FeatureTargetEvidence({ row, onChanged }) {
  const metric = row.metrics_json || {};
  return <div className="grid gap-4">
    {metric.roc_detail?.primary_tree?.length ? <details className="dq-tree-section" open><summary><span><Trees /><span><strong>Primary decision tree</strong><small>Existing ROC decision-tree explanation.</small></span></span><ChevronDown /></summary><DecisionTree result={metric.roc_detail} target={metric.target} /></details> : <p className="text-xs text-slate-500">Decision tree unavailable for this feature.</p>}
    {metric.binning_detail ? <BinningWorkspace resultId={row.result_id} initial={metric.binning_detail} initialGovernance={metric.binning_governance} onChanged={onChanged} /> : <p className="text-xs text-slate-500">IV fine-bin evidence is unavailable for this feature.</p>}
  </div>;
}
