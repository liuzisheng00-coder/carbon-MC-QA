#!/usr/bin/env python3
"""Promote hash-equivalent E5/E6 results after the E4 benchmark is frozen."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_main_csv(path: Path, overall: Mapping[str, Mapping[str, Any]]) -> None:
    rows = [{"variant": variant, **metrics} for variant, metrics in overall.items()]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def promote_qa_results(
    *,
    e4_summary_path: Path,
    run_benchmark_path: Path,
    comparison_report_path: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    e4 = _read_json(e4_summary_path)
    comparison = _read_json(comparison_report_path)
    frozen_path = Path(e4["outputs"]["balanced_jsonl"])
    frozen_hash = _sha256(frozen_path)
    run_hash = _sha256(run_benchmark_path)
    if frozen_hash != run_hash:
        raise ValueError("Benchmark hash mismatch blocks paper-ready promotion.")
    if not e4.get("paper_ready_benchmark"):
        raise ValueError("E4 benchmark is not marked paper-ready.")

    overall = comparison.get("overall") or {}
    if not overall:
        raise ValueError("QA comparison has no variants.")
    incomplete = {
        variant: metrics
        for variant, metrics in overall.items()
        if int(metrics.get("case_count") or 0) != 150
        or int(metrics.get("transport_error_count") or 0) != 0
    }
    if incomplete:
        raise ValueError(f"Incomplete or transport-failed QA variants: {sorted(incomplete)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    main_csv = output_dir / "paper_ready_qa_main_comparison.csv"
    status_csv = output_dir / "paper_ready_qa_metrics_by_status.csv"
    _write_main_csv(main_csv, overall)
    source_status_value = str(
        (comparison.get("outputs") or {}).get("by_status_csv") or ""
    )
    source_status_csv = Path(source_status_value) if source_status_value else None
    if source_status_csv is not None and source_status_csv.is_file():
        shutil.copy2(source_status_csv, status_csv)
    else:
        status_rows = comparison.get("by_expected_status") or []
        if status_rows:
            with status_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(status_rows[0]))
                writer.writeheader()
                writer.writerows(status_rows)
        else:
            status_csv.write_text("", encoding="utf-8")

    report = {
        "experiment": "E5_E6_paper_ready_promotion",
        "generated_at": time.time(),
        "paper_ready": True,
        "promotion_basis": (
            "The frozen human-reviewed V7 benchmark is byte-identical to the V7 "
            "benchmark used by the saved E5/E6 observations. Metrics were rescored "
            "with the current shared evaluator."
        ),
        "benchmark_equivalence": {
            "frozen_benchmark": str(frozen_path.resolve()),
            "run_benchmark": str(run_benchmark_path.resolve()),
            "frozen_sha256": frozen_hash,
            "run_sha256": run_hash,
            "hash_equal": True,
        },
        "validation": {
            "variant_count": len(overall),
            "case_count_per_variant": 150,
            "case_result_count": int(comparison.get("case_result_count") or 0),
            "transport_error_count": sum(
                int(metrics.get("transport_error_count") or 0)
                for metrics in overall.values()
            ),
            "scoring_policy": "current shared evaluator rescoring saved observations",
        },
        "overall": overall,
        "by_expected_status": comparison.get("by_expected_status") or [],
        "inputs": {
            "e4_summary": str(e4_summary_path.resolve()),
            "comparison_report": str(comparison_report_path.resolve()),
        },
        "outputs": {
            "main_csv": str(main_csv.resolve()),
            "by_status_csv": str(status_csv.resolve()),
        },
    }
    report_path = output_dir / "paper_ready_qa_report.json"
    report["outputs"]["report"] = str(report_path.resolve())
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    markdown = [
        "# Paper-ready E5/E6 QA results",
        "",
        f"- Variants: {len(overall)}",
        "- Cases per variant: 150",
        "- Transport errors: 0",
        f"- Frozen/run benchmark SHA-256: `{frozen_hash}`",
        "",
        "| Variant | Numeric | Status | Unsupported | Hallucinated reference | Pass |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variant, metrics in overall.items():
        markdown.append(
            "| {variant} | {numeric} | {status} | {unsupported} | "
            "{hallucinated} | {passed} |".format(
                variant=variant,
                numeric=metrics.get("numeric_accuracy", ""),
                status=metrics.get("status_accuracy", ""),
                unsupported=metrics.get("unsupported_answer_rate", ""),
                hallucinated=metrics.get("hallucinated_reference_rate", ""),
                passed=metrics.get("pass_rate", ""),
            )
        )
    (output_dir / "paper_ready_qa_report.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--e4-summary",
        type=Path,
        default=Path(
            "outputs/research_experiments/e4_benchmark/"
            "latest_e4_benchmark_summary.json"
        ),
    )
    parser.add_argument("--run-benchmark", type=Path, required=True)
    parser.add_argument(
        "--comparison-report",
        type=Path,
        default=Path(
            "outputs/research_experiments/qa_comparison/"
            "latest_qa_comparison_report.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/research_experiments/paper_ready_qa_v7"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    report = promote_qa_results(
        e4_summary_path=args.e4_summary,
        run_benchmark_path=args.run_benchmark,
        comparison_report_path=args.comparison_report,
        output_dir=args.output_dir,
    )
    print(json.dumps({"paper_ready": report["paper_ready"], **report["outputs"]}, indent=2))


if __name__ == "__main__":
    main()
