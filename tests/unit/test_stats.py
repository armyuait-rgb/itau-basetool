from __future__ import annotations

import threading


def _increment(stats_dict, stats_lock, target_key, count):
    for _ in range(count):
        with stats_lock:
            entry = stats_dict.setdefault(target_key, [0, 0])
            entry[0] += 1
            entry[1] += 1


def test_concurrent_stats_no_lost_updates():
    stats = {}
    lock = threading.Lock()
    target = "127.0.0.1:8080"
    threads = [
        threading.Thread(target=_increment, args=(stats, lock, target, 1000))
        for _ in range(100)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert stats[target] == [100000, 100000]
