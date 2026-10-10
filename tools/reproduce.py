"""Explicit, isolated execution of existing scripts; dry-run unless --execute."""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path("实验数据/results")
CODE = Path("实验数据/code")
SIMULATIONS = {
    "simple_local": "local_repair_vs_cds_mr_v1", "kinematic": "mechanism_stress_v2_vm_v10",
    "dynamic": "mechanism_dynamic_scale_v3_ctrl48_final_vm", "boundary": "mechanism_boundary_v1_vm",
    "grid": "suffix_grid_three_way_formal_v1", "random": "b3_independent_task_distribution_formal_v1",
}


def command_for(action, workspace, output):
    script = lambda name: str(workspace / CODE / name)
    if action in SIMULATIONS:
        command = [sys.executable, script("run_simple_local_vs_cds_mr.py"), "--input",
                   str(workspace / RESULTS / SIMULATIONS[action] / "tasks_recoverable_failure_v1.jsonl"),
                   "--output-dir", str(output)]
        if action in {"grid", "random"}:
            command.append("--include-global")
        return command
    if action == "main-summary":
        return [sys.executable, script("summarize_closed_main_comparison.py"), "--main-root",
                str(workspace / RESULTS / "main_v6_4"), "--external-root",
                str(workspace / RESULTS / "external_baseline_repro_v1"), "--output",
                str(output / "summary.json"), "--markdown", str(output / "summary.md")]
    if action == "ablation-summary":
        return [sys.executable, script("summarize_v6_3_preexec_ablation.py"), "--mode", "summary",
                "--input-dir", str(workspace / "实验数据/archive/previous/results/ablation_v6_4_seed8_12"),
                "--output", str(output / "summary.json"), "--markdown", str(output / "summary.md")]
    if action == "sensitivity-summary":
        return [sys.executable, script("summarize_constraint_sensitivity.py"), "--input",
                str(workspace / RESULTS / "constraint_sensitivity/raw_results.jsonl"), "--output",
                str(output / "summary.json"), "--markdown", str(output / "summary.md")]
    if action == "selection-summary":
        command = [sys.executable, script("summarize_sese_selection_evidence.py"), "--manifest",
                   str(workspace / "实验数据/scenarios/sese_selection/manifest.json")]
        for flag, name in [("--pools", "candidate_pools"), ("--executions", "candidate_executions"), ("--selections", "selections")]:
            command += [flag, str(workspace / RESULTS / "sese_selection" / (name + ".jsonl"))]
        return command + ["--output-json", str(output / "summary.json"), "--output-md", str(output / "summary.md"),
                          "--validation", str(output / "validation.json")]
    if action == "random-analysis":
        folder = workspace / RESULTS / SIMULATIONS["random"]
        return [sys.executable, script("analyze_b3_independent_distribution.py"), "--tasks",
                str(folder / "tasks_recoverable_failure_v1.jsonl"), "--results", str(folder),
                "--formal-audit", str(folder / "formal_metric_audit.json"), "--output-json",
                str(output / "analysis.json"), "--output-md", str(output / "analysis.md")]
    if action == "offline-analysis":
        return [sys.executable, script("offline_stability_control.py")]
    if action == "finite-analysis":
        return [sys.executable, str(workspace / "uav_exp/_paper_evidence_audit/validate_cds_mr_exhaustive_optimality.py"),
                "--input-root", str(workspace / RESULTS / "main_v6_4"), "--output-dir", str(output)]
    if action == "capability-analysis":
        folder = workspace / "uav_exp/_paper_evidence_audit/experiment_B_heterogeneous_capability_20260919"
        return [sys.executable, str(workspace / "uav_exp/_paper_evidence_audit/validate_experiment_b_heterogeneous_capability.py"),
                "--tasks", str(folder / "tasks.jsonl"), "--results", str(folder / "full/cds_mr.jsonl"),
                "--output-dir", str(output)]
    if action == "grid-analysis":
        folder = workspace / RESULTS / SIMULATIONS["grid"]
        return [sys.executable, script("audit_suffix_grid_formal_run.py"), "--tasks",
                str(folder / "tasks_recoverable_failure_v1.jsonl"), "--results", str(folder),
                "--output", str(output / "audit.json")]
    if action in {"plot3", "plot5", "plot67"}:
        relative = {"plot3": "scripts/fig3/plot_fig3.py", "plot5": "scripts/fig5/plot_fig5.py",
                    "plot67": "scripts/plot_suffix_results.py"}[action]
        return [sys.executable, str(workspace / "UAV_PAPER" / relative)]
    raise ValueError(action)


def main():
    actions = list(SIMULATIONS) + ["main-summary", "ablation-summary", "selection-summary", "sensitivity-summary",
                                  "random-analysis", "offline-analysis", "finite-analysis", "capability-analysis",
                                  "grid-analysis", "plot3", "plot5", "plot67"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=actions)
    parser.add_argument("--execute", action="store_true", help="Actually execute (simulations may take a long time)")
    args = parser.parse_args()
    run_id = args.action + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_root = ROOT / "generated" / run_id
    workspace = run_root / "workspace"
    output = run_root / "new_outputs"
    command = command_for(args.action, workspace, output)
    print(json.dumps({"execute": args.execute, "output_root": str(run_root), "command": command,
                      "archived_inputs_overwritten": False}, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return
    output.mkdir(parents=True, exist_ok=False)
    for directory in ("实验数据", "uav_exp", "UAV_PAPER", "图表数据", "paper_applied_intelligence"):
        shutil.copytree(ROOT / directory, workspace / directory)
    environment = os.environ.copy()
    additions = [str(workspace / CODE), str(workspace / CODE / "experiment/scripts")]
    environment["PYTHONPATH"] = os.pathsep.join(additions + [environment.get("PYTHONPATH", "")])
    environment.setdefault("MPLBACKEND", "Agg")
    code = subprocess.call(command, cwd=workspace, env=environment)
    print(json.dumps({"exit_code": code, "generated_root": str(run_root)}, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
