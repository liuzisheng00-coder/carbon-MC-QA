export const PERSPECTIVE_TABS = [
  { key: "product", label: "Product", description: "Components and module-level carbon account" },
  { key: "material", label: "Material", description: "Material projection of the accepted carbon records" },
  { key: "process", label: "Process", description: "Factory energy and manufacturing-activity projection" },
];

export const VIEWER_PERFORMANCE_POLICY = Object.freeze({
  renderMode: "demand",
  pixelRatioLimit: 1.5,
  highlightedEdgesOnly: true,
});

const APPLICATION_CLASSES = Object.freeze([
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
]);

const PRINCIPAL_TRIPLES = Object.freeze([
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
].map(([source, relation, target]) => Object.freeze({ source, relation, target })));

const APPLICATION_CLASS_SET = new Set(APPLICATION_CLASSES);
const PRINCIPAL_TRIPLE_KEYS = new Set(
  PRINCIPAL_TRIPLES.map(({ source, relation, target }) => `${source}|${relation}|${target}`),
);

export const PROJECT_GRAPH_SCHEMA = Object.freeze({
  schemaVersion: "m23-canonical-v2",
  applicationClasses: APPLICATION_CLASSES,
  principalTriples: PRINCIPAL_TRIPLES,
  inventory: Object.freeze({
    applicationClassCount: APPLICATION_CLASSES.length,
    principalTripleCount: PRINCIPAL_TRIPLES.length,
    principalPredicateCount: new Set(PRINCIPAL_TRIPLES.map(({ relation }) => relation)).size,
  }),
});

export const CONTROLLED_DEMO_ARTIFACT_SHA256 =
  "59FA65CEB742D8E2FD33660E529B92E26ADA3FBAE7C01AAC69BBFBFBFDD7AC34";

const CONTROLLED_DEMO_GENERATOR_ID = "dm2c-component-subgraph-v2:task12-controlled-closure-v1";
const CONTROLLED_DEMO_SOURCES = Object.freeze({
  controlledFixture: Object.freeze({
    path: "tests/task10_v2_fixture.py",
    sha256: "96E74DA04AAF56D6927F7E364FB2A0A3C9C45143E0C35446A05254C4C634C555",
  }),
  geometry: Object.freeze({
    path: "dm2c_frontend/public/demo-ifc-geometry.json",
    sha256: "3A8E91B47D7AA2894421215E379897BA2512EBE04B9E801C6DC4108F075077F5",
  }),
  "typed_completed.ifc": Object.freeze({
    path: "typed_completed.ifc",
    sha256: "9934A332500BCBDA7EA40835803546982926F99B91CC84F229D59721807B937B",
  }),
});

const CONTROLLED_DEMO_OPTIONAL_RELATIONS = Object.freeze([
  "associatedWithProcess",
  "directlyPrecedes",
  "recordedForResource",
]);

const CONTROLLED_DEMO_COMPONENT_ORDER = Object.freeze([
  "3czbugqbT86PTcmnme1im9",
  "3czbugqbT86PTcmnme1imB",
  "3czbugqbT86PTcmnme1im5",
  "3czbugqbT86PTcmnme1im7",
]);

const CONTROLLED_DEMO_COMPONENT_NAMES = Object.freeze({
  "3czbugqbT86PTcmnme1im9": "Steel beam B1 / 2134884",
  "3czbugqbT86PTcmnme1imB": "Steel beam B1 / 2134886",
  "3czbugqbT86PTcmnme1im5": "Timber beam B3 / 2134888",
  "3czbugqbT86PTcmnme1im7": "Timber beam B1a / 2134890",
});

export function isCanonicalGraphAvailable(graphInfo) {
  return graphInfo?.canonicalGraphAvailable === true
    && graphInfo?.schemaVersion === PROJECT_GRAPH_SCHEMA.schemaVersion;
}

export function isCanonicalQueryAvailable(graphInfo) {
  return isCanonicalGraphAvailable(graphInfo)
    && graphInfo?.canonicalQueryAvailable === true;
}

const FORBIDDEN_GRAPH_LABELS = new Set([
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
]);

const EXCLUDED_FACTORY_COMPONENT_REFINEMENTS = new Set([
  "IfcFurniture",
  "IfcAlarm",
  "IfcFireSuppressionTerminal",
  "IfcAirTerminal",
  "IfcValve",
]);

const FORBIDDEN_GRAPH_RELATIONS = new Set([
  "EMISSION_OF",
  "HAS_CARBON_DRIVER",
  "HAS_CONSUMPTION_QUANTITY",
  "HAS_EMISSION_FACTOR",
  "HAS_PRODUCT_TYPE",
  "USES_ENERGY_CARRIER",
  "RELATED_TO_QUANTITY",
]);

const PRINCIPAL_RELATIONS = new Set(PRINCIPAL_TRIPLES.map(({ relation }) => relation));

const toNumber = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

const round = (value) => {
  const number = toNumber(value);
  return number == null ? null : Math.round(number * 1000) / 1000;
};

const sourceName = (row) => row?.name || row?.componentName || row?.id || "Unspecified";

export function normalizeSelectionIds(value) {
  const source = Array.isArray(value) ? value : [value];
  const seen = new Set();
  return source.reduce((ids, item) => {
    const id = String(item || "").trim();
    if (!id || seen.has(id)) return ids;
    seen.add(id);
    ids.push(id);
    return ids;
  }, []);
}

export function primarySelectionId(value) {
  const ids = normalizeSelectionIds(value);
  return ids.length ? ids[ids.length - 1] : null;
}

export function updateSelectionIds(currentIds, clickedId, compareMode = false) {
  const current = normalizeSelectionIds(currentIds);
  const id = String(clickedId || "").trim();
  if (!id) return current;
  if (!compareMode) return [id];
  return current.includes(id) ? current.filter((value) => value !== id) : [...current, id];
}

export function buildSelectionContext(selectedIds) {
  const ids = normalizeSelectionIds(selectedIds);
  return {
    selected_component_ids: ids,
    scope: ids.length ? "selected_component" : "project",
  };
}

export function buildSelectionTags(results, selectedIds) {
  const rowsById = new Map(
    (Array.isArray(results) ? results : [])
      .filter((row) => row?.id)
      .map((row) => [String(row.id), row]),
  );

  return normalizeSelectionIds(selectedIds).map((id) => {
    const row = rowsById.get(id);
    return {
      id,
      label: String(row?.name || row?.componentName || id),
      type: String(row?.type || row?.ifcType || ""),
    };
  });
}

export function resolveSelectedComponent(selectionSources, selectedId, componentSubgraph) {
  const id = String(selectedId || "").trim();
  if (!id) return null;
  const selected = (Array.isArray(selectionSources) ? selectionSources : [])
    .find((row) => String(row?.id || "") === id);
  if (selected) return selected;

  const root = (componentSubgraph?.nodes || [])
    .find((node) => node?.id === componentSubgraph?.rootNodeId && node?.labels?.includes("BuildingComponent"));
  if (!root) return null;
  const props = root.props || {};
  const ifcLabel = root.labels.find((label) => /^Ifc/.test(label) && label !== "IfcMaterial") || "";
  return {
    id,
    name: String(props.name || props.componentName || props.globalId || props.ifcGlobalId || id),
    type: String(props.ifcClass || props.ifcType || ifcLabel),
  };
}

const GRAPH_ROLE_ORDER = ["context", "component", "consumption", "emission", "evidence", "process"];
const GRAPH_ROLE_X = {
  context: 40,
  component: 210,
  consumption: 400,
  emission: 590,
  evidence: 780,
  process: 970,
};

function graphNodeRole(labels) {
  const set = new Set(labels);
  if (set.has("BuildingComponent") || set.has("ComponentType")) return "component";
  if (set.has("CarbonEmission")) return "emission";
  if (set.has("MaterialConsumption") || set.has("EnergyConsumption")) return "consumption";
  if (["IfcMaterial", "EmissionFactor", "ConsumptionQuantity", "DesignQuantity", "EnergyCarrier"].some((label) => set.has(label))) return "evidence";
  if (["ManufacturingProcessTemplate", "ProductionStage", "ManufacturingActivity", "ManufacturingResource"].some((label) => set.has(label))) return "process";
  return "context";
}

