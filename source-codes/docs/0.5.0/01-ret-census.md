# 0.5.0 Step 0/1 — RET census: machine-readable evidence

**Purpose.** Requirements §7 (RET) and plan §7 measure the API-client and
orphaned-component dead weight at fixed numbers (153 exports / 74 unused / 65
route-absent / 9 route-live / 14 orphaned files). Per plan §6 todo 0.5, this
document re-derives every one of those numbers **from the live tree**, not
from the plan's prose, using two purpose-built tools run against this
worktree on 3 Aug 2026:

1. `ui/scripts/check-reachability.mjs` (the RET-04 gate, built in Step 1) — a
   real import-graph walk from `ui/index.html` → `ui/src/main.jsx` plus every
   `ui/e2e/**` spec, resolving `@/` and vite's extension/index rules,
   producing (i) every file under `ui/src/**` unreachable from any root and
   (ii) every named export of `ui/src/api/client.js` and `ui/src/lib/**`
   imported by nothing reachable.
2. A one-off route-liveness cross-check: `backend/main.py` was booted
   (`uvicorn main:app`, throwaway `SYSTEM_DB_PATH`) and `/openapi.json` was
   fetched, giving the **79 live path templates** from the four mounted
   routers (`auth`, `admin`, `v2`, `v3`) — the same 79 the plan's baseline
   states. Every dead export's route literal (or template) was parsed out of
   `client.js`'s AST and matched against that live set (path params
   normalised to a wildcard).

**Result: the plan's numbers are close but not exact.** `client.js` today
carries **154** exports (not 153), of which **77** have no caller anywhere in
`ui/src` (not 74). Of those 77, **66** target a route absent from the live
surface (not 65) and **11** either target a route that *is* live, or are not
a route at all (not 9) — see §3. Separately, the reachability walk finds
**27** unreferenced files under `ui/src/**` (not 14) — the plan's 14 are a
subset; the other 13 are additional orphans of the same retired-wizard
lineage, confirmed transitively dead. Every number below is stated with the
command/tool that produced it so it can be re-derived, not re-typed.

---

## 1. Method notes

- Reachability is computed **forward from roots** (`main.jsx`, every
  `ui/e2e/**/*.js` spec, `vite.config.js`), not backward ("who imports X"). A
  consequence: a file/export whose only importer is *itself* unreachable is
  correctly reported dead in the **same pass** — there is no need to delete
  the importer first and re-run to discover it. This is why the walker finds
  the `patchRelations → useErdModel.js → ErdPanel.jsx` chain (and one more,
  `getAppConfig → lib/appConfig.js`, the plan did not name) in one run. The
  two-pass sequencing in plan §7.2 (delete → re-walk → decide) is still
  followed literally in the Step-1 commit history for auditability and to
  physically remove the code, but the walker itself does not need multiple
  passes to *find* the transitive set — see §5.
- "Call sites" below are raw import edges (`grep`-equivalent, via the same
  AST parse) **regardless of whether the importer itself is reachable** —
  this is what surfaces the transitive chains in §5. "Dead" (used by the
  RET-01/02 classification) means *zero reachable importer*, which is the
  stricter, correct condition and is what `check-reachability.mjs` enforces.
- Route liveness: `client.js`'s `req(path)` calls pass a path **relative to
  `API_BASE`**, which already includes `/api`. Live paths below have that
  prefix stripped for the comparison. A route counts LIVE if the export's
  path template (params wildcarded) matches one of the 79 live templates.

---

## 2. (a) Every `client.js` export — call sites and route

154 rows. "Call sites" lists every `ui/src` file with a raw import edge for
that name (empty = none found anywhere). "Route" is the path literal/template
parsed from the export's body (blank = not a route — a local helper or a
plain re-exported constant). "Live" cross-checks that route, after stripping
`/api` and wildcarding `{param}` segments, against the 79-path dump of
`GET /openapi.json` from a throwaway-DB boot of this tree's `main.py`.

