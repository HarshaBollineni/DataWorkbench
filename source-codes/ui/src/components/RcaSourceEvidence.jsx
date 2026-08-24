import { BarChart3, ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { buildRaincloudModel, scaleRaincloudValue } from "@/lib/raincloud";
import FeatureTargetEvidence from "@/pages/testlab/FeatureTargetEvidence";
import { PsiPopulationProfile } from "@/pages/testlab/PopulationStabilityResults";

const value = (input, digits = 4) => {
  if (input == null || input === "") return "Not available";
  if (typeof input === "boolean") return input ? "Yes" : "No";
  if (typeof input === "number") return Number.isInteger(input) ? input.toLocaleString() : input.toLocaleString(undefined, { maximumFractionDigits: digits });
  if (Array.isArray(input)) return input.join(", ");
  return String(input).replaceAll("_", " ");
};

function FactGrid({ facts, columns = "sm:grid-cols-4" }) {
  return <div className={`grid gap-2 ${columns}`}>{facts.map(([label, item]) => <div key={label} className="rounded-md border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">{label}</span><strong className="mt-1 block text-slate-900">{value(item)}</strong></div>)}</div>;
}

function Tooltip({ children, placement = "above" }) {
  const position = placement === "below" ? "top-full mt-2" : "bottom-full mb-2";
  return <span className={`pointer-events-none absolute left-1/2 z-20 hidden min-w-44 -translate-x-1/2 rounded bg-slate-900 px-2.5 py-2 text-left text-[10px] leading-4 text-white shadow-lg group-hover:block group-focus-within:block ${position}`}>{children}</span>;
}

function NumericRaincloud({ rows, profile }) {
  const model = buildRaincloudModel(rows);
  const domainMinimum = model.bins[0]?.start;
  const domainMaximum = model.bins.at(-1)?.end;
  const position = (input) => scaleRaincloudValue(input, domainMinimum, domainMaximum);
  const q1 = position(profile.q1 ?? profile.percentiles?.p25);
  const median = position(profile.median ?? profile.percentiles?.p50);
  const q3 = position(profile.q3 ?? profile.percentiles?.p75);
  const minimum = position(profile.min);
  const maximum = position(profile.max);
  const mean = position(profile.mean);
  const hasBox = [minimum, q1, median, q3, maximum].every((item) => item != null);
  const densityPoints = model.bins.map((row, index) => {
    const x = (index + 0.5) / model.bins.length * 100;
    const y = 72 - (model.maximum ? row.count / model.maximum * 58 : 0);
    return `${x},${y}`;
  }).join(" ");

  return <div className="rounded-md border border-slate-200 bg-white p-3" role="group" aria-label="Retained numeric distribution summary">
    <div className="relative mx-1">
      <svg className="h-20 w-full overflow-visible" viewBox="0 0 100 76" preserveAspectRatio="none" role="img" aria-label="Approximate density reconstructed from retained histogram bins">
        <polygon points={`0,72 ${densityPoints} 100,72`} className="fill-teal-600/20 stroke-teal-700" vectorEffect="non-scaling-stroke" strokeWidth="1.5" />
        <line x1="0" x2="100" y1="72" y2="72" className="stroke-slate-300" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="absolute inset-x-0 top-0 grid h-20" style={{ gridTemplateColumns: `repeat(${model.bins.length}, minmax(0, 1fr))` }}>
        {model.bins.map((row, index) => <button key={`cloud-${row.start}-${row.end}-${index}`} type="button" className="group relative min-w-0 rounded-sm outline-none hover:bg-orange-400/10 focus:bg-orange-400/10" aria-label={`Density bin ${value(row.start)} to ${value(row.end)}: ${value(row.count)} records, ${(row.share * 100).toFixed(1)} percent`}>
          <Tooltip placement="below"><strong className="block text-orange-300">{value(row.start)}–{value(row.end)}</strong>Records {value(row.count)}<br />Share {(row.share * 100).toFixed(1)}%<br />Density reconstructed from this retained bin</Tooltip>
        </button>)}
      </div>

      {hasBox && <div className="group relative h-8 outline-none" tabIndex={0} aria-label={`Five-number summary: minimum ${value(profile.min)}, first quartile ${value(profile.q1 ?? profile.percentiles?.p25)}, median ${value(profile.median ?? profile.percentiles?.p50)}, third quartile ${value(profile.q3 ?? profile.percentiles?.p75)}, maximum ${value(profile.max)}`}>
        <span className="absolute top-1/2 h-px -translate-y-1/2 bg-slate-500" style={{ left: `${minimum}%`, width: `${maximum - minimum}%` }} />
        <span className="absolute top-2 h-4 border border-teal-700 bg-teal-50" style={{ left: `${q1}%`, width: `${Math.max(q3 - q1, 0.5)}%` }} />
        <span className="absolute top-1 h-6 w-0.5 bg-orange-400" style={{ left: `${median}%` }} />
        <span className="absolute top-2 h-4 w-px bg-slate-500" style={{ left: `${minimum}%` }} />
        <span className="absolute top-2 h-4 w-px bg-slate-500" style={{ left: `${maximum}%` }} />
        <Tooltip><strong className="block text-orange-300">Five-number summary</strong>Minimum {value(profile.min)} · Q1 {value(profile.q1 ?? profile.percentiles?.p25)}<br />Median {value(profile.median ?? profile.percentiles?.p50)} · Q3 {value(profile.q3 ?? profile.percentiles?.p75)}<br />Maximum {value(profile.max)}</Tooltip>
      </div>}

      {mean != null && <div className="group absolute top-[5.15rem] -translate-x-1/2 outline-none" style={{ left: `${mean}%` }} tabIndex={0} aria-label={`Mean ${value(profile.mean)}; standard deviation ${value(profile.stddev)}`}>
        <span className="block h-2.5 w-2.5 rotate-45 border border-white bg-orange-400 shadow-sm" />
        <Tooltip><strong className="block text-orange-300">Mean</strong>{value(profile.mean)}<br />Std. deviation {value(profile.stddev)}</Tooltip>
      </div>}

      <div className="grid min-h-28 items-start gap-1 border-t border-slate-200 pt-2" style={{ gridTemplateColumns: `repeat(${model.bins.length}, minmax(0, 1fr))` }}>
        {model.bins.map((row, index) => <div key={`${row.start}-${row.end}-${index}`} className="group relative flex min-w-0 flex-col items-center gap-0.5 rounded py-0.5 outline-none transition-colors hover:bg-orange-50 focus:bg-orange-50" tabIndex={0} aria-label={`${value(row.start)} to ${value(row.end)}: ${value(row.count)} records, ${(row.share * 100).toFixed(1)} percent`}>
          {row.dots.map((fill, dotIndex) => <span key={dotIndex} className="relative block h-2 w-2 overflow-hidden rounded-full border border-teal-700/40 bg-slate-100" aria-hidden="true"><span className="absolute inset-x-0 bottom-0 bg-teal-600 group-hover:bg-orange-400 group-focus:bg-orange-400" style={{ height: `${fill * 100}%` }} /></span>)}
          <Tooltip><strong className="block text-orange-300">{value(row.start)}–{value(row.end)}</strong>Records {value(row.count)}<br />Share {(row.share * 100).toFixed(1)}%<br />Encoding {row.dots.length} dots · ≈{value(model.dotUnit)} records per full dot</Tooltip>
        </div>)}
      </div>
    </div>
    <div className="mt-1 flex justify-between text-[10px] text-slate-400"><span>{value(domainMinimum)}</span><span>{value(domainMaximum)}</span></div>
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-500"><span className="font-medium text-slate-600"><span className="mr-1 inline-block h-2 w-2 rounded-full bg-teal-600" />≈ {value(model.dotUnit)} records per full dot</span><span>Maximum 10 dots per bin</span><span>Dots represent retained bin counts, not individual rows.</span></div>
  </div>;
}

function CategoryBars({ entries }) {
  const maximum = Math.max(...entries.map(([, count]) => Number(count || 0)), 1);
  return <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3" role="img" aria-label="Character feature frequency chart">{entries.map(([label, count], index) => <div key={`${label}-${index}`} className="grid grid-cols-[minmax(7rem,12rem)_1fr_auto] items-center gap-2 text-xs"><span className="truncate text-slate-600" title={label}>{label}</span><span className="h-2 overflow-hidden rounded-full bg-slate-100"><span className="block h-full rounded-full bg-teal-600" style={{ width: `${Number(count || 0) / maximum * 100}%` }} /></span><strong className="min-w-12 text-right text-slate-800">{value(count)}</strong></div>)}</div>;
}

function DataProfile({ profile }) {
  if (!profile) return <section className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white p-3 text-xs text-slate-500">A retained feature profile is not available for this intake.</section>;
  const observedType = String(profile.classification || profile.data_type || "").toLowerCase();
  const numeric = ["numeric", "numerical", "integer", "float", "decimal", "int"].some((kind) => observedType.includes(kind));
  const facts = numeric
    ? [["Rows", profile.total_count], ["Missing", profile.null_count], ["Unique values", profile.distinct_count], ["Minimum", profile.min], ["Maximum", profile.max], ["Average", profile.mean], ["Std. deviation", profile.stddev]]
    : [["Rows", profile.total_count], ["Missing", profile.null_count], ["Unique values", profile.distinct_count], ["Non-null", profile.non_null_count]];
  const categories = Object.entries(profile.top_k || {});
  return <section className="mt-3 rounded-lg border border-teal-200 bg-[#f8faf6] p-3">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><h4 className="flex items-center gap-2 text-sm font-semibold text-[#365a60]"><BarChart3 className="h-4 w-4 text-teal-700" /> Data profile</h4><p className="text-[11px] text-slate-500">Retained profile for the affected feature at intake.</p></div><Badge variant="outline" className="border-teal-200 text-teal-800">{value(profile.classification || profile.data_type)}</Badge></div>
    <FactGrid facts={facts} columns={numeric ? "sm:grid-cols-4 xl:grid-cols-7" : "sm:grid-cols-4"} />
    {numeric && profile.histogram?.length > 0 && <div className="mt-3"><p className="mb-2 text-xs font-semibold text-slate-700">Observed distribution</p><NumericRaincloud rows={profile.histogram} profile={profile} /></div>}
    {!numeric && categories.length > 0 && <div className="mt-3"><p className="mb-2 text-xs font-semibold text-slate-700">Most frequent values</p><CategoryBars entries={categories} /></div>}
  </section>;
}

function GenericTechnical({ source }) {
  const finding = source.finding || {};
  const evidence = Array.isArray(finding.evidence) ? finding.evidence : [];
  const scalarMetrics = Object.entries(source.metrics || {}).filter(([, item]) => ["string", "number", "boolean"].includes(typeof item)).slice(0, 12);
  return <div className="space-y-4"><section><h4 className="text-sm font-semibold text-[#365a60]">Decision evidence</h4><p className="mt-1 text-xs text-slate-600">{finding.rule_text || "The diagnostic's retained decision inputs are shown below."}</p>{scalarMetrics.length > 0 && <div className="mt-3"><FactGrid facts={scalarMetrics.map(([key, item]) => [value(key), item])} columns="sm:grid-cols-3 xl:grid-cols-4" /></div>}</section>{evidence.length > 0 && <section><h4 className="mb-2 text-sm font-semibold text-[#365a60]">Supporting observations</h4><div className="max-h-72 overflow-auto rounded-lg border border-slate-200 bg-white"><table className="w-full text-left text-xs"><tbody>{evidence.map((item, index) => <tr key={index} className="border-t border-slate-100 first:border-0"><th className="w-40 p-2 text-slate-500">Observation {index + 1}</th><td className="p-2 text-slate-800">{typeof item === "object" ? Object.entries(item).map(([key, entry]) => `${value(key)}: ${value(entry)}`).join(" · ") : value(item)}</td></tr>)}</tbody></table></div></section>}</div>;
}

export default function RcaSourceEvidence({ issue }) {
  const source = issue?.source_evidence;
  if (!source) return null;
  const metric = source.metrics || {};
  const isPsi = source.diagnostic_id === 14 || metric.result_kind === "psi_feature";
  const isFeatureTarget = source.diagnostic_id === 2 || metric.result_kind === "feature";
  const headline = isPsi ? `PSI ${value(metric.psi)} is classified as ${value(metric.classification)}.` : isFeatureTarget ? `Observed separation metric ${value(metric.auc ?? metric.metric ?? issue.metric)} requires contextual review.` : `${source.finding?.rule_text || issue.test_name} — observed value ${value(issue.metric)}.`;
  const facts = isPsi
    ? [["PSI", metric.psi ?? issue.metric], ["Classification", metric.classification], ["Watch boundary", metric.thresholds?.watch], ["Investigate boundary", metric.thresholds?.investigate]]
    : isFeatureTarget
      ? [["AUC", metric.auc ?? issue.metric], ["Gini", metric.gini ?? metric.roc_detail?.primary_gini], ["Information value", metric.iv], ["Classification", metric.classification]]
      : [["Observed", issue.metric], ["Affected rows", source.finding?.violation_count], ["Tolerance", source.finding?.tolerance], ["Outcome", source.finding?.outcome]];
  return <section className="rounded-lg border border-teal-200 bg-[#fffdf8] p-4" data-testid="rca-intake-evidence">
    <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-xs font-semibold uppercase tracking-wide text-teal-700">Intake · source diagnostic evidence</p><p className="mt-1 text-sm font-medium text-slate-900">{headline}</p><p className="mt-1 text-xs text-slate-500">The immutable diagnostic result and retained feature profile establish the RCA intake context.</p></div><Badge variant="outline" className="border-teal-200 text-[#365a60]">AAR result {source.result_id}</Badge></div>
    <div className="mt-3 grid gap-2 rounded-md border border-slate-200 bg-white p-3 text-xs sm:grid-cols-4"><span><strong className="text-slate-700">Dataset:</strong> {issue.item_name || "Retained source"}</span><span><strong className="text-slate-700">Table:</strong> {issue.table_name || source.entity_or_table}</span><span><strong className="text-slate-700">Feature:</strong> {(issue.columns || []).join(", ") || "Not specified"}</span><span><strong className="text-slate-700">Metric:</strong> {value(issue.metric)}</span></div>
    <DataProfile profile={source.data_profile} />
    <div className="mt-3"><FactGrid facts={facts} /></div>
    <details className="group mt-4 overflow-hidden rounded-lg border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-center gap-2 bg-[#f2f4ef] p-3 text-sm font-semibold text-[#365a60]">Technical source evidence <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open:rotate-180" /></summary><div className="border-t border-slate-200 p-4">{isFeatureTarget ? <FeatureTargetEvidence row={{ result_id: source.result_id, metrics_json: metric }} /> : isPsi ? <PsiPopulationProfile metric={metric} /> : <GenericTechnical source={source} />}</div></details>
  </section>;
}
