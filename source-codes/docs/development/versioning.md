# Archimedes versioning

**Status:** Current. Run release commands from the `source-codes/` directory.

Archimedes uses Semantic Versioning: `MAJOR.MINOR.PATCH`.
The root [`VERSION`](../../VERSION) file is the release source of truth. The backend API,
frontend package metadata, Docker image, and immutable Git release tag must
all use the same version.

## When to change each number

- **MAJOR** (`1.0.0` -> `2.0.0`): a deliberately incompatible public API,
  data model, or user-workflow change.
- **MINOR** (`0.1.0` -> `0.2.0`): a backward-compatible feature release.
- **PATCH** (`0.1.0` -> `0.1.1`): a backward-compatible bug fix, security
  fix, or operational correction.

Until `1.0.0`, Archimedes is in its foundation phase: use `0.MINOR.0` for a
meaningful feature milestone and `0.MINOR.PATCH` for fixes to that milestone.
Use pre-release suffixes only for test candidates, for example
`0.2.0-rc.1`; never reuse a released version.

## Release workflow

1. Work and commit on `dev`; `main` receives only a tested merge from `dev`.
2. Choose the next version and run `./Set-AppVersion.ps1 -Version 0.2.0`.
   This updates `VERSION` and the UI package metadata together.
3. Run the relevant tests and commit the version change with the feature work.
4. Merge `dev` into `main`. When the separate Archimedes Azure resources have
   been configured, run
   `./deploy.ps1 -Tag 0.2.0`. It verifies that `-Tag` matches `VERSION`
   and creates the Git tag `archimedes-v0.2.0` only after a successful deploy.

The deployment configuration belongs in `deployment.local.psd1`, which is
intentionally ignored by Git. Start from `deployment.example.psd1` and fill
in the new Archimedes resource names and URLs. It must never contain the
Galileo resources or URL.
