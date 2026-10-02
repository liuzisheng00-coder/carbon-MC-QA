import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import * as interfaceModel from "./interfaceModel.mjs";

import {
  PERSPECTIVE_TABS,
  VIEWER_PERFORMANCE_POLICY,
  buildAccountEntries,
  buildComponentSubgraphPath,
  buildComponentSubgraphLayout,
  buildComponentScene,
  buildDemoState,
  buildIfcGeometryScene,
  buildPerspectiveRows,
  buildSelectionContext,
  buildSelectionTags,
  deriveControlledDemoAccount,
  derivePendingGrounding,
  resolveDemoQuestion,
  selectDemoComponentSubgraph,
  extractAuditTrail,
  extractClarificationCandidates,
  normalizeComponentSubgraph,
  normalizeSelectionIds,
  primarySelectionId,
  resolveSelectedComponent,
  statusBadge,
  updateSelectionIds,
} from "./interfaceModel.mjs";

const EXPECTED_APPLICATION_CLASSES = [
  "ProductionBatch",
  "ModularUnit",
  "BuildingComponent",
  "ComponentType",
  "IfcMaterial",
  "DesignQuantity",
  "ManufacturingProcessTemplate",
  "ProductionStage",
  "ManufacturingActivity",
  "ManufacturingResource",
  "MaterialConsumption",
  "EnergyConsumption",
  "ConsumptionQuantity",
  "EmissionFactor",
  "EnergyCarrier",
  "CarbonEmission",
];

const EXPECTED_PRINCIPAL_TRIPLES = [
  ["ProductionBatch", "produces", "ModularUnit"],
  ["ModularUnit", "containsComponent", "BuildingComponent"],
  ["BuildingComponent", "hasComponentType", "ComponentType"],
  ["BuildingComponent", "hasMaterial", "IfcMaterial"],
  ["BuildingComponent", "hasDesignQuantity", "DesignQuantity"],
  ["ComponentType", "hasProcessTemplate", "ManufacturingProcessTemplate"],
  ["ManufacturingProcessTemplate", "hasStage", "ProductionStage"],
  ["ProductionStage", "hasActivity", "ManufacturingActivity"],
  ["ManufacturingActivity", "usesResource", "ManufacturingResource"],
  ["BuildingComponent", "manufacturedBy", "ManufacturingActivity"],
  ["MaterialConsumption", "recordedForObject", "BuildingComponent"],
  ["MaterialConsumption", "ofMaterial", "IfcMaterial"],
  ["MaterialConsumption", "hasQuantity", "ConsumptionQuantity"],
  ["MaterialConsumption", "hasFactor", "EmissionFactor"],
  ["EnergyConsumption", "recordedForObject", "BuildingComponent"],
  ["EnergyConsumption", "hasQuantity", "ConsumptionQuantity"],
  ["EnergyConsumption", "hasFactor", "EmissionFactor"],
  ["EnergyConsumption", "ofCarrier", "EnergyCarrier"],
  ["CarbonEmission", "hasCarbonDriver", "MaterialConsumption"],
  ["CarbonEmission", "hasCarbonDriver", "EnergyConsumption"],
  ["ConsumptionQuantity", "derivedFrom", "DesignQuantity"],
];

const FORBIDDEN_LABELS = [
  "AtomicCarbonEmission",
  "AggregateCarbonEmission",
  "AttributionObject",
  "ProductObject",
  "ProductType",
  "SourceObject",
  "FactoryTarget",
  "ConsumptionDriver",
  "ProcessElement",
  "Resource",
  "Material",
  "bim_Quantity",
  "CarbonBoundary",
  "VersionMapping",
  "GranularityLevel",
  "DataSource",
  "CaseSpecificExtension",
];

const FORBIDDEN_RELATIONS = [
  "EMISSION_OF",
  "HAS_CARBON_DRIVER",
  "HAS_CONSUMPTION_QUANTITY",
  "HAS_EMISSION_FACTOR",
  "HAS_PRODUCT_TYPE",
  "USES_ENERGY_CARRIER",
  "RELATED_TO_QUANTITY",
];

const sampleResults = [
  {
    id: "gid-1",
    name: "Steel beam",
    type: "IfcBeam",
    material: "Steel",
    cMat: 100,
    cProc: 20,
    total: 120,
    factor: "Steel | 2.08 kgCO2e/kg | ICE",
    materialStatus: "calculated",
    processStatus: "complete",
    quantityBasis: "NetVolume: 0.1 m3",
    process: [{ step: "Welding", status: "measured", co2e: 20, required: "kWh" }],
    gaps: [],
  },
  {
    id: "gid-2",
    name: "Concrete slab",
    type: "IfcSlab",
    material: "Concrete",
    cMat: 50,
    cProc: 0,
    total: 50,
    factor: "Concrete | 0.13 kgCO2e/kg | ICE",
    materialStatus: "calculated",
    processStatus: "missing",
    quantityBasis: "",
    process: [{ step: "Curing", status: "gap", co2e: null, required: "runtime hours" }],
    gaps: [{ process_title: "Curing", required_quantity: "runtime hours" }],
  },
];

