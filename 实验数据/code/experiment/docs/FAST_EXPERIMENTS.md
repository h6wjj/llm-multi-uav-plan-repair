# Agent4Drone 实验加速说明

正式评测和开发调试使用不同的执行路径，避免每次修改统计或仿真代码都重新调用 LLM。

## 1. 正式方法评测：两个隔离 worker

```bash
cd ~/uav_exp/experiment
source ~/uav_exp/venv312/bin/activate

python scripts/run_agent4drone_parallel.py \
  --input ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_test100.jsonl \
  --output ~/uav_exp/data/processed/v1/METHOD_NAME_seed0.jsonl \
  --workers 2
```

每个 worker 都有独立的 Agent4Drone 进程、PyBullet bridge 和端口。模型、Prompt、任务、
episode seed、900 步预算和成功判定均未改变。输出按完成顺序写入，分析时必须按 `task_id`
连接，不能依赖文件行号。

当前虚拟机为 8 GB 内存、6 vCPU，因此脚本限制最多两个 worker。若模型提供商出现限流，
把 `--workers` 改成 `1`。同一个方法中断后用完全相同的输出路径重新执行，脚本会跳过已有
`task_id` 并继续运行。

不要把新的方法写入已经完成的 baseline 文件。Ours、FullReplan 和外部方法必须分别使用
独立输出文件。

## 2. 仿真和指标调试：缓存工具链回放

如果只修改 PyBullet bridge、控制器、成功判定、统计或绘图，不需要再次调用 LLM：

```bash
python scripts/replay_agent4drone_traces.py \
  --tasks ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_test100.jsonl \
  --traces ~/uav_exp/data/processed/v1/agent4drone_baseline_v1.jsonl \
  --output ~/uav_exp/data/processed/v1/agent4drone_baseline_replay.jsonl \
  --workers 2
```

缓存回放只执行已经记录的工具调用，不产生新的模型 Token。它不能替代 Ours 或其他规划方法
的正式评测：只要 Prompt、规划器、修复器或工具选择逻辑发生改变，就必须使用正式并行 runner
重新调用模型。

## 3. 固定数据口径

正式 baseline 使用的是：

```text
uavbench_waypoint_projection_gymcompact_test100.jsonl
```

不要误用未缩放的 `uavbench_waypoint_projection_test100.jsonl`。两者任务 ID 相同，但坐标尺度
不同，混用会使 PyBullet 轨迹大量触发 900 步上限。

## 4. 当前实测

- 原串行 baseline：100 条约 101 分钟。
- 两 worker 冒烟：两条单任务分别耗时 56.3 秒和 71.3 秒，总墙钟 71.5 秒，速度提升 1.79 倍。
- 预计正式 100 条约 50～60 分钟，具体取决于模型 API 延迟和限流。
- 缓存回放：100 条约 85.5 秒；99 条与原始物理指标完全一致，剩余 1 条是原 baseline 的
  180 秒 runner timeout，缺少完整工具轨迹，不能严格回放。

