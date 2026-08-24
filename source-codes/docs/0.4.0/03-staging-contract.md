# 0.4.0 Staging contract (FWK-08…FWK-12, D-18)

## 1. Stage-1 → Stage-2 dependency (FWK-08/FWK-10)

Execution is **staged with a dependency contract**, not a flat sequential run:

- **Stage 1** diagnostics (hard/structural; in the register: #6, #8; #4/#11/#12/#20 are `Both`)
  run first. Stage 1 **writes the value-semantics tags** (#8's classification output) that Stage 2
  and RCA consume (FWK-12 — a first-class record, not a transient intermediate).
- **Stage 2** statistical screens (#2, #14, #17; plus the Stage-2 half of `Both` diagnostics)
  depend on those tags. **The value-semantics refusal (FWK-10):** a Stage-2 statistical screen
  whose target columns have no value-semantics tags **refuses to run** and reports the missing
  dependency — it must never run unguarded, and the runner must never silently reorder it into
  compliance.
- The runner orders stages; refusal is the diagnostic's own guard — both are asserted (3-T7).

## 2. The guard order (FWK-09, D-18)

Three guards scope every **statistical screen**, in exactly this order:

1. **Class-based eligibility** — a metric never runs on an incompatible column class.
2. **Material-fields filter** (reinstated by D-18, superseding D-05) — a field is material when it
   **maps to a KB semantic role**, OR sits in the **declared feature/target set**, OR is
   **dictionary-flagged for the use case**; immaterial otherwise (identifiers, free text,
   audit/system metadata). Materiality is **derived at runtime from KB + dictionary + profiling —
   never a hardcoded list** — and every exclusion is reported, not silent.
3. **Value-semantics gate** (FWK-11) — never compute over values tagged censored / sentinel / N-A;
   scope exclusion is reported.

**Deterministic hard rules are not filtered by materiality** — a KB rule runs wherever its roles
resolve.

### Slice-1 shape (built in Phase 3)

The shared guard-order skeleton exists now so no statistical engine can ever bypass it: the runner
routes every statistical screen through `class eligibility → material fields → value-semantics`,
where the material and value-semantics guards return explicit **"producer not yet available"**
markers until their producers land (#8 for tags; the D-18 materiality derivation with the first
statistical workflow). A statistical screen invoked outside the guard chain is a structural error
(3-T6). Since no statistical diagnostic is executable in slice 1, the chain is exercised by a
fixture fake diagnostic in tests, not by product paths.

## 3. Slice-1 censoring note (requirements §1.3)

Until #8 ships, the only executable diagnostic — #4 cross-field — handles censoring through each
rule's **encoded exceptions and verdict logic**, exactly as S5 does (S9's own verdict rule excludes
censored participants per rule; e.g. IRB-03 "'open' workouts are censored and out of scope by
construction"). That is **rule-local scope handling, not the general gate**. FWK-10/11 bite the
moment the first Stage-2 statistical screen becomes executable, and **#8 must be in place before
that happens** (DIA-01 — the natural second workflow, backlog B1).

## 4. Why this is a MUST (worked example, preserved from the requirements)

`recovery_workout.realised_lgd` under KB rule IRB-03 (*IF workout closed/written_off THEN
realised_lgd populated (open = censored)*): without tags, row-completeness raises a false ~25%
missing defect on recent vintages; IRB-35's bound flags non-violations that S9's own verdict rule
excludes; robust-Z computes over a mixed population or treats a sentinel as a value; #17 has
nothing to count (censoring *is* its subject); RCA hunts a pipeline fault for something correct by
construction — worst case closing **Confirmed — data defect** against an innocent data owner.
