#!/usr/bin/env python3
"""Apply the user's third-round E4 review and produce a V5 review candidate."""

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


ROUND3_REVISED_QUESTIONS: Dict[str, str] = {
    "e4_balanced_094_process_compare_unresolved_target": (
        "Compare factory-energy carbon for coating records associated with the "
        "wall-panel batch and the roof-cassette batch."
    ),
    "e4_balanced_124_process_rank_clarification_required": (
        "For that module, rank cutting, welding, and coating factory-energy "
        "records by kgCO2e from highest to lowest."
    ),
    "e4_balanced_127_product_compare_unresolved_target": (
        "Compare known component carbon for the roof cassette over the loading bay "
        "and the roof cassette over the stair core."
    ),
    "e4_balanced_134_process_rank_incomplete_path": (
        "Rank the selected BIM component's cutting, welding, and coating carbon "
        "contributions by kgCO2e from highest to lowest."
    ),
    "e4_balanced_140_traceability_compare_unresolved_target": (
        "Compare quantity-factor-source evidence-chain completeness for the roof "
        "cassette over the loading bay and the roof cassette over the stair core."
    ),
    "e4_balanced_146_traceability_rank_unresolved_target": (
        "Rank the material-carbon records traced to the roof cassette over the "
        "loading bay by kgCO2e from highest to lowest, and report each record's "
        "evidence-chain completeness."
    ),
}

NATURAL_SEARCH_PHRASES = {
    "e4_balanced_094_process_compare_unresolved_target": (
        "coating records associated with wall-panel and roof-cassette batches"
    ),
    "e4_balanced_127_product_compare_unresolved_target": (
        "roof cassette over the loading bay and roof cassette over the stair core"
    ),
    "e4_balanced_140_traceability_compare_unresolved_target": (
        "roof cassette over the loading bay and roof cassette over the stair core"
    ),
    "e4_balanced_146_traceability_rank_unresolved_target": (
        "roof cassette over the loading bay"
    ),
}

AMBIGUOUS_TARGETS = {
    "e4_balanced_124_process_rank_clarification_required": "that module",
}

FORBIDDEN_ROUND3_PHRASES = (
    "north side of the factory",
    "larger and smaller roof cassettes",
    "rank carbon-evidence chains",
    "process-energy records can be attributed to it",
)


def apply_round3_revision(case: Dict[str, Any], review_note: str = "") -> Dict[str, Any]:
    revised = copy.deepcopy(case)
    case_id = str(revised.get("case_id") or "")
    new_question = ROUND3_REVISED_QUESTIONS.get(case_id)
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
    revision["human_review_round_3"] = {
        "decision": "REVISE",
        "note": review_note,
        "question_before_human_revision": old_question,
    }
    revision["policy"] = "human_reviewed_natural_question_v5"
    revised["question_revision"] = revision
    return revised


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_round3_revised_benchmark(
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
    if set(notes) != set(ROUND3_REVISED_QUESTIONS):
        unmapped = sorted(set(notes) - set(ROUND3_REVISED_QUESTIONS))
        unreviewed = sorted(set(ROUND3_REVISED_QUESTIONS) - set(notes))
        raise ValueError(
            "Round-3 revision map mismatch; "
            f"unmapped_review={unmapped}, unreviewed_map={unreviewed}"
        )

    revised_cases = [
        apply_round3_revision(case, notes.get(str(case.get("case_id") or ""), ""))
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
    run_id = f"human_revised_{timestamp}_v5"
    output_dir = out_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_path = output_dir / "e4_balanced_benchmark_150_human_revised_v5.jsonl"
    benchmark_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in revised_cases)
        + "\n",
        encoding="utf-8",
    )

    review_csv = output_dir / "e4_round3_revision_round4.csv"
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
                    "answer_contains": "; ".join(expected.get("answer_contains") or []),
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
            phrase.casefold() in str(case.get("question") or "").casefold()
            for phrase in FORBIDDEN_ROUND3_PHRASES
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
        "version_label": "E4 QA Benchmark V5",
        "workbook_title": "E4 QA Benchmark V5 - Human Review Round 4",
        "source_jsonl": str(source_jsonl),
        "review_json": str(review_json),
        "output_dir": str(output_dir),
        "case_count": len(revised_cases),
        "changed_question_count": len(changed),
        "round3_pass_count": int((review.get("counts") or {}).get("PASS") or 0),
        "round3_revise_count": int((review.get("counts") or {}).get("REVISE") or 0),
        "operation_semantic_violation_count": len(invalid),
        "operation_semantic_violation_case_ids": invalid,
        "hidden_filter_leak_count": len(hidden_filters),
        "hidden_filter_leak_case_ids": hidden_filters,
        "forbidden_phrase_count": len(forbidden),
        "forbidden_phrase_case_ids": forbidden,
        "duplicate_question_count": len(revised_cases)
        - len({str(case.get("question") or "").casefold() for case in revised_cases}),
        "status_counts": status_counts,
        "requires_human_review": True,
        "paper_ready": False,
        "benchmark_jsonl": str(benchmark_path),
        "review_csv": str(review_csv),
    }
    summary_path = output_dir / "e4_round3_revision_round4_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_root / "latest_e4_round3_revision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the third E4 human review.")
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
    summary = build_round3_revised_benchmark(
        args.source_jsonl, args.review_json, args.out_root
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
