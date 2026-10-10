# 固定参数网格的正式结果

本目录包含 90 个冻结实例、三方法各 90 条正式运行记录。正式指标审计入口为 formal_metric_audit.json，其 scope=formal_run；metric_audit.json 的 scope=preflight_only 为原审计器留下的范围字段。

2026-10-03 元数据勘误：formal_metric_audit.json 的 audit_note 将原字段写成 pilot_only，实际应为 preflight_only。此勘误只修正备注的解读，不改变正式范围、数值或 PASS 检查。原审计文件字节及哈希均保留。

全部范围勘误、检查与源哈希见 ../../REPRODUCIBILITY_CLOSEOUT_20261003.md 及 ../../provenance/reproducibility_closeout_20261003.json。
