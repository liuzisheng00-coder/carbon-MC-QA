import json
from pathlib import Path

from dm2c_e4_freeze_benchmark import freeze_benchmark, validate_final_benchmark


SOURCE = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_211808_v6/"
    "e4_balanced_benchmark_150_human_revised_v6.jsonl"
)
SOURCE_SUMMARY = Path(
    "outputs/research_experiments/e4_benchmark/"
    "human_revised_20260710_211808_v6/"
    "e4_round4_revision_round5_summary.json"
)


def test_final_v6_passes_freeze_validation():
    validation = validate_final_benchmark(SOURCE)

    assert validation["validation_passed"] is True
    assert validation["case_count"] == 150
    assert validation["unique_case_ids"] == 150
    assert validation["unique_questions"] == 150
    assert validation["operation_semantic_violation_count"] == 0
    assert validation["hidden_filter_leak_count"] == 0
    assert validation["status_counts"] == {
        "executable": 90,
        "incomplete_path": 14,
        "empty_result": 16,
        "unresolved_target": 15,
        "clarification_required": 15,
    }


def test_freeze_writes_paper_ready_latest_pointer(tmp_path):
    summary = freeze_benchmark(
        SOURCE,
        SOURCE_SUMMARY,
        tmp_path,
        approval="user_message_all_three_pass",
    )

    latest = json.loads(
        (tmp_path / "latest_e4_benchmark_summary.json").read_text(encoding="utf-8")
    )
    frozen_jsonl = Path(summary["outputs"]["balanced_jsonl"])

    assert summary["paper_ready_benchmark"] is True
    assert summary["human_review"]["status"] == "passed_all"
    assert summary["human_review"]["final_confirmation"] == (
        "user_message_all_three_pass"
    )
    assert latest == summary
    assert frozen_jsonl.exists()
    assert len(frozen_jsonl.read_text(encoding="utf-8").splitlines()) == 150


def test_freeze_records_v7_targeted_review_metadata(tmp_path):
    summary = freeze_benchmark(
        SOURCE,
        SOURCE_SUMMARY,
        tmp_path,
        approval="user_workbook_all_21_issue_rows_pass",
        version_label="v7",
        reviewed_case_count=19,
        review_decision_count=21,
        review_rounds_completed=6,
    )

    assert summary["run_id"].endswith("_v7_human_pass")
    assert summary["human_review"]["final_reviewed_case_count"] == 19
    assert summary["human_review"]["final_pass_count"] == 21
    assert summary["human_review"]["review_rounds_completed"] == 6
    assert summary["outputs"]["balanced_jsonl"].endswith(
        "e4_balanced_benchmark_150_human_reviewed_v7.jsonl"
    )
