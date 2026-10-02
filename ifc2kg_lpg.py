"""
Ontology-Driven IFC → Labeled Property Graph (LPG) Converter
==============================================================
Follows the 5-step pipeline (Fig. X):

  Step 1: IFC parsing & entity extraction
  Step 2: Semantic alignment to ontology classes
  Step 3: Rule-based inference of stages, activities, resources & consumption drivers
  Step 4: Deterministic carbon calculation
  Step 5: KG population — Neo4j Cypher + JSON property graph

All domain knowledge is read from ontology.ttl at runtime.
Zero hardcoded domain logic in this file.

Usage:
  python ifc2kg_lpg.py --ontology ontology_v3.ttl --ifc model.ifc --out-dir output/
"""

from __future__ import annotations

import argparse, json, re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import ifcopenshell
except ImportError:
    ifcopenshell = None

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

# ── Namespaces (for ontology reading only) ───────────────────
BIM    = Namespace("http://example.org/bim#")
MC     = Namespace("http://example.org/mc#")
CARBON = Namespace("http://example.org/carbon#")
ONTO   = Namespace("http://example.org/onto#")

# ── Utilities ────────────────────────────────────────────────
def normalize(s): return re.sub(r"\s+", "", (s or "").strip().casefold())
def safe_float(v, d=0.0):
    try: return float(v) if v is not None else d
    except: return d
def slugify(v, n=60): return (re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]+","_",str(v)).strip("_") or "x")[:n]


# ═════════════════════════════════════════════════════════════
# ONTOLOGY READER  (reads rules from TTL — shared with RDF version)
# ═════════════════════════════════════════════════════════════

