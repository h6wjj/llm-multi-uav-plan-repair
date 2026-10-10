# Fig.1 初版装配审查

## 结论

本版已具备论文框架图的基本语义闭环，可作为正文写作和后续精修的第一版，不作为不经修改即可投稿的终版。

## 已锁定的叙事

1. Planning：自然语言任务及约束进入 LLM，生成候选结构化计划；结构质量检查仅为可选虚线支路，默认候选可直接形成初始执行计划。
2. Execution：角色/能力接口表达异构性，并通过唯一的 Agent4Drone-compatible bridge 执行与跟踪状态；无失效时直接继续执行，发生可恢复失效时转入 Recovery。
3. Recovery：失效后只接收失效无人机、实时状态与剩余任务后缀；保持已完成前缀不变，只在必要范围内编辑；只提交一个已选修复方案，不进行候选重试或回滚；随后经同一执行桥继续运行。

## 与 S0 契约的一致性

- 未出现 SeSE、CDS-MR 或 B0 等内部名称。
- 未使用合同隐喻。
- 未画失效后的 LLM 在线多轮闭环。
- 未加入强化学习、训练或数据库模块。
- 未为不同无人机复制多套流程；异构性只通过角色/能力接口表达。
- Fig.2 专属的集合、闭包、兼容矩阵、六步内部流程和词典序目标均未塞入 Fig.1。

## 视觉检查

- 画布比例为 1100:630；按 174 mm 通栏插入时高度约 99.7 mm。
- Arial/Helvetica；最小标签按最终宽度折算约 8.1 pt。
- 蓝、绿、橙、紫同时由阶段位置、边型和节点语义辅助编码，灰度打印不只依赖色相；已用 150 ppi 灰度渲染复核，节点边界、箭头及可选虚线仍可辨。
- 无渐变、阴影或位图图标；SVG 为主源，PNG 仅供预览。
- FigureSpec 与 CSV 已对齐：11 个节点、12 条边，无缺项或额外关系。

## 下一轮可改进项

- 将通用矩形替换为少量自绘任务/UAV语义符号，但仍保持矢量和克制。
- 在正式图注中说明角色异构由 task/UAV capability tags 表示，而仿真物理动力学采用共同 CF2X 模型。
- 结合正文最终术语，统一 `remaining suffix`、`locked prefix` 和 `scoped edits` 的定义。

## 建议图注（英文初稿）

**Fig. 1** Overall workflow of LLM-assisted task planning and intent-preserving minimal repair for a heterogeneous multi-UAV system. A natural-language mission is converted into candidate structured plans, with an optional pre-execution structural-quality branch. The selected plan is executed through a shared Agent4Drone-compatible bridge under role/capability constraints. Following a recoverable UAV failure, the method freezes the completed prefix, edits only the necessary portion of the remaining suffix, commits one selected repair plan, and resumes execution through the same live bridge. Heterogeneity is represented by task/UAV capability tags; all simulated vehicles use the common CF2X dynamics model.
