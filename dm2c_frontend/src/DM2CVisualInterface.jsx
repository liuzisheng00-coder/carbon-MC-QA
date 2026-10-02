import React, { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import GraphAssociationPanel from "./GraphAssociationPanel.jsx";
import SelectionComposer from "./SelectionComposer.jsx";
import ThreeModelViewer from "./ThreeModelViewer.jsx";
import WorkspaceModeSwitch from "./WorkspaceModeSwitch.jsx";
import {
  buildAccountRows,
  buildPerspectiveRows,
  buildSelectionTags,
  isCanonicalGraphAvailable,
  isCanonicalQueryAvailable,
  resolveSelectedComponent,
  statusBadge,
} from "./interfaceModel.mjs";
import "./dm2c-interface.css";

export const FILE_SLOTS = [
  {
    key: "design",
    label: "IFC model",
    accept: ".ifc",
    icon: "IFC",
    desc: "Final IFC model for geometry and component browsing",
  },
  {
    key: "knowledge_graph",
    label: "Knowledge graph",
    accept: ".json",
    icon: "KG",
    desc: "multigranular_carbon_kg.json from the final graph build",
  },
];

export const EMPTY_SUMMARY = {
  components: 0,
  totalMaterialCarbon_kgCO2e: 0,
  knownProcessCarbon_kgCO2e: 0,
  knownTotalCarbon_kgCO2e: 0,
  processInputGaps: 0,
};

const SUGGESTIONS = [
  "What is the carbon emission of the steel beam?",
  "Which components have the highest carbon?",
  "What data is still missing?",
];

const PERSPECTIVE_COPY = {
  product: {
    title: "Product account",
    description: "Components and modules projected from accepted canonical carbon records.",
  },
  material: {
    title: "Material account",
    description: "IFC materials projected from the same accepted emissions.",
  },
  process: {
    title: "Process account",
    description: "Factory activities, energy use, and process evidence.",
  },
};

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

function FileZone({ slot, files, onAdd, onRemove }) {
  const inputRef = useRef(null);
  const list = files || [];

  return (
    <div
      className={`dm2c-file-zone${list.length ? " has-files" : ""}`}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        if (event.dataTransfer.files.length) onAdd(Array.from(event.dataTransfer.files));
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={slot.accept}
        multiple
        hidden
        onChange={(event) => {
          if (event.target.files.length) onAdd(Array.from(event.target.files));
          event.target.value = "";
        }}
      />
      <button
        type="button"
        className="dm2c-file-zone-trigger"
        aria-label={`Add ${slot.label} files`}
        onClick={() => inputRef.current?.click()}
      >
        <span className="dm2c-file-icon">{slot.icon}</span>
        <strong>{slot.label}</strong>
        <small>{slot.desc}</small>
      </button>
      {list.length > 0 && (
        <div className="dm2c-file-list">
          {list.map((file, index) => (
            <div key={`${file.name}-${index}`}>
              <span title={file.name}>{file.name}</span>
              <button
                type="button"
                aria-label={`Remove ${file.name}`}
                onClick={() => onRemove(index)}
              >
                x
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }) {
  const badge = statusBadge(status);
  return <span className={`dm2c-status-pill is-${badge.tone}`}>{badge.label}</span>;
}

function AuditTrailPanel({ audit }) {
  const [open, setOpen] = useState(false);
  if (!audit) return null;

  const chain = audit.evidenceChain || [];
  const traceEvents = audit.traceEvents || [];

  return (
    <div className="dm2c-evidence-disclosure">
      <button
        type="button"
        className="dm2c-evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        {open ? "Hide evidence" : "Show evidence"}
      </button>

      {open && (
        <div className="dm2c-evidence-sheet">
          <div className="dm2c-evidence-meta">
            <StatusPill status={audit.status} />
            {audit.boundary && <span>{audit.boundary}</span>}
          </div>
          <dl>
            {chain.map((item, index) => (
              <div className="dm2c-evidence-row" key={`${item.label}-${index}`}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
          {traceEvents.length > 0 && (
            <div className="dm2c-trace-events">
              {traceEvents.slice(0, 4).map((event, index) => (
                <div key={`${event.agent}-${index}`}>{event.agent}: {event.action}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ClarificationOptions({ candidates, pendingQuestion, onPickCandidate }) {
  if (!Array.isArray(candidates) || !candidates.length) return null;

  return (
    <div className="dm2c-clarification-options">
      {candidates.map((candidate) => (
        <button
          key={candidate.id}
          type="button"
          className="dm2c-clarification-option"
          onClick={() => onPickCandidate?.(candidate, pendingQuestion)}
          title={candidate.id}
        >
          <strong>{candidate.label}</strong>
          <span>
            {[candidate.type, candidate.material, candidate.storey].filter(Boolean).join(" | ") || compactId(candidate.id)}
          </span>
        </button>
      ))}
    </div>
  );
}

function ChatBubble({ msg, onPickCandidate }) {
  const isUser = msg.role === "user";

  return (
    <div className={`dm2c-chat-row${isUser ? " is-user" : ""}`}>
      <div className="dm2c-message-stack">
        <div className="dm2c-chat-bubble">
          {!isUser && msg.audit?.status && (
            <div className="dm2c-answer-status">
              <StatusPill status={msg.audit.status} />
              {msg.audit.boundary && <span>{msg.audit.boundary}</span>}
            </div>
          )}
          {msg.content}
          {msg.table && (
            <div className="dm2c-answer-table">
              {msg.table.map((row, index) => (
                <div className="dm2c-answer-table-row" key={index}>
                  <span>{row.label}</span>
                  <strong>{row.value}</strong>
                </div>
              ))}
            </div>
          )}
        </div>
        {!isUser && (
          <ClarificationOptions
            candidates={msg.candidates}
            pendingQuestion={msg.pendingQuestion}
            onPickCandidate={onPickCandidate}
          />
        )}
        {!isUser && <AuditTrailPanel audit={msg.audit} />}
      </div>
    </div>
  );
}

function PerspectivePanel({ results, carbonAccounts, perspective }) {
  const rows = useMemo(
    () =>
      (carbonAccounts
        ? buildAccountRows(carbonAccounts, perspective)
        : buildPerspectiveRows(results, perspective)
      ).slice(0, 12),
    [carbonAccounts, results, perspective],
  );

  return (
    <div className="dm2c-projection-view">
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Item</th>
              <th>Context</th>
              <th className="is-number">C_mat</th>
              <th className="is-number">C_proc</th>
              <th className="is-number">Known total</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.primary}</strong></td>
                  <td>{row.secondary}</td>
                  <td className="is-number">{fmt(row.knownMaterial)}</td>
                  <td className="is-number">{fmt(row.knownProcess)}</td>
                  <td className="is-number"><strong>{fmt(row.knownTotal)}</strong></td>
                  <td><StatusPill status={row.status} /></td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>No projection rows.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function UploadScreen({ apiBase, files, busy, error, onAddFiles, onRemoveFile, onStartProject }) {
  const fileCount = Object.values(files).reduce((sum, list) => sum + (list ? list.length : 0), 0);

  return (
    <div className="dm2c-upload-screen">
      <div className="dm2c-upload-content">
        <div className="dm2c-upload-brand">
          <span className="dm2c-mark">D2C</span>
          <div>
            <strong>DM2C Carbon</strong>
            <small>Evidence-grounded carbon accounting and QA</small>
          </div>
        </div>
        <div className="dm2c-upload-grid">
          {FILE_SLOTS.map((slot) => (
            <FileZone
              key={slot.key}
              slot={slot}
              files={files[slot.key]}
              onAdd={(list) => onAddFiles(slot.key, list)}
              onRemove={(index) => onRemoveFile(slot.key, index)}
            />
          ))}
        </div>
        <small style={{ display: "block", marginTop: 10, color: "#6f6b62", lineHeight: 1.45 }}>
          Upload the final IFC model and the knowledge graph JSON. Manufacturing logs, factor workbooks, and audit release files are not required for this demo build.
        </small>
        {error && <div className="dm2c-upload-error">{error}</div>}
        <button
          type="button"
          className="dm2c-create-project"
          onClick={onStartProject}
          disabled={fileCount === 0 || busy}
        >
          {busy ? "Creating project..." : fileCount ? `Create project with ${fileCount} file(s)` : "Upload files to begin"}
        </button>
        <small className="dm2c-api-hint">API: {apiBase}</small>
      </div>
    </div>
  );
}

export default function DM2CVisualInterface({
  apiBase,
  files,
  started,
  projectId,
  messages,
  input,
  busy,
  error,
  summary,
  results,
  carbonAccounts,
  graphInfo,
  selectedId,
  selectedIds,
  compareSelection,
  candidateIds,
  componentSubgraph,
  subgraphBusy,
  subgraphError,
  onAddFiles,
  onRemoveFile,
  onStartProject,
  onInputChange,
  onSend,
  onSelectResult,
  onSelectCandidate,
  onRemoveSelection,
  onToggleCompareSelection,
  onClearSelection,
}) {
  const [activeWorkspaceMode, setActiveWorkspaceMode] = useState("model");
  const [viewerComponents, setViewerComponents] = useState({});
  const chatEnd = useRef(null);
  const previousMessageCount = useRef(messages.length);
  const selectionSources = useMemo(
    () => [
      ...results,
      ...Object.values(viewerComponents).map((component) => ({
        id: component.id,
        name: component.label,
        type: component.type,
      })),
    ],
    [results, viewerComponents],
  );
  const selectionTags = useMemo(
    () => buildSelectionTags(selectionSources, selectedIds),
    [selectionSources, selectedIds],
  );
  const selectedResult = useMemo(
    () => resolveSelectedComponent(selectionSources, selectedId, componentSubgraph),
    [selectionSources, selectedId, componentSubgraph],
  );
  const canonicalGraphAvailable = isCanonicalGraphAvailable(graphInfo);
  const canonicalQueryAvailable = isCanonicalQueryAvailable(graphInfo);

  useEffect(() => {
    const hasNewMessage = messages.length > previousMessageCount.current;
    if (hasNewMessage || busy) chatEnd.current?.scrollIntoView({ behavior: "smooth" });
    previousMessageCount.current = messages.length;
  }, [messages.length, busy]);

  const handleModelSelection = (id, metadata = {}) => {
    setViewerComponents((current) => ({
      ...current,
      [id]: {
        id,
        label: metadata.label || id,
        type: metadata.type || "",
      },
    }));
    onSelectResult(id);
  };

  if (!started) {
    return (
      <UploadScreen
        apiBase={apiBase}
        files={files}
        busy={busy}
        error={error}
        onAddFiles={onAddFiles}
        onRemoveFile={onRemoveFile}
        onStartProject={onStartProject}
      />
    );
  }

  const ifcGeometryUrl = projectId === "demo-paper-figure"
    ? "/demo-ifc-geometry.json"
    : projectId
      ? `${apiBase}/api/projects/${encodeURIComponent(projectId)}/ifc-geometry`
      : "";
  const ifcFileUrl = projectId && projectId !== "demo-paper-figure"
    ? `${apiBase}/api/projects/${encodeURIComponent(projectId)}/ifc-file`
    : "";
  const perspectiveCopy = PERSPECTIVE_COPY[activeWorkspaceMode];

  return (
    <div className="dm2c-app-shell">
      <main className="dm2c-immersive-canvas">
        <div className="dm2c-mode-view" hidden={activeWorkspaceMode !== "model"}>
          <ThreeModelViewer
            results={results}
            selectedIds={selectedIds}
            candidateIds={candidateIds}
            onSelectResult={handleModelSelection}
            ifcFileUrl={ifcFileUrl}
            geometryUrl={ifcGeometryUrl}
            progressiveIfc={Boolean(projectId && projectId !== "demo-paper-figure")}
          />
        </div>

        {perspectiveCopy && (
          <section className="dm2c-mode-view dm2c-account-canvas">
            <div className="dm2c-canvas-heading">
              <h1>{perspectiveCopy.title}</h1>
              <p>{canonicalQueryAvailable
                ? perspectiveCopy.description
                : canonicalGraphAvailable
                  ? "Canonical carbon accounts are unavailable because no canonical-aware query executor is connected."
                  : "Canonical carbon accounts are unavailable because this project contains only a design/RAG backbone."}</p>
            </div>
            {canonicalQueryAvailable
              ? <PerspectivePanel results={results} carbonAccounts={carbonAccounts} perspective={activeWorkspaceMode} />
              : <div className="dm2c-canonical-unavailable" role="status">Upload a validated canonical-v2 graph document before opening product, material, or process carbon accounts.</div>}
          </section>
        )}

        {activeWorkspaceMode === "graph" && (
          <div className="dm2c-mode-view dm2c-graph-canvas">
            <GraphAssociationPanel
              graphInfo={graphInfo}
              summary={summary}
              results={results}
              selectedResult={selectedResult}
              candidateIds={candidateIds}
              componentSubgraph={componentSubgraph}
              subgraphBusy={subgraphBusy}
              subgraphError={subgraphError}
            />
          </div>
        )}

        <header className="dm2c-floating-header">
          <span className="dm2c-mark">D2C</span>
          <span className="dm2c-brand-name">DM2C Carbon</span>
          <span className="dm2c-project-name" title={projectId}>Project {projectId}</span>
          {!canonicalGraphAvailable && (
            <span className="dm2c-graph-state" role="status" title="The IFC or legacy JSON is only a design/RAG backbone; no validated carbon graph is loaded.">
              Backbone only — no canonical carbon KG
            </span>
          )}
          {canonicalGraphAvailable && !canonicalQueryAvailable && (
            <span className="dm2c-graph-state" role="status" title="The canonical graph can be inspected, but live carbon-account queries are disabled until a canonical-aware executor is connected.">
              Canonical KG loaded — query executor unavailable
            </span>
          )}
        </header>

        <WorkspaceModeSwitch
          activeMode={activeWorkspaceMode}
          onChange={setActiveWorkspaceMode}
          canonicalQueryAvailable={canonicalQueryAvailable}
        />

        <aside className="dm2c-floating-qa" aria-label="DM2C grounded question answering">
          <div className="dm2c-qa-header">
            <span className="dm2c-qa-title">Ask DM2C</span>
            <span className="dm2c-grounded-state">{canonicalQueryAvailable ? "Grounded" : "Query offline"}</span>
          </div>

          <div className="dm2c-conversation" aria-live="polite">
            {messages.map((message, index) => (
              <ChatBubble key={`${message.role}-${index}`} msg={message} onPickCandidate={onSelectCandidate} />
            ))}

            {busy && (
              <div className="dm2c-thinking" aria-label="Processing question">
                <span /><span /><span />
              </div>
            )}

            {canonicalQueryAvailable && messages.length <= 1 && !busy && (
              <div className="dm2c-suggestions">
                {SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => onSend(suggestion)}>
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
            <div ref={chatEnd} />
          </div>

          <div className="dm2c-qa-footer">
            {error && <div className="dm2c-error">{error}</div>}
            <SelectionComposer
              input={input}
              selectionTags={selectionTags}
              compareSelection={compareSelection}
              loading={busy}
              disabled={!canonicalQueryAvailable}
              onInputChange={onInputChange}
              onSubmit={() => onSend()}
              onRemoveSelection={onRemoveSelection}
              onClearSelection={onClearSelection}
              onToggleCompareSelection={onToggleCompareSelection}
            />
          </div>
        </aside>
      </main>
    </div>
  );
}