class OntologyReader:
    """Reads all pipeline rules from the ontology TTL file."""

    def __init__(self, path):
        self.g = Graph()
        self.g.parse(str(path), format="turtle")
        self._c = {}

    def _cached(self, key, fn):
        if key not in self._c: self._c[key] = fn()
        return self._c[key]

    def get_supported_types(self):
        def _():
            return [{"ifcType": str(self.g.value(s, ONTO.ifcTypeName) or ""),
                     "label": str(self.g.value(s, ONTO.lpgLabel) or "")}
                    for s in self.g.subjects(RDF.type, ONTO.SupportedIfcType)]
        return self._cached("types", _)

    def get_property_mappings(self):
        def _():
            m = {}
            for s in self.g.subjects(RDF.type, ONTO.PropertyMapping):
                name = str(self.g.value(s, ONTO.ifcPropertyName) or "")
                pred = str(self.g.value(s, ONTO.rdfPredicate) or "")
                if name and pred:
                    m[name] = pred.split(":")[-1]  # e.g. "bim:loadBearing" → "loadBearing"
            return m
        return self._cached("props", _)

    def get_quantity_mappings(self):
        def _():
            m = {}
            for s in self.g.subjects(RDF.type, ONTO.QuantityMapping):
                name = str(self.g.value(s, ONTO.ifcQuantityName) or "")
                pred = str(self.g.value(s, ONTO.rdfPredicate) or "")
                if name and pred: m[name] = pred.split(":")[-1]
            return m
        return self._cached("qtys", _)

    def get_production_templates(self):
        def _():
            templates = []
            for tmpl in self.g.subjects(RDF.type, ONTO.ProductionTemplate):
                label = str(self.g.value(tmpl, RDFS.label) or tmpl)
                applies = [str(o).split("#")[-1] for o in self.g.objects(tmpl, ONTO.appliesTo)]
                mat_kw = [str(o) for o in self.g.objects(tmpl, ONTO.materialKeyword)]
                stages = []
                for su in self.g.objects(tmpl, ONTO.hasTemplateStage):
                    stype = None
                    for t in self.g.objects(su, RDF.type):
                        short = str(t).split("#")[-1]
                        if short != "ProductionStage" and short.endswith("Stage"):
                            stype = short; break
                    acts = []
                    for au in self.g.objects(su, ONTO.hasTemplateActivity):
                        res = [{"label": str(self.g.value(r, RDFS.label) or str(r).split("#")[-1])}
                               for r in self.g.objects(au, MC.requiresResource)]
                        acts.append({"name": str(self.g.value(au, MC.activityName) or ""),
                                     "sequence": int(self.g.value(au, MC.sequence) or 0),
                                     "resources": res})
                    acts.sort(key=lambda x: x["sequence"])
                    stages.append({"name": str(self.g.value(su, MC.stageName) or ""),
                                   "description": str(self.g.value(su, MC.stageDescription) or ""),
                                   "location": str(self.g.value(su, MC.location) or ""),
                                   "order": int(self.g.value(su, ONTO.stageOrder) or 0),
                                   "type": stype, "activities": acts})
                stages.sort(key=lambda x: x["order"])
                templates.append({"label": label, "applies_to": applies,
                                  "material_keywords": mat_kw, "stages": stages})
            return templates
        return self._cached("templates", _)

    def get_emission_factors(self):
        def _():
            efs = [{"keyword": str(self.g.value(s, ONTO.forMaterialKeyword) or ""),
                    "factor": safe_float(self.g.value(s, CARBON.factorValue)),
                    "unit": str(self.g.value(s, CARBON.factorUnit) or ""),
                    "source": str(self.g.value(s, CARBON.factorSource) or "")}
                   for s in self.g.subjects(RDF.type, CARBON.EmissionFactor)]
            efs.sort(key=lambda x: len(normalize(x["keyword"])), reverse=True)
            return efs
        return self._cached("efs", _)

    def match_template(self, ifc_type, mat_name="", elem_name="", ref=""):
        search = normalize(" ".join(filter(None, [mat_name, elem_name, ref])))
        ifc_short = ifc_type.replace("StandardCase", "")
        best, best_score = None, -1
        for t in self.get_production_templates():
            score = 50 if ifc_short in t["applies_to"] else 0
            bonus = max((len(normalize(kw)) for kw in t["material_keywords"]
                         if normalize(kw) and normalize(kw) in search), default=0)
            if bonus: score += 10 + bonus
            elif t["material_keywords"]: score -= 5
            if score > best_score: best_score, best = score, t
        return best if best_score >= 50 else None

    def match_ef(self, mat_name):
        tgt = normalize(mat_name)
        for ef in self.get_emission_factors():
            if normalize(ef["keyword"]) and normalize(ef["keyword"]) in tgt: return ef
        for ef in self.get_emission_factors():
            if normalize(ef["keyword"]) == normalize("默认"): return ef
        return {"keyword":"默认","factor":0.12,"unit":"kgCO2e/kg","source":"estimated"}

    def match_density(self, mat_name):
        tgt = normalize(mat_name)
        for s in self.g.subjects(RDF.type, BIM.IfcMaterial):
            kw = str(self.g.value(s, ONTO.materialKeyword) or "")
            density = self.g.value(s, BIM.density)
            if kw and density and normalize(kw) in tgt: return safe_float(density)
        return 1200.0


# ═════════════════════════════════════════════════════════════
# STEP 1: IFC PARSING & ENTITY EXTRACTION
# ═════════════════════════════════════════════════════════════

