import json

import pytest

from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    FORBIDDEN_RUNTIME_LABELS,
    FORBIDDEN_RUNTIME_RELATIONS,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_EDGE_TRIPLES,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    CanonicalLPGGraph,
    NodeIdCollisionError,
    stable_edge_id,
    stable_id,
)


def test_schema_contract_has_exactly_the_approved_classes_and_typed_triples():
    assert SCHEMA_VERSION == "m23-canonical-v2"
    assert set(APPLICATION_CLASSES) == {
        "ProductionBatch", "ModularUnit", "BuildingComponent", "ComponentType", "IfcMaterial",
        "DesignQuantity", "ManufacturingProcessTemplate", "ProductionStage", "ManufacturingActivity",
        "ManufacturingResource", "MaterialConsumption", "EnergyConsumption", "ConsumptionQuantity",
        "EmissionFactor", "EnergyCarrier", "CarbonEmission",
    }
    assert set(PRINCIPAL_EDGE_TRIPLES) == {
        ("ProductionBatch", "produces", "ModularUnit"),
        ("ModularUnit", "containsComponent", "BuildingComponent"),
        ("BuildingComponent", "hasComponentType", "ComponentType"),
        ("BuildingComponent", "hasMaterial", "IfcMaterial"),
        ("BuildingComponent", "hasDesignQuantity", "DesignQuantity"),
        ("ComponentType", "hasProcessTemplate", "ManufacturingProcessTemplate"),
        ("ModularUnit", "hasProcessTemplate", "ManufacturingProcessTemplate"),
        ("ManufacturingProcessTemplate", "hasStage", "ProductionStage"),
        ("ProductionStage", "hasActivity", "ManufacturingActivity"),
        ("ManufacturingActivity", "usesResource", "ManufacturingResource"),
        ("BuildingComponent", "manufacturedBy", "ManufacturingActivity"),
        ("ModularUnit", "manufacturedBy", "ManufacturingActivity"),
        ("MaterialConsumption", "recordedForObject", "BuildingComponent"),
        ("MaterialConsumption", "recordedForObject", "ModularUnit"),
        ("MaterialConsumption", "ofMaterial", "IfcMaterial"),
        ("MaterialConsumption", "hasQuantity", "ConsumptionQuantity"),
        ("MaterialConsumption", "hasFactor", "EmissionFactor"),
        ("EnergyConsumption", "recordedForObject", "BuildingComponent"),
        ("EnergyConsumption", "recordedForObject", "ModularUnit"),
        ("EnergyConsumption", "hasQuantity", "ConsumptionQuantity"),
        ("EnergyConsumption", "hasFactor", "EmissionFactor"),
        ("EnergyConsumption", "ofCarrier", "EnergyCarrier"),
        ("CarbonEmission", "hasCarbonDriver", "MaterialConsumption"),
        ("CarbonEmission", "hasCarbonDriver", "EnergyConsumption"),
        ("ConsumptionQuantity", "derivedFrom", "DesignQuantity"),
    }


def test_schema_contract_has_exactly_the_approved_principal_predicates():
    assert set(PRINCIPAL_PREDICATES) == {
        "produces", "containsComponent", "hasComponentType", "hasMaterial", "hasDesignQuantity",
        "hasProcessTemplate", "hasStage", "hasActivity", "usesResource", "manufacturedBy",
        "recordedForObject", "ofMaterial", "hasQuantity", "hasFactor", "ofCarrier", "hasCarbonDriver",
        "derivedFrom",
    }
    assert set(OPTIONAL_CONTEXT_PREDICATES) == {
        "associatedWithProcess",
        "recordedForResource",
        "directlyPrecedes",
    }


def test_stable_ids_are_repeatable_and_unicode_safe():
    steel = stable_id("IfcMaterial", "ifc-sha", 91, "钢")
    copper = stable_id("IfcMaterial", "ifc-sha", 92, "铜")

    assert steel == stable_id("IfcMaterial", "ifc-sha", 91, "钢")
    assert steel != copper
    assert stable_edge_id("hasMaterial", "component:一", steel, "association:7") == stable_edge_id(
        "hasMaterial", "component:一", steel, "association:7"
    )


def test_conflicting_node_id_reuse_fails_loudly():
    graph = CanonicalLPGGraph()
    graph.add_node("component:1", ["BuildingComponent"], {"globalId": "A"})

    with pytest.raises(NodeIdCollisionError):
        graph.add_node("component:1", ["BuildingComponent"], {"globalId": "B"})


def test_exact_repeat_and_compatible_node_enrichment_are_idempotent():
    graph = CanonicalLPGGraph()
    graph.add_node("component:1", ["BuildingComponent"], {"globalId": "A"})
    graph.add_node("component:1", ["BuildingComponent"], {"globalId": "A"})
    graph.add_node("component:1", ["BuildingComponent", "IfcBeam"], {"name": "梁"})

    assert len(graph.nodes) == 1
    assert graph.nodes["component:1"]["labels"] == ["BuildingComponent", "IfcBeam"]
    assert graph.nodes["component:1"]["props"] == {"globalId": "A", "name": "梁"}


