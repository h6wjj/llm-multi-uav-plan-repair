# 实验—论文—文件映射

所有路径相对于本包根目录，逐个确切文件见 `paper_applied_intelligence/REPRODUCTION_INDEX.json` 的对应 id。下表是导航，不重新定义样本或统计单位。

| 索引 id | 论文位置 / 图表 | 正式材料入口 | 分析或执行入口 |
|---|---|---|---|
| main | 6.1；Fig.3；主结果、任务级 NPSD 表 | `实验数据/results/main_v6_4`；`external_baseline_repro_v1` | `summarize_closed_main_comparison.py` |
| ablation | 6.2；2×2 消融表 | `实验数据/archive/previous/results/ablation_v6_4_seed8_12`；当前汇总在 `results/ablation_v6_4_seed8_12` | `summarize_v6_3_preexec_ablation.py` |
| main（配对案例） | 6.3；Fig.4 | `main_v6_4/seed_13`；绘图摘录 `UAV_PAPER/frozen_data/case_forest_fog_seed13` | 原始 b0/CDS-MR 行与图注共同解释 |
| selection | 6.4；Fig.5(a) | `实验数据/results/sese_selection` | `summarize_sese_selection_evidence.py` |
| sensitivity | 6.4；Fig.5(b,c) | `实验数据/results/constraint_sensitivity` | `summarize_constraint_sensitivity.py` |
| finite_domain | 6.5；有限候选枚举 | `uav_exp/_paper_evidence_audit/experiment_A_exhaustive_optimality_20260919` | `validate_cds_mr_exhaustive_optimality.py` |
| capability | 6.5；能力排除 | `uav_exp/_paper_evidence_audit/experiment_B_heterogeneous_capability_20260919` | `validate_experiment_b_heterogeneous_capability.py` |
| simple_local | 6.6 前置负对照 | `实验数据/results/local_repair_vs_cds_mr_v1` | `run_simple_local_vs_cds_mr.py` |
| kinematic | 6.6；三例运动学探针表 | `实验数据/results/mechanism_stress_v2_vm_v10` | 同上；不是动力学路径证据 |
| dynamic | 6.6.1；三例动力学机制结果 | `实验数据/results/mechanism_dynamic_scale_v3_ctrl48_final_vm` | 同上；作者确认当前实现 |
| boundary | 6.6.1；六例动力学边界结果 | `实验数据/results/mechanism_boundary_v1_vm` | 同上；作者确认当前实现 |
| grid | 6.6.2；Fig.6 | `实验数据/results/suffix_grid_three_way_formal_v1` | `audit_suffix_grid_formal_run.py`；绘图 `plot_suffix_results.py` |
| random | 6.6.3；Fig.7；随机布局表 | `实验数据/results/b3_independent_task_distribution_formal_v1` | `analyze_b3_independent_distribution.py`；同上绘图 |
| offline | 6.7；同目标离线对照 | `实验数据/results/offline_stability_control_v1` | `offline_stability_control.py` |

除 uav_exp 下两项，分析入口均在 `实验数据/code`；后缀绘图脚本在 `UAV_PAPER/scripts`。

Fig.1 和 Fig.2 是框架/机制示意图，不对应额外统计实验。包内 `paper_applied_intelligence/figures/Fig1.pdf` 至 `Fig7.pdf` 是当前论文实际引用的七幅图。绘图脚本输出用于复核数据图，不承诺逐像素重现作者后续手工调整的文字和位置。未将同名旧图、字体试版或桌面旧备份当作正式图。

主对比汇总采用 `summary_closed_external_overlay.json`；不采用历史 `summary_main_comparison_v6_4_seed13_18.*`。外部基线采用索引指定的 `h_mrta.jsonl` 与 `llm_ape_final.jsonl`，不采用 llm_ape 早期替代文件。固定网格与独立随机布局分别分析，不合并样本；随机正式种子见索引的 `random_formal_seeds`，不纳入 pilot。
