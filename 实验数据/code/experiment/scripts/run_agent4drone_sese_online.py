"""Online SeSE-style candidate sampling for the fixed UAVBench pilot.

Each candidate is generated and executed in its own fresh Gym bridge and
Agent4Drone process.  The gate then selects a candidate using only normalized
tool traces and canonical waypoints; simulator success/reward is never passed
to the selector.  The selected candidate's already-isolated rollout metrics
are retained for reporting.  Candidate zero is the B0-temperature anchor;
the other candidates use a non-zero temperature to create genuine samples.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import signal
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from sese_gate import multi_candidate_gate
from imprecise_higher_order_gate import multi_candidate_gate_ihou
from diverse_sampling import candidate_command
from diversity_gate import multi_candidate_gate_dcs
from lens_router import route_third_candidate
from btad_local_repair import accept_repair, compare_edit_scope, diagnose_plan, local_repair_command
from run_agent4drone_baseline import command_for_task, extract_tool_calls, read_tasks
from run_agent4drone_parallel import (
    load_environment_file,
    run_agent_command,
    start_agent,
    start_bridge,
    terminate_process,
    wait_health,
)


def _token_count(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    if usage.get("total_tokens") is not None:
        return int(usage.get("total_tokens") or 0)
    return sum(int(usage.get(key, 0) or 0) for key in ("prompt_tokens", "completion_tokens"))


def _prepare_agent_dirs(
    base: Path,
    temperatures: list[float],
    model_override: str | None = None,
) -> tuple[Path, list[Path]]:
    root = Path(tempfile.mkdtemp(prefix="agent4drone_sese_online_"))
    directories: list[Path] = []
    for index, temperature in enumerate(temperatures):
        target = root / f"candidate_{index}"
        shutil.copytree(base, target)
        settings_path = target / "llm_settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["temperature"] = float(temperature)
        if model_override:
            provider = settings.setdefault("provider_configs", {}).setdefault("Deepseek API", {})
            provider["default_model"] = str(model_override)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(settings_path, 0o600)
        except OSError:
            pass
        directories.append(target)
    return root, directories


def _candidate_command(task: dict[str, Any], index: int, sampling_mode: str = "stationary") -> str:
    base = command_for_task(task)
    if sampling_mode == "diverse":
        return candidate_command(base, index, sampling_mode)
    if index == 0:
        return base
    return base + (
        f"\n\nThis is independent candidate {index} of a pre-execution uncertainty "
        "sample. Produce one complete plan independently; do not refer to any "
        "other candidate and do not alter the canonical waypoint order."
    )


def _run_candidate(
    task: dict[str, Any],
    candidate_index: int,
    worker_id: int,
    args: argparse.Namespace,
    agent_environment: dict[str, str],
    agent_dir: Path,
    command_override: str | None = None,
    slot_index: int | None = None,
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    slots_per_task = args.num_candidates + (1 if args.gate_mode == "btad" else 0)
    slot = worker_id * slots_per_task + (candidate_index if slot_index is None else slot_index)
    bridge_port = args.bridge_port_base + slot
    agent_port = args.agent_port_base + slot
    bridge_url = f"http://127.0.0.1:{bridge_port}"
    agent_url = f"http://127.0.0.1:{agent_port}"
    safe = task_id.replace("/", "_").replace("\\", "_")
    bridge_log = args.log_dir / f"sese_bridge_{safe}_candidate{candidate_index}.log"
    agent_log = args.log_dir / f"sese_agent_{safe}_candidate{candidate_index}.log"
    bridge = None
    agent = None
    started = time.perf_counter()
    with bridge_log.open("w", encoding="utf-8") as bridge_handle, agent_log.open("w", encoding="utf-8") as agent_handle:
        try:
            bridge = start_bridge(task, args, bridge_port, bridge_handle)
            wait_health(bridge_url, args.startup_timeout)
            agent = start_agent(args, agent_port, bridge_port, agent_environment, agent_handle, agent_dir=agent_dir)
            health = wait_health(agent_url, args.startup_timeout, require_agent=True)
            phase = time.perf_counter()
            command = command_override or _candidate_command(task, candidate_index, args.sampling_mode)
            result = run_agent_command(agent_url, command, args.command_timeout, args.poll_interval)
            command_seconds = time.perf_counter() - phase
            metrics = __import__("requests").get(f"{bridge_url}/bridge/metrics", timeout=10).json()
            metric_task_id = metrics.get("task_id") if isinstance(metrics, dict) else None
            if metric_task_id is not None and str(metric_task_id) != task_id:
                raise RuntimeError(
                    f"bridge_metrics_task_mismatch: expected={task_id} got={metric_task_id}"
                )
            calls = extract_tool_calls(result.get("intermediate_steps", []))
            return {
                "candidate_index": candidate_index,
                "agent_health": health,
                "api_success": result.get("success"),
                "token_usage": result.get("token_usage"),
                "tool_calls": calls,
                "gym_metrics": metrics,
                "timing": {"command_seconds": round(command_seconds, 6), "total_seconds": round(time.perf_counter() - started, 6)},
                "bridge_log": str(bridge_log),
                "agent_log": str(agent_log),
            }
        except Exception as exc:
            return {
                "candidate_index": candidate_index,
                "status": "runner_error",
                "error": repr(exc),
                "tool_calls": [],
                "gym_metrics": None,
                "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
                "bridge_log": str(bridge_log),
                "agent_log": str(agent_log),
            }
        finally:
            terminate_process(agent)
            terminate_process(bridge)


def _run_task(
    task: dict[str, Any],
    worker_id: int,
    args: argparse.Namespace,
    env: dict[str, str],
    agent_dirs: list[Path],
    routed_dirs: dict[str, list[Path]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    routing: dict[str, Any] | None = None
    model_per_candidate: list[str] = []
    if args.gate_mode == "lens":
        if not routed_dirs or "flash" not in routed_dirs or "pro" not in routed_dirs:
            raise RuntimeError("LENS mode requires Flash and Pro agent directories")
        flash_dirs = routed_dirs["flash"]
        pro_dirs = routed_dirs["pro"]
        first = _run_candidate(task, 0, worker_id, args, env, flash_dirs[0])
        second = _run_candidate(task, 1, worker_id, args, env, flash_dirs[1])
        candidates = [first, second]
        model_per_candidate.extend([args.lens_flash_model, args.lens_flash_model])
        routing = route_third_candidate(
            task.get("goal", {}).get("waypoints", []),
            first.get("tool_calls", []),
            second.get("tool_calls", []),
            flash_model=args.lens_flash_model,
            pro_model=args.lens_pro_model,
            precision_threshold=args.lens_precision_threshold,
            entropy_threshold=args.entropy_threshold,
        )
        third_model = str(routing["selected_model"])
        third_dirs = pro_dirs if third_model == args.lens_pro_model else flash_dirs
        third = _run_candidate(task, 2, worker_id, args, env, third_dirs[2])
        candidates.append(third)
        model_per_candidate.append(third_model)
    else:
        candidates = [_run_candidate(task, index, worker_id, args, env, agent_dirs[index]) for index in range(args.num_candidates)]
    calls = [item.get("tool_calls", []) if isinstance(item.get("tool_calls"), list) else [] for item in candidates]
    gate_fn = (
        multi_candidate_gate_dcs if args.gate_mode == "dcs"
        else multi_candidate_gate_ihou if args.gate_mode == "sese_ihou"
        else multi_candidate_gate
    )
    gate = gate_fn(task.get("goal", {}).get("waypoints", []), calls, args.entropy_threshold)
    selected = gate.get("selected_index")
    selected = int(selected) if selected is not None and 0 <= int(selected) < len(candidates) else 0
    selected_record = candidates[selected]
    # Preserve the SeSE decision before any optional BTAD repair.  This is
    # the paired comparator for the final BTAD result in the same task.
    sese_gate = dict(gate)
    sese_selected_candidate_index = selected
    sese_selected_candidate = selected_record
    btad_diagnosis: dict[str, Any] | None = None
    btad_repair: dict[str, Any] | None = None
    btad_edit_scope: dict[str, Any] | None = None
    if args.gate_mode == "btad":
        expected = task.get("goal", {}).get("waypoints", [])
        btad_diagnosis = diagnose_plan(task, selected_record.get("tool_calls", []))
        if btad_diagnosis.get("repair_required"):
            repair_command = local_repair_command(task, selected_record.get("tool_calls", []), btad_diagnosis)
            repair_record = _run_candidate(
                task,
                len(candidates),
                worker_id,
                args,
                env,
                agent_dirs[0],
                command_override=repair_command,
                slot_index=args.num_candidates,
            )
            candidates.append(repair_record)
            repaired_diagnosis = diagnose_plan(task, repair_record.get("tool_calls", []))
            original_coverage = float(gate.get("selected_coverage", 0.0))
            repair_calls = [selected_record.get("tool_calls", []), repair_record.get("tool_calls", [])]
            repair_gate = multi_candidate_gate(expected, repair_calls, args.entropy_threshold)
            repaired_coverage = float(repair_gate.get("selected_coverage", 0.0)) if int(repair_gate.get("selected_index", 0) or 0) == 1 else original_coverage
            btad_repair = accept_repair(
                btad_diagnosis,
                repaired_diagnosis,
                original_coverage=original_coverage,
                repaired_coverage=repaired_coverage,
            )
            btad_edit_scope = compare_edit_scope(
                selected_record.get("tool_calls", []),
                repair_record.get("tool_calls", []),
                btad_diagnosis,
            )
            btad_repair["repaired_diagnosis"] = repaired_diagnosis
            btad_repair["repair_gate"] = repair_gate
            if btad_repair.get("accepted"):
                selected = len(candidates) - 1
                selected_record = repair_record
                gate = dict(gate)
                gate["selected_index"] = selected
                gate["selected_coverage"] = round(repaired_coverage, 6)
                gate["selected_by_btad"] = True
            else:
                gate = dict(gate)
                gate["selected_by_btad"] = False
    return {
        "protocol_version": "agent4drone_sese_online_v1",
        "method": (
            "B0+SeSE+DCS" if args.gate_mode == "dcs"
            else "B0+SeSE+IHOU" if args.gate_mode == "sese_ihou"
            else "B0+SeSE+LENS" if args.gate_mode == "lens"
            else "B0+SeSE+BTAD-style-local-repair" if args.gate_mode == "btad"
            else "B0+SeSE-style structural-entropy gate"
        ),
        "gate_mode": args.gate_mode,
        "sampling_mode": args.sampling_mode,
        "task_id": str(task["task_id"]),
        "source_dataset": task.get("source_dataset"),
        "split_seed": task.get("metadata", {}).get("split_seed"),
        "episode_seed": task.get("metadata", {}).get("episode_seed"),
        "candidate_temperature": args.candidate_temperature,
        "candidate_temperatures": [0.0] + [args.candidate_temperature] * max(0, args.num_candidates - 1),
        "candidate_count": len(candidates),
        "selection_uses_simulator_outcome": False,
        "model_per_candidate": model_per_candidate,
        "lens_routing": routing,
        "btad_diagnosis": btad_diagnosis,
        "btad_repair": btad_repair,
        "btad_edit_scope": btad_edit_scope,
        "sese_gate": sese_gate,
        "sese_selected_candidate_index": sese_selected_candidate_index,
        "sese_selected_candidate": sese_selected_candidate,
        "selected_candidate_index": selected,
        "gate": gate,
        "selected_candidate": selected_record,
        "candidates": candidates,
        "total_tokens": sum(_token_count(item.get("token_usage")) for item in candidates),
        "timing": {"total_seconds": round(time.perf_counter() - started, 6)},
    }


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or not record.get("task_id"):
                raise ValueError(f"invalid JSONL record at {path}:{line_number}")
            ids.add(str(record["task_id"]))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--num-candidates", type=int, default=3)
    parser.add_argument("--candidate-temperature", type=float, default=0.2)
    parser.add_argument("--entropy-threshold", type=float, default=0.8)
    parser.add_argument("--gate-mode", choices=["sese", "sese_ihou", "dcs", "lens", "btad"], default="sese")
    parser.add_argument("--sampling-mode", choices=["stationary", "diverse"], default="stationary")
    parser.add_argument("--lens-flash-model", default="deepseek-v4-flash")
    parser.add_argument("--lens-pro-model", default="deepseek-v4-pro")
    parser.add_argument("--lens-precision-threshold", type=float, default=0.80)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--bridge-port-base", type=int, default=8300)
    parser.add_argument("--agent-port-base", type=int, default=18300)
    parser.add_argument("--agent-dir", type=Path, default=Path("~/uav_exp/MultiUAV-Plat/agent4drone"))
    parser.add_argument("--agent-env-file", type=Path, default=Path("~/.config/agent4drone/agent4drone.env"))
    parser.add_argument("--log-dir", type=Path, default=Path("~/uav_exp/data/logs/agent4drone_sese_online"))
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--command-timeout", type=float, default=180)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--uvicorn-log-level", default="warning")
    args = parser.parse_args()
    if args.num_candidates < 2:
        parser.error("--num-candidates must be at least 2")
    if args.workers != 1:
        parser.error("online SeSE pilot currently requires --workers 1 to keep candidate ports and temp settings isolated")
    args.input = args.input.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.agent_dir = args.agent_dir.expanduser().resolve()
    args.agent_env_file = args.agent_env_file.expanduser().resolve()
    args.log_dir = args.log_dir.expanduser().resolve()
    tasks = read_tasks(args.input, limit=args.limit)
    if len(tasks) != args.limit:
        raise SystemExit(f"expected {args.limit} tasks, found {len(tasks)}")
    existing = _existing_ids(args.output)
    pending = [task for task in tasks if str(task["task_id"]) not in existing]
    env = load_environment_file(args.agent_env_file)
    if "LLM_API_KEY" not in env and "LLM_API_KEY" not in os.environ:
        raise SystemExit(f"LLM_API_KEY not found in environment or {args.agent_env_file}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    temperatures = [0.0] + [args.candidate_temperature] * (args.num_candidates - 1)
    temp_roots: list[Path] = []
    routed_dirs: dict[str, list[Path]] | None = None
    if args.gate_mode == "lens":
        flash_root, flash_dirs = _prepare_agent_dirs(args.agent_dir, temperatures, args.lens_flash_model)
        pro_root, pro_dirs = _prepare_agent_dirs(args.agent_dir, temperatures, args.lens_pro_model)
        temp_roots.extend([flash_root, pro_root])
        agent_dirs = flash_dirs
        routed_dirs = {"flash": flash_dirs, "pro": pro_dirs}
    else:
        temp_root, agent_dirs = _prepare_agent_dirs(args.agent_dir, temperatures)
        temp_roots.append(temp_root)
    print(json.dumps({"tasks": len(tasks), "already_completed": len(tasks) - len(pending), "pending": len(pending), "candidates_per_task": args.num_candidates, "candidate_temperature": args.candidate_temperature, "output": str(args.output)}, ensure_ascii=False), flush=True)
    try:
        mode = "a" if existing else "w"
        with args.output.open(mode, encoding="utf-8") as handle:
            completed = len(existing)
            for task in pending:
                record = _run_task(task, 0, args, env, agent_dirs, routed_dirs)
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                completed += 1
                print(f"{completed}/{len(tasks)} {record['task_id']} selected={record['selected_candidate_index']} coverage={record['gate'].get('selected_coverage')}", flush=True)
    finally:
        for temp_root in temp_roots:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
