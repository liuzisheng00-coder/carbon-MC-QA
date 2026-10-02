import { IfcAPI } from "web-ifc";

import {
  BATCH_MAX_MESHES,
  BATCH_MAX_TRIANGLES,
  buildProductMesh,
  configureWebIfcApi,
  isValidGeometryMesh,
  transferBuffers,
} from "./webIfcGeometryCore.mjs";

function errorMessage(error) {
  return error instanceof Error && error.message
    ? error.message
    : String(error || "Unknown Web IFC parser error");
}

function defaultYieldControl() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export function createWebIfcGeometryWorkerRuntime({
  IfcAPIClass = IfcAPI,
  workerScope,
  yieldControl = defaultYieldControl,
}) {
  if (!workerScope || typeof workerScope.postMessage !== "function") {
    throw new TypeError("A Worker-like scope with postMessage is required");
  }

  const activeRequests = new Map();
  const disposedRequests = new Set();

  function postBatch(requestId, meshes, loaded, total) {
    if (!meshes.length || disposedRequests.has(requestId)) return;
    if (!meshes.every(isValidGeometryMesh)) {
      throw new Error("Web IFC produced an invalid geometry batch");
    }
    workerScope.postMessage(
      { type: "batch", requestId, meshes, loaded, total },
      transferBuffers(meshes),
    );
  }

  async function parseIfc(requestId, ifcBytes) {
    const state = { disposed: false };
    activeRequests.set(requestId, state);
    let api = null;
    let modelId = null;
    let flatMeshes = null;
    let terminalMessage = null;
    let disposedAtFinish = false;

    try {
      if (disposedRequests.has(requestId)) return;

      api = new IfcAPIClass();
      state.api = api;
      await configureWebIfcApi(api);
      if (state.disposed || disposedRequests.has(requestId)) return;

      modelId = api.OpenModel(new Uint8Array(ifcBytes));
      flatMeshes = api.LoadAllGeometry(modelId);
      const total = flatMeshes.size();
      let batch = [];
      let batchTriangles = 0;

      for (let meshIndex = 0; meshIndex < total; meshIndex += 1) {
        if (state.disposed || disposedRequests.has(requestId)) return;

        const flatMesh = flatMeshes.get(meshIndex);
        let mesh = null;
        try {
          mesh = buildProductMesh(api, modelId, flatMesh);
        } finally {
          flatMesh.delete();
        }

        if (mesh) {
          const triangles = mesh.indices.length / 3;
          if (
            batch.length > 0 &&
            (
              batch.length >= BATCH_MAX_MESHES ||
              batchTriangles + triangles > BATCH_MAX_TRIANGLES
            )
          ) {
            postBatch(requestId, batch, meshIndex, total);
            batch = [];
            batchTriangles = 0;
            await yieldControl();
            if (state.disposed || disposedRequests.has(requestId)) return;
          }

          batch.push(mesh);
          batchTriangles += triangles;
          if (
            batch.length >= BATCH_MAX_MESHES ||
            batchTriangles >= BATCH_MAX_TRIANGLES
          ) {
            postBatch(requestId, batch, meshIndex + 1, total);
            batch = [];
            batchTriangles = 0;
            await yieldControl();
          }
        } else if ((meshIndex + 1) % BATCH_MAX_MESHES === 0) {
          await yieldControl();
        }
      }

      if (state.disposed || disposedRequests.has(requestId)) return;
      postBatch(requestId, batch, total, total);
      terminalMessage = {
        type: "complete",
        requestId,
        loaded: total,
        total,
      };
    } catch (error) {
      if (!state.disposed && !disposedRequests.has(requestId)) {
        terminalMessage = {
          type: "error",
          requestId,
          message: errorMessage(error),
        };
      }
    } finally {
      disposedAtFinish = state.disposed || disposedRequests.has(requestId);
      if (flatMeshes && typeof flatMeshes.delete === "function") {
        try {
          flatMeshes.delete();
        } catch {
          // The model/API cleanup below remains mandatory.
        }
      }
      if (api && modelId !== null) {
        try {
          api.CloseModel(modelId);
        } catch {
          // Dispose still releases any remaining model state.
        }
      }
      if (api) {
        try {
          api.Dispose();
        } catch {
          // There is no further worker-owned parser state to release.
        }
      }
      activeRequests.delete(requestId);
      disposedRequests.delete(requestId);
    }

    if (terminalMessage && !disposedAtFinish) {
      workerScope.postMessage(terminalMessage);
    }
  }

  function handleMessage({ data }) {
    const requestId = data?.requestId;
    if (!Number.isInteger(requestId)) return undefined;

    if (data.type === "dispose") {
      disposedRequests.add(requestId);
      const state = activeRequests.get(requestId);
      if (state) state.disposed = true;
      return undefined;
    }

    if (data.type !== "load") return undefined;
    if (!(data.ifcBytes instanceof ArrayBuffer)) {
      workerScope.postMessage({
        type: "error",
        requestId,
        message: "IFC load message requires an ArrayBuffer",
      });
      return undefined;
    }

    return parseIfc(requestId, data.ifcBytes);
  }

  workerScope.onmessage = handleMessage;
  return { handleMessage };
}

if (typeof self !== "undefined" && typeof self.postMessage === "function") {
  createWebIfcGeometryWorkerRuntime({ workerScope: self });
}
