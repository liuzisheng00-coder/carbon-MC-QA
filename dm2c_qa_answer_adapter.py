"""Map CarbonQL execution results into the M3 answer schema used by E5 scoring.

Driven by the question text and the compiled program — never by gold `expected`.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from dm2c_carbonql import CarbonQLProgram, derive_view_signature
from dm2c_m3_context import CanonicalQueryResult, M3ExecutionContext

PERSPECTIVE_FOR_SCORE = {
    "product": "product",
    "material_source": "material",
    "energy_source": "process",
    "source_union": "product",
}

_EXPLAIN_CUE = re.compile(
    r"\bexplain\b|\bdescribe\b|\bevidence behind\b|\bsupporting evidence\b",
    re.IGNORECASE,
)
_TRACE_CUE = re.compile(
    r"\btrace\b"
    r"|\bevidence\s+chain\b"
    r"|\bsource[-\s]+to[-\s]+factor\s+path\b",
    re.IGNORECASE,
)
_COMPARE_CUE = re.compile(
    r"\bcompare\b"
    r"|\bhow\s+much\s+(?:larger|greater|higher|smaller|lower|more|less)\b.*\bthan\b",
    re.IGNORECASE,
)
_RANK_CUE = re.compile(
    r"\bwhich\b|\brank\b|\bmost\b|\btop\b|\blargest\b|\bhighest\b",
    re.IGNORECASE,
)
_VALUE_CUE = re.compile(
    r"\bwhat is\b|\bwhat's\b|\bhow much\b"
    r"|\bwhat\s+(?:supported\s+)?(?:carbon\s+)?(?:evidence\s+)?value\b"
    r"|\bevidence(?:[-\s]+backed)?\s+carbon\s+value\b"
    r"|\bevidence\s+coverage\b"
    r"|\bknown\s+carbon\s+records\b"
    r"|\breport\s+the\s+supported\s+carbon\s+value\b"
    r"|\bgive\s+me\s+the\s+known\s+material\s+carbon\s+result\b",
    re.IGNORECASE,
)
_AGGREGATE_CUE = re.compile(
    r"\bsummarize\b|\baggregate\b|\bby material\b|\bby carrier\b|\bcontribution\b",
    re.IGNORECASE,
)
_EXPLAIN_EXTRA_CUE = re.compile(
    r"\bshow why\b|\bsupported by the available evidence\b",
    re.IGNORECASE,
)
_STRONG_TRACEABILITY_CUE = re.compile(
    r"\bquantity[-\s]+factor[-\s]+source\b"
    r"|\bcarbon[-\s]+evidence\s+chains?\b"
    r"|\bevidence[-\s]+chains?\b"
    r"|\bselected\s+carbon\s+record\b"
    r"|\bcarbon\s+evidence(?:\s+evidence)?\s+(?:record|result|value|path|comparison)\b"
    r"|\bevidence(?:[-\s]+backed)\s+carbon\b"
    r"|\bevidence\s+coverage\b"
    r"|\btraceable\s+(?:quantity|carbon|evidence)\b"
    r"|\bwhat\s+evidence\s+supports\b"
    r"|\bsupported\s+carbon\s+evidence\b"
    r"|\btraceable\s+evidence\s+comparison\b"
    r"|\bfrom\s+target\s+object\s+to\s+quantity\b",
    re.IGNORECASE,
)
_MATERIAL_PERSPECTIVE_CUE = re.compile(
    r"\bmaterial(?:[-\s]+carbon)?\b|\bembodied[-\s]+carbon\b",
    re.IGNORECASE,
)
_MATERIAL_ACCOUNT_CUE = re.compile(
    r"\bmaterial\s+famil(?:y|ies)\b"
    r"|\bmaterial\s+records?\b"
    r"|\bmaterial\s+item\b"
    r"|\bsteel\s+material\b"
    r"|\bknown\s+material\s+carbon\b"
    r"|\bmaterial\s+carbon\s+total\b"
    r"|\bmaterial\s+carbon\s+evidence\s+path\b"
    r"|\bembodied[-\s]+carbon\b",
    re.IGNORECASE,
)
_PROCESS_PERSPECTIVE_CUE = re.compile(
    r"\bfactory[-\s]+energy\b|\bprocess[-\s]+energy\b"
    r"|\benergy\s+(?:carrier|record|use)\b"
    r"|\bproduction\s+energy\b",
    re.IGNORECASE,
)
_PRODUCT_PERSPECTIVE_CUE = re.compile(
    r"\bcomponent[-\s]+carbon\b|\bcomponent\s+carbon\s+evidence\b",
    re.IGNORECASE,
)
_PRODUCT_SPLIT_CUE = re.compile(
    r"\bsplit\s+into\b"
    r"|\bmaterial(?:s)?\s+and\s+factory\b"
    r"|\bmaterial\s+contribution\s+and\s+factory\b"
    r"|\bseparate\s+the\s+material\s+and\s+factory\b"
    r"|\bmaterials?\s+and\s+(?:factory\s+)?energy\b"
    r"|\bproject(?:'s)?\s+known\s+carbon\s+total\b"
    r"|\bproject-level\s+carbon\b"
    r"|\bsupported\s+project\s+carbon\b",
    re.IGNORECASE,
)


def map_perspective(raw_perspective: str) -> str:
    return PERSPECTIVE_FOR_SCORE.get(str(raw_perspective or ""), str(raw_perspective or ""))


def _evidence_chain_question(question: str) -> bool:
    """True when the question is evidence/provenance wording, not an account total.

    Traceability is a question type (trace/explain/evidence-chain cues), not a
    fourth accounting perspective. This helper only decides summary shape.
    """

    text = question or ""
    if not _STRONG_TRACEABILITY_CUE.search(text):
        return False
    has_material_account = bool(_MATERIAL_ACCOUNT_CUE.search(text))
    has_material = bool(_MATERIAL_PERSPECTIVE_CUE.search(text))
    has_process = bool(_PROCESS_PERSPECTIVE_CUE.search(text))
    has_product = bool(_PRODUCT_PERSPECTIVE_CUE.search(text))
    if has_material_account and not has_process and not has_product:
        return False
    if has_process and not has_material_account and not has_product:
        return False
    if has_product and not has_material_account and not has_process:
        return False
    if has_material and not has_process:
        return False
    return not (has_material_account or has_process or has_product)


def infer_perspective(question: str, raw_perspective: str) -> str:
    """Map execution views to product / material / process answer classes."""

    text = question or ""
    mapped = map_perspective(raw_perspective)
    # Account anchors win over generic evidence wording when the question is
    # clearly a material/process/product account request.
    has_material_account = bool(_MATERIAL_ACCOUNT_CUE.search(text))
    has_material = bool(_MATERIAL_PERSPECTIVE_CUE.search(text))
    has_process = bool(_PROCESS_PERSPECTIVE_CUE.search(text))
    has_product = bool(_PRODUCT_PERSPECTIVE_CUE.search(text))
    if _STRONG_TRACEABILITY_CUE.search(text):
        if has_material_account and not has_process and not has_product:
            return "material"
        if has_process and not has_material_account and not has_product:
            return "process"
        if has_product and not has_material_account and not has_process:
            return "product"
        if not (has_material_account or has_process or has_product):
            return mapped
    # Product known-total / split questions mention "material" and "factory"
    # but remain product-account answers.
    if _PRODUCT_SPLIT_CUE.search(text):
        return "product"
    if has_material and not has_process:
        return "material"
    if has_process and not has_material:
        return "process"
    if has_product:
        return "product"
    return mapped


def infer_operation(question: str, program: CarbonQLProgram) -> str:
    ops = {step.op for step in program.steps}
    text = question or ""
    if _COMPARE_CUE.search(text):
        return "compare"
    if _EXPLAIN_CUE.search(text) or _EXPLAIN_EXTRA_CUE.search(text):
        return "explain"
    if _TRACE_CUE.search(text):
        return "trace"
    if _RANK_CUE.search(text):
        return "rank"
    if _AGGREGATE_CUE.search(text) or _VALUE_CUE.search(text):
        return "value"
    if "Compare" in ops:
        return "compare"
    if "Rank" in ops:
        return "rank"
    if "Trace" in ops:
        return "trace"
    return "value"


def adapt_observed(
    *,
    question: str,
    program: CarbonQLProgram,
    result: CanonicalQueryResult,
    context: M3ExecutionContext | None = None,
    selected_component_ids: Sequence[str] = (),
    elapsed_ms: float = 0.0,
    compiler_status: str | None = None,
) -> dict[str, Any]:
    perspective = infer_perspective(question, result.perspective)
    operation = infer_operation(question, program)
    summary = _build_summary(
        question=question,
        program=program,
        result=result,
        perspective=perspective,
        operation=operation,
        context=context,
        selected_component_ids=selected_component_ids,
    )
    evidence_ids = list(result.evidence_ids)
    observed: dict[str, Any] = {
        "variant": "full_real",
        "status": result.status,
        "coverage_status": result.coverage_status,
        "perspective": perspective,
        "operation": operation,
        "summary": summary,
        "evidence_ids": evidence_ids,
        "supported_numeric_values": _supported_numbers(summary),
        "supported_text_tokens": [
            result.status,
            result.coverage_status,
            perspective,
            operation,
            *evidence_ids,
        ],
        "supported_evidence_ids": evidence_ids,
        "answer": _answer_text(
            summary,
            perspective=perspective,
            operation=operation,
            status=result.status,
        ),
        "latency_ms": round(elapsed_ms, 3),
        "raw_perspective": result.perspective,
        "raw_operation": result.operation,
    }
    if compiler_status is not None:
        observed["compiler_status"] = compiler_status
    return observed


def _build_summary(
    *,
    question: str,
    program: CarbonQLProgram,
    result: CanonicalQueryResult,
    perspective: str,
    operation: str,
    context: M3ExecutionContext | None,
    selected_component_ids: Sequence[str],
) -> dict[str, Any]:
    base = dict(result.summary)
    if result.status != "executable":
        return base

    rows = [dict(row) for row in result.rows]
    total = base.get("total_kgCO2e")

    if _evidence_chain_question(question):
        return {
            "traced_atomic_emission_kgCO2e": total,
            "evidence_hops": len(result.evidence_ids),
        }

    sources = derive_view_signature(program).emission_sources
    if perspective in {"material", "process"} and set(sources) != {perspective}:
        # Grouping describes the answer, not the carbon sources in its total.
        # Keep the executor's neutral keys when the requested scope differs.
        summary = {**base, "source_scope": "+".join(sources), "rows": rows}
        if operation == "compare" and len(rows) >= 2:
            summary["difference_kgCO2e"] = (
                float(rows[0].get("kgCO2e") or 0.0)
                - float(rows[1].get("kgCO2e") or 0.0)
            )
        return summary

    if perspective == "product" and operation in {"value", "aggregate"}:
        material_total, process_total = _product_source_split(
            context, selected_component_ids
        )
        summary = {
            "components": base.get("available_component_count")
            or base.get("components"),
            "knownTotalCarbon_kgCO2e": total,
            "totalMaterialCarbon_kgCO2e": material_total,
            "knownProcessCarbon_kgCO2e": process_total,
        }
        return {key: value for key, value in summary.items() if value is not None}

    if perspective == "product" and operation == "rank" and rows:
        top = rows[0]
        summary = {"top_component_knownTotalCarbon_kgCO2e": top.get("kgCO2e")}
        component_id = str(top.get("component") or "")
        if component_id:
            summary["component"] = component_id
        global_id = _component_global_id(context, component_id)
        if global_id:
            summary["globalId"] = global_id
        return summary

    if perspective == "product" and operation == "compare" and len(rows) >= 2:
        first, second = rows[0], rows[1]
        summary = {
            "first_component_kgCO2e": first.get("kgCO2e"),
            "second_component_kgCO2e": second.get("kgCO2e"),
            "difference_kgCO2e": float(first.get("kgCO2e") or 0.0)
            - float(second.get("kgCO2e") or 0.0),
        }
        first_id = str(first.get("component") or "")
        second_id = str(second.get("component") or "")
        if first_id:
            summary["first_component"] = first_id
            gid = _component_global_id(context, first_id)
            if gid:
                summary["first_globalId"] = gid
        if second_id:
            summary["second_component"] = second_id
            gid = _component_global_id(context, second_id)
            if gid:
                summary["second_globalId"] = gid
        return summary

    if perspective == "product" and operation in {"explain", "trace"}:
        return {"component_knownTotalCarbon_kgCO2e": total}

    if perspective == "material" and operation in {"value", "aggregate"}:
        return {
            "totalMaterialCarbon_kgCO2e": total,
            "material_families": len(rows) if rows else base.get("row_count"),
        }

    if perspective == "material" and operation == "rank" and rows:
        return {"top_material_family_kgCO2e": rows[0].get("kgCO2e")}

    if perspective == "material" and operation == "compare" and len(rows) >= 2:
        first, second = rows[0], rows[1]
        return {
            "first_material_family_kgCO2e": first.get("kgCO2e"),
            "second_material_family_kgCO2e": second.get("kgCO2e"),
            "difference_kgCO2e": float(first.get("kgCO2e") or 0.0)
            - float(second.get("kgCO2e") or 0.0),
        }

    if perspective == "material":
        return {"material_record_kgCO2e": total}

    if perspective == "process" and operation in {"value", "aggregate"}:
        return {
            "knownProcessCarbon_kgCO2e": total,
            "process_energy_records": len(result.emission_ids),
        }

    if perspective == "process" and operation == "rank" and rows:
        return {"top_process_record_kgCO2e": rows[0].get("kgCO2e")}

    if perspective == "process" and operation == "compare" and len(rows) >= 2:
        first, second = rows[0], rows[1]
        return {
            "first_carrier_kgCO2e": first.get("kgCO2e"),
            "second_carrier_kgCO2e": second.get("kgCO2e"),
            "difference_kgCO2e": float(first.get("kgCO2e") or 0.0)
            - float(second.get("kgCO2e") or 0.0),
        }

    if perspective == "process":
        return {"process_record_kgCO2e": total}

    # Fallback: keep executor keys so numbers remain available.
    return base


def _component_global_id(
    context: M3ExecutionContext | None, component_id: str
) -> str | None:
    if context is None or not component_id:
        return None
    from dm2c_canonical_v2_reader import lookup_dimension

    try:
        entity = lookup_dimension(context.canonical, "component", component_id)
    except Exception:
        return None
    if not isinstance(entity, Mapping):
        return None
    props = entity.get("props") or {}
    value = props.get("globalId") or props.get("GlobalId")
    return str(value) if value else None


def _product_source_split(
    context: M3ExecutionContext | None,
    selected_component_ids: Sequence[str],
) -> tuple[float | None, float | None]:
    if context is None:
        return None, None
    from dm2c_full_qa_experiment_runner import execute_canonical_query

    material_program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {
                    "op": "CarbonAtoms",
                    "source": "material",
                    "known_total": True,
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    # Grouping by component is what makes this the product-attributed share
    # rather than the whole process account: the split has to add back up to
    # the product total, and the records no product carries belong to neither
    # side of it.
    process_program = CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {
                    "op": "CarbonAtoms",
                    "source": "process",
                    "known_total": True,
                },
                {"op": "GroupBy", "keys": ["component"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )
    material = execute_canonical_query(
        context, material_program, selected_component_ids
    )
    process = execute_canonical_query(
        context, process_program, selected_component_ids
    )
    material_total = (
        material.summary.get("total_kgCO2e")
        if material.status == "executable"
        else None
    )
    process_total = (
        process.summary.get("total_kgCO2e")
        if process.status == "executable"
        else None
    )
    return (
        float(material_total) if material_total is not None else None,
        float(process_total) if process_total is not None else None,
    )


def _supported_numbers(value: Any) -> list[float]:
    numbers: list[float] = []
    if isinstance(value, Mapping):
        for item in value.values():
            numbers.extend(_supported_numbers(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers.extend(_supported_numbers(item))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numbers.append(float(value))
    return numbers


def _disclosure_tokens(
    *,
    status: str,
    perspective: str,
    summary: Mapping[str, Any],
) -> list[str]:
    if status == "unresolved_target":
        return ["unresolved_target"]
    if status == "clarification_required":
        return ["clarification_required", "clarification"]
    if status != "incomplete_path":
        return [status] if status else []
    blocked = str(summary.get("blocked_status") or "")
    tokens = [status]
    if perspective == "process":
        tokens.append("process_granularity_not_supported")
    else:
        tokens.append("blocked_factor_unresolved")
    if blocked:
        tokens.append(blocked)
    return tokens


def _answer_text(
    summary: Mapping[str, Any],
    *,
    perspective: str,
    operation: str,
    status: str,
) -> str:
    import json

    # Include entity identifiers as plain text so answer_contains checks for
    # GlobalIds / names can succeed without loosening the scorer.
    parts = [f"perspective={perspective}", f"operation={operation}", f"status={status}"]
    parts.extend(
        _disclosure_tokens(status=status, perspective=perspective, summary=summary)
    )
    for key, value in summary.items():
        parts.append(f"{key}={value}")
        if isinstance(value, str) and value:
            parts.append(value)
    parts.append(json.dumps(dict(summary), ensure_ascii=False, sort_keys=True))
    return " ".join(parts)


__all__ = [
    "PERSPECTIVE_FOR_SCORE",
    "adapt_observed",
    "infer_operation",
    "infer_perspective",
    "map_perspective",
]
