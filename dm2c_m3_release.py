"""Safe derivation of temporary controlled canonical-v2 releases for M3 tests."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_PREDICATES,
    SCHEMA_VERSION,
    CanonicalLPGGraph,
)
from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_m3_context import M3ExecutionContext


_OUTPUTS = {
    "graphJson": "multigranular_carbon_kg.json",
    "cypher": "multigranular_carbon_kg.cypher",
    "stats": "multigranular_carbon_kg_stats.json",
    "validation": "multigranular_carbon_kg_validation.csv",
    "alignmentReport": "m2_alignment_report.json",
}
_VALIDATION_FIELDS = (
    "recordId",
    "sourceIdentity",
    "sourceRecordId",
    "evidenceSourceId",
    "kind",
    "status",
    "reasonCode",
    "message",
    "consumptionId",
    "quantityId",
    "factorId",
    "emissionId",
    "formulaCode",
    "isValidZero",
    "evidenceJson",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def canonical_release_digest(release_dir: Path) -> str:
    payload = b"".join(
        item.name.encode("utf-8") + b"\0" + item.read_bytes()
        for item in sorted(release_dir.iterdir(), key=lambda row: row.name)
        if item.is_file()
    )
    return hashlib.sha256(payload).hexdigest().upper()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _descriptor(filename: str, payload: bytes) -> dict[str, Any]:
    return {
        "path": filename,
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
        "sizeBytes": len(payload),
    }


def _validation_counts(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    accepted = [row for row in rows if row["status"] == "accepted"]
    rejected = [row for row in rows if row["status"] == "rejected"]
    accepted_by_kind = Counter(row["kind"] for row in accepted)
    rejected_by_kind = Counter(row["kind"] for row in rejected)
    rejected_by_reason = Counter(row["reasonCode"] for row in rejected)
    count = len(rows)
    return {
        "candidateCount": count,
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "acceptedByKind": dict(sorted(accepted_by_kind.items())),
        "rejectedByKind": dict(sorted(rejected_by_kind.items())),
        "rejectedByReason": dict(sorted(rejected_by_reason.items())),
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": len(accepted) / count if count else 0.0,
    }


def _stats(
    graph: CanonicalLPGGraph,
    rows: Sequence[Mapping[str, str]],
    module_id: str,
) -> dict[str, Any]:
    labels = Counter(
        label for node in graph.nodes.values() for label in node.get("labels", ())
    )
    relations = Counter(edge["type"] for edge in graph.edges)
    modes = Counter(
        str(node["props"].get("attributionMode", ""))
        for node in graph.nodes.values()
        if "EnergyConsumption" in node.get("labels", ())
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "moduleId": module_id,
        "nodeCount": len(graph.nodes),
        "edgeCount": len(graph.edges),
        "applicationClassCounts": {
            label: labels.get(label, 0) for label in APPLICATION_CLASSES
        },
        "ifcRefinementCounts": dict(
            sorted(
                (label, count)
                for label, count in labels.items()
                if label not in APPLICATION_CLASSES and label.startswith("Ifc")
            )
        ),
        "relationCounts": {
            relation: relations.get(relation, 0)
            for relation in (*PRINCIPAL_PREDICATES, *sorted(OPTIONAL_CONTEXT_PREDICATES))
        },
        "calculationCounts": {
            "material": labels.get("MaterialConsumption", 0),
            "energy": labels.get("EnergyConsumption", 0),
            "validZero": sum(
                node["props"].get("isValidZero") is True
                for node in graph.nodes.values()
                if "CarbonEmission" in node.get("labels", ())
            ),
        },
        "attributionCounts": {
            "direct": modes.get("direct", 0),
            "allocated": modes.get("allocated", 0),
            "process_only": modes.get("process_only", 0),
        },
        "validation": _validation_counts(rows),
    }


def _graph_from_source(
    context: M3ExecutionContext,
    edge_transform: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    node_transform: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    node_filter: Callable[[Mapping[str, Any]], bool] | None,
    edge_filter: Callable[[Mapping[str, Any]], bool] | None,
) -> CanonicalLPGGraph:
    payload = json.loads(
        (context.canonical.release_dir / _OUTPUTS["graphJson"]).read_text("utf-8")
    )
    graph = CanonicalLPGGraph()
    for node in payload["nodes"]:
        if node_filter is not None and not node_filter(node):
            continue
        node = node_transform(node) if node_transform else node
        graph.add_node(str(node["id"]), list(node["labels"]), dict(node["props"]))
    for original in payload["edges"]:
        if edge_filter is not None and not edge_filter(original):
            continue
        edge = edge_transform(original) if edge_transform else original
        graph.add_edge(
            str(edge["src"]),
            str(edge["type"]),
            str(edge["tgt"]),
            dict(edge.get("props", {})),
            occurrence_id=str(edge["occurrenceId"]),
        )
    return graph


def _csv_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=_VALIDATION_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8")


def publish_derived_release(
    context: M3ExecutionContext,
    *,
    output_root: Path,
    release_id: str,
    edge_transform: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    node_transform: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    node_filter: Callable[[Mapping[str, Any]], bool] | None = None,
    edge_filter: Callable[[Mapping[str, Any]], bool] | None = None,
    validation_filter: Callable[[Mapping[str, str]], bool] | None = None,
    additional_validation_rows: Sequence[Mapping[str, str]] = (),
    projection_totals: tuple[float, float] | None = None,
) -> Path:
    if not context.controlled_fixture:
        raise ValueError("derived experiments require an explicit controlled fixture")
    if not _SAFE_ID.fullmatch(release_id) or "v2" not in release_id.casefold():
        raise ValueError("release_id must be a safe v2 identifier")
    target = output_root / release_id
    staging = output_root / f".{release_id}.staging"
    if target.exists() or staging.exists():
        raise FileExistsError(target if target.exists() else staging)
    graph = _graph_from_source(
        context, edge_transform, node_transform, node_filter, edge_filter
    )
    rows = [
        dict(row)
        for row in context.canonical.validation_rows
        if validation_filter is None or validation_filter(row)
    ]
    rows.extend(dict(row) for row in additional_validation_rows)
    rows.sort(
        key=lambda row: (
            row["recordId"],
            0 if row["status"] == "accepted" else 1,
            row["reasonCode"],
        )
    )
    if len({row["recordId"] for row in rows}) != len(rows):
        raise ValueError("validation record identities must be unique")
    graph_bytes = _json_bytes(graph.to_payload())
    cypher_bytes = graph.to_cypher().encode("utf-8")
    stats = _stats(graph, rows, str(context.canonical.manifest["module"]["runtimeId"]))
    stats_bytes = _json_bytes(stats)
    validation_bytes = _csv_bytes(rows)
    if projection_totals is None:
        product_total = math.fsum(
            row.projected_value for row in context.canonical.product_contributions
        )
        source_process = math.fsum(
            fact.emission_value
            for fact in context.canonical.emissions
            if fact.kind == "energy"
        )
    else:
        product_total, source_process = map(float, projection_totals)
        if not all(math.isfinite(value) and value >= 0 for value in (product_total, source_process)):
            raise ValueError("projection totals must be finite and non-negative")
    alignment = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "pass",
        "highSeverityViolationCount": 0,
        "findings": [],
        "gates": dict(context.canonical.manifest["gates"]),
        "projectionTotals": {
            "product": product_total,
            "sourceProcess": source_process,
        },
        "checkedCounts": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "validationRows": len(rows),
        },
    }
    alignment_bytes = _json_bytes(alignment)
    blobs = {
        "graphJson": graph_bytes,
        "cypher": cypher_bytes,
        "stats": stats_bytes,
        "validation": validation_bytes,
        "alignmentReport": alignment_bytes,
    }
    manifest = _plain(context.canonical.manifest)
    manifest["releaseId"] = release_id
    manifest["generatedAtUtc"] = "2026-07-21T00:00:00Z"
    manifest["command"]["releaseId"] = release_id
    manifest["coverage"] = stats["validation"]
    manifest["counts"] = stats
    manifest["outputs"] = {
        key: _descriptor(_OUTPUTS[key], blobs[key]) for key in _OUTPUTS
    }
    manifest_bytes = _json_bytes(manifest)
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        for key, filename in _OUTPUTS.items():
            (staging / filename).write_bytes(blobs[key])
        (staging / "case_version_manifest.json").write_bytes(manifest_bytes)
        load_canonical_v2_context(staging)
        staging.rename(target)
    except Exception:
        if staging.exists():
            for item in staging.iterdir():
                if item.is_file():
                    item.unlink()
            staging.rmdir()
        raise
    return target


__all__ = ["canonical_release_digest", "publish_derived_release"]
