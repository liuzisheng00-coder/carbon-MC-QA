"""Pre-population controlled faults for canonical-v2 M3 experiments."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from dm2c_carbonql import CarbonQLProgram
from dm2c_full_qa_experiment_runner import execute_canonical_query
from dm2c_m23_calculation import (
    ConsumptionCandidate,
    QuantityOperand,
    StrictFactorSnapshot,
    ValidationIssue,
    ValidationResultSet,
    validate_energy_candidate,
)
from dm2c_m23_accounting import (
    EnergyAllocationTarget,
    EnergyPopulationPlan,
    validate_energy_population_plan,
)
from dm2c_m23_canonical_release import build_validation_rows
from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_m3_context import M3ExecutionContext
from dm2c_m3_release import canonical_release_digest, publish_derived_release


@dataclass(frozen=True, slots=True)
class InjectionSpec:
    injection_type: str
    candidate_record_id: str
    source_record_id: str
    evidence_source_id: str
    expected_reason_code: str
    sequence: int


@dataclass(frozen=True, slots=True)
class InjectionOutcome:
    injection_type: str
    reason_code: str
    new_rejection_count: int
    candidate_materialized: bool
    source_release_digest: str
    target_release: Path | None
    query_status: str | None


_FAULTS = (
    ("missing_quantity", "material_quantity_basis_missing"),
    ("missing_factor", "factor_missing"),
    ("missing_source", "source_record_id_missing"),
    ("incompatible_unit", "unit_dimension_incompatible"),
    ("missing_allocation_basis", "allocation_basis_missing"),
    ("synonym_rewrite", "unresolved_target"),
    ("ambiguous_target_name", "clarification_required"),
)


def build_injection_specs(
    context: M3ExecutionContext, per_category: int = 1
) -> tuple[InjectionSpec, ...]:
    if not context.controlled_fixture:
        raise ValueError("injection requires an explicit controlled fixture")
    if type(per_category) is not int or per_category < 1:
        raise ValueError("per_category must be a positive integer")
    allocated = next(
        fact for fact in context.canonical.emissions if fact.mode == "allocated"
    )
    rows: list[InjectionSpec] = []
    for injection_type, reason in _FAULTS:
        for index in range(per_category):
            source = (
                allocated.source_record_id
                if injection_type == "missing_allocation_basis"
                else f"source:task11:{injection_type}:{index}"
            )
            rows.append(
                InjectionSpec(
                    injection_type=injection_type,
                    candidate_record_id=f"record:task11:{injection_type}:{index}",
                    source_record_id=source,
                    evidence_source_id=f"evidence:task11:{injection_type}:{index}",
                    expected_reason_code=reason,
                    sequence=index,
                )
            )
    return tuple(rows)


def _factor() -> StrictFactorSnapshot:
    return StrictFactorSnapshot(
        factor_id="factor:task11:electricity",
        keyword="electricity",
        factor_value=1.0,
        factor_unit="kgCO2e/kWh",
        source="controlled-task11",
        match_status="accepted_strict_v2",
        confidence=1.0,
        source_row_id="factor-source:task11:electricity",
        original_factor_unit="kgCO2e/kWh",
        denominator_unit="kWh",
        normalized_factor_value=1.0,
        normalized_denominator="kWh",
        geography="controlled",
        year="2026",
        system_boundary="A1-A3",
        source_reference="controlled-task11",
        proxy_status="non_proxy",
        validation_status="accepted",
    )


def _candidate(spec: InjectionSpec) -> ConsumptionCandidate:
    operands = (
        ()
        if spec.injection_type == "missing_quantity"
        else (
            QuantityOperand(
                operand_id=f"operand:{spec.candidate_record_id}",
                role="energy_quantity",
                value=1.0,
                unit="kg" if spec.injection_type == "incompatible_unit" else "kWh",
                source_id=spec.evidence_source_id,
            ),
        )
    )
    return ConsumptionCandidate(
        record_id=spec.candidate_record_id,
        kind="energy",
        source_record_id="" if spec.injection_type == "missing_source" else spec.source_record_id,
        evidence_source_id=spec.evidence_source_id,
        product_target_id="component:c1",
        recorded_scope="A1-A3",
        requested_scope="A1-A3",
        factor=None if spec.injection_type == "missing_factor" else _factor(),
        factor_resolution_reason="factor_missing",
        formula_code="measured_energy",
        operands=operands,
        measured=True,
        energy_carrier_id="carrier:electricity",
    )


def _allocation_issue(
    context: M3ExecutionContext, spec: InjectionSpec
) -> ValidationIssue:
    fact = next(
        row for row in context.canonical.emissions if row.mode == "allocated"
    )
    contributions = tuple(
        row
        for row in context.canonical.product_contributions
        if row.emission_id == fact.emission_id
    )
    plan = EnergyPopulationPlan(
        attribution_mode="allocated",
        allocation_set_id=str(contributions[0].allocation_set_id or ""),
        allocation_basis="",
        allocations=tuple(
            EnergyAllocationTarget(
                target_component_id=row.component_id,
                raw_weight=float(row.raw_weight or 0.0),
                raw_weight_unit=str(row.raw_weight_unit or ""),
                normalized_weight=float(row.normalized_weight or 0.0),
                evidence_record_id=str(row.evidence_record_id or ""),
            )
            for row in contributions
        ),
    )
    try:
        validate_energy_population_plan(plan)
    except ValueError as exc:
        message = str(exc)
    else:  # pragma: no cover - guards the controlled fault definition
        raise AssertionError("missing allocation basis unexpectedly passed validation")
    return ValidationIssue(
        record_id=spec.candidate_record_id,
        reason_code="allocation_basis_missing",
        message=message,
        evidence={
            "kind": "energy",
            "sourceRecordId": spec.source_record_id,
            "evidenceSourceId": spec.evidence_source_id,
            "formulaCode": "measured_energy",
            "energyCarrierId": str(fact.carrier_id or ""),
            "recordedScope": fact.recorded_scope,
            "requestedScope": fact.requested_scope,
        },
    )


def _rejection_row(
    context: M3ExecutionContext, spec: InjectionSpec
) -> dict[str, str]:
    if spec.injection_type == "missing_allocation_basis":
        issue = _allocation_issue(context, spec)
    else:
        result = validate_energy_candidate(_candidate(spec))
        if result.issue is None:
            raise AssertionError("the controlled fault unexpectedly passed validation")
        issue = result.issue
    if issue.reason_code != spec.expected_reason_code:
        raise AssertionError(
            f"fault {spec.injection_type!r} produced {issue.reason_code!r}, expected {spec.expected_reason_code!r}"
        )
    rows = build_validation_rows(
        ValidationResultSet(accepted=(), rejected=(issue,))
    )
    if len(rows) != 1:
        raise AssertionError("one injected candidate must yield one validation row")
    return dict(rows[0])


def _query_for(spec: InjectionSpec) -> CarbonQLProgram:
    value = (
        "structural-member"
        if spec.injection_type == "synonym_rewrite"
        else "Structural member"
    )
    return CarbonQLProgram.from_dict(
        {
            "steps": [
                {
                    "op": "ResolveEntities",
                    "entity_type": "component",
                    "property": "name",
                    "value": value,
                    "cardinality": "singleton",
                },
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )


def execute_injection(
    context: M3ExecutionContext,
    spec: InjectionSpec,
    *,
    output_root: Path,
) -> InjectionOutcome:
    source_digest = canonical_release_digest(context.canonical.release_dir)
    if spec.injection_type in {"synonym_rewrite", "ambiguous_target_name"}:
        query = execute_canonical_query(context, _query_for(spec))
        return InjectionOutcome(
            injection_type=spec.injection_type,
            reason_code=spec.expected_reason_code,
            new_rejection_count=0,
            candidate_materialized=False,
            source_release_digest=source_digest,
            target_release=None,
            query_status=query.status,
        )
    row = _rejection_row(context, spec)
    release_id = f"controlled-v2-injection-{spec.injection_type}-{spec.sequence}"
    target = publish_derived_release(
        context,
        output_root=output_root,
        release_id=release_id,
        additional_validation_rows=(row,),
    )
    if canonical_release_digest(context.canonical.release_dir) != source_digest:
        raise RuntimeError("source release changed during controlled injection")
    if json.loads(
        (target / "multigranular_carbon_kg.json").read_text("utf-8")
    ) != json.loads(
        (
            context.canonical.release_dir / "multigranular_carbon_kg.json"
        ).read_text("utf-8")
    ):
        raise RuntimeError("a rejected candidate changed the canonical graph")
    loaded = load_canonical_v2_context(target)
    target_rejections = sum(
        candidate["status"] == "rejected" for candidate in loaded.validation_rows
    )
    source_rejections = sum(
        candidate["status"] == "rejected"
        for candidate in context.canonical.validation_rows
    )
    graph_payload = json.loads(
        (target / "multigranular_carbon_kg.json").read_text("utf-8")
    )
    candidate_materialized = any(
        node.get("props", {}).get("recordId") == spec.candidate_record_id
        for node in graph_payload["nodes"]
    ) or any(
        edge.get("props", {}).get("recordId") == spec.candidate_record_id
        for edge in graph_payload["edges"]
    )
    recorded = next(
        candidate
        for candidate in loaded.validation_rows
        if candidate["recordId"] == spec.candidate_record_id
    )
    return InjectionOutcome(
        injection_type=spec.injection_type,
        reason_code=str(recorded["reasonCode"]),
        new_rejection_count=target_rejections - source_rejections,
        candidate_materialized=candidate_materialized,
        source_release_digest=source_digest,
        target_release=target,
        query_status=None,
    )


__all__ = [
    "InjectionOutcome",
    "InjectionSpec",
    "build_injection_specs",
    "execute_injection",
]
