# Verification of the final-only repository — 2026-10-02

The repository was reduced from 351 files to 131 files by retaining the final application, final experiment entrypoints, their complete local import dependencies, required inputs and core tests. Original research files outside the Git checkout were not changed.

| Check | Result |
| --- | --- |
| Retained Python imports | Static checks include nested imports and compare against the pre-cleanup module inventory |
| Python source syntax | All 73 retained Python files parse successfully |
| Core Python tests: `python -m pytest -q tests` | 405 passed, 1 pre-existing assertion failed |
| Main frontend tests (Node.js 24.19.0) | 76 passed, 3 pre-existing source assertions failed |
| Main frontend production build | Passed; Vite reports large generated chunks |

## Existing assertions that need separate review

The Python failure is `test_unusable_release_is_reported_rather_than_silently_answered` in `tests/test_dm2c_carbonql_service.py`. The test expects `canonical_release_rejected`; the current API reports `canonical_release_unavailable` for an empty configured release directory. The same individual test was rerun in the original research workspace and failed identically. The cleanup did not modify that API behavior or suppress the test.

The frontend retains the same three failures seen before cleanup:

- `fullVariantIsolation.test.mjs`: an older source regex for the QA submit callback.
- `interfaceModel.test.mjs`: older IFC upload-description text.
- `interfaceModel.test.mjs`: older layout class names.

No new frontend failures appeared after removing the two unused frontend directories. Successful compilation does not replace live IFC/QA acceptance testing.

## Changes and limits

Business logic was preserved. One test now imports `write_task10_release` directly from its defining fixture module instead of re-exporting it through a removed historical test helper. This avoids retaining obsolete experiment modules solely for that helper. The existing `ifc-kg/dm2c_pipeline/translate_typea1_ifc_names.py` helper was added unchanged from the research workspace because `align_ifc_to_typea.py` imports it; the initial upload had omitted it. The alignment module now imports successfully.

The full paper experiments were not rerun and no paid LLM calls were made. Original IFCs, workbooks and frozen output/benchmark packages remain external. The final experiment guide specifies the V16 and Type A/B/D inputs explicitly because some reusable runners retain older compatibility defaults.
