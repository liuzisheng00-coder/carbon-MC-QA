from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import csv
from hashlib import sha256
import json
from pathlib import Path

import pytest

import dm2c_m2_alignment_audit as alignment_audit
from dm2c_m2_alignment_audit import audit_graph_payload, audit_release
from dm2c_m23_accounting import EnergyAllocationTarget, EnergyPopulationPlan
from dm2c_m23_calculation import ValidationIssue, ValidationResultSet
from dm2c_m23_canonical import stable_edge_id, stable_id
from dm2c_m23_canonical_release import build_validation_rows
from dm2c_m23_ifc import ComponentTypeRecord
from dm2c_multigranular_carbon_kg import (
    FactorLibrary,
    MultiGranularCarbonKGBuilder,
)
from tests.test_dm2c_m23_energy_accounting import (
    _accepted_energy,
    _builder as _energy_builder,
    _fixture as _energy_fixture,
)
from tests.test_dm2c_m23_material_builder import _one_material_fixture
from tests.test_dm2c_m23_release import actual_extraction, controlled_case


SCOPE = "A1-A3"


def _payload(graph) -> dict:
    return graph.to_payload()


def _factor_rows_from_graph(graph) -> dict[str, dict]:
    return {
        node["props"]["sourceRowId"]: dict(node["props"])
        for node in graph.nodes.values()
        if "EmissionFactor" in node["labels"]
    }


def _material_bundle():
    extraction, accepted = _one_material_fixture()
    builder = MultiGranularCarbonKGBuilder(
        ifc_path=Path("fixture.ifc"),
        factor_library=FactorLibrary(None),
        module_name="Audit module",
        module_identity="audit-module:1",
        extraction_result=extraction,
        accepted_materials=(accepted,),
    )
    graph = builder.build()
    validations = ValidationResultSet(accepted=(accepted,), rejected=())
    rows = build_validation_rows(validations)
    context = {
        "schemaVersion": "m23-canonical-v2",
        "releaseProfile": "controlled-fixture",
        "requestedScope": SCOPE,
        "ifcSha256": extraction.ifc_sha256,
        "extraction": extraction,
        "moduleId": builder.module_id,
        "moduleSourceIdentity": "audit-module:1",
        "moduleName": "Audit module",
        "oneModuleCase": True,
        "factoryInputPresent": False,
        "factorSourceRows": _factor_rows_from_graph(graph),
        "expectedValidationRows": rows,
        "measuredRecordIds": {accepted.record_id},
        "cypherText": graph.to_cypher(),
        "liveCypherConfig": None,
    }
    return graph, rows, context, accepted


def _typed_material_bundle():
    extraction, accepted = _one_material_fixture()
    component = extraction.components[0]
    component_type = ComponentTypeRecord(
        ifc_hash=extraction.ifc_sha256,
        global_id="3FixtureComponentType",
        step_id=22,
        ifc_class="IfcBeamType",
        name="Fixture beam type",
        description="Reviewed fixture type",
        tag="BT-22",
    )
    extraction = replace(
        extraction,
        components=(replace(component, component_type=component_type),),
    )
    builder = MultiGranularCarbonKGBuilder(
        ifc_path=Path("fixture.ifc"),
        factor_library=FactorLibrary(None),
        module_name="Audit module",
        module_identity="audit-module:1",
        extraction_result=extraction,
        accepted_materials=(accepted,),
    )
    graph = builder.build()
    rows = build_validation_rows(
        ValidationResultSet(accepted=(accepted,), rejected=())
    )
    context = {
        "schemaVersion": "m23-canonical-v2",
        "releaseProfile": "controlled-fixture",
        "requestedScope": SCOPE,
        "ifcSha256": extraction.ifc_sha256,
        "extraction": extraction,
        "moduleId": builder.module_id,
        "moduleSourceIdentity": "audit-module:1",
        "moduleName": "Audit module",
        "oneModuleCase": True,
        "factoryInputPresent": False,
        "factorSourceRows": _factor_rows_from_graph(graph),
        "expectedValidationRows": rows,
        "measuredRecordIds": {accepted.record_id},
        "cypherText": graph.to_cypher(),
        "liveCypherConfig": None,
    }
    return graph, rows, context


def _energy_bundle(mode: str):
    extraction, components = _energy_fixture()
    if mode == "direct":
        accepted = _accepted_energy(product_target_id=components[0].id)
        plan = EnergyPopulationPlan("direct")
    elif mode == "allocated":
        accepted = _accepted_energy()
        plan = EnergyPopulationPlan(
            "allocated",
            "allocation:set:audit",
            "mass",
            (
                EnergyAllocationTarget(components[0].id, 3.0, "kg", 0.6, "row:a"),
                EnergyAllocationTarget(components[1].id, 2.0, "kg", 0.4, "row:b"),
            ),
        )
    else:
        accepted = _accepted_energy()
        plan = None
    assert accepted is not None and not hasattr(accepted, "reason_code")
    builder = _energy_builder(extraction)
    graph = builder.build_product_backbone()
    if mode == "process_only":
        process_id = stable_id("ProductionStage", "audit-stage")
        graph.add_node(process_id, ["ProductionStage"], {"sourceRecordId": "stage:1"})
        plan = EnergyPopulationPlan("process_only", process_node_ids=(process_id,))
    assert plan is not None
    builder.populate_accepted_energy(accepted, plan)
    validations = ValidationResultSet(accepted=(accepted,), rejected=())
    rows = build_validation_rows(validations)
    module_id, module_node = next(
        (node_id, node)
        for node_id, node in graph.nodes.items()
        if "ModularUnit" in node["labels"]
    )
    module_props = module_node["props"]
    context = {
        "schemaVersion": "m23-canonical-v2",
        "releaseProfile": "controlled-fixture",
        "requestedScope": SCOPE,
        "ifcSha256": extraction.ifc_sha256,
        "extraction": extraction,
        "moduleId": module_id,
        "moduleSourceIdentity": module_props["sourceIdentity"],
        "moduleName": module_props["name"],
        "oneModuleCase": True,
        "factorSourceRows": _factor_rows_from_graph(graph),
        "expectedValidationRows": rows,
        "measuredRecordIds": {accepted.record_id},
        "cypherText": graph.to_cypher(),
        "liveCypherConfig": None,
    }
    return graph, rows, context, accepted, components


