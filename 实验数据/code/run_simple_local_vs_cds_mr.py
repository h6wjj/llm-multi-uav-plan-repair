"""Paired replacement ablation: simple local reassignment vs CDS-MR.

Both arms use the same recoverable-failure task records, the same structured
initial execution protocol, an isolated fresh bridge, and the same runtime
checks.  The simple arm greedily replaces only the failed/ineligible bundle
using the lowest live-state route cost with a deterministic UAV-id tie-break;
it does not compute a causal dependency closure or the CDS-MR lexicographic
objective.  This is deliberately a replacement ablation, not a weakened
constraint baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

from run_agent4drone_parallel import start_bridge, terminate_process, wait_health
from global_suffix_reallocation import global_total_route_plan
from run_cds_mr_contract_online import (
    _compatible,
    _contract_intent,
    _decision_latency,
    _dynamic_slice,
    _live_agent_view,
    _ordered_bundles,
    _plan_distance,
    _route_cost,
    _structured_repair_plan,
    _topological_remaining_order,
    _xyz,
)
from run_v6_3_preexec_ablation import get_agents, get_bundles, info, read_jsonl
from v6_metric_evaluator import common_metrics


PROTOCOL_VERSION = "simple_local_vs_cds_mr_paired_v1"
METHODS = ("Simple-local-reassignment", "CDS-MR")
GLOBAL_METHOD = "Global-total-route-reallocation"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_tasks(path: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    rows = [
        row for row in rows
        if str((row.get("metadata") or {}).get("condition_label"))
        == "recoverable_failure_uncertain"
    ]
    rows.sort(key=lambda row: (
        int((row.get("metadata") or {}).get("run_seed_index", 0)),
        str((row.get("metadata") or {}).get("source_task_id", "")),
        str(row.get("task_id", "")),
    ))
    if not rows:
        raise RuntimeError("no_recoverable_failure_uncertain_tasks")
    return rows


def get_metrics(url: str) -> dict[str, Any]:
    response = requests.get(url + "/bridge/metrics", timeout=20)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("bridge_metrics_not_object")
    return value


def move(url: str, drone: str, bundle_id: str, waypoints: list[Any]) -> dict[str, Any]:
    response = requests.post(
        url + "/drones/" + str(drone) + "/command/move_along_path",
        json={"waypoints": waypoints, "allow_partial_move": True, "bundle_id": bundle_id},
        timeout=120,
    )
    response.raise_for_status()
    value = response.json()
    return value if isinstance(value, dict) else {"success": False, "error": "non_object_response"}


def execute_initial(task: dict[str, Any], url: str) -> dict[str, Any]:
    """Identical contract-bound initial execution for both comparison arms."""

    by_id = {str(bundle["bundle_id"]): bundle for bundle in get_bundles(task)}
    records: list[dict[str, Any]] = []
    for bundle in sorted(by_id.values(), key=lambda item: (int(item.get("order", 0)), str(item["bundle_id"]))):
        bundle_id = str(bundle["bundle_id"])
        drone = str(bundle.get("preferred_drone_id") or bundle.get("original_owner") or bundle.get("drone_id"))
        started = time.perf_counter()
        try:
            response = move(url, drone, bundle_id, list(bundle.get("waypoints") or []))
        except Exception as exc:  # keep the raw failure in the run record
            response = {"success": False, "error": repr(exc)}
        records.append({
            "sequence": bundle.get("order"),
            "bundle_id": bundle_id,
            "assigned_drone": drone,
            "waypoints": bundle.get("waypoints") or [],
            "response": response,
            "seconds": round(time.perf_counter() - started, 6),
        })
        if not response.get("success"):
            break
    return {
        "structured_commands": records,
        "gym_metrics": get_metrics(url),
        "execution_mode": "common_bridge_structured_initial_v1",
    }


def execute_repair(url: str, plan: list[dict[str, Any]], method: str) -> dict[str, Any]:
    started = time.perf_counter()
    records: list[dict[str, Any]] = []
    status = "completed"
    for item in sorted(plan, key=lambda row: (int(row.get("sequence", 0)), str(row.get("bundle_id")))):
        command_started = time.perf_counter()
        try:
            response = move(
                url,
                str(item["assigned_drone"]),
                str(item["bundle_id"]),
                list(item.get("remaining_waypoints") or []),
            )
        except Exception as exc:
            response = {"success": False, "error": repr(exc)}
        records.append({
            "sequence": item.get("sequence"),
            "bundle_id": str(item["bundle_id"]),
            "assigned_drone": str(item["assigned_drone"]),
            "waypoints": list(item.get("remaining_waypoints") or []),
            "response": response,
            "seconds": round(time.perf_counter() - command_started, 6),
        })
        if not response.get("success"):
            status = "bridge_rejected_repair_command"
            break
    return {
        "status": status,
        "api_success": False,
        "token_usage": None,
        "tool_calls": [],
        "structured_commands": records,
        "gym_metrics": get_metrics(url),
        "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
        "execution_mode": "same_live_bridge_structured_repair_" + method,
    }


def _failure_bundle_id(task: dict[str, Any], dynamic_slice: dict[str, Any]) -> str:
    failure = dynamic_slice.get("first_failure") or {}
    details = failure.get("details") if isinstance(failure, dict) else {}
    policy = (task.get("contract") or {}).get("failure_policy") or {}
    value = details.get("bundle_id") if isinstance(details, dict) else None
    value = value or (policy.get("bundle_id") if isinstance(policy, dict) else None)
    return str(value or "")


def _current_assignment(initial_metrics: dict[str, Any], suffix: dict[str, Any], bundle: dict[str, Any]) -> str:
    assignment = initial_metrics.get("bundle_assignment") or {}
    value = assignment.get(str(bundle["bundle_id"])) if isinstance(assignment, dict) else None
    return str(value or suffix.get("assigned_drone") or bundle.get("original_owner") or bundle.get("drone_id"))


def simple_local_reassignment_plan(
    task: dict[str, Any],
    initial_metrics: dict[str, Any],
    dynamic_slice: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Greedy, constraint-valid local replacement of only directly affected work."""

    bundles = _ordered_bundles(task)
    by_id = {str(bundle["bundle_id"]): bundle for bundle in bundles}
    suffixes = {
        str(item.get("bundle_id")): dict(item)
        for item in dynamic_slice.get("remaining_bundle_suffixes", [])
        if isinstance(item, dict) and item.get("bundle_id")
    }
    if not suffixes:
        raise RuntimeError("simple_local_no_remaining_suffixes")

    progress = initial_metrics.get("completed_waypoints_by_bundle") or {}
    progress = progress if isinstance(progress, dict) else {}
    completed_ids = {
        str(bundle["bundle_id"])
        for bundle in bundles
        if int(progress.get(str(bundle["bundle_id"]), 0) or 0) >= len(bundle.get("waypoints") or [])
    }
    mission = (task.get("contract") or {}).get("mission") or {}
    dependencies = mission.get("bundle_dependencies") or {}
    order = _topological_remaining_order(bundles, set(suffixes), completed_ids, dependencies)
    agents, unavailable = _live_agent_view(task, initial_metrics, dynamic_slice)
    failed_bundle = _failure_bundle_id(task, dynamic_slice)
    failure = dynamic_slice.get("first_failure") or {}
    failed_drone = str(failure.get("drone_id") or "") if isinstance(failure, dict) else ""

    positions: dict[str, list[float]] = {}
    for agent_id, agent in agents.items():
        point = _xyz(agent.get("position")) or _xyz(agent.get("start"))
        if point is not None:
            positions[str(agent_id)] = point

    assignments: dict[str, str] = {}
    changed: list[str] = []
    candidate_graph: dict[str, list[str]] = {}
    route_costs: dict[str, dict[str, float]] = {}

    # Fixed topological order + greedy local replacement.  There is no
    # dependency closure expansion: downstream bundles remain owner-preserved
    # unless their own live owner is unavailable/incompatible.
    for bundle_id in order:
        bundle = by_id[bundle_id]
        suffix = suffixes[bundle_id]
        current = _current_assignment(initial_metrics, suffix, bundle)
        direct_issue = bundle_id == failed_bundle or current in unavailable or current not in agents or not _compatible(agents[current], bundle)
        if not direct_issue:
            assignments[bundle_id] = current
            continue

        allowed = [str(value) for value in (bundle.get("eligible_drone_ids") or agents.keys())]
        blocked = set(unavailable)
        if bundle_id == failed_bundle and failed_drone:
            blocked.add(failed_drone)
        candidates = sorted(
            agent_id for agent_id in allowed
            if agent_id in agents and agent_id not in blocked and _compatible(agents[agent_id], bundle)
        )
        if not candidates:
            raise RuntimeError("simple_local_no_compatible_candidate:" + bundle_id)
        candidate_graph[bundle_id] = candidates
        costs = {
            agent_id: round(_route_cost(positions[agent_id], suffix.get("remaining_waypoints") or []), 12)
            for agent_id in candidates
            if agent_id in positions
        }
        if not costs:
            raise RuntimeError("simple_local_no_live_position:" + bundle_id)
        route_costs[bundle_id] = costs
        selected = min(candidates, key=lambda agent_id: (costs.get(agent_id, math.inf), agent_id))
        assignments[bundle_id] = selected
        if selected != current:
            changed.append(bundle_id)

    plan = [
        {
            "sequence": index,
            "bundle_id": bundle_id,
            "assigned_drone": assignments[bundle_id],
            "original_assignment": _current_assignment(initial_metrics, suffixes[bundle_id], by_id[bundle_id]),
            "directly_affected": bundle_id in changed,
            "remaining_waypoints": suffixes[bundle_id].get("remaining_waypoints") or [],
            "dependencies": [str(value) for value in dependencies.get(bundle_id, []) or []],
        }
        for index, bundle_id in enumerate(order, start=1)
    ]
    unaffected = [bundle_id for bundle_id in order if bundle_id not in changed]
    audit = {
        "executor": "simple_local_reassignment_v1",
        "solver_status": "deterministic_greedy_direct_scope",
        "solver_inputs": ["task_contract", "failure_time_state", "directly_affected_remaining_bundle", "live_bridge_state"],
        "excluded_inputs": ["causal_dependency_closure", "minimum_edit_lexicographic_objective", "successful_reference_trace", "baseline_outputs"],
        "directly_affected_bundle_id": failed_bundle,
        "changed_bundle_ids": changed,
        "unaffected_bundle_ids": unaffected,
        "candidate_graph": candidate_graph,
        "route_costs": route_costs,
        "selected_assignment": assignments,
        "rule": "reassign only the failed or independently invalid remaining bundle; choose minimum individual live-state route cost; tie-break by UAV id; preserve topological order",
        "hard_constraints_delegated_to_bridge": ["remaining_waypoint_sequence", "role_and_capability_eligibility", "bundle_dependencies", "shared_execution_budget", "deadline", "live_uav_availability"],
        "not_a_weakened_baseline": True,
    }
    return plan, audit


