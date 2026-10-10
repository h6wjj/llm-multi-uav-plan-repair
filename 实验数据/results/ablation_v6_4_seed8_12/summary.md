# v6.4 shared-candidate-pool ablation

Failure stratum: recoverable_failure_uncertain; four arms share one candidate pool per task and seed.

| Method | RSR ↑ | CSR ↑ | NPSD ↓ | Repair latency ↓ | Mission time ↓ |
|---|---|---|---|---|---|
| Blind+Global-Replan | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.650 ± 0.190 (n=30) | 0.012 ± 0.005 (n=30) | 0.339 ± 0.063 (n=30) |
| SeSE+Global-Replan | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.650 ± 0.190 (n=30) | 0.011 ± 0.006 (n=30) | 0.339 ± 0.063 (n=30) |
| Blind+CDS-MR | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.433 ± 0.048 (n=30) | 0.011 ± 0.005 (n=30) | 0.339 ± 0.063 (n=30) |
| SeSE+CDS-MR | 1.000 ± 0.000 (n=30) | 1.000 ± 0.000 (n=30) | 0.433 ± 0.048 (n=30) | 0.010 ± 0.004 (n=30) | 0.339 ± 0.063 (n=30) |

## Protocol checks

- Rows: 120; completed: 120
- Shared candidate-pool hashes consistent: True
- Missing method pairs: 0
