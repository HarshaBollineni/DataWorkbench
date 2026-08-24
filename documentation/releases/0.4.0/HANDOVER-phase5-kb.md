# Handover: Phase 5 (Knowledge Base) — Archimedes 0.4.0

Written for continuity as this session hands off to Codex. `prompt.txt` (referenced
earlier) exists at this path but is empty — no additional instructions were found
there. Everything needed to pick this work up is below.

## Status: COMPLETE

Phase 5 (KB-01..KB-16 — upload, tag, table-aware parse, playback, bind) is fully
implemented, tested, and validated. No known open items within Phase 5's own scope.
The only intentionally-deferred piece is the `bound` binding-status transition,
which requires Phase 6's cross-field primitives (see "What Phase 6 needs" below).

Authority doc: `source-codes/docs/0.4.0/04-kb-contract.md` (already accurate — see
"S9 parity verification" below; no edits were needed).

## Files changed

- `backend/kb_convert.py` — table-aware PDF conversion. `_render_pdf_page()` uses
  `page.find_tables()` + `page.crop()` to interleave prose and tables in reading
  order, rendering tables as GFM pipe syntax. `_clean_cell()` collapses wrapped
  cell text (hyphen-continued line breaks join without a space, e.g.
  `"IFRS9-\n01"` → `"IFRS9-01"`; other breaks become a space). A
  `<!-- pdf:page=N -->` marker precedes each page's rendered content so
  `kb.py` can recover source-page traceability straight from the stored
  Markdown — no separate index. DOCX table rendering upgraded to the same GFM
  helper. Existing density-check/rejection invariants (scanned-PDF, oversized,
  corrupt) untouched.