def task_port(task: dict[str, Any], method: str, base: int) -> int:
    key = str(task.get("task_id")) + "::" + method
    return base + int(hashlib.sha256(key.encode()).hexdigest()[:6], 16) % 1500


def estimated_full_suffix_route_cost(
    plan: list[dict[str, Any]], initial_metrics: dict[str, Any],
) -> float:
    """Evaluate every arm's plan with one live-state geometric cost rule."""
    cursors = {
        str(agent.get("id") or agent.get("agent_id")): _xyz(agent.get("position"))
        for agent in initial_metrics.get("final_drones", [])
        if isinstance(agent, dict)
    }
    total = 0.0
    for item in sorted(plan, key=lambda row: (int(row.get("sequence", 0)), str(row.get("bundle_id")))):
        owner = str(item["assigned_drone"])
        waypoints = item.get("remaining_waypoints") or []
        if owner not in cursors or not waypoints:
            raise RuntimeError("estimated_suffix_route_cost_missing_live_position_or_waypoints")
        total += _route_cost(cursors[owner], waypoints)
        endpoint = _xyz(waypoints[-1])
        if endpoint is None:
            raise RuntimeError("estimated_suffix_route_cost_invalid_endpoint")
        cursors[owner] = endpoint
    return round(total, 9)


def actual_repair_trajectory(final: dict[str, Any]) -> dict[str, Any]:
    """Sum motor-executed per-step paths, not waypoint chord lengths.

    This is the commanded repair UAV's path over executed repair waypoints;
    idle-UAV drift is outside this particular metric's scope.
    """
    commands = final.get("structured_commands") or []
    segments = [
        item
        for command in commands
        for item in ((command.get("response") or {}).get("waypoint_results") or [])
        if isinstance(item, dict) and "steps" in item
    ]
    if not segments:
        return {"executed_suffix_path_length_m": None, "executed_waypoint_segments": 0,
                "all_segments_have_path_telemetry": False, "scope": "commanded_repair_UAV_only"}
    complete = all(isinstance(item.get("path_length_m"), (int, float)) for item in segments)
    value = round(sum(float(item.get("path_length_m") or 0.0) for item in segments), 9) if complete else None
    return {
        "executed_suffix_path_length_m": value,
        "executed_waypoint_segments": len(segments),
        "all_segments_have_path_telemetry": complete,
        "scope": "commanded_repair_UAV_only",
    }


