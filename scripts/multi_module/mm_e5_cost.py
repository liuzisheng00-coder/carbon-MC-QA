# -*- coding: utf-8 -*-
"""E5: cost report — graph build time, query latency, LLM calls, token usage, API cost, memory.

Token usage is measured by re-running the V4 compiler once over the 48 held-out
questions of each module with a client that records the provider usage field.
"""
from __future__ import annotations

import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import MODULES, OUT_ROOT, ROOT, load_context, load_json, markdown_table, write_json, write_text  # noqa: E402

E5_DIR = OUT_ROOT / "E5"
HELDOUT = {
    "Type A": ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typea1_en_v2_d_spread_20260731",
    "Type B": ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typeb_aligned_massproxy_v2_20260928",
    "Type D": ROOT / "outputs/research_experiments/e4c_carbonql_heldout/e4c_reviewed_typed_aligned_massproxy_v2_20260928",
}
E3_RUNS = {
    "Type A": ROOT / "outputs/research_experiments/e4e_compiler_ablation/e4e_typea1_en_v2_d_spread_20260731_5x",
    "Type B": ROOT / "outputs/research_experiments/e4e_compiler_ablation/e4e_typeb_aligned_massproxy_v2_20260928_5x",
    "Type D": ROOT / "outputs/research_experiments/e4e_compiler_ablation/e4e_typed_aligned_massproxy_v2_20260928_5x",
}
E5_TYPEA_SUMMARY = ROOT / "outputs/research_experiments/e5_full_multi_llm_typea1_en/comparison_full150_n3.json"
# List prices used for the illustration (USD per million tokens). Verify against the provider invoice.
PRICE = {"model": "deepseek-chat", "input_usd_per_m": 0.28, "output_usd_per_m": 0.42, "price_note": "DeepSeek public list price for deepseek-chat standard (cache-miss input); adjust to the invoiced rate."}


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def rss_mb() -> float | None:
    try:
        import psutil  # type: ignore

        return psutil.Process(os.getpid()).memory_info().rss / 1e6
    except Exception:
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return pmc.WorkingSetSize / 1e6
        except Exception:
            return None


def measure_tokens(label: str, spec, benchmark_dir: Path):
    from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient
    from dm2c_carbonql_compiler_ablation import load_cases
    from dm2c_carbonql_service import CarbonQLService

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"error": "DEEPSEEK_API_KEY not set"}
    usage_log = []
    lock = threading.Lock()
    local = threading.local()

    class RecordingClient(OpenAICompatibleToolClient):
        def complete(self, messages, tools=None, tool_choice="auto", max_tokens=2000, response_format=None):
            payload = {"model": self.model, "temperature": self.temperature, "messages": messages, "max_tokens": max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            if response_format is not None:
                payload["response_format"] = response_format
            data = self._post("chat/completions", payload)
            usage = data.get("usage") or {}
            with lock:
                usage_log.append({"case_id": getattr(local, "case_id", None), "prompt_tokens": usage.get("prompt_tokens", 0),
                                  "completion_tokens": usage.get("completion_tokens", 0),
                                  "cached_tokens": (usage.get("prompt_cache_hit_tokens") or 0)})
            return data["choices"][0]["message"]

    client = RecordingClient(api_key=api_key, model=PRICE["model"], base_url="https://api.deepseek.com", temperature=0.0, timeout=180, max_retries=4)
    service = CarbonQLService.from_release(spec.reader_dir, client, allow_synthetic=True)
    cases = load_cases(benchmark_dir)

    def run(case):
        local.case_id = case.case_id
        started = time.perf_counter()
        answer = service.answer(case.question, case.selected_component_ids, variant="V4")
        return {"case_id": case.case_id, "wall_ms": (time.perf_counter() - started) * 1000,
                "llm_calls": answer.llm_call_count, "compile_ms": answer.compile_latency_ms, "execute_ms": answer.execute_latency_ms}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, cases))
    per_case_tokens = {}
    for u in usage_log:
        d = per_case_tokens.setdefault(u["case_id"], {"prompt": 0, "completion": 0, "cached": 0, "calls": 0})
        d["prompt"] += u["prompt_tokens"]
        d["completion"] += u["completion_tokens"]
        d["cached"] += u["cached_tokens"]
        d["calls"] += 1
    prompts = [d["prompt"] for d in per_case_tokens.values()]
    completions = [d["completion"] for d in per_case_tokens.values()]
    cost_per_q = [(d["prompt"] * PRICE["input_usd_per_m"] + d["completion"] * PRICE["output_usd_per_m"]) / 1e6 for d in per_case_tokens.values()]
    return {
        "questions": len(results),
        "llm_calls_total": len(usage_log),
        "llm_calls_per_question": len(usage_log) / max(len(results), 1),
        "prompt_tokens_per_question_mean": statistics.mean(prompts) if prompts else None,
        "prompt_tokens_per_question_p95": percentile(prompts, 0.95),
        "completion_tokens_per_question_mean": statistics.mean(completions) if completions else None,
        "completion_tokens_per_question_p95": percentile(completions, 0.95),
        "cached_prompt_tokens_share": (sum(d["cached"] for d in per_case_tokens.values()) / max(sum(prompts), 1)),
        "usd_per_question_mean": statistics.mean(cost_per_q) if cost_per_q else None,
        "usd_per_150_questions": 150 * statistics.mean(cost_per_q) if cost_per_q else None,
        "wall_ms_mean": statistics.mean(r["wall_ms"] for r in results),
        "wall_ms_p95": percentile([r["wall_ms"] for r in results], 0.95),
        "compile_ms_mean": statistics.mean(r["compile_ms"] for r in results),
        "execute_ms_mean": statistics.mean(r["execute_ms"] for r in results),
        "per_case": per_case_tokens,
    }


