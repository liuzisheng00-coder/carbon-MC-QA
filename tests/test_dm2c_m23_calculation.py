from dataclasses import FrozenInstanceError, replace

import pytest

from dm2c_m23_calculation import (
    AcceptedConsumption,
    ConsumptionCandidate,
    OperandConversion,
    QuantityOperand,
    ValidationIssue,
    validate_candidates,
    validate_energy_candidate,
    validate_material_candidate,
)
from dm2c_multigranular_carbon_kg import (
    FactorRecord,
    LPGGraph,
    MultiGranularCarbonKGBuilder,
)


SCOPE = "A1-A3"
SOURCE = "sha256:factory-or-ifc-source"


def operand(
    role: str,
    value: float,
    unit: str,
    *,
    suffix: str | None = None,
    source_id: str | None = "source:operand",
    design_quantity_id: str | None = None,
) -> QuantityOperand:
    suffix = suffix or role
    return QuantityOperand(
        operand_id=f"operand:{suffix}",
        role=role,
        value=value,
        unit=unit,
        source_id=source_id or "",
        design_quantity_id=design_quantity_id,
    )


def factor(
    *,
    value: float = 2.0,
    denominator: str = "kg",
    boundary: str = SCOPE,
) -> FactorRecord:
    canonical = {
        "g": ("kg", value / 0.001),
        "kg": ("kg", value),
        "Wh": ("kWh", value / 0.001),
        "kWh": ("kWh", value),
        "MWh": ("kWh", value / 1000.0),
    }[denominator]
    return FactorRecord(
        factor_id=f"factor:test:{denominator}",
        keyword="test factor",
        factor_value=value,
        factor_unit=f"kgCO2e/{denominator}",
        source="test factor source",
        match_status="accepted_strict_v2",
        confidence=1.0,
        source_row_id=f"workbook:test:row:{denominator}",
        original_factor_unit=f"kgCO2e/{denominator}",
        denominator_unit=canonical[0],
        normalized_factor_value=canonical[1],
        normalized_denominator=canonical[0],
        system_boundary=boundary,
        source_reference="doi:test-factor",
        validation_status="accepted",
    )


def material_candidate(**overrides) -> ConsumptionCandidate:
    values = {
        "record_id": "record:material:1",
        "kind": "material",
        "source_record_id": "ifc-association:91:material:31",
        "evidence_source_id": SOURCE,
        "product_target_id": "component:gid-1",
        "material_id": "ifc_material:31",
        "recorded_scope": SCOPE,
        "requested_scope": SCOPE,
        "factor": factor(),
        "factor_resolution_reason": "",
        "formula_code": "direct_mass",
        "operands": (operand("material_mass", 100.0, "kg"),),
        "measured": True,
    }
    values.update(overrides)
    return ConsumptionCandidate(**values)


def energy_candidate(**overrides) -> ConsumptionCandidate:
    values = {
        "record_id": "record:energy:1",
        "kind": "energy",
        "source_record_id": "meter:1",
        "evidence_source_id": SOURCE,
        "product_target_id": "component:gid-1",
        "energy_carrier_id": "carrier:electricity",
        "recorded_scope": SCOPE,
        "requested_scope": SCOPE,
        "factor": factor(value=0.5, denominator="kWh"),
        "factor_resolution_reason": "",
        "formula_code": "measured_energy",
        "operands": (operand("energy_quantity", 10.0, "kWh"),),
        "measured": True,
    }
    values.update(overrides)
    return ConsumptionCandidate(**values)


def assert_rejected(result, reason_code: str) -> ValidationIssue:
    assert result.accepted is None
    assert result.issue is not None
    assert result.issue.reason_code == reason_code
    return result.issue


