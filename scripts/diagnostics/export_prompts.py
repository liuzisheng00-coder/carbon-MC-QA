# -*- coding: utf-8 -*-
"""D4: export the exact prompts, decoding settings, scoring rules and benchmark card
used by the full system, the compiler ablation and the three baselines (XL14, XL15).

Everything is read from the code paths that the experiment runners import, so the
exported text is the text that was sent, not a paraphrase.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dm2c_carbonql import GraphSchema  # noqa: E402
from dm2c_carbonql_synthesizer import SIMPLE_EXAMPLES, build_repair_messages, build_synthesis_messages  # noqa: E402
from dm2c_e5_e7_experiment_runner import load_benchmark_cases  # noqa: E402
from dm2c_full_qa_experiment_runner import load_full_qa_context  # noqa: E402
from dm2c_real_baseline_runner import (  # noqa: E402
    _STRUCTURED_JSON_INSTRUCTION,
    REAL_BASELINE_VARIANTS,
    build_baseline_messages,
    build_case_context,
    load_baseline_context,
)

RE_DIR = ROOT / "outputs/research_experiments"
KG = RE_DIR / "multi_module_20260928/releases/typea_clean"
BENCH_DIR = RE_DIR / "e4_benchmark/frozen_20260803_180240_v16_typea1_en_realloc"
BENCH = BENCH_DIR / "e4_balanced_benchmark_150_human_reviewed_v16_typea1_en.jsonl"
OUT = RE_DIR / "diagnostics_20260930/D4_prompts"

SETTINGS = [
    ("Provider / model", "DeepSeek `deepseek-chat` through the OpenAI-compatible chat endpoint (`https://api.deepseek.com`)"),
    ("Temperature", "0.0 for every configuration (compiler, repair call, all baselines)"),
    ("max_tokens", "1800 for the compiler and repair calls; provider default for baselines"),
    ("Timeout / retries", "180 s per call, 4 retries with back-off (transport errors are recorded, not resampled)"),
    ("response_format", "`{\"type\": \"json_object\"}` for the structured-JSON baseline only; the compiler returns JSON by instruction and code fences are stripped"),
    ("Compiler variant", "V4 = type validator + semantic coverage check + one repair call; V1 to V3 are the ablation stages in Appendix C / E4e"),
    ("Repeats", "Full system: 3 independent runs on the 150 questions. Control-policy ablations: 3 runs (each run compiles once and executes under four policies). Baselines: 1 run each. Held-out compiler ablation: 5 repeats per variant per module. Rule compiler: deterministic, 1 run"),
    ("Graph release", "Type A English-basis canonical release (`m2_typea1_full_en_layerdedup_20260731_d_spread`, six-artifact copy `multi_module_20260928/releases/typea_clean`)"),
    ("Truth leakage guard", "`expected` fields of a benchmark row are read only by the evaluator; the compiler receives the question and the selected component ids, the executor receives the program"),
]

SCORING = [
    ("slot_match", "observed perspective and operation equal the reference (operation aliases merged)"),
    ("status_match", "observed boundary state equals the reference state (executable, incomplete_path, empty_result, unresolved_target, clarification_required)"),
    ("numeric_match", "every numeric key of the reference summary is present in the observed summary with the same value within relative tolerance 1e-4"),
    ("numeric_match_keyfree", "every reference number appears among the observed numbers regardless of key name (reported for baselines, not gating)"),
    ("answer_contains", "required phrases appear in the prose answer, when the reference lists any"),
    ("provenance_supported", "every cited evidence id is in the reference or in the executor's supported set"),
    ("unsupported_answer", "a number or evidence id in the answer has no support (non-gating flag, but blocks a pass)"),
    ("passed (strict pass)", "all gating scores are not False and unsupported_answer is False"),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = load_benchmark_cases(BENCH)
    context = load_full_qa_context(KG, allow_synthetic=True)
    schema = GraphSchema.from_context(context.canonical)
    example = next(c for c in cases if c.selected_component_ids)
    project_example = next(c for c in cases if not c.selected_component_ids)

    # -------------------------------------------------- compiler prompts
    msgs = build_synthesis_messages(example.question, example.selected_component_ids, schema, "V4")
    system_text = msgs[0]["content"]
    user_payload = json.loads(msgs[1]["content"])
    contract = user_payload["contract"]
    repair = build_repair_messages(example.question, example.selected_component_ids, None, {"code": "<error code>", "message": "<validator message>"}, schema)
    (OUT / "compiler_system_prompt.txt").write_text(system_text, encoding="utf-8")
    (OUT / "compiler_contract_v4.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "compiler_user_payload_example.json").write_text(json.dumps(user_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "repair_messages_template.json").write_text(json.dumps(repair, ensure_ascii=False, indent=2), encoding="utf-8")
    v1 = json.loads(build_synthesis_messages(example.question, example.selected_component_ids, schema, "V1")[1]["content"])["contract"]
    (OUT / "compiler_contract_v1.json").write_text(json.dumps(v1, ensure_ascii=False, indent=2), encoding="utf-8")

    # -------------------------------------------------- baseline prompts
    bctx = load_baseline_context(KG, allow_synthetic=True)
    baseline_dump = {}
    for variant in REAL_BASELINE_VARIANTS:
        bm = build_baseline_messages(bctx, project_example, variant)
        system = bm[0]["content"]
        preamble = system.split("{", 1)[0]
        snapshot = build_case_context(bctx, project_example, variant)
        sizes = {k: (len(v) if isinstance(v, list) else None) for k, v in snapshot.items()}
        baseline_dump[variant] = {
            "preamble": preamble.strip(),
            "structured_json_instruction": _STRUCTURED_JSON_INSTRUCTION if variant == "llm_structured_json_real" else None,
            "snapshot_keys": list(snapshot.keys()),
            "snapshot_table_rows": {k: v for k, v in sizes.items() if v is not None},
            "system_prompt_chars": len(system),
            "system_prompt_words": len(system.split()),
            "response_format": {"type": "json_object"} if variant == "llm_structured_json_real" else None,
            "note": snapshot.get("note"),
            "representation": snapshot.get("representation"),
        }
    (OUT / "baseline_prompts.json").write_text(json.dumps(baseline_dump, ensure_ascii=False, indent=2), encoding="utf-8")

    # -------------------------------------------------- benchmark card
    rows = [json.loads(l) for l in BENCH.read_text(encoding="utf-8").splitlines() if l.strip()]
    status = collections.Counter(r["expected"]["status"] for r in rows)
    difficulty = collections.Counter(r["difficulty"] for r in rows)
    scope = collections.Counter(r["scope"] for r in rows)
    persp = collections.Counter(r["expected"]["perspective"] for r in rows)
    op = collections.Counter(r["expected"]["operation"] for r in rows)
    cell = collections.Counter((r["expected"]["perspective"], r["expected"]["operation"]) for r in rows)
    gen_source = collections.Counter((r.get("generation") or {}).get("source") for r in rows)
    policy = collections.Counter((r.get("question_revision") or {}).get("policy") for r in rows)
    reworded = sum(1 for r in rows if (r.get("question_revision") or {}).get("original_question") not in (None, r["question"]))
    flags = collections.Counter(f for r in rows for f in (r.get("question_revision") or {}).get("issue_flags") or [])
    lineage = []
    for d in sorted((RE_DIR / "e4_benchmark").iterdir()):
        m = re.match(r"(frozen|human_revised)_(\d{8})_(\d{6})_(.*)", d.name)
        if m:
            lineage.append((m.group(2), m.group(4)))
    card = {
        "file": str(BENCH.relative_to(ROOT)), "sha256": sha256(BENCH), "questions": len(rows),
        "status": dict(status), "difficulty": dict(difficulty), "scope": dict(scope), "perspective": dict(persp), "operation": dict(op),
        "perspective_x_operation": {f"{p}/{o}": n for (p, o), n in sorted(cell.items())},
        "generation_source": dict(gen_source), "naturalisation_policy": dict(policy), "reworded_from_template": reworded,
        "issue_flags": dict(flags), "frozen_lineage": lineage,
        "mean_question_words": sum(len(r["question"].split()) for r in rows) / len(rows),
    }
    (OUT / "benchmark_card_v16.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

    # -------------------------------------------------- markdown
    md = ["# Appendix material: prompts, settings, scoring and benchmark card", ""]
    md += ["## A.1 Compiler prompt (variant V4, sent verbatim)", "", "System message:", "", "```text", system_text, "```", "",
           "User message: one JSON object with `question`, `selected_component_ids` and `contract`. The contract carries the operator type signatures, argument contracts, allowed sources, group keys, aggregate metrics, the graph schema of the loaded release (dimension names, entity counts, readable names), the three worked single-view examples and the rule list below. V1 sends only the JSON shape, hole contract, allowed operations and the three examples; V2 adds the typed contract; V3 adds the one-shot repair call; V4 adds the semantic coverage check before accepting a program.", "",
           "Rules inside the contract (V2 to V4):", ""]
    md += [f"{i}. {r}" for i, r in enumerate(contract["rules"], 1)]
    md += ["", "Worked examples (all variants):", "", "```json", json.dumps(list(SIMPLE_EXAMPLES), ensure_ascii=False, indent=2), "```", ""]
    md += ["Repair call (V3, V4), system message:", "", "```text", repair[0]["content"], "```", "",
           "The repair user message carries `question_context`, `selection_context`, `program_to_repair`, `reported_error` and the same contracts. Full templates: `compiler_contract_v4.json`, `compiler_contract_v1.json`, `compiler_user_payload_example.json`, `repair_messages_template.json`.", ""]
    md += ["## A.2 Baseline prompts", ""]
    for variant, b in baseline_dump.items():
        md += [f"### {variant}", "", f"Representation: `{b['representation']}`. System prompt length on the Type A release: {b['system_prompt_chars']:,} characters (~{b['system_prompt_words']:,} words).", "", "Preamble:", "", "```text", b["preamble"], "```", "", f"Snapshot note: {b['note']}", "", "Tables embedded in the system prompt (rows): " + ", ".join(f"{k} {v}" for k, v in b["snapshot_table_rows"].items()), ""]
        if b["structured_json_instruction"]:
            md += ["Appended instruction:", "", "```text", b["structured_json_instruction"], "```", ""]
    md += ["## A.3 Decoding and run settings", "", "| Item | Value |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in SETTINGS]
    md += ["", "## A.4 Scoring rules (`evaluate_benchmark_response`)", "", "| Score | Definition |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in SCORING]
    md += ["", "## A.5 Benchmark card (v16)", "",
           f"File `{card['file']}`, SHA-256 `{card['sha256'][:16]}…`, {card['questions']} questions, mean length {card['mean_question_words']:.1f} words.", "",
           "| Facet | Distribution |", "|---|---|",
           f"| Boundary state | {', '.join(f'{k} {v}' for k, v in status.most_common())} |",
           f"| Difficulty | {', '.join(f'{k} {v}' for k, v in difficulty.most_common())} |",
           f"| Scope | {', '.join(f'{k} {v}' for k, v in scope.most_common())} |",
           f"| Perspective | {', '.join(f'{k} {v}' for k, v in persp.most_common())} |",
           f"| Operation | {', '.join(f'{k} {v}' for k, v in op.most_common())} |",
           f"| Generation | {', '.join(f'{k} {v}' for k, v in gen_source.items())}; naturalisation policy {', '.join(f'{k} {v}' for k, v in policy.items())}; {reworded} of {len(rows)} questions carry a reworded surface form distinct from the template question |",
           f"| Review flags kept on rows | {', '.join(f'{k} {v}' for k, v in flags.most_common())} |",
           "", "Construction. Reference answers are computed deterministically from the frozen graph release for each perspective × operation × boundary-state cell, so every reference number is reproducible from the release and independent of any model. Question wording was drafted from templates, naturalised with the same language model at temperature 0, then revised by the authors in review rounds; the reviewed wording of V7 was reused when the deterministic truth was migrated to later graph releases.", "",
           "Frozen versions (date, tag): " + "; ".join(f"{d[:4]}-{d[4:6]}-{d[6:]} {tag}" for d, tag in lineage), "",
           "Development use. The 150-question set was used while the compiler contract and its rules were developed, so it is a development set. The held-out sets (48 new questions per module, written after the compiler was frozen, Appendix C) are the clean test; no prompt or rule was changed after they were written."]
    (OUT / "appendix_A_prompts_and_settings.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("written", OUT)
    print(json.dumps({k: {"chars": v["system_prompt_chars"], "rows": v["snapshot_table_rows"]} for k, v in baseline_dump.items()}, indent=1))
    print("benchmark sha256", card["sha256"], "reworded", reworded, "flags", dict(flags))


if __name__ == "__main__":
    main()