const sampleSubgraphPayload = {
  subgraph: {
    schemaVersion: "m23-canonical-v2",
    rootNodeId: "component:g1",
    boundary: "selected_component_semantic_closure",
    truncated: false,
    nodes: [
      { id: "module:m1", labels: ["ModularUnit"], props: { name: "Module M1" } },
      {
        id: "component:g1",
        labels: ["BuildingComponent", "IfcBeam"],
        props: { globalId: "g1", name: "Beam B1" },
      },
      { id: "type:beam", labels: ["ComponentType"], props: { name: "IfcBeamType" } },
      { id: "material:steel", labels: ["IfcMaterial"], props: { name: "Steel S355", ifcStepId: 41 } },
      { id: "design:g1", labels: ["DesignQuantity", "IfcQuantityWeight"], props: { value: 6, unit: "kg" } },
      { id: "activity:a1", labels: ["ManufacturingActivity"], props: { name: "Assembly" } },
      {
        id: "consumption:material",
        labels: ["MaterialConsumption"],
        props: { recordId: "record:material", formulaCode: "direct_mass", isValidZero: false },
      },
      {
        id: "quantity:material",
        labels: ["ConsumptionQuantity"],
        props: { quantityValue: 6, quantityUnit: "kg" },
      },
      {
        id: "factor:steel",
        labels: ["EmissionFactor"],
        props: { factorSourceId: "factor-source:steel", factorValue: 2.08, factorUnit: "kgCO2e/kg" },
      },
      {
        id: "emission:material",
        labels: ["CarbonEmission"],
        props: {
          emissionValue: 12.5,
          emissionUnit: "kgCO2e",
          isValidZero: false,
        },
      },
      {
        id: "consumption:allocated",
        labels: ["EnergyConsumption"],
        props: {
          recordId: "record:allocated",
          attributionMode: "allocated",
          allocationSetId: "allocation:set:1",
          allocationBasis: "mass",
        },
      },
      {
        id: "quantity:allocated",
        labels: ["ConsumptionQuantity"],
        props: { quantityValue: 20, quantityUnit: "kWh" },
      },
      {
        id: "factor:electricity",
        labels: ["EmissionFactor"],
        props: { factorSourceId: "factor-source:electricity", factorValue: 1, factorUnit: "kgCO2e/kWh" },
      },
      { id: "carrier:electricity", labels: ["EnergyCarrier"], props: { carrierId: "electricity" } },
      {
        id: "emission:allocated",
        labels: ["CarbonEmission"],
        props: { emissionValue: 20, emissionUnit: "kgCO2e", isValidZero: false },
      },
      {
        id: "consumption:zero",
        labels: ["EnergyConsumption"],
        props: { recordId: "record:zero", attributionMode: "direct", isValidZero: true },
      },
      {
        id: "emission:zero",
        labels: ["CarbonEmission"],
        props: { emissionValue: 0, emissionUnit: "kgCO2e", isValidZero: true },
      },
    ],
    edges: [
      { id: "edge:module", occurrenceId: "occ:module", src: "module:m1", type: "containsComponent", tgt: "component:g1", props: {} },
      { id: "edge:type", occurrenceId: "occ:type", src: "component:g1", type: "hasComponentType", tgt: "type:beam", props: {} },
      { id: "edge:material:1", occurrenceId: "occ:material:1", src: "component:g1", type: "hasMaterial", tgt: "material:steel", props: { associationIndex: 0 } },
      { id: "edge:material:2", occurrenceId: "occ:material:2", src: "component:g1", type: "hasMaterial", tgt: "material:steel", props: { associationIndex: 1 } },
      { id: "edge:design", occurrenceId: "occ:design", src: "component:g1", type: "hasDesignQuantity", tgt: "design:g1", props: {} },
      { id: "edge:activity", occurrenceId: "occ:activity", src: "component:g1", type: "manufacturedBy", tgt: "activity:a1", props: {} },
      { id: "edge:material-recorded", occurrenceId: "occ:material:recorded", src: "consumption:material", type: "recordedForObject", tgt: "component:g1", props: { recordId: "record:material" } },
      { id: "edge:material-of", occurrenceId: "occ:material:of", src: "consumption:material", type: "ofMaterial", tgt: "material:steel", props: {} },
      { id: "edge:material-quantity", occurrenceId: "occ:material:quantity", src: "consumption:material", type: "hasQuantity", tgt: "quantity:material", props: {} },
      { id: "edge:material-factor", occurrenceId: "occ:material:factor", src: "consumption:material", type: "hasFactor", tgt: "factor:steel", props: {} },
      { id: "edge:material-derived", occurrenceId: "occ:material:derived", src: "quantity:material", type: "derivedFrom", tgt: "design:g1", props: {} },
      { id: "edge:material-emission", occurrenceId: "occ:material:emission", src: "emission:material", type: "hasCarbonDriver", tgt: "consumption:material", props: {} },
      {
        id: "edge:allocated-recorded",
        occurrenceId: "occ:allocated:recorded:g1",
        src: "consumption:allocated",
        type: "recordedForObject",
        tgt: "component:g1",
        props: {
          attributionMode: "allocated",
          allocationSetId: "allocation:set:1",
          allocationBasis: "mass",
          rawWeight: 5,
          rawWeightUnit: "kg",
          normalizedWeight: 0.25,
          evidenceRecordId: "allocation:evidence:g1",
        },
      },
      { id: "edge:allocated-quantity", occurrenceId: "occ:allocated:quantity", src: "consumption:allocated", type: "hasQuantity", tgt: "quantity:allocated", props: {} },
      { id: "edge:allocated-factor", occurrenceId: "occ:allocated:factor", src: "consumption:allocated", type: "hasFactor", tgt: "factor:electricity", props: {} },
      { id: "edge:allocated-carrier", occurrenceId: "occ:allocated:carrier", src: "consumption:allocated", type: "ofCarrier", tgt: "carrier:electricity", props: {} },
      { id: "edge:allocated-emission", occurrenceId: "occ:allocated:emission", src: "emission:allocated", type: "hasCarbonDriver", tgt: "consumption:allocated", props: {} },
      { id: "edge:zero-recorded", occurrenceId: "occ:zero:recorded", src: "consumption:zero", type: "recordedForObject", tgt: "component:g1", props: { sourceRecordId: "source:zero" } },
      { id: "edge:zero-emission", occurrenceId: "occ:zero:emission", src: "emission:zero", type: "hasCarbonDriver", tgt: "consumption:zero", props: {} },
    ],
    counts: { nodes: 17, edges: 19 },
  },
};

test("declares product material and process perspective tabs", () => {
  assert.deepEqual(
    PERSPECTIVE_TABS.map((tab) => tab.key),
    ["product", "material", "process"],
  );
});

test("declares the exact canonical-v2 application-class and typed-triple inventories", () => {
  assert.ok(interfaceModel.PROJECT_GRAPH_SCHEMA);
  const schema = interfaceModel.PROJECT_GRAPH_SCHEMA;

  assert.equal(schema.schemaVersion, "m23-canonical-v2");
  assert.deepEqual(schema.applicationClasses, EXPECTED_APPLICATION_CLASSES);
  assert.deepEqual(
    schema.principalTriples.map(({ source, relation, target }) => [source, relation, target]),
    EXPECTED_PRINCIPAL_TRIPLES,
  );
  assert.deepEqual(schema.inventory, {
    applicationClassCount: 16,
    principalTripleCount: 21,
    principalPredicateCount: 17,
  });
});

test("normalizes a canonical-v2 component closure without changing graph identity or zero values", () => {
  const graph = normalizeComponentSubgraph(sampleSubgraphPayload);

  assert.equal(graph.schemaVersion, "m23-canonical-v2");
  assert.equal(graph.rootNodeId, "component:g1");
  assert.equal(graph.boundary, "selected_component_semantic_closure");
  assert.equal(graph.nodes.find((node) => node.id === "component:g1").role, "component");
  assert.equal(graph.nodes.find((node) => node.id === "emission:material").role, "emission");
  assert.equal(graph.nodes.find((node) => node.id === "emission:material").detail, "12.5 kgCO2e");
  assert.equal(graph.nodes.find((node) => node.id === "consumption:allocated").role, "consumption");
  assert.match(graph.nodes.find((node) => node.id === "consumption:allocated").detail, /allocated/);
  assert.equal(graph.nodes.find((node) => node.id === "emission:zero").detail, "0 kgCO2e | valid zero");
  assert.equal(graph.nodes.find((node) => node.id === "emission:zero").validZero, true);

  const allocation = graph.edges.find((edge) => edge.id === "edge:allocated-recorded");
  assert.equal(allocation.occurrenceId, "occ:allocated:recorded:g1");
  assert.deepEqual(allocation.props, sampleSubgraphPayload.subgraph.edges.find((edge) => edge.id === allocation.id).props);
  assert.deepEqual(allocation.allocation, {
    sourceEmissionValue: 20,
    sourceEmissionUnit: "kgCO2e",
    componentContributionValue: 5,
    allocationSetId: "allocation:set:1",
    allocationBasis: "mass",
    rawWeight: 5,
    rawWeightUnit: "kg",
    normalizedWeight: 0.25,
    evidenceRecordId: "allocation:evidence:g1",
  });

  assert.deepEqual(
    graph.edges.filter((edge) => edge.type === "hasMaterial").map((edge) => edge.occurrenceId),
    ["occ:material:1", "occ:material:2"],
  );
});

test("uses all six canonical display roles without legacy parent labels", () => {
  const graph = normalizeComponentSubgraph({
    schemaVersion: "m23-canonical-v2",
    rootNodeId: "BuildingComponent:1",
    nodes: EXPECTED_APPLICATION_CLASSES.map((label) => ({
      id: `${label}:1`,
      labels: [label],
      props: label === "CarbonEmission" ? { emissionValue: 0, emissionUnit: "kgCO2e", isValidZero: true } : {},
    })),
    edges: [],
    counts: { nodes: 16, edges: 0 },
  });
  const roleByLabel = Object.fromEntries(graph.nodes.map((node) => [node.primaryLabel, node.role]));

  assert.deepEqual(roleByLabel, {
    BuildingComponent: "component",
    CarbonEmission: "emission",
    ComponentType: "component",
    ConsumptionQuantity: "evidence",
    DesignQuantity: "evidence",
    EmissionFactor: "evidence",
    EnergyCarrier: "evidence",
    EnergyConsumption: "consumption",
    IfcMaterial: "evidence",
    ManufacturingActivity: "process",
    ManufacturingProcessTemplate: "process",
    ManufacturingResource: "process",
    MaterialConsumption: "consumption",
    ModularUnit: "context",
    ProductionBatch: "context",
    ProductionStage: "process",
  });
});

