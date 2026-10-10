"""Regression: runner errors remain in the all-instance recovery denominator."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from run_simple_local_vs_cds_mr import METHODS, finalize


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    tasks = read_jsonl(args.tasks)
    assert len(tasks) == 3
    with tempfile.TemporaryDirectory(prefix="suffix_grid_aggregate_") as name:
        directory = Path(name)
        for method in METHODS:
            filename = method.replace("-", "_") + ".jsonl"
            rows = read_jsonl(args.results / filename)
            assert len(rows) == 3
            rows[-1] = {
                "task_id": rows[-1]["task_id"],
                "runner_status": "runner_error",
                "runner_error": "injected_aggregation_regression_only",
            }
            (directory / filename).write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
            )
        settings = argparse.Namespace(methods=METHODS, protocol_version="aggregation_regression", include_global=False)
        finalize(directory, tasks, settings)
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        assert summary["paired_state_audit"]["status"] == "WARN"
        for method in METHODS:
            item = summary["method_rows"][method]
            assert item["recoverable_success_count"] == 2, item
            assert abs(item["recoverable_success_rate"] - 2 / 3) < 1e-6, item
            assert item["runner_error_count"] == 1, item
            assert item["npsd_n"] == 2, item
    print(json.dumps({"verdict": "PASS", "recovery": "2/3 including runner_error", "pair_status": "WARN"}))


if __name__ == "__main__":
    main()
