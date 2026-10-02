from __future__ import annotations

import math
from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_case_study_facts import build_facts, render_markdown

RELEASE = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)


@pytest.fixture(scope="module")
def context():
    return load_canonical_v2_context(RELEASE, allow_synthetic=True)


@pytest.fixture(scope="module")
def facts(context):
    return build_facts(context)


def test_facts_name_the_release_they_were_derived_from(facts) -> None:
    release = facts["release"]
    assert release["release_id"] == RELEASE.name
    assert release["schema_version"] == "m23-canonical-v2"
    assert release["requested_scope"] == "A1-A3"
    # The factory energy of this fixture is synthetic, and the section must say so.
    assert release["synthetic_factory_inputs"] is True


def test_totals_reconcile_with_the_release_projection_totals(facts) -> None:
    """The manuscript totals must equal the release's own projection totals."""
    totals = facts["carbon_totals"]
    product = totals["product_perspective_total_kgCO2e"]
    process = totals["process_perspective_recorded_kgCO2e"]
    assert product == pytest.approx(9730.496, abs=1e-3)
    assert process == pytest.approx(1386.441, abs=1e-3)
    # A1 and the attributed part of A3 are what the product perspective carries.
    assert totals["A1_material_kgCO2e"] + totals[
        "A3_process_attributed_kgCO2e"
    ] == pytest.approx(product, abs=1e-3)


def test_the_two_perspectives_are_not_one_total_split_in_two(facts) -> None:
    totals = facts["carbon_totals"]
    unattributable = totals["process_only_unattributable_kgCO2e"]
    assert unattributable > 0
    assert totals["process_only_record_count"] == 8
    assert totals["partial_unattributable_record_count"] == 3
    assert totals["A3_process_attributed_kgCO2e"] + unattributable == pytest.approx(
        totals["process_perspective_recorded_kgCO2e"], abs=1e-3
    )


def test_material_breakdown_sums_to_the_a1_total(facts) -> None:
    rows = facts["material_breakdown"]
    assert rows
    total = math.fsum(sorted(row["kgCO2e"] for row in rows))
    assert total == pytest.approx(facts["carbon_totals"]["A1_material_kgCO2e"], abs=1e-2)


def test_carrier_and_stage_breakdowns_each_sum_to_recorded_process_carbon(facts) -> None:
    recorded = facts["carbon_totals"]["process_perspective_recorded_kgCO2e"]
    for key in ("by_carrier", "by_stage"):
        rows = facts["energy_breakdown"][key]
        assert rows
        total = math.fsum(sorted(row["kgCO2e"] for row in rows))
        assert total == pytest.approx(recorded, abs=1e-2), key


def test_every_energy_record_resolves_to_a_named_production_stage(facts) -> None:
    """Energy hangs off an activity, and the stage is one hop up.

    A null stage here means that walk regressed and the process perspective has
    silently collapsed into a single unlabelled group.
    """
    stages = facts["energy_breakdown"]["by_stage"]
    assert all(row["stage"] != "(unattributed)" for row in stages)
    assert sum(row["record_count"] for row in stages) == 17


def test_coverage_counts_add_up(facts) -> None:
    coverage = facts["coverage"]
    assert coverage["accepted_count"] + coverage["rejected_count"] == coverage[
        "candidate_count"
    ]
    assert sum(coverage["rejected_by_reason"].values()) == coverage["rejected_count"]
    assert sum(coverage["accepted_by_kind"].values()) == coverage["accepted_count"]


def test_component_count_matches_the_graph_class_count(facts) -> None:
    assert (
        facts["ifc_composition"]["component_count"]
        == facts["graph"]["class_counts"]["BuildingComponent"]
    )


def test_every_applied_factor_carries_its_basis(facts) -> None:
    """A bare number is not a citable factor; boundary and source must travel with it."""
    rows = facts["emission_factors"]
    assert len(rows) == 9
    for row in rows:
        assert row["keyword"]
        assert row["unit"]
        assert row["system_boundary"]
        assert row["source"]
        assert isinstance(row["value"], float)


def test_open_items_are_carried_into_the_rendered_table(facts) -> None:
    text = render_markdown(facts)
    assert "## Open items" in text
    for item in facts["open_items"]:
        assert item in text


def test_author_supplied_context_is_rendered_apart_from_derived_facts(facts) -> None:
    """Project context must not be presentable as a release-derived number."""
    text = render_markdown(facts)
    assert "## Case context supplied by the authors" in text
    assert "not derived from the release" in text
    for field, value in facts["case_context"]:
        assert field in text
        assert value in text
