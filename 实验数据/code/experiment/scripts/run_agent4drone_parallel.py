"""Run independent Agent4Drone/PyBullet tasks with bounded parallelism.

This runner preserves the baseline task protocol while reducing wall time:
each task receives a fresh Gym bridge and a fresh Agent4Drone API process,
and every worker uses its own pair of localhost ports.  The LLM, prompt,
task data, episode seed, action bridge, and success criteria are unchanged.

The output remains append-only JSONL and can be resumed safely.  Task records
may appear in completion order rather than input order; task_id is the stable
join key.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from run_agent4drone_baseline import (
    command_for_task,
    extract_tool_calls,
    read_tasks,
    wait_health,
)


def load_environment_file(path: Path | None) -> dict[str, str]:
    """Read a simple systemd EnvironmentFile without logging secret values."""
    if path is None or not path.exists():
        return {}
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    return values


def terminate_process(process: subprocess.Popen[Any] | None, timeout: float = 10.0) -> None:
    if process is None:
        return
    # Start bridge/API processes in their own session and terminate the whole
    # process group.  Do this even if the Popen leader has already exited:
    # uvicorn may leave a child listener behind after a timeout, causing the
    # next task on the same worker port to fail.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=timeout)


def run_agent_command(
    api_url: str,
    command: str,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    root = api_url.rstrip("/")
    accepted = requests.post(
        f"{root}/agent/command/async",
        json={"command": command},
        timeout=20,
    )
    accepted.raise_for_status()
    job_id = str(accepted.json()["job_id"])
    deadline = time.time() + timeout
    while time.time() < deadline:
        status_response = requests.get(f"{root}/agent/jobs/{job_id}", timeout=10)
        status_response.raise_for_status()
        payload = status_response.json()
        status = str(payload.get("status", ""))
        if status == "completed":
            result = payload.get("result") or {}
            if not isinstance(result, dict):
                raise RuntimeError(f"Agent job {job_id} returned a non-object result")
            return result
        if status in {"failed", "cancelled"}:
            raise RuntimeError(
                f"Agent job {job_id} ended with status={status}: {payload.get('error')}"
            )
        time.sleep(max(0.1, poll_interval))

    try:
        requests.post(f"{root}/agent/jobs/{job_id}/cancel", timeout=10)
    except requests.RequestException:
        pass
    raise TimeoutError(
        f"Agent job {job_id} exceeded command timeout={timeout}s; cancellation requested"
    )


def read_existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            task_id = record.get("task_id")
            if task_id:
                ids.add(str(task_id))
    return ids


def start_bridge(
    task: dict[str, Any],
    args: argparse.Namespace,
    bridge_port: int,
    log_handle: Any,
) -> subprocess.Popen[Any]:
    bridge_script = Path(__file__).with_name("gym_uav_bridge.py").resolve()
    command = [
        sys.executable,
        str(bridge_script),
        "--input",
        str(args.input),
        "--task-id",
        str(task["task_id"]),
        "--host",
        "127.0.0.1",
        "--port",
        str(bridge_port),
    ]
    bridge_max_steps = getattr(args, "bridge_max_steps", None)
    if bridge_max_steps is not None:
        command.extend(["--max-steps", str(int(bridge_max_steps))])
    environment = os.environ.copy()
    environment.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )


def start_agent(
    args: argparse.Namespace,
    agent_port: int,
    bridge_port: int,
    agent_environment: dict[str, str],
    log_handle: Any,
    agent_dir: Path | None = None,
) -> subprocess.Popen[Any]:
    environment = os.environ.copy()
    environment.update(agent_environment)
    environment["UAV_API_URL"] = f"http://127.0.0.1:{bridge_port}"
    environment.setdefault("PYTHONUNBUFFERED", "1")
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "agent_api_service:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(agent_port),
        "--log-level",
        args.uvicorn_log_level,
    ]
    return subprocess.Popen(
        command,
        cwd=agent_dir or args.agent_dir,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )


def run_one(
    task: dict[str, Any],
    worker_id: int,
    args: argparse.Namespace,
    agent_environment: dict[str, str],
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    bridge_port = args.bridge_port_base + worker_id
    agent_port = args.agent_port_base + worker_id
    bridge_url = f"http://127.0.0.1:{bridge_port}"
    agent_url = f"http://127.0.0.1:{agent_port}"
    safe_task_id = task_id.replace("/", "_").replace("\\", "_")
    bridge_log = args.log_dir / f"gym_bridge_{safe_task_id}.log"
    agent_log = args.log_dir / f"agent_api_{safe_task_id}.log"

    bridge: subprocess.Popen[Any] | None = None
    agent: subprocess.Popen[Any] | None = None
    metrics: dict[str, Any] | None = None
    timing: dict[str, float] = {}
    started = time.perf_counter()
    with (
        bridge_log.open("w", encoding="utf-8") as bridge_handle,
        agent_log.open("w", encoding="utf-8") as agent_handle,
    ):
        try:
            phase = time.perf_counter()
            bridge = start_bridge(task, args, bridge_port, bridge_handle)
            wait_health(bridge_url, args.startup_timeout)
            timing["bridge_startup_seconds"] = time.perf_counter() - phase

            phase = time.perf_counter()
            agent = start_agent(
                args,
                agent_port,
                bridge_port,
                agent_environment,
                agent_handle,
            )
            agent_health = wait_health(agent_url, args.startup_timeout, require_agent=True)
            timing["agent_startup_seconds"] = time.perf_counter() - phase

            phase = time.perf_counter()
            result = run_agent_command(
                agent_url,
                command_for_task(task),
                args.command_timeout,
                args.poll_interval,
            )
            timing["agent_command_seconds"] = time.perf_counter() - phase

            phase = time.perf_counter()
            metrics = requests.get(f"{bridge_url}/bridge/metrics", timeout=10).json()
            timing["metrics_seconds"] = time.perf_counter() - phase
            record: dict[str, Any] = {
                "protocol_version": "agent4drone_baseline_v1",
                "execution_mode": "isolated_parallel",
                "worker_id": worker_id,
                "task_id": task_id,
                "source_dataset": task.get("source_dataset"),
                "split_seed": task.get("metadata", {}).get("split_seed"),
                "episode_seed": task.get("metadata", {}).get("episode_seed"),
                "agent_health": agent_health,
                "api_success": result.get("success"),
                "api_output": result.get("output"),
                "token_usage": result.get("token_usage"),
                "intermediate_steps": result.get("intermediate_steps", []),
                "tool_calls": extract_tool_calls(result.get("intermediate_steps", [])),
                "gym_metrics": metrics,
                "bridge_log": str(bridge_log),
                "agent_log": str(agent_log),
            }
        except Exception as exc:
            record = {
                "protocol_version": "agent4drone_baseline_v1",
                "execution_mode": "isolated_parallel",
                "worker_id": worker_id,
                "task_id": task_id,
                "source_dataset": task.get("source_dataset"),
                "split_seed": task.get("metadata", {}).get("split_seed"),
                "episode_seed": task.get("metadata", {}).get("episode_seed"),
                "status": "runner_error",
                "error": repr(exc),
                "gym_metrics": metrics,
                "bridge_log": str(bridge_log),
                "agent_log": str(agent_log),
            }
            if bridge is not None and bridge.poll() is None:
                try:
                    metrics = requests.get(f"{bridge_url}/bridge/metrics", timeout=5).json()
                    record["gym_metrics"] = metrics
                except requests.RequestException:
                    pass
        finally:
            terminate_process(agent)
            terminate_process(bridge)

    timing["total_seconds"] = time.perf_counter() - started
    record["timing"] = {key: round(value, 6) for key, value in timing.items()}
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--bridge-port-base", type=int, default=8100)
    parser.add_argument("--agent-port-base", type=int, default=18100)
    parser.add_argument(
        "--agent-dir",
        type=Path,
        default=Path("~/uav_exp/MultiUAV-Plat/agent4drone"),
    )
    parser.add_argument(
        "--agent-env-file",
        type=Path,
        default=Path("~/.config/agent4drone/agent4drone.env"),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("~/uav_exp/data/logs/agent4drone_parallel"),
    )
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--command-timeout", type=float, default=180)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--uvicorn-log-level", default="warning")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.workers > 2:
        parser.error("this 8 GB VM is capped at --workers 2")
    if args.poll_interval < 0.1:
        parser.error("--poll-interval must be at least 0.1 seconds")
    if args.bridge_port_base + args.workers > args.agent_port_base:
        parser.error("bridge and agent port ranges overlap")

    args.input = args.input.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.agent_dir = args.agent_dir.expanduser().resolve()
    args.agent_env_file = args.agent_env_file.expanduser().resolve()
    args.log_dir = args.log_dir.expanduser().resolve()
    if not args.agent_dir.is_dir():
        parser.error(f"Agent4Drone directory not found: {args.agent_dir}")
    if not (args.agent_dir / "agent_api_service.py").is_file():
        parser.error(f"agent_api_service.py not found under {args.agent_dir}")

    tasks = read_tasks(args.input, args.task_id, args.limit)
    existing_ids = read_existing_ids(args.output)
    pending = [task for task in tasks if str(task["task_id"]) not in existing_ids]
    print(
        json.dumps(
            {
                "selected_tasks": len(tasks),
                "already_completed": len(tasks) - len(pending),
                "pending_tasks": len(pending),
                "workers": args.workers,
                "bridge_ports": [args.bridge_port_base + i for i in range(args.workers)],
                "agent_ports": [args.agent_port_base + i for i in range(args.workers)],
                "output": str(args.output),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.dry_run or not pending:
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    agent_environment = load_environment_file(args.agent_env_file)
    if "LLM_API_KEY" not in agent_environment and "LLM_API_KEY" not in os.environ:
        raise SystemExit(
            f"LLM_API_KEY not found in environment or {args.agent_env_file}"
        )

    output_lock = threading.Lock()
    worker_slots: queue.Queue[int] = queue.Queue()
    for worker_id in range(args.workers):
        worker_slots.put(worker_id)

    def execute(task: dict[str, Any]) -> dict[str, Any]:
        worker_id = worker_slots.get()
        try:
            return run_one(task, worker_id, args, agent_environment)
        finally:
            worker_slots.put(worker_id)

    mode = "a" if args.output.exists() else "w"
    completed = len(tasks) - len(pending)
    with (
        args.output.open(mode, encoding="utf-8") as output_handle,
        ThreadPoolExecutor(max_workers=args.workers) as executor,
    ):
        futures = {executor.submit(execute, task): task for task in pending}
        for future in as_completed(futures):
            task = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                record = {
                    "protocol_version": "agent4drone_baseline_v1",
                    "execution_mode": "isolated_parallel",
                    "task_id": str(task["task_id"]),
                    "status": "runner_error",
                    "error": repr(exc),
                }
            with output_lock:
                output_handle.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                output_handle.flush()
                completed += 1
                print(
                    f"{completed}/{len(tasks)} {record['task_id']} "
                    f"status={record.get('status', 'completed')} "
                    f"api_success={record.get('api_success')} "
                    f"seconds={(record.get('timing') or {}).get('total_seconds')}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
