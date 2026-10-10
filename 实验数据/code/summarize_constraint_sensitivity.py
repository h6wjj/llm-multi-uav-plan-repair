"""Summarize the constraint-tightness sensitivity run by factor level."""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any


METHODS = ("Blind+Global-Replan", "Blind+CDS-MR")
LEVELS = ("loose", "medium", "tight")
METRICS = (
    "recovery_success_rate_item",
    "contract_satisfaction_rate",
    "normalized_plan_stability_distance",
    "repair_decision_latency_seconds",
)


def stat(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6) if values else None,
        "std": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0 if values else None,
    }


def level(row: dict[str, Any]) -> str:
    match = re.search(r"::constraint_sensitivity_(loose|medium|tight)::", str(row.get("task_id", "")))
    if not match:
        raise ValueError(f"cannot infer constraint level from task_id: {row.get('task_id')}")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    completed = [row for row in rows if row.get("runner_status") == "completed"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in completed:
        grouped.setdefault((level(row), str(row.get("method"))), []).append(row)
    summary: dict[str, Any] = {}
    for factor in LEVELS:
        summary[factor] = {}
        for method in METHODS:
            values = grouped.get((factor, method), [])
            summary[factor][method] = {
                metric: stat([
                    float((row.get("standard_metrics") or {})[metric])
                    for row in values
                    if (row.get("standard_metrics") or {}).get(metric) is not None
                ])
                for metric in METRICS
            }
            summary[factor][method]["rows"] = len(values)
    paired: dict[str, Any] = {}
    for factor in LEVELS:
        by_key: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for row in grouped.get((factor, METHODS[0]), []) + grouped.get((factor, METHODS[1]), []):
            key = (str(row.get("source_task_id")), str(row.get("run_seed_index")))
            by_key.setdefault(key, {})[str(row.get("method"))] = row
        paired[factor] = {}
        for metric in METRICS:
            deltas = []
            for pair in by_key.values():
                left = (pair.get(METHODS[1], {}).get("standard_metrics") or {}).get(metric)
                right = (pair.get(METHODS[0], {}).get("standard_metrics") or {}).get(metric)
                if left is not None and right is not None:
                    deltas.append(float(left) - float(right))
            paired[factor][metric] = stat(deltas)
    result = {
        "protocol_version": "constraint_sensitivity_summary_v1",
        "input": str(args.input),
        "factor": "constraint_tightness",
        "levels": list(LEVELS),
        "methods": list(METHODS),
        "metrics": list(METRICS),
        "rows": len(rows),
        "completed_rows": len(completed),
        "runner_errors": sum(row.get("runner_status") != "completed" for row in rows),
        "summary_by_level": summary,
        "paired_cds_minus_global_by_level": paired,
        "interpretation_rule": "supplementary trend evidence only; do not claim universal robustness from one seed",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    labels = {
        "recovery_success_rate_item": "RSR ↑",
        "contract_satisfaction_rate": "CSR ↑",
        "normalized_plan_stability_distance": "NPSD ↓",
        "repair_decision_latency_seconds": "Repair latency ↓",
    }
    lines = [
        "# Constraint-tightness sensitivity",
        "",
        "Two repair arms share the same deterministic initial plan per task-level; only compatible UAV candidate-set tightness varies.",
        "",
        "| Level | Method | " + " | ".join(labels.values()) + " |",
        "|---|---|" + "---|" * len(labels),
    ]
    for factor in LEVELS:
        for method in METHODS:
            cells = []
            for metric in METRICS:
                item = summary[factor][method][metric]
                cells.append("—" if item["mean"] is None else f"{item['mean']:.3f} ± {item['std']:.3f} (n={item['n']})")
            lines.append(f"| {factor} | {method} | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "## Protocol status",
            "",
            f"- Rows: {len(rows)}; completed: {len(completed)}; runner errors: {result['runner_errors']}.",
            "- Paired comparison is CDS-MR minus Global-Replan within source task × level × seed.",
            "- This is a one-seed supplementary sensitivity trend, not a replacement for the main multi-seed comparison.",
        ]
    )
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
