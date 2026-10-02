"""Tests for deterministic CarbonQL normalization and M3 answer adaptation."""

from __future__ import annotations

import pytest

from dm2c_carbonql import CarbonQLProgram, GraphSchema, validate_program
from dm2c_carbonql_program_normalize import normalize_program
from dm2c_carbonql_synthesizer import _argument_contract, _attempt
from dm2c_m3_context import CanonicalQueryResult
from dm2c_qa_answer_adapter import adapt_observed, infer_operation, map_perspective


def _schema() -> GraphSchema:
    return GraphSchema(
        labels=frozenset({"BuildingComponent", "IfcMaterial", "Project"}),
        relations=frozenset({"hasMaterial"}),
        dimension_counts={
            "project": 1,
            "component": 2,
            "material": 1,
            "module": 1,
            "component_type": 1,
            "ifc_class": 1,
            "carrier": 1,
            "process": 1,
            "stage": 1,
            "resource": 1,
            "factor_keyword": 1,
            "factor_source": 1,
            "source_kind": 1,
        },
        carbon_sources=frozenset({"material", "process"}),
    )


def _result(
    *,
    status: str = "executable",
    coverage_status: str = "complete",
    perspective: str = "product",
    operation: str = "trace",
    rows=(),
    summary=None,
    emission_ids=(),
    evidence_ids=(),
) -> CanonicalQueryResult:
    return CanonicalQueryResult(
        status=status,
        coverage_status=coverage_status,
        perspective=perspective,
        operation=operation,
        rows=tuple(rows),
        summary=dict(summary or {}),
        emission_ids=tuple(emission_ids),
        projection_keys=(),
        evidence_ids=tuple(evidence_ids),
    )


