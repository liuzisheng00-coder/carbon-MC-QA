# -*- coding: utf-8 -*-
"""E1: per-module graph construction and quality table (Type A / B / D).

Reads the canonical release statistics, the alignment report and the material
evidence report of each module, times a fresh release build for B and D, and
checks which ontology classes and relations each graph uses.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import MODULES, ONTOLOGY, OUT_ROOT, ROOT, load_json, markdown_table, write_json, write_text  # noqa: E402

E1_DIR = OUT_ROOT / "E1"


def ontology_vocabulary(ttl: Path) -> tuple[set[str], set[str]]:
    """Instantiable application classes and object properties declared in the TBox."""
    text = ttl.read_text(encoding="utf-8")
    classes: set[str] = set()
    props: set[str] = set()
    for block in re.split(r"\n\s*\n", text):
        m = re.match(r"^\s*(?:bim|mc|carbon|integr):([A-Za-z][A-Za-z0-9]*)\s*\n", block)
        if not m:
            continue
        local = m.group(1)
        if "owl:Class" in block and "integr:instantiable true" in block:
            classes.add(local)
        elif "owl:ObjectProperty" in block:
            props.add(local)
    return classes, props


def time_release_build(spec, out_root: Path) -> float | None:
    """Re-run the release script into a scratch folder and return wall-clock seconds."""
    if not spec.case_config.exists():
        return None
    release_id = f"timing_{spec.key}_{int(time.time())}"
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "dm2c_m23_canonical_release.py", "--case-config", str(spec.case_config),
         "--out-root", str(out_root), "--release-id", release_id],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        return None
    return elapsed


def main() -> None:
    classes, props = ontology_vocabulary(ONTOLOGY)
    rows = []
    detail = {}
    scratch = OUT_ROOT / "E1" / "timing_builds"
    for spec in MODULES:
        stats = load_json(spec.release_dir / "multigranular_carbon_kg_stats.json")
        align = load_json(spec.release_dir / "m2_alignment_report.json")
        report = load_json(spec.evidence_report)
        acc = stats["applicationClassCounts"]
        rel = stats["relationCounts"]
        val = stats["validation"]
        used_classes = set(acc)
        used_rels = set(rel)
        build_s = time_release_build(spec, scratch)
        graph_bytes = (spec.release_dir / "multigranular_carbon_kg.json").stat().st_size
        row = {
            "module": spec.label,
            "ifc_components_candidate": val.get("candidateCount"),
            "building_components": acc.get("BuildingComponent"),
            "component_types": acc.get("ComponentType"),
            "ifc_materials": acc.get("IfcMaterial"),
            "design_quantities": acc.get("DesignQuantity"),
            "material_consumptions": acc.get("MaterialConsumption"),
            "energy_consumptions": acc.get("EnergyConsumption"),
            "carbon_emissions": acc.get("CarbonEmission"),
            "nodes": stats["nodeCount"],
            "edges": stats["edgeCount"],
            "accepted_records": val["acceptedCount"],
            "rejected_records": val["rejectedCount"],
            "coverage": round(val["coverageValue"], 4),
            "rejected_by_reason": dict(val.get("rejectedByReason", {})),
            "evidence_accepted": report["counts"]["acceptedRecords"],
            "evidence_rejected": report["counts"]["rejectedRecords"],
            "evidence_skipped": report["counts"]["skippedRecords"],
            "evidence_skipped_counts": dict(report.get("skippedCounts", {})),
            "attribution_counts": dict(stats.get("attributionCounts", {})),
            "alignment_status": align.get("status"),
            "gates": dict(align.get("gates", {})),
            "high_severity_violations": align.get("highSeverityViolationCount"),
            "ontology_classes_used": sorted(used_classes & classes),
            "ontology_classes_unused": sorted(classes - used_classes),
            "ontology_relations_used": sorted(used_rels & props),
            "ontology_relations_unused": sorted(props - used_rels),
            "graph_json_mb": round(graph_bytes / 1e6, 2),
            "build_seconds": None if build_s is None else round(build_s, 1),
            "release_dir": str(spec.release_dir.relative_to(ROOT)),
        }
        rows.append(row)
        detail[spec.key] = {"class_counts": acc, "relation_counts": rel, "validation": val, "alignment": align}

    # cross-module vocabulary consistency
    class_sets = [set(r["ontology_classes_used"]) for r in rows]
    rel_sets = [set(r["ontology_relations_used"]) for r in rows]
    summary = {
        "ontology_class_count": len(classes),
        "ontology_relation_count": len(props),
        "classes_used_by_all": sorted(set.intersection(*class_sets)),
        "classes_used_by_some": sorted(set.union(*class_sets) - set.intersection(*class_sets)),
        "relations_used_by_all": sorted(set.intersection(*rel_sets)),
        "relations_used_by_some": sorted(set.union(*rel_sets) - set.intersection(*rel_sets)),
    }
    write_json(E1_DIR / "e1_graph_quality.json", {"rows": rows, "summary": summary, "detail": detail})

    flat_keys = [k for k in rows[0] if not isinstance(rows[0][k], (dict, list))]
    with (E1_DIR / "e1_graph_quality.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=flat_keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in flat_keys})

    headers = ["Metric"] + [r["module"] for r in rows]
    metrics = [
        ("Calculation candidates (material + energy records)", "ifc_components_candidate"),
        ("BuildingComponent nodes", "building_components"),
        ("ComponentType nodes", "component_types"),
        ("IfcMaterial nodes", "ifc_materials"),
        ("DesignQuantity nodes", "design_quantities"),
        ("MaterialConsumption nodes", "material_consumptions"),
        ("EnergyConsumption nodes", "energy_consumptions"),
        ("CarbonEmission nodes", "carbon_emissions"),
        ("Nodes", "nodes"),
        ("Edges", "edges"),
        ("Accepted calculation records", "accepted_records"),
        ("Rejected calculation records", "rejected_records"),
        ("Coverage (accepted / candidates)", "coverage"),
        ("Material evidence accepted / rejected / skipped", None),
        ("Attribution (direct / allocated / process-only)", None),
        ("Alignment gate", "alignment_status"),
        ("High-severity violations", "high_severity_violations"),
        ("Graph JSON size (MB)", "graph_json_mb"),
        ("Release build wall-clock (s)", "build_seconds"),
    ]
    table_rows = []
    for name, key in metrics:
        if key is None and name.startswith("Material evidence"):
            table_rows.append([name] + [f"{r['evidence_accepted']} / {r['evidence_rejected']} / {r['evidence_skipped']}" for r in rows])
        elif key is None:
            table_rows.append([name] + [
                f"{r['attribution_counts'].get('direct', 0)} / {r['attribution_counts'].get('allocated', 0)} / {r['attribution_counts'].get('process_only', 0)}"
                for r in rows])
        else:
            table_rows.append([name] + [r[key] if r[key] is not None else "n/a" for r in rows])
    md = ["# E1 Graph construction and quality by module type", "",
          markdown_table(headers, table_rows), "",
          "## Rejected records by reason", ""]
    for r in rows:
        md.append(f"- {r['module']}: {r['rejected_by_reason']}; evidence skips {r['evidence_skipped_counts']}")
    md += ["", "## Ontology vocabulary coverage", "",
           f"TBox: {summary['ontology_class_count']} classes, {summary['ontology_relation_count']} object properties.",
           f"Classes instantiated by all three graphs ({len(summary['classes_used_by_all'])}): {', '.join(summary['classes_used_by_all'])}.",
           f"Classes instantiated by some graphs only: {', '.join(summary['classes_used_by_some']) or 'none'}.",
           f"Relations used by all three graphs ({len(summary['relations_used_by_all'])}): {', '.join(summary['relations_used_by_all'])}.",
           f"Relations used by some graphs only: {', '.join(summary['relations_used_by_some']) or 'none'}.",
           "", "Build wall-clock is measured by re-running `dm2c_m23_canonical_release.py` on this machine into a scratch folder; it covers IFC parsing, evidence assembly, graph construction, validation and the alignment audit."]
    write_text(E1_DIR / "e1_graph_quality.md", "\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
