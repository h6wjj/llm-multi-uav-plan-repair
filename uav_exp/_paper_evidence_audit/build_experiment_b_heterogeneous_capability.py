#!/usr/bin/env python3
"""Build an isolated, contract-level heterogeneous-capability protocol.

The source geometry and waypoint partitions come from the existing v6.2
comparative scenarios.  Only the agent capability/role contract, bundle
requirements, and predeclared failure policy are changed in the copied records.
No main-comparison record is modified.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


PROFILES = {
    "uav_0": {
        "role": "mapping_specialist",
        "capabilities": ["gnss", "imu", "camera_rgb"],
    },
    "uav_1": {
        "role": "thermal_specialist",
        "capabilities": ["gnss", "imu", "camera_thermal"],
    },
    "uav_2": {
        "role": "payload_specialist",
        "capabilities": ["gnss", "imu", "camera_thermal", "high_payload"],
    },
    "uav_3": {
        "role": "endurance_specialist",
        "capabilities": ["gnss", "imu", "camera_rgb", "long_endurance"],
    },
}

BUNDLE_REQUIREMENTS = {
    "bundle_1": ("camera_rgb", ["mapping_specialist", "endurance_specialist"]),
    "bundle_2": ("camera_thermal", ["thermal_specialist", "payload_specialist"]),
    "bundle_3": ("high_payload", ["payload_specialist"]),
    "bundle_4": ("long_endurance", ["endurance_specialist"]),
}


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def source_id(row: dict[str, Any]) -> str:
    return str(row.get("metadata", {}).get("source_task_id") or row.get("task_id", "")).split("::")[0]


def eligible_ids(required: str, roles: list[str]) -> list[str]:
    return sorted(
        aid for aid, profile in PROFILES.items()
        if required in profile["capabilities"] and profile["role"] in roles
    )


def apply_contract(row: dict[str, Any], condition: str, protocol_seed: int) -> dict[str, Any]:
    task = copy.deepcopy(row)
    sid = source_id(row)
    task_id = f"{sid}::experiment_b_heterogeneous_capability_v1::{condition}"
    task["task_id"] = task_id
    task["split"] = "experiment_b_heterogeneous_capability"
    task["metadata"] = dict(task.get("metadata") or {})
    task["metadata"].update(
        {
            "source_task_id": sid,
            "condition": condition,
            "condition_label": f"heterogeneous_capability_{condition}",
            "protocol_seed": protocol_seed,
            "experiment_id": "B_heterogeneous_capability_exclusion_v1",
            "scenario_extension_scope": "explicit_role_capability_exclusion_with_shared_thermal_replacement",
        }
    )
    task["agents"] = []
    original_agents = {str(a.get("agent_id")): a for a in (row.get("agents") or []) if isinstance(a, dict)}
    for aid in ["uav_0", "uav_1", "uav_2", "uav_3"]:
        original = copy.deepcopy(original_agents.get(aid, {"agent_id": aid, "start": [0.0, 0.0, 1.0]}))
        original["agent_id"] = aid
        original["role"] = PROFILES[aid]["role"]
        original["capabilities"] = list(PROFILES[aid]["capabilities"])
        original["heterogeneity_profile"] = {
            "role": PROFILES[aid]["role"],
            "capability_exclusion_protocol": True,
        }
        task["agents"].append(original)

    contract = task.setdefault("contract", {})
    mission = contract.setdefault("mission", {})
    bundles = mission.get("task_bundles", {})
    if not isinstance(bundles, dict) or set(str(v.get("bundle_id")) for v in bundles.values() if isinstance(v, dict)) != set(BUNDLE_REQUIREMENTS):
        raise ValueError(f"unexpected bundle layout for {sid}")
    for owner_id, bundle in bundles.items():
        if not isinstance(bundle, dict):
            continue
        bid = str(bundle.get("bundle_id"))
        required, roles = BUNDLE_REQUIREMENTS[bid]
        bundle["drone_id"] = str(owner_id)
        bundle["original_owner"] = str(owner_id)
        bundle["preferred_drone_id"] = str(owner_id)
        bundle["required_capabilities"] = [required]
        bundle["eligible_roles"] = list(roles)
        bundle["eligible_drone_ids"] = eligible_ids(required, roles)
        bundle["assignment_is_decision_variable"] = True
        bundle["repair_policy"] = "preserve_completed_prefix_and_reassign_only_unfinished_bundle_under_capability_constraints"
    mission["bundle_dependencies"] = {
        "bundle_2": ["bundle_1"],
        "bundle_3": ["bundle_2"],
        "bundle_4": ["bundle_3"],
    }
    mission["repair_must_preserve_completed_prefix"] = True
    mission["repair_constraints"] = {
        "immutable_completed_prefix": True,
        "enforce_role_and_capability_eligibility": True,
        "enforce_bundle_dependencies": True,
        "enforce_shared_step_budget": True,
        "enforce_deadlines": True,
    }
    contract["agents"] = {
        "allowed_drone_ids": list(PROFILES),
        "role_by_drone": {aid: profile["role"] for aid, profile in PROFILES.items()},
        "capabilities_by_drone": {aid: list(profile["capabilities"]) for aid, profile in PROFILES.items()},
    }
    failure = {
        "type": "role_capability_conflict_after_partial_execution" if condition == "recoverable_failure" else None,
        "fault_injection": condition == "recoverable_failure",
        "trigger_source": "bridge_predeclared_capability_exclusion_after_partial_bundle" if condition == "recoverable_failure" else "none_nominal_control",
        "repairable": condition == "recoverable_failure",
        "drone_id": "uav_1" if condition == "recoverable_failure" else None,
        "bundle_id": "bundle_2" if condition == "recoverable_failure" else None,
        "after_completed_waypoints": 1,
        "required_capabilities": ["camera_thermal"],
        "required_role_family": ["thermal_specialist", "payload_specialist"],
        "invalidated_role": "thermal_specialist" if condition == "recoverable_failure" else None,
        "invalidated_capabilities": ["camera_thermal"] if condition == "recoverable_failure" else [],
        "replacement_candidates": ["uav_2"],
        "constraint_layer": "role_capability_exclusion",
        "unaffected_bundle_ids": ["bundle_1", "bundle_3", "bundle_4"],
        "bundle_dependencies": mission["bundle_dependencies"],
        "repair_constraints": mission["repair_constraints"],
        "repair_policy": "preserve_completed_prefix_and_reassign_unfinished_bundle_to_capability_compatible_drone",
    }
    contract["failure_policy"] = failure
    contract.setdefault("failure_observability", {}).update(
        {"record_first_failure": True, "record_failure_state": True, "record_failed_tool_call": True}
    )
    scenario = dict(task.get("scenario") or {})
    scenario.update(
        {
            "scenario_version": "experiment_b_heterogeneous_capability_v1",
            "scenario_id": task_id,
            "source_task_id": sid,
            "condition": condition,
            "comparison_protocol": "experiment_b_heterogeneous_capability_exclusion_v1",
            "failure_model": failure,
            "capability_exclusion_matrix": {
                bid: {"required_capability": req, "eligible_drone_ids": eligible_ids(req, roles)}
                for bid, (req, roles) in BUNDLE_REQUIREMENTS.items()
            },
            "paired_protocol": {
                "same_source_task": True,
                "same_geometry": True,
                "same_task_bundles": True,
                "same_initial_assignment": True,
                "same_failure_policy": True,
                "explicit_capability_exclusion": True,
            },
        }
    )
    task["scenario"] = scenario
    task.setdefault("metadata", {})["heterogeneous_capability_matrix"] = scenario["capability_exclusion_matrix"]
    task.setdefault("instruction", "Execute the ordered bundles while obeying the role and capability constraints in the mission contract.")
    return task


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-count", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260919)
    args = parser.parse_args()
    rows = load_rows(args.input)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if len(row.get("agents") or []) != 4:
            continue
        sid = source_id(row)
        if sid not in grouped or row.get("metadata", {}).get("condition") == "nominal":
            grouped[sid] = row
    selected = sorted(grouped.items())[: args.source_count]
    if len(selected) < args.source_count:
        raise SystemExit(f"found only {len(selected)} four-UAV source tasks")
    derived = []
    for _, row in selected:
        derived.extend([apply_contract(row, "nominal", args.seed), apply_contract(row, "recoverable_failure", args.seed)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in derived:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {
        "experiment_id": "B_heterogeneous_capability_exclusion_v1",
        "input": str(args.input),
        "output": str(args.output),
        "source_task_count": len(selected),
        "record_count": len(derived),
        "conditions": ["nominal", "recoverable_failure"],
        "selected_source_task_ids": [sid for sid, _ in selected],
        "profiles": PROFILES,
        "bundle_requirements": {
            bid: {"required_capability": req, "eligible_roles": roles, "eligible_drone_ids": eligible_ids(req, roles)}
            for bid, (req, roles) in BUNDLE_REQUIREMENTS.items()
        },
        "failure_target": {"drone_id": "uav_1", "bundle_id": "bundle_2", "replacement_candidates": ["uav_2"]},
        "limitations": [
            "Capability and role heterogeneity are explicit contract constraints; the bridge still uses the common CF2X motion model.",
            "The protocol tests a thermal-capability failure with a payload-capable thermal replacement; it is not a complete vehicle-dynamics study.",
        ],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
