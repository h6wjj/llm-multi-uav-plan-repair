# Fig.1 FigureSpec v1 构建记录

- 语义依据：`figure-studio-runs/fig1_intent_preserving_uav/outputs/S0-paper-foundation/s0-semantic-precision-contract.json`
- 风险依据：`figure-studio-runs/fig1_intent_preserving_uav/outputs/S0-paper-foundation/framework-figure-risk-register.md`
- 图形源：`specs/Fig1_framework_v1.figurespec.json`
- 主输出：`figures/Fig1_framework_v1.svg`
- 预览：`figures/Fig1_framework_v1_preview.png`，仅用于检查，不作为投稿图
- 设计尺寸：1100 × 630 viewBox；按 174 mm 宽插入时高度约 99.7 mm
- 字体：Arial/Helvetica；基础字号 22 px；节点副标签和边标签为 19 px，按 174 mm 缩放后约 8.1 pt
- 物理动力学边界说明保留在图注，不用低于 8 pt 的图内小字呈现
- 语义约束：单一执行桥；执行前质量控制为可选虚线支路；失效后不返回 LLM；单次提交指一个修复方案承诺，不是一个 API 请求；物理模型不作异构声明
- 运行时说明：本机已安装的 `figure-spec` 缺少 canonical renderer，本次从其公开上游安装临时、未修改的兼容 runtime 到 `tmp/skill-runtime/figure-spec-runtime/` 后执行；JSON 和 SVG 均保留在项目目录中，不依赖临时 runtime 才能查看或编辑
