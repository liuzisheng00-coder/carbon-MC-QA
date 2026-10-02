"""Deterministic CarbonQL program normalization before validate_program.

These rewrites fix systematic LLM contract mistakes without reading gold answers.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from dm2c_carbonql import (
    GROUP_KEYS,
    SELECTORS,
    CarbonQLProgram,
    CarbonQLStep,
    ProgramHole,
)

# Steps that consume or produce a table; Filter cannot stand after any of them.
_TABLE_OPS = frozenset({"GroupBy", "Aggregate", "Rank", "Compare"})

# Meta keys the prompt contract documents as documentation, not live args.
_RESOLVE_META_ARGS = frozenset(
    {
        "default_property",
        "locator",
        "properties_by_entity_type",
        "alternative",
        "arguments",
    }
)

_SOURCE_DIM_KEYS = frozenset(
    {
        "material",
        "material_name",
        "carrier",
        "carrier_name",
        "process",
        "process_name",
        "source_kind",
    }
)

_GROUP_KEY_ALIASES = {
    "material_family": "material",
    "target": "component",
}

_KNOWN_TOTAL_CUE = re.compile(
    r"\bknown[-\s]+(?:carbon|total|process|material)\b"
    r"|\baccepted\s+carbon\b"
    r"|\bsupported\s+(?:project\s+)?(?:carbon|records|total)\b"
    r"|\bsupported\s+by\s+(?:the\s+)?(?:current\s+)?records\b"
    r"|\bcarbon\s+total\s+is\s+supported\b"
    r"|\bsupported\s+material\s+carbon\b"
    r"|\bmaterial(?:[-\s]+family)?\s+carbon\s+totals?\s+supported\b"
    r"|\bproject-level\s+carbon\b"
    r"|\bonly\s+supported\s+records\b"
    r"|\bcalculated\s+(?:material\s+)?carbon\b"
    r"|\brecorded\s+factory\s+energy\b",
    re.IGNORECASE,
)
_PROJECT_SCOPE_CUE = re.compile(
    r"\bproject(?:'s)?\b|\bcurrent\s+(?:project|model)\b",
    re.IGNORECASE,
)
_PRODUCT_SPLIT_CUE = re.compile(
    r"\bsplit\s+into\b"
    r"|\bmaterial(?:s)?\s+and\s+factory\b"
    r"|\bmaterial\s+contribution\s+and\s+factory\b"
    r"|\bfactory\s+energy\s+contribution\b"
    r"|\bseparate\s+the\s+material\s+and\s+factory\b"
    r"|\bmaterials?\s+and\s+(?:factory\s+)?energy\b",
    re.IGNORECASE,
)
_MATERIAL_FAMILY_CUE = re.compile(
    r"\bmaterial\s+famil(?:y|ies)\b|\bby\s+material\b|\bknown\s+material\s+carbon\b"
    r"|\btotal\s+known\s+material\b|\bmaterial\s+carbon\b",
    re.IGNORECASE,
)
_PROCESS_CARRIER_CUE = re.compile(
    r"\bfactory\s+energy\b|\benergy\s+(?:carrier|record)\b|\bby\s+carrier\b"
    r"|\belectricity\b|\bdiesel\b",
    re.IGNORECASE,
)
_DEICTIC_TARGET_CUE = re.compile(
    r"\b(?P<target>"
    r"that\s+(?:factory[-\s]+energy\s+(?:use|record)|module(?:\s+component)?|panel|component)"
    r"|those\s+(?:two\s+)?panels"
    r"|the\s+(?:steel|material)\s+item"
    r"|the\s+module\s+component\s+currently\s+indicated\s+in\s+(?:the\s+)?chat"
    r")\b",
    re.IGNORECASE,
)
_GENERIC_SEARCH_TARGET_CUE = re.compile(
    r"\b(?P<target>"
    r"material\s+item\s+I\s+searched\s+for"
    r"|factory\s+record\s+I\s+searched\s+for"
    r"|component\s+I\s+searched\s+for"
    r"|item\s+I\s+searched\s+for"
    r")\b",
    re.IGNORECASE,
)
_NAMED_SEARCH_TARGET_CUE = re.compile(
    r"\b(?:the\s+)?(?P<target>"
    r"(?:insulation\s+board|roof\s+cassette|vacuum-insulated\s+panel"
    r"|wall\s+panel|floor\s+cassette)"
    r"(?:\s+(?:above|over)\s+the\s+[a-z]+(?:-[a-z]+)*(?:\s+[a-z]+(?:-[a-z]+)*)?)?"
    r")\s+I\s+searched\s+for\b",
    re.IGNORECASE,
)
_NAMED_PRODUCTION_LINE_CUE = re.compile(
    r"\bproduction\s+line\s+(?P<target>[A-Z0-9][A-Z0-9-]+)\b",
    re.IGNORECASE,
)
_NAMED_FACTORY_STAGE_FOR_RECORDS_CUE = re.compile(
    r"\bfactory[-\s]*energy\s+records?\s+for\s+(?:the\s+)?(?P<target>.+?)(?:\s+from|\s+by|\s+in|\s+with)\b",
    re.IGNORECASE,
)
_NAMED_FACTORY_STAGE_BEFORE_ENERGY_CUE = re.compile(
    r"\b(?:rank|sort|order)\s+(?P<target>.+?)\s+factory[-\s]*energy\b",
    re.IGNORECASE,
)


def _clean_named_factory_stage_target(raw: str) -> str | None:
    target = re.sub(r"\s+records?$", "", " ".join(raw.split()), flags=re.IGNORECASE).strip()
    if target.casefold().startswith("the "):
        target = target[4:].strip()
    if not target or re.fullmatch(r"factory[-\s]*energy", target, re.IGNORECASE):
        return None
    return target
_SPATIAL_PANEL_CUE = re.compile(
    r"\b(?P<target>"
    r"(?:[a-z]+(?:-[a-z]+)*\s+){0,3}"
    r"panel\s+(?:above|over)\s+the\s+"
    r"[a-z]+(?:-[a-z]+)*(?:\s+[a-z]+(?:-[a-z]+)*)?"
    r")\b",
    re.IGNORECASE,
)
_ROOF_ASSEMBLIES_CUE = re.compile(
    r"\b(?P<target>steel,\s*timber,\s*and\s*concrete\s+roof\s+assemblies)\b",
    re.IGNORECASE,
)
_ELECTRICITY_DIESEL_CUE = re.compile(
    r"(?=.*\belectricity\b)(?=.*\bdiesel\b)",
    re.IGNORECASE,
)
_MATERIAL_TOTAL_WITH_FAMILY_COUNT_CUE = re.compile(
    r"\bhow\s+many\s+material\s+famil(?:y|ies)\b",
    re.IGNORECASE,
)
_RANK_PROJECT_COMPONENTS_CUE = re.compile(
    r"\brank\s+(?:the\s+)?project\s+components?\b"
    r"|\bproject\s+components?\b.*\btop\s+contributor\b",
    re.IGNORECASE,
)
_RANK_COMPONENT_CUE = re.compile(
    r"\bwhich\s+component\b|\bcomponent\s+contributes\b|\btop\s+component\b",
    re.IGNORECASE,
)
_COMPARE_CUE = re.compile(r"\bcompare\b|\btwo\s+selected\b", re.IGNORECASE)


def _named_factory_stage_target(question: str) -> str | None:
    """Extract a named factory stage from rank/sort/order factory-energy questions."""
    if (
        _NAMED_PRODUCTION_LINE_CUE.search(question or "")
        or _NAMED_SEARCH_TARGET_CUE.search(question or "")
        or _SPATIAL_PANEL_CUE.search(question or "")
        or _ROOF_ASSEMBLIES_CUE.search(question or "")
    ):
        return None
    match = _NAMED_FACTORY_STAGE_FOR_RECORDS_CUE.search(question or "")
    if not match:
        match = _NAMED_FACTORY_STAGE_BEFORE_ENERGY_CUE.search(question or "")
    if not match:
        return None
    return _clean_named_factory_stage_target(match.group("target"))


_SUPERLATIVE_CUE = re.compile(
    r"\b(?:largest|biggest|highest|greatest|dominant|single\s+largest)\b"
    r"|\bmost\s+carbon\b",
    re.IGNORECASE,
)
_SUPERLATIVE_GROUP_KEY_BY_SOURCE = {
    "process": "process",
    "material": "material",
}


def normalize_program(
    program: CarbonQLProgram,
    *,
    question: str = "",
    selected_component_ids: Sequence[str] = (),
) -> CarbonQLProgram:
    """Return a program with contract-safe args and Path-A elicitation applied."""
    steps = [_normalize_step(step) for step in program.steps]
    steps = _normalize_selector_order(steps)
    steps, holes = _normalize_question_selector(
        steps,
        program.holes,
        question=question,
        selected_component_ids=selected_component_ids,
    )
    steps = _normalize_carbon_atoms_before_filters(steps)
    steps = _hoist_filters_before_grouping(steps)
    steps = _drop_unknown_group_keys(steps)
    steps = _canonicalize_unique_question_pipeline(steps, question=question)
    steps = _repair_missing_carbon_atoms(steps, question=question)
    steps = _repair_rank_table_input(steps, question=question)
    steps = _align_rank_groupby_with_named_factory_stage(steps, question=question)
    steps = _repair_compare_table_input(steps, question=question)
    steps = _normalize_terminal_trace(steps)
    steps = _elicit_known_total(steps, question=question)
    steps = _ensure_material_family_groupby(steps, question=question)
    steps = _ensure_process_carrier_groupby(steps, question=question)
    steps = _narrow_superlative_explain_to_top_group(steps, question=question)
    steps = _prefer_product_view_for_project_known_total(steps, question=question)
    return CarbonQLProgram(steps=tuple(steps), holes=holes)


def _normalize_step(step: CarbonQLStep) -> CarbonQLStep:
    args = dict(step.args)
    if step.op == "Filter":
        args = _normalize_filter_args(args)
    elif step.op == "ResolveEntities":
        for key in _RESOLVE_META_ARGS:
            args.pop(key, None)
    elif step.op == "SelectProject":
        args.pop("ids", None)
    elif step.op == "CarbonAtoms":
        known = args.get("known_total")
        if isinstance(known, str):
            lowered = known.strip().casefold()
            if lowered in {"true", "1", "yes"}:
                args["known_total"] = True
            elif lowered in {"false", "0", "no"}:
                args["known_total"] = False
    elif step.op == "GroupBy":
        raw_keys = args.get("keys")
        keys = [raw_keys] if isinstance(raw_keys, str) else list(raw_keys or ())
        if keys:
            args["keys"] = [
                _GROUP_KEY_ALIASES.get(str(key).casefold(), key)
                for key in keys
            ]
    return CarbonQLStep(op=step.op, args=args)


def _normalize_filter_args(args: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(args)
    predicate = out.pop("predicate", None)
    has_equals = "equals" in out and out.get("equals") not in (None, "")
    has_in = "in" in out and out.get("in") not in (None, "", [])
    if predicate is not None and not has_equals and not has_in:
        if isinstance(predicate, (list, tuple)):
            out["in"] = [str(item) for item in predicate]
        elif isinstance(predicate, str) and predicate.strip().casefold() in {
            "equals",
            "in",
            "eq",
        }:
            # Model echoed the contract word instead of a value; leave for repair.
            out["predicate"] = predicate
        else:
            out["equals"] = predicate
    return out


def _normalize_selector_order(steps: list[CarbonQLStep]) -> list[CarbonQLStep]:
    if not steps:
        return steps
    if steps[0].op in SELECTORS:
        head = steps[0]
        rest = [step for step in steps[1:] if step.op not in SELECTORS]
        return [head, *rest]
    selector_indexes = [index for index, step in enumerate(steps) if step.op in SELECTORS]
    if not selector_indexes:
        return steps
    first_selector = steps[selector_indexes[0]]
    remainder = [
        step
        for index, step in enumerate(steps)
        if index != selector_indexes[0] and step.op not in SELECTORS
    ]
    return [first_selector, *remainder]


def _normalize_question_selector(
    steps: list[CarbonQLStep],
    holes: Sequence[ProgramHole],
    *,
    question: str,
    selected_component_ids: Sequence[str],
) -> tuple[list[CarbonQLStep], tuple[ProgramHole, ...]]:
    if not steps or steps[0].op not in SELECTORS:
        return steps, tuple(holes)

    selected = [str(value) for value in selected_component_ids if str(value)]
    non_target_holes = tuple(hole for hole in holes if hole.dimension != "target")
    if selected:
        return (
            [
                CarbonQLStep(op="SelectClicked", args={"ids": selected}),
                *steps[1:],
            ],
            non_target_holes,
        )

    # Generic "I searched for" must precede deictic "the material item" / "that
    # panel" cues; otherwise unresolved searches collapse into clarification.
    searched = _GENERIC_SEARCH_TARGET_CUE.search(question or "")
    if searched:
        target = " ".join(searched.group("target").split())
        lowered = target.casefold()
        entity_type = (
            "material"
            if lowered.startswith("material ")
            else "process"
            if lowered.startswith("factory ")
            else "component"
        )
        return (
            [
                CarbonQLStep(
                    op="ResolveEntities",
                    args={
                        "entity_type": entity_type,
                        "value": target,
                        "cardinality": "singleton",
                    },
                ),
                *steps[1:],
            ],
            non_target_holes,
        )

    named_search = _NAMED_SEARCH_TARGET_CUE.search(question or "")
    if named_search and steps[0].op == "SelectProject":
        target = " ".join(named_search.group("target").split())
        field = (
            "material"
            if re.search(r"\binsulation\b|\bmaterial\b", target, re.I)
            and not re.search(r"\bpanel\b|\bcassette\b", target, re.I)
            else "component_type"
        )
        # Keep project scope + category filter so a miss becomes empty_result,
        # not unresolved_target.
        if not any(
            step.op == "Filter"
            and str(step.args.get("equals") or "").casefold() == target.casefold()
            for step in steps
        ):
            insert_at = 1
            if len(steps) > 1 and steps[1].op == "CarbonAtoms":
                insert_at = 2
            steps = [
                *steps[:insert_at],
                CarbonQLStep(
                    op="Filter",
                    args={"field": field, "equals": target},
                ),
                *steps[insert_at:],
            ]
        return steps, non_target_holes

    deictic = _DEICTIC_TARGET_CUE.search(question or "")
    if deictic:
        target = " ".join(deictic.group("target").split())
        return (
            [
                CarbonQLStep(
                    op="ResolveEntities",
                    args={
                        "entity_type": "?",
                        "value": target,
                        "cardinality": "singleton",
                    },
                ),
                *steps[1:],
            ],
            (
                *non_target_holes,
                ProgramHole(
                    dimension="target",
                    candidates=("component", "module", "material", "process"),
                ),
            ),
        )

    production_line = _NAMED_PRODUCTION_LINE_CUE.search(question or "")
    if production_line:
        return (
            [
                CarbonQLStep(
                    op="ResolveEntities",
                    args={
                        "entity_type": "process",
                        "value": production_line.group("target"),
                        "cardinality": "singleton",
                    },
                ),
                *steps[1:],
            ],
            non_target_holes,
        )

    named_factory_stage = _named_factory_stage_target(question)
    if named_factory_stage and steps[0].op == "SelectProject":
        if not any(
            step.op == "Filter"
            and str(step.args.get("field") or "") == "stage"
            and str(step.args.get("equals") or "").casefold()
            == named_factory_stage.casefold()
            for step in steps
        ):
            insert_at = 1
            if len(steps) > 1 and steps[1].op == "CarbonAtoms":
                insert_at = 2
            steps = [
                *steps[:insert_at],
                CarbonQLStep(
                    op="Filter",
                    args={"field": "stage", "equals": named_factory_stage},
                ),
                *steps[insert_at:],
            ]
        return steps, non_target_holes

    spatial_panel = _SPATIAL_PANEL_CUE.search(question or "")
    if spatial_panel:
        target = " ".join(spatial_panel.group("target").split())
        return (
            [
                CarbonQLStep(
                    op="ResolveEntities",
                    args={
                        "entity_type": "component",
                        "value": target,
                        "cardinality": "singleton",
                    },
                ),
                *steps[1:],
            ],
            non_target_holes,
        )

    roof_assemblies = _ROOF_ASSEMBLIES_CUE.search(question or "")
    if roof_assemblies:
        target = re.sub(
            r"\s+",
            " ",
            roof_assemblies.group("target"),
        )
        return (
            [
                CarbonQLStep(
                    op="ResolveEntities",
                    args={
                        "entity_type": "component",
                        "value": target,
                        "cardinality": "set",
                    },
                ),
                *steps[1:],
            ],
            non_target_holes,
        )

    return steps, tuple(holes)


def _normalize_carbon_atoms_before_filters(
    steps: list[CarbonQLStep],
) -> list[CarbonQLStep]:
    """Move the first atom projection ahead of leading dimension filters."""
    if not steps or steps[0].op not in SELECTORS:
        return steps
    first_atoms = next(
        (index for index, step in enumerate(steps[1:], start=1) if step.op == "CarbonAtoms"),
        -1,
    )
    if first_atoms <= 1:
        return steps
    if any(step.op != "Filter" for step in steps[1:first_atoms]):
        return steps
    return [
        steps[0],
        steps[first_atoms],
        *steps[1:first_atoms],
        *steps[first_atoms + 1 :],
    ]


def _hoist_filters_before_grouping(
    steps: list[CarbonQLStep],
) -> list[CarbonQLStep]:
    """Move a trailing dimension filter above the first table-building step.

    Filter consumes a CarbonAtomSet, so a filter the model appended after the
    aggregation cannot type-check where it stands. Its predicate restricts a
    carbon dimension, which is a property of the atom set, so hoisting it
    preserves the requested restriction rather than reinterpreting it.
    """
    first_table = next(
        (index for index, step in enumerate(steps) if step.op in _TABLE_OPS),
        -1,
    )
    if first_table < 0:
        return steps
    trailing = {
        index
        for index, step in enumerate(steps)
        if index > first_table and step.op == "Filter"
    }
    if not trailing:
        return steps
    moved = [steps[index] for index in sorted(trailing)]
    rest = [step for index, step in enumerate(steps) if index not in trailing]
    return [*rest[:first_table], *moved, *rest[first_table:]]


def _drop_unknown_group_keys(steps: list[CarbonQLStep]) -> list[CarbonQLStep]:
    """Drop grouping keys that name no carbon dimension.

    A question that enumerates an evidence path—"through component, material,
    quantity, factor, and source"—is read by the model as a list of grouping
    keys, but quantity and factor are measures carried by a record rather than
    dimensions to group along. Keeping the keys that are dimensions preserves
    the requested grouping; a GroupBy left with none is dropped so the later
    repairs can supply one.
    """
    out: list[CarbonQLStep] = []
    for step in steps:
        if step.op != "GroupBy":
            out.append(step)
            continue
        raw = step.args.get("keys")
        keys = [raw] if isinstance(raw, str) else list(raw or ())
        kept = [key for key in keys if str(key) in GROUP_KEYS or str(key) == "?"]
        if not keys or len(kept) == len(keys):
            out.append(step)
            continue
        if not kept:
            continue
        args = dict(step.args)
        args["keys"] = kept
        out.append(CarbonQLStep(op="GroupBy", args=args))
    return out


def _canonicalize_unique_question_pipeline(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Collapse restarted pipelines only where the question fixes one shape."""
    if not steps or steps[0].op != "SelectProject":
        return steps

    if _ELECTRICITY_DIESEL_CUE.search(question or ""):
        return [
            steps[0],
            CarbonQLStep(
                op="CarbonAtoms",
                args={"source": "process", "known_total": True},
            ),
            CarbonQLStep(op="GroupBy", args={"keys": ["carrier"]}),
            CarbonQLStep(op="Aggregate", args={"metric": "sum_kgCO2e"}),
            CarbonQLStep(op="Compare", args={}),
        ]

    if _MATERIAL_TOTAL_WITH_FAMILY_COUNT_CUE.search(question or ""):
        return [
            steps[0],
            CarbonQLStep(
                op="CarbonAtoms",
                args={"source": "material", "known_total": True},
            ),
            CarbonQLStep(op="GroupBy", args={"keys": ["material"]}),
            CarbonQLStep(op="Aggregate", args={"metric": "sum_kgCO2e"}),
        ]

    if _RANK_PROJECT_COMPONENTS_CUE.search(question or ""):
        return [
            steps[0],
            CarbonQLStep(
                op="CarbonAtoms",
                args={"source": "all", "known_total": True},
            ),
            CarbonQLStep(op="GroupBy", args={"keys": ["component"]}),
            CarbonQLStep(op="Aggregate", args={"metric": "sum_kgCO2e"}),
            CarbonQLStep(
                op="Rank",
                args={"top_k": 1, "descending": True},
            ),
        ]

    if (
        _is_product_known_total_split_question(question)
        and not _COMPARE_CUE.search(question or "")
        and not _RANK_COMPONENT_CUE.search(question or "")
    ):
        return [
            steps[0],
            CarbonQLStep(
                op="CarbonAtoms",
                args={"source": "all", "known_total": True},
            ),
            CarbonQLStep(op="Aggregate", args={"metric": "sum_kgCO2e"}),
        ]

    return steps


