"""Shared publication style for the Applied Intelligence figures."""

import matplotlib as mpl

FIG_WIDTH_MM = 174
FONT_FAMILY = "Arial"

STRUCTURE_LINE = 0.8
EMPHASIS_LINE = 1.0
MIN_LINE = 0.3

STRUCTURAL_GRAY = "#4D555B"
REPAIR_TEAL = "#00858A"
FAILURE_RED = "#C43D32"
LOCKED_GRAY = "#D9DDDF"


def mm_to_inch(value_mm):
    """Convert millimetres to inches."""
    return value_mm / 25.4


mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": [FONT_FAMILY, "Liberation Sans", "DejaVu Sans"],
        "font.size": 9,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)