def test_reusing_an_id_for_a_different_application_class_raises_a_collision():
    graph = CanonicalLPGGraph()
    graph.add_node("entity:1", ["BuildingComponent", "IfcBeam"])

    with pytest.raises(NodeIdCollisionError):
        graph.add_node("entity:1", ["IfcMaterial"])

    quantity_graph = CanonicalLPGGraph()
    quantity_graph.add_node("quantity:1", ["DesignQuantity", "IfcQuantityVolume"])
    quantity_graph.add_node("quantity:1", ["DesignQuantity", "IfcPhysicalQuantity"])
    assert quantity_graph.nodes["quantity:1"]["labels"] == [
        "DesignQuantity", "IfcPhysicalQuantity", "IfcQuantityVolume"
    ]


def test_parallel_edges_are_distinct_by_occurrence_id():
    graph = CanonicalLPGGraph()
    graph.add_node("component:1", ["BuildingComponent"])
    graph.add_node("material:1", ["IfcMaterial"])
    graph.add_edge("component:1", "hasMaterial", "material:1", occurrence_id="assoc:one")
    graph.add_edge("component:1", "hasMaterial", "material:1", occurrence_id="assoc:two")
    graph.add_edge("component:1", "hasMaterial", "material:1", occurrence_id="assoc:one")

    assert len(graph.edges) == 2
    assert {edge["occurrenceId"] for edge in graph.edges} == {"assoc:one", "assoc:two"}


def test_exports_and_edge_ids_are_insertion_order_independent(tmp_path):
    def populate(graph, reverse):
        nodes = [
            ("component:1", ["BuildingComponent"], {"name": "A"}),
            ("material:1", ["IfcMaterial"], {"name": "钢"}),
        ]
        edges = [("component:1", "hasMaterial", "material:1", "assoc:1")]
        for node in reversed(nodes) if reverse else nodes:
            graph.add_node(*node)
        for src, relation, tgt, occurrence_id in reversed(edges) if reverse else edges:
            graph.add_edge(src, relation, tgt, occurrence_id=occurrence_id)

    first, second = CanonicalLPGGraph(), CanonicalLPGGraph()
    populate(first, False)
    populate(second, True)
    first_json, second_json = tmp_path / "first.json", tmp_path / "second.json"
    first_cypher, second_cypher = tmp_path / "first.cypher", tmp_path / "second.cypher"
    first.export_json(first_json)
    second.export_json(second_json)
    first.export_cypher(first_cypher)
    second.export_cypher(second_cypher)

    assert json.loads(first_json.read_text(encoding="utf-8")) == json.loads(second_json.read_text(encoding="utf-8"))
    assert first_cypher.read_text(encoding="utf-8") == second_cypher.read_text(encoding="utf-8")
    assert first.edges[0]["id"] == second.edges[0]["id"]


def test_cypher_merge_and_json_share_the_occurrence_id(tmp_path):
    graph = CanonicalLPGGraph()
    graph.add_node("component:1", ["BuildingComponent"])
    graph.add_node("material:1", ["IfcMaterial"])
    graph.add_edge("component:1", "hasMaterial", "material:1", occurrence_id="association:9")
    json_path, cypher_path = tmp_path / "graph.json", tmp_path / "graph.cypher"
    graph.export_json(json_path)
    graph.export_cypher(cypher_path)

    assert json.loads(json_path.read_text(encoding="utf-8"))["edges"][0]["occurrenceId"] == "association:9"
    assert "MERGE (a)-[r:hasMaterial {occurrenceId: 'association:9'}]->(b)" in cypher_path.read_text(
        encoding="utf-8"
    )


def test_nested_properties_are_rejected_and_canonical_json_text_is_exportable(tmp_path):
    graph = CanonicalLPGGraph()
    with pytest.raises(TypeError, match="Neo4j"):
        graph.add_node(
            "component:bad",
            ["BuildingComponent"],
            {"provenance": {"record": {"row": 7, "sheet": "Factors"}}},
        )

    provenance = json.dumps(
        {"record": {"row": 7, "sheet": "Factors"}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    graph.add_node(
        "component:1", ["BuildingComponent"], {"provenance": [provenance]}
    )
    cypher_path = tmp_path / "safe.cypher"
    graph.export_cypher(cypher_path)

    assert provenance in cypher_path.read_text(encoding="utf-8")


def test_invalid_runtime_labels_and_relations_are_rejected():
    graph = CanonicalLPGGraph()
    with pytest.raises(ValueError):
        graph.add_node("legacy:1", [next(iter(FORBIDDEN_RUNTIME_LABELS))])
    with pytest.raises(ValueError):
        graph.add_node("invalid:1", ["NotACanonicalLabel"])

    graph.add_node("component:1", ["BuildingComponent"])
    graph.add_node("material:1", ["IfcMaterial"])
    with pytest.raises(ValueError):
        graph.add_edge("component:1", next(iter(FORBIDDEN_RUNTIME_RELATIONS)), "material:1")
    with pytest.raises(ValueError):
        graph.add_edge("component:1", "hasCarbonDriver", "material:1")
