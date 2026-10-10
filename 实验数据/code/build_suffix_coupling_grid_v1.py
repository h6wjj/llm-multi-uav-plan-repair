"""Freeze 3 structures x 3 geometric coupling levels x 10 independent layouts.

All factors and random seeds are chosen before formal execution.  Regime
labels follow the generated geometry, never an observed method result.
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
COUPLINGS = ("none", "weak", "strong")
LAYOUTS_PER_CELL = 10
PILOT_BASE_SEED = 20260923
BASE_SEED = 20270000
TOLERANCE = 0.10
DEPENDENCIES = {
    "chain": {"B2": ["B1"], "B3": ["B2"], "B4": ["B3"]},
    "branch": {"B2": ["B1"], "B3": ["B1"], "B4": ["B2"]},
    "merge": {"B2": ["B1"], "B3": ["B1"], "B4": ["B2", "B3"]},
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def route_cost(start: list[float], points: list[list[float]]) -> float:
    return math.dist(start, points[0]) + sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def build_case(
    template: dict[str, Any], structure: str, coupling: str, layout: int, seed: int,
    seed_base: int = BASE_SEED,
) -> dict[str, Any]:
    rng = random.Random(seed)
    c2 = rng.uniform(1.40, 1.80)
    gap = rng.uniform(0.22, 0.38)
    c3 = c2 + gap
    y3 = rng.uniform(0.82, 1.02)
    leg1 = rng.uniform(0.55, 0.75)
    leg3 = rng.uniform(0.50, 0.70)
    leg4 = rng.uniform(0.50, 0.70)
    if coupling == "none":
        target_delta = -gap  # h2-h3 = 0; c2<c3 is decisive.
    elif coupling == "weak":
        target_delta = rng.uniform(0.12, 0.25)
    else:
        target_delta = rng.uniform(0.72, 0.95)
    x4 = y3 - target_delta / 2.0
    angle = rng.uniform(0.0, 2.0 * math.pi)
    shift_x = rng.uniform(8.0, 18.0)
    shift_y = rng.uniform(8.0, 18.0)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def transform(x: float, y: float) -> list[float]:
        return [round(shift_x + x * cos_a - y * sin_a, 9), round(shift_y + x * sin_a + y * cos_a, 9), 1.0]

    starts = {
        "uav_0": transform(-2.0, 2.0),
        "uav_1": transform(0.0, -1.0),
        "uav_2": transform(0.0, c2),
        "uav_3": transform(c3, 0.0),
    }
    paths = {
        "B1": [starts["uav_0"], transform(-2.0 + leg1, 2.0)],
        "B2": [starts["uav_1"], transform(0.0, -0.5), transform(0.0, 0.0)],
        "B3": [transform(0.0, y3), transform(0.0, y3 + leg3)],
        "B4": [transform(x4, 0.0), transform(x4, leg4)],
    }
    task = copy.deepcopy(template)
    task_id = f"suffix_grid_v1_{structure}_{coupling}_{layout:02d}"
    task["task_id"] = task_id
    task["source_dataset"] = "constructed_suffix_coupling_grid_v1"
    task["source_file"] = "suffix_coupling_grid_v1/tasks.jsonl"
    task["source_index"] = seed - seed_base
    task["split"] = "constructed_factorial"
    task["instruction"] = (
        "After UAV 1 becomes unavailable during B2, restore all unfinished task "
        "bundles under the frozen dependency graph and shared hard constraints."
    )
    for agent in task["agents"]:
        agent["start"] = starts[str(agent["agent_id"])]
        agent["extension_start_provenance"] = "suffix_grid_v1_seeded_geometry"
    mission = task["contract"]["mission"]
    mission["bundle_dependencies"] = copy.deepcopy(DEPENDENCIES[structure])
    mission["waypoint_tolerance"] = TOLERANCE
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
            # The local/CDS methods preserve valid downstream owners; a
            # globally reallocating baseline may change these two bundles.
            bundle["eligible_drone_ids"] = ["uav_2", "uav_3"]
            bundle["eligible_roles"] = ["scout", "relay"]
        cursor += len(paths[bundle_id])
    required = [point for bundle_id in ("B1", "B2", "B3", "B4") for point in paths[bundle_id]]
    mission["required_waypoint_sequence"] = required
    task["goal"]["waypoints"] = required
    task["goal"]["description"] = f"{structure} dependency; {coupling} suffix coupling"
    task["reference_plan"] = [
        {"order": index, "action": "MOVE_TO", "position": point, "source": "suffix_coupling_grid_v1_geometry"}
        for index, point in enumerate(required, start=1)
    ]
    task["environment"]["sim"]["policy_frequency"] = 48
    task["environment"]["sim"]["duration_steps"] = 12000
    task["contract"]["contract_version"] = "mission_contract_suffix_grid_v1"
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
    task["contract"]["failure_policy"]["bundle_dependencies"] = copy.deepcopy(DEPENDENCIES[structure])
    task["contract"]["failure_policy"]["trigger_source"] = "frozen_suffix_grid_v1_failure"
    scenario = task["scenario"]
    scenario["scenario_version"] = "suffix_grid_v1"
    scenario["scenario_id"] = task_id
    scenario["source_task_id"] = task_id
    scenario["evaluation_mode"] = "frozen_factorial_geometry"
    scenario["dynamic_scale_factor_xy"] = 1.0
    scenario["structure_factor"] = structure
    scenario["coupling_factor"] = coupling
    if isinstance(scenario.get("failure_model"), dict):
        scenario["failure_model"]["bundle_dependencies"] = copy.deepcopy(DEPENDENCIES[structure])
        scenario["failure_model"]["trigger_source"] = "frozen_suffix_grid_v1_failure"
    metadata = task["metadata"]
    metadata.update({
        "source_name": "suffix_coupling_grid_v1",
        "source_task_id": task_id,
        "mechanism_stress": "factorial_suffix_coupling_grid_v1",
        "condition": "recoverable_failure",
        "condition_label": "recoverable_failure_uncertain",
        "structure_factor": structure,
        "coupling_factor": coupling,
        "layout_index": layout,
        "layout_seed": seed,
        "run_seed": seed,
        "run_seed_index": seed - seed_base,
        "preregistered": False,
        "protocol_locked_before_formal_run": True,
        "population_claim": False,
        "grid_freeze_status": "geometry_frozen_before_formal_run",
        # The adapter requires this exact projection-profile key to drop
        # unsupported source-task features; motion itself remains PyBullet.
        "execution_profile": "gym_waypoint_projection_v1",
        "dynamics_profile": "VelocityAviary_motor_dynamics_48Hz",
        "dynamic_scale_factor_xy": 1.0,
        "waypoint_tolerance": TOLERANCE,
        "execution_mode": "VelocityAviary_motor_dynamics",
        "geometric_inputs": {
            "c2_distance": round(c2, 9),
            "c3_distance": round(c3, 9),
            "B3_first_offset_y": round(y3, 9),
            "B4_first_offset_x": round(x4, 9),
            "intended_simple_minus_alternative": round(target_delta, 9),
            "rotation_rad": round(angle, 9),
            "translation_xy": [round(shift_x, 9), round(shift_y, 9)],
        },
    })
    return task


def audit_case(task: dict[str, Any]) -> dict[str, Any]:
    agents = {str(agent["agent_id"]): agent["start"] for agent in task["agents"]}
    bundles = {str(bundle["bundle_id"]): bundle["waypoints"] for bundle in task["contract"]["mission"]["task_bundles"].values()}
    endpoint = bundles["B2"][-1]
    c2, c3 = route_cost(agents["uav_2"], [endpoint]), route_cost(agents["uav_3"], [endpoint])
    h2 = route_cost(endpoint, bundles["B3"]) + route_cost(agents["uav_3"], bundles["B4"])
    h3 = route_cost(agents["uav_2"], bundles["B3"]) + route_cost(endpoint, bundles["B4"])
    legs = [math.dist(a, b) for path in bundles.values() for a, b in zip(path, path[1:])]
    return {
        "task_id": task["task_id"],
        "structure": task["metadata"]["structure_factor"],
        "coupling": task["metadata"]["coupling_factor"],
        "layout_seed": task["metadata"]["layout_seed"],
        "immediate_gap_c3_minus_c2": round(c3 - c2, 9),
        "coupling_h2_minus_h3": round(h2 - h3, 9),
        "predicted_simple_minus_alternative": round(c2 + h2 - c3 - h3, 9),
        "minimum_leg_to_tolerance_ratio": round(min(legs) / TOLERANCE, 9),
        "failed_uav_to_B2_endpoint_m": round(math.dist(bundles["B2"][1], endpoint), 9),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--preflight-output", type=Path)
    args = parser.parse_args()
    template = read_jsonl(args.template)[0]
    tasks = []
    for i, structure in enumerate(STRUCTURES):
        for j, coupling in enumerate(COUPLINGS):
            for layout in range(1, LAYOUTS_PER_CELL + 1):
                seed = BASE_SEED + i * 1000 + j * 100 + layout
                tasks.append(build_case(template, structure, coupling, layout, seed))
    cases = [audit_case(task) for task in tasks]
    counts = Counter((case["structure"], case["coupling"]) for case in cases)
    checks = {
        "exactly_90_unique_instances": len(tasks) == 90 and len({task["task_id"] for task in tasks}) == 90,
        "nine_cells_of_ten": all(counts[(structure, coupling)] == 10 for structure in STRUCTURES for coupling in COUPLINGS),
        "unique_independent_layout_seeds": len({case["layout_seed"] for case in cases}) == 90,
        "all_direct_choices_uav2": all(case["immediate_gap_c3_minus_c2"] > 0.2 for case in cases),
        "no_coupling_is_near_zero": all(abs(case["coupling_h2_minus_h3"]) < 1e-6 for case in cases if case["coupling"] == "none"),
        "weak_coupling_gain_range": all(0.10 < case["predicted_simple_minus_alternative"] < 0.27 for case in cases if case["coupling"] == "weak"),
        "strong_coupling_gain_range": all(0.70 < case["predicted_simple_minus_alternative"] < 0.97 for case in cases if case["coupling"] == "strong"),
        "all_legs_above_4x_tolerance": all(case["minimum_leg_to_tolerance_ratio"] > 4.0 for case in cases),
        "failed_drone_clearance_over_collision_diameter": all(case["failed_uav_to_B2_endpoint_m"] > 0.12 for case in cases),
        "all_motor_dynamics": all(not task["contract"]["resources"].get("kinematic_mechanism_audit") for task in tasks),
        "all_three_dependency_graphs_present": len({json.dumps(task["contract"]["mission"]["bundle_dependencies"], sort_keys=True) for task in tasks}) == 3,
    }
    if not all(checks.values()):
        raise SystemExit("grid design audit failed: " + json.dumps(checks, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n" for task in tasks), encoding="utf-8")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    audit = {
        "protocol": "suffix_coupling_grid_v1",
        "status": "frozen_not_executed",
        "base_seed": BASE_SEED,
        "task_count": len(tasks),
        "cell_counts": {f"{structure}/{coupling}": counts[(structure, coupling)] for structure in STRUCTURES for coupling in COUPLINGS},
        "checks": checks,
        "cases": cases,
        "task_sha256": digest,
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    protocol = {
        "protocol": "suffix_coupling_grid_v1",
        "protocol_revision": "preflight_readiness_amendment_2026-09-23",
        "status": "formal_grid_frozen_ready_to_run_not_executed",
        "task_sha256": digest,
        "formal_seed_base": BASE_SEED,
        "disjoint_preflight_seed_base": PILOT_BASE_SEED,
        "disjoint_preflight_ids": [
            "preflight_suffix_grid_v1_chain_none_01",
            "preflight_suffix_grid_v1_branch_strong_01",
            "preflight_suffix_grid_v1_merge_strong_01",
        ],
        "factorial": {"structures": list(STRUCTURES), "coupling_levels": list(COUPLINGS), "independent_layouts_per_cell": LAYOUTS_PER_CELL},
        "assignment_rule": "Coupling labels come from seeded geometry and analytical c/h costs; no execution outcome is inspected.",
        "methods": [
            {"name": "Simple-local-reassignment", "rule": "minimum immediate B2 route cost; preserve valid remaining owners"},
            {"name": "CDS-MR", "rule": "minimum edit count, then minimum full-suffix live-state route cost under hard constraints"},
            {"name": "Global-total-route-reallocation", "rule": "over all compatible assignments of B2/B3/B4, minimize full-suffix live-state route cost subject to the same hard constraints; deterministic UAV-ID tie break", "implementation_status": "implemented_and_passed_v3_preflight"},
        ],
        "primary_metrics": ["estimated_full_suffix_route_cost", "executed_suffix_path_length_m", "recovery_success", "strict_contract_satisfaction", "NPSD", "repair_decision_latency_s"],
        "secondary_metrics": ["mission_completion_time_s", "runner_wallclock_s"],
        "aggregation": "Report per-cell means and dispersion, paired method differences, all failed/timeout cases, and confidence intervals across the 10 independent layouts; do not choose cells after observing outcomes.",
        "run_gate": "PASS in suffix_grid_three_way_preflight_v3. Before formal launch, verify every code hash in its run_manifest.json; run all 90 frozen tasks with all three methods; include runner errors and missing rows as recovery failures; report all failed and timed-out rows.",
        "execution_note": "The three dependency graphs share deterministic topological command order B2,B3,B4 in this generator; the structure factor changes which downstream bundles are causally affected by B2 failure, not parallel scheduling order.",
        "scope": "Constructed factorial instances; separate independently generated task test remains necessary for generalization. This simulation uses waypoint projection and does not model source weather, no-fly zones, or moving obstacles.",
    }
    args.protocol.parent.mkdir(parents=True, exist_ok=True)
    args.protocol.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.preflight_output:
        pilot_specs = (
            ("chain", "none", PILOT_BASE_SEED + 1),
            ("branch", "strong", PILOT_BASE_SEED + 1201),
            ("merge", "strong", PILOT_BASE_SEED + 2201),
        )
        pilots = [
            build_case(template, structure, coupling, 1, seed, seed_base=PILOT_BASE_SEED)
            for structure, coupling, seed in pilot_specs
        ]
        for pilot in pilots:
            pilot_id = "preflight_" + pilot["task_id"]
            pilot["task_id"] = pilot_id
            pilot["source_file"] = "suffix_coupling_grid_v1/preflight_3.jsonl"
            pilot["scenario"]["scenario_id"] = pilot_id
            pilot["scenario"]["source_task_id"] = pilot_id
            pilot["metadata"]["source_task_id"] = pilot_id
        if len(pilots) != 3:
            raise SystemExit("preflight subset generation failed")
        if set(task["metadata"]["layout_seed"] for task in pilots) & set(case["layout_seed"] for case in cases):
            raise SystemExit("preflight/formal seed overlap")
        if set(task["task_id"] for task in pilots) & set(task["task_id"] for task in tasks):
            raise SystemExit("preflight/formal task-id overlap")
        args.preflight_output.parent.mkdir(parents=True, exist_ok=True)
        args.preflight_output.write_text(
            "".join(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n" for task in pilots),
            encoding="utf-8",
        )
    print(json.dumps({"status": audit["status"], "task_count": len(tasks), "checks": checks, "task_sha256": digest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