def test_rejected_candidate_preserves_typed_target_and_scope_evidence() -> None:
    material = assert_rejected(
        validate_material_candidate(
            material_candidate(factor=None, factor_resolution_reason="factor_missing")
        ),
        "factor_missing",
    )
    assert dict(material.evidence) == {
        "kind": "material",
        "sourceRecordId": "ifc-association:91:material:31",
        "evidenceSourceId": SOURCE,
        "formulaCode": "direct_mass",
        "targetComponentId": "component:gid-1",
        "targetMaterialId": "ifc_material:31",
        "recordedScope": SCOPE,
        "requestedScope": SCOPE,
        "factorPresent": False,
    }
    energy = assert_rejected(
        validate_energy_candidate(
            energy_candidate(
                factor=None,
                factor_resolution_reason="factor_missing",
                process_ids=("process:cutting",),
            )
        ),
        "factor_missing",
    )
    assert energy.evidence["energyCarrierId"] == "carrier:electricity"
    assert energy.evidence["processIds"] == ("process:cutting",)


def test_direct_material_mass_is_accepted_and_computes_q_times_ef():
    result = validate_material_candidate(material_candidate())

    assert result.issue is None
    accepted = result.accepted
    assert isinstance(accepted, AcceptedConsumption)
    assert accepted.quantity_value == pytest.approx(100.0)
    assert accepted.quantity_unit == "kg"
    assert accepted.factor_value == pytest.approx(2.0)
    assert accepted.factor_denominator == "kg"
    assert accepted.emission_value == pytest.approx(200.0)
    assert accepted.emission_unit == "kgCO2e"


def test_volume_requires_sourced_compatible_density():
    volume = operand(
        "material_volume", 2.0, "m3", design_quantity_id="design_quantity:401"
    )
    missing = material_candidate(formula_code="volume_density", operands=(volume,))
    assert_rejected(validate_material_candidate(missing), "density_missing")

    unsourced = material_candidate(
        formula_code="volume_density",
        operands=(volume, operand("density", 7850.0, "kg/m3", source_id="")),
    )
    assert_rejected(validate_material_candidate(unsourced), "operand_source_missing")

    accepted = validate_material_candidate(
        material_candidate(
            formula_code="volume_density",
            operands=(volume, operand("density", 7850.0, "kg/m3")),
        )
    ).accepted
    assert accepted.quantity_value == pytest.approx(15700.0)
    assert accepted.emission_value == pytest.approx(31400.0)


@pytest.mark.parametrize("missing_role", ["thickness", "density"])
def test_area_requires_explicit_sourced_thickness_and_density(missing_role):
    inputs = {
        "material_area": operand(
            "material_area", 10.0, "m2", design_quantity_id="design_quantity:402"
        ),
        "thickness": operand("thickness", 100.0, "mm", source_id="ifc-layer:8"),
        "density": operand("density", 2400.0, "kg/m3", source_id="density-table:4"),
    }
    inputs.pop(missing_role)
    candidate = material_candidate(
        formula_code="area_thickness_density", operands=tuple(inputs.values())
    )

    expected = "thickness_missing" if missing_role == "thickness" else "density_missing"
    assert_rejected(validate_material_candidate(candidate), expected)


def test_area_thickness_density_preserves_explicit_operands():
    inputs = (
        operand("material_area", 10.0, "m2", design_quantity_id="design_quantity:402"),
        operand("thickness", 100.0, "mm", source_id="ifc-layer:8"),
        operand("density", 2400.0, "kg/m3", source_id="density-table:4"),
    )
    accepted = validate_material_candidate(
        material_candidate(formula_code="area_thickness_density", operands=inputs)
    ).accepted

    assert accepted.quantity_value == pytest.approx(2400.0)
    assert accepted.operands == inputs
    assert accepted.formula_code == "area_thickness_density"


