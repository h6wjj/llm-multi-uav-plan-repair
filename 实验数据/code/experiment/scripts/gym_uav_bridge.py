"""Minimal MultiUAV-Plat-compatible HTTP bridge backed by gym-pybullet-drones.

Agent4Drone talks to a REST API on port 8000.  This bridge implements the
read/command endpoints used by Agent4Drone and translates high-level movement
commands into closed-loop velocity actions for ``VelocityAviary``.  It is
deliberately a single-task bridge: set ``GYM_TASK_FILE`` and ``GYM_TASK_ID``
before starting it, then restart the Agent4Drone service for that task.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Request

from gym_task_adapter import make_env


def _read_task(path: Path, task_id: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict) and record.get("task_id") == task_id:
                return record
    raise FileNotFoundError(f"task_id={task_id!r} not found in {path}")


def _xyz(value: Any, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        try:
            return [float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0))]
        except (TypeError, ValueError):
            pass
    return list(default)


class GymBridge:
    def __init__(self, task: dict[str, Any], output_folder: str = "results/gym_bridge") -> None:
        self.task = task
        self.episode_wall_start = time.perf_counter()
        self.lock = threading.RLock()
        self.command_count = 0
        self.total_steps = 0
        self.command_history: list[dict[str, Any]] = []
        self.completed_waypoints: dict[str, int] = {}
        self.completed_waypoints_by_bundle: dict[str, int] = {}
        self.bundle_completion_step: dict[str, int] = {}
        self.bundle_assignment: dict[str, str] = {}
        self.role_by_drone: dict[str, str] = {}
        self.capabilities_by_drone: dict[str, list[str]] = {}
        self.contract = task.get("contract", {}) if isinstance(task.get("contract"), dict) else {}
        scenario = task.get("scenario", {}) if isinstance(task.get("scenario"), dict) else {}
        self.failure_profile = (
            scenario.get("failure_model")
            if isinstance(scenario.get("failure_model"), dict)
            else self.contract.get("failure_policy", {})
        )
        mission_contract = self.contract.get("mission", {})
        self.task_bundles: dict[str, dict[str, Any]] = {}
        if isinstance(mission_contract, dict) and isinstance(mission_contract.get("task_bundles"), dict):
            self.task_bundles = {
                str(drone_id): bundle
                for drone_id, bundle in mission_contract["task_bundles"].items()
                if isinstance(bundle, dict)
            }
            for owner_id, bundle in self.task_bundles.items():
                bundle_id = str(bundle.get("bundle_id") or owner_id)
                self.completed_waypoints_by_bundle[bundle_id] = 0
                self.bundle_assignment[bundle_id] = owner_id
        self.failure_events: list[dict[str, Any]] = []
        self.first_failure: dict[str, Any] | None = None
        self.unavailable_drones: set[str] = set()
        self.repair_window_enabled = False
        self.repair_window_activation: dict[str, Any] | None = None
        self.home: dict[str, list[float]] = {}
        self.agent_index: dict[str, int] = {}
        for index, agent in enumerate(task.get("agents", [])):
            drone_id = str(agent.get("agent_id") or f"uav_{index}")
            self.agent_index[drone_id] = index
            self.home[drone_id] = _xyz(agent.get("start"))
            self.role_by_drone[drone_id] = str(agent.get("role") or "unspecified")
            self.capabilities_by_drone[drone_id] = sorted(
                str(item) for item in agent.get("capabilities", [])
            )
            self.completed_waypoints[drone_id] = 0
        if not self.agent_index:
            raise ValueError("task has no agents")
        # Bundle-local launch points are part of the extension contract.  A
        # waypoint coinciding with the launch state is already satisfied even
        # when an agent reaches the next waypoint through a single-point
        # navigate_to command rather than a full move_along_path call.
        try:
            tolerance = float(
                self.contract.get("mission", {}).get("waypoint_tolerance", 0.25)
            )
        except (TypeError, ValueError):
            tolerance = 0.25
        for owner_id, bundle in self.task_bundles.items():
            points = bundle.get("waypoints") if isinstance(bundle, dict) else None
            if not isinstance(points, list) or not points:
                continue
            first = _xyz(points[0])
            home = self.home.get(owner_id)
            if first is not None and home is not None and math.dist(first, home) <= tolerance:
                bundle_id = str(bundle.get("bundle_id") or owner_id)
                self.completed_waypoints_by_bundle[bundle_id] = 1
                if len(points) == 1:
                    self.bundle_completion_step[bundle_id] = 0
        self.initial_completed_waypoints_by_bundle = dict(self.completed_waypoints_by_bundle)
        self.env, self.config = make_env(task, gui=False, record=False, output_folder=output_folder)
        seed = int(task.get("metadata", {}).get("episode_seed", 0))
        self.observation, _ = self.env.reset(seed=seed)

    def close(self) -> None:
        with self.lock:
            self.env.close()

    def _index(self, drone_id: str | None) -> int:
        if drone_id is None:
            return 0
        if drone_id not in self.agent_index:
            raise HTTPException(status_code=404, detail=f"unknown drone_id={drone_id}")
        return self.agent_index[drone_id]

    def _record_failure(
        self,
        reason: str,
        command: str,
        drone_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "reason": reason,
            "command": command,
            "drone_id": drone_id,
            "step": self.total_steps,
            "command_index": self.command_count,
            "wall_time_seconds": round(time.perf_counter() - self.episode_wall_start, 6),
            "details": details or {},
            "state": {
                "drones": self.drones(),
                "completed_waypoints_by_bundle": dict(self.completed_waypoints_by_bundle),
                "bundle_assignment": dict(self.bundle_assignment),
            },
        }
        self.failure_events.append(event)
        if self.first_failure is None:
            self.first_failure = event
        return event

    def _failure_result(
        self,
        reason: str,
        command: str,
        drone_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self._record_failure(reason, command, drone_id, details)
        return {
            "success": False,
            "partial_success": False,
            "status": "failed",
            "command": command,
            "command_id": str(uuid.uuid4()),
            "drone_id": drone_id,
            "failure_reason": reason,
            "failure_event_id": event["event_id"],
            "failure_details": details or {},
        }

    def _base_contract_max_steps(self) -> int | None:
        value = self.contract.get("resources", {}).get("max_total_steps")
        if value is None:
            return None
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return None

    def _repair_window_steps(self) -> int:
        value = self.contract.get("resources", {}).get("repair_step_budget", 0)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _contract_max_steps(self) -> int | None:
        base = self._base_contract_max_steps()
        if base is None:
            return None
        if self.repair_window_enabled:
            return base + self._repair_window_steps()
        return base

    def _expected_waypoints(self, drone_id: str) -> list[Any]:
        if drone_id in self.task_bundles:
            value = self.task_bundles[drone_id].get("waypoints")
            if isinstance(value, list):
                return value
        mission = self.contract.get("mission", {})
        value = mission.get("required_waypoint_sequence") if isinstance(mission, dict) else None
        return value if isinstance(value, list) else []

    def _contract_waypoint_count(self) -> int:
        if self.task_bundles:
            return sum(len(bundle.get("waypoints", [])) for bundle in self.task_bundles.values())
        return len(self.task.get("goal", {}).get("waypoints", []))

    def _completed_waypoint_count(self) -> int:
        if self.task_bundles:
            return sum(self.completed_waypoints_by_bundle.values())
        return sum(max(0, int(value)) for value in self.completed_waypoints.values())

    def _task_success(self) -> bool:
        if self.task_bundles:
            completed = bool(self.task_bundles) and all(
                self.completed_waypoints_by_bundle.get(
                    str(bundle.get("bundle_id") or drone_id), 0
                )
                >= len(bundle.get("waypoints", []))
                for drone_id, bundle in self.task_bundles.items()
            )
            if not completed:
                return False
            ctrl_freq = max(1, int(self.config.get("ctrl_freq", 30)))
            for owner_id, bundle in self.task_bundles.items():
                deadline = bundle.get("deadline_s")
                if deadline is None:
                    continue
                try:
                    completion_step = self.bundle_completion_step.get(
                        str(bundle.get("bundle_id") or owner_id), self.total_steps
                    )
                    if completion_step / ctrl_freq > float(deadline):
                        return False
                except (TypeError, ValueError):
                    continue
            return True
        target_count = len(self.task.get("goal", {}).get("waypoints", []))
        return target_count > 0 and self._completed_waypoint_count() >= target_count

    def _infer_repair_bundle(
        self, drone_id: str, waypoints: list[list[float]]
    ) -> str | None:
        """Infer a bundle when a replacement UAV cannot send bundle_id.

        The Agent4Drone movement tool schema does not expose the bridge-only
        bundle_id field.  During a repair window, match the requested full
        path or remaining suffix against the contract and bind it to the
        unique bundle.  This is contract matching, not a reference-trace
        oracle, and is necessary for capability-compatible reassignment.
        """
        if not self.repair_window_enabled or not self.task_bundles:
            return None
        try:
            tolerance = float(
                self.contract.get("mission", {}).get("waypoint_tolerance", 0.25)
            )
        except (TypeError, ValueError):
            tolerance = 0.25

        def matches(reference: list[Any]) -> bool:
            if len(reference) != len(waypoints):
                return False
            for left, right in zip(reference, waypoints):
                if math.dist(_xyz(left), _xyz(right)) > tolerance:
                    return False
            return True

        matches_found: list[str] = []
        for owner_id, bundle in self.task_bundles.items():
            expected = bundle.get("waypoints") if isinstance(bundle, dict) else None
            if not isinstance(expected, list):
                continue
            bundle_id = str(bundle.get("bundle_id") or owner_id)
            done = max(0, int(self.completed_waypoints_by_bundle.get(bundle_id, 0) or 0))
            # A completed bundle cannot be the target of a repair suffix.  This
            # also resolves legitimate coordinate reuse between a completed
            # one-point bundle and a later unfinished bundle.
            if done >= len(expected):
                continue
            if matches(expected) or (done > 0 and matches(expected[done:])):
                matches_found.append(bundle_id)
        if len(matches_found) == 1:
            return matches_found[0]
        return None

    def _validate_waypoint_sequence(
        self,
        drone_id: str,
        waypoints: list[list[float]],
        bundle_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, int]:
        """Validate a full path or the remaining suffix after a partial failure.

        The returned start index is zero for a normal full-plan execution and is
        the number of already completed contract waypoints for a resumed repair.
        A repair may therefore execute the expected suffix on the same live
        bridge without replaying completed work.
        """
        mission = self.contract.get("mission", {})
        if not mission:
            return None, 0
        selected_bundle: dict[str, Any] | None = None
        if bundle_id is None:
            bundle_id = self._infer_repair_bundle(drone_id, waypoints)
            if bundle_id is None and self.repair_window_enabled:
                profile = self.failure_profile if isinstance(self.failure_profile, dict) else {}
                profile_bundle = profile.get("bundle_id")
                if profile.get("type") in {
                    "drone_unavailable_after_partial_execution",
                    "role_capability_conflict_after_partial_execution",
                    "multi_constraint_contention_after_partial_execution",
                    "comparative_repair_choice_after_partial_execution",
                } and profile_bundle:
                    bundle_id = str(profile_bundle)
        if bundle_id is not None:
            for owner_id, bundle in self.task_bundles.items():
                if str(bundle.get("bundle_id") or owner_id) == str(bundle_id):
                    selected_bundle = bundle
                    break
            if selected_bundle is None:
                return self._failure_result(
                    "unknown_task_bundle",
                    "move_along_path",
                    drone_id,
                    {"bundle_id": bundle_id},
                ), 0
            bundle_failure = self._bundle_assignment_failure(
                "move_along_path", drone_id, selected_bundle
            )
            if bundle_failure is not None:
                return bundle_failure, 0
            dependencies = mission.get("bundle_dependencies", {})
            required_dependencies = (
                dependencies.get(str(bundle_id), [])
                if isinstance(dependencies, dict)
                else []
            )
            for dependency_id in required_dependencies:
                dependency_bundle = next(
                    (
                        value
                        for owner_id, value in self.task_bundles.items()
                        if str(value.get("bundle_id") or owner_id) == str(dependency_id)
                    ),
                    None,
                )
                if isinstance(dependency_bundle, dict):
                    dependency_done = int(
                        self.completed_waypoints_by_bundle.get(str(dependency_id), 0) or 0
                    )
                    if dependency_done < len(dependency_bundle.get("waypoints", [])):
                        return self._failure_result(
                            "bundle_precedence_violation",
                            "move_along_path",
                            drone_id,
                            {
                                "bundle_id": bundle_id,
                                "required_dependency": dependency_id,
                                "completed_dependency_waypoints": dependency_done,
                                "required_dependency_waypoints": len(
                                    dependency_bundle.get("waypoints", [])
                                ),
                                "steps_consumed": 0,
                            },
                        ), 0
            self.bundle_assignment[str(bundle_id)] = drone_id
            expected = selected_bundle.get("waypoints")
        elif drone_id in self.task_bundles:
            selected_bundle = self.task_bundles[drone_id]
            expected = selected_bundle.get("waypoints")
        elif mission.get("require_exact_sequence"):
            expected = mission.get("required_waypoint_sequence")
        else:
            return None, 0
        if not isinstance(expected, list):
            return None, 0
        try:
            tolerance = float(mission.get("waypoint_tolerance", 0.25))
        except (TypeError, ValueError):
            tolerance = 0.25
        progress_key = (
            str(selected_bundle.get("bundle_id") or drone_id)
            if selected_bundle is not None
            else None
        )
        completed_prefix = (
            max(0, int(self.completed_waypoints_by_bundle.get(progress_key, 0)))
            if progress_key is not None
            else max(0, int(self.completed_waypoints.get(drone_id, 0)))
        )

        def matches(reference: list[Any]) -> bool:
            if len(reference) != len(waypoints):
                return False
            for left, right in zip(reference, waypoints):
                try:
                    if math.dist(_xyz(left), _xyz(right)) > tolerance:
                        return False
                except (TypeError, ValueError):
                    return False
            return True

        if matches(expected):
            if 0 < completed_prefix < len(expected):
                if self.repair_window_enabled:
                    return self._failure_result(
                        "immutable_completed_prefix_replay",
                        "move_along_path",
                        drone_id,
                        {
                            "bundle_id": bundle_id,
                            "completed_prefix": completed_prefix,
                            "expected_waypoint_count": len(expected),
                        },
                    ), 0
                return None, completed_prefix
            return None, len(expected) if completed_prefix >= len(expected) else 0
        if completed_prefix > 0 and completed_prefix < len(expected):
            if matches(expected[completed_prefix:]):
                return None, completed_prefix

        mismatches: list[dict[str, Any]] = []
        for index, (left, right) in enumerate(zip(expected, waypoints)):
            try:
                distance = math.dist(_xyz(left), _xyz(right))
            except (TypeError, ValueError):
                distance = float("inf")
            if distance > tolerance:
                mismatches.append(
                    {
                        "index": index,
                        "expected": left,
                        "observed": right,
                        "distance": distance,
                    }
                )
        details = {
            "expected_waypoint_count": len(expected),
            "observed_waypoint_count": len(waypoints),
            "completed_waypoints_before_command": completed_prefix,
            "mismatches": mismatches[:8],
            "tolerance": tolerance,
            "accepted_form": "full_sequence_or_remaining_suffix",
        }
        return (
            self._failure_result(
                "waypoint_sequence_contract_violation",
                "move_along_path",
                drone_id,
                details,
            ),
            0,
        )

    def _required_drone_id(self) -> str | None:
        mission = self.contract.get("mission", {})
        required = mission.get("required_drone_id") if isinstance(mission, dict) else None
        return str(required) if required else None

    def _execution_mode_failure(self, command: str, drone_id: str) -> dict[str, Any] | None:
        mission = self.contract.get("mission", {})
        mode = mission.get("execution_mode") if isinstance(mission, dict) else None
        if mode == "single_waypoint_commands" and command == "move_along_path":
            return self._failure_result(
                "execution_mode_contract_violation",
                command,
                drone_id,
                {
                    "required_execution_mode": mode,
                    "observed_command": command,
                    "steps_consumed": 0,
                },
            )
        return None

    def _assignment_failure(self, command: str, drone_id: str) -> dict[str, Any] | None:
        required = self._required_drone_id()
        if required is None:
            return None
        if drone_id == required:
            return None
        return self._failure_result(
            "required_drone_assignment_violation",
            command,
            drone_id,
            {
                "required_drone_id": required,
                "observed_drone_id": drone_id,
                "required_role": self.role_by_drone.get(required),
                "observed_role": self.role_by_drone.get(drone_id),
                "steps_consumed": 0,
            },
        )

    def _bundle_assignment_failure(
        self, command: str, drone_id: str, bundle: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Enforce role/capability eligibility for every bundle assignment."""
        required = {
            str(item) for item in bundle.get("required_capabilities", [])
        }
        observed = set(self.capabilities_by_drone.get(drone_id, []))
        if not required.issubset(observed):
            return self._failure_result(
                "bundle_capability_violation",
                command,
                drone_id,
                {
                    "bundle_id": bundle.get("bundle_id"),
                    "required_capabilities": sorted(required),
                    "observed_capabilities": sorted(observed),
                    "steps_consumed": 0,
                },
            )
        eligible_roles = {
            str(item) for item in bundle.get("eligible_roles", [])
        }
        observed_role = self.role_by_drone.get(drone_id, "unspecified")
        if eligible_roles and observed_role not in eligible_roles:
            return self._failure_result(
                "bundle_role_violation",
                command,
                drone_id,
                {
                    "bundle_id": bundle.get("bundle_id"),
                    "eligible_roles": sorted(eligible_roles),
                    "observed_role": observed_role,
                    "steps_consumed": 0,
                },
            )
        return None

    def _resource_failure(self, command: str, drone_id: str) -> dict[str, Any] | None:
        max_steps = self._contract_max_steps()
        if max_steps is not None and self.total_steps >= max_steps:
            return self._failure_result(
                "execution_step_budget_exhausted",
                command,
                drone_id,
                {"max_total_steps": max_steps, "observed_total_steps": self.total_steps},
            )
        time_budget = self.contract.get("resources", {}).get("time_budget_s")
        if time_budget is not None:
            try:
                ctrl_freq = max(1, int(self.config.get("ctrl_freq", 30)))
                elapsed = self.total_steps / ctrl_freq
                if elapsed >= float(time_budget):
                    return self._failure_result(
                        "mission_deadline_exceeded",
                        command,
                        drone_id,
                        {
                            "time_budget_s": float(time_budget),
                            "observed_sim_time_s": elapsed,
                        },
                    )
            except (TypeError, ValueError):
                pass
        reserve = self.contract.get("resources", {}).get("min_battery_reserve_pct")
        if reserve is not None:
            try:
                battery = float(self._drone(drone_id, self._index(drone_id))["battery_level"])
                if battery < float(reserve):
                    return self._failure_result(
                        "battery_reserve_violation",
                        command,
                        drone_id,
                        {"min_battery_reserve_pct": float(reserve), "observed_battery_pct": battery},
                    )
            except (TypeError, ValueError):
                pass
        return None

    def _availability_failure(self, command: str, drone_id: str) -> dict[str, Any] | None:
        if drone_id not in self.unavailable_drones:
            return None
        profile = self.failure_profile if isinstance(self.failure_profile, dict) else {}
        return self._failure_result(
            "drone_unavailable",
            command,
            drone_id,
            {
                "unavailable_drone_ids": sorted(self.unavailable_drones),
                "repair_policy": profile.get(
                    "repair_policy",
                    "reassign_unfinished_bundle_to_capability_compatible_drone",
                ),
            },
        )

    def _partial_bundle_failure(
        self, drone_id: str, bundle_id: str | None
    ) -> dict[str, Any] | None:
        profile = self.failure_profile if isinstance(self.failure_profile, dict) else {}
        profile_type = str(profile.get("type") or "")
        if profile_type not in {
            "drone_unavailable_after_partial_execution",
            "role_capability_conflict_after_partial_execution",
            "multi_constraint_contention_after_partial_execution",
            "comparative_repair_choice_after_partial_execution",
        }:
            return None
        target_drone = str(profile.get("drone_id") or "")
        if not target_drone or drone_id != target_drone or drone_id in self.unavailable_drones:
            return None
        target_bundle = str(profile.get("bundle_id") or bundle_id or "")
        if bundle_id is not None and target_bundle != str(bundle_id):
            return None
        try:
            threshold = max(1, int(profile.get("after_completed_waypoints", 1)))
        except (TypeError, ValueError):
            threshold = 1
        completed = int(self.completed_waypoints_by_bundle.get(target_bundle, 0) or 0)
        if completed < threshold:
            return None
        self.unavailable_drones.add(drone_id)
        return self._failure_result(
            profile_type,
            "move_along_path",
            drone_id,
            {
                "bundle_id": target_bundle,
                "completed_waypoints_before_failure": completed,
                "unavailable_drone_ids": sorted(self.unavailable_drones),
                "required_capabilities": profile.get("required_capabilities", []),
                "required_role_family": profile.get("required_role_family", []),
                "invalidated_role": profile.get("invalidated_role"),
                "invalidated_capabilities": profile.get("invalidated_capabilities", []),
                "repair_policy": profile.get(
                    "repair_policy",
                    "reassign_unfinished_bundle_to_capability_compatible_drone",
                ),
            },
        )

    def _position(self, index: int) -> list[float]:
        return [float(value) for value in self.env._getDroneStateVector(index)[0:3]]

    def _drone(self, drone_id: str, index: int) -> dict[str, Any]:
        position = self._position(index)
        return {
            "id": drone_id,
            "name": drone_id,
            "status": (
                "unavailable"
                if drone_id in self.unavailable_drones
                else ("landed" if position[2] <= 0.25 else "flying")
            ),
            "position": {"x": position[0], "y": position[1], "z": position[2]},
            "battery_level": max(0.0, 100.0 - 0.01 * self.total_steps),
            "task_radius": float("inf"),
            "perceived_radius": float("inf"),
            "heading": float(self.env._getDroneStateVector(index)[9]),
            "role": self.role_by_drone.get(drone_id, "unspecified"),
            "capabilities": self.capabilities_by_drone.get(drone_id, []),
        }

    def drones(self) -> list[dict[str, Any]]:
        return [self._drone(drone_id, index) for drone_id, index in self.agent_index.items()]

    def _step(self, actions: np.ndarray) -> None:
        self.observation, _, _, _, _ = self.env.step(actions.astype(np.float32))
        self.total_steps += 1

    def _move_to(self, drone_id: str, target: list[float], tolerance: float | None = None) -> dict[str, Any]:
        if tolerance is None:
            try:
                tolerance = float(
                    self.contract.get("mission", {}).get("waypoint_tolerance", 0.25)
                )
            except (TypeError, ValueError):
                tolerance = 0.25
        index = self._index(drone_id)
        max_steps = max(1, int(self.config.get("max_steps", 900)) - self.total_steps)
        contract_max = self._contract_max_steps()
        if contract_max is not None:
            remaining = contract_max - self.total_steps
            if remaining <= 0:
                return self._failure_result(
                    "execution_step_budget_exhausted",
                    "move_to",
                    drone_id,
                    {"max_total_steps": contract_max, "observed_total_steps": self.total_steps},
                )
            # A recoverable-failure episode intentionally starts with a small
            # execution budget and receives an expanded contract budget only
            # after /bridge/repair-window is opened.  During that window the
            # original simulator cap must not truncate a valid suffix repair.
            # Before repair, retain the stricter configured/contract cap.
            max_steps = (
                max(1, remaining)
                if self.repair_window_enabled
                else min(max_steps, remaining)
            )
        reached = False
        start = self._position(index)
        steps = 0
        for _ in range(max_steps):
            current = np.asarray(self._position(index), dtype=np.float32)
            error = np.asarray(target, dtype=np.float32) - current
            distance = float(np.linalg.norm(error))
            if distance <= tolerance and steps > 0:
                reached = True
                break
            direction = error / max(distance, 1e-6)
            actions = np.zeros((len(self.agent_index), 4), dtype=np.float32)
            actions[index, :3] = np.clip(direction, -1.0, 1.0)
            actions[index, 3] = 0.005
            self._step(actions)
            steps += 1
        final = self._position(index)
        result = {
            "success": reached,
            "partial_success": not reached,
            "status": "completed" if reached else "partial",
            "command": "move_to",
            "command_id": str(uuid.uuid4()),
            "drone_id": drone_id,
            "position": {"x": target[0], "y": target[1], "z": target[2]},
            "final_position": {"x": final[0], "y": final[1], "z": final[2]},
            "distance_to_target": float(math.dist(final, target)),
            "steps": steps,
        }
        if not reached:
            event = self._record_failure(
                "execution_step_budget_exhausted",
                "move_to",
                drone_id,
                {"target": target, "max_steps_for_command": max_steps, "observed_total_steps": self.total_steps},
            )
            result.update({"failure_reason": event["reason"], "failure_event_id": event["event_id"]})
        return result

    def _record_contract_waypoint(
        self,
        drone_id: str,
        target: list[float],
        bundle_id: str | None = None,
    ) -> int | None:
        """Count a successful move_to when it reaches the next contract waypoint.

        Repair agents may use a single-waypoint command rather than the batch
        path tool.  Contract accounting must therefore be command-granular and
        independent of the surface tool used to execute the suffix.
        """
        mission = self.contract.get("mission", {})
        if not isinstance(mission, dict):
            return None
        selected_bundle: dict[str, Any] | None = None
        if bundle_id is None and drone_id in self.task_bundles:
            bundle_id = str(
                self.task_bundles[drone_id].get("bundle_id") or drone_id
            )
        if bundle_id is not None:
            for owner_id, bundle in self.task_bundles.items():
                if str(bundle.get("bundle_id") or owner_id) == str(bundle_id):
                    selected_bundle = bundle
                    self.bundle_assignment[str(bundle_id)] = drone_id
                    break
        expected = (
            selected_bundle.get("waypoints")
            if selected_bundle is not None
            else self._expected_waypoints(drone_id)
        )
        if not expected:
            return None
        progress_key = (
            str(selected_bundle.get("bundle_id") or bundle_id)
            if selected_bundle is not None
            else None
        )
        completed = (
            max(0, int(self.completed_waypoints_by_bundle.get(progress_key, 0)))
            if progress_key is not None
            else max(0, int(self.completed_waypoints.get(drone_id, 0)))
        )
        if completed >= len(expected):
            return completed
        try:
            tolerance = float(mission.get("waypoint_tolerance", 0.25))
            if math.dist(_xyz(expected[completed]), target) <= tolerance:
                completed += 1
                if progress_key is not None:
                    self.completed_waypoints_by_bundle[progress_key] = completed
                    if completed >= len(expected):
                        self.bundle_completion_step[progress_key] = self.total_steps
                else:
                    self.completed_waypoints[drone_id] = completed
                return completed
        except (TypeError, ValueError):
            return None
        return completed

    def _waypoints(
        self,
        drone_id: str,
        waypoints: list[Any],
        allow_partial: bool = True,
        bundle_id: str | None = None,
    ) -> dict[str, Any]:
        effective_bundle_id = bundle_id
        if (
            effective_bundle_id is None
            and drone_id in self.task_bundles
            and not self.repair_window_enabled
        ):
            effective_bundle_id = str(
                self.task_bundles[drone_id].get("bundle_id") or drone_id
            )
        normalized = [_xyz(item) for item in waypoints]
        if effective_bundle_id is None and self.repair_window_enabled:
            effective_bundle_id = self._infer_repair_bundle(drone_id, normalized)
            if effective_bundle_id is None:
                profile = self.failure_profile if isinstance(self.failure_profile, dict) else {}
                profile_bundle = profile.get("bundle_id")
                if profile.get("type") in {
                    "drone_unavailable_after_partial_execution",
                    "role_capability_conflict_after_partial_execution",
                    "multi_constraint_contention_after_partial_execution",
                    "comparative_repair_choice_after_partial_execution",
                } and profile_bundle:
                    effective_bundle_id = str(profile_bundle)
        contract_failure, start_index = self._validate_waypoint_sequence(
            drone_id, normalized, effective_bundle_id
        )
        if contract_failure is not None:
            return contract_failure
        expected_count = len(normalized)
        if effective_bundle_id is not None:
            bundle = next(
                (
                    value
                    for owner_id, value in self.task_bundles.items()
                    if str(value.get("bundle_id") or owner_id) == str(effective_bundle_id)
                ),
                None,
            )
            if isinstance(bundle, dict) and isinstance(bundle.get("waypoints"), list):
                expected_count = len(bundle["waypoints"])
        if start_index >= expected_count:
            return {
                "success": True,
                "partial_success": False,
                "status": "already_completed",
                "command": "move_along_path",
                "command_id": str(uuid.uuid4()),
                "drone_id": drone_id,
                "completed_waypoints": start_index,
                "completed_waypoints_in_command": 0,
                "resumed_from_waypoint": start_index,
                "waypoint_count": len(normalized),
                "allow_partial_move": allow_partial,
                "final_position": self._drone(drone_id, self._index(drone_id))["position"],
                "waypoint_results": [],
            }
        execution_waypoints = normalized
        auto_skipped_completed_prefix = 0
        if (
            not self.repair_window_enabled
            and start_index > 0
            and len(normalized) == expected_count
        ):
            auto_skipped_completed_prefix = start_index
            execution_waypoints = normalized[start_index:]
        if not self.repair_window_enabled:
            pre_execution_failure = self._partial_bundle_failure(
                drone_id, effective_bundle_id
            )
            if pre_execution_failure is not None:
                pre_execution_failure.update(
                    {
                        "completed_waypoints": start_index,
                        "completed_waypoints_in_command": 0,
                        "resumed_from_waypoint": start_index,
                        "waypoint_count": len(normalized),
                        "executed_waypoint_count": 0,
                        "auto_skipped_completed_prefix": auto_skipped_completed_prefix,
                        "allow_partial_move": allow_partial,
                        "waypoint_results": [],
                    }
                )
                return pre_execution_failure
        completed = 0
        results = []
        for waypoint in execution_waypoints:
            result = self._move_to(drone_id, waypoint)
            results.append(result)
            if not result["success"]:
                break
            completed += 1
            if effective_bundle_id is not None:
                self.completed_waypoints_by_bundle[str(effective_bundle_id)] = start_index + completed
            else:
                self.completed_waypoints[drone_id] = start_index + completed
            partial_failure = self._partial_bundle_failure(drone_id, effective_bundle_id)
            if partial_failure is not None:
                results.append(partial_failure)
                break
        if effective_bundle_id is not None:
            self.completed_waypoints_by_bundle[str(effective_bundle_id)] = start_index + completed
            if start_index + completed >= expected_count:
                self.bundle_completion_step[str(effective_bundle_id)] = self.total_steps
        else:
            self.completed_waypoints[drone_id] = start_index + completed
        success = completed == len(execution_waypoints) and all(
            bool(item.get("success")) for item in results
        )
        return {
            "success": success,
            "partial_success": not success,
            "status": "completed" if success else "partial",
            "command": "move_along_path",
            "command_id": str(uuid.uuid4()),
            "drone_id": drone_id,
            "completed_waypoints": start_index + completed,
            "completed_waypoints_in_command": completed,
            "resumed_from_waypoint": start_index,
            "waypoint_count": len(normalized),
            "executed_waypoint_count": len(execution_waypoints),
            "auto_skipped_completed_prefix": auto_skipped_completed_prefix,
            "allow_partial_move": allow_partial,
            "final_position": self._drone(drone_id, self._index(drone_id))["position"],
            "waypoint_results": results,
        }

    def _append_history(self, value: dict[str, Any], command: str, drone_id: str, started: float) -> None:
        record = dict(value)
        record.setdefault("command", command)
        record.setdefault("drone_id", drone_id)
        record["wall_started_seconds"] = round(started - self.episode_wall_start, 6)
        record["wall_finished_seconds"] = round(time.perf_counter() - self.episode_wall_start, 6)
        self.command_history.append(record)

    def command(self, drone_id: str, name: str, params: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            command_started = time.perf_counter()
            self.command_count += 1
            try:
                self._index(drone_id)
            except HTTPException as exc:
                self._record_failure("unknown_drone_or_role_mismatch", name, drone_id, {"detail": exc.detail})
                raise
            if name in {"move_to", "move_along_path", "take_off", "change_altitude", "land", "return_home", "hover"}:
                execution_mode_failure = self._execution_mode_failure(name, drone_id)
                if execution_mode_failure is not None:
                    self._append_history(execution_mode_failure, name, drone_id, command_started)
                    return execution_mode_failure
                assignment_failure = self._assignment_failure(name, drone_id)
                if assignment_failure is not None:
                    self._append_history(assignment_failure, name, drone_id, command_started)
                    return assignment_failure
                availability_failure = self._availability_failure(name, drone_id)
                if availability_failure is not None:
                    self._append_history(availability_failure, name, drone_id, command_started)
                    return availability_failure
                resource_failure = self._resource_failure(name, drone_id)
                if resource_failure is not None:
                    self._append_history(resource_failure, name, drone_id, command_started)
                    return resource_failure
            if name == "move_to":
                target = [float(params["x"]), float(params["y"]), float(params["z"])]
                effective_bundle_id = (
                    str(params.get("bundle_id")) if params.get("bundle_id") else None
                )
                if (
                    effective_bundle_id is None
                    and drone_id in self.task_bundles
                    and not self.repair_window_enabled
                ):
                    effective_bundle_id = str(
                        self.task_bundles[drone_id].get("bundle_id") or drone_id
                    )
                if effective_bundle_id is None and self.repair_window_enabled:
                    effective_bundle_id = self._infer_repair_bundle(drone_id, [target])
                    if effective_bundle_id is None:
                        profile = self.failure_profile if isinstance(self.failure_profile, dict) else {}
                        profile_bundle = profile.get("bundle_id")
                        if profile.get("type") in {
                            "drone_unavailable_after_partial_execution",
                            "role_capability_conflict_after_partial_execution",
                            "multi_constraint_contention_after_partial_execution",
                            "comparative_repair_choice_after_partial_execution",
                        } and profile_bundle:
                            effective_bundle_id = str(profile_bundle)
                pre_execution_failure = self._partial_bundle_failure(
                    drone_id, effective_bundle_id
                )
                if pre_execution_failure is not None:
                    self._append_history(pre_execution_failure, name, drone_id, command_started)
                    return pre_execution_failure
                result = self._move_to(drone_id, target)
                if result.get("success"):
                    sequential_completed = self._record_contract_waypoint(
                        drone_id, target, effective_bundle_id
                    )
                    if sequential_completed is not None:
                        result["completed_waypoints"] = sequential_completed
                self._append_history(result, name, drone_id, command_started)
                return result
            if name == "move_along_path":
                waypoints = params.get("waypoints")
                if not isinstance(waypoints, list) or not waypoints:
                    raise HTTPException(status_code=400, detail="waypoints is required")
                result = self._waypoints(
                    drone_id,
                    waypoints,
                    bool(params.get("allow_partial_move", True)),
                    str(params.get("bundle_id")) if params.get("bundle_id") else None,
                )
                self._append_history(result, name, drone_id, command_started)
                return result
            if name in {"take_off", "change_altitude"}:
                current = self._position(self._index(drone_id))
                result = self._move_to(drone_id, [current[0], current[1], float(params.get("altitude", 10.0))])
                self._append_history(result, name, drone_id, command_started)
                return result
            if name == "land":
                current = self._position(self._index(drone_id))
                result = self._move_to(drone_id, [current[0], current[1], 0.2])
                self._append_history(result, name, drone_id, command_started)
                return result
            if name == "return_home":
                result = self._move_to(drone_id, self.home[drone_id])
                self._append_history(result, name, drone_id, command_started)
                return result
            if name == "hover":
                duration = max(1.0, float(params.get("duration", 1.0)))
                steps = min(int(duration * self.config.get("ctrl_freq", 30)), 120)
                for _ in range(steps):
                    self._step(np.zeros((len(self.agent_index), 4), dtype=np.float32))
                result = {"success": True, "status": "completed", "command": name, "drone_id": drone_id}
                self._append_history(result, name, drone_id, command_started)
                return result
            result = {"success": True, "status": "completed", "command": name, "drone_id": drone_id}
            self._append_history(result, name, drone_id, command_started)
            return result


def create_app(task: dict[str, Any]) -> FastAPI:
    bridge = GymBridge(task)
    app = FastAPI(title="gym-pybullet-drones UAV bridge", version="1.0")
    app.state.bridge = bridge

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "active", "backend": "gym-pybullet-drones", "task_id": task.get("task_id")}

    @app.get("/sessions/current")
    def session() -> dict[str, Any]:
        return {
            "id": "gym-session",
            "name": "gym-pybullet-drones projection session",
            "task": task.get("goal", {}).get("type") or "uav_mission",
            "task_description": task.get("instruction", ""),
            "status": "running",
        }

    @app.get("/sessions/{session_id}/data")
    def session_data(session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "drones": bridge.drones(), "targets": [], "obstacles": [], "environment": task.get("environment", {})}

    @app.get("/sessions/{session_id}/task-progress")
    def progress(session_id: str) -> dict[str, Any]:
        completed = bridge._completed_waypoint_count()
        total = bridge._contract_waypoint_count()
        return {
            "session_id": session_id,
            "completed_waypoints": completed,
            "waypoint_count": total,
            "success": bridge._task_success(),
        }

    @app.get("/drones")
    def drones() -> list[dict[str, Any]]:
        with bridge.lock:
            return bridge.drones()

    @app.get("/drones/{drone_id}")
    def drone_status(drone_id: str) -> dict[str, Any]:
        with bridge.lock:
            index = bridge._index(drone_id)
            return bridge._drone(drone_id, index)

    @app.get("/drones/{drone_id}/nearby")
    def nearby(drone_id: str) -> dict[str, Any]:
        bridge._index(drone_id)
        return {"drone_id": drone_id, "drones": bridge.drones(), "targets": [], "obstacles": []}

    @app.get("/environments/current")
    def weather() -> dict[str, Any]:
        return task.get("environment", {}).get("weather", {}) or {"weather": "nominal"}

    @app.post("/bridge/repair-window")
    def open_repair_window() -> dict[str, Any]:
        with bridge.lock:
            if bridge.first_failure is None:
                raise HTTPException(
                    status_code=409,
                    detail="repair window requires a recorded execution failure",
                )
            if bridge.repair_window_enabled:
                return {
                    "enabled": True,
                    "already_enabled": True,
                    "repair_step_budget": bridge._repair_window_steps(),
                    "effective_max_total_steps": bridge._contract_max_steps(),
                }
            budget = bridge._repair_window_steps()
            if budget <= 0:
                raise HTTPException(
                    status_code=409,
                    detail="contract does not provide repair_step_budget",
                )
            bridge.repair_window_enabled = True
            bridge.repair_window_activation = {
                "failure_event_id": bridge.first_failure.get("event_id"),
                "wall_time_seconds": round(time.perf_counter() - bridge.episode_wall_start, 6),
                "base_max_total_steps": bridge._base_contract_max_steps(),
                "repair_step_budget": budget,
                "effective_max_total_steps": bridge._contract_max_steps(),
            }
            # The compact Gym adapter also keeps an internal per-episode
            # ``max_steps`` cap.  Extend that cap together with the contract
            # repair window; otherwise a valid same-session repair would be
            # rejected at the first step after the natural failure.
            effective_steps = bridge._contract_max_steps()
            if effective_steps is not None:
                try:
                    bridge.config["max_steps"] = max(
                        int(bridge.config.get("max_steps", 0) or 0),
                        int(effective_steps),
                    )
                except (TypeError, ValueError):
                    pass
            return {
                "enabled": True,
                "already_enabled": False,
                **bridge.repair_window_activation,
            }

    @app.get("/bridge/metrics")
    def bridge_metrics() -> dict[str, Any]:
        with bridge.lock:
            required = bridge._required_drone_id()
            completed = bridge._completed_waypoint_count()
            waypoint_count = bridge._contract_waypoint_count()
            return {
                "task_id": task.get("task_id"),
                "episode_seed": task.get("metadata", {}).get("episode_seed"),
                "ctrl_freq": int(bridge.config.get("ctrl_freq", 30)),
                "wall_time_seconds": round(time.perf_counter() - bridge.episode_wall_start, 6),
                "command_count": bridge.command_count,
                "total_steps": bridge.total_steps,
                "completed_waypoints": completed,
                "waypoint_count": waypoint_count,
                "required_drone_id": required,
                "task_success": bridge._task_success(),
                "completed_waypoints_by_drone": dict(bridge.completed_waypoints),
                "task_bundles": bridge.task_bundles,
                "completed_waypoints_by_bundle": dict(bridge.completed_waypoints_by_bundle),
                "initial_completed_waypoints_by_bundle": dict(bridge.initial_completed_waypoints_by_bundle),
                "bundle_completion_step": dict(bridge.bundle_completion_step),
                "bundle_assignment": dict(bridge.bundle_assignment),
                "final_drones": bridge.drones(),
                "unavailable_drone_ids": sorted(bridge.unavailable_drones),
                "command_history": bridge.command_history,
                "contract": bridge.contract,
                "failure_profile": bridge.failure_profile,
                "first_failure": bridge.first_failure,
                "failure_events": bridge.failure_events,
                "protocol": {
                    "supports_same_session_repair": True,
                    "supports_remaining_waypoint_suffix": True,
                    "supports_contract_repair_window": True,
                },
                "repair_window_enabled": bridge.repair_window_enabled,
                "repair_window_activation": bridge.repair_window_activation,
                "base_max_total_steps": bridge._base_contract_max_steps(),
                "effective_max_total_steps": bridge._contract_max_steps(),
                "sim_time_seconds": bridge.total_steps / max(
                    1, int(bridge.config.get("ctrl_freq", 30))
                ),
            }

    @app.get("/targets/waypoints")
    def target_waypoints() -> list[dict[str, Any]]:
        return [{"id": f"waypoint-{i + 1}", "name": f"Mission waypoint {i + 1}", "type": "waypoint", "position": {"x": p[0], "y": p[1], "z": p[2]}} for i, p in enumerate(task.get("goal", {}).get("waypoints", []))]

    @app.get("/targets/{target_id}")
    def target(target_id: str) -> dict[str, Any]:
        for item in target_waypoints():
            if item["id"] == target_id:
                return item
        raise HTTPException(status_code=404, detail="target not found")

    @app.get("/obstacles/{obstacle_id}")
    def obstacle(obstacle_id: str) -> dict[str, Any]:
        raise HTTPException(status_code=404, detail="obstacle not found in waypoint projection")

    @app.post("/obstacles/collision/check")
    async def collision_check(request: Request) -> dict[str, Any]:
        return {"collision": False, "safe": True, "projection_mode": True}

    @app.post("/obstacles/collision/path")
    async def path_collision(request: Request) -> dict[str, Any]:
        return {"collision": False, "safe": True, "projection_mode": True}

    async def command_endpoint(drone_id: str, name: str, request: Request) -> dict[str, Any]:
        params = dict(request.query_params)
        if request.method == "POST":
            try:
                body = await request.json()
                if isinstance(body, dict):
                    params.update(body)
            except Exception:
                pass
        return bridge.command(drone_id, name, params)

    def make_command_handler(command_name: str):
        async def handler(drone_id: str, request: Request) -> dict[str, Any]:
            return await command_endpoint(drone_id, command_name, request)

        return handler

    for command_name in ("take_off", "land", "change_altitude", "hover", "return_home", "set_home", "calibrate", "charge", "take_photo", "send_message", "broadcast", "rotate", "move_towards", "move_to", "move_along_path"):
        route = f"/drones/{{drone_id}}/command/{command_name}"
        app.add_api_route(route, make_command_handler(command_name), methods=["POST"])

    @app.on_event("shutdown")
    def shutdown() -> None:
        bridge.close()

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(os.environ.get("GYM_TASK_FILE", "~/uav_exp/data/processed/v1/uavbench_waypoint_projection_test100.jsonl")).expanduser())
    parser.add_argument("--task-id", default=os.environ.get("GYM_TASK_ID"))
    parser.add_argument("--host", default=os.environ.get("GYM_BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GYM_BRIDGE_PORT", "8000")))
    parser.add_argument("--max-steps", type=int, default=None, help="optional per-process step cap for fast repair pilots")
    args = parser.parse_args()
    if not args.task_id:
        raise SystemExit("--task-id or GYM_TASK_ID is required")
    task = _read_task(args.input.expanduser().resolve(), args.task_id)
    if args.max_steps is not None:
        task = json.loads(json.dumps(task))
        task.setdefault("environment", {}).setdefault("sim", {})["duration_steps"] = max(1, int(args.max_steps))
    import uvicorn

    uvicorn.run(create_app(task), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
