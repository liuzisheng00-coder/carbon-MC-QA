"""Independent alignment audit for the DM2C M2.3 canonical v2 release.

The audit deliberately works from serialized documents rather than trusting the
builder.  It is used twice by the release runner: once before the manifest is
written and once after all five manifest-bound artifacts have been hashed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping, Sequence

from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    FORBIDDEN_RUNTIME_LABELS,
    FORBIDDEN_RUNTIME_RELATIONS,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_EDGE_TRIPLES,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    CanonicalLPGGraph,
    clean_neo4j_properties,
    stable_edge_id,
    stable_id,
)
from dm2c_m23_units import validate_quantity_factor
from dm2c_m23_ifc import (
    EXCLUDED_FACTORY_BACKBONE_TYPES,
    IFCExtractionResult,
    deduplicate_material_associations,
)


_APP_CLASSES = frozenset(APPLICATION_CLASSES)
_PRINCIPAL_TRIPLES = frozenset(PRINCIPAL_EDGE_TRIPLES)
_RELATIONS = frozenset(PRINCIPAL_PREDICATES) | OPTIONAL_CONTEXT_PREDICATES
_IFC_REFINEMENT = re.compile(r"^Ifc[A-Za-z0-9_]+$")
_GRAPH_KEYS = frozenset({"schemaVersion", "nodes", "edges"})
_NODE_KEYS = frozenset({"id", "labels", "props"})
_EDGE_KEYS = frozenset({"id", "src", "type", "tgt", "occurrenceId", "props"})
_REQUIRED_CONTEXT_STRINGS = frozenset(
    {
        "requestedScope",
        "ifcSha256",
        "moduleId",
        "moduleSourceIdentity",
        "moduleName",
    }
)
_ALLOCATION_FIELDS = frozenset(
    {
        "allocated",
        "allocationSetId",
        "allocationBasis",
        "rawWeight",
        "rawWeightUnit",
        "allocatedFraction",
        "evidenceRecordId",
    }
)
_OUTPUTS = {
    "graphJson": "multigranular_carbon_kg.json",
    "cypher": "multigranular_carbon_kg.cypher",
    "stats": "multigranular_carbon_kg_stats.json",
    "validation": "multigranular_carbon_kg_validation.csv",
    "alignmentReport": "m2_alignment_report.json",
}
_EXACT_RELEASE_FILES = frozenset((*_OUTPUTS.values(), "case_version_manifest.json"))
_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "releaseId",
        "releaseProfile",
        "releaseReady",
        "generatedAtUtc",
        "syntheticFactoryInputsUsed",
        "inputs",
        "module",
        "configuration",
        "command",
        "coverage",
        "counts",
        "code",
        "outputs",
        "gates",
    }
)
_VALIDATION_FIELDS = (
    "recordId",
    "sourceIdentity",
    "sourceRecordId",
    "evidenceSourceId",
    "kind",
    "status",
    "reasonCode",
    "message",
    "consumptionId",
    "quantityId",
    "factorId",
    "emissionId",
    "formulaCode",
    "isValidZero",
    "evidenceJson",
)
_CODE_FILES = frozenset(
    {
        "dm2c_m23_canonical_release.py",
        "dm2c_m23_canonical.py",
        "dm2c_m23_ifc.py",
        "dm2c_m23_calculation.py",
        "dm2c_m23_accounting.py",
        "dm2c_multigranular_carbon_kg.py",
        "dm2c_m2_alignment_audit.py",
    }
)
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "allocationFraction",
        "allocationKey",
        "atomId",
        "blockedReason",
        "calculationStatus",
        "factoryTarget",
        "factoryTargetId",
        "isBlocked",
    }
)
_FACTORY_INPUT_CLASSES = frozenset(
    {
        "ProductionBatch",
        "ManufacturingProcessTemplate",
        "ProductionStage",
        "ManufacturingActivity",
        "ManufacturingResource",
        "EnergyConsumption",
        "EnergyCarrier",
    }
)


def _scope_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    normalized = value.casefold().translate(
        str.maketrans(
            {
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "―": "-",
                "−": "-",
            }
        )
    )
    match = re.search(r"a\s*(\d+)\s*-\s*a\s*(\d+)", normalized)
    if match:
        return f"a{match.group(1)}-a{match.group(2)}"
    return re.sub(r"\s+", "", normalized)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _strict_json_object(text: str) -> Mapping[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        text,
        object_pairs_hook=pairs,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {token}")
        ),
    )
    if not isinstance(value, Mapping):
        raise ValueError("provenance entry must be a JSON object")
    return value


def _finding(
    findings: list[dict[str, Any]],
    code: str,
    *,
    artifact: str = "graph",
    entity_id: object = "",
    message: str,
    evidence: Mapping[str, Any] | None = None,
) -> None:
    findings.append(
        {
            "severity": "high",
            "code": code,
            "artifact": artifact,
            "entityId": str(entity_id or ""),
            "message": message,
            "evidence": dict(evidence or {}),
        }
    )


def _sorted_findings(findings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for source in findings:
        row = {
            "severity": str(source.get("severity", "high")),
            "code": str(source.get("code", "audit_failure")),
            "artifact": str(source.get("artifact", "graph")),
            "entityId": str(source.get("entityId", "")),
            "message": str(source.get("message", "")),
            "evidence": dict(source.get("evidence", {}) or {}),
        }
        key = _canonical_json(row)
        unique[key] = row
    return sorted(
        unique.values(),
        key=lambda row: (
            row["severity"],
            row["code"],
            row["artifact"],
            row["entityId"],
            row["message"],
            _canonical_json(row["evidence"]),
        ),
    )


def _application_labels(node: Mapping[str, Any]) -> frozenset[str]:
    labels = node.get("labels", ())
    if not isinstance(labels, (list, tuple)):
        return frozenset()
    return frozenset(label for label in labels if isinstance(label, str)) & _APP_CLASSES


def _node_class(node: Mapping[str, Any]) -> str:
    labels = _application_labels(node)
    return next(iter(labels)) if len(labels) == 1 else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _forbidden_payload_details(props: Any) -> tuple[list[str], list[str]]:
    if not isinstance(props, Mapping):
        return [], []
    forbidden_keys = sorted(set(props) & _FORBIDDEN_PAYLOAD_KEYS)
    forbidden_values: list[str] = []
    for key, value in props.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, str):
                continue
            folded = item.casefold()
            if (
                "p0_factory_synth" in folded
                or folded.startswith("blocked")
                or folded == "all_batches"
                or folded.startswith("factorymodule_")
            ):
                forbidden_values.append(f"{key}={item}")
    return forbidden_keys, sorted(forbidden_values)


def _check_serialized_properties(
    props: Any,
    *,
    entity_id: str,
    path: str,
    findings: list[dict[str, Any]],
) -> None:
    try:
        if not isinstance(props, Mapping):
            raise TypeError(f"Neo4j {path} must be a property mapping")
        cleaned = clean_neo4j_properties(props, path=path)
        if cleaned != dict(props):
            raise ValueError(
                f"Neo4j {path} must already equal its lossless canonical property mapping"
            )
    except (TypeError, ValueError) as exc:
        _finding(
            findings,
            "neo4j_property_unsafe",
            entity_id=entity_id,
            message=str(exc),
        )
    forbidden_keys, forbidden_values = _forbidden_payload_details(props)
    if forbidden_keys or forbidden_values:
        _finding(
            findings,
            "forbidden_runtime_payload",
            entity_id=entity_id,
            message="entity contains blocked, placeholder, or synthetic-factory payload",
            evidence={"keys": forbidden_keys, "values": forbidden_values},
        )


def _canonical_graph_from_payload(graph: Mapping[str, Any]) -> CanonicalLPGGraph:
    """Rebuild a canonical graph solely from its serialized public contract."""

    canonical = CanonicalLPGGraph()
    nodes = graph.get("nodes", ())
    edges = graph.get("edges", ())
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("canonical graph nodes and edges must be lists")
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("canonical graph node must be an object")
        canonical.add_node(
            str(node.get("id", "")),
            node.get("labels", ()),
            node.get("props", {}),
        )
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise ValueError("canonical graph edge must be an object")
        canonical.add_edge(
            str(edge.get("src", "")),
            str(edge.get("type", "")),
            str(edge.get("tgt", "")),
            edge.get("props", {}),
            occurrence_id=str(edge.get("occurrenceId", "")),
        )
    return canonical


def _check_graph_order(
    graph: Mapping[str, Any], findings: list[dict[str, Any]]
) -> None:
    nodes = graph.get("nodes", ())
    edges = graph.get("edges", ())
    if isinstance(nodes, list):
        observed = [
            str(node.get("id", "")) for node in nodes if isinstance(node, Mapping)
        ]
        if observed != sorted(observed):
            _finding(
                findings,
                "graph_order_invalid",
                message="nodes are not in canonical id order",
            )
    if isinstance(edges, list):
        observed_edges = [
            (
                str(edge.get("src", "")),
                str(edge.get("type", "")),
                str(edge.get("tgt", "")),
                str(edge.get("occurrenceId", "")),
                str(edge.get("id", "")),
            )
            for edge in edges
            if isinstance(edge, Mapping)
        ]
        if observed_edges != sorted(observed_edges):
            _finding(
                findings,
                "graph_order_invalid",
                message="edges are not in canonical semantic order",
            )


def _edge_is_valid(
    edge: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]]
) -> bool:
    source = nodes.get(str(edge.get("src", "")))
    target = nodes.get(str(edge.get("tgt", "")))
    if source is None or target is None:
        return False
    relation = str(edge.get("type", ""))
    source_labels = _application_labels(source)
    target_labels = _application_labels(target)
    if any(
        (source_label, relation, target_label) in _PRINCIPAL_TRIPLES
        for source_label in source_labels
        for target_label in target_labels
    ):
        return True
    if relation == "associatedWithProcess":
        return source_labels == {"EnergyConsumption"} and target_labels in (
            {"ProductionStage"},
            {"ManufacturingActivity"},
        )
    if relation == "recordedForResource":
        return source_labels == {"EnergyConsumption"} and target_labels == {
            "ManufacturingResource"
        }
    if relation == "directlyPrecedes":
        return source_labels == {"ManufacturingActivity"} and target_labels == {
            "ManufacturingActivity"
        }
    return False


def _graph_indices(
    graph: Mapping[str, Any], findings: list[dict[str, Any]]
) -> tuple[
    dict[str, Mapping[str, Any]],
    list[Mapping[str, Any]],
    dict[str, list[Mapping[str, Any]]],
    dict[str, list[Mapping[str, Any]]],
]:
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])
    if not isinstance(raw_nodes, list):
        _finding(findings, "graph_shape_invalid", message="nodes must be a list")
        raw_nodes = []
    if not isinstance(raw_edges, list):
        _finding(findings, "graph_shape_invalid", message="edges must be a list")
        raw_edges = []

    nodes: dict[str, Mapping[str, Any]] = {}
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            _finding(findings, "graph_shape_invalid", message="node must be an object")
            continue
        if set(raw) != _NODE_KEYS:
            _finding(
                findings,
                "graph_shape_invalid",
                entity_id=raw.get("id", ""),
                message="node must contain exactly id/labels/props",
                evidence={
                    "missing": sorted(_NODE_KEYS - set(raw)),
                    "unknown": sorted(set(raw) - _NODE_KEYS),
                },
            )
        if type(raw.get("id")) is not str:
            _finding(
                findings,
                "graph_shape_invalid",
                entity_id=raw.get("id", ""),
                message="node id must already be a string",
            )
        node_id = str(raw.get("id", ""))
        if not node_id:
            _finding(findings, "node_id_missing", message="node id is empty")
            continue
        if node_id in nodes:
            _finding(
                findings,
                "duplicate_node_id",
                entity_id=node_id,
                message="node id occurs more than once",
            )
            continue
        nodes[node_id] = raw
        labels = raw.get("labels", [])
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            _finding(
                findings,
                "graph_shape_invalid",
                entity_id=node_id,
                message="node labels must be a string list",
            )
            labels = []
        elif labels != sorted(set(labels)):
            _finding(
                findings,
                "graph_shape_invalid",
                entity_id=node_id,
                message="node labels must be sorted and unique",
            )
        forbidden = sorted(set(labels) & FORBIDDEN_RUNTIME_LABELS)
        if forbidden:
            _finding(
                findings,
                "forbidden_runtime_vocabulary",
                entity_id=node_id,
                message="node uses forbidden runtime labels",
                evidence={"labels": forbidden},
            )
        application = set(labels) & _APP_CLASSES
        if len(application) != 1:
            _finding(
                findings,
                "application_label_cardinality",
                entity_id=node_id,
                message="node must have exactly one application-class label",
                evidence={"applicationLabels": sorted(application)},
            )
        unsupported = sorted(
            label
            for label in labels
            if label not in _APP_CLASSES and not _IFC_REFINEMENT.fullmatch(label)
        )
        if unsupported:
            _finding(
                findings,
                "unsupported_runtime_vocabulary",
                entity_id=node_id,
                message="node uses unsupported labels",
                evidence={"labels": unsupported},
            )
        _check_serialized_properties(
            raw.get("props", {}),
            entity_id=node_id,
            path=f"node[{node_id}].props",
            findings=findings,
        )

    edges: list[Mapping[str, Any]] = []
    edge_ids: set[str] = set()
    occurrence_ids: set[str] = set()
    outgoing: dict[str, list[Mapping[str, Any]]] = {}
    incoming: dict[str, list[Mapping[str, Any]]] = {}
    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            _finding(findings, "graph_shape_invalid", message="edge must be an object")
            continue
        if set(raw) != _EDGE_KEYS:
            _finding(
                findings,
                "graph_shape_invalid",
                entity_id=raw.get("id", ""),
                message="edge must contain exactly id/src/type/tgt/occurrenceId/props",
                evidence={
                    "missing": sorted(_EDGE_KEYS - set(raw)),
                    "unknown": sorted(set(raw) - _EDGE_KEYS),
                },
            )
        for field in ("id", "src", "type", "tgt", "occurrenceId"):
            if type(raw.get(field)) is not str:
                _finding(
                    findings,
                    "graph_shape_invalid",
                    entity_id=raw.get("id", ""),
                    message=f"edge {field} must already be a string",
                )
        edge_id = str(raw.get("id", ""))
        occurrence = str(raw.get("occurrenceId", ""))
        relation = str(raw.get("type", ""))
        source = str(raw.get("src", ""))
        target = str(raw.get("tgt", ""))
        if not edge_id or edge_id in edge_ids:
            _finding(
                findings,
                "duplicate_edge_id" if edge_id else "edge_id_missing",
                entity_id=edge_id,
                message="edge id is empty or duplicated",
            )
        edge_ids.add(edge_id)
        if not occurrence or occurrence in occurrence_ids:
            _finding(
                findings,
                "duplicate_occurrence_id" if occurrence else "occurrence_id_missing",
                entity_id=occurrence,
                message="relationship occurrence id is empty or duplicated",
            )
        occurrence_ids.add(occurrence)
        if relation in FORBIDDEN_RUNTIME_RELATIONS or relation not in _RELATIONS:
            _finding(
                findings,
                "forbidden_runtime_vocabulary",
                entity_id=edge_id,
                message="edge uses a forbidden or unsupported relation",
                evidence={"relation": relation},
            )
        if source not in nodes or target not in nodes:
            _finding(
                findings,
                "missing_edge_endpoint",
                entity_id=edge_id,
                message="edge endpoint does not exist",
                evidence={"src": source, "tgt": target},
            )
        elif not _edge_is_valid(raw, nodes):
            _finding(
                findings,
                "invalid_edge_triple",
                entity_id=edge_id,
                message="edge domain/range does not match the canonical typed triples",
                evidence={"relation": relation, "src": source, "tgt": target},
            )
        if edge_id and occurrence:
            expected = stable_edge_id(relation, source, target, occurrence)
            if edge_id != expected:
                _finding(
                    findings,
                    "edge_identity_invalid",
                    entity_id=edge_id,
                    message="edge id is not the stable id of its occurrence",
                    evidence={"expected": expected},
                )
        _check_serialized_properties(
            raw.get("props", {}),
            entity_id=edge_id,
            path=f"edge[{edge_id}].props",
            findings=findings,
        )
        edges.append(raw)
        outgoing.setdefault(source, []).append(raw)
        incoming.setdefault(target, []).append(raw)
    return nodes, edges, outgoing, incoming


def _relations(
    index: Mapping[str, list[Mapping[str, Any]]], node_id: str, relation: str
) -> list[Mapping[str, Any]]:
    return [edge for edge in index.get(node_id, ()) if edge.get("type") == relation]


def _check_audit_context(
    nodes: Mapping[str, Mapping[str, Any]],
    validation_rows: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> set[str]:
    invalid: set[str] = set()
    if context.get("schemaVersion") != SCHEMA_VERSION:
        invalid.add("schemaVersion")
    if context.get("releaseProfile") not in {"actual-case", "controlled-fixture"}:
        invalid.add("releaseProfile")
    for field in _REQUIRED_CONTEXT_STRINGS:
        value = context.get(field)
        if type(value) is not str or not value.strip():
            invalid.add(field)
    ifc_sha256 = context.get("ifcSha256")
    if type(ifc_sha256) is str and not re.fullmatch(r"[0-9A-F]{64}", ifc_sha256):
        invalid.add("ifcSha256")
    extraction = context.get("extraction")
    if not isinstance(extraction, IFCExtractionResult):
        invalid.add("extraction")
    elif ifc_sha256 != extraction.ifc_sha256:
        invalid.update({"extraction", "ifcSha256"})
    if context.get("oneModuleCase") is not True:
        invalid.add("oneModuleCase")
    module_id = context.get("moduleId")
    module_source = context.get("moduleSourceIdentity")
    if (
        type(ifc_sha256) is str
        and type(module_source) is str
        and ifc_sha256
        and module_source
        and module_id != stable_id("ModularUnit", ifc_sha256, module_source)
    ):
        invalid.add("moduleId")

    factor_rows = context.get("factorSourceRows")
    factor_nodes = {
        node_id for node_id, node in nodes.items() if _node_class(node) == "EmissionFactor"
    }
    if not isinstance(factor_rows, Mapping):
        invalid.add("factorSourceRows")
    elif factor_nodes and not factor_rows:
        invalid.add("factorSourceRows")

    expected_rows = context.get("expectedValidationRows")
    if (
        not isinstance(expected_rows, Sequence)
        or isinstance(expected_rows, (str, bytes, bytearray))
        or any(not isinstance(row, Mapping) for row in expected_rows)
    ):
        invalid.add("expectedValidationRows")
    elif tuple(validation_rows) != tuple(expected_rows):
        _finding(
            findings,
            "validation_source_mismatch",
            artifact="validation",
            message="validation rows differ from the independent expected validation rows",
            evidence={
                "actualRows": len(validation_rows),
                "expectedRows": len(expected_rows),
            },
        )

    measured_record_ids = context.get("measuredRecordIds")
    if not isinstance(measured_record_ids, (set, frozenset)) or any(
        type(record_id) is not str or not record_id.strip()
        for record_id in measured_record_ids
    ):
        invalid.add("measuredRecordIds")

    if invalid:
        _finding(
            findings,
            "audit_context_invalid",
            message="independent audit context is incomplete or invalid",
            evidence={"fields": sorted(invalid)},
        )
    return invalid


def _check_ifc_backbone(
    nodes: Mapping[str, Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    incoming: Mapping[str, list[Mapping[str, Any]]],
    context: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    components = {
        node_id
        for node_id, node in nodes.items()
        if _node_class(node) == "BuildingComponent"
    }
    modules = {
        node_id for node_id, node in nodes.items() if _node_class(node) == "ModularUnit"
    }
    module_id = str(context.get("moduleId", ""))
    if context.get("oneModuleCase"):
        if len(modules) != 1 or not module_id or module_id not in modules:
            _finding(
                findings,
                "module_parent_invalid",
                entity_id=module_id,
                message="one-module case requires exactly the configured ModularUnit",
                evidence={"modules": sorted(modules)},
            )
        for component_id in sorted(components):
            parents = [
                edge
                for edge in incoming.get(component_id, ())
                if edge.get("type") == "containsComponent"
            ]
            if len(parents) != 1 or str(parents[0].get("src", "")) != module_id:
                _finding(
                    findings,
                    "module_parent_invalid",
                    entity_id=component_id,
                    message="component must have exactly one configured module parent",
                    evidence={
                        "parents": sorted(str(edge.get("src", "")) for edge in parents)
                    },
                )

    extraction = context["extraction"]
    expected_nodes: dict[str, dict[str, Any]] = {}
    expected_edges: list[dict[str, Any]] = []

    def expected_node(
        node_id: str, labels: Iterable[str], props: Mapping[str, Any]
    ) -> None:
        expected_nodes[node_id] = {
            "id": node_id,
            "labels": sorted(set(labels)),
            "props": clean_neo4j_properties(
                props, path=f"expectedBackboneNode[{node_id}].props"
            ),
        }

    def expected_edge(
        source: str,
        relation: str,
        target: str,
        occurrence: str,
        props: Mapping[str, Any] | None = None,
    ) -> None:
        expected_edges.append(
            {
                "id": stable_edge_id(relation, source, target, occurrence),
                "src": source,
                "type": relation,
                "tgt": target,
                "occurrenceId": occurrence,
                "props": clean_neo4j_properties(
                    props, path=f"expectedBackboneEdge[{occurrence}].props"
                ),
            }
        )

    module_source = str(context.get("moduleSourceIdentity", ""))
    module_name = str(context.get("moduleName", ""))
    if context.get("oneModuleCase") and module_id and module_source:
        expected_node(
            module_id,
            ("ModularUnit",),
            {
                "ifcSha256": extraction.ifc_sha256,
                "sourceIdentity": module_source,
                "name": module_name,
            },
        )

    for component in sorted(extraction.components, key=lambda row: row.id):
        expected_node(
            component.id,
            ("BuildingComponent", component.ifc_class),
            {
                "ifcSha256": component.ifc_hash,
                "globalId": component.global_id,
                "stepId": component.step_id,
                "ifcClass": component.ifc_class,
                "name": component.name,
                "description": component.description,
                "objectType": component.object_type,
                "predefinedType": component.predefined_type,
                "tag": component.tag,
            },
        )
        if module_id and module_source:
            occurrence = stable_id(
                "ModuleComponentAssociation",
                extraction.ifc_sha256,
                module_source,
                component.id,
            )
            expected_edge(
                module_id,
                "containsComponent",
                component.id,
                occurrence,
                {"sourceIdentity": module_source},
            )
        component_type = component.component_type
        if component_type is not None:
            expected_node(
                component_type.id,
                ("ComponentType", component_type.ifc_class),
                {
                    "ifcSha256": component_type.ifc_hash,
                    "globalId": component_type.global_id,
                    "stepId": component_type.step_id,
                    "ifcClass": component_type.ifc_class,
                    "name": component_type.name,
                    "description": component_type.description,
                    "tag": component_type.tag,
                },
            )
            occurrence = stable_id(
                "ComponentTypeAssignment", component.id, component_type.id
            )
            expected_edge(
                component.id,
                "hasComponentType",
                component_type.id,
                occurrence,
            )

    for association in sorted(
        deduplicate_material_associations(extraction.material_associations),
        key=lambda row: row.id,
    ):
        expected_node(
            association.material_id,
            ("IfcMaterial",),
            {
                "ifcSha256": association.ifc_hash,
                "materialStepId": association.material_step_id,
                "name": association.material_name,
            },
        )
        expected_edge(
            association.component_id,
            "hasMaterial",
            association.material_id,
            association.id,
            {
                "associationRecordId": association.id,
                "associationStepId": association.association_step_id,
                "sourceKind": association.source_kind,
                "containerKind": association.container_kind,
                "containerStepId": association.container_step_id,
                "itemStepId": association.item_step_id,
                "layerOrConstituentIndex": association.layer_or_constituent_index,
                "thickness": association.thickness,
                "thicknessUnit": association.thickness_unit,
                "constituentFraction": association.constituent_fraction,
            },
        )

    for quantity in sorted(extraction.design_quantities, key=lambda row: row.id):
        expected_node(
            quantity.id,
            ("DesignQuantity", quantity.quantity_subtype),
            {
                "ifcSha256": quantity.ifc_hash,
                "componentId": quantity.component_id,
                "quantityStepId": quantity.quantity_step_id,
                "qtoSetStepId": quantity.qto_set_step_id,
                "qtoSetName": quantity.qto_set_name,
                "quantityName": quantity.quantity_name,
                "quantitySubtype": quantity.quantity_subtype,
                "rawValue": quantity.source_value,
                "rawUnit": quantity.source_unit,
                "sourceValue": quantity.source_value,
                "sourceUnit": quantity.source_unit,
                "normalizedValue": quantity.normalized_value,
                "normalizedUnit": quantity.normalized_unit,
            },
        )
        expected_edge(
            quantity.component_id,
            "hasDesignQuantity",
            quantity.id,
            quantity.id,
        )

    backbone_classes = {
        "ModularUnit",
        "BuildingComponent",
        "ComponentType",
        "IfcMaterial",
        "DesignQuantity",
    }
    actual_nodes = {
        node_id: dict(node)
        for node_id, node in nodes.items()
        if _node_class(node) in backbone_classes
    }
    backbone_relations = {
        "containsComponent",
        "hasComponentType",
        "hasMaterial",
        "hasDesignQuantity",
    }
    actual_edges = [dict(edge) for edge in edges if edge.get("type") in backbone_relations]
    edge_key = lambda edge: (
        str(edge.get("src", "")),
        str(edge.get("type", "")),
        str(edge.get("tgt", "")),
        str(edge.get("occurrenceId", "")),
        str(edge.get("id", "")),
    )
    expected_edges.sort(key=edge_key)
    actual_edges.sort(key=edge_key)
    mismatched_nodes = sorted(
        node_id
        for node_id in set(actual_nodes) & set(expected_nodes)
        if actual_nodes[node_id] != expected_nodes[node_id]
    )
    if actual_nodes != expected_nodes or actual_edges != expected_edges:
        _finding(
            findings,
            "ifc_backbone_mismatch",
            message="serialized IFC backbone differs from exact independent extraction projection",
            evidence={
                "missingNodes": sorted(set(expected_nodes) - set(actual_nodes))[:10],
                "unexpectedNodes": sorted(set(actual_nodes) - set(expected_nodes))[:10],
                "mismatchedNodes": mismatched_nodes[:10],
                "expectedEdges": len(expected_edges),
                "actualEdges": len(actual_edges),
            },
        )


def _check_runtime_payload_and_orphans(
    nodes: Mapping[str, Mapping[str, Any]],
    outgoing: Mapping[str, list[Mapping[str, Any]]],
    incoming: Mapping[str, list[Mapping[str, Any]]],
    context: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    orphan_requirements: dict[str, tuple[tuple[str, frozenset[str]], ...]] = {
        "ProductionBatch": (("out", frozenset({"produces"})),),
        "ModularUnit": (
            ("out", frozenset({"containsComponent"})),
            ("in", frozenset({"produces"})),
        ),
        "BuildingComponent": (
            ("in", frozenset({"containsComponent", "recordedForObject"})),
            (
                "out",
                frozenset(
                    {
                        "hasComponentType",
                        "hasMaterial",
                        "hasDesignQuantity",
                        "manufacturedBy",
                    }
                ),
            ),
        ),
        "ComponentType": (("in", frozenset({"hasComponentType"})),),
        "IfcMaterial": (("in", frozenset({"hasMaterial"})),),
        "DesignQuantity": (("in", frozenset({"hasDesignQuantity"})),),
        "ManufacturingProcessTemplate": (
            ("in", frozenset({"hasProcessTemplate"})),
            ("out", frozenset({"hasStage"})),
        ),
        "ProductionStage": (
            # The schema lets an energy record associate with a stage directly
            # instead of with one of its activities, so that association owns
            # the stage just as `associatedWithProcess` owns an activity below.
            ("in", frozenset({"hasStage", "associatedWithProcess"})),
            ("out", frozenset({"hasActivity"})),
        ),
        "ManufacturingActivity": (
            (
                "in",
                frozenset(
                    {
                        "hasActivity",
                        "manufacturedBy",
                        "associatedWithProcess",
                        "directlyPrecedes",
                    }
                ),
            ),
            ("out", frozenset({"usesResource", "directlyPrecedes"})),
        ),
        "ManufacturingResource": (
            ("in", frozenset({"usesResource", "recordedForResource"})),
        ),
        "MaterialConsumption": (("out", frozenset({"hasQuantity"})),),
        "EnergyConsumption": (("out", frozenset({"hasQuantity"})),),
        "ConsumptionQuantity": (("in", frozenset({"hasQuantity"})),),
        "EmissionFactor": (("in", frozenset({"hasFactor"})),),
        "EnergyCarrier": (("in", frozenset({"ofCarrier"})),),
        "CarbonEmission": (("out", frozenset({"hasCarbonDriver"})),),
    }
    for node_id, node in nodes.items():
        labels = set(node.get("labels", ()))
        excluded = sorted(labels & EXCLUDED_FACTORY_BACKBONE_TYPES)
        if excluded:
            _finding(
                findings,
                "excluded_ifc_refinement",
                entity_id=node_id,
                message="excluded factory-service IFC type entered the product backbone",
                evidence={"labels": excluded},
            )
        node_class = _node_class(node)
        if context.get("factoryInputPresent") is False and node_class in _FACTORY_INPUT_CLASSES:
            _finding(
                findings,
                "factory_input_fact_forbidden",
                entity_id=node_id,
                message=f"{node_class} requires a reviewed factory input, but factoryInput is null",
            )
        requirements = orphan_requirements.get(node_class, ())
        if requirements:
            connected = False
            for direction, relations in requirements:
                candidates = (
                    outgoing.get(node_id, ())
                    if direction == "out"
                    else incoming.get(node_id, ())
                )
                if any(str(edge.get("type", "")) in relations for edge in candidates):
                    connected = True
                    break
            if not connected:
                _finding(
                    findings,
                    "orphan_canonical_node",
                    entity_id=node_id,
                    message=f"{node_class} is disconnected from its required canonical ownership path",
                )

    module_id = str(context.get("moduleId", ""))
    module = nodes.get(module_id)
    expected_source = str(context.get("moduleSourceIdentity", ""))
    expected_name = str(context.get("moduleName", ""))
    if module is not None and expected_source and expected_name:
        props = module.get("props", {})
        ifc_hash = str(context.get("ifcSha256", ""))
        expected_id = stable_id("ModularUnit", ifc_hash, expected_source)
        if (
            not isinstance(props, Mapping)
            or module_id != expected_id
            or str(props.get("sourceIdentity", "")) != expected_source
            or str(props.get("name", "")) != expected_name
            or str(props.get("ifcSha256", "")) != ifc_hash
        ):
            _finding(
                findings,
                "module_identity_mismatch",
                entity_id=module_id,
                message="ModularUnit identity/name/source do not match configured provenance",
            )
def _check_provenance(
    nodes: Mapping[str, Mapping[str, Any]], findings: list[dict[str, Any]]
) -> None:
    for node_id, node in nodes.items():
        props = node.get("props", {})
        if not isinstance(props, Mapping):
            continue
        for field in ("orderedRawOperands", "conversionSteps"):
            if field not in props:
                continue
            values = props[field]
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                _finding(
                    findings,
                    "provenance_json_invalid",
                    entity_id=node_id,
                    message=f"{field} must be an ordered list of canonical JSON strings",
                )
                continue
            for index, text in enumerate(values):
                try:
                    parsed = _strict_json_object(text)
                    if _canonical_json(parsed) != text:
                        raise ValueError("provenance JSON is not canonical")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    _finding(
                        findings,
                        "provenance_json_invalid",
                        entity_id=node_id,
                        message=f"invalid {field}[{index}]: {exc}",
                    )


def _check_factor_source_rows(
    nodes: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    source_rows = context["factorSourceRows"]
    for node_id, node in nodes.items():
        if _node_class(node) != "EmissionFactor":
            continue
        props = node.get("props", {})
        if not isinstance(props, Mapping):
            continue
        source_id = str(props.get("sourceRowId", ""))
        expected = source_rows.get(source_id)
        mismatches = []
        if not isinstance(expected, Mapping):
            mismatches.append("sourceRowId")
        else:
            mismatches.extend(
                field
                for field in set(props) | set(expected)
                if props.get(field) != expected.get(field)
            )
            if node_id != expected.get("factorId"):
                mismatches.append("factorId")
        if mismatches:
            _finding(
                findings,
                "factor_workbook_mismatch",
                entity_id=node_id,
                message="EmissionFactor differs from its exact bound workbook source row",
                evidence={"sourceRowId": source_id, "fields": sorted(mismatches)},
            )


def _factor_source_rows_from_library(library: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for factor_kind, raw_rows in (
        ("material", getattr(library, "material_factors", ())),
        ("energy", getattr(library, "energy_factors", ())),
    ):
        for raw in raw_rows:
            try:
                record = library._strict_record(raw, factor_kind)
            except Exception:
                continue
            factor_node_id = stable_id("EmissionFactor", record.source_row_id)
            rows[record.source_row_id] = clean_neo4j_properties(
                {
                    "factorId": factor_node_id,
                    "factorSourceId": record.source_row_id,
                    "keyword": record.keyword,
                    "factorValue": record.normalized_factor_value,
                    "factorUnit": record.factor_unit,
                    "factorDenominator": record.normalized_denominator,
                    "originalFactorValue": record.factor_value,
                    "originalFactorUnit": record.original_factor_unit,
                    "source": record.source,
                    "sourceRowId": record.source_row_id,
                    "matchStatus": record.match_status,
                    "confidence": record.confidence,
                    "denominatorUnit": record.denominator_unit,
                    "normalizedFactorValue": record.normalized_factor_value,
                    "normalizedDenominator": record.normalized_denominator,
                    "geography": record.geography,
                    "year": record.year,
                    "systemBoundary": record.system_boundary,
                    "sourceReference": record.source_reference,
                    "proxyStatus": record.proxy_status,
                    "validationStatus": record.validation_status,
                },
                path=f"factorSourceRows[{record.source_row_id}]",
            )
    return rows


def _check_validation_ledger(
    rows: Sequence[Mapping[str, Any]],
    nodes: Mapping[str, Mapping[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    node_ids = set(nodes)
    record_ids = {
        str(node.get("props", {}).get("recordId", ""))
        for node in nodes.values()
        if isinstance(node.get("props", {}), Mapping)
    }
    graph_source_identities = {
        str(node.get("props", {}).get("sourceIdentity", ""))
        for node in nodes.values()
        if _node_class(node) in {"MaterialConsumption", "EnergyConsumption"}
        and isinstance(node.get("props", {}), Mapping)
    }
    graph_source_records = {
        str(node.get("props", {}).get("sourceRecordId", ""))
        for node in nodes.values()
        if _node_class(node) in {"MaterialConsumption", "EnergyConsumption"}
        and isinstance(node.get("props", {}), Mapping)
    }
    accepted_rows: dict[str, Mapping[str, Any]] = {}
    seen_outcomes: set[tuple[str, str]] = set()
    expected_classes = {
        "consumptionId": {"MaterialConsumption", "EnergyConsumption"},
        "quantityId": {"ConsumptionQuantity"},
        "factorId": {"EmissionFactor"},
        "emissionId": {"CarbonEmission"},
    }
    for row in rows:
        if not isinstance(row, Mapping):
            _finding(findings, "validation_ledger_invalid", artifact="validation", message="row is not an object")
            continue
        status = str(row.get("status", ""))
        record_id = str(row.get("recordId", ""))
        evidence_json = str(row.get("evidenceJson", ""))
        parsed_evidence: Mapping[str, Any] | None = None
        try:
            parsed_evidence = _strict_json_object(evidence_json)
            if _canonical_json(parsed_evidence) != evidence_json:
                raise ValueError("evidenceJson is not canonical compact JSON")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            _finding(
                findings,
                "validation_evidence_invalid",
                artifact="validation",
                entity_id=record_id,
                message=f"validation evidenceJson is invalid: {exc}",
            )
        outcome_key = (record_id, status)
        if not record_id or outcome_key in seen_outcomes:
            _finding(
                findings,
                "validation_ledger_invalid",
                artifact="validation",
                entity_id=record_id,
                message="validation record identity is empty or duplicated",
            )
        seen_outcomes.add(outcome_key)
        calculation_ids = [
            str(row.get(field, ""))
            for field in ("consumptionId", "quantityId", "factorId", "emissionId")
            if str(row.get(field, ""))
        ]
        if status == "accepted":
            accepted_rows[record_id] = row
            missing = [value for value in calculation_ids if value not in node_ids]
            wrong_classes = [
                field
                for field, classes in expected_classes.items()
                if str(row.get(field, "")) in nodes
                and _node_class(nodes[str(row.get(field, ""))]) not in classes
            ]
            identity_mismatches = [
                field
                for field in ("consumptionId", "quantityId", "emissionId")
                if str(row.get(field, "")) in nodes
                and str(
                    nodes[str(row.get(field, ""))]
                    .get("props", {})
                    .get("recordId", "")
                )
                != record_id
            ]
            if (
                len(calculation_ids) != 4
                or missing
                or wrong_classes
                or identity_mismatches
                or str(row.get("reasonCode", ""))
            ):
                _finding(
                    findings,
                    "accepted_identity_missing",
                    artifact="validation",
                    entity_id=record_id,
                    message="accepted validation row does not reconcile to graph identities",
                    evidence={
                        "missing": missing,
                        "wrongClasses": wrong_classes,
                        "identityMismatches": identity_mismatches,
                    },
                )
            consumption_id = str(row.get("consumptionId", ""))
            quantity_id = str(row.get("quantityId", ""))
            emission_id = str(row.get("emissionId", ""))
            factor_id = str(row.get("factorId", ""))
            semantic_mismatches: list[str] = []
            if all(value in nodes for value in (consumption_id, quantity_id, emission_id)):
                consumption_props = nodes[consumption_id].get("props", {})
                quantity_props = nodes[quantity_id].get("props", {})
                emission_props = nodes[emission_id].get("props", {})
                if all(
                    isinstance(props, Mapping)
                    for props in (consumption_props, quantity_props, emission_props)
                ):
                    for field, graph_field in (
                        ("sourceIdentity", "sourceIdentity"),
                        ("sourceRecordId", "sourceRecordId"),
                        ("evidenceSourceId", "evidenceSourceId"),
                        ("formulaCode", "formulaCode"),
                    ):
                        values = {
                            str(row.get(field, "")),
                            str(consumption_props.get(graph_field, "")),
                            str(quantity_props.get(graph_field, "")),
                            str(emission_props.get(graph_field, "")),
                        }
                        if "" in values or len(values) != 1:
                            semantic_mismatches.append(field)
                    graph_zero = bool(consumption_props.get("isValidZero"))
                    if (
                        str(row.get("isValidZero", "")).casefold()
                        != str(graph_zero).casefold()
                        or bool(quantity_props.get("isValidZero")) != graph_zero
                        or bool(emission_props.get("isValidZero")) != graph_zero
                    ):
                        semantic_mismatches.append("isValidZero")
                else:
                    semantic_mismatches.append("properties")
            factor_props = (
                nodes[factor_id].get("props", {}) if factor_id in nodes else {}
            )
            factor_source_row_id = (
                str(factor_props.get("sourceRowId", ""))
                if isinstance(factor_props, Mapping)
                else ""
            )
            expected_evidence = {
                "factorSourceRowId": factor_source_row_id,
                "formulaCode": str(row.get("formulaCode", "")),
                "sourceRecordId": str(row.get("sourceRecordId", "")),
            }
            evidence_mismatch = (
                not isinstance(parsed_evidence, Mapping)
                or any(
                    str(parsed_evidence.get(key, "")) != expected
                    for key, expected in expected_evidence.items()
                )
            )
            if (
                evidence_mismatch
                or not factor_source_row_id
                or factor_id
                != stable_id("EmissionFactor", factor_source_row_id)
            ):
                _finding(
                    findings,
                    "validation_evidence_invalid",
                    artifact="validation",
                    entity_id=record_id,
                    message="accepted evidenceJson does not match row and graph provenance",
                    evidence={"expected": expected_evidence},
                )
            if semantic_mismatches:
                _finding(
                    findings,
                    "accepted_ledger_mismatch",
                    artifact="validation",
                    entity_id=record_id,
                    message="accepted validation semantics differ from graph calculation provenance",
                    evidence={"fields": sorted(set(semantic_mismatches))},
                )
        elif status == "rejected":
            leaked = (
                record_id in record_ids
                or any(value in node_ids for value in calculation_ids)
                or str(row.get("sourceIdentity", "")) in graph_source_identities
                or str(row.get("sourceRecordId", "")) in graph_source_records
            )
            if leaked or calculation_ids:
                _finding(
                    findings,
                    "rejected_identity_in_graph",
                    artifact="validation",
                    entity_id=record_id,
                    message="rejected candidate leaked into graph calculation facts",
                )
            if not str(row.get("reasonCode", "")):
                _finding(
                    findings,
                    "validation_ledger_invalid",
                    artifact="validation",
                    entity_id=record_id,
                    message="rejected validation row requires a reason code",
                )
            projected = {
                field: str(row.get(field, ""))
                for field in (
                    "sourceIdentity",
                    "sourceRecordId",
                    "evidenceSourceId",
                    "kind",
                    "formulaCode",
                )
            }
            expected_projection = {
                field: str(parsed_evidence.get(field, ""))
                if parsed_evidence is not None
                else ""
                for field in projected
            }
            evidence_source_id = expected_projection["evidenceSourceId"]
            source_record_id = expected_projection["sourceRecordId"]
            expected_source_identity = (
                stable_id("ValidationRecord", evidence_source_id, record_id)
                if str(row.get("reasonCode", "")) == "source_record_id_missing"
                and evidence_source_id
                and not source_record_id
                else stable_id("SourceRecord", evidence_source_id, source_record_id)
                if evidence_source_id and source_record_id
                else expected_projection["sourceIdentity"]
            )
            expected_projection["sourceIdentity"] = expected_source_identity
            missing_source_contract = (
                str(row.get("reasonCode", "")) == "source_record_id_missing"
                and bool(evidence_source_id)
                and not source_record_id
            )
            regular_source_contract = (
                str(row.get("reasonCode", "")) != "source_record_id_missing"
                and bool(evidence_source_id)
                and bool(source_record_id)
            )
            if parsed_evidence is not None and (
                projected != expected_projection
                or str(parsed_evidence.get("sourceIdentity", ""))
                != expected_source_identity
                or not (missing_source_contract or regular_source_contract)
            ):
                _finding(
                    findings,
                    "validation_evidence_invalid",
                    artifact="validation",
                    entity_id=record_id,
                    message="rejected evidenceJson projection does not match ledger fields",
                    evidence={"fields": sorted(projected)},
                )
        else:
            _finding(
                findings,
                "validation_ledger_invalid",
                artifact="validation",
                entity_id=record_id,
                message="validation status must be accepted or rejected",
            )

    for node_id, node in nodes.items():
        if _node_class(node) not in {"MaterialConsumption", "EnergyConsumption"}:
            continue
        props = node.get("props", {})
        record_id = str(props.get("recordId", "")) if isinstance(props, Mapping) else ""
        row = accepted_rows.get(record_id)
        if row is None or str(row.get("consumptionId", "")) != node_id:
            _finding(
                findings,
                "graph_fact_missing_validation",
                artifact="validation",
                entity_id=node_id,
                message="graph consumption has no matching accepted validation row",
                evidence={"recordId": record_id},
            )


def _check_calculation_paths(
    nodes: Mapping[str, Mapping[str, Any]],
    outgoing: Mapping[str, list[Mapping[str, Any]]],
    incoming: Mapping[str, list[Mapping[str, Any]]],
    context: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, float]:
    product_total = 0.0
    source_process_total = 0.0
    requested_scope = str(context.get("requestedScope", ""))

    for emission_id, emission in nodes.items():
        if _node_class(emission) != "CarbonEmission":
            continue
        generated = _relations(outgoing, emission_id, "hasCarbonDriver")
        if len(generated) != 1:
            _finding(
                findings,
                "emission_cardinality_invalid",
                entity_id=emission_id,
                message="CarbonEmission must have exactly one hasCarbonDriver source",
            )

    for consumption_id, consumption in nodes.items():
        kind = _node_class(consumption)
        if kind not in {"MaterialConsumption", "EnergyConsumption"}:
            continue
        quantity_edges = _relations(outgoing, consumption_id, "hasQuantity")
        factor_edges = _relations(outgoing, consumption_id, "hasFactor")
        emissions = _relations(incoming, consumption_id, "hasCarbonDriver")
        required_ok = len(quantity_edges) == len(factor_edges) == len(emissions) == 1
        if not required_ok:
            _finding(
                findings,
                "consumption_cardinality_invalid",
                entity_id=consumption_id,
                message="consumption requires exactly one quantity, factor and incoming emission",
                evidence={
                    "quantity": len(quantity_edges),
                    "factor": len(factor_edges),
                    "emission": len(emissions),
                },
            )

        recorded = _relations(outgoing, consumption_id, "recordedForObject")
        if kind == "MaterialConsumption":
            material_edges = _relations(outgoing, consumption_id, "ofMaterial")
            if len(recorded) != 1 or len(material_edges) != 1:
                _finding(
                    findings,
                    "consumption_cardinality_invalid",
                    entity_id=consumption_id,
                    message="material consumption requires one component and one material",
                )
        else:
            carrier_edges = _relations(outgoing, consumption_id, "ofCarrier")
            if len(carrier_edges) != 1:
                _finding(
                    findings,
                    "consumption_cardinality_invalid",
                    entity_id=consumption_id,
                    message="energy consumption requires exactly one carrier",
                )

        emission_value: float | None = None
        if required_ok:
            quantity = nodes.get(str(quantity_edges[0].get("tgt", "")), {})
            factor = nodes.get(str(factor_edges[0].get("tgt", "")), {})
            emission = nodes.get(str(emissions[0].get("src", "")), {})
            q_props = quantity.get("props", {}) if isinstance(quantity, Mapping) else {}
            f_props = factor.get("props", {}) if isinstance(factor, Mapping) else {}
            e_props = emission.get("props", {}) if isinstance(emission, Mapping) else {}
            c_props = consumption.get("props", {})
            q_value = _number(q_props.get("quantityValue")) if isinstance(q_props, Mapping) else None
            factor_value = (
                _number(
                    f_props.get("normalizedFactorValue", f_props.get("factorValue"))
                )
                if isinstance(f_props, Mapping)
                else None
            )
            emission_value = _number(e_props.get("emissionValue")) if isinstance(e_props, Mapping) else None
            if q_value is None or factor_value is None or emission_value is None:
                _finding(
                    findings,
                    "calculation_value_invalid",
                    entity_id=consumption_id,
                    message="q, EF and emission values must be finite and non-negative",
                )
            elif not math.isclose(
                emission_value, q_value * factor_value, rel_tol=1e-9, abs_tol=1e-9
            ):
                _finding(
                    findings,
                    "emission_formula_mismatch",
                    entity_id=str(emission.get("id", "")),
                    message="emissionValue does not equal quantityValue × factorValue",
                    evidence={"quantity": q_value, "factor": factor_value, "emission": emission_value},
                )
            if isinstance(c_props, Mapping) and isinstance(f_props, Mapping) and isinstance(e_props, Mapping):
                factor_source_id = str(c_props.get("factorSourceId", ""))
                factor_node_id = str(factor.get("id", ""))
                source_values = {
                    factor_source_id,
                    str(f_props.get("factorSourceId", "")),
                    str(f_props.get("sourceRowId", "")),
                    str(e_props.get("factorSourceId", "")),
                }
                expected_factor_id = (
                    stable_id("EmissionFactor", factor_source_id)
                    if factor_source_id
                    else ""
                )
                linked_ids = {
                    factor_node_id,
                    str(c_props.get("factorId", "")),
                    str(e_props.get("factorId", "")),
                }
                if (
                    not factor_source_id
                    or len(source_values) != 1
                    or not expected_factor_id
                    or linked_ids != {expected_factor_id}
                ):
                    _finding(
                        findings,
                        "factor_source_identity_mismatch",
                        entity_id=consumption_id,
                        message="factor node and calculation path do not share one exact source-row identity",
                        evidence={
                            "sourceValues": sorted(source_values),
                            "factorIds": sorted(linked_ids),
                            "expectedFactorId": expected_factor_id,
                        },
                    )
                if (
                    q_value is not None
                    and _number(e_props.get("quantityValue")) != q_value
                ) or (
                    factor_value is not None
                    and _number(e_props.get("factorValue")) != factor_value
                ):
                    _finding(
                        findings,
                        "calculation_value_invalid",
                        entity_id=consumption_id,
                        message="emission provenance does not match linked normalized q/EF values",
                    )
            zero_flags = (
                bool(c_props.get("isValidZero"))
                if isinstance(c_props, Mapping)
                else False,
                bool(q_props.get("isValidZero")),
                bool(e_props.get("isValidZero")),
            )
            is_zero_quantity = q_value == 0.0
            measured_record_ids = context.get("measuredRecordIds")
            record_id = c_props.get("recordId") if isinstance(c_props, Mapping) else None
            source_record_id = (
                c_props.get("sourceRecordId") if isinstance(c_props, Mapping) else None
            )
            evidence_source_id = (
                c_props.get("evidenceSourceId") if isinstance(c_props, Mapping) else None
            )
            zero_evidence_valid = (
                isinstance(measured_record_ids, (set, frozenset))
                and type(record_id) is str
                and record_id in measured_record_ids
                and type(source_record_id) is str
                and bool(source_record_id.strip())
                and type(evidence_source_id) is str
                and bool(evidence_source_id.strip())
            )
            if (
                is_zero_quantity
                and not (
                    q_value == 0.0
                    and emission_value == 0.0
                    and all(zero_flags)
                    and zero_evidence_valid
                )
            ) or (not is_zero_quantity and any(zero_flags)):
                _finding(
                    findings,
                    "valid_zero_inconsistent",
                    entity_id=consumption_id,
                    message="valid-zero flags or measured source evidence do not match the finite q/EF/emission fact",
                )
            normalized_denominator = str(
                f_props.get("normalizedDenominator")
                or f_props.get("factorDenominator")
                or ""
            )
            normalized_factor_unit = (
                f"kgCO2e/{normalized_denominator}"
                if normalized_denominator
                else str(f_props.get("factorUnit", ""))
            )
            calculation_scope = (
                str(c_props.get("systemBoundary", ""))
                if isinstance(c_props, Mapping)
                else ""
            ) or str(e_props.get("systemBoundary", ""))
            factor_scope_for_accounting = (
                calculation_scope
                if kind == "EnergyConsumption" and calculation_scope
                else str(f_props.get("systemBoundary", ""))
            )
            unit_result = validate_quantity_factor(
                str(q_props.get("quantityUnit", "")),
                normalized_factor_unit,
                str(q_props.get("recordedScope", "")),
                factor_scope_for_accounting,
                requested_scope or str(q_props.get("requestedScope", "")),
            )
            if not unit_result.accepted:
                code = (
                    "unit_factor_incompatible"
                    if "unit" in unit_result.reason_code or "denominator" in unit_result.reason_code
                    else "scope_incompatible"
                )
                _finding(
                    findings,
                    code,
                    entity_id=consumption_id,
                    message=f"quantity/factor compatibility failed: {unit_result.reason_code}",
                )
            scopes = {
                _scope_key(value)
                for value in (
                    q_props.get("recordedScope", ""),
                    q_props.get("requestedScope", ""),
                    f_props.get("systemBoundary", "")
                    if kind == "MaterialConsumption"
                    else "",
                    e_props.get("recordedScope", ""),
                    e_props.get("requestedScope", ""),
                    e_props.get("systemBoundary", ""),
                    c_props.get("recordedScope", "")
                    if isinstance(c_props, Mapping)
                    else "",
                    c_props.get("requestedScope", "")
                    if isinstance(c_props, Mapping)
                    else "",
                    c_props.get("systemBoundary", "")
                    if isinstance(c_props, Mapping)
                    else "",
                    factor_scope_for_accounting,
                    requested_scope,
                )
                if _scope_key(value)
            }
            if len(scopes) > 1:
                _finding(
                    findings,
                    "scope_incompatible",
                    entity_id=consumption_id,
                    message="calculation path contains incompatible scopes",
                    evidence={"scopes": sorted(scopes)},
                )

        if kind == "MaterialConsumption":
            material_edges = _relations(outgoing, consumption_id, "ofMaterial")
            c_props = consumption.get("props", {})
            if isinstance(c_props, Mapping) and (
                len(recorded) != 1
                or str(c_props.get("productTargetId", ""))
                != str(recorded[0].get("tgt", ""))
                or len(material_edges) != 1
                or str(c_props.get("materialId", ""))
                != str(material_edges[0].get("tgt", ""))
            ):
                _finding(
                    findings,
                    "product_target_mismatch",
                    entity_id=consumption_id,
                    message="material target properties differ from canonical ownership edges",
                )
            if emission_value is not None and len(recorded) == 1:
                product_total += emission_value
            continue

        consumption_props = consumption.get("props", {})
        mode = str(
            consumption_props.get("attributionMode", "")
            if isinstance(consumption_props, Mapping)
            else ""
        )
        if emission_value is not None:
            source_process_total += emission_value
        if mode == "direct":
            if len(recorded) != 1:
                _finding(findings, "energy_attribution_invalid", entity_id=consumption_id, message="direct energy requires one product path")
            for edge in recorded:
                props = edge.get("props", {}) if isinstance(edge.get("props", {}), Mapping) else {}
                if _ALLOCATION_FIELDS & set(props):
                    _finding(findings, "mixed_energy_attribution", entity_id=consumption_id, message="direct energy contains allocation fields")
            if emission_value is not None and len(recorded) == 1:
                product_total += emission_value
        elif mode == "allocated":
            allocation_keys: set[tuple[str, str]] = set()
            weight_sum = 0.0
            unattributed_fraction = _number(consumption_props.get("unattributedFraction"))
            has_allocated = False
            has_direct = False
            declared_set = str(
                consumption_props.get("allocationSetId", "")
                if isinstance(consumption_props, Mapping)
                else ""
            )
            declared_basis = str(
                consumption_props.get("allocationBasis", "")
                if isinstance(consumption_props, Mapping)
                else ""
            )
            for edge in recorded:
                props = edge.get("props", {}) if isinstance(edge.get("props", {}), Mapping) else {}
                if set(props) == _ALLOCATION_FIELDS and props.get("allocated") is True:
                    has_allocated = True
                    allocation_set = str(props.get("allocationSetId", ""))
                    allocation_basis = str(props.get("allocationBasis", ""))
                    key = (allocation_set, str(edge.get("tgt", "")))
                    if key in allocation_keys:
                        _finding(findings, "duplicate_allocation_key", entity_id=consumption_id, message="allocated semantic target occurs more than once", evidence={"key": list(key)})
                    allocation_keys.add(key)
                    raw_weight = _number(props.get("rawWeight"))
                    weight = _number(props.get("allocatedFraction"))
                    if (
                        raw_weight is None
                        or weight is None
                        or weight > 1
                        or not allocation_set
                        or not allocation_basis
                        or not str(props.get("rawWeightUnit", "")).strip()
                        or not str(props.get("evidenceRecordId", "")).strip()
                        or allocation_set != declared_set
                        or allocation_basis != declared_basis
                    ):
                        _finding(findings, "allocation_edge_invalid", entity_id=str(edge.get("id", "")), message="allocated fraction is invalid")
                    if weight is not None and weight <= 1:
                        weight_sum += weight
                        if emission_value is not None:
                            product_total += emission_value * weight
                else:
                    has_direct = True
                    _finding(findings, "allocation_edge_invalid", entity_id=str(edge.get("id", "")), message="allocated edge does not have the exact seven-field contract")
            if has_allocated and has_direct:
                _finding(findings, "mixed_energy_attribution", entity_id=consumption_id, message="energy source mixes direct and allocated product paths")
            if (
                not recorded
                or unattributed_fraction is None
                or not 0.0 <= unattributed_fraction <= 1.0
                or not math.isclose(
                    weight_sum + unattributed_fraction,
                    1.0,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                _finding(findings, "allocation_not_conserved", entity_id=consumption_id, message="allocated and unattributed fractions do not sum to one", evidence={"allocatedSum": weight_sum, "unattributedFraction": unattributed_fraction})
        elif mode == "process_only":
            if recorded:
                _finding(findings, "process_only_product_path", entity_id=consumption_id, message="process-only energy has a product path")
            contexts = _relations(outgoing, consumption_id, "associatedWithProcess") + _relations(outgoing, consumption_id, "recordedForResource")
            if not contexts:
                _finding(findings, "process_only_context_missing", entity_id=consumption_id, message="process-only energy lacks process/resource context")
        else:
            _finding(findings, "energy_attribution_invalid", entity_id=consumption_id, message="energy attribution mode is missing or unsupported")

    return {"product": product_total, "sourceProcess": source_process_total}


def _check_cypher(
    graph: Mapping[str, Any], context: Mapping[str, Any], findings: list[dict[str, Any]]
) -> str:
    cypher = context.get("cypherText")
    if not isinstance(cypher, str) or not cypher:
        _finding(findings, "cypher_missing", artifact="cypher", message="structural Cypher text is missing")
        return "fail"
    if re.search(r"\bDETACH\s+DELETE\b", cypher, flags=re.IGNORECASE):
        _finding(
            findings,
            "cypher_wipe_forbidden",
            artifact="cypher",
            message="Cypher must not contain a destructive wipe directive",
        )
    for edge in graph.get("edges", ()) if isinstance(graph.get("edges", ()), list) else ():
        if not isinstance(edge, Mapping):
            continue
        occurrence = str(edge.get("occurrenceId", ""))
        if occurrence and occurrence not in cypher:
            _finding(
                findings,
                "cypher_occurrence_missing",
                artifact="cypher",
                entity_id=occurrence,
                message="Cypher does not preserve relationship occurrence id",
            )
    try:
        expected = _canonical_graph_from_payload(graph).to_cypher()
    except (TypeError, ValueError) as exc:
        _finding(
            findings,
            "cypher_regeneration_failed",
            artifact="cypher",
            message=f"canonical Cypher could not be regenerated: {exc}",
        )
    else:
        if cypher != expected:
            _finding(
                findings,
                "cypher_bytes_mismatch",
                artifact="cypher",
                message="Cypher bytes differ from deterministic canonical regeneration",
            )
    return "fail" if any(row["artifact"] == "cypher" for row in findings) else "pass"


def audit_graph_payload(
    graph: Mapping[str, Any],
    validation_rows: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit one canonical graph payload without mutating any input."""

    findings: list[dict[str, Any]] = []
    if not isinstance(context, Mapping):
        _finding(
            findings,
            "audit_context_invalid",
            message="independent audit context must be an object",
        )
        context = {}
    if not isinstance(graph, Mapping):
        _finding(findings, "graph_shape_invalid", message="graph payload must be an object")
        graph = {}
    elif set(graph) != _GRAPH_KEYS:
        _finding(
            findings,
            "graph_shape_invalid",
            message="graph must contain exactly schemaVersion/nodes/edges",
            evidence={
                "missing": sorted(_GRAPH_KEYS - set(graph)),
                "unknown": sorted(set(graph) - _GRAPH_KEYS),
            },
        )
    if graph.get("schemaVersion") != SCHEMA_VERSION or context.get("schemaVersion") != SCHEMA_VERSION:
        _finding(findings, "schema_version_invalid", message="graph/context schemaVersion is not canonical v2")
    _check_graph_order(graph, findings)
    nodes, edges, outgoing, incoming = _graph_indices(graph, findings)
    invalid_context = _check_audit_context(nodes, validation_rows, context, findings)
    if not invalid_context.intersection(
        {
            "extraction",
            "ifcSha256",
            "moduleId",
            "moduleSourceIdentity",
            "moduleName",
            "oneModuleCase",
        }
    ):
        _check_ifc_backbone(nodes, edges, incoming, context, findings)
    _check_runtime_payload_and_orphans(
        nodes, outgoing, incoming, context, findings
    )
    _check_provenance(nodes, findings)
    if "factorSourceRows" not in invalid_context:
        _check_factor_source_rows(nodes, context, findings)
    _check_validation_ledger(validation_rows, nodes, findings)
    totals = _check_calculation_paths(nodes, outgoing, incoming, context, findings)
    structural_cypher = _check_cypher(graph, context, findings)
    live_status = "not_configured" if not context.get("liveCypherConfig") else "not_run"
    if live_status == "not_run":
        _finding(findings, "live_cypher_not_run", artifact="cypher", message="live Cypher configuration was supplied but no live gate ran")
    ordered = _sorted_findings(findings)
    high = sum(row["severity"] == "high" for row in ordered)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "pass" if high == 0 else "fail",
        "highSeverityViolationCount": high,
        "findings": ordered,
        "gates": {
            "alignment": "pass" if high == 0 else "fail",
            "structuralCypher": structural_cypher,
            "liveCypherRoundTrip": live_status,
        },
        "projectionTotals": totals,
        "checkedCounts": {
            "nodes": len(graph.get("nodes", ())) if isinstance(graph.get("nodes", ()), list) else 0,
            "edges": len(graph.get("edges", ())) if isinstance(graph.get("edges", ()), list) else 0,
            "validationRows": len(validation_rows),
        },
    }


