import { useEffect, useState } from "react";
import {
  Archive, BookOpen, Check, FileText, Send, UploadCloud,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  archiveKbRuleV3, getKbDocumentsV3, getKbDocumentV3,
  getKbPlaybackV3, getKbRulesV3, getKbVersionPreviewV3, publishKbRuleV3,
  submitKbVersionV3, uploadKbDocumentV3,
} from "@/api/client";
import KbDocumentTagPicker from "@/pages/knowledge-base/KbDocumentTagPicker";
import DiagnosticPackagesPanel from "@/pages/knowledge-base/DiagnosticPackagesPanel";
import LearningCandidatesPanel from "@/pages/knowledge-base/LearningCandidatesPanel";
import { BindingBadge, ParseReportPanel, PlaybackSummary } from "@/pages/knowledge-base/KnowledgeReviewPanels";

// RCA Stage 2 / Phase 5 (docs/0.4.0/04-kb-contract.md) — Knowledge Base
// module. No LLM ever writes a rule directly: everything here is a human-
// driven upload -> tag -> table-aware parse -> playback -> review -> publish
// flow through deterministic services.

const CATEGORIES = ["structure", "lineage", "domain_fact", "ownership", "case_history"];

function LifecycleBadge({ state }) {
  const variant = state === "published" ? "success" : ["archived", "superseded"].includes(state) ? "secondary"
    : state === "under_suspicion" ? "destructive" : state === "pending_review" ? "warning" : "outline";
  return <Badge variant={variant}>{state}</Badge>;
}

