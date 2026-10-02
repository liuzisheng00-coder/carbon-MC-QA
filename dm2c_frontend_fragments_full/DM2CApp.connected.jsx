import React, { useEffect, useMemo, useState } from "react";
import DM2CVisualInterface, { EMPTY_SUMMARY, FILE_SLOTS } from "./src/DM2CVisualInterface.jsx";
import { getExistingProjectId } from "./src/projectHandoff.mjs";
import {
  PROJECT_GRAPH_SCHEMA,
  buildDemoState,
  buildComponentSubgraphPath,
  buildSelectionContext,
  deriveControlledDemoAccount,
  derivePendingGrounding,
  extractAuditTrail,
  extractClarificationCandidates,
  isCanonicalGraphAvailable,
  isCanonicalQueryAvailable,
  normalizeCarbonAccounts,
  normalizeComponentSubgraph,
  normalizeProjectGraphInfo,
  primarySelectionId,
  resolveDemoQuestion,
  selectDemoComponentSubgraph,
  updateSelectionIds,
  validateControlledDemoArtifact,
} from "./src/interfaceModel.mjs";

const API_BASE =
  (typeof window !== "undefined" && window.DM2C_API_BASE_URL) ||
  "http://localhost:8000";

let demoArtifactRequest = null;

function loadDemoArtifactOnce() {
  if (!demoArtifactRequest) {
    demoArtifactRequest = fetch("/demo-component-subgraphs.json").then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const bytes = await response.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const artifactHash = [...new Uint8Array(digest)]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("")
        .toUpperCase();
      const artifactText = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      const artifact = JSON.parse(artifactText);
      if (artifact?.schemaVersion !== PROJECT_GRAPH_SCHEMA.schemaVersion) {
        throw new Error(
          `expected ${PROJECT_GRAPH_SCHEMA.schemaVersion}, received ${artifact?.schemaVersion || "missing schemaVersion"}`,
        );
      }
      return validateControlledDemoArtifact(artifact, artifactHash);
    });
  }
  return demoArtifactRequest;
}

const toNumber = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

const fmt = (value) => {
  const number = toNumber(value);
  return number == null ? "-" : number.toLocaleString(undefined, { maximumFractionDigits: 1 });
};

const compactId = (value) => {
  const text = String(value || "");
  return text.length > 16 ? `${text.slice(0, 8)}...${text.slice(-5)}` : text;
};

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const text = await response.text();
  let data = {};

  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }

  if (!response.ok) {
    const message = data.detail || data.error || data.message || text || `HTTP ${response.status}`;
    throw new Error(message);
  }

  return data;
}

function unwrapPayload(data) {
  return data?.payload || data?.result || data || {};
}

function normalizeSummary(data) {
  const payload = unwrapPayload(data);
  return {
    ...EMPTY_SUMMARY,
    ...(payload.summary || data?.summary || {}),
  };
}

function hasSummary(data) {
  const payload = unwrapPayload(data);
  return Boolean(payload.summary || data?.summary);
}

function normalizePayload(data, fallbackProjectId = "") {
  const payload = unwrapPayload(data);
  const rows = payload.results || data?.results || payload.components || data?.components || [];

  return {
    projectId:
      data?.project_id ||
      data?.projectId ||
      payload.project_id ||
      payload.projectId ||
      fallbackProjectId,
    summary: normalizeSummary(data),
    results: Array.isArray(rows) ? rows.map(normalizeComponent) : [],
    carbonAccounts: normalizeCarbonAccounts(data),
    graphInfo: normalizeProjectGraphInfo(data),
  };
}

function normalizeFactor(factor) {
  if (!factor) return "No material factor selected";

  const name = factor.materialName || factor.material_name || factor.name || "";
  const value = factor.factorValue ?? factor.factor_value ?? "";
  const unit = factor.factorUnit || factor.factor_unit || "";
  const source = factor.sourceFile || factor.source_file || factor.source || "";

  return [name, value && `${value} ${unit}`.trim(), source].filter(Boolean).join(" | ");
}

