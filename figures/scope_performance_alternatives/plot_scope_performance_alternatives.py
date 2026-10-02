"""Nature-style alternatives for the scope-performance summary table."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from matplotlib.transforms import Bbox


mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
        "font.size": 7.2,
        "axes.titlesize": 9.0,
        "axes.labelsize": 7.5,
        "axes.linewidth": 0.7,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": False,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    }
)


METRIC_KEYS = (
    "strict_pass_rate",
    "numeric_accuracy",
    "status_accuracy",
)
EXPECTED_SCOPES = (
    "All questions",
    "Executable questions",
    "Boundary questions",
)

METRICS = (
    ("strict_pass_rate", "Strict pass rate", "o", "#0F4D92"),
    ("numeric_accuracy", "Numeric accuracy", "s", "#7884B4"),
    ("status_accuracy", "Status accuracy", "D", "#272727"),
)
SCOPE_LABELS = {
    "All questions": "All questions",
    "Executable questions": "Executable",
    "Boundary questions": "Boundary",
}
BLUE_CMAP = LinearSegmentedColormap.from_list(
    "scope_blue", ["#EEF2F7", "#B8CAE0", "#5E88B8", "#0F4D92"]
)
RATE_NORM = Normalize(vmin=70, vmax=100)
NEUTRAL_DARK = "#4D4D4D"
NEUTRAL_MID = "#8E8E8E"
NEUTRAL_LIGHT = "#E6E8EC"
BOUNDARY_BG = "#F5F6F8"


def parse_nullable(value: str) -> float | None:
    """Parse a percentage while retaining unreported scope metrics."""
    return None if value.strip().upper() in {"NA", "N/A", ""} else float(value)


def load_data(path: Path) -> list[dict]:
    """Load the shared source-data CSV into typed row dictionaries."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))

    return [
        {
            "scope": row["scope"],
            "cases": int(row["cases"]),
            **{key: parse_nullable(row[key]) for key in METRIC_KEYS},
        }
        for row in raw_rows
    ]


def validate_data(rows: list[dict]) -> None:
    """Reject data that could misstate nesting, rates, or N/A semantics."""
    if tuple(row["scope"] for row in rows) != EXPECTED_SCOPES:
        raise ValueError("Unexpected scope order")
    if rows[0]["cases"] != rows[1]["cases"] + rows[2]["cases"]:
        raise ValueError("All questions must equal executable plus boundary cases")
    if rows[2]["strict_pass_rate"] is None:
        raise ValueError("Boundary strict pass rate must be reported")
    if rows[2]["numeric_accuracy"] is not None:
        raise ValueError("Boundary numeric accuracy is not reported for this scope")

    for row in rows:
        for key in METRIC_KEYS:
            value = row[key]
            if value is not None and not 0 <= value <= 100:
                raise ValueError(f"{key} must fall between 0 and 100")


def _scope_ticklabels(rows: list[dict]) -> list[str]:
    return [f"{SCOPE_LABELS[row['scope']]}\n$n$={row['cases']}" for row in rows]


def _style_percent_axis(ax: plt.Axes) -> None:
    ax.set_xlim(70, 101.5)
    ax.set_xticks([70, 80, 90, 100])
    ax.set_xlabel("Accuracy or pass rate (%)")
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", length=3, color=NEUTRAL_MID)
    for value in (70, 80, 90, 100):
        ax.axvline(value, color="#DDDDDD", lw=0.55, zorder=0)


def draw_heatmap(ax: plt.Axes, rows: list[dict], compact: bool = False) -> None:
    """Draw an annotated scope-by-metric matrix with explicit N/A cells."""
    validate_data(rows)
    matrix = np.array(
        [[np.nan if row[key] is None else row[key] for key, *_ in METRICS] for row in rows],
        dtype=float,
    )
    masked = np.ma.masked_invalid(matrix)
    cmap = BLUE_CMAP.copy()
    cmap.set_bad("white")
    ax.imshow(masked, cmap=cmap, norm=RATE_NORM, aspect="auto", zorder=1)

    for y, row in enumerate(rows):
        for x, (key, _, _, _) in enumerate(METRICS):
            value = row[key]
            if value is None:
                patch = Rectangle(
                    (x - 0.48, y - 0.46),
                    0.96,
                    0.92,
                    facecolor="#F4F4F4",
                    edgecolor="#A8A8A8",
                    linewidth=0.6,
                    hatch="////",
                    zorder=2,
                )
                ax.add_patch(patch)
                ax.text(x, y, "N/A", ha="center", va="center", color="#777777", zorder=3)
            else:
                rgba = cmap(RATE_NORM(value))
                luminance = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
                color = "white" if luminance < 0.55 else "#202020"
                ax.text(
                    x,
                    y,
                    f"{value:.1f}%",
                    ha="center",
                    va="center",
                    color=color,
                    fontweight="bold",
                    zorder=3,
                )

    ax.set_xticks(range(3), [label for _, label, _, _ in METRICS])
    ax.xaxis.tick_top()
    ax.set_yticks(range(3), _scope_ticklabels(rows))
    ax.tick_params(axis="both", length=0, pad=5)
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(2.5, -0.5)
    for x in (-0.5, 0.5, 1.5, 2.5):
        ax.axvline(x, color="white", lw=2.2, zorder=4)
    for y in (-0.5, 0.5, 1.5, 2.5):
        ax.axhline(y, color="white", lw=2.2, zorder=4)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("Annotated performance heatmap", loc="left", pad=12 if not compact else 8, fontweight="bold")
    ax.text(
        1.0,
        -0.18 if not compact else -0.14,
        "Common colour scale: 70–100%; hatched cells are not reported",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=6.4 if not compact else 5.7,
        color=NEUTRAL_MID,
    )


