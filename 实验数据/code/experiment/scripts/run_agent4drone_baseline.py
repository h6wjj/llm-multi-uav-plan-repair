"""Run Agent4Drone on fixed tasks through the Gym bridge and save auditable traces.

The runner starts one single-task Gym bridge, restarts the Agent4Drone user
service so its session context matches that task, sends one API command, then
collects the bridge rollout metrics.  It is intentionally incremental: each
completed task is appended as one JSONL record so an interrupted run can be
resumed without losing previous API results.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from v6_metric_evaluator import common_metrics


def read_tasks(path: Path, task_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            if task_id is not None and record.get("task_id") != task_id:
                continue
            records.append(record)
            if limit is not None and len(records) >= limit:
                break
    return records


def wait_health(url: str, timeout: float, require_agent: bool = False) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = "not started"
    while time.time() < deadline:
        try:
            response = requests.get(f"{url.rstrip('/')}/health", timeout=5)
            if response.ok:
                payload = response.json()
                if not require_agent or payload.get("agent_initialized") is True:
                    return payload
                last_error = str(payload)
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"health check failed for {url}: {last_error}")


def extract_tool_calls(intermediate_steps: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(intermediate_steps, list):
        return calls
    for step in intermediate_steps:
        if isinstance(step, list) and step:
            action = step[0]
        else:
            action = step
        if not isinstance(action, dict):
            continue
        name = action.get("tool") or action.get("name")
        if not name:
            continue
        calls.append(
            {
                "tool": str(name),
                "arguments": action.get("tool_input", action.get("arguments", action.get("args", {}))),
            }
        )
    return calls


def command_for_task(task: dict[str, Any]) -> str:
    waypoints = task.get("goal", {}).get("waypoints", [])
    instruction = str(task.get("instruction", "")).strip()
    contract = task.get("contract") or {}
    return (
        "Execute the following UAV mission in the current session. "
        "Use the available drone(s), preserve the waypoint order, and use only numeric coordinates. "
        "Inspect the current state before acting, then execute the mission and verify the final state. "
        "If a move returns partial status or the simulator reports that its step budget is exhausted, "
        "do not repeat the same unreachable move; record the partial outcome and finish with [TASK DONE]. "
        "Do not invent targets or coordinates. End the final response with [TASK DONE].\n\n"
        f"Mission instruction:\n{instruction}\n\n"
        f"Canonical numeric waypoints (execute in this order):\n{json.dumps(waypoints, ensure_ascii=False)}\n\n"
        f"Authoritative task contract:\n{json.dumps(contract, ensure_ascii=False)}"
    )


def run_agent_command(api_url: str, command: str, timeout: float) -> dict[str, Any]:
    """Run through Agent4Drone's cancellable async API.

    The synchronous endpoint leaves the server-side agent running after a
    client read timeout.  Polling the async job endpoint lets the runner
    cancel a runaway command and keep one auditable record per task.
    """
    root = api_url.rstrip("/")
    accepted = requests.post(
        f"{root}/agent/command/async",
        json={"command": command},
        timeout=20,
    )
    accepted.raise_for_status()
    job_id = str(accepted.json()["job_id"])
    deadline = time.time() + timeout
    last: dict[str, Any] = {"job_id": job_id, "status": "queued"}
    while time.time() < deadline:
        status_response = requests.get(f"{root}/agent/jobs/{job_id}", timeout=10)
        status_response.raise_for_status()
        last = status_response.json()
        status = str(last.get("status", ""))
        if status == "completed":
            result = last.get("result") or {}
            if not isinstance(result, dict):
                raise RuntimeError(f"Agent job {job_id} returned a non-object result")
            return result
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"Agent job {job_id} ended with status={status}: {last.get('error')}")
        time.sleep(2)

    # Request cancellation before raising, so the callback in Agent4Drone can
    # stop the current provider/tool call.  The result is still represented as
    # runner_error if the service does not acknowledge cancellation promptly.
    try:
        requests.post(f"{root}/agent/jobs/{job_id}/cancel", timeout=10)
    except requests.RequestException:
        pass
    raise TimeoutError(f"Agent job {job_id} exceeded command timeout={timeout}s; cancellation requested")


def run_one(task: dict[str, Any], args: argparse.Namespace, output_handle) -> dict[str, Any]:
    started = time.perf_counter()
    task_id = str(task["task_id"])
    log_dir = Path(args.log_dir).expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    bridge_log = log_dir / f"gym_bridge_{task_id}.log"
    bridge_script = Path(__file__).with_name("gym_uav_bridge.py").resolve()
    bridge_cmd = [
        sys.executable,
        str(bridge_script),
        "--input",
        str(Path(args.input).expanduser().resolve()),
        "--task-id",
        task_id,
        "--host",
        "127.0.0.1",
        "--port",
        str(args.bridge_port),
    ]
    bridge_env = os.environ.copy()
    bridge_env.setdefault("PYTHONUNBUFFERED", "1")
    with bridge_log.open("w", encoding="utf-8") as log_handle:
        bridge = subprocess.Popen(bridge_cmd, stdout=log_handle, stderr=subprocess.STDOUT, env=bridge_env)
    metrics: dict[str, Any] | None = None
    initial_metrics: dict[str, Any] = {}
    try:
        wait_health(f"http://127.0.0.1:{args.bridge_port}", args.startup_timeout)
        subprocess.run(["systemctl", "--user", "restart", args.agent_service], check=True)
        agent_health = wait_health(args.api_url, args.startup_timeout, require_agent=True)
        initial_metrics = requests.get(f"http://127.0.0.1:{args.bridge_port}/bridge/metrics", timeout=10).json()
        result = run_agent_command(args.api_url, command_for_task(task), args.command_timeout)
        metrics = requests.get(f"http://127.0.0.1:{args.bridge_port}/bridge/metrics", timeout=10).json()
        record = {
            "protocol_version": "agent4drone_baseline_v1",
            "method": "B0",
            "task_id": task_id,
            "source_task_id": task.get("metadata", {}).get("source_task_id"),
            "condition": task.get("metadata", {}).get("condition"),
            "run_seed_index": task.get("metadata", {}).get("run_seed_index"),
            "run_seed": task.get("metadata", {}).get("run_seed"),
            "source_dataset": task.get("source_dataset"),
            "split_seed": task.get("metadata", {}).get("split_seed"),
            "episode_seed": task.get("metadata", {}).get("episode_seed"),
            "agent_health": agent_health,
            "initial_bridge_metrics": initial_metrics,
            "api_success": result.get("success"),
            "api_output": result.get("output"),
            "token_usage": result.get("token_usage"),
            "intermediate_steps": result.get("intermediate_steps", []),
            "tool_calls": extract_tool_calls(result.get("intermediate_steps", [])),
            "gym_metrics": metrics,
            "standard_metrics": common_metrics(
                task,
                {"first_failure": metrics.get("first_failure"), "completed_waypoints_by_bundle": initial_metrics.get("completed_waypoints_by_bundle", {})} if metrics else {},
                metrics or {},
                runner_wallclock_seconds=time.perf_counter() - started,
                mission_completion_time_seconds=(
                    metrics.get("sim_time_seconds") if metrics and metrics.get("task_success") else None
                ),
                repair_attempted=False,
            ) if metrics else None,
            "bridge_log": str(bridge_log),
        }
    except Exception as exc:
        record = {
            "protocol_version": "agent4drone_baseline_v1",
            "method": "B0",
            "task_id": task_id,
            "source_task_id": task.get("metadata", {}).get("source_task_id"),
            "condition": task.get("metadata", {}).get("condition"),
            "run_seed_index": task.get("metadata", {}).get("run_seed_index"),
            "run_seed": task.get("metadata", {}).get("run_seed"),
            "source_dataset": task.get("source_dataset"),
            "split_seed": task.get("metadata", {}).get("split_seed"),
            "episode_seed": task.get("metadata", {}).get("episode_seed"),
            "status": "runner_error",
            "error": repr(exc),
            "gym_metrics": metrics,
            "bridge_log": str(bridge_log),
        }
        if metrics is None:
            try:
                metrics = requests.get(f"http://127.0.0.1:{args.bridge_port}/bridge/metrics", timeout=5).json()
                record["gym_metrics"] = metrics
            except requests.RequestException:
                pass
        if metrics is not None and "standard_metrics" not in record:
            record["standard_metrics"] = common_metrics(
                task,
                {"first_failure": metrics.get("first_failure"), "completed_waypoints_by_bundle": initial_metrics.get("completed_waypoints_by_bundle", {})} if isinstance(metrics, dict) else {},
                metrics,
                runner_wallclock_seconds=time.perf_counter() - started,
                mission_completion_time_seconds=(
                    metrics.get("sim_time_seconds") if metrics.get("task_success") else None
                ),
                repair_attempted=False,
            )
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=10)
        except subprocess.TimeoutExpired:
            bridge.kill()
    output_handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    output_handle.flush()
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--api-url", default="http://127.0.0.1:18000")
    parser.add_argument("--bridge-port", type=int, default=8000)
    parser.add_argument("--agent-service", default="agent4drone.service")
    parser.add_argument("--log-dir", type=Path, default=Path("~/uav_exp/data/logs/agent4drone_baseline"))
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--command-timeout", type=float, default=420)
    args = parser.parse_args()
    tasks = read_tasks(args.input.expanduser().resolve(), args.task_id, args.limit)
    if not tasks:
        raise SystemExit("no tasks selected")
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if args.output.expanduser().resolve().exists():
        with args.output.expanduser().resolve().open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    existing_ids.add(str(json.loads(line).get("task_id")))
    mode = "a" if existing_ids else "w"
    with args.output.expanduser().resolve().open(mode, encoding="utf-8") as output_handle:
        for index, task in enumerate(tasks, start=1):
            if str(task["task_id"]) in existing_ids:
                continue
            record = run_one(task, args, output_handle)
            print(f"{index}/{len(tasks)} {task['task_id']} status={record.get('status', 'completed')} api_success={record.get('api_success')}", flush=True)


if __name__ == "__main__":
    main()
