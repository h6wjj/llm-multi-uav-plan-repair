# 五图五表数据包

生成日期：2026-09-13

绘图前必须先阅读：[00_五图统一视觉规范与装配审查.md](./00_五图统一视觉规范与装配审查.md)。该文件冻结了五张主图的颜色、字体、版式、数据字段、统计表达、图注职责和主张边界。

本目录只保存论文 Fig.1--Fig.5、Table 1--Table 5 所需的规范化数据和绘图结构，不修改 `实验数据/` 中的任何原始记录。CSV 使用 UTF-8 BOM，便于 Excel、Python、R 和绘图软件直接读取；JSON 使用 UTF-8。

## 一、必须遵守的主结果口径

- Fig.3 和 Table 4 的唯一权威汇总是：`实验数据/results/main_v6_4/summary_closed_external_overlay.json`。
- 不得再使用旧文件 `summary_main_comparison_v6_4_seed13_18.csv` 中的外部基线数值；其中的 H-MRTA 和 LLM-assisted 行早于证据链闭合。
- 主结果行级数据由六个 seed 目录中的 B0/完整方法记录，以及闭合后的 `h_mrta.jsonl`、`llm_ape_final_rebuilt.jsonl` 合并得到。
- 每种方法包含 36 条可恢复失效记录，共 144 行。

## 二、按图使用

### Fig.1 总体框架

- `fig1_总体框架/fig1_nodes.csv`：框架节点、阶段和是否为必要组成。
- `fig1_总体框架/fig1_edges.csv`：信息流、实线主链和虚线可选支线。

该图不使用实验数值。执行前选择器必须画成可选支线；修复方案只能单次提交在线桥。

### Fig.2 最小修复机制

- `fig2_最小修复机制/fig2_mechanism_steps.csv`：六个机制阶段及输入输出。
- `fig2_最小修复机制/fig2_set_definitions.csv`：集合定义和 Forest Fog 案例成员。
- `fig2_最小修复机制/fig2_objective_terms.csv`：词典序目标的五级优先顺序。

该图用于说明完成前缀、直接影响范围、依赖检查范围和实际编辑范围的区别，不能画成多个候选依次在在线桥中试错。

### Fig.3 主对比

- `fig3_主对比/fig3_main_rows_recoverable.csv`：144 条行级结果，适合散点、箱线、雨云和任务分面。
- `fig3_主对比/fig3_main_summary.csv`：方法—指标长表，包含权威均值、标准差、有效 n，以及从闭合行级记录确定性重采样得到的 95% CI。
- `fig3_主对比/fig3_paired_statistics.csv`：本文方法相对三种基线的配对效应、CI、符号检验和 Cohen's dz。
- `fig3_主对比/fig3_task_level_summary.csv`：六个固定任务分别跨 seed 的结果，用于检查任务聚类和异质性。

推荐面板：RSR/CSR、NPSD、修复延迟对数轴、成功条件任务时间。任务时间不能给失败记录补零；跨适配器延迟只能解释为端到端观测。

### Fig.4 Forest Fog 案例

- `fig4_案例分析/fig4_case_overview.json`：案例标识、失效对象、检查范围、编辑范围和两方法指标。
- `fig4_案例分析/fig4_bundle_assignment_comparison.csv`：四个任务束在原计划、H-MRTA 和本文修复中的拥有者变化。
- `fig4_案例分析/fig4_waypoints.csv`：每个航点坐标、失效前是否完成及修复后拥有者。

该案例使用规范 `bundle_i` 标识；原始 `uav_i` 字段只作为拥有者显示。单案例只解释机制，不承担总体性能证明。

### Fig.5 选择器与约束敏感性

- `fig5_选择器与约束/fig5a_selector_rows.csv`：60 条选择器行级记录。
- `fig5_选择器与约束/fig5a_selector_summary.csv`：Blind 与结构化选择的指标汇总。
- `fig5_选择器与约束/fig5a_selector_paired_statistics.csv`：选择效用、遗憾等配对统计。
- `fig5_选择器与约束/fig5bc_constraint_rows.csv`：36 条约束紧张度行级记录。
- `fig5_选择器与约束/fig5bc_constraint_summary.csv`：Loose/Medium/Tight × 两方法的汇总。
- `fig5_选择器与约束/fig5bc_constraint_paired_deltas.csv`：各紧张度下最小修复减全局重分配的配对差。

当前约束敏感性只有 seed 13，每个“等级—方法”只有六个固定任务，必须标为条件性趋势。正式终稿若补充真正排除候选的多种子角色/能力实验，应重新生成此部分，不能把当前数据写成异构筛选收益。

## 三、按表使用

- `tables/table1_symbols.csv`：Table 1 符号、状态与集合定义。
- `tables/table2_mechanism_functions.csv`：Table 2 功能阶段、输入、核心操作、输出及边界；不按旧模块名称组织。
- `tables/table3_experiment_setup.csv`：Table 3 平台、任务、seed、失效协议、模型和公平性边界。
- `tables/table4_main_results.csv`：Table 4 主结果宽表。
- `tables/table4_paired_statistics.csv`：Table 4 配对效应统计。
- `tables/table5_ablation_summary.csv`：Table 5 四臂消融汇总。
- `tables/table5_ablation_paired_statistics.csv`：Table 5 消融配对差。
- `tables/table5_simple_local_summary.csv`：Table 5 simple-local 负对照汇总。
- `tables/table5_simple_local_pairs.csv`：36 条同失效状态负对照配对。

## 四、方法名称字段

所有实验 CSV 同时保留：

- `raw_method`：原始实验记录名称，用于追溯；
- `display_method`：论文图表建议名称，避免把内部模块名直接写入正文。

不要删除 `raw_method`，否则难以回查原始日志。

## 五、数据边界

1. 主实验只要求 GNSS、允许全部角色，三个在线替代 UAV 均满足资格，因此不能用主实验证明角色/能力筛选有效。
2. 所有无人机使用共同 CF2X 物理模型，不能外推到真实机型动力学异构。
3. 36 条记录是六个固定任务各重复六个 seed，不是 36 个独立任务。
4. H-MRTA 和 LLM-assisted 是平台适配实现，不是官方 runtime 复现。
5. simple-local 与结构化修复在当前主要结果指标上未被区分；延迟没有预设等效界，不能声称统计等效。

## 六、完整性与复现

- `manifest.json` 保存 25 个来源文件和 28 个派生数据文件的 SHA-256、记录数及口径警告。
- `validation_report.json` 记录行数、共享配对键、K=5 候选池及同失效状态负对照等自动校验结果。
- `生成脚本/build_figure_table_data.py` 是唯一生成入口。

在项目根目录重新生成：

```powershell
python "图表数据/生成脚本/build_figure_table_data.py" "." "图表数据"
```

脚本会核对主结果每种方法是否恰好有 36 条记录，并验证行级均值、标准差与闭合权威汇总一致；不一致时直接终止，不生成静默替代结果。
