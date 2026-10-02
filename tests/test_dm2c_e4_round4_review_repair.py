from pathlib import Path

from dm2c_e4_round4_review_repair import (
    ROUND4_REVISED_QUESTIONS,
    apply_round4_revision,
    build_round4_revised_benchmark,
)


SOURCE = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_205306_v5/"
    "e4_balanced_benchmark_150_human_revised_v5.jsonl"
)
REVIEW = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_205306_v5/"
    "human_review_round4_decisions.json"
)


def make_case(case_id):
    return {
        "case_id": case_id,
        "question": "Old question",
        "scope": "project",
        "selected_component_ids": [],
        "filters": {"input_mode": "search_query"},
        "expected": {
            "perspective": "traceability",
            "operation": "rank",
            "status": "unresolved_target",
            "summary": {},
        },
    }


def test_round4_revision_map_covers_all_3_revise_rows():
    assert len(ROUND4_REVISED_QUESTIONS) == 3


def test_questions_compare_roofs_by_material_without_spatial_or_internal_labels():
    questions = "\n".join(ROUND4_REVISED_QUESTIONS.values()).casefold()

    assert "steel" in questions
    assert "timber" in questions
    assert "concrete" in questions
    assert "roof assemblies" in questions
    assert "fire-rated" not in questions
    assert "acoustic" not in questions
    assert "loading bay" not in questions
    assert "stair core" not in questions
    assert "rc-x" not in questions


def test_traceability_rank_ranks_records_and_reports_chain_completeness():
    case_id = "e4_balanced_146_traceability_rank_unresolved_target"
    revised = apply_round4_revision(make_case(case_id), "Can the questioner know the location?")

    assert "Rank the material-carbon records" in revised["question"]
    assert "steel, timber, and concrete roof assemblies" in revised["question"]
    assert "report each record's evidence-chain completeness" in revised["question"]
    assert revised["filters"]["natural_search_phrase"] == (
        "steel, timber, and concrete roof assemblies"
    )
    assert revised["filters"]["reference_mode"] == "entity_reference"


def test_build_preserves_150_cases_status_quotas_and_semantics(tmp_path):
    summary = build_round4_revised_benchmark(SOURCE, REVIEW, tmp_path)

    assert summary["case_count"] == 150
    assert summary["changed_question_count"] == 3
    assert summary["round4_pass_count"] == 3
    assert summary["round4_revise_count"] == 3
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
