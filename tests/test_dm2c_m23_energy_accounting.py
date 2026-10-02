from dataclasses import FrozenInstanceError, replace
import json
import math
from pathlib import Path

import pytest

from dm2c_m23_accounting import (
    EnergyAllocationTarget,
    EnergyPopulationPlan,
    ProductContribution,
    ScopeAggregationError,
    allocated_contribution_key,
    component_total,
    contribution_value,
    module_total,
    product_contributions,
    source_process_total,
)
from dm2c_m23_calculation import (
    AcceptedConsumption,
    ConsumptionCandidate,
    QuantityOperand,
    ValidationIssue,
    validate_energy_candidate,
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
    ComponentRecord,
    IFCExtractionResult,
    MaterialAssociationRecord,
)
from dm2c_multigranular_carbon_kg import (
    FactorLibrary,
    FactorRecord,
    MultiGranularCarbonKGBuilder,
)


IFC_SHA = "B" * 64
SCOPE = "A1-A3"
CARRIER_ID = stable_id("EnergyCarrier", "electricity")


def _factor(value: float = 0.5, *, source_row_id: str = "energy-factors:row:7") -> FactorRecord:
    return FactorRecord(
        factor_id=f"factor:{source_row_id}",
        keyword="grid electricity",
        factor_value=value,
        factor_unit="kgCO2e/kWh",
        source="fixture factor workbook",
        match_status="accepted_strict_v2",
        confidence=1.0,
        source_row_id=source_row_id,
        original_factor_unit="kgCO2e/kWh",
        denominator_unit="kWh",
        normalized_factor_value=value,
        normalized_denominator="kWh",
        geography="Hong Kong",
        year="2026",
        system_boundary=SCOPE,
        source_reference="doi:fixture-energy-factor",
        proxy_status="primary",
        validation_status="accepted",
    )


def _fixture() -> tuple[IFCExtractionResult, tuple[ComponentRecord, ComponentRecord]]:
    components = (
        ComponentRecord(
            ifc_hash=IFC_SHA,
            global_id="3EnergyComponentA",
            step_id=21,
            ifc_class="IfcBeam",
            name="Energy component A",
        ),
        ComponentRecord(
            ifc_hash=IFC_SHA,
            global_id="3EnergyComponentB",
            step_id=22,
            ifc_class="IfcColumn",
            name="Energy component B",
        ),
    )
    return (
        IFCExtractionResult(
            ifc_sha256=IFC_SHA,
            candidate_count=2,
            excluded_count=0,
            excluded_type_counts=(),
            components=components,
            material_associations=(),
            design_quantities=(),
        ),
        components,
    )


def _material_energy_fixture() -> tuple[
    IFCExtractionResult,
    tuple[ComponentRecord, ComponentRecord],
    AcceptedConsumption,
]:
    extraction, components = _fixture()
    association = MaterialAssociationRecord(
        ifc_hash=IFC_SHA,
        component_id=components[0].id,
        material_step_id=31,
        association_step_id=41,
        source_kind="occurrence",
        container_kind="IfcMaterial",
        container_step_id=31,
        item_step_id=None,
        layer_or_constituent_index=None,
        material_name="factor identity fixture",
    )
    extraction = replace(extraction, material_associations=(association,))
    validation = validate_material_candidate(
        ConsumptionCandidate(
            record_id="material-record:factor-identity",
            kind="material",
            source_record_id=association.id,
            evidence_source_id=f"ifc:{IFC_SHA}",
            product_target_id=components[0].id,
            material_id=association.material_id,
            recorded_scope=SCOPE,
            requested_scope=SCOPE,
            factor=_factor(),
            factor_resolution_reason="",
            formula_code="direct_mass",
            operands=(
                QuantityOperand(
                    operand_id="operand:material-factor-identity",
                    role="material_mass",
                    value=2.0,
                    unit="kWh",
                    source_id=association.id,
                ),
            ),
            measured=True,
        )
    )
    assert validation.issue is None
    assert validation.accepted is not None
    return extraction, components, validation.accepted


