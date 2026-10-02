import { useEffect, useMemo, useRef, useState } from "react";
import * as OBC from "@thatopen/components";
import fragmentsWorkerUrl from "@thatopen/fragments/worker?url";
import * as THREE from "three";

import { loadSharedProjectFragment } from "./fragmentLoader.mjs";
import { normalizeFragmentGlobalIds } from "./fragmentViewerState.mjs";
import {
  applyFragmentRenderPolicy,
  dispatchFragmentPick,
  FRAGMENT_VIEWER_STATUS,
  isXrayEnabled,
  loadFragmentRuntime,
} from "./fragmentViewerRuntime.mjs";

function projectIdFromIfcUrl(ifcFileUrl) {
  if (!ifcFileUrl) return "";
  try {
    const match = new URL(ifcFileUrl, globalThis.location?.href)
      .pathname.match(/\/api\/projects\/([^/]+)\/ifc-file$/);
    return match ? decodeURIComponent(match[1]) : "";
  } catch {
    return "";
  }
}

async function frameModel(world, model) {
  const box = model.box;
  if (!box || box.isEmpty()) {
    await world.camera.controls.setLookAt(12, 10, 12, 0, 0, 0);
    return;
  }
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const distance = Math.max(size.length() * 0.85, 8);
  await world.camera.controls.setLookAt(
    center.x + distance,
    center.y + distance * 0.55,
    center.z + distance,
    center.x,
    center.y,
    center.z,
  );
}

