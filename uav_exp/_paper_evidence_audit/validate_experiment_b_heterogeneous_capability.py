#!/usr/bin/env python3
"""Validate the isolated Experiment-B heterogeneous-capability run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    tasks = {row["task_id"]: row for row in load_jsonl(args.tasks)}
    rows = load_jsonl(args.results)
    checks: list[dict[str, Any]] = []
    for row in rows:
        task = tasks.get(row.get("task_id"), {})
        condition = row.get("condition")
        metrics = row.get("standard_metrics") if isinstance(row.get("standard_metrics"), dict) else {}
        passed: dict[str, bool] = {}
        passed["task_contract_found"] = bool(task)
        passed["nominal_success"] = condition != "nominal" or bool(metrics.get("task_success"))
        passed["nominal_no_repair"] = condition != "nominal" or not bool(row.get("cds_mr_repair", {}).get("triggered"))
        if condition == "recoverable_failure":
            failure = row.get("bridge_first_failure") if isinstance(row.get("bridge_first_failure"), dict) else {}
            details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
            repair = row.get("cds_mr_repair") if isinstance(row.get("cds_mr_repair"), dict) else {}
            executor = repair.get("structured_executor") if isinstance(repair.get("structured_executor"), dict) else {}
            graph = executor.get("compatibility_graph") if isinstance(executor.get("compatibility_graph"), dict) else {}
            selected = executor.get("selected_assignment") if isinstance(executor.get("selected_assignment"), dict) else {}
            passed.update(
                {
                    "failure_is_capability_target": details.get("bundle_id") == "bundle_2" and details.get("drone_id", "uav_1") == "uav_1" and details.get("required_capabilities") == ["camera_thermal"],
                    "invalidated_capability_recorded": details.get("invalidated_capabilities") == ["camera_thermal"],
                    "candidate_graph_excludes_incompatible_uavs": graph.get("bundle_2") == ["uav_2"],
                    "selected_capability_compatible_replacement": selected.get("bundle_2") == "uav_2",
                    "recovery_success": bool(metrics.get("failure_recovery_success")),
                    "strict_contract_satisfied": bool(metrics.get("strict_contract_satisfied")),
                    "one_bundle_reassigned": metrics.get("reassigned_bundle_count") == 1 and metrics.get("assignment_scope", {}).get("reassigned_bundle_ids") == ["bundle_2"],
                    "unaffected_bundles_preserved": metrics.get("unaffected_bundle_preservation_rate") == 1.0,
                }
            )
        else:
            passed["recoverable_only_check_not_applicable"] = True
        checks.append(
            {
                "task_id": row.get("task_id"),
                "condition": condition,
                "source_task_id": row.get("source_task_id"),
                "passed": passed,
                "all_pass": all(passed.values()),
            }
        )
    summary = {
        "experiment_id": "B_heterogeneous_capability_exclusion_v1",
        "rows": len(rows),
        "nominal_rows": sum(row.get("condition") == "nominal" for row in rows),
        "recoverable_rows": sum(row.get("condition") == "recoverable_failure" for row in rows),
        "recoverable_successes": sum(row.get("condition") == "recoverable_failure" and row["all_pass"] for row in checks),
        "all_pass": bool(checks) and all(row["all_pass"] for row in checks),
        "checks": checks,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Experiment B heterogeneous-capability validation",
        "",
        f"- Rows: {summary['rows']} ({summary['nominal_rows']} nominal + {summary['recoverable_rows']} recoverable)",
        f"- Recoverable rows passing all checks: {summary['recoverable_successes']}/{summary['recoverable_rows']}",
        f"- All checks pass: {summary['all_pass']}",
        "",
        "The recoverable protocol targets U1/B2, which requires `camera_thermal`; after U1 becomes unavailable, the recomputed compatibility graph contains only U2. CDS-MR must therefore reassign B2 to U2 while retaining B1, B3, and B4.",
    ]
    (args.output_dir / "validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, ensure_ascii=False, indent=2))
    return 0 if summary["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
