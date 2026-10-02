# -*- coding: utf-8 -*-
"""D3: diagnostics for XL16 on the v16 benchmark.

Error taxonomy, slot precision / recall / F1 and paired McNemar tests across the
configurations that share the 150 questions (full system, three baselines, three
control-policy ablations, rule compiler), plus the held-out comparison of the rule
compiler against the frozen LLM compiler (E4e V4).
"""
from __future__ import annotations

import csv
import json
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RE = ROOT / "outputs/research_experiments"
OUT = RE / "diagnostics_20260930/D3_report"
FULL_RUNS = [RE / f"e5_full_real_layerdedup/stage5_full_v16_realloc_run{k}/e5_full_real_cases.jsonl" for k in (1, 2, 3)]
BASELINES = RE / "e5_real_baselines_layerdedup/baselines_v16_en_kg_realloc/e5_real_baseline_cases.jsonl"
E6_RUNS = [RE / f"e6_real_ablations_typea1_en/e6_v16_realloc_run{k}/e6_real_ablation_cases.jsonl" for k in (1, 2, 3)]
RULE = RE / "diagnostics_20260930/D2_rule_compiler/rule_compiler_cases.jsonl"
RULE_HELDOUT = {m: RE / f"diagnostics_20260930/D2_rule_compiler/heldout_type{m.lower()}_cases.jsonl" for m in ("A", "B", "D")}
E4E = {"A": RE / "e4e_compiler_ablation/e4e_typea1_en_v2_d_spread_20260731_5x/ablation_cases.jsonl",
       "B": RE / "e4e_compiler_ablation/e4e_typeb_aligned_massproxy_v2_20260928_5x/ablation_cases.jsonl",
       "D": RE / "e4e_compiler_ablation/e4e_typed_aligned_massproxy_v2_20260928_5x/ablation_cases.jsonl"}
BENCH = RE / "e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc/e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl"
STAGES_ROOT = RE / "diagnostics_20260930/D6_compiler_stages_v16"
CONFIG_LABELS = {
    "full_real": "Full system (LLM compiler + validator + executor)",
    "full_real_e6_recompiled": "Full system, re-compiled for the ablation runs (reference arm)",
    "stage_V1": "Compiler stage V1: no typed contract, no repair, no semantic gate",
    "stage_V2": "Compiler stage V2: typed contract, no repair, no semantic gate",
    "stage_V3": "Compiler stage V3: typed contract + validator-driven repair, no semantic gate",
    "rule_compiler": "Rule compiler (no LLM) + validator + executor",
    "no_validation_gate_real": "Ablation: no validation gate",
    "no_status_blocking_real": "Ablation: no status blocking",
    "no_provenance_constraint_real": "Ablation: no provenance constraint",
    "llm_structured_json_real": "Baseline: LLM reads graph JSON, structured output",
    "unconstrained_kg_llm_real": "Baseline: LLM reads graph, free text (no executor)",
    "llm_only_real": "Baseline: LLM only (no graph)",
}
ERROR_ORDER = ["no_structured_output", "compile_error", "executor_error", "boundary_state_error", "perspective_error", "operation_error", "value_or_target_error", "other_failure"]
REJECT_STATUSES = {"type_error", "invalid_syntax", "semantic_error", "json_error", "compiler_rejected", "unsupported_operation", "not_executed"}


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------- key-free prose protocol (Section 4.3.4)
# Identical to tmp/build_keyfree_comparison_v16.py: every non-zero reference number must appear among the
# numbers written in the answer text or summary, within 1e-3 relative or 0.01 absolute.
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> list:
    out = []
    for m in _NUM_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _gold_numbers(summary) -> list:
    if not isinstance(summary, dict):
        return []
    return [float(v) for v in summary.values() if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0]


def keyfree(row):
    """(strict_all, any_hit) for a numeric-bearing row, None otherwise."""
    gold = _gold_numbers(row["expected"].get("summary"))
    if not gold:
        return None
    o = row["observed"]
    text = (o.get("answer") or "") + (" " + json.dumps(o["summary"]) if isinstance(o.get("summary"), dict) else "")
    pool = _numbers_in(text)
    hits = [g for g in gold if any(abs(x - g) <= max(0.01, 1e-3 * abs(g)) for x in pool)]
    return (len(hits) == len(gold), bool(hits))


