import assert from "node:assert/strict";
import test from "node:test";

import { fragmentCacheKey } from "./fragmentCache.mjs";

test("identical IFC bytes and converter version produce the same cache key", async () => {
  const ifcBytes = new TextEncoder().encode("ISO-10303-21;fixture");

  const firstKey = await fragmentCacheKey(ifcBytes, "fragments-1");
  const secondKey = await fragmentCacheKey(ifcBytes, "fragments-1");

  assert.equal(firstKey, secondKey);
});

test("changing IFC bytes changes the cache key", async () => {
  const originalKey = await fragmentCacheKey(new TextEncoder().encode("fixture-a"), "fragments-1");
  const changedKey = await fragmentCacheKey(new TextEncoder().encode("fixture-b"), "fragments-1");

  assert.notEqual(originalKey, changedKey);
});

test("changing the converter version changes the cache key", async () => {
  const ifcBytes = new TextEncoder().encode("ISO-10303-21;fixture");

  const originalKey = await fragmentCacheKey(ifcBytes, "fragments-1");
  const changedKey = await fragmentCacheKey(ifcBytes, "fragments-2");

  assert.notEqual(originalKey, changedKey);
});
