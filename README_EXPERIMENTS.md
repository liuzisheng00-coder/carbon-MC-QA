# DM2C Experiment Guide

This repository separates deterministic carbon-account experiments from LLM-facing QA experiments. Benchmark expected fields are evaluator-only and must never enter M3.1, M3.2, M3.3, or baseline prompts.

## Current status

| Experiment | Status | Main output | Paper use |
|---|---|---|---|
| P0 factory dataset | Complete, literature-calibrated synthetic data | `outputs/research_experiments/p0_factory_synth/` | Case-data statistics |
| E0 module instances | Partial | E2b module process accounts | Full material + process comparison is blocked because three virtual modules have no material atomic records |
| E1 independent truth | Material complete; revised process pending | `outputs/research_experiments/e1_independent_final_20260711/E1_independent_truth_final.xlsx` | The 80 material truths remain applicable; independently recalculate the revised 17-record process dataset before reporting combined M2-aligned E1 |
| E2 view consistency | Complete | `outputs/research_experiments/latest_e1_e3_summary.json` | Table 2; zero violations on the main KG |
| E2b allocation sensitivity | Complete | `outputs/research_experiments/allocation_sensitivity_report.json` | Fig. 8; A5 process boundary |
| E3 data readiness | Complete as profile/counterfactual | `outputs/research_experiments/latest_e1_e3_summary.json` | Fig. 7; label repair curve as counterfactual unless records are actually repaired and rebuilt |
| E4 benchmark | Complete and frozen V9 | `outputs/research_experiments/e4_benchmark/frozen_20260712_145321_v9_human_pass/` | 150 unique questions; all 28 M2-targeted review decisions passed; deterministic compatibility gate passed |
| E5 real baselines | Complete, paper-ready V9 | `outputs/research_experiments/qa_comparison_v9_m2/qa_comparison_20260712_155634/qa_comparison_report.json` | Full system, graph-only, LLM-only, and unconstrained KG+LLM on the same 150 questions; zero transport errors |
| E6 ablations | Complete, paper-ready V9 | `outputs/research_experiments/e6_real_ablations_v9_m2/paper_ready_audit/e6_v9_paper_ready_audit.json` | Six ablations, 900 case/variant results, 150 unique cases per variant, zero transport errors |
| E7 graph injections | Complete on revised graph | `outputs/research_experiments/e7_m2_aligned_allocation/` and `outputs/research_experiments/e7_m2_aligned_core/` | 105/105 optional-allocation injections and 90/90 applicable core injections passed |
| E8 efficiency | Template ready | `outputs/research_experiments/e8_efficiency/manual_timing_template.csv` | Requires manual and DM2C timings |
| E9 expert review | V9 sample refresh pending | `outputs/research_experiments/e9_expert_review/` | Export a V9 sample, then collect completed scores from independent domain reviewers |
| E10 LLM backends | Pending | none | Requires at least one additional provider beyond DeepSeek |
| M2 manuscript alignment | Current release: layerdedup | `outputs/research_experiments/m2_typed_completed_20260728_layerdedup/` | Revised ontology, direct attribution, process-only retention, optional allocation extension; the V9 150-question results remain historical until Scope B is re-frozen and rerun |

Multi-module extension (2026-09-28): Type B and Type D of the same project were aligned to the Type A accounting basis (`strip_ifc_mep.py`, `align_ifc_to_typea.py`) and evaluated with `scripts/multi_module/` (E1 graph quality, E2 accounts and reconciliation, E3 zero-shot held-out compilation, E5 cost, E6 sensitivity). Report: `outputs/research_experiments/multi_module_20260928/MULTI_MODULE_REPORT.md`; plan: `实验计划_多模块_XL10-13_20260928.md`.

The unified V9 E5/E6 comparison is in `outputs/research_experiments/qa_comparison_v9_m2/qa_comparison_20260712_155634/qa_comparison_report.json`. Exact remaining human actions are listed in `outputs/research_experiments/MANUAL_ACTIONS.md`.

The frozen E4c CarbonQL V3 candidate, `20260719_typed_completed_naturalized_v3`, is superseded after human review and remains immutable. V4, `20260719_typed_completed_naturalized_v4`, has completed its 48/48 human-pass gate with 48 `PASS`, 0 `REVISE`, 0 `REJECT`, and 0 blank decisions; `Coverage=READY`. Its status distribution is 40 `executable`, 4 `unresolved_target`, and 4 `clarification_required`, and all 48 truth rows are `direct_graph`. Eight former hole cases are now resolved from ordinary-language evidence. Eight spurious automatic joins were removed; only `e4c_4_02` and `e4c_4_03` retain explicit joins. The separate acceptance record is `outputs/research_experiments/e4c_carbonql_heldout/accepted_20260719_typed_completed_naturalized_v4_human_pass/e4c_human_review_acceptance.json`; the original four-file machine package remains unchanged. E4c LLM scoring is now allowed by the human gate but has not been run as part of this acceptance step. The frozen V2 candidate, `20260718_typed_completed_naturalized_v2`, remains superseded and unchanged.

