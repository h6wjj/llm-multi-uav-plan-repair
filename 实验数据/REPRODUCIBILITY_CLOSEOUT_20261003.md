# 当前论文复现材料收尾（2026-10-03）

本文件补齐 2026-09-27 论文证据审计留下的运行环境、审计范围备注和小尺度机制探针汇总解释，并处置随机布局生成器副本缺失。原实验 JSONL、输入、统计汇总和历史审计文件保留原始字节；本次新增资料采用单独文件名。

## 使用入口

- 当前主对比：results/main_v6_4/summary_closed_external_overlay.json；历史主汇总的优先级见同目录 README.md。
- 当前正文三个小尺度探针：results/mechanism_stress_v2_vm_v10/；v7/v8 是早期调试结果，route_cost_final 是另一组探针，不替代该目录。
- 真实电机动力学机制案例：results/mechanism_dynamic_scale_v3_ctrl48_final_vm/；边界：results/mechanism_boundary_v1_vm/。
- 固定参数网格：results/suffix_grid_three_way_formal_v1/；独立随机布局：results/b3_independent_task_distribution_formal_v1/。
- 本次核验记录：provenance/reproducibility_closeout_20261003.json；在复现包根目录运行 `python code/verify_reproducibility_closeout.py`。脚本通过自身位置定位输入，也可用绝对脚本路径从其他目录运行。命令读取归档并向终端输出 JSON，不执行仿真、不安装软件、不覆写输入。

56 项确定性检查全部通过，覆盖原文件哈希、两份正式审计的范围与布尔检查、六条机制探针的终态和约束字段、三对失效状态、生成器副本、正式输入和种子隔离。本轮语义复审调用未返回审计结果，故此记录只声明确定性检查通过，不宣称新的独立审稿验收。

## 运行环境记录

历史软件依据为 provenance/environment.txt 和正式运行清单：Python 3.12.3、PyBullet 3.2.7、gym-pybullet-drones 提交 24fccff4da746badb6eaa9f5055cd5d9b6613160。固定网格与随机布局的 run_manifest.json 还记录 Python 3.12.3 与 Linux 7.0.0-31-generic。

2026-10-03 00:43:28 UTC 通过只读 SSH 采集虚拟机配置，保存在 provenance/vm_environment_20261003.json：Ubuntu 24.04.4 LTS、VMware、8 个逻辑 CPU、来宾报告 i5-13400F、MemTotal=16,324,000 KiB（约 15.57 GiB），显卡列表为 VMware SVGA II。该快照与正文所列配置一致，但采集发生在实验之后，不能倒推证明各历史运行时硬件配置未变化。

当前系统 Python 仍为 3.12.3，但 /home/h/uav_exp/venv 中 Python 已为 3.14.6，pip 未查到 gym-pybullet-drones 包登记。此 venv 不能直接作为精确重跑环境。重跑应依据历史版本记录另建 Python 3.12.3 环境，并检查平台提交与各运行清单中的行为脚本哈希；本次收尾没有更改虚拟机环境或重跑实验。

## 两份正式审计备注的勘误

固定参数网格和独立随机布局各自的 metric_audit.json 均实际写有 scope=preflight_only；各自 formal_metric_audit.json 则写有 scope=formal_run、90 个任务和全部通过的检查。正式封装的 audit_note 将原范围误称为 pilot_only，准确说明应为：原审计保留 preflight_only 字段，正式范围由事后 formal_run 封装明确。

这一笔误也存在于归档封装脚本的备注字符串。为保持代码与历史输出校验值对应，本次以独立说明承接，未修改原封装 JSON 或生成脚本。两份正式结果目录的 README.md 均指向本勘误，数值、审计布尔项和正式范围不受此备注修正影响。

## 小尺度机制探针的恢复字段

正文使用的是 mechanism_stress_v2_vm_v10：三个实例、两方法共六条记录，NPSD 均为 0.333333。原输入 metadata 只有 condition_label，没有评价器识别的 condition；归档 common_metrics 按 metadata.condition 判断是否为 recoverable_failure，故原行级 recovery_success_rate_item 为 null。汇总将 True 数量计为 0，并留下 null 的恢复率；summary.md 又展示成 0/3，容易被误读为任务恢复失败。

本次逐行读取原输出，确认预定 U1/B2 失效发生，终态 task_success=true，六个 contract_checks 全为 true，strict_contract_satisfied=true，三对失效状态一致。按归档行的 recoverable_failure 条件、首次失效与终态成功重算，两方法各为 3/3。即时局部法均选 U2，CDS-MR 均选 U3。

这是一份事后派生更正，源文件仍保留空的原恢复字段。正文中“六次完成并通过六项检查”有终态证据支持；不能把原汇总的零个 True 当作六次恢复失败，也不能将本次更正包装成新增实验。这组小尺度推进仅支持决策辨别，动力学路径与时间收益使用后续动力学目录。

## 独立随机布局归档处置

freeze_manifest.json 指向的 build_b3_independent_tasks_v1.py 已从 code/ 中哈希相同的源码补齐至 scenarios/independent_task_distribution_b3_v1/。副本 SHA-256 为 a2bfc5eaf0a813bd0da59db761964877aba86d44e2d0fa05b45a3533d40de301，与冻结清单一致；正式 tasks.jsonl 未重新生成。

Pilot 与正式集共享三个 task_id 标签，但 run_seed 集合不重叠。跨目录关联明确使用数据划分、run_seed、task_id 三元组，正式分析只使用正式目录。原标签与历史审计不改写；“副本缺失”警告已由本次处置承接，标签重用的使用规则与外部有效性边界继续保留。

## 原文件保护与剩余边界

机器记录保存全部核验输入和脚本 SHA-256；其中八个重点原文件的字节校验与收尾前值一致。恢复字段更正不影响现有主对比、Fig.3--Fig.7 或动力学统计；补齐生成器副本不增加样本。

当前仍无同期历史硬件快照；实时采集不能补造此证据。当前 venv 与原运行环境有差异，精确重跑前须恢复历史环境。这两点已明确记录，不把材料说明齐全等同于已完成一次全包重跑。正文未修改；补充材料只同步了归档问题的处置说明。未主动运行 LaTeX 编译。
