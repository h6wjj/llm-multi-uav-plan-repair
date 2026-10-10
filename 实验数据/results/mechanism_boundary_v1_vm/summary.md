# Simple local reassignment vs CDS-MR

Protocol: `simple_local_vs_cds_mr_paired_v1`; recoverable-failure task–seed pairs: **6**.

## Paired-state audit

- Complete pairs: 6/6.
- Identical failure-time state signatures: 6/6.
- Audit status: **PASS**.

## Descriptive results

| Method | rows | recovery success | mean CSR | mean NPSD | mean repair latency (s) | successful completion-time n | mean completion time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Simple-local-reassignment | 6 | 6/6 | 1.0 | 0.333333 | 0.007524667 | 6 | 52.038194444 |
| CDS-MR | 6 | 6/6 | 1.0 | 0.333333 | 0.008032167 | 6 | 52.038194444 |

Simple local reassignment is intentionally constraint-valid: it keeps the completed prefix, uses the same eligible UAV set and live bridge, preserves dependency order, and lets the bridge apply the same runtime checks. Its only replacement rule is a direct-scope greedy minimum route-cost choice with deterministic UAV-id tie-break; it does not use CDS-MR causal closure or lexicographic minimum-edit optimization.

Interpretation must remain conditional on this fixed failure protocol. If the two arms are functionally equivalent on this candidate space, report that negative/equivalence result rather than claiming that CDS-MR is isolated by this experiment.
