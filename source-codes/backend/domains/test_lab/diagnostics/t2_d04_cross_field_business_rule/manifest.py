"""The run manifest — build, patch, freeze, persist (CFR-12/14/16).

testlab-redesign-0.4.0.md §3 step 2: the manifest is the *entire*
human-controllable surface and the run's provenance. What the user approves
is the manifest, never code. Three edits are offered — override a role
mapping, tune a threshold, exclude a table — and each writes an append-only
``diag_run_decisions`` row rather than blocking on a prompt. An unattended
run (no PATCH at all) applies the documented defaults and records that it
did.

Freezing is the contract: after ``freeze``, ``diag_runs.manifest_json`` is
the immutable description of what ran, and :func:`rules_from_manifest` /
:func:`roles_from_manifest` reconstruct the executable state from it ALONE
— so "what we said we'd run" and "what actually ran" cannot drift (6-T12),
and two runs of one frozen manifest on one snapshot are byte-identical
(CFR-16 / 6-T7).

Decision-record policy (the plan permits either; this is the choice made):
every field carries its own ``source`` in the manifest (``default`` /
``engagement (ref)`` / ``user-set (actor, ts)``), AND one ``default_applied``
decision row is written at build time listing every field that fell back to
a documented default. User edits then add one specific decision row each.
"""
from __future__ import annotations

import uuid
import json
from typing import Any

import system_db as s
from ai import control_plane, llm

from . import ENGINE_VERSION
from .roles import RoleMatch, derive_vocabulary, resolve_roles
from .rules import Rule
from domains.test_lab.shared.run_state import (
    DRAFT,
    DONE,
    FAILED,
    RUNNING,
    ManifestError,
    get_manifest,
    get_run,
    list_decisions,
    record_decision,
)
from dq_diagnostics.readiness import (
    eligible_cross_field_rules,
    normalize_framework,
    normalize_use_case,
    readiness,
)
from dq_diagnostics.register import require_executable
from dq_diagnostics.thresholds import effective_threshold

MANIFEST_VERSION = 1

# FWK-06 — every parameter #4 uses, read from the semantic layer. No
# threshold constant lives in this module or in the engine.
THRESHOLD_KEYS = ("tolerance", "cluster_lift", "cluster_coverage", "grain_role", "segment_role")

# How many rows are sampled per table for the role resolver's dtype gate.
# S5 inspects up to 200 non-null values per column; reading a bounded slice
# keeps building a manifest cheap on a large dataset (CFR-15 read-only).
SAMPLE_ROWS = 500

PATCH_KINDS = ("role_override", "threshold_tune", "scope_exclusion", "role_verification_change")

# CFR-11/15: this is deliberately a small, metadata-only request.  The
# verifier is advisory: a model cannot silently alter executable mappings;
# a human role_override remains the sole mapping-change policy.  That keeps
# role verification from changing rule maths or a frozen run's verdict.
ROLE_VERIFIER_KEY = "role_mapping_verifier"
MAX_VERIFICATION_ROLES = 32
MAX_VERIFICATION_CANDIDATES = 8
VERIFICATION_POLICY = "advisory_keep_deterministic_mapping"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
#  Dataset access — read-only, bounded (CFR-15)
# ---------------------------------------------------------------------------

def _table_names(item_id: str) -> list[str]:
    return [t["table_name"] for t in s.query("dq_item_tables", item_id=item_id, order_by="table_name")]


def load_tables(item_id: str, tables: list[str]) -> dict[str, Any]:
    """Full read of each in-scope table, for evaluation. Never writes back."""
    from ai.v2 import service as v2_service
    return {name: v2_service._read_table(item_id, name) for name in tables}


def sample_tables(item_id: str, tables: list[str],
                  limit: int = SAMPLE_ROWS) -> tuple[dict[str, list[str]], dict[tuple[str, str], list[Any]]]:
    """``(columns_by_table, {(table, column): [values]})`` for role scoring."""
    frames = load_tables(item_id, tables)
    columns_by_table: dict[str, list[str]] = {}
    samples: dict[tuple[str, str], list[Any]] = {}
    for name, frame in frames.items():
        cols = [str(c) for c in frame.columns]
        columns_by_table[name] = cols
        head = frame.head(limit)
        for col in cols:
            samples[(name, col)] = head[col].tolist()
    return columns_by_table, samples


