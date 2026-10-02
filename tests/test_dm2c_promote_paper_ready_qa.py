import json
from pathlib import Path

from dm2c_promote_paper_ready_qa import promote_qa_results


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_promotes_hash_equivalent_complete_results(tmp_path):
    frozen = tmp_path / "frozen.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    _write(frozen, '{"case_id":"c1"}\n')
    _write(candidate, frozen.read_text(encoding="utf-8"))
    e4_summary = tmp_path / "e4.json"
    e4_summary.write_text(
        json.dumps(
            {
                "paper_ready_benchmark": True,
                "outputs": {"balanced_jsonl": str(frozen)},
            }
        ),
        encoding="utf-8",
    )
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "overall": {
                    "full_real": {
                        "case_count": 150,
                        "transport_error_count": 0,
                        "pass_rate": 1.0,
                    }
                },
                "outputs": {},
            }
        ),
        encoding="utf-8",
    )

    report = promote_qa_results(
        e4_summary_path=e4_summary,
        run_benchmark_path=candidate,
        comparison_report_path=comparison,
        output_dir=tmp_path / "out",
    )

    assert report["paper_ready"] is True
    assert report["benchmark_equivalence"]["hash_equal"] is True
    assert report["validation"]["variant_count"] == 1
    assert report["validation"]["transport_error_count"] == 0
    assert (tmp_path / "out" / "paper_ready_qa_report.json").exists()


def test_rejects_non_equivalent_benchmark(tmp_path):
    frozen = tmp_path / "frozen.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    _write(frozen, '{"case_id":"c1"}\n')
    _write(candidate, '{"case_id":"different"}\n')
    e4_summary = tmp_path / "e4.json"
    e4_summary.write_text(
        json.dumps(
            {
                "paper_ready_benchmark": True,
                "outputs": {"balanced_jsonl": str(frozen)},
            }
        ),
        encoding="utf-8",
    )
    comparison = tmp_path / "comparison.json"
    comparison.write_text(json.dumps({"overall": {}}), encoding="utf-8")

    try:
        promote_qa_results(
            e4_summary_path=e4_summary,
            run_benchmark_path=candidate,
            comparison_report_path=comparison,
            output_dir=tmp_path / "out",
        )
    except ValueError as exc:
        assert "hash" in str(exc).lower()
    else:
        raise AssertionError("Expected a hash mismatch to block promotion")
