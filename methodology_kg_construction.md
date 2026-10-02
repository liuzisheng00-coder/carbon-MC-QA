# 3.X Ontology-driven knowledge graph construction

Based on the three-domain ontology developed in Section 3.X-1, this section presents the automated pipeline that transforms an IFC building model into a populated knowledge graph. As illustrated in Fig. X, the pipeline comprises five sequential steps: (1) IFC parsing and entity extraction, (2) semantic alignment to ontology classes, (3) rule-based inference of production stages, activities, resources, and consumption drivers, (4) deterministic carbon calculation, and (5) KG population. The left side of Fig. X shows the four input sources that feed into the pipeline: the project IFC/BIM model, the cross-domain ontology (TBox schema), the manufacturing rule base and process knowledge, and the emission-factor library. The right side shows the three output subgraphs: design, production, and carbon.

## 3.X.1 Step 1: IFC parsing and entity extraction

The pipeline begins by parsing the project IFC file using the IfcOpenShell library and extracting three categories of information for each building element. First, element identity and classification data (GlobalId, Name, IFC entity type) are extracted from the IFC entity instances. Second, material associations are resolved by traversing the `IfcRelAssociatesMaterial` relationship chain. The IFC schema represents material associations through multi-hop paths involving up to five intermediate entities (e.g., IfcRelAssociatesMaterial → IfcMaterialLayerSetUsage → IfcMaterialLayerSet → IfcMaterialLayer → IfcMaterial). The parser de-reifies these chains into flat material lists, preserving layer thickness where available for subsequent carbon allocation. The parser supports six material representation types: `IfcMaterialLayerSetUsage`, `IfcMaterialLayerSet`, `IfcMaterialConstituentSet`, `IfcMaterialProfileSetUsage`, `IfcMaterialProfileSet`, and `IfcMaterialList`, covering both IFC2X3 and IFC4 schemas. Third, geometric quantities (NetVolume, Length, Area, Weight) and semantic properties (LoadBearing, IsExternal, FireRating) are extracted from `IfcRelDefinesByProperties` and `IfcElementQuantity` instances.

The set of IFC entity types to extract is not hardcoded but read from `onto:SupportedIfcType` instances declared in the ontology. Adding support for a new element type (e.g., IfcCurtainWall) requires only appending a new ontology instance — the parser discovers it automatically at runtime.

## 3.X.2 Step 2: Semantic alignment to ontology classes

Each extracted IFC entity is aligned to the corresponding ontology class based on the type mappings declared in the ontology. For example, an `IfcWallStandardCase` entity is mapped to a graph node with label `IfcWall` in the BIM design domain. IFC properties and quantities are mapped to node attributes using the `onto:PropertyMapping` and `onto:QuantityMapping` rules. For instance, the ontology rule:

```
onto:PM_LoadBearing a onto:PropertyMapping ;
    onto:ifcPropertyName "LoadBearing" ;
    onto:rdfPredicate    "bim:loadBearing" .
```

instructs the converter to read the IFC property named "LoadBearing" and store it as the attribute `loadBearing` on the graph node. This approach decouples the property vocabulary from the converter code, so that new properties can be added by editing the ontology alone.

Material nodes are deduplicated: if multiple elements use the same material (e.g., Steel Q355), they all link to a single `IfcMaterial` node carrying the shared density and emission factor reference. This deduplication reduces graph size and ensures consistency.

## 3.X.3 Step 3: Rule-based inference of stages, activities, resources, and consumption drivers

For each building element, the converter queries the ontology to find the best-matching production template using a scoring function:

$$
\text{score}(e, T) = \begin{cases}
50 + 10 + \max_k |k| & \text{if } \texttt{type}(e) \in T.\texttt{appliesTo} \wedge \exists k \in T.\texttt{keywords}: k \subseteq \texttt{mat}(e) \\
50 & \text{if } \texttt{type}(e) \in T.\texttt{appliesTo} \wedge T.\texttt{keywords} = \emptyset \\
< 50 & \text{otherwise}
\end{cases} \tag{X}
$$

where $|k|$ is the character length of the matched keyword (longer keywords rank higher to resolve ambiguity). A template is accepted only if score ≥ 50.

