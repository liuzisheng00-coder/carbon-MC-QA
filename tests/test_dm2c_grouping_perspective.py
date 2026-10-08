"""Grouping labels must not change the records selected by product filters."""

from pathlib import Path
from dataclasses import replace

import pytest

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, derive_view_signature
from dm2c_carbonql_executor import CarbonQLExecutor
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec, reference_evaluate
from dm2c_carbonql_service import CarbonQLService, answer_payload
from dm2c_full_qa_experiment_runner import load_full_qa_context, execute_canonical_query
from dm2c_qa_answer_adapter import adapt_observed
from tests.task10_v2_fixture import write_task10_release
from tests.test_dm2c_carbonql_service import StubLLMClient


@pytest.fixture()
def release(tmp_path: Path) -> Path:
    return write_task10_release(tmp_path / "release")


def grouped_query(source: str, field: str, value: str, groups: list[str]) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": source, "known_total": True},
        {"op": "Filter", "field": field, "equals": value},
        {"op": "GroupBy", "keys": groups},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]})


@pytest.mark.parametrize(
    ("source", "field", "value", "groups", "views", "total"),
    [
        ("material", "component", "component:c1", ["material"], ("material",), 10.0),
        ("material", "component_type", "type:structural", ["material"], ("material",), 17.0),
        ("process", "component", "component:c1", ["carrier"], ("process",), 5.0),
        ("process", "ifc_class", "IfcColumn", ["carrier"], ("process",), 15.0),
        ("all", "component_type", "type:structural", ["material", "carrier"], ("material", "process"), 37.0),
        ("all", "component_type", "type:structural", ["source_kind"], ("material", "process"), 37.0),
    ],
)
def test_grouping_sets_view_while_product_filter_keeps_attributed_values(
    release: Path, source: str, field: str, value: str,
    groups: list[str], views: tuple[str, ...], total: float,
) -> None:
    query = grouped_query(source, field, value, groups)
    result = CarbonQLExecutor.from_context(load_canonical_v2_context(release)).execute(query)

    assert derive_view_signature(query).base_views == views
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(total)
    assert result.summary["record_projection_perspective"] == "product"
    assert all(key[0] != "source" for key in result.projection_keys)
    assert "emission:process" not in result.emission_ids


def test_redundant_product_filter_preserves_material_view_and_evidence(release: Path) -> None:
    executor = CarbonQLExecutor.from_context(load_canonical_v2_context(release))
    steps = [
        {"op": "SelectClicked", "ids": ["component:c1"]},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
        {"op": "Trace"},
    ]
    queries = [CarbonQLProgram.from_dict({"steps": steps})]
    queries.append(CarbonQLProgram.from_dict({"steps": [
        *steps[:2], {"op": "Filter", "field": "component", "equals": "component:c1"}, *steps[2:],
    ]}))
    plain, filtered = [executor.execute(query) for query in queries]

    for query, result in zip(queries, (plain, filtered)):
        assert derive_view_signature(query).base_views == ("material",)
        assert result.summary["projection_perspective"] == "material_source"
        assert result.rows[0]["material"] == "material:steel"
        assert result.rows[0]["kgCO2e"] == pytest.approx(10.0)
    assert filtered.rows == plain.rows
    assert filtered.projection_keys == plain.projection_keys
    assert filtered.emission_ids == plain.emission_ids
    assert plain.trace_rows
    assert filtered.trace_rows == plain.trace_rows


def test_material_grouping_does_not_hide_energy_rejections_in_product_scope(release: Path) -> None:
    context = load_full_qa_context(release)
    # Assign the fixture's rejected energy input to the selected component.
    # The query reads both sources even though its answer is grouped by material.
    coverage = context.validation_coverage
    context = replace(context, validation_coverage=replace(coverage, records=tuple(
        replace(row, component_ids=("component:c1",)) if row.status == "rejected" else row
        for row in coverage.records
    )))
    query = grouped_query("all", "component", "component:c1", ["material"])
    result = execute_canonical_query(context, query)

    assert result.perspective == "material_source"
    assert result.status == "incomplete_path"
    assert result.coverage_status == "relevant_rejection"
    assert result.summary["total_kgCO2e"] == pytest.approx(15.0)
    assert result.summary["blocked_status"] == "factor_not_found"
    assert {row["material"]: row["kgCO2e"] for row in result.rows} == {
        "material:steel": pytest.approx(10.0), None: pytest.approx(5.0),
    }


