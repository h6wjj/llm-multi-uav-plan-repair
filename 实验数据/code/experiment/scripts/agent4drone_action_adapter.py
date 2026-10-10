"""Normalize Agent4Drone tool calls into simulator-neutral commands.

Agent4Drone's tools accept JSON-string arguments.  This module deliberately
does not execute those calls; it validates their arguments and produces a
small command IR that the gym adapter can consume.  Symbolic destinations are
kept as unsupported instead of being guessed as coordinates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _args(call: dict[str, Any]) -> dict[str, Any]:
    value = call.get("arguments", call.get("args", call.get("input", {})))
    value = _json_value(value)
    return value if isinstance(value, dict) else {}


def _tool_name(call: dict[str, Any]) -> str:
    return str(call.get("tool", call.get("name", call.get("action", "")))).strip().lower()


def _position(args: dict[str, Any]) -> list[float] | None:
    value = args.get("position", args.get("destination", args.get("waypoint")))
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z", value.get("altitude"))]
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            result = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        return result if all(item == item and abs(item) != float("inf") for item in result) else None
    x, y = args.get("x"), args.get("y")
    z = args.get("z", args.get("altitude"))
    if x is None or y is None or z is None:
        return None
    try:
        return [float(x), float(y), float(z)]
    except (TypeError, ValueError):
        return None


def normalize_tool_call(call: Any, order: int = 1) -> dict[str, Any]:
    if isinstance(call, str):
        call = _json_value(call)
    if not isinstance(call, dict):
        return {"order": order, "status": "unsupported", "reason": "tool_call_not_object", "raw": call}
    name = _tool_name(call)
    args = _args(call)
    drone_id = args.get("drone_id") or args.get("robot_id")
    if name in {"gen_group_by_robot_names", "gen_group_by_drone_names", "create_group"}:
        return {"order": order, "status": "accepted", "command": "ASSIGN_GROUP", "drone_ids": args.get("robot_names", args.get("drone_ids", [])), "raw": call}
    if name in {"set_group_altitude", "change_altitude", "take_off"}:
        altitude = args.get("altitude", args.get("z"))
        if altitude is None:
            return {"order": order, "status": "unsupported", "reason": "altitude_missing", "tool": name, "raw": call}
        return {"order": order, "status": "accepted", "command": "SET_ALTITUDE", "drone_id": drone_id, "altitude": float(altitude), "raw": call}
    if name in {"move_to", "move_to_coordinate", "navigate_to"}:
        position = _position(args)
        if position is None:
            return {"order": order, "status": "unsupported", "reason": "numeric_position_missing_or_symbolic", "tool": name, "raw": call}
        return {"order": order, "status": "accepted", "command": "MOVE_TO", "drone_id": drone_id, "position": position, "raw": call}
    # Agent4Drone exposes both the plain path tool and the path-plus-sensing
    # variant.  Both execute the same waypoint trajectory in the Gym bridge;
    # the latter only adds a post-move sensing report.  Treating it as an
    # unsupported tool would artificially lower intent-preservation scores.
    if name in {"move_along_path", "move_along_path_and_sense", "follow_path"}:
        waypoints = args.get("waypoints", args.get("path"))
        if not isinstance(waypoints, list) or not waypoints:
            return {"order": order, "status": "unsupported", "reason": "path_missing", "tool": name, "raw": call}
        normalized = []
        for waypoint in waypoints:
            position = _position({"position": waypoint})
            if position is None:
                return {"order": order, "status": "unsupported", "reason": "path_contains_non_numeric_position", "tool": name, "raw": call}
            normalized.append(position)
        return {"order": order, "status": "accepted", "command": "MOVE_PATH", "drone_id": drone_id, "waypoints": normalized, "raw": call}
    if name in {"land", "return_home"}:
        return {"order": order, "status": "accepted", "command": "LAND", "drone_id": drone_id, "raw": call}
    if name in {"hover"}:
        return {"order": order, "status": "accepted", "command": "HOVER", "drone_id": drone_id, "raw": call}
    return {"order": order, "status": "unsupported", "reason": "unsupported_tool", "tool": name, "raw": call}


def normalize_tool_calls(calls: Iterable[Any]) -> list[dict[str, Any]]:
    return [normalize_tool_call(call, index) for index, call in enumerate(calls, start=1)]


def extract_calls(record: Any) -> tuple[str | None, list[Any]]:
    if not isinstance(record, dict):
        return None, [record]
    task_id = record.get("task_id")
    calls = record.get("tool_calls", record.get("actions", record.get("plan", [])))
    if isinstance(calls, list):
        return task_id, calls
    return task_id, [calls]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL containing one task trace per line")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.input.expanduser().resolve().open("r", encoding="utf-8-sig") as source, args.output.expanduser().resolve().open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            task_id, calls = extract_calls(record)
            normalized = normalize_tool_calls(calls)
            target.write(json.dumps({"task_id": task_id, "commands": normalized}, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    print(f"wrote {count} normalized traces to {args.output.expanduser().resolve()}")


if __name__ == "__main__":
    main()
