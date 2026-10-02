from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import pytest

from dm2c_canonical_v2_reader import dimension_ids, lookup_dimension
from dm2c_carbonql import CarbonQLProgram
from dm2c_e4_layerdedup_benchmark import (
    _OPERATION_ALIASES,
    build_generated_cases,
    reconstruct_program,
    selection_remap,
    write_generated_cases,
)
from dm2c_full_qa_experiment_runner import (
    execute_canonical_query,
    load_full_qa_context,
)


RELEASE_DIR = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_layerdedup"
)
REVIEWED_V9 = Path(
    "outputs/research_experiments/e4_benchmark/"
    "frozen_20260712_145321_v9_human_pass/"
    "e4_balanced_benchmark_150_human_reviewed_v9.jsonl"
)
EXPECTED_QUOTA = {
    "executable": 90,
    "incomplete_path": 14,
    "empty_result": 16,
    "unresolved_target": 15,
    "clarification_required": 15,
}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def reviewed_cases() -> list[dict[str, object]]:
    return _read_jsonl(REVIEWED_V9)


@pytest.fixture(scope="module")
def generated_cases() -> list[dict[str, object]]:
    return build_generated_cases(RELEASE_DIR, REVIEWED_V9)


@pytest.fixture(scope="module")
def layerdedup_context():
    return load_full_qa_context(RELEASE_DIR, allow_synthetic=True)


def _program(*steps: dict[str, object]) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": list(steps), "holes": []})


def test_generated_cases_preserve_ordered_v9_ids_cells_and_status_quota(
    reviewed_cases: list[dict[str, object]],
    generated_cases: list[dict[str, object]],
) -> None:
    assert len(generated_cases) == 150
    assert [case["case_id"] for case in generated_cases] == [
        case["case_id"] for case in reviewed_cases
    ]
    def _normalize_cell(cell: dict) -> dict:
        c = dict(cell)
        c["operation"] = _OPERATION_ALIASES.get(c.get("operation", ""), c.get("operation", ""))
        return c

    assert [
        case["generation"]["matrix_cell"]  # type: ignore[index]
        for case in generated_cases
    ] == [
        _normalize_cell(case["generation"]["matrix_cell"])  # type: ignore[index]
        for case in reviewed_cases
    ]
    assert Counter(
        case["expected"]["status"]  # type: ignore[index]
        for case in generated_cases
    ) == EXPECTED_QUOTA
    assert {
        case["generation"]["source"]  # type: ignore[index]
        for case in generated_cases
    } == {"kg_programmatic"}
    assert {
        Path(case["generation"]["kg_dir"])  # type: ignore[index,arg-type]
        for case in generated_cases
    } == {RELEASE_DIR}


def test_selected_targets_are_real_layerdedup_entities_including_broken_chains_and_module(
    reviewed_cases: list[dict[str, object]],
    generated_cases: list[dict[str, object]],
    layerdedup_context,
) -> None:
    component_ids = set(dimension_ids(layerdedup_context.canonical, "component"))
    module_ids = set(dimension_ids(layerdedup_context.canonical, "module"))
    valid_targets = component_ids | module_ids
    generated_selected = [
        selected
        for case in generated_cases
        for selected in case["selected_component_ids"]  # type: ignore[index]
    ]
    old_selected = {
        selected
        for case in reviewed_cases
        for selected in case["selected_component_ids"]  # type: ignore[index]
    }
    assert generated_selected
    assert set(generated_selected) <= valid_targets
    assert set(generated_selected).isdisjoint(old_selected)
    assert set(generated_selected).intersection(module_ids) == module_ids

    rejected_components = {
        component_id
        for record in layerdedup_context.validation_coverage.records
        if record.status == "rejected"
        for component_id in record.component_ids
    }
    incomplete_targets = {
        selected
        for case in generated_cases
        if case["expected"]["status"] == "incomplete_path"  # type: ignore[index]
        for selected in case["selected_component_ids"]  # type: ignore[index]
    }
    assert incomplete_targets
    assert incomplete_targets <= rejected_components
    incomplete_cases = [
        case
        for case in generated_cases
        if case["expected"]["status"] == "incomplete_path"  # type: ignore[index]
    ]
    assert all("evidence_ids" in case["expected"] for case in incomplete_cases)
    assert {
        case["expected"]["summary"]["blocked_status"]  # type: ignore[index]
        for case in incomplete_cases
    } == {"material_quantity_basis_missing", "thickness_missing"}

    reviewed_by_id = {case["case_id"]: case for case in reviewed_cases}
    for case in incomplete_cases:
        old = reviewed_by_id[case["case_id"]]
        old_template = old["question_template"]
        new_target = case["selected_component_ids"][0]
        props = lookup_dimension(
            layerdedup_context.canonical, "component", new_target
        )["props"]
        if "U型槽钢" in old_template:
            assert props["ifcClass"] == "IfcBeam"
        else:
            assert "W2-2A126" in old_template
            assert props["ifcClass"] == "IfcWindow"


