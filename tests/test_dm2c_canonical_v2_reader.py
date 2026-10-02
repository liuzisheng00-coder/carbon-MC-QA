from __future__ import annotations

from collections import Counter
import csv
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import io
import json
import math
from pathlib import Path
from copy import deepcopy
import shutil
from types import MappingProxyType

import pytest

import dm2c_canonical_v2_reader as canonical_reader

from dm2c_canonical_v2_reader import (
    CanonicalSchemaError,
    components_for_ifc_class,
    dimension_ids,
    evidence_ids,
    iter_emissions,
    iter_product_contributions,
    load_canonical_v2_context,
    lookup_component,
    lookup_dimension,
    lookup_process,
    product_total,
    source_process_total,
)
from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    CanonicalLPGGraph,
    stable_edge_id,
    stable_id,
)


OUTPUTS = {
    "graphJson": "multigranular_carbon_kg.json",
    "cypher": "multigranular_carbon_kg.cypher",
    "stats": "multigranular_carbon_kg_stats.json",
    "validation": "multigranular_carbon_kg_validation.csv",
    "alignmentReport": "m2_alignment_report.json",
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
CODE_FILES = (
    "dm2c_m23_accounting.py",
    "dm2c_m23_calculation.py",
    "dm2c_m23_canonical.py",
    "dm2c_m23_canonical_release.py",
    "dm2c_m23_ifc.py",
    "dm2c_m2_alignment_audit.py",
    "dm2c_multigranular_carbon_kg.py",
)

MODULE = "module:fixture"
COMPONENT_1 = "component:c1"
COMPONENT_2 = "component:c2"
COMPONENT_3 = "component:no-facts"
MATERIAL = "material:steel"
CARRIER_ELECTRICITY = "carrier:electricity"
CARRIER_GAS = "carrier:gas"
PROCESS_TEMPLATE = "process:template"
PROCESS_STAGE = "process:stage"
PROCESS_ACTIVITY = "process:activity"
RESOURCE = "resource:meter"
SCOPE = "A1-A3"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _descriptor(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": path.name,
        "sha256": sha256(payload).hexdigest().upper(),
        "sizeBytes": len(payload),
    }


def _source_identity(evidence_source_id: str, source_record_id: str) -> str:
    return stable_id("SourceRecord", evidence_source_id, source_record_id)


def _factor_props(
    source_row_id: str, keyword: str, denominator: str, factor_value: float
) -> dict[str, object]:
    factor_id = stable_id("EmissionFactor", source_row_id)
    return {
        "factorId": factor_id,
        "factorSourceId": source_row_id,
        "keyword": keyword,
        "factorValue": factor_value,
        "factorUnit": f"kgCO2e/{denominator}",
        "factorDenominator": denominator,
        "originalFactorValue": factor_value,
        "originalFactorUnit": f"kgCO2e/{denominator}",
        "denominatorUnit": denominator,
        "normalizedFactorValue": factor_value,
        "normalizedDenominator": denominator,
        "source": "controlled-fixture",
        "sourceRowId": source_row_id,
        "systemBoundary": "A1\u2014A3",
    }


def _provenance(
    name: str,
    value: float,
    unit: str,
    *,
    design_quantity_id: str | None = None,
) -> tuple[list[str], list[str]]:
    source_id = design_quantity_id or f"meter:{name}"
    operand = {
        "design_quantity_id": design_quantity_id,
        "operand_id": f"operand:{name}",
        "role": "material_mass" if design_quantity_id else "energy_quantity",
        "source_id": source_id,
        "unit": unit,
        "value": value,
    }
    conversion = {
        "conversion_factor": 1.0,
        "design_quantity_id": None,
        "normalized_unit": unit,
        "normalized_value": value,
        "operand_id": stable_id(
            "CalculatedQuantityOperand", f"evidence:{name}", f"record:{name}"
        ),
        "role": "calculated_quantity",
        "source_id": f"evidence:{name}",
        "source_unit": unit,
        "source_value": value,
    }
    return [_canonical_json(operand)], [_canonical_json(conversion)]


def _add_backbone(graph: CanonicalLPGGraph) -> None:
    graph.add_node(MODULE, ["ModularUnit"], {"name": "Controlled module"})
    graph.add_node(
        COMPONENT_1,
        ["BuildingComponent", "IfcBeam"],
        {"globalId": "C1", "ifcClass": "IfcBeam"},
    )
    graph.add_node(
        COMPONENT_2,
        ["BuildingComponent", "IfcColumn"],
        {"globalId": "C2", "ifcClass": "IfcColumn"},
    )
    graph.add_node(
        COMPONENT_3,
        ["BuildingComponent", "IfcWall"],
        {"globalId": "C3", "ifcClass": "IfcWall"},
    )
    graph.add_node("type:structural", ["ComponentType"], {"name": "Structural"})
    graph.add_node("type:wall", ["ComponentType"], {"name": "Wall"})
    graph.add_node(MATERIAL, ["IfcMaterial"], {"name": "Steel"})
    graph.add_node("material:unused", ["IfcMaterial"], {"name": "Timber"})
    graph.add_node("dq:material", ["DesignQuantity", "IfcQuantityWeight"], {"value": 20.0, "unit": "kg"})
    graph.add_node("dq:unused", ["DesignQuantity", "IfcQuantityWeight"], {"value": 1.0, "unit": "kg"})
    graph.add_node(PROCESS_TEMPLATE, ["ManufacturingProcessTemplate"], {"name": "Assembly"})
    graph.add_node(PROCESS_STAGE, ["ProductionStage"], {"name": "Stage"})
    graph.add_node(PROCESS_ACTIVITY, ["ManufacturingActivity"], {"name": "Activity"})
    graph.add_node(RESOURCE, ["ManufacturingResource"], {"name": "Meter"})

    def edge(src: str, relation: str, tgt: str, props: dict[str, object] | None = None, occurrence: str | None = None) -> None:
        graph.add_edge(
            src,
            relation,
            tgt,
            props,
            occurrence_id=occurrence or f"occ:{src}:{relation}:{tgt}",
        )

    for component in (COMPONENT_1, COMPONENT_2, COMPONENT_3):
        edge(MODULE, "containsComponent", component)
    edge(COMPONENT_1, "hasComponentType", "type:structural")
    edge(COMPONENT_2, "hasComponentType", "type:structural")
    edge(COMPONENT_3, "hasComponentType", "type:wall")
    edge(COMPONENT_1, "hasMaterial", MATERIAL, occurrence="source:material")
    edge(COMPONENT_3, "hasMaterial", "material:unused")
    edge(COMPONENT_1, "hasDesignQuantity", "dq:material")
    edge(COMPONENT_3, "hasDesignQuantity", "dq:unused")
    edge("type:structural", "hasProcessTemplate", PROCESS_TEMPLATE)
    edge(PROCESS_TEMPLATE, "hasStage", PROCESS_STAGE)
    edge(PROCESS_STAGE, "hasActivity", PROCESS_ACTIVITY)
    edge(PROCESS_ACTIVITY, "usesResource", RESOURCE)
    edge(COMPONENT_1, "manufacturedBy", PROCESS_ACTIVITY)


def _add_fact(
    graph: CanonicalLPGGraph,
    *,
    name: str,
    kind: str,
    mode: str,
    quantity_value: float,
    quantity_unit: str,
    factor_source_id: str,
    factor_keyword: str,
    factor_value: float = 1.0,
    emission_value: float,
    component_id: str | None = None,
    material_id: str | None = None,
    carrier_id: str | None = None,
    allocations: tuple[tuple[str, float, str], ...] = (),
    process_only: bool = False,
    valid_zero: bool = False,
    design_quantity_id: str = "dq:material",
) -> dict[str, str]:
    record_id = f"record:{name}"
    source_record_id = "source:material" if name == "material" else f"source:{name}"
    evidence_source_id = f"evidence:{name}"
    consumption_id = f"consumption:{name}"
    quantity_id = f"quantity:{name}"
    emission_id = f"emission:{name}"
    factor_id = stable_id("EmissionFactor", factor_source_id)
    source_identity = _source_identity(evidence_source_id, source_record_id)
    formula = "direct_mass" if kind == "material" else "measured_energy"
    common = {
        "recordId": record_id,
        "sourceIdentity": source_identity,
        "sourceRecordId": source_record_id,
        "evidenceSourceId": evidence_source_id,
        "formulaCode": formula,
        "recordedScope": "A1\u2013A3",
        "requestedScope": " a1 - a3 ",
        "systemBoundary": "A1\u2014A3",
        "isValidZero": valid_zero,
    }
    consumption_props: dict[str, object] = {
        **common,
        "quantityId": quantity_id,
        "factorId": factor_id,
        "factorSourceId": factor_source_id,
        "factorAliasId": f"factor-alias:{name}",
        "emissionId": emission_id,
    }
    if kind == "material":
        consumption_props.update(
            {"productTargetId": component_id, "materialId": material_id}
        )
    else:
        consumption_props.update(
            {"attributionMode": mode, "energyCarrierId": carrier_id}
        )
        if mode == "direct":
            consumption_props["productTargetId"] = component_id
        elif mode == "allocated":
            consumption_props.update(
                {
                    "allocationSetId": "allocation:set:1",
                    "allocationBasis": "mass",
                    "unattributedFraction": 0.0,
                }
            )
    operands, conversions = _provenance(
        name,
        quantity_value,
        quantity_unit,
        design_quantity_id=design_quantity_id if kind == "material" else None,
    )
    quantity_props = {
        **common,
        "consumptionId": consumption_id,
        "orderedRawOperands": operands,
        "conversionSteps": conversions,
        "quantityValue": quantity_value,
        "quantityUnit": quantity_unit,
        "quantityConversionFactor": 1.0,
    }
    emission_props = {
        **common,
        "consumptionId": consumption_id,
        "quantityId": quantity_id,
        "factorId": factor_id,
        "factorSourceId": factor_source_id,
        "factorAliasId": f"factor-alias:{name}",
        "quantityValue": quantity_value,
        "quantityUnit": quantity_unit,
        "factorValue": factor_value,
        "factorDenominator": quantity_unit,
        "emissionValue": emission_value,
        "emissionUnit": "kgCO2e",
    }
    graph.add_node(
        consumption_id,
        ["MaterialConsumption" if kind == "material" else "EnergyConsumption"],
        consumption_props,
    )
    graph.add_node(quantity_id, ["ConsumptionQuantity"], quantity_props)
    graph.add_node(
        factor_id,
        ["EmissionFactor"],
        _factor_props(
            factor_source_id, factor_keyword, quantity_unit, factor_value
        ),
    )
    if carrier_id:
        graph.add_node(carrier_id, ["EnergyCarrier"], {"carrierId": carrier_id})
    graph.add_node(emission_id, ["CarbonEmission"], emission_props)

    def edge(relation: str, tgt: str, props: dict[str, object] | None = None, suffix: str = "") -> None:
        graph.add_edge(
            consumption_id,
            relation,
            tgt,
            props,
            occurrence_id=f"occ:{name}:{relation}:{tgt}:{suffix}",
        )

    edge("hasQuantity", quantity_id)
    edge("hasFactor", factor_id)
    graph.add_edge(
        emission_id,
        "hasCarbonDriver",
        consumption_id,
        occurrence_id=f"occ:{name}:hasCarbonDriver",
    )
    if kind == "material":
        assert component_id and material_id
        edge("recordedForObject", component_id, {"recordId": record_id})
        edge("ofMaterial", material_id, {"recordId": record_id})
        graph.add_edge(
            quantity_id,
            "derivedFrom",
            design_quantity_id,
            occurrence_id=f"occ:{name}:derivedFrom",
        )
    else:
        assert carrier_id
        edge("ofCarrier", carrier_id)
        if mode == "direct":
            assert component_id
            edge(
                "recordedForObject",
                component_id,
                {
                    "sourceRecordId": source_record_id,
                    "evidenceSourceId": evidence_source_id,
                },
            )
        elif mode == "allocated":
            for target, weight, evidence_id in allocations:
                edge(
                    "recordedForObject",
                    target,
                    {
                        "attributionMode": "allocated",
                        "allocationSetId": "allocation:set:1",
                        "allocationBasis": "mass",
                        "rawWeight": weight,
                        "rawWeightUnit": "kg",
                        "normalizedWeight": weight,
                        "evidenceRecordId": evidence_id,
                    },
                    suffix=evidence_id,
                )
        if process_only:
            edge(
                "associatedWithProcess",
                PROCESS_STAGE,
                {
                    "sourceRecordId": source_record_id,
                    "evidenceSourceId": evidence_source_id,
                },
            )
            edge(
                "recordedForResource",
                RESOURCE,
                {
                    "sourceRecordId": source_record_id,
                    "evidenceSourceId": evidence_source_id,
                },
            )
    return {
        "recordId": record_id,
        "sourceIdentity": source_identity,
        "sourceRecordId": source_record_id,
        "evidenceSourceId": evidence_source_id,
        "kind": kind,
        "status": "accepted",
        "reasonCode": "",
        "message": "",
        "consumptionId": consumption_id,
        "quantityId": quantity_id,
        "factorId": factor_id,
        "emissionId": emission_id,
        "formulaCode": formula,
        "isValidZero": str(valid_zero).lower(),
        "evidenceJson": _canonical_json(
            {
                "factorSourceRowId": factor_source_id,
                "formulaCode": formula,
                "sourceRecordId": source_record_id,
            }
        ),
    }


def _validation_counts(rows: list[dict[str, str]]) -> dict[str, object]:
    accepted = [row for row in rows if row["status"] == "accepted"]
    rejected = [row for row in rows if row["status"] == "rejected"]
    accepted_by_kind = Counter(row["kind"] for row in accepted)
    rejected_by_kind = Counter(row["kind"] for row in rejected)
    rejected_by_reason = Counter(row["reasonCode"] for row in rejected)
    return {
        "candidateCount": len(rows),
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "acceptedByKind": dict(sorted(accepted_by_kind.items())),
        "rejectedByKind": dict(sorted(rejected_by_kind.items())),
        "rejectedByReason": dict(sorted(rejected_by_reason.items())),
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": len(accepted) / len(rows),
    }


def _stats(payload: dict[str, object], rows: list[dict[str, str]]) -> dict[str, object]:
    nodes = payload["nodes"]
    edges = payload["edges"]
    assert isinstance(nodes, list) and isinstance(edges, list)
    labels = Counter(label for node in nodes for label in node["labels"])
    relations = Counter(edge["type"] for edge in edges)
    modes = Counter(
        node["props"].get("attributionMode", "")
        for node in nodes
        if "EnergyConsumption" in node["labels"]
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "moduleId": MODULE,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "applicationClassCounts": {
            label: labels.get(label, 0) for label in APPLICATION_CLASSES
        },
        "ifcRefinementCounts": {
            label: count
            for label, count in sorted(labels.items())
            if label not in APPLICATION_CLASSES and label.startswith("Ifc")
        },
        "relationCounts": {
            relation: relations.get(relation, 0)
            for relation in (*PRINCIPAL_PREDICATES, *sorted(OPTIONAL_CONTEXT_PREDICATES))
        },
        "calculationCounts": {"material": 1, "energy": 4, "validZero": 1},
        "attributionCounts": {
            "direct": modes["direct"],
            "allocated": modes["allocated"],
            "process_only": modes["process_only"],
        },
        "validation": _validation_counts(rows),
    }


def write_release(root: Path, *, reverse_graph_order: bool = False) -> Path:
    root.mkdir()
    graph = CanonicalLPGGraph()
    _add_backbone(graph)
    rows = [
        _add_fact(
            graph,
            name="material",
            kind="material",
            mode="material",
            quantity_value=20.0,
            quantity_unit="kg",
            factor_source_id="factor-source:material",
            factor_keyword="steel",
            factor_value=0.5,
            emission_value=10.0,
            component_id=COMPONENT_1,
            material_id=MATERIAL,
        ),
        _add_fact(
            graph,
            name="direct",
            kind="energy",
            mode="direct",
            quantity_value=6.0,
            quantity_unit="kWh",
            factor_source_id="factor-source:electricity",
            factor_keyword="electricity",
            emission_value=6.0,
            component_id=COMPONENT_1,
            carrier_id=CARRIER_ELECTRICITY,
        ),
        _add_fact(
            graph,
            name="allocated",
            kind="energy",
            mode="allocated",
            quantity_value=20.0,
            quantity_unit="kWh",
            factor_source_id="factor-source:electricity",
            factor_keyword="electricity",
            emission_value=20.0,
            carrier_id=CARRIER_ELECTRICITY,
            allocations=(
                (COMPONENT_1, 0.25, "allocation:evidence:c1"),
                (COMPONENT_2, 0.75, "allocation:evidence:c2"),
            ),
        ),
        _add_fact(
            graph,
            name="process",
            kind="energy",
            mode="process_only",
            quantity_value=4.0,
            quantity_unit="m3",
            factor_source_id="factor-source:gas",
            factor_keyword="natural gas",
            emission_value=4.0,
            carrier_id=CARRIER_GAS,
            process_only=True,
        ),
        _add_fact(
            graph,
            name="zero",
            kind="energy",
            mode="direct",
            quantity_value=0.0,
            quantity_unit="kWh",
            factor_source_id="factor-source:electricity",
            factor_keyword="electricity",
            emission_value=0.0,
            component_id=COMPONENT_2,
            carrier_id=CARRIER_ELECTRICITY,
            valid_zero=True,
        ),
    ]
    rejected_source = "source:rejected"
    rejected_evidence = "evidence:rejected"
    rejected_identity = _source_identity(rejected_evidence, rejected_source)
    rows.append(
        {
            "recordId": "record:rejected",
            "sourceIdentity": rejected_identity,
            "sourceRecordId": rejected_source,
            "evidenceSourceId": rejected_evidence,
            "kind": "energy",
            "status": "rejected",
            "reasonCode": "factor_not_found",
            "message": "No exact factor mapping.",
            "consumptionId": "",
            "quantityId": "",
            "factorId": "",
            "emissionId": "",
            "formulaCode": "measured_energy",
            "isValidZero": "",
            "evidenceJson": _canonical_json(
                {
                    "evidenceSourceId": rejected_evidence,
                    "formulaCode": "measured_energy",
                    "kind": "energy",
                    "sourceIdentity": rejected_identity,
                    "sourceRecordId": rejected_source,
                }
            ),
        }
    )
    rows.sort(
        key=lambda row: (
            row["recordId"],
            0 if row["status"] == "accepted" else 1,
            row["reasonCode"],
        )
    )
    payload = graph.to_payload()
    if reverse_graph_order:
        payload["nodes"].reverse()
        payload["edges"].reverse()
    stats = _stats(payload, rows)

    graph_path = root / OUTPUTS["graphJson"]
    cypher_path = root / OUTPUTS["cypher"]
    stats_path = root / OUTPUTS["stats"]
    validation_path = root / OUTPUTS["validation"]
    alignment_path = root / OUTPUTS["alignmentReport"]
    _write_json(graph_path, payload)
    cypher_path.write_text(graph.to_cypher(), encoding="utf-8", newline="")
    _write_json(stats_path, stats)
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=VALIDATION_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    validation_path.write_text(handle.getvalue(), encoding="utf-8", newline="")
    gates = {
        "alignment": "pass",
        "structuralCypher": "pass",
        "liveCypherRoundTrip": "not_configured",
    }
    alignment = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "pass",
        "highSeverityViolationCount": 0,
        "findings": [],
        "gates": gates,
        "projectionTotals": {"product": 36.0, "sourceProcess": 30.0},
        "checkedCounts": {
            "nodes": stats["nodeCount"],
            "edges": stats["edgeCount"],
            "validationRows": len(rows),
        },
    }
    _write_json(alignment_path, alignment)
    dummy_input = {
        "selectedPath": "frozen-source.dat",
        "resolvedPath": "frozen-source.dat",
        "sha256": "0" * 64,
        "sizeBytes": 0,
    }
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "releaseId": "controlled-reader-fixture",
        "releaseProfile": "controlled-fixture",
        "releaseReady": True,
        "generatedAtUtc": "2026-07-20T00:00:00Z",
        "syntheticFactoryInputsUsed": False,
        "inputs": {
            "caseConfig": dummy_input,
            "ifc": dummy_input,
            "factorWorkbook": dummy_input,
            "ontology": dummy_input,
            "materialEvidence": dummy_input,
            "factoryInput": None,
        },
        "module": {
            "sourceIdentity": "module-source:fixture",
            "runtimeId": MODULE,
            "name": "Controlled module",
            "identitySource": "controlled-fixture",
        },
        "configuration": {
            "requestedScope": SCOPE,
            "includeOpenings": False,
            "factoryInput": None,
        },
        "command": {
            "entryPoint": "dm2c_m23_canonical_release.py",
            "caseConfig": "frozen-source.dat",
            "releaseId": "controlled-reader-fixture",
        },
        "coverage": stats["validation"],
        "counts": stats,
        "code": {
            name: {"path": name, "sha256": "0" * 64, "sizeBytes": 0}
            for name in CODE_FILES
        },
        "outputs": {
            key: _descriptor(root / filename) for key, filename in OUTPUTS.items()
        },
        "gates": gates,
    }
    _write_json(root / "case_version_manifest.json", manifest)
    return root