def test_fraction_requires_source_identity_and_parent_design_quantity():
    parent_without_design_id = operand("parent_design_quantity", 100.0, "kg")
    fraction = operand("fraction", 0.4, "1", source_id="ifc-constituent:5")
    candidate = material_candidate(
        formula_code="fraction_of_design_quantity",
        operands=(parent_without_design_id, fraction),
    )
    assert_rejected(validate_material_candidate(candidate), "design_quantity_id_missing")

    sourced_parent = operand(
        "parent_design_quantity",
        100.0,
        "kg",
        source_id="ifc-qto:401",
        design_quantity_id="design_quantity:401",
    )
    unsourced_fraction = operand("fraction", 0.4, "1", source_id="")
    candidate = material_candidate(
        formula_code="fraction_of_design_quantity",
        operands=(sourced_parent, unsourced_fraction),
    )
    assert_rejected(validate_material_candidate(candidate), "operand_source_missing")

    accepted = validate_material_candidate(
        material_candidate(
            formula_code="fraction_of_design_quantity",
            operands=(sourced_parent, fraction),
        )
    ).accepted
    assert accepted.quantity_value == pytest.approx(40.0)


@pytest.mark.parametrize(
    ("formula_code", "expected_reason"),
    [("none", "material_quantity_basis_missing"), ("equal_split", "equal_split_prohibited")],
)
def test_multimaterial_without_explicit_allocation_evidence_is_rejected(
    formula_code, expected_reason
):
    result = validate_material_candidate(
        material_candidate(formula_code=formula_code, operands=())
    )
    assert_rejected(result, expected_reason)


def test_rejected_constituent_does_not_renormalize_accepted_fraction():
    parent = operand(
        "parent_design_quantity",
        100.0,
        "kg",
        source_id="ifc-qto:401",
        design_quantity_id="design_quantity:401",
    )
    accepted_candidate = material_candidate(
        record_id="record:material:accepted",
        source_record_id="association:accepted",
        formula_code="fraction_of_design_quantity",
        operands=(parent, operand("fraction", 0.4, "1", source_id="ifc-constituent:5")),
    )
    rejected_candidate = material_candidate(
        record_id="record:material:rejected",
        source_record_id="association:rejected",
        formula_code="fraction_of_design_quantity",
        operands=(parent, operand("fraction", 0.6, "1", source_id="")),
    )

    result = validate_candidates((rejected_candidate, accepted_candidate))

    assert len(result.accepted) == 1
    assert result.accepted[0].quantity_value == pytest.approx(40.0)
    assert result.accepted[0].emission_value == pytest.approx(80.0)
    assert [issue.reason_code for issue in result.rejected] == ["operand_source_missing"]


def test_none_factor_rejects_without_creating_a_zero_factor():
    result = validate_material_candidate(
        material_candidate(factor=None, factor_resolution_reason="ambiguous_factor")
    )

    issue = assert_rejected(result, "ambiguous_factor")
    assert issue.evidence["factorPresent"] is False


def test_unit_and_scope_mismatch_have_specific_reasons():
    unit_mismatch = material_candidate(factor=factor(value=0.5, denominator="kWh"))
    assert_rejected(
        validate_material_candidate(unit_mismatch), "unit_dimension_incompatible"
    )

    scope_mismatch = energy_candidate(factor=factor(value=0.5, denominator="kWh", boundary="A4"))
    assert_rejected(
        validate_energy_candidate(scope_mismatch), "factor_scope_incompatible"
    )


def test_negative_quantity_is_rejected():
    result = validate_energy_candidate(
        energy_candidate(operands=(operand("energy_quantity", -1.0, "kWh"),))
    )
    assert_rejected(result, "quantity_negative")


def test_measured_energy_zero_with_valid_evidence_is_accepted():
    result = validate_energy_candidate(
        energy_candidate(operands=(operand("energy_quantity", 0.0, "kWh"),))
    )

    assert result.issue is None
    assert result.accepted.quantity_value == 0.0
    assert result.accepted.emission_value == 0.0
    assert result.accepted.is_valid_zero is True
    assert result.accepted.isValidZero is True


