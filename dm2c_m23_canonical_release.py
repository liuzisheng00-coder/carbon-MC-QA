#!/usr/bin/env python3
"""Deterministic, fail-closed canonical M2.3 v2 release runner."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import AbstractSet, Any, Iterable, Mapping, Optional
from uuid import uuid4

import openpyxl

from dm2c_m23_calculation import (
    AcceptedConsumption,
    ConsumptionCandidate,
    QuantityOperand,
    ValidationIssue,
    ValidationResultSet,
    validate_candidates,
)
from dm2c_m23_accounting import EnergyAllocationTarget, EnergyPopulationPlan
from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    CanonicalLPGGraph,
    stable_id,
)
from dm2c_m23_ifc import CanonicalIFCExtractor, IFCExtractionResult
from dm2c_multigranular_carbon_kg import (
    FactorLibrary,
    MultiGranularCarbonKGBuilder,
)


GRAPH_JSON = "multigranular_carbon_kg.json"
CYPHER = "multigranular_carbon_kg.cypher"
STATS_JSON = "multigranular_carbon_kg_stats.json"
VALIDATION_CSV = "multigranular_carbon_kg_validation.csv"
ALIGNMENT_JSON = "m2_alignment_report.json"
MANIFEST_JSON = "case_version_manifest.json"

OUTPUT_FILENAMES = {
    "graphJson": GRAPH_JSON,
    "cypher": CYPHER,
    "stats": STATS_JSON,
    "validation": VALIDATION_CSV,
    "alignmentReport": ALIGNMENT_JSON,
}

VALIDATION_FIELDS = (
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

_CONFIG_KEYS = frozenset(
    {
        "schemaVersion",
        "releaseProfile",
        "releaseReady",
        "ifc",
        "factorWorkbook",
        "ontology",
        "materialEvidence",
        "module",
        "requestedScope",
        "includeOpenings",
        "factoryInput",
        "generatedAtUtc",
    }
)
_OPTIONAL_CONFIG_KEYS = frozenset({"factoryTargetMap"})
_BINDING_KEYS = frozenset({"path", "sha256", "sizeBytes"})
_MODULE_KEYS = frozenset({"sourceIdentity", "name", "identitySource"})
_EVIDENCE_KEYS = frozenset({"schemaVersion", "records"})
_EVIDENCE_RECORD_KEYS = frozenset(
    {
        "recordId",
        "associationId",
        "componentId",
        "materialId",
        "factorSourceRowId",
        "formulaCode",
        "operands",
        "measured",
    }
)
_OPERAND_KEYS = frozenset(
    {"operandId", "role", "value", "unit", "sourceId", "designQuantityId"}
)
_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class CaseConfigError(ValueError):
    """Raised when the strict case configuration is malformed."""


class ReleaseBuildError(RuntimeError):
    """A classified fail-closed release error."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _strict_json_loads(text: str) -> Any:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        text,
        object_pairs_hook=pairs,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {token}")
        ),
    )


@dataclass(frozen=True)
class InputBinding:
    selected_path: str
    resolved_path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ModuleDeclaration:
    source_identity: str
    name: str
    identity_source: str


@dataclass(frozen=True)
class CaseConfig:
    schema_version: str
    release_profile: str
    release_ready: bool
    ifc: InputBinding
    factor_workbook: InputBinding
    ontology: InputBinding
    material_evidence: InputBinding | None
    module: ModuleDeclaration
    requested_scope: str
    include_openings: bool
    factory_input: InputBinding | None
    factory_target_map: InputBinding | None
    generated_at_utc: str
    config_path: Path


@dataclass(frozen=True)
class ReleaseArtifacts:
    graph_json: Path
    cypher: Path
    stats: Path
    validation: Path
    alignment_report: Path
    manifest: Path


@dataclass(frozen=True)
class EvidenceAssembly:
    candidates: tuple[ConsumptionCandidate, ...]
    rejected: tuple[ValidationIssue, ...]
    source_ledger_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "rejected", tuple(self.rejected))
        object.__setattr__(
            self,
            "source_ledger_identities",
            tuple(self.source_ledger_identities),
        )


@dataclass(frozen=True)
class FactoryContextNode:
    node_id: str
    labels: tuple[str, ...]
    props: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", tuple(self.labels))


@dataclass(frozen=True)
class FactoryContextEdge:
    src: str
    rel_type: str
    tgt: str
    props: Mapping[str, Any]
    occurrence_id: str


@dataclass(frozen=True)
class FactoryEvidenceAssembly:
    candidates: tuple[ConsumptionCandidate, ...]
    rejected: tuple[ValidationIssue, ...]
    plans: Mapping[str, EnergyPopulationPlan]
    context_nodes: tuple[FactoryContextNode, ...]
    context_edges: tuple[FactoryContextEdge, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "rejected", tuple(self.rejected))
        object.__setattr__(self, "context_nodes", tuple(self.context_nodes))
        object.__setattr__(self, "context_edges", tuple(self.context_edges))


@dataclass(frozen=True)
class ProcessDefinitionContext:
    nodes: tuple[FactoryContextNode, ...]
    edges: tuple[FactoryContextEdge, ...]
    activity_ids_by_template: Mapping[str, tuple[str, ...]]
    resource_ids_by_activity: Mapping[str, tuple[str, ...]]
    template_by_component_type_id: Mapping[str, str]
    template_id_to_node_id: Mapping[str, str]
    module_activity_ids: tuple[str, ...]
    stage_node_by_logical_id: Mapping[str, str] = field(default_factory=dict)
    activity_node_by_logical_id: Mapping[str, str] = field(default_factory=dict)
    resource_node_by_logical_id: Mapping[str, str] = field(default_factory=dict)
    activity_logical_ids_by_stage: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    resource_logical_id_by_activity: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "module_activity_ids", tuple(self.module_activity_ids))

    def resolve_stage_resource(
        self, stage_logical_id: str, resource_logical_id: str
    ) -> tuple[str, str]:
        """Return (activity_node_id, resource_node_id) for a MiC stage/resource pair."""
        resource_node = self.resource_node_by_logical_id.get(resource_logical_id)
        if resource_node is None:
            raise ReleaseBuildError(
                "process_link_unresolved",
                f"unknown manufacturing resource {resource_logical_id!r}",
            )
        activity_logicals = self.activity_logical_ids_by_stage.get(stage_logical_id, ())
        if not activity_logicals:
            raise ReleaseBuildError(
                "process_link_unresolved",
                f"unknown production stage {stage_logical_id!r}",
            )
        preferred = [
            activity_id
            for activity_id in activity_logicals
            if self.resource_logical_id_by_activity.get(activity_id) == resource_logical_id
        ]
        chosen = preferred[0] if preferred else activity_logicals[0]
        activity_node = self.activity_node_by_logical_id[chosen]
        return activity_node, resource_node


def _strict_keys(payload: Mapping[str, Any], allowed: frozenset[str], context: str) -> None:
    unknown = sorted(set(payload) - allowed)
    missing = sorted(allowed - set(payload))
    if unknown:
        raise CaseConfigError(f"unknown {context} key(s): {unknown}")
    if missing:
        raise CaseConfigError(f"missing {context} key(s): {missing}")


