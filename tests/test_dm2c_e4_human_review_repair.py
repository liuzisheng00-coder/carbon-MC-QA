from copy import deepcopy

from dm2c_e4_human_review_repair import (
    HUMAN_REVISED_QUESTIONS,
    PROCESS_GRANULARITY_CASES,
    apply_human_revision,
    sanitize_case_filters,
)


def make_case(case_id, perspective="product", operation="value", status="executable"):
    return {
        "case_id": case_id,
        "question": "Old question",
        "expected": {
            "perspective": perspective,
            "operation": operation,
            "status": status,
            "summary": {},
        },
        "selected_component_ids": [],
        "scope": "project",
        "filters": {},
    }


def test_human_revision_map_covers_all_25_revise_rows():
    assert len(HUMAN_REVISED_QUESTIONS) == 25


def test_compare_incomplete_revision_adds_two_selected_bim_items():
    case = make_case(
        "e4_balanced_091_traceability_compare_incomplete_path",
        perspective="traceability",
        operation="compare",
        status="incomplete_path",
    )
    case["selected_component_ids"] = ["3czbugqbT86PTcmnme1lZB"]
    case["filters"] = {"input_mode": "bim_selection", "selected_count": 1}

    revised = apply_human_revision(case, "Meaning was unclear")

    assert len(revised["selected_component_ids"]) == 2
    assert revised["filters"]["selected_count"] == 2
    assert "two selected BIM items" in revised["question"]


def test_process_incomplete_revision_uses_granularity_status_not_material_factor_status():
    case_id = "e4_balanced_102_process_aggregate_incomplete_path"
    assert case_id in PROCESS_GRANULARITY_CASES
    case = make_case(case_id, perspective="process", operation="aggregate", status="incomplete_path")
    case["selected_component_ids"] = ["3czbugqbT86PTcmnme1lZV"]
    case["expected"].update(
        {
            "summary": {"blocked_status": "blocked_factor_unresolved"},
            "evidence_ids": ["carbon_emission:material:old"],
            "answer_contains": ["blocked_factor_unresolved"],
        }
    )

    revised = apply_human_revision(case, "Meaning was unclear")

    assert revised["expected"]["summary"] == {
        "blocked_status": "process_granularity_not_supported"
    }
    assert revised["expected"]["answer_contains"] == [
        "process_granularity_not_supported"
    ]
    assert revised["expected"]["evidence_ids"] == [
        "component:3czbugqbT86PTcmnme1lZV"
    ]
    assert "component-level" in revised["question"]


def test_search_filters_remove_hidden_benchmark_target_ids():
    case = make_case(
        "e4_balanced_094_process_compare_unresolved_target",
        perspective="process",
        operation="compare",
        status="unresolved_target",
    )
    case["filters"] = {
        "input_mode": "search_query",
        "target_id": "E4_DOES_NOT_EXIST_094",
        "target_hints": ["E4_DOES_NOT_EXIST_094"],
        "natural_search_phrase": "the item I searched for",
    }

    revised = apply_human_revision(case, "Compare what with what")

    assert "target_id" not in revised["filters"]
    assert "target_hints" not in revised["filters"]
    assert revised["filters"]["reference_mode"] == "entity_reference"
    assert "NC-01" in revised["filters"]["natural_search_phrase"]
    assert "I searched for" not in revised["question"]


def test_sanitize_filters_preserves_safe_ui_context_only():
    case = make_case("unreviewed", status="empty_result")
    case["filters"] = {
        "input_mode": "search_query",
        "target_id": "E4-no-result-1",
        "target_hints": ["E4-no-result-1"],
        "natural_search_phrase": "roof cassette",
        "selected_count": 0,
    }
    original = deepcopy(case)

    sanitized = sanitize_case_filters(case)

    assert original["filters"]["target_id"] == "E4-no-result-1"
    assert sanitized["filters"] == {
        "input_mode": "search_query",
        "natural_search_phrase": "roof cassette",
        "selected_count": 0,
        "reference_mode": "category_filter",
    }


def test_rank_and_compare_revisions_name_metric_and_comparison_objects():
    rank = HUMAN_REVISED_QUESTIONS[
        "e4_balanced_105_traceability_rank_incomplete_path"
    ]
    compare = HUMAN_REVISED_QUESTIONS[
        "e4_balanced_141_material_compare_empty_result"
    ]

    assert "kgCO2e" in rank
    assert "highest to lowest" in rank
    assert "mineral-wool" in compare
    assert "vacuum-insulation" in compare
