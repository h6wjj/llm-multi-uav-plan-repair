# Fig.1 V2 线框审查

## 1. 审查结论

V2 线框通过当前阶段审查，可以作为论文 Fig.1 的第二版结构稿。它已从 V1 的“大分组流程图”转为更接近参考论文的“轻量三阶段系统架构图”。本次通过不等于最终投稿锁定；后续仍可对图标和字重做微调。

## 2. 审查评分

| 维度 | 评分 | 判断 |
|---|---:|---|
| Clarity | 8.6/10 | 不读正文也能识别语言规划、共享执行、失效恢复和续执行 |
| Readability | 8.4/10 | 174 mm 通栏下最小文字约 8.2 pt；无文字溢出和节点遮挡 |
| Semantic accuracy | 9.0/10 | 与 S0 契约、Fig.1 CSV 和当前实现边界一致 |
| Journal fit | 8.2/10 | 白底、矢量、低饱和、短标签，接近 Applied Intelligence 样例的紧凑模块图风格 |

## 3. 线框路径审查

### 主链

`Natural-language mission -> LLM candidate-plan generation -> Selected structured initial plan -> Agent4Drone-compatible execution bridge -> Continue through the same bridge -> Mission completed or recovery failed`

结果：主链连续；Planning 到 Execution 的交接为水平箭头；Execution 内部为垂直时序；没有跨节点连线。

### 可选支路

`LLM candidate-plan generation -- Optional quality check -- Selected structured initial plan`

结果：仅使用紫色虚线，节点改为小型圆角框，不再使用决策菱形；其面积、位置和线型均表明它是执行前附属支路，而不是本文核心贡献。

### 故障与恢复链

`Execution bridge -> Failure-state snapshot -> Intent-preserving minimal repair -> Commit one selected repair plan -> Continue through the same bridge`

结果：橙色曲线沿阶段外侧进入故障快照，不返回 LLM；恢复路径在右侧自上而下，最终水平返回中央同一续执行入口。没有画候选重试、回滚或在线多轮 LLM 闭环。

### 异构性入口

`Role/capability interface -> Agent4Drone-compatible execution bridge`

结果：Lead、Map、Scout、Relay 只作为角色/能力标签汇聚到一个接口，没有复制四套 UAV 流程；没有暗示四种不同物理机体。

## 4. 节点边界审查

| 节点 | 支持来源 | 是否越界 |
|---|---|---|
| Natural-language mission | 论文任务定义与 Fig.1 数据 | 否 |
| LLM candidate-plan generation | 规划流程与候选计划数据 | 否 |
| Optional quality check | 执行前结构质量控制支线 | 否；已视觉降级 |
| Selected structured initial plan | 初始计划产物 | 否 |
| Role/capability interface | task/UAV capability tags 与系统接口 | 否；未宣称动力学异构 |
| Agent4Drone-compatible execution bridge | `gym_uav_bridge.py` 与在线执行代码 | 否 |
| Failure-state snapshot | failed UAV、live state、remaining suffix | 否 |
| Intent-preserving minimal repair | 已完成前缀冻结和必要范围编辑 | 否；未展开 Fig.2 内部机制 |
| Commit one selected repair plan | 单一已选方案提交语义 | 否；未画成单一 HTTP 请求 |
| Continue through the same bridge | 同一 live bridge/session | 否 |
| Terminal outcome | 完成或恢复失败 | 否 |

## 5. 视觉层级审查

- 三个阶段标题位于同一水平基线，不再依赖高度不一致的大背景框。
- 主箭头统一为炭灰色；橙色只表示故障；蓝色只强调修复和修复后返回；紫色只表示可选支路。
- 输入/产物使用直角矩形，核心过程使用圆角矩形，终点使用椭圆，形状语法基本一致。
- 唯一长曲线是执行桥到故障快照的异常路径，没有穿过其他节点。
- 图中没有箭头标签白底块，避免参考图中常见的遮挡和碎片化问题。

## 6. Applied Intelligence 装配检查

- 画布：1200 × 700；按 174 mm 通栏插入时约为 174 × 101.5 mm。
- 字体：Arial/Helvetica；最小正文标签为 20 px，按最终尺寸约 8.2 pt。
- 主输出为可编辑 SVG；PDF 已核验嵌入 Arial 与 Arial Bold；EPS 由矢量 PDF 转换。
- 无渐变、阴影、照片、位图图标或 3D 效果。
- 已完成 150 ppi 灰度线框复核：虚线支路、故障曲线、正常路径和修复返回箭头均可辨。

## 7. 尚未阻塞定稿的改进点

1. FigureSpec 渲染器将主要节点标题统一为粗体，V3 可通过扩展样式或 SVG 精修降低部分产物节点的字重。
2. 当前异构性仍以角色文字为主；若需要，可加入最多三个极简 UAV 轮廓，但不能形成 survey 式图标海报。
3. 故障曲线可以在终稿中改为严格正交的外侧折线路由；现有曲线不交叉，已满足 V2 线框可读性。
4. `remaining suffix`、`locked prefix` 和 `scoped edits` 应在正文术语锁定后统一为最终写法。

## 8. 审查决定

状态：`V2_WIREFRAME_PASS_WITH_MINOR_POLISH`

允许进入正式图形精修；不需要回退论证链，也不需要新增方法模块。

## 9. 建议图注

**Fig. 1** Overall workflow of LLM-assisted task planning and intent-preserving minimal repair for a heterogeneous multi-UAV system. A natural-language mission is converted into candidate structured plans, with an optional pre-execution quality check. The selected plan is executed through a shared Agent4Drone-compatible bridge under role/capability constraints. After a recoverable UAV failure, the method captures the live failure state, locks the completed prefix, edits only the necessary part of the remaining plan, commits one selected repair plan, and continues execution through the same bridge.
