# -*- coding: utf-8 -*-
"""Shared registry for the multi-module (Type A / B / D) experiments."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT_ROOT = ROOT / "outputs" / "research_experiments" / "multi_module_20260928"
FACTOR_WORKBOOK = (
    ROOT / "outputs" / "research_experiments" / "case_study_facts_20260715"
    / "co2e_complete_factors" / "Embodied_Carbon_Coefficients_Hong_Kong_CO2e_Complete_2026-07-15.xlsx"
)
FACTORY_INPUT = (
    ROOT / "outputs" / "research_experiments" / "factory_data_consolidation_20260721"
    / "factory_data_consolidated.json"
)
ONTOLOGY = ROOT / "mic-carbon-ontology.ttl"


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    label: str
    release_dir: Path
    case_config: Path
    evidence_report: Path
    evidence_json: Path
    ifc: Path
    floor_area_m2: float
    floor_area_basis: str
    context_dir: Path | None = None  # six-artifact copy when the release folder holds extra figure files

    @property
    def reader_dir(self) -> Path:
        return self.context_dir or self.release_dir


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        key="A",
        label="Type A",
        release_dir=ROOT / "outputs/research_experiments/m2_typea1_full_en_layerdedup_20260731_d_spread",
        context_dir=ROOT / "outputs/research_experiments/multi_module_20260928/releases/typea_clean",
        case_config=ROOT / "inputs/case_study/m23_canonical_v2_case_typea1_en_energy.json",
        evidence_report=ROOT / "inputs/case_study/m23_typea1_en_material_evidence_report.json",
        evidence_json=ROOT / "inputs/case_study/m23_typea1_en_material_evidence.json",
        ifc=ROOT / "typea1_full_en.ifc",
        floor_area_m2=17.2525 + 3.1264,
        floor_area_basis="Sum of GrossArea of the two 'Floor Slab:110mm' structural slabs (IFC Qto)",
    ),
    ModuleSpec(
        key="B",
        label="Type B",
        release_dir=ROOT / "outputs/research_experiments/typebd_aligned_smoke/typeb_aligned_massproxy_20260928",
        case_config=ROOT / "inputs/case_study/m23_canonical_v2_case_typeb_aligned_massproxy.json",
        evidence_report=ROOT / "inputs/case_study/m23_typeb_aligned_material_evidence_report.json",
        evidence_json=ROOT / "inputs/case_study/m23_typeb_aligned_material_evidence.json",
        ifc=ROOT / "inputs/case_study/typeb_aligned_en.ifc",
        floor_area_m2=2.198 / 0.11,
        floor_area_basis="Structural slab NetVolume 2.198 m3 / 110 mm (slab exported without a plan-area quantity)",
    ),
    ModuleSpec(
        key="D",
        label="Type D",
        release_dir=ROOT / "outputs/research_experiments/typebd_aligned_smoke/typed_aligned_massproxy_20260928",
        case_config=ROOT / "inputs/case_study/m23_canonical_v2_case_typed_aligned_massproxy.json",
        evidence_report=ROOT / "inputs/case_study/m23_typed_aligned_material_evidence_report.json",
        evidence_json=ROOT / "inputs/case_study/m23_typed_aligned_material_evidence.json",
        ifc=ROOT / "inputs/case_study/typed_aligned_en.ifc",
        floor_area_m2=20.3691,
        floor_area_basis="GrossArea of 'Floor Slab G7:Floor Slab G1' structural slab (IFC Qto)",
    ),
)


# Process-perspective variants for B and D. The main releases use the mass-scaled factory
# proxy (Type A module-level log and allocation rows scaled by module material mass).
# "as recorded" keeps each module's own factory identity, so only the four component-level
# logs apply; "equal route" borrows the Type A module identity without scaling.
def _variant(base: ModuleSpec, key: str, label: str, release: str, case: str) -> ModuleSpec:
    return ModuleSpec(
        key=key, label=label,
        release_dir=ROOT / "outputs/research_experiments/typebd_aligned_smoke" / release,
        case_config=ROOT / "inputs/case_study" / case,
        evidence_report=base.evidence_report, evidence_json=base.evidence_json, ifc=base.ifc,
        floor_area_m2=base.floor_area_m2, floor_area_basis=base.floor_area_basis,
    )


PROXY_MODULES: tuple[ModuleSpec, ...] = (
    _variant(MODULES[1], "B_recorded", "Type B (as recorded)", "typeb_aligned_20260928", "m23_canonical_v2_case_typeb_aligned.json"),
    _variant(MODULES[1], "B_equal", "Type B (equal route, unscaled)", "typeb_aligned_sameroute_20260928", "m23_canonical_v2_case_typeb_aligned_sameroute.json"),
    _variant(MODULES[2], "D_recorded", "Type D (as recorded)", "typed_aligned_20260928", "m23_canonical_v2_case_typed_aligned.json"),
    _variant(MODULES[2], "D_equal", "Type D (equal route, unscaled)", "typed_aligned_sameroute_20260928", "m23_canonical_v2_case_typed_aligned_sameroute.json"),
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def markdown_table(headers: list[str], rows: list[list]) -> str:
    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.1f}" if abs(v) >= 100 else f"{v:.3g}" if abs(v) < 1 else f"{v:.2f}"
        return str(v)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(fmt(v) for v in row) + " |")
    return "\n".join(lines)


def load_context(spec: ModuleSpec):
    from dm2c_canonical_v2_reader import load_canonical_v2_context

    return load_canonical_v2_context(spec.reader_dir, allow_synthetic=True)


def load_factor_rows() -> dict[tuple[str, int], dict]:
    """(sheet title, sheet row) -> factor row of the case factor workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(FACTOR_WORKBOOK, read_only=True, data_only=True)
    rows: dict[tuple[str, int], dict] = {}
    for title in ("Embodied Carbon Coefficients", "Energy Emission Factors"):
        ws = wb[title]
        for idx, values in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
            if idx < 6 or not values or values[0] is None:
                continue
            rows[(title, idx)] = {
                "sheet": title,
                "row": idx,
                "category": values[0],
                "subtype": values[1],
                "original_factor": values[2] if isinstance(values[2], (int, float)) else None,
                "selected_factor": values[3] if isinstance(values[3], (int, float)) else None,
                "unit": values[5],
                "action": values[6],
                "source": values[7],
                "boundary": values[10] if len(values) > 10 else None,
            }
    return rows


def factor_row_of(factor_source_id: str, rows: dict[tuple[str, int], dict]) -> dict | None:
    # format: sha256:<workbook sha>:<sheet title>:<row>
    parts = factor_source_id.split(":")
    if len(parts) < 4:
        return None
    try:
        row = int(parts[-1])
    except ValueError:
        return None
    return rows.get((parts[-2], row))
