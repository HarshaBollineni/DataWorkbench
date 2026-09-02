"""Adapter from DataWorkbench snapshots to the ported missingness engine."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

import system_db as db
from domains.aar.repository import AnalysisArtifactRepository
from analysis_runtime.contracts import (
    AnalysisScope,
    FrozenAnalysisManifest,
    SupportingAnalysisResult,
    stable_fingerprint,
)
from analysis_runtime.snapshots import SnapshotLoader
from .missingness_engine import AnalysisConfig, MissingnessAnalyzer, analysis_parameter_catalog
from .missingness_engine.config import API_PARAMETER_BOUNDS, DEFAULT_ROLE_TOLERANCES


_TYPE_MAP = {
    "numerical": "continuous", "numeric": "continuous", "datetime": "date",
    "categorical": "categorical", "binary": "binary", "ordinal": "categorical",
    "identifier": "identifier", "target": "continuous", "text": "free_text",
    "derived": "other",
}
_ROLE_MAP = {
    "identifier": "identifier", "target": "target", "mandatory": "mandatory",
    "optional": "optional", "free text": "free_text", "free_text": "free_text",
}


def _config(parameters: dict[str, Any] | None) -> AnalysisConfig:
    supplied = dict(parameters or {})
    explicit_role_tolerances = dict(supplied.pop("role_tolerances", {}) or {})
    role_tolerances = {**DEFAULT_ROLE_TOLERANCES, **explicit_role_tolerances}
    # ``tolerance_default`` is the user-facing tolerance for ordinary/unmarked
    # fields. The upstream engine's defaults also contain an explicit
    # ``unmarked=0.05`` entry, which would otherwise mask a changed default.
    # Preserve a separately supplied unmarked override when one is intentional.
    if "tolerance_default" in supplied and "unmarked" not in explicit_role_tolerances:
        role_tolerances["unmarked"] = supplied["tolerance_default"]
    allowed = set(AnalysisConfig.__dataclass_fields__) - {"role_tolerances"}
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise ValueError("Unknown missingness parameter(s): " + ", ".join(unknown))
    for key, value in supplied.items():
        if key in API_PARAMETER_BOUNDS:
            low, high = API_PARAMETER_BOUNDS[key]
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
    for role, value in role_tolerances.items():
        if not 0 <= value <= 1:
            raise ValueError(f"role_tolerances.{role} must be between 0 and 1")
    config = AnalysisConfig(**supplied, role_tolerances=role_tolerances)
    config.validate()
    return config


def _inventory(snapshot_id: str, table: str, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    wanted = set(columns)
    return [row for row in db.query("variable_inventory", item_id=snapshot_id)
            if row.get("table_name") == table
            if row["column_name"] in wanted]


def _table_metadata(snapshot_id: str, table: str) -> dict[str, Any]:
    rows = db.execute(
        "SELECT * FROM dq_item_tables WHERE item_id=? AND table_name=? LIMIT 1",
        [snapshot_id, table],
    )
    if not rows:
        return {}
    row = rows[0]
    columns = row.get("columns")
    if isinstance(columns, str):
        import json
        try:
            row["columns"] = json.loads(columns)
        except ValueError:
            row["columns"] = []
    return row


def _dictionary_frame(inventory: list[dict[str, Any]],
                      missing_codes: dict[str, list[Any]]) -> pd.DataFrame | None:
    if not inventory and not missing_codes:
        return None
    rows = []
    for item in inventory:
        provisional = bool(item.get("provisional"))
        classification = str(item.get("classification") or "").strip().lower()
        role = str(item.get("role") or "").strip().lower()
        rows.append({
            "column_name": item["column_name"],
            "description": item.get("description") or "",
            "logical_type": "" if provisional else _TYPE_MAP.get(classification, ""),
            "role": _ROLE_MAP.get(role, "unmarked"),
            # Codes appear only when explicitly confirmed in the frozen request.
            "missing_value_codes": ",".join(map(str, missing_codes.get(item["column_name"], []))),
            "business_context": item.get("notes") or "",
        })
    existing = {row["column_name"] for row in rows}
    rows.extend({
        "column_name": column, "description": "", "logical_type": "", "role": "unmarked",
        "missing_value_codes": ",".join(map(str, codes)), "business_context": "",
    } for column, codes in missing_codes.items() if column not in existing)
    return pd.DataFrame(rows)


def _confirmed_missing_codes(inventory: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Return only Data Sourcing sentinel decisions explicitly confirmed by a user."""
    return {
        str(item["column_name"]): list(item.get("missing_value_codes")
                                      or item.get("missing_value_codes_json") or [])
        for item in inventory
        if item.get("missing_codes_confirmed")
        and (item.get("missing_value_codes") or item.get("missing_value_codes_json"))
    }


