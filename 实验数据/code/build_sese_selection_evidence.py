"""Build preregistered UAVBench tasks for independent SeSE selection evidence.

The builder uses frozen v6.4 nominal-uncertain source geometry. It creates a
planning-only uncertainty layer with two role-compatible owners per bundle and
a route budget derived from the complete feasible assignment space. No method
output or observed performance is used during construction.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "sese_selection_evidence_v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def source_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("source_task_id") or str(row.get("task_id", "")).split("::")[0])


def xyz(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z", value.get("altitude"))]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None


def route_cost(start: Any, waypoints: Any) -> float:
    origin = xyz(start)
    points = [point for point in (xyz(item) for item in (waypoints or [])) if point is not None]
    if origin is None or not points:
        return 0.0
    return math.dist(origin, points[0]) + sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _bundle_values(task: dict[str, Any]) -> list[dict[str, Any]]:
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    bundles = mission.get("task_bundles") if isinstance(mission.get("task_bundles"), dict) else {}
    values = []
    for owner, value in bundles.items():
        if isinstance(value, dict):
            value["bundle_id"] = str(value.get("bundle_id") or owner)
            values.append(value)
    return sorted(values, key=lambda item: (int(item.get("order", 0)), item["bundle_id"]))


def _configure_selection_contract(task: dict[str, Any]) -> None:
    agents = [item for item in task.get("agents", []) if isinstance(item, dict) and item.get("agent_id")]
    agent_ids = [str(item["agent_id"]) for item in agents]
    role_by_id = {str(item["agent_id"]): str(item.get("role") or "unspecified") for item in agents}
    start_by_id = {str(item["agent_id"]): item.get("start") for item in agents}
    if len(agent_ids) < 2:
        raise ValueError("selection evidence requires at least two UAVs")
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    bundles = _bundle_values(task)
    choices: list[list[str]] = []
    for position, bundle in enumerate(bundles):
        original = str(bundle.get("original_owner") or bundle.get("preferred_drone_id") or bundle.get("drone_id") or agent_ids[position % len(agent_ids)])
        anchor = agent_ids.index(original) if original in agent_ids else position % len(agent_ids)
        alternative = agent_ids[(anchor + 1) % len(agent_ids)]
        eligible = [original, alternative] if original != alternative else [original]
        bundle["original_owner"] = original
        bundle["preferred_drone_id"] = original
        bundle["eligible_drone_ids"] = eligible
        bundle["eligible_roles"] = sorted({role_by_id[item] for item in eligible})
        bundle["required_capabilities"] = ["gnss"]
        bundle["assignment_is_decision_variable"] = True
        bundle["planning_cost_hint"] = "estimated_start_to_bundle_route_distance"
        choices.append(eligible)
    feasible_costs: list[float] = []
    max_bundles_per_uav = 2
    for assignment in itertools.product(*choices):
        if any(assignment.count(agent_id) > max_bundles_per_uav for agent_id in agent_ids):
            continue
        feasible_costs.append(sum(route_cost(start_by_id[owner], bundle.get("waypoints", [])) for owner, bundle in zip(assignment, bundles)))
    if not feasible_costs:
        raise ValueError("selection contract has no feasible assignment")
    feasible_costs.sort()
    median_cost = feasible_costs[len(feasible_costs) // 2]
    mission["planning_assignment_mode"] = "llm_shared_candidate_pool"
    mission["planning_variables"] = ["bundle_to_role_compatible_uav", "dependency_order"]
    mission["assignment_is_not_pre_resolved"] = True
    contract["contract_version"] = "mission_contract_sese_selection_evidence_v1"
    resources = contract.setdefault("resources", {})
    resources["max_bundles_per_uav"] = max_bundles_per_uav
    resources["selection_route_budget"] = round(median_cost, 9)
    resources["selection_route_budget_derivation"] = "median_cost_of_all_role_eligible_assignments_with_load_limit"
    contract["failure_policy"] = {"type": None, "fault_injection": False, "trigger_source": "none_selection_evidence", "repairable": False}
    contract["scenario_contract_notes"] = {
        "planning_uncertainty": True,
        "candidate_pool_generated_once_per_task_seed": True,
        "blind_and_sese_share_identical_pool": True,
        "future_failure_information_available": False,
        "method_outputs_used_for_task_selection": False,
        "physical_model_scope": "common_cf2x_bridge_contract_level_role_capability_heterogeneity",
    }


def build_task(source: dict[str, Any], seed_index: int) -> dict[str, Any]:
    task = copy.deepcopy(source)
    sid = source_id(task)
    task["task_id"] = f"{sid}::sese_selection_evidence_v1::run_seed_{seed_index}"
    task["split"] = "sese_selection_evidence_v1"
    metadata = dict(task.get("metadata") or {})
    base_seed = int(metadata.get("base_episode_seed") or metadata.get("episode_seed") or 0)
    digest = hashlib.sha256(f"sese-selection::{sid}::{base_seed}::{seed_index}".encode()).hexdigest()
    metadata.update({
        "source_task_id": sid,
        "condition": "nominal",
        "condition_label": "sese_selection_uncertain",
        "planning_uncertainty": True,
        "run_seed_index": seed_index,
        "run_seed": int(digest[:8], 16) & 0x7FFFFFFF,
        "episode_seed": int(digest[8:16], 16) & 0x7FFFFFFF,
        "scenario_version": PROTOCOL_VERSION,
        "seed_derivation": "sha256(protocol,source_task_id,base_episode_seed,run_seed_index)",
    })
    task["metadata"] = metadata
    _configure_selection_contract(task)
    task["scenario"] = {
        "scenario_version": PROTOCOL_VERSION,
        "scenario_id": task["task_id"],
        "source_task_id": sid,
        "condition": "nominal",
        "condition_label": "sese_selection_uncertain",
        "planning_uncertainty": True,
        "evaluation_mode": "shared_pool_preexecution_selection",
        "fault_injection": False,
        "paired_protocol": {
            "same_candidate_pool": True,
            "same_contract": True,
            "same_episode_seed": True,
            "blind_hash_preregistered": True,
            "selected_candidate_only_compared": True,
        },
    }
    return task


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--source-count", type=int, default=6)
    parser.add_argument("--purpose", choices=("development_gate", "formal"), required=True)
    args = parser.parse_args()
    rows = read_jsonl(args.input)
    selected_by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if str(metadata.get("condition_label")) == "nominal_uncertain":
            selected_by_source.setdefault(source_id(row), row)
    selected = list(selected_by_source.values())[: args.source_count]
    if len(selected) != args.source_count:
        raise SystemExit(f"expected {args.source_count} nominal_uncertain sources, found {len(selected)}")
    seeds = [int(item.strip()) for item in args.seeds.split(",") if item.strip()]
    tasks = [build_task(source, seed) for seed in seeds for source in selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in tasks), encoding="utf-8")
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "purpose": args.purpose,
        "input": str(args.input),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "output": str(args.output),
        "source_task_ids": [source_id(item) for item in selected],
        "seed_indices": seeds,
        "candidate_pool_size": 5,
        "task_seed_units": len(tasks),
        "selector_arms": ["Blind", "SeSE"],
        "expected_selection_records": len(tasks) * 2,
        "preregistration": {
            "one_llm_call_per_task_seed_pool": True,
            "pool_shared_by_selectors": True,
            "blind_rule": "sha256(blind-v1|task_id) mod K",
            "sese_inputs": ["coverage", "role_capability", "dependencies", "load_limit", "route_budget", "route_cost"],
            "future_failure_information_excluded": True,
            "task_or_candidate_outcome_filtering_forbidden": True,
            "primary_metrics": ["initial_plan_contract_validity_rate", "selection_gain", "selection_regret"],
            "diagnostics": ["promotion_rate", "abstention_rate", "initial_planning_latency_seconds", "token_cost"],
        },
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