def _finding_codes(report: dict) -> set[str]:
    return {finding["code"] for finding in report["findings"]}


def test_audit_control_json_rejects_duplicate_keys_and_nonfinite_constants(
    tmp_path: Path,
):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        alignment_audit._read_json(duplicate)
    with pytest.raises(ValueError, match="non-finite"):
        alignment_audit._read_json(nonfinite)


def test_clean_canonical_payload_passes_with_stable_empty_findings():
    graph, rows, context, _accepted = _material_bundle()
    payload_before = json.dumps(_payload(graph), sort_keys=True)
    rows_before = deepcopy(rows)
    context_before = context["cypherText"]

    report = audit_graph_payload(_payload(graph), rows, context)

    assert report["schemaVersion"] == "m23-canonical-v2"
    assert report["status"] == "pass"
    assert report["highSeverityViolationCount"] == 0
    assert report["findings"] == []
    assert report["gates"] == {
        "alignment": "pass",
        "structuralCypher": "pass",
        "liveCypherRoundTrip": "not_configured",
    }
    assert json.dumps(_payload(graph), sort_keys=True) == payload_before
    assert rows == rows_before
    assert context["cypherText"] == context_before


def test_audit_accepts_only_explicit_missing_source_validation_identity() -> None:
    graph, _rows, context, accepted = _material_bundle()
    issue = ValidationIssue(
        record_id="record:missing-source",
        reason_code="source_record_id_missing",
        message="source is missing",
        evidence={
            "kind": "energy",
            "sourceRecordId": "",
            "evidenceSourceId": "evidence:missing-source",
            "formulaCode": "measured_energy",
            "energyCarrierId": "carrier:electricity",
            "recordedScope": SCOPE,
            "requestedScope": SCOPE,
        },
    )
    rows = build_validation_rows(
        ValidationResultSet(accepted=(accepted,), rejected=(issue,))
    )
    context["expectedValidationRows"] = rows
    report = audit_graph_payload(_payload(graph), rows, context)
    assert "validation_evidence_invalid" not in _finding_codes(report)
    rejected = next(dict(row) for row in rows if row["status"] == "rejected")
    rejected["reasonCode"] = "factor_not_found"
    bad_rows = tuple(
        rejected if row["status"] == "rejected" else row for row in rows
    )
    bad_context = dict(context)
    bad_context["expectedValidationRows"] = bad_rows
    bad = audit_graph_payload(_payload(graph), bad_rows, bad_context)
    assert "validation_evidence_invalid" in _finding_codes(bad)


@pytest.mark.parametrize(
    "missing_key",
    (
        "schemaVersion",
        "releaseProfile",
        "requestedScope",
        "ifcSha256",
        "extraction",
        "moduleId",
        "moduleSourceIdentity",
        "moduleName",
        "oneModuleCase",
        "factorSourceRows",
        "expectedValidationRows",
        "measuredRecordIds",
    ),
)
def test_graph_audit_fails_closed_when_independent_context_is_missing(
    missing_key: str,
):
    graph, rows, context, _accepted = _material_bundle()
    incomplete = dict(context)
    del incomplete[missing_key]

    report = audit_graph_payload(_payload(graph), rows, incomplete)

    assert report["status"] == "fail"
    assert "audit_context_invalid" in _finding_codes(report)


def test_graph_audit_requires_complete_factor_snapshot_when_factor_nodes_exist():
    graph, rows, context, _accepted = _material_bundle()
    context = {**context, "factorSourceRows": {}}

    report = audit_graph_payload(_payload(graph), rows, context)

    assert "audit_context_invalid" in _finding_codes(report)


def test_graph_audit_treats_expected_validation_rows_as_authoritative():
    graph, rows, context, _accepted = _material_bundle()
    context = {**context, "expectedValidationRows": ()}

    report = audit_graph_payload(_payload(graph), rows, context)

    assert "validation_source_mismatch" in _finding_codes(report)


