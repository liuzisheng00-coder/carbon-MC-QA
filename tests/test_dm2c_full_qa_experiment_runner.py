from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from dm2c_carbonql import CarbonQLProgram
from dm2c_canonical_v2_reader import dimension_ids
from dm2c_full_qa_experiment_runner import (
    QueryPolicy,
    execute_canonical_query,
    load_full_qa_context,
    status_for_availability,
)
from dm2c_m3_context import immutable_mapping
from tests.task11_v2_fixture import (
    controlled_programs,
    program,
    write_actual_like_no_energy_release,
    write_task10_release,
)


def test_full_runner_cli_documents_frozen_benchmark_and_release_pinning() -> None:
    completed = subprocess.run(
        [sys.executable, "dm2c_full_qa_experiment_runner.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--benchmark" in completed.stdout
    assert "--kg-dir" in completed.stdout
    assert "--full" in completed.stdout
    assert "--allow-synthetic" in completed.stdout
    assert "e4_balanced_benchmark_150_human_reviewed_v10_layerdedup.jsonl" in completed.stdout


@pytest.fixture
def context(tmp_path: Path):
    return load_full_qa_context(write_task10_release(tmp_path / "release"))


@pytest.fixture(scope="module")
def layerdedup_context():
    return load_full_qa_context(
        Path("outputs/research_experiments/m2_typed_completed_20260728_layerdedup"),
        allow_synthetic=True,
    )


def test_product_query_returns_immutable_canonical_result(context) -> None:
    result = execute_canonical_query(context, controlled_programs()[0][1])
    assert result.status == "executable"
    assert result.coverage_status == "complete"
    assert result.perspective == "product"
    assert result.operation == "aggregate"
    assert result.summary["total_kgCO2e"] == pytest.approx(43.0)
    assert len(result.projection_keys) == 8
    assert "emission:process" not in result.emission_ids


def test_nested_query_payloads_are_deeply_immutable() -> None:
    frozen = immutable_mapping({"nested": {"values": [1, 2]}})
    with pytest.raises(TypeError):
        frozen["nested"]["new"] = 1  # type: ignore[index]
    assert frozen["nested"]["values"] == (1, 2)


def test_modular_unit_selection_traces_module_evidence(layerdedup_context) -> None:
    module_id = dimension_ids(layerdedup_context.canonical, "module")[0]
    result = execute_canonical_query(
        layerdedup_context,
        program(
            {"op": "SelectClicked", "ids": [module_id]},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Trace"},
        ),
    )
    assert result.status == "executable"
    assert result.evidence_ids
    assert result.summary["total_kgCO2e"] > 0


def test_resolved_modular_unit_traces_module_evidence(layerdedup_context) -> None:
    module_id = dimension_ids(layerdedup_context.canonical, "module")[0]
    result = execute_canonical_query(
        layerdedup_context,
        program(
            {
                "op": "ResolveEntities",
                "entity_type": "module",
                "ids": [module_id],
            },
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Trace"},
        ),
    )
    assert result.status == "executable"
    assert result.evidence_ids
    assert result.summary["total_kgCO2e"] > 0


def test_nonexistent_module_target_remains_unresolved(layerdedup_context) -> None:
    result = execute_canonical_query(
        layerdedup_context,
        program(
            {"op": "SelectClicked", "ids": ["ModularUnit:does-not-exist"]},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "Trace"},
        ),
    )
    assert result.status == "unresolved_target"


def test_unmarked_energy_source_query_remains_incomplete_path(context) -> None:
    result = execute_canonical_query(context, controlled_programs()[2][1])
    assert result.status == "incomplete_path"
    assert result.coverage_status == "relevant_rejection"
    assert result.summary["total_kgCO2e"] == pytest.approx(31.0)
    assert "emission:process" in result.emission_ids


def test_marked_project_energy_source_executes_with_rejection_disclosure(context) -> None:
    result = execute_canonical_query(
        context,
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {"op": "GroupBy", "keys": ["carrier"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    assert result.status == "executable"
    assert result.coverage_status == "relevant_rejection_disclosed"
    assert result.summary["excluded_record_count"] == 1
    assert result.summary["excluded_rejection_reasons"] == {"factor_not_found": 1}


def test_bare_rank_keeps_the_slice_total(context) -> None:
    """Ranking only orders the rows, so the reported total stays the slice total."""
    steps = (
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process", "known_total": True},
        {"op": "GroupBy", "keys": ["carrier"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    unranked = execute_canonical_query(context, program(*steps))
    ranked = execute_canonical_query(
        context, program(*steps, {"op": "Rank", "top_k": 1, "descending": True})
    )

    assert unranked.summary["total_kgCO2e"] == pytest.approx(31.0)
    assert ranked.summary["total_kgCO2e"] == pytest.approx(31.0)
    assert ranked.rows[0]["kgCO2e"] == pytest.approx(24.0)
    assert set(ranked.emission_ids) == set(unranked.emission_ids)


def test_trace_after_rank_reports_only_the_top_group(context) -> None:
    """Tracing the largest group cites that group's evidence, not everything."""
    steps = (
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process", "known_total": True},
        {"op": "GroupBy", "keys": ["carrier"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    whole_account = execute_canonical_query(context, program(*steps, {"op": "Trace"}))
    top_group = execute_canonical_query(
        context,
        program(
            *steps,
            {"op": "Rank", "top_k": 1, "descending": True},
            {"op": "Trace"},
        ),
    )

    assert whole_account.summary["total_kgCO2e"] == pytest.approx(31.0)
    assert top_group.summary["total_kgCO2e"] == pytest.approx(24.0)
    assert set(top_group.evidence_ids) < set(whole_account.evidence_ids)
    assert set(top_group.emission_ids) < set(whole_account.emission_ids)


@pytest.mark.parametrize("availability", ("not_available", "incomplete"))
def test_known_total_does_not_disclose_rejections_when_source_availability_is_incomplete(
    context, availability: str
) -> None:
    unavailable_context = replace(
        context,
        availability=immutable_mapping(
            {**context.availability, "energy": availability}
        ),
    )
    result = execute_canonical_query(
        unavailable_context,
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {"op": "GroupBy", "keys": ["carrier"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    assert result.status == "incomplete_path"
    assert result.coverage_status == availability
    assert "excluded_record_count" not in result.summary


@pytest.mark.parametrize(
    ("operation_steps", "expected_operation"),
    [
        (({"op": "Aggregate", "metric": "sum_kgCO2e"},), "aggregate"),
        (
            (
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ),
            "rank",
        ),
    ],
)
def test_class_known_total_discloses_only_rejections_in_the_selected_class(
    context, operation_steps, expected_operation: str
) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    beam_rejection = replace(
        rejected,
        record_id="record:rejected-beam",
        component_ids=("component:c1",),
    )
    column_rejection = replace(
        rejected,
        record_id="record:rejected-column",
        reason_code="thickness_missing",
        component_ids=("component:c2",),
    )
    scoped_context = replace(
        context,
        validation_coverage=replace(
            context.validation_coverage,
            records=(beam_rejection, column_rejection),
        ),
    )
    result = execute_canonical_query(
        scoped_context,
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {"op": "Filter", "field": "ifc_class", "equals": "IfcBeam"},
            {"op": "GroupBy", "keys": ["component"]},
            *operation_steps,
        ),
    )
    assert result.status == "executable"
    assert result.operation == expected_operation
    assert result.coverage_status == "relevant_rejection_disclosed"
    assert result.summary["excluded_record_count"] == 1
    assert result.summary["excluded_rejection_reasons"] == {"factor_not_found": 1}


def test_component_type_known_total_discloses_only_rejections_in_the_selected_type(
    context,
) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    structural_rejection = replace(
        rejected,
        record_id="record:rejected-structural",
        component_ids=("component:c4",),
    )
    other_type_rejection = replace(
        rejected,
        record_id="record:rejected-other-type",
        reason_code="thickness_missing",
        component_ids=("component:no-facts",),
    )
    scoped_context = replace(
        context,
        validation_coverage=replace(
            context.validation_coverage,
            records=(structural_rejection, other_type_rejection),
        ),
    )
    result = execute_canonical_query(
        scoped_context,
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {
                "op": "Filter",
                "field": "component_type",
                "equals": "type:structural",
            },
            {"op": "GroupBy", "keys": ["component"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    assert result.status == "executable"
    assert result.coverage_status == "relevant_rejection_disclosed"
    assert result.summary["excluded_record_count"] == 1
    assert result.summary["excluded_rejection_reasons"] == {"factor_not_found": 1}


def test_empty_class_known_total_does_not_disclose_unrelated_rejections(context) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    scoped_context = replace(
        context,
        validation_coverage=replace(
            context.validation_coverage,
            records=(replace(rejected, component_ids=("component:c2",)),),
        ),
    )
    result = execute_canonical_query(
        scoped_context,
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {"op": "Filter", "field": "ifc_class", "equals": "IfcDoesNotExist"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    assert result.status == "empty_result"
    assert result.coverage_status == "searched_complete"
    assert "excluded_record_count" not in result.summary


def test_selected_known_total_with_relevant_rejection_remains_incomplete(context) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    targeted = replace(rejected, component_ids=("component:c4",))
    index = replace(
        context.validation_coverage,
        records=tuple(
            targeted if row is rejected else row
            for row in context.validation_coverage.records
        ),
    )
    result = execute_canonical_query(
        replace(context, validation_coverage=index),
        program(
            {"op": "SelectClicked", "ids": ["component:c4"]},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
        selected_component_ids=("component:c4",),
    )
    assert result.status == "incomplete_path"
    assert result.coverage_status == "relevant_rejection"


def test_known_total_material_target_with_relevant_rejection_remains_incomplete(
    context,
) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    targeted = replace(
        rejected,
        kind="material",
        material_id="material:steel",
        carrier_id=None,
    )
    index = replace(
        context.validation_coverage,
        records=tuple(
            targeted if row is rejected else row
            for row in context.validation_coverage.records
        ),
    )
    result = execute_canonical_query(
        replace(context, validation_coverage=index),
        program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material", "known_total": True},
            {"op": "Filter", "field": "material", "equals": "material:steel"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    assert result.status == "incomplete_path"
    assert result.coverage_status == "relevant_rejection"


def test_permitted_partial_known_subtotal_never_widens_scope(context) -> None:
    query = controlled_programs()[2][1]
    strict = execute_canonical_query(context, query)
    ablated = execute_canonical_query(
        context, query, policy=QueryPolicy(allow_partial_known_subtotal=True)
    )
    assert ablated.status == "partial_known_subtotal"
    assert ablated.perspective == strict.perspective
    assert ablated.projection_keys == strict.projection_keys
    assert ablated.emission_ids == strict.emission_ids
    assert ablated.summary == strict.summary


def test_unknown_and_ambiguous_entities_have_distinct_statuses(context) -> None:
    unknown = execute_canonical_query(context, controlled_programs()[4][1])
    ambiguous_program = program(
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "name",
            "value": "Structural member",
            "cardinality": "singleton",
        },
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    ambiguous = execute_canonical_query(context, ambiguous_program)
    assert unknown.status == "unresolved_target"
    assert ambiguous.status == "clarification_required"


def test_target_hole_maps_to_clarification_required(context) -> None:
    """Mapping a live target hole back to incomplete_path must break this test."""
    query = CarbonQLProgram.from_dict(
        {
            "steps": [
                {
                    "op": "ResolveEntities",
                    "entity_type": "?",
                    "value": "that panel",
                },
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ],
            "holes": [
                {
                    "dimension": "target",
                    "candidates": ["component", "module"],
                }
            ],
        }
    )
    result = execute_canonical_query(context, query)
    assert result.status == "clarification_required"
    assert result.coverage_status == "ambiguous_target"
    assert result.summary["ambiguous_target"] == "that panel"


def test_mixed_known_unknown_ids_are_unresolved_but_alias_dedupe_is_singleton(context) -> None:
    mixed = program(
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1", "component:missing"],
            "cardinality": "singleton",
        },
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    aliases = program(
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1", "C1"],
            "cardinality": "singleton",
        },
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    assert execute_canonical_query(context, mixed).status == "unresolved_target"
    assert execute_canonical_query(context, aliases).status == "executable"


def test_disclosure_policy_cannot_change_entity_resolution_or_product_scope(context) -> None:
    query = controlled_programs()[4][1]
    strict = execute_canonical_query(context, query)
    ablated = execute_canonical_query(
        context, query, policy=QueryPolicy(allow_partial_known_subtotal=True)
    )
    assert strict.status == ablated.status == "unresolved_target"
    assert strict.projection_keys == ablated.projection_keys == ()


def test_valid_measured_zero_is_executable(context) -> None:
    result = execute_canonical_query(
        context,
        controlled_programs()[3][1],
        selected_component_ids=("component:c4",),
    )
    assert result.status == "executable"
    assert result.summary["total_kgCO2e"] == pytest.approx(0.0)
    assert result.emission_ids == ("emission:zero",)


def test_accepted_zero_with_relevant_rejection_preserves_zero_but_is_incomplete(
    context,
) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    targeted = replace(
        rejected,
        component_ids=("component:c4",),
        carrier_id="carrier:electricity",
        requested_scope=" a1 - a3 ",
    )
    index = replace(
        context.validation_coverage,
        records=tuple(
            targeted if row is rejected else row
            for row in context.validation_coverage.records
        ),
    )
    result = execute_canonical_query(
        replace(context, validation_coverage=index),
        controlled_programs()[3][1],
        selected_component_ids=("component:c4",),
    )
    assert result.status == "incomplete_path"
    assert result.coverage_status == "relevant_rejection"
    assert result.summary["total_kgCO2e"] == pytest.approx(0.0)
    assert result.emission_ids == ("emission:zero",)


def test_carrier_filter_ignores_typed_rejection_for_another_carrier(context) -> None:
    gas = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Filter", "field": "carrier", "equals": "carrier:gas"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    electricity = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process"},
        {
            "op": "Filter",
            "field": "carrier",
            "equals": "carrier:electricity",
        },
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    gas_result = execute_canonical_query(context, gas)
    electricity_result = execute_canonical_query(context, electricity)
    assert gas_result.status == "executable"
    assert gas_result.summary["total_kgCO2e"] == pytest.approx(7.0)
    assert electricity_result.status == "incomplete_path"
    assert electricity_result.summary["total_kgCO2e"] == pytest.approx(24.0)


def test_unique_material_property_selector_uses_canonical_id_for_rejection_filter(
    tmp_path: Path,
) -> None:
    unique_context = load_full_qa_context(
        write_task10_release(
            tmp_path / "unique-material-release",
            ambiguous_material_names=False,
        )
    )
    rejected = next(
        row for row in unique_context.validation_coverage.records if row.status == "rejected"
    )
    unrelated = replace(
        rejected,
        kind="material",
        material_id="material:unused",
        carrier_id=None,
        process_ids=(),
        recorded_scope=None,
        requested_scope=None,
    )
    index = replace(
        unique_context.validation_coverage,
        records=tuple(
            unrelated if row is rejected else row
            for row in unique_context.validation_coverage.records
        ),
    )
    query = program(
        {
            "op": "ResolveEntities",
            "entity_type": "material",
            "property": "name",
            "value": "Structural material",
            "cardinality": "singleton",
        },
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )

    result = execute_canonical_query(
        replace(unique_context, validation_coverage=index), query
    )

    assert result.status == "executable"
    assert result.coverage_status == "complete"
    assert result.summary["total_kgCO2e"] == pytest.approx(16.0)


def test_unique_process_property_selector_uses_canonical_id_for_rejection_filter(
    context,
) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    unrelated = replace(
        rejected,
        carrier_id=None,
        process_ids=("process:activity",),
        recorded_scope=None,
        requested_scope=None,
    )
    index = replace(
        context.validation_coverage,
        records=tuple(
            unrelated if row is rejected else row
            for row in context.validation_coverage.records
        ),
    )
    query = program(
        {
            "op": "ResolveEntities",
            "entity_type": "process",
            "property": "name",
            "value": "Stage",
            "cardinality": "singleton",
        },
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )

    result = execute_canonical_query(replace(context, validation_coverage=index), query)

    assert result.status == "executable"
    assert result.coverage_status == "complete"
    assert result.summary["total_kgCO2e"] == pytest.approx(7.0)


@pytest.mark.parametrize(
    "selector",
    (
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1", "C1"],
            "cardinality": "singleton",
        },
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "globalId",
            "value": "C1",
            "cardinality": "singleton",
        },
        {"op": "SelectClicked", "ids": ["C1"]},
    ),
)
def test_zero_match_component_scope_is_canonical_and_not_derived_from_rows(
    context, selector: dict[str, object]
) -> None:
    rejected = next(
        row for row in context.validation_coverage.records if row.status == "rejected"
    )
    query = program(
        selector,
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Filter", "field": "carrier", "equals": "carrier:gas"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )

    def execute_for(component_id: str):
        targeted = replace(
            rejected,
            component_ids=(component_id,),
            carrier_id="carrier:gas",
            recorded_scope=None,
            requested_scope=None,
        )
        index = replace(
            context.validation_coverage,
            records=tuple(
                targeted if row is rejected else row
                for row in context.validation_coverage.records
            ),
        )
        return execute_canonical_query(replace(context, validation_coverage=index), query)

    unrelated = execute_for("component:c2")
    relevant = execute_for("component:c1")

    assert unrelated.status == "empty_result"
    assert unrelated.coverage_status == "searched_complete"
    assert unrelated.summary["total_kgCO2e"] == pytest.approx(0.0)
    assert relevant.status == "incomplete_path"
    assert relevant.coverage_status == "relevant_rejection"
    assert relevant.summary["total_kgCO2e"] == pytest.approx(0.0)


def test_empty_dimension_grouping_does_not_crash(context) -> None:
    query = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Filter", "field": "factor_keyword", "equals": "not-present"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    result = execute_canonical_query(context, query)
    assert result.status == "empty_result"
    assert result.rows == ()


@pytest.mark.parametrize(
    ("searched_complete", "rejections", "accepted_zero", "expected"),
    [
        (False, 0, False, "incomplete_path"),
        (False, 0, True, "incomplete_path"),
        (True, 1, False, "incomplete_path"),
        (True, 1, True, "incomplete_path"),
        (True, 0, False, "empty_result"),
        (True, 0, True, "executable"),
    ],
)
def test_actual_like_zero_requires_coverage_evidence(
    searched_complete: bool, rejections: int, accepted_zero: bool, expected: str
) -> None:
    assert status_for_availability(
        match_count=0,
        searched_context_complete=searched_complete,
        relevant_rejection_count=rejections,
        accepted_zero=accepted_zero,
    ) == expected


def test_actual_like_no_energy_release_never_reports_executable_zero(tmp_path: Path) -> None:
    release = write_actual_like_no_energy_release(tmp_path / "actual-like")
    actual_like = load_full_qa_context(release)
    assert actual_like.availability["energy"] == "not_available"
    process_query = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = execute_canonical_query(actual_like, process_query)
    assert result.status == "incomplete_path"
    assert result.coverage_status == "not_available"
    assert "total_kgCO2e" not in result.summary
