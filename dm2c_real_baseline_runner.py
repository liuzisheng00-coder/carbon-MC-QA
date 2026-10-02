"""Canonical-v2 static-client baselines over an immutable M3 context."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Sequence

from dm2c_canonical_v2_reader import lookup_dimension
from dm2c_carbonql_benchmark import CarbonQLCase
from dm2c_m3_context import M3ExecutionContext, load_m3_execution_context


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_FROZEN_V10_BENCHMARK = Path(
    "outputs/research_experiments/e4_benchmark/"
    "frozen_20260730_133530_v10_layerdedup_human_pass/"
    "e4_balanced_benchmark_150_human_reviewed_v10_layerdedup.jsonl"
)
DEFAULT_BASELINE_OUTPUT_ROOT = Path(
    "outputs/research_experiments/e5_real_baselines_layerdedup"
)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
REAL_BASELINE_VARIANTS = (
    "llm_only_real",
    "unconstrained_kg_llm_real",
    "llm_structured_json_real",
)

_STRUCTURED_JSON_INSTRUCTION = """
Reply with ONLY one JSON object (no markdown fences, no prose outside JSON).
Required keys:
  status: one of executable, incomplete_path, empty_result, unresolved_target, clarification_required
  perspective: one of product, material, process
  operation: one of value, rank, compare, explain, trace
  summary: object of numeric fields answering the question (use kgCO2e-style keys when totals are known)
