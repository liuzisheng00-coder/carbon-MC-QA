# -*- coding: utf-8 -*-
"""Derive mass-scaled factory energy inputs for Type B and Type D.

The factory dataset carries a module-level finishing log and mass-share allocation rows
only for the measured Type A production run (`Main_Modularization_Model`). For Type B and
Type D the same production route is assumed and the module-level records are scaled by the
ratio of module material mass (accepted material records of each module) to the Type A
mass. Component-level logs mapped by type mark stay as they are. Allocation fractions of
every affected record are renormalised so that they still sum to one.

Outputs: outputs/research_experiments/factory_data_proxy_20260928/factory_data_consolidated_<key>_massproxy.json
and inputs/case_study/m23_canonical_v2_case_<key>_aligned_massproxy.json.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import FACTORY_INPUT, ROOT, load_json  # noqa: E402

SOURCE_IDENTITY_A = "Main_Modularization_Model"
PROXY_DIR = ROOT / "outputs/research_experiments/factory_data_proxy_20260928"
TARGETS = {
    "typeb": {"identity": "TypeB", "evidence_report": ROOT / "inputs/case_study/m23_typeb_aligned_material_evidence_report.json",
              "base_case": ROOT / "inputs/case_study/m23_canonical_v2_case_typeb_aligned.json"},
    "typed": {"identity": "TypeD", "evidence_report": ROOT / "inputs/case_study/m23_typed_aligned_material_evidence_report.json",
              "base_case": ROOT / "inputs/case_study/m23_canonical_v2_case_typed_aligned.json"},
}
REPORT_A = ROOT / "inputs/case_study/m23_typea1_en_material_evidence_report.json"


def module_mass(report: Path) -> float:
    return sum(v["massKg"] for v in load_json(report)["materialBreakdown"].values())


def derive(payload: dict, identity: str, ratio: float) -> dict:
    out = copy.deepcopy(payload)
    scaled_energy = []
    for row in out["p0_energy_records"]:
        if row.get("target_id") == SOURCE_IDENTITY_A and row.get("target_level") == "module":
            row["target_id"] = identity
            original = float(row["quantity_value"])
            row["quantity_value"] = f"{original * ratio:.6f}"
            row["proxy_note"] = f"Type A module log {original} scaled by mass ratio {ratio:.4f}"
            scaled_energy.append(row["record_id"])
    groups = defaultdict(list)
    for row in out["p0_allocation_rows"]:
        groups[(row["record_id"], row.get("allocation_key") or row.get("allocation_basis"))].append(row)
    scaled_alloc = 0
    for rows in groups.values():
        touched = False
        for row in rows:
            if row.get("target_id") == SOURCE_IDENTITY_A and row.get("target_level") == "module":
                row["target_id"] = identity
                row["allocation_value"] = f"{float(row['allocation_value']) * ratio:.4f}"
                row["proxy_note"] = f"Type A module row scaled by mass ratio {ratio:.4f}"
                touched = True
                scaled_alloc += 1
        if touched:
            total = sum(float(r["allocation_value"]) for r in rows)
            for r in rows:
                r["allocation_fraction"] = f"{float(r['allocation_value']) / total:.10f}"
    out["derivation"] = {
        "derived_from": str(FACTORY_INPUT.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(FACTORY_INPUT.read_bytes()).hexdigest().upper(),
        "module_identity": identity,
        "mass_ratio_to_type_a": ratio,
        "rule": "Type A module-level energy log and module allocation rows retargeted to this module and scaled by module material mass; fractions renormalised per record and basis; component-level logs unchanged",
        "scaled_energy_records": scaled_energy,
        "scaled_allocation_rows": scaled_alloc,
    }
    return out


def main() -> None:
    payload = load_json(FACTORY_INPUT)
    mass_a = module_mass(REPORT_A)
    PROXY_DIR.mkdir(parents=True, exist_ok=True)
    for key, spec in TARGETS.items():
        mass = module_mass(spec["evidence_report"])
        ratio = mass / mass_a
        derived = derive(payload, spec["identity"], ratio)
        out_path = PROXY_DIR / f"factory_data_consolidated_{key}_massproxy.json"
        out_path.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
        data = out_path.read_bytes()
        case = load_json(spec["base_case"])
        rel = Path("..") / ".." / out_path.relative_to(ROOT)
        case["factoryInput"] = {"path": str(rel).replace("/", "\\"), "sha256": hashlib.sha256(data).hexdigest().upper(), "sizeBytes": len(data)}
        case["module"]["name"] = case["module"]["name"].replace(")", "; mass-scaled factory proxy)")
        case["generatedAtUtc"] = "2026-09-28T07:00:00Z"
        case_path = spec["base_case"].with_name(spec["base_case"].name.replace("_aligned.json", "_aligned_massproxy.json"))
        case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{key}: mass {mass:.1f} kg vs Type A {mass_a:.1f} kg, ratio {ratio:.4f}; "
              f"scaled energy {derived['derivation']['scaled_energy_records']}, allocation rows {derived['derivation']['scaled_allocation_rows']}; "
              f"wrote {out_path.name}, {case_path.name}")


if __name__ == "__main__":
    main()
