"""Shared capacity controls for expensive analytics and artifact persistence."""
from __future__ import annotations

import functools
import os
import threading
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar


def _configured_capacity() -> int:
    try:
        return max(1, min(int(os.environ.get("DWB_OPTIMIZER_CAPACITY", "4")), 16))
    except ValueError:
        return 4


def _configured_job_capacity() -> int:
    try:
        return max(1, min(int(os.environ.get("DWB_DIAGNOSTIC_JOB_CAPACITY", "1")), 8))
    except ValueError:
        return 1


_OPTIMIZER_CAPACITY = _configured_capacity()
_OPTIMIZER_ACTIVE = 0
_OPTIMIZER_QUEUE: deque[object] = deque()
_OPTIMIZER_CONDITION = threading.Condition()

_DIAGNOSTIC_JOB_CAPACITY = _configured_job_capacity()
_DIAGNOSTIC_JOBS_ACTIVE = 0
_DIAGNOSTIC_JOB_QUEUE: deque[object] = deque()
_DIAGNOSTIC_JOB_CONDITION = threading.Condition()

_ARTIFACT_WRITE_LOCK = threading.Lock()
_ARTIFACT_STATE_LOCK = threading.Lock()
_ARTIFACT_WRITER_ACTIVE = 0
_ARTIFACT_WRITERS_WAITING = 0


@contextmanager
def optimizer_slot() -> Iterator[None]:
    """Acquire one fair, process-wide IV/coarse-optimization slot."""
    global _OPTIMIZER_ACTIVE
    ticket = object()
    with _OPTIMIZER_CONDITION:
        _OPTIMIZER_QUEUE.append(ticket)
        while _OPTIMIZER_QUEUE[0] is not ticket or _OPTIMIZER_ACTIVE >= _OPTIMIZER_CAPACITY:
            _OPTIMIZER_CONDITION.wait()
        _OPTIMIZER_QUEUE.popleft()
        _OPTIMIZER_ACTIVE += 1
    try:
        yield
    finally:
        with _OPTIMIZER_CONDITION:
            _OPTIMIZER_ACTIVE -= 1
            _OPTIMIZER_CONDITION.notify_all()


@contextmanager
def diagnostic_job_slot(
    on_queued: Callable[[int, int], None] | None = None,
) -> Iterator[None]:
    """Admit long-running diagnostic workflows through one fair FIFO queue."""
    global _DIAGNOSTIC_JOBS_ACTIVE
    ticket = object()
    with _DIAGNOSTIC_JOB_CONDITION:
        _DIAGNOSTIC_JOB_QUEUE.append(ticket)
        must_wait = (
            _DIAGNOSTIC_JOB_QUEUE[0] is not ticket
            or _DIAGNOSTIC_JOBS_ACTIVE >= _DIAGNOSTIC_JOB_CAPACITY
        )
        if must_wait and on_queued:
            on_queued(
                _DIAGNOSTIC_JOBS_ACTIVE + len(_DIAGNOSTIC_JOB_QUEUE),
                _DIAGNOSTIC_JOB_CAPACITY,
            )
        while (
            _DIAGNOSTIC_JOB_QUEUE[0] is not ticket
            or _DIAGNOSTIC_JOBS_ACTIVE >= _DIAGNOSTIC_JOB_CAPACITY
        ):
            _DIAGNOSTIC_JOB_CONDITION.wait()
        _DIAGNOSTIC_JOB_QUEUE.popleft()
        _DIAGNOSTIC_JOBS_ACTIVE += 1
    try:
        yield
    finally:
        with _DIAGNOSTIC_JOB_CONDITION:
            _DIAGNOSTIC_JOBS_ACTIVE -= 1
            _DIAGNOSTIC_JOB_CONDITION.notify_all()


@contextmanager
def artifact_write_slot() -> Iterator[None]:
    """Serialize AAR file/catalogue commits to avoid SQLite writer contention."""
    global _ARTIFACT_WRITER_ACTIVE, _ARTIFACT_WRITERS_WAITING
    acquired = _ARTIFACT_WRITE_LOCK.acquire(blocking=False)
    if not acquired:
        with _ARTIFACT_STATE_LOCK:
            _ARTIFACT_WRITERS_WAITING += 1
        try:
            _ARTIFACT_WRITE_LOCK.acquire()
        finally:
            with _ARTIFACT_STATE_LOCK:
                _ARTIFACT_WRITERS_WAITING -= 1
    with _ARTIFACT_STATE_LOCK:
        _ARTIFACT_WRITER_ACTIVE = 1
    try:
        yield
    finally:
        with _ARTIFACT_STATE_LOCK:
            _ARTIFACT_WRITER_ACTIVE = 0
        _ARTIFACT_WRITE_LOCK.release()


F = TypeVar("F", bound=Callable[..., Any])


def governed_optimizer(function: F) -> F:
    """Decorate one feature-level optimizer operation."""
    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with optimizer_slot():
            return function(*args, **kwargs)
    return wrapped  # type: ignore[return-value]


def serialized_artifact_write(function: F) -> F:
    """Decorate an AAR mutation so its file and SQLite commit stay ordered."""
    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with artifact_write_slot():
            return function(*args, **kwargs)
    return wrapped  # type: ignore[return-value]


def workload_snapshot() -> dict[str, int]:
    """Return non-sensitive live capacity counters for progress heartbeats."""
    with _OPTIMIZER_CONDITION:
        optimizer = {
            "optimizer_capacity": _OPTIMIZER_CAPACITY,
            "optimizer_active": _OPTIMIZER_ACTIVE,
            "optimizer_waiting": len(_OPTIMIZER_QUEUE),
        }
    with _ARTIFACT_STATE_LOCK:
        persistence = {
            "artifact_writer_active": _ARTIFACT_WRITER_ACTIVE,
            "artifact_writers_waiting": _ARTIFACT_WRITERS_WAITING,
        }
    with _DIAGNOSTIC_JOB_CONDITION:
        diagnostics = {
            "diagnostic_job_capacity": _DIAGNOSTIC_JOB_CAPACITY,
            "diagnostic_jobs_active": _DIAGNOSTIC_JOBS_ACTIVE,
            "diagnostic_jobs_waiting": len(_DIAGNOSTIC_JOB_QUEUE),
        }
    return {**diagnostics, **optimizer, **persistence}
