"""Compose the three system panels into one perspective-query figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from PIL import Image


plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["savefig.facecolor"] = "white"

WIDTH_IN = 5.75
HEIGHT_IN = 7.1
PNG_DPI = 300
TIFF_DPI = 600

BLUE = "#0F4D92"
TEAL = "#42949E"
VIOLET = "#7C6CCF"
INK = "#272727"
PANEL_BG = "#F5F7FA"
PANEL_EDGE = "#D5DEE7"

HERE = Path(__file__).resolve().parent
SOURCES = {
    "a": HERE / "annotated_panel_a_model_qa.png",
    "b": HERE / "annotated_panel_b_accounts.png",
    "c": HERE / "annotated_panel_c_calculation_path.png",
}


def _axes_from_inches(fig, x: float, y: float, width: float, height: float):
    return fig.add_axes([x / WIDTH_IN, y / HEIGHT_IN, width / WIDTH_IN, height / HEIGHT_IN])


def _add_image_panel(fig, path: Path, x: float, y: float, width: float, height: float) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    ax = _axes_from_inches(fig, x, y, width, height)
    ax.imshow(image, interpolation="none")
    ax.set_xlim(-0.5, image.width - 0.5)
    ax.set_ylim(image.height - 0.5, -0.5)
    ax.axis("off")


def _add_mapping_card(ax, x: float, color: str, title: str, mapping: str) -> None:
    width = 0.305
    card = FancyBboxPatch(
        (x, 0.10),
        width,
        0.52,
        boxstyle="round,pad=0.006,rounding_size=0.018",
        transform=ax.transAxes,
        facecolor="white",
        edgecolor=color,
        linewidth=0.75,
        zorder=2,
    )
    ax.add_patch(card)
    ax.plot(
        [x + 0.015, x + width - 0.015],
        [0.555, 0.555],
        transform=ax.transAxes,
        color=color,
        linewidth=1.4,
        solid_capstyle="round",
        zorder=3,
    )
    ax.text(
        x + 0.018,
        0.435,
        title,
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=5.9,
        fontweight="bold",
        color=color,
        zorder=4,
    )
    ax.text(
        x + 0.018,
        0.245,
        mapping,
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=4.65,
        color=INK,
        zorder=4,
    )


def _add_mapping_strip(fig) -> None:
    ax = _axes_from_inches(fig, 0.08, 2.36, 5.59, 0.55)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    background = FancyBboxPatch(
        (0.002, 0.015),
        0.996,
        0.97,
        boxstyle="round,pad=0.003,rounding_size=0.018",
        transform=ax.transAxes,
        facecolor=PANEL_BG,
        edgecolor=PANEL_EDGE,
        linewidth=0.65,
        zorder=1,
    )
    ax.add_patch(background)
    ax.text(
        0.5,
        0.815,
        "Question scope determines the returned carbon result",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=6.3,
        fontweight="bold",
        color=INK,
        zorder=4,
    )
    _add_mapping_card(ax, 0.018, BLUE, "Product", "selected component  →  214.900 kgCO2e")
    _add_mapping_card(ax, 0.3475, TEAL, "Material", "highest material  →  4,176.676 kgCO2e")
    _add_mapping_card(ax, 0.677, VIOLET, "Process", "welding stage  →  159.168 kgCO2e")


def _add_flow_connectors(fig) -> None:
    """Connect the hero panel to the scope strip and branch to both outputs."""
    ax = fig.add_axes([0, 0, 1, 1], zorder=30)
    ax.set_xlim(0, WIDTH_IN)
    ax.set_ylim(0, HEIGHT_IN)
    ax.axis("off")

    ax.annotate(
        "",
        xy=(WIDTH_IN / 2, 2.925),
        xytext=(WIDTH_IN / 2, 3.085),
        arrowprops={
            "arrowstyle": "-|>",
            "color": "#606060",
            "linewidth": 0.9,
            "mutation_scale": 8,
            "shrinkA": 0,
            "shrinkB": 0,
        },
        zorder=31,
    )

    branch_y = 2.265
    center_x = WIDTH_IN / 2
    left_x = 1.25
    right_x = 4.15
    ax.plot([center_x, center_x], [2.355, branch_y], color="#767676", linewidth=0.8, zorder=31)
    ax.plot([left_x, center_x], [branch_y, branch_y], color=BLUE, linewidth=0.9, zorder=31)
    ax.plot([center_x, right_x], [branch_y, branch_y], color=TEAL, linewidth=0.9, zorder=31)
    ax.plot(center_x, branch_y, marker="o", markersize=2.4, color="#606060", zorder=32)
    ax.annotate(
        "",
        xy=(left_x, 2.165),
        xytext=(left_x, branch_y),
        arrowprops={
            "arrowstyle": "-|>",
            "color": BLUE,
            "linewidth": 0.9,
            "mutation_scale": 8,
            "shrinkA": 0,
            "shrinkB": 0,
        },
        zorder=31,
    )
    ax.annotate(
        "",
        xy=(right_x, 2.165),
        xytext=(right_x, branch_y),
        arrowprops={
            "arrowstyle": "-|>",
            "color": TEAL,
            "linewidth": 0.9,
            "mutation_scale": 8,
            "shrinkA": 0,
            "shrinkB": 0,
        },
        zorder=31,
    )


def render_composite(output_dir: Path | None = None) -> list[Path]:
    """Render the standalone perspective-query composite in four formats."""
    for path in SOURCES.values():
        if not path.exists():
            raise FileNotFoundError(path)
    if output_dir is None:
        output_dir = HERE
    output_dir.mkdir(parents=True, exist_ok=True)

    width_px = round(WIDTH_IN * PNG_DPI)
    height_px = round(HEIGHT_IN * PNG_DPI)
    fig = plt.figure(
        figsize=((width_px + 0.01) / PNG_DPI, (height_px + 0.01) / PNG_DPI),
        dpi=PNG_DPI,
        facecolor="white",
        frameon=False,
    )

    top_width = 5.59
    top_height = top_width * 639 / 938
    _add_image_panel(fig, SOURCES["a"], 0.08, 3.11, top_width, top_height)
    _add_mapping_strip(fig)

    bottom_height = 2.08
    b_width = 2.35
    b_height = b_width * 485 / 554
    _add_image_panel(fig, SOURCES["b"], 0.08, 0.08 + (bottom_height - b_height) / 2, b_width, b_height)

    c_width = 3.05
    c_height = c_width * 639 / 938
    _add_image_panel(fig, SOURCES["c"], 2.62, 0.08 + (bottom_height - c_height) / 2, c_width, c_height)
    _add_flow_connectors(fig)

    outputs: list[Path] = []
    base = output_dir / "perspective_query_composite"
    for extension, dpi in (("png", PNG_DPI), ("svg", PNG_DPI), ("pdf", PNG_DPI), ("tiff", TIFF_DPI)):
        target = base.with_suffix(f".{extension}")
        kwargs = {"dpi": dpi, "bbox_inches": None, "pad_inches": 0}
        if extension == "tiff":
            kwargs["pil_kwargs"] = {"compression": "tiff_lzw"}
        fig.savefig(target, **kwargs)
        outputs.append(target)
    plt.close(fig)
    return outputs


if __name__ == "__main__":
    for output in render_composite():
        print(output)
