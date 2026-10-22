#!/usr/bin/env python3
"""Queue-capacity inversion for a deterministic clinic simulation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


TOP = {"system_id", "initial_backlog", "arrivals_per_min", "checkpoints", "capacity_ceiling"}
CHECKPOINT = {"minute", "backlog"}


class Refusal(Exception):
    def __init__(self, kind):
        self.kind = kind


def fail(kind):
    sys.stderr.write(f"运行拒收: {kind}\n")
    raise SystemExit(1)


def integer(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def pairs_hook(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("BAD_INPUT")
        result[key] = value
    return result


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != TOP:
        raise Refusal("BAD_INPUT")
    system_id = payload["system_id"]
    if (
        not isinstance(system_id, str)
        or not 2 <= len(system_id) <= 48
        or system_id != system_id.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in system_id)
    ):
        raise Refusal("BAD_INPUT")
    initial = payload["initial_backlog"]
    arrivals = payload["arrivals_per_min"]
    ceiling = payload["capacity_ceiling"]
    if not integer(initial, 0, 1_000_000) or not integer(ceiling, 1, 100_000):
        raise Refusal("BAD_INPUT")
    if not isinstance(arrivals, list) or not 12 <= len(arrivals) <= 240:
        raise Refusal("BAD_INPUT")
    if any(not integer(value, 0, 100_000) for value in arrivals):
        raise Refusal("BAD_INPUT")
    checkpoints = payload["checkpoints"]
    if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 30:
        raise Refusal("BAD_INPUT")
    previous = 0
    cleaned = []
    for item in checkpoints:
        if not isinstance(item, dict) or set(item) != CHECKPOINT:
            raise Refusal("BAD_INPUT")
        minute = item["minute"]
        backlog = item["backlog"]
        if not integer(minute, 1, len(arrivals)) or not integer(backlog, 0, 1_000_000):
            raise Refusal("BAD_INPUT")
        if minute <= previous:
            raise Refusal("BAD_INPUT")
        previous = minute
        cleaned.append((minute, backlog))
    return system_id, initial, arrivals, cleaned, ceiling


def simulate(initial, arrivals, capacity, stop=None):
    backlog = initial
    series = []
    for arrival in arrivals[: stop or len(arrivals)]:
        backlog = max(0, backlog + arrival - capacity)
        series.append(backlog)
    return series


def calibrate(system_id, initial, arrivals, checkpoints, ceiling):
    for capacity in range(1, ceiling + 1):
        series = simulate(initial, arrivals, capacity)
        if all(series[minute - 1] == expected for minute, expected in checkpoints):
            return {
                "system_id": system_id,
                "status": "calibrated",
                "capacity_per_min": capacity,
                "final_backlog": series[-1],
                "peak_backlog": max(series),
                "backlog_series": series,
            }
    raise Refusal("NO_CAPACITY")


def publish(target, payload):
    if not target.parent.is_dir() or target.is_dir() or not os.access(target.parent, os.W_OK):
        raise Refusal("IO_FAILURE")
    old = target.read_bytes() if target.exists() else None
    stage = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".queue-", dir=target.parent)
        stage = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, target)
        stage = None
    except OSError:
        if old is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(old)
        raise Refusal("IO_FAILURE")
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)


def main(argv):
    if len(argv) != 3:
        fail("BAD_INPUT")
    source, target = Path(argv[1]), Path(argv[2])
    try:
        payload = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook)
        result = calibrate(*validate(payload))
        encoded = (json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        publish(target, encoded)
        return 0
    except Refusal as exc:
        fail(exc.kind)
    except (OSError, UnicodeError, ValueError):
        fail("BAD_INPUT")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
