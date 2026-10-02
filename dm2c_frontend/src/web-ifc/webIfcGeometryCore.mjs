export const BATCH_MAX_MESHES = 24;
export const BATCH_MAX_TRIANGLES = 80_000;

const DEFAULT_STYLE = Object.freeze({
  name: "IFC default",
  color: "#B7C3D0",
  opacity: 1,
});

function isNumericSequence(value, { integer = false } = {}) {
  if (
    !Array.isArray(value) &&
    !(ArrayBuffer.isView(value) && typeof value.length === "number")
  ) {
    return false;
  }
  for (const item of value) {
    if (
      typeof item !== "number" ||
      !Number.isFinite(item) ||
      (integer && (!Number.isInteger(item) || item < 0))
    ) {
      return false;
    }
  }
  return true;
}

export function isValidGeometryMesh(mesh) {
  if (
    mesh === null ||
    typeof mesh !== "object" ||
    typeof mesh.globalId !== "string" ||
    mesh.globalId.trim().length === 0 ||
    !isNumericSequence(mesh.vertices) ||
    mesh.vertices.length < 3 ||
    mesh.vertices.length % 3 !== 0 ||
    !isNumericSequence(mesh.indices, { integer: true }) ||
    mesh.indices.length < 3 ||
    mesh.indices.length % 3 !== 0
  ) {
    return false;
  }

  const vertexCount = mesh.vertices.length / 3;
  for (const index of mesh.indices) {
    if (index >= vertexCount) return false;
  }

  if (mesh.materialIds !== undefined) {
    if (
      !isNumericSequence(mesh.materialIds, { integer: true }) ||
      mesh.materialIds.length !== mesh.indices.length / 3 ||
      !Array.isArray(mesh.materials) ||
      mesh.materials.length === 0
    ) {
      return false;
    }
    for (const materialId of mesh.materialIds) {
      if (materialId >= mesh.materials.length) return false;
    }
  }
  return true;
}

export async function configureWebIfcApi(api) {
  api.SetWasmPath("/web-ifc/", true);
  await api.Init(undefined, true);
}

function wrappedString(value) {
  if (typeof value === "string") return value;
  return typeof value?.value === "string" ? value.value : "";
}

function clampUnit(value, fallback) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : fallback;
}

function colorChannelHex(value) {
  return Math.round(clampUnit(value, 0.72) * 255)
    .toString(16)
    .padStart(2, "0")
    .toUpperCase();
}

function styleFromColor(color) {
  if (
    !color ||
    ![color.x, color.y, color.z].every(
      (value) => typeof value === "number" && Number.isFinite(value),
    )
  ) {
    return { ...DEFAULT_STYLE };
  }

  return {
    name: "",
    color: `#${colorChannelHex(color.x)}${colorChannelHex(color.y)}${colorChannelHex(color.z)}`,
    opacity: clampUnit(color.w, 1),
  };
}

function requireTransform(value, geometryExpressID) {
  if (
    !Array.isArray(value) ||
    value.length !== 16 ||
    !value.every(
      (entry) => typeof entry === "number" && Number.isFinite(entry),
    )
  ) {
    throw new Error(
      `Geometry ${geometryExpressID} has an invalid placement transform`,
    );
  }
  return value;
}

function float32Coordinate(value, geometryExpressID) {
  const converted = Math.fround(value);
  if (!Number.isFinite(converted)) {
    throw new Error(
      `Geometry ${geometryExpressID} has a coordinate outside Float32 range`,
    );
  }
  return converted;
}

