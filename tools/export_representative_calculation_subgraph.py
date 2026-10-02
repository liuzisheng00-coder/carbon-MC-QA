"""Export a focused, end-to-end carbon-calculation subgraph for one component."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from convert_kg_to_gephi_gexf import convert_graph


REPRESENTATIVE_COMPONENT_ID = (
    "BuildingComponent:007432cb03b54e908f9a7c5cf5dc2c109cdc8acb8076ec6a03b9930049d3327e"
)
COMPONENT_RELATIONS = {
    "containsComponent",
    "hasComponentType",
    "hasDesignQuantity",
    "hasMaterial",
    "recordedForObject",
}
MATERIAL_CALCULATION_RELATIONS = {
    "hasCarbonDriver",
    "hasFactor",
    "hasQuantity",
    "ofMaterial",
    "recordedForObject",
}
ENERGY_RELATIONS = {"associatedWithProcess", "ofCarrier"}
ACTIVITY_RELATIONS = {"associatedWithProcess", "usesResource", "hasActivity"}
QUANTITY_RELATIONS = {"derivedFrom"}


def _node_prefix(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def _add_incident_edges(
    node_id: str,
    allowed_types: set[str],
    source_edges: list[dict[str, Any]],
    selected_node_ids: set[str],
    selected_edge_ids: set[str],
    component_id: str,
) -> set[str]:
    """Add allowed incident edges, without admitting a second component."""
    added_node_ids: set[str] = set()
    for edge in source_edges:
        if edge["type"] not in allowed_types or node_id not in {edge["src"], edge["tgt"]}:
            continue
        other_node_id = edge["tgt"] if edge["src"] == node_id else edge["src"]
        if _node_prefix(other_node_id) == "BuildingComponent" and other_node_id != component_id:
            continue
        selected_edge_ids.add(edge["id"])
        selected_node_ids.add(other_node_id)
        added_node_ids.add(other_node_id)
    return added_node_ids


def build_subgraph(source: dict[str, list[dict[str, Any]]], component_id: str) -> dict[str, list[dict[str, Any]]]:
    """Select the material and energy calculation context for one building component."""
    source_nodes = source["nodes"]
    source_edges = source["edges"]
    known_node_ids = {node["id"] for node in source_nodes}
    if component_id not in known_node_ids:
        raise ValueError(f"Representative component does not exist: {component_id}")

    selected_node_ids = {component_id}
    selected_edge_ids: set[str] = set()

    _add_incident_edges(
        component_id,
        COMPONENT_RELATIONS,
        source_edges,
        selected_node_ids,
        selected_edge_ids,
        component_id,
    )

    material_consumptions = [node_id for node_id in selected_node_ids if _node_prefix(node_id) == "MaterialConsumption"]
    for node_id in material_consumptions:
        _add_incident_edges(
            node_id,
            MATERIAL_CALCULATION_RELATIONS,
            source_edges,
            selected_node_ids,
            selected_edge_ids,
            component_id,
        )

    consumption_quantities = [node_id for node_id in selected_node_ids if _node_prefix(node_id) == "ConsumptionQuantity"]
    for node_id in consumption_quantities:
        _add_incident_edges(
            node_id,
            QUANTITY_RELATIONS,
            source_edges,
            selected_node_ids,
            selected_edge_ids,
            component_id,
        )

    energy_consumptions = [node_id for node_id in selected_node_ids if _node_prefix(node_id) == "EnergyConsumption"]
    for node_id in energy_consumptions:
        _add_incident_edges(
            node_id,
            ENERGY_RELATIONS,
            source_edges,
            selected_node_ids,
            selected_edge_ids,
            component_id,
        )

    manufacturing_activities = [
        node_id for node_id in selected_node_ids if _node_prefix(node_id) == "ManufacturingActivity"
    ]
    for node_id in manufacturing_activities:
        _add_incident_edges(
            node_id,
            ACTIVITY_RELATIONS,
            source_edges,
            selected_node_ids,
            selected_edge_ids,
            component_id,
        )

    selected_nodes = [node for node in source_nodes if node["id"] in selected_node_ids]
    selected_edges = [edge for edge in source_edges if edge["id"] in selected_edge_ids]
    component_nodes = [node for node in selected_nodes if _node_prefix(node["id"]) == "BuildingComponent"]
    if [node["id"] for node in component_nodes] != [component_id]:
        raise ValueError("Subgraph selection admitted an unexpected building component.")
    if any(edge["src"] not in selected_node_ids or edge["tgt"] not in selected_node_ids for edge in selected_edges):
        raise ValueError("Subgraph contains an edge with an unselected endpoint.")
    return {"nodes": selected_nodes, "edges": selected_edges}


def export_subgraph(source_path: Path, output_path: Path, component_id: str = REPRESENTATIVE_COMPONENT_ID) -> None:
    """Build the representative subgraph and write it using the shared colored GEXF exporter."""
    with source_path.open(encoding="utf-8") as source_file:
        subgraph = build_subgraph(json.load(source_file), component_id)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as temp_file:
            json.dump(subgraph, temp_file, ensure_ascii=False)
            temporary_path = Path(temp_file.name)
        convert_graph(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            os.unlink(temporary_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Full DM2C knowledge-graph JSON file")
    parser.add_argument("output", type=Path, help="Calculation subgraph GEXF file to create")
    args = parser.parse_args()
    export_subgraph(args.source, args.output)


if __name__ == "__main__":
    main()