When a template is matched, the converter instantiates its full stage-activity-resource hierarchy. For example, the SteelFrameProduction template produces:

$$
\text{Cutting} \xrightarrow{\text{precedes}} \text{Welding} \xrightarrow{\text{precedes}} \text{Assembly} \xrightarrow{\text{precedes}} \text{Coating}
$$

Each stage is instantiated as a `ProductionStage` node (with the appropriate subclass label, e.g., `StructureAssemblyStage`), linked to the element via the cross-domain edge `REQUIRES_STAGE`, and connected to its parent `ModuleProduction` node via `HAS_PRODUCTION_STAGE`. Within each stage, manufacturing activities are created with `HAS_PROCESS` edges and sequenced via `FOLLOWED_BY`. Each activity is linked to the materials it consumes via the cross-domain edge `CONSUMES_MATERIAL`.

Simultaneously, `ConsumptionDriver` nodes are generated for each production stage, establishing the MC → Carbon bridge via `HAS_CARBON_DRIVER`. Each manufacturing activity is linked to its driver via `RELATED_TO_DRIVER`, enabling process-level carbon attribution.

## 3.X.4 Step 4: Deterministic carbon calculation

For elements with material associations, the carbon allocator computes embodied carbon using the multi-layer allocation formula from Section 3.X-2. For each material layer $i$ in element $e$:

$$
EC_i = f_i \cdot V_e \cdot \rho_i \cdot EF_i \tag{X+1}
$$

where $f_i$ is the volume fraction (determined by thickness ratio or equal split), $V_e$ is the element's net volume from BIM, $\rho_i$ is the material density, and $EF_i$ is the emission factor — both retrieved from the ontology by keyword matching. The calculation results are stored as `CarbonEmission` nodes in the carbon subgraph, each linked to the source element via `PRODUCES_EMISSION` and to a `ConsumptionQuantity` node recording the mass basis via `RELATED_TO_QUANTITY`.

## 3.X.5 Step 5: KG population

The populated graph is serialised in two formats. A JSON property graph file stores all nodes and edges with their labels, domains, and property dictionaries, suitable for programmatic analysis and visualization tools. A Cypher script generates executable Neo4j import statements, including index creation for frequently queried attributes (e.g., `globalId` on building elements, `name` on materials, `emissionValue` on carbon nodes), node creation with all properties, and relationship creation using MATCH-CREATE patterns. The Cypher script can be executed directly via `cypher-shell` to populate a Neo4j database instance.

For the case study model (TypeA modular residential unit), the pipeline produces a graph with 1,686 nodes and 3,706 edges distributed across three domains (BIM: 244, MC: 847, Carbon: 595), with 1,991 cross-domain edges accounting for 53.7% of all relationships. Table X summarises the output statistics by node label and edge type.

> **Table X.** Knowledge graph statistics for the case study model.

| Node label | Domain | Count | Description |
|---|---|---|---|
| IfcWall | BIM | 45 | Wall elements |
| IfcBeam | BIM | 35 | Beam elements |
| IfcColumn | BIM | 24 | Column elements |
| IfcSlab | BIM | 14 | Slab elements |
| IfcMaterial | BIM | 5 | Deduplicated materials |
| IfcElementQuantity | BIM | 119 | Quantity nodes |
| ModuleProduction | MC | 103 | Production nodes |
| StructureAssemblyStage | MC | 219 | Structural fabrication stages |
| FitUpStage | MC | 146 | Fit-up stages |
| ManufacturingActivity | MC | 367 | Activity instances |
| ConsumptionDriver | Carbon | 367 | Emission drivers |
| CarbonEmission | Carbon | 112 | Emission results |
| EmissionFactor | Carbon | 4 | Factor coefficients |
| **Total** | | **1,686** | |

> **Fig. X.** Five-step ontology-driven IFC-to-KG pipeline architecture. Left: four input sources. Centre: sequential processing steps. Right: three output subgraphs. Arrows indicate data flow (solid) and rule lookup (dashed).

> **Fig. X+1.** Instance-level example showing how a single wall element (IfcWall_12) traverses all three domains through cross-domain edges, with verifiable numeric values at each node.
