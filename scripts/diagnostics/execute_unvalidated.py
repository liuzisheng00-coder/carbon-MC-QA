# -*- coding: utf-8 -*-
"""D6b: execute the first-attempt programmes of a compiler stage without the type validator.

The validator normally rejects a programme that fails the typing rules and the case ends
as a compile error. This script takes the first programme returned by the language model
(`first_program` in compiled_programs.jsonl), skips validation, and sends it straight to
the executor, so the outcome without a validator is observed: an executor exception, a
wrong answer, or a pass.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dm2c_carbonql import CarbonQLProgram  # noqa: E402
from dm2c_e5_e7_experiment_runner import evaluate_benchmark_response, load_benchmark_cases, summarize_results  # noqa: E402
from dm2c_full_qa_experiment_runner import QueryPolicy, execute_canonical_query, load_full_qa_context  # noqa: E402
from dm2c_qa_answer_adapter import adapt_observed  # noqa: E402
from run_e6_v16 import DEFAULT_BENCHMARK, DEFAULT_KG, program_slots  # noqa: E402

STAGES_ROOT = ROOT / "outputs/research_experiments/diagnostics_20260930/D6_compiler_stages_v16"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    ap.add_argument("--kg-dir", type=Path, default=DEFAULT_KG)
    ap.add_argument("--stages-root", type=Path, default=STAGES_ROOT)
    ap.add_argument("--dirs", nargs="*", default=None, help="stage run directories; default every stage_V*_run* present")
    args = ap.parse_args(argv)

    cases = {c.case_id: c for c in load_benchmark_cases(args.benchmark)}
    context = load_full_qa_context(args.kg_dir, allow_synthetic=True)
    dirs = [Path(d) for d in args.dirs] if args.dirs else sorted(p for p in args.stages_root.glob("stage_V*_run*") if p.is_dir())
    for d in dirs:
        meta_rows = [json.loads(l) for l in (d / "compiled_programs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        variant = d.name.split("_run")[0] + "_novalidator"
        evaluated = []
        counts = {"unparseable": 0, "executor_exception": 0, "executed": 0, "validated_same": 0}
        for index, meta in enumerate(meta_rows, 1):
            case = cases[meta["case_id"]]
            first = meta.get("first_program")
            lat = meta["compile_latency_ms"]
            if first is None:
                counts["unparseable"] += 1
                observed = {"status": "not_executed", "coverage_status": "unparseable_output", "perspective": "", "operation": "",
                            "summary": {}, "evidence_ids": [], "compiler_status": meta["compiler_status"], "latency_ms": lat}
                prog_meta = {}
            else:
                if meta.get("program") == first:
                    counts["validated_same"] += 1
                try:
                    program = CarbonQLProgram.from_dict(first)
                    t0 = time.perf_counter()
                    result = execute_canonical_query(context, program, case.selected_component_ids, policy=QueryPolicy(allow_partial_known_subtotal=False))
                    observed = adapt_observed(question=case.question, program=program, result=result, context=context,
                                              selected_component_ids=case.selected_component_ids, elapsed_ms=lat + (time.perf_counter() - t0) * 1000)
                    counts["executed"] += 1
                    try:
                        prog_meta = program_slots(program)
                    except Exception:
                        prog_meta = {}
                except Exception as exc:
                    counts["executor_exception"] += 1
                    observed = {"status": "executor_error", "coverage_status": "executor_error", "perspective": "", "operation": "",
                                "summary": {}, "evidence_ids": [], "executor_error": f"{type(exc).__name__}: {exc}"[:400], "latency_ms": lat}
                    prog_meta = {}
            observed["variant"] = variant
            observed["compiler_status"] = "unvalidated"
            row = evaluate_benchmark_response(case, observed, variant)
            row["case_index"] = index
            row["run"] = int(d.name.rsplit("run", 1)[1])
            row["program"] = first
            row.update(prog_meta)
            row["validator_verdict"] = meta["compiler_status"]
            evaluated.append(row)
        metrics = dict(summarize_results(evaluated))
        metrics.update(counts)
        (d / "unvalidated_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in evaluated), encoding="utf-8")
        (d / "unvalidated_summary.json").write_text(json.dumps({"variant": variant, "source_dir": str(d), "metrics": metrics,
                                                                 "note": "first-attempt programme executed without validation; exceptions recorded as executor_error"}, indent=2), encoding="utf-8")
        print(d.name, "->", variant, {k: metrics.get(k) for k in ("pass_rate", "status_accuracy", "numeric_accuracy")}, counts, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
