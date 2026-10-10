"""Common, protocol-versioned evaluator for the UAVBench v6.1 repair study."""
from __future__ import annotations
from typing import Any

PROTOCOL_VERSION = "v6_metric_protocol_v3"

def _bundles(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mission = (task.get("contract") or {}).get("mission") or {}
    value = mission.get("task_bundles") or {}
    return {str(k): v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}

def _bundle_id(owner: str, bundle: dict[str, Any]) -> str:
    return str(bundle.get("bundle_id") or owner)

def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0

def _completion_check(task: dict[str, Any], final: dict[str, Any]) -> bool:
    bundles = _bundles(task)
    completed = final.get("completed_waypoints_by_bundle") or {}
    if bundles:
        return all(_count(completed.get(_bundle_id(owner, b))) >= len(b.get("waypoints") or []) for owner, b in bundles.items())
    return bool(final.get("task_success"))

def _deadline_check(task: dict[str, Any], final: dict[str, Any]) -> bool:
    resources = (task.get("contract") or {}).get("resources") or {}
    mission = (task.get("contract") or {}).get("mission") or {}
    freq = max(1, _count(final.get("ctrl_freq") or 30))
    completion = final.get("bundle_completion_step") or {}
    default_deadline = resources.get("time_budget_s", mission.get("time_budget_s"))
    for owner, bundle in _bundles(task).items():
        limit = bundle.get("deadline_s", default_deadline)
        if limit is None:
            continue
        try:
            step = completion.get(_bundle_id(owner, bundle))
            if step is None or float(step) / freq > float(limit):
                return False
        except (TypeError, ValueError):
            return False
    return True

def _assignment_check(task: dict[str, Any], final: dict[str, Any]) -> bool:
    agents = {str(a.get("agent_id")): a for a in task.get("agents", []) if isinstance(a, dict)}
    assignment = final.get("bundle_assignment") or {}
    for owner, bundle in _bundles(task).items():
        drone = str(assignment.get(_bundle_id(owner, bundle)) or "")
        if not drone or drone not in agents:
            return False
        agent = agents[drone]
        required = {str(x) for x in (bundle.get("required_capabilities") or [])}
        actual = {str(x) for x in (agent.get("capabilities") or [])}
        roles = {str(x) for x in (bundle.get("eligible_roles") or [])}
        if not required.issubset(actual) or (roles and str(agent.get("role") or "unspecified") not in roles):
            return False
    return True

def _dependency_check(task: dict[str, Any], final: dict[str, Any]) -> bool:
    deps = ((task.get("contract") or {}).get("mission") or {}).get("bundle_dependencies") or {}
    completion = final.get("bundle_completion_step") or {}
    for bid, required in deps.items():
        for dep in required if isinstance(required, list) else []:
            try:
                if int(completion.get(str(dep), -1)) > int(completion.get(str(bid), -1)):
                    return False
            except (TypeError, ValueError):
                return False
    return True

def _budget_check(task: dict[str, Any], final: dict[str, Any]) -> bool:
    resources = (task.get("contract") or {}).get("resources") or {}
    limit = final.get("effective_max_total_steps") or resources.get("max_total_steps")
    if limit is None:
        return True
    try:
        return _count(final.get("total_steps")) <= int(limit)
    except (TypeError, ValueError):
        return False

def _prefix_check(initial: dict[str, Any], final: dict[str, Any]) -> bool:
    before = initial.get("completed_waypoints_by_bundle") or {}
    after = final.get("completed_waypoints_by_bundle") or {}
    return all(_count(after.get(str(bid))) >= _count(value) for bid, value in before.items())

def contract_satisfaction(task: dict[str, Any], initial: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "completion": _completion_check(task, final),
        "deadline": _deadline_check(task, final),
        "assignment_and_capability": _assignment_check(task, final),
        "dependency": _dependency_check(task, final),
        "budget": _budget_check(task, final),
        "immutable_completed_prefix": _prefix_check(initial, final),
    }
    return {"contract_satisfaction_rate": round(sum(bool(v) for v in checks.values()) / len(checks), 6), "strict_contract_satisfied": all(checks.values()), "contract_checks": checks}

def common_metrics(task: dict[str, Any], initial: dict[str, Any], final: dict[str, Any], *, plan_stability_distance: float | None = None, action_distance: float | None = None, repair_decision_latency_seconds: float | None = None, runner_wallclock_seconds: float | None = None, mission_completion_time_seconds: float | None = None, repair_attempted: bool = False) -> dict[str, Any]:
    recoverable = str((task.get("metadata") or {}).get("condition") or "") == "recoverable_failure"
    failed = isinstance(initial.get("first_failure"), dict)
    recovery = bool(final.get("task_success")) and failed if recoverable else None
    contract = contract_satisfaction(task, initial, final)
    if mission_completion_time_seconds is None and final.get("task_success"):
        mission_completion_time_seconds = final.get("sim_time_seconds")
    normalized = action_distance if action_distance is not None else plan_stability_distance
    return {
        "recovery_success_rate_item": recovery,
        "contract_satisfaction_rate": contract["contract_satisfaction_rate"],
        "strict_contract_satisfied": contract["strict_contract_satisfied"],
        "contract_checks": contract["contract_checks"],
        "normalized_plan_stability_distance": normalized,
        "repair_decision_latency_seconds": repair_decision_latency_seconds,
        "mission_completion_time_seconds": mission_completion_time_seconds,
        "mission_elapsed_until_terminal_seconds": final.get("sim_time_seconds"),
        "runner_wallclock_seconds": runner_wallclock_seconds,
        "metric_protocol": {
            "version": PROTOCOL_VERSION,
            "recovery_success_denominator": "all_preregistered_recoverable_failure_rows",
            "contract_satisfaction": "mean_of_completion_deadline_assignment_dependency_budget_prefix_checks",
            "normalized_plan_stability_distance": "symmetric_difference_over_union_of_remaining_actions",
            "repair_decision_latency": "first_successful_post_failure_command_wall_start_minus_failure_wall_time",
            "mission_completion_time": "bridge_sim_time_at_success; unsuccessful_rows_report_null",
            "nominal_recovery": "not_applicable",
        },
    }
