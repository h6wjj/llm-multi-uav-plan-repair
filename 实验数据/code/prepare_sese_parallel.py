"""Prepare non-overlapping generation shards and merge their JSONL outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in values), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("prepare", "merge", "merge-executions"), required=True)
    parser.add_argument("--shards", type=int, default=3)
    args = parser.parse_args()
    root = args.root
    if args.mode == "prepare":
        tasks = rows(root / "tasks.jsonl")
        prefix = rows(root / "candidate_pools.jsonl")
        done = {str(item.get("task_id")) for item in prefix if item.get("runner_status") == "completed"}
        remaining = [item for item in tasks if str(item.get("task_id")) not in done]
        write(root / "tasks_remaining.jsonl", remaining)
        value = {"mode": "prepare", "completed_prefix": len(done), "remaining": len(remaining), "shards": args.shards}
    elif args.mode == "merge":
        tasks = rows(root / "tasks.jsonl")
        expected = [str(item.get("task_id")) for item in tasks]
        values = rows(root / "candidate_pools.jsonl")
        for index in range(args.shards):
            values.extend(rows(root / f"candidate_pools_shard{index}.jsonl"))
        by_id: dict[str, dict] = {}
        duplicates = []
        for item in values:
            task_id = str(item.get("task_id"))
            if task_id in by_id:
                duplicates.append(task_id)
            by_id[task_id] = item
        missing = [task_id for task_id in expected if task_id not in by_id]
        extra = [task_id for task_id in by_id if task_id not in set(expected)]
        ordered = [by_id[task_id] for task_id in expected if task_id in by_id]
        if duplicates or missing or extra:
            raise SystemExit(json.dumps({"duplicates": duplicates, "missing": missing, "extra": extra}))
        write(root / "candidate_pools_merged.jsonl", ordered)
        (root / "candidate_pools.jsonl").replace(root / "candidate_pools_prefix_before_merge.jsonl")
        (root / "candidate_pools_merged.jsonl").replace(root / "candidate_pools.jsonl")
        value = {
            "mode": "merge",
            "rows": len(ordered),
            "unique_task_ids": len(by_id),
            "duplicates": duplicates,
            "missing": missing,
            "extra": extra,
            "sha256": hashlib.sha256((root / "candidate_pools.jsonl").read_bytes()).hexdigest(),
        }
    else:
        tasks = rows(root / "tasks.jsonl")
        expected = [(str(item.get("task_id")), candidate) for item in tasks for candidate in range(5)]
        values = []
        for index in range(args.shards):
            values.extend(rows(root / f"candidate_executions_shard{index}.jsonl"))
        by_key: dict[tuple[str, int], dict] = {}
        duplicates = []
        for item in values:
            key = (str(item.get("task_id")), int(item.get("candidate_index", -1)))
            if key in by_key:
                duplicates.append(key)
            by_key[key] = item
        missing = [key for key in expected if key not in by_key]
        extra = [key for key in by_key if key not in set(expected)]
        ordered = [by_key[key] for key in expected if key in by_key]
        if duplicates or missing or extra:
            raise SystemExit(json.dumps({"duplicates": duplicates, "missing": missing, "extra": extra}))
        write(root / "candidate_executions.jsonl", ordered)
        value = {
            "mode": "merge-executions",
            "rows": len(ordered),
            "unique_keys": len(by_key),
            "duplicates": duplicates,
            "missing": missing,
            "extra": extra,
            "runner_errors": sum(item.get("status") == "runner_error" for item in ordered),
            "sha256": hashlib.sha256((root / "candidate_executions.jsonl").read_bytes()).hexdigest(),
        }
    (root / f"parallel_{args.mode}_validation.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
