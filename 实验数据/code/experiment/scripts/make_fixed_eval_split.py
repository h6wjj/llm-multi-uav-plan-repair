"""Create a deterministic evaluation subset from executable task JSONL.

The split is selected by a SHA-256 ranking rather than by filesystem or input
line order.  This makes the selected task IDs reproducible across Python
versions, operating systems, and repeated conversions.  The seed is a
*split-seed* only; simulator episode seeds are derived separately from the
selected task ID by the same documented rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "fixed_eval_v1"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number}: record is not an object")
            task_id = str(value.get("task_id", "")).strip()
            if not task_id:
                raise ValueError(f"line {line_number}: missing task_id")
            if task_id in seen:
                raise ValueError(f"line {line_number}: duplicate task_id={task_id}")
            seen.add(task_id)
            records.append(value)
    return records


def _rank(seed: int, task_id: str) -> str:
    # Explicit UTF-8 encoding and delimiter make the ranking language/runtime
    # independent.  Do not replace this with Python's process-randomized hash.
    return hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()


def episode_seed(split_seed: int, task_id: str) -> int:
    """Derive a stable Gym reset seed for one task."""
    digest = hashlib.sha256(f"episode:{split_seed}:{task_id}".encode("utf-8")).digest()
    # Gymnasium accepts ordinary non-negative integer seeds; keep this within
    # the common 32-bit range used by NumPy and PyBullet wrappers.
    return int.from_bytes(digest[:4], "big") % 2_147_483_647


def make_split(input_path: Path, output_path: Path, manifest_path: Path, seed: int, count: int) -> dict[str, Any]:
    records = _read_jsonl(input_path)
    if count <= 0:
        raise ValueError("--count must be positive")
    if count > len(records):
        raise ValueError(f"--count={count} exceeds candidate count={len(records)}")

    ranked = sorted(records, key=lambda record: (_rank(seed, str(record["task_id"])), str(record["task_id"])))
    selected = ranked[:count]
    # Keep output line order stable for easy diffing and deterministic API logs.
    selected.sort(key=lambda record: str(record["task_id"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_records: list[dict[str, Any]] = []
    selected_ids = {str(record["task_id"]) for record in selected}
    for record in selected:
        derived = dict(record)
        metadata = dict(derived.get("metadata", {}))
        task_id = str(derived["task_id"])
        derived["split"] = "test"
        metadata.update(
            {
                "eval_protocol": PROTOCOL_VERSION,
                "split_seed": seed,
                "episode_seed": episode_seed(seed, task_id),
                "split_selection": "sha256_seed_task_id_rank",
                "eval_split": "fixed_test",
            }
        )
        derived["metadata"] = metadata
        output_records.append(derived)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in output_records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    source_counts = Counter(str(record.get("source_dataset")) for record in selected)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "input": str(input_path),
        "output": str(output_path),
        "split": "test",
        "split_seed": seed,
        "selection_method": "sha256(f'{split_seed}:{task_id}') ascending",
        "episode_seed_method": "uint32_be(sha256(f'episode:{split_seed}:{task_id}')[:4]) mod 2147483647",
        "candidate_records": len(records),
        "selected_records": len(output_records),
        "source_counts": dict(source_counts),
        "selected_task_ids": [str(record["task_id"]) for record in output_records],
        "selected_task_id_sha256": hashlib.sha256(
            "\n".join(str(record["task_id"]) for record in output_records).encode("utf-8")
        ).hexdigest(),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    manifest = make_split(
        args.input.expanduser().resolve(),
        args.output.expanduser().resolve(),
        args.manifest.expanduser().resolve(),
        args.seed,
        args.count,
    )
    print(
        f"candidates={manifest['candidate_records']} selected={manifest['selected_records']} "
        f"split_seed={manifest['split_seed']}"
    )
    print(f"wrote {manifest['output']}")
    print(f"wrote {args.manifest.expanduser().resolve()}")


if __name__ == "__main__":
    main()
