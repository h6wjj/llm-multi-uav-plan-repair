"""Build the exploratory physical-dynamics follow-up to mechanism stress v2.

The v2 geometry is scaled uniformly in the horizontal plane.  The paired
selection problem is unchanged, while a failed drone parked at B2's second
waypoint is separated from the takeover endpoint by more than the simulated
CF2X collision diameter (0.12 m).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from validate_mechanism_stress_v2 import frozen_geometry, read_jsonl


def scaled(point: list[float], factor: float) -> list[float]:
    return [round(float(point[0]) * factor, 9), round(float(point[1]) * factor, 9), float(point[2])]


def build(task: dict[str, Any], factor: float) -> dict[str, Any]:
    result = json.loads(json.dumps(task))
    result["task_id"] = str(task["task_id"]).replace("mechanism_stress_v2", "mechanism_dynamic_scale_v3")
    for agent in result["agents"]:
        agent["start"] = scaled(agent["start"], factor)
    mission = result["contract"]["mission"]
    for bundle in mission["task_bundles"].values():
        bundle["waypoints"] = [scaled(point, factor) for point in bundle["waypoints"]]
        bundle["deadline_s"] = 600.0
    mission["required_waypoint_sequence"] = [
        scaled(point, factor) for point in mission["required_waypoint_sequence"]
    ]
    mission["waypoint_tolerance"] = round(float(mission["waypoint_tolerance"]) * factor, 9)
    result["goal"]["waypoints"] = [scaled(point, factor) for point in result["goal"]["waypoints"]]
    result["goal"]["mission"]["time_budget_s"] = 600.0
    result["environment"]["sim"]["duration_steps"] = 12000
    result["environment"]["sim"]["policy_frequency"] = 48
    resources = result["contract"]["resources"]
    resources.update({
        "max_total_steps": 12000,
        "base_step_budget": 12000,
        "repair_step_budget": 6000,
        "time_budget_s": 600.0,
        "motion_speed_scale": 0.3,
        "motion_approach_gain": 0.7,
        "freeze_idle_drones": False,
        "kinematic_mechanism_audit": False,
    })
    resources.pop("kinematic_step_distance", None)
    result["contract"]["contract_version"] = "mission_contract_mechanism_dynamic_scale_v3"
    metadata = result["metadata"]
    metadata.update({
        "source_name": "mechanism_dynamic_scale_v3",
        "source_task_id": result["task_id"],
        "condition": "recoverable_failure",
        "preregistered": False,
        "dynamic_scale_factor_xy": factor,
        "execution_mode": "VelocityAviary_motor_dynamics_active_uav",
        "waypoint_tolerance": mission["waypoint_tolerance"],
    })
    scenario = result["scenario"]
    scenario.update({
        "scenario_version": "mechanism_dynamic_scale_v3",
        "scenario_id": result["task_id"],
        "source_task_id": result["task_id"],
        "evaluation_mode": "frozen_exploratory_dynamic_scale",
        "dynamic_scale_factor_xy": factor,
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=5.0)
    args = parser.parse_args()
    if args.scale <= 1.0:
        raise SystemExit("scale must be greater than 1")
    rows = [build(task, args.scale) for task in read_jsonl(args.source)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    geometry = frozen_geometry(rows)
    parked_clearances = []
    for row in rows:
        bundles = {bundle["bundle_id"]: bundle for bundle in row["contract"]["mission"]["task_bundles"].values()}
        parked = bundles["B2"]["waypoints"][1]
        takeover = bundles["B2"]["waypoints"][2]
        parked_clearances.append(((parked[0]-takeover[0])**2 + (parked[1]-takeover[1])**2)**0.5)
    audit = {
        "protocol": "mechanism_dynamic_scale_v3",
        "design_status": "exploratory_follow_up_frozen_before_v3_execution",
        "scale_factor_xy": args.scale,
        "motor_dynamics": True,
        "active_position_projection": False,
        "idle_drones_held_at_command_start": False,
        "control_frequency_hz": 48,
        "motion_speed_scale": 0.3,
        "minimum_parked_failed_uav_to_takeover_clearance_m": min(parked_clearances),
        "cf2x_collision_diameter_m": 0.12,
        "frozen_geometry": geometry,
        "task_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