test("requires canonical-v2 and rejects every exact forbidden label and relation", () => {
  assert.throws(
    () => normalizeComponentSubgraph({ ...sampleSubgraphPayload.subgraph, schemaVersion: "legacy" }),
    /m23-canonical-v2/,
  );

  for (const label of FORBIDDEN_LABELS) {
    assert.throws(
      () => normalizeComponentSubgraph({
        schemaVersion: "m23-canonical-v2",
        rootNodeId: "component:g1",
        nodes: [{ id: "component:g1", labels: ["BuildingComponent", label], props: {} }],
        edges: [],
      }),
      new RegExp(label),
    );
  }

  for (const relation of FORBIDDEN_RELATIONS) {
    assert.throws(
      () => normalizeComponentSubgraph({
        schemaVersion: "m23-canonical-v2",
        rootNodeId: "component:g1",
        nodes: [
          { id: "component:g1", labels: ["BuildingComponent"], props: {} },
          { id: "component:g2", labels: ["BuildingComponent"], props: {} },
        ],
        edges: [{ id: `edge:${relation}`, occurrenceId: `occ:${relation}`, src: "component:g1", type: relation, tgt: "component:g2", props: {} }],
      }),
      new RegExp(relation),
    );
  }

  assert.doesNotThrow(() => normalizeComponentSubgraph({
    schemaVersion: "m23-canonical-v2",
    rootNodeId: "component:g1",
    nodes: [
      { id: "component:g1", labels: ["BuildingComponent", "IfcMaterialProfileSetUsage"], props: {} },
      { id: "resource:1", labels: ["ManufacturingResource"], props: {} },
    ],
    edges: [],
  }));
});

test("requires exactly one application class per node and validates all typed triples", () => {
  const wrongTypedTriple = structuredClone(sampleSubgraphPayload.subgraph);
  wrongTypedTriple.edges[0].type = "produces";
  assert.throws(
    () => normalizeComponentSubgraph(wrongTypedTriple),
    /typed triple/i,
  );

  const classlessNode = structuredClone(sampleSubgraphPayload.subgraph);
  classlessNode.nodes.push({ id: "classless:1", labels: ["IfcBeam"], props: {} });
  assert.throws(
    () => normalizeComponentSubgraph(classlessNode),
    /exactly one canonical application class/i,
  );
});

test("never synthesizes RELATED_TO and uses occurrence identity when an edge id is absent", () => {
  const withoutType = structuredClone(sampleSubgraphPayload.subgraph);
  delete withoutType.edges[0].type;
  assert.throws(() => normalizeComponentSubgraph(withoutType), /edge type/i);

  const withoutId = structuredClone(sampleSubgraphPayload.subgraph);
  delete withoutId.edges[0].id;
  const normalized = normalizeComponentSubgraph(withoutId);
  assert.match(normalized.edges.find((edge) => edge.occurrenceId === "occ:module").id, /occ:module/);
  assert.equal(JSON.stringify(normalized).includes("RELATED_TO"), false);
});

test("lays out selected graph nodes deterministically and preserves edge endpoints", () => {
  const graph = normalizeComponentSubgraph(sampleSubgraphPayload);
  const first = buildComponentSubgraphLayout(graph);
  const second = buildComponentSubgraphLayout(graph);

  assert.deepEqual(first, second);
  assert.ok(first.width >= 800);
  assert.ok(first.height >= 240);
  assert.equal(first.nodes.find((node) => node.id === "component:g1").isRoot, true);
  const generatedFrom = first.edges.find((edge) => edge.id === "edge:material-emission");
  assert.equal(generatedFrom.source.id, "emission:material");
  assert.equal(generatedFrom.target.id, "consumption:material");
  assert.ok(first.nodes.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y)));
});

test("declares the demand-driven viewer policy", () => {
  assert.deepEqual(VIEWER_PERFORMANCE_POLICY, {
    renderMode: "demand",
    pixelRatioLimit: 1.5,
    highlightedEdgesOnly: true,
  });
});

test("builds encoded component subgraph API paths without shared-leaf expansion", () => {
  assert.equal(
    buildComponentSubgraphPath("project 1", "3cz$beam", true),
    "/api/projects/project%201/components/3cz%24beam/subgraph?max_nodes=80",
  );
  assert.equal(
    buildComponentSubgraphPath("project-1", "gid-1", false),
    "/api/projects/project-1/components/gid-1/subgraph?max_nodes=80",
  );
});

test("selects offline demo subgraphs by exact GlobalId", () => {
  const selected = selectDemoComponentSubgraph(
    { subgraphs: { g1: sampleSubgraphPayload.subgraph } },
    "g1",
  );
  assert.equal(selected.rootNodeId, "component:g1");
  assert.equal(selectDemoComponentSubgraph({ subgraphs: {} }, "missing"), null);
});

test("normalizes explicit external validation coverage without graph-status proxies", () => {
  assert.equal(typeof interfaceModel.normalizeExternalValidationCoverage, "function");
  const coverage = interfaceModel.normalizeExternalValidationCoverage({
    stats: {
      validation: {
        candidateCount: 5,
        acceptedCount: 3,
        rejectedCount: 2,
        acceptedByKind: { energy: 2, material: 1 },
        rejectedByKind: { material: 2 },
        rejectedByReason: { factor_not_found: 1, material_quantity_basis_missing: 1 },
        coverageFormula: "acceptedCount / candidateCount",
        coverageValue: 0.6,
      },
      unresolvedMaterials: 999,
    },
    blockedMaterialRecords: 999,
  });

  assert.deepEqual(coverage, {
    candidateCount: 5,
    acceptedCount: 3,
    rejectedCount: 2,
    acceptedByKind: { energy: 2, material: 1 },
    rejectedByKind: { material: 2 },
    rejectedByReason: { factor_not_found: 1, material_quantity_basis_missing: 1 },
    coverageFormula: "acceptedCount / candidateCount",
    coverageValue: 0.6,
  });
  assert.equal(interfaceModel.normalizeExternalValidationCoverage({ stats: { unresolvedMaterials: 7 } }), null);
});

test("rejects internally inconsistent external validation coverage", () => {
  const valid = {
    candidateCount: 10,
    acceptedCount: 8,
    rejectedCount: 2,
    acceptedByKind: { material: 5, energy: 3 },
    rejectedByKind: { material: 1, energy: 1 },
    rejectedByReason: { factor_not_found: 2 },
    coverageFormula: "acceptedCount / candidateCount",
    coverageValue: 0.8,
  };

  assert.deepEqual(interfaceModel.normalizeExternalValidationCoverage({ coverage: valid }), valid);
  assert.equal(interfaceModel.normalizeExternalValidationCoverage({ coverage: { ...valid, coverageValue: 0.1 } }), null);
  assert.equal(interfaceModel.normalizeExternalValidationCoverage({ coverage: {
    ...valid,
    acceptedByKind: { material: 7 },
  } }), null);
  assert.equal(interfaceModel.normalizeExternalValidationCoverage({ coverage: {
    ...valid,
    rejectedByReason: { factor_not_found: 1 },
  } }), null);
  assert.equal(interfaceModel.normalizeExternalValidationCoverage({ coverage: {
    ...valid,
    acceptedByKind: { material: 5, energy: 3, malformed: "not-a-count" },
  } }), null);
});

test("normalizes top-level project graph provenance without dropping artifact metadata or statistics", () => {
  assert.equal(typeof interfaceModel.normalizeProjectGraphInfo, "function");
  const coverage = {
    candidateCount: 5,
    acceptedCount: 4,
    rejectedCount: 1,
    acceptedByKind: { material: 2, energy: 2 },
    rejectedByKind: { energy: 1 },
    rejectedByReason: { factor_not_found: 1 },
    coverageFormula: "acceptedCount / candidateCount",
    coverageValue: 0.8,
  };
  const graphInfo = interfaceModel.normalizeProjectGraphInfo({
    schemaVersion: "m23-canonical-v2",
    graphInfo: { graph: "release/multigranular_carbon_kg.json", releaseId: "release-7" },
    metadata: { generatorId: "dm2c-component-subgraph-v2:test", notForActualRelease: true },
    manifest: { coverage, manifestId: "manifest-7" },
    stats: { nodeCount: 51, edgeCount: 68, validation: coverage },
  });

  assert.equal(graphInfo.schemaVersion, "m23-canonical-v2");
  assert.equal(graphInfo.graph, "release/multigranular_carbon_kg.json");
  assert.equal(graphInfo.releaseId, "release-7");
  assert.equal(graphInfo.metadata.generatorId, "dm2c-component-subgraph-v2:test");
  assert.equal(graphInfo.manifest.manifestId, "manifest-7");
  assert.equal(graphInfo.stats.nodeCount, 51);
  assert.equal(graphInfo.stats.edgeCount, 68);
  assert.deepEqual(interfaceModel.normalizeExternalValidationCoverage(graphInfo), coverage);
});

