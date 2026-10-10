"""Adapter from an executable ``uav_task_v1`` record to gym-pybullet-drones.

The current upstream environment accepts one ``DroneModel`` for all drones,
so this adapter refuses mixed vehicle models.  It uses ``VelocityAviary`` as a
high-level, headless control environment; converting an LLM tool call into a
velocity action is a separate execution/evaluation layer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from filter_executable_tasks import check_record


class AdapterError(ValueError):
    """Raised when a task cannot be represented by the current simulator."""


def _load_task(path: Path, task_id: str | None) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict) and (task_id is None or record.get("task_id") == task_id):
                return record
    raise AdapterError(f"no task found in {path} for task_id={task_id!r}")


def task_to_gym_config(task: dict[str, Any], allow_model_approximation: bool = False) -> dict[str, Any]:
    metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
    projection = metadata.get("execution_profile") == "gym_waypoint_projection_v1"
    reasons, info = check_record(
        task,
        allow_model_approximation or projection,
        mode="projection" if projection else "simulation",
    )
    if reasons:
        raise AdapterError("task is not executable: " + ", ".join(reasons))
    agents = task["agents"]
    environment = task.get("environment", {}) if isinstance(task.get("environment"), dict) else {}
    sim = environment.get("sim", {}) if isinstance(environment.get("sim"), dict) else {}
    policy_frequency = sim.get("policy_frequency", 30)
    try:
        ctrl_freq = max(1, int(policy_frequency))
    except (TypeError, ValueError):
        ctrl_freq = 30
    if 240 % ctrl_freq != 0:
        ctrl_freq = 30
    try:
        max_steps = max(1, int(sim.get("duration_steps", 900)))
    except (TypeError, ValueError):
        max_steps = 900
    return {
        "env_class": "VelocityAviary",
        "drone_model": info["model_names"][0],
        "num_drones": len(agents),
        "initial_xyzs": [agent["start"] for agent in agents],
        "initial_rpys": [[0.0, 0.0, 0.0] for _ in agents],
        "physics": "pyb",
        "pyb_freq": 240,
        "ctrl_freq": ctrl_freq,
        "gui": False,
        "record": False,
        "obstacles": False,
        "waypoints": task["goal"]["waypoints"],
        "goal_tolerance_m": 0.25,
        "max_steps": max_steps,
        "model_approximation": info["model_approximation"],
        "projection_mode": projection,
        "projection_dropped_features": metadata.get("projection_dropped_features", []),
        "task_id": task.get("task_id"),
    }


def make_env(task: dict[str, Any], gui: bool = False, record: bool = False, output_folder: str = "results", allow_model_approximation: bool = False):
    """Instantiate the upstream VelocityAviary lazily inside the VM."""
    config = task_to_gym_config(task, allow_model_approximation)
    try:
        import numpy as np
        from gym_pybullet_drones.envs.VelocityAviary import VelocityAviary
        from gym_pybullet_drones.utils.enums import DroneModel, Physics
    except ImportError as exc:
        raise AdapterError("gym-pybullet-drones is not importable in this environment") from exc
    config["gui"] = gui
    config["record"] = record
    env = VelocityAviary(
        drone_model=DroneModel(config["drone_model"]),
        num_drones=config["num_drones"],
        initial_xyzs=np.asarray(config["initial_xyzs"], dtype=np.float32),
        initial_rpys=np.asarray(config["initial_rpys"], dtype=np.float32),
        physics=Physics(config["physics"]),
        pyb_freq=config["pyb_freq"],
        ctrl_freq=config["ctrl_freq"],
        gui=config["gui"],
        record=config["record"],
        obstacles=config["obstacles"],
        output_folder=output_folder,
    )
    return env, config


def run_smoke(task: dict[str, Any], seed: int, allow_model_approximation: bool = False) -> dict[str, Any]:
    """Reset the env and execute one zero-velocity step to test the bridge."""
    import numpy as np

    env, config = make_env(task, gui=False, record=False, allow_model_approximation=allow_model_approximation)
    try:
        observation, reset_info = env.reset(seed=seed)
        action = np.zeros((config["num_drones"], 4), dtype=np.float32)
        next_observation, reward, terminated, truncated, step_info = env.step(action)
        return {
            "status": "adapter_smoke_pass",
            "task_id": task.get("task_id"),
            "env_class": config["env_class"],
            "observation_shape": list(observation.shape),
            "next_observation_shape": list(next_observation.shape),
            "action_shape": list(action.shape),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "max_steps": config["max_steps"],
            "model_approximation": config["model_approximation"],
            "projection_mode": config["projection_mode"],
            "projection_dropped_features": config["projection_dropped_features"],
            "reset_info": reset_info,
            "step_info": step_info,
            "reward": reward,
        }
    finally:
        env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Executable JSONL produced by filter_executable_tasks.py")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--allow-model-approximation", action="store_true")
    args = parser.parse_args()
    task = _load_task(args.input.expanduser().resolve(), args.task_id)
    result = run_smoke(task, args.seed, args.allow_model_approximation)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
