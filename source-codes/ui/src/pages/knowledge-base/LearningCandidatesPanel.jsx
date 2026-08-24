import { useEffect, useState } from "react";
import { Archive, BookOpen, CheckCircle2 } from "lucide-react";

import {
  archiveKbRuleV3, getKbLearningCandidatesV3, publishKbRuleV3,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function LearningCandidatesPanel() {
  const [candidates, setCandidates] = useState([]);
  const [reasons, setReasons] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const reload = async () => setCandidates(await getKbLearningCandidatesV3());
  useEffect(() => {
    let current = true;
    getKbLearningCandidatesV3().then((rows) => { if (current) setCandidates(rows); })
      .catch((requestError) => { if (current) setError(requestError.message); });
    return () => { current = false; };
  }, []);
  const publish = async (candidate) => {
    setBusy(candidate.candidate_id); setError(""); setMessage("");
    try {
      await publishKbRuleV3(candidate.candidate_id, {
        category: "case_history", related_tables: candidate.related_tables || [],
        trust_level: "human_confirmed",
      });
      setMessage("Candidate reviewed and published as governed case-history knowledge.");
      await reload();
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  };
  const archive = async (candidate) => {
    const reason = (reasons[candidate.candidate_id] || "").trim();
    if (!reason) return;
    setBusy(candidate.candidate_id); setError(""); setMessage("");
    try {
      await archiveKbRuleV3(candidate.candidate_id, reason);
      setMessage("Candidate archived without becoming governed knowledge.");
      await reload();
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  };
  return <div className="grid gap-4">
    <section className="rounded-lg border border-blue-200 bg-blue-50 p-4"><div className="flex gap-2"><BookOpen className="mt-0.5 h-5 w-5 text-blue-700" /><div><h2 className="font-semibold text-blue-950">Reusable-knowledge candidates</h2><p className="mt-1 text-xs text-blue-800">These were explicitly proposed after an RCA closed. They are not governed rules until a KB reviewer publishes them. Closing a case alone never creates an entry here.</p></div></div></section>
    {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
    {message && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</div>}
    {candidates.map((candidate) => { const proposal = candidate.proposal || {}; const pending = ["draft", "pending_review"].includes(candidate.lifecycle_state); return <article key={candidate.candidate_id} className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="flex items-center gap-2"><strong className="text-sm text-slate-900">{proposal.reusable_lesson || candidate.rule_text.split("\n")[0]}</strong><Badge variant={candidate.lifecycle_state === "published" ? "success" : candidate.lifecycle_state === "archived" ? "secondary" : "warning"}>{candidate.lifecycle_state}</Badge></div><p className="mt-1 text-xs text-slate-500">Source RCA {candidate.case_id} · proposed by {candidate.created_by}</p></div><span className="text-xs text-slate-400">{candidate.created_at}</span></div><dl className="mt-3 grid gap-3 rounded-md bg-slate-50 p-3 text-xs sm:grid-cols-2"><div><dt className="font-semibold text-slate-600">Applicability</dt><dd className="mt-1 text-slate-700">{proposal.applicability_scope || "Legacy candidate—scope not recorded"}</dd></div><div><dt className="font-semibold text-slate-600">Why it generalizes</dt><dd className="mt-1 text-slate-700">{proposal.generalization_reason || "Legacy candidate—rationale not recorded"}</dd></div><div><dt className="font-semibold text-slate-600">Related tables</dt><dd className="mt-1 text-slate-700">{(proposal.related_tables || candidate.related_tables || []).join(", ") || "Not specified"}</dd></div><div><dt className="font-semibold text-slate-600">Evidence references</dt><dd className="mt-1 break-words text-slate-700">{(proposal.supporting_evidence_ids || []).join(", ") || "Legacy candidate—references retained in RCA"}</dd></div></dl>{pending && <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" disabled={busy === candidate.candidate_id} onClick={() => publish(candidate)}><CheckCircle2 className="h-4 w-4" /> Review and publish</Button><input className="h-9 min-w-64 flex-1 rounded-md border border-slate-200 px-3 text-xs" value={reasons[candidate.candidate_id] || ""} onChange={(event) => setReasons((current) => ({ ...current, [candidate.candidate_id]: event.target.value }))} placeholder="Reason to archive candidate" /><Button size="sm" variant="outline" disabled={busy === candidate.candidate_id || !(reasons[candidate.candidate_id] || "").trim()} onClick={() => archive(candidate)}><Archive className="h-4 w-4" /> Archive</Button></div>}</article>; })}
    {!candidates.length && <div className="rounded-md border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">No reusable-knowledge candidates have been proposed.</div>}
  </div>;
}