def _accepted_energy(
    *,
    record_id: str = "energy-record:7",
    product_target_id: str | None = None,
    value: float = 20.0,
    unit: str = "kWh",
    factor: FactorRecord | None = None,
    measured: bool = True,
) -> AcceptedConsumption | ValidationIssue:
    result = validate_energy_candidate(
        ConsumptionCandidate(
            record_id=record_id,
            kind="energy",
            source_record_id=f"source:{record_id}",
            evidence_source_id="factory-meter:fixture",
            product_target_id=product_target_id,
            recorded_scope=SCOPE,
            requested_scope=SCOPE,
            factor=factor or _factor(),
            factor_resolution_reason="",
            formula_code="measured_energy",
            operands=(
                QuantityOperand(
                    operand_id=f"operand:{record_id}",
                    role="energy_quantity",
                    value=value,
                    unit=unit,
                    source_id=f"meter-reading:{record_id}",
                ),
            ),
            measured=measured,
            energy_carrier_id=CARRIER_ID,
        )
    )
    return result.accepted if result.accepted is not None else result.issue


def _builder(extraction: IFCExtractionResult, **kwargs) -> MultiGranularCarbonKGBuilder:
    return MultiGranularCarbonKGBuilder(
        ifc_path=Path("fixture.ifc"),
        factor_library=FactorLibrary(None),
        module_name="Fixture module",
        module_identity="fixture-module-1",
        extraction_result=extraction,
        **kwargs,
    )


