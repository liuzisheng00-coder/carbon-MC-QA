from __future__ import annotations

from pathlib import Path

import pytest

from dm2c_allocation_sensitivity import (
    AllocationPlan,
    build_allocation_variants,
    compare_component_allocations,
)
from dm2c_canonical_v2_reader import (
    iter_emissions,
    iter_product_contributions,
    load_canonical_v2_context,
    source_process_total,
)
from dm2c_m3_context import load_m3_execution_context
from tests.task11_v2_fixture import release_digest, write_task10_release


def _plans() -> tuple[AllocationPlan, ...]:
    return (
        AllocationPlan("mass", (("component:c1", 0.25), ("component:c2", 0.75))),
        AllocationPlan("duration", (("component:c1", 0.5), ("component:c2", 0.5))),
        AllocationPlan("machine_hours", (("component:c1", 0.0), ("component:c2", 1.0))),
    )


def test_each_basis_is_a_fresh_task9_loadable_controlled_release(tmp_path: Path) -> None:
    source = write_task10_release(tmp_path / "source-release")
    context = load_m3_execution_context(source)
    before = release_digest(source)
    variants = build_allocation_variants(
        context, output_root=tmp_path / "variants", plans=_plans()
    )
    assert [row.basis for row in variants] == ["mass", "duration", "machine_hours"]
    assert release_digest(source) == before
    assert not list((tmp_path / "variants").glob("latest_*"))
    for row in variants:
        loaded = load_canonical_v2_context(row.release_dir)
        assert source_process_total(loaded) == pytest.approx(31.0)
        allocated = [fact for fact in iter_emissions(loaded, kind="energy") if fact.mode == "allocated"]
        assert len(allocated) == 1
        fact = allocated[0]
        assert fact.emission_id == "emission:allocated"
        assert fact.consumption_id == "consumption:allocated"
        assert fact.quantity_value == pytest.approx(20.0)
        assert fact.factor_value == pytest.approx(1.0)
        shares = [
            item
            for item in iter_product_contributions(loaded)
            if item.emission_id == fact.emission_id
        ]
        assert sum(item.normalized_weight or 0.0 for item in shares) == pytest.approx(1.0)
        assert sum(item.projected_value for item in shares) == pytest.approx(20.0)


def test_zero_weight_contribution_is_explicit_and_traceable(tmp_path: Path) -> None:
    source = write_task10_release(tmp_path / "source-release")
    variants = build_allocation_variants(
        load_m3_execution_context(source),
        output_root=tmp_path / "variants",
        plans=_plans(),
    )
    machine = next(row for row in variants if row.basis == "machine_hours")
    loaded = load_canonical_v2_context(machine.release_dir)
    shares = [
        row
        for row in iter_product_contributions(loaded)
        if row.emission_id == "emission:allocated"
    ]
    zero = next(row for row in shares if row.component_id == "component:c1")
    assert zero.normalized_weight == pytest.approx(0.0)
    assert zero.projected_value == pytest.approx(0.0)
    assert zero.evidence_record_id


def test_sensitivity_reports_stable_ids_and_exact_material_names(tmp_path: Path) -> None:
    source = write_task10_release(tmp_path / "source-release")
    variants = build_allocation_variants(
        load_m3_execution_context(source),
        output_root=tmp_path / "variants",
        plans=_plans(),
    )
    report = compare_component_allocations(variants)
    assert report["allocation_source_value_kgCO2e"] == pytest.approx(20.0)
    assert report["source_process_total_kgCO2e"] == pytest.approx(31.0)
    assert report["module_ids"] == ["module:fixture"]
    assert {row["material_id"] for row in report["materials"]} == {
        "material:steel",
        "material:unused",
    }
    assert {row["name"] for row in report["materials"]} == {"Structural material"}
    assert "material_family" not in str(report).lower()


def test_variant_publication_fails_before_overwrite(tmp_path: Path) -> None:
    source = write_task10_release(tmp_path / "source-release")
    context = load_m3_execution_context(source)
    build_allocation_variants(context, output_root=tmp_path / "variants", plans=_plans())
    with pytest.raises(FileExistsError):
        build_allocation_variants(context, output_root=tmp_path / "variants", plans=_plans())
