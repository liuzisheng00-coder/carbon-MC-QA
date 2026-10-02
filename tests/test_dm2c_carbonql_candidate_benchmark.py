from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import dm2c_carbonql_candidate_benchmark as candidate_benchmark_module

from dm2c_canonical_v2_reader import dimension_ids, load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, GraphSchema
from dm2c_carbonql_candidate_benchmark import (
    _jsonl,
    audit_candidate_benchmark,
    build_candidate_benchmark,
    candidate_reference_evaluate,
    freeze_candidate_benchmark,
)
from dm2c_carbonql_candidate_views import (
    compile_candidate_set,
    execute_candidate_set,
    program_sha256,
)
from dm2c_carbonql_executor import CarbonQLExecutor
from tests.task10_v2_fixture import write_task10_release


@pytest.fixture()
def context(tmp_path: Path):
    return load_canonical_v2_context(write_task10_release(tmp_path / "release"))


def test_candidate_selection_is_deterministic_from_controlled_release(context) -> None:
    first = build_candidate_benchmark(context)
    second = build_candidate_benchmark(context)
    assert [case.to_dict() for case in first] == [case.to_dict() for case in second]
    available = set(dimension_ids(context, "component"))
    assert first
    assert all(set(case.selected_component_ids) <= available for case in first)
    assert "162" not in repr(first)
    assert "2JGZlW3qn3kOh$7$0N4p9" not in repr(first)


def test_candidate_truth_uses_emissions_and_projection_keys(context) -> None:
    cases = build_candidate_benchmark(context)
    for case in cases:
        truth = candidate_reference_evaluate(case, context)
        payload = truth.to_dict()
        assert "atom_ids" not in repr(payload)
        for execution in payload["executions"]:
            assert "emission_ids" in execution
            assert "projection_keys" in execution
            assert execution["status"] == "ok"


def test_candidate_audit_reports_controlled_coverage_without_legacy_assumptions(context) -> None:
    cases = build_candidate_benchmark(context)
    audit = audit_candidate_benchmark(cases, context)
    assert audit["release_profile"] == "controlled-fixture"
    assert audit["available_component_count"] == 4
    assert audit["accepted_emission_count"] == 8
    assert audit["rejected_validation_count"] == 1
    assert audit["oracle_mismatch_count"] == 0


def test_candidate_audit_and_freeze_reject_tampered_program_hash_before_staging(
    tmp_path: Path, context
) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    candidate = case.candidate_set.candidates[0]
    tampered = replace(candidate, program_sha256="0" * 64)
    invalid_case = replace(
        case,
        candidate_set=replace(
            case.candidate_set,
            candidates=(tampered, *case.candidate_set.candidates[1:]),
        ),
    )
    invalid_cases = (invalid_case, *cases[1:])

    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert audit["candidate_integrity_error_count"] == 1
    assert audit["candidate_integrity_errors"] == [
        {
            "case_id": case.case_id,
            "candidate_id": candidate.candidate_id,
            "issue": "program_sha256_mismatch",
            "declared_program_sha256": "0" * 64,
            "canonical_program_sha256": candidate.program_sha256,
        }
    ]

    output_root = tmp_path / "candidate-v2"
    freeze_id = "tampered-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=freeze_id,
        )
    assert not output_root.exists()


def test_candidate_audit_and_freeze_reject_duplicate_candidate_ids_before_staging(
    tmp_path: Path, context
) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    first, second, *remaining = case.candidate_set.candidates
    duplicate = replace(second, candidate_id=first.candidate_id)
    invalid_case = replace(
        case,
        candidate_set=replace(
            case.candidate_set,
            candidates=(first, duplicate, *remaining),
        ),
    )
    invalid_cases = (invalid_case, *cases[1:])

    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert audit["candidate_integrity_error_count"] == 1
    assert audit["candidate_integrity_errors"] == [
        {
            "case_id": case.case_id,
            "candidate_id": first.candidate_id,
            "issue": "duplicate_candidate_id",
        }
    ]

    output_root = tmp_path / "candidate-v2"
    freeze_id = "duplicate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=freeze_id,
        )
    assert not output_root.exists()


