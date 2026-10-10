"""Prepare the frozen statistics used by the paper's Fig.3 comparison.

The script is deliberately read-only with respect to ``frozen_data``.  It
reads JSON/JSONL records, filters invalid metric values, and creates one new
report file.  No experiment or plotting code is imported.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "frozen_data" / "main_comparison"
OUTPUT = ROOT / "reports" / "fig3_processed_metrics.json"

METHODS = {
    "Agent4Drone_no_recovery": {
        "display_name": "Agent4Drone (no recovery)",
        "experiment_label": "B0",
    },
    "Intent_preserving_repair": {
        "display_name": "Intent-preserving repair",
        "experiment_label": "B0+SeSE+CDS-MR",
    },
    "LLM_replanning": {
        "display_name": "LLM replanning",
        "experiment_label": "LLM-APE",
    },
    "Global_reallocation": {
        "display_name": "Global reallocation",
        "experiment_label": "H-MRTA",
    },
}

METRICS = {
    "CSR": "contract_satisfaction_rate",
    "NPSD": "normalized_plan_stability_distance",
    "repair_latency": "repair_decision_latency_seconds",
}


def records_from_file(path: Path) -> Iterable[dict[str, Any]]:
    """Yield record dictionaries from a JSONL or JSON file."""

    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
                if isinstance(value, dict):
                    yield value
        return

    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def metric_values(records: list[dict[str, Any]], field: str) -> tuple[list[float], int, int]:
    """Return finite numeric values, plus null and other-invalid counts."""

    values: list[float] = []
    null_count = 0
    invalid_count = 0
    for record in records:
        standard_metrics = record.get("standard_metrics")
        value = standard_metrics.get(field) if isinstance(standard_metrics, dict) else None
        if value is None:
            null_count += 1
            continue
        # bool is an int subclass, so it must be rejected explicitly.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            invalid_count += 1
            continue
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            invalid_count += 1
            continue
        values.append(numeric_value)
    return values, null_count, invalid_count


def summary(values: list[float]) -> dict[str, float | int | None]:
    """Compute mean and sample standard deviation for valid values."""

    if not values:
        return {"mean": None, "std": None, "n": 0}
    sample_std = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "mean": statistics.fmean(values),
        "std": sample_std,
        "n": len(values),
    }


def main() -> None:
    if not DATA_ROOT.is_dir():
        raise FileNotFoundError(f"Missing frozen data directory: {DATA_ROOT}")
    if OUTPUT.exists():
        raise FileExistsError(
            f"Refusing to modify an existing report: {OUTPUT}"
        )

    processed: dict[str, Any] = {
        "metadata": {
            "source": "frozen_data/main_comparison",
            "purpose": "Fig.3 quantitative comparison",
        },
        "methods": {},
    }

    for method_name, method_info in METHODS.items():
        method_dir = DATA_ROOT / method_name
        if not method_dir.is_dir():
            raise FileNotFoundError(f"Missing method directory: {method_dir}")

        data_files = sorted(
            path
            for path in method_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
        )
        records: list[dict[str, Any]] = []
        for data_file in data_files:
            records.extend(records_from_file(data_file))

        result: dict[str, Any] = {
            "display_name": method_info["display_name"],
            "experiment_label": method_info["experiment_label"],
        }
        print(f"{method_name}: files={len(data_files)}, records={len(records)}")

        for short_name, field in METRICS.items():
            values, null_count, invalid_count = metric_values(records, field)
            result[short_name] = summary(values)
            stats = result[short_name]
            print(
                f"  {short_name}: mean={stats['mean']}, std={stats['std']}, "
                f"n={stats['n']} (null_filtered={null_count}, "
                f"non_numeric_filtered={invalid_count})"
            )

        processed["methods"][method_name] = result

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(processed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("Frozen data modified: NO (read-only input)")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
