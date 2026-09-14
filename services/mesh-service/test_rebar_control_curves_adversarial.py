import copy
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_control_curves import _fit_piece, _project, fit_curved_pieces


RADIUS = 0.003
ARC_RADIUS = 0.019
ARC_ANGLE = np.pi / 2


def join_fixture():
    rows = {
        "a": {"id": 1, "designUnitId": "a", "status": "fitted",
              "centerlineM": [[-0.1, 0., 0.], [0., 0., 0.]],
              "designLengthM": 0.1},
        "b": {"id": 2, "designUnitId": "b", "status": "fitted",
              "centerlineM": [[0.02, 0.02, 0.], [0.02, 0.12, 0.]],
              "designLengthM": 0.1},
    }
    piece = {
        "id": "bar/join", "designBarId": "bar", "kind": "join",
        "unitIds": ["a", "b"], "radiusM": RADIUS,
        "designLengthM": 0.015 * ARC_ANGLE, "geometrySource": "ifc-analytic",
        "centerlineM": analytic_join(0.015, np.linspace(0., 1., 33))[0].tolist(),
        "primitives": [{"kind": "arc", "radiusM": 0.015}],
        "anchors": [{"designUnitId": "a", "side": "end"},
                    {"designUnitId": "b", "side": "start"}],
    }
    return piece, rows


def analytic_join(radius, fractions):
    phi = np.asarray(fractions) * ARC_ANGLE
    centers = np.column_stack((0.02-radius+radius*np.sin(phi),
                               radius*(1.-np.cos(phi)), np.zeros(len(phi))))
    tangents = np.column_stack((np.cos(phi), np.sin(phi), np.zeros(len(phi))))
    return centers, tangents


def terminal_fixture(unit_id="a", row_id=1):
    angle, radius, tail = 2.35, 0.02, 0.04
    phi = np.linspace(0., angle, 41)
    arc = np.column_stack((radius*np.sin(phi), radius*(1.-np.cos(phi)),
                           np.zeros(len(phi))))
    centerline = np.vstack((arc, arc[-1] + tail*np.array(
        [np.cos(angle), np.sin(angle), 0.])))
    piece = {
        "id": f"bar-{unit_id}/terminal", "designBarId": f"bar-{unit_id}",
        "kind": "terminal", "unitIds": [unit_id], "radiusM": RADIUS,
        "designLengthM": radius*angle+tail, "geometrySource": "ifc-analytic",
        "centerlineM": centerline.tolist(),
        "primitives": [{"kind": "arc", "radiusM": radius, "sweepRad": angle}],
        "anchors": [{"designUnitId": unit_id, "side": "start"}],
    }
    row = {"id": row_id, "designUnitId": unit_id, "status": "fitted",
           "centerlineM": [[0., 0., 0.], [-1., 0., 0.]], "designLengthM": 1.,
           "axisEvidenceRangeM": [0.08, 0.95], "pointCount": 0}
    return piece, row


def analytic_terminal(z_offset=0.):
    angle, radius, tail = 2.35, 0.02, 0.04
    lead_x = np.linspace(-0.08, 0., 45, endpoint=False)
    lead = np.column_stack((lead_x, np.zeros(len(lead_x)), np.zeros(len(lead_x))))
    lead_tangent = np.tile([1., 0., 0.], (len(lead), 1))
    phi = np.linspace(0., angle, 75, endpoint=False)
    arc = np.column_stack((radius*np.sin(phi), radius*(1.-np.cos(phi)),
                           np.full(len(phi), z_offset)))
    arc_tangent = np.column_stack((np.cos(phi), np.sin(phi), np.zeros(len(phi))))
    final = np.array([radius*np.sin(angle), radius*(1.-np.cos(angle)), z_offset])
    final_tangent = np.array([np.cos(angle), np.sin(angle), 0.])
    distance = np.linspace(0., tail, 36)
    tail_centers = final + distance[:, None]*final_tangent
    tail_tangents = np.tile(final_tangent, (len(tail_centers), 1))
    return (np.vstack((lead, arc, tail_centers)),
            np.vstack((lead_tangent, arc_tangent, tail_tangents)), len(lead))


def tube(centers, tangents, angles):
    tangents = np.asarray(tangents, float)
    side = np.cross(np.array([0., 0., 1.]), tangents)
    side /= np.linalg.norm(side, axis=1)[:, None]
    angles = np.asarray(angles, float)
    radial = (np.cos(angles)[None, :, None]*side[:, None, :]
              + np.sin(angles)[None, :, None]*np.array([0., 0., 1.]))
    return ((centers[:, None, :]+RADIUS*radial).reshape(-1, 3),
            radial.reshape(-1, 3))


def inventory_for(*unit_ids):
    return {"units": [{"designUnitId": uid, "designBarId": f"bar-{uid}",
                       "startM": [0., 0., 0.], "endM": [-1., 0., 0.]}
                      for uid in unit_ids], "bars": []}


class AdversarialParametricCurveTests(unittest.TestCase):
    def test_partial_one_sided_join_with_unoriented_normals_recovers_truth(self):
        piece, rows = join_fixture()
        # Partial refers to a single visible side of the tube; the observed
        # surface still spans the bend continuously up to the end margins.
        fractions = np.linspace(0.01, 0.97, 97)
        centers, tangents = analytic_join(ARC_RADIUS, fractions)
        points, normals = tube(centers, tangents, np.linspace(-0.15, 2.15, 13))
        normals[::2] *= -1.
        before = copy.deepcopy(rows)

        result, reason = _fit_piece(points, normals, piece, rows)

        self.assertIsNotNone(result, reason)
        self.assertLess(abs(result["meta"]["bendRadiusM"]-ARC_RADIUS), 0.00025)
        first_tangent = result["curve"][1]-result["curve"][0]
        last_tangent = result["curve"][-1]-result["curve"][-2]
        first_tangent /= np.linalg.norm(first_tangent)
        last_tangent /= np.linalg.norm(last_tangent)
        self.assertGreater(first_tangent @ np.array([1., 0., 0.]), 0.999)
        self.assertGreater(last_tangent @ np.array([0., 1., 0.]), 0.999)
        self.assertEqual(rows, before)

    def test_absent_sparse_planar_and_station_clusters_are_not_confirmed(self):
        piece, rows = join_fixture()
        centers, tangents = analytic_join(ARC_RADIUS, np.linspace(0.03, 0.77, 73))
        surface, normals = tube(centers, tangents, np.linspace(-1.1, 1.1, 12))
        planar, planar_normals = tube(centers, tangents, [0.])

        cases = {
            "absent": (surface[:0], normals[:0]),
            "sparse": (surface[:47], normals[:47]),
            "planar": (planar, planar_normals),
        }
        for label, (points, point_normals) in cases.items():
            with self.subTest(label=label):
                result, _ = _fit_piece(points, point_normals, piece, rows)
                self.assertIsNone(result)

        # Six infinitesimally narrow station clusters can populate six coarse
        # bins but do not form a continuous observed surface along the bend.
        clustered_centers, clustered_tangents = analytic_join(
            ARC_RADIUS, [0.06, 0.19, 0.31, 0.44, 0.56, 0.69])
        clustered, clustered_normals = tube(
            clustered_centers, clustered_tangents, np.linspace(-1.2, 1.2, 12))
        result, reason = _fit_piece(clustered, clustered_normals, piece, rows)
        self.assertIsNone(result, f"disconnected clusters accepted: {reason}")

    def test_shifted_neighbour_terminal_is_not_attached_to_baseline_body(self):
        piece, row = terminal_fixture()
        centers, tangents, lead_count = analytic_terminal(z_offset=0.007)
        # The established body lead remains at z=0. Only the nearby unowned
        # bend and tail surface is displaced by more than two rebar radii.
        centers[:lead_count, 2] = 0.
        points, normals = tube(centers, tangents, np.linspace(-1.2, 1.2, 16))
        status = np.full(len(points), 2, np.uint8)
        owner = np.zeros(len(points), np.uint32)
        lead_ids = np.arange(lead_count*16)
        neighbour_ids = np.arange(lead_count*16, len(points))
        status[lead_ids], owner[lead_ids] = 1, row["id"]
        source_ids = np.arange(len(points), dtype=np.int64)

        with patch("algorithms.rebar_design_curves.build_design_curve_pieces",
                   return_value=[piece]):
            reports, _ = fit_curved_pieces(
                points, normals, inventory_for("a"), [row], status, owner,
                tree=cKDTree(points[source_ids]), source_ids=source_ids)

        self.assertEqual(reports[0]["status"], "pending",
                         "shifted neighbouring steel was confirmed as the terminal")
        self.assertFalse(owner[neighbour_ids].any(),
                         "shifted neighbouring steel acquired the parent body owner")

    def test_assignment_is_additive_and_preserves_input_and_exclusions(self):
        piece, row = terminal_fixture()
        centers, tangents, _ = analytic_terminal()
        points, normals = tube(centers, tangents, np.linspace(-1.2, 1.2, 12))
        special_points = points[[120, 240, 360]].copy()
        special_normals = normals[[120, 240, 360]].copy()
        points = np.vstack((points, special_points))
        normals = np.vstack((normals, special_normals))
        status = np.full(len(points), 2, np.uint8)
        owner = np.zeros(len(points), np.uint32)
        body_ids = np.arange(0, 180)
        owner[body_ids], status[body_ids] = row["id"], 1
        table_id, excluded_id, neighbour_id = range(len(points)-3, len(points))
        status[table_id] = 0
        status[excluded_id] = 4
        owner[neighbour_id], status[neighbour_id] = 99, 1
        points_before, normals_before = points.copy(), normals.copy()
        axis_before = copy.deepcopy(row["centerlineM"])
        source_ids = np.arange(len(points), dtype=np.int64)

        with patch("algorithms.rebar_design_curves.build_design_curve_pieces",
                   return_value=[piece]):
            reports, summary = fit_curved_pieces(
                points, normals, inventory_for("a"), [row], status, owner,
                tree=cKDTree(points[source_ids]), source_ids=source_ids)

        self.assertEqual(reports[0]["status"], "fitted")
        self.assertGreater(summary["matchedPoints"], 12)
        recovered = np.asarray(reports[0]["centerlineM"])
        self.assertLess(_project(recovered, centers)[0].max(), 0.0015)
        self.assertAlmostEqual(reports[0]["fittedLengthM"], 0.08+0.02*2.35+0.04,
                               delta=0.004)
        self.assertEqual(row["centerlineM"], axis_before)
        np.testing.assert_array_equal(points, points_before)
        np.testing.assert_array_equal(normals, normals_before)
        np.testing.assert_array_equal(owner[body_ids], row["id"])
        self.assertEqual((int(status[table_id]), int(owner[table_id])), (0, 0))
        self.assertEqual((int(status[excluded_id]), int(owner[excluded_id])), (4, 0))
        self.assertEqual((int(status[neighbour_id]), int(owner[neighbour_id])), (1, 99))

    def test_competing_identical_parents_and_absent_body_stay_unowned(self):
        first, row_a = terminal_fixture("a", 1)
        second, row_b = terminal_fixture("b", 2)
        missing, _ = terminal_fixture("missing", 3)
        centers, tangents, _ = analytic_terminal()
        points, normals = tube(centers, tangents, np.linspace(-1.2, 1.2, 12))
        status = np.full(len(points), 2, np.uint8)
        owner = np.zeros(len(points), np.uint32)
        source_ids = np.arange(len(points), dtype=np.int64)

        with patch("algorithms.rebar_design_curves.build_design_curve_pieces",
                   return_value=[first, second, missing]):
            reports, _ = fit_curved_pieces(
                points, normals, inventory_for("a", "b", "missing"),
                [row_a, row_b], status, owner,
                tree=cKDTree(points[source_ids]), source_ids=source_ids)

        self.assertEqual([row["status"] for row in reports],
                         ["pending", "pending", "pending"])
        self.assertEqual(reports[0]["reason"], "insufficient-unique-curve-support")
        self.assertEqual(reports[1]["reason"], "insufficient-unique-curve-support")
        self.assertEqual(reports[2]["reason"], "body-not-supported")
        self.assertFalse(owner.any())
        self.assertTrue(np.all(status == 2))

    def test_open_end_cap_spheres_do_not_supply_side_support(self):
        curve = np.array([[0., 0., 0.], [1., 0., 0.]])
        points = np.array([[-RADIUS, 0., 0.], [1.+RADIUS, 0., 0.],
                           [0.5, RADIUS, 0.]])
        *_, side = _project(points, curve)
        np.testing.assert_array_equal(side, [False, False, True])


if __name__ == "__main__":
    unittest.main()
