"""Generate, execute, and compare shared SeSE candidate pools.

Each task/seed receives one DeepSeek call that returns K=5 structured plans.
Blind and SeSE share the exact stored pool. Every candidate is then executed by
the same deterministic bridge executor so the independent ranking does not use
the SeSE gate's own score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

from run_agent4drone_parallel import load_environment_file, start_bridge, terminate_process, wait_health
from v6_metric_evaluator import common_metrics


PROTOCOL_VERSION = "sese_selection_evidence_v1"
K = 5
BLIND_SALT = "blind-v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


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


def bundles(task: dict[str, Any]) -> list[dict[str, Any]]:
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    values = mission.get("task_bundles") if isinstance(mission.get("task_bundles"), dict) else {}
    result = []
    for owner, value in values.items():
        if isinstance(value, dict):
            item = dict(value)
            item["bundle_id"] = str(item.get("bundle_id") or owner)
            result.append(item)
    return sorted(result, key=lambda item: (int(item.get("order", 0)), item["bundle_id"]))


def agents(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["agent_id"]): item
        for item in task.get("agents", [])
        if isinstance(item, dict) and item.get("agent_id")
    }


def planner_context(task: dict[str, Any]) -> dict[str, Any]:
    amap = agents(task)
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    resources = contract.get("resources") if isinstance(contract.get("resources"), dict) else {}
    return {
        "task_id": task.get("task_id"),
        "agents": [
            {
                "agent_id": aid,
                "role": item.get("role"),
                "capabilities": item.get("capabilities", []),
                "start": item.get("start"),
            }
            for aid, item in amap.items()
        ],
        "bundles": [
            {
                "bundle_id": item["bundle_id"],
                "required_capabilities": item.get("required_capabilities", []),
                "eligible_roles": item.get("eligible_roles", []),
                "waypoints": item.get("waypoints", []),
                "deadline_s": item.get("deadline_s"),
            }
            for item in bundles(task)
        ],
        "bundle_dependencies": mission.get("bundle_dependencies", {}),
        "resources": {
            "max_bundles_per_uav": resources.get("max_bundles_per_uav"),
            "selection_route_budget": resources.get("selection_route_budget"),
        },
        "information_boundary": {
            "future_failure_state_available": False,
            "eligible_uav_ids_precomputed_for_model": False,
            "model_must_reason_from_roles_and_capabilities": True,
        },
    }


def normalize_plan(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("bundles") or value.get("assignments") or value.get("plan")
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        bundle_id = item.get("bundle_id") or item.get("id")
        owner = item.get("assigned_drone_id") or item.get("assigned_drone") or item.get("drone_id")
        if bundle_id is None or owner is None:
            continue
        try:
            order = int(item.get("order", item.get("sequence", len(result) + 1)))
        except (TypeError, ValueError):
            order = len(result) + 1
        result.append({"bundle_id": str(bundle_id), "assigned_drone_id": str(owner), "order": order})
    return result


def parse_pool(payload: dict[str, Any]) -> list[list[dict[str, Any]]]:
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    message = choices[0].get("message", {}) if choices else {}
    raw = str(message.get("content", "") if isinstance(message, dict) else "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").replace("json", "", 1).strip()
    decoded = json.loads(raw)
    rows = decoded.get("candidates") if isinstance(decoded, dict) else None
    if not isinstance(rows, list) or len(rows) != K:
        raise ValueError(f"planner must return exactly {K} candidates")
    return [normalize_plan(item) for item in rows]


def generate_pool(task: dict[str, Any], args: argparse.Namespace, key: str) -> dict[str, Any]:
    context = planner_context(task)
    nonce = hashlib.sha256(str(task.get("task_id")).encode()).hexdigest()[:16]
    prompt = (
        "Generate exactly five independently reasoned candidate plans for the UAV task below. "
        "Return JSON only as {\"candidates\":[{\"bundles\":[{\"bundle_id\":\"...\","
        "\"assigned_drone_id\":\"...\",\"order\":1}]}]}. Each candidate must assign every "
        "bundle once. Every order must be a unique integer from 1 through the number of bundles; "
        "equal or repeated order values are forbidden, and every dependency predecessor must have "
        "a strictly smaller order than its dependent bundle. Reason from UAV roles, capabilities, "
        "starts, dependencies, load limit, and route budget. Produce genuinely different feasible "
        "approaches when alternatives exist while preserving the required dependency order. "
        "Do not execute actions and do not assume any future failure. Pool nonce: "
        + nonce
        + "\nCONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )
    body = {
        "model": args.planner_model,
        "messages": [
            {"role": "system", "content": "You are a structured UAV mission planner. Output valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": args.temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    errors: list[str] = []
    started = time.perf_counter()
    for attempt in range(1, args.max_attempts + 1):
        try:
            response = requests.post(args.planner_endpoint, headers=headers, json=body, timeout=args.planner_timeout)
            if response.status_code >= 400 and "response_format" in body:
                retry_body = dict(body)
                retry_body.pop("response_format", None)
                response = requests.post(args.planner_endpoint, headers=headers, json=retry_body, timeout=args.planner_timeout)
            response.raise_for_status()
            payload = response.json()
            pool = parse_pool(payload)
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            return {
                "protocol_version": PROTOCOL_VERSION,
                "task_id": task.get("task_id"),
                "source_task_id": (task.get("metadata") or {}).get("source_task_id"),
                "run_seed_index": (task.get("metadata") or {}).get("run_seed_index"),
                "pool_id": hashlib.sha256((str(task.get("task_id")) + "|" + json.dumps(pool, sort_keys=True)).encode()).hexdigest(),
                "candidate_count": len(pool),
                "candidates": [
                    {"candidate_index": index, "plan": plan, "signature": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()}
                    for index, plan in enumerate(pool)
                ],
                "generation": {
                    "model": args.planner_model,
                    "single_call_pool": True,
                    "attempts": attempt,
                    "latency_seconds": round(time.perf_counter() - started, 6),
                    "total_tokens": usage.get("total_tokens"),
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "future_failure_information_excluded": True,
                    "errors_before_success": errors,
                },
                "runner_status": "completed",
            }
        except Exception as exc:
            errors.append(repr(exc))
    return {
        "protocol_version": PROTOCOL_VERSION,
        "task_id": task.get("task_id"),
        "source_task_id": (task.get("metadata") or {}).get("source_task_id"),
        "run_seed_index": (task.get("metadata") or {}).get("run_seed_index"),
        "candidate_count": 0,
        "candidates": [],
        "generation": {"model": args.planner_model, "single_call_pool": True, "attempts": args.max_attempts, "latency_seconds": round(time.perf_counter() - started, 6), "errors": errors},
        "runner_status": "generation_error",
    }


def gate_features(task: dict[str, Any], plan: list[dict[str, Any]]) -> dict[str, Any]:
    amap = agents(task)
    bvalues = bundles(task)
    bmap = {item["bundle_id"]: item for item in bvalues}
    ids = [str(item.get("bundle_id")) for item in plan]
    assignment = {str(item.get("bundle_id")): str(item.get("assigned_drone_id")) for item in plan}
    orders = {str(item.get("bundle_id")): item.get("order") for item in plan}
    checks = {
        "coverage": len(ids) == len(bmap) and len(set(ids)) == len(ids) and set(ids) == set(bmap),
        "known_uav": all(owner in amap for owner in assignment.values()),
        "role_capability": True,
        "dependency_order": True,
        "load_limit": True,
        "route_budget": True,
    }
    load: dict[str, int] = {}
    total_cost = 0.0
    for bundle_id, bundle in bmap.items():
        owner = assignment.get(bundle_id, "")
        agent = amap.get(owner, {})
        roles = {str(item) for item in bundle.get("eligible_roles", []) or []}
        required = {str(item) for item in bundle.get("required_capabilities", []) or []}
        actual = {str(item) for item in agent.get("capabilities", []) or []}
        if not agent or (roles and str(agent.get("role")) not in roles) or not required.issubset(actual):
            checks["role_capability"] = False
        load[owner] = load.get(owner, 0) + 1
        total_cost += route_cost(agent.get("start"), bundle.get("waypoints", [])) if agent else 1e6
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    dependencies = mission.get("bundle_dependencies") if isinstance(mission.get("bundle_dependencies"), dict) else {}
    for bundle_id, required in dependencies.items():
        for dependency in required if isinstance(required, list) else []:
            if bundle_id not in orders or str(dependency) not in orders or orders[str(dependency)] >= orders[bundle_id]:
                checks["dependency_order"] = False
    resources = contract.get("resources") if isinstance(contract.get("resources"), dict) else {}
    limit = int(resources.get("max_bundles_per_uav") or len(bmap))
    checks["load_limit"] = all(count <= limit for count in load.values())
    budget = float(resources.get("selection_route_budget") or math.inf)
    checks["route_budget"] = total_cost <= budget + 1e-12
    return {
        "valid": all(checks.values()),
        "contract_score": sum(bool(value) for value in checks.values()) / len(checks),
        "checks": checks,
        "route_cost": total_cost,
        "route_budget": budget,
        "route_margin": budget - total_cost,
    }


def independent_contract_evaluation(task: dict[str, Any], plan: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic evaluator implemented separately from the SeSE selector."""
    amap = agents(task)
    expected = bundles(task)
    expected_ids = {item["bundle_id"] for item in expected}
    seen = [str(item.get("bundle_id")) for item in plan]
    owners = {str(item.get("bundle_id")): str(item.get("assigned_drone_id")) for item in plan}
    order = {str(item.get("bundle_id")): item.get("order") for item in plan}
    facts: list[bool] = []
    facts.append(len(seen) == len(expected) and len(seen) == len(set(seen)) and set(seen) == expected_ids)
    facts.append(len(order) == len(expected) and len(set(order.values())) == len(order))
    role_ok = True
    counts: dict[str, int] = {}
    total_route = 0.0
    for bundle in expected:
        bundle_id = bundle["bundle_id"]
        owner = owners.get(bundle_id)
        agent = amap.get(owner or "")
        roles = set(map(str, bundle.get("eligible_roles", []) or []))
        required = set(map(str, bundle.get("required_capabilities", []) or []))
        capabilities = set(map(str, (agent or {}).get("capabilities", []) or []))
        if agent is None or (roles and str(agent.get("role")) not in roles) or not required.issubset(capabilities):
            role_ok = False
        counts[str(owner)] = counts.get(str(owner), 0) + 1
        total_route += route_cost((agent or {}).get("start"), bundle.get("waypoints", [])) if agent else 1e6
    facts.append(role_ok)
    contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    dependency_ok = True
    for bundle_id, dependencies in (mission.get("bundle_dependencies") or {}).items():
        for dependency in dependencies if isinstance(dependencies, list) else []:
            if bundle_id not in order or str(dependency) not in order or order[str(dependency)] >= order[bundle_id]:
                dependency_ok = False
    facts.append(dependency_ok)
    resources = contract.get("resources") if isinstance(contract.get("resources"), dict) else {}
    max_load = int(resources.get("max_bundles_per_uav") or len(expected))
    facts.append(all(value <= max_load for value in counts.values()))
    budget = float(resources.get("selection_route_budget") or math.inf)
    facts.append(total_route <= budget + 1e-12)
    return {
        "evaluator": "independent_contract_evaluator_v1",
        "valid": all(facts),
        "contract_satisfaction_rate": sum(facts) / len(facts),
        "route_cost": total_route,
        "route_budget": budget,
        "route_margin": budget - total_route,
        "check_count": len(facts),
    }


