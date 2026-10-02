#!/usr/bin/env python3
"""Independent replay oracle for the 150-case E4 layerdedup benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import dimension_ids, lookup_dimension
from dm2c_carbonql import CarbonQLProgram
from dm2c_e4_layerdedup_benchmark import reconstruct_program
from dm2c_full_qa_experiment_runner import (
    execute_canonical_query,
    load_full_qa_context,
)
from dm2c_m3_context import CanonicalQueryResult, M3ExecutionContext


NUMERIC_TOLERANCE = 0.0001
EXPECTED_CASE_COUNT = 150
LAYERDEDUP_RELEASE_DIR = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_layerdedup"
)
TYPEA1_EN_RELEASE_DIR = Path(
    "outputs/research_experiments/m2_typea1_full_en_layerdedup_20260731"
)
ALLOWED_RELEASE_DIRS = (LAYERDEDUP_RELEASE_DIR, TYPEA1_EN_RELEASE_DIR)
IMMUTABLE_REVIEWED_V9 = Path(
    "outputs/research_experiments/e4_benchmark/"
    "frozen_20260712_145321_v9_human_pass/"
    "e4_balanced_benchmark_150_human_reviewed_v9.jsonl"
)


class OracleMismatchError(RuntimeError):
    """Raised when a replay report is not safe to publish or freeze."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(row)
    return rows


_OPERATION_ALIASES = {"aggregate": "value"}


def _reviewed_cell(case: Mapping[str, Any]) -> dict[str, str]:
    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case.get('case_id')}: expected must be an object")
    compact = {
        key: str(expected.get(key) or "")
        for key in ("perspective", "operation", "status")
    }
    compact["operation"] = _OPERATION_ALIASES.get(
        compact["operation"], compact["operation"]
    )
    if not all(compact.values()):
        raise ValueError(f"{case.get('case_id')}: reviewed cell is incomplete")
    return compact


def _selected_ids(case: Mapping[str, Any]) -> tuple[str, ...]:
    values = case.get("selected_component_ids", ())
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            f"{case.get('case_id')}: selected_component_ids must be an array"
        )
    return tuple(str(value) for value in values)


def _oracle_case(
    case_id: str,
    cell: Mapping[str, str],
    selected_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "expected": dict(cell),
        "selected_component_ids": list(selected_ids),
    }


def _validate_candidate_provenance(cases: Sequence[Mapping[str, Any]]) -> None:
    for case in cases:
        generation = case.get("generation")
        if not isinstance(generation, Mapping):
            raise ValueError(f"{case.get('case_id')}: generation must be an object")
        reviewed_v9 = str(generation.get("reviewed_v9") or "")
        if not reviewed_v9:
            raise ValueError(
                f"{case.get('case_id')}: generation.reviewed_v9 is required"
            )
        if Path(reviewed_v9).resolve() != IMMUTABLE_REVIEWED_V9.resolve():
            raise ValueError(
                f"{case.get('case_id')}: untrusted reviewed_v9 provenance"
            )


def _rejected_component_records(
    context: M3ExecutionContext,
) -> tuple[tuple[str, str], ...]:
    available = set(dimension_ids(context.canonical, "component"))
    return tuple(
        sorted(
            {
                (component_id, record.record_id)
                for record in context.validation_coverage.records
                if record.status == "rejected"
                for component_id in record.component_ids
                if component_id in available
            }
        )
    )


def _clean_component_pair(context: M3ExecutionContext) -> tuple[str, str]:
    rejected = {
        component_id
        for component_id, _ in _rejected_component_records(context)
    }
    ranked = sorted(
        (
            summary
            for summary in context.component_summaries
            if summary.entity_id not in rejected and summary.value_kgCO2e > 0
        ),
        key=lambda summary: (-summary.value_kgCO2e, summary.entity_id),
    )
    chosen: list[str] = []
    values: list[float] = []
    for summary in ranked:
        if any(math.isclose(summary.value_kgCO2e, value) for value in values):
            continue
        chosen.append(summary.entity_id)
        values.append(summary.value_kgCO2e)
        if len(chosen) == 2:
            return chosen[0], chosen[1]
    raise RuntimeError("layerdedup release has fewer than two clean components")