def test_valid_zero_requires_record_in_independent_measured_record_ids():
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    for node in payload["nodes"]:
        labels = set(node["labels"])
        if "MaterialConsumption" in labels:
            node["props"]["isValidZero"] = True
        elif "ConsumptionQuantity" in labels:
            node["props"].update({"quantityValue": 0.0, "isValidZero": True})
        elif "CarbonEmission" in labels:
            node["props"].update(
                {"quantityValue": 0.0, "emissionValue": 0.0, "isValidZero": True}
            )
    validation_rows = tuple(
        {**row, "isValidZero": "true"} if row["status"] == "accepted" else row
        for row in rows
    )
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    context = {
        **context,
        "expectedValidationRows": validation_rows,
        "measuredRecordIds": set(),
        "cypherText": canonical.to_cypher(),
    }

    report = audit_graph_payload(canonical.to_payload(), validation_rows, context)

    assert "valid_zero_inconsistent" in _finding_codes(report)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("top_extra", "graph_shape_invalid"),
        ("node_extra", "graph_shape_invalid"),
        ("edge_extra", "graph_shape_invalid"),
        ("node_null", "neo4j_property_unsafe"),
        ("edge_empty", "neo4j_property_unsafe"),
        ("edge_placeholder", "forbidden_runtime_payload"),
    ),
)
def test_serialized_graph_shape_properties_and_edge_payload_are_exact(
    mutation: str, expected_code: str
):
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    if mutation == "top_extra":
        payload["unexpected"] = True
    elif mutation == "node_extra":
        payload["nodes"][0]["unexpected"] = True
    elif mutation == "edge_extra":
        payload["edges"][0]["unexpected"] = True
    elif mutation == "node_null":
        payload["nodes"][0]["props"]["serializedNull"] = None
    elif mutation == "edge_empty":
        payload["edges"][0]["props"]["serializedEmpty"] = ""
    else:
        payload["edges"][0]["props"]["sourcePath"] = "p0_factory_synth.csv"

    report = audit_graph_payload(payload, rows, context)

    assert expected_code in _finding_codes(report)


def test_zero_emission_from_zero_factor_is_not_a_valid_zero_quantity():
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    factor = next(node for node in payload["nodes"] if "EmissionFactor" in node["labels"])
    emission = next(node for node in payload["nodes"] if "CarbonEmission" in node["labels"])
    factor["props"].update(
        {
            "factorValue": 0.0,
            "originalFactorValue": 0.0,
            "normalizedFactorValue": 0.0,
        }
    )
    emission["props"].update({"factorValue": 0.0, "emissionValue": 0.0})
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    context = {
        **context,
        "factorSourceRows": _factor_rows_from_graph(canonical),
        "cypherText": canonical.to_cypher(),
    }

    report = audit_graph_payload(canonical.to_payload(), rows, context)

    assert "valid_zero_inconsistent" not in _finding_codes(report)
    assert report["highSeverityViolationCount"] == 0


