# Deployment package builder

From the workspace root, create a versioned VM deployment ZIP with:

```powershell
.\tools\New-DeploymentPackage.ps1 -PackageName DataWorkbench_25AugDepV2
```

By default, the ZIP is written beside the repository (`C:\Src` for this
checkout). Use `-OutputDirectory` to choose another location. Existing ZIPs are
never overwritten.

The builder packages backend and frontend source plus Docker Compose/Nginx
configuration. It excludes secrets, `.env*`, virtual environments,
`node_modules`, tests, reports, local databases, uploads, caches, and generated
runtime artifacts. It also checks the staged contents before creating the ZIP.

For the current RCA runtime and model settings, follow
[RCA deployment requirements](../../source-codes/docs/rca/deployment.md).
The Compose template currently forwards legacy Azure settings; additionally
inject the governed registry's required `AI_PRIMARY_*` values for RCA. Retain
the application data volume, allow Python child processes, and configure the
frontend build with a Node version compatible with the installed Vite.
Package creation alone does not validate live AI connectivity or production sizing.
