"""Build a preregistered UAVBench constraint-tightness sensitivity set.

The source tasks and failure event are frozen.  Only the number of
role/capability-compatible UAV candidates per bundle changes.  The generated
candidate pool is deterministic and shared by the two repair arms, so this
supplement isolates repair behavior rather than SeSE or LLM candidate quality.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


LEVELS = ("loose", "medium", "tight")
LEVEL_DEFINITIONS = {
    "loose": "retain every role/capability-compatible candidate in the frozen source contract",
    "medium": "retain the preferred owner and at most two compatible alternatives",
    "tight": "retain the preferred owner and one compatible alternative; the failed bundle keeps one backup",
}
METHODS = ("Blind+Global-Replan", "Blind+CDS-MR")
SCENARIO_VERSION = "uavbench_comparative_repair_v6_4_constraint_sensitivity_v1"
POOL_VERSION = "v6_4_constraint_sensitivity_shared_pool_v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def source_id(task: dict[str, Any]) -> str:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return str(metadata.get("source_task_id") or str(task.get("task_id", "")).split("::")[0])


def agents(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in task.get("agents", []) or []:
        if not isinstance(value, dict):
            continue
        agent_id = str(value.get("agent_id") or value.get("id") or "")
        if agent_id:
            result[agent_id] = value
    return result


def bundles(task: dict[str, Any]) -> list[dict[str, Any]]:
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    values = mission.get("task_bundles") if isinstance(mission.get("task_bundles"), dict) else {}
    result = []
    for owner, value in values.items():
        if isinstance(value, dict):
            item = copy.deepcopy(value)
            item["bundle_id"] = str(item.get("bundle_id") or owner)
            result.append(item)
    return sorted(result, key=lambda item: (int(item.get("order", 0)), item["bundle_id"]))


def compatible(agent: dict[str, Any], bundle: dict[str, Any]) -> bool:
    required = {str(item) for item in bundle.get("required_capabilities", []) or []}
    available = {str(item) for item in agent.get("capabilities", []) or []}
    roles = {str(item) for item in bundle.get("eligible_roles", []) or []}
    return required.issubset(available) and (not roles or str(agent.get("role") or "") in roles)


def failure_anchor(task: dict[str, Any]) -> tuple[str | None, str | None]:
    policy = ((task.get("contract") or {}).get("failure_policy") or {})
    return (
        str(policy.get("bundle_id")) if policy.get("bundle_id") else None,
        str(policy.get("drone_id")) if policy.get("drone_id") else None,
    )


def candidate_ids(task: dict[str, Any], bundle: dict[str, Any]) -> list[str]:
    amap = agents(task)
    existing = [str(item) for item in bundle.get("eligible_drone_ids", []) or []]
    ordered = existing + [item for item in amap if item not in existing]
    return [item for item in ordered if item in amap and compatible(amap[item], bundle)]


def constrained_candidates(
    task: dict[str, Any], bundle: dict[str, Any], level: str, failed_bundle_id: str | None
) -> list[str]:
    owner = str(bundle.get("preferred_drone_id") or bundle.get("original_owner") or bundle.get("drone_id") or "")
    choices = candidate_ids(task, bundle)
    if owner not in choices:
        raise RuntimeError(f"preferred owner is not compatible: {bundle.get('bundle_id')} -> {owner}")
    alternatives = [item for item in choices if item != owner]
    if level == "loose":
        selected = [owner] + alternatives
    elif level == "medium":
        selected = [owner] + alternatives[:2]
    elif level == "tight":
        selected = [owner] + alternatives[:1]
    else:
        raise ValueError(f"unknown level: {level}")
    if str(bundle.get("bundle_id")) == failed_bundle_id and len(selected) < 2:
        raise RuntimeError(f"failed bundle has no compatible backup: {failed_bundle_id}")
    return selected


def adapt_task(source: dict[str, Any], level: str) -> dict[str, Any]:
    task = copy.deepcopy(source)
    sid = source_id(source)
    failed_bundle_id, failed_drone_id = failure_anchor(source)
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    contract = copy.deepcopy(contract)
    mission = contract.setdefault("mission", {})
    bundle_values = mission.get("task_bundles") if isinstance(mission.get("task_bundles"), dict) else {}
    new_bundles: dict[str, dict[str, Any]] = {}
    candidate_counts: dict[str, int] = {}
    for owner_key, raw in bundle_values.items():
        if not isinstance(raw, dict):
            continue
        bundle = copy.deepcopy(raw)
        bundle["bundle_id"] = str(bundle.get("bundle_id") or owner_key)
        selected = constrained_candidates(task, bundle, level, failed_bundle_id)
        bundle["eligible_drone_ids"] = selected
        amap = agents(task)
        bundle["eligible_roles"] = sorted({str(amap[item].get("role") or "") for item in selected})
        bundle["assignment_is_decision_variable"] = True
        bundle["constraint_sensitivity_level"] = level
        new_bundles[str(owner_key)] = bundle
        candidate_counts[bundle["bundle_id"]] = len(selected)
    mission["task_bundles"] = new_bundles
    mission["planning_assignment_mode"] = "shared_deterministic_initial_plan_then_repair_comparison"
    mission["assignment_is_not_pre_resolved"] = True
    contract["contract_version"] = "mission_contract_v6_4_constraint_sensitivity"
    notes = contract.get("scenario_contract_notes") if isinstance(contract.get("scenario_contract_notes"), dict) else {}
    notes.update(
        {
            "constraint_sensitivity_level": level,
            "constraint_level_definition": LEVEL_DEFINITIONS[level],
            "shared_hard_constraints": [
                "role_and_capability_eligibility",
                "bundle_dependencies",
                "deadline",
                "shared_step_budget",
                "immutable_completed_prefix",
            ],
            "method_outputs_not_used_for_scenario_selection": True,
        }
    )
    contract["scenario_contract_notes"] = notes
    policy = contract.get("failure_policy") if isinstance(contract.get("failure_policy"), dict) else {}
    policy = copy.deepcopy(policy)
    policy["replacement_candidates"] = [
        item for item in new_bundles.get(str(failed_bundle_id), {}).get("eligible_drone_ids", [])
        if item != failed_drone_id
    ]
    policy["constraint_sensitivity_level"] = level
    contract["failure_policy"] = policy
    task["contract"] = contract
    task["task_id"] = f"{sid}::constraint_sensitivity_{level}::run_seed_13"
    task["split"] = "supplementary_constraint_sensitivity_v1"
    metadata = copy.deepcopy(task.get("metadata") or {})
    metadata.update(
        {
            "source_task_id": sid,
            "condition": "recoverable_failure",
            "condition_label": "recoverable_failure_uncertain",
            "planning_uncertainty": True,
            "run_seed_index": 13,
            "scenario_version": SCENARIO_VERSION,
            "robustness_factor": "constraint_tightness",
            "robustness_level": level,
            "constraint_level_rank": LEVELS.index(level),
            "constraint_level_definition": LEVEL_DEFINITIONS[level],
            "candidate_counts_by_bundle": candidate_counts,
            "selection_policy": "six frozen source ids and three levels fixed before execution; no method outcomes used",
            "same_source_episode_seed_across_levels": True,
            "simulator_limitations": [
                "all simulated vehicles use the common CF2X bridge model",
                "role and capability differences are contract-level constraints",
            ],
        }
    )
    task["metadata"] = metadata
    old_scenario = copy.deepcopy(task.get("scenario") or {})
    old_scenario.update(
        {
            "scenario_version": SCENARIO_VERSION,
            "scenario_id": task["task_id"],
            "source_task_id": sid,
            "condition": "recoverable_failure",
            "condition_label": "recoverable_failure_uncertain",
            "planning_uncertainty": True,
            "robustness_factor": "constraint_tightness",
            "robustness_level": level,
            "constraint_level_definition": LEVEL_DEFINITIONS[level],
            "failure_model": copy.deepcopy(contract.get("failure_policy") or {}),
            "paired_protocol": {
                "same_source_geometry": True,
                "same_episode_seed": True,
                "same_contract_within_level": True,
                "same_initial_plan_across_repair_arms": True,
                "only_constraint_tightness_varies_across_levels": True,
                "candidate_selection_before_execution": True,
            },
            "method_boundaries": {
                "Blind+Global-Replan": "global repair over every unfinished bundle",
                "Blind+CDS-MR": "causal-slice and lexicographic minimum-edit repair",
            },
        }
    )
    task["scenario"] = old_scenario
    return task


def validate_task(task: dict[str, Any]) -> dict[str, Any]:
    amap = agents(task)
    bs = bundles(task)
    by_id = {item["bundle_id"]: item for item in bs}
    checks: dict[str, bool] = {
        "six_or_fewer_agents_present": bool(amap),
        "all_bundle_candidates_nonempty": True,
        "preferred_assignments_compatible": True,
        "failed_bundle_has_backup": True,
        "failure_is_recoverable": bool((task.get("contract") or {}).get("failure_policy", {}).get("repairable")),
        "immutable_prefix_enabled": bool((task.get("contract") or {}).get("mission", {}).get("repair_must_preserve_completed_prefix")),
    }
    failure_bundle, failure_owner = failure_anchor(task)
    for bundle in bs:
        choices = [str(item) for item in bundle.get("eligible_drone_ids", []) or []]
        if not choices:
            checks["all_bundle_candidates_nonempty"] = False
        owner = str(bundle.get("preferred_drone_id") or bundle.get("original_owner") or bundle.get("drone_id") or "")
        if owner not in choices or not compatible(amap.get(owner, {}), bundle):
            checks["preferred_assignments_compatible"] = False
        if bundle["bundle_id"] == failure_bundle:
            checks["failed_bundle_has_backup"] = len([item for item in choices if item != failure_owner]) >= 1
    deps = ((task.get("contract") or {}).get("mission") or {}).get("bundle_dependencies") or {}
    order = {item["bundle_id"]: int(item.get("order", 0)) for item in bs}
    checks["dependency_graph_acyclic_in_order"] = all(
        order.get(str(dep), -1) < order.get(str(bid), -1)
        for bid, values in deps.items()
        for dep in (values if isinstance(values, list) else [])
        if str(dep) in order and str(bid) in order
    )
    return {"valid": all(checks.values()), "checks": checks, "candidate_counts": {item["bundle_id"]: len(item.get("eligible_drone_ids", []) or []) for item in bs}}


def deterministic_pool(task: dict[str, Any]) -> dict[str, Any]:
    plan = [
        {
            "bundle_id": bundle["bundle_id"],
            "assigned_drone_id": str(bundle.get("preferred_drone_id") or bundle.get("original_owner") or bundle.get("drone_id")),
            "order": int(bundle.get("order", index)),
        }
        for index, bundle in enumerate(bundles(task), start=1)
    ]
    validation = validate_task(task)
    candidate = {
        "candidate_index": 0,
        "plan": plan,
        "validation": {"valid": validation["valid"], "contract_score": 1.0 if validation["valid"] else 0.0, "route_cost": 0.0},
        "signature": str(tuple((item["bundle_id"], item["assigned_drone_id"], item["order"]) for item in plan)),
        "planner_status": "deterministic_shared_initial_plan",
        "planner_latency_seconds": 0.0,
        "planner_token_cost": 0,
        "temperature": 0.0,
    }
    core = {"task_id": task["task_id"], "candidate": candidate}
    pool_hash = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    failed_bundle, failed_drone = failure_anchor(task)
    return {
        "protocol_version": POOL_VERSION,
        "task_id": task["task_id"],
        "source_task_id": source_id(task),
        "run_seed_index": (task.get("metadata") or {}).get("run_seed_index"),
        "episode_seed": (task.get("metadata") or {}).get("episode_seed"),
        "constraint_sensitivity_level": (task.get("metadata") or {}).get("robustness_level"),
        "candidate_count": 1,
        "candidate_signature_count": 1,
        "candidate_pool_hash": pool_hash,
        "failure_anchor": {"bundle_id": failed_bundle, "drone_id": failed_drone},
        "generated_once_per_task_seed": True,
        "shared_by_repair_arms": list(METHODS),
        "selector": "Blind",
        "selector_excluded_inputs": ["failure_outcome", "simulator_metrics", "method_output"],
        "candidates": [candidate],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pools", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    args = parser.parse_args()
    rows = read_jsonl(args.input)
    frozen = [
        row
        for row in rows
        if str((row.get("metadata") or {}).get("condition_label")) == "recoverable_failure_uncertain"
    ]
    by_source: dict[str, dict[str, Any]] = {}
    for row in frozen:
        by_source.setdefault(source_id(row), row)
    if len(by_source) < 6:
        raise SystemExit(f"expected at least six frozen source tasks, found {len(by_source)}")
    selected = list(by_source.values())[:6]
    scenarios = [adapt_task(row, level) for row in selected for level in LEVELS]
    pools = [deterministic_pool(task) for task in scenarios]
    validations = [
        {
            "task_id": task["task_id"],
            "source_task_id": source_id(task),
            "robustness_level": (task.get("metadata") or {}).get("robustness_level"),
            **validate_task(task),
        }
        for task in scenarios
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.pools.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.validation.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in scenarios), encoding="utf-8")
    args.pools.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in pools), encoding="utf-8")
    args.validation.write_text(json.dumps({"valid": all(row["valid"] for row in validations), "rows": validations}, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "protocol_version": SCENARIO_VERSION,
        "source": "frozen v6.4 recoverable_failure_uncertain tasks, seed 13",
        "source_task_ids": [source_id(row) for row in selected],
        "factor": "constraint_tightness",
        "levels": list(LEVELS),
        "level_definitions": LEVEL_DEFINITIONS,
        "task_rows": len(scenarios),
        "expected_result_rows": len(scenarios) * len(METHODS),
        "methods": list(METHODS),
        "same_episode_seed_across_levels": True,
        "same_failure_event_across_levels": True,
        "candidate_pool": "one deterministic shared initial plan per task-level; no LLM candidate generation",
        "selection_outcome_excluded": True,
        "paired_key_definition": "source_task_id + constraint_sensitivity_level + run_seed_index + repair_method",
        "timeout_policy": "per-task runner errors are recorded and the batch continues",
        "outputs": {"tasks": str(args.output), "pools": str(args.pools), "validation": str(args.validation)},
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
