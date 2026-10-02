#!/usr/bin/env python3
"""Migrate human-reviewed V7 wording onto M2-aligned deterministic E4 truth."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an M2-aligned E4 V8 migration candidate.")
    parser.add_argument("--reviewed-v7", type=Path, required=True)
    parser.add_argument("--generated-m2", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    old_rows = load_jsonl(args.reviewed_v7)
    new_rows = load_jsonl(args.generated_m2)
    old_by_id = {row["case_id"]: row for row in old_rows}
    new_by_id = {row["case_id"]: row for row in new_rows}
    if set(old_by_id) != set(new_by_id):
        raise RuntimeError("V7 and M2-generated case IDs differ; automatic migration is not safe.")

    migrated: List[Dict[str, Any]] = []
    review_items: List[Dict[str, Any]] = []
    expected_changed = 0
    selected_changed = 0
    template_changed = 0
    filters_changed = 0
    for case_id in [row["case_id"] for row in old_rows]:
        old = old_by_id[case_id]
        new = new_by_id[case_id]
        old_cell = tuple(old["expected"].get(key) for key in ("perspective", "operation", "status"))
        new_cell = tuple(new["expected"].get(key) for key in ("perspective", "operation", "status"))
        if old_cell != new_cell:
            raise RuntimeError(f"Coverage cell changed for {case_id}: {old_cell} -> {new_cell}")

        changes = []
        if compact(old.get("expected")) != compact(new.get("expected")):
            changes.append("expected")
            expected_changed += 1
        if compact(old.get("selected_component_ids")) != compact(new.get("selected_component_ids")):
            changes.append("selected_target")
            selected_changed += 1
        if old.get("question_template") != new.get("question_template"):
            changes.append("question_template")
            template_changed += 1
        if compact(old.get("filters")) != compact(new.get("filters")):
            changes.append("filters")
            filters_changed += 1

        migrated_case = dict(new)
        migrated_case["question"] = old["question"]
        migrated_case["filters"] = dict(old.get("filters") or {})
        migrated_expected = dict(new.get("expected") or {})
        old_expected = dict(old.get("expected") or {})
        if "answer_contains" in old_expected:
            migrated_expected["answer_contains"] = list(old_expected["answer_contains"] or [])
        else:
            migrated_expected.pop("answer_contains", None)
        migrated_case["expected"] = migrated_expected
        migrated_case["rewrite"] = dict(old.get("rewrite") or {})
        revision = dict(old.get("question_revision") or {})
        revision["m2_v8_migration"] = {
            "source_reviewed_version": "V7",
            "question_wording_reused": True,
            "deterministic_fields_rebuilt": [
                "expected",
                "selected_component_ids",
                "scope",
                "question_template",
                "generation",
            ],
            "human_semantic_filters_reused": True,
            "human_answer_constraints_reused": True,
            "change_categories": changes,
            "requires_targeted_human_recheck": (
                "selected_target" in changes or "question_template" in changes
            ),
        }
        migrated_case["question_revision"] = revision
        generation = dict(new.get("generation") or {})
        generation["migration"] = "M2-aligned deterministic truth with V7 human-reviewed wording"
        migrated_case["generation"] = generation
        migrated.append(migrated_case)

        if revision["m2_v8_migration"]["requires_targeted_human_recheck"]:
            review_items.append(
                {
                    "case_id": case_id,
                    "question": old["question"],
                    "perspective": new_cell[0],
                    "operation": new_cell[1],
                    "status": new_cell[2],
                    "change_categories": "; ".join(changes),
                    "old_selected_ids": "; ".join(old.get("selected_component_ids") or []),
                    "new_selected_ids": "; ".join(new.get("selected_component_ids") or []),
                    "old_question_template": old.get("question_template", ""),
                    "new_question_template": new.get("question_template", ""),
                    "old_expected": compact(old.get("expected")),
                    "new_expected": compact(new.get("expected")),
                    "review_status": "",
                    "reviewer_notes": "",
                    "revised_question": "",
                }
            )

    questions = [str(row["question"]).strip().casefold() for row in migrated]
    duplicate_questions = sorted({question for question in questions if questions.count(question) > 1})
    args.out_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = args.out_dir / "e4_balanced_benchmark_150_m2_aligned_candidate_v8.jsonl"
    candidate_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in migrated) + "\n",
        encoding="utf-8",
    )
    review_data_path = args.out_dir / "v8_targeted_review_data.json"
    review_data_path.write_text(
        json.dumps({"review_items": review_items, "all_cases": migrated}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "source_reviewed_v7": str(args.reviewed_v7),
        "source_reviewed_v7_sha256": sha256(args.reviewed_v7),
        "source_generated_m2": str(args.generated_m2),
        "source_generated_m2_sha256": sha256(args.generated_m2),
        "candidate": str(candidate_path),
        "candidate_sha256": sha256(candidate_path),
        "case_count": len(migrated),
        "coverage_cell_changes": 0,
        "expected_changed_cases": expected_changed,
        "selected_target_changed_cases": selected_changed,
        "question_template_changed_cases": template_changed,
        "filter_changed_cases": filters_changed,
        "targeted_human_recheck_cases": len(review_items),
        "duplicate_question_count": len(duplicate_questions),
        "duplicate_questions": duplicate_questions,
        "review_data": str(review_data_path),
        "paper_ready": False,
        "reason_not_paper_ready": "Targeted human recheck and M2-aligned replay are pending.",
    }
    summary_path = args.out_dir / "v8_migration_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