@pytest.mark.parametrize("status", ("accepted", "rejected"))
def test_validation_evidence_is_bound_to_row_and_graph_semantics(status: str):
    graph, rows, context, _accepted = _material_bundle()
    validation_rows = [dict(rows[0])]
    if status == "accepted":
        validation_rows[0]["evidenceJson"] = "{}"
    else:
        evidence_source_id = "sha256:" + "B" * 64
        source_record_id = "MaterialAssociation:rejected-source"
        validation_rows.append(
            {
                "recordId": "rejected:evidence-projection",
                "sourceIdentity": stable_id(
                    "SourceRecord", evidence_source_id, source_record_id
                ),
                "sourceRecordId": source_record_id,
                "evidenceSourceId": evidence_source_id,
                "kind": "material",
                "status": "rejected",
                "reasonCode": "formula_unsupported",
                "message": "controlled rejection",
                "consumptionId": "",
                "quantityId": "",
                "factorId": "",
                "emissionId": "",
                "formulaCode": "direct_mass",
                "isValidZero": "",
                "evidenceJson": json.dumps(
                    {
                        "evidenceSourceId": evidence_source_id,
                        "formulaCode": "different_formula",
                        "kind": "material",
                        "sourceIdentity": stable_id(
                            "SourceRecord", evidence_source_id, source_record_id
                        ),
                        "sourceRecordId": source_record_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )

    report = audit_graph_payload(_payload(graph), tuple(validation_rows), context)

    assert "validation_evidence_invalid" in _finding_codes(report)


def test_connected_batch_is_forbidden_when_factory_input_is_explicitly_absent():
    graph, rows, context, _accepted = _material_bundle()
    batch_id = stable_id("ProductionBatch", "reviewed-batch:1")
    graph.add_node(
        batch_id,
        ["ProductionBatch"],
        {"sourceIdentity": "reviewed-batch:1", "name": "Reviewed batch"},
    )
    graph.add_edge(
        batch_id,
        "produces",
        context["moduleId"],
        {"sourceRecordId": "reviewed-batch:1"},
        occurrence_id="reviewed-batch:produces",
    )
    context = {
        **context,
        "factoryInputPresent": False,
        "cypherText": graph.to_cypher(),
    }

    report = audit_graph_payload(_payload(graph), rows, context)

    assert "factory_input_fact_forbidden" in _finding_codes(report)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("forbidden_label", "forbidden_runtime_vocabulary"),
        ("duplicate_node", "duplicate_node_id"),
        ("duplicate_edge", "duplicate_edge_id"),
        ("duplicate_occurrence", "duplicate_occurrence_id"),
        ("missing_endpoint", "missing_edge_endpoint"),
        ("invalid_triple", "invalid_edge_triple"),
        ("multiple_application_labels", "application_label_cardinality"),
        ("missing_factor_path", "consumption_cardinality_invalid"),
        ("q_times_ef", "emission_formula_mismatch"),
        ("scope", "scope_incompatible"),
        ("unit", "unit_factor_incompatible"),
        ("invalid_zero", "valid_zero_inconsistent"),
        ("rejection_leakage", "rejected_identity_in_graph"),
        ("nested_property", "neo4j_property_unsafe"),
        ("invalid_provenance_json", "provenance_json_invalid"),
    ),
)
def test_high_severity_structural_mutations_fail_closed(
    mutation: str, expected_code: str
):
    graph, rows, context, accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    validation_rows = [dict(row) for row in rows]
    if mutation == "forbidden_label":
        payload["nodes"][0]["labels"].append("AtomicCarbonEmission")
    elif mutation == "duplicate_node":
        payload["nodes"].append(deepcopy(payload["nodes"][0]))
    elif mutation == "duplicate_edge":
        payload["edges"].append(deepcopy(payload["edges"][0]))
    elif mutation == "duplicate_occurrence":
        duplicate = deepcopy(payload["edges"][1])
        duplicate["id"] = "edge:duplicate-occurrence"
        duplicate["occurrenceId"] = payload["edges"][0]["occurrenceId"]
        payload["edges"].append(duplicate)
    elif mutation == "missing_endpoint":
        payload["edges"][0]["tgt"] = "missing:node"
    elif mutation == "invalid_triple":
        edge = next(row for row in payload["edges"] if row["type"] == "hasFactor")
        edge["tgt"] = next(
            node["id"] for node in payload["nodes"] if "BuildingComponent" in node["labels"]
        )
    elif mutation == "multiple_application_labels":
        payload["nodes"][0]["labels"].append("ModularUnit")
    elif mutation == "missing_factor_path":
        payload["edges"] = [
            row for row in payload["edges"] if row["type"] != "hasFactor"
        ]
    elif mutation == "q_times_ef":
        emission = next(node for node in payload["nodes"] if "CarbonEmission" in node["labels"])
        emission["props"]["emissionValue"] += 1.0
    elif mutation == "scope":
        quantity = next(
            node for node in payload["nodes"] if "ConsumptionQuantity" in node["labels"]
        )
        quantity["props"]["recordedScope"] = "A4"
    elif mutation == "unit":
        quantity = next(
            node for node in payload["nodes"] if "ConsumptionQuantity" in node["labels"]
        )
        quantity["props"]["quantityUnit"] = "m3"
    elif mutation == "invalid_zero":
        for node in payload["nodes"]:
            if "ConsumptionQuantity" in node["labels"]:
                node["props"]["quantityValue"] = 0.0
                node["props"]["isValidZero"] = False
            if "CarbonEmission" in node["labels"]:
                node["props"]["quantityValue"] = 0.0
                node["props"]["emissionValue"] = 0.0
                node["props"]["isValidZero"] = False
    elif mutation == "rejection_leakage":
        validation_rows[0] = {
            **validation_rows[0],
            "status": "rejected",
            "reasonCode": "controlled_rejection",
            "message": "must not be in graph",
            "consumptionId": "",
            "quantityId": "",
            "factorId": "",
            "emissionId": "",
        }
    elif mutation == "nested_property":
        payload["nodes"][0]["props"]["unsafe"] = {"nested": True}
    elif mutation == "invalid_provenance_json":
        quantity = next(
            node for node in payload["nodes"] if "ConsumptionQuantity" in node["labels"]
        )
        quantity["props"]["orderedRawOperands"][0] = "{not-canonical-json"

    report = audit_graph_payload(payload, tuple(validation_rows), context)

    assert report["status"] == "fail"
    assert report["highSeverityViolationCount"] >= 1
    assert expected_code in _finding_codes(report)
    assert report["findings"] == sorted(
        report["findings"],
        key=lambda row: (
            row["severity"],
            row["code"],
            row["artifact"],
            row["entityId"],
            row["message"],
            json.dumps(row["evidence"], sort_keys=True),
        ),
    )


@pytest.mark.parametrize("mode", ("direct", "allocated", "process_only"))
def test_each_valid_energy_attribution_mode_passes_independent_audit(mode: str):
    graph, rows, context, _accepted, _components = _energy_bundle(mode)

    report = audit_graph_payload(_payload(graph), rows, context)

    assert report["highSeverityViolationCount"] == 0
    assert report["projectionTotals"]["sourceProcess"] == pytest.approx(10.0)
    if mode == "process_only":
        assert report["projectionTotals"]["product"] == 0.0
    else:
        assert report["projectionTotals"]["product"] == pytest.approx(10.0)


def test_allocation_conservation_and_semantic_key_uniqueness_are_high_severity():
    graph, rows, context, accepted, _components = _energy_bundle("allocated")
    payload = deepcopy(_payload(graph))
    edges = [
        edge
        for edge in payload["edges"]
        if edge["type"] == "recordedForObject" and edge["src"] == accepted.consumption_id
    ]
    edges[0]["props"]["normalizedWeight"] = 0.3
    duplicate = deepcopy(edges[1])
    duplicate["id"] = "edge:duplicate-semantic-allocation"
    duplicate["occurrenceId"] = "duplicate-semantic-allocation"
    payload["edges"].append(duplicate)

    report = audit_graph_payload(payload, rows, context)

    assert "allocation_not_conserved" in _finding_codes(report)
    assert "duplicate_allocation_key" in _finding_codes(report)


def test_mixed_and_process_only_product_paths_are_high_severity():
    allocated_graph, allocated_rows, allocated_context, accepted, components = _energy_bundle(
        "allocated"
    )
    allocated_payload = deepcopy(_payload(allocated_graph))
    direct_occurrence = "audit-mixed-direct"
    allocated_payload["edges"].append(
        {
            "id": stable_edge_id(
                "recordedForObject", accepted.consumption_id, components[0].id, direct_occurrence
            ),
            "src": accepted.consumption_id,
            "type": "recordedForObject",
            "tgt": components[0].id,
            "occurrenceId": direct_occurrence,
            "props": {"sourceRecordId": "mixed"},
        }
    )
    mixed_report = audit_graph_payload(
        allocated_payload, allocated_rows, allocated_context
    )

    process_graph, process_rows, process_context, process_accepted, process_components = (
        _energy_bundle("process_only")
    )
    process_payload = deepcopy(_payload(process_graph))
    process_occurrence = "audit-process-product"
    process_payload["edges"].append(
        {
            "id": stable_edge_id(
                "recordedForObject",
                process_accepted.consumption_id,
                process_components[0].id,
                process_occurrence,
            ),
            "src": process_accepted.consumption_id,
            "type": "recordedForObject",
            "tgt": process_components[0].id,
            "occurrenceId": process_occurrence,
            "props": {"sourceRecordId": "process-overlap"},
        }
    )
    process_report = audit_graph_payload(
        process_payload, process_rows, process_context
    )

    assert "mixed_energy_attribution" in _finding_codes(mixed_report)
    assert "process_only_product_path" in _finding_codes(process_report)


def test_structural_cypher_must_be_exact_and_include_every_occurrence_id():
    graph, rows, context, _accepted = _material_bundle()
    first_occurrence = _payload(graph)["edges"][0]["occurrenceId"]
    context = {**context, "cypherText": context["cypherText"].replace(first_occurrence, "")}

    report = audit_graph_payload(_payload(graph), rows, context)

    assert report["gates"]["structuralCypher"] == "fail"
    assert "cypher_occurrence_missing" in _finding_codes(report)
    assert report["gates"]["liveCypherRoundTrip"] == "not_configured"


def test_graph_order_and_full_cypher_bytes_are_mandatory():
    graph, rows, context, _accepted = _material_bundle()
    reordered = deepcopy(_payload(graph))
    reordered["nodes"].reverse()
    reordered["edges"].reverse()

    order_report = audit_graph_payload(reordered, rows, context)
    cypher_report = audit_graph_payload(
        _payload(graph), rows, {**context, "cypherText": context["cypherText"] + "// drift\n"}
    )

    assert "graph_order_invalid" in _finding_codes(order_report)
    assert "cypher_bytes_mismatch" in _finding_codes(cypher_report)
    assert cypher_report["gates"]["structuralCypher"] == "fail"


def test_scope_equivalence_normalizes_case_spacing_and_unicode_dashes():
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    for node in payload["nodes"]:
        props = node["props"]
        if "recordedScope" in props:
            props["recordedScope"] = " a1–a3 "
        if "requestedScope" in props:
            props["requestedScope"] = "A1—A3"
        if "systemBoundary" in props:
            props["systemBoundary"] = "A1-A3"
    equivalent_context = {
        **context,
        "requestedScope": "a1 - a3",
        "cypherText": None,
    }
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    equivalent_context["cypherText"] = canonical.to_cypher()

    report = audit_graph_payload(payload, rows, equivalent_context)

    assert report["highSeverityViolationCount"] == 0


def test_backbone_module_factor_and_validation_reconciliation_fail_closed():
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge["type"] not in {"containsComponent", "hasMaterial"}
    ]
    factor = next(node for node in payload["nodes"] if "EmissionFactor" in node["labels"])
    factor["props"]["sourceRowId"] = "wrong:factor:source"
    context = {
        **context,
        "cypherText": alignment_audit._canonical_graph_from_payload(payload).to_cypher(),
    }

    report = audit_graph_payload(payload, (), context)
    codes = _finding_codes(report)

    assert "module_parent_invalid" in codes
    assert "ifc_backbone_mismatch" in codes
    assert "factor_source_identity_mismatch" in codes
    assert "graph_fact_missing_validation" in codes


