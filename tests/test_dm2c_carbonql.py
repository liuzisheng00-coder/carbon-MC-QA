from __future__ import annotations

from pathlib import Path

import pytest

import dm2c_carbonql as carbonql
from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import (
    FACTOR_KEYS,
    CarbonQLProgram,
    CarbonQLValidationError,
    GraphSchema,
    derive_view_signature,
    validate_program,
)
from dm2c_carbonql_synthesizer import build_synthesis_messages
from tests.task10_v2_fixture import write_task10_release


@pytest.fixture()
def schema(tmp_path: Path) -> GraphSchema:
    return GraphSchema.from_context(load_canonical_v2_context(write_task10_release(tmp_path / "release")))


def program(*steps: dict) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": list(steps)})


def valid_program_containing(op: str) -> CarbonQLProgram:
    selector = {"op": "SelectProject"}
    if op == "SelectClicked":
        selector = {"op": "SelectClicked", "ids": ["component:c1"]}
    elif op == "ResolveEntities":
        selector = {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1"],
            "cardinality": "singleton",
        }
    steps: list[dict] = [selector, {"op": "CarbonAtoms", "source": "all"}]
    if op == "Filter":
        steps.append({"op": "Filter", "field": "material", "equals": "material:steel"})
    elif op == "JoinByAttribution":
        steps.append({"op": "JoinByAttribution", "required_sources": ["material", "process"]})
    if op in {"GroupBy", "Rank", "Compare"}:
        steps.append({"op": "GroupBy", "keys": ["material"]})
    if op == "Trace":
        steps.append({"op": "Trace"})
    else:
        steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
        if op == "Rank":
            steps.append({"op": "Rank", "top_k": 2, "descending": True})
        elif op == "Compare":
            steps.append({"op": "Compare"})
    return program(*steps)


def material_only_schema() -> GraphSchema:
    return GraphSchema(
        labels=frozenset({"BuildingComponent", "IfcMaterial"}),
        relations=frozenset({"hasMaterial"}),
        dimension_counts={
            "project": 1,
            "component": 2,
            "material": 1,
            "module": 1,
            "component_type": 1,
            "ifc_class": 1,
            "carrier": 0,
            "process": 0,
            "stage": 0,
            "resource": 0,
            "factor_keyword": 1,
            "factor_source": 1,
            "source_kind": 1,
        },
        carbon_sources=frozenset({"material"}),
    )


def test_validate_program_downgrades_absent_live_process_source() -> None:
    result = validate_program(
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        material_only_schema(),
    )
    assert result.compiler_status == "incomplete_path"
    assert not result.executable


@pytest.mark.parametrize("op", sorted(carbonql.OPERATIONS))
def test_each_operator_rejects_unknown_arguments(schema: GraphSchema, op: str) -> None:
    query = valid_program_containing(op)
    payload = query.to_dict()
    target = next(step for step in payload["steps"] if step["op"] == op)
    target["unexpected"] = "must-not-be-ignored"
    with pytest.raises(CarbonQLValidationError, match="unknown argument"):
        validate_program(CarbonQLProgram.from_dict(payload), schema)


@pytest.mark.parametrize(
    ("op", "forbidden_arg"),
    [
        ("SelectProject", "materialFamily"),
        ("CarbonAtoms", "ifcType"),
        ("Aggregate", "values"),
    ],
)
def test_forbidden_runtime_inputs_cannot_hide_on_other_steps(
    schema: GraphSchema, op: str, forbidden_arg: str
) -> None:
    query = valid_program_containing(op)
    payload = query.to_dict()
    next(step for step in payload["steps"] if step["op"] == op)[forbidden_arg] = "x"
    with pytest.raises(CarbonQLValidationError, match="unknown argument"):
        validate_program(CarbonQLProgram.from_dict(payload), schema)


