from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
import pytest

import dm2c_m23_ifc as extraction_module
from dm2c_m23_ifc import (
    CanonicalIFCExtractor,
    ComponentRecord,
    DesignQuantityRecord,
    MaterialAssociationRecord,
    deduplicate_material_associations,
)


ACTUAL_IFC = Path(__file__).parents[1] / "typed_completed.ifc"
ACTUAL_IFC_SHA256 = "9934A332500BCBDA7EA40835803546982926F99B91CC84F229D59721807B937B"
EXCLUDED_TYPES = {
    "IfcFurniture",
    "IfcAlarm",
    "IfcFireSuppressionTerminal",
    "IfcAirTerminal",
    "IfcValve",
}


def _component(**changes: object) -> ComponentRecord:
    values = {
        "ifc_hash": "A" * 64,
        "global_id": "0abc",
        "step_id": 101,
        "ifc_class": "IfcBeam",
        "name": "梁",
        "description": "",
        "object_type": "user text only",
        "predefined_type": "BEAM",
        "tag": "B-1",
        "component_type": None,
    }
    values.update(changes)
    return ComponentRecord(**values)


def _association(**changes: object) -> MaterialAssociationRecord:
    values = {
        "ifc_hash": "A" * 64,
        "component_id": _component().id,
        "material_step_id": 501,
        "association_step_id": 601,
        "source_kind": "occurrence",
        "container_kind": "IfcMaterialConstituentSet",
        "container_step_id": 701,
        "item_step_id": 801,
        "layer_or_constituent_index": 0,
        "material_name": "钢材",
        "thickness": None,
        "thickness_unit": None,
        "constituent_fraction": 0.6,
    }
    values.update(changes)
    return MaterialAssociationRecord(**values)


def _write_quantity_ifc(path: Path) -> None:
    model = ifcopenshell.file(schema="IFC4")
    millimetre = model.create_entity(
        "IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE"
    )
    square_millimetre = model.create_entity(
        "IfcSIUnit", UnitType="AREAUNIT", Prefix="MILLI", Name="SQUARE_METRE"
    )
    cubic_millimetre = model.create_entity(
        "IfcSIUnit", UnitType="VOLUMEUNIT", Prefix="MILLI", Name="CUBIC_METRE"
    )
    units = model.create_entity(
        "IfcUnitAssignment", Units=[millimetre, square_millimetre, cubic_millimetre]
    )
    model.create_entity(
        "IfcProject", GlobalId=ifcopenshell.guid.new(), Name="test", UnitsInContext=units
    )
    beam = model.create_entity(
        "IfcBeam",
        GlobalId="0u4wVQYxP0E9fI2yATest1",
        Name="测试梁",
        Description="quantity fixture",
        ObjectType="must not become a type",
        PredefinedType="BEAM",
        Tag="B-1",
    )
    quantities = [
        model.create_entity("IfcQuantityLength", Name="Length", LengthValue=1000.0),
        model.create_entity("IfcQuantityArea", Name="Area", AreaValue=1_000_000.0),
        model.create_entity("IfcQuantityVolume", Name="Volume", VolumeValue=1_000_000_000.0),
    ]
    qto = model.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="Qto_BeamBaseQuantities",
        Quantities=quantities,
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=[beam],
        RelatingPropertyDefinition=qto,
    )
    model.write(str(path))


def _in_memory_quantity_model(
    *,
    schema: str = "IFC4",
    units: list[object] | None = None,
    quantities: list[object] | None = None,
):
    model = ifcopenshell.file(schema=schema)
    assignment = model.create_entity("IfcUnitAssignment", Units=units or [])
    model.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name="in-memory test",
        UnitsInContext=assignment,
    )
    beam = model.create_entity(
        "IfcBeam", GlobalId=ifcopenshell.guid.new(), Name="in-memory beam"
    )
    qto = model.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="Qto_InMemory",
        Quantities=quantities or [],
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=[beam],
        RelatingPropertyDefinition=qto,
    )
    return model, assignment, beam, qto


