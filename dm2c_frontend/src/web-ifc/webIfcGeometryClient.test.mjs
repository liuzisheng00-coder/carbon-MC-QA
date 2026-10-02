import assert from "node:assert/strict";
import test from "node:test";

import { startWebIfcGeometry } from "./webIfcGeometryClient.mjs";

function installBrowserFakes(t, { arrayBuffer = new ArrayBuffer(8) } = {}) {
  const originalFetch = globalThis.fetch;
  const originalWorker = globalThis.Worker;
  const workers = [];
  const fetchCalls = [];

  class FakeWorker {
    constructor(url, options) {
      this.url = url;
      this.options = options;
      this.messages = [];
      this.terminated = false;
      workers.push(this);
    }

    postMessage(message, transfer = []) {
      this.messages.push({ message, transfer });
    }

    terminate() {
      this.terminated = true;
    }

    emit(message) {
      this.onmessage?.({ data: message });
    }
  }

  globalThis.Worker = FakeWorker;
  globalThis.fetch = async (url, options) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async arrayBuffer() {
        return arrayBuffer;
      },
    };
  };

  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Worker = originalWorker;
  });

  return { arrayBuffer, fetchCalls, workers };
}

async function startFixture(t, callbacks = {}) {
  const fakes = installBrowserFakes(t);
  const batches = [];
  const errors = [];
  const completions = [];
  const stop = startWebIfcGeometry({
    ifcFileUrl: "/model.ifc",
    onBatch: (batch) => batches.push(batch),
    onComplete: (complete) => completions.push(complete),
    onError: (error) => errors.push(error),
    ...callbacks,
  });

  await new Promise((resolve) => setImmediate(resolve));
  const worker = fakes.workers[0];
  const load = worker.messages[0];
  return {
    ...fakes,
    batches,
    completions,
    errors,
    load,
    stop,
    worker,
  };
}

test("delivers a valid worker batch to onBatch", async (t) => {
  const fixture = await startFixture(t);
  const { requestId } = fixture.load.message;

  fixture.worker.emit({
    type: "batch",
    requestId,
    meshes: [
      {
        globalId: "g1",
        expressId: 7,
        vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
      },
    ],
    loaded: 1,
    total: 1,
  });

  assert.equal(fixture.errors.length, 0);
  assert.equal(fixture.batches.length, 1);
  assert.equal(fixture.batches[0].meshes[0].globalId, "g1");
});

for (const [name, mesh] of [
  [
    "GlobalId",
    {
      expressId: 7,
      vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 2],
    },
  ],
  [
    "numeric vertices",
    {
      globalId: "g1",
      expressId: 7,
      vertices: [0, "bad", 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 2],
    },
  ],
  [
    "numeric indices",
    {
      globalId: "g1",
      expressId: 7,
      vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, "bad", 2],
    },
  ],
]) {
  test(`rejects a worker batch lacking ${name}`, async (t) => {
    const fixture = await startFixture(t);
    const { requestId } = fixture.load.message;

    fixture.worker.emit({
      type: "batch",
      requestId,
      meshes: [mesh],
      loaded: 1,
      total: 1,
    });

    assert.equal(fixture.batches.length, 0);
    assert.equal(fixture.errors.length, 1);
    assert.match(fixture.errors[0].message, /invalid web ifc geometry batch/i);
    assert.equal(fixture.worker.terminated, true);
  });
}

for (const [name, mesh] of [
  [
    "incomplete vertex triples",
    {
      globalId: "g1",
      vertices: [0, 0],
      indices: [0, 0, 0],
    },
  ],
  [
    "incomplete index triangles",
    {
      globalId: "g1",
      vertices: [0, 0, 0],
      indices: [0, 0],
    },
  ],
  [
    "out-of-range indices",
    {
      globalId: "g1",
      vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 3],
    },
  ],
  [
    "inconsistent material IDs",
    {
      globalId: "g1",
      vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 2],
      materials: [{ color: "#FFFFFF", opacity: 1 }],
      materialIds: [0, 0],
    },
  ],
]) {
  test(`rejects ${name} at the client boundary`, async (t) => {
    const fixture = await startFixture(t);
    const { requestId } = fixture.load.message;

    fixture.worker.emit({
      type: "batch",
      requestId,
      meshes: [mesh],
      loaded: 1,
      total: 1,
    });

    assert.equal(fixture.batches.length, 0);
    assert.equal(fixture.errors.length, 1);
    assert.equal(fixture.worker.terminated, true);
  });
}

test("accepts transferred typed vertex and index arrays", async (t) => {
  const fixture = await startFixture(t);
  const { requestId } = fixture.load.message;

  fixture.worker.emit({
    type: "batch",
    requestId,
    meshes: [
      {
        globalId: "g1",
        expressId: 7,
        vertices: new Float32Array([
          0, 0, 0,
          1, 0, 0,
          0, 1, 0,
        ]),
        indices: new Uint32Array([0, 1, 2]),
      },
    ],
    loaded: 1,
    total: 1,
  });

  assert.equal(fixture.errors.length, 0);
  assert.equal(fixture.batches.length, 1);
});