Optional keys: answer (short prose), evidence_ids (string array)
If coverage is unavailable, set the matching status and keep summary empty or partial.
""".strip()


def _reject_nonfinite_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant is not permitted: {value}")


class StaticLLMClient:
    def __init__(self, response: Mapping[str, Any]):
        self.response = dict(response)
        self.call_count = 0

    def complete(
        self, messages: Sequence[Mapping[str, str]], **_: Any
    ) -> dict[str, Any]:
        self.call_count += 1
        return dict(self.response)


def load_baseline_context(
    release_dir: Path | str, *, allow_synthetic: bool = False
) -> M3ExecutionContext:
    return load_m3_execution_context(
        release_dir, allow_synthetic=allow_synthetic
    )


def _name_of(context: M3ExecutionContext, dimension: str, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    try:
        node = lookup_dimension(context.canonical, dimension, entity_id)
    except Exception:
        return entity_id
    props = (node or {}).get("props") or {}
    return (
        props.get("name")
        or props.get("typeName")
        or props.get("activityName")
        or entity_id
    )


def _component_by_emission(context: M3ExecutionContext) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for contribution in context.canonical.product_contributions:
        mapping.setdefault(contribution.emission_id, contribution.component_id)
    return mapping


def _emission_factor_rows(context: M3ExecutionContext) -> list[dict[str, Any]]:
    factors: dict[str, dict[str, Any]] = {}
    for fact in context.canonical.emissions:
        applies_to = (
            _name_of(context, "material", fact.material_id)
            or _name_of(context, "carrier", fact.carrier_id)
        )
        if applies_to is None:
            continue
        factors[str(applies_to)] = {
            "applies_to": applies_to,
            "factor_value": fact.factor_value,
            "factor_unit": fact.factor_unit,
            "per_unit": fact.factor_denominator,
        }
    return list(factors.values())


def _raw_material_quantity_rows(context: M3ExecutionContext) -> list[dict[str, Any]]:
    component_by_emission = _component_by_emission(context)
    rows: list[dict[str, Any]] = []
    for fact in context.canonical.emissions:
        if fact.kind != "material":
            continue
        rows.append(
            {
                "component": _name_of(
                    context, "component", component_by_emission.get(fact.emission_id)
                ),
                "material": _name_of(context, "material", fact.material_id),
                "quantity_value": fact.quantity_value,
                "quantity_unit": fact.quantity_unit,
            }
        )
    return rows


def _raw_energy_record_rows(context: M3ExecutionContext) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for fact in (*context.canonical.emissions, *context.canonical.process_only_emissions):
        if fact.kind != "energy":
            continue
        rows[fact.record_id] = {
            "record_id": fact.record_id,
            "carrier": _name_of(context, "carrier", fact.carrier_id),
            "quantity_value": fact.quantity_value,
            "quantity_unit": fact.quantity_unit,
            "attribution_mode": fact.mode,
        }
    return list(rows.values())


def _kg_material_emission_rows(context: M3ExecutionContext) -> list[dict[str, Any]]:
    component_by_emission = _component_by_emission(context)
    rows: list[dict[str, Any]] = []
    for fact in context.canonical.emissions:
        if fact.kind != "material":
            continue
        rows.append(
            {
                "component": _name_of(
                    context, "component", component_by_emission.get(fact.emission_id)
                ),
                "material": _name_of(context, "material", fact.material_id),
                "quantity_value": fact.quantity_value,
                "factor_value": fact.factor_value,
                "value_kgCO2e": fact.emission_value,
            }
        )
    return rows


def _kg_energy_record_rows(context: M3ExecutionContext) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for fact in (*context.canonical.emissions, *context.canonical.process_only_emissions):
        if fact.kind != "energy":
            continue
        rows[fact.record_id] = {
            "record_id": fact.record_id,
            "carrier": _name_of(context, "carrier", fact.carrier_id),
            "value_kgCO2e": fact.emission_value,
            "attribution_mode": fact.mode,
            "process": [_name_of(context, "process", pid) for pid in fact.process_ids] or None,
            "resource": [_name_of(context, "resource", rid) for rid in fact.resource_ids] or None,
        }
    return list(rows.values())


def _coverage_block(context: M3ExecutionContext) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for row in context.validation_coverage.records:
        counts = by_kind.setdefault(row.kind, {"accepted": 0, "rejected": 0})
        counts[row.status] += 1
    return {
        "accepted": context.validation_coverage.accepted_count,
        "rejected": context.validation_coverage.rejected_count,
        "by_kind": by_kind,
    }


def build_case_context(
    context: M3ExecutionContext, case: CarbonQLCase, mode: str
) -> dict[str, Any]:
    if not isinstance(context, M3ExecutionContext):
        raise TypeError("context must be M3ExecutionContext")
    base = {
        "mode": str(mode),
        "question": case.question,
        "release_profile": context.release_profile,
        "availability": dict(context.availability),
        "coverage": _coverage_block(context),
    }
    if mode == "llm_only_real":
        base.update(
            {
                "representation": "raw_source_tables_ungraphed",
                "note": (
                    "Carbon is NOT precomputed. Multiply each material or energy "
                    "quantity by the matching factor (join by material/carrier name) "
                    "to obtain kgCO2e, then aggregate as the question requires."
                ),
                "material_quantities": _raw_material_quantity_rows(context),
                "energy_records": _raw_energy_record_rows(context),
                "emission_factors": _emission_factor_rows(context),
            }
        )
        return base
    base.update(
        {
            "representation": "constructed_kg_multitable",
            "note": (
                "Carbon is already computed per entity and organised into "
                "dimensional tables. Select the correct table and aggregate, rank "
                "or trace as the question requires; do not recompute factors."
            ),
            "components": [
                {"name": row.name, "value_kgCO2e": row.value_kgCO2e}
                for row in context.component_summaries
            ],
            "material_families": [
                {"name": row.name, "value_kgCO2e": row.value_kgCO2e}
                for row in context.material_summaries
            ],
            "carriers": [
                {"name": row.name, "value_kgCO2e": row.value_kgCO2e}
                for row in context.carrier_summaries
            ],
            "material_emissions": _kg_material_emission_rows(context),
            "energy_records": _kg_energy_record_rows(context),
            "emission_factors": _emission_factor_rows(context),
        }
    )
    return base


def build_baseline_messages(
    context: M3ExecutionContext, case: CarbonQLCase, variant: str
) -> list[dict[str, str]]:
    snapshot = build_case_context(context, case, variant)
    if variant == "llm_only_real":
        preamble = (
            "Answer only from the supplied raw source tables. Carbon is not "
            "precomputed: multiply each quantity by its matching factor and "
            "aggregate. Preserve the requested product/material/process "
            "perspective and disclose unavailable coverage.\n"
        )
    else:
        preamble = (
            "Answer only from the supplied constructed knowledge-graph tables. "
            "Carbon is already computed per entity; select and aggregate the "
            "correct table. Preserve the requested product/material/process "
            "perspective and disclose unavailable coverage.\n"
        )
    system = preamble + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    if variant == "llm_structured_json_real":
        system = system + "\n\n" + _STRUCTURED_JSON_INSTRUCTION
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": case.question},
    ]


def _parse_response(
    payload: Mapping[str, Any], *, require_json: bool = False
) -> dict[str, Any]:
    content = payload.get("content", payload)
    if isinstance(content, Mapping):
        try:
            encoded = json.dumps(content, ensure_ascii=False, allow_nan=False)
            value = json.loads(
                encoded, parse_constant=_reject_nonfinite_json_constant
            )
        except (TypeError, ValueError) as exc:
            return {
                "status": "transport_error",
                "error": str(exc),
                "raw_content": str(content),
            }
        return value if isinstance(value, dict) else {"status": "transport_error"}
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text, parse_constant=_reject_nonfinite_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        if require_json:
            return {
                "status": "transport_error",
                "error": f"structured JSON required: {exc}",
                "raw_content": text,
            }
        if text and not text.startswith(("{", "[")):
            return {
                "response_format": "unstructured",
                "answer": text,
                "raw_content": text,
            }
        return {"status": "transport_error", "error": str(exc), "raw_content": text}
    return value if isinstance(value, dict) else {"status": "transport_error"}


def run_real_baseline_suite(
    *,
    cases: Sequence[CarbonQLCase],
    context: M3ExecutionContext,
    client: StaticLLMClient,
    variants: Sequence[str] = REAL_BASELINE_VARIANTS,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for variant in variants:
        rows = []
        failures = 0
        require_json = variant == "llm_structured_json_real"
        for case in cases:
            try:
                kwargs: dict[str, Any] = {}
                if require_json:
                    kwargs["response_format"] = {"type": "json_object"}
                payload = client.complete(
                    build_baseline_messages(context, case, variant), **kwargs
                )
            except (OSError, RuntimeError) as exc:
                parsed = {"status": "transport_error", "error": str(exc)}
            else:
                parsed = _parse_response(payload, require_json=require_json)
            failures += parsed.get("status") == "transport_error"
            rows.append(
                {
                    "question": case.question,
                    "variant": variant,
                    "observed": parsed,
                }
            )
        report[str(variant)] = {
            "case_count": len(rows),
            "transport_error_count": failures,
            "cases": tuple(rows),
        }
    return report


def publish_baseline_run(
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
        (staging / "baseline_report.json").write_bytes(payload)
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
    parser = argparse.ArgumentParser(
        description=(
            "Run llm_only_real, unconstrained_kg_llm_real, and "
            "llm_structured_json_real on the frozen benchmark using "
            "deepseek/deepseek-chat (temperature=0)."
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
        default=DEFAULT_BASELINE_OUTPUT_ROOT,
        help="Root directory for immutable run outputs.",
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=REAL_BASELINE_VARIANTS,
        default=list(REAL_BASELINE_VARIANTS),
        help="Baseline variants to run.",
    )
    return parser


def _provider_client(args: argparse.Namespace):
    from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{args.api_key_env} is required to run provider-backed baselines"
        )
    return OpenAICompatibleToolClient(
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        temperature=0.0,
    )


def _write_baseline_cli_run(
    args: argparse.Namespace,
    suites: Mapping[str, Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> Path:
    run_id = args.run_id or (
        "e5_real_baselines_layerdedup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    case_path = output_dir / "e5_real_baseline_cases.jsonl"
    case_path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, allow_nan=False) + "\n"
            for row in rows
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
        "case_count": len(rows) // max(len(suites), 1),
        "variants": {name: dict(payload["metrics"]) for name, payload in suites.items()},
        "truth_leakage_policy": (
            "expected fields are evaluator-only and never enter baseline prompts"
        ),
        "case_jsonl": str(case_path),
    }
    (output_dir / "e5_real_baseline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return output_dir


def main(argv: Sequence[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    from dm2c_e5_e7_experiment_runner import (
        evaluate_benchmark_response,
        load_benchmark_cases,
        summarize_results,
    )

    cases = load_benchmark_cases(args.benchmark)
    context = load_baseline_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    variants = tuple(args.variants)
    raw = run_real_baseline_suite(
        cases=cases,
        context=context,
        client=_provider_client(args),
        variants=variants,
    )
    suites: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    for variant in variants:
        evaluated = []
        for index, (case, raw_row) in enumerate(
            zip(cases, raw[variant]["cases"]), start=1
        ):
            observed = dict(raw_row["observed"])
            observed["variant"] = variant
            row = evaluate_benchmark_response(case, observed, variant)
            row["case_index"] = index
            evaluated.append(row)
        suites[variant] = {
            "metrics": {
                **summarize_results(evaluated),
                "transport_error_count": raw[variant]["transport_error_count"],
            },
            "cases": evaluated,
        }
        all_rows.extend(evaluated)
    output_dir = _write_baseline_cli_run(args, suites, all_rows)
    print(output_dir)
    return 0


__all__ = [
    "DEFAULT_FROZEN_V10_BENCHMARK",
    "REAL_BASELINE_VARIANTS",
    "StaticLLMClient",
    "build_cli_parser",
    "build_baseline_messages",
    "build_case_context",
    "load_baseline_context",
    "main",
    "publish_baseline_run",
    "run_real_baseline_suite",
]


if __name__ == "__main__":
    raise SystemExit(main())
