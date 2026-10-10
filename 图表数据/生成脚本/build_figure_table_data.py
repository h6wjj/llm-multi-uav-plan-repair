#!/usr/bin/env python3
"""Build the figure/table data package from the frozen experiment evidence.

Only derived files under 图表数据 are written. Source experiment records are read-only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path


METRICS = {
    "recovery_success_rate_item": ("RSR", "up"),
    "contract_satisfaction_rate": ("CSR", "up"),
    "normalized_plan_stability_distance": ("NPSD", "down"),
    "repair_decision_latency_seconds": ("Repair latency (s)", "down"),
    "mission_completion_time_seconds": ("Mission time (s), success only", "down"),
}

METHOD_DISPLAY = {
    "B0": "No-repair execution",
    "LLM-Assisted Plan Execution": "LLM-assisted replanning adapter",
    "H-MRTA-Repair": "H-MRTA global-reallocation adapter",
    "B0+SeSE+CDS-MR": "Proposed full mechanism",
    "Blind+Global-Replan": "Blind selection + global reallocation",
    "SeSE+Global-Replan": "Structured selection + global reallocation",
    "Blind+CDS-MR": "Blind selection + minimal repair",
    "SeSE+CDS-MR": "Structured selection + minimal repair",
    "Simple-local-reassignment": "Simple local reassignment",
    "CDS-MR": "Structured minimal repair",
    "Blind": "Blind selection",
    "SeSE": "Structured selection",
}

TASK_DISPLAY = {
    "uavbench_Arctic_Swarm_Coordination_with_Lightning_Risk_003470": "Arctic lightning",
    "uavbench_Forest_Fog_Swarm_Coordination_012674": "Forest fog",
    "uavbench_HarborSwarmDelivery_Quadrotor_015740": "Harbor delivery",
    "uavbench_Harbor_Swarm_Coordination_with_Gusts_017664": "Harbor gusts",
    "uavbench_Suburban_Swarm_Coordination_Under_Microburst_Risk_034427": "Suburban microburst",
    "uavbench_Urban_Snowfall_Swarm_Coordination_038984": "Urban snowfall",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finite(values):
    return [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - pos) + sorted_values[hi] * (pos - lo)


def bootstrap_mean_ci(values, seed, iterations=10000):
    vals = finite(values)
    if not vals:
        return None, None
    rng = random.Random(seed)
    n = len(vals)
    means = [sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)]
    means.sort()
    return percentile(means, 0.025), percentile(means, 0.975)


def flatten_metric_summary(summary, method_order):
    rows = []
    for method in method_order:
        for metric, (label, direction) in METRICS.items():
            item = summary[method][metric]
            ci = item.get("ci95_bootstrap_mean", [None, None])
            rows.append({
                "raw_method": method,
                "display_method": METHOD_DISPLAY[method],
                "metric": metric,
                "metric_label": label,
                "direction": direction,
                "n": item.get("n"),
                "mean": item.get("mean"),
                "std": item.get("std"),
                "ci95_low": ci[0],
                "ci95_high": ci[1],
            })
    return rows


def flatten_paired(mapping, test_field="permutation_p_two_sided"):
    rows = []
    for comparison, metrics in mapping.items():
        for metric, item in metrics.items():
            ci = item.get("ci95_bootstrap_delta", item.get("bootstrap_95_ci", [None, None]))
            rows.append({
                "comparison": comparison,
                "metric": metric,
                "metric_label": METRICS.get(metric, (metric, ""))[0],
                "n": item.get("n"),
                "mean_delta_left_minus_right": item.get("mean_delta_left_minus_right", item.get("mean_difference")),
                "std_delta": item.get("std_delta"),
                "ci95_low": ci[0] if ci else None,
                "ci95_high": ci[1] if ci else None,
                "p_two_sided": item.get(test_field, item.get("paired_sign_flip_p")),
                "cohen_dz": item.get("cohen_dz"),
            })
    return rows


def main(workspace: Path, target: Path):
    source = workspace / "实验数据"
    results = source / "results"
    evidence = source / "evidence"

    paths = {
        "main_overlay": results / "main_v6_4" / "summary_closed_external_overlay.json",
        "hmrta_rows": results / "external_baseline_repro_v1" / "h_mrta.jsonl",
        "llm_rows": results / "external_baseline_repro_v1" / "llm_ape_final_rebuilt.jsonl",
        "ablation": results / "ablation_v6_4_seed8_12" / "ablation_stats_seed8_12.json",
        "selector_summary": results / "sese_selection" / "summary.json",
        "selector_rows": results / "sese_selection" / "selections.jsonl",
        "constraint_summary": results / "constraint_sensitivity" / "summary.json",
        "constraint_rows": results / "constraint_sensitivity" / "raw_results.jsonl",
        "local_summary": results / "local_repair_vs_cds_mr_v1" / "summary.json",
        "local_pairs": results / "local_repair_vs_cds_mr_v1" / "comparison_by_pair.jsonl",
        "case": evidence / "case" / "selected-case-evidence.json",
        "case_mapping": evidence / "case" / "canonical_bundle_id_mapping_2026-09-03.json",
        "protocol": evidence / "protocol_audit.json",
    }
    for key, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing source {key}: {path}")

    output_paths = []

    def out(rel):
        p = target / rel
        output_paths.append(p)
        return p

    # Fig. 1: deterministic architecture data; no experimental values.
    fig1_nodes = [
        {"id": "mission", "label_cn": "自然语言任务", "stage": "planning", "kind": "input", "required": True},
        {"id": "llm_pool", "label_cn": "LLM候选计划生成", "stage": "planning", "kind": "process", "required": True},
        {"id": "quality_gate", "label_cn": "执行前结构一致性质量控制", "stage": "planning", "kind": "optional_process", "required": False},
        {"id": "initial_plan", "label_cn": "选定结构化初始计划", "stage": "planning", "kind": "artifact", "required": True},
        {"id": "bridge", "label_cn": "Agent4Drone兼容执行桥", "stage": "execution", "kind": "process", "required": True},
        {"id": "failure_state", "label_cn": "失效时状态快照", "stage": "recovery", "kind": "state", "required": True},
        {"id": "minimal_repair", "label_cn": "执行状态驱动的意图保持最小修复", "stage": "recovery", "kind": "process", "required": True},
        {"id": "single_submit", "label_cn": "选定方案单次提交", "stage": "recovery", "kind": "artifact", "required": True},
        {"id": "runtime_check", "label_cn": "同桥继续执行与运行时检查", "stage": "execution", "kind": "process", "required": True},
        {"id": "terminal", "label_cn": "任务完成或恢复失败", "stage": "terminal", "kind": "terminal", "required": True},
    ]
    fig1_edges = [
        {"source": "mission", "target": "llm_pool", "label_cn": "生成", "style": "solid"},
        {"source": "llm_pool", "target": "initial_plan", "label_cn": "默认候选", "style": "solid"},
        {"source": "llm_pool", "target": "quality_gate", "label_cn": "多个候选", "style": "dashed"},
        {"source": "quality_gate", "target": "initial_plan", "label_cn": "可选支线", "style": "dashed"},
        {"source": "initial_plan", "target": "bridge", "label_cn": "执行", "style": "solid"},
        {"source": "bridge", "target": "failure_state", "label_cn": "平台不可用", "style": "solid"},
        {"source": "failure_state", "target": "minimal_repair", "label_cn": "状态+剩余后缀", "style": "solid"},
        {"source": "minimal_repair", "target": "single_submit", "label_cn": "选择P_sel", "style": "solid"},
        {"source": "single_submit", "target": "runtime_check", "label_cn": "仅提交一次", "style": "solid"},
        {"source": "runtime_check", "target": "terminal", "label_cn": "终止", "style": "solid"},
    ]
    write_csv(out("fig1_总体框架/fig1_nodes.csv"), fig1_nodes)
    write_csv(out("fig1_总体框架/fig1_edges.csv"), fig1_edges)

    # Fig. 2: mechanism steps, set semantics, and lexicographic objective.
    fig2_steps = [
        {"order": 1, "stage": "状态固化", "input": "P0 + bridge state", "operation": "锁定完成前缀并截取剩余后缀", "output": "A_f, C_keep, R_f", "online_mutation": False},
        {"order": 2, "stage": "影响检查", "input": "R_f + dependency DAG", "operation": "构造直接影响与依赖检查范围", "output": "C_dir, C_check", "online_mutation": False},
        {"order": 3, "stage": "编辑定位", "input": "C_check + owner state", "operation": "仅标记原拥有者离线或资格失效任务", "output": "C_aff", "online_mutation": False},
        {"order": 4, "stage": "候选构造", "input": "C_aff + UAV role/capability/state", "operation": "枚举兼容替代并静态检查硬约束", "output": "F_static(s_f)", "online_mutation": False},
        {"order": 5, "stage": "最小扰动选择", "input": "F_static(s_f)", "operation": "按词典序代价选择唯一方案", "output": "P_sel", "online_mutation": False},
        {"order": 6, "stage": "继续执行", "input": "P_sel", "operation": "单次提交在线桥并执行运行时检查", "output": "success or failure", "online_mutation": True},
    ]
    fig2_sets = [
        {"symbol": "A_f", "meaning_cn": "失效前已完成且不可重放的动作前缀", "forest_fog_members": "bundle_1全部; bundle_2前2点; bundle_3前1点; bundle_4前1点"},
        {"symbol": "C_dir", "meaning_cn": "直接受失效影响的剩余任务", "forest_fog_members": "bundle_2"},
        {"symbol": "C_check", "meaning_cn": "沿依赖关系展开的检查范围", "forest_fog_members": "bundle_2|bundle_3|bundle_4"},
        {"symbol": "C_aff", "meaning_cn": "实际需要更换拥有者的编辑集合", "forest_fog_members": "bundle_2"},
        {"symbol": "U_i^comp", "meaning_cn": "在线且满足角色、能力和可用性要求的候选UAV", "forest_fog_members": "bundle_2: uav_0|uav_2|uav_3"},
        {"symbol": "F_static(s_f)", "meaning_cn": "通过静态硬约束预检的有限候选计划集合", "forest_fog_members": "由候选组合构造"},
    ]
    fig2_objective = [
        {"priority": 1, "term": "N_bundle", "definition_cn": "重新分配的任务束数量", "direction": "min"},
        {"priority": 2, "term": "N_action", "definition_cn": "被重新分配任务束中的剩余航点动作数量", "direction": "min"},
        {"priority": 3, "term": "J_route", "definition_cn": "从失效时状态按执行顺序累加的欧氏路线代价", "direction": "min"},
        {"priority": 4, "term": "N_overlap", "definition_cn": "与下游原拥有者发生重叠的布尔代价", "direction": "min"},
        {"priority": 5, "term": "J_tie", "definition_cn": "按执行顺序排列的无人机标识元组", "direction": "lexicographic tie-break"},
    ]
    write_csv(out("fig2_最小修复机制/fig2_mechanism_steps.csv"), fig2_steps)
    write_csv(out("fig2_最小修复机制/fig2_set_definitions.csv"), fig2_sets)
    write_csv(out("fig2_最小修复机制/fig2_objective_terms.csv"), fig2_objective)

    # Fig. 3 and Table 4: closed main comparison.
    overlay = read_json(paths["main_overlay"])
    main_raw = []
    for seed_dir in sorted((results / "main_v6_4").glob("seed_*")):
        main_raw.extend(read_jsonl(seed_dir / "b0.jsonl"))
        main_raw.extend(read_jsonl(seed_dir / "cds_mr.jsonl"))
    main_raw.extend(read_jsonl(paths["hmrta_rows"]))
    main_raw.extend(read_jsonl(paths["llm_rows"]))
    main_raw = [r for r in main_raw if r.get("condition") == "recoverable_failure"]
    counts = defaultdict(int)
    fig3_rows = []
    for r in main_raw:
        method = r["method"]
        counts[method] += 1
        sm = r["standard_metrics"]
        checks = sm.get("contract_checks", {})
        fig3_rows.append({
            "paired_id": f'{r["source_task_id"]}::seed_{r["run_seed_index"]}',
            "task_id": r["task_id"],
            "source_task_id": r["source_task_id"],
            "task_label": TASK_DISPLAY.get(r["source_task_id"], r["source_task_id"]),
            "run_seed_index": r["run_seed_index"],
            "condition": r["condition"],
            "raw_method": method,
            "display_method": METHOD_DISPLAY[method],
            "RSR": sm.get("recovery_success_rate_item"),
            "CSR": sm.get("contract_satisfaction_rate"),
            "NPSD": sm.get("normalized_plan_stability_distance"),
            "repair_latency_s": sm.get("repair_decision_latency_seconds"),
            "mission_time_s_success_only": sm.get("mission_completion_time_seconds"),
            "strict_contract_satisfied": sm.get("strict_contract_satisfied"),
            "check_completion": checks.get("completion"),
            "check_deadline": checks.get("deadline"),
            "check_assignment_capability": checks.get("assignment_and_capability"),
            "check_dependency": checks.get("dependency"),
            "check_budget": checks.get("budget"),
            "check_immutable_prefix": checks.get("immutable_completed_prefix"),
        })
    expected_methods = overlay["methods"]
    if set(counts) != set(expected_methods) or any(counts[m] != 36 for m in expected_methods):
        raise AssertionError(f"Main row counts mismatch: {dict(counts)}")
    main_keys_by_method = {
        method: {r["paired_id"] for r in fig3_rows if r["raw_method"] == method}
        for method in expected_methods
    }
    if len({frozenset(v) for v in main_keys_by_method.values()}) != 1:
        raise AssertionError("Main methods do not share the same 36 paired keys")
    if len({(r["paired_id"], r["raw_method"]) for r in fig3_rows}) != len(fig3_rows):
        raise AssertionError("Duplicate main pair-method rows detected")
    write_csv(out("fig3_主对比/fig3_main_rows_recoverable.csv"), fig3_rows)

    field_for_metric = {
        "recovery_success_rate_item": "RSR",
        "contract_satisfaction_rate": "CSR",
        "normalized_plan_stability_distance": "NPSD",
        "repair_decision_latency_seconds": "repair_latency_s",
        "mission_completion_time_seconds": "mission_time_s_success_only",
    }
    fig3_summary = []
    for mi, method in enumerate(expected_methods):
        for mj, (metric, (label, direction)) in enumerate(METRICS.items()):
            auth = overlay["summary"][method]["recoverable_failure"][metric]
            vals = finite([r[field_for_metric[metric]] for r in fig3_rows if r["raw_method"] == method])
            calc_mean = statistics.mean(vals) if vals else None
            calc_std = statistics.stdev(vals) if len(vals) > 1 else (0.0 if len(vals) == 1 else None)
            if auth["n"] != len(vals):
                raise AssertionError(f"n mismatch: {method} {metric}")
            if vals and abs(calc_mean - auth["mean"]) > 2e-6:
                raise AssertionError(f"mean mismatch: {method} {metric}: {calc_mean} vs {auth['mean']}")
            if len(vals) > 1 and abs(calc_std - auth["std"]) > 2e-6:
                raise AssertionError(f"std mismatch: {method} {metric}: {calc_std} vs {auth['std']}")
            ci_lo, ci_hi = bootstrap_mean_ci(vals, 20260913 + mi * 100 + mj)
            fig3_summary.append({
                "raw_method": method,
                "display_method": METHOD_DISPLAY[method],
                "metric": metric,
                "metric_label": label,
                "direction": direction,
                "n": auth["n"],
                "mean": auth["mean"],
                "std": auth["std"],
                "bootstrap_ci95_low": ci_lo,
                "bootstrap_ci95_high": ci_hi,
                "ci_note": "derived from closed row-level records; deterministic 10000-resample percentile bootstrap",
            })
    write_csv(out("fig3_主对比/fig3_main_summary.csv"), fig3_summary)

    paired_rows = []
    for item in overlay["paired_statistics"]:
        ci = item["benefit_ci95"]
        paired_rows.append({
            "left_raw_method": item["left_method"],
            "left_display_method": METHOD_DISPLAY[item["left_method"]],
            "right_raw_method": item["right_method"],
            "right_display_method": METHOD_DISPLAY[item["right_method"]],
            "condition": item["condition"],
            "metric": item["metric"],
            "metric_label": METRICS[item["metric"]][0],
            "n": item["n"],
            "mean_delta_left_minus_right": item["mean_delta_left_minus_right"],
            "mean_benefit_delta": item["mean_benefit_delta"],
            "benefit_direction": item["benefit_direction"],
            "benefit_ci95_low": ci[0],
            "benefit_ci95_high": ci[1],
            "exact_p_sign_test_two_sided": item["exact_p_sign_test_two_sided"],
            "cohen_dz": item["effect_size_cohen_dz"],
        })
    write_csv(out("fig3_主对比/fig3_paired_statistics.csv"), paired_rows)

    grouped = defaultdict(list)
    for row in fig3_rows:
        for metric, field in field_for_metric.items():
            if row[field] is not None:
                grouped[(row["source_task_id"], row["raw_method"], metric)].append(float(row[field]))
    task_rows = []
    for (task, method, metric), vals in sorted(grouped.items()):
        task_rows.append({
            "source_task_id": task,
            "task_label": TASK_DISPLAY.get(task, task),
            "raw_method": method,
            "display_method": METHOD_DISPLAY[method],
            "metric": metric,
            "metric_label": METRICS[metric][0],
            "n_seeds": len(vals),
            "mean": statistics.mean(vals),
            "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        })
    write_csv(out("fig3_主对比/fig3_task_level_summary.csv"), task_rows)

    # Fig. 4: exact Forest Fog paired case.
    case = read_json(paths["case"])
    mapping = read_json(paths["case_mapping"])
    map_by_raw = {x["raw_bundle_id"]: x["canonical_bundle_id"] for x in mapping["entries"]}
    original_by_bundle = {}
    for rec in case["original_plan"]:
        canonical = map_by_raw[rec["bundle_id"]]
        original_by_bundle[canonical] = {**rec, "canonical_bundle_id": canonical}
    hmrta_case = next(
        r for r in read_jsonl(paths["hmrta_rows"])
        if r["task_id"] == case["task_id"]
    )
    hmrta_assign = hmrta_case["repair"]["planner_audit"]["selected_assignment"]
    ours_assign = {b: rec["original_owner"] for b, rec in original_by_bundle.items()}
    for b, change in case["minimum_repair"]["assignment_changes"].items():
        ours_assign[b] = change["to"]
    completed = case["failure_observation"]["completed_waypoints_by_bundle"]
    case_rows = []
    waypoint_rows = []
    for b in sorted(original_by_bundle, key=lambda x: original_by_bundle[x]["order"]):
        rec = original_by_bundle[b]
        original_owner = rec["original_owner"]
        total = rec["waypoint_count"]
        done = completed[b]
        set_role = (
            "immutable_completed_prefix" if b in case["causal_impact_closure"]["immutable_completed_prefix"]
            else "directly_affected_and_edited" if b in case["causal_impact_closure"]["directly_affected_bundle_ids"]
            else "dependency_checked_owner_preserved"
        )
        h_owner = hmrta_assign.get(b, original_owner)
        o_owner = ours_assign.get(b, original_owner)
        case_rows.append({
            "bundle_id": b,
            "order": rec["order"],
            "original_owner": original_owner,
            "completed_waypoints_at_failure": done,
            "total_waypoints": total,
            "remaining_waypoints": total - done,
            "set_role": set_role,
            "hmrta_owner_after_repair": h_owner,
            "hmrta_changed": h_owner != original_owner,
            "proposed_owner_after_repair": o_owner,
            "proposed_changed": o_owner != original_owner,
        })
        for idx, wp in enumerate(rec["waypoints"], start=1):
            waypoint_rows.append({
                "bundle_id": b,
                "waypoint_index": idx,
                "x": wp[0], "y": wp[1], "z": wp[2],
                "completed_before_failure": idx <= done,
                "original_owner": original_owner,
                "hmrta_owner_after_repair": h_owner,
                "proposed_owner_after_repair": o_owner,
            })
    write_csv(out("fig4_案例分析/fig4_bundle_assignment_comparison.csv"), case_rows)
    write_csv(out("fig4_案例分析/fig4_waypoints.csv"), waypoint_rows)
    case_overview = {
        "case_id": case["case_id"],
        "source_task_id": case["source_task_id"],
        "task_id": case["task_id"],
        "failure_drone": case["failure_observation"]["drone_id"],
        "failure_bundle": case["failure_observation"]["bundle_id"],
        "directly_affected": case["causal_impact_closure"]["directly_affected_bundle_ids"],
        "dependency_check_scope": case["causal_impact_closure"]["dependency_execution_closure"],
        "proposed_edited_bundles": list(case["minimum_repair"]["assignment_changes"]),
        "hmrta_edited_bundles": [r["bundle_id"] for r in hmrta_case["repair"]["repair_plan"] if r["assigned_drone"] != r["original_assignment"]],
        "proposed_metrics": {
            "RSR": 1.0,
            "CSR": case["minimum_repair"]["contract_satisfaction_rate"],
            "NPSD": case["minimum_repair"]["npsd"],
            "repair_latency_s": case["minimum_repair"]["repair_latency_s"],
        },
        "hmrta_metrics": {
            "RSR": hmrta_case["standard_metrics"]["recovery_success_rate_item"],
            "CSR": hmrta_case["standard_metrics"]["contract_satisfaction_rate"],
            "NPSD": hmrta_case["standard_metrics"]["normalized_plan_stability_distance"],
            "repair_latency_s": hmrta_case["standard_metrics"]["repair_decision_latency_seconds"],
        },
        "claim_boundary": case["claim_boundary"],
    }
    write_json(out("fig4_案例分析/fig4_case_overview.json"), case_overview)

    # Fig. 5: selector diagnostics and constraint sensitivity.
    selector_records = read_jsonl(paths["selector_rows"])
    selector_rows = []
    for r in selector_records:
        selector_rows.append({
            "paired_id": f'{r["source_task_id"]}::seed_{r["run_seed_index"]}',
            "source_task_id": r["source_task_id"],
            "task_label": TASK_DISPLAY.get(r["source_task_id"], r["source_task_id"]),
            "run_seed_index": r["run_seed_index"],
            "raw_selector": r["selector"],
            "display_selector": METHOD_DISPLAY[r["selector"]],
            "candidate_count": r["candidate_count"],
            "candidate_signature_count": r["candidate_signature_count"],
            "selected_candidate_index": r["selected_candidate_index"],
            "gate_promoted": r["gate_promoted"],
            "initial_plan_contract_valid": r["initial_plan_contract_valid"],
            "initial_plan_CSR": r["initial_plan_contract_satisfaction_rate"],
            "independent_selection_utility": r["independent_selection_utility"],
            "selection_regret": r["selection_regret"],
            "selected_route_cost": r["selected_route_cost"],
            "actual_initial_execution_success": r["actual_initial_execution_success"],
            "actual_CSR": r["actual_contract_satisfaction_rate"],
            "actual_mission_time_s_success_only": r["actual_mission_completion_time_seconds"],
            "candidate_generation_latency_s": r["candidate_generation_latency_seconds"],
            "selection_latency_s": r["selection_latency_seconds"],
            "initial_planning_latency_s": r["initial_planning_latency_seconds"],
            "initial_planning_token_cost": r["initial_planning_token_cost"],
        })
    if len(selector_rows) != 60:
        raise AssertionError(f"Expected 60 selector rows, found {len(selector_rows)}")
    selector_keys = defaultdict(set)
    for r in selector_rows:
        selector_keys[r["raw_selector"]].add(r["paired_id"])
        if r["candidate_count"] != 5 or r["candidate_signature_count"] != 5:
            raise AssertionError("Selector pool is not the preregistered K=5 shared-signature pool")
    if len({frozenset(v) for v in selector_keys.values()}) != 1 or any(len(v) != 30 for v in selector_keys.values()):
        raise AssertionError("Selector arms do not share the same 30 paired pools")
    write_csv(out("fig5_选择器与约束/fig5a_selector_rows.csv"), selector_rows)

    selector_summary = read_json(paths["selector_summary"])
    selector_summary_rows = []
    for arm, metrics in selector_summary["arm_summary"].items():
        for metric, item in metrics.items():
            if isinstance(item, dict) and "mean" in item:
                selector_summary_rows.append({
                    "raw_selector": arm,
                    "display_selector": METHOD_DISPLAY[arm],
                    "metric": metric,
                    "n": item.get("n"),
                    "mean": item.get("mean"),
                    "std": item.get("std"),
                })
    write_csv(out("fig5_选择器与约束/fig5a_selector_summary.csv"), selector_summary_rows)
    selector_paired_rows = []
    for metric, item in selector_summary["paired_analysis"].items():
        if not isinstance(item, dict) or "n" not in item:
            continue
        ci = item.get("bootstrap_95_ci", [None, None])
        selector_paired_rows.append({
            "metric": metric,
            "direction": item.get("direction"),
            "n": item.get("n"),
            "mean_difference": item.get("mean_difference"),
            "ci95_low": ci[0], "ci95_high": ci[1],
            "paired_sign_flip_p": item.get("paired_sign_flip_p"),
            "cohen_dz": item.get("cohen_dz"),
        })
    write_csv(out("fig5_选择器与约束/fig5a_selector_paired_statistics.csv"), selector_paired_rows)

    constraint_raw = read_jsonl(paths["constraint_rows"])
    constraint_rows = []
    for r in constraint_raw:
        sm = r["standard_metrics"]
        level = r["task_id"].split("::constraint_sensitivity_")[1].split("::")[0]
        constraint_rows.append({
            "paired_id": f'{r["source_task_id"]}::{level}::seed_{r["run_seed_index"]}',
            "source_task_id": r["source_task_id"],
            "task_label": TASK_DISPLAY.get(r["source_task_id"], r["source_task_id"]),
            "run_seed_index": r["run_seed_index"],
            "constraint_tightness": level,
            "raw_method": r["method"],
            "display_method": METHOD_DISPLAY[r["method"]],
            "RSR": sm.get("recovery_success_rate_item"),
            "CSR": sm.get("contract_satisfaction_rate"),
            "NPSD": sm.get("normalized_plan_stability_distance"),
            "repair_latency_s": sm.get("repair_decision_latency_seconds"),
        })
    if len(constraint_rows) != 36:
        raise AssertionError(f"Expected 36 constraint rows, found {len(constraint_rows)}")
    constraint_keys = defaultdict(set)
    for r in constraint_rows:
        constraint_keys[r["raw_method"]].add(r["paired_id"])
    if len({frozenset(v) for v in constraint_keys.values()}) != 1 or any(len(v) != 18 for v in constraint_keys.values()):
        raise AssertionError("Constraint methods do not share the same 18 task-level paired keys")
    write_csv(out("fig5_选择器与约束/fig5bc_constraint_rows.csv"), constraint_rows)
    constraint_summary = read_json(paths["constraint_summary"])
    constraint_summary_rows = []
    for level in constraint_summary["levels"]:
        for method in constraint_summary["methods"]:
            for metric in constraint_summary["metrics"]:
                item = constraint_summary["summary_by_level"][level][method][metric]
                constraint_summary_rows.append({
                    "constraint_tightness": level,
                    "raw_method": method,
                    "display_method": METHOD_DISPLAY[method],
                    "metric": metric,
                    "metric_label": METRICS[metric][0],
                    "n": item.get("n"), "mean": item.get("mean"), "std": item.get("std"),
                })
    write_csv(out("fig5_选择器与约束/fig5bc_constraint_summary.csv"), constraint_summary_rows)
    constraint_paired_rows = []
    for level, metrics in constraint_summary["paired_cds_minus_global_by_level"].items():
        for metric, item in metrics.items():
            constraint_paired_rows.append({
                "constraint_tightness": level,
                "comparison": "minimal_repair_minus_global_reallocation",
                "metric": metric,
                "metric_label": METRICS[metric][0],
                "n": item.get("n"), "mean_delta": item.get("mean"), "std_delta": item.get("std"),
            })
    write_csv(out("fig5_选择器与约束/fig5bc_constraint_paired_deltas.csv"), constraint_paired_rows)

    # Tables 1--3: frozen definitions and protocol settings.
    table1 = [
        {"symbol": "U", "definition_cn": "无人机集合", "source": "problem definition"},
        {"symbol": "r_u, K_u", "definition_cn": "无人机角色与能力集合", "source": "task contract"},
        {"symbol": "G=(V,E)", "definition_cn": "任务束依赖有向无环图", "source": "task contract"},
        {"symbol": "P_0", "definition_cn": "进入执行的初始结构化计划", "source": "LLM planning output"},
        {"symbol": "s_f", "definition_cn": "仿真执行中记录的失效时状态", "source": "bridge snapshot"},
        {"symbol": "A_f", "definition_cn": "不可重放的已完成动作前缀", "source": "bridge progress"},
        {"symbol": "R_f", "definition_cn": "失效时剩余任务束集合", "source": "bridge progress"},
        {"symbol": "C_dir", "definition_cn": "直接受失效影响的任务束", "source": "owner availability"},
        {"symbol": "C_check", "definition_cn": "依赖传播后的检查范围", "source": "dependency DAG"},
        {"symbol": "C_aff", "definition_cn": "实际需要改变拥有者的编辑集合", "source": "state decision"},
        {"symbol": "U_i^comp", "definition_cn": "任务束i的在线兼容候选UAV集合", "source": "role/capability/state"},
        {"symbol": "F_static(s_f)", "definition_cn": "通过静态硬约束预检的有限候选计划集合", "source": "candidate enumeration"},
        {"symbol": "P_sel", "definition_cn": "按词典序目标选出的唯一待提交方案", "source": "minimal-repair selector"},
        {"symbol": "NPSD", "definition_cn": "原剩余动作集合与修复动作集合的Jaccard距离", "source": "shared evaluator"},
    ]
    write_csv(out("tables/table1_symbols.csv"), table1)

    table2 = [
        {"order": 1, "functional_stage": "执行前候选质量控制", "input": "多个LLM候选", "core_operation": "结构签名一致性+独立约束检查", "output": "P0", "guarantee_or_boundary": "可选支持功能，不承担失效后收益"},
        {"order": 2, "functional_stage": "状态提取与前缀锁定", "input": "P0+s_f", "core_operation": "同步完成进度、拥有者和失效对象", "output": "A_f,R_f", "guarantee_or_boundary": "完成前缀不可重放"},
        {"order": 3, "functional_stage": "影响范围定位", "input": "R_f+G+s_f", "core_operation": "区分直接影响、检查范围和编辑范围", "output": "C_dir,C_check,C_aff", "guarantee_or_boundary": "检查不等于修改"},
        {"order": 4, "functional_stage": "候选构造与静态预检", "input": "C_aff+UAV状态", "core_operation": "资格、依赖、期限、预算检查", "output": "F_static(s_f)", "guarantee_or_boundary": "主实验资格约束不具约束性"},
        {"order": 5, "functional_stage": "最小扰动方案选择", "input": "F_static(s_f)", "core_operation": "词典序最小编辑", "output": "P_sel", "guarantee_or_boundary": "仅在有限候选空间内最优"},
        {"order": 6, "functional_stage": "在线继续执行", "input": "P_sel", "core_operation": "单次提交并运行时验证", "output": "success/failure", "guarantee_or_boundary": "无桥状态回滚与候选重试"},
    ]
    write_csv(out("tables/table2_mechanism_functions.csv"), table2)

    protocol = read_json(paths["protocol"])
    table3 = [
        {"item": "source tasks", "setting": "6 fixed UAVBench-derived tasks", "reporting_boundary": "not 6 independent task classes"},
        {"item": "main seeds", "setting": "13|14|15|16|17|18", "reporting_boundary": "36 task-seed pairs"},
        {"item": "ablation seeds", "setting": "8|9|10|11|12", "reporting_boundary": "30 task-seed pairs"},
        {"item": "selector seeds", "setting": "23|24|25|26|27; K=5", "reporting_boundary": "30 shared candidate pools"},
        {"item": "software environment", "setting": "Python 3.12.3; PyBullet 3.2.7; gym-pybullet-drones commit 24fccff", "reporting_boundary": "recorded simulation environment; no training-hardware comparison"},
        {"item": "execution bridge", "setting": protocol["platform"], "reporting_boundary": "simulation only"},
        {"item": "physical model", "setting": protocol["physical_model"], "reporting_boundary": "no physical-model heterogeneity"},
        {"item": "main failure", "setting": "uav_1 unavailable during bundle_2 after 2/3 waypoints", "reporting_boundary": "fixed failure object and timing"},
        {"item": "role/capability in main", "setting": "GNSS required; all role families allowed; all online replacements eligible", "reporting_boundary": "does not validate heterogeneous eligibility benefit"},
        {"item": "paired key", "setting": protocol["paired_key"], "reporting_boundary": "retain paired structure"},
        {"item": "mission time", "setting": protocol["metrics"]["mission_time_rule"], "reporting_boundary": "never impute failure as zero"},
        {"item": "external baselines", "setting": "platform-adapted H-MRTA and LLM-assisted replanning", "reporting_boundary": "official runtime not invoked"},
        {"item": "main/ablation candidate pool", "setting": "deepseek-v4-flash; K=3; T=0.0|0.55|0.9", "reporting_boundary": "generated once per task-seed and then frozen"},
        {"item": "selector diagnostic", "setting": "deepseek-v4-flash; K=5; one call per task-seed pool", "reporting_boundary": "shared pool; future failure and execution outcomes excluded from selection"},
    ]
    write_csv(out("tables/table3_experiment_setup.csv"), table3)

    # Table 4: wide exact values plus paired statistics.
    table4 = []
    for method in expected_methods:
        row = {"raw_method": method, "display_method": METHOD_DISPLAY[method]}
        for metric, (label, _) in METRICS.items():
            item = overlay["summary"][method]["recoverable_failure"][metric]
            prefix = METRICS[metric][0].split()[0].replace("(", "").replace(")", "").lower()
            if metric == "repair_decision_latency_seconds": prefix = "repair_latency_s"
            if metric == "mission_completion_time_seconds": prefix = "mission_time_s_success_only"
            row[f"{prefix}_n"] = item["n"]
            row[f"{prefix}_mean"] = item["mean"]
            row[f"{prefix}_std"] = item["std"]
        table4.append(row)
    write_csv(out("tables/table4_main_results.csv"), table4)
    write_csv(out("tables/table4_paired_statistics.csv"), paired_rows)

    # Table 5: four-arm ablation and simple-local negative control.
    ablation = read_json(paths["ablation"])
    ablation_summary_rows = flatten_metric_summary(ablation["summary"], ablation["methods"])
    write_csv(out("tables/table5_ablation_summary.csv"), ablation_summary_rows)
    ablation_paired_rows = flatten_paired(ablation["paired_comparisons"])
    write_csv(out("tables/table5_ablation_paired_statistics.csv"), ablation_paired_rows)

    local_summary = read_json(paths["local_summary"])
    local_rows = []
    for method, item in local_summary["method_rows"].items():
        local_rows.append({"raw_method": method, "display_method": METHOD_DISPLAY[method], **item})
    write_csv(out("tables/table5_simple_local_summary.csv"), local_rows)
    local_pairs = read_jsonl(paths["local_pairs"])
    if len(local_pairs) != 36 or not all(r.get("same_failure_time_state") for r in local_pairs):
        raise AssertionError("Simple-local negative control is not a complete 36-pair same-state comparison")
    for r in local_pairs:
        r["simple_changed_bundle_ids"] = "|".join(r.get("simple_changed_bundle_ids", []))
    write_csv(out("tables/table5_simple_local_pairs.csv"), local_pairs)

    validation = {
        "status": "PASS",
        "checks": {
            "main_row_count": len(fig3_rows) == 144,
            "main_each_method_n36": all(counts[m] == 36 for m in expected_methods),
            "main_shared_pair_keys": len({frozenset(v) for v in main_keys_by_method.values()}) == 1,
            "main_summary_matches_closed_overlay": True,
            "selector_row_count": len(selector_rows) == 60,
            "selector_shared_30_pools_K5": all(len(v) == 30 for v in selector_keys.values()),
            "constraint_row_count": len(constraint_rows) == 36,
            "constraint_shared_18_pair_keys": all(len(v) == 18 for v in constraint_keys.values()),
            "simple_local_36_same_state_pairs": len(local_pairs) == 36 and all(r.get("same_failure_time_state") for r in local_pairs),
        },
        "record_counts": {
            "fig3_main_rows": len(fig3_rows),
            "fig5_selector_rows": len(selector_rows),
            "fig5_constraint_rows": len(constraint_rows),
            "table5_simple_local_pairs": len(local_pairs),
        },
    }
    write_json(out("validation_report.json"), validation)

    # Manifest is written last and includes SHA-256 checksums for every source/output.
    source_manifest = []
    for key, path in paths.items():
        source_manifest.append({
            "id": key,
            "relative_path": path.relative_to(workspace).as_posix(),
            "sha256": sha256(path),
        })
    for seed_dir in sorted((results / "main_v6_4").glob("seed_*")):
        for name in ("b0.jsonl", "cds_mr.jsonl"):
            path = seed_dir / name
            source_manifest.append({
                "id": f"main_{seed_dir.name}_{name[:-6]}",
                "relative_path": path.relative_to(workspace).as_posix(),
                "sha256": sha256(path),
            })
    generated_manifest = [
        {"relative_path": p.relative_to(target).as_posix(), "sha256": sha256(p), "bytes": p.stat().st_size}
        for p in output_paths
    ]
    manifest = {
        "package_version": "figure_table_data_v1",
        "generated_date": "2026-09-13",
        "authoritative_main_summary": paths["main_overlay"].relative_to(workspace).as_posix(),
        "main_row_counts": dict(counts),
        "selector_row_count": len(selector_rows),
        "constraint_row_count": len(constraint_rows),
        "sources": source_manifest,
        "generated_files": generated_manifest,
        "warnings": [
            "Do not use summary_main_comparison_v6_4_seed13_18.csv for current external-baseline figures; its external rows predate evidence-chain closure.",
            "The 36 main pairs do not bind role/capability eligibility: GNSS is required, all roles are allowed, and every online replacement is eligible.",
            "Constraint sensitivity currently uses only seed 13 and is conditional trend evidence.",
            "Mission completion time is defined only for successful rows.",
            "Cross-adapter repair latency is an end-to-end observation, not a causal algorithm-cost estimate.",
        ],
    }
    manifest_path = target / "manifest.json"
    write_json(manifest_path, manifest)
    print(json.dumps({
        "target": str(target),
        "generated_files": len(output_paths) + 1,
        "main_rows": len(fig3_rows),
        "selector_rows": len(selector_rows),
        "constraint_rows": len(constraint_rows),
        "status": "PASS",
    }, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: build_figure_table_data.py WORKSPACE TARGET")
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