| # | Export | Call sites (ui/src) | Route | Live? |
|---|---|---|---|---|
| 1 | `getToken` | context/AuthContext.jsx | — | n/a (local helper) |
| 2 | `setToken` | *(none — only used inside client.js by `login`/`logout`)* | — | n/a (local helper) |
| 3 | `getAppConfig` | lib/appConfig.js **(unreachable file, §4)** | `/config` | LIVE |
| 4 | `login` | context/AuthContext.jsx | `/login` | LIVE |
| 5 | `logout` | context/AuthContext.jsx | `/logout` | LIVE |
| 6 | `me` | context/AuthContext.jsx | `/me` | LIVE |
| 7 | `updateProfile` | pages/Profile.jsx, theme/useTheme.js | `/me` | LIVE |
| 8 | `listUsers` | pages/Admin.jsx | `/admin/users` | LIVE |
| 9 | `createUser` | pages/Admin.jsx | `/admin/users` | LIVE |
| 10 | `deleteUser` | pages/Admin.jsx | `/admin/users/{username}` | LIVE |
| 11 | `factoryReset` | pages/Admin.jsx | `/admin/factory-reset` | LIVE |
| 12 | `getInventory` | *(none)* | `/inventory` | ABSENT |
| 13 | `getInventoryTables` | *(none)* | `/inventory/tables` | ABSENT |
| 14 | `getAvailableDbs` | *(none)* | `/ingestion/available` | ABSENT |
| 15 | `getDataSourcingCatalog` | *(none)* | `/ingestion/catalog` | ABSENT |
| 16 | `getDbSchema` | *(none)* | `/ingestion/databases/{db}/schema` | ABSENT |
| 17 | `ingestDb` | *(none)* | `/ingestion/ingest` | ABSENT |
| 18 | `describeDb` | *(none)* | `/ingestion/describe` | ABSENT |
| 19 | `saveSummaryText` | *(none)* | `/ingestion/{db}/summary-text` | ABSENT |
| 20 | `updateDbMetadata` | *(none)* | `/ingestion/{db}/metadata` | ABSENT |
| 21 | `deleteDatabase` | *(none)* | `/ingestion/{db}` | ABSENT |
| 22 | `getDatabaseState` | *(none)* | `/ingestion/{db}/state` | ABSENT |
| 23 | `generateDbSummary` | *(none)* | `/ingestion/{db}/summary` | ABSENT |
| 24 | `selectIngestTables` | *(none)* | `/ingestion/{db}/select-tables` | ABSENT |
| 25 | `recordCounts` | *(none)* | `/ingestion/{db}/record-counts` | ABSENT |
| 26 | `tablePreview` | *(none)* | `/tables/{t}/preview` | ABSENT |
| 27 | `summaryStreamUrl` | *(none)* | `/ingestion/{db}/summary/stream` | ABSENT |
| 28 | `dictionaryStreamUrl` | *(none)* | `/datasource/{db}/dictionary/generate/stream` | ABSENT |
| 29 | `relationSuggestionsStreamUrl` | *(none)* | `/datasource/{db}/relation-suggestions/stream` | ABSENT |
| 30 | `getSources` | *(none)* | `/datasource/sources` | ABSENT |
| 31 | `browseFolder` | *(none)* | `/datasource/browse` | ABSENT |
| 32 | `uploadSource` | *(none)* | `/datasource/upload` | ABSENT |
| 33 | `getRelationTemplate` | *(none)* | `/datasource/{db}/relation-template` | ABSENT |
| 34 | `patchRelations` | components/Erd/useErdModel.js **(unreachable file, §4/§5)** | `/ingestion/{db}/relations` | ABSENT |
| 35 | `getProfile` | *(none)* | `/ingestion/{db}/profile` | ABSENT |
| 36 | `relationSuggestions` | *(none)* | `/datasource/{db}/relation-suggestions` | ABSENT |
| 37 | `validateRelations` | *(none)* | `/datasource/{db}/relations/validate` | ABSENT |
| 38 | `generateDictionary` | *(none)* | `/datasource/{db}/dictionary/generate` | ABSENT |
| 39 | `downloadDbReport` | *(none)* | `/datasource/report` | ABSENT |
| 40 | `parseDictionary` | *(none)* | `/datasource/dictionary/parse` | ABSENT |
| 41 | `rcaEligible` | *(none)* | `/rca/eligible` | ABSENT |
| 42 | `rcaStartTable` | *(none)* | `/rca/start-table` | ABSENT |
| 43 | `rcaCheck` | *(none)* | `/rca/{id}/check` | ABSENT |
| 44 | `rcaSteps` | *(none)* | `/rca/{id}/steps` | ABSENT |
| 45 | `rcaSandbox` | *(none)* | `/rca/sandbox` | ABSENT |
| 46 | `rcaRaiseTicket` | *(none)* | `/rca/raise-ticket` | ABSENT |
| 47 | `rcaIncidents` | *(none)* | `/rca/incidents` | ABSENT |
| 48 | `rcaCases` | *(none)* | `/rca/cases` | ABSENT |
| 49 | `rcaCreateCase` | *(none)* | `/rca/cases` | ABSENT |
| 50 | `rcaGetCase` | *(none)* | `/rca/cases/{id}` | ABSENT |
| 51 | `rcaAddFailure` | *(none)* | `/rca/cases/{id}/failures` | ABSENT |
| 52 | `rcaCaseFeedback` | *(none)* | `/rca/cases/{id}/feedback` | ABSENT |
| 53 | `rcaCaseDossier` | *(none)* | `/rca/cases/{id}/dossier` | ABSENT |
| 54 | `rcaCloseCase` | *(none)* | `/rca/cases/{id}/close` | ABSENT |
| 55 | `rcaUpdateRemediation` | *(none)* | `/rca/remediation/{id}` | ABSENT |
| 56 | `getTickets` | *(none)* | `/tickets` | ABSENT |
| 57 | `getTicket` | *(none)* | `/tickets/{no}` | ABSENT |
| 58 | `updateTicket` | *(none)* | `/tickets/{no}` | ABSENT |
| 59 | `getSkills` | *(none)* | `/skills` | ABSENT |
| 60 | `getArchitectureMap` | *(none)* | `/skills/architecture/map` | ABSENT |
| 61 | `getSkill` | *(none)* | `/skills/{key}` | ABSENT |
| 62 | `saveSkill` | *(none)* | `/skills/{key}` | ABSENT |
| 63 | `getUseCases` | *(none)* | `/use-cases` | ABSENT |
| 64 | `getContract` | *(none)* | `/use-cases/{uc}` | ABSENT |
| 65 | `getPlantedIssues` | *(none)* | `/use-cases/{uc}/planted-issues` | ABSENT |
| 66 | `getTablePreview` | *(none)* | `/tables/{t}/preview` | ABSENT |
| 67 | `runValidations` | *(none)* | `/use-cases/{uc}/run-validations` | ABSENT |
| 68 | `generateRules` | *(none)* | `/use-cases/{uc}/generate-rules` | ABSENT |
| 69 | `rcaStart` | *(none)* | `/rca/start` | ABSENT |
| 70 | `rcaStatus` | *(none)* | `/rca/{id}/status` | ABSENT |
| 71 | `rcaApprove` | *(none)* | `/rca/{id}/approve-code` | ABSENT |
| 72 | `rcaResult` | *(none)* | `/rca/{id}/result` | ABSENT |
| 73 | `getFrameworkOverview` | *(none)* | `/dq-framework/overview` | ABSENT |
| 74 | `getFrameworkAreas` | *(none)* | `/dq-framework/areas` | ABSENT |
| 75 | `getFrameworkFamilies` | *(none)* | `/dq-framework/families` | ABSENT |
| 76 | `getFrameworkFamily` | *(none)* | `/dq-framework/family/{id}` | ABSENT |
| 77 | `getFrameworkMatrix` | *(none)* | `/dq-framework/matrix` | ABSENT |
| 78 | `createItemV2` | pages/DataSourcing.jsx | `/v2/items` | LIVE |
| 79 | `uploadItemFileV2` | *(none — `...WithProgress` variant is used instead)* | `/v2/items/{id}/files` | LIVE |
| 80 | `uploadItemFileV2WithProgress` | pages/DataSourcing.jsx | `/v2/items/{id}/files` | LIVE |
| 81 | `finalizeItemV2` | pages/DataSourcing.jsx | `/v2/items/{id}/finalize` | LIVE |
| 82 | `patchItemV2` | *(none)* | `/v2/items/{id}` | LIVE |
| 83 | `getIngestV2` | pages/DataSourcing.jsx | `/v2/items/{id}/ingest` | LIVE |
| 84 | `reuploadItemV2` | pages/DataSourcing.jsx | `/v2/items/{id}/reupload` | LIVE |
| 85 | `getItemsV2` | pages/DataSourcing.jsx, pages/Inventory.jsx, pages/IssueManagement.jsx, pages/TestLab.jsx | `/v2/items` | LIVE |
| 86 | `getItemTablesV2` | pages/testlab/VariableInventory.jsx | `/v2/items/{id}/tables` | LIVE |
| 87 | `getItemColumnsV2` | pages/DataSourcing.jsx | `/v2/items/{id}/columns` | LIVE |
| 88 | `profileStreamUrlV2` | pages/DataSourcing.jsx | `/v2/items/{id}/profile/stream` | LIVE |
| 89 | `getInventoryV2` | pages/testlab/VariableInventory.jsx | `/v2/items/{id}/inventory` | LIVE |
| 90 | `putInventoryV2` | pages/testlab/VariableInventory.jsx | `/v2/items/{id}/inventory` | LIVE |
| 91 | `getResultsV2` | pages/IssueManagement.jsx | `/v2/items/{id}/results` | LIVE |
| 92 | `getItemIssuesV2` | pages/IssueManagement.jsx | `/v2/items/{id}/issues` | LIVE |
| 93 | `getIssueRegisterV2` | pages/IssueManagement.jsx | `/v2/issues/register` | LIVE |
| 94 | `getIssueV2` | pages/IssueRca.jsx | `/v2/issues/{id}` | LIVE |
| 95 | `rcaStreamUrlV2` | pages/IssueRca.jsx | `/v2/issues/{id}/rca/stream` | LIVE |
| 96 | `createIssueAnalysesV2` | *(none)* | `/v2/issues/{id}/analysis` | LIVE |
| 97 | `closeIssueV2` | pages/IssueRca.jsx | `/v2/issues/{id}/close` | LIVE |
| 98 | `raiseIssueV2` | pages/IssueRca.jsx | `/v2/issues/{id}/raise` | LIVE |
| 99 | `interpretIssueAnalysesV2` | *(none)* | `/v2/issues/{id}/analysis/interpret` | LIVE |
| 100 | `patchTrackedIssueV2` | pages/IssueRca.jsx | `/v2/issues/tracked/{id}` | LIVE |
| 101 | `downloadReportV2` | pages/IssueManagement.jsx, pages/IssueRca.jsx | `/v2/items/{id}/report` | LIVE |
| 102 | `getFrameworkOverviewV2` | pages/DQFramework.jsx | `/v2/framework/overview` | LIVE |
| 103 | `getFrameworkAreasV2` | pages/DQFramework.jsx | `/v2/framework/areas` | LIVE |
| 104 | `getFrameworkTestsV2` | *(none)* | `/v2/framework/tests` | LIVE |
| 105 | `getFrameworkMatrixV2` | pages/DQFramework.jsx | `/v2/framework/matrix` | LIVE |
| 106 | `getAgentsV2` | *(none)* | `/v2/agents` | LIVE |
| 107 | `getDiagnosticsBoardV2` | pages/TestLab.jsx | `/v2/items/{id}/diagnostics/board` | LIVE |
| 108 | `buildDiagnosticManifestV2` | pages/TestLab.jsx | `/v2/items/{id}/diagnostics/manifest` | LIVE |
| 109 | `getDiagnosticManifestV2` | pages/testlab/ScopeGate.jsx | `/v2/diagnostics/manifests/{id}` | LIVE |
| 110 | `patchDiagnosticManifestV2` | pages/testlab/ScopeGate.jsx | `/v2/diagnostics/manifests/{id}` | LIVE |
| 111 | `runDiagnosticManifestV2` | pages/testlab/ScopeGate.jsx | `/v2/diagnostics/manifests/{id}/run` | LIVE |
| 112 | `diagnosticRunStreamUrlV2` | pages/testlab/RunConsole.jsx | `/v2/diagnostics/runs/{id}/stream` | LIVE |
| 113 | `getDiagnosticResultsV2` | pages/TestLab.jsx | `/v2/items/{id}/diagnostics/results` | LIVE |
| 114 | `dispositionFindingV2` | pages/TestLab.jsx | `/v2/diagnostics/findings/{id}/disposition` | LIVE |
| 115 | `getDiagnosticsCoverageSummaryV2` | pages/testlab/ScorePanel.jsx | `/v2/items/{id}/diagnostics/coverage-summary` | LIVE |
| 116 | `diagnosticReportUrlV2` | pages/testlab/ScorePanel.jsx | `/v2/diagnostics/runs/{id}/report` | LIVE |
| 117 | `downloadDiagnosticReportV2` | pages/testlab/ScorePanel.jsx | (uses `diagnosticReportUrlV2` internally) | LIVE |
| 118 | `getTaxonomyDimensionsV3` | components/TagPicker.jsx, pages/KnowledgeBase.jsx | `/v3/taxonomy/dimensions` | LIVE |
| 119 | `getItemTagsV3` | components/TagPicker.jsx | `/v3/items/{id}/tags` | LIVE |
| 120 | `setItemTagsV3` | components/TagPicker.jsx | `/v3/items/{id}/tags` | LIVE |
| 121 | `removeItemTagV3` | components/TagPicker.jsx | `/v3/items/{id}/tags/{aid}` | LIVE |
| 122 | `getIssueTagsV3` | pages/IssueRca.jsx | `/v3/issues/{id}/tags` | LIVE |
| 123 | `getResultTagsV3` | *(none)* | `/v3/results/{id}/tags` | LIVE |
| 124 | `getTestTagsV3` | *(none)* | `/v3/tests/{id}/tags` | LIVE |
| 125 | `getKbDocumentsV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/documents` | LIVE |
| 126 | `getKbDocumentV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/documents/{id}` | LIVE |
| 127 | `uploadKbDocumentV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/documents` | LIVE |
| 128 | `getKbVersionPreviewV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/versions/{id}/preview` | LIVE |
| 129 | `submitKbVersionV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/versions/{id}/submit-for-review` | LIVE |
| 130 | `getKbRulesV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/rules` | LIVE |
| 131 | `publishKbRuleV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/rules/{id}/publish` | LIVE |
| 132 | `archiveKbRuleV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/rules/{id}/archive` | LIVE |
| 133 | `getKbDocumentTagsV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/documents/{id}/tags` | LIVE |
| 134 | `setKbDocumentTagsV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/documents/{id}/tags` | LIVE |
| 135 | `removeKbDocumentTagV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/documents/{id}/tags/{aid}` | LIVE |
| 136 | `getKbPlaybackV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/versions/{id}/playback` | LIVE |
| 137 | `getKbParseReportV3` | pages/KnowledgeBase.jsx | `/v3/knowledge/versions/{id}/parse-report` | LIVE |
| 138 | `createRcaCase` | components/RcaCase.jsx | `/v3/issues/{id}/rca/case` | LIVE |
| 139 | `getRcaCase` | components/RcaCase.jsx | `/v3/rca/cases/{id}` | LIVE |
| 140 | `runRcaOpeningLook` | components/RcaCase.jsx | `/v3/rca/cases/{id}/opening-look` | LIVE |
| 141 | `runRcaPlannerLook` | components/RcaCase.jsx | `/v3/rca/cases/{id}/planner-look` | LIVE |
| 142 | `runRcaLook` | components/RcaCase.jsx | `/v3/rca/looks/{id}/run` | LIVE |
| 143 | `composeRcaHypothesis` | components/RcaCase.jsx | `/v3/rca/cases/{id}/compose` | LIVE |
| 144 | `runRcaConfirmationCheck` | components/RcaCase.jsx | `/v3/rca/checks/{id}/run` | LIVE |
| 145 | `proposeRcaFix` | components/RcaCase.jsx | `/v3/rca/hypotheses/{id}/propose-fix` | LIVE |
| 146 | `approveRcaFix` | components/RcaCase.jsx | `/v3/rca/fix-proposals/{id}/approve` | LIVE |
| 147 | `confirmRcaFixApplied` | components/RcaCase.jsx | `/v3/rca/fix-approvals/{id}/confirm-applied` | LIVE |
| 148 | `closeRcaCase` | components/RcaCase.jsx | `/v3/rca/cases/{id}/close` | LIVE |
| 149 | `runRcaCoveragePass1` | components/RcaCase.jsx | `/v3/rca/cases/{id}/coverage-challenge/pass1` | LIVE |
| 150 | `runRcaCoveragePass2` | components/RcaCase.jsx | `/v3/rca/cases/{id}/coverage-challenge/pass2` | LIVE |
| 151 | `runRcaReopenedKillAttempt` | components/RcaCase.jsx | `/v3/rca/cases/{id}/reopened-kill-attempt` | LIVE |
| 152 | `reviveRcaSuspect` | components/RcaCase.jsx | `/v3/rca/suspects/{id}/revive` | LIVE |
| 153 | `startRcaSecondChance` | components/RcaCase.jsx | `/v3/rca/cases/{id}/second-chance` | LIVE |
| 154 | `API_BASE` | *(none — e2e specs define their own local `API_BASE` constant, not an import)* | — | n/a (re-exported const, not a route) |