test("prioritizes payload graph provenance while keeping backbone statistics separate", () => {
  assert.equal(typeof interfaceModel.normalizeProjectGraphInfo, "function");
  const graphInfo = interfaceModel.normalizeProjectGraphInfo({
    graphInfo: {
      graph: "data-graph.json",
      metadata: { dataGraphInfo: true, priority: "data-graph-info" },
      stats: { dataGraphCount: 1, nodeCount: 10 },
    },
    metadata: { dataTop: true, priority: "data-top" },
    manifest: { dataManifest: true },
    stats: { dataTopCount: 2, nodeCount: 20 },
    inputs: { backboneBuild: { stats: { fallbackCount: 3, nodeCount: 30 } } },
    payload: {
      graphInfo: {
        graph: "payload-graph.json",
        metadata: { payloadGraphInfo: true, priority: "payload-graph-info" },
        stats: { payloadGraphCount: 4, nodeCount: 40 },
      },
      metadata: { payloadTop: true, priority: "payload-top" },
      manifest: { payloadManifest: true },
      stats: { payloadTopCount: 5, nodeCount: 50 },
    },
  });

  assert.equal(graphInfo.graph, "payload-graph.json");
  assert.deepEqual(graphInfo.metadata, {
    dataTop: true,
    dataGraphInfo: true,
    payloadTop: true,
    payloadGraphInfo: true,
    priority: "payload-graph-info",
  });
  assert.deepEqual(graphInfo.manifest, { dataManifest: true, payloadManifest: true });
  assert.deepEqual(graphInfo.stats, {
    dataTopCount: 2,
    dataGraphCount: 1,
    payloadTopCount: 5,
    payloadGraphCount: 4,
    nodeCount: 40,
  });
  assert.deepEqual(graphInfo.backboneStats, { fallbackCount: 3, nodeCount: 30 });
});

test("normalizes a backbone-only project without inventing canonical graph statistics", () => {
  const graphInfo = interfaceModel.normalizeProjectGraphInfo({
    inputs: {
      graph: "backbone/multigranular_carbon_kg.json",
      ifcSource: "typed_completed.ifc",
      backboneBuild: { stats: { nodeCount: 729, edgeCount: 728 } },
    },
    graphInfo: {
      canonicalGraphAvailable: false,
      canonicalGraphStatus: "backbone_only",
      canonicalValidationLevel: "none",
      stats: {},
    },
  });

  assert.equal(graphInfo.canonicalGraphAvailable, false);
  assert.equal(graphInfo.canonicalGraphStatus, "backbone_only");
  assert.deepEqual(graphInfo.stats, {});
  assert.deepEqual(graphInfo.backboneStats, { nodeCount: 729, edgeCount: 728 });
});

test("canonical graph and query availability are explicit fail-closed capabilities", () => {
  assert.equal(interfaceModel.isCanonicalGraphAvailable({ schemaVersion: "m23-canonical-v2" }), false);
  assert.equal(interfaceModel.isCanonicalGraphAvailable({
    schemaVersion: "m23-canonical-v2",
    canonicalGraphAvailable: false,
  }), false);
  assert.equal(interfaceModel.isCanonicalGraphAvailable({
    schemaVersion: "m23-canonical-v2",
    canonicalGraphAvailable: true,
  }), true);
  assert.equal(interfaceModel.isCanonicalGraphAvailable({}), false);
  assert.equal(interfaceModel.isCanonicalQueryAvailable({ canonicalGraphAvailable: true }), false);
  assert.equal(interfaceModel.isCanonicalQueryAvailable({
    schemaVersion: "m23-canonical-v2",
    canonicalGraphAvailable: true,
    canonicalQueryAvailable: true,
  }), true);
  assert.equal(interfaceModel.isCanonicalQueryAvailable({ canonicalQueryAvailable: false }), false);
});

test("graph UI displays the canonical inventories, allocation evidence, valid zero and external rejection coverage", () => {
  const panel = readFileSync(new URL("./GraphAssociationPanel.jsx", import.meta.url), "utf8");

  assert.match(panel, /PROJECT_GRAPH_SCHEMA/);
  assert.match(panel, /application classes/);
  assert.match(panel, /typed triples/);
  assert.match(panel, /Source emission/);
  assert.match(panel, /Component contribution/);
  assert.match(panel, /Allocation set/);
  assert.match(panel, /Evidence record/);
  assert.match(panel, /Valid zero/);
  assert.match(panel, /Rejected inputs are outside the graph/);
  assert.match(panel, /Coverage formula/);
});

test("upload and project mapping UI distinguish an IFC backbone from a canonical carbon graph", () => {
  const panel = readFileSync(new URL("./GraphAssociationPanel.jsx", import.meta.url), "utf8");
  const visual = readFileSync(new URL("./DM2CVisualInterface.jsx", import.meta.url), "utf8");

  assert.match(panel, /canonicalGraphAvailable/);
  assert.match(panel, /IFC alone does not create a carbon knowledge graph/);
  assert.match(panel, /schema\/ontology contract/);
  assert.match(panel, /not instantiated project facts/);
  assert.match(visual, /IFC is the design backbone\/geometry source/);
  assert.match(visual, /does not by itself create the carbon KG/);
});

test("graph-only and backbone-only status remain visible while live carbon queries fail closed", () => {
  const visual = readFileSync(new URL("./DM2CVisualInterface.jsx", import.meta.url), "utf8");
  const composer = readFileSync(new URL("./SelectionComposer.jsx", import.meta.url), "utf8");
  const connected = readFileSync(new URL("../DM2CApp.connected.jsx", import.meta.url), "utf8");

  assert.match(visual, /Backbone only — no canonical carbon KG/);
  assert.match(visual, /Canonical KG loaded — query executor unavailable/);
  assert.match(visual, /Canonical carbon accounts are unavailable/);
  assert.match(visual, /disabled=\{!canonicalQueryAvailable\}/);
  assert.match(composer, /disabled/);
  assert.match(composer, /Canonical-v2 carbon graph and query executor required/);
  assert.match(connected, /Carbon-account QA is unavailable until a validated canonical-v2 graph document is loaded/);
  assert.match(connected, /if \(!canonicalQueryAvailable\)/);
});

test("graph panel exposes accessible tab and tabpanel relationships", () => {
  const panel = readFileSync(new URL("./GraphAssociationPanel.jsx", import.meta.url), "utf8");

  assert.match(panel, /role="tablist"/);
  assert.match(panel, /aria-label="Knowledge graph views"/);
  assert.match(panel, /role="tab"/);
  assert.match(panel, /aria-selected=\{active\}/);
  assert.match(panel, /aria-controls=\{panelId\}/);
  assert.match(panel, /role="tabpanel"/);
  assert.match(panel, /aria-labelledby=\{`graph-tab-\$\{activeTab\}`\}/);
  assert.match(panel, /onKeyDown=\{handleTabKeyDown\}/);
  assert.match(panel, /ArrowLeft/);
  assert.match(panel, /ArrowRight/);
  assert.match(panel, /Home/);
  assert.match(panel, /End/);
});

