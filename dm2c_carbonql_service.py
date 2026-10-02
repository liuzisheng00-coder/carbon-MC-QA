"""Serve stakeholder questions from the canonical-v2 carbon account via CarbonQL.

This is the M3 chain behind the API: the language model compiles the question
into one CarbonQL program, the program is validated against the live graph
schema, and the deterministic executor produces every number. A question the
account cannot answer returns its compiler or query status with no numeric
value, so the API never reports a fabricated total.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dm2c_canonical_v2_reader import CanonicalV2Context, load_canonical_v2_context, load_canonical_v2_context_from_graph_document
from dm2c_carbonql import (
    CarbonQLProgram,
    GraphSchema,
    derive_projection_perspective,
    derive_view_signature,
)
from dm2c_carbonql_candidate_views import (
    compile_candidate_set,
    execute_candidate_set,
    resolve_perspective_constraint,
)
from dm2c_carbonql_executor import (
    CarbonQLExecutor,
    QueryExecution,
    build_embedding_name_matcher,
)
from dm2c_carbonql_synthesizer import CarbonQLSynthesizer, SynthesisResult

PERSPECTIVE_LABELS = {
    "product": "product",
    "material_source": "material",
    "energy_source": "process",
    "source_union": "material+process",
}

# Statuses that carry a computed carbon value. Every other status is reported
# without a number rather than as a zero total.
ANSWERED_STATUS = "ok"

# The three M3 layers, named once so the interface and the experiment harness
# score the same trace.
TRACE_AGENTS = (
    "M3.1 semantic compilation",
    "M3.2 retrieval and execution",
    "M3.3 answer generation",
)

# The four compiler variants form the ablation ladder: V1 drops the typed and
# graph-schema grounding, V2 adds it, V3 adds validator-driven repair, and V4
# adds the semantic coverage gate.
COMPILER_VARIANTS = ("V1", "V2", "V3", "V4")
DEFAULT_VARIANT = "V4"


def _finalized_rows(
    rows: Iterable[Mapping[str, Any]], *, split_sources: bool = False
) -> tuple[Mapping[str, Any], ...]:
    """Add the row total and say which carbon sources actually reached the row.

    `split_sources` marks the product view, where a row is expected to carry
    both a material and a process figure and a missing one is a real coverage
    gap. The material and process views are single-sided by construction, so
    reporting a missing counterpart there would invent a gap.
    """
    finalized = []
    for row in rows:
        material = row.get("materialKgCO2e")
        process = row.get("processKgCO2e")
        present = [value for value in (material, process) if value is not None]
        if not split_sources or (material is not None and process is not None):
            status = "measured"
        elif material is not None:
            status = "material_only"
        else:
            status = "process_only"
        finalized.append(
            {
                **row,
                "totalKgCO2e": math.fsum(present) if present else None,
                "status": status,
            }
        )
    finalized.sort(key=lambda row: row["totalKgCO2e"] or 0.0, reverse=True)
    return tuple(finalized)


@dataclass(frozen=True)
class CarbonQLAnswer:
    question: str
    selected_component_ids: tuple[str, ...]
    variant: str
    compiler_status: str
    query_status: str
    answered: bool
    program: dict[str, Any] | None
    perspective: str
    operation: str
    source_scope: tuple[str, ...]
    view_signature: dict[str, Any] | None
    total_kgCO2e: float | None
    rows: tuple[Mapping[str, Any], ...]
    trace_rows: tuple[Mapping[str, Any], ...]
    evidence_ids: tuple[str, ...]
    coverage: Mapping[str, Any]
    holes: tuple[Mapping[str, Any], ...]
    synthetic_energy: bool
    synthetic_energy_provenance: str
    compiler_error: Mapping[str, Any]
    llm_call_count: int
    compile_latency_ms: float
    execute_latency_ms: float
    release_id: str
    clarification: str = ""
    source_dependence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def entity_scope(self) -> str:
        return "selected_component" if self.selected_component_ids else "project"

    def to_payload(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answered": self.answered,
            "variant": self.variant,
            "compilerStatus": self.compiler_status,
            "queryStatus": self.query_status,
            "program": self.program,
            "perspective": self.perspective,
            "operation": self.operation,
            "entityScope": self.entity_scope,
            "sourceScope": list(self.source_scope),
            "viewSignature": self.view_signature,
            "totalKgCO2e": self.total_kgCO2e,
            "rows": [dict(row) for row in self.rows],
            "traceRows": [dict(row) for row in self.trace_rows],
            "evidenceIds": list(self.evidence_ids),
            "coverage": dict(self.coverage),
            "holes": [dict(hole) for hole in self.holes],
            "syntheticEnergy": self.synthetic_energy,
            "syntheticEnergyProvenance": self.synthetic_energy_provenance,
            "compilerError": dict(self.compiler_error),
            "clarification": self.clarification,
            "sourceDependence": dict(self.source_dependence),
            "selectedComponentIds": list(self.selected_component_ids),
            "releaseId": self.release_id,
            "llmCallCount": self.llm_call_count,
            "compileLatencyMs": self.compile_latency_ms,
            "executeLatencyMs": self.execute_latency_ms,
        }


_CLARIFICATION_BY_STATUS = {
    "partial": (
        "The question withholds the carbon source. State whether it concerns "
        "material carbon, process carbon, or both."
    ),
    "clarification_required": (
        "The question withholds the carbon source. State whether it concerns "
        "material carbon, process carbon, or both."
    ),
    "unresolved_target": (
        "The referenced objects could not be resolved in the carbon account. "
        "Select the components in the model or name them exactly."
    ),
    "incomplete_path": (
        "The carbon account does not hold records for the requested source, so "
        "no value is reported for this scope."
    ),
    "unsatisfiable_filter": (
        "A filter value could not be resolved uniquely in the carbon account, "
        "so no value is reported for this scope."
    ),
}


@dataclass(frozen=True)
class CarbonMeasurement:
    """One target's carbon read straight off the account, with per-source status."""

    target_id: str
    target_name: str
    C_mat_kgCO2e: float | None
    C_proc_kgCO2e: float | None
    C_total_kgCO2e: float | None
    statuses: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_name": self.target_name,
            "C_mat_kgCO2e": self.C_mat_kgCO2e,
            "C_proc_kgCO2e": self.C_proc_kgCO2e,
            "C_total_kgCO2e": self.C_total_kgCO2e,
            "statuses": dict(self.statuses),
        }


