"""Preregistered B3 independent-task-distribution analysis."""
import argparse
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path

METHODS = {
    "Immediate local": "Simple_local_reassignment.jsonl",
    "CDS-MR": "CDS_MR.jsonl",
    "Global total-route": "Global_total_route_reallocation.jsonl",
}
METRICS = (
    "executed_suffix_path_length_m",
    "mission_completion_time_seconds",
    "estimated_full_suffix_route_cost",
    "normalized_plan_stability_distance",
    "repair_solver_wallclock_seconds",
    "repair_decision_latency_seconds",
)
LABELS = {
    "executed_suffix_path_length_m": "Executed path (m)",
    "mission_completion_time_seconds": "Completion time (s)",
    "estimated_full_suffix_route_cost": "Estimated suffix cost (m)",
    "normalized_plan_stability_distance": "NPSD",
    "repair_solver_wallclock_seconds": "Solver time (s)",
    "repair_decision_latency_seconds": "Decision latency (s)",
}
SEED = 20289999
REPS = 10_000
MARGIN = 0.01


def read_jsonl(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def quantile(xs, q):
    if not xs:
        return None
    a = sorted(xs)
    z = (len(a) - 1) * q
    lo, hi = math.floor(z), math.ceil(z)
    return a[lo] if lo == hi else a[lo] * (hi - z) + a[hi] * (z - lo)


def desc(xs):
    return {
        "n": len(xs),
        "mean": statistics.fmean(xs) if xs else None,
        "sd": statistics.stdev(xs) if len(xs) > 1 else (0.0 if xs else None),
        "median": statistics.median(xs) if xs else None,
        "p25": quantile(xs, 0.25),
        "p75": quantile(xs, 0.75),
    }


def bootstrap_ci(xs, rng):
    if not xs:
        return [None, None]
    n = len(xs)
    means = [statistics.fmean(xs[rng.randrange(n)] for _ in range(n)) for _ in range(REPS)]
    return [quantile(means, 0.025), quantile(means, 0.975)]


def wilson(k, n):
    if not n:
        return [None, None]
    z = 1.959963984540054
    p = k / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    r = z * math.sqrt((p*(1-p) + z*z/(4*n))/n) / d
    return [max(0.0, c-r), min(1.0, c+r)]


def metrics(row):
    return (row or {}).get("standard_metrics") or {}


def valid(row):
    return bool(row and row.get("runner_status") == "completed" and isinstance(metrics(row), dict))


def strict(row):
    m = metrics(row)
    return valid(row) and m.get("recovery_success_rate_item") is True and m.get("strict_contract_satisfied") is True


def val(row, key):
    x = metrics(row).get(key)
    try:
        return float(x) if x is not None and math.isfinite(float(x)) else None
    except (TypeError, ValueError):
        return None


def category(base, challenger):
    if not valid(base) or not valid(challenger):
        return "missing_or_invalid"
    a, b = strict(base), strict(challenger)
    if b and not a:
        return "benefit"
    if a and not b:
        return "regression"
    if not a and not b:
        return "joint_failure"
    x, y = val(base, "executed_suffix_path_length_m"), val(challenger, "executed_suffix_path_length_m")
    if x is None or y is None or x <= 0:
        return "missing_or_invalid"
    delta = (x-y)/x
    return "benefit" if delta >= MARGIN else "regression" if delta <= -MARGIN else "practical_tie"


def assignments(row):
    plan = (row or {}).get("repair", {}).get("repair_plan")
    if not isinstance(plan, list):
        return None
    return tuple(sorted((str(i.get("bundle_id")), str(i.get("assigned_drone"))) for i in plan))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--formal-audit", type=Path, required=True)
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--output-md", type=Path, required=True)
    a = ap.parse_args()

    tasks = read_jsonl(a.tasks)
    tids = [str(t["task_id"]) for t in tasks]
    task_by_id = {str(t["task_id"]): t for t in tasks}
    if len(task_by_id) != len(tids):
        raise SystemExit("duplicate task id in frozen input")
    rows = {}
    duplicates = {}
    for method, filename in METHODS.items():
        raw = read_jsonl(a.results / filename)
        counts = Counter(str(r.get("task_id")) for r in raw)
        duplicates[method] = sorted(k for k, n in counts.items() if n != 1)
        rows[method] = {str(r.get("task_id")): r for r in raw}

    audit = json.loads(a.formal_audit.read_text(encoding="utf-8"))
    if audit.get("verdict") != "PASS" or audit.get("scope") != "formal_run":
        raise SystemExit("formal metric audit must pass before analysis")
    rng = random.Random(SEED)

    method_summary = {}
    for method, data in rows.items():
        records = [data.get(t) for t in tids]
        summary = {}
        for key in METRICS:
            xs = [x for r in records if (x := val(r, key)) is not None]
            summary[key] = desc(xs)
        method_summary[method] = {
            "n_expected": len(tids),
            "n_present": sum(r is not None for r in records),
            "n_runner_completed": sum(valid(r) for r in records),
            "n_recovered_and_strict": sum(strict(r) for r in records),
            "recovery_strict_rate_over_all_expected": sum(strict(r) for r in records)/len(tids),
            "n_strict_pass": sum(valid(r) and metrics(r).get("strict_contract_satisfied") is True for r in records),
            "metrics": summary,
            "selected_B2_owner_counts": dict(Counter(
                next((str(i.get("assigned_drone")) for i in (r or {}).get("repair", {}).get("repair_plan", [])
                      if str(i.get("bundle_id")) == "B2"), "missing")
                for r in records
            )),
            "runner_error_or_missing_ids": [
                t for t, r in zip(tids, records) if not valid(r)
            ],
        }

    comparisons = {}
    pairs_for = [
        ("Immediate local", "CDS-MR"),
        ("Immediate local", "Global total-route"),
        ("CDS-MR", "Global total-route"),
    ]
    cats = ("benefit", "practical_tie", "regression", "joint_failure", "missing_or_invalid")
    for base_name, challenger_name in pairs_for:
        counter = Counter()
        by_structure = {}
        diffs = {key: [] for key in METRICS}
        rel_path = []
        equal_assignments = 0
        for tid in tids:
            base, challenger = rows[base_name].get(tid), rows[challenger_name].get(tid)
            c = category(base, challenger)
            counter[c] += 1
            structure = str((task_by_id[tid].get("metadata") or {}).get("structure_factor", "unknown"))
            by_structure.setdefault(structure, Counter())[c] += 1
            equal_assignments += assignments(base) == assignments(challenger)
            if strict(base) and strict(challenger):
                for key in METRICS:
                    x, y = val(base, key), val(challenger, key)
                    if x is not None and y is not None:
                        diffs[key].append(x-y)
                x, y = val(base, "executed_suffix_path_length_m"), val(challenger, "executed_suffix_path_length_m")
                if x is not None and y is not None and x > 0:
                    rel_path.append((x-y)/x)
        counts = {
            c: {
                "count": counter[c],
                "proportion_all_90": counter[c]/len(tids),
                "wilson_95_ci": wilson(counter[c], len(tids)),
            } for c in cats
        }
        paired = {}
        for key, xs in diffs.items():
            paired[key] = {
                "baseline_minus_challenger": desc(xs),
                "paired_bootstrap_95_ci": bootstrap_ci(xs, rng),
                "n_strict_pairs": len(xs),
            }
        paired["relative_path_reduction"] = {
            "baseline_minus_challenger": desc(rel_path),
            "paired_bootstrap_95_ci": bootstrap_ci(rel_path, rng),
            "n_strict_pairs": len(rel_path),
            "positive_favors_challenger": True,
        }
        comparisons[f"{challenger_name} vs {base_name}"] = {
            "baseline": base_name,
            "challenger": challenger_name,
            "categories": counts,
            "by_structure": {
                s: {"n": sum(by_structure[s].values()), **{c: by_structure[s][c] for c in cats}}
                for s in sorted(by_structure)
            },
            "paired": paired,
            "identical_assignment_tuple_count": equal_assignments,
        }

    local_failures = []
    local_checks = Counter()
    local_by_structure = Counter()
    for tid in tids:
        row = rows["Immediate local"].get(tid)
        if not strict(row):
            structure = str((task_by_id[tid].get("metadata") or {}).get("structure_factor", "unknown"))
            checks = metrics(row).get("contract_checks") or {}
            local_by_structure[structure] += 1
            for check, passed in checks.items():
                if passed is False:
                    local_checks[check] += 1
            local_failures.append({
                "task_id": tid,
                "structure": structure,
                "runner_status": row.get("runner_status") if row else "missing",
                "failure_reason": ((row or {}).get("failure_event") or {}).get("reason"),
                "contract_checks": checks,
            })

    report = {
        "protocol": "b3_independent_task_distribution_analysis_v1",
        "scope": "descriptive for the preregistered synthetic random-layout generator, not real-world prevalence",
        "n_tasks": len(tasks),
        "input_sha256": sha(a.tasks),
        "formal_audit_sha256": sha(a.formal_audit),
        "result_sha256": {name: sha(a.results/filename) for name, filename in METHODS.items()},
        "analysis_script_sha256": sha(Path(__file__).resolve()),
        "bootstrap_reps": REPS,
        "bootstrap_seed": SEED,
        "practical_path_margin_relative": MARGIN,
        "method_summary": method_summary,
        "comparisons": comparisons,
        "local_edit_failures": {
            "count": len(local_failures),
            "by_structure": dict(local_by_structure),
            "failed_contract_checks": dict(local_checks),
            "cases": local_failures,
        },
        "duplicate_or_nonunique_ids": duplicates,
        "method_id_sets_equal_frozen_input": {
            name: set(data) == set(tids) and not duplicates[name] for name, data in rows.items()
        },
        "formal_integrity_audit_verdict": audit.get("verdict"),
        "analysis_checks": {
            "90_tasks": len(tasks) == 90,
            "all_three_method_id_sets_match": all(set(data) == set(tids) and not duplicates[name] for name, data in rows.items()),
            "formal_audit_pass": audit.get("verdict") == "PASS",
        },
    }
    report["analysis_checks"]["analysis_complete"] = all(report["analysis_checks"].values())
    if not report["analysis_checks"]["analysis_complete"]:
        raise SystemExit("B3 analysis checks failed")

    a.output_json.parent.mkdir(parents=True, exist_ok=True)
    a.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    labels = {
        "Immediate local": "Immediate local",
        "CDS-MR": "CDS-MR",
        "Global total-route": "Global total-route",
    }
    lines = [
        "# B3 独立任务分布测试：正式结果",
        "",
        f"正式样本 {len(tasks)} 例；链/分支/汇合各30例。正式指标审计：{audit['verdict']}。",
        f"冻结任务 SHA-256：{report['input_sha256']}。",
        "",
        "## 三方法结果",
        "",
        "| 方法 | 恢复且严格通过 | 实测路径均值±SD (m) | 完成时间均值±SD (s) | NPSD均值 | 求解时间中位数 (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        s = method_summary[method]
        p = s["metrics"]["executed_suffix_path_length_m"]
        tm = s["metrics"]["mission_completion_time_seconds"]
        np = s["metrics"]["normalized_plan_stability_distance"]
        so = s["metrics"]["repair_solver_wallclock_seconds"]
        lines.append(
            f"| {labels[method]} | {s['n_recovered_and_strict']}/90 | "
            f"{p['mean']:.4f}±{p['sd']:.4f} | {tm['mean']:.3f}±{tm['sd']:.3f} | "
            f"{np['mean']:.4f} | {so['median']*1000:.3f} |"
        )

    main = comparisons["CDS-MR vs Immediate local"]
    lines.extend([
        "",
        "## CDS-MR相对即时局部：获益/持平/劣化",
        "",
        "双方严格成功时，以实测路径相对变化为主：改善≥1%记获益，恶化≥1%记劣化，其余记实际持平；只有一方严格成功时按可行性分类。共同失败与技术无效单独报告。",
        "",
        "| 类别 | 数量 | 比例 | Wilson 95% CI |",
        "|---|---:|---:|---:|",
    ])
    cat_labels = {
        "benefit": "CDS-MR获益",
        "practical_tie": "实际持平",
        "regression": "CDS-MR劣化",
        "joint_failure": "双方均未严格恢复",
        "missing_or_invalid": "缺失/技术无效",
    }
    for key in cats:
        d = main["categories"][key]
        ci = d["wilson_95_ci"]
        lines.append(f"| {cat_labels[key]} | {d['count']}/90 | {d['proportion_all_90']:.1%} | [{ci[0]:.1%}, {ci[1]:.1%}] |")
    lines.extend([
        "",
        "## 连续配对差值",
        "",
        "方向为即时局部减比较方法，正数有利于比较方法；仅在两方法均严格成功的配对上计算。区间为预注册的10,000次 paired bootstrap percentile 95% CI。",
        "",
        "| 比较 | 指标 | n | 平均差 | SD | paired 95% CI |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for comp in ("CDS-MR vs Immediate local", "Global total-route vs Immediate local", "Global total-route vs CDS-MR"):
        for key in ("executed_suffix_path_length_m", "mission_completion_time_seconds", "estimated_full_suffix_route_cost", "repair_solver_wallclock_seconds", "repair_decision_latency_seconds"):
            d = comparisons[comp]["paired"][key]
            st, ci = d["baseline_minus_challenger"], d["paired_bootstrap_95_ci"]
            lines.append(f"| {comp} | {LABELS[key]} | {st['n']} | {st['mean']:.6f} | {st['sd']:.6f} | [{ci[0]:.6f}, {ci[1]:.6f}] |")
    lines.extend(["", "## 结构分层与局部编辑失败", "", "| 结构 | n | 获益 | 持平 | 劣化 | 双方失败 | 缺失/无效 |", "|---|---:|---:|---:|---:|---:|---:|"])
    for structure, d in main["by_structure"].items():
        lines.append(f"| {structure} | {d['n']} | {d['benefit']} | {d['practical_tie']} | {d['regression']} | {d['joint_failure']} | {d['missing_or_invalid']} |")
    if local_failures:
        lines.extend([
            "",
            f"即时局部严格失败 {len(local_failures)} 例；按结构：{json.dumps(dict(local_by_structure), ensure_ascii=False)}；合同检查失败计数：{json.dumps(dict(local_checks), ensure_ascii=False)}。",
            "失败实例ID：" + ", ".join(x["task_id"] for x in local_failures),
        ])
    else:
        lines.extend([
            "",
            "即时局部在本90例中无严格恢复失败，因此该生成分布未观察到局部编辑失败区域；这不表示其他分布中不存在失败区域。",
        ])
    lines.extend([
        "",
        "## 解释边界",
        "",
        "这90例是正式运行前按独立随机几何生成器冻结、未按方法获胜条件筛选的任务；链、分支、汇合比例人为平衡。获益比例只描述该生成器下的样本，不能解释为实际任务的普遍发生率。仿真基于 PyBullet/VelocityAviary 航点投影，不代表实飞，也未包含天气、禁飞区和移动障碍的完整物理约束。",
        "",
        f"输入、方法结果流和指标审计哈希记录在 {a.output_json.name}；样本有效性与抽样种子记录在 generation_audit.json。",
    ])
    a.output_md.parent.mkdir(parents=True, exist_ok=True)
    a.output_md.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({
        "analysis_checks": report["analysis_checks"],
        "categories_CDS_vs_local": main["categories"],
        "methods": {
            m: {
                "recovered_strict": method_summary[m]["n_recovered_and_strict"],
                "path_mean": method_summary[m]["metrics"]["executed_suffix_path_length_m"]["mean"],
                "time_mean": method_summary[m]["metrics"]["mission_completion_time_seconds"]["mean"],
            } for m in METHODS
        },
        "local_edit_failures": len(local_failures),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
