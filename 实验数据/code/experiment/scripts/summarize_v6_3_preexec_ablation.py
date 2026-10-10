"""Prepare, run, and summarize the v6.4 shared-candidate-pool ablation.

The protocol is a preregistered 2x2 design:
Blind/SeSE selection x Global-Replan/CDS-MR repair.  A candidate pool is
generated once per task and seed and is then consumed unchanged by all four
arms.  This file intentionally keeps pool generation, execution, and summary
in one auditable entry point so a run can be resumed without new candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics
import time
from pathlib import Path
from typing import Any

import requests

from run_agent4drone_baseline import extract_tool_calls
from run_agent4drone_parallel import load_environment_file
from run_v6_3_preexec_ablation import (
    call_planner,
    execute,
    execution_error,
    fallback,
    get_agents,
    get_bundles,
    info,
    read_jsonl,
    signature,
    start_session,
    validate,
)
from run_cds_mr_contract_online import (
    _close_live_candidate,
    _compatible,
    _contract_intent,
    _decision_latency,
    _dynamic_slice,
    _execute_structured_repair,
    _ordered_bundles,
    _plan_distance,
    _route_cost,
    _topological_remaining_order,
)
from v6_metric_evaluator import common_metrics


METHODS = (
    "Blind+Global-Replan",
    "SeSE+Global-Replan",
    "Blind+CDS-MR",
    "SeSE+CDS-MR",
)
POOL_VERSION = "v6_4_shared_candidate_pool_v1"


def source_tasks(path: Path, limit: int | None) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    rows = [
        row
        for row in rows
        if str((row.get("metadata") or {}).get("condition_label"))
        == "recoverable_failure_uncertain"
    ]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise RuntimeError("no recoverable_failure_uncertain tasks found")
    return rows


def failure_anchor(task: dict[str, Any]) -> tuple[str | None, str | None]:
    policy = ((task.get("contract") or {}).get("failure_policy") or {})
    if not isinstance(policy, dict):
        return None, None
    bundle = policy.get("bundle_id")
    drone = policy.get("drone_id")
    return (str(bundle) if bundle else None, str(drone) if drone else None)


def force_failure_anchor(task: dict[str, Any], plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the fixed, preregistered failure trigger common to every arm."""

    bundle_id, drone_id = failure_anchor(task)
    if not bundle_id or not drone_id:
        return plan
    return [
        dict(item, assigned_drone_id=drone_id)
        if str(item.get("bundle_id")) == bundle_id
        else dict(item)
        for item in plan
    ]


