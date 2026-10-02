# -*- coding: utf-8 -*-
"""E3: held-out CarbonQL compilation across module types (zero-shot on Type B / D).

Aggregates the E4e compiler runs (V4 = full compiler) of the three modules and the
E4c held-out benchmark audits. Prompt, worked examples and compiler are frozen on
Type A; the B and D benchmarks were generated from their own graphs with the same
wording specifications and deterministic truths.
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import OUT_ROOT, ROOT, load_json, markdown_table, write_json, write_text  # noqa: E402

E3_DIR = OUT_ROOT / "E3"
RUNS = {
    "Type A": {
        "ablation": ROOT / "outputs/research_experiments/e4e_compiler_ablation/e4e_typea1_en_v2_d_spread_20260731_5x",
        "benchmark": ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typea1_en_v2_d_spread_20260731",
    },
    "Type B": {
        "ablation": ROOT / "outputs/research_experiments/e4e_compiler_ablation/e4e_typeb_aligned_massproxy_v2_20260928_5x",
        "benchmark": ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typeb_aligned_massproxy_v2_20260928",
    },
    "Type D": {
        "ablation": ROOT / "outputs/research_experiments/e4e_compiler_ablation/e4e_typed_aligned_massproxy_v2_20260928_5x",
        "benchmark": ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typed_aligned_massproxy_v2_20260928",
    },
}
METRICS = ("answer_exact_match", "boundary_match", "presentation_match", "status_match", "total_match", "compiled")


def read_rows(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            import json
            rows.append(json.loads(line))
    return rows


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def main() -> None:
    summary = {}
    per_case_solved = {}
    for label, paths in RUNS.items():
        s = load_json(paths["ablation"] / "ablation_summary.json")
        audit = load_json(paths["benchmark"] / "e4c_machine_audit.json")
        v4 = s["variants"]["V4"]
        rows = [r for r in read_rows(paths["ablation"] / "ablation_cases.jsonl") if r["variant"] == "V4"]
        by_cat = defaultdict(lambda: defaultdict(list))
        by_case = defaultdict(list)
        for r in rows:
            for m in ("answer_exact_match", "boundary_match"):
                by_cat[r["category"]][m].append(bool(r[m]))
            by_case[r["case_id"]].append(bool(r["answer_exact_match"]))
        per_case_solved[label] = {cid: sum(v) > len(v) / 2 for cid, v in by_case.items()}
        summary[label] = {
            "release_id": s["release_id"],
            "model": s["model"],
            "repeats": s["repeats"],
            "case_count": s["case_count"],
            "benchmark_audit": {
                "development_overlap_count": audit.get("development_overlap_count"),
                "direct_graph_mismatch_count": audit.get("direct_graph_mismatch_count"),
                "compiler_status_mismatch_count": audit.get("compiler_status_mismatch_count"),
                "category_counts": audit.get("category_counts"),
            },
            "V4": {m: v4[m] for m in METRICS if m in v4},
            "mean_llm_calls": v4.get("mean_llm_calls"),
            "mean_compile_latency_ms": v4.get("mean_compile_latency_ms"),
            "mean_execute_latency_ms": statistics.mean(r["execute_latency_ms"] for r in rows) if rows else None,
            "by_category": {cat: {m: statistics.mean(v) for m, v in d.items()} for cat, d in by_cat.items()},
            "elapsed_s": s.get("elapsed_s"),
        }

    labels = list(RUNS)
    headers = ["Metric (V4 full compiler, mean ± sd over 5 repeats)"] + labels
    rows = []
    for m, name in (("answer_exact_match", "Answer exact"), ("boundary_match", "Accounting boundary"),
                    ("presentation_match", "Presentation"), ("status_match", "Query status"), ("total_match", "Total value"), ("compiled", "Compiled")):
        rows.append([name] + [f"{pct(summary[l]['V4'][m]['mean'])} ± {100 * summary[l]['V4'][m]['sd']:.1f}" for l in labels])
    rows.append(["LLM calls per question"] + [f"{summary[l]['mean_llm_calls']['mean']:.2f}" for l in labels])
    rows.append(["Compile latency, mean (ms)"] + [f"{summary[l]['mean_compile_latency_ms']['mean']:.0f}" for l in labels])
    rows.append(["Execute latency, mean (ms)"] + [f"{summary[l]['mean_execute_latency_ms']:.0f}" for l in labels])
    cat_rows = []
    for cat in ("cross_view", "partial_or_ambiguous"):
        for m, name in (("answer_exact_match", "answer exact"), ("boundary_match", "boundary")):
            cat_rows.append([f"{cat} — {name}"] + [pct(summary[l]["by_category"].get(cat, {}).get(m, float('nan'))) for l in labels])

    # paired case-level comparison A vs B, A vs D (solved = majority of repeats)
    pair_rows = []
    a = per_case_solved["Type A"]
    for other in ("Type B", "Type D"):
        o = per_case_solved[other]
        both = sum(1 for c in a if a[c] and o.get(c))
        only_a = sum(1 for c in a if a[c] and not o.get(c))
        only_o = sum(1 for c in a if not a[c] and o.get(c))
        neither = sum(1 for c in a if not a[c] and not o.get(c))
        pair_rows.append([f"Type A vs {other}", both, only_a, only_o, neither])

    md = ["# E3 Held-out CarbonQL compilation across module types", "",
          "Benchmark: 48 held-out questions per module (40 cross-view, 8 partial or ambiguous), generated from each module graph with the same wording specifications; "
          "reference programs and truths are executed deterministically. Compiler, prompt and worked examples are frozen on Type A; Type B and Type D are zero-shot. "
          "Model deepseek-chat, temperature 0, 5 repeats.", "",
          markdown_table(headers, rows), "", "## By question category", "", markdown_table(["Category — metric"] + labels, cat_rows), "",
          "## Case-level agreement with Type A (case solved when exact in more than half of the repeats)", "",
          markdown_table(["Pair", "Solved in both", "Only Type A", "Only other", "Neither"], pair_rows), "",
          "## Benchmark audits", ""]
    for l in labels:
        b = summary[l]["benchmark_audit"]
        md.append(f"- {l}: release `{summary[l]['release_id']}`; development overlap {b['development_overlap_count']}; direct-graph mismatches {b['direct_graph_mismatch_count']}; compiler-status mismatches {b['compiler_status_mismatch_count']}; categories {b['category_counts']}.")
    write_text(E3_DIR / "e3_heldout_summary.md", "\n".join(md) + "\n")
    write_json(E3_DIR / "e3_heldout_summary.json", {"summary": summary, "per_case_solved": per_case_solved})
    print("written:", E3_DIR / "e3_heldout_summary.md")


if __name__ == "__main__":
    main()