function normalizeProcessSteps(row, processCarbon) {
  const drivers = processCarbon?.drivers || [];

  if (Array.isArray(drivers) && drivers.length) {
    return drivers.map((driver) => ({
      step:
        driver.process_title ||
        driver.processTitle ||
        driver.driver_kind ||
        driver.driverKind ||
        "Manufacturing activity",
      co2e: toNumber(driver.carbon_value_kgco2e ?? driver.carbonValueKgCO2e),
      status: driver.status === "complete" ? "measured" : "gap",
      required: driver.required_quantity || driver.requiredQuantity || "",
    }));
  }

  const selected = row.selectedProcessSteps || row.processCandidates || row.process?.candidates || [];
  if (Array.isArray(selected) && selected.length) {
    return selected.map((step) => ({
      step: step.title || step.process_title || step.docId || step.doc_id || "Retrieved process evidence",
      co2e: null,
      status: "evidence",
      required: step.rationale || "",
    }));
  }

  const gaps = row.gaps?.gaps || [];
  if (Array.isArray(gaps) && gaps.length) {
    return gaps.map((gap) => ({
      step: gap.process_title || gap.processTitle || gap.driver_kind || "Missing process input",
      co2e: null,
      status: "gap",
      required: gap.required_quantity || gap.requiredQuantity || "",
    }));
  }

  return [];
}

function normalizeComponent(row, index) {
  const materialCarbon = row.materialCarbon || row.material?.calculation || {};
  const processCarbon = row.processCarbon || row.process?.calculation || {};
  const cMat = toNumber(materialCarbon.value_kgco2e ?? materialCarbon.valueKgCO2e);
  const cProc = toNumber(processCarbon.value_kgco2e ?? processCarbon.valueKgCO2e);
  const total = toNumber(row.knownTotalCarbon_kgCO2e ?? row.knownTotalCarbonKgCO2e);
  const factor = row.selectedMaterialFactor || row.material?.selected_factor || row.material?.selectedFactor;

  return {
    id: row.componentGlobalId || row.globalId || row.id || `component-${index}`,
    name: row.componentName || row.name || row.componentGlobalId || `Component ${index + 1}`,
    type: row.ifcType || row.type || "",
    material: row.materialText || row.material || "",
    cMat,
    cProc,
    total: total ?? (cMat != null || cProc != null ? (cMat || 0) + (cProc || 0) : null),
    confidence: toNumber(row.confidence) ?? 0,
    factor: normalizeFactor(factor),
    process: normalizeProcessSteps(row, processCarbon),
    materialStatus: row.materialStatus || row.material?.status || materialCarbon.status || "",
    processStatus: row.processStatus || row.process?.status || processCarbon.status || "",
    processIssue: processCarbon.issue || row.processError || row.process?.error || "",
    quantityBasis: materialCarbon.quantity_name
      ? `${materialCarbon.quantity_name}: ${fmt(materialCarbon.quantity_value)} ${materialCarbon.quantity_unit || ""}`.trim()
      : "",
    gaps: row.gaps?.gaps || [],
    raw: row,
  };
}

function compactTrace(data) {
  const payload = unwrapPayload(data);

  if (typeof data?.trace === "string") return data.trace;
  if (typeof payload?.trace === "string") return payload.trace;

  const trace = payload.reasoningTrace || data?.reasoningTrace || data?.trace || [];
  if (!Array.isArray(trace) || !trace.length) return "";

  return trace
    .slice(-5)
    .map((event) => {
      const agent = event.agent || "agent";
      const action = event.action || event.thought || "step";
      return `${agent}: ${action}`;
    })
    .join(" | ");
}

function requestContext(data) {
  const payload = unwrapPayload(data);
  return payload.requestContext || data?.requestContext || {};
}

