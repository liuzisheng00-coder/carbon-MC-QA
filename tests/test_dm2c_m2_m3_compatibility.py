from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

import dm2c_m2_m3_compatibility as compatibility
from dm2c_m2_m3_compatibility import check_compatibility, main
from dm2c_m3_context import load_m3_execution_context
from dm2c_m3_release import publish_derived_release
from dm2c_m23_canonical import stable_id
from tests.task11_v2_fixture import (
    semantic_graph_sha256,
    write_task10_release,
    write_task11_benchmark,
)


def _json_bytes(payload: object, *, sort_keys: bool = True) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=sort_keys,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


def _descriptor(payload: bytes, *, sidecar: bool) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
        "sizeBytes" if sidecar else "size_bytes": len(payload),
    }


def _rewrite_source_descriptors(benchmark: Path) -> None:
    sidecar = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / sidecar["sourceBenchmark"]["directory"]
    manifest_path = source_dir / "e4c_manifest_v2.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    for name in (
        "e4c_cases_v2.jsonl",
        "e4c_truth_v2.jsonl",
        "e4c_machine_audit_v2.json",
    ):
        payload = (source_dir / name).read_bytes()
        manifest["files"][name] = _descriptor(payload, sidecar=False)
    manifest_path.write_bytes(_json_bytes(manifest))
    for path in sorted(source_dir.iterdir(), key=lambda row: row.name):
        sidecar["sourceBenchmark"]["files"][path.name] = _descriptor(
            path.read_bytes(), sidecar=True
        )
    benchmark.write_bytes(_json_bytes(sidecar))


def _rebind_release(benchmark: Path, release: Path) -> None:
    payload = json.loads(benchmark.read_text("utf-8"))
    manifest = (release / "case_version_manifest.json").read_bytes()
    graph = (release / "multigranular_carbon_kg.json").read_bytes()
    payload["releaseBinding"] = {
        "releaseId": json.loads(manifest)["releaseId"],
        "manifestSha256": hashlib.sha256(manifest).hexdigest().upper(),
        "rawGraphSha256": hashlib.sha256(graph).hexdigest().upper(),
        "semanticGraphSha256": semantic_graph_sha256(release),
    }
    benchmark.write_bytes(_json_bytes(payload))


def _rewrite_graph_order(release: Path) -> None:
    graph_path = release / "multigranular_carbon_kg.json"
    graph = json.loads(graph_path.read_text("utf-8"))

    def reverse_mapping(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: reverse_mapping(item)
                for key, item in reversed(tuple(value.items()))
            }
        if isinstance(value, list):
            return [reverse_mapping(item) for item in value]
        return value

    graph["nodes"] = [reverse_mapping(row) for row in reversed(graph["nodes"])]
    graph["edges"] = [reverse_mapping(row) for row in reversed(graph["edges"])]
    graph_path.write_bytes(_json_bytes(graph, sort_keys=False))
    manifest_path = release / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["outputs"]["graphJson"] = {
        "path": "multigranular_carbon_kg.json",
        **_descriptor(graph_path.read_bytes(), sidecar=True),
    }
    manifest_path.write_bytes(_json_bytes(manifest))


def test_clean_controlled_release_and_bound_task10_truth_pass(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 0
    assert report.status == "clean"
    assert report.findings == ()
    assert report.totals["product"] == pytest.approx(43.0)
    assert report.totals["sourceProcess"] == pytest.approx(31.0)
    assert report.checks["processOnlyExcludedFromProducts"] is True
    assert report.checks["validZeroRetained"] is True
    assert report.checks["rejectedNonMaterialization"] is True
    assert report.checks["allocationConserved"] is True


def test_release_benchmark_manifest_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    other = write_task10_release(tmp_path / "other-release")
    manifest_path = other / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["generatedAtUtc"] = "2026-07-21T00:00:00Z"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
        newline="",
    )
    report = check_compatibility(other, benchmark)
    assert report.exit_code == 1
    assert "release_manifest_hash_mismatch" in {row.code for row in report.findings}