function appendPlacedGeometry(api, modelId, placedGeometry, aggregate) {
  const geometryExpressID = placedGeometry.geometryExpressID;
  const geometry = api.GetGeometry(modelId, geometryExpressID);

  try {
    const vertexData = api.GetVertexArray(
      geometry.GetVertexData(),
      geometry.GetVertexDataSize(),
    );
    const indexData = api.GetIndexArray(
      geometry.GetIndexData(),
      geometry.GetIndexDataSize(),
    );
    const hasVertexSequenceShape =
      Array.isArray(vertexData) ||
      (ArrayBuffer.isView(vertexData) && typeof vertexData.length === "number");
    if (
      !hasVertexSequenceShape ||
      vertexData.length === 0 ||
      vertexData.length % 6 !== 0
    ) {
      throw new Error(
        `Geometry ${geometryExpressID} has an incomplete vertex record`,
      );
    }
    if (
      !vertexData.every(
        (value) => typeof value === "number" && Number.isFinite(value),
      )
    ) {
      throw new Error(
        `Geometry ${geometryExpressID} requires finite vertex data`,
      );
    }
    if (
      !isNumericSequence(indexData, { integer: true }) ||
      indexData.length === 0 ||
      indexData.length % 3 !== 0
    ) {
      throw new Error(
        `Geometry ${geometryExpressID} must contain complete triangles`,
      );
    }

    const vertexCount = vertexData.length / 6;
    const matrix = requireTransform(
      placedGeometry.flatTransformation,
      geometryExpressID,
    );
    const indexOffset = aggregate.vertices.length / 3;
    for (let index = 0; index < vertexCount; index += 1) {
      const source = index * 6;
      const x = vertexData[source];
      const y = vertexData[source + 1];
      const z = vertexData[source + 2];
      aggregate.vertices.push(
        float32Coordinate(
          matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
          geometryExpressID,
        ),
        float32Coordinate(
          matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
          geometryExpressID,
        ),
        float32Coordinate(
          matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
          geometryExpressID,
        ),
      );
    }

    for (const index of indexData) {
      if (index >= vertexCount) {
        throw new Error(
          `Geometry ${geometryExpressID} has an invalid index`,
        );
      }
      aggregate.indices.push(index + indexOffset);
    }

    const style = styleFromColor(placedGeometry.color);
    const styleKey = `${style.color}:${style.opacity}`;
    let materialId = aggregate.materialByStyle.get(styleKey);
    if (materialId === undefined) {
      materialId = aggregate.materials.length;
      aggregate.materialByStyle.set(styleKey, materialId);
      aggregate.materials.push(style);
    }
    for (let index = 0; index < indexData.length / 3; index += 1) {
      aggregate.materialIds.push(materialId);
    }
  } finally {
    geometry.delete();
  }
}

export function buildProductMesh(api, modelId, flatMesh) {
  const product = api.GetLine(modelId, flatMesh.expressID);
  const globalId = wrappedString(product?.GlobalId).trim();
  if (!globalId) return null;

  const aggregate = {
    vertices: [],
    indices: [],
    materials: [],
    materialIds: [],
    materialByStyle: new Map(),
  };

  for (
    let geometryIndex = 0;
    geometryIndex < flatMesh.geometries.size();
    geometryIndex += 1
  ) {
    appendPlacedGeometry(
      api,
      modelId,
      flatMesh.geometries.get(geometryIndex),
      aggregate,
    );
  }
  if (aggregate.vertices.length === 0 || aggregate.indices.length === 0) {
    return null;
  }

  const materials = aggregate.materials.length
    ? aggregate.materials
    : [{ ...DEFAULT_STYLE }];
  const name = wrappedString(product?.Name).trim();
  const mesh = {
    globalId,
    expressId: flatMesh.expressID,
    ...(name ? { name } : {}),
    vertices: new Float32Array(aggregate.vertices),
    indices: new Uint32Array(aggregate.indices),
    color: materials[0].color,
    materials,
    materialIds: new Uint32Array(aggregate.materialIds),
  };
  if (!isValidGeometryMesh(mesh)) {
    throw new Error(`Product ${flatMesh.expressID} produced an invalid mesh`);
  }
  return mesh;
}

export function transferBuffers(meshes) {
  const buffers = [];
  for (const mesh of meshes) {
    buffers.push(mesh.vertices.buffer, mesh.indices.buffer);
    if (mesh.materialIds?.buffer) buffers.push(mesh.materialIds.buffer);
  }
  return buffers;
}
