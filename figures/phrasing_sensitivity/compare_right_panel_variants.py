"""Generate three alternative right-panel designs for phrasing sensitivity."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import FancyBboxPatch
from matplotlib.transforms import Bbox


plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["font.size"] = 7
plt.rcParams["axes.linewidth"] = 0.7
plt.rcParams["axes.spines.right"] = False
plt.rcParams["axes.spines.top"] = False
plt.rcParams["legend.frameon"] = False


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "phrasing_sensitivity_source_data.csv"

COLORS = {
    "operation": "#0F4D92",
    "perspective": "#7884B4",
    "neutral_dark": "#4D4D4D",
    "group_label": "#000000",
    "neutral_mid": "#8C8C8C",
    "neutral_light": "#D8D8D8",
    "track": "#E7E9ED",
    "band": "#F6F7FA",
    "white": "#FFFFFF",
}

Y_POSITIONS = [8.0, 7.0, 6.0, 5.0, 4.0, 2.35, 1.35, 0.35]
GROUP_LABEL_X = -0.225
PASS_RATE_HEADER_X = 0.91
PASS_RATE_LABEL_OFFSET = 2.7
CAPSULE_FRACTION_BASE_X = 114.0
PNG_EXPORT_DPI = 300
CANVAS_SAFETY_PAD_MM = 2.0


def load_data() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with DATA_PATH.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "group": row["group"],
                    "category": row["category"],
                    "questions": int(row["questions"]),
                    "pass_rate_pct": float(row["pass_rate_pct"]),
                    "fully_stable_questions": int(row["fully_stable_questions"]),
                    "display_order": int(row["display_order"]),
                }
            )
    rows.sort(key=lambda row: int(row["display_order"]))
    return rows


def validate_data(rows: list[dict[str, object]]) -> None:
    if len(rows) != 8:
        raise ValueError(f"Expected 8 rows, received {len(rows)}.")
    for row in rows:
        n = int(row["questions"])
        stable = int(row["fully_stable_questions"])
        rate = float(row["pass_rate_pct"])
        if n < 1 or not 0 <= stable <= n or not 0 <= rate <= 100:
            raise ValueError(f"Invalid row: {row}")


def row_color(row: dict[str, object]) -> str:
    return COLORS["operation"] if row["group"] == "Operation" else COLORS["perspective"]


def stable_pct(row: dict[str, object]) -> float:
    return 100 * int(row["fully_stable_questions"]) / int(row["questions"])


def export_pixel_dx(ax: plt.Axes, pixels: float, dpi: int = PNG_EXPORT_DPI) -> float:
    """Convert a horizontal export-pixel shift to data units for an axis."""
    xmin, xmax = ax.get_xlim()
    width_px = ax.get_position().width * ax.figure.get_figwidth() * dpi
    return pixels * (xmax - xmin) / width_px


def asymmetric_crop_bbox(fig: plt.Figure, pad_mm: float = CANVAS_SAFETY_PAD_MM) -> Bbox:
    """Crop only the left and top canvas edges around rendered content."""
    fig.canvas.draw()
    tight = fig.get_tightbbox(fig.canvas.get_renderer())
    pad_inches = pad_mm / 25.4
    left = max(0.0, tight.x0 - pad_inches)
    top = min(fig.get_figheight(), tight.y1 + pad_inches)
    return Bbox.from_extents(left, 0.0, fig.get_figwidth(), top)


def build_base(rows: list[dict[str, object]]) -> tuple[plt.Figure, plt.Axes, plt.Axes]:
    fig = plt.figure(figsize=(183 / 25.4, 96 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        1,
        2,
        width_ratios=[3.55, 1.45],
        left=0.245,
        right=0.975,
        bottom=0.16,
        top=0.86,
        wspace=0.13,
    )
    ax_rate = fig.add_subplot(grid[0, 0])
    ax_right = fig.add_subplot(grid[0, 1], sharey=ax_rate)

    for axis in (ax_rate, ax_right):
        axis.axhspan(-0.20, 2.85, color=COLORS["band"], zorder=0)
        axis.axhline(3.35, color=COLORS["neutral_light"], lw=0.7, zorder=0)

    for row, y in zip(rows, Y_POSITIONS, strict=True):
        rate = float(row["pass_rate_pct"])
        color = row_color(row)
        ax_rate.plot([0, rate], [y, y], color=color, alpha=0.34, lw=2.0, zorder=2)
        ax_rate.scatter(
            rate,
            y,
            s=35,
            marker="o",
            facecolor=color,
            edgecolor=COLORS["white"],
            linewidth=0.7,
            zorder=3,
        )
        label_x = rate + PASS_RATE_LABEL_OFFSET
        ax_rate.text(
            label_x,
            y,
            f"{rate:.0f}%",
            ha="left",
            va="center",
            color=COLORS["neutral_dark"],
            fontsize=6.7,
        )

    labels = [f"{row['category']}   $n$={row['questions']}" for row in rows]
    ax_rate.set_yticks(Y_POSITIONS)
    ax_rate.set_yticklabels(labels, fontsize=7)
    ax_rate.tick_params(axis="y", length=0, pad=6)
    ax_rate.tick_params(axis="x", width=0.7, length=3, color=COLORS["neutral_mid"])
    ax_rate.set_xlim(0, 110)
    ax_rate.set_xticks([0, 25, 50, 75, 100])
    ax_rate.set_xlabel("Pass rate (%)", fontsize=7, labelpad=5)
    ax_rate.set_ylim(-0.35, 8.75)
    ax_rate.spines["left"].set_visible(False)
    ax_rate.spines["bottom"].set_color(COLORS["neutral_mid"])
    ax_rate.text(
        PASS_RATE_HEADER_X,
        1.055,
        "Pass rate",
        transform=ax_rate.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.4,
        fontweight="bold",
        color=COLORS["neutral_dark"],
    )

    ax_rate.text(
        GROUP_LABEL_X,
        8.60,
        "OPERATION",
        transform=ax_rate.get_yaxis_transform(),
        ha="left",
        va="center",
        fontsize=6.2,
        fontweight="bold",
        color=COLORS["group_label"],
        clip_on=False,
    )
    ax_rate.text(
        GROUP_LABEL_X,
        2.82,
        "PERSPECTIVE",
        transform=ax_rate.get_yaxis_transform(),
        ha="left",
        va="center",
        fontsize=6.2,
        fontweight="bold",
        color=COLORS["group_label"],
        clip_on=False,
    )

    ax_right.tick_params(axis="y", left=False, labelleft=False)
    for spine in ax_right.spines.values():
        spine.set_visible(False)
    return fig, ax_rate, ax_right


def add_right_header(ax: plt.Axes, subtitle: str) -> None:
    ax.text(
        0,
        1.055,
        "Cross-phrasing stability",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.4,
        fontweight="bold",
        color=COLORS["neutral_dark"],
    )
    ax.text(
        0,
        1.005,
        subtitle,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=5.8,
        color=COLORS["neutral_mid"],
    )


def draw_capsule(ax: plt.Axes, rows: list[dict[str, object]]) -> None:
    add_right_header(ax, "filled length = stable share")
    ax.set_xlim(-3, 124)
    ax.set_xticks([])
    fraction_label_x = CAPSULE_FRACTION_BASE_X + export_pixel_dx(ax, 1, PNG_EXPORT_DPI)
    for row, y in zip(rows, Y_POSITIONS, strict=True):
        pct = stable_pct(row)
        color = row_color(row)
        ax.plot([0, 100], [y, y], color=COLORS["track"], lw=7.2, solid_capstyle="round", zorder=1)
        if pct > 0:
            ax.plot([0, pct], [y, y], color=color, lw=7.2, solid_capstyle="round", zorder=2)
        ax.text(
            fraction_label_x,
            y,
            f"{row['fully_stable_questions']}/{row['questions']}",
            ha="right",
            va="center",
            fontsize=6.7,
            color=COLORS["neutral_dark"],
        )


def draw_stability_dot(ax: plt.Axes, rows: list[dict[str, object]]) -> None:
    add_right_header(ax, "position = stable share")
    ax.set_xlim(0, 118)
    ax.set_xticks([0, 50, 100])
    ax.set_xticklabels(["0", "50", "100"], fontsize=6)
    ax.tick_params(axis="x", bottom=True, length=2.5, width=0.6, color=COLORS["neutral_mid"])
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(COLORS["neutral_mid"])
    ax.set_xlabel("Fully stable (%)", fontsize=6.5, labelpad=4)
    for row, y in zip(rows, Y_POSITIONS, strict=True):
        pct = stable_pct(row)
        color = row_color(row)
        ax.plot([0, 100], [y, y], color=COLORS["track"], lw=1.1, zorder=1)
        ax.scatter(
            pct,
            y,
            s=30,
            facecolor=color,
            edgecolor=COLORS["white"],
            linewidth=0.7,
            zorder=2,
        )
        ax.text(
            114,
            y,
            f"{row['fully_stable_questions']}/{row['questions']}",
            ha="right",
            va="center",
            fontsize=6.7,
            color=COLORS["neutral_dark"],
        )


def draw_heat_cell(ax: plt.Axes, rows: list[dict[str, object]]) -> None:
    add_right_header(ax, "colour intensity = stable share")
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    cmap = LinearSegmentedColormap.from_list(
        "stable_blue",
        ["#F1F3F6", "#B4C0E4", COLORS["operation"]],
    )
    norm = Normalize(vmin=0, vmax=100)
    for row, y in zip(rows, Y_POSITIONS, strict=True):
        pct = stable_pct(row)
        face = cmap(norm(pct))
        cell = FancyBboxPatch(
            (0.04, y - 0.29),
            0.92,
            0.58,
            boxstyle="round,pad=0.006,rounding_size=0.035",
            linewidth=0.55,
            edgecolor=COLORS["white"],
            facecolor=face,
            zorder=2,
        )
        ax.add_patch(cell)
        text_color = COLORS["white"] if pct >= 60 else COLORS["neutral_dark"]
        ax.text(
            0.5,
            y,
            f"{row['fully_stable_questions']}/{row['questions']}",
            ha="center",
            va="center",
            fontsize=6.8,
            fontweight="bold" if pct >= 60 else "normal",
            color=text_color,
            zorder=3,
        )


def save_variant(rows: list[dict[str, object]], name: str, drawer) -> None:
    fig, _, ax_right = build_base(rows)
    drawer(ax_right, rows)
    stem = ROOT / name
    crop_bbox = asymmetric_crop_bbox(fig)
    fig.savefig(stem.with_suffix(".svg"), facecolor="white", bbox_inches=crop_bbox)
    fig.savefig(
        stem.with_suffix(".png"),
        dpi=PNG_EXPORT_DPI,
        facecolor="white",
        bbox_inches=crop_bbox,
    )
    plt.close(fig)


def main() -> None:
    rows = load_data()
    validate_data(rows)
    save_variant(rows, "variant_a_capsule", draw_capsule)
    save_variant(rows, "variant_b_stability_dot", draw_stability_dot)
    save_variant(rows, "variant_c_heat_cell", draw_heat_cell)


if __name__ == "__main__":
    main()
