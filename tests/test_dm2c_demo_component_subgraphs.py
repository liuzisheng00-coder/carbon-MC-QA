from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from types import ModuleType
from typing import Any, Iterator

import pytest

from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    FORBIDDEN_RUNTIME_LABELS,
    FORBIDDEN_RUNTIME_RELATIONS,
    PRINCIPAL_EDGE_TRIPLES,
    SCHEMA_VERSION,
    stable_edge_id,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_demo_component_subgraphs.py"
ARTIFACT_PATH = (
    REPO_ROOT / "dm2c_frontend" / "public" / "demo-component-subgraphs.json"
)
GEOMETRY_IDS = (
    "3czbugqbT86PTcmnme1im9",
    "3czbugqbT86PTcmnme1imB",
    "3czbugqbT86PTcmnme1im5",
    "3czbugqbT86PTcmnme1im7",
)
COMPONENT_BY_GEOMETRY = {
    GEOMETRY_IDS[0]: "component:c1",
    GEOMETRY_IDS[1]: "component:c2",
    GEOMETRY_IDS[2]: "component:no-facts",
    GEOMETRY_IDS[3]: "component:c4",
}
ALLOCATION_FIELDS = {
    "attributionMode",
    "allocationSetId",
    "allocationBasis",
    "rawWeight",
    "rawWeightUnit",
    "normalizedWeight",
    "evidenceRecordId",
}


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "task12_demo_component_subgraphs_generator", GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    return _load_generator()


@pytest.fixture(scope="module")
def generated_pair(generator: ModuleType) -> tuple[bytes, bytes]:
    return (
        generator.render_artifact(REPO_ROOT),
        generator.render_artifact(REPO_ROOT),
    )


@pytest.fixture(scope="module")
def payload(generated_pair: tuple[bytes, bytes]) -> dict[str, Any]:
    return json.loads(generated_pair[0])


def _node_by_id(subgraph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in subgraph["nodes"]}


def _walk_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def test_generator_entrypoint_exists() -> None:
    assert GENERATOR_PATH.is_file()


