"""Acceptance tests for anonymous clinic-log calibration."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


ENTRY = os.environ.get("QUEUE_CALIBRATION_ENTRY", "/app/epidemic_calibration.py")
TRUSTED_PYTHON = Path("/usr/local/bin/python3")
SETPRIV = Path("/usr/bin/setpriv")


def candidate_argv(*arguments):
    values = [str(value) for value in arguments]
    if SETPRIV.is_file() and TRUSTED_PYTHON.is_file():
        return [
            str(SETPRIV),
            "--reuid=65534",
            "--regid=65534",
            "--clear-groups",
            str(TRUSTED_PYTHON),
            "-I",
            "-B",
            *values,
        ]
    return [sys.executable, "-I", "-B", *values]


def expose_to_candidate(path):
    if not SETPRIV.is_file():
        return
    path.chmod(0o777)
    for parent in path.parents:
        if parent == Path("/tmp"):
            break
        parent.chmod(parent.stat().st_mode | 0o001)


def invoke_candidate(path, *arguments, timeout=30):
    expose_to_candidate(Path(path))
    return subprocess.run(
        candidate_argv(*arguments),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
CAPACITY = (17, 23)
SWITCH = 7
SAMPLES = [2, 5, 11]
PROFILES = [
    ("WEST-17", [3, 7, 5, 11, 9, 4, 13, 6, 8, 12, 4, 3]),
    ("EAST-04", [5, 8, 3, 9, 12, 4, 10, 6, 7, 5, 8, 11]),
]
INITIALS = [200, 250]
BATCH_ORDER = [1, 0]
BATCH_IDS = ["B-731", "B-204"]
DECOY_BATCH_IDS = ["B-900", "B-901"]


def series(arrivals, capacity, initial, switch_minute=SWITCH):
    backlog = initial
    output = []
    for minute, arrival in enumerate(arrivals, 1):
        service = capacity[0] if minute < switch_minute else capacity[1]
        backlog = max(0, backlog + arrival - service)
        output.append(backlog)
    return output


def rendered_case(
    profiles=None,
    initials=None,
    order=None,
    capacity=CAPACITY,
    switch_minute=SWITCH,
    samples=None,
    samples_by_profile=None,
):
    if profiles is None:
        profiles = PROFILES
    if initials is None:
        initials = INITIALS
    if order is None:
        order = BATCH_ORDER
    if samples is None:
        samples = SAMPLES
    generated = []
    for arrivals in profiles:
        generated.append(
            series(
                arrivals[1],
                capacity,
                initials[len(generated)],
                switch_minute,
            )
        )
    batches = []
    for position, profile_index in enumerate(order):
        selected_samples = (
            samples_by_profile[profile_index]
            if samples_by_profile is not None
            else samples
        )
        values = [
            generated[profile_index][minute - 1]
            for minute in selected_samples
        ]
        permutation = (
            [2, 4, 0, 5, 1, 3]
            if len(selected_samples) == 6
            else ([2, 0, 3, 1] if len(selected_samples) == 4 else [2, 0, 1])
        )
        batches.append(
            {
                "batch_id": (
                    BATCH_IDS[position]
                    if position < len(BATCH_IDS)
                    else f"B-{400 + position * 13:03d}"
                ),
                "sampled_minutes": list(selected_samples),
                "checkpoint_backlogs": [values[index] for index in permutation],
                "closing_backlog": generated[profile_index][-1],
            }
        )
    for position, batch_id in enumerate(DECOY_BATCH_IDS):
        batches.append(
            {
                "batch_id": batch_id,
                "sampled_minutes": [2, 5, 11],
                "checkpoint_backlogs": [
                    900_000_000 - position,
                    800_000_000 - position,
                    700_000_000 - position,
                ],
                "closing_backlog": 500_000_000 - position,
            }
        )
    batches = [batches[0], batches[2], batches[1], batches[3]]
    return {
        "system_id": "MOBILE-CHECK-23",
        "capacity_ceiling": list(capacity),
        "profiles": [
            {"profile_id": identifier, "arrivals_per_min": list(arrivals)}
            for identifier, arrivals in profiles
        ],
        "batches": batches,
    }


def test_maximum_system_label_accepts_visible_space(tmp_path):
    """题面：「馆点交接编号写入 `system_id`：至少两个、至多四十八个可见符号，比较时不得带首尾空白，也不得夹入控制码。」"""
    payload = rendered_case()
    payload["system_id"] = "S" * 23 + " " + "T" * 24
    result, _, report = invoke(tmp_path, payload)
    assert result.returncode == 0
    assert report.read_text(encoding="utf-8").splitlines()[0].startswith(
        '{"system_id":"' + payload["system_id"] + '"'
    )


def write(path, document, raw=None):
    if raw is None:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_bytes(raw.encode("utf-8"))
    return path


def invoke(tmp_path, document, raw=None, name="result", timeout=30):
    source = write(tmp_path / "clinic.json", document, raw=raw)
    output = tmp_path / f"{name}.json"
    result = invoke_candidate(tmp_path, ENTRY, source, output, timeout=timeout)
    return result, source, output


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def assignment(document, profile_id):
    return next(
        item for item in document["assignments"] if item["profile_id"] == profile_id
    )


def test_success_is_quiet(tmp_path):
    """题面：「对账正常结束时，标准输出与标准错误都保持空白。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == "" and output.is_file()


