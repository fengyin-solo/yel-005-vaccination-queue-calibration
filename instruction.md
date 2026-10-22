# 接种门诊匿名日志的反标定

流动接种车月底要对账。值班员保留了各站点的到诊曲线，却把当天的排队快照导成了几份不带站点名的日志；几个站点又共用一套备用服务能力，因此单看一份日志无法确定它属于哪个站点。请修 `/app/epidemic_calibration.py`，把日志与站点重新配准，恢复共用服务能力和各站开门时的积压。

## 对账材料

对账文件采用 UTF-8 编码的单层 JSON 对象；顶层四个键必须不多不少：

```text
system_id
capacity_ceiling
profiles
batches
```

馆点交接编号写入 `system_id`：至少两个、至多四十八个可见符号，比较时不得带首尾空白，也不得夹入控制码。`capacity_ceiling` 是 1 到 1000000000 的整数。布尔值不算整数。

`profiles` 有 2 到 8 项，描述没有排队日志的到诊曲线。每项只含：

```text
profile_id
arrivals_per_min
```

`profile_id` 沿用标签规则且在同一数组中不可重复。到诊曲线的长度必须落在十二至一百二十个分钟之间，每一格记录 0 到 1000000 名到场者。

`batches` 与 `profiles` 等长，每项是失去站点名的日志，只含：

```text
batch_id
sampled_minutes
checkpoint_backlogs
closing_backlog
```

`batch_id` 沿用标签规则且在同一数组中不可重复。快照分钟从曲线中挑出二至六个位置。每个位置不低于 1，也不超过该站曲线长度。这些位置必须严格递增。`checkpoint_backlogs` 与采样分钟等长，每项是 1 到 1000000000 的整数，但数组顺序被打乱。`closing_backlog` 是最后一分钟结束时的非负整数，最大 1000000000。

这些对账材料都由持续有人的时段导出：任意两份相邻快照之间，以及末次快照到末分钟之间，队列不会落到零。可以用区间内加入人数与积压变化反推能力，但快照与分钟对应仍未知。

入口接收材料与报告的位置参数：

```bash
python3 /app/epidemic_calibration.py <对账材料> <标定结果>
```

## 排队关系

第 `t` 分钟先加入 `arrivals_per_min[t-1]` 人，再服务至多 `capacity` 人。所有站点使用同一个正整数服务能力，积压按整人递推：

```text
B_t = max(0, B_{t-1} + arrivals_per_min[t-1] - capacity)
```

`B_0` 是该站未交出的开门积压。批次中的积压值没有保留与采样分钟的对应，必须从整批观测反推。若能力为 `c`、开门积压为 `i` 时整段模拟能在采样分钟重现这批数值，并且末分钟等于 `closing_backlog`，称这对站点与日志在 `c` 下相容。

要在所有站点与日志之间求一个完整的一一配对。一个能力只有在其相容关系能形成唯一完整配对时才算候选。选择最小的候选能力；不能从单个日志独立挑选最接近的站点，因为两个日志可能都能解释同一站点，而全局配对随后无解。若最小可行能力下存在两种完整配对，按 `BAD_MODEL` 退出。

同一站点与日志出现多个可重现全部观测的开门积压时，取最小的非负整数。

## 标定结果

成功时写一行紧凑 JSON，末尾只保留一个 LF。顶层键依次为：

```text
system_id
status
capacity_per_min
assignments
```

`status` 固定为 `calibrated`。`assignments` 按 `profile_id` 的 UTF-8 字节序排列；每项只有以下八个键，并保持所列顺序：

```text
profile_id
batch_id
initial_backlog
checkpoint_pairs
final_backlog
peak_backlog
backlog_series
```

`checkpoint_pairs` 按分钟升序排列，每项只有 `minute` 和 `backlog` 两个键。`backlog_series` 覆盖整条到诊曲线，每项都是非负整数。对账正常结束时，标准输出与标准错误都保持空白。

使用同一份对账材料再次标定，报告应当逐字节不变。发布前先在同目录建立暂存项；替换失败时保留旧报告并清除暂存项。

达到 8 个站点、每条曲线 120 分钟的最大形状时，完整反演应在 20 秒内结束，不能逐个枚举十亿范围内的能力。

## 退回

材料解码失败时，标准错误输出 `运行拒收: BAD_INPUT`。字段规则不合规时同样输出这一行。该拒收路径返回 1。

当前 `capacity_ceiling` 内找不到完整全局配对时，退出码为 1，标准错误只写 `运行拒收: NO_CAPACITY`。最小可行能力下配对不唯一时，标准错误只写 `运行拒收: BAD_MODEL`。

如果报告目标位置无法使用，退出码为 1，标准错误只写 `运行拒收: IO_FAILURE`。以上路径都不得输出 Python `traceback`，也不得破坏已有报告。