Totals: **77 of 154** exports have zero reachable caller. Rows with a
call-site file marked *(unreachable file, §4)* are counted as dead — the
walker does not count an import edge from a file it cannot reach from any
root.

---

## 3. (b)/(c) Classification of the 77 dead exports

**RET-01 set — zero caller, route absent from the live surface: 66.**
Rows 12–77 above minus the 11 in the RET-02-shaped set below. Every one of
these calls into a router that was removed at the 0.4.0 Data-Sourcing/legacy
RCA/Test-Lab-wizard cutover (`/ingestion/*`, `/datasource/*`, `/tables/*`,
`/rca/*` non-v3, `/tickets/*`, `/skills/*`, `/use-cases/*`, `/dq-framework/*`
non-v2) — confirmed absent from the live 79-path dump. **MUST delete per
RET-01.**

**RET-02-shaped set — decided individually, not swept: 11 (plan named 9;
two more surfaced by the wider net cast here — see below).**

| Export | Why it isn't a clean RET-01 | Plan named it? |
|---|---|---|
| `uploadItemFileV2` | route `/v2/items/{id}/files` LIVE; `...WithProgress` sibling is used instead | yes |
| `patchItemV2` | route `/v2/items/{id}` LIVE (PATCH) | yes |
| `createIssueAnalysesV2` | route `/v2/issues/{id}/analysis` LIVE | yes |
| `interpretIssueAnalysesV2` | route `/v2/issues/{id}/analysis/interpret` LIVE | yes |
| `getFrameworkTestsV2` | route `/v2/framework/tests` LIVE | yes |
| `getAgentsV2` | route `/v2/agents` LIVE | yes |
| `getResultTagsV3` | route `/v3/results/{id}/tags` LIVE | yes |
| `getTestTagsV3` | route `/v3/tests/{id}/tags` LIVE | yes |
| `setToken` | not a route — local helper, used internally by `login`/`logout` but never imported elsewhere | yes |
| `getAppConfig` | route `/config` LIVE; only caller is `lib/appConfig.js`, itself one of RET-03's 14 named orphans (§4) — transitively dead, not a clean route-absent case | **no** — new finding |
| `API_BASE` | not a route — a plain re-exported local constant; zero external importer (e2e specs define their own local copy) | **no** — new finding |

