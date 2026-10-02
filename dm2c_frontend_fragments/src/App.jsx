import { useCallback, useState } from "react";

import FragmentModelViewer from "./FragmentModelViewer.jsx";

export const API_BASE_URL = "http://127.0.0.1:8000";

function projectIdFromLocation() {
  return new URLSearchParams(window.location.search)
    .get("projectId")
    ?.trim() ?? "";
}

export default function App() {
  const [projectInput, setProjectInput] = useState(projectIdFromLocation);
  const [projectId, setProjectId] = useState(projectIdFromLocation);
  const onSelectGlobalId = useCallback(() => {}, []);
  const ifcFileUrl = projectId
    ? `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/ifc-file`
    : "";

  const openProject = (event) => {
    event.preventDefault();
    const nextProjectId = projectInput.trim();
    if (!nextProjectId) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("projectId", nextProjectId);
    window.history.replaceState({}, "", url);
    setProjectId(nextProjectId);
  };

  return (
    <main style={styles.shell}>
      <header style={styles.header}>
        <div>
          <div style={styles.eyebrow}>DM2C · ThatOpen Fragments</div>
          <h1 style={styles.title}>BIM Viewer</h1>
        </div>
        <form onSubmit={openProject} style={styles.projectForm}>
          <label htmlFor="fragment-project-id" style={styles.label}>
            Project ID
          </label>
          <input
            id="fragment-project-id"
            value={projectInput}
            onChange={(event) => setProjectInput(event.target.value)}
            placeholder="Enter an API project ID"
            style={styles.input}
          />
          <button type="submit" style={styles.openButton}>
            Open
          </button>
        </form>
      </header>

      <div style={styles.content}>
        {projectId ? (
          <FragmentModelViewer
            key={projectId}
            projectId={projectId}
            ifcFileUrl={ifcFileUrl}
            onSelectGlobalId={onSelectGlobalId}
          />
        ) : (
          <section style={styles.empty}>
            <div style={styles.emptyCard}>
              <div style={styles.emptyIcon}>F</div>
              <h2 style={styles.emptyTitle}>Open a Fragment model</h2>
              <p style={styles.emptyCopy}>
                Enter the project ID created by the DM2C API. The first visit
                converts its IFC in-browser; later visits use the fragment
                cache.
              </p>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}

const styles = {
  shell: {
    position: "fixed",
    inset: 0,
    display: "grid",
    gridTemplateRows: "72px minmax(0, 1fr)",
    margin: 0,
    background: "#0f2132",
    color: "#f4f7fa",
    fontFamily:
      "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 24,
    padding: "0 22px",
    borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
    background: "#0f2132",
  },
  eyebrow: {
    marginBottom: 2,
    color: "#8ca3b7",
    fontSize: 10,
    fontWeight: 800,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
  },
  title: {
    margin: 0,
    fontSize: 22,
    lineHeight: 1,
  },
  projectForm: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  label: {
    color: "#a9bac9",
    fontSize: 12,
    fontWeight: 700,
  },
  input: {
    width: 220,
    boxSizing: "border-box",
    border: "1px solid rgba(255, 255, 255, 0.18)",
    borderRadius: 8,
    padding: "9px 11px",
    outline: "none",
    background: "rgba(255, 255, 255, 0.08)",
    color: "#ffffff",
  },
  openButton: {
    border: 0,
    borderRadius: 8,
    padding: "9px 14px",
    background: "#ffb000",
    color: "#172333",
    fontWeight: 800,
    cursor: "pointer",
  },
  content: {
    minHeight: 0,
  },
  empty: {
    display: "grid",
    height: "100%",
    placeItems: "center",
    background:
      "radial-gradient(circle at 50% 40%, #203d55 0, #132a3d 42%, #0f2132 76%)",
  },
  emptyCard: {
    width: "min(440px, calc(100% - 40px))",
    boxSizing: "border-box",
    padding: 34,
    border: "1px solid rgba(255, 255, 255, 0.1)",
    borderRadius: 18,
    textAlign: "center",
    background: "rgba(255, 255, 255, 0.05)",
  },
  emptyIcon: {
    display: "grid",
    width: 48,
    height: 48,
    margin: "0 auto 18px",
    placeItems: "center",
    borderRadius: 13,
    background: "#ffb000",
    color: "#172333",
    fontSize: 23,
    fontWeight: 900,
  },
  emptyTitle: {
    margin: "0 0 8px",
    fontSize: 24,
  },
  emptyCopy: {
    margin: 0,
    color: "#a9bac9",
    fontSize: 14,
    lineHeight: 1.6,
  },
};