function DocumentsPanel() {
  const [documents, setDocuments] = useState([]);
  const [selected, setSelected] = useState(null); // full document + versions
  const [preview, setPreview] = useState(null);
  const [playback, setPlayback] = useState(null); // KB-03 — set once submitted
  const [category, setCategory] = useState("domain_fact");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);

  // Braced body: useEffect(reload, []) below requires the effect callback to
  // return undefined or a cleanup function, never a Promise.
  const reload = () => { getKbDocumentsV3().then(setDocuments).catch(() => setDocuments([])); };
  useEffect(reload, []);

  const openDocument = async (documentId) => {
    setError(""); setMessage(""); setPlayback(null);
    const doc = await getKbDocumentV3(documentId);
    setSelected(doc);
    const latest = doc.versions[0];
    if (!latest) { setPreview(null); return; }
    const p = await getKbVersionPreviewV3(latest.version_id);
    setPreview(p);
    // Already submitted in an earlier session — the playback summary is
    // still meaningful to show (KB-03 is a record, not a one-time toast).
    if (p.review_state !== "draft") {
      getKbPlaybackV3(latest.version_id).then(setPlayback).catch(() => setPlayback(null));
    }
  };

  const upload = async (file) => {
    if (!file) return;
    setUploading(true); setError(""); setMessage("");
    try {
      const result = await uploadKbDocumentV3(file, category);
      await reload();
      await openDocument(result.document_id);
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  const submitForReview = async () => {
    if (!selected?.versions?.length) return;
    try {
      const versionId = selected.versions[0].version_id;
      const p = await submitKbVersionV3(versionId, category);
      setPreview(p);
      setMessage("Submitted for review — draft rules extracted below.");
      setPlayback(await getKbPlaybackV3(versionId));
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,320px)_1fr]">
      <div>
        <div className="mb-3 rounded-md border border-slate-200 bg-white p-4">
          <div className="mb-2 text-sm font-semibold text-slate-900">Upload a document</div>
          <select className="mb-2 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm"
            value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <input type="file" accept=".txt,.md,.docx,.pdf"
            onChange={(e) => upload(e.target.files?.[0])} disabled={uploading}
            className="block w-full text-xs" />
          <p className="mt-1 text-xs text-slate-400">.txt, .md, .docx, or text-based .pdf — max 20&nbsp;MB.</p>
        </div>
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          {documents.map((d) => (
            <button key={d.document_id} type="button" onClick={() => openDocument(d.document_id)}
              className={`flex w-full items-center justify-between gap-2 border-t border-slate-100 px-3 py-2 text-left text-sm first:border-t-0 hover:bg-slate-50 ${selected?.document_id === d.document_id ? "bg-dq-purple/5" : ""}`}>
              <span className="flex min-w-0 items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                <span className="truncate">{d.title}</span>
              </span>
              <LifecycleBadge state={d.latest_review_state || "draft"} />
            </button>
          ))}
          {documents.length === 0 && <div className="px-3 py-4 text-center text-xs text-slate-400">No documents yet.</div>}
        </div>
      </div>

      <div>
        {error && <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
        {message && <div className="mb-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{message}</div>}
        {!selected && <div className="rounded-md border border-dashed border-slate-200 p-8 text-center text-sm text-slate-400">Select or upload a document to preview it.</div>}
        {selected && preview && (
          <div className="space-y-4">
            <KbDocumentTagPicker documentId={selected.document_id} />

            <div className="rounded-md border border-slate-200 bg-white p-4">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-sm font-semibold text-slate-900">Converted Markdown preview</div>
                <span className="text-xs text-slate-400">{preview.converter_name} v{preview.converter_version}</span>
              </div>
              {(preview.conversion_warnings_json || []).length > 0 && (
                <div className="mb-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
                  {preview.conversion_warnings_json.join("; ")}
                </div>
              )}
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs text-slate-700">
                {preview.converted_markdown}
              </pre>
              {preview.review_state === "draft" && (
                <Button size="sm" className="mt-3" onClick={submitForReview}>
                  <Send className="h-4 w-4" /> Submit for review
                </Button>
              )}
            </div>

            {playback && <PlaybackSummary playback={playback} />}
            {preview.review_state !== "draft" && <ParseReportPanel versionId={preview.version_id} />}

            {preview.sections?.length > 0 && (
              <div className="rounded-md border border-slate-200 bg-white p-4">
                <div className="mb-2 text-sm font-semibold text-slate-900">Extracted rules ({preview.rules.length})</div>
                <div className="max-h-[32rem] space-y-2 overflow-auto">
                  {preview.sections.map((sec) => {
                    const rule = preview.rules.find((r) => r.section_id === sec.section_id);
                    return (
                      <div key={sec.section_id} className="rounded-md border border-slate-200 p-3 text-sm">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-medium text-slate-900">{sec.heading}</span>
                          <span className="flex items-center gap-1.5">
                            {rule && <BindingBadge status={rule.binding_status} />}
                            {rule && <LifecycleBadge state={rule.lifecycle_state} />}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-slate-500">{sec.body_markdown.slice(0, 200)}</p>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function PublishRow({ rule, onDone }) {
  const [category, setCategory] = useState(rule.category || "domain_fact");
  const [relatedTables, setRelatedTables] = useState(
    (rule.related_tables_json || []).join(", "),
  );
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const publish = async () => {
    setError("");
    try {
      await publishKbRuleV3(rule.rule_id, {
        category,
        related_tables: relatedTables.split(",").map((t) => t.trim()).filter(Boolean),
        related_columns: rule.related_columns_json || [],
      });
      onDone();
    } catch (e) { setError(e.message); }
  };
  const archive = async () => {
    if (!reason.trim()) return;
    setError("");
    try {
      await archiveKbRuleV3(rule.rule_id, reason.trim());
      onDone();
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-slate-900">
          {rule.source_rule_id ? `${rule.source_rule_id} — ` : ""}
          {rule.rule_text.split("\n")[0].slice(0, 80)}
        </span>
        <span className="flex items-center gap-1.5">
          <BindingBadge status={rule.binding_status} />
          <LifecycleBadge state={rule.lifecycle_state} />
        </span>
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      {["draft", "pending_review"].includes(rule.lifecycle_state) && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs" value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <input className="h-8 w-48 rounded-md border border-slate-200 px-2 text-xs"
            placeholder="Related tables (comma-separated)"
            value={relatedTables} onChange={(e) => setRelatedTables(e.target.value)} />
          <Button size="sm" onClick={publish}><Check className="h-4 w-4" /> Publish</Button>
        </div>
      )}
      {rule.lifecycle_state === "published" && (
        <div className="mt-2 flex items-center gap-2">
          <input className="h-8 flex-1 rounded-md border border-slate-200 px-2 text-xs"
            placeholder="Reason for archiving (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button size="sm" variant="outline" disabled={!reason.trim()} onClick={archive}>
            <Archive className="h-4 w-4" /> Archive
          </Button>
        </div>
      )}
    </div>
  );
}

function RulesPanel() {
  const [rules, setRules] = useState([]);
  const [lifecycleFilter, setLifecycleFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [bindingFilter, setBindingFilter] = useState("");

  const reload = () => {
    getKbRulesV3({
      lifecycleState: lifecycleFilter || undefined, category: categoryFilter || undefined,
      bindingStatus: bindingFilter || undefined,
    }).then(setRules);
  };
  useEffect(reload, [lifecycleFilter, categoryFilter, bindingFilter]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm" value={lifecycleFilter} onChange={(e) => setLifecycleFilter(e.target.value)}>
          <option value="">Any lifecycle state</option>
          {["draft", "pending_review", "published", "superseded", "under_suspicion", "archived"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">Any category</option>
          {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm" value={bindingFilter} onChange={(e) => setBindingFilter(e.target.value)}>
          <option value="">Any binding status</option>
          {["bound", "reference-only", "unparsed"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      <div className="grid gap-2">
        {rules.map((r) => <PublishRow key={r.rule_id} rule={r} onDone={reload} />)}
        {rules.length === 0 && <div className="rounded-md border border-dashed border-slate-200 p-6 text-center text-xs text-slate-400">No rules match the current filters.</div>}
      </div>
    </div>
  );
}

export default function KnowledgeBase() {
  const [tab, setTab] = useState("documents");

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-950"><BookOpen className="h-6 w-6 text-dq-purple" /> Knowledge Base</h1>
        <p className="mt-1 text-sm text-slate-500">
          Governed plain-English rules — structure, lineage, domain facts, ownership, and case history.
          Nothing here is written by an AI model directly: every rule change is edited, validated, reviewed, and published by a human.
        </p>
      </div>
      <div className="mb-4 flex gap-2">
        <Button variant={tab === "documents" ? "default" : "outline"} size="sm" onClick={() => setTab("documents")}>
          <UploadCloud className="h-4 w-4" /> Documents
        </Button>
        <Button variant={tab === "rules" ? "default" : "outline"} size="sm" onClick={() => setTab("rules")}>
          <FileText className="h-4 w-4" /> Rules
        </Button>
        <Button variant={tab === "diagnostic-packages" ? "default" : "outline"} size="sm" onClick={() => setTab("diagnostic-packages")}>
          <BookOpen className="h-4 w-4" /> Diagnostic packages
        </Button>
        <Button variant={tab === "learning-candidates" ? "default" : "outline"} size="sm" onClick={() => setTab("learning-candidates")}>
          <BookOpen className="h-4 w-4" /> Learning candidates
        </Button>
      </div>
      {tab === "documents" ? <DocumentsPanel />
        : tab === "rules" ? <RulesPanel />
          : tab === "diagnostic-packages" ? <DiagnosticPackagesPanel /> : <LearningCandidatesPanel />}
    </main>
  );
}
