"""Deterministic semantic-coverage contract for CarbonQL programs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from dm2c_carbonql import CarbonQLProgram


@dataclass(frozen=True)
class SemanticRequirement:
    group_keys: tuple[str, ...]
    sources: tuple[str, ...]
    operations: tuple[str, ...]
    holes: tuple[str, ...]
    evidence_spans: Mapping[str, tuple[str, ...]]
    required_selector: str = ""
    rank_before_compare: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "required_group_keys": list(self.group_keys),
            "required_sources": list(self.sources),
            "required_operations": list(self.operations),
            "required_holes": list(self.holes),
            "evidence_spans": {
                key: list(value) for key, value in self.evidence_spans.items()
            },
        }
        if self.required_selector:
            payload["required_selector"] = self.required_selector
        if self.rank_before_compare:
            payload["required_operation_order"] = ["Rank", "Compare"]
        return payload


@dataclass(frozen=True)
class SemanticCoverageError:
    code: str
    feature: str
    evidence_spans: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "feature": self.feature,
            "evidence_spans": list(self.evidence_spans),
            "message": self.message,
        }


@dataclass(frozen=True)
class SemanticValidation:
    valid: bool
    requirement: SemanticRequirement
    errors: tuple[SemanticCoverageError, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "requirement": self.requirement.to_dict(),
            "errors": [error.to_dict() for error in self.errors],
        }


_GROUP_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "component",
        (
            r"\beach\s+(?:selected\s+)?(?:(?:BIM|building)\s+)?(?:component|item)s?\b",
            r"\bselected\s+(?:BIM\s+)?(?:components|items)\b",
            r"\bselected[\s-]+component[\s-]+contributors?\b",
            r"\bcomponent[\s-]+by[\s-]+",
            r"\bcomponent(?=\s*(?:,|and)\s*(?:material|energy))",
            r"\bBIM[\s-]+items\b",
            r"\bcomponents\b",
            r"\bitems\b",
        ),
    ),
    ("module", (r"\bmodular[\s-]+units?\b", r"\bmodules?\b")),
    (
        "component_type",
        (r"\bproduct[\s-]+types?\b", r"\bcomponent[\s-]+types?\b"),
    ),
    (
        "source_kind",
        (
            r"\bsource[\s-]+kinds?\b",
            r"\bemission[\s-]+source[\s-]+types?\b",
            r"\bseparate\b[^.?!]{0,80}\b(?:from|by)[\s-]+kinds?[\s-]+of"
            r"[\s-]+material[\s-]+and[\s-]+(?:from|by)[\s-]+electricity"
            r"[\s-]+(?:and|or)[\s-]+diesel\b",
        ),
    ),
    (
        "material",
        (
            r"\b(?:explicit|linked|individual)[\s-]+materials?\b",
        ),
    ),
    (
        "factor_keyword",
        (r"\bfactor[\s-]+keywords?\b",),
    ),
    (
        "factor_source",
        (r"\bfactor[\s-]+sources?\b",),
    ),
    (
        "carrier",
        (
            r"\benergy[\s-]+carriers?\b",
            r"\b(?:electricity|diesel)[/\s-]+carriers?\b",
            r"\belectricity[\s-]+(?:and|or)[\s-]+diesel\b",
        ),
    ),
    (
        "stage",
        (r"\bproduction[\s-]+stages?\b", r"\bfactory[\s-]+stages?\b"),
    ),
    (
        "process",
        (
            r"\bmanufacturing[\s-]+activit(?:y|ies)\b",
            r"\bfactory[\s-]+activit(?:y|ies)\b",
        ),
    ),
    ("resource", (r"\bproduction[\s-]+resources?\b",)),
)

_SOURCE_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "material",
        (
            r"\bembodied[\s-]+carbon\b",
            r"\bmaterial[\s-]+carbon\b",
            r"\bmaterial[\s-]+contributions?\b",
            r"\bmaterial(?=\s+and\s+production[\s-]+energy\s+carbon\b)",
        ),
    ),
    (
        "process",
        (
            r"\bfactory[\s-]+energy\b",
            r"\bprocess[\s-]+carbon\b",
            r"\bproduction[\s-]+energy\b",
            r"\bfactory[\s-]+carbon\b",
            r"\bfactory[\s-]+emissions?\b",
        ),
    ),
)

_ALL_SOURCE_ANCHORS = (
    r"\bcombined[\s-]+carbon(?:[\s-]+account)?\b",
    r"\bknown[\s-]+(?:carbon[\s-]+)?total\b",
)

_OPERATION_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Rank",
        (
            r"\brank(?:ed|ing)?\b",
            r"\btop\s+\d+\b",
            r"\b(?:largest|highest)[\s-]+contributors?\b",
            r"\bwhich\b[^.?!]{0,80}\b(?:greater|more)\b",
        ),
    ),
    (
        "Compare",
        (
            r"\bcompar(?:e|ed|ing|ison)\b",
            r"\bdifferences?\b",
            r"\bwhich\b[^.?!]{0,80}\b(?:greater|more)\b",
        ),
    ),
    (
        "Trace",
        (
            r"\btrace\b",
            r"\bevidence[\s-]+paths?\b",
        ),
    ),
)

_HOLE_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "target",
        (
            r"\bask\s+(?:me\s+)?which[\s-]+target\b",
            r"\btarget[\s-]+not[\s-]+specified\b",
            r"\bwithout[\s-]+specifying[\s-]+(?:the[\s-]+)?target\b",
        ),
    ),
    (
        "emission_source",
        (
            r"\bwithout[\s-]+assuming[\s-]+which[\s-]+source\b",
            r"\bask\s+(?:me\s+)?which[\s-]+source\b",
            r"\bunspecified[\s-]+source\b",
        ),
    ),
    (
        "grouping_dimension",
        (
            r"\bask\s+(?:me\s+)?which[\s-]+grouping\b",
            r"\bgrouping[\s-]+not[\s-]+specified\b",
            r"\bchoose[\s-]+the[\s-]+breakdown[\s-]+level\b",
        ),
    ),
)

_MATERIAL_GROUP_KEYS = frozenset({"material"})
_PROCESS_GROUP_KEYS = frozenset({"carrier", "stage", "process", "resource"})


def _match_spans(question: str, patterns: Sequence[str]) -> tuple[str, ...]:
    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, question, flags=re.IGNORECASE):
            matches.append((match.start(), match.group(0)))
    matches.sort(key=lambda item: (item[0], -len(item[1])))
    spans: list[str] = []
    occupied: list[tuple[int, int]] = []
    seen: set[str] = set()
    for start, span in matches:
        end = start + len(span)
        if any(start < occupied_end and end > occupied_start for occupied_start, occupied_end in occupied):
            continue
        normalized = span.casefold()
        if normalized not in seen:
            seen.add(normalized)
            spans.append(span)
            occupied.append((start, end))
    return tuple(spans)


def compile_semantic_requirements(
    question: str,
    selected_component_ids: Sequence[str],
) -> SemanticRequirement:
    evidence: dict[str, tuple[str, ...]] = {}
    group_keys: list[str] = []
    for feature, patterns in _GROUP_ANCHORS:
        spans = _match_spans(question, patterns)
        if spans:
            group_keys.append(feature)
            evidence[feature] = spans

    sources: list[str] = []
    for feature, patterns in _SOURCE_ANCHORS:
        spans = _match_spans(question, patterns)
        if spans:
            sources.append(feature)
            evidence[f"source:{feature}"] = spans

    if _MATERIAL_GROUP_KEYS.intersection(group_keys) and "material" not in sources:
        sources.append("material")
        evidence["source:material"] = tuple(
            span
            for key in group_keys
            if key in _MATERIAL_GROUP_KEYS
            for span in evidence[key]
        )
    if _PROCESS_GROUP_KEYS.intersection(group_keys) and "process" not in sources:
        sources.append("process")
        evidence["source:process"] = tuple(
            span
            for key in group_keys
            if key in _PROCESS_GROUP_KEYS
            for span in evidence[key]
        )

    all_spans = _match_spans(question, _ALL_SOURCE_ANCHORS)
    if all_spans:
        for source in ("material", "process"):
            if source not in sources:
                sources.append(source)
            evidence.setdefault(f"source:{source}", all_spans)

    operations: list[str] = []
    if group_keys:
        operations.append("Aggregate")
        evidence["operation:Aggregate"] = tuple(
            span for key in group_keys for span in evidence[key]
        )
    for operation, patterns in _OPERATION_ANCHORS:
        spans = _match_spans(question, patterns)
        if spans:
            operations.append(operation)
            evidence[f"operation:{operation}"] = spans

    holes: list[str] = []
    for dimension, patterns in _HOLE_ANCHORS:
        spans = _match_spans(question, patterns)
        if spans:
            holes.append(dimension)
            evidence[f"hole:{dimension}"] = spans

    if "emission_source" in holes:
        sources.clear()

    required_selector = "SelectClicked" if selected_component_ids else ""
    if required_selector:
        evidence["selector:SelectClicked"] = ("selected_component_ids",)

    return SemanticRequirement(
        group_keys=tuple(group_keys),
        sources=tuple(source for source in ("material", "process") if source in sources),
        operations=tuple(
            operation
            for operation in ("Aggregate", "Rank", "Compare", "Trace")
            if operation in operations
        ),
        holes=tuple(holes),
        evidence_spans=evidence,
        required_selector=required_selector,
        rank_before_compare="Rank" in operations and "Compare" in operations,
    )


def _program_sources(program: CarbonQLProgram) -> frozenset[str]:
    sources: set[str] = set()
    for step in program.steps:
        if step.op != "CarbonAtoms":
            continue
        raw = step.args.get("source")
        values = [raw] if isinstance(raw, str) else list(raw or ())
        if "all" in values:
            sources.update(("material", "process"))
        sources.update(value for value in values if value in {"material", "process"})
    return frozenset(sources)


def _program_group_keys(program: CarbonQLProgram) -> frozenset[str]:
    keys: set[str] = set()
    for step in program.steps:
        if step.op != "GroupBy":
            continue
        raw = step.args.get("keys")
        if isinstance(raw, list):
            keys.update(value for value in raw if isinstance(value, str) and value != "?")
    return frozenset(keys)


def _hole_is_realized(program: CarbonQLProgram, dimension: str) -> bool:
    if dimension not in {hole.dimension for hole in program.holes}:
        return False
    if dimension == "target":
        return any(
            step.op == "ResolveEntities" and step.args.get("entity_type") == "?"
            for step in program.steps
        )
    if dimension == "emission_source":
        return any(
            step.op == "CarbonAtoms"
            and (
                step.args.get("source") == "?"
                or (
                    isinstance(step.args.get("source"), list)
                    and "?" in step.args["source"]
                )
            )
            for step in program.steps
        )
    return any(
        step.op == "GroupBy"
        and (
            step.args.get("keys") == "?"
            or (
                isinstance(step.args.get("keys"), list)
                and "?" in step.args["keys"]
            )
        )
        for step in program.steps
    )


def validate_semantic_coverage(
    question: str,
    selected_component_ids: Sequence[str],
    program: CarbonQLProgram,
) -> SemanticValidation:
    requirement = compile_semantic_requirements(question, selected_component_ids)
    errors: list[SemanticCoverageError] = []
    first_op = program.steps[0].op if program.steps else ""

    if (
        requirement.required_selector
        and first_op not in {requirement.required_selector, "ResolveEntities"}
    ):
        feature = requirement.required_selector
        errors.append(
            SemanticCoverageError(
                code="selector_mismatch",
                feature=feature,
                evidence_spans=requirement.evidence_spans[f"selector:{feature}"],
                message=f"Selection context requires {feature} as the selector.",
            )
        )

    observed_sources = _program_sources(program)
    for source in requirement.sources:
        if source not in observed_sources:
            errors.append(
                SemanticCoverageError(
                    code="source_mismatch",
                    feature=source,
                    evidence_spans=requirement.evidence_spans[f"source:{source}"],
                    message=f"Question explicitly requires the {source} carbon source.",
                )
            )

    observed_keys = _program_group_keys(program)
    for key in requirement.group_keys:
        if key not in observed_keys:
            errors.append(
                SemanticCoverageError(
                    code="missing_group_key",
                    feature=key,
                    evidence_spans=requirement.evidence_spans[key],
                    message=f"Question span requires GroupBy key {key}.",
                )
            )

    observed_operations = tuple(step.op for step in program.steps)
    for operation in requirement.operations:
        if operation not in observed_operations:
            errors.append(
                SemanticCoverageError(
                    code="missing_operation",
                    feature=operation,
                    evidence_spans=requirement.evidence_spans[f"operation:{operation}"],
                    message=f"Question explicitly requires operation {operation}.",
                )
            )

    if (
        requirement.rank_before_compare
        and "Rank" in observed_operations
        and "Compare" in observed_operations
        and observed_operations.index("Rank") > observed_operations.index("Compare")
    ):
        errors.append(
            SemanticCoverageError(
                code="operation_order_error",
                feature="RankBeforeCompare",
                evidence_spans=(
                    *requirement.evidence_spans.get("operation:Rank", ()),
                    *requirement.evidence_spans.get("operation:Compare", ()),
                ),
                message="Rank must precede Compare for a greater/more comparison.",
            )
        )

    for dimension in requirement.holes:
        if not _hole_is_realized(program, dimension):
            errors.append(
                SemanticCoverageError(
                    code="missing_required_hole",
                    feature=dimension,
                    evidence_spans=requirement.evidence_spans[f"hole:{dimension}"],
                    message=f"Explicit uncertainty requires a typed {dimension} hole.",
                )
            )

    return SemanticValidation(
        valid=not errors,
        requirement=requirement,
        errors=tuple(errors),
    )
