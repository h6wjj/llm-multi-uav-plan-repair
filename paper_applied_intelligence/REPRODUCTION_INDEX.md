# 当前中文稿复现入口（更新至2026-10-10）

所有路径以项目根目录为起点，而不是以“实验数据”目录为起点。逐文件入口见同目录的 REPRODUCTION_INDEX.json；其中保留原文件名和历史代码标识，不改写冻结记录。该索引不表示已发布公开仓库，也不新增实验。

## 正文实验与权威入口

| 实验 | 当前记录入口（项目相对路径） | 协议或复算源码 |
|---|---|---|
| 主对比 | 实验数据/results/main_v6_4/seed_13 至 seed_18 中的 b0.jsonl、cds_mr.jsonl；外部两法使用实验数据/results/external_baseline_repro_v1/h_mrta.jsonl、llm_ape_final.jsonl | 实验数据/code/summarize_closed_main_comparison.py；唯一主汇总为实验数据/results/main_v6_4/summary_closed_external_overlay.json |
| 2×2 消融 | 实验数据/archive/previous/results/ablation_v6_4_seed8_12/seed_8 至 seed_12/results.jsonl | 当前输入与共享池：实验数据/scenarios/ablation_v6_4_seed8_12/；汇总：实验数据/results/ablation_v6_4_seed8_12/summary.json；源码：实验数据/code/summarize_v6_3_preexec_ablation.py |
| 选择诊断 | 实验数据/results/sese_selection/ 下的 candidate_pools.jsonl、candidate_executions.jsonl、selections.jsonl | 实验数据/scenarios/sese_selection/；实验数据/code/summarize_sese_selection_evidence.py |
| 约束敏感性 | 实验数据/results/constraint_sensitivity/raw_results.jsonl | 实验数据/scenarios/constraint_sensitivity/；实验数据/code/summarize_constraint_sensitivity.py |
| 有限候选域核验 | uav_exp/_paper_evidence_audit/experiment_A_exhaustive_optimality_20260919/row_results.jsonl | uav_exp/_paper_evidence_audit/validate_cds_mr_exhaustive_optimality.py；输入为主对比的修复模块记录 |
| 能力排除验证 | uav_exp/_paper_evidence_audit/experiment_B_heterogeneous_capability_20260919/full/cds_mr.jsonl | 同实验目录 tasks.jsonl、manifest.json；uav_exp/_paper_evidence_audit/validate_experiment_b_heterogeneous_capability.py |
| 简单局部负对照 | 实验数据/results/local_repair_vs_cds_mr_v1/ | 该目录冻结输入、配对审计和汇总；实验数据/code/run_simple_local_vs_cds_mr.py |
| 三例运动学探针 | 实验数据/results/mechanism_stress_v2_vm_v10/ | 恢复字段勘误见实验数据/REPRODUCIBILITY_CLOSEOUT_20261003.md；不用 v7、v8 的旧解释替代 |
| 三例动力学机制与六例边界 | 实验数据/results/mechanism_dynamic_scale_v3_ctrl48_final_vm/、实验数据/results/mechanism_boundary_v1_vm/ | 当前复现实现：实验数据/code/run_simple_local_vs_cds_mr.py、实验数据/code/gym_uav_bridge.py；各结果目录保留输入与历史清单；作者已确认同条件复现，见下述说明 |
| 固定参数网格 | 实验数据/results/suffix_grid_three_way_formal_v1/ | 实验数据/scenarios/suffix_coupling_grid_v1/protocol.json；实验数据/code/audit_suffix_grid_formal_run.py |
| 独立随机布局 | 实验数据/results/b3_independent_task_distribution_formal_v1/ | 实验数据/scenarios/independent_task_distribution_b3_v1/protocol.json、freeze_manifest.json、tasks.jsonl；实验数据/code/analyze_b3_independent_distribution.py |
| 同目标离线枚举 | 实验数据/results/offline_stability_control_v1/by_task.jsonl | 实验数据/protocols/offline_stability_control_v1.md；实验数据/code/offline_stability_control.py |

