#!/usr/bin/env python3
"""
DM2C ontology-guided IFC-to-graph construction
Section 3.2 implementation: Parsing -> Mapping/Inference -> Export
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
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
    "IfcWallStandardCase",
    "IfcBeam",
    "IfcColumn",
    "IfcSlab",
    "IfcDoor",
    "IfcWindow",
    "IfcBuildingElementProxy",
]


def normalize_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", "", (text or "").strip().casefold())


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def slugify(text: str, max_len: int = 80) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", str(text)).strip("_")
    return (token or "x")[:max_len]


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
    quantity_units: Dict[str, str]
    materials: List[ExtractedMaterial]


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


class OntologyReader:
    """
    Reads DM2C ontology facts used by Section 3.2:
    - production templates, stages, activities, resources
    - emission factors
    - material densities
    """

    def __init__(self, ontology_path: Path):
        if Graph is None:
            raise RuntimeError("rdflib is required to parse ontology.ttl")
        self.ontology_path = ontology_path
        self.graph = Graph()
        self.graph.parse(str(ontology_path), format="turtle")

        self.BIM = Namespace(BIM_NS)
        self.MC = Namespace(MC_NS)
        self.CARBON = Namespace(CARBON_NS)
        self.ONTO = Namespace(ONTO_NS)

        self.templates = self._read_templates()
        self.emission_factors = self._read_emission_factors()
        self.material_densities = self._read_material_densities()
        self.activity_energy_profiles = self._read_activity_energy_profiles()
        self.material_keywords = self._collect_material_keywords()

    @staticmethod
    def _local_name(uri: Any) -> str:
        text = str(uri)
        if "#" in text:
            return text.split("#")[-1]
        return text.rsplit("/", 1)[-1]

    def _read_templates(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for tmpl in self.graph.subjects(RDF.type, self.ONTO.ProductionTemplate):
            label = str(self.graph.value(tmpl, RDFS.label) or self._local_name(tmpl))
            applies_to = [self._local_name(x) for x in self.graph.objects(tmpl, self.ONTO.appliesTo)]
            material_keywords = [str(x) for x in self.graph.objects(tmpl, self.ONTO.materialKeyword)]
            is_default_raw = self.graph.value(tmpl, self.ONTO.isDefaultTemplate)
            is_default = str(is_default_raw).strip().lower() in {"true", "1", "yes"}
            stages: List[Dict[str, Any]] = []

            for stage_uri in self.graph.objects(tmpl, self.ONTO.hasTemplateStage):
                stage_type = "ProductionStage"
                for t in self.graph.objects(stage_uri, RDF.type):
                    local = self._local_name(t)
                    if local != "ProductionStage" and local.endswith("Stage"):
                        stage_type = local
                        break

                activities: List[Dict[str, Any]] = []
                for act_uri in self.graph.objects(stage_uri, self.ONTO.hasTemplateActivity):
                    resources = []
                    for res_uri in self.graph.objects(act_uri, self.MC.requiresResource):
                        resources.append(
                            {
                                "uri": str(res_uri),
                                "label": str(self.graph.value(res_uri, RDFS.label) or self._local_name(res_uri)),
                            }
                        )
                    activities.append(
                        {
                            "uri": str(act_uri),
                            "name": str(self.graph.value(act_uri, self.MC.activityName) or self._local_name(act_uri)),
                            "sequence": int(self.graph.value(act_uri, self.MC.sequence) or 0),
                            "resources": resources,
                        }
                    )
                activities.sort(key=lambda x: x["sequence"])

                stages.append(
                    {
                        "uri": str(stage_uri),
                        "name": str(self.graph.value(stage_uri, self.MC.stageName) or self._local_name(stage_uri)),
                        "description": str(self.graph.value(stage_uri, self.MC.stageDescription) or ""),
                        "location": str(self.graph.value(stage_uri, self.MC.location) or ""),
                        "order": int(self.graph.value(stage_uri, self.ONTO.stageOrder) or 0),
                        "type": stage_type,
                        "activities": activities,
                    }
                )
            stages.sort(key=lambda x: x["order"])

            rows.append(
                {
                    "uri": str(tmpl),
                    "label": label,
                    "applies_to": applies_to,
                    "material_keywords": material_keywords,
                    "is_default": is_default,
                    "stages": stages,
                }
            )
        rows.sort(key=lambda x: x["uri"])
        return rows

    def _read_emission_factors(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for ef in self.graph.subjects(RDF.type, self.CARBON.EmissionFactor):
            rows.append(
                {
                    "uri": str(ef),
                    "label": str(self.graph.value(ef, RDFS.label) or self._local_name(ef)),
                    "keyword": str(self.graph.value(ef, self.ONTO.forMaterialKeyword) or ""),
                    "factor_value": safe_float(self.graph.value(ef, self.CARBON.factorValue), 0.0),
                    "factor_unit": str(self.graph.value(ef, self.CARBON.factorUnit) or ""),
                    "factor_source": str(self.graph.value(ef, self.CARBON.factorSource) or ""),
                }
            )
        rows.sort(key=lambda x: len(normalize_text(x["keyword"])), reverse=True)
        return rows

    def _read_material_densities(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for mat in self.graph.subjects(RDF.type, self.BIM.IfcMaterial):
            keyword = str(self.graph.value(mat, self.ONTO.materialKeyword) or "")
            density = self.graph.value(mat, self.ONTO.materialDensity)
            if keyword and density is not None:
                rows.append({"uri": str(mat), "keyword": keyword, "density": safe_float(density, 1200.0)})
        rows.sort(key=lambda x: len(normalize_text(x["keyword"])), reverse=True)
        return rows

    def _read_activity_energy_profiles(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for profile in self.graph.subjects(RDF.type, self.ONTO.ActivityEnergyProfile):
            rows.append(
                {
                    "uri": str(profile),
                    "for_activity": str(self.graph.value(profile, self.ONTO.forActivity) or ""),
                    "energy_type": str(self.graph.value(profile, self.ONTO.energyType) or ""),
                    "consumption_rate": self.graph.value(profile, self.ONTO.consumptionRate),
                    "consumption_unit": str(self.graph.value(profile, self.ONTO.consumptionUnit) or ""),
                    "consumption_basis": str(self.graph.value(profile, self.ONTO.consumptionBasis) or ""),
                    "data_source": str(self.graph.value(profile, self.ONTO.dataSource) or ""),
                }
            )
        for row in rows:
            row["consumption_rate"] = safe_float(row.get("consumption_rate"), 0.0)
        rows.sort(key=lambda x: x["for_activity"])
        return rows

    def _collect_material_keywords(self) -> List[str]:
        keys: Set[str] = set()
        for t in self.templates:
            for kw in t.get("material_keywords", []):
                if kw:
                    keys.add(str(kw))
        for f in self.emission_factors:
            if f.get("keyword"):
                n = normalize_text(str(f["keyword"]))
                if n not in {"electricity", "diesel", "gas", "lpg", "naturalgas"}:
                    keys.add(str(f["keyword"]))
        for d in self.material_densities:
            if d.get("keyword"):
                keys.add(str(d["keyword"]))
        return sorted(keys, key=lambda x: len(normalize_text(x)), reverse=True)

    def match_production_template(self, ifc_type: str, material_text: str, element_properties: Dict[str, Any]) -> Dict[str, Any]:
        ifc_short = ifc_type.replace("StandardCase", "")
        material_norm = normalize_text(material_text)
        evidence_fields = [
            str(element_properties.get("Reference", "") or ""),
            str(element_properties.get("Name", "") or ""),
            str(element_properties.get("ObjectType", "") or ""),
            str(element_properties.get("typeObjectName", "") or ""),
        ]
        evidence_norm = normalize_text(" ".join(evidence_fields))

        def contains_keyword(keyword: str, text_norm: str) -> bool:
            kn = normalize_text(keyword)
            return bool(kn and kn in text_norm)

        # 1) IFC class + material keyword
        ranked = []
        for t in self.templates:
            if ifc_short in t["applies_to"]:
                matched = [kw for kw in t["material_keywords"] if contains_keyword(kw, material_norm)]
                if matched:
                    ranked.append((t, matched, max(len(normalize_text(x)) for x in matched)))
        if ranked:
            ranked.sort(key=lambda x: x[2], reverse=True)
            t, matched, _ = ranked[0]
            return {
                "template_uri": t["uri"],
                "template_label": t["label"],
                "match_level": "ifc_type_and_material",
                "matched_ifc_type": ifc_short,
                "matched_keywords": matched,
                "confidence": 0.95,
                "reason": f"IFC class {ifc_short} and material keyword matched",
                "fallback_used": False,
            }

        # 2) IFC class + reference/name/objecttype keyword
        ranked = []
        for t in self.templates:
            if ifc_short in t["applies_to"]:
                matched = [kw for kw in t["material_keywords"] if contains_keyword(kw, evidence_norm)]
                if matched:
                    ranked.append((t, matched, max(len(normalize_text(x)) for x in matched)))
        if ranked:
            ranked.sort(key=lambda x: x[2], reverse=True)
            t, matched, _ = ranked[0]
            return {
                "template_uri": t["uri"],
                "template_label": t["label"],
                "match_level": "ifc_type_and_reference",
                "matched_ifc_type": ifc_short,
                "matched_keywords": matched,
                "confidence": 0.82,
                "reason": f"IFC class {ifc_short} matched with textual evidence fields",
                "fallback_used": False,
            }

        # 3) IFC class only
        for t in self.templates:
            if ifc_short in t["applies_to"]:
                return {
                    "template_uri": t["uri"],
                    "template_label": t["label"],
                    "match_level": "ifc_type_only",
                    "matched_ifc_type": ifc_short,
                    "matched_keywords": [],
                    "confidence": 0.70,
                    "reason": f"IFC class {ifc_short} matched template appliesTo",
                    "fallback_used": False,
                }

        # 4) material keyword only
        ranked = []
        for t in self.templates:
            matched = [kw for kw in t["material_keywords"] if contains_keyword(kw, material_norm)]
            if matched:
                ranked.append((t, matched, max(len(normalize_text(x)) for x in matched)))
        if ranked:
            ranked.sort(key=lambda x: x[2], reverse=True)
            t, matched, _ = ranked[0]
            return {
                "template_uri": t["uri"],
                "template_label": t["label"],
                "match_level": "material_only",
                "matched_ifc_type": "",
                "matched_keywords": matched,
                "confidence": 0.55,
                "reason": "Material keyword matched without IFC class alignment",
                "fallback_used": False,
            }

        # 5) explicit fallback
        if self.templates:
            default_templates = [t for t in self.templates if t.get("is_default")]
            t = default_templates[0] if default_templates else self.templates[0]
            return {
                "template_uri": t["uri"],
                "template_label": t["label"],
                "match_level": "fallback",
                "matched_ifc_type": ifc_short,
                "matched_keywords": [],
                "confidence": 0.35,
                "reason": "No strong match; fallback to first ontology template",
                "fallback_used": True,
            }

        # 6) no match
        return {
            "template_uri": "",
            "template_label": "",
            "match_level": "no_match",
            "matched_ifc_type": ifc_short,
            "matched_keywords": [],
            "confidence": 0.0,
            "reason": "No ProductionTemplate found in ontology",
            "fallback_used": False,
        }

    def match_emission_factor(self, material_text: str, allow_default: bool = True) -> Dict[str, Any]:
        target = normalize_text(material_text)
        for ef in self.emission_factors:
            keyword = normalize_text(ef["keyword"])
            if keyword and keyword in target:
                return {
                    "factor_uri": ef["uri"],
                    "factor_value": ef["factor_value"],
                    "factor_unit": ef["factor_unit"],
                    "factor_source": ef["factor_source"],
                    "matched_keyword": ef["keyword"],
                    "confidence": 0.95,
                    "fallback_used": False,
                }
        if allow_default:
            for ef in self.emission_factors:
                keyword = normalize_text(ef["keyword"])
                if keyword in {"default", "ef_default", normalize_text("默认")}:
                    return {
                        "factor_uri": ef["uri"],
                        "factor_value": ef["factor_value"],
                        "factor_unit": ef["factor_unit"],
                        "factor_source": ef["factor_source"],
                        "matched_keyword": ef["keyword"],
                        "confidence": 0.50,
                        "fallback_used": True,
                    }
        return {
            "factor_uri": "",
            "factor_value": None,
            "factor_unit": "",
            "factor_source": "",
            "matched_keyword": "",
            "confidence": 0.0,
            "fallback_used": True,
            "no_match": True,
        }

    def match_energy_emission_factor(self, energy_type: str) -> Dict[str, Any]:
        return self.match_emission_factor(energy_type, allow_default=False)

    def match_activity_energy(self, activity_uri: str) -> Dict[str, Any]:
        target = str(activity_uri or "")
        for profile in self.activity_energy_profiles:
            if profile.get("for_activity") == target:
                return {
                    "found": True,
                    "uri": profile.get("uri", ""),
                    "forActivity": profile.get("for_activity", ""),
                    "energyType": profile.get("energy_type", ""),
                    "consumptionRate": safe_float(profile.get("consumption_rate"), 0.0),
                    "consumptionUnit": profile.get("consumption_unit", ""),
                    "consumptionBasis": profile.get("consumption_basis", ""),
                    "dataSource": profile.get("data_source", ""),
                }
        return {
            "found": False,
            "uri": "",
            "forActivity": target,
            "energyType": "",
            "consumptionRate": None,
            "consumptionUnit": "",
            "consumptionBasis": "",
            "dataSource": "",
        }

    def match_density(self, material_text: str) -> Dict[str, Any]:
        target = normalize_text(material_text)
        for row in self.material_densities:
            keyword = normalize_text(row["keyword"])
            if keyword and keyword in target:
                return {
                    "density": row["density"],
                    "unit": "kg/m3",
                    "matched_keyword": row["keyword"],
                    "confidence": 0.95,
                    "fallback_used": False,
                }
        for row in self.material_densities:
            keyword = normalize_text(row["keyword"])
            if keyword in {"default", normalize_text("默认")}:
                return {
                    "density": row["density"],
                    "unit": "kg/m3",
                    "matched_keyword": row["keyword"],
                    "confidence": 0.50,
                    "fallback_used": True,
                }
        return {
            "density": 1200.0,
            "unit": "kg/m3",
            "matched_keyword": "",
            "confidence": 0.20,
            "fallback_used": True,
        }


class OntologySchemaReader:
    """
    Lightweight ontology reader for Section 3.2 backbone mode.
    It only provides schema/normalization hints and must not instantiate
    production chains or carbon calculation facts.
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
        self.property_map: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.quantity_map: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.quantity_units: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.storey_map: Dict[str, str] = {}
        self.type_map: Dict[str, Dict[str, str]] = {}
        self._build_caches()

    def _build_caches(self) -> None:
        # Materials
        for rel in self.ifc.by_type("IfcRelAssociatesMaterial"):
            mats = self._extract_materials(rel.RelatingMaterial)
            for obj in getattr(rel, "RelatedObjects", []) or []:
                gid = getattr(obj, "GlobalId", None)
                if not gid:
                    continue
                self.material_assoc[gid].extend(mats)

        # Properties and quantities
        for rel in self.ifc.by_type("IfcRelDefinesByProperties"):
            pset = rel.RelatingPropertyDefinition
            for obj in getattr(rel, "RelatedObjects", []) or []:
                gid = getattr(obj, "GlobalId", None)
                if not gid:
                    continue
                if pset.is_a("IfcPropertySet"):
                    for prop in pset.HasProperties:
                        if prop.is_a("IfcPropertySingleValue") and prop.NominalValue is not None:
                            wrapped = prop.NominalValue.wrappedValue
                            if isinstance(wrapped, (str, int, float, bool)):
                                self.property_map[gid][prop.Name] = wrapped
                elif pset.is_a("IfcElementQuantity"):
                    for qty in pset.Quantities:
                        q_value, q_unit = self._extract_quantity_value(qty)
                        if q_value is not None:
                            self.quantity_map[gid][qty.Name] = q_value
                            self.quantity_units[gid][qty.Name] = q_unit

        # Storey containment
        for rel in self.ifc.by_type("IfcRelContainedInSpatialStructure"):
            structure_name = str(getattr(rel.RelatingStructure, "Name", "") or "")
            for elem in getattr(rel, "RelatedElements", []) or []:
                gid = getattr(elem, "GlobalId", None)
                if gid:
                    self.storey_map[gid] = structure_name

        # Type object mapping
        for rel in self.ifc.by_type("IfcRelDefinesByType"):
            t_obj = getattr(rel, "RelatingType", None)
            for elem in getattr(rel, "RelatedObjects", []) or []:
                gid = getattr(elem, "GlobalId", None)
                if not gid or t_obj is None:
                    continue
                self.type_map[gid] = {
                    "type_object_name": str(getattr(t_obj, "Name", "") or ""),
                    "type_object_ifc_type": str(t_obj.is_a()),
                    "type_object_global_id": str(getattr(t_obj, "GlobalId", "") or ""),
                }

    def _extract_quantity_value(self, qty: Any) -> Tuple[Optional[float], str]:
        if qty.is_a("IfcQuantityLength"):
            return round(safe_float(getattr(qty, "LengthValue", 0.0), 0.0), 6), "m"
        if qty.is_a("IfcQuantityArea"):
            return round(safe_float(getattr(qty, "AreaValue", 0.0), 0.0), 6), "m2"
        if qty.is_a("IfcQuantityVolume"):
            return round(safe_float(getattr(qty, "VolumeValue", 0.0), 0.0), 6), "m3"
        if qty.is_a("IfcQuantityCount"):
            return round(safe_float(getattr(qty, "CountValue", 0.0), 0.0), 6), "unit"
        if qty.is_a("IfcQuantityWeight"):
            return round(safe_float(getattr(qty, "WeightValue", 0.0), 0.0), 6), "kg"
        return None, ""

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
            mat_name = f"Inferred_{ifc_type.replace('Ifc', '') or 'Material'}"
            confidence = 0.30

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

                properties = dict(self.property_map.get(gid, {}))
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

                quantities = dict(self.quantity_map.get(gid, {}))
                quantity_units = dict(self.quantity_units.get(gid, {}))
                storey_name = str(self.storey_map.get(gid, "") or "")

                materials = list(self.material_assoc.get(gid, []))
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
        return {"spatial": self._extract_spatial(), "elements": self._extract_elements()}