def _sha_size(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return sha256(data).hexdigest().upper(), len(data)


def _safe_relative_path(value: Any, expected: str) -> bool:
    if not isinstance(value, str) or value != expected or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and len(path.parts) == 1


def _read_json(path: Path) -> Any:
    return _strict_json_object(path.read_text(encoding="utf-8"))


def _validation_counts_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    accepted = [row for row in rows if row.get("status") == "accepted"]
    rejected = [row for row in rows if row.get("status") == "rejected"]
    accepted_by_kind = Counter(str(row.get("kind", "")) for row in accepted)
    rejected_by_kind = Counter(str(row.get("kind", "")) for row in rejected)
    rejected_by_reason = Counter(str(row.get("reasonCode", "")) for row in rejected)
    candidate_count = len(accepted) + len(rejected)
    return {
        "candidateCount": candidate_count,
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "acceptedByKind": dict(sorted(accepted_by_kind.items())),
        "rejectedByKind": dict(
            sorted((key, value) for key, value in rejected_by_kind.items() if key)
        ),
        "rejectedByReason": dict(sorted(rejected_by_reason.items())),
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": len(accepted) / candidate_count if candidate_count else 0.0,
    }


def _read_validation_csv(
    path: Path, findings: list[dict[str, Any]]
) -> tuple[dict[str, str], ...]:
    problems: list[str] = []
    payload = path.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        problems.append("utf8_bom")
    if b"\r" in payload:
        problems.append("non_lf_line_ending")
    try:
        text = payload.decode("utf-8")
        raw_rows = list(csv.reader(io.StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        _finding(
            findings,
            "validation_csv_invalid",
            artifact="validation",
            message=f"validation CSV cannot be parsed strictly: {exc}",
        )
        return ()
    if not raw_rows or tuple(raw_rows[0]) != _VALIDATION_FIELDS:
        problems.append("header")
        data_rows: list[list[str]] = []
    else:
        data_rows = raw_rows[1:]
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(data_rows, start=2):
        if len(raw) != len(_VALIDATION_FIELDS):
            problems.append(f"column_count:{index}")
            continue
        row = dict(zip(_VALIDATION_FIELDS, raw))
        rows.append(row)
        status = row["status"]
        if status not in {"accepted", "rejected"}:
            problems.append(f"status:{index}")
        if status == "accepted" and (
            row["reasonCode"]
            or row["message"]
            or not all(
                row[field]
                for field in ("consumptionId", "quantityId", "factorId", "emissionId")
            )
            or row["isValidZero"] not in {"true", "false"}
        ):
            problems.append(f"accepted_shape:{index}")
        if status == "rejected" and (
            not row["reasonCode"]
            or any(
                row[field]
                for field in (
                    "consumptionId",
                    "quantityId",
                    "factorId",
                    "emissionId",
                    "isValidZero",
                )
            )
        ):
            problems.append(f"rejected_shape:{index}")
        try:
            evidence = _strict_json_object(row["evidenceJson"])
            if _canonical_json(evidence) != row["evidenceJson"]:
                raise ValueError("evidenceJson is not canonical compact JSON")
        except (TypeError, ValueError, json.JSONDecodeError):
            problems.append(f"evidence_json:{index}")
    expected_order = sorted(
        rows,
        key=lambda row: (
            row["recordId"],
            0 if row["status"] == "accepted" else 1,
            row["reasonCode"],
        ),
    )
    if rows != expected_order:
        problems.append("row_order")
    if problems:
        _finding(
            findings,
            "validation_csv_invalid",
            artifact="validation",
            message="validation CSV differs from the exact canonical ledger contract",
            evidence={"problems": sorted(set(problems))},
        )
    return tuple(rows)


def _validation_csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        handle, fieldnames=_VALIDATION_FIELDS, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8")


def _check_stats_against_graph(
    stats: Any,
    graph: Mapping[str, Any],
    validation_rows: Sequence[Mapping[str, Any]],
    module_id: str,
    findings: list[dict[str, Any]],
) -> None:
    if not isinstance(stats, Mapping):
        _finding(
            findings,
            "stats_graph_mismatch",
            artifact="stats",
            message="stats artifact must be an object",
        )
        return
    nodes = graph.get("nodes", ())
    edges = graph.get("edges", ())
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return
    labels = Counter(
        label
        for node in nodes
        if isinstance(node, Mapping)
        for label in node.get("labels", ())
        if isinstance(label, str)
    )
    relations = Counter(
        str(edge.get("type", "")) for edge in edges if isinstance(edge, Mapping)
    )
    expected_classes = {label: labels.get(label, 0) for label in APPLICATION_CLASSES}
    expected_relations = {
        relation: relations.get(relation, 0)
        for relation in (*PRINCIPAL_PREDICATES, *sorted(OPTIONAL_CONTEXT_PREDICATES))
    }
    expected = {
        "schemaVersion": SCHEMA_VERSION,
        "moduleId": module_id,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "applicationClassCounts": expected_classes,
        "ifcRefinementCounts": dict(
            sorted(
                (label, count)
                for label, count in labels.items()
                if label not in APPLICATION_CLASSES and label.startswith("Ifc")
            )
        ),
        "relationCounts": expected_relations,
        "calculationCounts": {
            "material": labels.get("MaterialConsumption", 0),
            "energy": labels.get("EnergyConsumption", 0),
            "validZero": sum(
                bool(node.get("props", {}).get("isValidZero"))
                for node in nodes
                if isinstance(node, Mapping)
                and "CarbonEmission" in node.get("labels", ())
                and isinstance(node.get("props", {}), Mapping)
            ),
        },
        "attributionCounts": {
            mode: sum(
                1
                for node in nodes
                if isinstance(node, Mapping)
                and "EnergyConsumption" in node.get("labels", ())
                and isinstance(node.get("props", {}), Mapping)
                and str(node.get("props", {}).get("attributionMode", "")) == mode
            )
            for mode in ("direct", "allocated", "process_only")
        },
        "validation": _validation_counts_from_rows(validation_rows),
    }
    if dict(stats) != expected:
        mismatches = sorted(
            key for key in set(stats) | set(expected) if stats.get(key) != expected.get(key)
        )
        _finding(
            findings,
            "stats_graph_mismatch",
            artifact="stats",
            message="stats do not reconcile to the serialized graph and validation ledger",
            evidence={"fields": mismatches},
        )


def _check_code_descriptors(
    code: Any, findings: list[dict[str, Any]]
) -> None:
    if not isinstance(code, Mapping) or set(code) != _CODE_FILES:
        _finding(
            findings,
            "code_descriptor_invalid",
            artifact="manifest",
            message="manifest code descriptors are incomplete or unexpected",
        )
        return
    source_root = Path(__file__).resolve().parent
    for key in sorted(_CODE_FILES):
        descriptor = code.get(key)
        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "path",
            "sha256",
            "sizeBytes",
        }:
            _finding(
                findings,
                "code_descriptor_invalid",
                artifact="manifest",
                entity_id=key,
                message="code descriptor has the wrong fields",
            )
            continue
        relative = descriptor.get("path")
        if not _safe_relative_path(relative, key):
            _finding(
                findings,
                "code_descriptor_invalid",
                artifact="manifest",
                entity_id=key,
                message="code descriptor path is unsafe or unexpected",
            )
            continue
        source = source_root / key
        if not source.is_file():
            _finding(
                findings,
                "code_missing",
                artifact="manifest",
                entity_id=key,
                message="bound implementation file is missing",
            )
            continue
        digest, size = _sha_size(source)
        if descriptor.get("sha256") != digest or descriptor.get("sizeBytes") != size:
            _finding(
                findings,
                "code_hash_mismatch",
                artifact="manifest",
                entity_id=key,
                message="implementation hash or size differs from manifest",
                evidence={"actualSha256": digest, "actualSizeBytes": size},
            )


def _check_manifest_against_case_config(
    manifest: Mapping[str, Any],
    release_root: Path,
    verified_paths: Mapping[str, Path],
    findings: list[dict[str, Any]],
) -> Any:
    if set(manifest) != _MANIFEST_KEYS:
        _finding(
            findings,
            "manifest_schema_invalid",
            artifact="manifest",
            message="manifest contains missing/unknown fields or a forbidden self-hash",
            evidence={
                "missing": sorted(_MANIFEST_KEYS - set(manifest)),
                "unknown": sorted(set(manifest) - _MANIFEST_KEYS),
            },
        )
    config_path = verified_paths.get("caseConfig")
    if config_path is None:
        return None
    try:
        from dm2c_m23_canonical_release import load_case_config

        config = load_case_config(config_path)
    except Exception as exc:
        _finding(
            findings,
            "manifest_config_mismatch",
            artifact="manifest",
            entity_id="caseConfig",
            message=f"bound case config cannot be loaded strictly: {exc}",
        )
        return None

    def binding_descriptor(binding: Any) -> dict[str, Any] | None:
        if binding is None:
            return None
        return {
            "selectedPath": binding.selected_path,
            "resolvedPath": str(binding.resolved_path),
            "sha256": binding.sha256,
            "sizeBytes": binding.size_bytes,
        }

    config_sha256, config_size = _sha_size(config.config_path)
    expected_inputs = {
        "caseConfig": {
            "selectedPath": str(config.config_path),
            "resolvedPath": str(config.config_path),
            "sha256": config_sha256,
            "sizeBytes": config_size,
        },
        "ifc": binding_descriptor(config.ifc),
        "factorWorkbook": binding_descriptor(config.factor_workbook),
        "ontology": binding_descriptor(config.ontology),
        "materialEvidence": binding_descriptor(config.material_evidence),
        "factoryInput": binding_descriptor(config.factory_input),
        "factoryTargetMap": binding_descriptor(config.factory_target_map),
    }
    expected_module_id = stable_id(
        "ModularUnit", config.ifc.sha256, config.module.source_identity
    )
    expected_module = {
        "sourceIdentity": config.module.source_identity,
        "runtimeId": expected_module_id,
        "name": config.module.name,
        "identitySource": config.module.identity_source,
    }
    expected_configuration = {
        "requestedScope": config.requested_scope,
        "includeOpenings": config.include_openings,
        "factoryInput": binding_descriptor(config.factory_input),
        "factoryTargetMap": binding_descriptor(config.factory_target_map),
    }
    release_id = str(manifest.get("releaseId", ""))
    expected_command = {
        "entryPoint": "dm2c_m23_canonical_release.py",
        "caseConfig": str(config.config_path),
        "releaseId": release_id,
    }
    mismatches: list[str] = []
    for field, expected in (
        ("schemaVersion", config.schema_version),
        ("releaseProfile", config.release_profile),
        ("releaseReady", config.release_ready),
        ("generatedAtUtc", config.generated_at_utc),
        ("inputs", expected_inputs),
        ("module", expected_module),
        ("configuration", expected_configuration),
        ("command", expected_command),
    ):
        if manifest.get(field) != expected:
            mismatches.append(field)
    if mismatches:
        _finding(
            findings,
            "manifest_config_mismatch",
            artifact="manifest",
            message="manifest provenance differs from the hashed case config",
            evidence={"fields": sorted(mismatches)},
        )

    safe_release_id = bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release_id)
        and release_id not in {".", ".."}
        and not release_id.endswith(".")
        and ":" not in release_id
        and release_id.split(".", 1)[0].upper() not in _WINDOWS_RESERVED
    )
    root_matches = release_root.name == release_id or release_root.name.startswith(
        f".{release_id}.staging-"
    )
    command = manifest.get("command", {})
    if (
        not safe_release_id
        or not root_matches
        or not isinstance(command, Mapping)
        or command.get("releaseId") != release_id
    ):
        _finding(
            findings,
            "manifest_release_id_mismatch",
            artifact="manifest",
            entity_id=release_id,
            message="release id, directory, and command are not one safe identity",
        )

    inputs = manifest.get("inputs", {})
    if manifest.get("releaseProfile") == "actual-case" and (
        manifest.get("releaseReady") is not False
        or not isinstance(inputs, Mapping)
        or inputs.get("materialEvidence") is not None
    ):
        _finding(
            findings,
            "actual_profile_invalid",
            artifact="manifest",
            message="actual-case must remain not ready with null material evidence",
        )
    return config