def test_candidate_integrity_is_bound_to_constraint_and_prototype(context) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    first, second, *remaining = case.candidate_set.candidates
    forged = replace(
        first,
        candidate_id="forged",
        sources=second.sources,
        program=second.program,
        program_sha256=program_sha256(second.program),
    )
    invalid_cases = (
        replace(
            case,
            candidate_set=replace(
                case.candidate_set,
                candidates=(forged, second, *remaining),
            ),
        ),
        *cases[1:],
    )

    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert audit["oracle_mismatch_count"] == 0
    issues = {row["issue"] for row in audit["candidate_integrity_errors"]}
    assert "candidate_id_mismatch" in issues
    assert "candidate_sources_mismatch" in issues
    assert "candidate_program_mismatch" in issues
    assert "duplicate_canonical_program" in issues


@pytest.mark.parametrize(
    ("fault", "expected_issue"),
    [
        ("blank_id", "invalid_candidate_id"),
        ("odd_id", "invalid_candidate_id"),
        ("nan_id", "invalid_candidate_id"),
        ("missing_candidate", "candidate_count_mismatch"),
        ("extra_candidate", "candidate_count_mismatch"),
        ("duplicate_program", "duplicate_canonical_program"),
        ("empty_sources", "candidate_sources_mismatch"),
    ],
)
def test_candidate_integrity_rejects_noncanonical_set_shapes_before_output_root(
    tmp_path: Path, context, fault: str, expected_issue: str
) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    first, second, *remaining = case.candidate_set.candidates
    candidates = list(case.candidate_set.candidates)
    if fault == "blank_id":
        candidates[0] = replace(first, candidate_id="")
    elif fault == "odd_id":
        candidates[0] = replace(first, candidate_id="../material")
    elif fault == "nan_id":
        candidates[0] = replace(first, candidate_id=float("nan"))
    elif fault == "missing_candidate":
        candidates.pop()
    elif fault == "extra_candidate":
        candidates.append(replace(first, candidate_id="extra"))
    elif fault == "duplicate_program":
        candidates[1] = replace(
            second,
            program=first.program,
            program_sha256=program_sha256(first.program),
        )
    elif fault == "empty_sources":
        candidates[0] = replace(first, sources=())
    else:  # pragma: no cover - guards the test table itself
        raise AssertionError(fault)
    invalid_cases = (
        replace(
            case,
            candidate_set=replace(case.candidate_set, candidates=tuple(candidates)),
        ),
        *cases[1:],
    )

    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert expected_issue in {
        row["issue"] for row in audit["candidate_integrity_errors"]
    }
    assert audit["oracle_mismatch_count"] == 0

    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=f"{fault}-v2",
        )
    assert not output_root.exists()


def test_candidate_jsonl_rejects_nonfinite_numbers() -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        _jsonl(({"candidate_id": float("nan")},))


@pytest.mark.parametrize(
    "fault",
    [
        "garbage_mode",
        "required_selector",
        "group_keys",
        "operations",
        "unique_process",
    ],
)
def test_candidate_integrity_is_rooted_in_expected_controlled_case_constraints(
    tmp_path: Path, context, fault: str
) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    constraint = case.candidate_set.constraint
    if fault == "garbage_mode":
        forged_constraint = replace(constraint, mode="garbage")
    elif fault == "required_selector":
        forged_constraint = replace(constraint, required_selector="SelectProject")
    elif fault == "group_keys":
        forged_constraint = replace(constraint, group_keys=("module",))
    elif fault == "operations":
        forged_constraint = replace(constraint, operations=("trace",))
    elif fault == "unique_process":
        forged_constraint = replace(
            constraint, mode="unique", sources=("process",)
        )
    else:  # pragma: no cover
        raise AssertionError(fault)
    forged_set = compile_candidate_set(
        forged_constraint,
        case.prototype_program,
        GraphSchema.from_context(context),
    )
    invalid_cases = (
        replace(case, candidate_set=forged_set),
        *cases[1:],
    )

    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert audit["oracle_mismatch_count"] == 0
    assert {
        (row["issue"], row.get("field"))
        for row in audit["candidate_integrity_errors"]
    } >= {("case_field_mismatch", "constraint")}

    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=f"constraint-{fault}-v2",
        )
    assert not output_root.exists()


