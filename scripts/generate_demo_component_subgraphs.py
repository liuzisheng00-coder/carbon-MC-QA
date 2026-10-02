"""Generate the controlled canonical-v2 component-subgraph demo artifact.

The browser demo deliberately uses a controlled fixture.  It is not evidence
from the actual case-study release; the four controlled component identities
are mapped to four real ``IfcBeam`` geometry identities solely so selection in
the viewer can exercise the canonical-v2 product-closure contract.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dm2c_canonical_v2_reader import (  # noqa: E402
    graph_document_from_context,
    load_canonical_v2_context,
)
from dm2c_component_subgraph import build_component_subgraph  # noqa: E402
from dm2c_m23_canonical import SCHEMA_VERSION  # noqa: E402
from dm2c_m3_context import load_m3_execution_context  # noqa: E402
from dm2c_m3_release import publish_derived_release  # noqa: E402
from tests.task10_v2_fixture import write_task10_release  # noqa: E402


GENERATOR_ID = "dm2c-component-subgraph-v2:task12-controlled-closure-v1"
CONTROLLED_FIXTURE_PROFILE = "task12-controlled-component-closure"
DEFAULT_OUTPUT = (
    REPO_ROOT / "dm2c_frontend" / "public" / "demo-component-subgraphs.json"
)
SOURCE_PATHS = {
    "geometry": "dm2c_frontend/public/demo-ifc-geometry.json",
    "typed_completed.ifc": "typed_completed.ifc",
    "controlledFixture": "tests/task10_v2_fixture.py",
}
COMPONENT_GEOMETRY_BINDINGS = (
    ("component:c1", "3czbugqbT86PTcmnme1im9"),
    ("component:c2", "3czbugqbT86PTcmnme1imB"),
    ("component:no-facts", "3czbugqbT86PTcmnme1im5"),
    ("component:c4", "3czbugqbT86PTcmnme1im7"),
)
_GEOMETRY_BY_COMPONENT = dict(COMPONENT_GEOMETRY_BINDINGS)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _source_descriptor(repo_root: Path, relative_path: str) -> dict[str, str]:
    path = repo_root.joinpath(*relative_path.split("/"))
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": relative_path,
        "sha256": sha256(path.read_bytes()).hexdigest().upper(),
    }


def _validate_geometry_bindings(repo_root: Path) -> None:
    geometry_path = repo_root.joinpath(*SOURCE_PATHS["geometry"].split("/"))
    payload = json.loads(geometry_path.read_bytes())
    meshes = {
        str(mesh.get("globalId")): mesh
        for mesh in payload.get("meshes", ())
        if isinstance(mesh, dict)
    }
    for _, geometry_id in COMPONENT_GEOMETRY_BINDINGS:
        mesh = meshes.get(geometry_id)
        if mesh is None:
            raise ValueError(f"controlled geometry identity is absent: {geometry_id}")
        if mesh.get("ifcType") != "IfcBeam":
            raise ValueError(f"controlled geometry is not IfcBeam: {geometry_id}")


def _synchronise_component(node: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "id": str(node["id"]),
        "labels": list(node["labels"]),
        "props": dict(node["props"]),
    }
    geometry_id = _GEOMETRY_BY_COMPONENT.get(result["id"])
    if geometry_id is None:
        return result
    result["labels"] = ["BuildingComponent", "IfcBeam"]
    result["props"]["globalId"] = geometry_id
    result["props"]["ifcClass"] = "IfcBeam"
    return result


def build_artifact(repo_root: Path | str = REPO_ROOT) -> dict[str, Any]:
    """Build the deterministic, explicitly non-actual Task 12 demo payload."""

    root = Path(repo_root).resolve()
    _validate_geometry_bindings(root)
    sources = {
        name: _source_descriptor(root, relative_path)
        for name, relative_path in SOURCE_PATHS.items()
    }

    with TemporaryDirectory(prefix="dm2c-task12-") as temporary_name:
        temporary_root = Path(temporary_name)
        fixture_release = write_task10_release(temporary_root / "task10-source")
        execution_context = load_m3_execution_context(fixture_release)
        derived_release = publish_derived_release(
            execution_context,
            output_root=temporary_root / "derived",
            release_id="task12-controlled-component-closure-v2",
            node_transform=_synchronise_component,
        )
        context = load_canonical_v2_context(derived_release)
        document = graph_document_from_context(context)
        subgraphs = {
            geometry_id: build_component_subgraph(document, geometry_id)
            for _, geometry_id in COMPONENT_GEOMETRY_BINDINGS
        }

        stats = json.loads(
            (derived_release / "multigranular_carbon_kg_stats.json").read_bytes()
        )
        manifest_coverage = _plain(context.manifest["coverage"])
        stats_coverage = stats["validation"]
        if manifest_coverage != stats_coverage:
            raise RuntimeError("derived manifest and stats coverage disagree")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "metadata": {
            "controlledFixtureProfile": CONTROLLED_FIXTURE_PROFILE,
            "notForActualRelease": True,
            "generatorId": GENERATOR_ID,
            "sources": sources,
            "externalValidationCoverage": stats_coverage,
        },
        "stats": stats,
        "subgraphs": subgraphs,
    }


def render_artifact(repo_root: Path | str = REPO_ROOT) -> bytes:
    """Return canonical UTF-8 JSON bytes with one LF terminator."""

    payload = build_artifact(repo_root)
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def write_artifact(
    output_path: Path | str = DEFAULT_OUTPUT,
    *,
    repo_root: Path | str = REPO_ROOT,
) -> Path:
    """Write a freshly validated deterministic artifact and return its path."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(render_artifact(repo_root))
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = write_artifact(args.output, repo_root=args.repo_root)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