function graphPrimaryLabel(labels) {
  return APPLICATION_CLASSES.find((label) => labels.includes(label)) || labels[0] || "GraphNode";
}

function graphNodeTitle(id, props) {
  return String(
    props.name ||
    props.componentName ||
    props.materialName ||
    props.sourceName ||
    props.factorName ||
    props.title ||
    id,
  );
}

function valueWithUnit(value, unit) {
  const number = round(value);
  return number == null ? "" : `${number}${unit ? ` ${unit}` : ""}`;
}

function graphNodeDetail(labels, props) {
  const set = new Set(labels);
  if (set.has("CarbonEmission")) {
    const value = valueWithUnit(props.emissionValue ?? props.value, props.emissionUnit || props.unit || "kgCO2e");
    return props.isValidZero === true && toNumber(props.emissionValue ?? props.value) === 0
      ? `${value} | valid zero`
      : value;
  }
  if (set.has("EmissionFactor")) {
    return valueWithUnit(props.factorValue, props.factorUnit);
  }
  if (set.has("DesignQuantity") || set.has("ConsumptionQuantity")) {
    return valueWithUnit(
      props.quantityValue ?? props.normalizedValue ?? props.sourceQuantityValue ?? props.value,
      props.quantityUnit || props.normalizedUnit || props.sourceQuantityUnit || props.unit,
    );
  }
  if (set.has("BuildingComponent")) {
    return String(props.globalId || props.ifcGlobalId || props.ifcClass || props.ifcType || "");
  }
  if (set.has("EnergyConsumption")) {
    return [props.attributionMode, props.recordId].filter((value) => value !== null && value !== undefined && value !== "").join(" | ");
  }
  if (set.has("MaterialConsumption")) {
    return [props.formulaCode, props.recordId].filter((value) => value !== null && value !== undefined && value !== "").join(" | ");
  }
  if (set.has("IfcMaterial")) {
    return String(props.ifcStepId ?? props.materialId ?? "");
  }
  if (set.has("EnergyCarrier")) {
    return String(props.carrierId || props.name || "");
  }
  return String(props.recordId || props.sourceRecordId || props.ifcStepId || "");
}

export function normalizeComponentSubgraph(payload) {
  const source = payload?.subgraph || payload || {};
  if (source.schemaVersion !== PROJECT_GRAPH_SCHEMA.schemaVersion) {
    throw new Error(`Expected ${PROJECT_GRAPH_SCHEMA.schemaVersion} component subgraph`);
  }
  const rootNodeId = String(source.rootNodeId || "");
  if (!rootNodeId) throw new Error("component subgraph rootNodeId is required");
  const nodes = (Array.isArray(source.nodes) ? source.nodes : [])
    .filter((node) => node?.id)
    .map((node) => {
      const id = String(node.id);
      const labels = (Array.isArray(node.labels) ? node.labels : []).map(String);
      const forbiddenLabel = labels.find((label) => FORBIDDEN_GRAPH_LABELS.has(label));
      if (forbiddenLabel) throw new Error(`Forbidden canonical-v2 label: ${forbiddenLabel}`);
      const excludedFactoryType = labels.find((label) => EXCLUDED_FACTORY_COMPONENT_REFINEMENTS.has(label));
      if (excludedFactoryType) throw new Error(`Excluded factory component refinement: ${excludedFactoryType}`);
      const applicationClasses = labels.filter((label) => APPLICATION_CLASS_SET.has(label));
      if (applicationClasses.length !== 1) {
        throw new Error(`Node ${id} must have exactly one canonical application class`);
      }
      const props = node.props && typeof node.props === "object" ? { ...node.props } : {};
      const isRoot = id === rootNodeId;
      return {
        id,
        labels,
        props,
        role: graphNodeRole(labels),
        primaryLabel: graphPrimaryLabel(labels),
        title: graphNodeTitle(id, props),
        detail: graphNodeDetail(labels, props),
        validZero: labels.includes("CarbonEmission") && props.isValidZero === true && toNumber(props.emissionValue ?? props.value) === 0,
        isRoot,
      };
    })
    .sort((left, right) => left.id.localeCompare(right.id));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const nodeClassById = new Map(nodes.map((node) => [
    node.id,
    node.labels.find((label) => APPLICATION_CLASS_SET.has(label)),
  ]));
  const root = nodes.find((node) => node.id === rootNodeId);
  if (!root || !root.labels.includes("BuildingComponent")) {
    throw new Error("component subgraph root must be one BuildingComponent");
  }

  const edgesByIdentity = new Map();
  (Array.isArray(source.edges) ? source.edges : []).forEach((edge) => {
    const src = String(edge?.src || "");
    const tgt = String(edge?.tgt || "");
    if (!src || !tgt) throw new Error("component subgraph edge endpoints are required");
    if (!nodeIds.has(src) || !nodeIds.has(tgt)) throw new Error(`component subgraph has dangling edge ${src} -> ${tgt}`);
    const type = String(edge?.type || "");
    if (!type) throw new Error("component subgraph edge type is required");
    if (FORBIDDEN_GRAPH_RELATIONS.has(type)) throw new Error(`Forbidden canonical-v2 relation: ${type}`);
    if (!PRINCIPAL_RELATIONS.has(type)) throw new Error(`Unknown canonical-v2 relation: ${type}`);
    const typedTriple = `${nodeClassById.get(src)}|${type}|${nodeClassById.get(tgt)}`;
    if (!PRINCIPAL_TRIPLE_KEYS.has(typedTriple)) {
      throw new Error(`Invalid canonical-v2 typed triple: ${typedTriple}`);
    }
    const props = edge.props && typeof edge.props === "object" ? { ...edge.props } : {};
    const occurrenceId = String(edge.occurrenceId ?? props.occurrenceId ?? "");
    const explicitId = edge.id === null || edge.id === undefined ? "" : String(edge.id);
    if (!explicitId && !occurrenceId) throw new Error("component subgraph edge id or occurrenceId is required");
    const id = explicitId || `${src}|${type}|${tgt}|${occurrenceId}`;
    const identity = occurrenceId ? `occurrence:${occurrenceId}` : `id:${id}`;
    const normalized = { id, occurrenceId, src, type, tgt, props };
    const prior = edgesByIdentity.get(identity);
    if (prior && JSON.stringify(prior) !== JSON.stringify(normalized)) {
      throw new Error(`component subgraph edge occurrence conflicts: ${occurrenceId || id}`);
    }
    if (!prior) edgesByIdentity.set(identity, normalized);
  });
  const edges = [...edgesByIdentity.values()].sort((left, right) => (
    left.src.localeCompare(right.src) ||
    left.type.localeCompare(right.type) ||
    left.tgt.localeCompare(right.tgt) ||
    left.occurrenceId.localeCompare(right.occurrenceId) ||
    left.id.localeCompare(right.id)
  ));
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const emissionByConsumption = new Map();
  for (const edge of edges) {
    if (edge.type !== "hasCarbonDriver") continue;
    const emission = nodeById.get(edge.src);
    if (emission?.labels.includes("CarbonEmission")) emissionByConsumption.set(edge.tgt, emission);
  }
  for (const edge of edges) {
    if (edge.type !== "recordedForObject" || edge.props.attributionMode !== "allocated") continue;
    const emission = emissionByConsumption.get(edge.src);
    const sourceEmissionValue = toNumber(emission?.props?.emissionValue);
    const normalizedWeight = toNumber(edge.props.normalizedWeight);
    edge.allocation = {
      sourceEmissionValue,
      sourceEmissionUnit: String(emission?.props?.emissionUnit || "kgCO2e"),
      componentContributionValue: sourceEmissionValue == null || normalizedWeight == null
        ? null
        : sourceEmissionValue * normalizedWeight,
      allocationSetId: edge.props.allocationSetId,
      allocationBasis: edge.props.allocationBasis,
      rawWeight: edge.props.rawWeight,
      rawWeightUnit: edge.props.rawWeightUnit,
      normalizedWeight: edge.props.normalizedWeight,
      evidenceRecordId: edge.props.evidenceRecordId,
    };
  }
  return {
    schemaVersion: PROJECT_GRAPH_SCHEMA.schemaVersion,
    rootNodeId,
    boundary: String(source.boundary || ""),
    truncated: Boolean(source.truncated),
    counts: source.counts && typeof source.counts === "object" ? { ...source.counts } : {},
    nodes,
    edges,
  };
}