Plan §7.1 stated "the remaining 9 unused-but-live exports" (RET-02); this
census finds **11**, because the plan's set was scoped to "route absent vs.
route present" and did not have a category for "not a route at all" (`setToken`,
`API_BASE`) or "route present but the sole caller is itself dead"
(`getAppConfig`). All three get an individual decision in the Step 1 commit
alongside the original 8 — see `docs/0.5.0/00-start-gate.md` and the Step 1
commit message for the final disposition of each.

**`ui/src/lib/agents.js` (in the RET-04 export-check scope, but not named by
RET-02 at all — a genuinely new finding):** `AGENT_DESCRIPTORS` and
`V2_AGENT_NAMES` are exported but have zero external importer; both are used
only internally by `agentLabel` (which *is* imported externally, by
`components/AgentConsole.jsx`). Same shape as `setToken` — local
implementation detail, not a public export. Decided alongside the RET-02
set in Step 1.

---

## 4. (d) Unreferenced files under `ui/src/**`: 27 (plan named 14)

The walker reports every file under `ui/src/**` unreachable from
`main.jsx`/`ui/e2e/**`/`vite.config.js`. The plan's 14 are **confirmed, all
14, still unreferenced** — no import edge, from any file, live or dead,
targets any of them:

| # | File | In plan's 14? | Confirmed zero import edges |
|---|---|---|---|
| 1 | `components/AgentOrgChart.jsx` | yes | yes |
| 2 | `components/AgentTree.jsx` | yes | yes |
| 3 | `components/Erd/ErdPanel.jsx` | yes | yes |
| 4 | `components/ExecutionSwimlane.jsx` | yes | yes |
| 5 | `components/InfoHint.jsx` | yes | yes |
| 6 | `components/LimitedMarkdown.jsx` | yes | yes |
| 7 | `components/MarkdownEditor.jsx` | yes | yes |
| 8 | `components/PythonCodeBlock.jsx` | yes | yes |
| 9 | `components/TableDashboardDialog.jsx` | yes | yes |
| 10 | `components/WizardLayout.jsx` | yes | yes |
| 11 | `components/ui/dropdown-menu.jsx` | yes | yes |
| 12 | `components/ui/separator.jsx` | yes | yes |
| 13 | `components/ui/tabs.jsx` | yes | yes |
| 14 | `lib/appConfig.js` | yes | yes |
| 15 | `components/Erd/ErdConstellation.jsx` | **no** | yes — only importer was `ErdPanel.jsx` (row 3, dead) |
| 16 | `components/Erd/ErdStructured.jsx` | **no** | yes — only importer was `ErdPanel.jsx` (row 3, dead) |
| 17 | `components/Erd/useErdModel.js` | **no** | yes — only importer was `ErdPanel.jsx` (row 3, dead); see §5 |
| 18 | `components/WizardStepper.jsx` | **no** | yes — only importer was `WizardLayout.jsx` (row 10, dead) |
| 19 | `components/ui/progress.jsx` | **no** | yes — only importer was `InfoHint.jsx` (row 5, dead) |
| 20 | `components/ui/tooltip.jsx` | **no** | yes — only importer was `InfoHint.jsx` (row 5, dead) |
| 21 | `data/helpContent.js` | **no** | yes — only importers were `InfoHint.jsx`/`WizardLayout.jsx`/`WizardStepper.jsx` (all dead) |
| 22 | `data/mock.js` | **no** | yes — only importers were `WizardLayout.jsx`/`WizardStepper.jsx` (both dead) |
| 23 | `lib/markdown.js` | **no** | yes — only importer was `MarkdownEditor.jsx` (row 7, dead) |
| 24 | `App.css` | **no** | yes — never imported by any JS (create-vite scaffold leftover; `main.jsx` imports only `index.css`) |
| 25 | `assets/hero.png` | **no** | yes — no `src=`/import reference anywhere |
| 26 | `assets/react.svg` | **no** | yes — create-vite scaffold leftover |
| 27 | `assets/vite.svg` | **no** | yes — create-vite scaffold leftover |

