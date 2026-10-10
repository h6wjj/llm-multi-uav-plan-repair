"""Run B0+SeSE+CDS-MR on contract-enriched UAVBench scenarios.

SeSE selects a candidate without reading simulator outcomes.  CDS-MR is
triggered only when the selected candidate's bridge metrics contain a real
``first_failure`` event.  The dynamic slice is built from that event and the
failure-time state; no successful reference trace is supplied to the repair
prompt.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from v6_metric_evaluator import common_metrics

from run_agent4drone_baseline import extract_tool_calls, read_tasks
from run_agent4drone_parallel import (
    load_environment_file,
    run_agent_command,
    start_agent,
    start_bridge,
    terminate_process,
    wait_health,
)
from run_agent4drone_sese_online import (
    _candidate_command,
    _prepare_agent_dirs,
    _token_count,
)
from sese_gate import _coverage, movement_signatures, multi_candidate_gate


METHOD_DEFINITION = "B0+SeSE+CDS-MR"
REPAIR_MODULE = "CDS-MR"


def _method_definition(args: argparse.Namespace) -> str:
    if getattr(args, "ablation_variant", "full") == "b0_cds_mr":
        return "B0+CDS-MR"
    return METHOD_DEFINITION


def _dynamic_slice(task: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any] | None:
    metrics = selected.get("gym_metrics") if isinstance(selected.get("gym_metrics"), dict) else {}
    failure = metrics.get("first_failure") if isinstance(metrics, dict) else None
    if not isinstance(failure, dict):
        return None
    contract = task.get("contract", {}) if isinstance(task.get("contract"), dict) else {}
    expected = contract.get("mission", {}).get("required_waypoint_sequence", [])
    try:
        completed = max(0, int(metrics.get("completed_waypoints", 0)))
    except (TypeError, ValueError):
        completed = 0
    completed_by_bundle = metrics.get("completed_waypoints_by_bundle", {})
    if not isinstance(completed_by_bundle, dict):
        completed_by_bundle = {}
    remaining_bundle_suffixes: list[dict[str, Any]] = []
    mission = contract.get("mission", {}) if isinstance(contract, dict) else {}
    task_bundles = mission.get("task_bundles", {}) if isinstance(mission, dict) else {}
    if isinstance(task_bundles, dict):
        for owner_id, bundle in task_bundles.items():
            if not isinstance(bundle, dict):
                continue
            bundle_id = str(bundle.get("bundle_id") or owner_id)
            points = bundle.get("waypoints", [])
            if not isinstance(points, list):
                continue
            try:
                done = max(0, int(completed_by_bundle.get(bundle_id, 0)))
            except (TypeError, ValueError):
                done = 0
            suffix = points[min(done, len(points)):]
            if suffix:
                remaining_bundle_suffixes.append(
                    {
                        "bundle_id": bundle_id,
                        "original_owner": str(owner_id),
                        "assigned_drone": str(metrics.get("bundle_assignment", {}).get(bundle_id, owner_id)),
                        "completed_waypoints": done,
                        "remaining_waypoints": suffix,
                    }
                )
    remaining = expected[completed:] if isinstance(expected, list) else []
    return {
        "source": "bridge_failure_trace",
        "first_failure": failure,
        "failed_tool_command": failure.get("command"),
        "failed_drone_id": failure.get("drone_id"),
        "failure_step": failure.get("step"),
        "failure_command_index": failure.get("command_index"),
        "failure_time_state": failure.get("state"),
        "completed_waypoints_before_repair": completed,
        "remaining_waypoints": remaining,
        "completed_waypoints_by_bundle": completed_by_bundle,
        "remaining_bundle_suffixes": remaining_bundle_suffixes,
        "editable_scope": {
            "contract_sections": [
                "mission.required_waypoint_sequence",
                "agents.allowed_drone_ids",
                "resources.max_total_steps",
            ],
            "failed_command": failure.get("command"),
            "failed_drone_id": failure.get("drone_id"),
        },
        "contract_version": contract.get("contract_version"),
    }


def _is_v6_comparison(task: dict[str, Any]) -> bool:
    scenario = task.get("scenario") if isinstance(task.get("scenario"), dict) else {}
    return str(scenario.get("comparison_protocol") or "") == "v6_no_minimal_repair_rule_leakage"


def _ordered_bundles(task: dict[str, Any]) -> list[dict[str, Any]]:
    contract = task.get("contract", {}) if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission", {}) if isinstance(contract.get("mission"), dict) else {}
    values = mission.get("task_bundles", {}) if isinstance(mission, dict) else {}
    bundles: list[dict[str, Any]] = []
    if isinstance(values, dict):
        for owner_id, value in values.items():
            if not isinstance(value, dict):
                continue
            bundle = dict(value)
            bundle["bundle_id"] = str(bundle.get("bundle_id") or owner_id)
            bundle["original_owner"] = str(bundle.get("drone_id") or owner_id)
            bundles.append(bundle)
    return sorted(bundles, key=lambda item: (int(item.get("order", 0)), item["bundle_id"]))


def _xyz(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z", value.get("altitude"))]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _route_cost(start: list[float], waypoints: list[Any]) -> float:
    normalized = [point for point in (_xyz(value) for value in waypoints) if point is not None]
    if not normalized:
        return 0.0
    return math.dist(start, normalized[0]) + sum(
        math.dist(left, right) for left, right in zip(normalized, normalized[1:])
    )


def _live_agent_view(
    task: dict[str, Any],
    metrics: dict[str, Any],
    dynamic_slice: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Merge contract identities with the failure-time/live bridge state."""

    agents: dict[str, dict[str, Any]] = {}
    for value in task.get("agents", []):
        if not isinstance(value, dict):
            continue
        agent_id = str(value.get("agent_id") or value.get("id") or value.get("name") or "")
        if agent_id:
            agents[agent_id] = dict(value)

    live_drones = metrics.get("final_drones", []) if isinstance(metrics, dict) else []
    if not isinstance(live_drones, list) or not live_drones:
        state = dynamic_slice.get("failure_time_state", {})
        live_drones = state.get("drones", []) if isinstance(state, dict) else []
    for value in live_drones:
        if not isinstance(value, dict):
            continue
        agent_id = str(value.get("id") or value.get("name") or value.get("agent_id") or "")
        if agent_id:
            agents.setdefault(agent_id, {}).update(value)

    unavailable = {
        str(item)
        for item in (metrics.get("unavailable_drone_ids", []) if isinstance(metrics, dict) else [])
    }
    failure = dynamic_slice.get("first_failure", {})
    details = failure.get("details", {}) if isinstance(failure, dict) else {}
    if isinstance(details, dict):
        unavailable.update(str(item) for item in details.get("unavailable_drone_ids", []) or [])
    for agent_id, value in agents.items():
        if str(value.get("status") or "").lower() in {"unavailable", "failed", "lost", "offline"}:
            unavailable.add(agent_id)
    return agents, unavailable


