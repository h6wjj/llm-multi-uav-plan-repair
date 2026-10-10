"""Build preregistered route-cost outcome stress cases.

All commanded legs stay within the frozen bridge tolerance.  The immediate B2
leg is slightly shorter for U2 than U3, while the complete suffix is shorter
when U3 takes B2 and the downstream owners remain near their own work.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_case(template: dict[str, Any], task_id: str, *, mirror: bool, branch: bool, index: int) -> dict[str, Any]:
    task = copy.deepcopy(template)
    sx = -1.0 if mirror else 1.0
    starts = {
        "uav_0": [sx * 0.0, 0.5, 1.0],
        "uav_1": [sx * 0.2, 0.2, 1.0],
        "uav_2": [sx * 0.0, 0.0, 1.0],
        "uav_3": [sx * 0.41, 0.0, 1.0],
    }
    paths = {
        "B1": [[sx * 0.0, 0.5, 1.0], [sx * 0.1, 0.5, 1.0]],
        "B2": [[sx * 0.2, 0.2, 1.0], [sx * 0.2, 0.1, 1.0], [sx * 0.2, 0.0, 1.0]],
        "B3": [[sx * 0.0, 0.0, 1.0], [sx * -0.02, 0.0, 1.0]],
        "B4": [[sx * 0.41, 0.0, 1.0], [sx * 0.22, 0.0, 1.0]],
    }
    task["task_id"] = task_id
    task["instruction"] = "Deterministic suffix route-cost outcome stress test."
    metadata = task.setdefault("metadata", {})
    metadata.update({
        "source_name": "route_cost_outcome_stress_v1",
        "source_task_id": task_id,
        "run_seed_index": index,
        "run_seed": 9300 + index,
        "mechanism_stress": "suffix_route_cost_branch" if branch else "suffix_route_cost_competition",
        "population_claim": False,
        "preregistered": True,
    })
    for agent in task["agents"]:
        aid = str(agent["agent_id"])
        agent["start"] = starts[aid]
        agent["extension_start_provenance"] = "preregistered_route_cost_outcome_geometry_v1"
    mission = task["contract"]["mission"]
    bundles = sorted(mission["task_bundles"].values(), key=lambda bundle: int(bundle["order"]))
    required: list[list[float]] = []
    cursor = 0
    for bundle in bundles:
        bid = str(bundle["bundle_id"])
        bundle["waypoints"] = paths[bid]
        bundle["waypoint_indices"] = list(range(cursor, cursor + len(paths[bid])))
        bundle["deadline_s"] = 600.0
        bundle["repair_policy"] = "route_cost_outcome_stress_v1"
        required.extend(paths[bid])
        cursor += len(paths[bid])
    mission["required_waypoint_sequence"] = required
    mission["waypoint_tolerance"] = 0.25
    mission["bundle_dependencies"] = (
        {"B2": ["B1"], "B3": ["B1"], "B4": ["B2", "B3"]}
        if branch
        else {"B2": ["B1"], "B3": ["B2"], "B4": ["B3"]}
    )
    task["goal"]["waypoints"] = required
    task["contract"]["resources"].update({"max_total_steps": 900, "base_step_budget": 900, "repair_step_budget": 300, "time_budget_s": 600.0})
    failure = task["contract"]["failure_policy"]
    failure.update({"bundle_id": "B2", "drone_id": "uav_1", "after_completed_waypoints": 2})
    scenario = task.setdefault("scenario", {})
    scenario["failure_model"] = copy.deepcopy(failure)
    return task


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    templates = {str(row["task_id"]): row for row in read_jsonl(args.source)}
    spec = (
        ("mechanism_stress_S1_case_a", "route_cost_outcome_a", False, False),
        ("mechanism_stress_S1_case_b_mirror", "route_cost_outcome_b_mirror", True, False),
        ("mechanism_stress_S2_branch", "route_cost_outcome_branch", False, True),
    )
    rows = [build_case(templates[source], task_id, mirror=mirror, branch=branch, index=index) for index, (source, task_id, mirror, branch) in enumerate(spec, start=1)]
    write_jsonl(args.output, rows)
    audit = {
        "protocol": "route_cost_outcome_stress_v1",
        "case_count": len(rows),
        "expected_direct_B2_cost": {"uav_2": 0.20, "uav_3": 0.21},
        "expected_suffix_cost": {"Simple-local-reassignment": 0.61, "CDS-MR": 0.25},
        "expected_relative_reduction": (0.61 - 0.25) / 0.61,
        "output_sha256": sha256(args.output),
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
