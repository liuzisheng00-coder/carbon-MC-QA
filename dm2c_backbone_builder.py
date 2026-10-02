#!/usr/bin/env python3
"""
DM2C IFC-derived semantic graph backbone constructor.
Section 3.2 implementation: IFC extraction -> semantic interface -> agent-ready targets.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    import ifcopenshell
except ImportError:
    ifcopenshell = None

try:
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, RDFS, XSD
except ImportError:
    Graph = None
    Literal = None
    Namespace = None
    URIRef = None
    RDF = None
    RDFS = None
    XSD = None


BIM_NS = "http://example.org/bim#"
MC_NS = "http://example.org/mc#"
CARBON_NS = "http://example.org/carbon#"
ONTO_NS = "http://example.org/onto#"
INST_NS = "http://example.org/inst#"


DEFAULT_IFC_ELEMENT_TYPES = [
    "IfcWall",
    "IfcWallStandardCase",# 这个是不是和其他的不在一个层次啊
    "IfcBeam",
    "IfcColumn",
    "IfcSlab",
    "IfcDoor",
    "IfcWindow",
    "IfcBuildingElementProxy",
]

#去掉所有空白(不只是首尾)
def normalize_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", "", (text or "").strip().casefold())

#把 rdflib Literal、IFC 数值、None、字符串数字全都归一化成 float。
def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default
#用于生成节点/URI 的 id 段。节点 id 是内部标识,不直接给人看,这里牺牲可读性保一致性是对的

def slugify(text: str, max_len: int = 80) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", str(text)).strip("_")
    return (token or "x")[:max_len]

#三个 sanitizer 服务于 Cypher/SPARQL 的标识符约束(不能以数字开头、不能含特殊字符)。
def sanitize_key(text: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_]", "_", str(text)).strip("_")
    if not key:
        key = "value"
    if key[0].isdigit():
        key = f"p_{key}"
    return key


def sanitize_label(text: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_]", "_", str(text)).strip("_")
    if not label:
        label = "Entity"
    if label[0].isdigit():
        label = f"L_{label}"
    return label


def sanitize_rel_type(text: str) -> str:
    rel = re.sub(r"[^A-Za-z0-9_]", "_", str(text)).strip("_").upper()
    if not rel:
        rel = "RELATED_TO"
    if rel[0].isdigit():
        rel = f"R_{rel}"
    return rel

#递归 list,非 list 非 scalar 的 dict 直接 json.dumps 成字符串嵌进 Cypher。
def cypher_key(text: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", text):
        return text
    return f"`{text.replace('`', '``')}`"


def cypher_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else "null"
    if isinstance(value, list):
        return "[" + ", ".join(cypher_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def first_non_empty(values: Iterable[str]) -> str:
    for value in values:
        if str(value or "").strip():
            return str(value).strip()
    return ""

#<Unnamed> / < material > / _UNNAMED_ 都会被剥成 unnamed,命中。中文和英文 placeholder 并存(上次加的),覆盖常见 Revit/Tekla 导出的 BIM 模型陋习。注意只做等值判断,不做 in——"混凝土材料"不会被误判,因为它等于 "混凝土材料",不在 set 里。
def is_placeholder_material_name(text: Optional[str]) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    norm = normalize_text(raw).replace("<", "").replace(">", "").replace("_", "")
    placeholders = {
        "unnamed",
        "none",
        "null",
        "n/a",
        "na",
        "material",
        "ifcmaterial",
        "x",
        "未命名",
        "材料",
        "默认",
        "默认材料",
        "未定义",
        "无",
    }
    return norm in placeholders
#全文件唯一一处决定 material 能否继续走碳核算的二元判据,抽成函数避免两处实现漂移。一个名字有 = resolved,空名字 = unresolved,规则极简。


def material_candidate_status(material_name: Optional[str]) -> str:
    return "resolved" if str(material_name or "").strip() else "unresolved"

#4. 数据类 ExtractedMaterial / ExtractedElement(156–184 行)
#提取模板

@dataclass
class ExtractedMaterial:
    name: str
    source: str
    explicit: bool
    fallback_material: bool = False
    thickness: Optional[float] = None
    layer_order: Optional[int] = None
    confidence: float = 1.0
    evidence_fields: List[str] = field(default_factory=list)


@dataclass
class ExtractedElement:
    global_id: str
    ifc_type: str
    name: str
    object_type: str
    predefined_type: str
    tag: str
    type_object_name: str
    type_object_ifc_type: str
    type_object_global_id: str
    storey_name: str
    properties: Dict[str, Any]
    quantities: Dict[str, float]
    quantity_units: Dict[str, Dict[str, Any]]
    materials: List[ExtractedMaterial]

#内存图容器(
class DM2CGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self._edge_seen: Set[Tuple[str, str, str, str]] = set()

    def add_node(
        self,
        node_id: str,
        labels: List[str],
        domain: str,
        semantic_origin: str,
        reasoning_role: str,
        props: Optional[Dict[str, Any]] = None,
    ) -> None:
        clean_labels = sorted({sanitize_label(x) for x in labels if x})
        clean_props: Dict[str, Any] = {}
        for key, value in (props or {}).items():
            if value is None or value == "":
                continue
            clean_props[sanitize_key(key)] = value

        if node_id not in self.nodes:
            self.nodes[node_id] = {
                "id": node_id,
                "labels": clean_labels,
                "domain": domain,
                "semanticOrigin": semantic_origin,
                "reasoningRole": reasoning_role,
                "props": clean_props,
            }
            return

        node = self.nodes[node_id]
        node["labels"] = sorted(set(node["labels"]) | set(clean_labels))
        node["props"].update(clean_props)
        # keep original domain/origin/role if already present, otherwise fill.
        if not node.get("domain"):
            node["domain"] = domain
        if not node.get("semanticOrigin"):
            node["semanticOrigin"] = semantic_origin
        if not node.get("reasoningRole"):
            node["reasoningRole"] = reasoning_role

    def add_edge(
        self,
        src: str,
        tgt: str,
        rel_type: str,
        semantic_origin: str,
        inference_rule: str,
        props: Optional[Dict[str, Any]] = None,
    ) -> None:
        rel = sanitize_rel_type(rel_type)
        clean_props: Dict[str, Any] = {}
        for key, value in (props or {}).items():
            if value is None or value == "":
                continue
            clean_props[sanitize_key(key)] = value

        signature = (src, rel, tgt, json.dumps(clean_props, ensure_ascii=False, sort_keys=True))
        if signature in self._edge_seen:
            return
        self._edge_seen.add(signature)
        self.edges.append(
            {
                "src": src,
                "tgt": tgt,
                "type": rel,
                "semanticOrigin": semantic_origin,
                "inferenceRule": inference_rule,
                "props": clean_props,
            }
        )

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    def node_by_label(self, label: str) -> List[Dict[str, Any]]:
        return [n for n in self.nodes.values() if label in n.get("labels", [])]


class OntologySchemaReader:
    """
    Lightweight ontology reader for Section 3.2 backbone mode.
    It only provides schema/normalization hints and must not instantiate
    production chains or carbon calculation facts.

    Note:
    Carbon-related predicates (for example, forMaterialKeyword) are used
    only as lexical normalization hints for IFC material normalization.
    No emission factor value is bound in Section 3.2.
    """

    def __init__(self, ontology_path: Path):
        if Graph is None:
            raise RuntimeError("rdflib is required to parse ontology.ttl")
        self.ontology_path = ontology_path
        self.graph = Graph()
        self.graph.parse(str(ontology_path), format="turtle")
        self.material_keywords = self._collect_material_keywords()
        self.relation_names = self._collect_relation_names()
        self.class_labels = self._collect_class_labels()

    @staticmethod
    def _local_name(uri: Any) -> str:
        text = str(uri)
        if "#" in text:
            return text.split("#")[-1]
        return text.rsplit("/", 1)[-1]

    def _collect_material_keywords(self) -> List[str]:
        keys: Set[str] = set()
        onto_keyword = URIRef(f"{ONTO_NS}materialKeyword")
        carbon_keyword = URIRef(f"{ONTO_NS}forMaterialKeyword")
        factor_keyword = URIRef(f"{CARBON_NS}forMaterialKeyword")

        for pred in [onto_keyword, carbon_keyword, factor_keyword]:
            for obj in self.graph.objects(None, pred):
                token = str(obj or "").strip()
                if token:
                    keys.add(token)

        # Keep labels for IfcMaterial-like classes as fallback lexical keys.
        for subj in self.graph.subjects(RDF.type, URIRef(f"{BIM_NS}IfcMaterial")):
            lbl = str(self.graph.value(subj, RDFS.label) or "").strip()
            if lbl:
                keys.add(lbl)

        # Additional safety: include template-level keywords if present in ontology.
        for subj in self.graph.subjects(None, onto_keyword):
            token = str(self.graph.value(subj, onto_keyword) or "").strip()
            if token:
                keys.add(token)

        return sorted(keys, key=lambda x: len(normalize_text(x)), reverse=True)

    def _collect_relation_names(self) -> List[str]:
        rels: Set[str] = set()
        for pred in self.graph.predicates():
            rels.add(self._local_name(pred))
        return sorted(rels)

    def _collect_class_labels(self) -> Dict[str, str]:
        rows: Dict[str, str] = {}
        for cls in self.graph.subjects(RDF.type, RDFS.Class):
            uri = str(cls)
            label = str(self.graph.value(cls, RDFS.label) or self._local_name(cls))
            rows[uri] = label
        return rows

    def detect_material_keywords(self, text: str) -> List[str]:
        text_norm = normalize_text(text)
        matched: List[str] = []
        for kw in self.material_keywords:
            if normalize_text(kw) and normalize_text(kw) in text_norm:
                matched.append(kw)
        return sorted(set(matched), key=lambda x: len(normalize_text(x)), reverse=True)


class IFCExtractor:
    def __init__(self, ifc_path: Path, ontology: Any):
        if ifcopenshell is None:
            raise RuntimeError("ifcopenshell is required")
        self.ifc = ifcopenshell.open(str(ifc_path))
        self.ifc_path = ifc_path
        self.ontology = ontology

        self.material_assoc: Dict[str, List[ExtractedMaterial]] = defaultdict(list)
        self.type_material_assoc: Dict[str, List[ExtractedMaterial]] = defaultdict(list)
        self.property_map: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.type_property_map: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.quantity_map: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.type_quantity_map: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.quantity_units: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self.type_quantity_units: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self.storey_map: Dict[str, str] = {}
        self.type_map: Dict[str, Dict[str, str]] = {}
        self.project_units: List[Dict[str, Any]] = self._extract_project_units()
        self._build_caches()

    @staticmethod
    def _entity_key(entity: Any) -> str:
        gid = str(getattr(entity, "GlobalId", "") or "")
        if gid:
            return gid
        ent_id = getattr(entity, "id", None)
        if callable(ent_id):
            try:
                return f"{entity.is_a()}::{ent_id()}"
            except Exception:
                pass
        return ""

    @staticmethod
    def _si_unit_symbol(name: str, prefix: str) -> str:
        n = str(name or "").upper()
        p = str(prefix or "").upper()
        if n == "METRE":
            return {"MILLI": "mm", "CENTI": "cm", "DECI": "dm", "KILO": "km"}.get(p, "m")
        if n == "SQUARE_METRE":
            return {"MILLI": "mm2", "CENTI": "cm2", "DECI": "dm2", "KILO": "km2"}.get(p, "m2")
        if n == "CUBIC_METRE":
            return {"MILLI": "mm3", "CENTI": "cm3", "DECI": "dm3", "KILO": "km3"}.get(p, "m3")
        if n == "GRAM":
            return {"KILO": "kg", "MILLI": "mg"}.get(p, "g")
        if n == "SECOND":
            return "s"
        if n == "MINUTE":
            return "min"
        if n == "HOUR":
            return "h"
        if n == "WATT":
            return "W"
        if n == "JOULE":
            return "J"
        if n == "PASCAL":
            return "Pa"
        return str(name or "").lower() or "unit"

    def _extract_project_units(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        projects = self.ifc.by_type("IfcProject")
        if not projects:
            return rows
        unit_assignment = getattr(projects[0], "UnitsInContext", None)
        if unit_assignment is None:
            return rows
        for unit in getattr(unit_assignment, "Units", []) or []:
            unit_type = str(getattr(unit, "UnitType", "") or "")
            if unit.is_a("IfcSIUnit"):
                prefix = str(getattr(unit, "Prefix", "") or "")
                name = str(getattr(unit, "Name", "") or "")
                rows.append(
                    {
                        "unitType": unit_type,
                        "unitClass": "IfcSIUnit",
                        "name": name,
                        "prefix": prefix,
                        "symbol": self._si_unit_symbol(name, prefix),
                        "source": "ifc_project_unit_assignment",
                    }
                )
                continue
            if unit.is_a("IfcDerivedUnit"):
                elements = []
                for elem in getattr(unit, "Elements", []) or []:
                    base = getattr(elem, "Unit", None)
                    base_name = str(getattr(base, "Name", "") or "")
                    base_prefix = str(getattr(base, "Prefix", "") or "")
                    exponent = getattr(elem, "Exponent", None)
                    elements.append(
                        {
                            "baseClass": str(base.is_a()) if base is not None else "",
                            "baseName": base_name,
                            "basePrefix": base_prefix,
                            "baseSymbol": self._si_unit_symbol(base_name, base_prefix) if base_name else "",
                            "exponent": exponent,
                        }
                    )
                derived_name = str(getattr(unit, "UserDefinedType", "") or "")
                rows.append(
                    {
                        "unitType": unit_type,
                        "unitClass": "IfcDerivedUnit",
                        "name": derived_name,
                        "prefix": "",
                        "symbol": derived_name or unit_type.lower() or "derived_unit",
                        "derivedElements": elements,
                        "source": "ifc_project_unit_assignment",
                    }
                )
                continue
            if unit.is_a("IfcConversionBasedUnit"):
                name = str(getattr(unit, "Name", "") or "")
                conversion = None
                based_si_name = ""
                based_si_prefix = ""
                based_si_symbol = ""
                try:
                    cf = getattr(unit, "ConversionFactor", None)
                    vc = getattr(cf, "ValueComponent", None)
                    wrapped = getattr(vc, "wrappedValue", None)
                    if wrapped is not None:
                        conversion = safe_float(wrapped)
                    base_unit = getattr(cf, "UnitComponent", None)
                    if base_unit is not None and base_unit.is_a("IfcSIUnit"):
                        based_si_name = str(getattr(base_unit, "Name", "") or "")
                        based_si_prefix = str(getattr(base_unit, "Prefix", "") or "")
                        based_si_symbol = self._si_unit_symbol(based_si_name, based_si_prefix)
                except Exception:
                    conversion = None
                rows.append(
                    {
                        "unitType": unit_type,
                        "unitClass": "IfcConversionBasedUnit",
                        "name": name,
                        "prefix": "",
                        "symbol": name or "unit",
                        "conversionFactor": conversion,
                        "baseUnitName": based_si_name,
                        "baseUnitPrefix": based_si_prefix,
                        "baseUnitSymbol": based_si_symbol,
                        "source": "ifc_project_unit_assignment",
                    }
                )
                continue
            rows.append(
                {
                    "unitType": unit_type,
                    "unitClass": str(unit.is_a()),
                    "name": str(getattr(unit, "Name", "") or ""),
                    "prefix": str(getattr(unit, "Prefix", "") or ""),
                    "symbol": str(getattr(unit, "Name", "") or "unit"),
                    "source": "ifc_project_unit_assignment",
                }
            )
        return rows

    @staticmethod
    def _clone_material(mat: ExtractedMaterial, source_override: Optional[str] = None) -> ExtractedMaterial:
        return ExtractedMaterial(
            name=mat.name,
            source=source_override or mat.source,
            explicit=mat.explicit,
            fallback_material=mat.fallback_material,
            thickness=mat.thickness,
            layer_order=mat.layer_order,
            confidence=mat.confidence,
            evidence_fields=list(mat.evidence_fields),
        )

    @staticmethod
    def _unique_materials(rows: List[ExtractedMaterial]) -> List[ExtractedMaterial]:
        unique: List[ExtractedMaterial] = []
        seen: Set[Tuple[str, Optional[float], Optional[int], str, bool]] = set()
        for row in rows:
            sig = (row.name, row.thickness, row.layer_order, row.source, row.explicit)
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(row)
        return unique

    @staticmethod
    def _expected_unit_type_for_quantity(qty: Any) -> str:
        if qty.is_a("IfcQuantityLength"):
            return "LENGTHUNIT"
        if qty.is_a("IfcQuantityArea"):
            return "AREAUNIT"
        if qty.is_a("IfcQuantityVolume"):
            return "VOLUMEUNIT"
        if qty.is_a("IfcQuantityWeight"):
            return "MASSUNIT"
        if qty.is_a("IfcQuantityCount"):
            return "COUNTUNIT"
        return ""

    def _resolve_quantity_unit(self, qty: Any) -> Dict[str, Any]:
        defaults = {
            "IfcQuantityLength": "m",
            "IfcQuantityArea": "m2",
            "IfcQuantityVolume": "m3",
            "IfcQuantityWeight": "kg",
            "IfcQuantityCount": "unit",
        }
        qty_type = str(qty.is_a())
        expected_unit_type = self._expected_unit_type_for_quantity(qty)
        project_unit_index = {str(u.get("unitType", "") or ""): u for u in self.project_units}
        project_unit = project_unit_index.get(expected_unit_type)
        default_unit = defaults.get(qty_type, "unit")

        if project_unit:
            symbol = str(project_unit.get("symbol", "") or default_unit)
            return {
                "unit": symbol,
                "projectUnitType": expected_unit_type,
                "projectUnitClass": str(project_unit.get("unitClass", "") or ""),
                "projectUnitName": str(project_unit.get("name", "") or ""),
                "projectUnitPrefix": str(project_unit.get("prefix", "") or ""),
                "projectUnitConversionFactor": project_unit.get("conversionFactor", None),
                "projectUnitBaseSymbol": str(project_unit.get("baseUnitSymbol", "") or ""),
                "quantityUnitSource": "ifc_project_unit_assignment",
                "unitAssumption": f"{qty_type} interpreted using IFC project unit assignment.",
                "unitConfidence": 0.95,
                "unitInterpretationStatus": "from_project_unit_assignment",
            }

        return {
            "unit": default_unit,
            "projectUnitType": expected_unit_type,
            "projectUnitClass": "",
            "projectUnitName": "",
            "projectUnitPrefix": "",
            "projectUnitConversionFactor": None,
            "projectUnitBaseSymbol": "",
            "quantityUnitSource": "assumed_from_ifc_quantity_type",
            "unitAssumption": f"{qty_type} mapped to default unit because project assignment was unavailable.",
            "unitConfidence": 0.55,
            "unitInterpretationStatus": "assumed_from_ifc_quantity_type",
        }

    def _extract_quantity_value(self, qty: Any) -> Optional[float]:
        if qty.is_a("IfcQuantityLength"):
            return round(safe_float(getattr(qty, "LengthValue", 0.0), 0.0), 6)
        if qty.is_a("IfcQuantityArea"):
            return round(safe_float(getattr(qty, "AreaValue", 0.0), 0.0), 6)
        if qty.is_a("IfcQuantityVolume"):
            return round(safe_float(getattr(qty, "VolumeValue", 0.0), 0.0), 6)
        if qty.is_a("IfcQuantityCount"):
            return round(safe_float(getattr(qty, "CountValue", 0.0), 0.0), 6)
        if qty.is_a("IfcQuantityWeight"):
            return round(safe_float(getattr(qty, "WeightValue", 0.0), 0.0), 6)
        return None

    def _extract_property_definition(self, pset: Any, key: str, is_type_level: bool) -> None:
        prop_map = self.type_property_map if is_type_level else self.property_map
        qty_map = self.type_quantity_map if is_type_level else self.quantity_map
        unit_map = self.type_quantity_units if is_type_level else self.quantity_units

        if pset.is_a("IfcPropertySet"):
            for prop in getattr(pset, "HasProperties", []) or []:
                if prop.is_a("IfcPropertySingleValue") and prop.NominalValue is not None:
                    wrapped = prop.NominalValue.wrappedValue
                    if isinstance(wrapped, (str, int, float, bool)):
                        prop_map[key][prop.Name] = wrapped
            return

        if pset.is_a("IfcElementQuantity"):
            for qty in getattr(pset, "Quantities", []) or []:
                q_value = self._extract_quantity_value(qty)
                if q_value is None:
                    continue
                qty_map[key][qty.Name] = q_value
                unit_map[key][qty.Name] = self._resolve_quantity_unit(qty)

    def _build_caches(self) -> None:
        # Materials from direct material associations (occurrence and type-level).
        for rel in self.ifc.by_type("IfcRelAssociatesMaterial"):
            mats = self._extract_materials(rel.RelatingMaterial)
            for obj in getattr(rel, "RelatedObjects", []) or []:
                obj_key = self._entity_key(obj)
                if not obj_key:
                    continue
                if obj.is_a("IfcTypeObject"):
                    tagged = [self._clone_material(m, source_override=f"{m.source}:type_object") for m in mats]
                    self.type_material_assoc[obj_key].extend(tagged)
                else:
                    self.material_assoc[obj_key].extend(mats)

        # Properties and quantities on occurrences and type-objects.
        for rel in self.ifc.by_type("IfcRelDefinesByProperties"):
            pset = rel.RelatingPropertyDefinition
            for obj in getattr(rel, "RelatedObjects", []) or []:
                obj_key = self._entity_key(obj)
                if not obj_key:
                    continue
                self._extract_property_definition(pset, obj_key, is_type_level=bool(obj.is_a("IfcTypeObject")))

        # Storey containment.
        for rel in self.ifc.by_type("IfcRelContainedInSpatialStructure"):
            structure_name = str(getattr(rel.RelatingStructure, "Name", "") or "")
            for elem in getattr(rel, "RelatedElements", []) or []:
                gid = self._entity_key(elem)
                if gid:
                    self.storey_map[gid] = structure_name

        # Type object mapping + type-level property/material fallback.
        for rel in self.ifc.by_type("IfcRelDefinesByType"):
            t_obj = getattr(rel, "RelatingType", None)
            if t_obj is None:
                continue
            t_key = self._entity_key(t_obj)
            if t_key:
                for pset in getattr(t_obj, "HasPropertySets", []) or []:
                    self._extract_property_definition(pset, t_key, is_type_level=True)

                for assoc in getattr(t_obj, "HasAssociations", []) or []:
                    if assoc is None or not assoc.is_a("IfcRelAssociatesMaterial"):
                        continue
                    type_mats = self._extract_materials(getattr(assoc, "RelatingMaterial", None))
                    tagged = [self._clone_material(m, source_override=f"{m.source}:type_object_association") for m in type_mats]
                    self.type_material_assoc[t_key].extend(tagged)

            for elem in getattr(rel, "RelatedObjects", []) or []:
                gid = self._entity_key(elem)
                if not gid:
                    continue
                self.type_map[gid] = {
                    "type_object_name": str(getattr(t_obj, "Name", "") or ""),
                    "type_object_ifc_type": str(t_obj.is_a()),
                    "type_object_global_id": str(getattr(t_obj, "GlobalId", "") or ""),
                    "type_object_key": t_key,
                }

    def _extract_materials(self, mat: Any) -> List[ExtractedMaterial]:
        rows: List[ExtractedMaterial] = []
        if mat is None:
            return rows

        def push(name: Optional[str], source: str, thickness: Optional[float], layer_order: Optional[int]) -> None:
            if not name:
                return
            rows.append(
                ExtractedMaterial(
                    name=str(name),
                    source=source,
                    explicit=True,
                    fallback_material=False,
                    thickness=round(safe_float(thickness), 6) if thickness is not None else None,
                    layer_order=layer_order,
                    confidence=1.0,
                    evidence_fields=[],
                )
            )

        if mat.is_a("IfcMaterialLayerSetUsage"):
            layers = getattr(mat.ForLayerSet, "MaterialLayers", []) or []
            for idx, layer in enumerate(layers, start=1):
                push(getattr(getattr(layer, "Material", None), "Name", None), "IfcMaterialLayerSetUsage", getattr(layer, "LayerThickness", None), idx)
        elif mat.is_a("IfcMaterialLayerSet"):
            layers = getattr(mat, "MaterialLayers", []) or []
            for idx, layer in enumerate(layers, start=1):
                push(getattr(getattr(layer, "Material", None), "Name", None), "IfcMaterialLayerSet", getattr(layer, "LayerThickness", None), idx)
        elif mat.is_a("IfcMaterialConstituentSet"):
            parts = getattr(mat, "MaterialConstituents", []) or []
            for idx, item in enumerate(parts, start=1):
                push(getattr(getattr(item, "Material", None), "Name", None), "IfcMaterialConstituentSet", None, idx)
        elif mat.is_a("IfcMaterialProfileSetUsage"):
            items = getattr(mat.ForProfileSet, "MaterialProfiles", []) or []
            for idx, item in enumerate(items, start=1):
                push(getattr(getattr(item, "Material", None), "Name", None), "IfcMaterialProfileSetUsage", None, idx)
        elif mat.is_a("IfcMaterialProfileSet"):
            items = getattr(mat, "MaterialProfiles", []) or []
            for idx, item in enumerate(items, start=1):
                push(getattr(getattr(item, "Material", None), "Name", None), "IfcMaterialProfileSet", None, idx)
        elif mat.is_a("IfcMaterialList"):
            items = getattr(mat, "Materials", []) or []
            for idx, item in enumerate(items, start=1):
                push(getattr(item, "Name", None), "IfcMaterialList", None, idx)
        elif mat.is_a("IfcMaterial"):
            push(getattr(mat, "Name", None), "IfcMaterial", None, None)

        unique: List[ExtractedMaterial] = []
        seen: Set[Tuple[str, Optional[float], Optional[int], str]] = set()
        for row in rows:
            sig = (row.name, row.thickness, row.layer_order, row.source)
            if sig not in seen:
                seen.add(sig)
                unique.append(row)
        return unique

    def _infer_material(self, ifc_type: str, name: str, object_type: str, reference: str, type_object_name: str) -> ExtractedMaterial:
        evidence_map = {
            "Reference": reference,
            "Name": name,
            "ObjectType": object_type,
            "typeObjectName": type_object_name,
            "ifcType": ifc_type,
        }
        evidence_fields = [k for k, v in evidence_map.items() if str(v or "").strip()]
        text = " ".join(str(v) for v in evidence_map.values() if str(v or "").strip())
        text_norm = normalize_text(text)

        matched_keywords: List[str] = []
        for kw in getattr(self.ontology, "material_keywords", []):
            if normalize_text(kw) and normalize_text(kw) in text_norm:
                matched_keywords.append(kw)

        if matched_keywords:
            matched_keywords.sort(key=lambda x: len(normalize_text(x)), reverse=True)
            mat_name = matched_keywords[0]
            confidence = 0.72
        else:
            mat_name = ""
            confidence = 0.20

        return ExtractedMaterial(
            name=mat_name,
            source="inferred_from_ifc_text",
            explicit=False,
            fallback_material=True,
            thickness=None,
            layer_order=None,
            confidence=confidence,
            evidence_fields=evidence_fields,
        )

    def _extract_spatial(self) -> Dict[str, Any]:
        projects = self.ifc.by_type("IfcProject")
        sites = self.ifc.by_type("IfcSite")
        buildings = self.ifc.by_type("IfcBuilding")
        storeys = self.ifc.by_type("IfcBuildingStorey")

        return {
            "project": {
                "name": str(getattr(projects[0], "Name", "Project") if projects else "Project"),
                "global_id": str(getattr(projects[0], "GlobalId", "") if projects else ""),
            },
            "site": {
                "name": str(getattr(sites[0], "Name", "Site") if sites else "Site"),
                "global_id": str(getattr(sites[0], "GlobalId", "") if sites else ""),
            },
            "building": {
                "name": str(getattr(buildings[0], "Name", "Building") if buildings else "Building"),
                "global_id": str(getattr(buildings[0], "GlobalId", "") if buildings else ""),
            },
            "storeys": [
                {
                    "name": str(getattr(storey, "Name", "") or ""),
                    "global_id": str(getattr(storey, "GlobalId", "") or ""),
                    "elevation": round(safe_float(getattr(storey, "Elevation", 0.0), 0.0), 3),
                }
                for storey in storeys
            ],
            "project_units": list(self.project_units),
        }

    def _extract_elements(self) -> List[ExtractedElement]:
        rows: List[ExtractedElement] = []
        seen_gid: Set[str] = set()

        for ifc_type in DEFAULT_IFC_ELEMENT_TYPES:
            for entity in self.ifc.by_type(ifc_type):
                gid = str(getattr(entity, "GlobalId", "") or "")
                if not gid or gid in seen_gid:
                    continue
                seen_gid.add(gid)

                name = str(getattr(entity, "Name", "") or "")
                object_type = str(getattr(entity, "ObjectType", "") or "")
                predefined = str(getattr(entity, "PredefinedType", "") or "")
                tag = str(getattr(entity, "Tag", "") or "")

                type_info = self.type_map.get(gid, {})
                type_object_name = str(type_info.get("type_object_name", "") or "")
                type_object_ifc_type = str(type_info.get("type_object_ifc_type", "") or "")
                type_object_global_id = str(type_info.get("type_object_global_id", "") or "")
                type_object_key = str(type_info.get("type_object_key", "") or "")

                properties = dict(self.type_property_map.get(type_object_key, {})) if type_object_key else {}
                properties.update(self.property_map.get(gid, {}))
                # Preserve key IFC-level fields in properties too for template matching evidence.
                if object_type:
                    properties.setdefault("ObjectType", object_type)
                if predefined:
                    properties.setdefault("PredefinedType", predefined)
                if tag:
                    properties.setdefault("Tag", tag)
                if type_object_name:
                    properties.setdefault("typeObjectName", type_object_name)
                properties.setdefault("Name", name)

                quantities = dict(self.type_quantity_map.get(type_object_key, {})) if type_object_key else {}
                quantities.update(self.quantity_map.get(gid, {}))
                quantity_units = dict(self.type_quantity_units.get(type_object_key, {})) if type_object_key else {}
                quantity_units.update(self.quantity_units.get(gid, {}))
                storey_name = str(self.storey_map.get(gid, "") or "")

                materials = list(self.material_assoc.get(gid, []))
                if type_object_key:
                    materials.extend(self.type_material_assoc.get(type_object_key, []))
                materials = self._unique_materials(materials)
                if not materials:
                    inferred = self._infer_material(
                        ifc_type=ifc_type,
                        name=name,
                        object_type=object_type,
                        reference=str(properties.get("Reference", "") or ""),
                        type_object_name=type_object_name,
                    )
                    materials = [inferred]

                rows.append(
                    ExtractedElement(
                        global_id=gid,
                        ifc_type=ifc_type,
                        name=name,
                        object_type=object_type,
                        predefined_type=predefined,
                        tag=tag,
                        type_object_name=type_object_name,
                        type_object_ifc_type=type_object_ifc_type,
                        type_object_global_id=type_object_global_id,
                        storey_name=storey_name,
                        properties=properties,
                        quantities=quantities,
                        quantity_units=quantity_units,
                        materials=materials,
                    )
                )
        return rows

    def extract(self) -> Dict[str, Any]:
        return {"spatial": self._extract_spatial(), "elements": self._extract_elements(), "project_units": list(self.project_units)}

#这段 GraphExporter 是“把内存里的 DM2CGraph 导出成三种可用格式”的模块：JSON、Cypher、TTL。


class GraphExporter:
    def __init__(self, graph: DM2CGraph, source_ifc: Path, source_ontology: Path, graph_profile: str = "backbone"):
        self.graph = graph
        self.source_ifc = source_ifc
        self.source_ontology = source_ontology
        self.graph_profile = graph_profile

    @staticmethod
    def _sorted_nodes(nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [nodes[k] for k in sorted(nodes.keys())]

    @staticmethod
    def _sorted_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(edges, key=lambda x: (x["src"], x["type"], x["tgt"]))

    def export_json(self, path: Path) -> None:
        if self.graph_profile == "backbone":
            metadata = {
                "graphType": "IFC-derived semantic graph backbone with cross-domain interfaces",
                "notCompleteCrossDomainKG": True,
                "runtimeReasoningExpectedInSection3_3": True,
                "sourceIfc": str(self.source_ifc),
                "sourceOntology": str(self.source_ontology),
                "description": (
                    "Persistent project-specific graph containing IFC-derived design facts, semantic anchors, "
                    "assessment targets, and data requirements for agentic RAG-based manufacturing carbon reasoning."
                ),
            }
        else:
            metadata = {
                "graphType": "DM2C ontology-guided IFC-to-manufacturing-carbon graph",
                "notGenericIFCGraph": True,
                "sourceIfc": str(self.source_ifc),
                "sourceOntology": str(self.source_ontology),
                "description": "Task-oriented semantic graph linking IFC design elements to production processes and carbon reasoning paths.",
            }

        payload = {
            "metadata": metadata,
            "nodes": self._sorted_nodes(self.graph.nodes),
            "edges": self._sorted_edges(self.graph.edges),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def export_cypher(self, path: Path) -> None:
        lines: List[str] = [
            "// DM2C IFC-derived semantic graph backbone",
            "CREATE CONSTRAINT dm2c_entity_id IF NOT EXISTS FOR (n:DM2CEntity) REQUIRE n.id IS UNIQUE;",
            "",
        ]

        for node in self._sorted_nodes(self.graph.nodes):
            node_props = dict(node.get("props", {}))
            node_props["id"] = node["id"]
            node_props["domain"] = node.get("domain", "")
            node_props["semanticOrigin"] = node.get("semanticOrigin", "")
            node_props["reasoningRole"] = node.get("reasoningRole", "")

            labels = ["DM2CEntity"] + [sanitize_label(x) for x in node.get("labels", [])] + [sanitize_label(node.get("domain", "Entity"))]
            labels = sorted(set(labels))
            labels_str = ":".join(labels)

            props_clause = ", ".join(f"{cypher_key(k)}: {cypher_value(v)}" for k, v in sorted(node_props.items()))
            lines.append(f"MERGE (n:{labels_str} {{id: {cypher_value(node['id'])}}})")
            lines.append(f"SET n += {{{props_clause}}};")
            lines.append("")

        for edge in self._sorted_edges(self.graph.edges):
            rel_props = dict(edge.get("props", {}))
            rel_props["semanticOrigin"] = edge.get("semanticOrigin", "")
            rel_props["inferenceRule"] = edge.get("inferenceRule", "")
            rel_props_clause = ", ".join(f"{cypher_key(k)}: {cypher_value(v)}" for k, v in sorted(rel_props.items()))
            rel_type = sanitize_rel_type(edge.get("type", "RELATED_TO"))

            lines.append(f"MATCH (s:DM2CEntity {{id: {cypher_value(edge['src'])}}})")
            lines.append(f"MATCH (t:DM2CEntity {{id: {cypher_value(edge['tgt'])}}})")
            lines.append(f"MERGE (s)-[r:{rel_type}]->(t)")
            lines.append(f"SET r += {{{rel_props_clause}}};")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")

    def _to_literal(self, value: Any) -> Any:
        if isinstance(value, bool):
            return Literal(value, datatype=XSD.boolean)
        if isinstance(value, int):
            return Literal(value, datatype=XSD.integer)
        if isinstance(value, float):
            return Literal(value, datatype=XSD.double)
        return Literal(str(value))

    @staticmethod
    def _label_to_class_uri(label: str) -> URIRef:
        bim_labels = {
            "IfcProject",
            "IfcSite",
            "IfcBuilding",
            "IfcBuildingStorey",
            "Module",
            "ProjectUnit",
            "DerivedUnitElement",
            "IfcMaterial",
            "MaterialLayer",
            "IfcElementQuantity",
            "IfcWall",
            "IfcWallStandardCase",
            "IfcBeam",
            "IfcColumn",
            "IfcSlab",
            "IfcDoor",
            "IfcWindow",
            "IfcBuildingElementProxy",
        }
        prod_labels = {
            "BuildingComponent",
            "ModuleProduction",
            "ProductionTemplate",
            "ProductionStage",
            "StructureAssemblyStage",
            "FitOutStage",
            "FitUpStage",
            "TestingStage",
            "ManufacturingActivity",
            "Resource",
        }
        interface_labels = {
            "QuantityBasis",
            "ProcessAssessmentTarget",
            "CarbonAssessmentTarget",
        }
        carbon_labels = {
            "ConsumptionDriver",
            "MaterialConsumption",
            "ElectricityConsumption",
            "FuelConsumption",
            "ConsumptionQuantity",
            "EmissionFactor",
            "CarbonEmission",
        }
        mapping_calc_labels = {
            "TemplateApplication",
            "MappingEvidence",
            "MappingIssue",
            "CalculationRecord",
            "CalculationIssue",
            "DataRequirement",
        }

        if label in bim_labels:
            return URIRef(f"{BIM_NS}{label}")
        if label in prod_labels:
            return URIRef(f"{MC_NS}{label}")
        if label in interface_labels:
            return URIRef(f"{ONTO_NS}{label}")
        if label in carbon_labels:
            return URIRef(f"{CARBON_NS}{label}")
        if label in mapping_calc_labels:
            return URIRef(f"{ONTO_NS}{label}")
        return URIRef(f"{ONTO_NS}{sanitize_label(label)}")

    def export_ttl(self, path: Path) -> None:
        if Graph is None:
            raise RuntimeError("rdflib is required for TTL export")

        g = Graph()
        BIM = Namespace(BIM_NS)
        MC = Namespace(MC_NS)
        CARBON = Namespace(CARBON_NS)
        ONTO = Namespace(ONTO_NS)
        INST = Namespace(INST_NS)

        g.bind("bim", BIM)
        g.bind("mc", MC)
        g.bind("carbon", CARBON)
        g.bind("onto", ONTO)
        g.bind("inst", INST)

        node_uri: Dict[str, URIRef] = {}
        for node in self._sorted_nodes(self.graph.nodes):
            uri = URIRef(f"{INST}{slugify(node['id'], 180)}")
            node_uri[node["id"]] = uri

            for label in node.get("labels", []):
                g.add((uri, RDF.type, self._label_to_class_uri(label)))

            g.add((uri, ONTO.domain, Literal(node.get("domain", ""))))
            g.add((uri, ONTO.semanticOrigin, Literal(node.get("semanticOrigin", ""))))
            g.add((uri, ONTO.reasoningRole, Literal(node.get("reasoningRole", ""))))
            g.add((uri, ONTO.nodeId, Literal(node.get("id", ""))))

            for k, v in sorted(node.get("props", {}).items()):
                if v is None or v == "":
                    continue
                pred = URIRef(f"{INST}prop/{sanitize_key(k)}")
                g.add((uri, pred, self._to_literal(v)))

        for idx, edge in enumerate(self._sorted_edges(self.graph.edges), start=1):
            src_uri = node_uri.get(edge["src"])
            tgt_uri = node_uri.get(edge["tgt"])
            if src_uri is None or tgt_uri is None:
                continue
            pred = URIRef(f"{ONTO_NS}{sanitize_rel_type(edge['type'])}")
            g.add((src_uri, pred, tgt_uri))

            # Lightweight reification to keep relationship provenance
            rel_uri = URIRef(f"{INST}rel/{idx}")
            g.add((rel_uri, RDF.type, ONTO.GraphRelation))
            g.add((rel_uri, ONTO.sourceNode, src_uri))
            g.add((rel_uri, ONTO.targetNode, tgt_uri))
            g.add((rel_uri, ONTO.relationType, Literal(edge["type"])))
            g.add((rel_uri, ONTO.semanticOrigin, Literal(edge.get("semanticOrigin", ""))))
            g.add((rel_uri, ONTO.inferenceRule, Literal(edge.get("inferenceRule", ""))))
            for k, v in sorted(edge.get("props", {}).items()):
                if v is None or v == "":
                    continue
                pred_k = URIRef(f"{INST}relprop/{sanitize_key(k)}")
                g.add((rel_uri, pred_k, self._to_literal(v)))

        g.serialize(destination=str(path), format="turtle")

    @staticmethod
    def export_competency_queries(path: Path) -> None:
        """Deprecated in backbone mode. Use BackboneCompetencyQueryWriter.write(path)."""
        BackboneCompetencyQueryWriter.write(path)


class BackboneMappingReporter:
    def __init__(self, graph: DM2CGraph, per_element_rows: List[Dict[str, Any]], checks: Dict[str, Any]):
        self.graph = graph
        self.per_element_rows = per_element_rows
        self.checks = checks

    def build(self) -> Dict[str, Any]:
        explicit_materials = 0
        inferred_materials = 0
        unresolved_materials = 0
        for node in self.graph.node_by_label("IfcMaterial"):
            props = node.get("props", {})
            source = str(props.get("materialSource", ""))
            status = str(props.get("candidateStatus", "resolved"))
            if source == "inferred_from_ifc_text":
                inferred_materials += 1
            else:
                explicit_materials += 1
            if status == "unresolved":
                unresolved_materials += 1

        return {
            "summary": {
                "ifcElementsProcessed": len(self.per_element_rows),
                "buildingComponentsCreated": len(self.graph.node_by_label("BuildingComponent")),
                "ifcMaterials": len(self.graph.node_by_label("IfcMaterial")),
                "explicitMaterials": explicit_materials,
                "inferredMaterials": inferred_materials,
                "unresolvedMaterials": unresolved_materials,
                "quantityBasisNodes": len(self.graph.node_by_label("QuantityBasis")),
                "processAssessmentTargets": len(self.graph.node_by_label("ProcessAssessmentTarget")),
                "carbonAssessmentTargets": len(self.graph.node_by_label("CarbonAssessmentTarget")),
                "dataRequirements": len(self.graph.node_by_label("DataRequirement")),
                "mappingEvidenceNodes": len(self.graph.node_by_label("MappingEvidence")),
                "materialCandidateNodes": len(self.graph.node_by_label("MaterialCandidate")),
                "forbiddenProductionTemplateNodes": len(self.graph.node_by_label("ProductionTemplate")),
                "forbiddenProductionStageNodes": len(self.graph.node_by_label("ProductionStage")),
                "forbiddenManufacturingActivityNodes": len(self.graph.node_by_label("ManufacturingActivity")),
                "forbiddenModuleProductionNodes": len(self.graph.node_by_label("ModuleProduction")),
                "forbiddenResourceNodes": len(self.graph.node_by_label("Resource")),
                "forbiddenEmissionFactorNodes": len(self.graph.node_by_label("EmissionFactor")),
                "forbiddenCarbonEmissionNodes": len(self.graph.node_by_label("CarbonEmission")),
                "forbiddenCalculationRecordNodes": len(self.graph.node_by_label("CalculationRecord")),
            },
            "elements": self.per_element_rows,
            "minimalChecks": self.checks,
        }

#给后面rag用的
class AgentInterfaceManifestBuilder:
    @staticmethod
    def build() -> Dict[str, Any]:
        return {
            "manifestType": "Section3.3_agent_interface_contract",
            "section3_2Boundary": {
                "backboneOnly": True,
                "noEmissionFactorBinding": True,
                "noCarbonValueCalculation": True,
                "note": "Carbon-related ontology predicates are used only as lexical normalization hints in Section 3.2.",
            },
            "graphGroundingContract": {
                "componentGrounding": {
                    "nodeLabel": "BuildingComponent",
                    "primaryId": "ifcGlobalId",
                    "evidenceLinks": [
                        "ALIGNED_AS",
                        "hasMaterial",
                        "HAS_QUANTITY_BASIS",
                        "HAS_PROCESS_ASSESSMENT_TARGET",
                        "HAS_CARBON_ASSESSMENT_TARGET",
                    ],
                }
            },
            "toolInputSchemas": {
                "manufacturing_process_retrieval_tool": {
                    "inputs": [
                        "componentGlobalId",
                        "ifcType",
                        "componentName",
                        "materialText",
                        "reference",
                        "objectType",
                        "typeObjectName",
                        "genericRetrievalHints",
                    ]
                },
                "carbon_factor_excel_lookup_tool": {
                    "inputs": [
                        "materialText",
                        "factorLookupKeys",
                        "lifeCycleStageCandidate",
                        "unitPreference",
                        "regionOrProjectContext",
                    ],
                    "note": "unitPreference is unknown until runtime lookup in Section 3.3.",
                },
                "deterministic_carbon_calculator_tool": {
                    "inputs": [
                        "selectedEmissionFactor",
                        "selectedFactorUnit",
                        "quantityBasis",
                        "unitMetadata",
                        "density",
                        "processEnergyProfile",
                    ],
                    "unitMetadata": {
                        "fields": [
                            "quantityUnitSource",
                            "unitInterpretationStatus",
                            "projectUnitConversionFactor",
                            "projectUnitBaseSymbol",
                        ],
                        "unitNormalizationRequired": True,
                    },
                },
            },
            "toolOutputSchemas": {
                "manufacturing_process_retrieval_tool": {
                    "outputs": [
                        "processName",
                        "activityList",
                        "sourceDocument",
                        "evidenceChunk",
                        "confidence",
                    ]
                },
                "carbon_factor_excel_lookup_tool": {
                    "outputs": [
                        "factorValue",
                        "factorUnit",
                        "materialMatched",
                        "sourceRowId",
                        "sourceFile",
                        "confidence",
                    ]
                },
                "deterministic_carbon_calculator_tool": {
                    "outputs": [
                        "calculatedValue",
                        "formula",
                        "normalizedQuantity",
                        "unitConversionTrace",
                        "calculationStatus",
                    ]
                },
            },
            "runtimeReasoningSequence": [
                "User question",
                "Retrieve BuildingComponent from backbone graph",
                "Retrieve IfcMaterial and QuantityBasis",
                "Retrieve manufacturing process from external text documents",
                "Lookup emission factor from Excel/tabular data",
                "Call deterministic calculator",
                "Generate answer with provenance grounded in graph evidence",
            ],
            "runtimeReasoningExpectedInSection3_3": True,
        }


class BackboneCompetencyQueryWriter:
    @staticmethod
    def write(path: Path) -> None:
        queries = """// 1) Trace full backbone path for one component