export default function FragmentModelViewer({
  results,
  selectedIds,
  candidateIds,
  onSelectResult,
  ifcFileUrl,
}) {
  const viewportRef = useRef(null);
  const runtimeRef = useRef(null);
  const worldRef = useRef(null);
  const clipperRef = useRef(null);
  const resultsRef = useRef(results);
  const onSelectResultRef = useRef(onSelectResult);
  const desiredPolicyRef = useRef({ xray: false, selectedGlobalId: null });
  const [status, setStatus] = useState(FRAGMENT_VIEWER_STATUS.converting);
  const [errorDetail, setErrorDetail] = useState("");
  const [xray, setXray] = useState(false);
  const [sectionMode, setSectionMode] = useState(false);
  const [selectedGlobalIds, setSelectedGlobalIds] = useState(
    normalizeFragmentGlobalIds(selectedIds),
  );
  const candidateGlobalIds = useMemo(
    () => normalizeFragmentGlobalIds(candidateIds),
    [candidateIds],
  );
  const primarySelectedGlobalId = selectedGlobalIds.at(-1) ?? null;
  const selectedPolicyKey = selectedGlobalIds.join("\u0000");
  const candidatePolicyKey = candidateGlobalIds.join("\u0000");

  resultsRef.current = results;
  onSelectResultRef.current = onSelectResult;
  desiredPolicyRef.current = {
    xray,
    selectedGlobalIds,
    candidateGlobalIds,
  };

  useEffect(() => {
    setSelectedGlobalIds(normalizeFragmentGlobalIds(selectedIds));
  }, [selectedIds]);

  useEffect(() => {
    const container = viewportRef.current;
    const projectId = projectIdFromIfcUrl(ifcFileUrl);
    if (!container || !projectId) return undefined;

    let disposed = false;
    let fragments;
    let world;
    let updateFragments;
    let onModelAdded;
    let canvas;
    let pointerHandler;
    let sectionHandler;
    const components = new OBC.Components();

    setStatus(FRAGMENT_VIEWER_STATUS.converting);
    setErrorDetail("");
    runtimeRef.current = null;

    const fail = (error) => {
      if (disposed) return;
      setErrorDetail(
        error instanceof Error && error.message
          ? error.message
          : "The Fragment viewer could not load this IFC.",
      );
      setStatus(FRAGMENT_VIEWER_STATUS.error);
    };

    const setup = async () => {
      const worlds = components.get(OBC.Worlds);
      world = worlds.create();
      world.scene = new OBC.SimpleScene(components);
      world.scene.setup({ backgroundColor: new THREE.Color("#eef3f7") });
      world.renderer = new OBC.SimpleRenderer(components, container, {
        antialias: true,
        alpha: false,
      });
      world.camera = new OBC.OrthoPerspectiveCamera(components);
      components.init();

      fragments = components.get(OBC.FragmentsManager);
      fragments.init(fragmentsWorkerUrl);
      updateFragments = () => void fragments.core.update();
      world.camera.controls.addEventListener("update", updateFragments);
      onModelAdded = ({ value: model }) => {
        model.useCamera(world.camera.three);
        if (!model.object.parent) world.scene.three.add(model.object);
      };
      fragments.list.onItemSet.add(onModelAdded);

      const casters = components.get(OBC.Raycasters);
      casters.get(world);
      const clipper = components.get(OBC.Clipper);
      clipper.enabled = true;
      clipper.orthogonalY = true;
      worldRef.current = world;
      clipperRef.current = clipper;

      await loadFragmentRuntime({
        loadBytes({ onProgress }) {
          return loadSharedProjectFragment({
            projectId,
            ifcFileUrl,
            onProgress,
          });
        },
        loadModel(fragmentBytes) {
          return fragments.core.load(fragmentBytes, {
            modelId: `dm2c-${projectId}`,
            camera: world.camera.three,
          });
        },
        async prepareModel(model) {
          if (disposed) throw new Error("Fragment viewer was disposed");
          model.useCamera(world.camera.three);
          if (!model.object.parent) world.scene.three.add(model.object);
          await frameModel(world, model);
          await fragments.core.update(true);
          runtimeRef.current = {
            model,
            fragments,
            policyQueue: Promise.resolve(),
          };
          await applyFragmentRenderPolicy({
            model,
            fragments,
            ...desiredPolicyRef.current,
          });

          canvas = world.renderer.three.domElement;
          pointerHandler = async (event) => {
            try {
              const pick = await fragments.raycast({
                camera: world.camera.three,
                mouse: new THREE.Vector2(event.clientX, event.clientY),
                dom: canvas,
              });
              const globalId = await dispatchFragmentPick({
                pick,
                fragments,
                results: resultsRef.current,
                onSelectResult: onSelectResultRef.current,
              });
              if (!disposed && globalId) {
                // The parent owns compare/single-selection semantics and will
                // feed the complete selectedIds collection back through props.
                setSelectedGlobalIds((current) => (
                  current.includes(globalId) ? current : [...current, globalId]
                ));
              }
            } catch (error) {
              fail(error);
            }
          };
          const sectionHandler = async (event) => {
            const clipper = clipperRef.current;
            const world = worldRef.current;
            if (!clipper?.enabled || !world) return;
            event.preventDefault();
            try {
              await clipper.create(world);
            } catch (error) {
              fail(error);
            }
          };
          canvas.addEventListener("click", pointerHandler);
          canvas.addEventListener("dblclick", sectionHandler);
        },
        onStatus(nextStatus) {
          if (!disposed) setStatus(nextStatus);
        },
        isActive: () => !disposed,
      });
    };

    void setup().catch(fail);
    return () => {
      disposed = true;
      if (canvas && pointerHandler) canvas.removeEventListener("click", pointerHandler);
      if (canvas && sectionHandler) canvas.removeEventListener("dblclick", sectionHandler);
      try {
        clipperRef.current?.deleteAll?.();
      } catch {
        // Clipper may already be disposed with components.
      }
      clipperRef.current = null;
      worldRef.current = null;
      if (world && updateFragments) {
        world.camera.controls.removeEventListener("update", updateFragments);
      }
      if (fragments && onModelAdded) {
        fragments.list.onItemSet.remove(onModelAdded);
      }
      runtimeRef.current = null;
      components.dispose();
      container.replaceChildren();
    };
  }, [ifcFileUrl]);

  useEffect(() => {
    const clipper = clipperRef.current;
    if (!clipper) return;
    const ready = isXrayEnabled(status);
    clipper.enabled = sectionMode && ready;
    clipper.visible = sectionMode && ready;
  }, [sectionMode, status]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.policyQueue = runtime.policyQueue
      .then(() => applyFragmentRenderPolicy({
        model: runtime.model,
        fragments: runtime.fragments,
        xray,
        selectedGlobalIds,
        candidateGlobalIds,
      }))
      .catch((error) => {
        setErrorDetail(error instanceof Error ? error.message : String(error));
        setStatus(FRAGMENT_VIEWER_STATUS.error);
      });
  }, [candidatePolicyKey, selectedPolicyKey, xray]);

  return (
    <section style={styles.viewer} aria-label="Fragment BIM viewer">
      <div ref={viewportRef} style={styles.viewport} />
      <div style={styles.toolbar}>
        <button
          type="button"
          aria-pressed={xray}
          disabled={!isXrayEnabled(status)}
          title={isXrayEnabled(status)
            ? "Toggle X-Ray transparency"
            : `X-Ray becomes available once the model is loaded (current: ${status})`}
          onClick={() => setXray((current) => !current)}
          style={{
            ...styles.button,
            ...(xray ? styles.buttonActive : {}),
            ...(isXrayEnabled(status) ? {} : styles.buttonDisabled),
          }}
        >
          X-Ray {xray ? "On" : "Off"}
        </button>
        <button
          type="button"
          aria-pressed={sectionMode}
          disabled={!isXrayEnabled(status)}
          title={isXrayEnabled(status)
            ? "Double-click the model to add a section plane"
            : `Section planes become available once the model is loaded (current: ${status})`}
          onClick={() => setSectionMode((current) => !current)}
          style={{
            ...styles.button,
            ...(sectionMode ? styles.buttonActive : {}),
            ...(isXrayEnabled(status) ? {} : styles.buttonDisabled),
          }}
        >
          Section {sectionMode ? "On" : "Off"}
        </button>
        <button
          type="button"
          disabled={!isXrayEnabled(status) || !sectionMode}
          title="Remove the section plane under the cursor"
          onClick={() => {
            const clipper = clipperRef.current;
            const world = worldRef.current;
            if (clipper && world) void clipper.delete(world);
          }}
          style={{
            ...styles.button,
            ...((isXrayEnabled(status) && sectionMode) ? {} : styles.buttonDisabled),
          }}
        >
          Delete section
        </button>
      </div>
      <aside style={styles.inspector} aria-live="polite">
        <strong>{status}</strong>
        <div style={styles.label}>Original IFC GlobalId</div>
        <output style={styles.globalId}>
          {primarySelectedGlobalId ?? "Click a fragment to inspect it"}
        </output>
        {errorDetail && <p role="alert" style={styles.error}>{errorDetail}</p>}
      </aside>
    </section>
  );
}