Rows 15–27 are not named by RET-03 or by requirements §7's "14 files"
measurement, but are the same shape (zero import edge, live or dead) and are
within Step 1's Permitted paths ("any file the fixed-point pass proves
dead"). Each is either deleted or kept with a reason in the Step 1 commit —
see `docs/0.5.0/00-start-gate.md` for the disposition table.

None of the 27 has any import edge — "the import edges that would reference
them" (plan wording) is empty for all 27; nothing in the live tree, dead or
alive, still points at any of them except the internal dead-to-dead edges
listed above (which is exactly what makes rows 15–23 *transitively* dead
rather than independently orphaned).

---

## 5. (e) Transitive chains

Two confirmed chains of the shape the plan's R-02 risk names (the plan
predicted the first by inspection; the second is a new finding from the same
mechanism):

1. **`patchRelations` (client.js export) → `useErdModel.js` (its only
   caller) → `Erd/ErdPanel.jsx` (its only caller, one of the 14 named
   orphans) → nothing.** All three are dead. `patchRelations` also fails
   independently on route-liveness alone (ABSENT, §3), so it is RET-01-shaped
   even without the transitive argument; `useErdModel.js` is dead purely
   because its one caller (`ErdPanel.jsx`) is dead. `ErdConstellation.jsx`
   and `ErdStructured.jsx` (the other two files in `components/Erd/`) are
   dead the same way — `ErdPanel.jsx` was their only caller too. The entire
   `components/Erd/` directory is therefore dead, not just the one named
   file.
