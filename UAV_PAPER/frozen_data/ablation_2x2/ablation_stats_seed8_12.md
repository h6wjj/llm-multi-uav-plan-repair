# v6.4 held-out 消融统计（seed 8–12）

- 输入：5 个 seed 文件；120 条结果；30 个 task–seed 配对。
- 原始 JSONL 未修改；本文件为派生统计。
- CI：确定性 percentile bootstrap 95% CI；p：双侧 sign-flip permutation estimate。

## 1. 四臂描述统计

| 方法 | RSR ↑ | CSR ↑ | NPSD ↓ | Repair latency ↓ | Mission time ↓ |
|---|---:|---:|---:|---:|---:|
| Blind+Global-Replan | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.650 ± 0.190 (n=30) | 0.012 ± 0.005 (n=30) | 0.339 ± 0.063 (n=30) |
| SeSE+Global-Replan | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.650 ± 0.190 (n=30) | 0.011 ± 0.006 (n=30) | 0.339 ± 0.063 (n=30) |
| Blind+CDS-MR | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.433 ± 0.048 (n=30) | 0.011 ± 0.005 (n=30) | 0.339 ± 0.063 (n=30) |
| SeSE+CDS-MR | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.433 ± 0.048 (n=30) | 0.010 ± 0.004 (n=30) | 0.339 ± 0.063 (n=30) |

## 2. 配对比较（左 − 右）

| 比较 | 指标 | n | 平均差 | 95% CI | p | Cohen dz |
|---|---|---:|---:|---:|---:|---:|
| SeSE_vs_Blind_Global | RSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_Global | CSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_Global | NPSD | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_Global | Repair latency (s) | 30 | -0.001 | [-0.003, 0.002] | 0.6631 | -0.081 |
| SeSE_vs_Blind_Global | Mission time (s) | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_CDS | RSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_CDS | CSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_CDS | NPSD | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| SeSE_vs_Blind_CDS | Repair latency (s) | 30 | -0.001 | [-0.003, 0.001] | 0.4189 | -0.153 |
| SeSE_vs_Blind_CDS | Mission time (s) | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| CDS_vs_Global_Blind | RSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| CDS_vs_Global_Blind | CSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| CDS_vs_Global_Blind | NPSD | 30 | -0.217 | [-0.281, -0.157] | 0.0000 | -1.224 |
| CDS_vs_Global_Blind | Repair latency (s) | 30 | -0.001 | [-0.003, 0.002] | 0.5681 | -0.108 |
| CDS_vs_Global_Blind | Mission time (s) | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| CDS_vs_Global_SeSE | RSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| CDS_vs_Global_SeSE | CSR | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |
| CDS_vs_Global_SeSE | NPSD | 30 | -0.217 | [-0.281, -0.157] | 0.0000 | -1.224 |
| CDS_vs_Global_SeSE | Repair latency (s) | 30 | -0.001 | [-0.004, 0.002] | 0.4435 | -0.146 |
| CDS_vs_Global_SeSE | Mission time (s) | 30 | 0.000 | [0.000, 0.000] | 1.0000 | 0.000 |

## 3. SeSE 独立诊断

- task–seed 配对：30；共享候选池一致性：True。
- 独立合同检查器初始有效率：Blind 1.000；SeSE 1.000。
- 选择非 candidate-0 比例：Blind 0.633；SeSE 0.000。
- SeSE−Blind 选中路由代价：-0.493682 ± 0.398763，95% CI [-0.633351, -0.351945]。负值表示 SeSE 选择的候选路由代价更低。
- 路由代价遗憾：Blind 0.493682；SeSE 0.000000。
- 初始规划延迟：Blind 33.769 s；SeSE 33.769 s。

解释边界：本批次所有候选的合同有效率饱和为 1.0，因此不能用 Contract Selection Gain 证明 SeSE 提升合同可行性；SeSE 的可报告证据是共享候选池下的路线代价选择质量。它也不证明 SeSE 提高失败后的 RSR。

## 4. 逐任务结果

