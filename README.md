# carbon-MC-QA

Research code for multi-stakeholder carbon question answering in modular construction (MC), implemented by the DM2C framework. It connects IFC/BIM data, deterministic carbon accounting, a knowledge graph and evidence-constrained queries.

本仓库保存论文相关的碳核算、知识图谱、CarbonQL 问答、实验与前端代码。当前发布的是代码与部分示例数据；完整论文实验还需要对应的 IFC、因子工作簿、工厂数据和冻结实验数据，详见[数据准备说明](docs/DATA_PREPARATION.md)。

## Repository map

| Path | Purpose |
| --- | --- |
| `dm2c_m23_*.py`, `dm2c_multigranular_carbon_kg.py` | Canonical graph construction, unit validation and carbon accounting |
| `dm2c_carbonql*.py`, `dm2c_qa_*.py`, `dm2c_m3_*.py` | Query compilation, execution, validation and grounded answers |
| `dm2c_api_server.py` | FastAPI backend |
| `dm2c_frontend_fragments_full/` | Main application: upload, BIM viewer, QA and account views |
| `dm2c_frontend_fragments/` | Optional minimal BIM viewer for diagnostics |
| `dm2c_frontend/` | Earlier frontend retained for comparison |
| `scripts/multi_module/`, `scripts/diagnostics/` | Multi-module experiments and diagnostic analyses |
| `tests/` | Unit and historical integration tests |
| `inputs/`, `mic-carbon-*.ttl` | Case configurations, material evidence, ontology and SHACL shapes |
| `tools/`, `figures/` | Graph exports, analysis and figure utilities |
| `legacy/` | Optional legacy graph mode |

Some historical filenames contain `rag`; the main DM2C QA workflow uses graph evidence and CarbonQL. Document retrieval code is retained for historical/baseline experiments.

## Installation

The initial source release was checked with Python 3.12 on Windows. Python dependency versions record that environment; a fresh environment install is separate from source verification. Use Node.js 22 or newer and npm 10.5.1 or newer for the frontend (build verified with Node.js 24.19.0). Existing `package-lock.json` files are included.

```powershell
git clone https://github.com/liuzisheng00-coder/carbon-MC-QA.git
cd carbon-MC-QA
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Optional plotting/manuscript-export utilities also need `requirements-research.txt` and may refer to historical result paths.

## Offline checks

This subset uses in-memory or temporary data and does not call an LLM or need the original IFC files:

```powershell
python -m pytest -q tests/test_dm2c_m23_units.py tests/test_dm2c_m23_calculation.py tests/test_dm2c_carbonql.py tests/test_dm2c_carbonql_executor.py tests/test_dm2c_e6_result_audit.py tests/test_dm2c_qa_result_aggregator.py
```

Other tests cover historical releases, workbooks and frozen outputs. Running the entire test directory requires those additional data packages.

## Start the application

From the repository root, start the API:

```powershell
python -m uvicorn dm2c_api_server:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd dm2c_frontend_fragments_full
npm ci
npm test
npm run dev
```

Open <http://127.0.0.1:5173>. Upload files and create a project in the main interface. Project-specific calculations and QA require valid data and configuration. API health: <http://127.0.0.1:8000/health>.

The current frontend test suite has three pre-existing source-assertion failures; the production build succeeds. See [initial verification results](docs/INITIAL_VERIFICATION.md) before interpreting `npm test` results.

Do not use `--reload` while keeping an active project: project state is in memory. Start the two frontends using port 5173 separately; the diagnostic viewer uses 5174. Frontend WASM is generated from npm dependencies by the preparation script and is excluded from Git.

## LLM and graph configuration

`.env.example` lists supported settings without credentials. The application reads process environment variables and does **not** automatically load `.env` files. Set these in the API terminal before starting it, with your own key:

```powershell
$env:DM2C_LLM_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "<your-own-key>"
$env:DEEPSEEK_MODEL = "deepseek-chat"
# Optional: point to an existing compatible canonical graph release.
$env:DM2C_CARBONQL_RELEASE = "C:\path\to\canonical-release"
```

OpenAI-compatible and Gemini configuration paths are also implemented. LLM experiments require provider credentials and can incur API charges. Offline checks above require no credentials.

## Paper experiments and data

See [README_EXPERIMENTS.md](README_EXPERIMENTS.md) for the research workflow and [data preparation](docs/DATA_PREPARATION.md) for release construction. The experiment guide is a historical development record: its result/status statements refer to the original workspace and do not establish full reproducibility from this public checkout.

Included: code, small case configuration/evidence files, and frontend demo JSON. Excluded: full raw IFC files, Excel workbooks, manuscript drafts, downloaded literature, generated experiment outputs and credentials. No open-source license is selected in this initial upload; the repository owner can add one later.
