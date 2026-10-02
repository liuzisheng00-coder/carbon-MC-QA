#!/usr/bin/env python3
"""Apply the user's second-round E4 review and produce a V4 review candidate."""

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


ROUND2_REVISED_QUESTIONS: Dict[str, str] = {
    "e4_balanced_091_traceability_compare_incomplete_path": (
        "Compare the completeness of the quantity-factor-source evidence chains "
        "for these two selected BIM components."
    ),
    "e4_balanced_094_process_compare_unresolved_target": (
        "Compare factory-energy carbon for the two coating-line records from the "
        "north side of the factory."
    ),
    "e4_balanced_110_process_compare_clarification_required": (
        "Compare electricity and diesel carbon for that factory-energy record."
    ),
    "e4_balanced_120_process_compare_incomplete_path": (
        "Compare the process-energy carbon that can be attributed to these two "
        "selected BIM components."
    ),
    "e4_balanced_124_process_rank_clarification_required": (
        "Rank factory-energy records for that module by kgCO2e from highest to lowest."
    ),
    "e4_balanced_125_material_rank_unresolved_target": (
        "Rank material-carbon records for the vacuum-insulated panel above the "
        "loading bay by kgCO2e from highest to lowest."
    ),
    "e4_balanced_127_product_compare_unresolved_target": (
        "Compare known component carbon for the larger and smaller roof cassettes "
        "above the loading bay."
    ),
    "e4_balanced_128_traceability_compare_clarification_required": (
        "Compare the completeness of the quantity-factor-source evidence chains "
        "for those two panels."
    ),
    "e4_balanced_134_process_rank_incomplete_path": (
        "For the selected BIM component, determine whether process-energy records "
        "can be attributed to it and, if so, rank them by kgCO2e from highest to lowest."
    ),
    "e4_balanced_136_traceability_rank_clarification_required": (
        "Rank the carbon-evidence chains for that panel by traced kgCO2e from "
        "highest to lowest."
    ),
    "e4_balanced_140_traceability_compare_unresolved_target": (
        "Compare quantity-factor-source evidence-chain completeness for the larger "
        "and smaller roof cassettes above the loading bay."
    ),
    "e4_balanced_146_traceability_rank_unresolved_target": (
        "Rank carbon-evidence chains for the roof cassette above the loading bay by "
        "traced kgCO2e from highest to lowest."
    ),
}

NATURAL_SEARCH_PHRASES = {
    "e4_balanced_094_process_compare_unresolved_target": (
        "two coating-line records from the north side of the factory"
    ),
    "e4_balanced_125_material_rank_unresolved_target": (
        "vacuum-insulated panel above the loading bay"
    ),
    "e4_balanced_127_product_compare_unresolved_target": (
        "larger and smaller roof cassettes above the loading bay"
    ),
    "e4_balanced_140_traceability_compare_unresolved_target": (
        "larger and smaller roof cassettes above the loading bay"
    ),
    "e4_balanced_146_traceability_rank_unresolved_target": (
        "roof cassette above the loading bay"
    ),
}

AMBIGUOUS_TARGETS = {
    "e4_balanced_110_process_compare_clarification_required": (
        "that factory-energy record"
    ),
    "e4_balanced_124_process_rank_clarification_required": "that module",
    "e4_balanced_128_traceability_compare_clarification_required": "those two panels",
    "e4_balanced_136_traceability_rank_clarification_required": "that panel",
}

FORBIDDEN_USER_LABELS = (
    "currently indicated in the chat",
    "NC-01",
    "NC-02",
    "VIP-X",
    "RC-X1",
    "RC-X2",
)