def test_filter_predicate_rewrites_to_equals():
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {
                    "op": "Filter",
                    "field": "material",
                    "predicate": "Steel",
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(program)
    filt = fixed.steps[2]
    assert "predicate" not in filt.args
    assert filt.args["equals"] == "Steel"
    validate_program(fixed, _schema())


def test_filter_predicate_list_rewrites_to_in():
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {
                    "op": "Filter",
                    "field": "material",
                    "predicate": ["Steel", "Concrete"],
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(program)
    assert fixed.steps[2].args["in"] == ["Steel", "Concrete"]
    validate_program(fixed, _schema())


def test_selector_moved_to_first_step():
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "SelectProject"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(program)
    assert fixed.steps[0].op == "SelectProject"
    assert [step.op for step in fixed.steps].count("SelectProject") == 1
    validate_program(fixed, _schema())


def test_known_total_elicited_for_project_known_question():
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": ["material", "process"]},
                {"op": "GroupBy", "keys": ["material", "carrier"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    question = "What is the project's known carbon total, split into materials and factory energy?"
    fixed = normalize_program(program, question=question)
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True
    assert all(step.op != "GroupBy" for step in fixed.steps)
    validate_program(fixed, _schema())


def test_attempt_applies_normalize_before_validate():
    raw = """
    {"steps":[
      {"op":"CarbonAtoms","source":"material"},
      {"op":"SelectProject"},
      {"op":"Filter","field":"material","predicate":"Steel"},
      {"op":"Aggregate","metric":"sum_kgCO2e"}
    ]}
    """
    program, validation, error = _attempt(
        raw,
        _schema(),
        question="What known material carbon is available for the project?",
    )
    assert error == {}
    assert validation is not None
    assert program is not None
    assert program.steps[0].op == "SelectProject"
    assert program.steps[2].args.get("equals") == "Steel"
    assert any(
        step.op == "CarbonAtoms" and step.args.get("known_total") is True
        for step in program.steps
    )


def test_argument_contract_no_longer_teaches_predicate_key():
    contract = _argument_contract()
    assert "predicate" not in contract["Filter"]
    assert "known_total" in contract["CarbonAtoms"]


def test_material_known_aggregate_keeps_material_groupby():
    """Product-total cleanup must not strip material-family GroupBy."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Aggregate the known material carbon by material family for the current project.",
    )
    assert any(
        step.op == "GroupBy" and step.args.get("keys") == ["material"]
        for step in fixed.steps
    )
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True


def test_largest_explain_is_not_labeled_rank():
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Trace"},
            ]
        }
    )
    assert (
        infer_operation(
            "Explain the largest steel material record and supporting evidence.",
            program,
        )
        == "explain"
    )

    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": ["material", "process"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Can you aggregate the supported project carbon records into material and factory energy totals?",
    )
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True


def test_perspective_and_operation_mapping():
    assert map_perspective("material_source") == "material"
    assert map_perspective("energy_source") == "process"
    assert map_perspective("source_union") == "product"
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["c1"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
            ]
        }
    )
    assert (
        infer_operation(
            "For the selected BIM item, explain its known carbon contribution.",
            program,
        )
        == "explain"
    )
    assert (
        infer_operation(
            "For the selected BIM item, trace how its known carbon contribution is calculated.",
            program,
        )
        == "trace"
    )
    assert (
        infer_operation(
            "Describe the evidence behind the selected BIM item's carbon contribution.",
            program,
        )
        == "explain"
    )


def test_adapter_maps_generic_evidence_chain_to_traceability_summary():
    """Removing the question-based perspective inference must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["component:c1"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
            ]
        }
    )
    evidence_ids = tuple(f"evidence:{index}" for index in range(11))
    observed = adapt_observed(
        question=(
            "Trace the selected carbon record back to its quantity, factor, "
            "source item, and target."
        ),
        program=program,
        result=_result(
            perspective="product",
            summary={"total_kgCO2e": 16.199096},
            evidence_ids=evidence_ids,
        ),
    )
    assert observed["perspective"] == "product"
    assert observed["operation"] == "trace"
    assert observed["summary"] == {
        "traced_atomic_emission_kgCO2e": 16.199096,
        "evidence_hops": 11,
    }


def test_explicit_explain_intent_precedes_internal_rank_shape():
    """Restoring Rank-before-question-cue precedence must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["process"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
                {"op": "Trace"},
            ]
        }
    )
    observed = adapt_observed(
        question=(
            "Explain the largest factory energy carbon record, including "
            "quantity and emission factor evidence."
        ),
        program=program,
        result=_result(
            perspective="energy_source",
            summary={"total_kgCO2e": 1372.9997370312456},
        ),
    )
    assert observed["perspective"] == "process"
    assert observed["operation"] == "explain"
    assert observed["summary"] == {
        "process_record_kgCO2e": 1372.9997370312456
    }


def test_project_material_value_normalizes_known_total_and_family_grouping():
    """Dropping either known_total or the material GroupBy must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "What is the project's total known material carbon, and how many "
            "material families are represented?"
        ),
    )
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True
    assert any(
        step.op == "GroupBy" and step.args.get("keys") == ["material"]
        for step in fixed.steps
    )
    validate_program(fixed, _schema())


def test_project_calculated_material_rank_elicits_known_total():
    """Narrowing known-total cues back to the word 'known' must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Which material family contributes the most calculated material carbon?",
    )
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True
    validate_program(fixed, _schema())


def test_adapter_renders_unresolved_target_disclosure_without_gold():
    """Removing status from reader-facing answer text must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {
                    "op": "ResolveEntities",
                    "entity_type": "component",
                    "value": "missing component",
                },
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    observed = adapt_observed(
        question="What carbon value is available for the component I searched for?",
        program=program,
        result=_result(
            status="unresolved_target",
            coverage_status="unresolved_target",
            summary={"target_id": "missing component"},
        ),
    )
    assert "unresolved_target" in observed["answer"]


