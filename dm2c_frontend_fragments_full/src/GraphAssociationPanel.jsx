import React, { useEffect, useMemo, useState } from "react";
import {
  PROJECT_GRAPH_SCHEMA,
  buildComponentSubgraphLayout,
  isCanonicalGraphAvailable,
  normalizeExternalValidationCoverage,
} from "./interfaceModel.mjs";

const NODE_COLORS = {
  context: { fill: "#e8e5dc", stroke: "#aaa59a", text: "#27251f" },
  component: { fill: "#E24B4A", stroke: "#9f302f", text: "#ffffff" },
  consumption: { fill: "#6E67C7", stroke: "#4f49a1", text: "#ffffff" },
  emission: { fill: "#D76554", stroke: "#9e3e32", text: "#ffffff" },
  evidence: { fill: "#e8f2ed", stroke: "#6b9b84", text: "#19382a" },
  process: { fill: "#dce9f7", stroke: "#6887aa", text: "#193047" },
};

const compact = (value, limit = 24) => {
  const text = String(value ?? "");
  return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
};

const fmt = (value, maximumFractionDigits = 3) => {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString(undefined, { maximumFractionDigits })
    : "-";
};

function TabButton({ active, children, id, panelId, onClick, onKeyDown }) {
  return (
    <button
      type="button"
      id={id}
      role="tab"
      aria-selected={active}
      aria-controls={panelId}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
      style={{
        border: "none",
        borderBottom: active ? "2px solid #1a1a1a" : "2px solid transparent",
        background: "transparent",
        color: active ? "#1a1a1a" : "#777",
        padding: "6px 8px",
        fontSize: 11,
        fontWeight: active ? 800 : 600,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function AllocationEvidence({ edge }) {
  const allocation = edge.allocation;
  if (!allocation) return null;
  const rows = [
    ["Source emission", `${fmt(allocation.sourceEmissionValue)} ${allocation.sourceEmissionUnit}`],
    ["Component contribution", `${fmt(allocation.componentContributionValue)} ${allocation.sourceEmissionUnit}`],
    ["Allocation set", allocation.allocationSetId],
    ["Basis", allocation.allocationBasis],
    ["Raw weight", `${fmt(allocation.rawWeight)} ${allocation.rawWeightUnit || ""}`.trim()],
    ["Normalized weight", fmt(allocation.normalizedWeight)],
    ["Evidence record", allocation.evidenceRecordId],
  ];
  return (
    <div style={{ border: "1px solid #d9d4ef", borderRadius: 6, background: "#f8f7fc", padding: "8px 9px" }}>
      <div style={{ fontSize: 10, fontWeight: 800, color: "#4f49a1" }}>
        Allocated component contribution
      </div>
      <div style={{ marginTop: 5, fontSize: 9, color: "#777", fontFamily: "monospace" }} title={edge.occurrenceId}>
        {compact(edge.occurrenceId, 52)}
      </div>
      <dl style={{ margin: "7px 0 0", display: "grid", gridTemplateColumns: "minmax(110px, 0.7fr) minmax(0, 1.3fr)", gap: "4px 10px", fontSize: 10 }}>
        {rows.map(([label, value]) => (
          <React.Fragment key={label}>
            <dt style={{ color: "#777" }}>{label}</dt>
            <dd style={{ margin: 0, overflowWrap: "anywhere", fontWeight: 700 }}>{value ?? "-"}</dd>
          </React.Fragment>
        ))}
      </dl>
    </div>
  );
}

function SelectedSubgraph({ selectedResult, candidateRows, graph, busy, error, canonicalGraphAvailable }) {
  const layout = useMemo(() => buildComponentSubgraphLayout(graph), [graph]);

  if (!canonicalGraphAvailable) {
    return <div role="status" style={{ padding: "18px 4px 10px", color: "#6b5511", fontSize: 11 }}>Selected canonical graph accounts are unavailable for a design-backbone-only project.</div>;
  }
  if (!selectedResult) {
    return (
      <div style={{ padding: "18px 4px 10px", color: "#777", fontSize: 11, lineHeight: 1.55 }}>
        <div style={{ fontWeight: 800, color: "#333" }}>Select a BIM component to inspect its graph account.</div>
        {candidateRows.length > 0 && (
          <div style={{ marginTop: 7 }}>
            Grounding candidates: {candidateRows.map((row) => `${row.name} (${compact(row.id, 18)})`).join(", ")}
          </div>
        )}
      </div>
    );
  }

  if (busy) return <div style={{ padding: "20px 4px", color: "#777", fontSize: 11 }}>Loading selected component subgraph...</div>;
  if (error) return <div style={{ padding: "16px 4px", color: "#B42318", fontSize: 11 }}>Subgraph unavailable: {error}</div>;
  if (!graph?.nodes?.length) return <div style={{ padding: "20px 4px", color: "#777", fontSize: 11 }}>No graph nodes were returned for this selection.</div>;

  const allocationEdges = graph.edges.filter((edge) => edge.allocation);
  return (
    <div className="dm2c-subgraph-body">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, margin: "8px 0", fontSize: 10, color: "#777" }}>
        <span>Root <b style={{ color: "#1a1a1a" }} title={graph.rootNodeId}>{compact(graph.rootNodeId, 34)}</b></span>
        <span>{graph.nodes.length} nodes | {graph.edges.length} edges | canonical component closure</span>
      </div>

      <div className="dm2c-subgraph-canvas" data-testid="component-subgraph-canvas">
        <svg
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="img"
          aria-label={`Selected BIM component graph with ${graph.nodes.length} nodes and ${graph.edges.length} edges`}
        >
          <defs>
            <marker id="dm2c-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L8,3 z" fill="#aaa59a" />
            </marker>
          </defs>

          {layout.edges.map((edge, index) => {
            const sourceX = edge.source.x + edge.source.width / 2;
            const sourceY = edge.source.y + edge.source.height / 2;
            const targetX = edge.target.x + edge.target.width / 2;
            const targetY = edge.target.y + edge.target.height / 2;
            const controlOffset = Math.max(36, Math.abs(targetX - sourceX) * 0.35);
            const direction = targetX >= sourceX ? 1 : -1;
            const bend = ((index % 3) - 1) * 9;
            const path = `M ${sourceX} ${sourceY} C ${sourceX + controlOffset * direction} ${sourceY + bend}, ${targetX - controlOffset * direction} ${targetY + bend}, ${targetX} ${targetY}`;
            return (
              <path
                key={edge.id}
                d={path}
                fill="none"
                stroke={edge.allocation ? "#6E67C7" : "#b7b2a7"}
                strokeWidth={edge.allocation ? "2" : "1.2"}
                markerEnd="url(#dm2c-arrow)"
                opacity="0.86"
              >
                <title>{`${edge.src} --${edge.type}--> ${edge.tgt}\noccurrence: ${edge.occurrenceId}`}</title>
              </path>
            );
          })}

          {layout.nodes.map((node) => {
            const colors = NODE_COLORS[node.role] || NODE_COLORS.context;
            return (
              <g key={node.id} transform={`translate(${node.x}, ${node.y})`} data-node-id={node.id}>
                <title>{`${node.id}\n${JSON.stringify(node.props)}`}</title>
                <rect width={node.width} height={node.height} rx="5" fill={colors.fill} stroke={node.isRoot ? "#6e1f1e" : colors.stroke} strokeWidth={node.isRoot ? "2.5" : "1.2"} />
                <text x="10" y="18" fill={colors.text} fontSize="10" fontWeight="800">{compact(node.primaryLabel, 24)}</text>
                <text x="10" y="35" fill={colors.text} fontSize="11" fontWeight="700">{compact(node.title, 24)}</text>
                <text x="10" y="49" fill={colors.text} fontSize="9" opacity="0.82">{compact(node.detail || node.id, 28)}</text>
                {node.validZero && <text x="92" y="17" fill={colors.text} fontSize="7" fontWeight="800">Valid zero</text>}
              </g>
            );
          })}
        </svg>
      </div>

      <div className="dm2c-subgraph-table">
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 9.5 }}>
          <thead><tr style={{ color: "#777", textAlign: "left" }}><th style={{ padding: "6px 5px" }}>Source</th><th style={{ padding: "6px 5px" }}>Relation</th><th style={{ padding: "6px 5px" }}>Target</th><th style={{ padding: "6px 5px" }}>Occurrence</th></tr></thead>
          <tbody>
            {graph.edges.map((edge) => (
              <tr key={edge.id} style={{ borderTop: "1px solid #f0eee7" }}>
                <td style={{ padding: "5px", fontFamily: "monospace" }} title={edge.src}>{compact(edge.src, 22)}</td>
                <td style={{ padding: "5px", fontWeight: 800, color: "#4f49a1" }}>{edge.type}</td>
                <td style={{ padding: "5px", fontFamily: "monospace" }} title={edge.tgt}>{compact(edge.tgt, 22)}</td>
                <td style={{ padding: "5px", fontFamily: "monospace" }} title={edge.occurrenceId}>{compact(edge.occurrenceId, 22)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {allocationEdges.length > 0 && (
        <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 8 }}>
          {allocationEdges.map((edge) => <AllocationEvidence key={edge.id} edge={edge} />)}
        </div>
      )}
    </div>
  );
}

function CountMap({ title, values }) {
  const entries = Object.entries(values || {});
  if (!entries.length) return null;
  return (
    <div>
      <div style={{ fontSize: 9, color: "#777", fontWeight: 800 }}>{title}</div>
      <div style={{ marginTop: 3, display: "flex", flexWrap: "wrap", gap: 5 }}>
        {entries.map(([label, count]) => <span key={label} style={{ border: "1px solid #e5e2d8", borderRadius: 10, padding: "2px 6px", fontSize: 9 }}>{label}: {fmt(count, 0)}</span>)}
      </div>
    </div>
  );
}

function ValidationCoverage({ graphInfo }) {
  const coverage = normalizeExternalValidationCoverage(graphInfo);
  if (!coverage) {
    return <div style={{ marginTop: 10, color: "#777", fontSize: 10 }}>External input-validation coverage is not available in the project manifest or statistics.</div>;
  }
  return (
    <div style={{ marginTop: 12, border: "1px solid #e5e2d8", background: "#fbfaf7", borderRadius: 6, padding: 9 }}>
      <div style={{ fontSize: 10, fontWeight: 800 }}>External validation coverage</div>
      <div style={{ marginTop: 3, fontSize: 9.5, color: "#666" }}>Rejected inputs are outside the graph; only accepted records can form graph nodes and edges.</div>
      <div style={{ marginTop: 7, display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 5 }}>
        {[["Candidates", coverage.candidateCount], ["Accepted", coverage.acceptedCount], ["Rejected", coverage.rejectedCount], ["Coverage", `${fmt(coverage.coverageValue * 100, 1)}%`]].map(([label, value]) => (
          <div key={label} style={{ background: "#fff", border: "1px solid #ebe8df", padding: "5px 6px" }}><b>{value}</b><div style={{ color: "#777", fontSize: 8.5 }}>{label}</div></div>
        ))}
      </div>
      <div style={{ marginTop: 6, fontSize: 9 }}><b>Coverage formula:</b> {coverage.coverageFormula} = {fmt(coverage.coverageValue)}</div>
      <div style={{ marginTop: 7, display: "grid", gap: 6 }}>
        <CountMap title="Accepted by kind" values={coverage.acceptedByKind} />
        <CountMap title="Rejected by kind" values={coverage.rejectedByKind} />
        <CountMap title="Rejected by reason" values={coverage.rejectedByReason} />
      </div>
    </div>
  );
}

function ProjectMapping({ graphInfo, summary, resultCount }) {
  const stats = graphInfo?.stats || {};
  const classCounts = stats.applicationClassCounts || {};
  const canonicalGraphAvailable = isCanonicalGraphAvailable(graphInfo);
  const metrics = [
    [canonicalGraphAvailable ? "BIM components" : "Backbone components", canonicalGraphAvailable
      ? (classCounts.BuildingComponent ?? stats.buildingComponents ?? summary?.components ?? 0)
      : (summary?.components ?? 0)],
    ["IFC materials", canonicalGraphAvailable ? (classCounts.IfcMaterial ?? stats.ifcMaterials ?? 0) : null],
    ["Design quantities", canonicalGraphAvailable ? (classCounts.DesignQuantity ?? stats.quantityBasis ?? 0) : null],
    ["Carbon emissions", canonicalGraphAvailable ? (classCounts.CarbonEmission ?? stats.carbonEmissions ?? 0) : null],
    ["Graph nodes", canonicalGraphAvailable ? (stats.nodeCount ?? 0) : null],
    ["Graph edges", canonicalGraphAvailable ? (stats.edgeCount ?? 0) : null],
  ];
  return (
    <div style={{ paddingTop: 8 }}>
      {!canonicalGraphAvailable && (
        <div role="status" style={{ marginBottom: 9, border: "1px solid #e0c36c", borderRadius: 6, background: "#fff9e8", padding: "8px 9px", color: "#59470d", fontSize: 10, lineHeight: 1.45 }}>
          <b>IFC alone does not create a carbon knowledge graph.</b>{" "}
          This project currently contains a design backbone only. The inventory below is the M2.3 schema/ontology contract, not instantiated project facts.
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 1, border: "1px solid #e8e5dc", background: "#e8e5dc" }}>
        {metrics.map(([label, value]) => <div key={label} style={{ background: "#fbfaf7", padding: "7px 8px" }}><div style={{ fontSize: 13, fontWeight: 800 }}>{fmt(value, 0)}</div><div style={{ marginTop: 2, fontSize: 9, color: "#777" }}>{label}</div></div>)}
      </div>

      <div style={{ marginTop: 11, display: "grid", gridTemplateColumns: "minmax(180px, 0.7fr) minmax(360px, 1.3fr)", gap: 10 }}>
        <section style={{ minWidth: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 800 }}>M2.3 schema/ontology contract: {PROJECT_GRAPH_SCHEMA.inventory.applicationClassCount} application classes</div>
          <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 5 }}>
            {PROJECT_GRAPH_SCHEMA.applicationClasses.map((label) => <span key={label} style={{ border: "1px solid #ddd9cf", borderRadius: 10, padding: "3px 7px", fontSize: 9, overflowWrap: "anywhere" }}>{label}</span>)}
          </div>
        </section>
        <section style={{ minWidth: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 800 }}>M2.3 schema/ontology contract: {PROJECT_GRAPH_SCHEMA.inventory.principalTripleCount} typed triples ({PROJECT_GRAPH_SCHEMA.inventory.principalPredicateCount} predicates)</div>
          <div style={{ marginTop: 5, maxHeight: 250, overflow: "auto", borderTop: "1px solid #ebe8df" }}>
            {PROJECT_GRAPH_SCHEMA.principalTriples.map((row) => (
              <div key={`${row.source}-${row.relation}-${row.target}`} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)", gap: 8, alignItems: "center", padding: "5px 4px", borderBottom: "1px solid #f0eee7", fontSize: 9 }}>
                <span style={{ fontWeight: 700, overflowWrap: "anywhere" }}>{row.source}</span><span style={{ color: "#4f49a1", fontFamily: "monospace", fontWeight: 800 }}>{row.relation}</span><span style={{ textAlign: "right", fontWeight: 700, overflowWrap: "anywhere" }}>{row.target}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
      {canonicalGraphAvailable && <ValidationCoverage graphInfo={graphInfo} />}
      <div style={{ marginTop: 8, color: "#777", textAlign: "right", fontSize: 9 }}>
        {canonicalGraphAvailable ? `${resultCount} displayed component accounts` : `${resultCount} legacy assessment rows (not canonical accounts)`}
      </div>
    </div>
  );
}

export default function GraphAssociationPanel({ graphInfo, summary, results, selectedResult, candidateIds, componentSubgraph, subgraphBusy, subgraphError }) {
  const [activeTab, setActiveTab] = useState(selectedResult ? "selected" : "mapping");
  const canonicalGraphAvailable = isCanonicalGraphAvailable(graphInfo);
  const candidateSet = useMemo(() => new Set(candidateIds || []), [candidateIds]);
  const candidateRows = useMemo(() => (results || []).filter((row) => candidateSet.has(row.id)), [results, candidateSet]);
  useEffect(() => { if (selectedResult) setActiveTab("selected"); }, [selectedResult?.id]);
  const handleTabKeyDown = (event) => {
    const tabs = ["selected", "mapping"];
    const currentIndex = tabs.indexOf(activeTab);
    let nextTab = null;
    if (event.key === "ArrowRight") nextTab = tabs[(currentIndex + 1) % tabs.length];
    if (event.key === "ArrowLeft") nextTab = tabs[(currentIndex - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") nextTab = tabs[0];
    if (event.key === "End") nextTab = tabs[tabs.length - 1];
    if (!nextTab) return;
    event.preventDefault();
    setActiveTab(nextTab);
    document.getElementById(`graph-tab-${nextTab}`)?.focus();
  };

  return (
    <section className="dm2c-graph-panel">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flex: "0 0 auto" }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 800 }}>BIM to carbon graph</div>
          <div style={{ marginTop: 1, fontSize: 10, color: "#888" }}>
            {selectedResult
              ? (selectedResult.name && selectedResult.name !== selectedResult.id
                ? `${selectedResult.name} | ${compact(selectedResult.id, 30)}`
                : compact(selectedResult.id, 30))
              : "Canonical schema and selected-account evidence"}
          </div>
        </div>
        <div role="tablist" aria-label="Knowledge graph views" style={{ display: "flex", borderBottom: "1px solid #ddd9cf" }}>
          <TabButton id="graph-tab-selected" panelId="graph-panel-selected" active={activeTab === "selected"} onClick={() => setActiveTab("selected")} onKeyDown={handleTabKeyDown}>Selected subgraph</TabButton>
          <TabButton id="graph-tab-mapping" panelId="graph-panel-mapping" active={activeTab === "mapping"} onClick={() => setActiveTab("mapping")} onKeyDown={handleTabKeyDown}>Project mapping</TabButton>
        </div>
      </div>
      <div
        id={`graph-panel-${activeTab}`}
        className="dm2c-graph-panel-body"
        role="tabpanel"
        aria-labelledby={`graph-tab-${activeTab}`}
        tabIndex={0}
      >
        {activeTab === "selected"
          ? <SelectedSubgraph selectedResult={selectedResult} candidateRows={candidateRows} graph={componentSubgraph} busy={subgraphBusy} error={subgraphError} canonicalGraphAvailable={canonicalGraphAvailable} />
          : <ProjectMapping graphInfo={graphInfo} summary={summary} resultCount={(results || []).length} />}
      </div>
    </section>
  );
}
