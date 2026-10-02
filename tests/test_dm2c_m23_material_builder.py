from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from dm2c_m23_calculation import (
    ConsumptionCandidate,
    QuantityOperand,
    ValidationIssue,
    validate_material_candidate,
)
from dm2c_m23_canonical import (
    FORBIDDEN_RUNTIME_LABELS,
    FORBIDDEN_RUNTIME_RELATIONS,
    CanonicalLPGGraph,
    NodeIdCollisionError,
    stable_id,
)
from dm2c_m23_ifc import (
    CanonicalIFCExtractor,
    ComponentRecord,
    ComponentTypeRecord,
    DesignQuantityRecord,
    IFCExtractionResult,
    MaterialAssociationRecord,
)
from dm2c_multigranular_carbon_kg import (
    FactorLibrary,
    FactorRecord,
    MultiGranularCarbonKGBuilder,
)


IFC_SHA = "A" * 64
SCOPE = "A1-A3"


def _factor(value: float = 2.5) -> FactorRecord:
    return FactorRecord(
        factor_id="factor:workbook:row:7",
        keyword="structural steel",
        factor_value=value,
        factor_unit="kgCO2e/kg",
        source="fixture factor workbook",
        match_status="accepted_strict_v2",
        confidence=1.0,
        source_row_id="workbook:sheet:row:7",
        original_factor_unit="kgCO2e/kg",
        denominator_unit="kg",
        normalized_factor_value=value,
        normalized_denominator="kg",
        geography="Hong Kong",
        year="2026",
        system_boundary=SCOPE,
        source_reference="doi:fixture-factor",
        proxy_status="primary",
        validation_status="accepted",
    )


def _one_material_fixture():
    component = ComponentRecord(
        ifc_hash=IFC_SHA,
        global_id="3FixtureComponent",
        step_id=21,
        ifc_class="IfcBeam",
        name="Beam 甲",
        description="Fixture occurrence",
        object_type="",
        predefined_type="BEAM",
        tag="B-21",
    )
    association = MaterialAssociationRecord(
        ifc_hash=IFC_SHA,
        component_id=component.id,
        material_step_id=31,
        association_step_id=41,
        source_kind="occurrence",
        container_kind="IfcMaterial",
        container_step_id=31,
        item_step_id=None,
        layer_or_constituent_index=None,
        material_name="钢材",
    )
    design_quantity = DesignQuantityRecord(
        ifc_hash=IFC_SHA,
        component_id=component.id,
        quantity_step_id=51,
        qto_set_step_id=50,
        qto_set_name="Qto_BeamBaseQuantities",
        quantity_name="NetWeight",
        quantity_subtype="IfcQuantityWeight",
        source_value=12.0,
        source_unit="kg",
        normalized_value=12.0,
        normalized_unit="kg",
    )
    extraction = IFCExtractionResult(
        ifc_sha256=IFC_SHA,
        candidate_count=1,
        excluded_count=0,
        excluded_type_counts=(),
        components=(component,),
        material_associations=(association,),
        design_quantities=(design_quantity,),
    )
    candidate = ConsumptionCandidate(
        record_id="material-record:41:31",
        kind="material",
        source_record_id=association.id,
        evidence_source_id=f"ifc:{IFC_SHA}",
        product_target_id=component.id,
        material_id=association.material_id,
        recorded_scope=SCOPE,
        requested_scope=SCOPE,
        factor=_factor(),
        factor_resolution_reason="",
        formula_code="direct_mass",
        operands=(
            QuantityOperand(
                operand_id="operand:qto:51",
                role="material_mass",
                value=12.0,
                unit="kg",
                source_id=design_quantity.id,
                design_quantity_id=design_quantity.id,
            ),
        ),
        measured=True,
    )
    validation = validate_material_candidate(candidate)
    assert validation.issue is None
    assert validation.accepted is not None
    return extraction, validation.accepted


def _builder(extraction: IFCExtractionResult, accepted=(), **kwargs):
    return MultiGranularCarbonKGBuilder(
        ifc_path=Path("fixture.ifc"),
        factor_library=FactorLibrary(None),
        module_name="Fixture module",
        extraction_result=extraction,
        accepted_materials=accepted,
        **kwargs,
    )


