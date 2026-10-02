from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def _round_vertices(values: Any, digits: int = 5) -> List[float]:
    return [round(float(value), digits) for value in values]


def _indices(values: Any) -> List[int]:
    return [int(value) for value in values]


def _component(value: Any) -> float:
    raw = value() if callable(value) else value
    return max(0.0, min(1.0, float(raw)))


def _hex_color(red: float, green: float, blue: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        int(round(_component(red) * 255)),
        int(round(_component(green) * 255)),
        int(round(_component(blue) * 255)),
    )


def _extract_materials(geometry: Any) -> List[Dict[str, Any]]:
    materials: List[Dict[str, Any]] = []
    for material in getattr(geometry, "materials", []) or []:
        diffuse = getattr(material, "diffuse", None)
        if diffuse is None:
            continue
        transparency = float(getattr(material, "transparency", 0.0) or 0.0)
        materials.append(
            {
                "name": str(getattr(material, "name", "") or ""),
                "color": _hex_color(diffuse.r, diffuse.g, diffuse.b),
                "opacity": round(max(0.0, min(1.0, 1.0 - transparency)), 4),
            }
        )
    return materials


def extract_ifc_geometry(
    ifc_path: str | Path,
    *,
    start_product: int = 0,
    max_products: int = 250,
    max_triangles: int = 180_000,
) -> Dict[str, Any]:
    """Extract one cursor-paged batch of real IFC mesh geometry for Three.js."""
    try:
        import ifcopenshell
        import ifcopenshell.geom
    except Exception as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError("ifcopenshell with geometry support is required for IFC mesh extraction") from exc

    path = Path(ifc_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"IFC file not found: {path}")

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)

    model = ifcopenshell.open(str(path))
    meshes: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    triangle_count = 0

    products = [
        product
        for product in [*model.by_type("IfcSite"), *model.by_type("IfcElement")]
        if not (
            product.is_a("IfcOpeningElement")
            or product.is_a("IfcVirtualElement")
            or product.is_a("IfcSpace")
        )
    ]
    total_eligible_products = len(products)
    cursor = min(max(0, int(start_product)), total_eligible_products)
    page_start_product = cursor
    page_product_limit = min(total_eligible_products, page_start_product + max_products)

    while cursor < page_product_limit:
        product = products[cursor]
        representation = getattr(product, "Representation", None)
        if representation is None:
            cursor += 1
            continue

        try:
            shape = ifcopenshell.geom.create_shape(settings, product)
        except Exception as exc:
            errors.append(
                {
                    "globalId": str(getattr(product, "GlobalId", "") or ""),
                    "ifcType": str(product.is_a()),
                    "message": str(exc)[:240],
                }
            )
            cursor += 1
            continue

        geometry = getattr(shape, "geometry", None)
        vertices = getattr(geometry, "verts", None)
        faces = getattr(geometry, "faces", None)
        if not vertices or not faces:
            cursor += 1
            continue
        materials = _extract_materials(geometry)

        product_triangles = len(faces) // 3
        if triangle_count + product_triangles > max_triangles and meshes:
            break

        meshes.append(
            {
                "globalId": str(getattr(product, "GlobalId", "") or f"ifc:{product.id()}"),
                "expressId": int(product.id()),
                "ifcType": str(product.is_a()),
                "name": str(getattr(product, "Name", "") or getattr(product, "ObjectType", "") or product.is_a()),
                "vertices": _round_vertices(vertices),
                "indices": _indices(faces),
                "color": materials[0]["color"] if materials else "",
                "materials": materials,
                "materialIds": _indices(getattr(geometry, "material_ids", []) or []),
            }
        )
        triangle_count += product_triangles
        cursor += 1

        # Including one oversize first mesh is intentional: otherwise a page
        # could never advance past a product whose mesh alone exceeds the cap.
        if triangle_count >= max_triangles:
            break

    complete = cursor >= total_eligible_products
    next_product = None if complete else cursor

    return {
        "sourceFile": str(path),
        "meshCount": len(meshes),
        "triangleCount": triangle_count,
        "totalEligibleProducts": total_eligible_products,
        "pageStartProduct": page_start_product,
        "examinedProducts": cursor - page_start_product,
        "nextProduct": next_product,
        "complete": complete,
        "truncated": not complete,
        "errors": errors[:25],
        "meshes": meshes,
    }
