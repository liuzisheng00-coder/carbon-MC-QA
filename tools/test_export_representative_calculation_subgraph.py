import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from export_representative_calculation_subgraph import (  # noqa: E402
    REPRESENTATIVE_COMPONENT_ID,
    build_subgraph,
)


class RepresentativeCalculationSubgraphTests(unittest.TestCase):
    def test_selects_a_complete_calculation_chain_for_one_component(self):
        source_path = (
            Path(__file__).parents[1]
            / "outputs"
            / "research_experiments"
            / "m2_typea1_full_en_layerdedup_20260731_d_spread"
            / "multigranular_carbon_kg.json"
        )
        source = json.loads(source_path.read_text(encoding="utf-8"))

        subgraph = build_subgraph(source, REPRESENTATIVE_COMPONENT_ID)

        node_ids = {node["id"] for node in subgraph["nodes"]}
        node_prefixes = {node_id.split(":", 1)[0] for node_id in node_ids}
        required_prefixes = {
            "BuildingComponent",
            "CarbonEmission",
            "MaterialConsumption",
            "ConsumptionQuantity",
            "EmissionFactor",
            "EnergyConsumption",
            "ManufacturingActivity",
            "ManufacturingResource",
        }
        self.assertTrue(required_prefixes <= node_prefixes)
        self.assertEqual(
            [node_id for node_id in node_ids if node_id.startswith("BuildingComponent:")],
            [REPRESENTATIVE_COMPONENT_ID],
        )
        self.assertTrue(all(edge["src"] in node_ids and edge["tgt"] in node_ids for edge in subgraph["edges"]))


if __name__ == "__main__":
    unittest.main()