- `backend/kb.py` — `_split_sections_table_aware()` (new) wraps the unchanged
  `_split_sections()`: each H2 section's body is scanned for GFM tables whose
  header fuzzy-matches enough of the generic id/severity/type/entity/role/
  rule/reference vocabulary (`_HEADER_FIELD_PATTERNS`, ordered regex, no
  domain hardcoding). A recognized table explodes into one rule per data row
  instead of one rule per section. A `"<label> (N rules)"` heading convention
  (`_GROUP_HEADING_RE`) sets `framework` via a decrementing budget that
  survives a page break. `_detect_role_hazards()` flags a 15+ char,
  no-separator role token (KB-11). `"ID — note"` lines
  (`_NOTE_LINE_RE`) attach to `encoded_exceptions_json`. New:
  `parse_report()`, `playback_summary()`, `list_eligible_rules(...,
  require_bound=False)`. `submit_for_review()` populates the new KB-07
  fields; `publish_rule`/`archive_rule` are unmodified (verified their
  `s.update()` calls don't clobber the new columns).
- `backend/system_db.py` — additive-only. `kb_rules` gains: `source_rule_id,
  severity, rule_type, entity, semantic_roles_json, regulatory_ref,
  encoded_exceptions_json, source_page, binding_status DEFAULT 'unparsed',
  binding_primitive, binding_params_json, parse_hazards_json, framework`.
  New JSON cols registered, one new index
  (`ix_kb_rules_binding`). Follows the existing idempotent-ALTER pattern.
- `backend/routers/v3.py` — `GET/POST/DELETE /knowledge/documents/{id}/tags`
  (object_type="kb_document", reuses `taxonomy.py` unchanged), `GET
  /knowledge/versions/{id}/playback`, `GET /knowledge/versions/{id}/parse-report`,
  `binding_status` filter on `list_kb_rules`, `require_bound` query param on
  `retrieve_kb_rules`.
- `ui/src/pages/KnowledgeBase.jsx` — `KbDocumentTagPicker` (self-contained;
  deliberately does NOT touch `components/TagPicker.jsx`, which is hardwired
  to `object_type="dq_item"` for the other agent's `DataSourcing.jsx`),
  `PlaybackSummary`, `ParseReportPanel`, `BindingBadge` (shown in both the
  post-submit extracted-rules list and the Rules tab).
- `ui/src/api/client.js` — additive wrappers only:
  `getKbDocumentTagsV3/setKbDocumentTagsV3/removeKbDocumentTagV3`,
  `getKbPlaybackV3`, `getKbParseReportV3`, `getKbRulesV3` extended with
  `bindingStatus`.
- `ui/e2e/knowledge-base.spec.js` — added a second test (S9 upload → tag →
  playback → parse report → publish). Original prose test is untouched
  (5-T2's regression guard).
- New: `backend/tests/test_kb_parse.py` (14 tests), `test_kb_binding.py` (15
  tests), `test_kb_tags.py` (8 tests) — 37 total, all passing.
- New fixture: `ui/e2e/fixtures/KB_cross_field_reference_2.pdf` (copy of
  `synthetic-kb/KB_cross_field_reference_2.pdf`, "S9").

## S9 parity verification (5-T1)

`test_kb_parse.py::S9ParityTests` builds an INDEPENDENT oracle (a second,
from-scratch `pdfplumber` read, not importing anything from `kb.py`) and
compares it against both `kb.py`'s actual output and the contract doc's
published numbers. All three agree exactly:

- **49 rules total**
- By framework: **IRB 36 / IFRS9 11 / both 2**
- By type: **conditional 13 / date_ordering 3 / domain 26 / identity 3 /
  inequality 4**
- By severity: **CRITICAL 6 / MATERIAL 21 / MINOR 22**

The "both" framework label comes from the source PDF's own `"IRB+IFRS9 (2
rules)"` heading (page 3) — normalized generically (any `+`/`&`/`" and "` in
a group-heading label maps to `"both"`), not hardcoded to these two names.

## Binding-status policy (KB-08/09)

Every Phase 5 code path defaults to conservative, non-executable states:

- `rule_type` recovered from a table header → `binding_status = 'reference-only'`
  (parsed, displayable, no implementation).
- No table (prose/H2 section, or a table that failed the recognition bar) →
  `binding_status = 'unparsed'`.
- `'bound'` is **never** assigned anywhere in Phase 5 code
  (`test_kb_binding.py::test_no_code_path_in_kb_module_ever_assigns_bound`
  greps `kb.py`'s own source to prove this). It requires Phase 6's primitive
  signature match or an explicit human confirmation against a real primitive
  — neither exists yet.
- `kb.list_eligible_rules(..., require_bound=True)` — new, backward-compatible
  (defaults `False`) — is the hook Phase 6's cross-field engine should call;
  it excludes everything except `binding_status='bound'`.

## What Phase 6 needs from here

Phase 6 (cross-field engine, `docs/0.4.0/05-cross-field-contract.md`) is the
next natural continuation:

1. Register the actual primitive signatures (the 9 cross-field primitives
   mentioned in the plan — inequality, date_ordering, domain/bound, identity,
   conditional map roughly to `rule_type` values already recovered here).
2. Add the binder: given a `kb_rules` row with `rule_type` +
   `semantic_roles_json` + `rule_text`, match it to a registered primitive
   signature and flip `binding_status` to `'bound'`, populating
   `binding_primitive`/`binding_params_json`. This is additive to `kb.py` —
   no schema change needed, the columns already exist and are nullable.
3. Wire the cross-field engine's rule retrieval through
   `kb.list_eligible_rules(..., require_bound=True)` (already implemented,
   just needs a caller).
4. `parse_hazards_json` (collapsed role names like `exposureatdefault`) should
   block auto-binding for that specific rule until a human confirms the real
   role mapping — the hazard is already surfaced per-rule and in
   `playback_summary()`'s `hazards` list; the binder just needs to check it.

## Validation commands (all passing as of this handover)

```
# from backend/, with ../.venv active
../.venv/Scripts/python.exe -m pytest tests/test_kb_parse.py tests/test_kb_binding.py tests/test_kb_tags.py -q
../.venv/Scripts/python.exe -m pytest tests -q                 # 246 passed, 1 skipped
../.venv/Scripts/python.exe -c "import main"                   # boots clean

# from ui/
npm run build                                                  # succeeds
npx playwright test knowledge-base.spec.js --reporter=list     # 3/3 pass
```

One earlier Playwright run hit a transient failure — traced to the concurrent
ingestion agent live-editing `ui/src/pages/testlab/VariableInventory.jsx`
mid-run, which broke Vite's shared dev-server HMR bundle for an instant. An
immediate clean re-run passed. Not a bug in this work; just a reminder that
this UI dev server is shared across whichever agents are running e2e tests
concurrently — re-run once if a KB e2e test fails with an unrelated Vite/HMR
error in the webServer log.

## Boundaries respected (do not re-touch without checking with the orchestrator)

Per the original brief, these were explicitly out of scope and were not
modified by this work: `backend/ai/v2/service.py` (beyond the orchestrator's
own pre-existing KB-01 cleanup), `backend/routers/v2.py`, `backend/ingest/`,
`ui/src/pages/DataSourcing.jsx`, `ui/src/components/TagPicker.jsx`,
`ui/e2e/upload-workflow.spec.js`, `ui/e2e/taxonomy-tags.spec.js`,
`ui/e2e/admin-reset.spec.js`, `ui/e2e/rca.spec.js` — these belong to the
concurrent data-ingestion-redesign agent's territory.

## Deviations from spec

None. Independent extraction confirmed the contract doc's S9 numbers were
already accurate, so no doc edit was required (the plan's "trust your own
extraction, fix the doc if it disagrees" clause didn't trigger — there was no
disagreement).
