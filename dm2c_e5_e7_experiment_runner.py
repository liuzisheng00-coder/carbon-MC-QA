#!/usr/bin/env python3
"""
Research runner for E5/E6 scoring infrastructure and E7 robustness injections.

The runner deliberately separates two things:
- benchmark/evaluator mechanics that are safe to run before human E4 review;
- real LLM and unconstrained-KG baselines, which should be rerun after the reviewed benchmark is frozen.

The built-in responders are deterministic proxies/negative controls. They are
useful for validating metrics such as status accuracy and unsupported-answer
rate, but they are not paper-ready LLM-only or unconstrained-KG baseline measurements.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_E4_BENCHMARK = Path(
    "outputs/research_experiments/e4_benchmark/latest_e4_benchmark_summary.json"
)
DEFAULT_OUT_ROOT = Path("outputs/research_experiments/e5_e7")

DEFAULT_VARIANTS = [
    "full",
    "graph_only",
    "heuristic_m31_proxy",
    "no_m33_llm",
    "llm_only_proxy",
    "unconstrained_kg_llm_proxy",
    "no_validation_gate",
    "no_status_blocking",
    "no_provenance_constraint",
]

UNANSWERABLE_STATUSES = {
    "empty_result",
    "unresolved_target",
    "incomplete_path",
    "clarification_required",
}


@dataclass
class BenchmarkCase:
    case_id: str
    question: str
    expected: Dict[str, Any] = field(default_factory=dict)
    selected_component_ids: List[str] = field(default_factory=list)
    scope: str = "project"
    filters: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    difficulty: str = ""
    generation: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Dict[str, Any], index: int = 0) -> "BenchmarkCase":
        return cls(
            case_id=str(row.get("case_id") or row.get("id") or f"case_{index + 1:03d}"),
            question=str(row.get("question") or ""),
            expected=dict(row.get("expected") or {}),
            selected_component_ids=[str(value) for value in row.get("selected_component_ids", []) or []],
            scope=str(row.get("scope") or "project"),
            filters=dict(row.get("filters") or {}),
            tags=[str(value) for value in row.get("tags", []) or []],
            difficulty=str(row.get("difficulty") or ""),
            generation=dict(row.get("generation") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_benchmark_path(path: Path) -> Path:
    if path.suffix.lower() != ".json" or not path.exists():
        return path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return path

    balanced = str((data.get("outputs") or {}).get("balanced_jsonl") or "")
    if not balanced:
        return path
    candidate = Path(balanced)
    if candidate.exists():
        return candidate
    relative_to_summary = path.parent / candidate
    if relative_to_summary.exists():
        return relative_to_summary
    return candidate


def load_benchmark_metadata(path: Path) -> Dict[str, Any]:
    metadata_path = path
    if path.suffix.lower() == ".jsonl":
        sidecar_name = path.name.replace(
            "e4_balanced_benchmark_150", "e4_benchmark_summary", 1
        ).removesuffix(".jsonl") + ".json"
        sidecar_path = path.parent / sidecar_name
        if sidecar_path.exists():
            metadata_path = sidecar_path
    if metadata_path.suffix.lower() != ".json" or not metadata_path.exists():
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    human_review = data.get("human_review") or {}
    return {
        "source_path": str(metadata_path),
        "resolved_benchmark_path": str(resolve_benchmark_path(metadata_path)),
        "paper_ready_benchmark": bool(data.get("paper_ready_benchmark")),
        "human_review_status": str(human_review.get("status") or ""),
        "human_review_decision": str(human_review.get("decision") or ""),
        "source_run_id": str(data.get("source_run_id") or data.get("run_id") or ""),
    }


def load_benchmark_cases(source: Path | str | Sequence[Dict[str, Any]]) -> List[BenchmarkCase]:
    if isinstance(source, (str, Path)):
        path = resolve_benchmark_path(Path(source))
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        rows = list(source)
    return [BenchmarkCase.from_dict(row, index) for index, row in enumerate(rows)]


def generate_e7_injection_cases(per_category: int = 15) -> List[BenchmarkCase]:
    categories = [
        (
            "delete_quantity",
            "incomplete_path",
            "quantity_missing",
            "Report carbon for component E7-QTY-{i}, where the quantity field has been removed.",
        ),
        (
            "delete_factor",
            "incomplete_path",
            "factor_missing",
            "Trace the material carbon path for component E7-FACTOR-{i}, where the emission factor lookup is unavailable.",
        ),
        (
            "delete_process_record",
            "incomplete_path",
            "process_record_missing",
            "From the process perspective, calculate carbon for E7-PROC-{i} after deleting its process energy record.",
        ),
        (
            "unit_mismatch",
            "incomplete_path",
            "unit_mismatch",
            "Explain carbon for E7-UNIT-{i}, where quantity units and factor units no longer match.",
        ),
        (
            "delete_allocation_basis",
            "incomplete_path",
            "allocation_basis_missing",
            "Aggregate process carbon for E7-ALLOC-{i}, where the batch allocation basis is missing.",
        ),
        (
            "synonym_rewrite",
            "executable",
            "synonym_rewrite",
            "Using equivalent wording, show the embodied carbon trail for the selected module item E7-SYN-{i}.",
        ),
        (
            "ambiguous_target_attack",
            "clarification_required",
            "ambiguous_target",
            "Rank carbon for the panel E7-AMB-{i}; ask for clarification if more than one panel could match.",
        ),
    ]

    cases: List[BenchmarkCase] = []
    counter = 1
    for injection_type, status, marker, template in categories:
        for local_index in range(1, per_category + 1):
            perspective = "traceability"
            operation = "trace"
            if injection_type in {"delete_process_record", "delete_allocation_basis"}:
                perspective = "process"
                operation = "aggregate"
            if injection_type == "ambiguous_target_attack":
                perspective = "product"
                operation = "rank"

            expected = {
                "perspective": perspective,
                "operation": operation,
                "status": status,
                "summary": {
                    "injection_type": injection_type,
                    "missing_or_attack": marker,
                },
                "injection_type": injection_type,
                "answer_contains": [marker if status != "clarification_required" else "clarification"],
                "numeric_tolerance": 1e-4,
            }
            if status == "executable":
                expected["summary"]["expected_disclosure"] = marker

            cases.append(
                BenchmarkCase(
                    case_id=f"e7_{counter:03d}_{injection_type}",
                    question=template.format(i=local_index),
                    expected=expected,
                    scope="selected_component",
                    tags=["e7_robustness", injection_type, status],
                    difficulty="injected",
                    generation={
                        "source": "e7_injection_generator",
                        "injection_type": injection_type,
                    },
                )
            )
            counter += 1
    return cases


class RuleBasedBenchmarkResponder:
    """Deterministic responder used to validate metrics and ablation behavior."""

    def __init__(self, variant: str = "full"):
        self.variant = variant

    def answer(self, case: BenchmarkCase) -> Dict[str, Any]:
        started = time.perf_counter()
        expected = case.expected or {}
        expected_status = str(expected.get("status") or "executable")
        status = expected_status
        summary = dict(expected.get("summary") or {})
        evidence_ids = list(expected.get("evidence_ids") or [])
        perspective = str(expected.get("perspective") or "")
        operation = str(expected.get("operation") or "")
        answer_parts = [
            f"status={status}",
            f"perspective={perspective}",
            f"operation={operation}",
        ]

        if self.variant == "no_validation_gate" and expected_status in {
            "unresolved_target",
            "clarification_required",
            "empty_result",
        }:
            status = "executable"
            summary = {"estimatedCarbon_kgCO2e": 888.8}
            answer_parts.append("estimatedCarbon_kgCO2e=888.8")
        elif self.variant == "no_status_blocking" and expected_status == "incomplete_path":
            status = "executable"
            summary = {"estimatedCarbon_kgCO2e": 999.9}
            answer_parts.append("estimatedCarbon_kgCO2e=999.9")
        elif self.variant == "no_provenance_constraint":
            evidence_ids = evidence_ids + ["hallucinated:evidence:999"]
            answer_parts.append("extra unsupported cited value 999.9 from hallucinated:evidence:999")
        elif self.variant in {"llm_only_proxy", "unconstrained_kg_llm_proxy"}:
            if expected_status in UNANSWERABLE_STATUSES:
                status = "executable"
                summary = {"estimatedCarbon_kgCO2e": self._proxy_estimate(case)}
                answer_parts.append(f"estimatedCarbon_kgCO2e={summary['estimatedCarbon_kgCO2e']}")
            elif self.variant == "unconstrained_kg_llm_proxy" and case.case_id.endswith("_compare_executable"):
                summary = dict(summary)
                summary["unverifiedDifference_kgCO2e"] = 111.1
                answer_parts.append("unverifiedDifference_kgCO2e=111.1")
        elif self.variant == "no_m33_llm":
            answer_parts.append("template_answer_from_structured_query")
        elif self.variant == "heuristic_m31_proxy":
            if "clarification_required" == expected_status:
                status = "unresolved_target"
                answer_parts.append("heuristic parser treated ambiguity as unresolved target")

        if self.variant in {"full", "graph_only", "no_m33_llm", "heuristic_m31_proxy"}:
            answer_parts.extend(_summary_answer_parts(summary))
            required_terms = list(expected.get("answer_contains") or [])
            if expected_status in UNANSWERABLE_STATUSES and not required_terms:
                required_terms.append(expected_status)
            answer_parts.extend(str(term) for term in required_terms)

        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "variant": self.variant,
            "status": status,
            "perspective": perspective,
            "operation": operation,
            "summary": summary,
            "evidence_ids": evidence_ids,
            "answer": "; ".join(str(part) for part in answer_parts if str(part)),
            "latency_ms": round(elapsed_ms, 3),
        }

    @staticmethod
    def _proxy_estimate(case: BenchmarkCase) -> float:
        seed = sum(ord(ch) for ch in case.case_id)
        return round(100.0 + (seed % 9000) / 10.0, 4)


# Diagnostics that must not move the pass gate: unsupported_answer is folded in
# separately, and the key-free numeric score exists only to compare systems that
# do not share the answer schema.
_NON_GATING_SCORES = frozenset({"unsupported_answer", "numeric_match_keyfree"})


def evaluate_benchmark_response(
    case: BenchmarkCase,
    response: Dict[str, Any],
    variant: str,
) -> Dict[str, Any]:
    expected = case.expected or {}
    unsupported_values = detect_unsupported_numeric_values(
        str(response.get("answer", "")),
        expected,
        response.get("supported_numeric_values") or [],
        response.get("supported_text_tokens")
        or response.get("supported_evidence_ids")
        or [],
    )
    hallucinated_evidence = _hallucinated_evidence(
        response.get("evidence_ids") or [],
        expected,
        response.get("supported_evidence_ids") or [],
    )
    scores = {
        "slot_match": _slot_match(response, expected),
        "status_match": _status_match(response, expected),
        "numeric_match": _numeric_match(response.get("summary") or {}, expected),
        "numeric_match_keyfree": _numeric_match_keyfree(
            response.get("summary") or {}, expected
        ),
        "answer_contains": _answer_contains(str(response.get("answer", "")), expected),
        "provenance_supported": len(hallucinated_evidence) == 0,
    }
    scores["unsupported_answer"] = bool(unsupported_values or hallucinated_evidence)
    gating_scores = {
        key: value
        for key, value in scores.items()
        if key not in _NON_GATING_SCORES
    }
    scores["passed"] = all(value is not False for value in gating_scores.values()) and not scores["unsupported_answer"]
    return {
        "case_id": case.case_id,
        "variant": variant,
        "question": case.question,
        "expected": expected,
        "observed": response,
        "scores": scores,
        "unsupported_numeric_values": unsupported_values,
        "unsupported_numeric_count": len(unsupported_values),
        "hallucinated_evidence_ids": hallucinated_evidence,
        "latency_ms": float(response.get("latency_ms") or 0.0),
        "tags": case.tags,
    }


def summarize_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not results:
        return {
            "case_count": 0,
            "numeric_accuracy": None,
            "numeric_accuracy_keyfree": None,
            "status_accuracy": None,
            "slot_accuracy": None,
            "answer_contains_rate": None,
            "unsupported_answer_rate": None,
            "hallucinated_reference_rate": None,
            "pass_rate": None,
            "mean_latency_ms": None,
            "p95_latency_ms": None,
        }
    return {
        "case_count": len(results),
        "numeric_accuracy": _rate(results, "numeric_match"),
        "numeric_accuracy_keyfree": _rate(results, "numeric_match_keyfree"),
        "status_accuracy": _rate(results, "status_match"),
        "slot_accuracy": _rate(results, "slot_match"),
        "answer_contains_rate": _rate(results, "answer_contains"),
        "provenance_supported_rate": _rate(results, "provenance_supported"),
        "unsupported_answer_rate": round(
            sum(1 for item in results if item["scores"].get("unsupported_answer")) / len(results),
            4,
        ),
        "hallucinated_reference_rate": round(
            sum(1 for item in results if item.get("hallucinated_evidence_ids")) / len(results),
            4,
        ),
        "pass_rate": _rate(results, "passed"),
        "mean_latency_ms": round(statistics.fmean(float(item.get("latency_ms") or 0.0) for item in results), 3),
        "p95_latency_ms": round(_percentile([float(item.get("latency_ms") or 0.0) for item in results], 0.95), 3),
    }


def detect_unsupported_numeric_values(
    answer: str,
    expected: Dict[str, Any],
    supported_values: Sequence[Any] = (),
    supported_text_tokens: Sequence[Any] = (),
) -> List[str]:
    allowed = _allowed_numbers(expected)
    allowed.extend(
        value
        for item in supported_values
        if (value := _to_float(item)) is not None
    )
    for item in supported_text_tokens:
        for token in re.findall(r"(?<![A-Za-z0-9_$])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_$])", str(item)):
            value = _to_float(token)
            if value is not None:
                allowed.append(value)
    unsupported: List[str] = []
    seen = set()
    scan_text = str(answer or "")
    for supported_token in sorted(
        (str(value) for value in supported_text_tokens if str(value)),
        key=len,
        reverse=True,
    ):
        scan_text = scan_text.replace(supported_token, "")
    for token in re.findall(r"(?<![A-Za-z0-9_$])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_$])", scan_text):
        value = _to_float(token)
        if value is None:
            continue
        if any(_numeric_close(value, allowed_value, float(expected.get("numeric_tolerance", 1e-4))) for allowed_value in allowed):
            continue
        if token not in seen:
            unsupported.append(token)
            seen.add(token)
    return unsupported


def run_suite(cases: Sequence[BenchmarkCase], variants: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for variant in variants:
        responder = RuleBasedBenchmarkResponder(variant)
        results = [
            evaluate_benchmark_response(case, responder.answer(case), variant)
            for case in cases
        ]
        out[variant] = {
            "metrics": summarize_results(results),
            "cases": results,
        }
    return out


def write_jsonl(path: Path, cases: Sequence[BenchmarkCase]) -> None:
    path.write_text(
        "\n".join(json.dumps(case.to_dict(), ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )


def write_metrics_csv(path: Path, suites: Dict[str, Any]) -> None:
    fieldnames = [
        "variant",
        "case_count",
        "numeric_accuracy",
        "status_accuracy",
        "slot_accuracy",
        "answer_contains_rate",
        "provenance_supported_rate",
        "unsupported_answer_rate",
        "hallucinated_reference_rate",
        "pass_rate",
        "mean_latency_ms",
        "p95_latency_ms",
        "transport_error_count",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for variant, payload in suites.items():
            row = {"variant": variant}
            row.update(payload.get("metrics") or {})
            writer.writerow(row)


def write_case_results_csv(path: Path, suites: Dict[str, Any]) -> None:
    fieldnames = [
        "variant",
        "case_id",
        "expected_status",
        "observed_status",
        "perspective",
        "operation",
        "numeric_match",
        "status_match",
        "unsupported_answer",
        "unsupported_numeric_values",
        "hallucinated_evidence_ids",
        "latency_ms",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for variant, payload in suites.items():
            for result in payload.get("cases", []):
                expected = result.get("expected") or {}
                observed = result.get("observed") or {}
                scores = result.get("scores") or {}
                writer.writerow(
                    {
                        "variant": variant,
                        "case_id": result.get("case_id"),
                        "expected_status": expected.get("status"),
                        "observed_status": observed.get("status"),
                        "perspective": expected.get("perspective"),
                        "operation": expected.get("operation"),
                        "numeric_match": scores.get("numeric_match"),
                        "status_match": scores.get("status_match"),
                        "unsupported_answer": scores.get("unsupported_answer"),
                        "unsupported_numeric_values": "; ".join(result.get("unsupported_numeric_values") or []),
                        "hallucinated_evidence_ids": "; ".join(result.get("hallucinated_evidence_ids") or []),
                        "latency_ms": result.get("latency_ms"),
                    }
                )


def run(
    benchmark_path: Path = DEFAULT_E4_BENCHMARK,
    out_root: Path = DEFAULT_OUT_ROOT,
    variants: Sequence[str] = DEFAULT_VARIANTS,
    e7_per_category: int = 15,
) -> Dict[str, Any]:
    run_id = time.strftime("run_%Y%m%d_%H%M%S")
    out_dir = out_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    benchmark_metadata = load_benchmark_metadata(benchmark_path)
    benchmark_human_reviewed = (
        bool(benchmark_metadata.get("paper_ready_benchmark"))
        or benchmark_metadata.get("human_review_status") == "passed_all"
    )
    e4_cases = load_benchmark_cases(benchmark_path)
    e5_e6_suites = run_suite(e4_cases, variants)
    e7_cases = generate_e7_injection_cases(per_category=e7_per_category)
    e7_suites = run_suite(
        e7_cases,
        [
            "full",
            "graph_only",
            "no_validation_gate",
            "no_status_blocking",
            "no_provenance_constraint",
            "llm_only_proxy",
            "unconstrained_kg_llm_proxy",
        ],
    )

    write_metrics_csv(out_dir / "e5_e6_provisional_metrics.csv", e5_e6_suites)
    write_case_results_csv(out_dir / "e5_e6_provisional_case_results.csv", e5_e6_suites)
    write_jsonl(out_dir / "e7_injection_cases.jsonl", e7_cases)
    write_metrics_csv(out_dir / "e7_robustness_metrics.csv", e7_suites)
    write_case_results_csv(out_dir / "e7_robustness_case_results.csv", e7_suites)

    summary = {
        "run_id": run_id,
        "output_dir": str(out_dir),
        "generated_at": time.time(),
        "benchmark_path": str(benchmark_path),
        "benchmark_metadata": benchmark_metadata,
        "claim_scope": "provisional_metric_pipeline_and_proxy_negative_controls",
        "paper_ready": False,
        "paper_ready_blocker": (
            "Run real LLM-only and unconstrained-KG-LLM baselines; this run uses deterministic proxy/negative-control responders."
            if benchmark_human_reviewed
            else "Rerun real E5/E6 baselines after E4 human review freezes the benchmark questions."
        ),
        "e5_e6": {
            "case_count": len(e4_cases),
            "variants": {
                variant: payload["metrics"]
                for variant, payload in e5_e6_suites.items()
            },
            "metrics_csv": str(out_dir / "e5_e6_provisional_metrics.csv"),
            "case_results_csv": str(out_dir / "e5_e6_provisional_case_results.csv"),
        },
        "e7": {
            "case_count": len(e7_cases),
            "per_category": e7_per_category,
            "injection_jsonl": str(out_dir / "e7_injection_cases.jsonl"),
            "variants": {
                variant: payload["metrics"]
                for variant, payload in e7_suites.items()
            },
            "metrics_csv": str(out_dir / "e7_robustness_metrics.csv"),
            "case_results_csv": str(out_dir / "e7_robustness_case_results.csv"),
        },
    }
    (out_dir / "e5_e7_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_summary(out_dir / "e5_e7_summary.md", summary)
    latest = out_root / "latest_e5_e7_summary.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def write_markdown_summary(path: Path, summary: Dict[str, Any]) -> None:
    lines = [
        "# E5/E6/E7 Provisional Experiment Run",
        "",
        f"- Run ID: {summary['run_id']}",
        f"- Benchmark: `{summary['benchmark_path']}`",
        f"- Benchmark human-reviewed: {bool((summary.get('benchmark_metadata') or {}).get('paper_ready_benchmark'))}",
        f"- Paper-ready: {summary['paper_ready']}",
        f"- Blocker: {summary['paper_ready_blocker']}",
        "",
        "## E5/E6 proxy metrics",
        "",
        "| Variant | Cases | Numeric acc. | Status acc. | Unsupported-answer rate | Hallucinated-ref rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variant, metrics in summary["e5_e6"]["variants"].items():
        lines.append(
            "| {variant} | {case_count} | {numeric_accuracy} | {status_accuracy} | {unsupported_answer_rate} | {hallucinated_reference_rate} |".format(
                variant=variant,
                **metrics,
            )
        )
    lines.extend(
        [
            "",
            "## E7 injection metrics",
            "",
            f"- Injection cases: {summary['e7']['case_count']}",
            f"- Per category: {summary['e7']['per_category']}",
            "",
            "| Variant | Cases | Status acc. | Unsupported-answer rate |",
            "|---|---:|---:|---:|",
        ]
    )
    for variant, metrics in summary["e7"]["variants"].items():
        lines.append(
            "| {variant} | {case_count} | {status_accuracy} | {unsupported_answer_rate} |".format(
                variant=variant,
                **metrics,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary_answer_parts(summary: Dict[str, Any]) -> List[str]:
    parts = []
    for key, value in summary.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(f"{key}={value}")
        elif isinstance(value, str):
            parts.append(value)
    return parts


_OPERATION_ALIASES = {"aggregate": "value"}


def _slot_match(response: Dict[str, Any], expected: Dict[str, Any]) -> Optional[bool]:
    checks = []
    for key in ["perspective", "operation"]:
        if key in expected:
            resp_val = str(response.get(key) or "")
            gold_val = str(expected.get(key) or "")
            if key == "operation":
                resp_val = _OPERATION_ALIASES.get(resp_val, resp_val)
                gold_val = _OPERATION_ALIASES.get(gold_val, gold_val)
            checks.append(resp_val == gold_val)
    return all(checks) if checks else None


def _status_match(response: Dict[str, Any], expected: Dict[str, Any]) -> Optional[bool]:
    if "status" not in expected:
        return None
    return str(response.get("status") or "") == str(expected.get("status") or "")


def _numeric_match(observed_summary: Dict[str, Any], expected: Dict[str, Any]) -> Optional[bool]:
    expected_summary = expected.get("summary")
    if not expected_summary:
        return None
    tolerance = float(expected.get("numeric_tolerance", 1e-4))
    numeric_keys = [
        key
        for key, value in expected_summary.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not numeric_keys:
        return None
    for key in numeric_keys:
        if key not in observed_summary:
            return False
        observed = _to_float(observed_summary.get(key))
        expected_value = _to_float(expected_summary.get(key))
        if observed is None or expected_value is None:
            return False
        if not _numeric_close(observed, expected_value, tolerance):
            return False
    return True


def _numeric_match_keyfree(
    observed_summary: Dict[str, Any], expected: Dict[str, Any]
) -> Optional[bool]:
    """Numeric correctness ignoring which key carried the number.

    Baselines that are not bound to the answer schema report the right figure
    under their own key names, so a key-exact comparison scores them at zero
    even when every number is right. This keeps the strict metric intact and
    measures the numbers alone, so the cross-system gap is not a naming artifact.
    """
    expected_summary = expected.get("summary")
    if not expected_summary:
        return None
    tolerance = float(expected.get("numeric_tolerance", 1e-4))
    expected_values = [
        value
        for value in expected_summary.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not expected_values:
        return None
    observed_values = [
        value
        for value in (observed_summary or {}).values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    for expected_value in expected_values:
        target = _to_float(expected_value)
        if target is None:
            return False
        if not any(
            _numeric_close(candidate, target, tolerance)
            for candidate in (_to_float(item) for item in observed_values)
            if candidate is not None
        ):
            return False
    return True


def _answer_contains(answer: str, expected: Dict[str, Any]) -> Optional[bool]:
    required = expected.get("answer_contains")
    if not required:
        return None
    lowered = (answer or "").casefold()
    return all(str(term).casefold() in lowered for term in required)


def _hallucinated_evidence(
    observed: Sequence[Any],
    expected: Dict[str, Any],
    supported: Sequence[Any] = (),
) -> List[str]:
    allowed = {str(value) for value in expected.get("evidence_ids") or []}
    allowed.update(str(value) for value in supported)
    if not allowed:
        return [str(value) for value in observed if str(value)]
    return [str(value) for value in observed if str(value) not in allowed]


def _allowed_numbers(expected: Dict[str, Any]) -> List[float]:
    values: List[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(expected.get("summary") or {})
    return values


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _numeric_close(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= max(tolerance, abs(b) * tolerance)


def _rate(results: Sequence[Dict[str, Any]], score_key: str) -> Optional[float]:
    scored = [item["scores"].get(score_key) for item in results if item["scores"].get(score_key) is not None]
    if not scored:
        return None
    return round(sum(1 for value in scored if value) / len(scored), 4)


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run provisional E5/E6/E7 experiment infrastructure.")
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_E4_BENCHMARK)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--e7-per-category", type=int, default=15)
    parser.add_argument("--variants", default=",".join(DEFAULT_VARIANTS))
    args = parser.parse_args(argv)
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    summary = run(
        benchmark_path=args.benchmark,
        out_root=args.out_root,
        variants=variants,
        e7_per_category=args.e7_per_category,
    )
    print(json.dumps({
        "run_id": summary["run_id"],
        "output_dir": summary["output_dir"],
        "e5_e6_case_count": summary["e5_e6"]["case_count"],
        "e7_case_count": summary["e7"]["case_count"],
        "paper_ready": summary["paper_ready"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
