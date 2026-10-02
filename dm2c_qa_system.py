"""
DM2C question-answering layer aligned with Method M3.1-M3.3.

M3.1: ontology-constrained semantic parsing of the user question.
M3.2: deterministic model-grounded query execution over assessed carbon records.
M3.3: LLM answer synthesis from structured query results and provenance.

The LLM is deliberately kept out of numeric calculation. Carbon values come
from the deterministic assessment payload or from the local deterministic
AgenticRagV2 calculator when no assessment payload is available yet.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dm2c_agentic_rag import BackboneGraphStore, ComponentContext, normalize_text, props, safe_float
from dm2c_agentic_rag_v2 import (
    AgenticRagV2,
    ProcessEvidenceStore,
    ProcessProfileStore,
    WorkbookFactorStore,
    material_record_to_dict,
)
from dm2c_agentic_rag_v2_agentic import OpenAICompatibleToolClient, compact_json, parse_json_object
from dm2c_reference_grounding import parse_reference_selector, resolve_reference


PERSPECTIVES = {"product", "material", "process", "traceability"}
SCOPES = {"material_related", "process_related", "total_modularization"}
METRICS = {"value", "share", "intensity", "rank", "difference", "evidence_path"}
OPERATIONS = {"retrieve", "aggregate", "rank", "compare", "explain", "trace"}
RESPONSE_FORMS = {"value", "table", "ranking", "comparison", "explanation", "traceability_chain"}


M31_SYSTEM_PROMPT = """You identify carbon information requirements for modular construction.
Map the user question to a graph-executable requirement. Do not calculate.

Return ONLY JSON:
{
  "perspective": "product|material|process|traceability",
  "target_hints": ["short strings from the question"],
  "target_ids": ["candidate ids if clearly selected"],
  "scope": "material_related|process_related|total_modularization",
  "metric": "value|share|intensity|rank|difference|evidence_path",
  "operation": "retrieve|aggregate|rank|compare|explain|trace",
  "constraints": {"top_k": 10, "baseline": "", "unit": "kgCO2e"},
  "response_form": "value|table|ranking|comparison|explanation|traceability_chain",
  "reference_selector": {
    "expected_cardinality": "none|singleton|set",
    "type_terms": [],
    "name_terms": [],
    "material_terms": [],
    "storey_terms": [],
    "production_terms": [],
    "deictic": false,
    "required": false
  },
  "confidence": 0.0,
  "reasoning": "one sentence"
}

