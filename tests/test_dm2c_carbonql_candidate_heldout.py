from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import (
    components_for_ifc_class,
    dimension_ids,
    load_canonical_v2_context,
    lookup_component,
)
from dm2c_carbonql import GraphSchema
from dm2c_carbonql_candidate_heldout import (
    DEFAULT_SPEC_PATH,
    build_case,
    ground_case,
    load_question_spec,
)

CARRIER_NAMED = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)


@pytest.fixture(scope="module")
def specs():
    rows, _ = load_question_spec(DEFAULT_SPEC_PATH)
    return rows


@pytest.fixture(scope="module")
def context():
    return load_canonical_v2_context(CARRIER_NAMED, allow_synthetic=True)


def test_spec_keeps_the_24_question_12_unit_contract(specs) -> None:
    assert len(specs) == 24
    assert len({spec.case_id for spec in specs}) == 24
    assert len({spec.question for spec in specs}) == 24
    assert Counter(spec.operation for spec in specs) == {
        "aggregate": 6,
        "compare": 6,
        "rank": 6,
        "trace": 6,
    }
    assert Counter(spec.grounding_mode for spec in specs) == {
        "project": 8,
        "selected": 8,
        "natural_class": 8,
    }
    units = Counter(spec.decision_unit for spec in specs)
    assert len(units) == 12
    assert set(units.values()) == {2}


def test_spec_carries_no_release_bound_node_ids(specs) -> None:
    """A prototype must name a selector, never a graph node of one release."""
    for spec in specs:
        payload = repr(spec.prototype_program.to_dict())
        assert "BuildingComponent:" not in payload
        assert "IfcMaterial:" not in payload


def test_every_question_compiles_three_source_candidates(specs, context) -> None:
    schema = GraphSchema.from_context(context)
    for spec in specs:
        case = build_case(spec, context, schema)
        candidates = case.candidate_set.candidates
        assert [candidate.candidate_id for candidate in candidates] == [
            "material",
            "process",
            "unified",
        ]
        assert len({candidate.program_sha256 for candidate in candidates}) == 3


def test_clicked_pool_and_class_grounding_resolve_on_the_release(specs, context) -> None:
    available = set(dimension_ids(context, "component"))
    by_global = {}
    for entity_id in available:
        props = lookup_component(context, entity_id)
        props = props.get("props") or props
        global_id = str(props.get("globalId") or "")
        if global_id:
            by_global[global_id] = entity_id

    for spec in specs:
        for global_id in (*spec.selected_component_ids, *spec.base_target_pool):
            assert global_id in by_global, global_id

    beams = ground_case(
        next(spec for spec in specs if spec.grounding_mode == "natural_class"), context
    )
    assert len(beams) == len(components_for_ifc_class(context, "IfcBeam"))
    assert all(global_id in by_global for global_id in beams)


def test_non_class_questions_ground_nothing(specs, context) -> None:
    for spec in specs:
        if spec.grounding_mode == "natural_class":
            assert ground_case(spec, context)
        else:
            assert ground_case(spec, context) == ()
