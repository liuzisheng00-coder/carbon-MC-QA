"""Audit the effect of aligning perspective derivation with Equation M3.a.

The legacy rule gave the selector (SelectClicked, component ResolveEntities) and
JoinByAttribution priority over the organizing dimensions. Equation M3.a reads the
perspective off G(P) alone and falls back to the product perspective only when G(P)
is empty. This script reports every gold program whose projection perspective and
view signature change under the aligned rule, so the frozen benchmarks can be
re-issued with an explicit flip list.

Run before and after the change to `dm2c_carbonql.derive_projection_perspective`:
the `live_rule_matches` field records which of the two rules the imported module
currently implements.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from dm2c_carbonql import (
    FACTOR_KEYS,
    GROUP_KEYS,
    MATERIAL_KEYS,
    PRODUCT_KEYS,
    PROCESS_KEYS,
    SOURCE_KEYS,
    CarbonQLProgram,
    _dimension_keys,
    _sources_from_program,
    derive_projection_perspective,
)

PERSPECTIVE_TO_BASE_VIEWS = {
    "product": ("product",),
    "material_source": ("material",),
    "energy_source": ("process",),
    "source_union": ("material", "process"),
}

BENCHMARKS = {
    "e4b": (
        "outputs/research_experiments/e4b_carbonql_typed_completed_real/"
        "20260718_153407_deepseek/e4b_cases.jsonl"
    ),
    "e4c": (
        "outputs/research_experiments/e4c_carbonql_heldout/"
        "e4c_reviewed_slabfix_v2_20260723/e4c_cases.jsonl"
    ),
    "e4d_v5": (
        "outputs/research_experiments/e4d_candidate_views/"
        "20260720_typed_completed_candidate_v5/e4d_cases.jsonl"
    ),
    "e4d_slabfix": (
        "outputs/research_experiments/e4d_candidate_views_v2/"
        "e4d_synthetic_slabfix_v2_20260723b/e4d_cases_v2.jsonl"
    ),
}


def legacy_perspective(program: CarbonQLProgram) -> str:
    """The rule in force before the M3.a alignment (selector has priority)."""
    selector = program.steps[0]
    dimension_keys = _dimension_keys(program)
    if (
        selector.op == "SelectClicked"
        or (
            selector.op == "ResolveEntities"
            and str(selector.args.get("entity_type") or "component") == "component"
        )
        or bool(dimension_keys & PRODUCT_KEYS)
        or any(step.op == "JoinByAttribution" for step in program.steps)
    ):
        return "product"

    selector_type = (
        str(selector.args.get("entity_type") or "component")
        if selector.op == "ResolveEntities"
        else ""
    )
    has_material = selector_type == "material" or bool(dimension_keys & MATERIAL_KEYS)
    has_process = selector_type == "process" or bool(dimension_keys & PROCESS_KEYS)
    if has_material and has_process:
        return "source_union"
    if has_material:
        return "material_source"
    if has_process:
        return "energy_source"

    if dimension_keys & (FACTOR_KEYS | SOURCE_KEYS):
        sources = set(_sources_from_program(program))
        if sources == {"material"}:
            return "material_source"
        if sources == {"process"}:
            return "energy_source"
        return "source_union"
    return "product"


def m3a_perspective(program: CarbonQLProgram) -> str:
    """Equation M3.a: the perspective is a function of G(P).

    The empty-G(P) fallback carries one exception, added after the fallback was
    found to report the product-attributed part of a process account as though
    it were the whole of it. Reading material atoms through the product
    projection is lossless because every material record is carried by one
    component; reading process atoms through it is not.
    """
    dimension_keys = _dimension_keys(program)
    if not dimension_keys:
        if set(_sources_from_program(program)) == {"process"}:
            return "energy_source"
        return "product"
    if dimension_keys & PRODUCT_KEYS:
        return "product"

    has_material = bool(dimension_keys & MATERIAL_KEYS)
    has_process = bool(dimension_keys & PROCESS_KEYS)
    if has_material and has_process:
        return "source_union"
    if has_material:
        return "material_source"
    if has_process:
        return "energy_source"

    if dimension_keys & (FACTOR_KEYS | SOURCE_KEYS):
        sources = set(_sources_from_program(program))
        if sources == {"material"}:
            return "material_source"
        if sources == {"process"}:
            return "energy_source"
        return "source_union"
    return "product"


def flip_cause(program: CarbonQLProgram) -> str:
    """Attribute a flip to the legacy sub-rule that produced it."""
    selector = program.steps[0]
    dimension_keys = _dimension_keys(program)
    if dimension_keys & PRODUCT_KEYS:
        return "unattributed"

    selector_is_product = selector.op == "SelectClicked" or (
        selector.op == "ResolveEntities"
        and str(selector.args.get("entity_type") or "component") == "component"
    )
    has_join = any(step.op == "JoinByAttribution" for step in program.steps)
    if selector_is_product and has_join:
        return "selector_and_join_product_priority"
    if selector_is_product:
        return "selector_product_priority"
    if has_join:
        return "join_product_priority"

    selector_type = (
        str(selector.args.get("entity_type") or "component")
        if selector.op == "ResolveEntities"
        else ""
    )
    if selector_type in {"material", "process"}:
        return "selector_source_intent"
    return "unattributed"


def numeric_impact(
    root: Path, release: str, flips: list[dict[str, Any]], allow_synthetic: bool
) -> dict[str, Any]:
    """Execute every flipped program under both rules and compare the numbers.

    The perspective selects which projection set the executor reads, so a flip is
    only safe for the frozen results if the reported totals are unchanged.
    """
    import dm2c_carbonql_executor as executor_module
    from dm2c_carbonql_executor import CarbonQLExecutor

    executor = CarbonQLExecutor.from_release(
        root / release, allow_synthetic=allow_synthetic
    )
    original = executor_module.derive_projection_perspective
    comparisons: list[dict[str, Any]] = []

    def run(program: CarbonQLProgram, selected: tuple[str, ...]) -> dict[str, Any]:
        result = executor.execute(program, selected)
        return {
            "status": result.status,
            "total_kgCO2e": result.summary.get("total_kgCO2e"),
            "row_count": result.summary.get("row_count"),
        }

    try:
        for item in flips:
            program = CarbonQLProgram.from_dict(item["program_payload"])
            selected = tuple(item["selected_component_ids"])

            executor_module.derive_projection_perspective = legacy_perspective
            before = run(program, selected)
            executor_module.derive_projection_perspective = m3a_perspective
            after = run(program, selected)

            comparisons.append(
                {
                    "benchmark": item["benchmark"],
                    "case_id": item["case_id"],
                    "slot": item["slot"],
                    "legacy": before,
                    "m3a": after,
                    "total_unchanged": before["total_kgCO2e"] == after["total_kgCO2e"],
                    "status_unchanged": before["status"] == after["status"],
                }
            )
    finally:
        executor_module.derive_projection_perspective = original

    changed = [item for item in comparisons if not item["total_unchanged"]]
    return {
        "release": release,
        "allow_synthetic": allow_synthetic,
        "compared_count": len(comparisons),
        "total_changed_count": len(changed),
        "status_changed_count": sum(
            1 for item in comparisons if not item["status_unchanged"]
        ),
        "comparisons": comparisons,
    }


def _iter_programs(name: str, path: Path) -> Iterator[dict[str, Any]]:
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        case_id = str(row.get("case_id") or f"line{line_number}")

        selected = tuple(
            str(value)
            for value in (
                row.get("selected_component_ids")
                or row.get("grounded_component_ids")
                or ()
            )
        )

        for field in ("gold_program", "prototype_program"):
            payload = row.get(field)
            if isinstance(payload, dict) and payload.get("steps"):
                yield {
                    "benchmark": name,
                    "case_id": case_id,
                    "slot": field,
                    "question": row.get("question", ""),
                    "selected_component_ids": selected,
                    "frozen_base_views": tuple(
                        (row.get("gold_view_signature") or {}).get("base_views") or ()
                    ),
                    "program": payload,
                }

        for candidate in (row.get("candidate_set") or {}).get("candidates", ()):
            payload = candidate.get("program")
            if isinstance(payload, dict) and payload.get("steps"):
                yield {
                    "benchmark": name,
                    "case_id": case_id,
                    "slot": f"candidate:{candidate.get('candidate_id')}",
                    "question": row.get("question", ""),
                    "selected_component_ids": selected,
                    "frozen_base_views": (),
                    "program": payload,
                }


def audit(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    missing: list[str] = []
    stale_vocabulary: dict[str, list[str]] = {}
    live_matches_legacy = True
    live_matches_m3a = True

    for name, relative in BENCHMARKS.items():
        path = root / relative
        if not path.exists():
            missing.append(relative)
            continue
        for entry in _iter_programs(name, path):
            program = CarbonQLProgram.from_dict(entry["program"])
            unknown = sorted(_dimension_keys(program) - GROUP_KEYS)
            if unknown:
                stale_vocabulary[f"{entry['benchmark']}/{entry['case_id']}"] = unknown
            legacy = legacy_perspective(program)
            aligned = m3a_perspective(program)
            live = derive_projection_perspective(program)
            live_matches_legacy &= live == legacy
            live_matches_m3a &= live == aligned

            record = {
                "benchmark": entry["benchmark"],
                "case_id": entry["case_id"],
                "slot": entry["slot"],
                "question": entry["question"],
                "dimensions": sorted(_dimension_keys(program)),
                "sources": list(_sources_from_program(program)),
                "legacy_perspective": legacy,
                "m3a_perspective": aligned,
                "legacy_base_views": list(PERSPECTIVE_TO_BASE_VIEWS[legacy]),
                "m3a_base_views": list(PERSPECTIVE_TO_BASE_VIEWS[aligned]),
                "frozen_base_views": list(entry["frozen_base_views"]),
            }
            entries.append(record)
            if legacy != aligned:
                record["flip_cause"] = flip_cause(program)
                record["program_payload"] = entry["program"]
                record["selected_component_ids"] = list(
                    entry["selected_component_ids"]
                )
                flips.append(record)

    by_benchmark = Counter(item["benchmark"] for item in flips)
    return {
        "program_count": len(entries),
        "flip_count": len(flips),
        "flip_count_by_benchmark": dict(sorted(by_benchmark.items())),
        "flip_count_by_cause": dict(
            sorted(Counter(item["flip_cause"] for item in flips).items())
        ),
        "flip_transitions": dict(
            sorted(
                Counter(
                    f"{item['legacy_perspective']} -> {item['m3a_perspective']}"
                    for item in flips
                ).items()
            )
        ),
        "programs_per_benchmark": dict(
            sorted(Counter(item["benchmark"] for item in entries).items())
        ),
        "live_rule_matches": (
            "m3a"
            if live_matches_m3a and not live_matches_legacy
            else "legacy"
            if live_matches_legacy and not live_matches_m3a
            else "both_rules_agree_on_this_corpus"
            if live_matches_legacy and live_matches_m3a
            else "neither"
        ),
        "frozen_signature_rows_to_reissue": sorted(
            f"{item['benchmark']}/{item['case_id']}"
            for item in flips
            if item["frozen_base_views"]
            and item["frozen_base_views"] != item["m3a_base_views"]
        ),
        "missing_benchmarks": missing,
        # A dimension outside the canonical-v2 vocabulary matches no perspective
        # family, so both rules fall through to product and the case cannot
        # exercise the perspective rule at all.
        "stale_vocabulary_case_count": len(stale_vocabulary),
        "stale_vocabulary_keys": sorted(
            {key for keys in stale_vocabulary.values() for key in keys}
        ),
        "stale_vocabulary_cases": dict(sorted(stale_vocabulary.items())),
        "flips": flips,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# M3.a perspective alignment flip list",
        "",
        f"Programs audited: {report['program_count']}",
        f"Flipped programs: {report['flip_count']}",
        f"Live rule in `dm2c_carbonql`: {report['live_rule_matches']}",
        "",
        f"Programs whose grouping uses a dimension outside the canonical-v2 "
        f"vocabulary: {report['stale_vocabulary_case_count']} "
        f"({', '.join(report['stale_vocabulary_keys']) or 'none'}). "
        "Both rules classify these as product regardless of intent.",
        "",
        "## Flips by benchmark",
        "",
    ]
    for key, value in report["flip_count_by_benchmark"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Flips by legacy sub-rule", ""]
    for key, value in report["flip_count_by_cause"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Transitions", ""]
    for key, value in report["flip_transitions"].items():
        lines.append(f"- {key}: {value}")
    impact = report.get("numeric_impact")
    if impact:
        lines += [
            "",
            "## Numeric impact",
            "",
            f"Release: `{impact['release']}`",
            f"Programs re-executed: {impact['compared_count']}",
            f"Reported totals changed: {impact['total_changed_count']}",
            f"Query statuses changed: {impact['status_changed_count']}",
            "",
        ]
        for item in impact["comparisons"]:
            lines.append(
                f"- `{item['benchmark']}/{item['case_id']}`: "
                f"{item['legacy']['total_kgCO2e']} -> {item['m3a']['total_kgCO2e']} "
                f"({item['legacy']['status']} -> {item['m3a']['status']}, "
                f"rows {item['legacy']['row_count']} -> {item['m3a']['row_count']})"
            )
    lines += ["", "## Flipped programs", ""]
    for item in report["flips"]:
        lines.append(
            f"- `{item['benchmark']}/{item['case_id']}` [{item['slot']}] "
            f"G={item['dimensions'] or '{}'} sources={item['sources']}: "
            f"{item['legacy_base_views']} -> {item['m3a_base_views']} "
            f"({item['flip_cause']})"
        )
        if item["question"]:
            lines.append(f"  - {item['question']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument(
        "--out",
        default="outputs/research_experiments/m3a_perspective_alignment",
        help="Output directory for the flip list",
    )
    parser.add_argument(
        "--release",
        default=(
            "outputs/research_experiments/"
            "m2_typed_completed_20260727_real_a1_fixture_carriernamed"
        ),
        help="Canonical-v2 release used for the numeric impact check",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Permit a controlled fixture with synthetic factory inputs",
    )
    parser.add_argument(
        "--skip-numeric",
        action="store_true",
        help="Report the flip list without executing the flipped programs",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report = audit(root)
    if not args.skip_numeric and report["flips"]:
        report["numeric_impact"] = numeric_impact(
            root, args.release, report["flips"], args.allow_synthetic
        )
    out_dir = root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m3a_perspective_flip_audit.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "m3a_perspective_flip_audit.md").write_text(
        render_markdown(report), encoding="utf-8"
    )

    print(f"programs audited      : {report['program_count']}")
    print(f"flipped programs      : {report['flip_count']}")
    print(f"live rule             : {report['live_rule_matches']}")
    print(f"by benchmark          : {report['flip_count_by_benchmark']}")
    print(f"by cause              : {report['flip_count_by_cause']}")
    print(f"transitions           : {report['flip_transitions']}")
    print(f"frozen rows to reissue: {len(report['frozen_signature_rows_to_reissue'])}")
    print(f"stale-vocabulary cases: {report['stale_vocabulary_case_count']} "
          f"{report['stale_vocabulary_keys']}")
    impact = report.get("numeric_impact")
    if impact:
        print(f"numeric check release : {impact['release']}")
        print(f"programs re-executed  : {impact['compared_count']}")
        print(f"totals changed        : {impact['total_changed_count']}")
        print(f"statuses changed      : {impact['status_changed_count']}")
    if report["missing_benchmarks"]:
        print(f"missing benchmarks    : {report['missing_benchmarks']}")
    print(f"written to            : {out_dir}")


if __name__ == "__main__":
    main()