def _compatible(agent: dict[str, Any], bundle: dict[str, Any]) -> bool:
    required = {str(item) for item in bundle.get("required_capabilities", []) or []}
    capabilities = {str(item) for item in agent.get("capabilities", []) or []}
    eligible_roles = {str(item) for item in bundle.get("eligible_roles", []) or []}
    role = str(agent.get("role") or "unspecified")
    return required.issubset(capabilities) and (not eligible_roles or role in eligible_roles)


def _topological_remaining_order(
    bundles: list[dict[str, Any]],
    remaining_ids: set[str],
    completed_ids: set[str],
    dependencies: dict[str, Any],
) -> list[str]:
    by_id = {str(bundle["bundle_id"]): bundle for bundle in bundles}
    pending = set(remaining_ids)
    ordered: list[str] = []
    while pending:
        ready = [
            bundle_id
            for bundle_id in pending
            if all(
                str(dependency) in completed_ids or str(dependency) in ordered
                for dependency in (dependencies.get(bundle_id, []) or [])
            )
        ]
        if not ready:
            raise RuntimeError(f"cyclic_or_unsatisfied_bundle_dependencies:{sorted(pending)}")
        ready.sort(key=lambda bundle_id: (int(by_id[bundle_id].get("order", 0)), bundle_id))
        selected = ready[0]
        ordered.append(selected)
        pending.remove(selected)
    return ordered


