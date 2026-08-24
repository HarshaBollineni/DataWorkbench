"""Shared response and error helpers for the v2 router family."""
from __future__ import annotations

import json
import functools
import logging
from collections.abc import Iterable

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ai.v2 import issues as issues_svc

logger = logging.getLogger(__name__)


def sanitize_internal_errors(fn):
    """Preserve deliberate HTTP errors and sanitize unexpected failures."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled error in v2 route %s", fn.__name__)
            raise HTTPException(status_code=500, detail="internal error") from None
    return wrapped


def stream_events(events: Iterable[dict]) -> StreamingResponse:
    def generate():
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def raise_api_error(exc: Exception) -> None:
    """Translate service-layer errors using the established v2 contract."""
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, issues_svc.ConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc
