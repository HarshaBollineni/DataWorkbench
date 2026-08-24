"""Governed knowledge resolver for Test 2, Diagnostic 6.

The JSON package is bootstrap input only. Published KB rows are the runtime
authority; this module validates their binding to the closed Python primitive
registry before projecting them into a frozen diagnostic manifest.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import kb
import system_db as db


PACKAGE_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"
PACKAGE_GLOB = "row_completeness_v*.json"
EXPECTED_RULE_IDS = tuple(f"T2D6-{number:02d}" for number in range(1, 7))
SUPPORTED_PRIMITIVES = {
    "T2D6-01": "key_assignability",
    "T2D6-02": "panel_calendar_continuity",
    "T2D6-03": "period_level_coverage",
    "T2D6-04": "duplicate_facility_period_pairs",
    "T2D6-05": "facility_period_coverage",
    "T2D6-06": "segment_period_coverage",
}
FLOOR_RULE_IDS = {"T2D6-03", "T2D6-05", "T2D6-06"}
ALLOWED_ROLES = {"facility_id", "period", "segment"}
EDITABLE_DIAGNOSTIC_FIELDS = {"name", "what_it_computes", "metric",
                              "threshold_rule_default"}
EDITABLE_CONFIGURATION_FIELDS = {"default_reporting_grain", "default_continuity_floor",
                                 "continuity_floor_label", "continuity_floor_help"}
EDITABLE_RULE_FIELDS = {"title", "user_help", "severity", "next_step"}
ALLOWED_SEVERITIES = {"CRITICAL", "MATERIAL"}
REPORTING_GRAINS = {"monthly", "quarterly", "semiannual", "annual"}


class RowCompletenessKnowledgeError(RuntimeError):
    pass


def load_seed_packages() -> list[dict[str, Any]]:
    packages = []
    for path in sorted(PACKAGE_DIR.glob(PACKAGE_GLOB)):
        package = json.loads(path.read_text(encoding="utf-8"))
        _validate_specs(package["rules"])
        if package.get("diagnostic_id") != 6 or package.get("schema_version") != 1:
            raise RowCompletenessKnowledgeError(f"{path.name} has an unsupported package contract")
        packages.append(package)
    if not packages:
        raise RowCompletenessKnowledgeError("no governed T2D6 knowledge package is installed")
    sequences = [package["version_seq"] for package in packages]
    version_ids = [package["version_id"] for package in packages]
    if len(sequences) != len(set(sequences)) or len(version_ids) != len(set(version_ids)):
        raise RowCompletenessKnowledgeError("T2D6 knowledge package versions must be unique")
    return sorted(packages, key=lambda package: package["version_seq"])


def load_seed_package() -> dict[str, Any]:
    """Return the latest source-controlled, contract-valid package."""
    return load_seed_packages()[-1]


def _validate_specs(rules: list[dict[str, Any]]) -> None:
    ids = tuple(rule.get("rule_id") for rule in rules)
    if ids != EXPECTED_RULE_IDS:
        raise RowCompletenessKnowledgeError(
            "T2D6 KB must contain exactly T2D6-01 through T2D6-06 in display order"
        )
    for order, rule in enumerate(rules, 1):
        rule_id = rule["rule_id"]
        if rule.get("display_order") != order:
            raise RowCompletenessKnowledgeError(f"{rule_id} has an unsupported display order")
        if rule.get("primitive") != SUPPORTED_PRIMITIVES[rule_id]:
            raise RowCompletenessKnowledgeError(f"{rule_id} is not bound to its supported primitive")
        if bool(rule.get("uses_continuity_floor")) != (rule_id in FLOOR_RULE_IDS):
            raise RowCompletenessKnowledgeError(f"{rule_id} has an unsupported floor binding")
        roles = set(rule.get("required_roles") or [])
        if not roles or not roles <= ALLOWED_ROLES:
            raise RowCompletenessKnowledgeError(f"{rule_id} has unsupported semantic roles")
        if bool(rule.get("optional")) != (rule_id == "T2D6-06"):
            raise RowCompletenessKnowledgeError(f"{rule_id} has an unsupported applicability setting")
        if not all(str(rule.get(field) or "").strip() for field in
                   ("title", "user_help", "severity", "next_step")):
            raise RowCompletenessKnowledgeError(f"{rule_id} is missing required presentation metadata")


def _hash_package(package: dict[str, Any]) -> str:
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _register_seed_package(package: dict[str, Any], outcome: dict[str, Any],
                           tenant_id: str) -> None:
    if db.query_one("diagnostic_kb_packages", version_id=package["version_id"]):
        return
    db.insert("diagnostic_kb_packages", {
        "package_id": f"kbpkg_t2d6_seed_v{package['version_seq']}",
        "tenant_id": tenant_id, "diagnostic_id": 6,
        "document_id": package["document_id"], "version_id": package["version_id"],
        "version_seq": package["version_seq"], "based_on_version_id": None,
        "package_hash": outcome["package_hash"], "lifecycle_state": "installed",
        "package_json": package,
        "validation_json": {"valid": True, "errors": [], "warnings": [],
                            "contract": "closed-engine-v1"},
        "change_summary": "Source-controlled baseline package.",
        "created_by": "system-kb-seed", "created_at": db.now_ist(),
        "reviewed_by": "system-kb-seed", "reviewed_at": db.now_ist(),
        "activation_reason": None, "activated_at": None,
    })


def _active_row(tenant_id: str = "bootstrap") -> dict[str, Any]:
    row = db.query_one("diagnostic_kb_packages", tenant_id=tenant_id,
                       diagnostic_id=6, lifecycle_state="active")
    if not row:
        raise RowCompletenessKnowledgeError("no active governed T2D6 package is available")
    return row


def _apply_package_to_register(package: dict[str, Any], actor: str) -> None:
    diagnostic = package["diagnostic"]
    db.upsert("diagnostic_register", {
        "diagnostic_id": 6,
        **{key: diagnostic[key] for key in (
            "area", "mode", "name", "what_it_computes", "metric", "threshold_based",
            "threshold_rule_default", "det_stat", "decision_type", "stage", "kb_dependency",
            "workflow_status", "enabled_by"
        )},
        "l2_areas_json": diagnostic["l2_areas"], "updated_at": db.now_ist(),
    })
    threshold = db.query_one("threshold_settings", diagnostic_id=6,
                             key="continuity_floor", scope="default")
    value = package["configuration"]["default_continuity_floor"]
    if threshold and threshold.get("value_json") != value:
        db.update("threshold_settings", {"id": threshold["id"]}, {
            "value_json": value, "actor": actor, "ts": db.now_ist()})
    elif not threshold:
        db.insert("threshold_settings", {
            "diagnostic_id": 6, "key": "continuity_floor", "value_json": value,
            "scope": "default", "scope_ref": None, "actor": actor, "ts": db.now_ist(),
        })


def _strip_annotations(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_annotations(child) for key, child in value.items()
                if not key.startswith("_")}
    if isinstance(value, list):
        return [_strip_annotations(child) for child in value]
    return value


def editable_template(tenant_id: str = "bootstrap") -> dict[str, Any]:
    """Return the active package plus machine-readable live-editor guidance."""
    seed_package(tenant_id)
    active = copy.deepcopy(_active_row(tenant_id)["package_json"])
    guide = {
        "workflow": [
            "Use the guided editor in Knowledge Base > Diagnostic packages.",
            "Only the fields listed below are editable.",
            "Saving validates and creates a draft; it does not affect diagnostic runs.",
            "A KB reviewer must separately activate the validated draft.",
        ],
        "editable_fields": {
            "change_summary": "Required: explain why this version is needed.",
            "methodology": "Plain-language explanation only; executable logic is unchanged.",
            "diagnostic": sorted(EDITABLE_DIAGNOSTIC_FIELDS),
            "configuration": sorted(EDITABLE_CONFIGURATION_FIELDS),
            "each_rule": sorted(EDITABLE_RULE_FIELDS),
        },
        "allowed_values": {
            "severity": sorted(ALLOWED_SEVERITIES),
            "default_reporting_grain": sorted(REPORTING_GRAINS),
            "default_continuity_floor": "Number from 0 through 1, for example 0.95.",
        },
        "do_not_change": [
            "schema_version", "contract_version", "diagnostic_id",
            "methodology_version", "based_on_version_id", "rule_id", "display_order",
            "primitive", "uses_continuity_floor", "required_roles", "optional",
            "diagnostic.area/mode/stage/decision_type/workflow_status",
            "configuration.segment_optional",
        ],
        "note": "Keys beginning with '_' are guidance and are ignored during submission.",
    }
    # Keep the contract in the payload so clients can render the same guardrails.
    template = {"_editing_guide": guide,
                "based_on_version_id": active["version_id"], "change_summary": "",
                **{key: value for key, value in active.items()
                   if key not in {"document_id", "version_id", "version_seq"}}}
    for rule in template["rules"]:
        rule["_edit_note"] = "Edit title, user_help, severity, or next_step only."
    return template


def _validated_upload(uploaded: dict[str, Any], active: dict[str, Any]) -> dict[str, Any]:
    value = _strip_annotations(uploaded)
    allowed_top = {"schema_version", "contract_version", "diagnostic_id",
                   "based_on_version_id", "change_summary", "methodology_version",
                   "methodology", "diagnostic", "configuration", "rules"}
    unexpected = set(value) - allowed_top
    if unexpected:
        raise RowCompletenessKnowledgeError(
            f"unsupported top-level fields: {', '.join(sorted(unexpected))}")
    if value.get("based_on_version_id") != active["version_id"]:
        raise RowCompletenessKnowledgeError(
            "based_on_version_id must match the currently active package; refresh the editor")
    for field in ("schema_version", "contract_version", "diagnostic_id", "methodology_version"):
        if value.get(field) != active.get(field):
            raise RowCompletenessKnowledgeError(f"{field} is fixed by the supported engine contract")
    summary = str(value.get("change_summary") or "").strip()
    if not summary:
        raise RowCompletenessKnowledgeError("change_summary is required")
    methodology = str(value.get("methodology") or "").strip()
    if not methodology:
        raise RowCompletenessKnowledgeError("methodology must not be blank")

    diagnostic = value.get("diagnostic")
    configuration = value.get("configuration")
    rules = value.get("rules")
    if not isinstance(diagnostic, dict) or not isinstance(configuration, dict) \
            or not isinstance(rules, list):
        raise RowCompletenessKnowledgeError("diagnostic, configuration, and rules are required")
    if set(diagnostic) != set(active["diagnostic"]):
        raise RowCompletenessKnowledgeError("diagnostic fields cannot be added or removed")
    if set(configuration) != set(active["configuration"]):
        raise RowCompletenessKnowledgeError("configuration fields cannot be added or removed")
    for field, current in active["diagnostic"].items():
        if field not in EDITABLE_DIAGNOSTIC_FIELDS and diagnostic.get(field) != current:
            raise RowCompletenessKnowledgeError(f"diagnostic.{field} is not editable")
    for field in EDITABLE_DIAGNOSTIC_FIELDS:
        if not str(diagnostic.get(field) or "").strip():
            raise RowCompletenessKnowledgeError(f"diagnostic.{field} must not be blank")
    for field, current in active["configuration"].items():
        if field not in EDITABLE_CONFIGURATION_FIELDS and configuration.get(field) != current:
            raise RowCompletenessKnowledgeError(f"configuration.{field} is not editable")
    if configuration.get("default_reporting_grain") not in REPORTING_GRAINS:
        raise RowCompletenessKnowledgeError("default_reporting_grain is unsupported")
    for field in ("continuity_floor_label", "continuity_floor_help"):
        if not str(configuration.get(field) or "").strip():
            raise RowCompletenessKnowledgeError(f"configuration.{field} must not be blank")
    floor = configuration.get("default_continuity_floor")
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 <= floor <= 1:
        raise RowCompletenessKnowledgeError("default_continuity_floor must be between 0 and 1")

    active_rules = {rule["rule_id"]: rule for rule in active["rules"]}
    for rule in rules:
        if not isinstance(rule, dict):
            raise RowCompletenessKnowledgeError("every rule must be a JSON object")
        current = active_rules.get(rule.get("rule_id"))
        if current is None or set(rule) != set(current):
            raise RowCompletenessKnowledgeError("rule fields and rule IDs cannot be added or removed")
        for field, current_value in current.items():
            if field not in EDITABLE_RULE_FIELDS and rule.get(field) != current_value:
                raise RowCompletenessKnowledgeError(
                    f"{rule.get('rule_id')}.{field} is not editable")
        if rule.get("severity") not in ALLOWED_SEVERITIES:
            raise RowCompletenessKnowledgeError(
                f"{rule.get('rule_id')}.severity must be CRITICAL or MATERIAL")
    _validate_specs(rules)
    normalized = copy.deepcopy(active)
    normalized.update({"methodology": methodology, "diagnostic": diagnostic,
                       "configuration": configuration, "rules": rules})
    return {"package": normalized, "change_summary": summary}


def seed_package(tenant_id: str = "bootstrap") -> dict[str, Any]:
    packages = load_seed_packages()
    outcomes = [kb.ensure_system_diagnostic_package(package, tenant_id=tenant_id)
                for package in packages]
    for package, package_outcome in zip(packages, outcomes, strict=True):
        _register_seed_package(package, package_outcome, tenant_id)
    active = db.query_one("diagnostic_kb_packages", tenant_id=tenant_id,
                          diagnostic_id=6, lifecycle_state="active")
    if active is None:
        latest = db.query_one("diagnostic_kb_packages", tenant_id=tenant_id,
                              diagnostic_id=6, version_id=packages[-1]["version_id"])
        db.update("diagnostic_kb_packages", {"package_id": latest["package_id"]}, {
            "lifecycle_state": "active", "activation_reason": "Initial governed baseline",
            "activated_at": db.now_ist(),
        })
        active = _active_row(tenant_id)
    package = active["package_json"]
    outcome = next((item for item in outcomes if item["version_id"] == package["version_id"]), {
        "document_id": active["document_id"], "version_id": active["version_id"],
        "package_hash": active["package_hash"], "inserted": False,
    })
    _apply_package_to_register(package, "system-kb-seed")
    return {**outcome, "inserted": any(value.get("inserted") for value in outcomes)}


def _package_markdown(package: dict[str, Any]) -> str:
    return "\n\n".join([
        f"# Test 2, Diagnostic 6 — {package['diagnostic']['name']}",
        package["methodology"],
        *[f"## {rule['rule_id']} — {rule['title']}\n\n{rule['user_help']}\n\n"
          f"Next step: {rule['next_step']}" for rule in package["rules"]],
    ])


def _editable_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    paths = ["methodology"]
    paths += [f"diagnostic.{field}" for field in sorted(EDITABLE_DIAGNOSTIC_FIELDS)]
    paths += [f"configuration.{field}" for field in sorted(EDITABLE_CONFIGURATION_FIELDS)]
    for rule in before["rules"]:
        paths += [f"rules.{rule['rule_id']}.{field}" for field in sorted(EDITABLE_RULE_FIELDS)]

    def read(package: dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        if parts[0] == "rules":
            rule = next(item for item in package["rules"] if item["rule_id"] == parts[1])
            return rule[parts[2]]
        value: Any = package
        for part in parts:
            value = value[part]
        return value

    return [{"path": path, "before": read(before, path), "after": read(after, path)}
            for path in paths if read(before, path) != read(after, path)]


def list_package_versions(tenant_id: str = "bootstrap") -> dict[str, Any]:
    seed_package(tenant_id)
    rows = db.query("diagnostic_kb_packages", tenant_id=tenant_id,
                    diagnostic_id=6, order_by="version_seq DESC")
    by_version = {row["version_id"]: row for row in rows}
    packages = []
    for row in rows:
        base = by_version.get(row.get("based_on_version_id"))
        packages.append({key: row.get(key) for key in (
            "package_id", "diagnostic_id", "document_id", "version_id", "version_seq",
            "based_on_version_id", "package_hash", "lifecycle_state", "validation_json",
            "change_summary", "created_by", "created_at", "reviewed_by", "reviewed_at",
            "activation_reason", "activated_at"
        )} | {"changes": _editable_diff(base["package_json"], row["package_json"])
             if base else []})
    active = next((row for row in packages if row["lifecycle_state"] == "active"), None)
    editor_template = editable_template(tenant_id)
    return {"diagnostic_id": 6, "active": active, "packages": packages,
            "editable_contract": editor_template["_editing_guide"],
            "editor_template": editor_template}


def upload_package_draft(content: bytes, filename: str, actor: str,
                         tenant_id: str = "bootstrap") -> dict[str, Any]:
    if len(content) > 2 * 1024 * 1024:
        raise RowCompletenessKnowledgeError("diagnostic package JSON must not exceed 2 MB")
    try:
        uploaded = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RowCompletenessKnowledgeError(f"invalid JSON: {exc}") from exc
    if not isinstance(uploaded, dict):
        raise RowCompletenessKnowledgeError("the uploaded package must be a JSON object")
    seed_package(tenant_id)
    active_row = _active_row(tenant_id)
    validated = _validated_upload(uploaded, active_row["package_json"])
    rows = db.query("diagnostic_kb_packages", tenant_id=tenant_id, diagnostic_id=6)
    version_seq = max((int(row["version_seq"]) for row in rows), default=0) + 1
    version_id = f"kbver_t2d6_row_completeness_v{version_seq}"
    if db.query_one("diagnostic_kb_packages", version_id=version_id):
        version_id += f"_{uuid.uuid4().hex[:8]}"
    package = validated["package"]
    package.update({"document_id": active_row["document_id"], "version_id": version_id,
                    "version_seq": version_seq})
    package["diagnostic"]["enabled_by"] = f"T2D6 governed knowledge package v{version_seq}"
    package_hash = _hash_package(package)
    validation = {"valid": True, "errors": [], "warnings": [],
                  "contract": "closed-engine-v1", "rules_validated": len(package["rules"])}
    now = db.now_ist()
    markdown = _package_markdown(package)
    db.insert("kb_document_versions", {
        "version_id": version_id, "document_id": active_row["document_id"],
        "version_seq": version_seq, "original_filename": filename,
        "original_media_type": "application/json", "original_sha256": package_hash,
        "original_bytes_ref": None, "original_size": len(content),
        "converted_markdown": markdown,
        "converted_markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "converter_name": "diagnostic-package-json", "converter_version": "1",
        "conversion_warnings_json": [], "conversion_report_json": validation,
        "review_state": "pending_review", "reviewer": None, "reviewed_at": None,
        "created_by": actor, "created_at": now,
    })
    for rule in package["rules"]:
        kb._insert_system_rule(package, rule, tenant_id, actor, now)  # noqa: SLF001
        rule_id, _ = kb._system_rule_identity(package, rule)  # noqa: SLF001
        params, _ = kb._system_rule_params(package, rule)  # noqa: SLF001
        from dq_diagnostics.engines.cross_field.binder import bind_registered_rule
        bind_registered_rule(rule_id, rule["primitive"], params, actor,
                             registry="row_completeness")
        db.update("kb_rules", {"rule_id": rule_id}, {
            "trust_level": "inferred", "lifecycle_state": "draft",
            "effective_date": None, "last_confirmed_date": None,
            "reviewer": None, "approver": None, "updated_at": now,
        })
    package_id = f"kbpkg_t2d6_v{version_seq}_{uuid.uuid4().hex[:8]}"
    db.insert("diagnostic_kb_packages", {
        "package_id": package_id, "tenant_id": tenant_id, "diagnostic_id": 6,
        "document_id": active_row["document_id"], "version_id": version_id,
        "version_seq": version_seq, "based_on_version_id": active_row["version_id"],
        "package_hash": package_hash, "lifecycle_state": "draft",
        "package_json": package, "validation_json": validation,
        "change_summary": validated["change_summary"], "created_by": actor,
        "created_at": now, "reviewed_by": None, "reviewed_at": None,
        "activation_reason": None, "activated_at": None,
    })
    creation_channel = "live_editor" if filename == "row-completeness-live-editor.json" else "json_upload"
    db.insert("transaction_log", {"ts": now, "actor": actor,
        "event": "diagnostic_knowledge_draft_created", "payload": {
            "diagnostic_id": 6, "version_id": version_id,
            "based_on_version_id": active_row["version_id"], "package_hash": package_hash,
            "change_summary": validated["change_summary"], "creation_channel": creation_channel,
        }})
    return next(row for row in list_package_versions(tenant_id)["packages"]
                if row["package_id"] == package_id)


def activate_package(version_id: str, reason: str, actor: str,
                     tenant_id: str = "bootstrap") -> dict[str, Any]:
    rationale = (reason or "").strip()
    if not rationale:
        raise RowCompletenessKnowledgeError("an activation rationale is required")
    candidate = db.query_one("diagnostic_kb_packages", tenant_id=tenant_id,
                             diagnostic_id=6, version_id=version_id)
    if not candidate:
        raise KeyError("Unknown T2D6 knowledge package version")
    if candidate["lifecycle_state"] == "active":
        return candidate
    if candidate["lifecycle_state"] != "draft" or not candidate["validation_json"].get("valid"):
        raise RowCompletenessKnowledgeError("only a validated draft package can be activated")
    active = _active_row(tenant_id)
    if candidate["based_on_version_id"] != active["version_id"]:
        raise RowCompletenessKnowledgeError(
            "this draft is based on a superseded version; refresh the editor and create a new draft")
    package = candidate["package_json"]
    _validate_specs(package["rules"])
    now = db.now_ist()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE diagnostic_kb_packages SET lifecycle_state='superseded' "
                     "WHERE package_id=? AND lifecycle_state='active'", (active["package_id"],))
        conn.execute("UPDATE kb_rules SET lifecycle_state='superseded', updated_at=? "
                     "WHERE version_id=? AND lifecycle_state='published'", (now, active["version_id"]))
        changed = conn.execute(
            "UPDATE kb_rules SET lifecycle_state='published', trust_level='human_confirmed', "
            "effective_date=?, last_confirmed_date=?, reviewer=?, approver=?, updated_at=? "
            "WHERE version_id=? AND lifecycle_state='draft' AND binding_status='bound'",
            (now, now, actor, actor, now, version_id),
        )
        if changed.rowcount != len(package["rules"]):
            raise RowCompletenessKnowledgeError("activation requires six validated, bound draft rules")
        conn.execute(
            "UPDATE diagnostic_kb_packages SET lifecycle_state='active', reviewed_by=?, "
            "reviewed_at=?, activation_reason=?, activated_at=? WHERE package_id=?",
            (actor, now, rationale, now, candidate["package_id"]),
        )
        conn.execute("UPDATE kb_document_versions SET review_state='approved', reviewer=?, "
                     "reviewed_at=? WHERE version_id=?", (actor, now, version_id))
        conn.commit()
    _apply_package_to_register(package, actor)
    db.insert("transaction_log", {"ts": now, "actor": actor,
        "event": "diagnostic_knowledge_activated", "payload": {
            "diagnostic_id": 6, "version_id": version_id,
            "superseded_version_id": active["version_id"],
            "package_hash": candidate["package_hash"], "reason": rationale,
        }})
    return db.query_one("diagnostic_kb_packages", version_id=version_id)


def resolve_package(tenant_id: str = "bootstrap", case_id: str | None = None) -> dict[str, Any]:
    installation = seed_package(tenant_id=tenant_id)
    active = _active_row(tenant_id)
    package = active["package_json"]
    retrieval = kb.retrieve_diagnostic_package_rules(
        tenant_id, package["diagnostic_id"], package["version_id"], case_id=case_id
    )
    specs = []
    for row in retrieval["rules"]:
        params = row.get("binding_params_json") or {}
        specs.append({
            **params,
            "kb_rule_id": row["rule_id"],
            "rule_hash": row["rule_hash"],
            "binding_primitive": row["binding_primitive"],
        })
    _validate_specs(specs)
    package_hash = hashlib.sha256(json.dumps(
        specs, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": package["schema_version"],
        "contract_version": package["contract_version"],
        "document_id": installation["document_id"],
        "version_id": installation["version_id"],
        "document_sha256": active["package_hash"],
        "package_hash": package_hash,
        "retrieval_manifest_id": retrieval["manifest_id"],
        "methodology": package["methodology_version"],
        "methodology_summary": package["methodology"],
        "diagnostic": package["diagnostic"],
        "configuration": package["configuration"],
        "rules": specs,
    }