@pytest.mark.parametrize(
    "mutation",
    (
        "component_name",
        "component_type_global_id",
        "material_step_id",
        "material_edge_props",
        "design_quantity_value",
        "design_edge_props",
        "module_edge_occurrence",
    ),
)
def test_full_ifc_backbone_nodes_edges_properties_and_occurrences_are_exact(
    mutation: str,
):
    graph, rows, context = _typed_material_bundle()
    payload = deepcopy(_payload(graph))
    if mutation == "component_name":
        node = next(row for row in payload["nodes"] if "BuildingComponent" in row["labels"])
        node["props"]["name"] = "substituted component name"
    elif mutation == "component_type_global_id":
        node = next(row for row in payload["nodes"] if "ComponentType" in row["labels"])
        node["props"]["globalId"] = "substituted-type-global-id"
    elif mutation == "material_step_id":
        node = next(row for row in payload["nodes"] if "IfcMaterial" in row["labels"])
        node["props"]["materialStepId"] += 1
    elif mutation == "material_edge_props":
        edge = next(row for row in payload["edges"] if row["type"] == "hasMaterial")
        edge["props"]["associationStepId"] += 1
    elif mutation == "design_quantity_value":
        node = next(row for row in payload["nodes"] if "DesignQuantity" in row["labels"])
        node["props"]["normalizedValue"] += 1.0
    elif mutation == "design_edge_props":
        edge = next(
            row for row in payload["edges"] if row["type"] == "hasDesignQuantity"
        )
        edge["props"]["sourceRecordId"] = "substituted-design-ownership"
    else:
        edge = next(
            row for row in payload["edges"] if row["type"] == "containsComponent"
        )
        edge["occurrenceId"] = "substituted-module-association"
        edge["id"] = stable_edge_id(
            edge["type"], edge["src"], edge["tgt"], edge["occurrenceId"]
        )
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    context = {**context, "cypherText": canonical.to_cypher()}

    report = audit_graph_payload(canonical.to_payload(), rows, context)

    assert "ifc_backbone_mismatch" in _finding_codes(report)


