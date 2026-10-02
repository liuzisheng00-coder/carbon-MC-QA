from copy import deepcopy

from dm2c_e4_benchmark_repair import repair_case, validate_operation_semantics


def base_case(operation, status, perspective="process"):
    return {
        "case_id": f"case-{perspective}-{operation}-{status}",
        "question": "Original naturalized question.",
        "expected": {
            "perspective": perspective,
            "operation": operation,
            "status": status,
            "summary": {},
        },
        "selected_component_ids": [],
        "scope": "project",
        "filters": {},
    }


def test_repair_preserves_executable_question_and_truth():
    case = base_case("value", "executable", "product")
    case["question"] = "What is the project's known carbon total?"
    original = deepcopy(case)

    repaired = repair_case(case)

    assert repaired["question"] == original["question"]
    assert repaired["expected"] == original["expected"]


def test_repair_removes_grouping_language_from_material_value_question():
    case = base_case("value", "executable", "material")
    case["question"] = "How much material carbon is known in the project, grouped by material family?"
    expected_before = deepcopy(case["expected"])

    repaired = repair_case(case)

    assert "grouped" not in repaired["question"].lower()
    assert "total known material carbon" in repaired["question"].lower()
    assert repaired["expected"] == expected_before
    assert validate_operation_semantics(repaired) is True


def test_operation_validator_accepts_natural_superlative_rank_question():
    case = base_case("rank", "executable", "product")
    case["question"] = "Which component contributes the most known carbon in this project?"

    assert validate_operation_semantics(case) is True


def test_operation_validator_accepts_reviewed_natural_synonyms():
    examples = {
        "value": [
            "Show the known carbon records and evidence coverage for this scope.",
            "Report the known factory energy carbon and supporting record count.",
        ],
        "aggregate": [
            "Show the project-level carbon breakdown across materials and factory energy.",
            "Show the material-family carbon totals supported by the current records.",
        ],
        "rank": [
            "Which material group is the dominant contributor to material carbon?",
            "Which production energy record dominates the known process carbon?",
        ],
        "compare": [
            "How different are the supported carbon totals for the selected BIM items?",
            "Show the material-carbon gap between the two main material groups.",
        ],
        "explain": [
            "Describe how the largest factory energy carbon record was calculated.",
            "Show why the selected carbon record is supported by the available evidence.",
        ],
        "trace": [
            "Show the evidence chain for the largest steel material carbon record.",
            "Show the source-to-factor path for the largest factory energy carbon record.",
        ],
    }
    for operation, questions in examples.items():
        for question in questions:
            case = base_case(operation, "executable")
            case["question"] = question
            assert validate_operation_semantics(case), (operation, question)


def test_operation_validator_rejects_clear_operation_mismatches():
    mismatches = {
        "value": "Compare the two material groups.",
        "aggregate": "Explain the selected carbon record.",
        "rank": "Compare electricity and diesel carbon.",
        "compare": "Rank factory-energy records from highest to lowest.",
        "explain": "Rank material-carbon records from highest to lowest.",
        "trace": "How much known material carbon is recorded?",
    }
    for operation, question in mismatches.items():
        case = base_case(operation, "executable")
        case["question"] = question
        assert validate_operation_semantics(case) is False, (operation, question)


def test_repair_makes_empty_rank_question_express_ranking_without_leaking_status():
    case = base_case("rank", "empty_result", "process")

    repaired = repair_case(case)

    assert "rank" in repaired["question"].lower()
    assert "coating process" in repaired["question"].lower()
    assert "empty_result" not in repaired["question"]
    assert validate_operation_semantics(repaired) is True


def test_repair_removes_hidden_unresolved_id_from_required_answer_terms():
    case = base_case("compare", "unresolved_target", "process")
    case["expected"]["summary"] = {"target_id": "E4_DOES_NOT_EXIST_094"}
    case["expected"]["answer_contains"] = ["E4_DOES_NOT_EXIST_094"]

    repaired = repair_case(case)

    assert "compare" in repaired["question"].lower()
    assert "E4_DOES_NOT_EXIST_094" not in repaired["question"]
    assert repaired["expected"]["answer_contains"] == ["unresolved_target"]
    assert repaired["expected"]["summary"]["target_id"] == "E4_DOES_NOT_EXIST_094"


def test_repair_keeps_selected_context_for_incomplete_aggregate_request():
    case = base_case("aggregate", "incomplete_path", "material")
    case["selected_component_ids"] = ["3czbugqbT86PTcmnme1lZH"]
    case["scope"] = "selected_component"
    case["expected"]["summary"] = {"blocked_status": "blocked_factor_unresolved"}

    repaired = repair_case(case)

    assert "aggregate" in repaired["question"].lower()
    assert "selected material" in repaired["question"].lower()
    assert repaired["selected_component_ids"] == case["selected_component_ids"]
    assert repaired["expected"]["summary"] == case["expected"]["summary"]
    assert validate_operation_semantics(repaired) is True


def test_repair_produces_operation_semantics_for_all_non_executable_operations():
    for operation in ("value", "rank", "compare", "explain", "trace"):
        repaired = repair_case(base_case(operation, "clarification_required", "traceability"))
        assert validate_operation_semantics(repaired), (operation, repaired["question"])