def test_tampered_expected_total_is_a_mismatch_not_a_self_comparison(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    payload["expectedTotals"]["product"] = 999.0
    benchmark.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
        newline="",
    )
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert "expected_total_mismatch" in {row.code for row in report.findings}


def test_tampered_consumed_task10_file_is_rejected_by_sidecar_hash(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / payload["sourceBenchmark"]["directory"]
    truth = source_dir / "e4c_truth_v2.jsonl"
    truth.write_text(truth.read_text("utf-8") + "\n", encoding="utf-8", newline="")
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert "source_benchmark_hash_mismatch" in {row.code for row in report.findings}


def test_graph_order_does_not_change_independent_gate_results(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release", reverse_graph_order=True)
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 0
    assert report.totals == pytest.approx(
        {
            "materialSource": 19.0,
            "productEnergy": 24.0,
            "product": 43.0,
            "sourceProcess": 31.0,
            "allSource": 50.0,
        }
    )


def test_unreadable_release_or_benchmark_returns_exit_two(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    report = check_compatibility(release, broken)
    assert report.exit_code == 2
    assert report.status == "unreadable"
    assert main(["--release-dir", str(release), "--benchmark", str(broken)]) == 2


def test_empty_compatibility_cases_cannot_pass_by_vacuous_comparison(
    tmp_path: Path,
) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    payload["cases"] = []
    benchmark.write_bytes(_json_bytes(payload))
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert "compatibility_case_contract_mismatch" in {
        row.code for row in report.findings
    }


def test_coherently_rehashed_truth_tamper_is_found_by_independent_oracle(
    tmp_path: Path,
) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / payload["sourceBenchmark"]["directory"]
    truth_path = source_dir / "e4c_truth_v2.jsonl"
    rows = [json.loads(line) for line in truth_path.read_text("utf-8").splitlines()]
    rows[0]["summary"]["total_kgCO2e"] = 999.0
    truth_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="",
    )
    _rewrite_source_descriptors(benchmark)
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert "source_truth_mismatch" in {row.code for row in report.findings}


def test_coherently_rehashed_trace_tamper_is_found(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / payload["sourceBenchmark"]["directory"]
    truth_path = source_dir / "e4c_truth_v2.jsonl"
    rows = [json.loads(line) for line in truth_path.read_text("utf-8").splitlines()]
    rows[0]["trace_rows"][0]["evidence_ids"] = []
    truth_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="",
    )
    _rewrite_source_descriptors(benchmark)
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert "source_truth_mismatch" in {row.code for row in report.findings}


def test_synchronized_source_case_shrink_still_fails_fixed_contract(
    tmp_path: Path,
) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / payload["sourceBenchmark"]["directory"]
    for name in ("e4c_cases_v2.jsonl", "e4c_truth_v2.jsonl"):
        path = source_dir / name
        path.write_text(path.read_text("utf-8").splitlines()[0] + "\n", encoding="utf-8", newline="")
    audit_path = source_dir / "e4c_machine_audit_v2.json"
    audit = json.loads(audit_path.read_text("utf-8"))
    audit["case_count"] = 1
    audit_path.write_bytes(_json_bytes(audit))
    _rewrite_source_descriptors(benchmark)
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert {
        "source_case_contract_mismatch",
        "source_audit_mismatch",
    } <= {row.code for row in report.findings}


def test_executor_rows_status_and_case_identity_are_independently_checked(
    tmp_path: Path,
) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    payload["cases"][0]["executorOutput"]["rows"][0]["kgCO2e"] = 999.0
    payload["cases"][1]["expectedM3Status"] = "unresolved_target"
    payload["cases"][2]["caseId"] = "renamed-energy-source"
    benchmark.write_bytes(_json_bytes(payload))
    first = check_compatibility(release, benchmark)
    second = check_compatibility(release, benchmark)
    assert first.exit_code == 1
    assert {
        "executor_output_mismatch",
        "expected_status_mismatch",
        "compatibility_case_contract_mismatch",
    } <= {
        row.code for row in first.findings
    }
    assert first.findings == second.findings
    assert list(first.findings) == sorted(
        first.findings, key=lambda row: (row.code, row.message)
    )


def test_graph_and_property_order_share_one_semantic_fingerprint(
    tmp_path: Path,
) -> None:
    original = write_task10_release(tmp_path / "original")
    reordered = write_task10_release(tmp_path / "reordered")
    original_semantic = semantic_graph_sha256(original)
    original_raw = hashlib.sha256(
        (original / "multigranular_carbon_kg.json").read_bytes()
    ).hexdigest()
    _rewrite_graph_order(reordered)
    assert semantic_graph_sha256(reordered) == original_semantic
    assert hashlib.sha256(
        (reordered / "multigranular_carbon_kg.json").read_bytes()
    ).hexdigest() != original_raw
    benchmark = write_task11_benchmark(reordered, tmp_path / "compatibility.json")
    report = check_compatibility(reordered, benchmark)
    assert report.exit_code == 0
    assert report.checks["graphOrderInvariant"] is True


def test_semantic_graph_change_cannot_pass_after_rebinding_raw_and_manifest_hashes(
    tmp_path: Path,
) -> None:
    source = write_task10_release(tmp_path / "source")
    benchmark = write_task11_benchmark(source, tmp_path / "compatibility.json")
    context = load_m3_execution_context(source)

    def transform_node(node: dict[str, object]) -> dict[str, object]:
        updated = {**node, "labels": list(node["labels"]), "props": dict(node["props"])}
        if updated["id"] == "consumption:direct":
            updated["props"]["productTargetId"] = "component:c2"  # type: ignore[index]
        return updated

    def transform_edge(edge: dict[str, object]) -> dict[str, object]:
        updated = {**edge, "props": dict(edge.get("props", {}))}
        if updated["occurrenceId"] == "occ:direct:recordedForObject:component:no-facts:":
            updated["tgt"] = "component:c2"
            updated["occurrenceId"] = "occ:direct:recordedForObject:component:c2:"
        return updated

    changed = publish_derived_release(
        context,
        output_root=tmp_path / "changed",
        release_id="controlled-task10-v2-semantic-tamper",
        node_transform=transform_node,
        edge_transform=transform_edge,
        projection_totals=(43.0, 31.0),
    )
    manifest_path = changed / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["releaseId"] = "controlled-task10-v2"
    manifest["command"]["releaseId"] = "controlled-task10-v2"
    manifest_path.write_bytes(_json_bytes(manifest))
    _rebind_release(benchmark, changed)
    report = check_compatibility(changed, benchmark)
    assert report.exit_code == 1
    assert {
        "source_truth_mismatch",
        "expected_component_total_mismatch",
    } <= {row.code for row in report.findings}


def test_gate_oracle_ignores_tainted_m3_projection_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    original_loader = compatibility.load_m3_execution_context

    def tainted_loader(path: Path) -> object:
        context = original_loader(path)
        return replace(context, projections=(), source_projections=())

    monkeypatch.setattr(compatibility, "load_m3_execution_context", tainted_loader)
    assert check_compatibility(release, benchmark).exit_code == 0


def test_missing_source_file_is_unreadable_and_hash_mismatch_is_not(
    tmp_path: Path,
) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / payload["sourceBenchmark"]["directory"]
    (source_dir / "e4c_truth_v2.jsonl").unlink()
    first = check_compatibility(release, benchmark)
    second = check_compatibility(release, benchmark)
    assert first.exit_code == 2
    assert first.findings == second.findings


def test_allocation_raw_weights_must_normalize_to_recorded_shares(
    tmp_path: Path,
) -> None:
    source = write_task10_release(tmp_path / "source")
    benchmark = write_task11_benchmark(source, tmp_path / "compatibility.json")

    def transform(edge):
        updated = {**edge, "props": dict(edge.get("props", {}))}
        if updated["type"] == "recordedForObject" and updated["src"] == "consumption:allocated":
            updated["props"]["rawWeight"] = (
                99.0 if updated["tgt"] == "component:c1" else 1.0
            )
        return updated

    changed = publish_derived_release(
        load_m3_execution_context(source),
        output_root=tmp_path / "changed",
        release_id="controlled-task10-v2-raw-weight-tamper",
        edge_transform=transform,
    )
    manifest_path = changed / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["releaseId"] = "controlled-task10-v2"
    manifest["command"]["releaseId"] = "controlled-task10-v2"
    manifest_path.write_bytes(_json_bytes(manifest))
    _rebind_release(benchmark, changed)
    report = check_compatibility(changed, benchmark)
    assert report.exit_code == 1
    assert any(
        row.code == "invariant_mismatch" and row.message == "allocationConserved"
        for row in report.findings
    )


def test_extra_rejection_cannot_be_hidden_by_synchronized_truth_and_hashes(
    tmp_path: Path,
) -> None:
    source = write_task10_release(tmp_path / "source")
    benchmark = write_task11_benchmark(source, tmp_path / "compatibility.json")
    source_context = load_m3_execution_context(source)
    base = dict(
        next(
            row
            for row in source_context.canonical.validation_rows
            if row["status"] == "rejected"
        )
    )
    base.update(
        {
            "recordId": "record:rejected-extra",
            "sourceRecordId": "source:rejected-extra",
            "evidenceSourceId": "evidence:rejected-extra",
            "sourceIdentity": stable_id(
                "SourceRecord", "evidence:rejected-extra", "source:rejected-extra"
            ),
        }
    )
    evidence = json.loads(base["evidenceJson"])
    evidence.update(
        {
            "sourceRecordId": base["sourceRecordId"],
            "evidenceSourceId": base["evidenceSourceId"],
            "sourceIdentity": base["sourceIdentity"],
        }
    )
    base["evidenceJson"] = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    changed = publish_derived_release(
        source_context,
        output_root=tmp_path / "changed",
        release_id="controlled-task10-v2-extra-rejection",
        additional_validation_rows=(base,),
    )
    manifest_path = changed / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["releaseId"] = "controlled-task10-v2"
    manifest["command"]["releaseId"] = "controlled-task10-v2"
    manifest_path.write_bytes(_json_bytes(manifest))
    _rebind_release(benchmark, changed)

    sidecar = json.loads(benchmark.read_text("utf-8"))
    source_dir = benchmark.parent / sidecar["sourceBenchmark"]["directory"]
    truth_path = source_dir / "e4c_truth_v2.jsonl"
    truth = [json.loads(line) for line in truth_path.read_text("utf-8").splitlines()]
    for row in truth:
        row["coverage"]["rejected_count"] = 2
    truth_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in truth
        ),
        encoding="utf-8",
        newline="",
    )
    audit_path = source_dir / "e4c_machine_audit_v2.json"
    audit = json.loads(audit_path.read_text("utf-8"))
    audit["rejected_validation_count"] = 2
    audit_path.write_bytes(_json_bytes(audit))
    for case in sidecar["cases"]:
        case["executorOutput"]["coverage"]["rejected_count"] = 2
    benchmark.write_bytes(_json_bytes(sidecar))
    _rewrite_source_descriptors(benchmark)
    report = check_compatibility(changed, benchmark)
    assert report.exit_code == 1
    assert "controlled_validation_mismatch" in {
        row.code for row in report.findings
    }


def test_source_descriptor_map_rejects_phantom_entries(tmp_path: Path) -> None:
    release = write_task10_release(tmp_path / "release")
    benchmark = write_task11_benchmark(release, tmp_path / "compatibility.json")
    payload = json.loads(benchmark.read_text("utf-8"))
    payload["sourceBenchmark"]["files"]["phantom.json"] = {
        "sha256": "0" * 64,
        "sizeBytes": 0,
    }
    benchmark.write_bytes(_json_bytes(payload))
    report = check_compatibility(release, benchmark)
    assert report.exit_code == 1
    assert "source_benchmark_contract_mismatch" in {
        row.code for row in report.findings
    }