@pytest.mark.parametrize(
    "node_class",
    (
        "ProductionBatch",
        "ModularUnit",
        "BuildingComponent",
        "ComponentType",
        "IfcMaterial",
        "DesignQuantity",
        "ManufacturingProcessTemplate",
        "ProductionStage",
        "ManufacturingActivity",
        "ManufacturingResource",
        "MaterialConsumption",
        "EnergyConsumption",
        "ConsumptionQuantity",
        "EmissionFactor",
        "EnergyCarrier",
        "CarbonEmission",
    ),
)
def test_every_application_class_requires_a_canonical_ownership_path(
    node_class: str,
):
    graph, rows, context, _accepted = _material_bundle()
    orphan_id = stable_id(node_class, "orphan-review")
    graph.add_node(
        orphan_id,
        [node_class],
        {"sourceIdentity": f"orphan-review:{node_class}"},
    )
    context = {**context, "cypherText": graph.to_cypher()}

    report = audit_graph_payload(_payload(graph), rows, context)

    assert "orphan_canonical_node" in _finding_codes(report)


def test_accepted_ledger_semantics_and_consumption_scope_are_reconciled():
    graph, rows, context, accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    consumption = next(
        node for node in payload["nodes"] if "MaterialConsumption" in node["labels"]
    )
    consumption["props"]["recordedScope"] = "A4"
    mutated_rows = [dict(row) for row in rows]
    mutated_rows[0]["formulaCode"] = "wrong_formula"
    context = {
        **context,
        "cypherText": alignment_audit._canonical_graph_from_payload(payload).to_cypher(),
    }

    report = audit_graph_payload(payload, tuple(mutated_rows), context)

    assert "scope_incompatible" in _finding_codes(report)
    assert "accepted_ledger_mismatch" in _finding_codes(report)
    assert mutated_rows[0]["recordId"] == accepted.record_id


def test_allocation_requires_complete_consistent_source_evidence():
    graph, rows, context, accepted, _components = _energy_bundle("allocated")
    payload = deepcopy(_payload(graph))
    allocation = next(
        edge
        for edge in payload["edges"]
        if edge["src"] == accepted.consumption_id
        and edge["type"] == "recordedForObject"
    )
    allocation["props"]["rawWeight"] = -1.0
    allocation["props"]["allocationSetId"] = "different:set"
    context = {
        **context,
        "cypherText": alignment_audit._canonical_graph_from_payload(payload).to_cypher(),
    }

    report = audit_graph_payload(payload, rows, context)

    assert "allocation_edge_invalid" in _finding_codes(report)


@pytest.mark.parametrize(
    "mutation",
    (
        "excluded_refinement",
        "orphan_nodes",
        "module_identity",
        "rejected_identity_reuse",
        "synthetic_factory_payload",
        "blocked_status",
        "all_batches",
    ),
)
def test_forbidden_backbone_placeholder_and_ledger_mutations_fail_closed(
    mutation: str,
):
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    validation_rows = [dict(row) for row in rows]
    if mutation == "excluded_refinement":
        component = next(
            node for node in payload["nodes"] if "BuildingComponent" in node["labels"]
        )
        component["labels"].append("IfcValve")
    elif mutation == "orphan_nodes":
        for label in (
            "IfcMaterial",
            "DesignQuantity",
            "EmissionFactor",
            "EnergyCarrier",
        ):
            payload["nodes"].append(
                {"id": f"orphan:{label}", "labels": [label], "props": {}}
            )
    elif mutation == "module_identity":
        module = next(node for node in payload["nodes"] if "ModularUnit" in node["labels"])
        module["props"]["sourceIdentity"] = "mutated-module"
        module["props"]["name"] = "Mutated module"
    elif mutation == "rejected_identity_reuse":
        rejected = dict(validation_rows[0])
        rejected.update(
            {
                "recordId": "rejected:identity-reuse",
                "status": "rejected",
                "reasonCode": "controlled_rejection",
                "message": "must not alias accepted source identity",
                "consumptionId": "",
                "quantityId": "",
                "factorId": "",
                "emissionId": "",
            }
        )
        validation_rows.append(rejected)
    elif mutation == "synthetic_factory_payload":
        payload["nodes"][0]["props"].update(
            {
                "factoryTargetId": "FactoryModule_002",
                "sourcePath": "p0_factory_synth.csv",
            }
        )
    elif mutation == "blocked_status":
        payload["nodes"][0]["props"]["status"] = "blocked_missing_factor"
    else:
        payload["nodes"].append(
            {
                "id": "ProductionBatch:ALL_BATCHES",
                "labels": ["ProductionBatch"],
                "props": {"sourceIdentity": "ALL_BATCHES", "name": "ALL_BATCHES"},
            }
        )
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    payload = canonical.to_payload()
    context = {**context, "cypherText": canonical.to_cypher()}

    report = audit_graph_payload(payload, tuple(validation_rows), context)

    expected = {
        "excluded_refinement": "excluded_ifc_refinement",
        "orphan_nodes": "orphan_canonical_node",
        "module_identity": "module_identity_mismatch",
        "rejected_identity_reuse": "rejected_identity_in_graph",
        "synthetic_factory_payload": "forbidden_runtime_payload",
        "blocked_status": "forbidden_runtime_payload",
        "all_batches": "forbidden_runtime_payload",
    }[mutation]
    assert expected in _finding_codes(report)


