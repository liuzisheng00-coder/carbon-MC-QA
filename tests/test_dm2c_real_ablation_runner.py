from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from dm2c_full_qa_experiment_runner import load_full_qa_context
from dm2c_real_ablation_runner import (
    REAL_ABLATION_POLICIES,
    publish_ablation_run,
    run_real_ablation_suite,
)
from tests.task11_v2_fixture import (
    controlled_programs,
    program,
    write_actual_like_no_energy_release,
    write_task10_release,
)


def test_ablation_runner_cli_documents_four_policy_scope_and_release_pin() -> None:
    completed = subprocess.run(
        [sys.executable, "dm2c_real_ablation_runner.py", "--help"],
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
    for variant in REAL_ABLATION_POLICIES:
        assert variant in completed.stdout
    assert "legacy variants are out of scope" in completed.stdout
    assert "e4_balanced_benchmark_150_human_reviewed_v10_layerdedup.jsonl" in completed.stdout


def test_each_policy_changes_only_one_disclosure_or_validation_control() -> None:
    validation = REAL_ABLATION_POLICIES["no_validation_gate_real"]
    disclosure = REAL_ABLATION_POLICIES["no_status_blocking_real"]
    provenance = REAL_ABLATION_POLICIES["no_provenance_constraint_real"]
    assert (validation.validation_gate, validation.status_blocking, validation.provenance_constraint) == (
        False,
        True,
        True,
    )
    assert (disclosure.validation_gate, disclosure.status_blocking, disclosure.provenance_constraint) == (
        True,
        False,
        True,
    )
    assert (provenance.validation_gate, provenance.status_blocking, provenance.provenance_constraint) == (
        True,
        True,
        False,
    )


def test_status_disclosure_ablation_preserves_resolution_and_scope(tmp_path: Path) -> None:
    context = load_full_qa_context(write_task10_release(tmp_path / "release"))
    cases = (
        ("product", controlled_programs()[0][1], ()),
        ("unknown", controlled_programs()[4][1], ("component:missing",)),
    )
    suites = run_real_ablation_suite(
        cases,
        context,
        variants=("full_real", "no_status_blocking_real"),
    )
    full = suites["full_real"]["cases"]
    ablated = suites["no_status_blocking_real"]["cases"]
    assert full[0]["perspective"] == ablated[0]["perspective"] == "product"
    assert full[0]["projection_keys"] == ablated[0]["projection_keys"]
    assert full[1]["status"] == ablated[1]["status"] == "unresolved_target"


def test_validation_gate_ablation_changes_disclosure_but_never_fails_open(
    tmp_path: Path,
) -> None:
    context = load_full_qa_context(write_task10_release(tmp_path / "release"))
    cases = (
        ("energy", controlled_programs()[2][1], ()),
        ("unknown", controlled_programs()[4][1], ("component:missing",)),
    )
    suites = run_real_ablation_suite(
        cases,
        context,
        variants=("full_real", "no_validation_gate_real"),
    )
    full = suites["full_real"]["cases"]
    ablated = suites["no_validation_gate_real"]["cases"]
    assert full[0]["status"] == ablated[0]["status"] == "incomplete_path"
    assert full[0]["coverage_status"] == "relevant_rejection"
    assert ablated[0]["coverage_status"] == "not_evaluated"
    assert full[0]["validation_gate_applied"] is True
    assert ablated[0]["validation_gate_applied"] is False
    for field in ("perspective", "operation", "rows", "summary", "emission_ids", "projection_keys"):
        assert full[0][field] == ablated[0][field]
    assert full[1]["status"] == ablated[1]["status"] == "unresolved_target"
    assert full[1]["projection_keys"] == ablated[1]["projection_keys"] == []


def test_ablation_preserves_ambiguous_empty_zero_and_unavailable_boundaries(tmp_path: Path) -> None:
    context = load_full_qa_context(write_task10_release(tmp_path / "release"))
    ambiguous = program(
        {"op": "ResolveEntities", "entity_type": "component", "property": "name", "value": "Structural member", "cardinality": "singleton"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    empty = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Filter", "field": "factor_keyword", "equals": "not-present"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    cases = (
        ("ambiguous", ambiguous, ()),
        ("empty", empty, ()),
        ("zero", controlled_programs()[3][1], ("component:c4",)),
    )
    suites = run_real_ablation_suite(
        cases, context, variants=("full_real", "no_status_blocking_real")
    )
    for index, expected in enumerate(
        ("clarification_required", "empty_result", "executable")
    ):
        assert suites["full_real"]["cases"][index]["status"] == expected
        assert suites["no_status_blocking_real"]["cases"][index]["status"] == expected
        assert (
            suites["full_real"]["cases"][index]["projection_keys"]
            == suites["no_status_blocking_real"]["cases"][index]["projection_keys"]
        )

    actual_like = load_full_qa_context(
        write_actual_like_no_energy_release(tmp_path / "actual-like")
    )
    unavailable = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    unavailable_suites = run_real_ablation_suite(
        (("unavailable", unavailable, ()),),
        actual_like,
        variants=("full_real", "no_status_blocking_real"),
    )
    assert unavailable_suites["full_real"]["cases"][0]["status"] == "incomplete_path"
    assert unavailable_suites["no_status_blocking_real"]["cases"][0]["status"] == "incomplete_path"


def test_ablation_publication_never_writes_latest_and_fails_if_existing(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    target = publish_ablation_run(
        {"status": "complete"}, output_root=output_root, run_id="controlled-v2-ablation"
    )
    assert (target / "ablation_report.json").is_file()
    assert not list(output_root.glob("latest_*"))
    with pytest.raises(FileExistsError):
        publish_ablation_run({}, output_root=output_root, run_id="controlled-v2-ablation")
