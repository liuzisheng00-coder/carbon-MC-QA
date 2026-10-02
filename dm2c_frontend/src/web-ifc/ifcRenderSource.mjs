function mergeMeshesByGlobalId(previousGeometry, batch) {
  const meshes = new Map();
  const orderedIds = [];

  for (const mesh of previousGeometry?.meshes || []) {
    if (mesh?.globalId) {
      meshes.set(mesh.globalId, mesh);
      orderedIds.push(mesh.globalId);
    }
  }
  for (const mesh of batch?.meshes || []) {
    if (mesh?.globalId) {
      if (!meshes.has(mesh.globalId)) orderedIds.push(mesh.globalId);
      meshes.set(mesh.globalId, mesh);
    }
  }

  return {
    ...previousGeometry,
    ...batch,
    meshes: orderedIds.map((globalId) => meshes.get(globalId)),
  };
}

function hasMeshes(geometry) {
  return (geometry?.meshes?.length || 0) > 0;
}

export function formatIfcViewerStatus({ geometryMode, geometryProgress }) {
  const loadedMeshes = geometryProgress?.loadedMeshes || 0;
  const status = geometryProgress?.status || "loading";
  const errorSuffix = geometryProgress?.partialError
    ? ` (${geometryProgress.partialError})`
    : "";
  const totalSuffix = geometryProgress?.totalEligibleProducts
    ? ` / ${geometryProgress.totalEligibleProducts} products`
    : "";

  if (status === "web-partial-backend-error") {
    return `Web IFC partial / backend error: ${loadedMeshes} meshes${errorSuffix}`;
  }
  if (status === "web-partial-backend-empty") {
    return `Web IFC partial: ${loadedMeshes} meshes (backend IFC produced no meshes)`;
  }
  if (geometryMode === "web-partial-fallback") {
    return `Web IFC partial → backend IFC: ${loadedMeshes} meshes${errorSuffix}`;
  }
  if (geometryMode === "web-ifc") {
    return `Web IFC: ${loadedMeshes} meshes${status === "complete" ? "" : " loading"}`;
  }
  if (geometryMode === "backend-ifc") {
    if (status === "error") {
      return `backend IFC partial / backend error: ${loadedMeshes} meshes${errorSuffix}`;
    }
    return `backend IFC: ${loadedMeshes} meshes${status === "complete" ? "" : " loading"}${totalSuffix}`;
  }
  if (status === "backend-error" || status === "error") {
    return `account mesh / backend IFC error${errorSuffix}`;
  }
  return "account mesh";
}

export function createIfcRenderSourceController({
  startWebIfc,
  startBackend,
  onState,
}) {
  let webGeometry = { meshes: [] };
  let backendGeometry = { meshes: [] };
  let backendStarted = false;
  let backendFinished = false;
  let webCompleted = false;
  let started = false;
  let stopped = false;
  const cleanups = [];

  function emit(source, status, geometry, warning = null) {
    onState({ source, status, geometry, warning });
  }

  function install(startSource, handlers) {
    const cleanup = startSource(handlers);
    if (typeof cleanup === "function") cleanups.push(cleanup);
  }

  function handleBackendError(error) {
    if (stopped || backendFinished) return;
    backendFinished = true;
    if (hasMeshes(backendGeometry)) {
      emit("backend-ifc", "error", backendGeometry, error);
    } else if (hasMeshes(webGeometry)) {
      emit("web-ifc", "web-partial-backend-error", webGeometry, error);
    } else {
      emit("account", "backend-error", { meshes: [] }, error);
    }
  }

  function startBackendSource() {
    if (backendStarted || stopped) return;
    backendStarted = true;
    const handlers = {
      onBatch(geometry) {
        if (stopped || backendFinished) return;
        backendGeometry = geometry || { meshes: [] };
        if (!hasMeshes(backendGeometry) && hasMeshes(webGeometry)) return;
        emit("backend-ifc", "loading", backendGeometry);
      },
      onComplete() {
        if (stopped || backendFinished) return;
        backendFinished = true;
        if (hasMeshes(backendGeometry)) {
          emit("backend-ifc", "complete", backendGeometry);
        } else if (hasMeshes(webGeometry)) {
          emit("web-ifc", "web-partial-backend-empty", webGeometry);
        } else {
          emit("account", "complete", { meshes: [] });
        }
      },
      onError(error) {
        handleBackendError(error);
      },
    };
    try {
      install(startBackend, handlers);
    } catch (error) {
      handleBackendError(error);
    }
  }

  function handleWebError(error) {
    if (stopped || backendStarted || webCompleted) return;
    const hadPartialGeometry = hasMeshes(webGeometry);
    if (!hadPartialGeometry) webGeometry = { meshes: [] };
    emit(
      "web-ifc",
      hadPartialGeometry ? "web-partial-fallback" : "web-fallback",
      webGeometry,
      error,
    );
    startBackendSource();
  }

  return {
    start() {
      if (started || stopped) return;
      started = true;
      const handlers = {
        onBatch(geometry) {
          if (stopped || backendStarted || webCompleted) return;
          webGeometry = mergeMeshesByGlobalId(webGeometry, geometry);
          emit("web-ifc", "loading", webGeometry);
        },
        onComplete() {
          if (stopped || backendStarted || webCompleted) return;
          webCompleted = true;
          emit("web-ifc", "complete", webGeometry);
        },
        onError(error) {
          handleWebError(error);
        },
      };
      try {
        install(startWebIfc, handlers);
      } catch (error) {
        handleWebError(error);
      }
    },
    stop() {
      if (stopped) return;
      stopped = true;
      let cleanupError;
      for (const cleanup of cleanups.splice(0)) {
        try {
          cleanup();
        } catch (error) {
          if (cleanupError === undefined) cleanupError = error;
        }
      }
      if (cleanupError !== undefined) throw cleanupError;
    },
  };
}
