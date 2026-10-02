# -*- coding: utf-8 -*-
"""Manuscript text for reviewer comments 1 to 4 (XL10 single case, XL11 allocation and
reconciliation, XL12 uncertainty, XL13 scalability), assembled from the multi-module
experiment outputs.

One source of text for three renderings: the stand-alone additions document
(mm_word_additions.py), the revised manuscript (mm_apply_to_manuscript.py) and the
stand-alone Appendix C (mm_appendix_doc.py). Equation, table and figure labels are
parameters so that the renderings stay consistent with the numbering of the manuscript.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import MODULES, OUT_ROOT, ROOT, load_json  # noqa: E402

LABELS = [m.label for m in MODULES]
FIGURE_PNG = ROOT / "outputs/paper_figures/process_attribution_20260929/figure_process_attribution.png"


@dataclass
class Labels:
    eq_a: str = "4a"        # product-perspective reconciliation
    eq_b: str = "4b"        # process-perspective reconciliation
    eq_bldg: str = "11"     # building account
    tab_graph: str = "Table 2"   # graph statistics by module type (was X1)
    tab_acc: str = "Table 3"     # accounts and reconciliation (was X2)
    fig: str = "Figure X"        # process attribution sensitivity figure
    app: str = "Appendix C"
    old_table2_new: str = "Table 4"  # the manuscript's current Table 2 (phrasing sensitivity)


@dataclass
class Table:
    headers: list
    rows: list
    caption: str
    note: str | None = None


@dataclass
class Content:
    labels: Labels
    sec313: dict = field(default_factory=dict)
    sec411: dict = field(default_factory=dict)
    sec412: dict = field(default_factory=dict)
    sec413: dict = field(default_factory=dict)
    sec435: dict = field(default_factory=dict)
    sec51: dict = field(default_factory=dict)
    sec52: dict = field(default_factory=dict)
    abstract: str = ""
    conclusion: str = ""
    figure_caption: str = ""
    appendix: dict = field(default_factory=dict)


def f1(x):
    return f"{x:,.1f}"


def f0(x):
    return f"{x:,.0f}"


def pc(x, d=1):
    return f"{100 * x:.{d}f}"


def build_content(labels: Labels | None = None) -> Content:
    L = labels or Labels()
    e1 = {r["module"]: r for r in load_json(OUT_ROOT / "E1/e1_graph_quality.json")["rows"]}
    e2 = load_json(OUT_ROOT / "E2/e2_module_accounts.json")
    acc = {a["module"]: a for a in e2["accounts"]}
    variants = {a["module"]: a for a in load_json(OUT_ROOT / "E2/e2_process_variant_accounts.json")}
    e3 = load_json(OUT_ROOT / "E3/e3_heldout_summary.json")
    e3s, solved = e3["summary"], e3["per_case_solved"]
    e5 = load_json(OUT_ROOT / "E5/e5_cost_report.json")
    e6 = load_json(OUT_ROOT / "E6/e6_sensitivity.json")
    e6b = load_json(OUT_ROOT / "E6/e6b_allocation_basis.json")

    a, b, d = (acc[l] for l in LABELS)
    vb_rec, vb_eq = variants["Type B (as recorded)"], variants["Type B (equal route, unscaled)"]
    vd_rec, vd_eq = variants["Type D (as recorded)"], variants["Type D (equal route, unscaled)"]
    bases = ("mass", "duration", "machine_hours", "equal")
    spread = {l: (max(e6b[l]["per_basis"][x]["allocated"] for x in bases) - min(e6b[l]["per_basis"][x]["allocated"] for x in bases)) / e6b[l]["per_basis"]["mass"]["allocated"] for l in LABELS}
    tot_dev = max(abs(e6b[l]["per_basis"][x]["product"] / e6b[l]["per_basis"]["mass"]["product"] - 1) for l in LABELS for x in bases)
    route_dev = max(abs(v["product_total"] / base["product_total"] - 1) for v, base in ((vb_rec, b), (vd_rec, d), (vb_eq, b), (vd_eq, d)))
    sc = {l: e6[l]["scenarios"] for l in LABELS}
    rb = {l: e6[l]["rejected_bound"] for l in LABELS}
    rb_share = {l: rb[l]["upper_bound_kgCO2e"] / sc[l]["S0 baseline"]["material"] for l in LABELS}
    steel_share = [acc[l]["material_by_category"].get("Steel", 0) / acc[l]["material_total"] for l in LABELS]
    ea, eb, ed = (e3s[l]["V4"] for l in LABELS)
    case_ids = set(solved["Type A"])
    solved_all = sum(1 for c in case_ids if all(solved[l].get(c) for l in solved))
    failed_all = sum(1 for c in case_ids if not any(solved[l].get(c) for l in solved))
    lat, tok, mem, build = e5["latency"], e5["tokens"], e5["memory"], e5["build_seconds"]
    tokens_per_q = sum(t["prompt_tokens_per_question_mean"] + t["completion_tokens_per_question_mean"] for t in tok.values()) / 3

    def chg(l, name):
        return sc[l][name]["product"] / sc[l]["S0 baseline"]["product"] - 1

    def band(name):
        v = [abs(chg(l, name)) for l in LABELS]
        return f"{100 * min(v):.1f} to {100 * max(v):.1f}"

    C = Content(labels=L)

    # ------------------------------------------------------------------ 3.1.3
    C.sec313 = {
        "para1": (
            "The allocation policy places each energy record in one of three classes. A record whose factory log names a component or a module is a direct record, and its full value enters the product perspective of that target. "
            "A record with declared shares for the modules of the production batch is a shared record, and the declared shares distribute its value across those modules. The shares follow module mass, which is the physical relationship between the shared energy and the products that ISO 14044 [ref] ranks before any economic relationship, and the factory reports its own energy allocation on the same basis. "
            "A shared record without a declared share for the module is a process-only record, and its value enters the process perspective alone. The three classes give the following reconciliation. "
            f"For module type t the product perspective total of Equation 1 equals the sum of the material term, the direct term and the allocated term, as Equation {L.eq_a} states. "
            f"The process perspective total over the accounting period counts every energy record once, as Equation {L.eq_b} states, and it splits into the direct and allocated terms of module t, the shares allocated to the other modules of the batch and the process-only records."
        ),
        "eq_a": "E_{t}^{prod} = E_{t}^{mat} + E_{t}^{dir} + E_{t}^{alloc}",
        "eq_b": "E^{proc} = Σ_{r} e_{r} = E_{t}^{dir} + E_{t}^{alloc} + E_{t}^{other} + E^{po}",
        "para2": (
            f"The module total reported in Section 4 is the product perspective total of Equation {L.eq_a}. The energy that module t does not carry is U_{{t}} = E^{{proc}} − E_{{t}}^{{dir}} − E_{{t}}^{{alloc}}, and the process perspective reports it next to the module total, so the shared factory energy stays visible in every account and is counted once at factory level. "
            f"Section 4.1.3 reports both totals with case values and {L.app}.1 recomputes the allocated term under alternative allocation rules."
        ),
    }

    # ------------------------------------------------------------------ 4.1.1
    C.sec411 = {
        "old_focus": "The analysis focuses on one Type A module (Figure 7(b)). The module measures 5.616 metres by 2.546 metres internally and weighs between five and six tonnes without movable furniture.",
        "replace_focus": (
            "The analysis covers three module types of the building. Type A is the module studied in detail in Sections 4.2 and 4.3, shown in Figure 7(b), and Type B and Type D are two further residential module types from the same design model and the same factory. "
            "The Type A module measures 5.616 metres by 2.546 metres internally. The structural floor slabs of the three types cover 20.4, 20.0 and 20.4 square metres, and the material mass of Type B and Type D is 0.934 and 1.107 times the mass of Type A."
        ),
        "old_objects": "The selected module contains 321 building objects across seven IFC classes.",
        "replace_objects": (
            "The three models were exported from the same Revit project with the same settings. The Type A model contains 321 building objects in seven IFC classes, the Type B model 314 and the Type D model 298. "
            "We removed the building services elements from the Type B and Type D models, in line with the Type A model, and assigned their specified materials through the type-level material rules of the Type A model, so the three graphs share one material vocabulary. "
            "One structural floor slab of the Type B model had been exported as a generic proxy without a material, and we reclassified it as a floor slab with the Type A slab material after checking the model."
        ),
        "append_factory": (
            "The factory log records one module-level finishing entry and the mass shares of the shared meters for the measured Type A production run, and it records component-level entries for four beams that appear in all three types. "
            "For Type B and Type D we scale the Type A module entry and its mass shares by the ratio of module material mass to Type A mass, 0.934 for Type B and 1.107 for Type D, and renormalise the shares of each meter. "
            "This treats the three types as products of one production route, and Section 4.1.3 reports the account without this assumption as a bound."
        ),
    }

    # ------------------------------------------------------------------ 4.1.2
    C.sec412 = {
        "para": (
            f"{L.tab_graph} compares the three module graphs. The three IFC models instantiate the same 16 classes and 20 relationship types of the integration ontology, and no class or relationship type appears in one graph only. "
            "Graph size differs by less than 6 percent across the types, validation coverage stays between 95.0 and 95.7 percent, and every rejected record falls into the same two reasons, a missing quantity basis or a missing thickness. "
            f"Graph construction from IFC parsing to the alignment audit takes {min(build.values()):.0f} to {max(build.values()):.0f} seconds per module on the workstation described in Section 4.1.1."
        ),
        "table": Table(
            ["Metric"] + LABELS,
            [
                ["Building objects mapped from IFC"] + [f"{e1[l]['building_components']}" for l in LABELS],
                ["Component types"] + [f"{e1[l]['component_types']}" for l in LABELS],
                ["Materials"] + [f"{e1[l]['ifc_materials']}" for l in LABELS],
                ["Design quantities"] + [f"{e1[l]['design_quantities']:,}" for l in LABELS],
                ["Material carbon records accepted / rejected"] + [f"{e1[l]['material_consumptions']} / {e1[l]['evidence_rejected']}" for l in LABELS],
                ["Energy carbon records"] + [f"{e1[l]['energy_consumptions']}" for l in LABELS],
                ["Validation coverage"] + [f"{pc(e1[l]['coverage'])}%" for l in LABELS],
                ["Energy attribution, direct / allocated / process-only"] + [f"{e1[l]['attribution_counts'].get('direct', 0)} / {e1[l]['attribution_counts'].get('allocated', 0)} / {e1[l]['attribution_counts'].get('process_only', 0)}" for l in LABELS],
                ["Nodes / edges"] + [f"{e1[l]['nodes']:,} / {e1[l]['edges']:,}" for l in LABELS],
                ["Ontology classes / relationship types instantiated"] + ["16 / 20"] * 3,
                ["Alignment audit"] + [e1[l]["alignment_status"] for l in LABELS],
                ["Graph construction time (s)"] + [f"{e1[l]['build_seconds']:.1f}" for l in LABELS],
            ],
            f"{L.tab_graph}. Knowledge graph statistics and quality by module type",
        ),
    }

    # ------------------------------------------------------------------ 4.1.3
    C.sec413 = {
        "heading": "4.1.3 Carbon accounts of the three module types",
        "para": (
            f"{L.tab_acc} reports the carbon accounts of the three module types on the A1 to A3 boundary. Material carbon is {f0(a['material_total'])} kgCO2e for Type A, {f0(b['material_total'])} kgCO2e for Type B and {f0(d['material_total'])} kgCO2e for Type D, and steel sections carry {100 * min(steel_share):.0f} to {100 * max(steel_share):.0f} percent of it in every type. "
            f"Direct factory energy adds {f0(min(x['energy_direct_this_module'] for x in (a, b, d)))} to {f0(max(x['energy_direct_this_module'] for x in (a, b, d)))} kgCO2e and allocated shared energy adds {f0(min(x['energy_allocated_this_module'] for x in (a, b, d)))} to {f0(max(x['energy_allocated_this_module'] for x in (a, b, d)))} kgCO2e, so the product perspective totals are {f0(a['product_total'])}, {f0(b['product_total'])} and {f0(d['product_total'])} kgCO2e, or {f0(a['product_per_m2'])}, {f0(b['product_per_m2'])} and {f0(d['product_per_m2'])} kgCO2e per square metre of floor slab. "
            f"The process perspective total is {f0(min(x['process_total'] for x in (a, b, d)))} to {f0(max(x['process_total'] for x in (a, b, d)))} kgCO2e in every graph, because each graph counts the same factory records once. "
            f"The energy that a module does not carry is {f0(min(x['unattributed_to_this_module'] for x in (a, b, d)))} to {f0(max(x['unattributed_to_this_module'] for x in (a, b, d)))} kgCO2e, the sum of the shares allocated to the other modules of the batch and the process-only records. "
            f"Equations {L.eq_a} and {L.eq_b} close to within 1e-12 kgCO2e in all three accounts. Type D has the highest material carbon because its floor slab is heavier and it uses more light-gauge steel, and Type B has the lowest because its steel sections weigh less."
        ),
        "table": Table(
            ["Term (kgCO2e)"] + LABELS,
            [
                ["Material carbon, A1 to A3"] + [f1(acc[l]["material_total"]) for l in LABELS],
                ["Factory energy, direct to the module"] + [f1(acc[l]["energy_direct_this_module"]) for l in LABELS],
                ["Factory energy, allocated to the module by mass share"] + [f1(acc[l]["energy_allocated_this_module"]) for l in LABELS],
                [f"Product perspective total, Equation {L.eq_a}"] + [f1(acc[l]["product_total"]) for l in LABELS],
                ["Factory energy allocated to other modules of the batch"] + [f1(acc[l]["energy_allocated_other_modules"]) for l in LABELS],
                ["Factory energy, process-only"] + [f1(acc[l]["energy_process_only"]) for l in LABELS],
                [f"Process perspective total, Equation {L.eq_b}"] + [f1(acc[l]["process_total"]) for l in LABELS],
                ["Energy not carried by the module, U_t"] + [f1(acc[l]["unattributed_to_this_module"]) for l in LABELS],
                [f"Closure of Equation {L.eq_a}, kgCO2e"] + [f"{acc[l]['identity_product_check']:.1e}" for l in LABELS],
                [f"Closure of Equation {L.eq_b}, kgCO2e"] + [f"{acc[l]['identity_process_check']:.1e}" for l in LABELS],
                ["Floor slab area, m2"] + [f"{acc[l]['floor_area_m2']:.1f}" for l in LABELS],
                ["Product perspective total per m2"] + [f1(acc[l]["product_per_m2"]) for l in LABELS],
                ["Steel / calcium silicate / concrete, kgCO2e"] + [f"{f0(acc[l]['material_by_category'].get('Steel', 0))} / {f0(acc[l]['material_by_category'].get('Calcium Silicate', 0))} / {f0(acc[l]['material_by_category'].get('Concrete', 0))}" for l in LABELS],
            ],
            f"{L.tab_acc}. Carbon accounts and reconciliation of the three module types",
        ),
        "fig_para": (
            f"{L.fig} shows how the process terms respond to the production route assumption and to the allocation rule. "
            f"Using only the recorded component entries for Type B and Type D lowers their totals by {pc(abs(vb_rec['product_total'] / b['product_total'] - 1))} and {pc(abs(vd_rec['product_total'] / d['product_total'] - 1))} percent, applying the Type A entries without scaling changes them by {pc(abs(vb_eq['product_total'] / b['product_total'] - 1))} percent, and the four allocation rules move the allocated term within {100 * max(spread.values()):.0f} percent of its mass-share value and the totals within {100 * tot_dev:.1f} percent. "
            f"The order of the three types holds under every assumption and rule. {L.app}.1 lists the values."
        ),
        "appendix_para": (
            f"{L.app} verifies the accounts and tests their sensitivity. An independent recalculation reproduces every material total, the order of the three types holds under every emission factor scenario tested, the rejected records add at most {pc(min(rb_share.values()))} to {pc(max(rb_share.values()))} percent of material carbon, and the steel factor is the largest source of uncertainty because steel carries about 70 percent of material carbon."
        ),
    }
    C.figure_caption = (
        f"{L.fig}. Sensitivity of process attribution to the production route assumption and the allocation rule. (a) Product perspective totals of Type B and Type D under three production route assumptions, with the change against the mass-scaled proxy. "
        f"(b) The same factory energy split into the direct, allocated and outside-module classes of Equation {L.eq_b}, which sum to the process perspective total in every row. (c) Allocated factory energy of the three module types under the mass, production duration, machine-hour and equal allocation rules. "
        f"(d) Product perspective totals under the four rules, with the change against the mass share. Material carbon is {f1(a['material_total'])}, {f1(b['material_total'])} and {f1(d['material_total'])} kgCO2e for Type A, Type B and Type D and does not depend on either assumption."
    )

    # ------------------------------------------------------------------ 4.3.5
    C.sec435 = {
        "heading": "4.3.5 Transfer across module types",
        "para": (
            f"With the compiler, its prompt and its worked examples frozen on the Type A graph, held-out questions on Type B and Type D reach {pc(eb['answer_exact_match']['mean'])} percent exact answers against {pc(ea['answer_exact_match']['mean'])} percent on Type A, and {solved_all} of the 48 questions are solved on all three graphs while {failed_all} fail on all three, so failures follow the question type and not the module. "
            f"Compiling a question takes {min(v['compile_mean'] for v in lat.values()) / 1000:.1f} to {max(v['compile_mean'] for v in lat.values()) / 1000:.1f} seconds and about {f0(round(tokens_per_q, -2))} tokens on every graph, and building a module graph takes {min(build.values()):.0f} to {max(build.values()):.0f} seconds. "
            f"{L.app}.6 and {L.app}.7 report the held-out benchmark, the full results and the cost by module type."
        ),
    }

    # ------------------------------------------------------------------ 5.1
    C.sec51 = {
        "bridge": (
            "The case study covers three of the module types of the building, which contains 873 modules of a few types. A modular building repeats a small number of module designs, so the building account follows from the type accounts. "
            "Each module type has one IFC model and one production route, and its material carbon is fixed by the design. The knowledge graph therefore holds one graph per type together with the number of modules of each type, and the carbon of the building is the sum over types of the module count multiplied by the type total. "
            "A question about the building compiles into the same CarbonQL program as a question about one module, with the module count as a weight. The graph size grows with the number of types, not with the number of modules, and the compiler cost stays constant because the prompt carries the schema and not the data. "
            "Process carbon needs one further step. Factory energy is metered for the whole production period, so the type-level process totals multiplied by module counts should reconcile with the metered factory total over that period. This reconciliation checks the allocation policy of Section 3.1.3 at the building scale. "
            "Two assumptions limit the argument. Modules of one type may carry design variants that change material quantities, and production routes may change between batches. Both cases add a graph per variant and leave the aggregation unchanged."
        ),
        "eq_intro": f"Equation {L.eq_bldg} states the building account, where N_{{t}} is the number of modules of type t and E^{{shared}} is the factory energy metered over the production period that no module carries, counted once.",
        "eq": "E^{bldg} = Σ_{t} N_{t} (E_{t}^{mat} + E_{t}^{dir} + E_{t}^{alloc}) + E^{shared}",
        "eq_after": f"With the three types of this study the type totals are {f0(a['product_total'])}, {f0(b['product_total'])} and {f0(d['product_total'])} kgCO2e, and the building total follows from the module schedule [N_A, N_B, N_D and the remaining types of the 873 modules].",
        "uncertainty": (
            f"The sensitivity results in {L.app} order the sources of uncertainty. The steel factor comes first, because steel carries about 70 percent of material carbon and a 10 percent change of the factor moves the totals by 7 percent. "
            f"The production route assumption for Type B and Type D comes second and moves their totals by up to {pc(route_dev)} percent. The allocation rule for shared energy moves the allocated term by {100 * max(spread.values()):.0f} percent and the totals by {100 * tot_dev:.1f} percent. "
            "The rejected records come next with a bound of 2 to 7 percent of material carbon, and the electricity and diesel factors move the product perspective by 1 percent or less. None of these changes the order of the three types, and only the two smaller material categories change places under perturbation. "
            "The account therefore supports comparisons between module types and between material categories at the level of tens of percent, and a product-specific steel declaration is the one input that would narrow the band further."
        ),
    }

    # ------------------------------------------------------------------ 5.2
    C.sec52 = {
        "para": (
            "The evidence comes from one project and one factory. The three module types share the design model, the material library and the production route, so the results show that the ontology and the compiler transfer across module types within a project, and they do not show transfer across projects, factories or regions. "
            "The ontology and CarbonQL carry no project-specific terms, and moving to another project requires the IFC material mapping and the factory energy dataset of that project. "
            f"The factory dataset records module-level energy for the measured Type A run only, and the Type B and Type D process terms rest on the mass-scaled proxy of Section 4.1.1, which {L.app}.1 bounds. The disaggregation of the metered monthly energy into stages and resources is calibrated to the meter totals and is synthetic below the line level. "
            f"The cost figures in {L.app}.7 come from a single user, and concurrent access was not tested. The rejected records and the 2013 date of the steel factor are the main data quality gaps, and {L.app} quantifies their effect."
        ),
    }

    # ------------------------------------------------------------------ abstract / conclusion
    C.abstract = (
        f"The framework is demonstrated on three module types of a modular residence in Hong Kong, and a compiler frozen on one module type answers held-out questions on the other two with {pc(eb['answer_exact_match']['mean'], 0)} percent exact answers against {pc(ea['answer_exact_match']['mean'], 0)} percent on the type it was developed on."
    )
    C.conclusion = "Across the three module types of the case, the accounts reconcile to machine precision, an independent recalculation reproduces every material total, and the order of the types holds under every emission factor scenario and allocation rule tested."

    # ================================================================== Appendix C
    cols = [("Type B", "Mass-scaled proxy, main account", b), ("Type B", "Recorded entries only", vb_rec), ("Type B", "Unscaled Type A entries", vb_eq),
            ("Type D", "Mass-scaled proxy, main account", d), ("Type D", "Recorded entries only", vd_rec), ("Type D", "Unscaled Type A entries", vd_eq)]
    c1_rows = []
    for m, name, c in cols:
        base = b if m == "Type B" else d
        c1_rows.append([m, name, f1(c["energy_direct_this_module"]), f1(c["energy_allocated_this_module"]), f1(c["product_total"]), f1(c["unattributed_to_this_module"]),
                        "0.0" if c is base else f"{100 * (c['product_total'] / base['product_total'] - 1):+.1f}"])
    basis_names = {"mass": "Mass share, main account", "duration": "Production duration share", "machine_hours": "Machine-hour share", "equal": "Equal shares across batch modules"}
    c2_rows = []
    for x in bases:
        vals = [e6b[l]["per_basis"][x]["product"] for l in LABELS]
        order = ", ".join(l.replace("Type ", "") for l, _ in sorted(zip(LABELS, vals), key=lambda kv: -kv[1]))
        c2_rows.append([basis_names[x]] + [f1(e6b[l]["per_basis"][x]["allocated"]) for l in LABELS] + [f1(v) for v in vals] + [order])
    scen_rows = [
        ("S0 baseline", "Selected A1 to A3 factors, baseline"),
        ("S1 original database factors", "Original database factors before localisation"),
        ("S2 Steel +10%", "Steel factor +10%"), ("S2 Steel -10%", "Steel factor −10%"),
        ("S2 Calcium Silicate +10%", "Calcium silicate factor +10%"), ("S2 Calcium Silicate -10%", "Calcium silicate factor −10%"),
        ("S2 Concrete +10%", "Concrete factor +10%"), ("S2 Concrete -10%", "Concrete factor −10%"),
        ("S3 electricity national lifecycle 0.6205", "Electricity, national life-cycle factor 0.6205"),
        ("S3 electricity Guangdong CO2-only 0.4419", "Electricity, provincial CO2-only factor 0.4419"),
        ("S4 diesel combustion-only 2.6594", "Diesel, combustion only 2.6594"),
    ]
    c3_rows = []
    for key, name in scen_rows:
        vals = [sc[l][key]["product"] for l in LABELS]
        order = ", ".join(l.replace("Type ", "") for l, _ in sorted(zip(LABELS, vals), key=lambda kv: -kv[1]))
        c3_rows.append([name] + [f"{f1(v)} ({100 * chg(l, key):+.1f}%)" for l, v in zip(LABELS, vals)] + [order])
    c4_rows = []
    for l in LABELS:
        for g in rb[l]["detail"]:
            c4_rows.append([l, g["factor_row"], g["records"], f1(g["bound_mass_kg"]), f1(g["bound_kgCO2e"]), g["method"]])
    c5_rows = [
        ["Steel section and light-gauge steel factors", "HKUST ECO-CM v1.1, 2013, Hong Kong localised, 39% recycled content", "Low", "High", "Medium", "69 to 72% of material carbon; ±10% moves totals by 6.6 to 6.8%"],
        ["Aluminium profile factor", "HKUST ECO-CM v1.1 general aluminium, 2013, Hong Kong localised", "Low", "High", "Medium", "1.4 to 1.5% of material carbon"],
        ["Calcium silicate board factor", "Promat PROMATECT-H EPD, 2023, Guangzhou production", "High", "High", "Medium", "11 to 15% of material carbon; ±10% moves totals by 1.0 to 1.4%"],
        ["Concrete factor", "ICE v2.0 cement, mortar and concrete model, RC 25/30, 2011, United Kingdom", "Low", "Low", "Medium", "13 to 15% of material carbon; ±10% moves totals by 1.3 to 1.5%"],
        ["Rock wool factor", "ROCKWOOL stone wool EPD, 2022, Malaysia and Thailand", "High", "Medium", "Medium", "1.0 to 1.3% of material carbon"],
        ["Factory electricity factor", "MEE 2023 national life-cycle factor scaled to Guangdong, 0.5168 kgCO2e per kWh", "High", "Medium", "Medium", "Attributed energy ±20%; totals ±1%"],
        ["Diesel factor", "UK DESNZ 2023 life-cycle factor, 3.2835 kgCO2e per litre", "High", "Low", "Medium", "Process perspective 7%; product perspective below 0.01%"],
        ["Design quantities", "Revit 2024 design model base quantities exported to IFC4", "High", "Not applicable", "Medium, design not as-built", "Rejected records bound 2.3 to 6.7% of material carbon"],
        ["Factory energy records", "Metered monthly electricity and diesel, disaggregated to stages and resources", "High", "High", "Low below line level, synthetic disaggregation", "Allocated term ±17% across allocation rules; totals ±0.4%"],
        ["Type B and Type D module-level energy", "Type A entries scaled by module mass ratio", "High", "High", "Low, proxy", "Module totals within 4.1% of the bounding assumptions"],
    ]

    def ms(l, m):
        v = e3s[l]["V4"][m]
        return f"{pc(v['mean'])}% ± {100 * v['sd']:.1f}"

    c6_rows = [
        ["Answer exact"] + [ms(l, "answer_exact_match") for l in LABELS],
        ["Accounting boundary correct"] + [ms(l, "boundary_match") for l in LABELS],
        ["Presentation correct"] + [ms(l, "presentation_match") for l in LABELS],
        ["Query status correct"] + [ms(l, "status_match") for l in LABELS],
        ["Total value correct"] + [ms(l, "total_match") for l in LABELS],
        ["Programs compiled"] + [ms(l, "compiled") for l in LABELS],
        ["Cross-perspective questions, answer exact"] + [f"{pc(e3s[l]['by_category']['cross_view']['answer_exact_match'])}%" for l in LABELS],
        ["Partial or ambiguous questions, answer exact"] + [f"{pc(e3s[l]['by_category']['partial_or_ambiguous']['answer_exact_match'])}%" for l in LABELS],
        ["Language model calls per question"] + [f"{e3s[l]['mean_llm_calls']['mean']:.2f}" for l in LABELS],
    ]
    c7_rows = [
        ["Graph construction (s)"] + [f"{build[l]:.1f}" for l in LABELS],
        ["Graph file (MB)"] + [f"{e5['graph_mb'][l]:.2f}" for l in LABELS],
        ["Process memory growth on load (MB)"] + [f"{mem[l]['delta_mb']:.0f}" for l in LABELS],
        ["Compile latency, mean / p95 (ms)"] + [f"{lat[l]['compile_mean']:.0f} / {lat[l]['compile_p95']:.0f}" for l in LABELS],
        ["Execute latency, mean / p95 (ms)"] + [f"{lat[l]['execute_mean']:.0f} / {lat[l]['execute_p95']:.0f}" for l in LABELS],
        ["Language model calls per question"] + [f"{lat[l]['llm_calls_mean']:.2f}" for l in LABELS],
        ["Prompt tokens per question, mean / p95"] + [f"{tok[l]['prompt_tokens_per_question_mean']:.0f} / {tok[l]['prompt_tokens_per_question_p95']:.0f}" for l in LABELS],
        ["Completion tokens per question, mean / p95"] + [f"{tok[l]['completion_tokens_per_question_mean']:.0f} / {tok[l]['completion_tokens_per_question_p95']:.0f}" for l in LABELS],
        ["API cost per question (USD)"] + [f"{tok[l]['usd_per_question_mean']:.4f}" for l in LABELS],
        ["API cost per 150 questions (USD)"] + [f"{tok[l]['usd_per_150_questions']:.2f}" for l in LABELS],
    ]

    A = L.app
    C.appendix = {
        "title": f"{A}. Verification, sensitivity and cost of the multi-module case",
        "intro": "This appendix reports the checks behind the three-module accounts of Section 4.1.3, the transfer test of Section 4.3.5 and the cost of the pipeline. All values come from the released graphs and the stored evidence files.",
        "sections": [
            {"heading": f"{A}.1 Process attribution under alternative assumptions", "blocks": [
                ("para", f"Table C1 gives the values plotted in {L.fig}(a, b). Without the mass-scaled proxy of Section 4.1.1, Type B and Type D carry only the four component-level entries, {f0(vb_rec['energy_direct_this_module'])} kgCO2e direct and {f0(vb_rec['energy_allocated_this_module'])} kgCO2e allocated through the shared meters that the policy assigns to component subsets, and their product totals are {f0(vb_rec['product_total'])} and {f0(vd_rec['product_total'])} kgCO2e. With the Type A entries applied unscaled, the totals are {f0(vb_eq['product_total'])} and {f0(vd_eq['product_total'])} kgCO2e. The material term does not depend on the assumption, and every row sums to the process perspective total of Equation {L.eq_b}."),
                ("table", Table(["Module type", "Production route assumption", "Direct factory energy", "Allocated factory energy", "Product total", "Factory energy outside module attribution", "Change, percent"], c1_rows,
                                "Table C1. Carbon results for Type B and Type D under three production route assumptions, kgCO2e",
                                "Percentage changes refer to the main account. Material carbon is 5,723.6 kgCO2e for Type B and 6,477.8 kgCO2e for Type D under every assumption.")),
                ("para", f"Table C2 gives the values plotted in {L.fig}(c, d). The four shared meters with declared module shares are re-allocated by the production duration shares and the machine-hour shares of the factory dataset and by an equal split among the modules of the batch. The allocated term moves within {100 * max(spread.values()):.0f} percent of its mass-share value, the product totals move by at most {100 * tot_dev:.1f} percent, and the order of the three types does not change. On the four-module factory dataset used to develop the allocation policy, the same change of rule moved the allocated production energy of a module by 7 to 19 percent of its mean and reversed the order of one of the six module pairs [Section 4.x or Figure 8]."),
                ("table", Table(["Allocation rule"] + [f"{l} allocated" for l in LABELS] + [f"{l} total" for l in LABELS] + ["Descending order"], c2_rows,
                                "Table C2. Allocated factory energy and product totals under four allocation rules, kgCO2e")),
            ]},
            {"heading": f"{A}.2 Independent recalculation", "blocks": [
                ("para", "Two checks outside the graph builder cover the arithmetic from stored quantity and factor to carbon. A spreadsheet recomputes every accepted material record from the design quantity, the thickness and the density stored in the material evidence file and the selected factor of the factor workbook, and its totals match the graph totals of the three modules to within 1e-12 kgCO2e. An independent reviewer recalculated 47 sampled records of the Type A module, 30 material and 17 energy, at a tolerance of 1 percent, and all 47 passed with a summed difference of zero. The parser comparison in Section 4.1.2 covers the extraction of quantities and materials from IFC against manually verified reference data."),
            ]},
            {"heading": f"{A}.3 Emission factor scenarios", "blocks": [
                ("para", f"Table C3 reports the product perspective totals under emission factor scenarios. Replacing the selected Hong Kong localised factors by the original database values raises the totals by {band('S1 original database factors')} percent. A 10 percent change of the steel factor moves the totals by {band('S2 Steel +10%')} percent, and the same change of the calcium silicate or concrete factor moves them by 1.0 to 1.5 percent. Replacing the Guangdong electricity proxy by the national life-cycle factor of 0.6205 kgCO2e per kWh raises the totals by {band('S3 electricity national lifecycle 0.6205')} percent, and the CO2-only provincial factor of 0.4419 lowers them by {band('S3 electricity Guangdong CO2-only 0.4419')} percent. Restricting diesel to its combustion emissions lowers the process perspective by 7 percent and changes the product perspective by less than 0.01 percent, because diesel enters the account through process-only records. The order of the three types, Type D above Type A above Type B, holds in every scenario, and steel stays the largest material category in every type. Calcium silicate and concrete differ by 2 percent in Type A and by 8 percent in Type B, and their order changes under a 10 percent change of either factor."),
                ("table", Table(["Scenario"] + LABELS + ["Descending order"], c3_rows, "Table C3. Product perspective totals, kgCO2e, and change against the baseline under emission factor scenarios",
                                "The original calcium silicate and fibre cement factors are CO2-only values and stay at the baseline in the original-database scenario.")),
            ]},
            {"heading": f"{A}.4 Rejected material records", "blocks": [
                ("para", f"The pipeline rejected eight material records in Type A, eight in Type B and seven in Type D. They belong to four component and material groups in each type, the toughened glass of the windows and the ceramic tiles of the two toilet floors, and in Type D also the calcium silicate board of one partition. We bound their carbon by treating each window opening as one 6 millimetre glass pane and each other component as made entirely of the rejected material at its full model volume. The bound is {f0(rb['Type A']['upper_bound_kgCO2e'])} kgCO2e for Type A and Type B and {f0(rb['Type D']['upper_bound_kgCO2e'])} kgCO2e for Type D, that is {pc(rb_share['Type A'])}, {pc(rb_share['Type B'])} and {pc(rb_share['Type D'])} percent of material carbon, and adding the bound to any total leaves the order of the types unchanged. Table C4 lists the groups."),
                ("table", Table(["Module", "Material", "Records", "Bound mass, kg", "Bound, kgCO2e", "Bounding rule"], c4_rows, "Table C4. Conservative upper bound of the rejected material records")),
            ]},
            {"heading": f"{A}.5 Data quality indicators", "blocks": [
                ("para", "Table C5 rates the data sources of the account by the time, geography and technology criteria of the EN 15804 data quality requirements [ref] and the ecoinvent pedigree matrix [ref]. The steel, aluminium and light-gauge steel factors come from the Hong Kong localised inventory published in 2013, so their geography rates high and their time rates low. The two product declarations for calcium silicate board and rock wool are recent and stand for proxy products of the same class. The concrete factor is a 2011 mix model from the United Kingdom. The design quantities come from the design model and not from as-built measurement. The factory energy totals are metered monthly readings for the production period, and their split into stages and resources is a disaggregation calibrated to the meter totals, which the graph marks as synthetic. The steel factor has the largest effect on the totals because steel carries about 70 percent of material carbon, and its 2013 date is the main quality gap of the account. The electricity proxy has the second largest effect and moves the attributed energy by up to 20 percent and the totals by 1 percent."),
                ("table", Table(["Data source", "Origin and date", "Time", "Geography", "Technology", "Effect on the account"], c5_rows,
                                "Table C5. Data quality indicators of the account and their effect on totals and rankings",
                                "Time: high within 5 years, medium 5 to 10 years, low more than 10 years. Geography: high for the same market or factory, medium for a neighbouring region or a value scaled from it, low for another continent. Technology: high for a product-specific declaration, medium for a proxy of the same product class, low for a synthetic or derived value.")),
            ]},
            {"heading": f"{A}.6 Held-out transfer across module types", "blocks": [
                ("para", f"The compiler, its prompt and its worked examples stayed frozen on the Type A graph. For each of the three graphs we generated a held-out set of 48 questions from the same wording specifications, 40 cross-perspective questions and 8 partial or ambiguous questions, with reference programs and values executed deterministically on that graph, and no question overlaps with the development set. Table C6 reports the results of the full compiler with DeepSeek-chat at temperature zero over five repeats. Exact answers reach {pc(ea['answer_exact_match']['mean'])} percent on Type A and {pc(eb['answer_exact_match']['mean'])} percent on Type B and Type D, the accounting boundary is correct in {pc(ea['boundary_match']['mean'])} to {pc(ed['boundary_match']['mean'])} percent of cases, and every question compiles. {solved_all} of the 48 questions are solved on all three graphs and {failed_all} fail on all three. Partial and ambiguous questions reach 50 percent exact answers on every graph and are the main source of failure."),
                ("table", Table(["Measure, mean ± sd over 5 repeats"] + LABELS, c6_rows, "Table C6. Held-out compilation results on the three module graphs with the compiler frozen on Type A")),
            ]},
            {"heading": f"{A}.7 Computational cost", "blocks": [
                ("para", f"Table C7 reports the cost of the pipeline. Building a module graph takes {min(build.values()):.0f} to {max(build.values()):.0f} seconds and loading it adds {min(v['delta_mb'] for v in mem.values()):.0f} to {max(v['delta_mb'] for v in mem.values()):.0f} megabytes to the process memory. Compiling a question takes {min(v['compile_mean'] for v in lat.values()) / 1000:.1f} to {max(v['compile_mean'] for v in lat.values()) / 1000:.1f} seconds on average and {min(v['compile_p95'] for v in lat.values()) / 1000:.1f} to {max(v['compile_p95'] for v in lat.values()) / 1000:.1f} seconds at the 95th percentile, with 1.4 language model calls, about {f0(sum(t['prompt_tokens_per_question_mean'] for t in tok.values()) / 3)} prompt tokens and {f0(sum(t['completion_tokens_per_question_mean'] for t in tok.values()) / 3)} completion tokens per question, and a cost below 0.001 US dollars per question at the list price of DeepSeek-chat, or {sum(t['usd_per_150_questions'] for t in tok.values()) / 3:.2f} US dollars for the 150-question benchmark. Executing the compiled program on the in-memory graph takes {min(v['execute_mean'] for v in lat.values()) / 1000:.1f} to {max(v['execute_mean'] for v in lat.values()) / 1000:.1f} seconds. The full pipeline answers the 150-question benchmark in 3.1 seconds per question on average and 4.5 seconds at the 95th percentile. Compilation cost does not depend on graph size, because the prompt carries the operator contract, the schema and the worked examples and not the data, and the three graphs show the same token counts."),
                ("table", Table(["Measure"] + LABELS, c7_rows, "Table C7. Construction, query and language model cost of the pipeline by module type",
                                "Prices are the public list price of DeepSeek-chat at the time of the runs, 0.28 US dollars per million input tokens and 0.42 US dollars per million output tokens.")),
            ]},
        ],
    }
    return C
