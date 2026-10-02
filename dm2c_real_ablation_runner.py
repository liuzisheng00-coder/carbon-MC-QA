"""Controlled M3 ablations that preserve canonical resolution and query scope."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence

from dm2c_carbonql import CarbonQLProgram
from dm2c_full_qa_experiment_runner import QueryPolicy, execute_canonical_query
from dm2c_m3_context import M3ExecutionContext


@dataclass(frozen=True, slots=True)
class AblationPolicy:
    validation_gate: bool = True
    status_blocking: bool = True
    provenance_constraint: bool = True


REAL_ABLATION_POLICIES: Mapping[str, AblationPolicy] = {
    "full_real": AblationPolicy(),
    "no_validation_gate_real": AblationPolicy(validation_gate=False),
    "no_status_blocking_real": AblationPolicy(status_blocking=False),
    "no_provenance_constraint_real": AblationPolicy(provenance_constraint=False),
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_FROZEN_V10_BENCHMARK = Path(
    "outputs/research_experiments/e4_benchmark/"
    "frozen_20260730_133530_v10_layerdedup_human_pass/"
    "e4_balanced_benchmark_150_human_reviewed_v10_layerdedup.jsonl"
)
DEFAULT_ABLATION_OUTPUT_ROOT = Path(
    "outputs/research_experiments/e6_real_ablations_layerdedup"
)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _result_dict(result, policy: AblationPolicy) -> dict[str, Any]:
    coverage_status = result.coverage_status
    if not policy.validation_gate and coverage_status in {
        "complete",
        "searched_complete",
        "relevant_rejection",
    }:
        coverage_status = "not_evaluated"
    return {
        "status": result.status,
        "coverage_status": coverage_status,
        "validation_gate_applied": policy.validation_gate,
        "perspective": result.perspective,
        "operation": result.operation,
        "rows": [dict(row) for row in result.rows],
        "summary": dict(result.summary),
        "emission_ids": list(result.emission_ids),
        "projection_keys": [list(key) for key in result.projection_keys],
        "evidence_ids": list(result.evidence_ids) if policy.provenance_constraint else [],
    }


def run_real_ablation_suite(
    cases: Sequence[tuple[str, CarbonQLProgram, Sequence[str]]],
    context: M3ExecutionContext,
    *,
    variants: Sequence[str],
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name in variants:
        if name not in REAL_ABLATION_POLICIES:
            raise ValueError(f"unknown ablation variant {name!r}")
        policy = REAL_ABLATION_POLICIES[name]
        rows = []
        for case_id, program, selected in cases:
            result = execute_canonical_query(
                context,
                program,
                selected,
                policy=QueryPolicy(
                    allow_partial_known_subtotal=not policy.status_blocking
                ),
            )
            rows.append({"case_id": case_id, **_result_dict(result, policy)})
        report[name] = {"case_count": len(rows), "cases": rows}
    return report


def publish_ablation_run(
    report: Mapping[str, Any], *, output_root: Path, run_id: str
) -> Path:
    if not _SAFE_ID.fullmatch(run_id) or "v2" not in run_id.casefold():
        raise ValueError("run_id must be a safe v2 identifier")
    target = output_root / run_id
    staging = output_root / f".{run_id}.staging"
    if target.exists() or staging.exists():
        raise FileExistsError(target if target.exists() else staging)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        (staging / "ablation_report.json").write_bytes(payload)
        staging.rename(target)
    except Exception:
        if staging.exists():
            for item in staging.iterdir():
                if item.is_file():
                    item.unlink()
            staging.rmdir()
        raise
    return target


def build_cli_parser() -> argparse.ArgumentParser:
    policy_names = ", ".join(REAL_ABLATION_POLICIES)
    parser = argparse.ArgumentParser(
        description=(
            "Run the four current control policies on the frozen "
            "v10_layerdedup benchmark with deepseek/deepseek-chat "
            "(temperature=0). Policies: "
            f"{policy_names}. The July-12 legacy variants are out of scope "
            "because graph_only_real, heuristic_m31_real, and no_m33_llm_real "
            "are not provided by REAL_ABLATION_POLICIES."
        ),
        formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(
            prog, width=200
        ),
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=DEFAULT_FROZEN_V10_BENCHMARK,
        help="Frozen human-reviewed benchmark JSONL.",
    )
    parser.add_argument(
        "--kg-dir",
        type=Path,
        required=True,
        help="Explicit canonical-v2 release directory (use the layerdedup release).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run every benchmark case; otherwise run the first 20 cases.",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Opt in to loading a release that declares synthetic records.",
    )
    parser.add_argument(
        "--provider", choices=("deepseek",), default="deepseek", help="LLM provider."
    )
    parser.add_argument("--model", default="deepseek-chat", help="Chat model.")
    parser.add_argument(
        "--api-key-env", default="DEEPSEEK_API_KEY", help="API key environment variable."
    )
    parser.add_argument("--base-url", default=DEEPSEEK_BASE_URL, help="API base URL.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_ABLATION_OUTPUT_ROOT,
        help="Root directory for immutable run outputs.",
    )
    parser.add_argument("--run-id")
    return parser


def _provider_client(args: argparse.Namespace):
    from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{args.api_key_env} is required to run provider-backed ablations"
        )
    return OpenAICompatibleToolClient(
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        temperature=0.0,
    )


def _supported_numbers(value: Any) -> list[float]:
    numbers: list[float] = []
    if isinstance(value, Mapping):
        for item in value.values():
            numbers.extend(_supported_numbers(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers.extend(_supported_numbers(item))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numbers.append(float(value))
    return numbers


def _scorable_observed(
    variant: str, result: Mapping[str, Any], latency_ms: float
) -> dict[str, Any]:
    summary = dict(result.get("summary") or {})
    evidence_ids = list(result.get("evidence_ids") or [])
    return {
        **dict(result),
        "variant": variant,
        "supported_numeric_values": _supported_numbers(summary),
        "supported_text_tokens": [
            str(result.get("status") or ""),
            str(result.get("coverage_status") or ""),
            str(result.get("perspective") or ""),
            str(result.get("operation") or ""),
            *evidence_ids,
        ],
        "supported_evidence_ids": evidence_ids,
        "answer": json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "latency_ms": round(latency_ms, 3),
    }


def _write_ablation_cli_run(
    args: argparse.Namespace,
    suites: Mapping[str, Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    benchmark_case_count: int,
) -> Path:
    run_id = args.run_id or (
        "e6_real_ablations_layerdedup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    case_path = output_dir / "e6_real_ablation_cases.jsonl"
    case_path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, allow_nan=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    summary = {
        "run_id": run_id,
        "mode": "full" if args.full else "pilot20",
        "provider": args.provider,
        "model": args.model,
        "temperature": 0.0,
        "benchmark_path": str(args.benchmark),
        "kg_dir": str(args.kg_dir),
        "allow_synthetic": bool(args.allow_synthetic),
        "case_count": benchmark_case_count,
        "policies": {
            name: {
                "validation_gate": policy.validation_gate,
                "status_blocking": policy.status_blocking,
                "provenance_constraint": policy.provenance_constraint,
            }
            for name, policy in REAL_ABLATION_POLICIES.items()
        },
        "variant_scope": {
            "included": list(REAL_ABLATION_POLICIES),
            "out_of_scope_legacy": [
                "graph_only_real",
                "heuristic_m31_real",
                "no_m33_llm_real",
            ],
            "reason": "the current code does not provide these legacy policies",
        },
        "variants": {name: dict(payload["metrics"]) for name, payload in suites.items()},
        "truth_leakage_policy": (
            "expected fields are evaluator-only and never enter M3.1-M3.3"
        ),
        "case_jsonl": str(case_path),
    }
    (output_dir / "e6_real_ablation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return output_dir


def main(argv: Sequence[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    from dm2c_carbonql import GraphSchema
    from dm2c_carbonql_synthesizer import CarbonQLSynthesizer
    from dm2c_e5_e7_experiment_runner import (
        evaluate_benchmark_response,
        load_benchmark_cases,
        summarize_results,
    )
    from dm2c_full_qa_experiment_runner import load_full_qa_context

    benchmark_cases = load_benchmark_cases(args.benchmark)
    cases = benchmark_cases if args.full else benchmark_cases[:20]
    context = load_full_qa_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    synthesizer = CarbonQLSynthesizer(
        _provider_client(args), GraphSchema.from_context(context.canonical)
    )
    compiled: list[tuple[str, CarbonQLProgram, Sequence[str]]] = []
    compile_failures: dict[str, dict[str, Any]] = {}
    compile_latency: dict[str, float] = {}
    for case in cases:
        started = time.perf_counter()
        synthesis = synthesizer.synthesize(
            case.question, case.selected_component_ids, "V4"
        )
        compile_latency[case.case_id] = (time.perf_counter() - started) * 1000
        if synthesis.final_program is None:
            compile_failures[case.case_id] = {
                "status": "not_executed",
                "coverage_status": "compiler_rejected",
                "perspective": "",
                "operation": "",
                "summary": {},
                "evidence_ids": [],
                "compiler_status": synthesis.compiler_status,
                "compiler_error": dict(synthesis.final_error),
            }
        else:
            compiled.append(
                (
                    case.case_id,
                    synthesis.final_program,
                    case.selected_component_ids,
                )
            )
    programs_by_id = {
        case_id: program for case_id, program, _selected in compiled
    }
    suites: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    from dm2c_qa_answer_adapter import adapt_observed

    for variant in REAL_ABLATION_POLICIES:
        policy = REAL_ABLATION_POLICIES[variant]
        evaluated = []
        for index, case in enumerate(cases, start=1):
            latency_ms = compile_latency[case.case_id]
            if case.case_id in compile_failures:
                observed = _scorable_observed(
                    variant, compile_failures[case.case_id], latency_ms
                )
            else:
                program = programs_by_id[case.case_id]
                started = time.perf_counter()
                result = execute_canonical_query(
                    context,
                    program,
                    case.selected_component_ids,
                    policy=QueryPolicy(
                        allow_partial_known_subtotal=not policy.status_blocking
                    ),
                )
                observed = adapt_observed(
                    question=case.question,
                    program=program,
                    result=result,
                    context=context,
                    selected_component_ids=case.selected_component_ids,
                    elapsed_ms=latency_ms
                    + (time.perf_counter() - started) * 1000,
                )
                observed["variant"] = variant
            row = evaluate_benchmark_response(case, observed, variant)
            row["case_index"] = index
            evaluated.append(row)
        suites[variant] = {
            "metrics": {
                **summarize_results(evaluated),
                "transport_error_count": sum(
                    row["observed"].get("status") == "transport_error"
                    for row in evaluated
                ),
            },
            "cases": evaluated,
        }
        all_rows.extend(evaluated)
    output_dir = _write_ablation_cli_run(
        args, suites, all_rows, len(cases)
    )
    print(output_dir)
    return 0


__all__ = [
    "AblationPolicy",
    "DEFAULT_FROZEN_V10_BENCHMARK",
    "REAL_ABLATION_POLICIES",
    "build_cli_parser",
    "main",
    "publish_ablation_run",
    "run_real_ablation_suite",
]


if __name__ == "__main__":
    raise SystemExit(main())
