from pathlib import Path

import pytest

from dm2c_m23_units import (
    UnitError,
    convert_value,
    parse_factor_denominator,
    parse_unit,
    validate_quantity_factor,
)
from dm2c_multigranular_carbon_kg import FactorLibrary


def test_energy_conversions_are_explicit():
    assert convert_value(1000, "Wh", "kWh") == pytest.approx(1)
    assert convert_value(1, "MWh", "kWh") == pytest.approx(1000)


def test_length_area_and_volume_conversions_are_explicit():
    assert convert_value(1000, "mm", "m") == pytest.approx(1)
    assert convert_value(1_000_000, "mm2", "m2") == pytest.approx(1)
    assert convert_value(1_000_000_000, "mm3", "m3") == pytest.approx(1)


def test_equivalent_spelling_and_case_normalize_deterministically():
    assert parse_unit(" KWH ") == parse_unit("kWh")
    assert parse_unit("mm²") == parse_unit("mm^2") == parse_unit("MM2")
    assert parse_factor_denominator(" kgCO2e / KWH ").canonical_unit == "kWh"


@pytest.mark.parametrize("quantity_unit", ["h", "L"])
def test_non_energy_units_are_not_accepted_for_electricity_factor(quantity_unit):
    result = validate_quantity_factor(
        quantity_unit=quantity_unit,
        factor_unit="kgCO2e/kWh",
        quantity_scope="A1-A3",
        factor_scope="A1-A3",
        requested_scope="A1-A3",
    )

    assert not result.accepted
    assert result.reason_code == "unit_dimension_incompatible"


@pytest.mark.parametrize("factor_unit", ["kgCO2e", "kgCO2e/", "kgCO2e/furlong", "kgCO2/kWh"])
def test_factor_denominator_rejects_malformed_or_unknown_units(factor_unit):
    with pytest.raises(UnitError):
        parse_factor_denominator(factor_unit)


@pytest.mark.parametrize(
    ("quantity_scope", "factor_scope", "requested_scope", "reason_code"),
    [
        ("A1-A3", "A4-A5", "A1-A3", "factor_scope_incompatible"),
        ("A4-A5", "A1-A3", "A1-A3", "quantity_scope_incompatible"),
    ],
)
def test_scope_compatibility_is_controlled(quantity_scope, factor_scope, requested_scope, reason_code):
    result = validate_quantity_factor(
        quantity_unit="kWh",
        factor_unit="kgCO2e/kWh",
        quantity_scope=quantity_scope,
        factor_scope=factor_scope,
        requested_scope=requested_scope,
    )

    assert not result.accepted
    assert result.reason_code == reason_code


def test_validation_returns_canonical_units_and_conversion_factor():
    result = validate_quantity_factor(
        quantity_unit="Wh",
        factor_unit="kgCO2e/kWh",
        quantity_scope="A1-A3",
        factor_scope="A1-A3",
        requested_scope="A1-A3",
    )

    assert result.accepted
    assert result.source_canonical_unit == "kWh"
    assert result.target_canonical_unit == "kWh"
    assert result.conversion_factor == pytest.approx(0.001)


def test_scope_compatibility_accepts_dash_variants_and_boundary_notes():
    result = validate_quantity_factor(
        quantity_unit="kg",
        factor_unit="kgCO2e/kg",
        quantity_scope="A1-A3",
        factor_scope="A1–A3 / cradle-to-gate",
        requested_scope=" A1 — A3 ",
    )

    assert result.accepted
    assert result.reason_code == "accepted"


def _energy_row(row_id, factor=0.6205, **overrides):
    row = {
        "sourceRowId": row_id,
        "keyword": "Electricity carbon footprint",
        "category": "Electricity",
        "energyType": "electricity",
        "factor": factor,
        "unit": "kgCO2e/kWh",
        "year": "2024",
        "source": "Hong Kong workbook",
        "boundary": "A1-A3",
        "sourceUrl": "https://example.test/factor",
        "sourceNote": "proxy: HK electricity",
        "geography": "Hong Kong",
        "proxyStatus": "proxy",
    }
    row.update(overrides)
    return row


