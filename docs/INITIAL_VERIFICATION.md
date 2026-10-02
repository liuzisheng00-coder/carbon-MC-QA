# Initial upload verification — 2026-10-02

Checks ran on the prepared upload directory, separately from the original research workspace.

| Check | Result |
| --- | --- |
| Python syntax | All 201 Python source files parsed successfully |
| Six-file offline pytest subset documented in README | 181 passed |
| `import dm2c_api_server` | Passed |
| Main frontend `npm ci --no-audit --no-fund` | Installed from the existing lockfile |
| Main frontend tests with Node.js 24.19.0 | 76 passed, 3 failed |
| Main frontend `npm run build` with Node.js 24.19.0 | Passed, with a large-chunk size warning |

## Known frontend test failures

The identical 79-test command was run in both the original research frontend and the prepared release. Both reported the same three failures, so they were not introduced by copying the repository:

1. `fullVariantIsolation.test.mjs:49`: source regex for the QA submit callback no longer matches the current callback.
2. `interfaceModel.test.mjs:663`: source regex expects older IFC upload-description text.
3. `interfaceModel.test.mjs:1395`: source regex expects older layout class names.

The tests and application source are retained as found; this upload does not claim the entire frontend test suite passes. These failures do not prevent a production build, but a successful build is not a substitute for live BIM/QA acceptance testing.

## Verification boundaries

No LLM calls or full paper experiments were run. No live project upload, IFC rendering acceptance, or exact reproduction of the paper's numerical results was performed. The complete historical Python suite and optional frontends were not tested in this release. Python dependencies were checked in the existing Python environment; a clean-environment Python installation has not been verified.

Pattern scans of selected text files found no real API tokens, private keys or embedded credentials. Generated caches, installed npm dependencies, build output and original data files are excluded from the Git index by `.gitignore`.
