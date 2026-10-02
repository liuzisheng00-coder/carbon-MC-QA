# -*- coding: utf-8 -*-
"""Strip MEP content from Type B / Type D IFC models.

Removes: every IfcDistributionElement occurrence (pipes, conduits, fittings,
valves, terminals, alarms), their IfcDistributionPort ports, IfcDistributionSystem
groups, IfcBuildingElementProxy occurrences whose family names identify electrical
fittings (sockets, switches, junction boxes, distribution board, fans), the element
types left without occurrences, and the material definitions left without users.

Architectural / structural elements, openings, storeys and their GlobalIds are
left untouched. Originals are never modified; a *_nomep.ifc copy is written.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import ifcopenshell
import ifcopenshell.api.root
import ifcopenshell.util.element as eu

try:
    import ifcopenshell.api.system as api_system
except Exception:  # pragma: no cover
    api_system = None
try:
    import ifcopenshell.api.group as api_group
except Exception:  # pragma: no cover
    api_group = None

MEP_PROXY_PATTERN = re.compile(
    r"插座|插头|接线|导线盒|开关|配电|通风机|换气扇|遥控|线管"
)

MATERIAL_DESCRIPTOR_CLASSES = (
    "IfcMaterialProperties",
    "IfcMaterialDefinitionRepresentation",
    "IfcExtendedMaterialProperties",
)


def _text(el) -> str:
    return " ".join(str(x) for x in (getattr(el, "Name", None), getattr(el, "ObjectType", None)) if x)


def _family(el) -> str:
    return (getattr(el, "ObjectType", None) or getattr(el, "Name", None) or "").split(":")[0]


def _required_attr_missing(schema, el) -> bool:
    decl = schema.declaration_by_name(el.is_a())
    for idx, attr in enumerate(decl.all_attributes()):
        if attr.optional():
            continue
        try:
            value = el[idx]
        except Exception:
            continue
        if value is None or (isinstance(value, (tuple, list)) and len(value) == 0):
            return True
    return False


def _remove_orphan_types(model, type_ids_touched: set[int], log: dict) -> None:
    removed = Counter()
    for t in list(model.by_type("IfcTypeProduct")):
        is_mep_type = t.is_a("IfcDistributionElementType")
        if not is_mep_type and t.id() not in type_ids_touched:
            continue
        occurrences = []
        for rel in getattr(t, "Types", []) or []:
            occurrences.extend(rel.RelatedObjects or [])
        if occurrences:
            continue
        removed[t.is_a()] += 1
        ifcopenshell.api.root.remove_product(model, product=t)
    log["removed_types"] = dict(removed)


def _remove_orphan_materials(model, log: dict) -> None:
    removed = Counter()
    changed = True
    passes = 0
    while changed and passes < 6:
        changed = False
        passes += 1
        # constituent sets / layer sets / constituents with no users first
        for cls in ("IfcMaterialConstituentSet", "IfcMaterialLayerSetUsage", "IfcMaterialLayerSet",
                    "IfcMaterialProfileSetUsage", "IfcMaterialProfileSet", "IfcMaterialList",
                    "IfcMaterialConstituent", "IfcMaterialLayer", "IfcMaterialProfile"):
            for md in list(model.by_type(cls)):
                users = [inv for inv in model.get_inverse(md)
                         if not inv.is_a(MATERIAL_DESCRIPTOR_CLASSES[0])
                         and not inv.is_a(MATERIAL_DESCRIPTOR_CLASSES[1])]
                if users:
                    continue
                for inv in list(model.get_inverse(md)):
                    eu.remove_deep2(model, inv)
                model.remove(md)
                removed[cls] += 1
                changed = True
        for mat in list(model.by_type("IfcMaterial")):
            users = [inv for inv in model.get_inverse(mat)
                     if not any(inv.is_a(c) for c in MATERIAL_DESCRIPTOR_CLASSES)]
            if users:
                continue
            for inv in list(model.get_inverse(mat)):
                eu.remove_deep2(model, inv)
            removed[f"IfcMaterial:{mat.Name}"] += 1
            model.remove(mat)
            changed = True
    log["removed_materials"] = dict(removed)


def _remove_invalid_relationships(model, schema, log: dict) -> None:
    removed = Counter()
    changed = True
    passes = 0
    while changed and passes < 6:
        changed = False
        passes += 1
        targets = list(model.by_type("IfcRelationship")) + list(model.by_type("IfcPresentationLayerAssignment"))
        for rel in targets:
            if _required_attr_missing(schema, rel):
                removed[rel.is_a()] += 1
                model.remove(rel)
                changed = True
    log["removed_invalid_relationships"] = dict(removed)


def strip_mep(src: Path, dst: Path) -> dict:
    t0 = time.time()
    model = ifcopenshell.open(str(src))
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(model.schema)
    log: dict = {"source": str(src), "output": str(dst), "schema": model.schema}

    before = Counter(p.is_a() for p in model.by_type("IfcProduct"))
    keep_gids = {
        p.GlobalId
        for p in model.by_type("IfcElement")
        if not p.is_a("IfcDistributionElement") and not p.is_a("IfcBuildingElementProxy")
    }

    # 1. MEP occurrences
    dist = list(model.by_type("IfcDistributionElement"))
    proxies_mep = [p for p in model.by_type("IfcBuildingElementProxy") if MEP_PROXY_PATTERN.search(_text(p))]
    proxies_kept = [p for p in model.by_type("IfcBuildingElementProxy") if not MEP_PROXY_PATTERN.search(_text(p))]
    log["removed_distribution_elements"] = dict(Counter(p.is_a() for p in dist))
    log["removed_proxy_families"] = dict(Counter(_family(p) for p in proxies_mep))
    log["kept_proxy_families"] = dict(Counter(_family(p) for p in proxies_kept))

    type_ids_touched: set[int] = set()
    for p in dist + proxies_mep:
        t = eu.get_type(p)
        if t is not None:
            type_ids_touched.add(t.id())
    for p in dist + proxies_mep:
        ifcopenshell.api.root.remove_product(model, product=p)

    # 2. Ports left behind
    ports = list(model.by_type("IfcDistributionPort"))
    log["removed_ports_after_elements"] = len(ports)
    for port in ports:
        ifcopenshell.api.root.remove_product(model, product=port)

    # 3. Distribution systems
    systems = list(model.by_type("IfcDistributionSystem"))
    log["removed_systems"] = [s.Name for s in systems]
    for s in systems:
        done = False
        if api_system is not None and hasattr(api_system, "remove_system"):
            try:
                api_system.remove_system(model, system=s)
                done = True
            except Exception:
                done = False
        if not done and api_group is not None and hasattr(api_group, "remove_group"):
            try:
                api_group.remove_group(model, group=s)
                done = True
            except Exception:
                done = False
        if not done:
            for inv in list(model.get_inverse(s)):
                model.remove(inv)
            model.remove(s)
    # other groups emptied by the removal
    empty_groups = [g for g in model.by_type("IfcGroup") if not (getattr(g, "IsGroupedBy", None) or [])]
    log["removed_empty_groups"] = [f"{g.is_a()}:{g.Name}" for g in empty_groups]
    for g in empty_groups:
        for inv in list(model.get_inverse(g)):
            model.remove(inv)
        model.remove(g)

    # 4. Types without occurrences, dangling relationships, orphan materials
    _remove_orphan_types(model, type_ids_touched, log)
    _remove_invalid_relationships(model, schema, log)
    _remove_orphan_materials(model, log)
    _remove_invalid_relationships(model, schema, log)  # materials removal may empty associations

    after = Counter(p.is_a() for p in model.by_type("IfcProduct"))
    after_gids = {
        p.GlobalId
        for p in model.by_type("IfcElement")
        if not p.is_a("IfcDistributionElement") and not p.is_a("IfcBuildingElementProxy")
    }
    log["product_counts_before"] = dict(before)
    log["product_counts_after"] = dict(after)
    log["building_element_gids_preserved"] = keep_gids == after_gids
    log["n_building_elements"] = len(after_gids)
    log["remaining_distribution_elements"] = len(model.by_type("IfcDistributionElement"))
    log["remaining_ports"] = len(model.by_type("IfcDistributionPort"))
    log["remaining_systems"] = len(model.by_type("IfcDistributionSystem"))
    log["remaining_materials"] = sorted(m.Name or "<Unnamed>" for m in model.by_type("IfcMaterial"))

    dst.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(dst))
    log["seconds"] = round(time.time() - t0, 1)
    log["output_bytes"] = dst.stat().st_size
    log["source_bytes"] = src.stat().st_size

    # reopen check
    reopened = ifcopenshell.open(str(dst))
    log["reopen_ok"] = True
    log["reopen_products"] = len(reopened.by_type("IfcProduct"))
    return log


def main(argv: list[str]) -> int:
    """Usage: python strip_ifc_mep.py [<src.ifc> <dst.ifc>]

    Without arguments the Type B / Type D case inputs are processed.
    """
    pairs = [
        (Path("inputs/case_study/typeb.ifc"), Path("inputs/case_study/typeb_nomep.ifc")),
        (Path("inputs/case_study/typed.ifc"), Path("inputs/case_study/typed_nomep.ifc")),
    ]
    if len(argv) >= 2:
        pairs = [(Path(argv[0]), Path(argv[1]))]
    logs = {}
    for src, dst in pairs:
        print("stripping", src.name, "->", dst.name, flush=True)
        logs[src.stem] = strip_mep(src, dst)
        l = logs[src.stem]
        print(f"  done in {l['seconds']}s; products {sum(l['product_counts_before'].values())} -> "
              f"{sum(l['product_counts_after'].values())}; gids preserved={l['building_element_gids_preserved']}",
              flush=True)
    out = Path("outputs/typebd_mep_strip_log.json") if len(argv) < 2 else Path(argv[1]).with_suffix(".mep_strip_log.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    print("log", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