class MissingnessCapability:
    capability_id = "missingness_explanation"
    name = "Missingness Mechanism"
    version = "stage1-descriptive-v2"
    description = (
        "Explain field-level missing shares, shared gaps and descriptive patterns. "
        "Results require review and do not prove MCAR, MAR, MNAR or causality."
    )

    def __init__(self, loader: SnapshotLoader | None = None,
                 artifacts: AnalysisArtifactRepository | None = None):
        # Resolve defaults at operation time. Production paths are stable, while
        # this also avoids pinning import-time environment paths in test/app factories.
        self._loader = loader
        self._artifacts = artifacts

    @property
    def loader(self) -> SnapshotLoader:
        return self._loader or SnapshotLoader()

    @property
    def artifacts(self) -> AnalysisArtifactRepository:
        return self._artifacts or AnalysisArtifactRepository()

    def catalog(self) -> dict[str, Any]:
        return {
            "artifact_type": "missingness_report",
            "decision_type": "supporting",
            "review_required": True,
            "parameters": analysis_parameter_catalog(),
            "caveat": "Descriptive association only; not proof of MCAR, MAR, MNAR or causality.",
        }

    def readiness(self, snapshot_id: str, context: dict[str, Any]) -> dict[str, Any]:
        ref = self.loader.reference(snapshot_id)
        table = str(context.get("table") or "").strip()
        if not table:
            return {"status": "action_required", "reason": "Choose a table to assess."}
        if table not in ref.tables:
            return {"status": "action_required", "reason": f"Unknown table: {table}"}
        table_row = _table_metadata(snapshot_id, table)
        if not int(table_row.get("row_count") or 0):
            return {"status": "not_applicable", "reason": "The selected table has no rows."}
        available = tuple(table_row.get("columns") or ())
        selected = tuple(context.get("columns") or available)
        missing = [column for column in selected if column not in available]
        if missing:
            return {"status": "action_required", "reason": f"Unknown columns: {missing}"}
        if not selected:
            return {"status": "not_applicable", "reason": "The selected table has no columns."}
        if context.get("population"):
            return {"status": "action_required",
                    "reason": "Population filters are not supported in this first missingness slice."}
        period = context.get("period_column")
        if period and period not in selected:
            return {"status": "action_required",
                    "reason": "The period column must be included in the assessed columns."}
        try:
            _config(context.get("parameters"))
        except (TypeError, ValueError) as exc:
            return {"status": "action_required", "reason": str(exc)}
        codes = context.get("missing_value_codes") or {}
        unknown_code_columns = sorted(set(codes) - set(selected))
        if unknown_code_columns:
            return {"status": "action_required",
                    "reason": f"Missing-value codes reference unselected columns: {unknown_code_columns}"}
        return {"status": "ready", "reason": None,
                "detail": {"rows": int(table_row["row_count"]), "columns": len(selected)}}

    def build_manifest(self, snapshot_id: str, context: dict[str, Any]) -> FrozenAnalysisManifest:
        readiness = self.readiness(snapshot_id, context)
        if readiness["status"] != "ready":
            raise ValueError(readiness["reason"])
        ref = self.loader.reference(snapshot_id)
        table_row = _table_metadata(snapshot_id, context["table"])
        columns = tuple(context.get("columns") or table_row.get("columns") or ())
        config = _config(context.get("parameters"))
        inventory = _inventory(snapshot_id, context["table"], columns)
        missing_codes = _confirmed_missing_codes(inventory)
        missing_codes.update({str(key): list(value) for key, value in
                              dict(context.get("missing_value_codes") or {}).items()})
        item = db.query_one("dq_items", item_id=snapshot_id) or {}
        scope = AnalysisScope(table=context["table"], columns=columns,
                              population=dict(context.get("population") or {}))
        return FrozenAnalysisManifest(
            capability_id=self.capability_id, capability_version=self.version,
            snapshot=ref, scope=scope,
            methodology={
                "methodology_version": self.version,
                "parameter_schema_version": "analysis-parameters-v1",
                "parameters": asdict(config),
                "resolved_parameters": config.resolved(int(table_row.get("row_count") or 0)),
                "period_column": context.get("period_column") or None,
                "grain": (context.get("grain") or "").strip() or None,
                "analysis_context": (context.get("analysis_context") or "").strip(),
                "missing_value_codes": missing_codes,
                "dictionary_version_id": item.get("dictionary_version_id"),
                "inventory_fingerprint": stable_fingerprint(inventory),
                "uses_holdout_sample": False,
                "claim_boundary": "Descriptive association only; not proof of MCAR, MAR, MNAR or causality.",
            },
        )

    def execute(self, manifest: FrozenAnalysisManifest, context: dict[str, Any],
                emit=None) -> SupportingAnalysisResult:
        if manifest.capability_id != self.capability_id:
            raise ValueError(f"Manifest is for {manifest.capability_id!r}, not {self.capability_id!r}")
        if emit:
            emit({"phase": "start", "progress": 0, "message": "Loading frozen snapshot"})
        frame = self.loader.load_table(manifest.snapshot.snapshot_id, manifest.scope.table,
                                       list(manifest.scope.columns))
        inventory = _inventory(manifest.snapshot.snapshot_id, manifest.scope.table,
                               manifest.scope.columns)
        dictionary = _dictionary_frame(inventory,
                                       manifest.methodology.get("missing_value_codes") or {})
        config = _config(manifest.methodology.get("parameters"))

        def progress(percent: int, message: str) -> None:
            if emit:
                emit({"phase": "progress", "progress": percent, "message": message})

        report = MissingnessAnalyzer(config).analyze(
            frame, dictionary,
            grain=manifest.methodology.get("grain"),
            period_column=manifest.methodology.get("period_column"),
            analysis_context=manifest.methodology.get("analysis_context"),
            progress_callback=progress,
        ).to_dict()
        run_id = context.get("run_id")
        artifact, reused = self.artifacts.save_or_reuse(
            report, artifact_type="missingness_report",
            asset_id=manifest.snapshot.asset_id,
            snapshot_id=manifest.snapshot.snapshot_id,
            population_fingerprint=manifest.scope.population_fingerprint,
            methodology_fingerprint=manifest.methodology_fingerprint,
            # This evidence is capability-owned; retaining the table separately
            # makes the scope governed rather than an overloaded table string.
            scope="diagnostic_local", owner_id=self.capability_id,
            table=manifest.scope.table, workflow_id=None,
            run_id=run_id, created_by=context.get("actor"),
        )
        observations = tuple({
            "column": finding["column"], "classification": finding["classification"],
            "subtype": finding["subtype"], "role": finding["role"],
            "missing_count": finding["missing_count"],
            "missing_share": finding["missing_share"], "tolerance": finding["tolerance"],
            "exceeds_tolerance": finding["exceeds_tolerance"],
            "review_state": "open" if finding["exceeds_tolerance"] else "not_required",
            "rationale": finding["rationale"], "recommended_action": finding["recommended_action"],
            "block_id": finding["block_id"], "block_members": finding["block_members"],
            "correlates_with": finding["correlates_with"],
        } for finding in report["columns"])
        status = "action_required" if any(row["exceeds_tolerance"] for row in observations) else "complete"
        warnings = tuple(str(row.get("message")) for row in report["preamble"].get("dictionary_warnings", []))
        if emit:
            emit({"phase": "done", "progress": 100, "message": "Assessment complete",
                  "artifact_id": artifact.artifact_id, "reused": reused})
        return SupportingAnalysisResult(
            capability_id=self.capability_id, artifact=artifact, status=status,
            summary=report["summary"], observations=observations, warnings=warnings,
            methodology=report["preamble"],
            review_state="open" if status == "action_required" else "not_required",
        )