def _structured_repair_plan(
    task: dict[str, Any],
    initial_metrics: dict[str, Any],
    dynamic_slice: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Solve CDS-MR as a deterministic lexicographic small-fleet CSP.

    Only contract data, the selected candidate's failure trace, and live bridge
    state are inputs.  Scenario-generation witnesses and method outputs are not
    consumed by this solver.
    """

    bundles = _ordered_bundles(task)
    by_id = {str(bundle["bundle_id"]): bundle for bundle in bundles}
    suffixes = {
        str(item.get("bundle_id")): dict(item)
        for item in dynamic_slice.get("remaining_bundle_suffixes", [])
        if isinstance(item, dict) and item.get("bundle_id")
    }
    if not suffixes:
        raise RuntimeError("no_remaining_bundle_suffixes")

    progress = initial_metrics.get("completed_waypoints_by_bundle", {})
    progress = progress if isinstance(progress, dict) else {}
    completed_ids = {
        bundle_id
        for bundle_id, bundle in by_id.items()
        if int(progress.get(bundle_id, 0) or 0) >= len(bundle.get("waypoints", []) or [])
    }
    contract = task.get("contract", {}) if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission", {}) if isinstance(contract.get("mission"), dict) else {}
    dependencies = mission.get("bundle_dependencies", {}) if isinstance(mission, dict) else {}
    dependencies = dependencies if isinstance(dependencies, dict) else {}
    execution_order = _topological_remaining_order(
        bundles,
        set(suffixes),
        completed_ids,
        dependencies,
    )

    agents, unavailable = _live_agent_view(task, initial_metrics, dynamic_slice)
    initial_assignment = initial_metrics.get("bundle_assignment", {})
    initial_assignment = initial_assignment if isinstance(initial_assignment, dict) else {}
    failure = dynamic_slice.get("first_failure", {})
    details = failure.get("details", {}) if isinstance(failure, dict) else {}
    policy = contract.get("failure_policy", {}) if isinstance(contract, dict) else {}
    failed_bundle_id = str(
        (details.get("bundle_id") if isinstance(details, dict) else None)
        or (policy.get("bundle_id") if isinstance(policy, dict) else None)
        or ""
    )

    descendants = {failed_bundle_id} if failed_bundle_id else set()
    changed = True
    while changed:
        changed = False
        for child, parents in dependencies.items():
            if str(child) in suffixes and any(str(parent) in descendants for parent in parents or []):
                if str(child) not in descendants:
                    descendants.add(str(child))
                    changed = True
    check_scope = {
        bundle_id for bundle_id in execution_order if bundle_id in descendants
    }

    candidate_map: dict[str, list[str]] = {}
    affected: list[str] = []
    invalid_assignment_reasons: dict[str, list[str]] = {}
    current_assignment: dict[str, str] = {}
    for bundle_id in execution_order:
        bundle = by_id[bundle_id]
        owner = str(
            initial_assignment.get(bundle_id)
            or suffixes[bundle_id].get("assigned_drone")
            or bundle.get("original_owner")
        )
        current_assignment[bundle_id] = owner
        if bundle_id not in check_scope:
            continue
        reasons: list[str] = []
        if bundle_id == failed_bundle_id:
            reasons.append("failure_causal_source")
        if owner in unavailable:
            reasons.append("assigned_uav_unavailable")
        if owner not in agents:
            reasons.append("assigned_uav_unknown")
        elif not _compatible(agents[owner], bundle):
            reasons.append("assigned_uav_role_or_capability_incompatible")
        if reasons:
            affected.append(bundle_id)
            invalid_assignment_reasons[bundle_id] = reasons
            candidate_map[bundle_id] = [
                agent_id
                for agent_id in sorted(agents)
                if agent_id not in unavailable and _compatible(agents[agent_id], bundle)
            ]
            if not candidate_map[bundle_id]:
                raise RuntimeError(f"no_compatible_available_uav:{bundle_id}")

    if not affected:
        raise RuntimeError("failure_trace_did_not_identify_an_affected_bundle")

    # Preserve downstream continuity only as a soft preference.  It must not
    # remove a capable live UAV from the candidate set before feasibility and
    # live-state route cost have been evaluated.
    downstream_owners = {
        current_assignment[bundle_id]
        for bundle_id in descendants
        if bundle_id != failed_bundle_id and bundle_id in current_assignment
    }

    positions: dict[str, list[float]] = {}
    for agent_id, value in agents.items():
        point = _xyz(value.get("position")) or _xyz(value.get("start"))
        if point is not None:
            positions[agent_id] = point

    best_key: tuple[Any, ...] | None = None
    best_assignment: dict[str, str] | None = None
    best_route_cost = 0.0
    affected_in_order = [bundle_id for bundle_id in execution_order if bundle_id in affected]
    for replacements in itertools.product(*(candidate_map[bundle_id] for bundle_id in affected_in_order)):
        assignment = dict(current_assignment)
        assignment.update(dict(zip(affected_in_order, replacements)))
        reassigned = sum(
            assignment[bundle_id] != current_assignment[bundle_id]
            for bundle_id in execution_order
        )
        changed_actions = sum(
            len(suffixes[bundle_id].get("remaining_waypoints", []) or [])
            for bundle_id in execution_order
            if assignment[bundle_id] != current_assignment[bundle_id]
        )
        cursors = {agent_id: list(point) for agent_id, point in positions.items()}
        total_cost = 0.0
        feasible = True
        for bundle_id in execution_order:
            agent_id = assignment[bundle_id]
            waypoints = suffixes[bundle_id].get("remaining_waypoints", []) or []
            if agent_id not in cursors or not waypoints:
                feasible = False
                break
            total_cost += _route_cost(cursors[agent_id], waypoints)
            final_point = _xyz(waypoints[-1])
            if final_point is None:
                feasible = False
                break
            cursors[agent_id] = final_point
        if not feasible:
            continue
        downstream_owner_overlap = int(
            bool(
                failed_bundle_id
                and assignment.get(failed_bundle_id) in downstream_owners
            )
        )
        key = (
            reassigned,
            changed_actions,
            round(total_cost, 12),
            downstream_owner_overlap,
            tuple(assignment[bundle_id] for bundle_id in execution_order),
        )
        if best_key is None or key < best_key:
            best_key = key
            best_assignment = assignment
            best_route_cost = total_cost
    if best_assignment is None or best_key is None:
        raise RuntimeError("no_feasible_structured_repair_assignment")

    repair_plan = [
        {
            "sequence": index,
            "bundle_id": bundle_id,
            "assigned_drone": best_assignment[bundle_id],
            "original_assignment": current_assignment[bundle_id],
            "causally_affected": bundle_id in affected,
            "remaining_waypoints": suffixes[bundle_id].get("remaining_waypoints", []),
            "dependencies": [str(item) for item in dependencies.get(bundle_id, []) or []],
        }
        for index, bundle_id in enumerate(execution_order, start=1)
    ]
    unaffected = [bundle_id for bundle_id in execution_order if bundle_id not in affected]
    audit = {
        "executor": "structured_cds_mr_v2_feasibility_first",
        "solver_status": "optimal_exhaustive_lexicographic_small_fleet",
        "solver_inputs": ["task_contract", "selected_failure_trace", "live_bridge_state"],
        "excluded_inputs": ["scenario_feasibility_witnesses", "baseline_outputs", "successful_reference_trace"],
        "causal_slice": {
            "failed_bundle_id": failed_bundle_id,
            "affected_bundle_ids": affected,
            "dependency_execution_closure": [
                bundle_id for bundle_id in execution_order if bundle_id in descendants
            ],
            "unaffected_bundle_ids": unaffected,
            "invalid_assignment_reasons": invalid_assignment_reasons,
        },
        "contract_slice": {
            "bundle_fields": [
                "required_capabilities",
                "eligible_roles",
                "deadline_s",
                "remaining_waypoints",
            ],
            "mission_fields": ["bundle_dependencies", "repair_must_preserve_completed_prefix"],
            "resource_fields": ["max_total_steps", "repair_step_budget", "time_budget_s"],
        },
        "compatibility_graph": candidate_map,
        "unavailable_drone_ids": sorted(unavailable),
        "selected_assignment": best_assignment,
        "objective": {
            "priority": [
                "minimum_reassigned_bundle_count",
                "minimum_changed_action_count",
                "minimum_live_state_route_cost",
                "minimum_downstream_owner_overlap_after_route_cost",
                "deterministic_assignment_tiebreak",
            ],
            "reassigned_bundle_count": int(best_key[0]),
            "changed_action_count": int(best_key[1]),
            "route_cost": round(best_route_cost, 6),
            "downstream_owner_overlap": int(best_key[3]),
        },
        "hard_constraint_enforcement": {
            "solver_prechecked": [
                "immutable_completed_prefix_by_suffix_construction",
                "role_and_capability_eligibility",
                "bundle_dependency_topological_order",
                "live_uav_availability",
            ],
            "runtime_bridge_checked": [
                "remaining_waypoint_sequence",
                "role_and_capability_eligibility",
                "bundle_dependencies",
                "shared_execution_budget",
                "deadline",
                "live_uav_availability",
            ],
        },
        "repair_plan": repair_plan,
    }
    return repair_plan, audit


def _execute_structured_repair(
    session: dict[str, Any],
    task: dict[str, Any],
    selected: dict[str, Any],
    dynamic_slice: dict[str, Any],
    candidate_index: int,
) -> dict[str, Any]:
    """Execute a solved repair directly against the selected live bridge."""

    started = time.perf_counter()
    initial_metrics = selected.get("gym_metrics") if isinstance(selected.get("gym_metrics"), dict) else {}
    structured_commands: list[dict[str, Any]] = []
    try:
        repair_plan, solver_audit = _structured_repair_plan(task, initial_metrics, dynamic_slice)
        status = "completed"
        for item in repair_plan:
            command_started = time.perf_counter()
            response = requests.post(
                f"{session['bridge_url']}/drones/{item['assigned_drone']}/command/move_along_path",
                json={
                    "waypoints": item["remaining_waypoints"],
                    "allow_partial_move": True,
                    "bundle_id": item["bundle_id"],
                },
                timeout=120,
            )
            response.raise_for_status()
            value = response.json()
            command_record = {
                "sequence": item["sequence"],
                "bundle_id": item["bundle_id"],
                "assigned_drone": item["assigned_drone"],
                "waypoints": item["remaining_waypoints"],
                "response": value,
                "seconds": round(time.perf_counter() - command_started, 6),
            }
            structured_commands.append(command_record)
            if not isinstance(value, dict) or not value.get("success"):
                status = "bridge_rejected_repair_command"
                break
    except Exception as exc:
        repair_plan = []
        solver_audit = {
            "executor": "structured_cds_mr_v2_feasibility_first",
            "solver_status": "error",
            "error": repr(exc),
        }
        status = "structured_repair_error"

    try:
        metrics_response = requests.get(f"{session['bridge_url']}/bridge/metrics", timeout=20)
        metrics_response.raise_for_status()
        metrics = metrics_response.json()
    except requests.RequestException as exc:
        metrics = None
        status = "structured_repair_metrics_error"
        solver_audit["metrics_error"] = repr(exc)
    return {
        "candidate_index": candidate_index,
        "status": status,
        "api_success": bool(isinstance(metrics, dict) and metrics.get("task_success")),
        "token_usage": None,
        "tool_calls": [],
        "structured_commands": structured_commands,
        "structured_executor": solver_audit,
        "gym_metrics": metrics,
        "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
        "bridge_log": session["bridge_log"],
        "agent_log": session["agent_log"],
        "execution_mode": "same_live_bridge_structured_constraint_executor",
    }


def _repair_command(
    task: dict[str, Any],
    selected: dict[str, Any],
    dynamic_slice: dict[str, Any],
) -> str:
    contract = task.get("contract", {})
    candidate_calls = selected.get("tool_calls", [])
    remaining = dynamic_slice.get("remaining_waypoints", [])
    remaining_bundle_suffixes = dynamic_slice.get("remaining_bundle_suffixes", [])
    separator = chr(10) * 2
    return separator.join(
        [
            (
                "A UAV mission execution has failed against its task contract. "
                "Perform one causal dual-slicing minimal repair using only the same live "
                "bridge session. The completed prefix is already executed and must not "
                "be replayed. Preserve all non-failing mission intent. Change only the "
                "smallest affected command or fields, then execute only the remaining "
                "waypoint suffix shown below. Use exactly one move_along_path call for "
                "each remaining bundle suffix listed below, preserving its bundle_id and "
                "using its assigned_drone unless the failure policy marks that UAV "
                "unavailable or reports a role/capability conflict; in either case "
                "reassign only the affected unfinished bundle to an available "
                "role-and-capability-compatible UAV and preserve its bundle_id. "
                "When repair_constraints specify unaffected bundles or a maximum "
                "reassignment count, do not reassign those unrelated bundles. "
                "Do not use move_to, navigate_to, takeoff, "
                "landing, probing, or unrelated calls. Do not restart the environment, "
                "do not execute completed waypoints again, and do not issue unrelated calls."
            ),
            f"Task contract: {json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}",
            f"Bridge dynamic slice: {json.dumps(dynamic_slice, ensure_ascii=False, separators=(',', ':'))}",
            f"Failed candidate tool calls: {json.dumps(candidate_calls, ensure_ascii=False, separators=(',', ':'))}",
            f"Remaining waypoint suffix to execute: {json.dumps(remaining, ensure_ascii=False, separators=(',', ':'))}",
        f"Remaining bundle suffixes (one move_along_path per entry): {json.dumps(remaining_bundle_suffixes, ensure_ascii=False, separators=(',', ':'))}",
            f"Failure policy: {json.dumps((task.get('contract', {}).get('failure_policy') if isinstance(task.get('contract'), dict) else None), ensure_ascii=False, separators=(',', ':'))}",
            "Return and execute the minimally repaired suffix, then return [TASK DONE].",
        ]
    )


def _contract_initial_command(task: dict[str, Any], candidate_index: int) -> str:
    """Use one contract-bound initial protocol for every SeSE candidate."""
    contract = task.get("contract", {}) if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission", {}) if isinstance(contract.get("mission"), dict) else {}
    bundles = mission.get("task_bundles", {}) if isinstance(mission, dict) else {}
    ordered = [dict(value) for value in bundles.values() if isinstance(value, dict)] if isinstance(bundles, dict) else []
    ordered.sort(key=lambda item: int(item.get("order", 0)))
    bundle_view = [
        {
            "bundle_id": item.get("bundle_id"),
            "order": item.get("order"),
            "drone_id": item.get("drone_id"),
            "required_capabilities": item.get("required_capabilities", []),
            "waypoints": item.get("waypoints", []),
        }
        for item in ordered
    ]
    instruction = str(task.get("instruction", "")).strip()
    command = (
        "Execute this UAV mission in the current live session. Inspect the available "
        "drones and state once, then execute every task bundle in order. The contract "
        "below is authoritative. For each bundle, issue exactly one "
        "move_along_path call using its listed numeric waypoint sequence, its assigned "
        "drone_id, and its bundle_id. Do not use navigate_to, move_to, hover, probing, "
        "or exploratory calls. Do not invent coordinates, reorder bundles, or count "
        "partial_success as completion. If the bridge reports a failure, stop the "
        "initial plan and report the failure; do not attempt repair in this phase. "
        "Only finish with [TASK DONE] after all bundles have completed successfully.\n\n"
        f"Mission instruction:\n{instruction}\n\n"
        f"MISSION CONTRACT:\n{json.dumps({'task_id': task.get('task_id'), 'agents': task.get('agents', []), 'mission': mission, 'resources': contract.get('resources', {}), 'failure_policy': contract.get('failure_policy', {})}, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"ORDERED TASK BUNDLES:\n{json.dumps(bundle_view, ensure_ascii=False, separators=(',', ':'))}"
    )
    if candidate_index > 0:
        command += (
            f"\n\nThis is independent candidate {candidate_index} of a pre-execution "
            "uncertainty sample. Follow the same contract exactly and do not refer "
            "to any other candidate."
        )
    return command


def _contract_intent(
    task: dict[str, Any],
    calls: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = task.get("contract", {}) if isinstance(task.get("contract"), dict) else {}
    expected = contract.get("mission", {}).get("required_waypoint_sequence", [])
    if isinstance(metrics, dict) and metrics.get("completed_waypoints") is not None:
        try:
            matched = min(max(0, int(metrics.get("completed_waypoints", 0))), len(expected))
        except (TypeError, ValueError):
            matched = 0
        coverage = matched / len(expected) if expected else 0.0
    else:
        observed = movement_signatures(calls)
        coverage, matched = _coverage(expected, observed)
    return {
        "coverage": coverage,
        "matched_waypoints": matched,
        "expected_waypoints": len(expected),
        "preserved": bool(expected) and coverage >= 1.0,
    }


def _assignment_scope_metrics(
    task: dict[str, Any],
    initial_metrics: dict[str, Any] | None,
    final_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Measure whether repair stayed inside the declared affected scope."""
    initial = (
        initial_metrics.get("bundle_assignment", {})
        if isinstance(initial_metrics, dict)
        else {}
    )
    final = (
        final_metrics.get("bundle_assignment", {})
        if isinstance(final_metrics, dict)
        else {}
    )
    initial = initial if isinstance(initial, dict) else {}
    final = final if isinstance(final, dict) else {}
    changed = sorted(
        str(bundle_id)
        for bundle_id in set(initial) | set(final)
        if str(initial.get(bundle_id)) != str(final.get(bundle_id))
    )
    profile = (
        task.get("contract", {}).get("failure_policy", {})
        if isinstance(task.get("contract"), dict)
        else {}
    )
    unaffected = profile.get("unaffected_bundle_ids", []) if isinstance(profile, dict) else []
    unaffected = [str(item) for item in unaffected]
    if not unaffected:
        failure = (
            initial_metrics.get("first_failure") or {}
            if isinstance(initial_metrics, dict)
            else {}
        )
        affected_bundle_id = str(
            (failure.get("details", {}) or {}).get("bundle_id")
            or profile.get("bundle_id")
            or ""
        )
        bundles = (
            task.get("contract", {}).get("mission", {}).get("task_bundles", {})
            if isinstance(task.get("contract"), dict)
            else {}
        )
        unaffected = [
            str(bundle.get("bundle_id") or owner_id)
            for owner_id, bundle in bundles.items()
            if isinstance(bundle, dict)
            and str(bundle.get("bundle_id") or owner_id) != affected_bundle_id
        ]
    preserved = [
        bundle_id
        for bundle_id in unaffected
        if str(initial.get(bundle_id)) == str(final.get(bundle_id))
    ]
    return {
        "reassigned_bundle_count": len(changed),
        "reassigned_bundle_ids": changed,
        "unaffected_bundle_preservation_rate": (
            round(len(preserved) / len(unaffected), 6) if unaffected else None
        ),
        "unaffected_bundle_count": len(unaffected),
        "unaffected_bundle_preserved_count": len(preserved),
        "declared_allowed_edit_scope": profile.get("repair_constraints", {}).get("allowed_edit_scope")
        if isinstance(profile, dict) and isinstance(profile.get("repair_constraints"), dict)
        else [],
    }


def _quantized_xyz(value: Any, quantum: float = 0.05) -> tuple[int, int, int] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z", value.get("altitude"))]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        xyz = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return tuple(int(round(item / quantum)) for item in xyz)


def _repair_action_signatures(
    dynamic_slice: dict[str, Any],
    repair_history: list[dict[str, Any]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Build action identities for the original remainder and repair suffix.

    The comparison follows the action-level plan-stability/action-distance
    convention: actions are compared as sets, while the identity retains the
    UAV and bundle assignment because these are semantically meaningful in a
    heterogeneous multi-UAV mission.  The already executed prefix is
    intentionally excluded from both sides.
    """

    reference: list[tuple[Any, ...]] = []
    suffixes = dynamic_slice.get("remaining_bundle_suffixes", [])
    if isinstance(suffixes, list):
        for entry in suffixes:
            if not isinstance(entry, dict):
                continue
            bundle_id = str(entry.get("bundle_id", ""))
            drone_id = str(entry.get("assigned_drone", ""))
            waypoints = entry.get("remaining_waypoints", [])
            if not isinstance(waypoints, list):
                continue
            for waypoint in waypoints:
                xyz = _quantized_xyz(waypoint)
                if xyz is not None:
                    reference.append(("MOVE_PATH", drone_id, bundle_id, xyz))

    # Use the bridge's executed history instead of the LLM JSON arguments.
    # The Agent4Drone adapter may expose coordinate objects as serialized
    # strings, while the bridge history contains the actual executed targets.
    repaired: list[tuple[Any, ...]] = []
    reference_by_drone: dict[str, list[tuple[str, tuple[int, int, int]]]] = {}
    for action in reference:
        _, drone_id, bundle_id, xyz = action
        reference_by_drone.setdefault(str(drone_id), []).append((str(bundle_id), xyz))
    used_reference: set[tuple[str, tuple[int, int, int]]] = set()
    for entry in repair_history:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command", ""))
        if command == "move_along_path":
            executed = entry.get("waypoint_results", [])
            if not isinstance(executed, list):
                continue
            drone_id = str(entry.get("drone_id", ""))
            for waypoint_result in executed:
                if not isinstance(waypoint_result, dict) or not waypoint_result.get("success"):
                    continue
                xyz = _quantized_xyz(waypoint_result.get("position"))
                if xyz is None:
                    continue
                bundle_id = ""
                for candidate_bundle, candidate_xyz in reference_by_drone.get(drone_id, []):
                    candidate = (candidate_bundle, candidate_xyz)
                    if candidate not in used_reference and candidate_xyz == xyz:
                        bundle_id = candidate_bundle
                        used_reference.add(candidate)
                        break
                if not bundle_id:
                    for _, candidate_drone, candidate_bundle, candidate_xyz in reference:
                        candidate = (candidate_bundle, candidate_xyz)
                        if candidate not in used_reference and candidate_xyz == xyz:
                            bundle_id = candidate_bundle
                            used_reference.add(candidate)
                            break
                repaired.append(("MOVE_PATH", drone_id, bundle_id, xyz))
        elif command == "move_to" and entry.get("success"):
            xyz = _quantized_xyz(entry.get("position"))
            if xyz is not None:
                drone_id = str(entry.get("drone_id", ""))
                bundle_id = ""
                for candidate_bundle, candidate_xyz in reference_by_drone.get(drone_id, []):
                    candidate = (candidate_bundle, candidate_xyz)
                    if candidate not in used_reference and candidate_xyz == xyz:
                        bundle_id = candidate_bundle
                        used_reference.add(candidate)
                        break
                if not bundle_id:
                    for _, candidate_drone, candidate_bundle, candidate_xyz in reference:
                        candidate = (candidate_bundle, candidate_xyz)
                        if candidate not in used_reference and candidate_xyz == xyz:
                            bundle_id = candidate_bundle
                            used_reference.add(candidate)
                            break
                repaired.append(("MOVE_PATH", drone_id, bundle_id, xyz))
    return reference, repaired


def _plan_distance(
    dynamic_slice: dict[str, Any],
    repair_history: list[dict[str, Any]],
) -> dict[str, Any]:
    reference, repaired = _repair_action_signatures(dynamic_slice, repair_history)
    reference_set = set(reference)
    repaired_set = set(repaired)
    union = reference_set | repaired_set
    changed = reference_set ^ repaired_set
    return {
        "plan_stability_distance": len(changed),
        "action_distance": round(len(changed) / len(union), 6) if union else 0.0,
        "reference_action_count": len(reference),
        "repair_action_count": len(repaired),
        "shared_action_count": len(reference_set & repaired_set),
        "comparison_scope": "remaining_original_suffix_vs_repair_suffix",
        "completed_prefix_excluded": True,
        "action_identity": "command+assigned_drone+bundle_id+quantized_waypoint",
        "waypoint_quantization_m": 0.05,
        "distance_protocol_version": "plan_distance_v1",
    }


def _start_live_candidate(
    task: dict[str, Any],
    candidate_index: int,
    args: argparse.Namespace,
    agent_environment: dict[str, str],
    agent_dir: Path,
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    slot = candidate_index
    bridge_port = args.bridge_port_base + slot
    agent_port = args.agent_port_base + slot
    bridge_url = f"http://127.0.0.1:{bridge_port}"
    agent_url = f"http://127.0.0.1:{agent_port}"
    safe = task_id.replace("/", "_").replace(chr(92), "_")
    bridge_log = args.log_dir / f"cds_mr_bridge_{safe}_candidate{candidate_index}.log"
    agent_log = args.log_dir / f"cds_mr_agent_{safe}_candidate{candidate_index}.log"
    bridge_handle = bridge_log.open("w", encoding="utf-8")
    agent_handle = agent_log.open("w", encoding="utf-8")
    bridge = None
    agent = None
    try:
        bridge = start_bridge(task, args, bridge_port, bridge_handle)
        wait_health(bridge_url, args.startup_timeout)
        agent = start_agent(
            args,
            agent_port,
            bridge_port,
            agent_environment,
            agent_handle,
            agent_dir=agent_dir,
        )
        health = wait_health(agent_url, args.startup_timeout, require_agent=True)
        return {
            "candidate_index": candidate_index,
            "bridge": bridge,
            "agent": agent,
            "bridge_url": bridge_url,
            "agent_url": agent_url,
            "agent_health": health,
            "bridge_log": str(bridge_log),
            "agent_log": str(agent_log),
            "bridge_handle": bridge_handle,
            "agent_handle": agent_handle,
        }
    except Exception:
        terminate_process(agent)
        terminate_process(bridge)
        bridge_handle.close()
        agent_handle.close()
        raise


def _execute_live_candidate(
    session: dict[str, Any],
    command: str,
    args: argparse.Namespace,
    candidate_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    phase = time.perf_counter()
    result = run_agent_command(
        session["agent_url"],
        command,
        args.command_timeout,
        args.poll_interval,
    )
    command_seconds = time.perf_counter() - phase
    metrics = requests.get(f"{session['bridge_url']}/bridge/metrics", timeout=10).json()
    task_id = metrics.get("task_id")
    calls = extract_tool_calls(result.get("intermediate_steps", []))
    return {
        "candidate_index": candidate_index,
        "agent_health": session["agent_health"],
        "api_success": result.get("success"),
        "token_usage": result.get("token_usage"),
        "tool_calls": calls,
        "gym_metrics": metrics,
        "timing": {
            "command_seconds": round(command_seconds, 6),
            "total_seconds": round(time.perf_counter() - started, 6),
        },
        "bridge_log": session["bridge_log"],
        "agent_log": session["agent_log"],
        "bridge_task_id": task_id,
    }


def _close_live_candidate(session: dict[str, Any]) -> None:
    terminate_process(session.get("agent"))
    terminate_process(session.get("bridge"))
    for key in ("bridge_handle", "agent_handle"):
        handle = session.get(key)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def _decision_latency(initial_metrics: dict[str, Any], final_metrics: dict[str, Any]) -> float | None:
    failure = initial_metrics.get("first_failure")
    if not isinstance(failure, dict):
        return None
    try:
        failure_time = float(failure.get("wall_time_seconds"))
    except (TypeError, ValueError):
        return None
    history = final_metrics.get("command_history", [])
    if not isinstance(history, list):
        return None
    starts = []
    for entry in history:
        if not isinstance(entry, dict) or not entry.get("success"):
            continue
        try:
            start = float(entry.get("wall_started_seconds"))
        except (TypeError, ValueError):
            continue
        if start >= failure_time:
            starts.append(start)
    return round(max(0.0, min(starts) - failure_time), 6) if starts else None


def _run_task(
    task: dict[str, Any],
    args: argparse.Namespace,
    env: dict[str, str],
    agent_dirs: list[Path],
) -> dict[str, Any]:
    started = time.perf_counter()
    sessions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    try:
        for index in range(args.num_candidates):
            session = _start_live_candidate(task, index, args, env, agent_dirs[index])
            sessions.append(session)
            try:
                record = _execute_live_candidate(
                    session,
                    _contract_initial_command(task, index),
                    args,
                    index,
                )
            except Exception as exc:
                record = {
                    "candidate_index": index,
                    "status": "runner_error",
                    "error": repr(exc),
                    "tool_calls": [],
                    "gym_metrics": None,
                    "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
                    "bridge_log": session["bridge_log"],
                    "agent_log": session["agent_log"],
                }
            candidates.append(record)

        calls = [
            item.get("tool_calls", []) if isinstance(item.get("tool_calls"), list) else []
            for item in candidates
        ]
        gate = multi_candidate_gate(
            task.get("goal", {}).get("waypoints", []),
            calls,
            args.entropy_threshold,
        )
        selected_index = gate.get("selected_index")
        selected_index = (
            int(selected_index)
            if selected_index is not None and 0 <= int(selected_index) < len(candidates)
            else 0
        )
        sese_selected_candidate = candidates[selected_index]
        selected_session = sessions[selected_index]
        dynamic_slice = _dynamic_slice(task, sese_selected_candidate)
        repair_record: dict[str, Any] | None = None
        repair_info: dict[str, Any] = {
            "triggered": dynamic_slice is not None,
            "repair_success": False,
            "intent_preserved": False,
            "same_live_session": False,
            "executor": None,
        }
        final_record = sese_selected_candidate
        repair_history_for_metrics: list[dict[str, Any]] = []
        if dynamic_slice is not None:
            initial_failure_events = (sese_selected_candidate.get("gym_metrics") or {}).get("failure_events", [])
            initial_failure_count = len(initial_failure_events) if isinstance(initial_failure_events, list) else 0
            initial_command_history = (sese_selected_candidate.get("gym_metrics") or {}).get("command_history", [])
            initial_command_history_count = len(initial_command_history) if isinstance(initial_command_history, list) else 0
            repair_window = None
            try:
                response = requests.post(
                    f"{selected_session["bridge_url"]}/bridge/repair-window",
                    timeout=10,
                )
                response.raise_for_status()
                repair_window = response.json()
            except requests.RequestException as exc:
                repair_window = {"enabled": False, "error": repr(exc)}
            repair_executor = args.repair_executor
            if repair_executor == "auto":
                repair_executor = "structured" if _is_v6_comparison(task) else "llm"
            if repair_executor == "structured":
                repair_record = _execute_structured_repair(
                    selected_session,
                    task,
                    sese_selected_candidate,
                    dynamic_slice,
                    args.num_candidates,
                )
            else:
                try:
                    repair_record = _execute_live_candidate(
                        selected_session,
                        _repair_command(task, sese_selected_candidate, dynamic_slice),
                        args,
                        args.num_candidates,
                    )
                except Exception as exc:
                    try:
                        repair_metrics = requests.get(
                            f"{selected_session['bridge_url']}/bridge/metrics",
                            timeout=10,
                        ).json()
                    except requests.RequestException:
                        repair_metrics = None
                    repair_record = {
                        "candidate_index": args.num_candidates,
                        "status": "repair_runner_error",
                        "error": repr(exc),
                        "tool_calls": [],
                        "gym_metrics": repair_metrics,
                        "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
                        "bridge_log": selected_session["bridge_log"],
                        "agent_log": selected_session["agent_log"],
                    }
                repair_record["execution_mode"] = "same_live_bridge_and_agent_session"
            repair_metrics = repair_record.get("gym_metrics") if isinstance(repair_record.get("gym_metrics"), dict) else {}
            full_repair_history = repair_metrics.get("command_history", []) if isinstance(repair_metrics, dict) else []
            repair_history = (
                full_repair_history[initial_command_history_count:]
                if isinstance(full_repair_history, list)
                else []
            )
            repair_history_for_metrics = repair_history
            plan_distance = _plan_distance(dynamic_slice, repair_history)
            repair_intent = _contract_intent(
                task,
                repair_record.get("tool_calls", []),
                repair_metrics,
            )
            failure_events = repair_metrics.get("failure_events", []) if isinstance(repair_metrics, dict) else []
            post_repair_failure_events = failure_events[initial_failure_count:] if isinstance(failure_events, list) else []
            repair_info.update(
                {
                    "repair_success": bool(repair_metrics.get("task_success")) and repair_intent["preserved"],
                    "intent_preserved": repair_intent["preserved"],
                    "intent": repair_intent,
                    "failure_after_repair": post_repair_failure_events[-1] if post_repair_failure_events else None,
                    "new_failure_events_after_repair": post_repair_failure_events,
                    "completed_waypoints_after_repair": repair_metrics.get("completed_waypoints"),
                    "same_live_session": True,
                    "executor": repair_executor,
                    "structured_executor": repair_record.get("structured_executor"),
                    "repair_window": repair_window,
                    "plan_distance": plan_distance,
                    "token_usage": repair_record.get("token_usage"),
                    "timing": repair_record.get("timing"),
                }
            )
            final_record = repair_record

        selected_intent = _contract_intent(
            task,
            sese_selected_candidate.get("tool_calls", []),
        )
        final_metrics = final_record.get("gym_metrics") if isinstance(final_record.get("gym_metrics"), dict) else None
        assignment_scope = _assignment_scope_metrics(
            task,
            sese_selected_candidate.get("gym_metrics")
            if isinstance(sese_selected_candidate.get("gym_metrics"), dict)
            else None,
            final_metrics,
        )
        final_intent = _contract_intent(
            task,
            final_record.get("tool_calls", []),
            final_metrics if repair_record is not None else None,
        )
        repair_timing = repair_info.get("timing") if isinstance(repair_info.get("timing"), dict) else {}
        plan_distance = repair_info.get("plan_distance") if isinstance(repair_info.get("plan_distance"), dict) else {}
        task_success_value = final_metrics.get("task_success") if isinstance(final_metrics, dict) else None
        recoverable_failure_condition = str(task.get("metadata", {}).get("condition") or "") == "recoverable_failure"
        selected_initial_metrics = (
            sese_selected_candidate.get("gym_metrics")
            if isinstance(sese_selected_candidate.get("gym_metrics"), dict)
            else {}
        )
        final_metrics_for_eval = final_metrics if isinstance(final_metrics, dict) else {}
        decision_latency = _decision_latency(selected_initial_metrics, final_metrics_for_eval)
        common = common_metrics(
            task,
            selected_initial_metrics,
            final_metrics_for_eval,
            plan_stability_distance=(
                plan_distance.get("plan_stability_distance")
                if recoverable_failure_condition and plan_distance
                else None
            ),
            action_distance=(
                plan_distance.get("action_distance")
                if recoverable_failure_condition and plan_distance
                else None
            ),
            repair_decision_latency_seconds=decision_latency,
            runner_wallclock_seconds=time.perf_counter() - started,
            mission_completion_time_seconds=(
                final_metrics_for_eval.get("sim_time_seconds")
                if final_metrics_for_eval.get("task_success")
                else None
            ),
            repair_attempted=repair_record is not None,
        )
        standard_metrics = {
            **common,
            "task_success": (
                bool(task_success_value) and bool(final_intent.get("preserved"))
                if task_success_value is not None
                else None
            ),
            "failure_recovery_success": common["recovery_success_rate_item"],
            "plan_stability_distance": (
                plan_distance.get("plan_stability_distance")
                if recoverable_failure_condition and plan_distance
                else None
            ),
            "action_distance": (
                plan_distance.get("action_distance")
                if recoverable_failure_condition and plan_distance
                else None
            ),
            "repair_latency_seconds": decision_latency,
            "reassigned_bundle_count": (
                assignment_scope.get("reassigned_bundle_count")
                if recoverable_failure_condition
                else None
            ),
            "unaffected_bundle_preservation_rate": (
                assignment_scope.get("unaffected_bundle_preservation_rate")
                if recoverable_failure_condition
                else None
            ),
            "assignment_scope": assignment_scope if recoverable_failure_condition else None,
            "completion_time_seconds": common["mission_completion_time_seconds"],
            "repair_phase_wallclock_seconds": (
                repair_timing.get("total_seconds")
                if repair_record is not None
                else None
            ),
        }
        return {
            "protocol_version": "cds_mr_contract_online_v3_structured_repair",
            "method": _method_definition(args),
            "method_definition": _method_definition(args),
            "repair_module": REPAIR_MODULE,
            "baseline_condition": (
                "B0" if args.ablation_variant == "b0_cds_mr" else "B0+SeSE"
            ),
            "ablation_variant": args.ablation_variant,
            "task_id": str(task["task_id"]),
            "source_task_id": task.get("metadata", {}).get("source_task_id"),
            "condition": task.get("metadata", {}).get("condition"),
            "run_seed_index": task.get("metadata", {}).get("run_seed_index"),
            "run_seed": task.get("metadata", {}).get("run_seed"),
            "source_dataset": task.get("source_dataset"),
            "split_seed": task.get("metadata", {}).get("split_seed"),
            "episode_seed": task.get("metadata", {}).get("episode_seed"),
            "scenario_id": task.get("scenario", {}).get("scenario_id"),
            "selection_uses_simulator_outcome": False,
            "candidate_count": len(candidates),
            "sese_gate": gate,
            "sese_selected_candidate_index": selected_index,
            "sese_selected_candidate": sese_selected_candidate,
            "sese_selected_intent": selected_intent,
            "bridge_first_failure": (sese_selected_candidate.get("gym_metrics") or {}).get("first_failure"),
            "cds_mr_dynamic_slice": dynamic_slice,
            "cds_mr_repair": repair_info,
            "repair_record": repair_record,
            "selected_candidate": final_record,
            "final_intent": final_intent,
            "standard_metrics": standard_metrics,
            "candidates": candidates,
            "repair_execution_protocol": {
                "same_bridge_reused": bool(repair_record),
                "same_agent_session_reused": bool(
                    repair_record
                    and repair_record.get("execution_mode") == "same_live_bridge_and_agent_session"
                ),
                "repair_llm_called": bool(
                    repair_record
                    and repair_record.get("execution_mode") == "same_live_bridge_and_agent_session"
                ),
                "structured_constraint_executor": bool(
                    repair_record
                    and repair_record.get("execution_mode")
                    == "same_live_bridge_structured_constraint_executor"
                ),
                "remaining_waypoint_suffix_only": bool(dynamic_slice),
            },
            "total_tokens": sum(_token_count(item.get("token_usage")) for item in candidates)
            + _token_count((repair_record or {}).get("token_usage")),
            "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
        }
    finally:
        for session in sessions:
            _close_live_candidate(session)


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                ids.add(str(json.loads(line)["task_id"]))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument(
        "--condition",
        choices=["all", "nominal", "recoverable_failure"],
        default="all",
    )
    parser.add_argument("--num-candidates", type=int, default=3)
    parser.add_argument(
        "--ablation-variant",
        choices=["full", "b0_cds_mr"],
        default="full",
    )
    parser.add_argument(
        "--repair-executor",
        choices=["auto", "structured", "llm"],
        default="auto",
        help="auto selects the structured executor for v6 and the legacy LLM repair elsewhere",
    )
    parser.add_argument("--candidate-temperature", type=float, default=0.2)
    parser.add_argument("--entropy-threshold", type=float, default=0.8)
    parser.add_argument("--bridge-port-base", type=int, default=8800)
    parser.add_argument("--agent-port-base", type=int, default=18800)
    parser.add_argument("--agent-dir", type=Path, default=Path("~/uav_exp/MultiUAV-Plat/agent4drone"))
    parser.add_argument("--agent-env-file", type=Path, default=Path("~/.config/agent4drone/agent4drone.env"))
    parser.add_argument("--log-dir", type=Path, default=Path("~/uav_exp/data/logs/cds_mr_contract_online"))
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--command-timeout", type=float, default=420)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--bridge-max-steps", type=int, default=None)
    parser.add_argument("--uvicorn-log-level", default="warning")
    args = parser.parse_args()
    if args.ablation_variant == "b0_cds_mr":
        if args.num_candidates != 1:
            parser.error("b0_cds_mr requires --num-candidates 1")
    elif args.num_candidates < 2:
        parser.error("full variant requires --num-candidates at least 2")
    args.input = args.input.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.agent_dir = args.agent_dir.expanduser().resolve()
    args.agent_env_file = args.agent_env_file.expanduser().resolve()
    args.log_dir = args.log_dir.expanduser().resolve()
    args.gate_mode = "sese"
    args.sampling_mode = "stationary"
    tasks = read_tasks(args.input, limit=None)
    if args.condition != "all":
        tasks = [
            task
            for task in tasks
            if str(task.get("metadata", {}).get("condition") or "") == args.condition
        ]
    tasks = tasks[: args.limit]
    if len(tasks) != args.limit:
        raise SystemExit(f"expected {args.limit} tasks, found {len(tasks)}")
    env = load_environment_file(args.agent_env_file)
    if "LLM_API_KEY" not in env and "LLM_API_KEY" not in os.environ:
        raise SystemExit(f"LLM_API_KEY not found in environment or {args.agent_env_file}")
    existing = _existing_ids(args.output)
    pending = [task for task in tasks if str(task["task_id"]) not in existing]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    temperatures = [0.0] + [args.candidate_temperature] * (args.num_candidates - 1)
    temp_root, agent_dirs = _prepare_agent_dirs(args.agent_dir, temperatures)
    print(json.dumps({
        "method_definition": _method_definition(args),
        "repair_module": REPAIR_MODULE,
        "tasks": len(tasks),
        "already_completed": len(tasks) - len(pending),
        "pending": len(pending),
        "candidates_per_task": args.num_candidates,
        "repair_executor": args.repair_executor,
        "output": str(args.output),
    }, ensure_ascii=False), flush=True)
    try:
        with args.output.open("a" if existing else "w", encoding="utf-8") as handle:
            completed = len(existing)
            for task in pending:
                record = _run_task(task, args, env, agent_dirs)
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                completed += 1
                print(json.dumps({
                    "completed": completed,
                    "task_id": record["task_id"],
                    "repair_triggered": record["cds_mr_repair"]["triggered"],
                    "repair_success": record["cds_mr_repair"]["repair_success"],
                }, ensure_ascii=False), flush=True)
    finally:
        import shutil

        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
