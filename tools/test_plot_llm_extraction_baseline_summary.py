import importlib.util
import unittest
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


MODULE_PATH = Path(__file__).with_name("plot_llm_extraction_baseline_summary.py")


def load_module():
    spec = importlib.util.spec_from_file_location("extraction_figure", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExtractionFigureTests(unittest.TestCase):
    def test_create_figure_accepts_matplotlib_keyword_tick_labels(self):
        module = load_module()
        matrix = np.array(
            [
                [1.0, 1.0, 1.0],
                [0.60, 0.65, 0.6444],
                [1.0, 0.9833, 0.9889],
                [1.0, 1.0, 1.0],
                [0.60, 0.6333, 0.6333],
            ]
        )
        figure = module.create_figure([30, 60, 90], matrix)
        self.assertEqual(len(figure.axes), 2)
        plt.close(figure)

    def test_colorbar_label_does_not_overlap_the_uncertainty_note(self):
        module = load_module()
        matrix = np.array(
            [
                [1.0, 1.0, 1.0],
                [0.60, 0.65, 0.6444],
                [1.0, 0.9833, 0.9889],
                [1.0, 1.0, 1.0],
                [0.60, 0.6333, 0.6333],
            ]
        )
        figure = module.create_figure([30, 60, 90], matrix)
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        note_box = figure.texts[-1].get_window_extent(renderer)
        colorbar_box = figure.axes[0].child_axes[0].xaxis.label.get_window_extent(renderer)
        self.assertFalse(note_box.overlaps(colorbar_box))
        plt.close(figure)

    def test_single_panel_figure_uses_one_main_axis(self):
        module = load_module()
        matrix = np.array(
            [
                [1.0, 1.0, 1.0],
                [0.60, 0.65, 0.6444],
                [1.0, 0.9833, 0.9889],
                [1.0, 1.0, 1.0],
                [0.60, 0.6333, 0.6333],
            ]
        )
        figure = module.create_single_panel_figure([30, 60, 90], matrix)
        self.assertEqual(len(figure.axes), 1)
        self.assertEqual(len(figure.axes[0].child_axes), 1)
        plt.close(figure)

    def test_red_accuracy_palette_ends_in_deep_red_not_blue(self):
        module = load_module()
        cmap = module.make_accuracy_colormap("red")
        low = cmap(0.0)
        high = cmap(1.0)
        self.assertGreater(high[0], high[1])
        self.assertGreater(high[0], high[2])
        self.assertGreater(low[0], low[1])

    def test_clean_single_panel_omits_header_and_footer_text(self):
        module = load_module()
        matrix = np.array(
            [
                [1.0, 1.0, 1.0],
                [0.60, 0.65, 0.6444],
                [1.0, 0.9833, 0.9889],
                [1.0, 1.0, 1.0],
                [0.60, 0.6333, 0.6333],
            ]
        )
        figure = module.create_single_panel_figure([30, 60, 90], matrix, palette="red", show_context=False)
        self.assertEqual(figure.texts, [])
        plt.close(figure)

    def test_metric_rows_sort_by_descending_mean_accuracy(self):
        module = load_module()
        matrix = np.array(
            [
                [1.0, 1.0, 1.0],
                [0.60, 0.65, 0.6444],
                [1.0, 0.9833, 0.9889],
                [1.0, 1.0, 1.0],
                [0.60, 0.6333, 0.6333],
            ]
        )
        labels, sorted_matrix = module.sort_metrics_by_mean(matrix)
        self.assertEqual(labels, ["IFC class", "Volume", "Materials", "Element name", "All fields"])
        self.assertTrue(np.allclose(sorted_matrix[1], [1.0, 1.0, 1.0]))


if __name__ == "__main__":
    unittest.main()