class ProductionInferenceEngine:
    def __init__(self, graph: DM2CGraph, ontology: OntologyReader):
        self.graph = graph
        self.ontology = ontology
        self.resource_cache: Dict[str, str] = {}
        self.template_cache: Dict[str, str] = {}

    def _template_node(self, template_uri: str, template_label: str) -> str:
        if template_uri in self.template_cache:
            return self.template_cache[template_uri]
        nid = f"template:{slugify(template_uri or template_label, 100)}"
        self.graph.add_node(
            nid,
            ["ProductionTemplate"],
            domain="Production",
            semantic_origin="ontology_reference",
            reasoning_role="production_template_reference",
            props={"templateUri": template_uri, "templateLabel": template_label},
        )
        self.template_cache[template_uri] = nid
        return nid

    def _resource_node(self, uri: str, label: str) -> str:
        key = uri or label
        if key in self.resource_cache:
            return self.resource_cache[key]
        nid = f"resource:{slugify(key, 100)}"
        self.graph.add_node(
            nid,
            ["Resource"],
            domain="Production",
            semantic_origin="ontology_reference",
            reasoning_role="resource_reference",
            props={"ontologyUri": uri, "name": label},
        )
        self.resource_cache[key] = nid
        return nid

    def infer_for_element(
        self,
        element: ExtractedElement,
        element_node_id: str,
        module_node_id: str,
        material_node_ids: List[str],
    ) -> Dict[str, Any]:
        component_id = f"bc:{element.global_id}"
        self.graph.add_node(
            component_id,
            ["BuildingComponent"],
            domain="Production",
            semantic_origin="ontology_inferred",
            reasoning_role="component_alignment",
            props={"ifcGlobalId": element.global_id, "ifcType": element.ifc_type, "name": element.name},
        )
        self.graph.add_edge(
            element_node_id,
            component_id,
            "ALIGNED_AS",
            semantic_origin="ontology_inferred",
            inference_rule="ifc_element_to_building_component_alignment",
            props={},
        )

        material_text = " ".join(m.name for m in element.materials if m.name)
        match = self.ontology.match_production_template(
            ifc_type=element.ifc_type,
            material_text=material_text,
            element_properties={
                "Reference": element.properties.get("Reference", ""),
                "Name": element.name,
                "ObjectType": element.object_type,
                "typeObjectName": element.type_object_name,
            },
        )

        evidence_id = f"map_evidence:{element.global_id}"
        self.graph.add_node(
            evidence_id,
            ["MappingEvidence"],
            domain="Mapping",
            semantic_origin="ontology_inferred",
            reasoning_role="template_matching_evidence",
            props={
                "ifcGlobalId": element.global_id,
                "ifcType": element.ifc_type,
                "materialText": material_text,
                "matchLevel": match["match_level"],
                "matchedKeywords": json.dumps(match.get("matched_keywords", []), ensure_ascii=False),
                "confidence": match["confidence"],
                "reason": match["reason"],
                "fallbackUsed": match["fallback_used"],
            },
        )
        self.graph.add_edge(
            component_id,
            evidence_id,
            "HAS_MAPPING_EVIDENCE",
            semantic_origin="ontology_inferred",
            inference_rule="template_matching_evidence_creation",
            props={},
        )

        template_application_id = f"template_application:{element.global_id}"
        self.graph.add_node(
            template_application_id,
            ["TemplateApplication"],
            domain="Mapping",
            semantic_origin="ontology_inferred",
            reasoning_role="template_application_record",
            props={
                "ifcGlobalId": element.global_id,
                "ifcType": element.ifc_type,
                "selectedTemplateUri": match["template_uri"],
                "selectedTemplateLabel": match["template_label"],
                "matchLevel": match["match_level"],
                "confidence": match["confidence"],
                "fallbackUsed": match["fallback_used"],
            },
        )
        self.graph.add_edge(
            component_id,
            template_application_id,
            "HAS_TEMPLATE_APPLICATION",
            semantic_origin="ontology_inferred",
            inference_rule="template_application_binding",
            props={},
        )
        self.graph.add_edge(
            template_application_id,
            evidence_id,
            "MATCHED_BY_RULE",
            semantic_origin="ontology_inferred",
            inference_rule="template_match_rule",
            props={},
        )

        mapping_issue_id = ""
        selected_template = None
        if match["template_uri"]:
            for tmpl in self.ontology.templates:
                if tmpl["uri"] == match["template_uri"]:
                    selected_template = tmpl
                    break
            template_node_id = self._template_node(match["template_uri"], match["template_label"])
            self.graph.add_edge(
                template_application_id,
                template_node_id,
                "APPLIES_TEMPLATE",
                semantic_origin="ontology_reference",
                inference_rule="selected_template_reference",
                props={},
            )
        else:
            mapping_issue_id = f"mapping_issue:{element.global_id}"
            self.graph.add_node(
                mapping_issue_id,
                ["MappingIssue"],
                domain="Mapping",
                semantic_origin="fallback",
                reasoning_role="template_mapping_issue",
                props={"ifcGlobalId": element.global_id, "issue": "No template matched", "reason": match["reason"]},
            )
            self.graph.add_edge(
                component_id,
                mapping_issue_id,
                "HAS_MAPPING_EVIDENCE",
                semantic_origin="fallback",
                inference_rule="no_template_issue",
                props={},
            )

        module_production_id = f"module_production:{element.global_id}"
        self.graph.add_node(
            module_production_id,
            ["ModuleProduction"],
            domain="Production",
            semantic_origin="ontology_inferred",
            reasoning_role="module_production_instance",
            props={"ifcGlobalId": element.global_id, "name": f"Production_{first_non_empty([element.name, element.global_id])}"},
        )
        self.graph.add_edge(
            module_production_id,
            component_id,
            "HAS_INPUT",
            semantic_origin="ontology_inferred",
            inference_rule="module_production_input_binding",
            props={},
        )
        self.graph.add_edge(
            module_production_id,
            module_node_id,
            "HAS_OUTPUT",
            semantic_origin="ontology_inferred",
            inference_rule="module_production_output_binding",
            props={},
        )

        stage_ids: List[str] = []
        activity_ids: List[str] = []
        activity_to_stage_map: Dict[str, str] = {}
        if selected_template:
            prev_stage_id = ""
            for stage in selected_template["stages"]:
                stage_id = f"stage:{element.global_id}:{int(stage.get('order', 0))}:{slugify(stage.get('name', ''), 40)}"
                stage_labels = ["ProductionStage", stage.get("type", "ProductionStage")]
                self.graph.add_node(
                    stage_id,
                    stage_labels,
                    domain="Production",
                    semantic_origin="ontology_inferred",
                    reasoning_role="production_stage_instance",
                    props={
                        "ifcGlobalId": element.global_id,
                        "stageName": stage.get("name", ""),
                        "stageDescription": stage.get("description", ""),
                        "location": stage.get("location", ""),
                        "stageOrder": stage.get("order", 0),
                        "templateUri": selected_template["uri"],
                    },
                )
                stage_ids.append(stage_id)
                self.graph.add_edge(
                    module_production_id,
                    stage_id,
                    "HAS_PRODUCTION_STAGE",
                    semantic_origin="ontology_inferred",
                    inference_rule="template_stage_instantiation",
                    props={},
                )
                self.graph.add_edge(
                    component_id,
                    stage_id,
                    "REQUIRES_STAGE",
                    semantic_origin="ontology_inferred",
                    inference_rule="component_stage_requirement",
                    props={},
                )
                if prev_stage_id:
                    self.graph.add_edge(
                        prev_stage_id,
                        stage_id,
                        "PRECEDES_STAGE",
                        semantic_origin="ontology_inferred",
                        inference_rule="template_stage_sequence",
                        props={},
                    )
                prev_stage_id = stage_id

                prev_activity_id = ""
                for act in stage.get("activities", []):
                    activity_id = f"activity:{element.global_id}:{stage.get('order', 0)}:{int(act.get('sequence', 0))}:{slugify(act.get('name', ''), 40)}"
                    self.graph.add_node(
                        activity_id,
                        ["ManufacturingActivity"],
                        domain="Production",
                        semantic_origin="ontology_inferred",
                        reasoning_role="manufacturing_activity_instance",
                        props={
                            "ifcGlobalId": element.global_id,
                            "activityName": act.get("name", ""),
                            "activitySequence": act.get("sequence", 0),
                            "templateUri": act.get("uri", ""),
                        },
                    )
                    activity_ids.append(activity_id)
                    activity_to_stage_map[activity_id] = stage_id
                    self.graph.add_edge(
                        stage_id,
                        activity_id,
                        "HAS_PROCESS",
                        semantic_origin="ontology_inferred",
                        inference_rule="template_activity_instantiation",
                        props={},
                    )
                    if prev_activity_id:
                        self.graph.add_edge(
                            prev_activity_id,
                            activity_id,
                            "PRECEDES_ACTIVITY",
                            semantic_origin="ontology_inferred",
                            inference_rule="template_activity_sequence",
                            props={},
                        )
                    prev_activity_id = activity_id

                    for res in act.get("resources", []):
                        res_id = self._resource_node(res.get("uri", ""), res.get("label", "Resource"))
                        self.graph.add_edge(
                            activity_id,
                            res_id,
                            "REQUIRES_RESOURCE",
                            semantic_origin="ontology_inferred",
                            inference_rule="template_resource_binding",
                            props={},
                        )
                    for mat_id in material_node_ids:
                        self.graph.add_edge(
                            activity_id,
                            mat_id,
                            "CONSUMES_MATERIAL",
                            semantic_origin="ontology_inferred",
                            inference_rule="activity_material_consumption",
                            props={},
                        )

        return {
            "building_component_id": component_id,
            "template_application_id": template_application_id,
            "mapping_evidence_id": evidence_id,
            "mapping_issue_id": mapping_issue_id,
            "module_production_id": module_production_id,
            "stage_ids": stage_ids,
            "activity_ids": activity_ids,
            "activity_to_stage_map": activity_to_stage_map,
            "template_match": match,
        }

