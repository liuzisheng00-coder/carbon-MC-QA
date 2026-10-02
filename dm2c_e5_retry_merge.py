#!/usr/bin/env python3
"""Merge targeted E5 transport retries and rescore against a frozen benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from dm2c_e5_e7_experiment_runner import (
    evaluate_benchmark_response,
    load_benchmark_cases,
    summarize_results,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--base-results", type=Path, required=True)
    parser.add_argument("--retry-results", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    cases = load_benchmark_cases(args.benchmark)
    base = {row["case_id"]: row for row in read_jsonl(args.base_results)}
    retries = {row["case_id"]: row for row in read_jsonl(args.retry_results)}
    missing = [case.case_id for case in cases if case.case_id not in base]
    if missing:
        raise RuntimeError(f"Base E5 results are missing {len(missing)} benchmark cases.")

    merged = []
    transport_errors = []
    for index, case in enumerate(cases, start=1):
        source = retries.get(case.case_id, base[case.case_id])
        observed = dict(source.get("observed") or {})
        evaluated = evaluate_benchmark_response(case, observed, "full_real_v9_merged")
        evaluated["case_index"] = index
        evaluated["m31"] = source.get("m31")
        evaluated["m32"] = source.get("m32")
        evaluated["m33"] = source.get("m33")
        evaluated["observation_source"] = "targeted_retry" if case.case_id in retries else "base_run"
        merged.append({"variant": "full_real_v9_merged", **evaluated})
        errors = observed.get("llm_errors") or {}
        if any(str(value or "").strip() for value in errors.values()):
            transport_errors.append({"case_id": case.case_id, "errors": errors})

    metrics = summarize_results(merged)
    failed_case_ids = [row["case_id"] for row in merged if not row["scores"]["passed"]]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    merged_path = args.out_dir / "e5_full_real_v9_merged_cases.jsonl"
    merged_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in merged) + "\n",
        encoding="utf-8",
    )
    report = {
        "experiment": "E5_full_real_V9_with_targeted_transport_retry",
        "benchmark": str(args.benchmark),
        "benchmark_sha256": sha256(args.benchmark),
        "base_results": str(args.base_results),
        "base_results_sha256": sha256(args.base_results),
        "retry_results": str(args.retry_results),
        "retry_results_sha256": sha256(args.retry_results),
        "retry_case_ids": sorted(retries),
        "case_count": len(merged),
        "metrics": metrics,
        "transport_error_count": len(transport_errors),
        "transport_errors": transport_errors,
        "failed_case_count": len(failed_case_ids),
        "failed_case_ids": failed_case_ids,
        "paper_ready_e5": (
            len(merged) == 150
            and not transport_errors
            and not failed_case_ids
            and metrics.get("numeric_accuracy") == 1.0
            and metrics.get("status_accuracy") == 1.0
            and metrics.get("slot_accuracy") == 1.0
            and metrics.get("unsupported_answer_rate") == 0.0
            and metrics.get("hallucinated_reference_rate") == 0.0
        ),
        "merged_cases": str(merged_path),
    }
    report_path = args.out_dir / "e5_full_real_v9_merged_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