def audit_release(
    release_dir: Path | str, *, live_cypher_config: Any = None
) -> dict[str, Any]:
    """Audit the six-file release envelope and its canonical graph."""

    root = Path(release_dir)
    findings: list[dict[str, Any]] = []
    if not root.is_dir():
        raise FileNotFoundError(f"release directory does not exist: {root}")
    entries = list(root.iterdir())
    present = {path.name for path in entries}
    unsafe_entries = sorted(
        path.name for path in entries if not path.is_file() or path.is_symlink()
    )
    if present != _EXACT_RELEASE_FILES or unsafe_entries:
        _finding(
            findings,
            "release_artifact_set_invalid",
            artifact="release",
            message="release does not contain exactly the six canonical artifacts",
            evidence={
                "missing": sorted(_EXACT_RELEASE_FILES - present),
                "unexpected": sorted(present - _EXACT_RELEASE_FILES),
                "unsafeEntries": unsafe_entries,
            },
        )
    manifest_path = root / "case_version_manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be an object")
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        _finding(findings, "schema_version_invalid", artifact="manifest", message="manifest schemaVersion is not canonical v2")
    outputs = manifest.get("outputs", {})
    if not isinstance(outputs, Mapping) or set(outputs) != set(_OUTPUTS):
        _finding(findings, "output_descriptor_invalid", artifact="manifest", message="manifest output descriptors are incomplete")
        outputs = outputs if isinstance(outputs, Mapping) else {}
    for key, filename in _OUTPUTS.items():
        descriptor = outputs.get(key, {})
        if not isinstance(descriptor, Mapping):
            _finding(findings, "output_descriptor_invalid", artifact="manifest", entity_id=key, message="output descriptor is not an object")
            continue
        if set(descriptor) != {"path", "sha256", "sizeBytes"}:
            _finding(
                findings,
                "output_descriptor_invalid",
                artifact="manifest",
                entity_id=key,
                message="output descriptor must contain exactly path/sha256/sizeBytes",
            )
        if not _safe_relative_path(descriptor.get("path"), filename):
            _finding(findings, "unsafe_output_path", artifact="manifest", entity_id=key, message="output path is not the exact safe release-relative filename")
        path = root / filename
        if not path.is_file():
            continue
        digest, size = _sha_size(path)
        if descriptor.get("sha256") != digest or descriptor.get("sizeBytes") != size:
            _finding(
                findings,
                "output_hash_mismatch",
                artifact=filename,
                entity_id=key,
                message="output hash or byte size differs from manifest",
                evidence={"actualSha256": digest, "actualSizeBytes": size},
            )

    inputs = manifest.get("inputs", {})
    verified_paths: dict[str, Path] = {}
    expected_input_keys = {
        "caseConfig",
        "ifc",
        "factorWorkbook",
        "ontology",
        "materialEvidence",
        "factoryInput",
        "factoryTargetMap",
    }
    if isinstance(inputs, Mapping):
        if set(inputs) != expected_input_keys:
            _finding(
                findings,
                "input_descriptor_invalid",
                artifact="manifest",
                message="manifest input descriptors are incomplete or unexpected",
            )
        for key, descriptor in inputs.items():
            if descriptor is None:
                continue
            if not isinstance(descriptor, Mapping) or set(descriptor) != {
                "selectedPath",
                "resolvedPath",
                "sha256",
                "sizeBytes",
            }:
                _finding(
                    findings,
                    "input_descriptor_invalid",
                    artifact="manifest",
                    entity_id=key,
                    message="input descriptor is malformed",
                )
                continue
            raw_path = descriptor.get("resolvedPath") or descriptor.get("selectedPath")
            try:
                source = Path(str(raw_path))
                if not source.is_file():
                    _finding(
                        findings,
                        "input_missing",
                        artifact="manifest",
                        entity_id=key,
                        message="bound input file is missing",
                    )
                    continue
                digest, size = _sha_size(source)
                if descriptor.get("sha256") != digest or descriptor.get("sizeBytes") != size:
                    _finding(findings, "input_hash_mismatch", artifact="manifest", entity_id=key, message="input hash or size differs from bound file")
                else:
                    verified_paths[str(key)] = source
            except (OSError, ValueError):
                _finding(findings, "input_path_invalid", artifact="manifest", entity_id=key, message="input path cannot be resolved")
    else:
        _finding(findings, "input_descriptor_invalid", artifact="manifest", message="manifest inputs must be an object")

    case_config = _check_manifest_against_case_config(
        manifest, root, verified_paths, findings
    )
    _check_code_descriptors(manifest.get("code"), findings)

    graph = _read_json(root / _OUTPUTS["graphJson"])
    if not isinstance(graph, Mapping):
        raise ValueError("graph artifact must be an object")
    stats = _read_json(root / _OUTPUTS["stats"])
    stored_alignment = _read_json(root / _OUTPUTS["alignmentReport"])
    if manifest.get("counts") != stats:
        _finding(
            findings,
            "manifest_counts_mismatch",
            artifact="manifest",
            message="manifest counts do not equal the bound stats artifact",
        )
    rows = _read_validation_csv(root / _OUTPUTS["validation"], findings)
    cypher = (root / _OUTPUTS["cypher"]).read_text(encoding="utf-8")
    extraction = None
    verified_ifc_path = verified_paths.get("ifc")
    if verified_ifc_path is not None:
        try:
            from dm2c_m23_ifc import CanonicalIFCExtractor

            configuration = manifest.get("configuration", {})
            include_openings = (
                case_config.include_openings
                if case_config is not None
                else (
                    bool(configuration.get("includeOpenings", False))
                    if isinstance(configuration, Mapping)
                    else False
                )
            )
            extraction = CanonicalIFCExtractor(
                verified_ifc_path, include_openings=include_openings
            ).extract()
        except Exception as exc:
            _finding(
                findings,
                "ifc_extraction_failed",
                artifact="manifest",
                entity_id="ifc",
                message=f"bound IFC could not be independently extracted: {exc}",
            )
    factor_source_rows: Mapping[str, Mapping[str, Any]] | None = None
    factor_library: Any = None
    expected_rows: tuple[dict[str, Any], ...] | None = None
    measured_record_ids: frozenset[str] | None = None
    factor_workbook_path = verified_paths.get("factorWorkbook")
    if factor_workbook_path is not None:
        try:
            from dm2c_multigranular_carbon_kg import FactorLibrary

            factor_library = FactorLibrary(factor_workbook_path)
            if factor_library.load_error:
                raise ValueError(factor_library.load_error)
            factor_source_rows = _factor_source_rows_from_library(factor_library)
        except Exception as exc:
            _finding(
                findings,
                "factor_workbook_unreadable",
                artifact="manifest",
                entity_id="factorWorkbook",
                message=f"bound factor workbook could not be loaded strictly: {exc}",
            )
            factor_source_rows = {}
    if (
        case_config is not None
        and extraction is not None
        and factor_library is not None
        and (
            case_config.material_evidence is None
            or "materialEvidence" in verified_paths
        )
        and (
            case_config.factory_input is None
            or "factoryInput" in verified_paths
        )
        and (
            case_config.factory_target_map is None
            or "factoryTargetMap" in verified_paths
        )
    ):
        try:
            from dm2c_m23_calculation import ValidationResultSet, validate_candidates
            from dm2c_m23_canonical_release import (
                EvidenceAssembly,
                assemble_factory_input,
                assemble_material_evidence,
                build_validation_rows,
            )

            material_assembly = (
                EvidenceAssembly((), (), ())
                if case_config.material_evidence is None
                else assemble_material_evidence(
                    case_config, extraction, factor_library
                )
            )
            factory_assembly = assemble_factory_input(
                case_config, extraction, factor_library
            )
            measured_record_ids = frozenset(
                candidate.record_id
                for candidate in (
                    *material_assembly.candidates,
                    *factory_assembly.candidates,
                )
                if candidate.measured
            )
            calculated = validate_candidates(
                (*material_assembly.candidates, *factory_assembly.candidates)
            )
            expected_validations = ValidationResultSet(
                accepted=calculated.accepted,
                rejected=tuple(
                    sorted(
                        (
                            *material_assembly.rejected,
                            *factory_assembly.rejected,
                            *calculated.rejected,
                        ),
                        key=lambda row: (row.record_id, row.reason_code, row.message),
                    )
                ),
            )
            expected_rows = build_validation_rows(expected_validations)
            validation_path = root / _OUTPUTS["validation"]
            if (
                tuple(rows) != tuple(expected_rows)
                or validation_path.read_bytes()
                != _validation_csv_bytes(expected_rows)
            ):
                _finding(
                    findings,
                    "validation_source_mismatch",
                    artifact="validation",
                    message="validation CSV differs from independent reconstruction of hashed source inputs",
                    evidence={
                        "actualRows": len(rows),
                        "expectedRows": len(expected_rows),
                    },
                )
        except Exception as exc:
            _finding(
                findings,
                "validation_source_reconstruction_failed",
                artifact="validation",
                message=f"hashed source inputs could not reconstruct validation: {exc}",
            )
    manifest_module = manifest.get("module", {})
    configuration = manifest.get("configuration", {})
    if case_config is not None:
        expected_module_id = stable_id(
            "ModularUnit", case_config.ifc.sha256, case_config.module.source_identity
        )
        expected_module_source = case_config.module.source_identity
        expected_module_name = case_config.module.name
        requested_scope = case_config.requested_scope
        expected_ifc_sha256 = case_config.ifc.sha256
    else:
        expected_module_id = (
            str(manifest_module.get("runtimeId", ""))
            if isinstance(manifest_module, Mapping)
            else ""
        )
        expected_module_source = (
            str(manifest_module.get("sourceIdentity", ""))
            if isinstance(manifest_module, Mapping)
            else ""
        )
        expected_module_name = (
            str(manifest_module.get("name", ""))
            if isinstance(manifest_module, Mapping)
            else ""
        )
        requested_scope = (
            str(configuration.get("requestedScope", ""))
            if isinstance(configuration, Mapping)
            else ""
        )
        expected_ifc_sha256 = (
            str(inputs.get("ifc", {}).get("sha256", ""))
            if isinstance(inputs, Mapping)
            and isinstance(inputs.get("ifc"), Mapping)
            else ""
        )
    _check_stats_against_graph(
        stats, graph, rows, expected_module_id, findings
    )
    context = {
        "schemaVersion": manifest.get("schemaVersion"),
        "releaseProfile": manifest.get("releaseProfile", ""),
        "requestedScope": requested_scope,
        "ifcSha256": expected_ifc_sha256,
        "moduleId": expected_module_id,
        "moduleSourceIdentity": expected_module_source,
        "moduleName": expected_module_name,
        "oneModuleCase": True,
        "factoryInputPresent": bool(
            case_config is not None and case_config.factory_input is not None
        ),
        "extraction": extraction,
        "factorSourceRows": factor_source_rows,
        "expectedValidationRows": expected_rows,
        "measuredRecordIds": measured_record_ids,
        "cypherText": cypher,
        "liveCypherConfig": live_cypher_config,
    }
    core = audit_graph_payload(graph, rows, context)
    findings.extend(core["findings"])
    if stored_alignment != core:
        _finding(
            findings,
            "stored_alignment_mismatch",
            artifact="m2_alignment_report.json",
            message="stored core alignment report differs from independent re-audit",
        )
    manifest_gates = manifest.get("gates")
    if (
        not isinstance(manifest_gates, Mapping)
        or set(manifest_gates)
        != {"alignment", "structuralCypher", "liveCypherRoundTrip"}
        or dict(manifest_gates) != core.get("gates")
    ):
        _finding(
            findings,
            "manifest_gate_mismatch",
            artifact="manifest",
            message="manifest gate claims differ from independent re-audit",
        )
    graph_uses_synthetic_factory_inputs = any(
        isinstance(node, Mapping)
        and "EnergyConsumption" in node.get("labels", ())
        and isinstance(node.get("props", {}), Mapping)
        and str(node.get("props", {}).get("dataProvenance", "")).casefold()
        == "synthetic"
        for node in graph.get("nodes", ())
        if isinstance(graph, Mapping)
    )
    if manifest.get("syntheticFactoryInputsUsed") is not graph_uses_synthetic_factory_inputs:
        _finding(
            findings,
            "synthetic_factory_input_flag_mismatch",
            artifact="manifest",
            message="syntheticFactoryInputsUsed must match synthetic EnergyConsumption provenance in the graph",
        )
    if isinstance(stats, Mapping) and (
        manifest.get("coverage") != stats.get("validation")
    ):
        _finding(
            findings,
            "manifest_counts_mismatch",
            artifact="manifest",
            message="manifest coverage does not equal stats validation coverage",
        )
    ordered = _sorted_findings(findings)
    high = sum(row["severity"] == "high" for row in ordered)
    gates = dict(core["gates"])
    gates["alignment"] = "pass" if high == 0 else "fail"
    return {
        **core,
        "status": "pass" if high == 0 else "fail",
        "highSeverityViolationCount": high,
        "findings": ordered,
        "gates": gates,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a canonical M2.3 v2 release")
    parser.add_argument("--root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        report = audit_release(args.root)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    return 1 if report.get("highSeverityViolationCount") else 0


if __name__ == "__main__":
    raise SystemExit(main())
