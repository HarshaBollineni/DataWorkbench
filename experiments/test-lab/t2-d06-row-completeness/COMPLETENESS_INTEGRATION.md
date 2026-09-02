# Task: Integrate the "completeness" data-quality diagnostic into our pipeline

You are integrating ONE diagnostic — **completeness** (row-completeness reconciliation of a
loan x period panel). This handoff contains only what completeness needs; ignore other diagnostics.

## Files in this folder
- `derivation.py`          — core: role catalog, column profiling, role prediction, derived frame
- `guards.py`              — KB-driven pre-grain guards
- `run_tests.py`           — completeness rule implementations + `CONFIG`
- `bind_cli.py`            — role-binding CLI (produces `binding.json`)
- `completeness_kb_1.json` — the completeness rulebook (**3 rules**: CMG-01, CMG-02, CMU-01)
- `dq.py`                  — single entry-point dispatcher
- `agent.py`               — OPTIONAL AI layer that reviews/overrides the name-based binding (needs a key)
- `agent_config.json`      — AI credentials (key is blanked; add your own only if you use `--agent`)
- `mr_pd_sample.csv`       — sample panel to test against (replace with your data)

## Data shape required
A **panel**: one row per **facility x period**. You must have:
- a **period** column (reporting date / `YYYY-Qn` / `YYYY-MM` / `YYYYMM`) — **REQUIRED**. Without it every
  completeness rule returns NOT-APPLICABLE (the grid grain cannot be built).
- a **facility id** (repeats across periods).
- optional but used: **origination date**, **segment/partition**, **closure marker**.

## The 5 semantic roles completeness binds
`facility_id`, `period`, `origination_date`, `closure_marker`, `segment`.
Each role maps to one of your columns; roles left unbound make dependent rules NOT-APPLICABLE.

## What it checks (this KB = 3 rules)
- **CMG-01** — every facility has one row per expected period; coverage vs `portfolio_coverage_floor` (0.99).
- **CMG-02** — no duplicated facility-period pairs.
- **CMU-01** — per segment x period coverage vs peer median and cell floor. Needs `minimum_cell_facilities`
  in CONFIG, else it returns **NO-VERDICT** (by design - no default is substituted).

Verdicts: `PASS | FAIL | NO-VERDICT | NOT-APPLICABLE | NOT-ASSESSABLE`.

## Option 1 - run via CLI (fastest)
```bash
# 1) bind roles (writes binding.json). --yes auto-accepts predictions; omit for interactive confirm.
python dq.py bind  --folder . --dataset mr_pd_sample.csv --yes --out binding.json

# 2) run completeness
python dq.py test  --binding binding.json --framework IFRS9 --json findings.json
```
Notes:
- `--framework IRB|IFRS9`: a rule runs only if its KB `use_case` includes it.
- `--product` is required by the CLI but does NOT affect completeness (it only touches resolution-rate).
- **Review `binding.json`** before trusting results - confirm each role maps to the right column.

### How columns get matched to roles
- **Default (no key):** `bind_cli.py` predicts each role's column from the column NAME (regex patterns)
  plus value shape (dtype, distinct count, nulls), and shows its reasoning; you confirm or pick another.
  This already "checks column names and binds" - no AI needed.
- **AI-assisted (optional, needs key):** add `--agent` to review/override those predictions with an LLM.
  Put a key in `agent_config.json` (or `KB_AGENT_API_KEY`) first:
  ```bash
  python dq.py bind --folder . --dataset mr_pd_sample.csv --agent --yes --out binding.json
  ```
  With `--agent` the AI proposes corrections for role->column mappings (still human-confirmed unless `--yes`).
  Without a key, `--agent` safely falls back to the name-based prediction.

## Option 2 - call it programmatically (to embed in your process)
```python
import derivation as D, guards as G, run_tests as R

folder = "."
df, name = D.load_dataset(folder, "mr_pd_sample.csv")
kb = D.load_kbs(folder)["completeness"]              # loads completeness_kb_1.json (3 rules)

# roles: from your binding.json, or build the dict directly
roles = {"facility_id": "loan_id", "period": "observation_date",
         "origination_date": None, "closure_marker": None, "segment": "region"}

# (optional) enable CMU-01 and declare run-off convention
R.CONFIG["minimum_cell_facilities"] = 20             # else CMU-01 -> NO-VERDICT
# R.CONFIG["runoff_convention"] = "last_row"          # undeclared -> results boundary-approximate

d  = D.build(df, roles, as_of=None)                  # as_of optional; defaults to latest period
gr = G.run_shared_guards(kb, d, R.CONFIG)            # needs period + regular granularity
if not gr.may_proceed:
    findings = [(r["id"], "NOT-APPLICABLE", gr.block_reason) for r in kb["rules"]]
else:
    findings = [R.COMPLETENESS[r["id"]](d) for r in kb["rules"] if r["id"] in R.COMPLETENESS]
    findings = [(f.rule_id, f.verdict, f.measure) for f in findings]

for rid, verdict, measure in findings:
    print(rid, verdict, measure)
```

## Thresholds you may need to set (in `run_tests.py` CONFIG)
- `portfolio_coverage_floor` (CMG-01) - default 0.99
- `cell_coverage_floor` / `peer_tolerance` (CMU-01) - 0.95 / 0.02
- `minimum_cell_facilities` (CMU-01) - **None by default -> set it or CMU-01 is NO-VERDICT**
- `runoff_convention` - **None -> CMG-01 stamped "boundary-approximate"** (right-edge truncation
  undetectable). Declare it (e.g. `"last_row"`) if your convention is known.

## Guards / caveats
- **No period column -> everything NOT-APPLICABLE.** Bind `period` correctly.
- **Irregular period spacing -> NOT-APPLICABLE** (modal gap must hold for >=80% of gaps; quarterly is
  not a failed monthly panel).
- CMU-01 needs a segment column and `minimum_cell_facilities`.
- The engine never substitutes a default for a missing threshold - it returns NO-VERDICT so gaps are explicit.

## Definition of done
- `binding.json` reviewed; `period` + `facility_id` correctly mapped.
- `dq.py test` (or the programmatic call) returns real verdicts for **CMG-01, CMG-02** (and **CMU-01**
  once `minimum_cell_facilities` is set).
