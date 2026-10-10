"""Audit paired physical execution of the two boundary controls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def path_length(row: dict) -> float:
    return sum(
        float(point.get("path_length_m") or 0.0)
        for command in row["final_execution_record"]["structured_commands"]
        for point in command["response"].get("waypoint_results", [])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--design-audit", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tasks = {task["task_id"]: task for task in read_jsonl(args.tasks)}
    design = json.loads(args.design_audit.read_text(encoding="utf-8"))
    manifest = json.loads((args.results / "run_manifest.json").read_text(encoding="utf-8"))
    paired = json.loads((args.results / "pair_audit.json").read_text(encoding="utf-8"))
    simple = {row["task_id"]: row for row in read_jsonl(args.results / "Simple_local_reassignment.jsonl")}
    cds = {row["task_id"]: row for row in read_jsonl(args.results / "CDS_MR.jsonl")}
    cases = []
    for task_id in sorted(tasks):
        s, c = simple[task_id], cds[task_id]
        st = s["final_execution_record"]["gym_metrics"]["sim_time_seconds"]
        ct = c["final_execution_record"]["gym_metrics"]["sim_time_seconds"]
        sp, cp = path_length(s), path_length(c)
        cases.append({
            "task_id": task_id,
            "condition": tasks[task_id]["metadata"]["boundary_condition"],
            "simple_owner": s["repair"]["solver_audit"]["selected_assignment"]["B2"],
            "cds_owner": c["repair"]["solver_audit"]["selected_assignment"]["B2"],
            "simple_success": s["final_execution_record"]["gym_metrics"]["task_success"],
            "cds_success": c["final_execution_record"]["gym_metrics"]["task_success"],
            "simple_strict": s["standard_metrics"]["strict_contract_satisfied"],
            "cds_strict": c["standard_metrics"]["strict_contract_satisfied"],
            "simple_time_s": round(st, 6),
            "cds_time_s": round(ct, 6),
            "time_difference_s": round(st - ct, 6),
            "simple_path_m": round(sp, 6),
            "cds_path_m": round(cp, 6),
            "path_difference_m": round(sp - cp, 6),
        })
    checks = {
        "frozen_input_hash": hashlib.sha256(args.tasks.read_bytes()).hexdigest() == manifest["source_input_sha256"] == design["task_sha256"],
        "six_paired_cases": len(cases) == 6 and paired["complete_pairs"] == 6,
        "same_failure_time_state": paired["status"] == "PASS" and paired["same_failure_time_state_count"] == 6,
        "all_recovered_and_strict": all(case["simple_success"] and case["cds_success"] and case["simple_strict"] and case["cds_strict"] for case in cases),
        "tie_has_no_selection_or_execution_gain": all(case["simple_owner"] == case["cds_owner"] == "uav_2" and abs(case["time_difference_s"]) < 1e-6 and abs(case["path_difference_m"]) < 1e-6 for case in cases if case["condition"] == "tie"),
        "local_sufficient_selects_same_owner": all(case["simple_owner"] == case["cds_owner"] == "uav_2" and abs(case["time_difference_s"]) < 1e-6 and abs(case["path_difference_m"]) < 1e-6 for case in cases if case["condition"] == "local_sufficient"),
    }
    report = {"protocol": "mechanism_boundary_v1", "verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "cases": cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
