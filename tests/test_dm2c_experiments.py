import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dm2c_api_server import Project, _run_project_experiment_sync
from dm2c_carbonql_service import CarbonQLService
from dm2c_experiment_backend import (
    ABLATION_VARIANTS,
    ExperimentCase,
    evaluate_calculation_validation,
    generate_default_experiment_cases,
    make_robustness_cases,
    run_experiment_suite,
)
from dm2c_qa_system import CarbonRequirement, DM2CQuestionAnsweringSystem, NormalizedCarbonRow
from tests.task10_v2_fixture import write_task10_release


PROJECT_TOTAL = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
}

MATERIAL_RANKING = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
        {"op": "Rank", "descending": True, "top_k": 5},
    ]
}


class QuestionRoutedCompiler:
    """Return one program per question shape, and record the prompts it sees."""

    def __init__(self):
        self.prompts = []

    def complete(self, messages, **_kwargs):
        payload = json.loads(messages[-1]["content"])
        self.prompts.append(payload)
        question = str(payload.get("question", "")).lower()
        program = MATERIAL_RANKING if "rank" in question else PROJECT_TOTAL
        return {"content": json.dumps(program)}


class ExperimentBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp())
        cls.release = write_task10_release(cls._tmp / "release")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def service(self, client=None):
        return CarbonQLService.from_release(
            self.release, client or QuestionRoutedCompiler(), variant="V2"
        )

    def test_default_cases_cover_the_four_answer_perspectives(self):
        payload = {"results": [{"component": "component:c1", "componentName": "Beam A"}]}

        cases = generate_default_experiment_cases(payload)

        self.assertEqual(
            {"product", "material", "process"},
            {case.expected.get("perspective") for case in cases},
        )
        self.assertEqual(
            {"aggregate", "rank", "breakdown", "trace"},
            {case.expected.get("operation") for case in cases},
        )
        self.assertTrue(all(case.question for case in cases))

    def test_case_evaluation_scores_slots_status_trace_and_numeric_summary(self):
        case = ExperimentCase(
            case_id="q1",
            question="What is the project total?",
            expected={
                "perspective": "product",
                "operation": "aggregate",
                "scope": "project",
                "status": "ok",
                "answer_contains": ["kgCO2e"],
                "summary": {"total_kgCO2e": 19.0},
                "numeric_tolerance": 0.001,
            },
        )
        report = run_experiment_suite(
            service=self.service(),
            assessment_payload={"results": []},
            cases=[case],
            variants=["V2"],
        )

        scores = report["variants"]["V2"]["cases"][0]["scores"]
        self.assertTrue(scores["slot_match"])
        self.assertTrue(scores["status_match"])
        self.assertTrue(scores["trace_complete"])
        self.assertTrue(scores["answer_contains"])
        self.assertTrue(scores["numeric_match"])
        self.assertTrue(scores["passed"])

    def test_experiment_suite_runs_cases_and_aggregates_metrics(self):
        cases = [
            ExperimentCase(
                case_id="q1",
                question="What is the project total?",
                expected={"perspective": "product", "operation": "aggregate", "status": "ok"},
            ),
            ExperimentCase(
                case_id="q2",
                question="Rank material carbon contributors",
                expected={"perspective": "material", "operation": "rank", "status": "ok"},
            ),
        ]

        report = run_experiment_suite(
            service=self.service(),
            assessment_payload={"results": []},
            cases=cases,
            variants=["V2"],
        )

        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["release_id"], report["release_id"])
        metrics = report["variants"]["V2"]["metrics"]
        self.assertEqual(metrics["slot_accuracy"], 1.0)
        self.assertEqual(metrics["status_match_rate"], 1.0)
        self.assertEqual(metrics["trace_complete_rate"], 1.0)
        self.assertGreaterEqual(metrics["mean_latency_ms"], 0.0)

    def test_ablation_runs_every_compiler_variant_and_labels_the_answer(self):
        case = ExperimentCase(
            case_id="q1",
            question="What is the project total?",
            expected={"status": "ok"},
        )

        report = run_experiment_suite(
            service=self.service(),
            assessment_payload={"results": []},
            cases=[case],
            variants=ABLATION_VARIANTS,
        )

        self.assertEqual(list(report["variants"]), list(ABLATION_VARIANTS))
        for variant in ABLATION_VARIANTS:
            observed = report["variants"][variant]["cases"][0]
            self.assertEqual(observed["variant"], variant)

    def test_only_the_grounded_variants_receive_the_graph_schema(self):
        client = QuestionRoutedCompiler()
        service = self.service(client)

        service.answer("What is the project total?", variant="V1")
        service.answer("What is the project total?", variant="V2")

        self.assertNotIn("graph_schema", client.prompts[0]["contract"])
        self.assertIn("graph_schema", client.prompts[1]["contract"])

    def test_robustness_cases_expect_a_named_refusal(self):
        cases = make_robustness_cases([])
        statuses = {case.expected.get("status") for case in cases}
        self.assertEqual({"unresolved_target", "partial"}, statuses)

    def test_project_experiment_helper_stores_report_on_project(self):
        project = Project(
            project_id="p1",
            created_at=1.0,
            upload_dir=None,
            output_dir=None,
        )
        project.carbonql = self.service()
        project.payload = {"results": []}

        report = _run_project_experiment_sync(
            project,
            {
                "experiment_type": "qa_benchmark",
                "variants": ["V2"],
                "cases": [
                    {
                        "id": "q1",
                        "question": "What is the project total?",
                        "expected": {
                            "perspective": "product",
                            "operation": "aggregate",
                            "status": "ok",
                        },
                    }
                ],
            },
        )

        self.assertIn(report["run_id"], project.experiment_runs)
        self.assertEqual(report["variants"]["V2"]["metrics"]["slot_accuracy"], 1.0)


class CarbonValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp())
        cls.service = CarbonQLService.from_release(
            write_task10_release(cls._tmp / "release"),
            QuestionRoutedCompiler(),
            variant="V2",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_project_measurement_comes_from_the_account_not_a_payload(self):
        measurement = self.service.measure()

        self.assertEqual(measurement.target_id, "__project__")
        self.assertAlmostEqual(measurement.C_mat_kgCO2e, 19.0)
        self.assertEqual(measurement.statuses["C_mat_kgCO2e"], "ok")

    def test_validation_reports_error_against_a_manual_reference(self):
        report = evaluate_calculation_validation(
            service=self.service,
            references=[
                {
                    "reference_id": "manual_project",
                    "level": "project",
                    "expected": {"C_mat_kgCO2e": 19.5},
                }
            ],
            tolerance_abs=0.6,
            tolerance_pct=0.05,
        )

        self.assertEqual(report["record_count"], 1)
        self.assertEqual(report["matched_count"], 1)
        self.assertAlmostEqual(report["metrics"]["material_mae_kgCO2e"], 0.5)
        self.assertEqual(report["metrics"]["within_tolerance_rate"], 1.0)

    def test_unknown_target_is_unmatched_rather_than_scored_as_zero(self):
        report = evaluate_calculation_validation(
            service=self.service,
            references=[
                {
                    "reference_id": "manual_missing",
                    "level": "component",
                    "target_id": "DOES_NOT_EXIST",
                    "expected": {"C_total_kgCO2e": 10.0},
                }
            ],
        )

        record = report["records"][0]
        self.assertEqual(record["status"], "unmatched")
        self.assertFalse(record["within_tolerance"])
        self.assertEqual(report["metrics"]["total_mae_kgCO2e"], None)


class QASystemHeuristicTests(unittest.TestCase):
    """The legacy reader still serves the design backbone, so its rules stay tested."""

    def test_qa_heuristic_does_not_treat_show_as_how_traceability(self):
        qa = object.__new__(DM2CQuestionAnsweringSystem)

        requirement = qa._heuristic_requirement(
            "Rank the material carbon contributors and show the largest material group.",
            [],
        )

        self.assertEqual(requirement.perspective, "material")
        self.assertEqual(requirement.operation, "rank")

    def test_unresolved_target_status_is_propagated_to_query_result(self):
        qa = object.__new__(DM2CQuestionAnsweringSystem)
        requirement = CarbonRequirement(
            perspective="traceability",
            operation="trace",
            status="unresolved_target",
        )

        query_result, trace = qa.execute_query(requirement, [], [])

        self.assertEqual(query_result["status"], "unresolved_target")
        self.assertEqual(trace["observation"]["status"], "unresolved_target")

    def test_project_level_product_hint_does_not_force_component_grounding(self):
        qa = object.__new__(DM2CQuestionAnsweringSystem)
        row = _sample_normalized_row()
        requirement = CarbonRequirement(
            perspective="product",
            target_hints=["project", "total C_MM", "material carbon", "known process carbon"],
            operation="aggregate",
        )

        grounded, trace = qa._ground_requirement(requirement, [row], [])

        self.assertEqual(requirement.status, "executable")
        self.assertEqual(grounded, [])
        self.assertEqual(trace["observation"]["status"], "executable")

    def test_process_target_ids_do_not_require_component_id_grounding(self):
        qa = object.__new__(DM2CQuestionAnsweringSystem)
        row = _sample_normalized_row()
        requirement = CarbonRequirement(
            perspective="process",
            target_ids=["process_steel_column"],
            operation="trace",
        )

        grounded, trace = qa._ground_requirement(requirement, [row], [])

        self.assertEqual(requirement.status, "executable")
        self.assertEqual(grounded, [])
        self.assertEqual(trace["observation"]["status"], "executable")


def _sample_normalized_row():
    return NormalizedCarbonRow(
        component_id="G1",
        component_name="Beam A",
        ifc_type="IfcBeam",
        material_text="steel",
        c_mat=10.0,
        c_proc=None,
        c_total=10.0,
        material_status="complete",
        process_status="incomplete",
        total_status="material_only",
        selected_factor={},
        material_carbon={},
        process_carbon={},
        process_steps=[],
        gaps=[{"kind": "missing_process_quantity"}],
        confidence=None,
        raw={"id": "G1"},
    )


if __name__ == "__main__":
    unittest.main()
