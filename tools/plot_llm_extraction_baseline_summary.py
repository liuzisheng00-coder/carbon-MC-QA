"""Create a publication-ready summary figure for LLM extraction quality."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np


# Nature-style editable typography settings.
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["font.size"] = 7
plt.rcParams["axes.linewidth"] = 0.8
plt.rcParams["axes.spines.top"] = False
plt.rcParams["axes.spines.right"] = False
plt.rcParams["legend.frameon"] = False


METRICS = [
    ("class_ok", "IFC class"),
    ("name_ok", "Element name"),
    ("materials_ok", "Materials"),
    ("volume_ok", "Volume"),
    ("all_fields_ok", "All fields"),
]


def load_summary(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", [])
    if not results:
        raise ValueError("No results were found in the summary.")

    sample_sizes = [int(item["n"]) for item in results]
    matrix = np.array(
        [[float(item["aggregate"][key]) for item in results] for key, _ in METRICS],
        dtype=float,
    )
    if not np.isfinite(matrix).all() or np.any((matrix < 0) | (matrix > 1)):
        raise ValueError("Aggregate accuracy values must be finite and within [0, 1].")
    return data, sample_sizes, matrix


def write_source_data(path: Path, sample_sizes, matrix):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric"] + [f"accuracy_n_{n}" for n in sample_sizes] + ["range_percentage_points"])
        for (_, label), values in zip(METRICS, matrix):
            writer.writerow([label] + [f"{value:.8f}" for value in values] + [f"{np.ptp(values) * 100:.4f}"])


def add_panel_label(ax, label):
    ax.text(-0.16, 1.08, label, transform=ax.transAxes, fontsize=8, fontweight="bold", va="bottom")


def make_accuracy_colormap(palette="blue"):
    if palette == "red":
        colors = ["#FAECEA", "#F3D0CC", "#E7A29C", "#C85D57", "#8F302D"]
    elif palette == "blue":
        colors = ["#E9B9B5", "#F4E1DF", "#E3EAF3", "#8FB3D9", "#0F4D92"]
    else:
        raise ValueError(f"Unknown palette: {palette}")
    return mcolors.LinearSegmentedColormap.from_list(f"accuracy_{palette}", colors)


def sort_metrics_by_mean(matrix):
    """Sort rows by descending mean accuracy, preserving source order for ties."""
    labels = np.array([label for _, label in METRICS], dtype=object)
    order = np.argsort(-np.mean(matrix, axis=1), kind="stable")
    return labels[order].tolist(), matrix[order]


def create_figure(sample_sizes, matrix):
    labels = [label for _, label in METRICS]
    ranges = np.ptp(matrix, axis=1) * 100
    y = np.arange(len(labels))

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "accuracy",
        ["#E9B9B5", "#F4E1DF", "#E3EAF3", "#8FB3D9", "#0F4D92"],
    )
    norm = mcolors.Normalize(vmin=0.55, vmax=1.0)

    fig = plt.figure(figsize=(7.20, 3.95), facecolor="white")
    grid = fig.add_gridspec(
        1,
        2,
        width_ratios=[2.7, 1.15],
        left=0.16,
        right=0.975,
        bottom=0.24,
        top=0.78,
        wspace=0.43,
    )
    ax_heat = fig.add_subplot(grid[0, 0])
    ax_range = fig.add_subplot(grid[0, 1], sharey=ax_heat)

    fig.text(
        0.055,
        0.955,
        "Structured fields remain near-perfect, while name extraction limits completeness",
        fontsize=9.3,
        fontweight="bold",
        ha="left",
        va="top",
    )
    fig.text(
        0.055,
        0.902,
        "Aggregate extraction accuracy across three stratified evaluation sizes",
        fontsize=7,
        color="#606060",
        ha="left",
        va="top",
    )

    image = ax_heat.imshow(matrix, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
    ax_heat.set_xticks(np.arange(len(sample_sizes)), [f"n = {n}" for n in sample_sizes])
    ax_heat.xaxis.tick_top()
    ax_heat.tick_params(axis="x", length=0, pad=5)
    ax_heat.set_yticks(y, labels)
    ax_heat.tick_params(axis="y", length=0, pad=7)
    ax_heat.set_frame_on(False)
    ax_heat.axhline(3.5, color="white", linewidth=2.2)

    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            red, green, blue, _ = cmap(norm(value))
            luminance = 0.299 * red + 0.587 * green + 0.114 * blue
            text_color = "white" if luminance < 0.56 else "#272727"
            ax_heat.text(
                column,
                row,
                f"{value * 100:.1f}",
                ha="center",
                va="center",
                color=text_color,
                fontsize=7.2,
                fontweight="bold",
            )

    colorbar_ax = ax_heat.inset_axes([0.0, -0.16, 1.0, 0.055])
    colorbar = fig.colorbar(image, cax=colorbar_ax, orientation="horizontal")
    colorbar.set_ticks([0.6, 0.8, 1.0], labels=["60", "80", "100"])
    colorbar.set_label("Accuracy (%)", labelpad=3)
    colorbar.outline.set_visible(False)
    colorbar.ax.tick_params(length=0, pad=2, labelsize=6.2)
    add_panel_label(ax_heat, "a")

    ax_range.hlines(y, 0, ranges, color="#D8D8D8", linewidth=4.5, zorder=1)
    ax_range.scatter(ranges, y, s=34, color="#9A4D8E", edgecolor="white", linewidth=0.7, zorder=2)
    for yi, value in zip(y, ranges):
        ax_range.text(
            value + 0.22,
            yi,
            f"{value:.1f}",
            va="center",
            ha="left",
            fontsize=6.6,
            color="#4D4D4D",
        )
    ax_range.set_xlim(-0.25, 6.3)
    ax_range.set_xticks([0, 2, 4, 6])
    ax_range.set_xlabel("Range across n (percentage points)\nLower indicates greater stability", labelpad=6)
    ax_range.set_title("Sensitivity to sample size", fontsize=7.2, loc="left", pad=8, fontweight="bold")
    ax_range.tick_params(axis="y", left=False, labelleft=False)
    ax_range.tick_params(axis="x", length=3, width=0.7, color="#4D4D4D")
    ax_range.spines["left"].set_visible(False)
    ax_range.spines["bottom"].set_color("#767676")
    ax_range.axvline(0, color="#A8A8A8", linewidth=0.8, zorder=0)
    add_panel_label(ax_range, "b")

    fig.text(
        0.055,
        0.055,
        "Values are single aggregate estimates for each n; uncertainty intervals were not available.",
        fontsize=6.2,
        color="#767676",
        ha="left",
    )
    return fig


def create_single_panel_figure(sample_sizes, matrix, palette="blue", show_context=True, sort_descending=False):
    """Create one annotated heatmap that carries the full statistical message."""
    if sort_descending:
        labels, matrix = sort_metrics_by_mean(matrix)
    else:
        labels = [label for _, label in METRICS]
    cmap = make_accuracy_colormap(palette)
    norm = mcolors.Normalize(vmin=0.55, vmax=1.0)

    fig = plt.figure(figsize=(6.25, 4.05 if show_context else 3.25), facecolor="white")
    ax = fig.add_axes([0.25, 0.25, 0.70, 0.49 if show_context else 0.60])

    if show_context:
        fig.text(
            0.055,
            0.955,
            "Name extraction limits complete-record accuracy",
            fontsize=9.5,
            fontweight="bold",
            ha="left",
            va="top",
        )
        fig.text(
            0.055,
            0.900,
            "Aggregate LLM extraction accuracy across stratified evaluation sizes",
            fontsize=7,
            color="#606060",
            ha="left",
            va="top",
        )

    image = ax.imshow(matrix, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
    ax.set_xticks(np.arange(len(sample_sizes)), [f"n = {n}" for n in sample_sizes])
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_yticks(np.arange(len(labels)), labels)
    ax.tick_params(axis="y", length=0, pad=8)
    ax.set_frame_on(False)
    ax.axhline(3.5, color="white", linewidth=2.4)

    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            red, green, blue, _ = cmap(norm(value))
            luminance = 0.299 * red + 0.587 * green + 0.114 * blue
            ax.text(
                column,
                row,
                f"{value * 100:.1f}",
                ha="center",
                va="center",
                color="white" if luminance < 0.56 else "#272727",
                fontsize=7.6,
                fontweight="bold",
            )

    colorbar_ax = ax.inset_axes([0.0, -0.20, 1.0, 0.055])
    colorbar = fig.colorbar(image, cax=colorbar_ax, orientation="horizontal")
    colorbar.set_ticks([0.6, 0.8, 1.0], labels=["60", "80", "100"])
    colorbar.set_label("Accuracy (%)", labelpad=3)
    colorbar.outline.set_visible(False)
    colorbar.ax.tick_params(length=0, pad=2, labelsize=6.2)

    if show_context:
        fig.text(
            0.055,
            0.045,
            "Single aggregate estimate per n; uncertainty intervals were not available.",
            fontsize=6.2,
            color="#767676",
            ha="left",
        )
    return fig


def write_notes(path: Path, data, sample_sizes, layout="double"):
    if layout == "single":
        architecture = "Single-panel annotated accuracy matrix."
        panel_map = "One matrix: Aggregate accuracy by metric and evaluation size."
        final_size = "159 mm x 103 mm."
        caption = f"LLM-based extraction accuracy across stratified evaluation sizes. Aggregate field-level and complete-record accuracy is shown for n = {', '.join(str(n) for n in sample_sizes)}. Values are percentages. Element-name extraction remains the principal bottleneck, limiting all-fields accuracy despite near-perfect class, material and volume extraction."
    else:
        architecture = "Quantitative grid with a dominant accuracy matrix and a compact stability panel."
        panel_map = "Panel a: Aggregate accuracy by metric and evaluation size.\nPanel b: Max-minus-min variation across evaluation sizes."
        final_size = "183 mm x 100 mm."
        caption = f"LLM-based extraction accuracy across stratified evaluation sizes. (a) Aggregate field-level and complete-record accuracy for n = {', '.join(str(n) for n in sample_sizes)}. Values are percentages. (b) Range of each metric across evaluation sizes, expressed in percentage points; lower values indicate greater stability. Element-name extraction remains the principal bottleneck, limiting all-fields accuracy despite near-perfect class, material and volume extraction."
    note = f"""Figure contract
