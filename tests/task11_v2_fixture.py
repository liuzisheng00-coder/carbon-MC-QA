"""File-backed controlled inputs shared by the Task 11 M3 tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram
from dm2c_carbonql_executor import CarbonQLExecutor
from dm2c_carbonql_heldout import build_heldout_cases, freeze_heldout_benchmark
from dm2c_m3_context import load_m3_execution_context
from dm2c_m3_release import publish_derived_release
from tests.task10_v2_fixture import write_task10_release


def release_digest(release_dir: Path) -> str:
    payload = b"".join(
        path.name.encode("utf-8") + b"\0" + path.read_bytes()
        for path in sorted(release_dir.iterdir(), key=lambda item: item.name)
        if path.is_file()
    )
    return hashlib.sha256(payload).hexdigest().upper()


def semantic_graph_sha256(release_dir: Path) -> str:
    graph = json.loads((release_dir / "multigranular_carbon_kg.json").read_text("utf-8"))
    normalized = {
        "schemaVersion": graph["schemaVersion"],
        "nodes": sorted(graph["nodes"], key=lambda row: row["id"]),
        "edges": sorted(graph["edges"], key=lambda row: row["occurrenceId"]),
    }
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def program(*steps: dict[str, Any]) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict({"steps": list(steps)})


def controlled_programs() -> tuple[tuple[str, CarbonQLProgram, tuple[str, ...]], ...]:
    return (
        (
            "product-project",
            program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            (),
        ),
        (
            "material-source",
            program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "material"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            (),
        ),
        (
            "energy-source",
            program(
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "GroupBy", "keys": ["carrier"]},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            (),
        ),
        (
            "accepted-zero",
            program(
                {"op": "SelectClicked", "ids": ["component:c4"]},
                {"op": "CarbonAtoms", "source": "process"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ),
            ("component:c4",),
        ),
        (
            "unknown-component",
            program(
                {"op": "SelectClicked", "ids": ["component:missing"]},
                {"op": "CarbonAtoms", "source": "all"},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ),
            ("component:missing",),
        ),
    )


def write_task11_benchmark(release_dir: Path, path: Path) -> Path:
    """Bind Task 10 controlled truth plus executor outputs to one exact release."""
    context = load_canonical_v2_context(release_dir)
    executor = CarbonQLExecutor.from_context(context)
    source_root = path.parent / f"{path.stem}_task10_source"
    source_dir = freeze_heldout_benchmark(
        build_heldout_cases(context),
        context,
        output_root=source_root,
        freeze_id="controlled-v2-task11-source",
    )
    source_files = {
        item.name: {
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest().upper(),
            "sizeBytes": item.stat().st_size,
        }
        for item in sorted(source_dir.iterdir(), key=lambda row: row.name)
        if item.is_file()
    }
    cases = []
    m3_statuses = {
        "product-project": "executable",
        "material-source": "executable",
        "energy-source": "incomplete_path",
        "accepted-zero": "executable",
        "unknown-component": "unresolved_target",
    }
    for case_id, query, selected in controlled_programs():
        cases.append(
            {
                "caseId": case_id,
                "program": query.to_dict(),
                "selectedComponentIds": list(selected),
                "executorOutput": executor.execute(query, selected).to_dict(),
                "expectedM3Status": m3_statuses[case_id],
            }
        )
    manifest_bytes = (release_dir / "case_version_manifest.json").read_bytes()
    graph_bytes = (release_dir / "multigranular_carbon_kg.json").read_bytes()
    payload = {
        "schemaVersion": "m23-canonical-v2",
        "benchmarkProfile": "controlled-task11-v2",
        "releaseBinding": {
            "releaseId": str(context.manifest["releaseId"]),
            "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest().upper(),
            "rawGraphSha256": hashlib.sha256(graph_bytes).hexdigest().upper(),
            "semanticGraphSha256": semantic_graph_sha256(release_dir),
        },
        "sourceBenchmark": {
            "directory": source_dir.relative_to(path.parent).as_posix(),
            "files": source_files,
        },
        "expectedTotals": {
            "materialSource": 19.0,
            "productEnergy": 24.0,
            "product": 43.0,
            "sourceProcess": 31.0,
            "allSource": 50.0,
            "components": {
                "component:c1": 15.0,
                "component:c2": 21.0,
                "component:no-facts": 6.0,
                "component:c4": 1.0,
            },
            "modules": {"module:fixture": 43.0},
            "materials": {"material:steel": 16.0, "material:unused": 3.0},
            "carriers": {"carrier:electricity": 24.0, "carrier:gas": 7.0},
            "processes": {"process:stage": 7.0},
        },
        "cases": cases,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
        newline="",
    )
    return path


def write_actual_like_no_energy_release(root: Path) -> Path:
    root.mkdir(parents=True)
    source = write_task10_release(root / "source")
    context = load_m3_execution_context(source)
    energy_facts = [fact for fact in context.canonical.emissions if fact.kind == "energy"]
    removed = {
        value
        for fact in energy_facts
        for value in (
            fact.emission_id,
            fact.consumption_id,
            fact.quantity_id,
            fact.factor_id,
            fact.carrier_id,
        )
        if value
    }
    return publish_derived_release(
        context,
        output_root=root / "derived",
        release_id="controlled-v2-actual-like-no-energy",
        node_filter=lambda node: node["id"] not in removed,
        edge_filter=lambda edge: edge["src"] not in removed and edge["tgt"] not in removed,
        validation_filter=lambda row: row["kind"] != "energy",
        projection_totals=(19.0, 0.0),
    )


__all__ = [
    "controlled_programs",
    "program",
    "release_digest",
    "write_task10_release",
    "write_task11_benchmark",
    "write_actual_like_no_energy_release",
]
