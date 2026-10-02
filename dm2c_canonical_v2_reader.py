"""Strict, immutable reader and projections for frozen M2.3 canonical-v2 releases."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import csv
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence
from weakref import WeakSet

from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    FORBIDDEN_RUNTIME_LABELS,
    FORBIDDEN_RUNTIME_RELATIONS,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_EDGE_TRIPLES,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    stable_edge_id,
    stable_id,
)
from dm2c_m23_ifc import EXCLUDED_FACTORY_BACKBONE_TYPES


class CanonicalSchemaError(ValueError):
    """Raised when a frozen release violates the canonical-v2 contract."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class EmissionFact:
    emission_id: str
    consumption_id: str
    quantity_id: str
    factor_id: str
    record_id: str
    source_identity: str
    source_record_id: str
    evidence_source_id: str
    factor_source_id: str
    kind: str
    mode: str
    material_id: str | None
    carrier_id: str | None
    quantity_value: float
    quantity_unit: str
    factor_value: float
    factor_unit: str
    factor_denominator: str
    emission_value: float
    emission_unit: str
    recorded_scope: str
    requested_scope: str
    system_boundary: str
    formula_code: str
    is_valid_zero: bool
    ordered_raw_operands: tuple[Mapping[str, Any], ...]
    conversion_steps: tuple[Mapping[str, Any], ...]
    design_quantity_ids: tuple[str, ...]
    process_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]
    factor_keyword: str | None
    factor_source: str | None


@dataclass(frozen=True, slots=True)
class ProductContribution:
    key: tuple[str, ...]
    emission_id: str
    consumption_id: str
    component_id: str
    module_id: str
    material_id: str | None
    carrier_id: str | None
    mode: str
    source_emission_value: float
    projected_value: float
    emission_unit: str
    recorded_scope: str
    requested_scope: str
    system_boundary: str
    recorded_for_occurrence_id: str
    allocation_set_id: str | None
    allocation_basis: str | None
    raw_weight: float | None
    raw_weight_unit: str | None
    normalized_weight: float | None
    evidence_record_id: str | None
    source_record_id: str
    evidence_source_id: str


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class CanonicalV2Context:
    release_dir: Path
    manifest: Mapping[str, Any]
    emissions: tuple[EmissionFact, ...]
    product_contributions: tuple[ProductContribution, ...]
    process_only_emissions: tuple[EmissionFact, ...]
    validation_rows: tuple[Mapping[str, str], ...]
    _nodes_by_id: Mapping[str, Mapping[str, Any]]
    _edges_by_occurrence: Mapping[str, Mapping[str, Any]]
    _facts_by_emission: Mapping[str, EmissionFact]
    _contributions_by_key: Mapping[tuple[str, ...], ProductContribution]
    _dimension_ids: Mapping[str, tuple[str, ...]]
    _ifc_components: Mapping[str, tuple[str, ...]]
    synthetic_energy: bool = False


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class CanonicalV2GraphDocument:
    """Strictly validated, immutable canonical-v2 graph document.

    The standalone loader validates graph-contained accounting and attribution
    paths.  It does not validate the six-file release envelope, output hashes,
    stored reports or external accepted/rejected validation ledger; consumers
    of those release-level guarantees must use :class:`CanonicalV2Context`.
    """

    schema_version: str
    nodes: tuple[Mapping[str, Any], ...]
    edges: tuple[Mapping[str, Any], ...]


_VALID_CONTEXTS: WeakSet[CanonicalV2Context] = WeakSet()
_VALID_GRAPH_DOCUMENTS: WeakSet[CanonicalV2GraphDocument] = WeakSet()


_OUTPUTS = {
    "graphJson": "multigranular_carbon_kg.json",
    "cypher": "multigranular_carbon_kg.cypher",
    "stats": "multigranular_carbon_kg_stats.json",
    "validation": "multigranular_carbon_kg_validation.csv",
    "alignmentReport": "m2_alignment_report.json",
}
_EXACT_RELEASE_FILES = frozenset((*_OUTPUTS.values(), "case_version_manifest.json"))
_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "releaseId",
        "releaseProfile",
        "releaseReady",
        "generatedAtUtc",
        "syntheticFactoryInputsUsed",
        "inputs",
        "module",
        "configuration",
        "command",
        "coverage",
        "counts",
        "code",
        "outputs",
        "gates",
    }
)
_VALIDATION_FIELDS = (
    "recordId",
    "sourceIdentity",
    "sourceRecordId",
    "evidenceSourceId",
    "kind",
    "status",
    "reasonCode",
    "message",
    "consumptionId",
    "quantityId",
    "factorId",
    "emissionId",
    "formulaCode",
    "isValidZero",
    "evidenceJson",
)
_CODE_FILES = frozenset(
    {
        "dm2c_m23_accounting.py",
        "dm2c_m23_calculation.py",
        "dm2c_m23_canonical.py",
        "dm2c_m23_canonical_release.py",
        "dm2c_m23_ifc.py",
        "dm2c_m2_alignment_audit.py",
        "dm2c_multigranular_carbon_kg.py",
    }
)
_GATE_KEYS = frozenset(
    {"alignment", "structuralCypher", "liveCypherRoundTrip"}
)
_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_IFC_REFINEMENT_RE = re.compile(r"^Ifc[A-Za-z0-9_]+$")
_APP_CLASSES = frozenset(APPLICATION_CLASSES)
_PRINCIPAL_TRIPLES = frozenset(PRINCIPAL_EDGE_TRIPLES)
_RELATIONS = frozenset((*PRINCIPAL_PREDICATES, *OPTIONAL_CONTEXT_PREDICATES))
_GRAPH_KEYS = frozenset({"schemaVersion", "nodes", "edges"})
_NODE_KEYS = frozenset({"id", "labels", "props"})
_EDGE_KEYS = frozenset({"id", "src", "type", "tgt", "occurrenceId", "props"})
_FORBIDDEN_PROPERTY_KEYS = frozenset(
    {
        "allocationFraction",
        "allocationKey",
        "atomId",
        "blockedReason",
        "calculationStatus",
        "factoryTarget",
        "factoryTargetId",
        "isBlocked",
    }
)
_ALLOCATION_FIELDS = frozenset(
    {
        "attributionMode",
        "allocationSetId",
        "allocationBasis",
        "rawWeight",
        "rawWeightUnit",
        "normalizedWeight",
        "evidenceRecordId",
    }
)
_ALLOCATION_NODE_FIELDS = frozenset(
    {"attributionMode", "allocationSetId", "allocationBasis", "unattributedFraction"}
)
_ALLOCATION_EDGE_FIELDS = frozenset(
    {
        "allocated",
        "allocationSetId",
        "allocationBasis",
        "rawWeight",
        "rawWeightUnit",
        "allocatedFraction",
        "evidenceRecordId",
    }
)
_ALLOCATION_PROP_FIELDS = (
    _ALLOCATION_FIELDS | _ALLOCATION_EDGE_FIELDS | _ALLOCATION_NODE_FIELDS
)
_ALLOCATION_ONLY_FIELDS = _ALLOCATION_PROP_FIELDS - {"attributionMode", "allocated"}
_OPERAND_KEYS = frozenset(
    {"operand_id", "role", "value", "unit", "source_id", "design_quantity_id"}
)
_CONVERSION_KEYS = frozenset(
    {
        "operand_id",
        "role",
        "source_id",
        "source_value",
        "source_unit",
        "normalized_value",
        "normalized_unit",
        "conversion_factor",
        "design_quantity_id",
    }
)
_UNIT_DIMENSIONS = {
    "h": "time",
    "wh": "energy",
    "kwh": "energy",
    "mwh": "energy",
    "g": "mass",
    "kg": "mass",
    "mm": "length",
    "m": "length",
    "mm2": "area",
    "m2": "area",
    "mm3": "volume",
    "m3": "volume",
    "l": "liquid_volume",
}
_UNIT_CANONICAL = {
    "h": "h",
    "wh": "kwh",
    "kwh": "kwh",
    "mwh": "kwh",
    "g": "kg",
    "kg": "kg",
    "mm": "m",
    "m": "m",
    "mm2": "m2",
    "m2": "m2",
    "mm3": "m3",
    "m3": "m3",
    "l": "l",
}
_UNIT_TO_CANONICAL = {
    "h": 1.0,
    "wh": 0.001,
    "kwh": 1.0,
    "mwh": 1000.0,
    "g": 0.001,
    "kg": 1.0,
    "mm": 0.001,
    "m": 1.0,
    "mm2": 0.000001,
    "m2": 1.0,
    "mm3": 0.000000001,
    "m3": 1.0,
    "l": 1.0,
}
_REJECTED_CONTEXT_FIELDS = frozenset(
    {
        "targetComponentId",
        "targetMaterialId",
        "energyCarrierId",
        "processIds",
        "recordedScope",
        "requestedScope",
    }
)
_REJECTED_REASON_FIELDS: Mapping[str, frozenset[str]] = {
    "evidence_ownership_mismatch": frozenset(
        {"associationId", "componentId", "materialId"}
    ),
    "factor_source_not_found": frozenset({"associationId", "factorSourceRowId"}),
    "factor_source_invalid": frozenset({"associationId", "error"}),
    "design_quantity_ownership_mismatch": frozenset(
        {"associationId", "designQuantityId"}
    ),
    "factor_missing": frozenset({"factorPresent"}),
    "factor_not_found": frozenset({"factorPresent"}),
    "unresolved_factor": frozenset({"factorPresent"}),
    "ambiguous_factor": frozenset({"factorPresent"}),
    "factor_identity_missing": frozenset({"factorPresent"}),
    "factor_not_strict_v2": frozenset({"factorId"}),
    "factor_source_missing": frozenset({"factorId"}),
    "factor_normalization_missing": frozenset({"factorId"}),
    "factor_normalization_inconsistent": frozenset(
        {"factorId", "expectedNormalizedValue", "recordedNormalizedValue"}
    ),
    "factor_denominator_invalid": frozenset(
        {"factorId", "factorPresent", "quantityUnit", "factorUnit"}
    ),
    "factor_value_invalid": frozenset({"factorId"}),
    "unit_dimension_incompatible": frozenset(
        {
            "factorPresent",
            "quantityUnit",
            "factorUnit",
            "factorId",
            "factorDenominator",
        }
    ),
    "factor_scope_incompatible": frozenset(
        {"factorPresent", "quantityUnit", "factorUnit", "factorId"}
    ),
    "scope_missing": frozenset(
        {"factorPresent", "quantityUnit", "factorUnit", "factorId"}
    ),
    "quantity_scope_incompatible": frozenset(
        {"factorPresent", "quantityUnit", "factorUnit", "factorId"}
    ),
    "quantity_unit_invalid": frozenset(
        {"operandId", "factorPresent", "quantityUnit", "factorUnit", "factorId"}
    ),
    "operand_id_missing": frozenset({"operandRole"}),
    "operand_id_duplicate": frozenset({"duplicateOperandIds"}),
    "operand_role_duplicate": frozenset({"duplicateRoles"}),
    "operand_role_unsupported": frozenset({"unexpectedRoles", "allowedRoles"}),
    "operand_source_missing": frozenset({"operandId", "operandRole"}),
    "operand_value_invalid": frozenset({"operandId"}),
    "material_quantity_basis_missing": frozenset({"missingRole"}),
    "density_missing": frozenset({"missingRole"}),
    "thickness_missing": frozenset({"missingRole"}),
    "design_quantity_id_missing": frozenset({"operandId"}),
    "density_unit_invalid": frozenset({"operandId"}),
    "fraction_unit_invalid": frozenset({"operandId"}),
    "fraction_out_of_range": frozenset({"operandId"}),
    "formula_unsupported": frozenset(),
    "quantity_negative": frozenset({"operandId"}),
    "emission_value_invalid": frozenset({"quantityValue", "factorValue"}),
    "duplicate_source_record_id": frozenset({"duplicateCount", "sourceNamespace"}),
    "duplicate_record_id": frozenset({"duplicateCount", "sourceNamespace"}),
    **{
        reason: frozenset()
        for reason in (
            "record_id_missing",
            "source_record_id_missing",
            "evidence_source_missing",
            "material_target_missing",
            "material_identity_missing",
            "energy_carrier_missing",
            "candidate_kind_invalid",
            "equal_split_prohibited",
            "zero_evidence_missing",
            "allocation_basis_missing",
        )
    },
}


@dataclass(frozen=True, slots=True)
class _GraphView:
    payload: Mapping[str, Any]
    nodes: tuple[Mapping[str, Any], ...]
    edges: tuple[Mapping[str, Any], ...]
    nodes_by_id: Mapping[str, Mapping[str, Any]]
    outgoing: Mapping[str, tuple[Mapping[str, Any], ...]]
    incoming: Mapping[str, tuple[Mapping[str, Any], ...]]
    node_classes: Mapping[str, str]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _strict_json_text(text: str, path: str) -> Mapping[str, Any]:
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalSchemaError(f"{path} is not valid Unicode text") from exc
    return _strict_json_bytes(payload, path)


