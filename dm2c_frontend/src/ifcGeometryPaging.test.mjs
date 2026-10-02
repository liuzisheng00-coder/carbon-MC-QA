import assert from "node:assert/strict";
import test from "node:test";

import {
  appendIfcGeometryPage,
  buildIfcGeometryPageUrl,
} from "./ifcGeometryPaging.mjs";

test("builds a cursor-paged IFC geometry URL", () => {
  const url = new URL(
    buildIfcGeometryPageUrl(
      "http://localhost:8000/api/projects/project%201/ifc-geometry",
      42,
      { maxProducts: 40, maxTriangles: 120000 },
    ),
  );

  assert.equal(url.pathname, "/api/projects/project%201/ifc-geometry");
  assert.equal(url.searchParams.get("start_product"), "42");
  assert.equal(url.searchParams.get("max_products"), "40");
  assert.equal(url.searchParams.get("max_triangles"), "120000");
});

test("merges IFC pages by GlobalId without losing accumulated geometry", () => {
  const first = appendIfcGeometryPage(null, {
    meshes: [{ globalId: "A", name: "A" }, { globalId: "B", name: "B" }],
    errors: [{ globalId: "missing-1" }],
    totalEligibleProducts: 4,
    nextProduct: 2,
    complete: false,
  });
  const merged = appendIfcGeometryPage(first, {
    meshes: [{ globalId: "B", name: "B later" }, { globalId: "C", name: "C" }],
    errors: [{ globalId: "missing-2" }],
    totalEligibleProducts: 4,
    nextProduct: null,
    complete: true,
  });

  assert.deepEqual(merged.meshes.map((mesh) => mesh.globalId), ["A", "B", "C"]);
  assert.equal(merged.meshes[1].name, "B later");
  assert.deepEqual(merged.errors, [{ globalId: "missing-1" }, { globalId: "missing-2" }]);
  assert.equal(merged.totalEligibleProducts, 4);
  assert.equal(merged.complete, true);
  assert.equal(merged.nextProduct, null);
});
