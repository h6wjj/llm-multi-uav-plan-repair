"""Create the final polished, vertically stacked three-panel Fig.3.

All plotted numbers are read from ``reports/fig3_processed_metrics.json``.
The frozen experiment data are never opened by this script, and no
simulation, experiment, or synthetic-data generation is performed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

# Use a non-interactive backend for reproducible VM-side vector export.
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
STYLE_DIR = ROOT / "style"
if str(STYLE_DIR) not in sys.path:
    sys.path.insert(0, str(STYLE_DIR))

from paper_style import (  # noqa: E402
    FIG_WIDTH_MM,
    FONT_FAMILY,
    mm_to_inch,
)


INPUT = ROOT / "reports" / "fig3_processed_metrics.json"
SVG_OUTPUT = ROOT / "figures" / "source" / "fig3" / "Fig3_main_results_polished.svg"
PDF_OUTPUT = ROOT / "figures" / "pdf" / "Fig3_main_results_polished.pdf"

# These names are only the internal JSON keys. The formal names below are
# the only method labels allowed to appear in the final figure.
METHOD_NAMES = {
    "Agent4Drone_no_recovery": "Agent4Drone (no recovery)",
    "LLM_replanning": "LLM replanning",
    "Global_reallocation": "Global reallocation",
    "Intent_preserving_repair": "Intent-preserving repair",
}

# Full formal names are retained; only the long final label uses an allowed
# line break for compact typesetting in the y-axis.
PLOT_LABELS = {
    "Agent4Drone_no_recovery": "Agent4Drone (no recovery)",
    "LLM_replanning": "LLM replanning",
    "Global_reallocation": "Global reallocation",
    "Intent_preserving_repair": "Intent-preserving repair",
}

# The same four formal method rows are used by every panel. Recovery-only
# panels retain the baseline row but leave it without a data point.
PANEL_A_METHODS = [
    "Agent4Drone_no_recovery",
    "Global_reallocation",
    "LLM_replanning",
    "Intent_preserving_repair",
]
PANEL_BC_METHODS = [
    "Global_reallocation",
    "LLM_replanning",
    "Intent_preserving_repair",
]

# Fixed V4 palette: one teal contribution color and three grayscale baselines.
METHOD_COLORS = {
    "Agent4Drone_no_recovery": "#8A8F93",
    "LLM_replanning": "#B5BBC0",
    "Global_reallocation": "#555B60",
    "Intent_preserving_repair": "#00858A",
}


def load_metrics() -> dict[str, Any]:
    """Read and validate the one permitted processed-metrics report."""

    if not INPUT.is_file():
        raise FileNotFoundError(f"Missing processed metrics: {INPUT}")
    with INPUT.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("methods"), dict):
        raise ValueError("Processed metrics must contain a methods object")
    missing = [name for name in METHOD_NAMES if name not in data["methods"]]
    if missing:
        raise ValueError(f"Missing methods in processed metrics: {missing}")
    return data


def configure_axes(
    ax: plt.Axes,
    method_order: list[str],
    panel_title: str,
    xlabel: str,
    xlim: tuple[float, float],
    *,
    log_x: bool = False,
) -> dict[str, int]:
    """Apply shared paper styling and return method-to-y-position mapping."""

    method_count = len(method_order)
    y_positions = {
        method_name: method_count - 1 - index
        for index, method_name in enumerate(method_order)
    }
    ax.set_ylim(-0.5, method_count - 0.5)
    tick_positions = [y_positions[name] for name in method_order]
    ax.set_yticks(tick_positions)
    ax.set_yticklabels(
        [PLOT_LABELS[name] for name in method_order],
        fontsize=8,
        color="#4D555B",
    )

    if log_x:
        ax.set_xscale("log")
        ax.set_xticks([0.01, 0.1, 1, 10, 100])
        ax.set_xticklabels(["0.01", "0.1", "1", "10", "100"])
    ax.set_xlim(*xlim)
    ax.set_xlabel(xlabel, fontsize=8, color="#4D555B", labelpad=5)
    ax.set_title(
        panel_title,
        loc="left",
        fontsize=9.5,
        fontweight="normal",
        color="#4D555B",
        pad=5,
    )
    ax.tick_params(
        axis="both",
        which="both",
        labelsize=8,
        length=3,
        width=0.6,
        colors="#4D555B",
    )
    ax.grid(axis="x", color="#D9DDDF", linewidth=0.45, linestyle="-", zorder=0)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in (ax.spines["left"], ax.spines["bottom"]):
        spine.set_color("#4D555B")
        spine.set_linewidth(0.7)
    return y_positions


def draw_metric(
    ax: plt.Axes,
    method_data: dict[str, Any],
    method_order: list[str],
    y_positions: dict[str, int],
    metric_key: str,
    *,
    marker_size: float,
    line_width: float = 0.7,
) -> None:
    """Draw means with sample-standard-deviation error bars from the report."""

    for method_name in method_order:
        stats = method_data[method_name][metric_key]
        mean = stats.get("mean")
        std = stats.get("std")
        sample_count = stats.get("n", 0)
        # Null metrics remain absent; there is no interpolation or fallback.
        if mean is None or std is None or sample_count == 0:
            continue
        color = METHOD_COLORS[method_name]
        # Keep the long LLM interval visible but visually subordinate to its
        # marker; all numeric values remain those supplied by the report.
        current_line_width = 0.55 if method_name == "LLM_replanning" else line_width
        # Make the vertical end caps unambiguous without making the whisker
        # itself dominant. The cap length is purely visual.
        current_capsize = 2.4 if method_name == "LLM_replanning" else 3.0
        current_cap_width = 0.65 if method_name == "LLM_replanning" else 0.8
        # The point is the reported mean; the error bar is the reported
        # sample standard deviation (not a confidence interval or standard
        # error). No statistic is recomputed or altered here.
        ax.errorbar(
            mean,
            y_positions[method_name],
            xerr=std,
            fmt="o",
            markersize=marker_size,
            markerfacecolor=color,
            markeredgecolor=color,
            markeredgewidth=0.45,
            ecolor=color,
            elinewidth=current_line_width,
            capsize=current_capsize,
            capthick=current_cap_width,
            linestyle="none",
            zorder=3,
        )


def mark_inapplicable(
    ax: plt.Axes,
    y_positions: dict[str, int],
    method_name: str,
    xlim: tuple[float, float],
    *,
    log_x: bool = False,
) -> None:
    """Place a low-contrast dash in the retained baseline row."""

    x_position = xlim[0] * 1.6 if log_x else xlim[0] + 0.02 * (xlim[1] - xlim[0])
    ax.text(
        x_position,
        y_positions[method_name],
        "—",
        color="#D9DDDF",
        fontsize=8,
        ha="left",
        va="center",
        clip_on=False,
        zorder=1,
    )


def main() -> None:
    data = load_metrics()
    method_data = data["methods"]

    # Enforce editable SVG text and TrueType PDF text explicitly.
    matplotlib.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [FONT_FAMILY, "Liberation Sans", "DejaVu Sans"],
            "font.size": 9,
            "axes.titlesize": 9.5,
            "axes.labelsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )

    width_in = mm_to_inch(FIG_WIDTH_MM)
    height_in = mm_to_inch(135)
    figure = plt.figure(figsize=(width_in, height_in), facecolor="white")
    grid = figure.add_gridspec(
        3,
        1,
        # Panel B receives approximately 20% more height for the core metric.
        height_ratios=(1.0, 1.38, 1.0),
        # Compress the inter-panel whitespace by roughly 17% relative to V4.1.
        hspace=0.38,
    )
    axes = [figure.add_subplot(grid[index, 0]) for index in range(3)]

    # Panel A: Feasibility / contract satisfaction rate (CSR).
    positions_a = configure_axes(
        axes[0],
        PANEL_A_METHODS,
        "(a) Feasibility",
        "Constraint satisfaction rate",
        (0.55, 1.05),
    )
    draw_metric(
        axes[0],
        method_data,
        PANEL_A_METHODS,
        positions_a,
        "CSR",
        marker_size=5.0,
    )

    # Panel B: Plan stability / normalized plan stability distance (NPSD).
    positions_b = configure_axes(
        axes[1],
        PANEL_A_METHODS,
        "(b) Plan stability",
        "Normalized plan stability distance",
        (0.0, 1.05),
    )
    draw_metric(
        axes[1],
        method_data,
        PANEL_BC_METHODS,
        positions_b,
        "NPSD",
        marker_size=7.4,
        line_width=0.8,
    )
    mark_inapplicable(
        axes[1],
        positions_b,
        "Agent4Drone_no_recovery",
        (0.0, 1.05),
    )

    # Panel C: Repair decision time / repair-decision latency (log scale).
    positions_c = configure_axes(
        axes[2],
        PANEL_A_METHODS,
        "(c) Repair decision time",
        "Repair decision latency (s)",
        (0.005, 150.0),
        log_x=True,
    )
    draw_metric(
        axes[2],
        method_data,
        PANEL_BC_METHODS,
        positions_c,
        "repair_latency",
        marker_size=5.0,
    )
    mark_inapplicable(
        axes[2],
        positions_c,
        "Agent4Drone_no_recovery",
        (0.005, 150.0),
        log_x=True,
    )

    # One aligned figure, with a single left margin and no independent legend.
    figure.subplots_adjust(left=0.34, right=0.99, top=0.975, bottom=0.07)
    SVG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PDF_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(SVG_OUTPUT, format="svg", facecolor="white")
    figure.savefig(PDF_OUTPUT, format="pdf", facecolor="white")
    plt.close(figure)

    svg_text = SVG_OUTPUT.read_text(encoding="utf-8")
    text_count = svg_text.count("<text")
    path_count = svg_text.count("<path")
    if text_count <= 0:
        raise RuntimeError("SVG contains no editable text elements")
    print("Fig.3 polished generated")
    print(f"SVG: {SVG_OUTPUT}")
    print(f"PDF: {PDF_OUTPUT}")
    print(f"SVG text count: {text_count}")
    print(f"SVG path count: {path_count}")
    print("SVG text converted to paths: NO")
    print("Formal methods used in the figure:")
    for method_name in PANEL_A_METHODS:
        print(f"  {METHOD_NAMES[method_name]}")
    print("Numeric source: reports/fig3_processed_metrics.json")


if __name__ == "__main__":
    main()