test("connected demo loads one strict artifact and reuses it for graph provenance and selection", () => {
  const app = readFileSync(new URL("../DM2CApp.connected.jsx", import.meta.url), "utf8");
  const entry = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");

  assert.match(entry, /React\.StrictMode/);
  assert.match(app, /normalizeProjectGraphInfo/);
  assert.match(app, /validateControlledDemoArtifact/);
  assert.match(app, /crypto\.subtle\.digest\("SHA-256"/);
  assert.match(app, /response\.arrayBuffer\(\)/);
  assert.match(app, /let demoArtifactRequest = null/);
  assert.match(app, /if \(!demoArtifactRequest\)/);
  assert.match(app, /return demoArtifactRequest/);
  assert.match(app, /const \[demoArtifact, setDemoArtifact\]/);
  assert.match(app, /canonicalGraphAvailable: true/);
  assert.match(app, /canonicalQueryAvailable: true/);
  assert.match(app, /canonicalGraphAvailable: false/);
  assert.match(app, /canonicalQueryAvailable: false/);
  assert.match(app, /deriveControlledDemoAccount\(artifact\)/);
  assert.match(app, /setResults\(controlledAccount\.results\)/);
  assert.match(app, /setSummary\(controlledAccount\.summary\)/);
  assert.match(app, /selectDemoComponentSubgraph\(demoArtifact, selectedId\)/);
  assert.equal((app.match(/fetch\("\/demo-component-subgraphs\.json"/g) || []).length, 1);
  assert.match(app, /Demo graph artifact unavailable/);
});

test("front-end operational sources contain no exact legacy vocabulary or shared-expansion controls", () => {
  const sources = [
    new URL("./GraphAssociationPanel.jsx", import.meta.url),
    new URL("./DM2CVisualInterface.jsx", import.meta.url),
    new URL("../DM2CApp.connected.jsx", import.meta.url),
  ].map((path) => readFileSync(path, "utf8")).join("\n");
  const exactToken = (token) => new RegExp(`(^|[^A-Za-z0-9_])${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^A-Za-z0-9_]|$)`);

  const unambiguousOperationalTokens = [
    ...FORBIDDEN_LABELS.filter((token) => !["Material", "Resource"].includes(token)),
    ...FORBIDDEN_RELATIONS,
    "RELATED_TO",
  ];
  for (const token of unambiguousOperationalTokens) {
    assert.doesNotMatch(sources, exactToken(token), token);
  }
  for (const token of ["blocked", "atomic", "expandShared", "onExpandShared", "expand_shared", "factoryTarget"]) {
    assert.doesNotMatch(sources, new RegExp(token, "i"), token);
  }

  const publicVocabulary = JSON.stringify({
    schema: interfaceModel.PROJECT_GRAPH_SCHEMA,
    demo: buildDemoState(),
  });
  for (const token of [...FORBIDDEN_LABELS, ...FORBIDDEN_RELATIONS, "RELATED_TO"]) {
    assert.doesNotMatch(publicVocabulary, exactToken(token), token);
  }
});

test("builds product, material and process projection rows from one carbon account", () => {
  const productRows = buildPerspectiveRows(sampleResults, "product");
  const materialRows = buildPerspectiveRows(sampleResults, "material");
  const processRows = buildPerspectiveRows(sampleResults, "process");

  assert.equal(productRows[0].primary, "Steel beam");
  assert.equal(productRows[0].knownTotal, 120);
  assert.equal(materialRows.find((row) => row.primary === "Steel").knownMaterial, 100);
  assert.equal(processRows.find((row) => row.primary === "Curing").status, "gap");
});

const askPayloadWithAccounts = {
  project_id: "p-1",
  payload: {
    // The rows of the answered question, which are not the project's components.
    results: [
      { material: "IfcMaterial:04c6bb", material_name: "Double-layer louver", kgCO2e: 26.16 },
    ],
    carbonAccounts: {
      product: [
        {
          id: "BuildingComponent:aa",
          name: "Slab G7",
          context: "IfcSlab",
          materialKgCO2e: 1318.15,
          processKgCO2e: 111.35,
          totalKgCO2e: 1429.5,
          status: "measured",
        },
        {
          id: "BuildingComponent:bb",
          name: "Conduit elbow",
          context: "IfcCableCarrierFitting",
          materialKgCO2e: null,
          processKgCO2e: 0.006,
          totalKgCO2e: 0.006,
          status: "process_only",
        },
      ],
      material: [
        {
          id: "IfcMaterial:04c6bb",
          name: "Double-layer louver",
          context: "4 component(s)",
          materialKgCO2e: 26.16,
          processKgCO2e: null,
          totalKgCO2e: 26.16,
          status: "measured",
        },
      ],
      process: [
        {
          id: "ProductionStage:da5d",
          name: "Transfer & leveling",
          context: "electricity, diesel",
          materialKgCO2e: null,
          processKgCO2e: 1373.0,
          totalKgCO2e: 1373.0,
          status: "measured",
        },
      ],
    },
  },
};

test("reads the three account views from the payload instead of the answer rows", () => {
  const accounts = interfaceModel.normalizeCarbonAccounts(askPayloadWithAccounts);
  const productRows = interfaceModel.buildAccountRows(accounts, "product");
  const materialRows = interfaceModel.buildAccountRows(accounts, "material");
  const processRows = interfaceModel.buildAccountRows(accounts, "process");

  assert.equal(productRows[0].primary, "Slab G7");
  assert.equal(productRows[0].secondary, "IfcSlab");
  assert.equal(productRows[0].knownTotal, 1429.5);
  assert.equal(productRows[1].status, "process_only");
  assert.equal(materialRows[0].primary, "Double-layer louver");
  assert.equal(materialRows[0].knownMaterial, 26.16);
  assert.equal(processRows[0].knownProcess, 1373);
});

test("says there is no account rather than inventing one from answer rows", () => {
  assert.equal(interfaceModel.normalizeCarbonAccounts({ payload: { results: [] } }), null);
  assert.deepEqual(interfaceModel.buildAccountRows(null, "product"), []);
});

test("marks a partly covered row without calling it broken", () => {
  assert.equal(statusBadge("process_only").tone, "warn");
  assert.equal(statusBadge("material_only").tone, "warn");
  assert.equal(statusBadge("measured").tone, "good");
});

test("builds deictic selection context from clicked GlobalId", () => {
  assert.deepEqual(buildSelectionContext("gid-1"), {
    selected_component_ids: ["gid-1"],
    scope: "selected_component",
  });
  assert.deepEqual(buildSelectionContext(""), {
    selected_component_ids: [],
    scope: "project",
  });
});

test("normalizes a multi-component selection without blanks or duplicates", () => {
  assert.deepEqual(normalizeSelectionIds(["gid-1", "", "gid-2", "gid-1", null]), [
    "gid-1",
    "gid-2",
  ]);
  assert.deepEqual(normalizeSelectionIds("gid-1"), ["gid-1"]);
  assert.equal(primarySelectionId(["gid-1", "gid-2"]), "gid-2");
  assert.equal(primarySelectionId([]), null);
});

test("replaces selection in single mode and toggles components in compare mode", () => {
  assert.deepEqual(updateSelectionIds(["gid-1"], "gid-2", false), ["gid-2"]);
  assert.deepEqual(updateSelectionIds(["gid-1"], "gid-2", true), ["gid-1", "gid-2"]);
  assert.deepEqual(updateSelectionIds(["gid-1", "gid-2"], "gid-1", true), ["gid-2"]);
  assert.deepEqual(updateSelectionIds(["gid-1"], "", true), ["gid-1"]);
});

test("builds one QA selection context from all selected GlobalIds", () => {
  assert.deepEqual(buildSelectionContext(["gid-1", "gid-2", "gid-1"]), {
    selected_component_ids: ["gid-1", "gid-2"],
    scope: "selected_component",
  });
});

test("builds ordered composer tags for selected BIM components", () => {
  assert.deepEqual(buildSelectionTags(sampleResults, ["gid-2", "gid-1", "missing-id"]), [
    { id: "gid-2", label: "Concrete slab", type: "IfcSlab" },
    { id: "gid-1", label: "Steel beam", type: "IfcBeam" },
    { id: "missing-id", label: "missing-id", type: "" },
  ]);
  assert.deepEqual(buildSelectionTags(sampleResults, []), []);
});

test("resolves a selected graph component without depending on legacy result rows", () => {
  assert.deepEqual(
    resolveSelectedComponent(
      [{ id: "g1", name: "Viewer beam", type: "IfcBeam" }],
      "g1",
      null,
    ),
    { id: "g1", name: "Viewer beam", type: "IfcBeam" },
  );

  const graph = normalizeComponentSubgraph(sampleSubgraphPayload);
  assert.deepEqual(resolveSelectedComponent([], "g1", graph), {
    id: "g1",
    name: "Beam B1",
    type: "IfcBeam",
  });
  assert.equal(resolveSelectedComponent([], "", graph), null);
});

test("selection composer exposes removable BIM context tags", () => {
  const source = readFileSync(new URL("./SelectionComposer.jsx", import.meta.url), "utf8");

  assert.match(source, /aria-label="Selected BIM context"/);
  assert.ok(source.includes('aria-label={`Remove ${tag.label} from question context`}'));
  assert.match(source, /title=\{tag\.id\}/);
  assert.match(source, /onRemoveSelection\?\.\(tag\.id\)/);
  assert.match(source, /aria-pressed=\{compareSelection\}/);
});

test("extracts clickable clarification candidates", () => {
  const payload = {
    queryResult: { status: "clarification_required" },
    carbonRequirement: { target_hints: ["panel"] },
    requestContext: {
      selectedComponents: [
        { globalId: "gid-1", name: "Panel A", ifcType: "IfcWall" },
        { globalId: "gid-2", name: "Panel B", ifcType: "IfcWall" },
      ],
    },
  };

  const candidates = extractClarificationCandidates(payload);

  assert.equal(candidates.length, 2);
  assert.equal(candidates[0].id, "gid-1");
  assert.equal(candidates[0].label, "Panel A");
});

test("extracts status, boundary and evidence trail for audit outlet", () => {
  const payload = {
    queryResult: {
      status: "incomplete_path",
      summary: { knownTotalCarbon_kgCO2e: 120 },
      semanticPath: "CarbonEmission -> Driver -> Quantity",
    },
    carbonRequirement: { perspective: "traceability", operation: "trace" },
    reasoningTrace: [
      { agent: "M3.1_requirement_identifier", action: "semantic_parse" },
      { agent: "M3.2_query_executor", action: "traceability_trace" },
    ],
    referenceGrounding: {
      decision: "clarify",
      reason: "multiple_matching_components",
      committedIds: [],
      candidates: [{ id: "gid-1" }, { id: "gid-2" }],
    },
    requestContext: { scope: "selected_component" },
  };

  const audit = extractAuditTrail(payload);

  assert.equal(audit.status, "incomplete_path");
  assert.equal(audit.boundary, "factory-gate modularization / selected_component");
  assert.equal(audit.evidenceChain[0].label, "Semantic path");
  assert.equal(audit.evidenceChain.find((item) => item.label === "Reference decision").value, "clarify");
  assert.equal(audit.evidenceChain.find((item) => item.label === "Grounding candidates").value, "2");
  assert.equal(audit.traceEvents.length, 2);
});

test("normalizes status badge semantics", () => {
  assert.equal(statusBadge("executable").tone, "good");
  assert.equal(statusBadge("clarification_required").tone, "warn");
  assert.equal(statusBadge("incomplete_path").tone, "bad");
});

test("builds component account entries for detail panel", () => {
  const entries = buildAccountEntries(sampleResults[0]);

  assert.equal(entries[0].label, "Material carbon");
  assert.equal(entries.find((entry) => entry.label === "Quantity basis").value, "NetVolume: 0.1 m3");
});

test("builds demo state for implementation screenshot without backend data", () => {
  const demo = buildDemoState();

  assert.equal(demo.started, true);
  assert.ok(demo.results.length >= 2);
  assert.equal(demo.selectedId, null);
  assert.equal(demo.messages[0].audit.status, "incomplete_path");
  assert.equal(demo.messages[0].audit.boundary, "controlled_demo / artifact_loading");
  assert.match(demo.messages[0].content, /Loading and validating/);
  assert.equal(demo.summary.knownTotalCarbon_kgCO2e, 0);
  assert.equal(demo.summary.processInputGaps, 0);
  assert.equal("blockedMaterialRecords" in demo.summary, false);
  assert.equal("blockedMaterialComponents" in demo.summary, false);
  assert.equal(demo.graphInfo.schemaVersion, "m23-canonical-v2");
  assert.equal(demo.graphInfo.artifactStatus, "awaiting-controlled-artifact");
  assert.equal(demo.graphInfo.canonicalGraphAvailable, false);
  assert.equal(demo.graphInfo.canonicalQueryAvailable, false);
  assert.equal("metadata" in demo.graphInfo, false);
  assert.equal("manifest" in demo.graphInfo, false);
  assert.equal("stats" in demo.graphInfo, false);
  assert.equal(interfaceModel.normalizeExternalValidationCoverage(demo.graphInfo), null);
  assert.deepEqual(
    demo.results.map((row) => row.id),
    [
      "3czbugqbT86PTcmnme1im9",
      "3czbugqbT86PTcmnme1imB",
      "3czbugqbT86PTcmnme1im5",
      "3czbugqbT86PTcmnme1im7",
    ],
  );
});

test("controlled demo activation validates the complete artifact before enabling capabilities", () => {
  const artifactBytes = readFileSync(new URL("../public/demo-component-subgraphs.json", import.meta.url));
  const artifactHash = createHash("sha256").update(artifactBytes).digest("hex").toUpperCase();
  const artifact = JSON.parse(artifactBytes.toString("utf8"));
  assert.equal(artifactHash, interfaceModel.CONTROLLED_DEMO_ARTIFACT_SHA256);
  assert.equal(interfaceModel.validateControlledDemoArtifact(artifact, artifactHash), artifact);
  const controlledAccount = deriveControlledDemoAccount(artifact);
  assert.deepEqual(controlledAccount.summary, {
    components: 4,
    totalMaterialCarbon_kgCO2e: 19,
    knownProcessCarbon_kgCO2e: 24,
    knownTotalCarbon_kgCO2e: 43,
    processInputGaps: 0,
  });
  assert.deepEqual(
    controlledAccount.results.map(({ id, cMat, cProc, total }) => ({ id, cMat, cProc, total })),
    [
      { id: "3czbugqbT86PTcmnme1im9", cMat: 10, cProc: 5, total: 15 },
      { id: "3czbugqbT86PTcmnme1imB", cMat: 6, cProc: 15, total: 21 },
      { id: "3czbugqbT86PTcmnme1im5", cMat: 2, cProc: 4, total: 6 },
      { id: "3czbugqbT86PTcmnme1im7", cMat: 1, cProc: 0, total: 1 },
    ],
  );

  const invalidSubgraph = structuredClone(artifact);
  invalidSubgraph.subgraphs[Object.keys(invalidSubgraph.subgraphs)[0]].edges[0].type = "RELATED_TO";
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(invalidSubgraph, artifactHash),
    /forbidden|typed triple|relation/i,
  );

  const excludedComponent = structuredClone(artifact);
  const excludedRoot = Object.values(excludedComponent.subgraphs)[0].nodes.find(
    (node) => node.labels.includes("BuildingComponent"),
  );
  excludedRoot.labels = excludedRoot.labels.filter((label) => label !== "IfcBeam");
  excludedRoot.labels.push("IfcValve");
  excludedRoot.labels.sort();
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(excludedComponent, artifactHash),
    /excluded factory/i,
  );

  const missingProvenance = structuredClone(artifact);
  delete missingProvenance.metadata.sources;
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(missingProvenance, artifactHash),
    /provenance/i,
  );

  const inconsistentStats = structuredClone(artifact);
  inconsistentStats.stats.nodeCount += 1;
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(inconsistentStats, artifactHash),
    /statistics/i,
  );

  const invalidEmission = structuredClone(artifact);
  const emission = Object.values(invalidEmission.subgraphs)[0].nodes.find(
    (node) => node.labels.includes("CarbonEmission"),
  );
  emission.props.emissionValue = 999;
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(invalidEmission, artifactHash),
    /emission semantics/i,
  );

  const invalidCounts = structuredClone(artifact);
  Object.values(invalidCounts.subgraphs)[0].counts.labels = { BuildingComponent: 999 };
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(invalidCounts, artifactHash),
    /subgraph statistics/i,
  );

  const invalidRelationInventory = structuredClone(artifact);
  const relationEntries = Object.entries(invalidRelationInventory.stats.relationCounts);
  delete invalidRelationInventory.stats.relationCounts[relationEntries[0][0]];
  invalidRelationInventory.stats.relationCounts.EVIL = relationEntries[0][1];
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(invalidRelationInventory, artifactHash),
    /relation inventory/i,
  );

  const missingGenerator = structuredClone(artifact);
  delete missingGenerator.metadata.generatorId;
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(missingGenerator, artifactHash),
    /generator/i,
  );

  const staleSourcePath = structuredClone(artifact);
  staleSourcePath.metadata.sources["typed_completed.ifc"].path = "F:/old/typed_completed_20260718.ifc";
  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(staleSourcePath, artifactHash),
    /provenance/i,
  );

  assert.throws(
    () => interfaceModel.validateControlledDemoArtifact(artifact, "0".repeat(64)),
    /integrity/i,
  );
});

