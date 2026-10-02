from dm2c_qa_result_aggregator import rescore_case_results, summarize_case_results


def _row(variant, status, *, status_match, unsupported, numeric_match=True):
    return {
        "variant": variant,
        "expected": {"status": status},
        "scores": {
            "status_match": status_match,
            "numeric_match": numeric_match,
            "slot_match": True,
            "answer_contains": True,
            "provenance_supported": not unsupported,
            "unsupported_answer": unsupported,
            "passed": status_match and not unsupported,
        },
        "hallucinated_evidence_ids": ["factor:fake"] if unsupported else [],
        "latency_ms": 10.0,
        "observed": {"transport_error": ""},
    }


def test_summary_reports_overall_and_expected_status_slices():
    rows = [
        _row("baseline", "executable", status_match=True, unsupported=False),
        _row("baseline", "incomplete_path", status_match=False, unsupported=True),
        _row("full", "executable", status_match=True, unsupported=False),
        _row("full", "incomplete_path", status_match=True, unsupported=False),
    ]

    report = summarize_case_results(rows)

    baseline = report["overall"]["baseline"]
    assert baseline["case_count"] == 2
    assert baseline["status_accuracy"] == 0.5
    assert baseline["unsupported_answer_rate"] == 0.5
    baseline_missing = next(
        row for row in report["by_expected_status"]
        if row["variant"] == "baseline" and row["expected_status"] == "incomplete_path"
    )
    assert baseline_missing["status_accuracy"] == 0.0
    assert baseline_missing["unsupported_answer_rate"] == 1.0


def test_rescoring_uses_current_provenance_rules_on_saved_observation():
    row = _row("baseline", "executable", status_match=True, unsupported=False)
    row.update(
        {
            "case_id": "c1",
            "question": "What is the total?",
            "expected": {
                "perspective": "product",
                "operation": "value",
                "status": "executable",
            },
        }
    )
    row["observed"].update(
        {
            "status": "executable",
            "perspective": "product",
            "operation": "value",
            "summary": {},
            "evidence_ids": ["factor:invented"],
            "answer": "factor:invented",
        }
    )

    rescored = rescore_case_results([row])

    assert rescored[0]["scores"]["unsupported_answer"] is True
    assert rescored[0]["hallucinated_evidence_ids"] == ["factor:invented"]