export function buildComponentSubgraphLayout(subgraph) {
  const graph = subgraph || { nodes: [], edges: [] };
  const grouped = new Map(GRAPH_ROLE_ORDER.map((role) => [role, []]));
  for (const node of graph.nodes || []) {
    const role = grouped.has(node.role) ? node.role : "context";
    grouped.get(role).push(node);
  }
  grouped.forEach((nodes) => nodes.sort((left, right) => left.id.localeCompare(right.id)));

  const positioned = [];
  for (const role of GRAPH_ROLE_ORDER) {
    grouped.get(role).forEach((node, index) => {
      positioned.push({
        ...node,
        x: GRAPH_ROLE_X[role],
        y: 42 + index * 76,
        width: 144,
        height: 54,
      });
    });
  }
  const nodeById = new Map(positioned.map((node) => [node.id, node]));
  const edges = (graph.edges || [])
    .map((edge) => ({ ...edge, source: nodeById.get(edge.src), target: nodeById.get(edge.tgt) }))
    .filter((edge) => edge.source && edge.target);
  const largestColumn = Math.max(0, ...[...grouped.values()].map((nodes) => nodes.length));
  return {
    width: 1160,
    height: Math.max(240, 76 * largestColumn + 56),
    nodes: positioned,
    edges,
  };
}

export function buildComponentSubgraphPath(projectId, globalId) {
  return `/api/projects/${encodeURIComponent(String(projectId || ""))}/components/${encodeURIComponent(
    String(globalId || ""),
  )}/subgraph?max_nodes=80`;
}

const coverageCountMap = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  if (entries.some(([key, count]) => !key || !Number.isInteger(Number(count)) || Number(count) < 0)) return null;
  return Object.fromEntries(
    entries
      .map(([key, count]) => [String(key), Number(count)])
      .sort(([left], [right]) => left.localeCompare(right)),
  );
};

export function normalizeExternalValidationCoverage(value) {
  const source = value || {};
  const coverage = source.externalValidationCoverage
    || source.metadata?.externalValidationCoverage
    || source.manifest?.coverage
    || source.coverage
    || source.stats?.validation
    || null;
  if (!coverage || typeof coverage !== "object" || Array.isArray(coverage)) return null;
  const candidateCount = toNumber(coverage.candidateCount);
  const acceptedCount = toNumber(coverage.acceptedCount);
  const rejectedCount = toNumber(coverage.rejectedCount);
  const coverageValue = toNumber(coverage.coverageValue);
  if (![candidateCount, acceptedCount, rejectedCount].every((count) => Number.isInteger(count) && count >= 0)) return null;
  if (candidateCount !== acceptedCount + rejectedCount || coverageValue == null || coverageValue < 0 || coverageValue > 1) return null;
  if (coverage.coverageFormula !== "acceptedCount / candidateCount") return null;
  const acceptedByKind = coverageCountMap(coverage.acceptedByKind);
  const rejectedByKind = coverageCountMap(coverage.rejectedByKind);
  const rejectedByReason = coverageCountMap(coverage.rejectedByReason);
  if (!acceptedByKind || !rejectedByKind || !rejectedByReason) return null;
  const sumCounts = (counts) => Object.values(counts).reduce((sum, count) => sum + count, 0);
  const expectedCoverage = candidateCount === 0 ? 0 : acceptedCount / candidateCount;
  if (Math.abs(coverageValue - expectedCoverage) > 1e-12) return null;
  if (sumCounts(acceptedByKind) !== acceptedCount) return null;
  if (sumCounts(rejectedByKind) !== rejectedCount) return null;
  if (sumCounts(rejectedByReason) !== rejectedCount) return null;
  return {
    candidateCount,
    acceptedCount,
    rejectedCount,
    acceptedByKind,
    rejectedByKind,
    rejectedByReason,
    coverageFormula: coverage.coverageFormula,
    coverageValue,
  };
}

const sameCountMap = (left, right) => JSON.stringify(left) === JSON.stringify(right);

const incrementCount = (counts, key) => {
  counts[key] = (counts[key] || 0) + 1;
};

function validateControlledSubgraphStatistics(globalId, subgraph) {
  const reportedLabels = coverageCountMap(subgraph.counts.labels);
  const reportedRelations = coverageCountMap(subgraph.counts.relations);
  const actualLabels = {};
  const actualRelations = {};
  for (const node of subgraph.nodes) {
    for (const label of node.labels) incrementCount(actualLabels, label);
  }
  for (const edge of subgraph.edges) incrementCount(actualRelations, edge.type);
  const normalizedLabels = coverageCountMap(actualLabels);
  const normalizedRelations = coverageCountMap(actualRelations);
  if (
    subgraph.counts.nodes !== subgraph.nodes.length
    || subgraph.counts.edges !== subgraph.edges.length
    || !reportedLabels
    || !reportedRelations
    || !sameCountMap(reportedLabels, normalizedLabels)
    || !sameCountMap(reportedRelations, normalizedRelations)
  ) {
    throw new Error(`Controlled demo subgraph statistics are inconsistent: ${globalId}`);
  }
}

function validateControlledSubgraphEmissionSemantics(globalId, subgraph) {
  const nodeById = new Map(subgraph.nodes.map((node) => [node.id, node]));
  const outgoing = (sourceId, relation) => subgraph.edges.filter(
    (edge) => edge.src === sourceId && edge.type === relation,
  );
  const incoming = (targetId, relation) => subgraph.edges.filter(
    (edge) => edge.tgt === targetId && edge.type === relation,
  );
  const consumptions = subgraph.nodes.filter((node) => (
    node.labels.includes("MaterialConsumption") || node.labels.includes("EnergyConsumption")
  ));

  for (const consumption of consumptions) {
    const generated = incoming(consumption.id, "hasCarbonDriver");
    const quantityEdges = outgoing(consumption.id, "hasQuantity");
    const factorEdges = outgoing(consumption.id, "hasFactor");
    if (generated.length !== 1 || quantityEdges.length !== 1 || factorEdges.length !== 1) {
      throw new Error(`Controlled demo emission semantics are invalid: ${globalId}`);
    }

    const emission = nodeById.get(generated[0].src);
    const quantity = nodeById.get(quantityEdges[0].tgt);
    const factor = nodeById.get(factorEdges[0].tgt);
    if (
      !emission?.labels.includes("CarbonEmission")
      || !quantity?.labels.includes("ConsumptionQuantity")
      || !factor?.labels.includes("EmissionFactor")
    ) {
      throw new Error(`Controlled demo emission semantics are invalid: ${globalId}`);
    }

    const emissionValue = toNumber(emission.props.emissionValue);
    const quantityValue = toNumber(quantity.props.quantityValue);
    const factorValue = toNumber(factor.props.normalizedFactorValue ?? factor.props.factorValue);
    const expectedEmission = quantityValue == null || factorValue == null
      ? null
      : quantityValue * factorValue;
    const tolerance = expectedEmission == null ? 0 : Math.max(1, Math.abs(expectedEmission)) * 1e-12;
    const validZero = expectedEmission === 0;
    if (
      emissionValue == null
      || expectedEmission == null
      || Math.abs(emissionValue - expectedEmission) > tolerance
      || (emission.props.consumptionId && emission.props.consumptionId !== consumption.id)
      || (consumption.props.emissionId && consumption.props.emissionId !== emission.id)
      || (consumption.props.quantityId && consumption.props.quantityId !== quantity.id)
      || (consumption.props.factorId && consumption.props.factorId !== factor.id)
      || (quantity.props.consumptionId && quantity.props.consumptionId !== consumption.id)
      || toNumber(emission.props.quantityValue) !== quantityValue
      || toNumber(emission.props.factorValue) !== factorValue
      || emission.props.isValidZero !== validZero
      || consumption.props.isValidZero !== validZero
      || quantity.props.isValidZero !== validZero
    ) {
      throw new Error(`Controlled demo emission semantics are invalid: ${globalId}`);
    }
  }
}

