# -*- coding: utf-8 -*-
"""Manuscript text for reviewer comments XL14 to XL16 (benchmark transparency, leakage,
prompt and setting reporting, annotator agreement, error taxonomy, slot classification,
ablations and paired tests), assembled from the diagnostics outputs.

One source of text for two renderings: the stand-alone Appendix A (xl1416_docs.py
appendix) and the red changes applied to the manuscript (xl1416_docs.py manuscript).
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/multi_module"))
from mm_content import Table  # noqa: E402

RE_DIR = ROOT / "outputs/research_experiments"
DIAG = RE_DIR / "diagnostics_20260930"
BENCH = RE_DIR / "e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc/e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl"
E4E_A = RE_DIR / "e4e_compiler_ablation/e4e_typea1_en_v2_d_spread_20260731_5x/ablation_summary.json"
E4E = {"A": E4E_A,
       "B": RE_DIR / "e4e_compiler_ablation/e4e_typeb_aligned_massproxy_v2_20260928_5x/ablation_summary.json",
       "D": RE_DIR / "e4e_compiler_ablation/e4e_typed_aligned_massproxy_v2_20260928_5x/ablation_summary.json"}

STATES = ["executable", "incomplete_path", "empty_result", "unresolved_target", "clarification_required"]
PERSP = ["product", "material", "process"]
OPS = ["value", "rank", "compare", "explain", "trace"]


@dataclass
class Labels:
    app: str = "Appendix A"
    tab_measures: str = "Table 4"        # new table in 4.3.1
    tab_phrasing_new: str = "Table 5"    # the manuscript's Table 4 (phrasing sensitivity) moves to 5
    app_c_heldout: str = "Appendix C.6"
    app_c_rejected: str = "Appendix C.4"


@dataclass
class Content:
    labels: Labels
    appendix: dict = field(default_factory=dict)
    main: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)


def load_json(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_jsonl(p: Path):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def pc(x, d=1):
    return f"{100 * x:.{d}f}"


def pcw(x, d=1):
    """percentage with the word percent, for prose."""
    return f"{100 * x:.{d}f} percent"


_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def sup(n: int) -> str:
    return str(n).translate(_SUP)


def fmt_p(p: float) -> str:
    if p >= 0.001:
        return f"{p:.3f}".rstrip("0").rstrip(".") if p < 0.995 else "1.0"
    exp = int(f"{p:.0e}".split("e")[1])
    mant = p / 10 ** exp
    if round(mant) == 10:
        mant, exp = 1.0, exp + 1
    return f"{mant:.0f} × 10{sup(exp)}"


_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve",
          13: "Thirteen", 14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 18: "Eighteen", 19: "Nineteen", 20: "Twenty", 30: "Thirty", 40: "Forty", 50: "Fifty", 60: "Sixty", 70: "Seventy", 80: "Eighty", 90: "Ninety"}


def word(n: int) -> str:
    """Number word for a sentence start."""
    if n in _WORDS:
        return _WORDS[n]
    tens, ones = divmod(n, 10)
    if tens * 10 in _WORDS and ones:
        return f"{_WORDS[tens * 10]}-{_WORDS[ones].lower()}"
    return str(n)


def build_content(labels: Labels | None = None) -> Content:
    L = labels or Labels()
    D3 = load_json(DIAG / "D3_report/diagnostics.json")
    card = load_json(DIAG / "D4_prompts/benchmark_card_v16.json")
    sys_prompt = (DIAG / "D4_prompts/compiler_system_prompt.txt").read_text(encoding="utf-8")
    contract = load_json(DIAG / "D4_prompts/compiler_contract_v4.json")
    repair = load_json(DIAG / "D4_prompts/repair_messages_template.json")
    bprompts = load_json(DIAG / "D4_prompts/baseline_prompts.json")
    kappa = load_json(DIAG / "D5_human_tasks/annotation_sheet_40_kappa.json")
    sampling = load_json(DIAG / "D5_human_tasks/sampling_summary.json")
    rule = load_json(DIAG / "D2_rule_compiler/rule_compiler_summary.json")
    rule_ho = load_json(DIAG / "D2_rule_compiler/rule_compiler_heldout_summary.json")
    e4e = {m: load_json(p) for m, p in E4E.items() if p.exists()}
    bench = read_jsonl(BENCH)

    rates, tax, slots, pairs, ho = D3["rates"], D3["error_taxonomy"], D3["slots"], D3["paired_tests_150"], D3["heldout_rule_vs_llm"]
    fs, kf = D3["full_system_subsets"], D3["keyfree_numeric_90"]
    have_stages = all(f"stage_{v}" in rates for v in ("V1", "V2", "V3"))

    # ---------------------------------------------------------------- annotator notes
    notes = []
    try:
        from openpyxl import load_workbook
        ws = load_workbook(DIAG / "D5_human_tasks/annotation_sheet_40.xlsx")["annotation"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[6] and str(row[6]).strip():
                notes.append((row[0], str(row[6]).strip(), row[3], row[4], row[5]))
    except Exception:
        pass
    n_blank = sum(1 for r in notes if not (r[2] or r[3] or r[4]))
    n_compound = sum(1 for r in notes if re.search(r"rank|compare|两个任务|trace", r[1], re.I) and (r[2] or r[3]))
    n_evidence = sum(1 for r in notes if "evidence" in r[1].lower())

    # ---------------------------------------------------------------- coverage table A1
    cell = Counter((r["expected"]["perspective"], r["expected"]["operation"], r["expected"]["status"] == "executable") for r in bench)
    rows_a1 = []
    for op in OPS:
        row = [op]
        for p in PERSP:
            ex, bd = cell[(p, op, True)], cell[(p, op, False)]
            row.append(f"{ex} + {bd}")
        row.append(f"{sum(cell[(p, op, True)] for p in PERSP)} + {sum(cell[(p, op, False)] for p in PERSP)}")
        rows_a1.append(row)
    tot = ["Total"] + [f"{sum(cell[(p, op, True)] for op in OPS)} + {sum(cell[(p, op, False)] for op in OPS)}" for p in PERSP] + [f"{card['status']['executable']} + {150 - card['status']['executable']}"]
    rows_a1.append(tot)
    status_counts = card["status"]
    diff = card["difficulty"]
    scope = card["scope"]
    t_a1 = Table(headers=["Operation", "Product", "Material", "Process", "All"], rows=rows_a1,
                 caption="Table A1. Distribution of the 150 questions by perspective and operation, executable + boundary",
                 note=f"Boundary questions by reference state, incomplete_path {status_counts['incomplete_path']}, empty_result {status_counts['empty_result']}, unresolved_target {status_counts['unresolved_target']}, clarification_required {status_counts['clarification_required']}. Difficulty tags, single_hop {diff['single_hop']}, multi_hop {diff['multi_hop']}, batch_allocation {diff['batch_allocation']}. Scope, {scope['project']} project questions and {scope['selected_component']} questions asked with BIM items selected in the viewer.")

    # ---------------------------------------------------------------- agreement table A2
    kp, ko, ks = kappa["perspective"], kappa["operation"], kappa["status"]
    t_a2 = Table(headers=["Label", "Questions labelled", "Agreement", "Cohen's κ", "Disagreements, reference → annotator"],
                 rows=[["Perspective", kp["n"], pc(kp["agreement"]) + "%", f"{kp['kappa']:.2f}", ", ".join(f"{k} {v}" for k, v in kp["disagreements"].items()) or "none"],
                       ["Operation", ko["n"], pc(ko["agreement"]) + "%", f"{ko['kappa']:.2f}", ", ".join(f"{k} {v}" for k, v in ko["disagreements"].items()) or "none"],
                       ["Boundary state", ks["n"], pc(ks["agreement"]) + "%", f"{ks['kappa']:.2f}", ", ".join(f"{k} {v}" for k, v in ks["disagreements"].items()) or "none"]],
                 caption="Table A2. Agreement between the reference labels and a second annotator on 40 stratified questions",
                 note=f"Sample stratified by reference state, {', '.join(f'{k} {v}' for k, v in sampling['annotation_quota'].items())}, seed {sampling['seed']}. One question was left unlabelled by the annotator as unclear.")
    n_exec_labelled = sum(v for k, v in ks["disagreements"].items() if k.endswith("-> executable")) + int(round(ks["n"] * ks["agreement"])) - sum(v for k, v in ks["disagreements"].items() if k.startswith("executable ->"))
    # annotator marked executable = agreements on executable + boundary->executable disagreements
    exec_in_sample = sampling["annotation_quota"]["executable"]
    agreed_exec = exec_in_sample - sum(v for k, v in ks["disagreements"].items() if k.startswith("executable ->")) - (1 if n_blank else 0)
    marked_exec = agreed_exec + sum(v for k, v in ks["disagreements"].items() if k.endswith("-> executable"))

    # ---------------------------------------------------------------- settings table A3
    dates = {"full": "3 August 2026", "baselines": "3 August 2026", "e4e_a": "31 July 2026", "e4e_bd": "28 September 2026", "diag": "30 September 2026"}
    t_a3 = Table(headers=["Item", "Full system and compiler stages", "Rule compiler", "Three baselines"],
                 rows=[["Language model", "DeepSeek-chat, OpenAI-compatible endpoint api.deepseek.com", "none", "DeepSeek-chat, same endpoint"],
                       ["Temperature", "0", "deterministic", "0"],
                       ["Maximum output tokens", "1,800 per compiler call and per repair call", "n/a", "provider default"],
                       ["Timeout and retries", "180 s, up to 4 transport retries, errors recorded", "n/a", "same"],
                       ["Output constraint", "JSON programme by instruction, code fences stripped", "programme built directly", "json_object response format for the structured baseline only"],
                       ["Validator and repair", "type validator on every programme, one repair call after a rejection in V3 and V4, semantic coverage gate in V4", "same validator, no repair", "none"],
                       ["Executor", "deterministic CarbonQL executor on the Type A release", "same", "none, the model reads the tables"],
                       ["Runs on the 150 questions", "3 independent runs, 3 re-compiled runs for the control-policy ablation, " + ", ".join(f"{len(rates[f'stage_{v}']['per_run'])} for compiler stage {v}" for v in ("V1", "V2", "V3") if f"stage_{v}" in rates), "1, deterministic", "1 per baseline"],
                       ["Graph release", "Type A English release m2_typea1_full_en_layerdedup_20260731_d_spread", "same", "same, rendered as tables"],
                       ["Dates of the runs", f"full system {dates['full']}, compiler stages on the held-out sets {dates['e4e_a']} and {dates['e4e_bd']}, stages and control policies on the 150 questions {dates['diag']}", dates["diag"], dates["baselines"]]],
                 caption="Table A3. Model, decoding and run settings")

    # ---------------------------------------------------------------- scoring table (main text Table 4)
    t_measures = Table(headers=["Measure", "Question set", "N", "Criterion", "Aggregation"],
                       rows=[["Strict pass rate", "All questions", "150", "Returned value or response state matches the reference, the perspective and operation of the answer match, every cited record is one the executor used, and no unsupported number appears", "Mean of 3 runs"],
                             ["Strict pass rate", "Executable questions", "90", "As above", "Mean of 3 runs"],
                             ["Strict pass rate", "Boundary questions", "60", "As above", "Mean of 3 runs"],
                             ["Status accuracy", "All or boundary questions", "150 or 60", "Returned response state matches the reference", "Mean of 3 runs"],
                             ["Strict numeric accuracy", "Executable questions", "90", "Every reference number appears in the answer within a relative tolerance of 10⁻³ or 0.01 kgCO2e, under any key or in prose", "Mean of 3 runs"],
                             ["Any-hit rate", "Executable questions", "90", "At least one reference number appears within the same tolerance", "Mean of 3 runs"]],
                       caption=f"{L.tab_measures}. Evaluation measures")

    # ---------------------------------------------------------------- baseline table A4 (key-free protocol)
    def kfrow(cfg, label):
        k = kf[cfg]
        strict = f"{pc(k['strict_all_mean'])}" + (f" ± {100 * k['strict_all_pstdev']:.1f}" if k["runs"] > 1 else "")
        r = rates.get(cfg, {})
        spass = pc(tax[cfg]["pass_by_expected_status"]["executable"]) + "%" if cfg in tax and "executable" in tax[cfg]["pass_by_expected_status"] and cfg not in ("llm_only_real", "unconstrained_kg_llm_real") else "n/a"
        if cfg == "full_real":
            lo, hi = k["ci95_majority"]
            return [label, k["runs"], strict + "%", pc(k["any_hit_mean"]) + "%", spass, "—", "—", f"95% CI {pc(lo)} to {pc(hi)}%"]
        pr = k["paired_vs_full_majority"]
        return [label, k["runs"], strict + "%", pc(k["any_hit_mean"]) + "%", spass, str(pr["full_only"]), str(pr["other_only"]), fmt_p(pr["mcnemar_p"])]
    order_a4 = [("llm_only_real", "LLM reads the raw source tables, free text"), ("unconstrained_kg_llm_real", "LLM reads the knowledge graph tables, free text"),
                ("llm_structured_json_real", "LLM reads the knowledge graph tables, structured JSON"), ("rule_compiler", "Hand-written rule compiler with validator and executor"),
                ("full_real", "Full CarbonQL system")]
    t_a4 = Table(headers=["Configuration", "Runs", "Strict numeric accuracy", "Any-hit rate", "Strict pass rate on the 90", "Full only", "Other only", "McNemar exact p"],
                 rows=[kfrow(c, l) for c, l in order_a4],
                 caption="Table A4. Baselines and the full system on the 90 numeric questions under the key-free protocol of Section 4.3.4",
                 note="Full only and other only count the questions passed by exactly one of the two configurations under the majority of runs. Strict pass rate applies only to configurations that return a response state and a labelled answer.")

    # ---------------------------------------------------------------- rule compiler table A5 (held-out)
    rows_a5 = []
    for m in ("A", "B", "D"):
        h = ho[m]
        rows_a5.append([f"Type {m}", pc(h["rule_exact"]) + "%", pc(h["llm_exact_majority"]) + "%", pc(h["rule_boundary"]) + "%", pc(h["llm_boundary_majority"]) + "%",
                        pc(h["rule_status"]) + "%", pc(h["llm_status_majority"]) + "%", fmt_p(h["exact_paired"]["mcnemar_p"])])
    t_a5 = Table(headers=["Module", "Rule compiler, exact", "LLM compiler, exact", "Rule, accounting boundary", "LLM, accounting boundary", "Rule, response state", "LLM, response state", "McNemar p, exact"],
                 rows=rows_a5, caption="Table A5. Rule compiler against the frozen LLM compiler on the held-out sets of 48 questions per module",
                 note="LLM compiler values are the majority over the five repeats of Appendix C.6. Accounting boundary means that the entity scope, the emission sources and the base view of the compiled programme equal the reference.")

    # ---------------------------------------------------------------- taxonomy table A6
    tax_order = [("full_real", "Full system, 3 runs"), ("rule_compiler", "Rule compiler"), ("llm_structured_json_real", "Structured JSON baseline")]
    if have_stages:
        tax_order = [("full_real", "Full system, 3 runs"), ("stage_V3", "Compiler stage V3, 3 runs"), ("stage_V2", "Compiler stage V2, 3 runs"), ("stage_V1", "Compiler stage V1, 3 runs"),
                     ("rule_compiler", "Rule compiler"), ("llm_structured_json_real", "Structured JSON baseline")]
    cls = ["compile_error", "executor_error", "boundary_state_error", "perspective_error", "operation_error", "value_or_target_error"]
    rows_a6 = []
    for cfg, label in tax_order:
        t = tax[cfg]
        n = t["runs"]
        rows_a6.append([label, f"{t['rows'] // n}"] + [f"{t['counts'].get('pass', 0) / n:.0f}"] + [f"{t['counts'].get(c, 0) / n:.0f}" if n > 1 else str(t["counts"].get(c, 0)) for c in cls])
    t_a6 = Table(headers=["Configuration", "Questions", "Pass", "Compile", "Executor", "Boundary state", "Perspective", "Operation", "Value or target"], rows=rows_a6,
                 caption="Table A6. Failure classes per run of 150 questions, mean over runs where there are several",
                 note="A failed answer takes the first class that applies in the order of the columns. Compile means that every programme was rejected and nothing was executed. Executor means an exception or transport error. Boundary state means that the returned state differs from the reference. Perspective and operation mean that the state is right and the label is wrong. Value or target means that the state and both labels are right and a number, the target entity or a cited record is wrong.")
    bs = tax["full_real"]["boundary_split"]
    cons = bs["conservative (reference executable, returned a boundary state)"]
    over = bs["over_claim (reference boundary state, returned executable)"]
    cross = bs["cross_state (two different boundary states)"]
    js_bs = tax["llm_structured_json_real"]["boundary_split"]
    conf_full = tax["full_real"]["status_confusion"]
    n_fail_full = tax["full_real"]["rows"] - tax["full_real"]["counts"]["pass"]

    # ---------------------------------------------------------------- slot table A7
    def slotrow(label, s, key):
        v = s[key]
        return [label, pc(v["accuracy"]) + "%", pc(v["macro"]["precision"]) + "%", pc(v["macro"]["recall"]) + "%", pc(v["macro"]["f1"]) + "%",
                ", ".join(f"{c} {100 * v[c]['f1']:.0f}" for c in v if c not in ("macro", "accuracy"))]
    rows_a7 = [slotrow("Perspective, answer level, full system", slots["full_real"], "perspective"),
               slotrow("Operation, answer level, full system", slots["full_real"], "operation"),
               slotrow("Perspective, programme level, full system", slots["full_real (program level)"], "perspective"),
               slotrow("Operation, programme level, full system, explain excluded", slots["full_real (program level)"], "operation_raw"),
               slotrow("Entity scope, programme level, full system", slots["full_real (program level)"], "scope"),
               slotrow("Perspective, answer level, rule compiler", slots["rule_compiler"], "perspective"),
               slotrow("Operation, answer level, rule compiler", slots["rule_compiler"], "operation"),
               slotrow("Perspective, programme level, rule compiler", slots["rule_compiler (program level)"], "perspective"),
               slotrow("Operation, programme level, rule compiler, explain excluded", slots["rule_compiler (program level)"], "operation_raw"),
               slotrow("Perspective, answer level, structured JSON baseline", slots["llm_structured_json_real"], "perspective"),
               slotrow("Operation, answer level, structured JSON baseline", slots["llm_structured_json_real"], "operation")]
    src = slots["emission_source (held-out, LLM V4, program level)"]
    for m in ("A", "B", "D"):
        v = src[m]
        rows_a7.append([f"Emission source set, programme level, held-out Type {m}", pc(v["accuracy"]) + "%", pc(v["macro"]["precision"]) + "%", pc(v["macro"]["recall"]) + "%", pc(v["macro"]["f1"]) + "%",
                        ", ".join(f"{c} {100 * v[c]['f1']:.0f}" for c in v if c not in ("macro", "accuracy") and v[c]["support"] > 0)])
    t_a7 = Table(headers=["Slot and level", "Accuracy", "Macro precision", "Macro recall", "Macro F1", "F1 by class"], rows=rows_a7,
                 caption="Table A7. Slot classification against the reference labels",
                 note=f"Answer level uses the labels of the returned answer over the 450 answers of three runs for the full system and 150 for the others. Programme level reads the compiled programme, the perspective from the projection rule of Equation 10, the operation from the operator set and the entity scope from the selector, over the {slots['full_real (program level)']['n']} programmes of the re-compiled reference arm. Explain questions have no operator of their own and are excluded from the programme-level operation score. Emission source set uses the gold view signatures of the held-out sets, classes material, process and material+process.")

    # ---------------------------------------------------------------- ablation table A8
    def abrow(cfg, label, pairing_key=None):
        r = rates[cfg]
        t = tax[cfg]
        lo, hi = r["pass_ci95_majority"]
        strict = pc(r["pass_mean"]) + (f" ± {100 * statistics.pstdev([x['pass'] for x in r['per_run']]):.1f}" if len(r["per_run"]) > 1 else "")
        comp = t["counts"].get("compile_error", 0) / t["runs"]
        kfv = kf[cfg]["strict_all_mean"]
        if cfg == "full_real":
            return [label, len(r["per_run"]), strict + "%", pc(r["status_mean"]) + "%", pc(kfv) + "%", f"{comp:.0f}", "—", f"{pc(lo)} to {pc(hi)}%"]
        pr = pairs[cfg]["majority_vs_majority"]
        return [label, len(r["per_run"]), strict + "%", pc(r["status_mean"]) + "%", pc(kfv) + "%", f"{comp:.0f}", f"{pr['full_only']} / {pr['other_only']}, p = {fmt_p(pr['mcnemar_p'])}", f"{pc(lo)} to {pc(hi)}%"]
    rows_a8 = [abrow("full_real", "Full system, V4 compiler, all policies")]
    if "full_real_e6_recompiled" in rates:
        rows_a8.append(abrow("full_real_e6_recompiled", "Full system re-compiled for the ablation runs, reference arm"))
    if have_stages:
        rows_a8 += [abrow("stage_V3", "Compiler V3, no semantic coverage gate"), abrow("stage_V2", "Compiler V2, no repair call, no semantic gate"), abrow("stage_V1", "Compiler V1, no typed contract, no repair, no semantic gate")]
    rows_a8 += [abrow("no_status_blocking_real", "Status blocking off, partial subtotals returned"), abrow("no_provenance_constraint_real", "Provenance constraint off, no evidence identifiers"),
                abrow("rule_compiler", "Rule compiler in place of the LLM compiler"), abrow("llm_structured_json_real", "No compiler and no executor, structured JSON baseline"),
                abrow("unconstrained_kg_llm_real", "No compiler and no executor, free-text baseline"), abrow("llm_only_real", "No graph, no compiler, no executor, LLM-only baseline")]
    t_a8 = Table(headers=["Configuration", "Runs", "Strict pass rate, 150", "Status accuracy", "Strict numeric accuracy, 90, key-free", "Compile rejections per run", "Full only / other only, McNemar p", "95% bootstrap CI of the pass rate"], rows=rows_a8,
                 caption="Table A8. Ablations on the 150 questions with paired tests against the full system",
                 note="Paired counts and p values use the majority over runs. For the two control policies the pairing is by compiled programme within a run against the re-compiled reference arm, so the comparison isolates the policy. The free-text baselines return no response state, so their strict pass rate is zero by construction and their contribution is read from the key-free column.")

    # stage facts
    def stage_fact(cfg):
        r = rates.get(cfg)
        if not r:
            return None
        return {"pass": r["pass_mean"], "status": r["status_mean"], "compile": tax[cfg]["counts"].get("compile_error", 0) / tax[cfg]["runs"], "kf": kf[cfg]["strict_all_mean"], "p": pairs[cfg]["majority_vs_majority"]["mcnemar_p"]}
    st = {v: stage_fact(f"stage_{v}") for v in ("V1", "V2", "V3")}
    ref = stage_fact("full_real_e6_recompiled") if "full_real_e6_recompiled" in rates else None
    unval = []
    for d in sorted((DIAG / "D6_compiler_stages_v16").glob("stage_V1_run*/unvalidated_summary.json")):
        unval.append(load_json(d)["metrics"])
    e4a = e4e.get("A", {}).get("variants", {})
    e4p = {x["step"]: x for x in e4e.get("A", {}).get("paired", {}).get("answer_exact_match", [])}

    # ---------------------------------------------------------------- prose
    A = {}
    A["title"] = f"{L.app}. Benchmark construction, experimental settings and diagnostics"
    A["intro"] = ("This appendix reports how the 150-question benchmark of Section 4.3 was written and checked, the model, decoding and prompt settings of every configuration, "
                  "the scoring rules, and the failure analysis, slot classification, ablations and paired tests behind Sections 4.3.2 and 4.3.4. All values come from the stored run logs, "
                  f"and the held-out transfer test is reported in {L.app_c_heldout}.")
    lineage = sorted((d, tag) for d, tag in card["frozen_lineage"] if not tag.startswith("v") or True)
    frozen_dirs = sorted(p.name for p in (RE_DIR / "e4_benchmark").iterdir() if p.name.startswith("frozen_"))
    lineage = [(re.match(r"frozen_(\d{8})", n).group(1), n) for n in frozen_dirs]
    first_date, last_date = lineage[0][0], lineage[-1][0]
    bsum = load_json(RE_DIR / "e5_real_baselines_layerdedup/baselines_v16_en_kg_realloc/e5_real_baseline_summary.json")["variants"]
    unsupported_prose = bsum["unconstrained_kg_llm_real"]["unsupported_answer_rate"]
    halluc_json = bsum["llm_structured_json_real"]["hallucinated_reference_rate"]
    A["a1"] = [
        ("para", f"The benchmark holds 150 questions on the Type A module graph. Each question fixes one carbon perspective, product, material or process, one operation, value, rank, compare, explain or trace, and one response state, and its reference answer is computed from the frozen graph release and not written by hand. "
                 f"For every cell of the perspective by operation by state matrix a generator composed a template question, selected the target records or components, and ran the reference programme against the release, so that the reference numbers of the {status_counts['executable']} executable questions and the reference states of the {150 - status_counts['executable']} boundary questions follow from the release and are reproducible from it. "
                 f"The generation matrix also held a traceability family, whose questions ask for the quantity, factor and source chain behind a record and carry the perspective of the record they trace, and an aggregate operation, which the scorer merges with value. "
                 f"The boundary states are incomplete_path, where the chain behind the requested record is incomplete, empty_result, where the request is well formed and no record satisfies it, unresolved_target, where a named component, material or process does not resolve to one entity, and clarification_required, where the question withholds the target, the source or the grouping dimension."),
        ("para", f"The template wording was rewritten into natural questions by DeepSeek-chat at temperature 0 and then revised by the authors in seven review rounds, which checked the wording, the labels and the state of every question against the graph, and every one of the 150 questions differs from its template. "
                 f"When the deterministic references were rebuilt for later graph releases the reviewed wording was reused and the references recomputed, and the benchmark was frozen {len(lineage)} times between {first_date[:4]}-{first_date[4:6]}-{first_date[6:]} and {last_date[:4]}-{last_date[4:6]}-{last_date[6:]}. "
                 f"The version used in Section 4.3 is v16, SHA-256 {card['sha256'][:16]}, and its questions average {card['mean_question_words']:.1f} words. "
                 f"Table A1 gives the coverage. {scope['selected_component']} questions are asked with BIM items selected in the viewer and refer to them as the selected items, the other {scope['project']} address the project. "
                 f"Three difficulty tags follow the retrieval structure, single_hop questions read one account of the project, multi_hop questions resolve selected components or compare, explain or trace across record sets, and batch_allocation questions concern the factory energy records of the production batch and their allocation."),
        ("table", t_a1),
    ]
    A["a2"] = [
        ("para", f"The reference perspective, operation and state of every question are generated labels checked in review, so a second annotator, a doctoral researcher in the same field who had not seen the reference labels, labelled {sampling['annotation_quota']['executable'] + sum(v for k, v in sampling['annotation_quota'].items() if k != 'executable')} questions blind, "
                 f"stratified by reference state, with the question text and the viewer selection only, without the system, the graph or the reference answers. Table A2 reports the agreement. "
                 f"Perspective and operation are recovered from the wording, with κ of {kp['kappa']:.2f} and {ko['kappa']:.2f}. "
                 f"The response state is not. The annotator labelled {marked_exec} of the {ks['n']} questions executable, so the agreement on the state is {pc(ks['agreement'])} percent and κ is {ks['kappa']:.2f}, "
                 f"because the state depends on what the graph release holds for the named target and cannot be read from the question alone. The reference states are therefore treated as properties of the question against the release, computed by executing the reference programme, and not as human judgements. "
                 f"The annotator's notes flag {n_compound} questions as combining two operations, such as compare and trace, {n_evidence} ask what the word evidence refers to, and one question was left unlabelled as unclear. These wordings come from the traceability templates and are kept as they are, since the compiler is scored on them like on every other question."),
        ("table", t_a2),
    ]
    A["a3_note"] = None
    A["a4"] = [
        ("para", "The compiler contract, its rule list and its three worked examples were developed while the 150 questions were available and the compiler was run on them, so the 150-question set is a development set and the figures of Sections 4.3.2 to 4.3.4 are development-set figures. "
                 "None of the 150 questions appears in a prompt, none of the three worked examples is a benchmark question, and the closest benchmark question has a string similarity of 0.70 to a worked example. "
                 f"The held-out sets of {L.app_c_heldout}, 48 new questions per module written after the compiler contract was frozen, share no question with the 150 and are the test on unseen wording. "
                 "The reference fields of a benchmark row are read only by the scorer, the compiler receives the question and the selected component identifiers, and the executor receives the programme. The baseline prompts contain no benchmark question and no CarbonQL example."),
    ]
    A["a5"] = [
        ("para", "Table A3 lists the model, decoding and run settings of every configuration. The model comparison of Section 5.1 used the same benchmark and three runs per model, and its DeepSeek-chat value of 81.8 percent comes from a separate set of three runs from the 81.3 percent of Section 4.3.2, a difference of 0.5 points within the spread of the runs."),
        ("table", t_a3),
    ]
    rules = contract["rules"]
    examples = contract["simple_single_view_examples"]
    A["a6"] = [
        ("para", "The compiler receives two messages. The system message is fixed and reads as follows."),
        ("code", sys_prompt),
        ("para", "The user message is one JSON object with the question, the selected component identifiers and the contract. The contract lists the JSON shape, the hole contract, the allowed operations and the three worked examples in every variant, and adds in V2 to V4 the operator type signatures, the argument contract, the allowed carbon sources, group keys and aggregate metrics, the schema of the loaded release with its dimension names and entity counts, and the following rules."),
        ("numbered", rules),
        ("para", "The three worked examples are the only examples in the prompt and are not benchmark questions."),
        ("code", json.dumps(examples, ensure_ascii=False, indent=1)),
        ("para", "When the validator rejects a programme in V3 or V4, one repair call is made with the following system message and a user message holding the question, the selection, the rejected programme, the reported error and the same contracts."),
        ("code", repair[0]["content"]),
        ("para", "The three baselines receive one system message holding a preamble, a JSON snapshot of the data and, for the structured baseline, an output instruction, followed by the question as the user message. The preambles read as follows."),
        ("code", "Raw-table baseline. " + bprompts["llm_only_real"]["preamble"]),
        ("code", "Knowledge graph baselines. " + bprompts["unconstrained_kg_llm_real"]["preamble"]),
        ("para", f"The raw-table snapshot holds the material quantity rows, the energy records and the emission factors of the release, {bprompts['llm_only_real']['snapshot_table_rows']['material_quantities']}, {bprompts['llm_only_real']['snapshot_table_rows']['energy_records']} and {bprompts['llm_only_real']['snapshot_table_rows']['emission_factors']} rows, about {bprompts['llm_only_real']['system_prompt_chars']:,} characters, with the note that carbon is not precomputed and must be obtained by multiplying quantities and factors. "
                 f"The knowledge graph snapshot holds the component, material family and carrier totals, the material emission records, the energy records and the factors, {bprompts['unconstrained_kg_llm_real']['snapshot_table_rows']['components']}, {bprompts['unconstrained_kg_llm_real']['snapshot_table_rows']['material_families']}, {bprompts['unconstrained_kg_llm_real']['snapshot_table_rows']['carriers']}, {bprompts['unconstrained_kg_llm_real']['snapshot_table_rows']['material_emissions']}, {bprompts['unconstrained_kg_llm_real']['snapshot_table_rows']['energy_records']} and {bprompts['unconstrained_kg_llm_real']['snapshot_table_rows']['emission_factors']} rows, about {bprompts['unconstrained_kg_llm_real']['system_prompt_chars']:,} characters, with the note that carbon is already computed per entity. The structured baseline appends the following instruction and is called with the json_object response format."),
        ("code", bprompts["llm_structured_json_real"]["structured_json_instruction"]),
    ]
    A["a7"] = [
        ("para", f"The three baselines and the full system share the model, the temperature and the graph release, and differ in what the model is asked to do. The baselines answer directly from tables, the full system compiles a programme and executes it. "
                 f"Section 4.3.4 scores every configuration on the {kf['full_real']['n']} numeric questions under a key-free protocol, every non-zero reference number must appear among the numbers written in the answer text or its summary within a relative tolerance of 10⁻³ or an absolute tolerance of 0.01 kgCO2e, since prose answers round their numbers, and the any-hit rate credits at least one reference number. "
                 f"{L.tab_measures} in Section 4.3.1 defines the strict pass rate used elsewhere, which also requires the response state, the perspective and operation labels and the provenance of the answer to be right. Table A4 reports both readings and the paired tests. "
                 f"The full system passes {kf['unconstrained_kg_llm_real']['paired_vs_full_majority']['full_only']} numeric questions that the knowledge graph free-text baseline fails and fails {kf['unconstrained_kg_llm_real']['paired_vs_full_majority']['other_only']} that it passes, McNemar exact p = {fmt_p(kf['unconstrained_kg_llm_real']['paired_vs_full_majority']['mcnemar_p'])}. "
                 f"Unsupported values appear in {pcw(unsupported_prose)} of the free-text answers of both prose baselines, and the structured baseline cites record identifiers absent from the release in {pcw(halluc_json, 0)} of its answers and returns executable for {js_bs['over_claim (reference boundary state, returned executable)']} of the 60 boundary questions."),
        ("table", t_a4),
    ]
    sel = rule["selector_rule_counts"]
    gaz = rule["gazetteer_sizes"]
    A["a8"] = [
        ("para", f"To separate the contribution of the language model from that of the typed programme, the validator and the executor, an author wrote a deterministic rule compiler that maps a question to a CarbonQL programme without any model call. "
                 f"The rules read the viewer selection, deictic phrases, named targets and hits in gazetteers built from the release, {gaz['material']} material names, {gaz['component_type']} component types, {gaz['process']} process names, {gaz['carrier']} carriers and {gaz['component']} component names, to choose the selector, and cue words to choose the carbon source, the grouping keys, the known-total flag and the operation. "
                 f"On the 150 questions the selector came from the project scope in {sel['project']} cases, from the selected items in {sel['selected_items']}, from a material or component type in the gazetteer in {sel['gazetteer:material'] + sel['gazetteer:component_type']}, from a searched phrase in {sel['searched_phrase']}, from a deictic phrase in {sum(v for k, v in sel.items() if k.startswith('deictic'))} and from a named target in {sel['named_target']}. "
                 f"Every programme passed the same validator, executor, answer adapter and scorer as the programmes of the language model, and compilation took {rule['metrics']['mean_latency_ms']:.1f} ms per question."),
        ("para", f"On the 150 questions the rule compiler reaches a strict pass rate of {pcw(rule['metrics']['pass_rate'])}, status accuracy of {pcw(rule['metrics']['status_accuracy'])} and key-free strict numeric accuracy of {pcw(kf['rule_compiler']['strict_all_mean'])}, against {pcw(fs['pass_mean'])}, {pcw(fs['status_all'])} and {pcw(kf['full_real']['strict_all_mean'])} for the full system, and the full system passes {pairs['rule_compiler']['majority_vs_majority']['full_only']} questions that the rules fail and fails {pairs['rule_compiler']['majority_vs_majority']['other_only']} that the rules pass, p = {fmt_p(pairs['rule_compiler']['majority_vs_majority']['mcnemar_p'])}. "
                 f"The rules were written with the 150 questions visible, as the compiler contract was, so this comparison favours neither side. On the held-out sets, whose wording neither saw, the rules keep the response state right on {pcw(ho['A']['rule_status'])} of the questions and reach the reference accounting boundary on {pcw(ho['A']['rule_boundary'])}, against {pcw(ho['A']['llm_boundary_majority'])} to {pcw(ho['D']['llm_boundary_majority'])} for the frozen language model compiler, Table A5. "
                 f"Cue words therefore suffice for the state and for the answer-level labels, and the language model contributes the mapping from unseen wording to the source set, the grouping and the base view of the account."),
        ("table", t_a5),
    ]
    A["a9"] = [
        ("para", f"Table A6 classifies every failed answer into one of six classes in a fixed order, and Table A1 of Section 4.3.1 defines the pass conditions they refer to. "
                 f"Of the {n_fail_full} failures of the full system over three runs, none is a compile error or an executor error, since every programme that the validator accepted executed, and {tax['full_real']['counts']['boundary_state_error']} are boundary-state errors. "
                 f"In {cons} of the boundary-state errors the reference is executable and the system returned a boundary state, {conf_full.get('executable -> incomplete_path', 0)} times incomplete_path and {conf_full.get('executable -> empty_result', 0)} times empty_result, because the compiled programme selected a narrower source, filter or target than the question named, so the executor found the chain incomplete or the record set empty and the system disclosed that state in place of a number. "
                 f"In {over} the reference is a boundary state and the system returned a number, and in {cross} two boundary states are confused. "
                 f"{tax['full_real']['counts'].get('perspective_error', 0)} failures are perspective errors and {tax['full_real']['counts'].get('operation_error', 0)} operation errors with the state right, and {tax['full_real']['counts'].get('value_or_target_error', 0)} are value or target errors with the state and both labels right, most of them rank questions whose programme grouped by a different key than the reference. "
                 f"Data errors do not appear as a class, because the reference is computed from the same release, so a gap in the data changes the reference state and the answer alike, and the eight rejected material records and their bound are reported in {L.app_c_rejected}. "
                 f"By reference state the full system passes {pc(tax['full_real']['pass_by_expected_status']['executable'])} percent of the executable questions, {pc(tax['full_real']['pass_by_expected_status']['incomplete_path'])} percent of incomplete_path, {pc(tax['full_real']['pass_by_expected_status']['empty_result'])} percent of empty_result, {pc(tax['full_real']['pass_by_expected_status']['unresolved_target'])} percent of unresolved_target and {pc(tax['full_real']['pass_by_expected_status']['clarification_required'])} percent of clarification_required questions, and by difficulty {pc(tax['full_real']['pass_by_difficulty']['single_hop'])}, {pc(tax['full_real']['pass_by_difficulty']['multi_hop'])} and {pc(tax['full_real']['pass_by_difficulty']['batch_allocation'])} percent of the single_hop, multi_hop and batch_allocation questions. "
                 f"The structured JSON baseline shows the opposite pattern, {js_bs['over_claim (reference boundary state, returned executable)']} of its {sum(js_bs.values())} state errors return a number where the reference is a boundary state and none returns a boundary state where a number exists."),
        ("table", t_a6),
    ]
    A["a10"] = [
        ("para", f"Table A7 reports precision, recall and F1 for the perspective, the operation and the entity scope, at two levels. The answer level takes the labels attached to the returned answer, which the answer adapter derives from the executed programme and, for explain questions, from the wording of the question, and is the level the strict pass rate uses. "
                 f"The programme level reads the compiled programme itself, the perspective from the projection rule of Equation 10, the operation from the operator set and the entity scope from the selector, and is the level at which the compiler can be wrong before the executor and the adapter act. "
                 f"At the answer level the full system reaches macro F1 of {pc(slots['full_real']['perspective']['macro']['f1'])} percent for the perspective and {pc(slots['full_real']['operation']['macro']['f1'])} percent for the operation, at the programme level {pc(slots['full_real (program level)']['perspective']['macro']['f1'])} and {pc(slots['full_real (program level)']['operation_raw']['macro']['f1'])} percent, with trace the weakest operation class, and the entity scope follows the viewer selection that the compiler receives and is right in every programme. "
                 f"The source scope is scored on the held-out sets, where the gold view signature names the emission sources of the reference programme, and the frozen compiler selects the right source set for {pc(src['A']['accuracy'])} percent of the Type A questions and {pc(src['B']['accuracy'])} percent of the Type B and Type D questions. "
                 f"The gap between the two levels is the work of the answer adapter and it is reported so that the answer-level rates are not read as compiler accuracy."),
        ("table", t_a7),
    ]
    a11_paras = []
    a11_paras.append(
        f"Table A8 ablates the layers that Section 4.3.4 names on the same 150 questions and pairs every configuration with the full system by question, with the exact McNemar test on the majority over runs and a bootstrap 95 percent confidence interval of the pass rate. "
        f"The compiler stages follow the four variants of {L.app_c_heldout}. V1 sends the JSON shape, the allowed operations and the three worked examples, V2 adds the typed operator and argument contract, the schema and the rule list, V3 adds one validator-driven repair call, and V4, the full system, adds the semantic coverage gate. "
        f"The two control policies act after compilation, status blocking withholds a partial known subtotal and returns incomplete_path, and the provenance constraint attaches the identifiers of the records the executor used.")
    if have_stages and ref:
        a11_paras.append(
            f"The typed contract is the largest step. Without it the validator rejects on average {st['V1']['compile']:.0f} of the 150 first programmes per run and the strict pass rate is {pcw(st['V1']['pass'])}, with the contract the rejections fall to {st['V2']['compile']:.0f} and the pass rate rises to {pcw(st['V2']['pass'])}, the repair call recovers rejected programmes and gives {pcw(st['V3']['pass'])} with {st['V3']['compile']:.0f} rejections, and the semantic gate gives {pcw(ref['pass'])} with {ref['compile']:.0f} rejections in the re-compiled reference arm, "
            f"p = {fmt_p(st['V1']['p'])}, {fmt_p(st['V2']['p'])} and {fmt_p(st['V3']['p'])} for V1, V2 and V3 against the full system. "
            f"On 150 questions the paired test therefore separates the contract step and not the repair call or the semantic gate, whose gains of {100 * (st['V3']['pass'] - st['V2']['pass']):.1f} and {100 * (ref['pass'] - st['V3']['pass']):.1f} points lie within the run-to-run spread. "
            f"On the held-out Type A set the same stages give {pc(e4a['V1']['answer_exact_match']['mean'])}, {pc(e4a['V2']['answer_exact_match']['mean'])}, {pc(e4a['V3']['answer_exact_match']['mean'])} and {pc(e4a['V4']['answer_exact_match']['mean'])} percent exact answers over five repeats, and there the test separates both the contract step, p = {e4p['V1->V2']['mcnemar_exact_p']:.4f}, and the semantic gate step, p = {e4p['V3->V4']['mcnemar_exact_p']:.4f}, because the gate matters most on wording the contract has not seen.")
        if unval:
            runs_txt = " and ".join(f"{m['executor_exception']} of {m['executor_exception']} in run {k}" for k, m in enumerate(unval, 1))
            a11_paras.append(
                f"The validator cannot be removed on its own, because the executor enforces the same typing rules. Executing the rejected V1 programmes without validation raises a typing error in the executor in every case, {runs_txt}, so the pass rate does not change, and the contribution of the validator is the rejection code that drives the repair call of V3 and the coverage check of V4, together with a compile error that names the fault in place of an executor exception.")
    a11_paras.append(
        f"The two control policies do not change the pass rate on this benchmark. Switching off status blocking changes the outcome of one question per run, and switching off the provenance constraint changes none, because the strict pass rule checks the cited records against the records the executor used and an answer without citations passes that check. "
        f"Their effect is on what the answer discloses, the partial subtotal that status blocking withholds and the record identifiers that the provenance constraint attaches, which the pass rate does not measure. "
        f"The graph and the executor are ablated by the baselines, {pcw(kf['llm_only_real']['strict_all_mean'])} strict numeric accuracy without the graph, {pcw(kf['unconstrained_kg_llm_real']['strict_all_mean'])} with the graph and no executor, {pcw(kf['full_real']['strict_all_mean'])} with both, and the language model compiler is ablated by the rule compiler of Appendix A.8, {pcw(rates['rule_compiler']['pass_mean'])} strict pass against {pcw(fs['pass_mean'])}, p = {fmt_p(pairs['rule_compiler']['majority_vs_majority']['mcnemar_p'])}.")
    A["a11"] = [("para", p) for p in a11_paras] + [("table", t_a8)]

    sections = [
        {"heading": f"{L.app}.1 Question generation and reference answers", "blocks": A["a1"]},
        {"heading": f"{L.app}.2 Annotation agreement", "blocks": A["a2"]},
        {"heading": f"{L.app}.3 Prompt development and the benchmark", "blocks": A["a4"]},
        {"heading": f"{L.app}.4 Model, decoding and run settings", "blocks": A["a5"]},
        {"heading": f"{L.app}.5 Prompt templates", "blocks": A["a6"]},
        {"heading": f"{L.app}.6 Baseline construction and the key-free protocol", "blocks": A["a7"]},
        {"heading": f"{L.app}.7 Hand-written rule compiler", "blocks": A["a8"]},
        {"heading": f"{L.app}.8 Failure classes", "blocks": A["a9"]},
        {"heading": f"{L.app}.9 Slot classification", "blocks": A["a10"]},
        {"heading": f"{L.app}.10 Ablations and paired tests", "blocks": A["a11"]},
    ]
    # fix internal cross references to the renumbered subsections
    def fix(s: str) -> str:
        return s.replace("Appendix A.8", f"{L.app}.7").replace("Table A1 of Section 4.3.1", f"{L.tab_measures} of Section 4.3.1")
    for sec in sections:
        sec["blocks"] = [(b[0], fix(b[1])) if isinstance(b[1], str) else b for b in sec["blocks"]]
    appendix = {"title": A["title"], "intro": A["intro"], "sections": sections}

    # ---------------------------------------------------------------- main text changes
    M = {}
    M["sec431_para"] = (
        f"{L.tab_measures} defines the measures used in this section. All rates are means over three independent runs of the same question set. "
        f"The strict pass rate requires four conditions to hold together, the returned value or response state matches the reference, the perspective and operation of the answer match the reference, every record the answer cites is one the executor used, and no unsupported number appears in the answer. "
        f"Strict numeric accuracy, used in the baseline comparison, requires only that every reference number appear in the answer within tolerance, so on the same 90 executable questions it is a necessary condition for a strict pass and the pass rate lies at or below it. "
        f"Status accuracy checks the response state alone. {L.app} reports how the questions were written and checked, the agreement of a second annotator, the model, decoding and prompt settings, the baseline construction, and the failure analysis, slot classification and ablations behind Sections 4.3.2 and 4.3.4.")
    M["tab_measures"] = t_measures
    M["sec432_old_sd"] = "Across three runs, the mean strict pass rate was 81.3% with a standard deviation of 0.8, showing stable performance under the fixed setting."
    M["sec432_new_sd"] = (f"Across three runs the strict pass rate ({L.tab_measures}) was {pc(fs['pass_per_run'][0])}%, {pc(fs['pass_per_run'][1])}% and {pc(fs['pass_per_run'][2])}%, a mean of {pc(fs['pass_mean'])}% with a standard deviation of {100 * fs['pass_pstdev']:.1f}, "
                          f"and the 95% bootstrap confidence interval of the majority outcome is {pc(rates['full_real']['pass_ci95_majority'][0])} to {pc(rates['full_real']['pass_ci95_majority'][1])}%.")
    M["sec432_old_boundary"] = "Boundary questions reached 92.8% status accuracy, while executable questions achieved a strict pass rate of 76.3%, so"
    M["sec432_new_boundary"] = f"Boundary questions reached {pc(fs['boundary_status'])}% status accuracy and a strict pass rate of {pc(fs['boundary_pass'])}%, while executable questions achieved a strict pass rate of {pc(fs['executable_pass'])}%, so"
    M["sec432_append"] = (
        f"{L.app}.8 classifies the {n_fail_full} failures of the three runs. {word(cons + over + cross)} are boundary-state errors, and in {cons} of them the graph supported an answer and the system returned incomplete_path or empty_result because the compiled programme selected a narrower source, filter or target than the question named, so the system erred towards disclosure and not towards an unsupported number. "
        f"{word(tax['full_real']['counts'].get('perspective_error', 0))} are perspective errors, {tax['full_real']['counts'].get('operation_error', 0)} are operation errors and {tax['full_real']['counts'].get('value_or_target_error', 0)} are value or target errors with the state and both labels right. No failure arose in the executor, and at the programme level the compiler selects the reference perspective with a macro F1 of {pc(slots['full_real (program level)']['perspective']['macro']['f1'])}% and the reference operation with {pc(slots['full_real (program level)']['operation_raw']['macro']['f1'])}% ({L.app}.9).")
    M["sec434_heading_old"] = "4.3.4 Baseline comparison"
    M["sec434_heading_new"] = "4.3.4 Baseline comparison and ablation"
    M["sec434_old_tol"] = "under key-free strict scoring, where every expected number must appear in the response within tolerance τ = 10⁻⁴."
    M["sec434_new_tol"] = f"under key-free strict scoring, where every expected number must appear in the response within a relative tolerance of 10⁻³ or an absolute tolerance of 0.01 kgCO2e, because prose answers round their numbers ({L.app}.6 gives the protocol and the prompts)."
    M["sec434_old_full"] = "The full CarbonQL system achieved 82.6 % strict accuracy and 85.9 % any-hit accuracy."
    M["sec434_new_full"] = (f"The full CarbonQL system achieved {pc(kf['full_real']['strict_all_mean'])}% strict accuracy and {pc(kf['full_real']['any_hit_mean'])}% any-hit accuracy, and on the same 90 questions its strict pass rate under {L.tab_measures} is {pc(fs['executable_pass'])}%, the difference being answers with correct numbers whose response state, labels or provenance did not meet the pass conditions. "
                            f"Paired by question, the full system passes {kf['unconstrained_kg_llm_real']['paired_vs_full_majority']['full_only']} questions that the knowledge graph prose baseline fails and fails {kf['unconstrained_kg_llm_real']['paired_vs_full_majority']['other_only']} that the baseline passes, McNemar exact p = {fmt_p(kf['unconstrained_kg_llm_real']['paired_vs_full_majority']['mcnemar_p'])}, and p = {fmt_p(kf['llm_only_real']['paired_vs_full_majority']['mcnemar_p'])} and {fmt_p(kf['llm_structured_json_real']['paired_vs_full_majority']['mcnemar_p'])} against the raw-table and JSON baselines.")
    M["sec434_old_remaining"] = "The remaining 17.4 % failures mainly arose during compilation."
    M["sec434_new_remaining"] = f"The remaining {pc(1 - kf['full_real']['strict_all_mean'])}% failures arose during compilation, none in the executor, and {L.app}.8 classifies them."
    abl = (f"{L.app}.10 ablates the compiler stages and the control policies on the same 150 questions with paired tests. ")
    if have_stages and ref:
        abl += (f"Without the typed operator contract the validator rejects on average {st['V1']['compile']:.0f} of the 150 first programmes and the strict pass rate is {pc(st['V1']['pass'])}%, p = {fmt_p(st['V1']['p'])} against the full system. "
                f"The contract raises the rate to {pc(st['V2']['pass'])}%, the validator-driven repair call to {pc(st['V3']['pass'])}% and the semantic coverage gate to {pc(ref['pass'])}% in the re-compiled reference arm, and the paired test does not separate these two later steps on 150 questions, p = {fmt_p(st['V2']['p'])} and {fmt_p(st['V3']['p'])}, and does separate the semantic gate step on the held-out set of {L.app_c_heldout}, p = {e4p['V3->V4']['mcnemar_exact_p']:.3f}. "
                f"The executor enforces the same typing rules as the validator, so removing the validator turns each rejection into an executor exception and changes no pass. ")
    abl += (f"A hand-written rule compiler that maps cue words and the graph vocabulary to CarbonQL programmes and shares the validator and executor reaches {pc(rates['rule_compiler']['pass_mean'])}% strict pass on the 150 questions, p = {fmt_p(pairs['rule_compiler']['majority_vs_majority']['mcnemar_p'])} against the full system, and {pc(ho['A']['rule_exact'])} to {pc(ho['B']['rule_exact'])}% exact answers on the held-out sets of {L.app_c_heldout} against {pc(ho['A']['llm_exact_majority'])} to {pc(ho['B']['llm_exact_majority'])}% for the frozen LLM compiler, so the language model contributes the mapping from unseen wording to the accounting boundary. "
            f"Switching off status blocking or the provenance constraint changes the outcome of at most one question per run, because these policies govern what the answer discloses and not the number it returns.")
    M["sec434_ablation_para"] = abl
    M["sec52_append"] = (f"The 150-question benchmark served as the development set of the compiler contract, so the held-out sets of {L.app_c_heldout} are the only test on unseen wording, and a second annotator from the same field recovered the perspective and operation of the questions from their wording but not their response state, which is a property of the graph release ({L.app}.2).")
    M["table4_old"] = "Table 4"
    M["table4_new"] = L.tab_phrasing_new

    facts = {"pass_per_run": fs["pass_per_run"], "stages": st, "ref": ref, "have_stages": have_stages, "n_fail_full": n_fail_full, "marked_exec": marked_exec, "notes": notes}
    return Content(labels=L, appendix=appendix, main=M, facts=facts)


if __name__ == "__main__":
    C = build_content()
    for sec in C.appendix["sections"]:
        print("##", sec["heading"])
        for b in sec["blocks"]:
            if b[0] == "para":
                print(b[1], "\n")
            elif b[0] == "table":
                print("[", b[1].caption, "]", len(b[1].rows), "rows\n")
    print(json.dumps({k: v for k, v in C.facts.items() if k != "notes"}, indent=1, default=str))
