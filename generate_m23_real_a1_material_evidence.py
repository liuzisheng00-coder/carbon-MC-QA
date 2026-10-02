from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import openpyxl

from dm2c_m23_canonical import SCHEMA_VERSION, stable_id
from dm2c_m23_ifc import (
    CanonicalIFCExtractor,
    ComponentRecord,
    DesignQuantityRecord,
    MaterialAssociationRecord,
)
from dm2c_multigranular_carbon_kg import FactorLibrary


FACTOR_WORKBOOK = Path(
    "outputs/research_experiments/case_study_facts_20260715/"
    "co2e_complete_factors/"
    "Embodied_Carbon_Coefficients_Hong_Kong_CO2e_Complete_2026-07-15.xlsx"
)
MATERIAL_POOL = Path("material_pool_enhanced1.xlsx")
BASE_CASE_CONFIG = Path("inputs/case_study/m23_canonical_v2_case.json")
EVIDENCE_OUT = Path("inputs/case_study/m23_real_a1_material_evidence.json")
REPORT_OUT = Path("inputs/case_study/m23_real_a1_material_evidence_report.json")
FIXTURE_CASE_OUT = Path("inputs/case_study/m23_canonical_v2_case_fixture.json")


@dataclass(frozen=True)
class PoolDensity:
    semantic_key: str
    material_name: str
    density: float
    source_id: str
    sheet: str
    row_index: int


@dataclass(frozen=True)
class GenerationResult:
    output_path: Path
    report_path: Path | None
    accepted_record_count: int
    rejected_record_count: int
    skipped_counts: Mapping[str, int]
    semantic_counts: Mapping[str, int]


_DENSITY_TARGETS: dict[str, tuple[str, ...]] = {
    "steel_section": ("Rolled steel section",),
    "light_gauge_steel": ("Light-gauge galvanized steel stud", "Galvanized steel"),
    "steel_rebar": ("Steel reinforcement mesh",),
    "concrete_rc_25_30": ("Reinforced concrete",),
    "rockwool": ("Rock wool", "Rock wool board", "Mineral wool acoustic insulation"),
    "calcium_silicate": ("Calcium silicate board", "Calcium silicate brick"),
    "fibre_cement": ("Fibre cement board",),
    "ceramics_tiles": ("Ceramic tile", "Ceramic floor tile", "Porcelain tile"),
    "toughened_glass": ("Toughened glass", "Glass", "Float glass"),
    "aluminium_profile": (
        "Thermally broken aluminium profile (6063)",
        "Aluminium window frame",
        "Aluminium alloy",
    ),
}


_FACTOR_TARGETS: dict[str, tuple[tuple[str, str], ...]] = {
    "steel_section": (("Steel", "Steel, Section"),),
    "light_gauge_steel": (("Steel", "Light Gauge Steel"),),
    "steel_rebar": (("Steel", "Steel Rebar"),),
    "concrete_rc_25_30": (("Concrete", "RC 25/30"),),
    "rockwool": (("Rockwool", "Rock wool insulation"),),
    "calcium_silicate": (("Calcium Silicate", "Calcium Silicate Sheet"),),
    "fibre_cement": (("Fibre Cement", "Fire-rated Board"),),
    "ceramics_tiles": (("Ceramics", "Tiles and cladding"),),
    "toughened_glass": (("Glass", "Toughened Glass"),),
    "aluminium_profile": (("Aluminium Profile", "6063-T6 Extrusion"),),
}


_AREA_FORMULA_KEYS = {
    "rockwool",
    "calcium_silicate",
    "fibre_cement",
    "ceramics_tiles",
}

_MATERIAL_CATEGORY_BY_KEY = {
    "steel_section": "Steel",
    "light_gauge_steel": "Steel",
    "steel_rebar": "Steel",
    "concrete_rc_25_30": "Concrete",
    "calcium_silicate": "Calcium Silicate",
    "fibre_cement": "Fibre Cement",
    "aluminium_profile": "Aluminium",
    "rockwool": "Rockwool",
    "ceramics_tiles": "Ceramics Tiles",
    "toughened_glass": "Glass",
}
_REPORT_CATEGORIES = (
    "Steel",
    "Concrete",
    "Calcium Silicate",
    "Fibre Cement",
    "Aluminium",
    "Rockwool",
    "Ceramics Tiles",
    "Glass",
)
_CONCRETE_DENSITY = PoolDensity(
    semantic_key="concrete_rc_25_30",
    material_name="Reinforced concrete density",
    density=2500.0,
    source_id="literature:reinforced_concrete_density:2500kg_m3",
    sheet="literature",
    row_index=0,
)


