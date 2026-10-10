"""Build a current main-comparison overlay from the frozen B0/CDS outputs
and the closed external-baseline rerun.

The original v6.4 aggregate is preserved. This report makes the provenance
change explicit instead of silently overwriting the frozen summary.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


METHODS = ("B0", "LLM-Assisted Plan Execution", "H-MRTA-Repair", "B0+SeSE+CDS-MR")
CONDITIONS = ("nominal", "recoverable_failure")
METRICS = (
    "recovery_success_rate_item",
    "contract_satisfaction_rate",
    "normalized_plan_stability_distance",
    "repair_decision_latency_seconds",
    "mission_completion_time_seconds",
)


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6) if values else None,
        "std": round(statistics.stdev(values), 6) if len(values) > 1 else (0.0 if values else None),
    }


def usable(row: dict[str, Any]) -> bool:
    return row.get("runner_status") in (None, "completed")


def exact_sign_test_pvalue(differences: list[float]) -> float:
    """Two-sided exact sign-test p-value, dropping zero paired differences."""
    nonzero = [value for value in differences if value != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    positive = sum(value > 0 for value in nonzero)
    tail = sum(math.comb(n, k) / (2.0 ** n) for k in range(0, min(positive, n - positive) + 1))
    return min(1.0, 2.0 * tail)


def format_pvalue(value: float) -> str:
    if value == 0.0:
        return "0"
    if value < 0.0001:
        return f"{value:.3e}"
    return f"{value:.6f}"


def paired_comparison(
    rows: list[dict[str, Any]],
    left_method: str,
    right_method: str,
    condition: str,
    metric: str,
    higher_is_better: bool,
) -> dict[str, Any]:
    key = lambda row: (str(row.get("source_task_id")), str(row.get("condition")), str(row.get("run_seed_index")))
    left = {
        key(row): float((row.get("standard_metrics") or {})[metric])
        for row in rows
        if row.get("method") == left_method
        and row.get("condition") == condition
        and (row.get("standard_metrics") or {}).get(metric) is not None
    }
    right = {
        key(row): float((row.get("standard_metrics") or {})[metric])
        for row in rows
        if row.get("method") == right_method
        and row.get("condition") == condition
        and (row.get("standard_metrics") or {}).get(metric) is not None
    }
    paired_keys = sorted(set(left) & set(right))
    differences = [left[item] - right[item] for item in paired_keys]
    benefit_differences = differences if higher_is_better else [-value for value in differences]
    mean_difference = statistics.fmean(differences) if differences else None
    benefit_delta = statistics.fmean(benefit_differences) if benefit_differences else None
    std_difference = statistics.stdev(differences) if len(differences) > 1 else (0.0 if differences else None)
    task_values: dict[str, list[float]] = {}
    for item, value in zip(paired_keys, benefit_differences):
        task_values.setdefault(item[0], []).append(value)
    task_benefits = [statistics.fmean(values) for _, values in sorted(task_values.items())]
    positive = sum(value > 0 for value in task_benefits)
    tied = sum(value == 0 for value in task_benefits)
    negative = sum(value < 0 for value in task_benefits)
    return {
        "left_method": left_method,
        "right_method": right_method,
        "condition": condition,
        "metric": metric,
        "n_row_pairs": len(differences),
        "n_source_tasks": len(task_benefits),
        "paired_key_count": len(paired_keys),
        "mean_delta_left_minus_right": round(mean_difference, 6) if mean_difference is not None else None,
        "row_level_mean_benefit_delta": round(benefit_delta, 6) if benefit_delta is not None else None,
        "difference_std": round(std_difference, 6) if std_difference is not None else None,
        "benefit_direction": "left_minus_right" if higher_is_better else "right_minus_left",
        "task_level_benefit_mean": round(statistics.fmean(task_benefits), 6) if task_benefits else None,
        "task_level_direction_counts": {"improved": positive, "tied": tied, "worsened": negative},
        "task_level_exact_p_sign_test_two_sided": exact_sign_test_pvalue(task_benefits),
        "analysis_note": "Run-index rows are nested within source tasks; no row-level CI or effect size is reported.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-root", type=Path, required=True)
    parser.add_argument("--external-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for seed in range(13, 19):
        rows.extend(read(args.main_root / f"seed_{seed}" / "b0.jsonl"))
        rows.extend(read(args.main_root / f"seed_{seed}" / "cds_mr.jsonl"))
    rows.extend(read(args.external_root / "h_mrta.jsonl"))
    rows.extend(read(args.external_root / "llm_ape_final.jsonl"))
    rows = [row for row in rows if usable(row)]

    key = lambda row: (str(row.get("source_task_id")), str(row.get("condition")), str(row.get("run_seed_index")))
    counts = {}
    key_sets = {}
    for method in METHODS:
        selected = [row for row in rows if row.get("method") == method]
        counts[method] = len(selected)
        key_sets[method] = {key(row) for row in selected}
    if any(counts[m] != 72 for m in METHODS) or len(set(map(len, key_sets.values()))) != 1:
        raise RuntimeError(f"expected four 72-row method streams, got {counts}")
    if not (key_sets["B0"] == key_sets["LLM-Assisted Plan Execution"] == key_sets["H-MRTA-Repair"] == key_sets["B0+SeSE+CDS-MR"]):
        raise RuntimeError("method streams do not share the same paired key set")

    result_summary: dict[str, Any] = {}
    for method in METHODS:
        method_rows = [row for row in rows if row.get("method") == method]
        result_summary[method] = {}
        for condition in (*CONDITIONS, "all"):
            selected = method_rows if condition == "all" else [row for row in method_rows if row.get("condition") == condition]
            item = {}
            for metric in METRICS:
                values = [
                    float((row.get("standard_metrics") or {})[metric])
                    for row in selected
                    if (row.get("standard_metrics") or {}).get(metric) is not None
                ]
                item[metric] = summary(values)
            item["rows"] = len(selected)
            result_summary[method][condition] = item

    comparison_specs = [
        ("B0+SeSE+CDS-MR", "H-MRTA-Repair"),
        ("B0+SeSE+CDS-MR", "LLM-Assisted Plan Execution"),
        ("B0+SeSE+CDS-MR", "B0"),
    ]
    higher_metrics = {"recovery_success_rate_item", "contract_satisfaction_rate"}
    paired_statistics = []
    for left_method, right_method in comparison_specs:
        for metric in METRICS:
            paired_statistics.append(
                paired_comparison(
                    rows,
                    left_method,
                    right_method,
                    "recoverable_failure",
                    metric,
                    metric in higher_metrics,
                )
            )

    output = {
        "protocol_version": "v6_4_closed_external_overlay_v2_task_nested",
        "main_scenario": "v6.4 six-task UAVBench-derived bridge protocol, run indices 13-18",
        "methods": list(METHODS),
        "conditions": list(CONDITIONS),
        "paired_key": "source_task_id + condition + run_seed_index",
        "method_row_counts": counts,
        "shared_key_set": True,
        "metrics": list(METRICS),
        "summary": result_summary,
        "external_baseline_source": "results/external_baseline_repro_v1/",
        "frozen_aggregate_preserved": "results/main_v6_4/summary_main_comparison_v6_4_seed13_18.json",
        "interpretation": "Use this overlay for current baseline comparison; it does not silently rewrite the frozen v6.4 aggregate.",
        "task_nested_descriptive_comparisons": paired_statistics,
        "comparison_protocol": {
            "condition": "recoverable_failure",
            "difference": "full_method - comparator for raw metric; benefit direction reverses for lower-is-better metrics",
            "row_pairing": "source_task_id + condition + run_seed_index",
            "independent_unit_for_direction_summary": "source_task_id",
            "inference_scope": "descriptive six-task evidence; run-index rows are nested repeats",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    labels = {
        "recovery_success_rate_item": "RSR ↑",
        "contract_satisfaction_rate": "CSR ↑",
        "normalized_plan_stability_distance": "NPSD ↓",
        "repair_decision_latency_seconds": "Repair latency ↓",
        "mission_completion_time_seconds": "Mission time ↓",
    }
    lines = [
        "# v6.4 main comparison with closed external baselines",
        "",
        "B0 and CDS-MR are read from the frozen v6.4 streams; external columns are read from the closed 72-pair rerun. H-MRTA-Repair and LLM-Assisted Plan Execution are platform-adapted implementations, not official runtime reproductions.",
        "",
        "| Condition | Method | " + " | ".join(labels.values()) + " |",
        "|---|---|" + "---|" * len(labels),
    ]
    for condition in (*CONDITIONS, "all"):
        for method in METHODS:
            item = result_summary[method][condition]
            cells = []
            for metric in labels:
                stat = item[metric]
                cells.append("—" if stat["mean"] is None else f"{stat['mean']:.3f} ± {stat['std']:.3f} (n={stat['n']})")
            lines.append(f"| {condition} | {method} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Provenance and scope",
        "",
        "- The original v6.4 aggregate is preserved and is not overwritten.",
        "- The external-baseline source is the closed platform-adapted rerun with 72 records per method and zero runner errors.",
        "- Mission time is successful-task completion time; effective n is shown per cell.",
        "- This remains bridge-simulation evidence with common CF2X physics; it is not an official-runtime or physical-heterogeneity claim.",
        "",
        "## Task-nested descriptive comparisons (recoverable_failure)",
        "",
        "Row pairs use shared task-condition-run-index keys, but run-index repetitions are nested within six source tasks. `Benefit Δ` is oriented so that positive values favor the full method; for lower-is-better metrics it is comparator minus full method. The exact sign-test p-value uses source-task mean directions and is auxiliary descriptive evidence, not population-level inference.",
        "",
        "| Full method vs comparator | Metric | Row pairs | Source tasks | Benefit Δ | Improved / tied / worsened | Task-level exact p |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    for item in paired_statistics:
        if item["n_row_pairs"] == 0:
            continue
        metric_label = labels[item["metric"]]
        comparison = f"{item['left_method']} vs {item['right_method']}"
        p_text = format_pvalue(item["task_level_exact_p_sign_test_two_sided"])
        direction = item["task_level_direction_counts"]
        lines.append(
            f"| {comparison} | {metric_label} | {item['n_row_pairs']} | {item['n_source_tasks']} | {item['row_level_mean_benefit_delta']:.3f} | {direction['improved']} / {direction['tied']} / {direction['worsened']} | {p_text} |"
        )
    lines += [
        "",
        "Interpretation: the table is paired and metric-direction aware, but it is descriptive because only six source tasks are available. It does not convert conditional Mission Completion Time into an unconditional efficiency metric. Mission time remains successful-task completion time and must be read together with RSR and its effective n.",
    ]
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "method_row_counts": counts, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