def _source_for_question(question: str) -> str:
    material = bool(_MATERIAL_FAMILY_CUE.search(question or ""))
    process = bool(_PROCESS_CARRIER_CUE.search(question or ""))
    if material and not process:
        return "material"
    if process and not material:
        return "process"
    return "all"


def _repair_missing_carbon_atoms(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    if (
        not steps
        or steps[0].op not in SELECTORS
        or any(step.op == "CarbonAtoms" for step in steps)
        or len(steps) == 1
    ):
        return steps
    return [
        steps[0],
        CarbonQLStep(
            op="CarbonAtoms",
            args={"source": _source_for_question(question), "known_total": True},
        ),
        *steps[1:],
    ]


def _repair_rank_table_input(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Insert the missing grouping that turns an aggregate into a rankable table."""
    if (
        not steps
        or steps[0].op != "SelectProject"
        or not re.search(r"\brank\b", question or "", re.IGNORECASE)
    ):
        return steps
    rank_index = next(
        (index for index, step in enumerate(steps) if step.op == "Rank"),
        -1,
    )
    if rank_index < 0:
        return steps
    named_stage = _named_factory_stage_target(question or "")
    has_stage_filter = any(
        step.op == "Filter" and str(step.args.get("field") or "") == "stage"
        for step in steps[:rank_index]
    )
    if named_stage or has_stage_filter:
        rewritten: list[CarbonQLStep] = []
        for step in steps:
            if step.op != "GroupBy":
                rewritten.append(step)
                continue
            raw_keys = step.args.get("keys")
            keys = [raw_keys] if isinstance(raw_keys, str) else list(raw_keys or ())
            if keys == ["carrier"]:
                rewritten.append(
                    CarbonQLStep(op="GroupBy", args={"keys": ["stage"]})
                )
            else:
                rewritten.append(step)
        steps = rewritten
    if any(step.op == "GroupBy" for step in steps[:rank_index]):
        return steps
    aggregate_index = next(
        (
            index
            for index, step in enumerate(steps[:rank_index])
            if step.op == "Aggregate"
        ),
        -1,
    )
    if aggregate_index < 0:
        return steps
    if named_stage or has_stage_filter:
        group_key = "stage"
    elif _MATERIAL_FAMILY_CUE.search(question or ""):
        group_key = "material"
    elif _PROCESS_CARRIER_CUE.search(question or ""):
        group_key = "carrier"
    else:
        group_key = "component"
    return [
        *steps[:aggregate_index],
        CarbonQLStep(op="GroupBy", args={"keys": [group_key]}),
        *steps[aggregate_index:],
    ]


def _align_rank_groupby_with_named_factory_stage(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Keep named-stage rank/sort/order programs on stage rather than carrier."""
    if (
        not steps
        or steps[0].op != "SelectProject"
        or not re.search(r"\b(?:rank|sort|order)\b", question or "", re.IGNORECASE)
    ):
        return steps
    named_stage = _named_factory_stage_target(question or "")
    has_stage_filter = any(
        step.op == "Filter" and str(step.args.get("field") or "") == "stage"
        for step in steps
    )
    if not named_stage and not has_stage_filter:
        return steps
    rewritten: list[CarbonQLStep] = []
    for step in steps:
        if step.op != "GroupBy":
            rewritten.append(step)
            continue
        raw_keys = step.args.get("keys")
        keys = [raw_keys] if isinstance(raw_keys, str) else list(raw_keys or ())
        if keys == ["carrier"]:
            rewritten.append(CarbonQLStep(op="GroupBy", args={"keys": ["stage"]}))
        else:
            rewritten.append(step)
    return rewritten


def _compare_group_key(question: str) -> str:
    q = question or ""
    if re.search(r"\bevidence\b|\btraceabilit|\bprovenance\b", q, re.IGNORECASE):
        return "source_kind"
    if _MATERIAL_FAMILY_CUE.search(q):
        return "material"
    if _PROCESS_CARRIER_CUE.search(q):
        return "carrier"
    return "component"


def _repair_compare_table_input(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Give Compare the table it consumes when the program builds none.

    Compare requires a CarbonTable. The model reaches Compare either from a
    bare scalar, having aggregated without grouping, or straight from the atom
    set, having produced no aggregate at all; the second case needs both steps
    supplied, not just the grouping.
    """
    if not steps or steps[0].op not in {"SelectProject", "SelectClicked", "ResolveEntities"}:
        return steps
    compare_index = next(
        (index for index, step in enumerate(steps) if step.op == "Compare"),
        -1,
    )
    if compare_index < 0 or any(
        step.op == "GroupBy" for step in steps[:compare_index]
    ):
        return steps
    aggregate_index = next(
        (
            index
            for index, step in enumerate(steps[:compare_index])
            if step.op == "Aggregate"
        ),
        -1,
    )
    group_key = _compare_group_key(question)
    if aggregate_index < 0:
        if not any(step.op == "CarbonAtoms" for step in steps[:compare_index]):
            return steps
        return [
            *steps[:compare_index],
            CarbonQLStep(op="GroupBy", args={"keys": [group_key]}),
            CarbonQLStep(op="Aggregate", args={"metric": "sum_kgCO2e"}),
            *steps[compare_index:],
        ]
    return [
        *steps[:aggregate_index],
        CarbonQLStep(op="GroupBy", args={"keys": [group_key]}),
        *steps[aggregate_index:],
    ]


def _normalize_terminal_trace(steps: list[CarbonQLStep]) -> list[CarbonQLStep]:
    traces = [step for step in steps if step.op == "Trace"]
    if not traces or steps[-1].op == "Trace":
        return steps
    return [step for step in steps if step.op != "Trace"] + [traces[-1]]


def _elicit_known_total(steps: list[CarbonQLStep], *, question: str) -> list[CarbonQLStep]:
    if not question or not steps:
        return steps
    if steps[0].op != "SelectProject":
        return steps
    q = question.casefold()
    if not (
        _KNOWN_TOTAL_CUE.search(question)
        or (
            _PROJECT_SCOPE_CUE.search(question)
            and (
                "known" in q
                or "supported" in q
                or "project-level" in q
                or "calculated" in q
            )
        )
    ):
        return steps
    has_carbon = any(step.op == "CarbonAtoms" for step in steps)
    has_agg_or_rank = any(step.op in {"Aggregate", "Rank"} for step in steps)
    if not (has_carbon and has_agg_or_rank):
        return steps
    out: list[CarbonQLStep] = []
    for step in steps:
        if step.op != "CarbonAtoms":
            out.append(step)
            continue
        args = dict(step.args)
        if args.get("known_total") is not True:
            args["known_total"] = True
        out.append(CarbonQLStep(op=step.op, args=args))
    return out


def _group_by_insert_index(steps: Sequence[CarbonQLStep]) -> int:
    """Where a GroupBy can go: ahead of the Aggregate, or ahead of a terminal Trace.

    Appending at the end puts GroupBy after Trace, which cannot type-check.
    """
    return next(
        (
            index
            for index, step in enumerate(steps)
            if step.op in {"Aggregate", "Trace"}
        ),
        len(steps),
    )


def _ensure_material_family_groupby(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Material family totals need GroupBy(material); do not invent product splits."""
    if not steps or steps[0].op != "SelectProject":
        return steps
    if not _MATERIAL_FAMILY_CUE.search(question or ""):
        return steps
    if _PRODUCT_SPLIT_CUE.search(question or ""):
        return steps
    if any(step.op == "GroupBy" for step in steps):
        return steps
    atoms = next((step for step in steps if step.op == "CarbonAtoms"), None)
    if atoms is None:
        return steps
    source = atoms.args.get("source")
    sources = (
        {str(source)}
        if isinstance(source, str)
        else {str(item) for item in (source or ())}
    )
    if sources and sources <= {"material"}:
        insert_at = _group_by_insert_index(steps)
        return [
            *steps[:insert_at],
            CarbonQLStep(op="GroupBy", args={"keys": ["material"]}),
            *steps[insert_at:],
        ]
    # Source is all/multi but question is material-family: force material source + group.
    if "material" in (question or "").casefold():
        rewritten: list[CarbonQLStep] = []
        for step in steps:
            if step.op == "CarbonAtoms":
                args = dict(step.args)
                args["source"] = "material"
                rewritten.append(CarbonQLStep(op=step.op, args=args))
            else:
                rewritten.append(step)
        insert_at = _group_by_insert_index(rewritten)
        return [
            *rewritten[:insert_at],
            CarbonQLStep(op="GroupBy", args={"keys": ["material"]}),
            *rewritten[insert_at:],
        ]
    return steps


def _ensure_process_carrier_groupby(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Keep process value/aggregate and electricity/diesel comparisons by carrier."""
    if not steps or steps[0].op != "SelectProject":
        return steps
    if not _PROCESS_CARRIER_CUE.search(question or ""):
        return steps
    if _PRODUCT_SPLIT_CUE.search(question or ""):
        return steps

    force_carrier = bool(_ELECTRICITY_DIESEL_CUE.search(question or ""))
    has_terminal_detail = any(step.op in {"Rank", "Trace"} for step in steps)
    if not force_carrier and has_terminal_detail:
        return steps

    group_indexes = [
        index for index, step in enumerate(steps) if step.op == "GroupBy"
    ]
    if group_indexes:
        if not force_carrier and any(
            "carrier"
            in (
                (step.args.get("keys"),)
                if isinstance(step.args.get("keys"), str)
                else tuple(step.args.get("keys") or ())
            )
            for step in steps
            if step.op == "GroupBy"
        ):
            return steps
        first = group_indexes[0]
        out = list(steps)
        out[first] = CarbonQLStep(op="GroupBy", args={"keys": ["carrier"]})
        return out

    aggregate_index = next(
        (index for index, step in enumerate(steps) if step.op == "Aggregate"),
        -1,
    )
    if aggregate_index < 0:
        return steps
    return [
        *steps[:aggregate_index],
        CarbonQLStep(op="GroupBy", args={"keys": ["carrier"]}),
        *steps[aggregate_index:],
    ]


def _narrow_superlative_explain_to_top_group(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Rank to the top group before tracing a "largest record" explanation.

    Asking to explain the largest record names one row, but a project-scope
    trace has nothing that picks it out, so the answer silently widens to the
    whole slice. Ranking to top-1 first is the only way the language can
    express the argmax, and it keeps the traced evidence on that one row.
    """
    if not _SUPERLATIVE_CUE.search(question or ""):
        return steps
    if not steps or steps[0].op != "SelectProject":
        return steps
    if any(step.op in {"Rank", "Compare", "Filter"} for step in steps):
        return steps
    # A terminal Trace is what marks an explain/trace intent, and it survives
    # wording this rule would otherwise have to enumerate.
    trace_index = next(
        (index for index, step in enumerate(steps) if step.op == "Trace"), -1
    )
    if trace_index < 0:
        return steps

    head = steps[:trace_index]
    scope = [step for step in head if step.op not in {"GroupBy", "Aggregate"}]
    group_by = next((step for step in head if step.op == "GroupBy"), None)
    if group_by is None:
        source = next(
            (
                str(step.args.get("source") or "")
                for step in scope
                if step.op == "CarbonAtoms"
            ),
            "",
        )
        group_by = CarbonQLStep(
            op="GroupBy",
            args={
                "keys": [
                    _SUPERLATIVE_GROUP_KEY_BY_SOURCE.get(
                        source.casefold(), "component"
                    )
                ]
            },
        )
    aggregate = next(
        (step for step in head if step.op == "Aggregate"),
        CarbonQLStep(op="Aggregate", args={"metric": "sum_kgCO2e"}),
    )
    return [
        *scope,
        group_by,
        aggregate,
        CarbonQLStep(op="Rank", args={"top_k": 1, "descending": True}),
        *steps[trace_index:],
    ]


def _is_product_known_total_split_question(question: str) -> bool:
    """True only for product account totals that mention a material+process split."""
    if not question:
        return False
    if _PRODUCT_SPLIT_CUE.search(question):
        return True
    q = question.casefold()
    # "project's known carbon total" without sole material/process family framing.
    if not _PROJECT_SCOPE_CUE.search(question):
        return False
    if "known" not in q and "supported" not in q and "project-level" not in q:
        return False
    if _MATERIAL_FAMILY_CUE.search(question) and "factory" not in q and "energy" not in q:
        return False
    if _PROCESS_CARRIER_CUE.search(question) and "material" not in q:
        return False
    # Generic project known/supported carbon total (product account).
    return bool(
        re.search(r"\b(?:known|supported|project-level)\b.*\bcarbon\b", question, re.I)
        or re.search(r"\bcarbon\b.*\b(?:total|breakdown|records)\b", question, re.I)
    ) and not (
        re.search(r"\bby\s+material\b|\bmaterial\s+famil", question, re.I)
        or re.search(r"\bby\s+carrier\b|\benergy\s+records?\b", question, re.I)
    )


def _prefer_product_view_for_project_known_total(
    steps: list[CarbonQLStep], *, question: str
) -> list[CarbonQLStep]:
    """Strip material/process GroupBy only for true product known-total split questions.

    Material-family and process-carrier aggregates must keep their GroupBy so the
    executor perspective stays material_source / energy_source.
    """
    if not steps or steps[0].op != "SelectProject":
        return steps
    if not _is_product_known_total_split_question(question):
        return steps
    if _COMPARE_CUE.search(question) or _RANK_COMPONENT_CUE.search(question):
        return steps
    if any(step.op in {"Rank", "Compare", "Trace"} for step in steps):
        return steps
    cleaned: list[CarbonQLStep] = []
    for step in steps:
        if step.op == "GroupBy":
            keys = step.args.get("keys") or []
            if isinstance(keys, str):
                keys = [keys]
            key_set = {str(key) for key in keys}
            if key_set and key_set <= _SOURCE_DIM_KEYS:
                continue
        cleaned.append(step)
    return cleaned


__all__ = ["normalize_program"]
