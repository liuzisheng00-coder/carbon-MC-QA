# -*- coding: utf-8 -*-
"""Assemble MULTI_MODULE_REPORT.md from the E1/E2/E3/E5/E6 outputs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import MODULES, OUT_ROOT, ROOT, load_json, markdown_table, write_text  # noqa: E402


def main() -> None:
    e1 = load_json(OUT_ROOT / "E1/e1_graph_quality.json")["rows"]
    e2 = load_json(OUT_ROOT / "E2/e2_module_accounts.json")
    e3_all = load_json(OUT_ROOT / "E3/e3_heldout_summary.json")
    e3 = e3_all["summary"]
    solved = e3_all["per_case_solved"]
    case_ids = set(solved["Type A"])
    solved_all = sum(1 for c in case_ids if all(solved[l].get(c) for l in solved))
    failed_all = sum(1 for c in case_ids if not any(solved[l].get(c) for l in solved))
    e5 = load_json(OUT_ROOT / "E5/e5_cost_report.json")
    e6 = load_json(OUT_ROOT / "E6/e6_sensitivity.json")
    labels = [m.label for m in MODULES]
    acc = {a["module"]: a for a in e2["accounts"]}
    proxy = {a["module"]: a for a in load_json(OUT_ROOT / "E2/e2_process_variant_accounts.json")} if (OUT_ROOT / "E2/e2_process_variant_accounts.json").exists() else {}

    head = [
        "# Multi-module experiments (Type A / B / D) — consolidated report",
        "",
        "Date: 2026-09-28. Scope: one project, one factory, three module types on the English accounting basis of the paper's Type A case. "
        "Scripts: `scripts/multi_module/`. Plan: `实验计划_多模块_XL10-13_20260928.md`.",
        "",
        "## Headline table",
        "",
        markdown_table(
            ["Indicator"] + labels,
            [
                ["Graph nodes / edges"] + [f"{r['nodes']:,} / {r['edges']:,}" for r in e1],
                ["Accepted / rejected calculation records"] + [f"{r['accepted_records']} / {r['rejected_records']}" for r in e1],
                ["Coverage"] + [f"{100 * r['coverage']:.1f}%" for r in e1],
                ["Material carbon, A1–A3 (kgCO2e)"] + [f"{acc[l]['material_total']:,.0f}" for l in labels],
                ["Product-perspective total (kgCO2e)"] + [f"{acc[l]['product_total']:,.0f}" for l in labels],
                ["Product total per m² floor (kgCO2e/m²)"] + [f"{acc[l]['product_per_m2']:,.0f}" for l in labels],
                ["Independent recalculation difference (kgCO2e)"] + [f"{e2['recalculation'][m.key]['difference']:.1e}" for m in MODULES],
                ["Rejected-record upper bound (share of material)"] + [f"{100 * e6[l]['rejected_bound']['upper_bound_kgCO2e'] / e6[l]['scenarios']['S0 baseline']['material']:.1f}%" for l in labels],
                ["Held-out CarbonQL answer exact (V4, 5×48)"] + [f"{100 * e3[l]['V4']['answer_exact_match']['mean']:.1f}% ± {100 * e3[l]['V4']['answer_exact_match']['sd']:.1f}" for l in labels],
                ["Held-out accounting boundary"] + [f"{100 * e3[l]['V4']['boundary_match']['mean']:.1f}% ± {100 * e3[l]['V4']['boundary_match']['sd']:.1f}" for l in labels],
                ["Release build (s) / compile mean (ms)"] + [f"{e5['build_seconds'][l]} / {e5['latency'][l]['compile_mean']:.0f}" for l in labels],
                ["USD per question (deepseek-chat)"] + [f"{e5['tokens'][l]['usd_per_question_mean']:.4f}" for l in labels],
            ],
        ),
        "",
        "## What each experiment shows",
        "",
        f"- **E1** The three IFC models map onto the same 16 ontology classes and 20 relations; graphs are within 5% of each other in size; validation coverage is 95–96% with the same two rejection reasons; alignment and Cypher gates pass. Build takes {min(e5['build_seconds'].values()):.0f}–{max(e5['build_seconds'].values()):.0f} s.",
        f"- **E2** Product-perspective totals are {acc['Type A']['product_total']:,.0f} (A), {acc['Type B']['product_total']:,.0f} (B) and {acc['Type D']['product_total']:,.0f} (D) kgCO2e; steel carries "
        + "–".join(f"{100 * acc[l]['material_by_category'].get('Steel', 0) / acc[l]['material_total']:.0f}" for l in (min(labels, key=lambda l: acc[l]['material_by_category'].get('Steel', 0) / acc[l]['material_total']), max(labels, key=lambda l: acc[l]['material_by_category'].get('Steel', 0) / acc[l]['material_total'])))
        + "% of material carbon in every module. "
        "Both reconciliation identities close to machine precision. The independent recalculation from stored operands and the factor workbook reproduces every material total exactly. "
        "The process perspective sums the shared factory records once per graph (1,369–1,372 kgCO2e); only direct and allocated terms differ by module.",
    ]
    if proxy:
        head.append(
            "- **E2 (process attribution)** The factory dataset holds a module-level log and allocation rows only for the measured Type A run. "
            f"B and D use a mass-scaled proxy of those records (ratios 0.934 and 1.107): direct {acc['Type B']['energy_direct_this_module']:.1f} / {acc['Type D']['energy_direct_this_module']:.1f} and allocated {acc['Type B']['energy_allocated_this_module']:.1f} / {acc['Type D']['energy_allocated_this_module']:.1f} kgCO2e. "
            f"As recorded (own identity, component logs only) the attributed energy would be {proxy['Type B (as recorded)']['energy_direct_this_module'] + proxy['Type B (as recorded)']['energy_allocated_this_module']:.1f} kgCO2e; with the unscaled Type A records {proxy['Type B (equal route, unscaled)']['energy_direct_this_module'] + proxy['Type B (equal route, unscaled)']['energy_allocated_this_module']:.1f}. "
            "The proxy is a stated assumption of a shared production route, to be replaced by module-level logs when they exist."
        )
    head += [
        f"- **E3** Zero-shot on B and D, the frozen compiler reaches {100 * e3['Type B']['V4']['answer_exact_match']['mean']:.1f}% and {100 * e3['Type D']['V4']['answer_exact_match']['mean']:.1f}% exact answers against {100 * e3['Type A']['V4']['answer_exact_match']['mean']:.1f}% on Type A; {solved_all} of {len(case_ids)} cases are solved on all three graphs and {failed_all} fail on all three, so failures follow the question type, not the module.",
        f"- **E5** Compile latency and tokens are the same order on the three graphs (2.4–2.9 s, about 2,400 prompt tokens and 190 completion tokens per question, below 0.001 USD per question); build time {min(e5['build_seconds'].values()):.0f}–{max(e5['build_seconds'].values()):.0f} s; process memory growth on load {min(v['delta_mb'] for v in e5['memory'].values()):.0f}–{max(v['delta_mb'] for v in e5['memory'].values()):.0f} MB.",
        "- **E6** Module ranking D > A > B holds under the original database factors, ±10% on any top-3 material factor, and the electricity and diesel factor scenarios; the top-3 material order changes only when calcium silicate and concrete, which are within 2% of each other in A and B, are perturbed. "
        f"Rejected records bound at {100 * e6['Type A']['rejected_bound']['upper_bound_kgCO2e'] / e6['Type A']['scenarios']['S0 baseline']['material']:.1f}% (A), {100 * e6['Type B']['rejected_bound']['upper_bound_kgCO2e'] / e6['Type B']['scenarios']['S0 baseline']['material']:.1f}% (B), {100 * e6['Type D']['rejected_bound']['upper_bound_kgCO2e'] / e6['Type D']['scenarios']['S0 baseline']['material']:.1f}% (D) of material carbon.",
        "",
        "## Reviewer mapping",
        "",
        "- XL10 (single case): E1, E2, E3 — three module types of the same project and factory; cross-project transfer stays in 5.2.",
        "- XL11 (allocation and reconciliation): E2 identities and the three process-attribution variants; E6 energy-factor scenarios; E2b allocation-basis report reused.",
        "- XL12 (uncertainty, rejected records): E6 scenarios S1–S4 and the rejected-record bound.",
        "- XL13 (scalability): E5 costs; E2 building-scale note (Σ N_t × type total); concurrency untested (5.2).",
        "- XL14 (leakage): E3 benchmarks have zero development overlap by construction; B and D truths were generated after the compiler was frozen.",
        "",
        "## Open inputs",
        "",
        "- Module counts per type for the building (`E2/module_counts.json`) to fill the building-scale table.",
        "- Module-level factory energy logs for Type B and Type D, if they exist, to replace the mass-scaled proxy.",
        "",
        "---",
        "",
    ]
    parts = [Path(OUT_ROOT / p).read_text(encoding="utf-8") for p in ("E1/e1_graph_quality.md", "E2/e2_module_accounts.md", "E2/e2_building_scale.md", "E3/e3_heldout_summary.md", "E5/e5_cost_report.md", "E6/e6_sensitivity.md")]
    write_text(OUT_ROOT / "MULTI_MODULE_REPORT.md", "\n".join(head) + "\n\n---\n\n".join(parts))
    print("written:", OUT_ROOT / "MULTI_MODULE_REPORT.md")


if __name__ == "__main__":
    main()
