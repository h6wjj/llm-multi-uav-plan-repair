"""Probe one physical B1 movement before launching a paired mechanism run."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from gym_uav_bridge import GymBridge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speeds", nargs="+", type=float, default=[0.1, 0.2, 0.3])
    parser.add_argument("--frequency", type=int, default=15)
    parser.add_argument("--freeze-idle", action="store_true")
    parser.add_argument("--step-budget", type=int, default=900)
    args = parser.parse_args()
    task = json.loads(args.tasks.read_text(encoding="utf-8").splitlines()[0])
    bundle = next(bundle for bundle in task["contract"]["mission"]["task_bundles"].values() if bundle["bundle_id"] == "B1")
    target = bundle["waypoints"][1]
    rows = []
    for speed in args.speeds:
        test_task = copy.deepcopy(task)
        test_task["environment"]["sim"]["duration_steps"] = args.step_budget
        test_task["environment"]["sim"]["policy_frequency"] = args.frequency
        resources = test_task["contract"]["resources"]
        resources["max_total_steps"] = args.step_budget
        resources["motion_speed_scale"] = speed
        resources["freeze_idle_drones"] = args.freeze_idle
        bridge = GymBridge(test_task)
        try:
            result = bridge._move_to("uav_0", target)
            rows.append({"speed_scale": speed, "frequency": args.frequency, "freeze_idle": args.freeze_idle, "result": result})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        finally:
            bridge.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
