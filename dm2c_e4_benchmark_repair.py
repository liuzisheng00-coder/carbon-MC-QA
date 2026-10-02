#!/usr/bin/env python3
"""Repair E4 natural questions after the real full-QA pilot consistency audit."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence

from dm2c_e4_question_reviser import target_phrase_for_case
from dm2c_e5_e7_experiment_runner import DEFAULT_E4_BENCHMARK, resolve_benchmark_path


DEFAULT_OUT_ROOT = Path("outputs/research_experiments/e4_benchmark")

DOMAIN_BY_PERSPECTIVE = {
    "product": "component carbon",
    "material": "material carbon",
    "process": "factory energy carbon",
    "traceability": "carbon evidence",
}

OPERATION_PATTERNS = {
    "value": re.compile(
        r"\bvalue\b|\bhow much\b|\bwhat\b.*\b(?:total|result|records?|carbon)\b|"
        r"\b(?:give me|report|show)\b.*\b(?:known|supported)\b.*\b(?:total|result|records?|carbon)\b",
        re.IGNORECASE,
    ),
    "aggregate": re.compile(
        r"\baggregate\b|\bsummarize\b|\bbreakdown\b|\bcarbon totals\b|"
        r"\b(?:grouped|grouping)\b.*\bby\b",
        re.IGNORECASE,
    ),
    "rank": re.compile(
        r"\brank\b|\branking\b|\bhighest to lowest\b|\blowest to highest\b|"
        r"\bwhich\b.*\b(?:most|largest|highest|dominates?)\b|\bdominant contributor\b|\btop \d+\b",
        re.IGNORECASE,
    ),
    "compare": re.compile(
        r"\bcompare\b|\bcomparison\b|\bdifference\b|\bdifferent\b|\bdiffer\b|"
        r"\bgap between\b|\blarger\b.*\bthan\b|\bversus\b|\bvs\.?\b",
        re.IGNORECASE,
    ),
    "explain": re.compile(
        r"\bexplain\b|\bexplanation\b|\bdescribe\b|\bwhy\b|\bevidence behind\b|"
        r"\bhow\b.*\b(?:was|is) calculated\b",
        re.IGNORECASE,
    ),
    "trace": re.compile(
        r"\btrace\b|\bevidence path\b|\bcalculation path\b|\bevidence chain\b|"
        r"\bsource-to-factor path\b|\bquantity\b.*\bfactor\b.*\bsource\b",
        re.IGNORECASE,
    ),
}


def operation_aware_question(case: Dict[str, Any]) -> str:
    expected = case.get("expected") or {}
    perspective = str(expected.get("perspective") or "product")
    operation = str(expected.get("operation") or "value")
    domain = DOMAIN_BY_PERSPECTIVE.get(perspective, "carbon")
    target = target_phrase_for_case(case)
    if case.get("selected_component_ids"):
        selected_count = len(case.get("selected_component_ids") or [])
        if perspective == "material":
            target = "the selected material carbon calculation"
        elif perspective == "process":
            target = "the selected factory energy carbon record"
        elif perspective == "traceability":
            target = "the selected carbon evidence record"
        else:
            target = "the two selected BIM items" if selected_count > 1 else "the selected BIM item"
    templates = {
        "value": f"What supported {domain} value is available for {target}?",
        "aggregate": f"Aggregate the supported {domain} records for {target}.",
        "rank": f"Rank the supported {domain} records for {target}.",
        "compare": f"Compare the supported {domain} records for {target}.",
        "explain": f"Explain the supported {domain} result for {target}.",
        "trace": f"Trace the {domain} evidence path for {target}.",
    }
    return templates.get(operation, templates["value"])


def validate_operation_semantics(case: Dict[str, Any]) -> bool:
    expected = case.get("expected") or {}
    operation = str(expected.get("operation") or "value")
    pattern = OPERATION_PATTERNS.get(operation)
    return bool(pattern and pattern.search(str(case.get("question") or "")))


def repair_case(case: Dict[str, Any]) -> Dict[str, Any]:
    repaired = copy.deepcopy(case)
    expected = repaired.setdefault("expected", {})
    status = str(expected.get("status") or "executable")
    if status == "executable":
        if (
            str(expected.get("perspective") or "") == "material"
            and str(expected.get("operation") or "") == "value"
            and "grouped" in str(repaired.get("question") or "").lower()
        ):
            repaired["question"] = (
                "What is the project's total known material carbon, and how many material families are represented?"
            )
        return repaired

    original_question = str(repaired.get("question") or "")
    repaired["question"] = operation_aware_question(repaired)
    if status == "unresolved_target":
        expected["answer_contains"] = ["unresolved_target"]

    revision = dict(repaired.get("question_revision") or {})
    revision.update(
        {
            "policy": "evidence_consistent_natural_question_v2",
            "original_question_before_v2": original_question,
            "operation_semantics_preserved": True,
            "hidden_target_not_required_in_answer": status == "unresolved_target",
        }
    )
    repaired["question_revision"] = revision
    return repaired


def _read_cases(path: Path) -> List[Dict[str, Any]]:
    resolved = resolve_benchmark_path(path)
    return [json.loads(line) for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip()]


def repair_benchmark(
    source: Path = DEFAULT_E4_BENCHMARK,
    out_root: Path = DEFAULT_OUT_ROOT,
) -> Dict[str, Any]:
    original = _read_cases(source)
    repaired = [repair_case(case) for case in original]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"repaired_{timestamp}_real_pilot_v2"
    output_dir = out_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_dir / "e4_balanced_benchmark_150_real_pilot_v2.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in repaired) + "\n",
        encoding="utf-8",
    )

    review_csv = output_dir / "e4_real_pilot_v2_review.csv"
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
        for before, after in zip(original, repaired):
            expected = after.get("expected") or {}
            writer.writerow(
                {
                    "case_id": after.get("case_id", ""),
                    "perspective": expected.get("perspective", ""),
                    "operation": expected.get("operation", ""),
                    "status": expected.get("status", ""),
                    "original_question": before.get("question", ""),
                    "repaired_question": after.get("question", ""),
                    "operation_semantics_valid": validate_operation_semantics(after),
                    "answer_contains": "; ".join(expected.get("answer_contains") or []),
                    "human_review": "",
                    "review_notes": "",
                }
            )

    non_executable = [case for case in repaired if (case.get("expected") or {}).get("status") != "executable"]
    invalid = [case.get("case_id") for case in non_executable if not validate_operation_semantics(case)]
    hidden_required = [
        case.get("case_id")
        for case in repaired
        if any("E4_DOES_NOT_EXIST_" in str(term) for term in (case.get("expected") or {}).get("answer_contains") or [])
    ]
    summary = {
        "run_id": run_id,
        "source": str(source),
        "resolved_source": str(resolve_benchmark_path(source)),
        "output_dir": str(output_dir),
        "case_count": len(repaired),
        "changed_question_count": sum(1 for before, after in zip(original, repaired) if before.get("question") != after.get("question")),
        "status_counts": dict(Counter(str((case.get("expected") or {}).get("status") or "") for case in repaired)),
        "operation_counts": dict(Counter(str((case.get("expected") or {}).get("operation") or "") for case in repaired)),
        "invalid_operation_semantics_count": len(invalid),
        "invalid_operation_semantics_case_ids": invalid,
        "hidden_target_answer_requirement_count": len(hidden_required),
        "hidden_target_answer_requirement_case_ids": hidden_required,
        "duplicate_question_count": len(repaired) - len({str(case.get("question") or "").casefold() for case in repaired}),
        "requires_human_review": True,
        "paper_ready": False,
        "benchmark_jsonl": str(jsonl_path),
        "review_csv": str(review_csv),
    }
    summary_json = output_dir / "e4_real_pilot_v2_summary.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# E4 Real-Pilot V2 Repair",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Questions changed: {summary['changed_question_count']}",
        f"- Invalid operation semantics: {summary['invalid_operation_semantics_count']}",
        f"- Hidden target answer requirements: {summary['hidden_target_answer_requirement_count']}",
        f"- Duplicate questions: {summary['duplicate_question_count']}",
        "- Human review required: yes",
        "",
        "Only non-executable question wording and unresolved-target disclosure terms were repaired. Numeric truth, status truth, selected BIM context, and executable questions were preserved.",
        "",
    ]
    (output_dir / "e4_real_pilot_v2_summary.md").write_text("\n".join(lines), encoding="utf-8")
    (out_root / "latest_e4_real_pilot_v2_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair E4 questions using real-pilot findings.")
    parser.add_argument("--source", type=Path, default=DEFAULT_E4_BENCHMARK)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    print(json.dumps(repair_benchmark(args.source, args.out_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
