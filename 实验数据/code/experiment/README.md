# UAV 数据画像与统一任务格式

这些脚本只读取原始数据，先生成结构报告，不会修改原始文件。

## 在虚拟机运行

把本目录下的 `scripts/`、`schemas/` 复制到 `~/uav_exp/experiment/` 后，在虚拟机中执行：

```bash
cd ~/uav_exp/experiment

python scripts/profile_swarm_steward.py
python scripts/profile_uavbench.py
python scripts/profile_hal13k.py
```

默认输入目录为：

```text
~/uav_exp/data/swarm_steward
~/uav_exp/data/uavbench/extracted
~/uav_exp/data/hal13k_annotations
```

默认输出目录为：

```text
~/uav_exp/data/reports/
├── swarm_steward_profile.json
├── uavbench_profile.json
└── hal13k_profile.json
```

如果输入路径不同，可以显式指定：

```bash
python scripts/profile_uavbench.py \
  --root ~/uav_exp/data/uavbench/extracted \
  --output ~/uav_exp/data/reports/uavbench_profile.json
```

## 当前 schema

统一中间格式定义在 `schemas/uav_task_v1.schema.json`。后续转换器应保留
`source_dataset`、`source_file`、`source_index` 和 `metadata.transform_version`，
使每个预处理样本都能追溯到原始数据。

## 转换为统一 JSONL

先做小规模冒烟转换：

```bash
mkdir -p ~/uav_exp/data/processed/v1
python scripts/convert_swarm_steward.py \
  --output ~/uav_exp/data/processed/v1/swarm_steward_tasks_smoke.jsonl
python scripts/convert_uavbench.py \
  --max-tasks 100 \
  --output ~/uav_exp/data/processed/v1/uavbench_tasks_smoke.jsonl
python scripts/convert_hal13k.py \
  --max-tasks 100 \
  --output ~/uav_exp/data/processed/v1/hal13k_tasks_smoke.jsonl \
  --objects-output ~/uav_exp/data/processed/v1/hal13k_object_map.json
```

确认冒烟样本后，再去掉 `--max-tasks` 生成完整文件：

```bash
python scripts/convert_uavbench.py
python scripts/convert_hal13k.py
```

Swarm-Steward 的 `reference_plan` 是观测到的工具调用；UAVBench 的
`reference_plan` 是场景 waypoint 的规范化表示，二者都不是人工专家
演示。HaL-13k 当前下载内容只有位置/轨迹标注和对象映射，没有自然语言
指令或图像帧，因此转换结果的 `instruction` 为空，并带有
`instruction_missing` 等 `unsupported_features` 标记，不能直接作为
LLM 指令跟随测试集。

## 结构校验

```bash
python scripts/validate_uav_task_v1.py \
  --input ~/uav_exp/data/processed/v1/swarm_steward_tasks_smoke.jsonl
python scripts/validate_uav_task_v1.py \
  --input ~/uav_exp/data/processed/v1/uavbench_tasks_smoke.jsonl
python scripts/validate_uav_task_v1.py \
  --input ~/uav_exp/data/processed/v1/hal13k_tasks_smoke.jsonl
```

校验器只检查结构、唯一 ID 和可追溯字段；HaL-13k 的空指令属于已知
警告，不会被当成结构错误。

## 仿真可执行性过滤与适配

结构合格后，还要按当前 gym-pybullet-drones 的能力做第二次过滤。
严格模式不把八旋翼、天气、交通、移动障碍物或混合机型静默改成别的
仿真对象：

```bash
python scripts/filter_executable_tasks.py \
  --input ~/uav_exp/data/processed/v1/uavbench_tasks_smoke.jsonl \
  --output ~/uav_exp/data/processed/v1/uavbench_gym_executable_smoke.jsonl \
  --manifest ~/uav_exp/data/processed/v1/uavbench_gym_filter_smoke.json
```

Swarm-Steward 可单独生成规划评测清单；它的符号地点没有天然的 PyBullet
坐标，不能直接当作仿真 waypoint：

```bash
python scripts/filter_executable_tasks.py \
  --mode planning \
  --input ~/uav_exp/data/processed/v1/swarm_steward_tasks_smoke.jsonl \
  --output ~/uav_exp/data/processed/v1/swarm_steward_planning_smoke.jsonl \
  --manifest ~/uav_exp/data/processed/v1/swarm_steward_planning_smoke.json
```

过滤清单通过后，在虚拟机中运行一次无 GUI 适配冒烟测试：

```bash
python scripts/gym_task_adapter.py \
  --input ~/uav_exp/data/processed/v1/uavbench_gym_executable_smoke.jsonl \
  --seed 0
```

成功标志为 `"status": "adapter_smoke_pass"`。当前适配器使用
`VelocityAviary`，只接受统一的 `DroneModel`；因此严格模式下混合机型会
被拒绝，并在 manifest 中记录原因。适配器冒烟只验证环境初始化、reset
和一步 action，不等价于 baseline 成功率。

如果需要把 UAVBench 的可控 `quadrotor` 场景投影到当前 CF2X 仿真器，使用
显式的 waypoint-only 投影模式：

```bash
python scripts/convert_uavbench.py \
  --output ~/uav_exp/data/processed/v1/uavbench_tasks.jsonl

python scripts/filter_executable_tasks.py \
  --mode projection \
  --max-accepted 100 \
  --input ~/uav_exp/data/processed/v1/uavbench_tasks.jsonl \
  --output ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_smoke.jsonl \
  --manifest ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_smoke.json
```

