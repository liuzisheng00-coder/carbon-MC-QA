import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveFragmentRenderPolicy,
  originalGlobalIdForPick,
} from "./fragmentViewerState.mjs";

test("normal mode keeps non-selected fragments opaque and selectable", () => {
  const policy = deriveFragmentRenderPolicy({
    xray: false,
    selectedGlobalId: null,
  });

  assert.deepEqual(policy.background, {
    opacity: 1,
    transparent: false,
    selectable: true,
  });
  assert.equal(policy.selected, null);
});

test("X-Ray mode keeps non-selected fragments transparent and selectable", () => {
  const policy = deriveFragmentRenderPolicy({
    xray: true,
    selectedGlobalId: null,
  });

  assert.deepEqual(policy.background, {
    opacity: 0.16,
    transparent: true,
    selectable: true,
  });
  assert.equal(policy.selected, null);
});

test("a selected original IFC GlobalId gets an opaque high-contrast policy", () => {
  const globalId = "2O2Fr$t4X7Zf8NOew3FNr2";
  const policy = deriveFragmentRenderPolicy({
    xray: true,
    selectedGlobalId: globalId,
  });

  assert.deepEqual(policy.selected, {
    globalId,
    color: "#ffb000",
    opacity: 1,
    transparent: false,
    selectable: true,
  });
});

test("a fragment pick resolves through ThatOpen to the unchanged original IFC GlobalId", async () => {
  const originalGlobalId = "0BTBFw6f90Nfh9rP1dlXr$";
  let receivedModelIdMap;
  const fragments = {
    async modelIdMapToGuids(modelIdMap) {
      receivedModelIdMap = modelIdMap;
      return [originalGlobalId];
    },
  };

  const resolved = await originalGlobalIdForPick({
    fragments,
    pick: {
      localId: 731,
      fragments: { modelId: "project-42" },
    },
  });

  assert.equal(resolved, originalGlobalId);
  assert.deepEqual(Object.keys(receivedModelIdMap), ["project-42"]);
  assert.deepEqual([...receivedModelIdMap["project-42"]], [731]);
});

test("an empty-space pick has no IFC GlobalId", async () => {
  const resolved = await originalGlobalIdForPick({
    fragments: {
      async modelIdMapToGuids() {
        throw new Error("empty-space picks must not query GUIDs");
      },
    },
    pick: undefined,
  });

  assert.equal(resolved, null);
});
