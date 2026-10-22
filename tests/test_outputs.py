"""Acceptance tests for clinic queue-capacity calibration."""

import json
import os
import subprocess
import sys

import pytest


ENTRY = os.environ.get("QUEUE_CALIBRATION_ENTRY", "/app/epidemic_calibration.py")
ARRIVALS = [3, 8, 4, 10, 7, 2, 9, 5, 6, 12, 4, 3]


def sample():
    return {
        "system_id": "CLINIC-N17",
        "initial_backlog": 0,
        "arrivals_per_min": list(ARRIVALS),
        "checkpoints": [
            {"minute": 2, "backlog": 2},
            {"minute": 5, "backlog": 5},
            {"minute": 10, "backlog": 9},
            {"minute": 12, "backlog": 4},
        ],
        "capacity_ceiling": 20,
    }


def write(path, document, raw=None):
    if raw is None:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_bytes(raw.encode("utf-8"))
    return path


def invoke(tmp_path, document, raw=None, name="result"):
    source = write(tmp_path / "queue.json", document, raw=raw)
    output = tmp_path / f"{name}.json"
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, source, output


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def expected_series(capacity):
    backlog = 0
    series = []
    for arrival in ARRIVALS:
        backlog = max(0, backlog + arrival - capacity)
        series.append(backlog)
    return series


def test_success_is_quiet(tmp_path):
    """题面：「入口接收现场材料与输出报告的位置参数。」"""
    result, _, output = invoke(tmp_path, sample())
    assert result.returncode == 0 and result.stdout == "" and result.stderr == "" and output.is_file()


def test_smallest_consistent_capacity_is_selected(tmp_path):
    """题面：「寻找满足全部 checkpoint 的最小正整数 `capacity`」。"""
    result, _, output = invoke(tmp_path, sample())
    assert result.returncode == 0 and read(output)["capacity_per_min"] == 6


def test_backlog_series_follows_recurrence(tmp_path):
    """题面：「积压按下面公式更新。」"""
    result, _, output = invoke(tmp_path, sample())
    assert result.returncode == 0 and read(output)["backlog_series"] == expected_series(6)


def test_final_and_peak_backlog_match_series(tmp_path):
    """题面：「用整条到诊序列继续模拟，并给出最终积压、过程峰值」。"""
    result, _, output = invoke(tmp_path, sample())
    document = read(output)
    assert result.returncode == 0
    assert document["final_backlog"] == document["backlog_series"][-1]
    assert document["peak_backlog"] == max(document["backlog_series"])


def test_top_level_keys_are_exact(tmp_path):
    """题面：「现场记录由 UTF-8 JSON 解码。」"""
    payload = sample()
    payload["operator"] = "night"
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_checkpoint_keys_are_exact(tmp_path):
    """题面：「每项只有 `minute` 和 `backlog`」。"""
    payload = sample()
    payload["checkpoints"][0]["queue"] = 2
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_duplicate_json_key_is_refused(tmp_path):
    """题面：「快照字段或取值无法按门诊口径解释时，命令以 1 结束并在标准错误仅写 `运行拒收: BAD_INPUT`。」"""
    raw = (
        '{"system_id":"A1","initial_backlog":0,'
        '"arrivals_per_min":[1,1,1,1,1,1,1,1,1,1,1,1],'
        '"checkpoints":[{"minute":1,"backlog":0}],"capacity_ceiling":2,'
        '"capacity_ceiling":3}'
    )
    result, _, output = invoke(tmp_path, None, raw=raw)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_too_short_arrival_series_is_refused(tmp_path):
    """题面：「`arrivals_per_min` 的长度介于 12 和 240 之间，每项是 0 到 100000 的整数。」"""
    payload = sample()
    payload["arrivals_per_min"] = ARRIVALS[:11]
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_system_identifier_is_strict(tmp_path):
    """题面：「`system_id` 只接受长度合规、无首尾空白或控制字符的标签。」"""
    payload = sample()
    payload["system_id"] = " leading"
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_boolean_arrival_is_refused(tmp_path):
    """题面：「布尔值不算整数。」"""
    payload = sample()
    payload["arrivals_per_min"][0] = False
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_checkpoints_must_be_strictly_increasing(tmp_path):
    """题面：「`minute` 是 1 到到诊序列长度的整数，并且各 checkpoint 严格递增」。"""
    payload = sample()
    payload["checkpoints"][1]["minute"] = payload["checkpoints"][0]["minute"]
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_negative_initial_backlog_is_refused(tmp_path):
    """题面：「`initial_backlog` 是 0 到 1000000 的整数。」"""
    payload = sample()
    payload["initial_backlog"] = -1
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_no_capacity_under_ceiling_is_refused(tmp_path):
    """题面：「在能力上限内不存在任何可行能力时」。"""
    payload = sample()
    payload["capacity_ceiling"] = 3
    result, _, output = invoke(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: NO_CAPACITY\n" and not output.exists()


def test_result_has_compact_fixed_order(tmp_path):
    """题面：「标定结果压成一行 JSON，文件最后只保留一个换行。」"""
    result, _, output = invoke(tmp_path, sample())
    raw = output.read_bytes()
    document = json.loads(raw)
    assert result.returncode == 0
    assert list(document) == [
        "system_id",
        "status",
        "capacity_per_min",
        "final_backlog",
        "peak_backlog",
        "backlog_series",
    ]
    assert b": " not in raw and raw.endswith(b"\n") and raw.count(b"\n") == 1


def test_absent_parent_is_io_failure(tmp_path):
    """题面：「标定报告没有可用的落盘位置时，退出码为 1，标准错误只保留 `运行拒收: IO_FAILURE`。」"""
    source = write(tmp_path / "queue.json", sample())
    output = tmp_path / "absent-parent" / "result.json"
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1 and result.stderr == "运行拒收: IO_FAILURE\n" and not output.exists()


def test_bad_input_preserves_old_result(tmp_path):
    """题面：「遇到上述错误时，终端不应出现 Python `traceback`。」"""
    result, source, output = invoke(tmp_path, sample())
    before = output.read_bytes()
    payload = sample()
    payload["capacity_ceiling"] = True
    write(source, payload)
    rejected = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and rejected.returncode == 1
    assert output.read_bytes() == before and "traceback" not in rejected.stderr.lower()


def test_repeat_run_is_byte_identical(tmp_path):
    """题面：「沿用原记录重跑时，生成报告应与上次完全同字节。」"""
    result, source, output = invoke(tmp_path, sample())
    first = output.read_bytes()
    second = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and second.returncode == 0 and output.read_bytes() == first
