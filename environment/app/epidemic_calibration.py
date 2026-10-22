#!/usr/bin/env python3
"""Legacy capacity estimator based on average arrivals."""

import json
import sys
from pathlib import Path


def main(argv):
    if len(argv) != 3:
        return 1
    source, target = Path(argv[1]), Path(argv[2])
    payload = json.loads(source.read_text(encoding="utf-8"))
    arrivals = payload["arrivals_per_min"]
    capacity = max(1, round(sum(arrivals) / len(arrivals)))
    backlog = 0
    series = []
    for arrival in arrivals:
        backlog = max(0, backlog + arrival - capacity)
        series.append(backlog)
    result = {
        "system_id": payload["system_id"],
        "status": "calibrated",
        "capacity_per_min": capacity,
        "final_backlog": backlog,
        "peak_backlog": max(series),
        "backlog_series": series,
    }
    target.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
