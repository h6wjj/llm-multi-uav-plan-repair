"""Post-hoc offline control on frozen failure snapshots; no simulator calls.

Enumerate all compatible remaining-owner assignments independently of CDS-MR's
affected set. Reuse input parsing/qualification conventions, not its selector.
Planned NPSD is a counterfactual plan metric, NOT executed NPSD or success.
"""
from __future__ import annotations

import hashlib
import ast
import itertools
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

# Load unchanged pure function ASTs, excluding HTTP/process imports. The local
# archive lacks an upstream online service module; no stub or fake module is used.
PURE_NAMES = {"_compatible", "_dynamic_slice", "_live_agent_view", "_ordered_bundles",
              "_quantized_xyz", "_structured_repair_plan", "_topological_remaining_order",
              "_xyz", "_route_cost"}
SOURCE = Path(__file__).with_name("run_cds_mr_contract_online.py")
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in PURE_NAMES]
assert {n.name for n in nodes} == PURE_NAMES
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), globals())

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "实验数据/results/offline_stability_control_v1"
DATASETS = ["suffix_grid_three_way_formal_v1", "b3_independent_task_distribution_formal_v1"]


def read_rows(path):
    rows = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]
    result = {r["task_id"]: r for r in rows}
    assert len(result) == len(rows), f"duplicate task IDs: {path}"
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_assignment(plan):
    return {p["bundle_id"]: p["assigned_drone"] for p in plan}


def score(order, assignment, current, suffixes, positions, downstream, failed):
    cursors = {a: list(p) for a, p in positions.items()}
    route = 0.0
    reference, repaired = set(), set()
    for bid in order:
        owner = assignment[bid]
        for point in suffixes[bid]["remaining_waypoints"]:
            xyz = _xyz(point)
            assert xyz is not None
            route += math.dist(cursors[owner], xyz)
            cursors[owner] = xyz
            q = _quantized_xyz(point)
            reference.add(("MOVE_PATH", current[bid], bid, q))
            repaired.add(("MOVE_PATH", owner, bid, q))
    changed = [b for b in order if assignment[b] != current[b]]
    union = reference | repaired
    return {
        "assignment": assignment,
        "bundle_edits": len(changed),
        "action_edits": sum(len(suffixes[b]["remaining_waypoints"]) for b in changed),
        "route_cost": route,
        "downstream_overlap": int(assignment.get(failed) in downstream),
        "owner_tuple": [assignment[b] for b in order],
        "planned_npsd": len(reference ^ repaired) / len(union) if union else 0.0,
    }


def edit_key(c):
    return (c["bundle_edits"], c["action_edits"], round(c["route_cost"], 12),
            c["downstream_overlap"], tuple(c["owner_tuple"]))


def npsd_key(c):
    return (c["planned_npsd"], round(c["route_cost"], 12),
            c["downstream_overlap"], tuple(c["owner_tuple"]))


def enumerate_global(task, metrics):
    dynamic = _dynamic_slice(task, {"gym_metrics": metrics})
    assert dynamic is not None
    bundles = _ordered_bundles(task)
    by_id = {b["bundle_id"]: b for b in bundles}
    suffixes = {s["bundle_id"]: s for s in dynamic["remaining_bundle_suffixes"]}
    progress = metrics["completed_waypoints_by_bundle"]
    completed = {b for b in by_id if progress.get(b, 0) >= len(by_id[b]["waypoints"])}
    dependencies = task["contract"]["mission"].get("bundle_dependencies", {})
    order = _topological_remaining_order(bundles, set(suffixes), completed, dependencies)
    agents, unavailable = _live_agent_view(task, metrics, dynamic)
    positions = {a: _xyz(v.get("position")) or _xyz(v.get("start")) for a, v in agents.items()}
    current = {b: str(metrics.get("bundle_assignment", {}).get(b) or suffixes[b]["assigned_drone"])
               for b in order}
    failure = dynamic["first_failure"]
    failed = str(failure.get("details", {}).get("bundle_id") or
                 task["contract"].get("failure_policy", {}).get("bundle_id") or "")
    failed_owner = failure.get("drone_id")
    descendants = {failed}
    while True:
        expanded = descendants | {b for b in order if set(dependencies.get(b, [])) & descendants}
        if expanded == descendants:
            break
        descendants = expanded
    downstream = {current[b] for b in descendants if b in current and b != failed}
    options = {}
    for b in order:
        bundle = by_id[b]
        eligible = set(bundle.get("eligible_drone_ids") or agents)
        options[b] = sorted(a for a in agents if a in eligible and a not in unavailable
                            and positions[a] is not None and _compatible(agents[a], bundle)
                            and not (b == failed and a == failed_owner))
        if not bundle.get("assignment_is_decision_variable", True):
            options[b] = [current[b]] if current[b] in options[b] else []
        if not options[b]:
            raise ValueError("No static candidate: " + b)
    candidates = [score(order, dict(zip(order, owners)), current, suffixes, positions, downstream, failed)
                  for owners in itertools.product(*(options[b] for b in order))]
    return candidates, options, dynamic