MATCH (e)-[:ALIGNED_AS]->(bc:BuildingComponent:Interface)
OPTIONAL MATCH (e)-[:hasMaterial]->(m:IfcMaterial:BIM)
OPTIONAL MATCH (bc)-[:HAS_QUANTITY_BASIS]->(qb:QuantityBasis:Interface)
OPTIONAL MATCH (bc)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget:Interface)
OPTIONAL MATCH (bc)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget:Interface)
RETURN e.id AS ifcElementId, bc.id AS buildingComponentId, collect(DISTINCT m.name) AS materials, qb.quantityReadiness AS quantityReadiness, pt.id AS processTargetId, collect(DISTINCT ct.id) AS carbonTargets
LIMIT 25;

// 2) List all components that require manufacturing process retrieval (target-level)
MATCH (bc:BuildingComponent)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget)
RETURN bc.id AS componentId, bc.ifcGlobalId AS globalId, pt.genericRetrievalHints AS genericRetrievalHints, pt.expectedExternalSource AS expectedSource
ORDER BY componentId;

// 3) List all carbon assessment targets and their factor lookup keys
MATCH (bc:BuildingComponent)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget)
RETURN bc.id AS componentId, ct.id AS carbonTargetId, ct.materialText AS materialText, ct.factorLookupKeys AS factorLookupKeys
ORDER BY componentId, carbonTargetId;

