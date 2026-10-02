from __future__ import annotations

from collections import Counter
import asyncio
from io import BytesIO
import json
from pathlib import Path
import time

from fastapi import HTTPException, UploadFile
import pytest

import dm2c_component_subgraph as component_subgraph
from dm2c_canonical_v2_reader import (
    CanonicalSchemaError,
    iter_graph_edges,
    load_canonical_v2_context,
    load_canonical_v2_graph_document,
)
from dm2c_carbonql_service import CarbonQLService
from dm2c_m23_canonical import SCHEMA_VERSION, stable_edge_id, stable_id
from tests.test_dm2c_canonical_v2_reader import (
    CARRIER_ELECTRICITY,
    COMPONENT_1,
    COMPONENT_2,
    MATERIAL,
    MODULE,
    OUTPUTS,
    PROCESS_ACTIVITY,
    PROCESS_STAGE,
    RESOURCE,
    _rewrite_graph,
    _write_json,
    write_release,
)


FACTOR_MATERIAL = stable_id("EmissionFactor", "factor-source:material")
FACTOR_ELECTRICITY = stable_id("EmissionFactor", "factor-source:electricity")
ALLOCATION_OCCURRENCE_C1 = (
    "occ:allocated:recordedForObject:component:c1:allocation:evidence:c1"
)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize(
    "unsafe_name",
    (
        "../escape.txt",
        r"..\escape.txt",
        "/absolute.txt",
        r"C:\absolute.txt",
        ".",
        "..",
        "trailing.",
        "CONIN$",
        "CONOUT$.txt",
        "CLOCK$.json",
        "COM¹.csv",
        "COM².csv",
        "COM³.csv",
        "LPT¹.txt",
        "LPT².txt",
        "LPT³.txt",
        "CON .txt",
        "NUL .json",
    ),
)
def test_uploaded_filenames_cannot_escape_the_project_category(
    tmp_path: Path, unsafe_name: str
) -> None:
    import dm2c_api_server as api

    project = api.Project(
        project_id="upload-path-containment",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    upload = UploadFile(filename=unsafe_name, file=BytesIO(b"unsafe"))

    with pytest.raises(HTTPException) as exc_info:
        api.save_uploaded_files(project, [upload], [], [])

    assert exc_info.value.status_code == 400
    assert not project.upload_dir.exists()
    assert list(tmp_path.rglob("escape.txt")) == []


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_duplicate_uploaded_filenames_are_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    import dm2c_api_server as api

    project = api.Project(
        project_id="upload-duplicate",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    uploads = [
        UploadFile(filename="typed_completed.ifc", file=BytesIO(b"first")),
        UploadFile(filename="TYPED_COMPLETED.IFC", file=BytesIO(b"second")),
    ]

    with pytest.raises(HTTPException) as exc_info:
        api.save_uploaded_files(project, uploads, [], [])

    assert exc_info.value.status_code == 400
    assert not project.upload_dir.exists()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_safe_uploaded_filename_is_saved_as_one_direct_child(tmp_path: Path) -> None:
    import dm2c_api_server as api

    project = api.Project(
        project_id="upload-safe",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    upload = UploadFile(filename="typed_completed.ifc", file=BytesIO(b"ISO-10303-21;"))

    manifest = api.save_uploaded_files(project, [upload], [], [])

    saved = Path(manifest["design"][0])
    assert saved == project.upload_dir / "design" / "typed_completed.ifc"
    assert saved.read_bytes() == b"ISO-10303-21;"
    assert saved.parent.resolve() == (project.upload_dir / "design").resolve()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize(
    "design_names,factor_names,manufacturing_names,detail_match",
    (
        (("first.ifc", "second.ifc"), (), (), "IFC"),
        (("first.json", "second.json"), (), (), "legacy"),
        (("typed_completed.ifc", "backbone.json"), (), (), "backbone"),
        (("backbone.json",), ("first.xlsx", "second.xlsx"), (), "factor"),
        (("backbone.json",), (), ("first.jsonl", "second.jsonl"), "process"),
        (("backbone.json",), (), ("first.csv", "second.csv"), "profile"),
    ),
)
def test_singleton_upload_roles_reject_ambiguous_cardinality_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    design_names: tuple[str, ...],
    factor_names: tuple[str, ...],
    manufacturing_names: tuple[str, ...],
    detail_match: str,
) -> None:
    import dm2c_api_server as api

    def make_files(category: str, names: tuple[str, ...]) -> list[str]:
        paths = []
        for name in names:
            path = tmp_path / category / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() == ".json":
                path.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
            else:
                path.write_bytes(b"fixture")
            paths.append(str(path))
        return paths

    project = api.Project(
        project_id=f"ambiguous-{detail_match}",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        file_manifest={
            "design": make_files("design", design_names),
            "carbon_factors": make_files("factors", factor_names),
            "manufacturing": make_files("manufacturing", manufacturing_names),
        },
    )
    monkeypatch.setattr(
        api,
        "convert_ifc_to_backbone",
        lambda *_args, **_kwargs: pytest.fail("role ambiguity must fail before IFC conversion"),
    )

    with pytest.raises(HTTPException) as exc_info:
        api.resolve_roles(project)

    assert exc_info.value.status_code == 400
    assert detail_match.casefold() in str(exc_info.value.detail).casefold()
    assert project.ifc_path is None
    assert project.graph_path is None
    assert project.factor_path is None
    assert project.process_docs_path is None
    assert project.profile_path is None


def _add_edge(
    payload: dict[str, object],
    src: str,
    relation: str,
    tgt: str,
    occurrence_id: str,
    props: dict[str, object] | None = None,
) -> None:
    payload["edges"].append(
        {
            "id": stable_edge_id(relation, src, tgt, occurrence_id),
            "src": src,
            "type": relation,
            "tgt": tgt,
            "occurrenceId": occurrence_id,
            "props": props or {},
        }
    )


def write_component_release(root: Path, *, reverse_graph_order: bool = False) -> Path:
    release = write_release(root, reverse_graph_order=reverse_graph_order)

    def mutate(payload: dict[str, object]) -> None:
        component_2 = next(
            node for node in payload["nodes"] if node["id"] == COMPONENT_2
        )
        component_2["props"]["ifcGlobalId"] = "IFC-C2"
        _add_edge(
            payload,
            COMPONENT_1,
            "manufacturedBy",
            PROCESS_ACTIVITY,
            "occ:parallel:manufactured-by:c1",
            {"sequence": 2},
        )
        _add_edge(
            payload,
            COMPONENT_2,
            "hasMaterial",
            MATERIAL,
            "occ:c2:shared-material",
            {"sourceRecordId": "source:shared-material"},
        )
        process_context = next(
            edge
            for edge in payload["edges"]
            if edge["src"] == "consumption:process"
            and edge["type"] == "associatedWithProcess"
        )
        process_context["tgt"] = PROCESS_ACTIVITY
        process_context["id"] = stable_edge_id(
            process_context["type"],
            process_context["src"],
            process_context["tgt"],
            process_context["occurrenceId"],
        )

    _rewrite_graph(release, mutate, reconcile_counts=True)
    # The component fixture remains a fully validated release, not an ad-hoc
    # graph accepted only by the subgraph code.
    load_canonical_v2_context(release)
    return release


@pytest.fixture()
def component_release(tmp_path: Path) -> Path:
    return write_component_release(tmp_path / "release")


@pytest.fixture()
def document(component_release: Path):
    return load_canonical_v2_graph_document(
        component_release / OUTPUTS["graphJson"]
    )


def _ids(result: dict[str, object]) -> set[str]:
    return {node["id"] for node in result["nodes"]}


def test_exact_phased_component_closure_uses_only_root_directed_facts(document) -> None:
    result = component_subgraph.build_component_subgraph(document, "C1")

    assert result["schemaVersion"] == SCHEMA_VERSION
    assert result["rootNodeId"] == COMPONENT_1
    assert result["boundary"] == "canonical_component_product_closure"
    assert result["truncated"] is False
    assert _ids(result) == {
        MODULE,
        COMPONENT_1,
        "type:structural",
        MATERIAL,
        "dq:material",
        PROCESS_ACTIVITY,
        "consumption:material",
        "quantity:material",
        FACTOR_MATERIAL,
        "emission:material",
        "consumption:direct",
        "quantity:direct",
        FACTOR_ELECTRICITY,
        CARRIER_ELECTRICITY,
        "emission:direct",
        "consumption:allocated",
        "quantity:allocated",
        "emission:allocated",
    }
    assert Counter(edge["type"] for edge in result["edges"]) == Counter(
        {
            "containsComponent": 1,
            "recordedForObject": 3,
            "hasComponentType": 1,
            "hasMaterial": 1,
            "hasDesignQuantity": 1,
            "manufacturedBy": 2,
            "hasQuantity": 3,
            "hasFactor": 3,
            "ofMaterial": 1,
            "ofCarrier": 2,
            "hasCarbonDriver": 3,
            "derivedFrom": 1,
        }
    )
    assert result["counts"]["nodes"] == len(result["nodes"])
    assert result["counts"]["edges"] == len(result["edges"])
    assert all(
        edge["src"] in _ids(result) and edge["tgt"] in _ids(result)
        for edge in result["edges"]
    )


def test_fully_validated_release_context_is_an_equivalent_constructor(
    document, component_release: Path
) -> None:
    context = load_canonical_v2_context(component_release)
    expected = component_subgraph.build_component_subgraph(document, "C1")

    assert component_subgraph.build_component_subgraph(context, "C1") == expected
    assert component_subgraph.component_subgraph_from_context(
        context, "C1"
    ) == expected


def test_allocated_edge_and_parallel_occurrences_are_preserved(document) -> None:
    result = component_subgraph.build_component_subgraph(document, COMPONENT_1)
    selected = [
        edge
        for edge in result["edges"]
        if edge["src"] == "consumption:allocated"
        and edge["type"] == "recordedForObject"
    ]
    original = next(
        edge
        for edge in iter_graph_edges(document)
        if edge["occurrenceId"] == ALLOCATION_OCCURRENCE_C1
    )

    assert len(selected) == 1
    assert selected[0] == original
    assert selected[0]["tgt"] == COMPONENT_1
    assert set(selected[0]["props"]) == {
        "attributionMode",
        "allocationSetId",
        "allocationBasis",
        "rawWeight",
        "rawWeightUnit",
        "normalizedWeight",
        "evidenceRecordId",
    }
    assert selected[0]["props"] == {
        "attributionMode": "allocated",
        "allocationSetId": "allocation:set:1",
        "allocationBasis": "mass",
        "rawWeight": 0.25,
        "rawWeightUnit": "kg",
        "normalizedWeight": 0.25,
        "evidenceRecordId": "allocation:evidence:c1",
    }
    assert COMPONENT_2 not in _ids(result)
    assert not any(
        edge["src"] == "consumption:allocated" and edge["tgt"] == COMPONENT_2
        for edge in result["edges"]
    )
    manufactured = [
        edge
        for edge in result["edges"]
        if edge["src"] == COMPONENT_1 and edge["type"] == "manufacturedBy"
    ]
    assert len(manufactured) == 2
    assert len({edge["occurrenceId"] for edge in manufactured}) == 2
    assert any(edge["props"] == {"sequence": 2} for edge in manufactured)
    direct_original = next(
        edge
        for edge in iter_graph_edges(document)
        if edge["src"] == "consumption:direct"
        and edge["type"] == "recordedForObject"
    )
    direct_selected = next(
        edge
        for edge in result["edges"]
        if edge["occurrenceId"] == direct_original["occurrenceId"]
    )
    assert direct_selected == direct_original
    assert direct_selected["props"] == {
        "sourceRecordId": "source:direct",
        "evidenceSourceId": "evidence:direct",
    }


def test_terminal_leaves_and_shared_context_do_not_expand(document) -> None:
    result = component_subgraph.build_component_subgraph(document, "C1")
    ids = _ids(result)

    assert "consumption:process" not in ids
    assert "quantity:process" not in ids
    assert "emission:process" not in ids
    assert PROCESS_STAGE not in ids
    assert RESOURCE not in ids
    assert COMPONENT_2 not in ids
    assert not any(
        edge["type"] in {
            "hasProcessTemplate",
            "hasStage",
            "hasActivity",
            "usesResource",
            "associatedWithProcess",
            "recordedForResource",
        }
        for edge in result["edges"]
    )
    assert component_subgraph.build_component_subgraph(
        document, "C1", expand_shared=True
    ) == result


def test_declared_process_only_fact_with_a_product_edge_is_rejected_before_closure(
    component_release: Path, tmp_path: Path
) -> None:
    payload = json.loads(
        (component_release / OUTPUTS["graphJson"]).read_text(encoding="utf-8")
    )
    _add_edge(
        payload,
        "consumption:process",
        "recordedForObject",
        COMPONENT_1,
        "occ:malformed:process-only-product-edge",
        {
            "sourceRecordId": "source:process",
            "evidenceSourceId": "evidence:process",
        },
    )
    graph_path = tmp_path / "structural-process-only-product-edge.json"
    _write_json(graph_path, payload)
    with pytest.raises(CanonicalSchemaError):
        load_canonical_v2_graph_document(graph_path)


def test_selected_valid_zero_fact_is_not_dropped(document) -> None:
    result = component_subgraph.build_component_subgraph(document, "IFC-C2")
    by_id = {node["id"]: node for node in result["nodes"]}

    assert by_id["quantity:zero"]["props"]["quantityValue"] == 0.0
    assert by_id["emission:zero"]["props"]["emissionValue"] == 0.0
    assert by_id["emission:zero"]["props"]["isValidZero"] is True
    assert "consumption:process" not in by_id


def test_component_identity_is_exact_case_sensitive_and_typed(
    document, component_release: Path, tmp_path: Path
) -> None:
    assert component_subgraph.build_component_subgraph(document, COMPONENT_1)[
        "rootNodeId"
    ] == COMPONENT_1
    assert component_subgraph.build_component_subgraph(document, "C1")[
        "rootNodeId"
    ] == COMPONENT_1
    assert component_subgraph.build_component_subgraph(document, "IFC-C2")[
        "rootNodeId"
    ] == COMPONENT_2

    with pytest.raises(component_subgraph.ComponentNodeNotFound):
        component_subgraph.build_component_subgraph(document, "c1")
    with pytest.raises(component_subgraph.ComponentSelectorInvalid):
        component_subgraph.build_component_subgraph(document, "")
    with pytest.raises(component_subgraph.ComponentSelectorInvalid):
        component_subgraph.build_component_subgraph(document, MODULE)

    payload = json.loads(
        (component_release / OUTPUTS["graphJson"]).read_text(encoding="utf-8")
    )
    component_2 = next(
        node for node in payload["nodes"] if node["id"] == COMPONENT_2
    )
    component_2["props"]["globalId"] = "C1"
    ambiguous_path = tmp_path / "ambiguous-canonical-v2.json"
    _write_json(ambiguous_path, payload)
    ambiguous = load_canonical_v2_graph_document(ambiguous_path)
    with pytest.raises(component_subgraph.ComponentSelectorAmbiguous):
        component_subgraph.build_component_subgraph(ambiguous, "C1")
    # Exact component id has precedence over duplicate property values.
    assert component_subgraph.build_component_subgraph(
        ambiguous, COMPONENT_1
    )["rootNodeId"] == COMPONENT_1


def test_shuffled_input_is_deterministic_and_truncation_has_no_dangling_edges(
    tmp_path: Path,
) -> None:
    ordered_release = write_component_release(tmp_path / "ordered")
    shuffled_release = write_component_release(
        tmp_path / "shuffled", reverse_graph_order=True
    )
    ordered = load_canonical_v2_graph_document(
        ordered_release / OUTPUTS["graphJson"]
    )
    shuffled = load_canonical_v2_graph_document(
        shuffled_release / OUTPUTS["graphJson"]
    )

    assert component_subgraph.build_component_subgraph(
        ordered, "C1"
    ) == component_subgraph.build_component_subgraph(shuffled, "C1")

    bounded = component_subgraph.build_component_subgraph(
        ordered, "C1", max_nodes=7
    )
    assert bounded == component_subgraph.build_component_subgraph(
        ordered, "C1", max_nodes=7
    )
    assert bounded["truncated"] is True
    assert len(bounded["nodes"]) == 7
    bounded_ids = _ids(bounded)
    assert COMPONENT_1 in bounded_ids
    assert all(
        edge["src"] in bounded_ids and edge["tgt"] in bounded_ids
        for edge in bounded["edges"]
    )
    with pytest.raises(ValueError):
        component_subgraph.build_component_subgraph(ordered, "C1", max_nodes=0)


def test_file_constructor_accepts_only_strict_canonical_v2_documents(
    component_release: Path, tmp_path: Path
) -> None:
    graph_path = component_release / OUTPUTS["graphJson"]
    result = component_subgraph.component_subgraph_from_graph_path(
        graph_path, "C1"
    )
    assert result["schemaVersion"] == SCHEMA_VERSION

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": COMPONENT_1,
                        "labels": ["BuildingComponent"],
                        "props": {"globalId": "C1"},
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CanonicalSchemaError):
        component_subgraph.component_subgraph_from_graph_path(legacy_path, "C1")

    invalid_accounting = json.loads(graph_path.read_text(encoding="utf-8"))
    next(
        edge
        for edge in invalid_accounting["edges"]
        if edge["src"] == "consumption:allocated"
        and edge["type"] == "recordedForObject"
        and edge["tgt"] == COMPONENT_1
    )["props"]["normalizedWeight"] = 9.0
    invalid_accounting_path = tmp_path / "invalid-accounting-v2.json"
    _write_json(invalid_accounting_path, invalid_accounting)
    with pytest.raises(CanonicalSchemaError):
        component_subgraph.component_subgraph_from_graph_path(
            invalid_accounting_path, "C1"
        )


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_api_returns_v2_subgraph_and_maps_invalid_inputs_to_controlled_4xx(
    component_release: Path, tmp_path: Path
) -> None:
    from dm2c_api_server import Project, _projects, get_project_component_subgraph

    graph_path = component_release / OUTPUTS["graphJson"]
    project = Project(
        project_id="canonical-subgraph-test",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        graph_path=graph_path,
        canonical_graph_path=graph_path,
    )
    _projects[project.project_id] = project
    try:
        success = asyncio.run(
            get_project_component_subgraph(
                project.project_id, "C1", max_nodes=80, expand_shared=False
            )
        )
        assert success["status"] == "success"
        assert success["subgraph"]["schemaVersion"] == SCHEMA_VERSION

        invalid_accounting = json.loads(graph_path.read_text(encoding="utf-8"))
        next(
            node
            for node in invalid_accounting["nodes"]
            if node["id"] == "emission:direct"
        )["props"]["emissionValue"] = 999.0
        invalid_accounting_path = tmp_path / "invalid-accounting-api.json"
        _write_json(invalid_accounting_path, invalid_accounting)
        project.canonical_graph_path = invalid_accounting_path
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                get_project_component_subgraph(
                    project.project_id, "C1", max_nodes=80, expand_shared=False
                )
            )
        assert exc_info.value.status_code == 422
        project.canonical_graph_path = graph_path

        cases = (
            ("missing", 404),
            (MODULE, 400),
            ("", 400),
        )
        for selector, expected_status in cases:
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    get_project_component_subgraph(
                        project.project_id,
                        selector,
                        max_nodes=80,
                        expand_shared=False,
                    )
                )
            assert exc_info.value.status_code == expected_status

        ambiguous_payload = json.loads(graph_path.read_text(encoding="utf-8"))
        next(
            node
            for node in ambiguous_payload["nodes"]
            if node["id"] == COMPONENT_2
        )["props"]["globalId"] = "C1"
        ambiguous_path = tmp_path / "ambiguous-api.json"
        _write_json(ambiguous_path, ambiguous_payload)
        project.canonical_graph_path = ambiguous_path
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                get_project_component_subgraph(
                    project.project_id, "C1", max_nodes=80, expand_shared=False
                )
            )
        assert exc_info.value.status_code == 409

        legacy_path = tmp_path / "legacy-api.json"
        legacy_path.write_text(
            json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
        )
        project.canonical_graph_path = legacy_path
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                get_project_component_subgraph(
                    project.project_id, "C1", max_nodes=80, expand_shared=False
                )
            )
        assert exc_info.value.status_code == 422
    finally:
        _projects.pop(project.project_id, None)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_api_keeps_ifc_backbone_separate_from_validated_canonical_graph(
    component_release: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dm2c_api_server as api

    ifc_path = tmp_path / "typed_completed.ifc"
    ifc_path.write_text("ISO-10303-21;", encoding="utf-8")
    legacy_path = tmp_path / "legacy-backbone.json"
    legacy_path.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
    canonical_path = component_release / OUTPUTS["graphJson"]

    converter_calls: list[Path] = []

    def fake_converter(project, path: Path) -> Path:
        converter_calls.append(path)
        project.ifc_path = path
        project.backbone_build = {"stats": {"nodes": 7, "edges": 6}}
        return legacy_path

    monkeypatch.setattr(api, "convert_ifc_to_backbone", fake_converter)

    backbone_only = api.Project(
        project_id="backbone-only",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-backbone",
        output_dir=tmp_path / "outputs-backbone",
        file_manifest={"design": [str(ifc_path)], "manufacturing": [], "carbon_factors": []},
    )
    api.resolve_roles(backbone_only)
    assert backbone_only.ifc_path == ifc_path
    assert backbone_only.graph_path == legacy_path
    assert backbone_only.canonical_graph_path is None
    assert converter_calls == [ifc_path]

    api._projects[backbone_only.project_id] = backbone_only
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api.get_project_component_subgraph(
                    backbone_only.project_id, "C1", max_nodes=80, expand_shared=False
                )
            )
        assert exc_info.value.status_code == 409
        assert "IFC" in str(exc_info.value.detail)
        assert "design backbone" in str(exc_info.value.detail)
    finally:
        api._projects.pop(backbone_only.project_id, None)

    converter_calls.clear()
    canonical_and_ifc = api.Project(
        project_id="canonical-and-ifc",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-canonical",
        output_dir=tmp_path / "outputs-canonical",
        file_manifest={
            "design": [str(ifc_path), str(canonical_path)],
            "manufacturing": [],
            "carbon_factors": [],
        },
    )
    api.resolve_roles(canonical_and_ifc)
    assert canonical_and_ifc.ifc_path == ifc_path
    assert canonical_and_ifc.graph_path == legacy_path
    assert canonical_and_ifc.canonical_graph_path == canonical_path
    assert canonical_and_ifc.canonical_graph_info["canonicalGraphAvailable"] is True
    assert canonical_and_ifc.canonical_graph_info["canonicalValidationLevel"] == "graph_document"
    assert converter_calls == [ifc_path]
    graph_info = api._project_graph_info(canonical_and_ifc)
    assert graph_info["ifcRole"] == "design_backbone_geometry"
    assert graph_info["backboneGraph"] == str(legacy_path)
    assert graph_info["backboneStats"] == {"nodes": 7, "edges": 6}
    assert graph_info["canonicalGraphAvailable"] is True
    assert graph_info["canonicalQueryAvailable"] is False
    assert graph_info["canonicalQueryStatus"] == "not_connected_to_executor"

    canonical_only = api.Project(
        project_id="canonical-only",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-canonical-only",
        output_dir=tmp_path / "outputs-canonical-only",
        file_manifest={
            "design": [str(canonical_path)],
            "manufacturing": [],
            "carbon_factors": [],
        },
    )
    api.resolve_roles(canonical_only)
    assert canonical_only.canonical_graph_path == canonical_path
    assert canonical_only.graph_path is None
    with pytest.raises(HTTPException) as exc_info:
        api.init_runner(canonical_only)
    assert exc_info.value.status_code == 400
    assert "not compatible" in str(exc_info.value.detail)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_declared_invalid_or_ambiguous_canonical_uploads_fail_closed(
    component_release: Path, tmp_path: Path
) -> None:
    import dm2c_api_server as api

    canonical_path = component_release / OUTPUTS["graphJson"]
    invalid = json.loads(canonical_path.read_text(encoding="utf-8"))
    next(node for node in invalid["nodes"] if node["id"] == "emission:direct")["props"]["emissionValue"] = 999.0
    invalid_path = tmp_path / "invalid-v2.json"
    _write_json(invalid_path, invalid)
    invalid_project = api.Project(
        project_id="invalid-v2-upload",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-invalid",
        output_dir=tmp_path / "outputs-invalid",
        file_manifest={"design": [str(invalid_path)], "manufacturing": [], "carbon_factors": []},
    )
    with pytest.raises(HTTPException) as exc_info:
        api.resolve_roles(invalid_project)
    assert exc_info.value.status_code == 422

    excluded = json.loads(canonical_path.read_text(encoding="utf-8"))
    component = next(
        node for node in excluded["nodes"] if "BuildingComponent" in node["labels"]
    )
    component["labels"] = sorted(
        [label for label in component["labels"] if not label.startswith("Ifc")]
        + ["IfcValve"]
    )
    excluded_path = tmp_path / "excluded-ifc-v2.json"
    _write_json(excluded_path, excluded)
    excluded_project = api.Project(
        project_id="excluded-ifc-v2-upload",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-excluded",
        output_dir=tmp_path / "outputs-excluded",
        file_manifest={"design": [str(excluded_path)], "manufacturing": [], "carbon_factors": []},
    )
    with pytest.raises(HTTPException) as exc_info:
        api.resolve_roles(excluded_project)
    assert exc_info.value.status_code == 422
    assert "excluded factory" in str(exc_info.value.detail)

    second_path = tmp_path / "second-canonical.json"
    second_path.write_bytes(canonical_path.read_bytes())
    ambiguous_project = api.Project(
        project_id="ambiguous-v2-upload",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-ambiguous",
        output_dir=tmp_path / "outputs-ambiguous",
        file_manifest={
            "design": [str(canonical_path), str(second_path)],
            "manufacturing": [],
            "carbon_factors": [],
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        api.resolve_roles(ambiguous_project)
    assert exc_info.value.status_code == 400


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize(
    "name,content",
    (
        ("malformed.json", b'{"nodes": ['),
        ("invalid-utf8.json", b"\xff\xfe\x00"),
        ("array.json", b"[]"),
        ("scalar.json", b"42"),
        ("duplicate.json", b'{"nodes":[],"nodes":[],"edges":[]}'),
    ),
)
def test_invalid_design_json_is_rejected_during_role_resolution(
    tmp_path: Path, name: str, content: bytes
) -> None:
    import dm2c_api_server as api

    path = tmp_path / name
    path.write_bytes(content)
    project = api.Project(
        project_id=f"invalid-{name}",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        file_manifest={"design": [str(path)], "manufacturing": [], "carbon_factors": []},
    )
    with pytest.raises(HTTPException) as exc_info:
        api.resolve_roles(project)
    assert exc_info.value.status_code == 422
    assert "JSON" in str(exc_info.value.detail)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_first_ask_preserves_authoritative_graph_roles_and_rejects_backbone_only(
    component_release: Path, tmp_path: Path
) -> None:
    import dm2c_api_server as api

    canonical_path = component_release / OUTPUTS["graphJson"]

    class StubCompiler:
        """Stand in for the language model with one always-valid program."""

        def complete(self, _messages, **_kwargs):
            return {
                "content": json.dumps(
                    {
                        "steps": [
                            {"op": "SelectProject"},
                            {"op": "CarbonAtoms", "source": "material"},
                            {"op": "Aggregate", "metric": "sum_kgCO2e"},
                        ]
                    }
                )
            }

    carbonql = CarbonQLService.from_release(
        component_release, StubCompiler(), variant="V2"
    )

    project = api.Project(
        project_id="ask-graph-info",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads-ask",
        output_dir=tmp_path / "outputs-ask",
        graph_path=tmp_path / "backbone.json",
        canonical_graph_path=canonical_path,
        canonical_query_available=True,
        canonical_query_status="test_canonical_executor",
        canonical_graph_info={
            "schemaVersion": SCHEMA_VERSION,
            "canonicalGraphAvailable": True,
            "canonicalGraphStatus": "graph_document_validated",
            "canonicalValidationLevel": "graph_document",
            "graph": str(canonical_path),
            "stats": {"nodeCount": 1, "edgeCount": 0},
        },
        ifc_path=tmp_path / "typed_completed.ifc",
        backbone_build={"stats": {"nodes": 9, "edges": 8}},
        runner=object(),
        carbonql=carbonql,
        canonical_release_dir=component_release,
        payload={"results": [], "graphInfo": {"canonicalGraphAvailable": True}},
    )
    api._projects[project.project_id] = project
    try:
        response = asyncio.run(
            api.ask_question(project.project_id, api.AskRequest(question="carbon"))
        )
        payload = response["payload"]
        assert payload["graphInfo"]["canonicalGraphAvailable"] is True
        assert payload["graphInfo"]["canonicalQueryAvailable"] is True
        assert payload["graphInfo"]["canonicalQueryStatus"] == "test_canonical_executor"
        assert payload["graphInfo"]["backboneStats"] == {"nodes": 9, "edges": 8}
        assert payload["inputs"]["canonicalGraph"] == str(canonical_path)
        assert payload["inputs"]["ifcSource"] == str(project.ifc_path)
        assert payload["carbonAnswer"]["answered"] is True
        assert payload["carbonAnswer"]["releaseId"]

        project.canonical_query_available = False
        project.canonical_query_status = "not_connected_to_executor"
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api.ask_question(project.project_id, api.AskRequest(question="carbon"))
            )
        assert exc_info.value.status_code == 409
        assert "CarbonQL executor is not connected" in str(exc_info.value.detail)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api.run_project_experiment(
                    project.project_id,
                    api.ExperimentRunRequest(),
                )
            )
        assert exc_info.value.status_code == 409
        assert "CarbonQL executor is not connected" in str(exc_info.value.detail)

        project.canonical_graph_path = None
        project.canonical_graph_info = {}
        project.canonical_release_dir = None
        project.carbonql = None
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api.ask_question(project.project_id, api.AskRequest(question="carbon"))
            )
        assert exc_info.value.status_code == 409
        assert "carbon graph unavailable" in str(exc_info.value.detail)
    finally:
        api._projects.pop(project.project_id, None)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_initialized_payload_does_not_claim_query_readiness_without_executor(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace
    import dm2c_api_server as api

    project = api.Project(
        project_id="graph-only-payload",
        created_at=time.time(),
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        graph_path=tmp_path / "backbone.json",
        canonical_graph_path=tmp_path / "canonical.json",
        canonical_graph_info={"canonicalGraphAvailable": True},
        runner=SimpleNamespace(
            graph=SimpleNamespace(component_contexts=lambda: []),
            factors=SimpleNamespace(material_factors=[], energy_factors=[]),
            processes=SimpleNamespace(docs=[]),
        ),
    )
    payload = api.build_initialized_payload(project)
    assert "QA unavailable" in payload["architecture"]
    assert "not ready" in payload["reasoningTrace"][0]["reflection"]
    assert payload["graphInfo"]["canonicalGraphAvailable"] is True
    assert payload["graphInfo"]["canonicalQueryAvailable"] is False
