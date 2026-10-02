# Data preparation for the final workflow

The code release contains the final case configuration/evidence subset, ontology and frontend demo JSON. Original IFCs, carbon-factor workbooks and frozen benchmark/result packages are provided separately.

## Canonical graph inputs

For the chosen configuration in `inputs/case_study/`, prepare the bound IFC model, factor workbook, material evidence, ontology, factory inputs and target map. Bindings include paths, SHA-256 digests and byte sizes. Preserve the original data for a historical reproduction; use a separate configuration and recalculate bindings for a new dataset.

```powershell
python dm2c_m23_canonical_release.py --case-config <case-config.json> --out-root <output-directory> --release-id <release-id>
```

The final main cases are Type A English-energy and Type B/D aligned mass-proxy. Type B/D as-recorded and equal-route cases support the final sensitivity comparison. The B/D as-recorded configurations also provide the base bindings for factory-proxy preparation. These are different experiment conditions in the final paper, not redundant draft versions.

Many case paths are relative to the case configuration and reference `outputs/research_experiments/`. Restore those files before building. The retained question rewrite/review specifications support the current reviewed held-out benchmark code.

## Application and paper experiments

Set `DM2C_CARBONQL_RELEASE` to a compatible canonical release or use the supported project-upload flow. API defaults that refer to local research output directories do not imply those directories are bundled.

See [the final experiment guide](../README_EXPERIMENTS.md) for the V16 benchmark and Type A/B/D release references. Held-out compiler, main QA, diagnostic and sensitivity result files are required for exact reproduction. Obtain the frozen artifacts and their provenance from the repository owner.

Frontend `public/demo-*.json` files are generated examples used by the demo and tests. They are not the original IFC dataset. The retained Python tests use generated fixtures and do not require the full paper data.

API keys belong in the local process environment. The application does not automatically read `.env` files.
