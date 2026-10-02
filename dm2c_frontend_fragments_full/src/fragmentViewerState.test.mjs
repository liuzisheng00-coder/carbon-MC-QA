import assert from "node:assert/strict";
import test from "node:test";

import { deriveFragmentRenderPolicy } from "./fragmentViewerState.mjs";

test("X-Ray keeps background fragments transparent and selectable", () => {
  const policy = deriveFragmentRenderPolicy({ xray: true, selectedGlobalId: null });

  assert.deepEqual(policy.background, {
    opacity: 0.16,
    transparent: true,
    selectable: true,
  });
  assert.equal(policy.selected, null);
});

test("X-Ray selected fragment is opaque, amber, and selectable", () => {
  const policy = deriveFragmentRenderPolicy({ xray: true, selectedGlobalId: "g-a" });

  assert.deepEqual(policy.selected, {
    globalId: "g-a",
    color: "#ffb000",
    opacity: 1,
    transparent: false,
    selectable: true,
  });
});

test("policy preserves all selected IDs, keeps the last primary, and retains unresolved candidates", () => {
  const policy = deriveFragmentRenderPolicy({
    xray: true,
    selectedGlobalIds: ["g-a", "g-b", "g-a"],
    candidateGlobalIds: ["g-b", "g-c", "g-c"],
  });

  assert.deepEqual(
    policy.selectedItems.map((item) => item.globalId),
    ["g-a", "g-b"],
  );
  assert.equal(policy.selected.globalId, "g-b");
  assert.deepEqual(policy.candidates, [{
    globalId: "g-c",
    color: "#ef9f27",
    opacity: 0.72,
    transparent: true,
    selectable: true,
  }]);
});
