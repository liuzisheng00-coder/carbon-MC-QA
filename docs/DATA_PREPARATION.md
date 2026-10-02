# Data preparation for the final workflow

The code checkout contains the final case configuration/evidence subset, ontology and frontend demo JSON. Complete final IFCs, carbon-factor workbooks, bound source inputs and frozen benchmark/result packages are distributed through this repository's [release `data-2026-10-02`](https://github.com/liuzisheng00-coder/carbon-MC-QA/releases/tag/data-2026-10-02). Their original data paths are populated after download and restoration.

## Download and verify

Run from the repository root:

```powershell
python scripts/restore_final_data.py --download
python scripts/restore_final_data.py --verify-only
```

Restoration needs Python 3.10+ and only the standard library. The two archives contain 245 files and preserve the relative paths used by the case bindings and experiment scripts. The utility checks the archive and file digests against [the manifest](../data/final-data-manifest.json) and refuses to overwrite an existing file with different contents. See [archive contents and sizes](../data/README.md).

## Canonical graph inputs

For the chosen configuration in `inputs/case_study/`, use the restored bound IFC model, factor workbook, material evidence, ontology, factory inputs and target map. Bindings include paths, SHA-256 digests and byte sizes. Preserve the frozen inputs for a historical reproduction; use a separate configuration and recalculate bindings for a new dataset.

```powershell
python dm2c_m23_canonical_release.py --case-config <case-config.json> --out-root <output-directory> --release-id <release-id>
```

The final main cases are Type A English-energy and Type B/D aligned mass-proxy. Type B/D as-recorded and equal-route cases support the final sensitivity comparison. The B/D as-recorded configurations also provide the base bindings for factory-proxy preparation. These are different experiment conditions in the final paper, not redundant draft versions.

Many case paths are relative to the case configuration and reference `outputs/research_experiments/`. Restore those files before building. The retained question rewrite/review specifications support the current reviewed held-out benchmark code.

## Application and paper experiments

Set `DM2C_CARBONQL_RELEASE` to a restored compatible canonical release or use the supported project-upload flow. Download and restore the data before relying on API defaults that refer to local research output directories.

See [the final experiment guide](../README_EXPERIMENTS.md) for the V16 benchmark and Type A/B/D release references. The experiment archive includes the required held-out compiler, main QA, diagnostic and sensitivity observations. It also preserves the recorded limitations: 39 scored human annotation rows, no completed manual Cypher scoring result, and null per-type building module counts.

The package contains only final study inputs and required scientific comparison conditions. It excludes superseded pilots, manuscript files and model preprocessing copies. Historical source references within frozen records describe provenance; they do not require restoring every earlier dataset.

Frontend `public/demo-*.json` files are generated examples used by the demo and tests. They are not the original IFC dataset. The retained Python tests use generated fixtures and do not require the full paper data.

API keys belong in the local process environment. The application does not automatically read `.env` files.