def classify(row) -> str:
    s, o, e = row["scores"], row["observed"], row["expected"]
    if s.get("passed"):
        return "pass"
    if o.get("status") is None and o.get("response_format") == "unstructured":
        return "no_structured_output"
    if o.get("status") == "not_executed" or o.get("coverage_status") == "compiler_rejected" or str(o.get("compiler_status") or "") in REJECT_STATUSES:
        return "compile_error"
    if o.get("status") in ("transport_error", "error", "executor_error"):
        return "executor_error"
    if not s.get("status_match"):
        return "boundary_state_error"
    if e.get("perspective") != o.get("perspective"):
        return "perspective_error"
    if e.get("operation") != o.get("operation"):
        return "operation_error"
    if s.get("numeric_match") is False or s.get("unsupported_answer") or s.get("provenance_supported") is False:
        return "value_or_target_error"
    return "other_failure"


def program_op_label(program: dict) -> str:
    """Operation implied by the operator set of a compiled program.

    Compare and Rank are terminal presentation operators; Trace marks a trace or explain
    request; anything else reports a value. This reads the program itself, unlike the
    executor's internal label which ranks Aggregate above Trace.
    """
    ops = {s.get("op") for s in (program or {}).get("steps", [])}
    if "Compare" in ops:
        return "compare"
    if "Rank" in ops:
        return "rank"
    if "Trace" in ops:
        return "trace"
    return "value"


def prf(pairs, classes):
    """Macro precision / recall / F1 over (expected, observed) pairs."""
    out = {}
    for c in classes:
        tp = sum(1 for e, o in pairs if e == c and o == c)
        fp = sum(1 for e, o in pairs if e != c and o == c)
        fn = sum(1 for e, o in pairs if e == c and o != c)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        out[c] = {"precision": p, "recall": r, "f1": f, "support": tp + fn}
    out["macro"] = {k: statistics.mean(out[c][k] for c in classes) for k in ("precision", "recall", "f1")}
    out["accuracy"] = sum(1 for e, o in pairs if e == o) / len(pairs) if pairs else None
    return out


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_ci(values, n=2000, seed=7):
    rng = random.Random(seed)
    vals = list(values)
    if not vals:
        return (None, None)
    means = []
    for _ in range(n):
        sample = [vals[rng.randrange(len(vals))] for _ in vals]
        means.append(sum(sample) / len(sample))
    means.sort()
    return (means[int(0.025 * n)], means[int(0.975 * n) - 1])


def pass_map(rows):
    return {r["case_id"]: bool(r["scores"].get("passed")) for r in rows}


def majority(maps):
    ids = set().union(*[m.keys() for m in maps])
    return {i: sum(m.get(i, False) for m in maps) > len(maps) / 2 for i in ids}


