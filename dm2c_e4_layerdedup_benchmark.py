"""Deterministic 150-case E4 generator for the canonical layer-deduplicated release.

The frozen V9 file is used only for its ordered case design and human-authored
fields.  Programs, selections, statuses, numeric gold, and evidence are rebuilt
from the supplied canonical-v2 release.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    component_type_ids_for_component,
    components_for_ifc_class,
    dimension_ids,
    lookup_dimension,
)
from dm2c_carbonql import CarbonQLProgram
from dm2c_full_qa_experiment_runner import (
    execute_canonical_query,
    load_full_qa_context,
)
from dm2c_m3_context import CanonicalQueryResult, M3ExecutionContext


NUMERIC_TOLERANCE = 0.0001
EXPECTED_CASE_COUNT = 150
EXPECTED_STATUS_QUOTA = {
    "executable": 90,
    "incomplete_path": 14,
    "empty_result": 16,
    "unresolved_target": 15,
    "clarification_required": 15,
}
_CASE_NUMBER = re.compile(r"^e4_balanced_(\d{3})_")

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def _case_number(case: Mapping[str, Any]) -> int:
    case_id = str(case.get("case_id") or "")
    match = _CASE_NUMBER.match(case_id)
    if match is None:
        raise ValueError(f"invalid E4 case id: {case_id!r}")
    return int(match.group(1))


_OPERATION_ALIASES = {"aggregate": "value"}


def _cell(case: Mapping[str, Any]) -> tuple[str, str, str]:
    expected = case.get("expected")
    if not isinstance(expected, Mapping):
        raise ValueError(f"{case.get('case_id')}: expected must be an object")
    perspective = str(expected.get("perspective") or "")
    operation = _OPERATION_ALIASES.get(
        str(expected.get("operation") or ""),
        str(expected.get("operation") or ""),
    )
    status = str(expected.get("status") or "")
    return perspective, operation, status


def _validate_reviewed_design(cases: Sequence[Mapping[str, Any]]) -> None:
    if len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError(
            f"reviewed V9 must contain {EXPECTED_CASE_COUNT} cases, got {len(cases)}"
        )
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("reviewed V9 case ids must be unique")
    if [_case_number(case) for case in cases] != list(
        range(1, EXPECTED_CASE_COUNT + 1)
    ):
        raise ValueError("reviewed V9 case ids must retain ordered slots 001..150")
    quota = Counter(_cell(case)[2] for case in cases)
    if quota != Counter(EXPECTED_STATUS_QUOTA):
        raise ValueError(f"reviewed V9 status quota changed: {dict(quota)}")
    for case in cases:
        generation = case.get("generation")
        if not isinstance(generation, Mapping):
            raise ValueError(f"{case['case_id']}: generation must be an object")
        matrix_cell = generation.get("matrix_cell")
        if not isinstance(matrix_cell, Mapping):
            raise ValueError(f"{case['case_id']}: matrix_cell must be an object")
        matrix_raw = tuple(
            str(matrix_cell.get(key) or "")
            for key in ("perspective", "operation", "status")
        )
        matrix = (
            matrix_raw[0],
            _OPERATION_ALIASES.get(matrix_raw[1], matrix_raw[1]),
            matrix_raw[2],
        )
        if matrix != _cell(case):
            raise ValueError(
                f"{case['case_id']}: expected cell and generation.matrix_cell differ"
            )


def _props(
    context: M3ExecutionContext, dimension: str, entity_id: str
) -> Mapping[str, Any]:
    value = lookup_dimension(context.canonical, dimension, entity_id).get("props", {})
    return value if isinstance(value, Mapping) else {}


def _entity_name(
    context: M3ExecutionContext, dimension: str, entity_id: str
) -> str:
    props = _props(context, dimension, entity_id)
    value = (
        props.get("name")
        or props.get("activityName")
        or props.get("stageName")
        or props.get("globalId")
        or entity_id
    )
    return str(value)


def _rejected_component_records(
    context: M3ExecutionContext,
) -> tuple[tuple[str, str, str], ...]:
    available = set(dimension_ids(context.canonical, "component"))
    rows = {
        (
            component_id,
            str(record.reason_code or "unspecified_rejection"),
            record.record_id,
        )
        for record in context.validation_coverage.records
        if record.status == "rejected"
        for component_id in record.component_ids
        if component_id in available
    }
    if not rows:
        raise RuntimeError("layerdedup release has no rejected component records")
    return tuple(sorted(rows, key=lambda row: (row[1], row[0], row[2])))


def _clean_component_targets(
    context: M3ExecutionContext, count: int
) -> tuple[str, ...]:
    rejected = {
        component_id
        for component_id, _, _ in _rejected_component_records(context)
    }
    ranked = sorted(
        (
            summary
            for summary in context.component_summaries
            if summary.entity_id not in rejected and summary.value_kgCO2e > 0
        ),
        key=lambda item: (-item.value_kgCO2e, item.entity_id),
    )
    chosen: list[str] = []
    values: list[float] = []
    for summary in ranked:
        if any(math.isclose(summary.value_kgCO2e, value) for value in values):
            continue
        chosen.append(summary.entity_id)
        values.append(summary.value_kgCO2e)
        if len(chosen) == count:
            return tuple(chosen)
    raise RuntimeError(f"layerdedup release has fewer than {count} distinct clean components")


def _clean_material_targets(
    context: M3ExecutionContext, count: int
) -> tuple[str, ...]:
    rejected_materials = {
        str(record.material_id)
        for record in context.validation_coverage.records
        if record.status == "rejected" and record.material_id
    }
    ranked = sorted(
        (
            summary
            for summary in context.material_summaries
            if summary.entity_id not in rejected_materials
            and summary.value_kgCO2e > 0
        ),
        key=lambda item: (-item.value_kgCO2e, item.entity_id),
    )
    if len(ranked) < count:
        raise RuntimeError(f"layerdedup release has fewer than {count} clean materials")
    return tuple(summary.entity_id for summary in ranked[:count])


def _clean_process_target(context: M3ExecutionContext) -> str:
    totals: dict[str, list[float]] = defaultdict(list)
    for projection in context.source_projections:
        raw_process_ids = projection.dimensions.get("process")
        process_ids = (
            tuple(raw_process_ids)
            if isinstance(raw_process_ids, (list, tuple))
            else (raw_process_ids,)
        )
        for process_id in process_ids:
            if process_id and projection.value_kgCO2e > 0:
                totals[str(process_id)].append(projection.value_kgCO2e)
    if not totals:
        raise RuntimeError("layerdedup release has no executable process target")
    return min(
        totals,
        key=lambda process_id: (-math.fsum(totals[process_id]), process_id),
    )


def _ambiguous_component_class(context: M3ExecutionContext) -> str:
    for ifc_class in sorted(dimension_ids(context.canonical, "ifc_class")):
        if len(components_for_ifc_class(context.canonical, ifc_class)) > 1:
            return ifc_class
    raise RuntimeError("layerdedup release has no ambiguous component class selector")


def _projection_dimension_values(
    context: M3ExecutionContext, dimension: str
) -> set[str]:
    values: set[str] = set()
    for projection in context.source_projections:
        raw = projection.dimensions.get(dimension)
        items = tuple(raw) if isinstance(raw, (list, tuple)) else (raw,)
        values.update(str(item) for item in items if item)
    return values


def _zero_row_entity_target(
    context: M3ExecutionContext, dimension: str
) -> str:
    available = set(dimension_ids(context.canonical, dimension))
    observed = _projection_dimension_values(context, dimension)
    if dimension == "material":
        rejected = {
            str(record.material_id)
            for record in context.validation_coverage.records
            if record.status == "rejected" and record.material_id
        }
    elif dimension == "process":
        rejected = {
            str(process_id)
            for record in context.validation_coverage.records
            if record.status == "rejected"
            for process_id in record.process_ids
        }
    else:
        raise ValueError(f"unsupported zero-row dimension: {dimension}")
    candidates = sorted(available - observed - rejected)
    if not candidates:
        raise RuntimeError(
            f"layerdedup release has no clean zero-row {dimension} target"
        )
    return candidates[0]


def _zero_row_component_type(context: M3ExecutionContext) -> str:
    accepted_components = {
        str(projection.component_id)
        for projection in context.projections
        if projection.component_id
    }
    rejected_components = {
        component_id
        for component_id, _, _ in _rejected_component_records(context)
    }
    components_by_type: dict[str, set[str]] = defaultdict(set)
    for component_id in dimension_ids(context.canonical, "component"):
        for type_id in component_type_ids_for_component(
            context.canonical, component_id
        ):
            components_by_type[type_id].add(component_id)
    candidates = sorted(
        type_id
        for type_id, component_ids in components_by_type.items()
        if component_ids
        and component_ids.isdisjoint(accepted_components)
        and component_ids.isdisjoint(rejected_components)
    )
    if not candidates:
        raise RuntimeError(
            "layerdedup release has no real component type with a clean zero-row slice"
        )
    return candidates[0]


def _selected_targets_by_case(
    reviewed: Sequence[Mapping[str, Any]], context: M3ExecutionContext
) -> dict[str, tuple[str, ...]]:
    clean_pair = _clean_component_targets(context, 2)
    module_ids = dimension_ids(context.canonical, "module")
    if len(module_ids) != 1:
        raise RuntimeError(
            f"layerdedup release must expose one ModularUnit, got {len(module_ids)}"
        )
    old_broken_cases: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in reviewed:
        if _cell(case)[2] != "incomplete_path":
            continue
        for old_id in case.get("selected_component_ids", ()):
            old_broken_cases[str(old_id)].append(case)

    rejected_by_old: dict[str, str] = {}
    for old_id, old_cases in sorted(old_broken_cases.items()):
        templates = " ".join(
            str(case.get("question_template") or "") for case in old_cases
        )
        if "U型槽钢" in templates:
            preferred_classes = ("IfcBeam", "IfcBuildingElementProxy", "IfcColumn")
        elif "W2-2A126" in templates:
            preferred_classes = ("IfcWindow", "IfcDoor", "IfcBuildingElementProxy")
        else:
            raise RuntimeError(
                f"no semantic rejected-target rule for reviewed target {old_id!r}"
            )
        rejected_rows = _rejected_component_records(context)
        candidates: list[str] = []
        for desired_ifc_class in preferred_classes:
            candidates = sorted(
                {
                    component_id
                    for component_id, _, _ in rejected_rows
                    if str(
                        _props(context, "component", component_id).get("ifcClass")
                        or ""
                    )
                    == desired_ifc_class
                },
                key=lambda component_id: (
                    -sum(
                        record.status == "rejected"
                        and component_id in record.component_ids
                        for record in context.validation_coverage.records
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

    selected: dict[str, tuple[str, ...]] = {}
    for case in reviewed:
        case_id = str(case["case_id"])
        perspective, operation, status = _cell(case)
        old_ids = tuple(str(value) for value in case.get("selected_component_ids", ()))
        if not old_ids:
            selected[case_id] = ()
        elif status == "incomplete_path":
            selected[case_id] = tuple(rejected_by_old[value] for value in old_ids)
        elif perspective == "traceability":
            selected[case_id] = (module_ids[0],)
        elif perspective == "product" and operation == "compare":
            selected[case_id] = clean_pair
        elif perspective == "product" and operation in {"explain", "trace"}:
            selected[case_id] = (clean_pair[0],)
        else:
            raise RuntimeError(f"{case_id}: no deterministic selection rule")
    return selected


def _source_for(perspective: str) -> str:
    return {
        "product": "all",
        "material": "material",
        "process": "process",
        "traceability": "all",
    }[perspective]


def _group_key_for(perspective: str) -> str:
    return {
        "product": "component",
        "material": "material",
        "process": "process",
        "traceability": "source_kind",
    }[perspective]


def _finish_operation(
    steps: list[dict[str, Any]],
    *,
    operation: str,
    group_key: str,
) -> None:
    if operation in {"value", "aggregate"}:
        steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
    elif operation in {"explain", "trace"}:
        steps.append({"op": "Trace"})
    elif operation in {"rank", "compare"}:
        steps.extend(
            (
                {"op": "GroupBy", "keys": [group_key]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            )
        )
        if operation == "rank":
            steps.append({"op": "Rank", "top_k": 1, "descending": True})
        else:
            steps.append({"op": "Compare"})
    else:
        raise ValueError(f"unsupported E4 operation: {operation!r}")


def reconstruct_program(
    case: Mapping[str, Any], context: M3ExecutionContext
) -> CarbonQLProgram:
    """Reconstruct the typed executor program for one generated or migrated case."""

    if not isinstance(context, M3ExecutionContext):
        raise TypeError("context must be M3ExecutionContext")
    perspective, operation, status = _cell(case)
    if perspective not in {"product", "material", "process", "traceability"}:
        raise ValueError(f"{case.get('case_id')}: unknown perspective {perspective!r}")
    selected_ids = tuple(str(value) for value in case.get("selected_component_ids", ()))
    source = _source_for(perspective)
    group_key = _group_key_for(perspective)
    number = _case_number(case)

    if status == "executable":
        if selected_ids:
            steps: list[dict[str, Any]] = [
                {"op": "SelectClicked", "ids": list(selected_ids)}
            ]
        elif perspective == "material" and operation in {"explain", "trace"}:
            steps = [
                {
                    "op": "ResolveEntities",
                    "entity_type": "material",
                    "ids": [_clean_material_targets(context, 1)[0]],
                }
            ]
        elif perspective == "process" and operation in {"explain", "trace"}:
            steps = [
                {
                    "op": "ResolveEntities",
                    "entity_type": "process",
                    "ids": [_clean_process_target(context)],
                }
            ]
        else:
            steps = [{"op": "SelectProject"}]
        steps.append({"op": "CarbonAtoms", "source": source, "known_total": True})
        if perspective == "process" and operation in {"explain", "trace"}:
            steps.append(
                {
                    "op": "Filter",
                    "field": "process",
                    "equals": str(steps[0]["ids"][0]),
                }
            )
            _finish_operation(steps, operation=operation, group_key=group_key)
        elif perspective == "material" and operation in {"value", "aggregate"}:
            steps.append({"op": "GroupBy", "keys": ["material"]})
            steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
        elif perspective == "process" and operation in {"value", "aggregate"}:
            steps.append({"op": "GroupBy", "keys": ["carrier"]})
            steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
        elif perspective == "material" and operation == "compare":
            material_ids = _clean_material_targets(context, 2)
            steps.append({"op": "Filter", "field": "material", "in": list(material_ids)})
            _finish_operation(steps, operation=operation, group_key=group_key)
        elif perspective == "process" and operation in {"rank", "compare"}:
            # Process rank/compare gold is carrier-keyed (first_carrier_kgCO2e, etc.).
            _finish_operation(steps, operation=operation, group_key="carrier")
        else:
            _finish_operation(steps, operation=operation, group_key=group_key)
    elif status == "incomplete_path":
        if len(selected_ids) != 1:
            raise ValueError(
                f"{case.get('case_id')}: incomplete_path requires one selected target"
            )
        steps = [
            {"op": "SelectClicked", "ids": list(selected_ids)},
            {"op": "CarbonAtoms", "source": "all", "known_total": True},
        ]
        if perspective == "material":
            steps.append({"op": "Filter", "field": "source_kind", "equals": "material"})
        elif perspective == "process":
            steps.append({"op": "Filter", "field": "source_kind", "equals": "process"})
        incomplete_group = (
            "material"
            if perspective == "material"
            else "source_kind"
            if perspective in {"process", "traceability"}
            else "component"
        )
        _finish_operation(
            steps, operation=operation, group_key=incomplete_group
        )
    elif status == "empty_result":
        steps = [
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": source, "known_total": True},
        ]
        if perspective in {"product", "traceability"}:
            steps.append(
                {
                    "op": "Filter",
                    "field": "component_type",
                    "equals": _zero_row_component_type(context),
                }
            )
        elif perspective == "material":
            steps.append(
                {
                    "op": "Filter",
                    "field": "material",
                    "equals": _zero_row_entity_target(context, "material"),
                }
            )
        else:
            steps.append(
                {
                    "op": "Filter",
                    "field": "process",
                    "equals": _zero_row_entity_target(context, "process"),
                }
            )
        _finish_operation(steps, operation=operation, group_key=group_key)
    elif status == "unresolved_target":
        steps = [
            {
                "op": "ResolveEntities",
                "entity_type": "component",
                "ids": [f"E4_DOES_NOT_EXIST_{number:03d}"],
            },
            {"op": "CarbonAtoms", "source": source, "known_total": True},
        ]
        _finish_operation(steps, operation=operation, group_key=group_key)
    elif status == "clarification_required":
        steps = [
            {
                "op": "ResolveEntities",
                "entity_type": "component",
                "property": "ifcClass",
                "value": _ambiguous_component_class(context),
                "cardinality": "singleton",
            },
            {"op": "CarbonAtoms", "source": source, "known_total": True},
        ]
        _finish_operation(steps, operation=operation, group_key=group_key)
    else:
        raise ValueError(f"{case.get('case_id')}: unknown target status {status!r}")
    return CarbonQLProgram.from_dict({"steps": steps, "holes": []})


@dataclass
class _Oracle:
    context: M3ExecutionContext

    def __post_init__(self) -> None:
        self._cache: dict[str, CanonicalQueryResult] = {}

    def run(
        self, program: CarbonQLProgram, selected_ids: Sequence[str] = ()
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


def _simple_program(*steps: Mapping[str, Any]) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": [dict(step) for step in steps], "holes": []})


def _target_evidence(
    oracle: _Oracle, perspective: str, entity_id: str
) -> tuple[str, ...]:
    if perspective == "product":
        selector = {"op": "SelectClicked", "ids": [entity_id]}
        source = "all"
        selected_ids: Sequence[str] = (entity_id,)
    elif perspective == "material":
        selector = {
            "op": "ResolveEntities",
            "entity_type": "material",
            "ids": [entity_id],
        }
        source = "material"
        selected_ids = ()
    elif perspective == "process":
        selector = {
            "op": "ResolveEntities",
            "entity_type": "process",
            "ids": [entity_id],
        }
        source = "process"
        selected_ids = ()
    else:
        raise ValueError(f"unsupported evidence perspective: {perspective}")
    result = oracle.run(
        _simple_program(
            selector,
            {"op": "CarbonAtoms", "source": source, "known_total": True},
            *(
                (
                    {
                        "op": "Filter",
                        "field": "process",
                        "equals": entity_id,
                    },
                )
                if perspective == "process"
                else ()
            ),
            {"op": "Trace"},
        ),
        selected_ids,
    )
    if result.status != "executable":
        raise RuntimeError(
            f"clean {perspective} target {entity_id} is {result.status}, not executable"
        )
    return result.evidence_ids


def _expected_for(
    case: Mapping[str, Any],
    context: M3ExecutionContext,
    oracle: _Oracle,
    result: CanonicalQueryResult,
) -> dict[str, Any]:
    perspective, operation, status = _cell(case)
    expected: dict[str, Any] = {
        "perspective": perspective,
        "operation": operation,
        "status": status,
        "numeric_tolerance": NUMERIC_TOLERANCE,
    }
    rows = [dict(row) for row in result.rows]
    evidence_ids: tuple[str, ...] = ()

    if status == "executable":
        if perspective == "product" and operation in {"value", "aggregate"}:
            material = oracle.run(
                _simple_program(
                    {"op": "SelectProject"},
                    {
                        "op": "CarbonAtoms",
                        "source": "material",
                        "known_total": True,
                    },
                    {"op": "Aggregate", "metric": "sum_kgCO2e"},
                )
            )
            # Grouped by component so the figure is the process carbon the
            # products carry, which is what a product-perspective split reports;
            # ungrouped it would be the whole process account, including the
            # records no product carries, and the split would not add up.
            process = oracle.run(
                _simple_program(
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
            if material.status != "executable" or process.status != "executable":
                raise RuntimeError("project total split is not executable")
            summary = {
                "components": result.summary["available_component_count"],
                "knownTotalCarbon_kgCO2e": result.summary["total_kgCO2e"],
                "totalMaterialCarbon_kgCO2e": material.summary["total_kgCO2e"],
                "knownProcessCarbon_kgCO2e": process.summary["total_kgCO2e"],
            }
        elif perspective == "product" and operation == "rank":
            row = rows[0]
            summary = {
                "top_component_knownTotalCarbon_kgCO2e": row["kgCO2e"]
            }
            evidence_ids = _target_evidence(
                oracle, perspective, str(row["component"])
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
            row = rows[0]
            summary = {"top_material_family_kgCO2e": row["kgCO2e"]}
            evidence_ids = _target_evidence(
                oracle, perspective, str(row["material"])
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
            row = rows[0]
            summary = {"top_process_record_kgCO2e": row["kgCO2e"]}
            # Rank is carrier-grouped; keep the ranked result's own evidence.
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
    elif status == "incomplete_path":
        summary = {"blocked_status": result.summary["blocked_status"]}
        evidence_ids = result.evidence_ids
    elif status == "empty_result":
        summary = {"row_count": len(rows)}
    elif status == "unresolved_target":
        summary = {"target_id": result.summary["target_id"]}
        expected["answer_contains"] = ["unresolved_target"]
    elif status == "clarification_required":
        summary = {"ambiguous_target": result.summary["ambiguous_target"]}
        expected["answer_contains"] = ["clarification"]
    else:
        raise AssertionError(status)
    expected["summary"] = summary
    if evidence_ids or status == "incomplete_path":
        expected["evidence_ids"] = list(evidence_ids)
    return expected


def _question_template(
    old_case: Mapping[str, Any],
    context: M3ExecutionContext,
    selected_ids: Sequence[str],
    result: CanonicalQueryResult,
) -> str:
    perspective, operation, status = _cell(old_case)
    if status != "executable":
        program_case = {
            "case_id": old_case["case_id"],
            "expected": {
                "perspective": perspective,
                "operation": operation,
                "status": status,
            },
            "selected_component_ids": list(selected_ids),
        }
        program = reconstruct_program(program_case, context)
        selector = program.steps[0]
    if status == "clarification_required":
        target = str(result.summary["ambiguous_target"])
        return (
            f"From the {perspective} perspective, {operation} carbon for the "
            f"ambiguous component selector {selector.args['property']}={target}; "
            "ask for clarification."
        )
    if status == "incomplete_path":
        target = selected_ids[0]
        return (
            f"From the {perspective} perspective, {operation} the carbon path for "
            f"{_entity_name(context, 'component', target)} ({target}) and report "
            f"the rejected evidence ({result.summary['blocked_status']})."
        )
    if status == "empty_result":
        filter_step = next(step for step in program.steps if step.op == "Filter")
        field = str(filter_step.args["field"])
        target = str(filter_step.args["equals"])
        return (
            f"From the {perspective} perspective, {operation} the legitimate "
            f"zero-row layerdedup slice {field}={target}."
        )
    if status == "unresolved_target":
        target = str(result.summary["target_id"])
        return (
            f"From the {perspective} perspective, {operation} carbon for the "
            f"missing component id {target}; report unresolved_target."
        )
    if perspective == "product" and selected_ids:
        names = [
            _entity_name(context, "component", entity_id)
            for entity_id in selected_ids
        ]
        if operation == "compare":
            return f"Compare the known carbon of {names[0]} and {names[1]}."
        return (
            f"{operation.capitalize()} the product-level carbon contribution for "
            f"{names[0]} with evidence."
        )
    if perspective == "traceability" and selected_ids:
        module_id = selected_ids[0]
        return (
            f"From the traceability perspective, {operation} the auditable carbon "
            f"and evidence chain for {_entity_name(context, 'module', module_id)} "
            f"({module_id})."
        )
    if perspective == "material" and operation == "compare":
        targets = _clean_material_targets(context, 2)
        names = [_entity_name(context, "material", value) for value in targets]
        return f"Compare material carbon between {names[0]} and {names[1]}."
    return str(old_case.get("question_template") or "")


def build_generated_cases(
    release_dir: Path, reviewed_v9: Path
) -> list[dict[str, Any]]:
    """Rebuild all deterministic case fields against one canonical release."""

    release_dir = Path(release_dir)
    reviewed_v9 = Path(reviewed_v9)
    reviewed = _read_jsonl(reviewed_v9)
    _validate_reviewed_design(reviewed)
    context = load_full_qa_context(release_dir, allow_synthetic=True)
    selected_by_case = _selected_targets_by_case(reviewed, context)
    oracle = _Oracle(context)
    generated: list[dict[str, Any]] = []
    for old_case in reviewed:
        case_id = str(old_case["case_id"])
        perspective, operation, target_status = _cell(old_case)
        selected_ids = selected_by_case[case_id]
        working = {
            "case_id": case_id,
            "expected": {
                "perspective": perspective,
                "operation": operation,
                "status": target_status,
            },
            "selected_component_ids": list(selected_ids),
        }
        program = reconstruct_program(working, context)
        result = oracle.run(program, selected_ids)
        if result.status != target_status:
            raise RuntimeError(
                f"{case_id}: executor produced {result.status!r}, "
                f"expected cell requires {target_status!r}; "
                f"coverage={result.coverage_status!r}"
            )

        case = deepcopy(dict(old_case))
        case["question_template"] = _question_template(
            old_case, context, selected_ids, result
        )
        case["expected"] = _expected_for(working, context, oracle, result)
        case["selected_component_ids"] = list(selected_ids)
        case["scope"] = "selected_component" if selected_ids else "project"
        case["generation"] = {
            "source": "kg_programmatic",
            "kg_dir": str(release_dir),
            "reviewed_v9": str(reviewed_v9),
            "matrix_cell": {
                "perspective": perspective,
                "operation": operation,
                "status": target_status,
            },
        }
        generated.append(case)
    return generated


def write_generated_cases(
    cases: Sequence[Mapping[str, Any]], out_path: Path
) -> Path:
    """Write deterministic UTF-8 JSONL and return the destination path."""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(
            dict(case),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for case in cases
    )
    out_path.write_text(payload, encoding="utf-8", newline="")
    return out_path


def selection_remap(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return an auditable old-to-new target mapping with deterministic reasons."""

    reviewed_paths = {
        str((case.get("generation") or {}).get("reviewed_v9") or "")
        for case in cases
    }
    if len(reviewed_paths) != 1 or not next(iter(reviewed_paths)):
        raise ValueError("cases must identify exactly one generation.reviewed_v9")
    reviewed_path = Path(next(iter(reviewed_paths)))
    reviewed = _read_jsonl(reviewed_path)
    reviewed_by_id = {str(case["case_id"]): case for case in reviewed}
    generated_ids = [str(case.get("case_id") or "") for case in cases]
    if set(generated_ids) != set(reviewed_by_id):
        raise ValueError("generated and reviewed case ids differ")
    release_paths = {
        str((case.get("generation") or {}).get("kg_dir") or "")
        for case in cases
    }
    if len(release_paths) != 1 or not next(iter(release_paths)):
        raise ValueError("cases must identify exactly one generation.kg_dir")
    context = load_full_qa_context(
        Path(next(iter(release_paths))), allow_synthetic=True
    )

    case_remaps: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or "")
        old_ids = tuple(
            str(value)
            for value in reviewed_by_id[case_id].get(
                "selected_component_ids", ()
            )
        )
        new_ids = tuple(str(value) for value in case.get("selected_component_ids", ()))
        if old_ids == new_ids:
            continue
        perspective, operation, status = _cell(case)
        if not old_ids or not new_ids:
            raise ValueError(
                f"{case_id}: selected-target coverage changed instead of being remapped"
            )
        expected = case.get("expected") or {}
        executor_evidence = tuple(
            str(value) for value in expected.get("evidence_ids", ())
        )
        rejected_records = tuple(
            record
            for record in context.validation_coverage.records
            if record.status == "rejected"
            and set(new_ids).intersection(record.component_ids)
        )
        if status == "incomplete_path":
            blocked_status = str(
                expected
                .get("summary", {})
                .get("blocked_status", "unspecified_rejection")
            )
            rejection_record_ids = tuple(
                sorted(record.record_id for record in rejected_records)
            )
            rejection_evidence_ids = tuple(
                sorted(
                    {
                        record.evidence_source_id
                        for record in rejected_records
                        if record.evidence_source_id
                    }
                )
            )
            basis_text = (
                f"rejected records {list(rejection_record_ids)} with reason "
                f"{blocked_status} and rejection evidence {list(rejection_evidence_ids)}"
            )
        elif perspective == "traceability":
            basis_text = (
                f"executor trace evidence {list(executor_evidence)} for the real "
                "ModularUnit"
            )
        elif operation == "compare":
            basis_text = (
                f"executor compare evidence {list(executor_evidence)} and totals "
                f"{expected.get('summary', {})}"
            )
        else:
            basis_text = (
                f"executor known-total evidence {list(executor_evidence)} and "
                f"summary {expected.get('summary', {})}"
            )
        reason = (
            f"Reviewed-v9 target(s) {list(old_ids)} -> layerdedup target(s) "
            f"{list(new_ids)}; evidence basis: {basis_text}."
        )
        case_remaps.append(
            {
                "case_id": case_id,
                "perspective": perspective,
                "operation": operation,
                "status": status,
                "old_ids": list(old_ids),
                "new_ids": list(new_ids),
                "reason": reason,
                "basis": {
                    "executor_evidence_ids": list(executor_evidence),
                    "rejection_record_ids": [
                        record.record_id for record in rejected_records
                    ],
                    "rejection_reason_codes": sorted(
                        {
                            str(record.reason_code or "unspecified_rejection")
                            for record in rejected_records
                        }
                    ),
                },
            }
        )

    grouped: dict[tuple[tuple[str, ...], tuple[str, ...], str], list[str]] = defaultdict(list)
    for item in case_remaps:
        key = (
            tuple(item["old_ids"]),
            tuple(item["new_ids"]),
            str(item["reason"]),
        )
        grouped[key].append(str(item["case_id"]))
    target_remaps = [
        {
            "old_ids": list(old_ids),
            "new_ids": list(new_ids),
            "case_ids": case_ids,
            "reason": reason,
        }
        for (old_ids, new_ids, reason), case_ids in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1])
        )
    ]
    return {
        "schema_version": "e4-layerdedup-selection-remap-v1",
        "changed_case_count": len(case_remaps),
        "changed_target_count": len(target_remaps),
        "case_remaps": case_remaps,
        "target_remaps": target_remaps,
    }


__all__ = [
    "build_generated_cases",
    "reconstruct_program",
    "selection_remap",
    "write_generated_cases",
]
