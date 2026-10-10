"""Validate structural invariants of a ``uav_task_v1`` JSONL file.

This lightweight validator deliberately uses only the Python standard library
so it can run in the experiment VM before installing any optional packages.
An empty instruction is a warning (expected for the current HaL annotation
subset), not a structural error.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ALLOWED_DATASETS = {"swarm_steward_b", "uavbench", "hal13k_annotation"}
MAX_REPORTED_ERRORS = 50


def validate_record(record: Any, line_number: int, seen: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return [f"line {line_number}: record is not an object"]
    required = {"schema_version", "task_id", "source_dataset", "instruction", "agents", "goal", "metadata"}
    for key in sorted(required - set(record)):
        errors.append(f"line {line_number}: missing required field {key}")
    if record.get("schema_version") != "uav_task_v1":
        errors.append(f"line {line_number}: schema_version must be uav_task_v1")
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        errors.append(f"line {line_number}: task_id must be a non-empty string")
    elif task_id in seen:
        errors.append(f"line {line_number}: duplicate task_id {task_id}")
    else:
        seen.add(task_id)
    if record.get("source_dataset") not in ALLOWED_DATASETS:
        errors.append(f"line {line_number}: unsupported source_dataset {record.get('source_dataset')!r}")
    if not isinstance(record.get("instruction"), str):
        errors.append(f"line {line_number}: instruction must be a string")
    if not isinstance(record.get("agents"), list):
        errors.append(f"line {line_number}: agents must be a list")
    else:
        agent_ids: set[str] = set()
        for agent_index, agent in enumerate(record["agents"]):
            if not isinstance(agent, dict):
                errors.append(f"line {line_number}: agent {agent_index} is not an object")
                continue
            agent_id = agent.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id:
                errors.append(f"line {line_number}: agent {agent_index} has invalid agent_id")
            elif agent_id in agent_ids:
                errors.append(f"line {line_number}: duplicate agent_id {agent_id}")
            else:
                agent_ids.add(agent_id)
    if not isinstance(record.get("goal"), dict):
        errors.append(f"line {line_number}: goal must be an object")
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"line {line_number}: metadata must be an object")
    else:
        for key in ("source_dataset", "transform_version"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                errors.append(f"line {line_number}: metadata.{key} must be a non-empty string")
    if "unsupported_features" in record and not isinstance(record["unsupported_features"], list):
        errors.append(f"line {line_number}: unsupported_features must be a list")
    return errors


def validate(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    seen: set[str] = set()
    source_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    unsupported_counts: Counter[str] = Counter()
    total = 0
    invalid_records = 0
    empty_instruction = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid_records += 1
                errors.append(f"line {line_number}: invalid JSON ({exc.msg})")
                continue
            record_errors = validate_record(record, line_number, seen)
            if record_errors:
                invalid_records += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.extend(record_errors[: MAX_REPORTED_ERRORS - len(errors)])
            if isinstance(record, dict):
                source_counts[str(record.get("source_dataset"))] += 1
                split_counts[str(record.get("split"))] += 1
                if record.get("instruction") == "":
                    empty_instruction += 1
                for feature in record.get("unsupported_features", []) if isinstance(record.get("unsupported_features"), list) else []:
                    unsupported_counts[str(feature)] += 1
    return {
        "input": str(path),
        "total_records": total,
        "valid_records": total - invalid_records,
        "invalid_records": invalid_records,
        "error_count": len(errors),
        "errors": errors[:MAX_REPORTED_ERRORS],
        "unique_task_ids": len(seen),
        "empty_instruction_count": empty_instruction,
        "source_counts": dict(source_counts),
        "split_counts": dict(split_counts),
        "unsupported_feature_counts": dict(unsupported_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = validate(args.input.expanduser().resolve())
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote validation report to {output}")
    else:
        print(text, end="")
    print(f"records={report['total_records']} errors={report['error_count']} empty_instruction={report['empty_instruction_count']}")
    raise SystemExit(1 if report["error_count"] else 0)


if __name__ == "__main__":
    main()
