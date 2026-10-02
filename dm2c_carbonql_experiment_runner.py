"""Canonical-v2 CarbonQL experiment runner with no historical output defaults."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import CanonicalV2Context
from dm2c_carbonql_benchmark import CarbonQLCase, reference_evaluate
from dm2c_carbonql_executor import CarbonQLExecutor


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    execution_status: str
    exact_match: bool
    result: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "execution_status": self.execution_status,
            "exact_match": self.exact_match,
            "result": dict(self.result),
        }


def run_oracle(
    cases: Sequence[CarbonQLCase], context: CanonicalV2Context
) -> tuple[CaseResult, ...]:
    executor = CarbonQLExecutor.from_context(context)
    rows: list[CaseResult] = []
    for case in cases:
        expected = reference_evaluate(case, context).to_dict()
        selector = case.gold_program.steps[0]
        external_ids = (
            case.reference.selector_component_ids
            if selector.op == "SelectClicked"
            else ()
        )
        observed = executor.execute(case.gold_program, external_ids).to_dict()
        rows.append(
            CaseResult(
                case_id=case.case_id,
                execution_status=str(observed["status"]),
                exact_match=observed == expected,
                result=observed,
            )
        )
    return tuple(rows)


def summarize_variant(results: Sequence[CaseResult]) -> dict[str, Any]:
    return {
        "case_count": len(results),
        "executable_count": sum(row.execution_status == "ok" for row in results),
        "exact_match_count": sum(row.exact_match for row in results),
    }


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def write_run_artifacts(run: Mapping[str, Any], *, output_root: Path) -> Path:
    run_id = str(run.get("run_id") or "")
    if not _SAFE_RUN_ID.fullmatch(run_id) or "v2" not in run_id.lower():
        raise ValueError("run_id must be a safe v2 successor id")
    target = output_root / run_id
    staging = output_root / f".{run_id}.staging"
    if target.exists() or staging.exists():
        raise FileExistsError(target if target.exists() else staging)
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        (staging / "run_summary_v2.json").write_text(
            json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
            newline="",
        )
        cases = run.get("cases", ())
        (staging / "case_results_v2.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                for row in cases
            ),
            encoding="utf-8",
            newline="",
        )
        staging.rename(target)
    except Exception:
        if staging.exists():
            for path in staging.iterdir():
                if path.is_file():
                    path.unlink()
            staging.rmdir()
        raise
    return target


__all__ = ["CaseResult", "run_oracle", "summarize_variant", "write_run_artifacts"]
