"""Build a hand-checkable suffix-coupling mechanism experiment.

The cases are intentionally constructed before execution.  The direct local
replacement cost for ``uav_2`` is smaller than that for ``uav_3`` when taking
over B2, but the cumulative suffix route cost is smaller when ``uav_3`` takes
B2 and the original downstream owners keep B3/B4.  This is a mechanism test,
not a population-level benchmark.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


TOLERANCE = 0.02
MIRROR_AXIS_X = 6.0

CANONICAL_STARTS = {
    "uav_0": [0.0, 0.8, 1.0],
    "uav_1": [0.6, 0.0, 1.0],
    "uav_2": [0.6, 0.45, 1.0],
    "uav_3": [0.9, 0.2, 1.0],
}

CANONICAL_PATHS = {
    "B1": [[0.0, 0.8, 1.0], [0.12, 0.8, 1.0]],
    "B2": [[0.6, 0.0, 1.0], [0.6, 0.1, 1.0], [0.6, 0.2, 1.0]],
    "B3": [[0.6, 0.6, 1.0], [0.6, 0.7, 1.0]],
    # B4 starts at the B2 takeover endpoint, so the handoff is continuous;
    # its only non-zero intra-bundle leg remains well above the tolerance.
    "B4": [[0.6, 0.2, 1.0], [0.6, 0.3, 1.0]],
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mirror_point(point: list[float], enabled: bool) -> list[float]:
    if not enabled:
        return list(point)
    return [MIRROR_AXIS_X - float(point[0]), float(point[1]), float(point[2])]


def route_cost(start: list[float], waypoints: list[list[float]]) -> float:
    if not waypoints:
        return 0.0
    return math.dist(start, waypoints[0]) + sum(
        math.dist(left, right) for left, right in zip(waypoints, waypoints[1:])
    )


def build_case(template: dict[str, Any], task_id: str, *, mirror: bool, branch: bool, index: int) -> dict[str, Any]:
    task = copy.deepcopy(template)
    starts = {agent_id: mirror_point(point, mirror) for agent_id, point in CANONICAL_STARTS.items()}
    paths = {bundle_id: [mirror_point(point, mirror) for point in points] for bundle_id, points in CANONICAL_PATHS.items()}

    task["task_id"] = task_id
    task["instruction"] = (
        "Pre-registered mechanism stress test: compare immediate local replacement "
        "with suffix-aware replacement under a partial UAV failure."
    )
    for agent in task.get("agents", []):
        agent_id = str(agent.get("agent_id"))
        agent["start"] = starts[agent_id]
        agent["extension_start_provenance"] = "preregistered_mechanism_stress_v2"

    mission = task.setdefault("contract", {}).setdefault("mission", {})
    mission["waypoint_tolerance"] = TOLERANCE
    mission["bundle_dependencies"] = (
        {"B2": ["B1"], "B3": ["B1"], "B4": ["B2", "B3"]}
        if branch
        else {"B2": ["B1"], "B3": ["B2"], "B4": ["B3"]}
    )
    required: list[list[float]] = []
    cursor = 0
    by_bundle_id = {
        str(bundle.get("bundle_id")): bundle
        for bundle in mission.get("task_bundles", {}).values()
        if isinstance(bundle, dict)
    }
    for bundle_id in ("B1", "B2", "B3", "B4"):
        bundle = by_bundle_id[bundle_id]
        points = paths[bundle_id]
        bundle["waypoints"] = points
        bundle["waypoint_indices"] = list(range(cursor, cursor + len(points)))
        bundle["deadline_s"] = 600.0
        bundle["repair_policy"] = "mechanism_stress_v2"
        bundle["original_owner"] = bundle.get("drone_id")
        bundle["assignment_is_decision_variable"] = True
        # Only the failed B2 bundle has two replacement candidates.  Keeping
        # B3/B4 fixed isolates the suffix-coupling decision being tested.
        if bundle_id == "B2":
            bundle["eligible_drone_ids"] = ["uav_2", "uav_3"]
            # Keep the original mapping specialist valid for the initial plan,
            # while excluding the mission-lead UAV from the replacement pool
            # once uav_1 is declared unavailable.
            bundle["eligible_roles"] = ["mapping_specialist", "scout", "relay"]
        elif bundle_id == "B3":
            bundle["eligible_drone_ids"] = ["uav_2"]
        elif bundle_id == "B4":
            bundle["eligible_drone_ids"] = ["uav_3"]
        required.extend(points)
        cursor += len(points)
    mission["required_waypoint_sequence"] = required
    task.setdefault("goal", {})["waypoints"] = required
    task["contract"]["contract_version"] = "mission_contract_mechanism_stress_v2"
    task["contract"].setdefault("resources", {}).update(
        {
            "max_total_steps": 900,
            "base_step_budget": 900,
            "repair_step_budget": 300,
            "time_budget_s": 600.0,
            "motion_speed_scale": 0.3,
            "freeze_idle_drones": True,
            "kinematic_mechanism_audit": True,
            "kinematic_step_distance": 0.01,
        }
    )

    failure = task["contract"].setdefault("failure_policy", {})
    failure.update(
        {
            "type": "comparative_repair_choice_after_partial_execution",
            "fault_injection": True,
            "trigger_source": "preregistered_mechanism_stress_v2",
            "repairable": True,
            "drone_id": "uav_1",
            "bundle_id": "B2",
            "after_completed_waypoints": 2,
            "required_capabilities": ["gnss"],
            "replacement_candidates": ["uav_2", "uav_3"],
            "constraint_layer": "dependency_route_competition",
            "bundle_dependencies": mission["bundle_dependencies"],
            "repair_policy": "replan_unfinished_work_under_shared_hard_constraints",
        }
    )
    scenario = task.setdefault("scenario", {})
    scenario.update(
        {
            "scenario_version": "mechanism_stress_v2",
            "scenario_id": task_id,
            "source_task_id": task_id,
            "condition": "recoverable_failure",
            "condition_label": "recoverable_failure_uncertain",
            "planning_uncertainty": True,
            "evaluation_mode": "predeclared_mechanism_test",
            "repair_module": "CDS-MR",
            "fault_injection": True,
            "paired_protocol": {
                "same_geometry_and_failure_state": True,
                "same_initial_plan": True,
                "only_repair_selection_rule_varies": True,
                "candidate_selection_before_suffix_execution": True,
            },
            "method_boundaries": {
                "simple_local": "minimum immediate B2 route cost",
                "CDS-MR": "minimum cumulative live-state suffix route cost after causal closure",
            },
            "population_claim": False,
        }
    )
    metadata = task.setdefault("metadata", {})
    metadata.update(
        {
            "source_name": "mechanism_stress_v2",
            "source_task_id": task_id,
            "run_seed_index": index,
            "run_seed": 9400 + index,
            "mechanism_stress": "suffix_route_cost_branch_v2" if branch else "suffix_route_cost_competition_v2",
            "preregistered": True,
            "population_claim": False,
            "execution_profile": "gym_waypoint_projection_v1",
            "waypoint_tolerance": TOLERANCE,
            "mirror_geometry": mirror,
        }
    )
    return task


def make_audit(rows: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    starts = {agent_id: list(point) for agent_id, point in CANONICAL_STARTS.items()}
    paths = {bundle_id: [list(point) for point in points] for bundle_id, points in CANONICAL_PATHS.items()}
    direct_uav2 = route_cost(starts["uav_2"], paths["B2"][2:])
    direct_uav3 = route_cost(starts["uav_3"], paths["B2"][2:])
    simple_total = (
        direct_uav2
        + route_cost(starts["uav_2"], paths["B3"])
        + route_cost(starts["uav_3"], paths["B4"])
    )
    # Under local reassignment, U2's cursor is at the end of B2 before B3.
    simple_total = (
        direct_uav2
        + route_cost(paths["B2"][2], paths["B3"])
        + route_cost(starts["uav_3"], paths["B4"])
    )
    # Under suffix-aware choice, U3 takes B2; U2 and U3 retain B3/B4.
    cds_total = (
        direct_uav3
        + route_cost(starts["uav_2"], paths["B3"])
        + route_cost(paths["B2"][2], paths["B4"])
    )
    nonzero_legs: list[float] = []
    for points in paths.values():
        nonzero_legs.extend(math.dist(left, right) for left, right in zip(points, points[1:]))
    audit = {
        "protocol": "mechanism_stress_v2",
        "status": "frozen_before_execution",
        "case_count": len(rows),
        "waypoint_tolerance": TOLERANCE,
        "minimum_nonzero_intra_bundle_spacing": min(nonzero_legs),
        "minimum_spacing_to_tolerance_ratio": min(nonzero_legs) / TOLERANCE,
        "failure_state": {
            "failed_bundle": "B2",
            "completed_prefix": 2,
            "remaining_B2_waypoints": [paths["B2"][2]],
            "candidate_uav_2": "uav_2",
            "candidate_uav_3": "uav_3",
        },
        "hand_checked_costs": {
            "c2_immediate_uav_2": round(direct_uav2, 9),
            "c3_immediate_uav_3": round(direct_uav3, 9),
            "simple_local_suffix_total": round(simple_total, 9),
            "cds_mr_suffix_total": round(cds_total, 9),
            "relative_reduction_vs_simple": round((simple_total - cds_total) / simple_total, 9),
            "direct_choice_inequality": bool(direct_uav2 < direct_uav3),
            "suffix_choice_inequality": bool(simple_total > cds_total),
        },
        "expected_selection": {"Simple-local-reassignment": "uav_2", "CDS-MR": "uav_3"},
        "unsupported_claims": [
            "population-level superiority",
            "global optimality",
            "lower NPSD",
            "runtime failure avoidance beyond this fixed protocol",
        ],
        "output_sha256": sha256(output),
    }
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    templates = read_jsonl(args.source)
    if len(templates) < 3:
        raise SystemExit("source must contain at least three template tasks")
    specs = [
        ("mechanism_stress_v2_chain", 0, False, False),
        ("mechanism_stress_v2_chain_mirror", 1, True, False),
        ("mechanism_stress_v2_branch", 2, False, True),
    ]
    rows = [
        build_case(templates[source_index], task_id, mirror=mirror, branch=branch, index=index)
        for index, (task_id, source_index, mirror, branch) in enumerate(specs, start=1)
    ]
    write_jsonl(args.output, rows)
    audit = make_audit(rows, args.output)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
