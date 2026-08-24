# Windows embedded Python module path

The repository's Windows virtual environment is based on the embeddable Python
distribution under `.runtime/python312-embed`. Embeddable Python uses
`python312._pth`, which enables isolated path handling: the process working
directory and `PYTHONPATH` are not added to `sys.path` automatically.

`python312._pth` therefore includes `..\..\backend`. This entry is required for
fresh processes such as:

- application workers started with `python -c`;
- subprocess-based restart and persistence checks;
- isolated AI-module import checks; and
- background helpers that import `system_db`, `ai`, or `dq_diagnostics`.

When replacing or rebuilding `.runtime/python312-embed`, preserve the backend
entry in `python312._pth`. Verify the runtime from the repository root with:

```powershell
.venv\Scripts\python.exe -c "import ai, system_db; print(ai.__file__); print(system_db.__file__)"
```

Both paths must resolve beneath `source-codes\backend`.