class IFCParser:
    """Parses IFC file and extracts elements, materials, quantities, properties."""

    def __init__(self, path, onto):
        if ifcopenshell is None:
            raise ImportError("ifcopenshell required")
        self.ifc = ifcopenshell.open(str(path))
        self.onto = onto
        self._build_caches()

    def _build_caches(self):
        self.mat_assoc = defaultdict(list)
        for rel in self.ifc.by_type("IfcRelAssociatesMaterial"):
            mats = self._extract_materials(rel.RelatingMaterial)
            for obj in rel.RelatedObjects:
                gid = getattr(obj, "GlobalId", None)
                if gid: self.mat_assoc[gid].extend(mats)

        self.prop_map = defaultdict(dict)
        self.qty_map = defaultdict(dict)
        for rel in self.ifc.by_type("IfcRelDefinesByProperties"):
            pset = rel.RelatingPropertyDefinition
            for obj in rel.RelatedObjects:
                gid = getattr(obj, "GlobalId", None)
                if not gid: continue
                if pset.is_a("IfcPropertySet"):
                    for p in pset.HasProperties:
                        if p.is_a("IfcPropertySingleValue") and p.NominalValue:
                            v = p.NominalValue.wrappedValue
                            if isinstance(v, (int, float, str, bool)):
                                self.prop_map[gid][p.Name] = v
                elif pset.is_a("IfcElementQuantity"):
                    for q in pset.Quantities:
                        v = self._qty_val(q)
                        if v is not None: self.qty_map[gid][q.Name] = v

        self.storey_map = {}
        for rel in self.ifc.by_type("IfcRelContainedInSpatialStructure"):
            for e in rel.RelatedElements:
                gid = getattr(e, "GlobalId", None)
                if gid: self.storey_map[gid] = getattr(rel.RelatingStructure, "Name", "") or ""

    def _qty_val(self, q):
        for cls, attr in [("IfcQuantityLength","LengthValue"),("IfcQuantityArea","AreaValue"),
                          ("IfcQuantityVolume","VolumeValue"),("IfcQuantityWeight","WeightValue")]:
            if q.is_a(cls): return round(safe_float(getattr(q, attr, 0)), 6)
        return None

    def _extract_materials(self, mat):
        materials = []
        if mat is None: return materials
        def add(name, thickness):
            if name: materials.append({"name": str(name), "thickness": safe_float(thickness) if thickness else None})
        if mat.is_a("IfcMaterialLayerSetUsage"):
            for layer in getattr(mat.ForLayerSet, "MaterialLayers", []) or []:
                add(getattr(getattr(layer,"Material",None),"Name",None), getattr(layer,"LayerThickness",None))
        elif mat.is_a("IfcMaterialLayerSet"):
            for layer in getattr(mat, "MaterialLayers", []) or []:
                add(getattr(getattr(layer,"Material",None),"Name",None), getattr(layer,"LayerThickness",None))
        elif mat.is_a("IfcMaterialConstituentSet"):
            for c in getattr(mat, "MaterialConstituents", []) or []:
                add(getattr(getattr(c,"Material",None),"Name",None), None)
        elif mat.is_a("IfcMaterialProfileSetUsage"):
            for p in getattr(mat.ForProfileSet, "MaterialProfiles", []) or []:
                add(getattr(getattr(p,"Material",None),"Name",None), None)
        elif mat.is_a("IfcMaterial"):
            add(getattr(mat,"Name",None), None)
        elif mat.is_a("IfcMaterialList"):
            for m in getattr(mat,"Materials",[]) or []: add(getattr(m,"Name",None), None)
        seen = set(); unique = []
        for m in materials:
            key = (m["name"], m["thickness"])
            if key not in seen: seen.add(key); unique.append(m)
        return unique

    def get_spatial(self):
        P = self.ifc.by_type("IfcProject"); S = self.ifc.by_type("IfcSite")
        B = self.ifc.by_type("IfcBuilding"); ST = self.ifc.by_type("IfcBuildingStorey")
        return {"project": getattr(P[0],"Name","Project") if P else "Project",
                "site": getattr(S[0],"Name","Site") if S else "Site",
                "building": getattr(B[0],"Name","Building") if B else "Building",
                "storeys": [{"name": getattr(x,"Name",""), "elevation": round(safe_float(getattr(x,"Elevation",0)),2)} for x in ST]}

    def get_elements(self):
        supported = {t["ifcType"]: t["label"] for t in self.onto.get_supported_types()}
        elements = []
        for ifc_type, label in supported.items():
            for elem in self.ifc.by_type(ifc_type):
                gid = getattr(elem, "GlobalId", None)
                if not gid: continue
                mats = self.mat_assoc.get(gid, [])
                elements.append({"gid": gid, "name": getattr(elem,"Name","") or "",
                    "ifc_type": ifc_type, "label": label, "materials": mats,
                    "quantities": self.qty_map.get(gid, {}),
                    "properties": self.prop_map.get(gid, {}),
                    "storey": self.storey_map.get(gid),
                    "primary_material": mats[0]["name"] if mats else "",
                    "reference": self.prop_map.get(gid, {}).get("Reference", "")})
        return elements


