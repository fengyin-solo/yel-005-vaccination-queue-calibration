# 接种门诊匿名日志的反标定

流动接种车月底要对账。值班员保留了各站点的到诊曲线，却把当天的排队快照导成了几份不带站点名的日志；几个站点又共用一套备用服务能力，因此单看一份日志无法确定它属于哪个站点。请修 `/app/epidemic_calibration.py`，把日志与站点重新配准，恢复共用服务能力和各站开门时的积压。

## 对账材料

顶层四个键必须不多不少：

```text
system_id
capacity_ceiling
profiles
batches
```

馆点交接编号写入 `system_id`：至少两个、至多四十八个可见符号，比较时不得带首尾空白，也不得夹入控制码。`capacity_ceiling` 是恰好两项的整数数组，依次约束早段和晚段服务能力。每项都在 1 到 1000000000 之间。布尔值不算整数。

`profiles` 恰好有 2 项，描述没有排队日志的到诊曲线。每项只含：

```text
profile_id
arrivals_per_min
```

`profile_id` 沿用标签规则且在同一数组中不可重复。到诊曲线的长度是十二至六十四个分钟，每一格记录 0 到 1000000 名到场者。

`batches` 恰好比 `profiles` 多两条，每项是失去站点名的日志，只含：

```text
batch_id
sampled_minutes
checkpoint_backlogs
closing_backlog
```

`batch_id` 沿用标签规则且在同一数组中不可重复。快照分钟从曲线中挑出二至三个位置。每个位置不低于 1，也不超过该站曲线长度。这些位置必须严格递增。`checkpoint_backlogs` 与采样分钟等长，每项是 1 到 1000000000 的整数，但数组顺序被打乱。`closing_backlog` 是最后一分钟结束时的非负整数，最大 1000000000。

这些对账材料都由持续有人的时段导出：任意两份相邻快照之间，以及末次快照到末分钟之间，队列不会落到零。四份日志里只有两份来自当日正式站点，另外两份是测试台留下的相似波形。正式日志的选择和站点配对要由整批观测一次确定。可以用区间内加入人数与积压变化反推能力，但快照与分钟对应仍未知。

入口接收材料与报告的位置参数：

```bash
python3 /app/epidemic_calibration.py <对账材料> <标定结果>
```

## 排队关系

第 `t` 分钟先加入 `arrivals_per_min[t-1]` 人，再服务至多当前阶段容量。所有站点共享同一条阶段表：当天在第 `switch_minute` 分钟切换到晚段，切换发生在该分钟服务开始前。早段使用正整数 `early_capacity`，晚段使用正整数 `late_capacity`。两个容量分别不超过 `capacity_ceiling` 的两项。积压按整人递推：

```text
capacity_t = early_capacity  (t < switch_minute)
capacity_t = late_capacity   (t >= switch_minute)
B_t = max(0, B_{t-1} + arrivals_per_min[t-1] - capacity_t)
```

`B_0` 是该站未交出的开门积压。批次中的积压值没有保留与采样分钟的对应，必须从整批观测反推。若容量三元组 `(early_capacity, late_capacity, switch_minute)` 与开门积压 `i` 能共同让整段模拟重现采样值和 `closing_backlog`，称这对站点与日志在该三元组下相容。`switch_minute` 是 1 到曲线长度之间的整数。

## 恢复顺序

先枚举 `switch_minute`，并把每条日志的积压值枚举为采样分钟上的顺序。对相邻采样 `m1 < m2`，令 `a` 为 `m1+1..m2` 中早段分钟数、`b` 为晚段分钟数，则无归零区间满足：

```text
a * early_capacity + b * late_capacity
  = sum(arrivals_per_min[m1:m2]) - (B_m2 - B_m1)
```

末次采样 `m` 到曲线末尾同样满足：

```text
a * early_capacity + b * late_capacity
  = sum(arrivals_per_min[m:]) + B_m - closing_backlog
```

对每个站点与日志、每个切换分钟和积压排列，联立这些二元一次方程，筛出落在两个 ceiling 内且能由阶段模拟复现全部观测的容量三元组。再对每个三元组建立站点到日志的二分图，并用 DP 或等价算法计数完整匹配；不要从单个日志独立贪心。

要在全部日志中选出与站点数相同的正式日志，并求一个完整的一一配对。一个容量三元组只有在其相容关系能形成唯一完整选取与配对时才算候选。
候选三元组依次由 `early_capacity`、`late_capacity` 和 `switch_minute` 组成。
候选三元组用字典序比较。
排序后的第一项就是选定候选。
不能从单个日志独立挑选最接近的站点，因为两个日志可能都能解释同一站点，而全局配对随后无解。
若最小可行三元组下存在两种完整配对，按 `BAD_MODEL` 退出。

同一站点与日志出现多个可重现全部观测的开门积压时，取最小的非负整数。

## 标定结果

成功时写一行紧凑 JSON，末尾只保留一个 LF。顶层键依次为：

```text
system_id
status
capacity_regimes
assignments
```

`status` 固定为 `calibrated`。
`capacity_regimes` 只含三个键：

```text
early
late
switch_minute
```

该对象按上列顺序写两个容量和切换分钟。
`assignments` 按 `profile_id` 的 UTF-8 字节序排列；每项只有以下八个键，并保持所列顺序：

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

达到 2 个站点、每条曲线 32 分钟的最大形状时，完整反演应在 20 秒内结束，不能逐个枚举十亿范围内的能力。
实现完成后，以镜像内测试为准交付。

## 退回

材料解码失败时，标准错误输出 `运行拒收: BAD_INPUT`。字段规则不合规时同样输出这一行。该拒收路径返回 1。

当前 `capacity_ceiling` 内找不到完整全局配对时，退出码为 1，标准错误只写 `运行拒收: NO_CAPACITY`。
最小可行三元组下配对不唯一时，标准错误只写 `运行拒收: BAD_MODEL`。

如果报告目标位置无法使用，退出码为 1，标准错误只写 `运行拒收: IO_FAILURE`。以上路径都不得输出 Python `traceback`，也不得破坏已有报告。
