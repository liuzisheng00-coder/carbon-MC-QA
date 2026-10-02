# -*- coding: utf-8 -*-
"""E2: per-module carbon accounts, reconciliation identities, independent recalculation,
building-scale aggregation template and factory-meter reconciliation (Type A / B / D)."""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import (  # noqa: E402
    FACTORY_INPUT, MODULES, OUT_ROOT, PROXY_MODULES, ROOT, factor_row_of, load_context, load_factor_rows, load_json,
    markdown_table, write_json, write_text,
)

E2_DIR = OUT_ROOT / "E2"
MM_PER_M = 0.001


def graph_lookup(spec):
    kg = load_json(spec.release_dir / "multigranular_carbon_kg.json")
    nodes = {n["id"]: n for n in kg["nodes"]}
    stage_of_activity: dict[str, str] = {}
    for e in kg["edges"]:
        if e["type"] == "hasActivity":
            stage_of_activity[e["tgt"]] = e["src"]
    return nodes, stage_of_activity


def name_of(nodes, node_id: str) -> str:
    props = nodes.get(node_id, {}).get("props", {})
    return str(props.get("name") or props.get("activityName") or props.get("stageName") or node_id)


def module_account(spec, factor_rows):
    ctx = load_context(spec)
    nodes, stage_of_activity = graph_lookup(spec)
    material_facts = [e for e in ctx.emissions if e.kind == "material"]
    energy_facts = [e for e in ctx.emissions if e.kind == "energy"]
    material_total = sum(e.emission_value for e in material_facts)

    # product-side energy (projected onto this module's components)
    projected_by_emission: dict[str, float] = defaultdict(float)
    for c in ctx.product_contributions:
        if c.mode != "material":
            projected_by_emission[c.emission_id] += c.projected_value
    direct_this = allocated_this = allocated_other = process_only = 0.0
    for e in energy_facts:
        proj = projected_by_emission.get(e.emission_id, 0.0)
        if e.mode == "direct":
            direct_this += proj
        elif e.mode == "allocated":
            allocated_this += proj
            allocated_other += e.emission_value - proj
        else:
            process_only += e.emission_value
    product_total = sum(c.projected_value for c in ctx.product_contributions)
    process_total = sum(e.emission_value for e in energy_facts)
    identity_product = material_total + direct_this + allocated_this
    identity_process = direct_this + allocated_this + allocated_other + process_only

    # material by factor category / subtype
    by_category: Counter = Counter()
    by_subtype: Counter = Counter()
    factor_mismatch = []
    for e in material_facts:
        row = factor_row_of(e.factor_source_id, factor_rows)
        cat = row["category"] if row else e.factor_keyword or "?"
        sub = f"{row['category']} / {row['subtype']}" if row else e.factor_keyword or "?"
        by_category[cat] += e.emission_value
        by_subtype[sub] += e.emission_value
        if row and row["selected_factor"] is not None and abs(row["selected_factor"] - e.factor_value) > 1e-9:
            factor_mismatch.append((e.emission_id, row["selected_factor"], e.factor_value))

    # energy by stage (process perspective) and by carrier
    by_stage: Counter = Counter()
    by_carrier: Counter = Counter()
    energy_qty_by_carrier: Counter = Counter()
    for e in energy_facts:
        stages = {name_of(nodes, stage_of_activity.get(p, p)) for p in e.process_ids} or {"(no activity)"}
        for s in stages:
            by_stage[s] += e.emission_value / len(stages)
        carrier = name_of(nodes, e.carrier_id) if e.carrier_id else e.quantity_unit
        by_carrier[carrier] += e.emission_value
        energy_qty_by_carrier[(carrier, e.quantity_unit)] += e.quantity_value

    # top components (product perspective)
    by_component: Counter = Counter()
    for c in ctx.product_contributions:
        by_component[c.component_id] += c.projected_value
    top_components = [(name_of(nodes, cid), v) for cid, v in by_component.most_common(5)]

    return {
        "module": spec.label,
        "components": len({c.component_id for c in ctx.product_contributions}),
        "material_records": len(material_facts),
        "energy_records": len(energy_facts),
        "material_total": material_total,
        "energy_direct_this_module": direct_this,
        "energy_allocated_this_module": allocated_this,
        "energy_allocated_other_modules": allocated_other,
        "energy_process_only": process_only,
        "product_total": product_total,
        "process_total": process_total,
        "identity_product_check": product_total - identity_product,
        "identity_process_check": process_total - identity_process,
        "unattributed_to_this_module": process_total - direct_this - allocated_this,
        "floor_area_m2": spec.floor_area_m2,
        "floor_area_basis": spec.floor_area_basis,
        "product_per_m2": product_total / spec.floor_area_m2,
        "material_per_m2": material_total / spec.floor_area_m2,
        "material_by_category": dict(by_category.most_common()),
        "material_by_subtype": dict(by_subtype.most_common()),
        "energy_by_stage": dict(by_stage.most_common()),
        "energy_by_carrier": dict(by_carrier.most_common()),
        "energy_quantity_by_carrier": {f"{k[0]} [{k[1]}]": v for k, v in energy_qty_by_carrier.items()},
        "top_components": top_components,
        "factor_value_mismatches": factor_mismatch,
    }


