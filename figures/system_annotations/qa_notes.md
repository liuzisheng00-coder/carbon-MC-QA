# QA notes: annotated system screenshots

## Rendering contract

- Backend: Python only (Matplotlib + Pillow).
- Source treatment: read-only; no cropping, retouching, recolouring, or removal of interface content.
- Annotation treatment: vector text, outlines, and leader arrows only.
- Terminology: uses `Calculation path`; does not use “traceability” or “provenance”.

## Structural checks

| Panel | Source size | PNG size | SVG text | PDF pages | Source SHA-256 |
|---|---:|---:|---|---:|---|
| a | 938 × 639 | 938 × 639 | editable | 1 | `5dd70cc442705bf55ab495bc799b4a6c5d7528204bb4101eb28fe41f4da37136` |
| b | 554 × 485 | 554 × 485 | editable | 1 | `d39a82c20cc87d28c7337cfce3fe3c7094a138868b501393ea2a43311bc3b23d` |
| c | 938 × 639 | 938 × 639 | editable | 1 | `8306a773644aac8a7107f0d9a639b71342399d87a7b8530098a289ee76eee83d` |

All nine panel exports are non-empty and open successfully. The Python unit suite passes three tests covering source dimensions, normalized annotation geometry, required terminology, export count, file size, and exact PNG dimensions.

## Visual checks

- Panel a: the selected BIM component, grounded QA state, and component-level result are visible without covering the displayed carbon values.
- Panel b: the three perspective labels align with the Product, Material, and Process account rows; outlines isolate the relevant carbon columns.
- Panel c: the calculation path reads left to right as Component → Consumption → Carbon emission → Quantity; the grounded-result arrow terminates at the answer region without crossing the calculation graph.
- Panel labels remain readable at 50% preview scale.
- A vertical contact sheet is provided for rapid comparison of the three final panels.
