# Final study data

Download location: [GitHub Release `data-2026-10-02`](https://github.com/liuzisheng00-coder/carbon-MC-QA/releases/tag/data-2026-10-02).

| Asset | Archive bytes | Files | Contents |
| --- | ---: | ---: | --- |
| `final-inputs-20261002.zip` | 20,089,548 | 28 | Three complete final IFC models, two factor workbooks and bound source inputs |
| `final-experiments-20261002.zip` | 404,430,624 | 217 | V16 benchmark and runs, reviewed A/B/D E4c/E4e packages, canonical releases, final sensitivities and September 28/30 reports and diagnostics |

The [manifest](final-data-manifest.json) lists all 245 files, their original relative paths, byte sizes and SHA-256 digests. Large data paths are populated by restoring these assets; cloning the code alone does not restore them.

## Restore and check

From the repository root:

```powershell
python scripts/restore_final_data.py --download
python scripts/restore_final_data.py --verify-only
```

The utility requires Python 3.10+ and only the standard library. It checks archive and member integrity, restores the original directory paths used by the code, leaves identical existing files untouched and refuses to overwrite files with different contents. Verification checks the restored files against the manifest without changing them.

## Scope and recorded limitations

The package retains final study inputs and observations, including full V16 multi-model repeats and the scientific compiler and B/D factory-proxy comparison conditions. Both referenced Type A canonical-release paths are preserved for compatibility. Superseded benchmark versions, pilots, partial runs, console logs, resume checkpoints, manuscript files, figures and model preprocessing copies are excluded.

Human annotation scoring covers 39 rows from a 40-question sample; no completed manual Cypher scoring result is present. Per-type building module counts remain null as originally recorded. B/D factory proxies are modeled assumptions, and the prior four-module synthetic allocation report reused by the final sensitivity discussion retains that provenance. Restoration does not turn these records into completed human evaluations or measured building totals.

See [data preparation](../docs/DATA_PREPARATION.md) and [final experiments](../README_EXPERIMENTS.md) for the case bindings and reproduction commands.
