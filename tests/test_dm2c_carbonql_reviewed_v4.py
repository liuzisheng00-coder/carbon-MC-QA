from __future__ import annotations

from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql_reviewed_v4 import (
    DEFAULT_REVISION_PATH,
    DEFAULT_REWRITE_PATH,
    audit_reviewed_v4_cases,
    build_reviewed_v4_cases,
    load_review_revisions,
)


CARRIER_NAMED = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)
MATERIAL_ONLY = Path("outputs/research_experiments/m3_material_only_v2b_20260723")


@pytest.fixture(scope="module")
def context():
    return load_canonical_v2_context(CARRIER_NAMED, allow_synthetic=True)


@pytest.fixture(scope="module")
def reviewed_rows_and_audit(context):
    rows = build_reviewed_v4_cases(context, DEFAULT_REWRITE_PATH, DEFAULT_REVISION_PATH)
    return rows, audit_reviewed_v4_cases(rows, context)


def test_review_manifest_keeps_the_16_human_revision_contract() -> None:
    revisions = load_review_revisions(DEFAULT_REVISION_PATH)

    assert len(revisions) == 16
    assert {
        revision.revision_kind for revision in revisions
    } == {
        "automatic_join_correction",
        "implicit_grouping_resolution",
        "implicit_view_resolution",
    }


def test_reviewed_v4_cases_preserve_48_case_structure_on_release(reviewed_rows_and_audit) -> None:
    _, audit = reviewed_rows_and_audit

    assert audit["release_id"] == "m2_typed_completed_20260728_partial_allocation_final"
    assert audit["case_count"] == 48
    assert audit["language_tier_counts"] == {
        "L1_explicit": 16,
        "L2_natural": 16,
        "L3_deictic": 16,
    }
    assert audit["category_counts"] == {
        "cross_view": 40,
        "partial_or_ambiguous": 8,
    }
    assert audit["view_combination_counts"] == {
        "material+process": 12,
        "product+material": 12,
        "product+material+process": 12,
        "product+process": 12,
    }
    assert audit["operation_counts"] == {
        "aggregate": 28,
        "compare": 8,
        "rank": 8,
        "trace": 4,
    }
    assert audit["query_status_counts"] == {
        "clarification_required": 4,
        "executable": 40,
        "unresolved_target": 4,
    }
    assert audit["oracle_mismatch_count"] == 0
    assert audit["view_signature_mismatch_count"] == 0
    assert audit["raw_global_id_exposure_count"] == 0
    assert audit["forbidden_term_violation_count"] == 0
    assert audit["implicit_view_resolution_mismatch_count"] == 0
    assert audit["automatic_join_violation_count"] == 0


def test_reviewed_v4_synthetic_policy_and_material_crosscheck(
    reviewed_rows_and_audit
) -> None:
    rows, audit = reviewed_rows_and_audit
    material_rows = [row for row in rows if row.view_combination == "product+material"]
    process_rows = [row for row in rows if row.view_combination != "product+material"]

    assert len(material_rows) == 12
    assert len(process_rows) == 36
    assert audit["synthetic_energy_case_count"] == 36
    assert audit["synthetic_product_material_case_count"] == 0
    assert audit["fail_closed_violation_count"] == 0
    assert audit["product_material_material_total_kgCO2e"] == pytest.approx(
        9461.215008532761
    )