Rules:
- product: modular units/components and assigned carbon.
- material: material categories/items and material carbon contribution.
- process: production stages, activities, energy carriers, batches, process carbon, missing process inputs.
- traceability: quantity, factor, formula, source, BIM GlobalId, calculation path.
- Parse component references as selector constraints; never invent or choose a GlobalId.
- Use singleton for one component, set for plural/rank/aggregate targets, and none for project-level questions.
- Keep the scope inside the factory-gate modularization boundary.
- If unsure, choose the closest perspective and lower confidence.
"""


M33_SYSTEM_PROMPT = """You answer modular construction carbon questions using only the provided structured result.
Do not invent carbon values, factors, sources, quantities, or process data.
State missing data explicitly. Keep the answer concise and research-auditable.
Prefer 1-2 short paragraphs plus a compact list/table in text when useful.
"""


@dataclass
class CarbonRequirement:
    perspective: str
    target_hints: List[str] = field(default_factory=list)
    target_ids: List[str] = field(default_factory=list)
    scope: str = "total_modularization"
    metric: str = "value"
    operation: str = "retrieve"
    constraints: Dict[str, Any] = field(default_factory=dict)
    response_form: str = "explanation"
    reference_selector: Dict[str, Any] = field(default_factory=dict)
    status: str = "executable"
    confidence: float = 0.5
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedCarbonRow:
    component_id: str
    component_name: str
    ifc_type: str
    material_text: str
    c_mat: Optional[float]
    c_proc: Optional[float]
    c_total: Optional[float]
    material_status: str
    process_status: str
    total_status: str
    selected_factor: Dict[str, Any]
    material_carbon: Dict[str, Any]
    process_carbon: Dict[str, Any]
    process_steps: List[Dict[str, Any]]
    gaps: List[Dict[str, Any]]
    confidence: Optional[float]
    raw: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DM2CQuestionAnsweringSystem:
    def __init__(
        self,
        llm_client: OpenAICompatibleToolClient,
        graph_store: BackboneGraphStore,
        factor_store: WorkbookFactorStore,
        process_store: ProcessEvidenceStore,
        profile_store: ProcessProfileStore,
        top_process_k: int = 8,
        factory_grid: str = "Guangdong",
    ):
        self.llm = llm_client
        self.graph = graph_store
        self.factors = factor_store
        self.processes = process_store
        self.profiles = profile_store
        self.top_process_k = top_process_k
        self.factory_grid = factory_grid
        self._deterministic_runner = AgenticRagV2(
            graph_store=graph_store,
            factor_store=factor_store,
            process_store=process_store,
            profile_store=profile_store,
            top_process_k=top_process_k,
            factory_grid_preference=factory_grid,
        )

    def run(
        self,
        question: str,
        current_payload: Optional[Dict[str, Any]] = None,
        selected_context: Optional[List[Dict[str, Any]]] = None,
        scope: str = "project",
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        started = time.time()
        selected_context = selected_context or []
        filters = filters or {}
        assessment_payload = self._ensure_assessment_payload(question, current_payload)
        rows = self._normalize_rows(assessment_payload.get("results", []))

        requirement, parse_trace = self.identify_requirement(question, rows, selected_context, scope, filters)
        grounding_decision = resolve_reference(
            question,
            [self._reference_component(row) for row in rows],
            selected_ids=[
                str(item.get("globalId") or item.get("componentNodeId") or "")
                for item in selected_context
            ],
            selector_data=requirement.reference_selector or None,
        )
        reference_trace = self._reference_grounding_trace(grounding_decision.to_dict())

        if grounding_decision.decision == "commit":
            requirement.target_ids = list(grounding_decision.committed_ids)
        elif grounding_decision.decision == "clarify":
            requirement.status = "clarification_required"
        elif grounding_decision.decision == "unresolved":
            requirement.status = "unresolved_target"

        if requirement.status in {"clarification_required", "unresolved_target"}:
            grounded_rows = []
            grounding_trace = None
        else:
            grounded_rows, grounding_trace = self._ground_requirement(requirement, rows, selected_context)
        query_result, query_trace = self.execute_query(requirement, grounded_rows, rows)
        if grounding_decision.decision in {"clarify", "unresolved"}:
            query_result["candidates"] = [
                candidate.to_dict() for candidate in grounding_decision.candidates
            ]
            query_result["groundingReason"] = grounding_decision.reason
        answer, synthesis_trace = self.synthesize_answer(question, requirement, query_result)

        reasoning_trace = [parse_trace, reference_trace]
        if grounding_trace is not None:
            reasoning_trace.append(grounding_trace)
        reasoning_trace.extend([query_trace, synthesis_trace])

        payload = dict(assessment_payload)
        payload.update(
            {
                "question": question,
                "answer": answer,
                "architecture": "M3.1 semantic parsing -> M3.2 model-grounded query execution -> M3.3 grounded answer synthesis",
                "carbonRequirement": requirement.to_dict(),
                "referenceGrounding": grounding_decision.to_dict(),
                "queryResult": query_result,
                "requestContext": {
                    "scope": scope or ("selected_component" if selected_context else "project"),
                    "selectedComponents": selected_context,
                    "filters": filters,
                },
                "reasoningTrace": reasoning_trace,
            }
        )
        payload.setdefault("inputs", {})["elapsedMs"] = round((time.time() - started) * 1000, 3)
        payload["inputs"]["qaMode"] = "m3_question_answering"
        return payload

    # ------------------------------------------------------------------
    # M3.1
    # ------------------------------------------------------------------
    def identify_requirement(
        self,
        question: str,
        rows: Sequence[NormalizedCarbonRow],
        selected_context: Sequence[Dict[str, Any]],
        scope: str,
        filters: Dict[str, Any],
    ) -> Tuple[CarbonRequirement, Dict[str, Any]]:
        candidate_snapshot = self._candidate_snapshot(rows, selected_context)
        user_payload = {
            "question": question,
            "ui_scope": scope,
            "filters": filters,
            "selected_context": selected_context,
            "candidate_snapshot": candidate_snapshot,
        }
        messages = [
            {"role": "system", "content": M31_SYSTEM_PROMPT},
            {"role": "user", "content": compact_json(user_payload, limit=10000)},
        ]

        llm_error = ""
        data: Dict[str, Any] = {}
        try:
            response = self.llm.complete(messages, max_tokens=1200)
            data = parse_json_object(response.get("content", ""))
        except Exception as exc:  # Keep QA usable while surfacing the failure.
            llm_error = str(exc)

        requirement = self._requirement_from_json(data) if data else self._heuristic_requirement(question, selected_context)
        trace = {
            "agent": "M3.1_requirement_identifier",
            "thought": "Converted the user question into a constrained carbon requirement.",
            "action": "ontology_constrained_semantic_parse",
            "observation": {
                "requirement": requirement.to_dict(),
                "llm_json": data,
                "llm_error": llm_error,
                "candidateCounts": {
                    "components": len(candidate_snapshot.get("components", [])),
                    "materials": len(candidate_snapshot.get("materials", [])),
                    "processes": len(candidate_snapshot.get("processes", [])),
                },
            },
            "confidence": requirement.confidence,
            "reflection": requirement.reasoning or "Requirement ready for graph grounding.",
        }
        return requirement, trace

    def _requirement_from_json(self, data: Dict[str, Any]) -> CarbonRequirement:
        perspective = self._allowed(data.get("perspective"), PERSPECTIVES, "product")
        operation = self._allowed(data.get("operation"), OPERATIONS, "retrieve")
        metric = self._allowed(data.get("metric"), METRICS, "value")
        response_form = self._allowed(data.get("response_form") or data.get("responseForm"), RESPONSE_FORMS, "explanation")
        scope = self._allowed(data.get("scope"), SCOPES, "total_modularization")
        return CarbonRequirement(
            perspective=perspective,
            target_hints=self._as_text_list(data.get("target_hints") or data.get("targetHints")),
            target_ids=self._as_text_list(data.get("target_ids") or data.get("targetIds")),
            scope=scope,
            metric=metric,
            operation=operation,
            constraints=dict(data.get("constraints") or {}),
            response_form=response_form,
            reference_selector=dict(
                data.get("reference_selector") or data.get("referenceSelector") or {}
            ),
            confidence=float(safe_float(data.get("confidence"), 0.65) or 0.65),
            reasoning=str(data.get("reasoning", "") or ""),
        )

    def _heuristic_requirement(self, question: str, selected_context: Sequence[Dict[str, Any]]) -> CarbonRequirement:
        q = normalize_text(question)
        perspective = "product"
        if any(term in q for term in ["material", "factor", "steel", "concrete", "aluminium", "aluminum", "材料"]):
            perspective = "material"
        if any(term in q for term in ["process", "stage", "activity", "welding", "cutting", "energy", "missing", "gap", "工序", "过程"]):
            perspective = "process"
        trace_terms = ["trace", "source", "evidence", "formula", "globalid", "quantity", "追溯", "来源"]
        trace_question = re.search(r"\b(why|how)\b", q) is not None
        if any(term in q for term in trace_terms) or trace_question:
            perspective = "traceability"

        operation = "retrieve"
        metric = "value"
        response_form = "explanation"
        if any(term in q for term in ["highest", "top", "rank", "最大", "最高", "排序"]):
            operation = "rank"
            metric = "rank"
            response_form = "ranking"
        elif any(term in q for term in ["compare", "vs", "versus", "difference", "比较"]):
            operation = "compare"
            metric = "difference"
            response_form = "comparison"
        elif any(term in q for term in ["total", "sum", "aggregate", "breakdown", "总", "汇总"]):
            operation = "aggregate"
            response_form = "table"
        elif perspective == "traceability":
            operation = "trace"
            metric = "evidence_path"
            response_form = "traceability_chain"

        target_ids = []
        if selected_context:
            for item in selected_context:
                target_ids.extend([str(item.get("globalId", "")), str(item.get("componentNodeId", ""))])
        return CarbonRequirement(
            perspective=perspective,
            target_ids=[value for value in target_ids if value],
            scope="total_modularization",
            metric=metric,
            operation=operation,
            response_form=response_form,
            reference_selector=parse_reference_selector(question).to_dict(),
            confidence=0.45,
            reasoning="Heuristic fallback used because the LLM requirement JSON was unavailable.",
        )

    # ------------------------------------------------------------------
    # M3.2
    # ------------------------------------------------------------------
    def execute_query(
        self,
        requirement: CarbonRequirement,
        grounded_rows: Sequence[NormalizedCarbonRow],
        all_rows: Sequence[NormalizedCarbonRow],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if requirement.status in {"unresolved_target", "clarification_required"}:
            query_result = {
                "status": requirement.status,
                "perspective": requirement.perspective,
                "operation": requirement.operation,
                "scope": requirement.scope,
                "rows": [],
                "summary": {},
                "semanticPath": "",
            }
            trace = {
                "agent": "M3.2_query_executor",
                "thought": "Stopped deterministic query execution because the requirement was not graph-grounded.",
                "action": f"{requirement.perspective}_{requirement.operation}",
                "observation": {
                    "status": query_result.get("status"),
                    "rowCount": 0,
                    "summary": {},
                },
                "confidence": 0.2,
                "reflection": "Unsupported targets are reported rather than inferred.",
            }
            return query_result, trace

        rows = list(grounded_rows)
        if not rows and requirement.status == "executable":
            rows = list(all_rows)

        if requirement.perspective == "material":
            query_result = self._execute_material_query(requirement, rows)
        elif requirement.perspective == "process":
            query_result = self._execute_process_query(requirement, rows)
        elif requirement.perspective == "traceability":
            query_result = self._execute_traceability_query(requirement, rows)
        else:
            query_result = self._execute_product_query(requirement, rows)

        trace = {
            "agent": "M3.2_query_executor",
            "thought": "Executed a deterministic query over graph-grounded carbon records.",
            "action": f"{requirement.perspective}_{requirement.operation}",
            "observation": {
                "status": query_result.get("status"),
                "rowCount": len(query_result.get("rows", []) or []),
                "summary": query_result.get("summary", {}),
            },
            "confidence": 1.0 if query_result.get("status") != "unresolved_target" else 0.2,
            "reflection": "No LLM-generated carbon values were used.",
        }
        return query_result, trace

    def _execute_product_query(self, requirement: CarbonRequirement, rows: Sequence[NormalizedCarbonRow]) -> Dict[str, Any]:
        ranked = sorted(rows, key=lambda row: self._metric_value(row, requirement.scope), reverse=True)
        top_k = int(safe_float(requirement.constraints.get("top_k"), 10) or 10)
        selected = ranked[:top_k] if requirement.operation == "rank" else list(rows)
        total_mat = sum(row.c_mat or 0.0 for row in selected)
        total_proc = sum(row.c_proc or 0.0 for row in selected)
        out_rows = [
            {
                "componentGlobalId": row.component_id,
                "componentName": row.component_name,
                "ifcType": row.ifc_type,
                "materialText": row.material_text,
                "C_mat_kgCO2e": row.c_mat,
                "C_proc_known_kgCO2e": row.c_proc,
                "C_MM_known_kgCO2e": row.c_total,
                "status": row.total_status,
            }
            for row in selected
        ]
        return {
            "status": "executable" if selected else "empty_result",
            "perspective": "product",
            "operation": requirement.operation,
            "scope": requirement.scope,
            "rows": out_rows,
            "summary": {
                "components": len(selected),
                "totalMaterialCarbon_kgCO2e": round(total_mat, 4),
                "knownProcessCarbon_kgCO2e": round(total_proc, 4),
                "knownTotalCarbon_kgCO2e": round(total_mat + total_proc, 4),
            },
            "semanticPath": "CarbonEmission -> emissionOf -> ModularUnit / BuildingComponent",
        }

    def _execute_material_query(self, requirement: CarbonRequirement, rows: Sequence[NormalizedCarbonRow]) -> Dict[str, Any]:
        groups: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = self._material_key(row)
            bucket = groups.setdefault(
                key,
                {
                    "material": key,
                    "components": 0,
                    "materialCarbon_kgCO2e": 0.0,
                    "knownTotalCarbon_kgCO2e": 0.0,
                    "factorRows": set(),
                    "examples": [],
                },
            )
            bucket["components"] += 1
            bucket["materialCarbon_kgCO2e"] += row.c_mat or 0.0
            bucket["knownTotalCarbon_kgCO2e"] += row.c_total or 0.0
            factor_id = str(row.selected_factor.get("rowId", "") or "")
            if factor_id:
                bucket["factorRows"].add(factor_id)
            if len(bucket["examples"]) < 3:
                bucket["examples"].append(row.component_name)
        total_material = sum(item["materialCarbon_kgCO2e"] for item in groups.values())
        material_rows = []
        for item in groups.values():
            value = item["materialCarbon_kgCO2e"]
            material_rows.append(
                {
                    "material": item["material"],
                    "components": item["components"],
                    "materialCarbon_kgCO2e": round(value, 4),
                    "shareOfMaterialCarbon": round(value / total_material, 4) if total_material else None,
                    "factorRows": sorted(item["factorRows"]),
                    "exampleComponents": item["examples"],
                }
            )
        material_rows.sort(key=lambda item: item["materialCarbon_kgCO2e"], reverse=True)
        return {
            "status": "executable" if material_rows else "empty_result",
            "perspective": "material",
            "operation": requirement.operation,
            "scope": "material_related",
            "rows": material_rows[: int(safe_float(requirement.constraints.get("top_k"), 10) or 10)],
            "summary": {
                "materials": len(material_rows),
                "totalMaterialCarbon_kgCO2e": round(total_material, 4),
            },
            "semanticPath": "CarbonEmission -> hasCarbonDriver -> MaterialConsumption -> ofMaterial -> Material",
        }

    def _execute_process_query(self, requirement: CarbonRequirement, rows: Sequence[NormalizedCarbonRow]) -> Dict[str, Any]:
        process_rows: List[Dict[str, Any]] = []
        missing = 0
        known = 0.0
        for row in rows:
            drivers = row.process_carbon.get("drivers") or []
            if not drivers and row.process_steps:
                drivers = row.process_steps
            for driver in drivers:
                value = safe_float(
                    driver.get("carbon_value_kgco2e")
                    or driver.get("carbonValueKgCO2e")
                    or driver.get("co2e"),
                    None,
                )
                if value is not None:
                    known += value
                if str(driver.get("status", "")) != "complete":
                    missing += 1
                process_rows.append(
                    {
                        "componentGlobalId": row.component_id,
                        "componentName": row.component_name,
                        "process": driver.get("process_title") or driver.get("title") or driver.get("step") or "process evidence",
                        "driver": driver.get("driver_kind") or driver.get("driverKind") or "",
                        "status": driver.get("status", row.process_status),
                        "knownProcessCarbon_kgCO2e": value,
                        "requiredQuantity": driver.get("required_quantity") or driver.get("requiredQuantity") or "",
                        "factorRow": driver.get("preferred_factor_row_id") or driver.get("preferredFactorRowId") or "",
                    }
                )
        process_rows.sort(key=lambda item: item["knownProcessCarbon_kgCO2e"] or 0.0, reverse=True)
        return {
            "status": "executable" if process_rows else "incomplete_path",
            "perspective": "process",
            "operation": requirement.operation,
            "scope": "process_related",
            "rows": process_rows[: int(safe_float(requirement.constraints.get("top_k"), 20) or 20)],
            "summary": {
                "processDrivers": len(process_rows),
                "knownProcessCarbon_kgCO2e": round(known, 4),
                "missingProcessInputs": missing,
            },
            "semanticPath": "CarbonEmission -> hasCarbonDriver -> EnergyConsumption <- triggersDriver <- ProductionStage / ManufacturingActivity / ProductionBatch",
        }

    def _execute_traceability_query(self, requirement: CarbonRequirement, rows: Sequence[NormalizedCarbonRow]) -> Dict[str, Any]:
        trace_rows = []
        for row in rows[: int(safe_float(requirement.constraints.get("top_k"), 8) or 8)]:
            trace_rows.append(
                {
                    "componentGlobalId": row.component_id,
                    "componentName": row.component_name,
                    "ifcType": row.ifc_type,
                    "materialText": row.material_text,
                    "materialCarbon": {
                        "value_kgCO2e": row.c_mat,
                        "status": row.material_status,
                        "formula": row.material_carbon.get("formula", ""),
                        "quantityName": row.material_carbon.get("quantity_name", ""),
                        "quantityValue": row.material_carbon.get("quantity_value"),
                        "quantityUnit": row.material_carbon.get("quantity_unit", ""),
                        "factorRowId": row.material_carbon.get("factor_row_id", ""),
                        "factorValue": row.material_carbon.get("factor_value"),
                        "factorUnit": row.material_carbon.get("factor_unit", ""),
                    },
                    "selectedFactor": row.selected_factor,
                    "processCarbon": {
                        "value_kgCO2e": row.c_proc,
                        "status": row.process_status,
                        "issue": row.process_carbon.get("issue", ""),
                        "drivers": row.process_carbon.get("drivers", [])[:8],
                    },
                    "gaps": row.gaps[:8],
                    "knownTotalCarbon_kgCO2e": row.c_total,
                    "totalCarbonStatus": row.total_status,
                }
            )
        return {
            "status": "executable" if trace_rows else "empty_result",
            "perspective": "traceability",
            "operation": "trace",
            "scope": requirement.scope,
            "rows": trace_rows,
            "summary": {"tracedComponents": len(trace_rows)},
            "semanticPath": "BIM GlobalId -> quantity basis -> factor/source -> formula/calculation record -> carbon emission",
        }

    # ------------------------------------------------------------------
    # M3.3
    # ------------------------------------------------------------------
    def synthesize_answer(
        self,
        question: str,
        requirement: CarbonRequirement,
        query_result: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        messages = [
            {"role": "system", "content": M33_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": compact_json(
                    {
                        "question": question,
                        "requirement": requirement.to_dict(),
                        "query_result": query_result,
                    },
                    limit=14000,
                ),
            },
        ]
        llm_error = ""
        answer = ""
        try:
            response = self.llm.complete(messages, max_tokens=1600)
            answer = str(response.get("content", "") or "").strip()
        except Exception as exc:
            llm_error = str(exc)

        if not answer:
            answer = self._fallback_answer(requirement, query_result)

        trace = {
            "agent": "M3.3_answer_synthesizer",
            "thought": "Synthesized a natural-language answer from structured query results.",
            "action": "grounded_response_synthesis",
            "observation": {"llm_error": llm_error, "answerLength": len(answer)},
            "confidence": 0.8 if not llm_error else 0.45,
            "reflection": "Answer uses query_result only; no unsupported values are introduced.",
        }
        return answer, trace

    # ------------------------------------------------------------------
    # Grounding + normalization helpers
    # ------------------------------------------------------------------
    def _ensure_assessment_payload(self, question: str, current_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if current_payload and current_payload.get("results"):
            return current_payload
        payload = self._deterministic_runner.run(
            "Prepare graph-grounded carbon records for QA: C_mat, known C_proc, C_MM, gaps, and traceability evidence."
        )
        payload.setdefault("inputs", {}).update(
            {
                "qaAssessmentMode": "deterministic_local_precompute",
                "qaOriginalQuestion": question,
                "factoryGrid": self.factory_grid,
            }
        )
        return payload

    def _ground_requirement(
        self,
        requirement: CarbonRequirement,
        rows: Sequence[NormalizedCarbonRow],
        selected_context: Sequence[Dict[str, Any]],
    ) -> Tuple[List[NormalizedCarbonRow], Dict[str, Any]]:
        selected_ids = set(requirement.target_ids)
        for item in selected_context:
            selected_ids.update(str(item.get(key, "")) for key in ["globalId", "componentNodeId"] if item.get(key))

        specific_hints = self._specific_target_hints(requirement.target_hints)
        grounded = []
        if selected_ids:
            grounded = [row for row in rows if row.component_id in selected_ids or row.raw.get("id") in selected_ids]

        if not grounded and specific_hints:
            grounded = [row for row in rows if self._row_matches_hints(row, specific_hints, requirement.perspective)]

        if not grounded and requirement.perspective in {"product", "traceability"} and specific_hints:
            requirement.status = "unresolved_target"
        elif not grounded and selected_ids and requirement.perspective in {"product", "traceability"}:
            requirement.status = "unresolved_target"
        else:
            requirement.status = "executable"

        if requirement.perspective == "process":
            has_process_records = any(row.process_carbon or row.process_steps or row.gaps for row in (grounded or rows))
            if not has_process_records:
                requirement.status = "incomplete_path"

        trace = {
            "agent": "M3.1_grounding_gate",
            "thought": "Checked target grounding and path completeness before query execution.",
            "action": "graph_grounding_and_completeness_check",
            "observation": {
                "status": requirement.status,
                "targetIds": requirement.target_ids,
                "targetHints": requirement.target_hints,
                "groundedComponentIds": [row.component_id for row in grounded],
            },
            "confidence": 1.0 if requirement.status == "executable" else 0.3,
            "reflection": "Grounded targets are passed to M3.2; unsupported targets are not invented.",
        }
        return grounded, trace

    def _normalize_rows(self, rows: Sequence[Dict[str, Any]]) -> List[NormalizedCarbonRow]:
        return [self._normalize_row(row) for row in rows]

    @staticmethod
    def _reference_component(row: NormalizedCarbonRow) -> Dict[str, Any]:
        raw = row.raw or {}
        return {
            "id": row.component_id,
            "name": row.component_name,
            "ifcType": row.ifc_type,
            "material": row.material_text,
            "storey": raw.get("storey") or raw.get("buildingStorey") or "",
            "raw": raw,
        }

    @staticmethod
    def _reference_grounding_trace(decision: Dict[str, Any]) -> Dict[str, Any]:
        candidates = decision.get("candidates", []) or []
        return {
            "agent": "M3.1_reference_grounding_gate",
            "thought": "Resolved the user's referring expression against BIM component candidates.",
            "action": "candidate_generation_and_commit_gate",
            "observation": {
                "decision": decision.get("decision", "unresolved"),
                "reason": decision.get("reason", ""),
                "candidateCount": len(candidates),
                "committedIds": decision.get("committedIds", []),
            },
            "confidence": 1.0 if decision.get("decision") in {"not_required", "commit"} else 0.4,
            "reflection": "Carbon execution is permitted only after a unique or explicitly selected target is committed.",
        }

    def _normalize_row(self, row: Dict[str, Any]) -> NormalizedCarbonRow:
        material = row.get("materialCarbon") or (row.get("material") or {}).get("calculation") or {}
        process = row.get("processCarbon") or (row.get("process") or {}).get("calculation") or {}
        factor = row.get("selectedMaterialFactor") or (row.get("material") or {}).get("selected_factor") or {}
        gaps = row.get("gaps", {}).get("gaps", []) if isinstance(row.get("gaps"), dict) else row.get("gaps", [])
        steps = row.get("selectedProcessSteps") or row.get("processCandidates") or []
        c_mat = safe_float(material.get("value_kgco2e") or material.get("valueKgCO2e"), None)
        c_proc = safe_float(process.get("value_kgco2e") or process.get("valueKgCO2e"), None)
        c_total = safe_float(row.get("knownTotalCarbon_kgCO2e") or row.get("knownTotalCarbonKgCO2e"), None)
        if c_total is None and (c_mat is not None or c_proc is not None):
            c_total = round((c_mat or 0.0) + (c_proc or 0.0), 4)
        return NormalizedCarbonRow(
            component_id=str(row.get("componentGlobalId") or row.get("globalId") or row.get("id") or ""),
            component_name=str(row.get("componentName") or row.get("name") or row.get("componentGlobalId") or ""),
            ifc_type=str(row.get("ifcType") or row.get("type") or ""),
            material_text=str(row.get("materialText") or row.get("material") or ""),
            c_mat=c_mat,
            c_proc=c_proc,
            c_total=c_total,
            material_status=str(row.get("materialStatus") or material.get("status") or ""),
            process_status=str(row.get("processStatus") or process.get("status") or ""),
            total_status=str(row.get("totalCarbonStatus") or ""),
            selected_factor=factor or {},
            material_carbon=material or {},
            process_carbon=process or {},
            process_steps=steps if isinstance(steps, list) else [],
            gaps=gaps if isinstance(gaps, list) else [],
            confidence=safe_float(row.get("confidence"), None),
            raw=row,
        )

    @staticmethod
    def _specific_target_hints(hints: Sequence[str]) -> List[str]:
        generic = {
            "project",
            "the project",
            "overall",
            "all",
            "all components",
            "components",
            "total",
            "c_mm",
            "modular manufacturing",
            "modular manufacturing stage",
            "carbon",
            "carbon emission",
            "total carbon",
            "total c_mm",
            "material carbon",
            "known process carbon",
            "process carbon",
        }
        out = []
        for hint in hints:
            text = normalize_text(hint)
            if not text or text in generic:
                continue
            out.append(hint)
        return out

    def _candidate_snapshot(
        self,
        rows: Sequence[NormalizedCarbonRow],
        selected_context: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        components = [
            {
                "id": row.component_id,
                "name": row.component_name,
                "ifcType": row.ifc_type,
                "material": row.material_text,
                "C_mat": row.c_mat,
                "C_proc": row.c_proc,
                "C_total": row.c_total,
            }
            for row in rows[:80]
        ]
        material_names = sorted({self._material_key(row) for row in rows if self._material_key(row)})[:40]
        processes = []
        for doc in self.processes.docs[:40]:
            processes.append(
                {
                    "docId": doc.doc_id,
                    "title": doc.title,
                    "activities": doc.activities,
                    "componentTypes": doc.component_types,
                }
            )
        return {
            "components": components,
            "materials": material_names,
            "processes": processes,
            "selectedComponents": selected_context,
            "energyFactors": [
                {
                    "rowId": factor.row_id,
                    "energyType": factor.energy_type,
                    "factorValue": factor.factor_value,
                    "factorUnit": factor.factor_unit,
                }
                for factor in self.factors.energy_factors[:20]
            ],
        }

    @staticmethod
    def _row_matches_hints(row: NormalizedCarbonRow, hints: Sequence[str], perspective: str) -> bool:
        haystack_parts = [row.component_id, row.component_name, row.ifc_type, row.material_text]
        if perspective == "material":
            factor = row.selected_factor or {}
            haystack_parts.extend([factor.get("materialName", ""), factor.get("category", ""), factor.get("subtype", "")])
        if perspective == "process":
            drivers = row.process_carbon.get("drivers") or []
            for driver in drivers:
                haystack_parts.extend([driver.get("process_title", ""), driver.get("driver_kind", ""), driver.get("status", "")])
            for step in row.process_steps:
                haystack_parts.extend([step.get("title", ""), step.get("docId", ""), " ".join(step.get("activities", []) or [])])
        haystack = normalize_text(" ".join(str(part) for part in haystack_parts if part))
        return any(normalize_text(hint) and normalize_text(hint) in haystack for hint in hints)

    @staticmethod
    def _material_key(row: NormalizedCarbonRow) -> str:
        factor = row.selected_factor or {}
        category = str(factor.get("category", "") or "").strip()
        name = str(factor.get("materialName", "") or "").strip()
        return category or name or row.material_text or "unclassified material"

    @staticmethod
    def _metric_value(row: NormalizedCarbonRow, scope: str) -> float:
        if scope == "material_related":
            return float(row.c_mat or 0.0)
        if scope == "process_related":
            return float(row.c_proc or 0.0)
        return float(row.c_total or 0.0)

    @staticmethod
    def _fallback_answer(requirement: CarbonRequirement, query_result: Dict[str, Any]) -> str:
        status = query_result.get("status", requirement.status)
        if status == "clarification_required":
            count = len(query_result.get("candidates", []) or [])
            return (
                f"I found {count} BIM components matching that description. "
                "Select the intended component before I calculate its carbon emission."
            )
        if status == "unresolved_target":
            reason = query_result.get("groundingReason", "no_matching_component")
            return f"I could not ground that description to a BIM component ({reason})."
        summary = query_result.get("summary", {})
        rows = query_result.get("rows", []) or []
        if requirement.perspective == "product":
            return (
                f"Product-perspective query executed for {summary.get('components', len(rows))} component(s). "
                f"Known C_MM is {summary.get('knownTotalCarbon_kgCO2e', '-')} kgCO2e, including "
                f"{summary.get('totalMaterialCarbon_kgCO2e', '-')} kgCO2e material carbon and "
                f"{summary.get('knownProcessCarbon_kgCO2e', '-')} kgCO2e known process carbon."
            )
        if requirement.perspective == "material":
            top = rows[0] if rows else {}
            return (
                f"Material-perspective query executed for {summary.get('materials', len(rows))} material group(s). "
                f"Total material carbon is {summary.get('totalMaterialCarbon_kgCO2e', '-')} kgCO2e. "
                f"Largest returned group: {top.get('material', '-')}, {top.get('materialCarbon_kgCO2e', '-')} kgCO2e."
            )
        if requirement.perspective == "process":
            return (
                f"Process-perspective query executed for {summary.get('processDrivers', len(rows))} process driver(s). "
                f"Known process carbon is {summary.get('knownProcessCarbon_kgCO2e', '-')} kgCO2e; "
                f"missing process inputs: {summary.get('missingProcessInputs', 0)}."
            )
        return f"Traceability query returned {summary.get('tracedComponents', len(rows))} grounded component path(s)."

    @staticmethod
    def _allowed(value: Any, allowed: Iterable[str], default: str) -> str:
        text = normalize_text(value).replace("-", "_")
        return text if text in set(allowed) else default

    @staticmethod
    def _as_text_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []
