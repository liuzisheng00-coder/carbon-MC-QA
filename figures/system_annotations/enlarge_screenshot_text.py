"""Enlarge interface text while preserving the supplied screenshot composition."""

from pathlib import Path
import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.font_manager import FontProperties
from PIL import Image, ImageFont


HERE = Path(__file__).resolve().parent
FONT_PX = 26
DPI = 156  # 26 native pixels correspond to 12 pt at the SVG/PDF natural size.
FONT = Path("C:/Windows/Fonts/arial.ttf")
BOLD = Path("C:/Windows/Fonts/arialbd.ttf")
BLUE = "#0070e8"
GREY = "#f1f1f3"
INK = "#363636"

QUESTIONS = [
    "What is the process carbon of the steel frame welding stage?",
    "Which material has the highest embodied carbon in this modular room?",
    "What is the carbon emission of this component?",
]
ANSWERS = [
    "159.168 kgCO2e\nperspective: process; source scope: process\ncomputed from 2 atomic carbon record(s)",
    "Metal - Steel 43 - 355_A1: 4,176.676 kgCO2e\nperspective: material; source scope: material\ncomputed from 139 atomic carbon record(s)\ntotal over all groups in scope: 6,222.233 kgCO2e",
    "214.900 kgCO2e\nperspective: product; source scope: material+process\ncomputed from 6 atomic carbon record(s)",
]


def render(source: Path):
    screenshot = Image.open(source).convert("RGB")
    width, height = screenshot.size
    if (width, height) != (2205, 1488):
        raise ValueError("This overlay expects the supplied 2205 x 1488 image.")
    plt.rcParams.update({"svg.fonttype": "none", "pdf.fonttype": 42})
    fig = plt.figure(figsize=(width / DPI, height / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set(xlim=(0, width), ylim=(height, 0))
    ax.axis("off")
    ax.imshow(screenshot, extent=(0, width, height, 0), interpolation="none")
    text_items = []

    def text(x, y, content, px=FONT_PX, color=INK, bold=False):
        obj = ax.text(x, y, content, va="top", ha="left", color=color,
                      fontproperties=FontProperties(fname=str(BOLD if bold else FONT)),
                      fontsize=px * 72 / DPI, zorder=5)
        text_items.append(obj)
        return obj

    def rect(x, y, w, h, color="white", radius=0, edge="none"):
        if radius:
            ax.add_patch(FancyBboxPatch((x, y), w, h,
                         boxstyle=f"round,pad=0,rounding_size={radius}",
                         facecolor=color, edgecolor=edge, linewidth=0.7, zorder=3))
        else:
            ax.add_patch(Rectangle((x, y), w, h, facecolor=color,
                                  edgecolor=edge, linewidth=0.7, zorder=3))

    def wrapped(content, max_width, px=FONT_PX, bold=False):
        font = ImageFont.truetype(str(BOLD if bold else FONT), px)
        lines = []
        for para in content.split("\n"):
            line = ""
            for word in para.split():
                candidate = (line + " " + word).strip()
                if line and font.getlength(candidate) > max_width:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        return lines

    def bubble(x, y, w, content, color, text_color=INK, badge=False):
        lines = wrapped(content, w - 36)
        badge_h = 34 if badge else 0
        h = 24 + len(lines) * 29 + badge_h
        rect(x, y, w, h, color, radius=13)
        if badge:
            # Keep the original selected-component identifier as supplied.
            chip = screenshot.crop((1739, 1025, 1971, 1057))
            ax.imshow(chip, extent=(x + 18, x + 250, y + 42, y + 10),
                      interpolation="none", zorder=4)
        for i, line in enumerate(lines):
            text(x + 18, y + 11 + badge_h + i * 29, line, color=text_color)
        return y + h

    # Preserve the source frame, controls, scrollbar, model, and input area.
    rect(1573, 105, 622, 70)
    text(1592, 131, "Ask DM2C", bold=True)
    ax.plot(2064, 143, marker="o", color="#248944", markersize=4.5, zorder=5)
    text(2079, 131, "Grounded", px=22, color="#248944", bold=True)
    rect(1573, 177, 608, 1169)

    # Full original text with larger type; only bubble heights and wrapping vary.
    intro = ("Components: 321\nGraph nodes: 2993\nGraph edges: 5613\n"
             "Select a component in the model, or ask about this project carbon assessment.")
    y = bubble(1592, 181, 559, intro, GREY)
    for index, (question, answer) in enumerate(zip(QUESTIONS, ANSWERS)):
        question_y = [383, 681, 1009][index]
        if y > question_y - 8:
            raise RuntimeError(f"Chat blocks overlap before query {index + 1}")
        y = question_y
        y = bubble(1630 if index < 2 else 1686, y,
                   530 if index < 2 else 474, question, BLUE, "white", badge=index == 2)
        y += 14
        y = bubble(1592, y, 578, answer, GREY)
        y += 12
        ax.plot([1597, 1603, 1597], [y + 7, y + 13, y + 19],
                color=BLUE, linewidth=1.4, zorder=5)
        text(1614, y, "Show evidence", color=BLUE, bold=True)
        y += 29
    if y > 1341:
        raise RuntimeError(f"Chat text exceeds original viewport: {y} > 1341")

    # Increase the remaining labels inside their existing controls.
    rect(1066, 26, 173, 37, "#fdfdfd")
    text(1068, 29, "DM2C Carbon", px=28, bold=True, color="#242424")
    for x, y0, w, label, color in [
        (34, 120, 126, "X-Ray Off", "#18354b"),
        (211, 120, 148, "Section Off", "#18354b"),
        (415, 120, 166, "Delete section", "#81929e"),
    ]:
        # Small rectangles erase the source glyphs, not the button outlines.
        rect(x - 2, y0 - 4, w + 5, 42, "#f9fbfc" if x < 400 else "#f4f7f9")
        text(x, y0 - 1, label, bold=True, color=color)

    for x, end, label, active in [
        (72, 129, "Model", True), (182, 252, "Product", False),
        (294, 370, "Material", False), (410, 485, "Process", False),
        (537, 599, "Graph", False),
    ]:
        rect(x - 2, 1435, end - x + 5, 35, "#1b1b1b" if active else "#ffffff")
        text(x, 1438, label, px=21 if active else 22, bold=True,
             color="white" if active else "#62666b")

    rect(1595, 1424, 444, 26)
    text(1600, 1425, "Ask about the selected component...", px=22, color="#858585")

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    failures = []
    for item in text_items:
        bounds = item.get_window_extent(renderer)
        if bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > width or bounds.y1 > height:
            failures.append(item.get_text())
    if failures:
        raise RuntimeError(f"Text outside canvas: {failures}")
    output = HERE / "original_layout_text_enlarged"
    for ext in ("png", "svg", "pdf"):
        fig.savefig(output.with_suffix('.' + ext), dpi=DPI,
                    bbox_inches=None, pad_inches=0)
    # Record the design-size assumption so print resizing is not misrepresented.
    report = {
        "canvas_pixels": [width, height], "chat_font_pixels": FONT_PX,
        "chat_font_points_at_natural_size": 12,
        "natural_width_cm": width / DPI * 2.54,
        "last_content_y": y, "viewport_bottom_y": 1341,
        "content": {"questions": QUESTIONS, "answers": ANSWERS},
        "edits": "Text enlarged; chat bubbles reflowed within original panel; selected identifier retained as raster; model unaltered.",
    }
    output.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding="utf-8")
    plt.close(fig)
    print(json.dumps({"output": str(output), "chat_bottom": y, "font_pixels": FONT_PX}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    render(parser.parse_args().source)
