# 约束紧张度敏感性实验

## 目的

这是主对比和消融之外的小规模补充实验。它只检验一个问题：当每个任务
bundle 可用的角色/能力兼容 UAV 候选逐步减少时，CDS-MR 相对于全局重规划
的恢复质量和计划稳定性是否保持趋势。

## 冻结协议

- 输入：v6.4 `recoverable_failure_uncertain` 的 6 个冻结源任务。
- seed：沿用 v6.4 seed 13；同一源任务的三个等级使用同一 episode seed。
- 因素：`constraint_tightness`，等级为 `loose`、`medium`、`tight`。
- 等级含义：
  - `loose`：保留源合同中的所有角色/能力兼容候选 UAV；
  - `medium`：保留首选 UAV 和最多两个兼容备选 UAV；
  - `tight`：保留首选 UAV 和一个兼容备选 UAV；失败 bundle 保证仍有一个备选。
- 方法：`Blind+Global-Replan` 与 `Blind+CDS-MR`。
- 两个方法共享同一个确定性初始计划和同一失败事件；不调用 SeSE，不改变
  初始计划质量，因此该实验只解释修复机制随约束紧张度的变化。
- 指标：RSR、CSR、NPSD、Repair Decision Latency。Mission Completion Time
  仍可从标准记录读取，但只按成功任务统计有效 n。
- 规模：6 × 3 × 2 = 36 条任务级结果；每个约束等级有 6 个配对样本。
- 解释边界：单 seed 的补充趋势证据，不替代主实验的多 seed 结果，不能据此
  宣称对任意规模或所有 UAVBench 任务均鲁棒。

## 文件

- `tasks.jsonl`：18 个固定场景任务。
- `candidate_pools.jsonl`：每个 task-level 一个确定性共享初始计划。
- `manifest.json`：协议、规模和配对键。
- `validation.json`：18 个场景的合同兼容性与可修复性校验。

## 在实验环境中运行

在复现包根目录执行：

```bash
python code/summarize_v6_3_preexec_ablation.py \
  --mode run \
  --input scenarios/constraint_sensitivity/tasks.jsonl \
  --pool scenarios/constraint_sensitivity/candidate_pools.jsonl \
  --output results/constraint_sensitivity/raw_results.jsonl \
  --methods 'Blind+Global-Replan,Blind+CDS-MR' \
  --log-dir results/constraint_sensitivity/logs

python code/summarize_constraint_sensitivity.py \
  --input results/constraint_sensitivity/raw_results.jsonl \
  --output results/constraint_sensitivity/summary.json \
  --markdown results/constraint_sensitivity/summary.md
```

运行器按 task 记录 `runner_error` 并继续下一个 task；单个超时不会中断整批。
