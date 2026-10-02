"""Render Nature-style annotations over three untouched DM2C screenshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image


plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["savefig.facecolor"] = "white"

BLUE = "#0F4D92"
BLUE_2 = "#3775BA"
TEAL = "#42949E"
VIOLET = "#7C6CCF"
RED = "#B64342"
INK = "#272727"
WHITE = "#FFFFFF"


@dataclass(frozen=True)
class Callout:
    text: str
    xy: tuple[float, float]
    xytext: tuple[float, float]
    color: str = BLUE
    fontsize: float = 7.4
    align: str = "center"
    connection_rad: float = 0.05


@dataclass(frozen=True)
class Region:
    xy: tuple[float, float]
    width: float
    height: float
    color: str = BLUE
    linestyle: str = "--"


@dataclass(frozen=True)
class PathNode:
    text: str
    xy: tuple[float, float]
    color: str


@dataclass(frozen=True)
class PanelSpec:
    key: str
    panel_label: str
    source: Path
    expected_size: tuple[int, int]
    stem: str
    callouts: tuple[Callout, ...]
    regions: tuple[Region, ...] = ()
    path_title: str | None = None
    path_nodes: tuple[PathNode, ...] = ()
    panel_label_xy: tuple[float, float] = (0.012, 0.975)
    panel_label_align: str = "left"


PANEL_SPECS: dict[str, PanelSpec] = {
    "a": PanelSpec(
        key="a",
        panel_label="a",
        source=Path(
            r"C:\Users\liuzi\AppData\Local\Temp\codex-clipboard-6236b9c0-b5f8-46ba-978b-a3912a8d4ef0.png"
        ),
        expected_size=(938, 639),
        stem="annotated_panel_a_model_qa",
        callouts=(
            Callout(
                "Selected BIM\ncomponent",
                xy=(0.425, 0.455),
                xytext=(0.155, 0.825),
                color=TEAL,
                fontsize=6.6,
            ),
            Callout(
                "Grounded QA",
                xy=(0.938, 0.895),
                xytext=(0.610, 0.925),
                color=BLUE,
                fontsize=6.6,
            ),
            Callout(
                "Perspective-specific\nresult",
                xy=(0.815, 0.185),
                xytext=(0.555, 0.170),
                color=VIOLET,
                fontsize=6.1,
            ),
        ),
        regions=(
            Region((0.250, 0.285), 0.325, 0.355, color=TEAL, linestyle="--"),
            Region((0.707, 0.019), 0.280, 0.903, color=BLUE, linestyle="-"),
        ),
    ),
    "b": PanelSpec(
        key="b",
        panel_label="b",
        source=Path(
            r"C:\Users\liuzi\AppData\Local\Temp\codex-clipboard-8a669943-f418-480e-9bbb-b0bb01db051d.png"
        ),
        expected_size=(554, 485),
        stem="annotated_panel_b_accounts",
        callouts=(
            Callout(
                "Product perspective",
                xy=(0.840, 0.835),
                xytext=(0.640, 0.970),
                color=BLUE,
                fontsize=5.6,
            ),
            Callout(
                "Material perspective",
                xy=(0.820, 0.515),
                xytext=(0.750, 0.650),
                color=TEAL,
                fontsize=5.6,
            ),
            Callout(
                "Process perspective",
                xy=(0.820, 0.190),
                xytext=(0.755, 0.330),
                color=VIOLET,
                fontsize=5.6,
            ),
        ),
        regions=(
            Region((0.745, 0.682), 0.240, 0.225, color=BLUE, linestyle="-"),
            Region((0.660, 0.365), 0.325, 0.225, color=TEAL, linestyle="-"),
            Region((0.660, 0.045), 0.325, 0.225, color=VIOLET, linestyle="-"),
        ),
        panel_label_xy=(0.982, 0.985),
        panel_label_align="right",
    ),
    "c": PanelSpec(
        key="c",
        panel_label="c",
        source=Path(
            r"C:\Users\liuzi\AppData\Local\Temp\codex-clipboard-5adb9208-edb9-4cd6-a776-398c1bdd9cd7.png"
        ),
        expected_size=(938, 639),
        stem="annotated_panel_c_calculation_path",
        callouts=(
            Callout(
                "Grounded result",
                xy=(0.947, 0.585),
                xytext=(0.815, 0.962),
                color=BLUE,
                fontsize=6.5,
                connection_rad=-0.28,
            ),
        ),
        regions=(
            Region((0.017, 0.095), 0.675, 0.818, color=TEAL, linestyle="-"),
            Region((0.708, 0.020), 0.280, 0.902, color=BLUE, linestyle="-"),
        ),
        path_title="Calculation path",
        path_nodes=(
            PathNode("Component", (0.135, 0.785), RED),
            PathNode("Consumption", (0.285, 0.785), VIOLET),
            PathNode("Carbon\nemission", (0.445, 0.785), RED),
            PathNode("Quantity", (0.590, 0.785), TEAL),
        ),
    ),
}


def _normalized_values(spec: PanelSpec) -> Iterable[float]:
    for callout in spec.callouts:
        yield from callout.xy
        yield from callout.xytext
    for region in spec.regions:
        yield from region.xy
        yield region.xy[0] + region.width
        yield region.xy[1] + region.height
    for node in spec.path_nodes:
        yield from node.xy


def validate_specs() -> None:
    """Validate wording, geometry, and source expectations before rendering."""
    if set(PANEL_SPECS) != {"a", "b", "c"}:
        raise ValueError("Exactly panels a, b, and c are required")
    labels = " ".join(
        [
            *(callout.text for spec in PANEL_SPECS.values() for callout in spec.callouts),
            *(spec.path_title or "" for spec in PANEL_SPECS.values()),
            *(node.text for spec in PANEL_SPECS.values() for node in spec.path_nodes),
        ]
    )
    if "Calculation path" not in labels:
        raise ValueError("The graph panel must use the term 'Calculation path'")
    forbidden = {"traceability", "provenance"}
    if any(term in labels.lower() for term in forbidden):
        raise ValueError("Forbidden graph terminology detected")
    for spec in PANEL_SPECS.values():
        if not spec.source.exists():
            raise FileNotFoundError(spec.source)
        if not all(0 <= value <= 1 for value in _normalized_values(spec)):
            raise ValueError(f"Annotation geometry outside panel {spec.key}")


def _add_panel_label(ax: plt.Axes, spec: PanelSpec) -> None:
    ax.text(
        spec.panel_label_xy[0],
        spec.panel_label_xy[1],
        spec.panel_label,
        transform=ax.transAxes,
        ha=spec.panel_label_align,
        va="top",
        fontsize=8,
        fontweight="bold",
        color=INK,
        zorder=20,
        bbox={
            "boxstyle": "round,pad=0.18,rounding_size=0.08",
            "facecolor": WHITE,
            "edgecolor": INK,
            "linewidth": 0.7,
            "alpha": 0.96,
        },
    )


def _add_region(ax: plt.Axes, region: Region) -> None:
    ax.add_patch(
        Rectangle(
            region.xy,
            region.width,
            region.height,
            transform=ax.transAxes,
            fill=False,
            edgecolor=region.color,
            linewidth=1.0,
            linestyle=region.linestyle,
            zorder=8,
            joinstyle="round",
        )
    )


def _add_callout(ax: plt.Axes, callout: Callout) -> None:
    ax.annotate(
        callout.text,
        xy=callout.xy,
        xytext=callout.xytext,
        xycoords=ax.transAxes,
        textcoords=ax.transAxes,
        ha=callout.align,
        va="center",
        fontsize=callout.fontsize,
        fontweight="bold",
        color=callout.color,
        linespacing=1.05,
        zorder=15,
        bbox={
            "boxstyle": "round,pad=0.28,rounding_size=0.12",
            "facecolor": WHITE,
            "edgecolor": callout.color,
            "linewidth": 0.75,
            "alpha": 0.95,
        },
        arrowprops={
            "arrowstyle": "-|>",
            "color": callout.color,
            "linewidth": 0.8,
            "shrinkA": 3,
            "shrinkB": 3,
            "mutation_scale": 9,
            "connectionstyle": f"arc3,rad={callout.connection_rad}",
        },
    )


def _add_calculation_path(ax: plt.Axes, spec: PanelSpec) -> None:
    if not spec.path_nodes:
        return
    y_line = 0.825
    first_x = spec.path_nodes[0].xy[0]
    last_x = spec.path_nodes[-1].xy[0]
    ax.annotate(
        "",
        xy=(last_x + 0.035, y_line),
        xytext=(first_x - 0.035, y_line),
        xycoords=ax.transAxes,
        textcoords=ax.transAxes,
        arrowprops={"arrowstyle": "-|>", "color": INK, "linewidth": 1.0, "mutation_scale": 9},
        zorder=12,
    )
    title_x = (first_x + last_x) / 2
    ax.text(
        title_x,
        0.885,
        spec.path_title,
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=6.8,
        fontweight="bold",
        color=INK,
        zorder=15,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": WHITE, "edgecolor": INK, "linewidth": 0.7},
    )
    for node in spec.path_nodes:
        x, _ = node.xy
        ax.add_patch(
            FancyBboxPatch(
                (x - 0.056, y_line - 0.018),
                0.112,
                0.036,
                transform=ax.transAxes,
                boxstyle="round,pad=0.003,rounding_size=0.008",
                facecolor=WHITE,
                edgecolor=node.color,
                linewidth=0.9,
                alpha=0.96,
                zorder=13,
            )
        )
        ax.text(
            x,
            y_line,
            node.text,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=4.6,
            fontweight="bold",
            color=node.color,
            linespacing=0.85,
            zorder=14,
        )


def render_panel(spec: PanelSpec, output_dir: Path) -> list[Path]:
    """Render one screenshot with vector overlays into PNG, SVG, and PDF."""
    validate_specs()
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(spec.source) as source:
        image = source.convert("RGB")
        if image.size != spec.expected_size:
            raise ValueError(f"Unexpected dimensions for panel {spec.key}: {image.size}")
        width_px, height_px = image.size

    dpi = 200
    # A tiny epsilon prevents floating-point flooring from dropping one raster row.
    fig = plt.figure(
        figsize=((width_px + 0.01) / dpi, (height_px + 0.01) / dpi),
        dpi=dpi,
        frameon=False,
    )
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(image, extent=(0, 1, 0, 1), origin="upper", interpolation="none", zorder=0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    for region in spec.regions:
        _add_region(ax, region)
    _add_calculation_path(ax, spec)
    for callout in spec.callouts:
        _add_callout(ax, callout)
    _add_panel_label(ax, spec)

    outputs: list[Path] = []
    for extension, save_dpi in (("png", dpi), ("svg", dpi), ("pdf", dpi)):
        target = output_dir / f"{spec.stem}.{extension}"
        fig.savefig(target, dpi=save_dpi, bbox_inches=None, pad_inches=0)
        outputs.append(target)
    plt.close(fig)
    return outputs


def render_all(output_dir: Path | None = None) -> list[Path]:
    """Render all three panels and return the nine output paths."""
    validate_specs()
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent
    outputs: list[Path] = []
    for key in ("a", "b", "c"):
        outputs.extend(render_panel(PANEL_SPECS[key], output_dir))
    return outputs


if __name__ == "__main__":
    for output in render_all():
        print(output)
