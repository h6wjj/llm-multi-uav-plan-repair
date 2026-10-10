# SeSE independent plan-selection evidence

Design: 6 tasks × 5 held-out seeds × 2 selectors = 60 selection records; K=5 shared candidates per task/seed.

| Selector | IP-CVR ↑ | Independent utility ↑ | Selection regret ↓ | Initial execution success ↑ | Planning latency (s) ↓ | Tokens ↓ |
|---|---:|---:|---:|---:|---:|---:|
| Blind | 0.9667 | 0.4667 | 0.5333 | 0.9333 | 108.2211 | 15448.1 |
| SeSE | 1.0000 | 1.0000 | 0.0000 | 1.0000 | 108.2212 | 15448.1 |

## Paired SeSE − Blind differences

| Metric | n | Mean Δ | 95% bootstrap CI | sign-flip p | Cohen dz |
|---|---:|---:|---:|---:|---:|
| initial_plan_contract_valid | 30 | 0.0333 | [0.0000, 0.1000] | 1.000000 | 0.1826 |
| independent_selection_utility | 30 | 0.5333 | [0.3944, 0.6750] | 0.000020 | 1.3205 |
| selection_regret | 30 | -0.5333 | [-0.6750, -0.3944] | 0.000020 | -1.3205 |
| actual_initial_execution_success | 30 | 0.0667 | [0.0000, 0.1667] | 0.500000 | 0.2628 |

Gate promotion rate: 0.7333; abstention rate: 0.0000.
Candidate signature count: min=5, mean=5.0000.
Integrity validation: PASS.

Scope: this is independent SeSE selection evidence, not a post-failure repair experiment and not a physical-model heterogeneity experiment.
