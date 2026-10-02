"""Export the capsule chart with larger type and compact outer margins.

The two panels retain the source comparison of pass rate and stable-question
share; this revision changes typography and the spacing needed for that type.
"""

from pathlib import Path

import compare_right_panel_variants as base
from matplotlib.text import Text
from matplotlib.transforms import Bbox, ScaledTranslation


FONT_SCALE = 1.25
OUTPUT_STEM = Path(__file__).resolve().parent / "variant_a_capsule_large_font"


def make_figure():
    rows = base.load_data()
    base.validate_data(rows)
    fig, ax_rate, ax_right = base.build_base(rows)
    base.draw_capsule(ax_right, rows)
    fig.set_dpi(base.PNG_EXPORT_DPI)

    for text in fig.findobj(Text):
        text.set_fontsize(text.get_fontsize() * FONT_SCALE)

    # Leave a physical gap after the rounded ends of the stability bars.
    fraction_transform = ax_right.transData + ScaledTranslation(
        8 / 72, 0, fig.dpi_scale_trans
    )
    for text in ax_right.texts:
        if "/" in text.get_text():
            _, y = text.get_position()
            text.set_position((100, y))
            text.set_ha("left")
            text.set_transform(fraction_transform)

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    left_label_edge = min(
        label.get_window_extent(renderer).x0 for label in ax_rate.get_yticklabels()
    )
    group_x = ax_rate.transAxes.inverted().transform((left_label_edge, 0))[0]
    group_y = {"OPERATION": 8.70, "PERSPECTIVE": 2.95}
    for text in ax_rate.texts:
        if text.get_text() in group_y:
            text.set_position((group_x, group_y[text.get_text()]))

    fig.canvas.draw()
    return fig


def export_figure(fig):
    # Match the established compact left/top crop, allowing space for all glyphs.
    tight = fig.get_tightbbox(fig.canvas.get_renderer())
    pad = 2 / 25.4
    crop = Bbox.from_extents(
        max(0, tight.x0 - pad),
        0,
        max(fig.get_figwidth(), tight.x1 + pad),
        min(fig.get_figheight(), tight.y1 + pad),
    )
    for extension in ("png", "svg"):
        fig.savefig(
            OUTPUT_STEM.with_suffix(f".{extension}"),
            dpi=base.PNG_EXPORT_DPI,
            facecolor="white",
            bbox_inches=crop,
        )
    return crop


if __name__ == "__main__":
    figure = make_figure()
    bounds = export_figure(figure)
    print(f"Font scale: {FONT_SCALE}; export mm: {bounds.width * 25.4:.2f} x {bounds.height * 25.4:.2f}")
    base.plt.close(figure)
