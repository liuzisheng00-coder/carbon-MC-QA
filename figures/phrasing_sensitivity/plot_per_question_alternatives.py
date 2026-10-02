"""Alternative per-question visualizations for phrasing sensitivity."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


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

COLORS = {
    "blue_dark": "#0F4D92",
    "blue_mid": "#7884B4",
    "blue_soft": "#B4C0E4",
    "text": "#272727",
    "text_mid": "#5F5F5F",
    "text_light": "#999999",
    "line": "#D8D8D8",
    "band": "#F7F8FA",
    "black": "#000000",
    "white": "#FFFFFF",
}


def load_data(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for source in csv.DictReader(handle):
            rows.append(
                {
                    "q": int(source["q"]),
                    "perspective": source["perspective"],
                    "operation": source["operation"],
                    "expected_status": source["expected_status"],
                    "p0": int(source["p0"]),
                    "p1": int(source["p1"]),
                    "p2": int(source["p2"]),
                    "stable": int(source["stable"]),
                    "display_order": int(source["display_order"]),
                }
            )
    return sorted(rows, key=lambda row: int(row["display_order"]))


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


def derive_metrics(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    validate_data(rows)
    derived: list[dict[str, object]] = []
    for source in rows:
        row = dict(source)
        values = [int(row[key]) for key in ("p0", "p1", "p2")]
        row["delta_p1"] = values[1] - values[0]
        row["delta_p2"] = values[2] - values[0]
        row["sensitivity_gap"] = max(values) - min(values)
        derived.append(row)
    return derived


def validate_expected_metrics(rows: list[dict[str, object]]) -> None:
    by_q = {int(row["q"]): row for row in rows}
    expected_deltas = {
        4: (0, 0),
        6: (0, 0),
        1: (-3, 0),
        3: (-3, 0),
        5: (-3, 0),
        10: (0, 0),
        2: (-2, -2),
        7: (-3, -3),
        8: (0, 0),
        9: (-3, -3),
    }
    expected_gaps = {4: 0, 6: 0, 1: 3, 3: 3, 5: 3, 10: 0, 2: 2, 7: 3, 8: 0, 9: 3}
    actual_deltas = {
        q: (int(row["delta_p1"]), int(row["delta_p2"])) for q, row in by_q.items()
    }
    actual_gaps = {q: int(row["sensitivity_gap"]) for q, row in by_q.items()}
    if actual_deltas != expected_deltas or actual_gaps != expected_gaps:
        raise ValueError("Derived deltas or sensitivity gaps differ from expected values.")


def add_metadata(
    ax: plt.Axes,
    rows: list[dict[str, object]],
    *,
    bands: tuple[tuple[float, float], ...] = ((1.5, 5.5), (8.5, 9.5)),
    boundaries: tuple[float, ...] = (0.5, 1.5, 5.5, 8.5),
) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(9.5, -0.5)
    for low, high in bands:
        ax.axhspan(low, high, color=COLORS["band"], zorder=-2)
    for y in boundaries:
        ax.axhline(y, color=COLORS["line"], lw=0.65)
    ax.axis("off")
    header = dict(
        transform=ax.transAxes,
        va="bottom",
        fontsize=6.8,
        fontweight="bold",
        color=COLORS["text"],
    )
    ax.text(0.01, 1.055, "Question", ha="left", **header)
    ax.text(0.22, 1.055, "Perspective", ha="left", **header)
    ax.text(0.59, 1.055, "Operation", ha="left", **header)
    for y, row in enumerate(rows):
        ax.text(0.01, y, f"Q{row['q']}", ha="left", va="center", fontweight="bold", color=COLORS["text"])
        ax.text(0.22, y, str(row["perspective"]), ha="left", va="center", color=COLORS["text_mid"])
        ax.text(0.59, y, str(row["operation"]), ha="left", va="center", color=COLORS["text"])


def add_row_structure(
    ax: plt.Axes,
    *,
    bands: tuple[tuple[float, float], ...] = ((1.5, 5.5), (8.5, 9.5)),
    boundaries: tuple[float, ...] = (0.5, 1.5, 5.5, 8.5),
) -> None:
    ax.set_ylim(9.5, -0.5)
    for low, high in bands:
        ax.axhspan(low, high, color=COLORS["band"], zorder=-3)
    for y in boundaries:
        ax.axhline(y, color=COLORS["line"], lw=0.65, zorder=-1)


def plot_delta(rows: list[dict[str, object]]) -> plt.Figure:
    fig = plt.figure(figsize=(183 / 25.4, 115 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        1,
        2,
        width_ratios=[3.1, 2.2],
        left=0.045,
        right=0.965,
        bottom=0.17,
        top=0.82,
        wspace=0.08,
    )
    ax_meta = fig.add_subplot(grid[0, 0])
    ax = fig.add_subplot(grid[0, 1])
    add_metadata(ax_meta, rows)
    add_row_structure(ax)

    ax.set_xlim(-3.25, 0.25)
    ax.set_xticks([-3, -2, -1, 0])
    ax.set_yticks([])
    for x in (-3, -2, -1):
        ax.axvline(x, color=COLORS["line"], lw=0.55, zorder=-2)
    ax.axvline(0, color=COLORS["black"], lw=0.9, zorder=-1)

    for y, row in enumerate(rows):
        d1 = float(row["delta_p1"])
        d2 = float(row["delta_p2"])
        y1, y2 = y - 0.12, y + 0.12
        ax.plot([d1, d2], [y1, y2], color=COLORS["text_light"], lw=0.9, zorder=1)
        ax.scatter(d1, y1, s=31, marker="o", color=COLORS["blue_dark"], edgecolor=COLORS["white"], linewidth=0.6, zorder=3)
        ax.scatter(d2, y2, s=34, marker="D", color=COLORS["blue_soft"], edgecolor=COLORS["blue_dark"], linewidth=0.65, zorder=3)

    ax.set_xlabel("Change in correct runs relative to P0", fontsize=7, labelpad=6)
    ax.set_title("Loss relative to benchmark wording", loc="left", fontsize=7.6, fontweight="bold", pad=31, color=COLORS["text"])
    ax.text(0, 1.065, "negative values indicate fewer correct runs", transform=ax.transAxes, ha="left", va="bottom", fontsize=5.9, color=COLORS["text_light"])
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(COLORS["text_light"])
    ax.tick_params(axis="x", length=3, width=0.65, color=COLORS["text_light"])
    legend = [
        Line2D([0], [0], marker="o", linestyle="none", markersize=4.5, markerfacecolor=COLORS["blue_dark"], markeredgecolor=COLORS["blue_dark"], label="P1 − P0"),
        Line2D([0], [0], marker="D", linestyle="none", markersize=4.2, markerfacecolor=COLORS["blue_soft"], markeredgecolor=COLORS["blue_dark"], label="P2 − P0"),
    ]
    fig.legend(handles=legend, loc="lower right", bbox_to_anchor=(0.96, 0.045), ncol=2, fontsize=6.3, handletextpad=0.4, columnspacing=1.1)
    return fig


def _group_profiles(rows: list[dict[str, object]]) -> dict[tuple[int, int, int], list[int]]:
    profiles: dict[tuple[int, int, int], list[int]] = {}
    for row in rows:
        profile = tuple(int(row[key]) for key in ("p0", "p1", "p2"))
        profiles.setdefault(profile, []).append(int(row["q"]))
    return profiles


def plot_trajectory(rows: list[dict[str, object]]) -> plt.Figure:
    operations = ("Aggregate", "Compare", "Value", "Rank", "Explain")
    fig, axes = plt.subplots(
        5,
        1,
        figsize=(183 / 25.4, 115 / 25.4),
        sharex=True,
        sharey=True,
        gridspec_kw={"left": 0.20, "right": 0.93, "bottom": 0.14, "top": 0.84, "hspace": 0.28},
    )
    fig.patch.set_facecolor("white")
    unstable_colors = [COLORS["blue_dark"], COLORS["blue_mid"], COLORS["blue_soft"]]

    for facet_index, (ax, operation) in enumerate(zip(axes, operations, strict=True)):
        operation_rows = [row for row in rows if row["operation"] == operation]
        profiles = _group_profiles(operation_rows)
        ax.set_xlim(-0.08, 2.85)
        ax.set_ylim(-0.35, 3.35)
        ax.set_yticks([0, 1, 2, 3])
        ax.tick_params(axis="y", labelsize=6.1, length=2.5, width=0.55, color=COLORS["text_light"])
        ax.tick_params(axis="x", length=0)
        for y in (0, 1, 2, 3):
            ax.axhline(y, color=COLORS["line"], lw=0.5, zorder=-2)
        if facet_index % 2 == 1:
            ax.set_facecolor(COLORS["band"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.text(-0.12, 0.5, operation, transform=ax.transAxes, ha="right", va="center", fontsize=6.8, fontweight="bold", color=COLORS["text"])

        end_groups: dict[int, list[tuple[tuple[int, int, int], list[int]]]] = {}
        for profile, questions in profiles.items():
            end_groups.setdefault(profile[-1], []).append((profile, questions))

        unstable_index = 0
        for end_value, profile_group in sorted(end_groups.items(), reverse=True):
            n_group = len(profile_group)
            offsets = [0.0] if n_group == 1 else [(-0.16 + 0.32 * i / (n_group - 1)) for i in range(n_group)]
            for offset, (profile, questions) in zip(offsets, sorted(profile_group), strict=True):
                stable = profile[0] == profile[1] == profile[2]
                color = COLORS["black"] if stable else unstable_colors[unstable_index % len(unstable_colors)]
                if not stable:
                    unstable_index += 1
                ax.plot([0, 1, 2], profile, color=color, lw=1.35, marker="o", markersize=3.3, markeredgecolor=COLORS["white"], markeredgewidth=0.45, zorder=2)
                label_y = end_value + offset
                ax.plot([2.02, 2.11], [end_value, label_y], color=color, lw=0.6, zorder=2)
                ax.text(2.15, label_y, ", ".join(f"Q{q}" for q in sorted(questions)), ha="left", va="center", fontsize=6.0, color=color)

    axes[-1].set_xticks([0, 1, 2])
    axes[-1].set_xticklabels(["P0", "P1", "P2"], fontsize=6.8)
    axes[-1].tick_params(axis="x", labelbottom=True, pad=5)
    fig.text(0.47, 0.925, "Response profiles across wordings", ha="center", va="center", fontsize=7.8, fontweight="bold", color=COLORS["text"])
    fig.text(0.47, 0.885, "identical profiles are combined and directly labelled", ha="center", va="center", fontsize=5.9, color=COLORS["text_light"])
    fig.text(0.07, 0.49, "Correct runs / 3", rotation=90, ha="center", va="center", fontsize=7, color=COLORS["text"])
    legend = [
        Line2D([0], [0], color=COLORS["black"], marker="o", lw=1.2, markersize=3.3, label="stable profile"),
        Line2D([0], [0], color=COLORS["blue_dark"], marker="o", lw=1.2, markersize=3.3, label="sensitive profile"),
    ]
    fig.legend(handles=legend, loc="lower right", bbox_to_anchor=(0.94, 0.035), ncol=2, fontsize=6.2, handlelength=1.6, columnspacing=1.1)
    return fig


def plot_gap(rows: list[dict[str, object]]) -> plt.Figure:
    ordered = sorted(rows, key=lambda row: (-int(row["sensitivity_gap"]), int(row["display_order"])))
    fig = plt.figure(figsize=(183 / 25.4, 115 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        1,
        2,
        width_ratios=[3.1, 2.2],
        left=0.045,
        right=0.965,
        bottom=0.17,
        top=0.82,
        wspace=0.08,
    )
    ax_meta = fig.add_subplot(grid[0, 0])
    ax = fig.add_subplot(grid[0, 1])
    gap_bands = ((5.5, 9.5),)
    gap_boundaries = (4.5, 5.5)
    add_metadata(ax_meta, ordered, bands=gap_bands, boundaries=gap_boundaries)
    add_row_structure(ax, bands=gap_bands, boundaries=gap_boundaries)

    ax.set_xlim(-0.1, 3.45)
    ax.set_xticks([0, 1, 2, 3])
    ax.set_yticks([])
    for x in (0, 1, 2, 3):
        ax.axvline(x, color=COLORS["line"], lw=0.55, zorder=-2)
    for y, row in enumerate(ordered):
        gap = int(row["sensitivity_gap"])
        color = {0: COLORS["black"], 1: COLORS["blue_soft"], 2: COLORS["blue_mid"], 3: COLORS["blue_dark"]}[gap]
        ax.plot([0, gap], [y, y], color=color, lw=2.0, alpha=0.42 if gap else 1.0, zorder=1)
        ax.scatter(gap, y, s=35, color=color, edgecolor=COLORS["white"], linewidth=0.6, zorder=3)
        label = "0  stable" if gap == 0 else str(gap)
        ax.text(gap + 0.12, y, label, ha="left", va="center", fontsize=6.5, color=COLORS["text_mid"])

    ax.set_xlabel("Range of correct-run counts across P0–P2", fontsize=7, labelpad=6)
    ax.set_title("Sensitivity magnitude", loc="left", fontsize=7.6, fontweight="bold", pad=31, color=COLORS["text"])
    ax.text(0, 1.065, "gap = max(P0, P1, P2) − min(P0, P1, P2)", transform=ax.transAxes, ha="left", va="bottom", fontsize=5.9, color=COLORS["text_light"])
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(COLORS["text_light"])
    ax.tick_params(axis="x", length=3, width=0.65, color=COLORS["text_light"])
    return fig


def save_figure(fig: plt.Figure, stem: Path) -> None:
    fig.savefig(stem.with_suffix(".svg"), facecolor="white")
    fig.savefig(stem.with_suffix(".pdf"), facecolor="white")
    fig.savefig(stem.with_suffix(".tiff"), dpi=600, facecolor="white")
    fig.savefig(stem.with_suffix(".png"), dpi=300, facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    rows = derive_metrics(load_data(DATA_PATH))
    validate_expected_metrics(rows)
    if args.validate_only:
        print("PASS: 10 questions; deltas and gaps match expected values.")
        return
    outputs = (
        (plot_delta(rows), ROOT / "alternative_a_delta"),
        (plot_trajectory(rows), ROOT / "alternative_b_trajectory"),
        (plot_gap(rows), ROOT / "alternative_c_gap_lollipop"),
    )
    for fig, stem in outputs:
        save_figure(fig, stem)
    print("PASS: generated alternatives A, B, and C in SVG, PDF, TIFF, and PNG formats.")


if __name__ == "__main__":
    main()
