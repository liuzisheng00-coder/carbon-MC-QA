import assert from "node:assert/strict";
import test from "node:test";

import {
  FragmentLoadError,
  loadProjectFragment,
} from "./fragmentLoader.mjs";

const PROJECT_ID = "project-42";
const IFC_FILE_URL =
  "http://127.0.0.1:8000/api/projects/project-42/ifc-file";
const IFC_BYTES = new TextEncoder().encode("ISO-10303-21;IFC fixture");
const CACHED_FRAGMENT_BYTES = new Uint8Array([11, 22, 33]);
const CONVERTED_FRAGMENT_BYTES = new Uint8Array([44, 55, 66]);

function installFetch(t, handler) {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), init });
    return handler(url, init);
  };

  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  return requests;
}

function ifcResponse(bytes = IFC_BYTES) {
  return {
    ok: true,
    status: 200,
    async arrayBuffer() {
      return bytes.buffer.slice(
        bytes.byteOffset,
        bytes.byteOffset + bytes.byteLength,
      );
    },
  };
}

function installWorker(t, WorkerImplementation) {
  const OriginalWorker = globalThis.Worker;
  globalThis.Worker = WorkerImplementation;
  t.after(() => {
    globalThis.Worker = OriginalWorker;
  });
}

test("a cache hit returns fragment bytes without starting a converter Worker", async (t) => {
  const requests = installFetch(t, async () => ifcResponse());
  installWorker(
    t,
    class UnexpectedWorker {
      constructor() {
        throw new Error("a cache hit must not start a Worker");
      }
    },
  );

  const cacheReads = [];
  const cache = {
    async get(key) {
      cacheReads.push(key);
      return CACHED_FRAGMENT_BYTES;
    },
    async set() {
      throw new Error("a cache hit must not write the cache");
    },
  };

  const result = await loadProjectFragment({
    projectId: PROJECT_ID,
    ifcFileUrl: IFC_FILE_URL,
    cache,
  });

  assert.equal(result.source, "fragment-cache");
  assert.deepEqual(result.fragmentBytes, CACHED_FRAGMENT_BYTES);
  assert.deepEqual(
    requests.map(({ url }) => url),
    [IFC_FILE_URL],
  );
  assert.equal(
    requests[0].init?.credentials,
    undefined,
    "the unauthenticated cross-origin IFC request must not opt into cookies",
  );
  assert.equal(cacheReads.length, 1);
  assert.match(cacheReads[0], /^dm2c-fragment-cache:v1:[^:]+:[a-f0-9]{64}$/);
});

test("a cache miss starts exactly one module Worker and stores its converted bytes", async (t) => {
  const requests = installFetch(t, async () => ifcResponse());
  const workerConstructions = [];
  const workerMessages = [];
  const cacheWrites = [];

  class SuccessfulWorker {
    constructor(url, options) {
      workerConstructions.push({ url: String(url), options });
    }

    postMessage(message, transfer) {
      workerMessages.push({ message, transfer });
      queueMicrotask(() => {
        this.onmessage?.({
          data: {
            type: "progress",
            progress: 0.5,
            message: "Converting IFC",
          },
        });
        const converted = CONVERTED_FRAGMENT_BYTES.slice();
        this.onmessage?.({
          data: {
            type: "result",
            fragmentBytes: converted.buffer,
          },
        });
      });
    }

    terminate() {}
  }

  installWorker(t, SuccessfulWorker);
  const cache = {
    async get() {
      return undefined;
    },
    async set(key, bytes) {
      cacheWrites.push({ key, bytes });
    },
  };
  const states = [];

  const result = await loadProjectFragment({
    projectId: PROJECT_ID,
    ifcFileUrl: IFC_FILE_URL,
    cache,
    onProgress(update) {
      states.push(update.state);
    },
  });

  assert.equal(result.source, "fragment-converted");
  assert.deepEqual(result.fragmentBytes, CONVERTED_FRAGMENT_BYTES);
  assert.equal(workerConstructions.length, 1);
  assert.equal(workerConstructions[0].options.type, "module");
  assert.equal(workerMessages.length, 1);
  assert.equal(workerMessages[0].message.type, "convert");
  assert.deepEqual(
    new Uint8Array(workerMessages[0].message.ifcBytes),
    IFC_BYTES,
  );
  assert.equal(cacheWrites.length, 1);
  assert.deepEqual(cacheWrites[0].bytes, CONVERTED_FRAGMENT_BYTES);
  assert.deepEqual(
    requests.map(({ url }) => url),
    [IFC_FILE_URL],
  );
  assert.deepEqual(states, [
    "fragment-fetch",
    "fragment-cache-lookup",
    "fragment-converting",
    "fragment-converting",
    "fragment-cache-write",
    "fragment-ready",
  ]);
  assert.ok(
    [...states, ...requests.map(({ url }) => url)].every(
      (value) =>
        !value.includes("web-ifc") && !value.includes("ifc-geometry"),
    ),
  );
});

test("a converter Worker error rejects with the fragment-error source and visible detail", async (t) => {
  installFetch(t, async () => ifcResponse());
  let terminated = false;

  class FailingWorker {
    postMessage() {
      queueMicrotask(() => {
        this.onmessage?.({
          data: {
            type: "error",
            message: "Unsupported IFC schema",
          },
        });
      });
    }

    terminate() {
      terminated = true;
    }
  }

  installWorker(t, FailingWorker);

  await assert.rejects(
    loadProjectFragment({
      projectId: PROJECT_ID,
      ifcFileUrl: IFC_FILE_URL,
      cache: {
        async get() {
          return undefined;
        },
        async set() {
          throw new Error("failed conversions must not be cached");
        },
      },
    }),
    (error) => {
      assert.ok(error instanceof FragmentLoadError);
      assert.equal(error.source, "fragment-error");
      assert.match(error.message, /Unsupported IFC schema/);
      return true;
    },
  );
  assert.equal(terminated, true);
});

test("a non-project IFC URL is rejected before any request is sent", async (t) => {
  const requests = installFetch(t, async () => {
    throw new Error("invalid endpoints must not be fetched");
  });

  await assert.rejects(
    loadProjectFragment({
      projectId: PROJECT_ID,
      ifcFileUrl:
        "http://127.0.0.1:8000/api/projects/project-42/ifc-geometry",
      cache: {
        async get() {
          return undefined;
        },
        async set() {},
      },
    }),
    (error) => {
      assert.equal(error.source, "fragment-error");
      assert.match(error.message, /must end with/);
      return true;
    },
  );
  assert.deepEqual(requests, []);
});

test("an IFC download failure rejects explicitly as fragment-error", async (t) => {
  installFetch(t, async () => ({
    ok: false,
    status: 503,
    async arrayBuffer() {
      throw new Error("arrayBuffer must not be read for a failed response");
    },
  }));

  await assert.rejects(
    loadProjectFragment({
      projectId: PROJECT_ID,
      ifcFileUrl: IFC_FILE_URL,
      cache: {
        async get() {
          return undefined;
        },
        async set() {},
      },
    }),
    (error) => {
      assert.equal(error.source, "fragment-error");
      assert.match(error.message, /503/);
      return true;
    },
  );
});
