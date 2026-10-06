# MC²QA

Final research implementation of the MC²QA framework for multi-stakeholder carbon question answering in modular construction.

本仓库仅保留最终应用、论文最终实验入口、必要配置及核心测试。最终实验基于 V16 问答基准、Type A/B/D 多模块验证和 2026-09-30 诊断分析。完整最终 IFC、因子工作簿和冻结实验记录通过本仓库 Release 附件提供，下载并还原后才出现在原始相对路径。旧实验修补脚本、备用前端和文稿制作工具已从当前目录移除。

## Code map

| Location | Purpose |
| --- | --- |
| `dm2c_api_server.py` | FastAPI application |
| `dm2c_frontend_fragments_full/` | The single retained application frontend |
| `dm2c_m23_*.py`, `dm2c_multigranular_carbon_kg.py` | Canonical graph construction and carbon accounting |
| `dm2c_carbonql*.py`, `dm2c_m3_*.py` | Query compilation, execution and graph evidence |
| `dm2c_full_qa_experiment_runner.py`, `dm2c_real_baseline_runner.py` | Final QA and baseline evaluation |
| `scripts/multi_module/` | Type A/B/D validation and sensitivity analysis |
| `scripts/diagnostics/` | V16 control-policy/compiler ablations and diagnostics |
| `inputs/`, `mic-carbon-*.ttl` | Required case bindings, evidence, question specifications and ontology |
| `ifc-kg/dm2c_pipeline/translate_typea1_ifc_names.py` | Required IFC naming helper for the final A/B/D preparation |
| `tests/` | Core tests with generated fixtures |
| `data/` | Final data manifest and download/restore instructions |

Some retained modules have historical `v2` or `rag` names because the current application imports their shared clients and utilities. They are active dependencies, not alternate releases. Earlier compiler variants remain available only where needed for the final paper's ablation comparisons.

## Install

Use Python 3.12, Node.js 22 or newer and npm 10.5.1 or newer. Python requirements record the development environment; frontend dependencies are locked by `package-lock.json`.

```powershell
git clone https://github.com/liuzisheng00-coder/carbon-MC-QA.git
cd carbon-MC-QA
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

## Restore the final study data

From the repository root, download and restore the two assets from [release `data-2026-10-02`](https://github.com/liuzisheng00-coder/carbon-MC-QA/releases/tag/data-2026-10-02):

```powershell
python scripts/restore_final_data.py --download
python scripts/restore_final_data.py --verify-only
```

The restore utility needs only Python 3.10+ and its standard library. The archives contain 245 files, including three complete final IFC models, two factor workbooks, bound inputs and frozen final experiment data. It preserves original relative paths, checks SHA-256 digests and refuses to overwrite existing files whose contents differ. See [the data package guide](data/README.md) and [file manifest](data/final-data-manifest.json).

## Run the application

From the repository root:

```powershell
python -m uvicorn dm2c_api_server:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd dm2c_frontend_fragments_full
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Upload inputs and create a project in this application. Project-specific calculations and answers require valid IFC, factor and graph data; use the restored final study inputs for the paper dataset. Frontend demo JSON contains generated examples. Do not use `--reload` while using active projects, since project state is held in memory.

The application reads process environment variables. `.env.example` is a reference template; it is not loaded automatically. Set your provider key in the API terminal, and set `DM2C_CARBONQL_RELEASE` when using a prepared canonical release. LLM calls require credentials and may incur provider charges.

## Test and reproduce

```powershell
python -m pytest -q tests
```

Frontend checks, from `dm2c_frontend_fragments_full`:

```powershell
npm test
npm run build
```

See [verification results](docs/VERIFICATION.md), including the pre-existing API-status and frontend source-assertion failures. See [final experiment commands](README_EXPERIMENTS.md) and [data preparation](docs/DATA_PREPARATION.md) before running paper experiments.

The data package retains only final study inputs, observations and required scientific comparison conditions. It excludes superseded pilots, manuscript drafts, model preprocessing copies, downloaded literature, credentials and caches. No open-source license has been selected.