def _read_manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "case_version_manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    _write_json(root / "case_version_manifest.json", manifest)


def _refresh_outputs(root: Path, manifest: dict[str, object]) -> None:
    manifest["outputs"] = {
        key: _descriptor(root / filename) for key, filename in OUTPUTS.items()
    }


def _payload_to_graph(payload: dict[str, object]) -> CanonicalLPGGraph:
    rebuilt = CanonicalLPGGraph()
    for node in payload["nodes"]:
        rebuilt.add_node(node["id"], node["labels"], node["props"])
    for edge in payload["edges"]:
        rebuilt.add_edge(
            edge["src"],
            edge["type"],
            edge["tgt"],
            edge["props"],
            occurrence_id=edge["occurrenceId"],
        )
    return rebuilt


def _read_validation_rows(root: Path) -> list[dict[str, str]]:
    with (root / OUTPUTS["validation"]).open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        return list(csv.DictReader(handle))


def _rewrite_validation_rows(
    root: Path, rows: list[dict[str, str]], *, reconcile_stats: bool = True
) -> None:
    rows.sort(
        key=lambda row: (
            row["recordId"],
            0 if row["status"] == "accepted" else 1,
            row["reasonCode"],
        )
    )
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=VALIDATION_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    (root / OUTPUTS["validation"]).write_text(
        handle.getvalue(), encoding="utf-8", newline=""
    )
    manifest = _read_manifest(root)
    if reconcile_stats:
        graph_payload = json.loads(
            (root / OUTPUTS["graphJson"]).read_text(encoding="utf-8")
        )
        stats = _stats(graph_payload, rows)
        _write_json(root / OUTPUTS["stats"], stats)
        manifest["counts"] = stats
        manifest["coverage"] = stats["validation"]
    alignment = json.loads(
        (root / OUTPUTS["alignmentReport"]).read_text(encoding="utf-8")
    )
    alignment["checkedCounts"]["validationRows"] = len(rows)
    _write_json(root / OUTPUTS["alignmentReport"], alignment)
    _refresh_outputs(root, manifest)
    _write_manifest(root, manifest)


