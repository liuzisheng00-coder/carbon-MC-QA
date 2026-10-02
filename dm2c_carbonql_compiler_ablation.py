"""Ablate the CarbonQL compiler stages over the E4c held-out benchmark.

Each variant removes one stage of semantic compilation while the retrieval and
execution layers stay fixed, so a difference in the reported numbers is
attributable to compilation alone:

  V1  operation list and worked examples only
  V2  + typed operator contract, live graph schema, and the M3.a doctrine rules
  V3  + one validator-driven repair round
  V4  + the semantic coverage gate

Every variant runs through :class:`CarbonQLService`, the same chain the /ask
endpoint serves, and is scored against the frozen E4c truth: the compiled
program must reproduce the gold accounting boundary and the gold number, not
merely parse.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient
from dm2c_carbonql_service import COMPILER_VARIANTS, CarbonQLAnswer, CarbonQLService

DEFAULT_BENCHMARK = Path(
    "outputs/research_experiments/e4c_carbonql_heldout/"
    "e4c_reviewed_stagefix_v2_20260727"
)
DEFAULT_RELEASE = Path(
    "outputs/research_experiments/m2_typed_completed_20260727_real_a1_fixture_carriernamed"
)
DEFAULT_OUTPUT_ROOT = Path("outputs/research_experiments/e4e_compiler_ablation")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# The accounting boundary is what M3.a governs: which views the answer rests on,
# which emission sources it admits, and at which entity level it reads them. The
# remaining signature fields describe how the result is presented, so they are
# scored separately rather than folded into one pass/fail.
BOUNDARY_KEYS = ("base_views", "emission_sources", "entity_levels", "view_mode")
PRESENTATION_KEYS = ("operations", "trace")
SIGNATURE_KEYS = BOUNDARY_KEYS + PRESENTATION_KEYS


@dataclass(frozen=True)
class AblationCase:
    case_id: str
    question: str
    category: str
    selected_component_ids: tuple[str, ...]
    gold_view_signature: Mapping[str, Any]
    truth_status: str
    truth_total: float | None
    truth_row_count: int
    truth_emission_ids: frozenset[str]


def _normalize_signature(signature: Mapping[str, Any] | None) -> dict[str, Any]:
    if not signature:
        return {}
    out: dict[str, Any] = {}
    for key in SIGNATURE_KEYS:
        value = signature.get(key)
        out[key] = list(value) if isinstance(value, (list, tuple)) else value
    return out


def load_cases(benchmark_dir: Path) -> tuple[AblationCase, ...]:
    truth_by_id = {}
    for line in (benchmark_dir / "e4c_truth.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.strip():
            row = json.loads(line)
            truth_by_id[str(row["case_id"])] = row

    cases: list[AblationCase] = []
    for line in (benchmark_dir / "e4c_cases.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = str(row["case_id"])
        truth = truth_by_id[case_id]
        summary = truth.get("summary") or {}
        cases.append(
            AblationCase(
                case_id=case_id,
                question=str(row.get("question") or ""),
                category=str(row.get("category") or ""),
                selected_component_ids=tuple(
                    str(value) for value in row.get("selected_component_ids", ())
                ),
                gold_view_signature=_normalize_signature(
                    row.get("gold_view_signature")
                ),
                truth_status=str(truth.get("status") or ""),
                truth_total=summary.get("total_kgCO2e"),
                truth_row_count=len(truth.get("rows") or ()),
                truth_emission_ids=frozenset(
                    str(value) for value in truth.get("emission_ids") or ()
                ),
            )
        )
    return tuple(cases)


def _totals_agree(observed: float | None, expected: float | None) -> bool:
    if observed is None or expected is None:
        return observed is expected or (observed is None and expected is None)
    return abs(float(observed) - float(expected)) <= 1e-9 * max(
        1.0, abs(float(expected))
    )


def _subset_match(
    observed: Mapping[str, Any], gold: Mapping[str, Any], keys: Sequence[str]
) -> bool:
    if not observed:
        return False
    return all(observed.get(key) == gold.get(key) for key in keys)


def score_answer(case: AblationCase, answer: CarbonQLAnswer) -> dict[str, Any]:
    observed_signature = _normalize_signature(answer.view_signature)
    gold_signature = dict(case.gold_view_signature)
    signature_match = bool(observed_signature and observed_signature == gold_signature)
    boundary_match = _subset_match(observed_signature, gold_signature, BOUNDARY_KEYS)
    presentation_match = _subset_match(
        observed_signature, gold_signature, PRESENTATION_KEYS
    )
    status_match = answer.query_status == case.truth_status
    total_match = _totals_agree(answer.total_kgCO2e, case.truth_total)
    evidence_match = (
        frozenset(answer.evidence_ids) == case.truth_emission_ids
    )
    row_match = len(answer.rows) == case.truth_row_count
    return {
        "case_id": case.case_id,
        "category": case.category,
        "question": case.question,
        "compiled": answer.program is not None,
        "compiler_status": answer.compiler_status,
        "query_status": answer.query_status,
        "signature_match": signature_match,
        "boundary_match": boundary_match,
        "presentation_match": presentation_match,
        "status_match": status_match,
        "total_match": total_match,
        "evidence_match": evidence_match,
        "row_match": row_match,
        "answer_exact_match": bool(
            status_match and total_match and evidence_match and row_match
        ),
        "observed_view_signature": observed_signature,
        "gold_view_signature": dict(case.gold_view_signature),
        "observed_total_kgCO2e": answer.total_kgCO2e,
        "gold_total_kgCO2e": case.truth_total,
        "program": answer.program,
        "compiler_error": dict(answer.compiler_error),
        "llm_call_count": answer.llm_call_count,
        "compile_latency_ms": answer.compile_latency_ms,
        "execute_latency_ms": answer.execute_latency_ms,
    }


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if row.get(key)) / len(rows), 4)


SCORED_METRICS = (
    "compiled",
    "boundary_match",
    "presentation_match",
    "signature_match",
    "status_match",
    "total_match",
    "evidence_match",
    "answer_exact_match",
)


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latencies = [
        float(row["compile_latency_ms"]) for row in rows if row.get("compile_latency_ms")
    ]
    summary: dict[str, Any] = {"case_count": len(rows)}
    for metric in SCORED_METRICS:
        summary[metric] = _rate(rows, metric)
    summary["mean_llm_calls"] = (
        round(statistics.fmean(float(row["llm_call_count"]) for row in rows), 3)
        if rows
        else None
    )
    summary["mean_compile_latency_ms"] = (
        round(statistics.fmean(latencies), 1) if latencies else None
    )
    return summary


def _mean_sd(values: Sequence[float | None]) -> dict[str, float | None]:
    present = [float(value) for value in values if value is not None]
    if not present:
        return {"mean": None, "sd": None, "min": None, "max": None}
    return {
        "mean": round(statistics.fmean(present), 4),
        "sd": round(statistics.stdev(present), 4) if len(present) > 1 else 0.0,
        "min": round(min(present), 4),
        "max": round(max(present), 4),
    }


def summarize_repeats(per_repeat: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate one variant's per-repeat summaries into mean and spread."""
    keys = set()
    for summary in per_repeat:
        keys.update(k for k, v in summary.items() if isinstance(v, (int, float)))
    return {
        "repeat_count": len(per_repeat),
        **{
            key: _mean_sd([summary.get(key) for summary in per_repeat])
            for key in sorted(keys)
        },
    }


