let nextRequestId = 1;

function isNumericSequence(value, { integer = false } = {}) {
  if (
    !Array.isArray(value) &&
    !(ArrayBuffer.isView(value) && typeof value.length === "number")
  ) {
    return false;
  }

  for (const item of value) {
    if (
      typeof item !== "number" ||
      !Number.isFinite(item) ||
      (integer && (!Number.isInteger(item) || item < 0))
    ) {
      return false;
    }
  }
  return true;
}

function isValidMesh(mesh) {
  if (
    mesh === null ||
    typeof mesh !== "object" ||
    typeof mesh.globalId !== "string" ||
    mesh.globalId.trim().length === 0 ||
    !isNumericSequence(mesh.vertices) ||
    mesh.vertices.length < 3 ||
    mesh.vertices.length % 3 !== 0 ||
    !isNumericSequence(mesh.indices, { integer: true }) ||
    mesh.indices.length < 3 ||
    mesh.indices.length % 3 !== 0
  ) {
    return false;
  }

  const vertexCount = mesh.vertices.length / 3;
  for (const index of mesh.indices) {
    if (index >= vertexCount) return false;
  }

  if (mesh.materialIds !== undefined) {
    if (
      !isNumericSequence(mesh.materialIds, { integer: true }) ||
      mesh.materialIds.length !== mesh.indices.length / 3 ||
      !Array.isArray(mesh.materials) ||
      mesh.materials.length === 0
    ) {
      return false;
    }
    for (const materialId of mesh.materialIds) {
      if (materialId >= mesh.materials.length) return false;
    }
  }
  return true;
}

function isOptionalFiniteNumber(value) {
  return value === undefined || (typeof value === "number" && Number.isFinite(value));
}

function invalidMessageError(kind) {
  return new Error(`Invalid Web IFC geometry ${kind}`);
}

export function startWebIfcGeometry({
  ifcFileUrl,
  onBatch,
  onComplete,
  onError,
}) {
  const requestId = nextRequestId++;
  const abortController = new AbortController();
  let worker;
  try {
    worker = new Worker(
      new URL("./webIfcGeometryWorker.mjs", import.meta.url),
      { type: "module" },
    );
  } catch (error) {
    abortController.abort();
    onError(
      error instanceof Error
        ? error
        : new Error("Failed to start Web IFC geometry worker"),
    );
    return function stopFailedWebIfcGeometry() {};
  }
  let settled = false;

  function cleanup() {
    if (settled) return false;
    settled = true;
    abortController.abort();
    try {
      worker.postMessage({ type: "dispose", requestId });
    } catch {
      // A failed/crashed Worker may reject further messages.
    } finally {
      worker.terminate();
      worker.onmessage = null;
      worker.onerror = null;
    }
    return true;
  }

  function settle(callback) {
    if (!cleanup()) return;
    callback();
  }

  function settleWithError(error) {
    settle(() => onError(error));
  }

  worker.onmessage = ({ data }) => {
    if (settled || !data || data.requestId !== requestId) return;

    if (data.type === "batch") {
      if (
        !Array.isArray(data.meshes) ||
        !data.meshes.every(isValidMesh) ||
        !isOptionalFiniteNumber(data.loaded) ||
        !isOptionalFiniteNumber(data.total)
      ) {
        settleWithError(invalidMessageError("batch"));
        return;
      }
      onBatch(data);
      return;
    }

    if (data.type === "complete") {
      if (
        !isOptionalFiniteNumber(data.loaded) ||
        !isOptionalFiniteNumber(data.total)
      ) {
        settleWithError(invalidMessageError("completion"));
        return;
      }
      settle(() => onComplete(data));
      return;
    }

    if (data.type === "error") {
      if (typeof data.message !== "string" || data.message.length === 0) {
        settleWithError(invalidMessageError("error message"));
        return;
      }
      settleWithError(new Error(data.message));
      return;
    }

    settleWithError(invalidMessageError("worker message"));
  };

  worker.onerror = (event) => {
    if (!settled) {
      settleWithError(
        new Error(event?.message || "Web IFC geometry worker failed"),
      );
    }
  };

  void (async () => {
    try {
      const response = await fetch(ifcFileUrl, {
        signal: abortController.signal,
      });
      if (!response.ok) {
        throw new Error(`IFC fetch failed with HTTP ${response.status}`);
      }
      const ifcBytes = await response.arrayBuffer();
      if (settled) return;
      worker.postMessage(
        { type: "load", requestId, ifcBytes },
        [ifcBytes],
      );
    } catch (error) {
      if (!settled && error?.name !== "AbortError") {
        settleWithError(
          error instanceof Error
            ? error
            : new Error("Failed to load IFC geometry"),
        );
      }
    }
  })();

  return function stopWebIfcGeometry() {
    cleanup();
  };
}
