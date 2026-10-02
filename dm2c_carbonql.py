"""Typed CarbonQL program model used by the E4b experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    dimension_ids,
    graph_document_from_context,
    iter_emissions,
    iter_graph_edges,
    iter_graph_nodes,
    lookup_dimension,
)
from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_PREDICATES,
)


SELECTORS = frozenset({"SelectProject", "SelectClicked", "ResolveEntities"})
CARBON_SOURCES = frozenset({"material", "process"})
ENTITY_DIMENSIONS = frozenset(
    {
        "module",
        "component_type",
        "component",
        "ifc_class",
        "material",
        "carrier",
        "stage",
        "process",
        "resource",
    }
)
ENTITY_NAME_KEYS = frozenset(f"{dimension}_name" for dimension in ENTITY_DIMENSIONS)
GROUP_KEYS = frozenset(
    {
        "project",
        "module",
        "component_type",
        "component",
        "ifc_class",
        "material",
        "carrier",
        "factor_keyword",
        "factor_source",
        "stage",
        "process",
        "resource",
        "source_kind",
    }
) | ENTITY_NAME_KEYS
HOLE_DIMENSIONS = frozenset({"target", "emission_source", "grouping_dimension"})
AGGREGATE_METRICS = frozenset({"sum_kgCO2e"})
OPERATIONS = frozenset(
    {
        "SelectProject",
        "SelectClicked",
        "ResolveEntities",
        "CarbonAtoms",
        "Filter",
        "GroupBy",
        "Aggregate",
        "JoinByAttribution",
        "Rank",
        "Compare",
        "Trace",
    }
)
OPERATOR_ALLOWED_ARGS = {
    "SelectProject": frozenset(),
    "SelectClicked": frozenset({"ids"}),
    "ResolveEntities": frozenset(
        {"entity_type", "ids", "property", "value", "cardinality"}
    ),
    "CarbonAtoms": frozenset({"source", "known_total"}),
    "Filter": frozenset({"field", "equals", "in"}),
    "JoinByAttribution": frozenset({"required_sources"}),
    "GroupBy": frozenset({"keys"}),
    "Aggregate": frozenset({"metric"}),
    "Rank": frozenset({"top_k", "descending"}),
    "Compare": frozenset(),
    "Trace": frozenset(),
}

PRODUCT_KEYS = frozenset(
    {
        "project",
        "module",
        "module_name",
        "component_type",
        "component_type_name",
        "component",
        "component_name",
        "ifc_class",
        "ifc_class_name",
    }
)
MATERIAL_KEYS = frozenset({"material", "material_name"})
PROCESS_KEYS = frozenset(
    {
        "carrier",
        "carrier_name",
        "stage",
        "stage_name",
        "process",
        "process_name",
        "resource",
        "resource_name",
    }
)
FACTOR_KEYS = frozenset({"factor_keyword", "factor_source"})
SOURCE_KEYS = frozenset({"source_kind"})
RESOLVABLE_PROPERTIES_BY_ENTITY = {
    "component": frozenset({"name", "globalId", "ifcClass"}),
    "module": frozenset({"name"}),
    "material": frozenset({"name"}),
    "process": frozenset({"name"}),
}
RESOLVABLE_PROPERTIES = tuple(
    sorted(set().union(*RESOLVABLE_PROPERTIES_BY_ENTITY.values()))
)


class CarbonQLValidationError(ValueError):
    def __init__(self, code: str, step_index: int, message: str):
        super().__init__(message)
        self.code = code
        self.step_index = step_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "step_index": self.step_index,
            "message": str(self),
        }


@dataclass(frozen=True)
class CarbonQLStep:
    op: str
    args: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CarbonQLStep":
        op = str(data.get("op") or "").strip()
        if not op:
            raise ValueError("CarbonQL step requires op")
        return cls(op=op, args={key: value for key, value in data.items() if key != "op"})

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, **dict(self.args)}


@dataclass(frozen=True)
class ProgramHole:
    dimension: str
    candidates: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgramHole":
        dimension = str(data.get("dimension") or "").strip()
        candidates = tuple(str(value) for value in data.get("candidates", ()))
        if not dimension:
            raise ValueError("CarbonQL hole requires dimension")
        if not candidates:
            raise ValueError("CarbonQL hole requires candidates")
        return cls(dimension=dimension, candidates=candidates)

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "candidates": list(self.candidates)}


@dataclass(frozen=True)
class CarbonQLProgram:
    steps: tuple[CarbonQLStep, ...]
    holes: tuple[ProgramHole, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CarbonQLProgram":
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            raise ValueError("CarbonQL program requires a steps array")
        raw_holes = data.get("holes", [])
        if not isinstance(raw_holes, list):
            raise ValueError("CarbonQL holes must be an array")
        return cls(
            steps=tuple(CarbonQLStep.from_dict(step) for step in raw_steps),
            holes=tuple(ProgramHole.from_dict(hole) for hole in raw_holes),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"steps": [step.to_dict() for step in self.steps]}
        if self.holes:
            data["holes"] = [hole.to_dict() for hole in self.holes]
        return data


@dataclass(frozen=True)
class GraphSchema:
    labels: frozenset[str]
    relations: frozenset[str]
    dimension_counts: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    carbon_sources: frozenset[str] = CARBON_SOURCES

    @classmethod
    def from_context(cls, context: Any) -> "GraphSchema":
        if not isinstance(context, CanonicalV2Context):
            raise TypeError("context must be a CanonicalV2Context")
        document = graph_document_from_context(context)
        labels = frozenset(
            str(label)
            for node in iter_graph_nodes(document)
            for label in node.get("labels", ())
        )
        relations = frozenset(str(edge["type"]) for edge in iter_graph_edges(document))
        dimensions = {
            "project": 1,
            "component": len(dimension_ids(context, "component")),
            "module": len(dimension_ids(context, "module")),
            "component_type": len(dimension_ids(context, "component_type")),
            "ifc_class": len(dimension_ids(context, "ifc_class")),
            "material": len(dimension_ids(context, "material")),
            "carrier": len(dimension_ids(context, "carrier")),
            "process": len(dimension_ids(context, "process")),
            "resource": len(dimension_ids(context, "resource")),
        }
        stage_count = 0
        for process_id in dimension_ids(context, "process"):
            node = lookup_dimension(context, "process", process_id)
            if "ProductionStage" in tuple(node.get("labels", ())):
                stage_count += 1
        facts = tuple(iter_emissions(context))
        dimensions.update(
            {
                "stage": stage_count,
                "factor_keyword": len(
                    {fact.factor_keyword for fact in facts if fact.factor_keyword}
                ),
                "factor_source": len(
                    {fact.factor_source for fact in facts if fact.factor_source}
                ),
                "source_kind": len(facts),
            }
        )
        dimensions.update(
            {
                f"{dimension}_name": count
                for dimension, count in tuple(dimensions.items())
                if dimension in ENTITY_DIMENSIONS
            }
        )
        sources = frozenset(
            "material" if fact.kind == "material" else "process" for fact in facts
        )
        return cls(
            labels=labels,
            relations=relations,
            dimension_counts=MappingProxyType(dimensions),
            carbon_sources=sources,
        )

    def to_prompt_dict(self) -> dict[str, list[str]]:
        return {
            "labels": sorted(self.labels),
            "relations": sorted(self.relations),
        }


@dataclass(frozen=True)
class ValidationResult:
    compiler_status: str
    executable: bool
    output_type: str
    step_output_types: tuple[str, ...]


@dataclass(frozen=True)
class ViewSignature:
    entity_levels: tuple[str, ...]
    emission_sources: tuple[str, ...]
    operations: tuple[str, ...]
    trace: bool
    base_views: tuple[str, ...]
    view_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_levels": list(self.entity_levels),
            "emission_sources": list(self.emission_sources),
            "operations": list(self.operations),
            "trace": self.trace,
            "base_views": list(self.base_views),
            "view_mode": self.view_mode,
        }


def _error(code: str, step_index: int, message: str) -> CarbonQLValidationError:
    return CarbonQLValidationError(code=code, step_index=step_index, message=message)


def _as_string_sequence(value: Any, name: str, step_index: int) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise _error("schema_violation", step_index, f"{name} must be a string or string array")


def _hole_dimensions(program: CarbonQLProgram) -> frozenset[str]:
    raw_dimensions = [hole.dimension for hole in program.holes]
    dimensions = set(raw_dimensions)
    if len(dimensions) != len(raw_dimensions):
        raise _error("schema_violation", -1, "Hole dimensions must be unique")
    unknown = dimensions - HOLE_DIMENSIONS
    if unknown:
        raise _error("schema_violation", -1, f"Unknown hole dimension: {sorted(unknown)[0]}")
    return frozenset(dimensions)


def _used_hole_dimensions(program: CarbonQLProgram) -> frozenset[str]:
    used: set[str] = set()
    for step in program.steps:
        if step.op == "ResolveEntities" and step.args.get("entity_type") == "?":
            used.add("target")
        elif step.op == "CarbonAtoms":
            source = step.args.get("source")
            if source == "?" or (isinstance(source, list) and "?" in source):
                used.add("emission_source")
        elif step.op == "GroupBy":
            keys = step.args.get("keys")
            if keys == "?" or (isinstance(keys, list) and "?" in keys):
                used.add("grouping_dimension")
    return frozenset(used)


def _validate_source(value: Any, holes: frozenset[str], step_index: int) -> None:
    values = _as_string_sequence(value, "CarbonAtoms source", step_index)
    if not values:
        raise _error("schema_violation", step_index, "CarbonAtoms source cannot be empty")
    if values == ("?",):
        if "emission_source" not in holes:
            raise _error(
                "schema_violation",
                step_index,
                "Unresolved emission source requires emission_source hole",
            )
        return
    if "all" in values and any(source != "all" for source in values):
        raise _error(
            "schema_violation",
            step_index,
            "CarbonAtoms source 'all' cannot be combined with another source",
        )
    expanded = {"material", "process"} if "all" in values else set(values)
    unknown = expanded - CARBON_SOURCES
    if unknown:
        raise _error("schema_violation", step_index, f"Unknown carbon source: {sorted(unknown)[0]}")


def _expanded_source_values(value: Any) -> frozenset[str]:
    values = (value,) if isinstance(value, str) else tuple(value or ())
    if "?" in values:
        return frozenset()
    return CARBON_SOURCES if "all" in values else frozenset(str(item) for item in values)


def _validate_group_keys(value: Any, holes: frozenset[str], step_index: int) -> None:
    if value == "?":
        keys = ("?",)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        keys = tuple(value)
    else:
        raise _error("schema_violation", step_index, "GroupBy keys must be a string array")
    if not keys:
        raise _error("schema_violation", step_index, "GroupBy keys cannot be empty")
    if "?" in keys:
        if "grouping_dimension" not in holes:
            raise _error(
                "schema_violation",
                step_index,
                "Unresolved group key requires grouping_dimension hole",
            )
        if keys.count("?") != 1:
            raise _error(
                "schema_violation", step_index, "GroupBy permits one unresolved key"
            )
    unknown = set(keys) - GROUP_KEYS - {"?"}
    if unknown:
        raise _error(
            "schema_violation",
            step_index,
            f"Unknown grouping dimension: {sorted(unknown)[0]}",
        )
    if len(set(keys)) != len(keys):
        raise _error("schema_violation", step_index, "GroupBy keys must be unique")


def _schema_has_dimension(schema: GraphSchema, dimension: str) -> bool:
    if not schema.dimension_counts:
        return True
    return int(schema.dimension_counts.get(dimension, 0)) > 0


def _schema_has_source(schema: GraphSchema, source: str) -> bool:
    if not schema.carbon_sources:
        return False
    return source in schema.carbon_sources


def validate_program(program: CarbonQLProgram, schema: GraphSchema) -> ValidationResult:
    if not program.steps:
        raise _error("invalid_syntax", -1, "CarbonQL program has no steps")
    if program.steps[0].op not in SELECTORS:
        raise _error("type_error", 0, "Program must start with a selector")

    holes = _hole_dimensions(program)
    used_holes = _used_hole_dimensions(program)
    if holes != used_holes:
        raise _error(
            "schema_violation",
            -1,
            f"Declared hole dimensions {sorted(holes)} do not match unresolved arguments {sorted(used_holes)}",
        )
    current_type = ""
    output_types: list[str] = []
    incomplete_path = False

    for index, step in enumerate(program.steps):
        op = step.op
        if op not in OPERATIONS:
            raise _error("schema_violation", index, f"Unknown operation: {op}")
        unknown_args = set(step.args) - OPERATOR_ALLOWED_ARGS[op]
        if unknown_args:
            raise _error(
                "schema_violation",
                index,
                f"unknown argument for {op}: {sorted(unknown_args)[0]}",
            )

        if op in SELECTORS:
            if index != 0:
                raise _error("type_error", index, "Selector is only valid as the first step")
            if op == "ResolveEntities":
                raw_entity_type = step.args.get("entity_type", "component")
                if not isinstance(raw_entity_type, str) or not raw_entity_type.strip():
                    raise _error(
                        "schema_violation", index, "Unknown ResolveEntities type"
                    )
                entity_type = raw_entity_type
                output_by_entity = {
                    "component": "ProductSet",
                    "module": "ProductSet",
                    "material": "MaterialSet",
                    "process": "ProcessSet",
                }
                if entity_type == "?":
                    if "target" not in holes:
                        raise _error(
                            "schema_violation",
                            index,
                            "Unresolved entity type requires target hole",
                        )
                    current_type = "ProductSet"
                elif entity_type in output_by_entity:
                    current_type = output_by_entity[entity_type]
                    if not _schema_has_dimension(schema, entity_type):
                        incomplete_path = True
                else:
                    raise _error(
                        "schema_violation", index, f"Unknown ResolveEntities type: {entity_type}"
                    )
                has_ids = "ids" in step.args
                has_value = "value" in step.args
                has_property = "property" in step.args
                if has_ids == has_value or (has_property and not has_value):
                    raise _error(
                        "schema_violation",
                        index,
                        "ResolveEntities requires exactly one locator: ids or value; property is optional only with value",
                    )
                if has_ids and (
                    not isinstance(step.args["ids"], list)
                    or not step.args["ids"]
                    or not all(
                        isinstance(value, str) and bool(value.strip())
                        for value in step.args["ids"]
                    )
                ):
                    raise _error(
                        "schema_violation",
                        index,
                        "ResolveEntities ids must be a non-empty array of non-empty strings",
                    )
                property_name = step.args.get("property", "name")
                allowed_properties = (
                    set(RESOLVABLE_PROPERTIES)
                    if entity_type == "?"
                    else set(RESOLVABLE_PROPERTIES_BY_ENTITY.get(entity_type, ()))
                )
                if property_name not in allowed_properties:
                    raise _error(
                        "schema_violation",
                        index,
                        "ResolveEntities property is not queryable",
                    )
                if has_value and (
                    not isinstance(step.args["value"], str)
                    or not step.args["value"].strip()
                ):
                    raise _error(
                        "schema_violation",
                        index,
                        "ResolveEntities value must be a non-empty exact string",
                    )
                cardinality = step.args.get("cardinality", "singleton")
                if not isinstance(cardinality, str) or cardinality not in {
                    "singleton",
                    "set",
                }:
                    raise _error(
                        "schema_violation",
                        index,
                        "ResolveEntities cardinality must be singleton or set",
                    )
            elif op == "SelectClicked":
                if "ids" in step.args and (
                    not isinstance(step.args["ids"], list)
                    or not step.args["ids"]
                    or not all(
                        isinstance(value, str) and bool(value.strip())
                        for value in step.args["ids"]
                    )
                ):
                    raise _error(
                        "schema_violation",
                        index,
                        "SelectClicked ids must be a non-empty array of non-empty strings",
                    )
                current_type = "ProductSet"
            else:
                current_type = "ProductSet"

        elif op == "CarbonAtoms":
            if current_type not in {"ProductSet", "MaterialSet", "ProcessSet"}:
                raise _error(
                    "type_error", index, f"CarbonAtoms requires an entity set, got {current_type}"
                )
            _validate_source(step.args.get("source"), holes, index)
            if any(
                not _schema_has_source(schema, source)
                for source in _expanded_source_values(step.args.get("source"))
            ):
                incomplete_path = True
            current_type = "CarbonAtomSet"

        elif op == "Filter":
            if current_type != "CarbonAtomSet":
                raise _error("type_error", index, f"Filter cannot consume {current_type}")
            field = step.args.get("field")
            if not isinstance(field, str) or field not in GROUP_KEYS:
                raise _error(
                    "schema_violation",
                    index,
                    "Filter field must be one allowed carbon dimension",
                )
            if not _schema_has_dimension(schema, field):
                incomplete_path = True
            predicates = [name for name in ("equals", "in") if name in step.args]
            if len(predicates) != 1:
                raise _error(
                    "schema_violation",
                    index,
                    "Filter requires exactly one of equals or in",
                )
            if predicates[0] == "equals" and (
                not isinstance(step.args["equals"], str)
                or not step.args["equals"].strip()
            ):
                raise _error(
                    "schema_violation", index, "Filter equals must be a non-empty string"
                )
            if predicates[0] == "in" and (
                not isinstance(step.args["in"], list)
                or not step.args["in"]
                or not all(
                    isinstance(value, str) and bool(value.strip())
                    for value in step.args["in"]
                )
            ):
                raise _error(
                    "schema_violation",
                    index,
                    "Filter in must be a non-empty string array",
                )

        elif op == "JoinByAttribution":
            if current_type != "CarbonAtomSet":
                raise _error(
                    "type_error",
                    index,
                    f"JoinByAttribution requires CarbonAtomSet, got {current_type}",
                )
            required = _as_string_sequence(
                step.args.get("required_sources"), "JoinByAttribution required_sources", index
            )
            if len(set(required)) < 2 or set(required) - CARBON_SOURCES:
                raise _error(
                    "schema_violation",
                    index,
                    "JoinByAttribution requires material and process sources",
                )
            if any(not _schema_has_source(schema, source) for source in required):
                incomplete_path = True

        elif op == "GroupBy":
            if current_type != "CarbonAtomSet":
                raise _error("type_error", index, f"GroupBy requires CarbonAtomSet, got {current_type}")
            _validate_group_keys(step.args.get("keys"), holes, index)
            group_keys = (
                ("?",)
                if step.args.get("keys") == "?"
                else tuple(str(value) for value in step.args.get("keys", ()))
            )
            if any(
                key != "?" and not _schema_has_dimension(schema, key)
                for key in group_keys
            ):
                incomplete_path = True
            current_type = "GroupedCarbonSet"

        elif op == "Aggregate":
            if current_type not in {"CarbonAtomSet", "GroupedCarbonSet"}:
                raise _error(
                    "type_error", index, f"Aggregate requires carbon atoms or groups, got {current_type}"
                )
            metric = str(step.args.get("metric") or "")
            if metric not in AGGREGATE_METRICS:
                raise _error("schema_violation", index, f"Unknown aggregate metric: {metric}")
            current_type = "CarbonTable" if current_type == "GroupedCarbonSet" else "CarbonScalar"

        elif op == "Rank":
            if current_type != "CarbonTable":
                raise _error("type_error", index, f"Rank requires CarbonTable, got {current_type}")
            top_k = step.args.get("top_k", 10)
            descending = step.args.get("descending", True)
            if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
                raise _error(
                    "schema_violation", index, "Rank top_k must be a positive integer"
                )
            if not isinstance(descending, bool):
                raise _error(
                    "schema_violation", index, "Rank descending must be boolean"
                )
            current_type = "CarbonRanking"

        elif op == "Compare":
            if current_type not in {"CarbonTable", "CarbonRanking"}:
                raise _error("type_error", index, f"Compare requires CarbonTable, got {current_type}")
            current_type = "CarbonTable"

        elif op == "Trace":
            if current_type not in {
                "CarbonAtomSet",
                "GroupedCarbonSet",
                "CarbonScalar",
                "CarbonTable",
                "CarbonRanking",
            }:
                raise _error("type_error", index, f"Trace cannot consume {current_type}")
            current_type = "TraceResult"

        output_types.append(current_type)

    if current_type not in {
        "CarbonScalar",
        "CarbonTable",
        "CarbonRanking",
        "TraceResult",
    }:
        raise _error(
            "type_error",
            len(program.steps) - 1,
            f"Program must end in a carbon answer type, got {current_type}",
        )

    partial = bool(program.holes)
    return ValidationResult(
        compiler_status=(
            "partial" if partial else "incomplete_path" if incomplete_path else "valid"
        ),
        executable=not partial and not incomplete_path,
        output_type=current_type,
        step_output_types=tuple(output_types),
    )


def _sources_from_program(program: CarbonQLProgram) -> tuple[str, ...]:
    sources: set[str] = set()
    for step in program.steps:
        if step.op != "CarbonAtoms":
            continue
        value = step.args.get("source")
        values: Sequence[str]
        if isinstance(value, str):
            values = (value,)
        else:
            values = tuple(value or ())
        if "all" in values:
            sources.update(CARBON_SOURCES)
        else:
            sources.update(item for item in values if item in CARBON_SOURCES)
    return tuple(value for value in ("material", "process") if value in sources)


def _dimension_keys(program: CarbonQLProgram) -> frozenset[str]:
    keys: set[str] = set()
    for step in program.steps:
        if step.op == "GroupBy":
            raw = step.args.get("keys")
            values = (raw,) if isinstance(raw, str) else tuple(raw or ())
            keys.update(str(value) for value in values if value != "?")
        elif step.op == "Filter":
            field = str(step.args.get("field") or "")
            if field and field != "?":
                keys.add(field)
    return frozenset(keys)


def _selector_names_products(program: CarbonQLProgram) -> bool:
    """Whether the entity scope names product objects rather than the project."""
    selector = program.steps[0]
    if selector.op == "SelectClicked":
        return True
    if selector.op != "ResolveEntities":
        return False
    entity_type = str(selector.args.get("entity_type") or "component")
    return entity_type in {"component", "module"}


def derive_projection_perspective(program: CarbonQLProgram) -> str:
    """Read the perspective off G(P), per Equation M3.a.

    An organizing dimension settles the perspective on its own, and the entity
    scope does not contribute to that decision. When G(P) is empty the answer is
    a single scalar and no organizing dimension is available, so the default
    reading is the product perspective: the total carried by the selected
    objects.

    That default holds unless it would be unfaithful to the records it reads.
    Reading material atoms through the product projection is lossless, since
    every material record is carried by one component, so the label is only a
    presentation choice. A process record need not be carried by any product.
    An unorganized process-only program that names no product therefore takes
    the process perspective: read as a product total it would silently drop
    every shared record and report the remainder as though it were the whole
    process account. Where the scope does name products the product projection
    is what the question asked for, and the label follows it.
    """
    dimension_keys = _dimension_keys(program)
    if not dimension_keys:
        if set(_sources_from_program(program)) == {"process"} and not (
            _selector_names_products(program)
        ):
            return "energy_source"
        return "product"
    if dimension_keys & PRODUCT_KEYS:
        return "product"

    has_material = bool(dimension_keys & MATERIAL_KEYS)
    has_process = bool(dimension_keys & PROCESS_KEYS)
    if has_material and has_process:
        return "source_union"
    if has_material:
        return "material_source"
    if has_process:
        return "energy_source"

    if dimension_keys & (FACTOR_KEYS | SOURCE_KEYS):
        sources = set(_sources_from_program(program))
        if sources == {"material"}:
            return "material_source"
        if sources == {"process"}:
            return "energy_source"
        return "source_union"
    return "product"


def derive_view_signature(program: CarbonQLProgram) -> ViewSignature:
    operations: list[str] = []
    for step in program.steps:
        if step.op in {"Filter", "GroupBy", "Aggregate", "Rank", "Compare", "Trace"}:
            operations.append(
                {
                    "Filter": "filter",
                    "GroupBy": "group_by",
                    "Aggregate": "aggregate",
                    "Rank": "rank",
                    "Compare": "compare",
                    "Trace": "trace",
                }[step.op]
            )

    perspective = derive_projection_perspective(program)
    ordered_views = {
        "product": ("product",),
        "material_source": ("material",),
        "energy_source": ("process",),
        "source_union": ("material", "process"),
    }[perspective]
    return ViewSignature(
        entity_levels=ordered_views,
        emission_sources=_sources_from_program(program),
        operations=tuple(operations),
        trace=any(step.op == "Trace" for step in program.steps),
        base_views=ordered_views,
        view_mode="multi_view" if len(ordered_views) > 1 else "single_view",
    )
