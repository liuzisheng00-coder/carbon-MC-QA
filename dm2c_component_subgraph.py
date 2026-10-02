"""Deterministic bounded product closure for a canonical-v2 component."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    CanonicalV2GraphDocument,
    graph_document_from_context,
    iter_graph_edges,
    iter_graph_nodes,
    load_canonical_v2_graph_document,
)
from dm2c_m23_canonical import SCHEMA_VERSION


ROOT_OUTBOUND_PHASES = (
    "hasComponentType",
    "hasMaterial",
    "hasDesignQuantity",
    "manufacturedBy",
)
CONSUMPTION_OUTBOUND_PHASES = (
    "hasQuantity",
    "hasFactor",
    "ofMaterial",
    "ofCarrier",
)


class ComponentSelectorError(ValueError):
    """Base class for controlled component selector failures."""

    def __init__(self, selector: object, message: str):
        self.selector = selector
        super().__init__(message)


class ComponentSelectorInvalid(ComponentSelectorError):
    """Raised for an empty selector or an exact id of the wrong class."""


class ComponentSelectorAmbiguous(ComponentSelectorError):
    """Raised when one IFC identity property resolves to multiple components."""


class ComponentNodeNotFound(ComponentSelectorError, LookupError):
    """Raised when an exact, case-sensitive component selector is absent."""

    def __init__(self, selector: object):
        self.global_id = selector
        super().__init__(
            selector,
            f"BuildingComponent selector not found: {selector!r}",
        )


def _labels(node: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(str(value) for value in node["labels"])


def _edge_sort_key(edge: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(edge["src"]),
        str(edge["type"]),
        str(edge["tgt"]),
        str(edge["occurrenceId"]),
        str(edge["id"]),
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _as_document(
    source: CanonicalV2GraphDocument | CanonicalV2Context,
) -> CanonicalV2GraphDocument:
    if isinstance(source, CanonicalV2Context):
        return graph_document_from_context(source)
    if isinstance(source, CanonicalV2GraphDocument):
        # Iteration performs the reader's provenance check, so a directly
        # instantiated look-alike document is not accepted.
        tuple(iter_graph_nodes(source))
        tuple(iter_graph_edges(source))
        return source
    raise TypeError(
        "source must be a validated CanonicalV2GraphDocument or CanonicalV2Context"
    )


def _node_index(
    document: CanonicalV2GraphDocument,
) -> dict[str, Mapping[str, Any]]:
    return {str(node["id"]): node for node in iter_graph_nodes(document)}


def resolve_component_node_id(
    document: CanonicalV2GraphDocument | CanonicalV2Context,
    selector: str,
) -> str:
    """Resolve one BuildingComponent by exact id, globalId or ifcGlobalId."""

    document = _as_document(document)
    nodes = _node_index(document)
    if type(selector) is not str or not selector or not selector.strip():
        raise ComponentSelectorInvalid(selector, "component selector must be non-empty")

    exact = nodes.get(selector)
    if exact is not None:
        if "BuildingComponent" not in _labels(exact):
            raise ComponentSelectorInvalid(
                selector,
                f"exact node id {selector!r} is not a BuildingComponent",
            )
        return selector

    matches = {
        node_id
        for node_id, node in nodes.items()
        if "BuildingComponent" in _labels(node)
        and any(
            node["props"].get(key) == selector
            for key in ("globalId", "ifcGlobalId")
        )
    }
    if not matches:
        raise ComponentNodeNotFound(selector)
    if len(matches) > 1:
        raise ComponentSelectorAmbiguous(
            selector,
            f"component selector {selector!r} is ambiguous across {len(matches)} nodes",
        )
    return next(iter(matches))


class _ComponentClosure:
    def __init__(
        self,
        *,
        document: CanonicalV2GraphDocument,
        root_id: str,
        max_nodes: int,
    ) -> None:
        self.document = document
        self.node_by_id = _node_index(document)
        self.root_id = root_id
        self.max_nodes = max_nodes
        self.selected_ids: set[str] = {root_id}
        self.selected_edges: dict[str, Mapping[str, Any]] = {}
        self.truncated = False
        self.out_edges: dict[str, list[Mapping[str, Any]]] = {}
        self.in_edges: dict[str, list[Mapping[str, Any]]] = {}
        for edge in iter_graph_edges(document):
            self.out_edges.setdefault(str(edge["src"]), []).append(edge)
            self.in_edges.setdefault(str(edge["tgt"]), []).append(edge)
        for index in (self.out_edges, self.in_edges):
            for node_id in index:
                index[node_id].sort(key=_edge_sort_key)

    def _connect(self, edge: Mapping[str, Any], neighbor_id: str) -> bool:
        if neighbor_id not in self.selected_ids:
            if len(self.selected_ids) >= self.max_nodes:
                self.truncated = True
                return False
            self.selected_ids.add(neighbor_id)
        if edge["src"] in self.selected_ids and edge["tgt"] in self.selected_ids:
            self.selected_edges[str(edge["occurrenceId"])] = edge
            return True
        return False

    def _root_inbound(self) -> tuple[str, ...]:
        consumptions: list[str] = []
        inbound = self.in_edges.get(self.root_id, ())
        for edge in inbound:
            if edge["type"] != "containsComponent":
                continue
            source_id = str(edge["src"])
            if "ModularUnit" in _labels(self.node_by_id[source_id]):
                self._connect(edge, source_id)
        for edge in inbound:
            if edge["type"] != "recordedForObject":
                continue
            source_id = str(edge["src"])
            source_node = self.node_by_id[source_id]
            source_labels = _labels(source_node)
            if not (source_labels & {"MaterialConsumption", "EnergyConsumption"}):
                continue
            if (
                "EnergyConsumption" in source_labels
                and source_node["props"].get("attributionMode") == "process_only"
            ):
                continue
            if self._connect(edge, source_id):
                consumptions.append(source_id)
        return tuple(sorted(set(consumptions)))

    def _root_outbound(self) -> None:
        outbound = self.out_edges.get(self.root_id, ())
        for relation in ROOT_OUTBOUND_PHASES:
            for edge in outbound:
                if edge["type"] == relation:
                    self._connect(edge, str(edge["tgt"]))

    def _consumption_paths(self, consumption_ids: Iterable[str]) -> tuple[str, ...]:
        quantity_ids: list[str] = []
        for consumption_id in sorted(consumption_ids):
            outbound = self.out_edges.get(consumption_id, ())
            for relation in CONSUMPTION_OUTBOUND_PHASES:
                for edge in outbound:
                    if edge["type"] != relation:
                        continue
                    target_id = str(edge["tgt"])
                    if self._connect(edge, target_id) and relation == "hasQuantity":
                        quantity_ids.append(target_id)
            for edge in self.in_edges.get(consumption_id, ()):
                if edge["type"] != "hasCarbonDriver":
                    continue
                source_id = str(edge["src"])
                if "CarbonEmission" in _labels(self.node_by_id[source_id]):
                    self._connect(edge, source_id)
        return tuple(sorted(set(quantity_ids)))

    def _quantity_provenance(self, quantity_ids: Iterable[str]) -> None:
        for quantity_id in sorted(quantity_ids):
            for edge in self.out_edges.get(quantity_id, ()):
                if edge["type"] == "derivedFrom":
                    self._connect(edge, str(edge["tgt"]))

    def build(self) -> dict[str, Any]:
        consumptions = self._root_inbound()
        self._root_outbound()
        quantities = self._consumption_paths(consumptions)
        self._quantity_provenance(quantities)

        nodes = [
            _thaw(self.node_by_id[node_id]) for node_id in sorted(self.selected_ids)
        ]
        edges = [
            _thaw(edge)
            for edge in sorted(self.selected_edges.values(), key=_edge_sort_key)
        ]
        label_counts = Counter(label for node in nodes for label in node["labels"])
        relation_counts = Counter(edge["type"] for edge in edges)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "rootNodeId": self.root_id,
            "nodes": nodes,
            "edges": edges,
            "truncated": self.truncated,
            "boundary": "canonical_component_product_closure",
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "labels": dict(sorted(label_counts.items())),
                "relations": dict(sorted(relation_counts.items())),
            },
        }


def build_component_subgraph(
    source: CanonicalV2GraphDocument | CanonicalV2Context,
    selector: str,
    max_nodes: int = 80,
    expand_shared: bool = False,
) -> dict[str, Any]:
    """Build the exact bounded canonical product closure for one component.

    ``expand_shared`` is retained only as a temporary call-signature bridge;
    canonical terminal leaves are never expanded and the value cannot change
    the resulting closure.
    """

    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    if type(expand_shared) is not bool:
        raise TypeError("expand_shared must be a boolean")
    document = _as_document(source)
    root_id = resolve_component_node_id(document, selector)
    return _ComponentClosure(
        document=document,
        root_id=root_id,
        max_nodes=max_nodes,
    ).build()


def component_subgraph_from_graph_path(
    graph_path: str | Path,
    selector: str,
    max_nodes: int = 80,
    expand_shared: bool = False,
) -> dict[str, Any]:
    """Validate a canonical-v2 graph JSON file and extract one closure."""

    document = load_canonical_v2_graph_document(graph_path)
    return build_component_subgraph(
        document,
        selector,
        max_nodes=max_nodes,
        expand_shared=expand_shared,
    )


def component_subgraph_from_context(
    context: CanonicalV2Context,
    selector: str,
    max_nodes: int = 80,
    expand_shared: bool = False,
) -> dict[str, Any]:
    """Extract one closure from a fully validated six-file release context."""

    return build_component_subgraph(
        context,
        selector,
        max_nodes=max_nodes,
        expand_shared=expand_shared,
    )


__all__ = [
    "ComponentNodeNotFound",
    "ComponentSelectorAmbiguous",
    "ComponentSelectorError",
    "ComponentSelectorInvalid",
    "build_component_subgraph",
    "component_subgraph_from_context",
    "component_subgraph_from_graph_path",
    "resolve_component_node_id",
]