def test_adapter_renders_clarification_disclosure_without_gold():
    """Removing the clarification disclosure token must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {
                    "op": "ResolveEntities",
                    "entity_type": "component",
                    "property": "ifcClass",
                    "value": "IfcBeam",
                },
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    observed = adapt_observed(
        question="What supported carbon value is available for that panel?",
        program=program,
        result=_result(
            status="clarification_required",
            coverage_status="ambiguous_target",
            summary={"ambiguous_target": "IfcBeam"},
        ),
    )
    assert "clarification" in observed["answer"]


def test_adapter_maps_factor_gap_to_public_disclosure_code():
    """Removing the factor-gap disclosure mapping must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["component:c1"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
            ]
        }
    )
    observed = adapt_observed(
        question=(
            "Explain why the selected BIM item's component-carbon result "
            "cannot be completed from quantity and emission-factor evidence."
        ),
        program=program,
        result=_result(
            status="incomplete_path",
            coverage_status="relevant_rejection",
            summary={"blocked_status": "thickness_missing"},
        ),
    )
    assert "blocked_factor_unresolved" in observed["answer"]
    assert "thickness_missing" in observed["answer"]


def test_adapter_maps_component_process_gap_to_public_disclosure_code():
    """Removing the process-granularity mapping must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["component:c1"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    observed = adapt_observed(
        question=(
            "Aggregate process-energy carbon for the selected BIM component "
            "and report whether a complete component-level total can be produced."
        ),
        program=program,
        result=_result(
            status="incomplete_path",
            coverage_status="relevant_rejection",
            summary={"blocked_status": "material_quantity_basis_missing"},
        ),
    )
    assert "process_granularity_not_supported" in observed["answer"]
    assert "material_quantity_basis_missing" in observed["answer"]


def test_trace_is_normalized_to_the_terminal_operation():
    """Moving Trace back ahead of grouping must make validation fail."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["component:c1"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
                {"op": "GroupBy", "keys": ["source_kind"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Compare"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Compare the evidence chains for the two selected carbon records.",
    )
    assert fixed.steps[-1].op == "Trace"
    validate_program(fixed, _schema())


def test_superlative_process_explain_ranks_to_the_top_process():
    """"Explain the largest record" must rank to one row before tracing."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process", "known_total": True},
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Explain the largest factory energy carbon record, including "
            "quantity and emission factor evidence."
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "SelectProject",
        "CarbonAtoms",
        "GroupBy",
        "Aggregate",
        "Rank",
        "Trace",
    ]
    group_by = next(step for step in fixed.steps if step.op == "GroupBy")
    rank = next(step for step in fixed.steps if step.op == "Rank")
    assert group_by.args["keys"] == ["process"]
    assert rank.args == {"top_k": 1, "descending": True}
    validate_program(fixed, _schema())


def test_superlative_explain_leaves_an_already_narrowed_scope_alone():
    """A program that already pins its target must not be re-ranked."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {
                    "op": "ResolveEntities",
                    "entity_type": "process",
                    "ids": ["process:stage"],
                },
                {"op": "CarbonAtoms", "source": "process", "known_total": True},
                {"op": "Filter", "field": "process", "equals": "process:stage"},
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(
        program, question="Explain the largest factory energy carbon record."
    )
    assert not any(step.op == "Rank" for step in fixed.steps)


def test_non_superlative_process_trace_is_not_ranked():
    """Whole-account process traces must keep their full scope."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process", "known_total": True},
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(
        program, question="Trace the factory energy carbon evidence for the project."
    )
    assert not any(step.op == "Rank" for step in fixed.steps)


def test_trace_directly_after_selector_gets_carbon_atoms():
    """Removing the missing-CarbonAtoms repair must make validation fail."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {
                    "op": "ResolveEntities",
                    "entity_type": "component",
                    "value": "the component I searched for",
                },
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Trace the component carbon evidence path for the component "
            "I searched for."
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "ResolveEntities",
        "CarbonAtoms",
        "Trace",
    ]
    validate_program(fixed, _schema())


def test_complete_program_discards_malformed_unused_hole():
    """Keeping a malformed unused hole must preserve the observed parse failure."""
    raw = """
    {
      "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"}
      ],
      "holes": [{"dimension": "target"}]
    }
    """
    program, validation, error = _attempt(
        raw,
        _schema(),
        question=(
            "What is the project's total known material carbon, and how many "
            "material families are represented?"
        ),
    )
    assert error == {}
    assert program is not None
    assert validation is not None
    assert program.holes == ()


