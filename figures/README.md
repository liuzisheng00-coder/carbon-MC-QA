# Figure package

This folder contains five publication-oriented figures derived from `experiment.docx`.

- `fig_carbon_accounting_paths`: conceptual accounting-path schematic.
- `fig_system_pipeline`: conceptual system-pipeline schematic.
- `fig_baseline_comparison`: Table 4.7 converted to a grouped bar chart.
- `fig_compiler_backbones`: Table 4.8 converted to an accuracy/latency comparison.
- `fig_question_type_heatmap`: Table 4.9 converted to a model-by-question-type heatmap.

The figures are saved as editable SVG source, vector PDF, and 300 dpi PNG previews. Recreate all outputs with `run_all_figures.ps1`. Quantitative figure scripts read their data from `data/*.csv`; no numeric result is embedded in the drawing code.

`latex_includes.tex` contains draft captions and inclusion snippets. Use the supplied PDF files for PDF-only journal workflows, or use the SVG sources when further editing is needed.
