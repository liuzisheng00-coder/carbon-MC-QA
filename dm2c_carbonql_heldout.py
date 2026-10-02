"""Controlled-v2 CarbonQL held-out truth and safe successor publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import CanonicalV2Context, dimension_ids, load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram
from dm2c_carbonql_benchmark import (
    CarbonQLCase,
    ReferenceResult,
    ReferenceSpec,
    reference_evaluate,
)


def _program(source: object, group_keys: Sequence[str]) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": "SelectProject"},
                {"op": "CarbonAtoms", "source": source},
                {"op": "GroupBy", "keys": list(group_keys)},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
                {"op": "Trace"},
            ]
        }
    )


def build_heldout_cases(context: CanonicalV2Context) -> tuple[CarbonQLCase, ...]:
    # Public availability is intentionally consulted even for project-wide cases;
    # this rejects forged contexts and removes all fixed-count assumptions.
    dimension_ids(context, "component")
    specifications = (
        (
            "controlled-v2-product",
            "What is the component-attributed project carbon by component?",
            "all",
            ("component",),
            "product",
        ),
        (
            "controlled-v2-material-source",
            "What are the material source emissions by explicit material?",
            "material",
            ("material",),
            "material_source",
        ),
        (
            "controlled-v2-energy-source",
            "What are all energy source emissions by explicit carrier?",
            "process",
            ("carrier",),
            "energy_source",
        ),
        (
            "controlled-v2-source-union",
            "What are all unique source emissions across material and process context?",
            "all",
            ("material", "process"),
            "source_union",
        ),
    )
    return tuple(
        CarbonQLCase(
            case_id=case_id,
            question=question,
            category="controlled-v2-heldout",
            gold_program=_program(source, groups),
            expected_compiler_status="valid",
            reference=ReferenceSpec(
                projection_perspective=perspective,
                sources=("material", "process")
                if source == "all"
                else (str(source),),
                group_keys=tuple(groups),
            ),
        )
        for case_id, question, source, groups, perspective in specifications
    )


def heldout_reference_evaluate(
    case: CarbonQLCase, context: CanonicalV2Context
) -> ReferenceResult:
    return reference_evaluate(case, context)


def audit_heldout_cases(
    cases: Sequence[CarbonQLCase], context: CanonicalV2Context
) -> dict[str, Any]:
    from dm2c_carbonql_executor import CarbonQLExecutor

    executor = CarbonQLExecutor.from_context(context)
    mismatches: list[str] = []
    valid_zero_covered = False
    process_only_covered = False
    for case in cases:
        expected = heldout_reference_evaluate(case, context)
        selector = case.gold_program.steps[0]
        external_ids = (
            case.reference.selector_component_ids
            if selector.op == "SelectClicked"
            else ()
        )
        observed = executor.execute(case.gold_program, external_ids)
        if expected.to_dict() != observed.to_dict():
            mismatches.append(case.case_id)
        valid_zero_covered = valid_zero_covered or "emission:zero" in expected.emission_ids
        process_only_covered = process_only_covered or "emission:process" in expected.emission_ids
    return {
        "schema_version": "controlled-v2",
        "release_id": str(context.manifest["releaseId"]),
        "release_profile": str(context.manifest["releaseProfile"]),
        "case_count": len(cases),
        "oracle_mismatch_count": len(mismatches),
        "oracle_mismatch_case_ids": mismatches,
        "valid_zero_covered": valid_zero_covered,
        "process_only_source_covered": process_only_covered,
        "rejected_validation_count": sum(
            row["status"] == "rejected" for row in context.validation_rows
        ),
    }


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def freeze_heldout_benchmark(
    cases: Sequence[CarbonQLCase],
    context: CanonicalV2Context,
    *,
    output_root: Path,
    freeze_id: str,
) -> Path:
    if not _SAFE_ID.fullmatch(freeze_id) or "v2" not in freeze_id.lower():
        raise ValueError("freeze_id must be a safe controlled-v2 successor id")
    target = output_root / freeze_id
    staging = output_root / f".{freeze_id}.staging"
    if target.exists() or staging.exists():
        raise FileExistsError(target if target.exists() else staging)
    audit = audit_heldout_cases(cases, context)
    if audit.get("oracle_mismatch_count") != 0:
        raise ValueError("heldout oracle mismatch prevents benchmark freeze")
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        cases_bytes = _jsonl([case.to_dict() for case in cases])
        truth_bytes = _jsonl(
            [
                {"case_id": case.case_id, **heldout_reference_evaluate(case, context).to_dict()}
                for case in cases
            ]
        )
        audit_bytes = json.dumps(
            audit,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        payloads = {
            "e4c_cases_v2.jsonl": cases_bytes,
            "e4c_truth_v2.jsonl": truth_bytes,
            "e4c_machine_audit_v2.json": audit_bytes,
        }
        manifest = {
            "schema_version": "controlled-v2",
            "release_id": str(context.manifest["releaseId"]),
            "files": {
                name: {"sha256": hashlib.sha256(value).hexdigest().upper(), "size_bytes": len(value)}
                for name, value in sorted(payloads.items())
            },
        }
        payloads["e4c_manifest_v2.json"] = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        for name, value in payloads.items():
            (staging / name).write_bytes(value)
        staging.rename(target)
    except Exception:
        if staging.exists():
            for path in staging.iterdir():
                if path.is_file():
                    path.unlink()
            staging.rmdir()
        raise
    return target


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
    cases = build_heldout_cases(context)
    freeze_heldout_benchmark(
        cases, context, output_root=args.output_root, freeze_id=args.freeze_id
    )


if __name__ == "__main__":
    main()


__all__ = [
    "audit_heldout_cases",
    "build_heldout_cases",
    "freeze_heldout_benchmark",
    "heldout_reference_evaluate",
]
