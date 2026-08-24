import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Database, FileSpreadsheet, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getItemsV2 } from "@/api/client";

const tagTone = {
  "Data Sourcing": "bg-amber-100 text-amber-800",
  "Test Lab": "bg-blue-100 text-blue-800",
  "Issue Management": "bg-red-100 text-red-800",
};

// Static explainer cards (feedback 1.2) with live in-progress/completed counts
// per kind (feedback 1.3) — users start an upload from Data Sourcing.
const KIND_CARDS = [
  {
    key: "database",
    title: "Database",
    icon: Database,
    text: "Raw multi-table source data. Checked for structural health. No use case mapping required.",
  },
  {
    key: "dataset",
    title: "Dataset",
    icon: FileSpreadsheet,
    text: "Model-ready dataset for a specific use case. Checked for fitness.",
  },
];

// Feedback 1.5: status values render Title Case.
const STATUS_LABELS = {
  sourcing: "Sourcing",
  profiled: "Profiled",
  testlab_step1: "Test Lab Step 1",
  testlab_step2: "Test Lab Step 2",
  testlab_step3: "Test Lab Step 3",
  testlab_step4: "Test Lab Step 4",
  complete: "Complete",
  issues: "Issue Management",
  requires_reupload: "Re-upload required",
};

function statusLabel(status) {
  if (STATUS_LABELS[status]) return STATUS_LABELS[status];
  const text = String(status || "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function moduleTag(status) {
  if (status === "complete" || status === "issues") return "Issue Management";
  if (status === "sourcing") return "Data Sourcing";
  return "Test Lab";
}

export default function Inventory() {
  const [rows, setRows] = useState([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getItemsV2().then(setRows).catch((e) => setError(e.message));
  }, []);

  // Feedback 1.2: one search over BOTH sections (name, kind, status, use case,
  // target variable and its description).
  const filtered = useMemo(() => rows.filter((row) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return [row.name, row.kind, statusLabel(row.status), row.use_case,
      row.target_variable, row.target_description]
      .some((v) => (v || "").toLowerCase().includes(q));
  }), [rows, query]);

  const databases = filtered.filter((r) => r.kind === "database");
  const datasets = filtered.filter((r) => r.kind === "dataset");

  const counts = useMemo(() => {
    const out = { database: { inProgress: 0, completed: 0 }, dataset: { inProgress: 0, completed: 0 } };
    for (const row of rows) {
      const bucket = out[row.kind];
      if (!bucket) continue;
      if (row.status === "complete") bucket.completed += 1;
      else bucket.inProgress += 1;
    }
    return out;
  }, [rows]);

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-950">Data Inventory</h1>
        <p className="mt-1 text-sm text-slate-500">Uploaded databases and datasets move through sourcing, profiling, test lab, and completion. Start a new upload from Data Sourcing in the left menu.</p>
      </div>

      {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      <section className="mb-6 grid gap-3 md:grid-cols-2">
        {KIND_CARDS.map(({ key, title, icon: Icon, text }) => (
          <div key={key} className="rounded-lg border border-slate-200 bg-white p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-slate-950">
                <Icon className="h-5 w-5 text-dq-purple" />
                <span className="text-base font-semibold">{title}</span>
              </div>
              <div className="flex gap-4 text-right">
                <div>
                  <div className="text-lg font-bold leading-tight text-slate-900">{counts[key].inProgress}</div>
                  <div className="text-[10px] uppercase text-slate-500">In progress</div>
                </div>
                <div>
                  <div className="text-lg font-bold leading-tight text-emerald-700">{counts[key].completed}</div>
                  <div className="text-[10px] uppercase text-slate-500">Completed</div>
                </div>
              </div>
            </div>
            <p className="mt-2 text-sm text-slate-600">{text}</p>
          </div>
        ))}
      </section>

      <div className="mb-5 flex max-w-sm items-center gap-2 rounded-md border border-slate-200 bg-white px-3">
        <Search className="h-4 w-4 text-slate-400" />
        <Input className="border-0 shadow-none focus-visible:ring-0" placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>

      <ItemSection title="Databases" rows={databases} showUseCase={false} />
      <ItemSection title="Datasets" rows={datasets} showUseCase />
    </main>
  );
}

function ItemSection({ title, rows, showUseCase }) {
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-lg font-semibold text-slate-950">{title}</h2>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Last Active Module</th>
              <th className="px-4 py-3">Health Score</th>
              <th className="px-4 py-3">Active Issues</th>
              {showUseCase && <th className="px-4 py-3">Use case</th>}
              {showUseCase && <th className="px-4 py-3">Target Variable</th>}
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const tag = row.module_tag || moduleTag(row.status);
              return (
                <tr key={row.item_id} className="border-t border-slate-100">
                  <td className="px-4 py-3 font-medium text-slate-900">{row.name}</td>
                  <td className="px-4 py-3 text-slate-700">{statusLabel(row.status)}</td>
                  <td className="px-4 py-3"><span className={`rounded-full px-2 py-1 text-xs font-medium ${tagTone[tag] || "bg-slate-100 text-slate-700"}`}>{tag}</span></td>
                  <td className="px-4 py-3 text-slate-700">{row.health_score == null ? "—" : Number(row.health_score).toFixed(1)}</td>
                  <td className={`px-4 py-3 ${row.active_issues ? "font-semibold text-red-700" : "text-slate-700"}`}>{row.active_issues ?? 0}</td>
                  {showUseCase && <td className="px-4 py-3 text-slate-600">{row.use_case || "-"}</td>}
                  {showUseCase && (
                    <td className="px-4 py-3 text-slate-600" title={row.target_variable || undefined}>
                      {row.target_description || row.target_variable || "-"}
                    </td>
                  )}
                  <td className="px-4 py-3 text-right"><Button asChild variant="outline" size="sm"><Link to="/test-lab">Open</Link></Button></td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={showUseCase ? 8 : 6} className="px-4 py-8 text-center text-slate-500">
                  Nothing uploaded yet. Start in Data Sourcing.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
