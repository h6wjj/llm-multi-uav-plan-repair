# Sources and licensing scope

本目录为混合来源的复现材料。下列第三方许可证保留原文本，不能把本仓库统一描述为 MIT 或 Apache 2.0。

## UAVBench

- 来源：https://github.com/maferrag/UAVBench
- 论文：UAVBench: An Open Benchmark Dataset for Autonomous and Agentic AI UAV Systems via LLM-Generated Flight Scenarios。
- 作者：Mohamed Amine Ferrag、Abderrahmane Lakas、Merouane Debbah。
- 原许可：Apache License 2.0，原文件在 `实验数据/dataset/UAVBench/LICENSE`；原 README 同目录保留。
- 本实验采用来源任务并进行结构/航点映射；映射程序和冻结输入保留转换记录。映射不表示来源天气/交通等全部因素得到物理模拟。不附带源论文 PDF 或完整源数据集 ZIP。

## gym-pybullet-drones

- 来源：https://github.com/learnsyslab/gym-pybullet-drones
- 许可：MIT，原文本及作者信息保留于 `实验数据/platform/gym-pybullet-drones/LICENSE`。
- 本目录的平台及资源来自本地实验材料；历史提交见 ENVIRONMENT.md，文件版本另由 MANIFEST.json 登记。

## MultiUAV-Plat / Agent4Drone

- 原项目许可：GNU GPL v3，原文本在 `实验数据/platform/MultiUAV-Plat/LICENSE`。
- 保留 Agent4Drone 源代码、提示模板及原文档。本地适配源码不声明与上游未经修改的版本逐字一致；运行记录及本目录文件清单分别保留其来源。
- 本仓库的新说明不取代 GPL 对其覆盖的软件所授予的权利或施加额外限制。

## 作者代码、派生输入、结果与图表

本轮没有替作者及通讯作者选择新的许可证，亦不将第三方许可自动套用到全部材料。已存在许可的文件遵循各自许可；其他材料没有在本次整理中新增授权条款。后续作者应明确自己的代码、结果和派生数据的许可，以及相关集成代码的适用范围。

公开可访问与指定开源许可证不是同一件事。缺少新许可证的状态明确登记，不使用“全仓库 MIT”等误导性标签。
