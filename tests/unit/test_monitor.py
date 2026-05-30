from __future__ import annotations

import io
import threading
from unittest.mock import patch

from freezegun import freeze_time

from modules.basetool.runner.monitor import monitor_loop


@freeze_time("2026-05-24 12:00:00")
def test_monitor_loop_renders_sorted_targets_and_deltas():
    stop = threading.Event()
    stop.set()
    stats = {
        "alpha.example": [10, 1000],
        "beta.example": [20, 2000],
    }
    lock = threading.Lock()
    buffer = io.StringIO()

    with patch("modules.basetool.runner.monitor.sys.stdout", buffer):
        with patch("modules.basetool.runner.monitor.time.sleep", lambda _seconds: stop.clear()):
            monitor_loop(stop, stats, lock, list(stats.keys()), table_height=6)

    output = buffer.getvalue()
    assert "PPS:" in output
    assert "beta.example" in output
    assert "alpha.example" in output
    assert output.index("beta.example") < output.index("alpha.example")


@freeze_time("2026-05-24 12:00:00")
def test_monitor_loop_emits_json_when_enabled():
    stop = threading.Event()
    stop.set()
    stats = {"127.0.0.1:8081": [100, 5000]}
    lock = threading.Lock()
    buffer = io.StringIO()

    with patch("modules.basetool.runner.monitor.sys.stdout", buffer):
        with patch("modules.basetool.runner.monitor.time.sleep", lambda _seconds: stop.clear()):
            with patch("modules.basetool.runner.monitor._emit_json_tick") as emit_json:
                monitor_loop(stop, stats, lock, list(stats.keys()), table_height=6, json_output=True)

    emit_json.assert_called_once_with(100, 5000, {"127.0.0.1:8081": [100, 5000]})