def apply_round2_revision(case: Dict[str, Any], review_note: str = "") -> Dict[str, Any]:
    revised = copy.deepcopy(case)
    case_id = str(revised.get("case_id") or "")
    new_question = ROUND2_REVISED_QUESTIONS.get(case_id)
    if not new_question:
        return revised

    old_question = str(revised.get("question") or "")
    revised["question"] = new_question
    filters = copy.deepcopy(revised.get("filters") or {})

    phrase = NATURAL_SEARCH_PHRASES.get(case_id)
    if phrase:
        filters["input_mode"] = "search_query"
        filters["natural_search_phrase"] = phrase
        filters["reference_mode"] = "entity_reference"

    ambiguous_target = AMBIGUOUS_TARGETS.get(case_id)
    if ambiguous_target:
        filters["input_mode"] = "ambiguous_dialogue"
        filters["ambiguous_target"] = ambiguous_target
        filters.pop("natural_search_phrase", None)
        filters.pop("reference_mode", None)

    selected = [str(value) for value in revised.get("selected_component_ids") or []]
    if selected:
        filters["input_mode"] = "bim_selection"
        filters["selected_count"] = len(selected)
        revised["scope"] = "selected_component"
    revised["filters"] = filters

    revision = dict(revised.get("question_revision") or {})
    revision["human_review_round_2"] = {
        "decision": "REVISE",
        "note": review_note,
        "question_before_human_revision": old_question,
    }
    revision["policy"] = "human_reviewed_natural_question_v4"
    revised["question_revision"] = revision
    return revised


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_round2_revised_benchmark(
    source_jsonl: Path,
    review_json: Path,
    out_root: Path,
) -> Dict[str, Any]:
    source_cases = _read_jsonl(source_jsonl)
    review = json.loads(review_json.read_text(encoding="utf-8"))
    revise_rows = review.get("reviseRows") or []
    notes = {
        str(row.get("case_id") or ""): str(row.get("review_notes") or "")
        for row in revise_rows
    }
    if set(notes) != set(ROUND2_REVISED_QUESTIONS):
        unmapped = sorted(set(notes) - set(ROUND2_REVISED_QUESTIONS))
        unreviewed = sorted(set(ROUND2_REVISED_QUESTIONS) - set(notes))
        raise ValueError(
            "Round-2 revision map mismatch; "
            f"unmapped_review={unmapped}, unreviewed_map={unreviewed}"
        )

    revised_cases = [
        apply_round2_revision(case, notes.get(str(case.get("case_id") or ""), ""))
        for case in source_cases
    ]
    source_by_id = {str(case.get("case_id") or ""): case for case in source_cases}
    changed = [
        case
        for case in revised_cases
        if source_by_id[str(case.get("case_id") or "")].get("question")
        != case.get("question")
    ]

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"human_revised_{timestamp}_v4"
    output_dir = out_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_path = output_dir / "e4_balanced_benchmark_150_human_revised_v4.jsonl"
    benchmark_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in revised_cases)
        + "\n",
        encoding="utf-8",
    )

    review_csv = output_dir / "e4_round2_revision_round3.csv"
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
        for case in revised_cases:
            case_id = str(case.get("case_id") or "")
            expected = case.get("expected") or {}
            writer.writerow(
                {
                    "case_id": case_id,
                    "perspective": expected.get("perspective", ""),
                    "operation": expected.get("operation", ""),
                    "status": expected.get("status", ""),
                    "original_question": source_by_id[case_id].get("question", ""),
                    "repaired_question": case.get("question", ""),
                    "operation_semantics_valid": validate_operation_semantics(case),
                    "answer_contains": "; ".join(
                        expected.get("answer_contains") or []
                    ),
                    "human_review": "",
                    "review_notes": notes.get(case_id, ""),
                }
            )

    invalid = [
        str(case.get("case_id") or "")
        for case in changed
        if not validate_operation_semantics(case)
    ]
    hidden_filters = [
        str(case.get("case_id") or "")
        for case in revised_cases
        if any(key in (case.get("filters") or {}) for key in ("target_id", "target_hints"))
    ]
    forbidden = [
        str(case.get("case_id") or "")
        for case in changed
        if any(
            label.casefold() in str(case.get("question") or "").casefold()
            for label in FORBIDDEN_USER_LABELS
        )
    ]
    status_counts = dict(
        Counter(
            str((case.get("expected") or {}).get("status") or "")
            for case in revised_cases
        )
    )
    summary = {
        "run_id": run_id,
        "version_label": "E4 QA Benchmark V4",
        "workbook_title": "E4 QA Benchmark V4 - Human Review Round 3",
        "source_jsonl": str(source_jsonl),
        "review_json": str(review_json),
        "output_dir": str(output_dir),
        "case_count": len(revised_cases),
        "changed_question_count": len(changed),
        "round2_pass_count": int((review.get("counts") or {}).get("PASS") or 0),
        "round2_revise_count": int((review.get("counts") or {}).get("REVISE") or 0),
        "operation_semantic_violation_count": len(invalid),
        "operation_semantic_violation_case_ids": invalid,
        "hidden_filter_leak_count": len(hidden_filters),
        "hidden_filter_leak_case_ids": hidden_filters,
        "forbidden_user_label_count": len(forbidden),
        "forbidden_user_label_case_ids": forbidden,
        "duplicate_question_count": len(revised_cases)
        - len({str(case.get("question") or "").casefold() for case in revised_cases}),
        "status_counts": status_counts,
        "requires_human_review": True,
        "paper_ready": False,
        "benchmark_jsonl": str(benchmark_path),
        "review_csv": str(review_csv),
    }
    summary_path = output_dir / "e4_round2_revision_round3_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_root / "latest_e4_round2_revision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the second E4 human review.")
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
    summary = build_round2_revised_benchmark(
        args.source_jsonl, args.review_json, args.out_root
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