def _extract_in_memory(
    model: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "in-memory.ifc"
    source.write_bytes(b"in-memory IFC source identity")
    monkeypatch.setattr(extraction_module.ifcopenshell, "open", lambda _: model)
    return CanonicalIFCExtractor(source).extract()


def test_component_identity_uses_file_hash_global_id_and_occurrence_step_id():
    baseline = _component()

    assert replace(baseline, ifc_hash="B" * 64).id != baseline.id
    assert replace(baseline, global_id="different").id != baseline.id
    assert replace(baseline, step_id=102).id != baseline.id


def test_distinct_chinese_material_entities_do_not_collapse_by_display_name():
    first = _association(material_step_id=501, material_name="钢材")
    second = _association(material_step_id=502, material_name="钢材")

    assert first.material_id != second.material_id
    assert len(deduplicate_material_associations([first, second])) == 2


def test_duplicate_paths_for_same_material_semantic_association_collapse_once():
    record = _association()

    assert deduplicate_material_associations([record, record]) == (record,)


def test_direct_occurrence_association_precedes_equivalent_type_association():
    direct = _association(source_kind="occurrence", association_step_id=601)
    inherited = _association(source_kind="type", association_step_id=602)

    assert deduplicate_material_associations([inherited, direct]) == (direct,)


def test_distinct_layer_or_constituent_indices_remain_separate_candidates():
    first = _association(layer_or_constituent_index=0, item_step_id=801)
    second = _association(layer_or_constituent_index=1, item_step_id=802)

    assert len(deduplicate_material_associations([first, second])) == 2


def test_association_identity_includes_relation_step_and_item_index():
    baseline = _association()

    assert replace(baseline, association_step_id=999).id != baseline.id
    assert replace(baseline, layer_or_constituent_index=1).id != baseline.id


def test_component_type_is_absent_without_an_actual_ifc_type_object(tmp_path: Path):
    source = tmp_path / "quantities.ifc"
    _write_quantity_ifc(source)

    component = CanonicalIFCExtractor(source).extract().components[0]

    assert component.component_type is None
    assert component.object_type == "must not become a type"
    assert component.predefined_type == "BEAM"


def test_design_quantities_preserve_ifc_source_and_normalize_project_units(tmp_path: Path):
    source = tmp_path / "quantities.ifc"
    _write_quantity_ifc(source)

    result = CanonicalIFCExtractor(source).extract()
    quantities = {record.quantity_name: record for record in result.design_quantities}

    assert set(quantities) == {"Length", "Area", "Volume"}
    expected = {
        "Length": ("IfcQuantityLength", 1000.0, "mm", 1.0, "m"),
        "Area": ("IfcQuantityArea", 1_000_000.0, "mm2", 1.0, "m2"),
        "Volume": ("IfcQuantityVolume", 1_000_000_000.0, "mm3", 1.0, "m3"),
    }
    for name, (subtype, raw_value, raw_unit, si_value, si_unit) in expected.items():
        record = quantities[name]
        assert record.quantity_step_id > 0
        assert record.qto_set_step_id > 0
        assert record.qto_set_name == "Qto_BeamBaseQuantities"
        assert record.quantity_subtype == subtype
        assert record.source_value == raw_value
        assert record.source_unit == raw_unit
        assert record.normalized_value == pytest.approx(si_value)
        assert record.normalized_unit == si_unit


def test_project_units_follow_the_actual_project_context_not_assignment_step_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model = ifcopenshell.file(schema="IFC4")
    metre = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    model.create_entity("IfcUnitAssignment", Units=[metre])
    millimetre = model.create_entity(
        "IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE"
    )
    project_assignment = model.create_entity("IfcUnitAssignment", Units=[millimetre])
    model.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        UnitsInContext=project_assignment,
    )
    beam = model.create_entity("IfcBeam", GlobalId=ifcopenshell.guid.new())
    quantity = model.create_entity(
        "IfcQuantityLength", Name="Length", LengthValue=1000.0
    )
    qto = model.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="Qto_ProjectContext",
        Quantities=[quantity],
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=[beam],
        RelatingPropertyDefinition=qto,
    )

    result = _extract_in_memory(model, tmp_path, monkeypatch)

    record = result.design_quantities[0]
    assert record.source_unit == "mm"
    assert record.normalized_value == pytest.approx(1.0)
    assert record.normalized_unit == "m"


@pytest.mark.parametrize("project_count", [0, 2])
def test_project_unit_context_fails_closed_when_absent_or_ambiguous(
    project_count: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model = ifcopenshell.file(schema="IFC4")
    unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    assignment = model.create_entity("IfcUnitAssignment", Units=[unit])
    for _ in range(project_count):
        model.create_entity(
            "IfcProject",
            GlobalId=ifcopenshell.guid.new(),
            UnitsInContext=assignment,
        )

    with pytest.raises(ValueError, match="exactly one IfcProject with UnitsInContext"):
        _extract_in_memory(model, tmp_path, monkeypatch)


def test_project_unit_context_fails_closed_on_duplicate_dimension_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model = ifcopenshell.file(schema="IFC4")
    metre = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    millimetre = model.create_entity(
        "IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE"
    )
    assignment = model.create_entity(
        "IfcUnitAssignment", Units=[metre, millimetre]
    )
    model.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        UnitsInContext=assignment,
    )

    with pytest.raises(ValueError, match="ambiguous project unit assignment for LENGTHUNIT"):
        _extract_in_memory(model, tmp_path, monkeypatch)