2. **`getAppConfig` (client.js export) → `lib/appConfig.js` (its only
   caller, one of the 14 named orphans) → nothing.** Not named by the plan.
   `getAppConfig`'s route (`GET /api/config`) is live on the backend, so it
   does not qualify for RET-01 on route-liveness grounds — it is dead purely
   because its sole caller is dead. Decided alongside RET-02 in §3.

Two further same-shape chains found by the same query, inside the retired
wizard's own file set (not client.js exports, but the identical "only
caller is dead" pattern — see §4 rows 18–23):
`WizardStepper.jsx`/`data/mock.js` → only caller `WizardLayout.jsx` (dead);
`ui/progress.jsx`/`ui/tooltip.jsx`/`data/helpContent.js` → only caller
`InfoHint.jsx` (dead); `lib/markdown.js` → only caller `MarkdownEditor.jsx`
(dead).

**Why one walker pass found all of this without needing R-02's prescribed
"delete, re-walk, delete again" loop to *discover* it:** the walk computes
true reachability forward from roots; a file's only importer being
unreachable makes the file unreachable in the same computation, with no
dependency on deletion order. The two-pass discipline in plan §7.2 remains
followed in the Step 1 commit history (delete the named files first, re-run
the gate, confirm zero *new* findings — i.e. a fixed point on the first
re-run) because it is what plan rule 6 ("prove the invariant bites") and
1-A5 ask for as auditable evidence, not because the tool needs it to see the
chain.

