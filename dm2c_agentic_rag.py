"""
DM2C Section 3.3 API-based RAG comparison.

The script consumes the Section 3.2 IFC-derived backbone graph, retrieves
manufacturing-process and carbon-factor evidence from external sources, and
compares standard RAG, Graph RAG, and Agentic RAG with an OpenAI-compatible
chat completion API. Mock external data are generated only as stand-ins for
carbon-factor tables and manufacturing documents when real files are not yet
available.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_RAG_VARIANTS = ["rag", "graph_rag", "agentic_rag"]


def normalize_text(text: Any) -> str:
    return str(text or "").casefold().strip()


def split_tokens(text: Any) -> List[str]:
    return [t for t in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", str(text or "")) if t]


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def node_labels(node: Dict[str, Any]) -> List[str]:
    return list(node.get("labels", []) or [])


def props(node: Dict[str, Any]) -> Dict[str, Any]:
    return dict(node.get("props", {}) or {})


@dataclass
class ComponentContext:
    component: Dict[str, Any]
    ifc_element: Optional[Dict[str, Any]]
    materials: List[Dict[str, Any]]
    quantity_basis: Optional[Dict[str, Any]]
    quantities: List[Dict[str, Any]]
    process_target: Optional[Dict[str, Any]]
    carbon_targets: List[Dict[str, Any]]

    @property
    def global_id(self) -> str:
        return str(props(self.component).get("ifcGlobalId", "") or self.component.get("id", ""))

    @property
    def ifc_type(self) -> str:
        c_props = props(self.component)
        if c_props.get("ifcType"):
            return str(c_props["ifcType"])
        if self.ifc_element:
            return str(props(self.ifc_element).get("ifcType", ""))
        return ""

    @property
    def name(self) -> str:
        c_props = props(self.component)
        return str(c_props.get("name", "") or self.global_id)

    @property
    def material_text(self) -> str:
        texts = [str(props(m).get("name", "") or "") for m in self.materials]
        texts = [t for t in texts if t]
        if texts:
            return " | ".join(texts)
        return str(props(self.component).get("materialText", "") or "")


@dataclass
class FactorRecord:
    row_id: str
    material_name: str
    keywords: List[str]
    factor_value: float
    factor_unit: str
    density_kg_m3: Optional[float]
    source_file: str
    source_note: str = ""


@dataclass
class ProcessDoc:
    doc_id: str
    title: str
    component_types: List[str]
    keywords: List[str]
    text: str
    activities: List[str]
    source_file: str


@dataclass
class RetrievalBundle:
    graph_context: Optional[ComponentContext] = None
    factor_candidates: List[Tuple[FactorRecord, float, List[str]]] = field(default_factory=list)
    process_candidates: List[Tuple[ProcessDoc, float, List[str]]] = field(default_factory=list)


@dataclass
class CalculationResult:
    status: str
    value: Optional[float]
    unit: str
    formula: str
    quantity_name: str
    quantity_value: Optional[float]
    quantity_unit: str
    factor_row_id: str
    factor_value: Optional[float]
    factor_unit: str
    density_used: Optional[float]
    issue: str = ""


class BackboneGraphStore:
    def __init__(self, graph_json_path: Path):
        self.path = graph_json_path
        payload = json.loads(graph_json_path.read_text(encoding="utf-8"))
        self.metadata = payload.get("metadata", {})
        self.nodes: Dict[str, Dict[str, Any]] = {n["id"]: n for n in payload.get("nodes", [])}
        self.edges: List[Dict[str, Any]] = list(payload.get("edges", []) or [])
        self.out_edges: Dict[str, List[Dict[str, Any]]] = {}
        self.in_edges: Dict[str, List[Dict[str, Any]]] = {}
        for edge in self.edges:
            self.out_edges.setdefault(edge.get("src", ""), []).append(edge)
            self.in_edges.setdefault(edge.get("tgt", ""), []).append(edge)

    def nodes_by_label(self, label: str) -> List[Dict[str, Any]]:
        return [node for node in self.nodes.values() if label in node_labels(node)]

    def targets(self, src: str, rel_type: str) -> List[Dict[str, Any]]:
        rows = []
        for edge in self.out_edges.get(src, []):
            if edge.get("type") == rel_type and edge.get("tgt") in self.nodes:
                rows.append(self.nodes[edge["tgt"]])
        return rows

    def sources(self, tgt: str, rel_type: str) -> List[Dict[str, Any]]:
        rows = []
        for edge in self.in_edges.get(tgt, []):
            if edge.get("type") == rel_type and edge.get("src") in self.nodes:
                rows.append(self.nodes[edge["src"]])
        return rows

    def component_contexts(self) -> List[ComponentContext]:
        contexts: List[ComponentContext] = []
        for component in self.nodes_by_label("BuildingComponent"):
            component_id = component["id"]
            ifc_sources = self.sources(component_id, "ALIGNED_AS")
            ifc_element = ifc_sources[0] if ifc_sources else None
            element_id = ifc_element["id"] if ifc_element else ""

            materials = self.targets(element_id, "hasMaterial") if element_id else []
            quantity_basis = None
            qb_nodes = self.targets(component_id, "HAS_QUANTITY_BASIS")
            if qb_nodes:
                quantity_basis = qb_nodes[0]

            quantities = []
            if quantity_basis:
                quantities = self.targets(quantity_basis["id"], "GROUNDED_IN_QUANTITY")
            if not quantities and element_id:
                quantities = self.targets(element_id, "HAS_QUANTITY_SET")

            process_target = None
            pt_nodes = self.targets(component_id, "HAS_PROCESS_ASSESSMENT_TARGET")
            if pt_nodes:
                process_target = pt_nodes[0]

            carbon_targets = self.targets(component_id, "HAS_CARBON_ASSESSMENT_TARGET")
            contexts.append(
                ComponentContext(
                    component=component,
                    ifc_element=ifc_element,
                    materials=materials,
                    quantity_basis=quantity_basis,
                    quantities=quantities,
                    process_target=process_target,
                    carbon_targets=carbon_targets,
                )
            )
        return contexts


class MockExternalDataBuilder:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir

    def ensure(self) -> Tuple[Path, Path]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        factor_path = self.out_dir / "mock_carbon_factors.csv"
        process_path = self.out_dir / "mock_process_docs.jsonl"
        if not factor_path.exists():
            self._write_factor_csv(factor_path)
        if not process_path.exists():
            self._write_process_jsonl(process_path)
        return factor_path, process_path

    def _write_factor_csv(self, path: Path) -> None:
        rows = [
            {
                "row_id": "metal_generic",
                "material_name": "generic structural metal",
                "keywords": "metal;structural metal;\u91d1\u5c5e",
                "factor_value": "2.40",
                "factor_unit": "kgCO2e/kg",
                "density_kg_m3": "7800",
                "source_note": "mock generic metal factor",
            },
            {
                "row_id": "steel_s355_a1_a3",
                "material_name": "S355 structural steel",
                "keywords": "steel;S355;355;A1;\u94a2;\u91d1\u5c5e",
                "factor_value": "1.85",
                "factor_unit": "kgCO2e/kg",
                "density_kg_m3": "7850",
                "source_note": "mock A1-A3 steel factor",
            },
            {
                "row_id": "concrete_generic_a1_a3",
                "material_name": "ready-mix concrete",
                "keywords": "concrete;C30;C40;\u6df7\u51dd\u571f",
                "factor_value": "310",
                "factor_unit": "kgCO2e/m3",
                "density_kg_m3": "2400",
                "source_note": "mock concrete factor",
            },
            {
                "row_id": "gypsum_board_a1_a3",
                "material_name": "gypsum board",
                "keywords": "gypsum;plasterboard;board;\u77f3\u818f\u677f",
                "factor_value": "8.5",
                "factor_unit": "kgCO2e/m2",
                "density_kg_m3": "",
                "source_note": "mock gypsum board factor",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _write_process_jsonl(self, path: Path) -> None:
        docs = [
            {
                "doc_id": "process_steel_column",
                "title": "Steel column modular fabrication procedure",
                "component_types": ["IfcColumn", "IfcBeam"],
                "keywords": ["steel", "S355", "column", "beam", "\u94a2", "\u91d1\u5c5e"],
                "activities": ["cutting", "drilling", "welding", "surface treatment", "factory inspection"],
                "text": (
                    "Structural steel members for modular units are fabricated by cutting stock profiles, "
                    "drilling connection holes, welding plates or brackets, applying surface treatment, "
                    "and completing dimensional inspection before dispatch."
                ),
                "source_file": "mock_manufacturing_processes.pdf",
            },
            {
                "doc_id": "process_wall_panel",
                "title": "Lightweight wall panel assembly procedure",
                "component_types": ["IfcWall", "IfcWallStandardCase"],
                "keywords": ["wall", "panel", "gypsum", "insulation", "board"],
                "activities": ["frame preparation", "board fixing", "insulation placement", "joint sealing"],
                "text": (
                    "Wall panels are assembled by preparing the frame, fixing boards, placing insulation, "
                    "and sealing joints before quality inspection."
                ),
                "source_file": "mock_manufacturing_processes.pdf",
            },
            {
                "doc_id": "process_precast_slab",
                "title": "Precast slab manufacturing procedure",
                "component_types": ["IfcSlab"],
                "keywords": ["slab", "concrete", "precast", "\u6df7\u51dd\u571f"],
                "activities": ["mould preparation", "reinforcement placement", "concrete casting", "curing"],
                "text": (
                    "Precast slabs are manufactured through mould preparation, reinforcement placement, "
                    "concrete casting, curing, and demoulding."
                ),
                "source_file": "mock_manufacturing_processes.pdf",
            },
        ]
        with path.open("w", encoding="utf-8") as f:
            for row in docs:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


class CarbonFactorTable:
    def __init__(self, factor_path: Path):
        self.path = factor_path
        self.records = self._load(factor_path)

    def _load(self, path: Path) -> List[FactorRecord]:
        suffix = path.suffix.casefold()
        if suffix in {".csv", ".txt"}:
            return self._load_csv(path)
        if suffix in {".xlsx", ".xls"}:
            return self._load_excel(path)
        raise ValueError(f"Unsupported factor table format: {path}")

    def _load_csv(self, path: Path) -> List[FactorRecord]:
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            return [self._row_to_record(row, path.name) for row in csv.DictReader(f)]

    def _load_excel(self, path: Path) -> List[FactorRecord]:
        try:
            import pandas as pd  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Reading Excel requires pandas/openpyxl, or provide a CSV factor table.") from exc
        frame = pd.read_excel(path)
        return [self._row_to_record({k: row[k] for k in frame.columns}, path.name) for _, row in frame.iterrows()]

    def _row_to_record(self, row: Dict[str, Any], source_file: str) -> FactorRecord:
        keywords = []
        for part in str(row.get("keywords", "") or "").split(";"):
            part = part.strip()
            if part:
                keywords.append(part)
        return FactorRecord(
            row_id=str(row.get("row_id", "") or row.get("id", "") or row.get("material_name", "")),
            material_name=str(row.get("material_name", "") or row.get("material", "")),
            keywords=keywords,
            factor_value=float(safe_float(row.get("factor_value"), 0.0) or 0.0),
            factor_unit=str(row.get("factor_unit", "") or "kgCO2e/kg"),
            density_kg_m3=safe_float(row.get("density_kg_m3"), None),
            source_file=source_file,
            source_note=str(row.get("source_note", "") or ""),
        )

    def retrieve(self, material_text: str, lookup_keys: Sequence[str], top_k: int = 3) -> List[Tuple[FactorRecord, float, List[str]]]:
        query_terms = [normalize_text(t) for t in list(lookup_keys) + split_tokens(material_text)]
        query_terms = [t for t in dict.fromkeys(query_terms) if t]
        ranked = []
        for record in self.records:
            record_terms = [normalize_text(record.material_name)] + [normalize_text(k) for k in record.keywords]
            record_terms = [t for t in record_terms if t]
            matched = []
            for q in query_terms:
                for term in record_terms:
                    if q and (q == term or q in term or term in q):
                        matched.append(q)
                        break
            score = len(set(matched)) / max(1, len(set(query_terms)))
            if matched:
                ranked.append((record, round(score, 4), sorted(set(matched))))
        ranked.sort(key=lambda x: (x[1], len(x[2])), reverse=True)
        return ranked[:top_k]


class ProcessCorpus:
    def __init__(self, docs_path: Path):
        self.path = docs_path
        self.docs = self._load(docs_path)

    def _load(self, path: Path) -> List[ProcessDoc]:
        suffix = path.suffix.casefold()
        if suffix == ".jsonl":
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            return [self._row_to_doc(row, path.name) for row in rows]
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            return [
                ProcessDoc(
                    doc_id=path.stem,
                    title=path.stem,
                    component_types=[],
                    keywords=split_tokens(text)[:30],
                    text=text,
                    activities=[],
                    source_file=path.name,
                )
            ]
        if suffix == ".pdf":
            return [self._load_pdf(path)]
        raise ValueError(f"Unsupported process document format: {path}")

    def _load_pdf(self, path: Path) -> ProcessDoc:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Reading PDFs requires pypdf, or provide JSONL/TXT process documents.") from exc
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return ProcessDoc(
            doc_id=path.stem,
            title=path.stem,
            component_types=[],
            keywords=split_tokens(text)[:50],
            text=text,
            activities=[],
            source_file=path.name,
        )

    def _row_to_doc(self, row: Dict[str, Any], source_file: str) -> ProcessDoc:
        return ProcessDoc(
            doc_id=str(row.get("doc_id", "") or row.get("id", "") or row.get("title", "")),
            title=str(row.get("title", "")),
            component_types=list(row.get("component_types", []) or []),
            keywords=list(row.get("keywords", []) or []),
            text=str(row.get("text", "")),
            activities=list(row.get("activities", []) or []),
            source_file=str(row.get("source_file", "") or source_file),
        )

    def retrieve(self, context: ComponentContext, top_k: int = 3) -> List[Tuple[ProcessDoc, float, List[str]]]:
        pt_props = props(context.process_target) if context.process_target else {}
        hints = list(pt_props.get("genericRetrievalHints", []) or [])
        query_terms = [context.ifc_type, context.name, context.material_text] + hints
        query_tokens = [normalize_text(t) for item in query_terms for t in split_tokens(item)]
        query_tokens = [t for t in dict.fromkeys(query_tokens) if t]
        ranked = []
        for doc in self.docs:
            doc_terms = [normalize_text(t) for t in doc.component_types + doc.keywords + split_tokens(doc.title) + split_tokens(doc.text)]
            doc_terms = [t for t in doc_terms if t]
            matched = []
            for q in query_tokens:
                for term in doc_terms:
                    if q and (q == term or q in term or term in q):
                        matched.append(q)
                        break
            score = len(set(matched)) / max(1, len(set(query_tokens)))
            if context.ifc_type in doc.component_types:
                score += 0.35
                matched.append(context.ifc_type)
            if matched:
                ranked.append((doc, round(score, 4), sorted(set(matched))))
        ranked.sort(key=lambda x: (x[1], len(x[2])), reverse=True)
        return ranked[:top_k]


class DeterministicCarbonCalculator:
    def calculate(
        self,
        context: ComponentContext,
        factor: Optional[FactorRecord],
    ) -> CalculationResult:
        if factor is None:
            return CalculationResult(
                status="incomplete",
                value=None,
                unit="kgCO2e",
                formula="",
                quantity_name="",
                quantity_value=None,
                quantity_unit="",
                factor_row_id="",
                factor_value=None,
                factor_unit="",
                density_used=None,
                issue="missing emission factor",
            )

        q_by_name = {str(props(q).get("quantityName", "")): q for q in context.quantities}
        qb_props = props(context.quantity_basis) if context.quantity_basis else {}
        unit = normalize_text(factor.factor_unit)

        if "kgco2e/kg" in unit:
            for q_name in qb_props.get("preferredForMassFactor", []) or []:
                q_node = q_by_name.get(str(q_name))
                if not q_node:
                    continue
                q_props = props(q_node)
                value = safe_float(q_props.get("quantityValue"), None)
                q_unit = normalize_text(q_props.get("quantityUnit", ""))
                if value is None:
                    continue
                if "kg" in q_unit or "weight" in normalize_text(q_name):
                    kg_value = value
                    formula = f"{q_name} * factor"
                    result = kg_value * factor.factor_value
                    return self._complete(result, formula, q_name, value, q_props, factor, None)
                if ("m3" in q_unit or "volume" in normalize_text(q_name)) and factor.density_kg_m3:
                    kg_value = value * factor.density_kg_m3
                    formula = f"{q_name} * density * factor"
                    result = kg_value * factor.factor_value
                    return self._complete(result, formula, q_name, value, q_props, factor, factor.density_kg_m3)
            return self._issue(context, factor, "mass factor requires weight or volume plus density")

        if "kgco2e/m3" in unit:
            return self._calculate_by_quantity_family(context, factor, "preferredForVolumeFactor", "volume quantity required")

        if "kgco2e/m2" in unit:
            return self._calculate_by_quantity_family(context, factor, "preferredForAreaFactor", "area quantity required")

        if "kgco2e/unit" in unit or "kgco2e/item" in unit:
            return self._calculate_by_quantity_family(context, factor, "preferredForUnitFactor", "count quantity required")

        return self._issue(context, factor, f"unsupported factor unit: {factor.factor_unit}")

    def _calculate_by_quantity_family(
        self,
        context: ComponentContext,
        factor: FactorRecord,
        family_key: str,
        issue: str,
    ) -> CalculationResult:
        q_by_name = {str(props(q).get("quantityName", "")): q for q in context.quantities}
        qb_props = props(context.quantity_basis) if context.quantity_basis else {}
        for q_name in qb_props.get(family_key, []) or []:
            q_node = q_by_name.get(str(q_name))
            if not q_node:
                continue
            q_props = props(q_node)
            value = safe_float(q_props.get("quantityValue"), None)
            if value is None:
                continue
            result = value * factor.factor_value
            return self._complete(result, f"{q_name} * factor", q_name, value, q_props, factor, None)
        return self._issue(context, factor, issue)

    def _complete(
        self,
        result: float,
        formula: str,
        quantity_name: str,
        quantity_value: float,
        q_props: Dict[str, Any],
        factor: FactorRecord,
        density_used: Optional[float],
    ) -> CalculationResult:
        return CalculationResult(
            status="complete",
            value=round(result, 4),
            unit="kgCO2e",
            formula=formula,
            quantity_name=quantity_name,
            quantity_value=quantity_value,
            quantity_unit=str(q_props.get("quantityUnit", "")),
            factor_row_id=factor.row_id,
            factor_value=factor.factor_value,
            factor_unit=factor.factor_unit,
            density_used=density_used,
        )

    def _issue(self, context: ComponentContext, factor: FactorRecord, issue: str) -> CalculationResult:
        return CalculationResult(
            status="incomplete",
            value=None,
            unit="kgCO2e",
            formula="",
            quantity_name="",
            quantity_value=None,
            quantity_unit="",
            factor_row_id=factor.row_id,
            factor_value=factor.factor_value,
            factor_unit=factor.factor_unit,
            density_used=None,
            issue=issue,
        )


class OpenAICompatibleChatClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.0,
        timeout: int = 60,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"LLM API request failed ({exc.code}): {detail}") from exc
        return str(data["choices"][0]["message"]["content"])


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(text or "")).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def factor_to_dict(row: Tuple[FactorRecord, float, List[str]]) -> Dict[str, Any]:
    record, score, matches = row
    return {
        "rowId": record.row_id,
        "materialName": record.material_name,
        "keywords": record.keywords,
        "factorValue": record.factor_value,
        "factorUnit": record.factor_unit,
        "densityKgM3": record.density_kg_m3,
        "sourceFile": record.source_file,
        "sourceNote": record.source_note,
        "retrievalScore": score,
        "matchedTerms": matches,
    }


def process_to_dict(row: Tuple[ProcessDoc, float, List[str]]) -> Dict[str, Any]:
    doc, score, matches = row
    return {
        "docId": doc.doc_id,
        "title": doc.title,
        "componentTypes": doc.component_types,
        "activities": doc.activities,
        "text": doc.text,
        "sourceFile": doc.source_file,
        "retrievalScore": score,
        "matchedTerms": matches,
    }


def quantity_to_dict(node: Dict[str, Any]) -> Dict[str, Any]:
    q_props = props(node)
    return {
        "quantityName": q_props.get("quantityName", ""),
        "quantityValue": q_props.get("quantityValue", None),
        "quantityUnit": q_props.get("quantityUnit", ""),
        "unitInterpretationStatus": q_props.get("unitInterpretationStatus", ""),
        "quantityUnitSource": q_props.get("quantityUnitSource", ""),
    }


def component_to_brief(context: ComponentContext) -> Dict[str, Any]:
    qb_props = props(context.quantity_basis) if context.quantity_basis else {}
    return {
        "componentGlobalId": context.global_id,
        "ifcType": context.ifc_type,
        "componentName": context.name,
        "materialText": context.material_text,
        "quantityBasis": {
            "quantityReadiness": qb_props.get("quantityReadiness", ""),
            "preferredForMassFactor": qb_props.get("preferredForMassFactor", []),
            "preferredForVolumeFactor": qb_props.get("preferredForVolumeFactor", []),
            "preferredForAreaFactor": qb_props.get("preferredForAreaFactor", []),
            "preferredForUnitFactor": qb_props.get("preferredForUnitFactor", []),
            "unitInterpretationStatus": qb_props.get("unitInterpretationStatus", ""),
        },
        "quantities": [quantity_to_dict(q) for q in context.quantities],
    }


class APIRagComparisonBenchmark:
    def __init__(
        self,
        graph_store: BackboneGraphStore,
        factor_table: CarbonFactorTable,
        process_corpus: ProcessCorpus,
        rag_variants: Sequence[str],
        model_names: Sequence[str],
        api_key: str,
        api_base_url: str,
        temperature: float,
        timeout: int,
    ):
        self.graph_store = graph_store
        self.factor_table = factor_table
        self.process_corpus = process_corpus
        self.rag_variants = list(rag_variants)
        self.model_names = list(model_names)
        self.api_key = api_key
        self.api_base_url = api_base_url
        self.temperature = temperature
        self.timeout = timeout
        self.calculator = DeterministicCarbonCalculator()

    def run(self, question: str) -> Dict[str, Any]:
        contexts = self.graph_store.component_contexts()
        results = []
        for model_name in self.model_names:
            client = OpenAICompatibleChatClient(
                api_key=self.api_key,
                model=model_name,
                base_url=self.api_base_url,
                temperature=self.temperature,
                timeout=self.timeout,
            )
            for variant in self.rag_variants:
                for context in contexts:
                    start = time.time()
                    bundle = self._retrieve(variant, context)
                    factor_decision = self._select_factor_with_api(client, variant, question, context, bundle)
                    selected_factor = self._factor_by_id(bundle.factor_candidates, str(factor_decision.get("selectedFactorRowId", "")))
                    calc = self._calculate_if_agentic(variant, context, selected_factor)
                    answer = self._answer_with_api(client, variant, question, context, bundle, factor_decision, selected_factor, calc)
                    elapsed_ms = round((time.time() - start) * 1000, 3)
                    results.append(
                        self._result_row(
                            question=question,
                            variant=variant,
                            model_name=model_name,
                            context=context,
                            bundle=bundle,
                            factor_decision=factor_decision,
                            selected_factor=selected_factor,
                            calc=calc,
                            answer=answer,
                            elapsed_ms=elapsed_ms,
                        )
                    )
        return {"question": question, "results": results, "summary": self._summarize(results)}

    def _retrieve(self, variant: str, context: ComponentContext) -> RetrievalBundle:
        if variant not in {"rag", "graph_rag", "agentic_rag"}:
            raise ValueError(f"Unknown RAG variant: {variant}")
        bundle = RetrievalBundle(graph_context=context if variant in {"graph_rag", "agentic_rag"} else None)
        if variant == "rag":
            lookup = split_tokens(" ".join([context.material_text, context.ifc_type, context.name]))
            bundle.factor_candidates = self.factor_table.retrieve(context.material_text, lookup)
            bundle.process_candidates = self.process_corpus.retrieve(context)
            return bundle
        bundle.factor_candidates = self._retrieve_factors_from_graph_targets(context)
        bundle.process_candidates = self.process_corpus.retrieve(context)
        return bundle

    def _retrieve_factors_from_graph_targets(self, context: ComponentContext) -> List[Tuple[FactorRecord, float, List[str]]]:
        candidates: List[Tuple[FactorRecord, float, List[str]]] = []
        if not context.carbon_targets:
            return self.factor_table.retrieve(context.material_text, split_tokens(context.material_text))
        seen = set()
        for target in context.carbon_targets:
            t_props = props(target)
            rows = self.factor_table.retrieve(
                material_text=str(t_props.get("materialText", "") or context.material_text),
                lookup_keys=list(t_props.get("factorLookupKeys", []) or []),
            )
            for record, score, matches in rows:
                key = record.row_id
                if key not in seen:
                    seen.add(key)
                    candidates.append((record, score, matches))
        candidates.sort(key=lambda x: (x[1], len(x[2])), reverse=True)
        return candidates[:5]

    def _select_factor_with_api(
        self,
        client: OpenAICompatibleChatClient,
        variant: str,
        question: str,
        context: ComponentContext,
        bundle: RetrievalBundle,
    ) -> Dict[str, Any]:
        if not bundle.factor_candidates:
            return {"selectedFactorRowId": "", "confidence": 0.0, "rationale": "No factor candidates were retrieved."}
        system = (
            "You select the most appropriate carbon emission factor for a modular-construction component. "
            "Return only JSON with keys selectedFactorRowId, confidence, and rationale. "
            "Do not invent row IDs; choose from the candidate list or return an empty row ID."
        )
        user = json.dumps(
            {
                "ragVariant": variant,
                "question": question,
                "component": component_to_brief(context) if variant in {"graph_rag", "agentic_rag"} else {
                    "ifcType": context.ifc_type,
                    "componentName": context.name,
                    "materialText": context.material_text,
                },
                "factorCandidates": [factor_to_dict(row) for row in bundle.factor_candidates],
            },
            ensure_ascii=False,
            indent=2,
        )
        data = extract_json_object(client.complete(system, user))
        row_id = str(data.get("selectedFactorRowId", "") or "")
        if row_id and self._factor_by_id(bundle.factor_candidates, row_id) is None:
            row_id = ""
        return {
            "selectedFactorRowId": row_id,
            "confidence": safe_float(data.get("confidence"), 0.0),
            "rationale": str(data.get("rationale", "")),
        }

    @staticmethod
    def _factor_by_id(
        candidates: List[Tuple[FactorRecord, float, List[str]]],
        row_id: str,
    ) -> Optional[FactorRecord]:
        for record, _, _ in candidates:
            if record.row_id == row_id:
                return record
        return None

    def _calculate_if_agentic(
        self,
        variant: str,
        context: ComponentContext,
        selected_factor: Optional[FactorRecord],
    ) -> CalculationResult:
        if variant != "agentic_rag":
            return CalculationResult(
                status="not_executed",
                value=None,
                unit="kgCO2e",
                formula="",
                quantity_name="",
                quantity_value=None,
                quantity_unit="",
                factor_row_id=selected_factor.row_id if selected_factor else "",
                factor_value=selected_factor.factor_value if selected_factor else None,
                factor_unit=selected_factor.factor_unit if selected_factor else "",
                density_used=None,
                issue="deterministic calculator is reserved for agentic_rag",
            )
        return self.calculator.calculate(context, selected_factor)

    def _answer_with_api(
        self,
        client: OpenAICompatibleChatClient,
        variant: str,
        question: str,
        context: ComponentContext,
        bundle: RetrievalBundle,
        factor_decision: Dict[str, Any],
        selected_factor: Optional[FactorRecord],
        calc: CalculationResult,
    ) -> str:
        system = (
            "You answer manufacturing-carbon questions using only the provided evidence. "
            "State whether the answer comes from standard RAG, Graph RAG, or Agentic RAG. "
            "For agentic RAG, cite the deterministic calculation record. Be concise."
        )
        user = json.dumps(
            {
                "ragVariant": variant,
                "question": question,
                "component": component_to_brief(context) if variant in {"graph_rag", "agentic_rag"} else {
                    "componentGlobalId": context.global_id,
                    "ifcType": context.ifc_type,
                    "componentName": context.name,
                    "materialText": context.material_text,
                },
                "factorDecision": factor_decision,
                "selectedFactor": factor_to_dict((selected_factor, 1.0, [])) if selected_factor else None,
                "processEvidence": [process_to_dict(row) for row in bundle.process_candidates[:2]],
                "calculationRecord": calc.__dict__,
            },
            ensure_ascii=False,
            indent=2,
        )
        return client.complete(system, user).strip()

    def _result_row(
        self,
        question: str,
        variant: str,
        model_name: str,
        context: ComponentContext,
        bundle: RetrievalBundle,
        factor_decision: Dict[str, Any],
        selected_factor: Optional[FactorRecord],
        calc: CalculationResult,
        answer: str,
        elapsed_ms: float,
    ) -> Dict[str, Any]:
        process_doc = bundle.process_candidates[0][0] if bundle.process_candidates else None
        selected_factor_id = selected_factor.row_id if selected_factor else ""
        expected_factor = self._expected_factor_id(context)
        factor_correct = bool(selected_factor_id and expected_factor and selected_factor_id == expected_factor)
        return {
            "question": question,
            "ragVariant": variant,
            "apiModel": model_name,
            "componentGlobalId": context.global_id,
            "ifcType": context.ifc_type,
            "componentName": context.name,
            "materialText": context.material_text,
            "graphContextUsed": bundle.graph_context is not None,
            "factorRetrieved": bool(bundle.factor_candidates),
            "selectedFactorId": selected_factor_id,
            "expectedFactorId": expected_factor,
            "factorCorrect": factor_correct,
            "factorSelectionConfidence": factor_decision.get("confidence", 0.0),
            "factorSelectionRationale": factor_decision.get("rationale", ""),
            "processRetrieved": process_doc is not None,
            "processDocId": process_doc.doc_id if process_doc else "",
            "toolCalculationUsed": variant == "agentic_rag",
            "calculationStatus": calc.status,
            "carbonValue": calc.value,
            "carbonUnit": calc.unit,
            "formula": calc.formula,
            "quantityName": calc.quantity_name,
            "quantityValue": calc.quantity_value,
            "quantityUnit": calc.quantity_unit,
            "densityUsed": calc.density_used,
            "issue": calc.issue,
            "answer": answer,
            "latencyMs": elapsed_ms,
        }

    def _expected_factor_id(self, context: ComponentContext) -> str:
        text = normalize_text(context.material_text)
        if any(token in text for token in ["steel", "s355", "355", "\u94a2"]):
            return "steel_s355_a1_a3"
        if "concrete" in text or "\u6df7\u51dd\u571f" in text:
            return "concrete_generic_a1_a3"
        if "gypsum" in text or "board" in text or "\u77f3\u818f" in text:
            return "gypsum_board_a1_a3"
        return ""

    def _summarize(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault((row["ragVariant"], row["apiModel"]), []).append(row)
        out = []
        for (variant, model), group in sorted(grouped.items()):
            n = len(group)
            complete = sum(1 for r in group if r["calculationStatus"] == "complete")
            factor_found = sum(1 for r in group if r["factorRetrieved"])
            process_found = sum(1 for r in group if r["processRetrieved"])
            factor_correct = sum(1 for r in group if r["factorCorrect"])
            graph_context = sum(1 for r in group if r["graphContextUsed"])
            tool_used = sum(1 for r in group if r["toolCalculationUsed"])
            avg_latency = sum(float(r["latencyMs"]) for r in group) / max(1, n)
            out.append(
                {
                    "ragVariant": variant,
                    "apiModel": model,
                    "cases": n,
                    "graphContextRate": round(graph_context / max(1, n), 4),
                    "factorRetrievalRate": round(factor_found / max(1, n), 4),
                    "processRetrievalRate": round(process_found / max(1, n), 4),
                    "factorAccuracy": round(factor_correct / max(1, n), 4),
                    "toolUseRate": round(tool_used / max(1, n), 4),
                    "calculationCompletionRate": round(complete / max(1, n), 4),
                    "averageLatencyMs": round(avg_latency, 4),
                }
            )
        return {"byVariantAndModel": out}


class ExperimentExporter:
    @staticmethod
    def write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def write_markdown(path: Path, payload: Dict[str, Any]) -> None:
        lines = [
            "# DM2C API-based RAG Comparison",
            "",
            f"Question: {payload.get('question', '')}",
            "",
            "| RAG variant | API model | Graph context | Factor retrieval | Process retrieval | Factor accuracy | Tool use | Calculation completion | Avg latency (ms) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in payload.get("summary", {}).get("byVariantAndModel", []):
            lines.append(
                "| {ragVariant} | {apiModel} | {graphContextRate} | {factorRetrievalRate} | {processRetrievalRate} | "
                "{factorAccuracy} | {toolUseRate} | {calculationCompletionRate} | {averageLatencyMs} |".format(**row)
            )
        lines.extend(
            [
                "",
                "The comparison uses an OpenAI-compatible chat completion API for factor selection and answer synthesis. Mock external data are used only when real carbon-factor tables or manufacturing documents are not provided.",
            ]
        )
        path.write_text("\n".join(lines), encoding="utf-8")


def parse_csv_arg(value: str) -> List[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DM2C Section 3.3 API-based RAG comparison.")
    parser.add_argument("--graph", default="outputs/dm2c_backbone_graph.json", help="Backbone graph JSON from Section 3.2.")
    parser.add_argument("--factor-table", default="", help="Carbon factor CSV/XLSX. Mock CSV is generated if omitted.")
    parser.add_argument("--process-docs", default="", help="Manufacturing process JSONL/TXT/PDF. Mock JSONL is generated if omitted.")
    parser.add_argument("--out-dir", default="outputs/rag_experiments", help="Output directory for benchmark artifacts.")
    parser.add_argument("--rag-variants", default=",".join(DEFAULT_RAG_VARIANTS), help="Comma-separated RAG variants.")
    parser.add_argument("--models", default=os.environ.get("OPENAI_MODEL", ""), help="Comma-separated API model names.")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable that stores the API key.")
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--question",
        default="Calculate the manufacturing-stage A1-A3 carbon emission for each IFC-derived component with provenance.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get(args.api_key_env, "")
    model_names = parse_csv_arg(args.models)
    if not api_key:
        raise SystemExit(f"Missing API key. Set {args.api_key_env} before running the API-based benchmark.")
    if not model_names:
        raise SystemExit("Missing API model. Pass --models or set OPENAI_MODEL.")

    mock_builder = MockExternalDataBuilder(out_dir)
    mock_factor_path, mock_process_path = mock_builder.ensure()

    graph_path = Path(args.graph)
    factor_path = Path(args.factor_table) if args.factor_table else mock_factor_path
    process_path = Path(args.process_docs) if args.process_docs else mock_process_path

    graph_store = BackboneGraphStore(graph_path)
    factor_table = CarbonFactorTable(factor_path)
    process_corpus = ProcessCorpus(process_path)
    benchmark = APIRagComparisonBenchmark(
        graph_store=graph_store,
        factor_table=factor_table,
        process_corpus=process_corpus,
        rag_variants=parse_csv_arg(args.rag_variants),
        model_names=model_names,
        api_key=api_key,
        api_base_url=args.api_base_url,
        temperature=args.temperature,
        timeout=args.timeout,
    )

    payload = benchmark.run(args.question)
    payload["inputs"] = {
        "graph": str(graph_path),
        "factorTable": str(factor_path),
        "processDocs": str(process_path),
        "ragVariants": parse_csv_arg(args.rag_variants),
        "apiModels": model_names,
        "apiBaseUrl": args.api_base_url,
    }

    ExperimentExporter.write_json(out_dir / "agentic_rag_results.json", payload)
    ExperimentExporter.write_json(out_dir / "agentic_rag_summary.json", payload["summary"])
    ExperimentExporter.write_csv(out_dir / "agentic_rag_results.csv", payload["results"])
    ExperimentExporter.write_markdown(out_dir / "agentic_rag_report.md", payload)

    print("DM2C API-based RAG comparison completed.")
    print(f"  Graph: {graph_path}")
    print(f"  Factor table: {factor_path}")
    print(f"  Process docs: {process_path}")
    print(f"  API base URL: {args.api_base_url}")
    print(f"  API models: {', '.join(model_names)}")
    print(f"  Output directory: {out_dir}")
    print("Generated files:")
    for name in [
        "mock_carbon_factors.csv",
        "mock_process_docs.jsonl",
        "agentic_rag_results.json",
        "agentic_rag_results.csv",
        "agentic_rag_summary.json",
        "agentic_rag_report.md",
    ]:
        path = out_dir / name
        if path.exists():
            print(f"  - {path}")


if __name__ == "__main__":
    main()