@pytest.mark.parametrize("mutation", ("nonzero_valid_zero", "product_target"))
def test_zero_and_product_ownership_properties_match_calculation_paths(mutation: str):
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    validation_rows = [dict(row) for row in rows]
    consumption = next(
        node for node in payload["nodes"] if "MaterialConsumption" in node["labels"]
    )
    if mutation == "nonzero_valid_zero":
        for node in payload["nodes"]:
            if set(node["labels"]) & {
                "MaterialConsumption",
                "ConsumptionQuantity",
                "CarbonEmission",
            }:
                node["props"]["isValidZero"] = True
        validation_rows[0]["isValidZero"] = "true"
    else:
        consumption["props"]["productTargetId"] = "BuildingComponent:not-the-edge-target"
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    payload = canonical.to_payload()
    context = {**context, "cypherText": canonical.to_cypher()}

    report = audit_graph_payload(payload, tuple(validation_rows), context)

    assert (
        "valid_zero_inconsistent"
        if mutation == "nonzero_valid_zero"
        else "product_target_mismatch"
    ) in _finding_codes(report)


def test_factor_node_is_reconciled_to_bound_workbook_source_row():
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    factor = next(node for node in payload["nodes"] if "EmissionFactor" in node["labels"])
    source_row_id = factor["props"]["sourceRowId"]
    expected = dict(factor["props"])
    factor["props"]["sourceReference"] = "substituted:factor-source"
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    payload = canonical.to_payload()
    context = {
        **context,
        "factorSourceRows": {source_row_id: expected},
        "cypherText": canonical.to_cypher(),
    }

    report = audit_graph_payload(payload, rows, context)

    assert "factor_workbook_mismatch" in _finding_codes(report)


def test_factor_complete_snapshot_includes_workbook_geography():
    graph, rows, context, _accepted = _material_bundle()
    payload = deepcopy(_payload(graph))
    factor = next(node for node in payload["nodes"] if "EmissionFactor" in node["labels"])
    source_row_id = factor["props"]["sourceRowId"]
    expected = dict(factor["props"])
    factor["props"]["geography"] = "substituted geography"
    canonical = alignment_audit._canonical_graph_from_payload(payload)
    context = {
        **context,
        "factorSourceRows": {source_row_id: expected},
        "cypherText": canonical.to_cypher(),
    }

    report = audit_graph_payload(canonical.to_payload(), rows, context)

    assert "factor_workbook_mismatch" in _finding_codes(report)


def test_audit_release_detects_envelope_hash_and_forbidden_graph_mutation(
    tmp_path: Path, controlled_case
):
    from dm2c_m23_canonical_release import build_release, load_case_config

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-release",
        "audit_release_fixture",
    )
    clean = audit_release(release_dir)
    graph_path = release_dir / "multigranular_carbon_kg.json"
    graph = json.loads(graph_path.read_text("utf-8"))
    graph["nodes"][0]["labels"].append("AtomicCarbonEmission")
    graph_path.write_text(
        json.dumps(graph, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )

    mutated = audit_release(release_dir)

    assert clean["highSeverityViolationCount"] == 0
    assert "output_hash_mismatch" in _finding_codes(mutated)
    assert "forbidden_runtime_vocabulary" in _finding_codes(mutated)


def test_audit_release_rejects_unsafe_output_paths_and_mismatched_inputs(
    tmp_path: Path, controlled_case
):
    from dm2c_m23_canonical_release import build_release, load_case_config

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-envelope",
        "audit_envelope_fixture",
    )
    manifest_path = release_dir / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["outputs"]["graphJson"]["path"] = "../escape.json"
    manifest["inputs"]["ifc"]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    report = audit_release(release_dir)

    assert "unsafe_output_path" in _finding_codes(report)
    assert "input_hash_mismatch" in _finding_codes(report)


