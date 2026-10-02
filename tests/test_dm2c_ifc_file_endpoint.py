from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import dm2c_api_server as api


def test_ifc_file_endpoint_serves_the_project_ifc_with_private_caching(
    tmp_path: Path, monkeypatch
):
    """A raw IFC download must be scoped to the project's stored source file."""
    ifc_path = tmp_path / "model.ifc"
    ifc_path.write_bytes(b"ISO-10303-21;")

    monkeypatch.setattr(api, "get_project", lambda _project_id: SimpleNamespace(ifc_path=ifc_path))

    with TestClient(api.app) as client:
        response = client.get("/api/projects/p1/ifc-file")

    assert response.status_code == 200
    assert response.content == b"ISO-10303-21;"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"].startswith("private")
    assert 'filename="model.ifc"' in response.headers["content-disposition"]


def test_ifc_file_endpoint_returns_not_found_without_a_project_ifc(monkeypatch):
    monkeypatch.setattr(api, "get_project", lambda _project_id: SimpleNamespace(ifc_path=None))

    with TestClient(api.app) as client:
        response = client.get("/api/projects/no-ifc/ifc-file")

    assert response.status_code == 404
