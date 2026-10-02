"""Canonical M2.3 KG v2 vocabulary, identity helpers, and LPG export.

This module intentionally has no dependency on the legacy runtime vocabulary.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "m23-canonical-v2"

APPLICATION_CLASSES: Tuple[str, ...] = (
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
)

PRINCIPAL_EDGE_TRIPLES: Tuple[Tuple[str, str, str], ...] = (
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
)

PRINCIPAL_PREDICATES: Tuple[str, ...] = tuple(dict.fromkeys(edge[1] for edge in PRINCIPAL_EDGE_TRIPLES))
OPTIONAL_CONTEXT_PREDICATES = frozenset(
    {"associatedWithProcess", "recordedForResource", "directlyPrecedes"}
)

FORBIDDEN_RUNTIME_LABELS = frozenset(
    {
        "AtomicCarbonEmission",
        "AggregateCarbonEmission",
        "AttributionObject",
        "ProductObject",
        "ProductType",
        "SourceObject",
        "FactoryTarget",
        "ConsumptionDriver",
        "ProcessElement",
        "Resource",
        "Material",
        "bim_Quantity",
        "CarbonBoundary",
        "VersionMapping",
        "GranularityLevel",
        "DataSource",
        "CaseSpecificExtension",
    }
)
FORBIDDEN_RUNTIME_RELATIONS = frozenset(
    {"emissionOf", "hasProductType", "relatedToQuantity", "hasTotalEmission"}
)

_APPLICATION_CLASS_SET = frozenset(APPLICATION_CLASSES)
_PRINCIPAL_TRIPLE_SET = frozenset(PRINCIPAL_EDGE_TRIPLES)
_IFC_REFINEMENT = re.compile(r"^Ifc[A-Za-z0-9_]+$")


class NodeIdCollisionError(ValueError):
    """Raised when one stable node id is reused for different entity content."""


def _identity_digest(kind: str, identity_parts: Sequence[Any]) -> str:
    payload = json.dumps(
        [kind, *identity_parts], ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def stable_id(kind: str, *identity_parts: Any) -> str:
    """Return a Unicode-safe deterministic id without relying on display-name slugs."""
    if not isinstance(kind, str) or not kind:
        raise ValueError("kind must be a non-empty string")
    return f"{kind}:{_identity_digest(kind, identity_parts)}"


def stable_edge_id(rel_type: str, src: str, tgt: str, *identity_parts: Any) -> str:
    """Return a deterministic relationship id for one semantic occurrence."""
    if not isinstance(rel_type, str) or not rel_type:
        raise ValueError("rel_type must be a non-empty string")
    return f"edge:{_identity_digest(rel_type, (src, tgt, *identity_parts))}"


_NEO4J_SCALAR_TYPES = (bool, int, float, str)


def neo4j_safe_property_value(value: Any, *, path: str = "property") -> Any:
    """Validate and normalize one Neo4j-storable property value.

    Neo4j properties are scalars or homogeneous arrays of scalars.  Maps,
    nested collections, null array members and non-finite numbers are rejected
    before they can reach JSON or Cypher export.
    """

    if type(value) not in _NEO4J_SCALAR_TYPES:
        if not isinstance(value, (list, tuple)):
            raise TypeError(
                f"Neo4j property {path!r} must be a scalar or homogeneous scalar list"
            )
        normalized = list(value)
        if not normalized:
            return normalized
        element_types = {type(item) for item in normalized}
        if len(element_types) != 1 or not element_types <= set(_NEO4J_SCALAR_TYPES):
            raise TypeError(
                f"Neo4j property {path!r} requires a homogeneous scalar list"
            )
        for index, item in enumerate(normalized):
            neo4j_safe_property_value(item, path=f"{path}[{index}]")
        return normalized
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Neo4j property {path!r} must be finite")
    if isinstance(value, int) and not isinstance(value, bool) and not (
        -(2**63) <= value <= 2**63 - 1
    ):
        raise ValueError(f"Neo4j property {path!r} must fit a signed 64-bit integer")
    return value


def clean_neo4j_properties(
    props: Optional[Mapping[str, Any]], *, path: str = "properties"
) -> Dict[str, Any]:
    if props is not None and not isinstance(props, Mapping):
        raise TypeError(f"Neo4j {path} must be a property mapping")
    cleaned: Dict[str, Any] = {}
    for key, value in (props or {}).items():
        if type(key) is not str:
            raise TypeError(f"Neo4j property key at {path!r} must already be a string")
        if value is None or value == "":
            continue
        cleaned[key] = neo4j_safe_property_value(value, path=f"{path}.{key}")
    return cleaned


def _clean_props(props: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return clean_neo4j_properties(props)


def _cypher_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_cypher_value(item) for item in value) + "]"
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return "'" + escaped + "'"


def _cypher_key(key: str) -> str:
    return "`" + str(key).replace("`", "``") + "`"


class CanonicalLPGGraph:
    """A deterministic, collision-safe LPG restricted to the M2.3 v2 schema."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self._edges_by_id: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _validate_labels(labels: Iterable[str]) -> List[str]:
        cleaned = sorted({str(label) for label in labels if str(label).strip()})
        if not cleaned:
            raise ValueError("a canonical node requires at least one label")
        forbidden = set(cleaned) & FORBIDDEN_RUNTIME_LABELS
        if forbidden:
            raise ValueError(f"forbidden runtime label(s): {sorted(forbidden)}")
        invalid = [label for label in cleaned if label not in _APPLICATION_CLASS_SET and not _IFC_REFINEMENT.fullmatch(label)]
        if invalid:
            raise ValueError(f"unsupported canonical label(s): {invalid}")
        if not set(cleaned) & _APPLICATION_CLASS_SET:
            raise ValueError("a canonical node requires one application class label")
        return cleaned

    @staticmethod
    def _application_labels(node: Mapping[str, Any]) -> frozenset[str]:
        return frozenset(node.get("labels", ())) & _APPLICATION_CLASS_SET

    def add_node(self, node_id: str, labels: Iterable[str], props: Optional[Mapping[str, Any]] = None) -> None:
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string")
        clean_labels = self._validate_labels(labels)
        clean_props = _clean_props(props)
        current = self.nodes.get(node_id)
        if current is None:
            self.nodes[node_id] = {"id": node_id, "labels": clean_labels, "props": clean_props}
            return

        existing_application_labels = self._application_labels(current)
        incoming_application_labels = frozenset(clean_labels) & _APPLICATION_CLASS_SET
        if existing_application_labels != incoming_application_labels:
            raise NodeIdCollisionError(
                f"node id {node_id!r} was reused with conflicting application labels: "
                f"{sorted(existing_application_labels)} != {sorted(incoming_application_labels)}"
            )
        conflicts = {
            key: (current["props"][key], value)
            for key, value in clean_props.items()
            if key in current["props"] and current["props"][key] != value
        }
        if conflicts:
            raise NodeIdCollisionError(f"node id {node_id!r} was reused with conflicting properties: {conflicts}")
        current["labels"] = sorted(set(current["labels"]) | set(clean_labels))
        current["props"].update(clean_props)

    def _validate_edge(self, src: str, rel_type: str, tgt: str) -> None:
        if src not in self.nodes or tgt not in self.nodes:
            raise ValueError("canonical edges require existing source and target nodes")
        if rel_type in FORBIDDEN_RUNTIME_RELATIONS:
            raise ValueError(f"forbidden runtime relation: {rel_type}")
        source_labels = self._application_labels(self.nodes[src])
        target_labels = self._application_labels(self.nodes[tgt])
        if any((source, rel_type, target) in _PRINCIPAL_TRIPLE_SET for source in source_labels for target in target_labels):
            return
        if rel_type == "associatedWithProcess" and source_labels == {"EnergyConsumption"} and target_labels <= {
            "ProductionStage",
            "ManufacturingActivity",
        }:
            return
        if rel_type == "recordedForResource" and source_labels == {"EnergyConsumption"} and target_labels == {
            "ManufacturingResource"
        }:
            return
        if (
            rel_type == "directlyPrecedes"
            and source_labels == {"ManufacturingActivity"}
            and target_labels == {"ManufacturingActivity"}
        ):
            return
        raise ValueError(f"invalid canonical edge triple: {sorted(source_labels)} -{rel_type}-> {sorted(target_labels)}")

    def add_edge(
        self,
        src: str,
        rel_type: str,
        tgt: str,
        props: Optional[Mapping[str, Any]] = None,
        *,
        occurrence_id: Optional[str] = None,
        identity_parts: Sequence[Any] = (),
    ) -> None:
        if not isinstance(rel_type, str) or rel_type not in set(PRINCIPAL_PREDICATES) | OPTIONAL_CONTEXT_PREDICATES:
            raise ValueError(f"unsupported canonical relation: {rel_type}")
        self._validate_edge(src, rel_type, tgt)
        clean_props = _clean_props(props)
        prop_occurrence = clean_props.pop("occurrenceId", None)
        if occurrence_id is not None and prop_occurrence is not None and occurrence_id != prop_occurrence:
            raise ValueError("occurrence_id conflicts with props['occurrenceId']")
        occurrence = str(occurrence_id if occurrence_id is not None else prop_occurrence or stable_edge_id(rel_type, src, tgt, *identity_parts))
        edge_id = stable_edge_id(rel_type, src, tgt, occurrence)
        candidate = {
            "id": edge_id,
            "src": src,
            "type": rel_type,
            "tgt": tgt,
            "occurrenceId": occurrence,
            "props": clean_props,
        }
        existing = self._edges_by_id.get(edge_id)
        if existing is None:
            self._edges_by_id[edge_id] = candidate
            self.edges.append(candidate)
            return
        if existing != candidate:
            raise ValueError(f"edge occurrence {occurrence!r} was reused with conflicting content")

    def _sorted_nodes(self) -> List[Dict[str, Any]]:
        return [self.nodes[node_id] for node_id in sorted(self.nodes)]

    def _sorted_edges(self) -> List[Dict[str, Any]]:
        return sorted(self.edges, key=lambda edge: (edge["src"], edge["type"], edge["tgt"], edge["occurrenceId"], edge["id"]))

    def validate_property_types(self) -> None:
        for node_id, node in self.nodes.items():
            clean_neo4j_properties(node.get("props", {}), path=f"node[{node_id}].props")
        for edge in self.edges:
            clean_neo4j_properties(
                edge.get("props", {}), path=f"edge[{edge.get('id', '')}].props"
            )

    def to_payload(self) -> Dict[str, Any]:
        self.validate_property_types()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "nodes": self._sorted_nodes(),
            "edges": self._sorted_edges(),
        }

    def export_json(self, path: Path) -> None:
        Path(path).write_text(
            json.dumps(
                self.to_payload(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ),
            encoding="utf-8",
        )

    def to_cypher(self, wipe_before_import: bool = False) -> str:
        self.validate_property_types()
        lines: List[str] = [
            "// DM2C M2.3 canonical v2 carbon knowledge graph",
            f"// schemaVersion={SCHEMA_VERSION}",
            "",
        ]
        if wipe_before_import:
            lines.extend(["MATCH (n) DETACH DELETE n;", ""])
        lines.extend(["CREATE CONSTRAINT IF NOT EXISTS FOR (n:DM2CEntity) REQUIRE n.id IS UNIQUE;", "", "// Nodes"])
        for node in self._sorted_nodes():
            labels = ":".join(["DM2CEntity", *node["labels"]])
            lines.append(f"MERGE (n:{labels} {{id: {_cypher_value(node['id'])}}})")
            if node["props"]:
                properties = ", ".join(
                    f"{_cypher_key(key)}: {_cypher_value(value)}" for key, value in sorted(node["props"].items())
                )
                lines.append(f"SET n += {{ {properties} }};")
            else:
                lines.append(";")
        lines.extend(["", "// Relationships"])
        for edge in self._sorted_edges():
            lines.append(
                f"MATCH (a:DM2CEntity {{id: {_cypher_value(edge['src'])}}}), "
                f"(b:DM2CEntity {{id: {_cypher_value(edge['tgt'])}}})"
            )
            lines.append(
                f"MERGE (a)-[r:{edge['type']} {{occurrenceId: {_cypher_value(edge['occurrenceId'])}}}]->(b)"
            )
            if edge["props"]:
                properties = ", ".join(
                    f"{_cypher_key(key)}: {_cypher_value(value)}" for key, value in sorted(edge["props"].items())
                )
                lines.append(f"SET r += {{ {properties} }};")
            else:
                lines.append(";")
        return "\n".join(lines)

    def export_cypher(self, path: Path, wipe_before_import: bool = False) -> None:
        Path(path).write_bytes(
            self.to_cypher(wipe_before_import=wipe_before_import).encode("utf-8")
        )

    def count_label(self, label: str) -> int:
        return sum(label in node["labels"] for node in self.nodes.values())

    def count_relation(self, relation: str) -> int:
        return sum(edge["type"] == relation for edge in self.edges)

    def edges_of_type(self, relation: str) -> List[Dict[str, Any]]:
        return [edge for edge in self._sorted_edges() if edge["type"] == relation]


__all__ = [
    "APPLICATION_CLASSES",
    "FORBIDDEN_RUNTIME_LABELS",
    "FORBIDDEN_RUNTIME_RELATIONS",
    "OPTIONAL_CONTEXT_PREDICATES",
    "PRINCIPAL_EDGE_TRIPLES",
    "PRINCIPAL_PREDICATES",
    "SCHEMA_VERSION",
    "CanonicalLPGGraph",
    "NodeIdCollisionError",
    "clean_neo4j_properties",
    "neo4j_safe_property_value",
    "stable_edge_id",
    "stable_id",
]