def column_descriptions(item_id: str) -> dict[tuple[str, str], str]:
    """ING-08 — the ingested dictionary's free-text definition per column.

    This IS the substrate the role resolver's 0.88 dictionary-description
    tier reads (DX-04 clause 2): generic and per-dataset, never a hardcoded
    synonym list. ``variable_inventory.description`` backfills a column the
    mapping records do not cover.
    """
    from ingest import records
    out: dict[tuple[str, str], str] = {}
    for row in s.query("variable_inventory", item_id=item_id):
        if row.get("description"):
            out[(row["table_name"], row["column_name"])] = row["description"]
    for rec in records.get_mapping(item_id):
        source = rec.get("source_column")
        definition = (rec.get("definition") or "").strip()
        if source and definition:
            out[(rec.get("table_name"), source)] = definition
    return out


# ---------------------------------------------------------------------------
#  Build
# ---------------------------------------------------------------------------

def _rule_entry(row: dict[str, Any]) -> dict[str, Any]:
    params = row.get("binding_params_json") or {}
    return {
        "kb_rule_id": row["rule_id"],
        "rule_id": row.get("source_rule_id") or row["rule_id"],
        "severity": (row.get("severity") or "MATERIAL").upper(),
        "rule_type": row.get("rule_type"),
        "entity": row.get("entity"),
        "framework": normalize_framework(row.get("framework")),
        "framework_label": row.get("framework"),
        "roles": list(row.get("semantic_roles_json") or []),
        "rule_text": row.get("rule_text") or "",
        "regulatory_ref": row.get("regulatory_ref"),
        "primitive": row.get("binding_primitive"),
        "params": params,
        "exception_notes": list(row.get("encoded_exceptions_json") or []),
        "source_ref": (f"{row.get('source_rule_id')} (page {row.get('source_page')})"
                       if row.get("source_rule_id") and row.get("source_page")
                       else row.get("source_rule_id") or row["rule_id"]),
        "document_id": row.get("document_id"),
        "version_id": row.get("version_id"),
    }


def _kb_documents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("document_id"), row.get("version_id"))
        if key in seen or not key[0]:
            continue
        doc = s.query_one("kb_documents", document_id=key[0]) or {}
        version = s.query_one("kb_document_versions", version_id=key[1]) or {}
        seen[key] = {"document_id": key[0], "version_id": key[1],
                     "title": doc.get("title"), "version_seq": version.get("version_seq")}
    return [seen[k] for k in sorted(seen, key=lambda k: (str(k[0]), str(k[1])))]


def _threshold_block(diagnostic_id: int) -> dict[str, dict[str, Any]]:
    return {key: {"value": r["value"], "source": r["source"]}
            for key, r in ((k, effective_threshold(diagnostic_id, k)) for k in THRESHOLD_KEYS)}


