# 复算、绘图和重跑入口

在解压后的包根目录执行。默认仅打印命令，不运行；加 `--execute` 才执行。每次执行先复制科学材料到独立 `generated/<唯一批次>/workspace`，遗留脚本的固定输出路径也只影响该工作副本，不影响包内冻结数据。新结果不自动覆盖或替代论文归档。

## 检查包

```text
python tools/check_package.py
```

## 由已有逐条结果复算（不生成新的 LLM 候选）

```text
python tools/reproduce.py main-summary
python tools/reproduce.py ablation-summary
python tools/reproduce.py selection-summary
python tools/reproduce.py sensitivity-summary
python tools/reproduce.py random-analysis
python tools/reproduce.py offline-analysis
python tools/reproduce.py finite-analysis
python tools/reproduce.py capability-analysis
python tools/reproduce.py grid-analysis
```

确认环境后自行添加 `--execute`。统计复算保留原脚本的配对、失败处理和输出字段；不可把汇总脚本中的历史说明文字作为新的论文结论。第 5 章与 PROTOCOL_SCOPE.md 明确当前解释口径。

有限域、能力与网格验证入口同样调用索引中的原脚本、写入独立工作副本，不改写这些验证脚本。

## 三例机制、六例边界及后缀实验的仿真重跑

```text
python tools/reproduce.py simple_local
python tools/reproduce.py kinematic
python tools/reproduce.py dynamic
python tools/reproduce.py boundary
python tools/reproduce.py grid
python tools/reproduce.py random
```

这六个入口使用 `run_simple_local_vs_cds_mr.py` 和对应冻结输入；grid/random 同时设置 `--include-global`。不自行统一频率、容差或预算，不改写 tasks.jsonl。`--execute` 可能耗时较长，建议在配置好的 Ubuntu 虚拟机中逐组运行，端口不能冲突。本轮整理未执行这些命令；九例的当前版本复现已由作者确认，不再次开展新旧实现比较。

## 数据图

```text
python tools/reproduce.py plot3
python tools/reproduce.py plot5
python tools/reproduce.py plot67
```

Fig.3 脚本读取 `UAV_PAPER/reports/fig3_processed_metrics.json`；Fig.5 读取 frozen_data 的选择与敏感性汇总；Fig.6/7 脚本读取正式网格/随机布局材料。文件关系原样保留。输出可能有 PDF/EPS/SVG/预览图，是工作副本中的数据绘图，不自动替换论文正式图，不承诺与手工排版逐像素一致。

## 主对比 / 消融 / LLM 在线运行的高级入口

现行入口在 `实验数据/code/run_cds_mr_contract_online.py`、`run_v6_3_preexec_ablation.py`、`run_role_external_baselines_v2.py`、`run_agent4drone_baseline.py`。这些原脚本带有历史默认路径及远程 API 设置，**不可不核对参数直接批量运行**。

先查看 `--help`，明确指定输入、方法、独立输出目录及日志目录；涉及 Agent4Drone 时显式将 `--agent-dir` 设为本包 `实验数据/platform/MultiUAV-Plat/agent4drone`，将配置和密钥放在包外。对应端口、timeout、预算、planner 配置沿用各组清单和协议。保留固定候选池用于公平对照，不将重新采样的 LLM 响应冒充冻结候选。

两个历史构造入口 `build_v6_4_robustness_timing.py` 和 `build_v6_4_shared_pool_scenarios.py` 是零字节占位文件，未纳入运行代码；不妨碍使用包内现成冻结 tasks.jsonl 和候选池。这里不声称能从自然语言重新构造完全相同的历史采样。

本包没有把旧 scripts 的帮助参数猜成一键全实验重跑，也没有将逐组重跑未执行表述为论文结果不能复现。
