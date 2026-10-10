# 运行环境与版本

正式历史软件记录原样保存于 `实验数据/provenance/environment.txt`；运行清单另记录各组 Python/平台与协议。记录包括 Python 3.12.3、PyBullet 3.2.7、gym-pybullet-drones 提交 `24fccff4da746badb6eaa9f5055cd5d9b6613160`。

Ubuntu 24.04.4 LTS、VMware、8 vCPU、约 15.57 GiB 内存的事后快照在 `vm_environment_20261003.json`。它不是同期硬件测量，也不冒充作者本次测试的完整环境清单。

## 层级

1. 打包检查：`tools/check_package.py` 仅需 Python 标准库；本轮不安装任何包。
2. 归档统计和绘图：依脚本导入需要 numpy、scipy、matplotlib 等，使用历史环境记录中的对应版本。
3. 仿真：另需 pybullet、gymnasium、gym-pybullet-drones、fastapi、uvicorn、requests 等；建议在 Ubuntu 环境中执行。
4. 重新生成 LLM 候选/在线基线：另需 Agent4Drone 的 LangChain/OpenAI 相关依赖、作者自备 API 凭据及原协议配置。远程模型/服务可能变化；不能承诺重新采样逐字生成原候选。冻结候选池的统计复算无需重新采样。

可先在 Python 3.12.3 环境按历史 environment.txt 恢复所需依赖，再从本包 `实验数据/platform/gym-pybullet-drones` 安装本地平台（例如 `python -m pip install -e "实验数据/platform/gym-pybullet-drones"`）。依赖解决和安装尚未在本轮验证，environment.txt 是来源记录，不是助手测试通过的 lockfile。不要盲目把全部 CUDA/训练包作为 CPU 复算的必需条件。

新运行工具自动将 `实验数据/code`、`实验数据/code/experiment/scripts` 依次加入 PYTHONPATH，以承接 `filter_executable_tasks` 等现有导入；不修改算法源码。所有新输出写入 `generated/<唯一批次>/workspace`，与冻结结果隔离。

代码/数据中历史绝对路径属于来源字段，真实定位使用包内相对索引。重新运行需要的 CLI 参数由 RUNBOOK 明确给出；不要把历史 VM 绝对路径直接当作当前包内文件。