---

## 6. Final disposition (Step 1 commit)

Every one of the 77 dead `client.js` exports, the 2 dead `lib/agents.js`
exports, and the 27 dead files now has a recorded, non-ambiguous outcome.
`client.js` ends the step at **83 exports** (154 − 66 RET-01 − 3 RET-02
deletions − 2 un-exports = 83). `lib/agents.js` ends at 1 export
(`agentLabel`; `AGENT_DESCRIPTORS`/`V2_AGENT_NAMES` un-exported).

**Deleted (69 exports total: 66 RET-01 + 3 RET-02-shaped):** every row in
§3's RET-01 table, plus `uploadItemFileV2` (fully superseded by
`uploadItemFileV2WithProgress`, which is the one DataSourcing.jsx actually
calls — two uploaders for one endpoint is pure duplication), `getAgentsV2`
(its only historical consumer, the `AIAgents.jsx` page, was already deleted
at the 0.4.0 helper-layer sweep — docs/0.4.0/08-helper-layer.md's deletion
list — with no successor planned; `AgentConsole.jsx`, the one live
agent-display surface, reads the static `lib/agents.js` descriptors instead
and has no relation to this route), and `getAppConfig` (its only caller,
`lib/appConfig.js`, is deleted below; `GET /api/config` stays live
server-side but nothing in the frontend needs it once `appConfig.js` is
gone).

**Un-exported, not deleted (4):** `setToken` (client.js — used internally by
`login`/`logout`, never imported elsewhere), `API_BASE` (client.js — the
`export { API_BASE };` re-export line removed; the internal `const
API_BASE` stays, used by `req()` and the multipart/stream helpers),
`AGENT_DESCRIPTORS` and `V2_AGENT_NAMES` (lib/agents.js — used only inside
`agentLabel`, which stays exported and is `AgentConsole.jsx`'s actual
caller). Un-exporting means the walker no longer counts them as a public
export at all — the correct outcome for an implementation detail that
should never have been part of the module's public surface.

