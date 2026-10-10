"""A small, auditable SeSE-style gate for structured UAV plans.

The official SeSE method estimates black-box LLM uncertainty from the
structural entropy of a semantic graph built from multiple sampled answers.
Our planner has structured tool traces rather than free-form answers, so this
module applies the same principle to normalized action/waypoint signatures:
shared structure is one graph mass and candidate-specific structure is two
additional masses.  High entropy means the candidate plans disagree and the
gate abstains; a candidate is accepted only when it is no worse against the
canonical waypoints supplied to the planner.

This is an inference-time, cached-replay adaptation for the pilot.  It is not
claimed to reproduce the paper's entailment model or its multi-sample graph
construction.  The gate never reads simulator success, reward, or final
metrics when selecting a plan.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from agent4drone_action_adapter import normalize_tool_call


def _xyz(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        xyz = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return xyz if all(math.isfinite(item) for item in xyz) else None


def _quantized(xyz: tuple[float, float, float], quantum: float = 0.05) -> tuple[int, int, int]:
    return tuple(int(round(item / quantum)) for item in xyz)


def movement_signatures(tool_calls: Iterable[Any]) -> list[tuple[Any, ...]]:
    """Return ordered, simulator-neutral movement signatures."""

    signatures: list[tuple[Any, ...]] = []
    for order, call in enumerate(tool_calls, start=1):
        normalized = normalize_tool_call(call, order)
        if normalized.get("status") != "accepted":
            continue
        command = normalized.get("command")
        if command == "MOVE_TO":
            xyz = _xyz(normalized.get("position"))
            if xyz is not None:
                signatures.append(("MOVE_TO", _quantized(xyz)))
        elif command == "MOVE_PATH":
            for waypoint in normalized.get("waypoints", []):
                xyz = _xyz(waypoint)
                if xyz is not None:
                    signatures.append(("MOVE_PATH", _quantized(xyz)))
        elif command in {"SET_ALTITUDE", "LAND", "HOVER"}:
            signatures.append((str(command),))
    return signatures


def _lcs_length(left: list[Any], right: list[Any]) -> int:
    row = [0] * (len(right) + 1)
    for item in left:
        previous = 0
        for index, other in enumerate(right, start=1):
            saved = row[index]
            if item == other:
                row[index] = previous + 1
            else:
                row[index] = max(row[index], row[index - 1])
            previous = saved
    return row[-1]


def structural_similarity(left: list[Any], right: list[Any]) -> float:
    """Ordered/set agreement in [0, 1] for two normalized plans."""

    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    lcs = _lcs_length(left, right) / max(len(left), len(right))
    left_set, right_set = set(left), set(right)
    union = left_set | right_set
    jaccard = len(left_set & right_set) / len(union) if union else 1.0
    return round(0.5 * lcs + 0.5 * jaccard, 6)


def _entropy(masses: list[float]) -> float:
    total = sum(masses)
    if total <= 0 or len(masses) < 2:
        return 0.0
    probabilities = [mass / total for mass in masses if mass > 0]
    value = -sum(probability * math.log(probability, 2) for probability in probabilities)
    return value / math.log(len(masses), 2)


def structural_entropy(baseline: list[Any], alternate: list[Any]) -> dict[str, Any]:
    """Compute normalized entropy of shared and candidate-specific structure."""

    baseline_set, alternate_set = set(baseline), set(alternate)
    shared = baseline_set & alternate_set
    baseline_only = baseline_set - alternate_set
    alternate_only = alternate_set - baseline_set
    masses = [float(len(shared)), float(len(baseline_only)), float(len(alternate_only))]
    # When both candidates are empty, retain a deterministic zero-uncertainty
    # value; the subsequent coverage check still prevents accepting a no-op.
    entropy = _entropy(masses) if sum(masses) else 0.0
    return {
        "normalized_entropy": round(entropy, 6),
        "shared_structure_count": len(shared),
        "baseline_only_count": len(baseline_only),
        "alternate_only_count": len(alternate_only),
        "baseline_signature_count": len(baseline),
        "alternate_signature_count": len(alternate),
    }


def _coverage(expected: list[list[float]], observed: list[tuple[Any, ...]], tolerance: float = 0.30) -> tuple[float, int]:
    """Ordered waypoint coverage without using simulator outcomes."""

    if not expected:
        return 0.0, 0
    observed_xyz = [item[1] for item in observed if len(item) == 2 and isinstance(item[1], tuple)]
    # Signatures are quantized at 0.05 m; compare the reconstructed point to
    # each goal with a small margin for quantization.
    cursor = 0
    matched = 0
    for target in expected:
        if not isinstance(target, (list, tuple)) or len(target) != 3:
            continue
        try:
            tx, ty, tz = (float(value) for value in target)
        except (TypeError, ValueError):
            continue
        found = False
        while cursor < len(observed_xyz):
            ox, oy, oz = (value * 0.05 for value in observed_xyz[cursor])
            cursor += 1
            if math.dist((tx, ty, tz), (ox, oy, oz)) <= tolerance + 0.04:
                matched += 1
                found = True
                break
        if not found:
            break
    return matched / len(expected), matched


def gate_pair(
    expected: list[list[float]],
    baseline_calls: Iterable[Any],
    alternate_calls: Iterable[Any],
    entropy_threshold: float = 0.80,
) -> dict[str, Any]:
    """Select B0 or an alternate repair trace using a frozen gate rule."""

    baseline = movement_signatures(baseline_calls)
    alternate = movement_signatures(alternate_calls)
    entropy = structural_entropy(baseline, alternate)
    baseline_coverage, baseline_matched = _coverage(expected, baseline)
    alternate_coverage, alternate_matched = _coverage(expected, alternate)
    # A strict no-regression rule is applied after uncertainty gating.  The
    # full-match exception handles a clear, pre-execution intent improvement:
    # it does not inspect whether the simulator later succeeded.
    low_uncertainty = entropy["normalized_entropy"] <= entropy_threshold
    clear_intent_improvement = alternate_coverage > baseline_coverage and alternate_coverage >= 1.0
    accept_alternate = bool(
        alternate and alternate != baseline
        and (low_uncertainty or clear_intent_improvement)
        and alternate_coverage >= baseline_coverage
    )
    selected = "alternate" if accept_alternate else "b0"
    return {
        "selected": selected,
        "entropy_threshold": entropy_threshold,
        "low_uncertainty": low_uncertainty,
        "clear_intent_improvement": clear_intent_improvement,
        "baseline_coverage": round(baseline_coverage, 6),
        "alternate_coverage": round(alternate_coverage, 6),
        "baseline_matched_waypoints": baseline_matched,
        "alternate_matched_waypoints": alternate_matched,
        "entropy": entropy,
        "baseline_signatures": baseline,
        "alternate_signatures": alternate,
        "gate_version": "sese_structural_gate_v1",
    }


def multi_candidate_gate(
    expected: list[list[float]],
    candidate_calls: list[Iterable[Any]],
    entropy_threshold: float = 0.80,
) -> dict[str, Any]:
    """Select one of N independently sampled candidates without outcomes.

    Candidate zero is the B0 anchor.  The gate only promotes a candidate when
    its pre-execution waypoint coverage is strictly higher than the anchor.
    This makes the online experiment a conservative minimal-repair test.
    """

    signatures = [movement_signatures(calls) for calls in candidate_calls]
    if not signatures:
        return {"selected_index": None, "gate_version": "sese_structural_gate_v1"}
    sets = [set(items) for items in signatures]
    union = set().union(*sets)
    common = set.intersection(*sets) if sets else set()
    unique_counts: list[int] = []
    for index, current in enumerate(sets):
        others = set().union(*(sets[j] for j in range(len(sets)) if j != index))
        unique_counts.append(len(current - others))
    ambiguous_count = max(0, len(union - common) - sum(unique_counts))
    masses = [float(len(common)), float(ambiguous_count)] + [float(value) for value in unique_counts]
    entropy = _entropy(masses) if sum(masses) else 0.0
    coverages = [_coverage(expected, items) for items in signatures]
    baseline_coverage, baseline_matched = coverages[0]
    best_coverage = max(value[0] for value in coverages)
    best_indices = [index for index, value in enumerate(coverages) if value[0] == best_coverage]
    best_index = best_indices[0]
    clear_intent_improvement = best_coverage > baseline_coverage and best_coverage >= 1.0
    low_uncertainty = entropy <= entropy_threshold
    accept_alternate = bool(
        best_index != 0
        and best_coverage > baseline_coverage
        and (low_uncertainty or clear_intent_improvement)
    )
    selected_index = best_index if accept_alternate else 0
    return {
        "selected_index": selected_index,
        "candidate_count": len(signatures),
        "entropy_threshold": entropy_threshold,
        "normalized_entropy": round(entropy, 6),
        "low_uncertainty": low_uncertainty,
        "clear_intent_improvement": clear_intent_improvement,
        "baseline_coverage": round(baseline_coverage, 6),
        "selected_coverage": round(coverages[selected_index][0], 6),
        "candidate_coverages": [round(value[0], 6) for value in coverages],
        "candidate_matched_waypoints": [value[1] for value in coverages],
        "common_structure_count": len(common),
        "ambiguous_structure_count": ambiguous_count,
        "candidate_unique_structure_counts": unique_counts,
        "selected_by_gate": accept_alternate,
        "signatures": signatures,
        "gate_version": "sese_structural_gate_v1",
    }