Core conclusion: Structured field extraction is near-perfect, but element-name accuracy limits complete-record accuracy.
Figure archetype: {architecture}
Backend: Python (matplotlib only).
Final size: {final_size}
{panel_map}
Statistics: Single aggregate per n; no repeats, confidence intervals, or hypothesis tests were supplied.
Evaluation sizes: {', '.join(str(n) for n in sample_sizes)}.
Random seed recorded in source: {data.get('rng_seed', 'not supplied')}.
Reviewer risk: Accuracy uncertainty cannot be assessed from this summary alone; repeated seeds or bootstrap intervals would be needed.

Suggested caption
{caption}
"""
    path.write_text(note, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input summary JSON")
    parser.add_argument("output_dir", type=Path, help="Directory for figure exports")
    parser.add_argument("--stem", default="llm_extraction_baseline_nature", help="Output filename stem")
    parser.add_argument("--layout", choices=["single", "double"], default="double", help="Figure layout")
    parser.add_argument("--palette", choices=["blue", "red"], default="blue", help="Single-panel heatmap palette")
    parser.add_argument("--clean", action="store_true", help="Omit figure-level title, subtitle, and footer note")
    parser.add_argument("--sort-descending", action="store_true", help="Sort metric rows by descending mean accuracy")
    args = parser.parse_args()

    data, sample_sizes, matrix = load_summary(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / args.stem

    write_source_data(stem.with_name(stem.name + "_source_data.csv"), sample_sizes, matrix)
    write_notes(stem.with_name(stem.name + "_notes.txt"), data, sample_sizes, layout=args.layout)

    figure = create_single_panel_figure(
        sample_sizes,
        matrix,
        palette=args.palette,
        show_context=not args.clean,
        sort_descending=args.sort_descending,
    ) if args.layout == "single" else create_figure(sample_sizes, matrix)
    figure.savefig(stem.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    figure.savefig(stem.with_suffix(".pdf"), bbox_inches="tight", facecolor="white")
    figure.savefig(stem.with_suffix(".png"), dpi=600, bbox_inches="tight", facecolor="white")
    figure.savefig(
        stem.with_suffix(".tiff"),
        dpi=600,
        bbox_inches="tight",
        facecolor="white",
        pil_kwargs={"compression": "tiff_lzw"},
    )
    plt.close(figure)
    print(f"Exported figure bundle to {args.output_dir}")


if __name__ == "__main__":
    main()