// 4) List all inferred IFC materials and evidence fields
MATCH (m:IfcMaterial)
WHERE m.materialSource = 'inferred_from_ifc_text' OR m.fallbackMaterial = true
RETURN m.id AS ifcMaterialId, m.name AS materialName, m.evidenceFields AS evidenceFields
ORDER BY ifcMaterialId;

// 5) List all components with missing explicit material
MATCH (e)-[:ALIGNED_AS]->(bc:BuildingComponent)
MATCH (e)-[:hasMaterial]->(m:IfcMaterial)
WHERE m.materialSource = 'inferred_from_ifc_text' OR m.fallbackMaterial = true OR m.candidateStatus = 'unresolved'
RETURN DISTINCT bc.id AS componentId, bc.ifcGlobalId AS globalId, m.name AS inferredMaterial, m.candidateStatus AS materialStatus
ORDER BY componentId;

// 6) List all components with at least one usable quantity basis
MATCH (bc:BuildingComponent)-[:HAS_QUANTITY_BASIS]->(qb:QuantityBasis)
WHERE qb.availableWeight = true OR qb.availableVolume = true OR qb.availableArea = true OR qb.availableCount = true
RETURN bc.id AS componentId, qb.id AS quantityBasisId, qb.volumeQuantityCandidates AS volumeCandidates, qb.areaQuantityCandidates AS areaCandidates, qb.countQuantityName AS countQuantity
ORDER BY componentId;

