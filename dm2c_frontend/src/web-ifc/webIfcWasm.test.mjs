import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

test("prepares the Web IFC WASM runtime asset", () => {
  assert.ok(existsSync(new URL("../../public/web-ifc/web-ifc.wasm", import.meta.url)));
});
