# Documentation maintenance policy

Aegis Labs uses a one-fact, one-owner model. Update the document that owns a
changed contract, then update summaries only when their abstraction also
changed. Historical release records are immutable evidence and must not be
silently rewritten to resemble the current product.

## Sources of truth

| Subject | Owner | Update trigger |
| --- | --- | --- |
| Workspace navigation | [`../README.md`](../README.md) | Top-level directories or documentation entry points change |
| Setup and developer entry point | [`../source-codes/README.md`](../source-codes/README.md) | Prerequisites, setup, runtime commands, ports, or primary code boundaries change |
| User-visible workflows | [`../source-codes/USER_GUIDE.md`](../source-codes/USER_GUIDE.md) | Navigation, steps, visible states, permissions, or workflow consequences change |
| Current architecture | [`../source-codes/TSD.md`](../source-codes/TSD.md) | Deployables, routers, persistence, configuration, security, or module ownership changes |
| Frontend conventions | [`../source-codes/ui/README.md`](../source-codes/ui/README.md) | API ownership, page/component boundaries, or frontend verification changes |
| Git and version workflow | [`../source-codes/docs/development/`](../source-codes/docs/development/) | Branch, release, version-source, or tagging procedure changes |
| Product handover | [`product/aegis-labs-product-spec.md`](product/aegis-labs-product-spec.md) | Product scope, feature inventory, architecture summary, brand, or deployment model changes |
| Release requirements | `releases/<version>/` and `source-codes/docs/<version>/` | A release is planned, accepted, or its recorded decision is corrected |
| Active maintenance | [`../source-codes/TODO.md`](../source-codes/TODO.md) | Approved work starts, finishes, or changes disposition |

The code, public tests, and version file remain authoritative when a historical
record differs. A discrepancy in a current document is a defect to fix.

## Impact rules

- Internal refactoring with no observable behavior normally changes only the
  relevant code-organization section.
- A UI route or workflow change requires review of the user guide, technical
  design, and product feature/route inventory.
- A public route or serialized contract change requires review of the technical
  design and all user/product descriptions of that capability.
- A dependency, prerequisite, command, port, or environment-variable change
  requires review of setup and technical documentation.
- A version change requires synchronization of `source-codes/VERSION`, the UI
  package version, current-document version labels, and the product spec.
- A moved document requires updating relative links before the move is complete.

Do not copy detailed release requirements into current guides. Link to the
release record when provenance matters. Do not turn completed checklists into an
active backlog by leaving them at a current-document entry point.

## Validation tool

Run this from the workspace root:

```powershell
./documentation.ps1 check
```

The checker validates:

- required current and historical structure;
- UTF-8 Markdown and balanced fenced code blocks;
- local Markdown links;
- application-version agreement;
- current-document status markers and retired assertions;
- documentation of every browser route;
- documentation coverage for the split v2 router boundary;
- presence and basic validity of backend and frontend dependency manifests.

Use impact mode before finishing a change to list likely documentation owners:

```powershell
./documentation.ps1 impact source-codes/ui/src/App.jsx source-codes/backend/routers/diagnostics.py
```

Impact mode is advisory. The deterministic `check` command is the release gate.
When adding a new rule, include a focused negative test in
`tools/tests/test_check_documentation.py` so the checker demonstrates that the
rule can fail.

## Author checklist

1. Identify the owning current document from the table above.
2. Update facts at their owner and link from consumers.
3. Mark proposals and historical behavior explicitly.
4. Preserve exact public routes, identifiers, statuses, and version numbers.
5. Run `./documentation.ps1 check`.
6. Run the affected application tests; documentation validation does not prove
   product behavior.
