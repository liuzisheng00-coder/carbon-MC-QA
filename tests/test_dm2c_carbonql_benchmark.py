from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path

import pytest

import dm2c_carbonql_benchmark as benchmark_module
from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, CarbonQLValidationError
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec, reference_evaluate
from dm2c_carbonql_executor import CarbonQLExecutor
from tests.task10_v2_fixture import write_task10_release


def make_case(case_id: str, steps: list[dict], perspective: str) -> CarbonQLCase:
    return CarbonQLCase(
        case_id=case_id,
        question=case_id,
        category="controlled-v2",
        gold_program=CarbonQLProgram.from_dict({"steps": steps}),
        expected_compiler_status="valid",
        reference=ReferenceSpec(projection_perspective=perspective),
    )


def test_reference_perspective_is_explicit_and_reconciles_exactly(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        make_case("product", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("material", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"material"},{"op":"GroupBy","keys":["material"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "material_source"),
        make_case("carrier", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"process"},{"op":"GroupBy","keys":["carrier"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "energy_source"),
        make_case("union", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"GroupBy","keys":["material","process"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "source_union"),
    ]
    executor = CarbonQLExecutor.from_context(context)
    expected = [43.0, 19.0, 31.0, 50.0]
    for case, total in zip(cases, expected):
        reference = reference_evaluate(case, context)
        observed = executor.execute(case.gold_program)
        assert reference.to_dict() == observed.to_dict()
        assert reference.summary["total_kgCO2e"] == pytest.approx(total)


def test_reference_evaluator_does_not_share_executor_aggregation() -> None:
    source = inspect.getsource(benchmark_module)
    assert "dm2c_carbonql_executor" not in source
    assert "CarbonQLExecutor" not in source


def test_reference_spec_rejects_ambiguous_or_legacy_perspective() -> None:
    with pytest.raises(ValueError, match="projection_perspective"):
        ReferenceSpec()
    with pytest.raises(ValueError, match="projection_perspective"):
        ReferenceSpec(projection_perspective="atom")


def test_reference_reconciles_type_ifc_class_and_resource_context(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        make_case(
            "type",
            [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"GroupBy","keys":["component_type"]},{"op":"Aggregate","metric":"sum_kgCO2e"}],
            "product",
        ),
        make_case(
            "beam",
            [{"op":"ResolveEntities","entity_type":"component","property":"ifcClass","value":"IfcBeam","cardinality":"set"},{"op":"CarbonAtoms","source":"all"},{"op":"Aggregate","metric":"sum_kgCO2e"}],
            "product",
        ),
        make_case(
            "resource",
            [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"process"},{"op":"GroupBy","keys":["resource"]},{"op":"Aggregate","metric":"sum_kgCO2e"}],
            "energy_source",
        ),
    ]
    executor = CarbonQLExecutor.from_context(context)
    for case in cases:
        assert reference_evaluate(case, context).to_dict() == executor.execute(case.gold_program).to_dict()


def test_reference_reconciles_cross_source_filters_and_multi_entity_sets(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        make_case("material-carrier-empty", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"material"},{"op":"GroupBy","keys":["carrier"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "energy_source"),
        make_case("process-material-empty", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"process"},{"op":"GroupBy","keys":["material"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "material_source"),
        make_case("product-filter", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"Filter","field":"component_type","equals":"type:structural"},{"op":"GroupBy","keys":["material"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("all-materials", [{"op":"ResolveEntities","entity_type":"material","ids":["material:steel","material:unused"],"cardinality":"set"},{"op":"CarbonAtoms","source":"material"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("all-materials-by-name", [{"op":"ResolveEntities","entity_type":"material","property":"name","value":"Structural material","cardinality":"set"},{"op":"CarbonAtoms","source":"material"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("stage", [{"op":"ResolveEntities","entity_type":"process","ids":["process:stage"],"cardinality":"set"},{"op":"CarbonAtoms","source":"process"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "energy_source"),
    ]
    executor = CarbonQLExecutor.from_context(context)
    expected_totals = [0.0, 0.0, 37.0, 19.0, 19.0, 7.0]
    for case, total in zip(cases, expected_totals):
        reference = reference_evaluate(case, context)
        assert reference.to_dict() == executor.execute(case.gold_program).to_dict()
        assert reference.summary["total_kgCO2e"] == pytest.approx(total)


def test_reference_reconciles_source_neutral_factor_dimensions(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        make_case("factor-material", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"material"},{"op":"GroupBy","keys":["factor_source"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "material_source"),
        make_case("factor-process", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"process"},{"op":"GroupBy","keys":["factor_keyword"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "energy_source"),
        make_case("factor-all", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"GroupBy","keys":["factor_keyword"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "source_union"),
    ]
    executor = CarbonQLExecutor.from_context(context)
    for case, total in zip(cases, (19.0, 31.0, 50.0)):
        reference = reference_evaluate(case, context)
        assert reference.to_dict() == executor.execute(case.gold_program).to_dict()
        assert reference.summary["total_kgCO2e"] == pytest.approx(total)


def test_reference_reconciles_non_product_filter_perspectives(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        make_case("material-filter", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"material"},{"op":"Filter","field":"material","equals":"material:steel"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "material_source"),
        make_case("process-filter", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"process"},{"op":"Filter","field":"carrier","equals":"carrier:gas"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "energy_source"),
    ]
    executor = CarbonQLExecutor.from_context(context)
    for case, total in zip(cases, (16.0, 7.0)):
        reference = reference_evaluate(case, context)
        assert reference.to_dict() == executor.execute(case.gold_program).to_dict()
        assert reference.summary["total_kgCO2e"] == pytest.approx(total)

    filter_cases = [
        make_case("factor-filter-process", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"process"},{"op":"Filter","field":"factor_keyword","equals":"natural gas"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "energy_source"),
        make_case("factor-filter-all", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"Filter","field":"factor_source","equals":"controlled-fixture"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "source_union"),
    ]
    for case, total in zip(filter_cases, (7.0, 50.0)):
        reference = reference_evaluate(case, context)
        assert reference.to_dict() == executor.execute(case.gold_program).to_dict()
        assert reference.summary["total_kgCO2e"] == pytest.approx(total)


def test_reference_reconciles_component_cardinality(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        make_case("component-id-set", [{"op":"ResolveEntities","entity_type":"component","ids":["component:c1","component:c2"],"cardinality":"set"},{"op":"CarbonAtoms","source":"all"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("component-id-singleton", [{"op":"ResolveEntities","entity_type":"component","ids":["component:c1","component:c2"],"cardinality":"singleton"},{"op":"CarbonAtoms","source":"all"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("component-property-set", [{"op":"ResolveEntities","entity_type":"component","property":"name","value":"Structural member","cardinality":"set"},{"op":"CarbonAtoms","source":"all"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
        make_case("component-property-singleton", [{"op":"ResolveEntities","entity_type":"component","property":"name","value":"Structural member","cardinality":"singleton"},{"op":"CarbonAtoms","source":"all"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "product"),
    ]
    executor = CarbonQLExecutor.from_context(context)
    expected = [("ok", 36.0), ("unresolved_target", None), ("ok", 36.0), ("unresolved_target", None)]
    for case, (status, total) in zip(cases, expected):
        reference = reference_evaluate(case, context)
        observed = executor.execute(case.gold_program)
        assert reference.status == observed.status == status
        if total is None:
            assert "total_kgCO2e" not in reference.summary
            assert "total_kgCO2e" not in observed.summary
        else:
            assert reference.summary["total_kgCO2e"] == observed.summary["total_kgCO2e"] == pytest.approx(total)


@pytest.mark.parametrize(
    ("entity_type", "ids", "source", "perspective", "total"),
    [
        ("component", ["component:c1", "C1", "component:c1"], "all", "product", 15.0),
        ("material", ["material:steel", "material:steel"], "material", "material_source", 16.0),
        ("process", ["process:stage", "process:stage"], "process", "energy_source", 7.0),
    ],
)
def test_reference_exact_ids_deduplicate_before_singleton_cardinality(
    tmp_path: Path,
    entity_type: str,
    ids: list[str],
    source: str,
    perspective: str,
    total: float,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = make_case(
        "dedupe",
        [
            {"op":"ResolveEntities","entity_type":entity_type,"ids":ids,"cardinality":"singleton"},
            {"op":"CarbonAtoms","source":source},
            {"op":"Aggregate","metric":"sum_kgCO2e"},
        ],
        perspective,
    )
    result = reference_evaluate(case, context)
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(total)


@pytest.mark.parametrize("cardinality", ["set", "singleton"])
@pytest.mark.parametrize(
    ("entity_type", "ids", "source", "perspective"),
    [
        ("component", ["component:c1", "component:missing"], "all", "product"),
        ("material", ["material:steel", "material:missing"], "material", "material_source"),
        ("process", ["process:stage", "process:missing"], "process", "energy_source"),
    ],
)
def test_reference_exact_ids_fail_closed_when_any_token_is_unknown(
    tmp_path: Path,
    entity_type: str,
    ids: list[str],
    source: str,
    perspective: str,
    cardinality: str,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = make_case(
        "mixed-unknown",
        [
            {"op":"ResolveEntities","entity_type":entity_type,"ids":ids,"cardinality":cardinality},
            {"op":"CarbonAtoms","source":source},
            {"op":"Aggregate","metric":"sum_kgCO2e"},
        ],
        perspective,
    )
    result = reference_evaluate(case, context)
    assert result.status == "unresolved_target"
    assert "total_kgCO2e" not in result.summary


def test_component_mixed_unknown_reference_and_executor_coverage_are_exact(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = make_case(
        "component-mixed",
        [
            {"op":"ResolveEntities","entity_type":"component","ids":["component:c1","component:missing"],"cardinality":"set"},
            {"op":"CarbonAtoms","source":"all"},
            {"op":"Aggregate","metric":"sum_kgCO2e"},
        ],
        "product",
    )
    reference = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(case.gold_program)
    assert reference.to_dict() == observed.to_dict()
    assert reference.coverage == {
        "accepted_projection_count": 0,
        "accepted_emission_count": 0,
        "rejected_count": 1,
        "requested_component_count": 2,
        "covered_component_ids": ("component:c1",),
        "missing_component_ids": ("component:missing",),
    }


def test_reference_select_clicked_fails_closed_for_mixed_step_ids(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = make_case(
        "clicked-mixed",
        [
            {"op":"SelectClicked","ids":["component:c1","component:missing","component:c1"]},
            {"op":"CarbonAtoms","source":"all"},
            {"op":"Aggregate","metric":"sum_kgCO2e"},
        ],
        "product",
    )
    result = reference_evaluate(case, context)
    assert result.status == "unresolved_target"
    assert result.coverage["covered_component_ids"] == ("component:c1",)
    assert result.coverage["missing_component_ids"] == ("component:missing",)


def test_reference_select_clicked_without_step_or_runtime_ids_is_unresolved(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = make_case(
        "clicked-without-context",
        [
            {"op":"SelectClicked"},
            {"op":"CarbonAtoms","source":"all"},
            {"op":"Aggregate","metric":"sum_kgCO2e"},
        ],
        "product",
    )
    reference = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(case.gold_program)
    assert reference.to_dict() == observed.to_dict()
    assert reference.status == "unresolved_target"


def _selector_reference_case(
    case_id: str, selector: dict, reference_ids: tuple[str, ...]
) -> CarbonQLCase:
    return CarbonQLCase(
        case_id=case_id,
        question=case_id,
        category="controlled-v2",
        gold_program=CarbonQLProgram.from_dict(
            {
                "steps": [
                    selector,
                    {"op": "CarbonAtoms", "source": "all"},
                    {"op": "Aggregate", "metric": "sum_kgCO2e"},
                ]
            }
        ),
        expected_compiler_status="valid",
        reference=ReferenceSpec(
            projection_perspective="product",
            selector_component_ids=reference_ids,
        ),
    )


def _partial_selector_reference_case(
    case_id: str, selector: dict, reference_ids: tuple[str, ...]
) -> CarbonQLCase:
    return CarbonQLCase(
        case_id=case_id,
        question=case_id,
        category="controlled-v2",
        gold_program=CarbonQLProgram.from_dict(
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
        ),
        expected_compiler_status="partial",
        reference=ReferenceSpec(
            projection_perspective="product",
            selector_component_ids=reference_ids,
        ),
    )


def test_reference_rejects_conflicting_clicked_program_and_external_ids(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _selector_reference_case(
        "selector-conflict",
        {"op": "SelectClicked", "ids": ["component:c1"]},
        ("component:c2",),
    )
    with pytest.raises(ValueError, match="selector conflict"):
        reference_evaluate(case, context)


def test_partial_reference_still_rejects_conflicting_clicked_ids(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _partial_selector_reference_case(
        "partial-selector-conflict",
        {"op": "SelectClicked", "ids": ["component:c1"]},
        ("component:c2",),
    )
    with pytest.raises(ValueError, match="selector conflict"):
        reference_evaluate(case, context)


@pytest.mark.parametrize(
    "selector",
    [
        {"op": "SelectProject"},
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1"],
            "cardinality": "singleton",
        },
    ],
)
def test_partial_reference_rejects_external_ids_for_non_clicked_selectors(
    tmp_path: Path, selector: dict
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _partial_selector_reference_case(
        "partial-invalid-external-context", selector, ("component:c2",)
    )
    with pytest.raises(ValueError, match="SelectClicked"):
        reference_evaluate(case, context)


def test_partial_reference_preserves_holes_and_matches_executor_context(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _partial_selector_reference_case(
        "partial-external-context", {"op": "SelectClicked"}, ("component:c2",)
    )
    expected = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(
        case.gold_program, case.reference.selector_component_ids
    )
    assert expected.to_dict() == observed.to_dict()
    assert expected.status == "partial"
    assert expected.holes == case.gold_program.holes


def test_partial_reference_preserves_unresolved_target_selector_hole(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = CarbonQLCase(
        case_id="partial-target-selector",
        question="partial-target-selector",
        category="controlled-v2",
        gold_program=CarbonQLProgram.from_dict(
            {
                "steps": [
                    {
                        "op": "ResolveEntities",
                        "entity_type": "?",
                        "ids": ["component:c1"],
                        "cardinality": "singleton",
                    },
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
        ),
        expected_compiler_status="partial",
        reference=ReferenceSpec(projection_perspective="product"),
    )
    expected = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(case.gold_program)
    assert expected.to_dict() == observed.to_dict()
    assert expected.status == "partial"
    assert expected.holes == case.gold_program.holes


@pytest.mark.parametrize(
    ("selector", "perspective"),
    [
        (
            {
                "op": "ResolveEntities",
                "entity_type": "component",
                "ids": ["?"],
                "cardinality": "set",
            },
            "product",
        ),
        (
            {
                "op": "ResolveEntities",
                "entity_type": "material",
                "ids": ["?"],
                "cardinality": "set",
            },
            "material_source",
        ),
        (
            {
                "op": "ResolveEntities",
                "entity_type": "component",
                "property": "name",
                "value": "?",
                "cardinality": "set",
            },
            "product",
        ),
    ],
)
def test_literal_question_mark_selector_reconciles_as_unresolved_target(
    tmp_path: Path, selector: dict, perspective: str
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _selector_reference_case("literal-question", selector, ())
    case = replace(
        case,
        reference=replace(case.reference, projection_perspective=perspective),
    )
    expected = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(case.gold_program)
    assert expected.to_dict() == observed.to_dict()
    assert expected.status == "unresolved_target"


def test_literal_question_mark_selector_precedes_source_partial_in_both_oracles(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _partial_selector_reference_case(
        "literal-question-source-partial",
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["?"],
            "cardinality": "set",
        },
        (),
    )
    expected = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(case.gold_program)
    assert expected.to_dict() == observed.to_dict()
    assert expected.status == "unresolved_target"
    assert expected.holes == ()


@pytest.mark.parametrize("ids", [[], [" "], [1]])
def test_partial_reference_applies_same_clicked_id_grammar_as_executor(
    tmp_path: Path, ids: list[object]
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _partial_selector_reference_case(
        "partial-invalid-clicked-ids",
        {"op": "SelectClicked", "ids": ids},
        ("component:c1",),
    )
    with pytest.raises(CarbonQLValidationError) as executor_error:
        CarbonQLExecutor.from_context(context).execute(
            case.gold_program, case.reference.selector_component_ids
        )
    with pytest.raises(CarbonQLValidationError) as reference_error:
        reference_evaluate(case, context)
    assert reference_error.value.to_dict() == executor_error.value.to_dict()


def test_reference_accepts_canonically_equal_clicked_program_and_external_ids(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _selector_reference_case(
        "selector-equivalent",
        {"op": "SelectClicked", "ids": ["component:c1"]},
        ("C1", "component:c1", "C1"),
    )
    result = reference_evaluate(case, context)
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(15.0)


def test_reference_rejects_unknown_external_ids_that_conflict_with_gold_ids(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _selector_reference_case(
        "selector-unknown-conflict",
        {"op": "SelectClicked", "ids": ["component:c1"]},
        ("component:missing",),
    )
    with pytest.raises(ValueError, match="selector conflict"):
        reference_evaluate(case, context)


def test_reference_uses_external_ids_for_clicked_program_without_ids(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _selector_reference_case(
        "selector-external",
        {"op": "SelectClicked"},
        ("component:c2",),
    )
    result = reference_evaluate(case, context)
    assert result.status == "ok"
    assert result.summary["total_kgCO2e"] == pytest.approx(21.0)


@pytest.mark.parametrize(
    "selector",
    [
        {"op": "SelectProject"},
        {
            "op": "ResolveEntities",
            "entity_type": "component",
            "ids": ["component:c1"],
            "cardinality": "singleton",
        },
    ],
)
def test_reference_rejects_external_component_ids_for_non_clicked_selectors(
    tmp_path: Path, selector: dict
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = _selector_reference_case(
        "external-context-wrong-selector", selector, ("component:c2",)
    )
    with pytest.raises(ValueError, match="SelectClicked"):
        reference_evaluate(case, context)


@pytest.mark.parametrize(
    ("selector", "dimension", "perspective", "total"),
    [
        ({"op":"ResolveEntities","entity_type":"material","ids":["material:steel"],"cardinality":"set"}, "carrier", "energy_source", 16.0),
        ({"op":"ResolveEntities","entity_type":"process","ids":["process:stage"],"cardinality":"set"}, "material", "material_source", 7.0),
        ({"op":"ResolveEntities","entity_type":"material","ids":["material:steel"],"cardinality":"set"}, "factor_source", "source_union", 16.0),
        ({"op":"ResolveEntities","entity_type":"process","ids":["process:stage"],"cardinality":"set"}, "factor_source", "source_union", 7.0),
    ],
)
def test_reference_reconciles_dimension_driven_perspective_under_entity_scope(
    tmp_path: Path,
    selector: dict,
    dimension: str,
    perspective: str,
    total: float,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = make_case(
        "combined-intent",
        [selector,{"op":"CarbonAtoms","source":"all"},{"op":"GroupBy","keys":[dimension]},{"op":"Aggregate","metric":"sum_kgCO2e"}],
        perspective,
    )
    reference = reference_evaluate(case, context)
    observed = CarbonQLExecutor.from_context(context).execute(case.gold_program)
    assert reference.to_dict() == observed.to_dict()
    assert reference.summary["total_kgCO2e"] == pytest.approx(total)


def test_reference_reconciles_source_kind_filter_group_and_conflict(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    cases = [
        (make_case("filtered", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"Filter","field":"source_kind","equals":"process"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "source_union"), 31.0),
        (make_case("filtered-group", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"Filter","field":"source_kind","equals":"process"},{"op":"GroupBy","keys":["source_kind"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "source_union"), 31.0),
        (make_case("group-all", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"all"},{"op":"GroupBy","keys":["source_kind"]},{"op":"Aggregate","metric":"sum_kgCO2e"}], "source_union"), 50.0),
        (make_case("conflict", [{"op":"SelectProject"},{"op":"CarbonAtoms","source":"material"},{"op":"Filter","field":"source_kind","equals":"process"},{"op":"Aggregate","metric":"sum_kgCO2e"}], "material_source"), 0.0),
    ]
    executor = CarbonQLExecutor.from_context(context)
    for case, total in cases:
        reference = reference_evaluate(case, context)
        observed = executor.execute(case.gold_program)
        assert reference.to_dict() == observed.to_dict()
        assert reference.summary["total_kgCO2e"] == pytest.approx(total)