def test_reference_keeps_product_allocation_with_material_answer_label(release: Path) -> None:
    context = load_canonical_v2_context(release)
    query = grouped_query("all", "component_type", "type:structural", ["material"])
    case = CarbonQLCase(
        case_id="filtered-material", question="Carbon by material within structural components",
        category="grouped", gold_program=query, expected_compiler_status="valid",
        reference=ReferenceSpec(projection_perspective="product", sources=("material", "process")),
    )
    result = reference_evaluate(case, context)
    actual = CarbonQLExecutor.from_context(context).execute(query)

    assert result.summary["projection_perspective"] == "material_source"
    assert result.summary["record_projection_perspective"] == "product"
    assert result.summary["total_kgCO2e"] == pytest.approx(37.0)
    assert {row["material"]: row["kgCO2e"] for row in result.rows} == {
        "material:steel": pytest.approx(16.0), "material:unused": pytest.approx(1.0), None: pytest.approx(20.0),
    }
    assert result.rows == actual.rows
    assert set(result.projection_keys) == set(actual.projection_keys)
    assert result.emission_ids == actual.emission_ids


def test_service_reports_material_view_with_filtered_product_values(release: Path) -> None:
    query = grouped_query("material", "component_type", "type:structural", ["material"])
    service = CarbonQLService.from_release(release, StubLLMClient(query.to_dict()))
    answer = service.answer("Within the structural selection, show material carbon by material.")
    payload = answer_payload(answer)

    assert answer.answered
    assert answer.perspective == "material"
    assert answer.source_scope == ("material",)
    assert answer.total_kgCO2e == pytest.approx(17.0)
    assert "perspective: material; source scope: material" in payload["answer"]


@pytest.mark.parametrize("group", ["material", "carrier"])
def test_answer_adapter_does_not_rename_mixed_total_as_one_source(
    release: Path, group: str,
) -> None:
    context = load_full_qa_context(release)
    query = grouped_query("all", "component_type", "type:structural", [group])
    result = execute_canonical_query(context, query)
    observed = adapt_observed(
        question="查看所选范围的分类碳排放", program=query, result=result, context=context,
    )

    assert observed["status"] == "executable"
    assert observed["summary"]["total_kgCO2e"] == pytest.approx(37.0)
    assert observed["summary"]["source_scope"] == "material+process"
    assert "totalMaterialCarbon_kgCO2e" not in observed["summary"]
    assert "knownProcessCarbon_kgCO2e" not in observed["summary"]
    assert "material_families" not in observed["summary"]


@pytest.mark.parametrize("operation", ["Rank", "Compare"])
def test_mixed_source_summary_preserves_ranked_and_compared_values(
    release: Path, operation: str,
) -> None:
    context = load_full_qa_context(release)
    payload = grouped_query("all", "component_type", "type:structural", ["material"]).to_dict()
    step = {"op": operation}
    if operation == "Rank":
        step["top_k"] = 1
    payload["steps"].append(step)
    query = CarbonQLProgram.from_dict(payload)
    result = execute_canonical_query(context, query)
    observed = adapt_observed(
        question="查看所选范围的分类碳排放", program=query, result=result, context=context,
    )

    assert observed["status"] == "executable"
    assert observed["summary"]["total_kgCO2e"] == pytest.approx(37.0)
    assert observed["summary"]["rows"][0]["kgCO2e"] == pytest.approx(20.0)
    assert 20.0 in observed["supported_numeric_values"]
    if operation == "Rank":
        assert len(observed["summary"]["rows"]) == 1
    else:
        assert observed["summary"]["rows"][1]["kgCO2e"] == pytest.approx(16.0)
        assert observed["summary"]["difference_kgCO2e"] == pytest.approx(4.0)


@pytest.mark.parametrize("invalid_value", ["missing", None])
def test_mixed_comparison_does_not_substitute_zero_for_a_missing_measure(
    release: Path, invalid_value: str | None,
) -> None:
    context = load_full_qa_context(release)
    payload = grouped_query("all", "component_type", "type:structural", ["material"]).to_dict()
    payload["steps"].append({"op": "Compare"})
    query = CarbonQLProgram.from_dict(payload)
    result = execute_canonical_query(context, query)
    rows = [dict(row) for row in result.rows]
    if invalid_value == "missing":
        del rows[0]["kgCO2e"]
    else:
        rows[0]["kgCO2e"] = invalid_value
    broken_result = replace(result, rows=tuple(rows))

    # A successful executor result must carry a numeric measure in each row.
    # An internal contract violation must not become a plausible carbon value.
    with pytest.raises((KeyError, TypeError)):
        adapt_observed(
            question="查看所选范围的分类碳排放", program=query,
            result=broken_result, context=context,
        )
