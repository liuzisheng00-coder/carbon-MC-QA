from __future__ import annotations

from pathlib import Path
import json

import pytest

from dm2c_canonical_v2_reader import CanonicalSchemaError, load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, CarbonQLValidationError
from dm2c_carbonql_executor import CarbonQLExecutor
from tests.task10_v2_fixture import COMPONENT_4, write_task10_release


def query(*steps: dict) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": list(steps)})


def partial_query(selector: dict) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict(
        {
            "steps": [
                selector,
                {"op": "CarbonAtoms", "source": "?"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ],
            "holes": [
                {
                    "dimension": "emission_source",
                    "candidates": ["material", "process"],
                }
            ],
        }
    )


def partial_target_query(selector: dict) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict(
        {
            "steps": [
                selector,
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ],
            "holes": [
                {
                    "dimension": "target",
                    "candidates": ["component", "material", "process"],
                }
            ],
        }
    )


@pytest.fixture()
def executor(tmp_path: Path) -> CarbonQLExecutor:
    return CarbonQLExecutor.from_context(load_canonical_v2_context(write_task10_release(tmp_path / "release")))


@pytest.fixture()
def named_executor(tmp_path: Path) -> CarbonQLExecutor:
    return CarbonQLExecutor.from_context(
        load_canonical_v2_context(
            write_task10_release(tmp_path / "named-release", named_carriers=True)
        )
    )


def synthetic_release(tmp_path: Path) -> Path:
    release = write_task10_release(tmp_path / "synthetic-release")
    manifest_path = release / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["syntheticFactoryInputsUsed"] = True
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
        newline="",
    )
    return release


def execute(executor: CarbonQLExecutor, *, source: object = "all", selector: dict | None = None, group: list[str] | None = None, join: bool = False, trace: bool = False):
    steps = [selector or {"op": "SelectProject"}, {"op": "CarbonAtoms", "source": source}]
    if join:
        steps.append({"op": "JoinByAttribution", "required_sources": ["material", "process"]})
    if group is not None:
        steps.append({"op": "GroupBy", "keys": group})
    steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
    if trace:
        steps.append({"op": "Trace"})
    return executor.execute(query(*steps))


def test_projection_perspectives_match_controlled_totals(executor: CarbonQLExecutor) -> None:
    assert execute(executor).summary["total_kgCO2e"] == pytest.approx(43.0)
    assert execute(executor, source="material").summary["total_kgCO2e"] == pytest.approx(19.0)
    assert execute(executor, source="process").summary["total_kgCO2e"] == pytest.approx(31.0)
    material = execute(executor, source="material", group=["material"])
    carrier = execute(executor, source="process", group=["carrier"])
    union = execute(executor, source="all", group=["material", "process"])
    assert material.summary["total_kgCO2e"] == pytest.approx(19.0)
    assert carrier.summary["total_kgCO2e"] == pytest.approx(31.0)
    assert union.summary["total_kgCO2e"] == pytest.approx(50.0)
    assert material.summary["projection_perspective"] == "material_source"
    assert carrier.summary["projection_perspective"] == "energy_source"
    assert union.summary["projection_perspective"] == "source_union"


def test_process_total_does_not_depend_on_being_grouped(executor: CarbonQLExecutor) -> None:
    """The process account is the same account whether or not it is broken out.

    The product projection can only reach the 24.0 that products carry, so
    asking for a process total without an organizing dimension once returned a
    different, smaller number than the same question broken down by carrier.
    """
    ungrouped = execute(executor, source="process")
    grouped = execute(executor, source="process", group=["carrier"])
    assert ungrouped.summary["total_kgCO2e"] == pytest.approx(
        grouped.summary["total_kgCO2e"]
    )
    assert ungrouped.summary["projection_perspective"] == "energy_source"
    assert len(ungrouped.emission_ids) == len(grouped.emission_ids)


