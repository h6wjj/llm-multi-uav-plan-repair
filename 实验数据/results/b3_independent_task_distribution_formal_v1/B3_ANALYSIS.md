# B3 独立任务分布测试：正式结果

正式样本 90 例；链/分支/汇合各30例。正式指标审计：PASS。
冻结任务 SHA-256：e6aa209faa87ca6f918a9209c74a124f6bced16860cfd7b15a268d34005ffb98。

## 三方法结果

| 方法 | 恢复且严格通过 | 实测路径均值±SD (m) | 完成时间均值±SD (s) | NPSD均值 | 求解时间中位数 (ms) |
|---|---:|---:|---:|---:|---:|
| Immediate local | 90/90 | 6.0814±1.5466 | 99.399±21.159 | 0.3356 | 0.080 |
| CDS-MR | 90/90 | 5.5471±1.4049 | 92.399±19.687 | 0.3356 | 0.110 |
| Global total-route | 90/90 | 5.1304±0.9797 | 86.760±13.835 | 0.5115 | 0.143 |

## CDS-MR相对即时局部：获益/持平/劣化

双方严格成功时，以实测路径相对变化为主：改善≥1%记获益，恶化≥1%记劣化，其余记实际持平；只有一方严格成功时按可行性分类。共同失败与技术无效单独报告。

| 类别 | 数量 | 比例 | Wilson 95% CI |
|---|---:|---:|---:|
| CDS-MR获益 | 30/90 | 33.3% | [24.5%, 43.6%] |
| 实际持平 | 59/90 | 65.6% | [55.3%, 74.6%] |
| CDS-MR劣化 | 1/90 | 1.1% | [0.2%, 6.0%] |
| 双方均未严格恢复 | 0/90 | 0.0% | [0.0%, 4.1%] |
| 缺失/技术无效 | 0/90 | 0.0% | [0.0%, 4.1%] |

## 连续配对差值

方向为即时局部减比较方法，正数有利于比较方法；仅在两方法均严格成功的配对上计算。区间为预注册的10,000次 paired bootstrap percentile 95% CI。

| 比较 | 指标 | n | 平均差 | SD | paired 95% CI |
|---|---|---:|---:|---:|---:|
| CDS-MR vs Immediate local | Executed path (m) | 90 | 0.534363 | 0.888059 | [0.359027, 0.719526] |
| CDS-MR vs Immediate local | Completion time (s) | 90 | 7.000231 | 11.626649 | [4.672633, 9.488304] |
| CDS-MR vs Immediate local | Estimated suffix cost (m) | 90 | 0.581669 | 0.960071 | [0.393648, 0.783163] |
| CDS-MR vs Immediate local | Solver time (s) | 90 | -0.000041 | 0.000066 | [-0.000056, -0.000029] |
| CDS-MR vs Immediate local | Decision latency (s) | 90 | -0.000157 | 0.001466 | [-0.000468, 0.000142] |
| Global total-route vs Immediate local | Executed path (m) | 90 | 0.950983 | 0.974279 | [0.751500, 1.154967] |
| Global total-route vs Immediate local | Completion time (s) | 90 | 12.639120 | 12.928013 | [10.048345, 15.259363] |
| Global total-route vs Immediate local | Estimated suffix cost (m) | 90 | 0.992532 | 1.019191 | [0.788419, 1.202368] |
| Global total-route vs Immediate local | Solver time (s) | 90 | -0.000068 | 0.000046 | [-0.000078, -0.000059] |
| Global total-route vs Immediate local | Decision latency (s) | 90 | -0.000065 | 0.001280 | [-0.000333, 0.000195] |
| Global total-route vs CDS-MR | Executed path (m) | 90 | 0.416620 | 0.745496 | [0.267049, 0.577175] |
| Global total-route vs CDS-MR | Completion time (s) | 90 | 5.638889 | 10.020451 | [3.658484, 7.749138] |
| Global total-route vs CDS-MR | Estimated suffix cost (m) | 90 | 0.410863 | 0.739513 | [0.265660, 0.573820] |
| Global total-route vs CDS-MR | Solver time (s) | 90 | -0.000027 | 0.000072 | [-0.000041, -0.000011] |
| Global total-route vs CDS-MR | Decision latency (s) | 90 | 0.000091 | 0.001613 | [-0.000239, 0.000434] |

## 结构分层与局部编辑失败

| 结构 | n | 获益 | 持平 | 劣化 | 双方失败 | 缺失/无效 |
|---|---:|---:|---:|---:|---:|---:|
| branch | 30 | 13 | 17 | 0 | 0 | 0 |
| chain | 30 | 5 | 24 | 1 | 0 | 0 |
| merge | 30 | 12 | 18 | 0 | 0 | 0 |

即时局部在本90例中无严格恢复失败，因此该生成分布未观察到局部编辑失败区域；这不表示其他分布中不存在失败区域。

## 解释边界

这90例是正式运行前按独立随机几何生成器冻结、未按方法获胜条件筛选的任务；链、分支、汇合比例人为平衡。获益比例只描述该生成器下的样本，不能解释为实际任务的普遍发生率。仿真基于 PyBullet/VelocityAviary 航点投影，不代表实飞，也未包含天气、禁飞区和移动障碍的完整物理约束。

输入、方法结果流和指标审计哈希记录在 B3_ANALYSIS.json；样本有效性与抽样种子记录在 generation_audit.json。
