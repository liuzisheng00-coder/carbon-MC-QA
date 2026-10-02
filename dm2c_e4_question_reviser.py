#!/usr/bin/env python3
"""
Revise E4 benchmark questions into natural user-facing wording.

This script intentionally changes only the visible question text and adds
revision metadata. KG-derived expected fields remain immutable.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


EXPLICIT_PERSPECTIVE_RE = re.compile(
    r"\b(from|in|using)\s+(the\s+|a\s+)?"
    r"(product|material|process|traceability)\s+"
    r"(perspective|viewpoint|side|angle|standpoint)\b"
    r"|\b(product|material|process|traceability)[-\s]+"
    r"(perspective|viewpoint|side|angle|standpoint)\b",
    re.IGNORECASE,
)

INTERNAL_ID_RE = re.compile(
    r"\bGlobalId\b"
    r"|E4_DOES_NOT_EXIST_\d+"
    r"|E4-no-result-\d+"
    r"|\b(?:component|carbon_emission|driver|consumption_quantity|factor|material|module|energy_carrier):[A-Za-z0-9_$:.\-]+",
    re.IGNORECASE,
)

CLARIFICATION_INSTRUCTION_RE = re.compile(
    r"\bask\s+(?:me\s+)?(?:for\s+)?clarification\b"
    r"|\brequest\s+clarification\b"
    r"|\bif\s+(?:anything\s+is\s+unclear|needed|clarification\s+is\s+needed)\b",
    re.IGNORECASE,
)

SPECIFIC_COMPONENT_LABEL_RE = re.compile(
    r"\b\d{2,4}x\d{2,4}x\d{1,4}(?:mm)?\b|:[A-Za-z0-9]{2,}:?\d{4,}",
    re.IGNORECASE,
)


USER_ROLE_BY_PERSPECTIVE = {
    "product": "project_manager",
    "material": "carbon_estimator",
    "process": "factory_manager",
    "traceability": "carbon_auditor",
}


def has_explicit_perspective_prompt(text: str) -> bool:
    return bool(EXPLICIT_PERSPECTIVE_RE.search(text or ""))


def has_user_facing_internal_id(text: str) -> bool:
    return bool(INTERNAL_ID_RE.search(text or ""))


def has_clarification_instruction(text: str) -> bool:
    return bool(CLARIFICATION_INSTRUCTION_RE.search(text or ""))


def has_specific_component_label(text: str) -> bool:
    return bool(SPECIFIC_COMPONENT_LABEL_RE.search(text or ""))


def expected(case: Dict[str, Any]) -> Dict[str, Any]:
    return dict(case.get("expected", {}) or {})


def text_for_executable(perspective: str, operation: str, has_selection: bool) -> str:
    if perspective == "product":
        if operation == "value":
            return "What is the project's known carbon total, split into materials and factory energy?"
        if operation == "aggregate":
            return "Summarize the project's known carbon by material contribution and factory energy contribution."
        if operation == "rank":
            return "Which component contributes the most known carbon in this project?"
        if operation == "compare":
            return "Compare the known carbon of the two selected BIM items."
        if operation == "explain":
            return "For the selected BIM item, explain its known carbon contribution and supporting evidence."
        return "For the selected BIM item, trace how its known carbon contribution is calculated."

    if perspective == "material":
        if operation == "value":
            return "How much material carbon is known in the project, grouped by material family?"
        if operation == "aggregate":
            return "Aggregate the known material carbon by material family for the current project."
        if operation == "rank":
            return "Which material family contributes the most calculated material carbon?"
        if operation == "compare":
            return "Compare the calculated material carbon between the two main material groups."
        if operation == "explain":
            return "Explain the calculation evidence for the largest steel material record."
        return "Trace the quantity, factor, material, and component evidence for the largest steel material record."

    if perspective == "process":
        if operation == "value":
            return "How much known factory energy carbon is recorded, and how many energy records support it?"
        if operation == "aggregate":
            return "Aggregate the recorded factory energy carbon across the available energy records."
        if operation == "rank":
            return "Which factory energy record has the largest carbon value?"
        if operation == "compare":
            return "Compare the factory energy carbon from electricity and diesel."
        if operation == "explain":
            return "Explain the largest factory energy carbon record, including quantity and emission factor evidence."
        return "Trace the evidence chain for the largest factory energy carbon record."

    if operation == "value":
        return "Show the known carbon records and evidence coverage for the selected project scope."
    if operation == "aggregate":
        return "Summarize the evidence-backed carbon records across the selected project scope."
    if operation == "rank":
        return "Which carbon record has the largest value, and what evidence supports it?"
    if operation == "compare":
        return "Compare the evidence chains for the two selected carbon records."
    if operation == "explain":
        return "Explain the evidence chain behind the selected carbon record."
    return "Trace the selected carbon record back to its quantity, factor, source item, and target."


def text_for_unanswerable(status: str, perspective: str, operation: str, has_selection: bool) -> str:
    if status == "empty_result":
        if perspective == "process":
            return "Do we have factory energy carbon records for the coating process I searched for?"
        if perspective == "material":
            return "Do we have material carbon records for the insulation board I searched for?"
        if perspective == "traceability":
            return "Can you trace any carbon evidence for the roof cassette I searched for?"
        return "Do we have any carbon records for the roof cassette I searched for?"

    if status == "unresolved_target":
        if perspective == "process":
            return "Can you find carbon information for the factory record I searched for?"
        if perspective == "material":
            return "Can you find carbon information for the material item I searched for?"
        if perspective == "traceability":
            return "Can you compare the carbon evidence for the item I searched for?"
        return "Can you find carbon information for the component I searched for?"

    if status == "incomplete_path":
        if perspective == "process":
            return "Why can't the selected factory energy carbon record be fully calculated?"
        if perspective == "material":
            return "What is missing from the selected material carbon calculation?"
        if perspective == "traceability":
            return "Trace the selected item's carbon calculation path and identify what is missing."
        return "Why can't the selected BIM item's carbon path be fully calculated?"

    if perspective == "process":
        if operation == "rank":
            return "Which factory energy record is the biggest one?"
        if operation in {"explain", "trace"}:
            return "Show the evidence for that factory energy record."
        return "What is the carbon for that factory energy use?"
    if perspective == "material":
        if operation == "compare":
            return "Compare the carbon for the steel items."
        if operation in {"explain", "trace"}:
            return "Show the carbon evidence for the steel item."
        return "What is the carbon for the steel item?"
    if perspective == "traceability":
        if operation in {"explain", "trace"}:
            return "Show the calculation path for that panel."
        if operation == "rank":
            return "Rank the carbon records for the selected panel."
        return "What evidence do we have for that selected panel?"
    if operation in {"explain", "trace"}:
        return "Show the carbon path for that module component."
    return "What is the carbon for that module component?"


def natural_question_for_case(case: Dict[str, Any]) -> str:
    exp = expected(case)
    perspective = str(exp.get("perspective") or "product")
    operation = str(exp.get("operation") or "value")
    status = str(exp.get("status") or "executable")
    has_selection = bool(case.get("selected_component_ids"))

    if status == "executable":
        return text_for_executable(perspective, operation, has_selection)
    return text_for_unanswerable(status, perspective, operation, has_selection)


def operation_phrase(operation: str) -> str:
    return {
        "value": "a supported carbon value",
        "aggregate": "a supported aggregate",
        "rank": "a carbon ranking",
        "compare": "a carbon comparison",
        "explain": "an evidence-backed explanation",
        "trace": "a calculation trace",
    }.get(operation, "a carbon answer")


def domain_phrase(perspective: str) -> str:
    return {
        "product": "component carbon record",
        "material": "material carbon record",
        "process": "factory energy carbon record",
        "traceability": "carbon evidence chain",
    }.get(perspective, "carbon record")


def target_phrase_for_case(case: Dict[str, Any]) -> str:
    exp = expected(case)
    status = str(exp.get("status") or "")
    perspective = str(exp.get("perspective") or "")
    if case.get("selected_component_ids"):
        count = len(case.get("selected_component_ids") or [])
        return "the two selected BIM items" if count > 1 else "the selected BIM item"
    if status == "empty_result":
        if perspective == "process":
            return "the coating process I searched for"
        if perspective == "material":
            return "the insulation board I searched for"
        return "the roof cassette I searched for"
    if status == "unresolved_target":
        if perspective == "process":
            return "the factory record I searched for"
        if perspective == "material":
            return "the material item I searched for"
        if perspective == "traceability":
            return "the item I searched for"
        return "the component I searched for"
    if status == "clarification_required":
        ambiguous = str((exp.get("summary") or {}).get("ambiguous_target") or "")
        if "steel" in ambiguous:
            return "the steel item"
        if "panel" in ambiguous:
            return "that panel"
        if "energy" in ambiguous:
            return "that factory energy use"
        if "module" in ambiguous:
            return "that module component"
        return "that item"
    if status == "incomplete_path":
        if perspective == "process":
            return "the selected factory energy carbon record"
        if perspective == "material":
            return "the selected material carbon calculation"
        return "the selected item's carbon calculation"
    return "the current project"


def executable_variants(case: Dict[str, Any]) -> List[str]:
    exp = expected(case)
    perspective = str(exp.get("perspective") or "product")
    operation = str(exp.get("operation") or "value")
    target = target_phrase_for_case(case)
    domain = domain_phrase(perspective)

    variants_by_key: Dict[Tuple[str, str], List[str]] = {
        ("product", "value"): [
            "What is the project's known carbon total, split into materials and factory energy?",
            "For the current project, what known carbon total should I report with material and factory energy separated?",
            "Give me the supported project carbon total and separate the material and factory energy parts.",
        ],
        ("product", "aggregate"): [
            "Summarize the project's known carbon by material contribution and factory energy contribution.",
            "Can you aggregate the supported project carbon records into material and factory energy totals?",
            "Show the project-level carbon breakdown across materials and factory energy.",
        ],
        ("product", "rank"): [
            "Which component contributes the most known carbon in this project?",
            "Which BIM item is the largest known-carbon contributor in the current model?",
            "Rank the project components by supported carbon and identify the top contributor.",
        ],
        ("product", "compare"): [
            "Compare the known carbon of the two selected BIM items.",
            "How different are the supported carbon totals for the two selected BIM items?",
            "Show the carbon comparison between the two selected BIM items.",
        ],
        ("product", "explain"): [
            "For the selected BIM item, explain its known carbon contribution and supporting evidence.",
            "Explain why the selected BIM item has its supported carbon value.",
            "Describe the evidence behind the selected BIM item's carbon contribution.",
        ],
        ("product", "trace"): [
            "For the selected BIM item, trace how its known carbon contribution is calculated.",
            "Trace the selected BIM item's carbon value back to the supporting records.",
            "Show the calculation path behind the selected BIM item's known carbon.",
        ],
        ("material", "value"): [
            "How much material carbon is known in the project, grouped by material family?",
            "What material carbon total is supported by the current records, and how many families does it cover?",
            "Give me the known material carbon result for the project and its material-family coverage.",
        ],
        ("material", "aggregate"): [
            "Aggregate the known material carbon by material family for the current project.",
            "Summarize the project material carbon across the available material families.",
            "Show the material-family carbon totals supported by the current records.",
        ],
        ("material", "rank"): [
            "Which material family contributes the most calculated material carbon?",
            "Rank the material families by supported material carbon and identify the largest one.",
            "Which material group is the dominant contributor to material carbon?",
        ],
        ("material", "compare"): [
            "Compare the calculated material carbon between the two main material groups.",
            "How do the two main material groups differ in supported material carbon?",
            "Show the material-carbon gap between the two main material groups.",
        ],
        ("material", "explain"): [
            "Explain the calculation evidence for the largest steel material record.",
            "Describe how the largest steel material carbon record was calculated.",
            "Show the quantity, factor, and material evidence behind the largest steel record.",
        ],
        ("material", "trace"): [
            "Trace the quantity, factor, material, and component evidence for the largest steel material record.",
            "Trace the largest steel material record back to its quantity and emission factor.",
            "Show the evidence chain for the largest steel material carbon record.",
        ],
        ("process", "value"): [
            "How much known factory energy carbon is recorded, and how many energy records support it?",
            "What factory energy carbon value is supported by the recorded energy data?",
            "Report the known factory energy carbon and the number of supporting energy records.",
        ],
        ("process", "aggregate"): [
            "Aggregate the recorded factory energy carbon across the available energy records.",
            "Summarize the factory energy carbon total from the recorded production energy data.",
            "Show the supported aggregate carbon for the factory energy records.",
        ],
        ("process", "rank"): [
            "Which factory energy record has the largest carbon value?",
            "Rank the factory energy records by supported carbon and identify the largest one.",
            "Which production energy record dominates the known process carbon?",
        ],
        ("process", "compare"): [
            "Compare the factory energy carbon from electricity and diesel.",
            "How much larger is electricity-related factory carbon than diesel-related factory carbon?",
            "Show the supported carbon comparison between electricity and diesel energy records.",
        ],
        ("process", "explain"): [
            "Explain the largest factory energy carbon record, including quantity and emission factor evidence.",
            "Describe how the largest factory energy carbon record was calculated.",
            "Show the quantity and factor evidence behind the largest production energy record.",
        ],
        ("process", "trace"): [
            "Trace the evidence chain for the largest factory energy carbon record.",
            "Trace the largest production energy carbon record back to its consumption quantity and factor.",
            "Show the source-to-factor path for the largest factory energy carbon record.",
        ],
        ("traceability", "value"): [
            "Show the known carbon records and evidence coverage for the selected project scope.",
            "What evidence-backed carbon value is available for the selected project scope?",
            "Report the supported carbon value together with the available evidence coverage.",
        ],
        ("traceability", "aggregate"): [
            "Summarize the evidence-backed carbon records across the selected project scope.",
            "Aggregate the carbon records that have traceable quantity and factor evidence.",
            "Show the supported carbon aggregate and the evidence coverage behind it.",
        ],
        ("traceability", "rank"): [
            "Which carbon record has the largest value, and what evidence supports it?",
            "Rank the traceable carbon records and show the evidence for the largest one.",
            "Which evidence-backed carbon record is the largest in the current model?",
        ],
        ("traceability", "compare"): [
            "Compare the evidence chains for the two selected carbon records.",
            "How do the two selected carbon evidence chains differ?",
            "Show the traceable evidence comparison for the selected carbon records.",
        ],
        ("traceability", "explain"): [
            "Explain the evidence chain behind the selected carbon record.",
            "Describe the quantity, factor, source, and target behind the selected carbon record.",
            "Show why the selected carbon record is supported by the available evidence.",
        ],
        ("traceability", "trace"): [
            "Trace the selected carbon record back to its quantity, factor, source item, and target.",
            "Show the full evidence path behind the selected carbon record.",
            "Trace the selected carbon result from target object to quantity and factor evidence.",
        ],
    }
    variants = variants_by_key.get((perspective, operation), [])
    if not variants:
        variants = [
            natural_question_for_case(case),
            f"What supported result is available for {target}?",
            f"Use the current records to provide {operation_phrase(operation)} for {target}.",
            f"Show the {domain} needed for {operation_phrase(operation)} on {target}.",
            f"Answer this {operation} request for {target} using only supported records.",
        ]
    return variants


def unanswerable_variants(case: Dict[str, Any]) -> List[str]:
    exp = expected(case)
    perspective = str(exp.get("perspective") or "product")
    operation = str(exp.get("operation") or "value")
    status = str(exp.get("status") or "")
    target = target_phrase_for_case(case)
    op_phrase = operation_phrase(operation)
    domain = domain_phrase(perspective)

    if status == "empty_result":
        return [
            natural_question_for_case(case),
            f"Is there any supported {domain} for {target}?",
            f"Can the current model produce {op_phrase} for {target}?",
            f"What records, if any, support {op_phrase} for {target}?",
            f"Does the project contain enough data to answer about {target}?",
            f"Can {target} be included in the requested carbon result?",
        ]
    if status == "unresolved_target":
        return [
            natural_question_for_case(case),
            f"Can the system identify {target} well enough to produce {op_phrase}?",
            f"Find the matching project item for {target} and report its carbon information.",
            f"Can you ground {target} to a known project object before answering?",
            f"What supported carbon result is available after searching for {target}?",
            f"Use the project records to resolve {target} before giving {op_phrase}.",
        ]
    if status == "incomplete_path":
        return [
            natural_question_for_case(case),
            f"What evidence is missing from {target}?",
            f"Why is {op_phrase} incomplete for {target}?",
            f"Show which quantity, factor, or allocation evidence is missing for {target}.",
            f"Can {target} be fully calculated from the available records?",
            f"Explain the gap that prevents a supported carbon result for {target}.",
        ]
    return [
        natural_question_for_case(case),
        f"Show {op_phrase} for {target}.",
        f"What carbon information is available for {target}?",
        f"Can you answer this {operation} request for {target}?",
        f"Use the selected context to answer for {target}.",
        f"What should be reported for {target} from the available project context?",
    ]


def question_variants_for_case(case: Dict[str, Any]) -> List[str]:
    status = str(expected(case).get("status") or "executable")
    variants = executable_variants(case) if status == "executable" else unanswerable_variants(case)
    out = []
    for text in variants:
        clean = " ".join(str(text or "").split())
        if clean and clean not in out:
            out.append(clean)
    return out or [natural_question_for_case(case)]


def detect_issue_flags(question: str) -> List[str]:
    flags = []
    if has_explicit_perspective_prompt(question):
        flags.append("explicit_perspective_prompt")
    if has_user_facing_internal_id(question):
        flags.append("internal_or_synthetic_id_visible")
    if has_clarification_instruction(question):
        flags.append("self_clarification_instruction")
    if has_specific_component_label(question):
        flags.append("specific_bim_label_visible")
    return flags


def ui_context_for_case(case: Dict[str, Any]) -> Dict[str, Any]:
    exp = expected(case)
    status = str(exp.get("status") or "")
    if case.get("selected_component_ids"):
        return {
            "mode": "bim_selection",
            "selected_count": len(case.get("selected_component_ids") or []),
        }
    if status in {"empty_result", "unresolved_target"}:
        return {"mode": "search_query", "target_visibility": "natural_search_term"}
    if status == "clarification_required":
        return {"mode": "ambiguous_dialogue", "target_visibility": "underspecified_phrase"}
    return {"mode": "project_overview"}


def extract_synthetic_empty_target(question: str) -> str:
    match = re.search(r"E4-no-result-\d+", question or "", flags=re.IGNORECASE)
    return match.group(0) if match else ""


def revision_filters_for_case(case: Dict[str, Any]) -> Dict[str, Any]:
    exp = expected(case)
    status = str(exp.get("status") or "")
    filters = dict(case.get("filters") or {})
    if case.get("selected_component_ids"):
        filters.setdefault("input_mode", "bim_selection")
        filters.setdefault("selected_count", len(case.get("selected_component_ids") or []))
    if status == "unresolved_target":
        target_id = str((exp.get("summary") or {}).get("target_id") or "")
        if target_id:
            filters["target_id"] = target_id
            filters["target_hints"] = [target_id]
        filters["input_mode"] = "search_query"
        filters.setdefault("natural_search_phrase", "the item I searched for")
    elif status == "empty_result":
        synthetic_target = extract_synthetic_empty_target(
            str(case.get("question") or case.get("question_template") or "")
        )
        if synthetic_target:
            filters["target_id"] = synthetic_target
            filters["target_hints"] = [synthetic_target]
        filters["input_mode"] = "search_query"
        filters.setdefault("natural_search_phrase", "the item I searched for")
    elif status == "clarification_required":
        ambiguous_target = str((exp.get("summary") or {}).get("ambiguous_target") or "")
        filters["input_mode"] = "ambiguous_dialogue"
        if ambiguous_target:
            filters["ambiguous_target"] = ambiguous_target
    return filters


def revise_case(case: Dict[str, Any]) -> Dict[str, Any]:
    revised = copy.deepcopy(case)
    original_question = str(case.get("question") or "")
    original_flags = detect_issue_flags(original_question)
    exp = expected(case)
    perspective = str(exp.get("perspective") or "")

    revised["question"] = natural_question_for_case(case)
    revised["filters"] = revision_filters_for_case(case)
    revised["question_revision"] = {
        "policy": "natural_user_question_v1",
        "original_question": original_question,
        "issue_flags": original_flags,
        "user_role": USER_ROLE_BY_PERSPECTIVE.get(perspective, "domain_user"),
        "ui_context": ui_context_for_case(case),
        "expected_fields_unchanged": True,
    }
    rewrite = dict(revised.get("rewrite", {}) or {})
    rewrite.update({"mode": "deterministic_naturalization", "status": "revised_natural"})
    revised["rewrite"] = rewrite
    return revised


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, cases: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")


def summarize_cases(cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    perspective_counts = Counter()
    operation_counts = Counter()
    status_counts = Counter()
    rewrite_counts = Counter()
    issue_counts = Counter()
    revised_issue_counts = Counter()
    for case in cases:
        exp = expected(case)
        perspective_counts[str(exp.get("perspective") or "")] += 1
        operation_counts[str(exp.get("operation") or "")] += 1
        status = str(exp.get("status") or "")
        status_counts[status] += 1
        rewrite_counts[str((case.get("rewrite") or {}).get("status") or "")] += 1
        for flag in (case.get("question_revision") or {}).get("issue_flags", []):
            issue_counts[flag] += 1
        for flag in detect_issue_flags(str(case.get("question") or "")):
            revised_issue_counts[flag] += 1
    return {
        "case_count": len(cases),
        "perspective_counts": dict(perspective_counts),
        "operation_counts": dict(operation_counts),
        "status_counts": dict(status_counts),
        "rewrite_counts": dict(rewrite_counts),
        "original_question_issue_counts": dict(issue_counts),
        "revised_question_issue_counts": dict(revised_issue_counts),
        "executable_count": status_counts.get("executable", 0),
        "unanswerable_count": len(cases) - status_counts.get("executable", 0),
    }


def write_review_csv(path: Path, cases: Sequence[Dict[str, Any]]) -> None:
    unanswerable = [case for case in cases if expected(case).get("status") != "executable"]
    executable = [case for case in cases if expected(case).get("status") == "executable"]
    review_cases = unanswerable + executable[:30]
    fields = [
        "case_id",
        "question",
        "original_question",
        "expected_status",
        "perspective",
        "operation",
        "difficulty",
        "scope",
        "issue_flags",
        "ui_context",
        "expected_summary",
        "expected_evidence_ids",
        "answer_contains",
        "selected_component_ids",
        "review_status",
        "reviewer",
        "reviewer_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in review_cases:
            exp = expected(case)
            revision = case.get("question_revision") or {}
            writer.writerow(
                {
                    "case_id": case.get("case_id"),
                    "question": case.get("question"),
                    "original_question": revision.get("original_question", ""),
                    "expected_status": exp.get("status", ""),
                    "perspective": exp.get("perspective", ""),
                    "operation": exp.get("operation", ""),
                    "difficulty": case.get("difficulty", ""),
                    "scope": case.get("scope", ""),
                    "issue_flags": "; ".join(revision.get("issue_flags", []) or []),
                    "ui_context": json.dumps(revision.get("ui_context", {}), ensure_ascii=False),
                    "expected_summary": json.dumps(exp.get("summary", {}), ensure_ascii=False),
                    "expected_evidence_ids": "; ".join(exp.get("evidence_ids", []) or []),
                    "answer_contains": "; ".join(exp.get("answer_contains", []) or []),
                    "selected_component_ids": "; ".join(case.get("selected_component_ids", []) or []),
                    "review_status": "pending",
                    "reviewer": "",
                    "reviewer_notes": "",
                }
            )


def write_summary_markdown(path: Path, summary: Dict[str, Any]) -> None:
    balanced = summary["balanced_summary"]
    matrix = summary["matrix_summary"]
    lines = [
        "# E4 Revised Question Benchmark",
        "",
        "This run revises only user-facing question wording. Expected values, statuses, evidence ids, and scoring metadata are copied from the source benchmark.",
        "",
        "## Balanced Benchmark",
        f"- Cases: {balanced['case_count']}",
        f"- Executable: {balanced['executable_count']}",
        f"- Unanswerable: {balanced['unanswerable_count']}",
        f"- Original issue counts: {balanced['original_question_issue_counts']}",
        f"- Revised issue counts: {balanced['revised_question_issue_counts']}",
        "",
        "## Coverage Matrix",
        f"- Cases: {matrix['case_count']}",
        f"- Original issue counts: {matrix['original_question_issue_counts']}",
        f"- Revised issue counts: {matrix['revised_question_issue_counts']}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def assert_expected_unchanged(original: Sequence[Dict[str, Any]], revised: Sequence[Dict[str, Any]]) -> None:
    for original_case, revised_case in zip(original, revised):
        if original_case.get("case_id") != revised_case.get("case_id"):
            raise ValueError(f"case order mismatch: {original_case.get('case_id')} != {revised_case.get('case_id')}")
        if original_case.get("expected") != revised_case.get("expected"):
            raise ValueError(f"expected changed for {original_case.get('case_id')}")


def apply_unique_question_variants(cases: List[Dict[str, Any]]) -> None:
    seen: set[str] = set()
    for case in cases:
        original_natural_question = str(case.get("question") or "")
        selected = ""
        variant_index = 0
        for index, candidate in enumerate(question_variants_for_case(case)):
            if candidate not in seen:
                selected = candidate
                variant_index = index
                break
        if not selected:
            exp = expected(case)
            operation = str(exp.get("operation") or "value")
            target = target_phrase_for_case(case)
            for fallback in [
                f"Use only supported records to answer the {operation} request for {target}.",
                f"Based on the current project context, answer the {operation} request for {target}.",
                f"Report what the current evidence supports for the {operation} request on {target}.",
            ]:
                if fallback not in seen:
                    selected = fallback
                    variant_index = 100
                    break
        if not selected:
            raise ValueError(f"could not create a unique question for {case.get('case_id')}")

        case["question"] = selected
        seen.add(selected)
        revision = case.setdefault("question_revision", {})
        revision["dedupe"] = {
            "original_natural_question": original_natural_question,
            "variant_index": variant_index,
            "changed_for_uniqueness": selected != original_natural_question,
        }


def revise_cases(cases: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    revised = [revise_case(case) for case in cases]
    apply_unique_question_variants(revised)
    assert_expected_unchanged(cases, revised)
    return revised


def run(args: argparse.Namespace) -> Dict[str, Any]:
    input_dir = Path(args.input_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = args.run_id or f"run_{timestamp}_natural"
    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    balanced_path = input_dir / "e4_balanced_benchmark_150.jsonl"
    matrix_path = input_dir / "e4_coverage_matrix_120.jsonl"
    source_summary_path = input_dir / "e4_benchmark_summary.json"

    balanced_original = read_jsonl(balanced_path)
    matrix_original = read_jsonl(matrix_path)
    balanced_revised = revise_cases(balanced_original)
    matrix_revised = revise_cases(matrix_original)

    balanced_out = out_dir / "e4_balanced_benchmark_150.jsonl"
    matrix_out = out_dir / "e4_coverage_matrix_120.jsonl"
    review_csv = out_dir / "e4_human_review_sample.csv"
    summary_json = out_dir / "e4_benchmark_summary.json"
    summary_md = out_dir / "e4_question_revision_report.md"

    write_jsonl(balanced_out, balanced_revised)
    write_jsonl(matrix_out, matrix_revised)
    write_review_csv(review_csv, balanced_revised)

    source_summary = {}
    if source_summary_path.exists():
        source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))

    summary = {
        "run_id": run_id,
        "source_run_id": source_summary.get("run_id"),
        "kg_dir": source_summary.get("kg_dir"),
        "outputs": {
            "matrix_jsonl": str(matrix_out),
            "balanced_jsonl": str(balanced_out),
            "review_csv": str(review_csv),
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
        },
        "matrix_summary": {
            **source_summary.get("matrix_summary", {}),
            **summarize_cases(matrix_revised),
        },
        "balanced_summary": {
            **source_summary.get("balanced_summary", {}),
            **summarize_cases(balanced_revised),
        },
        "method": {
            **source_summary.get("method", {}),
            "rewrite_generation": "deterministic_natural_question_revision",
            "expected_immutable_during_rewrite": True,
            "human_review_policy": "review all non-executable cases plus first 30 executable cases",
            "surface_policy": "hide experiment perspectives, internal ids, and self-clarification instructions from user-facing questions",
        },
    }
    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_summary_markdown(summary_md, summary)

    latest_path = Path(args.out_root) / "latest_e4_benchmark_summary.json"
    latest_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Revise E4 QA benchmark question wording.")
    parser.add_argument(
        "--input-dir",
        default="outputs/research_experiments/e4_benchmark/run_20260707_170206",
        help="Directory containing the source E4 JSONL files.",
    )
    parser.add_argument(
        "--out-root",
        default="outputs/research_experiments/e4_benchmark",
        help="Root directory for the revised run.",
    )
    parser.add_argument("--run-id", default="", help="Optional output run id.")
    return parser


def main() -> None:
    summary = run(build_arg_parser().parse_args())
    print(json.dumps({"run_id": summary["run_id"], "outputs": summary["outputs"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
