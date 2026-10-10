# 当前论文的小尺度机制探针

本目录对应当前正文“机制压力测试”的三个确定性实例，采用运动学推进，只支持接管决策辨别。它与早期未成功到达预定故障的 v7/v8 目录、后来的真实电机动力学案例，以及 NPSD=0.500 的 route_cost_final 探针分别保存，不能混用。

## 原汇总字段勘误（2026-10-03）

原 summary.json 的 recoverable_success_count=0 和 recoverable_success_rate=null 不能解释为六次运行均恢复失败。三份输入缺少 metadata.condition；归档评价器依此字段识别可恢复失效条件，逐行 recovery_success_rate_item 因而为空，而汇总又把空值计为零个 True。

逐行 final_execution_record.gym_metrics.task_success、standard_metrics.strict_contract_satisfied 和六个 contract_checks 在两方法各三条记录中均为 true；预定 U1/B2 失效已经发生、恢复被执行，三对失效状态相同。按“失效发生后完成任务”的条件重算，两方法各为 3/3，六项检查全部通过，NPSD 均为 0.333333；即时局部法选择 U2，CDS-MR 选择 U3。正文使用终态完成和严格检查说明，不以空的原恢复率字段作为主结果依据。

原 JSONL、summary.json、summary.md 与清单保留其原始字节。更正口径、源文件 SHA-256 和只读检查见 ../../REPRODUCIBILITY_CLOSEOUT_20261003.md、../../provenance/reproducibility_closeout_20261003.json。重新核验命令为 python ../../code/verify_reproducibility_closeout.py；该命令只输出 JSON，不执行仿真。

本次更正属于事后元数据解释，不是新增运行，不增加独立任务样本，也不用于证明动力学时间收益。