def _rewrite_graph(
    root: Path,
    mutate,
    *,
    reconcile_counts: bool = False,
    rebuild_cypher: bool = True,
) -> dict[str, object]:
    graph_path = root / OUTPUTS["graphJson"]
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    mutate(payload)
    _write_json(graph_path, payload)
    if rebuild_cypher:
        (root / OUTPUTS["cypher"]).write_text(
            _payload_to_graph(payload).to_cypher(), encoding="utf-8", newline=""
        )
    manifest = _read_manifest(root)
    if reconcile_counts:
        rows = _read_validation_rows(root)
        stats = _stats(payload, rows)
        _write_json(root / OUTPUTS["stats"], stats)
        manifest["counts"] = stats
        manifest["coverage"] = stats["validation"]
        alignment = json.loads(
            (root / OUTPUTS["alignmentReport"]).read_text(encoding="utf-8")
        )
        alignment["checkedCounts"] = {
            "nodes": stats["nodeCount"],
            "edges": stats["edgeCount"],
            "validationRows": len(rows),
        }
        _write_json(root / OUTPUTS["alignmentReport"], alignment)
    _refresh_outputs(root, manifest)
    _write_manifest(root, manifest)
    return payload


def _write_raw_output(root: Path, key: str, payload: bytes) -> None:
    (root / OUTPUTS[key]).write_bytes(payload)
    manifest = _read_manifest(root)
    _refresh_outputs(root, manifest)
    _write_manifest(root, manifest)


@pytest.fixture()
def release_dir(tmp_path: Path) -> Path:
    return write_release(tmp_path / "release")


def test_loads_complete_immutable_context_and_exact_projections(release_dir: Path) -> None:
    context = load_canonical_v2_context(release_dir)

    assert [fact.emission_id for fact in context.emissions] == sorted(
        [
            "emission:material",
            "emission:direct",
            "emission:allocated",
            "emission:process",
            "emission:zero",
        ]
    )
    assert [fact.emission_id for fact in context.process_only_emissions] == [
        "emission:process"
    ]
    assert len(context.product_contributions) == 5
    assert product_total(context) == pytest.approx(36.0)
    assert product_total(context, component_id=COMPONENT_1) == pytest.approx(21.0)
    assert product_total(context, component_id=COMPONENT_2) == pytest.approx(15.0)
    assert product_total(context, component_id=COMPONENT_3) == pytest.approx(0.0)
    assert product_total(context, module_id=MODULE) == pytest.approx(36.0)
    assert product_total(context, material_id=MATERIAL) == pytest.approx(10.0)
    assert product_total(context, carrier_id=CARRIER_ELECTRICITY) == pytest.approx(26.0)
    assert source_process_total(context) == pytest.approx(30.0)
    assert source_process_total(context, carrier_id=CARRIER_ELECTRICITY) == pytest.approx(26.0)
    assert source_process_total(context, carrier_id=CARRIER_GAS) == pytest.approx(4.0)
    assert product_total(context, requested_scope=" a1 \u2013 a3 ") == pytest.approx(36.0)

    allocated = tuple(
        item
        for item in iter_product_contributions(context)
        if item.mode == "allocated"
    )
    assert [(item.component_id, item.projected_value) for item in allocated] == [
        (COMPONENT_1, 5.0),
        (COMPONENT_2, 15.0),
    ]
    assert math.fsum(item.projected_value for item in allocated) == pytest.approx(
        allocated[0].source_emission_value
    )
    assert tuple(iter_emissions(context, kind="energy")) == tuple(
        fact for fact in context.emissions if fact.kind == "energy"
    )
    assert [row["status"] for row in context.validation_rows].count("rejected") == 1
    assert not any(fact.record_id == "record:rejected" for fact in context.emissions)

    with pytest.raises(FrozenInstanceError):
        context.emissions[0].emission_id = "changed"  # type: ignore[misc]
    assert isinstance(context.manifest, MappingProxyType)
    with pytest.raises(TypeError):
        context.manifest["releaseReady"] = False  # type: ignore[index]
    with pytest.raises(TypeError):
        context.emissions[0].ordered_raw_operands[0]["value"] = 999  # type: ignore[index]


