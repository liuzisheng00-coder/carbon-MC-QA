"""Convert the DM2C JSON knowledge-graph export to Graphology/Gephi Lite JSON."""

import argparse
import json
from pathlib import Path


def convert_graph(source_path: Path, output_path: Path) -> None:
    """Convert a DM2C ``nodes``/``edges`` export into Graphology serialization."""
    with source_path.open(encoding="utf-8") as source_file:
        source = json.load(source_file)

    source_nodes = source.get("nodes", [])
    source_edges = source.get("edges", [])
    known_node_ids = set()
    nodes = []
    for node in source_nodes:
        node_id = node.get("id")
        if not node_id:
            raise ValueError("Every source node must contain a non-empty 'id'.")
        if node_id in known_node_ids:
            raise ValueError(f"Duplicate source node id: {node_id}")
        known_node_ids.add(node_id)

        attributes = dict(node.get("props", {}))
        labels = list(node.get("labels", []))
        attributes["labels"] = labels
        attributes["primaryLabel"] = labels[-1] if labels else "Unlabelled"
        nodes.append({"key": node_id, "attributes": attributes})

    edges = []
    for edge in source_edges:
        edge_id = edge.get("id")
        source_id = edge.get("src")
        target_id = edge.get("tgt")
        if not edge_id or not source_id or not target_id:
            raise ValueError("Every source edge must contain non-empty 'id', 'src', and 'tgt' values.")
        if source_id not in known_node_ids or target_id not in known_node_ids:
            raise ValueError(f"Edge {edge_id} references a node that does not exist.")

        attributes = dict(edge.get("props", {}))
        attributes["type"] = edge.get("type", "RELATED_TO")
        edges.append(
            {"key": edge_id, "source": source_id, "target": target_id, "attributes": attributes}
        )

    graphology_graph = {
        "attributes": {
            "name": "DM2C multigranular carbon knowledge graph",
            "sourceFormat": "DM2C JSON",
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
        },
        "options": {"type": "directed", "multi": True, "allowSelfLoops": True},
        "nodes": nodes,
        "edges": edges,
    }
    with output_path.open("w", encoding="utf-8", newline="\n") as output_file:
        json.dump(graphology_graph, output_file, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="DM2C knowledge-graph JSON file")
    parser.add_argument("output", type=Path, help="Gephi Lite-compatible JSON file to create")
    args = parser.parse_args()
    convert_graph(args.source, args.output)


if __name__ == "__main__":
    main()