def draw_lollipop(ax: plt.Axes, rows: list[dict], compact: bool = False) -> None:
    """Draw shape-coded lollipops on a common percentage scale."""
    validate_data(rows)
    y_base = np.array([2.0, 1.0, 0.0])
    offsets = (0.19, 0.0, -0.19)
    ax.axhspan(-0.45, 0.45, color=BOUNDARY_BG, zorder=-2)

    for (key, label, marker, color), offset in zip(METRICS, offsets):
        for y, row in zip(y_base, rows):
            value = row[key]
            if value is None:
                continue
            yy = y + offset
            ax.hlines(yy, 70, value, color=color, alpha=0.26, lw=1.8, zorder=1)
            ax.scatter(
                value,
                yy,
                s=34 if not compact else 24,
                marker=marker,
                color=color,
                edgecolor="white",
                linewidth=0.6,
                zorder=3,
            )
            ax.text(
                value + 0.8,
                yy,
                f"{value:.1f}%",
                ha="left",
                va="center",
                color=NEUTRAL_DARK,
                fontsize=6.8 if not compact else 5.8,
            )

    ax.text(
        70.8,
        0.00,
        "Numeric: N/A",
        color=NEUTRAL_MID,
        va="center",
        fontsize=6.4 if not compact else 5.5,
    )
    _style_percent_axis(ax)
    ax.set_ylim(-0.5, 2.5)
    ax.set_yticks(y_base, _scope_ticklabels(rows))
    handles = [
        Line2D([0], [0], marker=marker, color="none", markerfacecolor=color, markeredgecolor="white", markersize=5.5, label=label)
        for _, label, marker, color in METRICS
    ]
    ax.legend(
        handles=handles,
        ncol=3,
        loc="lower left",
        bbox_to_anchor=(0, 1.01),
        borderaxespad=0,
        handletextpad=0.35,
        columnspacing=1.0,
        fontsize=6.4 if not compact else 5.6,
    )
    ax.set_title("Scope-faceted lollipop comparison", loc="left", pad=28 if not compact else 24, fontweight="bold")


def _rounded_bar(ax: plt.Axes, x: float, y: float, width: float, value: float, color: str) -> None:
    height = 0.042
    radius = height / 2
    background = FancyBboxPatch(
        (x, y - height / 2),
        width,
        height,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=NEUTRAL_LIGHT,
        edgecolor="none",
        transform=ax.transAxes,
    )
    foreground = FancyBboxPatch(
        (x, y - height / 2),
        width * value / 100,
        height,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=color,
        edgecolor="none",
        transform=ax.transAxes,
    )
    ax.add_patch(background)
    ax.add_patch(foreground)