export function validateControlledDemoArtifact(artifact, artifactSha256) {
  if (String(artifactSha256 || "").toUpperCase() !== CONTROLLED_DEMO_ARTIFACT_SHA256) {
    throw new Error("Controlled demo artifact integrity check failed");
  }
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) {
    throw new Error("Controlled demo artifact must be one JSON object");
  }
  if (artifact.schemaVersion !== PROJECT_GRAPH_SCHEMA.schemaVersion) {
    throw new Error(`Controlled demo artifact must use ${PROJECT_GRAPH_SCHEMA.schemaVersion}`);
  }

  const metadata = artifact.metadata;
  if (!metadata || metadata.notForActualRelease !== true || metadata.controlledFixtureProfile !== "task12-controlled-component-closure") {
    throw new Error("Controlled demo provenance metadata is missing or invalid");
  }
  if (metadata.generatorId !== CONTROLLED_DEMO_GENERATOR_ID) {
    throw new Error("Controlled demo generator identity is missing or invalid");
  }
  for (const [key, expected] of Object.entries(CONTROLLED_DEMO_SOURCES)) {
    const source = metadata.sources?.[key];
    if (source?.path !== expected.path || String(source?.sha256 || "").toUpperCase() !== expected.sha256) {
      throw new Error(`Controlled demo provenance source is invalid: ${key}`);
    }
  }

  const stats = artifact.stats;
  if (!stats || stats.schemaVersion !== PROJECT_GRAPH_SCHEMA.schemaVersion) {
    throw new Error("Controlled demo statistics are missing or use the wrong schema");
  }
  const classCounts = coverageCountMap(stats.applicationClassCounts);
  const relationCounts = coverageCountMap(stats.relationCounts);
  const expectedClassKeys = [...PROJECT_GRAPH_SCHEMA.applicationClasses]
    .sort((left, right) => left.localeCompare(right));
  if (!classCounts || JSON.stringify(Object.keys(classCounts)) !== JSON.stringify(expectedClassKeys)) {
    throw new Error("Controlled demo statistics do not contain the exact application-class inventory");
  }
  const expectedRelationKeys = [
    ...new Set([
      ...PROJECT_GRAPH_SCHEMA.principalTriples.map(({ relation }) => relation),
      ...CONTROLLED_DEMO_OPTIONAL_RELATIONS,
    ]),
  ].sort((left, right) => left.localeCompare(right));
  if (!relationCounts || JSON.stringify(Object.keys(relationCounts)) !== JSON.stringify(expectedRelationKeys)) {
    throw new Error("Controlled demo statistics do not contain the exact relation inventory");
  }
  const nodeCount = toNumber(stats.nodeCount);
  const edgeCount = toNumber(stats.edgeCount);
  const sumCounts = (counts) => Object.values(counts).reduce((sum, count) => sum + count, 0);
  if (!Number.isInteger(nodeCount) || nodeCount < 0 || sumCounts(classCounts) !== nodeCount) {
    throw new Error("Controlled demo node statistics are inconsistent");
  }
  if (!relationCounts || !Number.isInteger(edgeCount) || edgeCount < 0 || sumCounts(relationCounts) !== edgeCount) {
    throw new Error("Controlled demo edge statistics are inconsistent");
  }

  const metadataCoverage = normalizeExternalValidationCoverage({
    coverage: metadata.externalValidationCoverage,
  });
  const statsCoverage = normalizeExternalValidationCoverage({ coverage: stats.validation });
  if (!metadataCoverage || !statsCoverage || JSON.stringify(metadataCoverage) !== JSON.stringify(statsCoverage)) {
    throw new Error("Controlled demo external validation coverage is missing or inconsistent");
  }

  const subgraphs = artifact.subgraphs;
  const entries = subgraphs && typeof subgraphs === "object" && !Array.isArray(subgraphs)
    ? Object.entries(subgraphs)
    : [];
  if (entries.length !== 4) {
    throw new Error("Controlled demo must contain exactly four component subgraphs");
  }
  for (const [globalId, rawSubgraph] of entries) {
    const subgraph = normalizeComponentSubgraph(rawSubgraph);
    const root = subgraph.nodes.find((node) => node.id === subgraph.rootNodeId);
    if (String(root?.props?.globalId || "") !== globalId) {
      throw new Error(`Controlled demo subgraph root provenance mismatch: ${globalId}`);
    }
    validateControlledSubgraphStatistics(globalId, subgraph);
    validateControlledSubgraphEmissionSemantics(globalId, subgraph);
  }
  return artifact;
}

const recordValue = (value) => (
  value && typeof value === "object" && !Array.isArray(value) ? value : {}
);

export function normalizeProjectGraphInfo(data) {
  const source = recordValue(data);
  const payloadCandidate = recordValue(source.payload);
  const resultCandidate = recordValue(source.result);
  const payload = Object.keys(payloadCandidate).length
    ? payloadCandidate
    : (Object.keys(resultCandidate).length ? resultCandidate : source);
  const nestedPayload = payload === source ? {} : payload;
  const dataInputs = recordValue(source.inputs);
  const payloadInputs = recordValue(nestedPayload.inputs);
  const inputs = { ...dataInputs, ...payloadInputs };
  const dataGraphInfo = recordValue(source.graphInfo);
  const payloadGraphInfo = recordValue(nestedPayload.graphInfo);
  const dataBackbone = recordValue(dataInputs.backboneBuild);
  const payloadBackbone = recordValue(payloadInputs.backboneBuild);

  const metadata = {
    ...recordValue(source.metadata),
    ...recordValue(dataGraphInfo.metadata),
    ...recordValue(nestedPayload.metadata),
    ...recordValue(payloadGraphInfo.metadata),
  };
  const manifest = {
    ...recordValue(source.manifest),
    ...recordValue(dataGraphInfo.manifest),
    ...recordValue(nestedPayload.manifest),
    ...recordValue(payloadGraphInfo.manifest),
  };
  const backboneStats = {
    ...recordValue(dataBackbone.stats),
    ...recordValue(payloadBackbone.stats),
    ...recordValue(source.backboneStats),
    ...recordValue(dataGraphInfo.backboneStats),
    ...recordValue(nestedPayload.backboneStats),
    ...recordValue(payloadGraphInfo.backboneStats),
  };
  const stats = {
    ...recordValue(source.stats),
    ...recordValue(dataGraphInfo.stats),
    ...recordValue(nestedPayload.stats),
    ...recordValue(payloadGraphInfo.stats),
  };

  return {
    graph: inputs.graph || "",
    ifcSource: inputs.ifcSource || "",
    factorWorkbook: inputs.factorWorkbook || "",
    processDocs: inputs.processDocs || "",
    llmProvider: inputs.llmProvider || "",
    model: inputs.model || "",
    materialFactorRows: toNumber(inputs.materialFactorRows) ?? 0,
    energyFactorRows: toNumber(inputs.energyFactorRows) ?? 0,
    processEvidenceRows: toNumber(inputs.processEvidenceRows) ?? 0,
    elapsedMs: toNumber(inputs.elapsedMs),
    ...dataGraphInfo,
    ...payloadGraphInfo,
    schemaVersion:
      payloadGraphInfo.schemaVersion
      || nestedPayload.schemaVersion
      || dataGraphInfo.schemaVersion
      || source.schemaVersion
      || "",
    metadata,
    manifest,
    stats,
    backboneStats,
  };
}

export function selectDemoComponentSubgraph(artifact, globalId) {
  const subgraphs = artifact?.subgraphs;
  if (!subgraphs || typeof subgraphs !== "object") return null;
  return subgraphs[String(globalId || "")] || null;
}

const ACCOUNT_VIEWS = ["product", "material", "process"];

export function normalizeCarbonAccounts(data) {
  const source = recordValue(data);
  const candidates = [
    recordValue(source.carbonAccounts),
    recordValue(recordValue(source.payload).carbonAccounts),
    recordValue(recordValue(source.result).carbonAccounts),
  ];
  const accounts = candidates.find((candidate) =>
    ACCOUNT_VIEWS.some((view) => Array.isArray(candidate[view])),
  );
  if (!accounts) return null;
  const normalized = {};
  for (const view of ACCOUNT_VIEWS) {
    const rows = accounts[view];
    if (!Array.isArray(rows)) continue;
    normalized[view] = rows.map((row, index) => ({
      id: String(row.id || `${view}-${index}`),
      name: String(row.name || row.id || ""),
      context: String(row.context || ""),
      materialKgCO2e: toNumber(row.materialKgCO2e),
      processKgCO2e: toNumber(row.processKgCO2e),
      totalKgCO2e: toNumber(row.totalKgCO2e),
      status: String(row.status || "measured"),
    }));
  }
  return Object.keys(normalized).length ? normalized : null;
}

