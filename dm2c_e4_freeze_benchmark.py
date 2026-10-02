#!/usr/bin/env python3
"""Validate and freeze the final human-reviewed E4 benchmark."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence

from dm2c_e4_benchmark_repair import validate_operation_semantics


EXPECTED_STATUS_COUNTS = {
    "executable": 90,
    "incomplete_path": 14,
    "empty_result": 16,
    "unresolved_target": 15,
    "clarification_required": 15,
}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_final_benchmark(source_jsonl: Path) -> Dict[str, Any]:
    cases = _read_jsonl(source_jsonl)
    case_ids = [str(case.get("case_id") or "") for case in cases]
    questions = [str(case.get("question") or "").strip().casefold() for case in cases]
    invalid_semantics = [
        case_id
        for case_id, case in zip(case_ids, cases)
        if not validate_operation_semantics(case)
    ]
    hidden_filters = [
        case_id
        for case_id, case in zip(case_ids, cases)
        if any(key in (case.get("filters") or {}) for key in ("target_id", "target_hints"))
    ]
    status_counts = dict(
        Counter(
            str((case.get("expected") or {}).get("status") or "") for case in cases
        )
    )
    validation = {
        "case_count": len(cases),
        "unique_case_ids": len(set(case_ids)),
        "unique_questions": len(set(questions)),
        "duplicate_case_id_count": len(cases) - len(set(case_ids)),
        "duplicate_question_count": len(cases) - len(set(questions)),
        "operation_semantic_violation_count": len(invalid_semantics),
        "operation_semantic_violation_case_ids": invalid_semantics,
        "hidden_filter_leak_count": len(hidden_filters),
        "hidden_filter_leak_case_ids": hidden_filters,
        "status_counts": status_counts,
    }
    validation["validation_passed"] = (
        validation["case_count"] == 150
        and validation["unique_case_ids"] == 150
        and validation["unique_questions"] == 150
        and validation["operation_semantic_violation_count"] == 0
        and validation["hidden_filter_leak_count"] == 0
        and status_counts == EXPECTED_STATUS_COUNTS
    )
    return validation


def freeze_benchmark(
    source_jsonl: Path,
    source_summary: Path,
    out_root: Path,
    *,
    approval: str,
    version_label: str = "v6",
    reviewed_case_count: int = 3,
    review_decision_count: int = 3,
    review_rounds_completed: int = 5,
) -> Dict[str, Any]:
    validation = validate_final_benchmark(source_jsonl)
    if not validation["validation_passed"]:
        raise ValueError(f"E4 benchmark freeze validation failed: {validation}")

    source_metadata = json.loads(source_summary.read_text(encoding="utf-8"))
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"frozen_{timestamp}_{version_label}_human_pass"
    output_dir = (out_root / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    frozen_jsonl = output_dir / (
        f"e4_balanced_benchmark_150_human_reviewed_{version_label}.jsonl"
    )
    shutil.copy2(source_jsonl, frozen_jsonl)

    summary_path = output_dir / (
        f"e4_benchmark_summary_human_reviewed_{version_label}.json"
    )
    summary = {
        "run_id": run_id,
        "source_run_id": str(source_metadata.get("run_id") or ""),
        "frozen_at": time.time(),
        "paper_ready_benchmark": True,
        "human_review": {
            "status": "passed_all",
            "decision": f"freeze_current_{version_label}_questions",
            "review_rounds_completed": review_rounds_completed,
            "final_reviewed_case_count": reviewed_case_count,
            "final_pass_count": review_decision_count,
            "final_confirmation": approval,
            "requires_question_rewrite": False,
        },
        "source": {
            "benchmark_jsonl": str(source_jsonl.resolve()),
            "summary_json": str(source_summary.resolve()),
        },
        "outputs": {
            "balanced_jsonl": str(frozen_jsonl),
            "summary_json": str(summary_path),
        },
        "validation": validation,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    latest_path = out_root / "latest_e4_benchmark_summary.json"
    latest_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze a human-approved E4 benchmark.")
    parser.add_argument("--source-jsonl", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("outputs/research_experiments/e4_benchmark"),
    )
    parser.add_argument("--approval", default="user_message_all_three_pass")
    parser.add_argument("--version-label", default="v6")
    parser.add_argument("--reviewed-case-count", type=int, default=3)
    parser.add_argument("--review-decision-count", type=int, default=3)
    parser.add_argument("--review-rounds-completed", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    summary = freeze_benchmark(
        args.source_jsonl,
        args.source_summary,
        args.out_root,
        approval=args.approval,
        version_label=args.version_label,
        reviewed_case_count=args.reviewed_case_count,
        review_decision_count=args.review_decision_count,
        review_rounds_completed=args.review_rounds_completed,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