def test_candidate_integrity_rejects_coherent_target_and_program_retargeting(
    tmp_path: Path, context
) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    prototype_payload = case.prototype_program.to_dict()
    prototype_payload["steps"][0]["ids"] = ["component:c2"]
    prototype = CarbonQLProgram.from_dict(prototype_payload)
    candidate_set = compile_candidate_set(
        case.candidate_set.constraint,
        prototype,
        GraphSchema.from_context(context),
    )
    invalid_cases = (
        replace(
            case,
            selected_component_ids=("component:c2",),
            prototype_program=prototype,
            candidate_set=candidate_set,
        ),
        *cases[1:],
    )

    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    fields = {
        row.get("field") for row in audit["candidate_integrity_errors"]
    }
    assert {"selected_component_ids", "prototype_program"} <= fields
    assert audit["oracle_mismatch_count"] == 0
    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id="retarget-v2",
        )
    assert not output_root.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "forged"),
        ("question", "forged controlled question"),
        ("base_target_pool", ("component:c1",)),
    ],
)
def test_candidate_integrity_rejects_forged_case_metadata(
    tmp_path: Path, context, field: str, value: object
) -> None:
    cases = build_candidate_benchmark(context)
    invalid_cases = (replace(cases[0], **{field: value}), *cases[1:])
    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert ("case_field_mismatch", field) in {
        (row["issue"], row.get("field"))
        for row in audit["candidate_integrity_errors"]
    }
    assert audit["oracle_mismatch_count"] == 0
    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=f"metadata-{field}-v2",
        )
    assert not output_root.exists()


@pytest.mark.parametrize(
    ("fault", "expected_issue"),
    [
        ("duplicate", "duplicate_case_id"),
        ("reordered", "case_field_mismatch"),
        ("missing", "case_count_mismatch"),
        ("extra", "case_count_mismatch"),
    ],
)
def test_candidate_integrity_rejects_noncanonical_case_sequence(
    tmp_path: Path, context, fault: str, expected_issue: str
) -> None:
    cases = build_candidate_benchmark(context)
    if fault == "duplicate":
        invalid_cases = (cases[0], replace(cases[1], case_id=cases[0].case_id), *cases[2:])
    elif fault == "reordered":
        invalid_cases = (cases[1], cases[0], *cases[2:])
    elif fault == "missing":
        invalid_cases = cases[:-1]
    elif fault == "extra":
        invalid_cases = (*cases, replace(cases[-1], case_id="controlled-v2-extra"))
    else:  # pragma: no cover
        raise AssertionError(fault)
    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert expected_issue in {
        row["issue"] for row in audit["candidate_integrity_errors"]
    }
    assert audit["oracle_mismatch_count"] == 0
    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=f"sequence-{fault}-v2",
        )
    assert not output_root.exists()


@pytest.mark.parametrize("case_id", ["", "../case", float("nan")])
def test_candidate_integrity_rejects_invalid_case_id_before_output_root(
    tmp_path: Path, context, case_id: object
) -> None:
    cases = build_candidate_benchmark(context)
    invalid_cases = (replace(cases[0], case_id=case_id), *cases[1:])
    audit = audit_candidate_benchmark(invalid_cases, context)
    assert audit["candidate_integrity_ok"] is False
    assert "invalid_case_id" in {
        row["issue"] for row in audit["candidate_integrity_errors"]
    }
    assert audit["oracle_mismatch_count"] == 0
    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id="invalid-case-v2",
        )
    assert not output_root.exists()


