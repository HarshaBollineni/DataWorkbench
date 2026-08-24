import { useEffect, useState } from "react";
import { Tags, X } from "lucide-react";

import {
  getKbDocumentTagsV3, getTaxonomyDimensionsV3, removeKbDocumentTagV3, setKbDocumentTagsV3,
} from "@/api/client";
import { Button } from "@/components/ui/button";

export default function KbDocumentTagPicker({ documentId }) {
  const [dimensions, setDimensions] = useState([]);
  const [tags, setTags] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [removing, setRemoving] = useState(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const reload = () => {
    getTaxonomyDimensionsV3().then(setDimensions).catch(() => setDimensions([]));
    getKbDocumentTagsV3(documentId).then((rows) => {
      setTags(rows);
      setSelected(new Set(rows.map((tag) => `${tag.dimension_key}:${tag.value_key}`)));
    }).catch(() => setTags([]));
  };
  useEffect(reload, [documentId]);

  const toggle = (key) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const alreadyTagged = new Set(tags.map((tag) => `${tag.dimension_key}:${tag.value_key}`));
  const dirty = [...selected].some((key) => !alreadyTagged.has(key));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const toAdd = [...selected].filter((key) => !alreadyTagged.has(key));
      if (toAdd.length) await setKbDocumentTagsV3(documentId, toAdd);
      reload();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  };

  const confirmRemove = async () => {
    if (!reason.trim()) return;
    try {
      await removeKbDocumentTagV3(documentId, removing, reason.trim());
      setRemoving(null);
      setReason("");
      reload();
    } catch (failure) {
      setError(failure.message);
    }
  };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Tags className="h-4 w-4 text-dq-purple" /> Taxonomy dimensions
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Risk type, portfolio, product, use case — the governed, user-facing tags for this document.
      </p>
      {tags.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {tags.map((tag) => (
            <span key={tag.assignment_id}
              className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
              {tag.dimension_label}: {tag.value_label}
              <button type="button" className="text-slate-400 hover:text-red-600" title="Remove tag"
                onClick={() => { setRemoving(tag.assignment_id); setReason(""); }}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {removing && (
        <div className="mb-3 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 p-2">
          <input className="h-8 flex-1 rounded-md border border-slate-200 px-2 text-xs"
            placeholder="Reason for removing this tag (required)"
            value={reason} onChange={(event) => setReason(event.target.value)} />
          <Button size="sm" disabled={!reason.trim()} onClick={confirmRemove}>Confirm</Button>
          <Button size="sm" variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {dimensions.map((dimension) => (
          <div key={dimension.dimension_id}>
            <div className="mb-1 text-xs font-semibold uppercase text-slate-500">{dimension.label}</div>
            <div className="flex flex-wrap gap-1.5">
              {dimension.values.map((value) => {
                const key = `${dimension.key}:${value.key}`;
                const active = selected.has(key);
                return (
                  <button key={value.value_id} type="button" onClick={() => toggle(key)}
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${
                      active ? "border-dq-purple bg-dq-purple/10 text-dq-purple" : "border-slate-200 text-slate-600 hover:border-slate-300"
                    }`}>
                    {value.label}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
      <div className="mt-3">
        <Button size="sm" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save tags"}
        </Button>
      </div>
    </div>
  );
}
