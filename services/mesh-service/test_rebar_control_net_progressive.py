import json
import unittest

import numpy as np

from algorithms.rebar_control_net import fit_control_net
from test_rebar_control_net import inventory, tube


class ProgressiveControlNetTests(unittest.TestCase):
    def test_parallel_stages_and_recovery_match_serial_ownership(self):
        lines = [([0, 0, 0], [.6, 0, 0]), ([0, .15, 0], [.6, .15, 0]),
                 ([0, .4, 0], [.28, .4, 0]), ([0, 1.4, 0], [.28, 1.4, 0]),
                 ([0, 2.1, 0], [.12, 2.1, 0]), ([0, 2.3, 0], [.12, 2.3, 0])]
        observed = list(lines)
        observed[2] = ([0, .55, 0], [.37, .55, 0])
        observed[3] = ([0, 1.55, 0], [.36, 1.55, 0])
        clouds, normal_sets = zip(*(tube(a, b, along=90) for a, b in observed))
        points, normals = np.vstack(clouds), np.vstack(normal_sets)
        inv = inventory(lines, kinds=['straight', 'straight', 'short', 'short', 'web', 'web'])
        serial, a = fit_control_net(points, np.zeros(len(points), bool), inv, normals=normals)
        parallel, b = fit_control_net(points, np.zeros(len(points), bool), inv, normals=normals, workers=3)
        self.assertEqual(serial['counts']['fittedUnits'], 6)
        self.assertEqual(serial['counts']['extendedShortUnits'], 2)
        self.assertEqual(serial['instances'], parallel['instances'])
        for key in a:
            np.testing.assert_array_equal(a[key], b[key])

    def fit(self, point_sets, normal_sets, lines, kinds):
        points = np.vstack(point_sets)
        normals = np.vstack(normal_sets)
        report, attrs = fit_control_net(
            points,
            np.zeros(len(points), dtype=bool),
            inventory(lines, kinds=kinds),
            mode="aligned",
            normals=normals,
        )
        json.dumps(report, allow_nan=False)
        return report, attrs, [len(part) for part in point_sets]

    def assert_design_length_unchanged(self, report, expected):
        self.assertAlmostEqual(report["inventory"]["units"][0]["lengthM"], expected)
        self.assertAlmostEqual(report["instances"][0]["designLengthM"], expected)

    def test_continuous_short_surface_extends_028_design_to_037_observed(self):
        observed, normals = tube([-.02, 0, 0], [.35, 0, 0], along=120, around=18)

        report, attrs, _ = self.fit(
            [observed], [normals], [([0, 0, 0], [.28, 0, 0])], ["short"]
        )

        row = report["instances"][0]
        self.assertEqual(report["version"], "design-control-net-v21")
        self.assertEqual(row["status"], "fitted")
        self.assertEqual(row["lengthCheck"], "extended-observed-span")
        self.assertEqual(row["axisModel"], "evidence-extended-straight-cylinder")
        self.assertAlmostEqual(row["fittedLengthM"], .37, delta=.006)
        self.assertAlmostEqual(row["observedLengthM"], .37, delta=.006)
        self.assertGreater(row["lengthEvidence"]["extensionStartM"], .012)
        self.assertGreater(row["lengthEvidence"]["extensionEndM"], .06)
        self.assertGreater(row["lengthEvidence"]["supportPoints"], 100)
        self.assertEqual(report["counts"]["extendedShortUnits"], 1)
        self.assertGreater(np.mean(attrs["control_instance"] == 1), .95)
        self.assert_design_length_unchanged(report, .28)

    def test_rotated_asymmetric_short_surface_extends_both_supported_ends(self):
        angle = np.deg2rad(23.)
        direction = np.array([np.cos(angle), np.sin(angle), 0.])
        # The visible interval is rotated and its midpoint is 26 mm beyond the
        # design midpoint along the observed tangent. Both ends remain within
        # the bounded initial fit while only continuous tube surface may move them.
        centre = np.array([.14, 0., .025]) + .026*direction
        start = centre-.17*direction
        end = centre+.20*direction
        observed, normals = tube(start, end, along=126, around=18)

        report, attrs, _ = self.fit(
            [observed], [normals], [([0, 0, .025], [.28, 0, .025])], ["short"]
        )

        row = report["instances"][0]
        self.assertEqual(row["status"], "fitted")
        self.assertEqual(row["lengthCheck"], "extended-observed-span")
        self.assertAlmostEqual(row["fittedLengthM"], .37, delta=.008)
        self.assertGreater(row["directionDifferenceDeg"], 18.)
        curve = np.asarray(row["centerlineM"])
        fitted_direction = curve[-1]-curve[0]
        fitted_direction /= np.linalg.norm(fitted_direction)
        self.assertGreater(abs(float(fitted_direction@direction)), .995)
        endpoint_error = min(
            np.linalg.norm(curve[0]-start)+np.linalg.norm(curve[-1]-end),
            np.linalg.norm(curve[-1]-start)+np.linalg.norm(curve[0]-end),
        )
        self.assertLess(endpoint_error, .014)
        self.assertGreater(np.mean(attrs["control_instance"] == 1), .94)
        self.assert_design_length_unchanged(report, .28)

    def test_disconnected_dense_tail_and_sparse_speckles_do_not_extend(self):
        body, body_normals = tube([0, 0, 0], [.28, 0, 0], along=96, around=18)
        tail, tail_normals = tube([.315, 0, 0], [.37, 0, 0], along=25, around=18)
        speckles, speckle_normals = tube([.285, 0, 0], [.41, 0, 0], along=15, around=1)

        report, _, _ = self.fit(
            [body, tail, speckles],
            [body_normals, tail_normals, speckle_normals],
            [([0, 0, 0], [.28, 0, 0])],
            ["short"],
        )

        row = report["instances"][0]
        self.assertEqual(row["status"], "fitted")
        self.assertNotEqual(row.get("lengthCheck"), "extended-observed-span")
        self.assertAlmostEqual(row.get("fittedLengthM", .28), .28, delta=.003)
        self.assertEqual(report["counts"]["extendedShortUnits"], 0)

    def test_perpendicular_crossing_at_end_does_not_extend_short_axis(self):
        body, body_normals = tube([0, 0, 0], [.28, 0, 0], along=96, around=18)
        crossing, crossing_normals = tube([.305, -.10, 0], [.305, .10, 0], along=82, around=18)

        report, _, _ = self.fit(
            [body, crossing], [body_normals, crossing_normals],
            [([0, 0, 0], [.28, 0, 0])], ["short"]
        )

        row = report["instances"][0]
        self.assertEqual(row["status"], "fitted")
        self.assertNotEqual(row.get("lengthCheck"), "extended-observed-span")
        self.assertAlmostEqual(row.get("fittedLengthM", .28), .28, delta=.003)
        self.assertEqual(report["counts"]["extendedShortUnits"], 0)

    def test_clipped_long_tube_cannot_be_published_as_short_extension(self):
        long_tube, normals = tube([-.45, 0, 0], [.85, 0, 0], along=220, around=18)

        report, _, _ = self.fit(
            [long_tube], [normals], [([0, 0, 0], [.28, 0, 0])], ["short"]
        )

        row = report["instances"][0]
        self.assertNotEqual(row.get("lengthCheck"), "extended-observed-span")
        self.assertLessEqual(row.get("fittedLengthM", .28), .282)
        self.assertEqual(report["counts"]["extendedShortUnits"], 0)

    def test_confirmed_straight_surface_is_locked_before_short_recovery(self):
        straight, normals = tube([0, 0, 0], [.32, 0, 0], along=104, around=18)
        lines = [([0, 0, 0], [.32, 0, 0]),
                 ([0, .18, 0], [.28, .18, 0])]

        report, attrs, _ = self.fit(
            [straight], [normals], lines, ["straight", "short"]
        )

        first, second = report["instances"]
        self.assertEqual(first["status"], "fitted")
        self.assertNotEqual(second["status"], "fitted")
        self.assertGreater(np.mean(attrs["control_instance"] == first["id"]), .95)
        self.assertFalse(np.any(attrs["control_instance"] == second["id"]))

    def test_exact_cross_stage_design_identities_remain_ambiguous(self):
        short, normals = tube([0, 0, 0], [.28, 0, 0], along=96, around=18)
        same_line = ([0, 0, 0], [.28, 0, 0])

        report, attrs, _ = self.fit(
            [short], [normals], [same_line, same_line], ["short", "web"]
        )

        short_row, web_row = report["instances"]
        self.assertEqual(short_row["status"], "pending")
        self.assertEqual(web_row["status"], "pending")
        self.assertFalse(np.any(attrs["control_instance"]))
        self.assertTrue(np.all(attrs["control_status"] == 2))
        stages = report["policy"]["ownershipStages"]
        self.assertEqual([stage["kind"] for stage in stages], ["short", "web"])
        self.assertIsInstance(report["policy"]["ownershipPolicy"], str)
        self.assertTrue(report["policy"]["ownershipPolicy"])

    def test_contained_cross_stage_identity_does_not_let_long_bar_win(self):
        observed, normals = tube([0, 0, 0], [.40, 0, 0], along=112, around=18)
        lines = [([0, 0, 0], [.40, 0, 0]),
                 ([.06, 0, 0], [.34, 0, 0])]

        report, attrs, _ = self.fit(
            [observed], [normals], lines, ["straight", "short"]
        )

        straight_row, short_row = report["instances"]
        self.assertEqual(straight_row["status"], "pending")
        self.assertEqual(short_row["status"], "pending")
        self.assertFalse(np.any(attrs["control_instance"]))
        self.assertTrue(np.all(attrs["control_status"] == 2))

    def test_ambiguous_same_stage_identities_stay_unowned_while_web_fits(self):
        ambiguous, ambiguous_normals = tube([0, 0, 0], [.40, 0, 0], along=112, around=18)
        web, web_normals = tube([.20, .20, 0], [.20, .20, .26], along=92, around=18)
        lines = [([0, 0, 0], [.40, 0, 0]),
                 ([0, 0, 0], [.40, 0, 0]),
                 ([.20, .20, 0], [.20, .20, .26])]

        report, attrs, sizes = self.fit(
            [ambiguous, web], [ambiguous_normals, web_normals],
            lines, ["straight", "straight", "web"]
        )

        first, second, web_row = report["instances"]
        self.assertEqual(first["status"], "pending")
        self.assertEqual(second["status"], "pending")
        self.assertEqual(web_row["status"], "fitted")
        ambiguous_slice = slice(0, sizes[0])
        web_slice = slice(sizes[0], sizes[0]+sizes[1])
        self.assertFalse(np.any(attrs["control_instance"][ambiguous_slice]))
        self.assertTrue(np.all(attrs["control_status"][ambiguous_slice] == 2))
        self.assertGreater(np.mean(attrs["control_instance"][web_slice] == web_row["id"]), .90)


if __name__ == "__main__":
    unittest.main()
