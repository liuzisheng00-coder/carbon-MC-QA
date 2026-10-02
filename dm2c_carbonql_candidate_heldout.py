#!/usr/bin/env python3
"""Re-run the 24-question E4d candidate-view benchmark on a canonical-v2 release.

The question set was frozen on 2026-07-20 against an earlier KG. Its questions
and prototype programs are release-independent: a prototype selects the project,
the clicked components, or an IFC class, and never names a graph node. Only the
candidate set and the grounded component set depend on the release, so both are
recomputed here rather than trusted from the old freeze.

Each question yields three candidates that differ only in the CarbonAtoms source
(material, process, both). All three execute deterministically, and their
decision signatures are compared to classify the question as view robust or view
sensitive. Two paraphrases share one decision unit and must agree.

The pinned spec carries one migration against the 2026-07-20 original: the
CarbonQL vocabulary since renamed ResolveEntities entity_type product to
component and property ifcType to ifcClass. Both renames are one-to-one and
meaning-preserving, and the resolved class value IfcBeam is unchanged.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    components_for_ifc_class,
    dimension_ids,
    load_canonical_v2_context,
    lookup_component,
)
from dm2c_carbonql import CarbonQLProgram, GraphSchema
from dm2c_carbonql_candidate_benchmark import (
    CandidateBenchmarkCase,
    candidate_reference_evaluate,
)
from dm2c_carbonql_candidate_views import (
    compile_candidate_set,
    resolve_perspective_constraint,
)
from dm2c_carbonql_executor import CarbonQLExecutor

DEFAULT_SPEC_PATH = Path("inputs/e4d_candidate_question_spec_v5.jsonl")
DEFAULT_KG_DIR = Path(
    "outputs/research_experiments/m2_typed_completed_20260727_real_a1_fixture_carriernamed"
)
DEFAULT_OUTPUT_ROOT = Path("outputs/research_experiments/e4d_candidate_views_v2")
# Only a decision that ranks or compares can flip when the carbon source changes.
FLIP_ELIGIBLE_OPERATIONS = ("compare", "rank")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    case_id: str
    operation: str
    grounding_mode: str
    paraphrase_index: int
    question: str
    rewrite_rationale: str
    expected_ifc_type: str
    expected_type_term: str
    selected_component_ids: tuple[str, ...]
    base_target_pool: tuple[str, ...]
    eligible_selection_pool: tuple[tuple[str, ...], ...]
    prototype_program: CarbonQLProgram

    @property
    def decision_unit(self) -> str:
        """Two paraphrases of one question share a decision unit."""
        return f"{self.grounding_mode}_{self.operation}"


def load_question_spec(path: Path) -> tuple[tuple[QuestionSpec, ...], str]:
    raw = path.read_bytes()
    rows = [
        json.loads(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    ]
    specs = tuple(
        QuestionSpec(
            case_id=str(row["case_id"]),
            operation=str(row["operation"]),
            grounding_mode=str(row["grounding_mode"]),
            paraphrase_index=int(row["paraphrase_index"]),
            question=str(row["question"]),
            rewrite_rationale=str(row.get("rewrite_rationale") or ""),
            expected_ifc_type=str(row.get("expected_ifc_type") or ""),
            expected_type_term=str(row.get("expected_type_term") or ""),
            selected_component_ids=tuple(row.get("selected_component_ids") or ()),
            base_target_pool=tuple(row.get("base_target_pool") or ()),
            eligible_selection_pool=tuple(
                tuple(entry) for entry in (row.get("eligible_selection_pool") or ())
            ),
            prototype_program=CarbonQLProgram.from_dict(row["prototype_program"]),
        )
        for row in rows
    )
    return specs, hashlib.sha256(raw).hexdigest()


def _global_ids(context: CanonicalV2Context, entity_ids: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for entity_id in entity_ids:
        node = lookup_component(context, entity_id)
        props = node.get("props") or node
        global_id = str(props.get("globalId") or "")
        if global_id:
            out.append(global_id)
    return tuple(sorted(out))


def ground_case(spec: QuestionSpec, context: CanonicalV2Context) -> tuple[str, ...]:
    """Recompute the component set a class-grounded question commits to."""
    if spec.grounding_mode != "natural_class" or not spec.expected_ifc_type:
        return ()
    return _global_ids(context, components_for_ifc_class(context, spec.expected_ifc_type))


def build_case(
    spec: QuestionSpec, context: CanonicalV2Context, schema: GraphSchema
) -> CandidateBenchmarkCase:
    constraint = resolve_perspective_constraint(spec.question, spec.selected_component_ids)
    candidate_set = compile_candidate_set(constraint, spec.prototype_program, schema)
    return CandidateBenchmarkCase(
        case_id=spec.case_id,
        operation=spec.operation,
        question=spec.question,
        selected_component_ids=spec.selected_component_ids,
        base_target_pool=spec.base_target_pool,
        prototype_program=spec.prototype_program,
        candidate_set=candidate_set,
    )


def audit_heldout_candidates(
    specs: Sequence[QuestionSpec],
    cases: Sequence[CandidateBenchmarkCase],
    results: Sequence[Any],
    context: CanonicalV2Context,
    *,
    spec_sha256: str,
) -> dict[str, Any]:
    executor = CarbonQLExecutor.from_context(context)

    oracle_mismatches: list[dict[str, str]] = []
    for case, result in zip(cases, results):
        by_id = {execution.candidate_id: execution for execution in result.executions}
        for candidate in case.candidate_set.candidates:
            observed = executor.execute(
                candidate.program, case.selected_component_ids
            ).to_dict()
            expected = by_id[candidate.candidate_id].to_dict()
            for key in ("candidate_id", "program_sha256", "decision_probe_rows"):
                expected.pop(key, None)
            observed.pop("holes", None)
            if observed != expected:
                oracle_mismatches.append(
                    {"case_id": case.case_id, "candidate_id": candidate.candidate_id}
                )

    candidate_counts: dict[str, int] = {}
    for case in cases:
        key = str(len(case.candidate_set.candidates))
        candidate_counts[key] = candidate_counts.get(key, 0) + 1

    # A decision unit is one question in two paraphrases; both must classify alike.
    by_unit: dict[str, list[tuple[QuestionSpec, Any]]] = {}
    for spec, result in zip(specs, results):
        by_unit.setdefault(spec.decision_unit, []).append((spec, result))
    paraphrase_mismatches = sorted(
        unit
        for unit, entries in by_unit.items()
        if len({entry[1].decision_class for entry in entries}) > 1
    )

    unit_classes = {
        unit: entries[0][1].decision_class for unit, entries in sorted(by_unit.items())
    }
    unit_class_counts: dict[str, int] = {}
    for decision_class in unit_classes.values():
        unit_class_counts[str(decision_class)] = (
            unit_class_counts.get(str(decision_class), 0) + 1
        )

    flip_units = sorted(
        unit
        for unit, entries in by_unit.items()
        if entries[0][0].operation in FLIP_ELIGIBLE_OPERATIONS
    )
    flipped = sorted(unit for unit in flip_units if unit_classes[unit] == "view_sensitive")

    grounding = {
        spec.case_id: ground_case(spec, context)
        for spec in specs
        if spec.grounding_mode == "natural_class"
    }
    grounded_counts = sorted({len(value) for value in grounding.values()})

    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[str(result.status)] = status_counts.get(str(result.status), 0) + 1

    return {
        "schema_version": "controlled-v2-candidate-heldout",
        "release_id": str(context.manifest["releaseId"]),
        "release_profile": str(context.manifest["releaseProfile"]),
        "synthetic_energy": context.synthetic_energy,
        "question_spec_sha256": spec_sha256,
        "case_count": len(cases),
        "decision_unit_count": len(by_unit),
        "available_component_count": len(dimension_ids(context, "component")),
        "accepted_emission_count": len(context.emissions),
        "rejected_validation_count": sum(
            row["status"] == "rejected" for row in context.validation_rows
        ),
        "candidate_count": sum(len(case.candidate_set.candidates) for case in cases),
        "candidates_per_case_counts": candidate_counts,
        "oracle_mismatch_count": len(oracle_mismatches),
        "oracle_mismatches": oracle_mismatches,
        "operation_question_counts": _counts(spec.operation for spec in specs),
        "grounding_mode_question_counts": _counts(spec.grounding_mode for spec in specs),
        "candidate_status_counts": status_counts,
        "decision_class_unit_counts": unit_class_counts,
        "decision_class_by_unit": unit_classes,
        "paraphrase_unit_mismatch_count": len(paraphrase_mismatches),
        "paraphrase_unit_mismatch_units": paraphrase_mismatches,
        "flip_unit_count": len(flip_units),
        "flip_rate_numerator": len(flipped),
        "flip_rate_denominator": len(flip_units),
        "flip_rate": (round(len(flipped) / len(flip_units), 4) if flip_units else None),
        "flipped_units": flipped,
        "class_grounded_counts": grounded_counts,
        "class_grounding_question_count": len(grounding),
        "human_review_complete": False,
        "paper_ready": False,
    }


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items()))


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
        for row in rows
    ).encode("utf-8")


def freeze_heldout_candidates(
    specs: Sequence[QuestionSpec],
    context: CanonicalV2Context,
    *,
    output_root: Path,
    freeze_id: str,
    spec_sha256: str,
) -> Path:
    if not _SAFE_ID.fullmatch(freeze_id) or "v2" not in freeze_id.lower():
        raise ValueError("freeze_id must be a safe controlled-v2 successor id")
    output_dir = output_root / freeze_id
    staging = output_root / f".{freeze_id}.staging"
    if output_dir.exists() or staging.exists():
        raise FileExistsError(output_dir if output_dir.exists() else staging)

    schema = GraphSchema.from_context(context)
    cases = [build_case(spec, context, schema) for spec in specs]
    results = [candidate_reference_evaluate(case, context) for case in cases]
    audit = audit_heldout_candidates(
        specs, cases, results, context, spec_sha256=spec_sha256
    )
    if audit["oracle_mismatch_count"] != 0:
        raise ValueError("candidate oracle mismatch prevents freeze")
    if audit["paraphrase_unit_mismatch_count"] != 0:
        raise ValueError("paraphrases of one decision unit disagree; freeze blocked")

    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        case_rows = [
            {
                **case.to_dict(),
                "grounding_mode": spec.grounding_mode,
                "paraphrase_index": spec.paraphrase_index,
                "decision_unit": spec.decision_unit,
                "expected_ifc_type": spec.expected_ifc_type,
                "expected_type_term": spec.expected_type_term,
                "grounded_component_ids": list(ground_case(spec, context)),
                "eligible_selection_pool": [list(e) for e in spec.eligible_selection_pool],
                "rewrite_rationale": spec.rewrite_rationale,
            }
            for spec, case in zip(specs, cases)
        ]
        truth_rows = [
            {"case_id": case.case_id, **result.to_dict()}
            for case, result in zip(cases, results)
        ]
        payloads = {
            "e4d_heldout_cases_v2.jsonl": _jsonl(case_rows),
            "e4d_heldout_truth_v2.jsonl": _jsonl(truth_rows),
            "e4d_heldout_machine_audit_v2.json": json.dumps(
                audit, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
            ).encode("utf-8"),
        }
        manifest = {
            "schema_version": "controlled-v2-candidate-heldout",
            "freeze_id": freeze_id,
            "release_id": str(context.manifest["releaseId"]),
            "question_spec_sha256": spec_sha256,
            "files": {
                name: {
                    "sha256": hashlib.sha256(value).hexdigest().upper(),
                    "size_bytes": len(value),
                }
                for name, value in sorted(payloads.items())
            },
        }
        payloads["e4d_heldout_manifest_v2.json"] = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
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
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--kg-dir", type=Path, default=DEFAULT_KG_DIR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--freeze-id", required=True)
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)

    specs, spec_sha256 = load_question_spec(args.spec)
    context = load_canonical_v2_context(args.kg_dir, allow_synthetic=args.allow_synthetic)
    output_dir = freeze_heldout_candidates(
        specs,
        context,
        output_root=args.output_root,
        freeze_id=args.freeze_id,
        spec_sha256=spec_sha256,
    )
    audit = json.loads(
        (output_dir / "e4d_heldout_machine_audit_v2.json").read_text(encoding="utf-8")
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "release_id": audit["release_id"],
                "case_count": audit["case_count"],
                "decision_unit_count": audit["decision_unit_count"],
                "decision_class_unit_counts": audit["decision_class_unit_counts"],
                "flip_rate": audit["flip_rate"],
                "flip_rate_numerator": audit["flip_rate_numerator"],
                "flip_rate_denominator": audit["flip_rate_denominator"],
                "oracle_mismatch_count": audit["oracle_mismatch_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
