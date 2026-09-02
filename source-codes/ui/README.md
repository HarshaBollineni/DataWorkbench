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