**Kept with a recorded reason (6 — the RET-02-shaped set, decided last,
against the fixed-point graph per plan §7.2 step 1.5):** `patchItemV2`,
`createIssueAnalysesV2`, `interpretIssueAnalysesV2`, `getFrameworkTestsV2`,
`getResultTagsV3`, `getTestTagsV3` — each is a live backend route with a
concrete, named reason it isn't wired yet (AST-04's rename UI, an
issue-analysis feature, a framework-test drill-down, result/test-level
tagging). The exact reason text lives in `ui/scripts/keep-reasons.json`,
keyed `src/api/client.js#<exportName>`, and is cross-checked by the gate
against this document (§3's classification table names all six).

**Post-census compatibility keeps (6):** later work added or exposed
`dispositionFindingV2`, `getAnalysisArtifactV2`, and
`getAnalysisArtifactTypesV2` without an active UI caller. Their backend
routes remain live, and the organization refactor explicitly preserves public
client APIs, so they are retained with compatibility reasons rather than
silently deleted. The domain-alignment pilot also moved the T2-D06
`RowCompletenessResults`, `RowCompletenessScopeGate`, and
`rowCompletenessWorkflow` implementations under `src/features` while retaining
their former `src/pages/testlab` paths as temporary compatibility exports. The
current keep list therefore contains nine exports and eighteen files; the
original six-entry count below remains the historical 0.5.0 census result.

The exact compatibility file keys are:

- `src/pages/testlab/RowCompletenessResults.jsx`
- `src/pages/testlab/RowCompletenessScopeGate.jsx`
- `src/pages/testlab/rowCompletenessWorkflow.js`

The T1-D02 domain migration adds three equivalent temporary compatibility files:

- `src/pages/testlab/FeatureTargetResults.jsx`
- `src/pages/testlab/FeatureTargetScopeGate.jsx`
- `src/pages/testlab/FeatureTargetEvidence.jsx`

The T4-D14 domain migration adds five equivalent temporary compatibility files:

- `src/pages/testlab/PopulationStabilityResults.jsx`
- `src/pages/testlab/PopulationStabilityScopeGate.jsx`
- `src/pages/testlab/PsiBinningWorkspace.jsx`
- `src/pages/testlab/psiWorkflow.js`
- `src/pages/testlab/binLabelDisplay.js`

The RCA domain migration adds four temporary compatibility files and retains one governed dormant
component until its live backend approval flow is either activated or explicitly retired:

- `src/components/RcaCase.jsx`
- `src/components/RcaSourceEvidence.jsx`
- `src/pages/issue-rca/TrackedIssueEditor.jsx`
- `src/components/rca/FixApprovalFlow.jsx`
- `src/features/rca/components/FixApprovalFlow.jsx`

The AAR domain migration adds two temporary compatibility files:

- `src/pages/artifact-repository/ArtifactRepositoryViews.jsx`
- `src/pages/artifact-repository/constants.js`

**Deleted files (all 27 — none kept):** the 14 named in requirements §7 plus
the 13 additional orphans in §4's table. None had a concrete, adopted
near-term requirement pointing at it (checked against requirements-0.5.0.md
in full — no AI-org-chart, execution-swimlane, wizard, or table-dashboard
requirement exists for 0.5.0), so per operating rule 14 ("do not invent a
missing input") every one is deleted rather than kept on a speculative
future-use reason.

**Fixed-point evidence.** Two deletion passes were needed to reach zero
findings with zero keep-reasons remaining beyond the permanent 6:
- Pass 1 deleted the 66 RET-01 exports and the 14 named RET-03 files.
  Re-running the gate immediately after (with the other 13 files' and 13
  exports' TEMPORARY keep-reasons still in place) passed clean.
- To make the "would pass 2 find something new" question answerable rather
  than assumed, the 13 extra files' TEMPORARY keep-reasons were then
  stripped (without touching the files) and the gate re-run: it reported
  **exactly** the 13 files predicted in §4/§5 — nothing more, nothing less.
  They were then deleted for real.
- A third run, with only the 13 RET-02-shaped export keep-reasons left
  TEMPORARY, passed with zero new file findings (as expected — file
  reachability doesn't depend on export decisions).
- RET-02's 13 exports were then decided (§ above) and the keep-reasons file
  rewritten to its final 6 permanent entries. The gate now passes with **47
  of 47** `ui/src` files reachable and **zero** uncalled exports outside the
  6 recorded reasons.

Independent confirmation outside the walker itself: `npm run build`
produced a byte-for-byte identical main JS bundle before and after this
step's deletions (`545.77 kB`, unchanged) — the deleted code was already
being tree-shaken out of the production build, exactly as expected for code
with zero reachable importer.

## 7. RET-05 — `ai/tool_registry.py` docstring

Verified 3 Aug 2026: `grep -rn "routers.ingestion" backend/` returns exactly
one hit, `ai/tool_registry.py`'s own `_fetch_schema_stats` docstring — there
is no `routers/ingestion.py` file (the live `routers/` directory holds only
`auth.py`, `admin.py`, `v2.py`, `v3.py`, `__init__.py`) and no other module
anywhere in `backend/` imports or calls anything under that name. The
docstring's "Root-cause fix (Phase 2 / PLT-08)" paragraph was rewritten to
read unambiguously as a historical note (it now opens "Historical note
(RET-05, 0.5.0) — before 0.4.0 Phase 2 this read from ... a router and a
warehouse loader that no longer exist") rather than something a reader could
mistake for a current dependency. The note itself — that this function used
to call a since-deleted router and warehouse loader — is preserved, not
deleted, per the plan's explicit instruction.