def independent_recalc(spec, factor_rows, graph_material_total: float):
    """Recompute every complete material record from its operands and the factor workbook,
    and write a formula-bearing workbook for hand audit."""
    import openpyxl
    from openpyxl.styles import Font

    ev = load_json(spec.evidence_json)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "recalc"
    headers = ["recordId", "componentId", "materialId", "formula", "volume_m3", "area_m2", "thickness_mm",
               "density_kg_m3", "mass_kg (formula)", "factor_kgCO2e_per_kg", "factor_row", "carbon_kgCO2e (formula)", "status"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    total_py = 0.0
    complete = incomplete = 0
    r = 2
    for rec in ev["records"]:
        ops = {o["role"]: o for o in rec["operands"]}
        vol = ops.get("material_volume", {}).get("value")
        area = ops.get("material_area", {}).get("value")
        thick = ops.get("thickness", {}).get("value")
        dens = ops.get("density", {}).get("value")
        row = factor_row_of(rec["factorSourceRowId"], factor_rows)
        factor = row["selected_factor"] if row else None
        formula = rec["formulaCode"]
        if formula == "volume_density":
            ok = vol is not None and dens is not None
            mass_formula = f"=E{r}*H{r}" if ok else None
            mass = vol * dens if ok else None
        else:
            ok = area is not None and thick is not None and dens is not None
            mass_formula = f"=F{r}*G{r}/1000*H{r}" if ok else None
            mass = area * thick * MM_PER_M * dens if ok else None
        status = "complete" if ok and factor is not None else "incomplete (rejected in pipeline)"
        carbon = mass * factor if (mass is not None and factor is not None) else None
        if carbon is not None:
            total_py += carbon
            complete += 1
        else:
            incomplete += 1
        ws.append([rec["recordId"], rec["componentId"], rec["materialId"], formula, vol, area, thick, dens,
                   mass_formula, factor, f"{row['category']} / {row['subtype']}" if row else "", 
                   f"=I{r}*J{r}" if mass_formula else None, status])
        r += 1
    ws.append([])
    ws.append(["TOTAL (Excel formula)", None, None, None, None, None, None, None, None, None, None, f"=SUM(L2:L{r-1})", ""])
    ws.append(["TOTAL (Python)", None, None, None, None, None, None, None, None, None, None, total_py, ""])
    ws.append(["Graph material total", None, None, None, None, None, None, None, None, None, None, graph_material_total, ""])
    ws.append(["Difference Python - graph", None, None, None, None, None, None, None, None, None, None, total_py - graph_material_total, ""])
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["K"].width = 40
    out = E2_DIR / f"e2_material_recalc_{spec.key}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"complete_records": complete, "incomplete_records": incomplete, "recalc_total": total_py,
            "graph_material_total": graph_material_total, "difference": total_py - graph_material_total,
            "workbook": str(out.relative_to(ROOT))}


def factory_reconciliation(accounts):
    d = load_json(FACTORY_INPUT)
    monthly = [(m["energy_metric"], m["scope_or_note"], m["numeric_value"], m["unit_guess"]) for m in d["monthly_energy"]]
    p0 = d["p0_energy_records"]
    p0_by_carrier: Counter = Counter()
    p0_by_role: Counter = Counter()
    for rec in p0:
        p0_by_carrier[(rec["energy_carrier"], rec["quantity_unit"])] += float(rec["quantity_value"])
        p0_by_role[rec["record_role"]] += 1
    return {
        "word_monthly_records": monthly,
        "p0_records": len(p0),
        "p0_quantity_by_carrier": {f"{k[0]} [{k[1]}]": v for k, v in p0_by_carrier.items()},
        "p0_records_by_role": dict(p0_by_role),
        "graph_energy_quantity_by_carrier": {a["module"]: a["energy_quantity_by_carrier"] for a in accounts},
        "graph_process_total_by_module": {a["module"]: a["process_total"] for a in accounts},
    }