该模式只要求数值起点和 waypoint，并将 `quadrotor` 映射为 CF2X；天气、
交通、移动障碍物、故障、禁飞区等字段不会被删除，而会写入
`metadata.projection_dropped_features`，表示它们不在本轨道的仿真指标中。
因此结果应称为“UAVBench waypoint projection”，不能声称复现完整原场景。

## 固定评测子集与随机种子

正式 baseline 不直接使用按文件顺序截取的 smoke 集。先对完整的 projection 候选集按固定种子生成评测子集：

```bash
python scripts/make_fixed_eval_split.py \
  --input ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_all.jsonl \
  --output ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_test100.jsonl \
  --manifest ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_test100.json \
  --seed 20260823 \
  --count 100
```

这里的 `20260823` 是论文实验协议中的 `split_seed`，不是当前时间的自动值。选择规则是对
`sha256(f"{split_seed}:{task_id}")` 排序后取前 100 条，因此不依赖输入文件顺序、操作系统或
Python 的进程随机哈希。每个任务的 Gym reset seed 另由
`sha256(f"episode:{split_seed}:{task_id}")` 稳定派生，并写入任务的
`metadata.episode_seed`。重新运行同一命令必须得到相同的任务 ID 清单和 manifest 哈希。

`--count 100` 只用于首轮 API 成本可控的正式评测；完成链路后可在保持同一 `split_seed` 的前提下
扩展为 300 或更多，并把新的清单作为单独实验版本保存。

## Agent4Drone baseline 配置

当前 baseline 已固定为 DeepSeek 官方 API 的 V4-Flash：

```text
provider: Deepseek API
base_url: https://api.deepseek.com
model: deepseek-v4-flash
temperature: 0.0
prompt: template/system_prompt.py (agent4drone_system_prompt_v1)
toolchain_json_recording: true
```

## 方法与实验条件命名

方法定义（实验组成）为 `B0+SeSE+CDS-MR`：`SeSE` 负责意图保持，`CDS-MR` 负责执行失败后的错误定位、因果切片和局部最小修复。

`B0+SeSE+CDS-MR` 仅作为实验条件标识；论文最终方法名称以论文中的正式命名为准，不直接使用该组合名。

生成不依赖参考轨迹的 contract 场景：

```bash
python scripts/build_cds_mr_contract_scenarios.py \
  --input ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_test100.jsonl \
  --output ~/uav_exp/data/processed/v1/uavbench_cds_mr_contract_scenarios_v1.jsonl \
  --manifest ~/uav_exp/data/processed/v1/uavbench_cds_mr_contract_scenarios_v1.json
```

该场景只从任务 contract 生成约束；故障由执行时 bridge 返回的首次失败触发，
不在场景文件中预先写入成功轨迹或人工故障标签。

仅运行本方法的 smoke 配对入口（正式 API 实验前使用新输出文件）：

```bash
python scripts/run_cds_mr_contract_online.py \
  --input ~/uav_exp/data/processed/v1/uavbench_cds_mr_contract_scenarios_v1.jsonl \
  --output ~/uav_exp/data/processed/v1/agent4drone_cds_mr_contract_online_smoke15.jsonl \
  --limit 15
```

该入口的修复触发条件是 bridge 的真实 `first_failure`；没有失败时不调用 CDS-MR。

外部对比算法尚未确定，当前 runner 不包含外部方法；待对比协议确认后，再在同一场景、同一任务顺序和同一模型配置下接入。

在虚拟机中用配置脚本复核或恢复该协议：

```bash
cd ~/uav_exp/experiment
source ~/uav_exp/venv312/bin/activate
python scripts/configure_agent4drone.py
```

脚本会备份原始 `llm_settings.json`，不写入 API 密钥；启动服务前通过
`export LLM_API_KEY='你的密钥'` 提供密钥。服务启动后，工具链记录中会保存模型、temperature、
固定系统提示词版本和提示词 SHA-256，保证每次 baseline 的四项配置可审计。

Agent4Drone 工具调用可先规范化为中间命令：

```bash
python scripts/agent4drone_action_adapter.py \
  --input ~/uav_exp/data/agent4drone_traces.jsonl \
  --output ~/uav_exp/data/processed/v1/agent4drone_commands.jsonl
```

`move_to` 只有在参数中包含数值 `x/y/z` 时才会进入 `MOVE_TO`；符号地点
会被标记为 `numeric_position_missing_or_symbolic`，避免把未知地点伪造为
仿真坐标。
### Gym local-coordinate projection

UAVBench source scenes often use world coordinates from hundreds to thousands
of metres, which are not directly executable in the compact `VelocityAviary`
workspace.  The formal waypoint-projection baseline therefore runs
`project_waypoints_for_gym.py`: it translates the primary start to `(0,0,1)`,
compresses the horizontal path to 2 m, and projects altitude to a fixed safe
plane.  Source waypoints, transform parameters, and the transform version are
kept in each record's `metadata`; results must be reported as **UAVBench
waypoint projection**, not as reproduction of the original world scale.

```bash
python scripts/project_waypoints_for_gym.py \
  --input ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_all.jsonl \
  --output ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_all.jsonl \
  --horizontal-budget 2.0 --vertical-budget 0

python scripts/make_fixed_eval_split.py \
  --input ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_all.jsonl \
  --output ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_test100.jsonl \
  --manifest ~/uav_exp/data/processed/v1/uavbench_waypoint_projection_gymcompact_test100.json \
  --seed 20260823 --count 100
```
