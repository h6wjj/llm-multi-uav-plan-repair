"""Generate B3 independently sampled random-layout tasks without outcome filtering.

The formal sample is fixed by protocol, uses independent geometric primitives,
and is never stratified by a post-hoc coupling or method-win condition.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

STRUCTURES = ("chain", "branch", "merge")
DEPENDENCIES = {
    "chain": {"B2": ["B1"], "B3": ["B2"], "B4": ["B3"]},
    "branch": {"B2": ["B1"], "B3": ["B1"], "B4": ["B2"]},
    "merge": {"B2": ["B1"], "B3": ["B1"], "B4": ["B2", "B3"]},
}
TOLERANCE_M = 0.10
MIN_SEQUENCE_GAP_M = 4.0 * TOLERANCE_M
FORMAL_LAYOUTS_PER_STRUCTURE = 30
FORMAL_SEEDS = {
    "chain": range(20280001, 20280031),
    "branch": range(20281001, 20281031),
    "merge": range(20282001, 20282031),
}
PILOT_SPECS = [
    ("chain", 20279001),
    ("branch", 20279002),
    ("merge", 20279003),
]
MAX_DRAW_ATTEMPTS = 1000


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def route_cost(start: list[float], points: list[list[float]]) -> float:
    return math.dist(start, points[0]) + sum(
        math.dist(a, b) for a, b in zip(points, points[1:])
    )


def signed_magnitude(rng: random.Random, low: float, high: float) -> float:
    sign = -1.0 if rng.random() < 0.5 else 1.0
    return sign * rng.uniform(low, high)


def build_candidate(
    template: dict[str, Any], structure: str, seed: int, layout_index: int,
    source_file: str, formal: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = random.Random(seed)
    # These variables are sampled independently. In particular, c2/c3 and the
    # downstream offsets are not coupled through a target gain or inequality.
    c2 = rng.uniform(0.5, 2.5)
    c3 = rng.uniform(0.5, 2.5)
    b3_x = rng.uniform(-0.35, 0.35)
    b3_y = signed_magnitude(rng, 0.5, 2.0)
    b4_x = signed_magnitude(rng, 0.5, 2.0)
    b4_y = rng.uniform(-0.35, 0.35)
    leg1 = rng.uniform(0.5, 1.5)
    leg3 = rng.uniform(0.5, 1.5)
    leg4 = rng.uniform(0.5, 1.5)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    shift_x = rng.uniform(8.0, 18.0)
    shift_y = rng.uniform(8.0, 18.0)
    # Slightly vary the unaffected B1 start/route without changing its owner.
    b1_x = rng.uniform(-3.0, -1.0)
    b1_y = rng.uniform(0.5, 2.5)
    b1_angle = rng.uniform(0.0, 2.0 * math.pi)
    b2_start_x = rng.uniform(-0.35, 0.35)
    b2_start_y = -rng.uniform(0.8, 1.4)
    b2_half = (b2_start_x / 2.0, b2_start_y / 2.0)
    phi3 = rng.uniform(0.0, 2.0 * math.pi)
    phi4 = rng.uniform(0.0, 2.0 * math.pi)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def transform(x: float, y: float) -> list[float]:
        return [
            round(shift_x + x * cos_a - y * sin_a, 9),
            round(shift_y + x * sin_a + y * cos_a, 9),
            1.0,
        ]

    b1_dx, b1_dy = leg1 * math.cos(b1_angle), leg1 * math.sin(b1_angle)
    start0 = transform(b1_x, b1_y)
    start1 = transform(b2_start_x, b2_start_y)
    start2 = transform(0.0, c2)
    start3 = transform(c3, 0.0)
    endpoint = transform(0.0, 0.0)
    paths = {
        "B1": [start0, transform(b1_x + b1_dx, b1_y + b1_dy)],
        "B2": [
            start1,
            transform(*b2_half),
            endpoint,
        ],
        "B3": [
            transform(b3_x, b3_y),
            transform(b3_x + leg3 * math.cos(phi3), b3_y + leg3 * math.sin(phi3)),
        ],
        "B4": [
            transform(b4_x, b4_y),
            transform(b4_x + leg4 * math.cos(phi4), b4_y + leg4 * math.sin(phi4)),
        ],
    }
    ordered_points = [
        point for bundle_id in ("B1", "B2", "B3", "B4")
        for point in paths[bundle_id]
    ]
    consecutive_gaps = [
        math.dist(first, second)
        for first, second in zip(ordered_points, ordered_points[1:])
    ]
    min_gap = min(consecutive_gaps)
    if not math.isfinite(min_gap) or min_gap <= MIN_SEQUENCE_GAP_M:
        return {}, {
            "accepted": False,
            "seed": seed,
            "reason": "consecutive_waypoint_gap_at_or_below_4x_tolerance",
            "minimum_sequence_gap_m": round(min_gap, 9),
        }

    task = copy.deepcopy(template)
    task_id = f"b3_indtask_v1_{structure}_{layout_index:02d}"
    task["task_id"] = task_id
    task["source_dataset"] = "b3_independent_random_layout_v1"
    task["source_file"] = source_file
    task["source_index"] = layout_index
    task["split"] = "independent_generated_distribution_b3"
    task["instruction"] = (
        "After UAV 1 becomes unavailable during B2, restore all unfinished "
        "task bundles under the frozen dependency graph and shared hard constraints."
    )
    starts = {
        "uav_0": start0,
        "uav_1": start1,
        "uav_2": start2,
        "uav_3": start3,
    }
    for agent in task["agents"]:
        agent["start"] = starts[str(agent["agent_id"])]
        agent["extension_start_provenance"] = "b3_independent_random_layout_seeded"

    mission = task["contract"]["mission"]
    mission["bundle_dependencies"] = copy.deepcopy(DEPENDENCIES[structure])
    mission["waypoint_tolerance"] = TOLERANCE_M
    cursor = 0
    for bundle in mission["task_bundles"].values():
        bundle_id = str(bundle["bundle_id"])
        bundle["waypoints"] = paths[bundle_id]
        bundle["waypoint_indices"] = list(range(cursor, cursor + len(paths[bundle_id])))
        bundle["deadline_s"] = 600.0
        bundle["assignment_is_decision_variable"] = bundle_id != "B1"
        if bundle_id == "B2":
            bundle["eligible_drone_ids"] = ["uav_2", "uav_3"]
            bundle["eligible_roles"] = ["mapping_specialist", "scout", "relay"]
        elif bundle_id in {"B3", "B4"}:
            bundle["eligible_drone_ids"] = ["uav_2", "uav_3"]
            bundle["eligible_roles"] = ["scout", "relay"]
        cursor += len(paths[bundle_id])

    required = list(ordered_points)
    mission["required_waypoint_sequence"] = required
    task["goal"]["waypoints"] = required
    task["goal"]["description"] = f"{structure} dependency; independently sampled geometry"
    task["reference_plan"] = [
        {
            "order": index,
            "action": "MOVE_TO",
            "position": point,
            "source": "b3_independent_random_layout_v1",
        }
        for index, point in enumerate(required, start=1)
    ]

    task["environment"]["sim"]["policy_frequency"] = 48
    task["environment"]["sim"]["duration_steps"] = 12000
    task["contract"]["contract_version"] = "mission_contract_b3_independent_v1"
    task["contract"]["resources"].update({
        "max_total_steps": 12000,
        "base_step_budget": 12000,
        "repair_step_budget": 6000,
        "time_budget_s": 600.0,
        "motion_speed_scale": 0.3,
        "motion_approach_gain": 0.7,
        "freeze_idle_drones": False,
        "kinematic_mechanism_audit": False,
    })
    task["contract"]["resources"].pop("kinematic_step_distance", None)
    failure_policy = task["contract"]["failure_policy"]
    failure_policy.update({
        "trigger_source": "b3_independent_random_layout_v1",
        "bundle_dependencies": copy.deepcopy(DEPENDENCIES[structure]),
        "replacement_candidates": ["uav_2", "uav_3"],
    })
    scenario = task["scenario"]
    scenario.update({
        "scenario_version": "b3_independent_random_layout_v1",
        "scenario_id": task_id,
        "source_task_id": task_id,
        "evaluation_mode": "independent_random_layout_distribution",
        "dynamic_scale_factor_xy": 1.0,
        "structure_factor": structure,
        "population_claim": False,
    })
    scenario.pop("coupling_factor", None)
    if isinstance(scenario.get("failure_model"), dict):
        scenario["failure_model"].update({
            "trigger_source": "b3_independent_random_layout_v1",
            "bundle_dependencies": copy.deepcopy(DEPENDENCIES[structure]),
            "replacement_candidates": ["uav_2", "uav_3"],
        })
        scenario["failure_model"].pop("coupling_factor", None)

    metadata = task["metadata"]
    metadata.pop("coupling_factor", None)
    metadata.update({
        "source_name": "b3_independent_random_layout_v1",
        "source_task_id": task_id,
        "mechanism_stress": None,
        "condition": "recoverable_failure",
        "condition_label": "recoverable_failure_uncertain",
        "structure_factor": structure,
        "layout_index": layout_index,
        "layout_seed": seed,
        "run_seed": seed,
        "run_seed_index": layout_index - 1,
        "preregistered": True,
        "protocol_locked_before_formal_run": True,
        "population_claim": False,
        "grid_freeze_status": "independent_distribution_frozen_before_run",
        "execution_profile": "gym_waypoint_projection_v1",
        "dynamics_profile": "VelocityAviary_motor_dynamics_48Hz",
        "dynamic_scale_factor_xy": 1.0,
        "waypoint_tolerance": TOLERANCE_M,
        "execution_mode": "VelocityAviary_motor_dynamics",
        "generation_rule": "independent_uniform_primitives_no_coupling_target",
        "generation_attempt": None,
        "geometric_inputs": {
            "uav2_to_B2_endpoint_distance": round(c2, 9),
            "uav3_to_B2_endpoint_distance": round(c3, 9),
            "B1_local_start_xy": [round(b1_x, 9), round(b1_y, 9)],
            "B2_local_start_xy": [round(b2_start_x, 9), round(b2_start_y, 9)],
            "B3_cross_track_x": round(b3_x, 9),
            "B3_offset_y": round(b3_y, 9),
            "B4_offset_x": round(b4_x, 9),
            "B4_cross_track_y": round(b4_y, 9),
            "B1_leg_length": round(leg1, 9),
            "B3_leg_length": round(leg3, 9),
            "B4_leg_length": round(leg4, 9),
            "B1_heading_rad": round(b1_angle, 9),
            "B3_heading_rad": round(phi3, 9),
            "B4_heading_rad": round(phi4, 9),
            "global_rotation_rad": round(angle, 9),
            "translation_xy": [round(shift_x, 9), round(shift_y, 9)],
            "minimum_consecutive_waypoint_gap_m": round(min_gap, 9),
        },
    })
    task["scenario"]["run_seed_index"] = layout_index - 1
    task["scenario"]["layout_seed"] = seed

    audit = {
        "task_id": task_id,
        "structure": structure,
        "seed": seed,
        "formal": formal,
        "generation_attempt": None,
        "c2_distance": round(c2, 9),
        "c3_distance": round(c3, 9),
        "h2_minus_h3": None,
        "minimum_consecutive_waypoint_gap_m": round(min_gap, 9),
        "minimum_gap_over_tolerance": round(min_gap / TOLERANCE_M, 9),
        "outcome_conditioned": False,
        "coupling_label_assigned": False,
    }
    return task, audit


def generate_one(
    template: dict[str, Any], structure: str, seed: int, layout_index: int,
    source_file: str, formal: bool,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    rng = random.Random(seed)
    # Candidate RNG for this seed is itself deterministic. Rejected attempts
    # consume only the generator RNG; no simulator or method output is read.
    for attempt in range(1, MAX_DRAW_ATTEMPTS + 1):
        draw_seed = rng.getrandbits(64)
        task, audit = build_candidate(
            template, structure, draw_seed, layout_index, source_file, formal
        )
        audit["seed"] = seed
        audit["draw_seed"] = draw_seed
        audit["generation_attempt"] = attempt
        if task:
            task["metadata"]["run_seed"] = seed
            task["metadata"]["layout_seed"] = seed
            task["metadata"]["generation_attempt"] = attempt
            task["scenario"]["layout_seed"] = seed
            audit["generation_attempt"] = attempt
            return task, audit, attempt
    raise RuntimeError(f"valid_layout_not_found_within_attempt_limit:{structure}:{seed}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("pilot", "formal"), required=True)
    args = parser.parse_args()

    out = args.output_dir
    target_files = {
        "tasks": out / ("pilot_3.jsonl" if args.mode == "pilot" else "tasks.jsonl"),
        "audit": out / ("pilot_generation_audit.json" if args.mode == "pilot" else "generation_audit.json"),
    }
    existing = [str(path) for path in target_files.values() if path.exists()]
    if existing:
        raise SystemExit("refuse_to_overwrite_existing_frozen_outputs:" + ",".join(existing))

    template_rows = read_jsonl(args.template)
    if not template_rows:
        raise SystemExit("template_has_no_rows")
    template = template_rows[0]

    if args.mode == "pilot":
        specs = [
            (structure, seed, index)
            for index, (structure, seed) in enumerate(PILOT_SPECS, start=1)
        ]
    else:
        specs = []
        for structure, seeds in FORMAL_SEEDS.items():
            for index, seed in enumerate(seeds, start=1):
                specs.append((structure, seed, index))

    tasks: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    attempts: dict[str, int] = {}
    for structure, seed, layout_index in specs:
        task, audit, attempt = generate_one(
            template,
            structure,
            seed,
            layout_index,
            "independent_task_distribution_b3_v1/tasks.jsonl" if args.mode == "formal"
            else "independent_task_distribution_b3_v1/pilot_3.jsonl",
            args.mode == "formal",
        )
        tasks.append(task)
        audits.append(audit)
        attempts[str(seed)] = attempt

    task_ids = [str(task["task_id"]) for task in tasks]
    seeds = [int(task["metadata"]["run_seed"]) for task in tasks]
    counts = Counter(str(task["metadata"]["structure_factor"]) for task in tasks)
    min_gap = min(float(task["metadata"]["geometric_inputs"]["minimum_consecutive_waypoint_gap_m"]) for task in tasks)
    checks = {
        "task_count_expected": len(tasks) == (90 if args.mode == "formal" else 3),
        "unique_task_ids": len(task_ids) == len(set(task_ids)),
        "unique_seeds": len(seeds) == len(set(seeds)),
        "formal_balanced_30_per_structure": args.mode != "formal" or all(counts[s] == FORMAL_LAYOUTS_PER_STRUCTURE for s in STRUCTURES),
        "all_recoverable_failure_uncertain": all(task["metadata"]["condition_label"] == "recoverable_failure_uncertain" for task in tasks),
        "all_have_same_candidate_set": all(
            next(bundle for bundle in task["contract"]["mission"]["task_bundles"].values() if bundle["bundle_id"] == "B2")["eligible_drone_ids"] == ["uav_2", "uav_3"]
            for task in tasks
        ),
        "no_coupling_bins": all("coupling_factor" not in task["metadata"] and "coupling_factor" not in task["scenario"] for task in tasks),
        "all_min_sequence_gaps_over_4x_tolerance": min_gap > MIN_SEQUENCE_GAP_M,
        "no_method_outcome_used": all(not audit["outcome_conditioned"] and audit["h2_minus_h3"] is None for audit in audits),
        "all_tasks_motor_dynamics": all(not task["contract"]["resources"].get("kinematic_mechanism_audit") for task in tasks),
    }
    if not all(checks.values()):
        raise SystemExit("b3_generation_audit_failed:" + json.dumps(checks, ensure_ascii=False))

    write_jsonl(target_files["tasks"], tasks)
    target_files["audit"].write_text(
        json.dumps({
            "protocol": "b3_independent_task_distribution_v1",
            "status": "frozen_not_executed",
            "mode": args.mode,
            "task_count": len(tasks),
            "structure_counts": dict(counts),
            "seed_range": [min(seeds), max(seeds)],
            "seed_list": seeds,
            "minimum_sequence_gap_m": round(min_gap, 9),
            "minimum_gap_over_tolerance": round(min_gap / TOLERANCE_M, 9),
            "generation_attempts": attempts,
            "checks": checks,
            "cases": audits,
            "tasks_sha256": sha256_file(target_files["tasks"]),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