@pytest.mark.parametrize(
    "candidate",
    [
        energy_candidate(
            measured=False, operands=(operand("energy_quantity", 0.0, "kWh"),)
        ),
        energy_candidate(
            evidence_source_id="", operands=(operand("energy_quantity", 0.0, "kWh"),)
        ),
    ],
)
def test_zero_without_measurement_or_source_evidence_is_rejected(candidate):
    result = validate_energy_candidate(candidate)
    assert result.accepted is None
    assert result.issue.reason_code in {"zero_evidence_missing", "evidence_source_missing"}


@pytest.mark.parametrize(
    ("value", "unit", "expected_quantity", "expected_emission"),
    [(1000.0, "Wh", 1.0, 0.5), (1.0, "MWh", 1000.0, 500.0)],
)
def test_energy_is_explicitly_normalized_to_factor_denominator(
    value, unit, expected_quantity, expected_emission
):
    accepted = validate_energy_candidate(
        energy_candidate(operands=(operand("energy_quantity", value, unit),))
    ).accepted

    assert accepted.quantity_value == pytest.approx(expected_quantity)
    assert accepted.quantity_unit == "kWh"
    assert accepted.quantity_conversion_factor == pytest.approx(expected_quantity / value)
    assert accepted.emission_value == pytest.approx(expected_emission)


def test_staged_builder_validation_is_pure_and_does_not_mutate_graph():
    builder = MultiGranularCarbonKGBuilder.__new__(MultiGranularCarbonKGBuilder)
    builder.graph = LPGGraph()
    builder.graph.add_node("existing", ["BuildingComponent"], {"name": "keep"})
    before = (dict(builder.graph.nodes), list(builder.graph.edges))

    result = builder.validate_consumption_candidates(
        (material_candidate(), material_candidate(record_id="bad", factor=None))
    )

    assert len(result.accepted) == 1
    assert len(result.rejected) == 1
    assert (builder.graph.nodes, builder.graph.edges) == before


def test_validate_candidates_is_deterministic_and_rejects_duplicate_source_identity():
    a = energy_candidate(record_id="a", source_record_id="meter:a")
    z = material_candidate(record_id="z", source_record_id="association:z")
    duplicate_1 = energy_candidate(record_id="d1", source_record_id="meter:duplicate")
    duplicate_2 = energy_candidate(record_id="d2", source_record_id="meter:duplicate")

    first = validate_candidates((z, duplicate_2, a, duplicate_1))
    second = validate_candidates((duplicate_1, a, duplicate_2, z))

    assert first == second
    assert [row.record_id for row in first.accepted] == ["a", "z"]
    assert [row.record_id for row in first.rejected] == ["d1", "d2"]
    assert {row.reason_code for row in first.rejected} == {"duplicate_source_record_id"}


def test_accepted_calculation_keeps_all_ordered_operand_source_ids():
    inputs = (
        operand(
            "material_area",
            2.0,
            "m2",
            suffix="area",
            source_id="ifc-qto:402",
            design_quantity_id="design_quantity:402",
        ),
        operand("thickness", 50.0, "mm", suffix="thickness", source_id="ifc-layer:8"),
        operand("density", 2400.0, "kg/m3", suffix="density", source_id="density:4"),
    )
    accepted = validate_material_candidate(
        material_candidate(formula_code="area_thickness_density", operands=inputs)
    ).accepted

    assert accepted.operands == inputs
    assert [row.operand_id for row in accepted.operands] == [
        "operand:area",
        "operand:thickness",
        "operand:density",
    ]
    assert [row.source_id for row in accepted.operands] == [
        "ifc-qto:402",
        "ifc-layer:8",
        "density:4",
    ]
    assert accepted.quantity_conversion_factor == pytest.approx(1.0)