def test_all_schema_simple_quantity_leaves_are_preserved_and_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model = ifcopenshell.file(schema="IFC4X3")
    kilogram = model.create_entity(
        "IfcSIUnit", UnitType="MASSUNIT", Prefix="KILO", Name="GRAM"
    )
    second = model.create_entity("IfcSIUnit", UnitType="TIMEUNIT", Name="SECOND")
    assignment = model.create_entity("IfcUnitAssignment", Units=[kilogram, second])
    model.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        UnitsInContext=assignment,
    )
    beam = model.create_entity("IfcBeam", GlobalId=ifcopenshell.guid.new())
    quantities = [
        model.create_entity("IfcQuantityNumber", Name="Number", NumberValue=2.5),
        model.create_entity("IfcQuantityCount", Name="Count", CountValue=3),
        model.create_entity("IfcQuantityWeight", Name="Weight", WeightValue=4.0),
        model.create_entity("IfcQuantityTime", Name="Time", TimeValue=5.0),
    ]
    qto = model.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="Qto_AllSimpleLeaves",
        Quantities=quantities,
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=[beam],
        RelatingPropertyDefinition=qto,
    )

    result = _extract_in_memory(model, tmp_path, monkeypatch)
    records = {record.quantity_name: record for record in result.design_quantities}

    assert set(records) == {"Number", "Count", "Weight", "Time"}
    assert (records["Number"].source_value, records["Number"].source_unit) == (
        2.5,
        "number",
    )
    assert (records["Number"].normalized_value, records["Number"].normalized_unit) == (
        2.5,
        "number",
    )
    assert (records["Count"].source_unit, records["Count"].normalized_unit) == (
        "count",
        "count",
    )
    assert (records["Weight"].source_unit, records["Weight"].normalized_unit) == (
        "kg",
        "kg",
    )
    assert (records["Time"].source_unit, records["Time"].normalized_unit) == (
        "s",
        "s",
    )
    assert [records[name].normalized_value for name in ["Count", "Weight", "Time"]] == [
        3.0,
        4.0,
        5.0,
    ]


def test_unsupported_simple_physical_quantity_subtype_fails_closed():
    class UnknownQuantity:
        def is_a(self, requested: str | None = None):
            if requested is None:
                return "IfcQuantityMystery"
            return requested == "IfcPhysicalSimpleQuantity"

        def id(self):
            return 999

    with pytest.raises(ValueError, match="unsupported physical quantity subtype"):
        extraction_module._quantity_descriptor(UnknownQuantity(), {})


def test_extraction_is_deterministic_when_ifc_traversal_order_is_reversed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "quantities.ifc"
    _write_quantity_ifc(source)
    expected = CanonicalIFCExtractor(source).extract()
    model = ifcopenshell.open(str(source))

    class ReverseTraversalModel:
        def by_type(self, name: str):
            return list(reversed(model.by_type(name)))

    monkeypatch.setattr(extraction_module.ifcopenshell, "open", lambda _: ReverseTraversalModel())

    assert CanonicalIFCExtractor(source).extract() == expected


def test_actual_ifc_regression_counts_exclusions_and_hash():
    assert sha256(ACTUAL_IFC.read_bytes()).hexdigest().upper() == ACTUAL_IFC_SHA256

    result = CanonicalIFCExtractor(ACTUAL_IFC).extract()

    assert result.ifc_sha256 == ACTUAL_IFC_SHA256
    assert result.candidate_count == 743
    assert result.excluded_count == 14
    assert len(result.components) == 729
    assert not ({component.ifc_class for component in result.components} & EXCLUDED_TYPES)
    assert sum(count for _, count in result.excluded_type_counts) == 14


def test_legacy_module_exposes_canonical_extractor_without_switching_builder():
    from dm2c_multigranular_carbon_kg import (
        CanonicalComponentRecord,
        CanonicalIFCExtractor as ReexportedExtractor,
        DesignQuantityRecord as ReexportedQuantity,
        IFCExtractionResult,
        MaterialAssociationRecord as ReexportedAssociation,
    )

    assert ReexportedExtractor is CanonicalIFCExtractor
    assert CanonicalComponentRecord is ComponentRecord
    assert ReexportedAssociation is MaterialAssociationRecord
    assert ReexportedQuantity is DesignQuantityRecord
    assert IFCExtractionResult.__dataclass_params__.frozen
