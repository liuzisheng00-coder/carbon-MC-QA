"""Render a Gephi Lite workspace with the same full-graph framing at high resolution."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont


RGB_PATTERN = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
BOUNDARY_OUTER_COLOR = (157, 132, 195, 255)


def compute_view_bounds(layout: Mapping[str, Mapping[str, float]], aspect_ratio: float, padding: float = 0.04):
    """Return a padded layout viewport with the requested width/height ratio."""
    xs = [float(node["x"]) for node in layout.values()]
    ys = [float(node["y"]) for node in layout.values()]
    if not xs or not ys:
        raise ValueError("Workspace contains no positioned nodes.")

    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    width = max(xmax - xmin, 1.0)
    height = max(ymax - ymin, 1.0)
    current_ratio = width / height
    if current_ratio < aspect_ratio:
        width = height * aspect_ratio
    else:
        height = width / aspect_ratio

    width *= 1 + 2 * padding
    height *= 1 + 2 * padding
    center_x = (xmin + xmax) / 2
    center_y = (ymin + ymax) / 2
    return (
        center_x - width / 2,
        center_x + width / 2,
        center_y - height / 2,
        center_y + height / 2,
    )


def parse_color(value, default=(153, 153, 153, 255)):
    if not isinstance(value, str):
        return default
    match = RGB_PATTERN.match(value)
    if match:
        return tuple(int(channel) for channel in match.groups()) + (255,)
    if value.startswith("#"):
        hex_value = value[1:]
        if len(hex_value) == 6:
            return tuple(int(hex_value[index : index + 2], 16) for index in range(0, 6, 2)) + (255,)
        if len(hex_value) == 8:
            return tuple(int(hex_value[index : index + 2], 16) for index in range(0, 8, 2))
    return default


def get_font(size: int):
    for candidate in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def scale_reference_pixels(reference_pixels: float, output_width: int) -> int:
    """Convert a line width measured in the 1689 px reference view to output pixels."""
    return max(1, round(reference_pixels * output_width / 1689))


def convex_hull(points):
    """Return the outer convex polygon of 2-D points in counter-clockwise order."""
    ordered = sorted(set(points))
    if len(ordered) <= 1:
        return ordered

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

    lower = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def draw_dashed_polygon(draw: ImageDraw.ImageDraw, points, color, width: int, dash: int, gap: int):
    for start, end in zip(points, points[1:] + points[:1]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            continue
        distance = 0.0
        while distance < length:
            segment_end = min(distance + dash, length)
            draw.line(
                (
                    (start[0] + dx * distance / length, start[1] + dy * distance / length),
                    (start[0] + dx * segment_end / length, start[1] + dy * segment_end / length),
                ),
                fill=color,
                width=width,
            )
            distance += dash + gap


def expand_hull(hull, margin: float):
    """Move a hull's vertices away from its center by a fixed screen-space margin."""
    center_x = sum(point[0] for point in hull) / len(hull)
    center_y = sum(point[1] for point in hull) / len(hull)
    expanded = []
    for x, y in hull:
        dx, dy = x - center_x, y - center_y
        distance = max((dx * dx + dy * dy) ** 0.5, 1)
        expanded.append((x + margin * dx / distance, y + margin * dy / distance))
    return expanded


def boundary_outline_hulls(hull, scale: float):
    """Return separate inner and outer envelopes so their colors never overlap."""
    return expand_hull(hull, margin=10 * scale), expand_hull(hull, margin=24 * scale)


def single_boundary_hull(hull, scale: float):
    """Position one envelope just outside the visible label area."""
    return expand_hull(hull, margin=20 * scale)


def get_boundary_style(name="pastel"):
    if name == "pastel":
        return {
            "color": BOUNDARY_OUTER_COLOR,
            "line_width": 3.5,
            "halo_width": 6.0,
            "halo_color": (255, 255, 255, 255),
        }
    if name == "fluorescent":
        return {
            "color": (216, 60, 255, 255),
            "line_width": 5.0,
            "halo_width": 9.0,
            "halo_color": (255, 255, 255, 255),
        }
    if name == "fluorescent_green":
        return {
            "color": (57, 255, 20, 255),
            "line_width": 10.0,
            "halo_width": 14.0,
            "halo_color": (0, 0, 0, 255),
        }
    if name == "fluorescent_yellow":
        return {
            "color": (255, 242, 0, 255),
            "line_width": 5.0,
            "halo_width": 9.0,
            "halo_color": (70, 70, 70, 255),
        }
    raise ValueError(f"Unknown boundary style: {name}")


