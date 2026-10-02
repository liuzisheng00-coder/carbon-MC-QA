"""Plot phrasing sensitivity as a rate-and-unit quantitative grid."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


# Publication font and editable-vector requirements.
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
OUTPUT_STEM = ROOT / "phrasing_sensitivity_rate_unit"

COLORS = {
    "operation": "#0F4D92",
    "perspective": "#7884B4",
    "neutral_dark": "#4D4D4D",
    "neutral_mid": "#8C8C8C",
    "neutral_light": "#D8D8D8",
    "guide": "#E7E7E7",
    "band": "#F6F7FA",
    "white": "#FFFFFF",
}


def load_data(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
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
    return sorted(rows, key=lambda item: item["display_order"])


def validate_data(rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError("Source data are empty.")
    for row in rows:
        n = int(row["questions"])
        stable = int(row["fully_stable_questions"])
        rate = float(row["pass_rate_pct"])
        if n < 1:
            raise ValueError(f"Question count must be positive: {row}")
        if not 0 <= stable <= n:
            raise ValueError(f"Stable count must lie in [0, n]: {row}")
        if not 0 <= rate <= 100:
            raise ValueError(f"Pass rate must lie in [0, 100]: {row}")


def make_figure(rows: list[dict[str, object]]) -> plt.Figure:
    # 183 mm x 96 mm: double-column width, compact robustness figure.
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
    ax_units = fig.add_subplot(grid[0, 1], sharey=ax_rate)

    y_positions = [8.0, 7.0, 6.0, 5.0, 4.0, 2.35, 1.35, 0.35]
    y_lookup = {
        str(row["category"]): y for row, y in zip(rows, y_positions, strict=True)
    }

    # A restrained band separates the supporting perspective block.
    for axis in (ax_rate, ax_units):
        axis.axhspan(-0.20, 2.85, color=COLORS["band"], zorder=0)
        axis.axhline(3.35, color=COLORS["neutral_light"], lw=0.7, zorder=0)

    for row in rows:
        category = str(row["category"])
        group = str(row["group"])
        y = y_lookup[category]
        rate = float(row["pass_rate_pct"])
        n = int(row["questions"])
        stable = int(row["fully_stable_questions"])
        color = COLORS["operation"] if group == "Operation" else COLORS["perspective"]

        # Hero evidence: a lollipop makes the common 0-100 scale immediately visible.
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
        if rate >= 94:
            ax_rate.text(
                rate - 2.7,
                y,
                f"{rate:.0f}%",
                ha="right",
                va="center",
                color=COLORS["neutral_dark"],
                fontsize=6.7,
            )
        else:
            ax_rate.text(
                rate + 2.7,
                y,
                f"{rate:.0f}%",
                ha="left",
                va="center",
                color=COLORS["neutral_dark"],
                fontsize=6.7,
            )

        # Validation evidence: one square per question preserves the small denominators.
        for question_index in range(n):
            is_stable = question_index < stable
            ax_units.scatter(
                question_index + 1,
                y,
                s=30,
                marker="s",
                facecolor=color if is_stable else COLORS["white"],
                edgecolor=color if is_stable else COLORS["neutral_mid"],
                linewidth=0.75,
                zorder=3,
            )
        ax_units.text(
            6.0,
            y,
            f"{stable}/{n}",
            ha="left",
            va="center",
            color=COLORS["neutral_dark"],
            fontsize=6.7,
        )

    labels = [f"{row['category']}   $n$={row['questions']}" for row in rows]
    ax_rate.set_yticks(y_positions)
    ax_rate.set_yticklabels(labels, fontsize=7)
    ax_rate.tick_params(axis="y", length=0, pad=6)
    ax_rate.tick_params(axis="x", width=0.7, length=3, color=COLORS["neutral_mid"])
    ax_rate.set_xlim(0, 110)
    ax_rate.set_xticks([0, 25, 50, 75, 100])
    ax_rate.set_xticklabels(["0", "25", "50", "75", "100"])
    ax_rate.set_xlabel("Pass rate (%)", fontsize=7, labelpad=5)
    ax_rate.set_ylim(-0.35, 8.75)
    ax_rate.spines["left"].set_visible(False)
    ax_rate.spines["bottom"].set_color(COLORS["neutral_mid"])

    ax_units.set_xlim(0.3, 7.05)
    ax_units.set_xticks([])
    ax_units.tick_params(axis="y", left=False, labelleft=False)
    for spine in ax_units.spines.values():
        spine.set_visible(False)

    # Direct headers keep the figure legend-free.
    ax_rate.text(
        0.0,
        1.055,
        "Pass rate",
        transform=ax_rate.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.4,
        fontweight="bold",
        color=COLORS["neutral_dark"],
    )
    ax_units.text(
        0.0,
        1.055,
        "Cross-phrasing stability",
        transform=ax_units.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.4,
        fontweight="bold",
        color=COLORS["neutral_dark"],
    )
    ax_units.text(
        0.0,
        1.005,
        "one square per question",
        transform=ax_units.transAxes,
        ha="left",
        va="bottom",
        fontsize=5.8,
        color=COLORS["neutral_mid"],
    )

    # Group labels live in the margin to preserve category alignment.
    ax_rate.text(
        -0.47,
        8.60,
        "OPERATION",
        transform=ax_rate.get_yaxis_transform(),
        ha="left",
        va="center",
        fontsize=6.2,
        fontweight="bold",
        color=COLORS["operation"],
        clip_on=False,
    )
    ax_rate.text(
        -0.47,
        2.82,
        "PERSPECTIVE",
        transform=ax_rate.get_yaxis_transform(),
        ha="left",
        va="center",
        fontsize=6.2,
        fontweight="bold",
        color=COLORS["perspective"],
        clip_on=False,
    )

    # A compact key explains the only non-obvious visual encoding.
    key_handles = [
        Line2D(
            [0],
            [0],
            marker="s",
            linestyle="none",
            markersize=4.5,
            markerfacecolor=COLORS["operation"],
            markeredgecolor=COLORS["operation"],
            label="fully stable",
        ),
        Line2D(
            [0],
            [0],
            marker="s",
            linestyle="none",
            markersize=4.5,
            markerfacecolor=COLORS["white"],
            markeredgecolor=COLORS["neutral_mid"],
            label="not fully stable",
        ),
    ]
    fig.legend(
        handles=key_handles,
        loc="lower right",
        bbox_to_anchor=(0.974, 0.025),
        ncol=2,
        handletextpad=0.35,
        columnspacing=1.0,
        fontsize=6.1,
    )

    return fig


def save_outputs(fig: plt.Figure) -> None:
    OUTPUT_STEM.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_STEM.with_suffix(".svg"), facecolor="white")
    fig.savefig(OUTPUT_STEM.with_suffix(".pdf"), facecolor="white")
    fig.savefig(OUTPUT_STEM.with_suffix(".tiff"), dpi=600, facecolor="white")
    fig.savefig(OUTPUT_STEM.with_suffix(".png"), dpi=300, facecolor="white")


def main() -> None:
    rows = load_data(DATA_PATH)
    validate_data(rows)
    figure = make_figure(rows)
    save_outputs(figure)
    plt.close(figure)


if __name__ == "__main__":
    main()