class CarbonReasoningEngine:
    def __init__(self, graph: DM2CGraph, ontology: OntologyReader):
        self.graph = graph
        self.ontology = ontology
        self.emission_factor_cache: Dict[str, str] = {}

    def _emission_factor_node(self, factor_match: Dict[str, Any], key_prefix: str = "emission_factor") -> str:
        key = factor_match.get("factor_uri") or (
            f"{factor_match.get('matched_keyword', '')}|"
            f"{factor_match.get('factor_value', '')}|"
            f"{factor_match.get('factor_unit', '')}"
        )
        if key in self.emission_factor_cache:
            return self.emission_factor_cache[key]

        node_id = f"{key_prefix}:{slugify(key, 120)}"
        semantic_origin = "fallback" if factor_match.get("fallback_used") else "ontology_reference"
        self.graph.add_node(
            node_id,
            ["EmissionFactor"],
            domain="Carbon",
            semantic_origin=semantic_origin,
            reasoning_role="emission_factor_reference",
            props={
                "factorUri": factor_match.get("factor_uri", ""),
                "factorValue": factor_match.get("factor_value"),
                "factorUnit": factor_match.get("factor_unit", ""),
                "factorSource": factor_match.get("factor_source", ""),
                "matchedKeyword": factor_match.get("matched_keyword", ""),
                "confidence": factor_match.get("confidence", 0.0),
                "fallbackUsed": factor_match.get("fallback_used", False),
            },
        )
        self.emission_factor_cache[key] = node_id
        return node_id

    @staticmethod
    def _find_quantity(
        quantities: Dict[str, float],
        quantity_units: Dict[str, str],
        preferred_names: List[str],
    ) -> Tuple[str, Optional[float], str]:
        # Exact match first
        for preferred in preferred_names:
            pn = normalize_text(preferred)
            for q_name, value in quantities.items():
                if normalize_text(q_name) == pn:
                    return q_name, value, str(quantity_units.get(q_name, "") or "")

        # Partial match second
        for preferred in preferred_names:
            pn = normalize_text(preferred)
            for q_name, value in quantities.items():
                if pn and pn in normalize_text(q_name):
                    return q_name, value, str(quantity_units.get(q_name, "") or "")
        return "", None, ""

    def _select_quantity_basis(
        self,
        element: ExtractedElement,
        factor_unit: str,
        density_match: Dict[str, Any],
    ) -> Dict[str, Any]:
        quantities = element.quantities
        quantity_units = element.quantity_units
        unit_norm = normalize_text(factor_unit)

        # kgCO2e/kg
        if unit_norm == normalize_text("kgCO2e/kg"):
            q_name, q_val, q_unit = self._find_quantity(
                quantities,
                quantity_units,
                ["Weight", "NetWeight", "GrossWeight"],
            )
            if q_val is not None:
                return {
                    "ok": True,
                    "quantity_basis": "weight",
                    "input_quantity_name": q_name,
                    "input_quantity_value": q_val,
                    "input_quantity_unit": q_unit or "kg",
                    "quantity_for_factor": q_val,
                    "quantity_for_factor_unit": "kg",
                    "density_used": None,
                    "formula_text": "Emission = Weight(kg) × EmissionFactor(kgCO2e/kg)",
                    "issue": "",
                }

            q_name, q_val, q_unit = self._find_quantity(
                quantities,
                quantity_units,
                ["NetVolume", "GrossVolume", "Volume"],
            )
            if q_val is None:
                return {
                    "ok": False,
                    "quantity_basis": "weight",
                    "input_quantity_name": "",
                    "input_quantity_value": None,
                    "input_quantity_unit": "",
                    "quantity_for_factor": None,
                    "quantity_for_factor_unit": "kg",
                    "density_used": None,
                    "formula_text": "",
                    "issue": "Missing IfcQuantityWeight and volume quantity for kgCO2e/kg calculation",
                }

            density = density_match.get("density")
            if density is None:
                return {
                    "ok": False,
                    "quantity_basis": "volume_x_density",
                    "input_quantity_name": q_name,
                    "input_quantity_value": q_val,
                    "input_quantity_unit": q_unit or "m3",
                    "quantity_for_factor": None,
                    "quantity_for_factor_unit": "kg",
                    "density_used": None,
                    "formula_text": "",
                    "issue": "Missing density for mass conversion from volume",
                }
            mass_kg = q_val * safe_float(density)
            return {
                "ok": True,
                "quantity_basis": "volume_x_density",
                "input_quantity_name": q_name,
                "input_quantity_value": q_val,
                "input_quantity_unit": q_unit or "m3",
                "quantity_for_factor": mass_kg,
                "quantity_for_factor_unit": "kg",
                "density_used": safe_float(density),
                "formula_text": "Emission = Volume(m3) × Density(kg/m3) × EmissionFactor(kgCO2e/kg)",
                "issue": "",
            }

        # kgCO2e/m3
        if unit_norm == normalize_text("kgCO2e/m3"):
            q_name, q_val, q_unit = self._find_quantity(
                quantities,
                quantity_units,
                ["NetVolume", "GrossVolume", "Volume"],
            )
            if q_val is None:
                return {
                    "ok": False,
                    "quantity_basis": "volume",
                    "input_quantity_name": "",
                    "input_quantity_value": None,
                    "input_quantity_unit": "",
                    "quantity_for_factor": None,
                    "quantity_for_factor_unit": "m3",
                    "density_used": None,
                    "formula_text": "",
                    "issue": "Missing volume quantity required by kgCO2e/m3 factor",
                }
            return {
                "ok": True,
                "quantity_basis": "volume",
                "input_quantity_name": q_name,
                "input_quantity_value": q_val,
                "input_quantity_unit": q_unit or "m3",
                "quantity_for_factor": q_val,
                "quantity_for_factor_unit": "m3",
                "density_used": None,
                "formula_text": "Emission = Volume(m3) × EmissionFactor(kgCO2e/m3)",
                "issue": "",
            }

        # kgCO2e/m2
        if unit_norm == normalize_text("kgCO2e/m2"):
            q_name, q_val, q_unit = self._find_quantity(
                quantities,
                quantity_units,
                ["NetSideArea", "GrossSideArea", "GrossArea", "OuterSurfaceArea", "Area"],
            )
            if q_val is None:
                return {
                    "ok": False,
                    "quantity_basis": "area",
                    "input_quantity_name": "",
                    "input_quantity_value": None,
                    "input_quantity_unit": "",
                    "quantity_for_factor": None,
                    "quantity_for_factor_unit": "m2",
                    "density_used": None,
                    "formula_text": "",
                    "issue": "Missing area quantity required by kgCO2e/m2 factor",
                }
            return {
                "ok": True,
                "quantity_basis": "area",
                "input_quantity_name": q_name,
                "input_quantity_value": q_val,
                "input_quantity_unit": q_unit or "m2",
                "quantity_for_factor": q_val,
                "quantity_for_factor_unit": "m2",
                "density_used": None,
                "formula_text": "Emission = Area(m2) × EmissionFactor(kgCO2e/m2)",
                "issue": "",
            }

        # kgCO2e/unit
        if unit_norm in {normalize_text("kgCO2e/unit"), normalize_text("kgCO2e/count")}:
            q_name, q_val, q_unit = self._find_quantity(
                quantities,
                quantity_units,
                ["Count", "Quantity", "UnitCount"],
            )
            if q_val is None:
                return {
                    "ok": False,
                    "quantity_basis": "count",
                    "input_quantity_name": "",
                    "input_quantity_value": None,
                    "input_quantity_unit": "",
                    "quantity_for_factor": None,
                    "quantity_for_factor_unit": "unit",
                    "density_used": None,
                    "formula_text": "",
                    "issue": "Missing count quantity required by kgCO2e/unit factor",
                }
            return {
                "ok": True,
                "quantity_basis": "count",
                "input_quantity_name": q_name,
                "input_quantity_value": q_val,
                "input_quantity_unit": q_unit or "unit",
                "quantity_for_factor": q_val,
                "quantity_for_factor_unit": "unit",
                "density_used": None,
                "formula_text": "Emission = Count(unit) × EmissionFactor(kgCO2e/unit)",
                "issue": "",
            }

        return {
            "ok": False,
            "quantity_basis": "unknown",
            "input_quantity_name": "",
            "input_quantity_value": None,
            "input_quantity_unit": "",
            "quantity_for_factor": None,
            "quantity_for_factor_unit": "",
            "density_used": None,
            "formula_text": "",
            "issue": f"Unsupported emission factor unit: {factor_unit}",
        }

    def reason_for_element(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        material_bindings: List[Tuple[ExtractedMaterial, str]],
        quantity_node_map: Dict[str, str],
        production_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        if not material_bindings:
            material_bindings = [(
                ExtractedMaterial(
                    name="UnknownMaterial",
                    source="fallback",
                    explicit=False,
                    fallback_material=True,
                    confidence=0.1,
                    evidence_fields=[],
                ),
                "",
            )]

        for idx, (material, material_node_id) in enumerate(material_bindings, start=1):
            factor_match = self.ontology.match_emission_factor(material.name)
            density_match = self.ontology.match_density(material.name)
            selection = self._select_quantity_basis(element, factor_match.get("factor_unit", ""), density_match)

            emission_id = f"carbon_emission:{element.global_id}:{idx}"
            driver_id = f"consumption_driver:{element.global_id}:{idx}"
            cq_id = f"consumption_quantity:{element.global_id}:{idx}"
            calc_id = f"calculation_record:{element.global_id}:{idx}"

            calculation_status = "complete" if selection.get("ok") else "incomplete"
            factor_value_raw = factor_match.get("factor_value")
            factor_value = safe_float(factor_value_raw, 0.0) if factor_value_raw is not None else None
            q_for_factor = selection.get("quantity_for_factor")
            emission_value = round(safe_float(q_for_factor, 0.0) * safe_float(factor_value, 0.0), 6) if calculation_status == "complete" else None
            factor_node_id = ""

            if factor_match.get("no_match"):
                calculation_status = "incomplete"
                emission_value = None
                selection["ok"] = False
                selection["issue"] = f"No emission factor found in ontology for material '{material.name}'"
            elif factor_value is None:
                calculation_status = "incomplete"
                emission_value = None
                selection["ok"] = False
                selection["issue"] = f"Emission factor value missing for material '{material.name}'"
            else:
                factor_node_id = self._emission_factor_node(factor_match, key_prefix="emission_factor")

            # Zero-value check: do not silently accept zero emissions
            if calculation_status == "complete":
                if factor_value == 0.0:
                    calculation_status = "incomplete"
                    emission_value = None
                    selection["ok"] = False
                    selection["issue"] = (
                        f"Emission factor value is 0.0 for material '{material.name}' "
                        f"- likely missing or placeholder data in ontology"
                    )
                elif q_for_factor is not None and q_for_factor == 0.0:
                    calculation_status = "incomplete"
                    emission_value = None
                    selection["ok"] = False
                    selection["issue"] = (
                        f"Computed consumption quantity is 0.0 for material '{material.name}' "
                        f"- IFC quantity value may be missing or zero"
                    )

            self.graph.add_node(
                driver_id,
                ["ConsumptionDriver", "MaterialConsumption"],
                domain="Carbon",
                semantic_origin="calculated",
                reasoning_role="consumption_driver",
                props={
                    "ifcGlobalId": element.global_id,
                    "materialText": material.name,
                    "driverType": "material_embodied_carbon",
                    "calculationStatus": calculation_status,
                },
            )
            self.graph.add_node(
                cq_id,
                ["ConsumptionQuantity"],
                domain="Carbon",
                semantic_origin="calculated",
                reasoning_role="consumption_quantity",
                props={
                    "ifcGlobalId": element.global_id,
                    "inputQuantityName": selection.get("input_quantity_name", ""),
                    "inputQuantityValue": selection.get("input_quantity_value"),
                    "inputQuantityUnit": selection.get("input_quantity_unit", ""),
                    "quantityForFactor": selection.get("quantity_for_factor"),
                    "quantityForFactorUnit": selection.get("quantity_for_factor_unit", ""),
                    "quantityBasis": selection.get("quantity_basis", ""),
                    "densityUsed": selection.get("density_used"),
                },
            )
            self.graph.add_node(
                emission_id,
                ["CarbonEmission"],
                domain="Carbon",
                semantic_origin="calculated",
                reasoning_role="carbon_emission_result",
                props={
                    "ifcGlobalId": element.global_id,
                    "materialText": material.name,
                    "value": emission_value,
                    "unit": "kgCO2e",
                    "lifeCycleStage": "A1-A3",
                    "emissionScope": "material_embodied",
                    "calculationStatus": calculation_status,
                    "formulaText": selection.get("formula_text", ""),
                    "quantityBasis": selection.get("quantity_basis", ""),
                    "factorValue": factor_value,
                    "factorUnit": factor_match.get("factor_unit", ""),
                    "densityUsed": selection.get("density_used"),
                    "matchReason": factor_match.get("matched_keyword", ""),
                    "fallbackUsed": bool(factor_match.get("fallback_used")) or bool(density_match.get("fallback_used")),
                },
            )
            self.graph.add_node(
                calc_id,
                ["CalculationRecord"],
                domain="Calculation",
                semantic_origin="calculated",
                reasoning_role="calculation_provenance",
                props={
                    "ifcGlobalId": element.global_id,
                    "formulaText": selection.get("formula_text", ""),
                    "inputQuantityName": selection.get("input_quantity_name", ""),
                    "inputQuantityValue": selection.get("input_quantity_value"),
                    "inputQuantityUnit": selection.get("input_quantity_unit", ""),
                    "densityValue": selection.get("density_used"),
                    "emissionFactorValue": factor_value,
                    "emissionFactorUnit": factor_match.get("factor_unit", ""),
                    "calculatedValue": emission_value,
                    "calculationStatus": calculation_status,
                },
            )

            # Core carbon reasoning path
            self.graph.add_edge(
                emission_id,
                element_node_id,
                "EMISSION_OF",
                semantic_origin="calculated",
                inference_rule="inverse_emission_trace",
                props={},
            )
            self.graph.add_edge(
                emission_id,
                driver_id,
                "HAS_CARBON_DRIVER",
                semantic_origin="calculated",
                inference_rule="driver_creation",
                props={},
            )
            self.graph.add_edge(
                driver_id,
                cq_id,
                "hasQuantity",
                semantic_origin="calculated",
                inference_rule="driver_quantity_link",
                props={},
            )
            self.graph.add_edge(
                emission_id,
                factor_node_id,
                "USES_EMISSION_FACTOR",
                semantic_origin="calculated",
                inference_rule="factor_binding",
                props={},
            ) if factor_node_id else None
            self.graph.add_edge(
                emission_id,
                calc_id,
                "CALCULATED_USING_FORMULA",
                semantic_origin="calculated",
                inference_rule="calculation_record_creation",
                props={},
            )
            self.graph.add_edge(
                calc_id,
                cq_id,
                "CALCULATED_FROM_QUANTITY",
                semantic_origin="calculated",
                inference_rule="quantity_provenance",
                props={},
            )
            self.graph.add_edge(
                calc_id,
                factor_node_id,
                "USES_EMISSION_FACTOR",
                semantic_origin="calculated",
                inference_rule="factor_provenance",
                props={},
            ) if factor_node_id else None

            # Cross-layer links for path querying
            self.graph.add_edge(
                component_id,
                emission_id,
                "PRODUCES_EMISSION",
                semantic_origin="calculated",
                inference_rule="component_material_emission",
                props={"emissionScope": "material_embodied"},
            )
            if material_node_id:
                self.graph.add_edge(
                    driver_id,
                    material_node_id,
                    "CONSUMES_MATERIAL",
                    semantic_origin="calculated",
                    inference_rule="driver_material_binding",
                    props={},
                )

            source_quantity_name = str(selection.get("input_quantity_name") or "")
            if source_quantity_name and source_quantity_name in quantity_node_map:
                self.graph.add_edge(
                    cq_id,
                    quantity_node_map[source_quantity_name],
                    "DERIVED_FROM_IFC_QUANTITY",
                    semantic_origin="calculated",
                    inference_rule="quantity_lineage",
                    props={},
                )

            issue_id = ""
            if calculation_status == "incomplete":
                issue_id = f"calculation_issue:{element.global_id}:{idx}"
                self.graph.add_node(
                    issue_id,
                    ["CalculationIssue"],
                    domain="Calculation",
                    semantic_origin="calculated",
                    reasoning_role="incomplete_calculation_issue",
                    props={
                        "ifcGlobalId": element.global_id,
                        "materialText": material.name,
                        "issue": selection.get("issue", "Unknown incomplete calculation reason"),
                        "missingInfo": selection.get("issue", ""),
                    },
                )
                self.graph.add_edge(
                    emission_id,
                    issue_id,
                    "HAS_CALCULATION_ISSUE",
                    semantic_origin="calculated",
                    inference_rule="incomplete_calculation_detection",
                    props={},
                )

            first_stage = production_context.get("stage_ids", [""])
            first_activity = production_context.get("activity_ids", [""])
            path_sample = (
                f"{element_node_id}"
                f" -> {component_id}"
                f" -> {production_context.get('template_application_id', '')}"
                f" -> {first_stage[0] if first_stage else ''}"
                f" -> {first_activity[0] if first_activity else ''}"
                f" -> {emission_id}"
                f" -> {calc_id}"
            )
            results.append(
                {
                    "material_name": material.name,
                    "material_node_id": material_node_id,
                    "emission_id": emission_id,
                    "calculation_record_id": calc_id,
                    "calculation_issue_id": issue_id,
                    "calculation_status": calculation_status,
                    "emission_value": emission_value,
                    "quantity_basis": selection.get("quantity_basis", ""),
                    "reasoning_path_available": calculation_status == "complete" and bool(source_quantity_name in quantity_node_map),
                    "reasoning_path_sample": path_sample,
                }
            )
        return results

    def reason_process_carbon_for_activity(
        self,
        activity_id: str,
        activity_template_uri: str,
        stage_id: str,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
    ) -> Optional[Dict[str, Any]]:
        activity_node = self.graph.nodes.get(activity_id, {})
        stage_node = self.graph.nodes.get(stage_id, {})
        stage_order = int(safe_float(stage_node.get("props", {}).get("stageOrder", 0), 0.0))
        activity_seq = int(safe_float(activity_node.get("props", {}).get("activitySequence", 0), 0.0))
        activity_name = str(activity_node.get("props", {}).get("activityName", "") or activity_id)

        profile = self.ontology.match_activity_energy(activity_template_uri)
        if not profile.get("found"):
            emission_id = f"process_emission:{element.global_id}:{stage_order}:{activity_seq}"
            issue_id = f"calculation_issue:process:{element.global_id}:{stage_order}:{activity_seq}"
            self.graph.add_node(
                emission_id,
                ["CarbonEmission"],
                domain="Carbon",
                semantic_origin="calculated",
                reasoning_role="process_carbon_emission_result",
                props={
                    "ifcGlobalId": element.global_id,
                    "activityId": activity_id,
                    "activityName": activity_name,
                    "value": None,
                    "unit": "kgCO2e",
                    "emissionScope": "process_carbon",
                    "lifeCycleStage": "A3-process",
                    "calculationStatus": "incomplete",
                    "formulaText": "",
                },
            )
            self.graph.add_node(
                issue_id,
                ["CalculationIssue"],
                domain="Calculation",
                semantic_origin="calculated",
                reasoning_role="incomplete_calculation_issue",
                props={
                    "ifcGlobalId": element.global_id,
                    "activityId": activity_id,
                    "activityName": activity_name,
                    "issue": f"No energy consumption data for activity {activity_name}",
                    "missingInfo": f"onto:ActivityEnergyProfile for {activity_template_uri}",
                },
            )
            self.graph.add_edge(activity_id, emission_id, "PRODUCES_EMISSION", "calculated", "activity_process_carbon_emission", {"emissionScope": "process_carbon"})
            self.graph.add_edge(emission_id, element_node_id, "EMISSION_OF", "calculated", "process_emission_trace_to_element", {})
            self.graph.add_edge(emission_id, issue_id, "HAS_CALCULATION_ISSUE", "calculated", "no_activity_energy_profile", {})
            return {
                "activity_id": activity_id,
                "stage_id": stage_id,
                "emission_id": emission_id,
                "calculation_status": "incomplete",
                "emission_value": None,
                "issue": f"No energy consumption data for activity {activity_name}",
                "energy_profile_found": False,
                "reasoning_path_sample": f"{element_node_id} -> {component_id} -> {stage_id} -> {activity_id} -> {emission_id}",
            }

        energy_type = str(profile.get("energyType", "") or "")
        ef_match = self.ontology.match_energy_emission_factor(energy_type)
        ef_no_match = bool(ef_match.get("no_match")) or ef_match.get("factor_value") is None

        base_id = f"{element.global_id}:{stage_order}:{activity_seq}"
        driver_id = f"process_driver:{base_id}"
        cq_id = f"process_quantity:{base_id}"
        emission_id = f"process_emission:{base_id}"
        calc_id = f"process_calc_record:{base_id}"
        issue_id = f"calculation_issue:process:{base_id}"

        consumption_rate = safe_float(profile.get("consumptionRate"), 0.0)
        factor_value = safe_float(ef_match.get("factor_value"), 0.0) if ef_match.get("factor_value") is not None else None
        calculation_status = "complete"
        emission_value = None
        issue_text = ""

        if ef_no_match:
            calculation_status = "incomplete"
            issue_text = f"No energy emission factor found in ontology for energy type '{energy_type}'"
        elif factor_value is None:
            calculation_status = "incomplete"
            issue_text = f"Energy emission factor value missing for energy type '{energy_type}'"
        else:
            emission_value = round(consumption_rate * factor_value, 6)

        # Zero-value check: do not silently accept zero emissions
        if calculation_status == "complete":
            if factor_value == 0.0:
                calculation_status = "incomplete"
                emission_value = None
                issue_text = (
                    f"Emission factor value is 0.0 for energy type '{energy_type}' "
                    f"- likely missing or placeholder data in ontology"
                )
            elif consumption_rate == 0.0:
                calculation_status = "incomplete"
                emission_value = None
                issue_text = (
                    f"Computed process consumption quantity is 0.0 for activity '{activity_name}' "
                    f"- ontology profile value may be missing or zero"
                )

        driver_labels = ["ConsumptionDriver"]
        if normalize_text(energy_type) == "electricity":
            driver_labels.append("ElectricityConsumption")
        elif normalize_text(energy_type) in {"diesel", "gas", "lpg", "naturalgas"}:
            driver_labels.append("FuelConsumption")
        else:
            driver_labels.append("FuelConsumption")

        self.graph.add_node(
            driver_id,
            driver_labels,
            domain="Carbon",
            semantic_origin="calculated",
            reasoning_role="process_energy_driver",
            props={
                "ifcGlobalId": element.global_id,
                "activityId": activity_id,
                "activityName": activity_name,
                "energyType": energy_type,
                "consumptionRate": consumption_rate,
                "consumptionUnit": profile.get("consumptionUnit", ""),
                "driverType": "process_energy_carbon",
                "calculationStatus": calculation_status,
                "dataSource": profile.get("dataSource", ""),
            },
        )
        self.graph.add_node(
            cq_id,
            ["ConsumptionQuantity"],
            domain="Carbon",
            semantic_origin="calculated",
            reasoning_role="process_energy_quantity",
            props={
                "ifcGlobalId": element.global_id,
                "activityId": activity_id,
                "inputQuantityName": f"{energy_type}_consumption_rate",
                "inputQuantityValue": consumption_rate,
                "inputQuantityUnit": profile.get("consumptionUnit", ""),
                "quantityBasis": profile.get("consumptionBasis", ""),
            },
        )
        self.graph.add_node(
            emission_id,
            ["CarbonEmission"],
            domain="Carbon",
            semantic_origin="calculated",
            reasoning_role="process_carbon_emission_result",
            props={
                "ifcGlobalId": element.global_id,
                "activityId": activity_id,
                "activityName": activity_name,
                "value": emission_value,
                "unit": "kgCO2e",
                "emissionScope": "process_carbon",
                "lifeCycleStage": "A3-process",
                "calculationStatus": calculation_status,
                "formulaText": f"Emission = ConsumptionRate({profile.get('consumptionUnit', '')}) × EmissionFactor({ef_match.get('factor_unit', '')})",
            },
        )
        self.graph.add_node(
            calc_id,
            ["CalculationRecord"],
            domain="Calculation",
            semantic_origin="calculated",
            reasoning_role="process_calculation_provenance",
            props={
                "ifcGlobalId": element.global_id,
                "activityId": activity_id,
                "formulaText": f"Emission = ConsumptionRate({profile.get('consumptionUnit', '')}) × EmissionFactor({ef_match.get('factor_unit', '')})",
                "inputQuantityName": f"{energy_type}_consumption_rate",
                "inputQuantityValue": consumption_rate,
                "inputQuantityUnit": profile.get("consumptionUnit", ""),
                "emissionFactorValue": factor_value,
                "emissionFactorUnit": ef_match.get("factor_unit", ""),
                "calculatedValue": emission_value,
                "calculationStatus": calculation_status,
            },
        )

        ef_node_id = ""
        if not ef_no_match and factor_value is not None:
            ef_node_id = self._emission_factor_node(ef_match, key_prefix="energy_emission_factor")

        self.graph.add_edge(activity_id, emission_id, "PRODUCES_EMISSION", "calculated", "activity_process_carbon_emission", {"emissionScope": "process_carbon"})
        if stage_id:
            self.graph.add_edge(stage_id, driver_id, "HAS_CARBON_DRIVER", "ontology_inferred", "stage_to_process_driver", {})
        self.graph.add_edge(activity_id, driver_id, "RELATED_TO_DRIVER", "ontology_inferred", "activity_to_process_driver", {})
        self.graph.add_edge(emission_id, driver_id, "HAS_CARBON_DRIVER", "calculated", "process_driver_creation", {})
        self.graph.add_edge(driver_id, cq_id, "hasQuantity", "calculated", "process_driver_quantity_link", {})
        self.graph.add_edge(emission_id, calc_id, "CALCULATED_USING_FORMULA", "calculated", "process_calculation_record_creation", {})
        self.graph.add_edge(calc_id, cq_id, "CALCULATED_FROM_QUANTITY", "calculated", "process_quantity_provenance", {})
        self.graph.add_edge(emission_id, element_node_id, "EMISSION_OF", "calculated", "process_emission_trace_to_element", {})

        if ef_node_id:
            self.graph.add_edge(emission_id, ef_node_id, "USES_EMISSION_FACTOR", "calculated", "process_factor_binding", {})
            self.graph.add_edge(calc_id, ef_node_id, "USES_EMISSION_FACTOR", "calculated", "process_factor_provenance", {})

        if calculation_status != "complete":
            self.graph.add_node(
                issue_id,
                ["CalculationIssue"],
                domain="Calculation",
                semantic_origin="calculated",
                reasoning_role="incomplete_calculation_issue",
                props={
                    "ifcGlobalId": element.global_id,
                    "activityId": activity_id,
                    "activityName": activity_name,
                    "issue": issue_text or f"Incomplete process carbon calculation for activity {activity_name}",
                    "missingInfo": issue_text,
                },
            )
            self.graph.add_edge(emission_id, issue_id, "HAS_CALCULATION_ISSUE", "calculated", "incomplete_process_calculation_detection", {})

        return {
            "activity_id": activity_id,
            "stage_id": stage_id,
            "emission_id": emission_id,
            "calculation_status": calculation_status,
            "emission_value": emission_value,
            "issue": issue_text,
            "energy_profile_found": True,
            "reasoning_path_sample": f"{element_node_id} -> {component_id} -> {stage_id} -> {activity_id} -> {emission_id} -> {calc_id}",
        }


class MappingReporter:
    def __init__(
        self,
        graph: DM2CGraph,
        per_element_rows: List[Dict[str, Any]],
        checks: Dict[str, Any],
    ):
        self.graph = graph
        self.per_element_rows = per_element_rows
        self.checks = checks

    def build(self) -> Dict[str, Any]:
        template_counter = Counter()
        mapping_examples: List[Dict[str, Any]] = []
        path_examples: List[str] = []
        total_material_carbon = 0.0
        total_process_carbon = 0.0
        total_activities_with_energy = 0
        total_activities_without_energy = 0

        for row in self.per_element_rows:
            selected = row.get("selectedTemplate", "")
            if selected:
                template_counter[selected] += 1
            total_material_carbon += safe_float(row.get("materialCarbonValue"), 0.0)
            total_process_carbon += safe_float(row.get("processCarbonValue"), 0.0)
            total_activities_with_energy += int(safe_float(row.get("activitiesWithEnergyData"), 0.0))
            total_activities_without_energy += int(safe_float(row.get("activitiesWithoutEnergyData"), 0.0))
            if len(mapping_examples) < 8:
                mapping_examples.append(
                    {
                        "globalId": row.get("globalId", ""),
                        "ifcType": row.get("ifcType", ""),
                        "materialText": row.get("materialText", ""),
                        "selectedTemplate": row.get("selectedTemplate", ""),
                        "matchLevel": row.get("matchLevel", ""),
                        "confidence": row.get("confidence", 0.0),
                    }
                )
            for sample in row.get("reasoningPathSamples", []):
                if sample and len(path_examples) < 8:
                    path_examples.append(sample)

        explicit_material_count = 0
        inferred_material_count = 0
        for node in self.graph.node_by_label("IfcMaterial"):
            if node.get("props", {}).get("fallbackMaterial"):
                inferred_material_count += 1
            else:
                explicit_material_count += 1

        complete_ce = 0
        incomplete_ce = 0
        material_ce_nodes = 0
        process_ce_nodes = 0
        for node in self.graph.node_by_label("CarbonEmission"):
            scope = str(node.get("props", {}).get("emissionScope", ""))
            if scope == "material_embodied":
                material_ce_nodes += 1
            elif scope == "process_carbon":
                process_ce_nodes += 1
            if str(node.get("props", {}).get("calculationStatus", "")).lower() == "complete":
                complete_ce += 1
            else:
                incomplete_ce += 1

        report = {
            "summary": {
                "ifcElementsProcessed": len(self.per_element_rows),
                "elementsAlignedToBuildingComponent": len(self.graph.node_by_label("BuildingComponent")),
                "explicitMaterialsFound": explicit_material_count,
                "inferredOrFallbackMaterials": inferred_material_count,
                "productionTemplatesApplied": sum(template_counter.values()),
                "templateApplicationCounts": dict(template_counter),
                "successfulCarbonCalculations": complete_ce,
                "incompleteCarbonCalculations": incomplete_ce,
                "materialCarbonEmissionNodes": material_ce_nodes,
                "processCarbonEmissionNodes": process_ce_nodes,
                "totalMaterialCarbon_kgCO2e": round(total_material_carbon, 6),
                "totalProcessCarbon_kgCO2e": round(total_process_carbon, 6),
                "totalCarbon_kgCO2e": round(total_material_carbon + total_process_carbon, 6),
                "processDataCoverage": f"{total_activities_with_energy} of {total_activities_with_energy + total_activities_without_energy} activities have energy profiles",
            },
            "examplesOfMappingEvidence": mapping_examples,
            "examplesOfFullReasoningPaths": path_examples,
            "perElement": self.per_element_rows,
            "minimalChecks": self.checks,
        }
        return report

class GraphExporter:
    def __init__(self, graph: DM2CGraph, source_ifc: Path, source_ontology: Path, graph_profile: str = "legacy_full"):
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
            "// DM2C ontology-guided IFC-to-graph",
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
            "MaterialCandidate",
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
            "PropertyEvidence",
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
        queries = """// 1) Trace full DM2C reasoning path for one component
MATCH (e:BIM)-[:ALIGNED_AS]->(bc:BuildingComponent:Production)
MATCH (bc)-[:HAS_TEMPLATE_APPLICATION]->(ta:TemplateApplication:Mapping)-[:APPLIES_TEMPLATE]->(pt:ProductionTemplate:Production)
OPTIONAL MATCH (bc)-[:REQUIRES_STAGE]->(st:ProductionStage:Production)-[:HAS_PROCESS]->(act:ManufacturingActivity:Production)-[:REQUIRES_RESOURCE]->(res:Resource:Production)
OPTIONAL MATCH (bc)-[:PRODUCES_EMISSION]->(ce:CarbonEmission:Carbon)-[:CALCULATED_USING_FORMULA]->(cr:CalculationRecord:Calculation)
RETURN e.id AS ifcElementId, bc.id AS componentId, ta.selectedTemplateLabel AS template, st.stageName AS stage, act.activityName AS activity, res.name AS resource, ce.value AS emissionKgCO2e, cr.formulaText AS formula
LIMIT 25;

// 2) List all production stages required by each IfcBeam
MATCH (b:IfcBeam:BIM)-[:ALIGNED_AS]->(bc:BuildingComponent)-[:REQUIRES_STAGE]->(st:ProductionStage)
RETURN b.id AS ifcBeamId, b.name AS beamName, collect(DISTINCT st.stageName) AS requiredStages
ORDER BY b.id;

// 3) List all manufacturing activities inferred for steel components
MATCH (bc:BuildingComponent)-[:HAS_MAPPING_EVIDENCE]->(ev:MappingEvidence)
WHERE ev.materialText CONTAINS '钢' OR ev.materialText =~ '(?i).*steel.*'
MATCH (bc)-[:REQUIRES_STAGE]->(st:ProductionStage)-[:HAS_PROCESS]->(act:ManufacturingActivity)
RETURN bc.id AS componentId, ev.materialText AS materialText, st.stageName AS stage, act.activityName AS activity
ORDER BY componentId, stage, act.activitySequence;

// 4) Show ontology template applied to each IFC element and why
MATCH (e:BIM)-[:ALIGNED_AS]->(bc:BuildingComponent)-[:HAS_TEMPLATE_APPLICATION]->(ta:TemplateApplication)-[:APPLIES_TEMPLATE]->(pt:ProductionTemplate)
OPTIONAL MATCH (ta)-[:MATCHED_BY_RULE]->(ev:MappingEvidence)
RETURN e.id AS ifcElementId, e.ifcType AS ifcType, pt.templateLabel AS templateLabel, ta.matchLevel AS matchLevel, ta.confidence AS confidence, ev.reason AS matchReason, ev.matchedKeywords AS matchedKeywords
ORDER BY ifcElementId;

// 5) Aggregate carbon emissions by IFC element type
MATCH (ce:CarbonEmission)-[:EMISSION_OF]->(e:BIM)
WHERE ce.calculationStatus = 'complete'
WITH e, ce, [l IN labels(e) WHERE l STARTS WITH 'Ifc'] AS ifcLabels
RETURN coalesce(head(ifcLabels), 'IfcElement') AS ifcType, round(sum(ce.value), 6) AS totalEmissionKgCO2e, count(ce) AS emissionCount
ORDER BY totalEmissionKgCO2e DESC;

// 6) Aggregate carbon emissions by material
MATCH (ce:CarbonEmission)-[:HAS_CARBON_DRIVER]->(cd:ConsumptionDriver)-[:CONSUMES_MATERIAL]->(m:IfcMaterial)
WHERE ce.calculationStatus = 'complete'
RETURN m.name AS material, round(sum(ce.value), 6) AS totalEmissionKgCO2e, count(ce) AS emissionCount
ORDER BY totalEmissionKgCO2e DESC;

// 7) Aggregate process carbon emissions by production stage
MATCH (st:ProductionStage)-[:HAS_PROCESS]->(act:ManufacturingActivity)-[:PRODUCES_EMISSION]->(ce:CarbonEmission)
WHERE ce.emissionScope = 'process_carbon' AND ce.calculationStatus = 'complete'
RETURN st.stageName AS stage, round(sum(ce.value), 6) AS processEmissionKgCO2e, count(DISTINCT act) AS activityCount
ORDER BY processEmissionKgCO2e DESC;

// 8) Show full calculation provenance for one CarbonEmission
MATCH (ce:CarbonEmission)-[:CALCULATED_USING_FORMULA]->(cr:CalculationRecord)
MATCH (cr)-[:CALCULATED_FROM_QUANTITY]->(cq:ConsumptionQuantity)
OPTIONAL MATCH (cq)-[:DERIVED_FROM_IFC_QUANTITY]->(iq:IfcElementQuantity)
MATCH (cr)-[:USES_EMISSION_FACTOR]->(ef:EmissionFactor)
RETURN ce.id AS carbonEmissionId, ce.value AS emissionKgCO2e, cr.formulaText AS formula, cq.inputQuantityName AS inputQuantityName, cq.inputQuantityValue AS inputQuantityValue, cq.inputQuantityUnit AS inputQuantityUnit, iq.quantityName AS ifcQuantityName, ef.factorValue AS factorValue, ef.factorUnit AS factorUnit
LIMIT 20;

// 9) List elements using fallback or inferred material
MATCH (e:BIM)-[:hasMaterial]->(m:IfcMaterial)
WHERE m.fallbackMaterial = true OR m.materialSource = 'inferred_from_ifc_text'
RETURN e.id AS ifcElementId, e.ifcType AS ifcType, m.name AS material, m.materialSource AS materialSource, m.confidence AS confidence
ORDER BY ifcElementId;

// 10) List incomplete carbon calculations and missing information
MATCH (ce:CarbonEmission {calculationStatus:'incomplete'})-[:HAS_CALCULATION_ISSUE]->(ci:CalculationIssue)
OPTIONAL MATCH (ce)-[:EMISSION_OF]->(e:BIM)
RETURN ce.id AS carbonEmissionId, e.id AS ifcElementId, ce.materialText AS material, ci.issue AS issue, ci.missingInfo AS missingInfo
ORDER BY carbonEmissionId;

// 11) Show process carbon emissions by manufacturing activity
MATCH (act:ManufacturingActivity)-[:PRODUCES_EMISSION]->(ce:CarbonEmission)
WHERE ce.emissionScope = 'process_carbon' AND ce.calculationStatus = 'complete'
RETURN act.activityName AS activity, ce.value AS processEmissionKgCO2e, ce.formulaText AS formula
ORDER BY processEmissionKgCO2e DESC;

// 12) Compare material carbon vs process carbon for each component
MATCH (bc:BuildingComponent)
OPTIONAL MATCH (bc)-[:PRODUCES_EMISSION]->(mat_ce:CarbonEmission {emissionScope: 'material_embodied', calculationStatus: 'complete'})
OPTIONAL MATCH (bc)-[:REQUIRES_STAGE]->(:ProductionStage)-[:HAS_PROCESS]->(act:ManufacturingActivity)-[:PRODUCES_EMISSION]->(proc_ce:CarbonEmission {emissionScope: 'process_carbon', calculationStatus: 'complete'})
WITH bc, coalesce(sum(DISTINCT mat_ce.value), 0.0) AS materialCarbon, coalesce(sum(DISTINCT proc_ce.value), 0.0) AS processCarbon
RETURN bc.id AS componentId, bc.name AS componentName,
       round(materialCarbon, 4) AS C_mat_kgCO2e,
       round(processCarbon, 4) AS C_proc_kgCO2e,
       round(materialCarbon + processCarbon, 4) AS C_total_kgCO2e
ORDER BY C_total_kgCO2e DESC;

// 13) Total module carbon = C_mat + C_proc (Formula 1)
MATCH (ce:CarbonEmission {calculationStatus: 'complete'})
WITH ce.emissionScope AS scope, sum(ce.value) AS total
RETURN scope, round(total, 4) AS totalKgCO2e
UNION ALL
MATCH (ce:CarbonEmission {calculationStatus: 'complete'})
RETURN 'C_MM_total' AS scope, round(sum(ce.value), 4) AS totalKgCO2e;

// 14) Trace full process carbon path for one activity
MATCH (act:ManufacturingActivity)-[:PRODUCES_EMISSION]->(ce:CarbonEmission {emissionScope: 'process_carbon'})
MATCH (ce)-[:CALCULATED_USING_FORMULA]->(cr:CalculationRecord)
MATCH (cr)-[:CALCULATED_FROM_QUANTITY]->(cq:ConsumptionQuantity)
MATCH (cr)-[:USES_EMISSION_FACTOR]->(ef:EmissionFactor)
MATCH (act)-[:RELATED_TO_DRIVER]->(cd:ConsumptionDriver)
RETURN act.activityName AS activity, cd.energyType AS energyType,
       cq.inputQuantityValue AS consumptionRate, cq.inputQuantityUnit AS consumptionUnit,
       ef.factorValue AS efValue, ef.factorUnit AS efUnit,
       ce.value AS emissionKgCO2e, cr.formulaText AS formula
LIMIT 20;
"""
        path.write_text(queries, encoding="utf-8")


class BackboneMappingReporter:
    def __init__(self, graph: DM2CGraph, per_element_rows: List[Dict[str, Any]], checks: Dict[str, Any]):
        self.graph = graph
        self.per_element_rows = per_element_rows
        self.checks = checks

    def build(self) -> Dict[str, Any]:
        explicit_materials = 0
        inferred_material_candidates = 0
        for node in self.graph.node_by_label("MaterialCandidate"):
            source = str(node.get("props", {}).get("materialSource", ""))
            if source == "explicit_ifc":
                explicit_materials += 1
            else:
                inferred_material_candidates += 1

        return {
            "summary": {
                "ifcElementsProcessed": len(self.per_element_rows),
                "buildingComponentsCreated": len(self.graph.node_by_label("BuildingComponent")),
                "explicitMaterials": explicit_materials,
                "inferredMaterialCandidates": inferred_material_candidates,
                "quantityBasisNodes": len(self.graph.node_by_label("QuantityBasis")),
                "processAssessmentTargets": len(self.graph.node_by_label("ProcessAssessmentTarget")),
                "carbonAssessmentTargets": len(self.graph.node_by_label("CarbonAssessmentTarget")),
                "dataRequirements": len(self.graph.node_by_label("DataRequirement")),
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


class AgentInterfaceManifestBuilder:
    @staticmethod
    def build() -> Dict[str, Any]:
        return {
            "manifestType": "Section3.3_agent_interface_contract",
            "graphGroundingContract": {
                "componentGrounding": {
                    "nodeLabel": "BuildingComponent",
                    "primaryId": "ifcGlobalId",
                    "evidenceLinks": [
                        "HAS_MAPPING_EVIDENCE",
                        "HAS_MATERIAL_CANDIDATE",
                        "HAS_QUANTITY_BASIS",
                    ],
                }
            },
            "toolInputSchemas": {
                "manufacturing_process_retrieval_tool": {
                    "inputs": [
                        "componentGlobalId",
                        "ifcType",
                        "componentName",
                        "materialCandidateText",
                        "reference",
                        "objectType",
                        "typeObjectName",
                        "queryHints",
                    ]
                },
                "carbon_factor_excel_lookup_tool": {
                    "inputs": [
                        "materialCandidateText",
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
                        "density",
                        "processEnergyProfile",
                    ]
                },
            },
            "runtimeReasoningSequence": [
                "User question",
                "Retrieve BuildingComponent from backbone graph",
                "Retrieve MaterialCandidate and QuantityBasis",
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
MATCH (e:BIM)-[:ALIGNED_AS]->(bc:BuildingComponent:Interface)
MATCH (bc)-[:HAS_MAPPING_EVIDENCE]->(ev:MappingEvidence:Mapping)
OPTIONAL MATCH (bc)-[:HAS_MATERIAL_CANDIDATE]->(mc:MaterialCandidate:Interface)
OPTIONAL MATCH (bc)-[:HAS_QUANTITY_BASIS]->(qb:QuantityBasis:Interface)
OPTIONAL MATCH (bc)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget:Interface)
OPTIONAL MATCH (bc)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget:Interface)
RETURN e.id AS ifcElementId, bc.id AS buildingComponentId, ev.id AS mappingEvidenceId, mc.materialName AS materialCandidate, qb.quantityReadiness AS quantityReadiness, pt.id AS processTargetId, ct.id AS carbonTargetId
LIMIT 25;

// 2) List all components that require manufacturing process retrieval
MATCH (bc:BuildingComponent)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget)-[:REQUIRES_DATA]->(dr:DataRequirement)
WHERE dr.requiredDataType = 'manufacturing_process_description'
RETURN bc.id AS componentId, bc.ifcGlobalId AS globalId, pt.queryHints AS queryHints, dr.expectedSource AS expectedSource
ORDER BY componentId;

// 3) List all carbon assessment targets and their factor lookup keys
MATCH (bc:BuildingComponent)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget)
RETURN bc.id AS componentId, ct.id AS carbonTargetId, ct.materialCandidateText AS materialCandidate, ct.factorLookupKeys AS factorLookupKeys
ORDER BY componentId, carbonTargetId;

// 4) List all inferred material candidates and their evidence fields
MATCH (mc:MaterialCandidate)
WHERE mc.materialSource = 'inferred_from_ifc_text' OR mc.fallbackMaterial = true
RETURN mc.id AS materialCandidateId, mc.materialName AS materialName, mc.evidenceFields AS evidenceFields, mc.matchedKeywords AS matchedKeywords
ORDER BY materialCandidateId;

// 5) List all components with missing explicit material
MATCH (bc:BuildingComponent)-[:HAS_MATERIAL_CANDIDATE]->(mc:MaterialCandidate)
WHERE mc.materialSource = 'inferred_from_ifc_text' OR mc.fallbackMaterial = true
RETURN DISTINCT bc.id AS componentId, bc.ifcGlobalId AS globalId, mc.materialName AS inferredMaterial
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

// 8) Group components by IFC type and material candidate for batched retrieval
MATCH (bc:BuildingComponent)-[:HAS_MATERIAL_CANDIDATE]->(mc:MaterialCandidate)
RETURN bc.ifcType AS ifcType, mc.materialName AS materialCandidate, count(DISTINCT bc) AS componentCount, collect(DISTINCT bc.id)[0..20] AS sampleComponents
ORDER BY componentCount DESC;

// 9) Retrieve all graph-grounding evidence for one component GlobalId
MATCH (bc:BuildingComponent {ifcGlobalId: $globalId})
OPTIONAL MATCH (bc)-[:HAS_MAPPING_EVIDENCE]->(ev:MappingEvidence)
OPTIONAL MATCH (bc)-[:HAS_MATERIAL_CANDIDATE]->(mc:MaterialCandidate)
OPTIONAL MATCH (bc)-[:HAS_QUANTITY_BASIS]->(qb:QuantityBasis)
OPTIONAL MATCH (bc)-[:HAS_PROCESS_ASSESSMENT_TARGET]->(pt:ProcessAssessmentTarget)
OPTIONAL MATCH (bc)-[:HAS_CARBON_ASSESSMENT_TARGET]->(ct:CarbonAssessmentTarget)
RETURN bc.id AS componentId, ev, collect(DISTINCT mc) AS materialCandidates, qb, pt, collect(DISTINCT ct) AS carbonTargets;

// 10) List all DataRequirement nodes and the assessment targets that require them
MATCH (dr:DataRequirement)<-[:REQUIRES_DATA]-(t)
RETURN dr.id AS requirementId, dr.requiredDataType AS requiredDataType, dr.expectedSource AS expectedSource, labels(t) AS requiredByLabels, t.id AS requiredByTarget
ORDER BY requirementId, requiredByTarget;
"""
        path.write_text(queries, encoding="utf-8")


class BackboneGraphBuilder:
    def __init__(self, ifc_path: Path, ontology_path: Path):
        self.ifc_path = ifc_path
        self.ontology_path = ontology_path
        self.graph = DM2CGraph()
        self.ontology = OntologySchemaReader(ontology_path)
        self.extractor = IFCExtractor(ifc_path, self.ontology)
        self.per_element_rows: List[Dict[str, Any]] = []
        self._element_node_ids: List[str] = []
        self._component_ids: List[str] = []

    @staticmethod
    def _element_labels(ifc_type: str) -> List[str]:
        if ifc_type == "IfcWallStandardCase":
            return ["IfcWall", "IfcWallStandardCase"]
        return [ifc_type]

    def _add_spatial_layer(self, spatial: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
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
        return module_id, storey_map

    def _add_property_evidence_nodes(self, element: ExtractedElement, element_id: str) -> None:
        key_props = {
            "Reference": element.properties.get("Reference", ""),
            "LoadBearing": element.properties.get("LoadBearing", ""),
            "IsExternal": element.properties.get("IsExternal", ""),
            "FireRating": element.properties.get("FireRating", ""),
            "ObjectType": element.object_type,
            "PredefinedType": element.predefined_type,
            "Tag": element.tag,
            "typeObjectName": element.type_object_name,
        }
        for key, value in key_props.items():
            if str(value or "").strip() == "":
                continue
            node_id = f"property_evidence:{element.global_id}:{sanitize_key(key)}"
            self.graph.add_node(
                node_id,
                ["PropertyEvidence"],
                domain="Mapping",
                semantic_origin="explicit_ifc",
                reasoning_role="ifc_property_evidence",
                props={
                    "ifcGlobalId": element.global_id,
                    "propertyName": key,
                    "propertyValue": str(value),
                },
            )
            self.graph.add_edge(
                element_id,
                node_id,
                "HAS_PROPERTY_EVIDENCE",
                semantic_origin="explicit_ifc",
                inference_rule="ifc_property_extraction",
                props={},
            )

    def _add_element_bim_nodes(
        self,
        element: ExtractedElement,
        module_id: str,
        storey_node_map: Dict[str, str],
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
        for q_name, q_value in element.quantities.items():
            q_id = f"ifc_quantity:{element.global_id}:{slugify(q_name, 80)}"
            q_unit = element.quantity_units.get(q_name, "")
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
            quantity_node_map[q_name] = q_id

        material_bindings: List[Tuple[int, ExtractedMaterial, str]] = []
        for idx, mat in enumerate(element.materials, start=1):
            mat_id = f"ifc_material:{element.global_id}:{idx}:{slugify(mat.name, 50)}"
            sem_origin = "explicit_ifc" if mat.explicit else "inferred_from_ifc_text"
            self.graph.add_node(
                mat_id,
                ["IfcMaterial"],
                domain="BIM",
                semantic_origin=sem_origin,
                reasoning_role="material_assignment",
                props={
                    "ifcGlobalId": element.global_id,
                    "name": mat.name,
                    "materialSource": mat.source,
                    "fallbackMaterial": mat.fallback_material,
                    "confidence": mat.confidence,
                    "evidenceFields": list(mat.evidence_fields),
                },
            )
            self.graph.add_edge(
                element_id,
                mat_id,
                "hasMaterial",
                semantic_origin=sem_origin,
                inference_rule="ifc_material_extraction" if mat.explicit else "material_inference_from_ifc_text",
                props={},
            )

            if mat.layer_order is not None or mat.thickness is not None:
                layer_id = f"material_layer:{element.global_id}:{idx}"
                self.graph.add_node(
                    layer_id,
                    ["MaterialLayer"],
                    domain="BIM",
                    semantic_origin="explicit_ifc",
                    reasoning_role="material_layer_detail",
                    props={
                        "ifcGlobalId": element.global_id,
                        "materialName": mat.name,
                        "layerThickness": mat.thickness,
                        "layerOrder": mat.layer_order,
                        "materialSource": mat.source,
                        "evidence": "explicit_ifc_material_layer",
                    },
                )
                self.graph.add_edge(
                    mat_id,
                    layer_id,
                    "HAS_MATERIAL_LAYER",
                    semantic_origin="explicit_ifc",
                    inference_rule="ifc_material_layer_extraction",
                    props={},
                )
            material_bindings.append((idx, mat, mat_id))

        self._add_property_evidence_nodes(element, element_id)
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
    ) -> Tuple[str, str]:
        component_id = f"bc:{element.global_id}"
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

        evidence_id = f"mapping_evidence:{element.global_id}"
        material_text = " | ".join([m.name for _, m, _ in material_bindings if m.name])
        self.graph.add_node(
            evidence_id,
            ["MappingEvidence"],
            domain="Mapping",
            semantic_origin="ontology_aligned",
            reasoning_role="interface_alignment_evidence",
            props={
                "ifcGlobalId": element.global_id,
                "ifcType": element.ifc_type,
                "ifcTypeNormalized": element.ifc_type.replace("StandardCase", ""),
                "reference": element.properties.get("Reference", ""),
                "objectType": element.object_type,
                "typeObjectName": element.type_object_name,
                "materialText": material_text,
                "ruleName": "ifc_to_interface_backbone_alignment",
                "reason": "BuildingComponent and interface anchors are grounded from IFC design evidence.",
            },
        )
        self.graph.add_edge(
            component_id,
            evidence_id,
            "HAS_MAPPING_EVIDENCE",
            semantic_origin="ontology_aligned",
            inference_rule="mapping_evidence_capture",
            props={},
        )
        return component_id, evidence_id

    @staticmethod
    def _split_text_tokens(text: str) -> List[str]:
        tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", str(text or ""))
        cleaned = [t.strip() for t in tokens if t.strip()]
        return cleaned[:20]

    def _create_material_candidates(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        material_bindings: List[Tuple[int, ExtractedMaterial, str]],
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for idx, mat, mat_id in material_bindings:
            source = "explicit_ifc" if mat.explicit else "inferred_from_ifc_text"
            evidence_text = " ".join(
                [
                    mat.name,
                    element.name,
                    str(element.properties.get("Reference", "") or ""),
                    element.object_type,
                    element.type_object_name,
                ]
            )
            matched_keywords = self._material_keyword_matches(evidence_text)
            candidate_id = f"material_candidate:{element.global_id}:{idx}"
            self.graph.add_node(
                candidate_id,
                ["MaterialCandidate"],
                domain="Interface",
                semantic_origin=source,
                reasoning_role="agent_material_lookup_anchor",
                props={
                    "ifcGlobalId": element.global_id,
                    "materialName": mat.name,
                    "materialSource": source,
                    "fallbackMaterial": mat.fallback_material,
                    "confidence": mat.confidence if source == "inferred_from_ifc_text" else 1.0,
                    "evidenceFields": list(mat.evidence_fields),
                    "matchedKeywords": list(matched_keywords),
                },
            )
            self.graph.add_edge(
                component_id,
                candidate_id,
                "HAS_MATERIAL_CANDIDATE",
                semantic_origin="ontology_aligned",
                inference_rule="material_candidate_creation",
                props={},
            )
            self.graph.add_edge(
                candidate_id,
                element_node_id,
                "GROUNDED_IN_IFC_ELEMENT",
                semantic_origin="agent_interface",
                inference_rule="material_candidate_grounding",
                props={},
            )
            self.graph.add_edge(
                candidate_id,
                mat_id,
                "GROUNDED_IN_MATERIAL",
                semantic_origin="agent_interface",
                inference_rule="material_candidate_material_grounding",
                props={},
            )
            candidates.append(
                {
                    "id": candidate_id,
                    "mat_id": mat_id,
                    "materialName": mat.name,
                    "materialSource": source,
                    "fallbackMaterial": bool(mat.fallback_material),
                    "confidence": mat.confidence if source == "inferred_from_ifc_text" else 1.0,
                    "evidenceFields": list(mat.evidence_fields),
                    "matchedKeywords": matched_keywords,
                    "index": idx,
                }
            )
        return candidates

    def _create_quantity_basis(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        quantity_node_map: Dict[str, str],
    ) -> Dict[str, Any]:
        q = element.quantities
        weight_preferred = ["Weight"]
        volume_preferred = ["NetVolume", "GrossVolume"]
        area_preferred = ["NetSideArea", "GrossSideArea", "GrossArea", "OuterSurfaceArea", "GrossFootprintArea", "CrossSectionArea"]
        count_preferred = ["Count"]

        def present(names: List[str]) -> List[str]:
            return [n for n in names if n in q]

        weight_name = present(weight_preferred)
        if not weight_name:
            weight_name = [name for name in q.keys() if "weight" in normalize_text(name)]
        volume_names = present(volume_preferred)
        volume_names.extend([name for name in q.keys() if name not in volume_names and "volume" in normalize_text(name)])
        area_names = present(area_preferred)
        area_names.extend([name for name in q.keys() if name not in area_names and "area" in normalize_text(name)])
        count_names = present(count_preferred)
        if not count_names:
            count_names = [name for name in q.keys() if normalize_text(name) in {"count", "quantity", "number"} or "count" in normalize_text(name)]

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
                "massReady": available_weight,
                "volumeReady": available_volume,
                "areaReady": available_area,
                "countReady": available_count,
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
        }

    def _add_data_requirement(
        self,
        requirement_id: str,
        required_data_type: str,
        expected_source: str,
        component_global_id: str,
        condition: str = "",
    ) -> str:
        self.graph.add_node(
            requirement_id,
            ["DataRequirement"],
            domain="Requirement",
            semantic_origin="data_requirement",
            reasoning_role=f"{required_data_type}_requirement",
            props={
                "requiredDataType": required_data_type,
                "expectedSource": expected_source,
                "componentGlobalId": component_global_id,
                "condition": condition,
                "runtimeReasoningStatus": "pending_section_3_3",
            },
        )
        return requirement_id

    def _build_query_hints(
        self,
        element: ExtractedElement,
        material_candidates: List[Dict[str, Any]],
    ) -> List[str]:
        hints: List[str] = []
        for token in [element.ifc_type, element.name, element.object_type, element.type_object_name, str(element.properties.get("Reference", "") or "")]:
            hints.extend(self._split_text_tokens(token))
        for c in material_candidates:
            hints.extend(self._split_text_tokens(c.get("materialName", "")))
            for kw in c.get("matchedKeywords", []):
                hints.append(str(kw))

        lower_ifc = element.ifc_type.lower()
        if "beam" in lower_ifc or "column" in lower_ifc:
            hints.extend(["welding", "cutting", "coating"])
        elif "wall" in lower_ifc:
            hints.extend(["framing", "boarding", "insulation"])
        elif "slab" in lower_ifc:
            hints.extend(["casting", "reinforcement", "curing"])
        elif "door" in lower_ifc or "window" in lower_ifc:
            hints.extend(["fabrication", "installation", "sealing"])
        else:
            hints.extend(["assembly", "handling"])

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
        material_candidates: List[Dict[str, Any]],
    ) -> Tuple[str, List[str]]:
        query_hints = self._build_query_hints(element, material_candidates)
        material_text = " | ".join([c.get("materialName", "") for c in material_candidates if c.get("materialName")])
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
                "materialCandidateText": material_text,
                "reference": element.properties.get("Reference", ""),
                "objectType": element.object_type,
                "typeObjectName": element.type_object_name,
                "queryHints": list(query_hints),
                "expectedExternalSource": "manufacturing_process_text",
                "hintSource": "ifc_type_based_retrieval_hint",
                "notProcessInference": True,
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

        req_id = self._add_data_requirement(
            requirement_id=f"data_req:{element.global_id}:manufacturing_process_description",
            required_data_type="manufacturing_process_description",
            expected_source="manufacturing process document or construction method statement",
            component_global_id=element.global_id,
        )
        self.graph.add_edge(
            target_id,
            req_id,
            "REQUIRES_DATA",
            semantic_origin="data_requirement",
            inference_rule="process_target_data_requirement",
            props={},
        )
        return target_id, [req_id]

    def _create_carbon_assessment_targets(
        self,
        element: ExtractedElement,
        element_node_id: str,
        component_id: str,
        material_candidates: List[Dict[str, Any]],
        quantity_basis: Dict[str, Any],
        quantity_node_map: Dict[str, str],
    ) -> Tuple[List[str], List[str]]:
        target_ids: List[str] = []
        requirement_ids: List[str] = []
        basis_options = list(quantity_basis.get("basisOptions", []))

        for c in material_candidates:
            idx = int(c.get("index", 1))
            ct_id = f"carbon_target:{element.global_id}:{idx}"
            factor_lookup_keys = list(c.get("matchedKeywords", []))
            factor_lookup_keys.extend(self._split_text_tokens(c.get("materialName", "")))
            factor_lookup_keys = list(dict.fromkeys([k for k in factor_lookup_keys if str(k).strip()]))[:12]

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
                    "materialCandidateText": c.get("materialName", ""),
                    "factorLookupKeys": list(factor_lookup_keys),
                    "expectedExternalSource": "carbon_factor_excel",
                    "lifeCycleStageCandidate": "A1-A3",
                    "quantityBasisOptions": list(basis_options),
                    "requiresEmissionFactor": True,
                    "requiresDensityIfMassFactorAndNoWeight": not bool(quantity_basis.get("availableWeight", False)),
                    "conditionalRequirements": ["density_if_mass_factor_and_no_weight"] if not bool(quantity_basis.get("availableWeight", False)) else [],
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
            if c.get("mat_id"):
                self.graph.add_edge(
                    ct_id,
                    c.get("mat_id", ""),
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

            req_factor = self._add_data_requirement(
                requirement_id=f"data_req:{element.global_id}:emission_factor",
                required_data_type="emission_factor",
                expected_source="Excel carbon factor table",
                component_global_id=element.global_id,
            )
            self.graph.add_edge(
                ct_id,
                req_factor,
                "REQUIRES_DATA",
                semantic_origin="data_requirement",
                inference_rule="carbon_target_factor_requirement",
                props={},
            )
            requirement_ids.append(req_factor)

            if not bool(quantity_basis.get("availableWeight", False)):
                req_density = self._add_data_requirement(
                    requirement_id=f"data_req:{element.global_id}:density",
                    required_data_type="conditional_density",
                    expected_source="Excel material table or factor table",
                    component_global_id=element.global_id,
                    condition="only required if selected emission factor is kgCO2e/kg and no direct weight exists",
                )
                self.graph.add_edge(
                    ct_id,
                    req_density,
                    "REQUIRES_DATA",
                    semantic_origin="data_requirement",
                    inference_rule="carbon_target_density_requirement",
                    props={},
                )
                requirement_ids.append(req_density)

            if str(quantity_basis.get("quantityReadiness", "")) == "missing":
                req_quantity = self._add_data_requirement(
                    requirement_id=f"data_req:{element.global_id}:quantity_basis",
                    required_data_type="quantity_basis",
                    expected_source="IFC quantity set or user input",
                    component_global_id=element.global_id,
                )
                self.graph.add_edge(
                    ct_id,
                    req_quantity,
                    "REQUIRES_DATA",
                    semantic_origin="data_requirement",
                    inference_rule="carbon_target_quantity_requirement",
                    props={},
                )
                requirement_ids.append(req_quantity)

            if c.get("materialSource") == "inferred_from_ifc_text" or c.get("fallbackMaterial"):
                req_material = self._add_data_requirement(
                    requirement_id=f"data_req:{element.global_id}:material_confirmation:{idx}",
                    required_data_type="material_confirmation",
                    expected_source="IFC text evidence or user confirmation",
                    component_global_id=element.global_id,
                )
                self.graph.add_edge(
                    ct_id,
                    req_material,
                    "REQUIRES_DATA",
                    semantic_origin="data_requirement",
                    inference_rule="carbon_target_material_confirmation_requirement",
                    props={},
                )
                requirement_ids.append(req_material)

            target_ids.append(ct_id)

        return target_ids, sorted(set(requirement_ids))

    def _run_minimal_checks(self, manifest_generated: bool) -> Dict[str, Any]:
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
            mapping_links = [e for e in self.graph.edges if e["src"] == bc_id and e["type"] == "HAS_MAPPING_EVIDENCE"]
            process_links = [e for e in self.graph.edges if e["src"] == bc_id and e["type"] == "HAS_PROCESS_ASSESSMENT_TARGET"]
            carbon_links = [e for e in self.graph.edges if e["src"] == bc_id and e["type"] == "HAS_CARBON_ASSESSMENT_TARGET"]
            if not mapping_links:
                issues.append(f"BuildingComponent missing MappingEvidence: {bc_id}")
            if len(process_links) != 1:
                issues.append(f"BuildingComponent must have exactly one ProcessAssessmentTarget: {bc_id}")
            if not carbon_links:
                has_data_explanation = any(
                    e["type"] == "REQUIRES_DATA" and e["src"] in [x["tgt"] for x in process_links] for e in self.graph.edges
                )
                if not has_data_explanation:
                    issues.append(f"BuildingComponent missing CarbonAssessmentTarget and fallback DataRequirement: {bc_id}")

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
        }
        for label in forbidden_labels:
            if self.graph.node_by_label(label):
                issues.append(f"Forbidden node label exists in backbone mode: {label}")

        if not manifest_generated:
            issues.append("agent_interface_manifest.json was not generated")

        return {"passed": len(issues) == 0, "issueCount": len(issues), "issues": issues[:80]}

    def build(self) -> Tuple[DM2CGraph, Dict[str, Any], Dict[str, Any]]:
        extracted = self.extractor.extract()
        spatial = extracted["spatial"]
        elements: List[ExtractedElement] = extracted["elements"]
        module_id, storey_node_map = self._add_spatial_layer(spatial)

        for element in elements:
            element_node_id, material_bindings, quantity_node_map = self._add_element_bim_nodes(
                element=element,
                module_id=module_id,
                storey_node_map=storey_node_map,
            )
            component_id, mapping_evidence_id = self._create_building_component(
                element=element,
                element_node_id=element_node_id,
                material_bindings=material_bindings,
            )
            material_candidates = self._create_material_candidates(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                material_bindings=material_bindings,
            )
            quantity_basis = self._create_quantity_basis(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                quantity_node_map=quantity_node_map,
            )
            process_target_id, process_reqs = self._create_process_assessment_target(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                material_candidates=material_candidates,
            )
            carbon_target_ids, carbon_reqs = self._create_carbon_assessment_targets(
                element=element,
                element_node_id=element_node_id,
                component_id=component_id,
                material_candidates=material_candidates,
                quantity_basis=quantity_basis,
                quantity_node_map=quantity_node_map,
            )

            first_candidate = material_candidates[0] if material_candidates else {}
            self.per_element_rows.append(
                {
                    "globalId": element.global_id,
                    "ifcType": element.ifc_type,
                    "name": element.name,
                    "reference": str(element.properties.get("Reference", "") or ""),
                    "materialCandidate": str(first_candidate.get("materialName", "") or ""),
                    "materialSource": str(first_candidate.get("materialSource", "") or ""),
                    "quantityReadiness": quantity_basis.get("quantityReadiness", "missing"),
                    "buildingComponentId": component_id,
                    "mappingEvidenceId": mapping_evidence_id,
                    "processAssessmentTargetId": process_target_id,
                    "carbonAssessmentTargetIds": carbon_target_ids,
                    "dataRequirements": sorted(set(process_reqs + carbon_reqs)),
                }
            )

        checks = self._run_minimal_checks(manifest_generated=False)
        report = BackboneMappingReporter(self.graph, self.per_element_rows, checks).build()
        stats = {
            "nodeCount": len(self.graph.nodes),
            "edgeCount": len(self.graph.edges),
            "bimNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "BIM"]),
            "interfaceNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Interface"]),
            "mappingNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Mapping"]),
            "requirementNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Requirement"]),
            "ifcElements": len(elements),
            "buildingComponents": len(self.graph.node_by_label("BuildingComponent")),
            "materialCandidates": len(self.graph.node_by_label("MaterialCandidate")),
            "quantityBasis": len(self.graph.node_by_label("QuantityBasis")),
            "processTargets": len(self.graph.node_by_label("ProcessAssessmentTarget")),
            "carbonTargets": len(self.graph.node_by_label("CarbonAssessmentTarget")),
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
            "checkPassed": checks["passed"],
            "checkIssueCount": checks["issueCount"],
        }
        return self.graph, report, stats

class DM2CGraphBuilder:
    def __init__(self, ifc_path: Path, ontology_path: Path):
        self.ifc_path = ifc_path
        self.ontology_path = ontology_path
        self.graph = DM2CGraph()
        self.ontology = OntologyReader(ontology_path)
        self.extractor = IFCExtractor(ifc_path, self.ontology)
        self.production_engine = ProductionInferenceEngine(self.graph, self.ontology)
        self.carb_engine = CarbonReasoningEngine(self.graph, self.ontology)
        self.per_element_rows: List[Dict[str, Any]] = []

    def _add_spatial_layer(self, spatial: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
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
            semantic_origin="fallback",
            reasoning_role="module_aggregation",
            props={
                "name": "TypeA_Module",
                "moduleSource": "default_module_when_ifc_module_absent",
            },
        )

        self.graph.add_edge(project_id, site_id, "HAS_SITE", "explicit_ifc", "ifc_spatial_structure", {})
        self.graph.add_edge(site_id, building_id, "HAS_BUILDING", "explicit_ifc", "ifc_spatial_structure", {})
        self.graph.add_edge(module_id, building_id, "REPRESENTS_BUILDING", "fallback", "module_building_binding", {})

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
        return module_id, storey_map

    def _add_element_bim_nodes(
        self,
        element: ExtractedElement,
        module_id: str,
        storey_node_map: Dict[str, str],
    ) -> Tuple[str, List[Tuple[ExtractedMaterial, str]], Dict[str, str]]:
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
            [element.ifc_type],
            domain="BIM",
            semantic_origin="explicit_ifc",
            reasoning_role="ifc_building_element_instance",
            props=props,
        )

        # spatial containment
        self.graph.add_edge(module_id, element_id, "CONTAINS_ELEMENT", "explicit_ifc", "module_element_containment", {})
        storey_id = storey_node_map.get(element.storey_name, "")
        if storey_id:
            self.graph.add_edge(storey_id, element_id, "CONTAINS_ELEMENT", "explicit_ifc", "storey_element_containment", {})

        # quantities
        quantity_node_map: Dict[str, str] = {}
        for q_name, q_value in element.quantities.items():
            q_id = f"ifc_quantity:{element.global_id}:{slugify(q_name, 80)}"
            q_unit = element.quantity_units.get(q_name, "")
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
            quantity_node_map[q_name] = q_id

        # materials + layers
        material_bindings: List[Tuple[ExtractedMaterial, str]] = []
        for idx, mat in enumerate(element.materials, start=1):
            mat_id = f"ifc_material:{element.global_id}:{idx}:{slugify(mat.name, 50)}"
            sem_origin = "explicit_ifc" if mat.explicit else "fallback"
            self.graph.add_node(
                mat_id,
                ["IfcMaterial"],
                domain="BIM",
                semantic_origin=sem_origin,
                reasoning_role="material_assignment",
                props={
                    "ifcGlobalId": element.global_id,
                    "name": mat.name,
                    "materialSource": mat.source,
                    "fallbackMaterial": mat.fallback_material,
                    "confidence": mat.confidence,
                    "evidenceFields": list(mat.evidence_fields),
                },
            )
            self.graph.add_edge(
                element_id,
                mat_id,
                "hasMaterial",
                semantic_origin=sem_origin,
                inference_rule="ifc_material_extraction" if mat.explicit else "material_inference_from_ifc_text",
                props={},
            )

            if mat.layer_order is not None or mat.thickness is not None:
                layer_id = f"material_layer:{element.global_id}:{idx}"
                self.graph.add_node(
                    layer_id,
                    ["MaterialLayer"],
                    domain="BIM",
                    semantic_origin="explicit_ifc",
                    reasoning_role="material_layer_detail",
                    props={
                        "ifcGlobalId": element.global_id,
                        "materialName": mat.name,
                        "layerThickness": mat.thickness,
                        "layerOrder": mat.layer_order,
                        "source": "explicit_ifc",
                    },
                )
                self.graph.add_edge(
                    mat_id,
                    layer_id,
                    "HAS_MATERIAL_LAYER",
                    semantic_origin="explicit_ifc",
                    inference_rule="ifc_material_layer_extraction",
                    props={},
                )

            material_bindings.append((mat, mat_id))

        return element_id, material_bindings, quantity_node_map

    def _run_minimal_checks(self) -> Dict[str, Any]:
        issues: List[str] = []

        # edge endpoints + self-loops
        for edge in self.graph.edges:
            src = edge["src"]
            tgt = edge["tgt"]
            if src not in self.graph.nodes or tgt not in self.graph.nodes:
                issues.append(f"Edge endpoint missing: {src} -[{edge['type']}]-> {tgt}")
            if src == tgt:
                issues.append(f"Unexpected self-loop: {src} -[{edge['type']}]-> {tgt}")

        # BuildingComponent <- ALIGNED_AS - IfcElement
        for node in self.graph.node_by_label("BuildingComponent"):
            nid = node["id"]
            has_alignment = any(e["tgt"] == nid and e["type"] == "ALIGNED_AS" for e in self.graph.edges)
            if not has_alignment:
                issues.append(f"BuildingComponent without ALIGNED_AS source: {nid}")

        # TemplateApplication links to BuildingComponent and ProductionTemplate
        for node in self.graph.node_by_label("TemplateApplication"):
            nid = node["id"]
            has_component_link = any(e["tgt"] == nid and e["type"] == "HAS_TEMPLATE_APPLICATION" for e in self.graph.edges)
            has_template_link = any(e["src"] == nid and e["type"] == "APPLIES_TEMPLATE" for e in self.graph.edges)
            if not has_component_link:
                issues.append(f"TemplateApplication missing BuildingComponent link: {nid}")
            if not has_template_link:
                issues.append(f"TemplateApplication missing ProductionTemplate link: {nid}")

        # CarbonEmission completeness checks
        for node in self.graph.node_by_label("CarbonEmission"):
            nid = node["id"]
            status = str(node.get("props", {}).get("calculationStatus", "")).lower()
            has_element = any(e["src"] == nid and e["type"] == "EMISSION_OF" for e in self.graph.edges)
            has_factor = any(e["src"] == nid and e["type"] == "USES_EMISSION_FACTOR" for e in self.graph.edges)
            has_calc = any(e["src"] == nid and e["type"] == "CALCULATED_USING_FORMULA" for e in self.graph.edges)
            has_driver = any(e["src"] == nid and e["type"] == "HAS_CARBON_DRIVER" for e in self.graph.edges)
            has_issue = any(e["src"] == nid and e["type"] == "HAS_CALCULATION_ISSUE" for e in self.graph.edges)

            if status == "complete":
                if not has_element:
                    issues.append(f"Complete CarbonEmission missing element link: {nid}")
                if not has_factor:
                    issues.append(f"Complete CarbonEmission missing factor link: {nid}")
                if not has_calc:
                    issues.append(f"Complete CarbonEmission missing calculation record link: {nid}")
                if not has_driver:
                    issues.append(f"Complete CarbonEmission missing quantity driver link: {nid}")
            else:
                if not has_issue:
                    issues.append(f"Incomplete CarbonEmission missing CalculationIssue: {nid}")

        # Check: PRODUCES_EMISSION from stage/activity must only target process_carbon emissions
        for edge in self.graph.edges:
            if edge["type"] == "PRODUCES_EMISSION":
                src_node = self.graph.nodes.get(edge["src"], {})
                tgt_node = self.graph.nodes.get(edge["tgt"], {})
                src_labels = src_node.get("labels", [])
                tgt_scope = tgt_node.get("props", {}).get("emissionScope", "")
                if ("ManufacturingActivity" in src_labels or "ProductionStage" in src_labels) and tgt_scope != "process_carbon":
                    issues.append(
                        f"Stage/Activity PRODUCES_EMISSION should only target process_carbon, "
                        f"but found scope='{tgt_scope}': {edge['src']} -> {edge['tgt']}"
                    )

        # Check: material_embodied CarbonEmission should only have BuildingComponent as PRODUCES_EMISSION source
        for edge in self.graph.edges:
            if edge["type"] == "PRODUCES_EMISSION":
                tgt_node = self.graph.nodes.get(edge["tgt"], {})
                tgt_scope = tgt_node.get("props", {}).get("emissionScope", "")
                if tgt_scope == "material_embodied":
                    src_node = self.graph.nodes.get(edge["src"], {})
                    src_labels = src_node.get("labels", [])
                    if "BuildingComponent" not in src_labels:
                        issues.append(
                            f"material_embodied CarbonEmission PRODUCES_EMISSION source should be BuildingComponent, "
                            f"found labels={src_labels}: {edge['src']} -> {edge['tgt']}"
                        )

        return {
            "passed": len(issues) == 0,
            "issueCount": len(issues),
            "issues": issues[:50],
        }

    def build(self) -> Tuple[DM2CGraph, Dict[str, Any], Dict[str, Any]]:
        extracted = self.extractor.extract()
        spatial = extracted["spatial"]
        elements: List[ExtractedElement] = extracted["elements"]

        module_id, storey_node_map = self._add_spatial_layer(spatial)

        for element in elements:
            element_node_id, material_bindings, quantity_node_map = self._add_element_bim_nodes(
                element,
                module_id,
                storey_node_map,
            )
            production_context = self.production_engine.infer_for_element(
                element=element,
                element_node_id=element_node_id,
                module_node_id=module_id,
                material_node_ids=[x[1] for x in material_bindings if x[1]],
            )
            carbon_results = self.carb_engine.reason_for_element(
                element=element,
                element_node_id=element_node_id,
                component_id=production_context["building_component_id"],
                material_bindings=material_bindings,
                quantity_node_map=quantity_node_map,
                production_context=production_context,
            )
            stage_for_activity: Dict[str, str] = dict(production_context.get("activity_to_stage_map", {}))

            process_results: List[Dict[str, Any]] = []
            for activity_id in production_context.get("activity_ids", []):
                activity_node = self.graph.nodes.get(activity_id, {})
                activity_template_uri = str(activity_node.get("props", {}).get("templateUri", "") or "")
                stage_id = stage_for_activity.get(activity_id, "")
                proc_row = self.carb_engine.reason_process_carbon_for_activity(
                    activity_id=activity_id,
                    activity_template_uri=activity_template_uri,
                    stage_id=stage_id,
                    element=element,
                    element_node_id=element_node_id,
                    component_id=production_context["building_component_id"],
                )
                if proc_row:
                    process_results.append(proc_row)

            stage_names = []
            for sid in production_context.get("stage_ids", []):
                stage_node = self.graph.nodes.get(sid, {})
                stage_name = str(stage_node.get("props", {}).get("stageName", ""))
                if stage_name:
                    stage_names.append(stage_name)

            material_complete_values = [r["emission_value"] for r in carbon_results if r.get("calculation_status") == "complete" and r.get("emission_value") is not None]
            process_complete_values = [r["emission_value"] for r in process_results if r.get("calculation_status") == "complete" and r.get("emission_value") is not None]
            total_material = round(sum(material_complete_values), 6) if material_complete_values else 0.0
            total_process = round(sum(process_complete_values), 6) if process_complete_values else 0.0
            total_carbon = round(total_material + total_process, 6)

            statuses = [r.get("calculation_status") for r in carbon_results] + [r.get("calculation_status") for r in process_results]
            element_status = "complete" if statuses and all(x == "complete" for x in statuses) else "incomplete"

            reasoning_paths = [r.get("reasoning_path_sample", "") for r in carbon_results if r.get("reasoning_path_sample")]
            reasoning_paths.extend([r.get("reasoning_path_sample", "") for r in process_results if r.get("reasoning_path_sample")])
            activities_with_energy = len([r for r in process_results if r.get("energy_profile_found")])
            activities_without_energy = len(process_results) - activities_with_energy

            self.per_element_rows.append(
                {
                    "globalId": element.global_id,
                    "ifcType": element.ifc_type,
                    "name": element.name,
                    "materialText": " | ".join([m.name for m in element.materials if m.name]),
                    "buildingComponentId": production_context["building_component_id"],
                    "selectedTemplate": production_context["template_match"].get("template_label", ""),
                    "matchLevel": production_context["template_match"].get("match_level", ""),
                    "confidence": production_context["template_match"].get("confidence", 0.0),
                    "productionStages": stage_names,
                    "carbonCalculationStatus": element_status,
                    "materialCarbonValue": total_material,
                    "processCarbonValue": total_process,
                    "totalCarbonValue": total_carbon,
                    "processEmissionValues": process_complete_values,
                    "activitiesWithEnergyData": activities_with_energy,
                    "activitiesWithoutEnergyData": activities_without_energy,
                    "reasoningPathAvailable": any(bool(r.get("reasoning_path_available")) for r in carbon_results) or bool(process_complete_values),
                    "reasoningPathSamples": reasoning_paths[:3],
                }
            )

        checks = self._run_minimal_checks()
        report = MappingReporter(self.graph, self.per_element_rows, checks).build()

        stats = {
            "nodeCount": len(self.graph.nodes),
            "edgeCount": len(self.graph.edges),
            "bimNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "BIM"]),
            "productionNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Production"]),
            "carbonNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Carbon"]),
            "mappingNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Mapping"]),
            "calculationNodes": len([n for n in self.graph.nodes.values() if n.get("domain") == "Calculation"]),
            "materialCarbonEmissionNodes": len(
                [
                    n
                    for n in self.graph.node_by_label("CarbonEmission")
                    if str(n.get("props", {}).get("emissionScope", "")) == "material_embodied"
                ]
            ),
            "processCarbonEmissionNodes": len(
                [
                    n
                    for n in self.graph.node_by_label("CarbonEmission")
                    if str(n.get("props", {}).get("emissionScope", "")) == "process_carbon"
                ]
            ),
            "checkPassed": checks["passed"],
            "checkIssueCount": checks["issueCount"],
        }
        return self.graph, report, stats


def build_and_export_legacy_full(ifc_path: Path, ontology_path: Path, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    builder = DM2CGraphBuilder(ifc_path=ifc_path, ontology_path=ontology_path)
    graph, mapping_report, stats = builder.build()

    exporter = GraphExporter(graph, source_ifc=ifc_path, source_ontology=ontology_path, graph_profile="legacy_full")
    json_path = out_dir / "dm2c_graph.json"
    cypher_path = out_dir / "dm2c_graph.cypher"
    ttl_path = out_dir / "dm2c_graph.ttl"
    report_path = out_dir / "dm2c_mapping_report.json"
    cq_path = out_dir / "dm2c_competency_queries.cypher"

    exporter.export_json(json_path)
    exporter.export_cypher(cypher_path)
    exporter.export_ttl(ttl_path)
    report_path.write_text(json.dumps(mapping_report, ensure_ascii=False, indent=2), encoding="utf-8")
    exporter.export_competency_queries(cq_path)

    return {
        "graph_json": str(json_path),
        "graph_cypher": str(cypher_path),
        "graph_ttl": str(ttl_path),
        "mapping_report": str(report_path),
        "competency_queries": str(cq_path),
        "agent_interface_manifest": "",
        "stats": stats,
        "mapping_report_data": mapping_report,
        "mode": "legacy_full",
    }


def build_and_export_backbone(ifc_path: Path, ontology_path: Path, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    builder = BackboneGraphBuilder(ifc_path=ifc_path, ontology_path=ontology_path)
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
    # Re-run minimal checks after manifest is physically written, so manifestGenerated is true runtime evidence.
    manifest_generated = manifest_path.exists()
    refreshed_checks = builder._run_minimal_checks(manifest_generated=manifest_generated)
    mapping_report = BackboneMappingReporter(graph, builder.per_element_rows, refreshed_checks).build()
    mapping_report.setdefault("minimalChecks", {})["manifestGenerated"] = manifest_generated
    stats["checkPassed"] = refreshed_checks.get("passed", False)
    stats["checkIssueCount"] = refreshed_checks.get("issueCount", 0)
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
    }


def build_and_export(ifc_path: Path, ontology_path: Path, out_dir: Path, mode: str = "backbone") -> Dict[str, Any]:
    if mode == "legacy_full":
        return build_and_export_legacy_full(ifc_path=ifc_path, ontology_path=ontology_path, out_dir=out_dir)
    return build_and_export_backbone(ifc_path=ifc_path, ontology_path=ontology_path, out_dir=out_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DM2C Section 3.2 IFC-to-graph constructor")
    parser.add_argument("--ifc", required=True, help="Path to IFC file")
    parser.add_argument("--ontology", required=True, help="Path to ontology TTL file")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument(
        "--mode",
        default="backbone",
        choices=["backbone", "legacy_full"],
        help="Execution mode. backbone builds Section 3.2 graph backbone only. legacy_full keeps the old end-to-end KG behavior.",
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

    result = build_and_export(ifc_path=ifc_path, ontology_path=ontology_path, out_dir=out_dir, mode=args.mode)
    stats = result["stats"]
    report = result["mapping_report_data"]
    summary = report.get("summary", {})

    print("DM2C IFC-to-Graph completed.")
    print(f"  Mode: {result.get('mode', args.mode)}")
    print(f"  IFC: {ifc_path}")
    print(f"  Ontology: {ontology_path}")
    print(f"  Output directory: {out_dir}")
    print("Generated files:")
    print(f"  - {result['graph_json']}")
    print(f"  - {result['graph_cypher']}")
    print(f"  - {result['graph_ttl']}")
    print(f"  - {result['mapping_report']}")
    if result.get("agent_interface_manifest"):
        print(f"  - {result['agent_interface_manifest']}")
    print(f"  - {result['competency_queries']}")

    if args.mode == "backbone":
        print("Backbone summary:")
        print(
            "  Nodes={nodeCount}, Edges={edgeCount}, BIM={bimNodes}, Interface={interfaceNodes}, Mapping={mappingNodes}, Requirement={requirementNodes}".format(
                **stats
            )
        )
        print(
            "  IFC elements={ifcElements}, BuildingComponent={buildingComponents}, MaterialCandidate={materialCandidates}, QuantityBasis={quantityBasis}, ProcessTarget={processTargets}, CarbonTarget={carbonTargets}, DataRequirement={dataRequirements}".format(
                **stats
            )
        )
        print(
            "  Forbidden nodes in backbone: ProductionTemplate={forbiddenProductionTemplates}, ProductionStage={forbiddenProductionStages}, ManufacturingActivity={forbiddenManufacturingActivities}, ModuleProduction={forbiddenModuleProduction}, Resource={forbiddenResources}, ConsumptionDriver={forbiddenConsumptionDrivers}, ConsumptionQuantity={forbiddenConsumptionQuantities}, EmissionFactor={forbiddenEmissionFactors}, CarbonEmission={forbiddenCarbonEmissions}, CalculationRecord={forbiddenCalculationRecords}".format(
                **stats
            )
        )
        print(f"  Minimal checks passed: {stats['checkPassed']} (issues={stats['checkIssueCount']})")
    else:
        print("Graph summary:")
        print(
            "  Nodes={nodeCount}, Edges={edgeCount}, BIM={bimNodes}, Production={productionNodes}, Carbon={carbonNodes}, Mapping={mappingNodes}, Calculation={calculationNodes}".format(
                **stats
            )
        )
        print(
            "  CarbonEmission nodes: material={materialCarbonEmissionNodes}, process={processCarbonEmissionNodes}".format(
                **stats
            )
        )
        print(
            "  Elements processed={ifcElementsProcessed}, Templates applied={productionTemplatesApplied}, Carbon complete={successfulCarbonCalculations}, Carbon incomplete={incompleteCarbonCalculations}".format(
                **summary
            )
        )
        print(
            "  C_mat={totalMaterialCarbon_kgCO2e}, C_proc={totalProcessCarbon_kgCO2e}, C_MM={totalCarbon_kgCO2e}".format(
                **summary
            )
        )
        print(f"  Minimal checks passed: {stats['checkPassed']} (issues={stats['checkIssueCount']})")


if __name__ == "__main__":
    main()