def derive_operation(program: CarbonQLProgram) -> str:
    """Name what the program asks for, in the order the outermost step wins."""
    ops = {step.op for step in program.steps}
    if "Trace" in ops:
        return "trace"
    if "Compare" in ops:
        return "compare"
    if "Rank" in ops:
        return "rank"
    if "GroupBy" in ops:
        return "breakdown"
    if "Aggregate" in ops:
        return "aggregate"
    return "select"


def _hole_payload(holes: Sequence[Any]) -> tuple[Mapping[str, Any], ...]:
    payload: list[Mapping[str, Any]] = []
    for hole in holes:
        if hasattr(hole, "to_dict"):
            payload.append(hole.to_dict())
        elif isinstance(hole, Mapping):
            payload.append(dict(hole))
    return tuple(payload)


def _row_label(row: Mapping[str, Any]) -> str:
    """Name a grouped row the way a reader would, not by its identifier."""
    for key, value in row.items():
        if key.endswith("_name") and value:
            return str(value)
    for key, value in row.items():
        if not key.endswith("kgCO2e") and value:
            return str(value)
    return ""


def _row_value(row: Mapping[str, Any]) -> float | None:
    for key, value in row.items():
        if key.endswith("kgCO2e") and isinstance(value, (int, float)):
            return float(value)
    return None


