import json
import tempfile
import unittest
from pathlib import Path

import trimesh

from analysis_mesh.contracts import ContractError
from analysis_mesh.loader import load_component_stream


def make_scene(path, names=("part-a", "part-b")):
    scene = trimesh.Scene()
    for i, name in enumerate(names):
        scene.add_geometry(trimesh.creation.box(), node_name=name, transform=trimesh.transformations.translation_matrix([i * 3, 0, 0]))
    path.write_bytes(trimesh.exchange.gltf.export_glb(scene))


class LoaderTest(unittest.TestCase):
    def test_nodes_map_to_global_ids_and_transform_is_applied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); make_scene(root / "model.glb")
            (root / "metadata.json").write_text(json.dumps({"items": [{"nodeName": "part-a", "GlobalId": "G1"}, {"nodeName": "part-b", "GlobalId": "G1"}]}))
            stream = load_component_stream(root / "model.glb", root / "metadata.json")
            self.assertEqual(len(stream.components), 1); self.assertEqual(len(stream.components[0].parts), 2)
            self.assertEqual(stream.tree["children"][0]["id"], "G1")
            self.assertGreater(max(p.mesh.centroid[0] for p in stream.components[0].parts), 2)
            self.assertEqual(len(stream.components[0].parts[0].position_hash), 64)

    def test_cloudbim_elements_keys_and_ifc_tree_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); make_scene(root / "model.glb", names=("G1", "G2"))
            tree = {"id": "project", "children": [{"id": "G1", "children": []}, {"id": "G2", "children": []}]}
            metadata = {"idKey": "GlobalId", "elements": {"G1": {"id": "G1", "type": "IfcWall"}, "G2": {"id": "G2", "type": "IfcSlab"}}, "tree": tree}
            (root / "metadata.json").write_text(json.dumps(metadata))
            stream = load_component_stream(root / "model.glb", root / "metadata.json")
            self.assertEqual({component.global_id for component in stream.components}, {"G1", "G2"})
            self.assertEqual(stream.tree, tree)
    def test_duplicate_tree_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); make_scene(root / "model.glb", names=("G1",))
            tree = {"id": "root", "children": [{"id": "G1", "children": []}, {"id": "G1", "children": []}]}
            (root / "metadata.json").write_text(json.dumps({"elements": {"G1": {"id": "G1"}}, "tree": tree}))
            with self.assertRaises(ContractError):
                load_component_stream(root / "model.glb", root / "metadata.json")
    def test_unmapped_geometry_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); make_scene(root / "model.glb")
            (root / "metadata.json").write_text(json.dumps({"nodeName": "part-a", "GlobalId": "G1"}))
            with self.assertRaises(ContractError): load_component_stream(root / "model.glb", root / "metadata.json")