def draw_unit_chart(ax: plt.Axes, rows: list[dict], compact: bool = False) -> None:
    """Draw a 150-unit case composition with compact metric summaries."""
    validate_data(rows)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_title("Case composition with applicable performance", loc="left", pad=8, fontweight="bold")

    composition_header = "Cases ($n$=150)" if compact else "Evaluation-set composition"
    performance_header = "Performance" if compact else "Applicable performance"
    ax.text(0.02, 0.91, composition_header, fontweight="bold", transform=ax.transAxes)
    ax.text(0.54, 0.91, performance_header, fontweight="bold", transform=ax.transAxes)

    left_x0, left_y0 = 0.025, 0.12
    dx, dy = 0.028, 0.062
    for idx in range(150):
        row_index = idx // 15
        col_index = idx % 15
        color = "#555555" if idx < 90 else "#CFCFCF"
        marker = Rectangle(
            (left_x0 + col_index * dx, left_y0 + (9 - row_index) * dy),
            0.020,
            0.036,
            transform=ax.transAxes,
            facecolor=color,
            edgecolor="white",
            linewidth=0.25,
        )
        ax.add_patch(marker)

    ax.text(0.025, 0.77, "90 executable (60%)", color="#333333", transform=ax.transAxes, fontsize=6.6)
    ax.text(0.025, 0.07, "60 boundary (40%)", color="#777777", transform=ax.transAxes, fontsize=6.6)
    if not compact:
        ax.text(0.43, 0.07, "All $n$=150", ha="right", transform=ax.transAxes, color=NEUTRAL_MID, fontsize=6.6)

    y_positions = [0.76, 0.68, 0.60, 0.45, 0.37, 0.29, 0.14, 0.06]
    entries = [
        ("All · strict", rows[0]["strict_pass_rate"], METRICS[0][3]),
        ("All · numeric", rows[0]["numeric_accuracy"], METRICS[1][3]),
        ("All · status", rows[0]["status_accuracy"], METRICS[2][3]),
        ("Executable · strict", rows[1]["strict_pass_rate"], METRICS[0][3]),
        ("Executable · numeric", rows[1]["numeric_accuracy"], METRICS[1][3]),
        ("Executable · status", rows[1]["status_accuracy"], METRICS[2][3]),
        ("Boundary · strict", rows[2]["strict_pass_rate"], METRICS[0][3]),
        ("Boundary · status", rows[2]["status_accuracy"], METRICS[2][3]),
    ]
    for y, (label, value, color) in zip(y_positions, entries):
        ax.text(0.54, y + 0.027, label, transform=ax.transAxes, fontsize=6.1 if not compact else 5.3, color=NEUTRAL_DARK)
        _rounded_bar(ax, 0.54, y, 0.32, value, color)
        ax.text(0.88, y, f"{value:.1f}%", transform=ax.transAxes, va="center", fontsize=6.5 if not compact else 5.5, color=NEUTRAL_DARK)
    note_y = 0.215
    ax.text(0.54, note_y, "Boundary numeric: N/A (not reported)", transform=ax.transAxes, color=NEUTRAL_MID, fontsize=6.0 if not compact else 5.2)


def draw_bubble_matrix(
    ax: plt.Axes,
    rows: list[dict],
    compact: bool = False,
    show_title: bool = True,
    show_note: bool = True,
) -> None:
    """Draw fixed-size circular cells whose colour intensity encodes rate."""
    validate_data(rows)
    ax.set_aspect("equal")
    radius = 0.31
    x_positions = np.arange(len(METRICS), dtype=float) * 1.3
    for y, row in enumerate(reversed(rows)):
        for x, (key, _, _, _) in zip(x_positions, METRICS):
            value = row[key]
            if value is None:
                circle = Circle(
                    (x, y),
                    radius,
                    facecolor="#F5F5F5",
                    edgecolor="#9B9B9B",
                    linewidth=0.7,
                    hatch="////",
                )
                text_color = "#777777"
                label = "N/A"
            else:
                rgba = BLUE_CMAP(RATE_NORM(value))
                circle = Circle((x, y), radius, facecolor=rgba, edgecolor="white", linewidth=0.8)
                luminance = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
                text_color = "white" if luminance < 0.55 else "#202020"
                label = f"{value:.1f}%"
            ax.add_patch(circle)
            ax.text(x, y, label, ha="center", va="center", color=text_color, fontweight="bold", fontsize=6.7 if not compact else 5.6)

    ordered_rows = list(reversed(rows))
    ax.set_yticks(range(3), _scope_ticklabels(ordered_rows))
    metric_ticklabels = (
        [label for _, label, _, _ in METRICS]
        if not compact
        else ["Strict pass\nrate", "Numeric\naccuracy", "Status\naccuracy"]
    )
    ax.set_xticks(x_positions, metric_ticklabels)
    ax.xaxis.tick_top()
    ax.tick_params(axis="both", length=0, pad=5)
    ax.set_xlim(-0.55, 3.15)
    ax.set_ylim(-0.55, 2.55)
    for spine in ax.spines.values():
        spine.set_visible(False)
    if show_title:
        ax.set_title("Scope × metric bubble matrix", loc="left", pad=12 if not compact else 26, fontweight="bold")
    if show_note:
        ax.text(
            1.0,
            -0.14,
            "Circle size is fixed; colour intensity supports the printed value",
            transform=ax.transAxes,
            ha="right",
            va="top",
            color=NEUTRAL_MID,
            fontsize=6.2 if not compact else 5.3,
        )


DRAWERS = {
    "heatmap": draw_heatmap,
    "lollipop": draw_lollipop,
    "unit": draw_unit_chart,
    "bubble": draw_bubble_matrix,
}


