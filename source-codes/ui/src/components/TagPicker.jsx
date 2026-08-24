import { useEffect, useState } from "react";
import { Tags, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getItemTagsV3, getTaxonomyDimensionsV3, removeItemTagV3, setItemTagsV3 } from "@/api/client";

// RCA Stage 1 — governed business-tag picker for an item (dq_item).
// Dimensions/values are fetched from the backend, never hardcoded here
// (docs/rca/00-contracts.md §7 / migration plan §5.2).

export function TagChips({ tags, empty = "No tags yet." }) {
  if (!tags?.length) return <span className="text-xs text-slate-400">{empty}</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((t) => (
        <Badge key={t.assignment_id} variant="secondary" title={`${t.dimension_label}: ${t.value_label} (${t.origin})`}>
          {t.dimension_label}: {t.value_label}
        </Badge>
      ))}
    </div>
  );
}

export default function TagPicker({ itemId }) {
  const [dimensions, setDimensions] = useState([]);
  const [tags, setTags] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [removing, setRemoving] = useState(null); // assignment_id awaiting a reason
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const reload = () => {
    getTaxonomyDimensionsV3().then(setDimensions).catch(() => setDimensions([]));
    getItemTagsV3(itemId).then((rows) => {
      setTags(rows);
      setSelected(new Set(rows.map((t) => `${t.dimension_key}:${t.value_key}`)));
    }).catch(() => setTags([]));
  };
  useEffect(reload, [itemId]);

  const toggle = (key) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const alreadyTagged = new Set(tags.map((t) => `${t.dimension_key}:${t.value_key}`));
  const dirty = [...selected].some((k) => !alreadyTagged.has(k));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const toAdd = [...selected].filter((k) => !alreadyTagged.has(k));
      if (toAdd.length) await setItemTagsV3(itemId, toAdd);
      reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const confirmRemove = async () => {
    if (!reason.trim()) return;
    try {
      await removeItemTagV3(itemId, removing, reason.trim());
      setRemoving(null);
      setReason("");
      reload();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Tags className="h-4 w-4 text-dq-purple" /> Business tags
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Governed metadata (risk type, portfolio, product, use case) that carries through tests,
        results, issues, and RCA cases for this item.
      </p>

      {tags.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {tags.map((t) => (
            <span key={t.assignment_id}
              className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
              {t.dimension_label}: {t.value_label}
              <button type="button" className="text-slate-400 hover:text-red-600" title="Remove tag"
                onClick={() => { setRemoving(t.assignment_id); setReason(""); }}>
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
            value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button size="sm" disabled={!reason.trim()} onClick={confirmRemove}>Confirm</Button>
          <Button size="sm" variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {dimensions.map((dim) => (
          <div key={dim.dimension_id}>
            <div className="mb-1 text-xs font-semibold uppercase text-slate-500">{dim.label}</div>
            <div className="flex flex-wrap gap-1.5">
              {dim.values.map((v) => {
                const key = `${dim.key}:${v.key}`;
                const active = selected.has(key);
                return (
                  <button key={v.value_id} type="button" onClick={() => toggle(key)}
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${
                      active ? "border-dq-purple bg-dq-purple/10 text-dq-purple" : "border-slate-200 text-slate-600 hover:border-slate-300"
                    }`}>
                    {v.label}
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
