from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import ifcopenshell

from rebar_bim import SCHEMA, _curve_points, _walk_items, _extruded_points, load_bim_prior


ROOT = Path(__file__).resolve().parents[2]
IFC = ROOT / "backend/data/uploads/419278dd32c37508da3af8bd/source"
GLB = ROOT / "backend/data/assets/45d9b4149bf0870e75ccce38/model.glb"
METADATA = ROOT / "backend/data/assets/45d9b4149bf0870e75ccce38/metadata.json"
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


@unittest.skipUnless(IFC.is_file() and GLB.is_file() and METADATA.is_file(), "checked-in example BIM asset unavailable")
class RebarBimPriorAssetTests(unittest.TestCase):
    def test_actual_ifc_preserves_mapped_circle_extrusions_in_meters(self):
        result = load_bim_prior(ifc_path=str(IFC), model_path=str(GLB), metadata_path=str(METADATA), scan_to_bim=IDENTITY)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertTrue(result["diagnostics"]["ifcUsed"])
        # The IFC has 50 circle profiles; 38 of them are occurrence-specific
        # mapped rods on one IFC product, so IDs must not collapse by class.
        self.assertEqual(result["diagnostics"]["ifcBarCount"], 50)
        self.assertEqual(len({bar["id"] for bar in result["bars"]}), len(result["bars"]))
        ifc_products = {bar["ifcGlobalId"] for bar in result["bars"] if bar["source"] == "ifc"}
        self.assertTrue(all(bar["ifcGlobalId"] not in ifc_products for bar in result["bars"] if bar["source"] == "glb"))
        first = result["bars"][0]
        self.assertEqual(first["source"], "ifc")
        self.assertAlmostEqual(first["radius"], 0.004, places=6)
        self.assertAlmostEqual(abs(first["points"][1][2] - first["points"][0][2]), 1.15, places=5)
        # IFC x,y,z is reconciled to the imported GLB x,z,-y frame.
        self.assertAlmostEqual(first["points"][0][1], 0.019, places=5)
        self.assertAlmostEqual(first["points"][0][2], 2.3949875, places=5)

    def test_inverse_column_major_scan_to_bim_transform_returns_raw_scan_points(self):
        translated = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 2, 3, 1]
        base = load_bim_prior(ifc_path=str(IFC), model_path=None, metadata_path=None, scan_to_bim=IDENTITY)["bars"][0]
        shifted = load_bim_prior(ifc_path=str(IFC), model_path=None, metadata_path=None, scan_to_bim=translated)["bars"][0]
        self.assertEqual(shifted["id"], base["id"])
        self.assertEqual(shifted["points"][0], [base["points"][0][0] - 1, base["points"][0][1] - 2, base["points"][0][2] - 3])

    def test_glb_only_fallback_is_conservative_and_identified(self):
        result = load_bim_prior(ifc_path=None, model_path=str(GLB), metadata_path=str(METADATA), scan_to_bim=IDENTITY)
        self.assertTrue(result["diagnostics"]["fallbackUsed"])
        self.assertFalse(result["diagnostics"]["ifcUsed"])
        self.assertGreaterEqual(len(result["bars"]), 38)
        self.assertTrue(all(bar["source"] == "glb" for bar in result["bars"]))
        self.assertGreater(result["diagnostics"]["glbUnsupportedComponents"], 0)

    def test_rejects_malformed_or_noninvertible_transform(self):
        with self.assertRaises(ValueError):
            load_bim_prior(ifc_path=None, model_path=None, metadata_path=None, scan_to_bim=[1] * 15)
        with self.assertRaises(ValueError):
            load_bim_prior(ifc_path=None, model_path=None, metadata_path=None, scan_to_bim=[0] * 16)


