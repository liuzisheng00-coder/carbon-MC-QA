from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw


COLOR = "#123A70"
RGBA = (18, 58, 112, 255)
ICON_NAMES = (
    "01_modular_building",
    "02_excavator",
    "03_factory",
    "04_delivery_truck",
    "05_welding_robot",
    "06_maintenance_tools",
    "07_puzzle_integration",
    "08_search",
    "09_factory_energy",
    "10_mine_cart",
)


class VectorIcon:
    """Simple shared geometry renderer for editable SVG and transparent PNG."""

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

    def cutout_rect(self, xy, *, radius=0) -> None:
        self.shapes.append(("cutout_rect", tuple(xy), radius))

    def cutout_ellipse(self, xy) -> None:
        self.shapes.append(("cutout_ellipse", tuple(xy)))

    def cutout_polygon(self, points) -> None:
        self.shapes.append(("cutout_polygon", tuple(tuple(p) for p in points)))

    def _svg_shape(self, shape, *, mask=False) -> str:
        kind = shape[0]
        ink = "black" if mask else COLOR
        if kind in ("rect", "cutout_rect"):
            _, (x0, y0, x1, y1), radius, *rest = shape
            if mask:
                return f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="{radius}" fill="black"/>'
            fill, width = rest
            return (
                f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="{radius}" '
                f'fill="{ink if fill else "none"}" stroke-width="{width}"/>'
            )
        if kind in ("ellipse", "cutout_ellipse"):
            _, (x0, y0, x1, y1), *rest = shape
            common = f'cx="{(x0+x1)/2:g}" cy="{(y0+y1)/2:g}" rx="{(x1-x0)/2:g}" ry="{(y1-y0)/2:g}"'
            if mask:
                return f'<ellipse {common} fill="black"/>'
            fill, width = rest
            return f'<ellipse {common} fill="{ink if fill else "none"}" stroke-width="{width}"/>'
        if kind in ("polygon", "cutout_polygon"):
            points = " ".join(f"{x},{y}" for x, y in shape[1])
            return f'<polygon points="{points}" fill="{ink}" stroke="none"/>'
        if kind == "line":
            points = " ".join(f"{x},{y}" for x, y in shape[1])
            return f'<polyline points="{points}" fill="none" stroke-width="{shape[2]}"/>'
        raise ValueError(f"Unsupported shape: {kind}")

    def to_svg(self) -> str:
        cutouts = [s for s in self.shapes if s[0].startswith("cutout_")]
        rows = [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" role="img">'
        ]
        if cutouts:
            rows.extend(('<defs><mask id="cutouts"><rect width="256" height="256" fill="white"/>',))
            rows.extend(self._svg_shape(s, mask=True) for s in cutouts)
            rows.append('</mask></defs>')
        mask_attr = ' mask="url(#cutouts)"' if cutouts else ""
        rows.append(f'<g fill="{COLOR}" stroke="{COLOR}" stroke-linecap="round" stroke-linejoin="round"{mask_attr}>')
        rows.extend(self._svg_shape(s) for s in self.shapes if not s[0].startswith("cutout_"))
        rows.extend(("</g>", "</svg>"))
        return "\n".join(rows) + "\n"

    def render(self, size: int) -> Image.Image:
        scale = size / 256
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        def box(xy):
            return tuple(round(v * scale) for v in xy)

        def points(items: Iterable[tuple[float, float]]):
            return [(round(x * scale), round(y * scale)) for x, y in items]

        for shape in self.shapes:
            kind = shape[0]
            if kind == "rect":
                _, xy, radius, fill, width = shape
                if fill:
                    draw.rounded_rectangle(box(xy), radius=round(radius * scale), fill=RGBA)
                else:
                    draw.rounded_rectangle(box(xy), radius=round(radius * scale), outline=RGBA, width=round(width * scale))
            elif kind == "ellipse":
                _, xy, fill, width = shape
                if fill:
                    draw.ellipse(box(xy), fill=RGBA)
                else:
                    draw.ellipse(box(xy), outline=RGBA, width=round(width * scale))
            elif kind == "polygon":
                draw.polygon(points(shape[1]), fill=RGBA)
            elif kind == "line":
                draw.line(points(shape[1]), fill=RGBA, width=round(shape[2] * scale), joint="curve")
            elif kind == "cutout_rect":
                _, xy, radius = shape
                draw.rounded_rectangle(box(xy), radius=round(radius * scale), fill=(0, 0, 0, 0))
            elif kind == "cutout_ellipse":
                draw.ellipse(box(shape[1]), fill=(0, 0, 0, 0))
            elif kind == "cutout_polygon":
                draw.polygon(points(shape[1]), fill=(0, 0, 0, 0))
        return image