def run_one(task: dict[str, Any], method: str, args: argparse.Namespace, log_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    bridge = None
    safe = hashlib.sha256(str(task.get("task_id")).encode()).hexdigest()[:16]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (safe + ".log")
    handle = log_path.open("w", encoding="utf-8")
    port = task_port(task, method, args.bridge_port_base)
    url = f"http://127.0.0.1:{port}"
    common = {
        "protocol_version": args.protocol_version,
        "method": method,
        "method_definition": method,
        "task_id": task.get("task_id"),
        **info(task),
        "implementation_scope": "paired_platform_adapted_repair_comparison",
    }
    try:
        bridge = start_bridge(task, SimpleNamespace(input=args.input, bridge_max_steps=None), port, handle)
        health = wait_health(url, args.startup_timeout)
        if str(health.get("task_id")) != str(task.get("task_id")):
            raise RuntimeError("bridge_task_mismatch")
        initial = execute_initial(task, url)
        initial_metrics = initial.get("gym_metrics") or {}
        failure = initial_metrics.get("first_failure")
        dynamic = _dynamic_slice(task, initial)
        repair: dict[str, Any] = {"triggered": False, "same_live_bridge": False}
        final = initial
        if isinstance(failure, dict) and dynamic is not None:
            repair["triggered"] = True
            try:
                repair["repair_window"] = requests.post(url + "/bridge/repair-window", timeout=20).json()
            except Exception as exc:
                repair["repair_window"] = {"enabled": False, "error": repr(exc)}
            solver_started_ns = time.perf_counter_ns()
            if method == "Simple-local-reassignment":
                plan, audit = simple_local_reassignment_plan(task, initial_metrics, dynamic)
                repair["executor"] = "simple_local_reassignment_v1"
            elif method == "CDS-MR":
                plan, audit = _structured_repair_plan(task, initial_metrics, dynamic)
                repair["executor"] = "structured_cds_mr_v2_feasibility_first"
            elif method == GLOBAL_METHOD:
                plan, audit = global_total_route_plan(task, initial_metrics, dynamic)
                repair["executor"] = "global_total_route_reallocation_v1"
            else:
                raise RuntimeError("unknown_comparison_method:" + method)
            solver_finished_ns = time.perf_counter_ns()
            repair["solver_started_monotonic_ns"] = solver_started_ns
            repair["solver_finished_monotonic_ns"] = solver_finished_ns
            repair["repair_solver_wallclock_seconds"] = round((solver_finished_ns - solver_started_ns) / 1e9, 9)
            repair["estimated_full_suffix_route_cost"] = estimated_full_suffix_route_cost(plan, initial_metrics)
            repair["repair_plan"] = plan
            repair["solver_audit"] = audit
            final = execute_repair(url, plan, method)
            repair["same_live_bridge"] = True
            final_metrics = final.get("gym_metrics") or {}
            before = initial_metrics.get("command_history") or []
            after = final_metrics.get("command_history") or []
            repair_history = after[len(before):] if len(after) >= len(before) else []
            repair["plan_distance"] = _plan_distance(dynamic, repair_history)
            repair["repair_decision_latency_seconds"] = _decision_latency(initial_metrics, final_metrics)
            repair["intent"] = _contract_intent(task, final.get("tool_calls", []), final_metrics)
            repair["intent_preserved"] = bool(repair["intent"].get("preserved"))
            repair["repair_success"] = bool(final_metrics.get("task_success")) and repair["intent_preserved"]
            repair["repair_history_count"] = len(repair_history)
        final_metrics = final.get("gym_metrics") or get_metrics(url)
        distance = repair.get("plan_distance") if isinstance(repair.get("plan_distance"), dict) else {}
        metrics = common_metrics(
            task,
            initial_metrics,
            final_metrics,
            plan_stability_distance=distance.get("plan_stability_distance"),
            action_distance=distance.get("action_distance"),
            repair_decision_latency_seconds=repair.get("repair_decision_latency_seconds"),
            runner_wallclock_seconds=round(time.perf_counter() - started, 6),
            mission_completion_time_seconds=final_metrics.get("sim_time_seconds") if final_metrics.get("task_success") else None,
            repair_attempted=bool(repair.get("triggered")),
        )
        trajectory = actual_repair_trajectory(final) if repair.get("triggered") else None
        metrics.update({
            "estimated_full_suffix_route_cost": repair.get("estimated_full_suffix_route_cost"),
            "executed_suffix_path_length_m": trajectory.get("executed_suffix_path_length_m") if trajectory else None,
            "executed_waypoint_segments": trajectory.get("executed_waypoint_segments") if trajectory else None,
            "repair_solver_wallclock_seconds": repair.get("repair_solver_wallclock_seconds"),
            "trajectory_scope": "commanded_repair_UAV_only",
            "repair_decision_latency_scope": "failure_event_to_first_successful_repair_command_wallclock",
            "repair_solver_time_scope": "plan_solver_only_excludes_bridge_HTTP_and_execution",
        })
        if repair.get("triggered"):
            bundles_by_id = {
                str(bundle["bundle_id"]): bundle for bundle in _ordered_bundles(task)
            }
            id_eligible = all(
                not bundles_by_id[str(item["bundle_id"])].get("eligible_drone_ids")
                or str(item["assigned_drone"]) in {
                    str(value) for value in bundles_by_id[str(item["bundle_id"])]["eligible_drone_ids"]
                }
                for item in repair.get("repair_plan", [])
            )
            metrics["strict_contract_satisfied_base"] = metrics["strict_contract_satisfied"]
            metrics["replacement_id_eligibility_satisfied"] = id_eligible
            metrics["strict_contract_satisfied"] = bool(metrics["strict_contract_satisfied"] and id_eligible)
        return {
            **common,
            "initial_execution_record": initial,
            "failure_event": failure,
            "dynamic_slice": dynamic,
            "repair": repair,
            "repair_trajectory": trajectory,
            "final_execution_record": final,
            "standard_metrics": metrics,
            "runner_status": "completed",
        }
    except Exception as exc:
        try:
            partial = get_metrics(url)
        except Exception:
            partial = None
        return {
            **common,
            "runner_status": "runner_error",
            "runner_error": repr(exc),
            "runner_traceback": traceback.format_exc(limit=8),
            "partial_metrics": partial,
        }
    finally:
        terminate_process(bridge)
        handle.close()


def stable(value: Any) -> Any:
    """Remove wall-clock fields while retaining the physical failure state."""

    # event_id is a bridge-generated UUID and is not part of the physical or
    # contractual failure state.  It must not make two isolated replicas of
    # the same preregistered failure look unpaired.
    drop = {"event_id", "wall_time_seconds", "elapsed_seconds", "duration_seconds", "runner_wallclock_seconds", "command_history", "sim_time_seconds"}
    if isinstance(value, dict):
        return {str(key): stable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])) if str(key) not in drop}
    if isinstance(value, list):
        return [stable(item) for item in value]
    if isinstance(value, float):
        return round(value, 10)
    return value


