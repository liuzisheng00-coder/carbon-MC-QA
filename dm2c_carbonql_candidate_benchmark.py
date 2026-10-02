"""Deterministic controlled-v2 CarbonQL candidate benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import CanonicalV2Context, dimension_ids, load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, GraphSchema
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec, reference_evaluate
from dm2c_carbonql_candidate_views import (
    CandidateExecution,
    CandidateSetResult,
    CarbonQLCandidateSet,
    PerspectiveConstraint,
    build_decision_probe_program,
    canonical_program_bytes,
    classify_candidate_results,
    compile_candidate_set,
    program_sha256,
)
from dm2c_carbonql_executor import CarbonQLExecutor


@dataclass(frozen=True, slots=True)
class CandidateBenchmarkCase:
    case_id: str
    operation: str
    question: str
    selected_component_ids: tuple[str, ...]
    base_target_pool: tuple[str, ...]
    prototype_program: CarbonQLProgram
    candidate_set: CarbonQLCandidateSet

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "operation": self.operation,
            "question": self.question,
            "selected_component_ids": list(self.selected_component_ids),
            "base_target_pool": list(self.base_target_pool),
            "prototype_program": self.prototype_program.to_dict(),
            "candidate_set": {
                "constraint": {
                    "mode": self.candidate_set.constraint.mode,
                    "sources": list(self.candidate_set.constraint.sources),
                    "group_keys": list(self.candidate_set.constraint.group_keys),
                    "operations": list(self.candidate_set.constraint.operations),
                    "evidence_spans": {
                        key: list(value)
                        for key, value in self.candidate_set.constraint.evidence_spans.items()
                    },
                    "required_selector": self.candidate_set.constraint.required_selector,
                },
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "sources": list(candidate.sources),
                        "program": candidate.program.to_dict(),
                        "program_sha256": candidate.program_sha256,
                    }
                    for candidate in self.candidate_set.candidates
                ],
            },
        }


def _prototype(operation: str, selected: tuple[str, ...]) -> CarbonQLProgram:
    steps: list[dict[str, Any]] = [
        {"op": "SelectClicked", "ids": list(selected)},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "GroupBy", "keys": ["component"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
    if operation == "compare":
        steps.append({"op": "Compare"})
    elif operation == "rank":
        steps.append({"op": "Rank", "top_k": min(3, len(selected)), "descending": True})
    elif operation == "trace":
        steps.append({"op": "Trace"})
    return CarbonQLProgram.from_dict({"steps": steps})


def build_candidate_benchmark(
    context: CanonicalV2Context,
) -> tuple[CandidateBenchmarkCase, ...]:
    schema = GraphSchema.from_context(context)
    pool = dimension_ids(context, "component")
    if not pool:
        raise ValueError("controlled v2 benchmark requires at least one component")
    sizes = {
        "aggregate": 1,
        "compare": min(2, len(pool)),
        "rank": min(3, len(pool)),
        "trace": 1,
    }
    cases: list[CandidateBenchmarkCase] = []
    for operation in ("aggregate", "compare", "rank", "trace"):
        size = sizes[operation]
        selected = pool[:size] if operation != "trace" else pool[-1:]
        prototype = _prototype(operation, selected)
        constraint = PerspectiveConstraint(
            mode="underspecified",
            sources=(),
            group_keys=("component",),
            operations=(operation,),
            evidence_spans={},
            required_selector="SelectClicked",
        )
        cases.append(
            CandidateBenchmarkCase(
                case_id=f"controlled-v2-{operation}",
                operation=operation,
                question=f"Controlled v2 {operation} across the selected components.",
                selected_component_ids=selected,
                base_target_pool=pool,
                prototype_program=prototype,
                candidate_set=compile_candidate_set(constraint, prototype, schema),
            )
        )
    return tuple(cases)


def _reference_case(
    benchmark_case: CandidateBenchmarkCase,
    program: CarbonQLProgram,
    sources: tuple[str, ...],
) -> CarbonQLCase:
    return CarbonQLCase(
        case_id=benchmark_case.case_id,
        question=benchmark_case.question,
        category="controlled-v2-candidate",
        gold_program=program,
        expected_compiler_status="valid",
        reference=ReferenceSpec(
            projection_perspective="product",
            selector_component_ids=benchmark_case.selected_component_ids,
            sources=sources,
            group_keys=("component",),
        ),
    )


def candidate_reference_evaluate(
    case: CandidateBenchmarkCase, context: CanonicalV2Context
) -> CandidateSetResult:
    executions: list[CandidateExecution] = []
    for candidate in case.candidate_set.candidates:
        full = reference_evaluate(
            _reference_case(case, candidate.program, candidate.sources), context
        )
        probe_program = build_decision_probe_program(candidate.program)
        probe = reference_evaluate(
            _reference_case(case, probe_program, candidate.sources), context
        )
        executions.append(
            CandidateExecution(
                candidate_id=candidate.candidate_id,
                program_sha256=candidate.program_sha256,
                status=full.status,
                rows=full.rows,
                summary=full.summary,
                emission_ids=full.emission_ids,
                projection_keys=full.projection_keys,
                trace_rows=full.trace_rows,
                decision_probe_rows=probe.rows,
                coverage=full.coverage,
            )
        )
    return classify_candidate_results(case.candidate_set, executions)


def audit_candidate_benchmark(
    cases: Sequence[CandidateBenchmarkCase], context: CanonicalV2Context
) -> dict[str, Any]:
    executor = CarbonQLExecutor.from_context(context)
    integrity_errors = _candidate_integrity_errors(cases, context)
    mismatches: list[dict[str, str]] = []
    if not integrity_errors:
        for case in cases:
            truth = candidate_reference_evaluate(case, context)
            by_id = {execution.candidate_id: execution for execution in truth.executions}
            for candidate in case.candidate_set.candidates:
                observed = executor.execute(
                    candidate.program, case.selected_component_ids
                ).to_dict()
                expected = by_id[candidate.candidate_id].to_dict()
                for metadata_key in ("candidate_id", "program_sha256", "decision_probe_rows"):
                    expected.pop(metadata_key, None)
                # Candidate execution has no holes because candidates are executable.
                observed.pop("holes", None)
                if observed != expected:
                    mismatches.append(
                        {"case_id": case.case_id, "candidate_id": candidate.candidate_id}
                    )
    return {
        "schema_version": "controlled-v2",
        "release_id": str(context.manifest["releaseId"]),
        "release_profile": str(context.manifest["releaseProfile"]),
        "case_count": len(cases),
        "available_component_count": len(dimension_ids(context, "component")),
        "accepted_emission_count": len(context.emissions),
        "rejected_validation_count": sum(
            row["status"] == "rejected" for row in context.validation_rows
        ),
        "candidate_integrity_ok": not integrity_errors,
        "candidate_integrity_error_count": len(integrity_errors),
        "candidate_integrity_errors": integrity_errors,
        "oracle_mismatch_count": len(mismatches),
        "oracle_mismatches": mismatches,
    }


def _candidate_integrity_errors(
    cases: Sequence[CandidateBenchmarkCase],
    context: CanonicalV2Context,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    expected_cases = build_candidate_benchmark(context)
    if len(cases) != len(expected_cases):
        errors.append(
            {
                "case_id": "",
                "candidate_id": "",
                "issue": "case_count_mismatch",
                "expected_count": len(expected_cases),
                "actual_count": len(cases),
            }
        )
    seen_case_ids: set[str] = set()
    for case_index, case in enumerate(cases):
        raw_case_id = case.case_id
        case_id = _safe_integrity_value(raw_case_id)
        valid_case_id = isinstance(raw_case_id, str) and bool(
            _SAFE_CASE_ID.fullmatch(raw_case_id)
        )
        duplicate_case_id = valid_case_id and raw_case_id in seen_case_ids
        if not valid_case_id:
            errors.append(
                {
                    "case_id": case_id,
                    "candidate_id": "",
                    "issue": "invalid_case_id",
                    "index": case_index,
                }
            )
        elif duplicate_case_id:
            errors.append(
                {
                    "case_id": case_id,
                    "candidate_id": "",
                    "issue": "duplicate_case_id",
                    "index": case_index,
                }
            )
        else:
            seen_case_ids.add(raw_case_id)

        expected_case = (
            expected_cases[case_index]
            if case_index < len(expected_cases)
            else None
        )
        if expected_case is not None:
            for field in (
                "case_id",
                "operation",
                "question",
                "selected_component_ids",
                "base_target_pool",
            ):
                if getattr(case, field) != getattr(expected_case, field):
                    errors.append(
                        {
                            "case_id": case_id,
                            "candidate_id": "",
                            "issue": "case_field_mismatch",
                            "index": case_index,
                            "field": field,
                        }
                    )
            try:
                prototype_matches = canonical_program_bytes(
                    case.prototype_program
                ) == canonical_program_bytes(expected_case.prototype_program)
            except Exception:
                prototype_matches = False
            if not prototype_matches:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": "",
                        "issue": "case_field_mismatch",
                        "index": case_index,
                        "field": "prototype_program",
                    }
                )
            if case.candidate_set.constraint != expected_case.candidate_set.constraint:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": "",
                        "issue": "case_field_mismatch",
                        "index": case_index,
                        "field": "constraint",
                    }
                )
            expected_candidates = expected_case.candidate_set.candidates
        else:
            expected_candidates = ()
        actual_candidates = case.candidate_set.candidates
        if len(actual_candidates) != len(expected_candidates):
            errors.append(
                {
                    "case_id": case_id,
                    "candidate_id": "",
                    "issue": "candidate_count_mismatch",
                    "expected_count": len(expected_candidates),
                    "actual_count": len(actual_candidates),
                }
            )
        seen_ids: set[str] = set()
        seen_program_hashes: set[str] = set()
        for index, candidate in enumerate(actual_candidates):
            candidate_id = candidate.candidate_id
            valid_id = isinstance(candidate_id, str) and bool(
                _SAFE_CANDIDATE_ID.fullmatch(candidate_id)
            )
            duplicate_id = valid_id and candidate_id in seen_ids
            if not valid_id:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": _safe_integrity_value(candidate_id),
                        "issue": "invalid_candidate_id",
                        "index": index,
                    }
                )
            elif duplicate_id:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": candidate_id,
                        "issue": "duplicate_candidate_id",
                    }
                )
            else:
                seen_ids.add(candidate_id)
            try:
                canonical_hash = program_sha256(candidate.program)
                canonical_bytes = canonical_program_bytes(candidate.program)
            except Exception as exc:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": _safe_integrity_value(candidate_id),
                        "issue": "invalid_candidate_program",
                        "index": index,
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            if canonical_hash in seen_program_hashes:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": _safe_integrity_value(candidate_id),
                        "issue": "duplicate_canonical_program",
                        "canonical_program_sha256": canonical_hash,
                    }
                )
            else:
                seen_program_hashes.add(canonical_hash)
            if candidate.program_sha256 != canonical_hash:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": _safe_integrity_value(candidate_id),
                        "issue": "program_sha256_mismatch",
                        "declared_program_sha256": (
                            candidate.program_sha256
                            if isinstance(candidate.program_sha256, str)
                            else _safe_integrity_value(candidate.program_sha256)
                        ),
                        "canonical_program_sha256": canonical_hash,
                    }
                )
            if index >= len(expected_candidates):
                continue
            expected = expected_candidates[index]
            if valid_id and not duplicate_id and candidate_id != expected.candidate_id:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": candidate_id,
                        "issue": "candidate_id_mismatch",
                        "index": index,
                        "expected_candidate_id": expected.candidate_id,
                    }
                )
            try:
                actual_sources = tuple(candidate.sources)
            except Exception:
                actual_sources = None
            if actual_sources != expected.sources:
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": _safe_integrity_value(candidate_id),
                        "issue": "candidate_sources_mismatch",
                        "index": index,
                        "expected_sources": list(expected.sources),
                        "actual_sources": (
                            list(actual_sources)
                            if actual_sources is not None
                            else [_safe_integrity_value(candidate.sources)]
                        ),
                    }
                )
            if canonical_bytes != canonical_program_bytes(expected.program):
                errors.append(
                    {
                        "case_id": case_id,
                        "candidate_id": _safe_integrity_value(candidate_id),
                        "issue": "candidate_program_mismatch",
                        "index": index,
                        "expected_program_sha256": expected.program_sha256,
                        "actual_program_sha256": canonical_hash,
                    }
                )
    return errors


_SAFE_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe_integrity_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value != value:
        return "<non-finite-float:nan>"
    if value is None or isinstance(value, (bool, int, float)):
        return f"<{type(value).__name__}:{value}>"
    return f"<invalid-{type(value).__name__}>"


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
        for row in rows
    ).encode("utf-8")


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def freeze_candidate_benchmark(
    cases: Sequence[CandidateBenchmarkCase],
    context: CanonicalV2Context,
    *,
    output_root: Path,
    freeze_id: str,
) -> Path:
    if not _SAFE_ID.fullmatch(freeze_id) or "v2" not in freeze_id.lower():
        raise ValueError("freeze_id must be a safe controlled-v2 successor id")
    output_dir = output_root / freeze_id
    staging = output_root / f".{freeze_id}.staging"
    if output_dir.exists() or staging.exists():
        raise FileExistsError(output_dir if output_dir.exists() else staging)
    if _candidate_integrity_errors(cases, context):
        raise ValueError("candidate integrity validation prevents benchmark freeze")
    audit = audit_candidate_benchmark(cases, context)
    if audit.get("candidate_integrity_ok", True) is not True:
        raise ValueError("candidate integrity validation prevents benchmark freeze")
    if audit.get("oracle_mismatch_count") != 0:
        raise ValueError("candidate oracle mismatch prevents benchmark freeze")
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        case_rows = [case.to_dict() for case in cases]
        truth_rows = [
            {"case_id": case.case_id, **candidate_reference_evaluate(case, context).to_dict()}
            for case in cases
        ]
        payloads = {
            "e4d_cases_v2.jsonl": _jsonl(case_rows),
            "e4d_candidate_truth_v2.jsonl": _jsonl(truth_rows),
            "e4d_machine_audit_v2.json": json.dumps(
                audit,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            ).encode("utf-8"),
        }
        manifest = {
            "schema_version": "controlled-v2",
            "release_id": str(context.manifest["releaseId"]),
            "files": {
                name: {"sha256": hashlib.sha256(value).hexdigest().upper(), "size_bytes": len(value)}
                for name, value in sorted(payloads.items())
            },
        }
        payloads["e4d_manifest_v2.json"] = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        for name, value in payloads.items():
            (staging / name).write_bytes(value)
        staging.rename(output_dir)
    except Exception:
        if staging.exists():
            for path in staging.iterdir():
                if path.is_file():
                    path.unlink()
            staging.rmdir()
        raise
    return output_dir


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kg-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--freeze-id", required=True)
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)
    context = load_canonical_v2_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    cases = build_candidate_benchmark(context)
    freeze_candidate_benchmark(
        cases, context, output_root=args.output_root, freeze_id=args.freeze_id
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CandidateBenchmarkCase",
    "audit_candidate_benchmark",
    "build_candidate_benchmark",
    "candidate_reference_evaluate",
    "freeze_candidate_benchmark",
]
