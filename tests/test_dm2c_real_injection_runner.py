from __future__ import annotations

import json
from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import CanonicalSchemaError
from dm2c_m3_context import load_m3_execution_context
from dm2c_m3_release import publish_derived_release
from dm2c_real_injection_runner import build_injection_specs, execute_injection
from tests.task11_v2_fixture import release_digest, write_task10_release


@pytest.fixture
def controlled(tmp_path: Path):
    release = write_task10_release(tmp_path / "source-release")
    return release, load_m3_execution_context(release)


def test_specs_bind_faults_to_candidate_identity_and_reason_code(controlled) -> None:
    _release, context = controlled
    specs = build_injection_specs(context, per_category=1)
    mapping = {row.injection_type: row.expected_reason_code for row in specs}
    assert mapping == {
        "missing_quantity": "material_quantity_basis_missing",
        "missing_factor": "factor_missing",
        "missing_source": "source_record_id_missing",
        "incompatible_unit": "unit_dimension_incompatible",
        "missing_allocation_basis": "allocation_basis_missing",
        "synonym_rewrite": "unresolved_target",
        "ambiguous_target_name": "clarification_required",
    }
    assert all(row.source_record_id for row in specs)
    assert all(row.candidate_record_id for row in specs)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_quantity",
        "missing_factor",
        "missing_source",
        "incompatible_unit",
        "missing_allocation_basis",
    ],
)
def test_candidate_fault_is_rejected_before_fresh_graph_population(
    tmp_path: Path, controlled, fault: str
) -> None:
    source_release, context = controlled
    before = release_digest(source_release)
    spec = next(row for row in build_injection_specs(context) if row.injection_type == fault)
    outcome = execute_injection(
        context,
        spec,
        output_root=tmp_path / "injected",
    )
    assert outcome.reason_code == spec.expected_reason_code
    assert outcome.new_rejection_count == 1
    assert outcome.candidate_materialized is False
    assert outcome.target_release is not None
    assert release_digest(source_release) == before == outcome.source_release_digest
    loaded = load_m3_execution_context(outcome.target_release)
    assert loaded.validation_coverage.rejected_count == context.validation_coverage.rejected_count + 1
    injected = [
        row
        for row in loaded.validation_coverage.records
        if row.record_id == spec.candidate_record_id
    ]
    assert len(injected) == 1
    assert injected[0].status == "rejected"
    assert injected[0].reason_code == spec.expected_reason_code
    assert injected[0].source_record_id == (
        "" if fault == "missing_source" else spec.source_record_id
    )
    if fault == "missing_source":
        raw_row = next(
            row
            for row in loaded.canonical.validation_rows
            if row["recordId"] == spec.candidate_record_id
        )
        evidence = json.loads(raw_row["evidenceJson"])
        assert raw_row["sourceRecordId"] == evidence["sourceRecordId"] == ""
        assert raw_row["sourceIdentity"].startswith("ValidationRecord:")
    assert all(fact.record_id != spec.candidate_record_id for fact in loaded.canonical.emissions)
    assert json.loads(
        (outcome.target_release / "multigranular_carbon_kg.json").read_text("utf-8")
    ) == json.loads(
        (source_release / "multigranular_carbon_kg.json").read_text("utf-8")
    )
    assert {path.name for path in outcome.target_release.iterdir()} == {
        "case_version_manifest.json",
        "m2_alignment_report.json",
        "multigranular_carbon_kg.cypher",
        "multigranular_carbon_kg.json",
        "multigranular_carbon_kg_stats.json",
        "multigranular_carbon_kg_validation.csv",
    }


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("synonym_rewrite", "unresolved_target"),
        ("ambiguous_target_name", "clarification_required"),
    ],
)
def test_language_fault_changes_query_input_only(
    tmp_path: Path, controlled, fault: str, expected: str
) -> None:
    source_release, context = controlled
    before = release_digest(source_release)
    spec = next(row for row in build_injection_specs(context) if row.injection_type == fault)
    outcome = execute_injection(context, spec, output_root=tmp_path / "query-only")
    assert outcome.query_status == expected
    assert outcome.target_release is None
    assert not (tmp_path / "query-only").exists()
    assert release_digest(source_release) == before


def test_injection_output_is_fail_if_existing(tmp_path: Path, controlled) -> None:
    _source_release, context = controlled
    spec = next(
        row for row in build_injection_specs(context)
        if row.injection_type == "missing_factor"
    )
    execute_injection(context, spec, output_root=tmp_path / "injected")
    with pytest.raises(FileExistsError):
        execute_injection(context, spec, output_root=tmp_path / "injected")


def test_invalid_derived_release_is_strictly_loaded_before_atomic_rename(
    tmp_path: Path, controlled
) -> None:
    _source_release, context = controlled

    def corrupt_emission(node):
        updated = {**node, "labels": list(node["labels"]), "props": dict(node["props"])}
        if updated["id"] == "emission:direct":
            updated["props"]["emissionValue"] = 999.0
        return updated

    output_root = tmp_path / "invalid-publication"
    release_id = "controlled-v2-invalid-emission"
    with pytest.raises(CanonicalSchemaError):
        publish_derived_release(
            context,
            output_root=output_root,
            release_id=release_id,
            node_transform=corrupt_emission,
        )
    assert not (output_root / release_id).exists()
    assert not (output_root / f".{release_id}.staging").exists()
