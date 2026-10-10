"""Summarize the closed platform-adapted external-baseline rerun.

The script deliberately treats runner errors separately from algorithmic
outcomes. A completed record is the only record eligible for metric means.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


METHODS = ("H-MRTA-Repair", "LLM-Assisted Plan Execution")
CONDITIONS = ("nominal_uncertain", "recoverable_failure_uncertain")
METRICS = (
    "recovery_success_rate_item",
    "contract_satisfaction_rate",
    "normalized_plan_stability_distance",
    "repair_decision_latency_seconds",
    "mission_completion_time_seconds",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def mean_std(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6) if values else None,
        "std": round(statistics.stdev(values), 6) if len(values) > 1 else (0.0 if values else None),
    }


def condition(row: dict[str, Any]) -> str:
    task_id = str(row.get("task_id", ""))
    for value in CONDITIONS:
        if value in task_id:
            return value
    return "unknown"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for method in METHODS:
        method_rows = [row for row in rows if row.get("method") == method and row.get("runner_status") == "completed"]
        out[method] = {}
        for cond in (*CONDITIONS, "all"):
            selected = method_rows if cond == "all" else [row for row in method_rows if condition(row) == cond]
            metrics: dict[str, Any] = {}
            for metric in METRICS:
                values = [
                    float((row.get("standard_metrics") or {})[metric])
                    for row in selected
                    if (row.get("standard_metrics") or {}).get(metric) is not None
                ]
                metrics[metric] = mean_std(values)
            metrics["rows"] = len(selected)
            out[method][cond] = metrics
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h-mrta", type=Path, required=True)
    parser.add_argument("--llm-ape", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--input-sha256", default=None)
    parser.add_argument("--runner-sha256", default=None)
    args = parser.parse_args()

    rows = load_jsonl(args.h_mrta) + load_jsonl(args.llm_ape)
    statuses = Counter(str(row.get("runner_status")) for row in rows)
    errors = [row for row in rows if row.get("runner_status") != "completed"]
    summary = {
        "protocol_version": "external_baseline_repro_v1_closed",
        "inputs": {"h_mrta": str(args.h_mrta), "llm_ape": str(args.llm_ape)},
        "methods": list(METHODS),
        "conditions": list(CONDITIONS),
        "metrics": list(METRICS),
        "rows": len(rows),
        "status_counts": dict(statuses),
        "runner_errors": len(errors),
        "summary": summarize(rows),
        "implementation_scope": "platform_adapted_external_baseline",
        "official_runtime_invoked": False,
        "input_sha256": args.input_sha256,
        "runner_sha256": args.runner_sha256,
        "interpretation": {
            "primary_claim": "closed reproducibility of the two platform-adapted external baseline runners",
            "not_claimed": "reproduction of an official external runtime or physical heterogeneity",
            "mission_time": "successful-task completion time; report effective n together with mean and std",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    labels = {
        "recovery_success_rate_item": "RSR ↑",
        "contract_satisfaction_rate": "CSR ↑",
        "normalized_plan_stability_distance": "NPSD ↓",
        "repair_decision_latency_seconds": "Repair latency ↓",
        "mission_completion_time_seconds": "Mission time ↓",
    }
    lines = [
        "# Closed external-baseline reproducibility summary",
        "",
        "Both methods were rerun with the same 72 task-condition-seed inputs through the platform-adapted runner.",
        "",
        "| Condition | Method | " + " | ".join(labels.values()) + " |",
        "|---|---|" + "---|" * len(labels),
    ]
    for cond in (*CONDITIONS, "all"):
        for method in METHODS:
            item = summary["summary"][method][cond]
            cells = []
            for metric in labels:
                stat = item[metric]
                cells.append("—" if stat["mean"] is None else f"{stat['mean']:.3f} ± {stat['std']:.3f} (n={stat['n']})")
            lines.append(f"| {cond} | {method} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Reproducibility status",
        "",
        f"- Total rows: {summary['rows']}; status counts: `{dict(statuses)}`.",
        f"- Runner errors: {summary['runner_errors']}. Errors are excluded from metric means and must be zero before formal claims.",
        "- Scope: platform-adapted implementations; the official external runtime was not invoked.",
        "- Mission time is conditional on successful completion; effective n is shown in every cell.",
    ]
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
