import assert from "node:assert/strict";
import test from "node:test";

import {
  applyFragmentRenderPolicy,
  dispatchFragmentPick,
  FragmentRuntimeCancelledError,
  FRAGMENT_VIEWER_STATUS,
  isXrayEnabled,
  loadFragmentRuntime,
} from "./fragmentViewerRuntime.mjs";

function makeStyledModel() {
  const calls = [];
  return {
    calls,
    async resetColor(ids) {
      calls.push(["resetColor", ids]);
    },
    async resetOpacity(ids) {
      calls.push(["resetOpacity", ids]);
    },
    async setColor(ids, color) {
      calls.push(["setColor", [...ids], color]);
    },
    async setOpacity(ids, opacity) {
      calls.push([
        "setOpacity",
        ids === undefined ? undefined : [...ids],
        opacity,
      ]);
    },
  };
}

test("selection changes and clear reset prior explicit color and opacity before applying policy", async () => {
  const model = makeStyledModel();
  const localIdsByGuid = new Map([
    ["g-a", new Set([11])],
    ["g-b", new Set([22])],
  ]);
  const resolveLocalIds = async (guid) => localIdsByGuid.get(guid) ?? new Set();

  await applyFragmentRenderPolicy({
    model,
    xray: true,
    selectedGlobalId: "g-a",
    resolveLocalIds,
  });
  await applyFragmentRenderPolicy({
    model,
    xray: true,
    selectedGlobalId: "g-b",
    resolveLocalIds,
  });
  await applyFragmentRenderPolicy({
    model,
    xray: true,
    selectedGlobalId: null,
    resolveLocalIds,
  });

  assert.deepEqual(model.calls, [
    ["resetColor", undefined],
    ["resetOpacity", undefined],
    ["setOpacity", undefined, 0.16],
    ["setColor", [11], "#ffb000"],
    ["setOpacity", [11], 1],
    ["resetColor", undefined],
    ["resetOpacity", undefined],
    ["setOpacity", undefined, 0.16],
    ["setColor", [22], "#ffb000"],
    ["setOpacity", [22], 1],
    ["resetColor", undefined],
    ["resetOpacity", undefined],
    ["setOpacity", undefined, 0.16],
  ]);
});

test("pick maps model/local IDs to original IFC GlobalId and result metadata", async () => {
  const calls = [];
  const fragments = {
    async modelIdMapToGuids(modelIdMap) {
      assert.deepEqual(modelIdMap, { "model-a": new Set([22]) });
      return ["2ABC_original_ifc_guid"];
    },
  };
  const results = [
    { id: "2ABC_original_ifc_guid", label: "Steel beam A", type: "IfcBeam" },
  ];

  const selected = await dispatchFragmentPick({
    pick: { modelId: "model-a", localId: 22 },
    fragments,
    results,
    onSelectResult: (...args) => calls.push(args),
  });

  assert.equal(selected, "2ABC_original_ifc_guid");
  assert.deepEqual(calls, [[
    "2ABC_original_ifc_guid",
    { label: "Steel beam A", type: "IfcBeam" },
  ]]);
});

test("runtime styles every selected ID and unresolved candidate", async () => {
  const model = makeStyledModel();
  const localIdsByGuid = new Map([
    ["g-a", new Set([11])],
    ["g-b", new Set([22])],
    ["g-c", new Set([33])],
  ]);

  await applyFragmentRenderPolicy({
    model,
    xray: true,
    selectedGlobalIds: ["g-a", "g-b"],
    candidateGlobalIds: ["g-b", "g-c"],
    resolveLocalIds: async (guid) => localIdsByGuid.get(guid) ?? new Set(),
  });

  assert.deepEqual(model.calls, [
    ["resetColor", undefined],
    ["resetOpacity", undefined],
    ["setOpacity", undefined, 0.16],
    ["setColor", [33], "#ef9f27"],
    ["setOpacity", [33], 0.72],
    ["setColor", [11], "#ffb000"],
    ["setOpacity", [11], 1],
    ["setColor", [22], "#ffb000"],
    ["setOpacity", [22], 1],
  ]);
});

test("Fragment ready and X-Ray availability wait for model load and preparation", async () => {
  const statuses = [];
  let resolveModel;
  let resolvePreparation;
  const modelPending = new Promise((resolve) => {
    resolveModel = resolve;
  });
  const preparationPending = new Promise((resolve) => {
    resolvePreparation = resolve;
  });

  const runtimePending = loadFragmentRuntime({
    loadBytes: async () => ({
      source: "fragment-worker",
      fragmentBytes: new Uint8Array([1]),
    }),
    loadModel: () => modelPending,
    prepareModel: () => preparationPending,
    onStatus: (status) => statuses.push(status),
  });

  await Promise.resolve();
  assert.deepEqual(statuses, [FRAGMENT_VIEWER_STATUS.converting]);
  assert.equal(isXrayEnabled(statuses.at(-1)), false);

  resolveModel({ id: "model-a" });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(statuses.includes(FRAGMENT_VIEWER_STATUS.ready), false);
  assert.equal(isXrayEnabled(statuses.at(-1)), false);

  resolvePreparation();
  await runtimePending;
  assert.equal(statuses.at(-1), FRAGMENT_VIEWER_STATUS.ready);
  assert.equal(isXrayEnabled(statuses.at(-1)), true);
});

test("cancelled setup never loads a Fragment model after its bytes resolve", async () => {
  let resolveBytes;
  const bytesPending = new Promise((resolve) => {
    resolveBytes = resolve;
  });
  let probeActive = true;
  let probeModelLoads = 0;
  let liveModelLoads = 0;

  const probeSetup = loadFragmentRuntime({
    loadBytes: () => bytesPending,
    isActive: () => probeActive,
    loadModel: () => {
      probeModelLoads += 1;
      return { id: "stale-model" };
    },
    prepareModel: async () => {},
  });
  probeActive = false;

  const liveSetup = loadFragmentRuntime({
    loadBytes: () => bytesPending,
    isActive: () => true,
    loadModel: () => {
      liveModelLoads += 1;
      return { id: "live-model" };
    },
    prepareModel: async () => {},
  });

  resolveBytes({ source: "fragment-worker", fragmentBytes: new Uint8Array([1]) });
  await assert.rejects(probeSetup, FragmentRuntimeCancelledError);
  await liveSetup;
  assert.equal(probeModelLoads, 0);
  assert.equal(liveModelLoads, 1);
});
