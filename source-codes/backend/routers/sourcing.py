"""Asset sourcing, upload, ingestion, and inventory routes for API v2."""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from ai.v2 import service
from routers.v2_common import raise_api_error, sanitize_internal_errors, stream_events

router = APIRouter()


class ItemIn(BaseModel):
    kind: str
    name: str
    # 0.5.0 AST-22 — the asset's fixed time basis, chosen once at Fresh
    # Upload. Optional for compatibility with clients that predate this field.
    time_basis: str = "none"


class UploadTargetIn(BaseModel):
    kind: str
    alias: str | None = None
    time_basis: str = "none"
    asset_id: str | None = None


class FinalizeIn(BaseModel):
    target_variable: str | None = None
    use_case: str | None = None


class ReuploadIn(BaseModel):
    as_of_date: str | None = None
    # 'add_period' preserves the pre-0.5.0 default behavior.
    intent: str = "add_period"
    snapshot_label: str | None = None
    end_date: str | None = None


class ProcessSnapshotIn(BaseModel):
    intent: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    snapshot_label: str | None = None
    period_column: str | None = None
    target_variable: str | None = None
    use_case: str | None = None
    product: str | None = None
    inventory_rows: list[dict] | None = None
    schema_override_confirmed: bool = False
    schema_override_flag: bool = False
    schema_override_confirmation: bool = False
    schema_override: bool = False
    full_replacement_confirmed: bool = False
    replacement_confirmation: bool = False
    full_replacement_confirmation: bool = False
    uploaded_by: str | None = None


class ItemPatch(BaseModel):
    status: str | None = None


class DictionaryReviewIn(BaseModel):
    header_mapping: dict[str, str]
    value_mapping: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.post("/items/{item_id}/abandon")
@sanitize_internal_errors
def abandon_upload(item_id: str, step: int = 2):
    return service.abandon_upload(item_id, step)


@router.get("/assets/next-id")
def next_asset_id(kind: str):
    """Read-only preview; unlike allocate(), this never inserts or increments."""
    try:
        from assets import identity

        scope = identity.scope_for_kind(kind)
        return {"kind": kind, "scope": scope, "system_id": identity.preview_next_id(scope)}
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/assets")
def list_assets(kind: str | None = None):
    from assets import reads

    return reads.list_assets(kind=kind)


@router.post("/assets/upload-target")
def create_upload_target(body: UploadTargetIn):
    """Create a snapshot target only when the user drops the data file."""
    try:
        import uuid

        import system_db
        from assets import service as assets_service

        if body.asset_id:
            asset = assets_service.require_asset(body.asset_id)
            if asset["kind"] != body.kind:
                raise ValueError("The selected asset kind does not match this upload.")
            snapshots = [
                row for row in system_db.query("dq_items", dataset_family_id=body.asset_id)
                if row.get("snapshot_status") == "active"
            ]
            if not snapshots:
                raise ValueError("The selected asset has no active snapshot.")
            latest = sorted(snapshots, key=lambda row: row.get("created_at") or "")[-1]
            snapshot = assets_service.add_snapshot(
                body.asset_id,
                intent="add_period",
                actor=None,
                start_date=latest.get("start_date"),
                end_date=latest.get("end_date"),
                snapshot_label=f"__staged_{uuid.uuid4().hex[:10]}",
            )
        else:
            if not body.alias:
                raise ValueError("An alias is required for a Fresh Upload.")
            asset = assets_service.create_asset(body.kind, body.alias, body.time_basis, actor=None)
            snapshot = assets_service.add_snapshot(
                asset["asset_id"], intent="fresh", actor=None, _staged=True
            )
        service._item_dir(snapshot["item_id"])
        return {
            "asset": assets_service.require_asset(snapshot["dataset_family_id"]),
            **snapshot,
            "item_id": snapshot["item_id"],
            "asset_id": snapshot["dataset_family_id"],
        }
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/items")
def create_item(body: ItemIn):
    try:
        return service.create_item(body.kind, body.name, body.time_basis)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.patch("/items/{item_id}")
def patch_item(item_id: str, body: ItemPatch):
    try:
        item = service.require_item(item_id)
        if body.status:
            import system_db

            system_db.update(
                "dq_items",
                {"item_id": item_id},
                {"status": body.status, "updated_at": system_db.now_ist()},
            )
            item = service.require_item(item_id)
        return item
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/items/{item_id}/files")
async def upload_file(item_id: str, role: str = Form(...), file: UploadFile = File(...)):
    try:
        return service.save_file(item_id, role, file.filename or "upload.bin", await file.read())
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/items/{item_id}/finalize")
def finalize_item(item_id: str, body: FinalizeIn):
    try:
        return service.finalize_item(item_id, body.target_variable, body.use_case)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/items")
def list_items(kind: str | None = None):
    return service.list_items(kind)


