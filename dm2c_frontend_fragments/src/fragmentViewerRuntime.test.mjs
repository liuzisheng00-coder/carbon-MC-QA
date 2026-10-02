import assert from "node:assert/strict";
import test from "node:test";

import { deriveFragmentRenderPolicy } from "./fragmentViewerState.mjs";
import {
  FRAGMENT_VIEWER_STATUS,
  applyFragmentRenderPolicy,
  isXrayEnabled,
  loadFragmentRuntime,
} from "./fragmentViewerRuntime.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("X-Ray application leaves the selected fragment opaque and high contrast", async () => {
  const itemState = new Map([
    [17, { color: "#8a8a8a", opacity: 1, transparent: false }],
    [23, { color: "#4f4f4f", opacity: 1, transparent: false }],
  ]);
  const model = {
    async resetColor() {
      itemState.get(17).color = "#8a8a8a";
      itemState.get(23).color = "#4f4f4f";
    },
    async resetOpacity() {
      for (const state of itemState.values()) {
        state.opacity = 1;
        state.transparent = false;
      }
    },
    async setOpacity(localIds, opacity) {
      const ids = localIds ?? [...itemState.keys()];
      for (const localId of ids) {
        const state = itemState.get(localId);
        state.opacity = opacity;
        state.transparent = opacity < 1;
      }
    },
    async setColor(localIds, color) {
      for (const localId of localIds) {
        itemState.get(localId).color = `#${color.getHexString()}`;
      }
    },
  };
  const fragments = {
    list: new Map([["project-42", model]]),
    core: {
      async update() {},
    },
    async resetHighlight() {},
    async guidsToModelIdMap() {
      return { "project-42": new Set([17]) };
    },
  };

  await applyFragmentRenderPolicy(
    { fragments, model },
    deriveFragmentRenderPolicy({
      xray: true,
      selectedGlobalId: "2O2Fr$t4X7Zf8NOew3FNr2",
    }),
  );

  assert.deepEqual(itemState.get(17), {
    color: "#ffb000",
    opacity: 1,
    transparent: false,
  });
  assert.deepEqual(itemState.get(23), {
    color: "#4f4f4f",
    opacity: 0.16,
    transparent: true,
  });
});

test("changing and clearing selection restores former selections to X-Ray styling", async () => {
  const originalColors = new Map([
    [17, "#8a8a8a"],
    [23, "#4f4f4f"],
  ]);
  const itemState = new Map(
    [...originalColors].map(([localId, color]) => [
      localId,
      { color, opacity: 1, transparent: false },
    ]),
  );
  const model = {
    async resetColor(localIds) {
      const ids = localIds ?? [...itemState.keys()];
      for (const localId of ids) {
        itemState.get(localId).color = originalColors.get(localId);
      }
    },
    async resetOpacity() {
      for (const state of itemState.values()) {
        state.opacity = 1;
        state.transparent = false;
      }
    },
    async setOpacity(localIds, opacity) {
      const ids = localIds ?? [...itemState.keys()];
      for (const localId of ids) {
        const state = itemState.get(localId);
        state.opacity = opacity;
        state.transparent = opacity < 1;
      }
    },
    async setColor(localIds, color) {
      for (const localId of localIds) {
        itemState.get(localId).color = `#${color.getHexString()}`;
      }
    },
  };
  const idByGlobalId = {
    "global-a": 17,
    "global-b": 23,
  };
  const fragments = {
    list: new Map([["project-42", model]]),
    core: {
      async update() {},
    },
    async resetHighlight() {},
    async guidsToModelIdMap([globalId]) {
      return {
        "project-42": new Set([idByGlobalId[globalId]]),
      };
    },
  };
  const runtime = { fragments, model };

  await applyFragmentRenderPolicy(
    runtime,
    deriveFragmentRenderPolicy({
      xray: true,
      selectedGlobalId: "global-a",
    }),
  );
  await applyFragmentRenderPolicy(
    runtime,
    deriveFragmentRenderPolicy({
      xray: true,
      selectedGlobalId: "global-b",
    }),
  );

  assert.deepEqual(itemState.get(17), {
    color: "#8a8a8a",
    opacity: 0.16,
    transparent: true,
  });
  assert.deepEqual(itemState.get(23), {
    color: "#ffb000",
    opacity: 1,
    transparent: false,
  });

  await applyFragmentRenderPolicy(
    runtime,
    deriveFragmentRenderPolicy({
      xray: true,
      selectedGlobalId: null,
    }),
  );

  assert.deepEqual(itemState.get(17), {
    color: "#8a8a8a",
    opacity: 0.16,
    transparent: true,
  });
  assert.deepEqual(itemState.get(23), {
    color: "#4f4f4f",
    opacity: 0.16,
    transparent: true,
  });
});

test("Fragment ready and X-Ray availability wait for core model loading and preparation", async () => {
  const pendingModel = deferred();
  const statuses = [];
  const events = [];

  const runtimePromise = loadFragmentRuntime({
    async loadBytes({ onProgress }) {
      onProgress({ state: "fragment-ready" });
      events.push("bytes-ready");
      return {
        source: "fragment-converted",
        fragmentBytes: new Uint8Array([1, 2, 3]),
      };
    },
    async loadModel() {
      events.push("model-load-started");
      return pendingModel.promise;
    },
    async prepareModel() {
      events.push("model-prepared");
    },
    onStatus(status) {
      statuses.push(status);
    },
  });

  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(statuses.includes(FRAGMENT_VIEWER_STATUS.ready), false);
  assert.equal(isXrayEnabled(statuses.at(-1)), false);
  assert.deepEqual(events, ["bytes-ready", "model-load-started"]);

  pendingModel.resolve({ modelId: "project-42" });
  await runtimePromise;

  assert.equal(statuses.at(-1), FRAGMENT_VIEWER_STATUS.ready);
  assert.equal(isXrayEnabled(statuses.at(-1)), true);
  assert.deepEqual(events, [
    "bytes-ready",
    "model-load-started",
    "model-prepared",
  ]);
});