@pytest.mark.parametrize("integrity_fault", ["tampered_hash", "duplicate_id"])
def test_candidate_freeze_directly_rejects_integrity_fault_before_output_root(
    tmp_path: Path, context, integrity_fault: str
) -> None:
    cases = build_candidate_benchmark(context)
    case = cases[0]
    first, second, *remaining = case.candidate_set.candidates
    if integrity_fault == "tampered_hash":
        candidates = (replace(first, program_sha256="0" * 64), second, *remaining)
    else:
        candidates = (first, replace(second, candidate_id=first.candidate_id), *remaining)
    invalid_cases = (
        replace(
            case,
            candidate_set=replace(case.candidate_set, candidates=candidates),
        ),
        *cases[1:],
    )
    output_root = tmp_path / "candidate-v2"
    with pytest.raises(ValueError, match="candidate integrity"):
        freeze_candidate_benchmark(
            invalid_cases,
            context,
            output_root=output_root,
            freeze_id=f"{integrity_fault}-v2",
        )
    assert not output_root.exists()


def test_candidate_freeze_is_staged_and_never_overwrites_or_updates_latest(tmp_path: Path, context) -> None:
    cases = build_candidate_benchmark(context)
    output_root = tmp_path / "candidate-v2"
    frozen = freeze_candidate_benchmark(cases, context, output_root=output_root, freeze_id="controlled-v2")
    assert frozen == output_root / "controlled-v2"
    assert frozen.is_dir()
    assert not any(path.name.startswith("latest_") for path in output_root.rglob("*"))
    before = {path.name: path.read_bytes() for path in frozen.iterdir()}
    with pytest.raises(FileExistsError):
        freeze_candidate_benchmark(cases, context, output_root=output_root, freeze_id="controlled-v2")
    assert {path.name: path.read_bytes() for path in frozen.iterdir()} == before


def test_candidate_freeze_rejects_oracle_mismatch_without_target_or_staging(
    tmp_path: Path, context, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "candidate-v2"
    freeze_id = "invalid-v2"
    monkeypatch.setattr(
        candidate_benchmark_module,
        "audit_candidate_benchmark",
        lambda cases, selected_context: {"oracle_mismatch_count": 1},
    )
    with pytest.raises(ValueError, match="oracle mismatch"):
        freeze_candidate_benchmark(
            build_candidate_benchmark(context),
            context,
            output_root=output_root,
            freeze_id=freeze_id,
        )
    assert not (output_root / freeze_id).exists()
    assert not (output_root / f".{freeze_id}.staging").exists()


def test_candidate_freeze_cleans_staging_after_serialization_failure(
    tmp_path: Path, context, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "candidate-v2"
    freeze_id = "serialization-v2"
    monkeypatch.setattr(
        candidate_benchmark_module,
        "_jsonl",
        lambda rows: (_ for _ in ()).throw(RuntimeError("serialization failed")),
    )
    with pytest.raises(RuntimeError, match="serialization failed"):
        freeze_candidate_benchmark(
            build_candidate_benchmark(context),
            context,
            output_root=output_root,
            freeze_id=freeze_id,
        )
    assert not (output_root / freeze_id).exists()
    assert not (output_root / f".{freeze_id}.staging").exists()


def test_all_candidate_operations_classify_successful_executor_results(context) -> None:
    reference_results = {
        case.operation: candidate_reference_evaluate(case, context)
        for case in build_candidate_benchmark(context)
    }
    executor = CarbonQLExecutor.from_context(context)
    executor_results = {
        case.operation: execute_candidate_set(case.candidate_set, executor)
        for case in build_candidate_benchmark(context)
    }
    for results in (reference_results, executor_results):
        assert set(results) == {"aggregate", "compare", "rank", "trace"}
        for result in results.values():
            assert result.status == "executable"
            assert result.decision_class not in {None, "unclassifiable"}
        assert results["aggregate"].decision_class == "decomposition_required"
        assert results["trace"].decision_class == "source_dependent_trace"
        assert results["compare"].decision_class == "view_sensitive"
        assert results["rank"].decision_class == "view_sensitive"
