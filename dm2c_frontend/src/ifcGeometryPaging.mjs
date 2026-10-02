export const IFC_PAGE_MAX_PRODUCTS = 40;
export const IFC_PAGE_MAX_TRIANGLES = 120000;

export function buildIfcGeometryPageUrl(
  geometryUrl,
  startProduct,
  {
    maxProducts = IFC_PAGE_MAX_PRODUCTS,
    maxTriangles = IFC_PAGE_MAX_TRIANGLES,
  } = {},
) {
  const isAbsolute = /^[a-z][a-z\d+.-]*:/i.test(geometryUrl);
  const url = new URL(geometryUrl, "http://localhost");
  url.searchParams.set("start_product", String(startProduct));
  url.searchParams.set("max_products", String(maxProducts));
  url.searchParams.set("max_triangles", String(maxTriangles));
  return isAbsolute ? url.toString() : `${url.pathname}${url.search}${url.hash}`;
}

export function appendIfcGeometryPage(previousGeometry, page) {
  const previous = previousGeometry || { meshes: [], errors: [] };
  const meshesByGlobalId = new Map();
  for (const mesh of previous.meshes || []) {
    if (mesh?.globalId) meshesByGlobalId.set(mesh.globalId, mesh);
  }
  for (const mesh of page?.meshes || []) {
    if (mesh?.globalId) meshesByGlobalId.set(mesh.globalId, mesh);
  }

  const orderedIds = [];
  for (const mesh of [...(previous.meshes || []), ...(page?.meshes || [])]) {
    if (mesh?.globalId && !orderedIds.includes(mesh.globalId)) orderedIds.push(mesh.globalId);
  }

  return {
    ...previous,
    ...page,
    meshes: orderedIds.map((globalId) => meshesByGlobalId.get(globalId)),
    errors: [...(previous.errors || []), ...(page?.errors || [])],
    loadedTriangleCount: (previous.loadedTriangleCount || 0) + (page?.triangleCount || 0),
  };
}