@pytest.mark.parametrize("ids", [[], [""], ["component:c1", ""], "component:c1"])
def test_select_clicked_ids_are_optional_but_strict_when_present(
    schema: GraphSchema, ids: object
) -> None:
    query = program(
        {"op": "SelectClicked", "ids": ids},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    with pytest.raises(CarbonQLValidationError, match="SelectClicked ids"):
        validate_program(query, schema)


@pytest.mark.parametrize(
    "selector",
    [
        {"op": "ResolveEntities", "entity_type": "component"},
        {"op": "ResolveEntities", "entity_type": "component", "property": "name"},
        {"op": "ResolveEntities", "entity_type": "component", "value": ""},
        {"op": "ResolveEntities", "entity_type": "component", "ids": [""]},
        {"op": "ResolveEntities", "entity_type": "component", "ids": ["component:c1"], "value": "Member"},
        {"op": "ResolveEntities", "entity_type": "component", "ids": ["component:c1"], "property": "name", "value": "Member"},
    ],
)
def test_resolve_entities_requires_exactly_one_well_formed_locator(
    schema: GraphSchema, selector: dict
) -> None:
    query = program(
        selector,
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    with pytest.raises(CarbonQLValidationError, match="locator|ids|value"):
        validate_program(query, schema)


def test_resolve_entities_value_defaults_to_exact_name_property(schema: GraphSchema) -> None:
    query = program(
        {"op": "ResolveEntities", "entity_type": "material", "value": "Structural material", "cardinality": "set"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(query, schema).executable


def test_resolve_entities_rejects_blank_explicit_entity_type(schema: GraphSchema) -> None:
    query = program(
        {"op": "ResolveEntities", "entity_type": "", "ids": ["component:c1"]},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    with pytest.raises(CarbonQLValidationError, match="ResolveEntities type"):
        validate_program(query, schema)


@pytest.mark.parametrize("predicate", [{"equals": ""}, {"in": ["material:steel", ""]}])
def test_filter_predicates_require_non_empty_exact_strings(
    schema: GraphSchema, predicate: dict
) -> None:
    query = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Filter", "field": "material", **predicate},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    with pytest.raises(CarbonQLValidationError, match="Filter"):
        validate_program(query, schema)


def test_v2_dimensions_are_exact_and_legacy_properties_are_rejected(schema: GraphSchema) -> None:
    valid = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "GroupBy", "keys": ["ifc_class", "component_type", "material", "carrier", "process", "resource"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(valid, schema).executable
    for legacy in ("ifcType", "materialFamily", "workstation", "batch", "activity"):
        invalid = program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "GroupBy", "keys": [legacy]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
        with pytest.raises(CarbonQLValidationError, match="Unknown grouping dimension"):
            validate_program(invalid, schema)


def test_resolve_entities_uses_ifc_class_and_explicit_names_only(schema: GraphSchema) -> None:
    valid = program(
        {"op": "ResolveEntities", "entity_type": "component", "property": "ifcClass", "value": "IfcBeam", "cardinality": "set"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(valid, schema).output_type == "CarbonScalar"
    for legacy in ("ifcType", "materialFamily", "objectType"):
        payload = valid.to_dict()
        payload["steps"][0]["property"] = legacy
        with pytest.raises(CarbonQLValidationError, match="property is not queryable"):
            validate_program(CarbonQLProgram.from_dict(payload), schema)


def test_aggregate_remains_a_legitimate_carbonql_operator(schema: GraphSchema) -> None:
    query = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = validate_program(query, schema)
    assert result.output_type == "CarbonScalar"
    assert derive_view_signature(query).base_views == ("product",)


def test_source_only_views_are_derived_explicitly(schema: GraphSchema) -> None:
    material = program(
        {"op": "ResolveEntities", "entity_type": "material", "property": "name", "value": "Steel", "cardinality": "set"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    process = program(
        {"op": "ResolveEntities", "entity_type": "process", "property": "name", "value": "Stage", "cardinality": "set"},
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "GroupBy", "keys": ["process"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert derive_view_signature(material).base_views == ("material",)
    assert derive_view_signature(process).base_views == ("process",)


def test_synthesis_contract_names_only_canonical_v2_fields(schema: GraphSchema) -> None:
    payload = repr(build_synthesis_messages("Group by carrier.", (), schema, "V2"))
    assert "ifcClass" in payload and "component_type" in payload and "carrier" in payload
    for legacy in ("ifcType", "materialFamily", "material_family", "energy_carrier", "workstation", "batch"):
        assert legacy not in payload
    assert "material families" not in payload.lower()


def test_factor_dimensions_are_source_neutral_and_explicit(schema: GraphSchema) -> None:
    assert FACTOR_KEYS == frozenset({"factor_keyword", "factor_source"})
    for source, views in (
        ("material", ("material",)),
        ("process", ("process",)),
        ("all", ("material", "process")),
    ):
        query = program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": source},
            {"op": "GroupBy", "keys": ["factor_source"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
        assert validate_program(query, schema).executable
        assert derive_view_signature(query).base_views == views


@pytest.mark.parametrize(
    ("source", "field", "value", "views"),
    [
        ("process", "factor_keyword", "natural gas", ("process",)),
        ("material", "material", "material:steel", ("material",)),
        ("process", "carrier", "carrier:gas", ("process",)),
        ("all", "component_type", "type:structural", ("product",)),
    ],
)
def test_filter_dimensions_control_view_signature(
    schema: GraphSchema,
    source: str,
    field: str,
    value: str,
    views: tuple[str, ...],
) -> None:
    query = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": source},
        {"op": "Filter", "field": field, "equals": value},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(query, schema).executable
    assert derive_view_signature(query).base_views == views


def test_entity_name_dimensions_are_legal_filter_and_group_keys(
    schema: GraphSchema,
) -> None:
    query = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Filter", "field": "carrier_name", "equals": "electricity"},
        {"op": "GroupBy", "keys": ["carrier_name"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = validate_program(query, schema)
    assert result.executable
    assert derive_view_signature(query).base_views == ("process",)


def test_material_grouping_sets_signature_within_product_filter(schema: GraphSchema) -> None:
    query = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Filter", "field": "component_type", "equals": "type:structural"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(query, schema).executable
    assert derive_view_signature(query).base_views == ("material",)


@pytest.mark.parametrize(
    ("entity_type", "property_name"),
    [
        ("component", "carrierId"),
        ("material", "ifcClass"),
        ("process", "source"),
        ("process", "keyword"),
    ],
)
def test_resolve_entity_properties_are_typed_to_real_canonical_lookups(
    schema: GraphSchema,
    entity_type: str,
    property_name: str,
) -> None:
    invalid = program(
        {"op": "ResolveEntities", "entity_type": entity_type, "property": property_name, "value": "x", "cardinality": "set"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    with pytest.raises(CarbonQLValidationError, match="property is not queryable"):
        validate_program(invalid, schema)


def test_synthesis_resolver_contract_is_typed_and_excludes_factor_fields(schema: GraphSchema) -> None:
    import json

    messages = build_synthesis_messages("Resolve a material.", (), schema, "V2")
    contract = json.loads(messages[1]["content"])["contract"]["operator_arguments"]["ResolveEntities"]
    assert contract["properties_by_entity_type"] == {
        "component": ["name", "globalId", "ifcClass"],
        "material": ["name"],
        "process": ["name"],
    }
    assert contract["default_property"] == "name"
    assert contract["locator"] == "exactly one of ids or value; property is optional only with value"
    assert "property" not in contract
    assert "property auto" not in repr(messages).lower()


@pytest.mark.parametrize(
    ("selector", "dimension", "source", "views"),
    [
        ({"op": "ResolveEntities", "entity_type": "material", "ids": ["material:steel"], "cardinality": "set"}, "carrier", "all", ("process",)),
        ({"op": "ResolveEntities", "entity_type": "process", "ids": ["process:stage"], "cardinality": "set"}, "material", "all", ("material",)),
        ({"op": "ResolveEntities", "entity_type": "material", "ids": ["material:steel"], "cardinality": "set"}, "factor_source", "all", ("material", "process")),
        ({"op": "ResolveEntities", "entity_type": "process", "ids": ["process:stage"], "cardinality": "set"}, "factor_source", "all", ("material", "process")),
        ({"op": "SelectClicked"}, "material", "material", ("material",)),
        ({"op": "SelectClicked"}, "carrier", "process", ("process",)),
        ({"op": "ResolveEntities", "entity_type": "component", "ids": ["component:beam"], "cardinality": "set"}, "material", "material", ("material",)),
    ],
)
def test_view_signature_follows_dimensions_not_the_selector(
    schema: GraphSchema,
    selector: dict,
    dimension: str,
    source: str,
    views: tuple[str, ...],
) -> None:
    query = program(
        selector,
        {"op": "CarbonAtoms", "source": source},
        {"op": "GroupBy", "keys": [dimension]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(query, schema).executable
    assert derive_view_signature(query).base_views == views


@pytest.mark.parametrize(
    "selector",
    [
        {"op": "SelectProject"},
        {"op": "SelectClicked"},
        {"op": "ResolveEntities", "entity_type": "component", "ids": ["component:beam"], "cardinality": "set"},
        {"op": "ResolveEntities", "entity_type": "material", "ids": ["material:steel"], "cardinality": "set"},
    ],
)
def test_empty_grouping_reports_the_product_perspective(
    schema: GraphSchema, selector: dict
) -> None:
    query = program(
        selector,
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert validate_program(query, schema).executable
    assert derive_view_signature(query).base_views == ("product",)


def test_source_kind_is_source_neutral_not_a_factor_key(schema: GraphSchema) -> None:
    assert carbonql.SOURCE_KEYS == frozenset({"source_kind"})
    assert FACTOR_KEYS == frozenset({"factor_keyword", "factor_source"})
    for grouped in (False, True):
        steps = [
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Filter", "field": "source_kind", "equals": "process"},
        ]
        if grouped:
            steps.append({"op": "GroupBy", "keys": ["source_kind"]})
        steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
        query = program(*steps)
        assert validate_program(query, schema).executable
        assert derive_view_signature(query).base_views == ("material", "process")
