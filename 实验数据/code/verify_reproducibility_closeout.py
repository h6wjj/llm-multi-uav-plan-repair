"""Read-only checks for the 2026-10-03 reproducibility closeout.

Run from any directory. JSON is emitted to stdout; frozen inputs are never written.
This verifies metadata interpretation and row consistency, not a new simulation.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS: dict[str, bool] = {}
INPUT_HASHES: dict[str, str] = {}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(relative: str):
    path = ROOT / relative
    INPUT_HASHES[relative] = digest(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_rows(relative: str):
    path = ROOT / relative
    INPUT_HASHES[relative] = digest(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def require(name: str, value: bool) -> None:
    CHECKS[name] = bool(value)


def state_for_comparison(row):
    state = row["failure_event"]["state"]
    drones = [{key: drone.get(key) for key in ("id", "status", "position", "role", "capabilities")}
              for drone in state["drones"]]
    return {"drones": sorted(drones, key=lambda drone: drone["id"]),
            "completed": state["completed_waypoints_by_bundle"],
            "owners": state["bundle_assignment"]}


def main() -> int:
    frozen_hashes = {
        "provenance/environment.txt": "0ffa30b961bb18343b62f203fdc69d0c6c5011d4500eb9d1badbbb57587c813e",
        "results/suffix_grid_three_way_formal_v1/metric_audit.json": "a9310aa858047ed67c7615c285c06c7bcc7e018d5f3aa90b86a982bd71f1f531",
        "results/suffix_grid_three_way_formal_v1/formal_metric_audit.json": "0dc9bc1784aa5ce5831edfbed6d6222a5833a8e8ed5d616f51bee030e7dd9f23",
        "results/b3_independent_task_distribution_formal_v1/formal_metric_audit.json": "495e4d939d8b6b79aff9259a88c0681e1595367b14816f8a8c34efa53d5abea7",
        "results/mechanism_stress_v2_vm_v10/tasks_recoverable_failure_v1.jsonl": "5b45aa533bf690ab49fba4072f44cd21d504d9f20cad2894d5cb7503dacefdcf",
        "results/mechanism_stress_v2_vm_v10/Simple_local_reassignment.jsonl": "657917dac5dcc2496059c42998721e67d53ba547586c0371e5cef77f2d35483f",
        "results/mechanism_stress_v2_vm_v10/CDS_MR.jsonl": "9c19051835551b3e9c100ec0d5827446542234bcce4aad492557a5541c0625ab",
        "results/mechanism_stress_v2_vm_v10/summary.json": "97fb9e561b3f528a979887966ee2b1346d0c55c55a5665bb2d2d5f19e75dfee1",
    }
    for relative, expected in frozen_hashes.items():
        actual = digest(ROOT / relative)
        INPUT_HASHES[relative] = actual
        require("frozen_hash_unchanged:" + relative, actual == expected)

    formal = {}
    for name in ("suffix_grid_three_way_formal_v1", "b3_independent_task_distribution_formal_v1"):
        base = "results/" + name + "/"
        original = read_json(base + "metric_audit.json")
        wrapper = read_json(base + "formal_metric_audit.json")
        manifest = read_json(base + "run_manifest.json")
        require(name + ":scope", original["scope"] == "preflight_only" and wrapper["scope"] == "formal_run")
        require(name + ":wrapper_pass", wrapper["verdict"] == "PASS" and all(wrapper["checks"].values()))
        require(name + ":formal_task_count", wrapper["formal_task_count"] == manifest["task_count"] == 90)
        require(name + ":source_note_typo_confirmed", "pilot_only" in wrapper["audit_note"])
        formal[name] = {
            "original_scope": original["scope"], "wrapper_scope": wrapper["scope"],
            "wrapper_verdict": wrapper["verdict"], "task_count": manifest["task_count"],
            "metadata_correction": "The original metric_audit.json retains scope=preflight_only. The wrapper is a post-run formal_run audit. Its audit_note uses pilot_only in error.",
        }

    base = "results/mechanism_stress_v2_vm_v10/"
    tasks = read_rows(base + "tasks_recoverable_failure_v1.jsonl")
    require("stress:three_tasks_missing_metadata_condition", len(tasks) == 3 and all("condition" not in task["metadata"] for task in tasks))
    summary = read_json(base + "summary.json")
    pair_audit = read_json(base + "pair_audit.json")
    require("stress:recorded_pair_audit", pair_audit["status"] == "PASS" and pair_audit["same_failure_time_state_count"] == 3)
    methods = {}
    rows_by_method = {}
    six_checks = {"completion", "deadline", "assignment_and_capability", "dependency", "budget", "immutable_completed_prefix"}
    for method, filename, expected_owner in (
        ("Simple-local-reassignment", "Simple_local_reassignment.jsonl", "uav_2"),
        ("CDS-MR", "CDS_MR.jsonl", "uav_3"),
    ):
        rows = read_rows(base + filename)
        rows_by_method[method] = {row["task_id"]: row for row in rows}
        require(method + ":three_unique_rows", len(rows) == len(rows_by_method[method]) == 3)
        successful = 0
        for row in rows:
            metrics = row["standard_metrics"]
            final = row["final_execution_record"]["gym_metrics"]
            event = row["failure_event"]
            initial = row["initial_execution_record"]["gym_metrics"]
            successful += int(row["condition"] == "recoverable_failure" and isinstance(initial.get("first_failure"), dict) and final.get("task_success") is True)
            label = method + ":" + row["task_id"]
            require(label + ":strict_terminal_completion", row["runner_status"] == "completed" and final["task_success"] is True and metrics["strict_contract_satisfied"] is True and set(metrics["contract_checks"]) == six_checks and all(value is True for value in metrics["contract_checks"].values()))
            require(label + ":missing_original_recovery_field", metrics["recovery_success_rate_item"] is None)
            require(label + ":intended_failure", event["reason"] == "comparative_repair_choice_after_partial_execution" and event["drone_id"] == "uav_1" and event["details"]["bundle_id"] == "B2")
            require(label + ":selected_owner", final["bundle_assignment"]["B2"] == expected_owner)
        recorded = summary["method_rows"][method]
        require(method + ":summary_missingness", recorded["recoverable_success_count"] == 0 and recorded["recoverable_success_rate"] is None and recorded["strict_contract_satisfied_count"] == 3)
        require(method + ":three_recomputed_recoveries", successful == 3)
        methods[method] = {
            "rows": len(rows), "terminal_task_success_count": successful,
            "strict_contract_satisfied_count": 3, "recomputed_recovery_count": successful,
            "recomputed_recovery_rate": successful / len(rows),
            "original_recovery_items": [row["standard_metrics"]["recovery_success_rate_item"] for row in rows],
            "original_summary_recoverable_success_count": recorded["recoverable_success_count"],
            "npsd_mean": sum(row["standard_metrics"]["normalized_plan_stability_distance"] for row in rows) / len(rows),
            "selected_B2_owner": expected_owner,
        }
    left, right = rows_by_method.values()
    require("stress:paired_task_ids", set(left) == set(right))
    require("stress:paired_failure_states", all(state_for_comparison(left[task_id]) == state_for_comparison(right[task_id]) for task_id in left))

    scenario = "scenarios/independent_task_distribution_b3_v1/"
    freeze = read_json(scenario + "freeze_manifest.json")
    generator = ROOT / scenario / freeze["generator_path"]
    INPUT_HASHES[str(generator.relative_to(ROOT)).replace("\\", "/")] = digest(generator)
    require("b3:frozen_generator_restored", digest(generator) == freeze["generator_sha256"] == digest(ROOT / "code/build_b3_independent_tasks_v1.py"))
    formal_tasks = read_rows(scenario + "tasks.jsonl")
    pilot_tasks = read_rows(scenario + "pilot_3.jsonl")
    shared_ids = sorted({task["task_id"] for task in formal_tasks} & {task["task_id"] for task in pilot_tasks})
    formal_seeds = {task["metadata"]["run_seed"] for task in formal_tasks}
    pilot_seeds = {task["metadata"]["run_seed"] for task in pilot_tasks}
    require("b3:documented_id_collision", len(shared_ids) == 3)
    require("b3:pilot_formal_seeds_disjoint", not (formal_seeds & pilot_seeds))
    require("b3:formal_input_hash", digest(ROOT / scenario / freeze["formal_input_path"]) == freeze["formal_input_sha256"])

    observation = read_json("provenance/vm_environment_20261003.json")
    require("environment:dated_live_observation", observation["observed_at_utc"] == "2026-10-03T00:43:28Z")
    require("environment:hardware_matches_description", observation["hardware"]["logical_cpus"] == 8 and observation["hardware"]["mem_total_kib"] == 16324000 and observation["os"]["pretty_name"] == "Ubuntu 24.04.4 LTS")

    report = {
        "closeout_date": "2026-10-03", "verification_type": "read_only_deterministic_metadata_and_row_consistency",
        "deterministic_checks": "PASS" if all(CHECKS.values()) else "FAIL",
        "checks_passed": sum(CHECKS.values()), "checks_total": len(CHECKS),
        "formal_audit_metadata": formal, "stress_corrected_interpretation": methods,
        "b3_archive": {"generator_sha256": freeze["generator_sha256"], "pilot_formal_shared_task_ids": shared_ids, "pilot_formal_seeds_disjoint": not (formal_seeds & pilot_seeds), "cross_directory_join_key": ["data_partition", "run_seed", "task_id"]},
        "environment": {"historical_software_source": "provenance/environment.txt and frozen run manifests", "live_hardware_observation": "provenance/vm_environment_20261003.json", "current_system_python": "3.12.3", "current_uav_exp_venv_python": "3.14.6", "historical_hardware_capture_available": False},
        "remaining_limits": ["The live hardware capture is retrospective; it cannot establish historical hardware settings.", "The current uav_exp/venv differs from the recorded Python 3.12.3 experiment environment and needs restoration before rerunning.", "The stress recovery correction is derived from archived final task success and intended failure records; original null values remain preserved.", "This closeout has no completed fresh semantic reviewer; its checks are deterministic, not an independent review acceptance."],
        "checks": CHECKS, "audited_input_sha256": INPUT_HASHES,
        "verification_script_sha256": digest(Path(__file__)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(CHECKS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