def _headline(answer: "CarbonQLAnswer") -> str:
    """State what was asked for, which is not always the aggregate total.

    A ranking question is answered by the entry at the top of the ranking and a
    comparison by the gap between the entries. Leading with the sum over all
    groups would put a true but irrelevant number where the answer belongs.
    """
    rows = answer.rows
    if answer.operation == "rank" and rows:
        label, value = _row_label(rows[0]), _row_value(rows[0])
        if label and value is not None:
            return f"{label}: {value:,.3f} kgCO2e"
    if answer.operation == "compare" and len(rows) >= 2:
        first, second = rows[0], rows[1]
        first_value, second_value = _row_value(first), _row_value(second)
        if first_value is not None and second_value is not None:
            return (
                f"{_row_label(first)}: {first_value:,.3f} kgCO2e vs "
                f"{_row_label(second)}: {second_value:,.3f} kgCO2e "
                f"(difference {abs(first_value - second_value):,.3f} kgCO2e)"
            )
    return f"{answer.total_kgCO2e:,.3f} kgCO2e"


def format_answer_text(answer: "CarbonQLAnswer") -> str:
    """Render the four elements of an M3 answer: value, status, scope, evidence."""
    if not answer.answered:
        head = f"No carbon value is reported ({answer.query_status})."
        return f"{head} {answer.clarification}".strip()

    scope = "+".join(answer.source_scope) or "unspecified"
    lines = [
        _headline(answer),
        f"perspective: {answer.perspective}; source scope: {scope}",
        f"computed from {len(answer.evidence_ids)} atomic carbon record(s)"
        + (f" across {len(answer.rows)} group(s)" if len(answer.rows) > 1 else ""),
    ]
    if answer.operation in {"rank", "compare"}:
        lines.append(
            f"total over all groups in scope: {answer.total_kgCO2e:,.3f} kgCO2e"
        )
    return "\n".join(lines)


def reasoning_trace(answer: "CarbonQLAnswer") -> list[dict[str, Any]]:
    """One trace entry per M3 layer, for the interface and the walkthrough figure."""
    compile_agent, execute_agent, answer_agent = TRACE_AGENTS
    return [
        {
            "agent": compile_agent,
            "thought": "Compile the question and the model selection into one CarbonQL program.",
            "action": "carbonql_compile",
            "observation": {
                "compilerStatus": answer.compiler_status,
                "compilerVariant": answer.variant,
                "program": answer.program,
                "llmCallCount": answer.llm_call_count,
                "latencyMs": answer.compile_latency_ms,
            },
            "confidence": 1.0 if answer.program else 0.0,
            "reflection": (
                "The program is a machine-checkable statement of what the question asks."
                if answer.program
                else "The question could not be compiled; no value is reported."
            ),
        },
        {
            "agent": execute_agent,
            "thought": "Resolve the program against the canonical-v2 carbon account.",
            "action": "carbonql_execute",
            "observation": {
                "queryStatus": answer.query_status,
                "rowCount": len(answer.rows),
                "evidenceCount": len(answer.evidence_ids),
                "coverage": dict(answer.coverage),
                "latencyMs": answer.execute_latency_ms,
            },
            "confidence": 1.0 if answer.answered else 0.0,
            "reflection": (
                "Every reported value is the exact sum of its contributing records."
                if answer.answered
                else "Execution is fail-closed: a non-executable program returns no number."
            ),
        },
        {
            "agent": answer_agent,
            "thought": "Report the value with its status, accounting scope, and evidence.",
            "action": "carbonql_answer",
            "observation": {
                "perspective": answer.perspective,
                "sourceScope": list(answer.source_scope),
                "viewSignature": answer.view_signature,
                "syntheticEnergy": answer.synthetic_energy,
            },
            "confidence": 1.0 if answer.answered else 0.0,
            "reflection": answer.clarification
            or "The accounting scope of the reported value is explicit.",
        },
    ]


_SOURCE_DEPENDENCE_MESSAGES = {
    "view_sensitive": (
        "The question did not name a carbon source, and the answer changes with "
        "the source chosen. Read the per-source results rather than the single "
        "total."
    ),
    "decomposition_required": (
        "The question did not name a carbon source. The total is reported over "
        "the union, and the material and process parts are reported separately."
    ),
    "source_dependent_trace": (
        "The question did not name a carbon source, so the evidence trace it "
        "returns depends on the source chosen."
    ),
    "unclassifiable": (
        "The question did not name a carbon source, and the candidate scopes "
        "could not be compared, so no source-invariance verdict is reported."
    ),
}