def _immutable_selected_targets(
    reviewed_cases: Sequence[Mapping[str, Any]],
    context: M3ExecutionContext,
) -> dict[str, tuple[str, ...]]:
    """Derive case targets from frozen design and release facts, not candidate data."""

    clean_pair = _clean_component_pair(context)
    module_ids = dimension_ids(context.canonical, "module")
    if len(module_ids) != 1:
        raise RuntimeError(
            f"layerdedup release must expose one ModularUnit, got {len(module_ids)}"
        )

    rejected_records = _rejected_component_records(context)
    rejected_ids = {component_id for component_id, _ in rejected_records}
    old_broken_cases: dict[str, list[Mapping[str, Any]]] = {}
    for case in reviewed_cases:
        if _reviewed_cell(case)["status"] != "incomplete_path":
            continue
        for old_id in _selected_ids(case):
            old_broken_cases.setdefault(old_id, []).append(case)

    rejected_by_old: dict[str, str] = {}
    for old_id, cases in sorted(old_broken_cases.items()):
        templates = " ".join(
            str(case.get("question_template") or "") for case in cases
        )
        if "U型槽钢" in templates:
            preferred_classes = ("IfcBeam", "IfcBuildingElementProxy", "IfcColumn")
        elif "W2-2A126" in templates:
            preferred_classes = ("IfcWindow", "IfcDoor", "IfcBuildingElementProxy")
        else:
            raise RuntimeError(
                f"no immutable rejected-target rule for {old_id!r}"
            )
        candidates: list[str] = []
        for desired_ifc_class in preferred_classes:
            candidates = sorted(
                (
                    component_id
                    for component_id in rejected_ids
                    if str(
                        (
                            lookup_dimension(
                                context.canonical,
                                "component",
                                component_id,
                            ).get("props", {})
                            or {}
                        ).get("ifcClass")
                        or ""
                    )
                    == desired_ifc_class
                ),
                key=lambda component_id: (
                    -sum(
                        component_id == rejected_component_id
                        for rejected_component_id, _ in rejected_records
                    ),
                    component_id,
                ),
            )
            if candidates:
                break
        if not candidates:
            raise RuntimeError(
                f"no rejected replacement in {preferred_classes} for {old_id!r}"
            )
        rejected_by_old[old_id] = candidates[0]

    targets: dict[str, tuple[str, ...]] = {}
    for case in reviewed_cases:
        case_id = str(case.get("case_id") or "")
        cell = _reviewed_cell(case)
        old_ids = _selected_ids(case)
        if not old_ids:
            targets[case_id] = ()
        elif cell["status"] == "incomplete_path":
            targets[case_id] = tuple(
                rejected_by_old[old_id] for old_id in old_ids
            )
        elif cell["perspective"] == "traceability":
            targets[case_id] = (module_ids[0],)
        elif (
            cell["perspective"] == "product"
            and cell["operation"] == "compare"
        ):
            targets[case_id] = clean_pair
        elif (
            cell["perspective"] == "product"
            and cell["operation"] in {"explain", "trace"}
        ):
            targets[case_id] = (clean_pair[0],)
        else:
            raise RuntimeError(f"{case_id}: no immutable selection rule")
    return targets


def _program(*steps: Mapping[str, Any]) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict(
        {"steps": [dict(step) for step in steps], "holes": []}
    )


