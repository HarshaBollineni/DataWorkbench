"""Process-owned execution with replayable Server-Sent Event subscriptions.

An HTTP response must never own a long-running diagnostic.  Browsers routinely
close and reconnect EventSource requests (navigation, sleep, proxy timeout).
This module consumes the work iterator on a dedicated server thread and lets
SSE responses observe its buffered events without controlling its lifetime.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Job:
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False
    condition: threading.Condition = field(default_factory=threading.Condition)


_JOBS: dict[str, _Job] = {}
_JOBS_GUARD = threading.Lock()


def _publish(job: _Job, event: dict[str, Any]) -> None:
    with job.condition:
        job.events.append(event)
        if event.get("phase") in {"done", "error"}:
            job.terminal = True
        job.condition.notify_all()


def _execute(job: _Job, event_factory: Callable[[], Iterator[dict[str, Any]]]) -> None:
    try:
        from workload_governor import diagnostic_job_slot

        def queued(position: int, capacity: int) -> None:
            _publish(job, {
                "phase": "queued",
                "queue_position": position,
                "thought": (
                    f"Queued behind {position - 1} active or earlier diagnostic "
                    f"workflow{'s' if position - 1 != 1 else ''}."
                ),
                "diagnostic_job_capacity": capacity,
            })

        with diagnostic_job_slot(queued):
            for event in event_factory():
                _publish(job, event)
    except Exception:
        # Route-specific factories should log and sanitize their own failures.
        # This guard prevents a programming error from leaving observers waiting
        # forever and deliberately exposes no exception detail.
        _publish(job, {"phase": "error", "thought": "internal error"})
    finally:
        with job.condition:
            if not job.terminal:
                job.events.append({"phase": "error", "thought": "Execution stopped before completion."})
                job.terminal = True
            job.condition.notify_all()


def observe_background_events(
    key: str,
    event_factory: Callable[[], Iterator[dict[str, Any]]],
    *,
    heartbeat_seconds: float = 10.0,
) -> Iterator[dict[str, Any]]:
    """Start-or-attach to server-owned work and replay events to this observer.

    Closing this iterator only removes an observer.  The execution thread keeps
    consuming ``event_factory`` through its terminal event.
    """
    with _JOBS_GUARD:
        job = _JOBS.get(key)
        if job is None:
            job = _Job()
            _JOBS[key] = job
            threading.Thread(
                target=_execute,
                args=(job, event_factory),
                name=f"background-stream-{key}",
                daemon=True,
            ).start()

    cursor = 0
    try:
        while True:
            with job.condition:
                if cursor >= len(job.events) and not job.terminal:
                    job.condition.wait(timeout=heartbeat_seconds)
                available = job.events[cursor:]
                cursor = len(job.events)
                terminal = job.terminal
            if not available and not terminal:
                # A data frame, rather than an SSE comment, works with the
                # existing JSON-only client and keeps proxies from timing out.
                from workload_governor import workload_snapshot
                resources = workload_snapshot()
                if resources["diagnostic_jobs_waiting"]:
                    thought = (
                        f"Waiting in the diagnostic queue "
                        f"({resources['diagnostic_jobs_waiting']} waiting)."
                    )
                elif resources["optimizer_waiting"]:
                    thought = (
                        f"Waiting for shared optimizer capacity "
                        f"({resources['optimizer_active']}/{resources['optimizer_capacity']} active)."
                    )
                elif resources["artifact_writers_waiting"]:
                    thought = "Waiting briefly for the ordered artifact writer."
                else:
                    thought = "Execution is active."
                yield {"phase": "heartbeat", "thought": thought, "resources": resources}
                continue
            yield from available
            if terminal and cursor >= len(job.events):
                return
    finally:
        # Retain a running job for future reconnects.  Terminal history only
        # needs to survive until an observer has received it; durable run state
        # and artifacts remain in the database/repository.
        if job.terminal:
            with _JOBS_GUARD:
                if _JOBS.get(key) is job:
                    _JOBS.pop(key, None)


def _reset_for_tests() -> None:
    """Clear completed broker state between isolated unit tests."""
    with _JOBS_GUARD:
        _JOBS.clear()
