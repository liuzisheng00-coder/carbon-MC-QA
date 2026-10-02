# -*- coding: utf-8 -*-
"""E6b: allocation-basis sensitivity of the module accounts on the case graphs.

For the shared factory meters that carry declared module shares, the allocated term of
each module is recomputed with the duration and machine-hour shares of the factory
dataset and with an equal split among the modules of the batch. Direct records and the
overriding shared meters that the policy assigns to component subsets do not depend on
the basis and keep their graph values.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import MODULES, OUT_ROOT, ROOT, load_context, load_json, markdown_table, write_json, write_text  # noqa: E402

E6_DIR = OUT_ROOT / "E6"
BASES = ("mass", "duration", "machine_hours", "equal")


def factory_payload(spec):
    case = load_json(spec.case_config)
    rel = case["factoryInput"]["path"].replace("\\", "/")
    return load_json((spec.case_config.parent / rel).resolve()), case["module"]["sourceIdentity"]


def module_shares(payload, identity):
    """(record_id, basis) -> declared share of this module; 'equal' -> row count share."""
    rows = defaultdict(list)
    for row in payload["p0_allocation_rows"]:
        if str(row.get("target_level", "")).casefold() != "module":
            continue
        rows[(row["record_id"], row.get("allocation_key") or row.get("allocation_basis"))].append(row)
    shares = {}
    for (record_id, basis), items in rows.items():
        mine = [r for r in items if r.get("target_id") == identity]
        if not mine:
            continue
        shares[(record_id, basis)] = sum(float(r["allocation_fraction"]) for r in mine)
        shares.setdefault((record_id, "equal"), len(mine) / len(items))
    return shares


def main() -> None:
    results = {}
    for spec in MODULES:
        payload, identity = factory_payload(spec)
        shares = module_shares(payload, identity)
        ctx = load_context(spec)
        projected = defaultdict(float)
        for c in ctx.product_contributions:
            if c.mode != "material":
                projected[c.emission_id] += c.projected_value
        material = sum(e.emission_value for e in ctx.emissions if e.kind == "material")
        direct = sum(projected[e.emission_id] for e in ctx.emissions if e.kind == "energy" and e.mode == "direct")
        per_basis = {}
        detail = []
        for basis in BASES:
            allocated = 0.0
            for e in ctx.emissions:
                if e.kind != "energy" or e.mode != "allocated":
                    continue
                key = (e.record_id, basis)
                if key in shares:
                    value = e.emission_value * shares[key]
                    basis_dependent = True
                else:
                    value = projected[e.emission_id]
                    basis_dependent = False
                allocated += value
                if basis == "mass":
                    detail.append({"record": e.record_id, "emission_kgCO2e": e.emission_value, "graph_share": projected[e.emission_id] / e.emission_value if e.emission_value else None,
                                   "basis_dependent": basis_dependent, "declared_shares": {b: shares.get((e.record_id, b)) for b in BASES}})
            per_basis[basis] = {"allocated": allocated, "product": material + direct + allocated, "per_m2": (material + direct + allocated) / spec.floor_area_m2}
        graph_allocated = sum(projected[e.emission_id] for e in ctx.emissions if e.kind == "energy" and e.mode == "allocated")
        results[spec.label] = {"identity": identity, "material": material, "direct": direct, "graph_allocated": graph_allocated,
                               "mass_basis_reproduces_graph": abs(per_basis["mass"]["allocated"] - graph_allocated) < 1e-6,
                               "per_basis": per_basis, "records": detail}
        print(spec.label, identity, {b: round(v["allocated"], 2) for b, v in per_basis.items()}, "graph", round(graph_allocated, 2))

    labels = [m.label for m in MODULES]
    rows = []
    for basis in BASES:
        vals = [results[l]["per_basis"][basis]["product"] for l in labels]
        alloc = [results[l]["per_basis"][basis]["allocated"] for l in labels]
        ranking = " > ".join(l.replace("Type ", "") for l, _ in sorted(zip(labels, vals), key=lambda kv: -kv[1]))
        rows.append([basis] + [f"{a:,.1f}" for a in alloc] + [f"{v:,.1f}" for v in vals] + [ranking])
    spread = [f"{100 * (max(results[l]['per_basis'][b]['allocated'] for b in BASES) - min(results[l]['per_basis'][b]['allocated'] for b in BASES)) / results[l]['per_basis']['mass']['allocated']:.1f}%" for l in labels]
    md = ["# E6b Allocation-basis sensitivity on the case graphs", "",
          "Shared meters with declared module shares are re-allocated by the duration and machine-hour shares of the factory dataset and by an equal split among the modules of the batch. Direct records and the overriding shared meters keep their graph values.", "",
          markdown_table(["Basis"] + [f"{l} allocated" for l in labels] + [f"{l} product total" for l in labels] + ["Ranking"], rows), "",
          "Range of the allocated term across bases as a share of the mass-basis value: " + ", ".join(f"{l} {s}" for l, s in zip(labels, spread)) + ".", ""]
    for l in labels:
        md.append(f"- {l}: mass basis reproduces the graph allocated term: {results[l]['mass_basis_reproduces_graph']}; basis-dependent records: "
                  + ", ".join(sorted({d['record'] for d in results[l]['records'] if d['basis_dependent']})))
    write_text(E6_DIR / "e6b_allocation_basis.md", "\n".join(md) + "\n")
    write_json(E6_DIR / "e6b_allocation_basis.json", results)
    print("written:", E6_DIR / "e6b_allocation_basis.md")


if __name__ == "__main__":
    main()
