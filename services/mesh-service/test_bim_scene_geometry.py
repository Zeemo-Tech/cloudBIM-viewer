from __future__ import annotations

import itertools
import json
from pathlib import Path
import tempfile
import unittest

import ifcopenshell
import numpy as np
from scipy.spatial import cKDTree
import trimesh

from bim_scene_geometry import IFC_TO_GLB, load_scene_geometry


ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / ".cloudbim/assessment-20261021/repair-20261022"
IDENTITY = np.eye(4).T.ravel().tolist()


def fixture(folder, malformed=False):
    """Independent mm box reference with nested placement and proper Z rotation."""
    model = ifcopenshell.file(schema="IFC4")
    point = lambda values: model.create_entity("IfcCartesianPoint", tuple(float(x) for x in values))
    direction = lambda values: model.create_entity("IfcDirection", tuple(float(x) for x in values))
    axis = lambda xyz, ref=(1, 0, 0): model.create_entity("IfcAxis2Placement3D", point(xyz), direction((0, 0, 1)), direction(ref))
    units = model.create_entity("IfcUnitAssignment", [model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")])
    context = model.create_entity("IfcGeometricRepresentationContext", None, "Model", 3, 1e-5, axis((0, 0, 0)), None)
    model.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), RepresentationContexts=[context], UnitsInContext=units)
    parent = model.create_entity("IfcLocalPlacement", None, axis((1000, 2000, 3000)))
    placement = model.create_entity("IfcLocalPlacement", parent, axis((200, 400, 600), (0, 1, 0)))
    profile = model.create_entity("IfcRectangleProfileDef", "AREA", None, None, 20., 50.)
    solid = model.create_entity("IfcExtrudedAreaSolid", profile, axis((0, 0, 0)), direction((0, 0, 1)), 80.)
    representation = model.create_entity("IfcShapeRepresentation", context, "Body", "SweptSolid", [solid])
    shape = model.create_entity("IfcProductDefinitionShape", None, None, [representation])
    native = np.array(list(itertools.product((-.01, .01), (-.025, .025), (0., .08))))
    expected_ifc = np.column_stack((1.2-native[:, 1], 2.4+native[:, 0], 3.6+native[:, 2]))
    expected_glb = np.column_stack((expected_ifc[:, 0], expected_ifc[:, 2], -expected_ifc[:, 1]))
    faces = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
                      [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                      [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    scene = trimesh.Scene()
    rows = []
    for kind in ["IfcPlate", "IfcMember", "IfcMechanicalFastener", "IfcDiscreteAccessory", "IfcFlowSegment", "IfcBuildingElementProxy", "IfcReinforcingBar"]:
        gid = ifcopenshell.guid.new()
        product = model.create_entity(kind, GlobalId=gid, Name="same duplicate name", ObjectPlacement=placement, Representation=shape)
        rows.append(product)
        scene.add_geometry(trimesh.Trimesh(expected_glb, faces, process=False), node_name=gid)
    failed_guid = None
    if malformed:
        # A represented object with no triangulatable body is a parse failure,
        # never a claim that its physical counterpart has no scan support.
        curve = model.create_entity("IfcPolyline", [point((0, 0, 0)), point((10, 0, 0))])
        rep = model.create_entity("IfcShapeRepresentation", context, "Body", "Curve3D", [curve])
        failed_guid = ifcopenshell.guid.new()
        model.create_entity("IfcPlate", GlobalId=failed_guid, ObjectPlacement=placement,
                            Representation=model.create_entity("IfcProductDefinitionShape", None, None, [rep]))
    ifc_path, glb_path = folder / "fixture.ifc", folder / "fixture.glb"
    model.write(str(ifc_path))
    glb_path.write_bytes(scene.export(file_type="glb"))
    return ifc_path, glb_path, expected_glb, rows, failed_guid


class SceneGeometryTests(unittest.TestCase):
    def test_nested_mm_placement_full_inverse_and_preserved_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path, glb, reference, rows, _ = fixture(Path(directory))
            # Independent Rodrigues rotation about asymmetric axis, plus translation.
            u = np.array([1., 2., 3.]); u /= np.linalg.norm(u)
            cross = np.array([[0., -u[2], u[1]], [u[2], 0., -u[0]], [-u[1], u[0], 0.]])
            matrix = np.eye(4)
            matrix[:3, :3] = np.cos(.73)*np.eye(3)+(1-np.cos(.73))*np.outer(u, u)+np.sin(.73)*cross
            matrix[:3, 3] = [5., -3., 2.]
            result = load_scene_geometry(path, glb, matrix.T.ravel().tolist())
            self.assertEqual(len(result["objects"]), 6)
            self.assertEqual(len({row["globalId"] for row in result["objects"]}), 6)
            self.assertNotIn(rows[-1].GlobalId, {row["globalId"] for row in result["objects"]})
            self.assertEqual(result["diagnostics"]["categoryCounts"], {"fixture": 3, "embedded": 1, "pipe": 1, "other": 1})
            expected = np.linalg.solve(matrix, np.c_[reference, np.ones(len(reference))].T).T[:, :3]
            for row in result["objects"]:
                self.assertLess(float(cKDTree(expected).query(row["vertices"])[0].max()), 1e-12)
                self.assertEqual(row["faces"].shape, (12, 3))
            self.assertEqual(result["diagnostics"]["ifcLengthUnitToM"], .001)
            self.assertEqual(result["diagnostics"]["ifcToGlbDeterminant"], 1.)
            self.assertTrue(result["diagnostics"]["coordinateValidation"]["passed"])
            json.dumps(result["diagnostics"])

    def test_parse_failure_is_named_separately_from_scan_support(self):
        with tempfile.TemporaryDirectory() as directory:
            path, glb, _, _, gid = fixture(Path(directory), malformed=True)
            result = load_scene_geometry(path, glb, IDENTITY)
            d = result["diagnostics"]
            self.assertEqual(d["representedNonRebarProducts"], 7)
            self.assertEqual(d["loadedObjects"], 6)
            self.assertEqual(d["parseFailureCount"], 1)
            self.assertEqual(d["errors"][0]["globalId"], gid)
            self.assertEqual(d["errors"][0]["stage"], "ifc_geometry")
            self.assertFalse(d["scanSupportAssessed"])

    def test_rejects_wrong_glb_origin_instead_of_fitting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path, glb, _, _, _ = fixture(Path(directory))
            scene = trimesh.load(str(glb), force="scene", process=False)
            scene.apply_translation([.1, 0, 0])
            glb.write_bytes(scene.export(file_type="glb"))
            with self.assertRaisesRegex(ValueError, "coordinate validation failed"):
                load_scene_geometry(path, glb, IDENTITY)

    def test_rejects_non_rigid_reflected_and_malformed_saved_transform(self):
        bad = [[0.]*16, [1.]*15, [True]*16]
        for change in ((0, 2.), (0, -1.), (3, .1), (12, float("nan"))):
            values = IDENTITY.copy(); values[change[0]] = change[1]; bad.append(values)
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                load_scene_geometry("missing.ifc", "missing.glb", values)


@unittest.skipUnless((ASSET / "夹具加钢筋预埋_钢筋中心线修复.ifc").is_file(), "repaired assessment IFC unavailable")
class RepairedAssessmentGeometryTests(unittest.TestCase):
    def test_all_422_non_rebar_objects_and_three_asymmetric_glb_references(self):
        alignment = json.loads((ASSET / "copied_alignment.json").read_text())["data"]["modelMatrix"]
        result = load_scene_geometry(ASSET / "夹具加钢筋预埋_钢筋中心线修复.ifc", ASSET / "imported_bim13.glb", alignment)
        d = result["diagnostics"]
        self.assertEqual(d["representedNonRebarProducts"], 422)
        self.assertEqual(d["loadedObjects"], 422)
        self.assertEqual(d["excludedRebarProducts"], 90)
        self.assertEqual(d["categoryCounts"], {"fixture": 392, "embedded": 22, "pipe": 8})
        self.assertEqual(d["ifcTypeCounts"], {"IfcMember": 10, "IfcPlate": 332, "IfcMechanicalFastener": 50, "IfcFlowSegment": 8, "IfcDiscreteAccessory": 22})
        self.assertEqual(d["errors"], [])
        self.assertEqual(d["coordinateValidation"]["matchedObjects"], 422)
        objects = {row["globalId"]: row for row in result["objects"]}
        matrix = np.array(alignment).reshape(4, 4).T
        for reference in json.loads((ASSET / "glb_coordinate_check.json").read_text()):
            scan = objects[reference["guid"]]["vertices"]
            world = (matrix @ np.c_[scan, np.ones(len(scan))].T).T[:, :3]
            np.testing.assert_allclose([world.min(0), world.max(0)], reference["newBounds"], atol=1e-6, rtol=0)


if __name__ == "__main__":
    unittest.main()
