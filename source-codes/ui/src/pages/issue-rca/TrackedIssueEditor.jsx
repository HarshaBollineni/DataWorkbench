import { useState } from "react";
import { Check, Ticket } from "lucide-react";

import { patchTrackedIssueV2 } from "@/api/client";
import { Button } from "@/components/ui/button";

function Field({ label, children }) {
  return <label className="block text-xs font-semibold text-slate-600">{label}<div className="mt-1 font-normal">{children}</div></label>;
}

export default function TrackedIssueEditor({ tracked, onSaved, onError }) {
  const [form, setForm] = useState({ owner: tracked.owner || "", priority: tracked.priority || "Medium", target_date: tracked.target_date || "", status: tracked.status || "Open" });
  const [saved, setSaved] = useState("");
  const save = async () => {
    try {
      const updated = await patchTrackedIssueV2(tracked.issue_id, form);
      onSaved?.(updated);
      setSaved("Saved");
      setTimeout(() => setSaved(""), 1500);
    } catch (reason) { onError?.(reason.message); }
  };
  return <div className="mt-3 rounded-md border border-slate-200 bg-white p-3">
    <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><Ticket className="h-4 w-4" /> {tracked.issue_id} · {tracked.title}</div>
    <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
      <Field label="Owner"><input className="w-full rounded-md border border-slate-200 p-2 text-sm" value={form.owner} onChange={(event) => setForm({ ...form, owner: event.target.value })} /></Field>
      <Field label="Priority"><select className="w-full rounded-md border border-slate-200 p-2 text-sm" value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })}>{["Critical", "High", "Medium", "Low"].map((priority) => <option key={priority}>{priority}</option>)}</select></Field>
      <Field label="Target date"><input type="date" className="w-full rounded-md border border-slate-200 p-2 text-sm" value={form.target_date} onChange={(event) => setForm({ ...form, target_date: event.target.value })} /></Field>
      <Field label="Status"><select className="w-full rounded-md border border-slate-200 p-2 text-sm" value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}>{["Open", "In Progress", "Resolved"].map((status) => <option key={status}>{status}</option>)}</select></Field>
    </div>
    <div className="mt-2 flex items-center gap-2"><Button size="sm" onClick={save}><Check className="h-4 w-4" /> Update Issue Details</Button><span className="text-sm text-emerald-600">{saved}</span></div>
  </div>;
}