class RebarBimPriorPortableTests(unittest.TestCase):
    def test_mm_profile_solid_and_product_placements_compose_without_rotating_extrusion(self):
        model=ifcopenshell.file(schema='IFC4')
        point=lambda coords:model.create_entity('IfcCartesianPoint',tuple(float(x) for x in coords))
        direction=lambda coords:model.create_entity('IfcDirection',tuple(float(x) for x in coords))
        profile_position=model.create_entity('IfcAxis2Placement2D',point([20,30]),direction([0,1]))
        profile=model.create_entity('IfcCircleProfileDef','AREA',None,profile_position,4.)
        solid_position=model.create_entity('IfcAxis2Placement3D',point([100,200,300]),direction([0,0,1]),direction([1,0,0]))
        solid=model.create_entity('IfcExtrudedAreaSolid',profile,solid_position,direction([1,0,1]),1000.)
        placement=np.eye(4); placement[:3,3]=[1,2,3]
        points,radius=_extruded_points(solid,placement,.001)
        np.testing.assert_allclose(points[0],[1.12,2.23,3.3])
        np.testing.assert_allclose(points[1]-points[0],np.array([1,0,1])/np.sqrt(2))
        self.assertEqual(radius,.004)

    def test_two_dimensional_cartesian_circle_trim_is_promoted_to_3d(self):
        model=ifcopenshell.file(schema='IFC4')
        origin=model.create_entity('IfcCartesianPoint',(0.,0.))
        position=model.create_entity('IfcAxis2Placement2D',origin,None)
        circle=model.create_entity('IfcCircle',position,1.)
        start=model.create_entity('IfcCartesianPoint',(1.,0.))
        end=model.create_entity('IfcCartesianPoint',(0.,1.))
        curve=model.create_entity('IfcTrimmedCurve',circle,(start,),(end,),True,'CARTESIAN')
        points=np.asarray(_curve_points(curve))
        np.testing.assert_allclose(points[[0,-1]],[[1,0,0],[0,1,0]],atol=1e-8)
    def test_indexed_arc_contains_midpoint_and_respects_circle(self):
        model = ifcopenshell.file(schema="IFC4")
        points = model.create_entity("IfcCartesianPointList3D", ((0., 1., 0.), (1., 0., 0.), (0., -1., 0.)))
        arc = model.create_entity("IfcArcIndex", (1, 2, 3))
        curve = model.create_entity("IfcIndexedPolyCurve", points, (arc,), False)
        # IFC's select wrapper currently exposes wrappedValue; the production
        # helper must support the actual API, rather than a mock-only attribute.
        self.assertGreater(len(_curve_points(curve)), 2)
        sampled = np.asarray(_curve_points(curve))
        self.assertTrue(np.allclose(np.linalg.norm(sampled[:, :2], axis=1), 1, atol=1e-6))
        self.assertLess(np.min(np.linalg.norm(sampled - np.array([1., 0., 0.]), axis=1)), 1e-6)

    def test_walk_items_keeps_nested_repeated_occurrence_paths_distinct(self):
        class Item:
            def __init__(self, name, entity_id, mapped=None): self.name, self.entity_id, self.mapped = name, entity_id, mapped
            def is_a(self, name): return name == "IfcMappedItem" and self.mapped is not None
            def id(self): return self.entity_id
            @property
            def MappingSource(self): return type("Source", (), {"MappedRepresentation": type("Rep", (), {"Items": self.mapped})})()
        source = Item("source", 99)
        nested = Item("nested", 20, [source])
        left, right = Item("left", 10, [nested]), Item("right", 11, [nested])
        transforms = {10: np.eye(4), 11: np.eye(4), 20: np.eye(4)}
        with patch("ifcopenshell.util.placement.get_mappeditem_transformation", side_effect=lambda x: transforms[x.id()]):
            rows = list(_walk_items([left, right], np.eye(4), 1.0, "representation0"))
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][2], rows[1][2])
        self.assertIn("item0:10", rows[0][2]); self.assertIn("item1:11", rows[1][2])

    def test_trimmed_circle_and_composite_respect_sense(self):
        model = ifcopenshell.file(schema="IFC4")
        origin = model.create_entity("IfcCartesianPoint", (0., 0.))
        placement = model.create_entity("IfcAxis2Placement2D", origin, model.create_entity("IfcDirection", (1., 0.)))
        circle = model.create_entity("IfcCircle", placement, 1.)
        zero = model.create_entity("IfcParameterValue", 0.)
        quarter = model.create_entity("IfcParameterValue", np.pi / 2)
        forward = model.create_entity("IfcTrimmedCurve", circle, (zero,), (quarter,), True, "PARAMETER")
        reverse = model.create_entity("IfcTrimmedCurve", circle, (zero,), (quarter,), False, "PARAMETER")
        a, b = np.asarray(_curve_points(forward)), np.asarray(_curve_points(reverse))
        self.assertTrue(np.allclose(a[0], [1, 0, 0])); self.assertTrue(np.allclose(a[-1], [0, 1, 0], atol=1e-6))
        self.assertTrue(np.allclose(b[0], [1, 0, 0])); self.assertTrue(np.allclose(b[-1], [0, 1, 0], atol=1e-6))
        self.assertGreater(len(b), len(a))  # reversed sense chooses the major arc

    def test_rejects_boolean_scaled_and_non_affine_transforms(self):
        with self.assertRaises(ValueError):
            load_bim_prior(ifc_path=None, model_path=None, metadata_path=None, scan_to_bim=[True] + [0] * 15)
        with self.assertRaises(ValueError):
            load_bim_prior(ifc_path=None, model_path=None, metadata_path=None, scan_to_bim=[2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        with self.assertRaises(ValueError):
            load_bim_prior(ifc_path=None, model_path=None, metadata_path=None, scan_to_bim=[1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])


if __name__ == "__main__":
    unittest.main()