def test_strict_factor_resolution_accepts_exactly_one_compatible_candidate():
    library = FactorLibrary(None)
    library.energy_factors = [_energy_row("workbook:energy:12")]

    factor = library.resolve_energy_factor_strict_v2(
        "electricity", "Wh", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert factor is not None
    assert factor.factor_id == "workbook:energy:12"
    assert factor.denominator_unit == "kWh"
    assert factor.normalized_denominator == "kWh"
    assert factor.normalized_factor_value == pytest.approx(0.6205)
    assert factor.source_row_id == "workbook:energy:12"


def test_strict_factor_resolution_rejects_equally_compatible_candidates_as_ambiguous():
    library = FactorLibrary(None)
    library.energy_factors = [_energy_row("workbook:energy:12"), _energy_row("workbook:energy:13")]

    factor = library.resolve_energy_factor_strict_v2(
        "electricity", "kWh", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert factor is None
    assert library.last_strict_resolution_reason == "ambiguous_factor"


def test_strict_unresolved_factor_returns_no_record_and_never_a_zero_placeholder():
    library = FactorLibrary(None)
    library.energy_factors = [_energy_row("workbook:energy:12", unit="kgCO2e/kWh")]

    factor = library.resolve_energy_factor_strict_v2(
        "electricity", "L", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert factor is None
    assert library.last_strict_resolution_reason == "unit_dimension_incompatible"


def test_strict_factor_retains_workbook_row_provenance_exactly():
    library = FactorLibrary(None)
    library.energy_factors = [_energy_row("sha256:abc:Energy Emission Factors:27")]

    factor = library.resolve_energy_factor_strict_v2(
        "electricity", "kWh", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert factor is not None
    assert factor.source_row_id == "sha256:abc:Energy Emission Factors:27"
    assert factor.geography == "Hong Kong"
    assert factor.year == "2024"
    assert factor.system_boundary == "A1-A3"
    assert factor.source_reference == "https://example.test/factor"
    assert factor.proxy_status == "proxy"


def _write_energy_workbook(path: Path, unit: str, energy_type: str = "electricity") -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Energy Emission Factors"
    worksheet.append(
        [
            "Category",
            "Sub-type / Year",
            "Selected Factor",
            "Unit",
            "Factor Year",
            "Original source / database",
            "Life-cycle boundary",
            "Source URL",
            "Selection / boundary note",
        ]
    )
    worksheet.append(
        [
            "Energy",
            energy_type,
            0.5,
            unit,
            "2024",
            "test workbook source",
            "A1-A3",
            "https://example.test/workbook",
            "test proxy note",
        ]
    )
    workbook.save(path)


def test_loader_preserves_blank_factor_unit_and_strict_v2_rejects_it(tmp_path):
    workbook_path = tmp_path / "blank-unit.xlsx"
    _write_energy_workbook(workbook_path, unit="")

    library = FactorLibrary(workbook_path)
    factor = library.resolve_energy_factor_strict_v2(
        "electricity", "kWh", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert library.energy_factors[0]["unit"] == ""
    assert factor is None
    assert library.last_strict_resolution_reason == "factor_denominator_invalid"


@pytest.mark.parametrize(
    ("rows", "requested_identity"),
    [
        ([_energy_row("energy:biogas", energyType="biogas")], "gas"),
        ([_energy_row("energy:natural-gas", energyType="natural gas")], "gas"),
    ],
)
def test_strict_energy_identity_does_not_accept_substring_only_matches(rows, requested_identity):
    library = FactorLibrary(None)
    library.energy_factors = rows

    factor = library.resolve_energy_factor_strict_v2(
        requested_identity, "kWh", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert factor is None
    assert library.last_strict_resolution_reason == "unresolved_factor"


def test_strict_material_identity_does_not_accept_substring_only_matches():
    library = FactorLibrary(None)
    library.material_factors = [
        {
            "sourceRowId": "material:stainless-steel",
            "keyword": "stainless steel",
            "category": "stainless steel",
            "subtype": "",
            "factor": 2.5,
            "unit": "kgCO2e/kg",
            "year": "2024",
            "source": "test workbook",
            "boundary": "A1-A3",
            "sourceUrl": "https://example.test/material",
            "sourceNote": "source factor",
        }
    ]

    factor = library.resolve_material_factor_strict_v2(
        "steel", "kg", quantity_scope="A1-A3", requested_scope="A1-A3"
    )

    assert factor is None
    assert library.last_strict_resolution_reason == "unresolved_factor"
