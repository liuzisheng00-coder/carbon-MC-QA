import copy
import json
import unittest
from pathlib import Path

from dm2c_e4_question_reviser import (
    has_explicit_perspective_prompt,
    has_user_facing_internal_id,
    revise_case,
    revise_cases,
)


class E4QuestionReviserTests(unittest.TestCase):
    def test_revise_case_removes_explicit_perspective_prompt_and_keeps_expected(self):
        case = {
            "case_id": "case_001",
            "question": "From the product perspective, report the project known carbon total.",
            "question_template": "From the product perspective, report the project known carbon total.",
            "expected": {
                "perspective": "product",
                "operation": "value",
                "status": "executable",
                "summary": {"knownTotalCarbon_kgCO2e": 4722.938},
            },
            "selected_component_ids": [],
            "scope": "project",
            "tags": ["e4_qa_benchmark", "product", "value", "executable"],
        }
        original_expected = copy.deepcopy(case["expected"])

        revised = revise_case(case)

        self.assertEqual(revised["expected"], original_expected)
        self.assertEqual(revised["question_revision"]["original_question"], case["question"])
        self.assertFalse(has_explicit_perspective_prompt(revised["question"]))
        self.assertIn("project", revised["question"].lower())

    def test_revise_case_hides_unresolved_target_internal_ids_from_question(self):
        case = {
            "case_id": "case_002",
            "question": "From the traceability perspective, compare carbon data for GlobalId E4_DOES_NOT_EXIST_140.",
            "question_template": "From the traceability perspective, compare carbon data for GlobalId E4_DOES_NOT_EXIST_140.",
            "expected": {
                "perspective": "traceability",
                "operation": "compare",
                "status": "unresolved_target",
                "summary": {"target_id": "E4_DOES_NOT_EXIST_140"},
                "answer_contains": ["E4_DOES_NOT_EXIST_140"],
            },
            "selected_component_ids": [],
            "scope": "project",
            "tags": ["e4_qa_benchmark", "traceability", "compare", "unresolved_target"],
        }

        revised = revise_case(case)

        self.assertFalse(has_user_facing_internal_id(revised["question"]))
        self.assertIn("E4_DOES_NOT_EXIST_140", revised["expected"]["summary"]["target_id"])
        self.assertEqual(revised["filters"]["target_id"], "E4_DOES_NOT_EXIST_140")
        self.assertEqual(revised["filters"]["input_mode"], "search_query")
        self.assertIn("searched", revised["question"].lower())

    def test_revise_case_turns_clarification_instruction_into_ambiguous_user_question(self):
        case = {
            "case_id": "case_003",
            "question": "From a material perspective, track carbon for steel-like material; if needed, request clarification.",
            "question_template": "From the material perspective, trace carbon for steel-like material; ask for clarification if needed.",
            "expected": {
                "perspective": "material",
                "operation": "trace",
                "status": "clarification_required",
                "summary": {"ambiguous_target": "steel-like material"},
                "answer_contains": ["clarification"],
            },
            "selected_component_ids": [],
            "scope": "project",
            "tags": ["e4_qa_benchmark", "material", "trace", "clarification_required"],
        }

        revised = revise_case(case)

        lowered = revised["question"].lower()
        self.assertNotIn("clarification", lowered)
        self.assertNotIn("if needed", lowered)
        self.assertEqual(revised["expected"]["status"], "clarification_required")
        self.assertIn("steel", lowered)

    def test_revise_cases_deduplicates_review_sample_questions_without_changing_expected(self):
        source = Path(
            "outputs/research_experiments/e4_benchmark/run_20260707_170206/e4_balanced_benchmark_150.jsonl"
        )
        cases = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        original_expected_by_id = {
            case["case_id"]: copy.deepcopy(case["expected"]) for case in cases
        }

        revised = revise_cases(cases)
        review_cases = [
            case
            for case in revised
            if int(case["case_id"].split("_")[2]) <= 30
            or int(case["case_id"].split("_")[2]) >= 91
        ]
        questions = [case["question"] for case in review_cases]

        self.assertEqual(len(questions), len(set(questions)))
        for case in revised:
            self.assertEqual(case["expected"], original_expected_by_id[case["case_id"]])


if __name__ == "__main__":
    unittest.main()