def test_public_validation_records_are_immutable():
    candidate = energy_candidate()
    with pytest.raises(FrozenInstanceError):
        candidate.record_id = "changed"

    accepted = validate_energy_candidate(candidate).accepted
    with pytest.raises(FrozenInstanceError):
        accepted.emission_value = 99.0

    issue = validate_energy_candidate(energy_candidate(factor=None)).issue
    with pytest.raises(TypeError):
        issue.evidence["new"] = "not allowed"

    with pytest.raises(FrozenInstanceError):
        candidate.factor.factor_value = 99.0


@pytest.mark.parametrize(
    ("factor_override", "expected_reason"),
    [
        ({"match_status": "legacy_exact"}, "factor_not_strict_v2"),
        ({"validation_status": "rejected"}, "factor_not_strict_v2"),
        ({"source_row_id": ""}, "factor_source_missing"),
        ({"normalized_factor_value": None}, "factor_normalization_missing"),
        ({"normalized_factor_value": 999.0}, "factor_normalization_inconsistent"),
        ({"normalized_denominator": "kWh"}, "factor_normalization_inconsistent"),
    ],
)
def test_only_coherent_strict_v2_factor_records_are_accepted(
    factor_override, expected_reason
):
    candidate = material_candidate(factor=replace(factor(), **factor_override))
    assert_rejected(validate_material_candidate(candidate), expected_reason)


def test_deterministic_order_is_total_even_when_primary_candidate_ids_match():
    no_factor = material_candidate(
        record_id="same",
        source_record_id="same-source",
        factor=None,
        factor_resolution_reason="ambiguous_factor",
    )
    prohibited = material_candidate(
        record_id="same",
        source_record_id="same-source",
        formula_code="equal_split",
        operands=(),
    )

    first = validate_candidates((no_factor, prohibited))
    second = validate_candidates((prohibited, no_factor))

    assert first == second
    assert [row.reason_code for row in first.rejected] == [
        "ambiguous_factor",
        "equal_split_prohibited",
    ]


@pytest.mark.parametrize(
    ("operands", "expected_reason"),
    [
        ((operand("material_mass", 1.0, "kg", suffix=""),), "operand_id_missing"),
        (
            (
                operand("material_mass", 1.0, "kg", suffix="same"),
                operand("unused", 1.0, "kg", suffix="same"),
            ),
            "operand_id_duplicate",
        ),
        (
            (
                operand("material_mass", 1.0, "kg"),
                operand("unused", 1.0, "kg"),
            ),
            "operand_role_unsupported",
        ),
    ],
)
def test_complete_operand_identity_and_exact_formula_roles_are_required(
    operands, expected_reason
):
    # The helper's empty suffix falls back to the role, so construct that case
    # explicitly to verify a genuinely blank operand identity.
    if expected_reason == "operand_id_missing":
        operands = (replace(operands[0], operand_id=""),)
    assert_rejected(
        validate_material_candidate(material_candidate(operands=operands)),
        expected_reason,
    )


def test_derived_formula_preserves_each_explicit_unit_conversion():
    inputs = (
        operand(
            "material_area",
            10.0,
            "m2",
            source_id="ifc-qto:402",
            design_quantity_id="design_quantity:402",
        ),
        operand("thickness", 100.0, "mm", source_id="ifc-layer:8"),
        operand("density", 2400.0, "kg/m3", source_id="density-table:4"),
    )

    accepted = validate_material_candidate(
        material_candidate(formula_code="area_thickness_density", operands=inputs)
    ).accepted

    assert all(isinstance(step, OperandConversion) for step in accepted.conversion_steps)
    by_role = {step.role: step for step in accepted.conversion_steps}
    assert by_role["material_area"].normalized_value == pytest.approx(10.0)
    assert by_role["material_area"].normalized_unit == "m2"
    assert by_role["thickness"].normalized_value == pytest.approx(0.1)
    assert by_role["thickness"].normalized_unit == "m"
    assert by_role["density"].normalized_value == pytest.approx(2400.0)
    assert by_role["density"].normalized_unit == "kg/m3"
    assert by_role["calculated_quantity"].normalized_value == pytest.approx(2400.0)
    assert by_role["calculated_quantity"].normalized_unit == "kg"
    assert [step.source_id for step in accepted.conversion_steps[:3]] == [
        "ifc-qto:402",
        "ifc-layer:8",
        "density-table:4",
    ]


