"""Structural QA and contact-sheet generation for annotated screenshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from annotate_system_screenshots import PANEL_SPECS


HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_contact_sheet() -> Path:
    images = [Image.open(HERE / f"{PANEL_SPECS[key].stem}.png").convert("RGB") for key in ("a", "b", "c")]
    margin = 24
    gap = 32
    width = max(image.width for image in images) + 2 * margin
    height = sum(image.height for image in images) + gap * (len(images) - 1) + 2 * margin
    sheet = Image.new("RGB", (width, height), "white")
    y = margin
    for image in images:
        x = (width - image.width) // 2
        sheet.paste(image, (x, y))
        y += image.height + gap
    target = HERE / "contact_sheet.png"
    sheet.save(target, dpi=(300, 300))
    return target


def run_qa() -> dict:
    result: dict[str, object] = {"panels": {}, "contact_sheet": str(make_contact_sheet())}
    required_by_panel = {
        "a": ["Selected BIM", "Grounded QA", "Perspective-specific"],
        "b": ["Product perspective", "Material perspective", "Process perspective"],
        "c": ["Calculation path", "Component", "Consumption", "Grounded result"],
    }
    for key in ("a", "b", "c"):
        spec = PANEL_SPECS[key]
        png = HERE / f"{spec.stem}.png"
        svg = HERE / f"{spec.stem}.svg"
        pdf = HERE / f"{spec.stem}.pdf"
        with Image.open(png) as image:
            png_size = image.size
            image.verify()
        svg_text = svg.read_text(encoding="utf-8")
        if "<text" not in svg_text:
            raise AssertionError(f"SVG text is not editable for panel {key}")
        missing = [label for label in required_by_panel[key] if label not in svg_text]
        if missing:
            raise AssertionError(f"Missing SVG labels in panel {key}: {missing}")
        reader = PdfReader(str(pdf))
        if len(reader.pages) != 1:
            raise AssertionError(f"Panel {key} PDF must contain one page")
        media_box = reader.pages[0].mediabox
        result["panels"][key] = {
            "source_sha256": sha256(spec.source),
            "source_size": spec.expected_size,
            "png_size": png_size,
            "png_sha256": sha256(png),
            "svg_editable_text": True,
            "pdf_pages": 1,
            "pdf_media_box_points": [float(media_box.width), float(media_box.height)],
            "outputs_nonempty": all(path.stat().st_size > 1000 for path in (png, svg, pdf)),
        }
    return result


if __name__ == "__main__":
    print(json.dumps(run_qa(), indent=2, ensure_ascii=False))