// 7) List all components with missing quantity basis
MATCH (bc:BuildingComponent)-[:HAS_QUANTITY_BASIS]->(qb:QuantityBasis)
WHERE qb.quantityReadiness = 'missing'
RETURN bc.id AS componentId, qb.id AS quantityBasisId, qb.notes AS notes
ORDER BY componentId;

// 8) Group components by IFC type and material for batched retrieval
MATCH (e)-[:ALIGNED_AS]->(bc:BuildingComponent)
OPTIONAL MATCH (e)-[:hasMaterial]->(m:IfcMaterial)
WITH bc, CASE WHEN m.name IS NULL OR m.name = '' THEN '__UNRESOLVED__' ELSE m.name END AS materialName
RETURN bc.ifcType AS ifcType, materialName, count(DISTINCT bc) AS componentCount, collect(DISTINCT bc.id)[0..20] AS sampleComponents
ORDER BY componentCount DESC;

// 9) Retrieve all graph-grounding evidence for one component GlobalId
MATCH (bc:BuildingComponent {ifcGlobalId: $globalId})
MATCH (e)-[:ALIGNED_AS]->(bc)
OPTIONAL MATCH (e)-[:hasMaterial]->(m:IfcMaterial)
OPTIONAL MATCH (bc)-[:HAS_QUANTITY_BASIS]->(qb:QuantityBasis)
OPTIONAL MATCH (bc)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget)
OPTIONAL MATCH (bc)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget)
RETURN bc.id AS componentId, e.id AS ifcElementId, collect(DISTINCT m) AS materials, qb, pt, collect(DISTINCT ct) AS carbonTargets;