const styles = {
  viewer: {
    position: "relative",
    width: "100%",
    height: "100%",
    overflow: "hidden",
    background: "#eef3f7",
  },
  viewport: { position: "absolute", inset: 0 },
  toolbar: {
    position: "absolute",
    top: 76,
    left: 18,
    display: "flex",
    gap: 8,
  },
  button: {
    border: "1px solid rgba(15,33,50,.18)",
    borderRadius: 999,
    padding: "10px 16px",
    background: "rgba(255,255,255,.94)",
    color: "#153049",
    fontWeight: 700,
    cursor: "pointer",
  },
  buttonActive: { background: "#ffb000", color: "#172333" },
  buttonDisabled: { opacity: 0.55, cursor: "not-allowed" },
  inspector: {
    position: "absolute",
    right: 18,
    bottom: 18,
    width: "min(360px, calc(100% - 36px))",
    padding: 16,
    boxSizing: "border-box",
    borderRadius: 14,
    background: "rgba(255,255,255,.94)",
    color: "#153049",
    boxShadow: "0 10px 36px rgba(31,53,72,.18)",
  },
  label: {
    marginTop: 12,
    color: "#708397",
    fontSize: 12,
    fontWeight: 700,
    textTransform: "uppercase",
  },
  globalId: {
    display: "block",
    marginTop: 5,
    overflowWrap: "anywhere",
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
  },
  error: { marginBottom: 0, color: "#9f2634" },
};
