# Constraint-tightness sensitivity

Two repair arms share the same deterministic initial plan per task-level; only compatible UAV candidate-set tightness varies.

| Level | Method | RSR ↑ | CSR ↑ | NPSD ↓ | Repair latency ↓ |
|---|---|---|---|---|---|
| loose | Blind+Global-Replan | 1.000 ± 0.000 (n=6) | 1.000 ± 0.000 (n=6) | 0.650 ± 0.204 (n=6) | 0.009 ± 0.002 (n=6) |
| loose | Blind+CDS-MR | 1.000 ± 0.000 (n=6) | 1.000 ± 0.000 (n=6) | 0.433 ± 0.052 (n=6) | 0.008 ± 0.001 (n=6) |
| medium | Blind+Global-Replan | 1.000 ± 0.000 (n=6) | 1.000 ± 0.000 (n=6) | 0.483 ± 0.160 (n=6) | 0.009 ± 0.002 (n=6) |
| medium | Blind+CDS-MR | 1.000 ± 0.000 (n=6) | 1.000 ± 0.000 (n=6) | 0.433 ± 0.052 (n=6) | 0.007 ± 0.001 (n=6) |
| tight | Blind+Global-Replan | 0.667 ± 0.516 (n=6) | 0.833 ± 0.258 (n=6) | 0.633 ± 0.288 (n=6) | 0.007 ± 0.000 (n=4) |
| tight | Blind+CDS-MR | 0.667 ± 0.516 (n=6) | 0.833 ± 0.258 (n=6) | 0.633 ± 0.288 (n=6) | 0.007 ± 0.001 (n=4) |

## Protocol status

- Rows: 36; completed: 36; runner errors: 0.
- Paired comparison is CDS-MR minus Global-Replan within source task × level × seed.
- This is a one-seed supplementary sensitivity trend, not a replacement for the main multi-seed comparison.
