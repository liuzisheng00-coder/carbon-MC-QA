# -*- coding: utf-8 -*-
"""Align Type B / Type D IFC models to the Type A case model.

The Type A case model (typea1_full.ifc / typea1_full_en.ifc) carries, on top of the
Revit export, one specified material per component added during material completion
(IfcRelAssociatesMaterial named 'WeaklySupervisedMaterialCompletion*'). This script
transfers those decisions to Type B / Type D by component type:

1. Filter: components whose type has no counterpart in Type A at type, family or
   class level are removed (furniture). Components the modeller confirmed to be
   misclassified (USER_CONFIRMED_RECLASS) are re-classed instead of removed.
2. Material completion: for every remaining component the completion material that
   Type A assigns to the same (class, type, Revit material) bucket is attached as a
   per-component IfcRelAssociatesMaterial, exactly as in Type A. Family- and
   class-level buckets and keyword rules act as fallbacks for B/D-only types.
3. Translation: names of products, types and materials are translated with the same
   glossary that produced typea1_full_en.ifc (extended for B/D-only terms), so the
   three modules share one vocabulary.

Outputs: <stem>_aligned.ifc (Chinese names) and <stem>_aligned_en.ifc (English).
GlobalIds, geometry, quantities and property sets are untouched.

Usage: python align_ifc_to_typea.py <src.ifc> <out_stem>
       python align_ifc_to_typea.py            (Type B and Type D case inputs)
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import ifcopenshell
import ifcopenshell.api.root
import ifcopenshell.guid

sys.path.insert(0, str(Path(__file__).resolve().parent / "ifc-kg" / "dm2c_pipeline"))
import translate_typea1_ifc_names as glossary  # noqa: E402

REFERENCE_ZH = Path("typea1_full.ifc")
REFERENCE_EN = Path("typea1_full_en.ifc")
COMPLETION_PREFIX = "WeaklySupervisedMaterialCompletion"
TRANSFER_REL_NAME = "TypeAReferenceMaterialCompletion"
# Deterministic GlobalIds for the added relationships, so re-running the script on the
# same input reproduces the same file byte for byte.
GUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "dm2c/align_ifc_to_typea/material-completion")


def deterministic_guid(*parts: str) -> str:
    return ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, "|".join(parts)).hex)

CLASSES = ("IfcWall", "IfcSlab", "IfcBeam", "IfcColumn", "IfcDoor", "IfcWindow",
           "IfcBuildingElementProxy", "IfcFurniture", "IfcMember", "IfcPlate",
           "IfcCovering", "IfcRailing", "IfcStair")

# Terms that occur in Type B / D but not in Type A. Longest first; merged in front of
# the Type A glossary so multi-word terms win over their parts.
EXTRA_TRANSLATIONS = {
    "隔板（修改参数）": "Partition Board (Modified Parameters)",
    "新型才窗扣件套_": "New Profile_Window Trim Clip Cover_",
    "新型才门窗套3_": "New Profile_Door/Window Trim 3_",
    "新型才门窗套2_": "New Profile_Door/Window Trim 2_",
    "B户型新窗3_": "Type-B New Window 3_",
    "PKC02（新）": "PKC02 (New)",
    "BTP（新）": "BTP (New)",
    "常规模型": "Generic Model",
    "毛巾架": "Towel Rack",
    "衣柜": "Wardrobe",
    "（新）": " (New)",
}

# Keyword fallbacks for buckets Type A never saw (regex on the type text, completion material).
# STRONG rules name the board / frame material in the type itself and outrank the
# class-level bucket, which is the weakest Type A evidence. WEAK rules only apply when
# no Type A bucket matches at all.
STRONG_KEYWORD_RULES = (
    (re.compile(r"硅钙板"), "Calcium silicate board"),
    (re.compile(r"防火板"), "Fire-resistant board"),
    (re.compile(r"岩棉"), "Rock wool"),
    (re.compile(r"厕所内贴面"), "Fired perforated brick"),
    (re.compile(r"常规 - 145"), "Crystalline waterproofing coating"),
    (re.compile(r"龙骨|槽钢"), "Galvanized steel"),
)
KEYWORD_RULES = (
    (re.compile(r"外墙板|扁管"), "Aluminium alloy"),
    (re.compile(r"地胶"), "PVC resilient flooring"),
    (re.compile(r"门槛石"), "Steel"),
    (re.compile(r"防火门|FM0"), "Galvanized steel sheet fire door"),
)
# Class-level keyword fallbacks that depend on the Revit material state.
UNNAMED_CLASS_RULES = {
    "IfcWindow": "Tempered glass",
}
DEFAULT_SLAB_RULE = ("默认楼板", "Galvanized profiled steel sheet")

# Component types removed because Type A has no counterpart at any level.
FILTER_OUT = (
    ("IfcFurniture", re.compile(r".*")),
)

# Components whose IFC class the modeller confirmed to be wrong. Type B's structural
# concrete floor slab (2.198 m3, ~20 m2 x 110 mm) was exported as a generic-model proxy
# with no material; it is re-classed as a floor slab and given Type A's structural slab
# material so that the pipeline treats it like Type A's "Floor Slab:110mm".
USER_CONFIRMED_RECLASS = {
    "3czbugqbT86PTcmnme1jZ_": {
        "ifc_class": "IfcSlab",
        "predefined_type": "FLOOR",
        "completion": "Concrete, Cast-in-Place Gray",
        "note": "User-confirmed structural concrete floor slab modelled as IfcBuildingElementProxy (常规模型 2)",
    },
}


def merged_glossary() -> dict[str, str]:
    merged = dict(EXTRA_TRANSLATIONS)
    for k, v in glossary.TRANSLATIONS.items():
        merged.setdefault(k, v)
    return merged


def translate(text: str | None, table: dict[str, str]) -> str:
    if not text:
        return text or ""
    out = text
    for zh, en in table.items():
        if zh in out:
            out = out.replace(zh, en)
    return out


def elements(model):
    out = []
    for c in CLASSES:
        out.extend(model.by_type(c))
    return out


def type_object(el):
    for rel in getattr(el, "IsTypedBy", []) or []:
        return rel.RelatingType
    return None


def association_materials(entity, *, completion: bool):
    """Material names on `entity` from completion or from Revit associations."""
    names = []
    for rel in getattr(entity, "HasAssociations", []) or []:
        if not rel.is_a("IfcRelAssociatesMaterial"):
            continue
        is_completion = bool(rel.Name) and (rel.Name.startswith(COMPLETION_PREFIX) or rel.Name == TRANSFER_REL_NAME)
        if is_completion != completion:
            continue
        rm = rel.RelatingMaterial
        if rm is None:
            continue
        if rm.is_a("IfcMaterial"):
            names.append(rm.Name or "<Unnamed>")
        elif rm.is_a("IfcMaterialConstituentSet"):
            for c in rm.MaterialConstituents or []:
                names.append(getattr(getattr(c, "Material", None), "Name", None) or "<Unnamed>")
        elif rm.is_a("IfcMaterialLayerSetUsage") and rm.ForLayerSet:
            for layer in rm.ForLayerSet.MaterialLayers or []:
                names.append(getattr(getattr(layer, "Material", None), "Name", None) or "<Unnamed>")
        else:
            names.append(getattr(rm, "Name", None) or f"<{rm.is_a()}>")
    return names


def revit_key(el, table: dict[str, str]) -> tuple[str, ...]:
    names = association_materials(el, completion=False)
    if not names:
        t = type_object(el)
        if t is not None:
            names = association_materials(t, completion=False)
    return tuple(sorted({translate(n, table) for n in names}))


class ReferenceRules:
    """Completion rules mined from the Type A zh/en pair."""

    def __init__(self, zh_path: Path, en_path: Path, table: dict[str, str]):
        zh = ifcopenshell.open(str(zh_path))
        en = ifcopenshell.open(str(en_path))
        zh_by_gid = {e.GlobalId: e for e in elements(zh)}
        en_by_gid = {e.GlobalId: e for e in elements(en)}
        self.by_type: dict[tuple, Counter] = defaultdict(Counter)
        self.by_family: dict[tuple, Counter] = defaultdict(Counter)
        self.by_class: dict[tuple, Counter] = defaultdict(Counter)
        self.type_en: dict[tuple[str, str], str] = {}
        self.types_zh: set[tuple[str, str]] = set()
        self.families_zh: set[tuple[str, str]] = set()
        self.family_en: dict[tuple[str, str], str] = {}
        for gid, z in zh_by_gid.items():
            e = en_by_gid.get(gid)
            if e is None:
                continue
            cls = z.is_a()
            type_zh = z.ObjectType or ""
            family_zh = type_zh.split(":")[0]
            key = revit_key(e, table)  # already English in the en model
            completion = frozenset(association_materials(e, completion=True))
            self.by_type[(cls, type_zh, key)][completion] += 1
            self.by_family[(cls, family_zh, key)][completion] += 1
            self.by_class[(cls, key)][completion] += 1
            self.type_en[(cls, type_zh)] = e.ObjectType or ""
            self.types_zh.add((cls, type_zh))
            self.families_zh.add((cls, family_zh))
            self.family_en[(cls, family_zh)] = (e.ObjectType or "").split(":")[0]

    @staticmethod
    def _decide(counter: Counter, min_share: float):
        total = sum(counter.values())
        if not total:
            return None
        winner, n = counter.most_common(1)[0]
        if n / total < min_share:
            return None
        return winner, n, total

    def lookup(self, cls: str, type_zh: str, key: tuple[str, ...], levels=("type", "family", "class")):
        family_zh = type_zh.split(":")[0]
        candidates = {
            "type": (self.by_type, (cls, type_zh, key), 0.5),
            "family": (self.by_family, (cls, family_zh, key), 0.5),
            "class": (self.by_class, (cls, key), 0.6),
        }
        for level in levels:
            table, k, share = candidates[level]
            counter = table.get(k)
            if not counter:
                continue
            decided = self._decide(counter, share)
            if decided is None:
                continue
            winner, n, total = decided
            return level, sorted(winner), n, total
        return None


def strong_keyword_completion(type_zh: str) -> tuple[str, list[str]] | None:
    for pattern, material in STRONG_KEYWORD_RULES:
        if pattern.search(type_zh):
            return f"keyword:{pattern.pattern}", [material]
    return None


def keyword_completion(cls: str, type_zh: str, key: tuple[str, ...]) -> tuple[str, list[str]] | None:
    text = type_zh
    if cls == "IfcSlab" and (DEFAULT_SLAB_RULE[0] in " ".join(key) or "Default Floor Slab" in key):
        return "keyword:default-slab", [DEFAULT_SLAB_RULE[1]]
    for pattern, material in KEYWORD_RULES:
        if pattern.search(text):
            if material == "Aluminium alloy" and key and key != ("<Unnamed>",):
                # Type A only completes aluminium on the unnamed panels
                return "keyword:panel-named", []
            return f"keyword:{pattern.pattern}", [material]
    if "<Unnamed>" in key and cls in UNNAMED_CLASS_RULES:
        return "keyword:class-unnamed", [UNNAMED_CLASS_RULES[cls]]
    if cls == "IfcWindow":
        return "keyword:window", [UNNAMED_CLASS_RULES["IfcWindow"]]
    return None


def decide_completion(rules: "ReferenceRules", cls: str, type_zh: str, key: tuple[str, ...]):
    """Return (level, materials, basis). Order: Type A type/family bucket, strong keyword,
    Type A class bucket, weak keyword."""
    decision = rules.lookup(cls, type_zh, key, levels=("type", "family"))
    if decision is not None:
        level, mats, n, total = decision
        return level, mats, f"Type A {level}-level rule; support {n}/{total} components; bucket class={cls} type={type_zh} revit={list(key)}"
    kw = strong_keyword_completion(type_zh)
    if kw is not None:
        level, mats = kw
        return level, mats, f"{level} rule (material named in type); bucket class={cls} type={type_zh} revit={list(key)}"
    decision = rules.lookup(cls, type_zh, key, levels=("class",))
    if decision is not None:
        level, mats, n, total = decision
        return level, mats, f"Type A {level}-level rule; support {n}/{total} components; bucket class={cls} type={type_zh} revit={list(key)}"
    kw = keyword_completion(cls, type_zh, key)
    if kw is not None:
        level, mats = kw
        return level, mats, f"{level} fallback; bucket class={cls} type={type_zh} revit={list(key)}"
    return "none", [], ""


def align(src: Path, out_stem: Path, rules: ReferenceRules, table: dict[str, str]) -> dict:
    model = ifcopenshell.open(str(src))
    owner_history = (model.by_type("IfcOwnerHistory") or [None])[0]
    log: dict = {"source": str(src), "removed": [], "completion": Counter(), "rule_levels": Counter(),
                 "per_type": {}, "unmatched_types_kept": Counter(), "translated_terms": Counter()}

    # 1. filter
    for el in list(elements(model)):
        cls, ot = el.is_a(), el.ObjectType or el.Name or ""
        for fcls, pattern in FILTER_OUT:
            if cls == fcls and pattern.search(ot):
                log["removed"].append({"class": cls, "type": ot, "gid": el.GlobalId,
                                       "materials": association_materials(el, completion=False)})
                ifcopenshell.api.root.remove_product(model, product=el)
                break

    # 1b. user-confirmed re-classification (class change keeps GlobalId, geometry, quantities)
    log["reclassified"] = []
    forced_completion: dict[str, tuple[str, str]] = {}
    for gid, spec in USER_CONFIRMED_RECLASS.items():
        try:
            el = model.by_guid(gid)
        except Exception:
            continue
        before = el.is_a()
        new_el = ifcopenshell.api.root.reassign_class(
            model, product=el, ifc_class=spec["ifc_class"], predefined_type=spec["predefined_type"]
        )
        log["reclassified"].append({"gid": gid, "from": before, "to": new_el.is_a(),
                                    "predefined_type": new_el.PredefinedType, "type": new_el.ObjectType,
                                    "type_object": type_object(new_el).is_a() if type_object(new_el) else None,
                                    "note": spec["note"]})
        forced_completion[gid] = (spec["completion"], spec["note"])

    # 2. completion transfer
    materials_by_name = {m.Name: m for m in model.by_type("IfcMaterial") if m.Name}

    def material(name: str):
        if name not in materials_by_name:
            materials_by_name[name] = model.create_entity("IfcMaterial", Name=name)
        return materials_by_name[name]

    for el in elements(model):
        cls, type_zh = el.is_a(), el.ObjectType or ""
        key = revit_key(el, table)
        if el.GlobalId in forced_completion:
            name, note = forced_completion[el.GlobalId]
            level, mats, basis = "user-confirmed", [name], f"{note}; bucket class={cls} type={type_zh} revit={list(key)}"
        else:
            level, mats, basis = decide_completion(rules, cls, type_zh, key)
        if (cls, type_zh) not in rules.types_zh:
            log["unmatched_types_kept"][f"{cls} | {type_zh}"] += 1
        log["rule_levels"][level] += 1
        row = log["per_type"].setdefault(f"{cls} | {type_zh}", {"count": 0, "levels": Counter(), "completion": Counter(), "revit": Counter()})
        row["count"] += 1
        row["levels"][level] += 1
        row["revit"][", ".join(key) or "-"] += 1
        existing = set(association_materials(el, completion=True))
        for name in mats:
            if name in existing:
                continue
            model.create_entity(
                "IfcRelAssociatesMaterial",
                GlobalId=deterministic_guid(el.GlobalId, name),
                OwnerHistory=owner_history,
                Name=TRANSFER_REL_NAME,
                Description=basis,
                RelatedObjects=[el],
                RelatingMaterial=material(name),
            )
            log["completion"][name] += 1
            row["completion"][name] += 1

    zh_out = out_stem.with_name(out_stem.name + "_aligned.ifc")
    model.write(str(zh_out))

    # 3. translation with the Type A glossary (text level, same as typea1_full_en.ifc)
    glossary.TRANSLATIONS = table
    source = zh_out.read_text(encoding="utf-8")
    replacements: Counter = Counter()
    untranslated: set[str] = set()
    translated = glossary.ENTITY_RE.sub(lambda m: glossary.translate_entity(m, replacements, untranslated), source)
    if source.count("#") != translated.count("#"):
        raise RuntimeError("entity count changed during translation")
    en_out = out_stem.with_name(out_stem.name + "_aligned_en.ifc")
    en_out.write_text(translated, encoding="utf-8", newline="\n")
    log["translated_terms"] = dict(replacements)
    log["untranslated_targets"] = sorted(untranslated)
    log["zh_output"] = str(zh_out)
    log["en_output"] = str(en_out)

    # verification
    check = ifcopenshell.open(str(en_out))
    log["en_reopen_products"] = len(check.by_type("IfcProduct"))
    log["en_materials"] = sorted(m.Name or "<Unnamed>" for m in check.by_type("IfcMaterial"))
    log["en_chinese_left_in_products"] = sorted({
        (e.is_a(), e.Name) for e in elements(check) if glossary.contains_chinese(e.Name or "") or glossary.contains_chinese(e.ObjectType or "")
    })[:20]
    log["completion"] = dict(log["completion"])
    log["rule_levels"] = dict(log["rule_levels"])
    log["unmatched_types_kept"] = dict(log["unmatched_types_kept"])
    for row in log["per_type"].values():
        row["completion"] = dict(row["completion"])
        row["revit"] = dict(row["revit"])
        row["levels"] = dict(row["levels"])
    return log


def main(argv: list[str]) -> int:
    table = merged_glossary()
    rules = ReferenceRules(REFERENCE_ZH, REFERENCE_EN, table)
    jobs = [
        (Path("inputs/case_study/typeb_nomep.ifc"), Path("inputs/case_study/typeb")),
        (Path("inputs/case_study/typed_nomep.ifc"), Path("inputs/case_study/typed")),
    ]
    if len(argv) >= 2:
        jobs = [(Path(argv[0]), Path(argv[1]))]
    logs = {}
    for src, stem in jobs:
        print("aligning", src.name, flush=True)
        logs[stem.name] = align(src, stem, rules, table)
        l = logs[stem.name]
        print(f"  removed {len(l['removed'])}; completion {l['completion']}; levels {l['rule_levels']}; "
              f"chinese left {len(l['en_chinese_left_in_products'])}", flush=True)
    out = Path("outputs/typebd_align_to_typea_log.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(logs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("log", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
