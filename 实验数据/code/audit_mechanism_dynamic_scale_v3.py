"""Independently audit the final paired physical-dynamics mechanism run."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def trajectory(record: dict[str, Any]) -> dict[str, float | int | bool]:
    commands = (record.get("final_execution_record") or {}).get("structured_commands") or []
    waypoints = [
        point
        for command in commands
        for point in (command.get("response") or {}).get("waypoint_results") or []
        if isinstance(point, dict) and "steps" in point
    ]
    return {
        "command_count": len(commands),
        "waypoint_move_count": len(waypoints),
        "suffix_path_length_m": round(sum(float(point.get("path_length_m") or 0.0) for point in waypoints), 6),
        "minimum_altitude_m": round(min(float(point["minimum_altitude_m"]) for point in waypoints), 6),
        "maximum_arrival_error_m": round(max(float(point["distance_to_target"]) for point in waypoints), 6),
        "all_waypoint_commands_succeeded": all(bool(point.get("success")) for point in waypoints),
    }


def audit(tasks_path: Path, results_dir: Path) -> dict[str, Any]:
    tasks = read_jsonl(tasks_path)
    manifest = json.loads((results_dir / "run_manifest.json").read_text(encoding="utf-8"))
    pair_audit = json.loads((results_dir / "pair_audit.json").read_text(encoding="utf-8"))
    rows = {
        "simple": {row["task_id"]: row for row in read_jsonl(results_dir / "Simple_local_reassignment.jsonl")},
        "cds": {row["task_id"]: row for row in read_jsonl(results_dir / "CDS_MR.jsonl")},
    }
    cases = []
    for task in tasks:
        task_id = task["task_id"]
        simple = rows["simple"][task_id]
        cds = rows["cds"][task_id]
        simple_gym = simple["final_execution_record"]["gym_metrics"]
        cds_gym = cds["final_execution_record"]["gym_metrics"]
        simple_track = trajectory(simple)
        cds_track = trajectory(cds)
        simple_time = float(simple_gym["sim_time_seconds"])
        cds_time = float(cds_gym["sim_time_seconds"])
        cases.append({
            "task_id": task_id,
            "same_failure_step": simple["failure_event"]["step"] == cds["failure_event"]["step"],
            "intended_failure_both": all(
                row["failure_event"]["reason"] == "comparative_repair_choice_after_partial_execution"
                for row in (simple, cds)
            ),
            "simple_B2_owner": simple["repair"]["solver_audit"]["selected_assignment"]["B2"],
            "cds_B2_owner": cds["repair"]["solver_audit"]["selected_assignment"]["B2"],
            "simple_task_success": bool(simple_gym["task_success"]),
            "cds_task_success": bool(cds_gym["task_success"]),
            "simple_strict_contract": bool(simple["standard_metrics"]["strict_contract_satisfied"]),
            "cds_strict_contract": bool(cds["standard_metrics"]["strict_contract_satisfied"]),
            "simple_recovery_metric": simple["standard_metrics"]["recovery_success_rate_item"],
            "cds_recovery_metric": cds["standard_metrics"]["recovery_success_rate_item"],
            "simple_completion_time_s": round(simple_time, 6),
            "cds_completion_time_s": round(cds_time, 6),
            "completion_time_gain_s": round(simple_time - cds_time, 6),
            "completion_time_gain_fraction": round((simple_time - cds_time) / simple_time, 6),
            "simple_trajectory": simple_track,
            "cds_trajectory": cds_track,
            "suffix_path_gain_m": round(float(simple_track["suffix_path_length_m"]) - float(cds_track["suffix_path_length_m"]), 6),
            "simple_npsd": simple["standard_metrics"]["normalized_plan_stability_distance"],
            "cds_npsd": cds["standard_metrics"]["normalized_plan_stability_distance"],
        })
    resources = [task["contract"]["resources"] for task in tasks]
    checks = {
        "input_hash_matches_manifest": hashlib.sha256(tasks_path.read_bytes()).hexdigest() == manifest["source_input_sha256"],
        "three_complete_pairs": len(cases) == 3 and pair_audit["complete_pairs"] == 3,
        "identical_failure_time_state": pair_audit["status"] == "PASS" and pair_audit["same_failure_time_state_count"] == 3,
        "motor_dynamics_without_position_projection": all(not item.get("kinematic_mechanism_audit", False) for item in resources),
        "idle_drones_not_position_frozen": all(not item.get("freeze_idle_drones", False) for item in resources),
        "controller_frequency_48_hz": all(task["environment"]["sim"]["policy_frequency"] == 48 for task in tasks),
        "intended_failure_and_decisions": all(case["intended_failure_both"] and case["same_failure_step"] and case["simple_B2_owner"] == "uav_2" and case["cds_B2_owner"] == "uav_3" for case in cases),
        "all_recovered_and_strict_contracts": all(case["simple_task_success"] and case["cds_task_success"] and case["simple_strict_contract"] and case["cds_strict_contract"] and case["simple_recovery_metric"] is True and case["cds_recovery_metric"] is True for case in cases),
        "all_repair_waypoints_reached": all(case["simple_trajectory"]["all_waypoint_commands_succeeded"] and case["cds_trajectory"]["all_waypoint_commands_succeeded"] and case["simple_trajectory"]["maximum_arrival_error_m"] <= 0.1 and case["cds_trajectory"]["maximum_arrival_error_m"] <= 0.1 for case in cases),
        "safe_positive_altitude": all(case["simple_trajectory"]["minimum_altitude_m"] >= 0.5 and case["cds_trajectory"]["minimum_altitude_m"] >= 0.5 for case in cases),
        "cds_shorter_time_and_path_all_pairs": all(case["completion_time_gain_s"] > 0 and case["suffix_path_gain_m"] > 0 for case in cases),
    }
    return {
        "protocol": "mechanism_dynamic_scale_v3_physical_audit",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "summary": {
            "pair_count": len(cases),
            "mean_simple_completion_time_s": round(statistics.mean(case["simple_completion_time_s"] for case in cases), 6),
            "mean_cds_completion_time_s": round(statistics.mean(case["cds_completion_time_s"] for case in cases), 6),
            "mean_completion_time_gain_fraction": round(statistics.mean(case["completion_time_gain_fraction"] for case in cases), 6),
            "mean_simple_suffix_path_m": round(statistics.mean(case["simple_trajectory"]["suffix_path_length_m"] for case in cases), 6),
            "mean_cds_suffix_path_m": round(statistics.mean(case["cds_trajectory"]["suffix_path_length_m"] for case in cases), 6),
            "mean_suffix_path_gain_fraction": round(statistics.mean(case["suffix_path_gain_m"] / case["simple_trajectory"]["suffix_path_length_m"] for case in cases), 6),
        },
        "cases": cases,
        "scope": "exploratory constructed cases; descriptive paired mechanism evidence, not population-level performance",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.tasks, args.results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "checks": report["checks"], "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
