#!/usr/bin/env python3
"""
Synthetic factory dataset generator for DM2C experiments.

The generated workbook is intentionally aligned with
dm2c_multigranular_carbon_kg.py:

* Factory energy records
* Batch allocation records
* Source metadata

One generated dataset stores three allocation bases. Workbook export selects a
single active basis so downstream KG builds can be compared under mass,
duration, or machine-hours allocation.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    import openpyxl
except ImportError:  # pragma: no cover - workbook export is optional at runtime
    openpyxl = None


ALLOCATION_BASES = ("mass", "duration", "machine_hours")
METERING_LEVELS = ("factory_month", "line", "workstation")
ENERGY_HEADERS = [
    "record_id",
    "source_id",
    "target_id",
    "target_level",
    "stage",
    "activity",
    "batch_id",
    "energy_carrier",
    "quantity_value",
    "quantity_unit",
    "metering_level",
    "production_line",
    "workstation_id",
    "period_start",
    "period_end",
    "record_role",
    "defect_flags",
]
ALLOCATION_HEADERS = [
    "record_id",
    "target_id",
    "target_level",
    "batch_id",
    "allocation_key",
    "allocation_value",
    "allocation_unit",
    "allocation_fraction",
    "module_id",
    "production_line",
    "workstation_id",
]
SOURCE_HEADERS = ["source_id", "source_name", "source_type", "note"]

# The metering hierarchy below is synthetic: the case evidence records energy at
# plant-month granularity only, while DM2C needs competing factory, line,
# workstation, module, and component records to test source disambiguation. The
# magnitudes are measured: 2023 steel-structure division monthly energy divided
# by the MiC programme throughput (873 modules in 105 days). Water is excluded
# because no compatible emission factor is in the case account.
MEASURED_MONTHLY_ELECTRICITY_KWH = 106_000.0
MEASURED_MONTHLY_DIESEL_L = 9_995.0
MICP_PROGRAMME_MODULES = 873.0
MICP_PROGRAMME_DAYS = 105.0
DAYS_PER_MONTH = 30.4375
MODULES_PER_MONTH = (MICP_PROGRAMME_MODULES / MICP_PROGRAMME_DAYS) * DAYS_PER_MONTH
ELECTRICITY_KWH_PER_MODULE = MEASURED_MONTHLY_ELECTRICITY_KWH / MODULES_PER_MONTH
DIESEL_L_PER_MODULE = MEASURED_MONTHLY_DIESEL_L / MODULES_PER_MONTH
ELECTRICITY_METERING_SHARES = {
    "factory": 0.5677,
    "line": 0.1798,
    "workstation": 0.1628,
    "module": 0.0742,
    "component": 0.0155,
}
DIESEL_METERING_SHARES = {"line": 0.6938, "workstation": 0.3062}


def _jittered_partition(total: float, count: int, rng: random.Random) -> list[float]:
    """Split a measured category total across synthetic records with seeded jitter."""

    if count <= 0:
        return []
    weights = [1.0 + rng.uniform(-0.08, 0.08) for _ in range(count)]
    weight_total = sum(weights)
    values = [_round(total * weight / weight_total, 3) for weight in weights[:-1]]
    values.append(_round(total - sum(values), 3))
    return values


@dataclass(frozen=True)
class FactorySynthConfig:
    seed: int = 42
    modules: int = 4
    module_types: int = 2
    production_lines: int = 2
    workstations: int = 6
    batches: int = 8
    module_name: str = "Main_Modularization_Model"
    graph_json: Optional[Path] = Path("outputs/m2_3_main_model_multigranular_carbon_kg/multigranular_carbon_kg.json")
    p_missing_quantity: float = 0.0
    p_missing_factor: float = 0.0
    p_unit_mismatch: float = 0.0
    p_unmatched_target: float = 0.0
    p_missing_allocation_basis: float = 0.0


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _pick_component_targets(graph_json: Optional[Path], minimum: int = 8) -> List[Dict[str, str]]:
    if graph_json and Path(graph_json).exists():
        try:
            payload = json.loads(Path(graph_json).read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        rows: List[Dict[str, str]] = []
        for node in payload.get("nodes", []):
            labels = set(node.get("labels", []))
            props = node.get("props", {})
            global_id = str(props.get("globalId") or "")
            if "BuildingComponent" in labels and global_id:
                rows.append(
                    {
                        "global_id": global_id,
                        "name": str(props.get("name") or global_id),
                        "ifc_type": str(props.get("ifcType") or ""),
                    }
                )
            if len(rows) >= minimum:
                return rows

    return [
        {
            "global_id": f"SYNTHETIC_IFC_COMPONENT_{idx:03d}",
            "name": f"Synthetic component {idx:03d}",
            "ifc_type": "SyntheticIfcElement",
        }
        for idx in range(1, minimum + 1)
    ]


def _maybe(rng: random.Random, probability: float) -> bool:
    return rng.random() < _clamp_probability(probability)


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _normalised_rows(
    record_id: str,
    basis: str,
    batches: Sequence[Dict[str, Any]],
    zero_basis: bool,
) -> List[Dict[str, Any]]:
    if basis == "mass":
        value_field = "mass_kg"
        unit = "kg"
    elif basis == "duration":
        value_field = "duration_h"
        unit = "h"
    elif basis == "machine_hours":
        value_field = "machine_hours"
        unit = "machine_h"
    else:
        raise ValueError(f"Unsupported allocation basis: {basis}")

    values = [0.0 if zero_basis else float(batch[value_field]) for batch in batches]
    total = sum(values)
    fractions: List[float] = []
    running = 0.0
    for idx, value in enumerate(values):
        if total <= 0:
            fraction = 0.0
        elif idx == len(values) - 1:
            fraction = max(0.0, 1.0 - running)
        else:
            fraction = round(value / total, 10)
            running += fraction
        fractions.append(fraction)

    rows: List[Dict[str, Any]] = []
    for batch, value, fraction in zip(batches, values, fractions):
        rows.append(
            {
                "record_id": record_id,
                "target_id": batch["module_target_id"],
                "target_level": "module",
                "batch_id": batch["batch_id"],
                "allocation_key": basis,
                "allocation_value": _round(value, 6),
                "allocation_unit": unit,
                "allocation_fraction": round(fraction, 10),
                "module_id": batch["module_id"],
                "production_line": batch["line_id"],
                "workstation_id": batch["workstation_id"],
            }
        )
    return rows


def _energy_record(
    record_id: str,
    source_id: str,
    target_id: str,
    target_level: str,
    stage: str,
    activity: str,
    batch_id: str,
    carrier: str,
    quantity_value: float,
    quantity_unit: str,
    metering_level: str,
    production_line: str = "",
    workstation_id: str = "",
    record_role: str = "",
) -> Dict[str, Any]:
    return {
        "record_id": record_id,
        "source_id": source_id,
        "target_id": target_id,
        "target_level": target_level,
        "stage": stage,
        "activity": activity,
        "batch_id": batch_id,
        "energy_carrier": carrier,
        "quantity_value": _round(quantity_value, 6),
        "quantity_unit": quantity_unit,
        "metering_level": metering_level,
        "production_line": production_line,
        "workstation_id": workstation_id,
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "record_role": record_role,
        "defect_flags": "",
    }


def _apply_energy_defects(
    records: List[Dict[str, Any]],
    config: FactorySynthConfig,
    rng: random.Random,
) -> None:
    for row in records:
        flags: List[str] = []
        if _maybe(rng, config.p_missing_quantity):
            row["quantity_value"] = ""
            flags.append("missing_quantity")
        if _maybe(rng, config.p_missing_factor):
            row["energy_carrier"] = f"unmatched_carrier_{row['record_id']}"
            flags.append("missing_factor")
        if _maybe(rng, config.p_unit_mismatch):
            row["quantity_unit"] = "unsupported_unit"
            flags.append("unit_mismatch")
        if row.get("target_level") != "unknown" and _maybe(rng, config.p_unmatched_target):
            row["target_id"] = f"UNMATCHED_{row['target_id']}"
            flags.append("unmatched_target")
        row["defect_flags"] = ",".join(flags)


def _summarize(dataset: Dict[str, Any]) -> Dict[str, Any]:
    shared_records = [
        row for row in dataset["energy_records"] if row.get("target_level") == "unknown"
    ]
    allocation_set_counts = {
        basis: len({row["record_id"] for row in rows})
        for basis, rows in dataset["allocation_bases"].items()
    }
    allocation_weight_sums: Dict[str, Dict[str, float]] = {}
    for basis, rows in dataset["allocation_bases"].items():
        grouped: Dict[str, float] = defaultdict(float)
        for row in rows:
            grouped[row["record_id"]] += float(row["allocation_fraction"])
        allocation_weight_sums[basis] = {
            record_id: round(total, 10) for record_id, total in sorted(grouped.items())
        }
    return {
        "module_count": len(dataset["modules"]),
        "module_type_count": len({row["module_type"] for row in dataset["modules"]}),
        "production_line_count": len(dataset["production_lines"]),
        "workstation_count": len(dataset["workstations"]),
        "batch_count": len(dataset["batches"]),
        "energy_record_count": len(dataset["energy_records"]),
        "shared_energy_record_count": len(shared_records),
        "energy_carriers": sorted({str(row["energy_carrier"]) for row in dataset["energy_records"]}),
        "metering_levels": sorted({str(row["metering_level"]) for row in dataset["energy_records"]}),
        "allocation_bases": list(ALLOCATION_BASES),
        "allocation_set_counts": allocation_set_counts,
        "allocation_weight_sums": allocation_weight_sums,
        "source_metadata_count": len(dataset["source_metadata"]),
    }


def generate_factory_dataset(config: FactorySynthConfig = FactorySynthConfig()) -> Dict[str, Any]:
    rng = random.Random(config.seed)
    module_count = max(4, int(config.modules))
    module_type_count = max(2, int(config.module_types))
    line_count = max(2, int(config.production_lines))
    workstation_count = max(6, int(config.workstations))
    batch_count = max(8, int(config.batches))

    components = _pick_component_targets(config.graph_json, minimum=8)
    module_types = [f"Type-{chr(ord('A') + idx)}" for idx in range(module_type_count)]
    modules: List[Dict[str, Any]] = []
    for idx in range(module_count):
        module_id = f"MOD_SYN_{idx + 1:03d}"
        module_name = config.module_name if idx == 0 else f"FactoryModule_{idx + 1:03d}"
        modules.append(
            {
                "module_id": module_id,
                "module_name": module_name,
                "module_target_id": module_name,
                "module_type": module_types[idx % module_type_count],
            }
        )

    production_lines = [
        {
            "line_id": f"LINE_{chr(ord('A') + idx)}",
            "line_name": f"Production line {chr(ord('A') + idx)}",
        }
        for idx in range(line_count)
    ]
    workstations: List[Dict[str, Any]] = []
    for idx in range(workstation_count):
        line = production_lines[idx % line_count]
        workstations.append(
            {
                "workstation_id": f"WS_{idx + 1:02d}",
                "workstation_name": f"Workstation {idx + 1:02d}",
                "line_id": line["line_id"],
            }
        )

    batches: List[Dict[str, Any]] = []
    for idx in range(batch_count):
        module = modules[idx % module_count]
        workstation = workstations[idx % workstation_count]
        mass = 8200.0 + idx * 430.0 + rng.randint(0, 180)
        duration = 10.5 + (idx % 4) * 1.75 + rng.random()
        machine_hours = 7.0 + (idx % 5) * 1.4 + rng.random() * 0.75
        batches.append(
            {
                "batch_id": f"BATCH_{idx + 1:03d}",
                "module_id": module["module_id"],
                "module_target_id": module["module_target_id"],
                "module_type": module["module_type"],
                "line_id": workstation["line_id"],
                "workstation_id": workstation["workstation_id"],
                "mass_kg": _round(mass, 3),
                "duration_h": _round(duration, 3),
                "machine_hours": _round(machine_hours, 3),
            }
        )

    records: List[Dict[str, Any]] = []
    electricity_total = (
        module_count
        * ELECTRICITY_KWH_PER_MODULE
        * (1.0 + rng.uniform(-0.0025, 0.0025))
    )
    diesel_total = (
        module_count * DIESEL_L_PER_MODULE * (1.0 + rng.uniform(-0.0025, 0.0025))
    )
    electricity_by_level = {
        level: electricity_total * share
        for level, share in ELECTRICITY_METERING_SHARES.items()
    }
    diesel_by_level = {
        level: diesel_total * share
        for level, share in DIESEL_METERING_SHARES.items()
    }
    records.append(
        _energy_record(
            "SYN_E_FACTORY_001",
            "source:synthetic_factory_meter",
            "unknown",
            "unknown",
            "Factory support",
            "Monthly shared factory electricity",
            "ALL_BATCHES",
            "electricity",
            electricity_by_level["factory"],
            "kWh",
            "factory_month",
            record_role="shared_factory_meter",
        )
    )

    line_carriers = [
        "electricity" if index % 2 == 0 else "diesel"
        for index in range(len(production_lines))
    ]
    electricity_line_quantities = iter(_jittered_partition(
        electricity_by_level["line"], line_carriers.count("electricity"), rng
    ))
    diesel_line_quantities = iter(_jittered_partition(
        diesel_by_level["line"], line_carriers.count("diesel"), rng
    ))
    for line_index, line in enumerate(production_lines):
        line_batches = [batch for batch in batches if batch["line_id"] == line["line_id"]]
        carrier = line_carriers[line_index]
        unit = "kWh" if carrier == "electricity" else "L"
        quantity = (
            next(electricity_line_quantities)
            if carrier == "electricity"
            else next(diesel_line_quantities)
        )
        records.append(
            _energy_record(
                f"SYN_E_{line['line_id']}_001",
                "source:synthetic_line_meter",
                "unknown",
                "unknown",
                "Line production",
                f"Shared metered energy for {line['line_id']}",
                f"{line['line_id']}_BATCHES",
                carrier,
                quantity,
                unit,
                "line",
                production_line=line["line_id"],
                record_role="shared_line_meter",
            )
        )

    workstation_carriers = [
        "diesel" if int(workstation["workstation_id"].split("_")[1]) % 3 == 0 else "electricity"
        for workstation in workstations
    ]
    electricity_workstation_quantities = iter(_jittered_partition(
        electricity_by_level["workstation"], workstation_carriers.count("electricity"), rng
    ))
    diesel_workstation_quantities = iter(_jittered_partition(
        diesel_by_level["workstation"], workstation_carriers.count("diesel"), rng
    ))
    for workstation, carrier in zip(workstations, workstation_carriers):
        unit = "L" if carrier == "diesel" else "kWh"
        records.append(
            _energy_record(
                f"SYN_E_{workstation['workstation_id']}_001",
                "source:synthetic_workstation_meter",
                "unknown",
                "unknown",
                "Workstation operation",
                f"Shared energy at {workstation['workstation_id']}",
                f"{workstation['workstation_id']}_BATCHES",
                carrier,
                (
                    next(diesel_workstation_quantities)
                    if carrier == "diesel"
                    else next(electricity_workstation_quantities)
                ),
                unit,
                "workstation",
                production_line=workstation["line_id"],
                workstation_id=workstation["workstation_id"],
                record_role="shared_workstation_meter",
            )
        )

    module_quantities = _jittered_partition(
        electricity_by_level["module"], len(modules), rng
    )
    for idx, module in enumerate(modules):
        records.append(
            _energy_record(
                f"SYN_E_MODULE_{idx + 1:03d}",
                "source:synthetic_direct_log",
                module["module_target_id"],
                "module",
                "Finishing",
                "Module-specific finishing",
                "",
                "electricity",
                module_quantities[idx],
                "kWh",
                "workstation",
                production_line=production_lines[idx % line_count]["line_id"],
                workstation_id=workstations[idx % workstation_count]["workstation_id"],
                record_role="direct_module_log",
            )
        )

    component_targets = components[:4]
    component_quantities = _jittered_partition(
        electricity_by_level["component"], len(component_targets), rng
    )
    for idx, component in enumerate(component_targets):
        records.append(
            _energy_record(
                f"SYN_E_COMPONENT_{idx + 1:03d}",
                "source:synthetic_direct_log",
                component["global_id"],
                "component",
                "Component assembly",
                f"Component-level energy log {idx + 1}",
                "",
                "electricity",
                component_quantities[idx],
                "kWh",
                "workstation",
                production_line=production_lines[idx % line_count]["line_id"],
                workstation_id=workstations[(idx + 1) % workstation_count]["workstation_id"],
                record_role="direct_component_log",
            )
        )

    allocation_records: Dict[str, List[Dict[str, Any]]] = {basis: [] for basis in ALLOCATION_BASES}
    shared_record_batches: Dict[str, List[Dict[str, Any]]] = {}
    for row in records:
        if row["target_level"] != "unknown":
            continue
        if row["metering_level"] == "factory_month":
            selected_batches = batches
        elif row["metering_level"] == "line":
            selected_batches = [
                batch for batch in batches if batch["line_id"] == row["production_line"]
            ]
        elif row["metering_level"] == "workstation":
            selected_batches = [
                batch for batch in batches if batch["workstation_id"] == row["workstation_id"]
            ]
        else:
            selected_batches = []
        if not selected_batches:
            selected_batches = batches[:1]
        shared_record_batches[row["record_id"]] = selected_batches
        for basis in ALLOCATION_BASES:
            zero_basis = _maybe(rng, config.p_missing_allocation_basis)
            allocation_records[basis].extend(
                _normalised_rows(row["record_id"], basis, selected_batches, zero_basis)
            )

    _apply_energy_defects(records, config, rng)

    source_metadata = [
        {
            "source_id": "source:synthetic_factory_meter",
            "source_name": "Synthetic factory monthly meter",
            "source_type": "factory_energy_record",
            "note": "P0 synthetic shared factory-meter layer.",
        },
        {
            "source_id": "source:synthetic_line_meter",
            "source_name": "Synthetic production line meter",
            "source_type": "factory_energy_record",
            "note": "P0 synthetic line-level meter layer.",
        },
        {
            "source_id": "source:synthetic_workstation_meter",
            "source_name": "Synthetic workstation meter",
            "source_type": "factory_energy_record",
            "note": "P0 synthetic workstation-level meter layer.",
        },
        {
            "source_id": "source:synthetic_direct_log",
            "source_name": "Synthetic direct process log",
            "source_type": "factory_energy_record",
            "note": "P0 synthetic direct module/component process layer.",
        },
    ]

    dataset: Dict[str, Any] = {
        "metadata": {
            "generator": "dm2c_factory_synth.py",
            "seed": config.seed,
            "config": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(config).items()
            },
        },
        "modules": modules,
        "production_lines": production_lines,
        "workstations": workstations,
        "batches": batches,
        "component_targets": components,
        "energy_records": records,
        "allocation_bases": allocation_records,
        "shared_record_batches": {
            record_id: [batch["batch_id"] for batch in selected]
            for record_id, selected in sorted(shared_record_batches.items())
        },
        "source_metadata": source_metadata,
    }
    dataset["summary"] = _summarize(dataset)
    dataset["validation"] = validate_factory_dataset(dataset)
    return dataset


def validate_factory_dataset(dataset: Dict[str, Any]) -> Dict[str, Any]:
    violations: List[Dict[str, Any]] = []

    def require(condition: bool, issue_type: str, message: str) -> None:
        if not condition:
            violations.append({"issue_type": issue_type, "message": message})

    require(len(dataset.get("modules", [])) >= 4, "scale_modules", "At least four modules are required.")
    require(
        len({row.get("module_type") for row in dataset.get("modules", [])}) >= 2,
        "scale_module_types",
        "At least two module types are required.",
    )
    require(
        len(dataset.get("production_lines", [])) >= 2,
        "scale_lines",
        "At least two production lines are required.",
    )
    require(
        len(dataset.get("workstations", [])) >= 6,
        "scale_workstations",
        "At least six workstations are required.",
    )
    require(len(dataset.get("batches", [])) >= 8, "scale_batches", "At least eight batches are required.")

    carriers = {row.get("energy_carrier") for row in dataset.get("energy_records", [])}
    require({"electricity", "diesel"}.issubset(carriers), "carrier_coverage", "Electricity and diesel are required.")
    levels = {row.get("metering_level") for row in dataset.get("energy_records", [])}
    require(set(METERING_LEVELS).issubset(levels), "metering_coverage", "Three metering levels are required.")
    require(
        set(ALLOCATION_BASES) == set(dataset.get("allocation_bases", {}).keys()),
        "allocation_basis_coverage",
        "Mass, duration, and machine-hours allocation bases are required.",
    )

    for basis, rows in dataset.get("allocation_bases", {}).items():
        grouped: Dict[str, float] = defaultdict(float)
        value_grouped: Dict[str, float] = defaultdict(float)
        for row in rows:
            grouped[str(row.get("record_id", ""))] += float(row.get("allocation_fraction") or 0.0)
            value_grouped[str(row.get("record_id", ""))] += float(row.get("allocation_value") or 0.0)
            if row.get("allocation_key") != basis:
                violations.append(
                    {
                        "issue_type": "allocation_key_mismatch",
                        "message": f"Allocation row key {row.get('allocation_key')} does not match {basis}.",
                    }
                )
        require(bool(grouped), "allocation_sets_missing", f"No allocation sets for {basis}.")
        for record_id, total in grouped.items():
            if abs(total - 1.0) > 1e-7:
                violations.append(
                    {
                        "issue_type": "allocation_fraction_sum",
                        "message": f"{basis} allocation fractions for {record_id} sum to {total}.",
                    }
                )
        for record_id, total in value_grouped.items():
            if total <= 0:
                violations.append(
                    {
                        "issue_type": "allocation_basis_value_missing",
                        "message": f"{basis} allocation values for {record_id} sum to {total}.",
                    }
                )

    return {
        "violation_count": len(violations),
        "violations": violations,
    }


def _write_sheet(workbook: Any, sheet_name: str, headers: Sequence[str], rows: Iterable[Dict[str, Any]]) -> None:
    ws = workbook.create_sheet(sheet_name)
    ws.append(list(headers))
    for row in rows:
        ws.append([row.get(header, "") for header in headers])
    ws.freeze_panes = "A2"


def export_factory_workbook(
    dataset: Dict[str, Any],
    path: Path,
    allocation_basis: str = "mass",
) -> None:
    if allocation_basis not in ALLOCATION_BASES:
        raise ValueError(f"allocation_basis must be one of {', '.join(ALLOCATION_BASES)}")
    if openpyxl is None:
        raise RuntimeError("openpyxl is required to export factory workbooks")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)
    workbook.properties.creator = "dm2c_factory_synth.py"
    workbook.properties.lastModifiedBy = "dm2c_factory_synth.py"
    _write_sheet(workbook, "Factory energy records", ENERGY_HEADERS, dataset["energy_records"])
    _write_sheet(
        workbook,
        "Batch allocation records",
        ALLOCATION_HEADERS,
        dataset["allocation_bases"][allocation_basis],
    )
    _write_sheet(workbook, "Source metadata", SOURCE_HEADERS, dataset["source_metadata"])
    workbook.save(path)


def _write_csv(path: Path, headers: Sequence[str], rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(headers), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_factory_dataset_outputs(dataset: Dict[str, Any], out_dir: Path) -> Dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: Dict[str, str] = {}

    dataset_path = out_dir / "factory_synth_dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs["dataset_json"] = str(dataset_path)

    summary_path = out_dir / "factory_synth_summary.json"
    summary_payload = {
        "summary": dataset["summary"],
        "validation": dataset["validation"],
        "outputs": {},
    }
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs["summary_json"] = str(summary_path)

    energy_csv = out_dir / "factory_energy_records.csv"
    _write_csv(energy_csv, ENERGY_HEADERS, dataset["energy_records"])
    outputs["energy_csv"] = str(energy_csv)

    source_csv = out_dir / "source_metadata.csv"
    _write_csv(source_csv, SOURCE_HEADERS, dataset["source_metadata"])
    outputs["source_csv"] = str(source_csv)

    for basis in ALLOCATION_BASES:
        allocation_csv = out_dir / f"batch_allocation_records_{basis}.csv"
        _write_csv(allocation_csv, ALLOCATION_HEADERS, dataset["allocation_bases"][basis])
        outputs[f"allocation_csv_{basis}"] = str(allocation_csv)
        if openpyxl is not None:
            workbook_path = out_dir / f"factory_synth_{basis}.xlsx"
            export_factory_workbook(dataset, workbook_path, allocation_basis=basis)
            outputs[f"workbook_{basis}"] = str(workbook_path)

    summary_payload["outputs"] = outputs
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a deterministic P0 synthetic factory dataset.")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/research_experiments/p0_factory_synth"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--modules", type=int, default=4)
    parser.add_argument("--module-types", type=int, default=2)
    parser.add_argument("--production-lines", type=int, default=2)
    parser.add_argument("--workstations", type=int, default=6)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--module-name", default="Main_Modularization_Model")
    parser.add_argument(
        "--graph-json",
        type=Path,
        default=Path("outputs/m2_3_main_model_multigranular_carbon_kg/multigranular_carbon_kg.json"),
    )
    parser.add_argument("--p-missing-quantity", type=float, default=0.0)
    parser.add_argument("--p-missing-factor", type=float, default=0.0)
    parser.add_argument("--p-unit-mismatch", type=float, default=0.0)
    parser.add_argument("--p-unmatched-target", type=float, default=0.0)
    parser.add_argument("--p-missing-allocation-basis", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = FactorySynthConfig(
        seed=args.seed,
        modules=args.modules,
        module_types=args.module_types,
        production_lines=args.production_lines,
        workstations=args.workstations,
        batches=args.batches,
        module_name=args.module_name,
        graph_json=args.graph_json,
        p_missing_quantity=args.p_missing_quantity,
        p_missing_factor=args.p_missing_factor,
        p_unit_mismatch=args.p_unit_mismatch,
        p_unmatched_target=args.p_unmatched_target,
        p_missing_allocation_basis=args.p_missing_allocation_basis,
    )
    dataset = generate_factory_dataset(config)
    outputs = write_factory_dataset_outputs(dataset, args.out_dir)
    print(json.dumps({"summary": dataset["summary"], "validation": dataset["validation"], "outputs": outputs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
