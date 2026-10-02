"""Controlled allocation-plan sensitivity over fresh canonical-v2 releases."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import dimension_ids
from dm2c_m3_context import M3ExecutionContext, load_m3_execution_context
from dm2c_m3_release import canonical_release_digest, publish_derived_release


@dataclass(frozen=True, slots=True)
class AllocationPlan:
    basis: str
    weights: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        basis = str(self.basis or "").strip()
        rows = tuple((str(component), float(weight)) for component, weight in self.weights)
        if not basis:
            raise ValueError("allocation basis must be explicit")
        if not rows or len({component for component, _ in rows}) != len(rows):
            raise ValueError("allocation targets must be non-empty and unique")
        if any(not component or not math.isfinite(weight) or weight < 0 for component, weight in rows):
            raise ValueError("allocation weights must be finite and non-negative")
        if not math.isclose(math.fsum(weight for _, weight in rows), 1.0, abs_tol=1e-9):
            raise ValueError("normalized allocation weights must sum to one")
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "weights", rows)


@dataclass(frozen=True, slots=True)
class AllocationVariantResult:
    basis: str
    release_dir: Path
    emission_id: str
    consumption_id: str
    quantity_value: float
    factor_value: float
    source_value_kgCO2e: float
    source_process_total_kgCO2e: float
    component_totals: tuple[tuple[str, float], ...]
    module_ids: tuple[str, ...]
    materials: tuple[tuple[str, str], ...]
    source_release_digest: str


def _unit_for_basis(basis: str) -> str:
    lowered = basis.casefold()
    if lowered == "mass":
        return "kg"
    if lowered in {"duration", "machine_hours"}:
        return "h"
    return "1"


def _edge_transform(
    consumption_id: str, plan: AllocationPlan
):
    weights = dict(plan.weights)

    def transform(edge: Mapping[str, Any]) -> Mapping[str, Any]:
        if edge["src"] != consumption_id or edge["type"] != "recordedForObject":
            return edge
        target = str(edge["tgt"])
        if target not in weights:
            raise ValueError(f"allocation plan omits source target {target!r}")
        payload = {
            "id": edge["id"],
            "src": edge["src"],
            "type": edge["type"],
            "tgt": edge["tgt"],
            "occurrenceId": edge["occurrenceId"],
            "props": {
                "attributionMode": "allocated",
                "allocationSetId": f"allocation:set:{plan.basis}",
                "allocationBasis": plan.basis,
                "rawWeight": weights[target],
                "rawWeightUnit": _unit_for_basis(plan.basis),
                "normalizedWeight": weights[target],
                "evidenceRecordId": f"allocation:evidence:{plan.basis}:{target}",
            },
        }
        return payload

    return transform


def _node_transform(consumption_id: str, plan: AllocationPlan):
    def transform(node: Mapping[str, Any]) -> Mapping[str, Any]:
        if node["id"] != consumption_id:
            return node
        props = dict(node["props"])
        props["allocationSetId"] = f"allocation:set:{plan.basis}"
        props["allocationBasis"] = plan.basis
        return {"id": node["id"], "labels": node["labels"], "props": props}

    return transform


def build_allocation_variants(
    context: M3ExecutionContext,
    *,
    output_root: Path,
    plans: Sequence[AllocationPlan],
) -> tuple[AllocationVariantResult, ...]:
    if not context.controlled_fixture:
        raise ValueError("allocation sensitivity requires a controlled fixture")
    allocated = [fact for fact in context.canonical.emissions if fact.mode == "allocated"]
    if len(allocated) != 1:
        raise ValueError("controlled sensitivity requires exactly one allocated source")
    source = allocated[0]
    source_targets = {
        row.component_id
        for row in context.canonical.product_contributions
        if row.emission_id == source.emission_id
    }
    source_digest = canonical_release_digest(context.canonical.release_dir)
    plan_rows = tuple(plans)
    if len({plan.basis for plan in plan_rows}) != len(plan_rows):
        raise ValueError("allocation bases must be unique")
    for plan in plan_rows:
        if {component for component, _ in plan.weights} != source_targets:
            raise ValueError("every plan must preserve the exact source target set")
    results: list[AllocationVariantResult] = []
    for plan in plan_rows:
        release = publish_derived_release(
            context,
            output_root=output_root,
            release_id=f"controlled-v2-allocation-{plan.basis}",
            edge_transform=_edge_transform(source.consumption_id, plan),
            node_transform=_node_transform(source.consumption_id, plan),
        )
        loaded = load_m3_execution_context(release)
        variant_source = next(
            fact for fact in loaded.canonical.emissions if fact.emission_id == source.emission_id
        )
        component_totals = tuple(
            (row.entity_id, row.value_kgCO2e) for row in loaded.component_summaries
        )
        module_ids = dimension_ids(loaded.canonical, "module")
        materials = tuple((row.entity_id, row.name) for row in loaded.material_summaries)
        results.append(
            AllocationVariantResult(
                basis=plan.basis,
                release_dir=release,
                emission_id=variant_source.emission_id,
                consumption_id=variant_source.consumption_id,
                quantity_value=variant_source.quantity_value,
                factor_value=variant_source.factor_value,
                source_value_kgCO2e=variant_source.emission_value,
                source_process_total_kgCO2e=float(
                    loaded.project_summary["source_process_kgCO2e"]
                ),
                component_totals=component_totals,
                module_ids=module_ids,
                materials=materials,
                source_release_digest=source_digest,
            )
        )
    if canonical_release_digest(context.canonical.release_dir) != source_digest:
        raise RuntimeError("source release changed during allocation sensitivity")
    return tuple(results)


def compare_component_allocations(
    variants: Sequence[AllocationVariantResult],
) -> dict[str, Any]:
    rows = tuple(variants)
    if not rows:
        raise ValueError("at least one allocation variant is required")
    source_values = {row.source_value_kgCO2e for row in rows}
    process_values = {row.source_process_total_kgCO2e for row in rows}
    module_sets = {row.module_ids for row in rows}
    material_sets = {row.materials for row in rows}
    if len(source_values) != 1 or len(process_values) != 1:
        raise ValueError("allocation variants do not conserve their source account")
    if len(module_sets) != 1 or len(material_sets) != 1:
        raise ValueError("allocation variants changed stable contextual identities")
    return {
        "allocation_source_value_kgCO2e": next(iter(source_values)),
        "source_process_total_kgCO2e": next(iter(process_values)),
        "module_ids": list(next(iter(module_sets))),
        "materials": [
            {"material_id": material_id, "name": name}
            for material_id, name in next(iter(material_sets))
        ],
        "component_totals_by_basis": {
            row.basis: dict(row.component_totals) for row in rows
        },
    }


__all__ = [
    "AllocationPlan",
    "AllocationVariantResult",
    "build_allocation_variants",
    "compare_component_allocations",
]
