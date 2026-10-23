#!/usr/bin/env python3
"""Match anonymous clinic logs and recover their shared service capacity."""

from __future__ import annotations

import itertools
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
    if (
        not label(system_id)
        or not isinstance(ceiling, list)
        or len(ceiling) != 2
        or any(not integer(value, 1, 1_000_000_000) for value in ceiling)
    ):
        raise Refusal("BAD_INPUT")
    profiles = payload["profiles"]
    batches = payload["batches"]
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise Refusal("BAD_INPUT")
    if not isinstance(batches, list) or len(batches) != len(profiles) + 2:
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
        if not isinstance(arrivals, list) or not 12 <= len(arrivals) <= 32:
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
        if not isinstance(minutes, list) or not 2 <= len(minutes) <= 3:
            raise Refusal("BAD_INPUT")
        previous = 0
        for minute in minutes:
            if not integer(minute, 1, 32) or minute <= previous:
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
    return system_id, tuple(ceiling), clean_profiles, clean_batches


def capacity_at(minute, capacities, switch_minute):
    return capacities[0] if minute < switch_minute else capacities[1]


def service_prefix(capacities, switch_minute, minute):
    early_minutes = min(minute, switch_minute - 1)
    late_minutes = max(0, minute - early_minutes)
    return early_minutes * capacities[0] + late_minutes * capacities[1]


def simulate(initial, arrivals, capacities, switch_minute):
    backlog = initial
    series = []
    for minute, arrival in enumerate(arrivals, 1):
        backlog = max(
            0,
            backlog + arrival - capacity_at(minute, capacities, switch_minute),
        )
        series.append(backlog)
    return series


def minimum_initial(arrivals, capacities, switch_minute, first_minute, target):
    intervals = [(target, target)]
    for minute in range(first_minute, 0, -1):
        delta = (
            arrivals[minute - 1]
            - capacity_at(minute, capacities, switch_minute)
        )
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


def solution_for(profile, batch, capacities, switch_minute):
    arrivals = profile["arrivals_per_min"]
    minutes = batch["sampled_minutes"]
    observed = batch["checkpoint_backlogs"]
    matches = []
    for assignment in sorted(set(itertools.permutations(observed))):
        initial = minimum_initial(
            arrivals,
            capacities,
            switch_minute,
            minutes[0],
            assignment[0],
        )
        if initial is None:
            continue
        series = simulate(initial, arrivals, capacities, switch_minute)
        if series[-1] != batch["closing_backlog"]:
            continue
        if [series[minute - 1] for minute in minutes] != list(assignment):
            continue
        pairs = [
            {"minute": minute, "backlog": series[minute - 1]}
            for minute in minutes
        ]
        matches.append((initial, pairs, series))
    return matches[0] if matches else None


def equation_capacities(equations, ceiling):
    partial = set()
    for first_index, (a, b, rhs) in enumerate(equations):
        if a == 0 and b and rhs % b == 0:
            late = rhs // b
            if 1 <= late <= ceiling[1]:
                partial.add((None, late))
        if b == 0 and a and rhs % a == 0:
            early = rhs // a
            if 1 <= early <= ceiling[0]:
                partial.add((early, None))
        for c, d, other_rhs in equations[first_index + 1 :]:
            determinant = a * d - b * c
            if determinant == 0:
                continue
            early_numerator = rhs * d - b * other_rhs
            late_numerator = a * other_rhs - rhs * c
            if early_numerator % determinant or late_numerator % determinant:
                continue
            early = early_numerator // determinant
            late = late_numerator // determinant
            if 1 <= early <= ceiling[0] and 1 <= late <= ceiling[1]:
                partial.add((early, late))
    completed = set()
    for early, late in partial:
        if early is not None and late is not None:
            completed.add((early, late))
            continue
        for a, b, rhs in equations:
            if early is None and a and not b:
                if rhs % a == 0:
                    candidate = rhs // a
                    if 1 <= candidate <= ceiling[0]:
                        completed.add((candidate, late))
            if late is None and b and not a:
                if rhs % b == 0:
                    candidate = rhs // b
                    if 1 <= candidate <= ceiling[1]:
                        completed.add((early, candidate))
    return {
        (early, late)
        for early, late in completed
        if all(a * early + b * late == rhs for a, b, rhs in equations)
    }


def candidates_for_pair(profile, batch, ceiling):
    arrivals = profile["arrivals_per_min"]
    minutes = batch["sampled_minutes"]
    observed = batch["checkpoint_backlogs"]
    length = len(arrivals)
    closing = batch["closing_backlog"]
    if minutes[-1] > length:
        return set()
    prefixes = [0]
    for arrival in arrivals:
        prefixes.append(prefixes[-1] + arrival)
    triples = set()
    for switch_minute in range(1, length + 1):
        for assignment in set(itertools.permutations(observed)):
            by_minute = dict(zip(minutes, assignment))
            equations = []
            for left, right in zip(minutes, minutes[1:]):
                span = right - left
                early = max(0, min(right, switch_minute - 1) - left)
                late = span - early
                required = (
                    prefixes[right] - prefixes[left]
                    - (by_minute[right] - by_minute[left])
                )
                equations.append((early, late, required))
            last = minutes[-1]
            early = max(0, min(length, switch_minute - 1) - last)
            late = length - last - early
            required = (
                prefixes[length]
                - prefixes[last]
                + by_minute[last]
                - closing
            )
            equations.append((early, late, required))
            for early_capacity, late_capacity in equation_capacities(
                equations,
                ceiling,
            ):
                triples.add(
                    (early_capacity, late_capacity, switch_minute)
                )
    return triples


def unique_assignment(adjacency):
    """Count injective assignments to distinct batches, stopping at two."""
    results = []

    def search(row, used, chosen):
        if len(results) >= 2:
            return
        if row == len(adjacency):
            results.append(tuple(chosen))
            return
        for batch_index in sorted(adjacency[row]):
            if batch_index in used:
                continue
            chosen.append(batch_index)
            search(row + 1, used | {batch_index}, chosen)
            chosen.pop()

    search(0, set(), [])
    return len(results), results[0] if len(results) == 1 else None


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
            for capacities_for_pair in pair_capacities:
                early, late, switch_minute = capacities_for_pair
                solution = solution_for(
                    profile,
                    batch,
                    (early, late),
                    switch_minute,
                )
                if solution is not None:
                    candidate_solutions[
                        (profile_index, batch_index, capacities_for_pair)
                    ] = solution

    for capacities_for_pair in sorted(capacities):
        adjacency = []
        for profile_index in range(len(ordered_profiles)):
            row = []
            for batch_index in range(len(batches)):
                if (
                    profile_index,
                    batch_index,
                    capacities_for_pair,
                ) in candidate_solutions:
                    row.append(batch_index)
            adjacency.append(row)
        count, matching = unique_assignment(adjacency)
        if count > 1:
            raise Refusal("BAD_MODEL")
        if matching is None:
            continue
        assignments = []
        for profile_index, profile in enumerate(ordered_profiles):
            batch_index = matching[profile_index]
            initial, pairs, series = candidate_solutions[
                (profile_index, batch_index, capacities_for_pair)
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
            "capacity_regimes": {
                "early": capacities_for_pair[0],
                "late": capacities_for_pair[1],
                "switch_minute": capacities_for_pair[2],
            },
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
