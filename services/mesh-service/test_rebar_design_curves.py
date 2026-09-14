from __future__ import annotations

from copy import deepcopy
import math
import unittest
from unittest.mock import patch

import ifcopenshell
import numpy as np

from algorithms.rebar_design_curves import build_design_curve_pieces, transform_primitives
from rebar_bim import _curve_primitives
from rebar_design_prior import extract_model, inventory_from_bars


IDENTITY = np.eye(4).ravel(order="F").tolist()


def line(start, end):
    return {"kind": "line", "startM": list(start), "endM": list(end)}


def quarter_arc():
    return {"kind": "arc", "startM": [1., 0., 0.], "endM": [2., 1., 0.],
            "centerM": [1., 1., 0.], "normal": [0., 0., 1.],
            "radiusM": 1., "sweepRad": math.pi/2}


class PrimitiveTransformTests(unittest.TestCase):
    def test_rigid_transform_preserves_dimensions_direction_and_input(self):
        primitives = [quarter_arc()]
        original = deepcopy(primitives)
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        transformed = transform_primitives(primitives, rotation, [3., 4., 5.])
        self.assertEqual(primitives, original)
        np.testing.assert_allclose(transformed[0]["startM"], [3., 5., 5.])
        np.testing.assert_allclose(transformed[0]["centerM"], [2., 5., 5.])
        np.testing.assert_allclose(transformed[0]["normal"], [0., 0., 1.])
        self.assertEqual(transformed[0]["radiusM"], 1.)
        self.assertEqual(transformed[0]["sweepRad"], math.pi/2)

    def test_inventory_registration_transforms_primitives_without_changing_units(self):
        source = {"designBarId": "bar", "points": [[0., 0., 0.], [1., 0., 0.]],
                  "curvePrimitives": [line([0., 0., 0.], [1., 0., 0.])],
                  "radiusM": .004, "coverage": "complete", "source": "ifc-analytic"}
        baseline = inventory_from_bars([source], IDENTITY)
        transform = np.eye(4)
        transform[:3, 3] = [4., 5., 6.]
        moved = deepcopy(source)
        moved["points"] = (np.asarray(moved["points"])+[4., 5., 6.]).tolist()
        moved["curvePrimitives"] = transform_primitives(
            moved["curvePrimitives"], np.eye(3), [4., 5., 6.])
        aligned = inventory_from_bars([moved], transform.ravel(order="F").tolist())
        self.assertEqual(aligned["units"], baseline["units"])
        self.assertEqual(aligned["bars"][0]["unitIds"], baseline["bars"][0]["unitIds"])
        np.testing.assert_allclose(aligned["bars"][0]["curvePrimitives"][0]["startM"], [0., 0., 0.])


class IfcPrimitiveTests(unittest.TestCase):
    def test_fresh_model_extraction_carries_curve_primitives(self):
        model = ifcopenshell.file(schema="IFC4")
        point = lambda value: model.create_entity("IfcCartesianPoint", tuple(float(x) for x in value))
        placement = model.create_entity("IfcAxis2Placement3D", point([0., 0., 0.]), None, None)
        context = model.create_entity("IfcGeometricRepresentationContext", None, "Model", 3, 1e-5, placement, None)
        local = model.create_entity("IfcLocalPlacement", None, placement)
        circle = model.create_entity("IfcCircle", placement, 1.)
        zero = model.create_entity("IfcParameterValue", 0.)
        quarter = model.create_entity("IfcParameterValue", math.pi/2)
        arc = model.create_entity("IfcTrimmedCurve", circle, (zero,), (quarter,), True, "PARAMETER")
        solid = model.create_entity("IfcSweptDiskSolid", arc, .004, None, None, None)
        shape = model.create_entity("IfcShapeRepresentation", context, "Body", "SweptSolid", (solid,))
        definition = model.create_entity("IfcProductDefinitionShape", None, None, (shape,))
        model.create_entity("IfcReinforcingBar", GlobalId=ifcopenshell.guid.new(),
                            ObjectPlacement=local, Representation=definition,
                            NominalDiameter=.008, CrossSectionArea=5e-5, BarLength=math.pi/2)
        with patch("ifcopenshell.open", return_value=model):
            result = extract_model("unused.ifc")
        self.assertEqual(result["version"], "design-inventory-v4")
        self.assertEqual(result["bars"][0]["curvePrimitives"][0]["kind"], "arc")
        self.assertAlmostEqual(result["bars"][0]["curvePrimitives"][0]["radiusM"], 1.)

    def test_trimmed_circle_and_composite_reversal_are_exact(self):
        model = ifcopenshell.file(schema="IFC4")
        point = lambda value: model.create_entity("IfcCartesianPoint", tuple(float(x) for x in value))
        placement = model.create_entity("IfcAxis2Placement2D", point([0., 0.]), None)
        circle = model.create_entity("IfcCircle", placement, 2.)
        zero = model.create_entity("IfcParameterValue", 0.)
        quarter = model.create_entity("IfcParameterValue", math.pi/2)
        arc = model.create_entity("IfcTrimmedCurve", circle, (zero,), (quarter,), True, "PARAMETER")
        forward = _curve_primitives(arc)
        self.assertEqual(len(forward), 1)
        self.assertAlmostEqual(forward[0]["radiusM"], 2.)
        self.assertAlmostEqual(forward[0]["sweepRad"], math.pi/2)
        segment = model.create_entity("IfcCompositeCurveSegment", "CONTINUOUS", False, arc)
        composite = model.create_entity("IfcCompositeCurve", (segment,), False)
        reverse = _curve_primitives(composite)
        np.testing.assert_allclose(reverse[0]["startM"], forward[0]["endM"])
        np.testing.assert_allclose(reverse[0]["endM"], forward[0]["startM"])
        self.assertAlmostEqual(reverse[0]["sweepRad"], -math.pi/2)

    def test_indexed_arc_is_not_replaced_by_lines(self):
        model = ifcopenshell.file(schema="IFC4")
        points = model.create_entity("IfcCartesianPointList3D",
                                     ((0., 1., 0.), (1., 0., 0.), (0., -1., 0.)))
        index = model.create_entity("IfcArcIndex", (1, 2, 3))
        curve = model.create_entity("IfcIndexedPolyCurve", points, (index,), False)
        result = _curve_primitives(curve)
        self.assertEqual([row["kind"] for row in result], ["arc"])
        self.assertAlmostEqual(result[0]["radiusM"], 1.)
        self.assertAlmostEqual(abs(result[0]["sweepRad"]), math.pi)


