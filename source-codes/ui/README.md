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
- `src/pages` contains route-level screens. Feature-specific components should
  be grouped below a matching page subdirectory, such as `src/pages/testlab`.

When adding an endpoint, put its path and response-specific behavior in
`client.js`. Extend `transport.js` only when the behavior applies across API
domains. Keep route-level pages focused on composition; move reusable state and
behavior into feature hooks, components, or `src/lib`.

## Development

The production backend URL is supplied through `VITE_API_BASE`; local
development defaults to `http://localhost:8001/api`.

```powershell
npm install
npm run dev
```

## Verification

```powershell
npm run lint
npm run test:unit
npm run build
npm run check:reachability
npm run test:e2e
```

The React Compiler is not enabled. The complete application and documentation
gates are described in [`../README.md`](../README.md).
