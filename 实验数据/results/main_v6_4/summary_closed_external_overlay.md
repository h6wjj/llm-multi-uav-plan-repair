# v6.4 main comparison with closed external baselines

B0 and CDS-MR are read from the frozen v6.4 streams; external columns are read from the closed 72-pair rerun. H-MRTA-Repair and LLM-Assisted Plan Execution are platform-adapted implementations, not official runtime reproductions.

| Condition | Method | RSR ↑ | CSR ↑ | NPSD ↓ | Repair latency ↓ | Mission time ↓ |
|---|---|---|---|---|---|---|
| nominal | B0 | — | 0.810 ± 0.222 (n=36) | — | — | 0.378 ± 0.107 (n=24) |
| nominal | LLM-Assisted Plan Execution | — | 1.000 ± 0.000 (n=36) | — | — | 0.339 ± 0.063 (n=36) |
| nominal | H-MRTA-Repair | — | 1.000 ± 0.000 (n=36) | — | — | 0.339 ± 0.063 (n=36) |
| nominal | B0+SeSE+CDS-MR | — | 1.000 ± 0.000 (n=36) | — | — | 0.339 ± 0.063 (n=36) |
| recoverable_failure | B0 | 0.000 ± 0.000 (n=36) | 0.509 ± 0.039 (n=36) | — | — | — |
| recoverable_failure | LLM-Assisted Plan Execution | 0.917 ± 0.280 (n=36) | 0.958 ± 0.140 (n=36) | 0.758 ± 0.258 (n=36) | 58.530 ± 67.767 (n=36) | 0.932 ± 0.947 (n=33) |
| recoverable_failure | H-MRTA-Repair | 1.000 ± 0.000 (n=36) | 1.000 ± 0.000 (n=36) | 0.650 ± 0.189 (n=36) | 0.008 ± 0.001 (n=36) | 0.339 ± 0.063 (n=36) |
| recoverable_failure | B0+SeSE+CDS-MR | 1.000 ± 0.000 (n=36) | 1.000 ± 0.000 (n=36) | 0.433 ± 0.048 (n=36) | 5.311 ± 1.746 (n=36) | 0.339 ± 0.063 (n=36) |
| all | B0 | 0.000 ± 0.000 (n=36) | 0.660 ± 0.219 (n=72) | — | — | 0.378 ± 0.107 (n=24) |
| all | LLM-Assisted Plan Execution | 0.917 ± 0.280 (n=36) | 0.979 ± 0.101 (n=72) | 0.758 ± 0.258 (n=36) | 58.530 ± 67.767 (n=36) | 0.622 ± 0.716 (n=69) |
| all | H-MRTA-Repair | 1.000 ± 0.000 (n=36) | 1.000 ± 0.000 (n=72) | 0.650 ± 0.189 (n=36) | 0.008 ± 0.001 (n=36) | 0.339 ± 0.063 (n=72) |
| all | B0+SeSE+CDS-MR | 1.000 ± 0.000 (n=36) | 1.000 ± 0.000 (n=72) | 0.433 ± 0.048 (n=36) | 5.311 ± 1.746 (n=36) | 0.339 ± 0.063 (n=72) |

## Provenance and scope

- The original v6.4 aggregate is preserved and is not overwritten.
- The external-baseline source is the closed platform-adapted rerun with 72 records per method and zero runner errors.
- Mission time is successful-task completion time; effective n is shown per cell.
- This remains bridge-simulation evidence with common CF2X physics; it is not an official-runtime or physical-heterogeneity claim.

## Task-nested descriptive comparisons (recoverable_failure)

Row pairs use shared task-condition-run-index keys, but run-index repetitions are nested within six source tasks. `Benefit Δ` is oriented so that positive values favor the full method; for lower-is-better metrics it is comparator minus full method. The exact sign-test p-value uses source-task mean directions and is auxiliary descriptive evidence, not population-level inference.

| Full method vs comparator | Metric | Row pairs | Source tasks | Benefit Δ | Improved / tied / worsened | Task-level exact p |
|---|---|---:|---:|---:|---|---:|
| B0+SeSE+CDS-MR vs H-MRTA-Repair | RSR ↑ | 36 | 6 | 0.000 | 0 / 6 / 0 | 1.000000 |
| B0+SeSE+CDS-MR vs H-MRTA-Repair | CSR ↑ | 36 | 6 | 0.000 | 0 / 6 / 0 | 1.000000 |
| B0+SeSE+CDS-MR vs H-MRTA-Repair | NPSD ↓ | 36 | 6 | 0.217 | 4 / 2 / 0 | 0.125000 |
| B0+SeSE+CDS-MR vs H-MRTA-Repair | Repair latency ↓ | 36 | 6 | -5.303 | 0 / 0 / 6 | 0.031250 |
| B0+SeSE+CDS-MR vs H-MRTA-Repair | Mission time ↓ | 36 | 6 | 0.000 | 0 / 6 / 0 | 1.000000 |
| B0+SeSE+CDS-MR vs LLM-Assisted Plan Execution | RSR ↑ | 36 | 6 | 0.083 | 1 / 5 / 0 | 1.000000 |
| B0+SeSE+CDS-MR vs LLM-Assisted Plan Execution | CSR ↑ | 36 | 6 | 0.042 | 1 / 5 / 0 | 1.000000 |
| B0+SeSE+CDS-MR vs LLM-Assisted Plan Execution | NPSD ↓ | 36 | 6 | 0.325 | 5 / 1 / 0 | 0.062500 |
| B0+SeSE+CDS-MR vs LLM-Assisted Plan Execution | Repair latency ↓ | 36 | 6 | 53.219 | 6 / 0 / 0 | 0.031250 |
| B0+SeSE+CDS-MR vs LLM-Assisted Plan Execution | Mission time ↓ | 33 | 6 | 0.598 | 3 / 3 / 0 | 0.250000 |
| B0+SeSE+CDS-MR vs B0 | RSR ↑ | 36 | 6 | 1.000 | 6 / 0 / 0 | 0.031250 |
| B0+SeSE+CDS-MR vs B0 | CSR ↑ | 36 | 6 | 0.491 | 6 / 0 / 0 | 0.031250 |

Interpretation: the table is paired and metric-direction aware, but it is descriptive because only six source tasks are available. It does not convert conditional Mission Completion Time into an unconditional efficiency metric. Mission time remains successful-task completion time and must be read together with RSR and its effective n.
