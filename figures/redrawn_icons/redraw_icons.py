from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw


COLOR = "#5B5B5B"
RGB = (91, 91, 91, 255)
ICON_NAMES = (
    "01_delivery_truck",
    "02_tower_crane",
    "03_buildings",
    "04_recycling",
)


class IconCanvas:
    """Store simple geometry once, then emit SVG or a transparent PNG."""

    def __init__(self) -> None:
        self.shapes: list[tuple] = []

    def rect(self, xy, *, radius=0, fill=True, width=0) -> None:
        self.shapes.append(("rect", tuple(xy), radius, fill, width))

    def ellipse(self, xy, *, fill=True, width=0) -> None:
        self.shapes.append(("ellipse", tuple(xy), fill, width))

    def polygon(self, points) -> None:
        self.shapes.append(("polygon", tuple(tuple(p) for p in points)))

    def line(self, points, *, width=6) -> None:
        self.shapes.append(("line", tuple(tuple(p) for p in points), width))

    def to_svg(self) -> str:
        rows = [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" ',
            'width="256" height="256" role="img">',
            f'<g fill="{COLOR}" stroke="{COLOR}" stroke-linecap="round" stroke-linejoin="round">',
        ]
        for shape in self.shapes:
            kind = shape[0]
            if kind == "rect":
                _, (x0, y0, x1, y1), radius, fill, width = shape
                attrs = f'x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="{radius}"'
                rows.append(f'<rect {attrs} fill="{COLOR if fill else "none"}" stroke-width="{width}"/>')
            elif kind == "ellipse":
                _, (x0, y0, x1, y1), fill, width = shape
                rows.append(
                    f'<ellipse cx="{(x0+x1)/2:g}" cy="{(y0+y1)/2:g}" '
                    f'rx="{(x1-x0)/2:g}" ry="{(y1-y0)/2:g}" '
                    f'fill="{COLOR if fill else "none"}" stroke-width="{width}"/>'
                )
            elif kind == "polygon":
                points = " ".join(f"{x},{y}" for x, y in shape[1])
                rows.append(f'<polygon points="{points}" stroke="none"/>')
            elif kind == "line":
                points = " ".join(f"{x},{y}" for x, y in shape[1])
                rows.append(f'<polyline points="{points}" fill="none" stroke-width="{shape[2]}"/>')
        rows.extend(("</g>", "</svg>"))
        return "\n".join(rows) + "\n"

    def render(self, size: int) -> Image.Image:
        scale = size / 256
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        def pts(points: Iterable[tuple[float, float]]):
            return [(round(x * scale), round(y * scale)) for x, y in points]

        for shape in self.shapes:
            kind = shape[0]
            if kind == "rect":
                _, xy, radius, fill, width = shape
                box = tuple(round(v * scale) for v in xy)
                if fill:
                    draw.rounded_rectangle(box, radius=round(radius * scale), fill=RGB)
                else:
                    draw.rounded_rectangle(box, radius=round(radius * scale), outline=RGB, width=round(width * scale))
            elif kind == "ellipse":
                _, xy, fill, width = shape
                box = tuple(round(v * scale) for v in xy)
                if fill:
                    draw.ellipse(box, fill=RGB)
                else:
                    draw.ellipse(box, outline=RGB, width=round(width * scale))
            elif kind == "polygon":
                draw.polygon(pts(shape[1]), fill=RGB)
            elif kind == "line":
                draw.line(pts(shape[1]), fill=RGB, width=round(shape[2] * scale), joint="curve")
        return image


def delivery_truck() -> IconCanvas:
    c = IconCanvas()
    c.rect((22, 52, 143, 157), radius=4)
    c.rect((22, 151, 222, 170), radius=3)
    c.rect((143, 94, 158, 158))
    c.polygon(((158, 94), (185, 94), (217, 132), (203, 132), (181, 108), (158, 108)))
    c.rect((158, 132, 217, 158))
    c.rect((202, 128, 217, 160), radius=2)
    c.rect((213, 146, 230, 169), radius=3)
    c.rect((27, 169, 223, 176), radius=2)
    c.ellipse((42, 151, 92, 201), fill=False, width=10)
    c.ellipse((163, 151, 213, 201), fill=False, width=10)
    c.ellipse((59, 168, 75, 184))
    c.ellipse((180, 168, 196, 184))
    c.line(((166, 115), (181, 115), (195, 131), (166, 131)), width=5)
    return c


