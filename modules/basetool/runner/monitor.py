from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from typing import List


class DisplayTools:
    @staticmethod
    def humanbytes(value: int, binary: bool = False, precision: int = 2) -> str:
        from math import log2, trunc

        multiples = ["B", "k{}B", "M{}B", "G{}B", "T{}B", "P{}B", "E{}B", "Z{}B", "Y{}B"]
        if value > 0:
            base = 1024 if binary else 1000
            multiple = trunc(log2(value) / log2(base))
            multiple = min(multiple, len(multiples) - 1)
            scaled = value / pow(base, multiple)
            suffix = multiples[multiple].format("i" if binary else "")
            return f"{scaled:.{precision}f} {suffix}"
        return "-- B"

    @staticmethod
    def humanformat(num: int, precision: int = 2) -> str:
        suffixes = ["", "k", "m", "g", "t", "p"]
        if num > 999:
            magnitude = sum(abs(num / 1000.0 ** x) >= 1 for x in range(1, len(suffixes)))
            return f"{num / 1000.0 ** magnitude:.{precision}f}{suffixes[magnitude]}"
        return str(num)


def _emit_json_tick(delta_req: int, delta_bytes: int, snapshot: dict) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pps": delta_req,
        "bps": delta_bytes,
        "targets": {
            target: {"req": values[0], "bytes": values[1]}
            for target, values in snapshot.items()
        },
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def monitor_loop(
    stop_event: threading.Event,
    target_stats: dict,
    stats_lock: threading.Lock,
    target_keys: List[str],
    table_height: int,
    json_output: bool = False,
) -> None:
    prev_total_req = 0
    prev_total_bytes = 0
    data_lines = max(1, table_height - 4)

    while stop_event.is_set():
        with stats_lock:
            snapshot = {key: val.copy() for key, val in target_stats.items()}

        sorted_targets = sorted(snapshot.items(), key=lambda item: item[1][0], reverse=True)
        total_req = sum(val[0] for val in snapshot.values())
        total_bytes = sum(val[1] for val in snapshot.values())
        delta_req = total_req - prev_total_req
        delta_bytes = total_bytes - prev_total_bytes
        prev_total_req = total_req
        prev_total_bytes = total_bytes

        lines = [
            f"PPS: {DisplayTools.humanformat(delta_req)} | BPS: {DisplayTools.humanbytes(delta_bytes)}",
            "-" * 57,
            f"{'Target':<30} {'Requests':>10} {'Bytes':>15}",
            "-" * 57,
        ]
        for index in range(data_lines):
            if index < len(sorted_targets):
                target, (req, byte_count) = sorted_targets[index]
                lines.append(
                    f"{target:<30} {req:>10} {DisplayTools.humanbytes(byte_count):>15}"
                )
            else:
                lines.append("")

        display_stream = sys.stderr if json_output else sys.stdout
        display_stream.write("\033[s")
        for index in range(table_height):
            text = lines[index] if index < len(lines) else ""
            display_stream.write(f"\033[{index + 1};1H\033[K{text}")
        display_stream.write("\033[u")
        display_stream.flush()
        if json_output:
            _emit_json_tick(delta_req, delta_bytes, snapshot)
        time.sleep(1)

    cleanup_stream = sys.stderr if json_output else sys.stdout
    cleanup_stream.write("\033[s")
    for index in range(1, table_height + 1):
        cleanup_stream.write(f"\033[{index};1H\033[K")
    cleanup_stream.write("\033[u")
    cleanup_stream.flush()