def _json_dump(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _hash_descriptor(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    payload = path.read_bytes()
    selected = _selected_path(path.resolve(), relative_to.resolve() if relative_to is not None else None)
    return {
        "path": selected,
        "sha256": sha256(payload).hexdigest().upper(),
        "sizeBytes": len(payload),
    }


def _selected_path(path: Path, relative_to: Path | None) -> str:
    if relative_to is None:
        return str(path)
    try:
        return os.path.relpath(path, relative_to)
    except ValueError:
        return str(path)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _norm(value: Any) -> str:
    return re.sub(r"[\W_]+", " ", _text(value).casefold()).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _text(value).casefold())


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle.casefold() in text for needle in needles)


def _find_header(rows: list[tuple[Any, ...]], required: str) -> tuple[int, list[str]]:
    for index, row in enumerate(rows):
        headers = [_text(cell) for cell in row]
        if required in headers:
            return index, headers
    raise ValueError(f"cannot find header containing {required!r}")


def _column(headers: list[str], *needles: str) -> int | None:
    for index, header in enumerate(headers):
        normalized = header.casefold()
        if all(needle.casefold() in normalized for needle in needles):
            return index
    return None


def _load_pool_densities(path: Path) -> dict[str, PoolDensity]:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows_by_sheet: list[tuple[str, int, Mapping[str, Any]]] = []
    for sheet_name in ("CandidatePool_EN", "Material_Hierarchy"):
        sheet = workbook[sheet_name]
        rows = list(sheet.iter_rows(values_only=True))
        header_index, headers = _find_header(rows, "Standardized Material Name")
        name_col = headers.index("Standardized Material Name")
        family_col = headers.index("Material Family") if "Material Family" in headers else None
        density_col = _column(headers, "density")
        source_col = (
            _column(headers, "reference url")
            or _column(headers, "ifc source url")
            or _column(headers, "parent material path")
            or _column(headers, "reference basis")
        )
        if density_col is None:
            continue
        for row_index, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            material_name = _text(row[name_col] if name_col < len(row) else "")
            if not material_name:
                continue
            density = row[density_col] if density_col < len(row) else None
            if type(density) not in (int, float) or not math.isfinite(float(density)):
                continue
            reference = _text(row[source_col] if source_col is not None and source_col < len(row) else "")
            source_id = f"material_pool:{sheet_name}:{row_index}:{reference or 'row'}"
            rows_by_sheet.append(
                (
                    sheet_name,
                    row_index,
                    {
                        "materialName": material_name,
                        "family": _text(row[family_col] if family_col is not None and family_col < len(row) else ""),
                        "density": float(density),
                        "sourceId": source_id,
                    },
                )
            )

    density_by_key: dict[str, PoolDensity] = {}
    for semantic_key, targets in _DENSITY_TARGETS.items():
        target_compacts = [_compact(target) for target in targets]
        for sheet_name, row_index, row in rows_by_sheet:
            row_name = _text(row["materialName"])
            row_compact = _compact(row_name)
            if any(row_compact == target or target in row_compact for target in target_compacts):
                density_by_key[semantic_key] = PoolDensity(
                    semantic_key=semantic_key,
                    material_name=row_name,
                    density=float(row["density"]),
                    source_id=_text(row["sourceId"]),
                    sheet=sheet_name,
                    row_index=row_index,
                )
                break
    return density_by_key


def _factor_rows_by_key(factor_library: FactorLibrary) -> dict[str, Mapping[str, Any]]:
    rows_by_key: dict[str, Mapping[str, Any]] = {}
    for semantic_key, selectors in _FACTOR_TARGETS.items():
        for category_token, subtype_token in selectors:
            matches = [
                row
                for row in factor_library.material_factors
                if category_token.casefold() in _text(row.get("category")).casefold()
                and subtype_token.casefold() in _text(row.get("subtype")).casefold()
            ]
            if len(matches) == 1:
                rows_by_key[semantic_key] = matches[0]
                break
        if semantic_key not in rows_by_key:
            raise ValueError(f"factor row not found or ambiguous for {semantic_key}")
    return rows_by_key


def _component_text(component: ComponentRecord) -> str:
    return " ".join(
        (
            component.ifc_class,
            component.name,
            component.object_type,
            component.predefined_type,
            component.tag,
        )
    ).casefold()


