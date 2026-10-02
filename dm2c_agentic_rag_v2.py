"""
DM2C Agentic RAG v2: material factors + ITP process steps + energy factors.

This version keeps the Section 3.2 backbone graph as the grounding layer, but
separates three evidence roles:

1. Material embodied-carbon factors from the workbook material sheet.
2. Energy/fuel emission factors from the workbook energy sheet.
3. Module production / ITP process evidence from JSONL process documents.

The default run is local and deterministic. It does not call an external LLM API.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dm2c_agentic_rag import (
    BackboneGraphStore,
    ComponentContext,
    normalize_text,
    props,
    safe_float,
    split_tokens,
)


MATERIAL_SHEET = "Embodied Carbon Coefficients"
ENERGY_SHEET = "Energy Emission Factors"


DENSITY_BY_CATEGORY_KG_M3 = {
    "Steel": 7850.0,
    "Aluminium": 2700.0,
    "Aluminium Profile": 2700.0,
    "Concrete": 2400.0,
    "Glass": 2500.0,
    "Lead": 11340.0,
    "Zinc": 7140.0,
    "PVC": 1380.0,
    "Stone": 2600.0,
    "Cement": 1440.0,
    "Sand": 1600.0,
    "Timber": 600.0,
    "Calcium Silicate": 900.0,
    "Fibre Cement": 1500.0,
}


MATERIAL_SYNONYMS = {
    "Steel": ["steel", "\u94a2", "\u91d1\u5c5e", "metal", "s355", "section"],
    "Aluminium": ["aluminium", "aluminum", "\u94dd", "\u91d1\u5c5e", "metal"],
    "Aluminium Profile": ["aluminium", "aluminum", "extrusion", "window", "\u94dd", "\u94dd\u578b\u6750"],
    "Concrete": ["concrete", "\u6df7\u51dd\u571f", "rc"],
    "Cement": ["cement", "\u6c34\u6ce5"],
    "Glass": ["glass", "\u73bb\u7483"],
    "Clay (incl. Bricks)": ["clay", "brick", "bricks", "\u7816", "\u9ecf\u571f"],
    "Timber": ["timber", "wood", "\u6728\u6750"],
    "PVC": ["pvc", "vinyl", "plastic", "\u5851\u6599"],
    "Paint": ["paint", "coating", "\u6cb9\u6f06"],
    "Coating": ["coating", "primer", "paint", "\u6d82\u5c42", "\u5bcc\u950c\u6f06"],
    "Zinc": ["zinc", "galvanizing", "\u9540\u950c", "\u950c"],
    "Rockwool": ["rockwool", "rock wool", "insulation", "\u5ca9\u68c9"],
}


STRUCTURAL_IFC_TYPES = {"ifccolumn", "ifcbeam", "ifcmember"}


def slug(text: Any) -> str:
    value = normalize_text(text)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value).strip("_")
    return value[:96] or "row"


def unique_list(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def token_set(values: Iterable[Any]) -> set[str]:
    terms: List[str] = []
    for value in values:
        terms.extend(split_tokens(value))
    return {normalize_text(t) for t in terms if normalize_text(t)}


def is_numeric_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", token))


def clean_cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.casefold() == "nan":
        return ""
    return text


@dataclass
class MaterialFactorRecord:
    row_id: str
    category: str
    subtype: str
    material_name: str
    keywords: List[str]
    factor_value: float
    factor_unit: str
    density_kg_m3: Optional[float]
    density_source: str
    source_file: str
    source_note: str


@dataclass
class EnergyFactorRecord:
    row_id: str
    category: str
    energy_type: str
    factor_value: Optional[float]
    factor_unit: str
    year: str
    source: str
    notes: str
    quality_flag: str
    source_file: str


@dataclass
class ProcessEvidence:
    doc_id: str
    title: str
    component_types: List[str]
    keywords: List[str]
    activities: List[str]
    text: str
    source_file: str
    source_type: str = ""
    sequence: Optional[int] = None


@dataclass
class RetrievalCandidate:
    record: Any
    score: float
    matched_terms: List[str]
    rationale: str


@dataclass
class MaterialCarbonResult:
    status: str
    value_kgco2e: Optional[float]
    formula: str
    quantity_name: str
    quantity_value: Optional[float]
    quantity_unit: str
    factor_row_id: str
    factor_value: Optional[float]
    factor_unit: str
    density_kg_m3: Optional[float]
    issue: str = ""


@dataclass
class ActivityDriver:
    process_doc_id: str
    process_title: str
    activity_tags: List[str]
    driver_kind: str
    required_quantity: str
    preferred_factor_row_id: str = ""
    preferred_factor_value: Optional[float] = None
    preferred_factor_unit: str = ""
    consumption_value: Optional[float] = None
    consumption_unit: str = ""
    carbon_value_kgco2e: Optional[float] = None
    status: str = "requires_runtime_quantity"
    note: str = ""


@dataclass
class ProcessCarbonResult:
    status: str
    value_kgco2e: Optional[float]
    complete_driver_count: int
    missing_driver_count: int
    drivers: List[ActivityDriver]
    issue: str = ""


class WorkbookFactorStore:
    def __init__(self, workbook_path: Path):
        self.path = workbook_path
        self.material_factors: List[MaterialFactorRecord] = []
        self.energy_factors: List[EnergyFactorRecord] = []
        self._load(workbook_path)

    def _load(self, workbook_path: Path) -> None:
        try:
            import pandas as pd  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Reading factor workbooks requires pandas/openpyxl.") from exc

        workbook = pd.ExcelFile(workbook_path)
        if MATERIAL_SHEET in workbook.sheet_names:
            material_frame = self._read_factor_frame(
                pd,
                workbook_path,
                MATERIAL_SHEET,
                [
                    ["material category", "factor"],
                    ["material_name", "factor_value"],
                ],
            )
            self.material_factors = self._load_material_factors(material_frame)
        else:
            first_sheet = workbook.sheet_names[0]
            material_frame = self._read_factor_frame(
                pd,
                workbook_path,
                first_sheet,
                [
                    ["material category", "factor"],
                    ["material_name", "factor_value"],
                ],
            )
            self.material_factors = self._load_material_factors(material_frame)

        if ENERGY_SHEET in workbook.sheet_names:
            energy_frame = self._read_factor_frame(
                pd,
                workbook_path,
                ENERGY_SHEET,
                [["category", "factor", "unit"]],
            )
            self.energy_factors = self._load_energy_factors(energy_frame)

    @staticmethod
    def _read_factor_frame(
        pd: Any,
        workbook_path: Path,
        sheet_name: str,
        marker_groups: Sequence[Sequence[str]],
    ) -> Any:
        raw = pd.read_excel(workbook_path, sheet_name=sheet_name, header=None)
        header_index: Optional[int] = None
        for index, row in raw.head(20).iterrows():
            cells = [normalize_text(clean_cell_text(value)) for value in row.tolist()]
            if any(
                all(any(normalize_text(marker) in cell for cell in cells) for marker in markers)
                for markers in marker_groups
            ):
                header_index = int(index)
                break
        if header_index is None:
            return pd.read_excel(workbook_path, sheet_name=sheet_name)

        frame = raw.iloc[header_index + 1 :].copy()
        frame.columns = [clean_cell_text(value) for value in raw.iloc[header_index].tolist()]
        frame = frame.reset_index(drop=True)
        frame.attrs["excel_header_row"] = header_index + 1
        return frame

    def _load_material_factors(self, frame: Any) -> List[MaterialFactorRecord]:
        if self._has_rag_material_schema(frame.columns):
            return self._load_rag_material_factors(frame)

        category_col = self._find_col(frame.columns, ["Material Category", "category"])
        subtype_col = self._find_col(frame.columns, ["Material Sub-type", "sub-type", "subtype"])
        factor_col = self._find_col_preferred(
            frame.columns,
            ["Selected A1", "ICE EC", "kg CO2e/kg", "Original Factor", "factor"],
        )
        unit_col = self._find_col_preferred(frame.columns, ["Unit"])
        source_col = self._find_col_preferred(
            frame.columns,
            ["Original Source / Database", "Data Source", "source"],
        )
        year_col = self._find_col_preferred(frame.columns, ["Factor Year", "Year"])
        boundary_col = self._find_col_preferred(
            frame.columns,
            ["Life-cycle Boundary", "Boundary"],
        )
        url_col = self._find_col_preferred(frame.columns, ["Source URL", "URL"])
        note_col = self._find_col_preferred(
            frame.columns,
            ["Verification / Replacement Note", "Notes", "Note"],
        )
        if not category_col or not factor_col:
            raise ValueError("Material sheet must contain category and numeric factor columns.")

        records: List[MaterialFactorRecord] = []
        seen: Dict[str, int] = {}
        for index, row in frame.iterrows():
            category = clean_cell_text(row.get(category_col, ""))
            if not category or category.casefold().startswith("notes"):
                continue
            factor = safe_float(row.get(factor_col), None)
            if factor is None or not math.isfinite(factor):
                continue
            subtype = clean_cell_text(row.get(subtype_col, "")) if subtype_col else ""
            material_name = f"{category} - {subtype}" if subtype else category
            base_id = "mat_" + slug(material_name)
            seen[base_id] = seen.get(base_id, 0) + 1
            row_id = base_id if seen[base_id] == 1 else f"{base_id}_{seen[base_id]}"
            keywords = unique_list(
                split_tokens(category)
                + split_tokens(subtype)
                + split_tokens(material_name)
                + MATERIAL_SYNONYMS.get(category, [])
            )
            density = DENSITY_BY_CATEGORY_KG_M3.get(category)
            density_source = "generic_adapter_density" if density is not None else ""
            unit = clean_cell_text(row.get(unit_col, "")) if unit_col else ""
            source = clean_cell_text(row.get(source_col, "")) if source_col else ""
            year = clean_cell_text(row.get(year_col, "")) if year_col else ""
            boundary = clean_cell_text(row.get(boundary_col, "")) if boundary_col else ""
            source_url = clean_cell_text(row.get(url_col, "")) if url_col else ""
            note = clean_cell_text(row.get(note_col, "")) if note_col else ""
            excel_row = int(frame.attrs.get("excel_header_row", 1)) + int(index) + 1
            source_note = "; ".join(
                part
                for part in [
                    f"{MATERIAL_SHEET} row {excel_row}",
                    boundary,
                    source,
                    year,
                    source_url,
                    note,
                ]
                if part
            )
            records.append(
                MaterialFactorRecord(
                    row_id=row_id,
                    category=category,
                    subtype=subtype,
                    material_name=material_name,
                    keywords=keywords,
                    factor_value=float(factor),
                    factor_unit=unit or "kgCO2e/kg",
                    density_kg_m3=density,
                    density_source=density_source,
                    source_file=self.path.name,
                    source_note=source_note,
                )
            )
        return records

    def _load_rag_material_factors(self, frame: Any) -> List[MaterialFactorRecord]:
        material_col = self._find_col(frame.columns, ["material_name", "material name", "material"])
        keywords_col = self._find_col(frame.columns, ["keywords", "keyword"])
        factor_col = self._find_col(frame.columns, ["factor_value", "factor value"])
        unit_col = self._find_col(frame.columns, ["factor_unit", "factor unit", "unit"])
        density_col = self._find_col(frame.columns, ["density_kg_m3", "density"])
        row_id_col = self._find_col(frame.columns, ["row_id", "id"])
        source_note_col = self._find_col(frame.columns, ["source_note", "source note", "notes"])
        if not material_col or not factor_col:
            raise ValueError("RAG material schema must contain material_name and factor_value columns.")

        records: List[MaterialFactorRecord] = []
        seen: Dict[str, int] = {}
        for index, row in frame.iterrows():
            material_name = clean_cell_text(row.get(material_col, ""))
            factor = safe_float(row.get(factor_col), None)
            if not material_name or factor is None or not math.isfinite(factor):
                continue
            category, subtype = self._split_material_name(material_name)
            raw_keywords = clean_cell_text(row.get(keywords_col, "")) if keywords_col else ""
            keywords = unique_list(
                [part.strip() for part in raw_keywords.split(";") if part.strip()]
                + split_tokens(material_name)
                + MATERIAL_SYNONYMS.get(category, [])
            )
            row_id = clean_cell_text(row.get(row_id_col, "")) if row_id_col else ""
            if not row_id:
                base_id = "mat_" + slug(material_name)
                seen[base_id] = seen.get(base_id, 0) + 1
                row_id = base_id if seen[base_id] == 1 else f"{base_id}_{seen[base_id]}"
            unit = clean_cell_text(row.get(unit_col, "")) if unit_col else "kgCO2e/kg"
            if not unit:
                unit = "kgCO2e/kg"
            density = safe_float(row.get(density_col), None) if density_col else None
            density_source = "rag_ready_table" if density is not None else ""
            source_note = clean_cell_text(row.get(source_note_col, "")) if source_note_col else ""
            if not source_note:
                source_note = f"RAG-ready material factor table row {index + 2}."
            records.append(
                MaterialFactorRecord(
                    row_id=row_id,
                    category=category,
                    subtype=subtype,
                    material_name=material_name,
                    keywords=keywords,
                    factor_value=float(factor),
                    factor_unit=unit,
                    density_kg_m3=density,
                    density_source=density_source,
                    source_file=self.path.name,
                    source_note=source_note,
                )
            )
        return records

    def _load_energy_factors(self, frame: Any) -> List[EnergyFactorRecord]:
        category_col = self._find_col(frame.columns, ["Category"])
        energy_col = self._find_col_preferred(
            frame.columns,
            ["Sub-type / Year", "Energy Type", "Sub-type", "type"],
        )
        factor_col = self._find_col_preferred(
            frame.columns,
            ["Selected Factor", "Emission Factor", "Original Factor", "factor"],
        )
        unit_col = self._find_col_preferred(frame.columns, ["Unit"])
        year_col = self._find_col_preferred(frame.columns, ["Factor Year", "Year"])
        source_col = self._find_col_preferred(
            frame.columns,
            ["Original Source / Database", "Data Source", "source"],
        )
        boundary_col = self._find_col_preferred(
            frame.columns,
            ["Life-cycle Boundary", "Boundary"],
        )
        notes_col = self._find_col_preferred(
            frame.columns,
            ["Selection / Boundary Note", "Notes", "Note"],
        )
        if not category_col or not energy_col or not factor_col:
            return []

        records: List[EnergyFactorRecord] = []
        seen: Dict[str, int] = {}
        for _, row in frame.iterrows():
            category = clean_cell_text(row.get(category_col, ""))
            energy_type = clean_cell_text(row.get(energy_col, ""))
            if not category or not energy_type or category.casefold().startswith("notes"):
                continue
            factor = safe_float(row.get(factor_col), None)
            unit = clean_cell_text(row.get(unit_col, "")) if unit_col else ""
            year = clean_cell_text(row.get(year_col, "")) if year_col else ""
            source = clean_cell_text(row.get(source_col, "")) if source_col else ""
            boundary = clean_cell_text(row.get(boundary_col, "")) if boundary_col else ""
            selection_note = clean_cell_text(row.get(notes_col, "")) if notes_col else ""
            notes = " | ".join(part for part in [boundary, selection_note] if part)
            quality_flag = "" if factor is not None else "non_numeric_factor_or_instruction"
            base_id = "energy_" + slug(f"{category}_{energy_type}_{year}_{unit}")
            seen[base_id] = seen.get(base_id, 0) + 1
            row_id = base_id if seen[base_id] == 1 else f"{base_id}_{seen[base_id]}"
            records.append(
                EnergyFactorRecord(
                    row_id=row_id,
                    category=category,
                    energy_type=energy_type,
                    factor_value=float(factor) if factor is not None else None,
                    factor_unit=unit,
                    year=year,
                    source=source,
                    notes=notes,
                    quality_flag=quality_flag,
                    source_file=self.path.name,
                )
            )
        return records

    @staticmethod
    def _has_rag_material_schema(columns: Sequence[str]) -> bool:
        normalized = {normalize_text(column).replace(" ", "_") for column in columns}
        return "material_name" in normalized and "factor_value" in normalized

    @staticmethod
    def _split_material_name(material_name: str) -> Tuple[str, str]:
        if " - " in material_name:
            category, subtype = material_name.split(" - ", 1)
            return category.strip(), subtype.strip()
        return material_name.strip(), ""

    @staticmethod
    def _find_col(columns: Sequence[str], fragments: Sequence[str]) -> str:
        for column in columns:
            text = normalize_text(column)
            for fragment in fragments:
                if normalize_text(fragment) in text:
                    return str(column)
        return ""

    @staticmethod
    def _find_col_preferred(columns: Sequence[str], fragments: Sequence[str]) -> str:
        for fragment in fragments:
            normalized_fragment = normalize_text(fragment)
            for column in columns:
                if normalized_fragment in normalize_text(column):
                    return str(column)
        return ""

    def retrieve_materials(self, context: ComponentContext, top_k: int = 5) -> List[RetrievalCandidate]:
        query_values = [context.ifc_type, context.name, context.material_text]
        for target in context.carbon_targets:
            query_values.extend(props(target).get("factorLookupKeys", []) or [])
        query_terms = token_set(query_values)
        material_text = normalize_text(context.material_text)
        ifc_type = normalize_text(context.ifc_type)
        structural = ifc_type in STRUCTURAL_IFC_TYPES

        candidates: List[RetrievalCandidate] = []
        for record in self.material_factors:
            record_terms = token_set([record.material_name, record.category, record.subtype] + record.keywords)
            matches = sorted(query_terms & record_terms)
            score = float(len(matches))
            rationale_parts: List[str] = []
            if matches:
                rationale_parts.append(f"matched {', '.join(matches)}")

            category = normalize_text(record.category)
            name = normalize_text(record.material_name)
            subtype = normalize_text(record.subtype)

            if ("\u94a2" in material_text or "steel" in material_text or "s355" in material_text) and category == "steel":
                score += 2.0
                rationale_parts.append("steel material context")
            if "\u91d1\u5c5e" in material_text and category in {"steel", "aluminium", "aluminium profile"}:
                score += 0.4
                rationale_parts.append("generic metal context")
            if structural and category == "steel" and "section" in subtype:
                score += 1.1
                rationale_parts.append("structural IFC type prefers steel section")
            if structural and category == "steel" and "rebar" in name:
                score -= 0.8
                rationale_parts.append("rebar is less suitable for structural section")
            if structural and category == "steel" and "light gauge" in name:
                score -= 0.2
            if "355" in query_terms and category == "steel":
                score += 0.3
                rationale_parts.append("S355-like steel token present")

            if score > 0:
                candidates.append(
                    RetrievalCandidate(
                        record=record,
                        score=round(score, 4),
                        matched_terms=matches,
                        rationale="; ".join(rationale_parts) or "weak lexical match",
                    )
                )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:top_k]

    def choose_energy_factor(self, driver_kind: str, factory_grid_preference: str = "Guangdong") -> Optional[EnergyFactorRecord]:
        available = [f for f in self.energy_factors if f.factor_value is not None]
        if not available:
            return None
        kind = normalize_text(driver_kind)
        preference = normalize_text(factory_grid_preference)

        def rank(factor: EnergyFactorRecord) -> Tuple[float, int]:
            text = normalize_text(" ".join([factor.category, factor.energy_type, factor.factor_unit, factor.notes]))
            score = 0.0
            year_match = re.search(r"(?:19|20)\d{2}", str(factor.year))
            year_num = int(year_match.group(0)) if year_match else 0
            if kind == "electricity":
                if "electricity" in text:
                    score += 2.0
                if "guangdong lifecycle co2e proxy" in text:
                    score += 10.0
                if preference and preference in text:
                    score += 2.0
                if "guangdong" in text:
                    score += 1.0
                if "china" in text:
                    score += 0.5
                if "kwh" in text:
                    score += 0.5
            elif kind == "diesel_l":
                if "diesel" in text and "/l" in text:
                    score += 3.0
            elif kind == "diesel_kg":
                if "diesel" in text and "/kg" in text:
                    score += 3.0
            elif kind == "welding_wire":
                if "welding" in text or "wire" in text:
                    score += 3.0
            else:
                if kind in text:
                    score += 1.0
            return score, year_num

        ranked = sorted(available, key=rank, reverse=True)
        if rank(ranked[0])[0] <= 0:
            return None
        return ranked[0]


class ProcessEvidenceStore:
    def __init__(self, docs_path: Path):
        self.path = docs_path
        self.docs = self._load(docs_path)

    def _load(self, docs_path: Path) -> List[ProcessEvidence]:
        suffix = docs_path.suffix.casefold()
        if suffix != ".jsonl":
            raise ValueError("Agentic RAG v2 expects process docs as JSONL.")
        docs: List[ProcessEvidence] = []
        for line in docs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            docs.append(
                ProcessEvidence(
                    doc_id=str(row.get("doc_id", "") or row.get("id", "") or row.get("title", "")),
                    title=str(row.get("title", "")),
                    component_types=[str(x) for x in row.get("component_types", []) or []],
                    keywords=[str(x) for x in row.get("keywords", []) or []],
                    activities=[str(x) for x in row.get("activities", []) or []],
                    text=str(row.get("text", "")),
                    source_file=str(row.get("source_file", "") or docs_path.name),
                    source_type=str(row.get("source_type", "")),
                    sequence=safe_int(row.get("sequence")),
                )
            )
        return docs

    def retrieve(self, context: ComponentContext, top_k: int = 8) -> List[RetrievalCandidate]:
        pt_props = props(context.process_target) if context.process_target else {}
        query_values = [
            context.ifc_type,
            context.name,
            context.material_text,
            props(context.component).get("reference", ""),
            props(context.component).get("typeObjectName", ""),
        ] + list(pt_props.get("genericRetrievalHints", []) or [])
        query_terms = token_set(query_values)
        query_terms = {t for t in query_terms if t and len(t) > 1}
        material_text = normalize_text(context.material_text)
        ifc_type = normalize_text(context.ifc_type)
        structural = ifc_type in STRUCTURAL_IFC_TYPES

        candidates: List[RetrievalCandidate] = []
        for doc in self.docs:
            record_terms = token_set([doc.title, doc.text] + doc.keywords + doc.activities + doc.component_types)
            matches: List[str] = []
            for q in query_terms:
                if q in record_terms:
                    matches.append(q)
                    continue
                if not is_numeric_token(q) and len(q) >= 4:
                    if any(q in term or term in q for term in record_terms if len(term) >= 4 and not is_numeric_token(term)):
                        matches.append(q)
            score = float(len(set(matches)))
            rationale_parts: List[str] = []
            if matches:
                rationale_parts.append(f"matched {', '.join(sorted(set(matches)))}")
            doc_types = {normalize_text(t) for t in doc.component_types}
            doc_activity_text = normalize_text(" ".join(doc.activities + [doc.title, doc.text]))

            if ifc_type and ifc_type in doc_types:
                score += 1.5
                rationale_parts.append(f"component type {context.ifc_type}")
            if structural and doc.source_type == "module production progress sequence":
                score += 1.0
                rationale_parts.append("progress sequence is preferred for structural production steps")
            if structural and ("\u94a2" in material_text or "steel" in material_text):
                if any(term in doc_activity_text for term in ["steel", "\u94a2", "\u94a2\u67b6", "\u89d2\u67f1", "welding", "\u710a", "cutting", "\u5f00\u6599"]):
                    score += 1.0
                    rationale_parts.append("steel-frame activity evidence")
            if doc.source_type.startswith("ITP"):
                score += 0.2
                rationale_parts.append("ITP QA/work-step evidence")
            if doc.sequence is not None:
                score += max(0.0, 0.4 - doc.sequence * 0.005)

            if score > 0:
                candidates.append(
                    RetrievalCandidate(
                        record=doc,
                        score=round(score, 4),
                        matched_terms=sorted(set(matches)),
                        rationale="; ".join(rationale_parts),
                    )
                )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:top_k]


def safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


class ProcessProfileStore:
    """Optional measured process consumption profile.

    CSV columns:
      activity_keyword, driver_kind, consumption_value, consumption_unit, note

    Example:
      SteelWelding,electricity,3.4,kWh,module welding measured value
    """

    def __init__(self, profile_path: Optional[Path] = None):
        self.rows: List[Dict[str, Any]] = []
        if profile_path and profile_path.exists():
            with profile_path.open("r", newline="", encoding="utf-8-sig") as f:
                self.rows = list(csv.DictReader(f))

    def find(self, evidence: ProcessEvidence, driver_kind: str) -> Optional[Dict[str, Any]]:
        haystack = normalize_text(" ".join([evidence.title, evidence.text] + evidence.activities + evidence.keywords))
        for row in self.rows:
            keyword = normalize_text(row.get("activity_keyword", ""))
            row_kind = normalize_text(row.get("driver_kind", ""))
            if keyword and keyword in haystack and (not row_kind or row_kind == normalize_text(driver_kind)):
                return row
        return None


class MaterialCarbonCalculator:
    def calculate(self, context: ComponentContext, factor: Optional[MaterialFactorRecord]) -> MaterialCarbonResult:
        if factor is None:
            return MaterialCarbonResult(
                status="incomplete",
                value_kgco2e=None,
                formula="",
                quantity_name="",
                quantity_value=None,
                quantity_unit="",
                factor_row_id="",
                factor_value=None,
                factor_unit="",
                density_kg_m3=None,
                issue="missing material emission factor",
            )

        unit = normalize_text(factor.factor_unit)
        q_by_name = {str(props(q).get("quantityName", "")): q for q in context.quantities}
        qb_props = props(context.quantity_basis) if context.quantity_basis else {}
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
                    carbon = value * factor.factor_value
                    return self._complete(carbon, f"{q_name} * material_factor", q_name, value, q_props, factor)
                if ("m3" in q_unit or "volume" in normalize_text(q_name)) and factor.density_kg_m3:
                    carbon = value * factor.density_kg_m3 * factor.factor_value
                    return self._complete(carbon, f"{q_name} * density * material_factor", q_name, value, q_props, factor)
            return self._issue(context, factor, "mass factor requires weight or volume plus density")

        if "kgco2e/m3" in unit:
            return self._calculate_by_quantity_family(context, factor, "preferredForVolumeFactor", "volume quantity required")
        if "kgco2e/m2" in unit:
            return self._calculate_by_quantity_family(context, factor, "preferredForAreaFactor", "area quantity required")
        if "kgco2e/unit" in unit or "kgco2e/item" in unit:
            return self._calculate_by_quantity_family(context, factor, "preferredForUnitFactor", "count quantity required")
        return self._issue(context, factor, f"unsupported material factor unit: {factor.factor_unit}")

    def _calculate_by_quantity_family(
        self,
        context: ComponentContext,
        factor: MaterialFactorRecord,
        family_key: str,
        issue: str,
    ) -> MaterialCarbonResult:
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
            carbon = value * factor.factor_value
            return self._complete(carbon, f"{q_name} * material_factor", q_name, value, q_props, factor)
        return self._issue(context, factor, issue)

    def _complete(
        self,
        carbon: float,
        formula: str,
        q_name: str,
        q_value: float,
        q_props: Dict[str, Any],
        factor: MaterialFactorRecord,
    ) -> MaterialCarbonResult:
        return MaterialCarbonResult(
            status="complete",
            value_kgco2e=round(carbon, 4),
            formula=formula,
            quantity_name=q_name,
            quantity_value=q_value,
            quantity_unit=str(q_props.get("quantityUnit", "")),
            factor_row_id=factor.row_id,
            factor_value=factor.factor_value,
            factor_unit=factor.factor_unit,
            density_kg_m3=factor.density_kg_m3,
        )

    def _issue(self, context: ComponentContext, factor: MaterialFactorRecord, issue: str) -> MaterialCarbonResult:
        return MaterialCarbonResult(
            status="incomplete",
            value_kgco2e=None,
            formula="",
            quantity_name="",
            quantity_value=None,
            quantity_unit="",
            factor_row_id=factor.row_id,
            factor_value=factor.factor_value,
            factor_unit=factor.factor_unit,
            density_kg_m3=factor.density_kg_m3,
            issue=issue,
        )


PROCESS_DRIVER_RULES: List[Dict[str, Any]] = [
    {
        "name": "cutting",
        "keywords": ["cutting", "steelcutting", "sawing", "plasma", "laser cut", "flame cut", "开料", "切割", "锯"],
        "drivers": [
            ("electricity", "cutting equipment electricity, kWh = rated_power_kW x operating_hours"),
        ],
    },
    {
        "name": "welding",
        "keywords": ["welding", "steelwelding", "arc weld", "mig", "mag", "tig", "焊", "烧焊", "拼焊", "点焊"],
        "drivers": [
            ("electricity", "welding power consumption, kWh = welding_machine_kW x arc_time_h"),
            ("welding_wire", "welding wire/electrode mass in kg"),
        ],
    },
    {
        "name": "brazing_or_soldering",
        "keywords": ["brazing", "soldering", "braze", "钎焊", "锡焊"],
        "drivers": [
            ("electricity", "brazing/soldering equipment electricity, kWh = rated_power_kW x heating_time_h"),
            ("welding_wire", "brazing filler/solder material mass in kg"),
        ],
    },
    {
        "name": "surface_treatment",
        "keywords": ["painting", "coating", "anticorrosioncoating", "spray", "blast", "galvanizing", "富锌", "油漆", "喷涂"],
        "drivers": [
            ("electricity", "surface treatment equipment electricity, kWh = equipment_kW x operating_hours"),
        ],
    },
    {
        "name": "casting_or_curing",
        "keywords": ["concrete casting", "casting", "curing", "steam curing", "水泥", "浇筑", "养护"],
        "drivers": [
            ("electricity", "casting/curing equipment electricity, kWh = equipment_kW x curing_or_operation_hours"),
        ],
    },
    {
        "name": "mechanical_fixing",
        "keywords": [
            "installation",
            "drilling",
            "grinding",
            "fastening",
            "pipework",
            "ductwork",
            "electrical installation",
            "安装",
            "钻孔",
            "打磨",
        ],
        "drivers": [
            ("electricity", "installation tool electricity, kWh = tool_kW x active_hours"),
        ],
    },
    {
        "name": "lifting_or_internal_transport",
        "keywords": ["forklift", "crane", "lifting", "hoist", "transport", "吊装", "叉车", "转运"],
        "drivers": [
            ("electricity", "electric lifting/handling equipment electricity, kWh = equipment_kW x operating_hours"),
            ("diesel_l", "diesel handling equipment fuel in L, if diesel-powered equipment is used"),
        ],
    },
    {
        "name": "testing_or_inspection",
        "keywords": ["hydraulic", "testing", "inspection", "pressure test", "验收", "试压", "检验"],
        "drivers": [
            ("electricity", "testing/inspection equipment electricity, kWh = equipment_kW x test_duration_h"),
        ],
    },
]


class ProcessCarbonPlanner:
    def __init__(
        self,
        factor_store: WorkbookFactorStore,
        profile_store: ProcessProfileStore,
        factory_grid_preference: str,
    ):
        self.factor_store = factor_store
        self.profile_store = profile_store
        self.factory_grid_preference = factory_grid_preference

    def plan(self, process_candidates: Sequence[RetrievalCandidate]) -> ProcessCarbonResult:
        drivers: List[ActivityDriver] = []
        seen: set[Tuple[str, str]] = set()
        for candidate in process_candidates:
            evidence: ProcessEvidence = candidate.record
            for driver_kind, required_quantity in self._driver_kinds(evidence):
                key = (evidence.doc_id, driver_kind)
                if key in seen:
                    continue
                seen.add(key)
                driver = ActivityDriver(
                    process_doc_id=evidence.doc_id,
                    process_title=evidence.title,
                    activity_tags=evidence.activities,
                    driver_kind=driver_kind,
                    required_quantity=required_quantity,
                    note=candidate.rationale,
                )
                energy_factor = self._select_factor(driver_kind)
                if energy_factor:
                    driver.preferred_factor_row_id = energy_factor.row_id
                    driver.preferred_factor_value = energy_factor.factor_value
                    driver.preferred_factor_unit = energy_factor.factor_unit
                profile = self.profile_store.find(evidence, driver_kind)
                if profile:
                    consumption = safe_float(profile.get("consumption_value"), None)
                    factor_is_co2e = bool(
                        energy_factor
                        and "kgco2e/" in normalize_text(energy_factor.factor_unit)
                    )
                    if (
                        consumption is not None
                        and energy_factor
                        and energy_factor.factor_value is not None
                        and factor_is_co2e
                    ):
                        driver.consumption_value = consumption
                        driver.consumption_unit = str(profile.get("consumption_unit", "") or "")
                        driver.carbon_value_kgco2e = round(consumption * energy_factor.factor_value, 4)
                        driver.status = "complete"
                        driver.note = unique_join([driver.note, str(profile.get("note", "") or "profile match")])
                    elif energy_factor and energy_factor.factor_value is not None and not factor_is_co2e:
                        driver.consumption_value = consumption
                        driver.consumption_unit = str(profile.get("consumption_unit", "") or "")
                        driver.status = "incompatible_factor_scope"
                        driver.note = unique_join(
                            [driver.note, "selected factor reports CO2 only; CO2e calculation blocked"]
                        )
                    else:
                        driver.status = "incomplete_profile_or_factor"
                drivers.append(driver)

        complete = [d for d in drivers if d.status == "complete" and d.carbon_value_kgco2e is not None]
        missing = [d for d in drivers if d.status != "complete"]
        if not drivers:
            return ProcessCarbonResult(
                status="incomplete",
                value_kgco2e=None,
                complete_driver_count=0,
                missing_driver_count=0,
                drivers=[],
                issue="no process evidence retrieved",
            )
        if missing:
            known = round(sum(float(d.carbon_value_kgco2e or 0.0) for d in complete), 4)
            return ProcessCarbonResult(
                status="requires_runtime_quantities",
                value_kgco2e=known if complete else None,
                complete_driver_count=len(complete),
                missing_driver_count=len(missing),
                drivers=drivers,
                issue="process steps retrieved, but measured kWh/L/kg activity quantities are missing",
            )
        total = round(sum(float(d.carbon_value_kgco2e or 0.0) for d in complete), 4)
        return ProcessCarbonResult(
            status="complete",
            value_kgco2e=total,
            complete_driver_count=len(complete),
            missing_driver_count=0,
            drivers=drivers,
        )

    def _select_factor(self, driver_kind: str) -> Optional[EnergyFactorRecord]:
        if driver_kind == "electricity":
            return self.factor_store.choose_energy_factor("electricity", self.factory_grid_preference)
        if driver_kind == "diesel_l":
            return self.factor_store.choose_energy_factor("diesel_l", self.factory_grid_preference)
        if driver_kind == "welding_wire":
            return self.factor_store.choose_energy_factor("welding_wire", self.factory_grid_preference)
        return None

    def _driver_kinds(self, evidence: ProcessEvidence) -> List[Tuple[str, str]]:
        text = normalize_text(" ".join([evidence.title, evidence.text] + evidence.activities + evidence.keywords))
        drivers: List[Tuple[str, str]] = []
        for rule in PROCESS_DRIVER_RULES:
            keywords = [normalize_text(term) for term in rule["keywords"]]
            if any(term and term in text for term in keywords):
                drivers.extend(rule["drivers"])
        if "diesel" not in text and "燃油" not in text and "柴油" not in text:
            drivers = [
                (kind, required)
                for kind, required in drivers
                if not (kind == "diesel_l" and "if diesel-powered" in required)
            ]
        return unique_driver_pairs(drivers)


def unique_driver_pairs(values: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for kind, required in values:
        key = f"{kind}:{required}"
        if key not in seen:
            seen.add(key)
            out.append((kind, required))
    return out


def unique_join(parts: Sequence[str]) -> str:
    return "; ".join(unique_list(parts))


class AgenticRagV2:
    def __init__(
        self,
        graph_store: BackboneGraphStore,
        factor_store: WorkbookFactorStore,
        process_store: ProcessEvidenceStore,
        profile_store: ProcessProfileStore,
        top_process_k: int,
        factory_grid_preference: str,
    ):
        self.graph_store = graph_store
        self.factor_store = factor_store
        self.process_store = process_store
        self.top_process_k = top_process_k
        self.material_calculator = MaterialCarbonCalculator()
        self.process_planner = ProcessCarbonPlanner(factor_store, profile_store, factory_grid_preference)

    def run(self, question: str) -> Dict[str, Any]:
        results = []
        for context in self.graph_store.component_contexts():
            material_candidates = self.factor_store.retrieve_materials(context, top_k=6)
            selected_material = material_candidates[0].record if material_candidates else None
            material_calc = self.material_calculator.calculate(context, selected_material)
            process_candidates = self.process_store.retrieve(context, top_k=self.top_process_k)
            process_calc = self.process_planner.plan(process_candidates)
            results.append(
                self._result_row(
                    question=question,
                    context=context,
                    material_candidates=material_candidates,
                    selected_material=selected_material,
                    material_calc=material_calc,
                    process_candidates=process_candidates,
                    process_calc=process_calc,
                )
            )
        return {
            "question": question,
            "ragLogic": "ifc_graph -> material_factor_retrieval + itp_process_retrieval + process_driver_planning -> C_mat/C_proc/C_MM",
            "inputs": {
                "materialFactorRows": len(self.factor_store.material_factors),
                "energyFactorRows": len(self.factor_store.energy_factors),
                "processEvidenceRows": len(self.process_store.docs),
            },
            "results": results,
            "summary": self._summary(results),
        }

    def _result_row(
        self,
        question: str,
        context: ComponentContext,
        material_candidates: Sequence[RetrievalCandidate],
        selected_material: Optional[MaterialFactorRecord],
        material_calc: MaterialCarbonResult,
        process_candidates: Sequence[RetrievalCandidate],
        process_calc: ProcessCarbonResult,
    ) -> Dict[str, Any]:
        c_mat = material_calc.value_kgco2e
        c_proc = process_calc.value_kgco2e
        total_known = None
        total_status = "incomplete"
        if c_mat is not None and c_proc is not None:
            total_known = round(c_mat + c_proc, 4)
            total_status = "complete" if process_calc.status == "complete" else "partial_process_known"
        elif c_mat is not None:
            total_known = c_mat
            total_status = "material_only_process_missing"

        return {
            "question": question,
            "componentGlobalId": context.global_id,
            "ifcType": context.ifc_type,
            "componentName": context.name,
            "materialText": context.material_text,
            "selectedMaterialFactor": material_record_to_dict(selected_material) if selected_material else None,
            "materialCandidates": [material_candidate_to_dict(c) for c in material_candidates],
            "materialCarbon": material_calc.__dict__,
            "selectedProcessSteps": [process_candidate_to_dict(c) for c in process_candidates],
            "processCarbon": process_result_to_dict(process_calc),
            "knownTotalCarbon_kgCO2e": total_known,
            "totalCarbonStatus": total_status,
            "agentDecision": {
                "materialStep": "selected highest-scoring material factor with structural steel preference",
                "processStep": "retrieved ITP/progress evidence as process sequence, not emission factors",
                "energyStep": "mapped process activities to required electricity/fuel/consumable drivers",
                "calculationStep": "computed C_mat when quantity and factor were ready; reported missing C_proc quantities",
            },
        }

    @staticmethod
    def _summary(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        total_c_mat = sum(float(row["materialCarbon"].get("value_kgco2e") or 0.0) for row in results)
        total_c_proc_known = sum(float(row["processCarbon"].get("value_kgco2e") or 0.0) for row in results)
        material_complete = sum(1 for row in results if row["materialCarbon"].get("status") == "complete")
        process_complete = sum(1 for row in results if row["processCarbon"].get("status") == "complete")
        return {
            "components": len(results),
            "materialCarbonComplete": material_complete,
            "processCarbonComplete": process_complete,
            "totalMaterialCarbon_kgCO2e": round(total_c_mat, 4),
            "knownProcessCarbon_kgCO2e": round(total_c_proc_known, 4),
            "knownTotalCarbon_kgCO2e": round(total_c_mat + total_c_proc_known, 4),
            "processInputGaps": sum(int(row["processCarbon"].get("missing_driver_count") or 0) for row in results),
        }


def material_record_to_dict(record: Optional[MaterialFactorRecord]) -> Dict[str, Any]:
    if record is None:
        return {}
    return {
        "rowId": record.row_id,
        "category": record.category,
        "subtype": record.subtype,
        "materialName": record.material_name,
        "factorValue": record.factor_value,
        "factorUnit": record.factor_unit,
        "densityKgM3": record.density_kg_m3,
        "densitySource": record.density_source,
        "sourceFile": record.source_file,
        "sourceNote": record.source_note,
    }


def material_candidate_to_dict(candidate: RetrievalCandidate) -> Dict[str, Any]:
    record: MaterialFactorRecord = candidate.record
    out = material_record_to_dict(record)
    out.update(
        {
            "retrievalScore": candidate.score,
            "matchedTerms": candidate.matched_terms,
            "rationale": candidate.rationale,
        }
    )
    return out


def process_candidate_to_dict(candidate: RetrievalCandidate) -> Dict[str, Any]:
    record: ProcessEvidence = candidate.record
    return {
        "docId": record.doc_id,
        "title": record.title,
        "componentTypes": record.component_types,
        "activities": record.activities,
        "sourceFile": record.source_file,
        "sourceType": record.source_type,
        "sequence": record.sequence,
        "retrievalScore": candidate.score,
        "matchedTerms": candidate.matched_terms,
        "rationale": candidate.rationale,
        "text": record.text,
    }


def process_result_to_dict(result: ProcessCarbonResult) -> Dict[str, Any]:
    return {
        "status": result.status,
        "value_kgco2e": result.value_kgco2e,
        "complete_driver_count": result.complete_driver_count,
        "missing_driver_count": result.missing_driver_count,
        "issue": result.issue,
        "drivers": [driver.__dict__ for driver in result.drivers],
    }


class RagV2Exporter:
    @staticmethod
    def write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        flat_rows: List[Dict[str, Any]] = []
        for row in rows:
            flat_rows.append(
                {
                    "componentGlobalId": row["componentGlobalId"],
                    "ifcType": row["ifcType"],
                    "componentName": row["componentName"],
                    "materialText": row["materialText"],
                    "selectedMaterialFactorId": (row["selectedMaterialFactor"] or {}).get("rowId", ""),
                    "selectedMaterialName": (row["selectedMaterialFactor"] or {}).get("materialName", ""),
                    "materialCarbonStatus": row["materialCarbon"].get("status", ""),
                    "materialCarbon_kgCO2e": row["materialCarbon"].get("value_kgco2e", ""),
                    "processCarbonStatus": row["processCarbon"].get("status", ""),
                    "processCarbonKnown_kgCO2e": row["processCarbon"].get("value_kgco2e", ""),
                    "processInputGaps": row["processCarbon"].get("missing_driver_count", ""),
                    "knownTotalCarbon_kgCO2e": row["knownTotalCarbon_kgCO2e"],
                    "totalCarbonStatus": row["totalCarbonStatus"],
                    "topProcessSteps": " | ".join(step["title"] for step in row["selectedProcessSteps"][:5]),
                }
            )
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(flat_rows[0].keys()))
            writer.writeheader()
            writer.writerows(flat_rows)

    @staticmethod
    def write_markdown(path: Path, payload: Dict[str, Any]) -> None:
        summary = payload.get("summary", {})
        lines = [
            "# DM2C Agentic RAG v2",
            "",
            f"Question: {payload.get('question', '')}",
            "",
            "## Logic",
            "",
            "`IFC/KG -> material factor retrieval + ITP process retrieval + process driver planning -> C_mat / C_proc / C_MM`",
            "",
            "## Summary",
            "",
            f"- Components: {summary.get('components', 0)}",
            f"- Material carbon complete: {summary.get('materialCarbonComplete', 0)}",
            f"- Process carbon complete: {summary.get('processCarbonComplete', 0)}",
            f"- Total material carbon: {summary.get('totalMaterialCarbon_kgCO2e', 0)} kgCO2e",
            f"- Known process carbon: {summary.get('knownProcessCarbon_kgCO2e', 0)} kgCO2e",
            f"- Known total carbon: {summary.get('knownTotalCarbon_kgCO2e', 0)} kgCO2e",
            f"- Process input gaps: {summary.get('processInputGaps', 0)}",
            "",
            "## Component Results",
            "",
        ]
        for row in payload.get("results", []):
            factor = row.get("selectedMaterialFactor") or {}
            mat = row.get("materialCarbon", {})
            proc = row.get("processCarbon", {})
            lines.extend(
                [
                    f"### {row.get('componentName', '')}",
                    "",
                    f"- IFC type: {row.get('ifcType', '')}",
                    f"- Material: {row.get('materialText', '')}",
                    f"- Selected material factor: {factor.get('materialName', '')} ({factor.get('factorValue', '')} {factor.get('factorUnit', '')})",
                    f"- C_mat: {mat.get('value_kgco2e', None)} kgCO2e ({mat.get('status', '')})",
                    f"- C_proc: {proc.get('value_kgco2e', None)} kgCO2e ({proc.get('status', '')})",
                    f"- Known C_MM: {row.get('knownTotalCarbon_kgCO2e', None)} kgCO2e ({row.get('totalCarbonStatus', '')})",
                    "",
                    "Top process evidence:",
                ]
            )
            for step in row.get("selectedProcessSteps", [])[:8]:
                lines.append(f"- {step.get('title', '')} [{step.get('sourceType', '')}]")
            missing = [d for d in proc.get("drivers", []) if d.get("status") != "complete"]
            if missing:
                lines.extend(["", "Missing runtime quantities:"])
                for driver in missing[:12]:
                    factor_text = driver.get("preferred_factor_row_id", "") or "no factor selected"
                    lines.append(
                        f"- {driver.get('process_title', '')}: {driver.get('required_quantity', '')}; factor={factor_text}"
                    )
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DM2C Agentic RAG v2.")
    parser.add_argument("--graph", default="outputs/dm2c_backbone_graph.json", help="Backbone graph JSON.")
    parser.add_argument(
        "--factor-workbook",
        default="Embodied_Carbon_Coefficients_Updated (1).xlsx",
        help="Workbook with material and energy factor sheets.",
    )
    parser.add_argument(
        "--process-docs",
        default="outputs/rag_experiments/itp_module_process_docs.jsonl",
        help="JSONL process corpus extracted from ITP/progress PDFs.",
    )
    parser.add_argument(
        "--process-profile",
        default="",
        help="Optional measured activity consumption CSV. See ProcessProfileStore docstring.",
    )
    parser.add_argument("--out-dir", default="outputs/rag_experiments", help="Output directory.")
    parser.add_argument("--top-process-k", type=int, default=8)
    parser.add_argument("--factory-grid", default="Guangdong", help="Preferred factory electricity grid factor.")
    parser.add_argument(
        "--question",
        default=(
            "Calculate modular manufacturing-stage carbon by combining material embodied "
            "carbon factors with ITP-derived process evidence and energy factors."
        ),
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    start = time.time()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_path = Path(args.graph)
    factor_path = Path(args.factor_workbook)
    process_path = Path(args.process_docs)
    profile_path = Path(args.process_profile) if args.process_profile else None
    if not graph_path.exists():
        raise SystemExit(f"Graph not found: {graph_path}")
    if not factor_path.exists():
        raise SystemExit(f"Factor workbook not found: {factor_path}")
    if not process_path.exists():
        raise SystemExit(f"Process docs not found: {process_path}")

    graph_store = BackboneGraphStore(graph_path)
    factor_store = WorkbookFactorStore(factor_path)
    process_store = ProcessEvidenceStore(process_path)
    profile_store = ProcessProfileStore(profile_path)
    runner = AgenticRagV2(
        graph_store=graph_store,
        factor_store=factor_store,
        process_store=process_store,
        profile_store=profile_store,
        top_process_k=args.top_process_k,
        factory_grid_preference=args.factory_grid,
    )
    payload = runner.run(args.question)
    payload["inputs"].update(
        {
            "graph": str(graph_path),
            "factorWorkbook": str(factor_path),
            "processDocs": str(process_path),
            "processProfile": str(profile_path) if profile_path else "",
            "factoryGrid": args.factory_grid,
            "elapsedMs": round((time.time() - start) * 1000, 3),
        }
    )

    RagV2Exporter.write_json(out_dir / "agentic_rag_v2_results.json", payload)
    RagV2Exporter.write_json(out_dir / "agentic_rag_v2_summary.json", payload["summary"])
    RagV2Exporter.write_csv(out_dir / "agentic_rag_v2_results.csv", payload["results"])
    RagV2Exporter.write_markdown(out_dir / "agentic_rag_v2_report.md", payload)

    print("DM2C Agentic RAG v2 completed.")
    print(f"  Graph: {graph_path}")
    print(f"  Factor workbook: {factor_path}")
    print(f"  Process docs: {process_path}")
    print(f"  Output directory: {out_dir}")
    print(f"  Material carbon total: {payload['summary']['totalMaterialCarbon_kgCO2e']} kgCO2e")
    print(f"  Known total carbon: {payload['summary']['knownTotalCarbon_kgCO2e']} kgCO2e")
    print(f"  Process input gaps: {payload['summary']['processInputGaps']}")


if __name__ == "__main__":
    main()
