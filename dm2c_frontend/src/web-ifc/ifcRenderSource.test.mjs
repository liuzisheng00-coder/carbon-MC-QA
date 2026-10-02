import assert from "node:assert/strict";
import test from "node:test";

import { createIfcRenderSourceController } from "./ifcRenderSource.mjs";

function createSource() {
  const source = { starts: 0, handlers: null, cleanups: 0 };
  return {
    source,
    start(handlers) {
      source.starts += 1;
      source.handlers = handlers;
      return () => {
        source.cleanups += 1;
      };
    },
  };
}

test("falls back from partial Web IFC geometry and replaces it with backend geometry", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onBatch({ meshes: [{ globalId: "A" }] });
  web.source.handlers.onError(new Error("WASM unavailable"));

  assert.equal(backend.source.starts, 1);
  assert.equal(states.at(-1).status, "web-partial-fallback");

  backend.source.handlers.onBatch({
    meshes: [{ globalId: "A" }, { globalId: "B" }],
  });

  assert.equal(states.at(-1).source, "backend-ifc");
  assert.deepEqual(
    states.at(-1).geometry.meshes.map((mesh) => mesh.globalId),
    ["A", "B"],
  );
});

test("does not start the backend when Web IFC completes successfully", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onBatch({ meshes: [{ globalId: "A" }] });
  web.source.handlers.onComplete();
  web.source.handlers.onBatch({ meshes: [{ globalId: "B" }] });
  web.source.handlers.onError(new Error("late Web IFC error"));

  assert.equal(backend.source.starts, 0);
  assert.equal(states.at(-1).source, "web-ifc");
  assert.equal(states.at(-1).status, "complete");
  assert.deepEqual(states.at(-1).geometry.meshes, [{ globalId: "A" }]);
});

test("accumulates Web IFC batches by GlobalId", () => {
  const web = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: () => () => {},
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onBatch({ meshes: [{ globalId: "A", name: "first" }] });
  web.source.handlers.onBatch({
    meshes: [{ globalId: "A", name: "updated" }, { globalId: "B" }],
  });

  assert.deepEqual(
    states.at(-1).geometry.meshes,
    [{ globalId: "A", name: "updated" }, { globalId: "B" }],
  );
});

test("clears Web IFC geometry before starting backend after an initial failure", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onError(new Error("WASM unavailable"));

  assert.equal(backend.source.starts, 1);
  assert.equal(states.at(-1).source, "web-ifc");
  assert.deepEqual(states.at(-1).geometry.meshes, []);
});

test("stops every installed source cleanup function", () => {
  const web = createSource();
  const backend = createSource();
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: () => {},
  });

  controller.start();
  web.source.handlers.onError(new Error("WASM unavailable"));
  controller.stop();

  assert.equal(web.source.cleanups, 1);
  assert.equal(backend.source.cleanups, 1);
});

test("runs remaining cleanup functions when an installed cleanup throws", () => {
  const web = createSource();
  const backend = createSource();
  const controller = createIfcRenderSourceController({
    startWebIfc(handlers) {
      const cleanup = web.start(handlers);
      return () => {
        cleanup();
        throw new Error("Web cleanup failed");
      };
    },
    startBackend: backend.start,
    onState: () => {},
  });

  controller.start();
  web.source.handlers.onError(new Error("WASM unavailable"));

  assert.throws(() => controller.stop(), /Web cleanup failed/);
  assert.equal(web.source.cleanups, 1);
  assert.equal(backend.source.cleanups, 1);
});

test("starts backend when the Web IFC source throws during synchronous startup", () => {
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc() {
      throw new Error("Worker construction blocked");
    },
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  assert.doesNotThrow(() => controller.start());
  assert.equal(backend.source.starts, 1);
  assert.equal(states[0].status, "web-fallback");
  assert.match(states[0].warning.message, /Worker construction blocked/);
});

test("retains partial Web geometry through empty backend batches and backend failure", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onBatch({ meshes: [{ globalId: "WEB" }] });
  web.source.handlers.onError(new Error("WASM failed"));
  const partialFallbackState = states.at(-1);
  backend.source.handlers.onBatch({ meshes: [] });

  assert.equal(states.at(-1), partialFallbackState);

  backend.source.handlers.onError(new Error("backend failed"));
  assert.equal(states.at(-1).source, "web-ifc");
  assert.equal(states.at(-1).status, "web-partial-backend-error");
  assert.deepEqual(states.at(-1).geometry.meshes, [{ globalId: "WEB" }]);
  assert.match(states.at(-1).warning.message, /backend failed/);
});

test("empty backend completion retains partial Web geometry", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onBatch({ meshes: [{ globalId: "WEB" }] });
  web.source.handlers.onError(new Error("WASM failed"));
  backend.source.handlers.onBatch({ meshes: [] });
  backend.source.handlers.onComplete();

  assert.equal(states.at(-1).source, "web-ifc");
  assert.equal(states.at(-1).status, "web-partial-backend-empty");
  assert.deepEqual(states.at(-1).geometry.meshes, [{ globalId: "WEB" }]);
});

test("first usable backend batch replaces retained partial Web geometry", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onBatch({ meshes: [{ globalId: "WEB" }] });
  web.source.handlers.onError(new Error("WASM failed"));
  backend.source.handlers.onBatch({ meshes: [] });
  backend.source.handlers.onBatch({ meshes: [{ globalId: "BACKEND" }] });

  assert.equal(states.at(-1).source, "backend-ifc");
  assert.deepEqual(states.at(-1).geometry.meshes, [{ globalId: "BACKEND" }]);
});

test("uses account mesh only when Web and backend both end without geometry", () => {
  const web = createSource();
  const backend = createSource();
  const states = [];
  const controller = createIfcRenderSourceController({
    startWebIfc: web.start,
    startBackend: backend.start,
    onState: (state) => states.push(state),
  });

  controller.start();
  web.source.handlers.onError(new Error("Worker failed"));
  backend.source.handlers.onError(new Error("backend failed"));

  assert.equal(states.at(-1).source, "account");
  assert.equal(states.at(-1).status, "backend-error");
  assert.deepEqual(states.at(-1).geometry.meshes, []);
});

test("formats terminal backend failures without calling them loading", async () => {
  const { formatIfcViewerStatus } = await import("./ifcRenderSource.mjs");
  assert.equal(
    formatIfcViewerStatus({
      geometryMode: "backend-ifc",
      geometryProgress: {
        loadedMeshes: 2,
        status: "error",
        partialError: "page failed",
      },
    }),
    "backend IFC partial / backend error: 2 meshes (page failed)",
  );
  assert.equal(
    formatIfcViewerStatus({
      geometryMode: "web-ifc",
      geometryProgress: {
        loadedMeshes: 1,
        status: "web-partial-backend-error",
        partialError: "backend failed",
      },
    }),
    "Web IFC partial / backend error: 1 meshes (backend failed)",
  );
  assert.equal(
    formatIfcViewerStatus({
      geometryMode: "account",
      geometryProgress: {
        loadedMeshes: 0,
        status: "backend-error",
        partialError: "backend failed",
      },
    }),
    "account mesh / backend IFC error (backend failed)",
  );
  assert.equal(
    formatIfcViewerStatus({
      geometryMode: "account",
      geometryProgress: {
        loadedMeshes: 0,
        status: "error",
        partialError: "static geometry failed",
      },
    }),
    "account mesh / backend IFC error (static geometry failed)",
  );
});