function findSelectedRows(data, normalized) {
  const context = requestContext(data);
  const ids = context.selectedComponentIds || context.selected_component_ids || [];
  if (!Array.isArray(ids) || !ids.length) return [];

  return ids
    .map((id) => normalized.results.find((row) => row.id === id || row.raw?.componentGlobalId === id))
    .filter(Boolean);
}

function selectedContextForRow(data, row) {
  const context = requestContext(data);
  const components = context.selectedComponents || context.selected_components || [];
  if (!Array.isArray(components)) return null;
  return (
    components.find(
      (component) =>
        component.globalId === row.id ||
        component.componentNodeId === row.id ||
        component.globalId === row.raw?.componentGlobalId
    ) || null
  );
}

function formatQuantityBasis(row, selectedContext) {
  if (row.quantityBasis) return row.quantityBasis;

  const quantities = selectedContext?.quantities || [];
  if (Array.isArray(quantities) && quantities.length) {
    return quantities
      .slice(0, 3)
      .map((quantity) =>
        `${quantity.quantityName || "Quantity"}: ${fmt(quantity.quantityValue)} ${quantity.quantityUnit || ""}`.trim()
      )
      .join(" | ");
  }

  return "-";
}

function buildSelectedComponentMessage(question, data, normalized) {
  const selectedRows = findSelectedRows(data, normalized);
  const context = requestContext(data);

  if ((context.scope || "") !== "selected_component" || !selectedRows.length) {
    return null;
  }

  const row = selectedRows[0];
  const selectedContext = selectedContextForRow(data, row);
  const processStatus = row.processStatus || (row.cProc ? "complete" : "missing");
  const processEvidenceCount = Array.isArray(row.raw?.selectedProcessSteps) ? row.raw.selectedProcessSteps.length : row.process.length;
  const gaps = row.gaps || [];
  const gapLines = gaps
    .slice(0, 6)
    .map((gap) => `- ${gap.process_title || gap.processTitle || gap.driver_kind || "process input"}: ${gap.required_quantity || gap.requiredQuantity || "quantity required"}`)
    .join("\n");

  let content = `Selected component: ${row.name}\n`;
  if (row.processIssue) {
    content += `\nProcess carbon status is ${processStatus}: ${row.processIssue}`;
  } else if ((row.cProc == null || row.cProc === 0) && gaps.length) {
    content +=
      "\nProcess carbon is not complete because the system has process evidence, but does not yet have measured activity quantities for this component.";
  } else if ((row.cProc == null || row.cProc === 0) && processEvidenceCount > 0) {
    content +=
      "\nProcess evidence was retrieved, but no complete process-carbon quantity was calculated for this component.";
  } else {
    content += `\nKnown process carbon is ${fmt(row.cProc)} kgCO2e.`;
  }

  if (gapLines) {
    content += `\n\nMissing process inputs:\n${gapLines}`;
  }

  return {
    role: "assistant",
    content,
    table: [
      { label: "GlobalId", value: compactId(row.id) },
      { label: "IFC type", value: row.type || "-" },
      { label: "Material", value: row.material || "-" },
      { label: "Quantity basis", value: formatQuantityBasis(row, selectedContext) },
      { label: "Material carbon", value: `${fmt(row.cMat)} kgCO2e (${row.materialStatus || "-"})` },
      { label: "Process carbon", value: `${fmt(row.cProc)} kgCO2e (${processStatus || "-"})` },
      { label: "Known C_MM", value: `${fmt(row.total)} kgCO2e` },
      { label: "Factor", value: row.factor || "-" },
      { label: "Gaps", value: String(gaps.length || 0) },
    ],
    trace: compactTrace(data),
    audit: extractAuditTrail(unwrapPayload(data)),
    candidates: extractClarificationCandidates(unwrapPayload(data)),
  };
}

function stripAnswerNote(text) {
  return String(text ?? "")
    .split("\n")
    .filter((line) => !/^\s*note:/i.test(line))
    .join("\n")
    .trim();
}

