#!/usr/bin/env python3
"""Fill E1 worksheets by recomputing from exported quantity/factor/share.

This is a pipeline-arithmetic identity check, not a human IFC take-off. The
reviewer columns are marked accordingly. Independent human verification remains
open for the manuscript claim in Section 4.1.2.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _f(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    return float(text)


def fill_material(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0].keys()) if rows else []
    for row in rows:
        qty = _f(row.get("quantity_value"))
        factor = _f(row.get("factor_value"))
        share = _f(row.get("allocation_fraction") or "1")
        if share == 0.0 and str(row.get("allocation_fraction") or "").strip() == "":
            share = 1.0
        row["independent_quantity_value"] = row.get("quantity_value") or ""
        row["independent_factor_value"] = row.get("factor_value") or ""
        row["independent_kgCO2e"] = f"{qty * factor * share:.12g}"
        row["reviewer"] = "pipeline_identity_autofill"
        row["notes"] = (
            "Restates exported quantity/factor/share; not an independent IFC take-off."
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def fill_energy(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0].keys()) if rows else []
    for row in rows:
        qty = _f(row.get("quantity_value"))
        factor = _f(row.get("factor_value"))
        # Energy worksheet records full carrier/stage carbon; attributed_fraction
        # is informational for product path, not a multiplier for process rows.
        row["independent_quantity_value"] = row.get("quantity_value") or ""
        row["independent_factor_value"] = row.get("factor_value") or ""
        row["independent_kgCO2e"] = f"{qty * factor:.12g}"
        row["reviewer"] = "pipeline_identity_autofill"
        row["notes"] = (
            "Restates exported quantity/factor; not an independent meter take-off."
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=Path(
            "outputs/research_experiments/"
            "e1_sample_m2_typea1_full_en_layerdedup_20260731"
        ),
    )
    args = parser.parse_args()
    material = args.sample_dir / "e1_material_worksheet.csv"
    energy = args.sample_dir / "e1_energy_worksheet.csv"
    n_m = fill_material(material)
    n_e = fill_energy(energy)
    print(f"filled material={n_m} energy={n_e} under {args.sample_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
