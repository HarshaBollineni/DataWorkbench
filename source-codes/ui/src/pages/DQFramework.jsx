import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { getFrameworkAreasV2, getFrameworkMatrixV2, getFrameworkOverviewV2 } from "@/api/client";
import { stageLabel } from "@/lib/stages";

const heat = { Critical: "bg-red-600 text-white", High: "bg-amber-500 text-white", Medium: "bg-slate-400 text-white" };
const TABS = [["overview", "Overview"], ["areas", "Areas"], ["matrix", "Matrix"]];

export default function DQFramework() {
  const [tab, setTab] = useState("overview");
  const [overview, setOverview] = useState(null);
  const [areas, setAreas] = useState([]);
  const [matrix, setMatrix] = useState(null);

  useEffect(() => {
    getFrameworkOverviewV2().then(setOverview);
    getFrameworkAreasV2().then(setAreas);
    getFrameworkMatrixV2().then(setMatrix);
  }, []);

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-950">DQ Framework</h1>
        <p className="mt-1 text-sm text-slate-500">Two-stage analytics and modeling data-quality framework. The register shows current coverage honestly: diagnostics undergoing additional testing and refinement remain workflow pending until their governed run paths are enabled.</p>
      </div>
      <div className="mb-5 flex gap-2">
        {TABS.map(([key, label]) => (
          <button key={key} className={`rounded-md px-3 py-2 text-sm font-medium ${tab === key ? "bg-dq-purple text-dq-dark" : "bg-white text-slate-600"}`} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {tab === "overview" && overview && (
        <section className="grid gap-5 lg:grid-cols-2">
          {["stage1", "stage2"].map((key) => (
            <div key={key} className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-lg font-semibold text-slate-950">{overview[key].title}</h2>
              <p className="mt-2 text-sm text-slate-600">{overview[key].desc}</p>
              <div className="mt-5 grid grid-cols-2 gap-3">
                <div className="rounded-md bg-slate-50 p-3"><div className="text-xs text-slate-500">Areas</div><div className="text-2xl font-bold">{overview[key].areas}</div></div>
                <div className="rounded-md bg-slate-50 p-3"><div className="text-xs text-slate-500">Tests</div><div className="text-2xl font-bold">{overview[key].tests}</div></div>
              </div>
            </div>
          ))}
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 lg:col-span-2">
            Areas tagged Both apply to systemic structural health and specific fitness for purpose. A registered diagnostic is run only when its workflow is executable and its readiness conditions are met; pending diagnostics are shown as pending rather than treated as passes or failures.
          </div>
        </section>
      )}

      {tab === "areas" && <AreasTab areas={areas} />}

      {tab === "matrix" && matrix && (
        <section className="overflow-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-[1100px] border-collapse text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="sticky left-0 bg-slate-100 px-3 py-2">Area</th>
                {matrix.families.map((f) => <th key={f} className="border-l border-slate-200 px-2 py-2 text-center">{f}</th>)}
              </tr>
            </thead>
            <tbody>
              {matrix.areas.map((area) => (
                <tr key={area.area_id} className="border-t border-slate-100">
                  <td className="sticky left-0 max-w-72 bg-white px-3 py-2 font-medium">{area.l2_area}</td>
                  {matrix.families.map((f) => {
                    const rating = matrix.ratings?.[area.area_id]?.[f] || "Medium";
                    return (
                      <td key={f} className="border-l border-slate-100 px-2 py-1.5 text-center">
                        <span className={`inline-block w-20 rounded py-1 text-center text-xs font-semibold ${heat[rating]}`}>{rating}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

// Areas tab (feedback 6.2): Systemic / Specific sub-tabs (areas tagged Both
// appear in each), grouped under a bold L1 theme heading with L2 cards below.
function AreasTab({ areas }) {
  const [sub, setSub] = useState("stage1");
  const visible = useMemo(
    () => areas.filter((a) => a.stage === sub || a.stage === "both"),
    [areas, sub],
  );
  const groups = useMemo(() => {
    const map = new Map();
    for (const area of visible) {
      const theme = area.l1_theme || "Other";
      if (!map.has(theme)) map.set(theme, []);
      map.get(theme).push(area);
    }
    return [...map.entries()];
  }, [visible]);

  return (
    <section>
      <div className="mb-4 flex gap-2">
        {[["stage1", "Systemic"], ["stage2", "Specific"]].map(([key, label]) => (
          <button key={key}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${sub === key ? "bg-slate-900 text-white" : "bg-white text-slate-600 border border-slate-200"}`}
            onClick={() => setSub(key)}>
            {label}
          </button>
        ))}
      </div>
      <div className="grid gap-6">
        {groups.map(([theme, themeAreas]) => (
          <div key={theme}>
            <h2 className="mb-2 text-lg font-bold text-slate-950">{theme}</h2>
            <div className="grid gap-3">
              {themeAreas.map((area) => (
                <details key={area.area_id} className="rounded-lg border border-slate-200 bg-white p-4">
                  <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3">
                    <h3 className="font-semibold text-slate-900">{area.l2_area}</h3>
                    <Badge variant={area.stage === "both" ? "default" : "secondary"}>{area.stage_label || stageLabel(area.stage)}</Badge>
                  </summary>
                  <div className="mt-4 grid gap-3 text-sm text-slate-600 md:grid-cols-2">
                    <p><span className="font-medium text-slate-900">Objective:</span> {area.objective}</p>
                    <p><span className="font-medium text-slate-900">Why it matters:</span> {area.why_it_matters}</p>
                    <div className="md:col-span-2">
                      <span className="font-medium text-slate-900">{sub === "stage1" ? "Systemic tests:" : "Specific tests:"}</span>
                      <ul className="mt-1 list-disc pl-5">
                        {((sub === "stage1" ? area.stage1_tests : area.stage2_tests) || []).map((t) => <li key={t}>{t}</li>)}
                        {((sub === "stage1" ? area.stage1_tests : area.stage2_tests) || []).length === 0 && (
                          <li className="list-none text-slate-400">No automated test in this stage — assessed qualitatively.</li>
                        )}
                      </ul>
                    </div>
                  </div>
                </details>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