@dataclass
class _Executor:
    context: M3ExecutionContext

    def __post_init__(self) -> None:
        self._cache: dict[str, CanonicalQueryResult] = {}

    def run(
        self,
        program: CarbonQLProgram,
        selected_ids: Sequence[str] = (),
    ) -> CanonicalQueryResult:
        key = json.dumps(
            [program.to_dict(), list(selected_ids)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if key not in self._cache:
            self._cache[key] = execute_canonical_query(
                self.context,
                program,
                selected_component_ids=tuple(selected_ids),
            )
        return self._cache[key]


def _trace_evidence(
    executor: _Executor,
    perspective: str,
    entity_id: str,
) -> tuple[str, ...]:
    if perspective == "product":
        selector = {"op": "SelectClicked", "ids": [entity_id]}
        source = "all"
        selected_ids: Sequence[str] = (entity_id,)
        filters: tuple[Mapping[str, Any], ...] = ()
    elif perspective == "material":
        selector = {
            "op": "ResolveEntities",
            "entity_type": "material",
            "ids": [entity_id],
        }
        source = "material"
        selected_ids = ()
        filters = ()
    elif perspective == "process":
        selector = {
            "op": "ResolveEntities",
            "entity_type": "process",
            "ids": [entity_id],
        }
        source = "process"
        selected_ids = ()
        filters = (
            {"op": "Filter", "field": "process", "equals": entity_id},
        )
    else:
        raise ValueError(f"unsupported trace perspective: {perspective!r}")
    result = executor.run(
        _program(
            selector,
            {"op": "CarbonAtoms", "source": source, "known_total": True},
            *filters,
            {"op": "Trace"},
        ),
        selected_ids,
    )
    return result.evidence_ids


def _executable_observation(
    executor: _Executor,
    result: CanonicalQueryResult,
    perspective: str,
    operation: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    rows = [dict(row) for row in result.rows]
    evidence_ids: tuple[str, ...] = ()
    if perspective == "product" and operation in {"value", "aggregate"}:
        material = executor.run(
            _program(
                {"op": "SelectProject"},
                {
                    "op": "CarbonAtoms",
                    "source": "material",
                    "known_total": True,
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            )
        )
        # Grouped by component: a product-perspective split reports the process
        # carbon the products carry, not the whole process account.
        process = executor.run(
            _program(
                {"op": "SelectProject"},
                {
                    "op": "CarbonAtoms",
                    "source": "process",
                    "known_total": True,
                },
                {"op": "GroupBy", "keys": ["component"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            )
        )
        summary = {
            "components": result.summary["available_component_count"],
            "knownTotalCarbon_kgCO2e": result.summary["total_kgCO2e"],
            "totalMaterialCarbon_kgCO2e": material.summary["total_kgCO2e"],
            "knownProcessCarbon_kgCO2e": process.summary["total_kgCO2e"],
        }
    elif perspective == "product" and operation == "rank":
        summary = {
            "top_component_knownTotalCarbon_kgCO2e": rows[0]["kgCO2e"]
        }
        evidence_ids = _trace_evidence(
            executor, perspective, str(rows[0]["component"])
        )
    elif perspective == "product" and operation == "compare":
        first, second = rows[:2]
        summary = {
            "first_component_kgCO2e": first["kgCO2e"],
            "second_component_kgCO2e": second["kgCO2e"],
            "difference_kgCO2e": first["kgCO2e"] - second["kgCO2e"],
        }
        evidence_ids = result.evidence_ids
    elif perspective == "product":
        summary = {
            "component_knownTotalCarbon_kgCO2e": result.summary[
                "total_kgCO2e"
            ]
        }
        evidence_ids = result.evidence_ids
    elif perspective == "material" and operation in {"value", "aggregate"}:
        summary = {
            "totalMaterialCarbon_kgCO2e": result.summary["total_kgCO2e"],
            "material_families": len(rows),
        }
    elif perspective == "material" and operation == "rank":
        summary = {"top_material_family_kgCO2e": rows[0]["kgCO2e"]}
        evidence_ids = _trace_evidence(
            executor, perspective, str(rows[0]["material"])
        )
    elif perspective == "material" and operation == "compare":
        first, second = rows[:2]
        summary = {
            "first_material_family_kgCO2e": first["kgCO2e"],
            "second_material_family_kgCO2e": second["kgCO2e"],
            "difference_kgCO2e": first["kgCO2e"] - second["kgCO2e"],
        }
    elif perspective == "material":
        summary = {"material_record_kgCO2e": result.summary["total_kgCO2e"]}
        evidence_ids = result.evidence_ids
    elif perspective == "process" and operation in {"value", "aggregate"}:
        summary = {
            "knownProcessCarbon_kgCO2e": result.summary["total_kgCO2e"],
            "process_energy_records": len(result.emission_ids),
        }
        evidence_ids = result.evidence_ids
    elif perspective == "process" and operation == "rank":
        summary = {"top_process_record_kgCO2e": rows[0]["kgCO2e"]}
        evidence_ids = result.evidence_ids
    elif perspective == "process" and operation == "compare":
        first, second = rows[:2]
        summary = {
            "first_carrier_kgCO2e": first["kgCO2e"],
            "second_carrier_kgCO2e": second["kgCO2e"],
            "difference_kgCO2e": first["kgCO2e"] - second["kgCO2e"],
        }
    elif perspective == "process":
        summary = {"process_record_kgCO2e": result.summary["total_kgCO2e"]}
        evidence_ids = result.evidence_ids
    else:
        summary = {
            "traced_atomic_emission_kgCO2e": result.summary["total_kgCO2e"],
            "evidence_hops": len(result.evidence_ids),
        }
        evidence_ids = result.evidence_ids
    return summary, evidence_ids


def _result_observation(
    executor: _Executor,
    result: CanonicalQueryResult,
    perspective: str,
    operation: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if result.status == "executable":
        return _executable_observation(
            executor, result, perspective, operation
        )
    if result.status == "incomplete_path":
        return (
            {"blocked_status": result.summary.get("blocked_status")},
            result.evidence_ids,
        )
    if result.status == "empty_result":
        return ({"row_count": len(result.rows)}, ())
    if result.status == "unresolved_target":
        return ({"target_id": result.summary.get("target_id")}, ())
    if result.status == "clarification_required":
        return (
            {"ambiguous_target": result.summary.get("ambiguous_target")},
            (),
        )
    return (dict(result.summary), result.evidence_ids)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _value_mismatches(
    expected: Any,
    observed: Any,
    path: str,
) -> list[str]:
    if _numeric(expected) and _numeric(observed):
        if not (
            math.isfinite(float(expected))
            and math.isfinite(float(observed))
            and math.isclose(
                float(expected),
                float(observed),
                rel_tol=0.0,
                abs_tol=NUMERIC_TOLERANCE,
            )
        ):
            return [path]
        return []
    if isinstance(expected, Mapping) and isinstance(observed, Mapping):
        mismatches: list[str] = []
        expected_keys = set(expected)
        observed_keys = set(observed)
        for key in sorted(expected_keys | observed_keys):
            child = f"{path}.{key}"
            if key not in expected or key not in observed:
                mismatches.append(child)
            else:
                mismatches.extend(
                    _value_mismatches(expected[key], observed[key], child)
                )
        return mismatches
    return [] if expected == observed else [path]


def _candidate_compact(case: Mapping[str, Any]) -> dict[str, Any]:
    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case.get('case_id')}: expected must be an object")
    summary = expected.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError(f"{case.get('case_id')}: expected.summary must be an object")
    evidence_ids = expected.get("evidence_ids", ())
    if not isinstance(evidence_ids, (list, tuple)):
        raise ValueError(f"{case.get('case_id')}: expected.evidence_ids must be an array")
    return {
        "perspective": str(expected.get("perspective") or ""),
        "operation": str(expected.get("operation") or ""),
        "status": str(expected.get("status") or ""),
        "summary": dict(summary),
        "evidence_ids": [str(value) for value in evidence_ids],
        "selected_target_ids": list(_selected_ids(case)),
        "numeric_tolerance": expected.get("numeric_tolerance"),
    }


def _program_selected_targets(
    program: CarbonQLProgram,
    external_ids: Sequence[str],
) -> list[str]:
    if not external_ids:
        return []
    selector = program.steps[0]
    if selector.op != "SelectClicked":
        return []
    raw_ids = selector.args.get("ids", ())
    return [str(value) for value in raw_ids]


def audit_benchmark(
    release_dir: Path,
    benchmark_path: Path,
) -> dict[str, Any]:
    """Re-execute and compact-compare every candidate case against layerdedup."""

    release_dir = Path(release_dir)
    benchmark_path = Path(benchmark_path)
    allowed = {path.resolve() for path in ALLOWED_RELEASE_DIRS}
    if release_dir.resolve() not in allowed:
        raise ValueError(
            "the E4 replay gate accepts only one of: "
            + ", ".join(str(path) for path in ALLOWED_RELEASE_DIRS)
        )
    cases = _read_jsonl(benchmark_path)
    if len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError(
            f"benchmark must contain {EXPECTED_CASE_COUNT} cases, got {len(cases)}"
        )
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if any(not case_id for case_id in case_ids):
        raise ValueError("every benchmark case must have a case_id")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("benchmark case_id values must be unique")
    _validate_candidate_provenance(cases)

    context = load_full_qa_context(release_dir, allow_synthetic=True)
    reviewed_cases = _read_jsonl(IMMUTABLE_REVIEWED_V9)
    reviewed_ids = [
        str(case.get("case_id") or "") for case in reviewed_cases
    ]
    if case_ids != reviewed_ids:
        raise ValueError(
            "benchmark case IDs/order differ from immutable reviewed V9"
        )
    reviewed_by_id = {
        str(case["case_id"]): case for case in reviewed_cases
    }
    selected_targets = _immutable_selected_targets(reviewed_cases, context)
    executor = _Executor(context)
    observations: list[dict[str, Any]] = []
    mismatch_ids: list[str] = []
    for case in cases:
        case_id = str(case["case_id"])
        cell = _reviewed_cell(reviewed_by_id[case_id])
        oracle_selected_ids = selected_targets[case_id]
        program = reconstruct_program(
            _oracle_case(case_id, cell, oracle_selected_ids),
            context,
        )
        result = executor.run(program, oracle_selected_ids)
        summary, evidence_ids = _result_observation(
            executor,
            result,
            cell["perspective"],
            cell["operation"],
        )
        expected = _candidate_compact(case)
        observed = {
            "perspective": cell["perspective"],
            "operation": cell["operation"],
            "status": result.status,
            "summary": summary,
            "evidence_ids": list(evidence_ids),
            "selected_target_ids": _program_selected_targets(
                program, oracle_selected_ids
            ),
            "numeric_tolerance": NUMERIC_TOLERANCE,
        }
        mismatch_fields: list[str] = []
        for field in (
            "perspective",
            "operation",
            "status",
            "summary",
            "evidence_ids",
            "selected_target_ids",
        ):
            mismatch_fields.extend(
                _value_mismatches(
                    expected[field],
                    observed[field],
                    field,
                )
            )
        if expected["numeric_tolerance"] != NUMERIC_TOLERANCE:
            mismatch_fields.append("numeric_tolerance")
        mismatch_fields = sorted(set(mismatch_fields))
        if mismatch_fields:
            mismatch_ids.append(case_id)
        observations.append(
            {
                "case_id": case_id,
                "expected": expected,
                "observed": observed,
                "mismatch_fields": mismatch_fields,
            }
        )

    return {
        "schema_version": "e4-layerdedup-replay-gate-v1",
        "release_dir": str(release_dir),
        "benchmark_path": str(benchmark_path),
        "case_count": len(cases),
        "numeric_tolerance": NUMERIC_TOLERANCE,
        "oracle_mismatch_count": len(mismatch_ids),
        "oracle_mismatch_case_ids": mismatch_ids,
        "case_observations": observations,
    }


def require_clean_audit(report: Mapping[str, Any]) -> None:
    """Block publication or freeze when an audit contains any mismatch."""

    mismatch_count = int(report.get("oracle_mismatch_count") or 0)
    if mismatch_count:
        case_ids = [
            str(value)
            for value in report.get("oracle_mismatch_case_ids", ())
        ]
        raise OracleMismatchError(
            "E4 layerdedup oracle mismatch blocks publication/freeze: "
            f"{mismatch_count} case(s): {', '.join(case_ids)}"
        )


def require_clean_benchmark(
    release_dir: Path,
    benchmark_path: Path,
) -> dict[str, Any]:
    """Audit a benchmark and raise unless all 150 cases replay cleanly."""

    report = audit_benchmark(release_dir, benchmark_path)
    require_clean_audit(report)
    return report


def write_audit_report(
    report: Mapping[str, Any],
    report_path: Path,
) -> Path:
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            dict(report),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay-audit the E4 150-question layerdedup benchmark."
    )
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit_benchmark(args.release_dir, args.benchmark)
    write_audit_report(report, args.report)
    print(args.report)
    require_clean_audit(report)
    return 0


__all__ = [
    "NUMERIC_TOLERANCE",
    "OracleMismatchError",
    "audit_benchmark",
    "require_clean_audit",
    "require_clean_benchmark",
    "write_audit_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