def building_scale(accounts):
    counts_path = E2_DIR / "module_counts.json"
    template = {a["module"]: None for a in accounts}
    template["_note"] = "Fill the number of modules of each type in the building (873 in total) and re-run mm_e2_accounting.py"
    if not counts_path.exists():
        write_json(counts_path, template)
        counts = None
    else:
        raw = load_json(counts_path)
        counts = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, (int, float))}
        if len(counts) < len(accounts):
            counts = None
    lines = ["# E2 Building-scale aggregation", "",
             "Building account = Σ_t N_t × (material_t + direct_t + allocated_t) + shared factory energy over the production period.", "",
             "- material_t, direct_t, allocated_t are the type-level product-perspective terms below.",
             "- The shared factory meters (process-only and allocation shares of other modules) are metered per period, not per module; they enter once per period and must not be multiplied by N_t.",
             "- The compiled CarbonQL program for a building question equals the module program with N_t as a weight; graph size grows with the number of types, not modules.", ""]
    rows = [[a["module"], a["material_total"], a["energy_direct_this_module"], a["energy_allocated_this_module"], a["product_total"]] for a in accounts]
    lines.append(markdown_table(["Type", "material_t", "direct_t", "allocated_t", "product_t = sum"], rows))
    lines.append("")
    if counts is None:
        lines.append(f"Module counts per type are not yet available. Fill `{counts_path.relative_to(ROOT)}` (N_A, N_B, N_D) and re-run; the table below then reports N_t × product_t and the building sum.")
    else:
        total = 0.0
        rows2 = []
        for a in accounts:
            n = counts[a["module"]]
            v = n * a["product_total"]
            total += v
            rows2.append([a["module"], n, a["product_total"], v])
        lines.append(markdown_table(["Type", "N_t", "product_t", "N_t × product_t"], rows2))
        lines.append("")
        lines.append(f"Σ_t N_t × product_t = {total:,.1f} kgCO2e (plus shared factory energy for the period).")
    return "\n".join(lines) + "\n"