def assert_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"port {port} already in use")


def execute_candidate(task: dict[str, Any], plan: list[dict[str, Any]], args: argparse.Namespace, candidate_index: int) -> dict[str, Any]:
    task_id = str(task.get("task_id"))
    safe = hashlib.sha256(f"{task_id}|{candidate_index}".encode()).hexdigest()[:16]
    log_path = args.log_dir / f"bridge_{safe}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    bridge = None
    handle = log_path.open("w", encoding="utf-8")
    started = time.perf_counter()
    try:
        assert_port_free(args.bridge_port)
        bridge_args = SimpleNamespace(input=args.input, bridge_max_steps=args.bridge_max_steps)
        bridge = start_bridge(task, bridge_args, args.bridge_port, handle)
        base_url = f"http://127.0.0.1:{args.bridge_port}"
        wait_health(base_url, args.startup_timeout)
        bmap = {item["bundle_id"]: item for item in bundles(task)}
        commands = []
        status = "completed"
        for item in sorted(plan, key=lambda value: int(value.get("order", 0))):
            bundle_id = str(item.get("bundle_id"))
            owner = str(item.get("assigned_drone_id"))
            bundle = bmap.get(bundle_id, {})
            command_started = time.perf_counter()
            try:
                response = requests.post(
                    f"{base_url}/drones/{owner}/command/move_along_path",
                    json={"waypoints": bundle.get("waypoints", []), "allow_partial_move": True, "bundle_id": bundle_id},
                    timeout=args.command_timeout,
                )
                response.raise_for_status()
                value = response.json()
            except Exception as exc:
                value = {"success": False, "error": repr(exc)}
            commands.append({"bundle_id": bundle_id, "assigned_drone_id": owner, "response": value, "seconds": round(time.perf_counter() - command_started, 6)})
            if not isinstance(value, dict) or not value.get("success"):
                status = "bridge_rejected_candidate"
                break
        metrics_response = requests.get(f"{base_url}/bridge/metrics", timeout=20)
        metrics_response.raise_for_status()
        metrics = metrics_response.json()
        common = common_metrics(
            task,
            metrics,
            metrics,
            runner_wallclock_seconds=time.perf_counter() - started,
            mission_completion_time_seconds=metrics.get("sim_time_seconds") if metrics.get("task_success") else None,
            repair_attempted=False,
        )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "task_id": task_id,
            "source_task_id": (task.get("metadata") or {}).get("source_task_id"),
            "run_seed_index": (task.get("metadata") or {}).get("run_seed_index"),
            "candidate_index": candidate_index,
            "status": status,
            "task_success": bool(metrics.get("task_success")),
            "contract_satisfaction_rate": common.get("contract_satisfaction_rate"),
            "mission_completion_time_seconds": common.get("mission_completion_time_seconds"),
            "commands": commands,
            "gym_metrics": metrics,
            "wallclock_seconds": round(time.perf_counter() - started, 6),
            "bridge_log": str(log_path),
        }
    except Exception as exc:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "task_id": task_id,
            "source_task_id": (task.get("metadata") or {}).get("source_task_id"),
            "run_seed_index": (task.get("metadata") or {}).get("run_seed_index"),
            "candidate_index": candidate_index,
            "status": "runner_error",
            "runner_error": repr(exc),
            "task_success": False,
            "wallclock_seconds": round(time.perf_counter() - started, 6),
            "bridge_log": str(log_path),
        }
    finally:
        terminate_process(bridge)
        handle.close()


