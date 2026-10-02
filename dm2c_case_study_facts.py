"""Derive the case-study facts of manuscript 4.1.1 from one frozen release.

Every number the case-study section reports about the model, the carbon account
and its boundary is computed here from the release itself, so the manuscript
text and the released artefacts cannot drift apart. Facts that no release can
supply, such as photographs or the physical dimensions of the module, are not
invented here; they are listed as open items for the author to fill in.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    dimension_ids,
    graph_document_from_context,
    iter_graph_edges,
    iter_graph_nodes,
    load_canonical_v2_context,
    lookup_dimension,
)

DEFAULT_RELEASE = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)
DEFAULT_OUTPUT_ROOT = Path("outputs/research_experiments/case_study_facts")

# Project facts the release cannot hold, supplied by the authors rather than
# derived from the graph. They are carried here so that 4.1.1 draws every number
# from one place, but they are rendered under their own heading because they do
# not inherit the release's reproducibility guarantee.
CASE_CONTEXT = (
    ("project", "PolyU student residence project"),
    ("storeys", "14-15"),
    ("gross floor area", "19,500 m2"),
    ("modules in the project", "873"),
    ("module types", "5"),
    ("module mass", "5-6 t each, excluding movable furniture"),
    (
        "Type-A internal dimensions",
        "5.616 m x 2.546 m (12.34 m2 usable, plus a 1.72 m2 bathroom)",
    ),
    ("Type-B / B-R internal dimensions", "5.560 m x 2.695 m"),
    ("workstation", "Intel Core i9-13900H, 14 cores / 20 threads, 47.6 GB RAM"),
    ("operating system", "Windows 11, build 10.0.26200"),
    ("Python", "3.12.7 (CPython)"),
    ("language model", "deepseek-chat, temperature 0"),
    (
        "language model execution",
        "remote API; no local GPU inference, so the workstation only compiles, "
        "executes and scores",
    ),
)

# Facts still missing. They are carried here so the section has one checklist
# rather than a note buried in a spreadsheet.
OPEN_ITEMS = (
    "de-identification of the corporate marks visible in the second module "
    "photograph, and whether any factory floor photograph is available",
    "the date on which the reported language-model calls were made, to complete "
    "the reporting convention alongside the model and temperature above",
    "English glosses for the authoring-tool material names carried over from the "
    "IFC model, and the grouping into material families used in the text",
)


def _props(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("props", {})
    return value if isinstance(value, Mapping) else {}


def _name_of(context: CanonicalV2Context, dimension: str, entity_id: str | None) -> str:
    """Prefer a readable label, and fall back to the id so nothing is silently lost."""
    if entity_id is None:
        return "(unattributed)"
    lookup_name = "process" if dimension == "stage" else dimension
    try:
        props = _props(lookup_dimension(context, lookup_name, entity_id))
    except Exception:
        return entity_id
    for key in ("name", "stageName", "activityName", "templateName"):
        value = props.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return entity_id


def _stage_by_activity(context: CanonicalV2Context) -> dict[str, str | None]:
    owners: dict[str, set[str]] = {}
    for edge in iter_graph_edges(graph_document_from_context(context)):
        if edge.get("type") != "hasActivity":
            continue
        stage_id = str(edge.get("src") or "")
        activity_id = str(edge.get("tgt") or "")
        if stage_id and activity_id:
            owners.setdefault(activity_id, set()).add(stage_id)
    return {
        activity_id: next(iter(stages)) if len(stages) == 1 else None
        for activity_id, stages in owners.items()
    }


def _total(values: Sequence[float]) -> float:
    return math.fsum(sorted(values))


def ifc_composition(context: CanonicalV2Context) -> dict[str, int]:
    """Count the building objects the study actually reasons over, by IFC class."""
    counts: dict[str, int] = defaultdict(int)
    for entity_id in dimension_ids(context, "component"):
        props = _props(lookup_dimension(context, "component", entity_id))
        counts[str(props.get("ifcClass") or "(unclassified)")] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def material_breakdown(context: CanonicalV2Context) -> list[dict[str, Any]]:
    """Attribute A1 material carbon and its driving quantity to each material."""
    carbon: dict[str | None, list[float]] = defaultdict(list)
    quantity: dict[str | None, list[float]] = defaultdict(list)
    units: dict[str | None, set[str]] = defaultdict(set)
    for fact in context.emissions:
        if fact.kind != "material":
            continue
        carbon[fact.material_id].append(float(fact.emission_value))
        quantity[fact.material_id].append(float(fact.quantity_value))
        units[fact.material_id].add(str(fact.quantity_unit))
    rows = [
        {
            "material": _name_of(context, "material", material_id),
            "record_count": len(values),
            "quantity": round(_total(quantity[material_id]), 3),
            "quantity_unit": (
                sorted(units[material_id])[0] if len(units[material_id]) == 1 else "mixed"
            ),
            "kgCO2e": round(_total(values), 3),
        }
        for material_id, values in carbon.items()
    ]
    return sorted(rows, key=lambda row: -row["kgCO2e"])


def energy_breakdown(context: CanonicalV2Context) -> dict[str, list[dict[str, Any]]]:
    """Split recorded process carbon by carrier and by owning production stage."""
    stage_of = _stage_by_activity(context)
    by_carrier: dict[str | None, list[float]] = defaultdict(list)
    by_stage: dict[str | None, list[float]] = defaultdict(list)
    carrier_quantity: dict[str | None, list[float]] = defaultdict(list)
    carrier_units: dict[str | None, set[str]] = defaultdict(set)
    for fact in context.emissions:
        if fact.kind != "energy":
            continue
        by_carrier[fact.carrier_id].append(float(fact.emission_value))
        carrier_quantity[fact.carrier_id].append(float(fact.quantity_value))
        carrier_units[fact.carrier_id].add(str(fact.quantity_unit))
        stage_id: str | None = None
        for entity_id in fact.process_ids:
            resolved = stage_of.get(entity_id)
            if resolved is not None:
                stage_id = resolved
                break
        by_stage[stage_id].append(float(fact.emission_value))
    carriers = [
        {
            "carrier": _name_of(context, "carrier", carrier_id),
            "record_count": len(values),
            "quantity": round(_total(carrier_quantity[carrier_id]), 3),
            "quantity_unit": (
                sorted(carrier_units[carrier_id])[0]
                if len(carrier_units[carrier_id]) == 1
                else "mixed"
            ),
            "kgCO2e": round(_total(values), 3),
        }
        for carrier_id, values in by_carrier.items()
    ]
    stages = [
        {
            "stage": _name_of(context, "stage", stage_id),
            "record_count": len(values),
            "kgCO2e": round(_total(values), 3),
        }
        for stage_id, values in by_stage.items()
    ]
    return {
        "by_carrier": sorted(carriers, key=lambda row: -row["kgCO2e"]),
        "by_stage": sorted(stages, key=lambda row: -row["kgCO2e"]),
    }


def emission_factors(context: CanonicalV2Context) -> list[dict[str, Any]]:
    """List every factor the account applies, with the basis it is applied on.

    The system boundary and geography travel with the value because two factors
    for the same carrier can differ by more than their number: a direct
    combustion factor and a lifecycle factor are not interchangeable.
    """
    rows: list[dict[str, Any]] = []
    for node in iter_graph_nodes(graph_document_from_context(context)):
        if "EmissionFactor" not in tuple(node.get("labels") or ()):
            continue
        props = _props(node)
        rows.append(
            {
                "keyword": str(props.get("keyword") or ""),
                "value": props.get("factorValue"),
                "unit": str(props.get("factorUnit") or ""),
                "source": str(props.get("source") or ""),
                "geography": str(props.get("geography") or ""),
                "system_boundary": str(props.get("systemBoundary") or ""),
                "year": str(props.get("year") or ""),
                "is_proxy": str(props.get("proxyStatus") or "") != "not_proxy",
                "reference": str(props.get("sourceReference") or ""),
            }
        )
    return sorted(rows, key=lambda row: row["keyword"])


def carbon_totals(context: CanonicalV2Context) -> dict[str, Any]:
    """Report the two perspectives separately; they are not one total split in two.

    The product perspective sums what is attributable to building objects. The
    process perspective sums every recorded factory energy emission, including
    the records that no object can carry.
    """
    material = _total(
        [float(f.emission_value) for f in context.emissions if f.kind == "material"]
    )
    energy_recorded = _total(
        [float(f.emission_value) for f in context.emissions if f.kind == "energy"]
    )
    product = _total([float(c.projected_value) for c in context.product_contributions])
    energy_attributed = product - material
    whole_process_only_count = len(context.process_only_emissions)
    partial_unattributable_count = sum(
        1
        for fact in context.emissions
        if fact.kind == "energy"
        and str(context._nodes_by_id[fact.consumption_id]["props"].get("attributionMode")) == "allocated"
        and float(context._nodes_by_id[fact.consumption_id]["props"].get("unattributedFraction", 0.0)) > 0.0
    )
    return {
        "A1_material_kgCO2e": round(material, 3),
        "A3_process_attributed_kgCO2e": round(energy_attributed, 3),
        "product_perspective_total_kgCO2e": round(product, 3),
        "process_perspective_recorded_kgCO2e": round(energy_recorded, 3),
        "process_only_unattributable_kgCO2e": round(energy_recorded - energy_attributed, 3),
        "process_only_record_count": whole_process_only_count,
        "partial_unattributable_record_count": partial_unattributable_count,
    }


def build_facts(context: CanonicalV2Context) -> dict[str, Any]:
    manifest = context.manifest
    counts = dict(manifest.get("counts") or {})
    coverage = dict(manifest.get("coverage") or {})
    composition = ifc_composition(context)
    return {
        "release": {
            "release_id": str(manifest.get("releaseId") or ""),
            "release_profile": str(manifest.get("releaseProfile") or ""),
            "schema_version": str(manifest.get("schemaVersion") or ""),
            "generated_at_utc": str(manifest.get("generatedAtUtc") or ""),
            "requested_scope": str(
                (manifest.get("configuration") or {}).get("requestedScope") or ""
            ),
            "synthetic_factory_inputs": bool(
                manifest.get("syntheticFactoryInputsUsed", False)
            ),
            "gates": dict(manifest.get("gates") or {}),
        },
        "module": dict(manifest.get("module") or {}),
        "graph": {
            "node_count": counts.get("nodeCount"),
            "edge_count": counts.get("edgeCount"),
            "class_counts": dict(counts.get("applicationClassCounts") or {}),
            "relation_counts": dict(counts.get("relationCounts") or {}),
        },
        "ifc_composition": {
            "component_count": sum(composition.values()),
            "distinct_ifc_classes": len(composition),
            "by_ifc_class": composition,
        },
        "coverage": {
            "candidate_count": coverage.get("candidateCount"),
            "accepted_count": coverage.get("acceptedCount"),
            "accepted_by_kind": dict(coverage.get("acceptedByKind") or {}),
            "rejected_count": coverage.get("rejectedCount"),
            "rejected_by_reason": dict(coverage.get("rejectedByReason") or {}),
            "coverage_value": coverage.get("coverageValue"),
            "coverage_formula": coverage.get("coverageFormula"),
        },
        "attribution": dict(counts.get("attributionCounts") or {}),
        "carbon_totals": carbon_totals(context),
        "material_breakdown": material_breakdown(context),
        "energy_breakdown": energy_breakdown(context),
        "emission_factors": emission_factors(context),
        "case_context": [list(row) for row in CASE_CONTEXT],
        "open_items": list(OPEN_ITEMS),
    }


def build_construction_facts(context: CanonicalV2Context) -> dict[str, Any]:
    """Return IFC-construction facts without deriving accounting results."""
    manifest = context.manifest
    counts = dict(manifest.get("counts") or {})
    if (
        manifest.get("releaseProfile") != "actual-case"
        or manifest.get("releaseReady") is not False
        or (manifest.get("inputs") or {}).get("materialEvidence") is not None
        or (manifest.get("inputs") or {}).get("factoryInput") is not None
    ):
        raise ValueError("construction facts require an evidence-free actual-case release")
    calculation = dict(counts.get("calculationCounts") or {})
    if any(calculation.get(key) != 0 for key in ("material", "energy", "validZero")):
        raise ValueError("construction facts require zero accounting calculations")
    composition = ifc_composition(context)
    return {
        "release": {
            "release_id": str(manifest.get("releaseId") or ""),
            "release_profile": str(manifest.get("releaseProfile") or ""),
            "schema_version": str(manifest.get("schemaVersion") or ""),
            "generated_at_utc": str(manifest.get("generatedAtUtc") or ""),
            "requested_scope": str(
                (manifest.get("configuration") or {}).get("requestedScope") or ""
            ),
            "gates": dict(manifest.get("gates") or {}),
        },
        "module": dict(manifest.get("module") or {}),
        "graph": {
            "node_count": counts.get("nodeCount"),
            "edge_count": counts.get("edgeCount"),
            "class_counts": dict(counts.get("applicationClassCounts") or {}),
            "relation_counts": dict(counts.get("relationCounts") or {}),
        },
        "ifc_composition": {
            "component_count": sum(composition.values()),
            "distinct_ifc_classes": len(composition),
            "by_ifc_class": composition,
        },
    }


def render_construction_markdown(facts: Mapping[str, Any]) -> str:
    """Render the restricted construction-only facts package."""
    release = facts["release"]
    graph = facts["graph"]
    ifc = facts["ifc_composition"]
    return "\n".join(
        [
            "# Construction-only case-study facts",
            "",
            "This package reports canonical-graph construction only. It contains no carbon totals, coverage, or accuracy claims.",
            "",
            "## Release under study",
            "",
            *_table(
                ("field", "value"),
                [
                    ("release id", release["release_id"]),
                    ("release profile", release["release_profile"]),
                    ("schema version", release["schema_version"]),
                    ("generated (UTC)", release["generated_at_utc"]),
                    ("requested scope", release["requested_scope"]),
                    ("alignment gate", release["gates"].get("alignment", "")),
                ],
            ),
            "",
            "## Knowledge graph composition",
            "",
            *_table(
                ("field", "value"),
                [
                    ("nodes", graph["node_count"]),
                    ("edges", graph["edge_count"]),
                    ("building components", ifc["component_count"]),
                    ("distinct IFC classes", ifc["distinct_ifc_classes"]),
                    ("component types", graph["class_counts"].get("ComponentType")),
                    ("design quantities", graph["class_counts"].get("DesignQuantity")),
                    ("materials", graph["class_counts"].get("IfcMaterial")),
                ],
            ),
            "",
        ]
    )


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render_markdown(facts: Mapping[str, Any]) -> str:
    release = facts["release"]
    graph = facts["graph"]
    ifc = facts["ifc_composition"]
    coverage = facts["coverage"]
    totals = facts["carbon_totals"]
    lines: list[str] = [
        "# Case-study facts for manuscript 4.1.1",
        "",
        "Every number below is derived from the frozen release named here, with the",
        "single exception of the author-supplied case context at the end, which is",
        "marked as such. Regenerate with `python dm2c_case_study_facts.py` after any",
        "release change.",
        "",
        "## Release under study",
        "",
        *_table(
            ("field", "value"),
            [
                ("release id", release["release_id"]),
                ("release profile", release["release_profile"]),
                ("schema version", release["schema_version"]),
                ("generated (UTC)", release["generated_at_utc"]),
                ("requested scope", release["requested_scope"]),
                ("synthetic factory inputs", release["synthetic_factory_inputs"]),
                ("alignment gate", release["gates"].get("alignment", "")),
                ("structural cypher gate", release["gates"].get("structuralCypher", "")),
            ],
        ),
        "",
        "## Knowledge graph composition",
        "",
        *_table(
            ("field", "value"),
            [
                ("nodes", graph["node_count"]),
                ("edges", graph["edge_count"]),
                ("building components", ifc["component_count"]),
                ("distinct IFC classes", ifc["distinct_ifc_classes"]),
                ("component types", graph["class_counts"].get("ComponentType")),
                ("design quantities", graph["class_counts"].get("DesignQuantity")),
                ("materials", graph["class_counts"].get("IfcMaterial")),
                ("production stages", graph["class_counts"].get("ProductionStage")),
                ("manufacturing activities", graph["class_counts"].get("ManufacturingActivity")),
                ("energy carriers", graph["class_counts"].get("EnergyCarrier")),
                ("carbon emission records", graph["class_counts"].get("CarbonEmission")),
            ],
        ),
        "",
        "## Building objects by IFC class",
        "",
        *_table(
            ("IFC class", "count"),
            [(name, count) for name, count in ifc["by_ifc_class"].items()],
        ),
        "",
        "## Accounting coverage",
        "",
        *_table(
            ("field", "value"),
            [
                ("candidate records", coverage["candidate_count"]),
                ("accepted records", coverage["accepted_count"]),
                ("accepted material", coverage["accepted_by_kind"].get("material")),
                ("accepted energy", coverage["accepted_by_kind"].get("energy")),
                ("rejected records", coverage["rejected_count"]),
                *[
                    (f"rejected: {reason}", count)
                    for reason, count in coverage["rejected_by_reason"].items()
                ],
                ("coverage", coverage["coverage_value"]),
                ("coverage formula", coverage["coverage_formula"]),
            ],
        ),
        "",
        "## Carbon totals",
        "",
        "The two perspectives are reported separately because they are not one total",
        "split in two. The product perspective sums what building objects can carry;",
        "the process perspective sums every recorded factory emission, including the",
        "records no object can carry.",
        "",
        *_table(
            ("quantity", "kgCO2e"),
            [
                ("A1 material carbon", totals["A1_material_kgCO2e"]),
                ("A3 process carbon attributed to objects", totals["A3_process_attributed_kgCO2e"]),
                ("product perspective total", totals["product_perspective_total_kgCO2e"]),
                ("process perspective, all recorded", totals["process_perspective_recorded_kgCO2e"]),
                (
                    f"of which unattributable ({totals['process_only_record_count']} whole-record, {totals['partial_unattributable_record_count']} partial-record)",
                    totals["process_only_unattributable_kgCO2e"],
                ),
            ],
        ),
        "",
        "## Material carbon by material",
        "",
        *_table(
            ("material", "records", "quantity", "unit", "kgCO2e"),
            [
                (
                    row["material"],
                    row["record_count"],
                    row["quantity"],
                    row["quantity_unit"],
                    row["kgCO2e"],
                )
                for row in facts["material_breakdown"]
            ],
        ),
        "",
        "## Process carbon by energy carrier",
        "",
        *_table(
            ("carrier", "records", "quantity", "unit", "kgCO2e"),
            [
                (
                    row["carrier"],
                    row["record_count"],
                    row["quantity"],
                    row["quantity_unit"],
                    row["kgCO2e"],
                )
                for row in facts["energy_breakdown"]["by_carrier"]
            ],
        ),
        "",
        "## Process carbon by production stage",
        "",
        *_table(
            ("production stage", "records", "kgCO2e"),
            [
                (row["stage"], row["record_count"], row["kgCO2e"])
                for row in facts["energy_breakdown"]["by_stage"]
            ],
        ),
        "",
        "## Emission factors applied",
        "",
        *_table(
            ("keyword", "value", "unit", "system boundary", "geography", "year", "proxy", "source"),
            [
                (
                    row["keyword"],
                    row["value"],
                    row["unit"],
                    row["system_boundary"],
                    row["geography"],
                    row["year"],
                    "yes" if row["is_proxy"] else "no",
                    row["source"],
                )
                for row in facts["emission_factors"]
            ],
        ),
        "",
        "## Case context supplied by the authors",
        "",
        "These describe the real project and the reporting convention. Unlike every",
        "table above, they are not derived from the release and carry no",
        "reproducibility guarantee, so they are kept separate.",
        "",
        *_table(
            ["field", "value"],
            [(row[0], row[1]) for row in facts["case_context"]],
        ),
        "",
        "## Open items",
        "",
        *[f"- {item}" for item in facts["open_items"]],
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--allow-synthetic", action="store_true", default=True)
    parser.add_argument(
        "--construction-only",
        action="store_true",
        help="read an evidence-free actual-case release and emit structural facts only",
    )
    args = parser.parse_args(argv)

    context = load_canonical_v2_context(
        args.release,
        allow_synthetic=args.allow_synthetic,
        allow_unready_backbone=args.construction_only,
    )
    facts = build_construction_facts(context) if args.construction_only else build_facts(context)

    output_dir = args.output_root / str(facts["release"]["release_id"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "case_study_facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    (output_dir / "case_study_facts.md").write_text(
        (
            render_construction_markdown(facts)
            if args.construction_only
            else render_markdown(facts)
        ),
        encoding="utf-8"
    )
    print(str(output_dir))


if __name__ == "__main__":
    main()
