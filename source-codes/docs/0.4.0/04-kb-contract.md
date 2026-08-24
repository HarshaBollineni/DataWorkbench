# 0.4.0 Knowledge Base contract (KB-01…KB-16)

The pipeline an uploaded knowledge document travels, end to end. Highest-consequence component in
the release (C-34): with no fallback rule set (CFR-04), a parse failure means cross-field cannot
run. Built in Phase 5.

## 1. The pipeline

```
upload → dimension tags → table-aware parse → rule records → parse report
      → playback summary → binding status per rule → review → publish
```

1. **Upload** (KB-02, KB-05, KB-06). Accepted: TXT, Markdown, DOCX, text-based PDF; ≤ 20 MB
   (existing `kb_convert.py` cap). Provenance preserved exactly as 0.3.0: original bytes stored
   content-addressed by SHA-256, converted Markdown immutable with its own hash, converter
   name+version, warnings, review and publication record. Unsupported / corrupt / scanned-image
   documents are rejected safely with **three distinct, clear messages** (5-T7); OCR stays out of
   scope (OOS-09).
2. **Dimension tags** (KB-02, C-35). The uploader tags the document with **taxonomy dimensions** —
   risk type, portfolio, product, use case — multi-select, wired into upload via
   `/knowledge/documents/{id}/tags`. Tags are the **user-facing axis** (§3).
3. **Table-aware parse** (KB-04, C-34). A document of N tabulated rules yields **N rule records**;
   heading-structured prose still parses one-rule-per-H2-section as today (5-T2). No LLM touches
   parsing (KB-14). Extraction hazards are handled and surfaced, never silently guessed (KB-11):
   PDF text extraction collapses `exposure_at_default` → `exposureatdefault` and wraps expressions
   across lines — **recovered role names are shown back for confirmation** (5-T4).
4. **Rule records** (KB-07). Each carries: rule id, severity, type, entity, semantic roles, rule
   text, regulatory reference, encoded exceptions and notes, plus **source page/line** for
   traceability.
5. **Parse report** (KB-10, D-06). A **first-class artifact** shown to the uploader: per-rule
   status with reasons, so they can revise the document and re-upload. Retrievable via API (5-T5).
   This is the intended correction loop.
6. **Playback summary** (KB-03). Short, nicely formatted confirmation: what kind of knowledge was
   found, how many rules, breakdown by framework / type / severity, semantic roles referenced,
   anything unparsed. **For S9 the summary must reach exactly: 49 rules · IFRS9 11 / IRB 36 /
   both 2 · conditional 13, date-ordering 3, domain 26, identity 3, inequality 4 · CRITICAL 6,
   MATERIAL 21, MINOR 22** (5-T1 parity).
7. **Binding** (KB-08/KB-09). Parsing does not make a rule executable. Status per rule:
   - **bound** — executable: the parsed rule matched a registered primitive signature exactly, or
     a human confirmed the binding;
   - **reference-only** — parsed and displayable, no implementation;
   - **unparsed** — with the reason and offending source line.
   **Never execute a predicate recovered from prose** (KB-09 — invariant, negatively tested 5-T3):
   execution reaches only bound rules.
8. **Review → publish** (KB-12, KB-13). Governance retained from 0.3.0 unchanged: trust levels
   (`human_confirmed`/`inferred`), lifecycle (draft → pending_review → published → expired /
   superseded / under_suspicion / archived), shelf life (12 months default, tunable per category;
   immediate demotion on related schema change), blame-back, supersession, version history,
   publication approval (reviewer role required — 5-T9), rule-level citations, retrieval manifests.
   Only **published and effective** rules enter production retrieval; draft knowledge never reaches
   a model context (5-T6).

## 2. What is deleted (KB-01, C-36)

`knowledge_base/business_rules.json` and its loader (`ai/v2/service.py:38` `RULES_PATH` +
`_rules_for`) are deleted in Phase 5. No domain rule set ships in code — no install seed, no
fallback. `synthetic-kb/` stays test-fixture-only, never loaded at startup. A source-inspection
test asserts no module ships a domain rule set (5-T8).

## 3. Two axes, kept apart (KB-15, C-27)

- **User-facing axis: taxonomy dimensions** — the only thing shown at upload.
- **Internal axis: fact kind** (`structure`, `lineage`, `domain_fact`, `ownership`,
  `case_history`) — system-derived, never surfaced in the upload UX. It *is* the agent
  knowledge-access matrix (`kb.py` `AGENT_CATEGORY_MATRIX`) — server-side enforcement of the
  Planner/Reader history quarantine — plus per-category shelf life. Plumbing, not user
  classification.

## 4. Retrieval order (KB-16) — fixed

authorize principal and workspace → filter published/effective/non-archived → match tags and
explicit table/column relationships → apply the agent's allowed fact kinds → enforce history
quarantine → apply trust, expiry and suspicion → optionally rank → **persist the retrieval manifest
with an eligibility reason per rule**. Tags connect records; they never prove relevance or
causality. The cross-field engine consumes exactly this via `kb.list_eligible_rules(...)`
(published + effective + **bound**, filtered by the item's use-case scope).

## 5. Playback summary fields (the KB-03 record shape)

| Field | Content |
|---|---|
| `document`, `version` | provenance reference |
| `rules_total` | count of parsed rule records |
| `by_framework` | e.g. `{IRB: 36, IFRS9: 11, both: 2}` |
| `by_type` | `{conditional, date_ordering, domain, identity, inequality}` counts |
| `by_severity` | `{CRITICAL, MATERIAL, MINOR}` counts |
| `roles_referenced` | distinct semantic roles, as recovered (post-confirmation names) |
| `binding` | `{bound, reference_only, unparsed}` counts |
| `unparsed` | list of `{source_ref, reason}` |
| `hazards` | surfaced extraction hazards needing confirmation |