def test_synthetic_release_requires_explicit_allow_synthetic(tmp_path: Path) -> None:
    release = synthetic_release(tmp_path)

    with pytest.raises(CanonicalSchemaError, match="syntheticFactoryInputsUsed"):
        CarbonQLExecutor.from_release(release)

    executor = CarbonQLExecutor.from_release(release, allow_synthetic=True)
    result = execute(executor, source="process")
    assert executor.context.synthetic_energy is True
    assert result.summary["synthetic_energy"] is True
    assert result.coverage["synthetic_energy"] is True
    assert result.summary["synthetic_energy_provenance"] == "controlled-fixture synthetic factory input"


def test_component_grouping_forces_product_projection_and_excludes_process_only(executor: CarbonQLExecutor) -> None:
    result = execute(executor, group=["component"])
    values = {row["component"]: row["kgCO2e"] for row in result.rows}
    assert values == {
        "component:c1": pytest.approx(15.0),
        "component:c2": pytest.approx(21.0),
        "component:no-facts": pytest.approx(6.0),
        COMPONENT_4: pytest.approx(1.0),
    }
    assert result.summary["projection_perspective"] == "product"
    assert "emission:process" not in result.emission_ids


def test_valid_zero_is_retained_in_rows_coverage_and_trace(executor: CarbonQLExecutor) -> None:
    result = execute(
        executor,
        source="process",
        selector={"op": "SelectClicked", "ids": [COMPONENT_4]},
        group=["component"],
        trace=True,
    )
    assert result.rows == (
        {"component": COMPONENT_4, "component_name": None, "kgCO2e": 0.0},
    )
    assert "emission:zero" in result.emission_ids
    assert any(row["emission_id"] == "emission:zero" and row["value"] == 0.0 for row in result.trace_rows)
    assert result.coverage["accepted_projection_count"] == 1


def test_allocated_trace_is_contribution_aware(executor: CarbonQLExecutor) -> None:
    result = execute(
        executor,
        selector={"op": "SelectClicked", "ids": ["component:c1"]},
        trace=True,
    )
    allocated = next(row for row in result.trace_rows if row["emission_id"] == "emission:allocated")
    assert allocated["component_id"] == "component:c1"
    assert allocated["recorded_for_occurrence_id"]
    assert allocated["evidence_record_id"] == "allocation:evidence:c1"
    assert allocated["projection_key"] == ("allocated", "emission:allocated", "allocation:set:1", "component:c1")


def test_rejection_never_materializes_and_graph_order_is_irrelevant(tmp_path: Path) -> None:
    first = CarbonQLExecutor.from_context(load_canonical_v2_context(write_task10_release(tmp_path / "first")))
    second = CarbonQLExecutor.from_context(load_canonical_v2_context(write_task10_release(tmp_path / "second", reverse_graph_order=True)))
    left = execute(first, group=["component"], trace=True).to_dict()
    right = execute(second, group=["component"], trace=True).to_dict()
    assert left == right
    assert "record:rejected" not in repr(left)
    assert "atom_ids" not in left
    assert left["coverage"]["rejected_count"] == 1