@router.post("/items/{item_id}/reupload")
def reupload_item(item_id: str, body: ReuploadIn):
    try:
        return service.reupload_item(
            item_id, body.as_of_date, body.intent, body.snapshot_label, body.end_date
        )
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/assets/{asset_id}/supersede-preview")
def supersede_preview(asset_id: str):
    try:
        from assets import service as assets_service

        asset = assets_service.require_asset(asset_id)
        return assets_service.preview_supersede(asset_id, asset["current_version_no"])
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/assets/{asset_id}/refresh")
def refresh_asset(asset_id: str):
    """Manual refresh delegates to the same automatic refresh function."""
    try:
        import system_db as system_state
        from analytics.events import event_object_id, record_event
        from assets.refresh import refresh_derived

        asset = system_state.query_one("dq_assets", asset_id=asset_id)
        result = refresh_derived(asset_id, actor="system", reason="manual refresh requested")
        if asset:
            record_event(event_type="refresh_requested", actor="system",
                at=system_state.now_ist(),
                object_type="asset",
                object_id=event_object_id("asset", asset_id),
                workflow_context=asset.get("system_id"),
            )
        return result
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/items/{item_id}/process")
def process_snapshot(item_id: str, body: ProcessSnapshotIn):
    try:
        return service.process_snapshot(
            item_id,
            intent=body.intent,
            start_date=body.start_date,
            end_date=body.end_date,
            snapshot_label=body.snapshot_label,
            period_column=body.period_column,
            target_variable=body.target_variable,
            use_case=body.use_case,
            product=body.product,
            inventory_rows=body.inventory_rows,
            schema_override_confirmed=(
                body.schema_override_confirmed
                or body.schema_override_flag
                or body.schema_override_confirmation
                or body.schema_override
            ),
            full_replacement_confirmed=(
                body.full_replacement_confirmed
                or body.replacement_confirmation
                or body.full_replacement_confirmation
            ),
            uploaded_by=body.uploaded_by,
        )
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/snapshots/{snapshot_id}/summary")
def snapshot_summary(snapshot_id: str):
    try:
        return service.ingest_summary(snapshot_id)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/assets/{asset_id}/versions/{version_no}/restore")
def restore_version(asset_id: str, version_no: int):
    try:
        from assets import service as assets_service

        return assets_service.restore_version_set(asset_id, version_no, actor=None)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/items/{item_id}/ingest")
def item_ingest(item_id: str, table: str | None = None):
    try:
        return service.ingest_summary(item_id, table)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/items/{item_id}/tables")
def item_tables(item_id: str):
    try:
        return service.tables(item_id)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/sources/inspect")
async def inspect_source(role: str = Form(...), file: UploadFile = File(...)):
    """Suggest workbook roles/context before an upload target is created."""
    try:
        return service.inspect_source_upload(file.filename or "source.csv", await file.read(), role)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/items/{item_id}/source-bundle")
async def upload_source_bundle(
    item_id: str,
    data_file: UploadFile = File(...),
    dictionary_file: UploadFile | None = File(None),
    parsing_options: str = Form("{}"),
    file_context: str = Form(""),
    dictionary_header_mapping: str = Form("{}"),
):
    """Stage reviewed data + optional dictionary together, then profile once."""
    try:
        parsed = json.loads(parsing_options or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("parsing_options must be a JSON object")
        header_mapping = json.loads(dictionary_header_mapping or "{}")
        if not isinstance(header_mapping, dict):
            raise ValueError("dictionary_header_mapping must be a JSON object")
        dictionary_payload = await dictionary_file.read() if dictionary_file else None
        return service.save_source_bundle(
            item_id,
            data_file.filename or "source.csv",
            await data_file.read(),
            dictionary_file.filename if dictionary_file else None,
            dictionary_payload,
            parsing_options=parsed,
            file_context=file_context,
            dictionary_header_mapping=header_mapping,
        )
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.put("/items/{item_id}/dictionary-review")
def review_dictionary(item_id: str, body: DictionaryReviewIn):
    try:
        return service.review_dictionary(item_id, body.header_mapping, body.value_mapping)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/items/{item_id}/columns")
def item_columns(item_id: str, table: str | None = None):
    try:
        return service.columns(item_id, table)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/items/{item_id}/profile/stream")
def profile_stream(item_id: str):
    def events():
        messages: queue.Queue[dict] = queue.Queue()

        def worker() -> None:
            try:
                rows = service.profile_item(
                    item_id,
                    progress_callback=lambda progress: messages.put({
                        "phase": "progress",
                        "agent": "data_profiling",
                        "thought": progress["message"],
                        "progress": progress,
                    }),
                )
                ingest = service.ingest_summary(item_id)
                if ingest.get("status") == "failed":
                    messages.put({
                        "phase": "error",
                        "agent": "data_profiling",
                        "thought": ingest.get("fail_reason") or "Profiling could not complete.",
                    })
                    return
                messages.put({
                    "phase": "done",
                    "agent": "data_profiling",
                    "thought": "Variable inventory is ready.",
                    "rows": rows,
                    "ingest": ingest,
                    "progress": {
                        "stage": "complete",
                        "percent": 100,
                        "completed": 9,
                        "total": 9,
                        "message": "Data foundation is ready for review.",
                    },
                })
            except Exception as exc:  # noqa: BLE001
                messages.put({
                    "phase": "error", "agent": "data_profiling", "thought": str(exc)
                })

        yield {
            "phase": "start",
            "agent": "data_profiling",
            "thought": "Reading uploaded tables and dictionaries.",
        }
        threading.Thread(target=worker, daemon=True).start()
        while True:
            event = messages.get()
            yield event
            if event.get("phase") in {"done", "error"}:
                return

    return stream_events(events())


@router.get("/items/{item_id}/inventory")
def get_inventory(item_id: str, table: str | None = None):
    try:
        return service.get_inventory(item_id, table)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.put("/items/{item_id}/inventory")
def put_inventory(item_id: str, rows: list[dict], table: str | None = None):
    try:
        return service.put_inventory(item_id, rows, table)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)
