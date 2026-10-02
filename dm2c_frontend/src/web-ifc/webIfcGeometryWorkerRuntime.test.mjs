import assert from "node:assert/strict";
import test from "node:test";

import {
  createWebIfcGeometryWorkerRuntime,
} from "./webIfcGeometryWorker.mjs";

function vector(values, onDelete = () => {}) {
  return {
    get(index) {
      return values[index];
    },
    size() {
      return values.length;
    },
    delete: onDelete,
  };
}

function createWorkerScope(log) {
  const posts = [];
  return {
    posts,
    postMessage(message, transfer = []) {
      log.push(`post:${message.type}`);
      posts.push({ message, transfer });
    },
  };
}

function createApiHarness(log, { initPromise, loadError } = {}) {
  const instances = [];
  const transform = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    5, 6, 7, 1,
  ];
  const vertices = [
    0, 0, 0, 0, 0, 1,
    1, 0, 0, 0, 0, 1,
    0, 1, 0, 0, 0, 1,
  ];
  const indices = [0, 1, 2];

  class FakeIfcAPI {
    constructor() {
      this.openedBytes = null;
      instances.push(this);
      log.push("construct");
    }

    SetWasmPath(path, absolute) {
      log.push(`path:${path}:${absolute}`);
    }

    async Init(locateFile, forceSingleThread) {
      log.push(`init:${String(locateFile)}:${forceSingleThread}`);
      await initPromise;
    }

    OpenModel(bytes) {
      this.openedBytes = bytes;
      log.push("open");
      return 7;
    }

    LoadAllGeometry() {
      log.push("load-all");
      if (loadError) throw loadError;
      const placed = {
        geometryExpressID: 10,
        flatTransformation: transform,
        color: { x: 1, y: 0, z: 0, w: 1 },
      };
      const mesh = {
        expressID: 42,
        geometries: vector([placed]),
        delete() {
          log.push("flat-mesh-delete");
        },
      };
      return vector([mesh], () => log.push("mesh-vector-delete"));
    }

    GetLine() {
      return {
        GlobalId: { value: "worker-guid" },
        Name: { value: "Worker Beam" },
      };
    }

    GetGeometry() {
      return {
        GetVertexData: () => 100,
        GetVertexDataSize: () => vertices.length,
        GetIndexData: () => 200,
        GetIndexDataSize: () => indices.length,
        delete() {
          log.push("geometry-delete");
        },
      };
    }

    GetVertexArray(pointer) {
      assert.equal(pointer, 100);
      return vertices;
    }

    GetIndexArray(pointer) {
      assert.equal(pointer, 200);
      return indices;
    }

    CloseModel(modelId) {
      assert.equal(modelId, 7);
      log.push("close");
    }

    Dispose() {
      log.push("dispose-api");
    }
  }

  return { ApiClass: FakeIfcAPI, instances };
}

test("actual worker module posts transferred batch then completes after cleanup", async () => {
  const log = [];
  const scope = createWorkerScope(log);
  const { ApiClass, instances } = createApiHarness(log);
  createWebIfcGeometryWorkerRuntime({
    IfcAPIClass: ApiClass,
    workerScope: scope,
    yieldControl: async () => {},
  });
  const ifcBytes = new Uint8Array([1, 2, 3, 4]).buffer;

  await scope.onmessage({
    data: { type: "load", requestId: 11, ifcBytes },
  });

  assert.equal(instances.length, 1);
  assert.deepEqual([...instances[0].openedBytes], [1, 2, 3, 4]);
  assert.deepEqual(
    scope.posts.map(({ message }) => message.type),
    ["batch", "complete"],
  );
  const batch = scope.posts[0];
  assert.equal(batch.message.requestId, 11);
  assert.equal(batch.message.meshes[0].globalId, "worker-guid");
  assert.deepEqual([...batch.message.meshes[0].vertices], [
    5, 6, 7,
    6, 6, 7,
    5, 7, 7,
  ]);
  assert.deepEqual(batch.transfer, [
    batch.message.meshes[0].vertices.buffer,
    batch.message.meshes[0].indices.buffer,
    batch.message.meshes[0].materialIds.buffer,
  ]);
  assert.ok(log.indexOf("close") < log.indexOf("post:complete"));
  assert.ok(log.indexOf("dispose-api") < log.indexOf("post:complete"));
  assert.ok(log.includes("geometry-delete"));
  assert.ok(log.includes("flat-mesh-delete"));
  assert.ok(log.includes("mesh-vector-delete"));
});

test("actual worker module reports parser errors only after model/API cleanup", async () => {
  const log = [];
  const scope = createWorkerScope(log);
  const { ApiClass } = createApiHarness(log, {
    loadError: new Error("native parser failed"),
  });
  createWebIfcGeometryWorkerRuntime({
    IfcAPIClass: ApiClass,
    workerScope: scope,
    yieldControl: async () => {},
  });

  await scope.onmessage({
    data: {
      type: "load",
      requestId: 12,
      ifcBytes: new ArrayBuffer(4),
    },
  });

  assert.deepEqual(
    scope.posts.map(({ message }) => message.type),
    ["error"],
  );
  assert.match(scope.posts[0].message.message, /native parser failed/);
  assert.ok(log.indexOf("close") < log.indexOf("post:error"));
  assert.ok(log.indexOf("dispose-api") < log.indexOf("post:error"));
});

test("actual worker module dispose during Init prevents open/post and disposes API", async () => {
  let releaseInit;
  const initPromise = new Promise((resolve) => {
    releaseInit = resolve;
  });
  const log = [];
  const scope = createWorkerScope(log);
  const { ApiClass } = createApiHarness(log, { initPromise });
  createWebIfcGeometryWorkerRuntime({
    IfcAPIClass: ApiClass,
    workerScope: scope,
    yieldControl: async () => {},
  });

  const loading = scope.onmessage({
    data: {
      type: "load",
      requestId: 13,
      ifcBytes: new ArrayBuffer(4),
    },
  });
  scope.onmessage({
    data: { type: "dispose", requestId: 13 },
  });
  releaseInit();
  await loading;

  assert.deepEqual(scope.posts, []);
  assert.ok(log.includes("dispose-api"));
  assert.equal(log.includes("open"), false);
  assert.equal(log.includes("close"), false);
});
