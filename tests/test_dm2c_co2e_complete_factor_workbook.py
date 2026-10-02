from pathlib import Path

import pytest

from dm2c_agentic_rag_v2 import WorkbookFactorStore
from dm2c_multigranular_carbon_kg import FactorLibrary


CO2E_FACTOR_WORKBOOK = (
    Path(__file__).resolve().parents[1]
    / "outputs"
    / "research_experiments"
    / "case_study_facts_20260715"
    / "co2e_complete_factors"
    / "Embodied_Carbon_Coefficients_Hong_Kong_CO2e_Complete_2026-07-15.xlsx"
)


def test_case_material_proxies_are_complete_co2e_factors():
    library = FactorLibrary(CO2E_FACTOR_WORKBOOK)

    assert library.load_error == ""
    calcium = library.resolve_material_factor("Calcium Silicate Sheet")
    fibre_cement = library.resolve_material_factor("Fibre Cement Fire-rated Board")

    assert calcium.factor_value == pytest.approx(16.5 / 21.2)
    assert calcium.factor_unit == "kgCO2e/kg"
    assert calcium.match_status == "exact_or_keyword"
    assert fibre_cement.factor_value == pytest.approx(0.6436)
    assert fibre_cement.factor_unit == "kgCO2e/kg"
    assert fibre_cement.match_status == "exact_or_keyword"


def test_case_energy_proxies_resolve_to_complete_co2e_factors():
    library = FactorLibrary(CO2E_FACTOR_WORKBOOK)

    electricity = library.resolve_energy_factor("electricity", "kWh")
    diesel = library.resolve_energy_factor("diesel", "L")

    assert electricity.factor_value == pytest.approx(0.5168)
    assert electricity.factor_unit == "kgCO2e/kWh"
    assert electricity.match_status == "exact_or_keyword"
    assert diesel.factor_value == pytest.approx(2.6593717369127514 + 0.62409)
    assert diesel.factor_unit == "kgCO2e/L"
    assert diesel.match_status == "exact_or_keyword"


def test_agent_store_prefers_case_co2e_electricity_proxy_over_co2_only_row():
    store = WorkbookFactorStore(CO2E_FACTOR_WORKBOOK)

    factor = store.choose_energy_factor("electricity", "Guangdong")

    assert factor is not None
    assert "guangdong lifecycle" in factor.energy_type.casefold()
    assert factor.factor_value == pytest.approx(0.5168)
    assert factor.factor_unit == "kgCO2e/kWh"


def test_co2_only_comparators_remain_available_but_are_not_case_selected():
    store = WorkbookFactorStore(CO2E_FACTOR_WORKBOOK)

    guangdong = next(
        factor
        for factor in store.energy_factors
        if "Guangdong provincial average" in factor.energy_type
        and factor.year.startswith("2023")
    )

    assert guangdong.factor_value == pytest.approx(0.4419)
    assert guangdong.factor_unit == "kgCO2/kWh"
    # This CO2-only provincial comparator remains available but cannot populate a CO2e account.
    assert guangdong.factor_value != pytest.approx(0.5168)
