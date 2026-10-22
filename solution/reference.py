#!/usr/bin/env python3
"""Match anonymous clinic logs and recover their shared service capacity."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path


TOP_KEYS = {"system_id", "capacity_ceiling", "profiles", "batches"}
PROFILE_KEYS = {"profile_id", "arrivals_per_min"}
BATCH_KEYS = {"batch_id", "sampled_minutes", "checkpoint_backlogs", "closing_backlog"}


class Refusal(Exception):
    def __init__(self, kind):
        self.kind = kind


def fail(kind):
    sys.stderr.write(f"运行拒收: {kind}\n")
    raise SystemExit(1)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def label(value):
    return (
        isinstance(value, str)
        and 2 <= len(value) <= 48
        and value == value.strip()
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def pairs_hook(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("BAD_INPUT")
        result[key] = value
    return result


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != TOP_KEYS:
        raise Refusal("BAD_INPUT")
    system_id = payload["system_id"]
    ceiling = payload["capacity_ceiling"]
    if not label(system_id) or not integer(ceiling, 1, 1_000_000_000):
        raise Refusal("BAD_INPUT")
    profiles = payload["profiles"]
    batches = payload["batches"]
    if not isinstance(profiles, list) or not 2 <= len(profiles) <= 8:
        raise Refusal("BAD_INPUT")
    if not isinstance(batches, list) or len(batches) != len(profiles):
        raise Refusal("BAD_INPUT")

    profile_ids = set()
    clean_profiles = []
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != PROFILE_KEYS:
            raise Refusal("BAD_INPUT")
        identifier = profile["profile_id"]
        arrivals = profile["arrivals_per_min"]
        if not label(identifier) or identifier in profile_ids:
            raise Refusal("BAD_INPUT")
        profile_ids.add(identifier)
        if not isinstance(arrivals, list) or not 12 <= len(arrivals) <= 120:
            raise Refusal("BAD_INPUT")
        if any(not integer(value, 0, 1_000_000) for value in arrivals):
            raise Refusal("BAD_INPUT")
        clean_profiles.append(
            {"profile_id": identifier, "arrivals_per_min": list(arrivals)}
        )

    batch_ids = set()
    clean_batches = []
    for batch in batches:
        if not isinstance(batch, dict) or set(batch) != BATCH_KEYS:
            raise Refusal("BAD_INPUT")
        identifier = batch["batch_id"]
        minutes = batch["sampled_minutes"]
        backlogs = batch["checkpoint_backlogs"]
        closing = batch["closing_backlog"]
        if not label(identifier) or identifier in batch_ids:
            raise Refusal("BAD_INPUT")
        batch_ids.add(identifier)
        if not isinstance(minutes, list) or not 2 <= len(minutes) <= 6:
            raise Refusal("BAD_INPUT")
        previous = 0
        for minute in minutes:
            if not integer(minute, 1, len(arrivals)) or minute <= previous:
                raise Refusal("BAD_INPUT")
            previous = minute
        if not isinstance(backlogs, list) or len(backlogs) != len(minutes):
            raise Refusal("BAD_INPUT")
        if any(not integer(value, 1, 1_000_000_000) for value in backlogs):
            raise Refusal("BAD_INPUT")
        if not integer(closing, 0, 1_000_000_000):
            raise Refusal("BAD_INPUT")
        clean_batches.append(
            {
                "batch_id": identifier,
                "sampled_minutes": list(minutes),
                "checkpoint_backlogs": list(backlogs),
                "closing_backlog": closing,
            }
        )
    return system_id, ceiling, clean_profiles, clean_batches


def simulate(initial, arrivals, capacity):
    backlog = initial
    series = []
    for arrival in arrivals:
        backlog = max(0, backlog + arrival - capacity)
        series.append(backlog)
    return series


def solution_for(profile, batch, capacity):
    arrivals = profile["arrivals_per_min"]
    minutes = batch["sampled_minutes"]
    observed = batch["checkpoint_backlogs"]
    candidates = set()
    prefixes = []
    running = 0
    for arrival in arrivals:
        prefixes.append(running)
        running += arrival
    prefixes.append(running)
    for minute in minutes:
        for backlog in observed:
            candidates.add(backlog - prefixes[minute] + minute * capacity)
    matches = []
    for initial in sorted(value for value in candidates if 0 <= value <= 1_000_000_000):
        series = simulate(initial, arrivals, capacity)
        if series[-1] != batch["closing_backlog"]:
            continue
        sampled = [series[minute - 1] for minute in minutes]
        if sorted(sampled) != sorted(observed):
            continue
        pairs = [
            {"minute": minute, "backlog": series[minute - 1]}
            for minute in minutes
        ]
        matches.append((initial, pairs, series))
    return matches[0] if matches else None


def candidates_for_pair(profile, batch, ceiling):
    arrivals = profile["arrivals_per_min"]
    minutes = batch["sampled_minutes"]
    observed = batch["checkpoint_backlogs"]
    capacity_candidates = set()
    for left_index, left_minute in enumerate(minutes):
        for right_minute in minutes[left_index + 1 :]:
            span = right_minute - left_minute
            arrival_gap = sum(arrivals[left_minute:right_minute])
            for left_value in observed:
                for right_value in observed:
                    numerator = arrival_gap - (right_value - left_value)
                    if numerator > 0 and numerator % span == 0:
                        capacity = numerator // span
                        if capacity <= ceiling:
                            capacity_candidates.add(capacity)
    length = len(arrivals)
    closing = batch["closing_backlog"]
    for minute in minutes:
        span = length - minute
        if span <= 0:
            continue
        arrival_gap = sum(arrivals[minute:])
        for value in observed:
            numerator = arrival_gap - (closing - value)
            if numerator > 0 and numerator % span == 0:
                capacity = numerator // span
                if capacity <= ceiling:
                    capacity_candidates.add(capacity)
    return capacity_candidates


def unique_matching(adjacency):
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


def calibrate(system_id, ceiling, profiles, batches):
    ordered_profiles = sorted(
        profiles, key=lambda item: item["profile_id"].encode("utf-8")
    )
    candidate_solutions = {}
    capacities = set()
    for profile_index, profile in enumerate(ordered_profiles):
        for batch_index, batch in enumerate(batches):
            pair_capacities = candidates_for_pair(profile, batch, ceiling)
            capacities.update(pair_capacities)
            for capacity in pair_capacities:
                solution = solution_for(profile, batch, capacity)
                if solution is not None:
                    candidate_solutions[(profile_index, batch_index, capacity)] = solution

    for capacity in sorted(capacities):
        adjacency = []
        for profile_index in range(len(ordered_profiles)):
            row = []
            for batch_index in range(len(batches)):
                if (profile_index, batch_index, capacity) in candidate_solutions:
                    row.append(batch_index)
            adjacency.append(row)
        count, matching = unique_matching(adjacency)
        if count > 1:
            raise Refusal("BAD_MODEL")
        if matching is None:
            continue
        assignments = []
        for profile_index, profile in enumerate(ordered_profiles):
            batch_index = matching[profile_index]
            initial, pairs, series = candidate_solutions[
                (profile_index, batch_index, capacity)
            ]
            assignments.append(
                {
                    "profile_id": profile["profile_id"],
                    "batch_id": batches[batch_index]["batch_id"],
                    "initial_backlog": initial,
                    "checkpoint_pairs": pairs,
                    "final_backlog": series[-1],
                    "peak_backlog": max(series),
                    "backlog_series": series,
                }
            )
        return {
            "system_id": system_id,
            "status": "calibrated",
            "capacity_per_min": capacity,
            "assignments": assignments,
        }
    raise Refusal("NO_CAPACITY")


def publish(target, payload):
    if (
        not target.parent.is_dir()
        or target.is_dir()
        or not target.parent.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ):
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
        payload = json.loads(
            source.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook
        )
        result = calibrate(*validate(payload))
        encoded = (
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        publish(target, encoded)
        return 0
    except Refusal as exc:
        fail(exc.kind)
    except (OSError, UnicodeError, ValueError):
        fail("BAD_INPUT")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