The E4d CarbonQL candidate-view package is currently frozen at `outputs/research_experiments/e4d_candidate_views/20260720_typed_completed_candidate_v3/` as a pre-LLM human-review package. V1 and V2 remain immutable and are superseded by V3. All 16 curated questions were question-conditioned for the emission-source perspective and resolved as source-underspecified before three source-conditioned candidate programs were compiled; grounding mode, grouping, and operation remain controlled 2 x 4 x 2 corpus factors. All 48 candidates are graph-executable; this does not constitute human approval or unconstrained full-program synthesis. Across 8 independent grounding-operation units, the realized decision classes are 4 `view_sensitive`, 2 `decomposition_required`, and 2 `source_dependent_trace`. The source-conditioned compare/rank flip rate is 4/4 = 1.0, calculated over the 4 eligible independent compare/rank units rather than treating paired paraphrases as independent outcomes. It remains `NOT READY` and `paper_ready=false` until reviewers record 16/16 `PASS` decisions. No LLM call, prediction artifact, or LLM scoring has occurred for E4d, and this package must not be described as having passed human review or as a paper-ready result.

Historical V3 truth basis is superseded and immutable: it retained 40 `direct_graph` cases and 8 `gold_hole_consistency` cases. Those eight V3 hole-consistency truths are historical and are not V4's independent direct-graph truths.

## Commands

Run deterministic allocation sensitivity:

```powershell
python dm2c_allocation_sensitivity.py
```

Run the evidence-constrained QA system on all benchmark cases:

```powershell
python dm2c_full_qa_experiment_runner.py --benchmark <benchmark.jsonl-or-summary.json> --full
```

Run real LLM-only and unconstrained-KG baselines:

```powershell
python dm2c_real_baseline_runner.py --benchmark <benchmark.jsonl-or-summary.json>
```

Run all six real ablations. M3.1 responses are cached and shared across variants for fairness:

```powershell
python dm2c_real_ablation_runner.py --benchmark <benchmark.jsonl-or-summary.json> --full
```

Aggregate saved full-system, baseline, and ablation observations with the current shared scorer:

```powershell
python dm2c_qa_result_aggregator.py --case-file <full-system.jsonl> --case-file <baselines.jsonl> --case-file <ablations.jsonl>
```

Run real KG mutation and missing-data propagation tests:

```powershell
python dm2c_real_injection_runner.py --per-category 15
```

Run regression tests:

```powershell
python -m pytest -q
```

## Reporting rules

1. V9 is the official frozen benchmark for the revised M2 graph. Use the V9 E5 comparison and E6 audited report rather than the older V7 promotion.
2. E2b's 16.67% flip rate and same-type variation use allocated production-energy carbon at the A5 boundary. Do not describe them as full material-plus-process module totals.
3. E7 mutates real KG edges/properties and derives blocked status with validation rules. Do not cite the older rule-based injection proxy as the main result.
4. A run is paper-ready only when the benchmark is frozen, all intended cases completed, and `transport_error_count` is zero.
5. The optional document-retrieval baseline is a negative control only; DM2C itself does not use RAG.
6. The revised M2 ontology and graph algorithm are paired with frozen V9, the deterministic compatibility gate, revised-graph E5/E6, and revised-graph E7. V7 remains historical evidence for the pre-alignment graph only.
7. Do not compare E6 latency directly with E5 full-system latency: E6 reuses a precomputed M3.1 intent cache and its per-case latency excludes that shared parsing call.

## Revised M2 audit

The manuscript-aligned M2 build is evaluated in two modes: core direct attribution, where shared energy remains process-only, and an optional case-specific allocation extension with an explicit mass basis. Neither mode invokes an LLM.

```powershell
python dm2c_m2_alignment_audit.py --root outputs/research_experiments/m2_typed_completed_20260728_layerdedup
```

The generated `m2_alignment_report.json` is the authority for product-attribution, product-aggregation, context, and allocation-conservation invariants. M3 grounding, status, answer-fidelity, baseline, and ablation statements remain governed by experiments rather than the prose in `manuscript7-11md.docx`.

The current factory dataset contains stage, activity, and workstation fields but no explicit process-template or equipment identifiers. Therefore the revised builder supports those canonical M2 paths, while the current populated graph does not claim them. Add documented process-template applicability and equipment IDs to the factory source before reporting those paths as case-study observations.

## Post-alignment experiment status

- E4 V9: 150/150 deterministic replay cases match revised-graph numeric truth, status, and intent slots. All 28 targeted review decisions passed and the benchmark is frozen.
- E7 revised graph: the optional allocation graph passed 105/105 injections; the core direct-attribution graph passed 90/90 applicable injections. Status accuracy, missing-data disclosure, mutation application, clarification accuracy, and synonym status accuracy are all 1.0. Allocation-basis deletion is correctly not applicable to the core graph.
- E5 V9: full system achieved 1.0 numeric/status/pass rates with zero unsupported answers and hallucinated references. LLM-only produced 0.8467 unsupported-answer rate; unconstrained KG+LLM produced 0.8067.
- E6 V9: removing the validation gate reduced status accuracy to 0.6933; removing status blocking reduced it to 0.9067; removing provenance constraints raised unsupported-answer rate to 0.3067.
- E1: the 80 material hand calculations remain applicable because the IFC material atoms are unchanged. The previous 10 process truths describe the pre-alignment factory inputs and must not be claimed as independent validation of the new 17-record factory dataset.