export function buildAccountRows(carbonAccounts, perspective = "product") {
  const rows = carbonAccounts?.[perspective];
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => ({
    id: row.id,
    primary: row.name,
    secondary: row.context,
    knownMaterial: round(row.materialKgCO2e),
    knownProcess: round(row.processKgCO2e),
    knownTotal: round(row.totalKgCO2e),
    status: row.status,
    source: row,
  }));
}

export function buildPerspectiveRows(results, perspective = "product") {
  const rows = Array.isArray(results) ? results : [];
  if (perspective === "material") return buildMaterialRows(rows);
  if (perspective === "process") return buildProcessRows(rows);
  return rows
    .map((row) => ({
      id: row.id,
      primary: sourceName(row),
      secondary: [row.type, row.material].filter(Boolean).join(" | "),
      knownMaterial: round(row.cMat),
      knownProcess: round(row.cProc),
      knownTotal: round(row.total ?? (row.cMat || 0) + (row.cProc || 0)),
      status: row.gaps?.length ? "incomplete_path" : "executable",
      source: row,
    }))
    .sort((a, b) => (b.knownTotal || 0) - (a.knownTotal || 0));
}

export function buildComponentScene(results, selectedIds, candidateIds = []) {
  const rows = Array.isArray(results) ? results : [];
  const normalizedSelectedIds = normalizeSelectionIds(selectedIds);
  const selectedSet = new Set(normalizedSelectedIds);
  const primarySelectedId = primarySelectionId(normalizedSelectedIds);
  const candidateSet = new Set(Array.isArray(candidateIds) ? candidateIds : []);
  const meshes = rows.map((row, index) => {
    const type = String(row.type || "").toLowerCase();
    const material = String(row.material || "").toLowerCase();
    const selected = selectedSet.has(row.id);
    const candidate = !selected && candidateSet.has(row.id);
    const size = sceneSizeFor(type);
    const rowIndex = Math.floor(index / 3);
    const columnIndex = index % 3;
    const position = [
      round(columnIndex * 1.8),
      round(rowIndex * 0.65 + (index % 2) * 0.5),
      round((rowIndex % 2) * 1.2),
    ];

    return {
      id: row.id,
      label: sourceName(row),
      type: row.type || "Component",
      material: row.material || "Unspecified",
      size,
      position,
      color: selected ? "#E24B4A" : candidate ? "#EF9F27" : sceneColorFor(type, material),
      selected,
      candidate,
      carbon: round(row.total ?? (row.cMat || 0) + (row.cProc || 0)),
      status: row.gaps?.length ? "incomplete_path" : "executable",
    };
  });

  const center = meshes.length
    ? meshes.reduce(
        (acc, mesh) => [
          acc[0] + mesh.position[0] / meshes.length,
          acc[1] + mesh.position[1] / meshes.length,
          acc[2] + mesh.position[2] / meshes.length,
        ],
        [0, 0, 0],
      )
    : [0, 0, 0];
  const radius =
    meshes.reduce((max, mesh) => {
      const dx = mesh.position[0] - center[0];
      const dy = mesh.position[1] - center[1];
      const dz = mesh.position[2] - center[2];
      const diagonal = Math.hypot(...mesh.size) / 2;
      return Math.max(max, Math.hypot(dx, dy, dz) + diagonal);
    }, 1) || 1;
  const primaryMesh = meshes.find((mesh) => mesh.id === primarySelectedId);
  const cameraTarget = primaryMesh ? primaryMesh.position : center.map(round);

  return {
    meshes,
    primarySelectedId,
    bounds: { center: center.map(round), radius: round(radius) },
    camera: {
      position: [
        round(cameraTarget[0] + radius * 2.2),
        round(cameraTarget[1] + radius * 1.55),
        round(cameraTarget[2] + radius * 2.4),
      ],
      target: cameraTarget,
    },
  };
}