def test_deictic_target_without_runtime_selection_becomes_typed_hole():
    """Removing deictic-target preflight must turn clarification into an answer."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="What supported carbon evidence value is available for that panel?",
        selected_component_ids=(),
    )
    assert fixed.steps[0].op == "ResolveEntities"
    assert fixed.steps[0].args["entity_type"] == "?"
    assert fixed.steps[0].args["value"] == "that panel"
    assert [hole.dimension for hole in fixed.holes] == ["target"]
    validate_program(fixed, _schema())


def test_runtime_selection_satisfies_deictic_target_without_hole():
    """Ignoring supplied UI selection must create a spurious clarification."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Trace the carbon evidence path for that panel.",
        selected_component_ids=("component:c1",),
    )
    assert fixed.steps[0].op == "SelectClicked"
    assert fixed.steps[0].args["ids"] == ["component:c1"]
    assert fixed.holes == ()
    validate_program(fixed, _schema())


def test_generic_searched_component_becomes_unresolved_selector():
    """Falling back to project scope must incorrectly answer a generic search."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "What supported component carbon value is available for the "
            "component I searched for?"
        ),
    )
    assert fixed.steps[0].op == "ResolveEntities"
    assert fixed.steps[0].args["entity_type"] == "component"
    assert fixed.steps[0].args["value"] == "component I searched for"
    assert fixed.holes == ()
    validate_program(fixed, _schema())


def test_named_roof_cassette_search_keeps_category_query():
    """Treating every 'searched for' phrase as an entity must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {
                    "op": "Filter",
                    "field": "component_type",
                    "equals": "roof cassette",
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "What supported component carbon value is available for the "
            "roof cassette I searched for?"
        ),
    )
    assert fixed.steps[0].op == "SelectProject"
    assert any(
        step.op == "Filter" and step.args.get("equals") == "roof cassette"
        for step in fixed.steps
    )
    validate_program(fixed, _schema())


def test_project_known_process_aggregate_preserves_carrier_grouping():
    """Reapplying the product-total cleanup must remove this required GroupBy."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["carrier"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Aggregate the recorded factory energy carbon across the available "
            "energy records."
        ),
    )
    assert any(
        step.op == "GroupBy" and step.args.get("keys") == ["carrier"]
        for step in fixed.steps
    )
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True
    validate_program(fixed, _schema())


def test_electricity_diesel_compare_uses_carrier_grouping():
    """Leaving process grouping in place must aggregate the wrong comparison."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["process"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Compare"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Compare the factory energy carbon from electricity and diesel."
        ),
    )
    grouping = next(step for step in fixed.steps if step.op == "GroupBy")
    assert grouping.args["keys"] == ["carrier"]
    validate_program(fixed, _schema())


def test_malformed_hole_is_not_dropped_when_question_mark_is_live():
    """Dropping a live malformed hole would silently invent missing intent."""
    raw = """
    {
      "steps": [
        {
          "op": "ResolveEntities",
          "entity_type": "?",
          "value": "that panel"
        },
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"}
      ],
      "holes": [{"dimension": "target"}]
    }
    """
    program, validation, error = _attempt(
        raw,
        _schema(),
        question="What supported carbon value is available for that panel?",
    )
    assert program is None
    assert validation is None
    assert error["code"] == "invalid_syntax"
    assert "requires candidates" in error["message"]


def test_named_production_line_becomes_process_entity_selector():
    """Treating a named line as a project-wide category must answer the wrong scope."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["process"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Rank factory-energy records for production line PL-Z by kgCO2e "
            "from highest to lowest."
        ),
    )
    assert fixed.steps[0].op == "ResolveEntities"
    assert fixed.steps[0].args["entity_type"] == "process"
    assert fixed.steps[0].args["value"] == "PL-Z"
    validate_program(fixed, _schema())


def test_spatial_panel_reference_becomes_component_entity_selector():
    """Dropping the spatial entity rule must widen the request to project scope."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Rank material-carbon records for the vacuum-insulated panel above "
            "the loading bay by kgCO2e from highest to lowest."
        ),
    )
    assert fixed.steps[0].op == "ResolveEntities"
    assert fixed.steps[0].args["entity_type"] == "component"
    assert "panel above the loading bay" in fixed.steps[0].args["value"]
    validate_program(fixed, _schema())


