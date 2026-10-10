"""Build deterministic suffix-coupling stress tests from frozen v1 templates.

The calibration tasks keep deadlines loose.  Final tasks set the B3 deadline
from an explicit, preregistered midpoint rule applied to paired calibration
completion steps; no task geometry or candidate set changes after calibration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


CASE_IDS = (
    "outcome_stress_route_a",
    "outcome_stress_route_b_mirror",
    "outcome_stress_branch",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def set_geometry(task: dict[str, Any], *, mirror: bool, branch: bool) -> None:
    sx = -1.0 if mirror else 1.0
    starts = {
        "uav_0": [sx * 0.0, 0.5, 1.0],
        "uav_1": [sx * 0.2, 0.5, 1.0],
        "uav_2": [sx * 0.0, 0.0, 1.0],
        "uav_3": [sx * 0.9, 0.0, 1.0],
    }
    paths = {
        "B1": [[sx * 0.0, 0.5, 1.0], [sx * 0.1, 0.5, 1.0]],
        "B2": [[sx * 0.2, 0.5, 1.0], [sx * 0.2, 0.2, 1.0], [sx * 0.4, 0.0, 1.0]],
        "B3": [[sx * 0.0, 0.0, 1.0], [sx * -0.4, 0.0, 1.0]],
        "B4": [[sx * 0.9, 0.0, 1.0], [sx * 0.45, 0.0, 1.0]],
    }
    for agent in task["agents"]:
        agent["start"] = starts[str(agent["agent_id"])]
        agent["extension_start_provenance"] = "preregistered_outcome_stress_geometry_v1"
    mission = task["contract"]["mission"]
    bundles = sorted(mission["task_bundles"].values(), key=lambda b: int(b["order"]))
    required: list[list[float]] = []
    cursor = 0
    for bundle in bundles:
        bid = str(bundle["bundle_id"])
        bundle["waypoints"] = paths[bid]
        bundle["waypoint_indices"] = list(range(cursor, cursor + len(paths[bid])))
        bundle["deadline_s"] = 600.0
        bundle["repair_policy"] = "outcome_discriminating_stress_v1"
        required.extend(paths[bid])
        cursor += len(paths[bid])
    mission["required_waypoint_sequence"] = required
    mission["waypoint_tolerance"] = 0.02
    mission["bundle_dependencies"] = (
        {"B2": ["B1"], "B3": ["B1"], "B4": ["B2", "B3"]}
        if branch
        else {"B2": ["B1"], "B3": ["B2"], "B4": ["B3"]}
    )
    resources = task["contract"]["resources"]
    resources.update({"max_total_steps": 2400, "base_step_budget": 1200, "repair_step_budget": 1200, "time_budget_s": 600.0})
    failure = task["contract"]["failure_policy"]
    failure.update({"bundle_id": "B2", "drone_id": "uav_1", "after_completed_waypoints": 2})
    if isinstance(task.get("scenario"), dict):
        task["scenario"]["failure_profile"] = copy.deepcopy(failure)


def calibration_rows(source: Path) -> list[dict[str, Any]]:
    templates = read_jsonl(source)
    by_id = {str(row["task_id"]): row for row in templates}
    selected = (
        ("mechanism_stress_S1_case_a", CASE_IDS[0], False, False),
        ("mechanism_stress_S1_case_b_mirror", CASE_IDS[1], True, False),
        ("mechanism_stress_S2_branch", CASE_IDS[2], False, True),
    )
    rows: list[dict[str, Any]] = []
    for index, (source_id, task_id, mirror, branch) in enumerate(selected, start=1):
        task = copy.deepcopy(by_id[source_id])
        task["task_id"] = task_id
        task["instruction"] = "Deterministic suffix-coupling mechanism stress test."
        metadata = task.setdefault("metadata", {})
        metadata.update({
            "source_name": "outcome_discriminating_stress_v1",
            "source_task_id": task_id,
            "run_seed_index": index,
            "run_seed": 9200 + index,
            "mechanism_stress": "suffix_coupling_branch" if branch else "suffix_coupling_route",
            "population_claim": False,
            "calibration_phase": True,
        })
        set_geometry(task, mirror=mirror, branch=branch)
        rows.append(task)
    return rows


def raw_by_task(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row["task_id"]): row for row in read_jsonl(path)}


def final_rows(calibration_tasks: Path, simple_raw: Path, cds_raw: Path, ctrl_freq: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tasks = read_jsonl(calibration_tasks)
    simple = raw_by_task(simple_raw)
    cds = raw_by_task(cds_raw)
    audit: dict[str, Any] = {"rule": "B3 deadline step=floor((CDS B3 completion step + simple B3 completion step)/2), converted by ctrl_freq", "ctrl_freq": ctrl_freq, "cases": {}}
    output: list[dict[str, Any]] = []
    for task in tasks:
        tid = str(task["task_id"])
        s_final = simple[tid]["final_execution_record"]["gym_metrics"]
        c_final = cds[tid]["final_execution_record"]["gym_metrics"]
        s_step = int(s_final["bundle_completion_step"]["B3"])
        c_step = int(c_final["bundle_completion_step"]["B3"])
        if not c_step < s_step:
            raise RuntimeError(f"calibration_not_discriminating:{tid}:cds={c_step}:simple={s_step}")
        deadline_step = (c_step + s_step) // 2
        if not c_step <= deadline_step < s_step:
            raise RuntimeError(f"invalid_midpoint:{tid}")
        final = copy.deepcopy(task)
        final["task_id"] = tid + "_deadline"
        final["metadata"]["source_task_id"] = final["task_id"]
        final["metadata"]["calibration_phase"] = False
        final["metadata"]["deadline_rule"] = "preregistered_midpoint_of_loose_calibration_B3_completion_steps"
        for bundle in final["contract"]["mission"]["task_bundles"].values():
            if str(bundle["bundle_id"]) == "B3":
                bundle["deadline_s"] = deadline_step / float(ctrl_freq)
        audit["cases"][tid] = {
            "simple_B3_completion_step": s_step,
            "cds_B3_completion_step": c_step,
            "deadline_step": deadline_step,
            "deadline_s": deadline_step / float(ctrl_freq),
        }
        output.append(final)
    return output, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("calibration", "final"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--simple-raw", type=Path)
    parser.add_argument("--cds-raw", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--ctrl-freq", type=int, default=15)
    args = parser.parse_args()
    if args.mode == "calibration":
        rows = calibration_rows(args.source)
        audit = {"phase": "calibration", "case_count": len(rows)}
    else:
        if args.simple_raw is None or args.cds_raw is None:
            parser.error("final mode requires --simple-raw and --cds-raw")
        rows, audit = final_rows(args.source, args.simple_raw, args.cds_raw, args.ctrl_freq)
    write_jsonl(args.output, rows)
    audit.update({"output": str(args.output), "sha256": sha256(args.output)})
    if args.audit:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