def test_product_project_gold_matches_fresh_executor_totals(
    generated_cases: list[dict[str, object]],
    layerdedup_context,
) -> None:
    by_id = {case["case_id"]: case for case in generated_cases}
    summary = by_id["e4_balanced_001_product_value_executable"]["expected"][
        "summary"
    ]
    total = execute_canonical_query(
        layerdedup_context,
        _program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "all", "known_total": True},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    material = execute_canonical_query(
        layerdedup_context,
        _program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "material", "known_total": True},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    process = execute_canonical_query(
        layerdedup_context,
        _program(
            {"op": "SelectProject"},
            {"op": "CarbonAtoms", "source": "process", "known_total": True},
            {"op": "GroupBy", "keys": ["component"]},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ),
    )
    assert total.status == material.status == process.status == "executable"
    assert summary == {
        "components": 729,
        "knownTotalCarbon_kgCO2e": pytest.approx(total.summary["total_kgCO2e"]),
        "totalMaterialCarbon_kgCO2e": pytest.approx(
            material.summary["total_kgCO2e"]
        ),
        "knownProcessCarbon_kgCO2e": pytest.approx(
            process.summary["total_kgCO2e"]
        ),
    }
    assert summary["knownTotalCarbon_kgCO2e"] == pytest.approx(8197.695537310923)
    assert summary["totalMaterialCarbon_kgCO2e"] == pytest.approx(
        7928.414716678913
    )
    assert summary["knownProcessCarbon_kgCO2e"] == pytest.approx(
        269.28082063201094
    )
    # The split is a partition of the product total, so the shared factory
    # records that no product carries appear on neither side of it.
    assert summary["knownTotalCarbon_kgCO2e"] == pytest.approx(
        summary["totalMaterialCarbon_kgCO2e"]
        + summary["knownProcessCarbon_kgCO2e"]
    )


def test_executor_results_expose_generator_gold_metadata(
    generated_cases: list[dict[str, object]],
    layerdedup_context,
) -> None:
    by_status = {}
    for case in generated_cases:
        by_status.setdefault(case["expected"]["status"], case)  # type: ignore[index]

    product = generated_cases[0]
    product_result = execute_canonical_query(
        layerdedup_context,
        reconstruct_program(product, layerdedup_context),
    )
    assert product["expected"]["summary"]["components"] == product_result.summary[  # type: ignore[index]
        "available_component_count"
    ]

    for status, summary_key in (
        ("incomplete_path", "blocked_status"),
        ("unresolved_target", "target_id"),
        ("clarification_required", "ambiguous_target"),
    ):
        case = by_status[status]
        result = execute_canonical_query(
            layerdedup_context,
            reconstruct_program(case, layerdedup_context),
            selected_component_ids=case["selected_component_ids"],
        )
        assert case["expected"]["summary"][summary_key] == result.summary[summary_key]  # type: ignore[index]
        assert case["expected"].get("evidence_ids", []) == list(result.evidence_ids)  # type: ignore[union-attr]


def test_product_and_trace_empty_programs_use_real_zero_row_component_type(
    generated_cases: list[dict[str, object]],
    layerdedup_context,
) -> None:
    available_types = set(
        dimension_ids(layerdedup_context.canonical, "component_type")
    )
    cases = [
        case
        for case in generated_cases
        if case["expected"]["status"] == "empty_result"  # type: ignore[index]
        and case["expected"]["perspective"] in {"product", "traceability"}  # type: ignore[index]
    ]
    assert cases
    for case in cases:
        program = reconstruct_program(case, layerdedup_context)
        filters = [step for step in program.steps if step.op == "Filter"]
        assert len(filters) == 1
        assert filters[0].args["field"] == "component_type"
        assert filters[0].args["equals"] in available_types
        result = execute_canonical_query(layerdedup_context, program)
        assert result.status == "empty_result"
        assert not result.rows


def test_non_executable_templates_bind_the_actual_program_target(
    reviewed_cases: list[dict[str, object]],
    generated_cases: list[dict[str, object]],
    layerdedup_context,
) -> None:
    reviewed_by_id = {case["case_id"]: case for case in reviewed_cases}
    for case in generated_cases:
        if case["expected"]["status"] == "executable":  # type: ignore[index]
            continue
        program = reconstruct_program(case, layerdedup_context)
        selector = program.steps[0]
        if case["expected"]["status"] == "incomplete_path":  # type: ignore[index]
            target_literals = list(case["selected_component_ids"])
        elif case["expected"]["status"] == "empty_result":  # type: ignore[index]
            filter_step = next(step for step in program.steps if step.op == "Filter")
            target_literals = [filter_step.args["equals"]]
        elif "ids" in selector.args:
            target_literals = list(selector.args["ids"])
        else:
            target_literals = [selector.args["value"]]
        template = case["question_template"]
        assert target_literals
        assert all(str(target) in template for target in target_literals)
        assert template != reviewed_by_id[case["case_id"]]["question_template"]