def _classify_association(
    association: MaterialAssociationRecord,
    component: ComponentRecord,
) -> str | None:
    material = association.material_name.casefold()
    component_text = _component_text(component)
    combined = f"{material} {component_text}"
    if _contains_any(combined, ("岩棉", "rock wool", "rockwool", "mineral wool")):
        return "rockwool"
    if _contains_any(combined, ("硅钙", "calcium silicate")):
        return "calcium_silicate"
    if _contains_any(combined, ("防火板", "fire rated board", "fire-rated board", "fire board", "fibre cement", "fiber cement")):
        return "fibre_cement"
    if _contains_any(combined, ("墙地砖", "瓷砖", "地砖", "tile", "ceramic", "porcelain")):
        return "ceramics_tiles"
    if _contains_any(combined, ("toughened glass", "tempered glass", "玻璃", "glass")):
        return "toughened_glass"
    if _contains_any(combined, ("窗框", "aluminium profile", "aluminum profile", "aluminium alloy", "aluminum alloy", "铝")):
        return "aluminium_profile"
    if _contains_any(combined, ("钢筋", "rebar", "reinforcement")):
        return "steel_rebar"
    if _contains_any(material, ("galvanized profiled steel sheet", "profiled steel sheet")):
        return "light_gauge_steel"
    if _contains_any(component_text, ("\u9f99\u9aa8", "\u69fd\u94a2")):
        return "light_gauge_steel"
    if _contains_any(combined, ("u型", "u-channel", "u channel", "龙骨", "light gauge", "light-gauge", "stud")) and _contains_any(
        combined, ("steel", "galvanized", "钢")
    ):
        return "light_gauge_steel"
    # "default floor slab" / "default slab" mirror the Revit placeholder "默认楼板" in the
    # English-named models, so a small default slab below the structural threshold is
    # classified the same way in both languages.
    if _contains_any(
        combined, ("混凝土", "concrete", "默认楼板", "default floor slab", "default slab")
    ) and component.ifc_class in {
        "IfcSlab",
        "IfcBeam",
        "IfcColumn",
    }:
        return "concrete_rc_25_30"
    if component.ifc_class in {"IfcBeam", "IfcColumn", "IfcMember"} and _contains_any(
        combined,
        (
            "hot rolled",
            "hot-rolled",
            "i section",
            "i-section",
            "steel",
            "galvanized",
            "钢",
        ),
    ):
        return "steel_section"
    return None


def _quantity_rank(quantity: DesignQuantityRecord, preferred_names: tuple[str, ...]) -> tuple[int, str]:
    names = [name.casefold() for name in preferred_names]
    quantity_name = quantity.quantity_name.casefold()
    try:
        rank = names.index(quantity_name)
    except ValueError:
        rank = len(names)
    return rank, quantity.id


def _pick_quantity(
    quantities: Iterable[DesignQuantityRecord],
    normalized_unit: str,
    preferred_names: tuple[str, ...],
) -> DesignQuantityRecord | None:
    candidates = [
        quantity
        for quantity in quantities
        if quantity.normalized_unit == normalized_unit and quantity.normalized_value > 0
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: _quantity_rank(row, preferred_names))[0]


def _unit_value(value: float, unit: str, target_unit: str) -> float:
    unit_key = unit.strip().casefold()
    if target_unit == "m":
        if unit_key == "mm":
            return value / 1000.0
        if unit_key == "m":
            return value
    if target_unit == "m2" and unit_key == "m2":
        return value
    if target_unit == "m3" and unit_key == "m3":
        return value
    if target_unit == "kg/m3" and unit_key == "kg/m3":
        return value
    raise ValueError(f"unsupported unit conversion {unit!r} to {target_unit!r}")


def _operand_by_role(record: Mapping[str, Any], role: str) -> Mapping[str, Any] | None:
    for operand in record.get("operands", ()):
        if isinstance(operand, Mapping) and operand.get("role") == role:
            return operand
    return None


def _record_mass_kg(record: Mapping[str, Any]) -> float | None:
    density_operand = _operand_by_role(record, "density")
    if density_operand is None:
        return None
    density = _unit_value(
        float(density_operand["value"]),
        _text(density_operand["unit"]),
        "kg/m3",
    )
    if record.get("formulaCode") == "volume_density":
        volume = _operand_by_role(record, "material_volume")
        if volume is None:
            return None
        return _unit_value(float(volume["value"]), _text(volume["unit"]), "m3") * density
    if record.get("formulaCode") == "area_thickness_density":
        area = _operand_by_role(record, "material_area")
        thickness = _operand_by_role(record, "thickness")
        if area is None or thickness is None:
            return None
        return (
            _unit_value(float(area["value"]), _text(area["unit"]), "m2")
            * _unit_value(float(thickness["value"]), _text(thickness["unit"]), "m")
            * density
        )
    return None


