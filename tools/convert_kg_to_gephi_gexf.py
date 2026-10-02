"""Convert the DM2C JSON knowledge-graph export to a Gephi Lite-compatible GEXF file."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


GEXF_NAMESPACE = "http://www.gexf.net/1.2draft"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
VIZ_NAMESPACE = "http://www.gexf.net/1.2draft/viz"
SCHEMA_LOCATION = "http://www.gexf.net/1.2draft http://www.gexf.net/1.2draft/gexf.xsd"
TYPE_COLOR_PALETTE = (
    (31, 119, 180),
    (255, 127, 14),
    (44, 160, 44),
    (214, 39, 40),
    (148, 103, 189),
    (140, 86, 75),
    (227, 119, 194),
    (188, 189, 34),
    (23, 190, 207),
)

ET.register_namespace("", GEXF_NAMESPACE)
ET.register_namespace("xsi", XSI_NAMESPACE)
ET.register_namespace("viz", VIZ_NAMESPACE)


def _tag(name: str) -> str:
    return f"{{{GEXF_NAMESPACE}}}{name}"


def _viz_tag(name: str) -> str:
    return f"{{{VIZ_NAMESPACE}}}{name}"


def _as_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _primary_label(node: dict[str, Any]) -> str:
    labels = list(node.get("labels", []))
    return labels[-1] if labels else "Unlabelled"


def _display_label(node: dict[str, Any]) -> str:
    properties = node.get("props", {})
    if "displayLabel" in properties:
        return str(properties["displayLabel"])
    name = properties.get("name")
    if name:
        return str(name)
    return str(node["id"]).split(":", 1)[0]


def _type_color(primary_label: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(primary_label.encode("utf-8")).digest()
    return TYPE_COLOR_PALETTE[int.from_bytes(digest[:4], "big") % len(TYPE_COLOR_PALETTE)]


def _collect_attribute_names(items: list[dict[str, Any]], additional: set[str]) -> list[str]:
    names = set(additional)
    for item in items:
        names.update(item.get("props", {}).keys())
    return sorted(names)


def _append_attribute_definitions(parent: ET.Element, attribute_names: list[str]) -> dict[str, str]:
    attribute_ids = {name: f"a{index}" for index, name in enumerate(attribute_names)}
    for name in attribute_names:
        ET.SubElement(parent, _tag("attribute"), id=attribute_ids[name], title=name, type="string")
    return attribute_ids


def _append_values(parent: ET.Element, values: dict[str, Any], attribute_ids: dict[str, str]) -> None:
    if not values:
        return
    attvalues = ET.SubElement(parent, _tag("attvalues"))
    for name, value in values.items():
        if name in attribute_ids and value is not None:
            ET.SubElement(
                attvalues,
                _tag("attvalue"),
                **{"for": attribute_ids[name], "value": _as_text(value)},
            )


def convert_graph(source_path: Path, output_path: Path) -> None:
    """Convert a DM2C ``nodes``/``edges`` export into GEXF 1.2."""
    with source_path.open(encoding="utf-8") as source_file:
        source = json.load(source_file)

    source_nodes = source.get("nodes", [])
    source_edges = source.get("edges", [])
    node_ids = {node.get("id") for node in source_nodes}
    if None in node_ids or "" in node_ids or len(node_ids) != len(source_nodes):
        raise ValueError("Every source node must have a unique non-empty 'id'.")

    for edge in source_edges:
        if not edge.get("id") or not edge.get("src") or not edge.get("tgt"):
            raise ValueError("Every source edge must have non-empty 'id', 'src', and 'tgt' values.")
        if edge["src"] not in node_ids or edge["tgt"] not in node_ids:
            raise ValueError(f"Edge {edge['id']} references a node that does not exist.")

    root = ET.Element(
        _tag("gexf"),
        version="1.2",
        **{f"{{{XSI_NAMESPACE}}}schemaLocation": SCHEMA_LOCATION},
    )
    meta = ET.SubElement(root, _tag("meta"), lastmodifieddate="2026-08-05")
    ET.SubElement(meta, _tag("creator")).text = "DM2C Gephi Lite converter"
    ET.SubElement(meta, _tag("description")).text = "Multigranular carbon knowledge graph"
    graph = ET.SubElement(root, _tag("graph"), mode="static", defaultedgetype="directed")

    node_attribute_names = _collect_attribute_names(source_nodes, {"labels", "primaryLabel"})
    edge_attribute_names = _collect_attribute_names(source_edges, {"relationshipType"})
    node_attributes = ET.SubElement(graph, _tag("attributes"), **{"class": "node"})
    edge_attributes = ET.SubElement(graph, _tag("attributes"), **{"class": "edge"})
    node_attribute_ids = _append_attribute_definitions(node_attributes, node_attribute_names)
    edge_attribute_ids = _append_attribute_definitions(edge_attributes, edge_attribute_names)

    nodes_element = ET.SubElement(graph, _tag("nodes"))
    for node in source_nodes:
        properties = dict(node.get("props", {}))
        labels = list(node.get("labels", []))
        properties["labels"] = labels
        primary_label = _primary_label(node)
        properties["primaryLabel"] = primary_label
        node_element = ET.SubElement(
            nodes_element,
            _tag("node"),
            id=node["id"],
            label=_display_label(node),
        )
        _append_values(node_element, properties, node_attribute_ids)
        red, green, blue = _type_color(primary_label)
        ET.SubElement(
            node_element,
            _viz_tag("color"),
            r=str(red),
            g=str(green),
            b=str(blue),
        )
        if properties.get("displaySize") is not None:
            ET.SubElement(node_element, _viz_tag("size"), value=str(properties["displaySize"]))

    edges_element = ET.SubElement(graph, _tag("edges"))
    for edge in source_edges:
        properties = dict(edge.get("props", {}))
        properties["relationshipType"] = edge.get("type", "RELATED_TO")
        edge_element = ET.SubElement(
            edges_element,
            _tag("edge"),
            id=edge["id"],
            source=edge["src"],
            target=edge["tgt"],
        )
        _append_values(edge_element, properties, edge_attribute_ids)

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="DM2C knowledge-graph JSON file")
    parser.add_argument("output", type=Path, help="GEXF file to create")
    args = parser.parse_args()
    convert_graph(args.source, args.output)


if __name__ == "__main__":
    main()