def _graph_bytes(graph: CanonicalLPGGraph) -> bytes:
    return json.dumps(
        {"nodes": graph.nodes, "edges": graph.edges, "edgeIndex": graph._edges_by_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _edge(graph: CanonicalLPGGraph, relation: str, source: str):
    return next(
        edge
        for edge in graph.edges_of_type(relation)
        if edge["src"] == source
    )


def test_population_records_are_frozen_and_tuple_normalized():
    target = EnergyAllocationTarget(
        target_component_id="component:a",
        raw_weight=3.0,
        raw_weight_unit="kg",
        normalized_weight=0.6,
        evidence_record_id="evidence:a",
    )
    plan = EnergyPopulationPlan(
        attribution_mode="allocated",
        allocation_set_id="allocation:set:1",
        allocation_basis="mass",
        allocations=[target],
        process_node_ids=["process:1"],
        resource_node_ids=["resource:1"],
    )

    assert plan.allocations == (target,)
    assert plan.process_node_ids == ("process:1",)
    assert plan.resource_node_ids == ("resource:1",)
    with pytest.raises(FrozenInstanceError):
        target.raw_weight = 4.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.allocation_basis = "volume"  # type: ignore[misc]
    with pytest.raises(ValueError):
        EnergyPopulationPlan(attribution_mode="guess")  # type: ignore[arg-type]
    contribution = ProductContribution(
        key=["direct", "emission:1"],  # type: ignore[arg-type]
        emission_id="emission:1",
        component_id="component:a",
        value=1.0,
        scope=SCOPE,
    )
    assert contribution.key == ("direct", "emission:1")
    with pytest.raises(FrozenInstanceError):
        contribution.value = 2.0  # type: ignore[misc]


def test_direct_energy_creates_one_complete_canonical_fact_and_product_total():
    extraction, components = _fixture()
    accepted = _accepted_energy(product_target_id=components[0].id)
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()

    builder.populate_accepted_energy(
        accepted,
        EnergyPopulationPlan(attribution_mode="direct"),
    )

    assert graph.count_label("EnergyConsumption") == 1
    assert graph.count_label("ConsumptionQuantity") == 1
    assert graph.count_label("EmissionFactor") == 1
    assert graph.count_label("EnergyCarrier") == 1
    assert graph.count_label("CarbonEmission") == 1
    assert graph.count_relation("hasQuantity") == 1
    assert graph.count_relation("hasFactor") == 1
    assert graph.count_relation("ofCarrier") == 1
    assert graph.count_relation("hasCarbonDriver") == 1
    assert graph.count_relation("recordedForObject") == 1
    recorded_for = _edge(graph, "recordedForObject", accepted.consumption_id)
    assert recorded_for["tgt"] == components[0].id
    assert recorded_for["props"].get("attributionMode") != "allocated"
    assert "normalizedWeight" not in recorded_for["props"]
    assert stable_id("EmissionFactor", accepted.factor_source_id) in graph.nodes
    assert accepted.energy_carrier_id in graph.nodes
    assert graph.nodes[accepted.emission_id]["props"]["emissionValue"] == pytest.approx(10.0)
    assert product_contributions(graph, components[0].id) == pytest.approx((10.0,))
    assert component_total(graph, components[0].id) == pytest.approx(10.0)


def test_allocated_energy_is_one_source_fact_with_weighted_edges_and_no_normalization():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    targets = (
        EnergyAllocationTarget(components[0].id, 3.0, "kg", 0.6, "allocation-row:a"),
        EnergyAllocationTarget(components[1].id, 2.0, "kg", 0.4, "allocation-row:b"),
    )
    plan = EnergyPopulationPlan(
        attribution_mode="allocated",
        allocation_set_id="allocation:set:fixture",
        allocation_basis="mass",
        allocations=targets,
    )

    builder.populate_accepted_energy(accepted, plan)

    assert graph.count_label("EnergyConsumption") == 1
    assert graph.count_label("CarbonEmission") == 1
    allocation_edges = [
        edge
        for edge in graph.edges_of_type("recordedForObject")
        if edge["src"] == accepted.consumption_id
    ]
    assert len(allocation_edges) == 2
    assert {
        edge["tgt"]: edge["props"]["allocatedFraction"]
        for edge in allocation_edges
    } == {components[0].id: 0.6, components[1].id: 0.4}
    for edge, expected in zip(
        sorted(allocation_edges, key=lambda row: row["tgt"]),
        sorted(targets, key=lambda row: row.target_component_id),
    ):
        assert edge["props"] == {
            "allocated": True,
            "allocationSetId": "allocation:set:fixture",
            "allocationBasis": "mass",
            "rawWeight": expected.raw_weight,
            "rawWeightUnit": expected.raw_weight_unit,
            "allocatedFraction": expected.normalized_weight,
            "evidenceRecordId": expected.evidence_record_id,
        }
        assert allocated_contribution_key(accepted.emission_id, edge) == (
            accepted.emission_id,
            "allocation:set:fixture",
            expected.target_component_id,
        )
        assert contribution_value(accepted.emission_value, edge) == pytest.approx(
            accepted.emission_value * expected.normalized_weight
        )
    assert component_total(graph, components[0].id) == pytest.approx(6.0)
    assert component_total(graph, components[1].id) == pytest.approx(4.0)
    assert module_total(graph, builder.module_id) == pytest.approx(10.0)


def test_process_only_energy_has_no_product_edge_but_counts_once_at_source_process():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    process_id = stable_id("ProductionStage", "fixture-stage")
    resource_id = stable_id("ManufacturingResource", "fixture-meter")
    graph.add_node(process_id, ["ProductionStage"], {"evidenceRecordId": "stage-row:1"})
    graph.add_node(resource_id, ["ManufacturingResource"], {"evidenceRecordId": "meter-row:1"})

    builder.populate_accepted_energy(
        accepted,
        EnergyPopulationPlan(
            attribution_mode="process_only",
            process_node_ids=[process_id],
            resource_node_ids=[resource_id],
        ),
    )

    assert not [
        edge
        for edge in graph.edges_of_type("recordedForObject")
        if edge["src"] == accepted.consumption_id
    ]
    assert _edge(graph, "associatedWithProcess", accepted.consumption_id)["tgt"] == process_id
    assert _edge(graph, "recordedForResource", accepted.consumption_id)["tgt"] == resource_id
    assert product_contributions(graph) == ()
    assert component_total(graph, components[0].id) == 0.0
    assert source_process_total(graph) == pytest.approx(accepted.emission_value)


@pytest.mark.parametrize(
    "plan",
    (
        EnergyPopulationPlan(attribution_mode="allocated"),
        EnergyPopulationPlan(
            attribution_mode="allocated",
            allocation_set_id="allocation:set:bad-sum",
            allocation_basis="mass",
            allocations=(
                EnergyAllocationTarget("placeholder:a", 1.0, "kg", 0.7, "row:a"),
                EnergyAllocationTarget("placeholder:b", 1.0, "kg", 0.2, "row:b"),
            ),
        ),
        EnergyPopulationPlan(attribution_mode="process_only"),
    ),
)
def test_invalid_population_is_transactional(plan: EnergyPopulationPlan):
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    if plan.allocations:
        plan = EnergyPopulationPlan(
            attribution_mode="allocated",
            allocation_set_id=plan.allocation_set_id,
            allocation_basis=plan.allocation_basis,
            allocations=(
                EnergyAllocationTarget(components[0].id, 1.0, "kg", 0.7, "row:a"),
                EnergyAllocationTarget(components[1].id, 1.0, "kg", 0.2, "row:b"),
            ),
        )
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    before = _graph_bytes(graph)

    with pytest.raises((TypeError, ValueError)):
        builder.populate_accepted_energy(accepted, plan)

    assert _graph_bytes(graph) == before


def test_invalid_zero_is_never_populated():
    extraction, components = _fixture()
    rejected = _accepted_energy(
        product_target_id=components[0].id,
        value=0.0,
        measured=False,
    )
    assert isinstance(rejected, ValidationIssue)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    before = _graph_bytes(graph)

    with pytest.raises(TypeError):
        builder.populate_accepted_energy(
            rejected,  # type: ignore[arg-type]
            EnergyPopulationPlan(attribution_mode="direct"),
        )

    assert _graph_bytes(graph) == before


def test_valid_measured_zero_is_a_real_energy_fact():
    extraction, components = _fixture()
    accepted = _accepted_energy(
        record_id="energy:measured-zero",
        product_target_id=components[0].id,
        value=0.0,
        measured=True,
    )
    assert isinstance(accepted, AcceptedConsumption)
    assert accepted.is_valid_zero is True
    builder = _builder(extraction)
    graph = builder.build_product_backbone()

    builder.populate_accepted_energy(
        accepted, EnergyPopulationPlan(attribution_mode="direct")
    )

    assert graph.count_label("EnergyConsumption") == 1
    assert graph.count_label("CarbonEmission") == 1
    assert graph.nodes[accepted.emission_id]["props"]["isValidZero"] is True
    assert component_total(graph, components[0].id) == 0.0
    assert source_process_total(graph) == 0.0


def test_wh_and_mwh_keep_explicit_conversion_provenance_and_correct_emissions():
    extraction, components = _fixture()
    wh = _accepted_energy(
        record_id="energy:wh",
        product_target_id=components[0].id,
        value=1000.0,
        unit="Wh",
    )
    mwh = _accepted_energy(
        record_id="energy:mwh",
        product_target_id=components[1].id,
        value=1.0,
        unit="MWh",
    )
    assert isinstance(wh, AcceptedConsumption)
    assert isinstance(mwh, AcceptedConsumption)
    assert (wh.quantity_value, wh.emission_value) == pytest.approx((1.0, 0.5))
    assert wh.quantity_unit == "kWh"
    assert (mwh.quantity_value, mwh.emission_value) == pytest.approx((1000.0, 500.0))
    assert mwh.quantity_unit == "kWh"
    builder = _builder(
        extraction,
        accepted_energies=(
            (wh, EnergyPopulationPlan(attribution_mode="direct")),
            (mwh, EnergyPopulationPlan(attribution_mode="direct")),
        ),
    )

    graph = builder.build()

    wh_steps = [
        json.loads(value)
        for value in graph.nodes[wh.quantity_id]["props"]["conversionSteps"]
    ]
    mwh_steps = [
        json.loads(value)
        for value in graph.nodes[mwh.quantity_id]["props"]["conversionSteps"]
    ]
    assert wh_steps[-1] == {
        "operand_id": wh.conversion_steps[-1].operand_id,
        "role": "calculated_quantity",
        "source_id": "factory-meter:fixture",
        "source_value": 1000.0,
        "source_unit": "Wh",
        "normalized_value": 1.0,
        "normalized_unit": "kWh",
        "conversion_factor": 0.001,
        "design_quantity_id": None,
    }
    assert mwh_steps[-1]["source_unit"] == "MWh"
    assert mwh_steps[-1]["normalized_value"] == pytest.approx(1000.0)
    assert graph.count_label("EmissionFactor") == 1


def test_allocation_validation_rejects_bad_rows_without_mutation():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    direct_accepted = _accepted_energy(product_target_id=components[0].id)
    assert isinstance(accepted, AcceptedConsumption)
    assert isinstance(direct_accepted, AcceptedConsumption)
    valid_a = EnergyAllocationTarget(components[0].id, 1.0, "kg", 0.5, "row:a")
    valid_b = EnergyAllocationTarget(components[1].id, 1.0, "kg", 0.5, "row:b")
    bad_plans = (
        (accepted, EnergyPopulationPlan("allocated", "", "mass", (valid_a, valid_b))),
        (accepted, EnergyPopulationPlan("allocated", "set:1", "", (valid_a, valid_b))),
        (
            accepted,
            EnergyPopulationPlan(
                "allocated",
                "set:1",
                "mass",
                (replace(valid_a, raw_weight=-1.0), valid_b),
            ),
        ),
        (
            accepted,
            EnergyPopulationPlan(
                "allocated",
                "set:1",
                "mass",
                (replace(valid_a, normalized_weight=math.nan), valid_b),
            ),
        ),
        (
            accepted,
            EnergyPopulationPlan(
                "allocated",
                "set:1",
                "mass",
                (replace(valid_a, evidence_record_id=""), valid_b),
            ),
        ),
        (
            accepted,
            EnergyPopulationPlan(
                "allocated",
                "set:1",
                "mass",
                (valid_a, replace(valid_b, target_component_id=components[0].id)),
            ),
        ),
        (
            direct_accepted,
            EnergyPopulationPlan("allocated", "set:1", "mass", (valid_a, valid_b)),
        ),
        (
            accepted,
            EnergyPopulationPlan(
                "direct",
                allocation_set_id="set:1",
                allocation_basis="mass",
                allocations=(valid_a, valid_b),
            ),
        ),
    )

    for fact, plan in bad_plans:
        builder = _builder(extraction)
        graph = builder.build_product_backbone()
        before = _graph_bytes(graph)
        with pytest.raises((TypeError, ValueError)):
            builder.populate_accepted_energy(fact, plan)
        assert _graph_bytes(graph) == before


def test_repopulation_rejects_conflicting_allocation_evidence_atomically():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    original = EnergyPopulationPlan(
        "allocated",
        "allocation:set:stable",
        "mass",
        (
            EnergyAllocationTarget(components[0].id, 3.0, "kg", 0.6, "row:a"),
            EnergyAllocationTarget(components[1].id, 2.0, "kg", 0.4, "row:b"),
        ),
    )
    builder.populate_accepted_energy(accepted, original)
    before = _graph_bytes(graph)
    builder.populate_accepted_energy(accepted, original)
    assert _graph_bytes(graph) == before
    conflicting = replace(
        original,
        allocations=(
            replace(original.allocations[0], evidence_record_id="row:a:changed"),
            replace(original.allocations[1], evidence_record_id="row:b:changed"),
        ),
    )

    with pytest.raises(ValueError, match="conflicting allocation evidence"):
        builder.populate_accepted_energy(accepted, conflicting)

    assert _graph_bytes(graph) == before
    assert len(
        [
            edge
            for edge in graph.edges_of_type("recordedForObject")
            if edge["src"] == accepted.consumption_id
        ]
    ) == 2


def test_repopulation_rejects_allocation_target_set_expansion_atomically():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    original = EnergyPopulationPlan(
        "allocated",
        "allocation:set:target-identity",
        "mass",
        (
            EnergyAllocationTarget(components[0].id, 1.0, "kg", 1.0, "row:a"),
        ),
    )
    builder.populate_accepted_energy(accepted, original)
    before = _graph_bytes(graph)
    expanded = replace(
        original,
        allocations=(
            original.allocations[0],
            EnergyAllocationTarget(components[1].id, 0.0, "kg", 0.0, "row:b"),
        ),
    )

    with pytest.raises(ValueError, match="target set"):
        builder.populate_accepted_energy(accepted, expanded)

    assert _graph_bytes(graph) == before


def test_process_only_rejects_wrong_context_without_creating_a_placeholder():
    extraction, _ = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    wrong_context_id = stable_id("BuildingComponent", "not-a-process")
    graph.add_node(wrong_context_id, ["BuildingComponent"], {"name": "wrong context"})
    before = _graph_bytes(graph)

    with pytest.raises(ValueError):
        builder.populate_accepted_energy(
            accepted,
            EnergyPopulationPlan(
                attribution_mode="process_only",
                process_node_ids=(wrong_context_id,),
            ),
        )

    assert _graph_bytes(graph) == before
    assert graph.count_label("ProductionStage") == 0
    assert graph.count_label("ManufacturingActivity") == 0
    assert graph.count_label("ManufacturingResource") == 0


def test_material_and_energy_share_factor_identity_and_query_views_deduplicate():
    extraction, components, material = _material_energy_fixture()
    energy = _accepted_energy(product_target_id=components[0].id)
    assert isinstance(energy, AcceptedConsumption)
    builder = _builder(extraction, accepted_materials=(material,))
    graph = builder.build()
    builder.populate_accepted_energy(
        energy, EnergyPopulationPlan(attribution_mode="direct")
    )

    factor_id = stable_id("EmissionFactor", material.factor_source_id)
    assert factor_id == stable_id("EmissionFactor", energy.factor_source_id)
    assert graph.count_label("EmissionFactor") == 1
    assert component_total(graph, components[0].id) == pytest.approx(11.0)
    assert source_process_total(graph) == pytest.approx(10.0)

    direct_edge = _edge(graph, "recordedForObject", energy.consumption_id)
    graph.add_edge(
        energy.consumption_id,
        "recordedForObject",
        components[0].id,
        direct_edge["props"],
        occurrence_id="deliberate-duplicate-direct-traversal",
    )
    assert builder.module_id is not None
    graph.add_edge(
        builder.module_id,
        "containsComponent",
        components[0].id,
        {"sourceIdentity": "fixture-module-1"},
        occurrence_id="deliberate-duplicate-module-traversal",
    )

    assert sorted(product_contributions(graph, components[0].id)) == pytest.approx(
        (1.0, 10.0)
    )
    assert component_total(graph, components[0].id) == pytest.approx(11.0)
    assert module_total(graph, builder.module_id) == pytest.approx(11.0)


def test_allocated_query_deduplicates_duplicate_edges_and_rejects_mixed_paths():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    plan = EnergyPopulationPlan(
        "allocated",
        "allocation:set:dedup",
        "mass",
        (
            EnergyAllocationTarget(components[0].id, 3.0, "kg", 0.6, "row:a"),
            EnergyAllocationTarget(components[1].id, 2.0, "kg", 0.4, "row:b"),
        ),
    )
    builder.populate_accepted_energy(accepted, plan)
    allocated_edge = next(
        edge
        for edge in graph.edges_of_type("recordedForObject")
        if edge["src"] == accepted.consumption_id
        and edge["tgt"] == components[0].id
    )
    graph.add_edge(
        accepted.consumption_id,
        "recordedForObject",
        components[0].id,
        allocated_edge["props"],
        occurrence_id="deliberate-duplicate-allocation-traversal",
    )

    assert component_total(graph, components[0].id) == pytest.approx(6.0)
    assert module_total(graph, builder.module_id) == pytest.approx(10.0)

    graph.add_edge(
        accepted.consumption_id,
        "recordedForObject",
        components[0].id,
        {"sourceRecordId": "malformed-direct-overlap"},
        occurrence_id="malformed-direct-overlap",
    )
    before_query = _graph_bytes(graph)
    with pytest.raises(ValueError, match="mixes direct and allocated"):
        product_contributions(graph)
    assert _graph_bytes(graph) == before_query


def test_query_rejects_process_only_mode_with_a_product_path():
    extraction, components = _fixture()
    accepted = _accepted_energy(product_target_id=components[0].id)
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    builder.populate_accepted_energy(
        accepted, EnergyPopulationPlan(attribution_mode="direct")
    )
    graph.nodes[accepted.consumption_id]["props"]["attributionMode"] = "process_only"
    before_query = _graph_bytes(graph)

    with pytest.raises(ValueError, match="process-only"):
        product_contributions(graph)

    assert _graph_bytes(graph) == before_query


@pytest.mark.parametrize(
    "malformation",
    ("partial_allocation", "mixed_product_paths", "process_only_product_path"),
)
def test_source_process_total_rejects_malformed_energy_attribution(
    malformation: str,
):
    extraction, components = _fixture()
    if malformation == "process_only_product_path":
        accepted = _accepted_energy(product_target_id=components[0].id)
        plan = EnergyPopulationPlan(attribution_mode="direct")
    else:
        accepted = _accepted_energy()
        plan = EnergyPopulationPlan(
            "allocated",
            "allocation:set:source-validation",
            "mass",
            (
                EnergyAllocationTarget(components[0].id, 1.0, "kg", 0.5, "row:a"),
                EnergyAllocationTarget(components[1].id, 1.0, "kg", 0.5, "row:b"),
            ),
        )
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    builder.populate_accepted_energy(accepted, plan)

    if malformation == "partial_allocation":
        allocation_edge = next(
            edge
            for edge in graph.edges_of_type("recordedForObject")
            if edge["src"] == accepted.consumption_id
        )
        del allocation_edge["props"]["evidenceRecordId"]
    elif malformation == "mixed_product_paths":
        graph.add_edge(
            accepted.consumption_id,
            "recordedForObject",
            components[0].id,
            {"sourceRecordId": "malformed-direct-overlap"},
            occurrence_id="source-view-malformed-direct-overlap",
        )
    else:
        graph.nodes[accepted.consumption_id]["props"][
            "attributionMode"
        ] = "process_only"
    before_query = _graph_bytes(graph)

    with pytest.raises(ValueError):
        source_process_total(graph)

    assert _graph_bytes(graph) == before_query


def test_malformed_allocation_edge_is_rejected_without_query_mutation():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    builder.populate_accepted_energy(
        accepted,
        EnergyPopulationPlan(
            "allocated",
            "allocation:set:malformed",
            "mass",
            (
                EnergyAllocationTarget(components[0].id, 1.0, "kg", 0.5, "row:a"),
                EnergyAllocationTarget(components[1].id, 1.0, "kg", 0.5, "row:b"),
            ),
        ),
    )
    edge = next(
        edge
        for edge in graph.edges_of_type("recordedForObject")
        if edge["src"] == accepted.consumption_id
    )
    del edge["props"]["evidenceRecordId"]
    before_query = _graph_bytes(graph)

    with pytest.raises(ValueError, match="missing fields"):
        product_contributions(graph)
    with pytest.raises(ValueError, match="unsupported"):
        contribution_value(
            10.0,
            {
                "tgt": components[0].id,
                "props": {"attributionMode": "guess"},
            },
        )

    assert _graph_bytes(graph) == before_query


def test_conflicting_duplicate_allocation_key_is_rejected_instead_of_guessed():
    extraction, components = _fixture()
    accepted = _accepted_energy()
    assert isinstance(accepted, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    builder.populate_accepted_energy(
        accepted,
        EnergyPopulationPlan(
            "allocated",
            "allocation:set:conflict",
            "mass",
            (
                EnergyAllocationTarget(components[0].id, 1.0, "kg", 0.5, "row:a"),
                EnergyAllocationTarget(components[1].id, 1.0, "kg", 0.5, "row:b"),
            ),
        ),
    )
    first = next(
        edge
        for edge in graph.edges_of_type("recordedForObject")
        if edge["src"] == accepted.consumption_id
        and edge["tgt"] == components[0].id
    )
    conflicting_props = dict(first["props"])
    conflicting_props["rawWeight"] = 99.0
    conflicting_props["evidenceRecordId"] = "row:conflict"
    graph.add_edge(
        accepted.consumption_id,
        "recordedForObject",
        components[0].id,
        conflicting_props,
        occurrence_id="conflicting-duplicate-allocation-key",
    )
    before_query = _graph_bytes(graph)

    with pytest.raises(ValueError, match="conflicting"):
        product_contributions(graph)

    assert _graph_bytes(graph) == before_query


def test_factor_carrier_planned_node_and_seeded_edge_collisions_are_atomic():
    extraction, components, material = _material_energy_fixture()
    direct_plan = EnergyPopulationPlan(attribution_mode="direct")

    conflicting_factor_energy = _accepted_energy(
        record_id="energy:factor-collision",
        product_target_id=components[0].id,
        factor=_factor(0.75),
    )
    assert isinstance(conflicting_factor_energy, AcceptedConsumption)
    factor_builder = _builder(extraction, accepted_materials=(material,))
    factor_graph = factor_builder.build()
    before = _graph_bytes(factor_graph)
    with pytest.raises(NodeIdCollisionError):
        factor_builder.populate_accepted_energy(conflicting_factor_energy, direct_plan)
    assert _graph_bytes(factor_graph) == before

    energy = _accepted_energy(product_target_id=components[0].id)
    assert isinstance(energy, AcceptedConsumption)
    carrier_builder = _builder(extraction)
    carrier_graph = carrier_builder.build_product_backbone()
    carrier_graph.add_node(CARRIER_ID, ["EnergyCarrier"], {"carrierId": "conflict"})
    before = _graph_bytes(carrier_graph)
    with pytest.raises(NodeIdCollisionError):
        carrier_builder.populate_accepted_energy(energy, direct_plan)
    assert _graph_bytes(carrier_graph) == before

    node_builder = _builder(extraction)
    node_graph = node_builder.build_product_backbone()
    node_graph.add_node(
        energy.consumption_id, ["EnergyConsumption"], {"recordId": "conflict"}
    )
    before = _graph_bytes(node_graph)
    with pytest.raises(NodeIdCollisionError):
        node_builder.populate_accepted_energy(energy, direct_plan)
    assert _graph_bytes(node_graph) == before

    edge_builder = _builder(extraction)
    edge_graph = edge_builder.build_product_backbone()
    edge_builder.populate_accepted_energy(energy, direct_plan)
    direct_edge = _edge(edge_graph, "recordedForObject", energy.consumption_id)
    direct_edge["props"]["sourceRecordId"] = "seeded-edge-conflict"
    before = _graph_bytes(edge_graph)
    with pytest.raises(ValueError):
        edge_builder.populate_accepted_energy(energy, direct_plan)
    assert _graph_bytes(edge_graph) == before


def test_energy_graph_has_no_legacy_runtime_vocabulary_or_blocked_payloads():
    extraction, components = _fixture()
    accepted = _accepted_energy(product_target_id=components[0].id)
    assert isinstance(accepted, AcceptedConsumption)
    graph = _builder(
        extraction,
        accepted_energies=((accepted, EnergyPopulationPlan("direct")),),
    ).build()

    labels = {
        label for node in graph.nodes.values() for label in node.get("labels", ())
    }
    relations = {edge["type"] for edge in graph.edges}
    serialized = _graph_bytes(graph).decode("utf-8").casefold()
    assert labels.isdisjoint(FORBIDDEN_RUNTIME_LABELS)
    assert relations.isdisjoint(FORBIDDEN_RUNTIME_RELATIONS)
    assert "blocked" not in serialized
    assert graph.count_label("AggregateCarbonEmission") == 0
    assert graph.count_label("AtomicCarbonEmission") == 0
    assert graph.count_label("FactoryTarget") == 0


def test_scope_mismatch_raises_instead_of_summing():
    extraction, components = _fixture()
    first = _accepted_energy(record_id="energy:first", product_target_id=components[0].id)
    second = _accepted_energy(record_id="energy:second", product_target_id=components[0].id)
    assert isinstance(first, AcceptedConsumption)
    assert isinstance(second, AcceptedConsumption)
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    direct = EnergyPopulationPlan(attribution_mode="direct")
    builder.populate_accepted_energy(first, direct)
    builder.populate_accepted_energy(second, direct)
    for field in ("recordedScope", "requestedScope", "systemBoundary"):
        graph.nodes[second.emission_id]["props"][field] = "A4"
    before_query = _graph_bytes(graph)

    with pytest.raises(ScopeAggregationError):
        component_total(graph, components[0].id)
    with pytest.raises(ScopeAggregationError):
        component_total(graph, components[0].id, requested_scope="A4")
    assert _graph_bytes(graph) == before_query


def test_scope_equivalence_uses_task4_case_spacing_and_dash_normalization():
    extraction, components = _fixture()
    equivalent_boundary = replace(_factor(), system_boundary=" a1 - a3 ")
    accepted = _accepted_energy(
        record_id="energy:normalized-scope",
        product_target_id=components[0].id,
        factor=equivalent_boundary,
    )
    assert isinstance(accepted, AcceptedConsumption)
    assert accepted.recorded_scope == "A1-A3"
    assert accepted.system_boundary == " a1 - a3 "
    builder = _builder(extraction)
    graph = builder.build_product_backbone()
    builder.populate_accepted_energy(
        accepted, EnergyPopulationPlan(attribution_mode="direct")
    )
    before_query = _graph_bytes(graph)

    assert component_total(graph, components[0].id) == pytest.approx(
        accepted.emission_value
    )
    assert component_total(
        graph, components[0].id, requested_scope=" A1 – A3 "
    ) == pytest.approx(accepted.emission_value)
    assert _graph_bytes(graph) == before_query


def test_legacy_energy_and_aggregate_builder_paths_are_removed():
    forbidden_methods = (
        "_attach_energy_records",
        "_load_factory_energy_inputs",
        "_load_factory_xlsx",
        "_expand_batch_allocations",
        "_attach_energy_row",
        "_resolve_factory_target",
        "_create_aggregates",
        "_add_aggregate",
        "_add_materials_and_material_emissions",
        "_material_consumption_mass",
    )
    assert all(
        not hasattr(MultiGranularCarbonKGBuilder, method)
        for method in forbidden_methods
    )
    extraction, _ = _fixture()
    builder = _builder(extraction)
    assert not hasattr(builder, "atomic_emissions")
    assert not hasattr(builder, "factory_target_node_by_id")
    assert not hasattr(builder, "factory_target_map_manifest")
    assert not hasattr(builder, "factory_component_target_map")
