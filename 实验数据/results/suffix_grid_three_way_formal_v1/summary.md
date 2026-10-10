# Three-way suffix-grid comparison

Protocol: `suffix_grid_three_way_v1`; recoverable-failure task groups: **90**.

## Paired-state audit

- Complete pairs: 90/90.
- Identical failure-time state signatures: 90/90.
- Audit status: **PASS**.

## Descriptive results

| Method | rows | recovery success | mean CSR | mean NPSD | mean repair latency (s) | successful completion-time n | mean completion time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Simple-local-reassignment | 90 | 90/90 | 1.0 | 0.333333 | 0.008177222 | 90 | 71.51087963 |
| CDS-MR | 90 | 90/90 | 1.0 | 0.333333 | 0.0081486 | 90 | 65.327546296 |
| Global-total-route-reallocation | 90 | 90/90 | 1.0 | 0.333333 | 0.0081445 | 90 | 65.327546296 |

Simple local reassignment is intentionally constraint-valid: it keeps the completed prefix, uses the same eligible UAV set and live bridge, preserves dependency order, and lets the bridge apply the same runtime checks. Its only replacement rule is a direct-scope greedy minimum route-cost choice with deterministic UAV-id tie-break; it does not use CDS-MR causal closure or lexicographic minimum-edit optimization.

Interpretation must remain conditional on this fixed failure protocol. If the two arms are functionally equivalent on this candidate space, report that negative/equivalence result rather than claiming that CDS-MR is isolated by this experiment.

The global arm searches all contract-eligible assignments of unfinished decision-variable bundles and minimizes estimated full-suffix route cost without an edit penalty. Runtime deadlines and step budgets are checked by the same bridge for every arm.
Actual suffix path is the summed motor-executed path of commanded repair UAV segments, not geometric chord distance or idle-UAV drift. Solver-only wall time and failure-to-first-repair-command latency are reported separately.
