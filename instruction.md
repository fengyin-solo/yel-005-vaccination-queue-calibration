# 接种门诊排队能力的反标定

门诊留下了几个排队快照。请修 `/app/epidemic_calibration.py`，用到诊序列和快照反推每分钟服务能力，并继续模拟当天积压。

## 现场记录

现场记录由 UTF-8 JSON 解码。根对象不得混入清单外字段。

```text
system_id
initial_backlog
arrivals_per_min
checkpoints
capacity_ceiling
```

`system_id` 只接受长度合规、无首尾空白或控制字符的标签。`initial_backlog` 是 0 到 1000000 的整数。`arrivals_per_min` 的长度介于 12 和 240 之间，每项是 0 到 100000 的整数。`checkpoints` 有 1 到 30 项，每项只有 `minute` 和 `backlog`；`minute` 是 1 到到诊序列长度的整数，并且各 checkpoint 严格递增；`backlog` 是非负整数。`capacity_ceiling` 是 1 到 100000 的整数。布尔值不算整数。

入口接收现场材料与输出报告的位置参数。

```bash
python3 /app/epidemic_calibration.py <现场记录> <标定结果>
```

## 排队模型

每个分钟的到诊先加入队列，再服务最多 `capacity` 人；未服务的人留到下一分钟。积压按下面公式更新。

能力与积压都按整人计，不做小数取整。

```text
B_t = max(0, B_{t-1} + arrivals_per_min[t-1] - capacity)
```

`t` 从 1 开始，`B_0` 是 `initial_backlog`。寻找满足全部 checkpoint 的最小正整数 `capacity`，且它不超过 `capacity_ceiling`。如果多个能力都能解释快照，最小能力代表门诊至少具备的承载量，不能选一个更大的数掩盖资源不足。

找到能力后，用整条到诊序列继续模拟，并给出最终积压、过程峰值以及每分钟结束时的积压序列。

## 交付结果

标定结果压成一行 JSON，文件最后只保留一个换行。

```text
system_id
status
capacity_per_min
final_backlog
peak_backlog
backlog_series
```

`status` 写 `calibrated`。`backlog_series` 从第 1 分钟到最后一分钟逐项写出，每项都是非负整数。计算完成时，命令行两个输出流均无字节。

沿用原记录重跑时，生成报告应与上次完全同字节。发布前先在同目录暂存；若替换中断，保留旧报告并清掉暂存项。

## 退回

快照字段或取值无法按门诊口径解释时，命令以 1 结束并在标准错误仅写 `运行拒收: BAD_INPUT`。

在能力上限内不存在任何可行能力时，进程返回 1，标准错误为 `运行拒收: NO_CAPACITY`。

标定报告没有可用的落盘位置时，退出码为 1，标准错误只保留 `运行拒收: IO_FAILURE`。

遇到上述错误时，终端不应出现 Python `traceback`。