def modular_building() -> VectorIcon:
    c = VectorIcon()
    c.line(((28, 62), (126, 27), (224, 57), (117, 92), (28, 62)), width=6)
    c.line(((28, 62), (28, 184), (117, 219), (117, 92)), width=6)
    c.line(((117, 92), (117, 219), (224, 181), (224, 57)), width=6)
    c.line(((38, 70), (117, 100), (214, 68)), width=3)
    c.line(((105, 88), (105, 214)), width=3)
    c.line(((47, 82), (47, 174)), width=3)
    c.polygon(((54, 91), (91, 104), (91, 191), (54, 177)))
    c.cutout_polygon(((60, 101), (84, 109), (84, 180), (60, 172)))
    c.ellipse((78, 148, 83, 153))
    c.polygon(((151, 102), (198, 87), (198, 139), (151, 154)))
    c.cutout_polygon(((158, 108), (171, 104), (171, 143), (158, 147)))
    c.cutout_polygon(((178, 101), (191, 97), (191, 134), (178, 139)))
    return c


def excavator() -> VectorIcon:
    c = VectorIcon()
    c.rect((27, 184, 223, 224), radius=18, fill=False, width=9)
    for x in (52, 86, 120, 154, 188):
        c.ellipse((x - 8, 196, x + 8, 212), fill=False, width=5)
    c.rect((104, 149, 190, 186), radius=5)
    c.polygon(((137, 149), (146, 100), (181, 100), (197, 149)))
    c.cutout_polygon(((153, 109), (176, 109), (185, 140), (147, 140)))
    c.line(((151, 103), (101, 52), (72, 44)), width=13)
    c.line(((101, 52), (76, 119)), width=13)
    c.polygon(((61, 112), (84, 116), (76, 148), (50, 142)))
    c.line(((196, 150), (208, 128)), width=8)
    return c


def factory() -> VectorIcon:
    c = VectorIcon()
    c.polygon(((29, 213), (29, 101), (72, 77), (72, 101), (111, 78), (111, 102),
               (139, 86), (148, 32), (174, 32), (184, 92), (195, 49), (220, 49),
               (227, 213)))
    for x in (54, 84, 114):
        c.cutout_rect((x, 130, x + 18, 145), radius=2)
    c.cutout_rect((174, 153, 200, 213), radius=3)
    c.line(((27, 216), (230, 216)), width=7)
    return c


def delivery_truck() -> VectorIcon:
    c = VectorIcon()
    c.rect((22, 63, 145, 168), radius=4)
    c.polygon(((145, 92), (184, 92), (214, 126), (225, 126), (225, 169), (145, 169)))
    c.cutout_polygon(((159, 104), (180, 104), (198, 126), (159, 126)))
    c.rect((22, 163, 231, 178), radius=3)
    c.ellipse((43, 155, 91, 203), fill=False, width=10)
    c.ellipse((167, 155, 215, 203), fill=False, width=10)
    c.ellipse((59, 171, 75, 187))
    c.ellipse((183, 171, 199, 187))
    c.line(((207, 143), (225, 143)), width=5)
    return c


def welding_robot() -> VectorIcon:
    c = VectorIcon()
    c.line(((30, 221), (113, 221)), width=8)
    c.polygon(((44, 209), (54, 155), (88, 155), (103, 209)))
    c.ellipse((51, 117, 91, 157))
    c.line(((71, 124), (99, 82), (148, 72)), width=14)
    c.ellipse((89, 70, 111, 92))
    c.ellipse((139, 61, 161, 83))
    c.line(((151, 73), (165, 119), (188, 139)), width=12)
    c.ellipse((156, 110, 176, 130))
    c.polygon(((184, 132), (205, 146), (195, 160), (175, 145)))
    c.line(((202, 157), (211, 175)), width=5)
    c.line(((212, 145), (226, 137)), width=5)
    c.line(((209, 160), (226, 164)), width=5)
    c.line(((202, 166), (205, 184)), width=5)
    c.line(((191, 166), (182, 180)), width=5)
    return c