def _calculation_counts(graph: CanonicalLPGGraph) -> dict[str, int]:
    return {
        label: graph.count_label(label)
        for label in (
            "MaterialConsumption",
            "ConsumptionQuantity",
            "EmissionFactor",
            "CarbonEmission",
        )
    }


def _factor_node_id(accepted) -> str:
    return stable_id("EmissionFactor", accepted.factor_source_id)


def _graph_bytes(graph: CanonicalLPGGraph) -> bytes:
    return json.dumps(
        {
            "nodes": graph.nodes,
            "edges": graph.edges,
            "edgeIndex": graph._edges_by_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _contains_blocked(value) -> bool:
    if isinstance(value, str):
        return value.casefold().startswith("blocked")
    if isinstance(value, dict):
        return any(
            str(key).casefold().startswith("blocked") or _contains_blocked(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_blocked(item) for item in value)
    return False


def test_accepted_material_record_has_one_complete_emission_path():
    extraction, accepted = _one_material_fixture()

    graph = _builder(extraction, (accepted,)).build()

    assert isinstance(graph, CanonicalLPGGraph)
    assert _calculation_counts(graph) == {
        "MaterialConsumption": 1,
        "ConsumptionQuantity": 1,
        "EmissionFactor": 1,
        "CarbonEmission": 1,
    }
    for relation in (
        "recordedForObject",
        "ofMaterial",
        "hasQuantity",
        "hasFactor",
        "hasCarbonDriver",
    ):
        assert graph.count_relation(relation) == 1

    generated = graph.edges_of_type("hasCarbonDriver")[0]
    assert (generated["src"], generated["tgt"]) == (
        accepted.emission_id,
        accepted.consumption_id,
    )
    quantity = graph.nodes[accepted.quantity_id]["props"]
    factor = graph.nodes[_factor_node_id(accepted)]["props"]
    emission = graph.nodes[accepted.emission_id]["props"]
    assert emission["emissionValue"] == pytest.approx(
        quantity["quantityValue"] * factor["factorValue"]
    )
    assert [json.loads(value) for value in quantity["orderedRawOperands"]] == [
        asdict(accepted.operands[0])
    ]
    assert [json.loads(value) for value in quantity["conversionSteps"]] == [
        asdict(step) for step in accepted.conversion_steps
    ]


def test_material_build_path_contains_no_legacy_runtime_vocabulary_or_blocked_property():
    extraction, accepted = _one_material_fixture()

    graph = _builder(extraction, (accepted,)).build()

    labels = {
        label for node in graph.nodes.values() for label in node.get("labels", ())
    }
    relations = {edge["type"] for edge in graph.edges}
    assert labels.isdisjoint(FORBIDDEN_RUNTIME_LABELS)
    assert relations.isdisjoint(FORBIDDEN_RUNTIME_RELATIONS)
    assert graph.count_relation("EMISSION_OF") == 0
    assert graph.count_label("FactoryTarget") == 0
    assert graph.count_label("AggregateCarbonEmission") == 0
    assert all(
        not str(key).casefold().startswith("blocked")
        for node in graph.nodes.values()
        for key in node.get("props", {})
    )
    assert all(
        not _contains_blocked(node.get("props", {}))
        for node in graph.nodes.values()
    )


def test_validation_issue_cannot_be_populated_and_leaves_graph_unchanged():
    extraction, _ = _one_material_fixture()
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    before = (dict(graph.nodes), list(graph.edges))
    issue = ValidationIssue(
        record_id="rejected:1",
        reason_code="factor_missing",
        message="No unique compatible factor.",
        evidence={"factorPresent": False},
    )

    with pytest.raises(TypeError, match="AcceptedConsumption"):
        builder.populate_accepted_material(issue)  # type: ignore[arg-type]

    assert (graph.nodes, graph.edges) == before


def test_same_name_material_entities_and_parallel_associations_remain_distinct():
    extraction, _ = _one_material_fixture()
    first = extraction.material_associations[0]
    second_entity = MaterialAssociationRecord(
        ifc_hash=IFC_SHA,
        component_id=first.component_id,
        material_step_id=32,
        association_step_id=42,
        source_kind="occurrence",
        container_kind="IfcMaterial",
        container_step_id=32,
        item_step_id=None,
        layer_or_constituent_index=None,
        material_name=first.material_name,
    )
    parallel_occurrence = MaterialAssociationRecord(
        ifc_hash=IFC_SHA,
        component_id=first.component_id,
        material_step_id=first.material_step_id,
        association_step_id=43,
        source_kind="occurrence",
        container_kind="IfcMaterialLayerSetUsage",
        container_step_id=60,
        item_step_id=61,
        layer_or_constituent_index=0,
        material_name=first.material_name,
        thickness=125.0,
        thickness_unit="mm",
        constituent_fraction=0.75,
    )
    extraction = replace(
        extraction,
        material_associations=(first, second_entity, parallel_occurrence),
    )

    graph = _builder(extraction).build_product_backbone()

    assert graph.count_label("IfcMaterial") == 2
    material_nodes = [
        node for node in graph.nodes.values() if "IfcMaterial" in node["labels"]
    ]
    assert {node["props"]["name"] for node in material_nodes} == {"钢材"}
    assert {node["props"]["materialStepId"] for node in material_nodes} == {31, 32}
    same_pair_edges = [
        edge
        for edge in graph.edges_of_type("hasMaterial")
        if edge["src"] == first.component_id and edge["tgt"] == first.material_id
    ]
    assert {edge["occurrenceId"] for edge in same_pair_edges} == {
        first.id,
        parallel_occurrence.id,
    }
    detailed = next(
        edge for edge in same_pair_edges if edge["occurrenceId"] == parallel_occurrence.id
    )["props"]
    assert detailed == {
        "associationRecordId": parallel_occurrence.id,
        "associationStepId": 43,
        "sourceKind": "occurrence",
        "containerKind": "IfcMaterialLayerSetUsage",
        "containerStepId": 60,
        "itemStepId": 61,
        "layerOrConstituentIndex": 0,
        "thickness": 125.0,
        "thicknessUnit": "mm",
        "constituentFraction": 0.75,
    }


def test_each_qto_leaf_preserves_raw_and_normalized_fields():
    extraction, _ = _one_material_fixture()
    first = extraction.design_quantities[0]
    second = DesignQuantityRecord(
        ifc_hash=IFC_SHA,
        component_id=first.component_id,
        quantity_step_id=52,
        qto_set_step_id=50,
        qto_set_name="Qto_BeamBaseQuantities",
        quantity_name="Length",
        quantity_subtype="IfcQuantityLength",
        source_value=1500.0,
        source_unit="mm",
        normalized_value=1.5,
        normalized_unit="m",
    )
    extraction = replace(extraction, design_quantities=(first, second))

    graph = _builder(extraction).build_product_backbone()

    assert graph.count_label("DesignQuantity") == 2
    assert graph.count_relation("hasDesignQuantity") == 2
    props = graph.nodes[second.id]["props"]
    assert props["quantityStepId"] == 52
    assert props["qtoSetStepId"] == 50
    assert props["qtoSetName"] == "Qto_BeamBaseQuantities"
    assert props["quantityName"] == "Length"
    assert props["quantitySubtype"] == "IfcQuantityLength"
    assert (props["rawValue"], props["rawUnit"]) == (1500.0, "mm")
    assert (props["normalizedValue"], props["normalizedUnit"]) == (1.5, "m")


def test_component_type_is_created_only_for_a_real_ifc_type_object():
    extraction, _ = _one_material_fixture()
    untyped = extraction.components[0]
    real_type = ComponentTypeRecord(
        ifc_hash=IFC_SHA,
        global_id="2FixtureBeamType",
        step_id=70,
        ifc_class="IfcBeamType",
        name="Real reusable beam type",
        description="Native IfcTypeObject",
        tag="BT-70",
    )
    typed = ComponentRecord(
        ifc_hash=IFC_SHA,
        global_id="3TypedFixtureComponent",
        step_id=22,
        ifc_class="IfcBeam",
        name="Typed beam",
        object_type="display text must not synthesize a type",
        predefined_type="BEAM",
        component_type=real_type,
    )
    extraction = replace(extraction, components=(untyped, typed))

    graph = _builder(extraction).build_product_backbone()

    assert graph.count_label("ComponentType") == 1
    assert graph.count_relation("hasComponentType") == 1
    edge = graph.edges_of_type("hasComponentType")[0]
    assert (edge["src"], edge["tgt"]) == (typed.id, real_type.id)
    assert graph.nodes[real_type.id]["props"]["stepId"] == 70
    assert all(
        edge["src"] != untyped.id for edge in graph.edges_of_type("hasComponentType")
    )


def test_derived_from_uses_exact_distinct_operand_design_quantity_ids():
    extraction, _ = _one_material_fixture()
    first = extraction.design_quantities[0]
    second = DesignQuantityRecord(
        ifc_hash=IFC_SHA,
        component_id=first.component_id,
        quantity_step_id=52,
        qto_set_step_id=50,
        qto_set_name=first.qto_set_name,
        quantity_name="AllocationFractionEvidence",
        quantity_subtype="IfcQuantityNumber",
        source_value=0.5,
        source_unit="number",
        normalized_value=0.5,
        normalized_unit="number",
    )
    extraction = replace(extraction, design_quantities=(first, second))
    association = extraction.material_associations[0]
    validation = validate_material_candidate(
        ConsumptionCandidate(
            record_id="material-record:fraction",
            kind="material",
            source_record_id=association.id,
            evidence_source_id=f"ifc:{IFC_SHA}",
            product_target_id=first.component_id,
            material_id=association.material_id,
            recorded_scope=SCOPE,
            requested_scope=SCOPE,
            factor=_factor(),
            factor_resolution_reason="",
            formula_code="fraction_of_design_quantity",
            operands=(
                QuantityOperand(
                    operand_id="operand:parent",
                    role="parent_design_quantity",
                    value=20.0,
                    unit="kg",
                    source_id=first.id,
                    design_quantity_id=first.id,
                ),
                QuantityOperand(
                    operand_id="operand:fraction",
                    role="fraction",
                    value=0.5,
                    unit="1",
                    source_id=second.id,
                    design_quantity_id=second.id,
                ),
            ),
            measured=True,
        )
    )
    assert validation.issue is None
    accepted = validation.accepted
    assert accepted is not None
    accepted = replace(accepted, operands=accepted.operands + (accepted.operands[0],))

    graph = _builder(extraction, (accepted,)).build()

    derived = graph.edges_of_type("derivedFrom")
    assert len(derived) == 2
    assert {edge["tgt"] for edge in derived} == {first.id, second.id}
    assert all(edge["src"] == accepted.quantity_id for edge in derived)


def test_material_population_is_idempotent_and_conflicts_do_not_overwrite():
    extraction, accepted = _one_material_fixture()
    builder = _builder(extraction, (accepted,))
    graph = builder.build()
    expected = (deepcopy(graph.nodes), deepcopy(graph.edges))

    builder.populate_accepted_material(accepted)
    assert (graph.nodes, graph.edges) == expected

    conflicting = replace(
        accepted,
        emission_value=accepted.emission_value + 1.0,
    )
    with pytest.raises(NodeIdCollisionError, match="conflicting properties"):
        builder.populate_accepted_material(conflicting)

    assert (graph.nodes, graph.edges) == expected

    alternate_factor = replace(
        accepted.factor,
        factor_id="factor:alternate-row",
        source_row_id="workbook:sheet:row:alternate",
    )
    conflicting_link = replace(
        accepted,
        factor=alternate_factor,
        factor_id=alternate_factor.factor_id,
        factor_source_id=alternate_factor.source_row_id,
    )
    with pytest.raises(NodeIdCollisionError, match="conflicting properties"):
        builder.populate_accepted_material(conflicting_link)

    assert (graph.nodes, graph.edges) == expected


def test_factor_is_shared_only_for_the_same_strict_source_identity_and_conflicts_are_atomic():
    extraction, first = _one_material_fixture()
    association = extraction.material_associations[0]
    design_quantity = extraction.design_quantities[0]

    def accepted_for(
        record_id: str,
        source_record_id: str,
        factor_value: float,
        factor_alias: str,
    ):
        factor = replace(_factor(factor_value), factor_id=factor_alias)
        result = validate_material_candidate(
            ConsumptionCandidate(
                record_id=record_id,
                kind="material",
                source_record_id=source_record_id,
                evidence_source_id=f"ifc:{IFC_SHA}",
                product_target_id=association.component_id,
                material_id=association.material_id,
                recorded_scope=SCOPE,
                requested_scope=SCOPE,
                factor=factor,
                factor_resolution_reason="",
                formula_code="direct_mass",
                operands=(
                    QuantityOperand(
                        operand_id=f"operand:{record_id}",
                        role="material_mass",
                        value=4.0,
                        unit="kg",
                        source_id=design_quantity.id,
                        design_quantity_id=design_quantity.id,
                    ),
                ),
                measured=True,
            )
        )
        assert result.issue is None
        assert result.accepted is not None
        return result.accepted

    same_factor = accepted_for(
        "material-record:second",
        association.id,
        2.5,
        "factor:alias-for-the-same-row",
    )
    assert same_factor.factor_id != first.factor_id
    assert same_factor.factor_source_id == first.factor_source_id
    graph = _builder(extraction, (first, same_factor)).build()
    assert graph.count_label("MaterialConsumption") == 2
    assert graph.count_label("ConsumptionQuantity") == 2
    assert graph.count_label("CarbonEmission") == 2
    assert graph.count_label("EmissionFactor") == 1
    assert graph.count_relation("hasFactor") == 2
    assert {edge["tgt"] for edge in graph.edges_of_type("hasFactor")} == {
        _factor_node_id(first)
    }

    builder = _builder(extraction, (first,))
    graph = builder.build()
    before = (deepcopy(graph.nodes), deepcopy(graph.edges))
    conflicting_factor = accepted_for(
        "material-record:conflicting-factor",
        association.id,
        3.0,
        "factor:another-alias-for-the-same-row",
    )
    assert conflicting_factor.factor_source_id == first.factor_source_id

    with pytest.raises(NodeIdCollisionError, match="conflicting properties"):
        builder.populate_accepted_material(conflicting_factor)

    assert (graph.nodes, graph.edges) == before


def test_factor_source_identity_must_match_immutable_snapshot_before_mutation():
    extraction, accepted = _one_material_fixture()
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    before = _graph_bytes(graph)
    mismatched = replace(
        accepted,
        factor_source_id="workbook:sheet:row:mismatch",
    )

    with pytest.raises(ValueError, match="factor_source_id.*source_row_id"):
        builder.populate_accepted_material(mismatched)

    assert _graph_bytes(graph) == before


def test_planned_calculation_node_collision_rolls_back_byte_for_byte():
    extraction, accepted = _one_material_fixture()
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    graph_identity = id(graph)
    before = _graph_bytes(graph)
    colliding = replace(accepted, quantity_id=accepted.consumption_id)

    with pytest.raises(NodeIdCollisionError, match="application labels"):
        builder.populate_accepted_material(colliding)

    assert id(builder.graph) == graph_identity
    assert _graph_bytes(graph) == before


def test_seeded_relationship_occurrence_collision_rolls_back_byte_for_byte():
    extraction, accepted = _one_material_fixture()
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    graph.add_node(accepted.consumption_id, ["MaterialConsumption"], {})
    occurrence_id = stable_id(
        "MaterialCalculationRelationship",
        accepted.consumption_id,
        "recordedForObject",
        accepted.product_target_id,
    )
    graph.add_edge(
        accepted.consumption_id,
        "recordedForObject",
        accepted.product_target_id,
        {"recordId": "seeded-conflicting-record"},
        occurrence_id=occurrence_id,
    )
    graph_identity = id(graph)
    before = _graph_bytes(graph)

    with pytest.raises(ValueError, match="edge occurrence.*conflicting content"):
        builder.populate_accepted_material(accepted)

    assert id(builder.graph) == graph_identity
    assert _graph_bytes(graph) == before


@pytest.mark.parametrize(
    ("violation", "message"),
    [
        ("material", "hasMaterial"),
        ("association", "source_record_id"),
        ("quantity", "hasDesignQuantity"),
    ],
)
def test_material_and_design_quantity_evidence_must_belong_to_product_component(
    violation: str, message: str
):
    extraction, accepted = _one_material_fixture()
    first_component = extraction.components[0]
    second_component = ComponentRecord(
        ifc_hash=IFC_SHA,
        global_id="3SecondFixtureComponent",
        step_id=22,
        ifc_class="IfcColumn",
        name="Column 乙",
    )
    second_association = MaterialAssociationRecord(
        ifc_hash=IFC_SHA,
        component_id=second_component.id,
        material_step_id=32,
        association_step_id=42,
        source_kind="occurrence",
        container_kind="IfcMaterial",
        container_step_id=32,
        item_step_id=None,
        layer_or_constituent_index=None,
        material_name="第二种材料",
    )
    second_quantity = DesignQuantityRecord(
        ifc_hash=IFC_SHA,
        component_id=second_component.id,
        quantity_step_id=62,
        qto_set_step_id=60,
        qto_set_name="Qto_ColumnBaseQuantities",
        quantity_name="NetWeight",
        quantity_subtype="IfcQuantityWeight",
        source_value=8.0,
        source_unit="kg",
        normalized_value=8.0,
        normalized_unit="kg",
    )
    extraction = replace(
        extraction,
        components=(first_component, second_component),
        material_associations=extraction.material_associations + (second_association,),
        design_quantities=extraction.design_quantities + (second_quantity,),
    )
    if violation == "material":
        invalid = replace(
            accepted,
            material_id=second_association.material_id,
            source_record_id=second_association.id,
        )
    elif violation == "association":
        invalid = replace(accepted, source_record_id=second_association.id)
    else:
        invalid = replace(
            accepted,
            operands=(
                replace(
                    accepted.operands[0],
                    design_quantity_id=second_quantity.id,
                ),
            ),
        )
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    before = _graph_bytes(graph)

    with pytest.raises(ValueError, match=message):
        builder.populate_accepted_material(invalid)

    assert _graph_bytes(graph) == before


def test_backbone_collision_rolls_back_graph_and_builder_state():
    extraction, _ = _one_material_fixture()
    first = extraction.material_associations[0]
    conflicting = MaterialAssociationRecord(
        ifc_hash=IFC_SHA,
        component_id=first.component_id,
        material_step_id=first.material_step_id,
        association_step_id=99,
        source_kind="occurrence",
        container_kind="IfcMaterial",
        container_step_id=first.material_step_id,
        item_step_id=None,
        layer_or_constituent_index=None,
        material_name="conflicting material content",
    )
    conflicting_extraction = replace(
        extraction,
        material_associations=(first, conflicting),
    )
    builder = MultiGranularCarbonKGBuilder(
        ifc_path=Path("fixture.ifc"),
        factor_library=FactorLibrary(None),
        module_name="Fixture module",
        module_identity="case-manifest:fixture-module",
    )
    builder.component_node_by_gid["sentinel"] = "component:sentinel"
    builder.stats["sentinel"] = 7
    graph = builder.graph
    graph_identity = id(graph)
    before_graph = _graph_bytes(graph)
    before_components = deepcopy(builder.component_node_by_gid)
    before_stats = deepcopy(builder.stats)
    before_extraction = builder.extraction_result
    before_module_id = builder.module_id

    with pytest.raises(NodeIdCollisionError, match="conflicting properties"):
        builder.build_product_backbone(conflicting_extraction)

    assert id(builder.graph) == graph_identity
    assert _graph_bytes(graph) == before_graph
    assert builder.component_node_by_gid == before_components
    assert builder.stats == before_stats
    assert builder.extraction_result is before_extraction
    assert builder.module_id == before_module_id


def test_valid_measured_material_zero_is_a_real_unblocked_graph_fact():
    extraction, _ = _one_material_fixture()
    association = extraction.material_associations[0]
    design_quantity = extraction.design_quantities[0]
    result = validate_material_candidate(
        ConsumptionCandidate(
            record_id="material-record:valid-zero",
            kind="material",
            source_record_id=association.id,
            evidence_source_id=f"ifc:{IFC_SHA}",
            product_target_id=association.component_id,
            material_id=association.material_id,
            recorded_scope=SCOPE,
            requested_scope=SCOPE,
            factor=_factor(),
            factor_resolution_reason="",
            formula_code="direct_mass",
            operands=(
                QuantityOperand(
                    operand_id="operand:valid-zero",
                    role="material_mass",
                    value=0.0,
                    unit="kg",
                    source_id=design_quantity.id,
                    design_quantity_id=design_quantity.id,
                ),
            ),
            measured=True,
        )
    )
    assert result.issue is None
    accepted = result.accepted
    assert accepted is not None and accepted.is_valid_zero

    graph = _builder(extraction, (accepted,)).build()

    assert graph.count_label("CarbonEmission") == 1
    props = graph.nodes[accepted.emission_id]["props"]
    assert props["emissionValue"] == 0.0
    assert props["isValidZero"] is True
    assert not _contains_blocked(props)


@pytest.mark.parametrize("missing_reference", ["component", "material", "quantity"])
def test_missing_reference_fails_before_any_calculation_node_is_added(
    missing_reference: str,
):
    extraction, accepted = _one_material_fixture()
    if missing_reference == "component":
        accepted = replace(accepted, product_target_id="BuildingComponent:missing")
    elif missing_reference == "material":
        accepted = replace(accepted, material_id="IfcMaterial:missing")
    else:
        accepted = replace(
            accepted,
            operands=(
                replace(
                    accepted.operands[0],
                    design_quantity_id="DesignQuantity:missing",
                ),
            ),
        )
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    before = (deepcopy(graph.nodes), deepcopy(graph.edges))

    with pytest.raises(ValueError, match=f"missing .*{missing_reference.capitalize()}"):
        builder.populate_accepted_material(accepted)

    assert _calculation_counts(graph) == {
        "MaterialConsumption": 0,
        "ConsumptionQuantity": 0,
        "EmissionFactor": 0,
        "CarbonEmission": 0,
    }
    assert (graph.nodes, graph.edges) == before


def test_modular_unit_requires_explicit_identity_and_never_uses_a_name_slug():
    extraction, _ = _one_material_fixture()
    implicit = _builder(extraction).build_product_backbone()
    assert implicit.count_label("ModularUnit") == 0
    assert implicit.count_relation("containsComponent") == 0

    configured_builder = _builder(
        extraction,
        module_identity="case-manifest:模块/甲",
    )
    configured = configured_builder.build_product_backbone()

    assert configured.count_label("ModularUnit") == 1
    assert configured.count_relation("containsComponent") == 1
    module_id = configured_builder.module_id
    assert module_id is not None
    assert module_id.startswith("ModularUnit:")
    assert "Fixture_module" not in module_id
    assert configured.nodes[module_id]["props"]["sourceIdentity"] == "case-manifest:模块/甲"


def test_canonical_build_never_reads_or_calls_legacy_energy_factory_or_aggregate_paths(
    tmp_path: Path,
):
    extraction, accepted = _one_material_fixture()
    builder = MultiGranularCarbonKGBuilder(
        ifc_path=Path("fixture.ifc"),
        factor_library=FactorLibrary(None),
        module_name="Fixture module",
        energy_csv=tmp_path / "legacy-energy-must-not-be-read.csv",
        factory_xlsx=tmp_path / "p0_factory_synth_must-not-be-read.xlsx",
        factory_target_map=tmp_path / "legacy-target-map-must-not-be-read.json",
        extraction_result=extraction,
        accepted_materials=(accepted,),
    )

    for method_name in (
        "_add_design_backbone",
        "_attach_energy_records",
        "_load_factory_energy_inputs",
        "_resolve_factory_target",
        "_create_aggregates",
    ):
        assert not hasattr(builder, method_name)

    graph = builder.build()

    assert graph.count_label("MaterialConsumption") == 1
    assert graph.count_label("EnergyConsumption") == 0
    assert graph.count_label("AggregateCarbonEmission") == 0
    assert graph.count_label("FactoryTarget") == 0


def test_actual_ifc_backbone_preserves_canonical_cardinalities():
    actual_ifc = Path(__file__).resolve().parents[1] / "typed_completed.ifc"
    extraction = CanonicalIFCExtractor(actual_ifc).extract()
    expected_type_ids = {
        component.component_type.id
        for component in extraction.components
        if component.component_type is not None
    }
    expected_type_assignments = sum(
        component.component_type is not None for component in extraction.components
    )

    graph = MultiGranularCarbonKGBuilder(
        ifc_path=actual_ifc,
        factor_library=FactorLibrary(None),
        module_name="Actual case fixture",
        extraction_result=extraction,
    ).build_product_backbone()

    assert graph.count_label("BuildingComponent") == 729
    assert graph.count_label("DesignQuantity") == 3383
    assert graph.count_label("IfcMaterial") == 48
    assert graph.count_relation("hasMaterial") == 1134
    assert graph.count_label("ComponentType") == len(expected_type_ids)
    assert graph.count_relation("hasComponentType") == expected_type_assignments
    for excluded_label in (
        "IfcFurniture",
        "IfcAlarm",
        "IfcFireSuppressionTerminal",
        "IfcAirTerminal",
        "IfcValve",
    ):
        assert graph.count_label(excluded_label) == 0