def _source_dependence_flags(answer: "CarbonQLAnswer") -> list[dict[str, Any]]:
    """Warn when the answer turned on a source the question never named."""
    dependence = answer.source_dependence
    if not dependence.get("evaluated"):
        return []
    decision_class = str(dependence.get("decisionClass") or "")
    message = _SOURCE_DEPENDENCE_MESSAGES.get(decision_class)
    if not message:
        return []
    return [
        {
            "level": "warning",
            "code": f"source_{decision_class}",
            "message": message,
        }
    ]


def answer_payload(answer: "CarbonQLAnswer") -> dict[str, Any]:
    """Build the payload the interface renders and the experiment harness scores."""
    return {
        "question": answer.question,
        "architecture": (
            "CarbonQL compilation and deterministic execution over the "
            "canonical-v2 carbon account"
        ),
        "answer": format_answer_text(answer),
        "carbonRequirement": {
            "perspective": answer.perspective,
            "operation": answer.operation,
            "scope": answer.entity_scope,
            "source": "+".join(answer.source_scope),
            "status": answer.query_status,
        },
        "queryResult": {
            "status": answer.query_status,
            "summary": {
                "total_kgCO2e": answer.total_kgCO2e,
                "row_count": len(answer.rows),
                "evidence_count": len(answer.evidence_ids),
            },
            "rows": [dict(row) for row in answer.rows],
        },
        "results": [dict(row) for row in answer.rows],
        "reasoningTrace": reasoning_trace(answer),
        "carbonAnswer": answer.to_payload(),
        "sourceDependence": dict(answer.source_dependence),
        "validation": {
            "flags": (
                [] if answer.answered else [
                    {
                        "level": "blocked",
                        "code": answer.query_status,
                        "message": answer.clarification,
                    }
                ]
            )
            + _source_dependence_flags(answer),
            "overall_confidence": 1.0 if answer.answered else None,
            "llm_validation": {},
        },
    }