def test_strict_legacy_graph_is_rejected(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "multigranular_carbon_kg.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
    with pytest.raises(CanonicalSchemaError):
        CarbonQLExecutor.from_release(legacy)


def test_incomplete_context_comes_from_request_and_validation_coverage(
    executor: CarbonQLExecutor,
) -> None:
    result = execute(
        executor,
        selector={"op": "SelectClicked", "ids": ["component:missing"]},
    )
    assert result.status == "unresolved_target"
    assert result.coverage["rejected_count"] == 1
    assert result.coverage["requested_component_count"] == 1
    assert result.coverage["missing_component_ids"] == ("component:missing",)


def test_component_type_grouping_and_ifc_class_resolution_use_canonical_edges(
    executor: CarbonQLExecutor,
) -> None:
    by_type = execute(executor, group=["component_type"])
    assert {row["component_type"]: row["kgCO2e"] for row in by_type.rows} == {
        "type:structural": pytest.approx(37.0),
        "type:wall": pytest.approx(6.0),
    }
    beam = execute(
        executor,
        selector={
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "ifcClass",
            "value": "IfcBeam",
            "cardinality": "set",
        },
    )
    assert beam.summary["total_kgCO2e"] == pytest.approx(15.0)


def test_allocation_conserves_one_source_into_two_product_projections(
    executor: CarbonQLExecutor,
) -> None:
    product = execute(executor, trace=True)
    allocated = [row for row in product.trace_rows if row["emission_id"] == "emission:allocated"]
    assert [row["value"] for row in allocated] == [5.0, 15.0]
    assert sum(row["value"] for row in allocated) == pytest.approx(20.0)
    source = execute(executor, source="process", group=["carrier"], trace=True)
    allocated_sources = [row for row in source.trace_rows if row["emission_id"] == "emission:allocated"]
    assert len(allocated_sources) == 1
    assert allocated_sources[0]["projection_key"] == ("source", "emission:allocated")
    assert allocated_sources[0]["value"] == pytest.approx(20.0)


def test_stage_and_resource_source_views_include_process_only(
    executor: CarbonQLExecutor,
) -> None:
    for dimension in ("stage", "resource"):
        result = execute(executor, source="process", group=[dimension], trace=True)
        assert result.summary["total_kgCO2e"] == pytest.approx(31.0)
        assert "emission:process" in result.emission_ids
        assert any(row[dimension] is not None and row["kgCO2e"] == 7.0 for row in result.rows)


@pytest.mark.parametrize(
    ("source", "group", "perspective"),
    [
        ("material", ["carrier"], "energy_source"),
        ("process", ["material"], "material_source"),
    ],
)
def test_incompatible_source_and_dimension_is_deterministically_empty(
    executor: CarbonQLExecutor,
    source: str,
    group: list[str],
    perspective: str,
) -> None:
    result = execute(executor, source=source, group=group, trace=True)
    assert result.status == "ok"
    assert result.summary == {
        "total_kgCO2e": 0.0,
        "projection_perspective": perspective,
        "record_projection_perspective": perspective,
        "row_count": 0,
    }
    assert result.rows == ()
    assert result.emission_ids == ()
    assert result.projection_keys == ()


def test_material_grouping_keeps_product_filter_attribution(
    executor: CarbonQLExecutor,
) -> None:
    program = query(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Filter", "field": "component_type", "equals": "type:structural"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = executor.execute(program)
    assert result.summary["projection_perspective"] == "material_source"
    assert result.summary["record_projection_perspective"] == "product"
    assert result.summary["total_kgCO2e"] == pytest.approx(37.0)
    assert {row["material"]: row["kgCO2e"] for row in result.rows} == {
        None: pytest.approx(20.0),
        "material:steel": pytest.approx(16.0),
        "material:unused": pytest.approx(1.0),
    }


@pytest.mark.parametrize(
    ("source", "field", "value", "perspective", "total"),
    [
        ("material", "material", "material:steel", "material_source", 16.0),
        ("process", "carrier", "carrier:gas", "energy_source", 7.0),
    ],
)
def test_non_product_filter_controls_perspective_without_grouping(
    executor: CarbonQLExecutor,
    source: str,
    field: str,
    value: str,
    perspective: str,
    total: float,
) -> None:
    program = query(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": source},
        {"op": "Filter", "field": field, "equals": value},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = executor.execute(program)
    assert result.summary["projection_perspective"] == perspective
    assert result.summary["total_kgCO2e"] == pytest.approx(total)


def test_entity_filter_resolves_readable_names_like_ids(
    named_executor: CarbonQLExecutor,
) -> None:
    by_name = named_executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "Filter", "field": "carrier", "in": ["electricity"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    by_id = named_executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "Filter", "field": "carrier", "equals": "carrier:electricity"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert by_name.status == by_id.status == "ok"
    assert by_name.summary["total_kgCO2e"] == by_id.summary["total_kgCO2e"] == pytest.approx(24.0)
    assert by_name.emission_ids == by_id.emission_ids


def test_unsatisfiable_entity_filter_fails_closed_without_numeric_zero(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "Filter", "field": "carrier", "equals": "unobtainium"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert result.status == "unsatisfiable_filter"
    assert result.rows == ()
    assert "total_kgCO2e" not in result.summary
    assert result.coverage["accepted_projection_count"] == 0


def test_ambiguous_entity_filter_fails_closed_without_choosing_a_match(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "Filter", "field": "material", "equals": "Structural material"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert result.status == "unsatisfiable_filter"
    assert result.rows == ()
    assert "total_kgCO2e" not in result.summary


def test_satisfiable_filter_with_no_records_remains_an_evidenced_zero(
    named_executor: CarbonQLExecutor,
) -> None:
    result = named_executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "Filter", "field": "carrier", "equals": "electricity"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(0.0)
    assert result.rows == ({"kgCO2e": 0.0},)
    assert result.emission_ids == ()


def test_grouped_entity_rows_include_readable_name_columns(
    named_executor: CarbonQLExecutor,
) -> None:
    result = execute(named_executor, source="process", group=["carrier"])
    rows = {row["carrier"]: row for row in result.rows}
    assert rows["carrier:electricity"]["carrier_name"] == "electricity"
    assert rows["carrier:gas"]["carrier_name"] == "natural gas"


def test_resolve_material_and_process_use_complete_canonical_id_sets(
    executor: CarbonQLExecutor,
) -> None:
    materials = execute(
        executor,
        source="material",
        selector={
            "op": "ResolveEntities",
            "entity_type": "material",
            "ids": ["material:steel", "material:unused"],
            "cardinality": "set",
        },
    )
    assert materials.summary["total_kgCO2e"] == pytest.approx(19.0)
    property_set = execute(
        executor,
        source="material",
        selector={
            "op": "ResolveEntities",
            "entity_type": "material",
            "property": "name",
            "value": "Structural material",
            "cardinality": "set",
        },
    )
    assert property_set.summary["total_kgCO2e"] == pytest.approx(19.0)
    singleton = execute(
        executor,
        source="material",
        selector={
            "op": "ResolveEntities",
            "entity_type": "material",
            "ids": ["material:steel", "material:unused"],
            "cardinality": "singleton",
        },
    )
    assert singleton.status == "unresolved_target"
    property_singleton = execute(
        executor,
        source="material",
        selector={
            "op": "ResolveEntities",
            "entity_type": "material",
            "property": "name",
            "value": "Structural material",
            "cardinality": "singleton",
        },
    )
    assert property_singleton.status == "unresolved_target"
    stage = execute(
        executor,
        source="process",
        selector={
            "op": "ResolveEntities",
            "entity_type": "process",
            "ids": ["process:stage"],
            "cardinality": "set",
        },
    )
    assert stage.summary["total_kgCO2e"] == pytest.approx(7.0)


def test_resolve_component_honors_set_and_singleton_cardinality(
    executor: CarbonQLExecutor,
) -> None:
    ids_set = execute(
        executor,
        selector={
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1", "component:c2"],
            "cardinality": "set",
        },
    )
    assert ids_set.summary["total_kgCO2e"] == pytest.approx(36.0)
    ids_singleton = execute(
        executor,
        selector={
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1", "component:c2"],
            "cardinality": "singleton",
        },
    )
    assert ids_singleton.status == "unresolved_target"
    property_set = execute(
        executor,
        selector={
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "name",
            "value": "Structural member",
            "cardinality": "set",
        },
    )
    assert property_set.summary["total_kgCO2e"] == pytest.approx(36.0)
    property_singleton = execute(
        executor,
        selector={
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "name",
            "value": "Structural member",
            "cardinality": "singleton",
        },
    )
    assert property_singleton.status == "unresolved_target"


@pytest.mark.parametrize(
    ("source", "expected_perspective", "expected_total"),
    [
        ("material", "material_source", 19.0),
        ("process", "energy_source", 31.0),
        ("all", "source_union", 50.0),
    ],
)
def test_factor_only_dimensions_follow_requested_source_perspective(
    executor: CarbonQLExecutor,
    source: str,
    expected_perspective: str,
    expected_total: float,
) -> None:
    for dimension in ("factor_keyword", "factor_source"):
        result = execute(executor, source=source, group=[dimension])
        assert result.summary["projection_perspective"] == expected_perspective
        assert result.summary["total_kgCO2e"] == pytest.approx(expected_total)


def test_factor_filters_are_source_perspectives_without_grouping(
    executor: CarbonQLExecutor,
) -> None:
    process = executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "Filter", "field": "factor_keyword", "equals": "natural gas"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert process.summary["projection_perspective"] == "energy_source"
    assert process.summary["total_kgCO2e"] == pytest.approx(7.0)
    assert process.emission_ids == ("emission:process",)
    unified = executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Filter", "field": "factor_source", "equals": "controlled-fixture"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert unified.summary["projection_perspective"] == "source_union"
    assert unified.summary["total_kgCO2e"] == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("entity_type", "ids", "source", "total"),
    [
        ("component", ["component:c1", "C1", "component:c1"], "all", 15.0),
        ("material", ["material:steel", "material:steel"], "material", 16.0),
        ("process", ["process:stage", "process:stage"], "process", 7.0),
    ],
)
def test_resolve_exact_ids_deduplicate_by_canonical_identity_before_singleton(
    executor: CarbonQLExecutor,
    entity_type: str,
    ids: list[str],
    source: str,
    total: float,
) -> None:
    result = execute(
        executor,
        source=source,
        selector={
            "op": "ResolveEntities",
            "entity_type": entity_type,
            "ids": ids,
            "cardinality": "singleton",
        },
    )
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(total)


@pytest.mark.parametrize("cardinality", ["set", "singleton"])
@pytest.mark.parametrize(
    ("entity_type", "ids", "source"),
    [
        ("component", ["component:c1", "component:missing"], "all"),
        ("material", ["material:steel", "material:missing"], "material"),
        ("process", ["process:stage", "process:missing"], "process"),
    ],
)
def test_resolve_exact_ids_fail_closed_when_any_token_is_unknown(
    executor: CarbonQLExecutor,
    entity_type: str,
    ids: list[str],
    source: str,
    cardinality: str,
) -> None:
    result = execute(
        executor,
        source=source,
        selector={
            "op": "ResolveEntities",
            "entity_type": entity_type,
            "ids": ids,
            "cardinality": cardinality,
        },
    )
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary
    assert result.coverage["accepted_projection_count"] == 0


def test_component_mixed_unknown_coverage_discloses_known_and_only_missing_token(
    executor: CarbonQLExecutor,
) -> None:
    result = execute(
        executor,
        selector={
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1", "component:missing"],
            "cardinality": "set",
        },
    )
    assert result.status == "unresolved_target"
    assert result.coverage == {
        "accepted_projection_count": 0,
        "accepted_emission_count": 0,
        "rejected_count": 1,
        "requested_component_count": 2,
        "covered_component_ids": ("component:c1",),
        "missing_component_ids": ("component:missing",),
    }


@pytest.mark.parametrize("runtime", [False, True])
def test_select_clicked_deduplicates_and_fails_closed_for_unknown_ids(
    executor: CarbonQLExecutor, runtime: bool
) -> None:
    selector = {"op": "SelectClicked"}
    selected: tuple[str, ...] = ()
    if runtime:
        selected = ("component:c1", "component:missing", "component:c1")
    else:
        selector["ids"] = ["component:c1", "component:missing", "component:c1"]
    program = query(
        selector,
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = executor.execute(program, selected)
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary
    assert result.coverage["requested_component_count"] == 2
    assert result.coverage["covered_component_ids"] == ("component:c1",)
    assert result.coverage["missing_component_ids"] == ("component:missing",)


def test_select_clicked_duplicate_known_ids_resolve_once(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectClicked", "ids": ["component:c1", "component:c1"]},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(15.0)
    assert result.coverage["requested_component_count"] == 1


def test_select_clicked_rejects_conflicting_program_and_runtime_components(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectClicked", "ids": ["component:c1"]},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        ("component:c2",),
    )
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary
    assert result.coverage == {
        "accepted_projection_count": 0,
        "accepted_emission_count": 0,
        "rejected_count": 1,
        "requested_component_count": 2,
        "covered_component_ids": ("component:c1", "component:c2"),
        "missing_component_ids": (),
    }


def test_partial_select_clicked_still_rejects_conflicting_runtime_components(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        partial_query({"op": "SelectClicked", "ids": ["component:c1"]}),
        ("component:c2",),
    )
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary
    assert result.coverage["requested_component_count"] == 2
    assert result.coverage["covered_component_ids"] == (
        "component:c1",
        "component:c2",
    )
    assert result.coverage["missing_component_ids"] == ()


def test_partial_unresolved_selector_preserves_target_hole_without_entity_resolution(
    executor: CarbonQLExecutor,
) -> None:
    program = partial_target_query(
        {
            "op": "ResolveEntities",
            "entity_type": "?",
            "ids": ["component:c1"],
            "cardinality": "singleton",
        }
    )
    result = executor.execute(program)
    assert result.status == "partial"
    assert result.holes == program.holes
    assert result.rows == ()
    assert "total_kgCO2e" not in result.summary
    assert result.coverage["requested_component_count"] == 0


@pytest.mark.parametrize(
    "selector",
    [
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["?"],
            "cardinality": "set",
        },
        {
            "op": "ResolveEntities",
            "entity_type": "material",
            "ids": ["?"],
            "cardinality": "set",
        },
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "name",
            "value": "?",
            "cardinality": "set",
        },
    ],
)
def test_literal_question_mark_locator_is_resolved_fail_closed(
    executor: CarbonQLExecutor, selector: dict
) -> None:
    result = executor.execute(
        query(
            selector,
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary
    assert result.holes == ()


def test_literal_question_mark_locator_precedes_unrelated_source_partial(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        partial_query(
            {
                "op": "ResolveEntities",
                "entity_type": "component",
                "ids": ["?"],
                "cardinality": "set",
            }
        )
    )
    assert result.status == "unresolved_target"
    assert result.holes == ()


def test_unresolved_entity_type_without_target_hole_is_rejected_by_grammar(
    executor: CarbonQLExecutor,
) -> None:
    with pytest.raises(CarbonQLValidationError, match="target"):
        executor.execute(
            query(
                {
                    "op": "ResolveEntities",
                    "entity_type": "?",
                    "ids": ["component:c1"],
                    "cardinality": "singleton",
                },
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            )
        )


def test_select_clicked_accepts_canonically_equal_program_and_runtime_components(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectClicked", "ids": ["component:c1"]},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        ("C1", "component:c1", "C1"),
    )
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(15.0)
    assert result.coverage["requested_component_count"] == 1


@pytest.mark.parametrize(
    ("program_ids", "runtime_ids"),
    [
        (["component:c1"], ("component:missing",)),
        (["component:missing"], ("component:c1",)),
    ],
)
def test_select_clicked_cannot_bypass_unknown_ids_from_either_source(
    executor: CarbonQLExecutor,
    program_ids: list[str],
    runtime_ids: tuple[str, ...],
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectClicked", "ids": program_ids},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        runtime_ids,
    )
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary
    assert result.coverage["covered_component_ids"] == ("component:c1",)
    assert result.coverage["missing_component_ids"] == ("component:missing",)


def test_partial_program_never_returns_numeric_summary(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(partial_query({"op": "SelectProject"}))
    assert result.status == "partial"
    assert result.rows == ()
    assert result.emission_ids == ()
    assert "total_kgCO2e" not in result.summary


def test_select_clicked_uses_runtime_components_when_program_has_no_ids(
    executor: CarbonQLExecutor,
) -> None:
    result = executor.execute(
        query(
            {"op": "SelectClicked"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        ("component:c2",),
    )
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(21.0)


def test_runtime_component_context_is_ignored_by_non_clicked_selectors(
    executor: CarbonQLExecutor,
) -> None:
    project = executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        ("component:c2",),
    )
    resolved = executor.execute(
        query(
            {
                "op": "ResolveEntities",
                "entity_type": "component",
                "ids": ["component:c1"],
                "cardinality": "singleton",
            },
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        ("component:c2",),
    )
    assert project.summary["total_kgCO2e"] == pytest.approx(43.0)
    assert project.coverage["requested_component_count"] == 0
    assert resolved.summary["total_kgCO2e"] == pytest.approx(15.0)
    assert resolved.coverage["requested_component_count"] == 1


@pytest.mark.parametrize(
    ("selector", "dimension", "perspective", "total"),
    [
        ({"op":"ResolveEntities","entity_type":"material","ids":["material:steel"],"cardinality":"set"}, "carrier", "energy_source", 16.0),
        ({"op":"ResolveEntities","entity_type":"process","ids":["process:stage"],"cardinality":"set"}, "material", "material_source", 7.0),
        ({"op":"ResolveEntities","entity_type":"material","ids":["material:steel"],"cardinality":"set"}, "factor_source", "source_union", 16.0),
        ({"op":"ResolveEntities","entity_type":"process","ids":["process:stage"],"cardinality":"set"}, "factor_source", "source_union", 7.0),
    ],
)
def test_execution_perspective_follows_dimensions_while_entity_scope_holds_totals(
    executor: CarbonQLExecutor,
    selector: dict,
    dimension: str,
    perspective: str,
    total: float,
) -> None:
    result = execute(executor, source="all", selector=selector, group=[dimension])
    assert result.summary["projection_perspective"] == perspective
    assert result.summary["total_kgCO2e"] == pytest.approx(total)


def test_source_kind_filter_and_group_share_source_union_semantics(
    executor: CarbonQLExecutor,
) -> None:
    results = []
    for grouped in (False, True):
        steps = [
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Filter", "field": "source_kind", "equals": "process"},
        ]
        if grouped:
            steps.append({"op": "GroupBy", "keys": ["source_kind"]})
        steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
        results.append(executor.execute(query(*steps)))
    for result in results:
        assert result.summary["projection_perspective"] == "source_union"
        assert result.summary["total_kgCO2e"] == pytest.approx(31.0)
        assert "emission:process" in result.emission_ids
    assert results[1].rows == ({"source_kind": "process", "kgCO2e": 31.0},)


def test_source_kind_group_all_totals_fifty_and_conflicts_are_empty(
    executor: CarbonQLExecutor,
) -> None:
    grouped = execute(executor, source="all", group=["source_kind"])
    assert grouped.summary["projection_perspective"] == "source_union"
    assert grouped.summary["total_kgCO2e"] == pytest.approx(50.0)
    assert {row["source_kind"]: row["kgCO2e"] for row in grouped.rows} == {
        "material": pytest.approx(19.0),
        "process": pytest.approx(31.0),
    }
    conflict = executor.execute(
        query(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "Filter", "field": "source_kind", "equals": "process"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        )
    )
    assert conflict.summary["projection_perspective"] == "material_source"
    assert conflict.summary["total_kgCO2e"] == pytest.approx(0.0)
