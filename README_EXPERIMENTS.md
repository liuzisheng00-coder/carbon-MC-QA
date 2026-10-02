# Final paper experiments

This guide covers the retained final experiment workflow. The reference versions are identified from the current multi-module registry and the 2026-09-30 diagnostic scripts. Earlier V9/V10 preparation, benchmark repair rounds and superseded experiments are not maintained in this repository.

## Reference data

- Main QA benchmark: `outputs/research_experiments/e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc/e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl`.
- Type A canonical context used by final diagnostics: `outputs/research_experiments/multi_module_20260928/releases/typea_clean`.
- Multi-module registry: `scripts/multi_module/mm_common.py`. The main B/D cases use the aligned mass-proxy configurations; as-recorded and equal-route configurations are retained for the final sensitivity comparisons.
- Held-out compiler evaluation: the reviewed E4c packages and E4e compiler runs referenced by `scripts/multi_module/mm_e3_heldout_summary.py`.

These frozen data and result directories are external to the code release. Prepare the exact inputs before executing the commands. Do not substitute another benchmark while claiming reproduction of these versions.

## Full system and baselines

The shared runners retain compatibility defaults. Pass the final benchmark explicitly:

```powershell
$benchmark = "outputs/research_experiments/e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc/e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl"
$release = "outputs/research_experiments/multi_module_20260928/releases/typea_clean"
python dm2c_full_qa_experiment_runner.py --benchmark $benchmark --kg-dir $release --allow-synthetic --full
python dm2c_real_baseline_runner.py --benchmark $benchmark --kg-dir $release --allow-synthetic
```

These commands call the configured LLM. Set the provider credentials first. Use new output/run identifiers for each repetition and preserve all source artifacts.

## Final diagnostics and ablations

| Script in `scripts/diagnostics/` | Purpose |
| --- | --- |
| `run_e6_v16.py` | V16 execution-policy ablations with compiled-program records |
| `run_compiler_stages_v16.py` | Compiler-stage ablations on the same V16 benchmark |
| `rule_compiler_baseline.py` | Deterministic rule-compiler comparator |
| `diagnostics_report.py` | Error taxonomy, slot metrics and paired comparisons from saved observations |
| `export_prompts.py` | Export the actual prompts/contracts used by the retained workflow |
| `human_task_sheets.py` | Prepare and score the human annotation/manual-query comparison sheets |

`execute_unvalidated.py` is a helper for the deliberately unvalidated ablation. It is not the standard execution policy. The V1–V3 compiler conditions are scientific comparators of the final V4 compiler, so they remain part of the final experiment code.

## Type A/B/D validation

Run the retained scripts in `scripts/multi_module/` against the releases registered in `mm_common.py`:

1. `mm_make_factory_proxy.py`: prepare the B/D mass-scaled factory proxies.
2. Build the corresponding canonical releases with `dm2c_m23_canonical_release.py` and their case configurations.
3. `mm_e1_graph_quality.py`: graph quality and accounting checks.
4. `mm_e2_accounting.py`: account reconciliation and independent recalculation.
5. `mm_e3_heldout_summary.py`: summarize the saved held-out compiler runs.
6. `mm_e5_cost.py`: aggregate build/compilation cost observations.
7. `mm_e6_sensitivity.py` and `mm_e6b_allocation_basis.py`: factor and attribution sensitivity.
8. `mm_report.py`: consolidate the generated observations.

Some scripts aggregate existing observations rather than generate them. In particular, the held-out and cost summaries require the frozen compiler runs referenced in their source. Use `dm2c_carbonql_compiler_ablation.py --help` for the held-out compiler runner, providing the correct reviewed benchmark and release explicitly.

## Interpretation

Keep full-system, baseline and ablation inputs aligned. Report incomplete/failed calls and data assumptions. The factory proxy is an explicitly modeled sensitivity assumption, not an independently measured factory dataset for B/D. No numerical experiment results were regenerated during repository cleanup.