function buildMessageFromPayload(question, data, normalized) {
  const payload = unwrapPayload(data);
  const direct = data?.message || payload.message;
  const candidates = extractClarificationCandidates(payload);
  const responseMeta = {
    trace: compactTrace(data),
    audit: extractAuditTrail(payload),
    candidates,
    pendingQuestion: candidates.length ? question : "",
  };

  if (typeof direct === "string") {
    return { role: "assistant", content: stripAnswerNote(direct), ...responseMeta };
  }

  if (direct?.content) {
    return {
      role: "assistant",
      content: stripAnswerNote(direct.content),
      table: direct.table,
      ...responseMeta,
      trace: direct.trace || responseMeta.trace,
    };
  }

  const answer = data?.answer || payload.answer || data?.response || payload.response;
  if (answer) {
    return { role: "assistant", content: stripAnswerNote(answer), ...responseMeta };
  }

  const lower = question.toLowerCase();
  const rows = normalized.results;
  const summary = normalized.summary;
  const selectedMessage = buildSelectedComponentMessage(question, data, normalized);

  if (selectedMessage) return selectedMessage;

  if (lower.includes("highest") || lower.includes("most") || lower.includes("ranking")) {
    const ranked = [...rows].sort((a, b) => (b.total || 0) - (a.total || 0));
    return {
      role: "assistant",
      content: "Components ranked by known total carbon:",
      table: ranked.slice(0, 10).map((row) => ({
        label: row.name,
        value: `${fmt(row.total)} kgCO2e`,
      })),
      trace: compactTrace(data),
      audit: extractAuditTrail(payload),
      candidates: extractClarificationCandidates(payload),
    };
  }

  if (lower.includes("missing") || lower.includes("gap")) {
    const gaps = rows.flatMap((row) =>
      row.gaps.map((gap) => `${row.name} -> ${gap.process_title || gap.required_quantity || "process input"}`)
    );
    return {
      role: "assistant",
      content:
        gaps.length > 0
          ? `${gaps.length} process data gaps found:\n\n${gaps.map((gap) => `- ${gap}`).join("\n")}`
          : "No process input gaps were returned by the backend for this project.",
      trace: compactTrace(data),
      audit: extractAuditTrail(payload),
      candidates: extractClarificationCandidates(payload),
    };
  }

  if (lower.includes("breakdown") || (lower.includes("material") && lower.includes("process"))) {
    return {
      role: "assistant",
      content: "Carbon breakdown from the current project run:",
      table: [
        { label: "Material carbon (C_mat)", value: `${fmt(summary.totalMaterialCarbon_kgCO2e)} kgCO2e` },
        { label: "Known process carbon (C_proc)", value: `${fmt(summary.knownProcessCarbon_kgCO2e)} kgCO2e` },
        { label: "Known total C_MM", value: `${fmt(summary.knownTotalCarbon_kgCO2e)} kgCO2e` },
        { label: "Process input gaps", value: String(summary.processInputGaps || 0) },
      ],
      trace: compactTrace(data),
      audit: extractAuditTrail(payload),
      candidates: extractClarificationCandidates(payload),
    };
  }

  return {
    role: "assistant",
    content: `Assessment completed for ${summary.components || rows.length} component(s).`,
    table: [
      { label: "C_mat", value: `${fmt(summary.totalMaterialCarbon_kgCO2e)} kgCO2e` },
      { label: "C_proc known", value: `${fmt(summary.knownProcessCarbon_kgCO2e)} kgCO2e` },
      { label: "C_MM known", value: `${fmt(summary.knownTotalCarbon_kgCO2e)} kgCO2e` },
    ],
    trace: compactTrace(data),
    audit: extractAuditTrail(payload),
    candidates: extractClarificationCandidates(payload),
  };
}

