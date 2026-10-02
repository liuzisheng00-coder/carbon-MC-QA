#!/usr/bin/env python3
"""Aggregate QA case-level outputs into overall and status-stratified metrics."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from dm2c_e5_e7_experiment_runner import BenchmarkCase, evaluate_benchmark_response


UNANSWERABLE_STATUSES = {
    "empty_result",
    "unresolved_target",
    "incomplete_path",
    "clarification_required",
}


def _rate(rows: Sequence[Mapping[str, Any]], score_key: str) -> float | None:
    values = [
        row.get("scores", {}).get(score_key)
        for row in rows
        if row.get("scores", {}).get(score_key) is not None
    ]
    if not values:
        return None
    return round(sum(1 for value in values if value is True) / len(values), 4)


def _has_transport_error(row: Mapping[str, Any]) -> bool:
    observed = row.get("observed") or {}
    if str(observed.get("transport_error") or "").strip():
        return True
    return any(
        str(value).strip()
        for value in (observed.get("llm_errors") or {}).values()
    )


def _summarize_group(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    case_count = len(rows)
    unanswerable = [
        row
        for row in rows
        if str((row.get("expected") or {}).get("status") or "") in UNANSWERABLE_STATUSES
    ]
    return {
        "case_count": case_count,
        "numeric_accuracy": _rate(rows, "numeric_match"),
        "numeric_accuracy_keyfree": _rate(rows, "numeric_match_keyfree"),
        "status_accuracy": _rate(rows, "status_match"),
        "slot_accuracy": _rate(rows, "slot_match"),
        "answer_contains_rate": _rate(rows, "answer_contains"),
        "provenance_supported_rate": _rate(rows, "provenance_supported"),
        "unsupported_answer_rate": round(
            sum(1 for row in rows if row.get("scores", {}).get("unsupported_answer") is True)
            / case_count,
            4,
        ) if case_count else None,
        "hallucinated_reference_rate": round(
            sum(1 for row in rows if row.get("hallucinated_evidence_ids")) / case_count,
            4,
        ) if case_count else None,
        "pass_rate": _rate(rows, "passed"),
        "missing_data_disclosure_rate": _rate(unanswerable, "answer_contains"),
        "transport_error_count": sum(1 for row in rows if _has_transport_error(row)),
        "mean_latency_ms": round(
            statistics.fmean(float(row.get("latency_ms") or 0.0) for row in rows),
            3,
        ) if rows else None,
    }


def summarize_case_results(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_variant: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    by_cell: Dict[tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        variant = str(row.get("variant") or (row.get("observed") or {}).get("variant") or "unknown")
        status = str((row.get("expected") or {}).get("status") or "unknown")
        by_variant[variant].append(row)
        by_cell[(variant, status)].append(row)

    overall = {
        variant: _summarize_group(group)
        for variant, group in sorted(by_variant.items())
    }
    by_expected_status = []
    for (variant, status), group in sorted(by_cell.items()):
        by_expected_status.append(
            {
                "variant": variant,
                "expected_status": status,
                **_summarize_group(group),
            }
        )
    return {
        "overall": overall,
        "by_expected_status": by_expected_status,
    }


def load_case_files(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (
                str(row.get("variant") or (row.get("observed") or {}).get("variant") or "unknown"),
                str(row.get("case_id") or ""),
            )
            if key in seen:
                raise ValueError(f"Duplicate variant/case pair across inputs: {key}")
            seen.add(key)
            rows.append(row)
    return rows


def rescore_case_results(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Re-evaluate saved observations with the current shared scorer."""
    rescored: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        case = BenchmarkCase.from_dict(dict(row), index=index)
        variant = str(
            row.get("variant")
            or (row.get("observed") or {}).get("variant")
            or "unknown"
        )
        evaluated = evaluate_benchmark_response(
            case,
            dict(row.get("observed") or {}),
            variant,
        )
        for key in ("case_index", "m31", "m32", "m33", "injection", "mutation"):
            if key in row:
                evaluated[key] = row[key]
        rescored.append(evaluated)
    return rescored


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_aggregate_report(case_files: Sequence[Path], out_dir: Path) -> Dict[str, Any]:
    rows = rescore_case_results(load_case_files(case_files))
    metrics = summarize_case_results(rows)
    run_id = f"qa_comparison_{time.strftime('%Y%m%d_%H%M%S')}"
    output_dir = out_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    overall_rows = [
        {"variant": variant, **values}
        for variant, values in metrics["overall"].items()
    ]
    overall_csv = output_dir / "qa_main_comparison.csv"
    status_csv = output_dir / "qa_metrics_by_status.csv"
    _write_csv(overall_csv, overall_rows)
    _write_csv(status_csv, metrics["by_expected_status"])
    report = {
        "run_id": run_id,
        "experiment_type": "E5_E6_QA_comparison",
        "generated_at": time.time(),
        "inputs": [
            {"path": str(path.resolve()), "sha256": _sha256(path)}
            for path in case_files
        ],
        "case_result_count": len(rows),
        **metrics,
        "outputs": {
            "overall_csv": str(overall_csv.resolve()),
            "by_status_csv": str(status_csv.resolve()),
        },
    }
    report_path = output_dir / "qa_comparison_report.json"
    report["outputs"]["report"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_dir / "latest_qa_comparison_report.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-file", action="append", type=Path, required=True)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("outputs/research_experiments/qa_comparison"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    report = write_aggregate_report(args.case_file, args.out_dir)
    print(json.dumps({"run_id": report["run_id"], "outputs": report["outputs"]}, indent=2))


if __name__ == "__main__":
    main()
