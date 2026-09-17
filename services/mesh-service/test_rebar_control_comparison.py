import json
from pathlib import Path
import tempfile
import unittest

import laspy
import numpy as np
import trimesh

from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart
from rebar_control_comparison import (
    ControlNetObservedSurface, build_control_units, build_inspection,
    refresh_inspection_tolerance,
)
from rebar_spacing import spacing_rows
from rebar_comparison import compute_instance_comparison


def surface_item(unit_id, curve, radius=.01, angles=(0.,), stations=None):
    curve = np.asarray(curve, float)
    if stations is None:
        stations = np.linspace(0., np.linalg.norm(curve[-1] - curve[0]), 21)
    centers = curve[0] + np.asarray(stations)[:, None] * (
        (curve[-1] - curve[0]) / np.linalg.norm(curve[-1] - curve[0]))
    normals = np.tile([0., 1., 0.], (len(centers), 1))
    points = centers + radius * normals
    return {"designUnitId": unit_id, "kind": "body", "evidence": "fitted-control-net",
            "curve": curve, "points": points, "centers": centers,
            "tangents": np.tile([1., 0., 0.], (len(points), 1)),
            "stations": np.asarray(stations), "normals": normals,
            "radius": radius, "pointCount": len(points)}


class ControlComparisonTests(unittest.TestCase):
    def test_production_envelope_selects_v4_inspection_in_both_distance_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = trimesh.creation.cylinder(radius=.01, height=.4, sections=32)
            build_artifact(ComponentMeshStream([
                Component("A", [MeshPart("part", "part", mesh, np.eye(4), "source")])
            ], {}), root / "analysis", {"id": "test"}, face_cap=10000)
            station = np.linspace(-.18, .18, 41)
            angle = np.linspace(-1.1, 1.1, 13)
            zz, aa = np.meshgrid(station, angle, indexing='ij')
            xyz = np.column_stack((.01 * np.cos(aa.ravel()), .01 * np.sin(aa.ravel()), zz.ravel()))
            las = laspy.create(point_format=3, file_version="1.2")
            las.header.scales = np.full(3, 1e-6)
            las.add_extra_dim(laspy.ExtraBytesParams(name="cloudbim_instance_id", type=np.uint32))
            las.x, las.y, las.z = xyz.T
            las["cloudbim_instance_id"] = np.ones(len(xyz), np.uint32)
            las.write(root / "scan.las")
            unit = {"designBarId": "A", "designUnitId": "u", "startM": [0., 0., -.2],
                    "endM": [0., 0., .2], "direction": [0., 0., 1.],
                    "diameterM": .02, "kind": "straight", "layerId": 1}
            mapping = {
                "schema": "rebar-instance-map-v1",
                "inventory": {"bars": [{"designBarId": "A", "ifcGlobalId": "A"}], "units": [unit]},
                "instances": [{"id": 1, "designBarId": "A", "designUnitId": "u",
                               "reviewStatus": "matched", "pointCount": len(xyz)}],
                "controlNet": {"schema": "rebar-control-net-evidence-v1", "coordinateFrame": "scan",
                               "algorithmVersion": "design-control-net-v24",
                               "report": {"instances": [{"id": 1, "designBarId": "A", "designUnitId": "u",
                                                          "status": "fitted", "pointCount": len(xyz),
                                                          "centerlineM": [[0., 0., -.2], [0., 0., .2]]}],
                                          "curvedPieces": []}},
            }
            (root / "map.json").write_text(json.dumps(mapping))
            identity = np.eye(4).T.reshape(-1).tolist()
            for same_side in (False, True):
                result = compute_instance_comparison(
                    str(root / "scan.las"), str(root / "analysis"), str(root / "map.json"), identity,
                    voxel_size=.001, downsample_enabled=False, max_histogram_distance=.05,
                    histogram_bins=10, tolerance=.005, normal_constraint_enabled=same_side)
                comparison = result["rebarComparison"]
                self.assertEqual(comparison["algorithmVersion"], "c2m-rebar-instance-v4")
                self.assertEqual(comparison["inspection"]["schema"], "rebar-inspection-v1")
                self.assertEqual(comparison["effective"]["normalConstraintEnabled"], same_side)
                if same_side:
                    self.assertEqual(comparison["inspection"]["method"],
                                     "control-net-real-point-radial-correspondence-v1")
                else:
                    self.assertIn("unconstrained-nearest", comparison["inspection"]["method"])

    def test_same_side_uses_geometry_with_wrong_normals_and_beyond_diameter(self):
        # Observed centre is 30 mm beyond design while diameter is only 20 mm.
        item = surface_item("u", [[0., .03, 0.], [1., .03, 0.]])
        surface = ControlNetObservedSurface(
            [("u", np.array([0., 0., 0.]), np.array([1., 0., 0.]))], {"u": [item]})
        vertices = np.array([[.5, .01, 0.], [.5, -.01, 0.]])
        wrong_and_opposite = np.array([[0., -1., 0.], [0., 1., 0.]])
        values, _, known = surface.match(vertices, wrong_and_opposite, max_search_distance=.1)
        self.assertTrue(known[0])
        self.assertAlmostEqual(values[0], .03, places=7)
        # A top-only partial arc cannot manufacture a backside correspondence.
        self.assertFalse(known[1]); self.assertTrue(np.isnan(values[1]))

    def test_local_control_tangent_handles_an_acute_bend(self):
        curve = np.array([[0., 0., 0.], [.5, 0., 0.], [.55, .0866, 0.]])
        tangent = (curve[-1] - curve[-2]) / np.linalg.norm(curve[-1] - curve[-2])
        radial = np.array([-tangent[1], tangent[0], 0.])
        centers = curve[-2] + np.linspace(.1, .9, 17)[:, None] * (curve[-1] - curve[-2])
        points = centers + .01 * radial
        item = {"designUnitId": "bend", "kind": "curve", "evidence": "fitted-control-net",
                "curve": curve[1:], "points": points, "centers": centers,
                "tangents": np.tile(tangent, (len(points), 1)),
                "stations": np.linspace(.01, .09, len(points)),
                "normals": np.tile(radial, (len(points), 1)), "radius": .01,
                "pointCount": len(points)}
        design_start, design_end = curve[-2], curve[-1]
        vertex = design_start + .5 * (design_end - design_start) + .01 * radial
        surface = ControlNetObservedSurface([("bend", design_start, design_end)], {"bend": [item]})
        values, _, known = surface.match(vertex[None], np.array([[0., 0., 1.]]), max_angle_deg=20)
        self.assertTrue(known[0]); self.assertAlmostEqual(values[0], 0., places=7)

    def test_control_geometry_is_transformed_once_and_clipped_to_real_support(self):
        matrix = np.array([[0., -1., 0., 2.], [1., 0., 0., 3.],
                           [0., 0., 1., 0.], [0., 0., 0., 1.]])
        scan_curve = np.array([[0., 0., 0.], [0., 1., 0.]])
        transformed_centers = np.column_stack((2 - np.linspace(.2, .8, 31), np.full(31, 3.), np.zeros(31)))
        transformed_points = transformed_centers + [0., 0., .01]
        mapping = {
            "inventory": {"units": [{"designUnitId": "u", "diameterM": .02}]},
            "controlNet": {"schema": "rebar-control-net-evidence-v1", "coordinateFrame": "scan",
                           "algorithmVersion": "design-control-net-v24",
                           "report": {"instances": [{"id": 1, "designUnitId": "u", "status": "fitted",
                                                     "centerlineM": scan_curve.tolist(), "pointCount": 31}],
                                      "curvedPieces": []}},
        }
        units = build_control_units(mapping, matrix, {"u": transformed_points})
        self.assertEqual(len(units["u"]), 1)
        clipped = units["u"][0]["curve"]
        self.assertGreater(clipped[:, 0].min(), 1.15)
        self.assertLess(clipped[:, 0].max(), 1.85)
        np.testing.assert_allclose(clipped[:, 1], 3., atol=1e-10)

    def test_adjacent_spacing_keeps_unknown_holes_and_excludes_other_layers(self):
        units = []
        bars = {}
        evidence = {}
        for index, (unit_id, y, layer) in enumerate((('a', 0., 1), ('b', .1, 1), ('c', .25, 1), ('x', .05, 2))):
            bar_id = f"bar-{unit_id}"
            units.append({"designUnitId": unit_id, "designBarId": bar_id,
                          "startM": [0., y, 0.], "endM": [1., y, 0.],
                          "direction": [1., 0., 0.], "diameterM": .02,
                          "kind": "straight", "layerId": layer, "familyId": "main"})
            bars[bar_id] = {"ifcGlobalId": f"guid-{unit_id}"}
        evidence['a'] = [surface_item('a', [[0., 0., 0.], [1., 0., 0.]])]
        # Two disjoint supported segments leave the intended middle station unknown.
        evidence['b'] = [surface_item('b', [[0., .11, 0.], [.4, .11, 0.]]),
                         surface_item('b', [[.6, .11, 0.], [1., .11, 0.]])]
        # c is unmatched; it must remain unavailable rather than borrowing b/a.
        rows, unavailable = spacing_rows({"units": units}, evidence, bars, .005)
        self.assertEqual(len(rows), 2)
        ab, bc = rows
        self.assertEqual(ab["designUnitIds"], ['a', 'b'])
        self.assertEqual(ab["coverage"]["status"], "partial")
        self.assertEqual(ab["samples"][4]["status"], "unknown")
        self.assertIsNone(ab["samples"][4]["signedDifferenceM"])
        self.assertLess(ab["coverage"]["supportedSpanM"], ab["coverage"]["sharedSpanM"])
        self.assertAlmostEqual(ab["signedDifferenceM"], .01, places=7)
        self.assertEqual(bc["coverage"]["status"], "unavailable")
        self.assertTrue(all(sample["status"] == "unknown" for sample in bc["samples"]))
        self.assertNotIn('x', sum((row["designUnitIds"] for row in rows), []))
        self.assertEqual(unavailable, {})

    def test_dislocated_short_bar_reports_directional_spacing_without_diameter_claim(self):
        inventory = {"units": [
            {"designUnitId": "a", "designBarId": "A", "startM": [0., 0., 0.], "endM": [.2, 0., 0.],
             "direction": [1., 0., 0.], "diameterM": .016, "kind": "short", "layerId": 1},
            {"designUnitId": "b", "designBarId": "B", "startM": [0., .08, 0.], "endM": [.2, .08, 0.],
             "direction": [1., 0., 0.], "diameterM": .016, "kind": "short", "layerId": 1},
        ]}
        evidence = {'a': [surface_item('a', [[0., 0., 0.], [.2, 0., 0.]], .008)],
                    'b': [surface_item('b', [[0., .115, 0.], [.2, .115, 0.]], .008)]}
        rows, _ = spacing_rows(inventory, evidence,
                               {'A': {'ifcGlobalId': 'ga'}, 'B': {'ifcGlobalId': 'gb'}}, .005)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["signedDifferenceM"], .035, places=7)
        self.assertAlmostEqual(rows[0]["netClearanceM"], .099, places=7)
        self.assertEqual(rows[0]["radiusSource"], "design-prior")
        self.assertFalse(rows[0]["withinTolerance"])

    def test_inspection_is_json_safe_and_recolor_updates_derived_tolerance(self):
        mapping = {"inventory": {"units": [{"designUnitId": "u", "designBarId": "A"}]},
                   "controlNet": {"schema": "rebar-control-net-evidence-v1", "coordinateFrame": "scan",
                                  "algorithmVersion": "design-control-net-v24", "report": {}}}
        comparison = [{"designBarId": "A", "ifcGlobalId": "g", "status": "matched",
                       "knownCount": 2, "unknownCount": 1,
                       "stats": {"withinToleranceRatio": .5}}]
        item = surface_item('u', [[0., 0., 0.], [1., 0., 0.]])
        spacing = [{"toleranceM": .005, "signedDifferenceM": .008, "withinTolerance": False,
                    "samples": [{"signedDifferenceM": .003, "withinTolerance": True}],
                    "coverage": {"status": "supported"}}]
        report = build_inspection(mapping, '0' * 64, np.eye(4).T.reshape(-1).tolist(),
                                  comparison, {'u': [item]}, spacing, {}, .005,
                                  same_side_surface=True)
        json.dumps(report, allow_nan=False)
        refresh_inspection_tolerance(report, .01)
        self.assertTrue(report["spacing"][0]["withinTolerance"])
        self.assertTrue(report["spacing"][0]["samples"][0]["withinTolerance"])
        self.assertEqual(report["summary"]["toleranceM"], .01)


if __name__ == '__main__':
    unittest.main()
