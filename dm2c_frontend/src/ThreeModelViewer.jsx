import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  VIEWER_PERFORMANCE_POLICY,
  buildComponentScene,
  buildIfcGeometryScene,
} from "./interfaceModel.mjs";
import {
  appendIfcGeometryPage,
  buildIfcGeometryPageUrl,
} from "./ifcGeometryPaging.mjs";
import { createIfcRenderSourceController } from "./web-ifc/ifcRenderSource.mjs";
import { formatIfcViewerStatus } from "./web-ifc/ifcRenderSource.mjs";
import { startWebIfcGeometry } from "./web-ifc/webIfcGeometryClient.mjs";

function disposeObject(object) {
  if (object.geometry) object.geometry.dispose();
  if (object.material) {
    if (Array.isArray(object.material)) {
      object.material.forEach((material) => material.dispose());
    } else {
      object.material.dispose();
    }
  }
}

function transformIfcPoint(point, center = [0, 0, 0]) {
  return [point[0] - center[0], point[2] - center[2], -(point[1] - center[1])];
}

function buildIfcBufferGeometry(entry, center) {
  const positions = new Float32Array(entry.vertices.length);
  for (let index = 0; index < entry.vertices.length; index += 3) {
    const [x, y, z] = transformIfcPoint(
      [entry.vertices[index], entry.vertices[index + 1], entry.vertices[index + 2]],
      center,
    );
    positions[index] = x;
    positions[index + 1] = y;
    positions[index + 2] = z;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(entry.indices);
  if (entry.materialIds?.length) {
    entry.materialIds.forEach((materialIndex, faceIndex) => {
      geometry.addGroup(faceIndex * 3, 3, materialIndex);
    });
  }
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

function createIfcMaterials(entry) {
  if (Array.isArray(entry.materials) && entry.materials.length) {
    return entry.materials.map(
      (material) =>
        new THREE.MeshStandardMaterial({
          color: entry.selected || entry.candidate ? entry.color : material.color || entry.color,
          roughness: 0.62,
          metalness: String(entry.type || "").toLowerCase().includes("steel") ? 0.34 : 0.08,
          transparent: Number(material.opacity) < 1,
          opacity: material.opacity ?? 1,
          side: THREE.DoubleSide,
        }),
    );
  }
  return new THREE.MeshStandardMaterial({
    color: entry.color,
    roughness: 0.58,
    metalness: String(entry.material || entry.type || "").toLowerCase().includes("steel") ? 0.42 : 0.12,
    side: THREE.DoubleSide,
  });
}

export default function ThreeModelViewer({
  results,
  selectedIds,
  candidateIds,
  onSelectResult,
  ifcFileUrl,
  geometryUrl,
  progressiveIfc = false,
}) {
  const containerRef = useRef(null);
  const onSelectResultRef = useRef(onSelectResult);
  const [ifcGeometry, setIfcGeometry] = useState(null);
  const [geometryMode, setGeometryMode] = useState("account");
  const [geometryProgress, setGeometryProgress] = useState({
    loadedMeshes: 0,
    totalEligibleProducts: null,
    complete: false,
    status: "loading",
    partialError: "",
  });

  useEffect(() => {
    onSelectResultRef.current = onSelectResult;
  }, [onSelectResult]);

  useEffect(() => {
    if (!ifcFileUrl && !geometryUrl) {
      setIfcGeometry(null);
      setGeometryMode("account");
      setGeometryProgress({ loadedMeshes: 0, totalEligibleProducts: null, complete: false, status: "idle", partialError: "" });
      return undefined;
    }

    setIfcGeometry(null);
    setGeometryMode(ifcFileUrl ? "web-ifc" : "backend-ifc");
    setGeometryProgress({ loadedMeshes: 0, totalEligibleProducts: null, complete: false, status: "loading", partialError: "" });

    const applyRenderState = ({ source, status, geometry, warning = null }) => {
      const loadedMeshes = geometry?.meshes?.length || 0;
      const totalEligibleProducts = geometry?.totalEligibleProducts ?? geometry?.total ?? null;
      const partialError = warning ? String(warning.message || warning) : "";

      setIfcGeometry(loadedMeshes ? geometry : null);
      setGeometryProgress({
        loadedMeshes,
        totalEligibleProducts,
        complete: status === "complete",
        status,
        partialError,
      });

      if (status === "web-partial-fallback") {
        setGeometryMode("web-partial-fallback");
      } else if (status === "web-fallback") {
        setGeometryMode("backend-ifc");
      } else if (!loadedMeshes && (status === "complete" || status === "error")) {
        setGeometryMode("account");
      } else {
        setGeometryMode(source);
      }
    };

    const startBackend = ({ onBatch, onComplete, onError }) => {
      const abortController = new AbortController();
      let cancelled = false;
      let accumulated = null;

      const fetchGeometry = async (url) => {
        const response = await fetch(url, { signal: abortController.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        return data.geometry || data;
      };

      const loadGeometry = async () => {
        try {
          if (!progressiveIfc) {
            const geometry = await fetchGeometry(geometryUrl);
            if (cancelled) return;
            accumulated = geometry;
            onBatch(geometry);
            onComplete();
            return;
          }

          let startProduct = 0;
          while (!cancelled) {
            const geometry = await fetchGeometry(buildIfcGeometryPageUrl(geometryUrl, startProduct));
            if (cancelled) return;
            accumulated = appendIfcGeometryPage(accumulated, geometry);
            onBatch(accumulated);

            if (geometry.complete || geometry.nextProduct === null || geometry.nextProduct === undefined) {
              onComplete();
              return;
            }
            if (!Number.isInteger(geometry.nextProduct) || geometry.nextProduct <= startProduct) {
              throw new Error("IFC geometry page did not advance its cursor");
            }
            startProduct = geometry.nextProduct;
          }
        } catch (error) {
          if (cancelled || error.name === "AbortError") return;
          onError(error);
        }
      };

      void loadGeometry();
      return () => {
        cancelled = true;
        abortController.abort();
      };
    };

    if (!ifcFileUrl) {
      let backendGeometry = { meshes: [] };
      return startBackend({
        onBatch(geometry) {
          backendGeometry = geometry || { meshes: [] };
          applyRenderState({ source: "backend-ifc", status: "loading", geometry: backendGeometry });
        },
        onComplete() {
          applyRenderState({ source: "backend-ifc", status: "complete", geometry: backendGeometry });
        },
        onError(error) {
          applyRenderState({
            source: "backend-ifc",
            status: "error",
            geometry: backendGeometry,
            warning: error,
          });
        },
      });
    }

    const sourceController = createIfcRenderSourceController({
      startWebIfc: (handlers) => startWebIfcGeometry({ ifcFileUrl, ...handlers }),
      startBackend,
      onState: applyRenderState,
    });
    sourceController.start();
    return () => {
      sourceController.stop();
    };
  }, [geometryUrl, ifcFileUrl, progressiveIfc]);

  const fallbackScene = useMemo(
    () => buildComponentScene(results, selectedIds, candidateIds),
    [results, selectedIds, candidateIds],
  );
  const ifcScene = useMemo(
    () => (ifcGeometry ? buildIfcGeometryScene(ifcGeometry, selectedIds, candidateIds) : null),
    [ifcGeometry, selectedIds, candidateIds],
  );
  const sceneModel = useMemo(
    () => (ifcScene?.meshes?.length ? { ...ifcScene, mode: "ifc" } : { ...fallbackScene, mode: "account" }),
    [fallbackScene, ifcScene],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const width = Math.max(container.clientWidth, 220);
    const height = Math.max(container.clientHeight, 180);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#f7f6f2");

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, VIEWER_PERFORMANCE_POLICY.pixelRatioLimit),
    );
    renderer.setSize(width, height);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.dataset.viewer = "dm2c-three-viewer";
    renderer.domElement.dataset.renderMode = VIEWER_PERFORMANCE_POLICY.renderMode;
    renderer.domElement.dataset.renderCount = "0";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.display = "block";
    container.appendChild(renderer.domElement);

    const radius = Math.max(sceneModel.bounds?.radius || 1, 1);
    scene.fog = new THREE.Fog("#f7f6f2", Math.max(7, radius * 3), Math.max(16, radius * 9));
    const center = sceneModel.bounds?.center || [0, 0, 0];
    const cameraPosition = sceneModel.camera.position;
    const cameraTarget = sceneModel.camera.target;

    const camera = new THREE.PerspectiveCamera(38, width / height, 0.1, Math.max(100, radius * 12));
    camera.position.set(...cameraPosition);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.target.set(...cameraTarget);
    controls.minDistance = 2.5;
    controls.maxDistance = Math.max(12, radius * 8);

    scene.add(new THREE.HemisphereLight("#ffffff", "#a7a39a", 1.6));
    const keyLight = new THREE.DirectionalLight("#ffffff", 2.1);
    keyLight.position.set(3, 5, 4);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight("#dbe9ff", 0.9);
    fillLight.position.set(-4, 3, -2);
    scene.add(fillLight);

    const hasIfcSite = sceneModel.mode === "ifc" && sceneModel.meshes.some((entry) => entry.type === "IfcSite");
    if (sceneModel.mode !== "ifc" || !hasIfcSite) {
      const gridSize = Math.max(7, radius * 2.6);
      const ground = new THREE.GridHelper(gridSize, 14, "#c9c5b8", "#e2dfd6");
      ground.position.y = sceneModel.mode === "ifc" ? -radius * 0.42 : -0.28;
      ground.position.x = sceneModel.camera.target[0];
      ground.position.z = sceneModel.camera.target[2];
      scene.add(ground);
    }

    const modelGroup = new THREE.Group();
    const pickable = [];
    let highlightedEdgeCount = 0;
    let triangleCount = 0;

    sceneModel.meshes.forEach((entry) => {
      const geometry = entry.vertices
        ? buildIfcBufferGeometry(entry, center)
        : new THREE.BoxGeometry(...entry.size);
      const material = entry.vertices ? createIfcMaterials(entry) : createIfcMaterials(entry);
      const mesh = new THREE.Mesh(geometry, material);
      triangleCount += Math.floor((geometry.index?.count || geometry.attributes.position?.count || 0) / 3);
      if (entry.position) mesh.position.set(...entry.position);
      mesh.userData = { id: entry.id, label: entry.label, type: entry.type };
      mesh.castShadow = false;
      mesh.receiveShadow = true;
      modelGroup.add(mesh);
      pickable.push(mesh);

      if (entry.selected || entry.candidate) {
        const edgeGeometry = new THREE.EdgesGeometry(geometry);
        const edgeMaterial = new THREE.LineBasicMaterial({
          color: entry.selected ? "#111111" : "#8b641f",
        });
        const edges = new THREE.LineSegments(edgeGeometry, edgeMaterial);
        edges.position.copy(mesh.position);
        edges.scale.setScalar(entry.selected ? 1.045 : 1.01);
        modelGroup.add(edges);
        highlightedEdgeCount += 1;
      }

      if (entry.selected) {
        const selectedBox = new THREE.Box3().setFromObject(mesh);
        const selectedSize = selectedBox.getSize(new THREE.Vector3());
        const haloGeometry = new THREE.BoxGeometry(
          Math.max(selectedSize.x + 0.12, 0.18),
          Math.max(selectedSize.y + 0.12, 0.18),
          Math.max(selectedSize.z + 0.12, 0.18),
        );
        const haloMaterial = new THREE.MeshBasicMaterial({
          color: "#E24B4A",
          transparent: true,
          opacity: 0.13,
          depthWrite: false,
        });
        const halo = new THREE.Mesh(haloGeometry, haloMaterial);
        selectedBox.getCenter(halo.position);
        modelGroup.add(halo);
      }
    });

    scene.add(modelGroup);
    renderer.domElement.dataset.meshCount = String(sceneModel.meshes.length);
    renderer.domElement.dataset.highlightEdgeCount = String(highlightedEdgeCount);
    renderer.domElement.dataset.triangleCount = String(triangleCount);

    const renderScene = () => {
      const nextCount = Number(renderer.domElement.dataset.renderCount || 0) + 1;
      renderer.domElement.dataset.renderCount = String(nextCount);
      renderer.render(scene, camera);
    };
    controls.addEventListener("change", renderScene);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let pointerDown = null;

    const setPointer = (event) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    const handlePointerDown = (event) => {
      pointerDown = { x: event.clientX, y: event.clientY };
    };

    const handlePointerUp = (event) => {
      if (!pointerDown) return;
      const moved = Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y);
      pointerDown = null;
      if (moved > 5) return;
      setPointer(event);
      raycaster.setFromCamera(pointer, camera);
      const [hit] = raycaster.intersectObjects(pickable, false);
      if (hit?.object?.userData?.id) {
        onSelectResultRef.current?.(hit.object.userData.id, hit.object.userData);
      }
    };

    renderer.domElement.addEventListener("pointerdown", handlePointerDown);
    renderer.domElement.addEventListener("pointerup", handlePointerUp);

    const resizeObserver = new ResizeObserver(() => {
      const nextWidth = Math.max(container.clientWidth, 220);
      const nextHeight = Math.max(container.clientHeight, 180);
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
      renderScene();
    });
    resizeObserver.observe(container);

    controls.update();
    renderScene();

    return () => {
      resizeObserver.disconnect();
      controls.removeEventListener("change", renderScene);
      renderer.domElement.removeEventListener("pointerdown", handlePointerDown);
      renderer.domElement.removeEventListener("pointerup", handlePointerUp);
      controls.dispose();
      scene.traverse(disposeObject);
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [sceneModel]);

  return (
    <div
      ref={containerRef}
      data-testid="three-model-viewer"
      data-mode={geometryMode}
      style={{
        width: "100%",
        height: "100%",
        minHeight: 210,
        background: "#f7f6f2",
        overflow: "hidden",
        position: "relative",
      }}
    >
      <div
        data-testid="ifc-viewer-status"
        style={{
          position: "absolute",
          top: 14,
          left: 14,
          zIndex: 20,
          fontSize: 12,
          fontWeight: 700,
          color: "#263238",
          background: "rgba(255,255,255,0.94)",
          border: "1px solid rgba(104,114,126,0.45)",
          borderRadius: 8,
          boxShadow: "0 2px 10px rgba(0,0,0,0.14)",
          padding: "6px 9px",
          pointerEvents: "none",
        }}
      >
        {formatIfcViewerStatus({ geometryMode, geometryProgress })}
      </div>
    </div>
  );
}