**历史源码可用性（2026-10-09 复核）。** 已按虚拟机上的真实结果目录核查：`/home/h/uav_exp/experiment/results/mechanism_dynamic_scale_v3_ctrl48_final/results/run_manifest.json` 与 `/home/h/uav_exp/experiment/results/mechanism_boundary_v1/results/run_manifest.json`。两份清单记录的历史 runner 路径均为 `/home/h/uav_exp/experiment/scripts/run_simple_local_vs_cds_mr.py`，SHA-256 为 `99efeb3db054e2f17c8898ceda497d02be1551c99ac155c110d51c312c98823f`；bridge SHA-256 为 `fe6807f8c52f624a7bbc3513d4db1d2e05f1e70d35e4220bd9caab43f28d2ec4`，但清单没有记录 bridge 路径。虚拟机上的输入文件哈希与清单一致（动力学 3 条、边界 6 条）。然而，当前同路径 runner 与 bridge 的 SHA-256 分别为 `a3ab395a1992bacf54969c20b17d5a07ba941ae84b07e2fda7da599ef39cc03d` 和 `a7e7034e9932e6532958a89ee0db5201c752ded7941748c97fd5b42b6df42fed`；当前文件修改时间晚于两份运行清单。检查的实际结果目录没有源码快照，虚拟机实验/复现代码树中的 98 个 Python 源文件及项目目录中的 Python 源文件均无记录哈希匹配；现存 `.bak`、`.orig` 与归档桥接文件也不匹配。故当前源码只能作为参考实现，不能冒充九例的精确历史执行源码。冻结结果仍可作为这九个实例的历史记录核读，但目前不支持精确代码重跑声明。核查范围和候选文件哈希见 `reports/chapter5_final_review.md` 的 F4 跟进记录。
消融行级文件虽保存在 archive/previous 中，但其输入、配对单元及重算值与当前消融汇总一致，因此在此明确指定；不能因此把该归档的其他实验或全部历史汇总当作当前证据。主对比旧 summary_main_comparison_v6_4_seed13_18.json 和同名 CSV 不用于当前外部基线结果。

## 九例当前版本复现说明（2026-10-10）

**当前有效状态：作者已完成同条件复现并确认结果一致。** 作者明确确认，当前版本在相同条件下复现了三个动力学机制案例与六个边界案例的接管选择、恢复状态、路径和时间，并指定当前版本作为复现实现，不再要求新旧代码差异核对。当前源码入口沿用上表的现行实现；输入及论文结果仍由原冻结目录提供，不覆盖历史清单或结果。

本说明依据作者亲自完成的测试及明确确认（`author_confirmed_same_conditions_reproduction`），不表述为助手本轮重新运行或独立审计。第五章F4／第六章G1的九例可重跑事项据此关闭；不再把找回旧源码哈希作为当前复现的前置条件。正文及既有实验数值保持不变。

前述2026-10-09的搜索结论仅保留为历史版本沿革：“当时原样源码未找回”不等于“当前版本不能复现”。其中“当前源码只能作为参考实现”“目前不支持精确代码重跑声明”等措辞是当时状态，不再适用于当前版本的结果复现能力。这里确认的是同条件结果复现，不声称当前源码与历史源码逐字相同。详细状态记录见 `reports/nine_case_current_reproduction_confirmation.md`。

## 期限、预算与量化参数

下表对应所有指定输入逐条核对后的合同值，而非单例推断。任务束 deadline_s 与 resources.time_budget_s 均为 600 s。基础步数和恢复预留为全机队共享控制步；仅开启窗口后将两者相加。离线枚举只读取源合同，不产生新预算或执行。

| 输入组 | max_total_steps | repair_step_budget | waypoint_tolerance（m） |
|---|---:|---:|---:|
| 主对比、消融的名义条件；选择诊断 | 900 | 0 | 0.25 |
| 主对比、消融的失效条件；约束敏感性；简单局部负对照 | 900 | 300 | 0.25 |
| 能力排除的名义与失效条件 | 900 | 0 | 0.25 |
| 三例运动学探针 | 900 | 300 | 0.02 |
| 动力学机制、边界、固定网格、随机布局 | 12000 | 6000 | 0.10 |

任务束期限读取 contract.mission.task_bundles 中各束的 deadline_s，到达容差读取 contract.mission.waypoint_tolerance，预算读取 contract.resources。动作签名量化为每坐标 int(round(coordinate / 0.05))，半格采用 Python round 的最近偶数规则；它不改变输入航点，也不同于到达容差。源码入口为实验数据/code/run_cds_mr_contract_online.py 的 _quantized_xyz；834 处指定行级记录中的 waypoint_quantization_m 均为 0.05。数量包含多个方法及重复记录，不作为独立样本数。

## 正式种子与绘图

随机布局链、分支、汇合分别使用 20280001–20280030、20281001–20281030、20282001–20282030。调试种子 20279001–20279003 不参与正式统计。实际任务顺序以冻结 tasks.jsonl 为准；bootstrap 的有限重抽样结果还依赖这一顺序及随机数消耗次序。

图 6、图 7 的源码为 UAV_PAPER/scripts/plot_suffix_results.py，准备数据为图表数据/suffix_results/，其中 figure_provenance.json 记录来源哈希。它们位于项目根目录，不在“实验数据”包内。实验 A/B 的导出清单与 tar.gz 也位于 uav_exp/_paper_evidence_audit/。公开仓库应保留这些根目录关系或明确提供迁移映射，不能只上传“实验数据”后声称上述入口已经包含在包内。

这里只提供定位与追溯，不运行会生成结果的分析或绘图入口。既有只读材料检查可从项目根目录执行 python -X utf8 实验数据/code/verify_reproducibility_closeout.py；它检查规定的元数据及记录一致性，不是新的独立科学有效性审计。
