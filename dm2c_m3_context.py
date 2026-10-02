"""Immutable M3 execution records derived from one canonical-v2 release."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    EmissionFact,
    ProductContribution,
    component_type_ids_for_component,
    dimension_ids,
    evidence_ids,
    iter_emissions,
    iter_product_contributions,
    load_canonical_v2_context,
    lookup_carrier,
    lookup_component,
    lookup_dimension,
    lookup_material,
    lookup_process,
)


def _properties(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("props", {})
    return value if isinstance(value, Mapping) else MappingProxyType({})


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_deep_freeze(item) for item in value), key=repr))
    return value


def immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _deep_freeze(value)


@dataclass(frozen=True, slots=True)
class ValidationCoverageRecord:
    record_id: str
    status: str
    reason_code: str | None
    kind: str
    source_record_id: str
    evidence_source_id: str
    emission_id: str | None
    component_ids: tuple[str, ...]
    material_id: str | None
    carrier_id: str | None
    process_ids: tuple[str, ...]
    recorded_scope: str | None
    requested_scope: str | None


@dataclass(frozen=True, slots=True)
class ValidationCoverageIndex:
    records: tuple[ValidationCoverageRecord, ...]
    accepted_count: int
    rejected_count: int

    def relevant_rejections(
        self,
        *,
        perspective: str,
        component_ids: tuple[str, ...] = (),
        material_ids: tuple[str, ...] = (),
        carrier_ids: tuple[str, ...] = (),
        process_ids: tuple[str, ...] = (),
        recorded_scopes: tuple[str, ...] = (),
        requested_scopes: tuple[str, ...] = (),
    ) -> tuple[ValidationCoverageRecord, ...]:
        requested = set(component_ids)
        materials = set(material_ids)
        carriers = set(carrier_ids)
        processes = set(process_ids)

        def scope_key(value: str | None) -> str:
            return (
                str(value or "")
                .casefold()
                .replace("–", "-")
                .replace("—", "-")
                .replace(" ", "")
            )

        recorded = {scope_key(value) for value in recorded_scopes}
        requested_scope_keys = {scope_key(value) for value in requested_scopes}
        rows: list[ValidationCoverageRecord] = []
        for row in self.records:
            if row.status != "rejected":
                continue
            if perspective == "material_source" and row.kind != "material":
                continue
            if perspective == "energy_source" and row.kind != "energy":
                continue
            # Entity scope and perspective are independent: a question scoped to
            # named components is unaffected by a rejection recorded elsewhere,
            # whichever perspective organizes its answer.
            if requested and not requested.intersection(row.component_ids):
                continue
            # A product-perspective total only aggregates what is attributed to
            # products, so an unattributable rejection cannot change it.
            if perspective == "product" and not row.component_ids:
                continue
            if materials and row.material_id and row.material_id not in materials:
                continue
            if carriers and row.carrier_id and row.carrier_id not in carriers:
                continue
            if processes and row.process_ids and processes.isdisjoint(row.process_ids):
                continue
            if recorded and row.recorded_scope and scope_key(row.recorded_scope) not in recorded:
                continue
            if (
                requested_scope_keys
                and row.requested_scope
                and scope_key(row.requested_scope) not in requested_scope_keys
            ):
                continue
            rows.append(row)
        return tuple(rows)


@dataclass(frozen=True, slots=True)
class ProjectionRecord:
    projection_key: tuple[str, ...]
    perspective: str
    emission_id: str
    consumption_id: str
    component_id: str | None
    module_id: str | None
    material_id: str | None
    carrier_id: str | None
    mode: str
    value_kgCO2e: float
    source_value_kgCO2e: float
    evidence_ids: tuple[str, ...]
    dimensions: Mapping[str, Any]
    recorded_for_occurrence_id: str | None
    allocation_set_id: str | None
    allocation_basis: str | None
    raw_weight: float | None
    raw_weight_unit: str | None
    normalized_weight: float | None
    evidence_record_id: str | None


@dataclass(frozen=True, slots=True)
class EntitySummary:
    entity_id: str
    name: str
    value_kgCO2e: float
    emission_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalQueryResult:
    status: str
    coverage_status: str
    perspective: str
    operation: str
    rows: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    emission_ids: tuple[str, ...]
    projection_keys: tuple[tuple[str, ...], ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class M3ExecutionContext:
    canonical: CanonicalV2Context
    release_profile: str
    controlled_fixture: bool
    availability: Mapping[str, str]
    validation_coverage: ValidationCoverageIndex
    project_summary: Mapping[str, float]
    component_summaries: tuple[EntitySummary, ...]
    material_summaries: tuple[EntitySummary, ...]
    carrier_summaries: tuple[EntitySummary, ...]
    projections: tuple[ProjectionRecord, ...]
    source_projections: tuple[ProjectionRecord, ...]


def _validation_index(context: CanonicalV2Context) -> ValidationCoverageIndex:
    facts_by_record = {fact.record_id: fact for fact in context.emissions}
    components_by_emission: dict[str, list[str]] = {}
    for item in context.product_contributions:
        components_by_emission.setdefault(item.emission_id, []).append(item.component_id)
    records: list[ValidationCoverageRecord] = []
    for row in context.validation_rows:
        fact = facts_by_record.get(str(row["recordId"]))
        if fact is not None:
            records.append(
                ValidationCoverageRecord(
                    record_id=fact.record_id,
                    status="accepted",
                    reason_code=None,
                    kind=fact.kind,
                    source_record_id=fact.source_record_id,
                    evidence_source_id=fact.evidence_source_id,
                    emission_id=fact.emission_id,
                    component_ids=tuple(
                        sorted(set(components_by_emission.get(fact.emission_id, ())))
                    ),
                    material_id=fact.material_id,
                    carrier_id=fact.carrier_id,
                    process_ids=fact.process_ids,
                    recorded_scope=fact.recorded_scope,
                    requested_scope=fact.requested_scope,
                )
            )
            continue
        evidence = json.loads(str(row["evidenceJson"]))
        raw_components = evidence.get("componentIds")
        if isinstance(raw_components, list):
            component_ids = tuple(str(value) for value in raw_components if str(value))
        else:
            component = str(
                evidence.get("targetComponentId")
                or evidence.get("componentId")
                or ""
            )
            component_ids = (component,) if component else ()
        raw_processes = evidence.get("processIds")
        process_ids = (
            tuple(str(value) for value in raw_processes if str(value))
            if isinstance(raw_processes, list)
            else ()
        )
        records.append(
            ValidationCoverageRecord(
                record_id=str(row["recordId"]),
                status="rejected",
                reason_code=str(row["reasonCode"]),
                kind=str(row["kind"]),
                source_record_id=str(row["sourceRecordId"]),
                evidence_source_id=str(row["evidenceSourceId"]),
                emission_id=None,
                component_ids=tuple(dict.fromkeys(component_ids)),
                material_id=str(
                    evidence.get("targetMaterialId")
                    or evidence.get("materialId")
                    or ""
                )
                or None,
                carrier_id=str(
                    evidence.get("energyCarrierId")
                    or evidence.get("carrierId")
                    or ""
                )
                or None,
                process_ids=tuple(dict.fromkeys(process_ids)),
                recorded_scope=str(evidence.get("recordedScope") or "") or None,
                requested_scope=str(evidence.get("requestedScope") or "") or None,
            )
        )
    accepted = sum(row.status == "accepted" for row in records)
    rejected = len(records) - accepted
    return ValidationCoverageIndex(tuple(records), accepted, rejected)


def _product_projection(
    context: CanonicalV2Context, item: ProductContribution
) -> ProjectionRecord:
    fact = next(row for row in context.emissions if row.emission_id == item.emission_id)
    # Factory energy can be attributed at the modular-unit level rather than a
    # building component; the executor already treats a module id as a valid
    # product-scope entity, so the M3 projection mirrors that instead of failing
    # a component lookup that a module id can never satisfy.
    if item.component_id in dimension_ids(context, "module"):
        component_props: Mapping[str, Any] = MappingProxyType({})
        component_type_ids: tuple[str, ...] = ()
        component_ifc_class: Any = "ModularUnit"
    else:
        component = lookup_component(context, item.component_id)
        component_props = _properties(component)
        component_type_ids = component_type_ids_for_component(context, item.component_id)
        component_ifc_class = component_props.get("ifcClass")
    process_classes: dict[str, list[str]] = {"stage": [], "process": []}
    for process_id in fact.process_ids:
        labels = set(lookup_process(context, process_id).get("labels", ()))
        key = "stage" if "ProductionStage" in labels else "process"
        process_classes[key].append(process_id)
    dimensions = immutable_mapping(
        {
            "module": item.module_id,
            "component": item.component_id,
            "component_type": component_type_ids,
            "ifc_class": component_ifc_class,
            "material": item.material_id,
            "carrier": item.carrier_id,
            "stage": tuple(process_classes["stage"]),
            "process": tuple(process_classes["process"]),
            "resource": fact.resource_ids,
            "source_kind": fact.kind,
        }
    )
    return ProjectionRecord(
        projection_key=item.key,
        perspective="product",
        emission_id=item.emission_id,
        consumption_id=item.consumption_id,
        component_id=item.component_id,
        module_id=item.module_id,
        material_id=item.material_id,
        carrier_id=item.carrier_id,
        mode=item.mode,
        value_kgCO2e=item.projected_value,
        source_value_kgCO2e=item.source_emission_value,
        evidence_ids=evidence_ids(context, item.emission_id, item.key),
        dimensions=dimensions,
        recorded_for_occurrence_id=item.recorded_for_occurrence_id,
        allocation_set_id=item.allocation_set_id,
        allocation_basis=item.allocation_basis,
        raw_weight=item.raw_weight,
        raw_weight_unit=item.raw_weight_unit,
        normalized_weight=item.normalized_weight,
        evidence_record_id=item.evidence_record_id,
    )


def _source_projection(context: CanonicalV2Context, fact: EmissionFact) -> ProjectionRecord:
    process_classes: dict[str, list[str]] = {"stage": [], "process": []}
    for process_id in fact.process_ids:
        labels = set(lookup_process(context, process_id).get("labels", ()))
        key = "stage" if "ProductionStage" in labels else "process"
        process_classes[key].append(process_id)
    return ProjectionRecord(
        projection_key=("source", fact.emission_id),
        perspective="material_source" if fact.kind == "material" else "energy_source",
        emission_id=fact.emission_id,
        consumption_id=fact.consumption_id,
        component_id=None,
        module_id=None,
        material_id=fact.material_id,
        carrier_id=fact.carrier_id,
        mode=fact.mode,
        value_kgCO2e=fact.emission_value,
        source_value_kgCO2e=fact.emission_value,
        evidence_ids=evidence_ids(context, fact.emission_id),
        dimensions=immutable_mapping(
            {
                "module": None,
                "component": None,
                "component_type": (),
                "ifc_class": None,
                "material": fact.material_id,
                "carrier": fact.carrier_id,
                "stage": tuple(process_classes["stage"]),
                "process": tuple(process_classes["process"]),
                "resource": fact.resource_ids,
                "source_kind": fact.kind,
            }
        ),
        recorded_for_occurrence_id=None,
        allocation_set_id=None,
        allocation_basis=None,
        raw_weight=None,
        raw_weight_unit=None,
        normalized_weight=None,
        evidence_record_id=None,
    )


def _summaries(
    context: CanonicalV2Context,
    projections: tuple[ProjectionRecord, ...],
    *,
    dimension: str,
) -> tuple[EntitySummary, ...]:
    values: dict[str, list[ProjectionRecord]] = {}
    for row in projections:
        entity_id = getattr(row, f"{dimension}_id")
        if entity_id:
            values.setdefault(entity_id, []).append(row)
    output: list[EntitySummary] = []
    for entity_id, rows in sorted(values.items()):
        if dimension == "component":
            node = (
                lookup_dimension(context, "module", entity_id)
                if entity_id in dimension_ids(context, "module")
                else lookup_component(context, entity_id)
            )
        elif dimension == "material":
            node = lookup_material(context, entity_id)
        else:
            node = lookup_carrier(context, entity_id)
        name = str(_properties(node).get("name") or entity_id)
        output.append(
            EntitySummary(
                entity_id=entity_id,
                name=name,
                value_kgCO2e=math.fsum(row.value_kgCO2e for row in rows),
                emission_ids=tuple(sorted({row.emission_id for row in rows})),
            )
        )
    return tuple(output)


def load_m3_execution_context(
    release_dir: Path | str, *, allow_synthetic: bool = False
) -> M3ExecutionContext:
    canonical = load_canonical_v2_context(release_dir, allow_synthetic=allow_synthetic)
    manifest_profile = str(canonical.manifest["releaseProfile"])
    product = tuple(
        _product_projection(canonical, item)
        for item in iter_product_contributions(canonical)
    )
    source = tuple(_source_projection(canonical, fact) for fact in iter_emissions(canonical))
    facts_by_emission = {fact.emission_id: fact for fact in canonical.emissions}
    material_source = math.fsum(
        row.value_kgCO2e for row in source if row.perspective == "material_source"
    )
    source_process = math.fsum(
        row.value_kgCO2e for row in source if row.perspective == "energy_source"
    )
    product_energy = math.fsum(
        row.value_kgCO2e
        for row in product
        if facts_by_emission[row.emission_id].kind == "energy"
    )
    product_total = math.fsum(row.value_kgCO2e for row in product)
    process_only = math.fsum(
        fact.emission_value for fact in canonical.process_only_emissions
    )
    summary = immutable_mapping(
        {
            "material_source_kgCO2e": material_source,
            "product_energy_kgCO2e": product_energy,
            "product_total_kgCO2e": product_total,
            "source_process_kgCO2e": source_process,
            "all_source_kgCO2e": math.fsum((material_source, source_process)),
            "process_only_kgCO2e": process_only,
        }
    )
    status_counts = {
        kind: {
            status: sum(
                row["kind"] == kind and row["status"] == status
                for row in canonical.validation_rows
            )
            for status in ("accepted", "rejected")
        }
        for kind in ("material", "energy")
    }
    availability_values = {}
    for kind, counts in status_counts.items():
        if counts["accepted"]:
            availability_values[kind] = "complete"
        elif counts["rejected"]:
            availability_values[kind] = "incomplete"
        else:
            availability_values[kind] = "not_available"
    return M3ExecutionContext(
        canonical=canonical,
        release_profile=manifest_profile,
        controlled_fixture=manifest_profile == "controlled-fixture",
        availability=immutable_mapping(availability_values),
        validation_coverage=_validation_index(canonical),
        project_summary=summary,
        component_summaries=_summaries(canonical, product, dimension="component"),
        material_summaries=_summaries(canonical, source, dimension="material"),
        carrier_summaries=_summaries(canonical, source, dimension="carrier"),
        projections=product,
        source_projections=source,
    )


__all__ = [
    "CanonicalQueryResult",
    "EntitySummary",
    "M3ExecutionContext",
    "ProjectionRecord",
    "ValidationCoverageIndex",
    "ValidationCoverageRecord",
    "immutable_mapping",
    "load_m3_execution_context",
]
