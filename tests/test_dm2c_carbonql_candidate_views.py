from __future__ import annotations

from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, GraphSchema
from dm2c_carbonql_candidate_views import (
    CandidateExecution,
    CarbonQLCandidate,
    CarbonQLCandidateSet,
    PerspectiveConstraint,
    classify_candidate_results,
    compile_candidate_set,
    execute_candidate_set,
    program_sha256,
    resolve_perspective_constraint,
)
from dm2c_carbonql_executor import CarbonQLExecutor
from tests.task10_v2_fixture import write_task10_release


def test_candidate_execution_uses_emission_and_projection_identity(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    prototype = CarbonQLProgram.from_dict(
        {"steps": [
            {"op": "SelectClicked", "ids": ["component:c1"]},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "GroupBy", "keys": ["component"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ]}
    )
    constraint = PerspectiveConstraint(
        mode="underspecified",
        sources=(),
        group_keys=("component",),
        operations=("aggregate",),
        evidence_spans={},
        required_selector="SelectClicked",
    )
    candidates = compile_candidate_set(constraint, prototype, GraphSchema.from_context(context))
    result = execute_candidate_set(candidates, CarbonQLExecutor.from_context(context))
    payload = result.to_dict()
    assert [row["candidate_id"] for row in payload["executions"]] == ["material", "process", "unified"]
    assert all("emission_ids" in row and "projection_keys" in row for row in payload["executions"])
    assert "atom_ids" not in repr(payload)


def test_valid_zero_candidate_remains_value_bearing(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    prototype = CarbonQLProgram.from_dict(
        {"steps": [
            {"op": "SelectClicked", "ids": ["component:c4"]},
            {"op": "CarbonAtoms", "source": "process"},
            {"op": "GroupBy", "keys": ["component"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ]}
    )
    constraint = PerspectiveConstraint("unique", ("process",), ("component",), ("aggregate",), {})
    candidate_set = compile_candidate_set(constraint, prototype, GraphSchema.from_context(context))
    result = execute_candidate_set(candidate_set, CarbonQLExecutor.from_context(context))
    execution = result.executions[0]
    assert execution.rows[0]["kgCO2e"] == 0.0
    assert execution.emission_ids == ("emission:zero",)
    assert execution.coverage["accepted_projection_count"] == 1


def test_semantic_grounding_emits_only_canonical_group_keys() -> None:
    constraint = resolve_perspective_constraint(
        "Rank components by explicit material and energy carrier.", ()
    )
    assert constraint.group_keys == ("component", "material", "carrier")


def sparse_tie_candidate_set(operation: str) -> tuple[CarbonQLCandidateSet, CandidateExecution]:
    suffix = {"op": "Rank", "top_k": 2, "descending": True} if operation == "Rank" else {"op": "Compare"}
    candidate_program = CarbonQLProgram.from_dict(
        {"steps": [
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all"},
            {"op": "GroupBy", "keys": ["material", "process"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
            suffix,
        ]}
    )
    candidate = CarbonQLCandidate(
        candidate_id="unified",
        sources=("material", "process"),
        program=candidate_program,
        program_sha256=program_sha256(candidate_program),
    )
    candidate_set = CarbonQLCandidateSet(
        constraint=PerspectiveConstraint("unique", ("material", "process"), ("material", "process"), (operation.lower(),), {}),
        candidates=(candidate,),
    )
    rows = (
        {"material": None, "process": "process:stage", "kgCO2e": 7.0},
        {"material": "material:steel", "process": None, "kgCO2e": 7.0},
    )
    execution = CandidateExecution(
        candidate_id="unified",
        program_sha256=candidate.program_sha256,
        status="ok",
        rows=rows,
        summary={"total_kgCO2e": 14.0},
        emission_ids=(),
        projection_keys=(),
        trace_rows=(),
        decision_probe_rows=rows,
        coverage={},
    )
    return candidate_set, execution


@pytest.mark.parametrize("operation", ["Compare", "Rank"])
def test_public_candidate_classification_sorts_sparse_ties_stably(operation: str) -> None:
    candidate_set, execution = sparse_tie_candidate_set(operation)
    forward = classify_candidate_results(candidate_set, (execution,))
    reversed_execution = CandidateExecution(
        **{**execution.__dict__, "decision_probe_rows": tuple(reversed(execution.decision_probe_rows))}
    )
    reverse = classify_candidate_results(candidate_set, (reversed_execution,))
    assert forward.status == reverse.status == "executable"
    assert forward.decision_class == reverse.decision_class == "view_robust"
    assert forward.answer_policy == reverse.answer_policy
    assert forward.decision_signatures == reverse.decision_signatures
