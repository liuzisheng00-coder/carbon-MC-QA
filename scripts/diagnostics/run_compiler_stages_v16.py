# -*- coding: utf-8 -*-
"""D6: compiler-stage ablation (V1, V2, V3) on the frozen v16 benchmark, executed under the
full control policy, so that the type contract, the validator-driven repair and the semantic
gate can be compared with the V4 reference arm of D1 on the same 150 questions.

V1  JSON shape + allowed operations + three worked examples, no typed contract, no repair
V2  V1 + operator type and argument contract + graph schema + rule list
V3  V2 + one validator-driven repair call on a rejected program
V4  V3 + semantic coverage gate before acceptance (reference arm, D1 full_real)
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient  # noqa: E402
from dm2c_carbonql import CarbonQLProgram, GraphSchema  # noqa: E402
from dm2c_carbonql_synthesizer import CarbonQLSynthesizer  # noqa: E402
from dm2c_e5_e7_experiment_runner import evaluate_benchmark_response, load_benchmark_cases, summarize_results  # noqa: E402
from dm2c_full_qa_experiment_runner import QueryPolicy, execute_canonical_query, load_full_qa_context  # noqa: E402
from dm2c_qa_answer_adapter import adapt_observed  # noqa: E402
from dm2c_real_ablation_runner import REAL_ABLATION_POLICIES, _scorable_observed  # noqa: E402
from run_e6_v16 import DEFAULT_BENCHMARK, DEFAULT_KG, program_slots  # noqa: E402

DEFAULT_OUT = ROOT / "outputs/research_experiments/diagnostics_20260930/D6_compiler_stages_v16"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    ap.add_argument("--kg-dir", type=Path, default=DEFAULT_KG)
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--variants", nargs="+", default=["V1", "V2", "V3"])
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
    policy = REAL_ABLATION_POLICIES["full_real"]
    args.output_root.mkdir(parents=True, exist_ok=True)

    for run in args.runs:
        for variant in args.variants:
            run_id = f"stage_{variant}_run{run}"
            out_dir = args.output_root / run_id
            if out_dir.exists():
                print("skip existing", out_dir, flush=True)
                continue
            started_at = datetime.now(timezone.utc).isoformat()
            t_run = time.time()
            checkpoint = args.output_root / f".{run_id}.compiled.jsonl"
            program_rows = []
            if checkpoint.exists():
                program_rows = [json.loads(l) for l in checkpoint.read_text(encoding="utf-8").splitlines() if l.strip()]
                print(f"{run_id}: reusing {len(program_rows)} compiled programs", flush=True)
            done = {m["case_id"] for m in program_rows}
            for i, case in enumerate(cases, 1):
                if case.case_id in done:
                    continue
                t0 = time.perf_counter()
                synthesis = synthesizer.synthesize(case.question, case.selected_component_ids, variant)
                meta = {
                    "case_id": case.case_id,
                    "compiler_status": synthesis.compiler_status,
                    "llm_call_count": synthesis.llm_call_count,
                    "compile_latency_ms": round((time.perf_counter() - t0) * 1000, 3),
                    "first_error": dict(synthesis.first_error) if synthesis.first_error else {},
                    "final_error": dict(synthesis.final_error) if synthesis.final_error else {},
                    "first_program": synthesis.first_program.to_dict() if synthesis.first_program else None,
                }
                if synthesis.final_program is None:
                    meta["program"] = None
                else:
                    meta["program"] = synthesis.final_program.to_dict()
                    meta.update(program_slots(synthesis.final_program))
                program_rows.append(meta)
                with checkpoint.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
                if i % 50 == 0:
                    print(f"{run_id}: compiled {i}/{len(cases)} ({time.time() - t_run:.0f}s)", flush=True)

            evaluated = []
            for index, case in enumerate(cases, 1):
                meta = next(m for m in program_rows if m["case_id"] == case.case_id)
                lat = meta["compile_latency_ms"]
                if meta["program"] is None:
                    observed = _scorable_observed(run_id, {
                        "status": "not_executed", "coverage_status": "compiler_rejected", "perspective": "", "operation": "",
                        "summary": {}, "evidence_ids": [], "compiler_status": meta["compiler_status"],
                        "compiler_error": dict(meta["final_error"]),
                    }, lat)
                else:
                    program = CarbonQLProgram.from_dict(meta["program"])
                    t0 = time.perf_counter()
                    try:
                        result = execute_canonical_query(context, program, case.selected_component_ids,
                                                         policy=QueryPolicy(allow_partial_known_subtotal=not policy.status_blocking))
                        observed = adapt_observed(question=case.question, program=program, result=result, context=context,
                                                  selected_component_ids=case.selected_component_ids,
                                                  elapsed_ms=lat + (time.perf_counter() - t0) * 1000)
                    except Exception as exc:  # unvalidated programs may fail inside the executor
                        observed = {"status": "executor_error", "coverage_status": "executor_error", "perspective": "", "operation": "",
                                    "summary": {}, "evidence_ids": [], "executor_error": f"{type(exc).__name__}: {exc}"[:400],
                                    "latency_ms": lat}
                    observed["variant"] = run_id
                row = evaluate_benchmark_response(case, observed, f"stage_{variant}")
                row["case_index"] = index
                row["run"] = run
                row["program"] = meta.get("program")
                row["first_program"] = meta.get("first_program")
                for k, v in meta.items():
                    if k.startswith("program_"):
                        row[k] = v
                row["compiler_status"] = meta["compiler_status"]
                row["llm_call_count"] = meta["llm_call_count"]
                evaluated.append(row)
            metrics = dict(summarize_results(evaluated))
            metrics["compile_failures"] = sum(m["program"] is None for m in program_rows)
            metrics["executor_errors"] = sum(r["observed"].get("status") == "executor_error" for r in evaluated)
            print(f"{run_id}: pass={metrics.get('pass_rate')} status={metrics.get('status_accuracy')} numeric={metrics.get('numeric_accuracy')} "
                  f"compile_failures={metrics['compile_failures']} executor_errors={metrics['executor_errors']}", flush=True)
            out_dir.mkdir(parents=True, exist_ok=False)
            (out_dir / "stage_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in evaluated), encoding="utf-8")
            (out_dir / "compiled_programs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in program_rows), encoding="utf-8")
            (out_dir / "stage_summary.json").write_text(json.dumps({
                "run_id": run_id, "run": run, "compiler_variant": variant, "provider": "deepseek", "model": args.model, "temperature": 0.0,
                "max_tokens": 1800, "timeout_s": 180, "max_retries": 4, "policy": "full_real",
                "benchmark_path": str(args.benchmark), "kg_dir": str(args.kg_dir), "allow_synthetic": True,
                "case_count": len(cases), "started_at_utc": started_at, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(time.time() - t_run, 1), "metrics": metrics,
                "truth_leakage_policy": "expected fields are evaluator-only and never enter M3.1-M3.3",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            checkpoint.unlink(missing_ok=True)
            print("written", out_dir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
