from pathlib import Path

from dm2c_ifc_geometry import extract_ifc_geometry


def test_extract_ifc_geometry_returns_real_meshes():
    ifc_path = Path(__file__).resolve().parents[1] / "test001.ifc"

    geometry = extract_ifc_geometry(ifc_path, max_products=2)

    assert geometry["meshCount"] >= 1
    mesh = geometry["meshes"][0]
    assert mesh["globalId"]
    assert mesh["ifcType"].startswith("Ifc")
    assert len(mesh["vertices"]) >= 9
    assert len(mesh["indices"]) >= 3


def test_extract_ifc_geometry_preserves_complete_house_types_and_styles():
    ifc_path = Path(__file__).resolve().parents[1] / "test001.ifc"

    geometry = extract_ifc_geometry(ifc_path, max_products=250)
    types = {mesh["ifcType"] for mesh in geometry["meshes"]}

    assert {"IfcWallStandardCase", "IfcWindow", "IfcDoor"}.issubset(types)
    assert "IfcSite" in types
    assert "IfcSpace" not in types
    assert "IfcOpeningElement" not in types
    assert geometry["meshCount"] >= 80
    styled_meshes = [mesh for mesh in geometry["meshes"] if mesh.get("materials")]
    assert styled_meshes
    assert all("color" in mesh for mesh in styled_meshes[:5])
    assert any(len(mesh.get("materials", [])) > 1 and mesh.get("materialIds") for mesh in geometry["meshes"])


def test_extract_ifc_geometry_reports_triangle_limited_page_as_incomplete():
    ifc_path = Path(__file__).resolve().parents[1] / "test001.ifc"

    geometry = extract_ifc_geometry(ifc_path, max_products=250, max_triangles=10)

    assert geometry["meshCount"] >= 1
    assert geometry["complete"] is False
    assert geometry["truncated"] is True
    assert geometry["nextProduct"] is not None
    assert geometry["nextProduct"] > geometry["pageStartProduct"]
    assert geometry["totalEligibleProducts"] > geometry["nextProduct"]


def test_extract_ifc_geometry_pages_without_duplicate_global_ids():
    ifc_path = Path(__file__).resolve().parents[1] / "test001.ifc"

    first_page = extract_ifc_geometry(ifc_path, max_products=2, max_triangles=1_000_000)
    second_page = extract_ifc_geometry(
        ifc_path,
        start_product=first_page["nextProduct"],
        max_products=2,
        max_triangles=1_000_000,
    )

    first_ids = {mesh["globalId"] for mesh in first_page["meshes"]}
    second_ids = {mesh["globalId"] for mesh in second_page["meshes"]}
    assert first_page["pageStartProduct"] == 0
    assert first_page["nextProduct"] is not None
    assert second_page["pageStartProduct"] == first_page["nextProduct"]
    assert second_page["nextProduct"] is None or second_page["nextProduct"] > first_page["nextProduct"]
    assert first_ids.isdisjoint(second_ids)
