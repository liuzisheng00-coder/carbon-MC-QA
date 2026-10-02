import { useEffect, useRef, useState } from "react";
import * as OBC from "@thatopen/components";
import fragmentsWorkerUrl from "@thatopen/fragments/worker?url";
import * as THREE from "three";

import { loadProjectFragment } from "./fragmentLoader.mjs";
import {
  deriveFragmentRenderPolicy,
  originalGlobalIdForPick,
} from "./fragmentViewerState.mjs";
import {
  FRAGMENT_VIEWER_STATUS,
  applyFragmentRenderPolicy,
  isXrayEnabled,
  loadFragmentRuntime,
} from "./fragmentViewerRuntime.mjs";

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
  projectId,
  ifcFileUrl,
  onSelectGlobalId,
}) {
  const viewportRef = useRef(null);
  const runtimeRef = useRef(null);
  const desiredPolicyRef = useRef(null);
  const [status, setStatus] = useState(
    FRAGMENT_VIEWER_STATUS.converting,
  );
  const [errorDetail, setErrorDetail] = useState("");
  const [xray, setXray] = useState(false);
  const [selectedGlobalId, setSelectedGlobalId] = useState(null);

  desiredPolicyRef.current = deriveFragmentRenderPolicy({
    xray,
    selectedGlobalId,
  });

  useEffect(() => {
    const container = viewportRef.current;
    if (!container || !projectId || !ifcFileUrl) {
      return undefined;
    }

    let disposed = false;
    let fragments;
    let world;
    let updateFragments;
    let onModelAdded;
    let canvas;

    setStatus(FRAGMENT_VIEWER_STATUS.converting);
    setErrorDetail("");
    setSelectedGlobalId(null);
    runtimeRef.current = null;

    const components = new OBC.Components();

    const fail = (error) => {
      if (disposed) {
        return;
      }
      const detail =
        error instanceof Error && error.message
          ? error.message
          : "The Fragment viewer could not load this IFC.";
      setErrorDetail(detail);
      setStatus(FRAGMENT_VIEWER_STATUS.error);
    };

    const setup = async () => {
      const worlds = components.get(OBC.Worlds);
      world = worlds.create();
      world.scene = new OBC.SimpleScene(components);
      world.scene.setup({
        backgroundColor: new THREE.Color("#eef3f7"),
      });
      world.renderer = new OBC.SimpleRenderer(components, container, {
        antialias: true,
        alpha: false,
      });
      world.camera = new OBC.OrthoPerspectiveCamera(components);
      await world.camera.controls.setLookAt(12, 10, 12, 0, 0, 0);
      components.init();

      fragments = components.get(OBC.FragmentsManager);
      fragments.init(fragmentsWorkerUrl);

      updateFragments = () => {
        void fragments.core.update();
      };
      world.camera.controls.addEventListener("update", updateFragments);

      onModelAdded = ({ value: model }) => {
        model.useCamera(world.camera.three);
        if (!model.object.parent) {
          world.scene.three.add(model.object);
        }
        void fragments.core.update(true);
      };
      fragments.list.onItemSet.add(onModelAdded);

      const modelId = `dm2c-${projectId}`;
      await loadFragmentRuntime({
        loadBytes({ onProgress }) {
          return loadProjectFragment({
            projectId,
            ifcFileUrl,
            onProgress,
          });
        },
        loadModel(fragmentBytes) {
          if (disposed) {
            throw new Error("Fragment viewer was disposed");
          }
          return fragments.core.load(fragmentBytes, {
            modelId,
            camera: world.camera.three,
          });
        },
        async prepareModel(model) {
          if (disposed) {
            await fragments.core.disposeModel(modelId);
            throw new Error("Fragment viewer was disposed");
          }

          model.useCamera(world.camera.three);
          if (!model.object.parent) {
            world.scene.three.add(model.object);
          }
          await frameModel(world, model);
          await fragments.core.update(true);

          runtimeRef.current = {
            fragments,
            model,
            world,
            policyQueue: Promise.resolve(),
          };
          await applyFragmentRenderPolicy(
            runtimeRef.current,
            desiredPolicyRef.current,
          );
          if (disposed) {
            return;
          }

          canvas = world.renderer.three.domElement;
          const selectFromPointer = async (event) => {
            try {
              const pick = await fragments.raycast({
                camera: world.camera.three,
                mouse: new THREE.Vector2(event.clientX, event.clientY),
                dom: canvas,
              });
              const globalId = await originalGlobalIdForPick({
                fragments,
                pick,
              });
              if (!disposed) {
                setSelectedGlobalId(globalId);
                onSelectGlobalId?.(globalId);
              }
            } catch (error) {
              fail(error);
            }
          };
          canvas.addEventListener("click", selectFromPointer);
          runtimeRef.current.removePointerListener = () => {
            canvas.removeEventListener("click", selectFromPointer);
          };
        },
        onStatus(nextStatus) {
          if (!disposed) {
            setStatus(nextStatus);
          }
        },
      });
    };

    void setup().catch(fail);

    return () => {
      disposed = true;
      runtimeRef.current?.removePointerListener?.();
      runtimeRef.current = null;
      if (world && updateFragments) {
        world.camera.controls.removeEventListener(
          "update",
          updateFragments,
        );
      }
      if (fragments && onModelAdded) {
        fragments.list.onItemSet.remove(onModelAdded);
      }
      components.dispose();
      container.replaceChildren();
    };
  }, [ifcFileUrl, onSelectGlobalId, projectId]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) {
      return;
    }

    const policy = deriveFragmentRenderPolicy({
      xray,
      selectedGlobalId,
    });
    runtime.policyQueue = runtime.policyQueue
      .then(() => applyFragmentRenderPolicy(runtime, policy))
      .catch((error) => {
        const detail =
          error instanceof Error && error.message
            ? error.message
            : "The Fragment render policy could not be applied.";
        setErrorDetail(detail);
        setStatus(FRAGMENT_VIEWER_STATUS.error);
      });
  }, [selectedGlobalId, xray]);

  const clearSelection = () => {
    setSelectedGlobalId(null);
    onSelectGlobalId?.(null);
  };

  return (
    <section style={styles.viewer} aria-label="Fragment BIM viewer">
      <div ref={viewportRef} style={styles.viewport} />

      <div style={styles.toolbar}>
        <button
          type="button"
          aria-pressed={xray}
          disabled={!isXrayEnabled(status)}
          onClick={() => setXray((current) => !current)}
          style={{
            ...styles.button,
            ...(xray ? styles.buttonActive : {}),
          }}
        >
          X-Ray {xray ? "On" : "Off"}
        </button>
        {selectedGlobalId && (
          <button
            type="button"
            onClick={clearSelection}
            style={styles.secondaryButton}
          >
            Clear selection
          </button>
        )}
      </div>

      <aside style={styles.inspector} aria-live="polite">
        <div style={styles.statusRow}>
          <span
            style={{
              ...styles.statusDot,
              ...(status === FRAGMENT_VIEWER_STATUS.error
                ? styles.errorDot
                : {}),
            }}
          />
          <strong>{status}</strong>
        </div>
        <div style={styles.inspectorLabel}>Original IFC GlobalId</div>
        <output style={styles.globalId}>
          {selectedGlobalId ?? "Click a fragment to inspect it"}
        </output>
        {errorDetail && (
          <p role="alert" style={styles.errorText}>
            {errorDetail}
          </p>
        )}
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
  viewport: {
    position: "absolute",
    inset: 0,
  },
  toolbar: {
    position: "absolute",
    top: 18,
    left: 18,
    display: "flex",
    gap: 8,
  },
  button: {
    border: "1px solid rgba(15, 33, 50, 0.18)",
    borderRadius: 999,
    padding: "10px 16px",
    background: "rgba(255, 255, 255, 0.92)",
    color: "#153049",
    fontWeight: 700,
    cursor: "pointer",
    boxShadow: "0 6px 24px rgba(31, 53, 72, 0.14)",
  },
  buttonActive: {
    borderColor: "#ffb000",
    background: "#ffb000",
    color: "#172333",
  },
  secondaryButton: {
    border: "1px solid rgba(15, 33, 50, 0.18)",
    borderRadius: 999,
    padding: "10px 14px",
    background: "rgba(255, 255, 255, 0.92)",
    color: "#42576a",
    cursor: "pointer",
  },
  inspector: {
    position: "absolute",
    right: 18,
    bottom: 18,
    width: "min(360px, calc(100% - 36px))",
    boxSizing: "border-box",
    padding: 16,
    border: "1px solid rgba(15, 33, 50, 0.12)",
    borderRadius: 14,
    background: "rgba(255, 255, 255, 0.94)",
    color: "#153049",
    boxShadow: "0 10px 36px rgba(31, 53, 72, 0.18)",
    backdropFilter: "blur(12px)",
  },
  statusRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginBottom: 14,
  },
  statusDot: {
    width: 9,
    height: 9,
    borderRadius: "50%",
    background: "#2fa47c",
    boxShadow: "0 0 0 4px rgba(47, 164, 124, 0.14)",
  },
  errorDot: {
    background: "#c83e4d",
    boxShadow: "0 0 0 4px rgba(200, 62, 77, 0.14)",
  },
  inspectorLabel: {
    marginBottom: 5,
    color: "#708397",
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
  },
  globalId: {
    display: "block",
    overflowWrap: "anywhere",
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
    fontSize: 14,
  },
  errorText: {
    margin: "12px 0 0",
    color: "#9f2634",
    fontSize: 13,
    lineHeight: 1.45,
  },
};
