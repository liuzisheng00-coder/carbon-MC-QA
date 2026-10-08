"""Independent canonical-v2 reference truth for CarbonQL benchmarks."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    EmissionFact,
    ProductContribution,
    component_type_ids_for_component,
    components_for_ifc_class,
    dimension_ids,
    evidence_ids,
    graph_document_from_context,
    iter_emissions,
    iter_graph_edges,
    iter_product_contributions,
    lookup_component,
    lookup_dimension,
    lookup_process,
)
from dm2c_carbonql import (
    CarbonQLProgram,
    ENTITY_DIMENSIONS,
    GraphSchema,
    ProgramHole,
    derive_projection_perspective,
    validate_program,
)


_PERSPECTIVES = {"product", "material_source", "energy_source", "source_union"}


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    projection_perspective: str = ""
    selector_component_ids: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    group_keys: tuple[str, ...] = ()
    filters: tuple[Mapping[str, Any], ...] = ()
    join_required_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.projection_perspective not in _PERSPECTIVES:
            raise ValueError("projection_perspective must explicitly name one canonical perspective")


@dataclass(frozen=True, slots=True)
class CarbonQLCase:
    case_id: str
    question: str
    category: str
    gold_program: CarbonQLProgram
    expected_compiler_status: str
    reference: ReferenceSpec
    expected_view_signature: Mapping[str, Any] = field(default_factory=dict)
    expected_holes: tuple[ProgramHole, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "category": self.category,
            "gold_program": self.gold_program.to_dict(),
            "expected_compiler_status": self.expected_compiler_status,
            "reference": {
                "projection_perspective": self.reference.projection_perspective,
                "selector_component_ids": list(self.reference.selector_component_ids),
                "sources": list(self.reference.sources),
                "group_keys": list(self.reference.group_keys),
                "filters": [dict(row) for row in self.reference.filters],
                "join_required_sources": list(self.reference.join_required_sources),
            },
            "expected_view_signature": dict(self.expected_view_signature),
            "expected_holes": [hole.to_dict() for hole in self.expected_holes],
        }


@dataclass(frozen=True, slots=True)
class ReferenceResult:
    status: str
    rows: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    emission_ids: tuple[str, ...]
    projection_keys: tuple[tuple[str, ...], ...]
    trace_rows: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    holes: tuple[ProgramHole, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rows": [_plain(row) for row in self.rows],
            "summary": _plain(self.summary),
            "emission_ids": list(self.emission_ids),
            "projection_keys": [list(key) for key in self.projection_keys],
            "trace_rows": [_plain(row) for row in self.trace_rows],
            "coverage": _plain(self.coverage),
            "holes": [hole.to_dict() for hole in self.holes],
        }


@dataclass(frozen=True, slots=True)
class _ReferenceProjection:
    key: tuple[str, ...]
    emission_id: str
    consumption_id: str
    source_kind: str
    value: float
    component_id: str | None
    dimensions: Mapping[str, str | None]
    evidence: tuple[str, ...]
    occurrence_id: str | None
    evidence_record_id: str | None


@dataclass(frozen=True, slots=True)
class _ReferenceFilter:
    field: str
    allowed: tuple[str, ...]
    unsatisfiable: bool = False


_ENTITY_NAME_FIELDS = {f"{dimension}_name": dimension for dimension in ENTITY_DIMENSIONS}
_CARRIER_NAME_CACHE: dict[int, Mapping[str, str | None]] = {}
_STAGE_BY_ACTIVITY_CACHE: dict[int, Mapping[str, str | None]] = {}


@dataclass(frozen=True, slots=True)
class _ReferenceComponentSelection:
    resolved: tuple[str, ...]
    missing: tuple[str, ...] = ()
    invalid_cardinality: bool = False

    @property
    def unresolved(self) -> bool:
        return bool(self.missing or self.invalid_cardinality or not self.resolved)

    @property
    def requested(self) -> tuple[str, ...]:
        return self.resolved + self.missing


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _props(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("props", {})
    return value if isinstance(value, Mapping) else {}


def _entity_ids(context: CanonicalV2Context, dimension: str) -> tuple[str, ...]:
    if dimension == "stage":
        return tuple(
            entity_id
            for entity_id in dimension_ids(context, "process")
            if "ProductionStage"
            in tuple(lookup_dimension(context, "process", entity_id).get("labels", ()))
        )
    return dimension_ids(context, dimension)


def _carrier_names(context: CanonicalV2Context) -> Mapping[str, str | None]:
    key = id(context)
    cached = _CARRIER_NAME_CACHE.get(key)
    if cached is not None:
        return cached
    names: dict[str, list[str]] = {}
    for fact in iter_emissions(context):
        if not fact.carrier_id:
            continue
        for process_id in fact.process_ids:
            process_props = _props(lookup_process(context, process_id))
            process_value = process_props.get("energyCarrier")
            if process_value is None:
                continue
            text = str(process_value).strip()
            if text and text not in names.setdefault(fact.carrier_id, []):
                names[fact.carrier_id].append(text)
    resolved = {
        carrier_id: values[0] if len(values) == 1 else None
        for carrier_id, values in names.items()
    }
    _CARRIER_NAME_CACHE[key] = MappingProxyType(resolved)
    return _CARRIER_NAME_CACHE[key]


def _stage_by_activity(context: CanonicalV2Context) -> Mapping[str, str | None]:
    """Map each activity to the production stage that owns it.

    Energy is recorded against the activity that consumed it, so the stage sits
    one hop up the production structure rather than on the record itself. An
    activity claimed by more than one stage stays unresolved. This walk is
    derived here rather than shared with the executor so the reference truth
    stays an independent reading of the release.
    """
    key = id(context)
    cached = _STAGE_BY_ACTIVITY_CACHE.get(key)
    if cached is not None:
        return cached
    owners: dict[str, set[str]] = {}
    for edge in iter_graph_edges(graph_document_from_context(context)):
        if edge.get("type") != "hasActivity":
            continue
        stage_id = str(edge.get("src") or "")
        activity_id = str(edge.get("tgt") or "")
        if stage_id and activity_id:
            owners.setdefault(activity_id, set()).add(stage_id)
    resolved = {
        activity_id: next(iter(stages)) if len(stages) == 1 else None
        for activity_id, stages in owners.items()
    }
    _STAGE_BY_ACTIVITY_CACHE[key] = MappingProxyType(resolved)
    return _STAGE_BY_ACTIVITY_CACHE[key]


def _readable_name(
    context: CanonicalV2Context, dimension: str, entity_id: str | None
) -> str | None:
    if entity_id is None:
        return None
    if dimension == "ifc_class":
        return entity_id if entity_id in dimension_ids(context, "ifc_class") else None
    if entity_id not in set(_entity_ids(context, dimension)):
        return None
    lookup_dimension_name = "process" if dimension == "stage" else dimension
    props = _props(lookup_dimension(context, lookup_dimension_name, entity_id))
    value = props.get("name")
    if value is None and dimension == "process":
        value = props.get("activityName") or props.get("templateName")
    if value is None and dimension == "stage":
        value = props.get("stageName") or props.get("name")
    if value is None and dimension == "carrier":
        value = _carrier_names(context).get(entity_id)
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _with_readable_names(
    context: CanonicalV2Context, values: dict[str, str | None]
) -> Mapping[str, str | None]:
    for dimension in ENTITY_DIMENSIONS:
        values[f"{dimension}_name"] = _readable_name(
            context, dimension, values.get(dimension)
        )
    return MappingProxyType(values)


def _dimensions(
    context: CanonicalV2Context,
    fact: EmissionFact,
    item: ProductContribution | None,
) -> Mapping[str, str | None]:
    component_id = item.component_id if item else None
    component_type: str | None = None
    ifc_class: str | None = None
    if component_id:
        if component_id in dimension_ids(context, "module"):
            ifc_class = "ModularUnit"
        else:
            type_ids = component_type_ids_for_component(context, component_id)
            component_type = type_ids[0] if len(type_ids) == 1 else None
            props = _props(lookup_component(context, component_id))
            ifc_class = str(props.get("ifcClass")) if props.get("ifcClass") is not None else None
    stage: str | None = None
    process: str | None = None
    for entity_id in fact.process_ids:
        labels = tuple(lookup_process(context, entity_id).get("labels", ()))
        if "ProductionStage" in labels:
            stage = entity_id
        elif process is None:
            process = entity_id
    if stage is None and process is not None:
        stage = _stage_by_activity(context).get(process)
    return _with_readable_names(
        context,
        {
            "project": str(context.manifest["releaseId"]),
            "module": item.module_id if item else None,
            "component_type": component_type,
            "component": component_id,
            "ifc_class": ifc_class,
            "material": fact.material_id,
            "carrier": fact.carrier_id,
            "factor_keyword": fact.factor_keyword,
            "factor_source": fact.factor_source,
            "process": process,
            "stage": stage,
            "resource": fact.resource_ids[0] if len(fact.resource_ids) == 1 else None,
            "source_kind": "material" if fact.kind == "material" else "process",
        },
    )


def _projections(
    context: CanonicalV2Context,
    perspective: str,
    *,
    entity_scope: str = "project",
) -> list[_ReferenceProjection]:
    """Entity scope decides whether records must carry product attribution.

    A scope naming product objects can only collect records attributable to them,
    whatever dimensions the result is organized along; a scope naming materials or
    processes reads each record once at its full value.
    """
    facts = {fact.emission_id: fact for fact in iter_emissions(context)}
    if entity_scope == "product" or (
        entity_scope == "project" and perspective == "product"
    ):
        return [
            _ReferenceProjection(
                key=item.key,
                emission_id=item.emission_id,
                consumption_id=item.consumption_id,
                source_kind="material" if item.mode == "material" else "process",
                value=item.projected_value,
                component_id=item.component_id,
                dimensions=_dimensions(context, facts[item.emission_id], item),
                evidence=evidence_ids(context, item.emission_id, item.key),
                occurrence_id=item.recorded_for_occurrence_id,
                evidence_record_id=item.evidence_record_id,
            )
            for item in iter_product_contributions(context)
        ]
    kind = (
        None
        if entity_scope == "entity"
        else "material"
        if perspective == "material_source"
        else "energy"
        if perspective == "energy_source"
        else None
    )
    return [
        _ReferenceProjection(
            key=("source", fact.emission_id),
            emission_id=fact.emission_id,
            consumption_id=fact.consumption_id,
            source_kind="material" if fact.kind == "material" else "process",
            value=fact.emission_value,
            component_id=None,
            dimensions=_dimensions(context, fact, None),
            evidence=evidence_ids(context, fact.emission_id),
            occurrence_id=None,
            evidence_record_id=None,
        )
        for fact in iter_emissions(context, kind=kind)
    ]


def _program_sources(program: CarbonQLProgram) -> tuple[str, ...]:
    for step in program.steps:
        if step.op == "CarbonAtoms":
            raw = step.args.get("source")
            values = (raw,) if isinstance(raw, str) else tuple(raw or ())
            return ("material", "process") if "all" in values else tuple(str(value) for value in values)
    return ()


def _program_requests_process(program: CarbonQLProgram) -> bool:
    return "process" in _program_sources(program)


def _synthetic_energy_stamp(
    context: CanonicalV2Context, involves_process: bool
) -> dict[str, Any]:
    if context.synthetic_energy and involves_process:
        return {
            "synthetic_energy": True,
            "synthetic_energy_provenance": "controlled-fixture synthetic factory input",
        }
    return {}


def _program_groups(program: CarbonQLProgram) -> tuple[str, ...]:
    for step in program.steps:
        if step.op == "GroupBy":
            return tuple(str(value) for value in step.args.get("keys", ()))
    return ()


def _filter_values(filter_row: Mapping[str, Any]) -> tuple[str, ...]:
    raw = (filter_row["equals"],) if "equals" in filter_row else tuple(filter_row["in"])
    return tuple(str(value) for value in raw)


def _resolve_entity_filter_values(
    context: CanonicalV2Context, field: str, values: tuple[str, ...]
) -> _ReferenceFilter:
    by_name = field in _ENTITY_NAME_FIELDS
    dimension = _ENTITY_NAME_FIELDS[field] if by_name else field
    available = tuple(_entity_ids(context, dimension))
    resolved: list[str] = []
    for value in values:
        if not by_name and value in available:
            if value not in resolved:
                resolved.append(value)
            continue
        matches = tuple(
            entity_id
            for entity_id in available
            if _readable_name(context, dimension, entity_id) == value
        )
        if len(matches) != 1:
            return _ReferenceFilter(field, (), unsatisfiable=True)
        target = _readable_name(context, dimension, matches[0]) if by_name else matches[0]
        if target is not None and target not in resolved:
            resolved.append(target)
    return _ReferenceFilter(field, tuple(resolved))


def _resolve_filter(
    context: CanonicalV2Context, filter_row: Mapping[str, Any]
) -> _ReferenceFilter:
    field = str(filter_row["field"])
    values = _filter_values(filter_row)
    if field in ENTITY_DIMENSIONS or field in _ENTITY_NAME_FIELDS:
        return _resolve_entity_filter_values(context, field, values)
    return _ReferenceFilter(field, tuple(dict.fromkeys(values)))


def _group_result_row(
    groups: tuple[str, ...], group: tuple[str | None, ...], bucket: Sequence[_ReferenceProjection]
) -> dict[str, Any]:
    row: dict[str, Any] = {key: value for key, value in zip(groups, group)}
    for key in groups:
        if key in ENTITY_DIMENSIONS:
            name_key = f"{key}_name"
            names = {
                item.dimensions.get(name_key)
                for item in bucket
                if item.dimensions.get(name_key) is not None
            }
            row[name_key] = next(iter(names)) if len(names) == 1 else None
    row["kgCO2e"] = math.fsum(item.value for item in bucket)
    return row


def _selector_components(
    context: CanonicalV2Context,
    case: CarbonQLCase,
) -> _ReferenceComponentSelection | None:
    selector = case.gold_program.steps[0]
    reference_ids = tuple(str(value) for value in case.reference.selector_component_ids)
    if reference_ids and selector.op != "SelectClicked":
        raise ValueError(
            "selector_component_ids are external context only for SelectClicked"
        )
    if selector.op == "SelectProject":
        return None
    if (
        selector.op == "ResolveEntities"
        and selector.args.get("entity_type", "component") != "component"
    ):
        return None
    selector_ids = tuple(str(value) for value in selector.args.get("ids", ()))
    if selector.op == "SelectClicked":
        program = (
            _canonical_reference_component_ids(context, selector_ids)
            if selector_ids
            else None
        )
        external = (
            _canonical_reference_component_ids(context, reference_ids)
            if reference_ids
            else None
        )
        if program is not None and external is not None:
            if (
                program.unresolved
                or external.unresolved
                or set(program.resolved) != set(external.resolved)
            ):
                raise ValueError(
                    "selector conflict: gold SelectClicked ids and "
                    "ReferenceSpec.selector_component_ids differ"
                )
            return program
        return program or external or _ReferenceComponentSelection(())
    if selector_ids:
        resolution = _canonical_reference_component_ids(context, selector_ids)
        return _ReferenceComponentSelection(
            resolution.resolved,
            resolution.missing,
            str(selector.args.get("cardinality") or "singleton") == "singleton"
            and len(resolution.resolved) != 1,
        )
    if selector.op == "ResolveEntities" and selector.args.get("entity_type", "component") == "component":
        prop = str(selector.args.get("property") or "name")
        value = str(selector.args.get("value") or "")
        if prop == "ifcClass":
            try:
                matches = components_for_ifc_class(context, value)
            except Exception:
                return _ReferenceComponentSelection(())
        else:
            matches = tuple(
                component_id
                for component_id in dimension_ids(context, "component")
                if str(_props(lookup_component(context, component_id)).get(prop, "")) == value
            )
        invalid_cardinality = (
            str(selector.args.get("cardinality") or "singleton") == "singleton"
            and len(matches) != 1
        )
        return _ReferenceComponentSelection(matches, (), invalid_cardinality)
    return None


def _canonical_reference_component_ids(
    context: CanonicalV2Context, ids: Sequence[str]
) -> _ReferenceComponentSelection:
    available = set(dimension_ids(context, "component"))
    by_gid = {}
    for component_id in available:
        global_id = str(
            _props(lookup_component(context, component_id)).get("globalId") or ""
        )
        if global_id:
            by_gid[global_id] = component_id
    resolved: list[str] = []
    missing: list[str] = []
    for raw in ids:
        token = str(raw).strip()
        component_id = token if token in available else by_gid.get(token)
        if component_id and component_id not in resolved:
            resolved.append(component_id)
        elif component_id is None and token not in missing:
            missing.append(token)
    return _ReferenceComponentSelection(tuple(resolved), tuple(missing))


def _selector_entities(
    context: CanonicalV2Context,
    program: CarbonQLProgram,
) -> tuple[str, tuple[str, ...]] | None:
    selector = program.steps[0]
    if selector.op != "ResolveEntities":
        return None
    entity_type = str(selector.args.get("entity_type") or "component")
    if entity_type not in {"material", "process"}:
        return None
    ids = tuple(str(value) for value in selector.args.get("ids", ()))
    if ids:
        available = set(dimension_ids(context, entity_type))
        matches = tuple(dict.fromkeys(value for value in ids if value in available))
        missing = tuple(dict.fromkeys(value for value in ids if value not in available))
    else:
        prop = str(selector.args.get("property") or "name")
        value = str(selector.args.get("value") or "")
        matches = tuple(
            entity_id
            for entity_id in dimension_ids(context, entity_type)
            if str(_props(lookup_dimension(context, entity_type, entity_id)).get(prop, "")) == value
        )
        missing = ()
    cardinality = str(selector.args.get("cardinality") or "singleton")
    if missing or not matches or (cardinality == "singleton" and len(matches) != 1):
        return entity_type, ()
    return entity_type, matches


def _empty(
    status: str,
    context: CanonicalV2Context,
    *,
    holes: tuple[ProgramHole, ...] = (),
    requested_components: tuple[str, ...] = (),
    covered_components: tuple[str, ...] = (),
    missing_components: tuple[str, ...] | None = None,
    involves_process: bool = False,
) -> ReferenceResult:
    rejected = sum(row["status"] == "rejected" for row in context.validation_rows)
    coverage = {
        "accepted_projection_count": 0,
        "accepted_emission_count": 0,
        "rejected_count": rejected,
        "requested_component_count": len(requested_components),
        "covered_component_ids": covered_components,
        "missing_component_ids": (
            requested_components
            if missing_components is None
            else missing_components
        ),
    }
    coverage.update(_synthetic_energy_stamp(context, involves_process))
    return ReferenceResult(
        status=status,
        rows=(),
        summary=MappingProxyType(_synthetic_energy_stamp(context, involves_process)),
        emission_ids=(),
        projection_keys=(),
        trace_rows=(),
        coverage=MappingProxyType(coverage),
        holes=holes,
    )


def reference_evaluate(case: CarbonQLCase, context: CanonicalV2Context) -> ReferenceResult:
    validation = validate_program(case.gold_program, GraphSchema.from_context(context))
    requested_process = _program_requests_process(case.gold_program)
    component_selection = _selector_components(context, case)
    if component_selection is not None and component_selection.unresolved:
        return _empty(
            "unresolved_target",
            context,
            requested_components=component_selection.requested,
            covered_components=component_selection.resolved,
            missing_components=component_selection.missing,
            involves_process=requested_process,
        )
    components = (
        component_selection.resolved if component_selection is not None else None
    )
    entity_filter = _selector_entities(context, case.gold_program)
    if entity_filter and not entity_filter[1]:
        return _empty("unresolved_target", context, involves_process=requested_process)
    if (
        case.expected_compiler_status == "partial"
        or case.gold_program.holes
        or not validation.executable
    ):
        return _empty(
            validation.compiler_status,
            context,
            holes=case.gold_program.holes,
            involves_process=requested_process,
        )

    perspective = case.reference.projection_perspective
    entity_scope = (
        "product" if components is not None else "entity" if entity_filter else "project"
    )
    selected = _projections(context, perspective, entity_scope=entity_scope)
    sources = set(case.reference.sources or _program_sources(case.gold_program))
    involves_process = "process" in sources
    selected = [row for row in selected if row.source_kind in sources]
    if components is not None:
        selected = [row for row in selected if row.component_id in components]
    if entity_filter:
        dimension, matches = entity_filter
        if dimension == "process":
            selected = [
                row
                for row in selected
                if row.dimensions.get("process") in matches
                or row.dimensions.get("stage") in matches
            ]
        else:
            selected = [
                row for row in selected if row.dimensions.get(dimension) in matches
            ]
    filters = case.reference.filters or tuple(
        step.args for step in case.gold_program.steps if step.op == "Filter"
    )
    for filter_row in filters:
        resolution = _resolve_filter(context, filter_row)
        if resolution.unsatisfiable:
            return _empty(
                "unsatisfiable_filter",
                context,
                requested_components=tuple(components or ()),
                involves_process=involves_process,
            )
        allowed = set(resolution.allowed)
        selected = [
            row for row in selected if row.dimensions.get(resolution.field) in allowed
        ]
    joins = case.reference.join_required_sources or next(
        (tuple(step.args["required_sources"]) for step in case.gold_program.steps if step.op == "JoinByAttribution"),
        (),
    )
    if joins:
        coverage: dict[str, set[str]] = {}
        for row in _projections(context, "product"):
            if row.component_id:
                coverage.setdefault(row.component_id, set()).add(row.source_kind)
        eligible = {component for component, kinds in coverage.items() if set(joins) <= kinds}
        selected = [row for row in selected if row.component_id in eligible]

    groups = case.reference.group_keys or _program_groups(case.gold_program)
    if groups:
        buckets: dict[tuple[str | None, ...], list[_ReferenceProjection]] = {}
        for row in selected:
            buckets.setdefault(tuple(row.dimensions.get(key) for key in groups), []).append(row)
        result_rows = [
            _group_result_row(groups, group, bucket)
            for group, bucket in sorted(buckets.items(), key=lambda item: tuple(str(value or "") for value in item[0]))
        ]
    else:
        result_rows = [{"kgCO2e": math.fsum(row.value for row in selected)}]
    for step in case.gold_program.steps:
        if step.op == "Rank":
            result_rows.sort(key=lambda row: row["kgCO2e"], reverse=bool(step.args.get("descending", True)))
            result_rows = result_rows[: int(step.args.get("top_k", 10))]
        elif step.op == "Compare":
            result_rows.sort(key=lambda row: row["kgCO2e"], reverse=True)
    selected.sort(key=lambda row: row.key)
    trace = any(step.op == "Trace" for step in case.gold_program.steps)
    trace_rows = tuple(
        MappingProxyType(
            {
                "projection_key": row.key,
                "emission_id": row.emission_id,
                "consumption_id": row.consumption_id,
                "source_kind": row.source_kind,
                "value": row.value,
                "component_id": row.component_id,
                "evidence_ids": row.evidence,
                "recorded_for_occurrence_id": row.occurrence_id,
                "evidence_record_id": row.evidence_record_id,
            }
        )
        for row in selected
    ) if trace else ()
    covered = tuple(sorted({row.component_id for row in selected if row.component_id}))
    requested = tuple(components or ())
    return ReferenceResult(
        status="ok",
        rows=tuple(MappingProxyType(row) for row in result_rows),
        summary=MappingProxyType(
            {
                "total_kgCO2e": math.fsum(row.value for row in selected),
                "projection_perspective": derive_projection_perspective(case.gold_program),
                "record_projection_perspective": perspective,
                "row_count": len(result_rows),
                **_synthetic_energy_stamp(context, involves_process),
            }
        ),
        emission_ids=tuple(sorted({row.emission_id for row in selected})),
        projection_keys=tuple(row.key for row in selected),
        trace_rows=trace_rows,
        coverage=MappingProxyType(
            {
                "accepted_projection_count": len(selected),
                "accepted_emission_count": len({row.emission_id for row in selected}),
                "rejected_count": sum(row["status"] == "rejected" for row in context.validation_rows),
                "requested_component_count": len(requested),
                "covered_component_ids": covered,
                "missing_component_ids": tuple(value for value in requested if value not in covered),
                **_synthetic_energy_stamp(context, involves_process),
            }
        ),
    )


def write_pilot_cases(cases: Sequence[CarbonQLCase], path: Path) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(
        "".join(json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for case in cases),
        encoding="utf-8",
        newline="",
    )


__all__ = [
    "CarbonQLCase",
    "ReferenceResult",
    "ReferenceSpec",
    "reference_evaluate",
    "write_pilot_cases",
]
