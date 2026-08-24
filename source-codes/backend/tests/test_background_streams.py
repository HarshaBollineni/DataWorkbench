from __future__ import annotations

import threading
import time

from background_streams import _reset_for_tests, observe_background_events


def setup_function():
    _reset_for_tests()


def test_disconnected_observer_does_not_cancel_execution():
    release = threading.Event()
    finished = threading.Event()
    starts = []

    def work():
        starts.append("started")
        yield {"phase": "start"}
        release.wait(timeout=2)
        yield {"phase": "feature_result", "preview": {"feature": "score", "iv": 0.42}}
        finished.set()
        yield {"phase": "done"}

    abandoned = observe_background_events("run:abandoned", work, heartbeat_seconds=0.01)
    assert next(abandoned)["phase"] == "start"
    abandoned.close()
    release.set()
    assert finished.wait(timeout=2)

    replay = list(observe_background_events("run:abandoned", work, heartbeat_seconds=0.01))
    assert starts == ["started"]
    assert [event["phase"] for event in replay] == ["start", "feature_result", "done"]
    assert replay[1]["preview"] == {"feature": "score", "iv": 0.42}


def test_duplicate_observers_attach_to_one_execution():
    release = threading.Event()
    starts = []

    def work():
        starts.append("started")
        yield {"phase": "start"}
        release.wait(timeout=2)
        yield {"phase": "done"}

    first = observe_background_events("run:shared", work, heartbeat_seconds=0.01)
    second = observe_background_events("run:shared", work, heartbeat_seconds=0.01)
    assert next(first)["phase"] == "start"
    assert next(second)["phase"] == "start"
    release.set()
    assert next(first)["phase"] == "done"
    assert next(second)["phase"] == "done"
    assert starts == ["started"]


def test_idle_execution_emits_observer_heartbeat():
    release = threading.Event()

    def work():
        yield {"phase": "start"}
        release.wait(timeout=2)
        yield {"phase": "done"}

    observer = observe_background_events("run:heartbeat", work, heartbeat_seconds=0.01)
    assert next(observer)["phase"] == "start"
    assert next(observer)["phase"] == "heartbeat"
    release.set()
    assert next(observer)["phase"] == "done"