def _nonempty(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CaseConfigError(f"{field} must be non-empty")
    return text


def _uppercase_hash(value: object, field: str) -> str:
    text = _nonempty(value, field)
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", text):
        raise CaseConfigError(f"{field} must be a SHA-256 hex digest")
    if text != text.upper():
        raise CaseConfigError(f"{field} must be uppercase")
    return text


def _resolve_selected_path(raw: object, config_path: Path, field: str) -> tuple[str, Path]:
    selected = _nonempty(raw, field)
    path = Path(selected)
    resolved = path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()
    return selected, resolved


def _parse_binding(
    payload: object, config_path: Path, field: str
) -> InputBinding:
    if not isinstance(payload, Mapping):
        raise CaseConfigError(f"{field} must be an object")
    _strict_keys(payload, _BINDING_KEYS, field)
    selected, resolved = _resolve_selected_path(payload["path"], config_path, f"{field}.path")
    size = payload["sizeBytes"]
    if type(size) is not int or size < 0:
        raise CaseConfigError(f"{field}.sizeBytes must be a non-negative integer")
    return InputBinding(
        selected_path=selected,
        resolved_path=resolved,
        sha256=_uppercase_hash(payload["sha256"], f"{field}.sha256"),
        size_bytes=size,
    )


def load_case_config(path: Path | str) -> CaseConfig:
    config_path = Path(path).resolve()
    try:
        payload = _strict_json_loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CaseConfigError(f"cannot read case config {config_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise CaseConfigError("case config must be a JSON object")
    unknown = sorted(set(payload) - _CONFIG_KEYS - _OPTIONAL_CONFIG_KEYS)
    missing = sorted(_CONFIG_KEYS - set(payload))
    if unknown:
        raise CaseConfigError(f"unknown case config key(s): {unknown}")
    if missing:
        raise CaseConfigError(f"missing case config key(s): {missing}")
    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise CaseConfigError(f"schemaVersion must equal {SCHEMA_VERSION!r}")
    profile = _nonempty(payload["releaseProfile"], "releaseProfile")
    if profile not in {"actual-case", "controlled-fixture"}:
        raise CaseConfigError("releaseProfile must be 'actual-case' or 'controlled-fixture'")
    if type(payload["releaseReady"]) is not bool:
        raise CaseConfigError("releaseReady must be boolean")
    if type(payload["includeOpenings"]) is not bool:
        raise CaseConfigError("includeOpenings must be boolean")
    module_payload = payload["module"]
    if not isinstance(module_payload, Mapping):
        raise CaseConfigError("module must be an object")
    _strict_keys(module_payload, _MODULE_KEYS, "module")
    module = ModuleDeclaration(
        source_identity=_nonempty(module_payload["sourceIdentity"], "module.sourceIdentity"),
        name=_nonempty(module_payload["name"], "module.name"),
        identity_source=_nonempty(module_payload["identitySource"], "module.identitySource"),
    )
    material_payload = payload["materialEvidence"]
    material = (
        None
        if material_payload is None
        else _parse_binding(material_payload, config_path, "materialEvidence")
    )
    factory_payload = payload["factoryInput"]
    factory = (
        None
        if factory_payload is None
        else _parse_binding(factory_payload, config_path, "factoryInput")
    )
    factory_target_payload = payload.get("factoryTargetMap")
    factory_target_map = (
        None
        if factory_target_payload is None
        else _parse_binding(factory_target_payload, config_path, "factoryTargetMap")
    )
    config = CaseConfig(
        schema_version=SCHEMA_VERSION,
        release_profile=profile,
        release_ready=payload["releaseReady"],
        ifc=_parse_binding(payload["ifc"], config_path, "ifc"),
        factor_workbook=_parse_binding(
            payload["factorWorkbook"], config_path, "factorWorkbook"
        ),
        ontology=_parse_binding(payload["ontology"], config_path, "ontology"),
        material_evidence=material,
        module=module,
        requested_scope=_nonempty(payload["requestedScope"], "requestedScope"),
        include_openings=payload["includeOpenings"],
        factory_input=factory,
        factory_target_map=factory_target_map,
        generated_at_utc=_nonempty(payload["generatedAtUtc"], "generatedAtUtc"),
        config_path=config_path,
    )
    if profile == "actual-case":
        if config.release_ready or config.material_evidence is not None:
            raise CaseConfigError(
                "actual-case must remain releaseReady=false with materialEvidence=null"
            )
    elif not config.release_ready or config.material_evidence is None:
        raise CaseConfigError(
            "controlled-fixture requires releaseReady=true and materialEvidence"
        )
    return config


def _sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest().upper()


def _file_descriptor(path: Path, *, relative_path: str | None = None) -> dict[str, Any]:
    payload = path.read_bytes()
    descriptor: dict[str, Any] = {
        "sha256": _sha256_bytes(payload),
        "sizeBytes": len(payload),
    }
    if relative_path is not None:
        descriptor["path"] = relative_path
    return descriptor


def _input_descriptor(binding: InputBinding) -> dict[str, Any]:
    return {
        "selectedPath": binding.selected_path,
        "resolvedPath": str(binding.resolved_path),
        "sha256": binding.sha256,
        "sizeBytes": binding.size_bytes,
    }


def _verify_binding(binding: InputBinding, name: str) -> None:
    path = binding.resolved_path
    if not path.is_file():
        raise ReleaseBuildError("input_missing", f"{name} input does not exist: {path}")
    actual = _file_descriptor(path)
    if actual["sha256"] != binding.sha256 or actual["sizeBytes"] != binding.size_bytes:
        raise ReleaseBuildError(
            "input_hash_mismatch",
            f"{name} input hash/size does not match its case configuration",
        )


def _issue(
    record_id: str,
    reason_code: str,
    message: str,
    evidence: Mapping[str, Any],
    *,
    evidence_source_id: str,
    source_record_id: str,
) -> ValidationIssue:
    source_identity = (
        stable_id("SourceRecord", evidence_source_id, source_record_id)
        if evidence_source_id and source_record_id
        else ""
    )
    return ValidationIssue(
        record_id=record_id,
        reason_code=reason_code,
        message=message,
        evidence={
            "kind": "material",
            **dict(evidence),
            "sourceIdentity": source_identity,
            "sourceRecordId": source_record_id,
            "evidenceSourceId": evidence_source_id,
        },
    )


def _load_evidence_payload(binding: InputBinding) -> Mapping[str, Any]:
    try:
        text = binding.resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseBuildError(
            "material_evidence_unreadable", f"cannot read material evidence: {exc}"
        ) from exc
    try:
        payload = _strict_json_loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(
            "material_evidence_schema_invalid", f"invalid material evidence JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ReleaseBuildError("material_evidence_schema_invalid", "evidence must be an object")
    try:
        _strict_keys(payload, _EVIDENCE_KEYS, "material evidence")
    except CaseConfigError as exc:
        raise ReleaseBuildError("material_evidence_schema_invalid", str(exc)) from exc
    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise ReleaseBuildError(
            "material_evidence_schema_invalid", "evidence schemaVersion is not canonical v2"
        )
    if not isinstance(payload["records"], list) or not payload["records"]:
        raise ReleaseBuildError(
            "material_evidence_empty", "material evidence requires at least one record"
        )
    return payload


def _evidence_nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ReleaseBuildError(
            "material_evidence_schema_invalid",
            f"{field} must be a native non-empty string",
        )
    return value.strip()


def _evidence_finite_number(value: Any, field: str) -> int | float:
    if type(value) not in (int, float) or (
        type(value) is float and not math.isfinite(value)
    ):
        raise ReleaseBuildError(
            "material_evidence_schema_invalid",
            f"{field} must be a native finite JSON number",
        )
    return value


def assemble_material_evidence(
    config: CaseConfig,
    extraction: IFCExtractionResult,
    factor_library: FactorLibrary,
) -> EvidenceAssembly:
    if config.material_evidence is None:
        raise ReleaseBuildError(
            "material_evidence_required", "materialEvidence is required for release"
        )
    payload = _load_evidence_payload(config.material_evidence)
    association_by_id = {row.id: row for row in extraction.material_associations}
    quantity_by_id = {row.id: row for row in extraction.design_quantities}
    factor_rows = {
        str(row.get("sourceRowId") or ""): row
        for row in factor_library.material_factors
        if str(row.get("sourceRowId") or "")
    }
    evidence_source_id = f"sha256:{config.material_evidence.sha256}"
    candidates: list[ConsumptionCandidate] = []
    rejected: list[ValidationIssue] = []
    ledger: list[str] = []
    seen_records: set[str] = set()
    for raw in payload["records"]:
        if not isinstance(raw, Mapping):
            raise ReleaseBuildError(
                "material_evidence_schema_invalid", "every evidence record must be an object"
            )
        try:
            _strict_keys(raw, _EVIDENCE_RECORD_KEYS, "material evidence record")
        except CaseConfigError as exc:
            raise ReleaseBuildError("material_evidence_schema_invalid", str(exc)) from exc
        record_id = _evidence_nonempty_string(raw["recordId"], "recordId")
        association_id = _evidence_nonempty_string(
            raw["associationId"], "associationId"
        )
        component_id = _evidence_nonempty_string(raw["componentId"], "componentId")
        material_id = _evidence_nonempty_string(raw["materialId"], "materialId")
        factor_source_id = _evidence_nonempty_string(
            raw["factorSourceRowId"], "factorSourceRowId"
        )
        formula_code = _evidence_nonempty_string(raw["formulaCode"], "formulaCode")
        if type(raw["measured"]) is not bool:
            raise ReleaseBuildError(
                "material_evidence_schema_invalid", "measured must be boolean"
            )
        if not isinstance(raw["operands"], list) or not raw["operands"]:
            raise ReleaseBuildError(
                "material_evidence_schema_invalid", "operands must be a non-empty list"
            )
        validated_operands: list[tuple[str, str, int | float, str, str, str | None]] = []
        for operand_index, operand_raw in enumerate(raw["operands"]):
            if not isinstance(operand_raw, Mapping):
                raise ReleaseBuildError(
                    "material_evidence_schema_invalid", "each operand must be an object"
                )
            try:
                _strict_keys(operand_raw, _OPERAND_KEYS, "material operand")
            except CaseConfigError as exc:
                raise ReleaseBuildError("material_evidence_schema_invalid", str(exc)) from exc
            field_prefix = f"operands[{operand_index}]"
            operand_id = _evidence_nonempty_string(
                operand_raw["operandId"], f"{field_prefix}.operandId"
            )
            role = _evidence_nonempty_string(
                operand_raw["role"], f"{field_prefix}.role"
            )
            value = _evidence_finite_number(
                operand_raw["value"], f"{field_prefix}.value"
            )
            unit = _evidence_nonempty_string(
                operand_raw["unit"], f"{field_prefix}.unit"
            )
            source_id = _evidence_nonempty_string(
                operand_raw["sourceId"], f"{field_prefix}.sourceId"
            )
            raw_design_id = operand_raw["designQuantityId"]
            design_id = (
                None
                if raw_design_id is None
                else _evidence_nonempty_string(
                    raw_design_id, f"{field_prefix}.designQuantityId"
                )
            )
            validated_operands.append(
                (operand_id, role, value, unit, source_id, design_id)
            )
        if record_id in seen_records:
            raise ReleaseBuildError(
                "material_evidence_schema_invalid", f"duplicate evidence recordId {record_id!r}"
            )
        seen_records.add(record_id)
        ledger.append(record_id)
        association = association_by_id.get(association_id)
        if (
            association is None
            or component_id != association.component_id
            or material_id != association.material_id
        ):
            rejected.append(
                _issue(
                    record_id,
                    "evidence_ownership_mismatch",
                    "Evidence does not resolve to the exact IFC material association ownership.",
                        {
                            "associationId": association_id,
                            "componentId": component_id,
                            "materialId": material_id,
                        },
                    evidence_source_id=evidence_source_id,
                    source_record_id=association_id,
                )
            )
            continue
        factor_row = factor_rows.get(factor_source_id)
        if factor_row is None:
            rejected.append(
                _issue(
                    record_id,
                    "factor_source_not_found",
                    "The exact material factor source row was not found.",
                    {"associationId": association_id, "factorSourceRowId": factor_source_id},
                    evidence_source_id=evidence_source_id,
                    source_record_id=association_id,
                )
            )
            continue
        try:
            factor = factor_library._strict_record(factor_row, "material")
        except Exception as exc:
            rejected.append(
                _issue(
                    record_id,
                    "factor_source_invalid",
                    "The exact material factor source row is not a strict v2 factor.",
                    {"associationId": association_id, "error": str(exc)},
                    evidence_source_id=evidence_source_id,
                    source_record_id=association_id,
                )
            )
            continue
        operands: list[QuantityOperand] = []
        operand_problem: ValidationIssue | None = None
        for operand_id, role, value, unit, source_id, design_id in validated_operands:
            if design_id is not None:
                quantity = quantity_by_id.get(design_id)
                allowed_source_ids = (
                    {design_id}
                    if quantity is None
                    else {design_id, f"ifc:{quantity.quantity_name}"}
                )
                if (
                    quantity is None
                    or quantity.component_id != association.component_id
                    or source_id not in allowed_source_ids
                    or value != quantity.source_value
                    or unit != quantity.source_unit
                ):
                    operand_problem = _issue(
                        record_id,
                        "design_quantity_ownership_mismatch",
                        "Operand does not resolve to the exact owned IFC DesignQuantity.",
                        {
                            "associationId": association_id,
                            "designQuantityId": design_id,
                        },
                        evidence_source_id=evidence_source_id,
                        source_record_id=association_id,
                    )
                    break
                value = quantity.source_value
                unit = quantity.source_unit
            operands.append(
                QuantityOperand(
                    operand_id=operand_id,
                    role=role,
                    value=value,
                    unit=unit,
                    source_id=source_id,
                    design_quantity_id=design_id,
                )
            )
        if operand_problem is not None:
            rejected.append(operand_problem)
            continue
        candidates.append(
            ConsumptionCandidate(
                record_id=record_id,
                kind="material",
                source_record_id=association.id,
                evidence_source_id=evidence_source_id,
                product_target_id=association.component_id,
                material_id=association.material_id,
                recorded_scope=config.requested_scope,
                requested_scope=config.requested_scope,
                factor=factor,
                factor_resolution_reason="",
                formula_code=formula_code,
                operands=tuple(operands),
                measured=raw["measured"],
            )
        )
    return EvidenceAssembly(
        candidates=tuple(sorted(candidates, key=lambda row: row.record_id)),
        rejected=tuple(sorted(rejected, key=lambda row: (row.record_id, row.reason_code))),
        source_ledger_identities=tuple(sorted(ledger)),
    )


def _factory_issue(
    record_id: str,
    reason_code: str,
    message: str,
    evidence: Mapping[str, Any],
    *,
    evidence_source_id: str,
    source_record_id: str,
) -> ValidationIssue:
    source_identity = (
        stable_id("SourceRecord", evidence_source_id, source_record_id)
        if evidence_source_id and source_record_id
        else ""
    )
    return ValidationIssue(
        record_id=record_id,
        reason_code=reason_code,
        message=message,
        evidence={
            "kind": "energy",
            **dict(evidence),
            "sourceIdentity": source_identity,
            "sourceRecordId": source_record_id,
            "evidenceSourceId": evidence_source_id,
        },
    )


def _load_factory_payload(binding: InputBinding) -> Mapping[str, Any]:
    try:
        text = binding.resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseBuildError(
            "factory_input_unreadable", f"cannot read factory input: {exc}"
        ) from exc
    try:
        payload = _strict_json_loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(
            "factory_input_schema_invalid", f"invalid factory input JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ReleaseBuildError("factory_input_schema_invalid", "factory input must be an object")
    return payload


def _load_factory_target_map(
    binding: InputBinding | None,
    *,
    expected_ifc_sha256: str,
) -> dict[str, str]:
    if binding is None:
        return {}
    try:
        text = binding.resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseBuildError(
            "factory_target_map_unreadable",
            f"cannot read factory target map: {exc}",
        ) from exc
    try:
        payload = _strict_json_loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(
            "factory_target_map_schema_invalid",
            f"invalid factory target map JSON: {exc}",
        ) from exc
    if not isinstance(payload, Mapping):
        raise ReleaseBuildError(
            "factory_target_map_schema_invalid", "factory target map must be an object"
        )
    target_sha = str(payload.get("target_ifc_sha256", "") or "").strip().upper()
    if target_sha and target_sha != expected_ifc_sha256:
        raise ReleaseBuildError(
            "factory_target_map_ifc_mismatch",
            "factory target map target_ifc_sha256 does not match the configured IFC",
        )
    raw_map = payload.get("component_global_id_map")
    if not isinstance(raw_map, Mapping):
        raise ReleaseBuildError(
            "factory_target_map_schema_invalid",
            "factory target map requires component_global_id_map",
        )
    result: dict[str, str] = {}
    for source, target in raw_map.items():
        source_text = str(source or "").strip()
        target_text = str(target or "").strip()
        if source_text and target_text:
            result[source_text] = target_text
    return dict(sorted(result.items()))


def _factory_rows(payload: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    rows = payload.get(key, ())
    if rows in (None, ""):
        return ()
    if not isinstance(rows, list):
        raise ReleaseBuildError(
            "factory_input_schema_invalid", f"{key} must be a JSON array"
        )
    result: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ReleaseBuildError(
                "factory_input_schema_invalid", f"{key}[{index}] must be an object"
            )
        result.append(row)
    return tuple(result)


def _factory_text(row: Mapping[str, Any], key: str, default: str = "") -> str:
    value = row.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _factory_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _factory_norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def _factory_carrier_key(carrier: str, unit: str) -> str:
    carrier_key = _factory_norm(carrier)
    unit_key = _factory_norm(unit)
    if "electric" in carrier_key or unit_key == "kwh":
        return "electricity"
    if "diesel" in carrier_key or ("oil" in carrier_key and unit_key == "l"):
        return "diesel"
    return carrier_key or "unknown"


def _row_unit_key(row: Mapping[str, Any]) -> str:
    return _factory_norm(row.get("unit", ""))


def _factor_text(row: Mapping[str, Any]) -> str:
    return " ".join(
        str(row.get(key, "") or "")
        for key in ("category", "energyType", "keyword", "sourceNote", "geography")
    ).casefold()


def _energy_factor_score(row: Mapping[str, Any], carrier_key: str) -> int:
    text = _factor_text(row)
    unit_key = _row_unit_key(row)
    if carrier_key == "electricity":
        if "electric" not in text or unit_key != "kgco2ekwh":
            return -1
        score = 10
        if "guangdong lifecycle co2e proxy" in text:
            score += 200
        if "guangdong factory proxy" in text:
            score += 100
        if "factory proxy" in text:
            score += 80
        if "guangdong" in text:
            score += 50
        if "mainland" in text or "supplier" in text:
            score += 20
        if "national average" in text:
            score += 10
        if "2023" in text:
            score += 5
        return score
    if carrier_key == "diesel":
        if "diesel" not in text or unit_key != "kgco2el":
            return -1
        score = 10
        if "lifecycle" in text or "wtt" in text or "upstream" in text:
            score += 100
        if "stationary" in text:
            score += 20
        if "2023" in text:
            score += 5
        return score
    return -1


def _select_factory_energy_factor(
    factor_library: FactorLibrary,
    *,
    carrier: str,
    quantity_unit: str,
    requested_scope: str,
) -> tuple[Any | None, str]:
    carrier_key = _factory_carrier_key(carrier, quantity_unit)
    scored = [
        (score, row)
        for row in factor_library.energy_factors
        for score in (_energy_factor_score(row, carrier_key),)
        if score >= 0
    ]
    if not scored:
        return None, f"unresolved_{carrier_key}_factor"
    scored.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("sourceRowId", "")),
        )
    )
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, f"ambiguous_{carrier_key}_factor"
    row = dict(scored[0][1])
    try:
        factor = factor_library._strict_record(row, "energy")
    except Exception as exc:
        return None, f"invalid_{carrier_key}_factor:{exc}"

    source_boundary = str(getattr(factor, "system_boundary", "") or "").strip()
    reason = (
        "accepted"
        if source_boundary == requested_scope
        else "accepted_factory_scope_policy"
    )
    return factor, reason


def _source_metadata_by_id(
    rows: Iterable[Mapping[str, Any]]
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        source_id = _factory_text(row, "source_id")
        if source_id and source_id not in result:
            result[source_id] = row
    return result


def _allocation_rows_by_record(
    rows: Iterable[Mapping[str, Any]],
    *,
    allocation_basis: str,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    wanted = _factory_norm(allocation_basis)
    for row in rows:
        basis = _factory_text(row, "allocation_basis") or _factory_text(
            row, "allocation_key"
        )
        if _factory_norm(basis) != wanted:
            continue
        record_id = _factory_text(row, "record_id")
        if record_id:
            grouped.setdefault(record_id, []).append(row)
    return {
        record_id: tuple(
            sorted(
                items,
                key=lambda row: (
                    _factory_text(row, "target_level"),
                    _factory_text(row, "target_id"),
                    _factory_text(row, "batch_id"),
                    _factory_text(row, "module_id"),
                ),
            )
        )
        for record_id, items in grouped.items()
    }


def _default_process_definition_path() -> Path:
    return Path(__file__).resolve().parent / "process_definition_simulated_v5.xlsx"


def _worksheet_records(workbook, sheet_name: str) -> tuple[dict[str, Any], ...]:
    if sheet_name not in workbook.sheetnames:
        raise ReleaseBuildError(
            "process_definition_schema_invalid",
            f"process definition workbook has no sheet {sheet_name!r}",
        )
    rows = list(workbook[sheet_name].iter_rows(values_only=True))
    if not rows:
        return ()
    headers = [str(value or "").strip() for value in rows[0]]
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows[1:]):
        record = {
            headers[column]: row[column] if column < len(row) else None
            for column in range(len(headers))
            if headers[column]
        }
        record["_rowIndex"] = index
        records.append(record)
    return tuple(records)


def _is_scope_yes(value: object) -> bool:
    return str(value or "").strip().casefold() in {"yes", "y", "true", "1"}


def _process_text(row: Mapping[str, Any], key: str, default: str = "") -> str:
    value = row.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _boundary_excluded_process_row(row: Mapping[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in row.values())
    return any(term in text for term in ("镀锌", "存放发运", "场内转运", "叉车"))


def _process_relationship_id(
    evidence_source_id: str, src: str, rel_type: str, tgt: str, *extra: object
) -> str:
    return stable_id(
        "ProcessDefinitionRelationship",
        evidence_source_id,
        src,
        rel_type,
        tgt,
        *extra,
    )


def _component_type_matches(rule: Mapping[str, Any], component_type: Any) -> bool:
    match_class = _process_text(rule, "match_ifc_class")
    if match_class and "/" not in match_class and component_type.ifc_class != match_class:
        return False
    text = " ".join(
        str(value or "")
        for value in (
            component_type.ifc_class,
            component_type.name,
            component_type.description,
            component_type.tag,
        )
    ).casefold()
    contains = _process_text(rule, "match_name_contains").casefold()
    if contains and contains not in text:
        return False
    type_mark = _process_text(rule, "type_mark").casefold()
    if type_mark and type_mark != "-" and type_mark not in text:
        return False
    return True


def _component_basis_weights(
    extraction: IFCExtractionResult,
    allocation_basis: str,
    *,
    allowed_ids: Optional[AbstractSet[str]] = None,
) -> tuple[tuple[str, float], ...]:
    component_ids = sorted(
        row.id
        for row in extraction.components
        if allowed_ids is None or row.id in allowed_ids
    )
    basis_key = _factory_norm(allocation_basis)
    if basis_key in {"producedqty", "producedquantity", "quantity", "count"}:
        return tuple((component_id, 1.0) for component_id in component_ids)
    allowed_component_ids = set(component_ids)
    preferred_units = ("kg", "m3", "m2", "m")
    quantities_by_unit: dict[str, dict[str, float]] = {
        unit: {} for unit in preferred_units
    }
    for quantity in extraction.design_quantities:
        if allowed_ids is not None and quantity.component_id not in allowed_component_ids:
            continue
        unit = str(quantity.normalized_unit or "").strip()
        if unit not in quantities_by_unit:
            continue
        value = float(quantity.normalized_value)
        if math.isfinite(value) and value > 0:
            quantities_by_unit[unit][quantity.component_id] = (
                quantities_by_unit[unit].get(quantity.component_id, 0.0) + value
            )
    for unit in preferred_units:
        weights = quantities_by_unit[unit]
        if weights:
            return tuple(sorted(weights.items()))
    return ()


def _module_allocation_targets(
    extraction: IFCExtractionResult,
    *,
    evidence_source_id: str,
    record_id: str,
    allocation_basis: str,
    raw_weight: float,
    raw_weight_unit: str,
    module_share: float,
    allowed_ids: Optional[AbstractSet[str]] = None,
) -> tuple[EnergyAllocationTarget, ...]:
    basis_weights = _component_basis_weights(
        extraction, allocation_basis, allowed_ids=allowed_ids
    )
    total = math.fsum(weight for _, weight in basis_weights)
    if not basis_weights or total <= 0:
        return ()
    targets: list[EnergyAllocationTarget] = []
    for index, (component_id, basis_weight) in enumerate(basis_weights):
        fraction = module_share * (basis_weight / total)
        targets.append(
            EnergyAllocationTarget(
                target_component_id=component_id,
                raw_weight=raw_weight * fraction,
                raw_weight_unit=raw_weight_unit,
                normalized_weight=fraction,
                evidence_record_id=stable_id(
                    "FactoryAllocationEvidence",
                    evidence_source_id,
                    record_id,
                    allocation_basis,
                    "module_to_component",
                    index,
                    component_id,
                ),
            )
        )
    return tuple(targets)


def _factory_record_is_synthetic(
    *,
    record_id: str,
    source_id: str,
    source_meta: Mapping[str, Any],
) -> bool:
    if record_id.startswith("SYN_") or source_id.startswith("source:synthetic_"):
        return True
    metadata_text = " ".join(str(value or "") for value in source_meta.values())
    return "synthetic" in metadata_text.casefold()


def _load_process_definition_context(
    config: CaseConfig,
    extraction: IFCExtractionResult,
    *,
    module_id: str,
) -> ProcessDefinitionContext | None:
    if config.factory_input is None:
        return None
    path = _default_process_definition_path()
    if not path.is_file():
        return None
    try:
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise ReleaseBuildError(
            "process_definition_unreadable",
            f"cannot read process definition workbook: {exc}",
        ) from exc
    descriptor = _file_descriptor(path)
    evidence_source_id = f"sha256:{descriptor['sha256']}"
    nodes: dict[str, FactoryContextNode] = {}
    edges: dict[str, FactoryContextEdge] = {}

    template_rows = list(_worksheet_records(workbook, "2_process_template"))
    stage_rows = list(_worksheet_records(workbook, "3_production_stage"))
    resource_rows = list(_worksheet_records(workbook, "4_manufacturing_resource"))
    activity_rows = list(_worksheet_records(workbook, "5_manufacturing_activity"))
    batch_rows = list(_worksheet_records(workbook, "6_production_batch"))
    component_rules = [
        row
        for row in _worksheet_records(workbook, "1_component_type")
        if _is_scope_yes(row.get("in_scope"))
        and not _boundary_excluded_process_row(row)
    ]

    template_node_by_id: dict[str, str] = {}
    for row in sorted(template_rows, key=lambda item: _process_text(item, "template_id")):
        template_id = _process_text(row, "template_id")
        if not template_id:
            continue
        node_id = stable_id("ManufacturingProcessTemplate", evidence_source_id, template_id)
        template_node_by_id[template_id] = node_id
        _factory_context_node(
            nodes,
            node_id,
            ("ManufacturingProcessTemplate",),
            {
                "templateId": template_id,
                "templateName": _process_text(row, "template_name"),
                "scopeLevel": _process_text(row, "scope_level"),
                "anchor": _process_text(row, "anchor"),
                "note": _process_text(row, "note"),
                "evidenceSourceId": evidence_source_id,
            },
        )

    stage_node_by_id: dict[str, str] = {}
    stage_sort: dict[str, tuple[str, float, int]] = {}
    for row in sorted(stage_rows, key=lambda item: (_process_text(item, "template_id"), _factory_number(item.get("seq")) or 0, int(item.get("_rowIndex", 0)))):
        stage_id = _process_text(row, "stage_id")
        template_id = _process_text(row, "template_id")
        template_node_id = template_node_by_id.get(template_id)
        if not stage_id or template_node_id is None:
            continue
        node_id = stable_id("ProductionStage", evidence_source_id, stage_id)
        stage_node_by_id[stage_id] = node_id
        sequence = _factory_number(row.get("seq")) or 0.0
        stage_sort[stage_id] = (template_id, sequence, int(row.get("_rowIndex", 0)))
        _factory_context_node(
            nodes,
            node_id,
            ("ProductionStage",),
            {
                "stageId": stage_id,
                "templateId": template_id,
                "stageName": _process_text(row, "stage_en") or _process_text(row, "stage_cn"),
                "level": _process_text(row, "level"),
                "sequence": sequence,
                "evidenceSourceId": evidence_source_id,
            },
        )
        _factory_context_edge(
            edges,
            template_node_id,
            "hasStage",
            node_id,
            {"stageId": stage_id, "evidenceSourceId": evidence_source_id},
            occurrence_id=_process_relationship_id(
                evidence_source_id, template_node_id, "hasStage", node_id
            ),
        )

    resource_node_by_id: dict[str, str] = {}
    for row in sorted(resource_rows, key=lambda item: _process_text(item, "resource_id")):
        resource_id = _process_text(row, "resource_id")
        if not resource_id:
            continue
        resource_kind = _process_text(row, "resource_kind").casefold()
        if resource_kind not in {"machine", "tool", "labor"}:
            raise ReleaseBuildError(
                "process_definition_schema_invalid",
                f"unsupported resource_kind {resource_kind!r}",
            )
        node_id = stable_id("ManufacturingResource", evidence_source_id, resource_id)
        resource_node_by_id[resource_id] = node_id
        _factory_context_node(
            nodes,
            node_id,
            ("ManufacturingResource",),
            {
                "resourceId": resource_id,
                "resourceKind": resource_kind,
                "name": _process_text(row, "name"),
                "model": _process_text(row, "model"),
                "powerKw": _factory_number(row.get("power_kw")),
                "line": _process_text(row, "line"),
                "note": _process_text(row, "note"),
                "evidenceSourceId": evidence_source_id,
            },
        )

    activity_node_by_id: dict[str, str] = {}
    activity_ids_by_template: dict[str, list[str]] = {}
    resource_ids_by_activity: dict[str, list[str]] = {}
    activity_logical_ids_by_stage: dict[str, list[str]] = {}
    resource_logical_id_by_activity: dict[str, str] = {}
    activity_order: list[tuple[str, float, int, str]] = []
    for row in sorted(
        activity_rows,
        key=lambda item: (
            _process_text(item, "template_id"),
            stage_sort.get(_process_text(item, "stage_id"), ("", 0.0, 0))[1],
            int(item.get("_rowIndex", 0)),
        ),
    ):
        activity_id = _process_text(row, "activity_id")
        stage_id = _process_text(row, "stage_id")
        template_id = _process_text(row, "template_id")
        stage_node_id = stage_node_by_id.get(stage_id)
        if not activity_id or stage_node_id is None:
            continue
        node_id = stable_id("ManufacturingActivity", evidence_source_id, activity_id)
        activity_node_by_id[activity_id] = node_id
        activity_ids_by_template.setdefault(template_id, []).append(node_id)
        activity_logical_ids_by_stage.setdefault(stage_id, []).append(activity_id)
        activity_order.append(
            (
                template_id,
                stage_sort.get(stage_id, ("", 0.0, 0))[1],
                int(row.get("_rowIndex", 0)),
                node_id,
            )
        )
        _factory_context_node(
            nodes,
            node_id,
            ("ManufacturingActivity",),
            {
                "activityId": activity_id,
                "stageId": stage_id,
                "templateId": template_id,
                "activityName": _process_text(row, "activity_en") or _process_text(row, "activity_cn"),
                "level": _process_text(row, "level"),
                "workdays": _factory_number(row.get("workdays")),
                "energyCarrier": _process_text(row, "energy_carrier"),
                "estimatedEnergyValue": _factory_number(row.get("est_energy_value")),
                "estimatedEnergyUnit": _process_text(row, "est_energy_unit"),
                "batchId": _process_text(row, "batch_id"),
                "evidenceSourceId": evidence_source_id,
            },
        )
        _factory_context_edge(
            edges,
            stage_node_id,
            "hasActivity",
            node_id,
            {"activityId": activity_id, "evidenceSourceId": evidence_source_id},
            occurrence_id=_process_relationship_id(
                evidence_source_id, stage_node_id, "hasActivity", node_id
            ),
        )
        resource_id = _process_text(row, "resource_id")
        resource_node_id = resource_node_by_id.get(resource_id)
        if resource_node_id is not None:
            resource_ids_by_activity.setdefault(node_id, []).append(resource_node_id)
            resource_logical_id_by_activity[activity_id] = resource_id
            _factory_context_edge(
                edges,
                node_id,
                "usesResource",
                resource_node_id,
                {"activityId": activity_id, "evidenceSourceId": evidence_source_id},
                occurrence_id=_process_relationship_id(
                    evidence_source_id, node_id, "usesResource", resource_node_id
                ),
            )

    for template_id, group in {
        template_id: sorted(
            (item for item in activity_order if item[0] == template_id),
            key=lambda item: (item[1], item[2], item[3]),
        )
        for template_id in {item[0] for item in activity_order}
    }.items():
        for before, after in zip(group, group[1:]):
            _factory_context_edge(
                edges,
                before[3],
                "directlyPrecedes",
                after[3],
                {"templateId": template_id, "evidenceSourceId": evidence_source_id},
                occurrence_id=_process_relationship_id(
                    evidence_source_id,
                    before[3],
                    "directlyPrecedes",
                    after[3],
                ),
            )

    component_types = {
        component.component_type.id: component.component_type
        for component in extraction.components
        if component.component_type is not None
    }
    template_by_component_type_id: dict[str, str] = {}
    for rule in component_rules:
        template_id = _process_text(rule, "template_id")
        template_node_id = template_node_by_id.get(template_id)
        if template_node_id is None or template_id == "TPL_MODULE":
            continue
        for component_type_id, component_type in sorted(component_types.items()):
            if not _component_type_matches(rule, component_type):
                continue
            template_by_component_type_id[component_type_id] = template_id
            _factory_context_edge(
                edges,
                component_type_id,
                "hasProcessTemplate",
                template_node_id,
                {
                    "componentTypeKey": _process_text(rule, "component_type_key"),
                    "templateId": template_id,
                    "evidenceSourceId": evidence_source_id,
                },
                occurrence_id=_process_relationship_id(
                    evidence_source_id,
                    component_type_id,
                    "hasProcessTemplate",
                    template_node_id,
                ),
            )

    module_template_node = template_node_by_id.get("TPL_MODULE")
    module_activity_ids = tuple(activity_ids_by_template.get("TPL_MODULE", ()))
    if module_template_node is not None:
        _factory_context_edge(
            edges,
            module_id,
            "hasProcessTemplate",
            module_template_node,
            {"templateId": "TPL_MODULE", "evidenceSourceId": evidence_source_id},
            occurrence_id=_process_relationship_id(
                evidence_source_id, module_id, "hasProcessTemplate", module_template_node
            ),
        )
        for activity_id in module_activity_ids:
            _factory_context_edge(
                edges,
                module_id,
                "manufacturedBy",
                activity_id,
                {"templateId": "TPL_MODULE", "evidenceSourceId": evidence_source_id},
                occurrence_id=_process_relationship_id(
                    evidence_source_id, module_id, "manufacturedBy", activity_id
                ),
            )

    for component in sorted(extraction.components, key=lambda item: item.id):
        component_type = component.component_type
        if component_type is None:
            continue
        template_id = template_by_component_type_id.get(component_type.id)
        if not template_id:
            continue
        for activity_id in activity_ids_by_template.get(template_id, ()):
            _factory_context_edge(
                edges,
                component.id,
                "manufacturedBy",
                activity_id,
                {"templateId": template_id, "evidenceSourceId": evidence_source_id},
                occurrence_id=_process_relationship_id(
                    evidence_source_id, component.id, "manufacturedBy", activity_id
                ),
            )

    for row in sorted(batch_rows, key=lambda item: _process_text(item, "batch_id")):
        batch_id_text = _process_text(row, "batch_id")
        if not batch_id_text:
            continue
        batch_id = stable_id("ProductionBatch", evidence_source_id, batch_id_text)
        _factory_context_node(
            nodes,
            batch_id,
            ("ProductionBatch",),
            {
                "batchId": batch_id_text,
                "batchName": _process_text(row, "batch_name"),
                "selectorType": _process_text(row, "selector_type"),
                "selectorValue": _process_text(row, "selector_value"),
                "unitsInBatch": _factory_number(row.get("units_in_batch")),
                "note": _process_text(row, "note"),
                "evidenceSourceId": evidence_source_id,
            },
        )
        _factory_context_edge(
            edges,
            batch_id,
            "produces",
            module_id,
            {"batchId": batch_id_text, "evidenceSourceId": evidence_source_id},
            occurrence_id=_process_relationship_id(
                evidence_source_id, batch_id, "produces", module_id
            ),
        )

    return ProcessDefinitionContext(
        nodes=tuple(nodes[key] for key in sorted(nodes)),
        edges=tuple(edges[key] for key in sorted(edges)),
        activity_ids_by_template={
            key: tuple(value) for key, value in sorted(activity_ids_by_template.items())
        },
        resource_ids_by_activity={
            key: tuple(value) for key, value in sorted(resource_ids_by_activity.items())
        },
        template_by_component_type_id=dict(sorted(template_by_component_type_id.items())),
        template_id_to_node_id=dict(sorted(template_node_by_id.items())),
        module_activity_ids=module_activity_ids,
        stage_node_by_logical_id=dict(sorted(stage_node_by_id.items())),
        activity_node_by_logical_id=dict(sorted(activity_node_by_id.items())),
        resource_node_by_logical_id=dict(sorted(resource_node_by_id.items())),
        activity_logical_ids_by_stage={
            key: tuple(value) for key, value in sorted(activity_logical_ids_by_stage.items())
        },
        resource_logical_id_by_activity=dict(sorted(resource_logical_id_by_activity.items())),
    )


def _factory_context_node(
    nodes: dict[str, FactoryContextNode],
    node_id: str,
    labels: tuple[str, ...],
    props: Mapping[str, Any],
) -> None:
    existing = nodes.get(node_id)
    candidate = FactoryContextNode(node_id, labels, dict(props))
    if existing is None:
        nodes[node_id] = candidate
    elif existing.labels != candidate.labels or dict(existing.props) != dict(candidate.props):
        raise ReleaseBuildError(
            "factory_context_identity_collision",
            f"factory context node id collision: {node_id}",
        )


def _factory_context_edge(
    edges: dict[str, FactoryContextEdge],
    src: str,
    rel_type: str,
    tgt: str,
    props: Mapping[str, Any],
    *,
    occurrence_id: str,
) -> None:
    existing = edges.get(occurrence_id)
    candidate = FactoryContextEdge(src, rel_type, tgt, dict(props), occurrence_id)
    if existing is None:
        edges[occurrence_id] = candidate
    elif (
        existing.src != candidate.src
        or existing.rel_type != candidate.rel_type
        or existing.tgt != candidate.tgt
        or dict(existing.props) != dict(candidate.props)
    ):
        raise ReleaseBuildError(
            "factory_context_identity_collision",
            f"factory context edge id collision: {occurrence_id}",
        )


# Energy records dropped from the case release. MEP/mechanical works are out of
# scope for this study, so the module-level MEP finishing record and its stage
# (ST_MOD_MEP) are removed from the workflow entirely.
_ENERGY_RECORD_DROP: frozenset[str] = frozenset({"SYN_E_MODULE_004"})


# Case-specific product attribution for shared finishing meters. These records
# are metered at a shared line/workstation/room level (not per component), so
# their energy stays visible at the process level AND is distributed by mass to
# the relevant component subset (identified by IFC class / material / object
# type). This makes shared finishing energy reachable from the product
# perspective on the components each operation physically acts on.
#   - Steel frame coating -> primary structural steel frame (beams/columns)
#   - Partition & ceiling framing -> secondary light-gauge framing (studs/龙骨)
#   - Plaster & painting -> wall surface panels
_ENERGY_RECORD_ALLOCATION_OVERRIDE: dict[str, dict[str, Any]] = {
    "SYN_E_WS_04_001": {
        "module_share": 1.0,
        "ifc_classes": ("IfcBeam", "IfcColumn"),
        "material_any": ("steel", "galvan"),
        "objecttype_none": ("secondary frame", "interior stud", "interior door stud"),
    },
    "SYN_E_MODULE_002": {
        "module_share": 1.0,
        "ifc_classes": ("IfcBeam", "IfcColumn"),
        "material_any": ("steel", "galvan"),
        "objecttype_any": ("secondary frame", "interior stud", "interior door stud"),
    },
    "SYN_E_MODULE_003": {
        "module_share": 1.0,
        "ifc_classes": ("IfcWall",),
    },
}


def _subset_component_ids(
    extraction: IFCExtractionResult, spec: Mapping[str, Any]
) -> set[str]:
    """Resolve the component-id subset targeted by a shared-meter override."""
    materials_by_component: dict[str, list[str]] = {}
    for association in extraction.material_associations:
        materials_by_component.setdefault(association.component_id, []).append(
            str(association.material_name or "").casefold()
        )
    ifc_classes = set(spec.get("ifc_classes", ()))
    material_any = tuple(str(item).casefold() for item in spec.get("material_any", ()))
    objecttype_any = tuple(str(item).casefold() for item in spec.get("objecttype_any", ()))
    objecttype_none = tuple(str(item).casefold() for item in spec.get("objecttype_none", ()))
    allowed: set[str] = set()
    for component in extraction.components:
        if ifc_classes and component.ifc_class not in ifc_classes:
            continue
        object_type = str(component.object_type or "").casefold()
        if material_any:
            joined = " ".join(materials_by_component.get(component.id, ()))
            if not any(keyword in joined for keyword in material_any):
                continue
        if objecttype_any and not any(keyword in object_type for keyword in objecttype_any):
            continue
        if objecttype_none and any(keyword in object_type for keyword in objecttype_none):
            continue
        allowed.add(component.id)
    return allowed


# Explicit process-context links for the synthetic factory energy records.
# Spreads energy across MiC stages/resources so process-perspective QA is not
# collapsed onto Transfer & leveling / R_HANDTOOL. Diesel always maps to R_LOGI.
# R_NONE is intentionally left empty (passive curing / air-dry).
_ENERGY_RECORD_PROCESS_LINKS: dict[str, tuple[str, str]] = {
    "SYN_E_LINE_B_001": ("ST_STEEL_INTAKE", "R_LOGI"),
    "SYN_E_WS_03_001": ("ST_STEEL_INTAKE", "R_LOGI"),
    "SYN_E_WS_06_001": ("ST_STEEL_INTAKE", "R_LOGI"),
    "SYN_E_FACTORY_001": ("ST_STEEL_QC", "R_NDT"),
    "SYN_E_LINE_A_001": ("ST_STEEL_WELD", "R_WELD"),
    "SYN_E_WS_01_001": ("ST_STEEL_CUT", "R_CUT"),
    "SYN_E_WS_02_001": ("ST_STEEL_CUT", "R_PUNCH"),
    "SYN_E_WS_04_001": ("ST_STEEL_COAT", "R_SPRAY"),
    "SYN_E_WS_05_001": ("ST_FLOOR_CONC", "R_HANDTOOL"),
    "SYN_E_MODULE_001": ("ST_MOD_TRANSFER", "R_HANDTOOL"),
    "SYN_E_MODULE_002": ("ST_MOD_FRAMING", "R_LABOR"),
    "SYN_E_MODULE_003": ("ST_MOD_PAINT", "R_SPRAY"),
    "SYN_E_COMPONENT_001": ("ST_STEEL_WELD", "R_WELD"),
    "SYN_E_COMPONENT_002": ("ST_MOD_WET", "R_HANDTOOL"),
    "SYN_E_COMPONENT_003": ("ST_MOD_FIXTURE", "R_LABOR"),
    "SYN_E_COMPONENT_004": ("ST_MOD_FINAL", "R_QC"),
}


def _default_process_link(
    process_context: ProcessDefinitionContext,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return a deterministic (activity, resource) node pair for energy records
    that carry no explicit link in ``_ENERGY_RECORD_PROCESS_LINKS``.

    When a process definition workbook is loaded the inline metering-level nodes
    are never materialised, so the metering fallback ids do not exist in the
    graph and would be rejected by the canonical validator. Instead resolve to a
    real activity/resource pair from the loaded context (the first activity that
    owns a resource, in deterministic order)."""
    for activity_logical in sorted(process_context.resource_logical_id_by_activity):
        resource_logical = process_context.resource_logical_id_by_activity[
            activity_logical
        ]
        activity_node = process_context.activity_node_by_logical_id.get(activity_logical)
        resource_node = process_context.resource_node_by_logical_id.get(resource_logical)
        if activity_node and resource_node:
            return (activity_node,), (resource_node,)
    return (), ()


def _resolve_energy_process_link(
    process_context: ProcessDefinitionContext | None,
    *,
    record_id: str,
    carrier: str,
    fallback_process_ids: tuple[str, ...],
    fallback_resource_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if process_context is None:
        return fallback_process_ids, fallback_resource_ids
    link = _ENERGY_RECORD_PROCESS_LINKS.get(record_id)
    if link is None:
        # The link table is case data, not a schema rule: releases built from
        # other factory inputs bind to a real activity/resource in their own
        # process context rather than the inline metering ids, which only exist
        # when no process definition workbook is loaded.
        return _default_process_link(process_context)
    stage_logical_id, resource_logical_id = link
    if carrier.casefold() == "diesel" and resource_logical_id != "R_LOGI":
        raise ReleaseBuildError(
            "process_link_unresolved",
            f"diesel record {record_id!r} must link to R_LOGI, got {resource_logical_id!r}",
        )
    activity_node, resource_node = process_context.resolve_stage_resource(
        stage_logical_id, resource_logical_id
    )
    return (activity_node,), (resource_node,)


def assemble_factory_input(
    config: CaseConfig,
    extraction: IFCExtractionResult,
    factor_library: FactorLibrary,
    *,
    allocation_basis: str = "mass",
) -> FactoryEvidenceAssembly:
    if config.factory_input is None:
        return FactoryEvidenceAssembly((), (), {}, (), ())
    payload = _load_factory_payload(config.factory_input)
    energy_rows = _factory_rows(payload, "p0_energy_records")
    allocation_rows = _allocation_rows_by_record(
        _factory_rows(payload, "p0_allocation_rows"),
        allocation_basis=allocation_basis,
    )
    source_metadata = _source_metadata_by_id(
        _factory_rows(payload, "p0_source_metadata")
    )
    evidence_source_id = f"sha256:{config.factory_input.sha256}"
    module_id = stable_id("ModularUnit", extraction.ifc_sha256, config.module.source_identity)
    component_by_global_id = {row.global_id: row.id for row in extraction.components}
    component_ids = {row.id for row in extraction.components}
    target_global_id_map = _load_factory_target_map(
        config.factory_target_map,
        expected_ifc_sha256=extraction.ifc_sha256,
    )
    context_nodes: dict[str, FactoryContextNode] = {}
    context_edges: dict[str, FactoryContextEdge] = {}
    candidates: list[ConsumptionCandidate] = []
    rejected: list[ValidationIssue] = []
    plans: dict[str, EnergyPopulationPlan] = {}
    process_context = _load_process_definition_context(
        config,
        extraction,
        module_id=module_id,
    )
    if process_context is not None:
        for node in process_context.nodes:
            _factory_context_node(context_nodes, node.node_id, node.labels, node.props)
        for edge in process_context.edges:
            _factory_context_edge(
                context_edges,
                edge.src,
                edge.rel_type,
                edge.tgt,
                edge.props,
                occurrence_id=edge.occurrence_id,
            )

    for index, row in enumerate(energy_rows):
        record_id = _factory_text(row, "record_id")
        source_record_id = record_id
        if not record_id:
            rejected.append(
                _factory_issue(
                    f"factory-energy-row-{index + 1}",
                    "record_id_missing",
                    "Factory energy record has no stable record_id.",
                    {"formulaCode": "measured_energy", "rowIndex": index},
                    evidence_source_id=evidence_source_id,
                    source_record_id="",
                )
            )
            continue

        if record_id in _ENERGY_RECORD_DROP:
            continue

        source_id = _factory_text(row, "source_id")
        target_id = _factory_text(row, "target_id")
        target_level = _factory_text(row, "target_level").casefold()
        carrier = _factory_text(row, "energy_carrier")
        quantity_unit = _factory_text(row, "quantity_unit")
        quantity_value = _factory_number(row.get("quantity_value"))
        if quantity_value is None:
            rejected.append(
                _factory_issue(
                    record_id,
                    "quantity_value_invalid",
                    "Factory energy quantity_value is not a finite number.",
                    {
                        "formulaCode": "measured_energy",
                        "quantityValue": str(row.get("quantity_value", "")),
                    },
                    evidence_source_id=evidence_source_id,
                    source_record_id=source_record_id,
                )
            )
            continue

        stage_name = _factory_text(row, "stage", "Factory process") or "Factory process"
        activity_name = (
            _factory_text(row, "activity")
            or _factory_text(row, "record_role")
            or "Factory energy activity"
        )
        production_line = _factory_text(row, "production_line")
        workstation_id = _factory_text(row, "workstation_id")
        metering_level = _factory_text(row, "metering_level")
        period_start = _factory_text(row, "period_start")
        period_end = _factory_text(row, "period_end")
        record_role = _factory_text(row, "record_role")
        batch_source_id = _factory_text(row, "batch_id")
        source_meta = source_metadata.get(source_id, {})
        stage_id = stable_id("ProductionStage", evidence_source_id, stage_name)
        activity_id = stable_id(
            "ManufacturingActivity", evidence_source_id, record_id, activity_name
        )
        resource_id = stable_id(
            "ManufacturingResource",
            evidence_source_id,
            source_id,
            production_line,
            workstation_id,
            metering_level,
        )

        if process_context is None:
            _factory_context_node(
                context_nodes,
                stage_id,
                ("ProductionStage",),
                {
                    "stageName": stage_name,
                    "evidenceSourceId": evidence_source_id,
                },
            )
            _factory_context_node(
                context_nodes,
                activity_id,
                ("ManufacturingActivity",),
                {
                    "activityName": activity_name,
                    "recordId": record_id,
                    "sourceRecordId": source_record_id,
                    "evidenceSourceId": evidence_source_id,
                    "sourceTargetReferenceId": (
                        stable_id(
                            "FactoryInputTargetReference",
                            evidence_source_id,
                            target_level,
                            target_id,
                        )
                        if target_id
                        else ""
                    ),
                    "sourceTargetLevel": target_level,
                    "productionLine": production_line,
                    "workstationId": workstation_id,
                    "meteringLevel": metering_level,
                    "periodStart": period_start,
                    "periodEnd": period_end,
                    "recordRole": record_role,
                },
            )
            _factory_context_node(
                context_nodes,
                resource_id,
                ("ManufacturingResource",),
                {
                    "sourceId": source_id,
                    "sourceName": _factory_text(source_meta, "source_name") if source_meta else "",
                    "sourceType": _factory_text(source_meta, "source_type") if source_meta else "",
                    "productionLine": production_line,
                    "workstationId": workstation_id,
                    "meteringLevel": metering_level,
                    "evidenceSourceId": evidence_source_id,
                },
            )
            _factory_context_edge(
                context_edges,
                stage_id,
                "hasActivity",
                activity_id,
                {"recordId": record_id, "evidenceSourceId": evidence_source_id},
                occurrence_id=stable_id(
                    "FactoryContextRelationship",
                    evidence_source_id,
                    record_id,
                    "hasActivity",
                ),
            )
            _factory_context_edge(
                context_edges,
                activity_id,
                "usesResource",
                resource_id,
                {"recordId": record_id, "evidenceSourceId": evidence_source_id},
                occurrence_id=stable_id(
                    "FactoryContextRelationship",
                    evidence_source_id,
                    record_id,
                    "usesResource",
                ),
            )
        if batch_source_id and process_context is None:
            batch_id = stable_id("ProductionBatch", evidence_source_id, batch_source_id)
            _factory_context_node(
                context_nodes,
                batch_id,
                ("ProductionBatch",),
                {
                    "batchReferenceId": stable_id(
                        "FactoryBatchReference", evidence_source_id, batch_source_id
                    ),
                    "evidenceSourceId": evidence_source_id,
                },
            )
            _factory_context_edge(
                context_edges,
                batch_id,
                "produces",
                module_id,
                {
                    "recordId": record_id,
                    "batchReferenceId": stable_id(
                        "FactoryBatchReference", evidence_source_id, batch_source_id
                    ),
                    "evidenceSourceId": evidence_source_id,
                },
                occurrence_id=stable_id(
                    "FactoryContextRelationship",
                    evidence_source_id,
                    record_id,
                    batch_source_id,
                    "produces",
                ),
            )

        product_target_id: str | None = None
        if target_level == "component":
            mapped_target_id = target_global_id_map.get(target_id, target_id)
            if mapped_target_id in component_by_global_id:
                product_target_id = component_by_global_id[mapped_target_id]
            elif mapped_target_id in component_ids:
                product_target_id = mapped_target_id
        elif target_level == "module" and target_id in {
            config.module.source_identity,
            config.module.name,
            module_id,
        }:
            product_target_id = module_id

        allocations: list[EnergyAllocationTarget] = []
        allocation_rows_for_record = allocation_rows.get(record_id, ())
        override_spec = _ENERGY_RECORD_ALLOCATION_OVERRIDE.get(record_id)
        if product_target_id is None and override_spec is not None:
            allowed_ids = _subset_component_ids(extraction, override_spec)
            if allowed_ids:
                module_share = float(override_spec.get("module_share", 1.0))
                subset_basis = _component_basis_weights(
                    extraction, allocation_basis, allowed_ids=allowed_ids
                )
                subset_total = math.fsum(weight for _, weight in subset_basis)
                override_targets = _module_allocation_targets(
                    extraction,
                    evidence_source_id=evidence_source_id,
                    record_id=record_id,
                    allocation_basis=allocation_basis,
                    raw_weight=subset_total,
                    raw_weight_unit=allocation_basis,
                    module_share=module_share,
                    allowed_ids=allowed_ids,
                )
                if override_targets:
                    allocations = list(override_targets)
        if product_target_id is None and not allocations and allocation_rows_for_record:
            component_allocation_rows: list[EnergyAllocationTarget] = []
            module_allocation_rows: list[Mapping[str, Any]] = []
            allocation_problem = False
            declared_fraction_total = 0.0
            module_share = 0.0
            for allocation_index, allocation in enumerate(allocation_rows_for_record):
                allocation_target_level = _factory_text(allocation, "target_level").casefold()
                allocation_fraction = _factory_number(allocation.get("allocation_fraction"))
                if (
                    allocation_fraction is None
                    or not 0.0 <= allocation_fraction <= 1.0
                ):
                    allocation_problem = True
                    break
                declared_fraction_total += allocation_fraction
                if allocation_target_level == "module":
                    raw_target = _factory_text(allocation, "target_id")
                    if raw_target in {config.module.source_identity, config.module.name, module_id}:
                        module_allocation_rows.append(allocation)
                        module_share += allocation_fraction
                    continue
                if allocation_target_level != "component":
                    allocation_problem = True
                    break
                raw_target = _factory_text(allocation, "target_id")
                target_component_id = component_by_global_id.get(raw_target, raw_target)
                if target_component_id not in component_ids:
                    allocation_problem = True
                    break
                raw_weight = _factory_number(allocation.get("allocation_value"))
                raw_weight_unit = _factory_text(allocation, "allocation_unit")
                if raw_weight is None or not raw_weight_unit:
                    allocation_problem = True
                    break
                component_allocation_rows.append(
                    EnergyAllocationTarget(
                        target_component_id=target_component_id,
                        raw_weight=raw_weight,
                        raw_weight_unit=raw_weight_unit,
                        normalized_weight=allocation_fraction,
                        evidence_record_id=stable_id(
                            "FactoryAllocationEvidence",
                            evidence_source_id,
                            record_id,
                            allocation_basis,
                            allocation_index,
                            raw_target,
                        ),
                    )
                )
            if not allocation_problem and not math.isclose(
                declared_fraction_total, 1.0, rel_tol=0.0, abs_tol=1e-9
            ):
                allocation_problem = True
            if module_allocation_rows and component_allocation_rows:
                allocation_problem = True
            if module_allocation_rows and not allocation_problem:
                raw_weight_total = 0.0
                raw_weight_unit = ""
                for allocation in module_allocation_rows:
                    raw_weight = _factory_number(allocation.get("allocation_value"))
                    current_unit = _factory_text(allocation, "allocation_unit")
                    if raw_weight is None or not current_unit:
                        allocation_problem = True
                        break
                    if raw_weight_unit and current_unit != raw_weight_unit:
                        allocation_problem = True
                        break
                    raw_weight_unit = current_unit
                    raw_weight_total += raw_weight
                if not allocation_problem:
                    component_allocation_rows = list(
                        _module_allocation_targets(
                            extraction,
                            evidence_source_id=evidence_source_id,
                            record_id=record_id,
                            allocation_basis=allocation_basis,
                            raw_weight=raw_weight_total,
                            raw_weight_unit=raw_weight_unit,
                            module_share=module_share,
                        )
                    )
            if not allocation_problem:
                normalized_total = math.fsum(
                    target.normalized_weight for target in component_allocation_rows
                )
                expected_total = module_share if module_allocation_rows else 1.0
                if math.isclose(normalized_total, expected_total, rel_tol=0.0, abs_tol=1e-9):
                    allocations = component_allocation_rows

        synthetic_record = _factory_record_is_synthetic(
            record_id=record_id,
            source_id=source_id,
            source_meta=source_meta,
        )
        provenance = "synthetic" if synthetic_record else "measured"
        linked_process_ids, linked_resource_ids = _resolve_energy_process_link(
            process_context,
            record_id=record_id,
            carrier=carrier,
            fallback_process_ids=(stage_id, activity_id),
            fallback_resource_ids=(resource_id,),
        )
        if product_target_id is not None:
            plan = EnergyPopulationPlan(
                "direct",
                process_node_ids=linked_process_ids,
                resource_node_ids=linked_resource_ids,
            )
            if process_context is None:
                _factory_context_edge(
                    context_edges,
                    product_target_id,
                    "manufacturedBy",
                    activity_id,
                    {"recordId": record_id, "evidenceSourceId": evidence_source_id},
                    occurrence_id=stable_id(
                        "FactoryContextRelationship",
                        evidence_source_id,
                        record_id,
                        product_target_id,
                        "manufacturedBy",
                    ),
                )
        elif allocations:
            plan = EnergyPopulationPlan(
                "allocated",
                allocation_set_id=stable_id(
                    "FactoryAllocationSet", evidence_source_id, record_id, allocation_basis
                ),
                allocation_basis=allocation_basis,
                unattributed_fraction=1.0
                - math.fsum(target.normalized_weight for target in allocations),
                allocations=tuple(allocations),
                process_node_ids=linked_process_ids,
                resource_node_ids=linked_resource_ids,
            )
        else:
            plan = EnergyPopulationPlan(
                "process_only",
                process_node_ids=linked_process_ids,
                resource_node_ids=linked_resource_ids,
            )

        factor, factor_reason = _select_factory_energy_factor(
            factor_library,
            carrier=carrier,
            quantity_unit=quantity_unit,
            requested_scope=config.requested_scope,
        )
        carrier_key = _factory_carrier_key(carrier, quantity_unit)
        carrier_id = stable_id("EnergyCarrier", carrier_key)
        candidates.append(
            ConsumptionCandidate(
                record_id=record_id,
                kind="energy",
                source_record_id=source_record_id,
                evidence_source_id=evidence_source_id,
                product_target_id=product_target_id if plan.attribution_mode == "direct" else None,
                recorded_scope=config.requested_scope,
                requested_scope=config.requested_scope,
                factor=factor,
                factor_resolution_reason=factor_reason,
                formula_code="measured_energy",
                operands=(
                    QuantityOperand(
                        operand_id=stable_id(
                            "FactoryEnergyOperand", evidence_source_id, record_id
                        ),
                        role="energy_quantity",
                        value=quantity_value,
                        unit=quantity_unit,
                        source_id=source_record_id,
                    ),
                ),
                measured=not synthetic_record,
                data_provenance=provenance,
                energy_carrier_id=carrier_id,
                energy_carrier_key=carrier_key,
                process_ids=(stage_id, activity_id, resource_id),
            )
        )
        plans[record_id] = plan

    return FactoryEvidenceAssembly(
        candidates=tuple(sorted(candidates, key=lambda row: row.record_id)),
        rejected=tuple(sorted(rejected, key=lambda row: (row.record_id, row.reason_code))),
        plans=dict(sorted(plans.items())),
        context_nodes=tuple(
            context_nodes[key] for key in sorted(context_nodes)
        ),
        context_edges=tuple(
            context_edges[key] for key in sorted(context_edges)
        ),
    )


def populate_factory_context(
    graph: CanonicalLPGGraph,
    assembly: FactoryEvidenceAssembly,
) -> None:
    for node in assembly.context_nodes:
        graph.add_node(node.node_id, node.labels, node.props)
    for edge in assembly.context_edges:
        graph.add_edge(
            edge.src,
            edge.rel_type,
            edge.tgt,
            edge.props,
            occurrence_id=edge.occurrence_id,
        )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _thaw(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def build_validation_rows(validations: ValidationResultSet) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for accepted in validations.accepted:
        evidence = {
            "factorSourceRowId": accepted.factor_source_id,
            "formulaCode": accepted.formula_code,
            "measured": accepted.measured,
            "operands": [
                {
                    "designQuantityId": operand.design_quantity_id,
                    "operandId": operand.operand_id,
                    "role": operand.role,
                    "sourceId": operand.source_id,
                    "unit": operand.unit,
                    "value": operand.value,
                }
                for operand in accepted.operands
            ],
            "sourceRecordId": accepted.source_record_id,
        }
        rows.append(
            {
                "recordId": accepted.record_id,
                "sourceIdentity": accepted.source_identity,
                "sourceRecordId": accepted.source_record_id,
                "evidenceSourceId": accepted.evidence_source_id,
                "kind": accepted.kind,
                "status": "accepted",
                "reasonCode": "",
                "message": "",
                "consumptionId": accepted.consumption_id,
                "quantityId": accepted.quantity_id,
                "factorId": stable_id("EmissionFactor", accepted.factor_source_id),
                "emissionId": accepted.emission_id,
                "formulaCode": accepted.formula_code,
                "isValidZero": str(bool(accepted.is_valid_zero)).lower(),
                "evidenceJson": _canonical_json(evidence),
            }
        )
    for issue in validations.rejected:
        evidence = _thaw(issue.evidence)
        evidence_source_id = str(evidence.get("evidenceSourceId", ""))
        source_record_id = str(evidence.get("sourceRecordId", ""))
        if (
            issue.reason_code == "source_record_id_missing"
            and evidence_source_id
            and not source_record_id
        ):
            evidence["sourceIdentity"] = stable_id(
                "ValidationRecord", evidence_source_id, issue.record_id
            )
        elif evidence_source_id and source_record_id:
            evidence["sourceIdentity"] = stable_id(
                "SourceRecord", evidence_source_id, source_record_id
            )
        rows.append(
            {
                "recordId": issue.record_id,
                "sourceIdentity": str(evidence.get("sourceIdentity", "")),
                "sourceRecordId": source_record_id,
                "evidenceSourceId": evidence_source_id,
                "kind": str(evidence.get("kind", "")),
                "status": "rejected",
                "reasonCode": issue.reason_code,
                "message": issue.message,
                "consumptionId": "",
                "quantityId": "",
                "factorId": "",
                "emissionId": "",
                "formulaCode": str(evidence.get("formulaCode", "")),
                "isValidZero": "",
                "evidenceJson": _canonical_json(evidence),
            }
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row["recordId"],
                0 if row["status"] == "accepted" else 1,
                row["reasonCode"],
            ),
        )
    )


def _validation_counts(validations: ValidationResultSet) -> dict[str, Any]:
    accepted_by_kind = Counter(row.kind for row in validations.accepted)
    rejected_by_kind = Counter(
        str(_thaw(row.evidence).get("kind", "")) for row in validations.rejected
    )
    rejected_by_reason = Counter(row.reason_code for row in validations.rejected)
    candidate_count = len(validations.accepted) + len(validations.rejected)
    accepted_count = len(validations.accepted)
    return {
        "candidateCount": candidate_count,
        "acceptedCount": accepted_count,
        "rejectedCount": len(validations.rejected),
        "acceptedByKind": dict(sorted(accepted_by_kind.items())),
        "rejectedByKind": dict(
            sorted((key, value) for key, value in rejected_by_kind.items() if key)
        ),
        "rejectedByReason": dict(sorted(rejected_by_reason.items())),
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": accepted_count / candidate_count if candidate_count else 0.0,
    }


def canonical_stats(
    graph: CanonicalLPGGraph,
    validations: ValidationResultSet,
    module_id: str | None,
) -> dict[str, Any]:
    label_counts = Counter(
        label for node in graph.nodes.values() for label in node.get("labels", ())
    )
    relation_counts = Counter(edge["type"] for edge in graph.edges)
    refinements = {
        label: count
        for label, count in sorted(label_counts.items())
        if label not in APPLICATION_CLASSES and label.startswith("Ifc")
    }
    energy_modes = Counter(
        str(node.get("props", {}).get("attributionMode", ""))
        for node in graph.nodes.values()
        if "EnergyConsumption" in node.get("labels", ())
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "moduleId": module_id or "",
        "nodeCount": len(graph.nodes),
        "edgeCount": len(graph.edges),
        "applicationClassCounts": {
            label: label_counts.get(label, 0) for label in APPLICATION_CLASSES
        },
        "ifcRefinementCounts": refinements,
        "relationCounts": {
            relation: relation_counts.get(relation, 0)
            for relation in (*PRINCIPAL_PREDICATES, *sorted(OPTIONAL_CONTEXT_PREDICATES))
        },
        "calculationCounts": {
            "material": label_counts.get("MaterialConsumption", 0),
            "energy": label_counts.get("EnergyConsumption", 0),
            "validZero": sum(
                bool(node.get("props", {}).get("isValidZero"))
                for node in graph.nodes.values()
                if "CarbonEmission" in node.get("labels", ())
            ),
        },
        "attributionCounts": {
            "direct": energy_modes.get("direct", 0),
            "allocated": energy_modes.get("allocated", 0),
            "process_only": energy_modes.get("process_only", 0),
        },
        "validation": _validation_counts(validations),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _write_validation(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=VALIDATION_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in VALIDATION_FIELDS})


def _validate_release_id(release_id: str) -> str:
    windows_device_stem = (
        release_id.split(".", 1)[0].upper() if isinstance(release_id, str) else ""
    )
    if (
        not isinstance(release_id, str)
        or not _RELEASE_ID.fullmatch(release_id)
        or release_id in {".", ".."}
        or release_id.endswith(".")
        or windows_device_stem in _WINDOWS_RESERVED
        or ":" in release_id
    ):
        raise ReleaseBuildError("invalid_release_id", f"unsafe release id: {release_id!r}")
    return release_id


def audit_graph_payload(graph, validation_rows, context):
    from dm2c_m2_alignment_audit import audit_graph_payload as implementation

    return implementation(graph, validation_rows, context)


def audit_release(release_dir, *, live_cypher_config=None):
    from dm2c_m2_alignment_audit import audit_release as implementation

    return implementation(release_dir, live_cypher_config=live_cypher_config)


def _code_descriptors() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    filenames = (
        "dm2c_m23_canonical_release.py",
        "dm2c_m23_canonical.py",
        "dm2c_m23_ifc.py",
        "dm2c_m23_calculation.py",
        "dm2c_m23_accounting.py",
        "dm2c_multigranular_carbon_kg.py",
        "dm2c_m2_alignment_audit.py",
    )
    return {
        filename: {
            "path": filename,
            **_file_descriptor(root / filename),
        }
        for filename in filenames
    }


def build_release(
    case_config: CaseConfig,
    out_root: Path | str,
    release_id: str,
    *,
    live_cypher_config=None,
) -> Path:
    if not isinstance(case_config, CaseConfig):
        raise TypeError("case_config must be a CaseConfig")
    release_id = _validate_release_id(release_id)
    root = Path(out_root).resolve()
    final_dir = (root / release_id).resolve()
    if final_dir.parent != root:
        raise ReleaseBuildError("invalid_release_id", "release path escapes output root")
    if final_dir.exists():
        raise ReleaseBuildError("destination_exists", f"release destination exists: {final_dir}")
    _verify_binding(case_config.ifc, "ifc")
    _verify_binding(case_config.factor_workbook, "factorWorkbook")
    _verify_binding(case_config.ontology, "ontology")
    if case_config.material_evidence is None and case_config.release_profile != "actual-case":
        raise ReleaseBuildError("material_evidence_required", "materialEvidence is required")
    if case_config.material_evidence is not None:
        _verify_binding(case_config.material_evidence, "materialEvidence")
    if case_config.factory_input is not None:
        _verify_binding(case_config.factory_input, "factoryInput")
    if case_config.factory_target_map is not None:
        _verify_binding(case_config.factory_target_map, "factoryTargetMap")

    root.mkdir(parents=True, exist_ok=True)
    staging = (root / f".{release_id}.staging-{uuid4().hex}").resolve()
    if staging.parent != root or staging.exists():
        raise ReleaseBuildError("staging_path_invalid", "could not allocate safe staging path")
    staging.mkdir()
    try:
        extraction = CanonicalIFCExtractor(
            case_config.ifc.resolved_path,
            include_openings=case_config.include_openings,
        ).extract()
        if extraction.ifc_sha256 != case_config.ifc.sha256:
            raise ReleaseBuildError(
                "input_hash_mismatch", "extracted IFC hash differs from case config"
            )
        factors = FactorLibrary(case_config.factor_workbook.resolved_path)
        if factors.load_error:
            raise ReleaseBuildError("factor_workbook_unreadable", factors.load_error)
        material_assembly = (
            EvidenceAssembly((), (), ())
            if case_config.material_evidence is None
            else assemble_material_evidence(case_config, extraction, factors)
        )
        factory_assembly = assemble_factory_input(case_config, extraction, factors)
        calculated = validate_candidates(
            (*material_assembly.candidates, *factory_assembly.candidates)
        )
        validations = ValidationResultSet(
            accepted=calculated.accepted,
            rejected=tuple(
                sorted(
                    (
                        *material_assembly.rejected,
                        *factory_assembly.rejected,
                        *calculated.rejected,
                    ),
                    key=lambda row: (row.record_id, row.reason_code, row.message),
                )
            ),
        )
        if case_config.release_profile != "actual-case" and not any(
            row.kind == "material" for row in validations.accepted
        ):
            raise ReleaseBuildError(
                "no_accepted_material_evidence",
                "controlled release requires at least one accepted material fact",
            )
        measured_record_ids = frozenset(
            candidate.record_id
            for candidate in (*material_assembly.candidates, *factory_assembly.candidates)
            if candidate.measured
        )
        synthetic_factory_inputs_used = any(
            candidate.data_provenance == "synthetic"
            for candidate in factory_assembly.candidates
        )
        accepted_materials = tuple(
            row for row in validations.accepted if row.kind == "material"
        )
        accepted_energies: list[tuple[AcceptedConsumption, EnergyPopulationPlan]] = []
        for accepted in validations.accepted:
            if accepted.kind != "energy":
                continue
            plan = factory_assembly.plans.get(accepted.record_id)
            if plan is None:
                raise ReleaseBuildError(
                    "factory_energy_plan_missing",
                    f"accepted energy fact has no factory attribution plan: {accepted.record_id}",
                )
            accepted_energies.append((accepted, plan))
        builder = MultiGranularCarbonKGBuilder(
            ifc_path=case_config.ifc.resolved_path,
            factor_library=factors,
            module_name=case_config.module.name,
            include_openings=case_config.include_openings,
            extraction_result=extraction,
            accepted_materials=accepted_materials,
            module_identity=case_config.module.source_identity,
        )
        graph = builder.build()
        populate_factory_context(graph, factory_assembly)
        for accepted, plan in accepted_energies:
            builder.populate_accepted_energy(accepted, plan)
        graph = builder.graph
        rows = build_validation_rows(validations)
        stats = canonical_stats(graph, validations, builder.module_id)

        graph_path = staging / GRAPH_JSON
        cypher_path = staging / CYPHER
        stats_path = staging / STATS_JSON
        validation_path = staging / VALIDATION_CSV
        alignment_path = staging / ALIGNMENT_JSON
        manifest_path = staging / MANIFEST_JSON
        graph.export_json(graph_path)
        graph.export_cypher(cypher_path)
        _write_json(stats_path, stats)
        _write_validation(validation_path, rows)

        from dm2c_m2_alignment_audit import _factor_source_rows_from_library

        audit_context = {
            "schemaVersion": SCHEMA_VERSION,
            "releaseProfile": case_config.release_profile,
            "requestedScope": case_config.requested_scope,
            "ifcSha256": extraction.ifc_sha256,
            "extraction": extraction,
            "moduleId": builder.module_id,
            "moduleSourceIdentity": case_config.module.source_identity,
            "moduleName": case_config.module.name,
            "oneModuleCase": True,
            "factoryInputPresent": case_config.factory_input is not None,
            "factorSourceRows": _factor_source_rows_from_library(factors),
            "expectedValidationRows": rows,
            "measuredRecordIds": measured_record_ids,
            "cypherText": cypher_path.read_text(encoding="utf-8"),
            "liveCypherConfig": live_cypher_config,
        }
        core_report = audit_graph_payload(graph.to_payload(), rows, audit_context)
        if core_report.get("highSeverityViolationCount"):
            raise ReleaseBuildError(
                "alignment_gate_failed", "core canonical alignment audit failed"
            )
        _write_json(alignment_path, core_report)

        output_descriptors = {
            key: _file_descriptor(staging / filename, relative_path=filename)
            for key, filename in OUTPUT_FILENAMES.items()
        }
        config_descriptor = _file_descriptor(case_config.config_path)
        manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "releaseId": release_id,
            "releaseProfile": case_config.release_profile,
            "releaseReady": case_config.release_ready,
            "generatedAtUtc": case_config.generated_at_utc,
            "syntheticFactoryInputsUsed": synthetic_factory_inputs_used,
            "inputs": {
                "caseConfig": {
                    "selectedPath": str(case_config.config_path),
                    "resolvedPath": str(case_config.config_path),
                    **config_descriptor,
                },
                "ifc": _input_descriptor(case_config.ifc),
                "factorWorkbook": _input_descriptor(case_config.factor_workbook),
                "ontology": _input_descriptor(case_config.ontology),
                "materialEvidence": (
                    None
                    if case_config.material_evidence is None
                    else _input_descriptor(case_config.material_evidence)
                ),
                "factoryInput": (
                    None
                    if case_config.factory_input is None
                    else _input_descriptor(case_config.factory_input)
                ),
                "factoryTargetMap": (
                    None
                    if case_config.factory_target_map is None
                    else _input_descriptor(case_config.factory_target_map)
                ),
            },
            "module": {
                "sourceIdentity": case_config.module.source_identity,
                "runtimeId": builder.module_id,
                "name": case_config.module.name,
                "identitySource": case_config.module.identity_source,
            },
            "configuration": {
                "requestedScope": case_config.requested_scope,
                "includeOpenings": case_config.include_openings,
                "factoryInput": (
                    None
                    if case_config.factory_input is None
                    else _input_descriptor(case_config.factory_input)
                ),
                "factoryTargetMap": (
                    None
                    if case_config.factory_target_map is None
                    else _input_descriptor(case_config.factory_target_map)
                ),
            },
            "command": {
                "entryPoint": "dm2c_m23_canonical_release.py",
                "caseConfig": str(case_config.config_path),
                "releaseId": release_id,
            },
            "coverage": _validation_counts(validations),
            "counts": stats,
            "code": _code_descriptors(),
            "outputs": output_descriptors,
            "gates": {
                "alignment": "pass",
                "structuralCypher": core_report["gates"]["structuralCypher"],
                "liveCypherRoundTrip": core_report["gates"]["liveCypherRoundTrip"],
            },
        }
        _write_json(manifest_path, manifest)

        envelope_report = audit_release(
            staging, live_cypher_config=live_cypher_config
        )
        if envelope_report.get("highSeverityViolationCount"):
            raise ReleaseBuildError(
                "alignment_gate_failed", "final release envelope audit failed"
            )
        for key, filename in OUTPUT_FILENAMES.items():
            if _file_descriptor(staging / filename, relative_path=filename) != manifest[
                "outputs"
            ][key]:
                raise ReleaseBuildError(
                    "output_hash_mismatch", f"output changed after hashing: {filename}"
                )
        if final_dir.exists():
            raise ReleaseBuildError(
                "destination_exists", f"release destination appeared concurrently: {final_dir}"
            )
        try:
            staging.rename(final_dir)
        except FileExistsError as exc:
            raise ReleaseBuildError(
                "destination_exists", f"release destination appeared concurrently: {final_dir}"
            ) from exc
        return final_dir
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a canonical M2.3 v2 release")
    parser.add_argument("--case-config", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        config = load_case_config(args.case_config)
        release_dir = build_release(config, args.out_root, args.release_id)
    except (CaseConfigError, ReleaseBuildError) as exc:
        reason = getattr(exc, "reason_code", "case_config_invalid")
        print(f"{reason}: {exc}", file=sys.stderr)
        return 1
    print(release_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CaseConfig",
    "CaseConfigError",
    "EvidenceAssembly",
    "FactoryContextEdge",
    "FactoryContextNode",
    "FactoryEvidenceAssembly",
    "InputBinding",
    "ModuleDeclaration",
    "ReleaseArtifacts",
    "ReleaseBuildError",
    "VALIDATION_FIELDS",
    "assemble_factory_input",
    "assemble_material_evidence",
    "build_release",
    "build_validation_rows",
    "canonical_stats",
    "load_case_config",
    "main",
]
