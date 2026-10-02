"""Deterministic CarbonQL execution over the sealed canonical-v2 projection API."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalSchemaError,
    CanonicalV2Context,
    EmissionFact,
    ProductContribution,
    component_type_ids_for_component,
    components_for_ifc_class,
    dimension_ids,
    evidence_ids,
    graph_document_from_context,
    iter_emissions,
    iter_graph_edges,
    iter_product_contributions,
    load_canonical_v2_context,
    lookup_component,
    lookup_dimension,
    lookup_process,
)
from dm2c_carbonql import (
    CarbonQLProgram,
    ENTITY_DIMENSIONS,
    GraphSchema,
    ProgramHole,
    _dimension_keys,
    derive_projection_perspective,
    validate_program,
)


@dataclass(frozen=True, slots=True)
class _Projection:
    projection_key: tuple[str, ...]
    emission_id: str
    consumption_id: str
    source_kind: str
    value: float
    component_id: str | None
    dimensions: Mapping[str, str | None]
    evidence: tuple[str, ...]
    recorded_for_occurrence_id: str | None
    evidence_record_id: str | None


@dataclass(frozen=True, slots=True)
class _ComponentResolution:
    resolved: tuple[str, ...]
    missing: tuple[str, ...] = ()
    invalid_cardinality: bool = False
    selector_conflict: bool = False

    @property
    def unresolved(self) -> bool:
        return bool(
            self.missing
            or self.invalid_cardinality
            or self.selector_conflict
            or not self.resolved
        )

    @property
    def requested(self) -> tuple[str, ...]:
        return self.resolved + self.missing


@dataclass(frozen=True, slots=True)
class _EntityResolution:
    entity_type: str
    resolved: tuple[str, ...]
    unresolved: bool


@dataclass(frozen=True, slots=True)
class _FilterResolution:
    field: str
    allowed: tuple[str, ...]
    unsatisfiable: bool = False


_ENTITY_NAME_FIELDS = {f"{dimension}_name": dimension for dimension in ENTITY_DIMENSIONS}
_CARRIER_NAME_CACHE: dict[int, Mapping[str, str | None]] = {}
_STAGE_BY_ACTIVITY_CACHE: dict[int, Mapping[str, str | None]] = {}


@dataclass(frozen=True, slots=True)
class QueryExecution:
    status: str
    rows: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    emission_ids: tuple[str, ...]
    projection_keys: tuple[tuple[str, ...], ...]
    trace_rows: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    holes: tuple[ProgramHole, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rows": [_plain(row) for row in self.rows],
            "summary": _plain(self.summary),
            "emission_ids": list(self.emission_ids),
            "projection_keys": [list(key) for key in self.projection_keys],
            "trace_rows": [_plain(row) for row in self.trace_rows],
            "coverage": _plain(self.coverage),
            "holes": [hole.to_dict() for hole in self.holes],
        }


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _props(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("props", {})
    return value if isinstance(value, Mapping) else MappingProxyType({})


def _source_kind(fact: EmissionFact) -> str:
    return "material" if fact.kind == "material" else "process"


def _fact_index(context: CanonicalV2Context) -> dict[str, EmissionFact]:
    return {fact.emission_id: fact for fact in iter_emissions(context)}


def _component_dimensions(context: CanonicalV2Context, component_id: str) -> dict[str, str | None]:
    if component_id in dimension_ids(context, "module"):
        return {
            "component": component_id,
            "component_type": None,
            "ifc_class": "ModularUnit",
        }
    node = lookup_component(context, component_id)
    props = _props(node)
    type_ids = component_type_ids_for_component(context, component_id)
    return {
        "component": component_id,
        "component_type": type_ids[0] if len(type_ids) == 1 else None,
        "ifc_class": str(props.get("ifcClass")) if props.get("ifcClass") is not None else None,
    }


def _stage_by_activity(context: CanonicalV2Context) -> Mapping[str, str | None]:
    """Map each activity to the production stage that owns it.

    Energy is recorded against the activity that consumed it, so the stage sits
    one hop up the production structure rather than on the record itself. An
    activity claimed by more than one stage has no unambiguous owner and stays
    unresolved instead of being assigned to an arbitrary one.
    """
    key = id(context)
    cached = _STAGE_BY_ACTIVITY_CACHE.get(key)
    if cached is not None:
        return cached
    owners: dict[str, set[str]] = {}
    for edge in iter_graph_edges(graph_document_from_context(context)):
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


def _process_dimensions(context: CanonicalV2Context, fact: EmissionFact) -> dict[str, str | None]:
    stage_id: str | None = None
    process_id: str | None = None
    for entity_id in fact.process_ids:
        labels = tuple(lookup_process(context, entity_id).get("labels", ()))
        if "ProductionStage" in labels:
            stage_id = entity_id
        elif process_id is None:
            process_id = entity_id
    if stage_id is None and process_id is not None:
        stage_id = _stage_by_activity(context).get(process_id)
    return {
        "process": process_id,
        "stage": stage_id,
        "resource": fact.resource_ids[0] if len(fact.resource_ids) == 1 else None,
    }


def _entity_ids(context: CanonicalV2Context, dimension: str) -> tuple[str, ...]:
    if dimension == "stage":
        return tuple(
            entity_id
            for entity_id in dimension_ids(context, "process")
            if "ProductionStage"
            in tuple(lookup_dimension(context, "process", entity_id).get("labels", ()))
        )
    return dimension_ids(context, dimension)


def _entity_node(
    context: CanonicalV2Context, dimension: str, entity_id: str
) -> Mapping[str, Any] | None:
    if dimension == "ifc_class":
        return None
    lookup_dimension_name = "process" if dimension == "stage" else dimension
    return lookup_dimension(context, lookup_dimension_name, entity_id)


def _carrier_names(context: CanonicalV2Context) -> Mapping[str, str | None]:
    key = id(context)
    cached = _CARRIER_NAME_CACHE.get(key)
    if cached is not None:
        return cached
    names: dict[str, list[str]] = {}
    for fact in iter_emissions(context):
        if not fact.carrier_id:
            continue
        for process_id in fact.process_ids:
            process_props = _props(lookup_process(context, process_id))
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


def _readable_name(
    context: CanonicalV2Context, dimension: str, entity_id: str | None
) -> str | None:
    if entity_id is None:
        return None
    if dimension == "ifc_class":
        return entity_id if entity_id in dimension_ids(context, "ifc_class") else None
    if entity_id not in set(_entity_ids(context, dimension)):
        return None
    node = _entity_node(context, dimension, entity_id)
    props = _props(node or {})
    value = props.get("name")
    if value is None and dimension == "process":
        value = props.get("activityName") or props.get("templateName")
    if value is None and dimension == "stage":
        value = props.get("stageName") or props.get("name")
    if value is None and dimension == "carrier":
        value = _carrier_names(context).get(entity_id)
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _with_readable_names(
    context: CanonicalV2Context, values: dict[str, str | None]
) -> Mapping[str, str | None]:
    for dimension in ENTITY_DIMENSIONS:
        values[f"{dimension}_name"] = _readable_name(
            context, dimension, values.get(dimension)
        )
    return MappingProxyType(values)


def _dimensions(
    context: CanonicalV2Context,
    fact: EmissionFact,
    contribution: ProductContribution | None,
) -> Mapping[str, str | None]:
    values: dict[str, str | None] = {
        "project": str(context.manifest["releaseId"]),
        "module": contribution.module_id if contribution else None,
        "component_type": None,
        "component": contribution.component_id if contribution else None,
        "ifc_class": None,
        "material": fact.material_id,
        "carrier": fact.carrier_id,
        "factor_keyword": fact.factor_keyword,
        "factor_source": fact.factor_source,
        "process": None,
        "stage": None,
        "resource": None,
        "source_kind": _source_kind(fact),
    }
    if contribution is not None:
        values.update(_component_dimensions(context, contribution.component_id))
    values.update(_process_dimensions(context, fact))
    return _with_readable_names(context, values)


def _product_projections(context: CanonicalV2Context) -> tuple[_Projection, ...]:
    facts = _fact_index(context)
    return tuple(
        _Projection(
            projection_key=item.key,
            emission_id=item.emission_id,
            consumption_id=item.consumption_id,
            source_kind="material" if item.mode == "material" else "process",
            value=item.projected_value,
            component_id=item.component_id,
            dimensions=_dimensions(context, facts[item.emission_id], item),
            evidence=evidence_ids(context, item.emission_id, item.key),
            recorded_for_occurrence_id=item.recorded_for_occurrence_id,
            evidence_record_id=item.evidence_record_id,
        )
        for item in iter_product_contributions(context)
    )


def _source_projections(
    context: CanonicalV2Context, *, kind: str | None = None
) -> tuple[_Projection, ...]:
    return tuple(
        _Projection(
            projection_key=("source", fact.emission_id),
            emission_id=fact.emission_id,
            consumption_id=fact.consumption_id,
            source_kind=_source_kind(fact),
            value=fact.emission_value,
            component_id=None,
            dimensions=_dimensions(context, fact, None),
            evidence=evidence_ids(context, fact.emission_id),
            recorded_for_occurrence_id=None,
            evidence_record_id=None,
        )
        for fact in iter_emissions(context, kind=kind)
    )


def _perspective(program: CarbonQLProgram) -> str:
    return derive_projection_perspective(program)


def _requested_sources(program: CarbonQLProgram) -> set[str]:
    values: set[str] = set()
    for step in program.steps:
        if step.op != "CarbonAtoms":
            continue
        raw = step.args.get("source")
        items = (raw,) if isinstance(raw, str) else tuple(raw or ())
        if "all" in items:
            values.update(("material", "process"))
        else:
            values.update(str(item) for item in items)
    return values


def _program_requests_process(program: CarbonQLProgram) -> bool:
    return "process" in _requested_sources(program)


def _synthetic_energy_stamp(
    context: CanonicalV2Context | None, involves_process: bool
) -> dict[str, Any]:
    if context is not None and context.synthetic_energy and involves_process:
        return {
            "synthetic_energy": True,
            "synthetic_energy_provenance": "controlled-fixture synthetic factory input",
        }
    return {}


def _resolve_component_ids(
    context: CanonicalV2Context,
    selector: Mapping[str, Any],
    selected_component_ids: Sequence[str],
) -> _ComponentResolution | None:
    op = selector["op"]
    if op == "SelectProject":
        return None
    if op == "SelectClicked":
        program_ids = tuple(selector.get("ids") or ())
        runtime_ids = tuple(selected_component_ids)
        program = (
            _canonical_product_entity_ids(context, program_ids)
            if program_ids
            else None
        )
        runtime = (
            _canonical_product_entity_ids(context, runtime_ids)
            if runtime_ids
            else None
        )
        if program is not None and runtime is not None:
            if (
                program.unresolved
                or runtime.unresolved
                or set(program.resolved) != set(runtime.resolved)
            ):
                resolved = tuple(dict.fromkeys(program.resolved + runtime.resolved))
                missing = tuple(dict.fromkeys(program.missing + runtime.missing))
                return _ComponentResolution(
                    resolved=resolved,
                    missing=missing,
                    selector_conflict=True,
                )
            return program
        return program or runtime or _ComponentResolution(())
    elif selector.get("entity_type", "component") in {"component", "module"}:
        entity_type = str(selector.get("entity_type") or "component")
        raw_ids = tuple(selector.get("ids") or ())
        if raw_ids:
            resolution = (
                _canonical_component_ids(context, raw_ids)
                if entity_type == "component"
                else _canonical_module_ids(context, raw_ids)
            )
            return _ComponentResolution(
                resolved=resolution.resolved,
                missing=resolution.missing,
                invalid_cardinality=(
                    str(selector.get("cardinality") or "singleton") == "singleton"
                    and len(resolution.resolved) != 1
                ),
            )
        elif entity_type == "component" and selector.get("property", "name") == "ifcClass":
            try:
                requested = components_for_ifc_class(context, str(selector.get("value") or ""))
            except CanonicalSchemaError:
                return _ComponentResolution(())
        else:
            property_name = str(selector.get("property") or "name")
            value = str(selector.get("value") or "")
            requested = tuple(
                entity_id
                for entity_id in dimension_ids(context, entity_type)
                if str(
                    _props(lookup_dimension(context, entity_type, entity_id)).get(
                        property_name, ""
                    )
                )
                == value
            )
    else:
        return None

    resolution = (
        _canonical_component_ids(context, requested)
        if entity_type == "component"
        else _canonical_module_ids(context, requested)
    )
    return _ComponentResolution(
        resolved=resolution.resolved,
        missing=resolution.missing,
        invalid_cardinality=(
            op == "ResolveEntities"
            and str(selector.get("cardinality") or "singleton") == "singleton"
            and len(resolution.resolved) != 1
        ),
    )


def _canonical_component_ids(
    context: CanonicalV2Context, requested: Sequence[str]
) -> _ComponentResolution:
    resolved: list[str] = []
    by_global_id = {}
    for entity_id in dimension_ids(context, "component"):
        global_id = str(_props(lookup_component(context, entity_id)).get("globalId") or "")
        if global_id:
            by_global_id[global_id] = entity_id
    available = set(dimension_ids(context, "component"))
    missing: list[str] = []
    for value in requested:
        token = str(value).strip()
        entity_id = token if token in available else by_global_id.get(token)
        if entity_id and entity_id not in resolved:
            resolved.append(entity_id)
        elif entity_id is None and token not in missing:
            missing.append(token)
    return _ComponentResolution(
        resolved=tuple(resolved),
        missing=tuple(missing),
    )


def _canonical_module_ids(
    context: CanonicalV2Context, requested: Sequence[str]
) -> _ComponentResolution:
    available = set(dimension_ids(context, "module"))
    resolved: list[str] = []
    missing: list[str] = []
    for value in requested:
        token = str(value).strip()
        if token in available and token not in resolved:
            resolved.append(token)
        elif token not in available and token not in missing:
            missing.append(token)
    return _ComponentResolution(
        resolved=tuple(resolved),
        missing=tuple(missing),
    )


def _canonical_product_entity_ids(
    context: CanonicalV2Context, requested: Sequence[str]
) -> _ComponentResolution:
    components = _canonical_component_ids(context, requested)
    modules = _canonical_module_ids(context, requested)
    return _ComponentResolution(
        resolved=tuple(dict.fromkeys((*components.resolved, *modules.resolved))),
        missing=tuple(value for value in components.missing if value not in modules.resolved),
    )


def _selector_has_unresolved_target(
    program: CarbonQLProgram, selector: Mapping[str, Any]
) -> bool:
    return bool(
        selector.get("op") == "ResolveEntities"
        and selector.get("entity_type") == "?"
        and any(hole.dimension == "target" for hole in program.holes)
    )


def _entity_filter(
    context: CanonicalV2Context, selector: Mapping[str, Any]
) -> _EntityResolution | None:
    if selector["op"] != "ResolveEntities":
        return None
    entity_type = str(selector.get("entity_type") or "component")
    if entity_type in {"component", "module"}:
        return None
    ids = tuple(str(value) for value in (selector.get("ids") or ()))
    dimension = "material" if entity_type == "material" else "process"
    if ids:
        available = set(dimension_ids(context, dimension))
        matches = tuple(dict.fromkeys(value for value in ids if value in available))
        missing = tuple(dict.fromkeys(value for value in ids if value not in available))
    else:
        prop = str(selector.get("property") or "name")
        value = str(selector.get("value") or "")
        matches = tuple(
            entity_id
            for entity_id in dimension_ids(context, dimension)
            if str(_props(lookup_dimension(context, dimension, entity_id)).get(prop, "")) == value
        )
        missing = ()
    cardinality = str(selector.get("cardinality") or "singleton")
    unresolved = bool(
        missing
        or not matches
        or (cardinality == "singleton" and len(matches) != 1)
    )
    return _EntityResolution(entity_type, matches, unresolved)


def _filter_values(step: Mapping[str, Any]) -> tuple[str, ...]:
    raw = (step["equals"],) if "equals" in step else tuple(step["in"])
    return tuple(str(value) for value in raw)


# Trailing role nouns that users/LLMs append to an entity name (e.g. "steel
# frame welding stage") but that are not part of the stored readable name.
_NAME_ROLE_SUFFIXES = (
    "stage",
    "process",
    "activity",
    "phase",
    "step",
    "operation",
    "material",
)


def _normalize_entity_name(value: str) -> str:
    text = str(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    for suffix in _NAME_ROLE_SUFFIXES:
        if text.endswith(" " + suffix):
            text = text[: -(len(suffix) + 1)].strip()
    return re.sub(r"\s+", " ", text)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = math.fsum(x * y for x, y in zip(a, b))
    na = math.sqrt(math.fsum(x * x for x in a))
    nb = math.sqrt(math.fsum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingNameMatcher:
    """Optional semantic fallback for entity-name resolution.

    When the deterministic lexical tiers cannot resolve a free-text entity
    reference, this matcher embeds the query and every candidate readable name
    and returns the highest-cosine candidate id. A minimum ``floor`` guards
    against mapping wholly unrelated text onto an entity; above the floor the
    single best candidate is adopted (per the "auto-adopt closest" policy),
    which trades the fail-closed guarantee for recall on paraphrased names.

    Candidate vectors are cached by readable name so a small, fixed vocabulary
    (stages/materials) is embedded at most once per process.
    """

    def __init__(
        self,
        embed_fn,
        *,
        floor: float = 0.60,
        margin: float = 0.0,
    ) -> None:
        self._embed = embed_fn
        self._floor = floor
        self._margin = margin
        self._cache: dict[str, list[float]] = {}

    def _vectors(self, names: Sequence[str]) -> list[list[float]]:
        missing = [name for name in names if name not in self._cache]
        if missing:
            for name, vec in zip(missing, self._embed(missing)):
                self._cache[name] = list(vec)
        return [self._cache[name] for name in names]

    def __call__(
        self, dimension: str, value: str, candidates: Sequence[tuple[str, str]]
    ) -> str | None:
        pairs = [(cid, name) for cid, name in candidates if name]
        if not value.strip() or not pairs:
            return None
        names = [name for _, name in pairs]
        try:
            query_vec = list(self._embed([value])[0])
            candidate_vecs = self._vectors(names)
        except Exception:
            return None
        scored = sorted(
            (
                (_cosine(query_vec, vec), cid)
                for (cid, _name), vec in zip(pairs, candidate_vecs)
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best_id = scored[0]
        if best_score < self._floor:
            return None
        if len(scored) > 1 and (best_score - scored[1][0]) < self._margin:
            return None
        return best_id


def build_embedding_name_matcher(
    embedding_client: Any,
    embedding_model: str,
    *,
    floor: float = 0.60,
    margin: float = 0.0,
) -> EmbeddingNameMatcher | None:
    """Adapt an OpenAI-compatible ``embed(inputs, model)`` client into a name
    matcher, or return ``None`` when embeddings are not configured."""

    if embedding_client is None or not embedding_model:
        return None
    embed_method = getattr(embedding_client, "embed", None)
    if not callable(embed_method):
        return None

    def embed_fn(texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return embed_method(list(texts), embedding_model)

    return EmbeddingNameMatcher(embed_fn, floor=floor, margin=margin)


def _match_entity_name(
    context: CanonicalV2Context,
    dimension: str,
    available: tuple[str, ...],
    value: str,
    matcher: "EmbeddingNameMatcher | None" = None,
) -> tuple[str, ...]:
    """Resolve a free-text ``value`` to entity ids using progressively looser
    tiers, but only ever return a match when exactly one candidate survives at
    that tier. Genuine ambiguity still falls through to an empty result so the
    caller can fail closed instead of guessing a wrong number.

    When every lexical tier is inconclusive and an optional semantic ``matcher``
    is supplied, the closest candidate by embedding similarity is adopted."""

    exact = tuple(
        entity_id
        for entity_id in available
        if _readable_name(context, dimension, entity_id) == value
    )
    if exact:
        return exact

    target = _normalize_entity_name(value)
    if not target:
        return ()
    readable = {
        entity_id: _readable_name(context, dimension, entity_id) or ""
        for entity_id in available
    }
    normalized = {
        entity_id: _normalize_entity_name(name) for entity_id, name in readable.items()
    }
    equal = tuple(eid for eid, norm in normalized.items() if norm and norm == target)
    if len(equal) == 1:
        return equal

    substring = tuple(
        eid
        for eid, norm in normalized.items()
        if norm and (target in norm or norm in target)
    )
    if len(substring) == 1:
        return substring

    if matcher is not None:
        candidates = tuple(
            (entity_id, name) for entity_id, name in readable.items() if name
        )
        chosen = matcher(dimension, value, candidates)
        if chosen is not None:
            return (chosen,)
    return ()


def _resolve_entity_filter_values(
    context: CanonicalV2Context,
    field: str,
    values: tuple[str, ...],
    matcher: "EmbeddingNameMatcher | None" = None,
) -> _FilterResolution:
    by_name = field in _ENTITY_NAME_FIELDS
    dimension = _ENTITY_NAME_FIELDS[field] if by_name else field
    available = tuple(_entity_ids(context, dimension))
    resolved: list[str] = []
    for value in values:
        if not by_name and value in available:
            if value not in resolved:
                resolved.append(value)
            continue
        matches = _match_entity_name(context, dimension, available, value, matcher)
        if len(matches) != 1:
            return _FilterResolution(field, (), unsatisfiable=True)
        target = _readable_name(context, dimension, matches[0]) if by_name else matches[0]
        if target is not None and target not in resolved:
            resolved.append(target)
    return _FilterResolution(field, tuple(resolved))


def _resolve_filter(
    context: CanonicalV2Context,
    step: Mapping[str, Any],
    matcher: "EmbeddingNameMatcher | None" = None,
) -> _FilterResolution:
    field = str(step["field"])
    values = _filter_values(step)
    if field in ENTITY_DIMENSIONS or field in _ENTITY_NAME_FIELDS:
        return _resolve_entity_filter_values(context, field, values, matcher)
    return _FilterResolution(field, tuple(dict.fromkeys(values)))


def _group_result_row(
    group_keys: tuple[str, ...], group: tuple[str | None, ...], items: Sequence[_Projection]
) -> dict[str, Any]:
    row: dict[str, Any] = {key: value for key, value in zip(group_keys, group)}
    for key in group_keys:
        if key in ENTITY_DIMENSIONS:
            name_key = f"{key}_name"
            names = {
                item.dimensions.get(name_key)
                for item in items
                if item.dimensions.get(name_key) is not None
            }
            row[name_key] = next(iter(names)) if len(names) == 1 else None
    row["kgCO2e"] = math.fsum(item.value for item in items)
    return row


def _coverage(
    context: CanonicalV2Context,
    projections: Sequence[_Projection],
    requested_components: tuple[str, ...] | None,
    *,
    involves_process: bool = False,
) -> Mapping[str, Any]:
    covered = tuple(sorted({item.component_id for item in projections if item.component_id}))
    requested = tuple(requested_components or ())
    rejected = tuple(row for row in context.validation_rows if row["status"] == "rejected")
    payload = {
        "accepted_projection_count": len(projections),
        "accepted_emission_count": len({item.emission_id for item in projections}),
        "rejected_count": len(rejected),
        "requested_component_count": len(requested),
        "covered_component_ids": covered,
        "missing_component_ids": tuple(value for value in requested if value not in covered),
    }
    payload.update(_synthetic_energy_stamp(context, involves_process))
    return MappingProxyType(payload)


def _empty(
    status: str,
    holes: tuple[ProgramHole, ...] = (),
    *,
    context: CanonicalV2Context | None = None,
    requested_components: tuple[str, ...] = (),
    covered_components: tuple[str, ...] = (),
    missing_components: tuple[str, ...] | None = None,
    involves_process: bool = False,
) -> QueryExecution:
    rejected_count = (
        sum(row["status"] == "rejected" for row in context.validation_rows)
        if context is not None
        else 0
    )
    coverage = {
        "accepted_projection_count": 0,
        "accepted_emission_count": 0,
        "rejected_count": rejected_count,
        "requested_component_count": len(requested_components),
        "covered_component_ids": covered_components,
        "missing_component_ids": (
            requested_components
            if missing_components is None
            else missing_components
        ),
    }
    coverage.update(_synthetic_energy_stamp(context, involves_process))
    return QueryExecution(
        status=status,
        rows=(),
        summary=MappingProxyType(_synthetic_energy_stamp(context, involves_process)),
        emission_ids=(),
        projection_keys=(),
        trace_rows=(),
        coverage=MappingProxyType(coverage),
        holes=holes,
    )


class CarbonQLExecutor:
    def __init__(
        self,
        context: CanonicalV2Context,
        *,
        name_matcher: "EmbeddingNameMatcher | None" = None,
    ):
        self.context = context
        self.schema = GraphSchema.from_context(context)
        self.name_matcher = name_matcher

    @classmethod
    def from_context(
        cls,
        context: CanonicalV2Context,
        *,
        allow_synthetic: bool | None = None,
        name_matcher: "EmbeddingNameMatcher | None" = None,
    ) -> "CarbonQLExecutor":
        if not isinstance(context, CanonicalV2Context):
            raise TypeError("context must be a CanonicalV2Context")
        if allow_synthetic is False and context.synthetic_energy:
            raise CanonicalSchemaError("syntheticFactoryInputsUsed must be false")
        return cls(context, name_matcher=name_matcher)

    @classmethod
    def from_release(
        cls, release_dir: Path | str, *, allow_synthetic: bool = False
    ) -> "CarbonQLExecutor":
        return cls.from_context(
            load_canonical_v2_context(release_dir, allow_synthetic=allow_synthetic),
            allow_synthetic=allow_synthetic,
        )

    def execute(
        self, program: CarbonQLProgram, selected_component_ids: Sequence[str] = ()
    ) -> QueryExecution:
        validation = validate_program(program, self.schema)
        requested_process = _program_requests_process(program)
        selector = program.steps[0].to_dict()
        unresolved_selector = _selector_has_unresolved_target(program, selector)
        component_resolution = (
            None
            if unresolved_selector
            else _resolve_component_ids(
                self.context, selector, selected_component_ids
            )
        )
        if component_resolution is not None and component_resolution.unresolved:
            return _empty(
                "unresolved_target",
                context=self.context,
                requested_components=component_resolution.requested,
                covered_components=component_resolution.resolved,
                missing_components=component_resolution.missing,
                involves_process=requested_process,
            )
        requested_components = (
            component_resolution.resolved if component_resolution is not None else None
        )
        entity_filter = (
            None if unresolved_selector else _entity_filter(self.context, selector)
        )
        if entity_filter and entity_filter.unresolved:
            return _empty(
                "unresolved_target",
                context=self.context,
                involves_process=requested_process,
            )
        if not validation.executable:
            return _empty(
                validation.compiler_status,
                program.holes,
                context=self.context,
                involves_process=requested_process,
            )

        perspective = _perspective(program)
        sources = _requested_sources(program)
        involves_process = "process" in sources
        # Entity scope, not perspective, decides whether records must carry product
        # attribution: a scope naming product objects can only collect records
        # attributable to them, whatever dimensions the result is organized along,
        # while a scope naming materials or processes reads each record once.
        if requested_components is not None:
            projections = list(_product_projections(self.context))
        elif entity_filter is not None:
            projections = list(_source_projections(self.context))
        elif perspective == "product":
            # Grouping along a product key legitimately reads process atoms
            # through the product projection: the question asks what each
            # object carries. Without any organizing dimension there is no such
            # question, so falling back to the product projection would drop
            # every record no product carries and report the remainder as the
            # process total.
            assert _dimension_keys(program) or sources != {"process"}, (
                "an unorganized process-only program must not read the product "
                "projection: records that no product carries would be dropped "
                "without being reported"
            )
            projections = list(_product_projections(self.context))
        elif perspective == "material_source":
            projections = list(_source_projections(self.context, kind="material"))
        elif perspective == "energy_source":
            projections = list(_source_projections(self.context, kind="energy"))
        else:
            projections = list(_source_projections(self.context))
        projections = [item for item in projections if item.source_kind in sources]

        if requested_components is not None:
            projections = [
                item for item in projections if item.component_id in requested_components
            ]
        if entity_filter:
            field, values = entity_filter.entity_type, entity_filter.resolved
            if field == "process":
                projections = [
                    item
                    for item in projections
                    if item.dimensions.get("process") in values
                    or item.dimensions.get("stage") in values
                ]
            else:
                projections = [
                    item for item in projections if item.dimensions.get(field) in values
                ]

        for step in program.steps:
            if step.op == "Filter":
                resolution = _resolve_filter(self.context, step.args, self.name_matcher)
                if resolution.unsatisfiable:
                    return _empty(
                        "unsatisfiable_filter",
                        context=self.context,
                        requested_components=tuple(requested_components or ()),
                        involves_process=involves_process,
                    )
                # An unresolvable entity literal is blocked above. If this
                # resolved filter still leaves no projections, the zero is an
                # evidenced empty slice, not a fail-open typo.
                allowed = set(resolution.allowed)
                projections = [
                    item
                    for item in projections
                    if item.dimensions.get(resolution.field) in allowed
                ]
            elif step.op == "JoinByAttribution":
                required = set(step.args["required_sources"])
                by_component: dict[str, set[str]] = {}
                for item in _product_projections(self.context):
                    if item.component_id:
                        by_component.setdefault(item.component_id, set()).add(item.source_kind)
                eligible = {key for key, kinds in by_component.items() if required <= kinds}
                projections = [item for item in projections if item.component_id in eligible]

        group_keys = next(
            (tuple(step.args["keys"]) for step in program.steps if step.op == "GroupBy"),
            (),
        )
        if group_keys:
            grouped: dict[tuple[str | None, ...], list[_Projection]] = {}
            for item in projections:
                grouped.setdefault(
                    tuple(item.dimensions.get(key) for key in group_keys), []
                ).append(item)
            paired: list[tuple[dict[str, Any], tuple[str | None, ...] | None]] = [
                (_group_result_row(group_keys, group, items), group)
                for group, items in sorted(grouped.items(), key=lambda row: tuple(str(v or "") for v in row[0]))
            ]
        else:
            paired = [
                ({"kgCO2e": math.fsum(item.value for item in projections)}, None)
            ]

        ranked = False
        for step in program.steps:
            if step.op == "Rank":
                paired.sort(
                    key=lambda entry: entry[0]["kgCO2e"],
                    reverse=bool(step.args.get("descending", True)),
                )
                paired = paired[: int(step.args.get("top_k", 10))]
                ranked = True
            elif step.op == "Compare":
                paired.sort(key=lambda entry: entry[0]["kgCO2e"], reverse=True)

        rows = [row for row, _ in paired]
        trace_requested = any(step.op == "Trace" for step in program.steps)

        # A bare Rank is an ordering view over the whole slice, so its total
        # stays the slice total. Tracing after a Rank instead asks for the
        # evidence behind the ranked rows, so the atoms, total and evidence
        # must shrink to them; otherwise explaining the largest record cites
        # and sums everything.
        if ranked and trace_requested and group_keys:
            surviving = {group for _, group in paired if group is not None}
            projections = [
                item
                for item in projections
                if tuple(item.dimensions.get(key) for key in group_keys) in surviving
            ]

        ordered = tuple(sorted(projections, key=lambda item: item.projection_key))
        trace_rows = tuple(
            MappingProxyType(
                {
                    "projection_key": item.projection_key,
                    "emission_id": item.emission_id,
                    "consumption_id": item.consumption_id,
                    "source_kind": item.source_kind,
                    "value": item.value,
                    "component_id": item.component_id,
                    "evidence_ids": item.evidence,
                    "recorded_for_occurrence_id": item.recorded_for_occurrence_id,
                    "evidence_record_id": item.evidence_record_id,
                }
            )
            for item in ordered
        ) if trace_requested else ()
        return QueryExecution(
            status="ok",
            rows=tuple(MappingProxyType(row) for row in rows),
            summary=MappingProxyType(
                {
                    "total_kgCO2e": math.fsum(item.value for item in ordered),
                    "projection_perspective": perspective,
                    "row_count": len(rows),
                    **_synthetic_energy_stamp(self.context, involves_process),
                }
            ),
            emission_ids=tuple(sorted({item.emission_id for item in ordered})),
            projection_keys=tuple(item.projection_key for item in ordered),
            trace_rows=trace_rows,
            coverage=_coverage(
                self.context,
                ordered,
                requested_components,
                involves_process=involves_process,
            ),
        )


__all__ = ["CarbonQLExecutor", "QueryExecution"]
