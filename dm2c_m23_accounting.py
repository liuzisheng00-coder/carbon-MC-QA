"""Canonical M2.3 v2 energy allocation records and query-time accounting.

The canonical graph stores source facts and allocation evidence only.  Product,
module, and source-process totals are projections over those facts; no aggregate
or atomic-emission runtime nodes are created here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable, Literal, Mapping

from dm2c_m23_canonical import CanonicalLPGGraph


AttributionMode = Literal["direct", "allocated", "process_only"]
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


class ScopeAggregationError(ValueError):
    """Raised when contributions cannot be combined under one declared scope."""


@dataclass(frozen=True)
class EnergyAllocationTarget:
    target_component_id: str
    raw_weight: float
    raw_weight_unit: str
    normalized_weight: float
    evidence_record_id: str


@dataclass(frozen=True)
class EnergyPopulationPlan:
    attribution_mode: AttributionMode
    allocation_set_id: str = ""
    allocation_basis: str = ""
    allocations: tuple[EnergyAllocationTarget, ...] = ()
    process_node_ids: tuple[str, ...] = ()
    resource_node_ids: tuple[str, ...] = ()
    unattributed_fraction: float = 0.0

    def __post_init__(self) -> None:
        if self.attribution_mode not in {"direct", "allocated", "process_only"}:
            raise ValueError(
                "attribution_mode must be 'direct', 'allocated', or 'process_only'"
            )
        object.__setattr__(self, "allocations", tuple(self.allocations))
        object.__setattr__(self, "process_node_ids", tuple(self.process_node_ids))
        object.__setattr__(self, "resource_node_ids", tuple(self.resource_node_ids))


def validate_energy_population_plan(plan: EnergyPopulationPlan) -> EnergyPopulationPlan:
    """Validate the graph-independent part of the energy population boundary."""

    if not isinstance(plan, EnergyPopulationPlan):
        raise TypeError("plan must be an EnergyPopulationPlan")
    allocation_set_id = str(plan.allocation_set_id or "").strip()
    allocation_basis = str(plan.allocation_basis or "").strip()
    allocations = tuple(plan.allocations)
    if plan.attribution_mode == "direct":
        if allocations or allocation_set_id or allocation_basis or plan.unattributed_fraction != 0.0:
            raise ValueError("direct attribution cannot carry allocation metadata")
    elif plan.attribution_mode == "allocated":
        if not allocation_set_id:
            raise ValueError("allocated energy requires allocation_set_id")
        if not allocation_basis:
            raise ValueError("allocated energy requires allocation_basis")
        if not allocations:
            raise ValueError("allocated energy requires allocation targets")
        normalized: list[float] = []
        targets: set[str] = set()
        units: set[str] = set()
        for allocation in allocations:
            if not isinstance(allocation, EnergyAllocationTarget):
                raise TypeError("allocations must contain EnergyAllocationTarget records")
            target = _nonempty_text(
                allocation.target_component_id, "target_component_id"
            )
            if target in targets:
                raise ValueError("allocation targets must be unique")
            targets.add(target)
            _finite_nonnegative(allocation.raw_weight, "rawWeight")
            units.add(_nonempty_text(allocation.raw_weight_unit, "rawWeightUnit"))
            weight = _finite_nonnegative(
                allocation.normalized_weight, "allocatedFraction"
            )
            if weight > 1.0:
                raise ValueError("allocatedFraction must not exceed 1")
            normalized.append(weight)
            _nonempty_text(allocation.evidence_record_id, "evidenceRecordId")
        if len(units) != 1:
            raise ValueError("one allocation set requires one rawWeightUnit")
        unattributed_fraction = _finite_nonnegative(
            plan.unattributed_fraction, "unattributedFraction"
        )
        if unattributed_fraction > 1.0 or not math.isclose(
            math.fsum(normalized) + unattributed_fraction,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("allocatedFraction and unattributedFraction values must sum to 1")
    elif plan.attribution_mode == "process_only":
        if allocations or allocation_set_id or allocation_basis or plan.unattributed_fraction != 0.0:
            raise ValueError("process-only energy cannot carry allocation metadata")
        if not plan.process_node_ids and not plan.resource_node_ids:
            raise ValueError(
                "process-only energy requires an existing process or resource context"
            )
    return plan


@dataclass(frozen=True)
class ProductContribution:
    """One deduplicated product projection of a canonical emission fact."""

    key: tuple[str, ...]
    emission_id: str
    component_id: str
    value: float
    scope: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", tuple(self.key))


def _nonempty_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _finite_nonnegative(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _scope_key(value: object) -> str:
    """Match the controlled scope equivalence used during Task 4 acceptance."""

    if not isinstance(value, str) or not value.strip():
        return ""
    return re.sub(
        r"\s+",
        "",
        value.casefold().replace("–", "-").replace("—", "-"),
    )


def _allocation_values(edge: Mapping[str, object]) -> tuple[str, str, float, str, float, str]:
    props = edge.get("props")
    if not isinstance(props, Mapping):
        raise ValueError("an allocation edge requires a property mapping")
    unexpected = sorted(set(props) - _ALLOCATION_FIELDS)
    if unexpected:
        raise ValueError(
            f"allocation edge has unexpected fields: {unexpected}"
        )
    missing = sorted(field for field in _ALLOCATION_FIELDS if field not in props)
    if missing:
        raise ValueError(f"allocation edge is missing fields: {missing}")
    if props.get("allocated") is not True:
        raise ValueError("allocation edge allocated flag must be true")
    allocation_set_id = _nonempty_text(props.get("allocationSetId"), "allocationSetId")
    allocation_basis = _nonempty_text(props.get("allocationBasis"), "allocationBasis")
    raw_weight = _finite_nonnegative(props.get("rawWeight"), "rawWeight")
    raw_weight_unit = _nonempty_text(props.get("rawWeightUnit"), "rawWeightUnit")
    normalized_weight = _finite_nonnegative(
        props.get("allocatedFraction"), "allocatedFraction"
    )
    if normalized_weight > 1.0:
        raise ValueError("allocatedFraction must not exceed 1")
    evidence_record_id = _nonempty_text(
        props.get("evidenceRecordId"), "evidenceRecordId"
    )
    return (
        allocation_set_id,
        allocation_basis,
        raw_weight,
        raw_weight_unit,
        normalized_weight,
        evidence_record_id,
    )


def allocated_contribution_key(
    emission_id: str, edge: Mapping[str, object]
) -> tuple[str, str, str]:
    """Return the canonical deduplication key for one allocation edge."""

    emission = _nonempty_text(emission_id, "emission_id")
    allocation_set_id, _, _, _, _, _ = _allocation_values(edge)
    target_id = _nonempty_text(edge.get("tgt"), "targetId")
    return emission, allocation_set_id, target_id


def contribution_value(emission_value: float, edge: Mapping[str, object]) -> float:
    """Project a source emission through a direct or allocated product edge."""

    value = _finite_nonnegative(emission_value, "emissionValue")
    props = edge.get("props")
    if not isinstance(props, Mapping):
        raise ValueError("a product edge requires a property mapping")
    if _is_allocation_edge(edge):
        _, _, _, _, normalized_weight, _ = _allocation_values(edge)
        return value * normalized_weight
    if "attributionMode" in props:
        raise ValueError("unsupported product-edge attributionMode")
    return value


def _node_has_label(graph: CanonicalLPGGraph, node_id: str, label: str) -> bool:
    node = graph.nodes.get(node_id)
    return node is not None and label in node.get("labels", ())


def _emission_scope(node: Mapping[str, object]) -> str:
    props = node.get("props")
    if not isinstance(props, Mapping):
        return ""
    declared = {
        _scope_key(props.get(field))
        for field in ("recordedScope", "requestedScope", "systemBoundary")
        if _scope_key(props.get(field))
    }
    if len(declared) > 1:
        raise ScopeAggregationError(
            f"emission {node.get('id')!r} declares incompatible scopes {sorted(declared)!r}"
        )
    return next(iter(declared), "")


def _generated_consumption(
    graph: CanonicalLPGGraph, emission_id: str
) -> tuple[str, str]:
    targets = {
        edge["tgt"]
        for edge in graph.edges_of_type("hasCarbonDriver")
        if edge["src"] == emission_id
    }
    if len(targets) != 1:
        raise ValueError(
            f"CarbonEmission {emission_id!r} must have exactly one hasCarbonDriver target"
        )
    consumption_id = next(iter(targets))
    node = graph.nodes.get(consumption_id)
    if node is None:
        raise ValueError(f"emission {emission_id!r} references a missing consumption")
    labels = set(node.get("labels", ()))
    kinds = labels & {"MaterialConsumption", "EnergyConsumption"}
    if len(kinds) != 1:
        raise ValueError(
            f"emission {emission_id!r} must reference one canonical consumption class"
        )
    return consumption_id, next(iter(kinds))


def _recorded_for_edges(
    graph: CanonicalLPGGraph, consumption_id: str
) -> tuple[Mapping[str, object], ...]:
    edges = tuple(
        edge
        for edge in graph.edges_of_type("recordedForObject")
        if edge["src"] == consumption_id
    )
    for edge in edges:
        target_id = str(edge.get("tgt") or "")
        if not (
            _node_has_label(graph, target_id, "BuildingComponent")
            or _node_has_label(graph, target_id, "ModularUnit")
        ):
            raise ValueError(
                f"recordedForObject target {target_id!r} is not an existing ProductObject"
            )
    return edges


def _is_allocation_edge(edge: Mapping[str, object]) -> bool:
    props = edge.get("props")
    if not isinstance(props, Mapping):
        raise ValueError("a product edge requires a property mapping")
    allocated = props.get("allocated")
    if allocated is not None and type(allocated) is not bool:
        raise ValueError("allocated edge flag must be boolean when present")
    return allocated is True or any(
        field in props for field in _ALLOCATION_FIELDS - {"allocated"}
    )


def _contributions_for_emission(
    graph: CanonicalLPGGraph,
    emission_id: str,
    component_filter: frozenset[str] | None,
) -> tuple[ProductContribution, ...]:
    emission_node = graph.nodes[emission_id]
    props = emission_node.get("props", {})
    if not isinstance(props, Mapping):
        raise ValueError(f"CarbonEmission {emission_id!r} requires properties")
    emission_value = _finite_nonnegative(props.get("emissionValue"), "emissionValue")
    scope = _emission_scope(emission_node)
    consumption_id, consumption_kind = _generated_consumption(graph, emission_id)
    edges = _recorded_for_edges(graph, consumption_id)

    if consumption_kind == "MaterialConsumption":
        if not edges:
            raise ValueError(
                f"MaterialConsumption {consumption_id!r} requires recordedForObject"
            )
        if any(_is_allocation_edge(edge) for edge in edges):
            raise ValueError("material product edges cannot carry energy allocation fields")
        targets = {str(edge["tgt"]) for edge in edges}
        if len(targets) != 1:
            raise ValueError("a material source fact must have exactly one product target")
        target_id = next(iter(targets))
        if component_filter is not None and target_id not in component_filter:
            return ()
        return (
            ProductContribution(
                key=("direct", emission_id),
                emission_id=emission_id,
                component_id=target_id,
                value=emission_value,
                scope=scope,
            ),
        )

    consumption_props = graph.nodes[consumption_id].get("props", {})
    if not isinstance(consumption_props, Mapping):
        raise ValueError(
            f"EnergyConsumption {consumption_id!r} requires properties"
        )
    declared_mode = consumption_props.get("attributionMode")
    if declared_mode is not None and declared_mode not in {
        "direct",
        "allocated",
        "process_only",
    }:
        raise ValueError(
            f"EnergyConsumption {consumption_id!r} has unsupported attributionMode"
        )
    if not edges:
        if declared_mode in {"direct", "allocated"}:
            raise ValueError(
                f"EnergyConsumption {consumption_id!r} declares {declared_mode} without a product path"
            )
        return ()
    if declared_mode == "process_only":
        raise ValueError("process-only energy cannot have a recordedForObject product path")
    allocated_flags = {_is_allocation_edge(edge) for edge in edges}
    if len(allocated_flags) != 1:
        raise ValueError(
            f"EnergyConsumption {consumption_id!r} mixes direct and allocated product edges"
        )
    if allocated_flags == {False}:
        if declared_mode == "allocated":
            raise ValueError("allocated energy cannot use a direct product path")
        targets = {str(edge["tgt"]) for edge in edges}
        if len(targets) != 1:
            raise ValueError("a direct energy fact must have exactly one product target")
        target_id = next(iter(targets))
        if component_filter is not None and target_id not in component_filter:
            return ()
        return (
            ProductContribution(
                key=("direct", emission_id),
                emission_id=emission_id,
                component_id=target_id,
                value=emission_value,
                scope=scope,
            ),
        )

    if declared_mode == "direct":
        raise ValueError("direct energy cannot use allocated product paths")
    unique: dict[tuple[str, str, str], ProductContribution] = {}
    allocation_sets: set[str] = set()
    allocation_bases: set[str] = set()
    weights: dict[tuple[str, str, str], float] = {}
    allocation_details: dict[
        tuple[str, str, str], tuple[str, str, float, str, float, str]
    ] = {}
    for edge in edges:
        key = allocated_contribution_key(emission_id, edge)
        details = _allocation_values(edge)
        allocation_set_id, allocation_basis, _, _, normalized_weight, _ = details
        allocation_sets.add(allocation_set_id)
        allocation_bases.add(allocation_basis)
        previous_details = allocation_details.get(key)
        if previous_details is not None and previous_details != details:
            raise ValueError("duplicate allocation key has conflicting evidence or weights")
        allocation_details[key] = details
        weights[key] = normalized_weight
        target_id = key[2]
        if component_filter is None or target_id in component_filter:
            unique[key] = ProductContribution(
                key=("allocated", *key),
                emission_id=emission_id,
                component_id=target_id,
                value=emission_value * normalized_weight,
                scope=scope,
            )
    if len(allocation_sets) != 1 or len(allocation_bases) != 1:
        raise ValueError("one energy source fact requires one allocation set and basis")
    unattributed_fraction = _finite_nonnegative(
        consumption_props.get("unattributedFraction"), "unattributedFraction"
    )
    if unattributed_fraction > 1.0 or not math.isclose(
        sum(weights.values()) + unattributed_fraction,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("allocatedFraction and unattributedFraction values must sum to 1")
    return tuple(unique[key] for key in sorted(unique))


def _product_records(
    graph: CanonicalLPGGraph,
    component_ids: Iterable[str] | None = None,
) -> tuple[ProductContribution, ...]:
    if not isinstance(graph, CanonicalLPGGraph):
        raise TypeError("graph must be a CanonicalLPGGraph")
    component_filter = (
        None if component_ids is None else frozenset(str(item) for item in component_ids)
    )
    deduplicated: dict[tuple[str, ...], ProductContribution] = {}
    emission_ids = sorted(
        node_id
        for node_id, node in graph.nodes.items()
        if "CarbonEmission" in node.get("labels", ())
    )
    for emission_id in emission_ids:
        for contribution in _contributions_for_emission(
            graph, emission_id, component_filter
        ):
            previous = deduplicated.get(contribution.key)
            if previous is not None and previous != contribution:
                raise ValueError("a contribution key resolves to conflicting facts")
            deduplicated[contribution.key] = contribution
    return tuple(deduplicated[key] for key in sorted(deduplicated))


def _validate_scopes(
    contributions: Iterable[ProductContribution], requested_scope: str | None
) -> None:
    rows = tuple(contributions)
    scopes = {row.scope for row in rows if row.scope}
    if len(scopes) > 1:
        raise ScopeAggregationError(
            f"cannot aggregate incompatible scopes {sorted(scopes)!r}"
        )
    if requested_scope is not None:
        requested = _scope_key(_nonempty_text(requested_scope, "requested_scope"))
        if rows and any(row.scope != requested for row in rows):
            raise ScopeAggregationError(
                f"requested scope {requested!r} does not match contribution scope(s) {sorted(scopes)!r}"
            )


def product_contributions(
    graph: CanonicalLPGGraph,
    component_id: str | None = None,
    requested_scope: str | None = None,
) -> tuple[float, ...]:
    """Return deterministic, deduplicated material and energy contributions."""

    component_ids = None if component_id is None else (component_id,)
    rows = _product_records(graph, component_ids)
    _validate_scopes(rows, requested_scope)
    return tuple(row.value for row in rows)


def component_total(
    graph: CanonicalLPGGraph,
    component_id: str,
    requested_scope: str | None = None,
) -> float:
    return float(sum(product_contributions(graph, component_id, requested_scope)))


def module_total(
    graph: CanonicalLPGGraph,
    module_id: str | None,
    requested_scope: str | None = None,
) -> float:
    module = _nonempty_text(module_id, "module_id")
    if not _node_has_label(graph, module, "ModularUnit"):
        raise ValueError(f"module_id {module!r} is not an existing ModularUnit")
    component_ids = {
        str(edge["tgt"])
        for edge in graph.edges_of_type("containsComponent")
        if edge["src"] == module
    }
    rows = _product_records(graph, component_ids)
    _validate_scopes(rows, requested_scope)
    return float(sum(row.value for row in rows))


def source_process_total(
    graph: CanonicalLPGGraph,
    requested_scope: str | None = None,
) -> float:
    """Sum every unique energy-source emission once, including process-only facts."""

    if not isinstance(graph, CanonicalLPGGraph):
        raise TypeError("graph must be a CanonicalLPGGraph")
    rows: list[ProductContribution] = []
    for emission_id, node in sorted(graph.nodes.items()):
        if "CarbonEmission" not in node.get("labels", ()):
            continue
        consumption_id, consumption_kind = _generated_consumption(graph, emission_id)
        if consumption_kind != "EnergyConsumption":
            continue
        # Source totals retain the full source value, but they must not make a
        # malformed product-attribution structure look valid merely because
        # allocation weights are irrelevant to the source-side arithmetic.
        _contributions_for_emission(graph, emission_id, None)
        props = node.get("props", {})
        if not isinstance(props, Mapping):
            raise ValueError(f"CarbonEmission {emission_id!r} requires properties")
        rows.append(
            ProductContribution(
                key=("source", emission_id),
                emission_id=emission_id,
                component_id=consumption_id,
                value=_finite_nonnegative(props.get("emissionValue"), "emissionValue"),
                scope=_emission_scope(node),
            )
        )
    _validate_scopes(rows, requested_scope)
    return float(sum(row.value for row in rows))


__all__ = [
    "EnergyAllocationTarget",
    "EnergyPopulationPlan",
    "ProductContribution",
    "ScopeAggregationError",
    "allocated_contribution_key",
    "component_total",
    "contribution_value",
    "module_total",
    "product_contributions",
    "source_process_total",
    "validate_energy_population_plan",
]