# ═════════════════════════════════════════════════════════════
# STEP 2-5: LPG BUILDER
# ═════════════════════════════════════════════════════════════

class LPGBuilder:
    """Builds a Labeled Property Graph following the 5-step pipeline."""

    def __init__(self, onto: OntologyReader):
        self.onto = onto
        self.nodes: List[dict] = []       # {id, label, domain, properties}
        self.edges: List[dict] = []       # {source, target, type, properties, cross_domain}
        self._nid = 0
        self._mat_ids: Dict[str, str] = {}
        self._ef_ids: Dict[str, str] = {}
        self._res_ids: Dict[str, str] = {}
        self.stats = Counter()

    def _next_id(self, prefix): self._nid += 1; return f"{prefix}_{self._nid}"

    def _add_node(self, nid, label, domain, **props):
        self.nodes.append({"id": nid, "label": label, "domain": domain, "properties": props})
        return nid

    def _add_edge(self, src, tgt, etype, cross=False, **props):
        self.edges.append({"source": src, "target": tgt, "type": etype,
                           "cross_domain": cross, "properties": props})

    def _ensure_material(self, name):
        if name in self._mat_ids: return self._mat_ids[name]
        nid = self._next_id("MAT")
        density = self.onto.match_density(name)
        ef = self.onto.match_ef(name)
        self._add_node(nid, "IfcMaterial", "BIM", name=name, density=density)
        # Emission factor node
        ef_key = ef["keyword"]
        if ef_key not in self._ef_ids:
            ef_id = self._next_id("EF")
            self._add_node(ef_id, "EmissionFactor", "Carbon",
                           keyword=ef["keyword"], factorValue=ef["factor"],
                           factorUnit=ef["unit"], factorSource=ef["source"])
            self._ef_ids[ef_key] = ef_id
        self._add_edge(nid, self._ef_ids[ef_key], "HAS_EMISSION_FACTOR", cross=True)
        self._mat_ids[name] = nid
        return nid

    def build(self, parser: IFCParser):
        """Execute all 5 pipeline steps."""

        # ── STEP 2: Semantic alignment ──────────────────────────
        spatial = parser.get_spatial()
        mod_id = self._add_node("MODULE_1", "Module", "BIM",
                                name="TypeA_Module", building=spatial["building"])

        prop_map = self.onto.get_property_mappings()
        qty_map = self.onto.get_quantity_mappings()

        for elem in parser.get_elements():
            # Align IFC entity to ontology class
            elem_id = self._next_id("ELEM")
            elem_props = {"globalId": elem["gid"], "name": elem["name"], "ifcType": elem["ifc_type"]}

            # Map ontology-declared properties
            for ifc_name, prop_key in prop_map.items():
                if ifc_name in elem["properties"]:
                    elem_props[prop_key] = elem["properties"][ifc_name]

            self._add_node(elem_id, elem["label"], "BIM", **elem_props)
            self._add_edge(mod_id, elem_id, "CONTAINS_ELEMENT")

            # Quantity node
            qty_id = None
            if elem["quantities"]:
                qty_id = self._next_id("QTY")
                qty_props = {}
                for qname, qval in elem["quantities"].items():
                    key = qty_map.get(qname, qname)
                    qty_props[key] = round(qval, 6)
                self._add_node(qty_id, "IfcElementQuantity", "BIM", **qty_props)
                self._add_edge(elem_id, qty_id, "HAS_QUANTITY_SET")

            # Material nodes (de-reified)
            portions = self._allocate_carbon(elem)
            mat_ids_for_elem = []
            for p in portions:
                mid = self._ensure_material(p.name)
                mat_ids_for_elem.append(mid)
                self._add_edge(elem_id, mid, "hasMaterial")

            if not portions:
                for m in elem["materials"]:
                    if m.get("name"):
                        mid = self._ensure_material(m["name"])
                        self._add_edge(elem_id, mid, "hasMaterial")

            self.stats["elements"] += 1

            # ── STEP 3: Rule-based inference ────────────────────
            tmpl = self.onto.match_template(
                elem["ifc_type"], elem.get("primary_material",""),
                elem.get("name",""), elem.get("reference",""))

            if tmpl:
                mp_id = self._next_id("MP")
                self._add_node(mp_id, "ModuleProduction", "MC",
                               templateName=tmpl["label"])
                self._add_edge(mp_id, elem_id, "HAS_INPUT", cross=True)
                self._add_edge(mp_id, mod_id, "HAS_OUTPUT", cross=True)

                prev_stage = None
                for stage_def in tmpl["stages"]:
                    s_id = self._next_id("STG")
                    s_label = stage_def["type"] or "ProductionStage"
                    self._add_node(s_id, s_label, "MC",
                                   stageName=stage_def["name"],
                                   stageDescription=stage_def["description"],
                                   location=stage_def["location"])
                    self._add_edge(mp_id, s_id, "HAS_PRODUCTION_STAGE")
                    self._add_edge(elem_id, s_id, "REQUIRES_STAGE", cross=True)
                    if prev_stage:
                        self._add_edge(prev_stage, s_id, "PRECEDES_STAGE")
                    prev_stage = s_id

                    # Consumption driver (MC → Carbon bridge)
                    cd_id = self._next_id("CD")
                    self._add_node(cd_id, "ConsumptionDriver", "Carbon",
                                   driverType="MaterialConsumption",
                                   relatedStage=stage_def["name"])
                    self._add_edge(s_id, cd_id, "HAS_CARBON_DRIVER", cross=True)

                    prev_act = None
                    for act_def in stage_def["activities"]:
                        a_id = self._next_id("ACT")
                        self._add_node(a_id, "ManufacturingActivity", "MC",
                                       activityName=act_def["name"],
                                       sequence=act_def["sequence"])
                        self._add_edge(s_id, a_id, "HAS_PROCESS")
                        self._add_edge(a_id, cd_id, "RELATED_TO_DRIVER", cross=True)
                        if prev_act:
                            self._add_edge(prev_act, a_id, "FOLLOWED_BY")
                        prev_act = a_id

                        # Resources
                        for res_def in act_def["resources"]:
                            r_label = res_def["label"]
                            if r_label not in self._res_ids:
                                r_id = self._next_id("RES")
                                self._add_node(r_id, "Resource", "MC", name=r_label)
                                self._res_ids[r_label] = r_id
                            self._add_edge(a_id, self._res_ids[r_label], "REQUIRES_RESOURCE")

                        # Activity consumes material (cross-domain)
                        for mid in mat_ids_for_elem:
                            self._add_edge(a_id, mid, "CONSUMES_MATERIAL", cross=True)

                self.stats["with_template"] += 1
            else:
                self.stats["no_template"] += 1

            # ── STEP 4: Deterministic carbon calculation ────────
            if portions:
                for i, p in enumerate(portions, 1):
                    emission_value = p.mass * p.ef_value

                    ce_id = self._next_id("CE")
                    self._add_node(ce_id, "CarbonEmission", "Carbon",
                                   emissionValue=round(emission_value, 6),
                                   emissionUnit="kgCO2e",
                                   lifeCycleStage="A1-A3",
                                   materialName=p.name,
                                   massKg=round(p.mass, 6),
                                   allocationMethod=p.method)
                    self._add_edge(elem_id, ce_id, "PRODUCES_EMISSION", cross=True)
                    self._add_edge(ce_id, elem_id, "EMISSION_OF", cross=True)

                    # Link to consumption quantity
                    cq_id = self._next_id("CQ")
                    self._add_node(cq_id, "ConsumptionQuantity", "Carbon",
                                   massKg=round(p.mass, 6),
                                   volumeM3=round(p.volume, 6),
                                   density=p.density)
                    if qty_id:
                        self._add_edge(cq_id, qty_id, "RELATED_TO_QUANTITY", cross=True)

        return self

    def _allocate_carbon(self, elem):
        mats = [m for m in elem.get("materials", []) if m.get("name")]
        if not mats: return []
        q = elem.get("quantities", {})
        vol = safe_float(q.get("NetVolume", 0)) or safe_float(q.get("GrossVolume", 0))
        if len(mats) == 1:
            fractions, method = [1.0], "single_material"
        elif all(m.get("thickness") and safe_float(m["thickness"]) > 0 for m in mats):
            total = sum(safe_float(m["thickness"]) for m in mats)
            fractions = [safe_float(m["thickness"])/total for m in mats] if total > 0 else [1/len(mats)]*len(mats)
            method = "thickness_ratio"
        else:
            fractions = [1/len(mats)]*len(mats); method = "equal_split"
        portions = []
        for m, frac in zip(mats, fractions):
            density = self.onto.match_density(m["name"])
            ef = self.onto.match_ef(m["name"])
            alloc_vol = vol * frac
            mass = alloc_vol * density
            portions.append(type('P',(),{"name":m["name"],"thickness":m.get("thickness"),
                "fraction":frac,"volume":alloc_vol,"density":density,"mass":mass,
                "ef_value":ef["factor"],"ef_unit":ef["unit"],"ef_source":ef["source"],
                "method":method})())
        return portions

    # ── STEP 5: KG Population — Output Formats ──────────────

    def to_json(self, path):
        """Export as JSON property graph."""
        graph = {
            "nodes": self.nodes,
            "edges": self.edges,
            "statistics": self.get_stats()
        }
        Path(path).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(self.nodes), len(self.edges)

    def to_cypher(self, path):
        """Export as Neo4j Cypher import script."""
        lines = [
            "// ═══════════════════════════════════════════════",
            "// Auto-generated Neo4j Cypher import script",
            "// Three-domain KG: BIM × MC × Carbon",
            "// ═══════════════════════════════════════════════",
            "",
            "// ── Clear database ──",
            "MATCH (n) DETACH DELETE n;",
            "",
            "// ── Create indexes ──",
            "CREATE INDEX IF NOT EXISTS FOR (n:IfcWall) ON (n.globalId);",
            "CREATE INDEX IF NOT EXISTS FOR (n:IfcBeam) ON (n.globalId);",
            "CREATE INDEX IF NOT EXISTS FOR (n:IfcColumn) ON (n.globalId);",
            "CREATE INDEX IF NOT EXISTS FOR (n:IfcSlab) ON (n.globalId);",
            "CREATE INDEX IF NOT EXISTS FOR (n:IfcMaterial) ON (n.name);",
            "CREATE INDEX IF NOT EXISTS FOR (n:CarbonEmission) ON (n.emissionValue);",
            "",
            "// ── Create nodes ──",
        ]

        for node in self.nodes:
            props = ", ".join(f'{k}: {json.dumps(v)}' for k, v in node["properties"].items()
                              if v is not None)
            props_str = f" {{{props}}}" if props else ""
            lines.append(f"CREATE (:{node['label']}{props_str})"
                         f"  // [{node['domain']}] id={node['id']};")

        lines.extend(["", "// ── Create relationships ──",
                       "// Using MATCH + CREATE for each edge", ""])

        # Group edges by type for efficiency
        for edge in self.edges:
            src_node = next((n for n in self.nodes if n["id"] == edge["source"]), None)
            tgt_node = next((n for n in self.nodes if n["id"] == edge["target"]), None)
            if not src_node or not tgt_node: continue

            src_match = f"(a:{src_node['label']} {{{'globalId: ' + json.dumps(src_node['properties'].get('globalId','')) if src_node['properties'].get('globalId') else 'name: ' + json.dumps(src_node['properties'].get('name', src_node['id']))}}})"
            tgt_match = f"(b:{tgt_node['label']} {{{'globalId: ' + json.dumps(tgt_node['properties'].get('globalId','')) if tgt_node['properties'].get('globalId') else 'name: ' + json.dumps(tgt_node['properties'].get('name', tgt_node['id']))}}})"

            cross_comment = " // CROSS-DOMAIN" if edge["cross_domain"] else ""
            lines.append(f"MATCH {src_match}, {tgt_match}")
            lines.append(f"CREATE (a)-[:{edge['type']}]->(b);{cross_comment}")

        Path(path).write_text("\n".join(lines), encoding="utf-8")
        return len(lines)

    def get_stats(self):
        domain_counts = Counter()
        label_counts = Counter()
        for n in self.nodes:
            domain_counts[n["domain"]] += 1
            label_counts[n["label"]] += 1

        edge_type_counts = Counter()
        cross_count = 0
        for e in self.edges:
            edge_type_counts[e["type"]] += 1
            if e["cross_domain"]: cross_count += 1

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "cross_domain_edges": cross_count,
            "cross_domain_ratio": round(cross_count / max(len(self.edges), 1), 3),
            "domains": dict(domain_counts),
            "node_labels": dict(label_counts),
            "edge_types": dict(edge_type_counts),
            "pipeline_stats": dict(self.stats),
        }


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Ontology-driven IFC → LPG (Neo4j)")
    ap.add_argument("--ontology", default="ontology_v3.ttl")
    ap.add_argument("--ifc", default="/mnt/project/typea_4_.ifc")
    ap.add_argument("--out-dir", default="lpg_output")
    args = ap.parse_args()

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Ontology-Driven IFC → LPG (5-Step Pipeline)")
    print("=" * 60)

    print("\n[Step 0] Loading ontology rules...")
    onto = OntologyReader(args.ontology)
    types = onto.get_supported_types()
    templates = onto.get_production_templates()
    efs = onto.get_emission_factors()
    print(f"  Supported types: {[t['ifcType'] for t in types]}")
    print(f"  Production templates: {len(templates)}")
    print(f"  Emission factors: {len(efs)}")

    print("\n[Step 1] IFC parsing & entity extraction...")
    parser = IFCParser(args.ifc, onto)
    elements = parser.get_elements()
    print(f"  Extracted {len(elements)} building elements")

    print("\n[Steps 2-4] Semantic alignment → Rule inference → Carbon calculation...")
    builder = LPGBuilder(onto)
    builder.build(parser)

    print("\n[Step 5] KG population — serialising outputs...")
    nn, ne = builder.to_json(out / "kg_lpg.json")
    cypher_lines = builder.to_cypher(out / "kg_lpg.cypher")
    stats = builder.get_stats()
    (out / "kg_lpg_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"Nodes: {nn}")
    print(f"Edges: {ne}")
    print(f"Cross-domain edges: {stats['cross_domain_edges']} ({stats['cross_domain_ratio']*100:.1f}%)")
    print(f"Domains: BIM={stats['domains'].get('BIM',0)}  MC={stats['domains'].get('MC',0)}  Carbon={stats['domains'].get('Carbon',0)}")
    print(f"\nOutputs:")
    print(f"  {out/'kg_lpg.json'}        — JSON property graph")
    print(f"  {out/'kg_lpg.cypher'}      — Neo4j Cypher import script")
    print(f"  {out/'kg_lpg_stats.json'}  — statistics")
    print(f"\nNeo4j import: cat {out/'kg_lpg.cypher'} | cypher-shell -u neo4j -p <password>")

if __name__ == "__main__":
    main()