def test_evidence_and_dimension_helpers_are_explicit_and_strict(release_dir: Path) -> None:
    context = load_canonical_v2_context(release_dir)
    allocated = next(
        item
        for item in context.product_contributions
        if item.key == (
            "allocated",
            "emission:allocated",
            "allocation:set:1",
            COMPONENT_1,
        )
    )
    ids = evidence_ids(context, allocated.emission_id, allocated.key)
    assert ids[-3:] == (
        COMPONENT_1,
        allocated.recorded_for_occurrence_id,
        "allocation:evidence:c1",
    )
    assert "factor-alias:allocated" not in ids

    assert dimension_ids(context, "component") == (
        COMPONENT_1,
        COMPONENT_2,
        COMPONENT_3,
    )
    assert dimension_ids(context, "process") == (
        PROCESS_ACTIVITY,
        PROCESS_STAGE,
        PROCESS_TEMPLATE,
    )
    assert components_for_ifc_class(context, "IfcBeam") == (COMPONENT_1,)
    assert lookup_component(context, COMPONENT_1)["id"] == COMPONENT_1
    assert lookup_process(context, PROCESS_STAGE)["id"] == PROCESS_STAGE
    assert lookup_dimension(context, "ifc_class", "IfcBeam") == (COMPONENT_1,)
    with pytest.raises(CanonicalSchemaError):
        lookup_component(context, MATERIAL)
    with pytest.raises(CanonicalSchemaError):
        product_total(context, component_id="component:unknown")
    with pytest.raises(CanonicalSchemaError):
        source_process_total(context, carrier_id="carrier:unknown")


def test_graph_array_order_does_not_change_context(tmp_path: Path) -> None:
    ordered = load_canonical_v2_context(write_release(tmp_path / "ordered"))
    shuffled = load_canonical_v2_context(
        write_release(tmp_path / "reversed", reverse_graph_order=True)
    )
    assert shuffled.emissions == ordered.emissions
    assert shuffled.product_contributions == ordered.product_contributions
    assert shuffled.process_only_emissions == ordered.process_only_emissions
    assert product_total(shuffled) == product_total(ordered) == pytest.approx(36.0)
    assert source_process_total(shuffled) == source_process_total(ordered) == pytest.approx(
        30.0
    )


def test_component_type_ids_are_exposed_without_raw_graph_traversal(
    release_dir: Path,
) -> None:
    from dm2c_canonical_v2_reader import component_type_ids_for_component

    context = load_canonical_v2_context(release_dir)

    assert component_type_ids_for_component(context, COMPONENT_1) == (
        "type:structural",
    )
    assert component_type_ids_for_component(context, COMPONENT_3) == ("type:wall",)
    with pytest.raises(
        CanonicalSchemaError,
        match="unknown for canonical dimension 'component'",
    ):
        component_type_ids_for_component(context, "component:missing")


def test_unknown_schema_or_legacy_vocabulary_is_rejected(release_dir: Path) -> None:
    manifest_path = release_dir / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schemaVersion"] = "legacy"
    _write_json(manifest_path, manifest)
    with pytest.raises(CanonicalSchemaError, match="schema"):
        load_canonical_v2_context(release_dir)


def test_unsafe_release_identity_is_rejected(release_dir: Path) -> None:
    manifest = _read_manifest(release_dir)
    manifest["releaseId"] = "../escape"
    manifest["command"]["releaseId"] = "../escape"
    _write_manifest(release_dir, manifest)
    with pytest.raises(CanonicalSchemaError, match="releaseId"):
        load_canonical_v2_context(release_dir)


def test_rejected_validation_kind_is_typed(release_dir: Path) -> None:
    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    rejected["kind"] = "legacy-process"
    evidence = json.loads(rejected["evidenceJson"])
    evidence["kind"] = "legacy-process"
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    with pytest.raises(CanonicalSchemaError, match="kind"):
        load_canonical_v2_context(release_dir)


def test_factor_denominator_metadata_must_reconcile(release_dir: Path) -> None:
    def mutate(payload):
        factor = next(
            node
            for node in payload["nodes"]
            if node["id"] == stable_id("EmissionFactor", "factor-source:material")
        )
        factor["props"]["factorDenominator"] = "m3"

    _rewrite_graph(release_dir, mutate)
    with pytest.raises(CanonicalSchemaError, match="denominator"):
        load_canonical_v2_context(release_dir)


def test_alignment_high_count_must_be_an_integer(release_dir: Path) -> None:
    alignment_path = release_dir / OUTPUTS["alignmentReport"]
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    alignment["highSeverityViolationCount"] = 0.0
    _write_json(alignment_path, alignment)
    manifest = _read_manifest(release_dir)
    _refresh_outputs(release_dir, manifest)
    _write_manifest(release_dir, manifest)
    with pytest.raises(CanonicalSchemaError, match="high-severity"):
        load_canonical_v2_context(release_dir)