def _counts(entries: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        key = str(e.get(field))
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _resolve(entries: list[dict[str, Any]], item_id: str, tables: list[str],
             thresholds: dict[str, dict[str, Any]],
             overrides: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Derive the vocabulary (DX-04), score every (table, column) pair and
    return ``(vocab, roles_block, roles_summary)``."""
    rule_rows = [{
        "semantic_roles_json": e["roles"], "entity": e["entity"],
        "rule_text": e["rule_text"], "binding_params_json": e["params"],
        "parse_hazards_json": [],
    } for e in entries]
    extra = tuple(str(thresholds[k]["value"]) for k in ("grain_role", "segment_role")
                  if thresholds.get(k) and thresholds[k].get("value"))
    vocab = derive_vocabulary(rule_rows, extra_roles=extra)
    columns_by_table, samples = sample_tables(item_id, tables)
    descriptions = column_descriptions(item_id)
    resolved = resolve_roles(vocab, columns_by_table, samples, descriptions, overrides)

    roles_block = {}
    for role in sorted(resolved):
        match = resolved[role]
        meta = vocab.get(role, {})
        roles_block[role] = {
            **match.to_dict(),
            "entity": meta.get("entity"),
            "dtype": meta.get("dtype"),
            "synonyms": meta.get("synonyms", []),
        }
    summary = {
        "total": len(resolved),
        "resolved": sum(1 for m in resolved.values() if m.column),
        "override": sum(1 for m in resolved.values() if m.via == "override"),
        "auto": sum(1 for m in resolved.values() if m.via == "auto"),
        "unresolved": sorted(r for r, m in resolved.items() if not m.column),
    }
    return vocab, roles_block, summary


def build_manifest(item_id: str, diagnostic_id: int, actor: str = "system",
                   tenant_id: str = "bootstrap",
                   use_case_override: str | None = None) -> dict[str, Any]:
    """Build a DRAFT manifest and persist it as a ``diag_runs`` row.

    Raises ``WorkflowPendingError`` for a pending diagnostic (FWK-17) and
    ``ManifestError`` when readiness refuses — a manifest for something that
    cannot run would be a lie about scope.

    0.5.0 AST-08/AST-12 (Step 3b) — a superseded snapshot is retained for
    audit and rollback but is never selectable in test configuration; a
    manifest built against one would silently make stale, retired data the
    scope of a "current" run. Refused here, before readiness/use-case
    resolution even run, so the refusal is never shadowed by an unrelated
    readiness verdict.
    """
    item = s.query_one("dq_items", item_id=item_id)
    if item is not None and item.get("snapshot_status") == "superseded":
        raise ManifestError(
            f"Snapshot {item_id!r} has been superseded and is retained for audit only — it "
            "cannot be used to build a diagnostic manifest. Select the asset's current active "
            "snapshot instead."
        )
    register_row = require_executable(diagnostic_id)
    state = readiness(item_id, diagnostic_id, tenant_id)
    if state.status != "ready":
        raise ManifestError(f"{state.status}: {state.reason}")

    if use_case_override:
        use_case, _ = normalize_use_case(use_case_override)
        use_case_source = "user-set"
    else:
        use_case, use_case_source = normalize_use_case(item.get("use_case"))

    rows, rule_counts = eligible_cross_field_rules(tenant_id, use_case)
    entries = sorted((_rule_entry(r) for r in rows), key=lambda e: (e["rule_id"], e["kb_rule_id"]))
    thresholds = _threshold_block(diagnostic_id)
    tables = _table_names(item_id)
    _vocab, roles_block, roles_summary = _resolve(entries, item_id, tables, thresholds, {})

    run_id = _id("drun")
    now = s.now_ist()
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "item_id": item_id,
        "item_name": item.get("name"),
        "tenant_id": tenant_id,
        "diagnostic_id": diagnostic_id,
        "diagnostic": {k: register_row.get(k) for k in
                       ("name", "area", "mode", "stage", "det_stat", "decision_type",
                        "kb_dependency", "metric", "workflow_status")},
        "engine_version": ENGINE_VERSION,
        "status": DRAFT,
        "created_at": now,
        "created_by": actor,
        "use_case": {"value": use_case, "source": use_case_source},
        "kb": {"documents": _kb_documents(rows), "counts": rule_counts},
        "rules": entries,
        "rules_summary": {
            "total": len(entries),
            "by_severity": _counts(entries, "severity"),
            "by_type": _counts(entries, "rule_type"),
            "by_framework": _counts(entries, "framework"),
        },
        "roles": roles_block,
        "roles_summary": roles_summary,
        "role_overrides": {},
        # CFR-11 / D-08: off by default.  Only an explicit DRAFT-manifest
        # role_verification_change can reach the one governed verifier call.
        "role_verification": {"enabled": False,
                              "reason": "disabled by default (explicit opt-in required)"},
        "thresholds": thresholds,
        "scope": {
            "tables": [dict(t) for t in s.query("dq_item_tables", item_id=item_id, order_by="table_name")],
            "excluded_tables": [],
        },
    }
    s.insert("diag_runs", {
        "run_id": run_id, "item_id": item_id, "diagnostic_id": diagnostic_id,
        "manifest_json": manifest, "status": DRAFT,
        "engine_versions_json": {"cross_field": ENGINE_VERSION},
        "created_at": now, "started_at": None, "finished_at": None,
    })
    defaults = {k: v["source"] for k, v in thresholds.items() if v["source"] == "default"}
    if use_case_source == "default":
        defaults["use_case"] = f"documented default '{use_case}' (item use case names no framework)"
    if defaults:
        record_decision(run_id, "default_applied", {"fields": defaults}, actor)
    return manifest


def affected_configurations(item_id: str, affected_columns: set[tuple[str, str]]) -> list[str]:
    """Return distinct stored diagnostic configuration names touching columns.

    This is a read-only lookup used by UPL-23.  Frozen manifests retain the
    selected role mappings and diagnostic name, so no data file is read.
    """
    names: set[str] = set()
    item = s.query_one("dq_items", item_id=item_id) or {}
    family = item.get("dataset_family_id")
    snapshot_ids = [item_id]
    if family:
        snapshot_ids = [row["item_id"] for row in s.query("dq_items", dataset_family_id=family)]
    runs = []
    for snapshot_id in snapshot_ids:
        runs.extend(s.query("diag_runs", item_id=snapshot_id, order_by="created_at"))
    for run in sorted(runs, key=lambda row: row.get("created_at") or ""):
        manifest = run.get("manifest_json") or {}
        refs = set()
        for spec in (manifest.get("roles") or {}).values():
            if spec.get("table") and spec.get("column"):
                refs.add((spec["table"], spec["column"]))
        for table in (manifest.get("scope") or {}).get("tables") or []:
            for column in table.get("columns") or []:
                refs.add((table.get("table_name"), column))
        if refs & affected_columns:
            diagnostic = manifest.get("diagnostic") or {}
            names.add(diagnostic.get("name") or manifest.get("item_name") or run.get("run_id"))
    return sorted(name for name in names if name)


# ---------------------------------------------------------------------------
#  Read / patch / freeze
# ---------------------------------------------------------------------------

def _save(run_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    s.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return manifest


def _scoped_tables(manifest: dict[str, Any]) -> list[str]:
    return [t["table_name"] for t in manifest["scope"]["tables"]]


def _reresolve(manifest: dict[str, Any]) -> None:
    _vocab, roles_block, roles_summary = _resolve(
        manifest["rules"], manifest["item_id"], _scoped_tables(manifest),
        manifest["thresholds"], manifest.get("role_overrides") or {})
    manifest["roles"], manifest["roles_summary"] = roles_block, roles_summary


def _verification_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded, non-row-level mapping payload for the verifier.

    No table values, dictionary prose, rule text, code, SQL, filesystem paths,
    or other dataset content crosses this boundary.  Column names are enough
    for the model to compare the deterministic selected mapping with a small
    candidate set.  The policy is included so the model cannot be mistaken for
    a rule evaluator or an autonomous mapping editor.
    """
    columns_by_table, _samples = sample_tables(
        manifest["item_id"], _scoped_tables(manifest), limit=1)
    choices = [
        {"table": table, "column": column}
        for table in sorted(columns_by_table)
        for column in sorted(columns_by_table[table])
    ][:MAX_VERIFICATION_CANDIDATES]
    roles = []
    for role in sorted(manifest["roles"])[:MAX_VERIFICATION_ROLES]:
        spec = manifest["roles"][role]
        selected = {"table": spec.get("table"), "column": spec.get("column")}
        # Always include the deterministic mapping, including an unresolved
        # ``{table: null, column: null}``, so a strict KEEP can echo it.
        candidates = [selected]
        candidates.extend(c for c in choices if c not in candidates)
        roles.append({
            "role": role,
            "expected_dtype": spec.get("dtype"),
            "synonyms": list(spec.get("synonyms") or [])[:8],
            "deterministic_mapping": {
                **selected,
                "score": spec.get("score"),
                "reason": spec.get("reason"),
                "via": spec.get("via"),
            },
            "candidates": candidates[:MAX_VERIFICATION_CANDIDATES],
        })
    return {
        "schema_version": 1,
        "policy": VERIFICATION_POLICY,
        "roles": roles,
    }


def _strict_verification_decisions(content: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept only the exact, bounded JSON decision shape from the model."""
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManifestError("role verification returned invalid structured output") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"decisions"} or not isinstance(parsed["decisions"], list):
        raise ManifestError("role verification returned invalid structured output")
    expected = {entry["role"]: entry for entry in payload["roles"]}
    if len(parsed["decisions"]) != len(expected):
        raise ManifestError("role verification returned incomplete structured decisions")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in parsed["decisions"]:
        if not isinstance(raw, dict) or set(raw) != {"role", "decision", "table", "column"}:
            raise ManifestError("role verification returned invalid structured decisions")
        role, decision = raw["role"], raw["decision"]
        if (not isinstance(role, str) or role not in expected or role in seen or
                decision not in {"keep", "llm", "manual"} or
                not isinstance(raw["table"], (str, type(None))) or
                not isinstance(raw["column"], (str, type(None)))):
            raise ManifestError("role verification returned invalid structured decisions")
        proposed = {"table": raw["table"], "column": raw["column"]}
        current = expected[role]["deterministic_mapping"]
        if decision == "keep" and proposed != {"table": current["table"], "column": current["column"]}:
            raise ManifestError("role verification KEEP must echo the deterministic mapping")
        if decision != "keep" and proposed not in expected[role]["candidates"]:
            raise ManifestError("role verification proposed a mapping outside the bounded candidates")
        seen.add(role)
        out.append({"role": role, "decision": decision, **proposed})
    return sorted(out, key=lambda entry: entry["role"])


def _verify_role_mappings(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Make exactly one governed metadata-only verifier call.

    The response may disagree, but the recorded advisory policy keeps the
    deterministic executable map.  A user who accepts an LLM suggestion must
    make the existing explicit ``role_override`` edit, which is separately
    append-only and remains inspectable in the frozen manifest.
    """
    payload = _verification_payload(manifest)
    cfg = control_plane.resolve(ROLE_VERIFIER_KEY)
    request_model = llm.get_model()
    messages = [
        {"role": "system", "content": (
            "You verify semantic role-to-column mappings only. Return JSON only: "
            '{"decisions":[{"role":"...","decision":"keep|llm|manual",'
            '"table":"...|null","column":"...|null"}]}. '
            "Return one decision for every supplied role. Do not assess rules, data quality, or verdicts.")},
        {"role": "user", "content": json.dumps(payload, sort_keys=True, separators=(",", ":"))},
    ]
    client = llm.get_client(cfg["house"])
    response = client.chat.completions.create(
        model=request_model, messages=messages, temperature=cfg["temperature"],
        max_completion_tokens=1200, response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    decisions = _strict_verification_decisions(content, payload)
    return ({"enabled": True, "status": "completed", "policy": VERIFICATION_POLICY,
             "agent_key": ROLE_VERIFIER_KEY, "model": request_model,
             "roles_sent": len(payload["roles"])}, decisions)


def patch_manifest(run_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    """Apply ONE scope-gate edit and record it. Never blocks; never prompts.

    ``patch['kind']`` is one of :data:`PATCH_KINDS`. Editing a frozen
    (running/finished) manifest is refused — provenance is immutable.
    """
    run = get_run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError(f"manifest {run_id} is frozen (status {run['status']}) and cannot be edited")
    manifest = run["manifest_json"]
    kind = patch.get("kind")

    if kind == "role_override":
        role = patch.get("role")
        if role not in manifest["roles"]:
            raise ManifestError(f"unknown role: {role!r}")
        column = patch.get("column")
        before = dict(manifest["roles"][role])
        overrides = dict(manifest.get("role_overrides") or {})
        if column:
            overrides[role] = column
        else:
            overrides.pop(role, None)
        manifest["role_overrides"] = overrides
        _reresolve(manifest)
        record_decision(run_id, "role_override",
                        {"role": role, "column": column, "before": before,
                         "after": manifest["roles"].get(role)}, actor)

    elif kind == "threshold_tune":
        key = patch.get("key")
        if key not in manifest["thresholds"]:
            raise ManifestError(f"unknown threshold key: {key!r}")
        before = dict(manifest["thresholds"][key])
        manifest["thresholds"][key] = {"value": patch.get("value"),
                                       "source": f"user-set ({actor}, {s.now_ist()})"}
        if key in ("grain_role", "segment_role"):
            _reresolve(manifest)
        record_decision(run_id, "threshold_tune",
                        {"key": key, "before": before, "after": manifest["thresholds"][key]}, actor)

    elif kind == "scope_exclusion":
        table = patch.get("table")
        reason = (patch.get("reason") or "").strip()
        if not reason:
            raise ManifestError("a scope exclusion requires a reason")
        names = [t["table_name"] for t in manifest["scope"]["tables"]]
        if table not in names:
            raise ManifestError(f"unknown table: {table!r}")
        manifest["scope"]["tables"] = [t for t in manifest["scope"]["tables"]
                                       if t["table_name"] != table]
        manifest["scope"]["excluded_tables"].append({"table_name": table, "reason": reason,
                                                     "actor": actor, "ts": s.now_ist()})
        _reresolve(manifest)
        record_decision(run_id, "scope_exclusion", {"table": table, "reason": reason}, actor)

    elif kind == "role_verification_change":
        enabled = bool(patch.get("enabled"))
        if not enabled:
            manifest["role_verification"] = {"enabled": False,
                                               "reason": "disabled by explicit scope-gate edit"}
            record_decision(run_id, "role_verification_change",
                            {"requested": False, "applied": True}, actor)
        else:
            # This call is intentionally reachable ONLY through the explicit
            # draft-manifest opt-in.  The default build/run path makes zero
            # calls, and no verifier output reaches rule evaluation.
            verification, decisions = _verify_role_mappings(manifest)
            manifest["role_verification"] = verification
            record_decision(run_id, "role_verification_change",
                            {"requested": True, "applied": True,
                             "policy": VERIFICATION_POLICY,
                             "roles_sent": verification["roles_sent"]}, actor)
            for decision in decisions:
                baseline = manifest["roles"][decision["role"]]
                proposed = {"table": decision["table"], "column": decision["column"]}
                current = {"table": baseline.get("table"), "column": baseline.get("column")}
                if decision["decision"] == "keep":
                    outcome = "keep"
                elif decision["decision"] == "manual":
                    outcome = "manual"
                elif proposed == current:
                    outcome = "keep"
                else:
                    outcome = "llm"
                # ``diag_run_decisions.kind`` is deliberately a closed
                # vocabulary.  Keep verifier outcomes under its existing
                # role_verification_change kind rather than adding a schema
                # migration for a provenance-only subevent.
                record_decision(run_id, "role_verification_change",
                                {"event": "decision", "role": decision["role"], "outcome": outcome,
                                 "deterministic_mapping": current,
                                 "proposed_mapping": proposed,
                                 "mapping_applied": False,
                                 "policy": VERIFICATION_POLICY}, actor)
    else:
        raise ManifestError(f"kind must be one of {', '.join(PATCH_KINDS)}")
    return _save(run_id, manifest)


def freeze(run_id: str, actor: str = "system") -> dict[str, Any]:
    """Freeze the manifest and mark the run started. After this the manifest
    is the immutable record of exactly what executes."""
    run = get_run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError(f"run {run_id} is already {run['status']}")
    manifest = run["manifest_json"]
    now = s.now_ist()
    manifest["status"] = RUNNING
    manifest["frozen_at"] = now
    manifest["frozen_by"] = actor
    s.update("diag_runs", {"run_id": run_id},
             {"manifest_json": manifest, "status": RUNNING, "started_at": now})
    return manifest


# ---------------------------------------------------------------------------
#  Reconstruction FROM the frozen manifest (6-T12 / CFR-16)
# ---------------------------------------------------------------------------

def threshold_values(manifest: dict[str, Any]) -> dict[str, Any]:
    return {k: v["value"] for k, v in manifest["thresholds"].items()}


def threshold_sources(manifest: dict[str, Any]) -> dict[str, str]:
    return {k: v["source"] for k, v in manifest["thresholds"].items()}


def rules_from_manifest(manifest: dict[str, Any]) -> list[Rule]:
    """Rebuild the executable rule list from the manifest alone."""
    tolerance = float(threshold_values(manifest).get("tolerance") or 0.0)
    out: list[Rule] = []
    for entry in manifest["rules"]:
        params = entry.get("params") or {}
        exceptions = tuple((e["role"], e["value"]) for e in (params.get("exceptions") or []))
        out.append(Rule(
            id=entry["rule_id"],
            rule_type=entry["rule_type"],
            description=entry["rule_text"],
            roles=tuple(entry["roles"]),
            primitive=entry["primitive"],
            params=params,
            entity=entry.get("entity"),
            framework=entry.get("framework"),
            severity=entry.get("severity") or "MATERIAL",
            tolerance=tolerance,
            exceptions_spec=exceptions,
            exception_notes=tuple(entry.get("exception_notes") or []),
            regulatory_ref=entry.get("regulatory_ref"),
            kb_rule_id=entry.get("kb_rule_id"),
            source_ref=entry.get("source_ref"),
        ))
    return out


def roles_from_manifest(manifest: dict[str, Any]) -> dict[str, RoleMatch]:
    return {role: RoleMatch(role=role, table=spec.get("table"), column=spec.get("column"),
                            score=spec.get("score", 0.0), reason=spec.get("reason", ""),
                            via=spec.get("via", "unresolved"))
            for role, spec in manifest["roles"].items()}


def vocab_from_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {role: {"entity": spec.get("entity", "*"), "dtype": spec.get("dtype"),
                   "synonyms": spec.get("synonyms", [])}
            for role, spec in manifest["roles"].items()}
