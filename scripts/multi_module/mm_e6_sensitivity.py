# -*- coding: utf-8 -*-
"""E6: scenario and data-gap sensitivity for the three module accounts.

S0 baseline (selected A1–A3 factors, column D of the factor workbook)
S1 original database factors (column C; CO2-only rows are not comparable and are held at S0)
S2 ±10% on each of the three largest material categories, one at a time
S3 factory electricity factor: Guangdong lifecycle proxy 0.5168 -> national lifecycle 0.6205 / Guangdong CO2-only 0.4419
S4 diesel: lifecycle 3.2835 -> combustion-only 2.6594 kgCO2e/L
Rejected-record upper bound: conservative mass bound for every record the pipeline rejected.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import (  # noqa: E402
    MODULES, OUT_ROOT, ROOT, factor_row_of, load_context, load_factor_rows, load_json, markdown_table, write_json, write_text,
)

E6_DIR = OUT_ROOT / "E6"
ELEC_BASE, ELEC_NATIONAL, ELEC_CO2ONLY = 0.5168, 0.6205, 0.4419
DIESEL_BASE, DIESEL_COMBUSTION = 3.2834617369127512, 2.6593717369127514
CO2_ONLY_ORIGINAL = {"Calcium Silicate", "Fibre Cement"}
DEFAULT_BOARD_THICKNESS_MM = 25.0


def carrier_names(spec):
    kg = load_json(spec.release_dir / "multigranular_carbon_kg.json")
    return {n["id"]: str(n["props"].get("name") or n["id"]) for n in kg["nodes"] if "EnergyCarrier" in n["labels"]}


def module_facts(spec, factor_rows):
    ctx = load_context(spec)
    carriers = carrier_names(spec)
    projected = defaultdict(float)
    for c in ctx.product_contributions:
        if c.mode != "material":
            projected[c.emission_id] += c.projected_value
    mats, energies = [], []
    for e in ctx.emissions:
        row = factor_row_of(e.factor_source_id, factor_rows) or {}
        if e.kind == "material":
            mats.append({"value": e.emission_value, "category": row.get("category", "?"), "subtype": row.get("subtype", ""),
                         "selected": row.get("selected_factor", e.factor_value), "original": row.get("original_factor")})
        else:
            carrier = carriers.get(e.carrier_id, e.quantity_unit).casefold()
            energies.append({"value": e.emission_value, "projected": projected.get(e.emission_id, 0.0),
                             "carrier": "electricity" if "elec" in carrier or e.quantity_unit == "kWh" else "diesel",
                             "factor": e.factor_value})
    return mats, energies


def totals(mats, energies, mat_scale=None, energy_scale=None):
    mat_scale = mat_scale or (lambda m: 1.0)
    energy_scale = energy_scale or (lambda e: 1.0)
    material = sum(m["value"] * mat_scale(m) for m in mats)
    by_cat = Counter()
    for m in mats:
        by_cat[m["category"]] += m["value"] * mat_scale(m)
    attributed = sum(e["projected"] * energy_scale(e) for e in energies)
    process = sum(e["value"] * energy_scale(e) for e in energies)
    return {"material": material, "attributed_energy": attributed, "product": material + attributed, "process": process,
            "top3": [k for k, _ in by_cat.most_common(3)], "by_category": dict(by_cat)}


GLASS_THICKNESS_M = 0.006


def component_quantities(spec, global_ids: set[str]) -> dict[str, dict]:
    """IFC base quantities of the rejected components: net/gross volume (m3), area (m2), opening height x width (m2)."""
    import ifcopenshell

    model = ifcopenshell.open(str(spec.ifc))
    out: dict[str, dict] = {}
    for gid in global_ids:
        try:
            el = model.by_guid(gid)
        except Exception:
            continue
        q: dict = {}
        for rel in getattr(el, "IsDefinedBy", []) or []:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            pdef = rel.RelatingPropertyDefinition
            if pdef is None or not pdef.is_a("IfcElementQuantity"):
                continue
            for item in pdef.Quantities or []:
                if item.is_a("IfcQuantityVolume"):
                    q[item.Name] = float(item.VolumeValue)
                elif item.is_a("IfcQuantityArea"):
                    q[item.Name] = float(item.AreaValue)
                elif item.is_a("IfcQuantityLength"):
                    q[item.Name] = float(item.LengthValue)
        volume = q.get("NetVolume") or q.get("GrossVolume")
        area = q.get("Area") or q.get("NetArea") or q.get("GrossArea")
        opening = None
        if area is not None and el.is_a() in ("IfcWindow", "IfcDoor"):
            opening = area
        elif q.get("Height") and q.get("Width") and el.is_a() in ("IfcWindow", "IfcDoor"):
            opening = q["Height"] * q["Width"] / 1e6
        out[gid] = {"volume_m3": volume, "area_m2": area, "opening_m2": opening, "class": el.is_a(), "name": el.Name}
    return out


def rejected_upper_bound(spec, factor_rows):
    """One conservative bound per (component, factor row) among the records the pipeline rejected."""
    ev = load_json(spec.evidence_json)
    kg = load_json(spec.release_dir / "multigranular_carbon_kg.json")
    gid_of = {n["id"]: n["props"].get("globalId") for n in kg["nodes"] if "BuildingComponent" in n["labels"]}
    complete_mass = defaultdict(list)
    complete_thickness = defaultdict(list)
    incomplete = []
    for rec in ev["records"]:
        ops = {o["role"]: o["value"] for o in rec["operands"]}
        row = factor_row_of(rec["factorSourceRowId"], factor_rows) or {}
        key = (row.get("category"), row.get("subtype"))
        vol, area, thick, dens = ops.get("material_volume"), ops.get("material_area"), ops.get("thickness"), ops.get("density")
        if rec["formulaCode"] == "volume_density" and vol is not None and dens is not None:
            complete_mass[key].append(vol * dens)
        elif rec["formulaCode"] == "area_thickness_density" and None not in (area, thick, dens):
            complete_mass[key].append(area * thick / 1000 * dens)
            complete_thickness[key].append(thick)
        else:
            incomplete.append((rec, key, area, dens, row.get("selected_factor")))
    rejected_gids = {gid_of.get(rec["componentId"]) for rec, *_ in incomplete if gid_of.get(rec["componentId"])}
    quantities = component_quantities(spec, rejected_gids) if rejected_gids else {}
    groups: dict[tuple[str, tuple], list] = defaultdict(list)
    for item in incomplete:
        rec, key = item[0], item[1]
        groups[(rec["componentId"], key)].append(item)
    bound_total = 0.0
    detail = []
    for (component_id, key), items in groups.items():
        rec, _, area, dens, factor = items[0]
        area = max((i[2] for i in items if i[2] is not None), default=None)
        dens = max((i[3] for i in items if i[3] is not None), default=None)
        gid = gid_of.get(component_id, "")
        q = quantities.get(gid, {})
        if factor is None or dens is None:
            method, mass = "no factor/density: not bounded", 0.0
        elif key[0] == "Glass" and q.get("opening_m2"):
            mass = q["opening_m2"] * GLASS_THICKNESS_M * dens
            method = f"window opening {q['opening_m2']:.2f} m2 x 6 mm glass x density (one pane per opening)"
        elif q.get("volume_m3"):
            mass = q["volume_m3"] * dens
            method = f"component NetVolume {q['volume_m3']:.4f} m3 x density (whole element as this material)"
        elif area is not None:
            t = max(complete_thickness.get(key) or [DEFAULT_BOARD_THICKNESS_MM])
            mass = area * t / 1000 * dens
            method = f"area x max accepted thickness of same factor row ({t:g} mm) x density"
        elif complete_mass.get(key):
            mass = max(complete_mass[key])
            method = "max accepted mass of same factor row"
        else:
            mass = 0.0
            method = "no comparable accepted record: not bounded"
        carbon = mass * (factor or 0.0)
        bound_total += carbon
        detail.append({"component": gid or component_id[:30], "records": len(items), "factor_row": " / ".join(str(k) for k in key),
                       "bound_mass_kg": mass, "bound_kgCO2e": carbon, "method": method})
    return {"rejected_records": len(incomplete), "rejected_component_groups": len(groups), "upper_bound_kgCO2e": bound_total, "detail": detail}


def main() -> None:
    factor_rows = load_factor_rows()
    results = {}
    for spec in MODULES:
        mats, energies = module_facts(spec, factor_rows)
        base = totals(mats, energies)
        scenarios = {"S0 baseline": base}
        scenarios["S1 original database factors"] = totals(
            mats, energies,
            mat_scale=lambda m: (m["original"] / m["selected"]) if (m["original"] and m["selected"] and m["category"] not in CO2_ONLY_ORIGINAL) else 1.0)
        for cat in base["top3"]:
            for sign, label in ((1.10, "+10%"), (0.90, "-10%")):
                scenarios[f"S2 {cat} {label}"] = totals(mats, energies, mat_scale=lambda m, c=cat, s=sign: s if m["category"] == c else 1.0)
        scenarios["S3 electricity national lifecycle 0.6205"] = totals(
            mats, energies, energy_scale=lambda e: ELEC_NATIONAL / ELEC_BASE if e["carrier"] == "electricity" else 1.0)
        scenarios["S3 electricity Guangdong CO2-only 0.4419"] = totals(
            mats, energies, energy_scale=lambda e: ELEC_CO2ONLY / ELEC_BASE if e["carrier"] == "electricity" else 1.0)
        scenarios["S4 diesel combustion-only 2.6594"] = totals(
            mats, energies, energy_scale=lambda e: DIESEL_COMBUSTION / DIESEL_BASE if e["carrier"] == "diesel" else 1.0)
        rej = rejected_upper_bound(spec, factor_rows)
        results[spec.label] = {"scenarios": scenarios, "rejected_bound": rej,
                               "co2_only_rows_held_at_baseline": sorted({m["category"] for m in mats if m["category"] in CO2_ONLY_ORIGINAL})}

    labels = [s.label for s in MODULES]
    scenario_names = list(results[labels[0]]["scenarios"].keys())
    # module ranking by product total under each scenario present in all modules
    common = [n for n in scenario_names if all(n in results[l]["scenarios"] for l in labels)]
    rows = []
    for n in common:
        vals = [results[l]["scenarios"][n]["product"] for l in labels]
        base_vals = [results[l]["scenarios"]["S0 baseline"]["product"] for l in labels]
        ranking = " > ".join(l.replace("Type ", "") for l, _ in sorted(zip(labels, vals), key=lambda kv: -kv[1]))
        rows.append([n] + [f"{v:,.1f} ({100 * (v / b - 1):+.1f}%)" for v, b in zip(vals, base_vals)] + [ranking])
    md = ["# E6 Sensitivity and data-gap analysis", "",
          "Product-perspective total per module under each scenario (change against S0 in brackets) and the resulting module ranking.", "",
          markdown_table(["Scenario"] + labels + ["Ranking"], rows), ""]
    md.append("S1 rescales each material record by original/selected factor where the original row is CO2e-comparable; Calcium Silicate and Fibre Cement originals are CO2-only and stay at S0.")
    md += ["", "## Process perspective under energy-factor scenarios (kgCO2e)", ""]
    prow = []
    for n in ("S0 baseline", "S3 electricity national lifecycle 0.6205", "S3 electricity Guangdong CO2-only 0.4419", "S4 diesel combustion-only 2.6594"):
        prow.append([n] + [f"{results[l]['scenarios'][n]['process']:,.1f}" for l in labels] + [f"{results[l]['scenarios'][n]['attributed_energy']:,.1f}" for l in labels])
    md.append(markdown_table(["Scenario"] + [f"{l} process" for l in labels] + [f"{l} attributed" for l in labels], prow))
    md += ["", "## Top-3 material categories per scenario", ""]
    trow = []
    for l in labels:
        for n in results[l]["scenarios"]:
            if n.startswith("S2") and l.replace("Type ", "") not in n:
                pass
            trow.append([l, n, " > ".join(results[l]["scenarios"][n]["top3"]), "yes" if results[l]["scenarios"][n]["top3"] == results[l]["scenarios"]["S0 baseline"]["top3"] else "no"])
    md.append(markdown_table(["Module", "Scenario", "Top-3 categories", "Same order as S0"], trow))
    md += ["", "## Rejected material records: conservative upper bound", ""]
    rrow = []
    for l in labels:
        r = results[l]["rejected_bound"]
        mat = results[l]["scenarios"]["S0 baseline"]["material"]
        rrow.append([l, r["rejected_records"], r["rejected_component_groups"], f"{r['upper_bound_kgCO2e']:,.1f}", f"{100 * r['upper_bound_kgCO2e'] / mat:.2f}%"])
    md.append(markdown_table(["Module", "Rejected records", "Component × material groups", "Upper bound (kgCO2e)", "Share of material total"], rrow))
    md.append("")
    md.append("Bound per component and material: glass records take the full window opening (IFC base quantity Area) as one 6 mm pane; other records take the whole component NetVolume as the rejected material, or, without a volume, the area × the largest accepted thickness of the same factor row. Several rejected records on one component describe one layer and are bounded once. Each choice overstates the omitted mass.")
    for l in labels:
        md.append("")
        md.append(f"### {l} rejected records")
        md.append(markdown_table(["Component", "Records", "Factor row", "Bound mass (kg)", "Bound kgCO2e", "Method"],
                                 [[d["component"], d["records"], d["factor_row"], d["bound_mass_kg"], d["bound_kgCO2e"], d["method"]] for d in results[l]["rejected_bound"]["detail"]]))
    alloc = ROOT / "outputs/research_experiments/allocation_sensitivity_report.json"
    md += ["", "## Allocation basis", ""]
    if alloc.exists():
        a = load_json(alloc)
        md.append(f"Existing E2b report `{alloc.relative_to(ROOT)}` is reused (keys: {', '.join(list(a.keys())[:8])}). It varies the allocation basis of shared factory energy (mass, duration, machine-hours) on the four-module factory dataset; the 16.67% flip rate reported there refers to allocated production-energy carbon at the A5 boundary.")
    else:
        md.append("E2b allocation-basis report not found; reuse the published numbers.")
    write_text(E6_DIR / "e6_sensitivity.md", "\n".join(md) + "\n")
    write_json(E6_DIR / "e6_sensitivity.json", results)
    print("written:", E6_DIR / "e6_sensitivity.md")


if __name__ == "__main__":
    main()
