import assert from "node:assert/strict";
import test from "node:test";

import {
  FragmentLoadError,
  FRAGMENT_CONVERTER_VERSION,
  loadProjectFragment,
  loadSharedProjectFragment,
} from "./fragmentLoader.mjs";

function makeCache(entries = []) {
  const values = new Map(entries);
  return {
    get: async (key) => values.get(key),
    set: async (key, value) => values.set(key, value),
    values,
  };
}

function makeFetch(body = new Uint8Array([1, 2, 3]), status = 200, requests = []) {
  return async (url, init) => {
    requests.push({ url, init });
    return {
      ok: status >= 200 && status < 300,
      status,
      arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength),
    };
  };
}

function makeWorkerFactory({ fragmentBytes = new Uint8Array([9, 8, 7]), error } = {}) {
  const workers = [];
  const factory = () => {
    const listeners = new Map();
    const worker = {
      posted: [],
      terminated: false,
      addEventListener(type, callback) {
        listeners.set(type, callback);
      },
      postMessage(message, transfer) {
        this.posted.push({ message, transfer });
        queueMicrotask(() => {
          if (error) {
            listeners.get("message")?.({ data: { type: "error", error } });
          } else {
            listeners.get("message")?.({
              data: {
                type: "converted",
                fragmentBytes: fragmentBytes.buffer.slice(
                  fragmentBytes.byteOffset,
                  fragmentBytes.byteOffset + fragmentBytes.byteLength,
                ),
              },
            });
          }
        });
      },
      terminate() {
        this.terminated = true;
      },
    };
    workers.push(worker);
    return worker;
  };
  factory.workers = workers;
  return factory;
}

test("retrieves only the explicit project IFC endpoint without credentialed fetch options", async () => {
  const requests = [];
  const workerFactory = makeWorkerFactory();

  await loadProjectFragment({
    projectId: "project-42",
    ifcFileUrl: "http://127.0.0.1:8000/api/projects/project-42/ifc-file",
    cache: makeCache(),
    fetchImpl: makeFetch(undefined, 200, requests),
    workerFactory,
  });

  assert.equal(requests[0].url, "http://127.0.0.1:8000/api/projects/project-42/ifc-file");
  assert.equal(requests[0].init, undefined);

  await assert.rejects(
    loadProjectFragment({
      projectId: "project-42",
      ifcFileUrl: "http://127.0.0.1:8000/api/projects/project-42/ifc-geometry",
      cache: makeCache(),
      fetchImpl: makeFetch(),
      workerFactory,
    }),
    /must end with \/ifc-file/,
  );
});

test("cache miss converts in one module Worker and stores bytes under the converter-version key", async () => {
  const cache = makeCache();
  const workerFactory = makeWorkerFactory();

  const result = await loadProjectFragment({
    projectId: "project-42",
    ifcFileUrl: "http://127.0.0.1:8000/api/projects/project-42/ifc-file",
    cache,
    fetchImpl: makeFetch(),
    workerFactory,
  });

  assert.equal(result.source, "fragment-worker");
  assert.deepEqual([...result.fragmentBytes], [9, 8, 7]);
  assert.equal(workerFactory.workers.length, 1);
  assert.equal(workerFactory.workers[0].posted.length, 1);
  assert.equal(workerFactory.workers[0].terminated, true);
  assert.match(result.cacheKey, new RegExp(`^${FRAGMENT_CONVERTER_VERSION.replaceAll(".", "\\.")}:`));
  assert.deepEqual([...cache.values.get(result.cacheKey)], [9, 8, 7]);
});

test("cache hit returns cached fragment bytes without creating a Worker", async () => {
  const firstFactory = makeWorkerFactory();
  const cache = makeCache();
  const args = {
    projectId: "project-42",
    ifcFileUrl: "http://127.0.0.1:8000/api/projects/project-42/ifc-file",
    cache,
    fetchImpl: makeFetch(),
  };
  const first = await loadProjectFragment({ ...args, workerFactory: firstFactory });
  const secondFactory = makeWorkerFactory();

  const second = await loadProjectFragment({ ...args, workerFactory: secondFactory });

  assert.equal(first.cacheKey, second.cacheKey);
  assert.equal(second.source, "fragment-cache");
  assert.deepEqual([...second.fragmentBytes], [9, 8, 7]);
  assert.equal(secondFactory.workers.length, 0);
});

test("reports concrete HTTP failures", async () => {
  await assert.rejects(
    loadProjectFragment({
      projectId: "missing",
      ifcFileUrl: "http://127.0.0.1:8000/api/projects/missing/ifc-file",
      cache: makeCache(),
      fetchImpl: makeFetch(new Uint8Array(), 404),
      workerFactory: makeWorkerFactory(),
    }),
    /IFC file request failed with HTTP 404/,
  );
});

test("normalizes Worker failures to fragment-error", async () => {
  await assert.rejects(
    loadProjectFragment({
      projectId: "project-42",
      ifcFileUrl: "http://127.0.0.1:8000/api/projects/project-42/ifc-file",
      cache: makeCache(),
      fetchImpl: makeFetch(),
      workerFactory: makeWorkerFactory({ error: "conversion exploded" }),
    }),
    (error) => {
      assert.ok(error instanceof FragmentLoadError);
      assert.equal(error.source, "fragment-error");
      assert.match(error.message, /conversion exploded/);
      return true;
    },
  );
});

test("StrictMode duplicate consumers share one in-flight IFC conversion", async () => {
  const requests = [];
  const workerFactory = makeWorkerFactory();
  const args = {
    projectId: "strict-mode-project",
    ifcFileUrl: "http://127.0.0.1:8000/api/projects/strict-mode-project/ifc-file",
    cache: makeCache(),
    fetchImpl: makeFetch(undefined, 200, requests),
    workerFactory,
  };

  const probeSetup = loadSharedProjectFragment(args);
  const liveSetup = loadSharedProjectFragment(args);
  const [probeResult, liveResult] = await Promise.all([probeSetup, liveSetup]);

  assert.equal(requests.length, 1);
  assert.equal(workerFactory.workers.length, 1);
  assert.deepEqual([...probeResult.fragmentBytes], [9, 8, 7]);
  assert.deepEqual([...liveResult.fragmentBytes], [9, 8, 7]);
});