def test_orphan_canonical_factor_is_rejected(release_dir: Path) -> None:
    def mutate(payload):
        source = "factor-source:orphan"
        payload["nodes"].append(
            {
                "id": stable_id("EmissionFactor", source),
                "labels": ["EmissionFactor"],
                "props": _factor_props(source, "orphan", "kg", 1.0),
            }
        )

    _rewrite_graph(release_dir, mutate, reconcile_counts=True)
    with pytest.raises(CanonicalSchemaError, match="orphan|unused"):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(
    "case",
    (
        "release_not_ready",
        "synthetic_factory",
        "gate_failed",
        "unsafe_output_path",
        "lowercase_hash",
        "missing_output_descriptor",
        "unexpected_manifest_field",
        "stale_counts",
        "wrong_entry_point",
    ),
)
def test_manifest_and_descriptor_mutations_are_rejected(
    release_dir: Path, case: str
) -> None:
    manifest = _read_manifest(release_dir)
    if case == "release_not_ready":
        manifest["releaseReady"] = False
    elif case == "synthetic_factory":
        manifest["syntheticFactoryInputsUsed"] = True
    elif case == "gate_failed":
        manifest["gates"]["structuralCypher"] = "fail"
    elif case == "unsafe_output_path":
        manifest["outputs"]["graphJson"]["path"] = "../graph.json"
    elif case == "lowercase_hash":
        manifest["outputs"]["graphJson"]["sha256"] = manifest["outputs"][
            "graphJson"
        ]["sha256"].lower()
    elif case == "missing_output_descriptor":
        del manifest["outputs"]["stats"]
    elif case == "unexpected_manifest_field":
        manifest["legacyCompatibility"] = True
    elif case == "stale_counts":
        manifest["counts"]["nodeCount"] += 1
    elif case == "wrong_entry_point":
        manifest["command"]["entryPoint"] = "legacy_reader.py"
    _write_manifest(release_dir, manifest)
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize("case", ("missing", "unexpected"))
def test_exact_six_file_set_is_required(release_dir: Path, case: str) -> None:
    if case == "missing":
        (release_dir / OUTPUTS["cypher"]).unlink()
    else:
        (release_dir / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(CanonicalSchemaError, match="exactly six"):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(
    "case", ("duplicate_key", "nan", "invalid_utf8", "non_object")
)
def test_strict_graph_json_rejects_duplicate_nonfinite_and_invalid_documents(
    release_dir: Path, case: str
) -> None:
    original = (release_dir / OUTPUTS["graphJson"]).read_bytes()
    text = original.decode("utf-8")
    if case == "duplicate_key":
        marker = f'"schemaVersion": "{SCHEMA_VERSION}"'
        payload = text.replace(marker, f'{marker},\n  {marker}', 1).encode("utf-8")
    elif case == "nan":
        payload = text.replace('"value": 20.0', '"value": NaN', 1).encode("utf-8")
    elif case == "invalid_utf8":
        payload = b"\xff" + original
    else:
        payload = b"[]"
    _write_raw_output(release_dir, "graphJson", payload)
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(
    "case",
    (
        "graph_schema",
        "legacy_label",
        "legacy_relation",
        "duplicate_node",
        "duplicate_occurrence",
        "missing_endpoint",
        "wrong_typed_triple",
        "extra_node_field",
        "extra_edge_field",
    ),
)
def test_graph_shape_vocabulary_and_identity_mutations_are_rejected(
    release_dir: Path, case: str
) -> None:
    def mutate(payload):
        if case == "graph_schema":
            payload["schemaVersion"] = "legacy"
        elif case == "legacy_label":
            node = next(node for node in payload["nodes"] if node["id"] == COMPONENT_1)
            node["labels"].append("AtomicCarbonEmission")
            node["labels"].sort()
        elif case == "legacy_relation":
            edge = next(edge for edge in payload["edges"] if edge["type"] == "hasFactor")
            edge["type"] = "HAS_EMISSION_FACTOR"
        elif case == "duplicate_node":
            payload["nodes"].append(deepcopy(payload["nodes"][0]))
        elif case == "duplicate_occurrence":
            duplicate = deepcopy(payload["edges"][0])
            duplicate["id"] = "edge:duplicate"
            payload["edges"].append(duplicate)
        elif case == "missing_endpoint":
            edge = payload["edges"][0]
            edge["tgt"] = "component:missing"
        elif case == "wrong_typed_triple":
            edge = next(
                edge for edge in payload["edges"] if edge["type"] == "containsComponent"
            )
            edge["tgt"] = MATERIAL
            edge["id"] = stable_edge_id(
                edge["type"], edge["src"], edge["tgt"], edge["occurrenceId"]
            )
        elif case == "extra_node_field":
            payload["nodes"][0]["legacy"] = True
        elif case == "extra_edge_field":
            payload["edges"][0]["legacy"] = True

    _rewrite_graph(release_dir, mutate, rebuild_cypher=False)
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(
    "case",
    (
        "missing_has_factor",
        "duplicate_has_factor",
        "q_times_ef",
        "negative_quantity",
        "boolean_factor",
        "unit_mismatch",
        "scope_mismatch",
        "valid_zero_inconsistent",
        "energy_mode_missing",
        "direct_allocation_field",
        "direct_duplicate_target",
        "allocated_missing_field",
        "allocation_not_conserved",
        "allocated_semantic_duplicate",
        "process_context_missing",
        "process_has_product_path",
        "material_energy_property",
        "provenance_noncanonical",
        "provenance_duplicate_key",
        "provenance_nested_mapping",
        "design_quantity_mismatch",
    ),
)
def test_fact_attribution_and_provenance_mutations_are_rejected(
    release_dir: Path, case: str
) -> None:
    edge_count_changes = case in {
        "missing_has_factor",
        "duplicate_has_factor",
        "direct_duplicate_target",
        "allocated_semantic_duplicate",
        "process_context_missing",
        "process_has_product_path",
    }

    def node(payload, node_id):
        return next(item for item in payload["nodes"] if item["id"] == node_id)

    def add_edge(payload, source, relation, target, props, occurrence):
        payload["edges"].append(
            {
                "id": stable_edge_id(relation, source, target, occurrence),
                "src": source,
                "type": relation,
                "tgt": target,
                "occurrenceId": occurrence,
                "props": props,
            }
        )

    def mutate(payload):
        if case == "missing_has_factor":
            payload["edges"][:] = [
                edge
                for edge in payload["edges"]
                if not (
                    edge["src"] == "consumption:direct"
                    and edge["type"] == "hasFactor"
                )
            ]
        elif case == "duplicate_has_factor":
            original = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:direct"
                and edge["type"] == "hasFactor"
            )
            add_edge(
                payload,
                original["src"],
                original["type"],
                original["tgt"],
                {},
                "occ:duplicate:hasFactor",
            )
        elif case == "q_times_ef":
            node(payload, "emission:material")["props"]["emissionValue"] = 11.0
        elif case == "negative_quantity":
            node(payload, "quantity:material")["props"]["quantityValue"] = -1.0
        elif case == "boolean_factor":
            factor = node(
                payload, stable_id("EmissionFactor", "factor-source:material")
            )["props"]
            factor["factorValue"] = True
            factor["normalizedFactorValue"] = True
        elif case == "unit_mismatch":
            node(payload, "quantity:material")["props"]["quantityUnit"] = "m3"
            node(payload, "emission:material")["props"]["quantityUnit"] = "m3"
        elif case == "scope_mismatch":
            node(payload, "quantity:material")["props"]["requestedScope"] = "A4"
        elif case == "valid_zero_inconsistent":
            for node_id in (
                "consumption:zero",
                "quantity:zero",
                "emission:zero",
            ):
                node(payload, node_id)["props"]["isValidZero"] = False
        elif case == "energy_mode_missing":
            del node(payload, "consumption:direct")["props"]["attributionMode"]
        elif case == "direct_allocation_field":
            edge = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:direct"
                and edge["type"] == "recordedForObject"
            )
            edge["props"]["normalizedWeight"] = 1.0
        elif case == "direct_duplicate_target":
            add_edge(
                payload,
                "consumption:direct",
                "recordedForObject",
                COMPONENT_2,
                {
                    "sourceRecordId": "source:direct",
                    "evidenceSourceId": "evidence:direct",
                },
                "occ:direct:recordedForObject:duplicate",
            )
        elif case == "allocated_missing_field":
            edge = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:allocated"
                and edge["type"] == "recordedForObject"
            )
            del edge["props"]["evidenceRecordId"]
        elif case == "allocation_not_conserved":
            edge = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:allocated"
                and edge["type"] == "recordedForObject"
                and edge["tgt"] == COMPONENT_1
            )
            edge["props"]["normalizedWeight"] = 0.5
        elif case == "allocated_semantic_duplicate":
            original = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:allocated"
                and edge["type"] == "recordedForObject"
                and edge["tgt"] == COMPONENT_1
            )
            add_edge(
                payload,
                original["src"],
                original["type"],
                original["tgt"],
                deepcopy(original["props"]),
                "occ:allocated:recordedForObject:semantic-duplicate",
            )
        elif case == "process_context_missing":
            payload["edges"][:] = [
                edge
                for edge in payload["edges"]
                if not (
                    edge["src"] == "consumption:process"
                    and edge["type"]
                    in {"associatedWithProcess", "recordedForResource"}
                )
            ]
        elif case == "process_has_product_path":
            add_edge(
                payload,
                "consumption:process",
                "recordedForObject",
                COMPONENT_1,
                {
                    "sourceRecordId": "source:process",
                    "evidenceSourceId": "evidence:process",
                },
                "occ:process:recordedForObject:forbidden",
            )
        elif case == "material_energy_property":
            node(payload, "consumption:material")["props"][
                "energyCarrierId"
            ] = CARRIER_ELECTRICITY
        elif case in {
            "provenance_noncanonical",
            "provenance_duplicate_key",
            "provenance_nested_mapping",
        }:
            props = node(payload, "quantity:direct")["props"]
            parsed = json.loads(props["orderedRawOperands"][0])
            if case == "provenance_noncanonical":
                props["orderedRawOperands"][0] = json.dumps(
                    parsed, ensure_ascii=False, sort_keys=True
                )
            elif case == "provenance_duplicate_key":
                canonical = props["orderedRawOperands"][0]
                marker = '"operand_id":"operand:direct"'
                props["orderedRawOperands"][0] = canonical.replace(
                    marker, marker + ',"operand_id":"duplicate"', 1
                )
            else:
                parsed["role"] = {"legacy": "nested"}
                props["orderedRawOperands"][0] = _canonical_json(parsed)
        elif case == "design_quantity_mismatch":
            props = node(payload, "quantity:material")["props"]
            operand = json.loads(props["orderedRawOperands"][0])
            conversion = json.loads(props["conversionSteps"][0])
            operand["design_quantity_id"] = "dq:unused"
            operand["source_id"] = "dq:unused"
            conversion["design_quantity_id"] = "dq:unused"
            conversion["source_id"] = "dq:unused"
            props["orderedRawOperands"][0] = _canonical_json(operand)
            props["conversionSteps"][0] = _canonical_json(conversion)

    _rewrite_graph(
        release_dir,
        mutate,
        reconcile_counts=edge_count_changes,
        rebuild_cypher=True,
    )
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(
    "case",
    (
        "hash_mismatch",
        "size_mismatch",
        "cypher_rehashed_but_wrong",
        "stats_rehashed_but_stale",
        "alignment_gate_diverges",
        "alignment_total_stale",
        "validation_header",
        "validation_noncanonical_evidence",
        "rejected_row_leaks",
        "accepted_row_missing",
    ),
)
def test_rehashed_artifact_and_validation_tampering_is_rejected(
    release_dir: Path, case: str
) -> None:
    manifest = _read_manifest(release_dir)
    if case == "hash_mismatch":
        path = release_dir / OUTPUTS["cypher"]
        path.write_bytes(path.read_bytes() + b"\n")
        _write_manifest(release_dir, manifest)
    elif case == "size_mismatch":
        manifest["outputs"]["graphJson"]["sizeBytes"] += 1
        _write_manifest(release_dir, manifest)
    elif case == "cypher_rehashed_but_wrong":
        path = release_dir / OUTPUTS["cypher"]
        path.write_text(
            path.read_text(encoding="utf-8") + "\n// stale", encoding="utf-8"
        )
        _refresh_outputs(release_dir, manifest)
        _write_manifest(release_dir, manifest)
    elif case == "stats_rehashed_but_stale":
        path = release_dir / OUTPUTS["stats"]
        stats = json.loads(path.read_text(encoding="utf-8"))
        stats["nodeCount"] += 1
        _write_json(path, stats)
        _refresh_outputs(release_dir, manifest)
        _write_manifest(release_dir, manifest)
    elif case in {"alignment_gate_diverges", "alignment_total_stale"}:
        path = release_dir / OUTPUTS["alignmentReport"]
        report = json.loads(path.read_text(encoding="utf-8"))
        if case == "alignment_gate_diverges":
            report["gates"]["liveCypherRoundTrip"] = "pass"
        else:
            report["projectionTotals"]["product"] = 999.0
        _write_json(path, report)
        _refresh_outputs(release_dir, manifest)
        _write_manifest(release_dir, manifest)
    elif case == "validation_header":
        path = release_dir / OUTPUTS["validation"]
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("recordId", "legacyId", 1), encoding="utf-8", newline="")
        _refresh_outputs(release_dir, manifest)
        _write_manifest(release_dir, manifest)
    else:
        rows = _read_validation_rows(release_dir)
        if case == "validation_noncanonical_evidence":
            row = next(row for row in rows if row["status"] == "accepted")
            row["evidenceJson"] = json.dumps(
                json.loads(row["evidenceJson"]), ensure_ascii=False, sort_keys=True
            )
        elif case == "rejected_row_leaks":
            row = next(row for row in rows if row["status"] == "rejected")
            row["recordId"] = "record:material"
        else:
            rows[:] = [
                row
                for row in rows
                if row["recordId"] != "record:direct"
            ]
        _rewrite_validation_rows(release_dir, rows)
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