def paired(full_map, other_map):
    ids = sorted(set(full_map) & set(other_map))
    b = sum(1 for i in ids if full_map[i] and not other_map[i])
    c = sum(1 for i in ids if other_map[i] and not full_map[i])
    return {"n": len(ids), "full_only": b, "other_only": c, "both": sum(1 for i in ids if full_map[i] and other_map[i]),
            "neither": sum(1 for i in ids if not full_map[i] and not other_map[i]), "mcnemar_p": mcnemar_exact(b, c)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bench = {r["case_id"]: r for r in read_jsonl(BENCH)}
    configs: dict[str, list[list]] = defaultdict(list)  # config -> list of runs (each a list of rows)
    for p in FULL_RUNS:
        rows = read_jsonl(p)
        if rows:
            configs["full_real"].append(rows)
    for rows in [read_jsonl(BASELINES)]:
        for v in ("llm_only_real", "unconstrained_kg_llm_real", "llm_structured_json_real"):
            sub = [r for r in rows if r["variant"] == v]
            if sub:
                configs[v].append(sub)
    e6_runs = [read_jsonl(p) for p in E6_RUNS]
    e6_runs = [r for r in e6_runs if r]
    for rows in e6_runs:
        for v in ("no_validation_gate_real", "no_status_blocking_real", "no_provenance_constraint_real"):
            configs[v].append([r for r in rows if r["variant"] == v])
    e6_full = [[r for r in rows if r["variant"] == "full_real"] for rows in e6_runs]
    if e6_full:
        configs["full_real_e6_recompiled"] = e6_full
    rule_rows = read_jsonl(RULE)
    if rule_rows:
        configs["rule_compiler"].append(rule_rows)
    for v in ("V1", "V2", "V3"):
        for k in (1, 2, 3):
            rows = read_jsonl(STAGES_ROOT / f"stage_{v}_run{k}/stage_cases.jsonl")
            if rows:
                configs[f"stage_{v}"].append(rows)

    report = {"benchmark": str(BENCH), "runs": {k: len(v) for k, v in configs.items()}}

    # ------------------------------------------------------------ 0. subset rates of the full system (manuscript 4.3.2)
    full_runs = configs["full_real"]
    def _rate(rows, key, cond=lambda r: True):
        vals = [r["scores"].get(key) for r in rows if cond(r) and r["scores"].get(key) is not None]
        return statistics.mean(bool(v) for v in vals) if vals else None
    is_exec = lambda r: r["expected"]["status"] == "executable"
    is_bound = lambda r: r["expected"]["status"] != "executable"
    pass_runs = [_rate(rows, "passed") for rows in full_runs]
    report["full_system_subsets"] = {
        "pass_per_run": pass_runs, "pass_mean": statistics.mean(pass_runs), "pass_pstdev": statistics.pstdev(pass_runs), "pass_stdev": statistics.stdev(pass_runs) if len(pass_runs) > 1 else 0.0,
        "executable_pass": statistics.mean(_rate(rows, "passed", is_exec) for rows in full_runs),
        "executable_numeric": statistics.mean(_rate(rows, "numeric_match", is_exec) for rows in full_runs),
        "boundary_pass": statistics.mean(_rate(rows, "passed", is_bound) for rows in full_runs),
        "boundary_status": statistics.mean(_rate(rows, "status_match", is_bound) for rows in full_runs),
        "status_all": statistics.mean(_rate(rows, "status_match") for rows in full_runs),
        "failures_by_perspective": dict(Counter(r["expected"]["perspective"] for rows in full_runs for r in rows if not r["scores"]["passed"])),
        "rows_by_perspective": dict(Counter(r["expected"]["perspective"] for rows in full_runs for r in rows)),
        "failures_by_operation": dict(Counter(r["expected"]["operation"] for rows in full_runs for r in rows if not r["scores"]["passed"])),
        "executor_errors": sum(r["observed"].get("status") in ("executor_error", "transport_error") for rows in full_runs for r in rows),
    }

    # ------------------------------------------------------------ 0b. key-free prose protocol on the 90 numeric questions
    kf_cfg = {}
    kf_maps = {}
    for cfg, runs in configs.items():
        per_run = []
        maps = []
        for rows in runs:
            scored = {r["case_id"]: keyfree(r) for r in rows}
            scored = {k: v for k, v in scored.items() if v is not None}
            per_run.append({"n": len(scored), "strict_all": statistics.mean(v[0] for v in scored.values()), "any_hit": statistics.mean(v[1] for v in scored.values())})
            maps.append({k: v[0] for k, v in scored.items()})
        kf_cfg[cfg] = {"runs": len(runs), "n": per_run[0]["n"], "strict_all_mean": statistics.mean(x["strict_all"] for x in per_run),
                       "strict_all_pstdev": statistics.pstdev(x["strict_all"] for x in per_run) if len(per_run) > 1 else 0.0,
                       "any_hit_mean": statistics.mean(x["any_hit"] for x in per_run), "per_run": per_run}
        kf_maps[cfg] = maps
    kf_full_maj = majority(kf_maps["full_real"])
    for cfg, maps in kf_maps.items():
        if cfg == "full_real":
            continue
        kf_cfg[cfg]["paired_vs_full_majority"] = paired(kf_full_maj, majority(maps))
        kf_cfg[cfg]["paired_vs_full_per_run_p"] = [paired(fm, om)["mcnemar_p"] for fm in kf_maps["full_real"] for om in maps]
    kf_cfg["full_real"]["ci95_majority"] = bootstrap_ci(list(kf_full_maj.values()))
    report["keyfree_numeric_90"] = kf_cfg

    # ------------------------------------------------------------ 1. rates and error taxonomy
    taxonomy = {}
    rates = {}
    for cfg, runs in configs.items():
        per_run = []
        counts = Counter()
        examples = defaultdict(list)
        for rows in runs:
            per_run.append({"pass": statistics.mean(bool(r["scores"].get("passed")) for r in rows),
                            "status": statistics.mean(bool(r["scores"].get("status_match")) for r in rows),
                            "numeric": statistics.mean(bool(r["scores"].get("numeric_match")) for r in [x for x in rows if x["expected"]["status"] == "executable"]) if any(x["expected"]["status"] == "executable" for x in rows) else None})
            for r in rows:
                cat = classify(r)
                counts[cat] += 1
                if cat != "pass" and len(examples[cat]) < 2:
                    examples[cat].append({"case_id": r["case_id"], "question": r["question"][:120], "expected_status": r["expected"]["status"], "observed_status": r["observed"].get("status"),
                                          "expected_slots": (r["expected"]["perspective"], r["expected"]["operation"]), "observed_slots": (r["observed"].get("perspective"), r["observed"].get("operation"))})
        n_rows = sum(len(rows) for rows in runs)
        pooled = [r for rows in runs for r in rows]
        status_conf = Counter((r["expected"]["status"], r["observed"].get("status") or "none") for r in pooled if not r["scores"].get("status_match"))
        mism = [(r["expected"]["status"], r["observed"].get("status")) for r in pooled if not r["scores"].get("status_match") and r["observed"].get("status")]
        boundary_split = {"conservative (reference executable, returned a boundary state)": sum(1 for e, o in mism if e == "executable" and o != "executable"),
                          "over_claim (reference boundary state, returned executable)": sum(1 for e, o in mism if e != "executable" and o == "executable"),
                          "cross_state (two different boundary states)": sum(1 for e, o in mism if e != "executable" and o != "executable")}
        by_status = {st: statistics.mean(bool(r["scores"].get("passed")) for r in pooled if r["expected"]["status"] == st) for st in sorted({r["expected"]["status"] for r in pooled})}
        by_diff = {d: statistics.mean(bool(r["scores"].get("passed")) for r in pooled if bench.get(r["case_id"], {}).get("difficulty") == d) for d in ("single_hop", "multi_hop", "batch_allocation") if any(bench.get(r["case_id"], {}).get("difficulty") == d for r in pooled)}
        taxonomy[cfg] = {"rows": n_rows, "runs": len(runs), "counts": dict(counts), "share": {k: v / n_rows for k, v in counts.items()}, "examples": dict(examples),
                         "status_confusion": {f"{a} -> {b}": n for (a, b), n in status_conf.most_common()}, "boundary_split": boundary_split,
                         "pass_by_expected_status": by_status, "pass_by_difficulty": by_diff}
        maps = [pass_map(rows) for rows in runs]
        maj = majority(maps)
        rates[cfg] = {"pass_mean": statistics.mean(x["pass"] for x in per_run), "pass_sd": statistics.stdev(x["pass"] for x in per_run) if len(per_run) > 1 else 0.0,
                      "status_mean": statistics.mean(x["status"] for x in per_run),
                      "numeric_mean": statistics.mean(x["numeric"] for x in per_run if x["numeric"] is not None) if any(x["numeric"] is not None for x in per_run) else None,
                      "pass_ci95_majority": bootstrap_ci(list(maj.values())), "per_run": per_run}
    report["rates"] = rates
    report["error_taxonomy"] = taxonomy

    # ------------------------------------------------------------ 2. slot P/R/F1
    slots = {}
    persp_classes = ["product", "material", "process"]
    op_classes = ["value", "rank", "compare", "explain", "trace"]
    for cfg in ("full_real", "rule_compiler", "llm_structured_json_real"):
        runs = configs.get(cfg, [])
        pairs_p, pairs_o = [], []
        for rows in runs:
            for r in rows:
                pairs_p.append((r["expected"]["perspective"], r["observed"].get("perspective") or "none"))
                pairs_o.append((r["expected"]["operation"], r["observed"].get("operation") or "none"))
        if pairs_p:
            slots[cfg] = {"level": "answer", "perspective": prf(pairs_p, persp_classes), "operation": prf(pairs_o, op_classes), "n": len(pairs_p)}
    # program level from D1 (LLM V4) and D2 (rule)
    prog_sets = {"full_real (program level)": [r for rows in e6_full for r in rows if r.get("program")],
                 "rule_compiler (program level)": [r for r in rule_rows if r.get("program")]}
    for name, rows in prog_sets.items():
        if not rows:
            continue
        pp = [(r["expected"]["perspective"], r.get("program_perspective") or "none") for r in rows]
        po = [(r["expected"]["operation"], program_op_label(r["program"])) for r in rows if r["expected"]["operation"] != "explain"]
        # benchmark scope: selected_component when BIM items were selected, project otherwise
        ps = [(bench[r["case_id"]]["scope"], "selected_component" if r.get("program_selector") == "SelectClicked" else "project")
              for r in rows if r["case_id"] in bench]
        slots[name] = {"level": "program", "n": len(rows), "perspective": prf(pp, persp_classes),
                       "operation_raw": prf(po, ["value", "rank", "compare", "trace"]),
                       "scope": prf(ps, ["project", "selected_component"]),
                       "note": "Program-level labels are read from the compiled program: perspective from the projection rule, operation from the operator set (Compare, Rank, Trace, otherwise value), entity scope from the selector. Explain questions are excluded from the program-level operation score because the program vocabulary has no explain operator; the answer adapter labels them from the question. Entity scope follows the viewer selection that the compiler receives, so its agreement is fixed by the interface, not learned."}
    # emission source on held-out from E4e V4 gold vs observed signatures
    src_pairs = defaultdict(list)
    for m, p in E4E.items():
        for r in read_jsonl(p):
            if r["variant"] != "V4":
                continue
            g = tuple(r["gold_view_signature"].get("emission_sources") or [])
            o = tuple((r.get("observed_view_signature") or {}).get("emission_sources") or [])
            src_pairs[m].append(("+".join(g) or "none", "+".join(o) or "none"))
    if src_pairs:
        classes = sorted({e for m in src_pairs for e, _ in src_pairs[m]})
        slots["emission_source (held-out, LLM V4, program level)"] = {m: prf(pairs, classes) for m, pairs in src_pairs.items()}
        slots["emission_source (held-out, LLM V4, program level)"]["classes"] = classes
    report["slots"] = slots

    # ------------------------------------------------------------ 3. paired tests on the 150 questions
    full_maps = [pass_map(rows) for rows in configs["full_real"]]
    full_maj = majority(full_maps)
    pairs_out = {}
    for cfg, runs in configs.items():
        if cfg == "full_real":
            continue
        other_maps = [pass_map(rows) for rows in runs]
        entry = {"majority_vs_majority": paired(full_maj, majority(other_maps)), "per_run": []}
        if cfg in ("no_validation_gate_real", "no_status_blocking_real", "no_provenance_constraint_real") and e6_full:
            # same compilation within a run: pair run k of the ablation with run k of the E6 full arm
            e6_maps = [pass_map(rows) for rows in e6_full]
            entry["majority_vs_majority"] = paired(majority(e6_maps), majority(other_maps))
            for k, rows in enumerate(runs):
                if k < len(e6_full):
                    entry["per_run"].append(paired(e6_maps[k], pass_map(rows)))
            entry["pairing"] = "same compiled program per run (reference arm vs policy)"
        else:
            for fm in full_maps:
                for om in other_maps:
                    entry["per_run"].append(paired(fm, om))
            entry["pairing"] = "each full-system run vs each run of the other configuration"
        pairs_out[cfg] = entry
    report["paired_tests_150"] = pairs_out

    # ------------------------------------------------------------ 4. held-out: rule compiler vs LLM V4
    heldout = {}
    for m, p in RULE_HELDOUT.items():
        rule = read_jsonl(p)
        llm = [r for r in read_jsonl(E4E[m]) if r["variant"] == "V4"]
        if not rule or not llm:
            continue
        by_case = defaultdict(list)
        for r in llm:
            by_case[r["case_id"]].append(bool(r["answer_exact_match"]))
        llm_maj = {c: sum(v) > len(v) / 2 for c, v in by_case.items()}
        rule_map = {r["case_id"]: bool(r["answer_exact_match"]) for r in rule}
        bmaj = {c: sum(bool(x["boundary_match"]) for x in llm if x["case_id"] == c) > 2.5 for c in by_case}
        rule_b = {r["case_id"]: bool(r["boundary_match"]) for r in rule}
        smaj = {c: sum(bool(x["status_match"]) for x in llm if x["case_id"] == c) > 2.5 for c in by_case}
        rule_s = {r["case_id"]: bool(r["status_match"]) for r in rule}
        heldout[m] = {"rule_exact": statistics.mean(rule_map.values()), "llm_exact_majority": statistics.mean(llm_maj.values()),
                      "rule_boundary": statistics.mean(rule_b.values()), "llm_boundary_majority": statistics.mean(bmaj.values()),
                      "rule_status": statistics.mean(rule_s.values()), "llm_status_majority": statistics.mean(smaj.values()),
                      "exact_paired": paired(llm_maj, rule_map), "boundary_paired": paired(bmaj, rule_b)}
    report["heldout_rule_vs_llm"] = heldout

    (OUT / "diagnostics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------ CSV + markdown
    with (OUT / "error_taxonomy.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["configuration", "runs", "rows", "pass"] + ERROR_ORDER)
        for cfg, t in taxonomy.items():
            w.writerow([cfg, t["runs"], t["rows"], t["counts"].get("pass", 0)] + [t["counts"].get(k, 0) for k in ERROR_ORDER])

    def pct(x):
        return "n/a" if x is None else f"{100 * x:.1f}%"

    md = ["# D3 Diagnostics on the v16 benchmark (150 questions, Type A d_spread graph)", ""]
    md += ["## Configurations, pass rates and 95% bootstrap CI (majority vote over runs)", "",
           "| Configuration | Runs | Strict pass, mean ± sd | Status acc. | Numeric acc. (90 exec.) | 95% CI (majority) |", "|---|---|---|---|---|---|"]
    order = ["full_real", "full_real_e6_recompiled", "stage_V3", "stage_V2", "stage_V1", "no_validation_gate_real", "no_status_blocking_real", "no_provenance_constraint_real", "rule_compiler", "llm_structured_json_real", "unconstrained_kg_llm_real", "llm_only_real"]
    fs = report["full_system_subsets"]
    md += ["## Full system on the 150 questions, three runs (manuscript Section 4.3.2 figures)", "",
           f"Strict pass per run {', '.join(pct(x) for x in fs['pass_per_run'])}; mean {pct(fs['pass_mean'])}, population sd {100 * fs['pass_pstdev']:.1f}, sample sd {100 * fs['pass_stdev']:.1f}. "
           f"Executable questions (90): strict pass {pct(fs['executable_pass'])}, strict numeric {pct(fs['executable_numeric'])}. Boundary questions (60): strict pass {pct(fs['boundary_pass'])}, status accuracy {pct(fs['boundary_status'])}. Status accuracy on all 150: {pct(fs['status_all'])}. "
           f"Failures by reference perspective (of rows): " + ", ".join(f"{k} {fs['failures_by_perspective'].get(k, 0)}/{fs['rows_by_perspective'][k]}" for k in ("product", "material", "process")) + f". Executor or transport errors: {fs['executor_errors']}.", ""]
    md += ["## Key-free numeric protocol on the 90 numeric questions (manuscript Section 4.3.4 protocol)", "",
           "Every non-zero reference number must appear among the numbers written in the answer, within 1e-3 relative or 0.01 absolute. Paired tests use the majority over runs.", "",
           "| Configuration | Runs | Strict (all numbers) | Any hit | Full only | Other only | McNemar p (majority) | Per-run p range |", "|---|---|---|---|---|---|---|---|"]
    kf = report["keyfree_numeric_90"]
    for cfg in order:
        if cfg not in kf:
            continue
        k = kf[cfg]
        if cfg == "full_real":
            lo, hi = k["ci95_majority"]
            md.append(f"| {CONFIG_LABELS[cfg]} | {k['runs']} | {pct(k['strict_all_mean'])} ± {100 * k['strict_all_pstdev']:.1f} | {pct(k['any_hit_mean'])} | — | — | 95% CI {pct(lo)} to {pct(hi)} | — |")
        else:
            pr = k["paired_vs_full_majority"]
            ps = k["paired_vs_full_per_run_p"]
            md.append(f"| {CONFIG_LABELS[cfg]} | {k['runs']} | {pct(k['strict_all_mean'])} ± {100 * k['strict_all_pstdev']:.1f} | {pct(k['any_hit_mean'])} | {pr['full_only']} | {pr['other_only']} | {pr['mcnemar_p']:.3g} | {min(ps):.3g} to {max(ps):.3g} |")
    md.append("")
    for cfg in order:
        if cfg not in rates:
            continue
        r = rates[cfg]
        lo, hi = r["pass_ci95_majority"]
        md.append(f"| {CONFIG_LABELS[cfg]} | {len(configs[cfg])} | {pct(r['pass_mean'])} ± {100 * r['pass_sd']:.1f} | {pct(r['status_mean'])} | {pct(r['numeric_mean'])} | {pct(lo)} to {pct(hi)} |")
    md += ["", "## Error taxonomy (rows pooled over runs)", "",
           "| Configuration | Rows | Pass | No structured output | Compile | Executor | Boundary state | Perspective | Operation | Value or target | Other |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for cfg in order:
        if cfg not in taxonomy:
            continue
        t = taxonomy[cfg]
        md.append(f"| {CONFIG_LABELS[cfg]} | {t['rows']} | {t['counts'].get('pass', 0)} | " + " | ".join(str(t["counts"].get(k, 0)) for k in ERROR_ORDER) + " |")
    md += ["", "Rule: a failed row takes the first applicable class in the order no structured output (free-text baselines return no state), compile (validator rejected every program, nothing executed), executor (transport or runtime error), boundary state (returned state differs from the reference), perspective, operation, value or target (state and slots correct, number or provenance wrong).", ""]
    md += ["### Pass rate by reference state and by difficulty", "", "| Configuration | " + " | ".join(["executable", "incomplete_path", "empty_result", "unresolved_target", "clarification_required", "single_hop", "multi_hop", "batch_allocation"]) + " |", "|---|---|---|---|---|---|---|---|---|"]
    for cfg in order:
        if cfg not in taxonomy:
            continue
        t = taxonomy[cfg]
        md.append(f"| {CONFIG_LABELS[cfg]} | " + " | ".join(pct(t["pass_by_expected_status"].get(k)) for k in ["executable", "incomplete_path", "empty_result", "unresolved_target", "clarification_required"]) + " | " + " | ".join(pct(t["pass_by_difficulty"].get(k)) for k in ["single_hop", "multi_hop", "batch_allocation"]) + " |")
    md += ["", "### Boundary-state confusion among failures (reference -> returned)", ""]
    for cfg in ("full_real", "rule_compiler", "llm_structured_json_real"):
        if cfg in taxonomy:
            md.append(f"- {CONFIG_LABELS[cfg]}: " + ", ".join(f"{k} {v}" for k, v in taxonomy[cfg]["status_confusion"].items()))
            md.append("  Direction: " + ", ".join(f"{k} {v}" for k, v in taxonomy[cfg]["boundary_split"].items()))
    md.append("")
    md += ["### Examples (full system)", ""]
    for cat, exs in taxonomy.get("full_real", {}).get("examples", {}).items():
        for e in exs:
            md.append(f"- {cat}: {e['case_id']} — “{e['question']}” expected {e['expected_status']}/{e['expected_slots']}, observed {e['observed_status']}/{e['observed_slots']}")
    md += ["", "## Slot classification, macro precision / recall / F1", ""]
    for name, s in slots.items():
        if name.startswith("emission_source"):
            md.append(f"### {name}")
            md.append("| Module | Accuracy | Macro P | Macro R | Macro F1 |")
            md.append("|---|---|---|---|---|")
            for m in ("A", "B", "D"):
                if m in s:
                    md.append(f"| Type {m} | {pct(s[m]['accuracy'])} | {pct(s[m]['macro']['precision'])} | {pct(s[m]['macro']['recall'])} | {pct(s[m]['macro']['f1'])} |")
            md.append("")
            continue
        md.append(f"### {name} (n = {s['n']})")
        md.append("| Slot | Accuracy | Macro P | Macro R | Macro F1 | Per-class F1 |")
        md.append("|---|---|---|---|---|---|")
        for slot, label in (("perspective", "perspective"), ("operation", "operation"), ("operation_raw", "operation (explain excluded)"), ("scope", "entity scope (selector)")):
            if slot in s:
                v = s[slot]
                per = ", ".join(f"{c} {100 * v[c]['f1']:.0f}" for c in v if c not in ("macro", "accuracy"))
                md.append(f"| {label} | {pct(v['accuracy'])} | {pct(v['macro']['precision'])} | {pct(v['macro']['recall'])} | {pct(v['macro']['f1'])} | {per} |")
        if s.get("note"):
            md.append(f"\n{s['note']}")
        md.append("")
    md += ["## Paired McNemar tests on strict pass (150 questions)", "",
           "| Full system vs | Pairing | n | Full only | Other only | Both | Neither | Exact p (majority) | Per-run p range |", "|---|---|---|---|---|---|---|---|---|"]
    for cfg in order[1:]:
        if cfg not in pairs_out:
            continue
        e = pairs_out[cfg]
        m = e["majority_vs_majority"]
        ps = [x["mcnemar_p"] for x in e["per_run"]]
        md.append(f"| {CONFIG_LABELS[cfg]} | {e['pairing']} | {m['n']} | {m['full_only']} | {m['other_only']} | {m['both']} | {m['neither']} | {m['mcnemar_p']:.3g} | {min(ps):.3g} to {max(ps):.3g} |" if ps else f"| {CONFIG_LABELS[cfg]} | {e['pairing']} | {m['n']} | {m['full_only']} | {m['other_only']} | {m['both']} | {m['neither']} | {m['mcnemar_p']:.3g} | n/a |")
    md += ["", "## Held-out sets (48 questions per module): rule compiler vs frozen LLM compiler (V4, majority of 5 repeats)", "",
           "| Module | Rule: answer exact | LLM: answer exact | Rule: accounting boundary | LLM: accounting boundary | Rule: query status | LLM: query status | McNemar p (exact) | McNemar p (boundary) |", "|---|---|---|---|---|---|---|---|---|"]
    for m, h in heldout.items():
        md.append(f"| Type {m} | {pct(h['rule_exact'])} | {pct(h['llm_exact_majority'])} | {pct(h['rule_boundary'])} | {pct(h['llm_boundary_majority'])} | {pct(h['rule_status'])} | {pct(h['llm_status_majority'])} | {h['exact_paired']['mcnemar_p']:.3g} | {h['boundary_paired']['mcnemar_p']:.3g} |")
    md += ["", "Accounting boundary = the boundary keys of the view signature (entity scope, emission sources, base view) equal the gold signature; query status = the returned boundary state equals the truth state. The rule compiler keeps the state right on most held-out questions but rarely reaches the right accounting boundary, because the held-out phrasing does not reuse the benchmark cue words its rules were written from. The rule compiler's cue lists were written with the 150 benchmark questions visible, exactly as the compiler contract was, so the 150-question comparison favours neither side and the held-out comparison is the clean one.",
           "", "The E4e compiler-stage ablation (V1 to V4 on the same held-out sets) is reported in `e4e_compiler_ablation/*/ablation_summary.md` with its own McNemar tests."]
    (OUT / "diagnostics.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
