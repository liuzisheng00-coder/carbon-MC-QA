"""Apply the human-reviewed E4c V4 revisions on canonical-v2 contexts."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import CanonicalV2Context, load_canonical_v2_context
from dm2c_carbonql import (
    CarbonQLProgram,
    derive_projection_perspective,
    derive_view_signature,
)
from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec, reference_evaluate
from dm2c_carbonql_naturalized_heldout import (
    _DEFAULT_KG_DIR,
    _DEFAULT_OUTPUT_ROOT,
    _DEFAULT_REWRITE_PATH,
    _hash_file,
    _join_sources_from_program,
    _make_case,
    _operation,
    _query_status,
    _reference_to_dict,
    _SAFE_ID,
    _sources_from_program,
    _groups_from_program,
    _write_freeze,
    StratifiedCarbonQLCase,
    audit_stratified_cases,
    build_stratified_cases,
    heldout_reference_evaluate,
    load_rewrite_specs,
)
from dm2c_carbonql_semantic import validate_semantic_coverage


DEFAULT_REWRITE_PATH = _DEFAULT_REWRITE_PATH
DEFAULT_REVISION_PATH = Path("inputs/e4c_human_review_revisions_v4.json")
_DEFAULT_REVIEWED_OUTPUT_ROOT = _DEFAULT_OUTPUT_ROOT

APPROVED_V4_REVISION_IDS = frozenset(
    {
        "e4c_1_10_source_hole",
        "e4c_1_11_group_hole",
        "e4c_2_10_source_hole",
        "e4c_2_11_group_hole",
        "e4c_3_10_source_hole",
        "e4c_3_11_group_hole",
        "e4c_4_10_source_hole",
        "e4c_4_11_group_hole",
        "e4c_4_01",
        "e4c_4_04",
        "e4c_4_05",
        "e4c_4_06",
        "e4c_4_07",
        "e4c_4_08",
        "e4c_4_09_unresolved",
        "e4c_4_12_ambiguous",
    }
)
_REVISION_KINDS = frozenset(
    {
        "implicit_view_resolution",
        "implicit_grouping_resolution",
        "automatic_join_correction",
    }
)
_REQUIRED_KEYS = frozenset(
    {
        "base_case_id",
        "new_case_id",
        "question",
        "revision_kind",
        "perspective_evidence",
        "review_rationale",
    }
)
_SCALAR_FIELDS = (
    "base_case_id",
    "new_case_id",
    "question",
    "revision_kind",
    "review_rationale",
)
_REVISED_PROGRAM_SPECS = {
    "e4c_1_10_implicit_view": (
        ("material",),
        ("component", "material"),
        "SelectClicked",
    ),
    "e4c_1_11_implicit_grouping": (
        ("material",),
        ("component", "material"),
        "SelectProject",
    ),
    "e4c_2_10_implicit_view": (
        ("process",),
        ("component", "carrier"),
        "SelectClicked",
    ),
    "e4c_2_11_implicit_grouping": (
        ("process",),
        ("component", "carrier"),
        "SelectProject",
    ),
    "e4c_3_10_implicit_view": (
        ("material", "process"),
        ("source_kind", "material", "carrier"),
        "SelectClicked",
    ),
    "e4c_3_11_implicit_grouping": (
        ("material", "process"),
        ("source_kind", "material", "carrier"),
        "SelectProject",
    ),
    "e4c_4_10_implicit_view": (
        ("material", "process"),
        ("component", "source_kind", "material", "carrier"),
        "SelectClicked",
    ),
    "e4c_4_11_implicit_grouping": (
        ("material", "process"),
        ("component", "source_kind", "material", "carrier"),
        "SelectProject",
    ),
}
_AUTOMATIC_JOIN_CORRECTION_CASE_IDS = frozenset(
    {
        "e4c_4_01",
        "e4c_4_04",
        "e4c_4_05",
        "e4c_4_06",
        "e4c_4_07",
        "e4c_4_08",
        "e4c_4_09_unresolved",
        "e4c_4_12_ambiguous",
    }
)
_ALLOWED_JOIN_CASE_IDS = frozenset({"e4c_4_02", "e4c_4_03"})


@dataclass(frozen=True)
class ReviewRevision:
    base_case_id: str
    new_case_id: str
    question: str
    revision_kind: str
    perspective_evidence: tuple[str, ...]
    review_rationale: str


@dataclass(frozen=True)
class ReviewedCarbonQLCase:
    row: StratifiedCarbonQLCase
    supersedes_case_id: str = ""
    review_revision_kind: str = ""
    perspective_evidence: tuple[str, ...] = ()

    @property
    def case(self) -> CarbonQLCase:
        return self.row.case

    @property
    def view_combination(self) -> str:
        return self.row.view_combination

    @property
    def truth_basis(self) -> str:
        return self.row.truth_basis

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.row.to_dict(),
            "supersedes_case_id": self.supersedes_case_id,
            "review_revision_kind": self.review_revision_kind,
            "perspective_evidence": list(self.perspective_evidence),
        }


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_review_revisions(path: Path) -> list[ReviewRevision]:
    payload = _json_load(path)
    if not isinstance(payload, list):
        raise ValueError("manifest source must be a JSON list")
    revisions: list[ReviewRevision] = []
    seen_base: set[str] = set()
    seen_new: set[str] = set()
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ValueError(f"manifest row {index} must be an object")
        unknown = set(row) - _REQUIRED_KEYS
        missing = _REQUIRED_KEYS - set(row)
        if unknown:
            raise ValueError(f"manifest row {index} has unknown keys: {sorted(unknown)}")
        if missing:
            raise ValueError(f"manifest row {index} is missing keys: {sorted(missing)}")
        scalar_values = {field: row[field] for field in _SCALAR_FIELDS}
        if not all(isinstance(value, str) for value in scalar_values.values()):
            raise ValueError(f"manifest row {index} scalar fields must be strings")
        if any(not value.strip() for value in scalar_values.values()):
            raise ValueError(f"manifest row {index} has blank scalar field")
        evidence = row["perspective_evidence"]
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(isinstance(item, str) and item.strip() for item in evidence)
        ):
            raise ValueError(
                f"manifest row {index} perspective_evidence must be non-empty strings"
            )
        base_case_id = scalar_values["base_case_id"]
        new_case_id = scalar_values["new_case_id"]
        revision_kind = scalar_values["revision_kind"]
        if base_case_id in seen_base:
            raise ValueError(f"duplicate base_case_id: {base_case_id}")
        if new_case_id in seen_new:
            raise ValueError(f"duplicate new_case_id: {new_case_id}")
        if base_case_id not in APPROVED_V4_REVISION_IDS:
            raise ValueError(f"unapproved base_case_id: {base_case_id}")
        if revision_kind not in _REVISION_KINDS:
            raise ValueError(f"unsupported revision_kind: {revision_kind}")
        seen_base.add(base_case_id)
        seen_new.add(new_case_id)
        revisions.append(
            ReviewRevision(
                base_case_id=base_case_id,
                new_case_id=new_case_id,
                question=scalar_values["question"],
                revision_kind=revision_kind,
                perspective_evidence=tuple(evidence),
                review_rationale=scalar_values["review_rationale"],
            )
        )
    if seen_base != APPROVED_V4_REVISION_IDS:
        missing = sorted(APPROVED_V4_REVISION_IDS - seen_base)
        extra = sorted(seen_base - APPROVED_V4_REVISION_IDS)
        raise ValueError(
            "approved base_case_id set differs: "
            f"missing={missing}, extra={extra}"
        )
    return revisions


def _program_from_resolution(
    sources: tuple[str, ...], group_keys: tuple[str, ...], selector: str
) -> CarbonQLProgram:
    return CarbonQLProgram.from_dict(
        {
            "steps": [
                {"op": selector},
                {"op": "CarbonAtoms", "source": list(sources)},
                {"op": "GroupBy", "keys": list(group_keys)},
                {"op": "Aggregate", "metric": "sum_kgCO2e"},
            ]
        }
    )


def _reference_for_program(
    program: CarbonQLProgram, selected_component_ids: tuple[str, ...]
) -> ReferenceSpec:
    return ReferenceSpec(
        projection_perspective=derive_projection_perspective(program),
        selector_component_ids=selected_component_ids,
        sources=_sources_from_program(program),
        group_keys=_groups_from_program(program),
        join_required_sources=_join_sources_from_program(program),
    )


def _replace_row_case(
    row: StratifiedCarbonQLCase,
    *,
    case: CarbonQLCase,
    rewrite_rationale: str | None = None,
) -> StratifiedCarbonQLCase:
    return StratifiedCarbonQLCase(
        case=case,
        view_combination=row.view_combination,
        language_tier=row.language_tier,
        grounding_mode=row.grounding_mode,
        rewrite_rationale=rewrite_rationale or row.rewrite_rationale,
    )


def build_reviewed_v4_cases(
    context: CanonicalV2Context,
    rewrite_path: Path = DEFAULT_REWRITE_PATH,
    revision_path: Path = DEFAULT_REVISION_PATH,
) -> list[ReviewedCarbonQLCase]:
    rows = build_stratified_cases(context, load_rewrite_specs(rewrite_path))
    revisions = {item.base_case_id: item for item in load_review_revisions(revision_path)}
    reviewed: list[ReviewedCarbonQLCase] = []
    for row in rows:
        revision = revisions.get(row.case.case_id)
        if revision is None:
            reviewed.append(ReviewedCarbonQLCase(row=row))
            continue
        case = row.case
        if revision.revision_kind == "automatic_join_correction":
            program_data = case.gold_program.to_dict()
            program_data["steps"] = [
                step
                for step in program_data["steps"]
                if step["op"] != "JoinByAttribution"
            ]
            program = CarbonQLProgram.from_dict(program_data)
            reference = _reference_for_program(
                program, case.reference.selector_component_ids
            )
            case = CarbonQLCase(
                case_id=case.case_id,
                question=revision.question,
                category=case.category,
                gold_program=program,
                expected_compiler_status="valid",
                reference=reference,
                expected_view_signature=derive_view_signature(program).to_dict(),
                expected_holes=(),
            )
        else:
            sources, group_keys, selector = _REVISED_PROGRAM_SPECS[revision.new_case_id]
            program = _program_from_resolution(sources, group_keys, selector)
            selected = (
                row.case.reference.selector_component_ids
                if selector == "SelectClicked"
                else ()
            )
            case = CarbonQLCase(
                case_id=revision.new_case_id,
                question=revision.question,
                category="cross_view",
                gold_program=program,
                expected_compiler_status="valid",
                reference=_reference_for_program(program, selected),
                expected_view_signature=derive_view_signature(program).to_dict(),
                expected_holes=(),
            )
        reviewed.append(
            ReviewedCarbonQLCase(
                row=_replace_row_case(
                    row, case=case, rewrite_rationale=revision.review_rationale
                ),
                supersedes_case_id=revision.base_case_id,
                review_revision_kind=revision.revision_kind,
                perspective_evidence=revision.perspective_evidence,
            )
        )
    return reviewed


def _tuple_arg(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _resolution_signature(
    program: CarbonQLProgram,
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    selectors = [
        step.op
        for step in program.steps
        if step.op in {"SelectClicked", "SelectProject", "ResolveEntities"}
    ]
    carbon_steps = [step for step in program.steps if step.op == "CarbonAtoms"]
    group_steps = [step for step in program.steps if step.op == "GroupBy"]
    if (
        len(selectors) != 1
        or not program.steps
        or program.steps[0].op != selectors[0]
        or len(carbon_steps) != 1
        or len(group_steps) != 1
    ):
        return None
    sources = _tuple_arg(carbon_steps[0].args.get("source"))
    group_keys = _tuple_arg(group_steps[0].args.get("keys"))
    if sources is None or group_keys is None:
        return None
    return selectors[0], sources, group_keys


def _join_signatures(program: CarbonQLProgram) -> tuple[tuple[str, ...] | None, ...]:
    return tuple(
        _tuple_arg(step.args.get("required_sources"))
        for step in program.steps
        if step.op == "JoinByAttribution"
    )


def audit_reviewed_v4_cases(
    rows: Sequence[ReviewedCarbonQLCase],
    context: CanonicalV2Context,
    truth_by_case_id: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    audit = dict(
        audit_stratified_cases(
            [row.row for row in rows], context, truth_by_case_id=truth_by_case_id
        )
    )
    expected_revision_kinds = {
        case_id: (
            "implicit_view_resolution"
            if selector == "SelectClicked"
            else "implicit_grouping_resolution"
        )
        for case_id, (_, _, selector) in _REVISED_PROGRAM_SPECS.items()
    }
    expected_revision_kinds.update(
        {
            case_id: "automatic_join_correction"
            for case_id in _AUTOMATIC_JOIN_CORRECTION_CASE_IDS
        }
    )
    rows_by_id = {row.case.case_id: row for row in rows}
    changed_case_ids = sorted(
        row.case.case_id for row in rows if row.case.case_id in expected_revision_kinds
    )
    unchanged_case_ids = sorted(
        row.case.case_id for row in rows if row.case.case_id not in expected_revision_kinds
    )
    revision_kind_counts = Counter(
        expected_revision_kinds.get(row.case.case_id, "unchanged") for row in rows
    )
    implicit_case_ids = sorted(
        row.case.case_id for row in rows if row.case.case_id in _REVISED_PROGRAM_SPECS
    )
    automatic_case_ids = sorted(
        row.case.case_id
        for row in rows
        if row.case.case_id in _AUTOMATIC_JOIN_CORRECTION_CASE_IDS
    )

    implicit_mismatch: list[str] = []
    for case_id, expected in _REVISED_PROGRAM_SPECS.items():
        row = rows_by_id.get(case_id)
        if row is None:
            implicit_mismatch.append(case_id)
            continue
        sources, group_keys, selector = expected
        if (
            _resolution_signature(row.case.gold_program)
            != (selector, sources, group_keys)
            or (
                row.case.reference.sources,
                row.case.reference.group_keys,
            )
            != (sources, group_keys)
            or not validate_semantic_coverage(
                row.case.question,
                row.case.reference.selector_component_ids,
                row.case.gold_program,
            ).valid
        ):
            implicit_mismatch.append(case_id)

    join_violations: list[str] = []
    for row in rows:
        joins = _join_signatures(row.case.gold_program)
        reference_join = row.case.reference.join_required_sources
        if row.case.case_id in _ALLOWED_JOIN_CASE_IDS:
            if len(joins) != 1 or joins[0] != reference_join:
                join_violations.append(row.case.case_id)
        elif joins or reference_join:
            join_violations.append(row.case.case_id)

    metadata_mismatch = [
        row.case.case_id
        for row in rows
        if row.review_revision_kind
        != expected_revision_kinds.get(row.case.case_id, "")
    ]
    evidence_mismatch: list[str] = []
    for row in rows:
        if row.case.case_id not in expected_revision_kinds:
            continue
        question = row.case.question.casefold()
        if not row.perspective_evidence or any(
            span != "selected_component_ids" and span.casefold() not in question
            for span in row.perspective_evidence
        ):
            evidence_mismatch.append(row.case.case_id)

    audit.update(
        {
            "changed_case_count": len(changed_case_ids),
            "changed_case_ids": changed_case_ids,
            "unchanged_case_count": len(unchanged_case_ids),
            "unchanged_case_ids": unchanged_case_ids,
            "revision_kind_counts": dict(sorted(revision_kind_counts.items())),
            "implicit_view_resolution_case_count": len(implicit_case_ids),
            "implicit_view_resolution_case_ids": implicit_case_ids,
            "implicit_view_resolution_mismatch_count": len(implicit_mismatch),
            "implicit_view_resolution_mismatch_case_ids": sorted(implicit_mismatch),
            "automatic_join_correction_case_count": len(automatic_case_ids),
            "automatic_join_correction_case_ids": automatic_case_ids,
            "automatic_join_violation_count": len(join_violations),
            "automatic_join_violation_case_ids": sorted(join_violations),
            "perspective_evidence_mismatch_count": len(evidence_mismatch),
            "perspective_evidence_mismatch_case_ids": sorted(evidence_mismatch),
            "revision_metadata_mismatch_count": len(metadata_mismatch),
            "revision_metadata_mismatch_case_ids": sorted(metadata_mismatch),
        }
    )
    return audit


def freeze_reviewed_v4_benchmark(
    context: CanonicalV2Context,
    output_root: Path,
    rewrite_path: Path = DEFAULT_REWRITE_PATH,
    revision_path: Path = DEFAULT_REVISION_PATH,
    freeze_id: str | None = None,
) -> dict[str, str]:
    resolved_id = freeze_id or datetime.now().strftime("%Y%m%d_%H%M%S_v2")
    if not _SAFE_ID.fullmatch(resolved_id) or "v2" not in resolved_id.lower():
        raise ValueError("freeze_id must be a safe v2 id")
    rows = build_reviewed_v4_cases(context, rewrite_path, revision_path)
    truth = [heldout_reference_evaluate(row.case, context) for row in rows]
    audit = audit_reviewed_v4_cases(
        rows,
        context,
        truth_by_case_id={row.case.case_id: result for row, result in zip(rows, truth)},
    )
    graph_path = context.release_dir / "multigranular_carbon_kg.json"
    revision_hash = _hash_file(revision_path)
    rewrite_hash = _hash_file(rewrite_path)
    return _write_freeze(
        rows=[row.row for row in rows],
        truth=truth,
        audit={
            **audit,
            "kg_dir": str(context.release_dir.resolve()),
            "graph_sha256": _hash_file(graph_path),
            "rewrite_spec_sha256": rewrite_hash,
            "revision_spec_sha256": revision_hash,
        },
        output_root=output_root,
        freeze_id=resolved_id,
        manifest_extra={
            "schema_version": "controlled-v2-reviewed-v4",
            "revision_spec_sha256": revision_hash,
            "rewrite_spec_sha256": rewrite_hash,
        },
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kg-dir", type=Path, default=_DEFAULT_KG_DIR)
    parser.add_argument("--rewrite-spec", type=Path, default=DEFAULT_REWRITE_PATH)
    parser.add_argument("--revision-spec", type=Path, default=DEFAULT_REVISION_PATH)
    parser.add_argument("--output-root", type=Path, default=_DEFAULT_REVIEWED_OUTPUT_ROOT)
    parser.add_argument("--freeze-id", default="")
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)
    context = load_canonical_v2_context(
        args.kg_dir, allow_synthetic=args.allow_synthetic
    )
    outputs = freeze_reviewed_v4_benchmark(
        context,
        args.output_root,
        args.rewrite_spec,
        args.revision_spec,
        freeze_id=args.freeze_id or None,
    )
    audit = json.loads(Path(outputs["audit_json"]).read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "freeze_id": audit["freeze_id"],
                "release_id": audit["release_id"],
                "case_count": audit["case_count"],
                "oracle_mismatch_count": audit["oracle_mismatch_count"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "APPROVED_V4_REVISION_IDS",
    "DEFAULT_REVISION_PATH",
    "DEFAULT_REWRITE_PATH",
    "ReviewRevision",
    "ReviewedCarbonQLCase",
    "audit_reviewed_v4_cases",
    "build_reviewed_v4_cases",
    "freeze_reviewed_v4_benchmark",
    "heldout_reference_evaluate",
    "load_review_revisions",
]