def tower_crane() -> IconCanvas:
    c = IconCanvas()
    # Tower and base.
    c.line(((68, 218), (100, 218)), width=7)
    c.line(((75, 218), (78, 65), (94, 65), (96, 218)), width=7)
    for y0, y1 in ((72, 96), (96, 120), (120, 144), (144, 168), (168, 192), (192, 214)):
        c.line(((79, y0), (94, y1)), width=4)
        c.line(((94, y0), (79, y1)), width=4)
    # Boom, counter-boom, supports, and trolley cable.
    c.line(((28, 78), (84, 51), (232, 82)), width=7)
    c.line(((84, 51), (84, 78), (226, 82)), width=4)
    c.line(((84, 51), (45, 77)), width=4)
    c.line(((84, 51), (123, 76)), width=4)
    c.line(((123, 76), (157, 66), (191, 80), (225, 72)), width=4)
    c.line(((39, 75), (39, 94), (25, 94), (25, 78)), width=6)
    c.line(((188, 78), (188, 139)), width=4)
    c.line(((181, 139), (195, 139)), width=5)
    # Suspended house-shaped load.
    c.line(((166, 157), (188, 139), (210, 157)), width=6)
    c.rect((166, 157, 210, 202), fill=False, width=6)
    c.rect((181, 174, 194, 202), fill=False, width=5)
    c.line(((158, 208), (218, 208)), width=6)
    return c


def buildings() -> IconCanvas:
    c = IconCanvas()
    c.line(((22, 220), (234, 220)), width=7)
    c.rect((38, 103, 91, 220), fill=False, width=7)
    c.rect((91, 42, 168, 220), fill=False, width=7)
    c.rect((168, 112, 220, 220), fill=False, width=7)
    # Windows are deliberately simple filled squares for thumbnail clarity.
    for x in (106, 128, 150):
        for y in (61, 84, 107, 130, 153):
            c.rect((x - 5, y - 5, x + 5, y + 5), radius=1)
    for x in (53, 76):
        for y in (122, 147, 172):
            c.rect((x - 5, y - 5, x + 5, y + 5), radius=1)
    for x in (183, 205):
        for y in (131, 156, 181):
            c.rect((x - 5, y - 5, x + 5, y + 5), radius=1)
    c.rect((122, 184, 138, 220), fill=False, width=6)
    return c


def recycling() -> IconCanvas:
    c = IconCanvas()
    # Three independently drawn folded arrows leave a clean triangular void.
    c.polygon(((94, 37), (139, 37), (166, 82), (181, 73), (174, 121),
               (128, 109), (143, 100), (123, 65), (106, 65), (91, 91), (69, 78)))
    c.polygon(((194, 88), (219, 130), (193, 174), (212, 184), (166, 202),
               (156, 153), (175, 164), (194, 131), (182, 111), (153, 111), (153, 86)))
    c.polygon(((153, 219), (103, 219), (76, 174), (50, 183), (68, 135),
               (115, 147), (98, 157), (119, 191), (139, 191), (154, 165), (177, 178)))
    return c


def build_canvases() -> dict[str, IconCanvas]:
    return dict(zip(ICON_NAMES, (delivery_truck(), tower_crane(), buildings(), recycling())))


def build_svgs() -> dict[str, str]:
    return {name: canvas.to_svg() for name, canvas in build_canvases().items()}


def render_all(output_dir: Path, png_size: int = 2048) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    canvases = build_canvases()
    rendered: list[Image.Image] = []

    for name, canvas in canvases.items():
        svg_path = output_dir / f"{name}.svg"
        png_path = output_dir / f"{name}.png"
        svg_path.write_text(canvas.to_svg(), encoding="utf-8")
        image = canvas.render(png_size)
        image.save(png_path, dpi=(300, 300), optimize=True)
        rendered.append(image)
        created.extend((svg_path, png_path))

    sheet = Image.new("RGB", (1200, 1200), "white")
    positions = ((70, 70), (630, 70), (70, 630), (630, 630))
    for image, position in zip(rendered, positions):
        thumb = image.resize((500, 500), Image.Resampling.LANCZOS)
        sheet.paste(thumb, position, thumb)
    preview_path = output_dir / "preview_sheet.png"
    sheet.save(preview_path, dpi=(200, 200), optimize=True)
    created.append(preview_path)
    return created


if __name__ == "__main__":
    render_all(Path(__file__).resolve().parent / "outputs")