def main():
    # New directory required; never silently overwrite an earlier audit.
    OUT.mkdir(parents=True, exist_ok=False)
    hashes, results, summaries = {}, [], {}
    for dataset in DATASETS:
        folder = ROOT / "实验数据/results" / dataset
        paths = {"tasks": folder / "tasks_recoverable_failure_v1.jsonl",
                 "cds": folder / "CDS_MR.jsonl",
                 "route": folder / "Global_total_route_reallocation.jsonl"}
        data = {k: read_rows(p) for k, p in paths.items()}
        assert set(data["tasks"]) == set(data["cds"]) == set(data["route"])
        assert len(data["tasks"]) == 90
        for p in paths.values():
            hashes[str(p.relative_to(ROOT))] = digest(p)
        rows = []
        for tid, task in data["tasks"].items():
            record = data["cds"][tid]
            metrics = record["initial_execution_record"]["gym_metrics"]
            candidates, options, dynamic = enumerate_global(task, metrics)
            stable = min(candidates, key=edit_key)
            npsd = min(candidates, key=npsd_key)
            route = min(candidates, key=lambda c: (round(c["route_cost"], 12), tuple(c["owner_tuple"])))
            # Read observed outputs only AFTER counterfactual selection.
            observed = plan_assignment(record["repair"]["repair_plan"])
            matches = [c for c in candidates if c["assignment"] == observed]
            assert len(matches) == 1, "CDS output not in independently enumerated domain"
            cds = matches[0]
            replay, replay_audit = _structured_repair_plan(task, metrics, dynamic)
            global_observed = plan_assignment(data["route"][tid]["repair"]["repair_plan"])
            assert route["assignment"] == global_observed, "route control replay mismatch"
            assert plan_assignment(replay) == observed, "CDS replay mismatch"
            assert abs(cds["route_cost"] - record["repair"]["estimated_full_suffix_route_cost"]) < 1e-8
            row = {
                "dataset": dataset, "task_id": tid, "candidate_graph": options,
                "global_candidate_count": len(candidates),
                "cds_candidate_count": math.prod(len(x) for x in replay_audit["compatibility_graph"].values()),
                "all_candidates": candidates,
                "edit_first_global": stable, "npsd_first_global": npsd, "cds": cds,
                "same_assignment_edit_first": stable["assignment"] == observed,
                "same_objective_edit_first": edit_key(stable) == edit_key(cds),
                "same_assignment_npsd_first": npsd["assignment"] == observed,
                "edit_first_primary_optimum_count": sum(edit_key(c)[:3] == edit_key(stable)[:3] for c in candidates),
                "cds_replay_matches": True, "route_replay_matches": True,
                "planned_npsd_matches_recorded_cds": abs(cds["planned_npsd"] - record["standard_metrics"]["normalized_plan_stability_distance"]) < 1e-6,
                "new_simulation_performed": False,
            }
            rows.append(row)
        summaries[dataset] = {
            "n": len(rows),
            **{k: sum(r[k] for r in rows) for k in ["same_assignment_edit_first", "same_objective_edit_first",
                "same_assignment_npsd_first", "cds_replay_matches", "route_replay_matches", "planned_npsd_matches_recorded_cds"]},
            "global_candidate_counts": dict(Counter(r["global_candidate_count"] for r in rows)),
            "cds_candidate_counts": dict(Counter(r["cds_candidate_count"] for r in rows)),
        }
        results.extend(rows)
    for name, before in hashes.items():
        assert digest(ROOT / name) == before, "input mutated"
    summary = {
        "protocol": "offline_stability_control_v1", "evaluation_type": "posthoc_offline_frozen_snapshot_enumeration",
        "selection_inputs": ["task_contract", "initial_failure_metrics"],
        "selection_uses_simulator_outcome": False, "new_simulation_performed": False,
        "runtime_success_and_time_for_new_control": "not_measured",
        "optimality_scope": "static_compatible_owner_assignments_fixed_suffix_and_topological_order; no dynamic deadline/budget search",
        "edit_objective": ["bundle_edits", "action_edits", "route_cost", "downstream_overlap", "owner_tuple"],
        "npsd_objective": ["planned_npsd", "route_cost", "downstream_overlap", "owner_tuple"],
        "datasets": summaries, "input_sha256": hashes,
        "code_sha256": {str(p.relative_to(ROOT)): digest(p) for p in [Path(__file__), Path(__file__).with_name("run_cds_mr_contract_online.py")]},
    }
    (OUT / "by_task.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