def main() -> None:
    e1 = load_json(OUT_ROOT / "E1" / "e1_graph_quality.json")
    build = {r["module"]: r["build_seconds"] for r in e1["rows"]}
    graph_mb = {r["module"]: r["graph_json_mb"] for r in e1["rows"]}

    latency = {}
    for label, run_dir in E3_RUNS.items():
        rows = []
        for line in (run_dir / "ablation_cases.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                import json
                r = json.loads(line)
                if r["variant"] == "V4":
                    rows.append(r)
        comp = [r["compile_latency_ms"] for r in rows]
        exe = [r["execute_latency_ms"] for r in rows]
        latency[label] = {"n": len(rows), "compile_mean": statistics.mean(comp), "compile_p50": percentile(comp, 0.5), "compile_p95": percentile(comp, 0.95),
                          "execute_mean": statistics.mean(exe), "execute_p95": percentile(exe, 0.95),
                          "llm_calls_mean": statistics.mean(r["llm_call_count"] for r in rows)}

    memory = {}
    for spec in MODULES:
        # fresh interpreter per module so that the RSS delta is not masked by memory reuse
        import json as _json
        import subprocess

        code = (
            "import sys, json; sys.path.insert(0, %r); sys.path.insert(0, %r);"
            "from mm_e5_cost import rss_mb; from mm_common import load_context, MODULES;"
            "spec=[m for m in MODULES if m.key==%r][0]; b=rss_mb(); ctx=load_context(spec); a=rss_mb();"
            "print(json.dumps({'before': b, 'after': a, 'emissions': len(ctx.emissions), 'contributions': len(ctx.product_contributions)}))"
        ) % (str(ROOT), str(Path(__file__).resolve().parent), spec.key)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
        payload = _json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 and proc.stdout.strip() else {}
        before, after = payload.get("before"), payload.get("after")
        memory[spec.label] = {"rss_before_mb": before, "rss_after_mb": after, "delta_mb": (after - before) if (before and after) else None,
                              "emissions": payload.get("emissions"), "contributions": payload.get("contributions")}

    tokens = {}
    cached = E5_DIR / "e5_cost_report.json"
    previous = load_json(cached).get("tokens", {}) if cached.exists() else {}
    for spec in MODULES:
        if spec.label in previous and "error" not in previous[spec.label] and os.environ.get("MM_E5_REMEASURE_TOKENS") != "1":
            tokens[spec.label] = previous[spec.label]  # reuse the measured pass unless a re-measurement is requested
            continue
        tokens[spec.label] = measure_tokens(spec.label, spec, HELDOUT[spec.label])
        print(spec.label, {k: v for k, v in tokens[spec.label].items() if k != "per_case"})

    e5a = load_json(E5_TYPEA_SUMMARY) if E5_TYPEA_SUMMARY.exists() else {}
    full_qa = e5a.get("deepseek-chat", {})

    labels = [s.label for s in MODULES]
    md = ["# E5 Cost report (build time, latency, tokens, API cost, memory)", "",
          "## Graph construction", "",
          markdown_table(["Module", "Release build wall-clock (s)", "Graph JSON (MB)", "Process RSS growth on load (MB)", "Emission facts", "Product contributions"],
                         [[l, build[l], graph_mb[l], round(memory[l]["delta_mb"], 1) if memory[l]["delta_mb"] is not None else "n/a", memory[l]["emissions"], memory[l]["contributions"]] for l in labels]),
          "", "## Held-out CarbonQL compilation latency (V4, deepseek-chat, 5 repeats × 48 questions)", "",
          markdown_table(["Module", "Compile mean (ms)", "Compile p50 (ms)", "Compile p95 (ms)", "Execute mean (ms)", "Execute p95 (ms)", "LLM calls / question"],
                         [[l, round(latency[l]["compile_mean"]), round(latency[l]["compile_p50"]), round(latency[l]["compile_p95"]), round(latency[l]["execute_mean"]), round(latency[l]["execute_p95"]), round(latency[l]["llm_calls_mean"], 2)] for l in labels]),
          "", "## Token usage and API cost (one V4 pass over the 48 held-out questions per module)", "",
          markdown_table(["Module", "LLM calls / question", "Prompt tokens / question (mean, p95)", "Completion tokens / question (mean, p95)", "Cached prompt share", "USD / question", "USD / 150 questions"],
                         [[l, round(tokens[l]["llm_calls_per_question"], 2),
                           f"{tokens[l]['prompt_tokens_per_question_mean']:.0f}, {tokens[l]['prompt_tokens_per_question_p95']:.0f}",
                           f"{tokens[l]['completion_tokens_per_question_mean']:.0f}, {tokens[l]['completion_tokens_per_question_p95']:.0f}",
                           f"{100 * tokens[l]['cached_prompt_tokens_share']:.0f}%",
                           f"{tokens[l]['usd_per_question_mean']:.4f}", f"{tokens[l]['usd_per_150_questions']:.2f}"] for l in labels if "error" not in tokens[l]]),
          "", f"Prices: {PRICE['input_usd_per_m']} USD per million input tokens, {PRICE['output_usd_per_m']} USD per million output tokens ({PRICE['price_note']}).", ""]
    if full_qa:
        md += ["## Full QA pipeline latency on the 150-question benchmark (Type A, deepseek-chat, 3 runs)", "",
               f"Mean latency {full_qa['mean_latency_ms_mean']:.0f} ms (sd {full_qa['mean_latency_ms_sd']:.0f} ms across runs); pass rate {100 * full_qa['pass_rate_mean']:.1f}% ± {100 * full_qa['pass_rate_sd']:.1f}. Source: `{E5_TYPEA_SUMMARY.relative_to(ROOT)}`.", ""]
    md += ["## Reading", "",
           "- The compiler cost does not depend on graph size: the prompt carries the operator contract, the schema and the worked examples, not the data. Compile latency and tokens are the same order for the three modules.",
           "- Execution runs on the in-memory canonical graph and takes 0.5–1.3 s per question on average; all three graphs carry the mass-share allocation of seven shared energy records spread over every component (about 1,500 product contributions each), so execution time is set by the number of contributions a question touches.",
           "- Graph size grows with the number of module types, not with the number of modules in the building; a building question compiles to the module program with the module count as a weight (see E2 building-scale note).",
           "- Concurrent users were not tested."]
    write_text(E5_DIR / "e5_cost_report.md", "\n".join(md) + "\n")
    write_json(E5_DIR / "e5_cost_report.json", {"build_seconds": build, "graph_mb": graph_mb, "latency": latency, "memory": memory, "tokens": tokens, "price": PRICE, "full_qa_typea": full_qa})
    print("written:", E5_DIR / "e5_cost_report.md")


if __name__ == "__main__":
    main()
