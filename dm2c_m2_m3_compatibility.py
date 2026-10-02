"""Independent M2-to-M3 gate for one bound canonical-v2 controlled benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    CanonicalSchemaError,
    EmissionFact,
    ProductContribution,
    component_type_ids_for_component,
    dimension_ids,
    graph_document_from_context,
    iter_graph_edges,
    lookup_component,
    lookup_dimension,
    lookup_process,
)
from dm2c_m3_context import M3ExecutionContext, load_m3_execution_context


class _Unreadable(ValueError):
    pass


_CONTROLLED_RELEASE_ID = "controlled-task10-v2"
_CONTROLLED_RELEASE_PROFILE = "controlled-fixture"
_EXPECTED_TOTALS: Mapping[str, Any] = MappingProxyType(
    {
        "materialSource": 19.0,
        "productEnergy": 24.0,
        "product": 43.0,
        "sourceProcess": 31.0,
        "allSource": 50.0,
        "components": MappingProxyType(
            {
                "component:c1": 15.0,
                "component:c2": 21.0,
                "component:no-facts": 6.0,
                "component:c4": 1.0,
            }
        ),
        "modules": MappingProxyType({"module:fixture": 43.0}),
        "materials": MappingProxyType(
            {"material:steel": 16.0, "material:unused": 3.0}
        ),
        "carriers": MappingProxyType(
            {"carrier:electricity": 24.0, "carrier:gas": 7.0}
        ),
        "processes": MappingProxyType({"process:stage": 7.0}),
    }
)


def _program(*steps: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({"steps": tuple(MappingProxyType(dict(row)) for row in steps)})


_EXPECTED_COMPAT_CASES: tuple[tuple[str, Mapping[str, Any], tuple[str, ...], str], ...] = (
    (
        "product-project",
        _program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            {"op": "Trace"},
        ),
        (),
        "executable",
    ),
    (
        "material-source",
        _program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            {"op": "Trace"},
        ),
        (),
        "executable",
    ),
    (
        "energy-source",
        _program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "GroupBy", "keys": ("carrier",)},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            {"op": "Trace"},
        ),
        (),
        "incomplete_path",
    ),
    (
        "accepted-zero",
        _program(
            {"op": "SelectClicked", "ids": ("component:c4",)},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            {"op": "Trace"},
        ),
        ("component:c4",),
        "executable",
    ),
    (
        "unknown-component",
        _program(
            {"op": "SelectClicked", "ids": ("component:missing",)},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        ("component:missing",),
        "unresolved_target",
    ),
)

_EXPECTED_SOURCE_CASE_IDS = (
    "controlled-v2-product",
    "controlled-v2-material-source",
    "controlled-v2-energy-source",
    "controlled-v2-source-union",
)

_EXPECTED_SOURCE_SIGNATURES: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = MappingProxyType(
    {
        "controlled-v2-product": (
            _program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "GroupBy", "keys": ("component",)},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            MappingProxyType(
                {
                    "filters": (),
                    "group_keys": ("component",),
                    "join_required_sources": (),
                    "projection_perspective": "product",
                    "selector_component_ids": (),
                    "sources": ("material", "process"),
                }
            ),
        ),
        "controlled-v2-material-source": (
            _program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ("material",)},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            MappingProxyType(
                {
                    "filters": (),
                    "group_keys": ("material",),
                    "join_required_sources": (),
                    "projection_perspective": "material_source",
                    "selector_component_ids": (),
                    "sources": ("material",),
                }
            ),
        ),
        "controlled-v2-energy-source": (
            _program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ("carrier",)},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            MappingProxyType(
                {
                    "filters": (),
                    "group_keys": ("carrier",),
                    "join_required_sources": (),
                    "projection_perspective": "energy_source",
                    "selector_component_ids": (),
                    "sources": ("process",),
                }
            ),
        ),
        "controlled-v2-source-union": (
            _program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "GroupBy", "keys": ("material", "process")},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            MappingProxyType(
                {
                    "filters": (),
                    "group_keys": ("material", "process"),
                    "join_required_sources": (),
                    "projection_perspective": "source_union",
                    "selector_component_ids": (),
                    "sources": ("material", "process"),
                }
            ),
        ),
    }
)

_ENTITY_DIMENSIONS = {
    "module",
    "component_type",
    "component",
    "ifc_class",
    "material",
    "carrier",
    "stage",
    "process",
    "resource",
}
_ENTITY_NAME_FIELDS = {f"{dimension}_name": dimension for dimension in _ENTITY_DIMENSIONS}
_CARRIER_NAME_CACHE: dict[int, Mapping[str, str | None]] = {}
_STAGE_BY_ACTIVITY_CACHE: dict[int, Mapping[str, str | None]] = {}


@dataclass(frozen=True, slots=True)
class CompatibilityFinding:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    exit_code: int
    status: str
    findings: tuple[CompatibilityFinding, ...]
    totals: Mapping[str, float]
    checks: Mapping[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exitCode": self.exit_code,
            "status": self.status,
            "findings": [
                {"code": row.code, "message": row.message} for row in self.findings
            ],
            "totals": dict(self.totals),
            "checks": dict(self.checks),
        }


@dataclass(frozen=True, slots=True)
class _Projection:
    key: tuple[str, ...]
    emission_id: str
    consumption_id: str
    source_kind: str
    value: float
    component_id: str | None
    dimensions: Mapping[str, Any]
    evidence: tuple[str, ...]
    occurrence_id: str | None
    evidence_record_id: str | None


def _reject_constant(value: str) -> None:
    raise _Unreadable(f"non-finite JSON constant {value!r}")


def _pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in rows:
        if key in output:
            raise _Unreadable(f"duplicate JSON key {key!r}")
        output[key] = value
    return output


def _strict_json(payload: bytes, label: str) -> Mapping[str, Any]:
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Unreadable(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise _Unreadable(f"{label} must be a JSON object")
    return value


def _jsonl(payload: bytes, label: str) -> tuple[Mapping[str, Any], ...]:
    if b"\r" in payload:
        raise _Unreadable(f"{label} must use LF line endings")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Unreadable(f"{label} is not UTF-8") from exc
    lines = text.splitlines()
    if not lines or any(not line for line in lines):
        raise _Unreadable(f"{label} contains an empty JSONL row")
    return tuple(_strict_json(line.encode("utf-8"), label) for line in lines)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _semantic_graph_hash(release_dir: Path) -> str:
    graph = _strict_json(
        (release_dir / "multigranular_carbon_kg.json").read_bytes(), "graph"
    )
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise _Unreadable("graph nodes/edges are invalid")
    normalized = {
        "schemaVersion": graph.get("schemaVersion"),
        "nodes": sorted(nodes, key=lambda row: str(row["id"])),
        "edges": sorted(edges, key=lambda row: str(row["occurrenceId"])),
    }
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha(payload)


_EXACT_RELEASE_FILES = {
    "case_version_manifest.json",
    "multigranular_carbon_kg.json",
    "multigranular_carbon_kg.cypher",
    "multigranular_carbon_kg_stats.json",
    "multigranular_carbon_kg_validation.csv",
    "m2_alignment_report.json",
}


def _preflight_release(release: Path) -> Mapping[str, bytes]:
    if not release.is_dir():
        raise _Unreadable("release directory is missing")
    names = {row.name for row in release.iterdir() if row.is_file()}
    if names != _EXACT_RELEASE_FILES:
        raise _Unreadable("release must contain exactly the six canonical files")
    blobs = {name: (release / name).read_bytes() for name in sorted(names)}
    _strict_json(blobs["case_version_manifest.json"], "release manifest")
    _strict_json(blobs["multigranular_carbon_kg.json"], "graph")
    _strict_json(blobs["multigranular_carbon_kg_stats.json"], "stats")
    _strict_json(blobs["m2_alignment_report.json"], "alignment report")
    try:
        blobs["multigranular_carbon_kg.cypher"].decode("utf-8")
        blobs["multigranular_carbon_kg_validation.csv"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Unreadable(f"release text file is not UTF-8: {exc}") from exc
    return MappingProxyType(blobs)


def _props(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("props", {})
    return value if isinstance(value, Mapping) else {}


def _entity_ids(canonical: CanonicalV2Context, dimension: str) -> tuple[str, ...]:
    if dimension == "stage":
        return tuple(
            entity_id
            for entity_id in dimension_ids(canonical, "process")
            if "ProductionStage"
            in tuple(lookup_dimension(canonical, "process", entity_id).get("labels", ()))
        )
    return dimension_ids(canonical, dimension)


def _carrier_names(canonical: CanonicalV2Context) -> Mapping[str, str | None]:
    key = id(canonical)
    cached = _CARRIER_NAME_CACHE.get(key)
    if cached is not None:
        return cached
    names: dict[str, list[str]] = {}
    for fact in canonical.emissions:
        if not fact.carrier_id:
            continue
        for process_id in fact.process_ids:
            process_props = _props(lookup_process(canonical, process_id))
            process_value = process_props.get("energyCarrier")
            if process_value is None:
                continue
            text = str(process_value).strip()
            if text and text not in names.setdefault(fact.carrier_id, []):
                names[fact.carrier_id].append(text)
    resolved = {
        carrier_id: values[0] if len(values) == 1 else None
        for carrier_id, values in names.items()
    }
    _CARRIER_NAME_CACHE[key] = MappingProxyType(resolved)
    return _CARRIER_NAME_CACHE[key]


def _stage_by_activity(canonical: CanonicalV2Context) -> Mapping[str, str | None]:
    """Map each activity to the production stage that owns it.

    Energy is recorded against the activity that consumed it, so the stage sits
    one hop up the production structure rather than on the record itself. An
    activity claimed by more than one stage stays unresolved. This gate walks
    the release itself so it keeps checking M3 against an independent reading.
    """
    key = id(canonical)
    cached = _STAGE_BY_ACTIVITY_CACHE.get(key)
    if cached is not None:
        return cached
    owners: dict[str, set[str]] = {}
    for edge in iter_graph_edges(graph_document_from_context(canonical)):
        if edge.get("type") != "hasActivity":
            continue
        stage_id = str(edge.get("src") or "")
        activity_id = str(edge.get("tgt") or "")
        if stage_id and activity_id:
            owners.setdefault(activity_id, set()).add(stage_id)
    resolved = {
        activity_id: next(iter(stages)) if len(stages) == 1 else None
        for activity_id, stages in owners.items()
    }
    _STAGE_BY_ACTIVITY_CACHE[key] = MappingProxyType(resolved)
    return _STAGE_BY_ACTIVITY_CACHE[key]


def _readable_name(
    canonical: CanonicalV2Context, dimension: str, entity_id: str | None
) -> str | None:
    if entity_id is None:
        return None
    if dimension == "ifc_class":
        return entity_id if entity_id in dimension_ids(canonical, "ifc_class") else None
    if entity_id not in set(_entity_ids(canonical, dimension)):
        return None
    lookup_dimension_name = "process" if dimension == "stage" else dimension
    props = _props(lookup_dimension(canonical, lookup_dimension_name, entity_id))
    value = props.get("name")
    if value is None and dimension == "process":
        value = props.get("activityName") or props.get("templateName")
    if value is None and dimension == "stage":
        value = props.get("stageName") or props.get("name")
    if value is None and dimension == "carrier":
        value = _carrier_names(canonical).get(entity_id)
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _with_readable_names(
    canonical: CanonicalV2Context, values: dict[str, Any]
) -> Mapping[str, Any]:
    for dimension in _ENTITY_DIMENSIONS:
        values[f"{dimension}_name"] = _readable_name(
            canonical, dimension, values.get(dimension)
        )
    return MappingProxyType(values)


def _fact_dimensions(
    canonical: CanonicalV2Context,
    fact: EmissionFact,
    contribution: ProductContribution | None,
) -> dict[str, Any]:
    component_id = contribution.component_id if contribution is not None else None
    component_type = None
    if component_id:
        type_ids = component_type_ids_for_component(
            canonical, component_id
        )
        component_type = type_ids[0] if len(type_ids) == 1 else None
        component_props = _props(lookup_component(canonical, component_id))
        ifc_class = component_props.get("ifcClass")
    else:
        ifc_class = None
    stage = None
    process = None
    for entity_id in fact.process_ids:
        labels = tuple(lookup_process(canonical, entity_id).get("labels", ()))
        if "ProductionStage" in labels:
            stage = entity_id
        elif process is None:
            process = entity_id
    if stage is None and process is not None:
        stage = _stage_by_activity(canonical).get(process)
    return _with_readable_names(
        canonical,
        {
            "project": str(canonical.manifest["releaseId"]),
            "module": contribution.module_id if contribution is not None else None,
            "component_type": component_type,
            "component": component_id,
            "ifc_class": ifc_class,
            "material": fact.material_id,
            "carrier": fact.carrier_id,
            "factor_keyword": fact.factor_keyword,
            "factor_source": fact.factor_source,
            "process": process,
            "stage": stage,
            "resource": fact.resource_ids[0] if len(fact.resource_ids) == 1 else None,
            "source_kind": "material" if fact.kind == "material" else "process",
        },
    )


def _independent_evidence(
    fact: EmissionFact, contribution: ProductContribution | None = None
) -> tuple[str, ...]:
    ordered: list[str] = []

    def add(value: str | None) -> None:
        if value and value not in ordered:
            ordered.append(value)

    for value in (
        fact.emission_id,
        fact.consumption_id,
        fact.quantity_id,
        fact.factor_id,
        fact.material_id,
        fact.carrier_id,
        *fact.design_quantity_ids,
        *fact.process_ids,
        *fact.resource_ids,
        fact.source_record_id,
        fact.evidence_source_id,
    ):
        add(value)
    if contribution is not None:
        add(contribution.component_id)
        add(contribution.recorded_for_occurrence_id)
        add(contribution.evidence_record_id)
    return tuple(ordered)


def _independent_projections(
    context: M3ExecutionContext, perspective: str
) -> tuple[_Projection, ...]:
    canonical = context.canonical
    facts = {row.emission_id: row for row in canonical.emissions}
    output: list[_Projection] = []
    if perspective == "product":
        for contribution in canonical.product_contributions:
            fact = facts[contribution.emission_id]
            output.append(
                _Projection(
                    key=contribution.key,
                    emission_id=fact.emission_id,
                    consumption_id=fact.consumption_id,
                    source_kind="material" if fact.kind == "material" else "process",
                    value=contribution.projected_value,
                    component_id=contribution.component_id,
                    dimensions=MappingProxyType(
                        _fact_dimensions(canonical, fact, contribution)
                    ),
                    evidence=_independent_evidence(fact, contribution),
                    occurrence_id=contribution.recorded_for_occurrence_id,
                    evidence_record_id=contribution.evidence_record_id,
                )
            )
        return tuple(sorted(output, key=lambda row: row.key))
    for fact in canonical.emissions:
        if perspective == "material_source" and fact.kind != "material":
            continue
        if perspective == "energy_source" and fact.kind != "energy":
            continue
        output.append(
            _Projection(
                key=("source", fact.emission_id),
                emission_id=fact.emission_id,
                consumption_id=fact.consumption_id,
                source_kind="material" if fact.kind == "material" else "process",
                value=fact.emission_value,
                component_id=None,
                dimensions=MappingProxyType(_fact_dimensions(canonical, fact, None)),
                evidence=_independent_evidence(fact),
                occurrence_id=None,
                evidence_record_id=None,
            )
        )
    return tuple(sorted(output, key=lambda row: row.key))


_PRODUCT_KEYS = {
    "project",
    "module",
    "module_name",
    "component_type",
    "component_type_name",
    "component",
    "component_name",
    "ifc_class",
    "ifc_class_name",
}
_MATERIAL_KEYS = {"material", "material_name"}
_PROCESS_KEYS = {
    "carrier",
    "carrier_name",
    "stage",
    "stage_name",
    "process",
    "process_name",
    "resource",
    "resource_name",
}


def _perspective(program: Mapping[str, Any]) -> str:
    steps = program.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
        raise _Unreadable("benchmark program has no steps")
    selector = steps[0]
    if not isinstance(selector, Mapping):
        raise _Unreadable("benchmark selector is invalid")
    keys: set[str] = set()
    for step in steps:
        if not isinstance(step, Mapping):
            raise _Unreadable("benchmark step is invalid")
        if step.get("op") == "GroupBy":
            raw = step.get("keys", ())
            values = (
                raw
                if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
                else (raw,)
            )
            keys.update(str(value) for value in values)
        elif step.get("op") == "Filter":
            keys.add(str(step.get("field") or ""))
    selector_type = str(selector.get("entity_type") or "component")
    if (
        selector.get("op") == "SelectClicked"
        or (selector.get("op") == "ResolveEntities" and selector_type == "component")
        or bool(keys & _PRODUCT_KEYS)
    ):
        return "product"
    material = selector_type == "material" or bool(keys & _MATERIAL_KEYS)
    process = selector_type == "process" or bool(keys & _PROCESS_KEYS)
    if material and process:
        return "source_union"
    if material:
        return "material_source"
    if process:
        return "energy_source"
    sources = _sources(program)
    if keys & {"factor_keyword", "factor_source", "source_kind"}:
        if sources == {"material"}:
            return "material_source"
        if sources == {"process"}:
            return "energy_source"
        return "source_union"
    if not keys and sources == {"process"}:
        selector_names_products = (
            selector.get("op") == "SelectClicked"
            or (selector.get("op") == "ResolveEntities" and selector_type == "component")
        )
        if not selector_names_products:
            return "energy_source"
    return "product"


def _sources(program: Mapping[str, Any]) -> set[str]:
    output: set[str] = set()
    for step in program["steps"]:
        if step.get("op") != "CarbonAtoms":
            continue
        raw = step.get("source")
        values = (raw,) if isinstance(raw, str) else tuple(raw or ())
        if "all" in values:
            output.update(("material", "process"))
        else:
            output.update(str(value) for value in values)
    return output


def _filter_values(step: Mapping[str, Any]) -> tuple[str, ...]:
    raw = (step["equals"],) if "equals" in step else tuple(step.get("in") or ())
    return tuple(str(value) for value in raw)


def _resolve_filter(
    canonical: CanonicalV2Context, step: Mapping[str, Any]
) -> tuple[str, tuple[str, ...], bool]:
    field = str(step.get("field"))
    values = _filter_values(step)
    if field not in _ENTITY_DIMENSIONS and field not in _ENTITY_NAME_FIELDS:
        return field, tuple(dict.fromkeys(values)), False
    by_name = field in _ENTITY_NAME_FIELDS
    dimension = _ENTITY_NAME_FIELDS[field] if by_name else field
    available = tuple(_entity_ids(canonical, dimension))
    resolved: list[str] = []
    for value in values:
        if not by_name and value in available:
            if value not in resolved:
                resolved.append(value)
            continue
        matches = tuple(
            entity_id
            for entity_id in available
            if _readable_name(canonical, dimension, entity_id) == value
        )
        if len(matches) != 1:
            return field, (), True
        target = _readable_name(canonical, dimension, matches[0]) if by_name else matches[0]
        if target is not None and target not in resolved:
            resolved.append(target)
    return field, tuple(resolved), False


def _group_result_row(
    groups: tuple[str, ...], group: tuple[Any, ...], items: Sequence[_Projection]
) -> dict[str, Any]:
    row: dict[str, Any] = {key: value for key, value in zip(groups, group)}
    for key in groups:
        if key in _ENTITY_DIMENSIONS:
            name_key = f"{key}_name"
            names = {
                item.dimensions.get(name_key)
                for item in items
                if item.dimensions.get(name_key) is not None
            }
            row[name_key] = next(iter(names)) if len(names) == 1 else None
    row["kgCO2e"] = math.fsum(
        item.value for item in sorted(items, key=lambda value: value.key)
    )
    return row


def _resolve_components(
    context: M3ExecutionContext,
    program: Mapping[str, Any],
    external_ids: Sequence[str],
) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    selector = program["steps"][0]
    op = selector.get("op")
    if op == "SelectProject":
        return None, ()
    raw = tuple(selector.get("ids") or ()) or tuple(external_ids)
    available = set(dimension_ids(context.canonical, "component"))
    aliases = {
        str(_props(lookup_component(context.canonical, entity_id)).get("globalId")): entity_id
        for entity_id in available
    }
    resolved: list[str] = []
    missing: list[str] = []
    for value in raw:
        token = str(value).strip()
        entity_id = token if token in available else aliases.get(token)
        if entity_id and entity_id not in resolved:
            resolved.append(entity_id)
        elif entity_id is None and token not in missing:
            missing.append(token)
    return tuple(resolved), tuple(missing)


def _independent_execute(
    context: M3ExecutionContext,
    program: Mapping[str, Any],
    external_ids: Sequence[str],
) -> dict[str, Any]:
    perspective = _perspective(program)
    components, missing = _resolve_components(context, program, external_ids)
    requested = tuple(components or ()) + missing
    rejected_count = context.validation_coverage.rejected_count
    if missing:
        return {
            "status": "unresolved_target",
            "rows": [],
            "summary": {},
            "emission_ids": [],
            "projection_keys": [],
            "trace_rows": [],
            "coverage": {
                "accepted_projection_count": 0,
                "accepted_emission_count": 0,
                "rejected_count": rejected_count,
                "requested_component_count": len(requested),
                "covered_component_ids": list(components or ()),
                "missing_component_ids": list(missing),
            },
            "holes": [],
        }
    projections = list(_independent_projections(context, perspective))
    sources = _sources(program)
    projections = [row for row in projections if row.source_kind in sources]
    if components is not None:
        projections = [row for row in projections if row.component_id in components]
    for step in program["steps"]:
        if step.get("op") == "Filter":
            field, values, unsatisfiable = _resolve_filter(context.canonical, step)
            if unsatisfiable:
                return {
                    "status": "unsatisfiable_filter",
                    "rows": [],
                    "summary": {},
                    "emission_ids": [],
                    "projection_keys": [],
                    "trace_rows": [],
                    "coverage": {
                        "accepted_projection_count": 0,
                        "accepted_emission_count": 0,
                        "rejected_count": rejected_count,
                        "requested_component_count": len(requested),
                        "covered_component_ids": list(components or ()),
                        "missing_component_ids": [],
                    },
                    "holes": [],
                }
            allowed = set(values)
            projections = [row for row in projections if row.dimensions.get(field) in allowed]
    groups = next(
        (
            tuple(str(value) for value in step.get("keys", ()))
            for step in program["steps"]
            if step.get("op") == "GroupBy"
        ),
        (),
    )
    if groups:
        grouped: dict[tuple[Any, ...], list[_Projection]] = {}
        for row in projections:
            grouped.setdefault(tuple(row.dimensions.get(key) for key in groups), []).append(row)
        result_rows = [
            _group_result_row(groups, group, items)
            for group, items in sorted(
                grouped.items(), key=lambda item: tuple(str(value or "") for value in item[0])
            )
        ]
    else:
        result_rows = [
            {
                "kgCO2e": math.fsum(
                    row.value for row in sorted(projections, key=lambda value: value.key)
                )
            }
        ]
    ordered = tuple(sorted(projections, key=lambda row: row.key))
    trace = any(step.get("op") == "Trace" for step in program["steps"])
    trace_rows = [
        {
            "projection_key": list(row.key),
            "emission_id": row.emission_id,
            "consumption_id": row.consumption_id,
            "source_kind": row.source_kind,
            "value": row.value,
            "component_id": row.component_id,
            "evidence_ids": list(row.evidence),
            "recorded_for_occurrence_id": row.occurrence_id,
            "evidence_record_id": row.evidence_record_id,
        }
        for row in ordered
    ] if trace else []
    covered = sorted({row.component_id for row in ordered if row.component_id})
    return {
        "status": "ok",
        "rows": result_rows,
        "summary": {
            "total_kgCO2e": math.fsum(row.value for row in ordered),
            "projection_perspective": perspective,
            "row_count": len(result_rows),
        },
        "emission_ids": sorted({row.emission_id for row in ordered}),
        "projection_keys": [list(row.key) for row in ordered],
        "trace_rows": trace_rows,
        "coverage": {
            "accepted_projection_count": len(ordered),
            "accepted_emission_count": len({row.emission_id for row in ordered}),
            "rejected_count": rejected_count,
            "requested_component_count": len(requested),
            "covered_component_ids": covered,
            "missing_component_ids": [],
        },
        "holes": [],
    }


def _m3_status(
    context: M3ExecutionContext,
    program: Mapping[str, Any],
    output: Mapping[str, Any],
) -> str:
    if output["status"] == "unresolved_target":
        return "unresolved_target"
    perspective = _perspective(program)
    sources = _sources(program)
    required_kinds = {
        *(('material',) if 'material' in sources else ()),
        *(('energy',) if 'process' in sources else ()),
    }
    if perspective == "material_source":
        required_kinds = {"material"}
    elif perspective == "energy_source":
        required_kinds = {"energy"}
    if any(
        context.availability.get(kind) != "complete" for kind in required_kinds
    ):
        return "incomplete_path"
    selected_components = set(output["coverage"]["covered_component_ids"])
    filters: dict[str, set[Any]] = {}
    for step in program["steps"]:
        if step.get("op") != "Filter":
            continue
        values = (
            {step.get("equals")}
            if "equals" in step
            else set(step.get("in") or ())
        )
        filters[str(step.get("field"))] = values
    relevant = []
    for row in context.validation_coverage.records:
        if row.status != "rejected" or row.kind not in required_kinds:
            continue
        if perspective == "product":
            if not row.component_ids:
                continue
            if selected_components and selected_components.isdisjoint(row.component_ids):
                continue
        candidates: Mapping[str, Any] = {
            "material": row.material_id,
            "carrier": row.carrier_id,
            "process": row.process_ids,
            "stage": row.process_ids,
            "requested_scope": row.requested_scope,
            "scope": row.requested_scope,
        }
        unrelated = False
        for field, allowed in filters.items():
            candidate = candidates.get(field)
            if candidate in (None, (), ""):
                continue
            values = set(candidate) if isinstance(candidate, tuple) else {candidate}
            if values.isdisjoint(allowed):
                unrelated = True
                break
        if not unrelated:
            relevant.append(row)
    if relevant:
        return "incomplete_path"
    if not output["projection_keys"]:
        return "empty_result"
    return "executable"


def _group_projection_values(
    rows: Sequence[_Projection], dimension: str
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        value = row.dimensions.get(dimension)
        values = value if isinstance(value, tuple) else (value,)
        for entity_id in values:
            if entity_id:
                grouped.setdefault(str(entity_id), []).append(row.value)
    return {
        key: math.fsum(sorted(values)) for key, values in sorted(grouped.items())
    }


def _totals(
    context: M3ExecutionContext,
) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, float]]]:
    source = _independent_projections(context, "source_union")
    product = _independent_projections(context, "product")
    material = math.fsum(
        row.value for row in source if row.source_kind == "material"
    )
    process = math.fsum(
        row.value for row in source if row.source_kind == "process"
    )
    product_energy = math.fsum(
        row.value for row in product if row.source_kind == "process"
    )
    totals = {
        "materialSource": material,
        "productEnergy": product_energy,
        "product": math.fsum(row.value for row in product),
        "sourceProcess": process,
        "allSource": math.fsum((material, process)),
    }
    components: dict[str, list[float]] = {}
    for row in product:
        if row.component_id is None:
            continue
        components.setdefault(row.component_id, []).append(row.value)
    component_totals = {
        key: math.fsum(sorted(values)) for key, values in sorted(components.items())
    }
    processes = _group_projection_values(source, "stage")
    for key, value in _group_projection_values(source, "process").items():
        processes[key] = math.fsum((processes.get(key, 0.0), value))
    dimensions = {
        "modules": _group_projection_values(product, "module"),
        "materials": _group_projection_values(source, "material"),
        "carriers": _group_projection_values(source, "carrier"),
        "processes": dict(sorted(processes.items())),
    }
    return totals, component_totals, dimensions


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)) and isinstance(right, Sequence) and not isinstance(right, (str, bytes)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _source_bundle(
    sidecar_path: Path,
    source: Mapping[str, Any],
    findings: list[CompatibilityFinding],
) -> tuple[Path, dict[str, bytes]] | None:
    directory = source.get("directory")
    files = source.get("files")
    if not isinstance(directory, str) or not isinstance(files, Mapping):
        raise _Unreadable("sourceBenchmark binding is invalid")
    pure = PurePosixPath(directory)
    if pure.is_absolute() or ".." in pure.parts or "\\" in directory:
        raise _Unreadable("sourceBenchmark directory is unsafe")
    root = sidecar_path.parent / Path(*pure.parts)
    expected_names = {
        "e4c_cases_v2.jsonl",
        "e4c_truth_v2.jsonl",
        "e4c_machine_audit_v2.json",
        "e4c_manifest_v2.json",
    }
    if set(files) != expected_names or any(
        not isinstance(descriptor, Mapping)
        or set(descriptor) != {"sha256", "sizeBytes"}
        for descriptor in files.values()
    ):
        findings.append(
            CompatibilityFinding("source_benchmark_contract_mismatch", "file descriptors")
        )
    if not root.is_dir() or {row.name for row in root.iterdir()} != expected_names:
        raise _Unreadable("sourceBenchmark must contain exactly four files")
    blobs = {name: (root / name).read_bytes() for name in sorted(expected_names)}
    for name, payload in blobs.items():
        descriptor = files.get(name)
        if not isinstance(descriptor, Mapping):
            findings.append(CompatibilityFinding("source_benchmark_hash_mismatch", name))
            continue
        if descriptor.get("sha256") != _sha(payload) or descriptor.get("sizeBytes") != len(payload):
            findings.append(CompatibilityFinding("source_benchmark_hash_mismatch", name))
    if any(row.code == "source_benchmark_hash_mismatch" for row in findings):
        return None
    return root, blobs


def _check_source_truth(
    context: M3ExecutionContext,
    blobs: Mapping[str, bytes],
    findings: list[CompatibilityFinding],
) -> None:
    manifest = _strict_json(blobs["e4c_manifest_v2.json"], "source manifest")
    audit = _strict_json(blobs["e4c_machine_audit_v2.json"], "source audit")
    if set(manifest) != {"schema_version", "release_id", "files"} or manifest.get(
        "schema_version"
    ) != "controlled-v2":
        findings.append(CompatibilityFinding("source_contract_mismatch", "manifest shape"))
    if manifest.get("release_id") != context.canonical.manifest["releaseId"]:
        findings.append(CompatibilityFinding("source_benchmark_release_mismatch", "release id"))
    internal = manifest.get("files")
    if not isinstance(internal, Mapping):
        raise _Unreadable("source manifest files are invalid")
    for name in ("e4c_cases_v2.jsonl", "e4c_truth_v2.jsonl", "e4c_machine_audit_v2.json"):
        descriptor = internal.get(name)
        payload = blobs[name]
        if not isinstance(descriptor, Mapping) or descriptor.get("sha256") != _sha(payload) or descriptor.get("size_bytes") != len(payload):
            findings.append(CompatibilityFinding("source_manifest_hash_mismatch", name))
    expected_audit = {
        "case_count": 4,
        "oracle_mismatch_case_ids": [],
        "oracle_mismatch_count": 0,
        "process_only_source_covered": True,
        "rejected_validation_count": 1,
        "release_id": _CONTROLLED_RELEASE_ID,
        "release_profile": _CONTROLLED_RELEASE_PROFILE,
        "schema_version": "controlled-v2",
        "valid_zero_covered": True,
    }
    if not _same(audit, expected_audit):
        findings.append(CompatibilityFinding("source_audit_mismatch", "stored audit"))
    cases = _jsonl(blobs["e4c_cases_v2.jsonl"], "source cases")
    truth = _jsonl(blobs["e4c_truth_v2.jsonl"], "source truth")
    truth_by_id = {str(row.get("case_id")): row for row in truth}
    case_ids = tuple(str(row.get("case_id")) for row in cases)
    truth_ids = tuple(str(row.get("case_id")) for row in truth)
    if case_ids != _EXPECTED_SOURCE_CASE_IDS or truth_ids != _EXPECTED_SOURCE_CASE_IDS:
        findings.append(CompatibilityFinding("source_case_contract_mismatch", "case ids/order"))
    if len(truth_by_id) != len(truth) or len(cases) != len(truth):
        findings.append(CompatibilityFinding("source_truth_mismatch", "case identity/count"))
        return
    for case in cases:
        case_id = str(case.get("case_id"))
        program = case.get("gold_program")
        reference = case.get("reference")
        if not isinstance(program, Mapping) or not isinstance(reference, Mapping):
            raise _Unreadable("source case program/reference is invalid")
        signature = _EXPECTED_SOURCE_SIGNATURES.get(case_id)
        if signature is None or not _same(program, signature[0]) or not _same(reference, signature[1]):
            findings.append(CompatibilityFinding("source_case_contract_mismatch", case_id))
        observed = _independent_execute(
            context, program, tuple(reference.get("selector_component_ids") or ())
        )
        expected = dict(truth_by_id.get(case_id, {}))
        expected.pop("case_id", None)
        if not _same(observed, expected):
            findings.append(CompatibilityFinding("source_truth_mismatch", case_id))


def _controlled_validation_ok(context: M3ExecutionContext) -> bool:
    expected = {
        "record:allocated": ("accepted", "energy", "emission:allocated"),
        "record:direct": ("accepted", "energy", "emission:direct"),
        "record:material": ("accepted", "material", "emission:material"),
        "record:material-c2": ("accepted", "material", "emission:material-c2"),
        "record:material-c3": ("accepted", "material", "emission:material-c3"),
        "record:material-c4": ("accepted", "material", "emission:material-c4"),
        "record:process": ("accepted", "energy", "emission:process"),
        "record:zero": ("accepted", "energy", "emission:zero"),
        "record:rejected": ("rejected", "energy", None),
    }
    records = {row.record_id: row for row in context.validation_coverage.records}
    if set(records) != set(expected):
        return False
    if any(
        (records[key].status, records[key].kind, records[key].emission_id) != value
        for key, value in expected.items()
    ):
        return False
    rejected = records["record:rejected"]
    return (
        rejected.reason_code == "factor_not_found"
        and rejected.source_record_id == "source:rejected"
        and rejected.evidence_source_id == "evidence:rejected"
        and rejected.component_ids == ()
        and rejected.material_id is None
        and rejected.carrier_id == "carrier:electricity"
        and rejected.process_ids == ()
        and rejected.recorded_scope == "A1–A3"
        and rejected.requested_scope == " a1 - a3 "
    )


def _invariant_checks(context: M3ExecutionContext) -> dict[str, bool]:
    canonical = context.canonical
    facts = {row.emission_id: row for row in canonical.emissions}
    allocated = [
        row for row in canonical.product_contributions if row.mode == "allocated"
    ]
    by_allocation: dict[tuple[str, str | None], list[ProductContribution]] = {}
    for row in allocated:
        by_allocation.setdefault((row.emission_id, row.allocation_set_id), []).append(row)
    allocation_ok = bool(by_allocation)
    for (emission_id, allocation_set_id), rows in by_allocation.items():
        fact = facts.get(emission_id)
        weights = [row.normalized_weight for row in rows]
        allocation_ok = allocation_ok and fact is not None and bool(allocation_set_id)
        allocation_ok = allocation_ok and all(
            row.allocation_set_id == allocation_set_id
            and bool(row.allocation_basis)
            and row.raw_weight is not None
            and math.isfinite(row.raw_weight)
            and row.raw_weight >= 0.0
            and bool(row.raw_weight_unit)
            and row.normalized_weight is not None
            and math.isfinite(row.normalized_weight)
            and 0.0 <= row.normalized_weight <= 1.0
            and bool(row.recorded_for_occurrence_id)
            and bool(row.evidence_record_id)
            for row in rows
        )
        if fact is None or any(value is None for value in weights):
            allocation_ok = False
            continue
        normalized = [float(value) for value in weights if value is not None]
        consumption = canonical._nodes_by_id.get(fact.consumption_id, {})
        consumption_props = consumption.get("props", {}) if isinstance(consumption, Mapping) else {}
        unattributed_fraction = consumption_props.get("unattributedFraction")
        allocation_ok = allocation_ok and isinstance(unattributed_fraction, (int, float))
        if not isinstance(unattributed_fraction, (int, float)):
            allocation_ok = False
            continue
        unattributed_fraction = float(unattributed_fraction)
        allocation_ok = allocation_ok and math.isfinite(unattributed_fraction) and 0.0 <= unattributed_fraction <= 1.0
        raw_weights = [float(row.raw_weight) for row in rows if row.raw_weight is not None]
        raw_total = math.fsum(raw_weights)
        allocation_ok = allocation_ok and raw_total > 0.0
        allocation_ok = allocation_ok and len(
            {row.raw_weight_unit for row in rows}
        ) == 1
        allocation_ok = allocation_ok and math.isclose(
            math.fsum(normalized) + unattributed_fraction, 1.0, abs_tol=1e-9
        )
        if raw_total > 0.0:
            allocation_ok = allocation_ok and all(
                math.isclose(
                    float(row.normalized_weight),
                    (1.0 - unattributed_fraction) * float(row.raw_weight) / raw_total,
                    abs_tol=1e-9,
                )
                for row in rows
                if row.normalized_weight is not None and row.raw_weight is not None
            )
        allocation_ok = allocation_ok and all(
            math.isclose(
                row.projected_value,
                fact.emission_value * float(row.normalized_weight),
                abs_tol=1e-9,
            )
            for row in rows
        )
        allocation_ok = allocation_ok and math.isclose(
            math.fsum(row.projected_value for row in rows),
            fact.emission_value * math.fsum(normalized),
            abs_tol=1e-9,
        )
    allocation_ok = allocation_ok and {
        (
            row.emission_id,
            row.component_id,
            row.allocation_set_id,
            row.allocation_basis,
            row.raw_weight,
            row.raw_weight_unit,
            row.normalized_weight,
            row.projected_value,
        )
        for row in allocated
    } == {
        (
            "emission:allocated",
            "component:c1",
            "allocation:set:1",
            "mass",
            0.25,
            "kg",
            0.25,
            5.0,
        ),
        (
            "emission:allocated",
            "component:c2",
            "allocation:set:1",
            "mass",
            0.75,
            "kg",
            0.75,
            15.0,
        ),
    }

    source_rows = _independent_projections(context, "source_union")
    product_rows = _independent_projections(context, "product")
    all_keys = [row.key for row in (*source_rows, *product_rows)]
    occurrences = [
        row.recorded_for_occurrence_id for row in canonical.product_contributions
    ]
    allocation_evidence = [
        row.evidence_record_id for row in allocated if row.evidence_record_id
    ]
    trace_ok = (
        len(all_keys) == len(set(all_keys))
        and len(occurrences) == len(set(occurrences))
        and len(allocation_evidence) == len(set(allocation_evidence))
        and all(
            row.occurrence_id in row.evidence
            and (
                row.evidence_record_id is None
                or row.evidence_record_id in row.evidence
            )
            for row in product_rows
        )
        and all(len(row.evidence) >= 6 for row in source_rows)
    )
    process_only_ids = {fact.emission_id for fact in canonical.process_only_emissions}
    product_ids = {row.emission_id for row in canonical.product_contributions}
    source_energy_ids = {
        fact.emission_id for fact in canonical.emissions if fact.kind == "energy"
    }
    zeros = [fact for fact in canonical.emissions if fact.is_valid_zero]
    zero_ids = {fact.emission_id for fact in zeros}
    zero_product = [
        row for row in canonical.product_contributions if row.emission_id in zero_ids
    ]
    graph_record_ids = {fact.record_id for fact in canonical.emissions}
    accepted_sources = {
        (fact.source_identity, fact.source_record_id, fact.evidence_source_id)
        for fact in canonical.emissions
    }
    rejected_rows = [
        row for row in canonical.validation_rows if row["status"] == "rejected"
    ]
    rejected_ok = all(
        row["recordId"] not in graph_record_ids
        and not any(
            row[field]
            for field in ("consumptionId", "quantityId", "factorId", "emissionId")
        )
        and (
            row["sourceIdentity"],
            row["sourceRecordId"],
            row["evidenceSourceId"],
        )
        not in accepted_sources
        for row in rejected_rows
    )

    def snapshot(rows: Sequence[_Projection]) -> tuple[Any, ...]:
        grouped: dict[str, list[float]] = {}
        for row in rows:
            grouped.setdefault(row.source_kind, []).append(row.value)
        return (
            tuple(sorted(row.key for row in rows)),
            tuple(
                (key, math.fsum(sorted(values)))
                for key, values in sorted(grouped.items())
            ),
            math.fsum(row.value for row in sorted(rows, key=lambda item: item.key)),
        )

    order_ok = snapshot(source_rows) == snapshot(tuple(reversed(source_rows))) and snapshot(
        product_rows
    ) == snapshot(tuple(reversed(product_rows)))
    return {
        "allocationConserved": allocation_ok,
        "contributionAwareTraces": trace_ok,
        "processOnlyExcludedFromProducts": bool(process_only_ids)
        and process_only_ids.isdisjoint(product_ids)
        and process_only_ids <= source_energy_ids
        and process_only_ids == {"emission:process"}
        and math.isclose(
            math.fsum(facts[value].emission_value for value in process_only_ids),
            7.0,
            abs_tol=1e-9,
        ),
        "validZeroRetained": len(zeros) == 1
        and zeros[0].emission_id == "emission:zero"
        and zeros[0].quantity_value == 0.0
        and zeros[0].emission_value == 0.0
        and len(zero_product) == 1
        and zero_product[0].component_id == "component:c4"
        and zero_product[0].projected_value == 0.0,
        "rejectedNonMaterialization": bool(rejected_rows) and rejected_ok,
        "graphOrderInvariant": order_ok,
    }


def check_compatibility(
    release_dir: Path | str, benchmark_path: Path | str
) -> CompatibilityReport:
    findings: list[CompatibilityFinding] = []
    try:
        release = Path(release_dir)
        sidecar_path = Path(benchmark_path)
        release_blobs = _preflight_release(release)
        sidecar = _strict_json(sidecar_path.read_bytes(), "compatibility sidecar")
        if set(sidecar) != {
            "schemaVersion",
            "benchmarkProfile",
            "releaseBinding",
            "sourceBenchmark",
            "expectedTotals",
            "cases",
        }:
            raise _Unreadable("compatibility sidecar keys are invalid")
        if sidecar.get("schemaVersion") != "m23-canonical-v2" or sidecar.get("benchmarkProfile") != "controlled-task11-v2":
            raise _Unreadable("compatibility sidecar profile is invalid")
        binding = sidecar.get("releaseBinding")
        source = sidecar.get("sourceBenchmark")
        expected_totals = sidecar.get("expectedTotals")
        cases = sidecar.get("cases")
        if not isinstance(binding, Mapping) or not isinstance(source, Mapping) or not isinstance(expected_totals, Mapping) or not isinstance(cases, list):
            raise _Unreadable("compatibility sidecar has an invalid shape")
        if set(binding) != {
            "releaseId",
            "manifestSha256",
            "rawGraphSha256",
            "semanticGraphSha256",
        }:
            raise _Unreadable("releaseBinding keys are invalid")
        try:
            context = load_m3_execution_context(release)
        except CanonicalSchemaError as exc:
            return CompatibilityReport(
                exit_code=1,
                status="mismatch",
                findings=(CompatibilityFinding("release_canonical_mismatch", str(exc)),),
                totals=MappingProxyType({}),
                checks=MappingProxyType({}),
            )
        manifest_bytes = release_blobs["case_version_manifest.json"]
        graph_bytes = release_blobs["multigranular_carbon_kg.json"]
        if context.canonical.manifest.get("releaseId") != _CONTROLLED_RELEASE_ID:
            findings.append(
                CompatibilityFinding("controlled_profile_mismatch", "release id")
            )
        if context.release_profile != _CONTROLLED_RELEASE_PROFILE or not context.controlled_fixture:
            findings.append(
                CompatibilityFinding("controlled_profile_mismatch", "release profile")
            )
        if binding.get("releaseId") != context.canonical.manifest["releaseId"]:
            findings.append(CompatibilityFinding("release_id_mismatch", "release id"))
        if binding.get("manifestSha256") != _sha(manifest_bytes):
            findings.append(
                CompatibilityFinding("release_manifest_hash_mismatch", "manifest hash")
            )
        if binding.get("rawGraphSha256") != _sha(graph_bytes):
            findings.append(CompatibilityFinding("raw_graph_hash_mismatch", "graph hash"))
        if binding.get("semanticGraphSha256") != _semantic_graph_hash(release):
            findings.append(
                CompatibilityFinding("semantic_graph_hash_mismatch", "semantic graph hash")
            )
        source_bundle = _source_bundle(sidecar_path, source, findings)
        if source_bundle is not None:
            _check_source_truth(context, source_bundle[1], findings)
        if not _controlled_validation_ok(context):
            findings.append(
                CompatibilityFinding(
                    "controlled_validation_mismatch", "validation signature"
                )
            )
        if not _same(expected_totals, _EXPECTED_TOTALS):
            findings.append(
                CompatibilityFinding("benchmark_contract_mismatch", "expected totals")
            )
        totals, component_totals, dimension_totals = _totals(context)
        for key, actual in totals.items():
            if not _same(actual, _EXPECTED_TOTALS[key]) or not _same(
                actual, expected_totals.get(key)
            ):
                findings.append(CompatibilityFinding("expected_total_mismatch", key))
        if not _same(component_totals, _EXPECTED_TOTALS["components"]) or not _same(
            component_totals, expected_totals.get("components")
        ):
            findings.append(CompatibilityFinding("expected_component_total_mismatch", "components"))
        for dimension, actual in dimension_totals.items():
            if not _same(actual, _EXPECTED_TOTALS[dimension]) or not _same(
                actual, expected_totals.get(dimension)
            ):
                findings.append(
                    CompatibilityFinding("expected_dimension_total_mismatch", dimension)
                )
        case_ids = tuple(
            str(case.get("caseId")) if isinstance(case, Mapping) else ""
            for case in cases
        )
        expected_case_ids = tuple(row[0] for row in _EXPECTED_COMPAT_CASES)
        if case_ids != expected_case_ids:
            findings.append(
                CompatibilityFinding("compatibility_case_contract_mismatch", "case ids/order")
            )
        for index, expected_case in enumerate(_EXPECTED_COMPAT_CASES):
            if index >= len(cases):
                continue
            case = cases[index]
            if not isinstance(case, Mapping) or not isinstance(case.get("program"), Mapping):
                raise _Unreadable("compatibility case is invalid")
            case_id = str(case.get("caseId"))
            expected_id, expected_program, expected_selected, expected_status = expected_case
            selected = tuple(case.get("selectedComponentIds") or ())
            if (
                case_id != expected_id
                or not _same(case["program"], expected_program)
                or selected != expected_selected
                or case.get("expectedM3Status") != expected_status
            ):
                findings.append(
                    CompatibilityFinding("compatibility_case_contract_mismatch", expected_id)
                )
            observed = _independent_execute(
                context,
                expected_program,
                expected_selected,
            )
            if not _same(observed, case.get("executorOutput")):
                findings.append(CompatibilityFinding("executor_output_mismatch", expected_id))
            observed_status = _m3_status(context, expected_program, observed)
            if observed_status != expected_status or observed_status != case.get(
                "expectedM3Status"
            ):
                findings.append(CompatibilityFinding("expected_status_mismatch", expected_id))
        checks = _invariant_checks(context)
        for key, value in checks.items():
            if not value:
                findings.append(CompatibilityFinding("invariant_mismatch", key))
        findings = sorted(findings, key=lambda row: (row.code, row.message))
        return CompatibilityReport(
            exit_code=0 if not findings else 1,
            status="clean" if not findings else "mismatch",
            findings=tuple(findings),
            totals=MappingProxyType(totals),
            checks=MappingProxyType(checks),
        )
    except (OSError, _Unreadable, UnicodeError, json.JSONDecodeError) as exc:
        finding = CompatibilityFinding("unreadable_input", str(exc))
        return CompatibilityReport(
            exit_code=2,
            status="unreadable",
            findings=(finding,),
            totals=MappingProxyType({}),
            checks=MappingProxyType({}),
        )
    except (ArithmeticError, AssertionError, KeyError, StopIteration, TypeError, ValueError) as exc:
        return CompatibilityReport(
            exit_code=1,
            status="mismatch",
            findings=(CompatibilityFinding("semantic_evaluation_mismatch", type(exc).__name__),),
            totals=MappingProxyType({}),
            checks=MappingProxyType({}),
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    args = parser.parse_args(argv)
    report = check_compatibility(args.release_dir, args.benchmark)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CompatibilityFinding",
    "CompatibilityReport",
    "check_compatibility",
    "main",
]
