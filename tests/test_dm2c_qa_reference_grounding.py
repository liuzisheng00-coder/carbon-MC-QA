import json
from types import SimpleNamespace

from dm2c_qa_system import DM2CQuestionAnsweringSystem


class GroundingLLM:
    def complete(self, messages, max_tokens=0):
        system = messages[0]["content"]
        if "identify carbon information requirements" in system:
            return {
                "content": json.dumps(
                    {
                        "perspective": "product",
                        "target_hints": ["steel beam"],
                        "target_ids": [],
                        "scope": "total_modularization",
                        "metric": "value",
                        "operation": "retrieve",
                        "constraints": {"top_k": 10, "unit": "kgCO2e"},
                        "response_form": "value",
                        "confidence": 0.95,
                        "reasoning": "The user refers to one steel beam.",
                        "reference_selector": {
                            "expected_cardinality": "singleton",
                            "type_terms": ["beam"],
                            "material_terms": ["steel"],
                            "required": True,
                        },
                    }
                )
            }
        raise RuntimeError("Use the deterministic fallback answer in this test.")


def two_beam_payload():
    rows = []
    for component_id, name, carbon in [
        ("beam-a", "Steel beam B1a", 120.0),
        ("beam-b", "Steel beam B3", 85.0),
    ]:
        rows.append(
            {
                "componentGlobalId": component_id,
                "componentName": name,
                "ifcType": "IfcBeam",
                "materialText": "Steel S355",
                "storey": "1F",
                "materialCarbon": {
                    "status": "calculated",
                    "value_kgco2e": carbon,
                    "quantity_name": "NetVolume",
                    "quantity_value": 0.01,
                    "quantity_unit": "m3",
                },
                "processCarbon": {"status": "complete", "value_kgco2e": 0.0, "drivers": []},
                "knownTotalCarbon_kgCO2e": carbon,
                "totalCarbonStatus": "calculated",
                "selectedMaterialFactor": {
                    "materialName": "Steel",
                    "factorValue": 2.08,
                    "factorUnit": "kgCO2e/kg",
                },
                "gaps": {"gaps": []},
            }
        )
    return {
        "results": rows,
        "summary": {
            "components": 2,
            "totalMaterialCarbon_kgCO2e": 205.0,
            "knownProcessCarbon_kgCO2e": 0.0,
            "knownTotalCarbon_kgCO2e": 205.0,
        },
        "inputs": {},
    }


def make_test_qa():
    qa = object.__new__(DM2CQuestionAnsweringSystem)
    qa.llm = GroundingLLM()
    qa.processes = SimpleNamespace(docs=[])
    qa.factors = SimpleNamespace(energy_factors=[])
    qa.factory_grid = "Guangdong"
    return qa


def test_run_stops_before_carbon_query_for_ambiguous_component():
    qa = make_test_qa()

    payload = qa.run("What is the carbon emission of the steel beam?", two_beam_payload())

    assert payload["referenceGrounding"]["decision"] == "clarify"
    assert payload["queryResult"]["status"] == "clarification_required"
    assert len(payload["queryResult"]["candidates"]) == 2
    assert "kgCO2e" not in payload["answer"]
    assert payload["reasoningTrace"][1]["agent"] == "M3.1_reference_grounding_gate"


def test_selected_candidate_commits_and_executes_existing_query():
    qa = make_test_qa()
    selected = [
        {
            "globalId": "beam-a",
            "componentNodeId": "component:beam-a",
            "componentName": "Steel beam B1a",
            "ifcType": "IfcBeam",
            "materialText": "Steel S355",
        }
    ]

    payload = qa.run(
        "What is the carbon emission of this component?",
        two_beam_payload(),
        selected,
        "selected_component",
    )

    assert payload["referenceGrounding"]["decision"] == "commit"
    assert payload["referenceGrounding"]["committedIds"] == ["beam-a"]
    assert payload["queryResult"]["status"] == "executable"
    assert payload["queryResult"]["summary"]["knownTotalCarbon_kgCO2e"] == 120.0


def test_project_total_bypasses_reference_grounding_gate():
    qa = make_test_qa()
    qa.llm = SimpleNamespace(
        complete=lambda messages, max_tokens=0: {
            "content": json.dumps(
                {
                    "perspective": "product",
                    "target_hints": ["project"],
                    "scope": "total_modularization",
                    "metric": "value",
                    "operation": "aggregate",
                    "response_form": "table",
                    "reference_selector": {
                        "expected_cardinality": "none",
                        "required": False,
                    },
                }
            )
        }
    )

    payload = qa.run("What is the project total carbon?", two_beam_payload())

    assert payload["referenceGrounding"]["decision"] == "not_required"
    assert payload["queryResult"]["status"] == "executable"
    assert payload["queryResult"]["summary"]["knownTotalCarbon_kgCO2e"] == 205.0
