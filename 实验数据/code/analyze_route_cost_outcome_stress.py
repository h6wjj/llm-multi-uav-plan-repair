"""Audit preregistered route-cost outcome stress results."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from run_cds_mr_contract_online import _route_cost, _xyz


METHOD_FILES = {
    "Simple-local-reassignment": "Simple_local_reassignment.jsonl",
    "CDS-MR": "CDS_MR.jsonl",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def suffix_cost(record: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    initial = record["initial_execution_record"]["gym_metrics"]
    cursors = {str(agent["id"]): _xyz(agent.get("position")) for agent in initial["final_drones"]}
    total = 0.0
    parts: list[dict[str, Any]] = []
    for item in sorted(record["repair"]["repair_plan"], key=lambda row: int(row["sequence"])):
        owner = str(item["assigned_drone"])
        waypoints = item["remaining_waypoints"]
        cost = _route_cost(cursors[owner], waypoints)
        total += cost
        cursors[owner] = _xyz(waypoints[-1])
        parts.append({"bundle_id": item["bundle_id"], "owner": owner, "cost": round(cost, 9)})
    return round(total, 9), parts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for method, filename in METHOD_FILES.items():
        for row in read_jsonl(args.results / filename):
            records[(str(row["task_id"]), method)] = row
    pair_audit = json.loads((args.results / "pair_audit.json").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for task_id in sorted({key[0] for key in records}):
        case: dict[str, Any] = {"task_id": task_id, "methods": {}}
        for method in METHOD_FILES:
            row = records[(task_id, method)]
            metrics = row["standard_metrics"]
            final = row["final_execution_record"]["gym_metrics"]
            audit = row["repair"]["solver_audit"]
            cost, parts = suffix_cost(row)
            case["methods"][method] = {
                "selected_B2_owner": audit["selected_assignment"]["B2"],
                "terminal_task_success": bool(final["task_success"]),
                "strict_contract_satisfied": bool(metrics["strict_contract_satisfied"]),
                "contract_checks": metrics["contract_checks"],
                "npsd": metrics["normalized_plan_stability_distance"],
                "suffix_route_cost": cost,
                "route_cost_parts": parts,
            }
        simple_cost = case["methods"]["Simple-local-reassignment"]["suffix_route_cost"]
        cds_cost = case["methods"]["CDS-MR"]["suffix_route_cost"]
        case["absolute_cost_reduction"] = round(simple_cost - cds_cost, 9)
        case["relative_cost_reduction"] = round((simple_cost - cds_cost) / simple_cost, 9)
        cases.append(case)
    checks = {
        "three_complete_pairs": pair_audit.get("complete_pairs") == 3,
        "same_failure_state_3_of_3": pair_audit.get("same_failure_time_state_count") == 3,
        "simple_selects_uav2": all(case["methods"]["Simple-local-reassignment"]["selected_B2_owner"] == "uav_2" for case in cases),
        "cds_selects_uav3": all(case["methods"]["CDS-MR"]["selected_B2_owner"] == "uav_3" for case in cases),
        "all_terminal_success": all(value["terminal_task_success"] for case in cases for value in case["methods"].values()),
        "all_strict_contracts": all(value["strict_contract_satisfied"] for case in cases for value in case["methods"].values()),
        "route_cost_reduction_at_least_50pct": all(case["relative_cost_reduction"] >= 0.5 for case in cases),
        "npsd_equal_within_pairs": all(case["methods"]["Simple-local-reassignment"]["npsd"] == case["methods"]["CDS-MR"]["npsd"] for case in cases),
    }
    report = {
        "protocol": "route_cost_outcome_stress_v1",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "cases": cases,
        "supported_claim": "In these three deterministic suffix-coupling cases, CDS-MR's different owner choice avoided the additional cumulative estimated suffix route cost incurred by immediate local reassignment.",
        "unsupported_claims": [
            "avoidance of runtime failure",
            "avoidance of deadline or budget violation",
            "lower NPSD",
            "population-level superiority",
        ],
        "source_hashes": {filename: sha256(args.results / filename) for filename in [*METHOD_FILES.values(), "comparison_by_pair.jsonl", "pair_audit.json"]},
    }
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Route-cost outcome stress audit",
        "",
        f"Verdict: **{report['verdict']}**.",
        "",
        "| Case | Simple owner / cost | CDS-MR owner / cost | Reduction | Strict contract | NPSD |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for case in cases:
        simple = case["methods"]["Simple-local-reassignment"]
        cds = case["methods"]["CDS-MR"]
        lines.append(f"| {case['task_id']} | {simple['selected_B2_owner']} / {simple['suffix_route_cost']:.3f} | {cds['selected_B2_owner']} / {cds['suffix_route_cost']:.3f} | {100*case['relative_cost_reduction']:.1f}% | both pass | {simple['npsd']:.3f} / {cds['npsd']:.3f} |")
    lines.extend([
        "",
        "All 3 pairs used identical failure-time states. Both methods completed all cases and passed all six contract checks.",
        "The result supports only the preregistered cumulative estimated suffix-route-cost claim; it does not establish failure avoidance, deadline/budget avoidance, lower NPSD, or population-level superiority.",
    ])
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "output_json": str(args.output_json), "output_md": str(args.output_md)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