def test_joint_assignment_recovers_capacity(tmp_path):
    """题面：「要在所有站点与日志之间求一个完整的一一配对。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0 and document["capacity_regimes"] == {
        "early": CAPACITY[0],
        "late": CAPACITY[1],
        "switch_minute": SWITCH,
    }
    assert [item["profile_id"] for item in document["assignments"]] == sorted(
        identifier for identifier, _ in PROFILES
    )
    assert [item["batch_id"] for item in document["assignments"]] == [
        "B-731",
        "B-204",
    ]


def test_smallest_feasible_capacity_is_selected(tmp_path):
    """题面：「排序后的第一项就是选定候选。」"""
    result, _, output = invoke(tmp_path, rendered_case(capacity=CAPACITY))
    document = read(output)
    assert result.returncode == 0
    assert document["capacity_regimes"]["early"] == CAPACITY[0]
    assert document["capacity_regimes"]["late"] == CAPACITY[1]


def test_candidate_tuple_uses_lexicographic_tiebreak(tmp_path):
    """题面：「排序后的第一项就是选定候选。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert document["capacity_regimes"] == {
        "early": CAPACITY[0],
        "late": CAPACITY[1],
        "switch_minute": SWITCH,
    }


def test_initial_backlog_is_recovered(tmp_path):
    """题面：「同一站点与日志出现多个可重现全部观测的开门积压时，取最小的非负整数。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert [item["initial_backlog"] for item in document["assignments"]] == [
        250,
        200,
    ]


def test_checkpoint_values_are_reassociated(tmp_path):
    """题面：「批次中的积压值没有保留与采样分钟的对应，必须从整批观测反推。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    expected = series(PROFILES[0][1], CAPACITY, INITIALS[0], SWITCH)
    assert result.returncode == 0
    assert assignment(document, "WEST-17")["checkpoint_pairs"] == [
        {"minute": minute, "backlog": expected[minute - 1]} for minute in SAMPLES
    ]


def test_backlog_series_follows_recurrence(tmp_path):
    """题面：「积压按整人递推」。"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    item = assignment(document, "EAST-04")
    assert result.returncode == 0
    assert item["backlog_series"] == series(
        PROFILES[1][1], CAPACITY, INITIALS[1], SWITCH
    )


def test_final_and_peak_match_series(tmp_path):
    """题面：「末次采样 `m` 到曲线末尾同样满足：」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert all(
        item["final_backlog"] == item["backlog_series"][-1]
        and item["peak_backlog"] == max(item["backlog_series"])
        for item in document["assignments"]
    )


def test_capacity_ceiling_boundary(tmp_path):
    """题面：「`capacity_ceiling` 是恰好两项的整数数组，依次约束早段和晚段服务能力。」"""
    accepted_document = rendered_case(capacity=CAPACITY)
    accepted_document["capacity_ceiling"] = list(CAPACITY)
    accepted, _, accepted_output = invoke(
        tmp_path, accepted_document, name="at-limit"
    )
    assert accepted.returncode == 0
    assert read(accepted_output)["capacity_regimes"]["switch_minute"] == SWITCH