def test_manifest_json_itself_rejects_duplicate_keys(release_dir: Path) -> None:
    path = release_dir / "case_version_manifest.json"
    text = path.read_text(encoding="utf-8")
    marker = f'"schemaVersion": "{SCHEMA_VERSION}"'
    path.write_text(
        text.replace(marker, f'{marker},\n  {marker}', 1), encoding="utf-8"
    )
    with pytest.raises(CanonicalSchemaError, match="duplicate"):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(
    "case",
    (
        "missing_generated_from",
        "duplicate_generated_from",
        "missing_quantity",
        "duplicate_quantity",
        "material_missing_target",
        "material_duplicate_target",
        "energy_missing_carrier",
        "allocation_raw_negative",
        "allocation_normalized_negative",
        "allocation_normalized_over_one",
        "allocation_normalized_boolean",
        "allocation_set_mismatch",
        "allocation_basis_mismatch",
        "process_context_evidence_mismatch",
        "provenance_nonobject",
        "provenance_nan",
        "provenance_duplicate_operand",
        "provenance_duplicate_source",
    ),
)
def test_cardinality_allocation_numeric_and_provenance_identity_failures(
    release_dir: Path, case: str
) -> None:
    edge_count_changes = case in {
        "missing_generated_from",
        "duplicate_generated_from",
        "missing_quantity",
        "duplicate_quantity",
        "material_missing_target",
        "material_duplicate_target",
        "energy_missing_carrier",
    }

    def node(payload, node_id):
        return next(item for item in payload["nodes"] if item["id"] == node_id)

    def append_edge(payload, original, *, target=None, occurrence):
        duplicate = deepcopy(original)
        duplicate["tgt"] = target or duplicate["tgt"]
        duplicate["occurrenceId"] = occurrence
        duplicate["id"] = stable_edge_id(
            duplicate["type"], duplicate["src"], duplicate["tgt"], occurrence
        )
        payload["edges"].append(duplicate)

    def allocated_edge(payload):
        return next(
            edge
            for edge in payload["edges"]
            if edge["src"] == "consumption:allocated"
            and edge["type"] == "recordedForObject"
            and edge["tgt"] == COMPONENT_1
        )

    def mutate(payload):
        if case in {
            "missing_generated_from",
            "missing_quantity",
            "material_missing_target",
            "energy_missing_carrier",
        }:
            targets = {
                "missing_generated_from": ("emission:direct", "hasCarbonDriver"),
                "missing_quantity": ("consumption:direct", "hasQuantity"),
                "material_missing_target": (
                    "consumption:material",
                    "recordedForObject",
                ),
                "energy_missing_carrier": ("consumption:direct", "ofCarrier"),
            }
            source, relation = targets[case]
            payload["edges"][:] = [
                edge
                for edge in payload["edges"]
                if not (edge["src"] == source and edge["type"] == relation)
            ]
        elif case == "duplicate_generated_from":
            original = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "emission:allocated"
                and edge["type"] == "hasCarbonDriver"
            )
            append_edge(
                payload,
                original,
                target="consumption:direct",
                occurrence="occ:duplicate:hasCarbonDriver",
            )
        elif case == "duplicate_quantity":
            original = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:direct"
                and edge["type"] == "hasQuantity"
            )
            append_edge(
                payload,
                original,
                target="quantity:allocated",
                occurrence="occ:duplicate:hasQuantity",
            )
        elif case == "material_duplicate_target":
            original = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:material"
                and edge["type"] == "recordedForObject"
            )
            append_edge(
                payload,
                original,
                occurrence="occ:material:recordedForObject:duplicate",
            )
        elif case.startswith("allocation_"):
            props = allocated_edge(payload)["props"]
            if case == "allocation_raw_negative":
                props["rawWeight"] = -0.1
            elif case == "allocation_normalized_negative":
                props["normalizedWeight"] = -0.1
            elif case == "allocation_normalized_over_one":
                props["normalizedWeight"] = 1.1
            elif case == "allocation_normalized_boolean":
                props["normalizedWeight"] = True
            elif case == "allocation_set_mismatch":
                props["allocationSetId"] = "allocation:set:other"
            elif case == "allocation_basis_mismatch":
                props["allocationBasis"] = "energy"
        elif case == "process_context_evidence_mismatch":
            edge = next(
                edge
                for edge in payload["edges"]
                if edge["src"] == "consumption:process"
                and edge["type"] == "associatedWithProcess"
            )
            edge["props"]["evidenceSourceId"] = "evidence:wrong"
        else:
            props = node(payload, "quantity:direct")["props"]
            original_operand = props["orderedRawOperands"][0]
            original_conversion = props["conversionSteps"][0]
            if case == "provenance_nonobject":
                props["orderedRawOperands"][0] = "[]"
            elif case == "provenance_nan":
                props["orderedRawOperands"][0] = original_operand.replace(
                    '"value":6.0', '"value":NaN'
                )
            else:
                operand = json.loads(original_operand)
                conversion = json.loads(original_conversion)
                if case == "provenance_duplicate_source":
                    operand["operand_id"] = "operand:direct:second"
                    conversion["operand_id"] = "operand:direct:second"
                props["orderedRawOperands"].append(_canonical_json(operand))
                props["conversionSteps"].append(_canonical_json(conversion))

    _rewrite_graph(
        release_dir,
        mutate,
        reconcile_counts=edge_count_changes,
        rebuild_cypher=True,
    )
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


def test_public_records_preserve_exact_fact_and_contribution_semantics(
    release_dir: Path,
) -> None:
    context = load_canonical_v2_context(release_dir)
    facts = {fact.emission_id: fact for fact in context.emissions}
    material = facts["emission:material"]
    assert (
        material.kind,
        material.mode,
        material.quantity_value,
        material.quantity_unit,
        material.factor_value,
        material.factor_denominator,
        material.emission_value,
        material.emission_unit,
    ) == ("material", "material", 20.0, "kg", 0.5, "kg", 10.0, "kgCO2e")
    assert material.material_id == MATERIAL
    assert material.carrier_id is None
    assert material.design_quantity_ids == ("dq:material",)
    assert material.factor_keyword == "steel"
    assert material.factor_source == "controlled-fixture"
    assert material.ordered_raw_operands[0]["operand_id"] == "operand:material"

    process = facts["emission:process"]
    assert process.mode == "process_only"
    assert process.process_ids == (PROCESS_STAGE,)
    assert process.resource_ids == (RESOURCE,)
    assert process.emission_value == pytest.approx(4.0)
    zero = facts["emission:zero"]
    assert zero.is_valid_zero is True
    assert zero.quantity_value == zero.emission_value == 0.0

    allocated = next(
        item
        for item in context.product_contributions
        if item.key
        == ("allocated", "emission:allocated", "allocation:set:1", COMPONENT_1)
    )
    assert (
        allocated.mode,
        allocated.source_emission_value,
        allocated.projected_value,
        allocated.allocation_set_id,
        allocated.allocation_basis,
        allocated.raw_weight,
        allocated.raw_weight_unit,
        allocated.normalized_weight,
        allocated.evidence_record_id,
    ) == (
        "allocated",
        20.0,
        5.0,
        "allocation:set:1",
        "mass",
        0.25,
        "kg",
        0.25,
        "allocation:evidence:c1",
    )
    assert not any(
        item.emission_id == "emission:process"
        for item in context.product_contributions
    )
    assert not hasattr(material, "__dict__")
    assert not hasattr(allocated, "__dict__")
    assert not hasattr(context, "__dict__")


def test_evidence_role_allowlist_has_exact_deterministic_order(release_dir: Path) -> None:
    context = load_canonical_v2_context(release_dir)
    material_factor = stable_id("EmissionFactor", "factor-source:material")
    assert evidence_ids(context, "emission:material") == (
        "emission:material",
        "consumption:material",
        "quantity:material",
        material_factor,
        MATERIAL,
        "dq:material",
        "source:material",
        "evidence:material",
    )
    process_factor = stable_id("EmissionFactor", "factor-source:gas")
    assert evidence_ids(context, "emission:process") == (
        "emission:process",
        "consumption:process",
        "quantity:process",
        process_factor,
        CARRIER_GAS,
        PROCESS_STAGE,
        RESOURCE,
        "source:process",
        "evidence:process",
    )
    allocated_key = (
        "allocated",
        "emission:allocated",
        "allocation:set:1",
        COMPONENT_1,
    )
    allocated_base = evidence_ids(context, "emission:allocated")
    assert evidence_ids(context, "emission:allocated", allocated_key) == (
        *allocated_base,
        COMPONENT_1,
        "occ:allocated:recordedForObject:component:c1:allocation:evidence:c1",
        "allocation:evidence:c1",
    )
    direct_key = ("direct", "emission:direct")
    assert evidence_ids(context, "emission:direct", direct_key)[-2:] == (
        COMPONENT_1,
        "occ:direct:recordedForObject:component:c1:",
    )


