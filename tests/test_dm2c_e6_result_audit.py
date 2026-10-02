import json
import tempfile
import unittest
from pathlib import Path

from dm2c_e6_result_audit import build_e6_audit


class E6ResultAuditTests(unittest.TestCase):
    def test_build_e6_audit_uses_sidecar_metadata_and_recomputes_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            benchmark = root / "e4_balanced_benchmark_150_human_reviewed_v9.jsonl"
            benchmark.write_text("{}\n{}\n", encoding="utf-8")
            sidecar = root / "e4_benchmark_summary_human_reviewed_v9.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "paper_ready_benchmark": True,
                        "human_review": {"status": "passed_all"},
                        "outputs": {"balanced_jsonl": str(benchmark)},
                    }
                ),
                encoding="utf-8",
            )
            cases_path = root / "e6_cases.jsonl"
            rows = [
                {
                    "variant": "no_gate",
                    "case_id": "c1",
                    "scores": {
                        "numeric_match": True,
                        "status_match": True,
                        "slot_match": True,
                        "answer_contains": True,
                        "provenance_supported": True,
                        "unsupported_answer": False,
                        "passed": True,
                    },
                    "hallucinated_evidence_ids": [],
                    "latency_ms": 10,
                    "observed": {"llm_errors": {"intent": "", "answer": ""}},
                },
                {
                    "variant": "no_gate",
                    "case_id": "c2",
                    "scores": {
                        "numeric_match": False,
                        "status_match": False,
                        "slot_match": True,
                        "answer_contains": False,
                        "provenance_supported": True,
                        "unsupported_answer": False,
                        "passed": False,
                    },
                    "hallucinated_evidence_ids": [],
                    "latency_ms": 20,
                    "observed": {"llm_errors": {"intent": "", "answer": ""}},
                },
            ]
            cases_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            e6_summary = root / "e6_summary.json"
            e6_summary.write_text(
                json.dumps(
                    {
                        "benchmark_path": str(benchmark),
                        "case_count": 2,
                        "case_jsonl": str(cases_path),
                        "variants": {"no_gate": {}},
                    }
                ),
                encoding="utf-8",
            )
            e5_report = root / "e5_report.json"
            e5_report.write_text(
                json.dumps(
                    {
                        "paper_ready_e5": True,
                        "case_count": 2,
                        "transport_error_count": 0,
                        "metrics": {
                            "numeric_accuracy": 1.0,
                            "status_accuracy": 1.0,
                            "pass_rate": 1.0,
                            "unsupported_answer_rate": 0.0,
                            "hallucinated_reference_rate": 0.0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_e6_audit(e6_summary, e5_report)

        self.assertTrue(report["paper_ready_e6"])
        self.assertEqual(report["variants"]["no_gate"]["metrics"]["status_accuracy"], 0.5)
        self.assertEqual(report["variants"]["no_gate"]["status_mismatch_case_ids"], ["c2"])
        self.assertEqual(report["variants"]["no_gate"]["delta_vs_full"]["status_accuracy"], -0.5)


if __name__ == "__main__":
    unittest.main()
