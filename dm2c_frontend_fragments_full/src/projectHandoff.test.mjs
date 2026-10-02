import assert from "node:assert/strict";
import test from "node:test";
import { getExistingProjectId } from "./projectHandoff.mjs";

test("accepts an existing API project through the exact projectId query parameter", () => {
  assert.equal(getExistingProjectId("?projectId=project-42"), "project-42");
  assert.equal(getExistingProjectId("?projectId=%206ac3d39ead76%20"), "6ac3d39ead76");
  assert.equal(getExistingProjectId("?project=project-42"), "");
  assert.equal(getExistingProjectId("?projectId="), "");
});