def test_all_dimensions_and_private_indices_are_deeply_read_only(
    release_dir: Path,
) -> None:
    context = load_canonical_v2_context(release_dir)
    assert dimension_ids(context, "module") == (MODULE,)
    assert dimension_ids(context, "component_type") == (
        "type:structural",
        "type:wall",
    )
    assert dimension_ids(context, "ifc_class") == (
        "IfcBeam",
        "IfcColumn",
        "IfcWall",
    )
    assert dimension_ids(context, "material") == (MATERIAL, "material:unused")
    assert dimension_ids(context, "carrier") == (
        CARRIER_ELECTRICITY,
        CARRIER_GAS,
    )
    assert dimension_ids(context, "resource") == (RESOURCE,)
    first_occurrence = next(iter(context._edges_by_occurrence))
    with pytest.raises(TypeError):
        context._nodes_by_id[COMPONENT_1]["props"]["globalId"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        context._edges_by_occurrence[first_occurrence]["props"]["x"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        context._dimension_ids["component"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        context.manifest["outputs"]["graphJson"]["path"] = "changed"  # type: ignore[index]


def test_known_empty_dimensions_return_zero_and_unknown_requests_raise(
    release_dir: Path,
) -> None:
    context = load_canonical_v2_context(release_dir)
    assert product_total(context, material_id="material:unused") == 0.0
    assert product_total(context, requested_scope="A4") == 0.0
    assert source_process_total(context, requested_scope="A4") == 0.0
    assert tuple(iter_emissions(context, requested_scope="A4")) == ()
    with pytest.raises(CanonicalSchemaError):
        product_total(context, module_id="module:unknown")
    with pytest.raises(CanonicalSchemaError):
        product_total(context, material_id="material:unknown")
    with pytest.raises(CanonicalSchemaError):
        product_total(context, carrier_id="carrier:unknown")
    with pytest.raises(CanonicalSchemaError):
        product_total(context, material_id=CARRIER_ELECTRICITY)
    with pytest.raises(CanonicalSchemaError):
        tuple(iter_emissions(context, kind="legacy"))
    with pytest.raises(CanonicalSchemaError):
        dimension_ids(context, "legacy")
    with pytest.raises(CanonicalSchemaError):
        components_for_ifc_class(context, "IfcDoor")
    with pytest.raises(CanonicalSchemaError):
        evidence_ids(context, "emission:unknown")
    with pytest.raises(CanonicalSchemaError):
        evidence_ids(
            context,
            "emission:allocated",
            ("allocated", "emission:allocated", "allocation:set:1", "component:bad"),
        )


def test_renamed_copy_loads_without_external_manifest_sources(
    release_dir: Path, tmp_path: Path
) -> None:
    copied = tmp_path / "renamed-independent-copy"
    shutil.copytree(release_dir, copied)
    original_manifest = _read_manifest(release_dir)
    original_manifest["releaseReady"] = False
    _write_manifest(release_dir, original_manifest)
    context = load_canonical_v2_context(copied)
    assert context.release_dir == copied.resolve()
    assert product_total(context) == pytest.approx(36.0)


def test_valid_original_factor_denominator_conversion_is_accepted(
    release_dir: Path,
) -> None:
    def mutate(payload):
        factor = next(
            node
            for node in payload["nodes"]
            if node["id"] == stable_id("EmissionFactor", "factor-source:material")
        )["props"]
        factor["factorUnit"] = "kgCO2e/g"
        factor["originalFactorUnit"] = "kgCO2e/g"
        factor["originalFactorValue"] = 0.0005
        factor["denominatorUnit"] = "kg"

    _rewrite_graph(release_dir, mutate)
    context = load_canonical_v2_context(release_dir)
    material = next(fact for fact in context.emissions if fact.kind == "material")
    assert material.factor_unit == "kgCO2e/g"
    assert material.factor_denominator == "kg"
    assert material.factor_value == pytest.approx(0.5)
    assert material.emission_value == pytest.approx(10.0)


@pytest.mark.parametrize(
    "case",
    (
        "unknown_release_profile",
        "actual_case_ready",
        "missing_ready_input",
        "input_factory_not_null",
        "configuration_factory_not_null",
    ),
)
def test_ready_manifest_profile_and_input_contract_is_closed(
    release_dir: Path, case: str
) -> None:
    manifest = _read_manifest(release_dir)
    if case == "unknown_release_profile":
        manifest["releaseProfile"] = "legacy"
    elif case == "actual_case_ready":
        manifest["releaseProfile"] = "actual-case"
    elif case == "missing_ready_input":
        manifest["inputs"]["materialEvidence"] = None
    elif case == "input_factory_not_null":
        manifest["inputs"]["factoryInput"] = deepcopy(
            manifest["inputs"]["caseConfig"]
        )
    else:
        manifest["configuration"]["factoryInput"] = "factory.csv"
    _write_manifest(release_dir, manifest)
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_context(release_dir)


def test_canonical_cypher_export_is_byte_deterministic_lf(tmp_path: Path) -> None:
    graph = CanonicalLPGGraph()
    graph.add_node("module:lf", ["ModularUnit"], {"name": "LF fixture"})
    path = tmp_path / "graph.cypher"
    graph.export_cypher(path)
    payload = path.read_bytes()
    assert payload == graph.to_cypher().encode("utf-8")
    assert b"\r" not in payload


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("role", "energy_quantity"),
        ("source_id", "evidence:wrong"),
        ("source_value", 7.0),
        ("normalized_value", 7.0),
        ("conversion_factor", 2.0),
    ),
)
def test_final_calculated_quantity_conversion_is_semantically_closed(
    release_dir: Path, field: str, value: object
) -> None:
    def mutate(payload):
        quantity = next(
            node for node in payload["nodes"] if node["id"] == "quantity:direct"
        )["props"]
        conversion = json.loads(quantity["conversionSteps"][-1])
        conversion[field] = value
        quantity["conversionSteps"][-1] = _canonical_json(conversion)

    _rewrite_graph(release_dir, mutate)
    with pytest.raises(CanonicalSchemaError, match="conversion|provenance"):
        load_canonical_v2_context(release_dir)


def test_rejected_reason_specific_evidence_extensions_are_closed(
    release_dir: Path,
) -> None:
    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    rejected["reasonCode"] = "evidence_ownership_mismatch"
    rejected["formulaCode"] = ""
    evidence = json.loads(rejected["evidenceJson"])
    evidence.pop("formulaCode")
    evidence.update(
        {
            "associationId": "association:missing",
            "componentId": COMPONENT_1,
            "materialId": MATERIAL,
        }
    )
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    load_canonical_v2_context(release_dir)

    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    evidence = json.loads(rejected["evidenceJson"])
    evidence["legacyPayload"] = "not canonical"
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    with pytest.raises(CanonicalSchemaError, match="evidence"):
        load_canonical_v2_context(release_dir)


def test_allocation_basis_missing_is_a_closed_energy_rejection(
    release_dir: Path,
) -> None:
    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    assert rejected["kind"] == "energy"
    assert not any(
        rejected[field]
        for field in ("consumptionId", "quantityId", "factorId", "emissionId")
    )
    rejected["reasonCode"] = "allocation_basis_missing"
    _rewrite_validation_rows(release_dir, rows)
    load_canonical_v2_context(release_dir)

    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    evidence = json.loads(rejected["evidenceJson"])
    evidence["allocationSetId"] = "allocation:unexpected"
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    with pytest.raises(CanonicalSchemaError, match="evidence"):
        load_canonical_v2_context(release_dir)


def test_rejected_target_context_is_closed_typed_and_non_materialized(
    release_dir: Path,
) -> None:
    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    evidence = json.loads(rejected["evidenceJson"])
    evidence.update(
        {
            "targetComponentId": "component:c4",
            "energyCarrierId": "carrier:electricity",
            "processIds": ["process:stage"],
            "recordedScope": "A1–A3",
            "requestedScope": " a1 - a3 ",
        }
    )
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    context = load_canonical_v2_context(release_dir)
    typed = json.loads(
        next(row for row in context.validation_rows if row["status"] == "rejected")[
            "evidenceJson"
        ]
    )
    assert typed["targetComponentId"] == "component:c4"
    assert all(fact.record_id != rejected["recordId"] for fact in context.emissions)

    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    evidence = json.loads(rejected["evidenceJson"])
    evidence["energyCarrierID"] = evidence.pop("energyCarrierId")
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    with pytest.raises(CanonicalSchemaError, match="evidence"):
        load_canonical_v2_context(release_dir)


def test_missing_source_rejection_preserves_empty_source_without_forgery(
    release_dir: Path,
) -> None:
    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    evidence = json.loads(rejected["evidenceJson"])
    rejected["reasonCode"] = "source_record_id_missing"
    rejected["sourceRecordId"] = ""
    rejected["sourceIdentity"] = stable_id(
        "ValidationRecord", rejected["evidenceSourceId"], rejected["recordId"]
    )
    evidence["sourceRecordId"] = ""
    evidence["sourceIdentity"] = rejected["sourceIdentity"]
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)
    context = load_canonical_v2_context(release_dir)
    loaded = next(row for row in context.validation_rows if row["status"] == "rejected")
    assert loaded["sourceRecordId"] == ""
    assert loaded["sourceIdentity"] == stable_id(
        "ValidationRecord", loaded["evidenceSourceId"], loaded["recordId"]
    )

    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    rejected["reasonCode"] = "factor_not_found"
    _rewrite_validation_rows(release_dir, rows)
    with pytest.raises(CanonicalSchemaError, match="sourceRecordId|source"):
        load_canonical_v2_context(release_dir)


def test_forged_context_is_rejected_by_every_public_projection(release_dir: Path) -> None:
    context = load_canonical_v2_context(release_dir)
    forged = replace(context, emissions=())
    with pytest.raises(CanonicalSchemaError, match="context"):
        tuple(iter_emissions(forged))
    with pytest.raises(CanonicalSchemaError, match="context"):
        product_total(forged)