test("demo IFC geometry retains the selectable GlobalIds", () => {
  const geometry = JSON.parse(
    readFileSync(new URL("../public/demo-ifc-geometry.json", import.meta.url), "utf8"),
  );
  const expectedIds = [
    "3czbugqbT86PTcmnme1im9",
    "3czbugqbT86PTcmnme1imB",
    "3czbugqbT86PTcmnme1im5",
    "3czbugqbT86PTcmnme1im7",
  ];

  assert.equal(geometry.sourceFile, "typed_completed.ifc");
  assert.ok(expectedIds.every((id) => geometry.meshes.some((mesh) => mesh.globalId === id)));
});

test("demo component closures are controlled canonical-v2 data grounded to the demo geometry", () => {
  const geometry = JSON.parse(
    readFileSync(new URL("../public/demo-ifc-geometry.json", import.meta.url), "utf8"),
  );
  const artifact = JSON.parse(
    readFileSync(new URL("../public/demo-component-subgraphs.json", import.meta.url), "utf8"),
  );
  const geometryIds = new Set(geometry.meshes.map((mesh) => mesh.globalId));

  assert.equal(artifact.schemaVersion, "m23-canonical-v2");
  assert.equal(artifact.metadata.controlledFixtureProfile, "task12-controlled-component-closure");
  assert.equal(artifact.metadata.notForActualRelease, true);
  assert.match(artifact.metadata.generatorId, /^dm2c-component-subgraph-v2:/);
  assert.deepEqual(
    interfaceModel.normalizeExternalValidationCoverage(artifact.metadata),
    artifact.metadata.externalValidationCoverage,
  );

  const sourceDescriptors = Object.values(artifact.metadata.sources);
  assert.ok(sourceDescriptors.length >= 2);
  for (const descriptor of sourceDescriptors) {
    assert.ok(descriptor.path && !/^(?:[A-Za-z]:|[/\\])/.test(descriptor.path));
    assert.match(descriptor.sha256, /^[A-F0-9]{64}$/);
  }

  const entries = Object.entries(artifact.subgraphs);
  assert.ok(entries.length >= 2);
  assert.ok(entries.every(([globalId]) => geometryIds.has(globalId)));
  let allocatedClosureCount = 0;
  for (const [globalId, subgraph] of entries) {
    assert.equal(subgraph.schemaVersion, "m23-canonical-v2");
    const root = subgraph.nodes.find((node) => node.id === subgraph.rootNodeId);
    assert.ok(root.labels.includes("BuildingComponent"));
    assert.ok([root.props.globalId, root.props.ifcGlobalId].includes(globalId));

    const labels = new Set(subgraph.nodes.flatMap((node) => node.labels));
    const relations = new Set(subgraph.edges.map((edge) => edge.type));
    assert.deepEqual([...labels].filter((label) => FORBIDDEN_LABELS.includes(label)), []);
    assert.deepEqual([...relations].filter((relation) => FORBIDDEN_RELATIONS.includes(relation)), []);
    assert.equal(JSON.stringify({ nodes: subgraph.nodes, edges: subgraph.edges }).includes("process_only"), false);

    const allocated = subgraph.edges.filter(
      (edge) => edge.type === "recordedForObject" && edge.props?.attributionMode === "allocated",
    );
    if (allocated.length) allocatedClosureCount += 1;
    assert.ok(allocated.every((edge) => edge.tgt === subgraph.rootNodeId));
  }
  assert.ok(allocatedClosureCount >= 1);
});