def test_two_runs_are_byte_identical_and_checked_in_artifact_is_fresh(
    generated_pair: tuple[bytes, bytes],
) -> None:
    first, second = generated_pair
    assert first == second
    assert ARTIFACT_PATH.read_bytes() == first
    assert first.endswith(b"\n")
    assert b"\r" not in first
    parsed = json.loads(first)
    canonical = (
        json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    assert first == canonical


def test_exact_artifact_contract_sources_and_coverage(payload: dict[str, Any]) -> None:
    assert set(payload) == {"schemaVersion", "metadata", "stats", "subgraphs"}
    assert payload["schemaVersion"] == SCHEMA_VERSION
    assert set(payload["subgraphs"]) == set(GEOMETRY_IDS)

    metadata = payload["metadata"]
    assert metadata["controlledFixtureProfile"] == (
        "task12-controlled-component-closure"
    )
    assert metadata["notForActualRelease"] is True
    assert metadata["generatorId"].startswith("dm2c-component-subgraph-v2:")
    assert set(metadata["sources"]) >= {
        "geometry",
        "typed_completed.ifc",
        "controlledFixture",
    }
    assert metadata["sources"]["typed_completed.ifc"]["path"] == (
        "typed_completed.ifc"
    )
    assert metadata["externalValidationCoverage"] == {
        "candidateCount": 9,
        "acceptedCount": 8,
        "rejectedCount": 1,
        "acceptedByKind": {"energy": 4, "material": 4},
        "rejectedByKind": {"energy": 1},
        "rejectedByReason": {"factor_not_found": 1},
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": pytest.approx(8 / 9),
    }

    stats = payload["stats"]
    assert stats["schemaVersion"] == SCHEMA_VERSION
    assert stats["nodeCount"] == 51
    assert stats["edgeCount"] == 68
    assert set(stats["applicationClassCounts"]) == set(APPLICATION_CLASSES)
    assert len(stats["applicationClassCounts"]) == 16
    assert stats["validation"] == metadata["externalValidationCoverage"]

    for descriptor in metadata["sources"].values():
        assert set(descriptor) == {"path", "sha256"}
        source_path = descriptor["path"]
        assert isinstance(source_path, str)
        assert source_path == PurePosixPath(source_path).as_posix()
        assert not PurePosixPath(source_path).is_absolute()
        assert ".." not in PurePosixPath(source_path).parts
        resolved = REPO_ROOT.joinpath(*PurePosixPath(source_path).parts)
        assert resolved.is_file()
        expected_hash = sha256(resolved.read_bytes()).hexdigest().upper()
        assert descriptor["sha256"] == expected_hash
        assert re.fullmatch(r"[0-9A-F]{64}", descriptor["sha256"])

    strings = tuple(_walk_strings(metadata))
    assert not any(re.match(r"^[A-Za-z]:[\\/]", value) for value in strings)
    assert not any("generatedAt" in value or "timestamp" in value.lower() for value in strings)


def test_roots_match_the_four_ifcbeam_geometry_components(
    payload: dict[str, Any],
) -> None:
    for geometry_id, component_id in COMPONENT_BY_GEOMETRY.items():
        subgraph = payload["subgraphs"][geometry_id]
        assert subgraph["schemaVersion"] == SCHEMA_VERSION
        assert subgraph["rootNodeId"] == component_id
        root = _node_by_id(subgraph)[component_id]
        assert root["props"]["globalId"] == geometry_id
        assert root["props"]["ifcClass"] == "IfcBeam"
        assert set(root["labels"]) == {"BuildingComponent", "IfcBeam"}


def test_each_subgraph_is_strict_canonical_v2_without_legacy_vocabulary(
    payload: dict[str, Any],
) -> None:
    allowed_triples = set(PRINCIPAL_EDGE_TRIPLES)
    application_classes = set(APPLICATION_CLASSES)
    for subgraph in payload["subgraphs"].values():
        assert set(subgraph) == {
            "schemaVersion",
            "rootNodeId",
            "nodes",
            "edges",
            "truncated",
            "boundary",
            "counts",
        }
        labels = {label for node in subgraph["nodes"] for label in node["labels"]}
        relations = {edge["type"] for edge in subgraph["edges"]}
        assert labels.isdisjoint(FORBIDDEN_RUNTIME_LABELS)
        assert relations.isdisjoint(FORBIDDEN_RUNTIME_RELATIONS)

        nodes = _node_by_id(subgraph)
        assert list(nodes) == sorted(nodes)
        for node in nodes.values():
            assert set(node) == {"id", "labels", "props"}
            app_labels = set(node["labels"]) & application_classes
            assert len(app_labels) == 1
            assert node["labels"] == sorted(set(node["labels"]))
        edge_ids: set[str] = set()
        occurrences: set[str] = set()
        for edge in subgraph["edges"]:
            assert set(edge) == {
                "id",
                "src",
                "type",
                "tgt",
                "occurrenceId",
                "props",
            }
            assert edge["src"] in nodes and edge["tgt"] in nodes
            source_class = next(
                iter(set(nodes[edge["src"]]["labels"]) & application_classes)
            )
            target_class = next(
                iter(set(nodes[edge["tgt"]]["labels"]) & application_classes)
            )
            assert (source_class, edge["type"], target_class) in allowed_triples
            assert edge["id"] == stable_edge_id(
                edge["type"], edge["src"], edge["tgt"], edge["occurrenceId"]
            )
            assert edge["id"] not in edge_ids
            assert edge["occurrenceId"] not in occurrences
            edge_ids.add(edge["id"])
            occurrences.add(edge["occurrenceId"])


@pytest.mark.parametrize(
    ("geometry_id", "own_component", "sibling_component", "weight", "evidence"),
    (
        (
            GEOMETRY_IDS[0],
            "component:c1",
            "component:c2",
            0.25,
            "allocation:evidence:c1",
        ),
        (
            GEOMETRY_IDS[1],
            "component:c2",
            "component:c1",
            0.75,
            "allocation:evidence:c2",
        ),
    ),
)
def test_allocated_closure_keeps_only_its_selected_product_edge(
    payload: dict[str, Any],
    geometry_id: str,
    own_component: str,
    sibling_component: str,
    weight: float,
    evidence: str,
) -> None:
    subgraph = payload["subgraphs"][geometry_id]
    selected = [
        edge
        for edge in subgraph["edges"]
        if edge["src"] == "consumption:allocated"
        and edge["type"] == "recordedForObject"
    ]
    assert len(selected) == 1
    assert selected[0]["tgt"] == own_component
    assert set(selected[0]["props"]) == ALLOCATION_FIELDS
    assert selected[0]["props"] == {
        "attributionMode": "allocated",
        "allocationSetId": "allocation:set:1",
        "allocationBasis": "mass",
        "rawWeight": weight,
        "rawWeightUnit": "kg",
        "normalizedWeight": weight,
        "evidenceRecordId": evidence,
    }
    assert sibling_component not in _node_by_id(subgraph)
    assert not any(
        edge["src"] == "consumption:allocated" and edge["tgt"] == sibling_component
        for edge in subgraph["edges"]
    )


def test_process_only_is_absent_valid_zero_is_retained_and_no_edges_dangle(
    payload: dict[str, Any],
) -> None:
    for subgraph in payload["subgraphs"].values():
        nodes = _node_by_id(subgraph)
        assert "consumption:process" not in nodes
        assert "emission:process" not in nodes
        assert all(
            edge["src"] in nodes and edge["tgt"] in nodes
            for edge in subgraph["edges"]
        )

    c4 = payload["subgraphs"][GEOMETRY_IDS[3]]
    c4_nodes = _node_by_id(c4)
    assert c4_nodes["quantity:zero"]["props"]["quantityValue"] == 0.0
    assert c4_nodes["emission:zero"]["props"]["emissionValue"] == 0.0
    assert c4_nodes["emission:zero"]["props"]["isValidZero"] is True
