# CDS-MR: Multi-UAV Runtime Plan Repair

执行状态驱动的意图保持最小修复：代码、实验输入、逐条结果及统计/绘图材料。

这是公开上传准备目录，目前尚未上传到 GitHub。计划使用账户 `h6wjj`、仓库名 `CDS-MR`；只有创建并验证真实仓库后，才登记正式访问链接。

## 内容与入口

- `EXPERIMENT_MAP.md`：13 个实验组及图表对应关系。
- `RUNBOOK.md`：统计复算、绘图和仿真运行入口。
- `PROTOCOL_SCOPE.md`：航点映射、运动学探针、动力学执行与指标口径。
- `ENVIRONMENT.md`：软件版本和运行层级。
- `THIRD_PARTY_NOTICES.md`：来源、第三方许可证与本仓库授权状态。
- `paper_applied_intelligence/REPRODUCTION_INDEX.json`：冻结实验输入、输出、汇总和脚本的逐文件索引。
- `MANIFEST.json`：本目录文件大小与 SHA-256。

```text
python tools/check_package.py
python tools/reproduce.py main-summary
python tools/reproduce.py dynamic
```

运行入口默认只显示命令；添加 `--execute` 才运行，并将新输出写入 `generated` 的独立工作副本。现有归档结果不会被覆盖。

## 目录

| 路径 | 材料 |
|---|---|
| `实验数据/code` | 当前实现及统计/验证脚本 |
| `实验数据/scenarios` | 冻结场景、任务与候选池 |
| `实验数据/results` | 正式逐条结果、配对数据、汇总与运行清单 |
| `实验数据/archive/previous/results/ablation_v6_4_seed8_12` | 索引采用的消融原始记录 |
| `实验数据/platform` | 仿真平台、Agent4Drone 及原许可证 |
| `实验数据/dataset/UAVBench` | 来源 README 与 Apache 2.0 许可证；不包含源数据集大压缩包 |
| `uav_exp/_paper_evidence_audit` | 有限域与能力排除材料 |
| `图表数据`、`UAV_PAPER` | 派生 CSV 与绘图代码/输入 |
| `paper_applied_intelligence/figures` | 当前七幅正式图 |

未发表论文的主稿、补充稿、参考文献库、期刊模板和章节审查报告不包含在本公开目录中。原本地完整复现包保持不变。

## 复现范围

三个动力学机制案例与六个边界案例使用作者已在相同条件下测试确认的当前实现，确认项为选择、恢复状态、路径与时间。这不是新的独立重跑报告，不要求重新比较历史/当前代码。历史运行清单仍是历史来源记录；原结果不冒充本次新生成的结果。

估计路线代价不等于实际路径；静态候选可接受性不保证恢复成功；运动学探针不用于宣称动力学收益；所有执行验证属于仿真。理论编辑最小性限于规定的有限拥有者替换模型。

## 来源与许可

UAVBench、gym-pybullet-drones、MultiUAV-Plat/Agent4Drone 均保留各自来源和原许可证，详见 `THIRD_PARTY_NOTICES.md`。没有把整个仓库擅自许可为 MIT，也没有替作者选择新代码/数据的开源许可证。论文题录、正式作者信息及引用格式将在作者确认和论文信息明确后补充，不编造 DOI 或发表状态。
