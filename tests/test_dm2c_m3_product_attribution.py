from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dm2c_m3_context import load_m3_execution_context
from tests.task10_v2_fixture import write_task10_release


def test_m3_context_is_immutable_and_contains_no_raw_graph_cache(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    context = load_m3_execution_context(release)
    assert context.release_profile == "controlled-fixture"
    assert context.controlled_fixture is True
    assert not hasattr(context, "graph")
    assert not hasattr(context, "nodes")
    with pytest.raises(FrozenInstanceError):
        context.release_profile = "actual-case"  # type: ignore[misc]
    with pytest.raises(TypeError):
        load_m3_execution_context(  # type: ignore[call-arg]
            release, operational_profile="actual-case"
        )
    with pytest.raises(TypeError):
        load_m3_execution_context(  # type: ignore[call-arg]
            release, availability={"energy": "complete"}
        )


def test_product_and_source_perspectives_follow_manuscript_equations(tmp_path: Path) -> None:
    context = load_m3_execution_context(write_task10_release(tmp_path / "release"))
    summary = context.project_summary
    assert summary["material_source_kgCO2e"] == pytest.approx(19.0)
    assert summary["product_energy_kgCO2e"] == pytest.approx(24.0)
    assert summary["product_total_kgCO2e"] == pytest.approx(43.0)
    assert summary["source_process_kgCO2e"] == pytest.approx(31.0)
    assert summary["all_source_kgCO2e"] == pytest.approx(50.0)
    assert summary["process_only_kgCO2e"] == pytest.approx(7.0)


def test_component_projection_uses_direct_and_exact_allocated_shares(tmp_path: Path) -> None:
    context = load_m3_execution_context(write_task10_release(tmp_path / "release"))
    totals = {row.entity_id: row.value_kgCO2e for row in context.component_summaries}
    assert totals == pytest.approx(
        {
            "component:c1": 15.0,
            "component:c2": 21.0,
            "component:no-facts": 6.0,
            "component:c4": 1.0,
        }
    )
    allocated = [row for row in context.projections if row.mode == "allocated"]
    assert {row.projection_key for row in allocated} == {
        ("allocated", "emission:allocated", "allocation:set:1", "component:c1"),
        ("allocated", "emission:allocated", "allocation:set:1", "component:c2"),
    }
    assert {row.component_id: row.value_kgCO2e for row in allocated} == pytest.approx(
        {"component:c1": 5.0, "component:c2": 15.0}
    )
    c1 = next(row for row in allocated if row.component_id == "component:c1")
    assert c1.recorded_for_occurrence_id == (
        "occ:allocated:recordedForObject:component:c1:allocation:evidence:c1"
    )
    assert c1.allocation_set_id == "allocation:set:1"
    assert c1.allocation_basis == "mass"
    assert c1.raw_weight == pytest.approx(0.25)
    assert c1.raw_weight_unit == "kg"
    assert c1.normalized_weight == pytest.approx(0.25)
    assert c1.evidence_record_id == "allocation:evidence:c1"
    assert c1.dimensions["ifc_class"] == "IfcBeam"
    with pytest.raises(TypeError):
        c1.dimensions["component"] = "component:c2"  # type: ignore[index]


def test_validation_coverage_is_typed_and_external_to_graph(tmp_path: Path) -> None:
    context = load_m3_execution_context(write_task10_release(tmp_path / "release"))
    index = context.validation_coverage
    assert index.accepted_count == 8
    assert index.rejected_count == 1
    rejected = next(row for row in index.records if row.status == "rejected")
    assert rejected.status == "rejected"
    assert rejected.kind == "energy"
    assert rejected.reason_code == "factor_not_found"
    assert rejected.emission_id is None
    assert rejected.component_ids == ()
