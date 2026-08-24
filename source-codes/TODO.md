# Aegis Labs maintenance tracker

**Status:** Current  
**Application version:** 0.5.2

There is no active feature-release checklist in this file. Product changes need
an approved requirements baseline and a bounded implementation plan before they
are added here.

## Open operational follow-up

- [ ] Harden the Azure deployment wrapper's temporary staging and Windows CLI
  log-rendering behavior. This is operational work and does not change product
  features or public APIs.

## Maintenance expectations

- Preserve public routes and the compatibility exports in `backend/routers/v2.py`.
- Keep UI endpoint definitions in `ui/src/api/client.js` and shared transport
  behavior in `ui/src/api/transport.js`.
- Run `./ci-local.ps1` from this directory before release integration.
- Run `../documentation.ps1 check` whenever product behavior, routes,
  dependencies, repository structure, or release documentation changes.

Completed release plans are evidence, not an active backlog:

- [0.5.0 requirements and plan](../documentation/releases/0.5.0/)
- [0.4.0 release records](../documentation/releases/0.4.0/)