def maintenance_tools() -> VectorIcon:
    c = VectorIcon()
    # Wrench from upper left to lower right.
    c.polygon(((39, 43), (57, 49), (67, 64), (61, 78), (138, 157), (154, 143),
               (177, 166), (151, 194), (127, 171), (140, 157), (59, 82), (43, 87),
               (29, 75), (25, 55), (39, 64), (50, 61), (51, 50)))
    # Screwdriver from upper right to lower left.
    c.line(((194, 42), (83, 159)), width=13)
    c.polygon(((184, 34), (200, 31), (218, 47), (214, 62), (198, 78), (181, 61)))
    c.polygon(((70, 150), (94, 174), (55, 219), (31, 195)))
    c.line(((45, 198), (78, 165)), width=5)
    return c


def puzzle_integration() -> VectorIcon:
    c = VectorIcon()
    # Four interlocking pieces form a compact square-plus-tab silhouette.
    c.rect((36, 58, 126, 146), radius=5)
    c.ellipse((91, 35, 131, 76))
    c.cutout_ellipse((104, 111, 143, 150))
    c.rect((126, 67, 212, 151), radius=5)
    c.ellipse((193, 91, 230, 128))
    c.cutout_ellipse((108, 83, 146, 122))
    c.rect((45, 146, 130, 224), radius=5)
    c.ellipse((93, 126, 133, 165))
    c.cutout_ellipse((63, 199, 100, 235))
    c.rect((130, 151, 214, 218), radius=5)
    c.ellipse((113, 169, 150, 206))
    c.ellipse((151, 199, 190, 236))
    return c


def search() -> VectorIcon:
    c = VectorIcon()
    c.ellipse((38, 34, 157, 153), fill=False, width=11)
    c.line(((136, 137), (220, 221)), width=15)
    c.ellipse((203, 204, 225, 226))
    return c


def factory_energy() -> VectorIcon:
    c = factory()
    # Clear a badge area from the factory before drawing its outline and bolt.
    c.cutout_ellipse((118, 103, 232, 217))
    c.ellipse((118, 103, 232, 217), fill=False, width=8)
    c.polygon(((181, 118), (151, 162), (173, 162), (158, 203), (203, 151), (180, 151)))
    return c


def mine_cart() -> VectorIcon:
    c = VectorIcon()
    c.line(((26, 91), (230, 91)), width=8)
    c.polygon(((34, 91), (222, 91), (202, 177), (58, 177)))
    c.cutout_polygon(((45, 103), (211, 103), (195, 164), (65, 164)))
    # Rock pile.
    c.polygon(((46, 85), (63, 58), (89, 50), (108, 68), (129, 45), (154, 52),
               (170, 67), (190, 55), (216, 85)))
    c.line(((37, 218), (222, 218)), width=5)
    c.ellipse((57, 163, 105, 211), fill=False, width=8)
    c.ellipse((155, 163, 203, 211), fill=False, width=8)
    c.ellipse((72, 178, 90, 196))
    c.ellipse((170, 178, 188, 196))
    return c


def build_canvases() -> dict[str, VectorIcon]:
    builders = (
        modular_building,
        excavator,
        factory,
        delivery_truck,
        welding_robot,
        maintenance_tools,
        puzzle_integration,
        search,
        factory_energy,
        mine_cart,
    )
    return {name: builder() for name, builder in zip(ICON_NAMES, builders)}


def build_svgs() -> dict[str, str]:
    return {name: icon.to_svg() for name, icon in build_canvases().items()}


def render_all(output_dir: Path, png_size: int = 2048) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    rendered: list[Image.Image] = []

    for name, icon in build_canvases().items():
        svg_path = output_dir / f"{name}.svg"
        png_path = output_dir / f"{name}.png"
        svg_path.write_text(icon.to_svg(), encoding="utf-8")
        image = icon.render(png_size)
        image.save(png_path, dpi=(300, 300), optimize=True)
        rendered.append(image)
        created.extend((svg_path, png_path))

    sheet = Image.new("RGB", (1800, 900), "white")
    for index, image in enumerate(rendered):
        thumb = image.resize((300, 300), Image.Resampling.LANCZOS)
        x = 40 + (index % 5) * 350
        y = 55 + (index // 5) * 420
        sheet.paste(thumb, (x, y), thumb)
    preview_path = output_dir / "preview_sheet.png"
    sheet.save(preview_path, dpi=(200, 200), optimize=True)
    created.append(preview_path)
    return created


if __name__ == "__main__":
    render_all(Path(__file__).resolve().parent / "outputs")