def _expect_keys(value: Any, expected: frozenset[str] | set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanonicalSchemaError(f"{path} must be an object")
    if set(value) != set(expected):
        missing = sorted(set(expected) - set(value))
        unknown = sorted(set(value) - set(expected))
        raise CanonicalSchemaError(
            f"{path} has invalid fields (missing={missing}, unknown={unknown})"
        )
    return value


def _text(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise CanonicalSchemaError(f"{path} must be non-empty text")
    return value


def _strict_count(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CanonicalSchemaError(f"{path} must be a non-negative integer")
    return value


def _reject_nonfinite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalSchemaError(f"{path} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{path}[{index}]")


def _strict_json_bytes(payload: bytes, path: str) -> Mapping[str, Any]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise CanonicalSchemaError(f"{path} must not contain a UTF-8 BOM")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalSchemaError(f"{path} is not strict UTF-8") from exc

    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise CanonicalSchemaError(f"{path} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                CanonicalSchemaError(f"{path} contains non-finite JSON token {token}")
            ),
        )
    except CanonicalSchemaError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise CanonicalSchemaError(f"{path} is not valid strict JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CanonicalSchemaError(f"{path} must contain one JSON object")
    _reject_nonfinite(value, path)
    return value


def _validate_descriptor(
    descriptor: Any, *, path: str, expected_filename: str | None = None
) -> Mapping[str, Any]:
    row = _expect_keys(descriptor, {"path", "sha256", "sizeBytes"}, path)
    filename = _text(row["path"], f"{path}.path")
    if expected_filename is not None and filename != expected_filename:
        raise CanonicalSchemaError(f"{path}.path must be {expected_filename!r}")
    pure = PurePosixPath(filename)
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1 or "\\" in filename:
        raise CanonicalSchemaError(f"{path}.path is not a safe release-relative filename")
    digest = row["sha256"]
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        raise CanonicalSchemaError(f"{path}.sha256 must be uppercase SHA-256")
    _strict_count(row["sizeBytes"], f"{path}.sizeBytes")
    return row


def _validate_manifest_shape(
    manifest: Mapping[str, Any], *, allow_synthetic: bool = False,
    allow_unready_backbone: bool = False,
) -> None:
    _expect_keys(manifest, _MANIFEST_KEYS, "manifest")
    if manifest["schemaVersion"] != SCHEMA_VERSION:
        raise CanonicalSchemaError(f"manifest schemaVersion must be {SCHEMA_VERSION!r}")
    unready_backbone = (
        allow_unready_backbone
        and manifest["releaseReady"] is False
        and manifest["releaseProfile"] == "actual-case"
    )
    if manifest["releaseReady"] is not True and not unready_backbone:
        raise CanonicalSchemaError("manifest releaseReady must be true")
    if manifest["syntheticFactoryInputsUsed"] is not False and not (
        allow_synthetic and manifest["syntheticFactoryInputsUsed"] is True
    ):
        raise CanonicalSchemaError("syntheticFactoryInputsUsed must be false")
    for field in ("releaseId", "releaseProfile", "generatedAtUtc"):
        _text(manifest[field], f"manifest.{field}")
    if not unready_backbone and manifest["releaseProfile"] != "controlled-fixture":
        raise CanonicalSchemaError(
            "a releaseReady reader input must use releaseProfile='controlled-fixture'"
        )
    release_id = str(manifest["releaseId"])
    if (
        not _RELEASE_ID_RE.fullmatch(release_id)
        or release_id in {".", ".."}
        or release_id.endswith(".")
        or release_id.split(".", 1)[0].upper() in _WINDOWS_RESERVED
    ):
        raise CanonicalSchemaError("manifest.releaseId is unsafe")

    outputs = _expect_keys(manifest["outputs"], set(_OUTPUTS), "manifest.outputs")
    for key, filename in _OUTPUTS.items():
        _validate_descriptor(
            outputs[key], path=f"manifest.outputs.{key}", expected_filename=filename
        )
    gates = _expect_keys(manifest["gates"], _GATE_KEYS, "manifest.gates")
    if gates["alignment"] != "pass" or gates["structuralCypher"] != "pass":
        raise CanonicalSchemaError("mandatory release gates must pass")
    if gates["liveCypherRoundTrip"] not in {"pass", "not_configured"}:
        raise CanonicalSchemaError("liveCypherRoundTrip must pass or be not_configured")

    module = _expect_keys(
        manifest["module"],
        {"sourceIdentity", "runtimeId", "name", "identitySource"},
        "manifest.module",
    )
    for field in module:
        _text(module[field], f"manifest.module.{field}")
    configuration = manifest["configuration"]
    if not isinstance(configuration, Mapping):
        raise CanonicalSchemaError("manifest.configuration must be an object")
    configuration_keys = set(configuration)
    configuration_required = {"requestedScope", "includeOpenings", "factoryInput"}
    configuration_allowed = {*configuration_required, "factoryTargetMap"}
    if (
        missing := sorted(configuration_required - configuration_keys)
    ) or (
        unknown := sorted(configuration_keys - configuration_allowed)
    ):
        raise CanonicalSchemaError(
            "manifest.configuration has invalid fields "
            f"(missing={missing}, unknown={unknown})"
        )
    _text(configuration["requestedScope"], "manifest.configuration.requestedScope")
    if type(configuration["includeOpenings"]) is not bool:
        raise CanonicalSchemaError("manifest.configuration.includeOpenings must be boolean")
    synthetic_allowed = allow_synthetic and manifest["syntheticFactoryInputsUsed"] is True
    if configuration["factoryInput"] is not None and not synthetic_allowed:
        raise CanonicalSchemaError("manifest.configuration.factoryInput must be null")
    command = _expect_keys(
        manifest["command"], {"entryPoint", "caseConfig", "releaseId"}, "manifest.command"
    )
    for field in command:
        _text(command[field], f"manifest.command.{field}")
    if command["entryPoint"] != "dm2c_m23_canonical_release.py":
        raise CanonicalSchemaError("manifest.command.entryPoint is invalid")
    if command["releaseId"] != manifest["releaseId"]:
        raise CanonicalSchemaError("manifest command releaseId does not match releaseId")

    inputs = manifest["inputs"]
    if not isinstance(inputs, Mapping):
        raise CanonicalSchemaError("manifest.inputs must be an object")
    input_keys = set(inputs)
    input_required = {
        "caseConfig",
        "ifc",
        "factorWorkbook",
        "ontology",
        "materialEvidence",
        "factoryInput",
    }
    input_allowed = {*input_required, "factoryTargetMap"}
    if (missing := sorted(input_required - input_keys)) or (
        unknown := sorted(input_keys - input_allowed)
    ):
        raise CanonicalSchemaError(
            "manifest.inputs has invalid fields "
            f"(missing={missing}, unknown={unknown})"
        )
    if inputs["factoryInput"] is not None and not synthetic_allowed:
        raise CanonicalSchemaError("manifest.inputs.factoryInput must be null")
    for key in (
        "caseConfig",
        "ifc",
        "factorWorkbook",
        "ontology",
        "materialEvidence",
        "factoryInput",
        "factoryTargetMap",
    ):
        descriptor = inputs.get(key)
        if key in {"factoryInput", "factoryTargetMap"} and descriptor is None:
            continue
        if descriptor is None and key == "materialEvidence" and unready_backbone:
            continue
        if descriptor is None:
            raise CanonicalSchemaError(
                f"manifest.inputs.{key} must be a complete ready-release descriptor"
            )
        row = _expect_keys(
            descriptor,
            {"selectedPath", "resolvedPath", "sha256", "sizeBytes"},
            f"manifest.inputs.{key}",
        )
        _text(row["selectedPath"], f"manifest.inputs.{key}.selectedPath")
        _text(row["resolvedPath"], f"manifest.inputs.{key}.resolvedPath")
        if type(row["sha256"]) is not str or not _SHA256_RE.fullmatch(row["sha256"]):
            raise CanonicalSchemaError(f"manifest.inputs.{key}.sha256 is invalid")
        _strict_count(row["sizeBytes"], f"manifest.inputs.{key}.sizeBytes")

    code = _expect_keys(manifest["code"], _CODE_FILES, "manifest.code")
    for key in sorted(_CODE_FILES):
        _validate_descriptor(code[key], path=f"manifest.code.{key}", expected_filename=key)
    if not isinstance(manifest["coverage"], Mapping) or not isinstance(manifest["counts"], Mapping):
        raise CanonicalSchemaError("manifest coverage/counts must be objects")


def _load_envelope(
    kg_dir: Path | str, *, allow_synthetic: bool = False,
    allow_unready_backbone: bool = False,
) -> tuple[Path, Mapping[str, Any], Mapping[str, bytes]]:
    root = Path(kg_dir)
    try:
        if root.is_symlink() or not root.is_dir():
            raise CanonicalSchemaError("release path must be an ordinary directory")
        entries = tuple(root.iterdir())
    except OSError as exc:
        raise CanonicalSchemaError(f"release directory cannot be inspected: {exc}") from exc
    names = {entry.name for entry in entries}
    unsafe = sorted(
        entry.name for entry in entries if entry.is_symlink() or not entry.is_file()
    )
    if names != _EXACT_RELEASE_FILES or unsafe:
        raise CanonicalSchemaError(
            "release must contain exactly six ordinary canonical artifacts "
            f"(missing={sorted(_EXACT_RELEASE_FILES - names)}, "
            f"unexpected={sorted(names - _EXACT_RELEASE_FILES)}, unsafe={unsafe})"
        )
    try:
        manifest_bytes = (root / "case_version_manifest.json").read_bytes()
    except OSError as exc:
        raise CanonicalSchemaError(f"manifest cannot be read: {exc}") from exc
    manifest = _strict_json_bytes(manifest_bytes, "case_version_manifest.json")
    _validate_manifest_shape(
        manifest,
        allow_synthetic=allow_synthetic,
        allow_unready_backbone=allow_unready_backbone,
    )

    blobs: dict[str, bytes] = {}
    outputs = manifest["outputs"]
    assert isinstance(outputs, Mapping)
    for key, filename in _OUTPUTS.items():
        path = root / filename
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise CanonicalSchemaError(f"{filename} cannot be read: {exc}") from exc
        descriptor = outputs[key]
        assert isinstance(descriptor, Mapping)
        actual_hash = sha256(payload).hexdigest().upper()
        if descriptor["sha256"] != actual_hash or descriptor["sizeBytes"] != len(payload):
            raise CanonicalSchemaError(f"{filename} hash or byte size does not match manifest")
        blobs[key] = payload
    return root.resolve(), manifest, MappingProxyType(blobs)


def _read_validation_csv(payload: bytes) -> tuple[dict[str, str], ...]:
    path = "multigranular_carbon_kg_validation.csv"
    if payload.startswith(b"\xef\xbb\xbf"):
        raise CanonicalSchemaError(f"{path} must not contain a UTF-8 BOM")
    if b"\r" in payload:
        raise CanonicalSchemaError(f"{path} must use LF line endings")
    try:
        text = payload.decode("utf-8")
        raw_rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise CanonicalSchemaError(f"{path} is not a strict UTF-8 CSV") from exc
    if not raw_rows or tuple(raw_rows[0]) != _VALIDATION_FIELDS:
        raise CanonicalSchemaError(f"{path} has an invalid header")
    rows: list[dict[str, str]] = []
    seen_records: set[str] = set()
    for line, values in enumerate(raw_rows[1:], start=2):
        if len(values) != len(_VALIDATION_FIELDS):
            raise CanonicalSchemaError(f"{path}:{line} has an invalid column count")
        row = dict(zip(_VALIDATION_FIELDS, values))
        record_id = _text(row["recordId"], f"{path}:{line}.recordId")
        if record_id in seen_records:
            raise CanonicalSchemaError(f"validation recordId {record_id!r} is duplicated")
        seen_records.add(record_id)
        status = row["status"]
        for field in ("sourceIdentity", "evidenceSourceId", "kind"):
            _text(row[field], f"validation[{record_id}].{field}")
        missing_source_rejection = (
            status == "rejected"
            and row["reasonCode"] == "source_record_id_missing"
        )
        if missing_source_rejection:
            if row["sourceRecordId"]:
                raise CanonicalSchemaError(
                    f"validation[{record_id}].sourceRecordId must preserve the missing value"
                )
        else:
            _text(
                row["sourceRecordId"],
                f"validation[{record_id}].sourceRecordId",
            )
        if row["kind"] not in {"material", "energy"}:
            raise CanonicalSchemaError(
                f"validation row {record_id!r} has invalid kind"
            )
        if status == "accepted":
            _text(row["formulaCode"], f"validation[{record_id}].formulaCode")
            if row["reasonCode"] or row["message"]:
                raise CanonicalSchemaError(f"accepted row {record_id!r} has rejection text")
            for field in ("consumptionId", "quantityId", "factorId", "emissionId"):
                _text(row[field], f"validation[{record_id}].{field}")
            if row["isValidZero"] not in {"true", "false"}:
                raise CanonicalSchemaError(
                    f"accepted row {record_id!r} has invalid isValidZero"
                )
        elif status == "rejected":
            _text(row["reasonCode"], f"validation[{record_id}].reasonCode")
            if any(
                row[field]
                for field in (
                    "consumptionId",
                    "quantityId",
                    "factorId",
                    "emissionId",
                    "isValidZero",
                )
            ):
                raise CanonicalSchemaError(
                    f"rejected row {record_id!r} leaks calculation identities"
                )
        else:
            raise CanonicalSchemaError(f"validation row {record_id!r} has invalid status")
        evidence = _strict_json_text(
            row["evidenceJson"], f"validation[{record_id}].evidenceJson"
        )
        if _canonical_json(evidence) != row["evidenceJson"]:
            raise CanonicalSchemaError(
                f"validation row {record_id!r} evidenceJson is not canonical compact JSON"
            )
        rows.append(row)
    expected = sorted(
        rows,
        key=lambda row: (
            row["recordId"],
            0 if row["status"] == "accepted" else 1,
            row["reasonCode"],
        ),
    )
    if rows != expected:
        raise CanonicalSchemaError("validation rows are not in canonical order")
    return tuple(rows)


def _validate_property_value(value: Any, path: str) -> None:
    if value is None or value == "":
        raise CanonicalSchemaError(f"{path} is not a lossless canonical property")
    if type(value) in {bool, str}:
        return
    if type(value) is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise CanonicalSchemaError(f"{path} is outside Neo4j integer range")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise CanonicalSchemaError(f"{path} must be finite")
        return
    if isinstance(value, list):
        if not value:
            return
        element_types = {type(item) for item in value}
        if len(element_types) != 1 or not element_types <= {bool, int, float, str}:
            raise CanonicalSchemaError(f"{path} must be a homogeneous scalar list")
        for index, item in enumerate(value):
            _validate_property_value(item, f"{path}[{index}]")
        return
    raise CanonicalSchemaError(f"{path} is not a Neo4j-safe scalar/list property")


def _validate_props(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanonicalSchemaError(f"{path} must be a property object")
    forbidden = sorted(set(value) & _FORBIDDEN_PROPERTY_KEYS)
    if forbidden:
        raise CanonicalSchemaError(f"{path} contains forbidden legacy keys {forbidden}")
    for key, item in value.items():
        if type(key) is not str:
            raise CanonicalSchemaError(f"{path} has a non-string property key")
        _validate_property_value(item, f"{path}.{key}")
        strings = item if isinstance(item, list) else (item,)
        for candidate in strings:
            if not isinstance(candidate, str):
                continue
            folded = candidate.casefold()
            if (
                "p0_factory_synth" in folded
                or folded.startswith("blocked")
                or folded == "all_batches"
                or folded.startswith("factorymodule_")
            ):
                raise CanonicalSchemaError(f"{path} contains forbidden runtime payload")
    return value


def _class_of(node: Mapping[str, Any]) -> str:
    labels = set(node["labels"]) & _APP_CLASSES
    return next(iter(labels)) if len(labels) == 1 else ""


def _edge_sort_key(edge: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(edge["src"]),
        str(edge["type"]),
        str(edge["tgt"]),
        str(edge["occurrenceId"]),
        str(edge["id"]),
    )


def _validate_no_orphans(
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    node_classes: Mapping[str, str],
    outgoing: Mapping[str, Sequence[Mapping[str, Any]]],
    incoming: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    requirements: dict[str, tuple[tuple[str, frozenset[str]], ...]] = {
        "ProductionBatch": (("out", frozenset({"produces"})),),
        "ModularUnit": (
            ("out", frozenset({"containsComponent"})),
            ("in", frozenset({"produces"})),
        ),
        "BuildingComponent": (
            ("in", frozenset({"containsComponent", "recordedForObject"})),
            (
                "out",
                frozenset(
                    {
                        "hasComponentType",
                        "hasMaterial",
                        "hasDesignQuantity",
                        "manufacturedBy",
                    }
                ),
            ),
        ),
        "ComponentType": (("in", frozenset({"hasComponentType"})),),
        "IfcMaterial": (("in", frozenset({"hasMaterial", "ofMaterial"})),),
        "DesignQuantity": (("in", frozenset({"hasDesignQuantity", "derivedFrom"})),),
        "ManufacturingProcessTemplate": (
            ("in", frozenset({"hasProcessTemplate"})),
            ("out", frozenset({"hasStage"})),
        ),
        "ProductionStage": (
            ("in", frozenset({"hasStage", "associatedWithProcess"})),
            ("out", frozenset({"hasActivity"})),
        ),
        "ManufacturingActivity": (
            (
                "in",
                frozenset(
                    {"hasActivity", "manufacturedBy", "associatedWithProcess"}
                ),
            ),
            ("out", frozenset({"usesResource"})),
        ),
        "ManufacturingResource": (
            ("in", frozenset({"usesResource", "recordedForResource"})),
        ),
        "MaterialConsumption": (("out", frozenset({"hasQuantity"})),),
        "EnergyConsumption": (("out", frozenset({"hasQuantity"})),),
        "ConsumptionQuantity": (("in", frozenset({"hasQuantity"})),),
        "EmissionFactor": (("in", frozenset({"hasFactor"})),),
        "EnergyCarrier": (("in", frozenset({"ofCarrier"})),),
        "CarbonEmission": (("out", frozenset({"hasCarbonDriver"})),),
    }
    for node_id in nodes_by_id:
        connected = False
        for direction, relations in requirements[node_classes[node_id]]:
            candidates = outgoing[node_id] if direction == "out" else incoming[node_id]
            if any(str(edge["type"]) in relations for edge in candidates):
                connected = True
                break
        if not connected:
            raise CanonicalSchemaError(f"canonical node {node_id!r} is orphan/unused")


def _parse_graph(payload: bytes) -> _GraphView:
    graph = _strict_json_bytes(payload, "multigranular_carbon_kg.json")
    _expect_keys(graph, _GRAPH_KEYS, "graph")
    if graph["schemaVersion"] != SCHEMA_VERSION:
        raise CanonicalSchemaError(f"graph schemaVersion must be {SCHEMA_VERSION!r}")
    raw_nodes = graph["nodes"]
    raw_edges = graph["edges"]
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise CanonicalSchemaError("graph nodes and edges must be arrays")

    nodes_by_id: dict[str, Mapping[str, Any]] = {}
    node_classes: dict[str, str] = {}
    for index, raw in enumerate(raw_nodes):
        node = _expect_keys(raw, _NODE_KEYS, f"graph.nodes[{index}]")
        node_id = _text(node["id"], f"graph.nodes[{index}].id")
        if node_id in nodes_by_id:
            raise CanonicalSchemaError(f"node id {node_id!r} is duplicated")
        labels = node["labels"]
        if (
            not isinstance(labels, list)
            or not labels
            or any(type(label) is not str or not label for label in labels)
            or labels != sorted(set(labels))
        ):
            raise CanonicalSchemaError(f"node {node_id!r} has invalid canonical labels")
        forbidden = set(labels) & FORBIDDEN_RUNTIME_LABELS
        if forbidden:
            raise CanonicalSchemaError(
                f"node {node_id!r} uses forbidden legacy labels {sorted(forbidden)}"
            )
        excluded_factory_types = set(labels) & EXCLUDED_FACTORY_BACKBONE_TYPES
        if excluded_factory_types:
            raise CanonicalSchemaError(
                f"node {node_id!r} uses excluded factory component refinements "
                f"{sorted(excluded_factory_types)}"
            )
        app_labels = set(labels) & _APP_CLASSES
        if len(app_labels) != 1:
            raise CanonicalSchemaError(
                f"node {node_id!r} must have exactly one application class"
            )
        invalid = [
            label
            for label in labels
            if label not in _APP_CLASSES and not _IFC_REFINEMENT_RE.fullmatch(label)
        ]
        if invalid:
            raise CanonicalSchemaError(
                f"node {node_id!r} has unsupported labels {invalid}"
            )
        _validate_props(node["props"], f"node[{node_id}].props")
        nodes_by_id[node_id] = node
        node_classes[node_id] = next(iter(app_labels))

    edges: list[Mapping[str, Any]] = []
    edge_ids: set[str] = set()
    occurrence_ids: set[str] = set()
    outgoing: dict[str, list[Mapping[str, Any]]] = {key: [] for key in nodes_by_id}
    incoming: dict[str, list[Mapping[str, Any]]] = {key: [] for key in nodes_by_id}
    for index, raw in enumerate(raw_edges):
        edge = _expect_keys(raw, _EDGE_KEYS, f"graph.edges[{index}]")
        edge_id = _text(edge["id"], f"graph.edges[{index}].id")
        src = _text(edge["src"], f"graph.edges[{index}].src")
        rel = _text(edge["type"], f"graph.edges[{index}].type")
        tgt = _text(edge["tgt"], f"graph.edges[{index}].tgt")
        occurrence = _text(
            edge["occurrenceId"], f"graph.edges[{index}].occurrenceId"
        )
        if edge_id in edge_ids:
            raise CanonicalSchemaError(f"edge id {edge_id!r} is duplicated")
        if occurrence in occurrence_ids:
            raise CanonicalSchemaError(
                f"edge occurrenceId {occurrence!r} is duplicated"
            )
        edge_ids.add(edge_id)
        occurrence_ids.add(occurrence)
        if src not in nodes_by_id or tgt not in nodes_by_id:
            raise CanonicalSchemaError(f"edge {edge_id!r} has a missing endpoint")
        if rel in FORBIDDEN_RUNTIME_RELATIONS:
            raise CanonicalSchemaError(f"edge {edge_id!r} uses forbidden relation {rel!r}")
        if rel not in _RELATIONS:
            raise CanonicalSchemaError(f"edge {edge_id!r} uses unsupported relation {rel!r}")
        source_class = node_classes[src]
        target_class = node_classes[tgt]
        valid = (source_class, rel, target_class) in _PRINCIPAL_TRIPLES
        if rel == "associatedWithProcess":
            valid = source_class == "EnergyConsumption" and target_class in {
                "ProductionStage",
                "ManufacturingActivity",
            }
        elif rel == "recordedForResource":
            valid = source_class == "EnergyConsumption" and target_class == "ManufacturingResource"
        elif rel == "directlyPrecedes":
            valid = (
                source_class == "ManufacturingActivity"
                and target_class == "ManufacturingActivity"
            )
        if not valid:
            raise CanonicalSchemaError(
                f"edge {edge_id!r} has invalid typed triple {source_class}-{rel}->{target_class}"
            )
        expected_id = stable_edge_id(rel, src, tgt, occurrence)
        if edge_id != expected_id:
            raise CanonicalSchemaError(f"edge {edge_id!r} is not the stable occurrence id")
        _validate_props(edge["props"], f"edge[{edge_id}].props")
        edges.append(edge)
        outgoing[src].append(edge)
        incoming[tgt].append(edge)

    _validate_no_orphans(nodes_by_id, node_classes, outgoing, incoming)
    ordered_nodes = tuple(nodes_by_id[node_id] for node_id in sorted(nodes_by_id))
    ordered_edges = tuple(sorted(edges, key=_edge_sort_key))
    return _GraphView(
        payload=graph,
        nodes=ordered_nodes,
        edges=ordered_edges,
        nodes_by_id=MappingProxyType(nodes_by_id),
        outgoing=MappingProxyType(
            {key: tuple(sorted(value, key=_edge_sort_key)) for key, value in outgoing.items()}
        ),
        incoming=MappingProxyType(
            {key: tuple(sorted(value, key=_edge_sort_key)) for key, value in incoming.items()}
        ),
        node_classes=MappingProxyType(node_classes),
    )


def _graph_document_from_view(graph: _GraphView) -> CanonicalV2GraphDocument:
    document = CanonicalV2GraphDocument(
        schema_version=SCHEMA_VERSION,
        nodes=tuple(_freeze(node) for node in graph.nodes),
        edges=tuple(_freeze(edge) for edge in graph.edges),
    )
    _VALID_GRAPH_DOCUMENTS.add(document)
    return document


def load_canonical_v2_graph_document(
    graph_path: Path | str,
) -> CanonicalV2GraphDocument:
    """Strictly validate one standalone canonical-v2 graph JSON document.

    The same parser used by the frozen-release reader enforces document shape,
    vocabulary, typed triples, occurrence identity, properties and endpoints;
    the existing fact builder then enforces every graph-contained calculation,
    provenance and attribution path.  Release-envelope and external rejected-
    input validation still require :func:`load_canonical_v2_context`.
    """

    path = Path(graph_path).expanduser()
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CanonicalSchemaError(
            f"canonical graph document is unreadable: {path}"
        ) from exc
    graph = _parse_graph(payload)
    _validate_standalone_graph_semantics(graph)
    return _graph_document_from_view(graph)


def _cypher_value(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_cypher_value(item) for item in value) + "]"
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return "'" + escaped + "'"


def _render_cypher(graph: _GraphView) -> str:
    lines = [
        "// DM2C M2.3 canonical v2 carbon knowledge graph",
        f"// schemaVersion={SCHEMA_VERSION}",
        "",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:DM2CEntity) REQUIRE n.id IS UNIQUE;",
        "",
        "// Nodes",
    ]
    for node in graph.nodes:
        labels = ":".join(("DM2CEntity", *node["labels"]))
        lines.append(f"MERGE (n:{labels} {{id: {_cypher_value(node['id'])}}})")
        props = node["props"]
        if props:
            rendered = ", ".join(
                f"`{key.replace('`', '``')}`: {_cypher_value(value)}"
                for key, value in sorted(props.items())
            )
            lines.append(f"SET n += {{ {rendered} }};")
        else:
            lines.append(";")
    lines.extend(("", "// Relationships"))
    for edge in graph.edges:
        lines.append(
            f"MATCH (a:DM2CEntity {{id: {_cypher_value(edge['src'])}}}), "
            f"(b:DM2CEntity {{id: {_cypher_value(edge['tgt'])}}})"
        )
        lines.append(
            f"MERGE (a)-[r:{edge['type']} {{occurrenceId: {_cypher_value(edge['occurrenceId'])}}}]->(b)"
        )
        props = edge["props"]
        if props:
            rendered = ", ".join(
                f"`{key.replace('`', '``')}`: {_cypher_value(value)}"
                for key, value in sorted(props.items())
            )
            lines.append(f"SET r += {{ {rendered} }};")
        else:
            lines.append(";")
    return "\n".join(lines)


def _validate_cypher(payload: bytes, graph: _GraphView) -> None:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise CanonicalSchemaError("Cypher must not contain a UTF-8 BOM")
    if b"\r" in payload:
        raise CanonicalSchemaError("Cypher contains a noncanonical line ending")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalSchemaError("Cypher is not strict UTF-8") from exc
    if text != _render_cypher(graph):
        raise CanonicalSchemaError("Cypher differs from deterministic graph serialization")


def _validation_counts(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    accepted = [row for row in rows if row["status"] == "accepted"]
    rejected = [row for row in rows if row["status"] == "rejected"]
    accepted_by_kind = Counter(row["kind"] for row in accepted)
    rejected_by_kind = Counter(row["kind"] for row in rejected)
    rejected_by_reason = Counter(row["reasonCode"] for row in rejected)
    candidate_count = len(rows)
    return {
        "candidateCount": candidate_count,
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "acceptedByKind": dict(sorted(accepted_by_kind.items())),
        "rejectedByKind": dict(
            sorted((key, value) for key, value in rejected_by_kind.items() if key)
        ),
        "rejectedByReason": dict(sorted(rejected_by_reason.items())),
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": len(accepted) / candidate_count if candidate_count else 0.0,
    }


def _same_json(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return set(left) == set(right) and all(
            _same_json(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right)
        )
    return left == right


def _expected_stats(
    graph: _GraphView, rows: Sequence[Mapping[str, str]], module_id: str
) -> dict[str, Any]:
    labels = Counter(label for node in graph.nodes for label in node["labels"])
    relations = Counter(edge["type"] for edge in graph.edges)
    modes = Counter(
        str(node["props"].get("attributionMode", ""))
        for node in graph.nodes
        if graph.node_classes[str(node["id"])] == "EnergyConsumption"
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "moduleId": module_id,
        "nodeCount": len(graph.nodes),
        "edgeCount": len(graph.edges),
        "applicationClassCounts": {
            label: labels.get(label, 0) for label in APPLICATION_CLASSES
        },
        "ifcRefinementCounts": dict(
            sorted(
                (label, count)
                for label, count in labels.items()
                if label not in _APP_CLASSES and label.startswith("Ifc")
            )
        ),
        "relationCounts": {
            relation: relations.get(relation, 0)
            for relation in (*PRINCIPAL_PREDICATES, *sorted(OPTIONAL_CONTEXT_PREDICATES))
        },
        "calculationCounts": {
            "material": labels.get("MaterialConsumption", 0),
            "energy": labels.get("EnergyConsumption", 0),
            "validZero": sum(
                node["props"].get("isValidZero") is True
                for node in graph.nodes
                if graph.node_classes[str(node["id"])] == "CarbonEmission"
            ),
        },
        "attributionCounts": {
            "direct": modes.get("direct", 0),
            "allocated": modes.get("allocated", 0),
            "process_only": modes.get("process_only", 0),
        },
        "validation": _validation_counts(rows),
    }


def _validate_stats(
    stats_payload: bytes,
    graph: _GraphView,
    rows: Sequence[Mapping[str, str]],
    manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    stats = _strict_json_bytes(stats_payload, "multigranular_carbon_kg_stats.json")
    module = manifest["module"]
    assert isinstance(module, Mapping)
    module_id = _text(module["runtimeId"], "manifest.module.runtimeId")
    expected = _expected_stats(graph, rows, module_id)
    if not _same_json(stats, expected):
        raise CanonicalSchemaError("stats do not exactly reconcile to graph/validation")
    if not _same_json(manifest["counts"], expected):
        raise CanonicalSchemaError("manifest counts do not exactly equal recomputed stats")
    if not _same_json(manifest["coverage"], expected["validation"]):
        raise CanonicalSchemaError("manifest coverage does not equal validation coverage")
    return stats


def _scope_key(value: Any) -> str:
    if type(value) is not str or not value.strip():
        return ""
    normalized = re.sub(
        r"\s+", "", value.casefold().replace("\u2013", "-").replace("\u2014", "-")
    )
    match = re.match(r"^(a[0-9](?:-a[0-9])?)", normalized)
    return match.group(1) if match else normalized


def _unit_key(value: Any, path: str) -> str:
    text = _text(value, path).strip().casefold()
    text = text.replace("\u00b2", "2").replace("\u00b3", "3")
    text = re.sub(r"\*\*?\s*([23])$", r"\1", text)
    text = re.sub(r"\^\s*([23])$", r"\1", text)
    if text not in _UNIT_DIMENSIONS:
        raise CanonicalSchemaError(f"{path} uses an unknown exact unit {value!r}")
    return text


def _factor_denominator_from_unit(value: Any, path: str) -> str:
    text = _text(value, path)
    parts = text.split("/")
    if len(parts) != 2 or re.sub(r"\s+", "", parts[0]).casefold() != "kgco2e":
        raise CanonicalSchemaError(f"{path} must be exact kgCO2e/<unit>")
    return _unit_key(parts[1], path)


def _nonnegative(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CanonicalSchemaError(f"{path} must be a non-boolean number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise CanonicalSchemaError(f"{path} is outside the finite numeric range") from exc
    if not math.isfinite(number) or number < 0:
        raise CanonicalSchemaError(f"{path} must be finite and non-negative")
    return number


def _positive(value: Any, path: str) -> float:
    number = _nonnegative(value, path)
    if number <= 0:
        raise CanonicalSchemaError(f"{path} must be positive")
    return number


def _prop_text(props: Mapping[str, Any], key: str, path: str) -> str:
    if key not in props:
        raise CanonicalSchemaError(f"{path}.{key} is required")
    return _text(props[key], f"{path}.{key}")


def _relations(
    index: Mapping[str, tuple[Mapping[str, Any], ...]], entity_id: str, relation: str
) -> tuple[Mapping[str, Any], ...]:
    return tuple(edge for edge in index.get(entity_id, ()) if edge["type"] == relation)


def _one_outgoing(
    graph: _GraphView,
    source_id: str,
    relation: str,
    target_class: str,
) -> Mapping[str, Any]:
    edges = _relations(graph.outgoing, source_id, relation)
    if len(edges) != 1:
        raise CanonicalSchemaError(
            f"{source_id!r} must have exactly one outgoing {relation} edge"
        )
    edge = edges[0]
    if graph.node_classes[str(edge["tgt"])] != target_class:
        raise CanonicalSchemaError(
            f"{source_id!r} {relation} target must be {target_class}"
        )
    return edge


def _common_fact_props(
    consumption_props: Mapping[str, Any],
    quantity_props: Mapping[str, Any],
    emission_props: Mapping[str, Any],
    *,
    consumption_id: str,
) -> dict[str, Any]:
    fields = (
        "recordId",
        "sourceIdentity",
        "sourceRecordId",
        "evidenceSourceId",
        "formulaCode",
        "recordedScope",
        "requestedScope",
        "systemBoundary",
        "isValidZero",
    )
    common: dict[str, Any] = {}
    for field in fields:
        values = []
        for name, props in (
            ("consumption", consumption_props),
            ("quantity", quantity_props),
            ("emission", emission_props),
        ):
            if field not in props:
                raise CanonicalSchemaError(
                    f"{consumption_id!r} {name} is missing common field {field}"
                )
            values.append(props[field])
        if any(type(value) is not type(values[0]) for value in values[1:]) or not all(
            value == values[0] for value in values[1:]
        ):
            raise CanonicalSchemaError(
                f"{consumption_id!r} has inconsistent common field {field}"
            )
        common[field] = values[0]
    for field in fields[:-1]:
        _text(common[field], f"fact[{consumption_id}].{field}")
    if type(common["isValidZero"]) is not bool:
        raise CanonicalSchemaError(f"fact[{consumption_id}].isValidZero must be boolean")
    expected_identity = stable_id(
        "SourceRecord", common["evidenceSourceId"], common["sourceRecordId"]
    )
    if common["sourceIdentity"] != expected_identity:
        raise CanonicalSchemaError(
            f"fact {consumption_id!r} sourceIdentity is not the stable source record id"
        )
    return common


def _parse_provenance_list(
    value: Any,
    *,
    path: str,
    expected_keys: frozenset[str],
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not value or not all(type(item) is str for item in value):
        raise CanonicalSchemaError(f"{path} must be a non-empty list of canonical JSON strings")
    parsed: list[Mapping[str, Any]] = []
    for index, text in enumerate(value):
        item = _strict_json_text(text, f"{path}[{index}]")
        _expect_keys(item, expected_keys, f"{path}[{index}]")
        if _canonical_json(item) != text:
            raise CanonicalSchemaError(f"{path}[{index}] is not canonical compact JSON")
        if any(isinstance(member, (Mapping, list)) for member in item.values()):
            raise CanonicalSchemaError(f"{path}[{index}] contains a legacy nested payload")
        parsed.append(item)
    return tuple(parsed)


def _numbers_match(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    except (TypeError, ValueError, OverflowError):
        return False


def _convert_provenance_value(
    value: float, source_unit: Any, target_unit: Any, path: str
) -> tuple[float, float]:
    source_key = _unit_key(source_unit, f"{path}.source_unit")
    target_key = _unit_key(target_unit, f"{path}.target_unit")
    if _UNIT_DIMENSIONS[source_key] != _UNIT_DIMENSIONS[target_key]:
        raise CanonicalSchemaError(f"{path} converts incompatible unit dimensions")
    factor = _UNIT_TO_CANONICAL[source_key] / _UNIT_TO_CANONICAL[target_key]
    return value * factor, factor


def _density_provenance_value(value: float, unit: Any, path: str) -> tuple[float, float]:
    key = (
        _text(unit, path)
        .strip()
        .casefold()
        .replace(" ", "")
        .replace("\u00b3", "3")
    )
    factors = {"kg/m3": 1.0, "g/cm3": 1000.0}
    if key not in factors:
        raise CanonicalSchemaError(f"{path} uses an unknown exact density unit {unit!r}")
    factor = factors[key]
    return value * factor, factor


def _validate_conversion_step(
    conversion: Mapping[str, Any],
    *,
    expected_operand_id: str,
    expected_role: str,
    expected_source_id: str,
    expected_source_value: float,
    expected_source_unit: str,
    expected_normalized_value: float,
    expected_normalized_unit: str,
    expected_factor: float,
    expected_design_quantity_id: str | None,
    path: str,
) -> None:
    for field in ("operand_id", "role", "source_id", "source_unit", "normalized_unit"):
        _text(conversion[field], f"{path}.{field}")
    source_value = _nonnegative(conversion["source_value"], f"{path}.source_value")
    normalized_value = _nonnegative(
        conversion["normalized_value"], f"{path}.normalized_value"
    )
    factor = _positive(conversion["conversion_factor"], f"{path}.conversion_factor")
    expected_text = {
        "operand_id": expected_operand_id,
        "role": expected_role,
        "source_id": expected_source_id,
        "source_unit": expected_source_unit,
        "normalized_unit": expected_normalized_unit,
    }
    if any(conversion[field] != expected for field, expected in expected_text.items()):
        raise CanonicalSchemaError(f"{path} has inconsistent conversion identity/units")
    if conversion["design_quantity_id"] != expected_design_quantity_id:
        raise CanonicalSchemaError(f"{path} has inconsistent DesignQuantity identity")
    if not (
        _numbers_match(source_value, expected_source_value)
        and _numbers_match(normalized_value, expected_normalized_value)
        and _numbers_match(factor, expected_factor)
    ):
        raise CanonicalSchemaError(f"{path} has inconsistent conversion arithmetic")


def _parse_provenance(
    quantity_props: Mapping[str, Any],
    graph: _GraphView,
    quantity_id: str,
    *,
    kind: str,
    formula_code: str,
    record_id: str,
    evidence_source_id: str,
    quantity_value: float,
    quantity_unit: str,
    quantity_conversion_factor: float,
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
    tuple[str, ...],
]:
    operands = _parse_provenance_list(
        quantity_props.get("orderedRawOperands"),
        path=f"quantity[{quantity_id}].orderedRawOperands",
        expected_keys=_OPERAND_KEYS,
    )
    conversions = _parse_provenance_list(
        quantity_props.get("conversionSteps"),
        path=f"quantity[{quantity_id}].conversionSteps",
        expected_keys=_CONVERSION_KEYS,
    )
    operand_ids: list[str] = []
    source_ids: list[str] = []
    by_role: dict[str, Mapping[str, Any]] = {}
    design_ids: list[str] = []
    for index, operand in enumerate(operands):
        operand_id = _text(
            operand["operand_id"], f"quantity[{quantity_id}].operand[{index}].operand_id"
        )
        source_id = _text(
            operand["source_id"], f"quantity[{quantity_id}].operand[{index}].source_id"
        )
        role = _text(
            operand["role"], f"quantity[{quantity_id}].operand[{index}].role"
        )
        unit_path = f"quantity[{quantity_id}].operand[{index}].unit"
        if role == "density":
            _density_provenance_value(1.0, operand["unit"], unit_path)
        elif role == "fraction":
            if operand["unit"] != "1":
                raise CanonicalSchemaError(f"{unit_path} must equal '1'")
        else:
            _unit_key(operand["unit"], unit_path)
        _nonnegative(operand["value"], f"quantity[{quantity_id}].operand[{index}].value")
        design_id = operand["design_quantity_id"]
        if design_id is not None:
            design_id = _text(
                design_id,
                f"quantity[{quantity_id}].operand[{index}].design_quantity_id",
            )
            if design_id not in graph.nodes_by_id:
                raise CanonicalSchemaError(
                    f"quantity {quantity_id!r} DesignQuantity operand is unknown"
                )
            design_ids.append(design_id)
        operand_ids.append(operand_id)
        source_ids.append(source_id)
        if role in by_role:
            raise CanonicalSchemaError(f"quantity {quantity_id!r} has duplicate operand roles")
        by_role[role] = operand
    if len(set(operand_ids)) != len(operand_ids):
        raise CanonicalSchemaError(f"quantity {quantity_id!r} has duplicate operand identities")
    if len(set(source_ids)) != len(source_ids):
        raise CanonicalSchemaError(f"quantity {quantity_id!r} has duplicate operand source ids")
    if len(set(design_ids)) != len(design_ids):
        raise CanonicalSchemaError(f"quantity {quantity_id!r} has duplicate DesignQuantity ids")

    formula_roles = {
        "direct_mass": ("material_mass",),
        "measured_energy": ("energy_quantity",),
        "volume_density": ("material_volume", "density"),
        "area_thickness_density": ("material_area", "thickness", "density"),
        "fraction_of_design_quantity": ("parent_design_quantity", "fraction"),
    }
    allowed_formulas = (
        {"measured_energy"}
        if kind == "energy"
        else {
            "direct_mass",
            "volume_density",
            "area_thickness_density",
            "fraction_of_design_quantity",
        }
    )
    if formula_code not in allowed_formulas or set(by_role) != set(
        formula_roles.get(formula_code, ())
    ):
        raise CanonicalSchemaError(
            f"quantity {quantity_id!r} has an unsupported formula/operand role set"
        )

    intermediates: list[tuple[Mapping[str, Any], float, str, float]] = []
    if formula_code in {"direct_mass", "measured_energy"}:
        operand = by_role[formula_roles[formula_code][0]]
        raw_result = _nonnegative(operand["value"], "direct provenance value")
        raw_result_unit = str(operand["unit"])
    elif formula_code == "volume_density":
        volume = by_role["material_volume"]
        density = by_role["density"]
        if volume["design_quantity_id"] is None:
            raise CanonicalSchemaError("volume_density requires a parent DesignQuantity")
        volume_value = _nonnegative(volume["value"], "volume provenance value")
        density_value = _nonnegative(density["value"], "density provenance value")
        volume_m3, volume_factor = _convert_provenance_value(
            volume_value, volume["unit"], "m3", "volume provenance"
        )
        density_kg_m3, density_factor = _density_provenance_value(
            density_value, density["unit"], "density provenance"
        )
        intermediates.extend(
            (
                (volume, volume_m3, "m3", volume_factor),
                (density, density_kg_m3, "kg/m3", density_factor),
            )
        )
        raw_result = volume_m3 * density_kg_m3
        raw_result_unit = "kg"
    elif formula_code == "area_thickness_density":
        area = by_role["material_area"]
        thickness = by_role["thickness"]
        density = by_role["density"]
        if area["design_quantity_id"] is None:
            raise CanonicalSchemaError("area_thickness_density requires a parent DesignQuantity")
        area_m2, area_factor = _convert_provenance_value(
            _nonnegative(area["value"], "area provenance value"),
            area["unit"], "m2", "area provenance",
        )
        thickness_m, thickness_factor = _convert_provenance_value(
            _nonnegative(thickness["value"], "thickness provenance value"),
            thickness["unit"], "m", "thickness provenance",
        )
        density_kg_m3, density_factor = _density_provenance_value(
            _nonnegative(density["value"], "density provenance value"),
            density["unit"], "density provenance",
        )
        intermediates.extend(
            (
                (area, area_m2, "m2", area_factor),
                (thickness, thickness_m, "m", thickness_factor),
                (density, density_kg_m3, "kg/m3", density_factor),
            )
        )
        raw_result = area_m2 * thickness_m * density_kg_m3
        raw_result_unit = "kg"
    else:
        parent = by_role["parent_design_quantity"]
        fraction = by_role["fraction"]
        if parent["design_quantity_id"] is None:
            raise CanonicalSchemaError(
                "fraction_of_design_quantity requires a parent DesignQuantity"
            )
        parent_value = _nonnegative(parent["value"], "parent provenance value")
        fraction_value = _nonnegative(fraction["value"], "fraction provenance value")
        if fraction_value > 1 or fraction["unit"] != "1":
            raise CanonicalSchemaError("fraction provenance must be dimensionless in [0,1]")
        intermediates.extend(
            (
                (parent, parent_value, str(parent["unit"]), 1.0),
                (fraction, fraction_value, "1", 1.0),
            )
        )
        raw_result = parent_value * fraction_value
        raw_result_unit = str(parent["unit"])

    if len(conversions) != len(intermediates) + 1:
        raise CanonicalSchemaError(
            f"quantity {quantity_id!r} has a noncanonical conversion-step count"
        )
    for index, (operand, normalized_value, normalized_unit, factor) in enumerate(
        intermediates
    ):
        _validate_conversion_step(
            conversions[index],
            expected_operand_id=str(operand["operand_id"]),
            expected_role=str(operand["role"]),
            expected_source_id=str(operand["source_id"]),
            expected_source_value=float(operand["value"]),
            expected_source_unit=str(operand["unit"]),
            expected_normalized_value=normalized_value,
            expected_normalized_unit=normalized_unit,
            expected_factor=factor,
            expected_design_quantity_id=(
                str(operand["design_quantity_id"])
                if operand["design_quantity_id"] is not None
                else None
            ),
            path=f"quantity[{quantity_id}].conversion[{index}]",
        )

    expected_quantity, expected_final_factor = _convert_provenance_value(
        raw_result, raw_result_unit, quantity_unit, "calculated quantity provenance"
    )
    final = conversions[-1]
    _validate_conversion_step(
        final,
        expected_operand_id=stable_id(
            "CalculatedQuantityOperand", evidence_source_id, record_id
        ),
        expected_role="calculated_quantity",
        expected_source_id=evidence_source_id,
        expected_source_value=raw_result,
        expected_source_unit=raw_result_unit,
        expected_normalized_value=quantity_value,
        expected_normalized_unit=quantity_unit,
        expected_factor=quantity_conversion_factor,
        expected_design_quantity_id=None,
        path=f"quantity[{quantity_id}].conversion[{len(conversions) - 1}]",
    )
    if not (
        _numbers_match(quantity_value, expected_quantity)
        and _numbers_match(quantity_conversion_factor, expected_final_factor)
    ):
        raise CanonicalSchemaError(
            f"quantity {quantity_id!r} final conversion does not reproduce quantityValue"
        )

    derived_edges = _relations(graph.outgoing, quantity_id, "derivedFrom")
    derived_ids = [str(edge["tgt"]) for edge in derived_edges]
    if len(derived_ids) != len(set(derived_ids)) or set(derived_ids) != set(design_ids):
        raise CanonicalSchemaError(
            f"quantity {quantity_id!r} DesignQuantity provenance does not reconcile"
        )
    for design_id in derived_ids:
        if graph.node_classes[design_id] != "DesignQuantity":
            raise CanonicalSchemaError(
                f"quantity {quantity_id!r} derives from a non-DesignQuantity"
            )
    return operands, conversions, tuple(design_ids)


def _accepted_ledger(rows: Sequence[Mapping[str, str]]) -> dict[str, Mapping[str, str]]:
    accepted: dict[str, Mapping[str, str]] = {}
    for row in rows:
        if row["status"] == "accepted":
            accepted[row["recordId"]] = row
    return accepted


def _module_ownership(graph: _GraphView, manifest: Mapping[str, Any]) -> dict[str, str]:
    module = manifest["module"]
    assert isinstance(module, Mapping)
    manifest_module_id = str(module["runtimeId"])
    modules = sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class == "ModularUnit"
    )
    if modules != [manifest_module_id]:
        raise CanonicalSchemaError(
            "graph must contain exactly the manifest's one ModularUnit"
        )
    ownership: dict[str, str] = {}
    for node_id, node_class in graph.node_classes.items():
        if node_class != "BuildingComponent":
            continue
        incoming = _relations(graph.incoming, node_id, "containsComponent")
        if len(incoming) != 1 or incoming[0]["src"] != manifest_module_id:
            raise CanonicalSchemaError(
                f"component {node_id!r} must belong to exactly the manifest module"
            )
        ownership[node_id] = manifest_module_id
    return ownership


def _context_ids(
    graph: _GraphView, consumption_id: str, common: Mapping[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    process_edges = _relations(graph.outgoing, consumption_id, "associatedWithProcess")
    resource_edges = _relations(graph.outgoing, consumption_id, "recordedForResource")
    expected_props = {
        "sourceRecordId": common["sourceRecordId"],
        "evidenceSourceId": common["evidenceSourceId"],
    }
    for edge in (*process_edges, *resource_edges):
        if dict(edge["props"]) != expected_props:
            raise CanonicalSchemaError(
                f"context edge {edge['occurrenceId']!r} has inconsistent source evidence"
            )
    process_ids = tuple(sorted(str(edge["tgt"]) for edge in process_edges))
    resource_ids = tuple(sorted(str(edge["tgt"]) for edge in resource_edges))
    if len(process_ids) != len(set(process_ids)) or len(resource_ids) != len(
        set(resource_ids)
    ):
        raise CanonicalSchemaError(
            f"consumption {consumption_id!r} has duplicate semantic context edges"
        )
    return process_ids, resource_ids


def _validate_fact_ledger_row(
    row: Mapping[str, str] | None,
    *,
    common: Mapping[str, Any],
    kind: str,
    consumption_id: str,
    quantity_id: str,
    factor_id: str,
    emission_id: str,
    factor_source_row_id: str,
) -> None:
    record_id = str(common["recordId"])
    if row is None:
        raise CanonicalSchemaError(
            f"graph fact {record_id!r} has no accepted validation row"
        )
    expected_fields = {
        "sourceIdentity": common["sourceIdentity"],
        "sourceRecordId": common["sourceRecordId"],
        "evidenceSourceId": common["evidenceSourceId"],
        "kind": kind,
        "consumptionId": consumption_id,
        "quantityId": quantity_id,
        "factorId": factor_id,
        "emissionId": emission_id,
        "formulaCode": common["formulaCode"],
        "isValidZero": str(common["isValidZero"]).lower(),
    }
    mismatches = [
        field for field, expected in expected_fields.items() if row[field] != expected
    ]
    if mismatches:
        raise CanonicalSchemaError(
            f"accepted validation row {record_id!r} differs in {sorted(mismatches)}"
        )
    evidence = _strict_json_text(
        row["evidenceJson"], f"validation[{record_id}].evidenceJson"
    )
    expected_evidence = {
        "factorSourceRowId": factor_source_row_id,
        "formulaCode": common["formulaCode"],
        "sourceRecordId": common["sourceRecordId"],
    }
    if any(evidence.get(field) != expected for field, expected in expected_evidence.items()):
        raise CanonicalSchemaError(
            f"accepted validation evidence {record_id!r} does not match the graph"
        )


def _validate_rejected_extension(field: str, value: Any, path: str) -> None:
    text_fields = {
        "associationId",
        "componentId",
        "materialId",
        "factorSourceRowId",
        "error",
        "designQuantityId",
        "factorId",
        "quantityUnit",
        "factorDenominator",
        "factorUnit",
        "operandId",
        "operandRole",
        "missingRole",
        "sourceNamespace",
        "targetComponentId",
        "targetMaterialId",
        "energyCarrierId",
        "recordedScope",
        "requestedScope",
    }
    list_fields = {
        "duplicateOperandIds",
        "duplicateRoles",
        "unexpectedRoles",
        "allowedRoles",
        "processIds",
    }
    number_fields = {"expectedNormalizedValue", "recordedNormalizedValue", "quantityValue", "factorValue"}
    if field in text_fields:
        _text(value, path)
    elif field in list_fields:
        if not isinstance(value, list) or not value:
            raise CanonicalSchemaError(f"{path} must be a non-empty string list")
        for index, item in enumerate(value):
            _text(item, f"{path}[{index}]")
    elif field in number_fields:
        _nonnegative(value, path)
    elif field == "factorPresent":
        if type(value) is not bool:
            raise CanonicalSchemaError(f"{path} must be boolean")
    elif field == "duplicateCount":
        if type(value) is not int or value < 2:
            raise CanonicalSchemaError(f"{path} must be an integer of at least two")
    else:  # pragma: no cover - guarded by the closed reason map
        raise CanonicalSchemaError(f"{path} is not a canonical rejected-evidence field")


def _validate_rejected_rows(
    rows: Sequence[Mapping[str, str]], graph: _GraphView
) -> None:
    graph_record_ids: set[str] = set()
    for node in graph.nodes:
        props = node["props"]
        if "recordId" in props:
            graph_record_ids.add(str(props["recordId"]))
    for row in rows:
        if row["status"] != "rejected":
            continue
        if row["recordId"] in graph_record_ids:
            raise CanonicalSchemaError(
                f"rejected validation row {row['recordId']!r} leaked into graph facts"
            )
        evidence = _strict_json_text(
            row["evidenceJson"], f"validation[{row['recordId']}].evidenceJson"
        )
        missing_source = row["reasonCode"] == "source_record_id_missing"
        expected_identity = (
            stable_id(
                "ValidationRecord", row["evidenceSourceId"], row["recordId"]
            )
            if missing_source
            else stable_id(
                "SourceRecord", row["evidenceSourceId"], row["sourceRecordId"]
            )
        )
        expected = {
            "sourceIdentity": expected_identity,
            "sourceRecordId": row["sourceRecordId"],
            "evidenceSourceId": row["evidenceSourceId"],
            "kind": row["kind"],
        }
        if row["sourceIdentity"] != expected_identity or any(
            evidence.get(field) != value for field, value in expected.items()
        ):
            raise CanonicalSchemaError(
                f"rejected validation evidence {row['recordId']!r} is inconsistent"
            )
        if "formulaCode" in evidence:
            if evidence["formulaCode"] != row["formulaCode"]:
                raise CanonicalSchemaError(
                    f"rejected validation evidence {row['recordId']!r} formula is inconsistent"
                )
        elif row["formulaCode"]:
            raise CanonicalSchemaError(
                f"rejected validation evidence {row['recordId']!r} omits its formula"
            )
        reason_fields = _REJECTED_REASON_FIELDS.get(row["reasonCode"])
        if reason_fields is None:
            raise CanonicalSchemaError(
                f"rejected validation reason {row['reasonCode']!r} is not canonical"
            )
        allowed = set(reason_fields) | set(_REJECTED_CONTEXT_FIELDS)
        base_fields = set(expected) | ({"formulaCode"} if "formulaCode" in evidence else set())
        extension_fields = set(evidence) - base_fields
        if not extension_fields <= allowed:
            raise CanonicalSchemaError(
                f"rejected validation evidence {row['recordId']!r} has invalid fields"
            )
        if row["reasonCode"] in {
            "evidence_ownership_mismatch",
            "factor_source_not_found",
            "factor_source_invalid",
            "design_quantity_ownership_mismatch",
        } and not set(reason_fields) <= extension_fields:
            raise CanonicalSchemaError(
                f"rejected validation evidence {row['recordId']!r} is incomplete"
            )
        for field in sorted(extension_fields):
            _validate_rejected_extension(
                field,
                evidence[field],
                f"validation[{row['recordId']}].evidenceJson.{field}",
            )


def _build_facts(
    graph: _GraphView,
    rows: Sequence[Mapping[str, str]],
    manifest: Mapping[str, Any],
) -> tuple[tuple[EmissionFact, ...], tuple[ProductContribution, ...]]:
    accepted = _accepted_ledger(rows)
    component_modules = _module_ownership(graph, manifest)
    configuration = manifest["configuration"]
    assert isinstance(configuration, Mapping)
    manifest_scope = _scope_key(configuration["requestedScope"])
    if not manifest_scope:
        raise CanonicalSchemaError("manifest requested scope is invalid")

    emission_ids = sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class == "CarbonEmission"
    )
    consumption_ids = {
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class in {"MaterialConsumption", "EnergyConsumption"}
    }
    quantity_ids = {
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class == "ConsumptionQuantity"
    }
    used_consumptions: set[str] = set()
    used_quantities: set[str] = set()
    used_ledger_rows: set[str] = set()
    used_source_records: set[tuple[str, str]] = set()
    contribution_keys: set[tuple[str, ...]] = set()
    facts: list[EmissionFact] = []
    contributions: list[ProductContribution] = []

    for emission_id in emission_ids:
        generated = _relations(graph.outgoing, emission_id, "hasCarbonDriver")
        if len(generated) != 1:
            raise CanonicalSchemaError(
                f"CarbonEmission {emission_id!r} must have exactly one hasCarbonDriver source"
            )
        consumption_id = str(generated[0]["tgt"])
        consumption_class = graph.node_classes[consumption_id]
        if consumption_class not in {"MaterialConsumption", "EnergyConsumption"}:
            raise CanonicalSchemaError(
                f"CarbonEmission {emission_id!r} source is not a consumption"
            )
        incoming_emissions = _relations(
            graph.incoming, consumption_id, "hasCarbonDriver"
        )
        if len(incoming_emissions) != 1 or incoming_emissions[0]["src"] != emission_id:
            raise CanonicalSchemaError(
                f"consumption {consumption_id!r} must have exactly one incoming emission"
            )
        if consumption_id in used_consumptions:
            raise CanonicalSchemaError(
                f"consumption {consumption_id!r} is used by multiple emissions"
            )
        used_consumptions.add(consumption_id)

        quantity_edge = _one_outgoing(
            graph, consumption_id, "hasQuantity", "ConsumptionQuantity"
        )
        factor_edge = _one_outgoing(
            graph, consumption_id, "hasFactor", "EmissionFactor"
        )
        quantity_id = str(quantity_edge["tgt"])
        factor_id = str(factor_edge["tgt"])
        quantity_incoming = _relations(graph.incoming, quantity_id, "hasQuantity")
        if len(quantity_incoming) != 1 or quantity_id in used_quantities:
            raise CanonicalSchemaError(
                f"ConsumptionQuantity {quantity_id!r} must belong to one fact"
            )
        used_quantities.add(quantity_id)

        consumption_props = graph.nodes_by_id[consumption_id]["props"]
        quantity_props = graph.nodes_by_id[quantity_id]["props"]
        factor_props = graph.nodes_by_id[factor_id]["props"]
        emission_props = graph.nodes_by_id[emission_id]["props"]
        common = _common_fact_props(
            consumption_props,
            quantity_props,
            emission_props,
            consumption_id=consumption_id,
        )
        record_id = str(common["recordId"])
        if record_id in used_ledger_rows:
            raise CanonicalSchemaError(f"recordId {record_id!r} materializes twice")
        used_ledger_rows.add(record_id)
        source_record_key = (
            str(common["evidenceSourceId"]),
            str(common["sourceRecordId"]),
        )
        if source_record_key in used_source_records:
            raise CanonicalSchemaError(
                f"accepted source record {source_record_key!r} materializes twice"
            )
        used_source_records.add(source_record_key)

        reciprocal = {
            "consumption.quantityId": (consumption_props.get("quantityId"), quantity_id),
            "consumption.factorId": (consumption_props.get("factorId"), factor_id),
            "consumption.emissionId": (consumption_props.get("emissionId"), emission_id),
            "quantity.consumptionId": (
                quantity_props.get("consumptionId"),
                consumption_id,
            ),
            "emission.consumptionId": (
                emission_props.get("consumptionId"),
                consumption_id,
            ),
            "emission.quantityId": (emission_props.get("quantityId"), quantity_id),
            "emission.factorId": (emission_props.get("factorId"), factor_id),
        }
        bad_reciprocal = [
            field for field, (actual, expected) in reciprocal.items() if actual != expected
        ]
        if bad_reciprocal:
            raise CanonicalSchemaError(
                f"fact {record_id!r} has inconsistent ids {sorted(bad_reciprocal)}"
            )

        factor_source_row_id = _prop_text(
            factor_props, "sourceRowId", f"factor[{factor_id}]"
        )
        expected_factor_id = stable_id("EmissionFactor", factor_source_row_id)
        factor_source_values = {
            factor_props.get("factorId"),
            factor_props.get("factorSourceId"),
            consumption_props.get("factorSourceId"),
            emission_props.get("factorSourceId"),
        }
        if (
            factor_id != expected_factor_id
            or factor_props.get("factorId") != factor_id
            or factor_source_values
            != {factor_id, factor_source_row_id}
        ):
            # The set contains both the node id and the source-row id by design.
            if not (
                factor_id == expected_factor_id
                and factor_props.get("factorId") == factor_id
                and factor_props.get("factorSourceId") == factor_source_row_id
                and consumption_props.get("factorSourceId") == factor_source_row_id
                and emission_props.get("factorSourceId") == factor_source_row_id
            ):
                raise CanonicalSchemaError(
                    f"factor {factor_id!r} does not preserve one source-row identity"
                )

        quantity_value = _nonnegative(
            quantity_props.get("quantityValue"), f"quantity[{quantity_id}].quantityValue"
        )
        quantity_unit = _prop_text(
            quantity_props, "quantityUnit", f"quantity[{quantity_id}]"
        )
        quantity_unit_key = _unit_key(
            quantity_unit, f"quantity[{quantity_id}].quantityUnit"
        )
        quantity_conversion_factor = _positive(
            quantity_props.get("quantityConversionFactor"),
            f"quantity[{quantity_id}].quantityConversionFactor",
        )
        factor_value = _nonnegative(
            factor_props.get("normalizedFactorValue"),
            f"factor[{factor_id}].normalizedFactorValue",
        )
        if not math.isclose(
            factor_value,
            _nonnegative(factor_props.get("factorValue"), f"factor[{factor_id}].factorValue"),
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise CanonicalSchemaError(f"factor {factor_id!r} normalized value is inconsistent")
        factor_denominator = _prop_text(
            factor_props, "normalizedDenominator", f"factor[{factor_id}]"
        )
        denominator_key = _unit_key(
            factor_denominator, f"factor[{factor_id}].normalizedDenominator"
        )
        declared_denominator_key = _unit_key(
            _prop_text(factor_props, "factorDenominator", f"factor[{factor_id}]"),
            f"factor[{factor_id}].factorDenominator",
        )
        factor_unit = _prop_text(factor_props, "factorUnit", f"factor[{factor_id}]")
        factor_unit_denominator = _factor_denominator_from_unit(
            factor_unit, f"factor[{factor_id}].factorUnit"
        )
        original_factor_unit = _prop_text(
            factor_props, "originalFactorUnit", f"factor[{factor_id}]"
        )
        if original_factor_unit != factor_unit:
            raise CanonicalSchemaError(
                f"factor {factor_id!r} original factor unit is inconsistent"
            )
        original_factor_value = _nonnegative(
            factor_props.get("originalFactorValue"),
            f"factor[{factor_id}].originalFactorValue",
        )
        denominator_unit_key = _unit_key(
            _prop_text(factor_props, "denominatorUnit", f"factor[{factor_id}]"),
            f"factor[{factor_id}].denominatorUnit",
        )
        expected_normalized_factor = (
            original_factor_value / _UNIT_TO_CANONICAL[factor_unit_denominator]
        )
        if (
            quantity_unit != factor_denominator
            or quantity_unit != factor_props.get("factorDenominator")
            or quantity_unit != factor_props.get("denominatorUnit")
            or _UNIT_DIMENSIONS[quantity_unit_key] != _UNIT_DIMENSIONS[denominator_key]
            or declared_denominator_key != denominator_key
            or denominator_unit_key != denominator_key
            or _UNIT_CANONICAL[factor_unit_denominator] != denominator_key
            or not math.isclose(
                factor_value,
                expected_normalized_factor,
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
        ):
            raise CanonicalSchemaError(
                f"fact {record_id!r} has incompatible quantity/factor unit or denominator"
            )

        emission_quantity = _nonnegative(
            emission_props.get("quantityValue"),
            f"emission[{emission_id}].quantityValue",
        )
        emission_factor = _nonnegative(
            emission_props.get("factorValue"), f"emission[{emission_id}].factorValue"
        )
        emission_value = _nonnegative(
            emission_props.get("emissionValue"),
            f"emission[{emission_id}].emissionValue",
        )
        emission_unit = _prop_text(
            emission_props, "emissionUnit", f"emission[{emission_id}]"
        )
        if emission_unit != "kgCO2e":
            raise CanonicalSchemaError(f"emission {emission_id!r} has invalid unit")
        if (
            not math.isclose(emission_quantity, quantity_value, rel_tol=0.0, abs_tol=0.0)
            or emission_props.get("quantityUnit") != quantity_unit
            or not math.isclose(emission_factor, factor_value, rel_tol=0.0, abs_tol=0.0)
            or _unit_key(
                emission_props.get("factorDenominator"),
                f"emission[{emission_id}].factorDenominator",
            )
            != denominator_key
        ):
            raise CanonicalSchemaError(
                f"emission {emission_id!r} q/EF provenance does not match linked nodes"
            )
        if not math.isclose(
            emission_value,
            quantity_value * factor_value,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise CanonicalSchemaError(
                f"emission {emission_id!r} does not equal quantity times factor"
            )

        scopes = {
            _scope_key(common["recordedScope"]),
            _scope_key(common["requestedScope"]),
            _scope_key(common["systemBoundary"]),
            manifest_scope,
        }
        if consumption_class == "MaterialConsumption":
            scopes.add(_scope_key(factor_props.get("systemBoundary")))
        if "" in scopes or len(scopes) != 1:
            raise CanonicalSchemaError(f"fact {record_id!r} has incompatible scopes")

        kind = "material" if consumption_class == "MaterialConsumption" else "energy"
        operands, conversions, design_quantity_ids = _parse_provenance(
            quantity_props,
            graph,
            quantity_id,
            kind=kind,
            formula_code=str(common["formulaCode"]),
            record_id=record_id,
            evidence_source_id=str(common["evidenceSourceId"]),
            quantity_value=quantity_value,
            quantity_unit=quantity_unit,
            quantity_conversion_factor=quantity_conversion_factor,
        )
        zero = quantity_value == 0.0
        if zero:
            if (
                emission_value != 0.0
                or common["isValidZero"] is not True
                or not any(_nonnegative(item["value"], "zero operand") == 0 for item in operands)
            ):
                raise CanonicalSchemaError(
                    f"fact {record_id!r} lacks measured valid-zero evidence"
                )
        elif common["isValidZero"] is not False:
            raise CanonicalSchemaError(
                f"nonzero fact {record_id!r} cannot be marked valid zero"
            )

        _validate_fact_ledger_row(
            accepted.get(record_id),
            common=common,
            kind=kind,
            consumption_id=consumption_id,
            quantity_id=quantity_id,
            factor_id=factor_id,
            emission_id=emission_id,
            factor_source_row_id=factor_source_row_id,
        )
        process_ids, resource_ids = _context_ids(graph, consumption_id, common)
        recorded_edges = _relations(graph.outgoing, consumption_id, "recordedForObject")
        material_edges = _relations(graph.outgoing, consumption_id, "ofMaterial")
        carrier_edges = _relations(graph.outgoing, consumption_id, "ofCarrier")
        material_id: str | None = None
        carrier_id: str | None = None
        mode: str

        def add_contribution(
            *,
            key: tuple[str, ...],
            component_id: str,
            edge: Mapping[str, Any],
            projected_value: float,
            allocation_set_id: str | None = None,
            allocation_basis: str | None = None,
            raw_weight: float | None = None,
            raw_weight_unit: str | None = None,
            normalized_weight: float | None = None,
            evidence_record_id: str | None = None,
        ) -> None:
            if key in contribution_keys:
                raise CanonicalSchemaError(
                    f"semantic product contribution {key!r} is duplicated"
                )
            contribution_keys.add(key)
            module_id = component_modules.get(component_id)
            if module_id is None and graph.node_classes.get(component_id) == "ModularUnit":
                module_id = component_id
            if module_id is None:
                raise CanonicalSchemaError(
                    f"product component {component_id!r} has no module ownership"
                )
            contributions.append(
                ProductContribution(
                    key=key,
                    emission_id=emission_id,
                    consumption_id=consumption_id,
                    component_id=component_id,
                    module_id=module_id,
                    material_id=material_id,
                    carrier_id=carrier_id,
                    mode=mode,
                    source_emission_value=emission_value,
                    projected_value=projected_value,
                    emission_unit=emission_unit,
                    recorded_scope=str(common["recordedScope"]),
                    requested_scope=str(common["requestedScope"]),
                    system_boundary=str(common["systemBoundary"]),
                    recorded_for_occurrence_id=str(edge["occurrenceId"]),
                    allocation_set_id=allocation_set_id,
                    allocation_basis=allocation_basis,
                    raw_weight=raw_weight,
                    raw_weight_unit=raw_weight_unit,
                    normalized_weight=normalized_weight,
                    evidence_record_id=evidence_record_id,
                    source_record_id=str(common["sourceRecordId"]),
                    evidence_source_id=str(common["evidenceSourceId"]),
                )
            )

        if kind == "material":
            mode = "material"
            if (
                len(recorded_edges) != 1
                or len(material_edges) != 1
                or carrier_edges
                or process_ids
                or resource_ids
            ):
                raise CanonicalSchemaError(
                    f"material consumption {consumption_id!r} has an invalid path"
                )
            if "attributionMode" in consumption_props or "energyCarrierId" in consumption_props:
                raise CanonicalSchemaError(
                    f"material consumption {consumption_id!r} contains energy attribution"
                )
            if set(consumption_props) & _ALLOCATION_ONLY_FIELDS:
                raise CanonicalSchemaError(
                    f"material consumption {consumption_id!r} contains allocation fields"
                )
            product_edge = recorded_edges[0]
            material_edge = material_edges[0]
            if set(product_edge["props"]) & _ALLOCATION_PROP_FIELDS:
                raise CanonicalSchemaError("material product edge contains allocation fields")
            if dict(product_edge["props"]) != {"recordId": record_id} or dict(
                material_edge["props"]
            ) != {"recordId": record_id}:
                raise CanonicalSchemaError("material fact edges have inconsistent record evidence")
            component_id = str(product_edge["tgt"])
            material_id = str(material_edge["tgt"])
            if (
                consumption_props.get("productTargetId") != component_id
                or consumption_props.get("materialId") != material_id
            ):
                raise CanonicalSchemaError("material target properties do not match its edges")
            ownership = [
                edge
                for edge in _relations(graph.outgoing, component_id, "hasMaterial")
                if edge["tgt"] == material_id
            ]
            if not any(edge["occurrenceId"] == common["sourceRecordId"] for edge in ownership):
                raise CanonicalSchemaError(
                    "material sourceRecordId is not an exact hasMaterial occurrence"
                )
            owned_design = {
                str(edge["tgt"])
                for edge in _relations(
                    graph.outgoing, component_id, "hasDesignQuantity"
                )
            }
            if not set(design_quantity_ids) <= owned_design:
                raise CanonicalSchemaError(
                    "material DesignQuantity is not owned by its component"
                )
            add_contribution(
                key=("direct", emission_id),
                component_id=component_id,
                edge=product_edge,
                projected_value=emission_value,
            )
        else:
            if len(carrier_edges) != 1 or material_edges:
                raise CanonicalSchemaError(
                    f"energy consumption {consumption_id!r} requires one carrier and no material"
                )
            carrier_id = str(carrier_edges[0]["tgt"])
            if consumption_props.get("energyCarrierId") != carrier_id:
                raise CanonicalSchemaError("energyCarrierId does not match ofCarrier")
            declared_mode = consumption_props.get("attributionMode")
            if declared_mode not in {"direct", "allocated", "process_only"}:
                raise CanonicalSchemaError(
                    f"energy consumption {consumption_id!r} has invalid attributionMode"
                )
            mode = str(declared_mode)
            expected_node_attribution_fields = (
                {"attributionMode", "allocationSetId", "allocationBasis", "unattributedFraction"}
                if mode == "allocated"
                else {"attributionMode"}
            )
            if (
                set(consumption_props) & _ALLOCATION_NODE_FIELDS
                != expected_node_attribution_fields
                or set(consumption_props)
                & (_ALLOCATION_PROP_FIELDS - _ALLOCATION_NODE_FIELDS)
            ):
                raise CanonicalSchemaError(
                    f"energy consumption {consumption_id!r} has invalid allocation/attribution fields"
                )
            if mode == "direct":
                if len(recorded_edges) != 1 or set(consumption_props) & _ALLOCATION_ONLY_FIELDS:
                    raise CanonicalSchemaError("direct energy has incomplete/mixed attribution")
                product_edge = recorded_edges[0]
                if set(product_edge["props"]) & _ALLOCATION_PROP_FIELDS:
                    raise CanonicalSchemaError("direct energy edge contains allocation fields")
                expected_direct_props = {
                    "sourceRecordId": common["sourceRecordId"],
                    "evidenceSourceId": common["evidenceSourceId"],
                }
                if dict(product_edge["props"]) != expected_direct_props:
                    raise CanonicalSchemaError("direct energy edge evidence is inconsistent")
                component_id = str(product_edge["tgt"])
                if consumption_props.get("productTargetId") != component_id:
                    raise CanonicalSchemaError("direct energy target property is inconsistent")
                add_contribution(
                    key=("direct", emission_id),
                    component_id=component_id,
                    edge=product_edge,
                    projected_value=emission_value,
                )
            elif mode == "allocated":
                if not recorded_edges or "productTargetId" in consumption_props:
                    raise CanonicalSchemaError("allocated energy has incomplete attribution")
                allocation_set_id = _prop_text(
                    consumption_props,
                    "allocationSetId",
                    f"consumption[{consumption_id}]",
                )
                allocation_basis = _prop_text(
                    consumption_props,
                    "allocationBasis",
                    f"consumption[{consumption_id}]",
                )
                unattributed_fraction = _nonnegative(
                    consumption_props.get("unattributedFraction"),
                    f"consumption[{consumption_id}].unattributedFraction",
                )
                if unattributed_fraction > 1:
                    raise CanonicalSchemaError("allocated unattributedFraction exceeds one")
                weights: list[float] = []
                projected: list[float] = []
                for edge in recorded_edges:
                    props = edge["props"]
                    if set(props) == _ALLOCATION_FIELDS:
                        normalized_weight_value = props["normalizedWeight"]
                        if props["attributionMode"] != "allocated":
                            raise CanonicalSchemaError(
                                "allocated edge set/basis is inconsistent"
                            )
                    elif set(props) == _ALLOCATION_EDGE_FIELDS:
                        normalized_weight_value = props["allocatedFraction"]
                        if props["allocated"] is not True:
                            raise CanonicalSchemaError(
                                "allocated edge set/basis is inconsistent"
                            )
                    else:
                        raise CanonicalSchemaError(
                            "allocated product edge must have exactly seven fields"
                        )
                    if (
                        props["allocationSetId"] != allocation_set_id
                        or props["allocationBasis"] != allocation_basis
                    ):
                        raise CanonicalSchemaError("allocated edge set/basis is inconsistent")
                    raw_weight = _nonnegative(
                        props["rawWeight"], f"edge[{edge['id']}].rawWeight"
                    )
                    normalized_weight = _nonnegative(
                        normalized_weight_value,
                        f"edge[{edge['id']}].normalizedWeight",
                    )
                    if normalized_weight > 1:
                        raise CanonicalSchemaError("allocated normalizedWeight exceeds one")
                    raw_weight_unit = _text(
                        props["rawWeightUnit"], f"edge[{edge['id']}].rawWeightUnit"
                    )
                    evidence_record_id = _text(
                        props["evidenceRecordId"],
                        f"edge[{edge['id']}].evidenceRecordId",
                    )
                    component_id = str(edge["tgt"])
                    key = (
                        "allocated",
                        emission_id,
                        allocation_set_id,
                        component_id,
                    )
                    share = emission_value * normalized_weight
                    weights.append(normalized_weight)
                    projected.append(share)
                    add_contribution(
                        key=key,
                        component_id=component_id,
                        edge=edge,
                        projected_value=share,
                        allocation_set_id=allocation_set_id,
                        allocation_basis=allocation_basis,
                        raw_weight=raw_weight,
                        raw_weight_unit=raw_weight_unit,
                        normalized_weight=normalized_weight,
                        evidence_record_id=evidence_record_id,
                    )
                if not math.isclose(
                    math.fsum(weights) + unattributed_fraction,
                    1.0,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ) or not math.isclose(
                    math.fsum(projected),
                    emission_value * math.fsum(weights),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    raise CanonicalSchemaError("allocated energy is not conserved")
            else:
                if (
                    recorded_edges
                    or set(consumption_props) & _ALLOCATION_ONLY_FIELDS
                    or "productTargetId" in consumption_props
                    or (not process_ids and not resource_ids)
                ):
                    raise CanonicalSchemaError("process-only energy has an invalid context/path")

        facts.append(
            EmissionFact(
                emission_id=emission_id,
                consumption_id=consumption_id,
                quantity_id=quantity_id,
                factor_id=factor_id,
                record_id=record_id,
                source_identity=str(common["sourceIdentity"]),
                source_record_id=str(common["sourceRecordId"]),
                evidence_source_id=str(common["evidenceSourceId"]),
                factor_source_id=factor_source_row_id,
                kind=kind,
                mode=mode,
                material_id=material_id,
                carrier_id=carrier_id,
                quantity_value=quantity_value,
                quantity_unit=quantity_unit,
                factor_value=factor_value,
                factor_unit=factor_unit,
                factor_denominator=factor_denominator,
                emission_value=emission_value,
                emission_unit=emission_unit,
                recorded_scope=str(common["recordedScope"]),
                requested_scope=str(common["requestedScope"]),
                system_boundary=str(common["systemBoundary"]),
                formula_code=str(common["formulaCode"]),
                is_valid_zero=bool(common["isValidZero"]),
                ordered_raw_operands=tuple(_freeze(item) for item in operands),
                conversion_steps=tuple(_freeze(item) for item in conversions),
                design_quantity_ids=design_quantity_ids,
                process_ids=process_ids,
                resource_ids=resource_ids,
                factor_keyword=(
                    _prop_text(factor_props, "keyword", f"factor[{factor_id}]")
                    if "keyword" in factor_props
                    else None
                ),
                factor_source=(
                    _prop_text(factor_props, "source", f"factor[{factor_id}]")
                    if "source" in factor_props
                    else None
                ),
            )
        )

    if used_consumptions != consumption_ids:
        raise CanonicalSchemaError("one or more consumption nodes have no unique emission")
    if used_quantities != quantity_ids:
        raise CanonicalSchemaError("one or more ConsumptionQuantity nodes are not facts")
    if used_ledger_rows != set(accepted):
        raise CanonicalSchemaError("accepted validation rows and graph facts are not bijective")
    _validate_rejected_rows(rows, graph)
    return tuple(facts), tuple(sorted(contributions, key=lambda item: item.key))


def _standalone_accepted_rows(
    graph: _GraphView,
) -> tuple[Mapping[str, str], ...]:
    """Reconstruct only graph-materialized accepted rows for semantic reuse."""

    rows: list[dict[str, str]] = []
    for consumption_id in sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class in {"MaterialConsumption", "EnergyConsumption"}
    ):
        props = graph.nodes_by_id[consumption_id]["props"]
        path = f"consumption[{consumption_id}]"
        record_id = _prop_text(props, "recordId", path)
        source_identity = _prop_text(props, "sourceIdentity", path)
        source_record_id = _prop_text(props, "sourceRecordId", path)
        evidence_source_id = _prop_text(props, "evidenceSourceId", path)
        formula_code = _prop_text(props, "formulaCode", path)
        quantity_id = _prop_text(props, "quantityId", path)
        factor_id = _prop_text(props, "factorId", path)
        emission_id = _prop_text(props, "emissionId", path)
        factor_source_row_id = _prop_text(props, "factorSourceId", path)
        is_valid_zero = props.get("isValidZero")
        if type(is_valid_zero) is not bool:
            raise CanonicalSchemaError(f"{path}.isValidZero must be boolean")
        rows.append(
            {
                "recordId": record_id,
                "sourceIdentity": source_identity,
                "sourceRecordId": source_record_id,
                "evidenceSourceId": evidence_source_id,
                "kind": (
                    "material"
                    if graph.node_classes[consumption_id] == "MaterialConsumption"
                    else "energy"
                ),
                "status": "accepted",
                "reasonCode": "",
                "message": "",
                "consumptionId": consumption_id,
                "quantityId": quantity_id,
                "factorId": factor_id,
                "emissionId": emission_id,
                "formulaCode": formula_code,
                "isValidZero": str(is_valid_zero).lower(),
                "evidenceJson": _canonical_json(
                    {
                        "factorSourceRowId": factor_source_row_id,
                        "formulaCode": formula_code,
                        "sourceRecordId": source_record_id,
                    }
                ),
            }
        )
    return tuple(sorted(rows, key=lambda row: (row["recordId"], row["consumptionId"])))


def _validate_standalone_graph_semantics(graph: _GraphView) -> None:
    """Reuse full fact/path validation without manufacturing an external ledger."""

    module_ids = sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class == "ModularUnit"
    )
    if len(module_ids) != 1:
        raise CanonicalSchemaError(
            "standalone canonical graph must contain exactly one ModularUnit"
        )
    consumption_ids = sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class in {"MaterialConsumption", "EnergyConsumption"}
    )
    requested_scope = (
        _prop_text(
            graph.nodes_by_id[consumption_ids[0]]["props"],
            "requestedScope",
            f"consumption[{consumption_ids[0]}]",
        )
        if consumption_ids
        else "A1-A3"
    )
    manifest = {
        "module": {"runtimeId": module_ids[0]},
        "configuration": {"requestedScope": requested_scope},
    }
    _build_facts(graph, _standalone_accepted_rows(graph), manifest)


def _context_from_graph_view(
    graph: _GraphView,
    root: Path,
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
) -> CanonicalV2Context:
    facts, contributions = _build_facts(graph, rows, manifest)
    dimension_labels = {
        "component": {"BuildingComponent"},
        "module": {"ModularUnit"},
        "component_type": {"ComponentType"},
        "material": {"IfcMaterial"},
        "carrier": {"EnergyCarrier"},
        "process": {
            "ManufacturingProcessTemplate",
            "ProductionStage",
            "ManufacturingActivity",
        },
        "resource": {"ManufacturingResource"},
    }
    dimensions = {
        name: tuple(
            sorted(
                node_id
                for node_id, node_class in graph.node_classes.items()
                if node_class in labels
            )
        )
        for name, labels in dimension_labels.items()
    }
    ifc_components: dict[str, list[str]] = {}
    for node_id in dimensions["component"]:
        for label in graph.nodes_by_id[node_id]["labels"]:
            if label not in _APP_CLASSES:
                ifc_components.setdefault(str(label), []).append(node_id)
    frozen_nodes = MappingProxyType(
        {node_id: _freeze(node) for node_id, node in graph.nodes_by_id.items()}
    )
    frozen_edges = MappingProxyType(
        {
            str(edge["occurrenceId"]): _freeze(edge)
            for edge in graph.edges
        }
    )
    facts_by_emission = MappingProxyType({fact.emission_id: fact for fact in facts})
    contributions_by_key = MappingProxyType(
        {item.key: item for item in contributions}
    )
    context = CanonicalV2Context(
        release_dir=root,
        manifest=_freeze(manifest),
        emissions=facts,
        product_contributions=contributions,
        process_only_emissions=tuple(
            fact for fact in facts if fact.mode == "process_only"
        ),
        validation_rows=tuple(_freeze(row) for row in rows),
        _nodes_by_id=frozen_nodes,
        _edges_by_occurrence=frozen_edges,
        _facts_by_emission=facts_by_emission,
        _contributions_by_key=contributions_by_key,
        _dimension_ids=_freeze(dimensions),
        _ifc_components=_freeze(
            {key: tuple(sorted(value)) for key, value in ifc_components.items()}
        ),
        synthetic_energy=manifest.get("syntheticFactoryInputsUsed") is True,
    )
    _VALID_CONTEXTS.add(context)
    return context


def load_canonical_v2_context_from_graph_document(
    graph_path: Path | str,
    *,
    allow_synthetic: bool = True,
) -> CanonicalV2Context:
    """Load a demo-ready CarbonQL context from one validated graph JSON file.

    This path skips the six-file release envelope and external validation ledger.
    It is intended for demonstrations rather than audited paper experiments.
    """

    path = Path(graph_path).expanduser()
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CanonicalSchemaError(
            f"canonical graph document is unreadable: {path}"
        ) from exc
    graph = _parse_graph(payload)
    _validate_standalone_graph_semantics(graph)
    rows = _standalone_accepted_rows(graph)
    module_ids = sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class == "ModularUnit"
    )
    consumption_ids = sorted(
        node_id
        for node_id, node_class in graph.node_classes.items()
        if node_class in {"MaterialConsumption", "EnergyConsumption"}
    )
    requested_scope = (
        _prop_text(
            graph.nodes_by_id[consumption_ids[0]]["props"],
            "requestedScope",
            f"consumption[{consumption_ids[0]}]",
        )
        if consumption_ids
        else "A1-A3"
    )
    manifest = {
        "module": {"runtimeId": module_ids[0]},
        "configuration": {"requestedScope": requested_scope},
        "syntheticFactoryInputsUsed": bool(allow_synthetic),
        "releaseId": f"demo-graph:{path.name}",
    }
    return _context_from_graph_view(graph, path.parent, manifest, rows)


def load_canonical_v2_context(
    kg_dir: Path | str, *, allow_synthetic: bool = False,
    allow_unready_backbone: bool = False,
) -> CanonicalV2Context:
    """Load and fully validate one frozen six-file canonical-v2 release.

    ``allow_unready_backbone`` is limited to an ``actual-case`` release with
    ``releaseReady=false`` and no material-evidence descriptor.  It exists only
    for construction-only statistics; accounting consumers must retain the
    default fail-closed behaviour.
    """

    root, manifest, blobs = _load_envelope(
        kg_dir,
        allow_synthetic=allow_synthetic,
        allow_unready_backbone=allow_unready_backbone,
    )
    return _build_context(root, manifest, blobs)


def _build_context(
    root: Path, manifest: Mapping[str, Any], blobs: Mapping[str, bytes]
) -> CanonicalV2Context:
    graph = _parse_graph(blobs["graphJson"])
    rows = _read_validation_csv(blobs["validation"])
    _validate_cypher(blobs["cypher"], graph)
    _validate_stats(blobs["stats"], graph, rows, manifest)
    facts, contributions = _build_facts(graph, rows, manifest)
    _validate_alignment(blobs["alignmentReport"], graph, rows, manifest, facts, contributions)
    return _context_from_graph_view(graph, root, manifest, rows)


def _validate_alignment(
    payload: bytes,
    graph: _GraphView,
    rows: Sequence[Mapping[str, str]],
    manifest: Mapping[str, Any],
    facts: Sequence[EmissionFact],
    contributions: Sequence[ProductContribution],
) -> None:
    report = _strict_json_bytes(payload, "m2_alignment_report.json")
    _expect_keys(
        report,
        {
            "schemaVersion",
            "status",
            "highSeverityViolationCount",
            "findings",
            "gates",
            "projectionTotals",
            "checkedCounts",
        },
        "alignmentReport",
    )
    if report["schemaVersion"] != SCHEMA_VERSION or report["status"] != "pass":
        raise CanonicalSchemaError("stored alignment report is not a canonical pass")
    if (
        type(report["highSeverityViolationCount"]) is not int
        or report["highSeverityViolationCount"] != 0
        or report["findings"] != []
    ):
        raise CanonicalSchemaError("stored alignment report has high-severity findings")
    gates = _expect_keys(report["gates"], _GATE_KEYS, "alignmentReport.gates")
    if not _same_json(gates, manifest["gates"]):
        raise CanonicalSchemaError("stored alignment gates differ from manifest gates")
    expected_checked = {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "validationRows": len(rows),
    }
    if not _same_json(report["checkedCounts"], expected_checked):
        raise CanonicalSchemaError("stored alignment checkedCounts are stale")
    totals = _expect_keys(
        report["projectionTotals"], {"product", "sourceProcess"}, "alignmentReport.projectionTotals"
    )
    expected_product = math.fsum(item.projected_value for item in contributions)
    expected_source = math.fsum(
        fact.emission_value for fact in facts if fact.kind == "energy"
    )
    actual_product = _nonnegative(
        totals["product"], "alignmentReport.projectionTotals.product"
    )
    actual_source = _nonnegative(
        totals["sourceProcess"], "alignmentReport.projectionTotals.sourceProcess"
    )
    if not math.isclose(
        actual_product, expected_product, rel_tol=1e-9, abs_tol=1e-9
    ) or not math.isclose(
        actual_source, expected_source, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise CanonicalSchemaError("stored alignment projection totals are stale")


def _require_context(context: Any) -> CanonicalV2Context:
    if not isinstance(context, CanonicalV2Context):
        raise TypeError("context must be a CanonicalV2Context")
    if context not in _VALID_CONTEXTS:
        raise CanonicalSchemaError(
            "context was not produced by load_canonical_v2_context()"
        )
    return context


def _require_graph_document(document: Any) -> CanonicalV2GraphDocument:
    if not isinstance(document, CanonicalV2GraphDocument):
        raise TypeError("document must be a CanonicalV2GraphDocument")
    if document not in _VALID_GRAPH_DOCUMENTS:
        raise CanonicalSchemaError(
            "document was not produced by a canonical-v2 graph loader"
        )
    return document


def graph_document_from_context(
    context: CanonicalV2Context,
) -> CanonicalV2GraphDocument:
    """Return a structural document from one fully validated release context."""

    context = _require_context(context)
    document = CanonicalV2GraphDocument(
        schema_version=SCHEMA_VERSION,
        nodes=tuple(
            _freeze(context._nodes_by_id[node_id])
            for node_id in sorted(context._nodes_by_id)
        ),
        edges=tuple(
            _freeze(edge)
            for edge in sorted(
                context._edges_by_occurrence.values(), key=_edge_sort_key
            )
        ),
    )
    _VALID_GRAPH_DOCUMENTS.add(document)
    return document


def iter_graph_nodes(
    document: CanonicalV2GraphDocument,
) -> Iterator[Mapping[str, Any]]:
    """Iterate deterministic immutable nodes from a validated graph document."""

    return iter(_require_graph_document(document).nodes)


def iter_graph_edges(
    document: CanonicalV2GraphDocument,
) -> Iterator[Mapping[str, Any]]:
    """Iterate deterministic immutable edge occurrences from a validated document."""

    return iter(_require_graph_document(document).edges)


def _scope_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _scope_key(_text(value, "requested_scope"))
    if not normalized:
        raise CanonicalSchemaError("requested_scope is invalid")
    return normalized


def _dimension_name(value: Any) -> str:
    if type(value) is not str or value not in {
        "component",
        "module",
        "component_type",
        "ifc_class",
        "material",
        "carrier",
        "process",
        "resource",
    }:
        raise CanonicalSchemaError(f"unknown canonical dimension {value!r}")
    return value


def iter_emissions(
    context: CanonicalV2Context, *, kind: str | None = None, requested_scope: str | None = None
) -> Iterator[EmissionFact]:
    context = _require_context(context)
    if kind not in {None, "material", "energy"}:
        raise CanonicalSchemaError(f"unknown emission kind {kind!r}")
    scope = _scope_filter(requested_scope)
    selected = tuple(
        fact
        for fact in context.emissions
        if (kind is None or fact.kind == kind)
        and (scope is None or _scope_key(fact.requested_scope) == scope)
    )
    return iter(selected)


def iter_product_contributions(
    context: CanonicalV2Context,
    *,
    component_id: str | None = None,
    module_id: str | None = None,
    material_id: str | None = None,
    carrier_id: str | None = None,
    requested_scope: str | None = None,
) -> Iterator[ProductContribution]:
    context = _require_context(context)
    filters = {
        "component": component_id,
        "module": module_id,
        "material": material_id,
        "carrier": carrier_id,
    }
    for dimension, entity_id in filters.items():
        if entity_id is not None:
            lookup_dimension(context, dimension, entity_id)
    scope = _scope_filter(requested_scope)
    selected = tuple(
        item
        for item in context.product_contributions
        if (component_id is None or item.component_id == component_id)
        and (module_id is None or item.module_id == module_id)
        and (material_id is None or item.material_id == material_id)
        and (carrier_id is None or item.carrier_id == carrier_id)
        and (scope is None or _scope_key(item.requested_scope) == scope)
    )
    return iter(selected)


def evidence_ids(
    context: CanonicalV2Context,
    emission_id: str,
    contribution_key: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    context = _require_context(context)
    if type(emission_id) is not str or emission_id not in context._facts_by_emission:
        raise CanonicalSchemaError(f"unknown CarbonEmission id {emission_id!r}")
    fact = context._facts_by_emission[emission_id]
    ordered: list[str] = []

    def add(value: str | None) -> None:
        if value and value not in ordered:
            ordered.append(value)

    for value in (
        fact.emission_id,
        fact.consumption_id,
        fact.quantity_id,
        fact.factor_id,
        fact.material_id,
        fact.carrier_id,
        *fact.design_quantity_ids,
        *fact.process_ids,
        *fact.resource_ids,
        fact.source_record_id,
        fact.evidence_source_id,
    ):
        add(value)
    if contribution_key is not None:
        if (
            not isinstance(contribution_key, tuple)
            or not contribution_key
            or not all(type(item) is str for item in contribution_key)
        ):
            raise CanonicalSchemaError("contribution_key must be one exact semantic tuple")
        contribution = context._contributions_by_key.get(contribution_key)
        if contribution is None or contribution.emission_id != emission_id:
            raise CanonicalSchemaError(
                f"unknown contribution key {contribution_key!r} for {emission_id!r}"
            )
        add(contribution.component_id)
        add(contribution.recorded_for_occurrence_id)
        add(contribution.evidence_record_id)
    return tuple(ordered)


def product_total(context: CanonicalV2Context, **filters: Any) -> float:
    return math.fsum(
        item.projected_value for item in iter_product_contributions(context, **filters)
    )


def source_process_total(
    context: CanonicalV2Context,
    *,
    carrier_id: str | None = None,
    requested_scope: str | None = None,
) -> float:
    context = _require_context(context)
    if carrier_id is not None:
        lookup_dimension(context, "carrier", carrier_id)
    scope = _scope_filter(requested_scope)
    return math.fsum(
        fact.emission_value
        for fact in context.emissions
        if fact.kind == "energy"
        and (carrier_id is None or fact.carrier_id == carrier_id)
        and (scope is None or _scope_key(fact.requested_scope) == scope)
    )


def dimension_ids(context: CanonicalV2Context, dimension: str) -> tuple[str, ...]:
    context = _require_context(context)
    dimension = _dimension_name(dimension)
    if dimension == "ifc_class":
        return tuple(sorted(context._ifc_components))
    return context._dimension_ids[dimension]


def lookup_dimension(context: CanonicalV2Context, dimension: str, entity_id: str) -> Any:
    context = _require_context(context)
    dimension = _dimension_name(dimension)
    entity_id = _text(entity_id, f"{dimension}_id")
    if dimension == "ifc_class":
        return components_for_ifc_class(context, entity_id)
    if entity_id not in context._dimension_ids[dimension]:
        raise CanonicalSchemaError(
            f"id {entity_id!r} is unknown for canonical dimension {dimension!r}"
        )
    return context._nodes_by_id[entity_id]


def components_for_ifc_class(context: CanonicalV2Context, ifc_class: str) -> tuple[str, ...]:
    context = _require_context(context)
    ifc_class = _text(ifc_class, "ifc_class")
    if ifc_class not in context._ifc_components:
        raise CanonicalSchemaError(f"unknown canonical IFC class {ifc_class!r}")
    return context._ifc_components[ifc_class]


def component_type_ids_for_component(
    context: CanonicalV2Context, component_id: str
) -> tuple[str, ...]:
    context = _require_context(context)
    lookup_dimension(context, "component", component_id)
    return tuple(
        sorted(
            edge["tgt"]
            for edge in context._edges_by_occurrence.values()
            if edge["src"] == component_id
            and edge["type"] == "hasComponentType"
        )
    )


def lookup_component(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "component", entity_id)


def lookup_module(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "module", entity_id)


def lookup_component_type(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "component_type", entity_id)


def lookup_material(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "material", entity_id)


def lookup_carrier(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "carrier", entity_id)


def lookup_process(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "process", entity_id)


def lookup_resource(context: CanonicalV2Context, entity_id: str) -> Mapping[str, Any]:
    return lookup_dimension(context, "resource", entity_id)


__all__ = [
    "CanonicalSchemaError",
    "CanonicalV2GraphDocument",
    "CanonicalV2Context",
    "EmissionFact",
    "ProductContribution",
    "components_for_ifc_class",
    "component_type_ids_for_component",
    "dimension_ids",
    "evidence_ids",
    "graph_document_from_context",
    "iter_emissions",
    "iter_graph_edges",
    "iter_graph_nodes",
    "iter_product_contributions",
    "load_canonical_v2_graph_document",
    "load_canonical_v2_context_from_graph_document",
    "load_canonical_v2_context",
    "lookup_carrier",
    "lookup_component",
    "lookup_component_type",
    "lookup_dimension",
    "lookup_material",
    "lookup_module",
    "lookup_process",
    "lookup_resource",
    "product_total",
    "source_process_total",
]
