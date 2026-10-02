"""Export the full graph with the representative calculation chain highlighted."""

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from convert_kg_to_gephi_gexf import convert_graph
from export_representative_calculation_subgraph import build_subgraph


HIGHLIGHTED_NODE_SIZE = 14.0
ORDINARY_NODE_SIZE = 1.0


def _display_label(node: dict[str, Any]) -> str:
    name = node.get("props", {}).get("name")
    if name:
        return str(name)
    return node["id"].split(":", 1)[0]


def build_annotated_graph(
    source: dict[str, list[dict[str, Any]]], highlighted_node_ids: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Copy a full graph and add display attributes for highlighted calculation nodes."""
    annotated = copy.deepcopy(source)
    for node in annotated["nodes"]:
        properties = node.setdefault("props", {})
        highlighted = node["id"] in highlighted_node_ids
        properties["isCalculationSubgraph"] = highlighted
        properties["displayLabel"] = _display_label(node) if highlighted else ""
        properties["displaySize"] = HIGHLIGHTED_NODE_SIZE if highlighted else ORDINARY_NODE_SIZE
    return annotated


def export_annotated_total_graph(source_path: Path, output_path: Path) -> None:
    """Write a colored total GEXF in which the representative calculation chain is highlighted."""
    with source_path.open(encoding="utf-8") as source_file:
        source = json.load(source_file)
    calculation_subgraph = build_subgraph(source, component_id=(
        "BuildingComponent:007432cb03b54e908f9a7c5cf5dc2c109cdc8acb8076ec6a03b9930049d3327e"
    ))
    highlighted_node_ids = {node["id"] for node in calculation_subgraph["nodes"]}
    annotated = build_annotated_graph(source, highlighted_node_ids)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as temp_file:
            json.dump(annotated, temp_file, ensure_ascii=False)
            temporary_path = Path(temp_file.name)
        convert_graph(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            os.unlink(temporary_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Full DM2C knowledge-graph JSON file")
    parser.add_argument("output", type=Path, help="Annotated total-graph GEXF file to create")
    args = parser.parse_args()
    export_annotated_total_graph(args.source, args.output)


if __name__ == "__main__":
    main()