def test_rehashed_cypher_with_carriage_returns_is_rejected(release_dir: Path) -> None:
    path = release_dir / OUTPUTS["cypher"]
    _write_raw_output(release_dir, "cypher", path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(CanonicalSchemaError, match="line ending"):
        load_canonical_v2_context(release_dir)


@pytest.mark.parametrize(("field", "value"), (("keyword", True), ("source", 1)))
def test_optional_factor_classification_fields_are_typed(
    release_dir: Path, field: str, value: object
) -> None:
    def mutate(payload):
        factor = next(
            node
            for node in payload["nodes"]
            if node["id"] == stable_id("EmissionFactor", "factor-source:material")
        )
        factor["props"][field] = value

    _rewrite_graph(release_dir, mutate)
    with pytest.raises(CanonicalSchemaError, match=field):
        load_canonical_v2_context(release_dir)


def test_allocated_consumption_rejects_edge_only_weight_fields(
    release_dir: Path,
) -> None:
    def mutate(payload):
        consumption = next(
            node
            for node in payload["nodes"]
            if node["id"] == "consumption:allocated"
        )
        consumption["props"]["rawWeight"] = 1.0

    _rewrite_graph(release_dir, mutate)
    with pytest.raises(CanonicalSchemaError, match="allocation|attribution"):
        load_canonical_v2_context(release_dir)


def test_final_quantity_unit_must_exactly_equal_factor_denominator(
    release_dir: Path,
) -> None:
    def mutate(payload):
        next(
            node for node in payload["nodes"] if node["id"] == "quantity:material"
        )["props"]["quantityUnit"] = "g"
        next(
            node for node in payload["nodes"] if node["id"] == "emission:material"
        )["props"]["quantityUnit"] = "g"

    _rewrite_graph(release_dir, mutate)
    with pytest.raises(CanonicalSchemaError, match="unit|denominator"):
        load_canonical_v2_context(release_dir)


def test_accepted_facts_cannot_duplicate_one_source_record(release_dir: Path) -> None:
    duplicate_source_record = "source:direct"
    duplicate_evidence_source = "evidence:direct"
    duplicate_identity = _source_identity(
        duplicate_evidence_source, duplicate_source_record
    )

    def mutate(payload):
        for node_id in (
            "consumption:process",
            "quantity:process",
            "emission:process",
        ):
            props = next(
                node for node in payload["nodes"] if node["id"] == node_id
            )["props"]
            props["sourceIdentity"] = duplicate_identity
            props["sourceRecordId"] = duplicate_source_record
            props["evidenceSourceId"] = duplicate_evidence_source
        quantity_props = next(
            node
            for node in payload["nodes"]
            if node["id"] == "quantity:process"
        )["props"]
        final_conversion = json.loads(quantity_props["conversionSteps"][-1])
        final_conversion["operand_id"] = stable_id(
            "CalculatedQuantityOperand",
            duplicate_evidence_source,
            "record:process",
        )
        final_conversion["source_id"] = duplicate_evidence_source
        quantity_props["conversionSteps"][-1] = _canonical_json(final_conversion)
        for edge in payload["edges"]:
            if edge["src"] == "consumption:process" and edge["type"] in {
                "associatedWithProcess",
                "recordedForResource",
            }:
                edge["props"] = {
                    "sourceRecordId": duplicate_source_record,
                    "evidenceSourceId": duplicate_evidence_source,
                }

    _rewrite_graph(release_dir, mutate)
    rows = _read_validation_rows(release_dir)
    process_row = next(row for row in rows if row["recordId"] == "record:process")
    process_row["sourceIdentity"] = duplicate_identity
    process_row["sourceRecordId"] = duplicate_source_record
    process_row["evidenceSourceId"] = duplicate_evidence_source
    evidence = json.loads(process_row["evidenceJson"])
    evidence["sourceRecordId"] = duplicate_source_record
    process_row["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)

    with pytest.raises(CanonicalSchemaError, match="source"):
        load_canonical_v2_context(release_dir)


def test_rejected_row_may_share_an_accepted_source_without_materializing(
    release_dir: Path,
) -> None:
    rows = _read_validation_rows(release_dir)
    rejected = next(row for row in rows if row["status"] == "rejected")
    rejected["sourceIdentity"] = _source_identity("evidence:direct", "source:direct")
    rejected["sourceRecordId"] = "source:direct"
    rejected["evidenceSourceId"] = "evidence:direct"
    evidence = json.loads(rejected["evidenceJson"])
    evidence["sourceIdentity"] = rejected["sourceIdentity"]
    evidence["sourceRecordId"] = rejected["sourceRecordId"]
    evidence["evidenceSourceId"] = rejected["evidenceSourceId"]
    rejected["evidenceJson"] = _canonical_json(evidence)
    _rewrite_validation_rows(release_dir, rows)

    context = load_canonical_v2_context(release_dir)
    assert len(context.emissions) == 5
    assert any(
        row["recordId"] == "record:rejected" and row["status"] == "rejected"
        for row in context.validation_rows
    )


def test_public_validated_graph_document_is_deterministic_and_read_only(
    tmp_path: Path,
) -> None:
    release = write_release(tmp_path / "release")
    graph_path = release / OUTPUTS["graphJson"]

    document = canonical_reader.load_canonical_v2_graph_document(graph_path)
    nodes = tuple(canonical_reader.iter_graph_nodes(document))
    edges = tuple(canonical_reader.iter_graph_edges(document))

    assert document.schema_version == SCHEMA_VERSION
    assert tuple(node["id"] for node in nodes) == tuple(
        sorted(node["id"] for node in nodes)
    )
    assert edges == tuple(
        sorted(
            edges,
            key=lambda edge: (
                edge["src"],
                edge["type"],
                edge["tgt"],
                edge["occurrenceId"],
                edge["id"],
            ),
        )
    )
    with pytest.raises(TypeError):
        nodes[0]["props"]["forbiddenMutation"] = True

    context = load_canonical_v2_context(release)
    from_context = canonical_reader.graph_document_from_context(context)
    assert tuple(canonical_reader.iter_graph_nodes(from_context)) == nodes
    assert tuple(canonical_reader.iter_graph_edges(from_context)) == edges


@pytest.mark.parametrize("mutation", ["legacy_schema", "invalid_typed_triple"])
def test_public_graph_document_loader_rejects_noncanonical_graphs(
    tmp_path: Path, mutation: str
) -> None:
    release = write_release(tmp_path / "release")
    payload = json.loads(
        (release / OUTPUTS["graphJson"]).read_text(encoding="utf-8")
    )
    if mutation == "legacy_schema":
        payload["schemaVersion"] = "legacy-v1"
    else:
        edge = next(
            row for row in payload["edges"] if row["type"] == "containsComponent"
        )
        edge["tgt"] = MATERIAL
    graph_path = tmp_path / f"{mutation}.json"
    _write_json(graph_path, payload)

    with pytest.raises(CanonicalSchemaError):
        canonical_reader.load_canonical_v2_graph_document(graph_path)


@pytest.mark.parametrize(
    "excluded_ifc_type",
    [
        "IfcFurniture",
        "IfcAlarm",
        "IfcFireSuppressionTerminal",
        "IfcAirTerminal",
        "IfcValve",
    ],
)
def test_standalone_graph_document_rejects_excluded_factory_component_refinements(
    tmp_path: Path, excluded_ifc_type: str
) -> None:
    release = write_release(tmp_path / "release")
    payload = json.loads(
        (release / OUTPUTS["graphJson"]).read_text(encoding="utf-8")
    )
    component = next(
        node for node in payload["nodes"] if "BuildingComponent" in node["labels"]
    )
    component["labels"] = sorted(
        [label for label in component["labels"] if not label.startswith("Ifc")]
        + [excluded_ifc_type]
    )
    graph_path = tmp_path / f"excluded-{excluded_ifc_type}.json"
    _write_json(graph_path, payload)

    with pytest.raises(CanonicalSchemaError, match="excluded factory"):
        canonical_reader.load_canonical_v2_graph_document(graph_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "bogus_attribution_mode",
        "nonconserving_allocation",
        "quantity_factor_mismatch",
        "invalid_zero_evidence",
    ],
)
def test_standalone_graph_document_rejects_graph_contained_accounting_mutations(
    tmp_path: Path, mutation: str
) -> None:
    release = write_release(tmp_path / "release")
    payload = json.loads(
        (release / OUTPUTS["graphJson"]).read_text(encoding="utf-8")
    )
    if mutation == "bogus_attribution_mode":
        next(
            node
            for node in payload["nodes"]
            if node["id"] == "consumption:direct"
        )["props"]["attributionMode"] = "bogus"
    elif mutation == "nonconserving_allocation":
        next(
            edge
            for edge in payload["edges"]
            if edge["src"] == "consumption:allocated"
            and edge["type"] == "recordedForObject"
            and edge["tgt"] == COMPONENT_1
        )["props"]["normalizedWeight"] = 9.0
    elif mutation == "quantity_factor_mismatch":
        next(
            node
            for node in payload["nodes"]
            if node["id"] == "emission:direct"
        )["props"]["emissionValue"] = 999.0
    else:
        for node_id in (
            "consumption:zero",
            "quantity:zero",
            "emission:zero",
        ):
            next(
                node for node in payload["nodes"] if node["id"] == node_id
            )["props"]["isValidZero"] = False
    graph_path = tmp_path / f"accounting-{mutation}.json"
    _write_json(graph_path, payload)

    with pytest.raises(CanonicalSchemaError):
        canonical_reader.load_canonical_v2_graph_document(graph_path)


def test_standalone_graph_document_allows_a_canonical_backbone_without_facts(
    tmp_path: Path,
) -> None:
    graph = CanonicalLPGGraph()
    _add_backbone(graph)
    graph_path = tmp_path / "canonical-backbone-only.json"
    _write_json(graph_path, graph.to_payload())

    document = canonical_reader.load_canonical_v2_graph_document(graph_path)

    assert document.schema_version == SCHEMA_VERSION
    assert not any(
        "CarbonEmission" in node["labels"]
        for node in canonical_reader.iter_graph_nodes(document)
    )
