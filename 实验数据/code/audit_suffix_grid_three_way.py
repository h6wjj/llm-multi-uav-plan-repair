"""Independently recompute three-arm preflight paths, costs and timing.

Reads raw bridge responses and task records; never replaces a failed/missing
metric with a favorable value.  Pilot PASS is a run gate, not a generality
claim for the 90 frozen formal instances.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any


METHOD_FILES = {
    "Simple-local-reassignment": "Simple_local_reassignment.jsonl",
    "CDS-MR": "CDS_MR.jsonl",
    "Global-total-route-reallocation": "Global_total_route_reallocation.jsonl",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def xyz(value: Any) -> list[float]:
    if isinstance(value, dict):
        return [float(value[key]) for key in ("x", "y", "z")]
    return [float(x) for x in value]


def route_cost(start: list[float], waypoints: list[Any]) -> float:
    points = [xyz(point) for point in waypoints]
    return math.dist(start, points[0]) + sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def plan_route_cost(plan: list[dict[str, Any]], live_positions: dict[str, list[float]]) -> float:
    cursors = {owner: list(position) for owner, position in live_positions.items()}
    total = 0.0
    for item in sorted(plan, key=lambda row: int(row["sequence"])):
        owner = str(item["assigned_drone"])
        points = item["remaining_waypoints"]
        total += route_cost(cursors[owner], points)
        cursors[owner] = xyz(points[-1])
    return total


def independent_npsd(row: dict[str, Any]) -> float:
    """Rebuild action-set distance from planned remainder and executed moves."""
    quantize = lambda point: tuple(int(round(value / 0.05)) for value in xyz(point))
    reference = {
        (str(item["assigned_drone"]), str(item["bundle_id"]), quantize(point))
        for item in row["dynamic_slice"]["remaining_bundle_suffixes"]
        for point in item["remaining_waypoints"]
    }
    repaired = {
        (str(command["assigned_drone"]), str(command["bundle_id"]), quantize(point["position"]))
        for command in row["final_execution_record"]["structured_commands"]
        for point in (command.get("response") or {}).get("waypoint_results", [])
        if isinstance(point, dict) and point.get("success") and point.get("position") is not None
    }
    union = reference | repaired
    return round(len(reference ^ repaired) / len(union), 6) if union else 0.0


def stable_state(row: dict[str, Any]) -> dict[str, Any]:
    initial = row["initial_execution_record"]["gym_metrics"]
    failure = row["failure_event"]
    return {
        "failure_step": failure["step"],
        "failure_reason": failure["reason"],
        "failed_drone": failure["drone_id"],
        "completed_waypoints_by_bundle": initial["completed_waypoints_by_bundle"],
        "bundle_assignment": initial["bundle_assignment"],
        "unavailable_drone_ids": initial["unavailable_drone_ids"],
        "drone_positions": {
            str(drone["id"]): [round(value, 7) for value in xyz(drone["position"])]
            for drone in initial["final_drones"]
        },
    }


def recompute_metrics(row: dict[str, Any]) -> dict[str, Any]:
    repair = row["repair"]
    initial = row["initial_execution_record"]["gym_metrics"]
    final_record = row["final_execution_record"]
    final = final_record["gym_metrics"]
    standard = row["standard_metrics"]
    segments = [
        point
        for command in final_record.get("structured_commands", [])
        for point in (command.get("response") or {}).get("waypoint_results", [])
        if isinstance(point, dict) and "steps" in point
    ]
    telemetry_complete = bool(segments) and all(isinstance(point.get("path_length_m"), (int, float)) for point in segments)
    actual_path = sum(float(point["path_length_m"]) for point in segments) if telemetry_complete else None
    solver_ns = int(repair["solver_finished_monotonic_ns"]) - int(repair["solver_started_monotonic_ns"])
    failure_wall = float(initial["first_failure"]["wall_time_seconds"])
    command_starts = [
        float(command["wall_started_seconds"])
        for command in final.get("command_history", [])
        if command.get("success") and command.get("wall_started_seconds") is not None
        and float(command["wall_started_seconds"]) >= failure_wall
    ]
    decision_latency = max(0.0, min(command_starts) - failure_wall) if command_starts else None
    live_positions = {str(drone["id"]): xyz(drone["position"]) for drone in initial["final_drones"]}
    estimated = plan_route_cost(repair["repair_plan"], live_positions)
    npsd = independent_npsd(row)
    def near(left: Any, right: Any, tolerance: float) -> bool:
        return left is not None and right is not None and abs(float(left) - float(right)) <= tolerance
    checks = {
        "raw_path_telemetry_present": telemetry_complete,
        "actual_path_matches_standard_metric": near(actual_path, standard.get("executed_suffix_path_length_m"), 2e-8),
        "actual_path_matches_repair_trajectory": near(actual_path, (row.get("repair_trajectory") or {}).get("executed_suffix_path_length_m"), 2e-8),
        "segment_count_matches": len(segments) == standard.get("executed_waypoint_segments"),
        "solver_clock_monotonic": solver_ns >= 0,
        "solver_duration_matches": near(solver_ns / 1e9, standard.get("repair_solver_wallclock_seconds"), 2e-9),
        "decision_latency_matches": near(decision_latency, standard.get("repair_decision_latency_seconds"), 2e-6),
        "estimated_route_matches": near(estimated, standard.get("estimated_full_suffix_route_cost"), 2e-8),
        "npsd_matches_independent_action_sets": near(npsd, standard.get("normalized_plan_stability_distance"), 1e-6),
        "mission_time_matches_bridge": near(final.get("sim_time_seconds"), standard.get("mission_completion_time_seconds"), 2e-8),
        "path_and_solver_units_positive": actual_path is not None and actual_path > 0 and solver_ns > 0,
        "strict_and_recovered": standard.get("strict_contract_satisfied") is True and standard.get("recovery_success_rate_item") is True,
    }
    return {
        "checks": checks,
        "executed_suffix_path_length_m": round(actual_path, 6) if actual_path is not None else None,
        "repair_solver_wallclock_seconds": round(solver_ns / 1e9, 6),
        "repair_decision_latency_seconds": round(decision_latency, 6) if decision_latency is not None else None,
        "estimated_full_suffix_route_cost": round(estimated, 6),
        "mission_completion_time_seconds": round(float(final["sim_time_seconds"]), 6),
        "selected_B2_owner": repair["solver_audit"]["selected_assignment"].get("B2"),
        "npsd": npsd,
    }


def global_optimum_check(task: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    repair = row["repair"]
    audit = repair["solver_audit"]
    initial = row["initial_execution_record"]["gym_metrics"]
    positions = {str(drone["id"]): xyz(drone["position"]) for drone in initial["final_drones"]}
    unavailable = set(initial["unavailable_drone_ids"])
    agents = {str(agent["agent_id"]): agent for agent in task["agents"]}
    bundles = {str(bundle["bundle_id"]): bundle for bundle in task["contract"]["mission"]["task_bundles"].values()}
    suffixes = {
        str(item["bundle_id"]): item["remaining_waypoints"]
        for item in row["dynamic_slice"]["remaining_bundle_suffixes"]
    }
    completed = {
        bundle_id for bundle_id, bundle in bundles.items()
        if int(initial["completed_waypoints_by_bundle"].get(bundle_id, 0)) >= len(bundle["waypoints"])
    }
    dependencies = task["contract"]["mission"].get("bundle_dependencies") or {}
    pending = set(suffixes)
    order = []
    while pending:
        ready = sorted(
            (bundle_id for bundle_id in pending
             if all(str(parent) in completed or str(parent) in order for parent in dependencies.get(bundle_id, []))),
            key=lambda bundle_id: (int(bundles[bundle_id]["order"]), bundle_id),
        )
        if not ready:
            raise ValueError("task_dependency_graph_has_no_valid_remaining_order")
        order.append(ready[0])
        pending.remove(ready[0])
    reported_plan = sorted(repair["repair_plan"], key=lambda value: value["sequence"])
    choices = []
    for bundle_id in order:
        bundle = bundles[bundle_id]
        eligible_ids = {str(item) for item in bundle.get("eligible_drone_ids") or agents}
        roles = {str(item) for item in bundle.get("eligible_roles") or []}
        required = {str(item) for item in bundle.get("required_capabilities") or []}
        available = sorted(
            owner for owner in eligible_ids
            if owner in agents and owner in positions and owner not in unavailable
            and (not roles or str(agents[owner]["role"]) in roles)
            and required.issubset(set(agents[owner]["capabilities"]))
        )
        if not bundle.get("assignment_is_decision_variable", True):
            old_owner = str(initial["bundle_assignment"][bundle_id])
            available = [old_owner] if old_owner in available else []
        choices.append(available)
    costs = []
    for owners in itertools.product(*choices):
        plan = [{"sequence": i, "assigned_drone": owner, "remaining_waypoints": suffixes[bundle_id]}
                for i, (bundle_id, owner) in enumerate(zip(order, owners), start=1)]
        costs.append((round(plan_route_cost(plan, positions), 12), owners))
    optimum = min(costs) if costs else None
    selected = tuple(audit["selected_assignment"][bundle_id] for bundle_id in order)
    return {
        "enumerated_assignment_count": len(costs),
        "independent_optimum_cost": round(optimum[0], 9) if optimum else None,
        "selected_tuple": list(selected),
        "checks": {
            "plan_order_matches_task_dependencies": [str(item["bundle_id"]) for item in reported_plan] == order,
            "plan_suffix_matches_dynamic_slice": all(
                item["remaining_waypoints"] == suffixes[str(item["bundle_id"])] for item in reported_plan
            ) and len(reported_plan) == len(order),
            "candidate_product_count_matches": len(costs) == audit["enumerated_assignment_count"],
            "selected_is_independent_cost_optimum": optimum is not None and selected == optimum[1],
            "reported_objective_matches": optimum is not None and abs(optimum[0] - float(audit["estimated_full_suffix_route_cost"])) <= 2e-8,
        },
    }


def audit(tasks_path: Path, results_dir: Path, require_success: bool) -> dict[str, Any]:
    tasks = {str(task["task_id"]): task for task in read_jsonl(tasks_path)}
    manifest = json.loads((results_dir / "run_manifest.json").read_text(encoding="utf-8"))
    rows = {method: {str(row["task_id"]): row for row in read_jsonl(results_dir / filename)}
            for method, filename in METHOD_FILES.items()}
    checks = {
        "input_hash_matches_manifest": hashlib.sha256(tasks_path.read_bytes()).hexdigest() == manifest["source_input_sha256"],
        "three_methods_recorded": set(manifest["methods"]) == set(METHOD_FILES),
        "all_expected_rows_present": all(set(method_rows) == set(tasks) for method_rows in rows.values()),
    }
    cases = []
    for task_id, task in tasks.items():
        methods = {method: rows[method].get(task_id) for method in METHOD_FILES}
        if not all(methods.values()):
            cases.append({"task_id": task_id, "error": "missing_method_record"})
            continue
        complete = all(row.get("runner_status") == "completed" for row in methods.values())
        if not complete:
            cases.append({"task_id": task_id, "error": "runner_error", "statuses": {m: r.get("runner_status") for m, r in methods.items()}})
            continue
        states = [stable_state(row) for row in methods.values()]
        method_metrics = {method: recompute_metrics(row) for method, row in methods.items()}
        global_check = global_optimum_check(task, methods["Global-total-route-reallocation"])
        bundles = {str(bundle["bundle_id"]): bundle for bundle in task["contract"]["mission"]["task_bundles"].values()}
        repair_assignments_eligible = all(
            str(item["assigned_drone"]) in {str(agent) for agent in bundles[str(item["bundle_id"])].get("eligible_drone_ids") or []}
            for row in methods.values()
            for item in row["repair"]["repair_plan"]
        )
        profile = task["scenario"]["failure_model"]
        failed_bundle = str(profile["bundle_id"])
        intended_partial_failure = all(
            row["failure_event"].get("reason") == profile["type"]
            and str((row["failure_event"].get("details") or {}).get("bundle_id")) == failed_bundle
            and int((row["failure_event"].get("details") or {}).get("completed_waypoints_before_failure") or -1)
                == int(profile["after_completed_waypoints"])
            and int(row["initial_execution_record"]["gym_metrics"]["completed_waypoints_by_bundle"].get(failed_bundle, -1))
                == int(profile["after_completed_waypoints"])
            and row["repair"].get("triggered") is True
            for row in methods.values()
        )
        cases.append({
            "task_id": task_id,
            "same_physical_failure_state": all(state == states[0] for state in states[1:]),
            "intended_partial_failure": intended_partial_failure,
            "repair_assignments_eligible": repair_assignments_eligible,
            "methods": method_metrics,
            "global_optimum": global_check,
        })
    checks["all_three_way_failure_states_equal"] = len(cases) == len(tasks) and all(case.get("same_physical_failure_state") is True for case in cases)
    checks["all_intended_partial_failures"] = len(cases) == len(tasks) and all(case.get("intended_partial_failure") is True for case in cases)
    checks["all_repair_assignments_eligible"] = len(cases) == len(tasks) and all(case.get("repair_assignments_eligible") is True for case in cases)
    checks["all_metric_recomputations_match"] = len(cases) == len(tasks) and all(
        all(
            all(value for name, value in metric["checks"].items() if name != "strict_and_recovered")
            for metric in case.get("methods", {}).values()
        ) for case in cases
    )
    checks["global_solver_independently_optimal"] = len(cases) == len(tasks) and all(
        all(case.get("global_optimum", {}).get("checks", {}).values()) for case in cases
    )
    if require_success:
        checks["all_pilot_runs_completed_and_strict"] = len(cases) == len(tasks) and all(
            all(metric["checks"]["strict_and_recovered"] for metric in case.get("methods", {}).values()) for case in cases
        )
    return {
        "protocol": "suffix_grid_three_way_metric_audit_v1",
        "scope": "preflight_only" if require_success else "formal_descriptive_audit",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "task_count": len(tasks),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-success", action="store_true")
    parser.add_argument("--formal-tasks", type=Path, help="verify pilot IDs and seeds are disjoint from formal rows")
    args = parser.parse_args()
    report = audit(args.tasks, args.results, args.require_success)
    if args.formal_tasks:
        pilot = read_jsonl(args.tasks)
        formal = read_jsonl(args.formal_tasks)
        pilot_ids = {str(item["task_id"]) for item in pilot}
        formal_ids = {str(item["task_id"]) for item in formal}
        pilot_seeds = {int(item["metadata"]["layout_seed"]) for item in pilot}
        formal_seeds = {int(item["metadata"]["layout_seed"]) for item in formal}
        report["checks"]["pilot_formal_task_ids_disjoint"] = not bool(pilot_ids & formal_ids)
        report["checks"]["pilot_formal_layout_seeds_disjoint"] = not bool(pilot_seeds & formal_seeds)
        report["formal_task_sha256"] = hashlib.sha256(args.formal_tasks.read_bytes()).hexdigest()
        report["verdict"] = "PASS" if all(report["checks"].values()) else "FAIL"
    report["audit_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["audited_input_sha256"] = hashlib.sha256(args.tasks.read_bytes()).hexdigest()
    report["audit_command"] = [
        "audit_suffix_grid_three_way.py", "--tasks", str(args.tasks),
        "--results", str(args.results), "--output", str(args.output),
        *(["--require-success"] if args.require_success else []),
        *(["--formal-tasks", str(args.formal_tasks)] if args.formal_tasks else []),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "checks": report["checks"], "task_count": report["task_count"]}, ensure_ascii=False, indent=2))
    if report["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
