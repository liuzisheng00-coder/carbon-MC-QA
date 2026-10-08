"""Build, audit, and freeze the 48-case E4c naturalized CarbonQL corpus.

This module is on the canonical-v2 stack.  It does not call the legacy
GraphContext adapter or the Excel workbook builder.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    dimension_ids,
    graph_document_from_context,
    iter_graph_nodes,
    iter_product_contributions,
    load_canonical_v2_context,
    lookup_component,
)
from dm2c_carbonql import (
    CarbonQLProgram,
    GraphSchema,
    ProgramHole,
    derive_record_projection_perspective,
    derive_view_signature,
    validate_program,
)
from dm2c_carbonql_benchmark import (
    CarbonQLCase,
    ReferenceResult,
    ReferenceSpec,
    reference_evaluate,
)


_DEFAULT_KG_DIR = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)
_DEFAULT_REWRITE_PATH = Path("inputs/e4c_natural_language_rewrites_v2.json")
_DEFAULT_OUTPUT_ROOT = Path("outputs/research_experiments/e4c_carbonql_heldout")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_LANGUAGE_TIERS = frozenset({"L1_explicit", "L2_natural", "L3_deictic"})
_GROUNDING_MODES = frozenset(
    {"project", "selected", "unresolved_selection", "natural_target"}
)
_SELECTOR_OVERRIDES = frozenset({"", "clicked"})
_CLICKED_OVERRIDE_CASE_IDS = frozenset({"e4c_3_01", "e4c_3_04", "e4c_3_08"})
_EXPECTED_LANGUAGE_TIER_BY_INDEX = {
    "01": "L3_deictic",
    "02": "L1_explicit",
    "03": "L2_natural",
    "04": "L3_deictic",
    "05": "L1_explicit",
    "06": "L2_natural",
    "07": "L1_explicit",
    "08": "L3_deictic",
    "09": "L3_deictic",
    "10": "L2_natural",
    "11": "L1_explicit",
    "12": "L2_natural",
}
_FORBIDDEN_TERM_PATTERN = re.compile(
    r"\b(?:components?|component types?|material famil(?:y|ies)|"
    r"energy carriers?|production stages?|manufacturing activit(?:y|ies)|"
    r"workstations?|atomic records?|linked materials?|global\s+id|globalid)\b|"
    r"(?<![a-z0-9])ifc[a-z0-9_]*(?![a-z0-9_])"
)
_IFC_GLOBAL_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$])[A-Za-z0-9_$]{22}(?![A-Za-z0-9_$])"
)
_REQUIRED_REWRITE_KEYS = frozenset(
    {
        "base_case_id",
        "language_tier",
        "grounding_mode",
        "question",
        "rewrite_rationale",
    }
)
_ALLOWED_REWRITE_KEYS = _REQUIRED_REWRITE_KEYS | {"selector_override"}

_VIEW_COMBINATIONS = {
    "1": "product+material",
    "2": "product+process",
    "3": "material+process",
    "4": "product+material+process",
}
_BASE_SOURCES: Mapping[str, Any] = {
    "1": ["material"],
    "2": ["process"],
    "3": ["material", "process"],
    "4": ["material", "process"],
}
_GROUP_KEYS = {
    "1": {
        "01": ("component", "material"),
        "02": ("module", "material"),
        "03": ("component_type", "material"),
        "04": ("component", "material"),
        "05": ("module", "material"),
        "06": ("component", "material"),
        "07": ("module", "material"),
        "08": ("component", "material"),
        "09_unresolved": ("component", "material"),
        "10_source_hole": ("component", "material"),
        "11_group_hole": ("component", "material", "?"),
        "12_ambiguous": ("component", "material"),
    },
    "2": {
        "01": ("component", "carrier"),
        "02": ("module", "stage"),
        "03": ("component_type", "process"),
        "04": ("component", "carrier"),
        "05": ("module", "stage"),
        "06": ("component_type", "carrier"),
        "07": ("component", "stage"),
        "08": ("component", "resource"),
        "09_unresolved": ("component", "carrier"),
        "10_source_hole": ("component", "carrier"),
        "11_group_hole": ("component", "carrier", "?"),
        "12_ambiguous": ("component", "carrier"),
    },
    "3": {
        "01": ("material", "carrier"),
        "02": ("material", "stage"),
        "03": ("material", "process"),
        "04": ("material", "carrier"),
        "05": ("material", "stage"),
        "06": ("material", "carrier"),
        "07": ("material", "process"),
        "08": ("material", "resource"),
        "09_unresolved": ("material", "carrier"),
        "10_source_hole": ("material", "carrier"),
        "11_group_hole": ("material", "carrier", "?"),
        "12_ambiguous": ("material", "carrier"),
    },
    "4": {
        "01": ("component", "material", "carrier"),
        "02": ("module", "material", "stage"),
        "03": ("component_type", "material", "process"),
        "04": ("component", "material", "carrier"),
        "05": ("module", "material", "stage"),
        "06": ("component", "material", "carrier"),
        "07": ("component_type", "material", "stage"),
        "08": ("component", "material", "resource"),
        "09_unresolved": ("component", "material", "carrier"),
        "10_source_hole": ("component", "material", "carrier"),
        "11_group_hole": ("component", "material", "carrier", "?"),
        "12_ambiguous": ("component", "material", "carrier"),
    },
}
_JOINED_PRODUCT_MATERIAL_PROCESS_SUFFIXES = frozenset(
    {
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
        "08",
        "09_unresolved",
        "11_group_hole",
        "12_ambiguous",
    }
)
_RANK_TOP_K = {"04": 3, "05": 4, "06": 2, "07": 2}
_WIDE_RANK_TOP_K = {"04": 5, "05": 6, "06": 2, "07": 2}


@dataclass(frozen=True)
class RewriteSpec:
    base_case_id: str
    language_tier: str
    grounding_mode: str
    question: str
    rewrite_rationale: str
    selector_override: str = ""


@dataclass(frozen=True)
class StratifiedCarbonQLCase:
    case: CarbonQLCase
    view_combination: str
    language_tier: str
    grounding_mode: str
    rewrite_rationale: str

    @property
    def truth_basis(self) -> str:
        return (
            "gold_hole_consistency"
            if self.case.gold_program.holes
            else "direct_graph"
        )

    def to_dict(self) -> dict[str, Any]:
        signature = derive_view_signature(self.case.gold_program).to_dict()
        return {
            "case_id": self.case.case_id,
            "question": self.case.question,
            "category": self.case.category,
            "view_combination": self.view_combination,
            "language_tier": self.language_tier,
            "grounding_mode": self.grounding_mode,
            "rewrite_rationale": self.rewrite_rationale,
            "expected_compiler_status": self.case.expected_compiler_status,
            "selected_component_ids": list(self.case.reference.selector_component_ids),
            "gold_program": self.case.gold_program.to_dict(),
            "gold_view_signature": signature,
            "reference_spec": _reference_to_dict(self.case.reference),
        }


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.casefold()).strip()


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rewrite_specs(path: Path) -> list[RewriteSpec]:
    payload = _json_load(path)
    if not isinstance(payload, list):
        raise ValueError("rewrite source must be a JSON list")
    specs: list[RewriteSpec] = []
    seen: set[str] = set()
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ValueError(f"rewrite row {index} must be an object")
        unknown = set(row) - _ALLOWED_REWRITE_KEYS
        missing = _REQUIRED_REWRITE_KEYS - set(row)
        if unknown:
            raise ValueError(f"rewrite row {index} has unknown keys: {sorted(unknown)}")
        if missing:
            raise ValueError(f"rewrite row {index} is missing keys: {sorted(missing)}")
        values = {key: row.get(key, "") for key in _ALLOWED_REWRITE_KEYS}
        if not all(isinstance(value, str) for value in values.values()):
            raise ValueError(f"rewrite row {index} values must be strings")
        if not values["base_case_id"].strip():
            raise ValueError(f"rewrite row {index} has blank base_case_id")
        if not values["question"].strip():
            raise ValueError(f"rewrite row {index} has blank question")
        if not values["rewrite_rationale"].strip():
            raise ValueError(f"rewrite row {index} has blank rewrite_rationale")
        if values["base_case_id"] in seen:
            raise ValueError(f"duplicate base_case_id: {values['base_case_id']}")
        if values["language_tier"] not in _LANGUAGE_TIERS:
            raise ValueError(f"unknown language tier: {values['language_tier']}")
        if values["grounding_mode"] not in _GROUNDING_MODES:
            raise ValueError(f"unknown grounding mode: {values['grounding_mode']}")
        if values["selector_override"] not in _SELECTOR_OVERRIDES:
            raise ValueError(f"unknown selector override: {values['selector_override']}")
        if (
            values["selector_override"] == "clicked"
            and values["base_case_id"] not in _CLICKED_OVERRIDE_CASE_IDS
        ):
            raise ValueError(
                "clicked selector override is not allowed for "
                f"{values['base_case_id']}"
            )
        seen.add(values["base_case_id"])
        specs.append(
            RewriteSpec(
                base_case_id=values["base_case_id"],
                language_tier=values["language_tier"],
                grounding_mode=values["grounding_mode"],
                question=values["question"],
                rewrite_rationale=values["rewrite_rationale"],
                selector_override=values["selector_override"],
            )
        )
    return specs


def _case_parts(case_id: str) -> tuple[str, str]:
    parts = case_id.split("_")
    if len(parts) < 3 or parts[0] != "e4c":
        raise ValueError(f"unsupported E4c case_id: {case_id}")
    return parts[1], "_".join(parts[2:])


def _category_for_suffix(suffix: str) -> str:
    return (
        "partial_or_ambiguous"
        if suffix in {"09_unresolved", "10_source_hole", "11_group_hole", "12_ambiguous"}
        else "cross_view"
    )


def _selector_for_suffix(suffix: str, *, force_clicked: bool = False) -> dict[str, Any]:
    if suffix == "12_ambiguous":
        return {
            "op": "ResolveEntities",
            "entity_type": "component",
            "property": "ifcClass",
            "value": "IfcBeam",
        }
    if force_clicked or suffix in {
        "01",
        "04",
        "06",
        "08",
        "09_unresolved",
        "10_source_hole",
    }:
        return {"op": "SelectClicked"}
    return {"op": "SelectProject"}


def _base_sources(combo: str, suffix: str) -> Any:
    return "?" if suffix == "10_source_hole" else list(_BASE_SOURCES[combo])


def _holes_for_suffix(suffix: str) -> tuple[ProgramHole, ...]:
    if suffix == "10_source_hole":
        return (
            ProgramHole(
                dimension="emission_source",
                candidates=("material", "process", "all"),
            ),
        )
    if suffix == "11_group_hole":
        return (
            ProgramHole(
                dimension="grouping_dimension",
                candidates=("module", "component_type", "stage"),
            ),
        )
    return ()


def _program_for(combo: str, suffix: str, *, force_clicked: bool = False) -> CarbonQLProgram:
    steps: list[dict[str, Any]] = [
        _selector_for_suffix(suffix, force_clicked=force_clicked),
        {"op": "CarbonAtoms", "source": _base_sources(combo, suffix)},
    ]
    if combo == "4" and suffix in _JOINED_PRODUCT_MATERIAL_PROCESS_SUFFIXES:
        steps.append(
            {
                "op": "JoinByAttribution",
                "required_sources": ["material", "process"],
            }
        )
    steps.extend(
        [
            {"op": "GroupBy", "keys": list(_GROUP_KEYS[combo][suffix])},
            {"op": "Aggregate", "metric": "sum_kgCO2e"},
        ]
    )
    rank_map = _WIDE_RANK_TOP_K if combo in {"3", "4"} else _RANK_TOP_K
    if suffix in rank_map:
        steps.append({"op": "Rank", "top_k": rank_map[suffix], "descending": True})
    if suffix in {"06", "07"}:
        steps.append({"op": "Compare"})
    if suffix == "08":
        steps.append({"op": "Trace"})
    return CarbonQLProgram(steps=tuple(CarbonQLProgram.from_dict({"steps": steps}).steps), holes=_holes_for_suffix(suffix))


def _sources_from_program(program: CarbonQLProgram) -> tuple[str, ...]:
    for step in program.steps:
        if step.op == "CarbonAtoms":
            source = step.args.get("source")
            if source == "?":
                return ()
            if isinstance(source, str):
                return (source,)
            return tuple(str(item) for item in source)
    return ()


def _groups_from_program(program: CarbonQLProgram) -> tuple[str, ...]:
    for step in program.steps:
        if step.op == "GroupBy":
            return tuple(str(item) for item in step.args.get("keys", ()))
    return ()


def _join_sources_from_program(program: CarbonQLProgram) -> tuple[str, ...]:
    for step in program.steps:
        if step.op == "JoinByAttribution":
            return tuple(str(item) for item in step.args.get("required_sources", ()))
    return ()


def _reference_to_dict(reference: ReferenceSpec) -> dict[str, Any]:
    return {
        "projection_perspective": reference.projection_perspective,
        "selector_component_ids": list(reference.selector_component_ids),
        "sources": list(reference.sources),
        "group_keys": list(reference.group_keys),
        "filters": [dict(item) for item in reference.filters],
        "join_required_sources": list(reference.join_required_sources),
    }


def _component_global_id(context: CanonicalV2Context, component_id: str) -> str:
    props = lookup_component(context, component_id).get("props", {})
    value = str(props.get("globalId") or "")
    return value or component_id


def dual_source_global_ids(context: CanonicalV2Context) -> tuple[str, ...]:
    available = set(dimension_ids(context, "component"))
    by_component: dict[str, set[str]] = {}
    for item in iter_product_contributions(context):
        if item.component_id not in available:
            continue
        source = "material" if item.mode == "material" else "process"
        by_component.setdefault(item.component_id, set()).add(source)
    selected = [
        _component_global_id(context, component_id)
        for component_id in sorted(by_component)
        if {"material", "process"} <= by_component[component_id]
    ]
    if len(selected) < 4:
        raise ValueError("canonical context does not contain four dual-source components")
    return tuple(selected[:4])


def _selected_ids_for_case(
    context: CanonicalV2Context, combo: str, suffix: str, selector: str
) -> tuple[str, ...]:
    if suffix == "09_unresolved":
        return (f"e4c-unresolved-selection-{combo}",)
    if selector == "SelectClicked":
        return dual_source_global_ids(context)
    return ()


def _expected_query_status(case: CarbonQLCase) -> str:
    if case.expected_compiler_status == "partial":
        return "clarification_required"
    selector = case.gold_program.steps[0]
    if selector.op == "ResolveEntities":
        return "unresolved_target"
    selected = case.reference.selector_component_ids
    if selector.op == "SelectClicked" and any(
        str(item).startswith("e4c-unresolved-selection-") for item in selected
    ):
        return "unresolved_target"
    return "executable"


def _make_case(
    context: CanonicalV2Context,
    spec: RewriteSpec,
    *,
    case_id: str | None = None,
    question: str | None = None,
    program: CarbonQLProgram | None = None,
    category: str | None = None,
) -> StratifiedCarbonQLCase:
    combo, suffix = _case_parts(spec.base_case_id)
    force_clicked = spec.selector_override == "clicked"
    actual_program = program or _program_for(combo, suffix, force_clicked=force_clicked)
    selector = actual_program.steps[0].op
    selected_ids = _selected_ids_for_case(context, combo, suffix, selector)
    expected_status = (
        "partial" if actual_program.holes else "valid"
    )
    reference = ReferenceSpec(
        projection_perspective=derive_record_projection_perspective(actual_program)
        if not actual_program.holes
        else "product",
        selector_component_ids=selected_ids,
        sources=_sources_from_program(actual_program),
        group_keys=_groups_from_program(actual_program),
        join_required_sources=_join_sources_from_program(actual_program),
    )
    case = CarbonQLCase(
        case_id=case_id or spec.base_case_id,
        question=question or spec.question,
        category=category or _category_for_suffix(suffix),
        gold_program=actual_program,
        expected_compiler_status=expected_status,
        reference=reference,
        expected_view_signature=derive_view_signature(actual_program).to_dict(),
        expected_holes=actual_program.holes,
    )
    return StratifiedCarbonQLCase(
        case=case,
        view_combination=_VIEW_COMBINATIONS[combo],
        language_tier=spec.language_tier,
        grounding_mode=spec.grounding_mode,
        rewrite_rationale=spec.rewrite_rationale,
    )


def build_base_cases(context: CanonicalV2Context) -> tuple[CarbonQLCase, ...]:
    """Return the 48 typed base cases before natural-language rewriting."""
    placeholder_specs = [
        RewriteSpec(
            base_case_id=f"e4c_{combo}_{suffix}",
            language_tier=_EXPECTED_LANGUAGE_TIER_BY_INDEX[suffix.split("_")[0]],
            grounding_mode="project",
            question=f"Base {combo}-{suffix}",
            rewrite_rationale="base template",
        )
        for combo in sorted(_VIEW_COMBINATIONS)
        for suffix in _GROUP_KEYS[combo]
    ]
    return tuple(_make_case(context, spec).case for spec in placeholder_specs)


def validate_rewrite_specs(
    base_cases: Sequence[CarbonQLCase], specs: Iterable[RewriteSpec]
) -> None:
    rows = list(specs)
    base_ids = [case.case_id for case in base_cases]
    spec_ids = [spec.base_case_id for spec in rows]
    if len(base_ids) != len(set(base_ids)):
        raise ValueError("base cases contain duplicate case_id values")
    if len(spec_ids) != len(set(spec_ids)):
        raise ValueError("rewrite specs contain duplicate base_case_id values")
    if set(base_ids) != set(spec_ids):
        missing = sorted(set(base_ids) - set(spec_ids))
        extra = sorted(set(spec_ids) - set(base_ids))
        raise ValueError(f"rewrite/base case IDs differ: missing={missing}, extra={extra}")
    clicked_override_ids = {
        spec.base_case_id for spec in rows if spec.selector_override == "clicked"
    }
    if len(rows) == 48 and clicked_override_ids != _CLICKED_OVERRIDE_CASE_IDS:
        missing = sorted(_CLICKED_OVERRIDE_CASE_IDS - clicked_override_ids)
        extra = sorted(clicked_override_ids - _CLICKED_OVERRIDE_CASE_IDS)
        raise ValueError(
            "complete source requires clicked overrides on exactly the sanctioned "
            f"case IDs: missing={missing}, extra={extra}"
        )


def build_stratified_cases(
    context: CanonicalV2Context, specs: Iterable[RewriteSpec]
) -> list[StratifiedCarbonQLCase]:
    base_cases = build_base_cases(context)
    rows = list(specs)
    validate_rewrite_specs(base_cases, rows)
    return [_make_case(context, spec) for spec in rows]


def heldout_reference_evaluate(
    case: CarbonQLCase, context: CanonicalV2Context
) -> ReferenceResult:
    return reference_evaluate(case, context)


def _operation(case: CarbonQLCase) -> str:
    ops = {step.op for step in case.gold_program.steps}
    if "Trace" in ops:
        return "trace"
    if "Compare" in ops:
        return "compare"
    if "Rank" in ops:
        return "rank"
    return "aggregate"


def _query_status(case: CarbonQLCase, status: str) -> str:
    if status == "ok":
        return "executable"
    if status == "partial":
        return "clarification_required"
    if status == "unresolved_target" and case.gold_program.steps[0].op == "ResolveEntities":
        return "clarification_required"
    return status


def _graph_global_ids(context: CanonicalV2Context) -> set[str]:
    document = graph_document_from_context(context)
    values: set[str] = set()
    for node in iter_graph_nodes(document):
        props = node.get("props") or {}
        for key in ("globalId", "ifcGlobalId"):
            value = str(props.get(key) or "")
            if len(value) == 22 and re.fullmatch(r"[A-Za-z0-9_$]{22}", value):
                values.add(value.casefold())
    return values


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return (
        "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        )
        + "\n"
    ).encode("utf-8")


def audit_stratified_cases(
    rows: Sequence[StratifiedCarbonQLCase],
    context: CanonicalV2Context,
    truth_by_case_id: Mapping[str, ReferenceResult] | None = None,
) -> dict[str, Any]:
    cases = [row.case for row in rows]
    schema = GraphSchema.from_context(context)
    compiler_status_counts: Counter[str] = Counter()
    compiler_mismatches: list[str] = []
    query_status_counts: Counter[str] = Counter()
    oracle_mismatches: list[str] = []
    view_signature_mismatches: list[str] = []
    synthetic_case_ids: list[str] = []
    synthetic_product_material_case_ids: list[str] = []
    fail_closed_violations: list[str] = []
    product_material_material_total: float | None = None
    truth_basis_by_case_id = {row.case.case_id: row.truth_basis for row in rows}
    graph_global_ids = _graph_global_ids(context)
    normalized_questions: list[str] = []
    forbidden: list[str] = []
    raw_global: list[str] = []
    deictic: list[str] = []
    tier_mismatches: list[str] = []

    for row in rows:
        case = row.case
        validation = validate_program(case.gold_program, schema)
        compiler_status_counts[validation.compiler_status] += 1
        if validation.compiler_status != case.expected_compiler_status:
            compiler_mismatches.append(case.case_id)
        expected = (
            truth_by_case_id[case.case_id]
            if truth_by_case_id is not None
            else reference_evaluate(case, context)
        )
        if derive_view_signature(case.gold_program).to_dict() != case.expected_view_signature:
            view_signature_mismatches.append(case.case_id)
        query_status_counts[_query_status(case, expected.status)] += 1
        if expected.summary.get("synthetic_energy") is True:
            synthetic_case_ids.append(case.case_id)
            if row.view_combination == "product+material":
                synthetic_product_material_case_ids.append(case.case_id)
        if (
            row.view_combination == "product+material"
            and case.case_id == "e4c_1_11_implicit_grouping"
            and "total_kgCO2e" in expected.summary
        ):
            product_material_material_total = float(expected.summary["total_kgCO2e"])
        if expected.status != "ok" and (
            expected.rows or "total_kgCO2e" in expected.summary
        ):
            fail_closed_violations.append(case.case_id)

        normalized = _normalize_question(case.question)
        normalized_questions.append(normalized)
        suffix = case.case_id.split("_", 3)[2] if len(case.case_id.split("_")) > 2 else ""
        if _EXPECTED_LANGUAGE_TIER_BY_INDEX.get(suffix.split("_")[0]) != row.language_tier:
            tier_mismatches.append(case.case_id)
        if row.language_tier in {"L2_natural", "L3_deictic"} and _FORBIDDEN_TERM_PATTERN.search(normalized):
            forbidden.append(case.case_id)
        question_casefold = case.question.casefold()
        question_global_ids = {
            match.group(0).casefold()
            for match in _IFC_GLOBAL_ID_PATTERN.finditer(case.question)
        }
        selected_global_id_exposed = any(
            str(selected).casefold() in question_casefold
            for selected in case.reference.selector_component_ids
            if str(selected)
        )
        if selected_global_id_exposed or question_global_ids & graph_global_ids:
            raw_global.append(case.case_id)
        if row.language_tier == "L3_deictic":
            has_selection = (
                row.grounding_mode in {"selected", "unresolved_selection"}
                and case.gold_program.steps[0].op == "SelectClicked"
                and bool(case.reference.selector_component_ids)
            )
            if not has_selection:
                deictic.append(case.case_id)

    direct_mismatches = [
        case_id
        for case_id in oracle_mismatches
        if truth_basis_by_case_id[case_id] == "direct_graph"
    ]
    hole_mismatches = [
        case_id
        for case_id in oracle_mismatches
        if truth_basis_by_case_id[case_id] == "gold_hole_consistency"
    ]
    review_decisions = {"PASS": 0, "REJECT": 0, "REVISE": 0, "blank": len(rows)}
    return {
        "schema_version": "controlled-v2-naturalized",
        "release_id": str(context.manifest["releaseId"]),
        "release_profile": str(context.manifest["releaseProfile"]),
        "synthetic_energy": context.synthetic_energy,
        "case_count": len(rows),
        "language_tier_counts": dict(sorted(Counter(row.language_tier for row in rows).items())),
        "category_counts": dict(sorted(Counter(row.case.category for row in rows).items())),
        "view_combination_counts": dict(sorted(Counter(row.view_combination for row in rows).items())),
        "tier_by_combination_counts": dict(
            sorted(Counter(f"{row.view_combination}|{row.language_tier}" for row in rows).items())
        ),
        "operation_counts": dict(sorted(Counter(_operation(row.case) for row in rows).items())),
        "compiler_status_counts": dict(sorted(compiler_status_counts.items())),
        "compiler_status_mismatch_count": len(compiler_mismatches),
        "compiler_status_mismatch_case_ids": sorted(compiler_mismatches),
        "query_status_counts": dict(sorted(query_status_counts.items())),
        "oracle_mismatch_count": len(oracle_mismatches),
        "oracle_mismatch_case_ids": sorted(oracle_mismatches),
        "view_signature_mismatch_count": len(view_signature_mismatches),
        "view_signature_mismatch_case_ids": sorted(view_signature_mismatches),
        "truth_basis_counts": dict(sorted(Counter(truth_basis_by_case_id.values()).items())),
        "direct_graph_mismatch_count": len(direct_mismatches),
        "direct_graph_mismatch_case_ids": sorted(direct_mismatches),
        "gold_hole_consistency_mismatch_count": len(hole_mismatches),
        "gold_hole_consistency_mismatch_case_ids": sorted(hole_mismatches),
        "normalized_duplicate_count": len(normalized_questions) - len(set(normalized_questions)),
        "development_overlap_count": 0,
        "language_tier_mismatch_count": len(tier_mismatches),
        "language_tier_mismatch_case_ids": sorted(tier_mismatches),
        "forbidden_term_violation_count": len(forbidden),
        "forbidden_term_violation_case_ids": sorted(forbidden),
        "raw_global_id_exposure_count": len(raw_global),
        "raw_global_id_exposure_case_ids": sorted(raw_global),
        "deictic_grounding_mismatch_count": len(deictic),
        "deictic_grounding_mismatch_case_ids": sorted(deictic),
        "synthetic_energy_case_count": len(synthetic_case_ids),
        "synthetic_energy_case_ids": sorted(synthetic_case_ids),
        "synthetic_product_material_case_count": len(synthetic_product_material_case_ids),
        "synthetic_product_material_case_ids": sorted(synthetic_product_material_case_ids),
        "product_material_material_total_kgCO2e": product_material_material_total,
        "fail_closed_violation_count": len(fail_closed_violations),
        "fail_closed_violation_case_ids": sorted(fail_closed_violations),
        "review_decision_counts": review_decisions,
        "human_review_complete": False,
        "paper_ready": False,
    }


def _write_freeze(
    *,
    rows: Sequence[StratifiedCarbonQLCase],
    truth: Sequence[ReferenceResult],
    audit: Mapping[str, Any],
    output_root: Path,
    freeze_id: str,
    manifest_extra: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if not _SAFE_ID.fullmatch(freeze_id) or "v2" not in freeze_id.lower():
        raise ValueError("freeze_id must be a safe v2 id")
    output_dir = output_root / freeze_id
    staging = output_root / f".{freeze_id}.staging"
    if output_dir.exists() or staging.exists():
        raise FileExistsError(output_dir if output_dir.exists() else staging)
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        case_rows = [row.to_dict() for row in rows]
        truth_rows = [
            {"case_id": row.case.case_id, "truth_basis": row.truth_basis, **result.to_dict()}
            for row, result in zip(rows, truth)
        ]
        cases_bytes = _jsonl_bytes(case_rows)
        truth_bytes = _jsonl_bytes(truth_rows)
        audit_payload = {
            "freeze_id": freeze_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            **dict(audit),
            "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
            "truth_sha256": hashlib.sha256(truth_bytes).hexdigest(),
        }
        audit_bytes = json.dumps(
            audit_payload, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        manifest = {
            "schema_version": "controlled-v2-naturalized",
            "freeze_id": freeze_id,
            "release_id": audit_payload["release_id"],
            "files": {
                "e4c_cases.jsonl": {
                    "sha256": hashlib.sha256(cases_bytes).hexdigest(),
                    "size_bytes": len(cases_bytes),
                },
                "e4c_truth.jsonl": {
                    "sha256": hashlib.sha256(truth_bytes).hexdigest(),
                    "size_bytes": len(truth_bytes),
                },
                "e4c_machine_audit.json": {
                    "sha256": hashlib.sha256(audit_bytes).hexdigest(),
                    "size_bytes": len(audit_bytes),
                },
            },
            **dict(manifest_extra or {}),
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        payloads = {
            "e4c_cases.jsonl": cases_bytes,
            "e4c_truth.jsonl": truth_bytes,
            "e4c_machine_audit.json": audit_bytes,
            "e4c_manifest.json": manifest_bytes,
        }
        for name, value in payloads.items():
            (staging / name).write_bytes(value)
        staging.rename(output_dir)
        return {
            "cases_jsonl": str((output_dir / "e4c_cases.jsonl").resolve()),
            "truth_jsonl": str((output_dir / "e4c_truth.jsonl").resolve()),
            "audit_json": str((output_dir / "e4c_machine_audit.json").resolve()),
            "manifest_json": str((output_dir / "e4c_manifest.json").resolve()),
        }
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def freeze_stratified_benchmark(
    context: CanonicalV2Context,
    output_root: Path,
    rewrite_path: Path,
    freeze_id: str | None = None,
) -> dict[str, str]:
    resolved_id = freeze_id or datetime.now().strftime("%Y%m%d_%H%M%S_v2")
    rows = build_stratified_cases(context, load_rewrite_specs(rewrite_path))
    truth = [reference_evaluate(row.case, context) for row in rows]
    audit = audit_stratified_cases(
        rows,
        context,
        truth_by_case_id={row.case.case_id: result for row, result in zip(rows, truth)},
    )
    graph_path = context.release_dir / "multigranular_carbon_kg.json"
    return _write_freeze(
        rows=rows,
        truth=truth,
        audit={
            **audit,
            "kg_dir": str(context.release_dir.resolve()),
            "graph_sha256": _hash_file(graph_path),
            "rewrite_spec_sha256": _hash_file(rewrite_path),
        },
        output_root=output_root,
        freeze_id=resolved_id,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kg-dir", type=Path, default=_DEFAULT_KG_DIR)
    parser.add_argument("--rewrite-spec", type=Path, default=_DEFAULT_REWRITE_PATH)
    parser.add_argument("--output-root", type=Path, default=_DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--freeze-id", default="")
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)

    context = load_canonical_v2_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    outputs = freeze_stratified_benchmark(
        context,
        args.output_root,
        args.rewrite_spec,
        freeze_id=args.freeze_id or None,
    )
    audit = json.loads(Path(outputs["audit_json"]).read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "freeze_id": audit["freeze_id"],
                "release_id": audit["release_id"],
                "case_count": audit["case_count"],
                "oracle_mismatch_count": audit["oracle_mismatch_count"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "RewriteSpec",
    "StratifiedCarbonQLCase",
    "_DEFAULT_KG_DIR",
    "_DEFAULT_OUTPUT_ROOT",
    "_DEFAULT_REWRITE_PATH",
    "audit_stratified_cases",
    "build_base_cases",
    "build_stratified_cases",
    "dual_source_global_ids",
    "freeze_stratified_benchmark",
    "heldout_reference_evaluate",
    "load_rewrite_specs",
    "validate_rewrite_specs",
]
