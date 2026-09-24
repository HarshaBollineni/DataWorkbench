# DataWorkbench UI

React and Vite frontend for DataWorkbench.

## Source layout

- `src/App.jsx` owns authenticated route composition and lazy loading.
- `src/api/client.js` is the public catalog of backend endpoint functions. Keep
  its existing exports stable for callers.
- `src/api/transport.js` owns the API base URL, persisted session token,
  authorization headers, JSON requests, and error-body parsing.
- `src/components` contains reusable application components; generated UI
  primitives remain under `src/components/ui`.
- `src/lib` contains framework-independent display and workflow helpers.
- `src/pages` contains route-level screens and temporary compatibility exports.
- `src/features` contains domain-owned workflow code. Test Lab diagnostics use
  `src/features/test-lab/diagnostics/<test-diagnostic-name>`; T2-D06 Row
  Completeness, T1-D02 Feature Target Separation, T2-D04 Cross-field Business
  Rule, and T4-D14 Population Stability Index are migrated features.
- `src/features/test-lab/shared` contains cross-diagnostic presentation such as
  binning evidence consumed by T1-D02, T4-D14, and RCA.
- `src/features/rca` owns the RCA case journey and evidence components.
- `src/features/aar` owns the Analysis Artifact Repository route implementation
  and its catalogue, lineage, retained-run, and saved-schema views.

When adding an endpoint, put its path and response-specific behavior in
`client.js`. Extend `transport.js` only when the behavior applies across API
domains. Keep route-level pages focused on composition; move reusable state and
behavior into its domain feature package, a shared feature component, or
`src/lib`.

## Development

The production backend URL is supplied through `VITE_API_BASE`; local
development defaults to `http://localhost:8001/api`.

```powershell
npm install
npm run dev
```

## Verification

RCA Intake is issue-first across diagnostics: retained decision/scope, up to three
supported summary measures, and visible limitations precede the optional context
entry. Data profile, diagnostic detail and technical provenance are collapsed.
`src/features/rca/intakePresentation.js` provides the shared deterministic projection;
it does not infer failure from issue lifecycle status, invent missing thresholds or
use profile statistics as diagnostic results. Missing/legacy evidence has an explicit
fallback. Saved intake context is passed to initial review as unverified user background.
Focused coverage: `node --test tests/rcaIntakePresentation.test.js` and
`npx playwright test e2e/rca.spec.js --grep "intake is issue-first|uses four pages"`.

The existing issue route hosts the four-page RCA case UI in
`src/features/rca/components/RcaCase.jsx`. Its Investigate page includes the
server-controlled data-chat unlock, threaded answers, bounded tables, local
request errors, expandable evidence/method and artifact downloads. `askRcaDataChat`
in `src/api/client.js` posts a question and refreshes the retained case bundle.
Reload continuation comes from the backend; it does not depend on local chat state.
Investigation results lead with the retained assessment and reasoning, status icons,
and diagnostic-independent metric cards supplied by the versioned backend
presentation contract (including comparisons when both retained values are valid).
Supporting evidence, method and earlier investigations are collapsed by default;
the latest executed investigation stays open. Panels share a compact full-width
layout. Raw metrics, identifiers and implementation details are available
through the on-demand audit record, not repeated in the normal result view.
The Investigation method retains a separate, collapsed-by-default generated /
executed code viewer. Older executions without retained source say so explicitly.
The compact header keeps identifiers, ownership and reset under Case details.
After closure it offers RCA PDF/text exports with local loading/error feedback,
separate from the dataset report. The assessment wording does not equate a
supported hypothesis with proof of causation.

`src/pages/sourcing/StagedStructureReview.jsx` and `stagedStructureReview.js`
own pre-finalization structure selections and validation. The governed
`DatasetStructureReview.jsx` remains the confirmation surface after materialization.

The investigation record groups tests under collapsible, numbered hypotheses with
visible, copyable IDs. Discovery, confirmation and follow-up tests share their
hypothesis reference (for example, 2.1–2.3); opening/unlinked evidence stays separate.
Add context on the active hypothesis before a follow-up. Saving retains the context
in the AAR and supersedes an unexecuted plan, without changing completed evidence.
After a run, **Continue exploring** plans and executes one follow-up under
the same ID. **Explore another explanation** proposes a distinct hypothesis with
its own ID and plans/executes its test. Neither requires a second run click.
Both buttons are disabled after two started hypothesis runs in the current RCA
generation, with a local explanation. Discovery plus confirmation counts once;
failed runs count, but cancelled/unrun plans do not. Existing evidence and pending
hypotheses remain visible. Chat still requires two successful runs; closure remains
available. Focused browser coverage: `e2e/rca-run-limit.spec.js`.
If no distinct testable explanation is available, a local error explains the limit.
One hypothesis card owns the statement, latest finding and context. Tests, evidence
and generated/executed code remain expandable; background evidence is collapsed below.
Numbering is retained across reloads within the current RCA generation.

Focused rendered RCA coverage is in `e2e/rca.spec.js`; run the chat scenario with
`npx playwright test e2e/rca.spec.js --grep "RCA data chat"`. The scenario mocks
API responses and does not validate a live model deployment.

Unit tests mirror their feature paths under `tests/unit`; `npm run test:unit`
discovers both nested domain tests and remaining flat compatibility tests.

```powershell
npm run lint
npm run test:unit
npm run build
npm run check:reachability
npm run test:e2e
```

The React Compiler is not enabled. The complete application and documentation
gates are described in [`../README.md`](../README.md).

RCA issue entry loads the workspace module, issue details and tags concurrently.
Initial requests are reused only within their mounted component during React
StrictMode effect replay; there is no global response cache. Navigation discards
stale responses and reloads perform fresh reads. Verify this behavior with
`npx playwright test e2e/rca-loading.spec.js e2e/rca-progress.spec.js`.

Initial Review labels proposals as Candidate A, B, C. Investigation display numbers
start at Hypothesis 1 in retained-plan order, independently of candidate creation
order; confirmation/follow-up tests stay within that hypothesis (1.1, 1.2, …).
Permanent IDs and chat eligibility are unchanged. Existing cases are renumbered
for display on reload without rewriting evidence. The focused regression is
`npx playwright test e2e/rca-numbering.spec.js`.

RCA progress is local to the initiating controls: investigation actions, candidate
selection, chat composer, conclusion controls and report downloads. Initial-review
startup displays progress in its destination content area. There is no duplicate
page-top banner or automatic scroll. Report downloads use a local elapsed timer
without polling analysis progress. Placement and cleanup are covered by
`npx playwright test e2e/rca-progress.spec.js`.