def make_pool(task: dict[str, Any], args: argparse.Namespace, key: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    temperatures = (0.0, 0.55, 0.9)
    for index, temperature in enumerate(temperatures):
        error: str | None = None
        try:
            raw = call_planner(
                task,
                index,
                temperature,
                args.planner_endpoint,
                args.planner_model,
                key,
                args.planner_timeout,
            )
            plan = raw.get("plan")
        except Exception as exc:
            raw = {}
            plan = None
            error = repr(exc)
        if not isinstance(plan, list):
            plan = fallback(task, index, True)
        plan = force_failure_anchor(task, plan)
        checked = validate(task, plan, False)
        if not checked.get("valid"):
            plan = force_failure_anchor(task, fallback(task, index, True))
            checked = validate(task, plan, False)
        chosen_signature = signature(plan)
        if str(chosen_signature) in seen:
            for retry_index in range(index + 1, index + 12):
                alternative = force_failure_anchor(task, fallback(task, retry_index, True))
                alternative_checked = validate(task, alternative, False)
                alternative_signature = signature(alternative)
                if alternative_checked.get("valid") and str(alternative_signature) not in seen:
                    plan, checked, chosen_signature = alternative, alternative_checked, alternative_signature
                    error = error or "deterministic_diversity_fallback"
                    break
        seen.add(str(chosen_signature))
        candidates.append(
            {
                "candidate_index": index,
                "plan": plan,
                "validation": checked,
                "signature": str(chosen_signature),
                "planner_status": raw.get("planner_status", "fallback" if error else "completed"),
                "planner_error": error or raw.get("planner_error"),
                "planner_latency_seconds": raw.get("planner_latency_seconds"),
                "planner_token_cost": raw.get("planner_token_cost"),
                "temperature": temperature,
            }
        )
    pool_core = {
        "task_id": task.get("task_id"),
        "run_seed_index": info(task).get("run_seed_index"),
        "candidates": candidates,
    }
    pool_hash = hashlib.sha256(
        json.dumps(pool_core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "protocol_version": POOL_VERSION,
        "task_id": task.get("task_id"),
        "source_task_id": info(task).get("source_task_id"),
        "run_seed_index": info(task).get("run_seed_index"),
        "episode_seed": (task.get("metadata") or {}).get("episode_seed"),
        "candidate_count": len(candidates),
        "candidate_signature_count": len(seen),
        "candidate_pool_hash": pool_hash,
        "failure_anchor": {"bundle_id": failure_anchor(task)[0], "drone_id": failure_anchor(task)[1]},
        "generated_once_per_task_seed": True,
        "selector_excluded_inputs": ["failure_outcome", "simulator_metrics", "method_output"],
        "candidates": candidates,
    }


def generate_pools(args: argparse.Namespace) -> None:
    tasks = source_tasks(args.input, args.limit)
    env = load_environment_file(args.agent_env_file)
    key = env.get("LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not key:
        raise RuntimeError(f"LLM_API_KEY not found in {args.agent_env_file}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[str(row["task_id"])] = row
    with args.output.open("w", encoding="utf-8") as handle:
        for task in tasks:
            row = existing.get(str(task["task_id"])) or make_pool(task, args, key)
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {
        "protocol_version": POOL_VERSION,
        "tasks": len(tasks),
        "rows": len(existing) if existing else len(tasks),
        "candidate_count": 3,
        "candidate_pool_generated_once": True,
        "task_selection": "frozen input order; no result-based filtering",
        "output": str(args.output),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


def hash_index(task: dict[str, Any], count: int) -> int:
    key = f"{info(task).get('source_task_id')}::{info(task).get('run_seed_index')}::{task.get('task_id')}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16) % count


def select_candidate(task: dict[str, Any], pool: dict[str, Any], selector: str) -> dict[str, Any]:
    candidates = pool.get("candidates") or []
    if not candidates:
        raise RuntimeError("empty shared candidate pool")
    if selector == "Blind":
        index = hash_index(task, len(candidates))
        reason = "preregistered_sha256_index"
    else:
        best = max(
            candidates,
            key=lambda item: (
                float((item.get("validation") or {}).get("contract_score", 0.0)),
                -float((item.get("validation") or {}).get("route_cost", math.inf)),
                -int(item.get("candidate_index", 0)),
            ),
        )
        index = int(best.get("candidate_index", 0))
        reason = "independent_contract_checker_then_route_cost"
    selected = candidates[index]
    scores = [float((item.get("validation") or {}).get("contract_score", 0.0)) for item in candidates]
    selected_score = scores[index]
    return {
        "selector": selector,
        "selected_candidate_index": index,
        "selection_reason": reason,
        "candidate_count": len(candidates),
        "candidate_signature_count": pool.get("candidate_signature_count"),
        "candidate_pool_hash": pool.get("candidate_pool_hash"),
        "candidate_scores": scores,
        "candidate_route_costs": [float((item.get("validation") or {}).get("route_cost", math.inf)) for item in candidates],
        "baseline_contract_score": scores[0],
        "selected_contract_score": selected_score,
        "selection_gain": round(selected_score - scores[0], 6),
        "selection_regret": round(max(scores) - selected_score, 6),
        "gate_promotion": bool(selector == "SeSE" and index != 0),
        "excluded_from_selection": ["failure_outcome", "simulator_metrics", "method_output"],
        "selected_plan": selected.get("plan"),
    }


def global_remaining_plan(task: dict[str, Any], initial_metrics: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replan every unfinished bundle, without a causal slice or edit penalty."""

    bundles = _ordered_bundles(task)
    by_id = {str(bundle["bundle_id"]): bundle for bundle in bundles}
    progress = initial_metrics.get("completed_waypoints_by_bundle") or {}
    progress = progress if isinstance(progress, dict) else {}
    completed_ids = {
        bid for bid, bundle in by_id.items()
        if int(progress.get(bid, 0) or 0) >= len(bundle.get("waypoints", []) or [])
    }
    suffixes = {}
    for bundle in bundles:
        bid = str(bundle["bundle_id"])
        done = int(progress.get(bid, 0) or 0)
        points = bundle.get("waypoints", []) or []
        if done < len(points):
            suffixes[bid] = {
                "bundle_id": bid,
                "remaining_waypoints": points[done:],
                "current_assignment": str((initial_metrics.get("bundle_assignment") or {}).get(bid) or bundle.get("original_owner")),
            }
    if not suffixes:
        raise RuntimeError("global_replan_has_no_unfinished_bundle")
    mission = (task.get("contract") or {}).get("mission") or {}
    dependencies = mission.get("bundle_dependencies", {}) if isinstance(mission, dict) else {}
    order = _topological_remaining_order(bundles, set(suffixes), completed_ids, dependencies or {})
    agents = get_agents(task)
    unavailable = {str(item) for item in (initial_metrics.get("unavailable_drone_ids") or [])}
    positions = {}
    for item in initial_metrics.get("final_drones", []) or []:
        if isinstance(item, dict):
            aid = str(item.get("drone_id") or item.get("agent_id") or item.get("id") or "")
            point = item.get("position")
            if aid and isinstance(point, (list, tuple)) and len(point) == 3:
                positions[aid] = [float(value) for value in point]
    for aid, agent in agents.items():
        if aid not in positions and isinstance(agent.get("start"), (list, tuple)):
            positions[aid] = [float(value) for value in agent["start"]]
    options = {}
    for bid in order:
        bundle = by_id[bid]
        allowed = [str(item) for item in bundle.get("eligible_drone_ids", []) or agents.keys()]
        options[bid] = [
            aid for aid in allowed
            if aid in agents and aid not in unavailable and _compatible(agents[aid], bundle)
        ]
        if not options[bid]:
            raise RuntimeError(f"global_replan_no_compatible_uav:{bid}")
    best_key = None
    best_assignment = None
    best_cost = None
    for replacement in itertools.product(*(options[bid] for bid in order)):
        assignment = dict(zip(order, replacement))
        cursors = {aid: list(point) for aid, point in positions.items()}
        cost = 0.0
        feasible = True
        for bid in order:
            aid = assignment[bid]
            points = suffixes[bid]["remaining_waypoints"]
            if aid not in cursors or not points:
                feasible = False
                break
            cost += _route_cost(cursors[aid], points)
            cursors[aid] = [float(value) for value in points[-1]]
        if feasible:
            key = (round(cost, 12), tuple(assignment[bid] for bid in order))
            if best_key is None or key < best_key:
                best_key, best_assignment, best_cost = key, assignment, cost
    if best_assignment is None:
        raise RuntimeError("global_replan_no_feasible_assignment")
    plan = [
        {
            "sequence": index,
            "bundle_id": bid,
            "assigned_drone": best_assignment[bid],
            "original_assignment": suffixes[bid]["current_assignment"],
            "remaining_waypoints": suffixes[bid]["remaining_waypoints"],
            "dependencies": [str(item) for item in dependencies.get(bid, []) or []],
        }
        for index, bid in enumerate(order, start=1)
    ]
    audit = {
        "executor": "global_replan_all_unfinished_v1",
        "solver_status": "optimal_route_cost_over_all_unfinished_bundles",
        "solver_inputs": ["task_contract", "failure_time_state", "all_unfinished_bundles", "live_drone_positions"],
        "excluded_inputs": ["causal_slice", "minimum_edit_objective", "successful_reference_trace"],
        "unfinished_bundle_ids": order,
        "candidate_graph": options,
        "selected_assignment": best_assignment,
        "route_cost": round(float(best_cost), 6),
        "repair_plan": plan,
    }
    return plan, audit


def execute_global_replan(session: dict[str, Any], plan: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    commands = []
    status = "completed"
    for item in plan:
        command_started = time.perf_counter()
        try:
            response = requests.post(
                f"{session['bridge_url']}/drones/{item['assigned_drone']}/command/move_along_path",
                json={"waypoints": item["remaining_waypoints"], "allow_partial_move": True, "bundle_id": item["bundle_id"]},
                timeout=120,
            )
            response.raise_for_status()
            value = response.json()
        except Exception as exc:
            value = {"success": False, "error": repr(exc)}
        commands.append({
            "sequence": item["sequence"],
            "bundle_id": item["bundle_id"],
            "assigned_drone": item["assigned_drone"],
            "waypoints": item["remaining_waypoints"],
            "response": value,
            "seconds": round(time.perf_counter() - command_started, 6),
        })
        if not isinstance(value, dict) or not value.get("success"):
            status = "bridge_rejected_global_replan_command"
            break
    try:
        metrics = requests.get(f"{session['bridge_url']}/bridge/metrics", timeout=20).json()
    except Exception as exc:
        metrics = None
        status = "global_replan_metrics_error"
    return {
        "status": status,
        "api_success": bool(isinstance(metrics, dict) and metrics.get("task_success")),
        "token_usage": None,
        "tool_calls": [],
        "structured_commands": commands,
        "gym_metrics": metrics,
        "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
        "execution_mode": "same_live_bridge_global_replan",
    }


def execute_direct_initial(session: dict[str, Any], task: dict[str, Any], plan: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute the selected plan through the common bridge command contract.

    The old pilot delegated this phase to a free-form Agent4Drone command
    parser.  That parser can turn a multi-waypoint bundle into one waypoint
    command, creating an artificial sequence failure before the registered
    injector fires.  All four ablation arms therefore use this identical
    structured execution path; Agent4Drone remains started by the platform
    session but is not allowed to change the selected plan.
    """

    started = time.perf_counter()
    by_id = {str(bundle["bundle_id"]): bundle for bundle in get_bundles(task)}
    commands = []
    status = "completed"
    ordered = sorted(plan, key=lambda item: (int(item.get("order", 0)), str(item.get("bundle_id"))))
    for item in ordered:
        bid = str(item.get("bundle_id"))
        bundle = by_id.get(bid)
        if bundle is None:
            status = "selected_plan_unknown_bundle"
            break
        command_started = time.perf_counter()
        try:
            response = requests.post(
                f"{session['bridge_url']}/drones/{item['assigned_drone_id']}/command/move_along_path",
                json={
                    "waypoints": bundle.get("waypoints", []),
                    "allow_partial_move": True,
                    "bundle_id": bid,
                },
                timeout=120,
            )
            response.raise_for_status()
            value = response.json()
        except Exception as exc:
            value = {"success": False, "error": repr(exc)}
        commands.append({
            "sequence": item.get("order"),
            "bundle_id": bid,
            "assigned_drone": item.get("assigned_drone_id"),
            "waypoints": bundle.get("waypoints", []),
            "response": value,
            "seconds": round(time.perf_counter() - command_started, 6),
        })
        if not isinstance(value, dict) or not value.get("success"):
            status = "bridge_rejected_initial_command"
            break
    try:
        metrics = requests.get(f"{session['bridge_url']}/bridge/metrics", timeout=20).json()
    except Exception:
        metrics = None
        status = "initial_metrics_error"
    return {
        "status": status,
        "api_success": bool(isinstance(metrics, dict) and metrics.get("task_success")),
        "token_usage": None,
        "tool_calls": [],
        "structured_commands": commands,
        "gym_metrics": metrics,
        "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
        "execution_mode": "common_bridge_structured_initial_v1",
    }


def run_one(task: dict[str, Any], pool: dict[str, Any], method: str, args: argparse.Namespace, env: dict[str, str], slot: int) -> dict[str, Any]:
    selector, repair_mode = method.split("+", 1)
    selection = select_candidate(task, pool, selector)
    candidate = (pool.get("candidates") or [])[int(selection["selected_candidate_index"])]
    run_args = argparse.Namespace(**vars(args))
    run_args.bridge_port_base = args.bridge_port_base + slot * 2
    run_args.agent_port_base = args.agent_port_base + slot * 2
    run_args.log_dir = args.log_dir / method.replace("+", "_").replace("/", "_")
    run_args.log_dir.mkdir(parents=True, exist_ok=True)
    session = None
    started = time.perf_counter()
    try:
        session = start_session(task, run_args, env)
        try:
            initial = execute_direct_initial(session, task, candidate.get("plan") or [])
        except Exception as exc:
            initial = execution_error(session, exc)
        initial_metrics = initial.get("gym_metrics") if isinstance(initial.get("gym_metrics"), dict) else {}
        dynamic = _dynamic_slice(task, initial)
        repair = {
            "triggered": False,
            "repair_success": False,
            "intent_preserved": False,
            "same_live_session": False,
            "executor": None,
        }
        final = initial
        if dynamic is not None and repair_mode in {"Global-Replan", "CDS-MR"}:
            repair["triggered"] = True
            try:
                repair["repair_window"] = requests.post(f"{session['bridge_url']}/bridge/repair-window", timeout=10).json()
            except Exception as exc:
                repair["repair_window"] = {"enabled": False, "error": repr(exc)}
            initial_history = initial_metrics.get("command_history", [])
            initial_count = len(initial_history) if isinstance(initial_history, list) else 0
            if repair_mode == "CDS-MR":
                repair_record = _execute_structured_repair(session, task, initial, dynamic, len(pool.get("candidates") or []))
                repair["executor"] = "structured_cds_mr_v1"
            else:
                global_plan, audit = global_remaining_plan(task, initial_metrics)
                repair_record = execute_global_replan(session, global_plan)
                repair_record["global_replan_executor"] = audit
                repair["executor"] = "global_replan_all_unfinished_v1"
            final = repair_record
            final_metrics = final.get("gym_metrics") if isinstance(final.get("gym_metrics"), dict) else {}
            history = final_metrics.get("command_history", [])
            repair_history = history[initial_count:] if isinstance(history, list) else []
            repair_intent = _contract_intent(task, final.get("tool_calls", []), final_metrics)
            repair["intent"] = repair_intent
            repair["intent_preserved"] = bool(repair_intent.get("preserved"))
            repair["repair_success"] = bool(final_metrics.get("task_success")) and repair["intent_preserved"]
            repair["same_live_session"] = True
            repair["execution_record"] = repair_record
            repair["plan_distance"] = _plan_distance(dynamic, repair_history)
            repair["repair_decision_latency_seconds"] = _decision_latency(initial_metrics, final_metrics)
            repair["timing"] = final.get("timing")
        final_metrics = final.get("gym_metrics") if isinstance(final.get("gym_metrics"), dict) else {}
        distance = repair.get("plan_distance") if isinstance(repair.get("plan_distance"), dict) else {}
        standard = common_metrics(
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
        return {
            "protocol_version": POOL_VERSION,
            "method": method,
            "selector": selector,
            "repair_method": repair_mode,
            "method_definition": method,
            "task_id": task.get("task_id"),
            **info(task),
            "candidate_pool_hash": pool.get("candidate_pool_hash"),
            "candidate_pool_signature_count": pool.get("candidate_signature_count"),
            "candidate_count": pool.get("candidate_count"),
            "selected_candidate_index": selection["selected_candidate_index"],
            "selected_plan": candidate.get("plan"),
            "selection": selection,
            "initial_plan_contract_validity": candidate.get("validation"),
            "initial_planning_latency_seconds": round(sum(float(item.get("planner_latency_seconds") or 0.0) for item in pool.get("candidates", [])), 6),
            "initial_planning_token_cost": sum(int(item.get("planner_token_cost") or 0) for item in pool.get("candidates", [])),
            "initial_execution_record": initial,
            "repair": repair,
            "standard_metrics": standard,
            "runner_status": "completed",
        }
    except Exception as exc:
        return {
            "protocol_version": POOL_VERSION,
            "method": method,
            "selector": selector,
            "repair_method": repair_mode,
            "task_id": task.get("task_id"),
            **info(task),
            "candidate_pool_hash": pool.get("candidate_pool_hash"),
            "runner_status": "runner_error",
            "runner_error": repr(exc),
        }
    finally:
        if session is not None:
            _close_live_candidate(session)


def run_ablation(args: argparse.Namespace) -> None:
    tasks = source_tasks(args.input, args.limit)
    pools = {}
    for line in args.pool.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            pools[str(row["task_id"])] = row
    missing = [str(task["task_id"]) for task in tasks if str(task["task_id"]) not in pools]
    if missing:
        raise RuntimeError(f"missing shared pools for {len(missing)} tasks")
    env = load_environment_file(args.agent_env_file)
    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    unknown = [item for item in methods if item not in METHODS]
    if unknown:
        raise RuntimeError(f"unknown methods: {unknown}")
    done = set()
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((str(row.get("task_id")), str(row.get("method"))))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    slot = 0
    with args.output.open("a", encoding="utf-8") as handle:
        for task in tasks:
            for method in methods:
                key = (str(task["task_id"]), method)
                if key in done:
                    continue
                row = run_one(task, pools[key[0]], method, args, env, slot)
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                slot += 1
                print(json.dumps({
                    "completed": slot,
                    "total": len(tasks) * len(methods),
                    "task_id": row.get("task_id"),
                    "method": method,
                    "status": row.get("runner_status"),
                    "repair_triggered": (row.get("repair") or {}).get("triggered"),
                    "repair_success": (row.get("repair") or {}).get("repair_success"),
                }, ensure_ascii=False), flush=True)


METRICS = (
    "recovery_success_rate_item",
    "contract_satisfaction_rate",
    "normalized_plan_stability_distance",
    "repair_decision_latency_seconds",
    "mission_completion_time_seconds",
)


def stat(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6) if values else None,
        "std": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0 if values else None,
    }


def paired(rows: list[dict[str, Any]], left: str, right: str, field: str) -> dict[str, Any]:
    by_key = {}
    for row in rows:
        key = (str(row.get("source_task_id")), str(row.get("run_seed_index")))
        by_key.setdefault(key, {})[str(row.get("method"))] = row
    deltas = []
    for values in by_key.values():
        if left not in values or right not in values:
            continue
        lv = (values[left].get("standard_metrics") or {}).get(field)
        rv = (values[right].get("standard_metrics") or {}).get(field)
        if lv is not None and rv is not None:
            deltas.append(float(lv) - float(rv))
    return {"n": len(deltas), "mean_left_minus_right": round(statistics.fmean(deltas), 6) if deltas else None, "std": round(statistics.stdev(deltas), 6) if len(deltas) > 1 else 0.0 if deltas else None}


def summarize(args: argparse.Namespace) -> None:
    input_paths = [args.input] if args.input is not None else sorted(args.input_dir.rglob("results.jsonl"))
    if not input_paths:
        raise RuntimeError("no result files found for summary")
    rows = []
    for input_path in input_paths:
        rows.extend(
            json.loads(line)
            for line in input_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    summary = {method: {} for method in METHODS}
    for method in METHODS:
        selected = [row for row in rows if row.get("method") == method and row.get("runner_status") == "completed"]
        for field in METRICS:
            summary[method][field] = stat([
                float((row.get("standard_metrics") or {})[field])
                for row in selected
                if (row.get("standard_metrics") or {}).get(field) is not None
            ])
        summary[method]["rows"] = len(selected)
    paired_results = {}
    for field in METRICS:
        paired_results[field] = {
            "SeSE_minus_Blind_global": paired(rows, "SeSE+Global-Replan", "Blind+Global-Replan", field),
            "SeSE_minus_Blind_cds": paired(rows, "SeSE+CDS-MR", "Blind+CDS-MR", field),
            "CDS_minus_Global_blind": paired(rows, "Blind+CDS-MR", "Blind+Global-Replan", field),
            "CDS_minus_Global_sese": paired(rows, "SeSE+CDS-MR", "SeSE+Global-Replan", field),
        }
    pool_hashes = {}
    for row in rows:
        pool_hashes.setdefault(str(row.get("task_id")), set()).add(str(row.get("candidate_pool_hash")))
    protocol_checks = {
        "rows": len(rows),
        "completed_rows": sum(row.get("runner_status") == "completed" for row in rows),
        "expected_methods": list(METHODS),
        "shared_pool_hash_consistent": all(len(values) == 1 for values in pool_hashes.values()),
        "task_pool_count": len(pool_hashes),
        "missing_method_pairs": [
            {"task_id": task_id, "method": method}
            for task_id, hashes in pool_hashes.items()
            for method in METHODS
            if not any(row.get("task_id") == task_id and row.get("method") == method for row in rows)
        ],
    }
    result = {
        "protocol_version": POOL_VERSION,
        "input": [str(path) for path in input_paths],
        "methods": list(METHODS),
        "metric_fields": list(METRICS),
        "paired_key_definition": "source_task_id + run_seed_index",
        "protocol_checks": protocol_checks,
        "summary": summary,
        "paired_deltas_left_minus_right": paired_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    labels = {
        "recovery_success_rate_item": "RSR ↑",
        "contract_satisfaction_rate": "CSR ↑",
        "normalized_plan_stability_distance": "NPSD ↓",
        "repair_decision_latency_seconds": "Repair latency ↓",
        "mission_completion_time_seconds": "Mission time ↓",
    }
    def fmt(method: str, field: str) -> str:
        item = summary[method][field]
        if item["mean"] is None:
            return "—"
        return f"{item['mean']:.3f} ± {item['std']:.3f} (n={item['n']})"
    lines = [
        "# v6.4 shared-candidate-pool ablation",
        "",
        "Failure stratum: recoverable_failure_uncertain; four arms share one candidate pool per task and seed.",
        "",
        "| Method | " + " | ".join(labels.values()) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for method in METHODS:
        lines.append("| " + method + " | " + " | ".join(fmt(method, field) for field in labels) + " |")
    lines.extend(["", "## Protocol checks", "", f"- Rows: {protocol_checks['rows']}; completed: {protocol_checks['completed_rows']}", f"- Shared candidate-pool hashes consistent: {protocol_checks['shared_pool_hash_consistent']}", f"- Missing method pairs: {len(protocol_checks['missing_method_pairs'])}"])
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["pool", "run", "summary"], required=True)
    p.add_argument("--input", type=Path)
    p.add_argument("--input-dir", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--pool", type=Path)
    p.add_argument("--manifest", type=Path, default=Path("pool_manifest.json"))
    p.add_argument("--markdown", type=Path, default=Path("summary.md"))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--methods", default=",".join(METHODS))
    p.add_argument("--planner-endpoint", default="https://api.deepseek.com/chat/completions")
    p.add_argument("--planner-model", default="deepseek-v4-flash")
    p.add_argument("--planner-timeout", type=float, default=120)
    p.add_argument("--agent-env-file", type=Path, default=Path("~/.config/agent4drone/agent4drone.env").expanduser())
    p.add_argument("--agent-dir", type=Path, default=Path("~/uav_exp/MultiUAV-Plat/agent4drone").expanduser())
    p.add_argument("--log-dir", type=Path, default=Path("~/uav_exp/data/logs/v6_4_shared_pool").expanduser())
    p.add_argument("--bridge-port-base", type=int, default=9600)
    p.add_argument("--agent-port-base", type=int, default=19600)
    p.add_argument("--startup-timeout", type=float, default=60)
    p.add_argument("--command-timeout", type=float, default=420)
    p.add_argument("--poll-interval", type=float, default=0.5)
    p.add_argument("--uvicorn-log-level", default="warning")
    p.add_argument("--bridge-max-steps", type=int, default=None)
    return p


def main() -> None:
    args = parser().parse_args()
    if args.input is not None:
        args.input = args.input.expanduser().resolve()
    if args.input_dir is not None:
        args.input_dir = args.input_dir.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.agent_env_file = args.agent_env_file.expanduser().resolve()
    args.agent_dir = args.agent_dir.expanduser().resolve()
    args.log_dir = args.log_dir.expanduser().resolve()
    args.manifest = args.manifest.expanduser().resolve()
    args.markdown = args.markdown.expanduser().resolve()
    if args.mode == "pool":
        generate_pools(args)
    elif args.mode == "run":
        if args.pool is None:
            raise SystemExit("--pool is required for --mode run")
        args.pool = args.pool.expanduser().resolve()
        run_ablation(args)
    else:
        if args.input is None and args.input_dir is None:
            raise SystemExit("--input or --input-dir is required for --mode summary")
        summarize(args)


if __name__ == "__main__":
    main()
