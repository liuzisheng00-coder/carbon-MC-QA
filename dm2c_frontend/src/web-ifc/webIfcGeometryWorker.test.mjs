import assert from "node:assert/strict";
import test from "node:test";

import {
  buildProductMesh,
  configureWebIfcApi,
} from "./webIfcGeometryCore.mjs";

function vector(values) {
  return {
    get(index) {
      return values[index];
    },
    size() {
      return values.length;
    },
  };
}

function identityWithTranslation(x = 0, y = 0, z = 0) {
  return [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    x, y, z, 1,
  ];
}

function interleaved(points) {
  return points.flatMap(([x, y, z]) => [x, y, z, 0, 0, 1]);
}

function createApi({
  product = { GlobalId: { value: "wrapped-guid" }, Name: { value: "Beam" } },
  geometries,
}) {
  const deleted = [];
  const records = new Map(
    geometries.map(({ id, vertices, indices }) => [
      id,
      { vertices, indices },
    ]),
  );

  return {
    deleted,
    GetGeometry(_modelId, geometryExpressID) {
      const record = records.get(geometryExpressID);
      return {
        GetVertexData: () => geometryExpressID,
        GetVertexDataSize: () => record.vertices.length,
        GetIndexData: () => geometryExpressID,
        GetIndexDataSize: () => record.indices.length,
        delete: () => deleted.push(geometryExpressID),
      };
    },
    GetIndexArray(pointer) {
      return records.get(pointer).indices;
    },
    GetLine() {
      return product;
    },
    GetVertexArray(pointer) {
      return records.get(pointer).vertices;
    },
  };
}

function flatMesh(placedGeometries) {
  return {
    expressID: 42,
    geometries: vector(placedGeometries),
  };
}

test("configures the absolute public WASM path and forces the single-thread runtime", async () => {
  const calls = [];
  const api = {
    SetWasmPath(...args) {
      calls.push(["SetWasmPath", ...args]);
    },
    async Init(...args) {
      calls.push(["Init", ...args]);
    },
  };

  await configureWebIfcApi(api);

  assert.deepEqual(calls, [
    ["SetWasmPath", "/web-ifc/", true],
    ["Init", undefined, true],
  ]);
});

test("merges placed geometries with transforms, rebased indices, and wrapped GlobalId", () => {
  const api = createApi({
    geometries: [
      {
        id: 10,
        vertices: interleaved([
          [0, 0, 0],
          [1, 0, 0],
          [0, 1, 0],
        ]),
        indices: [0, 1, 2],
      },
      {
        id: 20,
        vertices: interleaved([
          [0, 0, 0],
          [0, 0, 1],
          [1, 0, 0],
        ]),
        indices: [0, 1, 2],
      },
    ],
  });
  const mesh = buildProductMesh(
    api,
    0,
    flatMesh([
      {
        geometryExpressID: 10,
        flatTransformation: identityWithTranslation(10, 0, 0),
        color: { x: 1, y: 0, z: 0, w: 1 },
      },
      {
        geometryExpressID: 20,
        flatTransformation: identityWithTranslation(0, 5, 0),
        color: { x: 0, y: 1, z: 0, w: 0.5 },
      },
    ]),
  );

  assert.equal(mesh.globalId, "wrapped-guid");
  assert.equal(mesh.expressId, 42);
  assert.deepEqual([...mesh.vertices], [
    10, 0, 0,
    11, 0, 0,
    10, 1, 0,
    0, 5, 0,
    0, 5, 1,
    1, 5, 0,
  ]);
  assert.deepEqual([...mesh.indices], [0, 1, 2, 3, 4, 5]);
  assert.deepEqual([...mesh.materialIds], [0, 1]);
  assert.deepEqual(
    mesh.materials.map(({ color, opacity }) => ({ color, opacity })),
    [
      { color: "#FF0000", opacity: 1 },
      { color: "#00FF00", opacity: 0.5 },
    ],
  );
  assert.deepEqual(api.deleted, [10, 20]);
});

for (const [name, vertices, indices, transform, message] of [
  [
    "an incomplete interleaved vertex record",
    [0, 0, 0, 0, 0, 1, 1],
    [0, 0, 0],
    identityWithTranslation(),
    /vertex record/i,
  ],
  [
    "a non-finite source coordinate",
    interleaved([[Number.NaN, 0, 0]]),
    [0, 0, 0],
    identityWithTranslation(),
    /finite vertex/i,
  ],
  [
    "an incomplete triangle",
    interleaved([[0, 0, 0]]),
    [0, 0],
    identityWithTranslation(),
    /complete triangles/i,
  ],
  [
    "an out-of-range index",
    interleaved([[0, 0, 0]]),
    [0, 0, 1],
    identityWithTranslation(),
    /invalid index/i,
  ],
  [
    "a non-finite placement transform",
    interleaved([[0, 0, 0]]),
    [0, 0, 0],
    [
      1, 0, 0, 0,
      0, Number.NaN, 0, 0,
      0, 0, 1, 0,
      0, 0, 0, 1,
    ],
    /placement transform/i,
  ],
  [
    "a coordinate outside Float32 range",
    interleaved([[3.5e38, 0, 0]]),
    [0, 0, 0],
    identityWithTranslation(),
    /float32/i,
  ],
]) {
  test(`rejects ${name} and deletes the native geometry`, () => {
    const api = createApi({
      geometries: [{ id: 10, vertices, indices }],
    });

    assert.throws(
      () => buildProductMesh(
        api,
        0,
        flatMesh([{
          geometryExpressID: 10,
          flatTransformation: transform,
          color: { x: 1, y: 1, z: 1, w: 1 },
        }]),
      ),
      message,
    );
    assert.deepEqual(api.deleted, [10]);
  });
}