test("transfers fetched IFC bytes to the module worker", async (t) => {
  const fixture = await startFixture(t);

  assert.equal(fixture.fetchCalls[0].url, "/model.ifc");
  assert.ok(fixture.fetchCalls[0].options.signal instanceof AbortSignal);
  assert.equal(fixture.worker.options.type, "module");
  assert.match(fixture.worker.url.href, /webIfcGeometryWorker\.mjs$/);
  assert.equal(fixture.load.message.type, "load");
  assert.equal(fixture.load.message.ifcBytes, fixture.arrayBuffer);
  assert.deepEqual(fixture.load.transfer, [fixture.arrayBuffer]);
});

test("stop terminates the worker and aborts the raw IFC fetch", (t) => {
  const originalFetch = globalThis.fetch;
  const originalWorker = globalThis.Worker;
  let fetchSignal;
  let worker;

  class PendingWorker {
    constructor() {
      worker = this;
      this.terminated = false;
    }

    postMessage() {}

    terminate() {
      this.terminated = true;
    }
  }

  globalThis.Worker = PendingWorker;
  globalThis.fetch = (_url, { signal }) => {
    fetchSignal = signal;
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        reject(new DOMException("Aborted", "AbortError"));
      });
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Worker = originalWorker;
  });

  const errors = [];
  const stop = startWebIfcGeometry({
    ifcFileUrl: "/slow.ifc",
    onBatch: () => {},
    onComplete: () => {},
    onError: (error) => errors.push(error),
  });
  stop();

  assert.equal(worker.terminated, true);
  assert.equal(fetchSignal.aborted, true);
  assert.deepEqual(errors, []);
});

test("completion terminates the worker and suppresses later callbacks", async (t) => {
  const fixture = await startFixture(t);
  const { requestId } = fixture.load.message;

  fixture.worker.emit({
    type: "complete",
    requestId: requestId + 1,
    loaded: 99,
    total: 99,
  });
  fixture.worker.emit({
    type: "complete",
    requestId,
    loaded: 2,
    total: 2,
  });
  fixture.worker.emit({
    type: "error",
    requestId,
    message: "parser failed",
  });
  fixture.worker.emit({
    type: "batch",
    requestId,
    meshes: [{
      globalId: "late",
      vertices: [0, 0, 0],
      indices: [0, 0, 0],
    }],
  });

  assert.deepEqual(fixture.completions, [
    { type: "complete", requestId, loaded: 2, total: 2 },
  ]);
  assert.equal(fixture.worker.terminated, true);
  assert.equal(fixture.errors.length, 0);
  assert.equal(fixture.batches.length, 0);
});

test("parser error terminates the worker and suppresses later callbacks", async (t) => {
  const fixture = await startFixture(t);
  const { requestId } = fixture.load.message;

  fixture.worker.emit({
    type: "error",
    requestId,
    message: "parser failed",
  });
  fixture.worker.emit({
    type: "complete",
    requestId,
    loaded: 1,
    total: 1,
  });

  assert.equal(fixture.errors.length, 1);
  assert.match(fixture.errors[0].message, /parser failed/);
  assert.equal(fixture.worker.terminated, true);
  assert.equal(fixture.completions.length, 0);
});

test("fetch failure terminates the worker exactly once", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalWorker = globalThis.Worker;
  let worker;

  class FakeWorker {
    constructor() {
      worker = this;
      this.terminationCount = 0;
    }

    postMessage() {}

    terminate() {
      this.terminationCount += 1;
    }
  }

  globalThis.Worker = FakeWorker;
  globalThis.fetch = async () => ({
    ok: false,
    status: 503,
  });
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Worker = originalWorker;
  });

  const errors = [];
  startWebIfcGeometry({
    ifcFileUrl: "/unavailable.ifc",
    onBatch: () => {},
    onComplete: () => {},
    onError: (error) => errors.push(error),
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /HTTP 503/);
  assert.equal(worker.terminationCount, 1);
});

test("native worker failure is terminal", async (t) => {
  const fixture = await startFixture(t);

  fixture.worker.onerror({ message: "worker crashed" });
  fixture.worker.emit({
    type: "error",
    requestId: fixture.load.message.requestId,
    message: "late parser error",
  });

  assert.equal(fixture.errors.length, 1);
  assert.match(fixture.errors[0].message, /worker crashed/);
  assert.equal(fixture.worker.terminated, true);
});

test("reports a synchronous module Worker construction failure", (t) => {
  const originalWorker = globalThis.Worker;
  globalThis.Worker = class BlockedWorker {
    constructor() {
      throw new Error("module Workers blocked");
    }
  };
  t.after(() => {
    globalThis.Worker = originalWorker;
  });

  const errors = [];
  let stop;
  assert.doesNotThrow(() => {
    stop = startWebIfcGeometry({
      ifcFileUrl: "/model.ifc",
      onBatch: () => {},
      onComplete: () => {},
      onError: (error) => errors.push(error),
    });
  });

  assert.equal(typeof stop, "function");
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /module Workers blocked/);
  assert.doesNotThrow(() => stop());
});
