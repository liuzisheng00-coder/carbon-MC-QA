# -*- coding: utf-8 -*-
"""D2: rule-based CarbonQL compiler baseline (no language model).

A deterministic compiler maps the question to a CarbonQL program with keyword rules and a
gazetteer built from the graph (material, component-type, process, carrier, component and
module names). The program then goes through the same validator, executor, answer adapter
and scorer as the language-model compiler, so the difference to the full system isolates
the contribution of the LLM compilation stage.

Rules (applied in order):
  selector   selected BIM items -> SelectClicked; deictic reference ("that panel") ->
             ResolveEntities on an IFC class with singleton cardinality (asks for
             clarification); "... I searched for" or an unknown named target ->
             ResolveEntities by name (unresolved when absent); gazetteer hit on a
             component / module / material / process name -> ResolveEntities by name;
             gazetteer hit on a component-type name -> project scope with a
             component_type_name filter; otherwise SelectProject.
  sources    material cue -> material; factory-energy cue -> process; both or none ->
             material and process; "known" -> known_total.
  grouping   carrier / material / stage / component / process cues; a default per
             source when the operation needs a grouping.
  operation  rank / compare / trace cues; explain -> aggregate with trace; else aggregate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dm2c_canonical_v2_reader import dimension_ids, lookup_dimension  # noqa: E402
from dm2c_carbonql import CarbonQLProgram, GraphSchema, derive_projection_perspective, validate_program  # noqa: E402
from dm2c_e5_e7_experiment_runner import evaluate_benchmark_response, load_benchmark_cases, summarize_results  # noqa: E402
from dm2c_full_qa_experiment_runner import _operation, execute_canonical_query, load_full_qa_context  # noqa: E402
from dm2c_qa_answer_adapter import adapt_observed, map_perspective  # noqa: E402
from dm2c_real_ablation_runner import _scorable_observed  # noqa: E402

DEFAULT_BENCHMARK = ROOT / "outputs/research_experiments/e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc/e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl"
DEFAULT_KG = ROOT / "outputs/research_experiments/multi_module_20260928/releases/typea_clean"
DEFAULT_OUT = ROOT / "outputs/research_experiments/diagnostics_20260930/D2_rule_compiler"

DEICTIC = re.compile(r"\b(?:that|those|this|these)\s+(?:two\s+)?(?:selected\s+)?(?:module\s+)?(panel|panels|module|component|components|record|records|item|items|energy use|factory[- ]energy record|steel item)\b|currently indicated|\bthe steel item\b|\bthe module energy record\b|\bthe selected panel\b", re.I)
SEARCHED = re.compile(r"\b(?:for|of)\s+(the [a-z ]*?(?:item|component|material item|factory record|material|record)[a-z ]*? I searched for)", re.I)
NAMED_TARGET = re.compile(r"\bfor\s+(?:the\s+)?(production line [A-Z0-9\-]+|[A-Za-z0-9\-]+(?: [a-z0-9\-]+){0,6}?(?: above the loading bay| batch(?: and the [a-z\- ]+ batch)?| roof assemblies| roof-cassette variants))", re.I)
MATERIAL_CUE = re.compile(r"\bmaterial", re.I)
PROCESS_CUE = re.compile(r"factory[- ]energy|process[- ]energy|\benergy\b|electricity|diesel|\bstage\b|cutting|welding|coating|production line|factory record|factory carbon", re.I)
KNOWN_CUE = re.compile(r"\bknown\b|\bsupported\b", re.I)
RANK_CUE = re.compile(r"\brank|\bmost\b|largest|highest|\btop\b|from highest to lowest", re.I)
COMPARE_CUE = re.compile(r"\bcompare|\bversus\b|\bvs\.?\b|difference between|which is larger", re.I)
TRACE_CUE = re.compile(r"\btrace|evidence chain|evidence path|how .* is calculated", re.I)
EXPLAIN_CUE = re.compile(r"\bexplain|\bwhy\b", re.I)
CARRIER_CUE = re.compile(r"energy carrier|electricity and diesel|by carrier", re.I)
STAGE_CUE = re.compile(r"\bstages?\b|cutting, welding, and coating|by production stage", re.I)
MATERIAL_GROUP_CUE = re.compile(r"material famil|by material|which material|material groups|each kind of material|kind of material", re.I)
COMPONENT_GROUP_CUE = re.compile(r"which component|by component|component contributes|per component|each component", re.I)
RECORD_CUE = re.compile(r"\brecords?\b", re.I)
CLASS_HINT = (("panel", "IfcWall"), ("wall", "IfcWall"), ("slab", "IfcSlab"), ("floor", "IfcSlab"), ("door", "IfcDoor"), ("window", "IfcWindow"), ("column", "IfcColumn"))


def _props(node) -> dict:
    if isinstance(node, Mapping):
        props = node.get("props", {})
        return dict(props) if isinstance(props, Mapping) else {}
    return {}


def build_gazetteer(canonical) -> dict[str, list[tuple[str, str]]]:
    """dimension -> [(lowercased name, entity id)] sorted by name length, longest first."""
    gaz: dict[str, list[tuple[str, str]]] = {}
    for dim, keys in (("material", ("name",)), ("component_type", ("name",)), ("process", ("activityName", "name")),
                      ("carrier", ("name",)), ("module", ("name",)), ("component", ("name",))):
        entries = []
        for entity_id in dimension_ids(canonical, dim):
            props = _props(lookup_dimension(canonical, dim, entity_id))
            name = next((str(props[k]) for k in keys if props.get(k)), "")
            if len(name) >= 4:
                entries.append((name.casefold(), entity_id, name))
        entries.sort(key=lambda e: -len(e[0]))
        gaz[dim] = entries
    return gaz


def gazetteer_hit(question: str, gaz, dims):
    q = question.casefold()
    for dim in dims:
        for low, entity_id, name in gaz[dim]:
            if low in q:
                return dim, entity_id, name
    return None


def compile_rule(question: str, selected_ids, gaz) -> tuple[CarbonQLProgram, dict]:
    q = question
    notes = {}
    steps: list[dict] = []

    # ---------------------------------------------------------------- selector
    hit = None
    if selected_ids:
        steps.append({"op": "SelectClicked", "ids": list(selected_ids)})
        notes["selector_rule"] = "selected_items"
    elif DEICTIC.search(q):
        m = DEICTIC.search(q)
        word = (m.group(1) or m.group(0)).casefold()
        ifc_class = next((cls for key, cls in CLASS_HINT if key in word), "IfcBeam")
        steps.append({"op": "ResolveEntities", "entity_type": "component", "property": "ifcClass", "value": ifc_class, "cardinality": "singleton"})
        notes["selector_rule"] = f"deictic->{ifc_class}"
    elif SEARCHED.search(q):
        phrase = SEARCHED.search(q).group(1).strip()
        entity_type = "material" if "material" in phrase.casefold() else "process" if "factory" in phrase.casefold() else "component"
        steps.append({"op": "ResolveEntities", "entity_type": entity_type, "property": "name", "value": phrase})
        notes["selector_rule"] = "searched_phrase"
    else:
        hit = gazetteer_hit(q, gaz, ("component", "module", "material", "process"))
        type_hit = gazetteer_hit(q, gaz, ("component_type",))
        named = NAMED_TARGET.search(q)
        if hit and (not type_hit or len(hit[2]) >= len(type_hit[2])):
            dim, entity_id, name = hit
            steps.append({"op": "ResolveEntities", "entity_type": dim, "property": "name", "value": name})
            notes["selector_rule"] = f"gazetteer:{dim}"
        elif type_hit:
            steps.append({"op": "SelectProject"})
            notes["selector_rule"] = "gazetteer:component_type"
            notes["type_filter"] = type_hit[2]
        elif named:
            phrase = named.group(1).strip()
            steps.append({"op": "ResolveEntities", "entity_type": "component", "property": "name", "value": phrase})
            notes["selector_rule"] = "named_target"
        else:
            steps.append({"op": "SelectProject"})
            notes["selector_rule"] = "project"

    # ---------------------------------------------------------------- sources
    has_material = bool(MATERIAL_CUE.search(q))
    has_process = bool(PROCESS_CUE.search(q))
    if steps[0].get("entity_type") == "material":
        source = "material"
    elif steps[0].get("entity_type") == "process":
        source = "process"
    elif has_material and not has_process:
        source = "material"
    elif has_process and not has_material:
        source = "process"
    else:
        source = ["material", "process"]
    atoms = {"op": "CarbonAtoms", "source": source}
    if KNOWN_CUE.search(q) or source == ["material", "process"]:
        atoms["known_total"] = True
    steps.append(atoms)
    if notes.get("type_filter"):
        steps.append({"op": "Filter", "field": "component_type_name", "equals": notes["type_filter"]})
    if steps[0].get("entity_type") == "process":
        pass  # the selector already scopes the process records

    # ---------------------------------------------------------------- operation and grouping
    if COMPARE_CUE.search(q):
        operation = "compare"
    elif RANK_CUE.search(q):
        operation = "rank"
    elif TRACE_CUE.search(q):
        operation = "trace"
    elif EXPLAIN_CUE.search(q):
        operation = "explain"
    else:
        operation = "value"
    group = None
    if CARRIER_CUE.search(q):
        group = "carrier"
    elif MATERIAL_GROUP_CUE.search(q):
        group = "material"
    elif STAGE_CUE.search(q) and source == "process":
        group = "stage"
    elif COMPONENT_GROUP_CUE.search(q):
        group = "component"
    if group is None and operation in {"rank", "compare"}:
        if source == "material":
            group = "material"
        elif source == "process":
            group = "process" if RECORD_CUE.search(q) else "carrier"
        else:
            group = "component"
    if group:
        steps.append({"op": "GroupBy", "keys": [group]})
    steps.append({"op": "Aggregate", "metric": "sum_kgCO2e"})
    if operation == "rank":
        steps.append({"op": "Rank", "top_k": 5, "descending": True})
    elif operation == "compare":
        steps.append({"op": "Compare"})
    elif operation in {"trace", "explain"}:
        steps.append({"op": "Trace"})
    notes.update({"source": source, "group": group, "operation_cue": operation})
    return CarbonQLProgram.from_dict({"steps": steps}), notes


def program_slots(program) -> dict:
    sources = set()
    for step in program.steps:
        if step.op == "CarbonAtoms":
            v = step.args.get("source")
            vals = (v,) if isinstance(v, str) else tuple(v or ())
            sources.update(vals)
    selector = program.steps[0].op
    return {
        "program_perspective": map_perspective(derive_projection_perspective(program)),
        "program_raw_perspective": derive_projection_perspective(program),
        "program_operation": _operation(program),
        "program_sources": sorted(sources),
        "program_selector": selector,
        "program_scope": "selected_component" if selector in {"SelectClicked", "ResolveEntities"} else "project",
    }


HELDOUT = {
    "Type A": (ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typea1_en_v2_d_spread_20260731", DEFAULT_KG),
    "Type B": (ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typeb_aligned_massproxy_v2_20260928",
               ROOT / "outputs/research_experiments/typebd_aligned_smoke/typeb_aligned_massproxy_20260928"),
    "Type D": (ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typed_aligned_massproxy_v2_20260928",
               ROOT / "outputs/research_experiments/typebd_aligned_smoke/typed_aligned_massproxy_20260928"),
}


def run_heldout(out_dir: Path) -> dict:
    """Rule compiler on the E4c held-out sets, scored exactly like the E4e compiler variants."""
    from dm2c_carbonql_compiler_ablation import load_cases as load_heldout_cases, score_answer, summarize
    from dm2c_carbonql_service import CarbonQLService
    from dm2c_carbonql_synthesizer import SynthesisResult

    results = {}
    for label, (bench_dir, release_dir) in HELDOUT.items():
        service = CarbonQLService.from_release(release_dir, None, allow_synthetic=True)
        gaz = build_gazetteer(service.context)
        rows = []
        for case in load_heldout_cases(bench_dir):
            t0 = time.perf_counter()
            program, notes = compile_rule(case.question, case.selected_component_ids, gaz)
            try:
                validation = validate_program(program, service.executor.schema)
                status, valid = validation.compiler_status, validation.executable
            except Exception as exc:
                status, valid = f"invalid:{type(exc).__name__}", False
            latency = (time.perf_counter() - t0) * 1000
            synthesis = SynthesisResult(variant="RULE", compiler_status=status, first_raw_content="", repair_raw_content="",
                                        first_program=program, final_program=program if valid else None, first_error={},
                                        final_error={} if valid else {"code": status}, validation=validation if valid else None,
                                        initial_semantic_validation=None, final_semantic_validation=None, llm_call_count=0, latency_ms=latency)
            selected = tuple(case.selected_component_ids)
            if valid:
                t1 = time.perf_counter()
                execution = service.executor.execute(program, selected)
                answer = service._from_execution(case.question, selected, "RULE", synthesis, execution, (time.perf_counter() - t1) * 1000)
            else:
                answer = service._compilation_failed(case.question, selected, "RULE", synthesis)
            row = score_answer(case, answer)
            row["rule_notes"] = notes
            rows.append(row)
        summary = summarize(rows)
        by_cat = {}
        for cat in ("cross_view", "partial_or_ambiguous"):
            sub = [r for r in rows if r["category"] == cat]
            by_cat[cat] = {"n": len(sub), "answer_exact_match": round(sum(r["answer_exact_match"] for r in sub) / len(sub), 4) if sub else None,
                           "boundary_match": round(sum(r["boundary_match"] for r in sub) / len(sub), 4) if sub else None}
        results[label] = {"benchmark": str(bench_dir), "release": str(release_dir), "summary": summary, "by_category": by_cat}
        key = label.replace("Type ", "type").lower()
        (out_dir / f"heldout_{key}_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), encoding="utf-8")
        print(label, "held-out rule compiler:", {k: summary[k] for k in ("answer_exact_match", "boundary_match", "presentation_match", "status_match", "compiled")}, flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    ap.add_argument("--kg-dir", type=Path, default=DEFAULT_KG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--heldout-only", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.heldout_only:
        results = run_heldout(args.out)
        (args.out / "rule_compiler_heldout_summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    cases = load_benchmark_cases(args.benchmark)
    context = load_full_qa_context(args.kg_dir, allow_synthetic=True)
    schema = GraphSchema.from_context(context.canonical)
    gaz = build_gazetteer(context.canonical)
    rows, compile_fail = [], 0
    rule_counter = Counter()
    t_run = time.time()
    for index, case in enumerate(cases, 1):
        t0 = time.perf_counter()
        program, notes = compile_rule(case.question, case.selected_component_ids, gaz)
        rule_counter[notes["selector_rule"]] += 1
        try:
            validation = validate_program(program, schema)
            compiler_status = validation.compiler_status
            valid = validation.executable
        except Exception as exc:  # validator raises on malformed programs
            compiler_status, valid = f"invalid:{type(exc).__name__}", False
            notes["validation_error"] = str(exc)[:200]
        latency = (time.perf_counter() - t0) * 1000
        if not valid:
            compile_fail += 1
            observed = _scorable_observed("rule_compiler", {
                "status": "not_executed", "coverage_status": "compiler_rejected", "perspective": "", "operation": "",
                "summary": {}, "evidence_ids": [], "compiler_status": compiler_status, "compiler_error": {"message": notes.get("validation_error", compiler_status)},
            }, latency)
        else:
            result = execute_canonical_query(context, program, case.selected_component_ids)
            observed = adapt_observed(question=case.question, program=program, result=result, context=context,
                                      selected_component_ids=case.selected_component_ids,
                                      elapsed_ms=latency + 0.0, compiler_status=compiler_status)
            observed["variant"] = "rule_compiler"
        row = evaluate_benchmark_response(case, observed, "rule_compiler")
        row["case_index"] = index
        row["program"] = program.to_dict()
        row["rule_notes"] = notes
        row["compiler_status"] = compiler_status
        row.update(program_slots(program))
        rows.append(row)
    metrics = dict(summarize_results(rows))
    metrics["compile_failures"] = compile_fail
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "rule_compiler_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    summary = {
        "run_id": "rule_compiler_v16", "variant": "rule_compiler", "benchmark_path": str(args.benchmark), "kg_dir": str(args.kg_dir),
        "case_count": len(rows), "metrics": metrics, "selector_rule_counts": dict(rule_counter),
        "gazetteer_sizes": {k: len(v) for k, v in gaz.items()}, "elapsed_s": round(time.time() - t_run, 1),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Deterministic keyword and gazetteer compiler; same validator, executor, answer adapter and scorer as the LLM compiler.",
    }
    (args.out / "rule_compiler_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"metrics": metrics, "selector_rules": dict(rule_counter)}, ensure_ascii=False, indent=1))
    results = run_heldout(args.out)
    (args.out / "rule_compiler_heldout_summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