def test_audit_release_reconciles_manifest_code_stats_report_and_missing_input(
    tmp_path: Path, controlled_case
):
    from dm2c_m23_canonical_release import build_release, load_case_config

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-envelope-deep",
        "audit_envelope_deep_fixture",
    )
    manifest_path = release_dir / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["inputs"]["ifc"]["resolvedPath"] = str(tmp_path / "missing.ifc")
    first_code = next(iter(manifest["code"]))
    manifest["code"][first_code]["sha256"] = "0" * 64
    manifest["gates"]["liveCypherRoundTrip"] = "pass"
    manifest["counts"]["nodeCount"] += 1

    alignment_path = release_dir / "m2_alignment_report.json"
    stored_report = json.loads(alignment_path.read_text("utf-8"))
    stored_report["status"] = "fail"
    alignment_path.write_text(
        json.dumps(stored_report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    alignment_bytes = alignment_path.read_bytes()
    manifest["outputs"]["alignmentReport"].update(
        {
            "sha256": sha256(alignment_bytes).hexdigest().upper(),
            "sizeBytes": len(alignment_bytes),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    (release_dir / "unexpected-directory").mkdir()

    report = audit_release(release_dir)
    codes = _finding_codes(report)

    assert "input_missing" in codes
    assert "code_hash_mismatch" in codes
    assert "manifest_gate_mismatch" in codes
    assert "manifest_counts_mismatch" in codes
    assert "stored_alignment_mismatch" in codes
    assert "release_artifact_set_invalid" in codes


def test_manifest_is_reconciled_to_case_config_and_rejects_actual_profile_lies(
    tmp_path: Path, controlled_case
):
    from dm2c_m23_canonical_release import build_release, load_case_config

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-config-binding",
        "audit_config_binding_fixture",
    )
    manifest_path = release_dir / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["manifestSha256"] = "0" * 64
    manifest["releaseId"] = "different_release"
    manifest["releaseProfile"] = "actual-case"
    manifest["releaseReady"] = True
    manifest["generatedAtUtc"] = "2099-01-01T00:00:00Z"
    manifest["module"]["sourceIdentity"] = "wrong-module"
    manifest["configuration"]["requestedScope"] = "A4"
    manifest["command"].update(
        {
            "entryPoint": "legacy.py",
            "caseConfig": "wrong.json",
            "releaseId": "different-command-release",
        }
    )
    manifest["inputs"]["ifc"]["selectedPath"] = "substituted.ifc"
    manifest["inputs"]["factoryInput"] = dict(manifest["inputs"]["materialEvidence"])
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    report = audit_release(release_dir)
    codes = _finding_codes(report)

    assert "manifest_schema_invalid" in codes
    assert "manifest_release_id_mismatch" in codes
    assert "manifest_config_mismatch" in codes
    assert "actual_profile_invalid" in codes


def test_coordinated_stats_and_validation_tampering_is_independently_detected(
    tmp_path: Path, controlled_case
):
    from dm2c_m23_canonical_release import build_release, load_case_config

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-coordinated",
        "audit_coordinated_fixture",
    )
    manifest_path = release_dir / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))

    stats_path = release_dir / "multigranular_carbon_kg_stats.json"
    stats = json.loads(stats_path.read_text("utf-8"))
    stats["applicationClassCounts"]["BuildingComponent"] += 1
    stats["calculationCounts"]["validZero"] += 1
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    manifest["counts"] = stats

    validation_path = release_dir / "multigranular_carbon_kg_validation.csv"
    with validation_path.open(encoding="utf-8", newline="") as handle:
        validation_rows = list(csv.reader(handle))
    evidence_index = validation_rows[0].index("evidenceJson")
    validation_rows[1][evidence_index] = "{not-json"
    validation_rows.reverse()
    with validation_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(validation_rows)

    for key, filename in (
        ("stats", "multigranular_carbon_kg_stats.json"),
        ("validation", "multigranular_carbon_kg_validation.csv"),
    ):
        data = (release_dir / filename).read_bytes()
        manifest["outputs"][key].update(
            {"sha256": sha256(data).hexdigest().upper(), "sizeBytes": len(data)}
        )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    report = audit_release(release_dir)

    assert "stats_graph_mismatch" in _finding_codes(report)
    assert "validation_csv_invalid" in _finding_codes(report)


def test_audit_release_reconstructs_validation_from_hashed_source_inputs(
    tmp_path: Path, controlled_case
):
    from dm2c_m23_canonical_release import (
        VALIDATION_FIELDS,
        build_release,
        load_case_config,
    )

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-source-ledger",
        "audit_source_ledger_fixture",
    )
    validation_path = release_dir / "multigranular_carbon_kg_validation.csv"
    with validation_path.open(encoding="utf-8", newline="") as handle:
        validation_rows = list(csv.DictReader(handle))
    accepted = next(row for row in validation_rows if row["status"] == "accepted")
    accepted["evidenceJson"] = "{}"
    with validation_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=VALIDATION_FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(validation_rows)

    manifest_path = release_dir / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    validation_bytes = validation_path.read_bytes()
    manifest["outputs"]["validation"].update(
        {
            "sha256": sha256(validation_bytes).hexdigest().upper(),
            "sizeBytes": len(validation_bytes),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    report = audit_release(release_dir)

    assert "validation_source_mismatch" in _finding_codes(report)


def test_audit_cli_exit_codes_are_zero_one_and_two(tmp_path: Path, controlled_case):
    from dm2c_m23_canonical_release import build_release, load_case_config

    release_dir = build_release(
        load_case_config(controlled_case["configPath"]),
        tmp_path / "audit-cli",
        "audit_cli_fixture",
    )
    assert alignment_audit.main(["--root", str(release_dir)]) == 0

    graph_path = release_dir / "multigranular_carbon_kg.json"
    graph = json.loads(graph_path.read_text("utf-8"))
    graph["nodes"][0]["labels"].append("FactoryTarget")
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    assert alignment_audit.main(["--root", str(release_dir)]) == 1
    assert alignment_audit.main(["--root", str(tmp_path / "missing")]) == 2
