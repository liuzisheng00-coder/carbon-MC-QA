"""Create an annotated discrete heatmap for per-question phrasing sensitivity."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap


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
DATA_PATH = ROOT / "per_question_phrasing_heatmap_source_data.csv"
OUTPUT_STEM = ROOT / "per_question_phrasing_heatmap"

COLORS = {
    "score_0": "#F2F3F5",
    "score_1": "#DCE5F1",
    "score_2": "#8FAAD0",
    "score_3": "#0F4D92",
    "text": "#272727",
    "text_mid": "#5F5F5F",
    "text_light": "#949494",
    "line": "#D8D8D8",
    "band": "#F7F8FA",
    "white": "#FFFFFF",
    "black": "#000000",
}

STATUS_LABELS = {
    "executable": "Executable",
    "empty_result": "Empty result",
    "clarification_required": "Clarification required",
    "unresolved_target": "Unresolved target",
}


def load_data(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "q": int(row["q"]),
                    "perspective": row["perspective"],
                    "operation": row["operation"],
                    "expected_status": row["expected_status"],
                    "p0": int(row["p0"]),
                    "p1": int(row["p1"]),
                    "p2": int(row["p2"]),
                    "stable": int(row["stable"]),
                    "display_order": int(row["display_order"]),
                }
            )
    return sorted(rows, key=lambda item: int(item["display_order"]))


def validate_data(rows: list[dict[str, object]]) -> None:
    if len(rows) != 10:
        raise ValueError(f"Expected 10 questions, received {len(rows)}.")
    if len({int(row["q"]) for row in rows}) != 10:
        raise ValueError("Question identifiers must be unique.")
    for row in rows:
        if any(not 0 <= int(row[key]) <= 3 for key in ("p0", "p1", "p2")):
            raise ValueError(f"Phrasing scores must lie in [0, 3]: {row}")
        if int(row["stable"]) not in (0, 1):
            raise ValueError(f"Stable must be binary: {row}")
        if str(row["expected_status"]) not in STATUS_LABELS:
            raise ValueError(f"Unknown expected status: {row}")


def add_group_structure(axes: list[plt.Axes]) -> None:
    # Alternating operation blocks retain the row grouping without heavy boxes.
    for ax in axes:
        ax.axhspan(1.5, 5.5, color=COLORS["band"], zorder=-3)
        ax.axhspan(8.5, 9.5, color=COLORS["band"], zorder=-3)
        for boundary in (0.5, 1.5, 5.5, 8.5):
            ax.axhline(boundary, color=COLORS["line"], lw=0.65, zorder=4)


def make_figure(rows: list[dict[str, object]]) -> plt.Figure:
    matrix = np.array([[row["p0"], row["p1"], row["p2"]] for row in rows], dtype=int)
    column_totals = matrix.sum(axis=0)
    column_rates = 100 * column_totals / (len(rows) * 3)
    stable_total = sum(int(row["stable"]) for row in rows)

    fig = plt.figure(figsize=(183 / 25.4, 115 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        1,
        4,
        width_ratios=[3.05, 1.55, 0.64, 1.82],
        left=0.045,
        right=0.985,
        bottom=0.145,
        top=0.82,
        wspace=0.055,
    )
    ax_meta = fig.add_subplot(grid[0, 0])
    ax_heat = fig.add_subplot(grid[0, 1], sharey=ax_meta)
    ax_stable = fig.add_subplot(grid[0, 2], sharey=ax_meta)
    ax_status = fig.add_subplot(grid[0, 3], sharey=ax_meta)
    axes = [ax_meta, ax_heat, ax_stable, ax_status]

    for ax in axes:
        ax.set_ylim(9.5, -0.5)
    add_group_structure(axes)

    # Left metadata columns.
    ax_meta.set_xlim(0, 1)
    ax_meta.axis("off")
    header_style = dict(
        transform=ax_meta.transAxes,
        va="bottom",
        fontsize=6.8,
        fontweight="bold",
        color=COLORS["text"],
    )
    ax_meta.text(0.01, 1.055, "Question", ha="left", **header_style)
    ax_meta.text(0.22, 1.055, "Perspective", ha="left", **header_style)
    ax_meta.text(0.59, 1.055, "Operation", ha="left", **header_style)

    for y, row in enumerate(rows):
        ax_meta.text(0.01, y, f"Q{row['q']}", ha="left", va="center", fontweight="bold", color=COLORS["text"])
        ax_meta.text(0.22, y, str(row["perspective"]), ha="left", va="center", color=COLORS["text_mid"])
        ax_meta.text(0.59, y, str(row["operation"]), ha="left", va="center", color=COLORS["text"])

    # Hero evidence: a four-level discrete heatmap with exact counts.
    cmap = ListedColormap(
        [COLORS["score_0"], COLORS["score_1"], COLORS["score_2"], COLORS["score_3"]]
    )
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], cmap.N)
    image = ax_heat.imshow(matrix, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest", zorder=1)
    ax_heat.set_xlim(-0.5, 2.5)
    ax_heat.set_xticks([0, 1, 2])
    ax_heat.set_xticklabels(
        [f"P{i}\n{rate:.1f}%" for i, rate in enumerate(column_rates)],
        fontsize=6.7,
        linespacing=1.35,
    )
    ax_heat.xaxis.tick_top()
    ax_heat.tick_params(axis="x", top=False, labeltop=True, pad=5)
    ax_heat.tick_params(axis="y", left=False, labelleft=False)
    ax_heat.text(
        0.5,
        1.16,
        "Correct runs / 3",
        transform=ax_heat.transAxes,
        ha="center",
        va="bottom",
        fontsize=7.2,
        fontweight="bold",
        color=COLORS["text"],
    )
    ax_heat.text(
        0.5,
        1.105,
        "overall pass rate",
        transform=ax_heat.transAxes,
        ha="center",
        va="bottom",
        fontsize=5.6,
        color=COLORS["text_light"],
    )

    ax_heat.set_xticks(np.arange(-0.5, 3, 1), minor=True)
    ax_heat.set_yticks(np.arange(-0.5, 10, 1), minor=True)
    ax_heat.grid(which="minor", color=COLORS["white"], linewidth=1.0)
    ax_heat.tick_params(which="minor", bottom=False, top=False, left=False, right=False)
    for spine in ax_heat.spines.values():
        spine.set_visible(False)

    for y in range(matrix.shape[0]):
        for x in range(matrix.shape[1]):
            value = int(matrix[y, x])
            ax_heat.text(
                x,
                y,
                str(value),
                ha="center",
                va="center",
                fontsize=7.2,
                fontweight="bold" if value == 3 else "normal",
                color=COLORS["white"] if value >= 2 else COLORS["text"],
                zorder=3,
            )

    # A single marker per row replaces the visually noisy unit-square display.
    ax_stable.set_xlim(0, 1)
    ax_stable.set_xticks([])
    ax_stable.tick_params(axis="y", which="both", left=False, right=False, labelleft=False)
    ax_stable.text(
        0.5,
        1.16,
        "Stable",
        transform=ax_stable.transAxes,
        ha="center",
        va="bottom",
        fontsize=7.2,
        fontweight="bold",
        color=COLORS["text"],
    )
    ax_stable.text(
        0.5,
        1.105,
        f"{stable_total}/10 questions",
        transform=ax_stable.transAxes,
        ha="center",
        va="bottom",
        fontsize=5.6,
        color=COLORS["text_light"],
    )
    for y, row in enumerate(rows):
        if int(row["stable"]) == 1:
            ax_stable.scatter(0.5, y, s=27, color=COLORS["black"], zorder=3)
        else:
            ax_stable.text(0.5, y, "–", ha="center", va="center", fontsize=8, color=COLORS["text_light"])
    for spine in ax_stable.spines.values():
        spine.set_visible(False)

    # Expected status remains text because it is categorical context, not a metric.
    ax_status.set_xlim(0, 1)
    ax_status.axis("off")
    ax_status.text(
        0.02,
        1.055,
        "Expected status",
        transform=ax_status.transAxes,
        ha="left",
        va="bottom",
        fontsize=6.8,
        fontweight="bold",
        color=COLORS["text"],
    )
    for y, row in enumerate(rows):
        status = STATUS_LABELS[str(row["expected_status"])]
        color = COLORS["text"] if status == "Executable" else COLORS["text_mid"]
        ax_status.text(0.02, y, status, ha="left", va="center", fontsize=6.7, color=color)

    # Compact, discrete legend; numeric cell labels remain the primary lookup.
    legend_x = 0.335
    legend_y = 0.052
    fig.text(legend_x - 0.10, legend_y, "Correct runs:", ha="right", va="center", fontsize=6.2, color=COLORS["text_mid"])
    for index, color in enumerate(cmap.colors):
        x = legend_x + index * 0.055
        fig.text(
            x,
            legend_y,
            str(index),
            ha="center",
            va="center",
            fontsize=6.2,
            color=COLORS["white"] if index >= 2 else COLORS["text"],
            bbox=dict(boxstyle="square,pad=0.24", facecolor=color, edgecolor=COLORS["white"], linewidth=0.4),
        )
    fig.text(
        0.60,
        legend_y,
        "● fully stable across all 9 runs",
        ha="left",
        va="center",
        fontsize=6.2,
        color=COLORS["text_mid"],
    )

    return fig


def save_outputs(fig: plt.Figure) -> None:
    fig.savefig(OUTPUT_STEM.with_suffix(".svg"), facecolor="white")
    fig.savefig(OUTPUT_STEM.with_suffix(".pdf"), facecolor="white")
    fig.savefig(OUTPUT_STEM.with_suffix(".tiff"), dpi=600, facecolor="white")
    fig.savefig(OUTPUT_STEM.with_suffix(".png"), dpi=300, facecolor="white")


def main() -> None:
    rows = load_data(DATA_PATH)
    validate_data(rows)
    fig = make_figure(rows)
    save_outputs(fig)
    plt.close(fig)


if __name__ == "__main__":
    main()