def test_roof_assemblies_reference_becomes_component_set_selector():
    """Treating named assemblies as material categories must hide target failure."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "GroupBy", "keys": ["component"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Compare"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Compare known component carbon among steel, timber, and concrete "
            "roof assemblies."
        ),
    )
    assert fixed.steps[0].op == "ResolveEntities"
    assert fixed.steps[0].args["entity_type"] == "component"
    assert fixed.steps[0].args["cardinality"] == "set"
    assert fixed.steps[0].args["value"] == (
        "steel, timber, and concrete roof assemblies"
    )
    validate_program(fixed, _schema())


def test_roof_cassette_variants_remain_zero_row_category_query():
    """Conflating variants with concrete entity references must break this case."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {
                    "op": "Filter",
                    "field": "component_type",
                    "equals": "roof cassette",
                },
                {"op": "GroupBy", "keys": ["component"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Compare"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Compare known component carbon between timber and steel "
            "roof-cassette variants."
        ),
    )
    assert fixed.steps[0].op == "SelectProject"
    assert any(step.op == "Filter" for step in fixed.steps)
    validate_program(fixed, _schema())


def test_process_incomplete_always_renders_granularity_disclosure():
    """Falling back to factor disclosure must mislabel a process boundary."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["component:c1"]},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    observed = adapt_observed(
        question=(
            "Compare process-energy carbon attributable to the selected BIM "
            "components."
        ),
        program=program,
        result=_result(
            status="incomplete_path",
            coverage_status="relevant_rejection",
            perspective="energy_source",
            summary={"blocked_status": "relevant_rejection"},
        ),
    )
    assert "process_granularity_not_supported" in observed["answer"]
    assert "blocked_factor_unresolved" not in observed["answer"]


def test_product_total_split_collapses_restarted_pipeline():
    """Keeping a second CarbonAtoms branch must reproduce the type error."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Give me the supported project carbon total and separate the "
            "material and factory energy parts."
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "SelectProject",
        "CarbonAtoms",
        "Aggregate",
    ]
    atoms = fixed.steps[1]
    assert atoms.args["source"] == "all"
    assert atoms.args["known_total"] is True
    validate_program(fixed, _schema())


def test_electricity_diesel_compare_collapses_two_serial_branches():
    """Leaving serial carrier subqueries must reproduce the scalar→atoms error."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {
                    "op": "Filter",
                    "field": "carrier",
                    "equals": "electricity",
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "CarbonAtoms", "source": "process"},
                {
                    "op": "Filter",
                    "field": "carrier",
                    "equals": "diesel",
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Compare"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "How much larger is electricity-related factory carbon than "
            "diesel-related factory carbon?"
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "SelectProject",
        "CarbonAtoms",
        "GroupBy",
        "Aggregate",
        "Compare",
    ]
    assert fixed.steps[1].args == {
        "source": "process",
        "known_total": True,
    }
    assert fixed.steps[2].args["keys"] == ["carrier"]
    validate_program(fixed, _schema())


@pytest.mark.parametrize(
    "steps",
    [
        [
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "GroupBy", "keys": ["material"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ],
        [
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material"},
            {"op": "GroupBy", "keys": ["material"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ],
    ],
)
def test_material_total_and_family_count_have_one_typed_pipeline(steps):
    """Restarted/duplicate aggregation must not survive this unique question shape."""
    program = CarbonQLProgram.from_dict({"steps": steps})
    fixed = normalize_program(
        program,
        question=(
            "What is the project's total known material carbon, and how many "
            "material families are represented?"
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "SelectProject",
        "CarbonAtoms",
        "GroupBy",
        "Aggregate",
    ]
    assert fixed.steps[1].args == {
        "source": "material",
        "known_total": True,
    }
    assert fixed.steps[2].args["keys"] == ["material"]
    validate_program(fixed, _schema())


def test_project_component_rank_has_required_group_aggregate_and_rank():
    """An LLM's generic total must not erase an explicit component ranking task."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Rank the project components by supported carbon and identify the "
            "top contributor."
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "SelectProject",
        "CarbonAtoms",
        "GroupBy",
        "Aggregate",
        "Rank",
    ]
    assert fixed.steps[1].args == {
        "source": "all",
        "known_total": True,
    }
    assert fixed.steps[2].args["keys"] == ["component"]
    assert fixed.steps[-1].args == {"top_k": 1, "descending": True}
    validate_program(fixed, _schema())


