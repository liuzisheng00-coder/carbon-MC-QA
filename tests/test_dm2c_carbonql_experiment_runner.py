from __future__ import annotations

from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec
from dm2c_carbonql_experiment_runner import run_oracle, write_run_artifacts
from dm2c_carbonql_heldout import build_heldout_cases
from tests.task10_v2_fixture import write_task10_release


def test_oracle_runner_uses_canonical_context_and_projection_keys(tmp_path: Path) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    result = run_oracle(build_heldout_cases(context), context)
    assert result
    assert all(row.execution_status == "ok" for row in result)
    assert all("projection_keys" in row.result for row in result)
    assert "atom_ids" not in repr(result)


def test_oracle_runner_passes_external_clicked_context_symmetrically(
    tmp_path: Path,
) -> None:
    context = load_canonical_v2_context(write_task10_release(tmp_path / "release"))
    case = CarbonQLCase(
        case_id="external-clicked",
        question="external-clicked",
        category="controlled-v2",
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
    result = run_oracle((case,), context)[0]
    assert result.exact_match is True
    assert result.execution_status == "ok"
    assert result.result["summary"]["total_kgCO2e"] == pytest.approx(21.0)


def test_oracle_runner_reconciles_partial_unresolved_target_selector(
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
    result = run_oracle((case,), context)[0]
    assert result.exact_match is True
    assert result.execution_status == "partial"
    assert result.result["holes"] == [
        {
            "dimension": "target",
            "candidates": ["component", "material", "process"],
        }
    ]


def test_run_writer_is_no_overwrite_and_never_creates_latest(tmp_path: Path) -> None:
    run = {"run_id": "controlled-v2", "variants": {}, "cases": []}
    target = write_run_artifacts(run, output_root=tmp_path / "runs")
    assert target.is_dir()
    assert not any(path.name.startswith("latest_") for path in tmp_path.rglob("*"))
    with pytest.raises(FileExistsError):
        write_run_artifacts(run, output_root=tmp_path / "runs")