@dataclass
class CarbonQLService:
    """Compile and execute one stakeholder question against one release."""

    context: CanonicalV2Context
    synthesizer: CarbonQLSynthesizer
    executor: CarbonQLExecutor
    variant: str = "V4"
    schema: GraphSchema = field(init=False)

    def __post_init__(self) -> None:
        self.schema = self.executor.schema

    @classmethod
    def from_graph_document(
        cls,
        graph_path: Path | str,
        client: Any,
        *,
        allow_synthetic: bool = True,
        variant: str = "V4",
        embedding_client: Any = None,
        embedding_model: str = "",
        embedding_match_floor: float = 0.60,
    ) -> "CarbonQLService":
        context = load_canonical_v2_context_from_graph_document(
            graph_path, allow_synthetic=allow_synthetic
        )
        executor = CarbonQLExecutor.from_context(
            context,
            allow_synthetic=allow_synthetic,
            name_matcher=build_embedding_name_matcher(
                embedding_client, embedding_model, floor=embedding_match_floor
            ),
        )
        return cls(
            context=context,
            synthesizer=CarbonQLSynthesizer(client, executor.schema),
            executor=executor,
            variant=variant,
        )

    @classmethod
    def from_release(
        cls,
        release_dir: Path | str,
        client: Any,
        *,
        allow_synthetic: bool = False,
        variant: str = "V4",
        embedding_client: Any = None,
        embedding_model: str = "",
        embedding_match_floor: float = 0.60,
    ) -> "CarbonQLService":
        context = load_canonical_v2_context(release_dir, allow_synthetic=allow_synthetic)
        executor = CarbonQLExecutor.from_context(
            context,
            allow_synthetic=allow_synthetic,
            name_matcher=build_embedding_name_matcher(
                embedding_client, embedding_model, floor=embedding_match_floor
            ),
        )
        return cls(
            context=context,
            synthesizer=CarbonQLSynthesizer(client, executor.schema),
            executor=executor,
            variant=variant,
        )

    @property
    def release_id(self) -> str:
        return str(self.context.manifest.get("releaseId") or "")

    @property
    def synthetic_energy(self) -> bool:
        return bool(self.context.synthetic_energy)

    def graph_summary(self) -> dict[str, Any]:
        return {
            "releaseId": self.release_id,
            "releaseDir": str(self.context.release_dir),
            "syntheticEnergy": self.synthetic_energy,
            "emissionRecordCount": len(self.context.emissions),
            "productContributionCount": len(self.context.product_contributions),
            "processOnlyRecordCount": len(self.context.process_only_emissions),
            "dimensionCounts": dict(self.schema.dimension_counts),
        }

    def measure(self, target_id: str = "") -> "CarbonMeasurement":
        """Read C_mat, C_proc and C_MM for one target without invoking the model.

        The manual-comparison experiment tests the carbon account, not the
        compiler, so the three programs are written here rather than synthesized.
        A source the account cannot resolve yields no number and keeps its status.
        """
        selected = (str(target_id),) if target_id else ()
        selector: dict[str, Any] = (
            {"op": "SelectClicked", "ids": [str(target_id)]}
            if target_id
            else {"op": "SelectProject"}
        )
        values: dict[str, float | None] = {}
        statuses: dict[str, str] = {}
        for key, source in (
            ("C_mat_kgCO2e", "material"),
            ("C_proc_kgCO2e", "process"),
            ("C_total_kgCO2e", "all"),
        ):
            program = CarbonQLProgram.from_dict(
                {
                    "steps": [
                        selector,
                        {"op": "CarbonAtoms", "source": source},
                        {"op": "Aggregate", "metric": "sum_kgCO2e"},
                    ]
                }
            )
            execution = self.executor.execute(program, selected)
            statuses[key] = execution.status
            values[key] = (
                execution.summary.get("total_kgCO2e")
                if execution.status == ANSWERED_STATUS
                else None
            )
        return CarbonMeasurement(
            target_id=str(target_id) if target_id else "__project__",
            target_name=str(target_id) if target_id else "project",
            C_mat_kgCO2e=values["C_mat_kgCO2e"],
            C_proc_kgCO2e=values["C_proc_kgCO2e"],
            C_total_kgCO2e=values["C_total_kgCO2e"],
            statuses=statuses,
        )

    def _grouped(self, source: str, keys: Sequence[str]) -> QueryExecution:
        program = CarbonQLProgram.from_dict(
            {
                "steps": [
                    {"op": "SelectProject"},
                    {"op": "CarbonAtoms", "source": source},
                    {"op": "GroupBy", "keys": list(keys)},
                    {"op": "Aggregate", "metric": "sum_kgCO2e"},
                ]
            }
        )
        return self.executor.execute(program)

    def perspective_accounts(self) -> dict[str, tuple[Mapping[str, Any], ...]]:
        """Project the account into the product, material and process views.

        The panels beside the model have to agree with what a question returns,
        so they are read from the same executor rather than from the design
        backbone. The programs are written here for the same reason `measure`
        writes its own: a fixed view is not a question, and routing it through
        the compiler would make a rendering depend on a language model.

        The three views do not share a total, and that is the point. Product
        rows carry only the process carbon that allocation attributed to a
        component, while the process rows carry the plant's whole recorded
        burden including the part no product claims.
        """
        return {
            "product": self._product_rows(),
            "material": self._material_rows(),
            "process": self._process_rows(),
        }

    def _product_rows(self) -> tuple[Mapping[str, Any], ...]:
        material = self._grouped("material", ("component", "ifc_class"))
        process = self._grouped("process", ("component", "ifc_class"))
        merged: dict[str, dict[str, Any]] = {}
        for execution, field_name in ((material, "materialKgCO2e"), (process, "processKgCO2e")):
            if execution.status != ANSWERED_STATUS:
                continue
            for row in execution.rows:
                key = str(row.get("component") or "")
                # Carbon that reaches no component is not a component row; the
                # process view is where that remainder stays visible.
                if not key:
                    continue
                entry = merged.setdefault(
                    key,
                    {
                        "id": key,
                        "name": row.get("component_name") or key,
                        "context": row.get("ifc_class_name") or "",
                        "materialKgCO2e": None,
                        "processKgCO2e": None,
                    },
                )
                entry[field_name] = row.get("kgCO2e")
        return _finalized_rows(merged.values(), split_sources=True)

    def _material_rows(self) -> tuple[Mapping[str, Any], ...]:
        totals = self._grouped("material", ("material",))
        spread = self._grouped("material", ("material", "component"))
        components: dict[str, set[str]] = {}
        if spread.status == ANSWERED_STATUS:
            for row in spread.rows:
                key = str(row.get("material") or "")
                components.setdefault(key, set()).add(str(row.get("component") or ""))
        if totals.status != ANSWERED_STATUS:
            return ()
        rows = []
        for row in totals.rows:
            key = str(row.get("material") or "")
            count = len(components.get(key, ()))
            rows.append(
                {
                    "id": key or "material:unassigned",
                    "name": row.get("material_name") or key or "Unassigned material",
                    "context": f"{count} component(s)" if count else "",
                    "materialKgCO2e": row.get("kgCO2e"),
                    "processKgCO2e": None,
                }
            )
        return _finalized_rows(rows)

    def _process_rows(self) -> tuple[Mapping[str, Any], ...]:
        totals = self._grouped("process", ("stage",))
        spread = self._grouped("process", ("stage", "carrier"))
        carriers: dict[str, list[str]] = {}
        if spread.status == ANSWERED_STATUS:
            for row in spread.rows:
                key = str(row.get("stage") or "")
                name = row.get("carrier_name")
                if name and name not in carriers.setdefault(key, []):
                    carriers[key].append(str(name))
        if totals.status != ANSWERED_STATUS:
            return ()
        rows = []
        for row in totals.rows:
            key = str(row.get("stage") or "")
            rows.append(
                {
                    "id": key or "stage:unassigned",
                    # Energy the graph records without a stage still belongs to
                    # the plant's burden, so it is labelled rather than dropped.
                    "name": row.get("stage_name") or key or "Unassigned production stage",
                    "context": ", ".join(carriers.get(key, ())),
                    "materialKgCO2e": None,
                    "processKgCO2e": row.get("kgCO2e"),
                }
            )
        return _finalized_rows(rows)

    def answer(
        self,
        question: str,
        selected_component_ids: Sequence[str] = (),
        variant: str | None = None,
    ) -> CarbonQLAnswer:
        active = variant or self.variant
        if active not in COMPILER_VARIANTS:
            raise ValueError(f"Unsupported CarbonQL compiler variant: {active}")
        selected = tuple(str(value) for value in selected_component_ids)
        synthesis = self.synthesizer.synthesize(question, selected, active)
        if synthesis.final_program is None:
            return self._compilation_failed(question, selected, active, synthesis)

        started = time.perf_counter()
        execution = self.executor.execute(synthesis.final_program, selected)
        execute_latency = round((time.perf_counter() - started) * 1000, 3)
        return self._from_execution(
            question, selected, active, synthesis, execution, execute_latency
        )

    def _compilation_failed(
        self,
        question: str,
        selected: tuple[str, ...],
        variant: str,
        synthesis: SynthesisResult,
    ) -> CarbonQLAnswer:
        return CarbonQLAnswer(
            question=question,
            selected_component_ids=selected,
            variant=variant,
            compiler_status=synthesis.compiler_status,
            query_status="not_executed",
            answered=False,
            program=(
                synthesis.first_program.to_dict() if synthesis.first_program else None
            ),
            perspective="",
            operation="",
            source_scope=(),
            view_signature=None,
            total_kgCO2e=None,
            rows=(),
            trace_rows=(),
            evidence_ids=(),
            coverage={},
            holes=(),
            synthetic_energy=False,
            synthetic_energy_provenance="",
            compiler_error=dict(synthesis.final_error),
            llm_call_count=synthesis.llm_call_count,
            compile_latency_ms=synthesis.latency_ms,
            execute_latency_ms=0.0,
            release_id=self.release_id,
            clarification=(
                "The question could not be compiled into a valid carbon query, so "
                "no value is reported."
            ),
        )

    def _source_dependence(
        self,
        question: str,
        selected: tuple[str, ...],
        program: CarbonQLProgram,
    ) -> dict[str, Any]:
        """Decide whether the answer survives the choice of carbon source.

        A question that never named a source was answered over one particular
        source scope. Whether that choice mattered is a separate question, and
        this is where it is settled: the same program is re-executed over the
        material, process and unified scopes and the resulting decision
        signatures are compared.

        This is diagnostic only. It never changes the status, the total or the
        rows, because the frozen benchmarks score those and because a reader is
        better served by the answer plus its sensitivity than by an answer that
        silently mutates.
        """
        constraint = resolve_perspective_constraint(question, selected)
        if constraint.mode != "underspecified":
            return {
                "evaluated": False,
                "reason": f"the question fixes its carbon source ({constraint.mode})",
            }
        started = time.perf_counter()
        try:
            candidate_set = compile_candidate_set(constraint, program, self.schema)
            result = execute_candidate_set(candidate_set, self.executor, selected)
        except (ValueError, KeyError) as exc:
            # The candidate construction is deliberately narrow: one CarbonAtoms
            # step, one GroupBy, no join, an Aggregate to probe. A program
            # outside that shape gets no verdict rather than a guessed one.
            return {
                "evaluated": False,
                "reason": f"the program is not eligible for candidate comparison: {exc}",
            }
        payload = result.to_dict()
        return {
            "evaluated": True,
            # Reported separately from executeLatencyMs, which times the answer
            # the reader is given rather than the check on top of it.
            "latencyMs": round((time.perf_counter() - started) * 1000, 3),
            "status": payload["status"],
            "decisionClass": payload["decision_class"],
            "answerPolicy": payload["answer_policy"],
            "candidates": [
                {
                    "candidateId": execution["candidate_id"],
                    "status": execution["status"],
                    "totalKgCO2e": execution["summary"].get("total_kgCO2e"),
                    "rowCount": len(execution["rows"]),
                    "programSha256": execution["program_sha256"],
                }
                for execution in payload["executions"]
            ],
            "decisionSignatures": payload["decision_signatures"],
        }

    def _from_execution(
        self,
        question: str,
        selected: tuple[str, ...],
        variant: str,
        synthesis: SynthesisResult,
        execution: QueryExecution,
        execute_latency_ms: float,
    ) -> CarbonQLAnswer:
        program: CarbonQLProgram = synthesis.final_program  # type: ignore[assignment]
        answered = execution.status == ANSWERED_STATUS
        signature = derive_view_signature(program)
        summary = execution.summary
        return CarbonQLAnswer(
            question=question,
            selected_component_ids=selected,
            variant=variant,
            compiler_status=synthesis.compiler_status,
            query_status=execution.status,
            answered=answered,
            program=program.to_dict(),
            perspective=PERSPECTIVE_LABELS.get(
                derive_projection_perspective(program), ""
            ),
            operation=derive_operation(program),
            source_scope=signature.emission_sources,
            view_signature=signature.to_dict(),
            total_kgCO2e=summary.get("total_kgCO2e") if answered else None,
            rows=execution.rows,
            trace_rows=execution.trace_rows,
            evidence_ids=execution.emission_ids,
            coverage=execution.coverage,
            holes=_hole_payload(execution.holes),
            synthetic_energy=bool(summary.get("synthetic_energy", False)),
            synthetic_energy_provenance=str(
                summary.get("synthetic_energy_provenance") or ""
            ),
            compiler_error={},
            llm_call_count=synthesis.llm_call_count,
            compile_latency_ms=synthesis.latency_ms,
            execute_latency_ms=execute_latency_ms,
            release_id=self.release_id,
            clarification=(
                "" if answered else _CLARIFICATION_BY_STATUS.get(execution.status, "")
            ),
            source_dependence=self._source_dependence(question, selected, program),
        )