def _mcnemar_exact_p(gained: int, lost: int) -> float | None:
    """Two-sided exact McNemar p-value over the discordant pairs."""
    total = gained + lost
    if total == 0:
        return None
    smaller = min(gained, lost)
    tail = sum(math.comb(total, i) for i in range(smaller + 1)) / (2**total)
    return round(min(1.0, 2 * tail), 5)


def paired_analysis(
    case_scores: Mapping[str, Mapping[str, list[bool]]],
    variants: Sequence[str],
    metric: str,
) -> list[dict[str, Any]]:
    """Compare adjacent variants case by case rather than by marginal rate.

    Each case contributes its success frequency across repeats. A case counts as
    solved by a variant when it succeeds in more than half of the repeats, which
    keeps the paired test from being driven by a single nondeterministic draw.
    """
    rows: list[dict[str, Any]] = []
    for earlier, later in zip(variants, variants[1:]):
        gained: list[str] = []
        lost: list[str] = []
        deltas: list[float] = []
        for case_id, by_variant in case_scores.items():
            a_runs = by_variant.get(earlier) or []
            b_runs = by_variant.get(later) or []
            if not a_runs or not b_runs:
                continue
            a_rate = sum(a_runs) / len(a_runs)
            b_rate = sum(b_runs) / len(b_runs)
            deltas.append(b_rate - a_rate)
            a_solved, b_solved = a_rate > 0.5, b_rate > 0.5
            if b_solved and not a_solved:
                gained.append(case_id)
            elif a_solved and not b_solved:
                lost.append(case_id)
        rows.append(
            {
                "metric": metric,
                "step": f"{earlier}->{later}",
                "gained": len(gained),
                "lost": len(lost),
                "net": len(gained) - len(lost),
                "gained_case_ids": sorted(gained),
                "lost_case_ids": sorted(lost),
                "mean_per_case_delta": (
                    round(statistics.fmean(deltas), 4) if deltas else None
                ),
                "mcnemar_exact_p": _mcnemar_exact_p(len(gained), len(lost)),
            }
        )
    return rows


