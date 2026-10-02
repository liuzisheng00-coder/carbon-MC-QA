from dm2c_carbonql import CarbonQLProgram
from dm2c_carbonql_semantic import compile_semantic_requirements, validate_semantic_coverage


def program(*steps: dict) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": list(steps)})


def test_semantic_compiler_emits_canonical_material_and_carrier_keys() -> None:
    requirement = compile_semantic_requirements(
        "Rank components by explicit material and energy carrier.", []
    )
    assert requirement.group_keys == ("component", "material", "carrier")
    assert requirement.sources == ("material", "process")


def test_process_context_cues_map_to_process_stage_and_resource() -> None:
    requirement = compile_semantic_requirements(
        "Break down factory energy by production stage, manufacturing activity, and production resource.", []
    )
    assert requirement.group_keys == ("stage", "process", "resource")
    assert requirement.sources == ("process",)


def test_semantic_validator_reports_canonical_missing_keys() -> None:
    incomplete = program(
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    )
    result = validate_semantic_coverage(
        "Compare components by explicit material and energy carrier.", [], incomplete
    )
    assert [(error.code, error.feature) for error in result.errors] == [
        ("source_mismatch", "process"),
        ("missing_group_key", "component"),
        ("missing_group_key", "material"),
        ("missing_group_key", "carrier"),
        ("missing_operation", "Compare"),
    ]


def test_source_uncertainty_keeps_canonical_material_group() -> None:
    requirement = compile_semantic_requirements(
        "Break down by explicit material without assuming which source.", []
    )
    assert requirement.sources == ()
    assert requirement.group_keys == ("material",)
    assert requirement.holes == ("emission_source",)


def test_legacy_factory_dimension_terms_are_not_inferred() -> None:
    requirement = compile_semantic_requirements(
        "Break down workstations and production batches.", []
    )
    assert requirement.group_keys == ()


def test_factor_fields_are_source_neutral_and_material_family_is_forbidden() -> None:
    factor = compile_semantic_requirements(
        "Group by factor keyword and factor source.", []
    )
    assert factor.group_keys == ("factor_keyword", "factor_source")
    assert factor.sources == ()
    family = compile_semantic_requirements(
        "Which material families contribute most?", []
    )
    assert family.group_keys == ()
    assert family.sources == ()


def test_source_kind_language_emits_source_neutral_grouping() -> None:
    requirement = compile_semantic_requirements("Group emissions by source kind.", [])
    assert requirement.group_keys == ("source_kind",)
    assert requirement.sources == ()
