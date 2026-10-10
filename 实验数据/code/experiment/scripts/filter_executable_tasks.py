"""Filter ``uav_task_v1`` JSONL records for the current PyBullet adapter.

The filter is intentionally conservative.  It does not silently turn an
octocopter, weather condition, symbolic destination, or heterogeneous fleet
into a different simulation.  Every rejected task receives one or more
machine-readable reasons in the manifest.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


MODEL_ALIASES = {
    "cf2x": "cf2x",
    "cf2p": "cf2p",
    "racer": "racer",
    "race": "racer",
}

APPROXIMATE_MODEL_ALIASES = {
    "quadcopter": "cf2x",
    "quadrotor": "cf2x",
    "crazyflie": "cf2x",
    "crazyflie_2": "cf2x",
    "crazyflie_2.0": "cf2x",
}

PROJECTION_MODEL_ALIASES = {
    "quadrotor": "cf2x",
}


def is_vec3(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    return all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in value)


def nonempty(value: Any) -> bool:
    return value is not None and value != {} and value != [] and value != ""


def canonical_model(vehicle_type: Any, allow_approximation: bool, projection: bool = False) -> tuple[str | None, bool]:
    name = str(vehicle_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if name in MODEL_ALIASES:
        return MODEL_ALIASES[name], False
    if projection and name in PROJECTION_MODEL_ALIASES:
        return PROJECTION_MODEL_ALIASES[name], True
    if allow_approximation and name in APPROXIMATE_MODEL_ALIASES:
        return APPROXIMATE_MODEL_ALIASES[name], True
    return None, False


def check_record(record: dict[str, Any], allow_approximation: bool, mode: str) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    is_simulation = mode in {"simulation", "projection"}
    is_projection = mode == "projection"
    agents = record.get("agents")
    if not isinstance(record.get("instruction"), str) or not record.get("instruction", "").strip():
        reasons.append("instruction_missing")
    if not isinstance(agents, list) or not agents:
        reasons.append("agents_missing")
        agents = []
    if record.get("unsupported_features") and not is_projection:
        reasons.extend(str(item) for item in record["unsupported_features"])

    info: dict[str, Any] = {"model_names": [], "model_approximation": False}
    models: list[str] = []
    for agent in agents:
        if not isinstance(agent, dict):
            reasons.append("invalid_agent")
            continue
        if is_simulation and not is_vec3(agent.get("start")):
            reasons.append("agent_start_missing_or_invalid")
        if is_simulation:
            model, approximate = canonical_model(agent.get("vehicle_type"), allow_approximation, projection=is_projection)
            if model is None:
                reasons.append("unsupported_drone_model")
            else:
                models.append(model)
                info["model_approximation"] = info["model_approximation"] or approximate
    info["model_names"] = models
    if is_simulation and models and len(set(models)) > 1:
        reasons.append("heterogeneous_drone_models_not_supported_by_current_env")

    goal = record.get("goal") if isinstance(record.get("goal"), dict) else {}
    waypoints = goal.get("waypoints", [])
    if is_simulation:
        if not isinstance(waypoints, list) or not waypoints:
            reasons.append("goal_waypoints_missing")
        elif any(not is_vec3(waypoint) for waypoint in waypoints):
            reasons.append("goal_waypoint_invalid")

    if mode == "simulation":
        environment = record.get("environment") if isinstance(record.get("environment"), dict) else {}
        unsupported_environment = {
            "moving_obstacles": "moving_obstacles_not_supported",
            "traffic": "traffic_not_supported",
            "faults": "fault_injection_not_supported",
            "no_fly_zones": "no_fly_zones_not_supported",
        }
        for field, reason in unsupported_environment.items():
            if nonempty(environment.get(field)):
                reasons.append(reason)
        scenario = environment.get("scenario") if isinstance(environment.get("scenario"), dict) else {}
        if nonempty(scenario.get("weather")):
            reasons.append("weather_not_supported")

    return sorted(set(reasons)), info


def projection_dropped_features(record: dict[str, Any]) -> list[str]:
    """List source features intentionally excluded from waypoint-only runs."""
    environment = record.get("environment") if isinstance(record.get("environment"), dict) else {}
    dropped: list[str] = []
    for field in ("airspace", "scenario", "landing", "controls", "comms", "faults", "traffic", "moving_obstacles"):
        value = environment.get(field)
        if nonempty(value):
            dropped.append(field)
    if nonempty(environment.get("sim")):
        dropped.append("sim_physics_semantics")
    constraints = record.get("constraints")
    if isinstance(constraints, list) and constraints:
        dropped.append("constraints")
    if record.get("unsupported_features"):
        dropped.append("unsupported_features")
    return sorted(set(dropped))


def iter_records(path: Path) -> Iterable[tuple[int, dict[str, Any] | None, str | None]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                yield line_number, None, f"invalid_json:{exc.msg}"
                continue
            if not isinstance(value, dict):
                yield line_number, None, "record_not_object"
                continue
            yield line_number, value, None


def filter_file(
    input_path: Path,
    output_path: Path,
    manifest_path: Path,
    allow_approximation: bool,
    mode: str,
    max_tasks: int | None,
    max_accepted: int | None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    reasons = Counter[str]()
    projection_features = Counter[str]()
    source_counts = Counter[str]()
    accepted_source_counts = Counter[str]()
    accepted = 0
    rejected = 0
    total = 0
    examples: list[dict[str, Any]] = []
    with output_path.open("w", encoding="utf-8") as output:
        for line_number, record, parse_error in iter_records(input_path):
            if max_tasks is not None and total >= max_tasks:
                break
            if max_accepted is not None and accepted >= max_accepted:
                break
            total += 1
            if parse_error is not None:
                rejected += 1
                reasons[parse_error] += 1
                if len(examples) < 20:
                    examples.append({"line": line_number, "task_id": None, "reasons": [parse_error]})
                continue
            assert record is not None
            source = str(record.get("source_dataset"))
            source_counts[source] += 1
            record_reasons, info = check_record(record, allow_approximation, mode)
            if record_reasons:
                rejected += 1
                reasons.update(record_reasons)
                if len(examples) < 20:
                    examples.append({"line": line_number, "task_id": record.get("task_id"), "reasons": record_reasons})
                continue
            accepted += 1
            accepted_source_counts[source] += 1
            if mode == "projection":
                projection_features.update(projection_dropped_features(record))
            derived = copy.deepcopy(record)
            metadata = derived.setdefault("metadata", {})
            metadata["execution_profile"] = (
                "gym_velocity_aviary_v1"
                if mode == "simulation"
                else "gym_waypoint_projection_v1"
                if mode == "projection"
                else "agent4drone_planning_v1"
            )
            metadata["filter_version"] = "gym_executable_filter_20260823"
            metadata["model_approximation"] = info["model_approximation"]
            metadata["gym_drone_model"] = info["model_names"][0] if info["model_names"] else None
            if mode == "projection":
                metadata["projection_semantics"] = "waypoint_only_under_cf2x"
                metadata["projection_source_model"] = [
                    str(agent.get("vehicle_type")) for agent in derived.get("agents", []) if isinstance(agent, dict)
                ]
                metadata["projection_target_model"] = "cf2x"
                metadata["projection_dropped_features"] = projection_dropped_features(derived)
            output.write(json.dumps(derived, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {
        "filter_version": "gym_executable_filter_20260823",
        "input": str(input_path),
        "output": str(output_path),
        "mode": mode,
        "allow_model_approximation": allow_approximation,
        "total_records": total,
        "accepted_records": accepted,
        "rejected_records": rejected,
        "acceptance_rate": accepted / total if total else 0.0,
        "source_counts": dict(source_counts),
        "accepted_source_counts": dict(accepted_source_counts),
        "rejection_reason_counts": dict(reasons),
        "projection_dropped_feature_counts": dict(projection_features),
        "rejection_examples": examples,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=("simulation", "projection", "planning"), default="simulation")
    parser.add_argument("--allow-model-approximation", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--max-accepted", type=int, default=None, help="Stop after this many accepted records")
    args = parser.parse_args()
    manifest = filter_file(
        args.input.expanduser().resolve(),
        args.output.expanduser().resolve(),
        args.manifest.expanduser().resolve(),
        args.allow_model_approximation,
        args.mode,
        args.max_tasks,
        args.max_accepted,
    )
    print(
        f"total={manifest['total_records']} accepted={manifest['accepted_records']} "
        f"rejected={manifest['rejected_records']} acceptance_rate={manifest['acceptance_rate']:.4f}"
    )
    print(f"wrote manifest to {args.manifest.expanduser().resolve()}")


if __name__ == "__main__":
    main()