@pytest.mark.parametrize(
    ("question", "field", "value"),
    [
        (
            "Trace the material carbon evidence path for the steel item.",
            "material",
            "steel",
        ),
        (
            "Compare quantity-factor-source evidence-chain completeness among "
            "steel, timber, and concrete roof assemblies.",
            "component",
            "roof assemblies",
        ),
    ],
)
def test_carbon_atoms_precede_filters_after_entity_selection(question, field, value):
    """A dimension filter consumes carbon atoms, never the selected entity set."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "Filter", "field": field, "equals": value},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(program, question=question)
    assert [step.op for step in fixed.steps[:3]] == [
        "ResolveEntities",
        "CarbonAtoms",
        "Filter",
    ]
    validate_program(fixed, _schema())


@pytest.mark.parametrize(
    ("raw_key", "canonical_key"),
    [
        ("material_family", "material"),
        ("target", "component"),
    ],
)
def test_groupby_dimension_aliases_use_carbonql_schema_names(
    raw_key, canonical_key
):
    """Common semantic aliases must not reach the validator as unknown keys."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "GroupBy", "keys": [raw_key]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Describe the supported carbon evidence and its grouping.",
    )
    grouping = next(step for step in fixed.steps if step.op == "GroupBy")
    assert grouping.args["keys"] == [canonical_key]
    validate_program(fixed, _schema())


def test_rank_after_scalar_aggregate_gets_a_grouped_table_input():
    """A project rank cannot consume the scalar produced by an ungrouped aggregate."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Use only supported records to answer the rank request for the "
            "current project."
        ),
    )
    assert [step.op for step in fixed.steps] == [
        "SelectProject",
        "CarbonAtoms",
        "GroupBy",
        "Aggregate",
        "Rank",
    ]
    assert fixed.steps[2].args["keys"] == ["component"]
    validate_program(fixed, _schema())


def test_named_factory_stage_rank_inserts_stage_filter_and_groups_by_stage():
    """A named coating stage must not widen to a project-wide carrier ranking."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ]
        }
    )
    question = (
        "Rank MEP anticorrosive coating factory-energy records by kgCO2e "
        "from highest to lowest."
    )
    fixed = normalize_program(program, question=question)
    assert any(
        step.op == "Filter"
        and step.args.get("field") == "stage"
        and step.args.get("equals") == "MEP anticorrosive coating"
        for step in fixed.steps
    )
    group_by = next(step for step in fixed.steps if step.op == "GroupBy")
    assert group_by.args["keys"] == ["stage"]
    validate_program(fixed, _schema())


