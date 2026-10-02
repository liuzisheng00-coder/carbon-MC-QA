import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from convert_kg_to_gephi_lite import convert_graph  # noqa: E402


class ConvertKgToGephiLiteTests(unittest.TestCase):
    def test_converts_dm2c_ids_and_edges_to_graphology_keys(self):
        source = {
            "nodes": [
                {
                    "id": "component:1",
                    "labels": ["AttributionObject", "BuildingComponent"],
                    "props": {"name": "Wall A", "granularityLevel": "component"},
                },
                {"id": "material:1", "labels": ["Material"], "props": {"name": "Concrete"}},
            ],
            "edges": [
                {
                    "id": "edge:1",
                    "src": "component:1",
                    "tgt": "material:1",
                    "type": "hasMaterial",
                    "props": {"confidence": 1.0},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.json"
            output_path = Path(temp_dir) / "gephi-lite.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")

            convert_graph(source_path, output_path)

            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["attributes"]["name"], "DM2C multigranular carbon knowledge graph")
        self.assertEqual(
            result["options"],
            {"type": "directed", "multi": True, "allowSelfLoops": True},
        )
        self.assertEqual(result["nodes"][0]["key"], "component:1")
        self.assertEqual(result["nodes"][0]["attributes"]["labels"], ["AttributionObject", "BuildingComponent"])
        self.assertEqual(result["nodes"][0]["attributes"]["name"], "Wall A")
        self.assertEqual(result["edges"][0]["key"], "edge:1")
        self.assertEqual(result["edges"][0]["source"], "component:1")
        self.assertEqual(result["edges"][0]["target"], "material:1")
        self.assertEqual(result["edges"][0]["attributes"]["type"], "hasMaterial")


if __name__ == "__main__":
    unittest.main()
