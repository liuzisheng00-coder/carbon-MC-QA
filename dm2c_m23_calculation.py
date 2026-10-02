"""Pure pre-population validation for the canonical M2.3 carbon KG.

The functions in this module validate and calculate candidate consumption
facts without receiving, constructing, or mutating a graph.  Rejected inputs
are represented only as :class:`ValidationIssue` values; graph population is a
separate downstream step.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence

from dm2c_m23_canonical import stable_id
from dm2c_m23_units import (
    UnitError,
    convert_value,
    parse_factor_denominator,
    parse_unit,
    validate_quantity_factor,
)


ConsumptionKind = Literal["material", "energy"]


class FactorRecordLike(Protocol):
    """Structural contract used to avoid importing the legacy builder.

    ``FactorRecord`` currently lives in ``dm2c_multigranular_carbon_kg``.
    Depending on that module here would create a circular import when the
    builder re-exports this validation API, so calculation uses only the
    strict-v2 factor fields it needs.
    """

    factor_id: str
    keyword: str
    factor_value: float
    factor_unit: str
    source: str
    match_status: str
    confidence: float
    source_row_id: str
    original_factor_unit: str
    denominator_unit: str
    normalized_factor_value: float | None
    normalized_denominator: str
    geography: str
    year: str
    system_boundary: str
    source_reference: str
    proxy_status: str
    validation_status: str


@dataclass(frozen=True)
class StrictFactorSnapshot:
    """Immutable copy of the strict factor fields used by one calculation."""

    factor_id: str
    keyword: str
    factor_value: object
    factor_unit: str
    source: str
    match_status: str
    confidence: object
    source_row_id: str
    original_factor_unit: str
    denominator_unit: str
    normalized_factor_value: object
    normalized_denominator: str
    geography: str
    year: str
    system_boundary: str
    source_reference: str
    proxy_status: str
    validation_status: str

    @classmethod
    def from_record(cls, factor: FactorRecordLike) -> "StrictFactorSnapshot":
        if isinstance(factor, cls):
            return factor
        return cls(
            factor_id=str(getattr(factor, "factor_id", "") or ""),
            keyword=str(getattr(factor, "keyword", "") or ""),
            factor_value=getattr(factor, "factor_value", None),
            factor_unit=str(getattr(factor, "factor_unit", "") or ""),
            source=str(getattr(factor, "source", "") or ""),
            match_status=str(getattr(factor, "match_status", "") or ""),
            confidence=getattr(factor, "confidence", None),
            source_row_id=str(getattr(factor, "source_row_id", "") or ""),
            original_factor_unit=str(getattr(factor, "original_factor_unit", "") or ""),
            denominator_unit=str(getattr(factor, "denominator_unit", "") or ""),
            normalized_factor_value=getattr(factor, "normalized_factor_value", None),
            normalized_denominator=str(getattr(factor, "normalized_denominator", "") or ""),
            geography=str(getattr(factor, "geography", "") or ""),
            year=str(getattr(factor, "year", "") or ""),
            system_boundary=str(getattr(factor, "system_boundary", "") or ""),
            source_reference=str(getattr(factor, "source_reference", "") or ""),
            proxy_status=str(getattr(factor, "proxy_status", "") or ""),
            validation_status=str(getattr(factor, "validation_status", "") or ""),
        )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


@dataclass(frozen=True)
class QuantityOperand:
    operand_id: str
    role: str
    value: float
    unit: str
    source_id: str
    design_quantity_id: str | None = None


@dataclass(frozen=True)
class OperandConversion:
    """One explicit raw-to-normalized operand or result conversion."""

    operand_id: str
    role: str
    source_id: str
    source_value: float
    source_unit: str
    normalized_value: float
    normalized_unit: str
    conversion_factor: float
    design_quantity_id: str | None = None


@dataclass(frozen=True)
class ConsumptionCandidate:
    record_id: str
    kind: ConsumptionKind
    source_record_id: str
    evidence_source_id: str
    product_target_id: str | None
    recorded_scope: str
    requested_scope: str
    factor: StrictFactorSnapshot | FactorRecordLike | None
    factor_resolution_reason: str
    formula_code: str
    operands: tuple[QuantityOperand, ...]
    measured: bool
    data_provenance: str = ""
    material_id: str | None = None
    energy_carrier_id: str | None = None
    # The normalized token the carrier id was derived from, kept so the account
    # can name a carrier without re-deriving it from the hash.
    energy_carrier_key: str = ""
    process_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operands", tuple(self.operands))
        object.__setattr__(self, "process_ids", tuple(self.process_ids))
        if self.factor is not None:
            object.__setattr__(self, "factor", StrictFactorSnapshot.from_record(self.factor))


@dataclass(frozen=True)
class AcceptedConsumption:
    record_id: str
    consumption_id: str
    quantity_id: str
    emission_id: str
    source_identity: str
    kind: ConsumptionKind
    source_record_id: str
    evidence_source_id: str
    product_target_id: str | None
    material_id: str | None
    energy_carrier_id: str | None
    energy_carrier_key: str
    recorded_scope: str
    requested_scope: str
    system_boundary: str
    factor: StrictFactorSnapshot
    factor_id: str
    factor_source_id: str
    factor_value: float
    factor_denominator: str
    formula_code: str
    operands: tuple[QuantityOperand, ...]
    conversion_steps: tuple[OperandConversion, ...]
    quantity_value: float
    quantity_unit: str
    quantity_conversion_factor: float
    emission_value: float
    emission_unit: str
    is_valid_zero: bool
    measured: bool = False
    data_provenance: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "operands", tuple(self.operands))
        object.__setattr__(self, "conversion_steps", tuple(self.conversion_steps))

    @property
    def isValidZero(self) -> bool:  # noqa: N802 - exported LPG spelling
        return self.is_valid_zero


@dataclass(frozen=True)
class ValidationIssue:
    record_id: str
    reason_code: str
    message: str
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", _freeze(self.evidence))


@dataclass(frozen=True)
class CandidateValidation:
    accepted: AcceptedConsumption | None
    issue: ValidationIssue | None

    def __post_init__(self) -> None:
        if (self.accepted is None) == (self.issue is None):
            raise ValueError("candidate validation requires exactly one outcome")


@dataclass(frozen=True)
class ValidationResultSet:
    accepted: tuple[AcceptedConsumption, ...]
    rejected: tuple[ValidationIssue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted", tuple(self.accepted))
        object.__setattr__(self, "rejected", tuple(self.rejected))


_MESSAGES = {
    "record_id_missing": "The candidate has no stable record identity.",
    "source_record_id_missing": "The candidate has no source-record identity.",
    "evidence_source_missing": "The candidate has no traceable evidence source.",
    "material_target_missing": "A material consumption requires a component target.",
    "material_identity_missing": "A material consumption requires an IFC material identity.",
    "energy_carrier_missing": "An energy consumption requires an energy-carrier identity.",
    "factor_missing": "No unique compatible emission factor was resolved.",
    "factor_identity_missing": "The resolved factor has no stable identity.",
    "factor_value_invalid": "The resolved factor value is not finite or is negative.",
    "factor_source_missing": "The resolved factor has no traceable source identity.",
    "factor_not_strict_v2": "The factor was not accepted by the strict-v2 resolver.",
    "factor_normalization_missing": "The strict factor lacks canonical normalization fields.",
    "factor_normalization_inconsistent": "The factor's original and normalized fields are inconsistent.",
    "operand_value_invalid": "A calculation operand is not finite.",
    "operand_id_missing": "Every calculation operand requires a stable identity.",
    "operand_id_duplicate": "Calculation operand identities must be unique within a record.",
    "operand_source_missing": "Every calculation operand requires a source identity.",
    "operand_role_duplicate": "A formula role occurs more than once.",
    "operand_role_unsupported": "The formula contains an unexpected operand role.",
    "material_quantity_basis_missing": "No explicit material quantity basis is available.",
    "equal_split_prohibited": "Evidence-free equal allocation is prohibited.",
    "density_missing": "The selected formula requires an explicit sourced density.",
    "thickness_missing": "The selected formula requires an explicit sourced thickness.",
    "design_quantity_id_missing": "The derived quantity requires its parent DesignQuantity identity.",
    "density_unit_invalid": "The density unit is outside the explicit compatible registry.",
    "fraction_unit_invalid": "A constituent or layer fraction must use the dimensionless unit '1'.",
    "fraction_out_of_range": "A constituent or layer fraction must lie between zero and one.",
    "formula_unsupported": "The calculation formula is not part of the canonical method.",
    "quantity_negative": "A consumption quantity or one of its physical operands is negative.",
    "emission_value_invalid": "The calculated emission is not finite.",
    "zero_evidence_missing": "A zero quantity is valid only when it is explicitly measured and sourced.",
    "duplicate_source_record_id": "The source record identity occurs more than once and is rejected as ambiguous.",
    "duplicate_record_id": "The candidate record identity occurs more than once.",
    "candidate_kind_invalid": "The candidate kind must be either material or energy.",
}


def _issue(
    candidate: ConsumptionCandidate,
    reason_code: str,
    *,
    message: str | None = None,
    evidence: Mapping[str, object] | None = None,
) -> CandidateValidation:
    base: dict[str, object] = {
        "kind": candidate.kind,
        "sourceRecordId": candidate.source_record_id,
        "evidenceSourceId": candidate.evidence_source_id,
        "formulaCode": candidate.formula_code,
    }
    optional_context: tuple[tuple[str, object], ...] = (
        ("targetComponentId", candidate.product_target_id or ""),
        ("targetMaterialId", candidate.material_id or ""),
        ("energyCarrierId", candidate.energy_carrier_id or ""),
        ("processIds", candidate.process_ids),
        ("recordedScope", candidate.recorded_scope),
        ("requestedScope", candidate.requested_scope),
    )
    for field, value in optional_context:
        if value not in (None, "", ()):
            base[field] = value
    if evidence:
        base.update(evidence)
    return CandidateValidation(
        accepted=None,
        issue=ValidationIssue(
            record_id=candidate.record_id,
            reason_code=reason_code,
            message=message or _MESSAGES.get(reason_code, reason_code.replace("_", " ")),
            evidence=base,
        ),
    )


def _identity_or_factor_issue(candidate: ConsumptionCandidate) -> CandidateValidation | None:
    if not str(candidate.record_id).strip():
        return _issue(candidate, "record_id_missing")
    if not str(candidate.source_record_id).strip():
        return _issue(candidate, "source_record_id_missing")
    if not str(candidate.evidence_source_id).strip():
        return _issue(candidate, "evidence_source_missing")
    if candidate.kind == "material":
        if not str(candidate.product_target_id or "").strip():
            return _issue(candidate, "material_target_missing")
        if not str(candidate.material_id or "").strip():
            return _issue(candidate, "material_identity_missing")
    elif candidate.kind == "energy":
        if not str(candidate.energy_carrier_id or "").strip():
            return _issue(candidate, "energy_carrier_missing")
    else:
        return _issue(candidate, "candidate_kind_invalid")

    if candidate.factor is None:
        reason = str(candidate.factor_resolution_reason or "").strip() or "factor_missing"
        return _issue(candidate, reason, evidence={"factorPresent": False})
    factor_id = str(getattr(candidate.factor, "factor_id", "") or "").strip()
    if not factor_id:
        return _issue(candidate, "factor_identity_missing", evidence={"factorPresent": True})
    if (
        str(getattr(candidate.factor, "match_status", "") or "") != "accepted_strict_v2"
        or str(getattr(candidate.factor, "validation_status", "") or "") != "accepted"
    ):
        return _issue(candidate, "factor_not_strict_v2", evidence={"factorId": factor_id})
    factor_source = str(getattr(candidate.factor, "source_row_id", "") or "").strip()
    if not factor_source:
        return _issue(candidate, "factor_source_missing", evidence={"factorId": factor_id})
    return None


def _operand_map(
    candidate: ConsumptionCandidate,
) -> tuple[dict[str, QuantityOperand], CandidateValidation | None]:
    for operand in candidate.operands:
        if not str(operand.operand_id or "").strip():
            return {}, _issue(candidate, "operand_id_missing", evidence={"operandRole": operand.role})
    operand_ids = [str(operand.operand_id) for operand in candidate.operands]
    duplicate_ids = sorted(
        operand_id for operand_id, count in Counter(operand_ids).items() if count > 1
    )
    if duplicate_ids:
        return {}, _issue(
            candidate,
            "operand_id_duplicate",
            evidence={"duplicateOperandIds": duplicate_ids},
        )
    roles = [operand.role for operand in candidate.operands]
    duplicate_roles = sorted(role for role, count in Counter(roles).items() if count > 1)
    if duplicate_roles:
        return {}, _issue(
            candidate,
            "operand_role_duplicate",
            evidence={"duplicateRoles": duplicate_roles},
        )
    for operand in candidate.operands:
        try:
            value = float(operand.value)
        except (TypeError, ValueError):
            return {}, _issue(candidate, "operand_value_invalid", evidence={"operandId": operand.operand_id})
        if not math.isfinite(value):
            return {}, _issue(candidate, "operand_value_invalid", evidence={"operandId": operand.operand_id})
    return {operand.role: operand for operand in candidate.operands}, None


def _require_roles(
    candidate: ConsumptionCandidate,
    operands: Mapping[str, QuantityOperand],
    requirements: Sequence[tuple[str, str]],
) -> CandidateValidation | None:
    for role, reason in requirements:
        if role not in operands:
            return _issue(candidate, reason, evidence={"missingRole": role})
    return None


def _exact_roles_issue(
    candidate: ConsumptionCandidate,
    operands: Mapping[str, QuantityOperand],
    allowed_roles: set[str],
) -> CandidateValidation | None:
    unexpected = sorted(set(operands) - allowed_roles)
    if unexpected:
        return _issue(
            candidate,
            "operand_role_unsupported",
            evidence={"unexpectedRoles": unexpected, "allowedRoles": sorted(allowed_roles)},
        )
    return None


def _operand_source_issue(candidate: ConsumptionCandidate) -> CandidateValidation | None:
    for operand in candidate.operands:
        if not str(operand.source_id or "").strip():
            return _issue(
                candidate,
                "operand_source_missing",
                evidence={"operandId": operand.operand_id, "operandRole": operand.role},
            )
    return None


def _density_conversion(value: float, unit: str) -> tuple[float, float]:
    key = str(unit).strip().casefold().replace("³", "3").replace(" ", "")
    factors = {
        "kg/m3": 1.0,
        "g/cm3": 1000.0,
    }
    try:
        factor = factors[key]
        return float(value) * factor, factor
    except KeyError as exc:
        raise UnitError(f"unsupported density unit: {unit!r}") from exc


def _operand_conversion(
    operand: QuantityOperand,
    normalized_value: float,
    normalized_unit: str,
    conversion_factor: float,
) -> OperandConversion:
    return OperandConversion(
        operand_id=operand.operand_id,
        role=operand.role,
        source_id=operand.source_id,
        source_value=float(operand.value),
        source_unit=operand.unit,
        normalized_value=float(normalized_value),
        normalized_unit=normalized_unit,
        conversion_factor=float(conversion_factor),
        design_quantity_id=operand.design_quantity_id,
    )


def _factor_calculation_fields(
    candidate: ConsumptionCandidate,
) -> tuple[StrictFactorSnapshot, str, str, float, str] | CandidateValidation:
    factor = candidate.factor
    assert isinstance(factor, StrictFactorSnapshot)
    factor_id = factor.factor_id
    factor_source_id = factor.source_row_id
    original_unit = factor.original_factor_unit
    if not original_unit or not factor.factor_unit:
        return _issue(candidate, "factor_normalization_missing", evidence={"factorId": factor_id})
    if factor.factor_unit != original_unit:
        return _issue(candidate, "factor_normalization_inconsistent", evidence={"factorId": factor_id})
    try:
        original_denominator = parse_factor_denominator(original_unit)
    except UnitError:
        return _issue(candidate, "factor_denominator_invalid", evidence={"factorId": factor_id})
    if (
        factor.normalized_factor_value is None
        or not factor.normalized_denominator
        or not factor.denominator_unit
    ):
        return _issue(candidate, "factor_normalization_missing", evidence={"factorId": factor_id})
    normalized_denominator = factor.normalized_denominator
    if (
        normalized_denominator != original_denominator.canonical_unit
        or factor.denominator_unit != original_denominator.canonical_unit
    ):
        return _issue(candidate, "factor_normalization_inconsistent", evidence={"factorId": factor_id})
    try:
        parse_unit(normalized_denominator)
    except UnitError:
        return _issue(candidate, "factor_denominator_invalid", evidence={"factorId": factor_id})
    try:
        original_factor_value = float(factor.factor_value)
        factor_value = float(factor.normalized_factor_value)
    except (TypeError, ValueError):
        return _issue(candidate, "factor_value_invalid", evidence={"factorId": factor_id})
    if (
        not math.isfinite(original_factor_value)
        or original_factor_value < 0
        or not math.isfinite(factor_value)
        or factor_value < 0
    ):
        return _issue(candidate, "factor_value_invalid", evidence={"factorId": factor_id})
    expected_normalized = original_factor_value / original_denominator.to_canonical
    if not math.isclose(factor_value, expected_normalized, rel_tol=1e-12, abs_tol=1e-15):
        return _issue(
            candidate,
            "factor_normalization_inconsistent",
            evidence={
                "factorId": factor_id,
                "expectedNormalizedValue": expected_normalized,
                "recordedNormalizedValue": factor_value,
            },
        )
    return factor, factor_id, factor_source_id, factor_value, normalized_denominator


def _finalize(
    candidate: ConsumptionCandidate,
    raw_quantity_value: float,
    raw_quantity_unit: str,
    conversion_steps: Sequence[OperandConversion] = (),
) -> CandidateValidation:
    factor_fields = _factor_calculation_fields(candidate)
    if isinstance(factor_fields, CandidateValidation):
        return factor_fields
    factor, factor_id, factor_source_id, factor_value, normalized_denominator = factor_fields
    original_factor_unit = factor.original_factor_unit
    accepted_system_boundary = factor.system_boundary
    factor_scope_for_validation = factor.system_boundary
    if (
        candidate.kind == "energy"
        and str(candidate.factor_resolution_reason or "").strip()
        == "accepted_factory_scope_policy"
    ):
        factor_scope_for_validation = candidate.requested_scope
        accepted_system_boundary = candidate.requested_scope

    compatibility = validate_quantity_factor(
        raw_quantity_unit,
        original_factor_unit,
        candidate.recorded_scope,
        factor_scope_for_validation,
        candidate.requested_scope,
    )
    if not compatibility.accepted:
        return _issue(
            candidate,
            compatibility.reason_code,
            evidence={
                "quantityUnit": raw_quantity_unit,
                "factorUnit": original_factor_unit,
                "factorId": factor_id,
            },
        )
    try:
        normalized_quantity = convert_value(
            float(raw_quantity_value), raw_quantity_unit, normalized_denominator
        )
        conversion_factor = convert_value(1.0, raw_quantity_unit, normalized_denominator)
    except UnitError:
        return _issue(
            candidate,
            "unit_dimension_incompatible",
            evidence={"quantityUnit": raw_quantity_unit, "factorDenominator": normalized_denominator},
        )
    if not math.isfinite(normalized_quantity):
        return _issue(candidate, "operand_value_invalid")
    if normalized_quantity < 0:
        return _issue(candidate, "quantity_negative")
    is_valid_zero = normalized_quantity == 0.0
    if is_valid_zero and not candidate.measured:
        return _issue(candidate, "zero_evidence_missing")

    emission = normalized_quantity * factor_value
    if not math.isfinite(emission):
        return _issue(
            candidate,
            "emission_value_invalid",
            evidence={"quantityValue": normalized_quantity, "factorValue": factor_value},
        )
    result_conversion = OperandConversion(
        operand_id=stable_id("CalculatedQuantityOperand", candidate.evidence_source_id, candidate.record_id),
        role="calculated_quantity",
        source_id=candidate.evidence_source_id,
        source_value=float(raw_quantity_value),
        source_unit=raw_quantity_unit,
        normalized_value=normalized_quantity,
        normalized_unit=normalized_denominator,
        conversion_factor=conversion_factor,
    )
    all_conversion_steps = tuple(conversion_steps) + (result_conversion,)
    consumption_class = "MaterialConsumption" if candidate.kind == "material" else "EnergyConsumption"
    source_identity = stable_id(
        "SourceRecord", candidate.evidence_source_id, candidate.source_record_id
    )
    consumption_id = stable_id(consumption_class, source_identity, candidate.record_id)
    accepted = AcceptedConsumption(
        record_id=candidate.record_id,
        consumption_id=consumption_id,
        quantity_id=stable_id("ConsumptionQuantity", consumption_id),
        emission_id=stable_id("CarbonEmission", consumption_id),
        source_identity=source_identity,
        kind=candidate.kind,
        source_record_id=candidate.source_record_id,
        evidence_source_id=candidate.evidence_source_id,
        product_target_id=candidate.product_target_id,
        material_id=candidate.material_id,
        energy_carrier_id=candidate.energy_carrier_id,
        energy_carrier_key=candidate.energy_carrier_key,
        recorded_scope=candidate.recorded_scope,
        requested_scope=candidate.requested_scope,
        system_boundary=accepted_system_boundary,
        factor=factor,
        factor_id=factor_id,
        factor_source_id=factor_source_id,
        factor_value=factor_value,
        factor_denominator=normalized_denominator,
        formula_code=candidate.formula_code,
        operands=candidate.operands,
        conversion_steps=all_conversion_steps,
        quantity_value=normalized_quantity,
        quantity_unit=normalized_denominator,
        quantity_conversion_factor=conversion_factor,
        emission_value=emission,
        emission_unit="kgCO2e",
        is_valid_zero=is_valid_zero,
        measured=candidate.measured,
        data_provenance=candidate.data_provenance,
    )
    return CandidateValidation(accepted=accepted, issue=None)


def validate_material_candidate(candidate: ConsumptionCandidate) -> CandidateValidation:
    """Validate and calculate one material candidate without graph mutation."""
    if candidate.kind != "material":
        return _issue(candidate, "candidate_kind_invalid")
    common_issue = _identity_or_factor_issue(candidate)
    if common_issue is not None:
        return common_issue
    if candidate.formula_code == "equal_split":
        return _issue(candidate, "equal_split_prohibited")
    if candidate.formula_code in {"", "none"}:
        return _issue(candidate, "material_quantity_basis_missing")

    operands, operand_issue = _operand_map(candidate)
    if operand_issue is not None:
        return operand_issue

    if candidate.formula_code == "direct_mass":
        missing = _require_roles(candidate, operands, (("material_mass", "material_quantity_basis_missing"),))
        if missing is not None:
            return missing
        exact = _exact_roles_issue(candidate, operands, {"material_mass"})
        if exact is not None:
            return exact
        source_issue = _operand_source_issue(candidate)
        if source_issue is not None:
            return source_issue
        quantity = operands["material_mass"]
        if float(quantity.value) < 0:
            return _issue(candidate, "quantity_negative", evidence={"operandId": quantity.operand_id})
        return _finalize(candidate, float(quantity.value), quantity.unit)

    if candidate.formula_code == "volume_density":
        missing = _require_roles(
            candidate,
            operands,
            (("material_volume", "material_quantity_basis_missing"), ("density", "density_missing")),
        )
        if missing is not None:
            return missing
        exact = _exact_roles_issue(candidate, operands, {"material_volume", "density"})
        if exact is not None:
            return exact
        volume = operands["material_volume"]
        density = operands["density"]
        if not volume.design_quantity_id:
            return _issue(candidate, "design_quantity_id_missing", evidence={"operandId": volume.operand_id})
        source_issue = _operand_source_issue(candidate)
        if source_issue is not None:
            return source_issue
        if float(volume.value) < 0 or float(density.value) < 0:
            return _issue(candidate, "quantity_negative")
        try:
            volume_m3 = convert_value(float(volume.value), volume.unit, "m3")
            volume_factor = convert_value(1.0, volume.unit, "m3")
        except UnitError:
            return _issue(candidate, "quantity_unit_invalid", evidence={"operandId": volume.operand_id})
        try:
            density_kg_m3, density_factor = _density_conversion(float(density.value), density.unit)
        except UnitError:
            return _issue(candidate, "density_unit_invalid", evidence={"operandId": density.operand_id})
        conversions = (
            _operand_conversion(volume, volume_m3, "m3", volume_factor),
            _operand_conversion(density, density_kg_m3, "kg/m3", density_factor),
        )
        return _finalize(candidate, volume_m3 * density_kg_m3, "kg", conversions)

    if candidate.formula_code == "area_thickness_density":
        missing = _require_roles(
            candidate,
            operands,
            (
                ("material_area", "material_quantity_basis_missing"),
                ("thickness", "thickness_missing"),
                ("density", "density_missing"),
            ),
        )
        if missing is not None:
            return missing
        exact = _exact_roles_issue(candidate, operands, {"material_area", "thickness", "density"})
        if exact is not None:
            return exact
        area = operands["material_area"]
        thickness = operands["thickness"]
        density = operands["density"]
        if not area.design_quantity_id:
            return _issue(candidate, "design_quantity_id_missing", evidence={"operandId": area.operand_id})
        source_issue = _operand_source_issue(candidate)
        if source_issue is not None:
            return source_issue
        if any(float(row.value) < 0 for row in (area, thickness, density)):
            return _issue(candidate, "quantity_negative")
        try:
            area_m2 = convert_value(float(area.value), area.unit, "m2")
            area_factor = convert_value(1.0, area.unit, "m2")
            thickness_m = convert_value(float(thickness.value), thickness.unit, "m")
            thickness_factor = convert_value(1.0, thickness.unit, "m")
        except UnitError:
            return _issue(candidate, "quantity_unit_invalid")
        try:
            density_kg_m3, density_factor = _density_conversion(float(density.value), density.unit)
        except UnitError:
            return _issue(candidate, "density_unit_invalid", evidence={"operandId": density.operand_id})
        conversions = (
            _operand_conversion(area, area_m2, "m2", area_factor),
            _operand_conversion(thickness, thickness_m, "m", thickness_factor),
            _operand_conversion(density, density_kg_m3, "kg/m3", density_factor),
        )
        return _finalize(candidate, area_m2 * thickness_m * density_kg_m3, "kg", conversions)

    if candidate.formula_code == "fraction_of_design_quantity":
        missing = _require_roles(
            candidate,
            operands,
            (
                ("parent_design_quantity", "material_quantity_basis_missing"),
                ("fraction", "material_quantity_basis_missing"),
            ),
        )
        if missing is not None:
            return missing
        exact = _exact_roles_issue(candidate, operands, {"parent_design_quantity", "fraction"})
        if exact is not None:
            return exact
        parent = operands["parent_design_quantity"]
        fraction = operands["fraction"]
        if not parent.design_quantity_id:
            return _issue(candidate, "design_quantity_id_missing", evidence={"operandId": parent.operand_id})
        source_issue = _operand_source_issue(candidate)
        if source_issue is not None:
            return source_issue
        if str(fraction.unit).strip() != "1":
            return _issue(candidate, "fraction_unit_invalid", evidence={"operandId": fraction.operand_id})
        fraction_value = float(fraction.value)
        if fraction_value < 0 or fraction_value > 1:
            return _issue(candidate, "fraction_out_of_range", evidence={"operandId": fraction.operand_id})
        if float(parent.value) < 0:
            return _issue(candidate, "quantity_negative", evidence={"operandId": parent.operand_id})
        conversions = (
            _operand_conversion(parent, float(parent.value), parent.unit, 1.0),
            _operand_conversion(fraction, fraction_value, "1", 1.0),
        )
        return _finalize(
            candidate,
            float(parent.value) * fraction_value,
            parent.unit,
            conversions,
        )

    return _issue(candidate, "formula_unsupported", evidence={"formulaCode": candidate.formula_code})


def validate_energy_candidate(candidate: ConsumptionCandidate) -> CandidateValidation:
    """Validate and calculate one energy candidate without graph mutation."""
    if candidate.kind != "energy":
        return _issue(candidate, "candidate_kind_invalid")
    common_issue = _identity_or_factor_issue(candidate)
    if common_issue is not None:
        return common_issue
    if candidate.formula_code != "measured_energy":
        return _issue(candidate, "formula_unsupported", evidence={"formulaCode": candidate.formula_code})
    operands, operand_issue = _operand_map(candidate)
    if operand_issue is not None:
        return operand_issue
    missing = _require_roles(candidate, operands, (("energy_quantity", "material_quantity_basis_missing"),))
    if missing is not None:
        return missing
    exact = _exact_roles_issue(candidate, operands, {"energy_quantity"})
    if exact is not None:
        return exact
    source_issue = _operand_source_issue(candidate)
    if source_issue is not None:
        return source_issue
    quantity = operands["energy_quantity"]
    if float(quantity.value) < 0:
        return _issue(candidate, "quantity_negative", evidence={"operandId": quantity.operand_id})
    return _finalize(candidate, float(quantity.value), quantity.unit)


def _candidate_order_key(candidate: ConsumptionCandidate) -> tuple[object, ...]:
    operand_key = tuple(
        (
            operand.operand_id,
            operand.role,
            repr(operand.value),
            operand.unit,
            operand.source_id,
            operand.design_quantity_id or "",
        )
        for operand in candidate.operands
    )
    return (
        candidate.record_id,
        candidate.evidence_source_id,
        candidate.source_record_id,
        candidate.kind,
        candidate.product_target_id or "",
        candidate.material_id or "",
        candidate.energy_carrier_id or "",
        candidate.recorded_scope,
        candidate.requested_scope,
        candidate.formula_code,
        str(candidate.measured),
        repr(candidate.factor),
        operand_key,
    )


def _canonical_order_value(value: object) -> tuple[object, ...]:
    if isinstance(value, Mapping):
        return (
            "mapping",
            tuple(
                (str(key), _canonical_order_value(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ),
        )
    if isinstance(value, (list, tuple)):
        return ("sequence", tuple(_canonical_order_value(item) for item in value))
    return ("scalar", type(value).__name__, repr(value))


def _issue_order_key(issue: ValidationIssue) -> tuple[object, ...]:
    return (
        issue.record_id,
        issue.reason_code,
        issue.message,
        _canonical_order_value(issue.evidence),
    )


def validate_candidates(candidates: Iterable[ConsumptionCandidate]) -> ValidationResultSet:
    """Validate a batch deterministically and prevent source double counting.

    Duplicate detection is applied to otherwise acceptable facts.  Thus, if
    one malformed candidate happens to repeat the source id of one valid
    candidate, the malformed row retains its own rejection reason and the
    single valid source fact remains usable.  Two or more acceptable facts for
    the same source are all rejected as ambiguous.
    """
    rows = tuple(candidates)
    tentative: list[tuple[ConsumptionCandidate, CandidateValidation]] = []
    rejected: list[ValidationIssue] = []

    for candidate in sorted(rows, key=_candidate_order_key):
        if candidate.kind == "material":
            result = validate_material_candidate(candidate)
        elif candidate.kind == "energy":
            result = validate_energy_candidate(candidate)
        else:
            result = _issue(candidate, "candidate_kind_invalid")
        tentative.append((candidate, result))

    acceptable_rows = [
        (candidate, result.accepted)
        for candidate, result in tentative
        if result.accepted is not None
    ]
    source_counts = Counter(
        (candidate.evidence_source_id, candidate.source_record_id)
        for candidate, _accepted in acceptable_rows
        if str(candidate.source_record_id).strip()
    )
    record_counts = Counter(
        (candidate.evidence_source_id, candidate.record_id)
        for candidate, _accepted in acceptable_rows
        if str(candidate.record_id).strip()
    )
    accepted: list[AcceptedConsumption] = []
    for candidate, result in tentative:
        if result.accepted is None:
            assert result.issue is not None
            rejected.append(result.issue)
            continue
        source_key = (candidate.evidence_source_id, candidate.source_record_id)
        record_key = (candidate.evidence_source_id, candidate.record_id)
        if source_counts.get(source_key, 0) > 1:
            duplicate = _issue(
                candidate,
                "duplicate_source_record_id",
                evidence={
                    "duplicateCount": source_counts[source_key],
                    "sourceNamespace": candidate.evidence_source_id,
                },
            )
            assert duplicate.issue is not None
            rejected.append(duplicate.issue)
        elif record_counts.get(record_key, 0) > 1:
            duplicate = _issue(
                candidate,
                "duplicate_record_id",
                evidence={
                    "duplicateCount": record_counts[record_key],
                    "sourceNamespace": candidate.evidence_source_id,
                },
            )
            assert duplicate.issue is not None
            rejected.append(duplicate.issue)
        else:
            accepted.append(result.accepted)

    return ValidationResultSet(
        accepted=tuple(
            sorted(accepted, key=lambda row: (row.record_id, row.source_identity, row.consumption_id))
        ),
        rejected=tuple(sorted(rejected, key=_issue_order_key)),
    )


__all__ = [
    "AcceptedConsumption",
    "CandidateValidation",
    "ConsumptionCandidate",
    "OperandConversion",
    "QuantityOperand",
    "StrictFactorSnapshot",
    "ValidationIssue",
    "ValidationResultSet",
    "validate_candidates",
    "validate_energy_candidate",
    "validate_material_candidate",
]
