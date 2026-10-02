# -*- coding: utf-8 -*-
"""D1: rerun the E6 control-policy ablations on the frozen v16 benchmark and the Type A
d_spread release, recording the compiled program of every case.

Same function chain as dm2c_real_ablation_runner.py (V4 compilation once per case, then
execution under the four policies, answer adaptation and shared scoring); the only
additions are the per-case program record and the program-level slot labels needed by
the diagnostics report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient  # noqa: E402
from dm2c_carbonql import CarbonQLProgram, GraphSchema, derive_projection_perspective  # noqa: E402
from dm2c_carbonql_synthesizer import CarbonQLSynthesizer  # noqa: E402
from dm2c_e5_e7_experiment_runner import evaluate_benchmark_response, load_benchmark_cases, summarize_results  # noqa: E402
from dm2c_full_qa_experiment_runner import QueryPolicy, _operation, execute_canonical_query, load_full_qa_context  # noqa: E402
from dm2c_qa_answer_adapter import adapt_observed, map_perspective  # noqa: E402
from dm2c_real_ablation_runner import REAL_ABLATION_POLICIES, _scorable_observed  # noqa: E402

DEFAULT_BENCHMARK = ROOT / "outputs/research_experiments/e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc/e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl"
DEFAULT_KG = ROOT / "outputs/research_experiments/multi_module_20260928/releases/typea_clean"
DEFAULT_OUT = ROOT / "outputs/research_experiments/e6_real_ablations_typea1_en"


def program_slots(program) -> dict:
    sources = set()
    group_keys = []
    for step in program.steps:
        if step.op == "CarbonAtoms":
            v = step.args.get("source")
            vals = (v,) if isinstance(v, str) else tuple(v or ())
            sources.update("material" if x == "material" else "process" if x == "process" else x for x in vals)
        elif step.op == "GroupBy":
            raw = step.args.get("keys")
            group_keys.extend((raw,) if isinstance(raw, str) else tuple(raw or ()))
    selector = program.steps[0].op if program.steps else ""
    return {
        "program_perspective": map_perspective(derive_projection_perspective(program)),
        "program_raw_perspective": derive_projection_perspective(program),
        "program_operation": _operation(program),
        "program_sources": sorted(sources),
        "program_selector": selector,
        "program_scope": "selected_component" if selector in {"SelectClicked", "ResolveEntities"} else "project",
        "program_group_keys": group_keys,
        "program_holes": [h.to_dict() for h in program.holes],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    ap.add_argument("--kg-dir", type=Path, default=DEFAULT_KG)
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--runs", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args(argv)

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    cases = load_benchmark_cases(args.benchmark)
    if args.limit:
        cases = cases[: args.limit]
    context = load_full_qa_context(args.kg_dir, allow_synthetic=True)
    client = OpenAICompatibleToolClient(api_key=api_key, model=args.model, base_url="https://api.deepseek.com",
                                        temperature=0.0, timeout=180, max_retries=4)
    synthesizer = CarbonQLSynthesizer(client, GraphSchema.from_context(context.canonical))

    for run in args.runs:
        run_id = f"e6_v16_realloc_run{run}"
        out_dir = args.output_root / run_id
        if out_dir.exists():
            print("skip existing", out_dir, flush=True)
            continue
        started_at = datetime.now(timezone.utc).isoformat()
        t_run = time.time()
        compiled = {}
        failures = {}
        latency = {}
        program_rows = []
        # compile-stage checkpoint: a crash during policy execution must not discard the LLM calls
        checkpoint = args.output_root / f".{run_id}.compiled.jsonl"
        if checkpoint.exists():
            program_rows = [json.loads(l) for l in checkpoint.read_text(encoding="utf-8").splitlines() if l.strip()]
            print(f"run {run}: reusing {len(program_rows)} compiled programs from {checkpoint.name}", flush=True)
        done = {m["case_id"] for m in program_rows}
        for i, case in enumerate(cases, 1):
            if case.case_id in done:
                continue
            t0 = time.perf_counter()
            synthesis = synthesizer.synthesize(case.question, case.selected_component_ids, "V4")
            meta = {
                "case_id": case.case_id,
                "compiler_status": synthesis.compiler_status,
                "llm_call_count": synthesis.llm_call_count,
                "compile_latency_ms": round((time.perf_counter() - t0) * 1000, 3),
                "final_error": dict(synthesis.final_error) if synthesis.final_error else {},
            }
            if synthesis.final_program is None:
                meta["program"] = None
            else:
                meta["program"] = synthesis.final_program.to_dict()
                meta.update(program_slots(synthesis.final_program))
            program_rows.append(meta)
            with checkpoint.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
            if i % 25 == 0:
                print(f"run {run}: compiled {i}/{len(cases)} ({time.time() - t_run:.0f}s)", flush=True)
        for meta in program_rows:
            latency[meta["case_id"]] = meta["compile_latency_ms"]
            if meta["program"] is None:
                failures[meta["case_id"]] = {
                    "status": "not_executed", "coverage_status": "compiler_rejected", "perspective": "", "operation": "",
                    "summary": {}, "evidence_ids": [], "compiler_status": meta["compiler_status"],
                    "compiler_error": dict(meta["final_error"]),
                }
            else:
                compiled[meta["case_id"]] = CarbonQLProgram.from_dict(meta["program"])

        suites = {}
        all_rows = []
        for variant, policy in REAL_ABLATION_POLICIES.items():
            evaluated = []
            for index, case in enumerate(cases, 1):
                lat = latency[case.case_id]
                if case.case_id in failures:
                    observed = _scorable_observed(variant, failures[case.case_id], lat)
                else:
                    program = compiled[case.case_id]
                    t0 = time.perf_counter()
                    result = execute_canonical_query(context, program, case.selected_component_ids,
                                                     policy=QueryPolicy(allow_partial_known_subtotal=not policy.status_blocking))
                    observed = adapt_observed(question=case.question, program=program, result=result, context=context,
                                              selected_component_ids=case.selected_component_ids,
                                              elapsed_ms=lat + (time.perf_counter() - t0) * 1000)
                    observed["variant"] = variant
                    if not policy.provenance_constraint:
                        observed["evidence_ids"] = []
                        observed["supported_evidence_ids"] = []
                row = evaluate_benchmark_response(case, observed, variant)
                row["case_index"] = index
                row["run"] = run
                meta = next(m for m in program_rows if m["case_id"] == case.case_id)
                row["program"] = meta.get("program")
                for k, v in meta.items():
                    if k.startswith("program_"):
                        row[k] = v
                row["compiler_status"] = meta["compiler_status"]
                row["llm_call_count"] = meta["llm_call_count"]
                evaluated.append(row)
            metrics = dict(summarize_results(evaluated))
            metrics["transport_error_count"] = sum(r["observed"].get("status") == "transport_error" for r in evaluated)
            suites[variant] = metrics
            all_rows.extend(evaluated)
            print(f"run {run} {variant}: pass={metrics.get('pass_rate')} status={metrics.get('status_accuracy')} numeric={metrics.get('numeric_accuracy')}", flush=True)

        out_dir.mkdir(parents=True, exist_ok=False)
        (out_dir / "e6_real_ablation_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_rows), encoding="utf-8")
        (out_dir / "compiled_programs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in program_rows), encoding="utf-8")
        (out_dir / "e6_real_ablation_summary.json").write_text(json.dumps({
            "run_id": run_id, "run": run, "provider": "deepseek", "model": args.model, "temperature": 0.0,
            "max_tokens": 1800, "timeout_s": 180, "max_retries": 4, "compiler_variant": "V4",
            "benchmark_path": str(args.benchmark), "kg_dir": str(args.kg_dir), "allow_synthetic": True,
            "case_count": len(cases), "started_at_utc": started_at, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.time() - t_run, 1),
            "policies": {k: {"validation_gate": p.validation_gate, "status_blocking": p.status_blocking, "provenance_constraint": p.provenance_constraint} for k, p in REAL_ABLATION_POLICIES.items()},
            "variants": suites,
            "compile_failures": len(failures),
            "truth_leakage_policy": "expected fields are evaluator-only and never enter M3.1-M3.3",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        checkpoint.unlink(missing_ok=True)
        print("written", out_dir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