| UAVBench source task | 方法 | RSR | CSR | NPSD | Repair latency (s) | Mission time (s) |
|---|---|---:|---:|---:|---:|---:|
| Arctic_Swarm_Coordination_with_Lightning_Risk_003470 | Blind+Global-Replan | 1.000 | 1.000 | 0.667 | 0.010 | 0.400 |
| Arctic_Swarm_Coordination_with_Lightning_Risk_003470 | SeSE+Global-Replan | 1.000 | 1.000 | 0.667 | 0.011 | 0.400 |
| Arctic_Swarm_Coordination_with_Lightning_Risk_003470 | Blind+CDS-MR | 1.000 | 1.000 | 0.400 | 0.012 | 0.400 |
| Arctic_Swarm_Coordination_with_Lightning_Risk_003470 | SeSE+CDS-MR | 1.000 | 1.000 | 0.400 | 0.010 | 0.400 |
| Forest_Fog_Swarm_Coordination_012674 | Blind+Global-Replan | 1.000 | 1.000 | 0.667 | 0.013 | 0.400 |
| Forest_Fog_Swarm_Coordination_012674 | SeSE+Global-Replan | 1.000 | 1.000 | 0.667 | 0.015 | 0.400 |
| Forest_Fog_Swarm_Coordination_012674 | Blind+CDS-MR | 1.000 | 1.000 | 0.400 | 0.012 | 0.400 |
| Forest_Fog_Swarm_Coordination_012674 | SeSE+CDS-MR | 1.000 | 1.000 | 0.400 | 0.010 | 0.400 |
| HarborSwarmDelivery_Quadrotor_015740 | Blind+Global-Replan | 1.000 | 1.000 | 0.500 | 0.008 | 0.267 |
| HarborSwarmDelivery_Quadrotor_015740 | SeSE+Global-Replan | 1.000 | 1.000 | 0.500 | 0.008 | 0.267 |
| HarborSwarmDelivery_Quadrotor_015740 | Blind+CDS-MR | 1.000 | 1.000 | 0.500 | 0.008 | 0.267 |
| HarborSwarmDelivery_Quadrotor_015740 | SeSE+CDS-MR | 1.000 | 1.000 | 0.500 | 0.007 | 0.267 |
| Harbor_Swarm_Coordination_with_Gusts_017664 | Blind+Global-Replan | 1.000 | 1.000 | 0.667 | 0.012 | 0.300 |
| Harbor_Swarm_Coordination_with_Gusts_017664 | SeSE+Global-Replan | 1.000 | 1.000 | 0.667 | 0.008 | 0.300 |
| Harbor_Swarm_Coordination_with_Gusts_017664 | Blind+CDS-MR | 1.000 | 1.000 | 0.400 | 0.010 | 0.300 |
| Harbor_Swarm_Coordination_with_Gusts_017664 | SeSE+CDS-MR | 1.000 | 1.000 | 0.400 | 0.008 | 0.300 |
| Suburban_Swarm_Coordination_Under_Microburst_Risk_034427 | Blind+Global-Replan | 1.000 | 1.000 | 0.400 | 0.012 | 0.400 |
| Suburban_Swarm_Coordination_Under_Microburst_Risk_034427 | SeSE+Global-Replan | 1.000 | 1.000 | 0.400 | 0.010 | 0.400 |
| Suburban_Swarm_Coordination_Under_Microburst_Risk_034427 | Blind+CDS-MR | 1.000 | 1.000 | 0.400 | 0.014 | 0.400 |
| Suburban_Swarm_Coordination_Under_Microburst_Risk_034427 | SeSE+CDS-MR | 1.000 | 1.000 | 0.400 | 0.013 | 0.400 |
| Urban_Snowfall_Swarm_Coordination_038984 | Blind+Global-Replan | 1.000 | 1.000 | 1.000 | 0.013 | 0.267 |
| Urban_Snowfall_Swarm_Coordination_038984 | SeSE+Global-Replan | 1.000 | 1.000 | 1.000 | 0.015 | 0.267 |
| Urban_Snowfall_Swarm_Coordination_038984 | Blind+CDS-MR | 1.000 | 1.000 | 0.500 | 0.010 | 0.267 |
| Urban_Snowfall_Swarm_Coordination_038984 | SeSE+CDS-MR | 1.000 | 1.000 | 0.500 | 0.011 | 0.267 |

## 5. 论文可用结论

- CDS-MR：在 held-out seed 8–12 上，CDS 臂相对同一 selector 的 Global-Replan 臂使 NPSD 从 0.650 降至 0.433，且 RSR/CSR 未下降；这支持“减少计划修改，同时保持恢复质量”的限定表述。
- SeSE：本批次的独立合同有效率没有区分度，不能声称它提升初始合同可行性；若保留 SeSE，应报告其在共享候选池中的路线代价选择诊断，并将其定位为执行前结构化候选筛选前端。
- 不应据此声称完整方法在所有五项指标均最优；H-MRTA 的修复延迟仍然是优势。
- 本报告不覆盖失败类型/约束紧张度鲁棒性扩展，也不等同于真实物理异构无人机验证。
