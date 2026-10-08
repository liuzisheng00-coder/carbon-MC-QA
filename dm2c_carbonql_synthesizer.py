"""LLM-to-CarbonQL synthesis for E4b/E4c variants V1-V4."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from dm2c_carbonql import (
    AGGREGATE_METRICS,
    CARBON_SOURCES,
    GROUP_KEYS,
    OPERATIONS,
    CarbonQLProgram,
    CarbonQLValidationError,
    GraphSchema,
    ValidationResult,
    validate_program,
)
from dm2c_carbonql_program_normalize import normalize_program
from dm2c_carbonql_semantic import SemanticValidation, validate_semantic_coverage


SIMPLE_EXAMPLES = (
    {
        "question": "What known carbon total is available for the project?",
        "program": {
            "steps": [
                {"op": "SelectProject"},
                {
                    "op": "CarbonAtoms",
                    "source": ["material", "process"],
                    "known_total": True,
                },
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        },
    },
    {
        "question": "Which explicit materials contribute most?",
        "program": {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "GroupBy", "keys": ["material"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Rank", "descending": True, "top_k": 5},
            ]
        },
    },
    {
        "question": "Show factory carbon by energy carrier for the selected items.",
        "program": {
            "steps": [
                {"op": "SelectClicked"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["carrier"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        },
    },
)


@dataclass(frozen=True)
class SynthesisResult:
    variant: str
    compiler_status: str
    first_raw_content: str
    repair_raw_content: str
    first_program: CarbonQLProgram | None
    final_program: CarbonQLProgram | None
    first_error: dict[str, Any]
    final_error: dict[str, Any]
    validation: ValidationResult | None
    initial_semantic_validation: SemanticValidation | None
    final_semantic_validation: SemanticValidation | None
    llm_call_count: int
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "compiler_status": self.compiler_status,
            "first_raw_content": self.first_raw_content,
            "repair_raw_content": self.repair_raw_content,
            "first_program": self.first_program.to_dict() if self.first_program else None,
            "final_program": self.final_program.to_dict() if self.final_program else None,
            "first_error": self.first_error,
            "final_error": self.final_error,
            "validation": (
                {
                    "compiler_status": self.validation.compiler_status,
                    "executable": self.validation.executable,
                    "output_type": self.validation.output_type,
                    "step_output_types": list(self.validation.step_output_types),
                }
                if self.validation
                else None
            ),
            "initial_semantic_validation": (
                self.initial_semantic_validation.to_dict()
                if self.initial_semantic_validation
                else None
            ),
            "final_semantic_validation": (
                self.final_semantic_validation.to_dict()
                if self.final_semantic_validation
                else None
            ),
            "llm_call_count": self.llm_call_count,
            "latency_ms": self.latency_ms,
        }


def _type_contract() -> dict[str, Any]:
    return {
        "SelectProject": "start -> ProductSet",
        "SelectClicked": "start -> ProductSet",
        "ResolveEntities": "start -> ProductSet|MaterialSet|ProcessSet",
        "CarbonAtoms": "entity set -> CarbonAtomSet",
        "Filter": "entity/atom set -> same type",
        "JoinByAttribution": "CarbonAtomSet -> CarbonAtomSet",
        "GroupBy": "CarbonAtomSet -> GroupedCarbonSet",
        "Aggregate": "CarbonAtomSet -> CarbonScalar; GroupedCarbonSet -> CarbonTable",
        "Rank": "CarbonTable -> CarbonRanking",
        "Compare": "CarbonTable|CarbonRanking -> CarbonTable",
        "Trace": "carbon atoms/groups/value/table/ranking -> TraceResult",
    }


def _argument_contract() -> dict[str, Any]:
    return {
        "SelectProject": {},
        "SelectClicked": {"ids": "optional non-empty exact component-id array"},
        "ResolveEntities": {
            "entity_type": "component|material|process",
            "properties_by_entity_type": {
                "component": ["name", "globalId", "ifcClass"],
                "material": ["name"],
                "process": ["name"],
            },
            "default_property": "name",
            "locator": "exactly one of ids or value; property is optional only with value",
            "value": "exact string",
            "cardinality": "singleton|set",
            "alternative": "ids: non-empty exact-id array",
        },
        "CarbonAtoms": {
            "source": "material|process|all or an array",
            "known_total": (
                "optional boolean; set true for project/class known-total "
                "questions that disclose accepted evidence with exclusions"
            ),
        },
        "Filter": {
            "field": "one allowed group key",
            "choose_exactly_one_of": ["equals", "in"],
            "equals": "exact id or exact readable name for entity dimensions; exact literal for factor/source fields",
            "in": "non-empty string array; each entity value must resolve uniquely",
            "do_not_emit_argument_named": "predicate",
        },
        "JoinByAttribution": {
            "required_sources": ["material", "process"]
        },
        "GroupBy": {"keys": "non-empty allowed-key array"},
        "Aggregate": {"metric": "sum_kgCO2e"},
        "Rank": {"descending": "boolean", "top_k": "positive integer"},
        "Compare": {},
        "Trace": {},
    }


def build_synthesis_messages(
    question: str,
    selected_component_ids: Sequence[str],
    schema: GraphSchema,
    variant: str,
) -> list[dict[str, str]]:
    if variant not in {"V1", "V2", "V3", "V4"}:
        raise ValueError(f"Unsupported CarbonQL synthesis variant: {variant}")

    system = (
        "Translate one BIM-carbon information request into one CarbonQL JSON program. "
        "Return JSON only with a steps array and optional holes array. Never calculate values, "
        "invent identifiers, or output a perspective label. Start with exactly one selector. "
        "Use SelectClicked when selected_component_ids are supplied. Use Trace as an operation, "
        "not as a perspective. Infer material source from material or embodied-carbon terms; infer "
        "process source from factory, energy, or process terms; use all for an unrestricted known or "
        "combined carbon account. Absence of the words material or process is not by itself "
        "underspecification. Use '?' only when the user explicitly withholds or asks to choose the "
        "target, source, or grouping dimension. A hole must match an exact '?' argument; omit holes "
        "from every complete program."
    )
    contract: dict[str, Any] = {
        "json_shape": {
            "steps": "non-empty array of operation objects",
            "holes": "optional array; omit holes unless a matching argument is exactly '?'",
        },
        "hole_contract": {
            "allowed_dimensions": [
                "target",
                "emission_source",
                "grouping_dimension",
            ],
            "item_fields": "dimension plus a non-empty candidates string array",
        },
        "allowed_operations": sorted(OPERATIONS),
        "simple_single_view_examples": list(SIMPLE_EXAMPLES),
    }
    if variant in {"V2", "V3", "V4"}:
        contract.update(
            {
                "operator_types": _type_contract(),
                "operator_arguments": _argument_contract(),
                "carbon_sources": sorted(CARBON_SOURCES | {"all"}),
                "group_keys": sorted(GROUP_KEYS),
                "aggregate_metrics": sorted(AGGREGATE_METRICS),
                "graph_schema": schema.to_prompt_dict(),
                "rules": [
                    "For grouped answers, the base view follows GroupBy keys. Filter fields restrict the records and do not override the grouping view. Keep filters needed to express the requested population.",
                    "Within GroupBy, product dimensions take the product base view. Without product dimensions, material dimensions take the material base view, process dimensions take the process base view, and both families together use the combined view.",
                    "factor fields and source_kind are source-neutral; when the grouping contains only these fields, its base view follows the requested source.",
                    "Without GroupBy, filter fields supply the view context using the same dimension precedence and source fallback. If neither grouping nor filter fields are present, use the product view except for process-only requests whose selector does not name products. SelectClicked and ResolveEntities(component/module) name products.",
                    "Record projection is separate from the grouping label and considers both grouping and filter fields. Product filters retain attributed contributions; resolved product selectors read their attributed contributions and material/process selectors read matching source records.",
                    "The validated operator sequence determines the answer type. Absence of GroupBy and Filter does not prevent Trace from returning a TraceResult.",
                    "Sparse multi-source grouping may combine material and carrier.",
                    "JoinByAttribution requires both material and process in required_sources.",
                    "Rank must follow grouped Aggregate.",
                    "Compare requires at least a CarbonTable and normally follows Rank for largest-two questions.",
                    "ResolveEntities is itself the first selector; never place it after SelectProject or SelectClicked.",
                    "ResolveEntities uses exact ids or an exact value; when property is omitted with value, it defaults to name.",
                    "Filter accepts exact ids or exact readable names for entity dimensions such as material, carrier, process, component, component_type, module, resource, stage, and ifc_class; an unknown or ambiguous filter value is rejected without a carbon number.",
                    "Filter must use field plus exactly one of equals or in; never emit an argument named predicate.",
                    "For project or class known-total questions, set CarbonAtoms known_total true with Aggregate or Rank so accepted totals can disclose excluded records.",
                    "Plural or all class references use cardinality set; singular references default to singleton.",
                    "Item or component cues require component; material cues require material; stage, carrier, process, or resource cues require the matching canonical key. Preserve every cue in GroupBy.",
                    "The phrases without assuming which source, ask me which source, or unspecified source require source '?' plus an emission_source hole.",
                    "A request asking which of two values is more requires Rank with top_k 2 before Compare.",
                    "For rank or comparison across several dimensions, use one sparse GroupBy containing all requested keys; never start a second GroupBy after Rank.",
                ],
            }
        )
    payload = {
        "question": question,
        "selected_component_ids": list(selected_component_ids),
        "contract": contract,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        raise ValueError("Unclosed JSON code fence")
    return "\n".join(lines[1:-1]).strip()


def _contains_question_mark(value: Any) -> bool:
    if value == "?":
        return True
    if isinstance(value, Mapping):
        return any(_contains_question_mark(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_question_mark(item) for item in value)
    return False


def _parse_program(content: str) -> CarbonQLProgram:
    data = json.loads(_strip_code_fence(content))
    if not isinstance(data, dict):
        raise ValueError("CarbonQL response must be a JSON object")
    raw_holes = data.get("holes")
    if isinstance(raw_holes, list) and not _contains_question_mark(
        data.get("steps")
    ):
        # Some providers emit an unused documentation-shaped hole with no
        # candidates on otherwise complete programs.  It cannot affect intent
        # because no live '?' argument refers to it, so discard only that
        # malformed unused entry.  Live holes remain strict.
        cleaned_holes = [
            hole
            for hole in raw_holes
            if not (
                isinstance(hole, Mapping)
                and not tuple(hole.get("candidates") or ())
            )
        ]
        if len(cleaned_holes) != len(raw_holes):
            data = {**data, "holes": cleaned_holes}
    return CarbonQLProgram.from_dict(data)


def _attempt(
    content: str,
    schema: GraphSchema,
    *,
    question: str = "",
    selected_component_ids: Sequence[str] = (),
) -> tuple[CarbonQLProgram | None, ValidationResult | None, dict[str, Any]]:
    try:
        program = normalize_program(
            _parse_program(content),
            question=question,
            selected_component_ids=selected_component_ids,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return None, None, {
            "code": "invalid_syntax",
            "step_index": -1,
            "message": str(exc),
        }
    try:
        validation = validate_program(program, schema)
    except CarbonQLValidationError as exc:
        return program, None, exc.to_dict()
    return program, validation, {}


def _semantic_error(validation: SemanticValidation) -> dict[str, Any]:
    return {
        "code": "semantic_coverage_error",
        "step_index": -1,
        "message": "The type-valid program omits explicit question requirements.",
        "requirement": validation.requirement.to_dict(),
        "errors": [error.to_dict() for error in validation.errors],
    }


def build_repair_messages(
    question: str,
    selected_component_ids: Sequence[str],
    invalid_program: CarbonQLProgram | None,
    error: Mapping[str, Any],
    schema: GraphSchema,
) -> list[dict[str, str]]:
    del schema
    payload = {
        "question_context": question,
        "selection_context": list(selected_component_ids),
        "program_to_repair": invalid_program.to_dict() if invalid_program else None,
        "reported_error": dict(error),
        "contracts": {
            "operator_types": _type_contract(),
            "operator_arguments": _argument_contract(),
            "allowed_operations": sorted(OPERATIONS),
            "allowed_group_keys": sorted(GROUP_KEYS),
            "allowed_sources": ["material", "process", "all"],
            "allowed_hole_dimensions": [
                "target",
                "emission_source",
                "grouping_dimension",
            ],
            "rules": [
                "A declared hole must match an exact '?' argument; otherwise omit holes.",
                "ResolveEntities is the selector and cannot follow another selector.",
                "Use one sparse GroupBy for a multi-dimensional rank or comparison.",
                "Filter uses field plus equals or in; never emit predicate.",
                "Project known-total questions need CarbonAtoms known_total true.",
            ],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "Repair the CarbonQL program once. Return one JSON object whose top-level keys must be steps and optional holes. "
                "Never echo the repair request, question, contracts, or reported error. Do not calculate an answer. "
                "Fix the reported error and ensure the entire returned program satisfies the supplied contracts."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class CarbonQLSynthesizer:
    def __init__(self, client: Any, schema: GraphSchema):
        self.client = client
        self.schema = schema

    def synthesize(
        self,
        question: str,
        selected_component_ids: Sequence[str],
        variant: str,
    ) -> SynthesisResult:
        started = time.perf_counter()
        first_raw = self.client.complete(
            build_synthesis_messages(question, selected_component_ids, self.schema, variant),
            max_tokens=1800,
        )
        first_content = str(first_raw.get("content") or "")
        first_program, first_validation, first_error = _attempt(
            first_content,
            self.schema,
            question=question,
            selected_component_ids=selected_component_ids,
        )
        first_semantic: SemanticValidation | None = None

        if variant == "V4" and first_validation is not None and first_program is not None:
            first_semantic = validate_semantic_coverage(
                question, selected_component_ids, first_program
            )
            if not first_semantic.valid:
                first_error = _semantic_error(first_semantic)

        if first_validation is not None and (
            variant != "V4" or (first_semantic is not None and first_semantic.valid)
        ):
            return SynthesisResult(
                variant=variant,
                compiler_status=first_validation.compiler_status,
                first_raw_content=first_content,
                repair_raw_content="",
                first_program=first_program,
                final_program=first_program,
                first_error={},
                final_error={},
                validation=first_validation,
                initial_semantic_validation=first_semantic,
                final_semantic_validation=first_semantic,
                llm_call_count=1,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )

        if variant not in {"V3", "V4"}:
            return SynthesisResult(
                variant=variant,
                compiler_status=str(first_error["code"]),
                first_raw_content=first_content,
                repair_raw_content="",
                first_program=first_program,
                final_program=None,
                first_error=first_error,
                final_error=first_error,
                validation=None,
                initial_semantic_validation=first_semantic,
                final_semantic_validation=None,
                llm_call_count=1,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )

        repair_raw = self.client.complete(
            build_repair_messages(
                question,
                selected_component_ids,
                first_program,
                first_error,
                self.schema,
            ),
            max_tokens=1800,
        )
        repair_content = str(repair_raw.get("content") or "")
        repaired_program, repaired_validation, repaired_error = _attempt(
            repair_content,
            self.schema,
            question=question,
            selected_component_ids=selected_component_ids,
        )
        repaired_semantic: SemanticValidation | None = None
        if variant == "V4" and repaired_validation is not None and repaired_program is not None:
            repaired_semantic = validate_semantic_coverage(
                question, selected_component_ids, repaired_program
            )
            if not repaired_semantic.valid:
                repaired_error = _semantic_error(repaired_semantic)

        repair_valid = repaired_validation is not None and (
            variant != "V4"
            or (repaired_semantic is not None and repaired_semantic.valid)
        )
        compiler_status = (
            (
                "semantic_repaired"
                if variant == "V4" and first_error.get("code") == "semantic_coverage_error"
                else "repaired"
            )
            if repair_valid and repaired_validation.compiler_status == "valid"
            else (
                repaired_validation.compiler_status
                if repair_valid and repaired_validation
                else str(repaired_error["code"])
            )
        )
        return SynthesisResult(
            variant=variant,
            compiler_status=compiler_status,
            first_raw_content=first_content,
            repair_raw_content=repair_content,
            first_program=first_program,
            final_program=repaired_program if repair_valid else None,
            first_error=first_error,
            final_error=repaired_error,
            validation=repaired_validation if repair_valid else None,
            initial_semantic_validation=first_semantic,
            final_semantic_validation=repaired_semantic,
            llm_call_count=2,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