def draw_label_boundary(draw: ImageDraw.ImageDraw, label_boxes, scale: float, style_name="pastel"):
    """Draw a high-contrast envelope just outside all visible label boxes."""
    corners = [corner for box in label_boxes for corner in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))]
    hull = convex_hull(corners)
    if len(hull) < 3:
        return
    boundary = single_boundary_hull(hull, scale)
    closed = boundary + boundary[:1]
    style = get_boundary_style(style_name)
    # A contrasting halo prevents the fluorescent callout from blending into graph edges.
    draw.line(closed, fill=style["halo_color"], width=max(1, round(style["halo_width"] * scale)), joint="curve")
    draw.line(closed, fill=style["color"], width=max(1, round(style["line_width"] * scale)), joint="curve")


def render_workspace(
    workspace: Mapping,
    output_path: Path,
    target_size: tuple[int, int],
    padding: float = 0.04,
    edge_width_reference: float = 0.6,
    label_boundary: bool = False,
    boundary_style: str = "pastel",
):
    """Render layout, edges, nodes, and the saved visible labels to a PNG."""
    graph = workspace["graphDataset"]
    layout = graph["layout"]
    node_data = graph["nodeData"]
    width, height = target_size
    xmin, xmax, ymin, ymax = compute_view_bounds(layout, width / height, padding)
    x_range, y_range = xmax - xmin, ymax - ymin

    image = Image.new("RGBA", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")

    def point(node_id):
        position = layout[node_id]
        return (
            (float(position["x"]) - xmin) / x_range * width,
            (ymax - float(position["y"])) / y_range * height,
        )

    appearance = workspace.get("appearance", {})
    edge_value = appearance.get("edgesColor", {}).get("value", "#4A4A4AFF")
    edge_color = parse_color(edge_value, (74, 74, 74, 255))
    edge_width = scale_reference_pixels(edge_width_reference, width)
    for edge in graph.get("fullGraph", {}).get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        if source in layout and target in layout:
            draw.line((point(source), point(target)), fill=edge_color, width=edge_width)

    base_diameter = float(appearance.get("nodesSize", {}).get("value", 20))
    radius = max(1, base_diameter * width / 1689 / 2)
    for node_id in layout:
        x, y = point(node_id)
        color = parse_color(node_data.get(node_id, {}).get("color"))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    label_font = get_font(max(1, round(14 * width / 1689)))
    label_offset = max(2, round(4 * width / 1689))
    label_boxes = []
    for node_id in layout:
        label = node_data.get(node_id, {}).get("label", "")
        if label:
            x, y = point(node_id)
            label_position = (x + radius + label_offset, y)
            draw.text(label_position, str(label), font=label_font, fill=(0, 0, 0, 255), anchor="lm")
            label_boxes.append(draw.textbbox(label_position, str(label), font=label_font, anchor="lm"))
    if label_boundary:
        draw_label_boundary(draw, label_boxes, width / 1689, style_name=boundary_style)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Gephi Lite workspace JSON")
    parser.add_argument("reference", type=Path, help="Reference screenshot that defines the viewport aspect ratio")
    parser.add_argument("output", type=Path, help="Output PNG path")
    parser.add_argument("--scale", type=int, default=4, help="Resolution multiplier relative to the reference image")
    parser.add_argument("--padding", type=float, default=0.04, help="Fractional whitespace around the graph")
    parser.add_argument(
        "--edge-width",
        type=float,
        default=0.6,
        help="Edge width in pixels when viewed at the reference screenshot size",
    )
    parser.add_argument("--label-boundary", action="store_true", help="Draw an envelope around all saved labels")
    parser.add_argument(
        "--boundary-style",
        choices=["pastel", "fluorescent", "fluorescent_green", "fluorescent_yellow"],
        default="pastel",
    )
    args = parser.parse_args()

    with args.workspace.open(encoding="utf-8") as handle:
        workspace = json.load(handle)
    with Image.open(args.reference) as reference:
        reference_width, reference_height = reference.size
    output = render_workspace(
        workspace,
        args.output,
        target_size=(reference_width * args.scale, reference_height * args.scale),
        padding=args.padding,
        edge_width_reference=args.edge_width,
        label_boundary=args.label_boundary,
        boundary_style=args.boundary_style,
    )
    print(f"Rendered {output}")


if __name__ == "__main__":
    main()
