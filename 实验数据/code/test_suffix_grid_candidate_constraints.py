"""Small read-only regression checks for the shared replacement candidate set."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from global_suffix_reallocation import global_total_route_plan
from run_cds_mr_contract_online import _structured_repair_plan
from run_simple_local_vs_cds_mr import simple_local_reassignment_plan


def first(path: Path) -> dict:
    return json.loads(next(line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    task = first(args.tasks)
    row = first(args.results / "Simple_local_reassignment.jsonl")
    initial = row["initial_execution_record"]["gym_metrics"]
    dynamic = row["dynamic_slice"]
    methods = (
        simple_local_reassignment_plan,
        _structured_repair_plan,
        global_total_route_plan,
    )

    only_u3 = copy.deepcopy(task)
    only_u3["contract"]["mission"]["task_bundles"]["uav_1"]["eligible_drone_ids"] = ["uav_3"]
    selected = []
    for solver in methods:
        plan, _ = solver(only_u3, initial, dynamic)
        selected.append(next(item["assigned_drone"] for item in plan if item["bundle_id"] == "B2"))
    assert selected == ["uav_3"] * 3, selected

    impossible = copy.deepcopy(task)
    impossible["contract"]["mission"]["task_bundles"]["uav_1"]["eligible_drone_ids"] = ["uav_0"]
    rejected = []
    for solver in methods:
        try:
            solver(impossible, initial, dynamic)
        except RuntimeError:
            rejected.append(True)
        else:
            rejected.append(False)
    assert rejected == [True] * 3, rejected
    print(json.dumps({"verdict": "PASS", "only_u3_selected": selected, "impossible_rejected": rejected}, indent=2))


if __name__ == "__main__":
    main()
