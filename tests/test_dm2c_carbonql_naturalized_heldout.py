from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql_naturalized_heldout import (
    _DEFAULT_KG_DIR,
    _DEFAULT_REWRITE_PATH,
    build_stratified_cases,
    load_rewrite_specs,
)


CARRIER_NAMED = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)


@pytest.fixture(scope="module")
def context():
    return load_canonical_v2_context(CARRIER_NAMED, allow_synthetic=True)


def test_default_kg_matches_release_and_requires_explicit_synthetic_loading() -> None:
    assert _DEFAULT_KG_DIR == CARRIER_NAMED
    with pytest.raises(Exception, match="syntheticFactoryInputsUsed"):
        load_canonical_v2_context(_DEFAULT_KG_DIR)


def test_rewrite_source_keeps_the_48_case_language_contract() -> None:
    specs = load_rewrite_specs(_DEFAULT_REWRITE_PATH)

    assert len(specs) == 48
    assert Counter(spec.language_tier for spec in specs) == {
        "L1_explicit": 16,
        "L2_natural": 16,
        "L3_deictic": 16,
    }
    assert {
        spec.base_case_id for spec in specs if spec.selector_override == "clicked"
    } == {"e4c_3_01", "e4c_3_04", "e4c_3_08"}


def test_naturalized_cases_run_on_canonical_v2_release(context) -> None:
    rows = build_stratified_cases(context, load_rewrite_specs(_DEFAULT_REWRITE_PATH))

    assert str(context.manifest["releaseId"]) == "m2_typed_completed_20260728_partial_allocation_final"
    assert len(rows) == 48
    assert Counter(row.language_tier for row in rows) == {
        "L1_explicit": 16,
        "L2_natural": 16,
        "L3_deictic": 16,
    }
    assert Counter(row.view_combination for row in rows) == {
        "material+process": 12,
        "product+material": 12,
        "product+material+process": 12,
        "product+process": 12,
    }
    assert Counter(row.case.category for row in rows) == {
        "cross_view": 32,
        "partial_or_ambiguous": 16,
    }