def _factor_value(factor_row: Mapping[str, Any]) -> float:
    value = factor_row.get("factor")
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError("factor row lacks finite factor value")
    return float(value)


def _is_floor_slab(component: ComponentRecord) -> bool:
    return (
        component.ifc_class == "IfcSlab"
        and _text(component.predefined_type).casefold() == "floor"
    )


def _length_quantity_m(quantity: DesignQuantityRecord | None) -> float:
    if quantity is None:
        return 0.0
    return _unit_value(quantity.source_value, quantity.source_unit, "m")


def _is_structural_floor_slab(
    component: ComponentRecord,
    component_quantities: Iterable[DesignQuantityRecord],
) -> bool:
    if not _is_floor_slab(component):
        return False
    volume = _pick_quantity(component_quantities, "m3", ("NetVolume", "GrossVolume"))
    depth = _pick_quantity(component_quantities, "m", ("Depth", "Thickness", "Width"))
    return (volume is not None and volume.normalized_value >= 0.5) or (
        _length_quantity_m(depth) >= 0.08
    )


def _is_profiled_steel_sheet(association: MaterialAssociationRecord) -> bool:
    material = association.material_name.casefold()
    return _contains_any(
        material,
        (
            "profiled steel sheet",
            "galvanized profiled steel sheet",
            "\u538b\u578b\u94a2\u677f",
            "\u9540\u950c\u94a2\u677f",
            "\u896c\u677f",
        ),
    )


def _is_secondary_slab_material(association: MaterialAssociationRecord) -> bool:
    material = association.material_name.casefold()
    return _is_profiled_steel_sheet(association) or _contains_any(
        material,
        (
            "\u57fa\u7840\u7802\u6d46",
            "base mortar",
            "mortar",
        ),
    )


def _is_authoring_default_material(association: MaterialAssociationRecord) -> bool:
    """Is this the authoring tool's placeholder material rather than a specified one?

    The model assigns one such material to a whole family of components through a
    single blanket association, and then names the actual material on each
    component separately. Where both are present they describe one layer, so the
    placeholder is the one to give up.
    """
    return _contains_any(
        association.material_name.casefold(),
        ("\u9ed8\u8ba4", "default"),
    )


def _layer_signature(
    association: MaterialAssociationRecord,
    semantic_key: str,
    record: Mapping[str, Any],
) -> tuple[Any, ...] | None:
    """Identify the physical layer a record describes, or None if it cannot.

    Two associations on one component that resolve to the same factor and draw on
    the same design quantities for the same mass are not two layers; they are one
    layer named twice. Keying on the computed mass rather than on the association
    alone keeps genuinely distinct layers apart, so a real layer set with
    per-layer thicknesses still yields one record per layer.
    """
    mass = _record_mass_kg(record)
    if mass is None:
        return None
    quantity_ids = tuple(
        sorted(
            _text(operand.get("designQuantityId"))
            for operand in record.get("operands", ())
            if isinstance(operand, Mapping) and operand.get("designQuantityId")
        )
    )
    return (
        association.component_id,
        semantic_key,
        _text(record.get("formulaCode")),
        quantity_ids,
        round(mass, 9),
    )


def _is_default_slab_material(association: MaterialAssociationRecord) -> bool:
    material = association.material_name.casefold()
    return _contains_any(
        material,
        (
            "\u9ed8\u8ba4\u697c\u677f",
            "default floor",
            "default slab",
            "concrete",
        ),
    )


def _slab_finish_key(component: ComponentRecord) -> str | None:
    if not _is_floor_slab(component):
        return None
    text = _component_text(component)
    if _contains_any(text, ("\u7845\u9499\u677f", "calcium silicate")):
        return "calcium_silicate"
    if _contains_any(
        text,
        (
            "\u9632\u706b\u677f",
            "fire rated board",
            "fire-rated board",
            "fire board",
            "fibre cement",
            "fiber cement",
        ),
    ):
        return "fibre_cement"
    return None


def _density_for_key(
    semantic_key: str,
    densities: Mapping[str, PoolDensity],
) -> PoolDensity | None:
    if semantic_key == "concrete_rc_25_30":
        return _CONCRETE_DENSITY
    return densities.get(semantic_key)