def run_variant(
    service: CarbonQLService,
    cases: Sequence[AblationCase],
    variant: str,
    workers: int,
) -> list[dict[str, Any]]:
    def run_one(case: AblationCase) -> dict[str, Any]:
        try:
            answer = service.answer(
                case.question, case.selected_component_ids, variant=variant
            )
        except Exception as exc:  # a provider failure must not be scored as a miss
            return {
                "case_id": case.case_id,
                "category": case.category,
                "question": case.question,
                "provider_error": f"{type(exc).__name__}: {exc}",
                **{metric: False for metric in SCORED_METRICS},
                "llm_call_count": 0,
                "compile_latency_ms": 0.0,
            }
        return score_answer(case, answer)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run_one, cases))


def open_staging(output_root: Path, run_id: str) -> Path:
    """Create or reopen the staging directory that checkpoints a run.

    A full sweep is tens of minutes of remote calls, so every variant block is
    flushed to disk as it finishes. A crashed run can then be resumed instead of
    paying for the completed blocks a second time.
    """
    target = output_root / run_id
    if target.exists():
        raise FileExistsError(target)
    staging = output_root / f".{run_id}.staging"
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def load_checkpoint(staging: Path) -> list[dict[str, Any]]:
    path = staging / "ablation_cases.jsonl"
    if not path.exists():
        return []
    # utf-8-sig: a checkpoint is a recovery artifact that may have been opened by
    # a Windows editor, which can prepend a BOM.
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def append_checkpoint(staging: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with (staging / "ablation_cases.jsonl").open("a", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def finalize(report: Mapping[str, Any], staging: Path, target: Path) -> Path:
    (staging / "ablation_summary.json").write_text(
        json.dumps(
            {key: value for key, value in report.items() if key != "variants_cases"},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
        newline="",
    )
    (staging / "ablation_summary.md").write_text(
        render_markdown(report), encoding="utf-8", newline=""
    )
    staging.rename(target)
    return target


def render_markdown(report: Mapping[str, Any]) -> str:
    def cell(metrics: Mapping[str, Any], key: str) -> str:
        entry = metrics.get(key) or {}
        mean, sd = entry.get("mean"), entry.get("sd")
        if mean is None:
            return "—"
        if not sd:
            return f"{mean * 100:.1f}%"
        return f"{mean * 100:.1f}% ± {sd * 100:.1f}"

    lines = [
        "# CarbonQL compiler ablation (E4c held-out)",
        "",
        f"Benchmark: `{report['benchmark']}` ({report['case_count']} cases)",
        f"Release: `{report['release_id']}`",
        f"Model: `{report['model']}`, {report['repeats']} repeat(s) per variant",
        "",
        "Rates are the mean over repeats, plus or minus one standard deviation.",
        "The accounting boundary covers base_views, emission_sources, entity_levels",
        "and view_mode; presentation covers operations and trace.",
        "",
        "| Variant | Compiled | Boundary | Presentation | Query status | Answer exact | LLM calls |",
        "|---|---|---|---|---|---|---|",
    ]
    for variant in report["variant_order"]:
        metrics = report["variants"][variant]
        calls = (metrics.get("mean_llm_calls") or {}).get("mean")
        lines.append(
            f"| {variant} | {cell(metrics, 'compiled')} | "
            f"{cell(metrics, 'boundary_match')} | "
            f"{cell(metrics, 'presentation_match')} | "
            f"{cell(metrics, 'status_match')} | "
            f"{cell(metrics, 'answer_exact_match')} | "
            f"{'—' if calls is None else f'{calls:.2f}'} |"
        )

    for metric, rows in report["paired"].items():
        lines += [
            "",
            f"## Paired transitions on {metric}",
            "",
            "A case counts as solved when it succeeds in more than half of the repeats.",
            "",
            "| Step | Gained | Lost | Net | Mean per-case delta | McNemar exact p |",
            "|---|---|---|---|---|---|",
        ]
        for row in rows:
            delta = row["mean_per_case_delta"]
            pval = row["mcnemar_exact_p"]
            lines.append(
                f"| {row['step']} | {row['gained']} | {row['lost']} | {row['net']:+d} | "
                f"{'—' if delta is None else f'{delta * 100:+.1f}pp'} | "
                f"{'—' if pval is None else f'{pval:.4f}'} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--release", default=str(DEFAULT_RELEASE))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", required=True, help="Safe v2 successor id")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--base-url", default=DEEPSEEK_BASE_URL)
    parser.add_argument("--variants", nargs="*", default=list(COMPILER_VARIANTS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help=(
            "Times to run every variant. The provider is nondeterministic even at "
            "temperature zero, so a single pass cannot resolve small differences."
        ),
    )
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N cases")
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Permit a controlled fixture whose factory energy inputs are synthetic",
    )
    args = parser.parse_args()

    import os

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set")

    cases = load_cases(Path(args.benchmark))
    if args.limit:
        cases = cases[: args.limit]

    client = OpenAICompatibleToolClient(
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        temperature=0.0,
        timeout=180,
        max_retries=4,
    )
    service = CarbonQLService.from_release(
        Path(args.release), client, allow_synthetic=args.allow_synthetic
    )

    started = time.time()
    output_root = Path(args.output_root)
    staging = open_staging(output_root, args.run_id)
    all_rows = load_checkpoint(staging)
    done = {(int(row["repeat"]), str(row["variant"])) for row in all_rows}
    if done:
        print(f"resuming: {len(done)} variant block(s) already on disk", flush=True)

    for repeat in range(1, args.repeats + 1):
        for variant in args.variants:
            if (repeat, variant) in done:
                continue
            variant_started = time.time()
            rows = [
                {"repeat": repeat, "variant": variant, **row}
                for row in run_variant(service, cases, variant, args.workers)
            ]
            append_checkpoint(staging, rows)
            all_rows.extend(rows)
            summary = summarize(rows)
            errors = sum(1 for row in rows if row.get("provider_error"))
            print(
                f"repeat {repeat}/{args.repeats} {variant}: "
                f"exact={summary['answer_exact_match']} "
                f"boundary={summary['boundary_match']} "
                f"compiled={summary['compiled']} "
                f"errors={errors} "
                f"({time.time() - variant_started:.0f}s)",
                flush=True,
            )

    # Everything below is derived from the checkpointed rows, so a resumed run
    # reports exactly what an uninterrupted one would.
    per_repeat: dict[str, list[dict[str, Any]]] = {v: [] for v in args.variants}
    for variant in args.variants:
        for repeat in range(1, args.repeats + 1):
            block = [
                row
                for row in all_rows
                if row["variant"] == variant and int(row["repeat"]) == repeat
            ]
            if block:
                per_repeat[variant].append(summarize(block))

    case_scores: dict[str, dict[str, dict[str, list[bool]]]] = {
        metric: {} for metric in ("answer_exact_match", "boundary_match")
    }
    for row in all_rows:
        for metric, by_case in case_scores.items():
            by_case.setdefault(str(row["case_id"]), {}).setdefault(
                str(row["variant"]), []
            ).append(bool(row.get(metric)))

    report = {
        "run_id": args.run_id,
        "benchmark": str(args.benchmark),
        "release_id": service.release_id,
        "model": args.model,
        "case_count": len(cases),
        "repeats": args.repeats,
        "variant_order": list(args.variants),
        "variants": {
            variant: summarize_repeats(summaries)
            for variant, summaries in per_repeat.items()
        },
        "per_repeat": per_repeat,
        "paired": {
            metric: paired_analysis(by_case, args.variants, metric)
            for metric, by_case in case_scores.items()
        },
        "variants_cases": all_rows,
        "elapsed_s": round(time.time() - started, 1),
    }

    target = finalize(report, staging, output_root / args.run_id)
    print(render_markdown(report))
    print(f"written to: {target}")


if __name__ == "__main__":
    main()
