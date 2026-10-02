from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import CanonicalSchemaError
from dm2c_e4_benchmark_builder import load_main_graph_context, total_material, total_process, total_product_process
from tests.task10_v2_fixture import write_task10_release


def test_graph_context_is_a_thin_immutable_canonical_adapter(tmp_path: Path) -> None:
    context = load_main_graph_context(write_task10_release(tmp_path / "release"))
    assert not hasattr(context, "calculated_atoms")
    assert not hasattr(context, "blocked_atoms")
    assert context.release_profile == "controlled-fixture"
    assert len(context.validation_rows) == 9
    with pytest.raises(FrozenInstanceError):
        context.release_profile = "actual-case"  # type: ignore[misc]


def test_totals_use_unambiguous_source_and_product_keys(tmp_path: Path) -> None:
    context = load_main_graph_context(write_task10_release(tmp_path / "release"))
    assert total_material(context) == pytest.approx(19.0)
    assert total_product_process(context) == pytest.approx(24.0)
    assert total_process(context) == pytest.approx(31.0)


def test_legacy_or_actual_unready_input_is_not_accepted(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "multigranular_carbon_kg.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CanonicalSchemaError):
        load_main_graph_context(legacy)
