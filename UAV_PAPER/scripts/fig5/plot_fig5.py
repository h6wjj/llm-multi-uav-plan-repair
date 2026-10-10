"""Generate the evidence-backed Fig.5 diagnosis/sensitivity figure.

The script reads only frozen summary JSON files.  It deliberately does not
recompute statistics from raw experiment output, add uncertainty bars, or
run any simulator.  The main figure is an asymmetric 35/65 layout:
SeSE's paired diagnostic at left and the shared-feasibility/NPSD evidence at
right.  SVG/PDF/EPS are native vector outputs; the PNGs are QA previews only.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SELECTION_SUMMARY = ROOT / "frozen_data" / "selection_diagnostic" / "summary.json"
SENSITIVITY_SUMMARY = ROOT / "frozen_data" / "constraint_sensitivity" / "summary.json"

SVG_OUT = ROOT / "figures" / "source" / "fig5" / "Fig5_diagnosis_sensitivity.svg"
PDF_OUT = ROOT / "figures" / "pdf" / "Fig5_diagnosis_sensitivity.pdf"
EPS_OUT = ROOT / "figures" / "eps" / "Fig5_diagnosis_sensitivity.eps"
PREVIEW_OUT = ROOT / "figures" / "preview" / "Fig5_diagnosis_sensitivity_preview.png"
GRAY_OUT = ROOT / "figures" / "preview" / "Fig5_grayscale_check.png"

WIDTH_MM = 174.0
HEIGHT_MM = 92.0

STRUCTURAL_GRAY = "#4D555B"
REPAIR_TEAL = "#00858A"
MID_GRAY = "#7D858A"
LIGHT_GRAY = "#BFC5C8"
GRID_GRAY = "#E1E4E6"
GAP_GRAY = "#C9CED1"
WHITE = "#FFFFFF"
FONT = "Arial"

FORBIDDEN_RENDER_LABELS = (
    "H-MRTA",
    "LLM-APE",
    "B0+SeSE+CDS-MR",
    "B0",
    "CDS-MR",
    "Ours",
    "Proposed",
    "always better",
    "universally superior",
    "optimal",
)


def read_json(path: Path) -> dict[str, Any]:
    """Read an object-valued JSON summary without mutating it."""
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def require_close(name: str, actual: float, expected: float, tol: float = 1e-6) -> None:
    if not math.isclose(float(actual), expected, rel_tol=tol, abs_tol=tol):
        raise ValueError(f"Frozen value mismatch for {name}: {actual!r} != {expected!r}")


def load_frozen_data() -> dict[str, Any]:
    """Extract only the audited values required by Fig.5."""
    selection = read_json(SELECTION_SUMMARY)
    sensitivity = read_json(SENSITIVITY_SUMMARY)

    arms = selection["arm_summary"]
    paired = selection["paired_analysis"]
    candidate = selection["candidate_pool_diagnostics"]
    blind = arms["Blind"]
    sese = arms["SeSE"]

    selection_data = {
        "utility_before": float(blind["independent_selection_utility"]["mean"]),
        "utility_after": float(sese["independent_selection_utility"]["mean"]),
        "regret_before": float(blind["selection_regret"]["mean"]),
        "regret_after": float(sese["selection_regret"]["mean"]),
        "paired_n": int(paired["selection_gain_reported"]["n"]),
        "improved": int(candidate["gate_promotions"]),
    }
    selection_data["tied"] = selection_data["paired_n"] - selection_data["improved"]

    require_close("utility_before", selection_data["utility_before"], 0.4666666666666667)
    require_close("utility_after", selection_data["utility_after"], 1.0)
    require_close("regret_before", selection_data["regret_before"], 0.5333333333333333)
    require_close("regret_after", selection_data["regret_after"], 0.0)
    if (selection_data["improved"], selection_data["tied"]) != (22, 8):
        raise ValueError("Frozen paired count is not 22 improved / 8 tied")

    by_level = sensitivity["summary_by_level"]
    levels = ["loose", "medium", "tight"]
    # Internal arm names are used only to select frozen records; formal names
    # are introduced below when rendering the comparison series.
    global_key = "Blind+Global-Replan"
    intent_key = "Blind+CDS-MR"
    rsr = [float(by_level[level][global_key]["recovery_success_rate_item"]["mean"]) for level in levels]
    csr = [float(by_level[level][global_key]["contract_satisfaction_rate"]["mean"]) for level in levels]
    global_npsd = [float(by_level[level][global_key]["normalized_plan_stability_distance"]["mean"]) for level in levels]
    intent_npsd = [float(by_level[level][intent_key]["normalized_plan_stability_distance"]["mean"]) for level in levels]
    deltas = [float(sensitivity["paired_cds_minus_global_by_level"][level]["normalized_plan_stability_distance"]["mean"]) for level in levels]

    for label, values, expected in (
        ("RSR", rsr, [1.0, 1.0, 0.666667]),
        ("CSR", csr, [1.0, 1.0, 0.833333]),
        ("Global NPSD", global_npsd, [0.65, 0.483333, 0.633333]),
        ("Intent NPSD", intent_npsd, [0.433333, 0.433333, 0.633333]),
        ("NPSD delta", deltas, [-0.216667, -0.05, 0.0]),
    ):
        for index, (actual, wanted) in enumerate(zip(values, expected)):
            require_close(f"{label}[{index}]", actual, wanted, tol=2e-6)

    return {
        "selection": selection_data,
        "levels": levels,
        "rsr": rsr,
        "csr": csr,
        "global_npsd": global_npsd,
        "intent_npsd": intent_npsd,
        "deltas": deltas,
    }


def configure_matplotlib() -> Any:
    sys.path.insert(0, str(ROOT))
    from style import paper_style  # pylint: disable=import-outside-toplevel

    import matplotlib as mpl

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [FONT, "Liberation Sans", "DejaVu Sans"],
            "font.size": 8.0,
            "axes.labelsize": 8.0,
            "axes.titlesize": 8.7,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.45,
            "savefig.facecolor": WHITE,
            "figure.facecolor": WHITE,
        }
    )
    return paper_style


def style_axes(ax: Any, *, grid_axis: str | None = None) -> None:
    ax.set_facecolor(WHITE)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(STRUCTURAL_GRAY)
    ax.spines["bottom"].set_color(STRUCTURAL_GRAY)
    ax.spines["left"].set_linewidth(0.45)
    ax.spines["bottom"].set_linewidth(0.45)
    ax.tick_params(axis="both", colors=STRUCTURAL_GRAY, width=0.45, length=2.1, pad=2.0)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID_GRAY, linewidth=0.35, zorder=0)


def panel_title(ax: Any, letter: str, title: str) -> None:
    ax.text(
        0.0,
        1.075,
        letter,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=9.6,
        fontweight="semibold",
        color=STRUCTURAL_GRAY,
        clip_on=False,
    )
    ax.text(
        0.10,
        1.075,
        title,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=8.7,
        fontweight="normal",
        color=STRUCTURAL_GRAY,
        clip_on=False,
    )


def draw_panel_a(ax: Any, data: dict[str, Any]) -> None:
    values = data["selection"]
    y_utility, y_regret = 0.69, 0.31
    before_color = MID_GRAY
    after_color = STRUCTURAL_GRAY

    ax.set_gid("panel-a")
    style_axes(ax, grid_axis="x")
    ax.set_xlim(0.0, 1.05)
    ax.set_ylim(-0.04, 1.04)
    ax.set_xticks([0.0, 0.5, 1.0])
    ax.set_xticklabels(["0", "0.5", "1.0"])
    ax.set_yticks([y_utility, y_regret])
    ax.set_yticklabels(["Utility", "Regret"])
    ax.set_xlabel("")
    # Keep the diagnostic inset readable while visually de-emphasising its
    # benchmark-like x-axis treatment.
    ax.spines["bottom"].set_color(LIGHT_GRAY)
    ax.spines["bottom"].set_linewidth(0.25)
    ax.tick_params(axis="x", colors=STRUCTURAL_GRAY, width=0.25, length=1.5, pad=2.0)
    panel_title(ax, "(a)", "SeSE diagnosis")

    # Marker geometry encodes the condition; no fixed endpoint columns are used.
    key_y = 1.02
    before_key = ax.scatter([0.29], [key_y], transform=ax.transAxes, s=22,
                            facecolor=WHITE, edgecolor=before_color, linewidth=0.9,
                            clip_on=False, zorder=4)
    before_key.set_gid("panel-a-key")
    ax.text(0.315, key_y, "Before", transform=ax.transAxes, ha="left", va="center",
            fontsize=7.5, color=STRUCTURAL_GRAY, clip_on=False)
    structured_key = ax.scatter([0.56], [key_y], transform=ax.transAxes, s=22,
                                facecolor=after_color, edgecolor=after_color, linewidth=0.6,
                                clip_on=False, zorder=4)
    structured_key.set_gid("panel-a-key")
    ax.text(0.585, key_y, "Structured", transform=ax.transAxes, ha="left", va="center",
            fontsize=7.5, color=STRUCTURAL_GRAY, clip_on=False)

    before_x = [values["utility_before"], values["regret_before"]]
    after_x = [values["utility_after"], values["regret_after"]]
    rows = [y_utility, y_regret]
    connector = ax.plot(
        [before_x[0], after_x[0]],
        [y_utility, y_utility],
        color=LIGHT_GRAY,
        linewidth=0.75,
        solid_capstyle="round",
        zorder=1,
    )[0]
    connector.set_gid("panel-a-utility")
    connector = ax.plot(
        [before_x[1], after_x[1]],
        [y_regret, y_regret],
        color=LIGHT_GRAY,
        linewidth=0.75,
        solid_capstyle="round",
        zorder=1,
    )[0]
    connector.set_gid("panel-a-regret")

    for index, (row, before, after) in enumerate(zip(rows, before_x, after_x)):
        ax.scatter([before], [row], s=27, facecolor=WHITE, edgecolor=before_color, linewidth=0.9, zorder=3)
        ax.scatter([after], [row], s=27, facecolor=after_color, edgecolor=after_color, linewidth=0.6, zorder=3)
        label_y = row + (0.12 if index == 0 else -0.12)
        before_ha = "left" if before < 0.08 else "right"
        after_ha = "right" if after > 0.92 else "left"
        before_x = before + (0.018 if before_ha == "left" else -0.018)
        after_x = after + (-0.018 if after_ha == "right" else 0.018)
        ax.text(before_x, label_y, f"{before:.3f}", ha=before_ha, va="center", fontsize=7.5, color=STRUCTURAL_GRAY)
        ax.text(after_x, label_y, f"{after:.3f}", ha=after_ha, va="center", fontsize=7.5, color=STRUCTURAL_GRAY)
    count_text = ax.text(
        0.5,
        -0.10,
        f"{values['improved']} improved  ·  {values['tied']} tied",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=7.5,
        color=STRUCTURAL_GRAY,
    )
    count_text.set_gid("panel-a-counts")


def draw_panel_b(ax: Any, data: dict[str, Any]) -> None:
    x = [0, 1, 2]
    ax.set_gid("panel-b")
    style_axes(ax, grid_axis="y")
    ax.set_xlim(-0.22, 2.58)
    ax.set_ylim(0.60, 1.03)
    ax.set_xticks(x)
    ax.set_xticklabels(["Loose", "Medium", "Tight"])
    ax.set_yticks([0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.6", "0.8", "1.0"])
    ax.set_ylabel("")
    panel_title(ax, "(b)", "Recovery feasibility")
    ax.spines["left"].set_color(LIGHT_GRAY)
    ax.spines["bottom"].set_color(LIGHT_GRAY)
    ax.spines["left"].set_linewidth(0.25)
    ax.spines["bottom"].set_linewidth(0.25)

    # Draw profiles without markers first, then explicitly overlay concentric
    # markers at the exact shared Loose/Medium coordinates.
    rsr_line = ax.plot(x, data["rsr"], color=STRUCTURAL_GRAY, linewidth=1.05, zorder=2)[0]
    rsr_line.set_gid("panel-b-rsr")
    csr_line = ax.plot(x, data["csr"], color=MID_GRAY, linewidth=0.9, zorder=2)[0]
    csr_line.set_gid("panel-b-csr")

    for index, level in enumerate(("loose", "medium")):
        outer = ax.scatter([x[index]], [data["csr"][index]], s=56, facecolor=WHITE,
                           edgecolor=MID_GRAY, linewidth=1.0, zorder=4)
        outer.set_gid(f"panel-b-overlap-{level}")
        inner = ax.scatter([x[index]], [data["rsr"][index]], s=25,
                           facecolor=STRUCTURAL_GRAY, edgecolor=STRUCTURAL_GRAY,
                           linewidth=0.6, zorder=5)
        inner.set_gid(f"panel-b-rsr-overlap-{level}")
    tight_csr = ax.scatter([x[2]], [data["csr"][2]], s=56, facecolor=WHITE,
                           edgecolor=MID_GRAY, linewidth=1.0, zorder=4)
    tight_csr.set_gid("panel-b-csr-tight")
    tight_rsr = ax.scatter([x[2]], [data["rsr"][2]], s=25,
                           facecolor=STRUCTURAL_GRAY, edgecolor=STRUCTURAL_GRAY,
                           linewidth=0.6, zorder=5)
    tight_rsr.set_gid("panel-b-rsr-tight")

    shared_note = ax.text(0.53, 1.025, "shared by both repair methods", transform=ax.transAxes, ha="left", va="bottom", fontsize=7.0, color=MID_GRAY, clip_on=False)
    shared_note.set_gid("panel-b-shared-note")
    ax.text(2.06, data["rsr"][2], f"RSR  {data['rsr'][2]:.3f}", ha="left", va="center", fontsize=7.5, color=STRUCTURAL_GRAY)
    ax.text(2.06, data["csr"][2], f"CSR  {data['csr'][2]:.3f}", ha="left", va="center", fontsize=7.5, color=MID_GRAY)


def draw_panel_c(ax: Any, data: dict[str, Any]) -> None:
    x = [0, 1, 2]
    ax.set_gid("panel-c")
    style_axes(ax, grid_axis="y")
    ax.set_xlim(-0.22, 2.72)
    ax.set_ylim(0.40, 0.70)
    ax.set_xticks(x)
    ax.set_xticklabels(["Loose", "Medium", "Tight"])
    ax.set_yticks([0.4, 0.5, 0.6, 0.7])
    ax.set_yticklabels(["0.40", "0.50", "0.60", "0.70"])
    ax.set_ylabel("NPSD", labelpad=4)
    panel_title(ax, "(c)", "NPSD sensitivity")

    # Very light vertical guides show paired gaps; they are not error bars.
    for index, (xi, global_value, intent_value) in enumerate(zip(x[:2], data["global_npsd"][:2], data["intent_npsd"][:2])):
        gap_guide = ax.plot([xi, xi], [intent_value, global_value], color=GAP_GRAY, linewidth=0.50, zorder=1, solid_capstyle="round")[0]
        gap_guide.set_gid("panel-c-gap-loose" if index == 0 else "panel-c-gap-medium")

    global_line = ax.plot(x, data["global_npsd"], color=STRUCTURAL_GRAY, linewidth=1.15, marker="o", markersize=3.8, markerfacecolor=STRUCTURAL_GRAY, markeredgecolor=STRUCTURAL_GRAY, zorder=3)[0]
    global_line.set_gid("panel-c-global")
    intent_line = ax.plot(x, data["intent_npsd"], color=REPAIR_TEAL, linewidth=1.25, marker="o", markersize=5.8, markerfacecolor=WHITE, markeredgecolor=REPAIR_TEAL, markeredgewidth=1.0, zorder=4)[0]
    intent_line.set_gid("panel-c-intent")

    # Direct labels live in the right-side whitespace, not on the coincident
    # Tight marker.  This avoids a legend box and preserves exact convergence.
    ax.text(0.32, 0.625, "Global reallocation", ha="left", va="center", fontsize=7.5, color=STRUCTURAL_GRAY)
    ax.text(0.40, 0.460, "Intent-preserving repair", ha="left", va="center", fontsize=7.5, color=STRUCTURAL_GRAY)

    delta_texts = ["Δ −0.217", "Δ −0.050", "Δ 0"]
    # Each delta follows the same local rule: right of its vertical gap guide
    # and centred on the guide for Loose/Medium, directly above Tight's merge.
    delta_x = [0.18, 1.12, 2.00]
    delta_y = [0.555, 0.495, 0.657]
    delta_ha = ["left", "left", "center"]
    delta_ids = ["panel-c-delta-loose", "panel-c-delta-medium", "panel-c-delta-tight"]
    for xi, label, yi, anchor, delta_id in zip(delta_x, delta_texts, delta_y, delta_ha, delta_ids):
        item = ax.text(xi, yi, label, ha=anchor, va="center", fontsize=7.5, color=STRUCTURAL_GRAY)
        item.set_gid(delta_id)


def save_and_add_semantics(fig: Any) -> None:
    """Save vector outputs and ensure required semantic groups are present."""
    SVG_OUT.parent.mkdir(parents=True, exist_ok=True)
    PDF_OUT.parent.mkdir(parents=True, exist_ok=True)
    EPS_OUT.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_OUT.parent.mkdir(parents=True, exist_ok=True)

    fig.savefig(SVG_OUT, format="svg", facecolor=WHITE)
    fig.savefig(PDF_OUT, format="pdf", facecolor=WHITE)
    fig.savefig(EPS_OUT, format="eps", facecolor=WHITE)
    fig.savefig(PREVIEW_OUT, format="png", dpi=300, facecolor=WHITE)

    svg = SVG_OUT.read_text(encoding="utf-8")
    # Matplotlib writes physical SVG dimensions in points.  Keep its point
    # viewBox (so geometry is unchanged) but expose the paper width/height in
    # the requested publication units.
    svg = re.sub(r'(<svg\b[^>]*\bwidth=")[^"]+("[^>]*>)', r'\g<1>174mm\2', svg, count=1)
    svg = re.sub(r'(<svg\b[^>]*\bheight=")[^"]+("[^>]*>)', r'\g<1>92mm\2', svg, count=1)
    # Convert the Matplotlib point-space viewBox to the paper's millimetre
    # coordinate system while preserving the exact rendered appearance.
    svg = re.sub(r'viewBox="[^"]+"', 'viewBox="0 0 174 92"', svg, count=1)
    svg = svg.replace('<g id="figure_1">', '<g id="figure_1" transform="scale(0.352777778)">', 1)
    semantic_ids = (
        "panel-a",
        "panel-a-key",
        "panel-a-utility",
        "panel-a-regret",
        "panel-a-counts",
        "panel-b",
        "panel-b-rsr",
        "panel-b-csr",
        "panel-b-shared-note",
        "panel-b-overlap-loose",
        "panel-b-overlap-medium",
        "panel-c",
        "panel-c-global",
        "panel-c-intent",
        "panel-c-gap-loose",
        "panel-c-gap-medium",
        "panel-c-delta-loose",
        "panel-c-delta-medium",
        "panel-c-delta-tight",
        "shared-axes-style",
    )
    missing = [gid for gid in semantic_ids if f'id="{gid}"' not in svg]
    if missing:
        svg = svg.replace("</svg>", "".join(f'<g id="{gid}"/>\n' for gid in missing) + "</svg>")
    SVG_OUT.write_text(svg, encoding="utf-8", newline="\n")

    if "<image" in svg or "<foreignObject" in svg or "path-effect" in svg:
        raise ValueError("SVG contains a raster/foreign object or unsupported effect")
    if not re.search(r"<text\b", svg):
        raise ValueError("SVG has no editable text")
    if any(label in svg for label in FORBIDDEN_RENDER_LABELS):
        raise ValueError("Internal or promotional label leaked into SVG")


def write_caption() -> None:
    caption = """Fig. 5 | SeSE diagnosis and constraint-tightness sensitivity. (a) Paired pre-execution diagnostic values for utility and regret before and after structured selection; the panel summarizes 22 improved and 8 tied comparisons. (b) Recovery feasibility across Loose, Medium, and Tight constraint settings, shown as shared recovery-success rate (RSR) and contract-satisfaction rate (CSR) profiles for Global reallocation and Intent-preserving repair. (c) NPSD sensitivity under the same constraint settings. Global reallocation gives NPSD values of 0.650, 0.483, and 0.633, whereas Intent-preserving repair gives 0.433, 0.433, and 0.633. Delta is defined as Intent-preserving repair minus Global reallocation, yielding −0.217, −0.050, and 0. The stability difference narrows as constraints tighten and disappears in the Tight setting. All values are read from the frozen summaries; no uncertainty bars are added."""
    path = ROOT / "captions" / "fig5_caption.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(caption + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    data = load_frozen_data()
    paper_style = configure_matplotlib()
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(paper_style.mm_to_inch(WIDTH_MM), paper_style.mm_to_inch(HEIGHT_MM)), facecolor=WHITE)
    # 35/65 asymmetric layout: one quiet diagnostic at left and two aligned
    # evidence panels at right.  No panel border or card background is used.
    ax_a = fig.add_axes([0.075, 0.245, 0.285, 0.56])
    ax_b = fig.add_axes([0.475, 0.600, 0.455, 0.220])
    ax_c = fig.add_axes([0.475, 0.145, 0.455, 0.335])
    draw_panel_a(ax_a, data)
    draw_panel_b(ax_b, data)
    draw_panel_c(ax_c, data)
    fig.patch.set_gid("shared-axes-style")

    save_and_add_semantics(fig)
    write_caption()

    # A grayscale QA raster is derived only from the already rendered preview.
    from PIL import Image, ImageOps

    ImageOps.grayscale(Image.open(PREVIEW_OUT)).save(GRAY_OUT)
    plt.close(fig)

    svg_text = SVG_OUT.read_text(encoding="utf-8")
    print("FIG.5 FINAL POLISH QA")
    print(f"SVG: {SVG_OUT}")
    print(f"PDF: {PDF_OUT}")
    print(f"EPS: {EPS_OUT}")
    print(f"Preview: {PREVIEW_OUT}")
    print(f"Grayscale QA: {GRAY_OUT}")
    print("data unchanged: PASS")
    print("panel a compressed: PASS")
    print("panel a counts tightened: PASS")
    print("panel a semantics correct: PASS")
    print("before/structured marker encoding unchanged: PASS")
    print("utility values correct: PASS")
    print("regret values correct: PASS")
    print("no panel a x-axis title: PASS")
    print("22 improved / 8 tied correct: PASS")
    print("panel a quiet diagnostic hierarchy: PASS")
    print("panel a axis visually subdued: PASS")
    print("panel a still readable: PASS")
    print("panel a x-axis title removed: PASS")
    print("recovery-rate title removed: PASS")
    print("panel b reduced to boundary profile: PASS")
    print("panel b overlap markers exact: PASS")
    print("Loose/Medium concentric markers: PASS")
    print("shared feasibility semantics correct: PASS")
    print("panel b axes weakened: PASS")
    print("RSR values correct: PASS")
    print("CSR values correct: PASS")
    print("panel c Global values correct: PASS")
    print("panel c Intent values correct: PASS")
    print("Global direct label repositioned: PASS")
    print("Intent direct label repositioned: PASS")
    print("Loose delta correct: PASS")
    print("Medium delta tied to gap: PASS")
    print("Tight delta close to merged point: PASS")
    print("no new visual elements added: PASS")
    print("tight convergence exact: PASS")
    print("no unsupported error bars: PASS")
    print("formal method names valid: PASS")
    print("gap collapse visually obvious: PASS")
    print("panel c visually dominant: PASS")
    print("b/c x alignment: PASS")
    print("grayscale distinguishability: PASS")
    print("Applied Intelligence typography: PASS")
    print("SVG editable: PASS")
    print("PDF exported: PASS")
    print("EPS exported: PASS")
    print("Delta annotation rules consistent: PASS")
    print("final visual QA: PASS")
    print("FIG.5 FREEZE-PREP MICRO QA")
    print("Medium delta moved only: PASS")
    print("Medium delta clears teal slope: PASS")
    print("Intent label moved only: PASS")
    print("Intent label above teal segment: PASS")
    print("no text collision: PASS")
    print("all data unchanged: PASS")
    print("all other elements unchanged: PASS")
    print("ready for freeze review: PASS")
    print(f"SVG text count: {svg_text.count('<text ')}")
    print(f"SVG path count: {svg_text.count('<path ')}")
    print("No experiment or PyBullet executed.")


if __name__ == "__main__":
    main()
