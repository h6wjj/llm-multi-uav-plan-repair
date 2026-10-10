"""Project UAVBench waypoint geometry into the compact Gym workspace.

UAVBench contains missions expressed in world-scale coordinates (tens to
thousands of metres).  ``VelocityAviary`` is a small quadrotor workspace with
an episode budget of a few hundred control steps, so feeding those coordinates
directly would measure reachability/scale mismatch instead of planning.

This projection keeps the waypoint order and relative directions while making
the geometry executable: the primary start is translated to ``(0, 0, 1)``;
horizontal and vertical offsets are scaled independently to bounded budgets.
The original coordinates and transform are retained in metadata for audit.
It is a projection benchmark, not a claim that the simulator reproduces the
original UAVBench world scale.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any


def xyz(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"expected xyz vector, got {value!r}")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"non-finite xyz vector: {value!r}")
    return result


def project_record(record: dict[str, Any], horizontal_budget: float, vertical_budget: float) -> dict[str, Any]:
    agents = record.get("agents")
    waypoints = record.get("goal", {}).get("waypoints", [])
    if not isinstance(agents, list) or not agents or not isinstance(waypoints, list) or not waypoints:
        raise ValueError(f"task {record.get('task_id')!r} has no agents/waypoints")

    source_primary_start = xyz(agents[0].get("start"))
    source_waypoints = [xyz(value) for value in waypoints]
    source_agent_starts = [xyz(agent.get("start")) for agent in agents]
    horizontal_path = 0.0
    previous = source_primary_start
    for target in source_waypoints:
        horizontal_path += math.hypot(target[0] - previous[0], target[1] - previous[1])
        previous = target
    vertical_span = max(
        [abs(target[2] - source_primary_start[2]) for target in source_waypoints]
        + [abs(start[2] - source_primary_start[2]) for start in source_agent_starts]
        + [0.0]
    )
    scale_xy = min(1.0, horizontal_budget / max(horizontal_path, 1e-9))
    scale_z = 0.0 if vertical_budget == 0 else min(1.0, vertical_budget / max(vertical_span, 1e-9))

    def transform(point: list[float]) -> list[float]:
        return [
            (point[0] - source_primary_start[0]) * scale_xy,
            (point[1] - source_primary_start[1]) * scale_xy,
            1.0 + (point[2] - source_primary_start[2]) * scale_z,
        ]

    derived = copy.deepcopy(record)
    for agent, source_start in zip(derived["agents"], source_agent_starts):
        agent["start"] = transform(source_start)
    derived["goal"]["waypoints"] = [transform(target) for target in source_waypoints]
    if isinstance(derived.get("reference_plan"), list):
        for item, target in zip(derived["reference_plan"], source_waypoints):
            if isinstance(item, dict):
                item["position"] = transform(target)

    metadata = derived.setdefault("metadata", {})
    metadata.update(
        {
            "projection_coordinate_transform_version": "gym_local_affine_v1",
            "projection_coordinate_semantics": "translate_primary_start_to_0_0_1_and_scale_offsets",
            "projection_source_primary_start": source_primary_start,
            "projection_source_agent_starts": source_agent_starts,
            "projection_source_waypoints": source_waypoints,
            "projection_target_primary_start": [0.0, 0.0, 1.0],
            "projection_scale_xy": scale_xy,
            "projection_scale_z": scale_z,
            "projection_horizontal_budget_m": horizontal_budget,
            "projection_vertical_budget_m": vertical_budget,
        }
    )
    return derived


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizontal-budget", type=float, default=5.5)
    parser.add_argument("--vertical-budget", type=float, default=0.8)
    args = parser.parse_args()
    if args.horizontal_budget <= 0 or args.vertical_budget < 0:
        raise SystemExit("horizontal budget must be positive and vertical budget non-negative")
    count = 0
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    with args.input.expanduser().resolve().open("r", encoding="utf-8-sig") as source, args.output.expanduser().resolve().open("w", encoding="utf-8") as target:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: record is not an object")
            target.write(json.dumps(project_record(record, args.horizontal_budget, args.vertical_budget), ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    print(f"projected={count} output={args.output.expanduser().resolve()}")


if __name__ == "__main__":
    main()
