import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image


MODULE_PATH = Path(__file__).with_name("render_gephi_workspace_highres.py")


def load_module():
    spec = importlib.util.spec_from_file_location("workspace_renderer", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RenderWorkspaceHighResolutionTests(unittest.TestCase):
    def test_view_bounds_match_requested_aspect_ratio(self):
        module = load_module()
        bounds = module.compute_view_bounds(
            {"a": {"x": -10, "y": -5}, "b": {"x": 10, "y": 5}},
            aspect_ratio=2.0,
            padding=0.0,
        )
        xmin, xmax, ymin, ymax = bounds
        self.assertAlmostEqual((xmax - xmin) / (ymax - ymin), 2.0)
        self.assertLessEqual(xmin, -10)
        self.assertGreaterEqual(xmax, 10)

    def test_render_creates_requested_size_and_nonwhite_pixels(self):
        module = load_module()
        workspace = {
            "appearance": {
                "nodesSize": {"type": "fixed", "value": 20},
                "edgesSize": {"type": "fixed", "value": 2},
                "edgesColor": {"type": "fixed", "value": "#4A4A4AFF"},
            },
            "graphDataset": {
                "layout": {"a": {"x": -10, "y": 0}, "b": {"x": 10, "y": 0}},
                "nodeData": {
                    "a": {"color": "rgb(255, 0, 0)", "label": "A"},
                    "b": {"color": "rgb(0, 0, 255)", "label": ""},
                },
                "fullGraph": {"edges": [{"source": "a", "target": "b"}]},
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "graph.png"
            module.render_workspace(workspace, output, target_size=(400, 200), padding=0.05)
            image = Image.open(output)
            self.assertEqual(image.size, (400, 200))
            self.assertTrue(any(pixel[:3] != (255, 255, 255) for pixel in image.getdata()))

    def test_edge_width_scales_from_reference_pixels(self):
        module = load_module()
        self.assertEqual(module.scale_reference_pixels(0.6, 6756), 2)

    def test_convex_hull_encloses_outer_label_box_corners(self):
        module = load_module()
        hull = module.convex_hull([(0, 0), (3, 0), (3, 2), (0, 2), (1, 1)])
        self.assertEqual(hull, [(0, 0), (3, 0), (3, 2), (0, 2)])

    def test_expanded_hull_moves_boundary_outward(self):
        module = load_module()
        expanded = module.expand_hull([(0, 0), (10, 0), (10, 10), (0, 10)], margin=5)
        self.assertLess(expanded[0][0], 0)
        self.assertLess(expanded[0][1], 0)

    def test_boundary_outer_color_matches_the_supplied_purple_reference(self):
        module = load_module()
        self.assertEqual(module.BOUNDARY_OUTER_COLOR, (157, 132, 195, 255))

    def test_boundary_outlines_keep_purple_outside_the_inner_line(self):
        module = load_module()
        inner, outer = module.boundary_outline_hulls([(0, 0), (10, 0), (10, 10), (0, 10)], scale=1)
        self.assertLess(outer[0][0], inner[0][0])
        self.assertLess(outer[0][1], inner[0][1])

    def test_single_boundary_hull_is_outside_the_labels(self):
        module = load_module()
        boundary = module.single_boundary_hull([(0, 0), (10, 0), (10, 10), (0, 10)], scale=1)
        self.assertLess(boundary[0][0], 0)
        self.assertLess(boundary[0][1], 0)

    def test_fluorescent_boundary_is_brighter_and_thicker_than_pastel(self):
        module = load_module()
        pastel = module.get_boundary_style("pastel")
        fluorescent = module.get_boundary_style("fluorescent")
        self.assertEqual(fluorescent["color"], (216, 60, 255, 255))
        self.assertGreater(fluorescent["line_width"], pastel["line_width"])
        self.assertGreater(fluorescent["halo_width"], fluorescent["line_width"])

    def test_green_and_yellow_fluorescent_styles_have_expected_contrast(self):
        module = load_module()
        green = module.get_boundary_style("fluorescent_green")
        yellow = module.get_boundary_style("fluorescent_yellow")
        self.assertEqual(green["color"], (57, 255, 20, 255))
        self.assertEqual(yellow["color"], (255, 242, 0, 255))
        self.assertEqual(green["line_width"], 10.0)
        self.assertEqual(green["halo_width"], 14.0)
        self.assertEqual(green["halo_color"], (0, 0, 0, 255))
        self.assertEqual(yellow["halo_color"], (70, 70, 70, 255))


if __name__ == "__main__":
    unittest.main()