def test_capacity_ceiling_lower_bound_is_not_bad_input(tmp_path):
    """题面：「`capacity_ceiling` 是 1 到 1000000000 的整数」。"""
    payload = rendered_case()
    payload["capacity_ceiling"] = [1, 1]
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: NO_CAPACITY\n" and not output.exists()


def test_capacity_ceiling_below_limit_is_refused(tmp_path):
    """题面：「当前 `capacity_ceiling` 内找不到完整全局配对时」。"""
    rejected_document = rendered_case(capacity=CAPACITY)
    rejected_document["capacity_ceiling"] = [CAPACITY[0], CAPACITY[1] - 1]
    rejected, _, rejected_output = invoke(
        tmp_path, rejected_document, name="below-limit"
    )
    assert rejected.returncode == 1
    assert rejected.stderr == "运行拒收: NO_CAPACITY\n" and not rejected_output.exists()


def test_top_level_keys_are_exact(tmp_path):
    """题面：「顶层四个键必须不多不少：」"""
    payload = rendered_case()
    payload["operator"] = "night"
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_profile_keys_are_exact(tmp_path):
    """题面：「`profiles` 恰好有 2 项，描述没有排队日志的到诊曲线。每项只含：」"""
    payload = rendered_case()
    payload["profiles"][0]["clinic"] = "west"
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_profile_count_is_exact(tmp_path):
    """题面：「`profiles` 恰好有 2 项，描述没有排队日志的到诊曲线。」"""
    payload = rendered_case()
    payload["profiles"].pop()
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()
    payload = rendered_case()
    payload["profiles"].pop()
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_batch_keys_are_exact(tmp_path):
    """题面：「每项是失去站点名的日志，只含」四个字段。"""
    payload = rendered_case()
    payload["batches"][0]["station"] = "guessed"
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_missing_required_key_is_refused(tmp_path):
    # CASE BHV-012 BHV-036
    """题面：「字段规则不合规时同样输出这一行。」"""
    payload = rendered_case()
    payload.pop("batches")
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_duplicate_json_key_is_refused(tmp_path):
    # CASE BHV-013 BHV-035
    """题面：「材料解码失败时，标准错误输出 `运行拒收: BAD_INPUT`。」"""
    raw = (
        '{"system_id":"A1","capacity_ceiling":2,"profiles":[],'
        '"batches":[],"capacity_ceiling":3}'
    )
    result, _, output = invoke(tmp_path, None, raw=raw)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_batches_must_include_two_decoys(tmp_path):
    """题面：「`batches` 恰好比 `profiles` 多两条」。"""
    payload = rendered_case()
    payload["batches"].pop()
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()
    payload = rendered_case()
    payload["batches"].append(dict(payload["batches"][-1]))
    payload["batches"][-1]["batch_id"] = "B-902"
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_official_log_subset_is_selected(tmp_path):
    """题面：「四份日志里只有两份来自当日正式站点，另外两份是测试台留下的相似波形。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert [item["batch_id"] for item in document["assignments"]] == ["B-731", "B-204"]


def test_official_logs_and_sites_are_jointly_assigned(tmp_path):
    """题面：「正式日志的选择和站点配对要由整批观测一次确定。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert {
        (item["profile_id"], item["batch_id"])
        for item in document["assignments"]
    } == {("WEST-17", "B-204"), ("EAST-04", "B-731")}


def test_duplicate_profile_identifier_is_refused(tmp_path):
    """题面：「`profile_id` 沿用标签规则且在同一数组中不可重复。」"""
    payload = rendered_case()
    payload["profiles"][1]["profile_id"] = payload["profiles"][0]["profile_id"]
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_too_short_arrival_series_is_refused(tmp_path):
    """题面：「到诊曲线的长度是十二至三十二个分钟」。"""
    payload = rendered_case()
    payload["profiles"][0]["arrivals_per_min"] = [1] * 11
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_mixed_profile_lengths_use_each_profile_own_bound(tmp_path):
    """题面：「到诊曲线的长度是十二至三十二个分钟」。"""
    short = [24, 21, 23, 22, 25, 24, 22, 23, 21, 25, 24, 22]
    long = [25, 21, 24, 23, 22, 25, 21, 24, 22, 23, 25, 21, 24, 22, 23, 21, 24]
    result, _, output = invoke(
        tmp_path,
        rendered_case(
            profiles=[("LONG-2", long), ("SHORT-1", short)],
            initials=[210, 260],
            order=[0, 1],
            samples_by_profile=[
                [2, 9, 14],
                [2, 5, 11],
            ],
        ),
    )
    assert result.returncode == 0, result.stderr
    document = read(output)
    assert len(assignment(document, "SHORT-1")["backlog_series"]) == 12
    assert len(assignment(document, "LONG-2")["backlog_series"]) == 17
    assert document["capacity_regimes"] == {
        "early": CAPACITY[0],
        "late": CAPACITY[1],
        "switch_minute": SWITCH,
    }