def test_factory_energy_records_for_named_stage_inserts_stage_filter():
    """Order/sort phrasings with a named stage must narrow before ranking."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["carrier"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "top_k": 1, "descending": True},
            ]
        }
    )
    question = (
        "Order the factory-energy records for MEP anticorrosive coating "
        "from highest to lowest kgCO2e."
    )
    fixed = normalize_program(program, question=question)
    assert any(
        step.op == "Filter"
        and step.args.get("field") == "stage"
        and step.args.get("equals") == "MEP anticorrosive coating"
        for step in fixed.steps
    )
    group_by = next(step for step in fixed.steps if step.op == "GroupBy")
    assert group_by.args["keys"] == ["stage"]
    validate_program(fixed, _schema())


def test_evidence_coverage_question_maps_to_traceability_not_product():
    """Generic evidence-coverage wording must not inherit the product account."""
    from dm2c_qa_answer_adapter import infer_perspective

    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
            ]
        }
    )
    question = (
        "Show the known carbon records and evidence coverage for the "
        "selected project scope."
    )
    assert infer_perspective(question, "product") == "product"
    assert infer_operation(question, program) == "value"
    observed = adapt_observed(
        question=question,
        program=program,
        result=_result(
            perspective="product",
            summary={"total_kgCO2e": 16.199096},
            evidence_ids=tuple(f"e{i}" for i in range(11)),
        ),
    )
    assert observed["perspective"] == "product"
    assert observed["summary"] == {
        "traced_atomic_emission_kgCO2e": 16.199096,
        "evidence_hops": 11,
    }


def test_material_evidence_path_stays_material_account():
    """Evidence wording must not steal an explicit material-account request."""
    from dm2c_qa_answer_adapter import infer_perspective

    assert (
        infer_perspective(
            "Trace the material carbon evidence path for the steel item.",
            "product",
        )
        == "material"
    )
    assert (
        infer_perspective(
            "Trace the evidence chain for the largest factory energy carbon "
            "record.",
            "product",
        )
        == "process"
    )


def test_generic_searched_material_item_precedes_deictic_hole():
    """Deictic 'the material item' must not turn an unresolved search into clarification."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Explain the supported material carbon result for the material "
            "item I searched for."
        ),
    )
    assert fixed.steps[0].op == "ResolveEntities"
    assert fixed.steps[0].args["entity_type"] == "material"
    assert fixed.steps[0].args["value"] == "material item I searched for"
    assert fixed.holes == ()
    validate_program(fixed, _schema())


def test_named_roof_cassette_search_injects_category_filter_when_missing():
    """A named search without a filter must not answer the whole project."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "Aggregate the supported carbon evidence records for the roof "
            "cassette I searched for."
        ),
    )
    assert fixed.steps[0].op == "SelectProject"
    assert any(
        step.op == "Filter"
        and step.args.get("field") == "component_type"
        and step.args.get("equals") == "roof cassette"
        for step in fixed.steps
    )
    validate_program(fixed, _schema())


def test_supported_by_current_records_elicits_known_total_for_material():
    """Material totals phrased with 'supported by current records' need Path A."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question=(
            "What material carbon total is supported by the current records, "
            "and how many families does it cover?"
        ),
    )
    atoms = next(step for step in fixed.steps if step.op == "CarbonAtoms")
    assert atoms.args.get("known_total") is True
    validate_program(fixed, _schema())


def test_bim_supported_carbon_explain_stays_product_account():
    """'supported carbon value' alone must not flip a BIM explain into evidence-class."""
    from dm2c_qa_answer_adapter import infer_perspective

    question = "Explain why the selected BIM item has its supported carbon value."
    assert infer_perspective(question, "product") == "product"
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["component:c1"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Trace"},
            ]
        }
    )
    assert infer_operation(question, program) == "explain"


def test_explicit_aggregate_beats_incidental_total_wording():
    """Aggregate wording folds into the merged 'value' operation label."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    assert (
        infer_operation(
            "Aggregate the project's known material carbon by material family "
            "and report the overall supported total.",
            program,
        )
        == "value"
    )


def test_doubled_carbon_evidence_path_stays_evidence_class():
    """Benchmark typos 'carbon evidence evidence path' still mark evidence-class."""
    from dm2c_qa_answer_adapter import infer_perspective

    assert (
        infer_perspective(
            "Trace the carbon evidence evidence path for that panel.",
            "product",
        )
        == "product"
    )


def test_compare_without_groupby_gets_source_kind_table_for_evidence_question():
    """Compare-over-scalar compile failures are repaired into a group table."""
    program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectClicked", "ids": ["ModularUnit:demo"]},
                {"op": "CarbonAtoms", "source": "all", "known_total": True},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Compare"},
            ]
        }
    )
    fixed = normalize_program(
        program,
        question="Compare the evidence chains for the two selected carbon records.",
        selected_component_ids=["ModularUnit:demo"],
    )
    ops = [step.op for step in fixed.steps]
    assert "GroupBy" in ops
    assert ops.index("GroupBy") < ops.index("Aggregate") < ops.index("Compare")
    group = next(step for step in fixed.steps if step.op == "GroupBy")
    assert group.args.get("keys") == ["source_kind"]
    validate_program(fixed, _schema())
