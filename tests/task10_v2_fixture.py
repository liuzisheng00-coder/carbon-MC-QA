"""Shared file-backed canonical-v2 fixture for Task 10 consumer tests."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from dm2c_m23_canonical import CanonicalLPGGraph
from tests.test_dm2c_canonical_v2_reader import (
    APPLICATION_CLASSES,
    CARRIER_ELECTRICITY,
    CARRIER_GAS,
    CODE_FILES,
    COMPONENT_1,
    COMPONENT_2,
    COMPONENT_3,
    MATERIAL,
    MODULE,
    OPTIONAL_CONTEXT_PREDICATES,
    OUTPUTS,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    VALIDATION_FIELDS,
    _add_backbone,
    _add_fact,
    _canonical_json,
    _descriptor,
    _source_identity,
    _validation_counts,
    _write_json,
)

COMPONENT_4 = "component:c4"
MATERIAL_TIMBER = "material:unused"


def write_task10_release(
    root: Path,
    *,
    reverse_graph_order: bool = False,
    ambiguous_material_names: bool = True,
    named_carriers: bool = False,
) -> Path:
    """Generate the controlled non-actual release only through canonical artifacts."""
    root.mkdir()
    graph = CanonicalLPGGraph()
    _add_backbone(graph)
    # Two components also share an exact queryable name so component
    # ResolveEntities exercises set versus singleton cardinality.
    del graph.nodes[COMPONENT_1]
    del graph.nodes[COMPONENT_2]
    graph.add_node(
        COMPONENT_1,
        ["BuildingComponent", "IfcBeam"],
        {"name": "Structural member", "globalId": "C1", "ifcClass": "IfcBeam"},
    )
    graph.add_node(
        COMPONENT_2,
        ["BuildingComponent", "IfcColumn"],
        {"name": "Structural member", "globalId": "C2", "ifcClass": "IfcColumn"},
    )
    # Two canonical IfcMaterial identities deliberately share one exact name so
    # set versus singleton property resolution is exercised without fuzzy keys.
    del graph.nodes[MATERIAL]
    del graph.nodes[MATERIAL_TIMBER]
    graph.add_node(MATERIAL, ["IfcMaterial"], {"name": "Structural material"})
    graph.add_node(
        MATERIAL_TIMBER,
        ["IfcMaterial"],
        {
            "name": (
                "Structural material" if ambiguous_material_names else "Timber"
            )
        },
    )
    graph.add_node(
        COMPONENT_4,
        ["BuildingComponent", "IfcSlab"],
        {"globalId": "C4", "ifcClass": "IfcSlab"},
    )
    graph.add_node("dq:c2", ["DesignQuantity", "IfcQuantityWeight"], {"value": 6.0, "unit": "kg"})
    graph.add_node("dq:c4", ["DesignQuantity", "IfcQuantityWeight"], {"value": 1.0, "unit": "kg"})

    def edge(src: str, relation: str, tgt: str, *, occurrence: str) -> None:
        graph.add_edge(src, relation, tgt, occurrence_id=occurrence)

    edge(MODULE, "containsComponent", COMPONENT_4, occurrence="occ:module:c4")
    edge(COMPONENT_4, "hasComponentType", "type:structural", occurrence="occ:type:c4")
    edge(COMPONENT_2, "hasDesignQuantity", "dq:c2", occurrence="occ:dq:c2")
    edge(COMPONENT_4, "hasDesignQuantity", "dq:c4", occurrence="occ:dq:c4")
    edge(COMPONENT_2, "hasMaterial", MATERIAL, occurrence="source:material-c2")
    edge(COMPONENT_3, "hasMaterial", MATERIAL_TIMBER, occurrence="source:material-c3")
    edge(COMPONENT_4, "hasMaterial", MATERIAL_TIMBER, occurrence="source:material-c4")

    rows = [
        _add_fact(
            graph,
            name="material",
            kind="material",
            mode="material",
            quantity_value=10.0,
            quantity_unit="kg",
            factor_source_id="factor-source:material-c1",
            factor_keyword="steel",
            emission_value=10.0,
            component_id=COMPONENT_1,
            material_id=MATERIAL,
        ),
        _add_fact(
            graph,
            name="material-c2",
            kind="material",
            mode="material",
            quantity_value=6.0,
            quantity_unit="kg",
            factor_source_id="factor-source:material-c2",
            factor_keyword="steel",
            emission_value=6.0,
            component_id=COMPONENT_2,
            material_id=MATERIAL,
            design_quantity_id="dq:c2",
        ),
        _add_fact(
            graph,
            name="material-c3",
            kind="material",
            mode="material",
            quantity_value=2.0,
            quantity_unit="kg",
            factor_source_id="factor-source:material-c3",
            factor_keyword="timber",
            emission_value=2.0,
            component_id=COMPONENT_3,
            material_id=MATERIAL_TIMBER,
            design_quantity_id="dq:unused",
        ),
        _add_fact(
            graph,
            name="material-c4",
            kind="material",
            mode="material",
            quantity_value=1.0,
            quantity_unit="kg",
            factor_source_id="factor-source:material-c4",
            factor_keyword="timber",
            emission_value=1.0,
            component_id=COMPONENT_4,
            material_id=MATERIAL_TIMBER,
            design_quantity_id="dq:c4",
        ),
        _add_fact(
            graph,
            name="direct",
            kind="energy",
            mode="direct",
            quantity_value=4.0,
            quantity_unit="kWh",
            factor_source_id="factor-source:electricity-direct",
            factor_keyword="electricity",
            emission_value=4.0,
            component_id=COMPONENT_3,
            carrier_id=CARRIER_ELECTRICITY,
        ),
        _add_fact(
            graph,
            name="zero",
            kind="energy",
            mode="direct",
            quantity_value=0.0,
            quantity_unit="kWh",
            factor_source_id="factor-source:electricity-zero",
            factor_keyword="electricity",
            emission_value=0.0,
            component_id=COMPONENT_4,
            carrier_id=CARRIER_ELECTRICITY,
            valid_zero=True,
        ),
        _add_fact(
            graph,
            name="allocated",
            kind="energy",
            mode="allocated",
            quantity_value=20.0,
            quantity_unit="kWh",
            factor_source_id="factor-source:electricity-allocated",
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
            quantity_value=7.0,
            quantity_unit="m3",
            factor_source_id="factor-source:gas-process",
            factor_keyword="natural gas",
            emission_value=7.0,
            carrier_id=CARRIER_GAS,
            process_only=True,
        ),
    ]
    if named_carriers:
        graph.add_node(CARRIER_ELECTRICITY, ["EnergyCarrier"], {"name": "electricity"})
        graph.add_node(CARRIER_GAS, ["EnergyCarrier"], {"name": "natural gas"})
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
                    "energyCarrierId": CARRIER_ELECTRICITY,
                    "formulaCode": "measured_energy",
                    "kind": "energy",
                    "recordedScope": "A1–A3",
                    "requestedScope": " a1 - a3 ",
                    "sourceIdentity": rejected_identity,
                    "sourceRecordId": rejected_source,
                }
            ),
        }
    )
    rows.sort(key=lambda row: (row["recordId"], 0 if row["status"] == "accepted" else 1))
    payload = graph.to_payload()
    if reverse_graph_order:
        payload["nodes"].reverse()
        payload["edges"].reverse()

    labels = {label for node in payload["nodes"] for label in node["labels"]}
    label_counts = {
        label: sum(label in node["labels"] for node in payload["nodes"])
        for label in APPLICATION_CLASSES
    }
    relations = {
        relation: sum(edge_row["type"] == relation for edge_row in payload["edges"])
        for relation in (*PRINCIPAL_PREDICATES, *sorted(OPTIONAL_CONTEXT_PREDICATES))
    }
    stats = {
        "schemaVersion": SCHEMA_VERSION,
        "moduleId": MODULE,
        "nodeCount": len(payload["nodes"]),
        "edgeCount": len(payload["edges"]),
        "applicationClassCounts": label_counts,
        "ifcRefinementCounts": {
            label: sum(label in node["labels"] for node in payload["nodes"])
            for label in sorted(labels)
            if label not in APPLICATION_CLASSES and label.startswith("Ifc")
        },
        "relationCounts": relations,
        "calculationCounts": {"material": 4, "energy": 4, "validZero": 1},
        "attributionCounts": {"direct": 2, "allocated": 1, "process_only": 1},
        "validation": _validation_counts(rows),
    }
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
    _write_json(
        alignment_path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "status": "pass",
            "highSeverityViolationCount": 0,
            "findings": [],
            "gates": gates,
            "projectionTotals": {"product": 43.0, "sourceProcess": 31.0},
            "checkedCounts": {
                "nodes": stats["nodeCount"],
                "edges": stats["edgeCount"],
                "validationRows": len(rows),
            },
        },
    )
    dummy_input = {
        "selectedPath": "controlled-task10-source.dat",
        "resolvedPath": "controlled-task10-source.dat",
        "sha256": "0" * 64,
        "sizeBytes": 0,
    }
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "releaseId": "controlled-task10-v2",
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
            "sourceIdentity": "module-source:task10-fixture",
            "runtimeId": MODULE,
            "name": "Controlled module",
            "identitySource": "controlled-fixture",
        },
        "configuration": {"requestedScope": "A1-A3", "includeOpenings": False, "factoryInput": None},
        "command": {
            "entryPoint": "dm2c_m23_canonical_release.py",
            "caseConfig": "controlled-task10-source.dat",
            "releaseId": "controlled-task10-v2",
        },
        "coverage": stats["validation"],
        "counts": stats,
        "code": {
            name: {"path": name, "sha256": "0" * 64, "sizeBytes": 0}
            for name in CODE_FILES
        },
        "outputs": {key: _descriptor(root / filename) for key, filename in OUTPUTS.items()},
        "gates": gates,
    }
    _write_json(root / "case_version_manifest.json", manifest)
    return root
