"""Helpers for converting source records into ``uav_task_v1`` JSONL."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def coerce(value: Any) -> Any:
    """Convert CSV scalar strings while leaving complex values intact."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    if value == "":
        return None
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none"}:
        return None
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", value):
            return float(value)
    except ValueError:
        pass
    if value[:1] in "[{" and value[-1:] in "]}":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def coerce_record(record: dict[str, Any]) -> dict[str, Any]:
    return {str(key): coerce(value) for key, value in record.items()}


def parse_xyz(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        try:
            values = [float(item) for item in value.replace(",", " ").split()]
            return values if len(values) == 3 else None
        except ValueError:
            return None
    return None


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def slug(value: str, fallback: str = "task") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value or fallback


def write_jsonl(records: Iterable[dict[str, Any]], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)
