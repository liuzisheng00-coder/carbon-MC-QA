"""
Experiment backend for DM2C M3 question answering.

Experiments run the same CarbonQL chain the interface runs and score the same
payload, so a benchmark number cannot come from a code path a user never takes.
The module keeps experiment execution separate from FastAPI so the same logic
can be reused by API endpoints, scripts, and paper-result generation.

An ablation removes one compiler stage at a time: V1 gives the model the
operation list alone, V2 adds the typed contract and the live graph schema, V3
adds validator-driven repair, and V4 adds the semantic coverage gate.
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from dm2c_carbonql_service import (
    COMPILER_VARIANTS,
    DEFAULT_VARIANT,
    TRACE_AGENTS,
    CarbonQLService,
    answer_payload,
)

REQUIRED_TRACE_AGENTS = set(TRACE_AGENTS)

SUPPORTED_VARIANTS = set(COMPILER_VARIANTS)

ABLATION_VARIANTS = list(COMPILER_VARIANTS)


@dataclass
class ExperimentCase:
    case_id: str
    question: str
    expected: Dict[str, Any] = field(default_factory=dict)
    selected_component_ids: List[str] = field(default_factory=list)
    scope: str = "project"
    filters: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], index: int = 0) -> "ExperimentCase":
        return cls(
            case_id=str(data.get("case_id") or data.get("id") or f"q{index + 1}"),
            question=str(data.get("question") or ""),
            expected=dict(data.get("expected") or {}),
            selected_component_ids=[str(value) for value in data.get("selected_component_ids", [])],
            scope=str(data.get("scope") or "project"),
            filters=dict(data.get("filters") or {}),
            tags=[str(value) for value in data.get("tags", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_default_experiment_cases(assessment_payload: Dict[str, Any]) -> List[ExperimentCase]:
    """Create a compact starter benchmark covering the four answer perspectives."""
    rows = assessment_payload.get("results", []) or []
    first = rows[0] if rows else {}
    component_id = (
        first.get("component")
        or first.get("componentGlobalId")
        or first.get("globalId")
        or first.get("id")
        or ""
    )
    component_name = first.get("componentName") or component_id or "the selected component"

    selected = [str(component_id)] if component_id else []
    return [
        ExperimentCase(
            case_id="default_product_total",
            question="What is the total manufacturing-stage carbon of the project?",
            expected={
                "perspective": "product",
                "operation": "aggregate",
                "scope": "project",
                "status": "ok",
            },
            tags=["qa_benchmark", "product", "value"],
        ),
        ExperimentCase(
            case_id="default_material_ranking",
            question="Rank the material carbon contributors and show the largest material group.",
            expected={
                "perspective": "material",
                "operation": "rank",
                "status": "ok",
            },
            tags=["qa_benchmark", "material", "ranking"],
        ),
        ExperimentCase(
            case_id="default_process_breakdown",
            question="Break the factory energy carbon down by production stage.",
            expected={
                "perspective": "process",
                "operation": "breakdown",
                "status": "ok",
            },
            tags=["qa_benchmark", "process", "breakdown"],
        ),
        ExperimentCase(
            case_id="default_traceability_chain",
            question=f"Trace the quantity, factor and evidence chain for {component_name}.",
            expected={
                "perspective": "product",
                "operation": "trace",
                "status": "ok",
            },
            selected_component_ids=selected,
            scope="selected_component" if selected else "project",
            tags=["qa_benchmark", "traceability", "evidence"],
        ),
    ]


def evaluate_experiment_case(
    case: ExperimentCase,
    payload: Dict[str, Any],
    elapsed_ms: float,
    variant: str,
) -> Dict[str, Any]:
    requirement = payload.get("carbonRequirement") or {}
    query_result = payload.get("queryResult") or {}
    expected = case.expected or {}
    scores = {
        "slot_match": _slot_match(requirement, expected),
        "status_match": _status_match(query_result, expected),
        "trace_complete": _trace_complete(payload.get("reasoningTrace", [])),
        "answer_contains": _answer_contains(payload.get("answer", ""), expected),
        "numeric_match": _numeric_match(query_result, expected),
    }
    scores["passed"] = all(value is not False for value in scores.values())

    return {
        "case_id": case.case_id,
        "variant": variant,
        "question": case.question,
        "tags": case.tags,
        "elapsed_ms": round(float(elapsed_ms), 3),
        "expected": expected,
        "observed": {
            "requirement": requirement,
            "query_status": query_result.get("status"),
            "query_summary": query_result.get("summary", {}),
            "row_count": len(query_result.get("rows", []) or []),
            "answer": payload.get("answer", ""),
        },
        "scores": scores,
        "trace_agents": [
            str(item.get("agent", ""))
            for item in payload.get("reasoningTrace", []) or []
            if isinstance(item, dict)
        ],
    }


def run_experiment_suite(
    service: CarbonQLService,
    assessment_payload: Dict[str, Any],
    cases: Optional[Sequence[ExperimentCase]] = None,
    variants: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = time.time()
    test_cases = list(cases or generate_default_experiment_cases(assessment_payload))
    requested_variants = _normalize_variants(variants)

    report: Dict[str, Any] = {
        "run_id": run_id,
        "experiment_type": "m3_qa_backend",
        "case_count": len(test_cases),
        "started_at": started,
        "release_id": service.release_id,
        "variants": {},
    }

    for variant in requested_variants:
        results = []
        for case in test_cases:
            payload, elapsed_ms = _run_variant_case(service, variant, case)
            results.append(evaluate_experiment_case(case, payload, elapsed_ms, variant))

        report["variants"][variant] = {
            "metrics": summarize_case_results(results),
            "cases": results,
        }

    finished = time.time()
    report["finished_at"] = finished
    report["elapsed_ms"] = round((finished - started) * 1000, 3)
    return report


def evaluate_calculation_validation(
    service: CarbonQLService,
    references: Sequence[Dict[str, Any]],
    tolerance_abs: float = 1e-6,
    tolerance_pct: float = 0.01,
) -> Dict[str, Any]:
    """Compare the carbon account with manual or LCA reference values.

    This measures accounting fidelity, not language understanding, so each
    target is read with a fixed program instead of a synthesized one.
    """
    started = time.time()

    records = []
    for index, reference in enumerate(references):
        ref_id = str(reference.get("reference_id") or reference.get("id") or f"ref{index + 1}")
        level = str(reference.get("level") or "component")
        target_id = str(reference.get("target_id") or reference.get("componentGlobalId") or "")
        expected = _expected_values(reference)
        project_level = level == "project" or target_id in {"project", "__project__"}
        measurement = service.measure("" if project_level else target_id)
        observed = measurement.to_dict()

        if not project_level and all(
            observed[key] is None
            for key in ("C_mat_kgCO2e", "C_proc_kgCO2e", "C_total_kgCO2e")
        ):
            records.append(
                {
                    "reference_id": ref_id,
                    "level": level,
                    "target_id": target_id,
                    "status": "unmatched",
                    "expected": expected,
                    "observed": observed,
                    "errors": {},
                    "within_tolerance": False,
                }
            )
            continue

        errors = _carbon_errors(observed, expected)
        records.append(
            {
                "reference_id": ref_id,
                "level": level,
                "target_id": target_id,
                "target_name": observed.get("target_name", ""),
                "status": "matched",
                "expected": expected,
                "observed": observed,
                "errors": errors,
                "within_tolerance": _within_tolerance(errors, tolerance_abs, tolerance_pct),
            }
        )

    report = {
        "run_id": uuid.uuid4().hex[:12],
        "experiment_type": "carbon_validation",
        "release_id": service.release_id,
        "record_count": len(records),
        "matched_count": sum(1 for record in records if record["status"] == "matched"),
        "unmatched_count": sum(1 for record in records if record["status"] == "unmatched"),
        "tolerance": {
            "abs_kgCO2e": tolerance_abs,
            "pct": tolerance_pct,
        },
        "metrics": _summarize_validation_records(records),
        "records": records,
    }
    report["elapsed_ms"] = round((time.time() - started) * 1000, 3)
    return report


def summarize_case_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(results)
    if not count:
        return {
            "case_count": 0,
            "slot_accuracy": None,
            "status_match_rate": None,
            "trace_complete_rate": None,
            "answer_contains_rate": None,
            "numeric_match_rate": None,
            "pass_rate": None,
            "mean_latency_ms": None,
            "p95_latency_ms": None,
        }

    return {
        "case_count": count,
        "slot_accuracy": _rate(results, "slot_match"),
        "status_match_rate": _rate(results, "status_match"),
        "trace_complete_rate": _rate(results, "trace_complete"),
        "answer_contains_rate": _rate(results, "answer_contains"),
        "numeric_match_rate": _rate(results, "numeric_match"),
        "pass_rate": _rate(results, "passed"),
        "mean_latency_ms": round(statistics.fmean(item["elapsed_ms"] for item in results), 3),
        "p95_latency_ms": round(_percentile([item["elapsed_ms"] for item in results], 0.95), 3),
    }


def make_robustness_cases(base_cases: Sequence[ExperimentCase]) -> List[ExperimentCase]:
    """Add prompts whose only correct answer is a refusal with a named status."""
    cases = list(base_cases)
    cases.append(
        ExperimentCase(
            case_id="robust_unknown_component",
            question="Trace the carbon calculation path for component GlobalId DOES_NOT_EXIST.",
            expected={
                "operation": "trace",
                "status": "unresolved_target",
            },
            tags=["robustness", "unknown_target"],
        )
    )
    cases.append(
        ExperimentCase(
            case_id="robust_withheld_source",
            question=(
                "How much carbon does the project carry, without assuming which source?"
            ),
            expected={"status": "partial"},
            tags=["robustness", "withheld_source"],
        )
    )
    return cases


def _run_variant_case(
    service: CarbonQLService,
    variant: str,
    case: ExperimentCase,
) -> tuple[Dict[str, Any], float]:
    start = time.perf_counter()
    answer = service.answer(case.question, case.selected_component_ids, variant=variant)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return answer_payload(answer), elapsed_ms


def _normalize_variants(variants: Optional[Sequence[str]]) -> List[str]:
    out = []
    for variant in variants or [DEFAULT_VARIANT]:
        value = str(variant or "").strip()
        if value in SUPPORTED_VARIANTS and value not in out:
            out.append(value)
    return out or [DEFAULT_VARIANT]


def _expected_values(reference: Dict[str, Any]) -> Dict[str, Optional[float]]:
    expected = dict(reference.get("expected") or {})
    return {
        "C_mat_kgCO2e": _first_float(
            expected.get("C_mat_kgCO2e"),
            expected.get("materialCarbon_kgCO2e"),
            reference.get("C_mat_kgCO2e"),
            reference.get("materialCarbon_kgCO2e"),
        ),
        "C_proc_kgCO2e": _first_float(
            expected.get("C_proc_kgCO2e"),
            expected.get("processCarbon_kgCO2e"),
            reference.get("C_proc_kgCO2e"),
            reference.get("processCarbon_kgCO2e"),
        ),
        "C_total_kgCO2e": _first_float(
            expected.get("C_total_kgCO2e"),
            expected.get("knownTotalCarbon_kgCO2e"),
            expected.get("C_MM_kgCO2e"),
            reference.get("C_total_kgCO2e"),
            reference.get("knownTotalCarbon_kgCO2e"),
            reference.get("C_MM_kgCO2e"),
        ),
    }


def _carbon_errors(observed: Dict[str, Any], expected: Dict[str, Optional[float]]) -> Dict[str, Dict[str, Optional[float]]]:
    errors: Dict[str, Dict[str, Optional[float]]] = {}
    for key, expected_value in expected.items():
        if expected_value is None:
            continue
        observed_value = _first_float(observed.get(key))
        if observed_value is None:
            errors[key] = {
                "observed": None,
                "expected": expected_value,
                "abs": None,
                "pct": None,
            }
            continue
        abs_error = abs(observed_value - expected_value)
        errors[key] = {
            "observed": observed_value,
            "expected": expected_value,
            "abs": abs_error,
            "pct": abs_error / abs(expected_value) if expected_value else None,
        }
    return errors


def _within_tolerance(errors: Dict[str, Dict[str, Optional[float]]], tolerance_abs: float, tolerance_pct: float) -> bool:
    if not errors:
        return False
    for error in errors.values():
        abs_error = error.get("abs")
        pct_error = error.get("pct")
        if abs_error is None:
            return False
        if abs_error <= tolerance_abs:
            continue
        if pct_error is not None and pct_error <= tolerance_pct:
            continue
        return False
    return True


def _summarize_validation_records(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out = {
        "material_mae_kgCO2e": _mean_abs(records, "C_mat_kgCO2e"),
        "process_mae_kgCO2e": _mean_abs(records, "C_proc_kgCO2e"),
        "total_mae_kgCO2e": _mean_abs(records, "C_total_kgCO2e"),
        "material_mape": _mean_pct(records, "C_mat_kgCO2e"),
        "process_mape": _mean_pct(records, "C_proc_kgCO2e"),
        "total_mape": _mean_pct(records, "C_total_kgCO2e"),
        "within_tolerance_rate": None,
    }
    matched = [record for record in records if record.get("status") == "matched"]
    if matched:
        out["within_tolerance_rate"] = round(sum(1 for record in matched if record.get("within_tolerance")) / len(matched), 4)
    return out


def _mean_abs(records: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
    values = [
        record["errors"][key]["abs"]
        for record in records
        if key in record.get("errors", {}) and record["errors"][key].get("abs") is not None
    ]
    return round(statistics.fmean(values), 6) if values else None


def _mean_pct(records: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
    values = [
        record["errors"][key]["pct"]
        for record in records
        if key in record.get("errors", {}) and record["errors"][key].get("pct") is not None
    ]
    return round(statistics.fmean(values), 6) if values else None


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


_OPERATION_ALIASES = {"aggregate": "value"}


def _slot_match(requirement: Dict[str, Any], expected: Dict[str, Any]) -> Optional[bool]:
    slot_keys = ["perspective", "operation", "scope", "source"]
    checks = []
    for key in slot_keys:
        if key in expected:
            resp_val = str(requirement.get(key, ""))
            gold_val = str(expected.get(key, ""))
            if key == "operation":
                resp_val = _OPERATION_ALIASES.get(resp_val, resp_val)
                gold_val = _OPERATION_ALIASES.get(gold_val, gold_val)
            checks.append(resp_val == gold_val)
    return all(checks) if checks else None


def _status_match(query_result: Dict[str, Any], expected: Dict[str, Any]) -> Optional[bool]:
    if "status" not in expected:
        return None
    observed = str(query_result.get("status") or "")
    expected_status = str(expected.get("status") or "")
    return observed == expected_status


def _trace_complete(trace: Iterable[Dict[str, Any]]) -> bool:
    agents = {str(item.get("agent", "")) for item in trace if isinstance(item, dict)}
    return REQUIRED_TRACE_AGENTS.issubset(agents)


def _answer_contains(answer: Any, expected: Dict[str, Any]) -> Optional[bool]:
    required = expected.get("answer_contains")
    if not required:
        return None
    answer_text = str(answer or "").casefold()
    return all(str(term).casefold() in answer_text for term in required)


def _numeric_match(query_result: Dict[str, Any], expected: Dict[str, Any]) -> Optional[bool]:
    expected_summary = expected.get("summary")
    if not expected_summary:
        return None
    observed_summary = query_result.get("summary") or {}
    tolerance = float(expected.get("numeric_tolerance", 1e-6))
    for key, expected_value in expected_summary.items():
        observed_value = observed_summary.get(key)
        if observed_value is None:
            return False
        try:
            if abs(float(observed_value) - float(expected_value)) > tolerance:
                return False
        except (TypeError, ValueError):
            if str(observed_value) != str(expected_value):
                return False
    return True


def _rate(results: Sequence[Dict[str, Any]], score_key: str) -> Optional[float]:
    scored = [item["scores"].get(score_key) for item in results if item["scores"].get(score_key) is not None]
    if not scored:
        return None
    return round(sum(1 for value in scored if value) / len(scored), 4)


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)
