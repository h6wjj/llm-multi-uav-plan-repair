"""Small-fleet total-route reallocation with no plan-edit preference.

The optimizer enumerates every available, contract-eligible assignment of
unfinished decision-variable bundles.  It shares the same live state and
execution order as the local and CDS-MR arms; the bridge remains the final
authority for dynamic deadlines and resource budgets.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from run_cds_mr_contract_online import (
    _compatible,
    _live_agent_view,
    _ordered_bundles,
    _route_cost,
    _topological_remaining_order,
    _xyz,
)


def global_total_route_plan(
    task: dict[str, Any],
    initial_metrics: dict[str, Any],
    dynamic_slice: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bundles = _ordered_bundles(task)
    by_id = {str(bundle["bundle_id"]): bundle for bundle in bundles}
    suffixes = {
        str(item["bundle_id"]): dict(item)
        for item in dynamic_slice.get("remaining_bundle_suffixes", [])
        if isinstance(item, dict) and item.get("bundle_id")
    }
    if not suffixes:
        raise RuntimeError("global_no_remaining_suffixes")

    mission = (task.get("contract") or {}).get("mission") or {}
    dependencies = mission.get("bundle_dependencies") or {}
    progress = initial_metrics.get("completed_waypoints_by_bundle") or {}
    completed_ids = {
        bundle_id for bundle_id, bundle in by_id.items()
        if int(progress.get(bundle_id, 0) or 0) >= len(bundle.get("waypoints") or [])
    }
    order = _topological_remaining_order(bundles, set(suffixes), completed_ids, dependencies)
    agents, unavailable = _live_agent_view(task, initial_metrics, dynamic_slice)
    positions = {
        agent_id: point
        for agent_id, agent in agents.items()
        if (point := (_xyz(agent.get("position")) or _xyz(agent.get("start")))) is not None
    }
    original = initial_metrics.get("bundle_assignment") or {}
    current = {
        bundle_id: str(
            original.get(bundle_id)
            or suffixes[bundle_id].get("assigned_drone")
            or by_id[bundle_id].get("original_owner")
            or by_id[bundle_id].get("drone_id")
        )
        for bundle_id in order
    }
    failure = dynamic_slice.get("first_failure") or {}
    failure_detail = failure.get("details") or {}
    failed_bundle = str(failure_detail.get("bundle_id") or (task.get("contract") or {}).get("failure_policy", {}).get("bundle_id") or "")
    failed_drone = str(failure.get("drone_id") or "")

    candidates: dict[str, list[str]] = {}
    for bundle_id in order:
        bundle = by_id[bundle_id]
        allowed_ids = {str(value) for value in bundle.get("eligible_drone_ids") or agents}
        allowed = sorted(
            agent_id for agent_id in allowed_ids
            if agent_id in agents
            and agent_id in positions
            and agent_id not in unavailable
            and not (bundle_id == failed_bundle and agent_id == failed_drone)
            and _compatible(agents[agent_id], bundle)
        )
        if not bool(bundle.get("assignment_is_decision_variable", True)):
            allowed = [current[bundle_id]] if current[bundle_id] in allowed else []
        if not allowed:
            raise RuntimeError("global_no_compatible_candidate:" + bundle_id)
        candidates[bundle_id] = allowed

    best_key: tuple[Any, ...] | None = None
    best_assignment: dict[str, str] | None = None
    explored = 0
    feasible = 0
    for owners in itertools.product(*(candidates[bundle_id] for bundle_id in order)):
        explored += 1
        assignment = dict(zip(order, owners))
        cursors = {agent_id: list(point) for agent_id, point in positions.items()}
        route_cost = 0.0
        valid = True
        for bundle_id in order:
            waypoints = suffixes[bundle_id].get("remaining_waypoints") or []
            endpoint = _xyz(waypoints[-1]) if waypoints else None
            if endpoint is None:
                valid = False
                break
            owner = assignment[bundle_id]
            leg_cost = _route_cost(cursors[owner], waypoints)
            if not math.isfinite(leg_cost):
                valid = False
                break
            route_cost += leg_cost
            cursors[owner] = endpoint
        if not valid:
            continue
        feasible += 1
        key = (round(route_cost, 12), owners)
        if best_key is None or key < best_key:
            best_key = key
            best_assignment = assignment
    if best_assignment is None or best_key is None:
        raise RuntimeError("global_no_route_feasible_assignment")

    plan = [
        {
            "sequence": index,
            "bundle_id": bundle_id,
            "assigned_drone": best_assignment[bundle_id],
            "original_assignment": current[bundle_id],
            "remaining_waypoints": suffixes[bundle_id].get("remaining_waypoints") or [],
            "dependencies": [str(item) for item in dependencies.get(bundle_id, []) or []],
        }
        for index, bundle_id in enumerate(order, start=1)
    ]
    audit = {
        "executor": "global_total_route_reallocation_v1",
        "solver_status": "optimal_exhaustive_route_cost_given_live_state_and_candidate_graph",
        "objective": "minimum_full_suffix_live_state_route_cost_without_edit_penalty",
        "tie_break": "lexicographic_UAV_id_tuple_in_topological_bundle_order",
        "candidate_graph": candidates,
        "selected_assignment": best_assignment,
        "current_assignment": current,
        "estimated_full_suffix_route_cost": round(float(best_key[0]), 9),
        "enumerated_assignment_count": explored,
        "route_feasible_assignment_count": feasible,
        "unavailable_drone_ids": sorted(unavailable),
        "hard_constraints_prechecked": [
            "immutable_completed_prefix_by_suffix_construction",
            "eligible_drone_ids",
            "role_and_capability_eligibility",
            "bundle_dependency_topological_order",
            "live_UAV_availability",
        ],
        "hard_constraints_runtime_checked_by_bridge": [
            "remaining_waypoint_sequence",
            "eligible_drone_ids",
            "role_and_capability_eligibility",
            "bundle_dependencies",
            "shared_execution_budget",
            "deadline",
            "live_UAV_availability",
        ],
        "selection_uses_simulator_outcome": False,
    }
    return plan, audit
