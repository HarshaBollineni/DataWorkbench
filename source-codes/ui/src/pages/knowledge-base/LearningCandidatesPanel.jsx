import { useEffect, useState } from "react";
import { AlertTriangle, Archive, BookOpen, CheckCircle2, GitBranch } from "lucide-react";

import {
  archiveKbRuleV3, getKbLearningCandidatesV3, publishKbRuleV3,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const D11_PROPOSAL_KIND = "t2_d11_expected_direction";

const DIRECTION_LABELS = {
  INCREASING: "Higher feature value → Higher risk",
  DECREASING: "Higher feature value → Lower risk",
  NON_MONOTONIC: "Non-monotonic risk relationship",
  NO_CLEAR_DIRECTION: "No clear risk direction",
};

const ORIENTATION_LABELS = {
  SAME: "Moves in the same direction as the KB concept",
  INVERSE: "Moves in the opposite direction to the KB concept",
  UNDETERMINED: "Representation direction is undetermined",
};

function lifecycleVariant(state) {
  if (state === "published") return "success";
  if (state === "archived" || state === "superseded") return "secondary";
  if (state === "under_suspicion") return "destructive";
  return "warning";
}

function isDirectionalityProposal(candidate) {
  const proposal = candidate.proposal || {};
  return proposal.proposal_kind === D11_PROPOSAL_KIND
    || Number(proposal.diagnostic_id) === 11;
}

function conceptLabel(value) {
  if (!value) return "No KB concept linked";
  return String(value).replaceAll("_", " ");
}

function evidenceVariant(state) {
  if (state === "conflicting") return "warning";
  if (state === "supporting") return "success";
  return "outline";
}

function CandidateHeader({ candidate, directionality }) {
  const proposal = candidate.proposal || {};
  const fallback = String(candidate.rule_text || "Knowledge proposal").split("\n")[0];
  const title = directionality
    ? proposal.feature || "Expected-direction proposal"
    : proposal.reusable_lesson || fallback;
  const sourceRun = proposal.source_run_id || candidate.case_id;

  return <div className="flex flex-wrap items-start justify-between gap-2">
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <strong className="text-sm text-slate-900">{title}</strong>
        {directionality && <Badge variant="outline">Diagnostic 11</Badge>}
        <Badge variant={lifecycleVariant(candidate.lifecycle_state)}>{candidate.lifecycle_state}</Badge>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {directionality
          ? <>Source Diagnostic 11{sourceRun ? <> · run <span className="font-mono">{sourceRun}</span></> : ""}</>
          : <>Source RCA {candidate.case_id || "not recorded"}</>}
        {candidate.created_by ? <> · proposed by {candidate.created_by}</> : ""}
      </p>
    </div>
    <span className="text-xs text-slate-400">{candidate.created_at}</span>
  </div>;
}

function RcaCandidateDetails({ candidate }) {
  const proposal = candidate.proposal || {};
  return <dl className="mt-3 grid gap-3 rounded-md bg-slate-50 p-3 text-xs sm:grid-cols-2">
    <div>
      <dt className="font-semibold text-slate-600">Applicability</dt>
      <dd className="mt-1 text-slate-700">{proposal.applicability_scope || "Legacy candidate—scope not recorded"}</dd>
    </div>
    <div>
      <dt className="font-semibold text-slate-600">Why it generalizes</dt>
      <dd className="mt-1 text-slate-700">{proposal.generalization_reason || "Legacy candidate—rationale not recorded"}</dd>
    </div>
    <div>
      <dt className="font-semibold text-slate-600">Related tables</dt>
      <dd className="mt-1 text-slate-700">{(proposal.related_tables || candidate.related_tables || []).join(", ") || "Not specified"}</dd>
    </div>
    <div>
      <dt className="font-semibold text-slate-600">Evidence references</dt>
      <dd className="mt-1 break-words text-slate-700">{(proposal.supporting_evidence_ids || []).join(", ") || "Legacy candidate—references retained in RCA"}</dd>
    </div>
  </dl>;
}

function DirectionalityEvidence({ candidate }) {
  const evidence = candidate.proposal_evidence || [];
  const supporting = evidence.filter((row) => row.evidence_state === "supporting").length;
  const conflicting = evidence.filter((row) => row.evidence_state === "conflicting").length;

  return <section className={`rounded-md border p-3 ${conflicting > 0
    ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-slate-50"}`}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2">
        {conflicting > 0
          ? <AlertTriangle className="h-4 w-4 text-amber-700" />
          : <GitBranch className="h-4 w-4 text-slate-500" />}
        <strong className="text-xs text-slate-800">Source-run evidence</strong>
      </div>
      <div className="flex flex-wrap gap-1">
        <Badge variant="success">{supporting} supporting</Badge>
        <Badge variant={conflicting > 0 ? "warning" : "secondary"}>{conflicting} conflicting</Badge>
      </div>
    </div>
    {conflicting > 0 && <p className="mt-2 text-xs text-amber-900">
      One or more runs propose a different concept, representation, or direction. Review the
      evidence below before publishing or archiving this candidate.
    </p>}
    {!evidence.length ? <p className="mt-2 text-xs text-slate-500">
      No source-run evidence details are attached. Review the proposal text and lineage before
      taking a governance action.
    </p> : <details className="mt-2" defaultOpen={conflicting > 0}>
      <summary className="cursor-pointer text-xs font-medium text-slate-700">
        Review {evidence.length} evidence record{evidence.length === 1 ? "" : "s"}
      </summary>
      <ol className="mt-2 grid gap-2">
        {evidence.map((row, index) => <li key={row.evidence_id || `${row.source_run_id}-${index}`} className="rounded border border-slate-200 bg-white p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={evidenceVariant(row.evidence_state)}>{row.evidence_state || "unclassified"}</Badge>
              <span className="text-slate-500">Run <span className="font-mono text-slate-700">{row.source_run_id || "not recorded"}</span></span>
            </div>
            <span className="text-slate-400">{row.created_at}</span>
          </div>
          <div className="mt-2 grid gap-1 text-slate-700 sm:grid-cols-2">
            <p><strong>Direction:</strong> {DIRECTION_LABELS[row.expected_direction] || row.expected_direction || "Not recorded"}</p>
            <p><strong>KB concept:</strong> {conceptLabel(row.canonical_feature)}</p>
            <p><strong>Representation:</strong> {ORIENTATION_LABELS[row.representation_orientation] || row.representation_orientation || "Not linked"}</p>
            <p><strong>Submitted by:</strong> {row.actor || "Not recorded"}</p>
          </div>
          {row.rationale && <p className="mt-2 border-t border-slate-100 pt-2 leading-5 text-slate-600"><strong>Rationale:</strong> {row.rationale}</p>}
        </li>)}
      </ol>
    </details>}
  </section>;
}

function DirectionalityCandidateDetails({ candidate }) {
  const proposal = candidate.proposal || {};
  const table = (candidate.related_tables || [])[0] || proposal.table || "Not specified";
  return <div className="mt-3 grid gap-3">
    <dl className="grid gap-3 rounded-md bg-indigo-50 p-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
      <div>
        <dt className="font-semibold text-indigo-700">Source feature</dt>
        <dd className="mt-1 break-words text-slate-800">{proposal.feature || "Not recorded"}</dd>
      </div>
      <div>
        <dt className="font-semibold text-indigo-700">Source table</dt>
        <dd className="mt-1 break-words text-slate-800">{table}</dd>
      </div>
      <div>
        <dt className="font-semibold text-indigo-700">Linked KB concept</dt>
        <dd className="mt-1 capitalize text-slate-800">{conceptLabel(proposal.canonical_feature)}</dd>
      </div>
      <div>
        <dt className="font-semibold text-indigo-700">Expected risk direction</dt>
        <dd className="mt-1 text-slate-800">{DIRECTION_LABELS[proposal.expected_direction] || proposal.expected_direction || "Not recorded"}</dd>
      </div>
      <div>
        <dt className="font-semibold text-indigo-700">Representation relationship</dt>
        <dd className="mt-1 text-slate-800">{ORIENTATION_LABELS[proposal.representation_orientation] || proposal.representation_orientation || "Not linked"}</dd>
      </div>
      <div>
        <dt className="font-semibold text-indigo-700">Knowledge category</dt>
        <dd className="mt-1 text-slate-800">{candidate.category || "domain_fact"}</dd>
      </div>
      {proposal.based_on_rule_id && <div className="sm:col-span-2 lg:col-span-3">
        <dt className="font-semibold text-indigo-700">Proposed revision of</dt>
        <dd className="mt-1 font-mono text-slate-800">{proposal.based_on_rule_id}</dd>
      </div>}
    </dl>
    <DirectionalityEvidence candidate={candidate} />
  </div>;
}

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
    const directionality = isDirectionalityProposal(candidate);
    const proposal = candidate.proposal || {};
    const relatedTables = candidate.related_tables?.length
      ? candidate.related_tables : proposal.related_tables || [];
    setBusy(candidate.candidate_id); setError(""); setMessage("");
    try {
      await publishKbRuleV3(candidate.candidate_id, {
        category: directionality ? candidate.category || "domain_fact" : "case_history",
        related_tables: relatedTables,
        related_columns: directionality && proposal.feature ? [proposal.feature] : [],
        trust_level: "human_confirmed",
      });
      setMessage(directionality
        ? "Directionality proposal reviewed and published as governed domain knowledge."
        : "Candidate reviewed and published as governed case-history knowledge.");
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
      setMessage(isDirectionalityProposal(candidate)
        ? "Directionality proposal archived without changing governed knowledge."
        : "Candidate archived without becoming governed knowledge.");
      await reload();
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  };

  return <div className="grid gap-4">
    <section className="rounded-lg border border-blue-200 bg-blue-50 p-4">
      <div className="flex gap-2">
        <BookOpen className="mt-0.5 h-5 w-5 text-blue-700" />
        <div>
          <h2 className="font-semibold text-blue-950">Reusable-knowledge candidates</h2>
          <p className="mt-1 text-xs text-blue-800">
            Candidates come from an explicit RCA learning submission or a user-confirmed Diagnostic
            11 direction proposal. They do not become governed knowledge until a KB reviewer
            publishes them; the source workflow and AI suggestions cannot publish or archive rules.
          </p>
        </div>
      </div>
    </section>
    {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
    {message && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</div>}
    {candidates.map((candidate) => {
      const directionality = isDirectionalityProposal(candidate);
      const pending = ["draft", "pending_review"].includes(candidate.lifecycle_state);
      return <article key={candidate.candidate_id} className="rounded-lg border border-slate-200 bg-white p-4">
        <CandidateHeader candidate={candidate} directionality={directionality} />
        {directionality
          ? <DirectionalityCandidateDetails candidate={candidate} />
          : <RcaCandidateDetails candidate={candidate} />}
        {pending && <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy === candidate.candidate_id} onClick={() => publish(candidate)}>
            <CheckCircle2 className="h-4 w-4" /> Review and publish
          </Button>
          <input
            className="h-9 min-w-64 flex-1 rounded-md border border-slate-200 px-3 text-xs"
            value={reasons[candidate.candidate_id] || ""}
            onChange={(event) => setReasons((current) => ({
              ...current, [candidate.candidate_id]: event.target.value,
            }))}
            placeholder="Reason to archive candidate"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy === candidate.candidate_id || !(reasons[candidate.candidate_id] || "").trim()}
            onClick={() => archive(candidate)}
          >
            <Archive className="h-4 w-4" /> Archive
          </Button>
        </div>}
      </article>;
    })}
    {!candidates.length && <div className="rounded-md border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
      No reusable-knowledge candidates have been proposed.
    </div>}
  </div>;
}