test("derives the active clarification question and candidate ids", () => {
  const pending = derivePendingGrounding([
    { role: "assistant", content: "Ready" },
    {
      role: "assistant",
      pendingQuestion: "What is the carbon emission of the steel beam?",
      candidates: [{ id: "gid-1" }, { id: "gid-2" }],
    },
  ]);

  assert.deepEqual(pending, {
    pendingQuestion: "What is the carbon emission of the steel beam?",
    candidateIds: ["gid-1", "gid-2"],
    candidates: [{ id: "gid-1" }, { id: "gid-2" }],
  });
  assert.deepEqual(derivePendingGrounding([]), {
    pendingQuestion: "",
    candidateIds: [],
    candidates: [],
  });
  assert.deepEqual(
    derivePendingGrounding([
      {
        role: "assistant",
        pendingQuestion: "What is the carbon emission of the steel beam?",
        candidates: [{ id: "gid-1" }],
      },
      { role: "assistant", content: "Steel beam B1a has 289.808 kgCO2e known carbon." },
    ]),
    { pendingQuestion: "", candidateIds: [], candidates: [] },
  );
});

test("demo ambiguous beam question returns grounding candidates without a carbon value", () => {
  const artifact = JSON.parse(
    readFileSync(new URL("../public/demo-component-subgraphs.json", import.meta.url), "utf8"),
  );
  const account = deriveControlledDemoAccount(artifact);
  const payload = resolveDemoQuestion("What is the carbon emission of the steel beam?", account.results);

  assert.equal(payload.referenceGrounding.decision, "clarify");
  assert.equal(payload.queryResult.status, "clarification_required");
  assert.equal(payload.queryResult.candidates.length, 2);
  assert.equal(payload.queryResult.summary.knownTotalCarbon_kgCO2e, undefined);
});

test("demo selected candidate returns executable carbon answer", () => {
  const artifact = JSON.parse(
    readFileSync(new URL("../public/demo-component-subgraphs.json", import.meta.url), "utf8"),
  );
  const account = deriveControlledDemoAccount(artifact);
  const selectedId = account.results[0].id;
  const payload = resolveDemoQuestion(
    "What is the carbon emission of the steel beam?",
    account.results,
    [selectedId],
  );

  assert.equal(payload.referenceGrounding.decision, "commit");
  assert.deepEqual(payload.referenceGrounding.committedIds, [selectedId]);
  assert.equal(payload.queryResult.status, "executable");
  assert.equal(payload.queryResult.rows[0].componentGlobalId, selectedId);
  assert.equal(payload.queryResult.summary.knownTotalCarbon_kgCO2e, account.results[0].total);
});

test("builds deterministic 3D scene primitives for component rendering", () => {
  const scene = buildComponentScene(sampleResults, ["gid-2"]);

  assert.equal(scene.meshes.length, sampleResults.length);
  assert.equal(scene.meshes[0].id, "gid-1");
  assert.deepEqual(scene.meshes[0].size, [2.4, 0.32, 0.32]);
  assert.equal(scene.meshes[1].selected, true);
  assert.equal(scene.meshes[1].color, "#E24B4A");
  assert.equal(scene.primarySelectedId, "gid-2");
  assert.deepEqual(scene.camera.target, scene.meshes[1].position);
  assert.ok(scene.bounds.radius > 1);
});

test("highlights every selected fallback mesh and keeps the last selection primary", () => {
  const scene = buildComponentScene(sampleResults, ["gid-1", "gid-2"]);

  assert.deepEqual(scene.meshes.filter((mesh) => mesh.selected).map((mesh) => mesh.id), [
    "gid-1",
    "gid-2",
  ]);
  assert.equal(scene.primarySelectedId, "gid-2");
  assert.deepEqual(scene.camera.target, scene.meshes[1].position);
});

