import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).with_name("create_calculation_chain_magnifier_composite.py")
GEXF_PATH = (
    Path(__file__).parents[1]
    / "outputs"
    / "research_experiments"
    / "m2_typea1_full_en_layerdedup_20260731_d_spread"
    / "multigranular_carbon_kg_representative_calculation_subgraph.gexf"
)


def load_module():
    spec = importlib.util.spec_from_file_location("calculation_chain_magnifier", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CalculationChainMagnifierTests(unittest.TestCase):
    def test_default_canvas_size_is_publication_resolution(self):
        module = load_module()
        self.assertEqual(module.DEFAULT_CANVAS_SIZE, (9000, 4500))

    def test_detail_panel_style_is_large_enough_for_true_enlargement(self):
        module = load_module()
        self.assertGreaterEqual(module.DETAIL_LABEL_FONT_SIZE, 10.0)
        self.assertGreaterEqual(module.DETAIL_NODE_BASE_SIZE, 360)
        self.assertGreaterEqual(module.RIGHT_PANEL_BOUNDS[2] - module.RIGHT_PANEL_BOUNDS[0], 0.40)

    def test_revision_uses_half_size_magnifier_and_approved_title(self):
        module = load_module()
        self.assertEqual(module.DETAIL_TITLE, "Calculation Information Subgraph")
        self.assertAlmostEqual(module.MAGNIFIER_RADIUS_FRACTION, 0.0275)

    def test_composite_adds_title_ink_above_detail_panel(self):
        module = load_module()
        image = Image.new("RGBA", (1800, 900), "white")

        module.draw_detail_title(ImageDraw.Draw(image, "RGBA"), image.size)

        self.assertGreater(module.nonwhite_count_in_box(image, (1040, 20, 1795, 130)), 500)

    def test_render_detail_graph_creates_readable_high_resolution_panel(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "detail.png"

            result = module.render_detail_graph(GEXF_PATH, output, size=(1600, 1200))

            with Image.open(result) as image:
                self.assertEqual(image.size, (1600, 1200))
                self.assertGreater(module.count_nonwhite_pixels(image), 10_000)

    def test_cartoon_magnifier_uses_approved_palette(self):
        module = load_module()
        image = Image.new("RGBA", (600, 400), "white")

        module.draw_cartoon_magnifier(ImageDraw.Draw(image, "RGBA"), (250, 180), 90, (440, 330))

        colours = set(image.getdata())
        self.assertIn(module.LENS_COLOUR, colours)
        self.assertIn(module.RIM_COLOUR, colours)
        self.assertIn(module.HANDLE_COLOUR, colours)

    def test_compose_figure_places_two_nonempty_panels(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            full_graph = Image.new("RGB", (800, 800), "white")
            ImageDraw.Draw(full_graph).ellipse((50, 50, 750, 750), fill="#17becf")
            full_graph_path = temp_path / "full.png"
            full_graph.save(full_graph_path)
            detail = Image.new("RGB", (800, 600), "white")
            ImageDraw.Draw(detail).rectangle((40, 40, 760, 560), fill="#9467bd")
            detail_path = temp_path / "detail.png"
            detail.save(detail_path)
            output = temp_path / "composite.png"

            module.compose_figure(full_graph_path, detail_path, output, (1800, 900))

            with Image.open(output) as image:
                self.assertEqual(image.size, (1800, 900))
                self.assertGreater(module.nonwhite_count_in_box(image, (20, 20, 990, 880)), 10_000)
                self.assertGreater(module.nonwhite_count_in_box(image, (1080, 80, 1780, 820)), 5_000)

    def test_cli_renders_detail_before_composing_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            full_graph = Image.new("RGB", (800, 800), "white")
            ImageDraw.Draw(full_graph).ellipse((50, 50, 750, 750), fill="#17becf")
            full_graph_path = temp_path / "full.png"
            full_graph.save(full_graph_path)
            detail_path = temp_path / "detail.png"
            output_path = temp_path / "composite.png"

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--full-graph",
                    str(full_graph_path),
                    "--detail-gexf",
                    str(GEXF_PATH),
                    "--detail-cache",
                    str(detail_path),
                    "--output",
                    str(output_path),
                    "--width",
                    "1200",
                    "--height",
                    "600",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(detail_path.exists())
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