def main() -> None:
    factor_rows = load_factor_rows()
    accounts = []
    recalcs = {}
    for spec in MODULES:
        acc = module_account(spec, factor_rows)
        accounts.append(acc)
        recalcs[spec.key] = independent_recalc(spec, factor_rows, acc["material_total"])
        print(f"{spec.label}: product={acc['product_total']:.2f} material={acc['material_total']:.2f} "
              f"direct={acc['energy_direct_this_module']:.2f} alloc={acc['energy_allocated_this_module']:.2f} "
              f"process={acc['process_total']:.2f} recalc diff={recalcs[spec.key]['difference']:.6f}")
    recon = factory_reconciliation(accounts)
    write_json(E2_DIR / "e2_module_accounts.json", {"accounts": accounts, "recalculation": recalcs, "factory": recon})

    flat = [k for k in accounts[0] if not isinstance(accounts[0][k], (dict, list))]
    with (E2_DIR / "e2_module_accounts.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=flat)
        w.writeheader()
        for a in accounts:
            w.writerow({k: a[k] for k in flat})

    headers = ["Term (kgCO2e)"] + [a["module"] for a in accounts]
    rows = [
        ["Material (A1–A3)"] + [a["material_total"] for a in accounts],
        ["Factory energy, direct to this module"] + [a["energy_direct_this_module"] for a in accounts],
        ["Factory energy, allocated to this module (mass share)"] + [a["energy_allocated_this_module"] for a in accounts],
        ["Product perspective total"] + [a["product_total"] for a in accounts],
        ["Factory energy, allocated to other modules"] + [a["energy_allocated_other_modules"] for a in accounts],
        ["Factory energy, process-only (no product target)"] + [a["energy_process_only"] for a in accounts],
        ["Process perspective total (all factory records)"] + [a["process_total"] for a in accounts],
        ["Not attributed to this module = process − direct − allocated"] + [a["unattributed_to_this_module"] for a in accounts],
        ["Identity check: product − (material + direct + allocated)"] + [a["identity_product_check"] for a in accounts],
        ["Identity check: process − (direct + alloc_this + alloc_other + process_only)"] + [a["identity_process_check"] for a in accounts],
        ["Floor area basis (m²)"] + [a["floor_area_m2"] for a in accounts],
        ["Product total per m²"] + [a["product_per_m2"] for a in accounts],
        ["Material per m²"] + [a["material_per_m2"] for a in accounts],
    ]
    md = ["# E2 Module carbon accounts and reconciliation", "", markdown_table(headers, rows), ""]
    md.append("Floor-area basis: " + "; ".join(f"{a['module']}: {a['floor_area_basis']}" for a in accounts) + ".")
    md += ["", "## Top material categories (kgCO2e)", ""]
    for a in accounts:
        top = list(a["material_by_category"].items())[:3]
        md.append(f"- {a['module']}: " + "; ".join(f"{k} {v:,.1f}" for k, v in top))
    md += ["", "## Material by factor sub-type (kgCO2e)", ""]
    subtypes = sorted({k for a in accounts for k in a["material_by_subtype"]})
    md.append(markdown_table(["Factor row"] + [a["module"] for a in accounts],
                             [[s] + [a["material_by_subtype"].get(s, 0.0) for a in accounts] for s in subtypes]))
    md += ["", "## Factory energy by production stage (process perspective, kgCO2e)", ""]
    stages = sorted({k for a in accounts for k in a["energy_by_stage"]})
    md.append(markdown_table(["Stage"] + [a["module"] for a in accounts],
                             [[s] + [a["energy_by_stage"].get(s, 0.0) for a in accounts] for s in stages]))
    md += ["", "## Independent recalculation of material records", ""]
    md.append(markdown_table(["Module", "Complete records", "Incomplete (rejected)", "Recalculated total", "Graph total", "Difference"],
                             [[MODULES[i].label, r["complete_records"], r["incomplete_records"], r["recalc_total"], r["graph_material_total"], r["difference"]]
                              for i, r in enumerate(recalcs.values())]))
    md.append("")
    md.append("Each record is recomputed as volume × density × factor or area × thickness × density × factor from the operands stored in the material evidence file and the selected factor column of the factor workbook, outside the graph builder. The workbooks `e2_material_recalc_<module>.xlsx` carry the formulas.")
    md += ["", "## Factory meter reconciliation", ""]
    md.append("Word-reported monthly totals: " + "; ".join(f"{m[0]} {m[2]:,.0f} {m[3]} ({m[1]})" for m in recon["word_monthly_records"]) + ".")
    md.append("P0 energy records in the graph input: " + ", ".join(f"{k} {v:,.1f}" for k, v in recon["p0_quantity_by_carrier"].items()) + f" across {recon['p0_records']} records ({recon['p0_records_by_role']}).")
    md.append("Graph energy quantities per module: " + "; ".join(f"{m}: " + ", ".join(f"{k} {v:,.1f}" for k, v in q.items()) for m, q in recon["graph_energy_quantity_by_carrier"].items()) + ".")
    md.append("")
    md.append("The process perspective total is identical for the three modules because it sums the same factory records once. Only the direct and allocated terms depend on the module. The factory records are a measured-total-calibrated disaggregation of the reported monthly meters; the disaggregation below line level is synthetic and is labelled as such in the graph (`dataProvenance = synthetic`).")
    md += ["", "## Top product-perspective components", ""]
    for a in accounts:
        md.append(f"- {a['module']}: " + "; ".join(f"{n} {v:,.1f}" for n, v in a["top_components"][:3]))
    mismatches = {a["module"]: a["factor_value_mismatches"] for a in accounts if a["factor_value_mismatches"]}
    md += ["", f"Factor value mismatches between graph and workbook: {mismatches if mismatches else 'none'}."]

    # process-perspective variants for B and D
    proxy_accounts = [module_account(spec, factor_rows) for spec in PROXY_MODULES if spec.release_dir.exists()]
    if proxy_accounts:
        cols = accounts + proxy_accounts
        md += ["", "## Process attribution variants for Type B and Type D", "",
               "The factory dataset carries a module-level finishing log and mass-share allocation rows only for the measured Type A production run "
               "(`Main_Modularization_Model`). The main Type B and Type D accounts above use a **mass-scaled proxy**: the Type A module log and module "
               "allocation rows are retargeted to the module and scaled by the ratio of module material mass to the Type A mass (B 0.934, D 1.107), with "
               "allocation fractions renormalised. Two bounding variants are listed: **as recorded**, where the module keeps its own factory identity and "
               "receives only the four component-level logs mapped by type mark, and **equal route**, where the Type A module records apply unscaled. "
               "Material terms are identical across variants.", ""]
        md.append(markdown_table(["Term (kgCO2e)"] + [a["module"] for a in cols], [
            ["Material (A1–A3)"] + [a["material_total"] for a in cols],
            ["Factory energy, direct to this module"] + [a["energy_direct_this_module"] for a in cols],
            ["Factory energy, allocated to this module"] + [a["energy_allocated_this_module"] for a in cols],
            ["Product perspective total"] + [a["product_total"] for a in cols],
            ["Not attributed to this module"] + [a["unattributed_to_this_module"] for a in cols],
            ["Product total per m²"] + [a["product_per_m2"] for a in cols],
        ]))
        write_json(E2_DIR / "e2_process_variant_accounts.json", proxy_accounts)
    write_text(E2_DIR / "e2_module_accounts.md", "\n".join(md) + "\n")
    write_text(E2_DIR / "e2_building_scale.md", building_scale(accounts))
    print("written:", E2_DIR / "e2_module_accounts.md")


if __name__ == "__main__":
    main()
