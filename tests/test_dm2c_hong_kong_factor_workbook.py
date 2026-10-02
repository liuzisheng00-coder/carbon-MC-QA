from pathlib import Path

import pytest

from dm2c_agentic_rag_v2 import (
    EnergyFactorRecord,
    ProcessCarbonPlanner,
    ProcessEvidence,
    RetrievalCandidate,
    WorkbookFactorStore,
)
from dm2c_multigranular_carbon_kg import FactorLibrary


FACTOR_WORKBOOK = (
    Path(__file__).resolve().parents[1]
    / "Embodied_Carbon_Coefficients_Hong_Kong_2026-07-15.xlsx"
)


def test_kg_factor_library_uses_selected_hong_kong_columns():
    library = FactorLibrary(FACTOR_WORKBOOK)

    assert library.load_error == ""
    assert len(library.material_factors) == 41

    aluminium = next(
        item
        for item in library.material_factors
        if item["category"] == "Aluminium"
        and item["subtype"] == "General Aluminium"
    )
    assert aluminium["factor"] == pytest.approx(8.886)
    assert aluminium["unit"] == "kgCO2e/kg"
    assert "HKUST ECO-CM" in aluminium["source"]

    clp_2023 = next(
        item
        for item in library.energy_factors
        if "CLP Power Hong Kong" in item["energyType"]
        and item["year"] == "2023"
    )
    assert clp_2023["factor"] == pytest.approx(0.39)
    assert clp_2023["unit"] == "kgCO2e/kWh"
    assert clp_2023["year"] == "2023"
    assert "CLP 2023" in clp_2023["source"]

    calcium_silicate = next(
        item
        for item in library.material_factors
        if item["category"] == "Calcium Silicate"
    )
    assert calcium_silicate["unit"] == "kgCO2/kg"

    guangdong_2023 = next(
        item
        for item in library.energy_factors
        if "Guangdong provincial average" in item["energyType"]
        and item["year"].startswith("2023")
    )
    assert guangdong_2023["unit"] == "kgCO2/kWh"


def test_agent_factor_store_reads_selected_values_and_boundary_metadata():
    store = WorkbookFactorStore(FACTOR_WORKBOOK)

    assert len(store.material_factors) == 41
    aluminium = next(
        item
        for item in store.material_factors
        if item.category == "Aluminium" and item.subtype == "General Aluminium"
    )
    assert aluminium.factor_value == pytest.approx(8.886)
    assert "A1-A3" in aluminium.source_note.replace("–", "-")
    assert "HKUST ECO-CM" in aluminium.source_note

    clp_2023 = next(
        item
        for item in store.energy_factors
        if "CLP Power Hong Kong" in item.energy_type and item.year == "2023"
    )
    assert clp_2023.factor_value == pytest.approx(0.39)
    assert clp_2023.factor_unit == "kgCO2e/kWh"
    assert clp_2023.year == "2023"
    assert clp_2023.quality_flag == ""

    calcium_silicate = next(
        item
        for item in store.material_factors
        if item.category == "Calcium Silicate"
    )
    assert calcium_silicate.factor_unit == "kgCO2/kg"

    guangdong_2023 = next(
        item
        for item in store.energy_factors
        if "Guangdong provincial average" in item.energy_type
        and item.year.startswith("2023")
    )
    assert guangdong_2023.factor_unit == "kgCO2/kWh"


def test_instructional_energy_row_is_retained_but_not_treated_as_numeric():
    store = WorkbookFactorStore(FACTOR_WORKBOOK)

    equipment = next(
        item
        for item in store.energy_factors
        if item.category == "Equipment" and item.energy_type == "Electric equipment"
    )
    assert equipment.factor_value is None
    assert equipment.quality_flag == "non_numeric_factor_or_instruction"
    assert "CLP" in equipment.notes


def test_latest_guangdong_factor_is_selected_without_relabeling_co2_as_co2e():
    store = WorkbookFactorStore(FACTOR_WORKBOOK)

    factor = store.choose_energy_factor("electricity", "Guangdong")

    assert factor is not None
    assert "Guangdong provincial average" in factor.energy_type
    assert factor.year.startswith("2023")
    assert factor.factor_value == pytest.approx(0.4419)
    assert factor.factor_unit == "kgCO2/kWh"


def test_co2_only_factor_cannot_enter_a_co2e_account():
    """A kgCO2 factor must not be usable where the account is denominated in kgCO2e.

    Both halves matter. The status makes the incompatibility visible to a reader
    of the evidence trail, and the absent normalized value makes it impossible
    for the factor to contribute a number even if some caller ignores the status.
    """
    library = FactorLibrary(FACTOR_WORKBOOK)

    material = library.resolve_material_factor("Calcium Silicate Sheet")
    energy = library.resolve_energy_factor("Guangdong provincial average 2023", "kWh")

    for factor in (material, energy):
        assert factor.match_status == "factor_scope_incompatible"
        assert factor.normalized_factor_value is None

    assert material.factor_unit == "kgCO2/kg"
    assert energy.factor_unit == "kgCO2/kWh"


def test_agent_process_planner_does_not_report_co2_only_factor_as_co2e():
    factor = EnergyFactorRecord(
        row_id="energy_guangdong_2023",
        category="Electricity (Mainland supplier)",
        energy_type="Guangdong provincial average — 2023",
        factor_value=0.4419,
        factor_unit="kgCO2/kWh",
        year="2023",
        source="test",
        notes="CO2-only",
        quality_flag="",
        source_file="test.xlsx",
    )

    class FactorStore:
        @staticmethod
        def choose_energy_factor(driver_kind, factory_grid_preference):
            return factor

    class ProfileStore:
        @staticmethod
        def find(evidence, driver_kind):
            return {"consumption_value": 100, "consumption_unit": "kWh"}

    evidence = ProcessEvidence(
        doc_id="process-1",
        title="Steel cutting",
        component_types=["IfcBeam"],
        keywords=["cutting"],
        activities=["cutting"],
        text="electric cutting equipment",
        source_file="test.jsonl",
    )
    result = ProcessCarbonPlanner(FactorStore(), ProfileStore(), "Guangdong").plan(
        [RetrievalCandidate(evidence, 1.0, ["cutting"], "test")]
    )

    assert result.value_kgco2e is None
    assert result.status == "requires_runtime_quantities"
    assert result.drivers[0].status == "incompatible_factor_scope"