def _infer_thickness(association: MaterialAssociationRecord, component: ComponentRecord) -> tuple[float, str, str] | None:
    if association.thickness is not None and association.thickness > 0:
        return (
            association.thickness,
            association.thickness_unit or "m",
            f"ifc:MaterialLayerThickness:{association.id}",
        )
    text = " ".join((component.name, component.object_type, association.material_name))
    for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*mm(?![a-z])", text, flags=re.IGNORECASE):
        value = float(match.group(1))
        if 1 <= value <= 500:
            return value, "mm", f"ifc:NominalThickness:{component.id}"
    if _contains_any(text.casefold(), ("常规 - 145", "145mm")):
        return 145.0, "mm", f"ifc:NominalThickness:{component.id}"
    return None


def _infer_thickness_v2(
    association: MaterialAssociationRecord,
    component: ComponentRecord,
    component_quantities: Iterable[DesignQuantityRecord],
) -> tuple[float, str, str, str | None] | None:
    if association.thickness is not None and association.thickness > 0:
        return (
            association.thickness,
            association.thickness_unit or "m",
            f"ifc:MaterialLayerThickness:{association.id}",
            None,
        )
    text = " ".join((component.name, component.object_type, association.material_name))
    for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*mm(?![a-z])", text, flags=re.IGNORECASE):
        value = float(match.group(1))
        if 1 <= value <= 500:
            return value, "mm", f"ifc:NominalThickness:{component.id}", None
    preferred_thickness_names = (
        ("Depth", "Thickness", "Width")
        if component.ifc_class == "IfcSlab"
        else ("Width", "Depth", "Thickness")
    )
    thickness_quantity = _pick_quantity(
        component_quantities,
        "m",
        preferred_thickness_names,
    )
    if thickness_quantity is not None:
        return (
            thickness_quantity.source_value,
            thickness_quantity.source_unit,
            f"ifc:{thickness_quantity.quantity_name}",
            thickness_quantity.id,
        )
    return None


def _operand(record_id: str, role: str, value: float, unit: str, source_id: str, design_quantity_id: str | None) -> dict[str, Any]:
    return {
        "operandId": stable_id("MaterialEvidenceOperand", record_id, role, source_id),
        "role": role,
        "value": value,
        "unit": unit,
        "sourceId": source_id,
        "designQuantityId": design_quantity_id,
    }


def _evidence_record(
    association: MaterialAssociationRecord,
    semantic_key: str,
    factor_source_row_id: str,
    formula_code: str,
    operands: list[dict[str, Any]],
) -> dict[str, Any]:
    record_id = stable_id("MaterialEvidenceRecord", association.id, semantic_key, formula_code)
    return {
        "recordId": record_id,
        "associationId": association.id,
        "componentId": association.component_id,
        "materialId": association.material_id,
        "factorSourceRowId": factor_source_row_id,
        "formulaCode": formula_code,
        "operands": [
            {
                **operand,
                "operandId": stable_id(
                    "MaterialEvidenceOperand",
                    record_id,
                    operand["role"],
                    operand["sourceId"],
                ),
            }
            for operand in operands
        ],
        "measured": False,
    }


def _record_with_operands(
    association: MaterialAssociationRecord,
    component: ComponentRecord,
    component_quantities: Iterable[DesignQuantityRecord],
    semantic_key: str,
    factor_source_row_id: str,
    density: PoolDensity,
    *,
    force_formula_code: str | None = None,
    thickness_override: tuple[float, str, str, str | None] | None = None,
) -> tuple[dict[str, Any], str]:
    record_id_seed = stable_id("MaterialEvidenceRecord", association.id, semantic_key)
    density_operand = _operand(
        record_id_seed,
        "density",
        density.density,
        "kg/m3",
        density.source_id,
        None,
    )
    if force_formula_code == "area_thickness_density" or (
        force_formula_code is None and semantic_key in _AREA_FORMULA_KEYS
    ):
        formula_code = "area_thickness_density"
        area = _pick_quantity(
            component_quantities,
            "m2",
            (
                "NetSurfaceArea",
                "NetSideArea",
                "NetArea",
                "Area",
                "GrossSurfaceArea",
                "OuterSurfaceArea",
                "GrossSideArea",
                "GrossArea",
                "GrossFootprintArea",
            ),
        )
        thickness = thickness_override or _infer_thickness_v2(
            association, component, component_quantities
        )
        operands: list[dict[str, Any]] = []
        if area is not None:
            operands.append(
                _operand(
                    record_id_seed,
                    "material_area",
                    area.source_value,
                    area.source_unit,
                    f"ifc:{area.quantity_name}",
                    area.id,
                )
            )
        if thickness is not None:
            operands.append(
                _operand(
                    record_id_seed,
                    "thickness",
                    thickness[0],
                    thickness[1],
                    thickness[2],
                    thickness[3],
                )
            )
        operands.append(density_operand)
        status = "accepted" if area is not None and thickness is not None else "rejected_missing_geometry"
        return (
            _evidence_record(
                association,
                semantic_key,
                factor_source_row_id,
                formula_code,
                operands,
            ),
            status,
        )

    formula_code = force_formula_code or "volume_density"
    volume = _pick_quantity(
        component_quantities,
        "m3",
        ("NetVolume", "GrossVolume"),
    )
    operands = []
    if volume is not None:
        operands.append(
            _operand(
                record_id_seed,
                "material_volume",
                volume.source_value,
                volume.source_unit,
                f"ifc:{volume.quantity_name}",
                volume.id,
            )
        )
    operands.append(density_operand)
    status = "accepted" if volume is not None else "rejected_missing_geometry"
    return (
        _evidence_record(
            association,
            semantic_key,
            factor_source_row_id,
            formula_code,
            operands,
        ),
        status,
    )