def make_standalone(kind: str, rows: list[dict]) -> plt.Figure:
    """Create one double-column-width standalone alternative."""
    if kind not in DRAWERS:
        raise ValueError(f"Unknown figure kind: {kind}")
    fig, ax = plt.subplots(figsize=(183 / 25.4, 92 / 25.4))
    DRAWERS[kind](ax, rows)
    margins = {
        "heatmap": dict(left=0.24, right=0.97, top=0.78, bottom=0.23),
        "lollipop": dict(left=0.22, right=0.97, top=0.72, bottom=0.19),
        "unit": dict(left=0.05, right=0.98, top=0.86, bottom=0.08),
        "bubble": dict(left=0.23, right=0.96, top=0.76, bottom=0.20),
    }
    fig.subplots_adjust(**margins[kind])
    return fig


def make_bubble_matrix_only(rows: list[dict]) -> plt.Figure:
    """Create a tightly framed bubble matrix without title or footer."""
    fig, ax = plt.subplots(figsize=(183 / 25.4, 105 / 25.4))
    draw_bubble_matrix(ax, rows, compact=False, show_title=False, show_note=False)
    fig.subplots_adjust(left=0.23, right=0.95, top=0.83, bottom=0.08)
    return fig


def make_comparison_sheet(rows: list[dict]) -> plt.Figure:
    """Place all four alternatives on one selection sheet."""
    fig, axes = plt.subplots(2, 2, figsize=(183 / 25.4, 190 / 25.4))
    for panel, ax, (kind, drawer) in zip("abcd", axes.flat, DRAWERS.items()):
        drawer(ax, rows, compact=True)
        ax.text(
            -0.16,
            1.10,
            panel,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontweight="bold",
            fontsize=8.2,
        )
    fig.suptitle(
        "Alternative representations of scope-level performance",
        x=0.12,
        y=0.985,
        ha="left",
        fontweight="bold",
        fontsize=10,
    )
    fig.subplots_adjust(left=0.14, right=0.98, top=0.91, bottom=0.07, wspace=0.52, hspace=0.65)
    return fig


def _content_bbox_inches(fig: plt.Figure, pad_inches: float = 0.05) -> Bbox:
    """Return the union of visible data/text artists, excluding empty axes boxes."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    boxes = []
    for ax in fig.axes:
        artists = (
            list(ax.texts)
            + list(ax.get_xticklabels())
            + list(ax.get_yticklabels())
            + list(ax.patches)
            + list(ax.collections)
            + list(ax.lines)
            + list(ax.images)
        )
        title_artists = [ax.title, ax._left_title, ax._right_title]
        artists.extend(title for title in title_artists if title.get_text())
        for artist in artists:
            if not artist.get_visible():
                continue
            box = artist.get_window_extent(renderer=renderer)
            if np.isfinite(box.extents).all() and box.width > 0 and box.height > 0:
                boxes.append(box)
    if not boxes:
        raise ValueError("Cannot tightly crop a figure with no visible artists")
    content = Bbox.union(boxes).transformed(fig.dpi_scale_trans.inverted())
    return Bbox.from_extents(
        content.x0 - pad_inches,
        content.y0 - pad_inches,
        content.x1 + pad_inches,
        content.y1 + pad_inches,
    )


def save_figure(
    fig: plt.Figure,
    base_path: Path,
    include_tiff: bool = True,
    tight: bool = False,
) -> list[Path]:
    """Save editable and raster publication formats without changing layout."""
    base_path = Path(base_path)
    base_path.parent.mkdir(parents=True, exist_ok=True)
    formats: list[tuple[str, int | None]] = [
        (".svg", None),
        (".pdf", None),
        (".png", 300),
    ]
    if include_tiff:
        formats.append((".tiff", 600))

    content_bbox = _content_bbox_inches(fig) if tight else None
    paths: list[Path] = []
    for suffix, dpi in formats:
        path = base_path.with_suffix(suffix)
        kwargs = {"facecolor": "white"}
        if tight:
            kwargs["bbox_inches"] = content_bbox
        if dpi is not None:
            kwargs["dpi"] = dpi
        fig.savefig(path, **kwargs)
        paths.append(path)
    return paths


def main() -> None:
    """Render every alternative and the combined comparison sheet."""
    script_dir = Path(__file__).resolve().parent
    source_path = script_dir.parent / "scope_performance" / "scope_performance_source_data.csv"
    output_dir = script_dir / "outputs"
    rows = load_data(source_path)
    validate_data(rows)

    for kind in DRAWERS:
        fig = make_standalone(kind, rows)
        save_figure(fig, output_dir / f"scope_performance_{kind}", include_tiff=True)
        plt.close(fig)

    sheet = make_comparison_sheet(rows)
    save_figure(sheet, output_dir / "scope_performance_alternatives_comparison", include_tiff=False)
    plt.close(sheet)

    matrix_only = make_bubble_matrix_only(rows)
    save_figure(
        matrix_only,
        output_dir / "scope_performance_bubble_matrix_only",
        include_tiff=True,
        tight=True,
    )
    plt.close(matrix_only)


if __name__ == "__main__":
    main()
