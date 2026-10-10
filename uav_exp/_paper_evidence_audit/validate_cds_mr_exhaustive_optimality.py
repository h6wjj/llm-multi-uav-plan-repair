#!/usr/bin/env python3
"""Independent exhaustive optimality audit for the recorded CDS-MR repairs.

This validator intentionally does not import or call the CDS-MR solver.  It
reconstructs the finite repair problem from the recorded contract, failure
state, live UAV state, and remaining waypoint suffixes, then independently
enumerates every feasible assignment.  It is an audit of existing records;
it does not rerun an LLM, a simulator, or alter the main results.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any


def xyz(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z", value.get("altitude"))]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def route_cost(start: list[float], waypoints: list[Any]) -> float:
    points = [point for point in (xyz(value) for value in waypoints) if point is not None]
    if not points:
        return 0.0
    return math.dist(start, points[0]) + sum(
        math.dist(left, right) for left, right in zip(points, points[1:])
    )


def as_bundles(contract: dict[str, Any]) -> list[dict[str, Any]]:
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    values = mission.get("task_bundles", {})
    bundles: list[dict[str, Any]] = []
    if isinstance(values, dict):
        for owner_id, raw in values.items():
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["bundle_id"] = str(item.get("bundle_id") or owner_id)
            item["original_owner"] = str(item.get("drone_id") or item.get("original_owner") or owner_id)
            bundles.append(item)
    elif isinstance(values, list):
        for raw in values:
            if isinstance(raw, dict) and raw.get("bundle_id"):
                item = dict(raw)
                item["bundle_id"] = str(item["bundle_id"])
                item["original_owner"] = str(item.get("drone_id") or item.get("original_owner") or "")
                bundles.append(item)
    return sorted(bundles, key=lambda item: (int(item.get("order", 0)), str(item["bundle_id"])))


def topological_order(
    bundles: list[dict[str, Any]],
    remaining_ids: set[str],
    completed_ids: set[str],
    dependencies: dict[str, Any],
) -> list[str]:
    by_id = {str(item["bundle_id"]): item for item in bundles}
    pending = set(remaining_ids)
    ordered: list[str] = []
    while pending:
        ready = [
            bundle_id
            for bundle_id in pending
            if all(
                str(parent) in completed_ids or str(parent) in ordered
                for parent in (dependencies.get(bundle_id, []) or [])
            )
        ]
        if not ready:
            raise ValueError(f"cyclic_or_unsatisfied_bundle_dependencies:{sorted(pending)}")
        ready.sort(key=lambda bundle_id: (int(by_id[bundle_id].get("order", 0)), bundle_id))
        selected = ready[0]
        ordered.append(selected)
        pending.remove(selected)
    return ordered


def agents_from_record(contract: dict[str, Any], live_drones: list[Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    spec = contract.get("agents") if isinstance(contract.get("agents"), dict) else {}
    allowed = [str(item) for item in (spec.get("allowed_drone_ids", []) or [])]
    roles = spec.get("role_by_drone", {}) if isinstance(spec.get("role_by_drone"), dict) else {}
    capabilities = spec.get("capabilities_by_drone", {}) if isinstance(spec.get("capabilities_by_drone"), dict) else {}
    agents = {
        agent_id: {
            "id": agent_id,
            "role": roles.get(agent_id),
            "capabilities": list(capabilities.get(agent_id, []) or []),
        }
        for agent_id in allowed
    }
    for raw in live_drones if isinstance(live_drones, list) else []:
        if not isinstance(raw, dict):
            continue
        agent_id = str(raw.get("id") or raw.get("name") or raw.get("agent_id") or "")
        if agent_id:
            agents.setdefault(agent_id, {}).update(raw)
    unavailable: set[str] = set()
    for raw in live_drones if isinstance(live_drones, list) else []:
        if isinstance(raw, dict):
            agent_id = str(raw.get("id") or raw.get("name") or raw.get("agent_id") or "")
            if str(raw.get("status") or "").lower() in {"unavailable", "failed", "lost", "offline"}:
                unavailable.add(agent_id)
    return agents, unavailable


def compatible(agent: dict[str, Any], bundle: dict[str, Any]) -> bool:
    required = {str(item) for item in (bundle.get("required_capabilities", []) or [])}
    caps = {str(item) for item in (agent.get("capabilities", []) or [])}
    roles = {str(item) for item in (bundle.get("eligible_roles", []) or [])}
    role = str(agent.get("role") or "unspecified")
    return required.issubset(caps) and (not roles or role in roles)


def close_descendants(failed: str, dependencies: dict[str, Any]) -> set[str]:
    closure = {failed} if failed else set()
    changed = True
    while changed:
        changed = False
        for child, parents in dependencies.items():
            child = str(child)
            if any(str(parent) in closure for parent in (parents or [])) and child not in closure:
                closure.add(child)
                changed = True
    return closure


def json_key(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(k), json_key(v)) for k, v in sorted(value.items()))
    if isinstance(value, list):
        return tuple(json_key(v) for v in value)
    return value


def validate_row(row: dict[str, Any], source_file: str, line_no: int) -> dict[str, Any]:
    repair = row.get("repair") if isinstance(row.get("repair"), dict) else {}
    execution = repair.get("execution_record") if isinstance(repair.get("execution_record"), dict) else {}
    recorded = execution.get("structured_executor") if isinstance(execution.get("structured_executor"), dict) else {}
    metrics = row.get("initial_execution_record", {}).get("gym_metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("missing_initial_gym_metrics")
    contract = metrics.get("contract") if isinstance(metrics.get("contract"), dict) else {}
    bundles = as_bundles(contract)
    by_id = {str(item["bundle_id"]): item for item in bundles}
    progress = metrics.get("completed_waypoints_by_bundle", {})
    progress = progress if isinstance(progress, dict) else {}
    suffixes: dict[str, list[Any]] = {}
    for bundle_id, bundle in by_id.items():
        points = bundle.get("waypoints", []) if isinstance(bundle.get("waypoints"), list) else []
        try:
            done = max(0, int(progress.get(bundle_id, 0) or 0))
        except (TypeError, ValueError):
            done = 0
        suffix = points[min(done, len(points)):]
        if suffix:
            suffixes[bundle_id] = suffix
    mission = contract.get("mission") if isinstance(contract.get("mission"), dict) else {}
    dependencies = mission.get("bundle_dependencies", {}) if isinstance(mission.get("bundle_dependencies"), dict) else {}
    completed_ids = set(by_id) - set(suffixes)
    order = topological_order(bundles, set(suffixes), completed_ids, dependencies)
    failure = metrics.get("first_failure") if isinstance(metrics.get("first_failure"), dict) else {}
    details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
    policy = contract.get("failure_policy") if isinstance(contract.get("failure_policy"), dict) else {}
    failed = str(details.get("bundle_id") or policy.get("bundle_id") or "")
    state = failure.get("state") if isinstance(failure.get("state"), dict) else {}
    live_drones = metrics.get("final_drones") if isinstance(metrics.get("final_drones"), list) else []
    if not live_drones:
        live_drones = state.get("drones", []) if isinstance(state.get("drones"), list) else []
    agents, unavailable = agents_from_record(contract, live_drones)
    unavailable.update(str(x) for x in (metrics.get("unavailable_drone_ids", []) or []))
    unavailable.update(str(x) for x in (details.get("unavailable_drone_ids", []) or []))
    for aid, agent in agents.items():
        if str(agent.get("status") or "").lower() in {"unavailable", "failed", "lost", "offline"}:
            unavailable.add(aid)
    initial_assignment = metrics.get("bundle_assignment", {})
    initial_assignment = initial_assignment if isinstance(initial_assignment, dict) else {}
    current: dict[str, str] = {}
    candidate_map: dict[str, list[str]] = {}
    affected: list[str] = []
    reasons: dict[str, list[str]] = {}
    for bundle_id in order:
        bundle = by_id[bundle_id]
        owner = str(initial_assignment.get(bundle_id) or bundle.get("original_owner") or "")
        current[bundle_id] = owner
        why: list[str] = []
        if bundle_id == failed:
            why.append("failure_causal_source")
        if owner in unavailable:
            why.append("assigned_uav_unavailable")
        if owner not in agents:
            why.append("assigned_uav_unknown")
        elif not compatible(agents[owner], bundle):
            why.append("assigned_uav_role_or_capability_incompatible")
        if why:
            affected.append(bundle_id)
            reasons[bundle_id] = why
            candidate_map[bundle_id] = [
                aid for aid in sorted(agents)
                if aid not in unavailable and compatible(agents[aid], bundle)
            ]
            if not candidate_map[bundle_id]:
                raise ValueError(f"no_compatible_available_uav:{bundle_id}")
    if not affected:
        raise ValueError("failure_trace_did_not_identify_affected_bundle")
    descendants = close_descendants(failed, dependencies)
    downstream_owners = {current[bid] for bid in descendants if bid != failed and bid in current}
    positions: dict[str, list[float]] = {}
    for aid, agent in agents.items():
        point = xyz(agent.get("position")) or xyz(agent.get("start"))
        if point is not None:
            positions[aid] = point

    feasible: list[dict[str, Any]] = []
    affected_in_order = [bid for bid in order if bid in affected]
    candidate_lists = [candidate_map[bid] for bid in affected_in_order]
    for repl in itertools.product(*candidate_lists):
        assignment = dict(current)
        assignment.update(dict(zip(affected_in_order, repl)))
        reassigned = sum(assignment[bid] != current[bid] for bid in order)
        changed_actions = sum(len(suffixes[bid]) for bid in order if assignment[bid] != current[bid])
        cursors = {aid: list(point) for aid, point in positions.items()}
        total_cost = 0.0
        feasible_route = True
        for bid in order:
            aid = assignment[bid]
            waypoints = suffixes[bid]
            if aid not in cursors or not waypoints:
                feasible_route = False
                break
            total_cost += route_cost(cursors[aid], waypoints)
            endpoint = xyz(waypoints[-1])
            if endpoint is None:
                feasible_route = False
                break
            cursors[aid] = endpoint
        if not feasible_route:
            continue
        overlap = int(bool(failed and assignment.get(failed) in downstream_owners))
        key = (reassigned, changed_actions, round(total_cost, 12), overlap, tuple(assignment[bid] for bid in order))
        feasible.append({"assignment": assignment, "key": key, "route_cost": total_cost})
    if not feasible:
        raise ValueError("no_feasible_exhaustive_assignment")
    feasible.sort(key=lambda item: item["key"])
    best = feasible[0]
    selected_assignment = recorded.get("selected_assignment") if isinstance(recorded.get("selected_assignment"), dict) else {}
    selected_assignment = {str(k): str(v) for k, v in selected_assignment.items()}
    selected_item = next((item for item in feasible if all(selected_assignment.get(bid) == item["assignment"].get(bid) for bid in order)), None)
    recorded_graph = recorded.get("compatibility_graph") if isinstance(recorded.get("compatibility_graph"), dict) else {}
    recorded_graph = {str(k): [str(v) for v in (vals or [])] for k, vals in recorded_graph.items()}
    objective = recorded.get("objective") if isinstance(recorded.get("objective"), dict) else {}
    selected_key = selected_item["key"] if selected_item else None
    graph_match = json_key(recorded_graph) == json_key(candidate_map)
    objective_match = selected_key == best["key"] if selected_key is not None else False
    assignment_match = all(selected_assignment.get(bid) == best["assignment"].get(bid) for bid in order)
    selected_objective_fields = {
        "reassigned_bundle_count": objective.get("reassigned_bundle_count"),
        "changed_action_count": objective.get("changed_action_count"),
        "route_cost": objective.get("route_cost"),
        "downstream_owner_overlap": objective.get("downstream_owner_overlap"),
    }
    return {
        "source_file": source_file,
        "line_no": line_no,
        "task_id": row.get("task_id"),
        "source_task_id": row.get("source_task_id"),
        "run_seed": row.get("run_seed"),
        "run_seed_index": row.get("run_seed_index"),
        "failed_bundle_id": failed,
        "order": order,
        "affected_bundle_ids": affected,
        "candidate_graph_recomputed": candidate_map,
        "candidate_graph_recorded": recorded_graph,
        "candidate_graph_match": graph_match,
        "feasible_assignment_count": len(feasible),
        "oracle_assignment": best["assignment"],
        "selected_assignment": selected_assignment,
        "oracle_key": list(best["key"][:-1]) + [list(best["key"][-1])],
        "selected_key_recomputed": (list(selected_key[:-1]) + [list(selected_key[-1])]) if selected_key else None,
        "selected_objective_recorded": selected_objective_fields,
        "objective_match": objective_match,
        "assignment_match": assignment_match,
        "selected_rank": next((i + 1 for i, item in enumerate(feasible) if item is selected_item), None),
        "tie_count_at_oracle_key": sum(item["key"] == best["key"] for item in feasible),
        "solver_status_recorded": recorded.get("solver_status"),
        "pass": bool(graph_match and objective_match and assignment_match),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    files = sorted(args.input_root.glob("seed_*/cds_mr.jsonl"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen = 0
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                repair = row.get("repair") if isinstance(row.get("repair"), dict) else {}
                executor = repair.get("execution_record", {}).get("structured_executor") if isinstance(repair.get("execution_record"), dict) else None
                if repair.get("triggered") is not True or not isinstance(executor, dict):
                    continue
                if args.limit and seen >= args.limit:
                    break
                seen += 1
                try:
                    rows.append(validate_row(row, str(path), line_no))
                except Exception as exc:  # retain row-level diagnostics
                    errors.append({"source_file": str(path), "line_no": line_no, "task_id": row.get("task_id"), "error": f"{type(exc).__name__}:{exc}"})
            if args.limit and seen >= args.limit:
                break
    summary = {
        "input_root": str(args.input_root),
        "source_files": [str(path) for path in files],
        "recoverable_rows_seen": seen,
        "validated_rows": len(rows),
        "error_rows": len(errors),
        "candidate_graph_match_count": sum(bool(row["candidate_graph_match"]) for row in rows),
        "objective_match_count": sum(bool(row["objective_match"]) for row in rows),
        "assignment_match_count": sum(bool(row["assignment_match"]) for row in rows),
        "rank_one_count": sum(row.get("selected_rank") == 1 for row in rows),
        "all_pass": bool(rows) and not errors and all(row["pass"] for row in rows),
    }
    (args.output_dir / "row_results.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    (args.output_dir / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# CDS-MR exhaustive optimality audit", "", f"- Recoverable rows seen: {seen}", f"- Validated rows: {len(rows)}", f"- Error rows: {len(errors)}", f"- Candidate graph matches: {summary['candidate_graph_match_count']}/{len(rows)}", f"- Objective matches: {summary['objective_match_count']}/{len(rows)}", f"- Assignment matches: {summary['assignment_match_count']}/{len(rows)}", f"- Rank-one selections: {summary['rank_one_count']}/{len(rows)}", f"- All pass: {summary['all_pass']}", ""]
    if errors:
        lines += ["## Errors", ""] + [f"- `{item['source_file']}:{item['line_no']}` — {item['error']}" for item in errors]
    failures = [row for row in rows if not row["pass"]]
    if failures:
        lines += ["## Non-passing rows", ""] + [f"- `{row['source_file']}:{row['line_no']}` task `{row['task_id']}`: graph={row['candidate_graph_match']}, objective={row['objective_match']}, assignment={row['assignment_match']}, rank={row['selected_rank']}" for row in failures]
    (args.output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
