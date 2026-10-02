import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildIfcGeometryScene } from "../interfaceModel.mjs";

const viewerSource = readFileSync(
  new URL("../ThreeModelViewer.jsx", import.meta.url),
  "utf8",
);
const interfaceSource = readFileSync(
  new URL("../DM2CVisualInterface.jsx", import.meta.url),
  "utf8",
);
const renderSource = readFileSync(
  new URL("./ifcRenderSource.mjs", import.meta.url),
  "utf8",
);

test("ThreeModelViewer starts Web IFC through the source controller before backend paging", () => {
  assert.match(viewerSource, /ifcFileUrl/);
  assert.match(
    viewerSource,
    /import\s*\{\s*createIfcRenderSourceController\s*\}\s*from\s*["']\.\/web-ifc\/ifcRenderSource\.mjs["']/,
  );
  assert.match(
    viewerSource,
    /import\s*\{\s*startWebIfcGeometry\s*\}\s*from\s*["']\.\/web-ifc\/webIfcGeometryClient\.mjs["']/,
  );
  assert.match(viewerSource, /createIfcRenderSourceController\s*\(/);
  assert.match(viewerSource, /startWebIfcGeometry\s*\(/);
  assert.match(viewerSource, /startBackend/);
  assert.match(viewerSource, /buildIfcGeometryPageUrl\s*\(/);
  assert.match(viewerSource, /appendIfcGeometryPage\s*\(/);
});

test("DM2CVisualInterface gives raw IFC bytes only to non-demo projects", () => {
  assert.match(
    interfaceSource,
    /const ifcFileUrl = projectId && projectId !== "demo-paper-figure"/,
  );
  assert.match(
    interfaceSource,
    /`\$\{apiBase\}\/api\/projects\/\$\{encodeURIComponent\(projectId\)\}\/ifc-file`/,
  );
  assert.match(interfaceSource, /<ThreeModelViewer[\s\S]*ifcFileUrl=\{ifcFileUrl\}/);
  assert.doesNotMatch(
    interfaceSource,
    /projectId === "demo-paper-figure"\s*\?\s*["'][^"']*ifc-file/,
  );
});

test("viewer status copy identifies the active or fallback geometry source truthfully", () => {
  assert.match(viewerSource, /formatIfcViewerStatus\s*\(/);
  for (const label of [
    "Web IFC",
    "backend IFC",
    "Web IFC partial → backend IFC",
    "account mesh",
  ]) {
    assert.match(`${viewerSource}\n${renderSource}`, new RegExp(label));
  }
});

test("transferred Web IFC typed arrays remain renderable and selectable by GlobalId", () => {
  const scene = buildIfcGeometryScene(
    {
      meshes: [{
        globalId: "typed-guid",
        vertices: new Float32Array([
          0, 0, 0,
          1, 0, 0,
          0, 1, 0,
        ]),
        indices: new Uint32Array([0, 1, 2]),
        materialIds: new Uint32Array([0]),
        materials: [{ color: "#FFFFFF", opacity: 1 }],
      }],
    },
    ["typed-guid"],
  );

  assert.equal(scene.meshes.length, 1);
  assert.equal(scene.meshes[0].id, "typed-guid");
  assert.equal(scene.meshes[0].selected, true);
  assert.deepEqual(scene.meshes[0].indices, [0, 1, 2]);
  assert.deepEqual(scene.meshes[0].materialIds, [0]);
});
