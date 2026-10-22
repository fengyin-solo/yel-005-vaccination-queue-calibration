#!/usr/bin/env python3
"""Preliminary clinic queue report assembled from the exported curves."""

import json
import sys
from pathlib import Path


def simulate(arrivals, capacity):
    backlog = 0
    series = []
    for arrival in arrivals:
        backlog = max(0, backlog + arrival - capacity)
        series.append(backlog)
    return series


def main(argv):
    if len(argv) != 3:
        return 1
    source, target = Path(argv[1]), Path(argv[2])
    payload = json.loads(source.read_text(encoding="utf-8"))
    profiles = sorted(payload["profiles"], key=lambda item: item["profile_id"])
    batches = payload["batches"]
    all_arrivals = [
        value
        for profile in profiles
        for value in profile["arrivals_per_min"]
    ]
    capacity = max(1, round(sum(all_arrivals) / len(all_arrivals)))
    assignments = []
    for profile, batch in zip(profiles, batches):
        arrivals = profile["arrivals_per_min"]
        backlog_series = simulate(arrivals, capacity)
        assignments.append(
            {
                "profile_id": profile["profile_id"],
                "batch_id": batch["batch_id"],
                "initial_backlog": 0,
                "checkpoint_pairs": [
                    {"minute": minute, "backlog": backlog_series[minute - 1]}
                    for minute in batch["sampled_minutes"]
                ],
                "final_backlog": backlog_series[-1],
                "peak_backlog": max(backlog_series),
                "backlog_series": backlog_series,
            }
        )
    result = {
        "system_id": payload["system_id"],
        "status": "calibrated",
        "capacity_per_min": capacity,
        "assignments": assignments,
    }
    target.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
