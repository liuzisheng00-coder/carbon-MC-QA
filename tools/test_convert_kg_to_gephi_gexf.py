import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from convert_kg_to_gephi_gexf import convert_graph  # noqa: E402


class ConvertKgToGephiGexfTests(unittest.TestCase):
    def test_writes_a_directed_gexf_with_node_and_edge_attributes(self):
        source = {
            "nodes": [
                {"id": "component:1", "labels": ["BuildingComponent"], "props": {"name": "Wall A"}},
                {"id": "material:1", "labels": ["Material"], "props": {"name": "Concrete"}},
                {"id": "DesignQuantity:1", "labels": ["IfcQuantityArea"], "props": {}},
            ],
            "edges": [
                {"id": "edge:1", "src": "component:1", "tgt": "material:1", "type": "hasMaterial", "props": {}},
                {"id": "edge:2", "src": "component:1", "tgt": "material:1", "type": "WAS_DERIVED_FROM", "props": {}},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.json"
            output_path = Path(temp_dir) / "graph.gexf"
            source_path.write_text(json.dumps(source), encoding="utf-8")

            convert_graph(source_path, output_path)

            root = ET.parse(output_path).getroot()

        ns = {
            "g": "http://www.gexf.net/1.2draft",
            "viz": "http://www.gexf.net/1.2draft/viz",
        }
        graph = root.find("g:graph", ns)
        self.assertEqual(graph.attrib["defaultedgetype"], "directed")
        self.assertEqual(len(root.findall(".//g:node", ns)), 3)
        self.assertEqual(len(root.findall(".//g:edge", ns)), 2)
        self.assertEqual(root.find(".//g:node[@id='component:1']", ns).attrib["label"], "Wall A")
        quantity_node = root.find(".//g:node[@id='DesignQuantity:1']", ns)
        self.assertEqual(quantity_node.attrib["label"], "DesignQuantity")
        color = quantity_node.find("viz:color", ns)
        self.assertIsNotNone(color)
        self.assertNotEqual(
            (color.attrib["r"], color.attrib["g"], color.attrib["b"]),
            ("158", "158", "158"),
        )
        self.assertEqual(root.find(".//g:edge[@id='edge:1']", ns).attrib["source"], "component:1")


if __name__ == "__main__":
    unittest.main()
