# Archimedes Git and release guide

**Status:** Current. Run the commands below from the `source-codes/` directory.

Archimedes has two permanent branches:

| Branch | Plain-English meaning |
|---|---|
| `main` | The approved production line. Do not make normal commits here. |
| `dev` | Your everyday workbench for the next version. |

A version is a label on a release, not a permanent branch. [`VERSION`](../../VERSION) says what
you are building; a tag such as `archimedes-v0.2.0` permanently identifies the
exact deployed code.

## Daily work

```powershell
git switch dev
# make changes
git status
git add -A
git commit -m "describe the completed change"
```

## Release a version

```powershell
git switch dev
.\ci-local.ps1
git switch main
git merge dev
.\deploy.ps1 -Tag 0.2.0
git push origin main dev --tags
git switch dev
git merge main
```

`deploy.ps1` checks that `VERSION` matches the requested version and creates
the release tag only after the Azure deployment succeeds. Never reuse a tag.

## Safety net

This repository uses a local Git hook that rejects direct commits on `main`.
Merging `dev` into `main` is the normal, allowed promotion. To make the hook
active on a new machine, run:

```powershell
git config core.hooksPath .githooks
```

For an urgent production fix, create a temporary `hotfix/<description>` branch
from `main`, test it, merge it into both `main` and `dev`, then release the next
patch version.
