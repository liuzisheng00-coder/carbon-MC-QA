from pathlib import Path

from dm2c_e4_round3_review_repair import (
    ROUND3_REVISED_QUESTIONS,
    apply_round3_revision,
    build_round3_revised_benchmark,
)


SOURCE = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_202027_v4/"
    "e4_balanced_benchmark_150_human_revised_v4.jsonl"
)
REVIEW = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_202027_v4/"
    "human_review_round3_decisions.json"
)


def make_case(case_id, selected=None, status="unresolved_target"):
    return {
        "case_id": case_id,
        "question": "Old question",
        "scope": "selected_component" if selected else "project",
        "selected_component_ids": list(selected or []),
        "filters": {"input_mode": "search_query"},
        "expected": {
            "perspective": "traceability",
            "operation": "rank",
            "status": status,
            "summary": {},
        },
    }


def test_round3_revision_map_covers_all_6_revise_rows():
    assert len(ROUND3_REVISED_QUESTIONS) == 6


def test_process_clarification_names_concrete_factory_operations():
    question = ROUND3_REVISED_QUESTIONS[
        "e4_balanced_124_process_rank_clarification_required"
    ]

    assert "cutting, welding, and coating" in question
    assert "that module" in question
    assert "kgCO2e" in question


def test_component_process_rank_asks_for_contributions_not_attribution_metadata():
    case_id = "e4_balanced_134_process_rank_incomplete_path"
    revised = apply_round3_revision(
        make_case(case_id, selected=["gid-1"], status="incomplete_path"),
        "Attribution wording was unclear",
    )

    assert revised["question"] == (
        "Rank the selected BIM component's cutting, welding, and coating carbon "
        "contributions by kgCO2e from highest to lowest."
    )
    assert revised["selected_component_ids"] == ["gid-1"]
    assert revised["filters"]["selected_count"] == 1


def test_two_roof_targets_are_distinguished_without_assuming_only_two_exist():
    product = ROUND3_REVISED_QUESTIONS[
        "e4_balanced_127_product_compare_unresolved_target"
    ]
    trace = ROUND3_REVISED_QUESTIONS[
        "e4_balanced_140_traceability_compare_unresolved_target"
    ]

    for question in (product, trace):
        assert "roof cassette over the loading bay" in question
        assert "roof cassette over the stair core" in question
        assert "larger and smaller" not in question


def test_traceability_rank_ranks_carbon_records_and_reports_chain_completeness():
    case_id = "e4_balanced_146_traceability_rank_unresolved_target"
    revised = apply_round3_revision(make_case(case_id), "Can evidence chains be ranked?")

    assert "Rank the material-carbon records" in revised["question"]
    assert "report each record's evidence-chain completeness" in revised["question"]
    assert revised["filters"]["natural_search_phrase"] == (
        "roof cassette over the loading bay"
    )


def test_build_preserves_150_cases_status_quotas_and_semantics(tmp_path):
    summary = build_round3_revised_benchmark(SOURCE, REVIEW, tmp_path)

    assert summary["case_count"] == 150
    assert summary["changed_question_count"] == 6
    assert summary["round3_pass_count"] == 6
    assert summary["round3_revise_count"] == 6
    assert summary["status_counts"] == {
        "executable": 90,
        "incomplete_path": 14,
        "empty_result": 16,
        "unresolved_target": 15,
        "clarification_required": 15,
    }
    assert summary["operation_semantic_violation_count"] == 0
    assert summary["hidden_filter_leak_count"] == 0
    assert summary["duplicate_question_count"] == 0