def test_nonfinite_emission_result_is_rejected():
    result = validate_material_candidate(
        material_candidate(
            factor=factor(value=1e308),
            operands=(operand("material_mass", 1e308, "kg"),),
        )
    )
    assert_rejected(result, "emission_value_invalid")


def test_noncanonical_factor_denominators_use_coherent_normalized_pairs():
    material = validate_material_candidate(
        material_candidate(
            factor=factor(value=2.0, denominator="g"),
            operands=(operand("material_mass", 1.0, "kg"),),
        )
    ).accepted
    assert material.quantity_value == pytest.approx(1.0)
    assert material.quantity_unit == "kg"
    assert material.factor_value == pytest.approx(2000.0)
    assert material.emission_value == pytest.approx(2000.0)

    energy = validate_energy_candidate(
        energy_candidate(
            factor=factor(value=500.0, denominator="MWh"),
            operands=(operand("energy_quantity", 1000.0, "kWh"),),
        )
    ).accepted
    assert energy.quantity_value == pytest.approx(1000.0)
    assert energy.quantity_unit == "kWh"
    assert energy.factor_value == pytest.approx(0.5)
    assert energy.emission_value == pytest.approx(500.0)


def test_mutating_original_factor_after_candidate_creation_cannot_change_result():
    mutable_factor = replace(
        factor(value=0.5, denominator="kWh"),
        geography="Hong Kong",
        year="2024",
    )
    candidate = energy_candidate(factor=mutable_factor)
    mutable_factor.factor_value = 999.0
    mutable_factor.normalized_factor_value = 999.0
    mutable_factor.geography = "changed"
    mutable_factor.year = "2099"

    accepted = validate_energy_candidate(candidate).accepted
    assert accepted.factor.factor_value == pytest.approx(0.5)
    assert accepted.factor.normalized_factor_value == pytest.approx(0.5)
    assert accepted.factor.geography == "Hong Kong"
    assert accepted.factor.year == "2024"
    assert accepted.emission_value == pytest.approx(5.0)


def test_source_record_identity_is_namespaced_by_evidence_source():
    file_a = energy_candidate(
        record_id="local-row-1",
        source_record_id="local-row-1",
        evidence_source_id="sha256:file-a",
    )
    file_b = energy_candidate(
        record_id="local-row-1",
        source_record_id="local-row-1",
        evidence_source_id="sha256:file-b",
    )

    independent = validate_candidates((file_b, file_a))

    assert len(independent.accepted) == 2
    assert not independent.rejected
    assert len({row.source_identity for row in independent.accepted}) == 2
    assert len({row.consumption_id for row in independent.accepted}) == 2

    duplicate_a = replace(file_a, record_id="candidate-a")
    duplicate_b = replace(file_a, record_id="candidate-b")
    duplicated = validate_candidates((duplicate_b, duplicate_a))
    assert not duplicated.accepted
    assert [row.reason_code for row in duplicated.rejected] == [
        "duplicate_source_record_id",
        "duplicate_source_record_id",
    ]


def test_validation_delegate_never_touches_any_graph_member():
    class FailOnGraphAccess:
        def __getattribute__(self, name):
            if name in {"__class__", "__dict__"}:
                return object.__getattribute__(self, name)
            raise AssertionError(f"graph member accessed: {name}")

    builder = MultiGranularCarbonKGBuilder.__new__(MultiGranularCarbonKGBuilder)
    builder.graph = FailOnGraphAccess()
    result = builder.validate_consumption_candidates((material_candidate(),))
    assert len(result.accepted) == 1