def test_selection_remap_reads_actual_reviewed_input_and_states_concrete_basis(
    tmp_path: Path,
    reviewed_cases: list[dict[str, object]],
    generated_cases: list[dict[str, object]],
) -> None:
    remap = selection_remap(generated_cases)
    for item in remap["case_remaps"]:
        assert all(old_id in item["reason"] for old_id in item["old_ids"])
        assert all(new_id in item["reason"] for new_id in item["new_ids"])
        assert "evidence" in item["reason"].casefold()
        if item["status"] == "incomplete_path":
            assert "rejected" in item["reason"].casefold()

    variant = deepcopy(reviewed_cases)
    variant[3]["selected_component_ids"] = [
        "reviewed:alternate-component-a",
        "reviewed:alternate-component-b",
    ]
    variant_path = tmp_path / "reviewed-variant.jsonl"
    variant_path.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n"
            for case in variant
        ),
        encoding="utf-8",
    )
    variant_generated = build_generated_cases(RELEASE_DIR, variant_path)
    variant_item = next(
        item
        for item in selection_remap(variant_generated)["case_remaps"]
        if item["case_id"] == variant[3]["case_id"]
    )
    assert variant_item["old_ids"] == variant[3]["selected_component_ids"]


def test_all_150_expected_summaries_and_evidence_replay_from_executor_results(
    generated_cases: list[dict[str, object]],
    layerdedup_context,
) -> None:
    cache = {}

    def run(program, selected=()):
        key = json.dumps(
            [program.to_dict(), list(selected)],
            sort_keys=True,
            separators=(",", ":"),
        )
        if key not in cache:
            cache[key] = execute_canonical_query(
                layerdedup_context,
                program,
                selected_component_ids=selected,
            )
        return cache[key]

    def trace_evidence(perspective, entity_id):
        if perspective == "product":
            selector = {"op": "SelectClicked", "ids": [entity_id]}
            source = "all"
            selected = (entity_id,)
            extra = ()
        elif perspective == "material":
            selector = {
                "op": "ResolveEntities",
                "entity_type": "material",
                "ids": [entity_id],
            }
            source = "material"
            selected = ()
            extra = ()
        else:
            selector = {
                "op": "ResolveEntities",
                "entity_type": "process",
                "ids": [entity_id],
            }
            source = "process"
            selected = ()
            extra = (
                {"op": "Filter", "field": "process", "equals": entity_id},
            )
        return run(
            _program(
                selector,
                {"op": "CarbonAtoms", "source": source, "known_total": True},
                *extra,
                {"op": "Trace"},
            ),
            selected,
        ).evidence_ids

    assert len(generated_cases) == 150
    for case in generated_cases:
        expected = case["expected"]
        perspective = expected["perspective"]
        operation = expected["operation"]
        target_status = expected["status"]
        selected = tuple(case["selected_component_ids"])
        result = run(
            reconstruct_program(case, layerdedup_context),
            selected,
        )
        assert result.status == target_status, case["case_id"]
        rows = [dict(row) for row in result.rows]
        evidence = ()

        if target_status == "incomplete_path":
            summary = {"blocked_status": result.summary["blocked_status"]}
            evidence = result.evidence_ids
        elif target_status == "empty_result":
            summary = {"row_count": result.summary["row_count"]}
        elif target_status == "unresolved_target":
            summary = {"target_id": result.summary["target_id"]}
        elif target_status == "clarification_required":
            summary = {"ambiguous_target": result.summary["ambiguous_target"]}
        elif perspective == "product" and operation in {"value", "aggregate"}:
            material = run(
                _program(
                    {"op": "SelectProject"},
                    {
                        "op": "CarbonAtoms",
                        "source": "material",
                        "known_total": True,
                    },
                    {"op": "Aggregate", "metric": "sum_kgCO2e"},
                )
            )
            process = run(
                _program(
                    {"op": "SelectProject"},
                    {
                        "op": "CarbonAtoms",
                        "source": "process",
                        "known_total": True,
                    },
                    {"op": "GroupBy", "keys": ["component"]},
                    {"op": "Aggregate", "metric": "sum_kgCO2e"},
                )
            )
            summary = {
                "components": result.summary["available_component_count"],
                "knownTotalCarbon_kgCO2e": result.summary["total_kgCO2e"],
                "totalMaterialCarbon_kgCO2e": material.summary["total_kgCO2e"],
                "knownProcessCarbon_kgCO2e": process.summary["total_kgCO2e"],
            }
        elif perspective == "product" and operation == "rank":
            summary = {
                "top_component_knownTotalCarbon_kgCO2e": rows[0]["kgCO2e"]
            }
            evidence = trace_evidence("product", rows[0]["component"])
        elif perspective == "product" and operation == "compare":
            summary = {
                "first_component_kgCO2e": rows[0]["kgCO2e"],
                "second_component_kgCO2e": rows[1]["kgCO2e"],
                "difference_kgCO2e": rows[0]["kgCO2e"] - rows[1]["kgCO2e"],
            }
            evidence = result.evidence_ids
        elif perspective == "product":
            summary = {
                "component_knownTotalCarbon_kgCO2e": result.summary[
                    "total_kgCO2e"
                ]
            }
            evidence = result.evidence_ids
        elif perspective == "material" and operation in {"value", "aggregate"}:
            summary = {
                "totalMaterialCarbon_kgCO2e": result.summary["total_kgCO2e"],
                "material_families": len(rows),
            }
        elif perspective == "material" and operation == "rank":
            summary = {"top_material_family_kgCO2e": rows[0]["kgCO2e"]}
            evidence = trace_evidence("material", rows[0]["material"])
        elif perspective == "material" and operation == "compare":
            summary = {
                "first_material_family_kgCO2e": rows[0]["kgCO2e"],
                "second_material_family_kgCO2e": rows[1]["kgCO2e"],
                "difference_kgCO2e": rows[0]["kgCO2e"] - rows[1]["kgCO2e"],
            }
        elif perspective == "material":
            summary = {
                "material_record_kgCO2e": result.summary["total_kgCO2e"]
            }
            evidence = result.evidence_ids
        elif perspective == "process" and operation in {"value", "aggregate"}:
            summary = {
                "knownProcessCarbon_kgCO2e": result.summary["total_kgCO2e"],
                "process_energy_records": len(result.emission_ids),
            }
            evidence = result.evidence_ids
        elif perspective == "process" and operation == "rank":
            summary = {"top_process_record_kgCO2e": rows[0]["kgCO2e"]}
            # Rank is carrier-grouped; keep the ranked result's own evidence.
            evidence = result.evidence_ids
        elif perspective == "process" and operation == "compare":
            summary = {
                "first_carrier_kgCO2e": rows[0]["kgCO2e"],
                "second_carrier_kgCO2e": rows[1]["kgCO2e"],
                "difference_kgCO2e": rows[0]["kgCO2e"] - rows[1]["kgCO2e"],
            }
        elif perspective == "process":
            summary = {
                "process_record_kgCO2e": result.summary["total_kgCO2e"]
            }
            evidence = result.evidence_ids
        else:
            summary = {
                "traced_atomic_emission_kgCO2e": result.summary["total_kgCO2e"],
                "evidence_hops": len(result.evidence_ids),
            }
            evidence = result.evidence_ids

        compact_expected = json.dumps(
            {
                "summary": expected["summary"],
                "evidence_ids": expected.get("evidence_ids", []),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        compact_replayed = json.dumps(
            {"summary": summary, "evidence_ids": list(evidence)},
            sort_keys=True,
            separators=(",", ":"),
        )
        assert compact_expected == compact_replayed, case["case_id"]


def test_selection_remap_explains_every_changed_case_and_writer_is_deterministic(
    tmp_path: Path,
    reviewed_cases: list[dict[str, object]],
    generated_cases: list[dict[str, object]],
) -> None:
    changed_case_ids = {
        old["case_id"]
        for old, new in zip(reviewed_cases, generated_cases)
        if old["selected_component_ids"] != new["selected_component_ids"]
    }
    remap = selection_remap(generated_cases)
    case_remaps = remap["case_remaps"]
    assert remap["changed_case_count"] == len(changed_case_ids) == 44
    assert {item["case_id"] for item in case_remaps} == changed_case_ids
    assert all(item["old_ids"] != item["new_ids"] for item in case_remaps)
    assert all(item["reason"].strip() for item in case_remaps)
    assert any(
        "ModularUnit" in " ".join(item["new_ids"])
        and "module" in item["reason"].casefold()
        for item in case_remaps
    )
    assert all(
        "rejected" in item["reason"].casefold()
        for item in case_remaps
        if item["status"] == "incomplete_path"
    )

    first = write_generated_cases(generated_cases, tmp_path / "first.jsonl")
    second = write_generated_cases(generated_cases, tmp_path / "second.jsonl")
    assert first == tmp_path / "first.jsonl"
    assert second == tmp_path / "second.jsonl"
    assert first.read_bytes() == second.read_bytes()
    assert _read_jsonl(first) == generated_cases
