"""Validate frozen geometry and, optionally, paired execution results."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def xyz(value: Any) -> list[float]:
    return [float(item) for item in value]


def route_cost(start: list[float], points: list[list[float]]) -> float:
    if not points:
        return 0.0
    return math.dist(start, points[0]) + sum(math.dist(left, right) for left, right in zip(points, points[1:]))


def frozen_geometry(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    per_case: list[dict[str, Any]] = []
    for task in tasks:
        bundles = {
            str(bundle["bundle_id"]): bundle
            for bundle in task["contract"]["mission"]["task_bundles"].values()
        }
        agents = {str(agent["agent_id"]): agent for agent in task["agents"]}
        tolerance = float(task["contract"]["mission"]["waypoint_tolerance"])
        remaining_b2 = [xyz(bundles["B2"]["waypoints"][2])]
        c2 = route_cost(xyz(agents["uav_2"]["start"]), remaining_b2)
        c3 = route_cost(xyz(agents["uav_3"]["start"]), remaining_b2)
        simple = (
            c2
            + route_cost(remaining_b2[-1], [xyz(p) for p in bundles["B3"]["waypoints"]])
            + route_cost(xyz(agents["uav_3"]["start"]), [xyz(p) for p in bundles["B4"]["waypoints"]])
        )
        cds = (
            c3
            + route_cost(xyz(agents["uav_2"]["start"]), [xyz(p) for p in bundles["B3"]["waypoints"]])
            + route_cost(remaining_b2[-1], [xyz(p) for p in bundles["B4"]["waypoints"]])
        )
        legs = []
        for bundle in bundles.values():
            points = [xyz(point) for point in bundle["waypoints"]]
            legs.extend(math.dist(left, right) for left, right in zip(points, points[1:]))
        case = {
            "task_id": task["task_id"],
            "tolerance": tolerance,
            "min_intra_bundle_spacing": min(legs),
            "spacing_ratio": min(legs) / tolerance,
            "c2_uav_2": round(c2, 9),
            "c3_uav_3": round(c3, 9),
            "simple_suffix_cost": round(simple, 9),
            "cds_suffix_cost": round(cds, 9),
            "relative_reduction": round((simple - cds) / simple, 9),
            "simple_expected_owner": "uav_2",
            "cds_expected_owner": "uav_3",
        }
        per_case.append(case)
    checks["all_spacing_gt_4x_tolerance"] = all(item["spacing_ratio"] > 4.0 for item in per_case)
    checks["all_direct_choices_local_uav2"] = all(item["c2_uav_2"] < item["c3_uav_3"] for item in per_case)
    checks["all_suffix_choices_cds_uav3"] = all(item["simple_suffix_cost"] > item["cds_suffix_cost"] for item in per_case)
    checks["all_relative_reductions_positive"] = all(item["relative_reduction"] > 0 for item in per_case)
    return {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "cases": per_case}


def result_audit(results_dir: Path) -> dict[str, Any]:
    methods = {
        "Simple-local-reassignment": results_dir / "Simple_local_reassignment.jsonl",
        "CDS-MR": results_dir / "CDS_MR.jsonl",
    }
    rows = {name: {str(row["task_id"]): row for row in read_jsonl(path)} for name, path in methods.items()}
    cases = []
    for task_id in sorted(set(rows["Simple-local-reassignment"]) & set(rows["CDS-MR"])):
        simple = rows["Simple-local-reassignment"][task_id]
        cds = rows["CDS-MR"][task_id]
        sa = (simple.get("repair") or {}).get("solver_audit") or {}
        ca = (cds.get("repair") or {}).get("solver_audit") or {}
        sm = simple.get("standard_metrics") or {}
        cm = cds.get("standard_metrics") or {}
        sg = ((simple.get("final_execution_record") or {}).get("gym_metrics") or {})
        cg = ((cds.get("final_execution_record") or {}).get("gym_metrics") or {})
        cases.append(
            {
                "task_id": task_id,
                "simple_owner": sa.get("selected_assignment", {}).get("B2"),
                "cds_owner": ca.get("selected_assignment", {}).get("B2"),
                "simple_strict_contract": sm.get("strict_contract_satisfied"),
                "cds_strict_contract": cm.get("strict_contract_satisfied"),
                "simple_task_success": sm.get("task_success", sg.get("task_success")),
                "cds_task_success": cm.get("task_success", cg.get("task_success")),
            }
        )
    checks = {
        "paired_rows": len(cases) == 3,
        "simple_selects_uav2": all(case["simple_owner"] == "uav_2" for case in cases),
        "cds_selects_uav3": all(case["cds_owner"] == "uav_3" for case in cases),
        "all_strict_contracts": all(case["simple_strict_contract"] and case["cds_strict_contract"] for case in cases),
        "all_task_success": all(case["simple_task_success"] and case["cds_task_success"] for case in cases),
    }
    return {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    report = {"protocol": "mechanism_stress_v2", "frozen_geometry": frozen_geometry(read_jsonl(args.tasks))}
    if args.results:
        report["execution"] = result_audit(args.results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