test("marks unresolved component candidates amber in the fallback scene", () => {
  const scene = buildComponentScene(sampleResults, "", ["gid-1"]);

  assert.equal(scene.meshes[0].candidate, true);
  assert.equal(scene.meshes[0].color, "#EF9F27");
  assert.equal(scene.meshes[1].candidate, false);
});

test("builds camera and selection state for real IFC mesh geometry", () => {
  const geometry = {
    meshes: [
      {
        globalId: "gid-1",
        ifcType: "IfcWall",
        name: "Wall A",
        color: "#34AA22",
        materials: [{ color: "#34AA22", opacity: 1 }],
        materialIds: [0],
        vertices: [0, 0, 0, 2, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
      },
      {
        globalId: "gid-2",
        ifcType: "IfcBeam",
        name: "Beam B",
        vertices: [2, 1, 0, 3, 1, 0, 2, 2, 0],
        indices: [0, 1, 2],
      },
    ],
  };

  const scene = buildIfcGeometryScene(geometry, ["gid-2"]);

  assert.equal(scene.meshes.length, 2);
  assert.equal(scene.meshes[0].color, "#34AA22");
  assert.equal(scene.meshes[0].materials[0].color, "#34AA22");
  assert.equal(scene.meshes[1].selected, true);
  assert.equal(scene.meshes[1].color, "#E24B4A");
  assert.equal(scene.primarySelectedId, "gid-2");
  assert.deepEqual(scene.bounds.center, [1.5, 1, 0]);
  assert.ok(scene.bounds.radius > 1.7);
  assert.ok(scene.camera.position[2] > scene.bounds.center[2]);
});

test("highlights every selected IFC mesh and targets the last selected mesh", () => {
  const geometry = {
    meshes: [
      {
        globalId: "gid-1",
        vertices: [0, 0, 0, 2, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
      },
      {
        globalId: "gid-2",
        vertices: [2, 1, 0, 3, 1, 0, 2, 2, 0],
        indices: [0, 1, 2],
      },
    ],
  };

  const scene = buildIfcGeometryScene(geometry, ["gid-1", "gid-2"]);

  assert.deepEqual(scene.meshes.filter((mesh) => mesh.selected).map((mesh) => mesh.id), [
    "gid-1",
    "gid-2",
  ]);
  assert.equal(scene.primarySelectedId, "gid-2");
  assert.deepEqual(scene.camera.target, [1, 0, -0.5]);
});

test("marks real IFC candidate meshes amber until one is selected", () => {
  const geometry = {
    meshes: [
      {
        globalId: "gid-1",
        ifcType: "IfcWall",
        name: "Wall A",
        vertices: [0, 0, 0, 2, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
      },
      {
        globalId: "gid-2",
        ifcType: "IfcBeam",
        name: "Beam B",
        vertices: [2, 1, 0, 3, 1, 0, 2, 2, 0],
        indices: [0, 1, 2],
      },
    ],
  };

  const candidateScene = buildIfcGeometryScene(geometry, "", ["gid-2"]);
  assert.equal(candidateScene.meshes[1].candidate, true);
  assert.equal(candidateScene.meshes[1].color, "#EF9F27");

  const selectedScene = buildIfcGeometryScene(geometry, "gid-2", ["gid-2"]);
  assert.equal(selectedScene.meshes[1].selected, true);
  assert.equal(selectedScene.meshes[1].color, "#E24B4A");
});

test("DM2CApp memoizes grounding candidates so typing does not rebuild the IFC scene", () => {
  const source = readFileSync(new URL("../DM2CApp.connected.jsx", import.meta.url), "utf8");

  assert.match(
    source,
    /const pendingGrounding = useMemo\(\(\) => derivePendingGrounding\(messages\), \[messages\]\)/,
  );
});

test("DM2CApp and the composer expose a real compare-selection workflow", () => {
  const appSource = readFileSync(new URL("../DM2CApp.connected.jsx", import.meta.url), "utf8");
  const interfaceSource = readFileSync(new URL("./DM2CVisualInterface.jsx", import.meta.url), "utf8");

  assert.match(appSource, /const \[selectedIds, setSelectedIds\] = useState/);
  assert.match(appSource, /const \[compareSelection, setCompareSelection\] = useState\(false\)/);
  assert.match(appSource, /buildSelectionContext\(activeSelectedIds\)/);
  assert.match(appSource, /setSelectedIds\(\[id\]\)[\s\S]*setCompareSelection\(false\)/);
  assert.match(appSource, /const removeSelection = \(id\) =>/);
  assert.match(appSource, /onRemoveSelection=\{removeSelection\}/);
  assert.match(interfaceSource, /<SelectionComposer/);
  assert.match(interfaceSource, /compareSelection=\{compareSelection\}/);
  assert.match(interfaceSource, /selectionTags=\{selectionTags\}/);
});

test("the approved interface uses an immersive canvas and floating QA panel", () => {
  const source = readFileSync(new URL("./DM2CVisualInterface.jsx", import.meta.url), "utf8");

  assert.match(source, /className="dm2c-immersive-canvas"/);
  assert.match(source, /className="dm2c-floating-qa"/);
  assert.match(source, /showQaPanel/);
  assert.match(source, /activeWorkspaceMode === "model"/);
  assert.match(source, /<WorkspaceModeSwitch/);
  assert.doesNotMatch(source, /dm2c-header-totals/);
  assert.doesNotMatch(source, /<ModelEntryPanel/);
});

test("workspace mode switch exposes the five account views", () => {
  const source = readFileSync(new URL("./WorkspaceModeSwitch.jsx", import.meta.url), "utf8");

  for (const mode of ["model", "product", "material", "process", "graph"]) {
    assert.match(source, new RegExp(`key: "${mode}"`));
  }
  assert.match(source, /aria-pressed=\{activeMode === mode\.key\}/);
  assert.match(source, /onChange\?\.\(mode\.key\)/);
  assert.match(source, /canonicalQueryAvailable = false/);
});

test("assistant answers keep evidence behind an explicit disclosure", () => {
  const source = readFileSync(new URL("./DM2CVisualInterface.jsx", import.meta.url), "utf8");

  assert.match(source, /Show evidence/);
  assert.match(source, /className="dm2c-evidence-sheet"/);
  assert.match(source, /<StatusPill status=\{audit\.status\}/);
  assert.doesNotMatch(source, /\{msg\.trace && \(/);
});

test("the immersive layout becomes a bottom-sheet QA interface below 900px", () => {
  const css = readFileSync(new URL("./dm2c-interface.css", import.meta.url), "utf8");

  assert.match(css, /@media \(max-width: 899px\)/);
  assert.match(css, /\.dm2c-floating-qa[\s\S]*height: min\(55vh,/);
  assert.match(css, /\.dm2c-mode-switch/);
});

test("the immersive canvas owns the viewport without browser body margins", () => {
  const css = readFileSync(new URL("./dm2c-interface.css", import.meta.url), "utf8");

  assert.match(css, /html,\s*body,\s*#root\s*\{[\s\S]*margin: 0;/);
  assert.match(css, /html,\s*body,\s*#root\s*\{[\s\S]*height: 100%;/);
  assert.match(css, /body\s*\{[\s\S]*overflow: hidden;/);
});

test("the non-demo upload entry remains a complete usable screen", () => {
  const css = readFileSync(new URL("./dm2c-interface.css", import.meta.url), "utf8");
  const source = readFileSync(new URL("./DM2CVisualInterface.jsx", import.meta.url), "utf8");

  assert.match(css, /\.dm2c-upload-screen\s*\{/);
  assert.match(css, /\.dm2c-upload-grid\s*\{[\s\S]*grid-template-columns:/);
  assert.match(css, /\.dm2c-file-zone\s*\{/);
  assert.match(css, /\.dm2c-create-project\s*\{/);
  assert.match(source, /className="dm2c-file-zone-trigger"/);
  assert.ok(source.includes('aria-label={`Add ${slot.label} files`}'));
});
