"""Run E4b CarbonQL oracle checks on a canonical-v2 release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram, ProgramHole, derive_projection_perspective
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec
from dm2c_carbonql_experiment_runner import (
    run_oracle,
    summarize_variant,
    write_run_artifacts,
)


_DEFAULT_CASES = Path(
    "outputs/research_experiments/e4b_carbonql_typed_completed_real/"
    "20260718_153407_deepseek/e4b_cases.jsonl"
)
_DEFAULT_OUTPUT_ROOT = Path("outputs/research_experiments/e4b_carbonql_v2")
_LEGACY_DIMENSION_MAP = {
    "material_family": "material",
    "energy_carrier": "carrier",
    "product_type": "component_type",
    "batch": "process",
}


def _current_dimension(value: Any) -> Any:
    if isinstance(value, str):
        return _LEGACY_DIMENSION_MAP.get(value, value)
    return value


def _migrate_legacy_program(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = json.loads(json.dumps(payload))
    for step in migrated.get("steps", ()):
        if step.get("op") == "ResolveEntities":
            if step.get("entity_type") == "product":
                step["entity_type"] = "component"
            if step.get("property") == "objectType":
                step["property"] = "name"
        if step.get("op") == "GroupBy":
            step["keys"] = [_current_dimension(value) for value in step.get("keys", ())]
        elif step.get("op") == "Filter":
            step["field"] = _current_dimension(step.get("field"))
    return migrated


def _case_from_legacy_row(row: Mapping[str, Any]) -> CarbonQLCase:
    program = CarbonQLProgram.from_dict(_migrate_legacy_program(row["gold_program"]))
    reference_spec = row.get("reference_spec") or {}
    group_keys = tuple(
        str(_current_dimension(value)) for value in reference_spec.get("group_keys", ())
    )
    return CarbonQLCase(
        case_id=str(row["case_id"]),
        question=str(row.get("question") or ""),
        category=str(row.get("category") or "e4b"),
        gold_program=program,
        expected_compiler_status=str(row.get("expected_compiler_status") or "valid"),
        reference=ReferenceSpec(
            projection_perspective=derive_projection_perspective(program),
            selector_component_ids=tuple(
                str(value) for value in row.get("selected_component_ids", ())
            ),
            sources=tuple(str(value) for value in reference_spec.get("sources", ())),
            group_keys=group_keys,
            join_required_sources=tuple(
                str(value)
                for value in reference_spec.get("join_required_sources", ())
            ),
        ),
        expected_view_signature=dict(row.get("gold_view_signature") or {}),
        expected_holes=tuple(
            ProgramHole.from_dict(item) for item in row.get("expected_holes", ())
        ),
    )


def load_cases(path: Path) -> tuple[CarbonQLCase, ...]:
    rows: list[CarbonQLCase] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(_case_from_legacy_row(json.loads(line)))
    return tuple(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kg-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--output-root", type=Path, default=_DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)

    context = load_canonical_v2_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    cases = load_cases(args.cases)
    results = run_oracle(cases, context)
    run = {
        "run_id": args.run_id,
        "schema_version": "e4b-carbonql-v2",
        "release_id": str(context.manifest["releaseId"]),
        "release_profile": str(context.manifest["releaseProfile"]),
        "synthetic_energy": context.synthetic_energy,
        "case_count": len(cases),
        "oracle": summarize_variant(results),
        "cases_source": str(args.cases),
        "cases": [row.to_dict() for row in results],
    }
    output_dir = write_run_artifacts(run, output_root=args.output_root)
    print(output_dir)


if __name__ == "__main__":
    main()
