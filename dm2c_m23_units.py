"""Closed unit and factor-compatibility rules for the M2.3 canonical v2 KG."""

from __future__ import annotations

from dataclasses import dataclass
import re


class UnitError(ValueError):
    """Raised when a unit or factor denominator is outside the closed registry."""


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    canonical_unit: str
    to_canonical: float


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason_code: str
    source_canonical_unit: str | None
    target_canonical_unit: str | None
    conversion_factor: float | None


UNIT_REGISTRY = {
    "h": UnitDefinition("time", "h", 1.0),
    "wh": UnitDefinition("energy", "kWh", 0.001),
    "kwh": UnitDefinition("energy", "kWh", 1.0),
    "mwh": UnitDefinition("energy", "kWh", 1000.0),
    "g": UnitDefinition("mass", "kg", 0.001),
    "kg": UnitDefinition("mass", "kg", 1.0),
    "mm": UnitDefinition("length", "m", 0.001),
    "m": UnitDefinition("length", "m", 1.0),
    "mm2": UnitDefinition("area", "m2", 0.000001),
    "m2": UnitDefinition("area", "m2", 1.0),
    "mm3": UnitDefinition("volume", "m3", 0.000000001),
    "m3": UnitDefinition("volume", "m3", 1.0),
    "l": UnitDefinition("liquid_volume", "L", 1.0),
}


def _unit_key(text: str) -> str:
    if not isinstance(text, str):
        raise UnitError("unit must be text")
    normalized = text.strip().casefold()
    normalized = normalized.translate(str.maketrans({"²": "2", "³": "3"}))
    normalized = re.sub(r"\*\*?\s*([23])$", r"\1", normalized)
    normalized = re.sub(r"\^\s*([23])$", r"\1", normalized)
    return normalized


def parse_unit(text: str) -> UnitDefinition:
    """Parse one exact unit token; this deliberately performs no substring matching."""
    key = _unit_key(text)
    try:
        return UNIT_REGISTRY[key]
    except KeyError as exc:
        raise UnitError(f"unknown unit: {text!r}") from exc


def convert_value(value: float, source_unit: str, target_unit: str) -> float:
    source = parse_unit(source_unit)
    target = parse_unit(target_unit)
    if source.dimension != target.dimension:
        raise UnitError(f"incompatible units: {source_unit!r} and {target_unit!r}")
    return float(value) * source.to_canonical / target.to_canonical


def parse_factor_denominator(factor_unit: str) -> UnitDefinition:
    """Return the denominator of an exact ``kgCO2e/<unit>`` factor expression."""
    if not isinstance(factor_unit, str):
        raise UnitError("factor unit must be text")
    parts = factor_unit.split("/")
    if len(parts) != 2:
        raise UnitError(f"malformed factor unit: {factor_unit!r}")
    numerator = re.sub(r"\s+", "", parts[0]).casefold().translate(str.maketrans({"₂": "2"}))
    denominator = parts[1].strip()
    if numerator != "kgco2e" or not denominator:
        raise UnitError(f"unsupported factor unit: {factor_unit!r}")
    return parse_unit(denominator)


def _scope_key(scope: str) -> str:
    if not isinstance(scope, str) or not scope.strip():
        return ""
    normalized = scope.casefold().translate(
        str.maketrans(
            {
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "―": "-",
                "−": "-",
            }
        )
    )
    match = re.search(r"a\s*(\d+)\s*-\s*a\s*(\d+)", normalized)
    if match:
        return f"a{match.group(1)}-a{match.group(2)}"
    return re.sub(r"\s+", "", normalized)


def validate_quantity_factor(
    quantity_unit: str,
    factor_unit: str,
    quantity_scope: str,
    factor_scope: str,
    requested_scope: str,
) -> ValidationResult:
    """Validate unit dimensions and controlled accounting-scope equality.

    The conversion factor converts a quantity in ``quantity_unit`` to the
    factor denominator's original unit.  It is provided even for scope
    rejections so callers can report the candidate precisely without using it.
    """
    try:
        quantity = parse_unit(quantity_unit)
    except UnitError:
        return ValidationResult(False, "quantity_unit_invalid", None, None, None)
    try:
        denominator = parse_factor_denominator(factor_unit)
    except UnitError:
        return ValidationResult(
            False, "factor_denominator_invalid", quantity.canonical_unit, None, None
        )
    conversion = quantity.to_canonical / denominator.to_canonical
    if quantity.dimension != denominator.dimension:
        return ValidationResult(
            False,
            "unit_dimension_incompatible",
            quantity.canonical_unit,
            denominator.canonical_unit,
            conversion,
        )

    quantity_scope_key = _scope_key(quantity_scope)
    factor_scope_key = _scope_key(factor_scope)
    requested_scope_key = _scope_key(requested_scope)
    if not requested_scope_key or not quantity_scope_key or not factor_scope_key:
        return ValidationResult(
            False,
            "scope_missing",
            quantity.canonical_unit,
            denominator.canonical_unit,
            conversion,
        )
    if quantity_scope_key != requested_scope_key:
        return ValidationResult(
            False,
            "quantity_scope_incompatible",
            quantity.canonical_unit,
            denominator.canonical_unit,
            conversion,
        )
    if factor_scope_key != requested_scope_key:
        return ValidationResult(
            False,
            "factor_scope_incompatible",
            quantity.canonical_unit,
            denominator.canonical_unit,
            conversion,
        )
    return ValidationResult(
        True,
        "accepted",
        quantity.canonical_unit,
        denominator.canonical_unit,
        conversion,
    )
