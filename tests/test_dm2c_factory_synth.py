import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

from dm2c_factory_synth import (
    FactorySynthConfig,
    export_factory_workbook,
    generate_factory_dataset,
    validate_factory_dataset,
)


class FactorySynthTests(unittest.TestCase):
    def test_generate_dataset_meets_minimum_scale_and_allocation_bases(self):
        dataset = generate_factory_dataset(FactorySynthConfig(seed=42))
        validation = validate_factory_dataset(dataset)

        self.assertEqual(validation["violation_count"], 0)
        self.assertGreaterEqual(len(dataset["modules"]), 4)
        self.assertGreaterEqual(len({row["module_type"] for row in dataset["modules"]}), 2)
        self.assertGreaterEqual(len(dataset["production_lines"]), 2)
        self.assertGreaterEqual(len(dataset["workstations"]), 6)
        self.assertGreaterEqual(len(dataset["batches"]), 8)
        self.assertGreaterEqual(len(dataset["energy_records"]), 8)
        self.assertTrue({"electricity", "diesel"}.issubset(set(dataset["summary"]["energy_carriers"])))
        self.assertEqual(
            {"factory_month", "line", "workstation"},
            set(dataset["summary"]["metering_levels"]),
        )
        self.assertEqual(
            {"mass", "duration", "machine_hours"},
            set(dataset["allocation_bases"].keys()),
        )

        for basis, rows in dataset["allocation_bases"].items():
            totals = defaultdict(float)
            for row in rows:
                totals[row["record_id"]] += float(row["allocation_fraction"])
                self.assertEqual(row["allocation_key"], basis)
                self.assertGreater(float(row["allocation_value"]), 0.0)
            self.assertTrue(totals, f"{basis} has no allocation sets")
            for total in totals.values():
                self.assertAlmostEqual(total, 1.0, places=8)

    def test_generator_is_seed_reproducible(self):
        config = FactorySynthConfig(seed=7)

        first = generate_factory_dataset(config)
        second = generate_factory_dataset(config)

        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )

    def test_measured_module_month_calibration_preserves_metering_split(self):
        dataset = generate_factory_dataset(FactorySynthConfig(seed=42))
        records = dataset["energy_records"]
        totals = defaultdict(float)
        electricity_levels = defaultdict(float)
        diesel_levels = defaultdict(float)
        for row in records:
            carrier = str(row["energy_carrier"])
            quantity = float(row["quantity_value"])
            totals[carrier] += quantity
            if carrier == "electricity":
                level = (
                    "factory"
                    if row["record_role"] == "shared_factory_meter"
                    else "line"
                    if row["record_role"] == "shared_line_meter"
                    else "workstation"
                    if row["record_role"] == "shared_workstation_meter"
                    else "module"
                    if row["record_role"] == "direct_module_log"
                    else "component"
                )
                electricity_levels[level] += quantity
            else:
                diesel_levels["line" if row["record_role"] == "shared_line_meter" else "workstation"] += quantity

        self.assertEqual(len(records), 17)
        self.assertAlmostEqual(totals["electricity"], 4 * 418.86294516536475, delta=5.0)
        self.assertAlmostEqual(totals["diesel"], 4 * 39.49561449931906, delta=1.0)
        for level, expected in {
            "factory": 0.5677,
            "line": 0.1798,
            "workstation": 0.1628,
            "module": 0.0742,
            "component": 0.0155,
        }.items():
            self.assertAlmostEqual(electricity_levels[level] / totals["electricity"], expected, delta=0.005)
        self.assertAlmostEqual(diesel_levels["line"] / totals["diesel"], 0.6938, delta=0.005)
        self.assertAlmostEqual(diesel_levels["workstation"] / totals["diesel"], 0.3062, delta=0.005)

    @unittest.skipIf(openpyxl is None, "openpyxl is not installed")
    def test_export_workbook_uses_kg_builder_sheet_names_and_headers(self):
        dataset = generate_factory_dataset(FactorySynthConfig(seed=3))

        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "factory_synth_mass.xlsx"
            export_factory_workbook(dataset, workbook_path, allocation_basis="mass")

            workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
            self.assertEqual(
                {"Factory energy records", "Batch allocation records", "Source metadata"},
                set(workbook.sheetnames),
            )
            energy_headers = [cell.value for cell in workbook["Factory energy records"][1]]
            allocation_headers = [cell.value for cell in workbook["Batch allocation records"][1]]
            source_headers = [cell.value for cell in workbook["Source metadata"][1]]
            workbook.close()

        self.assertTrue(
            {
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
            }.issubset(set(energy_headers))
        )
        self.assertTrue(
            {
                "record_id",
                "target_id",
                "target_level",
                "batch_id",
                "allocation_key",
                "allocation_value",
                "allocation_unit",
                "allocation_fraction",
            }.issubset(set(allocation_headers))
        )
        self.assertTrue({"source_id", "source_name", "source_type", "note"}.issubset(set(source_headers)))

    def test_defect_injection_marks_expected_rows(self):
        missing_quantity = generate_factory_dataset(
            FactorySynthConfig(seed=11, p_missing_quantity=1.0)
        )
        self.assertTrue(
            any(row["quantity_value"] in ("", 0, 0.0) for row in missing_quantity["energy_records"])
        )

        unmatched_target = generate_factory_dataset(
            FactorySynthConfig(seed=11, p_unmatched_target=1.0)
        )
        self.assertTrue(
            any(
                str(row["target_id"]).startswith("UNMATCHED_")
                for row in unmatched_target["energy_records"]
                if row["target_level"] != "unknown"
            )
        )

        missing_basis = generate_factory_dataset(
            FactorySynthConfig(seed=11, p_missing_allocation_basis=1.0)
        )
        self.assertTrue(
            any(
                float(row["allocation_value"]) == 0.0
                for rows in missing_basis["allocation_bases"].values()
                for row in rows
            )
        )


if __name__ == "__main__":
    unittest.main()
