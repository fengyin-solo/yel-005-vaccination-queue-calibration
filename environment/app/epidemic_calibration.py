#!/usr/bin/env python3
"""Preliminary clinic queue report assembled from the exported curves."""

import json
import sys
from pathlib import Path


def simulate(arrivals, capacities, switch_minute, initial=0):
    backlog = initial
    series = []
    for minute, arrival in enumerate(arrivals, 1):
        capacity = capacities[0] if minute < switch_minute else capacities[1]
        backlog = max(0, backlog + arrival - capacity)
        series.append(backlog)
    return series


def minimum_initial(arrivals, capacities, switch_minute, first_minute, target):
    """Smallest opening backlog that can reach target at first_minute."""
    intervals = [(target, target)]
    for minute in range(first_minute, 0, -1):
        capacity = capacities[0] if minute < switch_minute else capacities[1]
        delta = arrivals[minute - 1] - capacity
        previous = []
        for low, high in intervals:
            positive_low = max(1, low)
            if positive_low <= high:
                predecessor_low = max(0, positive_low - delta)
                predecessor_high = high - delta
                if predecessor_high >= predecessor_low:
                    previous.append((predecessor_low, predecessor_high))
            if low <= 0 <= high and -delta >= 0:
                previous.append((0, -delta))
        if not previous:
            return None
        previous.sort()
        merged = []
        for low, high in previous:
            if merged and low <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))
        intervals = merged
    return intervals[0][0]


def unique_matching(adjacency):
    """Count perfect matchings, stopping at two; return one when unique."""
    count = len(adjacency)
    ways = [0] * (1 << count)
    choice = [None] * (1 << count)
    predecessor = [None] * (1 << count)
    ways[0] = 1
    for mask in range(1 << count):
        row = mask.bit_count()
        if ways[mask] == 0 or row == count:
            continue
        for batch_index in adjacency[row]:
            bit = 1 << batch_index
            if mask & bit:
                continue
            target = mask | bit
            ways[target] = min(2, ways[target] + ways[mask])
            if choice[target] is None:
                choice[target] = batch_index
                predecessor[target] = mask
    full = (1 << count) - 1
    if ways[full] != 1:
        return ways[full], None
    matching = [0] * count
    mask = full
    for row in range(count - 1, -1, -1):
        chosen = choice[mask]
        matching[row] = chosen
        mask = predecessor[mask]
    return 1, matching


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
        backlog_series = simulate(
            arrivals,
            (capacity, capacity),
            len(arrivals) + 1,
        )
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
        "capacity_regimes": {
            "early": capacity,
            "late": capacity,
            "switch_minute": len(profiles[0]["arrivals_per_min"]) + 1,
        },
        "assignments": assignments,
    }
    target.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
