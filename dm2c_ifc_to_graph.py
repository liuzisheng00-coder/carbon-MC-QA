#!/usr/bin/env python3
"""
DM2C IFC-derived semantic graph backbone constructor.
Section 3.2 implementation: IFC extraction -> semantic interface -> agent-ready targets.

Modes:
- backbone (default): Section 3.2 backbone only
- legacy_full: archived full KG method for comparison
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DM2C Section 3.2 IFC-to-graph runner")
    parser.add_argument("--ifc", required=True, help="Path to IFC file")
    parser.add_argument("--ontology", required=True, help="Path to ontology TTL file")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument(
        "--mode",
        default="backbone",
        choices=["backbone", "legacy_full"],
        help="Execution mode. backbone builds Section 3.2 graph backbone only. legacy_full keeps archived complete KG behavior.",
    )
    parser.add_argument(
        "--unit-graph-level",
        default="compact",
        choices=["compact", "full"],
        help="Backbone mode only. compact (default) does not materialize ProjectUnit/DerivedUnitElement nodes.",
    )
    parser.add_argument(
        "--quantity-node-mode",
        default="preferred",
        choices=["preferred", "all"],
        help="Backbone mode only. preferred (default) keeps only preferred IfcElementQuantity nodes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ifc_path = Path(args.ifc).expanduser().resolve()
    ontology_path = Path(args.ontology).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not ifc_path.exists():
        raise FileNotFoundError(f"IFC file not found: {ifc_path}")
    if not ontology_path.exists():
        raise FileNotFoundError(f"Ontology file not found: {ontology_path}")

    if args.mode == "backbone":
        from dm2c_backbone_builder import build_and_export_backbone

        result = build_and_export_backbone(
            ifc_path=ifc_path,
            ontology_path=ontology_path,
            out_dir=out_dir,
            unit_graph_level=args.unit_graph_level,
            quantity_node_mode=args.quantity_node_mode,
        )
        stats = result["stats"]

        print("DM2C IFC-to-Graph completed.")
        print("  Mode: backbone")
        print(f"  Unit graph level: {args.unit_graph_level}")
        print(f"  Quantity node mode: {args.quantity_node_mode}")
        print(f"  IFC: {ifc_path}")
        print(f"  Ontology: {ontology_path}")
        print(f"  Output directory: {out_dir}")
        print("Generated files:")
        print(f"  - {result['graph_json']}")
        print(f"  - {result['graph_cypher']}")
        print(f"  - {result['graph_ttl']}")
        print(f"  - {result['mapping_report']}")
        print(f"  - {result['agent_interface_manifest']}")
        print(f"  - {result['competency_queries']}")
        print("Backbone summary:")
        print(
            "  Nodes={nodeCount}, Edges={edgeCount}, BIM={bimNodes}, Interface={interfaceNodes}, Mapping={mappingNodes}, Requirement={requirementNodes}".format(
                **stats
            )
        )
        print(
            "  IFC elements={ifcElements}, BuildingComponent={buildingComponents}, IfcMaterial={ifcMaterials}, UnresolvedMaterial={unresolvedMaterials}, QuantityBasis={quantityBasis}, ProcessTarget={processTargets}, CarbonTarget={carbonTargets}, MappingEvidence={mappingEvidenceNodes}, MaterialCandidate={materialCandidateNodes}, DataRequirement={dataRequirements}".format(
                **stats
            )
        )
        print(
            "  Forbidden nodes in backbone: ProductionTemplate={forbiddenProductionTemplates}, ProductionStage={forbiddenProductionStages}, ManufacturingActivity={forbiddenManufacturingActivities}, ModuleProduction={forbiddenModuleProduction}, Resource={forbiddenResources}, ConsumptionDriver={forbiddenConsumptionDrivers}, ConsumptionQuantity={forbiddenConsumptionQuantities}, EmissionFactor={forbiddenEmissionFactors}, CarbonEmission={forbiddenCarbonEmissions}, CalculationRecord={forbiddenCalculationRecords}".format(
                **stats
            )
        )
        print(f"  Minimal checks passed: {stats['checkPassed']} (issues={stats['checkIssueCount']})")
    else:
        from legacy.dm2c_legacy_full_kg import build_and_export_legacy_full

        result = build_and_export_legacy_full(ifc_path=ifc_path, ontology_path=ontology_path, out_dir=out_dir)
        stats = result["stats"]
        report = result["mapping_report_data"]
        summary = report.get("summary", {})

        print("DM2C IFC-to-Graph completed.")
        print("  Mode: legacy_full")
        print(f"  IFC: {ifc_path}")
        print(f"  Ontology: {ontology_path}")
        print(f"  Output directory: {out_dir}")
        print("Generated files:")
        print(f"  - {result['graph_json']}")
        print(f"  - {result['graph_cypher']}")
        print(f"  - {result['graph_ttl']}")
        print(f"  - {result['mapping_report']}")
        print(f"  - {result['competency_queries']}")
        print("Graph summary:")
        print(
            "  Nodes={nodeCount}, Edges={edgeCount}, BIM={bimNodes}, Production={productionNodes}, Carbon={carbonNodes}, Mapping={mappingNodes}, Calculation={calculationNodes}".format(
                **stats
            )
        )
        print(
            "  CarbonEmission nodes: material={materialCarbonEmissionNodes}, process={processCarbonEmissionNodes}".format(
                **stats
            )
        )
        print(
            "  Elements processed={ifcElementsProcessed}, Templates applied={productionTemplatesApplied}, Carbon complete={successfulCarbonCalculations}, Carbon incomplete={incompleteCarbonCalculations}".format(
                **summary
            )
        )
        print(
            "  C_mat={totalMaterialCarbon_kgCO2e}, C_proc={totalProcessCarbon_kgCO2e}, C_MM={totalCarbon_kgCO2e}".format(
                **summary
            )
        )
        print(f"  Minimal checks passed: {stats['checkPassed']} (issues={stats['checkIssueCount']})")


if __name__ == "__main__":
    main()