def test_boolean_arrival_is_refused(tmp_path):
    """题面：「布尔值不算整数。」"""
    payload = rendered_case()
    payload["profiles"][0]["arrivals_per_min"][0] = False
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_sampled_minutes_must_be_strictly_increasing(tmp_path):
    """题面：「这些位置必须严格递增。」"""
    payload = rendered_case()
    payload["batches"][0]["sampled_minutes"][1] = payload["batches"][0]["sampled_minutes"][0]
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_sampled_minutes_count_and_range_are_strict(tmp_path):
    """题面：「快照分钟从曲线中挑出二至三个位置。每个位置不低于 1，也不超过该站曲线长度。」"""
    too_few = rendered_case()
    too_few["batches"][0]["sampled_minutes"] = [2]
    too_few["batches"][0]["checkpoint_backlogs"] = [too_few["batches"][0]["checkpoint_backlogs"][0]]
    result, _, output = invoke(tmp_path, too_few)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()
    outside = rendered_case()
    outside["batches"][0]["sampled_minutes"][-1] = 65
    result, _, output = invoke(tmp_path, outside)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()
    too_many = rendered_case()
    too_many["batches"][0]["sampled_minutes"] = [2, 3, 4, 5, 6]
    too_many["batches"][0]["checkpoint_backlogs"] = [12, 13, 14, 15, 16]
    result, _, output = invoke(tmp_path, too_many)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_nonpositive_checkpoint_backlog_is_refused(tmp_path):
    """题面：「`checkpoint_backlogs` 与采样分钟等长，每项是 1 到 1000000000 的整数」。"""
    payload = rendered_case()
    payload["batches"][0]["checkpoint_backlogs"][0] = 0
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_ambiguous_pairing_is_refused(tmp_path):
    """题面：「若最小可行三元组下存在两种完整配对，按 `BAD_MODEL` 退出。」"""
    arrivals = [3, 7, 5, 11, 9, 4, 13, 6, 8, 12, 4, 3]
    payload = rendered_case(
        profiles=[("ALPHA-1", arrivals), ("BETA-2", arrivals)],
        initials=[200, 231],
        order=[0, 1],
    )
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: BAD_MODEL\n" and not output.exists()


def test_result_has_compact_fixed_order(tmp_path):
    """题面：「成功时写一行紧凑 JSON，末尾只保留一个 LF。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    raw = output.read_bytes()
    document = json.loads(raw)
    assert result.returncode == 0
    assert list(document) == [
        "system_id",
        "status",
        "capacity_regimes",
        "assignments",
    ]
    assert b": " not in raw and raw.endswith(b"\n") and raw.count(b"\n") == 1


def test_assignment_keys_are_exact(tmp_path):
    """题面：「`assignments` 按 `profile_id` 的 UTF-8 字节序排列；每项只有以下八个键，并保持所列顺序：」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert list(document["assignments"][0]) == [
        "profile_id",
        "batch_id",
        "initial_backlog",
        "checkpoint_pairs",
        "final_backlog",
        "peak_backlog",
        "backlog_series",
    ]


def test_backlog_series_are_integers(tmp_path):
    """题面：「`assignments` 按 `profile_id` 的 UTF-8 字节序排列；每项只有以下八个键，并保持所列顺序：」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    assert result.returncode == 0
    assert all(
        type(value) is int and value >= 0
        for value in document["assignments"][0]["backlog_series"]
    )


def test_checkpoint_pair_keys_are_exact(tmp_path):
    """题面：「`checkpoint_pairs` 按分钟升序排列，每项只有 `minute` 和 `backlog` 两个键。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    item = document["assignments"][0]
    assert result.returncode == 0
    assert list(item["checkpoint_pairs"][0]) == ["minute", "backlog"]


