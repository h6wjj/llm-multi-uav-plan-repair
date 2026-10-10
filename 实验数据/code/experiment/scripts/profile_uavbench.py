"""Profile extracted UAVBench scenario JSON files.

The profiler ignores macOS metadata (``__MACOSX`` and ``._*``) and never edits
the extracted files.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from profile_common import file_info, is_ignored, json_load, json_type, sample_value, write_report


def profile(root: Path) -> dict[str, Any]:
    paths = [path for path in root.rglob("*.json") if not is_ignored(path)]
    top_level_types: Counter[str] = Counter()
    top_level_keys: Counter[str] = Counter()
    record_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, path in enumerate(sorted(paths), start=1):
        try:
            value = json_load(path)
            value_type = json_type(value)
            top_level_types[value_type] += 1
            if isinstance(value, dict):
                top_level_keys.update(str(key) for key in value.keys())
                record_counts["objects"] += 1
            elif isinstance(value, list):
                record_counts["array_items"] += len(value)
                for item in value:
                    if isinstance(item, dict):
                        top_level_keys.update(str(key) for key in item.keys())
            if len(samples) < 5:
                samples.append(
                    {
                        **file_info(path, root),
                        "json_type": value_type,
                        "sample": sample_value(value),
                    }
                )
        except Exception as exc:
            if len(errors) < 20:
                errors.append(
                    {
                        "path": str(path.relative_to(root)),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        if index % 10000 == 0:
            print(f"profiled {index}/{len(paths)} JSON files", file=sys.stderr)

    return {
        "dataset": "uavbench",
        "root": str(root),
        "json_files_seen": len(paths),
        "top_level_type_counts": dict(top_level_types),
        "top_level_key_counts": dict(top_level_keys.most_common()),
        "record_counts": dict(record_counts),
        "samples": samples,
        "parse_error_count": len(errors),
        "parse_errors_first_20": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("~/uav_exp/data/uavbench/extracted").expanduser(),
        help="Extracted UAVBench directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("~/uav_exp/data/reports/uavbench_profile.json").expanduser(),
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    report = profile(root)
    write_report(report, args.output.expanduser().resolve())
    print(f"wrote {args.output.expanduser().resolve()}")
    print(
        f"json_files={report['json_files_seen']} "
        f"parse_errors={report['parse_error_count']}"
    )


if __name__ == "__main__":
    main()
