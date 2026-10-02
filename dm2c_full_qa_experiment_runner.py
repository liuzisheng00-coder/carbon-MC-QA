"""Canonical-v2 query execution and availability-aware M3 status semantics."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    component_type_ids_for_component,
    components_for_ifc_class,
    dimension_ids,
    lookup_dimension,
)
from dm2c_carbonql import CarbonQLProgram, derive_projection_perspective
from dm2c_carbonql_executor import CarbonQLExecutor
from dm2c_m3_context import (
    CanonicalQueryResult,
    M3ExecutionContext,
    immutable_mapping,
    load_m3_execution_context,
)


DEFAULT_FROZEN_V10_BENCHMARK = Path(
    "outputs/research_experiments/e4_benchmark/"
    "frozen_20260730_133530_v10_layerdedup_human_pass/"
    "e4_balanced_benchmark_150_human_reviewed_v10_layerdedup.jsonl"
)
DEFAULT_FULL_OUTPUT_ROOT = Path(
    "outputs/research_experiments/e5_full_real_layerdedup"
)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True, slots=True)
class QueryPolicy:
    allow_partial_known_subtotal: bool = False


@dataclass(frozen=True, slots=True)
class _ResolvedSelectorScope:
    entity_type: str | None
    ids: tuple[str, ...]
    unresolved: bool = False
    ambiguous: bool = False


def load_full_qa_context(
    release_dir: Path | str, *, allow_synthetic: bool = False
) -> M3ExecutionContext:
    return load_m3_execution_context(release_dir, allow_synthetic=allow_synthetic)


def status_for_availability(
    *,
    match_count: int,
    searched_context_complete: bool,
    relevant_rejection_count: int,
    accepted_zero: bool,
) -> str:
    if relevant_rejection_count or not searched_context_complete:
        return "incomplete_path"
    if accepted_zero:
        return "executable"
    if match_count == 0:
        return "empty_result"
    return "executable"


def _operation(program: CarbonQLProgram) -> str:
    operations = {step.op for step in program.steps}
    if "Rank" in operations:
        return "rank"
    if "Compare" in operations:
        return "compare"
    if "Aggregate" in operations:
        return "aggregate"
    if "Trace" in operations:
        return "trace"
    return "aggregate"


def _may_disclose_scope_rejections(
    program: CarbonQLProgram, selector_scope: _ResolvedSelectorScope
) -> bool:
    class_scope_filters = {
        "ifc_class",
        "ifc_class_name",
        "component_type",
        "component_type_name",
    }
    return (
        program.steps[0].op == "SelectProject"
        and not selector_scope.ids
        and all(
            step.op != "Filter"
            or step.args.get("field") in class_scope_filters
            for step in program.steps
        )
        and any(
            step.op == "CarbonAtoms" and step.args.get("known_total") is True
            for step in program.steps
        )
        and _operation(program) in {"aggregate", "rank", "compare", "trace"}
    )


def _rejection_reason_counts(records: Sequence[Any]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        reason = str(record.reason_code or "unspecified")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _props(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("props", {})
    return value if isinstance(value, Mapping) else {}


def _canonical_component_ids(
    context: M3ExecutionContext,
    values: Sequence[Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    available = set(dimension_ids(context.canonical, "component"))
    aliases = {
        str(_props(lookup_dimension(context.canonical, "component", entity_id)).get("globalId")): entity_id
        for entity_id in available
        if _props(lookup_dimension(context.canonical, "component", entity_id)).get("globalId")
    }
    resolved: list[str] = []
    missing: list[str] = []
    for value in values:
        token = str(value).strip()
        entity_id = token if token in available else aliases.get(token)
        if entity_id is not None and entity_id not in resolved:
            resolved.append(entity_id)
        elif entity_id is None and token not in missing:
            missing.append(token)
    return tuple(resolved), tuple(missing)


def _canonical_product_entity_ids(
    context: M3ExecutionContext,
    values: Sequence[Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    component_ids, missing = _canonical_component_ids(context, values)
    module_ids, _ = _canonical_module_ids(context, values)
    resolved = tuple(
        dict.fromkeys((*component_ids, *module_ids))
    )
    remaining_missing = tuple(value for value in missing if value not in module_ids)
    return resolved, remaining_missing


def _canonical_module_ids(
    context: M3ExecutionContext,
    values: Sequence[Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    available = set(dimension_ids(context.canonical, "module"))
    resolved: list[str] = []
    missing: list[str] = []
    for value in values:
        token = str(value).strip()
        if token in available and token not in resolved:
            resolved.append(token)
        elif token not in available and token not in missing:
            missing.append(token)
    return tuple(resolved), tuple(missing)


def _class_scope_component_ids(
    context: M3ExecutionContext, program: CarbonQLProgram
) -> tuple[str, ...] | None:
    selected: set[str] | None = None
    component_ids = dimension_ids(context.canonical, "component")
    for step in program.steps:
        if step.op != "Filter":
            continue
        field = str(step.args.get("field") or "")
        if field not in {
            "ifc_class",
            "ifc_class_name",
            "component_type",
            "component_type_name",
        }:
            continue
        raw_values = (
            (step.args.get("equals"),)
            if "equals" in step.args
            else tuple(step.args.get("in") or ())
        )
        values = {str(value).strip() for value in raw_values if str(value).strip()}
        matched: set[str] = set()
        if field in {"ifc_class", "ifc_class_name"}:
            for ifc_class in values.intersection(
                dimension_ids(context.canonical, "ifc_class")
            ):
                matched.update(components_for_ifc_class(context.canonical, ifc_class))
        else:
            type_ids = set(dimension_ids(context.canonical, "component_type"))
            if field == "component_type_name":
                type_ids = {
                    entity_id
                    for entity_id in type_ids
                    if str(
                        _props(
                            lookup_dimension(
                                context.canonical, "component_type", entity_id
                            )
                        ).get("name", "")
                    )
                    in values
                }
            else:
                type_ids.intersection_update(values)
            matched = {
                component_id
                for component_id in component_ids
                if type_ids.intersection(
                    component_type_ids_for_component(
                        context.canonical, component_id
                    )
                )
            }
        selected = matched if selected is None else selected.intersection(matched)
    if selected is None:
        return None
    return tuple(component_id for component_id in component_ids if component_id in selected)


def _resolved_selector_scope(
    context: M3ExecutionContext,
    program: CarbonQLProgram,
    selected_component_ids: Sequence[str] = (),
) -> _ResolvedSelectorScope:
    selector = program.steps[0]
    if selector.op == "SelectProject":
        return _ResolvedSelectorScope(None, ())
    if selector.op == "SelectClicked":
        program_values = tuple(selector.args.get("ids") or ())
        runtime_values = tuple(selected_component_ids)
        program_ids, program_missing = _canonical_product_entity_ids(
            context, program_values
        )
        runtime_ids, runtime_missing = _canonical_product_entity_ids(
            context, runtime_values
        )
        conflict = bool(
            program_values
            and runtime_values
            and set(program_ids) != set(runtime_ids)
        )
        resolved = tuple(dict.fromkeys((*program_ids, *runtime_ids)))
        missing = tuple(dict.fromkeys((*program_missing, *runtime_missing)))
        return _ResolvedSelectorScope(
            "component",
            resolved,
            unresolved=bool(missing or conflict or not resolved),
        )
    if selector.op != "ResolveEntities":
        return _ResolvedSelectorScope(None, (), unresolved=True)
    args = selector.args
    entity_type = str(args.get("entity_type") or "component")
    if entity_type not in {"component", "module", "material", "process"}:
        return _ResolvedSelectorScope(None, (), unresolved=True)
    raw_ids = tuple(args.get("ids") or ())
    if raw_ids and entity_type == "component":
        resolved, missing = _canonical_component_ids(context, raw_ids)
    elif raw_ids and entity_type == "module":
        resolved, missing = _canonical_module_ids(context, raw_ids)
    elif raw_ids:
        available = set(dimension_ids(context.canonical, entity_type))
        resolved = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in raw_ids
                if str(value).strip() in available
            )
        )
        missing = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in raw_ids
                if str(value).strip() not in available
            )
        )
    else:
        property_name = str(args.get("property") or "name")
        value = str(args.get("value") or "")
        resolved = tuple(
            entity_id
            for entity_id in dimension_ids(context.canonical, entity_type)
            if str(
                _props(
                    lookup_dimension(context.canonical, entity_type, entity_id)
                ).get(property_name, "")
            )
            == value
        )
        missing = ()
    singleton = str(args.get("cardinality") or "singleton") == "singleton"
    ambiguous = bool(singleton and not missing and len(resolved) > 1)
    return _ResolvedSelectorScope(
        entity_type,
        resolved,
        unresolved=bool(missing or not resolved or (singleton and len(resolved) != 1)),
        ambiguous=ambiguous,
    )


def _ambiguous_selector(
    program: CarbonQLProgram, selector_scope: _ResolvedSelectorScope
) -> bool:
    return bool(
        program.steps[0].op == "ResolveEntities" and selector_scope.ambiguous
    )


def _requested_source_kinds(program: CarbonQLProgram) -> set[str]:
    requested: set[str] = set()
    for step in program.steps:
        if step.op != "CarbonAtoms":
            continue
        raw = step.args.get("source")
        values = (raw,) if isinstance(raw, str) else tuple(raw or ())
        if "all" in values:
            requested.update(("material", "energy"))
        else:
            requested.update("energy" if value == "process" else str(value) for value in values)
    return requested


def _availability_complete(context: M3ExecutionContext, program: CarbonQLProgram) -> tuple[bool, str]:
    statuses = [context.availability[kind] for kind in sorted(_requested_source_kinds(program))]
    if not statuses:
        return False, "not_available"
    if "not_available" in statuses:
        return False, "not_available"
    if "incomplete" in statuses:
        return False, "incomplete"
    return True, "complete"


def _typed_rejection_filters(
    program: CarbonQLProgram, selector_scope: _ResolvedSelectorScope
) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {
        "material_ids": [],
        "carrier_ids": [],
        "process_ids": [],
        "recorded_scopes": [],
        "requested_scopes": [],
    }
    field_map = {
        "material": "material_ids",
        "carrier": "carrier_ids",
        "process": "process_ids",
        "stage": "process_ids",
        "recorded_scope": "recorded_scopes",
        "requested_scope": "requested_scopes",
        "scope": "requested_scopes",
    }
    for step in program.steps:
        if step.op != "Filter":
            continue
        target = field_map.get(str(step.args.get("field") or ""))
        if target is None:
            continue
        raw = (
            (step.args.get("equals"),)
            if "equals" in step.args
            else tuple(step.args.get("in") or ())
        )
        for value in raw:
            text = str(value or "")
            if text and text not in values[target]:
                values[target].append(text)
    if not selector_scope.unresolved:
        target = {
            "material": "material_ids",
            "process": "process_ids",
        }.get(selector_scope.entity_type)
        if target:
            for value in selector_scope.ids:
                text = str(value)
                if text and text not in values[target]:
                    values[target].append(text)
    return {key: tuple(items) for key, items in values.items()}


def _evidence_for_projection_keys(
    context: M3ExecutionContext, keys: tuple[tuple[str, ...], ...]
) -> tuple[str, ...]:
    wanted = set(keys)
    ordered: list[str] = []
    for projection in (*context.projections, *context.source_projections):
        if projection.projection_key not in wanted:
            continue
        for value in projection.evidence_ids:
            if value not in ordered:
                ordered.append(value)
    return tuple(ordered)


def execute_canonical_query(
    context: M3ExecutionContext,
    program: CarbonQLProgram,
    selected_component_ids: Sequence[str] = (),
    *,
    policy: QueryPolicy | None = None,
) -> CanonicalQueryResult:
    if not isinstance(context, M3ExecutionContext):
        raise TypeError("context must be M3ExecutionContext")
    policy = policy or QueryPolicy()
    perspective = derive_projection_perspective(program)
    selector_scope = _resolved_selector_scope(
        context, program, selected_component_ids
    )
    raw = CarbonQLExecutor.from_context(context.canonical).execute(
        program, selected_component_ids
    )
    rows = tuple(immutable_mapping(dict(row)) for row in raw.rows)
    summary = immutable_mapping(dict(raw.summary))
    projection_keys = tuple(tuple(value) for value in raw.projection_keys)
    emission_ids = tuple(raw.emission_ids)
    evidence = _evidence_for_projection_keys(context, projection_keys)
    relevant: tuple[Any, ...] = ()
    if raw.status == "unresolved_target":
        status = (
            "clarification_required"
            if _ambiguous_selector(program, selector_scope)
            else "unresolved_target"
        )
        coverage_status = "ambiguous_target" if status == "clarification_required" else "unresolved_target"
    elif raw.status == "partial":
        has_target_hole = any(
            hole.dimension == "target" for hole in program.holes
        )
        status = (
            "clarification_required" if has_target_hole else "incomplete_path"
        )
        coverage_status = (
            "ambiguous_target" if has_target_hole else "program_incomplete"
        )
    else:
        components = (
            selector_scope.ids
            if selector_scope.entity_type in {"component", "module"}
            and not selector_scope.unresolved
            else ()
        )
        class_components = _class_scope_component_ids(context, program)
        if class_components == ():
            relevant = ()
        else:
            relevant = context.validation_coverage.relevant_rejections(
                perspective=perspective,
                component_ids=tuple(
                    dict.fromkeys((*components, *(class_components or ())))
                ),
                **_typed_rejection_filters(program, selector_scope),
            )
        valid_zero = bool(emission_ids) and all(
            fact.is_valid_zero
            for fact in context.canonical.emissions
            if fact.emission_id in emission_ids
        )
        searched_complete, availability_status = _availability_complete(context, program)
        status = status_for_availability(
            match_count=len(projection_keys),
            searched_context_complete=searched_complete,
            relevant_rejection_count=len(relevant),
            accepted_zero=valid_zero,
        )
        if (
            searched_complete
            and relevant
            and _may_disclose_scope_rejections(program, selector_scope)
        ):
            status = "executable"
            coverage_status = "relevant_rejection_disclosed"
            summary = immutable_mapping(
                {
                    **dict(summary),
                    "excluded_record_count": len(relevant),
                    "excluded_rejection_reasons": _rejection_reason_counts(relevant),
                }
            )
        else:
            coverage_status = (
                availability_status
                if not searched_complete
                else "relevant_rejection"
                if relevant
                else "complete"
                if status == "executable"
                else "searched_complete"
            )
        if (
            status == "incomplete_path"
            and policy.allow_partial_known_subtotal
            and projection_keys
        ):
            status = "partial_known_subtotal"
    result_metadata: dict[str, Any] = {}
    if (
        program.steps[0].op == "SelectProject"
        and perspective == "product"
        and any(
            step.op == "CarbonAtoms" and step.args.get("known_total") is True
            for step in program.steps
        )
    ):
        result_metadata["available_component_count"] = len(
            dimension_ids(context.canonical, "component")
        )
    if status in {"unresolved_target", "clarification_required"}:
        selector = program.steps[0]
        if status == "clarification_required":
            result_metadata["ambiguous_target"] = str(
                selector.args.get("value") or ""
            )
        else:
            raw_ids = tuple(selector.args.get("ids") or ())
            result_metadata["target_id"] = str(
                raw_ids[0] if raw_ids else selector.args.get("value") or ""
            )
    if status in {"incomplete_path", "partial_known_subtotal"}:
        reasons = _rejection_reason_counts(relevant)
        result_metadata["blocked_status"] = (
            next(iter(reasons))
            if len(reasons) == 1
            else coverage_status
        )
    if status == "empty_result":
        rows = ()
        result_metadata["row_count"] = 0
    if result_metadata:
        summary = immutable_mapping({**dict(summary), **result_metadata})
    return CanonicalQueryResult(
        status=status,
        coverage_status=coverage_status,
        perspective=perspective,
        operation=_operation(program),
        rows=rows,
        summary=summary,
        emission_ids=emission_ids,
        projection_keys=projection_keys,
        evidence_ids=evidence,
    )


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full canonical-v2 QA system on the frozen v10_layerdedup "
            "benchmark. Provider configuration is deepseek/deepseek-chat with "
            "temperature=0."
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
        "--provider",
        choices=("deepseek", "gemini", "dashscope", "openai_compatible"),
        default="deepseek",
        help="LLM provider label (client is OpenAI-compatible; behaviour is driven by --model/--base-url/--api-key-env).",
    )
    parser.add_argument("--model", default="deepseek-chat", help="Chat model.")
    parser.add_argument(
        "--api-key-env", default="DEEPSEEK_API_KEY", help="API key environment variable."
    )
    parser.add_argument("--base-url", default=DEEPSEEK_BASE_URL, help="API base URL.")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max retries on 429/5xx/network errors per LLM call.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds per LLM call.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_FULL_OUTPUT_ROOT,
        help="Root directory for immutable run outputs.",
    )
    parser.add_argument("--run-id")
    return parser


def _deepseek_client(args: argparse.Namespace):
    from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{args.api_key_env} is required to run provider-backed QA experiments"
        )
    return OpenAICompatibleToolClient(
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        temperature=0.0,
        timeout=getattr(args, "timeout", 60),
        max_retries=getattr(args, "max_retries", 2),
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


def _observed_from_result(
    result: CanonicalQueryResult,
    elapsed_ms: float,
    *,
    question: str = "",
    program: CarbonQLProgram | None = None,
    context: M3ExecutionContext | None = None,
    selected_component_ids: Sequence[str] = (),
    compiler_status: str | None = None,
) -> dict[str, Any]:
    if program is not None:
        from dm2c_qa_answer_adapter import adapt_observed

        return adapt_observed(
            question=question,
            program=program,
            result=result,
            context=context,
            selected_component_ids=selected_component_ids,
            elapsed_ms=elapsed_ms,
            compiler_status=compiler_status,
        )
    summary = dict(result.summary)
    evidence_ids = list(result.evidence_ids)
    observed = {
        "variant": "full_real",
        "status": result.status,
        "coverage_status": result.coverage_status,
        "perspective": result.perspective,
        "operation": result.operation,
        "summary": summary,
        "evidence_ids": evidence_ids,
        "supported_numeric_values": _supported_numbers(summary),
        "supported_text_tokens": [
            result.status,
            result.coverage_status,
            result.perspective,
            result.operation,
            *evidence_ids,
        ],
        "supported_evidence_ids": evidence_ids,
        "answer": json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "latency_ms": round(elapsed_ms, 3),
    }
    if compiler_status is not None:
        observed["compiler_status"] = compiler_status
    return observed


def _write_cli_run(
    *,
    args: argparse.Namespace,
    case_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> Path:
    run_id = args.run_id or (
        "e5_full_real_layerdedup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    case_path = output_dir / "e5_full_real_cases.jsonl"
    case_path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, allow_nan=False) + "\n"
            for row in case_rows
        ),
        encoding="utf-8",
    )
    summary = {
        "run_id": run_id,
        "provider": args.provider,
        "model": args.model,
        "temperature": 0.0,
        "benchmark_path": str(args.benchmark),
        "kg_dir": str(args.kg_dir),
        "allow_synthetic": bool(args.allow_synthetic),
        "case_count": len(case_rows),
        "variants": {"full_real": dict(metrics)},
        "truth_leakage_policy": (
            "expected fields are evaluator-only and never enter M3.1-M3.3"
        ),
        "case_jsonl": str(case_path),
    }
    (output_dir / "e5_full_real_summary.json").write_text(
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

    benchmark_cases = load_benchmark_cases(args.benchmark)
    cases = benchmark_cases if args.full else benchmark_cases[:20]
    context = load_full_qa_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    synthesizer = CarbonQLSynthesizer(
        _deepseek_client(args), GraphSchema.from_context(context.canonical)
    )
    evaluated: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        synthesis = synthesizer.synthesize(
            case.question, case.selected_component_ids, "V4"
        )
        if synthesis.final_program is None:
            observed = {
                "variant": "full_real",
                "status": "not_executed",
                "coverage_status": "compiler_rejected",
                "perspective": "",
                "operation": "",
                "summary": {},
                "evidence_ids": [],
                "supported_numeric_values": [],
                "supported_text_tokens": [synthesis.compiler_status],
                "supported_evidence_ids": [],
                "answer": "",
                "compiler_status": synthesis.compiler_status,
                "compiler_error": dict(synthesis.final_error),
                "latency_ms": round(
                    (time.perf_counter() - started) * 1000, 3
                ),
            }
        else:
            result = execute_canonical_query(
                context,
                synthesis.final_program,
                case.selected_component_ids,
            )
            observed = _observed_from_result(
                result,
                (time.perf_counter() - started) * 1000,
                question=case.question,
                program=synthesis.final_program,
                context=context,
                selected_component_ids=case.selected_component_ids,
                compiler_status=synthesis.compiler_status,
            )
        row = evaluate_benchmark_response(case, observed, "full_real")
        row["case_index"] = index
        evaluated.append(row)
    output_dir = _write_cli_run(
        args=args,
        case_rows=evaluated,
        metrics=summarize_results(evaluated),
    )
    print(output_dir)
    return 0


__all__ = [
    "DEFAULT_FROZEN_V10_BENCHMARK",
    "QueryPolicy",
    "build_cli_parser",
    "execute_canonical_query",
    "load_full_qa_context",
    "main",
    "status_for_availability",
]


if __name__ == "__main__":
    raise SystemExit(main())
