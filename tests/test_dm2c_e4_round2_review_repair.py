from pathlib import Path

from dm2c_e4_round2_review_repair import (
    FORBIDDEN_USER_LABELS,
    ROUND2_REVISED_QUESTIONS,
    apply_round2_revision,
    build_round2_revised_benchmark,
)


SOURCE = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_183840_v3/"
    "e4_balanced_benchmark_150_human_revised_v3.jsonl"
)
REVIEW = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_183840_v3/"
    "human_review_round2_decisions.json"
)


def make_case(case_id, question="Old question", selected=None, status="unresolved_target"):
    return {
        "case_id": case_id,
        "question": question,
        "scope": "selected_component" if selected else "project",
        "selected_component_ids": list(selected or []),
        "filters": {"input_mode": "search_query"},
        "expected": {
            "perspective": "product",
            "operation": "compare",
            "status": status,
            "summary": {},
        },
    }


def test_round2_revision_map_covers_all_12_revise_rows():
    assert len(ROUND2_REVISED_QUESTIONS) == 12


def test_revised_questions_do_not_require_internal_names_or_chat_state():
    questions = "\n".join(ROUND2_REVISED_QUESTIONS.values()).casefold()
    for label in FORBIDDEN_USER_LABELS:
        assert label.casefold() not in questions


def test_unresolved_reference_uses_a_natural_spatial_description():
    case_id = "e4_balanced_146_traceability_rank_unresolved_target"
    revised = apply_round2_revision(make_case(case_id), "RC-X1 is not user-facing")

    assert "above the loading bay" in revised["question"]
    assert revised["filters"]["reference_mode"] == "entity_reference"
    assert revised["filters"]["natural_search_phrase"] == "roof cassette above the loading bay"


def test_clarification_reference_uses_natural_deixis_without_chat_wording():
    case_id = "e4_balanced_110_process_compare_clarification_required"
    case = make_case(case_id, status="clarification_required")
    revised = apply_round2_revision(case, "Chat wording was inaccurate")

    assert revised["question"] == "Compare electricity and diesel carbon for that factory-energy record."
    assert revised["filters"]["ambiguous_target"] == "that factory-energy record"
    assert "chat" not in revised["question"].casefold()


def test_multi_selection_cases_keep_two_ids_while_case_134_stays_single_selection():
    compare = make_case(
        "e4_balanced_120_process_compare_incomplete_path",
        selected=["gid-1", "gid-2"],
        status="incomplete_path",
    )
    rank = make_case(
        "e4_balanced_134_process_rank_incomplete_path",
        selected=["gid-3"],
        status="incomplete_path",
    )

    revised_compare = apply_round2_revision(compare, "Can the UI select two components?")
    revised_rank = apply_round2_revision(rank, "Does this require multiple selection?")

    assert revised_compare["selected_component_ids"] == ["gid-1", "gid-2"]
    assert revised_compare["filters"]["selected_count"] == 2
    assert revised_rank["selected_component_ids"] == ["gid-3"]
    assert revised_rank["filters"]["selected_count"] == 1
    assert "selected BIM component" in revised_rank["question"]


def test_build_preserves_150_cases_and_status_quotas(tmp_path):
    summary = build_round2_revised_benchmark(SOURCE, REVIEW, tmp_path)

    assert summary["case_count"] == 150
    assert summary["changed_question_count"] == 12
    assert summary["round2_pass_count"] == 13
    assert summary["round2_revise_count"] == 12
    assert summary["status_counts"] == {
        "executable": 90,
        "incomplete_path": 14,
        "empty_result": 16,
        "unresolved_target": 15,
        "clarification_required": 15,
    }
    assert summary["operation_semantic_violation_count"] == 0
    assert summary["duplicate_question_count"] == 0
