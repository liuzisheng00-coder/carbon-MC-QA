# Data and reproduction scope

## Included examples

This is a source-code release with selected configuration, material-evidence and frontend demonstration JSON files. Frontend `public/demo-*.json` files support the demo and interface tests; they are not the original IFC dataset or a complete experiment package.

`inputs/case_study/*.json` manifests preserve original paths, SHA-256 digests and sizes. A filename containing `fixture` does not imply that its data are bundled: `m23_canonical_v2_case_fixture.json` still references a real IFC model, a factor workbook and consolidated factory inputs.

## Canonical graph construction

Prepare the inputs referenced by your chosen case configuration:

1. An IFC model.
2. A carbon-factor Excel workbook with the schema expected by `FactorLibrary`.
3. The ontology (`mic-carbon-ontology.ttl` is included).
4. Material-evidence JSON (examples are included).
5. Factory process/energy inputs and the applicable factory-to-product map, when enabled by the configuration.

Resolve paths according to the case configuration and verify the recorded SHA-256/size bindings. Existing configurations often use Windows-style relative paths into `outputs/research_experiments/`; those files are not included. For a new dataset, create a separate configuration with paths and digests calculated from the actual files and document their provenance. Do not describe new data as an exact reproduction of a historical frozen release.

```powershell
python dm2c_m23_canonical_release.py --case-config <case-config.json> --out-root <output-directory> --release-id <release-id>
```

After building a compatible release, set `DM2C_CARBONQL_RELEASE` to its directory or use the application's supported project/release upload flow. Default historical release paths in the source may not exist in a fresh clone.

## Paper evaluations

Historical E4–E7, multi-module and diagnostic runners additionally require particular frozen benchmark JSON/JSONL files, graphs and/or saved observations under `outputs/research_experiments/`. These artifacts are not in this first upload. Obtain the corresponding data and provenance from the repository owner before attempting exact numerical reproduction.

`README_EXPERIMENTS.md` retains historical workflow/status notes; it is not verification of this public checkout and does not replace frozen input/result artifacts.

## Checks without original data

Use the explicit six-file pytest command in the root README. Those tests create temporary data. Frontend tests use included demo JSON or mocked objects. Full BIM upload, graph construction and live LLM answers require application data/configuration.

## Excluded material

Raw IFC models, Excel workbooks, manuscript drafts, downloaded reference PDFs, complete generated output directories, caches, dependency folders and credentials are excluded. Some plotting/export utilities retain historical machine-specific paths; supply appropriate inputs or adapt their local path settings before use. API keys belong only in the local process environment.