def test_checkpoint_minutes_are_ordered(tmp_path):
    """题面：「`checkpoint_pairs` 按分钟升序排列，每项只有 `minute` 和 `backlog` 两个键。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    document = read(output)
    item = document["assignments"][0]
    assert result.returncode == 0
    assert [pair["minute"] for pair in item["checkpoint_pairs"]] == SAMPLES


def test_absent_parent_is_io_failure(tmp_path):
    """题面：「如果报告目标位置无法使用」。"""
    source = write(tmp_path / "clinic.json", rendered_case())
    output = tmp_path / "absent-parent" / "result.json"
    result = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 1
    assert result.stderr == "运行拒收: IO_FAILURE\n" and not output.exists()


def test_bad_input_preserves_old_result(tmp_path):
    """题面：「以上路径都不得输出 Python `traceback`，也不得破坏已有报告。」"""
    result, source, output = invoke(tmp_path, rendered_case())
    before = output.read_bytes()
    payload = rendered_case()
    payload["capacity_ceiling"] = True
    write(source, payload)
    rejected = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 0 and rejected.returncode == 1
    assert output.read_bytes() == before
    assert "traceback" not in rejected.stderr.lower()


def test_publish_failure_preserves_old_report(tmp_path):
    """题面：「发布前先在同目录建立暂存项；替换失败时保留旧报告并清除暂存项。」"""
    directory = tmp_path / "locked"
    directory.mkdir()
    source = write(tmp_path / "clinic.json", rendered_case())
    output = directory / "result.json"
    output.write_bytes(b'{"previous":true}\n')
    before = output.read_bytes()
    directory.chmod(0o555)
    result = invoke_candidate(tmp_path, ENTRY, source, output)
    directory.chmod(0o755)
    assert result.returncode == 1
    assert output.read_bytes() == before


def test_publish_failure_leaves_no_temporary(tmp_path):
    """题面：「发布前先在同目录建立暂存项；替换失败时保留旧报告并清除暂存项。」"""
    directory = tmp_path / "locked"
    directory.mkdir()
    source = write(tmp_path / "clinic.json", rendered_case())
    report = directory / "result.json"
    report.write_bytes(b'{"previous":true}\n')
    directory.chmod(0o555)
    result = invoke_candidate(tmp_path, ENTRY, source, report)
    directory.chmod(0o755)
    assert result.returncode == 1
    assert report.read_bytes() == b'{"previous":true}\n'
    assert not list(directory.glob(".queue-*"))


def test_publish_success_leaves_no_temporary(tmp_path):
    """题面：「发布结束不得留下草稿。」"""
    result, _, output = invoke(tmp_path, rendered_case())
    assert result.returncode == 0 and output.is_file()
    assert not list(tmp_path.glob(".queue-*"))


def test_repeat_run_is_byte_identical(tmp_path):
    """题面：「使用同一份对账材料再次标定，报告应当逐字节不变。」"""
    result, source, output = invoke(tmp_path, rendered_case())
    first = output.read_bytes()
    second = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 0 and second.returncode == 0
    assert output.read_bytes() == first


def test_maximum_shape_runs_without_capacity_scan(tmp_path):
    """题面：「`profiles` 恰好有 2 项」「到诊曲线的长度是十二至三十二个分钟」。"""
    profiles = []
    initials = []
    for index in range(2):
        profiles.append(
            (
                f"SITE-{index:02d}",
                [(index * 17 + minute * 7) % 23 + 3 for minute in range(32)],
            )
        )
        initials.append(50000 + index * 111)
    payload = rendered_case(
        profiles=profiles,
        initials=initials,
        order=[1, 0],
        capacity=(53, 61),
        samples=[2, 5, 11],
    )
    payload["capacity_ceiling"] = [1_000_000_000, 1_000_000_000]
    started = time.monotonic()
    result, _, output = invoke(tmp_path, payload, timeout=25)
    elapsed = time.monotonic() - started
    assert result.returncode == 0 and read(output)["capacity_regimes"] == {
        "early": 53,
        "late": 61,
        "switch_minute": SWITCH,
    }
    assert elapsed < 20
