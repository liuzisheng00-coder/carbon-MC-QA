from dm2c_multigranular_carbon_kg import FactorLibrary


def test_energy_factor_rejects_incompatible_quantity_unit():
    library = FactorLibrary(None)
    library.energy_factors = [
        {
            "keyword": "Grid electricity",
            "energyType": "electricity",
            "factor": 0.61,
            "unit": "kgCO2e/kWh",
            "source": "test factor",
        }
    ]

    result = library.resolve_energy_factor("electricity", "L")

    assert result.match_status == "unit_incompatible"
    assert result.factor_value == 0.0


def test_energy_factor_accepts_compatible_quantity_unit():
    library = FactorLibrary(None)
    library.energy_factors = [
        {
            "keyword": "Grid electricity",
            "energyType": "electricity",
            "factor": 0.61,
            "unit": "kgCO2e/kWh",
            "source": "test factor",
        }
    ]

    result = library.resolve_energy_factor("electricity", "kWh")

    assert result.match_status == "exact_or_keyword"
    assert result.factor_value == 0.61

