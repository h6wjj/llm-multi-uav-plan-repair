"""Freeze equal-suffix and local-sufficient controls for the mechanism test.

The positive case was run separately.  These controls are defined only from
the pre-execution geometry; no method outcome is used to select or label them.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def cost(start: list[float], points: list[list[float]]) -> float:
    return math.dist(start, points[0]) + sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def make_case(source: dict[str, Any], condition: str) -> dict[str, Any]:
    task = copy.deepcopy(source)
    task_id = str(source["task_id"]).replace("mechanism_dynamic_scale_v3", f"mechanism_boundary_v1_{condition}")
    task["task_id"] = task_id
    mission = task["contract"]["mission"]
    bundles = {str(value["bundle_id"]): value for value in mission["task_bundles"].values()}
    endpoint = [float(value) for value in bundles["B2"]["waypoints"][-1]]
    u3 = next(agent for agent in task["agents"] if agent["agent_id"] == "uav_3")["start"]
    # U2 is 1.25 m from B2, U3 is 1.50 m away.  B3 begins 0.50 m
    # beyond B2.  Placing B4 one-third of the way to U3 gives exact
    # equal suffix costs; placing it at four-fifths makes local U2 optimal.
    fraction = 1.0 / 3.0 if condition == "tie" else 0.8
    b4_start = [
        round(endpoint[0] + fraction * (float(u3[0]) - endpoint[0]), 9),
        round(endpoint[1] + fraction * (float(u3[1]) - endpoint[1]), 9),
        endpoint[2],
    ]
    bundles["B3"]["waypoints"] = [
        [endpoint[0], round(endpoint[1] + 0.5, 9), endpoint[2]],
        [endpoint[0], round(endpoint[1] + 1.0, 9), endpoint[2]],
    ]
    bundles["B4"]["waypoints"] = [
        b4_start,
        [b4_start[0], round(b4_start[1] + 0.5, 9), b4_start[2]],
    ]
    ordered = [bundles[key] for key in ("B1", "B2", "B3", "B4")]
    points = [point for bundle in ordered for point in bundle["waypoints"]]
    mission["required_waypoint_sequence"] = points
    task["goal"]["waypoints"] = points
    task["metadata"].update({
        "source_name": "mechanism_boundary_v1",
        "source_task_id": task_id,
        "boundary_condition": condition,
        "preregistered": False,
        "case_design": "geometry_fixed_before_boundary_execution",
    })
    task["scenario"].update({
        "scenario_version": "mechanism_boundary_v1",
        "scenario_id": task_id,
        "source_task_id": task_id,
        "boundary_condition": condition,
        "evaluation_mode": "exploratory_boundary_control",
    })
    return task


def geometry(task: dict[str, Any]) -> dict[str, Any]:
    agents = {str(value["agent_id"]): value["start"] for value in task["agents"]}
    bundles = {str(value["bundle_id"]): value["waypoints"] for value in task["contract"]["mission"]["task_bundles"].values()}
    endpoint = bundles["B2"][-1]
    c2 = cost(agents["uav_2"], [endpoint])
    c3 = cost(agents["uav_3"], [endpoint])
    h2 = cost(endpoint, bundles["B3"]) + cost(agents["uav_3"], bundles["B4"])
    h3 = cost(agents["uav_2"], bundles["B3"]) + cost(endpoint, bundles["B4"])
    tolerance = float(task["contract"]["mission"]["waypoint_tolerance"])
    legs = [math.dist(a, b) for path in bundles.values() for a, b in zip(path, path[1:])]
    return {
        "task_id": task["task_id"],
        "condition": task["metadata"]["boundary_condition"],
        "c2": round(c2, 9), "c3": round(c3, 9),
        "h2": round(h2, 9), "h3": round(h3, 9),
        "simple_suffix_cost": round(c2 + h2, 9),
        "cds_alternative_suffix_cost": round(c3 + h3, 9),
        "simple_minus_alternative": round(c2 + h2 - c3 - h3, 9),
        "minimum_leg_to_tolerance_ratio": round(min(legs) / tolerance, 9),
        "initial_decision_local_uav2": c2 < c3,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    sources = rows(args.source)
    if len(sources) != 3:
        raise SystemExit("expected exactly the three frozen dynamic cases")
    tasks = [make_case(source, condition) for condition in ("tie", "local_sufficient") for source in sources]
    cases = [geometry(task) for task in tasks]
    checks = {
        "six_cases": len(tasks) == 6,
        "all_local_direct_choices_uav2": all(case["initial_decision_local_uav2"] for case in cases),
        "all_legs_above_4x_tolerance": all(case["minimum_leg_to_tolerance_ratio"] > 4 for case in cases),
        "tie_suffix_costs_equal": all(abs(case["simple_minus_alternative"]) < 1e-8 for case in cases if case["condition"] == "tie"),
        "local_sufficient_better_than_uav3": all(case["simple_minus_alternative"] < -0.5 for case in cases if case["condition"] == "local_sufficient"),
        "physical_dynamics_enabled": all(not task["contract"]["resources"].get("kinematic_mechanism_audit") for task in tasks),
    }
    if not all(checks.values()):
        raise SystemExit("boundary geometry invalid: " + json.dumps(checks))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n" for task in tasks), encoding="utf-8")
    audit = {
        "protocol": "mechanism_boundary_v1",
        "design_status": "frozen_before_execution",
        "case_count": len(tasks),
        "checks": checks,
        "cases": cases,
        "task_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "cases": cases, "task_sha256": audit["task_sha256"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
