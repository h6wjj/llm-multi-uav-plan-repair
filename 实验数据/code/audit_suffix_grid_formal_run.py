"""Post-run, formal-scope wrapper for the frozen three-way metric auditor.

The underlying audit implementation is unchanged. This wrapper gives formal
results an accurate scope label, checks the run manifest and local code hashes,
and renames the pilot-specific success check before writing a separate report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from audit_suffix_grid_three_way import audit


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_tasks(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--code-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    tasks_path = args.tasks.expanduser().resolve()
    results_dir = args.results.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    code_dir = args.code_dir.expanduser().resolve()
    manifest_path = results_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = read_tasks(tasks_path)

    report = audit(tasks_path, results_dir, require_success=True)
    checks = report["checks"]
    pilot_check = checks.pop("all_pilot_runs_completed_and_strict", None)
    if pilot_check is not None:
        checks["all_formal_method_runs_completed_and_strict"] = pilot_check

    # Match run_simple_local_vs_cds_mr.source_tasks ordering exactly. The
    # frozen input may be grouped differently from the execution order.
    ordered_tasks = sorted(tasks, key=lambda row: (
        int((row.get("metadata") or {}).get("run_seed_index", 0)),
        str((row.get("metadata") or {}).get("source_task_id", "")),
        str(row.get("task_id", "")),
    ))
    task_id_digest = hashlib.sha256(
        json.dumps([str(task["task_id"]) for task in ordered_tasks], separators=(",", ":")).encode()
    ).hexdigest()
    checks["formal_input_hash_matches_run_manifest"] = (
        sha256_file(tasks_path) == manifest.get("source_input_sha256")
    )
    checks["formal_task_count_matches_run_manifest"] = len(tasks) == manifest.get("task_count")
    checks["formal_task_id_digest_matches_run_manifest"] = (
        task_id_digest == manifest.get("task_ids_sha256")
    )

    code_hash_fields = {
        "run_simple_local_vs_cds_mr.py": "script_sha256",
        "gym_uav_bridge.py": "bridge_script_sha256",
        "global_suffix_reallocation.py": "global_solver_script_sha256",
        "run_cds_mr_contract_online.py": "cds_solver_script_sha256",
        "v6_metric_evaluator.py": "metric_evaluator_script_sha256",
        "gym_task_adapter.py": "adapter_script_sha256",
        "audit_suffix_grid_three_way.py": "auditor_script_sha256",
    }
    local_code_hashes = {
        filename: sha256_file(code_dir / filename) for filename in code_hash_fields
    }
    checks["local_behavior_code_hashes_match_run_manifest"] = all(
        local_code_hashes[filename] == manifest.get(manifest_field)
        for filename, manifest_field in code_hash_fields.items()
    )

    report["protocol"] = "suffix_grid_three_way_formal_metric_audit_v1"
    report["scope"] = "formal_run"
    report["formal_task_count"] = len(tasks)
    report["formal_methods"] = manifest.get("methods", [])
    report["run_manifest_sha256"] = sha256_file(manifest_path)
    report["run_manifest_auditor_script_sha256"] = manifest.get("auditor_script_sha256")
    report["local_behavior_code_sha256"] = local_code_hashes
    report["audit_core_script_sha256"] = sha256_file(code_dir / "audit_suffix_grid_three_way.py")
    report["audit_script_sha256"] = sha256_file(Path(__file__).resolve())
    report["audit_note"] = (
        "Post-run formal-scope wrapper around the frozen metric auditor; it does not alter or rerun any method. "
        "The original metric_audit.json is retained as generated, including its pilot_only label."
    )
    report["audit_command"] = [
        Path(__file__).name,
        "--tasks",
        str(tasks_path),
        "--results",
        str(results_dir),
        "--output",
        str(output_path),
        "--code-dir",
        str(code_dir),
    ]
    report["verdict"] = "PASS" if all(checks.values()) else "FAIL"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"verdict": report["verdict"], "scope": report["scope"], "checks": checks, "task_count": len(tasks)},
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
