"""Create publication-width system figures with a 14 pt minimum text size."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image


BODY_PT = 14
MODEL_BODY_PT = 12
TITLE_PT = 18
SMALL_TITLE_PT = 16
PNG_DPI = 300
TIFF_DPI = 600

BLUE = "#0B6EDC"
BLUE_DARK = "#0F4D92"
TEAL = "#2F8F99"
VIOLET = "#7564D6"
GREEN = "#238A45"
INK = "#252525"
MID = "#6B7280"
LIGHT = "#F3F5F7"
LINE = "#D6DCE3"
WHITE = "#FFFFFF"

HERE = Path(__file__).resolve().parent
MODEL_SOURCE = HERE / "source_model_qa.png"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
        "font.size": BODY_PT,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "savefig.facecolor": "white",
    }
)


def _rounded_box(
    ax: plt.Axes,
    xy: tuple[float, float],
    width: float,
    height: float,
    facecolor: str,
    edgecolor: str = "none",
    linewidth: float = 0.8,
    radius: float = 0.018,
    zorder: int = 1,
) -> FancyBboxPatch:
    patch = FancyBboxPatch(
        xy,
        width,
        height,
        transform=ax.transAxes,
        boxstyle=f"round,pad=0.006,rounding_size={radius}",
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=linewidth,
        zorder=zorder,
    )
    ax.add_patch(patch)
    return patch


def _add_question(ax: plt.Axes, y: float, lines: str, height: float = 0.110) -> None:
    _rounded_box(ax, (0.045, y), 0.91, height, BLUE, BLUE_DARK, 0.7, 0.014, 2)
    ax.text(
        0.075,
        y + height / 2,
        lines,
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=MODEL_BODY_PT,
        color=WHITE,
        fontweight="bold",
        linespacing=0.90,
        zorder=3,
    )


def _add_answer(ax: plt.Axes, y: float, height: float, lines: str) -> None:
    _rounded_box(ax, (0.045, y), 0.91, height, LIGHT, LINE, 0.6, 0.014, 2)
    ax.text(
        0.075,
        y + height / 2,
        lines,
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=MODEL_BODY_PT,
        color=INK,
        linespacing=0.98,
        zorder=3,
    )


def _new_figure(width_in: float, height_in: float) -> plt.Figure:
    width_px = round(width_in * PNG_DPI)
    height_px = round(height_in * PNG_DPI)
    return plt.figure(
        figsize=((width_px + 0.01) / PNG_DPI, (height_px + 0.01) / PNG_DPI),
        dpi=PNG_DPI,
        facecolor=WHITE,
        frameon=False,
    )


def _save_bundle(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for extension, dpi in (("png", PNG_DPI), ("svg", PNG_DPI), ("pdf", PNG_DPI), ("tiff", TIFF_DPI)):
        target = output_dir / f"{stem}.{extension}"
        kwargs = {"dpi": dpi, "bbox_inches": None, "pad_inches": 0}
        if extension == "tiff":
            kwargs["pil_kwargs"] = {"compression": "tiff_lzw"}
        fig.savefig(target, **kwargs)
        outputs.append(target)
    plt.close(fig)
    return outputs


def render_model_qa(output_dir: Path) -> list[Path]:
    if not MODEL_SOURCE.exists():
        raise FileNotFoundError(MODEL_SOURCE)
    with Image.open(MODEL_SOURCE) as source:
        source_rgb = source.convert("RGB")

    # Retain the original 865:590 landscape interface composition.  Only the
    # question-and-answer panel is redrawn so its key text is editable at 14 pt.
    fig = _new_figure(7.2, 4.91)
    screenshot_ax = fig.add_axes([0, 0, 1, 1])
    screenshot_ax.imshow(source_rgb, interpolation="lanczos", aspect="auto")
    screenshot_ax.axis("off")

    qa_ax = fig.add_axes([0.705, 0.020, 0.282, 0.900])
    qa_ax.set_xlim(0, 1)
    qa_ax.set_ylim(0, 1)
    qa_ax.axis("off")
    _rounded_box(qa_ax, (0.005, 0.005), 0.99, 0.99, WHITE, LINE, 0.9, 0.018, 0)
    qa_ax.text(0.045, 0.975, "Ask DM2C", ha="left", va="top", fontsize=MODEL_BODY_PT, fontweight="bold", color=INK)
    qa_ax.text(0.955, 0.915, "● Grounded", ha="right", va="top", fontsize=MODEL_BODY_PT, fontweight="bold", color=GREEN)

    _rounded_box(qa_ax, (0.045, 0.745), 0.91, 0.130, LIGHT, LINE, 0.6, 0.014, 2)
    qa_ax.text(
        0.075,
        0.810,
        "321 components\n2,993 nodes\n5,613 graph edges",
        transform=qa_ax.transAxes,
        ha="left",
        va="center",
        fontsize=MODEL_BODY_PT,
        color=INK,
        linespacing=0.95,
        zorder=3,
    )

    _add_question(qa_ax, 0.600, "Process query\nSteel-frame\nwelding", 0.120)
    _add_answer(
        qa_ax,
        0.490,
        0.090,
        "159.168 kgCO2e\nProcess perspective",
    )

    _add_question(qa_ax, 0.350, "Material query\nHighest-carbon\nSteel 43-355_A1", 0.120)
    _add_answer(
        qa_ax,
        0.240,
        0.090,
        "4,176.676 kgCO2e\nMaterial perspective",
    )

    _add_question(qa_ax, 0.100, "Product query\nSelected\ncomponent", 0.120)
    _add_answer(
        qa_ax,
        0.005,
        0.075,
        "214.900 kgCO2e\nProduct perspective",
    )

    return _save_bundle(fig, output_dir, "large_text_model_qa")


def _draw_table_panel(
    fig: plt.Figure,
    bounds: tuple[float, float, float, float],
    title: str,
    subtitle: str,
    columns: list[str],
    rows: list[list[str]],
    column_widths: list[float],
    accent: str,
) -> None:
    ax = fig.add_axes(bounds)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _rounded_box(ax, (0.002, 0.002), 0.996, 0.996, WHITE, LINE, 0.9, 0.012, 0)
    ax.add_patch(Rectangle((0.002, 0.86), 0.996, 0.138, transform=ax.transAxes, facecolor="#F7F8FA", edgecolor="none", zorder=1))
    ax.add_patch(Rectangle((0.002, 0.86), 0.010, 0.138, transform=ax.transAxes, facecolor=accent, edgecolor="none", zorder=2))
    ax.text(0.028, 0.948, title, ha="left", va="center", fontsize=TITLE_PT, fontweight="bold", color=INK, zorder=3)
    ax.text(0.028, 0.885, subtitle, ha="left", va="center", fontsize=BODY_PT, color=MID, zorder=3)

    left = 0.018
    right = 0.982
    table_width = right - left
    x_edges = [left]
    for width in column_widths:
        x_edges.append(x_edges[-1] + table_width * width)

    header_top = 0.825
    header_bottom = 0.675
    ax.add_patch(Rectangle((left, header_bottom), table_width, header_top - header_bottom, transform=ax.transAxes, facecolor=accent, edgecolor="none", alpha=0.10, zorder=1))
    for edge in x_edges:
        ax.plot([edge, edge], [0.045, header_top], transform=ax.transAxes, color=LINE, linewidth=0.65, zorder=2)
    for top in (header_top, header_bottom, 0.465, 0.255, 0.045):
        ax.plot([left, right], [top, top], transform=ax.transAxes, color=LINE, linewidth=0.65, zorder=2)

    for index, label in enumerate(columns):
        x0, x1 = x_edges[index], x_edges[index + 1]
        alignment = "left" if index == 0 else "center"
        x = x0 + 0.012 if index == 0 else (x0 + x1) / 2
        ax.text(x, (header_top + header_bottom) / 2, label, transform=ax.transAxes, ha=alignment, va="center", fontsize=BODY_PT, fontweight="bold", color=INK, zorder=3)

    row_centres = [0.570, 0.360, 0.150]
    for row_index, (row, y) in enumerate(zip(rows, row_centres)):
        if row_index % 2 == 1:
            ax.add_patch(Rectangle((left, y - 0.105), table_width, 0.210, transform=ax.transAxes, facecolor="#FAFBFC", edgecolor="none", zorder=0.5))
        for column_index, value in enumerate(row):
            x0, x1 = x_edges[column_index], x_edges[column_index + 1]
            alignment = "left" if column_index == 0 else "center"
            x = x0 + 0.012 if column_index == 0 else (x0 + x1) / 2
            kwargs = {"fontweight": "bold"} if column_index in {0, len(row) - 2} else {}
            if value == "measured":
                ax.text(
                    x,
                    y,
                    value,
                    transform=ax.transAxes,
                    ha="center",
                    va="center",
                    fontsize=BODY_PT,
                    fontweight="bold",
                    color=GREEN,
                    bbox={"boxstyle": "round,pad=0.24", "facecolor": "#EAF7EE", "edgecolor": GREEN, "linewidth": 0.8},
                    zorder=3,
                )
            else:
                ax.text(
                    x,
                    y,
                    value,
                    transform=ax.transAxes,
                    ha=alignment,
                    va="center",
                    fontsize=BODY_PT,
                    color=INK,
                    linespacing=1.02,
                    zorder=3,
                    **kwargs,
                )


def render_accounts(output_dir: Path) -> list[Path]:
    fig = _new_figure(7.2, 8.4)
    panel_height = 0.305
    _draw_table_panel(
        fig,
        (0.018, 0.678, 0.964, panel_height),
        "Product account",
        "Components projected from accepted canonical carbon records",
        ["ITEM", "CONTEXT", "C_MAT", "C_PROC"],
        [
            ["Floor Slab:110mm:\n2130995", "IfcSlab", "664.2", "88"],
            ["200×100×14 Steel Purlin:\nC1:2130673", "IfcColumn", "345.6", "3.9"],
            ["200×100×14 Steel Purlin:\nC1:2130675", "IfcColumn", "345.6", "3.9"],
        ],
        [0.53, 0.20, 0.135, 0.135],
        BLUE_DARK,
    )
    _draw_table_panel(
        fig,
        (0.018, 0.3475, 0.964, panel_height),
        "Material account",
        "IFC materials projected from the same accepted emissions",
        ["ITEM", "CONTEXT", "C_MAT", "TOTAL", "STATUS"],
        [
            ["Metal - Steel 43 -\n355_A1", "45 components", "4,176.7", "4,176.7", "measured"],
            ["Concrete, Cast-in-Place\nGray", "2 components", "784.6", "784.6", "measured"],
            ["Calcium silicate board", "9 components", "594.8", "594.8", "measured"],
        ],
        [0.34, 0.22, 0.13, 0.15, 0.16],
        TEAL,
    )
    _draw_table_panel(
        fig,
        (0.018, 0.017, 0.964, panel_height),
        "Process account",
        "Factory activities, energy use, and process evidence",
        ["ITEM", "CONTEXT", "C_PROC", "TOTAL", "STATUS"],
        [
            ["Steel material intake\n& inspection", "diesel", "519.2", "519.2", "measured"],
            ["Post-weld repair & NDT", "electricity", "492.3", "492.3", "measured"],
            ["Steel frame welding", "electricity", "159.2", "159.2", "measured"],
        ],
        [0.34, 0.22, 0.13, 0.15, 0.16],
        VIOLET,
    )
    return _save_bundle(fig, output_dir, "large_text_accounts")


def render_all(output_dir: Path | None = None) -> list[Path]:
    if output_dir is None:
        output_dir = HERE
    outputs: list[Path] = []
    outputs.extend(render_model_qa(output_dir))
    outputs.extend(render_accounts(output_dir))
    return outputs


if __name__ == "__main__":
    for output in render_all():
        print(output)
