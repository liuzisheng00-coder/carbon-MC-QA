#!/usr/bin/env python3
"""Apply the user's first-round E4 v2 review to produce a v3 review candidate."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence

from dm2c_e4_benchmark_repair import validate_operation_semantics


HUMAN_REVISED_QUESTIONS: Dict[str, str] = {
    "e4_balanced_091_traceability_compare_incomplete_path": "Compare the completeness of the quantity-factor-source evidence chains for the two selected BIM items.",
    "e4_balanced_092_process_rank_empty_result": "Rank coating-stage factory-energy records by kgCO2e from highest to lowest.",
    "e4_balanced_093_product_explain_incomplete_path": "Explain why the selected BIM item's component-carbon result cannot be completed from its quantity and emission-factor evidence.",
    "e4_balanced_094_process_compare_unresolved_target": "Compare factory-energy carbon for North Coating Line records NC-01 and NC-02.",
    "e4_balanced_099_product_compare_empty_result": "Compare known component carbon between timber and steel roof-cassette variants.",
    "e4_balanced_102_process_aggregate_incomplete_path": "Aggregate process-energy carbon for the selected BIM component and report whether a complete component-level total can be produced.",
    "e4_balanced_105_traceability_rank_incomplete_path": "Rank the selected BIM item's carbon-evidence chains by traced kgCO2e from highest to lowest, and disclose any incomplete chain.",
    "e4_balanced_108_traceability_rank_empty_result": "Rank roof-cassette carbon-evidence chains by traced kgCO2e from highest to lowest.",
    "e4_balanced_109_product_rank_clarification_required": "Rank BIM components of the same type as the module component currently indicated in the chat by known kgCO2e from highest to lowest.",
    "e4_balanced_110_process_compare_clarification_required": "Compare electricity and diesel carbon for the factory-energy record currently indicated in the chat.",
    "e4_balanced_111_material_aggregate_empty_result": "Aggregate (sum) all material-carbon records classified as insulation board.",
    "e4_balanced_112_product_aggregate_empty_result": "Aggregate (sum) the known component-carbon values for all roof cassettes.",
    "e4_balanced_113_process_rank_unresolved_target": "Rank factory-energy records for production line PL-Z by kgCO2e from highest to lowest.",
    "e4_balanced_117_material_rank_incomplete_path": "Rank the selected BIM item's material-carbon records by kgCO2e from highest to lowest, and disclose records that cannot be ranked because factor evidence is missing.",
    "e4_balanced_120_process_compare_incomplete_path": "Compare component-level process-energy carbon for the two selected BIM components.",
    "e4_balanced_124_process_rank_clarification_required": "Rank factory-energy records for the module currently indicated in the chat by kgCO2e from highest to lowest.",
    "e4_balanced_125_material_rank_unresolved_target": "Rank material-carbon records for vacuum-insulation panel VIP-X by kgCO2e from highest to lowest.",
    "e4_balanced_127_product_compare_unresolved_target": "Compare known component carbon for roof cassettes RC-X1 and RC-X2.",
    "e4_balanced_128_traceability_compare_clarification_required": "Compare quantity-factor-source evidence-chain completeness for the two panels currently indicated in the chat.",
    "e4_balanced_134_process_rank_incomplete_path": "Rank process-energy records attributed to the selected BIM component by kgCO2e from highest to lowest.",
    "e4_balanced_136_traceability_rank_clarification_required": "Rank carbon-evidence chains for the panel currently indicated in the chat by traced kgCO2e from highest to lowest.",
    "e4_balanced_139_process_value_empty_result": "What factory-energy carbon value is recorded for the coating stage?",
    "e4_balanced_140_traceability_compare_unresolved_target": "Compare quantity-factor-source evidence-chain completeness for roof cassettes RC-X1 and RC-X2.",
    "e4_balanced_141_material_compare_empty_result": "Compare material-carbon values for mineral-wool and vacuum-insulation board records.",
    "e4_balanced_146_traceability_rank_unresolved_target": "Rank carbon-evidence chains for roof cassette RC-X1 by traced kgCO2e from highest to lowest.",
}

PROCESS_GRANULARITY_CASES = {
    "e4_balanced_102_process_aggregate_incomplete_path",
    "e4_balanced_120_process_compare_incomplete_path",
    "e4_balanced_134_process_rank_incomplete_path",
}

SECOND_SELECTED_COMPONENT = {
    "e4_balanced_091_traceability_compare_incomplete_path": "3czbugqbT86PTcmnme1lZ7",
    "e4_balanced_120_process_compare_incomplete_path": "3czbugqbT86PTcmnme1lZV",
}

NATURAL_SEARCH_PHRASES = {
    "e4_balanced_092_process_rank_empty_result": "coating-stage factory-energy records",
    "e4_balanced_094_process_compare_unresolved_target": "North Coating Line records NC-01 and NC-02",
    "e4_balanced_099_product_compare_empty_result": "timber and steel roof-cassette variants",
    "e4_balanced_108_traceability_rank_empty_result": "roof-cassette carbon-evidence chains",
    "e4_balanced_111_material_aggregate_empty_result": "insulation-board material-carbon records",
    "e4_balanced_112_product_aggregate_empty_result": "roof-cassette component-carbon records",
    "e4_balanced_113_process_rank_unresolved_target": "production line PL-Z",
    "e4_balanced_125_material_rank_unresolved_target": "vacuum-insulation panel VIP-X",
    "e4_balanced_127_product_compare_unresolved_target": "roof cassettes RC-X1 and RC-X2",
    "e4_balanced_139_process_value_empty_result": "coating stage",
    "e4_balanced_140_traceability_compare_unresolved_target": "roof cassettes RC-X1 and RC-X2",
    "e4_balanced_141_material_compare_empty_result": "mineral-wool and vacuum-insulation board records",
    "e4_balanced_146_traceability_rank_unresolved_target": "roof cassette RC-X1",
}


def sanitize_case_filters(case: Dict[str, Any]) -> Dict[str, Any]:
    revised = copy.deepcopy(case)
    expected = revised.get("expected") or {}
    status = str(expected.get("status") or "")
    original = revised.get("filters") or {}
    filters = {
        key: copy.deepcopy(value)
        for key, value in original.items()
        if key in {"input_mode", "selected_count", "natural_search_phrase", "ambiguous_target"}
    }
    if status == "empty_result":
        filters["reference_mode"] = "category_filter"
    elif status == "unresolved_target":
        filters["reference_mode"] = "entity_reference"
    phrase = NATURAL_SEARCH_PHRASES.get(str(revised.get("case_id") or ""))
    if phrase:
        filters["natural_search_phrase"] = phrase
    revised["filters"] = filters
    return revised


def apply_human_revision(case: Dict[str, Any], review_note: str = "") -> Dict[str, Any]:
    revised = sanitize_case_filters(case)
    case_id = str(revised.get("case_id") or "")
    new_question = HUMAN_REVISED_QUESTIONS.get(case_id)
    if not new_question:
        return revised

    old_question = str(revised.get("question") or "")
    revised["question"] = new_question
    second = SECOND_SELECTED_COMPONENT.get(case_id)
    if second:
        selected = [str(value) for value in revised.get("selected_component_ids") or []]
        if second not in selected:
            selected.append(second)
        revised["selected_component_ids"] = selected
        revised.setdefault("filters", {})["input_mode"] = "bim_selection"
        revised["filters"]["selected_count"] = len(selected)
        revised["scope"] = "selected_component"

    if case_id in PROCESS_GRANULARITY_CASES:
        selected = [str(value) for value in revised.get("selected_component_ids") or []]
        expected = revised.setdefault("expected", {})
        expected["summary"] = {
            "blocked_status": "process_granularity_not_supported"
        }
        expected["answer_contains"] = ["process_granularity_not_supported"]
        expected["evidence_ids"] = [
            value if value.startswith("component:") else f"component:{value}"
            for value in selected
        ]

    revision = dict(revised.get("question_revision") or {})
    revision["human_review_round_1"] = {
        "decision": "REVISE",
        "note": review_note,
        "question_before_human_revision": old_question,
    }
    revision["policy"] = "human_reviewed_natural_question_v3"
    revised["question_revision"] = revision
    return revised


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_human_revised_benchmark(
    source_jsonl: Path,
    review_json: Path,
    out_root: Path,
) -> Dict[str, Any]:
    source_cases = _read_jsonl(source_jsonl)
    review = json.loads(review_json.read_text(encoding="utf-8"))
    revise_rows = review.get("reviseRows") or []
    notes = {str(row.get("case_id") or ""): str(row.get("review_notes") or "") for row in revise_rows}
    if set(notes) != set(HUMAN_REVISED_QUESTIONS):
        missing = sorted(set(notes) - set(HUMAN_REVISED_QUESTIONS))
        extra = sorted(set(HUMAN_REVISED_QUESTIONS) - set(notes))
        raise ValueError(f"Human revision map mismatch; unmapped_review={missing}, unreviewed_map={extra}")

    revised_cases = [apply_human_revision(case, notes.get(str(case.get("case_id") or ""), "")) for case in source_cases]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"human_revised_{timestamp}_v3"
    output_dir = out_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_path = output_dir / "e4_balanced_benchmark_150_human_revised_v3.jsonl"
    benchmark_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in revised_cases) + "\n",
        encoding="utf-8",
    )

    review_csv = output_dir / "e4_human_revision_round2.csv"
    with review_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "perspective",
                "operation",
                "status",
                "original_question",
                "repaired_question",
                "operation_semantics_valid",
                "answer_contains",
                "human_review",
                "review_notes",
            ],
        )
        writer.writeheader()
        source_by_id = {str(case.get("case_id") or ""): case for case in source_cases}
        for case in revised_cases:
            case_id = str(case.get("case_id") or "")
            expected = case.get("expected") or {}
            before = source_by_id[case_id]
            writer.writerow(
                {
                    "case_id": case_id,
                    "perspective": expected.get("perspective", ""),
                    "operation": expected.get("operation", ""),
                    "status": expected.get("status", ""),
                    "original_question": before.get("question", ""),
                    "repaired_question": case.get("question", ""),
                    "operation_semantics_valid": validate_operation_semantics(case),
                    "answer_contains": "; ".join(expected.get("answer_contains") or []),
                    "human_review": "",
                    "review_notes": notes.get(case_id, ""),
                }
            )

    changed = [
        case
        for case in revised_cases
        if source_by_id[str(case.get("case_id") or "")].get("question") != case.get("question")
    ]
    invalid = [str(case.get("case_id") or "") for case in changed if not validate_operation_semantics(case)]
    leaked_filters = [
        str(case.get("case_id") or "")
        for case in revised_cases
        if any(key in (case.get("filters") or {}) for key in ("target_id", "target_hints"))
    ]
    summary = {
        "run_id": run_id,
        "version_label": "E4 QA Benchmark V3",
        "workbook_title": "E4 QA Benchmark V3 - Human Review Round 2",
        "source_jsonl": str(source_jsonl),
        "review_json": str(review_json),
        "output_dir": str(output_dir),
        "case_count": len(revised_cases),
        "changed_question_count": len(changed),
        "round1_pass_count": int((review.get("counts") or {}).get("PASS") or 0),
        "round1_revise_count": int((review.get("counts") or {}).get("REVISE") or 0),
        "operation_semantic_violation_count": len(invalid),
        "operation_semantic_violation_case_ids": invalid,
        "hidden_filter_leak_count": len(leaked_filters),
        "hidden_filter_leak_case_ids": leaked_filters,
        "process_granularity_case_count": len(PROCESS_GRANULARITY_CASES),
        "duplicate_question_count": len(revised_cases) - len({str(case.get("question") or "").casefold() for case in revised_cases}),
        "status_counts": dict(Counter(str((case.get("expected") or {}).get("status") or "") for case in revised_cases)),
        "requires_human_review": True,
        "paper_ready": False,
        "benchmark_jsonl": str(benchmark_path),
        "review_csv": str(review_csv),
    }
    summary_path = output_dir / "e4_human_revision_round2_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "latest_e4_human_revision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply E4 human review revisions.")
    parser.add_argument("--source-jsonl", type=Path, required=True)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("outputs/research_experiments/e4_benchmark"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    summary = build_human_revised_benchmark(
        args.source_jsonl, args.review_json, args.out_root
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