class DesignCurvePieceTests(unittest.TestCase):
    def inventory(self, *, reverse=False, exact=True):
        primitives = [line([0., 0., 0.], [1., 0., 0.]), quarter_arc(),
                      line([2., 1., 0.], [3., 1., 0.])]
        sampled = [[0., 0., 0.], [1., 0., 0.], [1.292893218, .292893218, 0.],
                   [2., 1., 0.], [3., 1., 0.]]
        first = ([1., 0., 0.], [0., 0., 0.]) if reverse else ([0., 0., 0.], [1., 0., 0.])
        second = ([3., 1., 0.], [2., 1., 0.]) if reverse else ([2., 1., 0.], [3., 1., 0.])
        bar = {"designBarId": "bar", "radiusM": .004, "points": sampled}
        if exact:
            bar["curvePrimitives"] = primitives
        return {"bars": [bar], "units": [
            {"designBarId": "bar", "designUnitId": "u0", "startM": first[0], "endM": first[1], "diameterM": .008},
            {"designBarId": "bar", "designUnitId": "u1", "startM": second[0], "endM": second[1], "diameterM": .008},
        ]}

    def test_one_exact_join_has_arc_length_and_requested_chord_error(self):
        pieces = build_design_curve_pieces(self.inventory())
        self.assertEqual(len(pieces), 1)
        piece = pieces[0]
        self.assertEqual(piece["kind"], "join")
        self.assertEqual(piece["unitIds"], ["u0", "u1"])
        self.assertEqual(piece["geometrySource"], "ifc-analytic")
        self.assertEqual([row["kind"] for row in piece["primitives"]], ["arc"])
        self.assertAlmostEqual(piece["designLengthM"], math.pi/2, places=12)
        points = np.asarray(piece["centerlineM"])
        chord_midpoints = (points[:-1]+points[1:])/2
        sagitta = 1.-np.linalg.norm(chord_midpoints-np.array([1., 1., 0.]), axis=1)
        self.assertLessEqual(float(np.max(sagitta)), .0001+1e-12)

    def test_reversed_unit_orientation_changes_anchor_sides_not_parent_order(self):
        piece = build_design_curve_pieces(self.inventory(reverse=True))[0]
        self.assertEqual(piece["unitIds"], ["u0", "u1"])
        self.assertEqual(piece["anchors"], [
            {"designUnitId": "u0", "side": "start"},
            {"designUnitId": "u1", "side": "end"},
        ])
        np.testing.assert_allclose(piece["centerlineM"][0], [1., 0., 0.])
        np.testing.assert_allclose(piece["centerlineM"][-1], [2., 1., 0.])

    def test_sampled_snapshot_fallback_is_explicit_and_has_no_exact_primitives(self):
        piece = build_design_curve_pieces(self.inventory(exact=False))[0]
        self.assertEqual(piece["geometrySource"], "sampled-design")
        self.assertEqual(piece["primitives"], [])
        expected = math.sqrt(.292893218**2*2) + math.sqrt(.707106782**2*2)
        self.assertAlmostEqual(piece["designLengthM"], expected)

    def test_terminals_and_two_joins_cover_each_outside_interval_once(self):
        bar = {"designBarId": "b", "radiusM": .005, "points": [[0., 0., 0.], [4., 0., 0.]],
               "curvePrimitives": [line([0., 0., 0.], [4., 0., 0.])]}
        units = []
        for uid, start, end in [("a", .5, 1.), ("b", 1.5, 2.), ("c", 2.5, 3.5)]:
            units.append({"designBarId": "b", "designUnitId": uid,
                          "startM": [start, 0., 0.], "endM": [end, 0., 0.], "diameterM": .01})
        pieces = build_design_curve_pieces({"bars": [bar], "units": units})
        self.assertEqual([row["kind"] for row in pieces], ["terminal", "join", "join", "terminal"])
        self.assertEqual([row["unitIds"] for row in pieces], [["a"], ["a", "b"], ["b", "c"], ["c"]])
        self.assertAlmostEqual(sum(row["designLengthM"] for row in pieces), 2.)
        self.assertEqual(len({row["id"] for row in pieces}), 4)

    def test_invalid_dimensions_do_not_emit_a_piece(self):
        inventory = self.inventory()
        inventory["bars"][0]["radiusM"] = float("nan")
        for unit in inventory["units"]:
            unit.pop("diameterM")
        self.assertEqual(build_design_curve_pieces(inventory), [])


if __name__ == "__main__":
    unittest.main()
