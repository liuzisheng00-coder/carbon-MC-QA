"""IFC-native source records for the M2.3 canonical KG v2 migration.

This module extracts immutable source evidence only.  It deliberately does not
populate a graph, allocate material consumption, or calculate carbon.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Sequence

from dm2c_m23_canonical import stable_id

try:
    import ifcopenshell
except ImportError:  # pragma: no cover - reported when the extractor is constructed
    ifcopenshell = None


EXCLUDED_FACTORY_BACKBONE_TYPES = frozenset(
    {
        "IfcFurniture",
        "IfcAlarm",
        "IfcFireSuppressionTerminal",
        "IfcAirTerminal",
        "IfcValve",
    }
)


def _derived_id(instance: object, kind: str, *parts: object) -> None:
    object.__setattr__(instance, "id", stable_id(kind, *parts))


@dataclass(frozen=True)
class ComponentTypeRecord:
    ifc_hash: str
    global_id: str
    step_id: int
    ifc_class: str
    name: str = ""
    description: str = ""
    tag: str = ""
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _derived_id(self, "ComponentType", self.ifc_hash, self.global_id, self.step_id)


@dataclass(frozen=True)
class ComponentRecord:
    ifc_hash: str
    global_id: str
    step_id: int
    ifc_class: str
    name: str = ""
    description: str = ""
    object_type: str = ""
    predefined_type: str = ""
    tag: str = ""
    component_type: Optional[ComponentTypeRecord] = None
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _derived_id(
            self,
            "BuildingComponent",
            self.ifc_hash,
            self.global_id,
            self.step_id,
        )


@dataclass(frozen=True)
class MaterialAssociationRecord:
    ifc_hash: str
    component_id: str
    material_step_id: int
    association_step_id: int
    source_kind: Literal["occurrence", "type"]
    container_kind: str
    container_step_id: Optional[int]
    item_step_id: Optional[int]
    layer_or_constituent_index: Optional[int]
    material_name: str
    thickness: Optional[float] = None
    thickness_unit: Optional[str] = None
    constituent_fraction: Optional[float] = None
    material_id: str = field(init=False)
    id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.source_kind not in {"occurrence", "type"}:
            raise ValueError("source_kind must be 'occurrence' or 'type'")
        material_id = stable_id("IfcMaterial", self.ifc_hash, self.material_step_id)
        object.__setattr__(self, "material_id", material_id)
        _derived_id(
            self,
            "MaterialAssociation",
            self.ifc_hash,
            self.component_id,
            self.association_step_id,
            self.source_kind,
            self.container_kind,
            self.container_step_id,
            self.item_step_id,
            self.layer_or_constituent_index,
            self.material_step_id,
        )

    def _precedence_key(self) -> tuple[object, ...]:
        """Identity of one semantic material item, excluding traversal path."""
        return (
            self.component_id,
            self.material_id,
            self.item_step_id,
            self.layer_or_constituent_index,
            self.thickness,
            self.thickness_unit,
            self.constituent_fraction,
        )


@dataclass(frozen=True)
class DesignQuantityRecord:
    ifc_hash: str
    component_id: str
    quantity_step_id: int
    qto_set_step_id: int
    qto_set_name: str
    quantity_name: str
    quantity_subtype: str
    source_value: float
    source_unit: str
    normalized_value: float
    normalized_unit: str
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _derived_id(
            self,
            "DesignQuantity",
            self.ifc_hash,
            self.component_id,
            self.qto_set_step_id,
            self.quantity_step_id,
        )


@dataclass(frozen=True)
class IFCExtractionResult:
    ifc_sha256: str
    candidate_count: int
    excluded_count: int
    excluded_type_counts: tuple[tuple[str, int], ...]
    components: tuple[ComponentRecord, ...]
    material_associations: tuple[MaterialAssociationRecord, ...]
    design_quantities: tuple[DesignQuantityRecord, ...]


def deduplicate_material_associations(
    records: Iterable[MaterialAssociationRecord],
) -> tuple[MaterialAssociationRecord, ...]:
    """Collapse duplicate paths and prefer direct occurrence evidence.

    IDs retain relation and item identities.  Precedence only removes an
    inherited type path when it resolves to the same underlying material item;
    distinct IFC layer/constituent entities and indices remain independent.
    """
    unique_by_id = {record.id: record for record in records}
    direct_keys = {
        record._precedence_key()
        for record in unique_by_id.values()
        if record.source_kind == "occurrence"
    }
    retained = [
        record
        for record in unique_by_id.values()
        if record.source_kind == "occurrence" or record._precedence_key() not in direct_keys
    ]
    return tuple(sorted(retained, key=lambda record: record.id))


_PREFIX_FACTORS = {
    None: 1.0,
    "": 1.0,
    "EXA": 1e18,
    "PETA": 1e15,
    "TERA": 1e12,
    "GIGA": 1e9,
    "MEGA": 1e6,
    "KILO": 1e3,
    "HECTO": 1e2,
    "DECA": 1e1,
    "DECI": 1e-1,
    "CENTI": 1e-2,
    "MILLI": 1e-3,
    "MICRO": 1e-6,
    "NANO": 1e-9,
    "PICO": 1e-12,
    "FEMTO": 1e-15,
    "ATTO": 1e-18,
}

_PREFIX_SYMBOLS = {
    None: "",
    "": "",
    "EXA": "E",
    "PETA": "P",
    "TERA": "T",
    "GIGA": "G",
    "MEGA": "M",
    "KILO": "k",
    "HECTO": "h",
    "DECA": "da",
    "DECI": "d",
    "CENTI": "c",
    "MILLI": "m",
    "MICRO": "u",
    "NANO": "n",
    "PICO": "p",
    "FEMTO": "f",
    "ATTO": "a",
}

_QUANTITY_ATTRIBUTES = {
    "IfcQuantityLength": ("LengthValue", "LENGTHUNIT", "m"),
    "IfcQuantityArea": ("AreaValue", "AREAUNIT", "m2"),
    "IfcQuantityVolume": ("VolumeValue", "VOLUMEUNIT", "m3"),
    "IfcQuantityWeight": ("WeightValue", "MASSUNIT", "kg"),
    "IfcQuantityTime": ("TimeValue", "TIMEUNIT", "s"),
    "IfcQuantityCount": ("CountValue", None, "count"),
    "IfcQuantityNumber": ("NumberValue", None, "number"),
}


def _step_id(entity: Any) -> int:
    entity_id = getattr(entity, "id", None)
    if not callable(entity_id):
        raise ValueError(f"IFC entity has no STEP identity: {entity!r}")
    value = int(entity_id())
    if value <= 0:
        raise ValueError(f"IFC entity has invalid STEP identity: {value}")
    return value


def _text(entity: Any, attribute: str) -> str:
    return str(getattr(entity, attribute, "") or "")


def _is_a(entity: Any, ifc_class: str) -> bool:
    return entity is not None and bool(entity.is_a(ifc_class))


def _si_unit_descriptor(unit: Any) -> tuple[str, str, float]:
    unit_type = _text(unit, "UnitType").upper()
    prefix = _text(unit, "Prefix").upper() or None
    try:
        multiplier = _PREFIX_FACTORS[prefix]
        symbol_prefix = _PREFIX_SYMBOLS[prefix]
    except KeyError as exc:
        raise ValueError(f"unsupported IFC SI prefix: {prefix}") from exc

    name = _text(unit, "Name").upper()
    if unit_type == "LENGTHUNIT" and name == "METRE":
        return f"{symbol_prefix}m", "m", multiplier
    if unit_type == "AREAUNIT" and name == "SQUARE_METRE":
        return f"{symbol_prefix}m2", "m2", multiplier**2
    if unit_type == "VOLUMEUNIT" and name == "CUBIC_METRE":
        return f"{symbol_prefix}m3", "m3", multiplier**3
    if unit_type == "MASSUNIT" and name == "GRAM":
        return f"{symbol_prefix}g", "kg", multiplier * 0.001
    if unit_type == "TIMEUNIT" and name == "SECOND":
        return f"{symbol_prefix}s", "s", multiplier
    raise ValueError(f"unsupported IFC SI unit: {unit_type}/{name}")


def _numeric_value(value: Any) -> float:
    wrapped = getattr(value, "wrappedValue", value)
    return float(wrapped)


def _unit_descriptor(unit: Any) -> tuple[str, str, float]:
    if _is_a(unit, "IfcSIUnit"):
        return _si_unit_descriptor(unit)
    if _is_a(unit, "IfcConversionBasedUnit"):
        factor = getattr(unit, "ConversionFactor", None)
        base_unit = getattr(factor, "UnitComponent", None)
        if factor is None or base_unit is None:
            raise ValueError("IfcConversionBasedUnit has no conversion factor")
        _, canonical_unit, base_scale = _unit_descriptor(base_unit)
        scale = _numeric_value(getattr(factor, "ValueComponent", None)) * base_scale
        return _text(unit, "Name"), canonical_unit, scale
    raise ValueError(f"unsupported IFC named unit variant: {unit.is_a()}")


def _project_units(model: Any) -> dict[str, tuple[str, str, float]]:
    projects = list(model.by_type("IfcProject"))
    if len(projects) != 1 or getattr(projects[0], "UnitsInContext", None) is None:
        raise ValueError(
            "IFC extraction requires exactly one IfcProject with UnitsInContext"
        )
    assignment = projects[0].UnitsInContext
    units: dict[str, tuple[str, str, float]] = {}
    for unit in sorted(getattr(assignment, "Units", ()) or (), key=_step_id):
        unit_type = _text(unit, "UnitType").upper()
        if unit_type not in {
            "LENGTHUNIT",
            "AREAUNIT",
            "VOLUMEUNIT",
            "MASSUNIT",
            "TIMEUNIT",
        }:
            continue
        if unit_type in units:
            raise ValueError(f"ambiguous project unit assignment for {unit_type}")
        units[unit_type] = _unit_descriptor(unit)
    return units


def _quantity_descriptor(
    quantity: Any, project_units: dict[str, tuple[str, str, float]]
) -> tuple[float, str, float, str]:
    subtype = str(quantity.is_a())
    definition = _QUANTITY_ATTRIBUTES.get(subtype)
    if definition is None:
        raise ValueError(
            f"unsupported physical quantity subtype #{_step_id(quantity)}: {subtype}"
        )
    value_attribute, unit_type, canonical_unit = definition
    raw_value = _numeric_value(getattr(quantity, value_attribute))
    if unit_type is None:
        explicit_unit = getattr(quantity, "Unit", None)
        if explicit_unit is None:
            return raw_value, canonical_unit, raw_value, canonical_unit
        source_unit, resolved_canonical, scale = _unit_descriptor(explicit_unit)
        return raw_value, source_unit, raw_value * scale, resolved_canonical
    explicit_unit = getattr(quantity, "Unit", None)
    if explicit_unit is not None:
        source_unit, resolved_canonical, scale = _unit_descriptor(explicit_unit)
    else:
        try:
            source_unit, resolved_canonical, scale = project_units[unit_type]
        except KeyError as exc:
            raise ValueError(
                f"no project {unit_type} is assigned for #{_step_id(quantity)} {subtype}"
            ) from exc
    if resolved_canonical != canonical_unit:
        raise ValueError(
            f"quantity #{_step_id(quantity)} has incompatible unit {source_unit!r}"
        )
    return raw_value, source_unit, raw_value * scale, canonical_unit


def _iter_simple_quantities(quantity: Any) -> Iterable[Any]:
    if _is_a(quantity, "IfcPhysicalComplexQuantity"):
        for child in getattr(quantity, "HasQuantities", ()) or ():
            yield from _iter_simple_quantities(child)
        return
    yield quantity


class CanonicalIFCExtractor:
    """Extract canonical IFC-native records without graph or carbon mutation."""

    def __init__(self, ifc_path: Path | str, include_openings: bool = False) -> None:
        if ifcopenshell is None:
            raise RuntimeError("ifcopenshell is required to parse IFC")
        self.ifc_path = Path(ifc_path)
        self.include_openings = bool(include_openings)

    def extract(self) -> IFCExtractionResult:
        ifc_hash = sha256(self.ifc_path.read_bytes()).hexdigest().upper()
        model = ifcopenshell.open(str(self.ifc_path))
        project_units = _project_units(model)

        source_elements = {
            _step_id(entity): entity for entity in model.by_type("IfcElement")
        }
        candidates = [
            source_elements[step]
            for step in sorted(source_elements)
            if self.include_openings or not _is_a(source_elements[step], "IfcOpeningElement")
        ]
        excluded_counts = Counter(
            str(entity.is_a())
            for entity in candidates
            if str(entity.is_a()) in EXCLUDED_FACTORY_BACKBONE_TYPES
        )
        included = [
            entity
            for entity in candidates
            if str(entity.is_a()) not in EXCLUDED_FACTORY_BACKBONE_TYPES
        ]

        type_by_occurrence = self._type_map(model)
        components = tuple(
            self._component_record(entity, ifc_hash, type_by_occurrence.get(_step_id(entity)))
            for entity in included
        )
        component_by_step = {record.step_id: record for record in components}
        material_associations = deduplicate_material_associations(
            self._material_records(
                model,
                ifc_hash,
                component_by_step,
                type_by_occurrence,
                project_units,
            )
        )
        quantities = tuple(
            sorted(
                self._quantity_records(model, ifc_hash, component_by_step, project_units),
                key=lambda record: record.id,
            )
        )
        return IFCExtractionResult(
            ifc_sha256=ifc_hash,
            candidate_count=len(candidates),
            excluded_count=sum(excluded_counts.values()),
            excluded_type_counts=tuple(sorted(excluded_counts.items())),
            components=tuple(sorted(components, key=lambda record: record.id)),
            material_associations=material_associations,
            design_quantities=quantities,
        )

    @staticmethod
    def _type_map(model: Any) -> dict[int, Any]:
        candidates: dict[int, list[tuple[int, int, Any]]] = defaultdict(list)
        for relation in sorted(model.by_type("IfcRelDefinesByType"), key=_step_id):
            type_object = getattr(relation, "RelatingType", None)
            if type_object is None or not _is_a(type_object, "IfcTypeObject"):
                continue
            for occurrence in getattr(relation, "RelatedObjects", ()) or ():
                candidates[_step_id(occurrence)].append(
                    (_step_id(relation), _step_id(type_object), type_object)
                )
        return {
            occurrence_step: sorted(rows, key=lambda row: (row[0], row[1]))[0][2]
            for occurrence_step, rows in candidates.items()
        }

    @staticmethod
    def _component_record(
        entity: Any, ifc_hash: str, type_object: Optional[Any]
    ) -> ComponentRecord:
        global_id = _text(entity, "GlobalId")
        if not global_id:
            raise ValueError(f"IFC occurrence #{_step_id(entity)} has no GlobalId")
        component_type = None
        if type_object is not None:
            type_global_id = _text(type_object, "GlobalId")
            if not type_global_id:
                raise ValueError(f"IFC type object #{_step_id(type_object)} has no GlobalId")
            component_type = ComponentTypeRecord(
                ifc_hash=ifc_hash,
                global_id=type_global_id,
                step_id=_step_id(type_object),
                ifc_class=str(type_object.is_a()),
                name=_text(type_object, "Name"),
                description=_text(type_object, "Description"),
                tag=_text(type_object, "Tag"),
            )
        return ComponentRecord(
            ifc_hash=ifc_hash,
            global_id=global_id,
            step_id=_step_id(entity),
            ifc_class=str(entity.is_a()),
            name=_text(entity, "Name"),
            description=_text(entity, "Description"),
            object_type=_text(entity, "ObjectType"),
            predefined_type=_text(entity, "PredefinedType"),
            tag=_text(entity, "Tag"),
            component_type=component_type,
        )

    @staticmethod
    def _material_items(material_select: Any) -> list[tuple[Any, Any, int]]:
        if _is_a(material_select, "IfcMaterial"):
            return [(material_select, None, -1)]
        traversals = (
            ("IfcMaterialLayerSetUsage", "ForLayerSet", "MaterialLayers"),
            ("IfcMaterialLayerSet", None, "MaterialLayers"),
            ("IfcMaterialConstituentSet", None, "MaterialConstituents"),
            ("IfcMaterialProfileSetUsage", "ForProfileSet", "MaterialProfiles"),
            ("IfcMaterialProfileSet", None, "MaterialProfiles"),
            ("IfcMaterialList", None, "Materials"),
        )
        for ifc_class, nested_attribute, items_attribute in traversals:
            if not _is_a(material_select, ifc_class):
                continue
            container = (
                getattr(material_select, nested_attribute, None)
                if nested_attribute
                else material_select
            )
            return [
                (getattr(item, "Material", item), item, index)
                for index, item in enumerate(getattr(container, items_attribute, ()) or ())
            ]
        raise ValueError(
            f"unsupported IfcMaterialSelect variant: {material_select.is_a()}"
        )

    @classmethod
    def _records_for_material_relation(
        cls,
        relation: Any,
        component: ComponentRecord,
        ifc_hash: str,
        source_kind: Literal["occurrence", "type"],
        project_units: dict[str, tuple[str, str, float]],
    ) -> list[MaterialAssociationRecord]:
        material_select = getattr(relation, "RelatingMaterial", None)
        if material_select is None:
            return []
        records: list[MaterialAssociationRecord] = []
        for material, item, index in cls._material_items(material_select):
            if material is None or not _is_a(material, "IfcMaterial"):
                continue
            thickness = getattr(item, "LayerThickness", None) if item is not None else None
            thickness_unit = None
            if thickness is not None:
                try:
                    thickness_unit = project_units["LENGTHUNIT"][0]
                except KeyError as exc:
                    raise ValueError("material layer thickness has no project LENGTHUNIT") from exc
            fraction = getattr(item, "Fraction", None) if item is not None else None
            records.append(
                MaterialAssociationRecord(
                    ifc_hash=ifc_hash,
                    component_id=component.id,
                    material_step_id=_step_id(material),
                    association_step_id=_step_id(relation),
                    source_kind=source_kind,
                    container_kind=str(material_select.is_a()),
                    container_step_id=_step_id(material_select),
                    item_step_id=_step_id(item) if item is not None else None,
                    layer_or_constituent_index=index if index >= 0 else None,
                    material_name=_text(material, "Name"),
                    thickness=float(thickness) if thickness is not None else None,
                    thickness_unit=thickness_unit,
                    constituent_fraction=float(fraction) if fraction is not None else None,
                )
            )
        return records

    @classmethod
    def _material_records(
        cls,
        model: Any,
        ifc_hash: str,
        component_by_step: dict[int, ComponentRecord],
        type_by_occurrence: dict[int, Any],
        project_units: dict[str, tuple[str, str, float]],
    ) -> list[MaterialAssociationRecord]:
        occurrence_relations: dict[int, list[Any]] = defaultdict(list)
        type_relations: dict[int, list[Any]] = defaultdict(list)
        for relation in sorted(model.by_type("IfcRelAssociatesMaterial"), key=_step_id):
            for related in getattr(relation, "RelatedObjects", ()) or ():
                if _is_a(related, "IfcTypeObject"):
                    type_relations[_step_id(related)].append(relation)
                else:
                    occurrence_relations[_step_id(related)].append(relation)

        records: list[MaterialAssociationRecord] = []
        for occurrence_step in sorted(component_by_step):
            component = component_by_step[occurrence_step]
            for relation in occurrence_relations.get(occurrence_step, ()):
                records.extend(
                    cls._records_for_material_relation(
                        relation, component, ifc_hash, "occurrence", project_units
                    )
                )
            type_object = type_by_occurrence.get(occurrence_step)
            if type_object is not None:
                for relation in type_relations.get(_step_id(type_object), ()):
                    records.extend(
                        cls._records_for_material_relation(
                            relation, component, ifc_hash, "type", project_units
                        )
                    )
        return records

    @staticmethod
    def _quantity_records(
        model: Any,
        ifc_hash: str,
        component_by_step: dict[int, ComponentRecord],
        project_units: dict[str, tuple[str, str, float]],
    ) -> list[DesignQuantityRecord]:
        records: list[DesignQuantityRecord] = []
        for relation in sorted(model.by_type("IfcRelDefinesByProperties"), key=_step_id):
            quantity_set = getattr(relation, "RelatingPropertyDefinition", None)
            if quantity_set is None or not _is_a(quantity_set, "IfcElementQuantity"):
                continue
            related_steps = sorted(
                {
                    _step_id(related)
                    for related in getattr(relation, "RelatedObjects", ()) or ()
                    if _step_id(related) in component_by_step
                }
            )
            for quantity in sorted(
                (
                    leaf
                    for item in getattr(quantity_set, "Quantities", ()) or ()
                    for leaf in _iter_simple_quantities(item)
                ),
                key=_step_id,
            ):
                descriptor = _quantity_descriptor(quantity, project_units)
                raw_value, raw_unit, normalized_value, normalized_unit = descriptor
                for component_step in related_steps:
                    component = component_by_step[component_step]
                    records.append(
                        DesignQuantityRecord(
                            ifc_hash=ifc_hash,
                            component_id=component.id,
                            quantity_step_id=_step_id(quantity),
                            qto_set_step_id=_step_id(quantity_set),
                            qto_set_name=_text(quantity_set, "Name"),
                            quantity_name=_text(quantity, "Name"),
                            quantity_subtype=str(quantity.is_a()),
                            source_value=raw_value,
                            source_unit=raw_unit,
                            normalized_value=normalized_value,
                            normalized_unit=normalized_unit,
                        )
                    )
        return records


__all__ = [
    "CanonicalIFCExtractor",
    "ComponentRecord",
    "ComponentTypeRecord",
    "DesignQuantityRecord",
    "EXCLUDED_FACTORY_BACKBONE_TYPES",
    "IFCExtractionResult",
    "MaterialAssociationRecord",
    "deduplicate_material_associations",
]