function vertexBoundsCenter(vertices) {
  const bounds = {
    min: [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
    max: [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
  };
  for (let index = 0; index < vertices.length; index += 3) {
    for (let axis = 0; axis < 3; axis += 1) {
      const value = toNumber(vertices[index + axis]);
      if (value == null) continue;
      bounds.min[axis] = Math.min(bounds.min[axis], value);
      bounds.max[axis] = Math.max(bounds.max[axis], value);
    }
  }
  if (!bounds.min.every(Number.isFinite) || !bounds.max.every(Number.isFinite)) return null;
  return bounds.min.map((value, axis) => (value + bounds.max[axis]) / 2);
}

function isIfcGeometrySequence(value) {
  return Array.isArray(value) || ArrayBuffer.isView(value);
}

export function buildIfcGeometryScene(geometry, selectedIds, candidateIds = []) {
  const sourceMeshes = Array.isArray(geometry?.meshes) ? geometry.meshes : [];
  const normalizedSelectedIds = normalizeSelectionIds(selectedIds);
  const selectedSet = new Set(normalizedSelectedIds);
  const primarySelectedId = primarySelectionId(normalizedSelectedIds);
  const candidateSet = new Set(Array.isArray(candidateIds) ? candidateIds : []);
  const bounds = sourceMeshes.reduce(
    (acc, mesh) => {
      const vertices = isIfcGeometrySequence(mesh.vertices) ? mesh.vertices : [];
      for (let index = 0; index < vertices.length; index += 3) {
        const x = toNumber(vertices[index]);
        const y = toNumber(vertices[index + 1]);
        const z = toNumber(vertices[index + 2]);
        if (x == null || y == null || z == null) continue;
        acc.min[0] = Math.min(acc.min[0], x);
        acc.min[1] = Math.min(acc.min[1], y);
        acc.min[2] = Math.min(acc.min[2], z);
        acc.max[0] = Math.max(acc.max[0], x);
        acc.max[1] = Math.max(acc.max[1], y);
        acc.max[2] = Math.max(acc.max[2], z);
      }
      return acc;
    },
    {
      min: [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
      max: [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
    },
  );
  const hasBounds = bounds.min.every(Number.isFinite) && bounds.max.every(Number.isFinite);
  const center = hasBounds
    ? [
        (bounds.min[0] + bounds.max[0]) / 2,
        (bounds.min[1] + bounds.max[1]) / 2,
        (bounds.min[2] + bounds.max[2]) / 2,
      ]
    : [0, 0, 0];
  const radius = hasBounds
    ? Math.max(1, Math.hypot(bounds.max[0] - bounds.min[0], bounds.max[1] - bounds.min[1], bounds.max[2] - bounds.min[2]) / 2)
    : 1;

  const meshes = sourceMeshes
    .filter((mesh) => isIfcGeometrySequence(mesh.vertices) && mesh.vertices.length >= 9 && isIfcGeometrySequence(mesh.indices) && mesh.indices.length >= 3)
    .map((mesh, index) => {
      const id = String(mesh.globalId || mesh.id || `ifc-mesh-${index}`);
      const type = String(mesh.ifcType || mesh.type || "IfcElement");
      const selected = selectedSet.has(id);
      const candidate = !selected && candidateSet.has(id);
      return {
        id,
        label: mesh.name || id,
        type,
        vertices: Array.from(mesh.vertices, (value) => round(value) ?? 0),
        indices: Array.from(mesh.indices, (value) => Number.parseInt(value, 10)).filter(Number.isFinite),
        color: selected ? "#E24B4A" : candidate ? "#EF9F27" : mesh.color || sceneColorFor(type.toLowerCase(), ""),
        materials: Array.isArray(mesh.materials) ? mesh.materials : [],
        materialIds: isIfcGeometrySequence(mesh.materialIds) ? Array.from(mesh.materialIds) : [],
        selected,
        candidate,
      };
    });
  const primarySourceMesh = sourceMeshes.find(
    (mesh, index) => String(mesh.globalId || mesh.id || `ifc-mesh-${index}`) === primarySelectedId,
  );
  const primaryWorldCenter = primarySourceMesh
    ? vertexBoundsCenter(primarySourceMesh.vertices || [])
    : null;
  const cameraTarget = primaryWorldCenter
    ? [
        primaryWorldCenter[0] - center[0],
        primaryWorldCenter[2] - center[2],
        -(primaryWorldCenter[1] - center[1]),
      ].map(round)
    : [0, 0, 0];

  return {
    sourceFile: geometry?.sourceFile || "",
    meshes,
    primarySelectedId,
    bounds: {
      center: center.map(round),
      min: (hasBounds ? bounds.min : [0, 0, 0]).map(round),
      max: (hasBounds ? bounds.max : [0, 0, 0]).map(round),
      radius: round(radius),
    },
    camera: {
      position: [
        round(cameraTarget[0] + radius * 1.7),
        round(cameraTarget[1] + radius * 1.2),
        round(cameraTarget[2] + radius * 2.1),
      ],
      target: cameraTarget,
    },
  };
}

function sceneSizeFor(type) {
  if (type.includes("beam") || type.includes("member")) return [2.4, 0.32, 0.32];
  if (type.includes("column")) return [0.34, 1.8, 0.34];
  if (type.includes("slab") || type.includes("plate")) return [1.6, 0.16, 1.1];
  if (type.includes("wall") || type.includes("panel")) return [1.6, 1.0, 0.18];
  if (type.includes("module")) return [1.8, 1.0, 1.2];
  return [0.9, 0.55, 0.55];
}

function sceneColorFor(type, material) {
  if (material.includes("steel")) return "#378ADD";
  if (material.includes("concrete")) return "#B8B6AD";
  if (material.includes("wood") || material.includes("timber")) return "#9C6B3A";
  if (type.includes("beam") || type.includes("member")) return "#378ADD";
  if (type.includes("slab") || type.includes("plate")) return "#B8B6AD";
  if (type.includes("wall") || type.includes("panel")) return "#0F6E56";
  if (type.includes("column")) return "#EF9F27";
  if (type.includes("window") || type.includes("door")) return "#7A86A1";
  if (type.includes("module")) return "#0F6E56";
  return "#534AB7";
}

function buildMaterialRows(rows) {
  const groups = new Map();
  rows.forEach((row) => {
    const key = row.material || "Unspecified material";
    const group = groups.get(key) || {
      id: `material:${key}`,
      primary: key,
      secondary: "Material family/account projection",
      knownMaterial: 0,
      knownProcess: 0,
      knownTotal: 0,
      count: 0,
      status: "executable",
    };
    group.count += 1;
    group.knownMaterial += row.cMat || 0;
    group.knownProcess += row.cProc || 0;
    group.knownTotal += row.total ?? (row.cMat || 0) + (row.cProc || 0);
    if (row.gaps?.length) group.status = "incomplete_path";
    groups.set(key, group);
  });
  return [...groups.values()]
    .map((row) => ({
      ...row,
      knownMaterial: round(row.knownMaterial),
      knownProcess: round(row.knownProcess),
      knownTotal: round(row.knownTotal),
      secondary: `${row.count} component${row.count === 1 ? "" : "s"}`,
    }))
    .sort((a, b) => (b.knownMaterial || 0) - (a.knownMaterial || 0));
}

function buildProcessRows(rows) {
  const processRows = [];
  rows.forEach((row) => {
    const steps = Array.isArray(row.process) ? row.process : [];
    if (!steps.length) {
      processRows.push({
        id: `${row.id}:process:none`,
        primary: "No process evidence",
        secondary: sourceName(row),
        knownMaterial: null,
        knownProcess: round(row.cProc),
        knownTotal: round(row.cProc),
        status: row.processStatus || "incomplete_path",
        source: row,
      });
      return;
    }
    steps.forEach((step, index) => {
      processRows.push({
        id: `${row.id}:process:${index}`,
        primary: step.step || "Manufacturing activity",
        secondary: sourceName(row),
        knownMaterial: null,
        knownProcess: round(step.co2e ?? (index === 0 ? row.cProc : null)),
        knownTotal: round(step.co2e ?? (index === 0 ? row.cProc : null)),
        status: step.status || row.processStatus || "evidence",
        required: step.required || "",
        source: row,
      });
    });
  });
  return processRows.sort((a, b) => (b.knownProcess || 0) - (a.knownProcess || 0));
}

export function statusBadge(status) {
  const value = String(status || "executable");
  if (value === "executable" || value === "measured" || value === "complete") {
    return { label: value, tone: "good" };
  }
  if (
    value === "clarification_required" ||
    value === "empty_result" ||
    value === "evidence" ||
    // One carbon source reached the row and the other did not. That is a
    // coverage gap worth flagging, not a failed row.
    value === "material_only" ||
    value === "process_only"
  ) {
    return { label: value, tone: "warn" };
  }
  return { label: value, tone: "bad" };
}

export function extractClarificationCandidates(payload) {
  const status = payload?.queryResult?.status || payload?.status;
  if (status !== "clarification_required") return [];
  const components =
    payload?.queryResult?.candidates ||
    payload?.referenceGrounding?.candidates ||
    payload?.requestContext?.selectedComponents ||
    payload?.requestContext?.selected_components ||
    payload?.queryResult?.candidates ||
    payload?.candidates ||
    [];
  return (Array.isArray(components) ? components : [])
    .map((candidate, index) => ({
      id: candidate.globalId || candidate.componentGlobalId || candidate.componentNodeId || candidate.id || "",
      label: candidate.name || candidate.componentName || candidate.globalId || `Candidate ${index + 1}`,
      type: candidate.ifcType || candidate.type || "",
      material: candidate.material || candidate.materialText || "",
      storey: candidate.storey || "",
      score: toNumber(candidate.score),
      matchedConstraints: candidate.matchedConstraints || candidate.matched_constraints || [],
      raw: candidate,
    }))
    .filter((candidate) => candidate.id);
}

export function extractAuditTrail(payload) {
  const query = payload?.queryResult || {};
  const requirement = payload?.carbonRequirement || {};
  const trace = Array.isArray(payload?.reasoningTrace) ? payload.reasoningTrace : [];
  const summary = query.summary || {};
  const grounding = payload?.referenceGrounding || {};
  const scope = payload?.requestContext?.scope || "project";
  const evidenceChain = [];
  if (query.semanticPath) evidenceChain.push({ label: "Semantic path", value: query.semanticPath });
  if (grounding.decision) {
    evidenceChain.push({ label: "Reference decision", value: grounding.decision });
  }
  if (Array.isArray(grounding.candidates) && grounding.candidates.length) {
    evidenceChain.push({ label: "Grounding candidates", value: String(grounding.candidates.length) });
  }
  if (Array.isArray(grounding.committedIds) && grounding.committedIds.length) {
    evidenceChain.push({ label: "Committed GlobalId", value: grounding.committedIds.join(", ") });
  }
  if (grounding.reason) {
    evidenceChain.push({ label: "Grounding reason", value: grounding.reason });
  }
  if (summary.knownTotalCarbon_kgCO2e != null) {
    evidenceChain.push({ label: "Known total", value: `${summary.knownTotalCarbon_kgCO2e} kgCO2e` });
  }
  if (summary.totalMaterialCarbon_kgCO2e != null) {
    evidenceChain.push({ label: "Material subtotal", value: `${summary.totalMaterialCarbon_kgCO2e} kgCO2e` });
  }
  if (summary.knownProcessCarbon_kgCO2e != null) {
    evidenceChain.push({ label: "Process subtotal", value: `${summary.knownProcessCarbon_kgCO2e} kgCO2e` });
  }
  if (Array.isArray(query.rows) && query.rows.length) {
    evidenceChain.push({ label: "Evidence rows", value: String(query.rows.length) });
  }
  return {
    status: query.status || payload?.status || "executable",
    perspective: requirement.perspective || query.perspective || "",
    operation: requirement.operation || query.operation || "",
    boundary: `factory-gate modularization / ${scope}`,
    evidenceChain,
    traceEvents: trace.map((event) => ({
      agent: event.agent || "agent",
      action: event.action || event.thought || "step",
      reflection: event.reflection || "",
    })),
  };
}

export function buildAccountEntries(result) {
  if (!result) return [];
  const total = result.total ?? (result.cMat || 0) + (result.cProc || 0);
  return [
    { label: "Material carbon", value: `${round(result.cMat) ?? "-"} kgCO2e`, status: result.materialStatus || "" },
    { label: "Process carbon", value: `${round(result.cProc) ?? "-"} kgCO2e`, status: result.processStatus || "" },
    { label: "Known total", value: `${round(total) ?? "-"} kgCO2e`, status: result.gaps?.length ? "incomplete_path" : "executable" },
    { label: "Quantity basis", value: result.quantityBasis || "-", status: result.quantityBasis ? "available" : "missing" },
    { label: "Factor", value: result.factor || "-", status: result.factor ? "available" : "missing" },
  ];
}

export function derivePendingGrounding(messages) {
  const list = Array.isArray(messages) ? messages : [];
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const message = list[index] || {};
    if (message.role === "assistant" && !message.pendingQuestion) {
      return { pendingQuestion: "", candidateIds: [], candidates: [] };
    }
    const candidates = Array.isArray(message.candidates) ? message.candidates : [];
    if (message.pendingQuestion && candidates.length) {
      return {
        pendingQuestion: message.pendingQuestion,
        candidateIds: candidates.map((candidate) => candidate.id).filter(Boolean),
        candidates,
      };
    }
  }
  return { pendingQuestion: "", candidateIds: [], candidates: [] };
}

const controlledDemoDisplayName = (globalId, root) => (
  CONTROLLED_DEMO_COMPONENT_NAMES[globalId]
  || String(root?.props?.name || root?.props?.globalId || globalId)
);

const controlledDemoMaterialName = (materialNode, factorNode) => {
  const raw = String(
    factorNode?.props?.keyword
    || materialNode?.props?.name
    || materialNode?.props?.materialName
    || "IfcMaterial",
  ).trim();
  return raw ? `${raw.charAt(0).toUpperCase()}${raw.slice(1)}` : "IfcMaterial";
};

export function deriveControlledDemoAccount(artifact) {
  const entries = Object.entries(artifact?.subgraphs || {});
  const order = new Map(CONTROLLED_DEMO_COMPONENT_ORDER.map((globalId, index) => [globalId, index]));
  const results = entries.map(([globalId, rawSubgraph]) => {
    const subgraph = normalizeComponentSubgraph(rawSubgraph);
    const nodeById = new Map(subgraph.nodes.map((node) => [node.id, node]));
    const root = nodeById.get(subgraph.rootNodeId);
    if (!root || String(root.props.globalId || "") !== globalId) {
      throw new Error(`Controlled demo account root mismatch: ${globalId}`);
    }

    const generatedByConsumption = new Map();
    for (const edge of subgraph.edges) {
      if (edge.type === "hasCarbonDriver") generatedByConsumption.set(edge.tgt, nodeById.get(edge.src));
    }

    let materialCarbon = 0;
    let processCarbon = 0;
    let materialNode = null;
    let materialFactor = null;
    let materialQuantity = null;
    const process = [];
    for (const edge of subgraph.edges) {
      if (edge.type !== "recordedForObject" || edge.tgt !== root.id) continue;
      const consumption = nodeById.get(edge.src);
      const emission = generatedByConsumption.get(consumption?.id);
      const sourceValue = toNumber(emission?.props?.emissionValue);
      if (!consumption || sourceValue == null) {
        throw new Error(`Controlled demo account is missing accepted emission evidence: ${globalId}`);
      }
      const attributionMode = String(edge.props.attributionMode || consumption.props.attributionMode || "direct");
      const normalizedWeight = attributionMode === "allocated" ? toNumber(edge.props.normalizedWeight) : 1;
      if (normalizedWeight == null || normalizedWeight < 0) {
        throw new Error(`Controlled demo account allocation is invalid: ${globalId}`);
      }
      const contribution = sourceValue * normalizedWeight;

      if (consumption.labels.includes("MaterialConsumption")) {
        materialCarbon += contribution;
        const materialEdge = subgraph.edges.find((candidate) => (
          candidate.src === consumption.id && candidate.type === "ofMaterial"
        ));
        const factorEdge = subgraph.edges.find((candidate) => (
          candidate.src === consumption.id && candidate.type === "hasFactor"
        ));
        const quantityEdge = subgraph.edges.find((candidate) => (
          candidate.src === consumption.id && candidate.type === "hasQuantity"
        ));
        materialNode = nodeById.get(materialEdge?.tgt) || materialNode;
        materialFactor = nodeById.get(factorEdge?.tgt) || materialFactor;
        materialQuantity = nodeById.get(quantityEdge?.tgt) || materialQuantity;
      } else if (consumption.labels.includes("EnergyConsumption")) {
        processCarbon += contribution;
        process.push({
          step: attributionMode === "allocated" ? "Allocated energy consumption" : "Direct energy consumption",
          status: "measured",
          co2e: round(contribution),
          required: attributionMode === "allocated"
            ? `${round(normalizedWeight * 100)}% of ${round(sourceValue)} kgCO2e source emission`
            : (emission.props.isValidZero === true ? "valid zero" : "direct record"),
        });
      }
    }

    const material = controlledDemoMaterialName(materialNode, materialFactor);
    const factorValue = toNumber(materialFactor?.props?.normalizedFactorValue ?? materialFactor?.props?.factorValue);
    const factorUnit = String(materialFactor?.props?.factorUnit || "");
    const factorSource = String(materialFactor?.props?.source || "controlled fixture");
    const quantityValue = toNumber(materialQuantity?.props?.quantityValue);
    const quantityUnit = String(materialQuantity?.props?.quantityUnit || "");
    const cMat = round(materialCarbon);
    const cProc = round(processCarbon);
    return {
      id: globalId,
      name: controlledDemoDisplayName(globalId, root),
      type: String(root.props.ifcClass || root.labels.find((label) => /^Ifc/.test(label)) || "BuildingComponent"),
      material,
      storey: globalId === "3czbugqbT86PTcmnme1im7" ? "1F" : "GF",
      cMat,
      cProc,
      total: round((cMat || 0) + (cProc || 0)),
      confidence: 1,
      factor: [material, factorValue == null ? "" : `${factorValue} ${factorUnit}`.trim(), factorSource]
        .filter(Boolean)
        .join(" | "),
      materialStatus: "calculated",
      processStatus: "complete",
      quantityBasis: quantityValue == null
        ? ""
        : `Consumption quantity: ${quantityValue} ${quantityUnit}`.trim(),
      process,
      gaps: [],
      raw: { componentGlobalId: globalId },
    };
  }).sort((left, right) => (
    (order.get(left.id) ?? Number.MAX_SAFE_INTEGER) - (order.get(right.id) ?? Number.MAX_SAFE_INTEGER)
    || left.id.localeCompare(right.id)
  ));

  return {
    results,
    summary: {
      components: results.length,
      totalMaterialCarbon_kgCO2e: round(results.reduce((sum, row) => sum + (row.cMat || 0), 0)),
      knownProcessCarbon_kgCO2e: round(results.reduce((sum, row) => sum + (row.cProc || 0), 0)),
      knownTotalCarbon_kgCO2e: round(results.reduce((sum, row) => sum + (row.total || 0), 0)),
      processInputGaps: 0,
    },
  };
}

export function buildDemoState() {
  const results = CONTROLLED_DEMO_COMPONENT_ORDER.map((id) => ({
    id,
    name: CONTROLLED_DEMO_COMPONENT_NAMES[id],
    type: "IfcBeam",
    material: "",
    cMat: null,
    cProc: null,
    total: null,
    confidence: 0,
    factor: "",
    materialStatus: "awaiting_validated_artifact",
    processStatus: "awaiting_validated_artifact",
    quantityBasis: "",
    process: [],
    gaps: [],
    raw: { componentGlobalId: id },
  }));
  const summary = {
    components: 0,
    totalMaterialCarbon_kgCO2e: 0,
    knownProcessCarbon_kgCO2e: 0,
    knownTotalCarbon_kgCO2e: 0,
    processInputGaps: 0,
  };
  return {
    started: true,
    projectId: "demo-paper-figure",
    summary,
    results,
    graphInfo: {
      schemaVersion: PROJECT_GRAPH_SCHEMA.schemaVersion,
      artifactStatus: "awaiting-controlled-artifact",
      canonicalGraphAvailable: false,
      canonicalGraphStatus: "loading_controlled_artifact",
      canonicalQueryAvailable: false,
      canonicalQueryStatus: "loading_controlled_artifact",
    },
    selectedId: null,
    messages: [
      {
        role: "assistant",
        content: "Loading and validating the controlled canonical-v2 graph artifact before carbon-account queries are enabled.",
        audit: {
          status: "incomplete_path",
          boundary: "controlled_demo / artifact_loading",
          evidenceChain: [{ label: "Capability gate", value: "artifact hash, provenance, statistics and emission semantics" }],
          traceEvents: [{ agent: "canonical_v2_artifact_gate", action: "validating" }],
        },
      },
    ],
  };
}

const DEMO_SET_TERMS = ["all", "list", "rank", "which", "highest", "top", "compare", "所有", "全部", "哪些", "最高"];

function demoReferenceSelector(question) {
  const text = String(question || "").toLowerCase();
  const typeTerms = [];
  if (/\bbeam\b|钢梁|梁/.test(text)) typeTerms.push("beam");
  if (/\bwall\b|wall panel|墙板|外墙/.test(text)) typeTerms.push("wall");
  if (/\bmodule\b|模块/.test(text)) typeTerms.push("module");
  const materialTerms = [];
  if (/\bsteel\b|s355|钢/.test(text)) materialTerms.push("steel");
  if (/\bconcrete\b|混凝土/.test(text)) materialTerms.push("concrete");
  const nameTerms = (text.match(/\b[a-z]+\d+[a-z]*\b/g) || []).filter((term) => !/^\d+f$/.test(term));
  const deictic = /this component|selected component|这个构件|选中的构件/.test(text);
  const required = Boolean(typeTerms.length || materialTerms.length || nameTerms.length || deictic);
  return {
    expected_cardinality: required && DEMO_SET_TERMS.some((term) => text.includes(term)) ? "set" : required ? "singleton" : "none",
    type_terms: typeTerms,
    name_terms: nameTerms,
    material_terms: materialTerms,
    storey_terms: [],
    production_terms: [],
    deictic,
    required,
  };
}

function demoCandidate(row, selector) {
  const type = String(row.type || "").toLowerCase();
  const name = String(row.name || "").toLowerCase();
  const material = String(row.material || "").toLowerCase();
  const matchedConstraints = [];
  if (selector.type_terms.length) {
    const matchesType = selector.type_terms.some((term) =>
      term === "beam" ? type.includes("beam") || name.includes("beam") :
      term === "wall" ? type.includes("wall") || name.includes("wall") || name.includes("panel") :
      term === "module" ? type.includes("module") || name.includes("module") : false,
    );
    if (!matchesType) return null;
    matchedConstraints.push(...selector.type_terms.map((term) => `type:${term}`));
  }
  if (selector.material_terms.length) {
    const matchesMaterial = selector.material_terms.some((term) => material.includes(term));
    if (!matchesMaterial) return null;
    matchedConstraints.push(...selector.material_terms.map((term) => `material:${term}`));
  }
  if (selector.name_terms.length) {
    if (!selector.name_terms.every((term) => name.includes(term))) return null;
    matchedConstraints.push(...selector.name_terms.map((term) => `name:${term}`));
  }
  if (!matchedConstraints.length) return null;
  return {
    id: row.id,
    globalId: row.id,
    name: row.name,
    ifcType: row.type,
    material: row.material,
    storey: row.storey || "",
    score: Math.min(1, selector.type_terms.length * 0.5 + selector.material_terms.length * 0.25 + selector.name_terms.length * 0.15),
    matchedConstraints,
  };
}

export function resolveDemoQuestion(question, results, selectedIds = []) {
  const rows = Array.isArray(results) ? results : [];
  const selector = demoReferenceSelector(question);
  const selected = rows.filter((row) => selectedIds.includes(row.id));
  let decision = "not_required";
  let reason = "no_component_reference";
  let candidates = [];
  let committedIds = [];

  if (selectedIds.length) {
    if (selected.length) {
      decision = "commit";
      reason = "selected_component_context";
      committedIds = selected.map((row) => row.id);
      candidates = selected.map((row) => ({
        id: row.id,
        globalId: row.id,
        name: row.name,
        ifcType: row.type,
        material: row.material,
        storey: row.storey || "",
        score: 1,
        matchedConstraints: ["deictic:selected_component"],
      }));
    } else {
      decision = "unresolved";
      reason = "selected_component_not_found";
    }
  } else if (selector.required) {
    candidates = rows.map((row) => demoCandidate(row, selector)).filter(Boolean);
    if (!candidates.length) {
      decision = "unresolved";
      reason = "no_matching_component";
    } else if (selector.expected_cardinality === "set" || candidates.length === 1) {
      decision = "commit";
      reason = selector.expected_cardinality === "set" ? "set_reference_resolved" : "unique_candidate";
      committedIds = candidates.map((candidate) => candidate.id);
    } else {
      decision = "clarify";
      reason = "multiple_matching_components";
    }
  }

  const committedRows = rows.filter((row) => committedIds.includes(row.id));
  const status = decision === "clarify" ? "clarification_required" : decision === "unresolved" ? "unresolved_target" : "executable";
  const summary = decision === "commit"
    ? {
        components: committedRows.length,
        totalMaterialCarbon_kgCO2e: round(committedRows.reduce((sum, row) => sum + (row.cMat || 0), 0)),
        knownProcessCarbon_kgCO2e: round(committedRows.reduce((sum, row) => sum + (row.cProc || 0), 0)),
        knownTotalCarbon_kgCO2e: round(committedRows.reduce((sum, row) => sum + (row.total ?? (row.cMat || 0) + (row.cProc || 0)), 0)),
      }
    : {};
  const referenceGrounding = { selector, decision, committedIds, candidates, reason };
  const answer = decision === "clarify"
    ? `I found ${candidates.length} BIM components matching that description. Select the intended component before I calculate its carbon emission.`
    : decision === "unresolved"
      ? `I could not ground that description to a BIM component (${reason}).`
      : decision === "commit"
        ? `${committedRows[0]?.name || "The selected component"} has ${summary.knownTotalCarbon_kgCO2e} kgCO2e known carbon.`
        : "The project-level carbon account is ready for aggregation.";

  return {
    question,
    answer,
    results: rows,
    summary,
    carbonRequirement: {
      perspective: "product",
      operation: "retrieve",
      target_ids: committedIds,
      reference_selector: selector,
      status,
    },
    referenceGrounding,
    queryResult: {
      status,
      perspective: "product",
      operation: "retrieve",
      rows: committedRows.map((row) => ({
        componentGlobalId: row.id,
        componentName: row.name,
        ifcType: row.type,
        C_mat_kgCO2e: row.cMat,
        C_proc_known_kgCO2e: row.cProc,
        C_MM_known_kgCO2e: row.total,
      })),
      summary,
      candidates: decision === "clarify" ? candidates : [],
      groundingReason: reason,
      semanticPath: "Natural-language reference -> BIM candidate -> GlobalId -> CarbonEmission",
    },
    requestContext: {
      scope: committedIds.length ? "selected_component" : "project",
      selectedComponentIds: committedIds,
      selectedComponents: committedRows.map((row) => ({
        globalId: row.id,
        componentName: row.name,
        ifcType: row.type,
        materialText: row.material,
      })),
      filters: {},
    },
    reasoningTrace: [
      { agent: "M3.1_requirement_identifier", action: "semantic_parse" },
      {
        agent: "M3.1_reference_grounding_gate",
        action: "candidate_generation_and_commit_gate",
        observation: { decision, candidateCount: candidates.length, committedIds },
      },
      ...(decision === "commit" ? [{ agent: "M3.2_query_executor", action: "product_retrieve" }] : []),
      { agent: "M3.3_answer_synthesizer", action: "grounded_response_synthesis" },
    ],
    inputs: { qaMode: "demo_reference_grounding" },
  };
}
