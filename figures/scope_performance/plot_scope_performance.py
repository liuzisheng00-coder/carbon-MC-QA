"""Plot evaluation-set composition and performance by nested question scope."""

from __future__ import annotations

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
DATA_PATH = ROOT / "scope_performance_source_data.csv"
OUTPUT_STEM = ROOT / "scope_aware_performance"

COLORS = {
    "strict": "#0F4D92",
    "numeric": "#7884B4",
    "status": "#272727",
    "composition_exec": "#4D4D4D",
    "composition_boundary": "#D8D8D8",
    "text": "#272727",
    "text_mid": "#5F5F5F",
    "text_light": "#969696",
    "line": "#DDDDDD",
    "band": "#F6F7FA",
    "white": "#FFFFFF",
}

METRICS = (
    ("strict_pass_rate", "Strict pass rate", "o", COLORS["strict"], 0.17),
    ("numeric_accuracy", "Numeric accuracy", "s", COLORS["numeric"], 0.00),
    ("status_accuracy", "Status accuracy", "D", COLORS["status"], -0.17),
)


def parse_optional_percent(value: str) -> float | None:
    cleaned = value.strip()
    if cleaned.upper() in {"NA", "N/A", "NOT APPLICABLE", ""}:
        return None
    return float(cleaned.rstrip("%"))


