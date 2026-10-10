# 独立随机布局的正式结果

本目录为 90 个正式布局、三方法各 90 条运行记录；结果统计入口为 B3_ANALYSIS.json。输入、生成规则与随机种子见 ../../scenarios/independent_task_distribution_b3_v1/。

2026-10-03 归档处置：冻结清单指向的 build_b3_independent_tasks_v1.py 已补齐，副本 SHA-256 与冻结清单及 code/ 中源码均相同。此操作没有重新生成输入。历史 B3_EXPERIMENT_AUDIT 的“生成器副本缺失”警告由本次处置说明承接，历史审计保留原文。

Pilot 与正式集共有三个相同 task_id 标签，但种子不重叠。跨目录关联必须使用数据划分、run_seed 和 task_id 的组合；正式分析只读取本目录 90 例。标签不改写，以保持原记录可追溯。

formal_metric_audit.json 的 scope=formal_run，其 audit_note 中 pilot_only 是备注笔误；对应原 metric_audit.json 实际 scope=preflight_only。正式与原审计的字节及哈希保留，具体勘误见 ../../REPRODUCIBILITY_CLOSEOUT_20261003.md。
