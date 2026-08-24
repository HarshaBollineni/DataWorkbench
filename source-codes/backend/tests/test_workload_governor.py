from __future__ import annotations

import threading
import time

from workload_governor import (
    artifact_write_slot,
    diagnostic_job_slot,
    optimizer_slot,
    workload_snapshot,
)


def test_diagnostic_workflows_are_queued_and_serialized():
    release = threading.Event()
    first_entered = threading.Event()
    second_entered = threading.Event()
    queued = []

    def first():
        with diagnostic_job_slot():
            first_entered.set()
            release.wait(timeout=2)

    def second():
        first_entered.wait(timeout=2)
        with diagnostic_job_slot(lambda position, capacity: queued.append((position, capacity))):
            second_entered.set()

    one = threading.Thread(target=first)
    two = threading.Thread(target=second)
    one.start(); two.start()
    assert first_entered.wait(timeout=2)
    deadline = time.monotonic() + 2
    while not queued and time.monotonic() < deadline:
        time.sleep(0.01)
    assert queued == [(2, 1)]
    assert not second_entered.is_set()
    release.set()
    one.join(timeout=2); two.join(timeout=2)
    assert second_entered.is_set()
    assert workload_snapshot()["diagnostic_jobs_active"] == 0


def test_optimizer_capacity_is_shared_across_callers():
    observed_active = []
    release = threading.Event()
    entered = threading.Event()

    def worker():
        with optimizer_slot():
            observed_active.append(workload_snapshot()["optimizer_active"])
            if len(observed_active) >= workload_snapshot()["optimizer_capacity"]:
                entered.set()
            release.wait(timeout=2)

    capacity = workload_snapshot()["optimizer_capacity"]
    threads = [threading.Thread(target=worker) for _ in range(capacity + 2)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=2)
    deadline = time.monotonic() + 2
    while workload_snapshot()["optimizer_waiting"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = workload_snapshot()
    assert snapshot["optimizer_active"] == capacity
    assert snapshot["optimizer_waiting"] == 2
    release.set()
    for thread in threads:
        thread.join(timeout=2)
    assert max(observed_active) <= capacity
    assert workload_snapshot()["optimizer_active"] == 0


def test_artifact_writes_are_serialized():
    release = threading.Event()
    first_entered = threading.Event()
    second_entered = threading.Event()

    def first():
        with artifact_write_slot():
            first_entered.set()
            release.wait(timeout=2)

    def second():
        first_entered.wait(timeout=2)
        with artifact_write_slot():
            second_entered.set()

    one = threading.Thread(target=first)
    two = threading.Thread(target=second)
    one.start(); two.start()
    assert first_entered.wait(timeout=2)
    time.sleep(0.05)
    assert not second_entered.is_set()
    assert workload_snapshot()["artifact_writers_waiting"] == 1
    release.set()
    one.join(timeout=2); two.join(timeout=2)
    assert second_entered.is_set()
    assert workload_snapshot()["artifact_writers_waiting"] == 0
