#!/usr/bin/env python3
"""Stage R1: read-only offline re-score of stored Stage-5 cases.

Does NOT modify production scorers or model outputs. Applies a documented
semantic-equivalence normalization to *copies* of stored `observed` payloads,
then re-applies the existing exact-match scorer helpers.

See PROMPT_section_4_2_repair.md Stage R1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dm2c_e5_e7_experiment_runner import (
    _answer_contains,
    _hallucinated_evidence,
    _numeric_close,
    _numeric_match,
    _rate,
    _slot_match,
    _status_match,
    _to_float,
    detect_unsupported_numeric_values,
    summarize_results,
)

PERSPECTIVE_ALIASES = {
    "material_source": "material",
    "energy_source": "process",
    "source_union": "product",
}

# Accepted product known-total on layerdedup (material + attributed process).
ACCEPTED_PRODUCT_KNOWN_TOTAL = 8197.695537310923
# Incomplete-path source_union figure that must NOT be treated as the known total.
REJECTED_UNION_TOTAL = 9314.855904910159

DEFAULT_FULL = (
    ROOT
    / "outputs/research_experiments/e5_full_real_layerdedup"
    / "stage5_full_v10_layerdedup_20260730_134845"
    / "e5_full_real_cases.jsonl"
)
DEFAULT_BASELINES = (
    ROOT
    / "outputs/research_experiments/e5_real_baselines_layerdedup"
    / "stage5_baselines_v10_layerdedup_baselineparserfix_20260730"
    / "e5_real_baseline_cases.jsonl"
)
DEFAULT_ABLATIONS = (
    ROOT
    / "outputs/research_experiments/e6_real_ablations_layerdedup"
    / "stage5_ablations_v10_layerdedup_20260730_134845"
    / "e6_real_ablation_cases.jsonl"
)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _numeric_keys(summary: Optional[Dict[str, Any]]) -> List[str]:
    summary = summary or {}
    return [
        key
        for key, value in summary.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _is_compare_multi_key(expected: Dict[str, Any]) -> bool:
    keys = set(_numeric_keys(expected.get("summary")))
    if expected.get("operation") != "compare":
        return False
    has_pair = any(k.startswith("first_") for k in keys) and any(
        k.startswith("second_") for k in keys
    )
    return has_pair and "difference_kgCO2e" in keys


def _is_project_known_total_cell(expected: Dict[str, Any]) -> bool:
    summary = expected.get("summary") or {}
    return (
        expected.get("perspective") == "product"
        and "knownTotalCarbon_kgCO2e" in summary
        and expected.get("status") == "executable"
    )


def _alias_perspective(perspective: Any) -> Tuple[Any, Optional[str]]:
    text = str(perspective or "")
    mapped = PERSPECTIVE_ALIASES.get(text)
    if mapped is None:
        return perspective, None
    return mapped, f"perspective_alias:{text}->{mapped}"


def normalize_observed(
    observed: Dict[str, Any],
    expected: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """Return (normalized_observed, applied_rules, meta).

    Rules are uniform and documented. They never invent compare entity values,
    never promote incomplete_path union totals to known totals, and never read
    gold to invent numeric values — only to choose the *key name* for an already
    present single total that matches the gold scalar within tolerance.
    """
    out = deepcopy(observed)
    rules: List[str] = []
    meta: Dict[str, Any] = {
        "compare_needs_reexecution": False,
        "rejected_union_as_known_total": False,
    }

    # 1) Perspective aliases (true account-view synonyms).
    new_pers, rule = _alias_perspective(out.get("perspective"))
    if rule:
        out["perspective"] = new_pers
        rules.append(rule)

    summary = dict(out.get("summary") or {})
    expected_summary = expected.get("summary") or {}
    numeric_keys = _numeric_keys(expected_summary)
    tolerance = float(expected.get("numeric_tolerance", 1e-4))
    total = _to_float(summary.get("total_kgCO2e"))

    # Guard: never accept the incomplete_path union figure as the project known total.
    if _is_project_known_total_cell(expected) and total is not None:
        if _numeric_close(total, REJECTED_UNION_TOTAL, tolerance) and str(
            out.get("status") or ""
        ) == "incomplete_path":
            meta["rejected_union_as_known_total"] = True
            # Do not remap keys for this total.
            out["summary"] = summary
            return out, rules, meta

    # 2) Single-value numeric equivalence: exactly one gold numeric key,
    #    and observed total_kgCO2e matches that scalar.
    if len(numeric_keys) == 1 and total is not None:
        gold_key = numeric_keys[0]
        gold_value = _to_float(expected_summary.get(gold_key))
        if gold_value is not None and _numeric_close(total, gold_value, tolerance):
            if gold_key not in summary:
                summary[gold_key] = total
                rules.append(f"single_value_key:{gold_key}")

    # 3) Project known-total: only when status is already executable and the
    #    observed total matches the accepted known total — remap the total key.
    if (
        _is_project_known_total_cell(expected)
        and str(out.get("status") or "") == "executable"
        and total is not None
        and _numeric_close(total, ACCEPTED_PRODUCT_KNOWN_TOTAL, tolerance)
    ):
        if "knownTotalCarbon_kgCO2e" not in summary:
            summary["knownTotalCarbon_kgCO2e"] = total
            rules.append("project_known_total_key:knownTotalCarbon_kgCO2e")

    # 4) Compare multi-key: cannot recover offline from total alone.
    if _is_compare_multi_key(expected):
        missing = [k for k in numeric_keys if k not in summary]
        if missing and total is not None:
            meta["compare_needs_reexecution"] = True
            rules.append("compare_needs_reexecution")

    out["summary"] = summary
    return out, rules, meta


def score_response(observed: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, Any]:
    unsupported_values = detect_unsupported_numeric_values(
        str(observed.get("answer", "")),
        expected,
        observed.get("supported_numeric_values") or [],
        observed.get("supported_text_tokens")
        or observed.get("supported_evidence_ids")
        or [],
    )
    hallucinated_evidence = _hallucinated_evidence(
        observed.get("evidence_ids") or [],
        expected,
        observed.get("supported_evidence_ids") or [],
    )
    scores = {
        "slot_match": _slot_match(observed, expected),
        "status_match": _status_match(observed, expected),
        "numeric_match": _numeric_match(observed.get("summary") or {}, expected),
        "answer_contains": _answer_contains(str(observed.get("answer", "")), expected),
        "provenance_supported": len(hallucinated_evidence) == 0,
    }
    scores["unsupported_answer"] = bool(unsupported_values or hallucinated_evidence)
    gating = {k: v for k, v in scores.items() if k != "unsupported_answer"}
    scores["passed"] = all(v is not False for v in gating.values()) and not scores[
        "unsupported_answer"
    ]
    return {
        "scores": scores,
        "unsupported_numeric_values": unsupported_values,
        "hallucinated_evidence_ids": hallucinated_evidence,
    }


def classify_case(
    *,
    raw_passed: bool,
    adjusted_passed: bool,
    observed_status: str,
    compare_needs_reexecution: bool,
    slot_ok_after: bool,
    status_ok_after: bool,
) -> str:
    if raw_passed:
        return "correct"
    if str(observed_status or "") == "not_executed":
        return "genuine_compile_fail"
    if (
        compare_needs_reexecution
        and slot_ok_after
        and status_ok_after
        and not adjusted_passed
    ):
        return "scorer_artifact_needs_reexecution"
    if adjusted_passed:
        return "scorer_artifact_recoverable"
    return "genuine_wrong_program"


def rescore_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_variant: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_variant[str(row.get("variant") or "unknown")].append(row)

    variant_reports: Dict[str, Any] = {}
    for variant, cases in sorted(by_variant.items()):
        adjusted_results: List[Dict[str, Any]] = []
        classifications: Counter = Counter()
        detailed: List[Dict[str, Any]] = []

        for row in cases:
            expected = row.get("expected") or {}
            observed = row.get("observed") or {}
            raw_scores = row.get("scores") or {}

            # Recompute raw with the same helpers for consistency (stored scores
            # should match; keep stored as authoritative RAW headline).
            recomputed_raw = score_response(observed, expected)
            normalized, rules, meta = normalize_observed(observed, expected)
            adjusted = score_response(normalized, expected)

            adjusted_row = {
                "case_id": row.get("case_id"),
                "variant": variant,
                "question": row.get("question"),
                "expected": expected,
                "observed": normalized,
                "scores": adjusted["scores"],
                "unsupported_numeric_values": adjusted["unsupported_numeric_values"],
                "hallucinated_evidence_ids": adjusted["hallucinated_evidence_ids"],
                "latency_ms": float(row.get("latency_ms") or observed.get("latency_ms") or 0.0),
                "tags": row.get("tags"),
            }
            adjusted_results.append(adjusted_row)

            slot_ok = adjusted["scores"].get("slot_match") is not False and (
                adjusted["scores"].get("slot_match") is True
                or adjusted["scores"].get("slot_match") is None
            )
            # For classification of compare recovery, require explicit True when expected has slot fields.
            slot_true = adjusted["scores"].get("slot_match") is True or (
                adjusted["scores"].get("slot_match") is None
                and "perspective" not in expected
                and "operation" not in expected
            )
            status_true = adjusted["scores"].get("status_match") is True or (
                adjusted["scores"].get("status_match") is None and "status" not in expected
            )

            klass = classify_case(
                raw_passed=bool(raw_scores.get("passed")),
                adjusted_passed=bool(adjusted["scores"].get("passed")),
                observed_status=str(observed.get("status") or ""),
                compare_needs_reexecution=bool(meta.get("compare_needs_reexecution")),
                slot_ok_after=bool(slot_true),
                status_ok_after=bool(status_true),
            )
            classifications[klass] += 1

            detailed.append(
                {
                    "case_id": row.get("case_id"),
                    "variant": variant,
                    "classification": klass,
                    "rules_applied": rules,
                    "meta": meta,
                    "raw_scores": raw_scores,
                    "recomputed_raw_scores": recomputed_raw["scores"],
                    "adjusted_scores": adjusted["scores"],
                    "expected_status": expected.get("status"),
                    "observed_status": observed.get("status"),
                    "expected_perspective": expected.get("perspective"),
                    "observed_perspective_raw": observed.get("perspective"),
                    "observed_perspective_adj": normalized.get("perspective"),
                    "expected_operation": expected.get("operation"),
                    "observed_operation": observed.get("operation"),
                    "expected_numeric_keys": _numeric_keys(expected.get("summary")),
                    "observed_summary_keys_raw": sorted((observed.get("summary") or {}).keys()),
                    "observed_summary_keys_adj": sorted((normalized.get("summary") or {}).keys()),
                }
            )

        raw_metrics = {
            "case_count": len(cases),
            "numeric_accuracy": _rate(
                [{"scores": c.get("scores") or {}} for c in cases], "numeric_match"
            ),
            "status_accuracy": _rate(
                [{"scores": c.get("scores") or {}} for c in cases], "status_match"
            ),
            "slot_accuracy": _rate(
                [{"scores": c.get("scores") or {}} for c in cases], "slot_match"
            ),
            "pass_rate": _rate(
                [{"scores": c.get("scores") or {}} for c in cases], "passed"
            ),
        }
        adj_metrics = summarize_results(adjusted_results)
        variant_reports[variant] = {
            "raw_metrics": raw_metrics,
            "adjusted_metrics": {
                "case_count": adj_metrics["case_count"],
                "numeric_accuracy": adj_metrics["numeric_accuracy"],
                "status_accuracy": adj_metrics["status_accuracy"],
                "slot_accuracy": adj_metrics["slot_accuracy"],
                "pass_rate": adj_metrics["pass_rate"],
                "answer_contains_rate": adj_metrics["answer_contains_rate"],
                "unsupported_answer_rate": adj_metrics["unsupported_answer_rate"],
            },
            "classification_counts": dict(classifications),
            "cases": detailed,
        }

    return variant_reports


def write_markdown(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# Stage R1 offline re-score (read-only)",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Output dir: `{report['output_dir']}`",
        "- Production code unchanged; original Stage-5 runs left immutable.",
        "",
        "## Normalization rules applied",
        "",
        "1. Perspective aliases: `material_source→material`, `energy_source→process`, `source_union→product`.",
        "2. Single-value numeric equivalence: if gold has exactly one numeric summary key and `total_kgCO2e` matches it within tolerance, copy that value under the gold key.",
        "3. Project known-total: remap `total_kgCO2e` → `knownTotalCarbon_kgCO2e` only when status is already `executable` and the total matches the accepted known total (8197.696). Never accept the incomplete_path union figure (~9314.86).",
        "4. Compare multi-key (`first_*` / `second_*` / `difference_*`): not recoverable offline → `scorer_artifact_needs_reexecution`.",
        "",
        "## RAW vs ADJUSTED headline",
        "",
        "| Suite | Variant | N | RAW numeric | ADJ numeric | RAW status | ADJ status | RAW slot | ADJ slot | RAW pass | ADJ pass |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for suite_name, suite in report["suites"].items():
        for variant, payload in suite["variants"].items():
            raw = payload["raw_metrics"]
            adj = payload["adjusted_metrics"]
            lines.append(
                "| {suite} | {variant} | {n} | {rn} | {an} | {rs} | {as_} | {rl} | {al} | {rp} | {ap} |".format(
                    suite=suite_name,
                    variant=variant,
                    n=raw["case_count"],
                    rn=raw["numeric_accuracy"],
                    an=adj["numeric_accuracy"],
                    rs=raw["status_accuracy"],
                    as_=adj["status_accuracy"],
                    rl=raw["slot_accuracy"],
                    al=adj["slot_accuracy"],
                    rp=raw["pass_rate"],
                    ap=adj["pass_rate"],
                )
            )

    lines.extend(["", "## Classification counts (exclusive)", ""])
    for suite_name, suite in report["suites"].items():
        lines.append(f"### {suite_name}")
        lines.append("")
        lines.append(
            "| Variant | correct | scorer_artifact_recoverable | scorer_artifact_needs_reexecution | genuine_compile_fail | genuine_wrong_program |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|")
        for variant, payload in suite["variants"].items():
            c = payload["classification_counts"]
            lines.append(
                "| {variant} | {correct} | {rec} | {need} | {comp} | {wrong} |".format(
                    variant=variant,
                    correct=c.get("correct", 0),
                    rec=c.get("scorer_artifact_recoverable", 0),
                    need=c.get("scorer_artifact_needs_reexecution", 0),
                    comp=c.get("genuine_compile_fail", 0),
                    wrong=c.get("genuine_wrong_program", 0),
                )
            )
        lines.append("")

    # Full-system decision summary
    full = report["suites"].get("full", {}).get("variants", {}).get("full_real")
    if full:
        c = full["classification_counts"]
        adj = full["adjusted_metrics"]
        raw = full["raw_metrics"]
        lines.extend(
            [
                "## Decision input (full_real)",
                "",
                f"- RAW pass={raw['pass_rate']}, numeric={raw['numeric_accuracy']}, status={raw['status_accuracy']}, slot={raw['slot_accuracy']}",
                f"- ADJUSTED pass={adj['pass_rate']}, numeric={adj['numeric_accuracy']}, status={adj['status_accuracy']}, slot={adj['slot_accuracy']}",
                f"- recoverable (scorer artifact) = {c.get('scorer_artifact_recoverable', 0)}",
                f"- needs re-execution (compare keys) = {c.get('scorer_artifact_needs_reexecution', 0)}",
                f"- genuine compile fail = {c.get('genuine_compile_fail', 0)}",
                f"- genuine wrong program = {c.get('genuine_wrong_program', 0)}",
                "",
                "Baselines should gain ~nothing if they return unstructured prose; confirm in the table above.",
                "",
                "**STOP for author decision.** Do not proceed to R1b/R2/R3 without approval.",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="R1 offline re-score (read-only).")
    parser.add_argument("--full", type=Path, default=DEFAULT_FULL)
    parser.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES)
    parser.add_argument("--ablations", type=Path, default=DEFAULT_ABLATIONS)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=ROOT / "outputs/research_experiments/r1_offline_rescore",
    )
    args = parser.parse_args(argv)

    run_id = time.strftime("r1_%Y%m%d_%H%M%S")
    out_dir = args.out_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    suites_spec = {
        "full": args.full,
        "baselines": args.baselines,
        "ablations": args.ablations,
    }
    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "output_dir": str(out_dir),
        "inputs": {name: str(path) for name, path in suites_spec.items()},
        "normalization_rules": {
            "perspective_aliases": PERSPECTIVE_ALIASES,
            "accepted_product_known_total": ACCEPTED_PRODUCT_KNOWN_TOTAL,
            "rejected_union_total": REJECTED_UNION_TOTAL,
            "single_value_numeric_equivalence": True,
            "compare_offline_recovery": False,
        },
        "suites": {},
    }

    for name, path in suites_spec.items():
        rows = load_jsonl(path)
        variants = rescore_rows(rows)
        report["suites"][name] = {
            "source_path": str(path),
            "row_count": len(rows),
            "variants": variants,
        }
        # Write per-suite case classifications
        flat_cases = []
        for variant, payload in variants.items():
            flat_cases.extend(payload["cases"])
            # strip bulky cases from summary json later
        (out_dir / f"{name}_case_classifications.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in flat_cases) + "\n",
            encoding="utf-8",
        )

    # Compact JSON without duplicating every case twice
    compact = deepcopy(report)
    for suite in compact["suites"].values():
        for payload in suite["variants"].values():
            payload["recoverable_case_ids"] = [
                c["case_id"]
                for c in payload["cases"]
                if c["classification"] == "scorer_artifact_recoverable"
            ]
            payload["needs_reexecution_case_ids"] = [
                c["case_id"]
                for c in payload["cases"]
                if c["classification"] == "scorer_artifact_needs_reexecution"
            ]
            del payload["cases"]

    (out_dir / "r1_offline_rescore_report.json").write_text(
        json.dumps(compact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(out_dir / "r1_offline_rescore_report.md", report)
    print(json.dumps({"output_dir": str(out_dir), "run_id": run_id}, indent=2))


if __name__ == "__main__":
    main()
