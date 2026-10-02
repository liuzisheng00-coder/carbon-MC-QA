"""Candidate CarbonQL views derived from deterministic semantic requirements."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from dm2c_carbonql import CarbonQLProgram, GraphSchema, validate_program
from dm2c_carbonql_executor import CarbonQLExecutor
from dm2c_carbonql_semantic import compile_semantic_requirements

_SOURCE_ORDER = ("material", "process")


@dataclass(frozen=True)
class PerspectiveConstraint:
    mode: str
    sources: tuple[str, ...]
    group_keys: tuple[str, ...]
    operations: tuple[str, ...]
    evidence_spans: Mapping[str, tuple[str, ...]]
    required_selector: str = ""


@dataclass(frozen=True)
class CarbonQLCandidate:
    candidate_id: str
    sources: tuple[str, ...]
    program: CarbonQLProgram
    program_sha256: str


@dataclass(frozen=True)
class CarbonQLCandidateSet:
    constraint: PerspectiveConstraint
    candidates: tuple[CarbonQLCandidate, ...]


@dataclass(frozen=True)
class CandidateExecution:
    candidate_id: str
    program_sha256: str
    status: str
    rows: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    emission_ids: tuple[str, ...]
    projection_keys: tuple[tuple[str, ...], ...]
    trace_rows: tuple[Mapping[str, Any], ...]
    decision_probe_rows: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _plain_data({
            "candidate_id": self.candidate_id,
            "program_sha256": self.program_sha256,
            "status": self.status,
            "rows": [dict(row) for row in self.rows],
            "summary": dict(self.summary),
            "emission_ids": list(self.emission_ids),
            "projection_keys": [list(key) for key in self.projection_keys],
            "trace_rows": [dict(row) for row in self.trace_rows],
            "decision_probe_rows": [dict(row) for row in self.decision_probe_rows],
            "coverage": dict(self.coverage),
        })


@dataclass(frozen=True)
class CandidateSetResult:
    status: str
    decision_class: str | None
    answer_policy: str
    executions: tuple[CandidateExecution, ...]
    decision_signatures: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _plain_data({
            "status": self.status,
            "decision_class": self.decision_class,
            "answer_policy": self.answer_policy,
            "executions": [execution.to_dict() for execution in self.executions],
            "decision_signatures": dict(self.decision_signatures),
        })


def _plain_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(
            (_plain_data(item) for item in value),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    return value


def resolve_perspective_constraint(
    question: str, selected_component_ids: Sequence[str]
) -> PerspectiveConstraint:
    requirement = compile_semantic_requirements(question, selected_component_ids)
    if "emission_source" in requirement.holes:
        mode = "clarification"
    elif requirement.sources:
        mode = "unique"
    else:
        mode = "underspecified"
    return PerspectiveConstraint(
        mode=mode,
        sources=requirement.sources,
        group_keys=requirement.group_keys,
        operations=requirement.operations,
        evidence_spans=requirement.evidence_spans,
        required_selector=requirement.required_selector,
    )


def _normalized_sources(source: object) -> list[str]:
    values = [source] if isinstance(source, str) else list(source)
    expanded = {"material", "process"} if "all" in values else set(values)
    return [candidate for candidate in _SOURCE_ORDER if candidate in expanded]


def _canonical_program_dict(program: CarbonQLProgram) -> dict[str, object]:
    payload = program.to_dict()
    for step in payload["steps"]:
        if step["op"] == "CarbonAtoms":
            step["source"] = _normalized_sources(step["source"])
    return payload


def canonical_program_bytes(program: CarbonQLProgram) -> bytes:
    payload = json.dumps(
        _canonical_program_dict(program),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return b"carbonql-program-v1\0" + payload


def program_sha256(program: CarbonQLProgram) -> str:
    return hashlib.sha256(canonical_program_bytes(program)).hexdigest()


def _candidate_id(sources: tuple[str, ...]) -> str:
    return "unified" if sources == _SOURCE_ORDER else sources[0]


def _clone_with_sources(
    prototype: CarbonQLProgram, sources: tuple[str, ...]
) -> CarbonQLProgram:
    payload = prototype.to_dict()
    for step in payload["steps"]:
        if step["op"] == "CarbonAtoms":
            step["source"] = list(sources)
    return CarbonQLProgram.from_dict(payload)


def compile_candidate_set(
    constraint: PerspectiveConstraint,
    prototype: CarbonQLProgram,
    schema: GraphSchema,
) -> CarbonQLCandidateSet:
    if constraint.mode == "clarification":
        raise ValueError("Cannot compile candidates for an explicit source clarification.")
    if prototype.holes:
        raise ValueError("Candidate prototype must be hole-free.")
    carbon_atom_steps = [step for step in prototype.steps if step.op == "CarbonAtoms"]
    if len(carbon_atom_steps) != 1:
        raise ValueError("Candidate prototype requires exactly one CarbonAtoms step.")
    if any(step.op == "JoinByAttribution" for step in prototype.steps):
        raise ValueError("Candidate prototype cannot contain JoinByAttribution.")
    validate_program(prototype, schema)

    source_options = (
        (constraint.sources,)
        if constraint.mode == "unique"
        else (("material",), ("process",), _SOURCE_ORDER)
    )
    candidates = tuple(
        CarbonQLCandidate(
            candidate_id=_candidate_id(sources),
            sources=sources,
            program=candidate_program,
            program_sha256=program_sha256(candidate_program),
        )
        for sources in source_options
        for candidate_program in (_clone_with_sources(prototype, sources),)
    )
    return CarbonQLCandidateSet(constraint=constraint, candidates=candidates)


def build_decision_probe_program(program: CarbonQLProgram) -> CarbonQLProgram:
    aggregate_indexes = [
        index for index, step in enumerate(program.steps) if step.op == "Aggregate"
    ]
    if not aggregate_indexes:
        raise ValueError("Decision probe requires an Aggregate step.")
    aggregate_index = aggregate_indexes[-1]
    suffix = program.steps[aggregate_index + 1 :]
    if any(step.op not in {"Rank", "Compare", "Trace"} for step in suffix):
        raise ValueError("Decision probe can only omit Rank, Compare, and Trace suffix steps.")
    payload = program.to_dict()
    payload["steps"] = payload["steps"][: aggregate_index + 1]
    return CarbonQLProgram.from_dict(payload)


def execute_candidate_set(
    candidate_set: CarbonQLCandidateSet,
    executor: CarbonQLExecutor,
    selected_component_ids: Sequence[str] = (),
) -> CandidateSetResult:
    executions = tuple(
        _execute_candidate(candidate, executor, selected_component_ids)
        for candidate in candidate_set.candidates
    )
    return classify_candidate_results(candidate_set, executions)


def _execute_candidate(
    candidate: CarbonQLCandidate,
    executor: CarbonQLExecutor,
    selected_component_ids: Sequence[str],
) -> CandidateExecution:
    result = executor.execute(candidate.program, selected_component_ids)
    probe_result = executor.execute(
        build_decision_probe_program(candidate.program), selected_component_ids
    )
    return CandidateExecution(
        candidate_id=candidate.candidate_id,
        program_sha256=candidate.program_sha256,
        status=result.status,
        rows=tuple(dict(row) for row in result.rows),
        summary=dict(result.summary),
        emission_ids=tuple(result.emission_ids),
        projection_keys=tuple(result.projection_keys),
        trace_rows=tuple(dict(row) for row in result.trace_rows),
        decision_probe_rows=tuple(dict(row) for row in probe_result.rows),
        coverage=dict(result.coverage),
    )


def classify_candidate_results(
    candidate_set: CarbonQLCandidateSet,
    executions: Sequence[CandidateExecution],
) -> CandidateSetResult:
    identity_keys = _identity_keys(candidate_set)
    signatures: dict[str, Any] = {"identity_keys": list(identity_keys)}
    mismatches: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidate_set.candidates):
        if index >= len(executions):
            mismatches.append(
                {
                    "index": index,
                    "expected_candidate_id": candidate.candidate_id,
                    "actual_candidate_id": None,
                    "expected_program_sha256": candidate.program_sha256,
                    "actual_program_sha256": None,
                }
            )
            continue
        execution = executions[index]
        if (
            execution.candidate_id != candidate.candidate_id
            or execution.program_sha256 != candidate.program_sha256
        ):
            mismatches.append(
                {
                    "index": index,
                    "expected_candidate_id": candidate.candidate_id,
                    "actual_candidate_id": execution.candidate_id,
                    "expected_program_sha256": candidate.program_sha256,
                    "actual_program_sha256": execution.program_sha256,
                }
            )
    for index, execution in enumerate(executions[len(candidate_set.candidates) :], start=len(candidate_set.candidates)):
        mismatches.append(
            {
                "index": index,
                "expected_candidate_id": None,
                "actual_candidate_id": execution.candidate_id,
                "expected_program_sha256": None,
                "actual_program_sha256": execution.program_sha256,
            }
        )
    statuses = {
        execution.candidate_id: execution.status for execution in executions
    }
    allowed_statuses = {"ok", "executable", "incomplete_path"}
    if mismatches or any(
        execution.status not in allowed_statuses for execution in executions
    ):
        signatures["statuses"] = {
            execution.candidate_id: execution.status for execution in executions
        }
        if mismatches:
            signatures["execution_mismatches"] = mismatches
        return CandidateSetResult(
            status="unclassifiable",
            decision_class="unclassifiable",
            answer_policy="unclassifiable",
            executions=tuple(executions),
            decision_signatures=signatures,
        )
    result_status = (
        "incomplete_path"
        if any(status == "incomplete_path" for status in statuses.values())
        else "executable"
    )
    if result_status == "incomplete_path":
        signatures["statuses"] = statuses

    operations = {step.op for step in candidate_set.candidates[0].program.steps}
    if "Trace" in operations:
        return CandidateSetResult(
            status=result_status,
            decision_class="source_dependent_trace",
            answer_policy="source_dependent_trace",
            executions=tuple(executions),
            decision_signatures=signatures,
        )
    if "Compare" not in operations and "Rank" not in operations:
        return CandidateSetResult(
            status=result_status,
            decision_class="decomposition_required",
            answer_policy="decomposition_required",
            executions=tuple(executions),
            decision_signatures=signatures,
        )

    signature_builder = _compare_signature if "Compare" in operations else _rank_signature
    built_signatures: list[Mapping[str, Any]] = []
    for execution in executions:
        signature = signature_builder(
            execution.decision_probe_rows, identity_keys, candidate_set.candidates[0].program
        )
        if signature is None:
            return CandidateSetResult(
                status="unclassifiable",
                decision_class="unclassifiable",
                answer_policy="unclassifiable",
                executions=tuple(executions),
                decision_signatures=signatures,
            )
        signatures[execution.candidate_id] = signature
        built_signatures.append(signature)

    if any(signature.get("top_k_cutoff_splits_tie") for signature in built_signatures):
        return CandidateSetResult(
            status="unclassifiable",
            decision_class="unclassifiable",
            answer_policy="unclassifiable",
            executions=tuple(executions),
            decision_signatures=signatures,
        )

    decision_class = (
        "view_robust" if all(signature == built_signatures[0] for signature in built_signatures) else "view_sensitive"
    )
    answer_policy = _answer_policy(built_signatures[0], "Compare" in operations)
    return CandidateSetResult(
        status=result_status,
        decision_class=decision_class,
        answer_policy=answer_policy,
        executions=tuple(executions),
        decision_signatures=signatures,
    )


def _identity_keys(candidate_set: CarbonQLCandidateSet) -> tuple[str, ...]:
    group_steps = [
        step for step in candidate_set.candidates[0].program.steps if step.op == "GroupBy"
    ]
    if len(group_steps) != 1:
        raise ValueError("Decision classification requires exactly one GroupBy step.")
    return tuple(str(key) for key in group_steps[0].args["keys"])


def _row_identity(row: Mapping[str, Any], identity_keys: Sequence[str]) -> tuple[Any, ...] | None:
    if any(key not in row for key in identity_keys):
        return None
    return tuple(row[key] for key in identity_keys)


def _identity_sort_key(identity: tuple[Any, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            type(value).__qualname__,
            json.dumps(
                _plain_data(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
        for value in identity
    )


def _ranked_classes(
    rows: Sequence[Mapping[str, Any]],
    identity_keys: Sequence[str],
    *,
    descending: bool = True,
) -> list[tuple[float, frozenset[tuple[Any, ...]]]] | None:
    if not rows:
        return None
    classified: list[tuple[float, tuple[Any, ...]]] = []
    for row in rows:
        identity = _row_identity(row, identity_keys)
        value = row.get("kgCO2e")
        if identity is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        classified.append((float(value), identity))
    classified.sort(
        key=lambda item: (
            -item[0] if descending else item[0],
            _identity_sort_key(item[1]),
        )
    )
    classes: list[tuple[float, frozenset[tuple[Any, ...]]]] = []
    for value, identity in classified:
        if classes and abs(classes[-1][0] - value) <= 1e-4:
            classes[-1] = (classes[-1][0], classes[-1][1] | {identity})
        else:
            classes.append((value, frozenset({identity})))
    return classes


def _compare_signature(
    rows: Sequence[Mapping[str, Any]],
    identity_keys: Sequence[str],
    program: CarbonQLProgram,
) -> Mapping[str, Any] | None:
    del program
    classes = _ranked_classes(rows, identity_keys)
    if classes is None:
        return None
    top = classes[0][1]
    if len(top) == 1:
        return {"winner": next(iter(top))}
    return {"tie": top}


def _rank_signature(
    rows: Sequence[Mapping[str, Any]],
    identity_keys: Sequence[str],
    program: CarbonQLProgram,
) -> Mapping[str, Any] | None:
    rank_step = next(step for step in program.steps if step.op == "Rank")
    classes = _ranked_classes(
        rows,
        identity_keys,
        descending=bool(rank_step.args.get("descending", True)),
    )
    if classes is None:
        return None
    top_k = int(rank_step.args.get("top_k", 10))
    selected_classes: list[list[tuple[Any, ...]]] = []
    seen = 0
    for _, tie_class in classes:
        next_seen = seen + len(tie_class)
        if seen < top_k < next_seen:
            return {
                "tie_classes": selected_classes,
                "top_k_cutoff_splits_tie": True,
            }
        selected_classes.append(sorted(tie_class, key=_identity_sort_key))
        seen = next_seen
        if seen >= top_k:
            break
    return {"tie_classes": selected_classes}


def _answer_policy(signature: Mapping[str, Any], is_compare: bool) -> str:
    if is_compare:
        return "strict_winner" if "winner" in signature else "tie_set"
    return "ranked_tie_classes"