def generate_material_evidence(
    *,
    ifc_path: Path | str,
    factor_workbook: Path | str,
    material_pool: Path | str,
    output_path: Path | str,
    report_path: Path | str | None = None,
) -> GenerationResult:
    ifc_path = Path(ifc_path)
    factor_workbook = Path(factor_workbook)
    material_pool = Path(material_pool)
    output_path = Path(output_path)
    report = Path(report_path) if report_path is not None else None

    extraction = CanonicalIFCExtractor(ifc_path).extract()
    component_by_id = {component.id: component for component in extraction.components}
    quantities_by_component: dict[str, list[DesignQuantityRecord]] = {}
    for quantity in extraction.design_quantities:
        quantities_by_component.setdefault(quantity.component_id, []).append(quantity)

    factor_library = FactorLibrary(factor_workbook)
    if factor_library.load_error:
        raise ValueError(factor_library.load_error)
    factor_rows = _factor_rows_by_key(factor_library)
    densities = _load_pool_densities(material_pool)

    records: list[dict[str, Any]] = []
    accepted = 0
    rejected = 0
    skipped: Counter[str] = Counter()
    semantic_counts: Counter[str] = Counter()
    skipped_records: list[dict[str, Any]] = []
    rejected_records: list[dict[str, Any]] = []
    suspicious_records: list[dict[str, Any]] = []
    material_breakdown: dict[str, dict[str, float | int]] = {
        category: {"acceptedRecords": 0, "massKg": 0.0, "carbonKgCO2e": 0.0}
        for category in _REPORT_CATEGORIES
    }
    slab_finish_recorded: set[str] = set()

    def append_record(
        *,
        association: MaterialAssociationRecord,
        component: ComponentRecord,
        semantic_key: str,
        factor_row: Mapping[str, Any],
        density: PoolDensity,
        record: dict[str, Any],
        status: str,
    ) -> None:
        nonlocal accepted, rejected
        records.append(record)
        semantic_counts[semantic_key] += 1
        if status == "accepted":
            accepted += 1
            mass = _record_mass_kg(record)
            if mass is not None:
                category = _MATERIAL_CATEGORY_BY_KEY.get(semantic_key, semantic_key)
                if category not in material_breakdown:
                    material_breakdown[category] = {
                        "acceptedRecords": 0,
                        "massKg": 0.0,
                        "carbonKgCO2e": 0.0,
                    }
                material_breakdown[category]["acceptedRecords"] = int(
                    material_breakdown[category]["acceptedRecords"]
                ) + 1
                material_breakdown[category]["massKg"] = float(
                    material_breakdown[category]["massKg"]
                ) + mass
                material_breakdown[category]["carbonKgCO2e"] = float(
                    material_breakdown[category]["carbonKgCO2e"]
                ) + mass * _factor_value(factor_row)
                if mass > 1000 and semantic_key != "concrete_rc_25_30":
                    suspicious_records.append(
                        {
                            "recordId": record["recordId"],
                            "associationId": association.id,
                            "componentId": association.component_id,
                            "ifcClass": component.ifc_class,
                            "componentName": component.name,
                            "materialName": association.material_name,
                            "semanticKey": semantic_key,
                            "formulaCode": record["formulaCode"],
                            "massKg": mass,
                        }
                    )
        else:
            rejected += 1
            rejected_records.append(
                {
                    "recordId": record["recordId"],
                    "associationId": association.id,
                    "componentId": association.component_id,
                    "materialName": association.material_name,
                    "semanticKey": semantic_key,
                    "formulaCode": record["formulaCode"],
                    "reasonCode": status,
                }
            )

    # Within a component, consider named materials before the authoring tool's
    # placeholder, so that when the two describe one layer the record kept is the
    # one that says what the layer is made of.
    ordered_associations = sorted(
        extraction.material_associations,
        key=lambda row: (
            row.component_id,
            _is_authoring_default_material(row),
            row.id,
        ),
    )
    layer_signatures: dict[tuple[Any, ...], str] = {}
    for association in ordered_associations:
        component = component_by_id.get(association.component_id)
        if component is None:
            skipped["component_missing"] += 1
            continue
        component_quantities = quantities_by_component.get(association.component_id, ())
        force_formula_code: str | None = None
        thickness_override: tuple[float, str, str, str | None] | None = None
        structural_slab = _is_structural_floor_slab(component, component_quantities)
        slab_finish_key = None if structural_slab else _slab_finish_key(component)
        if structural_slab:
            if _is_profiled_steel_sheet(association):
                semantic_key = "light_gauge_steel"
                force_formula_code = "area_thickness_density"
                thickness_override = (
                    1.0,
                    "mm",
                    "controlled_fixture:profiled_steel_sheet_nominal_thickness:1mm",
                    None,
                )
            elif _is_default_slab_material(association):
                semantic_key = "concrete_rc_25_30"
            elif _classify_association(association, component) == "steel_rebar":
                skipped["slab_reinforcement_excluded"] += 1
                skipped_records.append(
                    {
                        "associationId": association.id,
                        "componentId": association.component_id,
                        "materialName": association.material_name,
                        "ifcClass": component.ifc_class,
                        "reasonCode": "slab_reinforcement_excluded",
                    }
                )
                continue
            else:
                skipped["structural_slab_secondary_material_ignored"] += 1
                skipped_records.append(
                    {
                        "associationId": association.id,
                        "componentId": association.component_id,
                        "materialName": association.material_name,
                        "ifcClass": component.ifc_class,
                        "reasonCode": "structural_slab_secondary_material_ignored",
                    }
                )
                continue
        elif slab_finish_key is not None:
            if association.component_id in slab_finish_recorded:
                skipped["slab_finish_secondary_material_ignored"] += 1
                skipped_records.append(
                    {
                        "associationId": association.id,
                        "componentId": association.component_id,
                        "materialName": association.material_name,
                        "semanticKey": slab_finish_key,
                        "ifcClass": component.ifc_class,
                        "reasonCode": "slab_finish_secondary_material_ignored",
                    }
                )
                continue
            slab_finish_recorded.add(association.component_id)
            semantic_key = slab_finish_key
            force_formula_code = "area_thickness_density"
        elif _is_floor_slab(component) and _is_profiled_steel_sheet(association):
            semantic_key = "light_gauge_steel"
            force_formula_code = "area_thickness_density"
            thickness_override = (
                1.0,
                "mm",
                "controlled_fixture:profiled_steel_sheet_nominal_thickness:1mm",
                None,
            )
        else:
            semantic_key = _classify_association(association, component)
        if semantic_key is None:
            skipped["factor_not_mapped"] += 1
            skipped_records.append(
                {
                    "associationId": association.id,
                    "componentId": association.component_id,
                    "materialName": association.material_name,
                    "ifcClass": component.ifc_class,
                    "reasonCode": "factor_not_mapped",
                }
            )
            continue
        density = _density_for_key(semantic_key, densities)
        if density is None:
            skipped["density_not_mapped"] += 1
            skipped_records.append(
                {
                    "associationId": association.id,
                    "componentId": association.component_id,
                    "materialName": association.material_name,
                    "semanticKey": semantic_key,
                    "ifcClass": component.ifc_class,
                    "reasonCode": "density_not_mapped",
                }
            )
            continue
        factor_row = factor_rows.get(semantic_key)
        if factor_row is None:
            skipped["factor_not_mapped"] += 1
            continue
        record, status = _record_with_operands(
            association,
            component,
            component_quantities,
            semantic_key,
            _text(factor_row.get("sourceRowId")),
            density,
            force_formula_code=force_formula_code,
            thickness_override=thickness_override,
        )
        signature = _layer_signature(association, semantic_key, record)
        if signature is not None and signature in layer_signatures:
            skipped["duplicate_material_layer"] += 1
            skipped_records.append(
                {
                    "associationId": association.id,
                    "componentId": association.component_id,
                    "materialName": association.material_name,
                    "semanticKey": semantic_key,
                    "ifcClass": component.ifc_class,
                    "reasonCode": "duplicate_material_layer",
                    "retainedAssociationId": layer_signatures[signature],
                }
            )
            continue
        if signature is not None:
            layer_signatures[signature] = association.id
        append_record(
            association=association,
            component=component,
            semantic_key=semantic_key,
            factor_row=factor_row,
            density=density,
            record=record,
            status=status,
        )

    payload = {"schemaVersion": SCHEMA_VERSION, "records": records}
    _json_dump(output_path, payload)

    if report is not None:
        _json_dump(
            report,
            {
                "schemaVersion": SCHEMA_VERSION,
                "inputs": {
                    "ifc": str(ifc_path.resolve()),
                    "factorWorkbook": str(factor_workbook.resolve()),
                    "materialPool": str(material_pool.resolve()),
                },
                "counts": {
                    "materialAssociations": len(extraction.material_associations),
                    "records": len(records),
                    "acceptedRecords": accepted,
                    "rejectedRecords": rejected,
                    "skippedRecords": sum(skipped.values()),
                },
                "semanticCounts": dict(sorted(semantic_counts.items())),
                "skippedCounts": dict(sorted(skipped.items())),
                "materialBreakdown": {
                    category: {
                        "acceptedRecords": int(values["acceptedRecords"]),
                        "massKg": float(values["massKg"]),
                        "carbonKgCO2e": float(values["carbonKgCO2e"]),
                    }
                    for category, values in sorted(material_breakdown.items())
                },
                "suspiciousRecords": suspicious_records,
                "notes": {
                    "slabReinforcement": (
                        "slab reinforcement excluded; "
                        "Pset_ReinforcementBarPitchOfSlab can support future calculation"
                    )
                },
                "rejectedRecords": rejected_records,
                "skippedRecords": skipped_records,
            },
        )

    return GenerationResult(
        output_path=output_path,
        report_path=report,
        accepted_record_count=accepted,
        rejected_record_count=rejected,
        skipped_counts=dict(skipped),
        semantic_counts=dict(semantic_counts),
    )


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_controlled_fixture_case(
    *,
    base_case_config: Path | str,
    material_evidence: Path | str,
    output_path: Path | str,
    generated_at_utc: str | None = None,
) -> Path:
    base_case_config = Path(base_case_config)
    material_evidence = Path(material_evidence)
    output_path = Path(output_path)
    payload = json.loads(base_case_config.read_text(encoding="utf-8"))
    output_dir = output_path.parent
    for key in ("ifc", "factorWorkbook", "ontology", "factoryInput", "factoryTargetMap"):
        binding = payload.get(key)
        if binding is None:
            continue
        selected = Path(binding["path"])
        resolved = (
            selected.resolve()
            if selected.is_absolute()
            else (base_case_config.parent / selected).resolve()
        )
        payload[key] = {
            **binding,
            "path": _selected_path(resolved, output_dir.resolve()),
        }
    payload["releaseProfile"] = "controlled-fixture"
    payload["releaseReady"] = True
    payload["materialEvidence"] = _hash_descriptor(
        material_evidence,
        relative_to=output_dir,
    )
    payload["generatedAtUtc"] = generated_at_utc or _utc_now_text()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate real A1 material evidence and a controlled-fixture M2.3 case config."
    )
    parser.add_argument("--ifc", type=Path, default=Path("typed_completed.ifc"))
    parser.add_argument("--factor-workbook", type=Path, default=FACTOR_WORKBOOK)
    parser.add_argument("--material-pool", type=Path, default=MATERIAL_POOL)
    parser.add_argument("--evidence-out", type=Path, default=EVIDENCE_OUT)
    parser.add_argument("--report-out", type=Path, default=REPORT_OUT)
    parser.add_argument("--base-case-config", type=Path, default=BASE_CASE_CONFIG)
    parser.add_argument("--fixture-case-out", type=Path, default=FIXTURE_CASE_OUT)
    parser.add_argument("--generated-at-utc", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = generate_material_evidence(
        ifc_path=args.ifc,
        factor_workbook=args.factor_workbook,
        material_pool=args.material_pool,
        output_path=args.evidence_out,
        report_path=args.report_out,
    )
    case_path = write_controlled_fixture_case(
        base_case_config=args.base_case_config,
        material_evidence=args.evidence_out,
        output_path=args.fixture_case_out,
        generated_at_utc=args.generated_at_utc,
    )
    print(
        json.dumps(
            {
                "materialEvidence": str(Path(args.evidence_out).resolve()),
                "generationReport": str(Path(args.report_out).resolve()),
                "fixtureCaseConfig": str(case_path.resolve()),
                "acceptedRecords": result.accepted_record_count,
                "rejectedRecords": result.rejected_record_count,
                "skippedCounts": dict(result.skipped_counts),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
