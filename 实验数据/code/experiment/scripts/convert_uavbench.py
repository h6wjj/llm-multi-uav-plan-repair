"""Convert UAVBench structured scenarios into ``uav_task_v1`` JSONL.

The converter keeps the original scenario addressable through ``source_file``
and ``source_index`` and only derives fields that are explicitly present in a
scenario.  A waypoint list is retained as a *canonical* reference plan; it is
not claimed to be an expert or human demonstration.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

from profile_common import is_ignored
from task_common import read_json, slug, write_jsonl


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return as_dict(value)


def as_waypoints(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value


def sensor_capabilities(sensor_value: Any) -> list[str]:
    if isinstance(sensor_value, dict):
        return [str(key) for key, enabled in sensor_value.items() if enabled]
    if isinstance(sensor_value, list):
        return [str(item) for item in sensor_value]
    return []


def spawn_positions(spawn: dict[str, Any]) -> list[Any]:
    xyz = spawn.get("xyz")
    if isinstance(xyz, list) and xyz and isinstance(xyz[0], (list, tuple)):
        return list(xyz)
    return [xyz] if xyz is not None else []


def uav_specs(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [as_dict(item) for item in raw if isinstance(item, dict)]
    return [as_dict(raw)] if isinstance(raw, dict) else [{}]


def make_agents(scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    uav_entries = uav_specs(scenario.get("uav"))
    swarm = as_dict(scenario.get("swarm"))
    flags: list[str] = []
    raw_count = swarm.get("size", swarm.get("count", swarm.get("num_drones")))
    try:
        count = int(raw_count) if raw_count is not None else len(uav_entries)
    except (TypeError, ValueError):
        count = len(uav_entries)
    if count < 1:
        count = 1
    if not bool(swarm.get("enabled", False)) and raw_count is None:
        count = max(1, len(uav_entries))
    if bool(swarm.get("enabled", False)) and raw_count is None and len(uav_entries) == 1:
        flags.append("swarm_agent_count_missing")

    positions = spawn_positions(as_dict(scenario.get("spawn")))
    agents: list[dict[str, Any]] = []
    for index in range(count):
        spec = uav_entries[index] if index < len(uav_entries) else uav_entries[0]
        start = positions[index] if index < len(positions) else (positions[0] if index == 0 and positions else None)
        if index > 0 and len(positions) < count:
            flags.append("swarm_initial_positions_missing")
        agents.append(
            {
                "agent_id": f"uav_{index}",
                "vehicle_type": spec.get("type") or spec.get("model"),
                "role": "primary" if index == 0 else "swarm_member",
                "capabilities": sensor_capabilities(spec.get("sensors")),
                "start": start,
            }
        )
    return agents, sorted(set(flags))


def constraints_for(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    airspace = as_dict(scenario.get("airspace"))
    daa = as_dict(scenario.get("daa"))
    metrics = as_dict(scenario.get("metrics"))
    constraints: list[dict[str, Any]] = []
    if "altitude_agl_min_m" in airspace or "altitude_agl_max_m" in airspace:
        constraints.append(
            {
                "name": "altitude_agl",
                "min_m": airspace.get("altitude_agl_min_m"),
                "max_m": airspace.get("altitude_agl_max_m"),
                "source": "airspace",
            }
        )
    if daa:
        constraints.append({"name": "detect_and_avoid", "parameters": daa, "source": "daa"})
    if "reserve_fraction" in metrics:
        constraints.append(
            {"name": "battery_reserve_fraction", "value": metrics["reserve_fraction"], "source": "metrics"}
        )
    if airspace.get("no_fly_zones"):
        constraints.append({"name": "no_fly_zones", "zones": airspace["no_fly_zones"], "source": "airspace"})
    return constraints


def reference_plan(mission: dict[str, Any]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for order, waypoint in enumerate(as_waypoints(mission.get("waypoints")), start=1):
        plan.append(
            {
                "order": order,
                "action": "MOVE_TO",
                "position": waypoint,
                "source": "mission_waypoints",
            }
        )
    return plan


def convert_file(path: Path, root: Path, index: int) -> dict[str, Any] | None:
    value = read_json(path)
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or path.stem)
    mission = as_dict(value.get("mission"))
    environment_data = as_dict(value.get("environment"))
    airspace = as_dict(value.get("airspace"))
    agents, flags = make_agents(value)
    description = as_dict(value.get("description"))
    instruction = description.get("text")
    instruction_source = "description.text" if isinstance(instruction, str) and instruction.strip() else "canonical_name"
    if not isinstance(instruction, str) or not instruction.strip():
        instruction = f"Execute mission: {name}"
    relative = str(path.relative_to(root)).replace("\\", "/")
    task_id = f"uavbench_{slug(name)}_{index:06d}"
    source_keys = sorted(str(key) for key in value.keys())
    unsupported = list(flags)
    if value.get("faults"):
        unsupported.append("fault_injection_requires_adapter")
    if value.get("moving_obstacles"):
        unsupported.append("moving_obstacles_requires_adapter")
    return {
        "schema_version": "uav_task_v1",
        "task_id": task_id,
        "source_dataset": "uavbench",
        "source_file": relative,
        "source_index": index,
        "split": "unassigned",
        "instruction": instruction,
        "agents": agents,
        "environment": {
            "sim": value.get("sim", {}),
            "airspace": airspace,
            "scenario": environment_data,
            "spawn": value.get("spawn", {}),
            "landing": value.get("landing", {}),
            "controls": value.get("controls", {}),
            "comms": value.get("comms", {}),
            "faults": value.get("faults", {}),
            "traffic": value.get("traffic", []),
            "moving_obstacles": value.get("moving_obstacles", []),
            "no_fly_zones": airspace.get("no_fly_zones", []),
            "weather": environment_data.get("weather"),
        },
        "goal": {
            "type": mission.get("type"),
            "description": mission.get("description") or name,
            "waypoints": as_waypoints(mission.get("waypoints")),
            "targets": mission.get("targets", []),
            "mission": mission,
        },
        "constraints": constraints_for(value),
        "reference_plan": reference_plan(mission),
        "unsupported_features": sorted(set(unsupported)),
        "metadata": {
            "source_dataset": "uavbench",
            "transform_version": "uavbench_to_uav_task_v1_20260823",
            "modality": "structured_scenario",
            "instruction_source": instruction_source,
            "source_name": name,
            "benchmark_tags": value.get("benchmark_tags", []),
            "label": value.get("label", {}),
            "source_keys": source_keys,
            "reference_plan_semantics": "canonical_waypoints_not_expert_plan",
            "source_feature_flags": {
                "faults": bool(value.get("faults")),
                "moving_obstacles": bool(value.get("moving_obstacles")),
                "swarm": bool(as_dict(value.get("swarm")).get("enabled", False)),
                "required": bool(value.get("required")),
                "autofix": bool(value.get("_autofix")),
            },
        },
    }


def convert(root: Path, max_tasks: int | None = None) -> Iterable[dict[str, Any]]:
    index = 0
    for path in sorted(root.rglob("*.json")):
        if is_ignored(path):
            continue
        record = convert_file(path, root, index)
        if record is None:
            continue
        yield record
        index += 1
        if max_tasks is not None and index >= max_tasks:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("~/uav_exp/data/uavbench/extracted").expanduser())
    parser.add_argument("--output", type=Path, default=Path("~/uav_exp/data/processed/v1/uavbench_tasks.jsonl").expanduser())
    parser.add_argument("--max-tasks", type=int, default=None, help="Optional smoke-test limit")
    args = parser.parse_args()
    count = write_jsonl(convert(args.root.expanduser().resolve(), args.max_tasks), args.output.expanduser().resolve())
    print(f"wrote {count} tasks to {args.output.expanduser().resolve()}")


if __name__ == "__main__":
    main()
