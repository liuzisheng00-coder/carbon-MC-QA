import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from export_annotated_total_graph import build_annotated_graph  # noqa: E402


class AnnotatedTotalGraphTests(unittest.TestCase):
    def test_marks_and_enlarges_only_highlighted_nodes(self):
        source = {
            "nodes": [
                {"id": "component:1", "labels": ["BuildingComponent"], "props": {"name": "Wall A"}},
                {"id": "material:1", "labels": ["IfcMaterial"], "props": {"name": "Concrete"}},
            ],
            "edges": [{"id": "edge:1", "src": "component:1", "tgt": "material:1", "type": "hasMaterial", "props": {}}],
        }

        annotated = build_annotated_graph(source, {"component:1"})

        highlighted, ordinary = annotated["nodes"]
        self.assertTrue(highlighted["props"]["isCalculationSubgraph"])
        self.assertEqual(highlighted["props"]["displayLabel"], "Wall A")
        self.assertGreater(highlighted["props"]["displaySize"], ordinary["props"]["displaySize"])
        self.assertFalse(ordinary["props"]["isCalculationSubgraph"])
        self.assertEqual(ordinary["props"]["displayLabel"], "")


if __name__ == "__main__":
    unittest.main()
