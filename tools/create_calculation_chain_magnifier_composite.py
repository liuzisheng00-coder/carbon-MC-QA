"""Render and compose a full knowledge graph with a magnified calculation chain."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from PIL import Image, ImageDraw, ImageFont


LENS_COLOUR = (186, 230, 245, 150)
RIM_COLOUR = (91, 119, 138, 255)
HANDLE_COLOUR = (238, 132, 69, 255)
DEFAULT_CANVAS_SIZE = (9000, 4500)
DETAIL_LABEL_FONT_SIZE = 10.5
DETAIL_NODE_BASE_SIZE = 360
LEFT_PANEL_BOUNDS = (0.01, 0.02, 0.50, 0.98)
RIGHT_PANEL_BOUNDS = (0.58, 0.04, 0.995, 0.96)
DETAIL_TITLE = "Calculation Information Subgraph"
MAGNIFIER_RADIUS_FRACTION = 0.0275
DETAIL_TITLE_Y_FRACTION = 0.07
DETAIL_TITLE_FONT_FRACTION = 0.019


def _node_rgb(node_data):
    color = node_data.get("viz", {}).get("color", {})
    return tuple(int(color.get(channel, 128)) / 255 for channel in ("r", "g", "b"))


def _node_size(graph, node_id):
    label = str(graph.nodes[node_id].get("label", ""))
    if node_id.startswith("BuildingComponent:"):
        return 950
    if label == "CarbonEmission":
        return 1200
    return DETAIL_NODE_BASE_SIZE + min(graph.degree(node_id), 8) * 48


def _short_labels(graph):
    labels = {}
    for node_id, data in graph.nodes(data=True):
        label = str(data.get("label", node_id.split(":", 1)[0]))
        if label.startswith("DesignQuantity"):
            label = "DesignQuantity"
        elif label.startswith("EnergyConsumption"):
            label = "EnergyConsumption"
        labels[node_id] = label
    return labels


def count_nonwhite_pixels(image):
    """Count pixels that are not close to the white canvas background."""
    rgb = image.convert("RGB")
    return sum(pixel != (255, 255, 255) for pixel in rgb.getdata())


def nonwhite_count_in_box(image, box):
    """Count non-white pixels inside a rectangular QA region."""
    return count_nonwhite_pixels(image.crop(box))


def fit_inside(image, box):
    """Resize an image to fit inside a target box while preserving aspect ratio."""
    left, top, right, bottom = box
    box_width = right - left
    box_height = bottom - top
    ratio = min(box_width / image.width, box_height / image.height)
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    resized = image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    offset = (left + (box_width - size[0]) // 2, top + (box_height - size[1]) // 2)
    return resized, offset


def _proportional_box(canvas_size, left, top, right, bottom):
    width, height = canvas_size
    return (
        round(width * left),
        round(height * top),
        round(width * right),
        round(height * bottom),
    )


def _paste_fitted(canvas, source, box):
    fitted, offset = fit_inside(source, box)
    canvas.alpha_composite(fitted, offset)


def draw_cartoon_magnifier(draw, center, radius, handle_end):
    """Draw a flat cartoon magnifying glass without fluorescent styling."""
    cx, cy = center
    end_x, end_y = handle_end
    outer_width = max(8, radius // 3)
    inner_width = max(5, radius // 5)
    rim_width = max(5, radius // 10)
    draw.line((cx, cy, end_x, end_y), fill=RIM_COLOUR, width=outer_width)
    draw.line((cx, cy, end_x, end_y), fill=HANDLE_COLOUR, width=inner_width)
    bounds = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.ellipse(bounds, fill=LENS_COLOUR, outline=RIM_COLOUR, width=rim_width)
    highlight = (
        round(cx - radius * 0.55),
        round(cy - radius * 0.55),
        round(cx + radius * 0.15),
        round(cy + radius * 0.15),
    )
    draw.arc(highlight, 205, 285, fill=(255, 255, 255, 230), width=max(3, radius // 18))


def _load_bold_font(size):
    for candidate in (Path("C:/Windows/Fonts/arialbd.ttf"), Path("DejaVuSans-Bold.ttf")):
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw_detail_title(draw, canvas_size):
    """Draw the approved topic centred above the right detail panel."""
    width, height = canvas_size
    font = _load_bold_font(max(12, round(height * DETAIL_TITLE_FONT_FRACTION)))
    centre_x = round(width * ((RIGHT_PANEL_BOUNDS[0] + RIGHT_PANEL_BOUNDS[2]) / 2))
    centre_y = round(height * DETAIL_TITLE_Y_FRACTION)
    draw.text(
        (centre_x, centre_y),
        DETAIL_TITLE,
        fill=(25, 25, 25, 255),
        font=font,
        anchor="mm",
    )


def compose_figure(full_graph_path, detail_path, output_path, canvas_size=(9000, 4500)) -> Path:
    """Place the full graph and detail graph on one horizontal publication canvas."""
    canvas = Image.new("RGBA", canvas_size, "white")
    left_box = _proportional_box(canvas_size, *LEFT_PANEL_BOUNDS)
    right_box = _proportional_box(canvas_size, *RIGHT_PANEL_BOUNDS)
    with Image.open(full_graph_path) as full_graph:
        _paste_fitted(canvas, full_graph, left_box)
    with Image.open(detail_path) as detail_graph:
        _paste_fitted(canvas, detail_graph, right_box)

    width, height = canvas_size
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw_detail_title(draw, canvas_size)
    draw_cartoon_magnifier(
        draw,
        center=(round(width * 0.515), round(height * 0.47)),
        radius=round(height * MAGNIFIER_RADIUS_FRACTION),
        handle_end=(round(width * 0.58), round(height * 0.55)),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-graph", type=Path, required=True)
    parser.add_argument("--detail-gexf", type=Path, required=True)
    parser.add_argument("--detail-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=DEFAULT_CANVAS_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_CANVAS_SIZE[1])
    args = parser.parse_args()

    render_detail_graph(args.detail_gexf, args.detail_cache)
    compose_figure(
        args.full_graph,
        args.detail_cache,
        args.output,
        canvas_size=(args.width, args.height),
    )
    print(f"Rendered detail graph: {args.detail_cache}")
    print(f"Rendered magnifier composite: {args.output}")


def render_detail_graph(gexf_path: Path, output_path: Path, size=(3600, 2700)) -> Path:
    """Render a deterministic, high-resolution calculation-chain panel."""
    graph = nx.read_gexf(gexf_path)
    positions = nx.spring_layout(graph, seed=42, k=0.92, iterations=700)
    node_colours = [_node_rgb(graph.nodes[node_id]) for node_id in graph.nodes]
    node_sizes = [_node_size(graph, node_id) for node_id in graph.nodes]
    edge_colours = [_node_rgb(graph.nodes[source]) for source, _target in graph.edges]

    dpi = 300
    fig = plt.figure(figsize=(size[0] / dpi, size[1] / dpi), dpi=dpi, facecolor="white")
    ax = fig.add_axes((0.025, 0.025, 0.95, 0.95))
    nx.draw_networkx_edges(
        graph,
        positions,
        ax=ax,
        edge_color=edge_colours,
        width=1.7,
        arrows=True,
        arrowsize=12,
        node_size=node_sizes,
        connectionstyle="arc3,rad=0.015",
    )
    nx.draw_networkx_nodes(
        graph,
        positions,
        ax=ax,
        node_color=node_colours,
        node_size=node_sizes,
        linewidths=0.6,
        edgecolors="white",
    )
    labels = _short_labels(graph)
    for node_id, (x_coord, y_coord) in positions.items():
        ax.annotate(
            labels[node_id],
            (x_coord, y_coord),
            xytext=(5, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=DETAIL_LABEL_FONT_SIZE,
            color="#222222",
        )
    ax.margins(0.13)
    ax.set_axis_off()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    main()
