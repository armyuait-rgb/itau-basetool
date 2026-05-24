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
