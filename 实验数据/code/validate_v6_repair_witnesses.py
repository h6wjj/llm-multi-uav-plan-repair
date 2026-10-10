"""Validate local and global v6 repair witnesses in independent bridge sessions."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from run_external_baseline_pilot import (
    _real_bundles,
    _real_condition,
    _real_get,
    _real_move,
    _real_post,
    _real_remaining,
    _real_root,
    _real_start_bridge,
    _real_terminate,
    _real_wait_health,
    read_tasks,
)


def _run_witness(
    task: dict[str, Any],
    witness_name: str,
    assignment: dict[str, str],
    args: argparse.Namespace,
    port: int,
) -> dict[str, Any]:
    root = _real_root(port)
    safe = str(task["task_id"]).replace("/", "_").replace(":", "_")
    log_path = args.log_dir / f"{safe}_{witness_name}.log"
    bridge = None
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        try:
            bridge_args = SimpleNamespace(
                input=args.input, bridge_port=port, bridge_max_steps=None
            )
            bridge = _real_start_bridge(task, bridge_args, port, log)
            _real_wait_health(root, args.startup_timeout)
            initial: list[dict[str, Any]] = []
            for bundle in _real_bundles(task):
                response = _real_move(
                    root,
                    str(bundle["drone_id"]),
                    list(bundle.get("waypoints", [])),
                    str(bundle.get("bundle_id") or bundle.get("drone_id")),
                )
                initial.append(
                    {
                        "bundle_id": bundle.get("bundle_id"),
                        "drone_id": bundle.get("drone_id"),
                        "response": response,
                    }
                )
                if not response.get("success"):
                    break
            initial_metrics = _real_get(root, "/bridge/metrics")
            failure = initial_metrics.get("first_failure")
            if not isinstance(failure, dict):
                raise RuntimeError("recoverable-failure witness did not trigger")
            window = _real_post(root, "/bridge/repair-window")
            repair: list[dict[str, Any]] = []
            for bundle in _real_bundles(task):
                metrics = _real_get(root, "/bridge/metrics")
                remaining = _real_remaining(metrics, bundle)
                if not remaining:
                    continue
                bundle_id = str(bundle.get("bundle_id") or bundle.get("drone_id"))
                selected = str(assignment[bundle_id])
                response = _real_move(root, selected, remaining, bundle_id)
                repair.append(
                    {
                        "bundle_id": bundle_id,
                        "assigned_drone": selected,
                        "remaining_waypoints": remaining,
                        "response": response,
                    }
                )
                if not response.get("success"):
                    break
            final = _real_get(root, "/bridge/metrics")
            return {
                "protocol_version": "v6_repair_witness_validation_v1",
                "task_id": task["task_id"],
                "source_task_id": task.get("metadata", {}).get("source_task_id"),
                "witness": witness_name,
                "assignment": assignment,
                "failure_event": failure,
                "repair_window": window,
                "initial_results": initial,
                "repair_results": repair,
                "task_success": bool(final.get("task_success")),
                "completed_waypoints_by_bundle": final.get("completed_waypoints_by_bundle"),
                "bundle_assignment": final.get("bundle_assignment"),
                "total_steps": final.get("total_steps"),
                "failure_reasons": [item.get("reason") for item in final.get("failure_events", [])],
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "log": str(log_path),
            }
        except Exception as exc:
            return {
                "protocol_version": "v6_repair_witness_validation_v1",
                "task_id": task.get("task_id"),
                "source_task_id": task.get("metadata", {}).get("source_task_id"),
                "witness": witness_name,
                "status": "runner_error",
                "error": repr(exc),
                "task_success": False,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "log": str(log_path),
            }
        finally:
            _real_terminate(bridge)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--bridge-port-base", type=int, default=9010)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument(
        "--log-dir", type=Path, default=Path("~/uav_exp/data/logs/v6_witness_validation")
    )
    args = parser.parse_args()
    args.input = args.input.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.log_dir = args.log_dir.expanduser().resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        task
        for task in read_tasks(args.input, limit=None)
        if _real_condition(task) == "recoverable_failure"
    ][: args.limit]
    records: list[dict[str, Any]] = []
    for task_index, task in enumerate(tasks):
        witnesses = task.get("scenario", {}).get("failure_model", {}).get(
            "feasibility_witnesses", {}
        )
        for witness_index, key in enumerate(("local_minimal_edit", "global_reallocation")):
            payload = witnesses.get(key, {})
            assignment = payload.get("assignment") if isinstance(payload, dict) else None
            if not isinstance(assignment, dict):
                raise SystemExit(f"missing {key} assignment for {task.get('task_id')}")
            record = _run_witness(
                task,
                key,
                {str(k): str(v) for k, v in assignment.items()},
                args,
                args.bridge_port_base + task_index * 2 + witness_index,
            )
            records.append(record)
            print(
                json.dumps(
                    {
                        "source_task_id": record.get("source_task_id"),
                        "witness": key,
                        "task_success": record.get("task_success"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    if not records or not all(record.get("task_success") for record in records):
        raise SystemExit("one or more preregistered repair witnesses failed")


if __name__ == "__main__":
    main()