def load_data(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for source in csv.DictReader(handle):
            rows.append(
                {
                    "scope": source["scope"],
                    "cases": int(source["cases"]),
                    "strict_pass_rate": parse_optional_percent(source["strict_pass_rate"]),
                    "numeric_accuracy": parse_optional_percent(source["numeric_accuracy"]),
                    "status_accuracy": parse_optional_percent(source["status_accuracy"]),
                    "display_order": int(source["display_order"]),
                }
            )
    return sorted(rows, key=lambda row: int(row["display_order"]))


def validate_data(rows: list[dict[str, object]]) -> None:
    expected_scopes = {"All questions", "Executable questions", "Boundary questions"}
    if {str(row["scope"]) for row in rows} != expected_scopes:
        raise ValueError("Expected All, Executable, and Boundary question scopes.")
    by_scope = {str(row["scope"]): row for row in rows}
    all_cases = int(by_scope["All questions"]["cases"])
    executable_cases = int(by_scope["Executable questions"]["cases"])
    boundary_cases = int(by_scope["Boundary questions"]["cases"])
    if all_cases != executable_cases + boundary_cases:
        raise ValueError("All-question cases must equal executable plus boundary cases.")
    boundary = by_scope["Boundary questions"]
    if boundary["strict_pass_rate"] is None:
        raise ValueError("Boundary strict pass rate must be reported.")
    if boundary["numeric_accuracy"] is not None:
        raise ValueError("Boundary numeric accuracy is not reported for this scope.")
    for row in rows:
        for metric in ("strict_pass_rate", "numeric_accuracy", "status_accuracy"):
            value = row[metric]
            if value is not None and not 0 <= float(value) <= 100:
                raise ValueError(f"{metric} must lie in [0, 100]: {row}")


def add_panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(
        -0.08,
        1.12,
        label,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=8,
        fontweight="bold",
        color=COLORS["text"],
    )


def make_figure(rows: list[dict[str, object]]) -> plt.Figure:
    validate_data(rows)
    by_scope = {str(row["scope"]): row for row in rows}
    executable_cases = int(by_scope["Executable questions"]["cases"])
    boundary_cases = int(by_scope["Boundary questions"]["cases"])
    all_cases = int(by_scope["All questions"]["cases"])

    fig = plt.figure(figsize=(183 / 25.4, 105 / 25.4), facecolor="white")
    grid = fig.add_gridspec(
        2,
        1,
        height_ratios=[0.85, 2.35],
        left=0.24,
        right=0.96,
        bottom=0.19,
        top=0.84,
        hspace=0.72,
    )
    ax_comp = fig.add_subplot(grid[0, 0])
    ax_metric = fig.add_subplot(grid[1, 0])

    # Panel a: the parent scope and its two mutually exclusive components.
    ax_comp.barh(0, executable_cases, height=0.48, color=COLORS["composition_exec"], edgecolor="none")
    ax_comp.barh(
        0,
        boundary_cases,
        left=executable_cases,
        height=0.48,
        color=COLORS["composition_boundary"],
        edgecolor="none",
    )
    ax_comp.text(
        executable_cases / 2,
        0,
        f"Executable  {executable_cases}  ({100 * executable_cases / all_cases:.0f}%)",
        ha="center",
        va="center",
        fontsize=6.7,
        fontweight="bold",
        color=COLORS["white"],
    )
    ax_comp.text(
        executable_cases + boundary_cases / 2,
        0,
        f"Boundary  {boundary_cases}  ({100 * boundary_cases / all_cases:.0f}%)",
        ha="center",
        va="center",
        fontsize=6.7,
        fontweight="bold",
        color=COLORS["text"],
    )
    ax_comp.set_xlim(0, all_cases)
    ax_comp.set_ylim(-0.55, 0.55)
    ax_comp.set_title("Evaluation-set composition", loc="left", fontsize=7.5, fontweight="bold", pad=13, color=COLORS["text"])
    ax_comp.text(
        1,
        1.16,
        f"All questions  n={all_cases}",
        transform=ax_comp.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.2,
        color=COLORS["text_mid"],
    )
    ax_comp.axis("off")
    add_panel_label(ax_comp, "a")

    # Panel b: one common scale, with structural missingness shown explicitly.
    y_positions = {"All questions": 2, "Executable questions": 1, "Boundary questions": 0}
    ax_metric.axhspan(-0.38, 0.38, color=COLORS["band"], zorder=-3)
    for x in (70, 80, 90, 100):
        ax_metric.axvline(x, color=COLORS["line"], lw=0.6, zorder=-2)
    for row in rows:
        scope = str(row["scope"])
        y = y_positions[scope]
        for metric, _, marker, color, offset in METRICS:
            value = row[metric]
            current_y = y + offset
            if value is None:
                ax_metric.plot([70.8, 71.6], [current_y, current_y], color=COLORS["text_light"], lw=1.0)
                ax_metric.text(72.0, current_y, "N/A", ha="left", va="center", fontsize=6.2, color=COLORS["text_light"])
                continue
            numeric_value = float(value)
            ax_metric.scatter(
                numeric_value,
                current_y,
                s=34,
                marker=marker,
                facecolor=color,
                edgecolor=COLORS["white"],
                linewidth=0.65,
                zorder=3,
            )
            ax_metric.text(
                numeric_value + 0.8,
                current_y,
                f"{numeric_value:.1f}%",
                ha="left",
                va="center",
                fontsize=6.4,
                color=COLORS["text_mid"],
            )

    labels = []
    for scope in ("Boundary questions", "Executable questions", "All questions"):
        labels.append(f"{scope}\n$n$={by_scope[scope]['cases']}")
    ax_metric.set_yticks([0, 1, 2])
    ax_metric.set_yticklabels(labels, fontsize=6.8, linespacing=1.35)
    ax_metric.tick_params(axis="y", length=0, pad=9)
    ax_metric.set_xlim(70, 101.5)
    ax_metric.set_ylim(-0.45, 2.45)
    ax_metric.set_xticks([70, 80, 90, 100])
    ax_metric.set_xticklabels(["70", "80", "90", "100"], fontsize=6.5)
    ax_metric.set_xlabel("Accuracy or pass rate (%)", fontsize=7, labelpad=6)
    ax_metric.set_title("Performance by evaluation scope", loc="left", fontsize=7.5, fontweight="bold", pad=30, color=COLORS["text"])
    ax_metric.text(
        0,
        1.07,
        "Boundary: strict pass and status; numeric not reported",
        transform=ax_metric.transAxes,
        ha="left",
        va="bottom",
        fontsize=5.9,
        color=COLORS["text_light"],
    )
    ax_metric.spines["left"].set_visible(False)
    ax_metric.spines["bottom"].set_color(COLORS["text_light"])
    ax_metric.tick_params(axis="x", length=3, width=0.65, color=COLORS["text_light"])
    add_panel_label(ax_metric, "b")

    handles = [
        Line2D([0], [0], marker=marker, linestyle="none", markersize=4.6, markerfacecolor=color, markeredgecolor=color, label=label)
        for _, label, marker, color, _ in METRICS
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.60, 0.035),
        ncol=3,
        fontsize=6.2,
        handletextpad=0.4,
        columnspacing=1.1,
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
    fig = make_figure(rows)
    save_outputs(fig)
    plt.close(fig)


if __name__ == "__main__":
    main()
