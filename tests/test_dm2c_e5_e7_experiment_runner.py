import json
import tempfile
import unittest
from pathlib import Path

from dm2c_e5_e7_experiment_runner import (
    BenchmarkCase,
    DEFAULT_VARIANTS,
    RuleBasedBenchmarkResponder,
    detect_unsupported_numeric_values,
    evaluate_benchmark_response,
    generate_e7_injection_cases,
    load_benchmark_cases,
    load_benchmark_metadata,
    summarize_results,
)


class E5E7ExperimentRunnerTests(unittest.TestCase):
    def test_load_benchmark_cases_from_jsonl_preserves_expected_fields(self):
        rows = [
            {
                "case_id": "c1",
                "question": "Project total?",
                "expected": {
                    "perspective": "product",
                    "operation": "value",
                    "status": "executable",
                    "summary": {"knownTotalCarbon_kgCO2e": 12.34},
                    "numeric_tolerance": 0.001,
                },
                "tags": ["e4"],
            }
        ]

        cases = load_benchmark_cases(rows)

        self.assertEqual(len(cases), 1)
        self.assertIsInstance(cases[0], BenchmarkCase)
        self.assertEqual(cases[0].case_id, "c1")
        self.assertEqual(cases[0].expected["status"], "executable")
        self.assertEqual(cases[0].expected["summary"]["knownTotalCarbon_kgCO2e"], 12.34)

    def test_load_benchmark_cases_accepts_latest_summary_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jsonl_path = tmp_path / "e4_balanced_benchmark_150.jsonl"
            jsonl_path.write_text(
                json.dumps(
                    {
                        "case_id": "case_from_latest",
                        "question": "What is the project's known carbon total?",
                        "expected": {"status": "executable"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            summary_path = tmp_path / "latest_e4_benchmark_summary.json"
            summary_path.write_text(
                json.dumps({"outputs": {"balanced_jsonl": str(jsonl_path)}}),
                encoding="utf-8",
            )

            cases = load_benchmark_cases(summary_path)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].case_id, "case_from_latest")

    def test_load_benchmark_metadata_discovers_frozen_jsonl_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jsonl_path = tmp_path / "e4_balanced_benchmark_150_human_reviewed_v9.jsonl"
            jsonl_path.write_text("{}\n", encoding="utf-8")
            summary_path = tmp_path / "e4_benchmark_summary_human_reviewed_v9.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "run_id": "frozen-v9",
                        "paper_ready_benchmark": True,
                        "human_review": {
                            "status": "passed_all",
                            "decision": "freeze_current_v9_questions",
                        },
                        "outputs": {"balanced_jsonl": str(jsonl_path)},
                    }
                ),
                encoding="utf-8",
            )

            metadata = load_benchmark_metadata(jsonl_path)

        self.assertTrue(metadata["paper_ready_benchmark"])
        self.assertEqual(metadata["human_review_status"], "passed_all")
        self.assertEqual(metadata["source_run_id"], "frozen-v9")
        self.assertEqual(metadata["source_path"], str(summary_path))

    def test_default_variants_do_not_include_rag_baseline_when_method_has_no_rag(self):
        self.assertNotIn("rag_only_proxy", DEFAULT_VARIANTS)
        self.assertIn("llm_only_proxy", DEFAULT_VARIANTS)
        self.assertIn("unconstrained_kg_llm_proxy", DEFAULT_VARIANTS)

    def test_detect_unsupported_numeric_values_ignores_expected_numbers_and_ids(self):
        expected = {
            "summary": {"knownTotalCarbon_kgCO2e": 4722.938, "components": 604},
            "answer_contains": ["E4_DOES_NOT_EXIST_094"],
        }
        answer = "Known total is 4722.938 kgCO2e for 604 components; unsupported estimate is 999.9."

        unsupported = detect_unsupported_numeric_values(answer, expected)

        self.assertEqual(unsupported, ["999.9"])

    def test_evaluator_accepts_numbers_explicitly_supported_by_evidence_object(self):
        case = BenchmarkCase(
            case_id="supported-number",
            question="Give the grouped values",
            expected={"status": "executable", "summary": {"total": 10.0}},
        )
        response = {
            "status": "executable",
            "summary": {"total": 10.0},
            "answer": "The total is 10.0 and the supported group value is 4.0.",
            "evidence_ids": [],
            "supported_numeric_values": [10.0, 4.0],
        }

        result = evaluate_benchmark_response(case, response, "full_real")

        self.assertFalse(result["scores"]["unsupported_answer"])

    def test_evaluator_accepts_alternative_evidence_id_in_supplied_evidence_object(self):
        case = BenchmarkCase(
            case_id="supported-evidence",
            question="Trace the blocked path",
            expected={
                "status": "incomplete_path",
                "evidence_ids": ["carbon_emission:expected-chain"],
            },
        )
        response = {
            "status": "incomplete_path",
            "summary": {"blocked_status": "blocked_factor_unresolved"},
            "answer": "The path is blocked_factor_unresolved.",
            "evidence_ids": ["carbon_emission:alternative-valid-chain"],
            "supported_evidence_ids": ["carbon_emission:alternative-valid-chain"],
        }

        result = evaluate_benchmark_response(case, response, "full_real")

        self.assertTrue(result["scores"]["provenance_supported"])
        self.assertFalse(result["scores"]["unsupported_answer"])

    def test_evaluator_still_rejects_values_outside_expected_and_evidence_object(self):
        case = BenchmarkCase(
            case_id="unsupported-number",
            question="Give the total",
            expected={"status": "executable", "summary": {"total": 10.0}},
        )
        response = {
            "status": "executable",
            "summary": {"total": 10.0},
            "answer": "The total is 10.0 but an unsupported estimate is 999.0.",
            "evidence_ids": [],
            "supported_numeric_values": [10.0, 4.0],
        }

        result = evaluate_benchmark_response(case, response, "full_real")

        self.assertTrue(result["scores"]["unsupported_answer"])
        self.assertEqual(result["unsupported_numeric_values"], ["999.0"])

    def test_evaluator_does_not_parse_numbers_inside_supported_evidence_ids_as_values(self):
        case = BenchmarkCase(
            case_id="evidence-id-numbers",
            question="Trace the evidence",
            expected={"status": "executable", "summary": {"value": 121.2472}},
        )
        evidence_ids = [
            "carbon_emission:energy:7",
            "factor:energy:China_National_Grid_HKEX_ref_0.6101",
        ]
        response = {
            "status": "executable",
            "summary": {"value": 121.2472},
            "answer": (
                "121.2472 kgCO2e from carbon_emission:energy:7 and "
                "factor:energy:China_National_Grid_HKEX_ref_0.6101."
            ),
            "evidence_ids": evidence_ids,
            "supported_numeric_values": [121.2472],
            "supported_evidence_ids": evidence_ids,
        }

        result = evaluate_benchmark_response(case, response, "full_real")

        self.assertFalse(result["scores"]["unsupported_answer"])
        self.assertEqual(result["unsupported_numeric_values"], [])

    def test_status_blocking_ablation_hard_answers_incomplete_paths(self):
        case = BenchmarkCase(
            case_id="blocked",
            question="Trace blocked path",
            expected={
                "perspective": "traceability",
                "operation": "trace",
                "status": "incomplete_path",
                "summary": {"blocked_status": "blocked_factor_unresolved"},
                "answer_contains": ["blocked_factor_unresolved"],
            },
            tags=["blocked"],
        )

        response = RuleBasedBenchmarkResponder("no_status_blocking").answer(case)
        result = evaluate_benchmark_response(case, response, "no_status_blocking")

        self.assertFalse(result["scores"]["status_match"])
        self.assertGreater(result["unsupported_numeric_count"], 0)

    def test_generate_e7_injection_cases_covers_required_categories(self):
        cases = generate_e7_injection_cases(per_category=3)

        categories = {case.expected["injection_type"] for case in cases}
        self.assertEqual(
            {
                "delete_quantity",
                "delete_factor",
                "delete_process_record",
                "unit_mismatch",
                "delete_allocation_basis",
                "synonym_rewrite",
                "ambiguous_target_attack",
            },
            categories,
        )
        self.assertEqual(len(cases), 21)
        self.assertTrue(any(case.expected["status"] == "clarification_required" for case in cases))
        self.assertTrue(any(case.expected["status"] == "incomplete_path" for case in cases))

    def test_summarize_results_reports_core_e5_metrics(self):
        case = BenchmarkCase(
            case_id="c1",
            question="Total?",
            expected={
                "perspective": "product",
                "operation": "value",
                "status": "executable",
                "summary": {"knownTotalCarbon_kgCO2e": 1.0},
            },
            tags=[],
        )
        response = RuleBasedBenchmarkResponder("full").answer(case)
        result = evaluate_benchmark_response(case, response, "full")

        summary = summarize_results([result])

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(summary["numeric_accuracy"], 1.0)
        self.assertEqual(summary["status_accuracy"], 1.0)
        self.assertEqual(summary["unsupported_answer_rate"], 0.0)

    def test_full_responder_includes_required_answer_terms(self):
        case = BenchmarkCase(
            case_id="c_answer_contains",
            question="Which target?",
            expected={
                "perspective": "product",
                "operation": "rank",
                "status": "executable",
                "summary": {"top_component_knownTotalCarbon_kgCO2e": 2.5},
                "answer_contains": ["component:abc"],
            },
            tags=[],
        )

        response = RuleBasedBenchmarkResponder("full").answer(case)
        result = evaluate_benchmark_response(case, response, "full")

        self.assertTrue(result["scores"]["answer_contains"])
        self.assertTrue(result["scores"]["passed"])


if __name__ == "__main__":
    unittest.main()
