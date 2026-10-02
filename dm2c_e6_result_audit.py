#!/usr/bin/env python3
"""Audit a completed E6 run against the frozen benchmark and E5 full system."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from dm2c_e5_e7_experiment_runner import load_benchmark_metadata, summarize_results


COMPARISON_METRICS = (
    "numeric_accuracy",
    "status_accuracy",
    "slot_accuracy",
    "answer_contains_rate",
    "provenance_supported_rate",
    "unsupported_answer_rate",
    "hallucinated_reference_rate",
    "pass_rate",
)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _has_transport_error(row: Mapping[str, Any]) -> bool:
    errors = ((row.get("observed") or {}).get("llm_errors") or {}).values()
    return any(str(value).strip() for value in errors)


def _false_score_ids(rows: Iterable[Mapping[str, Any]], key: str) -> list[str]:
    return [
        str(row.get("case_id") or "")
        for row in rows
        if (row.get("scores") or {}).get(key) is False
    ]


def _delta_vs_full(
    metrics: Mapping[str, Any], full_metrics: Mapping[str, Any]
) -> Dict[str, float | None]:
    delta: Dict[str, float | None] = {}
    for key in COMPARISON_METRICS:
        current = metrics.get(key)
        full = full_metrics.get(key)
        delta[key] = (
            round(float(current) - float(full), 4)
            if current is not None and full is not None
            else None
        )
    return delta


def build_e6_audit(e6_summary_path: Path, e5_report_path: Path) -> Dict[str, Any]:
    e6_summary = _read_json(e6_summary_path)
    e5_report = _read_json(e5_report_path)
    case_path = Path(str(e6_summary["case_jsonl"]))
    rows = _read_jsonl(case_path)
    benchmark_path = Path(str(e6_summary["benchmark_path"]))
    benchmark_metadata = load_benchmark_metadata(benchmark_path)
    target_case_count = int(e6_summary.get("case_count") or 0)

    expected_variants = list((e6_summary.get("variants") or {}).keys())
    variants: Dict[str, Any] = {}
    for variant in expected_variants:
        variant_rows = [row for row in rows if row.get("variant") == variant]
        metrics = summarize_results(variant_rows)
        transport_ids = [
            str(row.get("case_id") or "")
            for row in variant_rows
            if _has_transport_error(row)
        ]
        case_ids = [str(row.get("case_id") or "") for row in variant_rows]
        variants[variant] = {
            "metrics": metrics,
            "delta_vs_full": _delta_vs_full(metrics, e5_report.get("metrics") or {}),
            "unique_case_count": len(set(case_ids)),
            "transport_error_case_ids": transport_ids,
            "slot_mismatch_case_ids": _false_score_ids(variant_rows, "slot_match"),
            "status_mismatch_case_ids": _false_score_ids(variant_rows, "status_match"),
            "numeric_mismatch_case_ids": _false_score_ids(variant_rows, "numeric_match"),
            "answer_constraint_failure_case_ids": _false_score_ids(
                variant_rows, "answer_contains"
            ),
            "unsupported_answer_case_ids": [
                str(row.get("case_id") or "")
                for row in variant_rows
                if (row.get("scores") or {}).get("unsupported_answer") is True
            ],
            "hallucinated_reference_case_ids": [
                str(row.get("case_id") or "")
                for row in variant_rows
                if row.get("hallucinated_evidence_ids")
            ],
            "failed_case_ids": _false_score_ids(variant_rows, "passed"),
        }

    integrity_ok = all(
        payload["metrics"]["case_count"] == target_case_count
        and payload["unique_case_count"] == target_case_count
        and not payload["transport_error_case_ids"]
        for payload in variants.values()
    )
    paper_ready = bool(
        benchmark_metadata.get("paper_ready_benchmark")
        and e5_report.get("paper_ready_e5")
        and int(e5_report.get("case_count") or 0) == target_case_count
        and len(variants) == len(expected_variants)
        and integrity_ok
    )
    return {
        "experiment": "E6_real_behavior_ablation_V9_audit",
        "raw_e6_summary": str(e6_summary_path),
        "e5_full_report": str(e5_report_path),
        "benchmark_path": str(benchmark_path),
        "benchmark_metadata": benchmark_metadata,
        "case_count": target_case_count,
        "variant_count": len(variants),
        "full_system_metrics": e5_report.get("metrics") or {},
        "variants": variants,
        "integrity_ok": integrity_ok,
        "paper_ready_e6": paper_ready,
    }


def _write_outputs(report: Mapping[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "e6_v9_paper_ready_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metric_names = ["variant", *COMPARISON_METRICS, "transport_error_count"]
    with (out_dir / "e6_v9_paper_ready_metrics.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_names)
        writer.writeheader()
        full = report.get("full_system_metrics") or {}
        writer.writerow(
            {
                "variant": "full_real",
                **{key: full.get(key) for key in COMPARISON_METRICS},
                "transport_error_count": 0,
            }
        )
        for variant, payload in (report.get("variants") or {}).items():
            metrics = payload.get("metrics") or {}
            writer.writerow(
                {
                    "variant": variant,
                    **{key: metrics.get(key) for key in COMPARISON_METRICS},
                    "transport_error_count": len(
                        payload.get("transport_error_case_ids") or []
                    ),
                }
            )
    lines = [
        "# E6 V9 Real Ablation Audit",
        "",
        f"- Cases per variant: {report['case_count']}",
        f"- Variants: {report['variant_count']}",
        f"- Integrity passed: {report['integrity_ok']}",
        f"- Paper-ready E6: {report['paper_ready_e6']}",
        "",
        "| Variant | Numeric | Status | Unsupported | Hallucinated refs | Pass |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    full = report.get("full_system_metrics") or {}
    lines.append(
        "| full_real | {numeric_accuracy} | {status_accuracy} | "
        "{unsupported_answer_rate} | {hallucinated_reference_rate} | {pass_rate} |".format(
            **full
        )
    )
    for variant, payload in (report.get("variants") or {}).items():
        metrics = payload["metrics"]
        lines.append(
            "| {variant} | {numeric_accuracy} | {status_accuracy} | "
            "{unsupported_answer_rate} | {hallucinated_reference_rate} | {pass_rate} |".format(
                variant=variant, **metrics
            )
        )
    (out_dir / "e6_v9_paper_ready_audit.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e6-summary", type=Path, required=True)
    parser.add_argument("--e5-report", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_e6_audit(args.e6_summary, args.e5_report)
    _write_outputs(report, args.out_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