// 10) List carbon/process targets and their pending runtime status
MATCH (bc:BuildingComponent)
OPTIONAL MATCH (bc)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget)
OPTIONAL MATCH (bc)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget)
RETURN bc.id AS componentId, pt.runtimeReasoningStatus AS processStatus, collect(DISTINCT ct.runtimeReasoningStatus) AS carbonStatuses
ORDER BY componentId;
"""
        path.write_text(queries, encoding="utf-8")


class BackboneGraphBuilder:
    def __init__(
        self,
        ifc_path: Path,
        ontology_path: Path,
        evidence_high_threshold: int = 3,
        evidence_medium_threshold: int = 2,
        unit_graph_level: str = "compact",
        quantity_node_mode: str = "preferred",
    ):
        self.ifc_path = ifc_path
        self.ontology_path = ontology_path
        self.graph = DM2CGraph()
        self.ontology = OntologySchemaReader(ontology_path)
        self.extractor = IFCExtractor(ifc_path, self.ontology)
        unit_level = str(unit_graph_level or "compact").strip().lower()
        if unit_level not in {"compact", "full"}:
            raise ValueError(f"Unsupported unit_graph_level={unit_graph_level!r}. Expected one of: compact, full.")
        self.unit_graph_level = unit_level
        quantity_mode = str(quantity_node_mode or "preferred").strip().lower()
        if quantity_mode not in {"all", "preferred"}:
            raise ValueError(
                f"Unsupported quantity_node_mode={quantity_node_mode!r}. Expected one of: all, preferred."
            )
        self.quantity_node_mode = quantity_mode
        # Heuristic thresholds (not empirically tuned) for mapping evidence quality bucketing.
        high = max(1, int(evidence_high_threshold))
        medium = max(1, int(evidence_medium_threshold))
        if medium > high:
            warnings.warn(
                f"evidence_medium_threshold ({medium}) exceeds evidence_high_threshold ({high}); "
                f"clamping medium to {high}. The 'medium' evidence-completeness bucket will be empty.",
                UserWarning,
                stacklevel=2,
            )
            medium = high
        self.evidence_high_threshold = high
        self.evidence_medium_threshold = medium
        self.per_element_rows: List[Dict[str, Any]] = []
        self._element_node_ids: List[str] = []
        self._component_ids: List[str] = []

    @staticmethod
    def _element_labels(ifc_type: str) -> List[str]:
        if ifc_type == "IfcWallStandardCase":
            return ["IfcWall", "IfcWallStandardCase"]
        return [ifc_type]

    def _add_spatial_layer(
        self,
        spatial: Dict[str, Any],
        project_units: List[Dict[str, Any]],
    ) -> Tuple[str, Dict[str, str], Dict[str, str]]:
        project_data = spatial.get("project", {})
        site_data = spatial.get("site", {})
        building_data = spatial.get("building", {})
        storeys = spatial.get("storeys", [])

        project_id = f"ifc_project:{project_data.get('global_id', '') or slugify(project_data.get('name', 'Project'), 50)}"
        site_id = f"ifc_site:{site_data.get('global_id', '') or slugify(site_data.get('name', 'Site'), 50)}"
        building_id = f"ifc_building:{building_data.get('global_id', '') or slugify(building_data.get('name', 'Building'), 50)}"
        module_id = "module:TypeA_Module"

        self.graph.add_node(
            project_id,
            ["IfcProject"],
            domain="BIM",
            semantic_origin="explicit_ifc",
            reasoning_role="project_root",
            props={"globalId": project_data.get("global_id", ""), "name": project_data.get("name", "Project")},
        )
        self.graph.add_node(
            site_id,
            ["IfcSite"],
            domain="BIM",
            semantic_origin="explicit_ifc",
            reasoning_role="site_context",
            props={"globalId": site_data.get("global_id", ""), "name": site_data.get("name", "Site")},
        )
        self.graph.add_node(
            building_id,
            ["IfcBuilding"],
            domain="BIM",
            semantic_origin="explicit_ifc",
            reasoning_role="building_context",
            props={"globalId": building_data.get("global_id", ""), "name": building_data.get("name", "Building")},
        )
        self.graph.add_node(
            module_id,
            ["Module"],
            domain="BIM",
            semantic_origin="ontology_aligned",
            reasoning_role="module_aggregation_anchor",
            props={
                "name": "TypeA_Module",
                "moduleSource": "default_module_when_ifc_module_absent",
            },
        )

        self.graph.add_edge(project_id, site_id, "HAS_SITE", "explicit_ifc", "ifc_spatial_structure", {})
        self.graph.add_edge(site_id, building_id, "HAS_BUILDING", "explicit_ifc", "ifc_spatial_structure", {})
        self.graph.add_edge(module_id, building_id, "REPRESENTS_BUILDING", "ontology_aligned", "module_building_binding", {})

        project_unit_id_map: Dict[str, str] = {}
        if self.unit_graph_level == "full":
            for idx, unit in enumerate(project_units, start=1):
                unit_type = str(unit.get("unitType", "") or f"UNIT_{idx}")
                unit_id = f"project_unit:{slugify(unit_type, 60)}:{idx}"
                self.graph.add_node(
                    unit_id,
                    ["ProjectUnit"],
                    domain="BIM",
                    semantic_origin="explicit_ifc",
                    reasoning_role="project_unit_assignment",
                    props={
                        "unitType": unit.get("unitType", ""),
                        "unitClass": unit.get("unitClass", ""),
                        "name": unit.get("name", ""),
                        "prefix": unit.get("prefix", ""),
                        "symbol": unit.get("symbol", ""),
                        "conversionFactor": unit.get("conversionFactor", None),
                        "baseUnitName": unit.get("baseUnitName", ""),
                        "baseUnitPrefix": unit.get("baseUnitPrefix", ""),
                        "baseUnitSymbol": unit.get("baseUnitSymbol", ""),
                        "derivedElements": unit.get("derivedElements", []),
                        "source": unit.get("source", ""),
                    },
                )
                self.graph.add_edge(project_id, unit_id, "HAS_PROJECT_UNIT", "explicit_ifc", "ifc_project_unit_assignment", {})
                if unit.get("unitType"):
                    project_unit_id_map[str(unit["unitType"])] = unit_id

                if str(unit.get("unitClass", "")) == "IfcDerivedUnit":
                    for e_idx, elem in enumerate(unit.get("derivedElements", []) or [], start=1):
                        elem_id = f"{unit_id}:element:{e_idx}"
                        self.graph.add_node(
                            elem_id,
                            ["DerivedUnitElement"],
                            domain="BIM",
                            semantic_origin="explicit_ifc",
                            reasoning_role="derived_unit_element",
                            props={
                                "baseClass": elem.get("baseClass", ""),
                                "baseName": elem.get("baseName", ""),
                                "basePrefix": elem.get("basePrefix", ""),
                                "baseSymbol": elem.get("baseSymbol", ""),
                                "exponent": elem.get("exponent", None),
                                "elementOrder": e_idx,
                            },
                        )
                        self.graph.add_edge(
                            unit_id,
                            elem_id,
                            "HAS_DERIVED_ELEMENT",
                            "explicit_ifc",
                            "derived_unit_decomposition",
                            {},
                        )

        storey_map: Dict[str, str] = {}
        for idx, storey in enumerate(storeys, start=1):
            s_name = str(storey.get("name", "") or f"Storey_{idx}")
            s_gid = str(storey.get("global_id", "") or f"storey_{idx}")
            s_id = f"ifc_storey:{s_gid or slugify(s_name, 60)}"
            self.graph.add_node(
                s_id,
                ["IfcBuildingStorey"],
                domain="BIM",
                semantic_origin="explicit_ifc",
                reasoning_role="spatial_containment",
                props={
                    "globalId": s_gid,
                    "name": s_name,
                    "elevation": storey.get("elevation", 0.0),
                },
            )
            self.graph.add_edge(building_id, s_id, "HAS_STOREY", "explicit_ifc", "ifc_spatial_structure", {})
            storey_map[s_name] = s_id
        return module_id, storey_map, project_unit_id_map

    def _quantity_name_groups(
        self,
        quantities: Dict[str, float],
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        weight_preferred = ["Weight"]
        volume_preferred = ["NetVolume", "GrossVolume"]
        area_preferred = ["NetSideArea", "GrossSideArea", "GrossArea", "OuterSurfaceArea", "GrossFootprintArea", "CrossSectionArea"]
        count_preferred = ["Count"]

        def present(names: List[str]) -> List[str]:
            return [n for n in names if n in quantities]

        weight_names = present(weight_preferred)
        if not weight_names:
            weight_names = [name for name in quantities.keys() if "weight" in normalize_text(name)]

        volume_names = present(volume_preferred)
        volume_names.extend([name for name in quantities.keys() if name not in volume_names and "volume" in normalize_text(name)])

        area_names = present(area_preferred)
        area_names.extend([name for name in quantities.keys() if name not in area_names and "area" in normalize_text(name)])

        count_names = present(count_preferred)
        if not count_names:
            count_names = [
                name
                for name in quantities.keys()
                if normalize_text(name) in {"count", "quantity", "number"} or "count" in normalize_text(name)
            ]

        return weight_names, volume_names, area_names, count_names

    def _selected_quantity_names_for_nodes(self, quantities: Dict[str, float]) -> List[str]:
        if self.quantity_node_mode == "all":
            return list(quantities.keys())

        weight_names, volume_names, area_names, count_names = self._quantity_name_groups(quantities)
        selected: List[str] = []
        if weight_names:
            selected.append(weight_names[0])
        if volume_names:
            selected.append(volume_names[0])
        if area_names:
            selected.append(area_names[0])
        if count_names:
            selected.append(count_names[0])

        if not selected and quantities:
            selected.append(next(iter(quantities.keys())))
        return list(dict.fromkeys(selected))

    def _add_element_bim_nodes(
        self,
        element: ExtractedElement,
        module_id: str,
        storey_node_map: Dict[str, str],
        project_unit_id_map: Dict[str, str],
    ) -> Tuple[str, List[Tuple[int, ExtractedMaterial, str]], Dict[str, str]]:
        element_id = f"ifc_element:{element.global_id}"
        props = {
            "globalId": element.global_id,
            "ifcType": element.ifc_type,
            "name": element.name,
            "objectType": element.object_type,
            "predefinedType": element.predefined_type,
            "tag": element.tag,
            "typeObjectName": element.type_object_name,
            "typeObjectIfcType": element.type_object_ifc_type,
            "typeObjectGlobalId": element.type_object_global_id,
            "storeyName": element.storey_name,
            "Reference": element.properties.get("Reference", ""),
            "LoadBearing": element.properties.get("LoadBearing", ""),
            "IsExternal": element.properties.get("IsExternal", ""),
            "FireRating": element.properties.get("FireRating", ""),
        }
        self.graph.add_node(
            element_id,
            self._element_labels(element.ifc_type),
            domain="BIM",
            semantic_origin="explicit_ifc",
            reasoning_role="ifc_building_element_instance",
            props=props,
        )
        self._element_node_ids.append(element_id)

        self.graph.add_edge(module_id, element_id, "CONTAINS_ELEMENT", "explicit_ifc", "module_element_containment", {})
        storey_id = storey_node_map.get(element.storey_name, "")
        if storey_id:
            self.graph.add_edge(storey_id, element_id, "CONTAINS_ELEMENT", "explicit_ifc", "storey_element_containment", {})

        quantity_node_map: Dict[str, str] = {}
        selected_quantity_names = set(self._selected_quantity_names_for_nodes(element.quantities))
        for q_name, q_value in element.quantities.items():
            if q_name not in selected_quantity_names:
                continue
            q_id = f"ifc_quantity:{element.global_id}:{slugify(q_name, 80)}"
            q_meta = dict(element.quantity_units.get(q_name, {}))
            q_unit = str(q_meta.get("unit", "") or "")
            self.graph.add_node(
                q_id,
                ["IfcElementQuantity"],
                domain="BIM",
                semantic_origin="explicit_ifc",
                reasoning_role="ifc_quantity_observation",
                props={
                    "ifcGlobalId": element.global_id,
                    "quantityName": q_name,
                    "quantityValue": q_value,
                    "quantityUnit": q_unit,
                    "quantityUnitSource": q_meta.get("quantityUnitSource", ""),
                    "projectUnitType": q_meta.get("projectUnitType", ""),
                    "projectUnitClass": q_meta.get("projectUnitClass", ""),
                    "projectUnitName": q_meta.get("projectUnitName", ""),
                    "projectUnitPrefix": q_meta.get("projectUnitPrefix", ""),
                    "projectUnitConversionFactor": q_meta.get("projectUnitConversionFactor", None),
                    "projectUnitBaseSymbol": q_meta.get("projectUnitBaseSymbol", ""),
                    "unitAssumption": q_meta.get("unitAssumption", ""),
                    "unitConfidence": q_meta.get("unitConfidence", None),
                    "unitInterpretationStatus": q_meta.get("unitInterpretationStatus", ""),
                },
            )
            self.graph.add_edge(
                element_id,
                q_id,
                "HAS_QUANTITY_SET",
                semantic_origin="explicit_ifc",
                inference_rule="ifc_element_quantity_extraction",
                props={},
            )
            proj_unit_type = str(q_meta.get("projectUnitType", "") or "")
            proj_unit_id = project_unit_id_map.get(proj_unit_type, "")
            if proj_unit_id:
                self.graph.add_edge(
                    q_id,
                    proj_unit_id,
                    "USES_PROJECT_UNIT",
                    semantic_origin="explicit_ifc",
                    inference_rule="quantity_project_unit_link",
                    props={},
                )
            quantity_node_map[q_name] = q_id

        # Merge same-name materials for one element and keep source paths in node properties.
        # This avoids triplicated IfcMaterial nodes when occurrence/type/type-association all point to the same material.
        grouped_materials: Dict[str, Dict[str, Any]] = {}
        material_group_order: List[str] = []
        for mat in element.materials:
            raw_mat_name = str(mat.name or "").strip()
            normalized_mat_name = "" if is_placeholder_material_name(raw_mat_name) else raw_mat_name
            group_key = normalize_text(normalized_mat_name) if normalized_mat_name else "__unresolved__"
            if group_key not in grouped_materials:
                grouped_materials[group_key] = {
                    "name": normalized_mat_name,
                    "raw_names": [],
                    "sources": [],
                    "explicit_any": False,
                    "fallback_any": False,
                    "confidence_max": 0.0,
                    "evidence_fields": [],
                    "items": [],
                }
                material_group_order.append(group_key)
            group = grouped_materials[group_key]
            if raw_mat_name and raw_mat_name not in group["raw_names"]:
                group["raw_names"].append(raw_mat_name)
            source_name = str(mat.source or "")
            if source_name and source_name not in group["sources"]:
                group["sources"].append(source_name)
            group["explicit_any"] = bool(group["explicit_any"] or mat.explicit)
            group["fallback_any"] = bool(group["fallback_any"] or mat.fallback_material)
            group["confidence_max"] = max(float(group["confidence_max"]), float(mat.confidence))
            for ef in mat.evidence_fields:
                if ef not in group["evidence_fields"]:
                    group["evidence_fields"].append(ef)
            group["items"].append(mat)

        material_bindings: List[Tuple[int, ExtractedMaterial, str]] = []
        for idx, group_key in enumerate(material_group_order, start=1):
            group = grouped_materials[group_key]
            normalized_mat_name = str(group.get("name", "") or "")
            raw_names = list(group.get("raw_names", []))
            raw_mat_name = raw_names[0] if raw_names else ""
            source_list = list(group.get("sources", []))
            primary_source = source_list[0] if source_list else ""
            sem_origin = "explicit_ifc" if bool(group.get("explicit_any", False)) else "inferred_from_ifc_text"
            candidate_status = material_candidate_status(normalized_mat_name)
            mat_id = f"ifc_material:{element.global_id}:{idx}:{slugify(normalized_mat_name or raw_mat_name or 'unresolved_material', 50)}"
            self.graph.add_node(
                mat_id,
                ["IfcMaterial"],
                domain="BIM",
                semantic_origin=sem_origin,
                reasoning_role="material_assignment",
                props={
                    "ifcGlobalId": element.global_id,
                    "name": normalized_mat_name,
                    "rawMaterialName": raw_mat_name,
                    "rawMaterialNames": raw_names,
                    "materialSource": primary_source,
                    "materialSources": source_list,
                    "sourceCount": len(source_list),
                    "fallbackMaterial": bool(group.get("fallback_any", False)),
                    "confidence": float(group.get("confidence_max", 0.0)),
                    "evidenceFields": list(group.get("evidence_fields", [])),
                    # Intentional duplication for query convenience across BIM and Interface layers.
                    "candidateStatus": candidate_status,
                },
            )
            self.graph.add_edge(
                element_id,
                mat_id,
                "hasMaterial",
                semantic_origin=sem_origin,
                inference_rule="ifc_material_extraction" if sem_origin == "explicit_ifc" else "material_inference_from_ifc_text",
                props={},
            )

            layer_seq = 1
            for item in group.get("items", []):
                if item.layer_order is None and item.thickness is None:
                    continue
                item_raw_name = str(item.name or "").strip()
                item_normalized_name = "" if is_placeholder_material_name(item_raw_name) else item_raw_name
                layer_id = f"material_layer:{element.global_id}:{idx}:{layer_seq}"
                layer_seq += 1
                self.graph.add_node(
                    layer_id,
                    ["MaterialLayer"],
                    domain="BIM",
                    semantic_origin="explicit_ifc",
                    reasoning_role="material_layer_detail",
                    props={
                        "ifcGlobalId": element.global_id,
                        "materialName": item_normalized_name,
                        "rawMaterialName": item_raw_name,
                        "layerThickness": item.thickness,
                        "layerOrder": item.layer_order,
                        "materialSource": item.source,
                        "evidence": "explicit_ifc_material_layer",
                    },
                )
                self.graph.add_edge(
                    element_id,
                    layer_id,
                    "HAS_MATERIAL_LAYER",
                    semantic_origin="explicit_ifc",
                    inference_rule="ifc_material_layer_extraction",
                    props={},
                )
                self.graph.add_edge(
                    layer_id,
                    mat_id,
                    "LAYER_MATERIAL",
                    semantic_origin="explicit_ifc",
                    inference_rule="ifc_material_layer_material_binding",
                    props={},
                )

            material_bindings.append(
                (
                    idx,
                    ExtractedMaterial(
                        name=normalized_mat_name,
                        source=primary_source,
                        explicit=bool(group.get("explicit_any", False)),
                        fallback_material=bool(group.get("fallback_any", False)),
                        confidence=float(group.get("confidence_max", 0.0)),
                        evidence_fields=list(group.get("evidence_fields", [])),
                    ),
                    mat_id,
                )
            )

        return element_id, material_bindings, quantity_node_map

    def _material_keyword_matches(self, text: str) -> List[str]:
        if hasattr(self.ontology, "detect_material_keywords"):
            return self.ontology.detect_material_keywords(text)
        matched: List[str] = []
        text_norm = normalize_text(text)
        for kw in getattr(self.ontology, "material_keywords", []):
            if normalize_text(kw) and normalize_text(kw) in text_norm:
                matched.append(kw)
        return sorted(set(matched), key=lambda x: len(normalize_text(x)), reverse=True)

    def _create_building_component(
        self,
        element: ExtractedElement,
        element_node_id: str,
        material_bindings: List[Tuple[int, ExtractedMaterial, str]],
    ) -> str:
        component_id = f"bc:{element.global_id}"
        material_text = " | ".join([m.name for _, m, _ in material_bindings if m.name and not is_placeholder_material_name(m.name)])
        has_explicit_material = any(
            m.explicit and str(m.name or "").strip() and not is_placeholder_material_name(m.name) for _, m, _ in material_bindings
        )
        has_quantity_basis = bool(element.quantities)
        has_reference = bool(str(element.properties.get("Reference", "") or "").strip())
        has_type_object = bool(str(element.type_object_name or "").strip() or str(element.type_object_global_id or "").strip())
        completeness_score = sum([has_explicit_material, has_quantity_basis, has_reference, has_type_object])
        if completeness_score >= self.evidence_high_threshold:
            evidence_completeness = "high"
        elif completeness_score >= self.evidence_medium_threshold:
            evidence_completeness = "medium"
        else:
            evidence_completeness = "low"
        self.graph.add_node(
            component_id,
            ["BuildingComponent"],
            domain="Interface",
            semantic_origin="ontology_aligned",
            reasoning_role="cross_domain_component_anchor",
            props={
                "ifcGlobalId": element.global_id,
                "ifcType": element.ifc_type,
                "name": element.name or element.global_id,
                "reference": element.properties.get("Reference", ""),
                "objectType": element.object_type,
                "typeObjectName": element.type_object_name,
                "ontologyClassUri": f"{MC_NS}BuildingComponent",
                "ontologyClassLabel": "BuildingComponent",
                "alignedFromClass": element.ifc_type,
                "interfaceRole": "project_component_grounding",
                "materialText": material_text,
                "hasExplicitMaterial": has_explicit_material,
                "hasQuantityBasis": has_quantity_basis,
                "hasReference": has_reference,
                "hasTypeObject": has_type_object,
                "evidenceCompleteness": evidence_completeness,
                "evidenceCompletenessHeuristic": "heuristic_threshold_rule_not_empirically_tuned",
                "evidenceHighThreshold": self.evidence_high_threshold,
                "evidenceMediumThreshold": self.evidence_medium_threshold,
            },
        )
        self._component_ids.append(component_id)
        self.graph.add_edge(
            element_node_id,
            component_id,
            "ALIGNED_AS",
            semantic_origin="ontology_aligned",
            inference_rule="ifc_element_to_building_component_alignment",
            props={},
        )
        return component_id

    @staticmethod
    def _split_text_tokens(text: str) -> List[str]:
        tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", str(text or ""))
        cleaned = [t.strip() for t in tokens if t.strip()]
        return cleaned[:20]

    @staticmethod
    def _material_binding_rows(
        material_bindings: List[Tuple[int, ExtractedMaterial, str]],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for idx, mat, mat_id in material_bindings:
            raw_material_name = str(mat.name or "").strip()
            material_name = "" if is_placeholder_material_name(raw_material_name) else raw_material_name
            candidate_status = material_candidate_status(material_name)
            rows.append(
                {
                    "mat_id": mat_id,
                    "materialName": material_name,
                    "materialSource": "explicit_ifc" if mat.explicit else "inferred_from_ifc_text",
                    "fallbackMaterial": bool(mat.fallback_material),
                    "candidateStatus": candidate_status,
                    "confidence": mat.confidence if not mat.explicit else 1.0,
                    "evidenceFields": list(mat.evidence_fields),
                    "rawMaterialName": raw_material_name,
                    "index": idx,
                }
            )
        return rows

    def _create_quantity_basis(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        quantity_node_map: Dict[str, str],
    ) -> Dict[str, Any]:
        q = element.quantities
        weight_name, volume_names, area_names, count_names = self._quantity_name_groups(q)

        available_weight = bool(weight_name)
        available_volume = bool(volume_names)
        available_area = bool(area_names)
        available_count = bool(count_names)

        readiness = "available" if any([available_weight, available_volume, available_area, available_count]) else "missing"

        basis_options: List[str] = []
        basis_options.extend(weight_name[:1])
        basis_options.extend(volume_names)
        basis_options.extend(area_names)
        basis_options.extend(count_names[:1])
        basis_options = list(dict.fromkeys([x for x in basis_options if x]))
        preferred_for_mass_factor = list(dict.fromkeys(weight_name[:1] + volume_names))
        preferred_for_volume_factor = list(dict.fromkeys(volume_names))
        preferred_for_area_factor = list(dict.fromkeys(area_names))
        preferred_for_unit_factor = list(dict.fromkeys(count_names[:1]))

        unit_sources: List[str] = []
        unit_assumptions: List[str] = []
        unit_confidences: List[float] = []
        interpretation_statuses: List[str] = []
        for q_name in basis_options:
            q_meta = dict(element.quantity_units.get(q_name, {}))
            src = str(q_meta.get("quantityUnitSource", "") or "")
            assumption = str(q_meta.get("unitAssumption", "") or "")
            status = str(q_meta.get("unitInterpretationStatus", "") or "")
            conf = q_meta.get("unitConfidence", None)
            if src:
                unit_sources.append(src)
            if assumption:
                unit_assumptions.append(assumption)
            if status:
                interpretation_statuses.append(status)
            if isinstance(conf, (int, float)):
                unit_confidences.append(float(conf))

        unit_sources = sorted(set(unit_sources))
        unit_assumptions = list(dict.fromkeys(unit_assumptions))
        interpretation_statuses = sorted(set(interpretation_statuses))
        if "assumed_from_ifc_quantity_type" in interpretation_statuses:
            unit_interpretation_status = "assumed_from_ifc_quantity_type"
        elif "from_project_unit_assignment" in interpretation_statuses:
            unit_interpretation_status = "from_project_unit_assignment"
        else:
            unit_interpretation_status = "unknown"

        qb_id = f"quantity_basis:{element.global_id}"
        self.graph.add_node(
            qb_id,
            ["QuantityBasis"],
            domain="Interface",
            semantic_origin="agent_interface",
            reasoning_role="quantity_basis_anchor",
            props={
                "ifcGlobalId": element.global_id,
                "availableWeight": available_weight,
                "weightQuantityName": weight_name[0] if weight_name else "",
                "weightValue": q.get(weight_name[0]) if weight_name else None,
                "availableVolume": available_volume,
                "volumeQuantityCandidates": list(volume_names),
                "availableArea": available_area,
                "areaQuantityCandidates": list(area_names),
                "availableCount": available_count,
                "countQuantityName": count_names[0] if count_names else "",
                "quantityReadiness": readiness,
                "massBasisAvailable": available_weight,
                "volumeBasisAvailable": available_volume,
                "areaBasisAvailable": available_area,
                "countBasisAvailable": available_count,
                "preferredForMassFactor": preferred_for_mass_factor,
                "preferredForVolumeFactor": preferred_for_volume_factor,
                "preferredForAreaFactor": preferred_for_area_factor,
                "preferredForUnitFactor": preferred_for_unit_factor,
                "quantityNodeMode": self.quantity_node_mode,
                "quantityNodeNames": list(quantity_node_map.keys()),
                "quantityUnitSource": unit_sources,
                "unitAssumption": unit_assumptions,
                "unitConfidenceMin": min(unit_confidences) if unit_confidences else None,
                "unitConfidenceAvg": round(sum(unit_confidences) / len(unit_confidences), 4) if unit_confidences else None,
                "unitInterpretationStatus": unit_interpretation_status,
                "notes": "Quantity basis only; no emission factor has been selected in Section 3.2.",
            },
        )
        self.graph.add_edge(
            component_id,
            qb_id,
            "HAS_QUANTITY_BASIS",
            semantic_origin="agent_interface",
            inference_rule="quantity_basis_aggregation",
            props={},
        )
        self.graph.add_edge(
            qb_id,
            element_node_id,
            "GROUNDED_IN_IFC_ELEMENT",
            semantic_origin="agent_interface",
            inference_rule="quantity_basis_element_grounding",
            props={},
        )
        for q_name in basis_options:
            q_node = quantity_node_map.get(q_name)
            if not q_node:
                continue
            self.graph.add_edge(
                qb_id,
                q_node,
                "GROUNDED_IN_QUANTITY",
                semantic_origin="agent_interface",
                inference_rule="quantity_basis_quantity_grounding",
                props={},
            )

        return {
            "id": qb_id,
            "availableWeight": available_weight,
            "availableVolume": available_volume,
            "availableArea": available_area,
            "availableCount": available_count,
            "quantityReadiness": readiness,
            "basisOptions": basis_options,
            "preferredForMassFactor": preferred_for_mass_factor,
            "preferredForVolumeFactor": preferred_for_volume_factor,
            "preferredForAreaFactor": preferred_for_area_factor,
            "preferredForUnitFactor": preferred_for_unit_factor,
            "quantityNodeMode": self.quantity_node_mode,
            "quantityNodeNames": list(quantity_node_map.keys()),
            "unitInterpretationStatus": unit_interpretation_status,
        }

    def _build_query_hints(
        self,
        element: ExtractedElement,
        material_rows: List[Dict[str, Any]],
    ) -> List[str]:
        hints: List[str] = []
        for token in [element.ifc_type, element.name, element.object_type, element.type_object_name, str(element.properties.get("Reference", "") or "")]:
            hints.extend(self._split_text_tokens(token))
        for row in material_rows:
            mat_name = str(row.get("materialName", "") or "")
            hints.extend(self._split_text_tokens(mat_name))
            for kw in self._material_keyword_matches(mat_name):
                hints.append(str(kw))

        unique = []
        seen = set()
        for h in hints:
            key = normalize_text(h)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(h)
        return unique[:16]

    def _create_process_assessment_target(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        material_rows: List[Dict[str, Any]],
    ) -> str:
        query_hints = self._build_query_hints(element, material_rows)
        material_text = " | ".join([r.get("materialName", "") for r in material_rows if r.get("materialName")])
        target_id = f"process_target:{element.global_id}"
        self.graph.add_node(
            target_id,
            ["ProcessAssessmentTarget"],
            domain="Interface",
            semantic_origin="agent_interface",
            reasoning_role="manufacturing_process_retrieval_target",
            props={
                "targetType": "manufacturing_process_retrieval",
                "componentGlobalId": element.global_id,
                "ifcType": element.ifc_type,
                "componentName": element.name,
                "materialText": material_text,
                "reference": element.properties.get("Reference", ""),
                "objectType": element.object_type,
                "typeObjectName": element.type_object_name,
                "genericRetrievalHints": list(query_hints),
                "expectedExternalSource": "manufacturing_process_text",
                "hintSource": "ifc_text_and_material_tokenization",
                "notProcessInference": True,
                "requiredRuntimeInputs": ["manufacturing_process_description"],
                "runtimeReasoningStatus": "pending_section_3_3",
            },
        )
        self.graph.add_edge(
            component_id,
            target_id,
            "HAS_PROCESS_ASSESSMENT_TARGET",
            semantic_origin="agent_interface",
            inference_rule="process_assessment_target_creation",
            props={},
        )
        self.graph.add_edge(
            target_id,
            element_node_id,
            "GROUNDED_IN_IFC_ELEMENT",
            semantic_origin="agent_interface",
            inference_rule="process_target_element_grounding",
            props={},
        )
        return target_id

    def _create_carbon_assessment_targets(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        material_rows: List[Dict[str, Any]],
        quantity_basis: Dict[str, Any],
        quantity_node_map: Dict[str, str],
    ) -> List[str]:
        target_ids: List[str] = []
        basis_options = list(quantity_basis.get("basisOptions", []))

        for row in material_rows:
            idx = int(row.get("index", 1))
            ct_id = f"carbon_target:{element.global_id}:{idx}"
            mat_name = str(row.get("materialName", "") or "")
            factor_lookup_keys = list(self._material_keyword_matches(mat_name))
            if str(row.get("candidateStatus", "")) != "unresolved":
                factor_lookup_keys.extend(self._split_text_tokens(mat_name))
            factor_lookup_keys = list(dict.fromkeys([k for k in factor_lookup_keys if str(k).strip()]))[:12]
            required_runtime_inputs = ["emission_factor"]
            if not bool(quantity_basis.get("availableWeight", False)):
                required_runtime_inputs.append("density_if_mass_factor_and_no_weight")
            if str(quantity_basis.get("quantityReadiness", "")) == "missing":
                required_runtime_inputs.append("quantity_basis")
            if row.get("materialSource") == "inferred_from_ifc_text" or row.get("fallbackMaterial") or row.get("candidateStatus") == "unresolved":
                required_runtime_inputs.append("material_confirmation")

            self.graph.add_node(
                ct_id,
                ["CarbonAssessmentTarget"],
                domain="Interface",
                semantic_origin="agent_interface",
                reasoning_role="carbon_factor_lookup_target",
                props={
                    "targetType": "carbon_factor_lookup",
                    "componentGlobalId": element.global_id,
                    "ifcType": element.ifc_type,
                    "materialText": mat_name,
                    "materialStatus": row.get("candidateStatus", "resolved"),
                    "materialSource": row.get("materialSource", ""),
                    "factorLookupKeys": list(factor_lookup_keys),
                    "expectedExternalSource": "carbon_factor_excel",
                    "lifeCycleStageCandidate": "A1-A3",
                    "quantityBasisOptions": list(basis_options),
                    "requiresEmissionFactor": True,
                    "requiresDensityIfMassFactorAndNoWeight": not bool(quantity_basis.get("availableWeight", False)),
                    "requiredRuntimeInputs": list(dict.fromkeys(required_runtime_inputs)),
                    "runtimeReasoningStatus": "pending_section_3_3",
                },
            )
            self.graph.add_edge(
                component_id,
                ct_id,
                "HAS_CARBON_ASSESSMENT_TARGET",
                semantic_origin="agent_interface",
                inference_rule="carbon_assessment_target_creation",
                props={},
            )
            self.graph.add_edge(
                ct_id,
                element_node_id,
                "GROUNDED_IN_IFC_ELEMENT",
                semantic_origin="agent_interface",
                inference_rule="carbon_target_element_grounding",
                props={},
            )
            if row.get("mat_id"):
                self.graph.add_edge(
                    ct_id,
                    row.get("mat_id", ""),
                    "GROUNDED_IN_MATERIAL",
                    semantic_origin="agent_interface",
                    inference_rule="carbon_target_material_grounding",
                    props={},
                )
            for q_name in basis_options:
                q_id = quantity_node_map.get(q_name)
                if not q_id:
                    continue
                self.graph.add_edge(
                    ct_id,
                    q_id,
                    "GROUNDED_IN_QUANTITY",
                    semantic_origin="agent_interface",
                    inference_rule="carbon_target_quantity_grounding",
                    props={},
                )

            target_ids.append(ct_id)

        return target_ids

    def _run_graph_minimal_checks(self) -> Dict[str, Any]:
        issues: List[str] = []

        for edge in self.graph.edges:
            src = edge.get("src", "")
            tgt = edge.get("tgt", "")
            if src not in self.graph.nodes or tgt not in self.graph.nodes:
                issues.append(f"Edge endpoint missing: {src} -[{edge.get('type', '')}]-> {tgt}")
            if src == tgt:
                issues.append(f"Unexpected self-loop: {src} -[{edge.get('type', '')}]-> {tgt}")

        for eid in self._element_node_ids:
            has_component = any(e["src"] == eid and e["type"] == "ALIGNED_AS" for e in self.graph.edges)
            if not has_component:
                issues.append(f"IFC element missing BuildingComponent alignment: {eid}")

        for bc_id in self._component_ids:
            process_links = [e for e in self.graph.edges if e["src"] == bc_id and e["type"] == "HAS_PROCESS_ASSESSMENT_TARGET"]
            carbon_links = [e for e in self.graph.edges if e["src"] == bc_id and e["type"] == "HAS_CARBON_ASSESSMENT_TARGET"]
            if len(process_links) < 1:
                issues.append(f"BuildingComponent missing ProcessAssessmentTarget: {bc_id}")
            if not carbon_links:
                issues.append(f"BuildingComponent missing CarbonAssessmentTarget: {bc_id}")

        forbidden_labels = {
            "ProductionTemplate",
            "ProductionStage",
            "ManufacturingActivity",
            "ModuleProduction",
            "Resource",
            "ConsumptionDriver",
            "ConsumptionQuantity",
            "EmissionFactor",
            "CarbonEmission",
            "CalculationRecord",
            "MaterialCandidate",
            "MappingEvidence",
            "DataRequirement",
        }
        for label in forbidden_labels:
            if self.graph.node_by_label(label):
                issues.append(f"Forbidden node label exists in backbone mode: {label}")

        return {"passed": len(issues) == 0, "issueCount": len(issues), "issues": issues[:80]}

    @staticmethod
    def _combine_graph_and_export_checks(graph_checks: Dict[str, Any], manifest_generated: bool) -> Dict[str, Any]:
        issues = list(graph_checks.get("issues", []))
        if not manifest_generated:
            issues.append("agent_interface_manifest.json was not generated")
        return {
            "passed": bool(graph_checks.get("passed", False)) and manifest_generated,
            "issueCount": len(issues),
            "issues": issues[:80],
            "manifestGenerated": manifest_generated,
        }

    def build(self) -> Tuple[DM2CGraph, Dict[str, Any], Dict[str, Any]]:
        extracted = self.extractor.extract()
        spatial = extracted["spatial"]
        project_units = extracted.get("project_units", []) or []
        elements: List[ExtractedElement] = extracted["elements"]
        module_id, storey_node_map, project_unit_id_map = self._add_spatial_layer(spatial, project_units)

        for element in elements:
            element_node_id, material_bindings, quantity_node_map = self._add_element_bim_nodes(
                element=element,
                module_id=module_id,
                storey_node_map=storey_node_map,
                project_unit_id_map=project_unit_id_map,
            )
            material_rows = self._material_binding_rows(material_bindings)
            component_id = self._create_building_component(
                element=element,
                element_node_id=element_node_id,
                material_bindings=material_bindings,
            )
            quantity_basis = self._create_quantity_basis(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                quantity_node_map=quantity_node_map,
            )
            process_target_id = self._create_process_assessment_target(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                material_rows=material_rows,
            )
            carbon_target_ids = self._create_carbon_assessment_targets(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                material_rows=material_rows,
                quantity_basis=quantity_basis,
                quantity_node_map=quantity_node_map,
            )

            first_material = material_rows[0] if material_rows else {}
            self.per_element_rows.append(
                {
                    "globalId": element.global_id,
                    "ifcType": element.ifc_type,
                    "name": element.name,
                    "reference": str(element.properties.get("Reference", "") or ""),
                    "material": str(first_material.get("materialName", "") or ""),
                    "materialStatus": str(first_material.get("candidateStatus", "") or ""),
                    "materialSource": str(first_material.get("materialSource", "") or ""),
                    "quantityReadiness": quantity_basis.get("quantityReadiness", "missing"),
                    "quantityUnitInterpretationStatus": quantity_basis.get("unitInterpretationStatus", "unknown"),
                    "buildingComponentId": component_id,
                    "processAssessmentTargetId": process_target_id,
                    "carbonAssessmentTargetIds": carbon_target_ids,
                }
            )

        graph_checks = self._run_graph_minimal_checks()
        report = BackboneMappingReporter(self.graph, self.per_element_rows, graph_checks).build()
        stats = {
            "nodeCount": len(self.graph.nodes),
            "edgeCount": len(self.graph.edges),
            "bimNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "BIM"]),
            "interfaceNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Interface"]),
            "mappingNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Mapping"]),
            "requirementNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Requirement"]),
            "ifcElements": len(elements),
            "buildingComponents": len(self.graph.node_by_label("BuildingComponent")),
            "ifcMaterials": len(self.graph.node_by_label("IfcMaterial")),
            "unresolvedMaterials": len(
                [
                    n
                    for n in self.graph.node_by_label("IfcMaterial")
                    if str(n.get("props", {}).get("candidateStatus", "")) == "unresolved"
                ]
            ),
            "quantityBasis": len(self.graph.node_by_label("QuantityBasis")),
            "processTargets": len(self.graph.node_by_label("ProcessAssessmentTarget")),
            "carbonTargets": len(self.graph.node_by_label("CarbonAssessmentTarget")),
            "mappingEvidenceNodes": len(self.graph.node_by_label("MappingEvidence")),
            "materialCandidateNodes": len(self.graph.node_by_label("MaterialCandidate")),
            "dataRequirements": len(self.graph.node_by_label("DataRequirement")),
            "forbiddenProductionTemplates": len(self.graph.node_by_label("ProductionTemplate")),
            "forbiddenProductionStages": len(self.graph.node_by_label("ProductionStage")),
            "forbiddenManufacturingActivities": len(self.graph.node_by_label("ManufacturingActivity")),
            "forbiddenModuleProduction": len(self.graph.node_by_label("ModuleProduction")),
            "forbiddenResources": len(self.graph.node_by_label("Resource")),
            "forbiddenConsumptionDrivers": len(self.graph.node_by_label("ConsumptionDriver")),
            "forbiddenConsumptionQuantities": len(self.graph.node_by_label("ConsumptionQuantity")),
            "forbiddenEmissionFactors": len(self.graph.node_by_label("EmissionFactor")),
            "forbiddenCarbonEmissions": len(self.graph.node_by_label("CarbonEmission")),
            "forbiddenCalculationRecords": len(self.graph.node_by_label("CalculationRecord")),
            "checkPassed": graph_checks["passed"],
            "checkIssueCount": graph_checks["issueCount"],
        }
        return self.graph, report, stats

def build_and_export_backbone(
    ifc_path: Path,
    ontology_path: Path,
    out_dir: Path,
    unit_graph_level: str = "compact",
    quantity_node_mode: str = "preferred",
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    builder = BackboneGraphBuilder(
        ifc_path=ifc_path,
        ontology_path=ontology_path,
        unit_graph_level=unit_graph_level,
        quantity_node_mode=quantity_node_mode,
    )
    graph, mapping_report, stats = builder.build()

    exporter = GraphExporter(graph, source_ifc=ifc_path, source_ontology=ontology_path, graph_profile="backbone")
    json_path = out_dir / "dm2c_backbone_graph.json"
    cypher_path = out_dir / "dm2c_backbone_graph.cypher"
    ttl_path = out_dir / "dm2c_backbone_graph.ttl"
    report_path = out_dir / "dm2c_backbone_mapping_report.json"
    manifest_path = out_dir / "agent_interface_manifest.json"
    cq_path = out_dir / "backbone_competency_queries.cypher"

    exporter.export_json(json_path)
    exporter.export_cypher(cypher_path)
    exporter.export_ttl(ttl_path)
    manifest = AgentInterfaceManifestBuilder.build()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Keep graph checks and export checks separated to avoid coupling graph invariants with filesystem state.
    manifest_generated = manifest_path.exists()
    graph_checks = builder._run_graph_minimal_checks()
    final_checks = builder._combine_graph_and_export_checks(graph_checks, manifest_generated)
    mapping_report = BackboneMappingReporter(graph, builder.per_element_rows, final_checks).build()
    stats["checkPassed"] = final_checks.get("passed", False)
    stats["checkIssueCount"] = final_checks.get("issueCount", 0)
    report_path.write_text(json.dumps(mapping_report, ensure_ascii=False, indent=2), encoding="utf-8")
    BackboneCompetencyQueryWriter.write(cq_path)

    return {
        "graph_json": str(json_path),
        "graph_cypher": str(cypher_path),
        "graph_ttl": str(ttl_path),
        "mapping_report": str(report_path),
        "competency_queries": str(cq_path),
        "agent_interface_manifest": str(manifest_path),
        "stats": stats,
        "mapping_report_data": mapping_report,
        "mode": "backbone",
        "unit_graph_level": unit_graph_level,
        "quantity_node_mode": quantity_node_mode,
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DM2C IFC-derived semantic graph backbone constructor")
    parser.add_argument("--ifc", required=True, help="Path to IFC file")
    parser.add_argument("--ontology", required=True, help="Path to ontology TTL file")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument(
        "--unit-graph-level",
        default="compact",
        choices=["compact", "full"],
        help="compact (default) keeps unit metadata on quantity properties without ProjectUnit/DerivedUnitElement nodes; full also materializes unit nodes and links.",
    )
    parser.add_argument(
        "--quantity-node-mode",
        default="preferred",
        choices=["preferred", "all"],
        help="preferred (default) materializes only preferred quantity nodes per component type; all keeps all IfcElementQuantity nodes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ifc_path = Path(args.ifc).expanduser().resolve()
    ontology_path = Path(args.ontology).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not ifc_path.exists():
        raise FileNotFoundError(f"IFC file not found: {ifc_path}")
    if not ontology_path.exists():
        raise FileNotFoundError(f"Ontology file not found: {ontology_path}")

    result = build_and_export_backbone(
        ifc_path=ifc_path,
        ontology_path=ontology_path,
        out_dir=out_dir,
        unit_graph_level=args.unit_graph_level,
        quantity_node_mode=args.quantity_node_mode,
    )
    stats = result["stats"]

    print("DM2C IFC-to-Backbone completed.")
    print(f"  IFC: {ifc_path}")
    print(f"  Ontology: {ontology_path}")
    print(f"  Unit graph level: {result['unit_graph_level']}")
    print(f"  Quantity node mode: {result['quantity_node_mode']}")
    print(f"  Output directory: {out_dir}")
    print("Generated files:")
    print(f"  - {result['graph_json']}")
    print(f"  - {result['graph_cypher']}")
    print(f"  - {result['graph_ttl']}")
    print(f"  - {result['mapping_report']}")
    print(f"  - {result['agent_interface_manifest']}")
    print(f"  - {result['competency_queries']}")
    print("Backbone summary:")
    print(
        "  Nodes={nodeCount}, Edges={edgeCount}, BIM={bimNodes}, Interface={interfaceNodes}, Mapping={mappingNodes}, Requirement={requirementNodes}".format(
            **stats
        )
    )
    print(
        "  IFC elements={ifcElements}, BuildingComponent={buildingComponents}, IfcMaterial={ifcMaterials}, UnresolvedMaterial={unresolvedMaterials}, QuantityBasis={quantityBasis}, ProcessTarget={processTargets}, CarbonTarget={carbonTargets}, MappingEvidence={mappingEvidenceNodes}, MaterialCandidate={materialCandidateNodes}, DataRequirement={dataRequirements}".format(
            **stats
        )
    )
    print(
        "  Forbidden nodes in backbone: ProductionTemplate={forbiddenProductionTemplates}, ProductionStage={forbiddenProductionStages}, ManufacturingActivity={forbiddenManufacturingActivities}, ModuleProduction={forbiddenModuleProduction}, Resource={forbiddenResources}, ConsumptionDriver={forbiddenConsumptionDrivers}, ConsumptionQuantity={forbiddenConsumptionQuantities}, EmissionFactor={forbiddenEmissionFactors}, CarbonEmission={forbiddenCarbonEmissions}, CalculationRecord={forbiddenCalculationRecords}".format(
            **stats
        )
    )
    print(f"  Minimal checks passed: {stats['checkPassed']} (issues={stats['checkIssueCount']})")


if __name__ == "__main__":
    main()
