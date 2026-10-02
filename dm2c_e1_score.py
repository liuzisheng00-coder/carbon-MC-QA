"""Score the E1 independent hand-calculation worksheets against the pipeline.

E1 is the independent-verification experiment for Section 4.1.2. A reviewer
returns to the IFC model and the published factor source, establishes the
quantity, factor and allocation share of a sampled record, computes its
emission by hand, and records the result in a worksheet. This script compares
each hand-computed emission (`independent_kgCO2e`) against the value the
pipeline produced for the same record, held out of the reviewer's sight in the
answer key.

It reports, separately for the material and energy samples and for the two
combined:

- coverage: how many sampled rows the reviewer actually filled;
- agreement: the relative error of each reviewed row against the pipeline, and
  the pass rate at a tolerance;
- aggregate agreement: the summed hand-computed carbon against the summed
  pipeline carbon over the reviewed rows;
- two transparency diagnostics that bear on how strong the verification is:
  whether the reviewer's own arithmetic is self-consistent
  (`independent_kgCO2e` vs `independent_quantity_value x
  independent_factor_value`), and how many rows re-state the exported quantity
  and factor verbatim rather than recomputing them from source. Neither
  diagnostic is a pass/fail gate; both are surfaced so the reader can judge the
  independence of the check.

The worksheets are read with an auto-detected delimiter, because a reviewer
editing in a spreadsheet may save them semicolon-separated when material names
contain commas.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE_DIR = (
    REPO_ROOT
    / "outputs/research_experiments/e1_sample_m2_typed_completed_20260728_layerdedup"
)


@dataclass
class RowScore:
    row_id: str
    kind: str
    reviewed: bool
    system_kgCO2e: Optional[float]
    independent_kgCO2e: Optional[float]
    abs_error: Optional[float]
    rel_error: Optional[float]
    within_tolerance: Optional[bool]
    arithmetic_consistent: Optional[bool]
    restates_exported_inputs: Optional[bool]
    note: str = ""


def _read_rows(path: Path) -> List[Dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    return [dict(row) for row in reader]


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _score_row(
    row: Dict[str, str],
    key_entry: Optional[Dict[str, Any]],
    kind: str,
    tolerance: float,
    exported_quantity_col: str,
    exported_factor_col: str,
) -> RowScore:
    row_id = (row.get("row_id") or "").strip()
    system = key_entry.get("system_kgCO2e") if key_entry else None
    independent = _to_float(row.get("independent_kgCO2e"))
    reviewed = independent is not None

    if not reviewed or system is None:
        note = "not reviewed" if not reviewed else "no answer-key entry"
        return RowScore(
            row_id=row_id,
            kind=kind,
            reviewed=reviewed,
            system_kgCO2e=system,
            independent_kgCO2e=independent,
            abs_error=None,
            rel_error=None,
            within_tolerance=None,
            arithmetic_consistent=None,
            restates_exported_inputs=None,
            note=note,
        )

    abs_error = abs(independent - system)
    rel_error = abs_error / abs(system) if system != 0 else (0.0 if abs_error == 0 else math.inf)
    within = rel_error <= tolerance

    ind_qty = _to_float(row.get("independent_quantity_value"))
    ind_factor = _to_float(row.get("independent_factor_value"))
    arithmetic_consistent: Optional[bool] = None
    if ind_qty is not None and ind_factor is not None:
        product = ind_qty * ind_factor
        arithmetic_consistent = abs(product - independent) <= max(1e-6, 1e-4 * abs(independent))

    exp_qty = _to_float(row.get(exported_quantity_col))
    exp_factor = _to_float(row.get(exported_factor_col))
    restates: Optional[bool] = None
    if None not in (ind_qty, ind_factor, exp_qty, exp_factor):
        restates = (
            abs(ind_qty - exp_qty) <= 1e-9 and abs(ind_factor - exp_factor) <= 1e-9
        )

    return RowScore(
        row_id=row_id,
        kind=kind,
        reviewed=True,
        system_kgCO2e=system,
        independent_kgCO2e=independent,
        abs_error=abs_error,
        rel_error=rel_error,
        within_tolerance=within,
        arithmetic_consistent=arithmetic_consistent,
        restates_exported_inputs=restates,
        note="",
    )


def _summarise(scores: List[RowScore], tolerance: float) -> Dict[str, Any]:
    reviewed = [s for s in scores if s.reviewed and s.rel_error is not None]
    rel_errors = [s.rel_error for s in reviewed if s.rel_error is not None]
    passed = [s for s in reviewed if s.within_tolerance]
    sys_sum = sum(s.system_kgCO2e for s in reviewed if s.system_kgCO2e is not None)
    ind_sum = sum(s.independent_kgCO2e for s in reviewed if s.independent_kgCO2e is not None)
    aggregate_rel = abs(ind_sum - sys_sum) / abs(sys_sum) if sys_sum else None
    arithmetic = [s for s in reviewed if s.arithmetic_consistent is not None]
    restating = [s for s in reviewed if s.restates_exported_inputs]
    return {
        "sampled": len(scores),
        "reviewed": len(reviewed),
        "tolerance": tolerance,
        "passed": len(passed),
        "pass_rate": (len(passed) / len(reviewed)) if reviewed else None,
        "max_rel_error": max(rel_errors) if rel_errors else None,
        "mean_rel_error": mean(rel_errors) if rel_errors else None,
        "median_rel_error": median(rel_errors) if rel_errors else None,
        "system_sum_kgCO2e": sys_sum,
        "independent_sum_kgCO2e": ind_sum,
        "aggregate_rel_error": aggregate_rel,
        "arithmetic_consistent": sum(1 for s in arithmetic if s.arithmetic_consistent),
        "arithmetic_checked": len(arithmetic),
        "rows_restating_exported_inputs": len(restating),
    }


def _worst(scores: List[RowScore], limit: int = 5) -> List[Dict[str, Any]]:
    reviewed = [s for s in scores if s.reviewed and s.rel_error is not None]
    reviewed.sort(key=lambda s: s.rel_error, reverse=True)
    return [asdict(s) for s in reviewed[:limit]]


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.4f}%"


def _fmt_num(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.3f}"


def _markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# E1 independent verification score")
    lines.append("")
    lines.append(f"Release `{report['release_id']}`, tolerance {report['tolerance'] * 100:.2f} per cent relative error.")
    lines.append("")
    lines.append("| Sample | Sampled | Reviewed | Passed | Pass rate | Max rel. error | Mean rel. error | Aggregate rel. error |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, key in (("Material", "material"), ("Energy", "energy"), ("Combined", "combined")):
        s = report["summary"][key]
        lines.append(
            f"| {label} | {s['sampled']} | {s['reviewed']} | {s['passed']} | "
            f"{_fmt_pct(s['pass_rate'])} | {_fmt_pct(s['max_rel_error'])} | "
            f"{_fmt_pct(s['mean_rel_error'])} | {_fmt_pct(s['aggregate_rel_error'])} |"
        )
    lines.append("")
    combined = report["summary"]["combined"]
    lines.append(
        f"Over the reviewed sample the hand-computed carbon sums to "
        f"{_fmt_num(combined['independent_sum_kgCO2e'])} kgCO2e against a pipeline sum of "
        f"{_fmt_num(combined['system_sum_kgCO2e'])} kgCO2e."
    )
    lines.append("")
    lines.append(
        f"Reviewer arithmetic self-consistent in {combined['arithmetic_consistent']} of "
        f"{combined['arithmetic_checked']} checked rows. "
        f"{combined['rows_restating_exported_inputs']} reviewed rows re-state the exported "
        f"quantity and factor verbatim rather than recomputing them from source; the strength "
        f"of the check on those rows rests on the emission arithmetic, not on an independent "
        f"quantity take-off."
    )
    worst = report["worst_rows"]["combined"]
    if worst:
        lines.append("")
        lines.append("Largest relative errors:")
        lines.append("")
        lines.append("| Row | Kind | System kgCO2e | Independent kgCO2e | Rel. error |")
        lines.append("| --- | --- | ---: | ---: | ---: |")
        for row in worst:
            lines.append(
                f"| {row['row_id']} | {row['kind']} | {_fmt_num(row['system_kgCO2e'])} | "
                f"{_fmt_num(row['independent_kgCO2e'])} | {_fmt_pct(row['rel_error'])} |"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material", type=Path, default=REPO_ROOT / "e1_material_worksheet.csv")
    parser.add_argument("--energy", type=Path, default=REPO_ROOT / "e1_energy_worksheet.csv")
    parser.add_argument("--answer-key", type=Path, default=DEFAULT_SAMPLE_DIR / "e1_answer_key.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_SAMPLE_DIR / "e1_sample_manifest.json")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--tolerance", type=float, default=0.01, help="relative error pass threshold")
    args = parser.parse_args()

    answer_key: Dict[str, Any] = json.loads(args.answer_key.read_text(encoding="utf-8"))
    release_id = "unknown"
    if args.manifest.is_file():
        release_id = json.loads(args.manifest.read_text(encoding="utf-8")).get("releaseId", "unknown")

    material_rows = _read_rows(args.material)
    energy_rows = _read_rows(args.energy)

    material_scores = [
        _score_row(row, answer_key.get((row.get("row_id") or "").strip()), "material", args.tolerance, "quantity_value", "factor_value")
        for row in material_rows
    ]
    energy_scores = [
        _score_row(row, answer_key.get((row.get("row_id") or "").strip()), "energy", args.tolerance, "quantity_value", "factor_value")
        for row in energy_rows
    ]
    combined_scores = material_scores + energy_scores

    report = {
        "release_id": release_id,
        "tolerance": args.tolerance,
        "answer_key": str(args.answer_key),
        "worksheets": {"material": str(args.material), "energy": str(args.energy)},
        "summary": {
            "material": _summarise(material_scores, args.tolerance),
            "energy": _summarise(energy_scores, args.tolerance),
            "combined": _summarise(combined_scores, args.tolerance),
        },
        "worst_rows": {
            "material": _worst(material_scores),
            "energy": _worst(energy_scores),
            "combined": _worst(combined_scores),
        },
        "rows": [asdict(s) for s in combined_scores],
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "e1_score_report.json"
    md_path = args.out_dir / "e1_score_report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")

    combined = report["summary"]["combined"]
    print(f"release: {release_id}")
    print(f"reviewed {combined['reviewed']}/{combined['sampled']} rows at {args.tolerance * 100:.2f}% tolerance")
    print(f"pass rate: {_fmt_pct(combined['pass_rate'])} ({combined['passed']} passed)")
    print(f"max rel error: {_fmt_pct(combined['max_rel_error'])}, mean: {_fmt_pct(combined['mean_rel_error'])}")
    print(f"aggregate rel error: {_fmt_pct(combined['aggregate_rel_error'])}")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
