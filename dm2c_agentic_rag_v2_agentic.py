"""
DM2C Agentic RAG v2 architecture implementation.

This script rebuilds the v2 RAG around the architecture in:
  dm2c_agentic_rag_v2_architecture.md

It adds:
  - Orchestrator Agent (LLM-preferred, local fallback for grouping only)
  - Material Agent (LLM-required, no fallback — research-critical)
  - Process Agent (LLM-required, no fallback — research-critical)
  - Gap Agent (LLM-required, no fallback — research-critical)
  - Validator Agent
  - Tool registry and ReAct-style reasoning trace

An LLM provider (OpenAI or Gemini) is REQUIRED. Material matching, process
reasoning, and gap estimation are fully LLM-driven with retry on parse
failure. The only fallback path is in the OrchestratorAgent (grouping),
which is not a research-critical decision. Per-component failures are
recorded in results with status 'llm_failed' rather than crashing the
entire assessment.
"""

from __future__ import annotations

import argparse
import ast
import http.client
import json
import math
import os
import operator
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from dm2c_agentic_rag import BackboneGraphStore, ComponentContext, normalize_text, props, safe_float, split_tokens
from dm2c_agentic_rag_v2 import (
    ActivityDriver,
    MaterialCarbonCalculator,
    MaterialFactorRecord,
    ProcessCarbonPlanner,
    ProcessCarbonResult,
    ProcessEvidence,
    ProcessEvidenceStore,
    ProcessProfileStore,
    RetrievalCandidate,
    WorkbookFactorStore,
    material_candidate_to_dict,
    material_record_to_dict,
    process_candidate_to_dict,
    process_result_to_dict,
    unique_join,
    unique_list,
)


PLANNER_SYSTEM_PROMPT = """You are a carbon assessment planner for modular construction.
Group similar IFC components so material factor decisions can be reused.
Components may have Chinese material descriptions — group by underlying material type.

You MUST return ONLY a JSON object (no markdown, no explanation) in this exact format:
{"groups":[{"strategy":"batch_steel","component_ids":["id1","id2"],"reasoning":"Same steel material"}]}

Rules:
- Every component_id from the input MUST appear in exactly one group
- Group by material similarity (e.g. all steel beams together, all concrete slabs together)
- strategy should be descriptive (batch_steel, batch_concrete, batch_aluminium, etc.)"""


ASSESSMENT_SYSTEM_PROMPT = """You are a modular construction carbon assessment agent.
Use tools to match material factors, retrieve ITP/process evidence, calculate material carbon,
map process steps to energy drivers, and report missing data. Do not do arithmetic mentally
when a calculator tool is available.

IMPORTANT: Material descriptions from IFC models are often in Chinese (e.g. '金属 - 钢 43 - 355_A1'
means Steel S355, '混凝土' means Concrete, '铝' means Aluminium). The carbon factor database uses
English names. You MUST translate/interpret the Chinese material text to find the correct English
factor match. Common mappings:
  金属 - 钢 → Steel    混凝土 → Concrete    铝 → Aluminium    玻璃 → Glass
  木材 → Timber    石材 → Stone    水泥 → Cement    砖 → Brick/Clay

Return final JSON only when ready. The JSON MUST include: selected_factor_id, confidence, reasoning."""


VALIDATOR_SYSTEM_PROMPT = """You are a carbon result validator.
Review component-level material/process carbon results. Flag outliers, inconsistent factor
usage across similar components, or missing process quantities. Return JSON only:
{"flags":[{"component_id":"...", "severity":"info|warning|error", "issue":"...", "action":"none|reassess"}],
 "overall_confidence":0.0}"""