function buildProjectWelcomeMessage({
  fileCount = 0,
  summary,
  resultCount = 0,
  graphInfo,
}) {
  const lines = [
    `Loaded ${fileCount} file(s).`,
    `Components: ${summary?.components || resultCount}`,
  ];
  if (isCanonicalGraphAvailable(graphInfo)) {
    const stats = graphInfo?.stats || {};
    if (stats.nodeCount) lines.push(`Graph nodes: ${stats.nodeCount}`);
    if (stats.edgeCount) lines.push(`Graph edges: ${stats.edgeCount}`);
    lines.push("");
    lines.push("Select a component in the model, or ask about this project carbon assessment.");
  } else {
    lines.push("");
    lines.push(
      "Carbon-account QA is unavailable until a validated canonical-v2 graph document and canonical-aware query executor are ready.",
    );
  }
  return lines.join("\n");
}

function getInitialDemoState() {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  return params.get("demo") === "1" ? buildDemoState() : null;
}

export default function DM2CApp() {
  const [demoState] = useState(getInitialDemoState);
  const [initialExistingProjectId] = useState(() => (
    demoState
      ? ""
      : getExistingProjectId(typeof window === "undefined" ? "" : window.location.search)
  ));
  const [files, setFiles] = useState({});
  const [started, setStarted] = useState(() => demoState?.started || Boolean(initialExistingProjectId));
  const [projectId, setProjectId] = useState(() => demoState?.projectId || initialExistingProjectId);
  const [messages, setMessages] = useState(() => demoState?.messages || []);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState(() => demoState?.summary || EMPTY_SUMMARY);
  const [results, setResults] = useState(() => demoState?.results || []);
  const [carbonAccounts, setCarbonAccounts] = useState(() => demoState?.carbonAccounts || null);
  const [graphInfo, setGraphInfo] = useState(() => demoState?.graphInfo || null);
  const canonicalGraphAvailable = isCanonicalGraphAvailable(graphInfo);
  const canonicalQueryAvailable = isCanonicalQueryAvailable(graphInfo);
  const [selectedIds, setSelectedIds] = useState(() =>
    demoState?.selectedIds || (demoState?.selectedId ? [demoState.selectedId] : []),
  );
  const [compareSelection, setCompareSelection] = useState(false);
  const selectedId = useMemo(() => primarySelectionId(selectedIds), [selectedIds]);
  const [detailOpen, setDetailOpen] = useState(false);
  const [componentSubgraph, setComponentSubgraph] = useState(null);
  const [subgraphBusy, setSubgraphBusy] = useState(false);
  const [subgraphError, setSubgraphError] = useState("");
  const [demoArtifact, setDemoArtifact] = useState(null);
  const [demoArtifactError, setDemoArtifactError] = useState("");
  const pendingGrounding = useMemo(() => derivePendingGrounding(messages), [messages]);

  const fileCount = Object.values(files).reduce((sum, list) => sum + (list ? list.length : 0), 0);

  useEffect(() => {
    if (!demoState) return undefined;

    let active = true;
    const loadDemoArtifact = async () => {
      try {
        const artifact = await loadDemoArtifactOnce();
        if (!active) return;
        const controlledAccount = deriveControlledDemoAccount(artifact);
        setDemoArtifact(artifact);
        setDemoArtifactError("");
        setResults(controlledAccount.results);
        setSummary(controlledAccount.summary);
        setGraphInfo({
          ...normalizeProjectGraphInfo(artifact),
          canonicalGraphAvailable: true,
          canonicalGraphStatus: "controlled_artifact_validated",
          canonicalQueryAvailable: true,
          canonicalQueryStatus: "controlled_demo_executor",
        });
        setMessages((current) => (
          current.length === 1 && current[0]?.audit?.boundary === "controlled_demo / artifact_loading"
            ? [{
                role: "assistant",
                content: "The controlled canonical-v2 graph artifact is validated. Describe a component or select it in the model, then ask for its carbon emission.",
                audit: {
                  status: "executable",
                  boundary: "controlled_demo / validated_canonical_v2",
                  evidenceChain: [{ label: "Capability gate", value: "artifact integrity and carbon semantics validated" }],
                  traceEvents: [{ agent: "canonical_v2_artifact_gate", action: "validated" }],
                },
              }]
            : current
        ));
        setError("");
      } catch (err) {
        if (!active) return;
        const message = `Demo graph artifact unavailable: ${err.message || "unknown error"}`;
        setDemoArtifact(null);
        setDemoArtifactError(message);
        setGraphInfo({
          schemaVersion: PROJECT_GRAPH_SCHEMA.schemaVersion,
          artifactStatus: "controlled_artifact_invalid",
          canonicalGraphAvailable: false,
          canonicalGraphStatus: "controlled_artifact_invalid",
          canonicalQueryAvailable: false,
          canonicalQueryStatus: "controlled_artifact_invalid",
          stats: {},
        });
        setMessages((current) => (
          current.length === 1 && current[0]?.audit?.boundary === "controlled_demo / artifact_loading"
            ? [{
                role: "assistant",
                content: `${message} Carbon-account queries remain disabled.`,
                audit: {
                  status: "incomplete_path",
                  boundary: "controlled_demo / artifact_invalid",
                  evidenceChain: [{ label: "Capability gate", value: message }],
                  traceEvents: [{ agent: "canonical_v2_artifact_gate", action: "rejected" }],
                },
              }]
            : current
        ));
        setError(message);
      }
    };

    loadDemoArtifact();
    return () => { active = false; };
  }, [demoState]);

  useEffect(() => {
    if (!initialExistingProjectId || demoState) return undefined;

    let active = true;
    const hydrateExistingProject = async () => {
      setBusy(true);
      setError("");
      try {
        const data = await apiRequest(`/api/projects/${initialExistingProjectId}`);
        if (!active) return;
        const normalized = normalizePayload(data, initialExistingProjectId);
        const resolvedProjectId = normalized.projectId || initialExistingProjectId;
        setProjectId(resolvedProjectId);
        setSummary(normalized.summary);
        setResults(normalized.results);
        setCarbonAccounts(normalized.carbonAccounts);
        setGraphInfo(normalized.graphInfo);
        setStarted(true);
        setMessages([{
          role: "assistant",
          content:
            `Opened existing project ${resolvedProjectId}.\n\n` +
            `Components: ${normalized.summary.components || normalized.results.length}\n\n` +
            (isCanonicalQueryAvailable(normalized.graphInfo)
              ? "Ask me anything about this project carbon assessment."
              : "Carbon-account QA is unavailable until a validated canonical-v2 graph document and canonical-aware query executor are ready."),
          trace: compactTrace(data),
        }]);
      } catch (err) {
        if (!active) return;
        setError(`Unable to open existing project ${initialExistingProjectId}: ${err.message || "unknown error"}`);
      } finally {
        if (active) setBusy(false);
      }
    };

    hydrateExistingProject();
    return () => { active = false; };
  }, [demoState, initialExistingProjectId]);

  useEffect(() => {
    if (!selectedId || !projectId) {
      setComponentSubgraph(null);
      setSubgraphBusy(false);
      setSubgraphError("");
      return undefined;
    }

    if (!canonicalGraphAvailable) {
      setComponentSubgraph(null);
      setSubgraphBusy(false);
      setSubgraphError("Canonical-v2 component graph accounts are unavailable for a design-backbone-only project.");
      return undefined;
    }

    if (demoState && !demoArtifact) {
      setComponentSubgraph(null);
      setSubgraphBusy(!demoArtifactError);
      setSubgraphError(demoArtifactError);
      return undefined;
    }

    const controller = new AbortController();
    setSubgraphBusy(true);
    setSubgraphError("");

    const loadSubgraph = async () => {
      try {
        let rawSubgraph;
        if (demoState) {
          rawSubgraph = selectDemoComponentSubgraph(demoArtifact, selectedId);
          if (!rawSubgraph) throw new Error(`No demo subgraph for ${compactId(selectedId)}`);
        } else {
          const data = await apiRequest(
            buildComponentSubgraphPath(projectId, selectedId),
            { signal: controller.signal },
          );
          rawSubgraph = unwrapPayload(data).subgraph || data.subgraph || data;
        }
        setComponentSubgraph(normalizeComponentSubgraph(rawSubgraph));
      } catch (err) {
        if (err.name === "AbortError") return;
        setComponentSubgraph(null);
        setSubgraphError(err.message || "unknown graph error");
      } finally {
        if (!controller.signal.aborted) setSubgraphBusy(false);
      }
    };

    loadSubgraph();
    return () => controller.abort();
  }, [canonicalGraphAvailable, demoArtifact, demoArtifactError, demoState, projectId, selectedId]);

  const addFiles = (key, list) => {
    setFiles((prev) => ({ ...prev, [key]: [...(prev[key] || []), ...list] }));
  };

  const removeFile = (key, index) => {
    setFiles((prev) => {
      const nextList = [...(prev[key] || [])];
      nextList.splice(index, 1);
      return { ...prev, [key]: nextList.length ? nextList : undefined };
    });
  };

  const startProject = async () => {
    if (!fileCount || busy) return;

    setBusy(true);
    setError("");

    try {
      const form = new FormData();
      FILE_SLOTS.forEach((slot) => {
        const field = slot.key === "knowledge_graph" ? "design" : slot.key;
        (files[slot.key] || []).forEach((file) => form.append(field, file));
      });

      const created = await apiRequest("/api/projects", { method: "POST", body: form });
      let normalized = normalizePayload(created);

      if (normalized.projectId && (!normalized.results.length || !normalized.summary.components)) {
        try {
          const hydrated = await apiRequest(`/api/projects/${normalized.projectId}`);
          normalized = normalizePayload(hydrated, normalized.projectId);
        } catch {
          // Some wrappers only return a project id on creation. Chat still works after that.
        }
      }

      setProjectId(normalized.projectId);
      setSummary(normalized.summary);
      setResults(normalized.results);
      setCarbonAccounts(normalized.carbonAccounts);
      setGraphInfo(normalized.graphInfo);
      setStarted(true);
      setMessages([
        {
          role: "assistant",
          content: buildProjectWelcomeMessage({
            fileCount,
            summary: normalized.summary,
            resultCount: normalized.results.length,
            graphInfo: normalized.graphInfo,
          }),
          trace: compactTrace(created),
        },
      ]);
    } catch (err) {
      setError(err.message || "Failed to create project.");
    } finally {
      setBusy(false);
    }
  };

  const ask = async (question, selectedOverride = undefined) => {
    if (!question || busy) return;

    if (!canonicalQueryAvailable) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Carbon-account QA is unavailable until a validated canonical-v2 graph document is loaded and a canonical-aware query executor is connected.",
        },
      ]);
      return;
    }

    if (!projectId) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Project id is missing. Please upload files and start a project first.",
        },
      ]);
      return;
    }

    setBusy(true);
    setError("");

    try {
      const activeSelectedIds = selectedOverride === undefined ? selectedIds : selectedOverride;
      const selectionContext = buildSelectionContext(activeSelectedIds);
      const data = demoState
        ? resolveDemoQuestion(question, results, selectionContext.selected_component_ids)
        : await apiRequest(`/api/projects/${projectId}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              question,
              selected_component_ids: selectionContext.selected_component_ids,
              scope: selectionContext.scope,
              filters: {},
            }),
          });
      const normalized = demoState
        ? {
            projectId,
            summary: { ...EMPTY_SUMMARY, ...(data.summary || {}) },
            results,
            graphInfo,
          }
        : normalizePayload(data, projectId);
      // An answer carries the rows of one query, not the project's component
      // list. Adopting them here used to replace the model's components with
      // whatever the last question grouped by.
      if (demoState ? Object.keys(data.summary || {}).length > 0 : hasSummary(data)) {
        setSummary(normalized.summary);
      }
      if (!demoState) {
        setGraphInfo(normalized.graphInfo);
        if (normalized.carbonAccounts) setCarbonAccounts(normalized.carbonAccounts);
      }
      setMessages((prev) => [...prev, buildMessageFromPayload(question, data, normalized)]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Backend request failed: ${err.message || "unknown error"}`,
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const send = (text = input, contextLabels = []) => {
    const question = text.trim();
    if (!question || busy) return;
    const context = Array.isArray(contextLabels) ? contextLabels.filter(Boolean) : [];
    setMessages((prev) => [...prev, { role: "user", content: question, context }]);
    setInput("");
    ask(question);
  };

  const selectCandidate = (candidateValue, pendingQuestionOverride = "") => {
    if (busy) return;
    const candidate =
      typeof candidateValue === "string"
        ? pendingGrounding.candidates.find((item) => item.id === candidateValue) || { id: candidateValue }
        : candidateValue || {};
    const id = candidate.id;
    if (!id) return;

    setSelectedIds([id]);
    setCompareSelection(false);
    setDetailOpen(true);
    const pendingQuestion = pendingQuestionOverride || pendingGrounding.pendingQuestion;
    if (pendingQuestion && pendingGrounding.candidateIds.includes(id)) {
      setMessages((prev) => [
        ...prev,
        { role: "user", content: `Selected ${candidate.label || compactId(id)} from the BIM candidates.` },
      ]);
      ask(pendingQuestion, [id]);
    }
  };

  const selectResult = (id) => {
    if (!id) {
      setSelectedIds([]);
      setDetailOpen(false);
      return;
    }
    const candidate = pendingGrounding.candidates.find((item) => item.id === id);
    if (candidate) {
      selectCandidate(candidate, pendingGrounding.pendingQuestion);
      return;
    }
    const nextIds = updateSelectionIds(selectedIds, id, compareSelection);
    setSelectedIds(nextIds);
    setDetailOpen(nextIds.length > 0);
  };

  const clearSelection = () => {
    setSelectedIds([]);
    setDetailOpen(false);
  };

  const removeSelection = (id) => {
    setSelectedIds((current) => {
      const nextIds = current.filter((value) => value !== id);
      if (!nextIds.length) setDetailOpen(false);
      return nextIds;
    });
  };

  const toggleCompareSelection = () => {
    const nextMode = !compareSelection;
    setCompareSelection(nextMode);
    if (!nextMode && selectedId) setSelectedIds([selectedId]);
  };

  return (
    <DM2CVisualInterface
      apiBase={API_BASE}
      files={files}
      started={started}
      projectId={projectId}
      messages={messages}
      input={input}
      busy={busy}
      error={error}
      summary={summary}
      results={results}
      carbonAccounts={carbonAccounts}
      graphInfo={graphInfo}
      selectedId={selectedId}
      selectedIds={selectedIds}
      compareSelection={compareSelection}
      detailOpen={detailOpen}
      candidateIds={pendingGrounding.candidateIds}
      componentSubgraph={componentSubgraph}
      subgraphBusy={subgraphBusy}
      subgraphError={subgraphError}
      onAddFiles={addFiles}
      onRemoveFile={removeFile}
      onStartProject={startProject}
      onInputChange={setInput}
      onSend={send}
      onSelectResult={selectResult}
      onSelectCandidate={selectCandidate}
      onRemoveSelection={removeSelection}
      onToggleCompareSelection={toggleCompareSelection}
      onCloseDetail={() => setDetailOpen(false)}
      onClearSelection={clearSelection}
    />
  );
}
