"""Summarize the preregistered Blind-versus-SeSE selection experiment."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def std(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else (0.0 if values else None)


def describe(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return {"n": len(values), "mean": mean(values), "std": std(values)}


def bootstrap_ci(values: list[float], iterations: int = 20000) -> list[float | None]:
    if not values:
        return [None, None]
    rng = random.Random(20260831)
    samples = [statistics.fmean(rng.choice(values) for _ in values) for _ in range(iterations)]
    samples.sort()
    return [samples[int(0.025 * (iterations - 1))], samples[int(0.975 * (iterations - 1))]]


def sign_flip_p(values: list[float], iterations: int = 100000) -> float | None:
    nonzero = [value for value in values if abs(value) > 1e-15]
    if not nonzero:
        return 1.0 if values else None
    observed = abs(statistics.fmean(nonzero))
    if len(nonzero) <= 20:
        total = 0
        extreme = 0
        for signs in itertools.product((-1.0, 1.0), repeat=len(nonzero)):
            total += 1
            value = abs(statistics.fmean(sign * item for sign, item in zip(signs, nonzero)))
            extreme += int(value >= observed - 1e-15)
        return extreme / total
    rng = random.Random(20260831)
    extreme = 0
    for _ in range(iterations):
        value = abs(statistics.fmean((1.0 if rng.random() >= 0.5 else -1.0) * item for item in nonzero))
        extreme += int(value >= observed - 1e-15)
    return (extreme + 1) / (iterations + 1)


def paired_result(pairs: list[tuple[dict[str, Any], dict[str, Any]]], field: str) -> dict[str, Any]:
    differences = []
    for blind, sese in pairs:
        if blind.get(field) is None or sese.get(field) is None:
            continue
        differences.append(float(sese[field]) - float(blind[field]))
    deviation = std(differences)
    return {
        "field": field,
        "direction": "SeSE-Blind",
        "n": len(differences),
        "mean_difference": mean(differences),
        "bootstrap_95_ci": bootstrap_ci(differences),
        "paired_sign_flip_p": sign_flip_p(differences),
        "cohen_dz": (statistics.fmean(differences) / deviation) if differences and deviation not in (None, 0.0) else None,
    }


def fmt(value: Any, digits: int = 4) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pools", type=Path, required=True)
    parser.add_argument("--executions", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pools = read_jsonl(args.pools)
    executions = read_jsonl(args.executions)
    selections = read_jsonl(args.selections)
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in selections:
        key = (str(row.get("source_task_id")), int(row.get("run_seed_index")))
        by_key.setdefault(key, {})[str(row.get("selector"))] = row
    pairs = [(arms["Blind"], arms["SeSE"]) for arms in by_key.values() if set(arms) == {"Blind", "SeSE"}]
    fields = [
        "initial_plan_contract_valid",
        "initial_plan_contract_satisfaction_rate",
        "independent_selection_utility",
        "selection_regret",
        "actual_initial_execution_success",
        "actual_contract_satisfaction_rate",
        "actual_mission_completion_time_seconds",
        "initial_planning_latency_seconds",
        "initial_planning_token_cost",
    ]
    arm_summary = {}
    for selector in ("Blind", "SeSE"):
        rows = [row for row in selections if row.get("selector") == selector]
        arm_summary[selector] = {field: describe(rows, field) for field in fields}
        arm_summary[selector]["promotion_rate"] = mean([float(bool(row.get("gate_promoted"))) for row in rows])
        arm_summary[selector]["abstention_rate"] = mean([float(bool(row.get("gate_abstained"))) for row in rows])
        arm_summary[selector]["rows"] = len(rows)
    paired = {field: paired_result(pairs, field) for field in fields}
    paired["selection_gain_reported"] = describe([sese for _, sese in pairs], "selection_gain")
    signature_counts = [int(row.get("candidate_signature_count") or 0) for row in selections if row.get("selector") == "SeSE"]
    validation = {
        "protocol_version": manifest.get("protocol_version"),
        "expected_task_seed_units": manifest.get("task_seed_units"),
        "expected_selection_records": manifest.get("expected_selection_records"),
        "pool_rows": len(pools),
        "completed_pool_rows": sum(row.get("runner_status") == "completed" for row in pools),
        "execution_rows": len(executions),
        "unique_execution_keys": len({(row.get("task_id"), row.get("candidate_index")) for row in executions}),
        "selection_rows": len(selections),
        "paired_units": len(pairs),
        "missing_selector_pairs": [list(key) for key, arms in by_key.items() if set(arms) != {"Blind", "SeSE"}],
        "shared_pool_id_mismatches": sum(blind.get("pool_id") != sese.get("pool_id") for blind, sese in pairs),
        "llm_call_count_mismatches": sum(blind.get("llm_call_count") != sese.get("llm_call_count") for blind, sese in pairs),
        "future_failure_information_violations": sum(bool(row.get("future_failure_information_used")) for row in selections),
        "candidate_signature_count_min": min(signature_counts) if signature_counts else None,
        "candidate_signature_count_mean": mean([float(value) for value in signature_counts]),
        "all_checks_pass": False,
    }
    validation["all_checks_pass"] = bool(
        validation["completed_pool_rows"] == manifest.get("task_seed_units")
        and validation["execution_rows"] == manifest.get("task_seed_units") * manifest.get("candidate_pool_size")
        and validation["unique_execution_keys"] == validation["execution_rows"]
        and validation["selection_rows"] == manifest.get("expected_selection_records")
        and validation["paired_units"] == manifest.get("task_seed_units")
        and not validation["missing_selector_pairs"]
        and validation["shared_pool_id_mismatches"] == 0
        and validation["llm_call_count_mismatches"] == 0
        and validation["future_failure_information_violations"] == 0
    )
    result = {
        "protocol_version": manifest.get("protocol_version"),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "design": {
            "source_tasks": len(manifest.get("source_task_ids", [])),
            "seed_indices": manifest.get("seed_indices"),
            "task_seed_units": manifest.get("task_seed_units"),
            "candidate_pool_size": manifest.get("candidate_pool_size"),
            "selector_arms": manifest.get("selector_arms"),
            "selection_records": len(selections),
        },
        "arm_summary": arm_summary,
        "paired_analysis": paired,
        "candidate_pool_diagnostics": {
            "signature_count": {"n": len(signature_counts), "min": min(signature_counts) if signature_counts else None, "mean": mean([float(value) for value in signature_counts]), "max": max(signature_counts) if signature_counts else None},
            "gate_promotions": sum(bool(row.get("gate_promoted")) for row in selections if row.get("selector") == "SeSE"),
            "gate_abstentions": sum(bool(row.get("gate_abstained")) for row in selections if row.get("selector") == "SeSE"),
        },
        "scope_notes": [
            "This experiment isolates pre-execution plan selection; it does not test post-failure CDS-MR.",
            "The independent ranking uses deterministic bridge execution plus a separate contract evaluator, not the SeSE gate score.",
            "All physical vehicles use the current common CF2X bridge model; heterogeneity is at the role/capability contract layer.",
        ],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.validation.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# SeSE independent plan-selection evidence",
        "",
        f"Design: {result['design']['source_tasks']} tasks × {len(result['design']['seed_indices'])} held-out seeds × 2 selectors = {len(selections)} selection records; K={result['design']['candidate_pool_size']} shared candidates per task/seed.",
        "",
        "| Selector | IP-CVR ↑ | Independent utility ↑ | Selection regret ↓ | Initial execution success ↑ | Planning latency (s) ↓ | Tokens ↓ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for selector in ("Blind", "SeSE"):
        summary = arm_summary[selector]
        lines.append(
            f"| {selector} | {fmt(summary['initial_plan_contract_valid']['mean'])} | {fmt(summary['independent_selection_utility']['mean'])} | {fmt(summary['selection_regret']['mean'])} | {fmt(summary['actual_initial_execution_success']['mean'])} | {fmt(summary['initial_planning_latency_seconds']['mean'])} | {fmt(summary['initial_planning_token_cost']['mean'], 1)} |"
        )
    lines.extend(["", "## Paired SeSE − Blind differences", "", "| Metric | n | Mean Δ | 95% bootstrap CI | sign-flip p | Cohen dz |", "|---|---:|---:|---:|---:|---:|"])
    for field in ("initial_plan_contract_valid", "independent_selection_utility", "selection_regret", "actual_initial_execution_success"):
        item = paired[field]
        ci = item["bootstrap_95_ci"]
        lines.append(f"| {field} | {item['n']} | {fmt(item['mean_difference'])} | [{fmt(ci[0])}, {fmt(ci[1])}] | {fmt(item['paired_sign_flip_p'], 6)} | {fmt(item['cohen_dz'])} |")
    lines.extend([
        "",
        f"Gate promotion rate: {fmt(arm_summary['SeSE']['promotion_rate'])}; abstention rate: {fmt(arm_summary['SeSE']['abstention_rate'])}.",
        f"Candidate signature count: min={validation['candidate_signature_count_min']}, mean={fmt(validation['candidate_signature_count_mean'])}.",
        f"Integrity validation: {'PASS' if validation['all_checks_pass'] else 'FAIL'}.",
        "",
        "Scope: this is independent SeSE selection evidence, not a post-failure repair experiment and not a physical-model heterogeneity experiment.",
    ])
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"validation": validation, "summary": str(args.output_json)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
