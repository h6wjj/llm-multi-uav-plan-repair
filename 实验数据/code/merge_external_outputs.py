"""Deterministically merge the interrupted LLM-APE run with its v2 rerun."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--rerun", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    merged: dict[str, dict] = {}
    for row in read(args.old):
        if row.get("runner_status") == "completed":
            merged[str(row.get("task_id"))] = row
    for row in read(args.rerun):
        merged[str(row.get("task_id"))] = row
    if len(merged) != 72 or any(row.get("runner_status") != "completed" for row in merged.values()):
        raise RuntimeError("final external output is not 72 unique completed records")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(merged[key], ensure_ascii=False, separators=(",", ":")) + "\n" for key in sorted(merged)),
        encoding="utf-8",
    )
    print(json.dumps({"rows": len(merged), "runner_errors": 0, "output": str(args.output)}))


if __name__ == "__main__":
    main()