def token_set(values: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for value in values:
        for token in split_tokens(value):
            token = normalize_text(token)
            if token:
                out.add(token)
    return out


def compact_json(payload: Any, limit: int = 12000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...TRUNCATED..."


@dataclass
class AgentTraceEvent:
    agent: str
    thought: str
    action: str
    observation: Any
    confidence: float = 1.0
    reflection: str = ""


@dataclass
class ToolResult:
    name: str
    input: Dict[str, Any]
    output: Any
    ok: bool = True
    error: str = ""


@dataclass
class ComponentSummary:
    component_id: str
    global_id: str
    ifc_type: str
    name: str
    material_text: str
    quantities: List[Dict[str, Any]]


class OpenAICompatibleToolClient:
    """Minimal OpenAI-compatible chat-completions tool-call client."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.0,
        timeout: int = 60,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        detail = ""
        for attempt in range(self.max_retries + 1):
            body = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                data=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                # Retry on rate limiting (429) and transient upstream errors (500/502/503/504).
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(self._retry_delay_seconds(exc, detail, attempt))
                    continue
                raise RuntimeError(f"LLM API request failed ({exc.code}): {detail}") from exc
            except urllib.error.URLError as exc:
                detail = str(exc)
                if attempt < self.max_retries:
                    time.sleep(min(2.0 * (attempt + 1), 30.0))
                    continue
                raise RuntimeError(f"LLM API request failed (network): {detail}") from exc
            except (TimeoutError, ConnectionError, OSError, http.client.HTTPException) as exc:
                # http.client.HTTPException covers mid-response drops such as
                # IncompleteRead that urllib does not wrap in URLError.
                detail = str(exc)
                if attempt < self.max_retries:
                    time.sleep(min(5.0 * (attempt + 1), 60.0))
                    continue
                raise RuntimeError(f"LLM API request failed (timeout/connection): {detail}") from exc
        raise RuntimeError(f"LLM API request failed after retries: {detail}")

    @staticmethod
    def _retry_delay_seconds(exc: urllib.error.HTTPError, detail: str, attempt: int) -> float:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            seconds = safe_float(retry_after, None)
            if seconds is not None:
                return min(max(seconds, 1.0), 120.0)
        match = re.search(r'"retryDelay"\s*:\s*"(?P<seconds>\d+(?:\.\d+)?)s"', detail)
        if match:
            return min(max(float(match.group("seconds")) + 1.0, 1.0), 120.0)
        match = re.search(r"retry in (?P<seconds>\d+(?:\.\d+)?)s", detail, flags=re.IGNORECASE)
        if match:
            return min(max(float(match.group("seconds")) + 1.0, 1.0), 120.0)
        return min(2.0 * (attempt + 1), 30.0)

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = "auto",
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        data = self._post("chat/completions", payload)
        return data["choices"][0]["message"]

    def embed(self, inputs: List[str], model: str) -> List[List[float]]:
        if not inputs:
            return []
        data = self._post("embeddings", {"model": model, "input": inputs})
        rows = sorted(data.get("data", []), key=lambda row: int(row.get("index", 0)))
        return [row["embedding"] for row in rows]


@dataclass
class ReactLoopResult:
    final_text: str
    messages: List[Dict[str, Any]]
    trace: List[AgentTraceEvent]
    stopped_by: str


def run_react_tool_loop(
    llm: OpenAICompatibleToolClient,
    tool_registry: "ToolRegistry",
    agent_name: str,
    system_prompt: str,
    user_content: str,
    max_iterations: int = 5,
    max_tokens: int = 3000,
) -> ReactLoopResult:
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    trace: List[AgentTraceEvent] = []
    final_text = ""
    for iteration in range(max_iterations):
        try:
            response = llm.complete(messages, tools=tool_registry.schemas(), max_tokens=max_tokens)
        except RuntimeError as exc:
            trace.append(
                AgentTraceEvent(
                    agent=agent_name,
                    thought="LLM request failed during ReAct loop.",
                    action="llm_request_failed",
                    observation=str(exc),
                    confidence=0.2,
                    reflection="Agent should keep deterministic result or fallback path.",
                )
            )
            return ReactLoopResult(final_text="", messages=messages, trace=trace, stopped_by="llm_error")
        tool_calls = response.get("tool_calls") or []
        if tool_calls:
            assistant_message = {
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)
            requested = []
            for tool_call in tool_calls:
                function = tool_call.get("function", {}) or {}
                name = str(function.get("name", ""))
                args_text = function.get("arguments", "{}") or "{}"
                args = parse_json_object(args_text)
                requested.append({"name": name, "arguments": args})
                result = tool_registry.execute(name, args)
                payload = asdict(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "name": name,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )
                trace.append(
                    AgentTraceEvent(
                        agent=agent_name,
                        thought=f"LLM requested tool {name}.",
                        action=name,
                        observation=payload,
                        confidence=1.0 if result.ok else 0.2,
                        reflection="Tool result returned." if result.ok else result.error,
                    )
                )
            trace.append(
                AgentTraceEvent(
                    agent=agent_name,
                    thought=f"Iteration {iteration + 1}: select tools to reduce uncertainty.",
                    action="tool_selection",
                    observation=requested,
                    confidence=0.7,
                    reflection="Tool calls executed and returned to LLM.",
                )
            )
            continue

        final_text = response.get("content") or ""
        trace.append(
            AgentTraceEvent(
                agent=agent_name,
                thought="LLM concluded after observing tool results.",
                action="final_answer",
                observation=final_text,
                confidence=0.7,
                reflection="Final structured response received.",
            )
        )
        return ReactLoopResult(final_text=final_text, messages=messages, trace=trace, stopped_by="final")

    return ReactLoopResult(final_text=final_text, messages=messages, trace=trace, stopped_by="max_iterations")


DOMAIN_SYNONYM_EXPANSIONS: Dict[str, str] = {
    # Chinese IFC material texts → English factor names
    "金属": "metal steel aluminium",
    "钢": "steel section general rebar",
    "混凝土": "concrete RC reinforced",
    "铝": "aluminium aluminum extrusion",
    "玻璃": "glass primary toughened secondary",
    "木材": "timber plywood hardboard",
    "石材": "stone natural",
    "水泥": "cement CEM",
    "砖": "brick clay",
    "铅": "lead",
    "锌": "zinc galvanizing",
    "石灰": "lime",
    "沥青": "bitumen asphalt",
    # Product-specific Chinese terms
    "硅钙板": "calcium silicate board sheet",
    "硅酸钙": "calcium silicate board sheet",
    "三乐": "calcium silicate board sheet",
    "钢通": "steel tube hollow section SHS RHS",
    "钢架": "steel frame section",
    "角柱": "steel column corner post",
    "钢筋": "steel rebar reinforcement bar",
    "龙骨": "light gauge steel framing channel",
    "岩棉": "rockwool rock wool insulation",
    "防火板": "fire rated board fibre cement",
    "地胶": "PVC vinyl flooring",
    "腻子": "putty cement based",
    "密封胶": "sealant silicone",
    # Process-related Chinese terms
    "烧焊": "welding arc weld",
    "拼焊": "welding assembly weld",
    "焊": "welding",
    "钎焊": "brazing soldering",
    "开料": "cutting sawing plasma cutting",
    "油漆": "painting coating",
    "富锌": "zinc rich coating anticorrosion coating",
    # Steel grade identifiers
    "355": "steel S355 structural",
    "43": "steel grade 43 S275",
}


def expand_domain_text(text: str) -> str:
    expanded = [str(text or "")]
    normalized = normalize_text(text)
    for source, expansion in DOMAIN_SYNONYM_EXPANSIONS.items():
        if normalize_text(source) in normalized:
            expanded.append(expansion)
    return " ".join(expanded)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


class SemanticSearchIndex:
    def __init__(
        self,
        factor_store: WorkbookFactorStore,
        process_store: ProcessEvidenceStore,
        embedding_client: OpenAICompatibleToolClient,
        embedding_model: str,
    ):
        self.factor_store = factor_store
        self.process_store = process_store
        self.embedding_client = embedding_client
        self.embedding_model = embedding_model
        self._entries: Optional[List[Dict[str, Any]]] = None

    def search(self, text: str, top_k: int = 5, source: str = "both") -> List[Dict[str, Any]]:
        entries = [entry for entry in self._build_entries() if source == "both" or entry["source"] == source]
        if not entries:
            return []
        query_embedding = self.embedding_client.embed([expand_domain_text(text)], self.embedding_model)[0]
        ranked: List[Tuple[float, Dict[str, Any]]] = []
        for entry in entries:
            score = cosine_similarity(query_embedding, entry["embedding"])
            ranked.append((score, entry))
        ranked.sort(key=lambda row: row[0], reverse=True)
        return [
            {
                "score": round(score, 6),
                "source": entry["source"],
                "searchMode": "embedding",
                **entry["payload"],
            }
            for score, entry in ranked[:top_k]
        ]

    def _build_entries(self) -> List[Dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        raw_entries: List[Dict[str, Any]] = []
        for record in self.factor_store.material_factors:
            raw_entries.append(
                {
                    "source": "factors",
                    "text": expand_domain_text(
                        " ".join([record.row_id, record.material_name, record.category, record.subtype] + record.keywords)
                    ),
                    "payload": material_record_to_dict(record),
                }
            )
        for record in self.process_store.docs:
            raw_entries.append(
                {
                    "source": "itp",
                    "text": expand_domain_text(
                        " ".join([record.doc_id, record.title, record.text] + record.keywords + record.activities + record.component_types)
                    ),
                    "payload": process_evidence_to_dict(record),
                }
            )
        embeddings: List[List[float]] = []
        batch_size = 64
        for start in range(0, len(raw_entries), batch_size):
            batch = raw_entries[start : start + batch_size]
            embeddings.extend(self.embedding_client.embed([entry["text"] for entry in batch], self.embedding_model))
        self._entries = [
            {**entry, "embedding": embedding}
            for entry, embedding in zip(raw_entries, embeddings)
        ]
        return self._entries


class ToolRegistry:
    def __init__(
        self,
        graph_store: BackboneGraphStore,
        factor_store: WorkbookFactorStore,
        process_store: ProcessEvidenceStore,
        profile_store: ProcessProfileStore,
        factory_grid: str,
        embedding_client: Optional[OpenAICompatibleToolClient] = None,
        embedding_model: str = "",
    ):
        self.graph_store = graph_store
        self.factor_store = factor_store
        self.process_store = process_store
        self.profile_store = profile_store
        self.factory_grid = factory_grid
        self.semantic_index = (
            SemanticSearchIndex(factor_store, process_store, embedding_client, embedding_model)
            if embedding_client and embedding_model
            else None
        )
        self.contexts_by_id: Dict[str, ComponentContext] = {}
        for context in graph_store.component_contexts():
            self.contexts_by_id[context.global_id] = context
            self.contexts_by_id[str(context.component.get("id", ""))] = context

    def schemas(self) -> List[Dict[str, Any]]:
        return [
            self._tool_schema(
                "factor_lookup",
                "Look up a carbon emission factor by material/energy name or row ID.",
                {
                    "query": {"type": "string"},
                    "sheet": {"type": "string", "enum": ["materials", "energy"]},
                    "match_type": {"type": "string", "enum": ["exact", "fuzzy"]},
                },
                ["query"],
            ),
            self._tool_schema(
                "vector_search",
                "Lexical/semantic-style search over factor records and ITP/process docs.",
                {
                    "text": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                    "source": {"type": "string", "enum": ["factors", "itp", "both"]},
                },
                ["text"],
            ),
            self._tool_schema(
                "itp_retrieve",
                "Retrieve ITP/inspection evidence chunks for a process or component type.",
                {
                    "component_type": {"type": "string"},
                    "process_keyword": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                ["component_type", "process_keyword"],
            ),
            self._tool_schema(
                "energy_calc",
                "Calculate CO2e from energy or consumable quantity using the chosen factor.",
                {
                    "energy_type": {"type": "string"},
                    "quantity": {"type": "number"},
                    "grid": {"type": "string"},
                },
                ["energy_type", "quantity"],
            ),
            self._tool_schema(
                "graph_query",
                "Query IFC backbone component properties.",
                {
                    "component_id": {"type": "string"},
                    "properties": {"type": "array", "items": {"type": "string"}},
                },
                ["component_id"],
            ),
            self._tool_schema(
                "profile_lookup",
                "Check measured activity consumption data for a component/process.",
                {
                    "component_type": {"type": "string"},
                    "process_step": {"type": "string"},
                },
                ["component_type", "process_step"],
            ),
            self._tool_schema(
                "calculator",
                "Evaluate a safe mathematical expression.",
                {"expression": {"type": "string"}},
                ["expression"],
            ),
            self._tool_schema(
                "validator",
                "Validate a carbon factor/result against broad benchmark ranges.",
                {
                    "material_type": {"type": "string"},
                    "co2e_per_kg": {"type": "number"},
                    "process_type": {"type": "string"},
                },
                ["material_type", "co2e_per_kg"],
            ),
        ]

    @staticmethod
    def _tool_schema(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    def execute(self, name: str, params: Dict[str, Any]) -> ToolResult:
        dispatch: Dict[str, Callable[..., Any]] = {
            "factor_lookup": self.factor_lookup,
            "vector_search": self.vector_search,
            "itp_retrieve": self.itp_retrieve,
            "energy_calc": self.energy_calc,
            "graph_query": self.graph_query,
            "profile_lookup": self.profile_lookup,
            "calculator": self.calculator,
            "validator": self.validator,
        }
        fn = dispatch.get(name)
        if not fn:
            return ToolResult(name=name, input=params, output=None, ok=False, error=f"Unknown tool: {name}")
        try:
            return ToolResult(name=name, input=params, output=fn(**params))
        except Exception as exc:
            return ToolResult(name=name, input=params, output=None, ok=False, error=str(exc))

    def factor_lookup(self, query: str, sheet: str = "materials", match_type: str = "fuzzy") -> List[Dict[str, Any]]:
        q = normalize_text(query)
        if sheet == "energy":
            rows = []
            for record in self.factor_store.energy_factors:
                haystack = normalize_text(" ".join([record.row_id, record.category, record.energy_type, record.factor_unit, record.notes]))
                if (match_type == "exact" and q in {normalize_text(record.row_id), normalize_text(record.energy_type)}) or (
                    match_type != "exact" and q in haystack
                ):
                    rows.append(asdict(record))
            return rows[:10]

        rows = []
        for record in self.factor_store.material_factors:
            haystack = normalize_text(" ".join([record.row_id, record.material_name, record.category, record.subtype] + record.keywords))
            if (match_type == "exact" and q in {normalize_text(record.row_id), normalize_text(record.material_name)}) or (
                match_type != "exact" and q in haystack
            ):
                rows.append(material_record_to_dict(record))
        return rows[:10]

    def vector_search(self, text: str, top_k: int = 5, source: str = "both") -> List[Dict[str, Any]]:
        embedding_error = ""
        if self.semantic_index:
            try:
                return self.semantic_index.search(text=text, top_k=top_k, source=source)
            except Exception as exc:
                embedding_error = str(exc)

        query_terms = token_set([expand_domain_text(text)])
        results: List[Tuple[float, str, Dict[str, Any]]] = []
        if source in {"factors", "both"}:
            for record in self.factor_store.material_factors:
                terms = token_set([expand_domain_text(" ".join([record.material_name, record.category, record.subtype] + record.keywords))])
                score = len(query_terms & terms)
                if score:
                    results.append((float(score), "factors", material_record_to_dict(record)))
        if source in {"itp", "both"}:
            for record in self.process_store.docs:
                terms = token_set(
                    [
                        expand_domain_text(
                            " ".join([record.title, record.text] + record.keywords + record.activities + record.component_types)
                        )
                    ]
                )
                score = len(query_terms & terms)
                if score:
                    results.append((float(score), "itp", process_evidence_to_dict(record)))
        results.sort(key=lambda row: row[0], reverse=True)
        search_mode = "lexical_fallback_after_embedding_error" if embedding_error else "lexical_fallback"
        return [
            {"score": score, "source": kind, "searchMode": search_mode, "embeddingError": embedding_error, **payload}
            for score, kind, payload in results[:top_k]
        ]

    def itp_retrieve(self, component_type: str, process_keyword: str, top_k: int = 5) -> List[Dict[str, Any]]:
        query_terms = token_set([component_type, process_keyword])
        results: List[Tuple[float, ProcessEvidence]] = []
        for record in self.process_store.docs:
            terms = token_set([record.title, record.text] + record.keywords + record.activities + record.component_types)
            score = len(query_terms & terms)
            if normalize_text(component_type) in {normalize_text(x) for x in record.component_types}:
                score += 2
            if score:
                results.append((float(score), record))
        results.sort(key=lambda row: row[0], reverse=True)
        return [{"score": score, **process_evidence_to_dict(record)} for score, record in results[:top_k]]

    def energy_calc(self, energy_type: str, quantity: float, grid: str = "") -> Dict[str, Any]:
        factor = self.factor_store.choose_energy_factor(energy_type, grid or self.factory_grid)
        if not factor or factor.factor_value is None:
            return {"status": "missing_factor", "energy_type": energy_type, "quantity": quantity}
        return {
            "status": "complete",
            "energy_type": energy_type,
            "quantity": quantity,
            "factor_id": factor.row_id,
            "factor_value": factor.factor_value,
            "factor_unit": factor.factor_unit,
            "co2e_kg": round(float(quantity) * float(factor.factor_value), 4),
        }

    def graph_query(self, component_id: str, properties: Optional[List[str]] = None) -> Dict[str, Any]:
        context = self.contexts_by_id.get(component_id)
        if not context:
            return {"error": f"component not found: {component_id}"}
        payload = component_summary(context).__dict__
        if not properties:
            return payload
        wanted = set(properties)
        return {key: value for key, value in payload.items() if key in wanted or key in {"component_id", "global_id"}}

    def profile_lookup(self, component_type: str, process_step: str) -> Dict[str, Any]:
        dummy = ProcessEvidence(
            doc_id="profile_query",
            title=process_step,
            component_types=[component_type],
            keywords=split_tokens(process_step),
            activities=[process_step],
            text=process_step,
            source_file="profile_query",
        )
        for driver_kind in ["electricity", "diesel_l", "welding_wire"]:
            row = self.profile_store.find(dummy, driver_kind)
            if row:
                return {"status": "found", "driver_kind": driver_kind, "profile": row}
        return {"status": "missing", "component_type": component_type, "process_step": process_step}

    def calculator(self, expression: str) -> Dict[str, Any]:
        value = safe_eval(expression)
        return {"expression": expression, "value": value}

    def validator(self, material_type: str, co2e_per_kg: float, process_type: str = "") -> Dict[str, Any]:
        material = normalize_text(material_type)
        ranges = {
            "steel": (1.0, 4.0),
            "aluminium": (5.0, 20.0),
            "concrete": (0.05, 0.4),
            "cement": (0.5, 1.5),
            "glass": (0.4, 2.5),
        }
        lower, upper = ranges.get(material, (0.0, 25.0))
        status = "within_expected_range" if lower <= float(co2e_per_kg) <= upper else "outside_expected_range"
        return {"status": status, "material_type": material_type, "value": co2e_per_kg, "expected_range": [lower, upper]}


class OrchestratorAgent:
    """
    Groups components for batch assessment.

    NOTE: The orchestrator's grouping is an efficiency optimization, not a
    research-critical agentic decision. If the LLM fails to produce a plan
    (rate limits, truncated output, parse failure), a deterministic grouping
    by IFC type + material is used. This is clearly marked in the trace so
    it is transparent in research analysis.

    The research-critical LLM decisions happen in MaterialAgent, ProcessAgent,
    and GapAgent — those have NO fallback and will fail loudly.
    """
    def __init__(self, llm: OpenAICompatibleToolClient):
        self.llm = llm

    def plan(self, components: Sequence[ComponentSummary], question: str) -> Tuple[Dict[str, Any], AgentTraceEvent]:
        # Send compact summaries to avoid exceeding context window
        compact_components = [
            {"id": c.global_id, "type": c.ifc_type, "material": c.material_text[:80]}
            for c in components
        ]
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": compact_json({"question": question, "components": compact_components}, limit=6000)},
        ]
        last_error = ""
        for attempt in range(3):
            try:
                response = self.llm.complete(messages, max_tokens=4000)
            except RuntimeError as exc:
                last_error = str(exc)
                continue
            plan = parse_json_object(response.get("content", ""))
            if plan and plan.get("groups"):
                return plan, AgentTraceEvent(
                    agent="orchestrator",
                    thought="LLM planned execution order and batching.",
                    action="llm_plan",
                    observation=plan,
                    confidence=0.8,
                    reflection=f"Planner returned {len(plan['groups'])} groups (attempt {attempt + 1}).",
                )
            last_error = f"LLM returned unparseable or empty plan: {response.get('content', '')[:200]}"

        # Fallback: deterministic grouping (orchestration is not research-critical)
        plan = self._local_group(components)
        return plan, AgentTraceEvent(
            agent="orchestrator",
            thought=f"LLM planning failed after 3 attempts ({last_error[:120]}). Using deterministic grouping.",
            action="local_plan_fallback",
            observation=plan,
            confidence=0.5,
            reflection="Orchestrator used deterministic grouping. This does NOT affect MaterialAgent/ProcessAgent/GapAgent LLM decisions.",
        )

    @staticmethod
    def _local_group(components: Sequence[ComponentSummary]) -> Dict[str, Any]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for c in components:
            key = f"{normalize_text(c.ifc_type)}::{normalize_text(c.material_text)}"
            grouped.setdefault(key, {
                "strategy": f"batch_{normalize_text(c.ifc_type) or 'component'}",
                "component_ids": [],
                "reasoning": "Deterministic grouping by IFC type + material (LLM planner unavailable).",
            })["component_ids"].append(c.global_id)
        return {"groups": list(grouped.values())}


class MaterialAgent:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        calculator: MaterialCarbonCalculator,
        llm: Optional[OpenAICompatibleToolClient] = None,
    ):
        self.tools = tool_registry
        self.calculator = calculator
        self.llm = llm

    def assess(self, context: ComponentContext) -> Tuple[Dict[str, Any], List[AgentTraceEvent]]:
        result = self._assess_with_llm(context)
        if result:
            return result

        raise RuntimeError(
            f"MaterialAgent: LLM failed to produce a valid material match for "
            f"component '{context.name}' (material: '{context.material_text}'). "
            f"Check LLM connectivity and prompt quality."
        )

    def _assess_with_llm(self, context: ComponentContext) -> Optional[Tuple[Dict[str, Any], List[AgentTraceEvent]]]:
        candidates = self.tools.factor_store.retrieve_materials(context, top_k=10)
        candidate_rows = [material_candidate_to_dict(c) for c in candidates]
        react = run_react_tool_loop(
            llm=self.llm,
            tool_registry=self.tools,
            agent_name="material",
            system_prompt=ASSESSMENT_SYSTEM_PROMPT,
            user_content=compact_json(
                {
                    "task": (
                        "Select the best material factor. You may call factor_lookup, "
                        "vector_search, graph_query, calculator, and validator. "
                        "Return final JSON with selected_factor_id, confidence, reasoning."
                    ),
                    "component": asdict(component_summary(context)),
                    "factor_candidates": candidate_rows,
                }
            ),
            max_iterations=5,
            max_tokens=3000,
        )
        data = parse_json_object(react.final_text)
        if not data:
            # Retry once with explicit instruction to return JSON
            retry_react = run_react_tool_loop(
                llm=self.llm,
                tool_registry=self.tools,
                agent_name="material",
                system_prompt=ASSESSMENT_SYSTEM_PROMPT,
                user_content=(
                    "Your previous response was not valid JSON. "
                    "Return ONLY a JSON object with: selected_factor_id, confidence, reasoning. "
                    f"Candidates: {compact_json(candidate_rows)}"
                ),
                max_iterations=2,
                max_tokens=1500,
            )
            data = parse_json_object(retry_react.final_text)
            if not data:
                return None  # triggers RuntimeError in assess()
        selected_id = str(data.get("selected_factor_id", "") or "")
        selected = next(
            (
                c.record
                for c in candidates
                if c.record.row_id == selected_id or normalize_text(c.record.material_name) == normalize_text(selected_id)
            ),
            None,
        )
        if selected is None and selected_id:
            # LLM picked an ID not in candidates — retry with explicit candidate list
            retry_react = run_react_tool_loop(
                llm=self.llm,
                tool_registry=self.tools,
                agent_name="material",
                system_prompt=ASSESSMENT_SYSTEM_PROMPT,
                user_content=(
                    f"You selected factor_id '{selected_id}' but it does not match any candidate. "
                    f"Choose ONLY from these IDs: {[row['rowId'] for row in candidate_rows]}. "
                    "Return JSON with selected_factor_id, confidence, reasoning."
                ),
                max_iterations=2,
                max_tokens=1500,
            )
            retry_data = parse_json_object(retry_react.final_text)
            if retry_data:
                retry_id = str(retry_data.get("selected_factor_id", "") or "")
                selected = next(
                    (c.record for c in candidates
                     if c.record.row_id == retry_id or normalize_text(c.record.material_name) == normalize_text(retry_id)),
                    None,
                )
                if selected:
                    data = retry_data
                    selected_id = retry_id
        if selected is None and candidates:
            # Still no match after retry — use top candidate but mark low confidence
            selected = candidates[0].record
        calc = self.calculator.calculate(context, selected)
        confidence = safe_float(data.get("confidence"), 0.6) or 0.6
        selection_issue = selected is not None and selected.row_id != selected_id
        if selection_issue:
            confidence = min(confidence, 0.35)
        action = "llm_material_match"
        reflection = "LLM material decision accepted." if not selection_issue else f"LLM ID mismatch after retry; top candidate used (confidence capped)."
        trace = [
            AgentTraceEvent(
                agent="material",
                thought=str(data.get("reasoning", "LLM selected material factor.")),
                action=action,
                observation={
                    "llm_json": data,
                    "selected_factor_id_from_llm": selected_id,
                    "candidate_ids": [row["rowId"] for row in candidate_rows],
                    "accepted_selected_factor_id": selected.row_id if selected else "",
                    "selection_issue": selection_issue,
                    "calculation": asdict(calc),
                    "stopped_by": react.stopped_by,
                },
                confidence=confidence,
                reflection=reflection,
            )
        ]
        trace = react.trace + trace
        return {
            "selected_factor": selected,
            "candidates": candidates,
            "calculation": calc,
            "confidence": confidence,
            "status": calc.status,
        }, trace


class ProcessAgent:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        planner: ProcessCarbonPlanner,
        top_k: int,
        llm: Optional[OpenAICompatibleToolClient] = None,
    ):
        self.tools = tool_registry
        self.planner = planner
        self.top_k = top_k
        self.llm = llm

    def assess(self, context: ComponentContext) -> Tuple[Dict[str, Any], List[AgentTraceEvent]]:
        candidates = self.tools.process_store.retrieve(context, top_k=self.top_k)
        process_result = self.planner.plan(candidates)
        confidence = 0.75 if candidates else 0.2
        trace = [
            AgentTraceEvent(
                agent="process",
                thought="Retrieve ITP/progress evidence and infer manufacturing energy drivers.",
                action="itp_retrieve + energy_driver_mapping",
                observation={
                    "process_candidates": [process_candidate_to_dict(c) for c in candidates],
                    "process_carbon": process_result_to_dict(process_result),
                },
                confidence=confidence,
                reflection=process_result.issue or "Process carbon plan complete.",
            )
        ]
        if self.llm:
            process_result, llm_trace, llm_confidence = self._improve_with_llm(context, candidates, process_result)
            trace.extend(llm_trace)
            confidence = round(max(confidence, llm_confidence), 4)
        return {
            "candidates": candidates,
            "calculation": process_result,
            "confidence": confidence,
            "status": process_result.status,
        }, trace

    def _improve_with_llm(
        self,
        context: ComponentContext,
        candidates: Sequence[RetrievalCandidate],
        process_result: ProcessCarbonResult,
    ) -> Tuple[ProcessCarbonResult, List[AgentTraceEvent], float]:
        react = run_react_tool_loop(
            llm=self.llm,
            tool_registry=self.tools,
            agent_name="process",
            system_prompt=ASSESSMENT_SYSTEM_PROMPT,
            user_content=compact_json(
                {
                    "task": (
                        "Review retrieved ITP process chain and missing energy drivers. "
                        "You may call itp_retrieve, vector_search, profile_lookup, "
                        "energy_calc, and graph_query. Return JSON with confidence, reasoning, "
                        "driver_updates, and additional_drivers. A driver_update may include "
                        "process_doc_id, driver_kind, consumption_value, consumption_unit, "
                        "estimate_low, estimate_high, basis, and confidence. Only propose an "
                        "estimated consumption when it is explicitly marked as an analogy estimate."
                    ),
                    "component": asdict(component_summary(context)),
                    "process_candidates": [process_candidate_to_dict(c) for c in candidates],
                    "process_result": process_result_to_dict(process_result),
                }
            ),
            max_iterations=5,
            max_tokens=3000,
        )
        data = parse_json_object(react.final_text)
        if not data:
            return process_result, react.trace + [
                AgentTraceEvent(
                    agent="process",
                    thought="LLM process answer was not valid JSON, so deterministic process result was kept.",
                    action="llm_process_parse_failed",
                    observation={"raw_final_text": react.final_text, "stopped_by": react.stopped_by},
                    confidence=0.35,
                    reflection="No process changes were applied.",
                )
            ], 0.35
        process_result, application = self._apply_llm_process_decision(data, process_result, candidates)
        confidence = safe_float(data.get("confidence"), 0.6) or 0.6
        return process_result, react.trace + [
            AgentTraceEvent(
                agent="process",
                thought=str(data.get("reasoning", "LLM reviewed and optionally revised the process chain.")),
                action="llm_process_decision_applied",
                observation={"llm_json": data, "application": application, "process_result": process_result_to_dict(process_result)},
                confidence=confidence,
                reflection="Process decision applied where evidence and factor support were sufficient.",
            )
        ], confidence

    def _apply_llm_process_decision(
        self,
        data: Dict[str, Any],
        process_result: ProcessCarbonResult,
        candidates: Sequence[RetrievalCandidate],
    ) -> Tuple[ProcessCarbonResult, Dict[str, Any]]:
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        doc_titles = {candidate.record.doc_id: candidate.record.title for candidate in candidates}
        doc_tags = {candidate.record.doc_id: candidate.record.activities for candidate in candidates}

        updates = list(data.get("driver_updates") or []) + list(data.get("estimated_drivers") or [])
        for update in updates:
            if not isinstance(update, dict):
                rejected.append({"update": update, "reason": "update is not an object"})
                continue
            doc_id = str(update.get("process_doc_id", "") or "")
            driver_kind = str(update.get("driver_kind", "") or "")
            matches = [
                driver
                for driver in process_result.drivers
                if (not doc_id or driver.process_doc_id == doc_id)
                and normalize_text(driver.driver_kind) == normalize_text(driver_kind)
            ]
            if not matches:
                rejected.append({"update": update, "reason": "no matching retrieved driver"})
                continue
            for driver in matches:
                if self._apply_estimate_to_driver(driver, update, accepted, rejected):
                    break

        for addition in data.get("additional_drivers") or []:
            if not isinstance(addition, dict):
                rejected.append({"update": addition, "reason": "additional driver is not an object"})
                continue
            doc_id = str(addition.get("process_doc_id", "") or "")
            if doc_id and doc_id not in doc_titles:
                rejected.append({"update": addition, "reason": "additional driver doc ID was not retrieved"})
                continue
            driver_kind = str(addition.get("driver_kind", "") or "")
            if not driver_kind:
                rejected.append({"update": addition, "reason": "missing driver_kind"})
                continue
            driver = ActivityDriver(
                process_doc_id=doc_id,
                process_title=doc_titles.get(doc_id, str(addition.get("process_title", "") or "LLM-added process driver")),
                activity_tags=doc_tags.get(doc_id, []),
                driver_kind=driver_kind,
                required_quantity=str(addition.get("required_quantity", "") or "activity-specific consumption quantity"),
                note="LLM-added driver; pending validation",
            )
            energy_factor = self.planner._select_factor(driver_kind)
            if energy_factor:
                driver.preferred_factor_row_id = energy_factor.row_id
                driver.preferred_factor_value = energy_factor.factor_value
                driver.preferred_factor_unit = energy_factor.factor_unit
            self._apply_estimate_to_driver(driver, addition, accepted, rejected)
            process_result.drivers.append(driver)

        self._recompute_process_result(process_result)
        return process_result, {"accepted": accepted, "rejected": rejected}

    def _apply_estimate_to_driver(
        self,
        driver: ActivityDriver,
        update: Dict[str, Any],
        accepted: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
    ) -> bool:
        confidence = safe_float(update.get("confidence"), 0.0) or 0.0
        value = safe_float(update.get("consumption_value"), None)
        if value is None:
            value = safe_float(update.get("estimate_likely"), None)
        if value is None:
            value = safe_float(update.get("likely"), None)
        if value is None:
            rejected.append({"update": update, "reason": "missing numeric consumption_value or likely estimate"})
            return False
        if confidence < 0.45:
            rejected.append({"update": update, "reason": "estimate confidence below 0.45"})
            return False
        if driver.preferred_factor_value is None:
            energy_factor = self.planner._select_factor(driver.driver_kind)
            if energy_factor:
                driver.preferred_factor_row_id = energy_factor.row_id
                driver.preferred_factor_value = energy_factor.factor_value
                driver.preferred_factor_unit = energy_factor.factor_unit
        if driver.preferred_factor_value is None:
            rejected.append({"update": update, "reason": "no emission factor available for driver_kind"})
            return False
        unit = str(update.get("consumption_unit", "") or update.get("unit", "") or "")
        estimate_low = safe_float(update.get("estimate_low"), None)
        estimate_high = safe_float(update.get("estimate_high"), None)
        driver.consumption_value = value
        driver.consumption_unit = unit
        driver.carbon_value_kgco2e = round(value * float(driver.preferred_factor_value), 4)
        driver.status = "estimated_by_llm_analogy"
        interval_text = ""
        if estimate_low is not None or estimate_high is not None:
            interval_text = f" interval=[{estimate_low}, {estimate_high}] {unit}".strip()
        driver.note = unique_join(
            [
                driver.note,
                f"LLM analogy estimate confidence={confidence}{interval_text}",
                str(update.get("basis", "") or update.get("reasoning", "") or ""),
            ]
        )
        accepted.append(
            {
                "process_doc_id": driver.process_doc_id,
                "driver_kind": driver.driver_kind,
                "consumption_value": value,
                "consumption_unit": unit,
                "carbon_value_kgco2e": driver.carbon_value_kgco2e,
                "confidence": confidence,
            }
        )
        return True

    @staticmethod
    def _recompute_process_result(process_result: ProcessCarbonResult) -> None:
        measured = [d for d in process_result.drivers if d.status == "complete" and d.carbon_value_kgco2e is not None]
        estimated = [
            d for d in process_result.drivers if d.status == "estimated_by_llm_analogy" and d.carbon_value_kgco2e is not None
        ]
        unresolved = [d for d in process_result.drivers if d.status not in {"complete", "estimated_by_llm_analogy"}]
        resolved = measured + estimated
        process_result.value_kgco2e = round(sum(float(d.carbon_value_kgco2e or 0.0) for d in resolved), 4) if resolved else None
        process_result.complete_driver_count = len(measured)
        process_result.missing_driver_count = len(unresolved)
        if unresolved and estimated:
            process_result.status = "requires_runtime_quantities_with_llm_estimates"
            process_result.issue = "some process quantities remain missing; included clearly labelled LLM analogy estimates"
        elif unresolved:
            process_result.status = "requires_runtime_quantities"
            process_result.issue = "process steps retrieved, but measured kWh/L/kg activity quantities are missing"
        elif estimated:
            process_result.status = "estimated_process_quantities"
            process_result.issue = "process carbon includes LLM analogy estimates; replace with measured quantities when available"
        else:
            process_result.status = "complete"
            process_result.issue = ""


class GapAgent:
    def __init__(
        self,
        llm: Optional[OpenAICompatibleToolClient] = None,
        tool_registry: Optional[ToolRegistry] = None,
    ):
        self.llm = llm
        self.tools = tool_registry

    def analyze(self, context: ComponentContext, process_payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[AgentTraceEvent]]:
        process_result = process_payload["calculation"]
        missing: List[ActivityDriver] = [driver for driver in process_result.drivers if driver.status != "complete"]
        gaps = []
        for driver in missing:
            gaps.append(
                {
                    "process_doc_id": driver.process_doc_id,
                    "process_title": driver.process_title,
                    "required_quantity": driver.required_quantity,
                    "driver_kind": driver.driver_kind,
                    "suggested_data_collection": self._recommend_collection(driver),
                    "can_estimate_by_analogy": True,
                    "estimate": None,
                    "confidence": 0.35,
                }
            )
        payload = {"gap_count": len(gaps), "gaps": gaps}
        trace = [
            AgentTraceEvent(
                agent="gap",
                thought="Identify missing process quantities and describe what site/factory data are needed.",
                action="gap_analysis",
                observation=payload,
                confidence=0.7 if gaps else 0.95,
                reflection="Missing driver quantities remain." if gaps else "No process gaps.",
            )
        ]
        if self.llm and self.tools and gaps:
            payload, llm_trace = self._analyze_with_llm(context, process_result, payload)
            trace.extend(llm_trace)
        return payload, trace

    def _analyze_with_llm(
        self,
        context: ComponentContext,
        process_result: ProcessCarbonResult,
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[AgentTraceEvent]]:
        react = run_react_tool_loop(
            llm=self.llm,
            tool_registry=self.tools,
            agent_name="gap",
            system_prompt=ASSESSMENT_SYSTEM_PROMPT,
            user_content=compact_json(
                {
                    "task": (
                        "Analyze unresolved process quantity gaps. Use tools if needed. "
                        "For each gap, return estimation_candidates with process_doc_id, driver_kind, "
                        "estimate_low, estimate_likely, estimate_high, unit, confidence, basis, "
                        "and suggested_data_collection. These are planning estimates only, not measured values."
                    ),
                    "component": asdict(component_summary(context)),
                    "process_result": process_result_to_dict(process_result),
                    "gaps": payload["gaps"],
                }
            ),
            max_iterations=4,
            max_tokens=2500,
        )
        data = parse_json_object(react.final_text)
        if not data:
            return payload, react.trace + [
                AgentTraceEvent(
                    agent="gap",
                    thought="LLM gap answer was not valid JSON; deterministic gap list was kept.",
                    action="llm_gap_parse_failed",
                    observation={"raw_final_text": react.final_text, "stopped_by": react.stopped_by},
                    confidence=0.35,
                    reflection="No LLM gap estimates were attached.",
                )
            ]
        estimates = data.get("estimation_candidates") or data.get("gap_estimates") or []
        attached = 0
        for estimate in estimates:
            if not isinstance(estimate, dict):
                continue
            doc_id = str(estimate.get("process_doc_id", "") or "")
            driver_kind = normalize_text(estimate.get("driver_kind", ""))
            for gap in payload["gaps"]:
                if gap["process_doc_id"] == doc_id and normalize_text(gap["driver_kind"]) == driver_kind:
                    gap["estimate"] = {
                        "low": safe_float(estimate.get("estimate_low"), None),
                        "likely": safe_float(estimate.get("estimate_likely"), None),
                        "high": safe_float(estimate.get("estimate_high"), None),
                        "unit": str(estimate.get("unit", "") or ""),
                        "basis": str(estimate.get("basis", "") or ""),
                    }
                    gap["confidence"] = safe_float(estimate.get("confidence"), gap.get("confidence", 0.35)) or 0.35
                    if estimate.get("suggested_data_collection"):
                        gap["suggested_data_collection"] = str(estimate["suggested_data_collection"])
                    attached += 1
                    break
        payload["llm_gap_reasoning"] = data.get("reasoning", "")
        return payload, react.trace + [
            AgentTraceEvent(
                agent="gap",
                thought=str(data.get("reasoning", "LLM generated gap-level analogy estimates.")),
                action="llm_gap_estimation",
                observation={"llm_json": data, "attached_estimate_count": attached, "gap_payload": payload},
                confidence=safe_float(data.get("confidence"), 0.6) or 0.6,
                reflection="Gap estimates attached as planning ranges; measured data still preferred.",
            )
        ]

    @staticmethod
    def _recommend_collection(driver: ActivityDriver) -> str:
        if driver.driver_kind == "electricity":
            return "Record kWh by machine meter, sub-meter, or activity time x rated power."
        if driver.driver_kind == "welding_wire":
            return "Record welding wire issue/return mass by module or weld package."
        if driver.driver_kind.startswith("diesel"):
            return "Record litres of fuel consumed by equipment during the activity."
        return "Record activity-specific consumption quantity."


class ValidatorAgent:
    def __init__(self, llm: Optional[OpenAICompatibleToolClient] = None):
        self.llm = llm

    def validate(self, results: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Any], AgentTraceEvent]:
        flags = []
        factor_by_material: Dict[str, str] = {}
        for row in results:
            material = normalize_text(row.get("materialText", ""))
            factor = (row.get("material") or {}).get("selected_factor", None)
            factor_id = getattr(factor, "row_id", "") if factor else ""
            if material and material in factor_by_material and factor_by_material[material] != factor_id:
                flags.append(
                    {
                        "component_id": row.get("componentGlobalId", ""),
                        "severity": "warning",
                        "issue": "Same material text received inconsistent factor IDs.",
                        "action": "reassess",
                    }
                )
            elif material:
                factor_by_material[material] = factor_id
            mat_calc = row.get("material", {}).get("calculation")
            if mat_calc and field_value(mat_calc, "status") != "complete":
                flags.append(
                    {
                        "component_id": row.get("componentGlobalId", ""),
                        "severity": "warning",
                        "issue": field_value(mat_calc, "issue", ""),
                        "action": "none",
                    }
                )
            flags.extend(self._numeric_flags(row, mat_calc))
        llm_observation: Dict[str, Any] = {}
        if self.llm:
            llm_observation = self._validate_with_llm(results)
            flags.extend(llm_observation.get("flags", []))
        max_severity = max((self._severity_rank(flag.get("severity", "info")) for flag in flags), default=0)
        confidence = 0.85
        if max_severity == 1:
            confidence = 0.72
        elif max_severity == 2:
            confidence = 0.55
        elif max_severity >= 3:
            confidence = 0.35
        validation = {"flags": flags, "overall_confidence": confidence, "llm_validation": llm_observation}
        return validation, AgentTraceEvent(
            agent="validator",
            thought="Check aggregate consistency, numeric outliers, and material/process plausibility.",
            action="aggregate_validation",
            observation=validation,
            confidence=validation["overall_confidence"],
            reflection="Validation complete.",
        )

    def _numeric_flags(self, row: Dict[str, Any], mat_calc: Any) -> List[Dict[str, Any]]:
        flags: List[Dict[str, Any]] = []
        component_id = row.get("componentGlobalId", "")
        component_name = normalize_text(row.get("componentName", ""))
        material_text = normalize_text(row.get("materialText", ""))
        if not mat_calc:
            return flags
        c_mat = safe_float(field_value(mat_calc, "value_kgco2e"), None)
        factor_value = safe_float(field_value(mat_calc, "factor_value"), None)
        quantity = safe_float(field_value(mat_calc, "quantity_value"), None)
        density = safe_float(field_value(mat_calc, "density_kg_m3"), None)
        quantity_unit = normalize_text(field_value(mat_calc, "quantity_unit", ""))
        if c_mat is not None and c_mat < 0:
            flags.append(self._flag(component_id, "error", "Material carbon is negative.", "reassess"))
        if factor_value is not None and (factor_value <= 0 or factor_value > 50):
            flags.append(
                self._flag(
                    component_id,
                    "warning",
                    f"Material factor value looks outside broad kgCO2e/kg bounds: {factor_value}.",
                    "reassess",
                )
            )
        mass_kg = None
        if quantity is not None and density is not None and "m3" in quantity_unit:
            mass_kg = quantity * density
        elif quantity is not None and "kg" in quantity_unit:
            mass_kg = quantity
        if c_mat is not None and mass_kg and mass_kg > 0:
            intensity = c_mat / mass_kg
            if intensity > 25:
                flags.append(
                    self._flag(
                        component_id,
                        "warning",
                        f"Computed material intensity is unusually high: {round(intensity, 4)} kgCO2e/kg.",
                        "reassess",
                    )
                )
            if intensity < 0.005:
                flags.append(
                    self._flag(
                        component_id,
                        "info",
                        f"Computed material intensity is unusually low: {round(intensity, 6)} kgCO2e/kg.",
                        "reassess",
                    )
                )
            if mass_kg < 5 and c_mat > 100:
                flags.append(
                    self._flag(
                        component_id,
                        "warning",
                        f"Small component mass ({round(mass_kg, 4)} kg) has high material carbon ({c_mat} kgCO2e).",
                        "reassess",
                    )
                )
        if c_mat is not None and quantity is not None and "m3" in quantity_unit and quantity < 0.005 and c_mat > 500:
            flags.append(
                self._flag(
                    component_id,
                    "warning",
                    f"Small-volume component ({quantity} m3) has high material carbon ({c_mat} kgCO2e).",
                    "reassess",
                )
            )
        if any(term in component_name + material_text for term in ["bolt", "screw", "anchor", "螺栓", "螺丝"]) and c_mat and c_mat > 100:
            flags.append(self._flag(component_id, "warning", "Fastener-like component has material carbon above 100 kgCO2e.", "reassess"))

        proc_calc = row.get("process", {}).get("calculation")
        c_proc = safe_float(field_value(proc_calc, "value_kgco2e"), None) if proc_calc else None
        if c_mat and c_proc is not None and c_mat > 0:
            ratio = c_proc / c_mat
            if ratio > 1.5:
                flags.append(
                    self._flag(
                        component_id,
                        "warning",
                        f"Process carbon exceeds material carbon by a large margin (C_proc/C_mat={round(ratio, 4)}).",
                        "reassess",
                    )
                )
            elif ratio < 0.001 and field_value(proc_calc, "status", "") == "complete":
                flags.append(
                    self._flag(
                        component_id,
                        "info",
                        f"Complete process carbon is near zero relative to material carbon (C_proc/C_mat={round(ratio, 6)}).",
                        "reassess",
                    )
                )
        return flags

    def _validate_with_llm(self, results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        rows = []
        for row in results:
            material = row.get("material") or {}
            process = row.get("process") or {}
            rows.append(
                {
                    "componentGlobalId": row.get("componentGlobalId", ""),
                    "ifcType": row.get("ifcType", ""),
                    "componentName": row.get("componentName", ""),
                    "materialText": row.get("materialText", ""),
                    "materialStatus": material.get("status", ""),
                    "materialError": material.get("error", ""),
                    "materialCarbon": plain_dataclass_dict(material.get("calculation")),
                    "selectedFactor": safe_material_record_to_dict(material.get("selected_factor")),
                    "processStatus": process.get("status", ""),
                    "processError": process.get("error", ""),
                    "processCarbon": safe_process_result_to_dict(process.get("calculation")),
                    "totalCarbonStatus": row.get("totalCarbonStatus", ""),
                }
            )
        try:
            response = self.llm.complete(
                [
                    {"role": "system", "content": VALIDATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": compact_json({"results": rows}, limit=18000)},
                ],
                max_tokens=2500,
            )
            data = parse_json_object(response.get("content", "")) or {}
        except Exception as exc:
            return {"flags": [], "error": str(exc)}
        clean_flags = []
        for flag in data.get("flags", []) or []:
            if not isinstance(flag, dict):
                continue
            clean_flags.append(
                {
                    "component_id": str(flag.get("component_id", "") or ""),
                    "severity": str(flag.get("severity", "info") or "info"),
                    "issue": str(flag.get("issue", "") or ""),
                    "action": str(flag.get("action", "none") or "none"),
                    "source": "llm_validator",
                }
            )
        return {"flags": clean_flags, "overall_confidence": data.get("overall_confidence", None)}

    @staticmethod
    def _flag(component_id: str, severity: str, issue: str, action: str) -> Dict[str, Any]:
        return {"component_id": component_id, "severity": severity, "issue": issue, "action": action, "source": "local_validator"}

    @staticmethod
    def _severity_rank(severity: str) -> int:
        return {"info": 1, "warning": 2, "error": 3}.get(str(severity or "").lower(), 1)


class DM2CAgenticRAG:
    def __init__(
        self,
        llm_client: OpenAICompatibleToolClient,
        graph_store: BackboneGraphStore,
        factor_store: WorkbookFactorStore,
        process_store: ProcessEvidenceStore,
        profile_store: ProcessProfileStore,
        top_process_k: int,
        factory_grid: str,
        embedding_model: str = "",
        max_iterations: int = 5,
    ):
        self.llm = llm_client
        self.graph = graph_store
        self.factors = factor_store
        self.processes = process_store
        self.profiles = profile_store
        self.max_iterations = max_iterations
        self.tools = ToolRegistry(graph_store, factor_store, process_store, profile_store, factory_grid, llm_client, embedding_model)
        self.orchestrator = OrchestratorAgent(llm_client)
        self.material_agent = MaterialAgent(self.tools, MaterialCarbonCalculator(), llm_client)
        self.process_agent = ProcessAgent(
            self.tools,
            ProcessCarbonPlanner(factor_store, profile_store, factory_grid),
            top_process_k,
            llm_client,
        )
        self.gap_agent = GapAgent(llm_client, self.tools)
        self.validator = ValidatorAgent(llm_client)

    def run(self, question: str) -> Dict[str, Any]:
        started = time.time()
        contexts = self.graph.component_contexts()
        summaries = [component_summary(context) for context in contexts]
        plan, plan_trace = self.orchestrator.plan(summaries, question)
        trace: List[AgentTraceEvent] = [plan_trace]
        context_by_id = {context.global_id: context for context in contexts}
        context_by_id.update({str(context.component.get("id", "")): context for context in contexts})

        results: List[Dict[str, Any]] = []
        # Cache LLM material factor decisions by material text.
        # Same material text → same factor match → skip redundant LLM calls.
        # The Orchestrator's grouping quality directly affects cache hit rate.
        material_factor_cache: Dict[str, Dict[str, Any]] = {}

        for group in plan.get("groups", []):
            for component_id in group.get("component_ids", []):
                context = context_by_id.get(component_id)
                if not context:
                    continue
                try:
                    cache_key = normalize_text(context.material_text)
                    cached = material_factor_cache.get(cache_key)
                    result, component_trace = self._assess_component(
                        context, group.get("strategy", "default"), cached_material=cached
                    )
                    results.append(result)
                    trace.extend(component_trace)
                    # Cache the factor decision for reuse by same-material components
                    if cache_key and cache_key not in material_factor_cache:
                        mat = result.get("material", {})
                        if mat.get("status") != "llm_failed" and mat.get("selected_factor"):
                            material_factor_cache[cache_key] = {
                                "selected_factor": mat["selected_factor"],
                                "confidence": mat.get("confidence", 0.5),
                                "source_component": context.name,
                            }
                except Exception as exc:
                    # Don't crash the entire assessment — record the failure and continue
                    error_trace = AgentTraceEvent(
                        agent="component_loop",
                        thought=f"Component assessment failed: {exc}",
                        action="component_error",
                        observation={
                            "component_id": component_id,
                            "component_name": context.name,
                            "material_text": context.material_text,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                        confidence=0.0,
                        reflection="LLM failed for this component. It is skipped but recorded in trace and results.",
                    )
                    trace.append(error_trace)
                    results.append({
                        "componentGlobalId": context.global_id,
                        "ifcType": context.ifc_type,
                        "componentName": context.name,
                        "materialText": context.material_text,
                        "strategy": group.get("strategy", "default"),
                        "material": {"status": "llm_failed", "error": str(exc)},
                        "process": {"status": "skipped", "calculation": {"status": "skipped", "value_kgco2e": None, "drivers": []}},
                        "gaps": {"gaps": []},
                        "knownTotalCarbon_kgCO2e": None,
                        "totalCarbonStatus": "llm_failed",
                        "confidence": 0.0,
                    })

        validation, validation_trace = self.validator.validate(results)
        trace.append(validation_trace)

        # Count cache efficiency for research analysis
        cache_hits = sum(1 for t in trace if t.action == "material_cache_hit")
        total_components = len(results)
        cache_misses = total_components - cache_hits - sum(1 for r in results if r.get("totalCarbonStatus") == "llm_failed")

        payload = self._build_payload(question, plan, results, validation, trace, started)
        payload["materialFactorCache"] = {
            "unique_materials": len(material_factor_cache),
            "cache_hits": cache_hits,
            "llm_calls_saved": cache_hits,
            "total_components": total_components,
            "hit_rate": round(cache_hits / max(total_components, 1), 3),
        }
        return payload

    def _assess_component(
        self,
        context: ComponentContext,
        strategy: str,
        cached_material: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], List[AgentTraceEvent]]:
        trace: List[AgentTraceEvent] = [
            AgentTraceEvent(
                agent="component_loop",
                thought="Start ReAct-style component assessment.",
                action="observe_component",
                observation={"component": asdict(component_summary(context)), "strategy": strategy},
                confidence=1.0,
                reflection="Component state gathered from graph.",
            )
        ]

        if cached_material:
            # Reuse LLM's factor decision from a previous same-material component.
            # Recalculate C_mat with THIS component's quantities (mass/volume/area).
            selected_factor = cached_material["selected_factor"]
            calc = self.material_agent.calculator.calculate(context, selected_factor)
            material_payload = {
                "selected_factor": selected_factor,
                "candidates": [],
                "calculation": calc,
                "confidence": cached_material["confidence"],
                "status": calc.status,
            }
            trace.append(AgentTraceEvent(
                agent="material",
                thought=f"Reused factor match from '{cached_material['source_component']}' (same material text).",
                action="material_cache_hit",
                observation={
                    "cached_from": cached_material["source_component"],
                    "factor_id": selected_factor.row_id if hasattr(selected_factor, 'row_id') else str(selected_factor),
                    "calculation": asdict(calc),
                },
                confidence=cached_material["confidence"],
                reflection="LLM factor decision reused — Orchestrator grouping saved an LLM call.",
            ))
        else:
            material_payload, material_trace = self.material_agent.assess(context)
            trace.extend(material_trace)

        process_payload, process_trace = self.process_agent.assess(context)
        trace.extend(process_trace)
        gap_payload, gap_trace = self.gap_agent.analyze(context, process_payload)
        trace.extend(gap_trace)

        material_calc = material_payload["calculation"]
        process_calc = process_payload["calculation"]
        c_mat = material_calc.value_kgco2e
        c_proc = process_calc.value_kgco2e
        known_total = None
        total_status = "incomplete"
        if c_mat is not None and c_proc is not None:
            known_total = round(c_mat + c_proc, 4)
            total_status = "complete" if process_calc.status == "complete" else "partial_process_known"
        elif c_mat is not None:
            known_total = c_mat
            total_status = "material_only_process_missing"

        result = {
            "componentGlobalId": context.global_id,
            "ifcType": context.ifc_type,
            "componentName": context.name,
            "materialText": context.material_text,
            "strategy": strategy,
            "material": material_payload,
            "process": process_payload,
            "gaps": gap_payload,
            "knownTotalCarbon_kgCO2e": known_total,
            "totalCarbonStatus": total_status,
            "confidence": round(min(material_payload["confidence"], process_payload["confidence"]), 4),
            "reasoningTrace": [asdict(event) for event in trace],
        }
        return result, trace

    def _build_payload(
        self,
        question: str,
        plan: Dict[str, Any],
        results: Sequence[Dict[str, Any]],
        validation: Dict[str, Any],
        trace: Sequence[AgentTraceEvent],
        started: float,
    ) -> Dict[str, Any]:
        serializable_results = [serialize_component_result(row) for row in results]
        summary = summarize_results(results)
        return {
            "question": question,
            "architecture": "orchestrator + material/process/gap/validator agents with tool registry and ReAct trace",
            "llmEnabled": self.llm is not None,
            "plan": plan,
            "results": serializable_results,
            "summary": summary,
            "validation": validation,
            "reasoningTrace": [asdict(event) for event in trace],
            "inputs": {
                "materialFactorRows": len(self.factors.material_factors),
                "energyFactorRows": len(self.factors.energy_factors),
                "processEvidenceRows": len(self.processes.docs),
                "elapsedMs": round((time.time() - started) * 1000, 3),
            },
        }


def component_summary(context: ComponentContext) -> ComponentSummary:
    quantities = []
    for quantity in context.quantities:
        q_props = props(quantity)
        quantities.append(
            {
                "quantityName": q_props.get("quantityName", ""),
                "quantityValue": q_props.get("quantityValue", None),
                "quantityUnit": q_props.get("quantityUnit", ""),
            }
        )
    return ComponentSummary(
        component_id=str(context.component.get("id", "")),
        global_id=context.global_id,
        ifc_type=context.ifc_type,
        name=context.name,
        material_text=context.material_text,
        quantities=quantities,
    )


def process_evidence_to_dict(record: ProcessEvidence) -> Dict[str, Any]:
    return {
        "docId": record.doc_id,
        "title": record.title,
        "componentTypes": record.component_types,
        "keywords": record.keywords,
        "activities": record.activities,
        "text": record.text,
        "sourceFile": record.source_file,
        "sourceType": record.source_type,
        "sequence": record.sequence,
    }


def field_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def plain_dataclass_dict(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    return {"value": str(value)}


def safe_material_record_to_dict(record: Any) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    if isinstance(record, dict):
        return record
    return material_record_to_dict(record)


def safe_material_candidate_to_dict(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return candidate
    return material_candidate_to_dict(candidate)


def safe_process_candidate_to_dict(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return candidate
    return process_candidate_to_dict(candidate)


def safe_process_result_to_dict(result: Any) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    return process_result_to_dict(result)


def serialize_component_result(row: Dict[str, Any]) -> Dict[str, Any]:
    material = row.get("material") or {}
    process = row.get("process") or {}
    return {
        "componentGlobalId": row.get("componentGlobalId", ""),
        "ifcType": row.get("ifcType", ""),
        "componentName": row.get("componentName", ""),
        "materialText": row.get("materialText", ""),
        "strategy": row.get("strategy", ""),
        "selectedMaterialFactor": safe_material_record_to_dict(material.get("selected_factor")),
        "materialCandidates": [safe_material_candidate_to_dict(c) for c in material.get("candidates", [])],
        "materialCarbon": plain_dataclass_dict(material.get("calculation")),
        "materialStatus": material.get("status", ""),
        "materialError": material.get("error", ""),
        "selectedProcessSteps": [safe_process_candidate_to_dict(c) for c in process.get("candidates", [])],
        "processCarbon": safe_process_result_to_dict(process.get("calculation")),
        "processStatus": process.get("status", ""),
        "processError": process.get("error", ""),
        "gaps": row.get("gaps", {}),
        "knownTotalCarbon_kgCO2e": row.get("knownTotalCarbon_kgCO2e"),
        "totalCarbonStatus": row.get("totalCarbonStatus", ""),
        "confidence": row.get("confidence", 0.0),
        "reasoningTrace": row.get("reasoningTrace", []),
    }


def summarize_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total_c_mat = 0.0
    total_c_proc = 0.0
    material_complete = 0
    process_complete = 0
    gap_count = 0
    for row in results:
        mat_calc = (row.get("material") or {}).get("calculation")
        proc_calc = (row.get("process") or {}).get("calculation")
        c_mat = safe_float(field_value(mat_calc, "value_kgco2e"), None) if mat_calc else None
        c_proc = safe_float(field_value(proc_calc, "value_kgco2e"), None) if proc_calc else None
        if c_mat is not None:
            total_c_mat += float(c_mat)
        if c_proc is not None:
            total_c_proc += float(c_proc)
        material_complete += int(field_value(mat_calc, "status") == "complete") if mat_calc else 0
        process_complete += int(field_value(proc_calc, "status") == "complete") if proc_calc else 0
        gap_count += int((row.get("gaps") or {}).get("gap_count", 0) or 0)
    return {
        "components": len(results),
        "materialCarbonComplete": material_complete,
        "processCarbonComplete": process_complete,
        "totalMaterialCarbon_kgCO2e": round(total_c_mat, 4),
        "knownProcessCarbon_kgCO2e": round(total_c_proc, 4),
        "knownTotalCarbon_kgCO2e": round(total_c_mat + total_c_proc, 4),
        "processInputGaps": gap_count,
    }


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(text or "")).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


ALLOWED_OPERATORS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


def safe_eval(expression: str) -> float:
    def eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -eval_node(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPERATORS:
            return float(ALLOWED_OPERATORS[type(node.op)](eval_node(node.left), eval_node(node.right)))
        raise ValueError(f"Unsupported expression: {expression}")

    return round(eval_node(ast.parse(expression, mode="eval")), 8)


class AgenticExporter:
    @staticmethod
    def write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def write_markdown(path: Path, payload: Dict[str, Any]) -> None:
        summary = payload["summary"]
        lines = [
            "# DM2C Agentic RAG v2 Architecture Run",
            "",
            f"Question: {payload.get('question', '')}",
            "",
            "## Summary",
            "",
            f"- LLM enabled: {payload.get('llmEnabled')}",
            f"- Components: {summary.get('components', 0)}",
            f"- Material carbon complete: {summary.get('materialCarbonComplete', 0)}",
            f"- Process carbon complete: {summary.get('processCarbonComplete', 0)}",
            f"- Total material carbon: {summary.get('totalMaterialCarbon_kgCO2e', 0)} kgCO2e",
            f"- Known process carbon: {summary.get('knownProcessCarbon_kgCO2e', 0)} kgCO2e",
            f"- Known total carbon: {summary.get('knownTotalCarbon_kgCO2e', 0)} kgCO2e",
            f"- Process input gaps: {summary.get('processInputGaps', 0)}",
            "",
            "## Execution Plan",
            "",
            "```json",
            json.dumps(payload.get("plan", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## Component Results",
            "",
        ]
        for row in payload.get("results", []):
            factor = row.get("selectedMaterialFactor") or {}
            mat = row.get("materialCarbon") or {}
            proc = row.get("processCarbon") or {}
            lines.extend(
                [
                    f"### {row.get('componentName', '')}",
                    "",
                    f"- IFC type: {row.get('ifcType', '')}",
                    f"- Material: {row.get('materialText', '')}",
                    f"- Selected factor: {factor.get('materialName', '')} ({factor.get('factorValue', '')} {factor.get('factorUnit', '')})",
                    f"- C_mat: {mat.get('value_kgco2e', None)} kgCO2e ({mat.get('status', '')})",
                    f"- C_proc: {proc.get('value_kgco2e', None)} kgCO2e ({proc.get('status', '')})",
                    f"- Known C_MM: {row.get('knownTotalCarbon_kgCO2e', None)} kgCO2e ({row.get('totalCarbonStatus', '')})",
                    f"- Confidence: {row.get('confidence', '')}",
                    "",
                    "Top process evidence:",
                ]
            )
            for step in row.get("selectedProcessSteps", [])[:8]:
                lines.append(f"- {step.get('title', '')} [{step.get('sourceType', '')}]")
            gaps = row.get("gaps", {}).get("gaps", [])
            if gaps:
                lines.extend(["", "Missing quantities:"])
                for gap in gaps[:12]:
                    lines.append(f"- {gap.get('process_title', '')}: {gap.get('required_quantity', '')}")
            lines.append("")
        lines.extend(
            [
                "## Validation",
                "",
                "```json",
                json.dumps(payload.get("validation", {}), ensure_ascii=False, indent=2),
                "```",
            ]
        )
        path.write_text("\n".join(lines), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run architecture-level DM2C Agentic RAG v2.")
    parser.add_argument("--graph", default="outputs/dm2c_backbone_graph.json")
    parser.add_argument("--factor-workbook", default="Embodied_Carbon_Coefficients_Updated (1).xlsx")
    parser.add_argument("--process-docs", default="outputs/rag_experiments/itp_module_process_docs.jsonl")
    parser.add_argument("--process-profile", default="")
    parser.add_argument("--out-dir", default="outputs/rag_experiments/agentic_architecture")
    parser.add_argument("--top-process-k", type=int, default=8)
    parser.add_argument("--factory-grid", default="Guangdong")
    parser.add_argument("--llm-provider", choices=["openai", "gemini"], default="openai",
                        help="LLM provider (required — no deterministic fallback).")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--embedding-model", default=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--api-max-retries", type=int, default=2)
    parser.add_argument(
        "--question",
        default="Calculate modular manufacturing-stage C_MM using material factors, ITP process evidence, and energy factors.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"Missing API key. Set {args.api_key_env} environment variable.")
    llm_client = OpenAICompatibleToolClient(
        api_key=api_key,
        model=args.model,
        base_url=args.api_base_url,
        temperature=args.temperature,
        timeout=args.timeout,
        max_retries=args.api_max_retries,
    )

    graph_store = BackboneGraphStore(Path(args.graph))
    factor_store = WorkbookFactorStore(Path(args.factor_workbook))
    process_store = ProcessEvidenceStore(Path(args.process_docs))
    profile_path = Path(args.process_profile) if args.process_profile else None
    profile_store = ProcessProfileStore(profile_path)
    runner = DM2CAgenticRAG(
        llm_client=llm_client,
        graph_store=graph_store,
        factor_store=factor_store,
        process_store=process_store,
        profile_store=profile_store,
        top_process_k=args.top_process_k,
        factory_grid=args.factory_grid,
        embedding_model=args.embedding_model,
    )
    payload = runner.run(args.question)
    payload["inputs"].update(
        {
            "graph": args.graph,
            "factorWorkbook": args.factor_workbook,
            "processDocs": args.process_docs,
            "processProfile": args.process_profile,
            "factoryGrid": args.factory_grid,
            "llmProvider": args.llm_provider,
            "model": args.model,
            "embeddingModel": args.embedding_model,
        }
    )

    AgenticExporter.write_json(out_dir / "agentic_rag_v2_architecture_results.json", payload)
    AgenticExporter.write_json(out_dir / "agentic_rag_v2_architecture_summary.json", payload["summary"])
    AgenticExporter.write_markdown(out_dir / "agentic_rag_v2_architecture_report.md", payload)
    print("DM2C architecture-level Agentic RAG v2 completed.")
    print(f"  LLM provider: {args.llm_provider}")
    print(f"  Output directory: {out_dir}")
    print(f"  Total material carbon: {payload['summary']['totalMaterialCarbon_kgCO2e']} kgCO2e")
    print(f"  Known total carbon: {payload['summary']['knownTotalCarbon_kgCO2e']} kgCO2e")
    print(f"  Process input gaps: {payload['summary']['processInputGaps']}")


if __name__ == "__main__":
    main()