def state_signature(record: dict[str, Any]) -> str:
    initial = record.get("initial_execution_record") or {}
    metrics = initial.get("gym_metrics") or {}
    failure = record.get("failure_event") or {}
    selected = {
        "task_id": record.get("task_id"),
        "source_task_id": record.get("source_task_id"),
        "run_seed_index": record.get("run_seed_index"),
        "initial_commands": [
            {key: item.get(key) for key in ("sequence", "bundle_id", "assigned_drone", "waypoints")}
            for item in initial.get("structured_commands", [])
            if isinstance(item, dict)
        ],
        "completed_waypoints": metrics.get("completed_waypoints"),
        "completed_waypoints_by_bundle": metrics.get("completed_waypoints_by_bundle"),
        "bundle_assignment": metrics.get("bundle_assignment"),
        "unavailable_drone_ids": metrics.get("unavailable_drone_ids"),
        "final_drones": metrics.get("final_drones"),
        "first_failure": failure,
    }
    return hashlib.sha256(json.dumps(stable(selected), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finalize(output_dir: Path, tasks: list[dict[str, Any]], args: argparse.Namespace) -> None:
    rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for method in args.methods:
        path = output_dir / (method.replace("-", "_") + ".jsonl")
        values = []
        if path.exists():
            values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[method] = {str(value.get("task_id")): [value] for value in values}

    pairs: list[dict[str, Any]] = []
    state_mismatches: list[str] = []
    for task in tasks:
        task_id = str(task.get("task_id"))
        left = (rows[METHODS[0]].get(task_id) or [None])[0]
        right = (rows[METHODS[1]].get(task_id) or [None])[0]
        pair = {
            "task_id": task_id,
            "simple_present": left is not None,
            "cds_mr_present": right is not None,
            "simple_completed": bool(left and left.get("runner_status") == "completed"),
            "cds_mr_completed": bool(right and right.get("runner_status") == "completed"),
            "same_failure_time_state": False,
            "simple_state_signature": state_signature(left) if left else None,
            "cds_mr_state_signature": state_signature(right) if right else None,
        }
        if left and right:
            pair["same_failure_time_state"] = pair["simple_state_signature"] == pair["cds_mr_state_signature"]
            if not pair["same_failure_time_state"]:
                state_mismatches.append(task_id)
            pair["simple_recovery_success"] = (left.get("standard_metrics") or {}).get("recovery_success_rate_item")
            pair["cds_mr_recovery_success"] = (right.get("standard_metrics") or {}).get("recovery_success_rate_item")
            pair["simple_npsd"] = (left.get("standard_metrics") or {}).get("normalized_plan_stability_distance")
            pair["cds_mr_npsd"] = (right.get("standard_metrics") or {}).get("normalized_plan_stability_distance")
            pair["simple_repair_latency_s"] = (left.get("standard_metrics") or {}).get("repair_decision_latency_seconds")
            pair["cds_mr_repair_latency_s"] = (right.get("standard_metrics") or {}).get("repair_decision_latency_seconds")
            pair["simple_completion_time_s"] = (left.get("standard_metrics") or {}).get("mission_completion_time_seconds")
            pair["cds_mr_completion_time_s"] = (right.get("standard_metrics") or {}).get("mission_completion_time_seconds")
            pair["simple_changed_bundle_ids"] = ((left.get("repair") or {}).get("solver_audit") or {}).get("changed_bundle_ids", [])
            pair["cds_mr_reassigned_bundle_count"] = (((right.get("repair") or {}).get("solver_audit") or {}).get("objective") or {}).get("reassigned_bundle_count")
        pairs.append(pair)

    summary: dict[str, Any] = {
        "protocol_version": args.protocol_version,
        "task_count": len(tasks),
        "method_rows": {},
        "paired_state_audit": {
            "expected_pairs": len(tasks),
            "complete_pairs": sum(bool(pair["simple_present"] and pair["cds_mr_present"]) for pair in pairs),
            "same_failure_time_state_count": sum(bool(pair["same_failure_time_state"]) for pair in pairs),
            "state_mismatch_task_ids": state_mismatches,
            "status": "PASS" if len(pairs) == len(tasks) and not state_mismatches and all(pair["simple_completed"] and pair["cds_mr_completed"] for pair in pairs) else "WARN",
        },
    }
    if GLOBAL_METHOD in args.methods:
        triplets = []
        for task in tasks:
            task_id = str(task.get("task_id"))
            records = {method: (rows[method].get(task_id) or [None])[0] for method in args.methods}
            signatures = {method: state_signature(record) if record else None for method, record in records.items()}
            triplets.append({
                "task_id": task_id,
                "all_present": all(record is not None for record in records.values()),
                "all_completed": all(record is not None and record.get("runner_status") == "completed" for record in records.values()),
                "same_failure_time_state": len(set(signatures.values())) == 1 and None not in signatures.values(),
                "state_signatures": signatures,
            })
        summary["three_way_state_audit"] = {
            "expected_triplets": len(tasks),
            "complete_triplets": sum(item["all_present"] for item in triplets),
            "same_failure_time_state_count": sum(item["same_failure_time_state"] for item in triplets),
            "status": "PASS" if all(item["all_completed"] and item["same_failure_time_state"] for item in triplets) else "WARN",
        }
        (output_dir / "three_way_by_task.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in triplets),
            encoding="utf-8",
        )
        (output_dir / "three_way_state_audit.json").write_text(
            json.dumps(summary["three_way_state_audit"], ensure_ascii=False, indent=2), encoding="utf-8",
        )
    for method in args.methods:
        values = [item for item in rows[method].values() if item]
        records = [item[0] for item in values]
        metric_rows = [record.get("standard_metrics") or {} for record in records]
        def mean(key: str, predicate=lambda value: value is not None) -> float | None:
            nums = [float(row[key]) for row in metric_rows if key in row and predicate(row[key])]
            return round(sum(nums) / len(nums), 9) if nums else None
        summary["method_rows"][method] = {
            "rows": len(records),
            "expected_rows": len(tasks),
            "missing_rows": max(0, len(tasks) - len(records)),
            "runner_completed": sum(record.get("runner_status") == "completed" for record in records),
            "runner_error_count": sum(record.get("runner_status") != "completed" for record in records),
            "recoverable_success_count": sum(row.get("recovery_success_rate_item") is True for row in metric_rows),
            "recoverable_success_rate": round(sum(row.get("recovery_success_rate_item") is True for row in metric_rows) / len(tasks), 9) if tasks else None,
            "recovery_denominator": "all_expected_task_rows_including_runner_errors_and_missing_rows",
            "contract_satisfaction_rate_mean": mean("contract_satisfaction_rate"),
            "contract_satisfaction_rate_n": sum(row.get("contract_satisfaction_rate") is not None for row in metric_rows),
            "npsd_mean": mean("normalized_plan_stability_distance"),
            "npsd_n": sum(row.get("normalized_plan_stability_distance") is not None for row in metric_rows),
            "repair_latency_mean_s": mean("repair_decision_latency_seconds"),
            "repair_latency_n": sum(row.get("repair_decision_latency_seconds") is not None for row in metric_rows),
            "repair_solver_mean_s": mean("repair_solver_wallclock_seconds"),
            "repair_solver_n": sum(row.get("repair_solver_wallclock_seconds") is not None for row in metric_rows),
            "executed_suffix_path_mean_m": mean("executed_suffix_path_length_m"),
            "executed_suffix_path_n": sum(row.get("executed_suffix_path_length_m") is not None for row in metric_rows),
            "estimated_suffix_route_mean_m": mean("estimated_full_suffix_route_cost"),
            "estimated_suffix_route_n": sum(row.get("estimated_full_suffix_route_cost") is not None for row in metric_rows),
            "mission_completion_time_mean_s_success_only": mean("mission_completion_time_seconds"),
            "mission_completion_time_success_n": sum(row.get("mission_completion_time_seconds") is not None for row in metric_rows),
            "strict_contract_satisfied_count": sum(row.get("strict_contract_satisfied") is True for row in metric_rows),
        }

    (output_dir / "comparison_by_pair.jsonl").write_text(
        "".join(json.dumps(pair, ensure_ascii=False, separators=(",", ":")) + "\n" for pair in pairs),
        encoding="utf-8",
    )
    (output_dir / "pair_audit.json").write_text(json.dumps(summary["paired_state_audit"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Three-way suffix-grid comparison" if args.include_global else "# Simple local reassignment vs CDS-MR",
        "",
        f"Protocol: `{args.protocol_version}`; recoverable-failure task groups: **{len(tasks)}**.",
        "",
        "## Paired-state audit",
        "",
        f"- Complete pairs: {summary['paired_state_audit']['complete_pairs']}/{len(tasks)}.",
        f"- Identical failure-time state signatures: {summary['paired_state_audit']['same_failure_time_state_count']}/{len(tasks)}.",
        f"- Audit status: **{summary['paired_state_audit']['status']}**.",
        "",
        "## Descriptive results",
        "",
        "| Method | rows | recovery success | mean CSR | mean NPSD | mean repair latency (s) | successful completion-time n | mean completion time (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in args.methods:
        item = summary["method_rows"][method]
        lines.append(
            f"| {method} | {item['rows']} | {item['recoverable_success_count']}/{item['rows']} | "
            f"{item['contract_satisfaction_rate_mean']} | {item['npsd_mean']} | {item['repair_latency_mean_s']} | "
            f"{item['mission_completion_time_success_n']} | {item['mission_completion_time_mean_s_success_only']} |"
        )
    lines.extend([
        "",
        "Simple local reassignment is intentionally constraint-valid: it keeps the completed prefix, uses the same eligible UAV set and live bridge, preserves dependency order, and lets the bridge apply the same runtime checks. Its only replacement rule is a direct-scope greedy minimum route-cost choice with deterministic UAV-id tie-break; it does not use CDS-MR causal closure or lexicographic minimum-edit optimization.",
        "",
        "Interpretation must remain conditional on this fixed failure protocol. If the two arms are functionally equivalent on this candidate space, report that negative/equivalence result rather than claiming that CDS-MR is isolated by this experiment.",
    ])
    if args.include_global:
        lines.extend([
            "",
            "The global arm searches all contract-eligible assignments of unfinished decision-variable bundles and minimizes estimated full-suffix route cost without an edit penalty. Runtime deadlines and step budgets are checked by the same bridge for every arm.",
            "Actual suffix path is the summed motor-executed path of commanded repair UAV segments, not geometric chord distance or idle-UAV drift. Solver-only wall time and failure-to-first-repair-command latency are reported separately.",
        ])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--bridge-port-base", type=int, default=27500)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--include-global", action="store_true", help="run a third total-route reallocation arm")
    args = parser.parse_args()
    args.input = args.input.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.methods = METHODS + ((GLOBAL_METHOD,) if args.include_global else ())
    args.protocol_version = "suffix_grid_three_way_v1" if args.include_global else PROTOCOL_VERSION
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tasks = source_tasks(args.input)
    if args.max_tasks is not None:
        tasks = tasks[: args.max_tasks]
    manifest = {
        "protocol_version": args.protocol_version,
        "task_count": len(tasks),
        "task_ids_sha256": hashlib.sha256(json.dumps([str(task["task_id"]) for task in tasks], separators=(",", ":")).encode()).hexdigest(),
        "methods": list(args.methods),
        "source_input": str(args.input),
        "source_input_sha256": sha256_file(args.input),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "bridge_script_sha256": sha256_file(Path(__file__).with_name("gym_uav_bridge.py")),
        "global_solver_script_sha256": sha256_file(Path(__file__).with_name("global_suffix_reallocation.py")) if args.include_global else None,
        "cds_solver_script_sha256": sha256_file(Path(__file__).with_name("run_cds_mr_contract_online.py")),
        "metric_evaluator_script_sha256": sha256_file(Path(__file__).with_name("v6_metric_evaluator.py")),
        "adapter_script_sha256": sha256_file(Path(__file__).with_name("gym_task_adapter.py")),
        "auditor_script_sha256": sha256_file(Path(__file__).with_name("audit_suffix_grid_three_way.py")) if args.include_global else None,
        "python": sys.version,
        "platform": platform.platform(),
        "same_initial_protocol": "structured preferred-owner move_along_path with allow_partial_move=true",
        "comparison_question": "Does suffix-aware minimal repair improve on immediate local selection, and how does it compare with total-route reallocation?" if args.include_global else "Does CDS-MR add value beyond a constraint-valid direct-scope local reassignment rule?",
        "no_existing_results_overwritten": True,
    }
    manifest_path = args.output_dir / "run_manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous != manifest:
            raise RuntimeError("existing_run_manifest_mismatch_refuse_resume")
    else:
        existing_outputs = [args.output_dir / (method.replace("-", "_") + ".jsonl") for method in args.methods]
        existing_outputs.append(args.output_dir / "tasks_recoverable_failure_v1.jsonl")
        if any(path.exists() for path in existing_outputs):
            raise RuntimeError("unmanifested_result_rows_refuse_resume")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    task_copy = args.output_dir / "tasks_recoverable_failure_v1.jsonl"
    serialized_tasks = "".join(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n" for task in tasks)
    if task_copy.exists():
        if task_copy.read_text(encoding="utf-8") != serialized_tasks:
            raise RuntimeError("existing_task_copy_mismatch_refuse_resume")
    else:
        task_copy.write_text(serialized_tasks, encoding="utf-8")
    if not args.finalize_only:
        for method in args.methods:
            output_path = args.output_dir / (method.replace("-", "_") + ".jsonl")
            done = set()
            if output_path.exists():
                done = {str(json.loads(line).get("task_id")) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()}
            pending = [task for task in tasks if str(task.get("task_id")) not in done]
            with output_path.open("a", encoding="utf-8") as handle:
                for index, task in enumerate(pending, start=1):
                    record = run_one(task, method, args, args.output_dir / "logs" / method.replace("-", "_"))
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
                    print(json.dumps({"method": method, "progress": f"{index}/{len(pending)}", "task_id": task.get("task_id"), "status": record.get("runner_status"), "recovery": (record.get("standard_metrics") or {}).get("recovery_success_rate_item")}, ensure_ascii=False), flush=True)
    finalize(args.output_dir, tasks, args)
    print(json.dumps({"output_dir": str(args.output_dir), "task_count": len(tasks), "finalized": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
