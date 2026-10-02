from __future__ import annotations

from pathlib import Path

import pytest

import dm2c_carbonql_heldout as heldout_module

from dm2c_canonical_v2_reader import dimension_ids, load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec
from dm2c_carbonql_heldout import (
    audit_heldout_cases,
    build_heldout_cases,
    freeze_heldout_benchmark,
    heldout_reference_evaluate,
)
from tests.task10_v2_fixture import write_task10_release


@pytest.fixture()
def context(tmp_path: Path):
    return load_canonical_v2_context(write_task10_release(tmp_path / "release"))


def test_heldout_cases_encode_projection_truth_and_use_available_ids(context) -> None:
    cases = build_heldout_cases(context)
    assert {case.reference.projection_perspective for case in cases} == {
        "product", "material_source", "energy_source", "source_union"
    }
    available = set(dimension_ids(context, "component"))
    assert all(set(case.reference.selector_component_ids) <= available for case in cases)
    assert "162" not in repr(cases)


def test_heldout_reference_and_executor_reconcile(context) -> None:
    audit = audit_heldout_cases(build_heldout_cases(context), context)
    assert audit["oracle_mismatch_count"] == 0
    assert audit["valid_zero_covered"] is True
    assert audit["process_only_source_covered"] is True
    assert all("atom_ids" not in repr(heldout_reference_evaluate(case, context).to_dict()) for case in build_heldout_cases(context))


def test_heldout_audit_passes_external_clicked_context_symmetrically(context) -> None:
    case = CarbonQLCase(
        case_id="external-clicked",
        question="external-clicked",
        category="controlled-v2-heldout",
        gold_program=CarbonQLProgram.from_dict(
            {
                "steps": [
                    {"op": "SelectClicked"},
                    {"op": "CarbonAtoms", "source": "all"},
                    {"op": "Aggregate", "metric": "sum_kgCO2e"},
                ]
            }
        ),
        expected_compiler_status="valid",
        reference=ReferenceSpec(
            projection_perspective="product",
            selector_component_ids=("component:c2",),
        ),
    )
    audit = audit_heldout_cases((case,), context)
    assert audit["oracle_mismatch_count"] == 0


def test_heldout_freeze_fails_on_existing_successor_and_has_no_latest(tmp_path: Path, context) -> None:
    output_root = tmp_path / "heldout-v2"
    cases = build_heldout_cases(context)
    target = freeze_heldout_benchmark(cases, context, output_root=output_root, freeze_id="controlled-v2")
    assert target.is_dir()
    assert not any(path.name.startswith("latest_") for path in output_root.rglob("*"))
    with pytest.raises(FileExistsError):
        freeze_heldout_benchmark(cases, context, output_root=output_root, freeze_id="controlled-v2")


def test_heldout_freeze_rejects_oracle_mismatch_without_target_or_staging(
    tmp_path: Path, context, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "heldout-v2"
    freeze_id = "invalid-v2"
    monkeypatch.setattr(
        heldout_module,
        "audit_heldout_cases",
        lambda cases, selected_context: {"oracle_mismatch_count": 1},
    )
    with pytest.raises(ValueError, match="oracle mismatch"):
        freeze_heldout_benchmark(
            build_heldout_cases(context),
            context,
            output_root=output_root,
            freeze_id=freeze_id,
        )
    assert not (output_root / freeze_id).exists()
    assert not (output_root / f".{freeze_id}.staging").exists()


def test_heldout_freeze_cleans_staging_after_serialization_failure(
    tmp_path: Path, context, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "heldout-v2"
    freeze_id = "serialization-v2"
    monkeypatch.setattr(
        heldout_module,
        "_jsonl",
        lambda rows: (_ for _ in ()).throw(RuntimeError("serialization failed")),
    )
    with pytest.raises(RuntimeError, match="serialization failed"):
        freeze_heldout_benchmark(
            build_heldout_cases(context),
            context,
            output_root=output_root,
            freeze_id=freeze_id,
        )
    assert not (output_root / freeze_id).exists()
    assert not (output_root / f".{freeze_id}.staging").exists()