def blind_index(task_id: str, count: int) -> int:
    digest = hashlib.sha256(f"{BLIND_SALT}|{task_id}".encode()).hexdigest()
    return int(digest[:16], 16) % count


def choose_sese(task: dict[str, Any], candidates: list[dict[str, Any]], blind: int) -> tuple[int, bool, float, list[dict[str, Any]]]:
    started = time.perf_counter()
    features = [gate_features(task, item.get("plan", [])) for item in candidates]
    valid_indices = [index for index, item in enumerate(features) if item["valid"]]
    abstained = not valid_indices
    if abstained:
        selected = blind
    else:
        selected = min(valid_indices, key=lambda index: (features[index]["route_cost"], index))
    return selected, abstained, time.perf_counter() - started, features


def independent_utility_rows(task: dict[str, Any], candidates: list[dict[str, Any]], executions: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in candidates:
        index = int(item["candidate_index"])
        static = independent_contract_evaluation(task, item.get("plan", []))
        execution = executions.get(index, {})
        mission_time = execution.get("mission_completion_time_seconds")
        rows.append({
            "candidate_index": index,
            "static": static,
            "execution": execution,
            "quality_key": (
                int(bool(execution.get("task_success"))),
                float(execution.get("contract_satisfaction_rate") or 0.0),
                int(bool(static.get("valid"))),
                float(static.get("contract_satisfaction_rate") or 0.0),
                -float(mission_time if mission_time is not None else 1e9),
                -float(static.get("route_cost") or 1e9),
            ),
        })
    unique_keys = sorted({row["quality_key"] for row in rows}, reverse=True)
    denominator = max(1, len(unique_keys) - 1)
    for row in rows:
        rank = unique_keys.index(row["quality_key"])
        row["independent_rank"] = rank
        row["independent_utility"] = 1.0 - rank / denominator
        row.pop("quality_key", None)
    return rows


def derive_selection_records(tasks: list[dict[str, Any]], pools: list[dict[str, Any]], executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_map = {str(item["task_id"]): item for item in tasks}
    pool_map = {str(item["task_id"]): item for item in pools if item.get("runner_status") == "completed"}
    execution_map: dict[str, dict[int, dict[str, Any]]] = {}
    for item in executions:
        execution_map.setdefault(str(item.get("task_id")), {})[int(item.get("candidate_index", -1))] = item
    output = []
    for task_id, task in task_map.items():
        pool = pool_map.get(task_id)
        if not pool:
            continue
        candidates = pool.get("candidates", [])
        blind = blind_index(task_id, len(candidates))
        sese, abstained, sese_latency, gate_rows = choose_sese(task, candidates, blind)
        independent = independent_utility_rows(task, candidates, execution_map.get(task_id, {}))
        independent_by_index = {int(item["candidate_index"]): item for item in independent}
        best_utility = max(float(item["independent_utility"]) for item in independent)
        blind_utility = float(independent_by_index[blind]["independent_utility"])
        sese_utility = float(independent_by_index[sese]["independent_utility"])
        generation = pool.get("generation", {})
        for selector, selected, selection_latency in (("Blind", blind, 0.0), ("SeSE", sese, sese_latency)):
            independent_row = independent_by_index[selected]
            static = independent_row["static"]
            execution = independent_row["execution"]
            utility = float(independent_row["independent_utility"])
            output.append({
                "protocol_version": PROTOCOL_VERSION,
                "task_id": task_id,
                "source_task_id": (task.get("metadata") or {}).get("source_task_id"),
                "run_seed_index": (task.get("metadata") or {}).get("run_seed_index"),
                "selector": selector,
                "pool_id": pool.get("pool_id"),
                "candidate_count": len(candidates),
                "candidate_signature_count": len({item.get("signature") for item in candidates}),
                "selected_candidate_index": selected,
                "blind_candidate_index": blind,
                "gate_promoted": bool(selector == "SeSE" and selected != blind),
                "gate_abstained": bool(selector == "SeSE" and abstained),
                "initial_plan_contract_valid": bool(static.get("valid")),
                "initial_plan_contract_satisfaction_rate": static.get("contract_satisfaction_rate"),
                "independent_selection_utility": utility,
                "selection_gain": (sese_utility - blind_utility) if selector == "SeSE" else 0.0,
                "selection_regret": best_utility - utility,
                "selected_route_cost": static.get("route_cost"),
                "actual_initial_execution_success": bool(execution.get("task_success")),
                "actual_contract_satisfaction_rate": execution.get("contract_satisfaction_rate"),
                "actual_mission_completion_time_seconds": execution.get("mission_completion_time_seconds"),
                "candidate_generation_latency_seconds": generation.get("latency_seconds"),
                "selection_latency_seconds": selection_latency,
                "initial_planning_latency_seconds": (float(generation.get("latency_seconds") or 0.0) + selection_latency),
                "initial_planning_token_cost": generation.get("total_tokens"),
                "llm_call_count": 1,
                "future_failure_information_used": False,
                "gate_features": gate_rows[selected] if selector == "SeSE" else None,
                "independent_candidate_evaluations": independent,
            })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("generate", "execute", "select", "all"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pools", type=Path, required=True)
    parser.add_argument("--executions", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--planner-endpoint", default="https://api.deepseek.com/chat/completions")
    parser.add_argument("--planner-model", default="deepseek-v4-flash")
    parser.add_argument("--planner-timeout", type=float, default=180.0)
    parser.add_argument("--temperature", type=float, default=0.9)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--agent-env-file", type=Path, default=Path("~/.config/agent4drone/agent4drone.env").expanduser())
    parser.add_argument("--bridge-port", type=int, default=9830)
    parser.add_argument("--bridge-max-steps", type=int, default=None)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--command-timeout", type=float, default=120.0)
    args = parser.parse_args()
    args.input = args.input.expanduser().resolve()
    args.pools = args.pools.expanduser().resolve()
    args.executions = args.executions.expanduser().resolve()
    args.selections = args.selections.expanduser().resolve()
    args.log_dir = args.log_dir.expanduser().resolve()
    tasks = read_jsonl(args.input)
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("shard-index must satisfy 0 <= index < shard-count")
    tasks = [item for index, item in enumerate(tasks) if index % args.shard_count == args.shard_index]
    if args.limit is not None:
        tasks = tasks[: max(0, args.limit)]
    task_map = {str(item["task_id"]): item for item in tasks}
    if args.phase in {"generate", "all"}:
        environment = load_environment_file(args.agent_env_file.expanduser().resolve())
        key = environment.get("LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
        if not key:
            raise RuntimeError("LLM_API_KEY is unavailable")
        done = {str(item.get("task_id")) for item in read_jsonl(args.pools) if item.get("runner_status") == "completed"}
        pending = [item for item in tasks if str(item.get("task_id")) not in done]
        print(json.dumps({"phase": "generate", "tasks": len(tasks), "pending": len(pending), "K": K}), flush=True)
        for position, task in enumerate(pending, 1):
            row = generate_pool(task, args, key)
            append_jsonl(args.pools, row)
            print(json.dumps({"progress": f"{position}/{len(pending)}", "task_id": task.get("task_id"), "status": row.get("runner_status"), "candidate_count": row.get("candidate_count")}), flush=True)
    if args.phase in {"execute", "all"}:
        pools = [item for item in read_jsonl(args.pools) if str(item.get("task_id")) in task_map and item.get("runner_status") == "completed"]
        done = {(str(item.get("task_id")), int(item.get("candidate_index", -1))) for item in read_jsonl(args.executions) if item.get("status") != "runner_error"}
        pending = []
        for pool in pools:
            task = task_map[str(pool["task_id"])]
            for candidate in pool.get("candidates", []):
                key = (str(pool["task_id"]), int(candidate["candidate_index"]))
                if key not in done:
                    pending.append((task, candidate))
        print(json.dumps({"phase": "execute", "candidate_runs": len(pools) * K, "pending": len(pending)}), flush=True)
        for position, (task, candidate) in enumerate(pending, 1):
            row = execute_candidate(task, candidate.get("plan", []), args, int(candidate["candidate_index"]))
            append_jsonl(args.executions, row)
            print(json.dumps({"progress": f"{position}/{len(pending)}", "task_id": task.get("task_id"), "candidate": candidate.get("candidate_index"), "status": row.get("status"), "success": row.get("task_success")}), flush=True)
    if args.phase in {"select", "all"}:
        pools = [item for item in read_jsonl(args.pools) if str(item.get("task_id")) in task_map]
        executions = [item for item in read_jsonl(args.executions) if str(item.get("task_id")) in task_map]
        rows = derive_selection_records(tasks, pools, executions)
        args.selections.parent.mkdir(parents=True, exist_ok=True)
        args.selections.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
        print(json.dumps({"phase": "select", "selection_records": len(rows), "expected": len(tasks) * 2}), flush=True)


if __name__ == "__main__":
    main()
