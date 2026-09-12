import unittest
import json
from collections import Counter

import numpy as np

from algorithms.design_floating_zones import build_floating_zones, classify_floating_zones


def inventory(bars, units=None):
    return {"bars": bars, "units": units or [], "coverage": {"completeBars": len(bars)}}


def bar(identifier, points, coverage="complete"):
    return {"designBarId": identifier, "points": points, "radiusM": .006, "coverage": coverage, "source": "ifc-analytic"}


class DesignFloatingZonesTests(unittest.TestCase):
    def test_step05_candidates_include_frame_band_and_far_steel(self):
        from algorithms.shared_floating_noise import prepare_floating_scene
        from types import SimpleNamespace
        points = np.array([[.5, 0, 0], [.5, .1, 0], [100., 100., 100.], [.5, 0, -.1]])
        context = SimpleNamespace(positions=points, shared_table_mask=np.array([0, 0, 0, 1]),
            partition_zone=np.array([1, 2, 0, 0]), normals=np.tile([0., 1., 0.], (4, 1)),
            normal_valid=np.ones(4), scene_cache={})
        output = {key:np.zeros(4, np.uint8) for key in ['shared_layer','shared_floating_noise']}
        prepare_floating_scene(context, inventory([bar('rod', [[0, 0, 0], [1, 0, 0]])]), output=output)
        np.testing.assert_array_equal(context.shared_floating_noise, [0, 1, 1, 0])

    def test_default_cloth_expands_two_mm_without_changing_structural_bands(self):
        inv = inventory([bar('rod', [[0, 0, 0], [2, 0, 0]])])
        old = build_floating_zones(inv, params={'envelope_expansion_m': 0.})
        new = build_floating_zones(inv)
        self.assertTrue(old['enabled'])
        self.assertEqual(old['layers'], new['layers'])
        old_vertices = np.asarray(old['outerEnvelope']['verticesM'])
        vertices = np.asarray(new['outerEnvelope']['verticesM'])
        np.testing.assert_allclose(vertices.min(0), old_vertices.min(0)-.002, atol=1e-9)
        np.testing.assert_allclose(vertices.max(0), old_vertices.max(0)+.002, atol=1e-9)
        points = np.array([[1., .012, 0.], [1., 0., .011], [2.013, 0., 0.], [1., .014, 0.]])
        _, before = classify_floating_zones(points, old, eligible_mask=[True]*4)
        _, after = classify_floating_zones(points, new, eligible_mask=[True]*4)
        np.testing.assert_array_equal(before, [True, True, True, True])
        np.testing.assert_array_equal(after, [False, False, False, True])

    def test_candidates_require_eligibility_without_distance_exceptions(self):
        report = build_floating_zones(inventory([bar('a', [[0, 0, 0], [1, 0, 0]])]))
        points = np.array([[.5, 0, 0], [.5, .05, 0], [100, 100, 100]])
        for eligible in (None, [False, False, False]):
            _, rejected = classify_floating_zones(points, report, eligible_mask=eligible)
            self.assertEqual(rejected.tolist(), [False, False, False])
        _, rejected = classify_floating_zones(points, report, eligible_mask=[True]*3)
        self.assertEqual(rejected.tolist(), [False, True, True])
        report['forbiddenRule']['scope'] = 'eligible-floating-candidates'
        _, rejected = classify_floating_zones(points, report, eligible_mask=[True]*3)
        self.assertEqual(rejected.tolist(), [False, True, False])
        # Historical global-exclusion reports retain their persisted semantics.
        report['forbiddenRule']['scope'] = 'all-source-points'
        _, rejected = classify_floating_zones(points, report, eligible_mask=[False]*3)
        self.assertEqual(rejected.tolist(), [False, True, True])

    def test_parallel_layer_gaps_share_an_exact_plane_at_different_grid_sizes(self):
        inv = inventory([bar('lower', [[0, -.10, 0], [2, -.10, 0]]),
                         bar('a', [[0, -.08, .008], [2, -.08, .008]]),
                         bar('b', [[0, .08, .008], [2, .08, .008]])])
        for spacing in (.006, .035):
            report = build_floating_zones(inv, params={'envelope_grid_spacing_m': spacing})
            body = report['outerEnvelope']['body']
            vertices = np.asarray(body['verticesM'])[:body['upperVertexCount']]
            central = (vertices[:, 0] > .05) & (vertices[:, 0] < 1.95) & (np.abs(vertices[:, 1]) < .07)
            self.assertGreater(np.count_nonzero(central), 10)
            np.testing.assert_allclose(vertices[central, 2], .008+.006+.006, atol=1.e-9)
            _, rejected = classify_floating_zones(np.array([[1, 0, .020], [1, 0, .020002]]), report,
                                                 eligible_mask=[True, True])
            self.assertEqual(rejected.tolist(), [False, True])

    def test_bend_row_has_common_inner_and_outer_cloth_between_bars(self):
        source = np.array([[0, 0, 0], [1, 0, 0], [1, 0, .10], [.94, 0, .10], [.94, 0, .035]])
        bars = [bar(str(i), (source+[0, i*.1, 0]).tolist()) for i in range(3)]
        for item in bars: item['excludedHookRunCount'] = 1
        report = build_floating_zones(inventory(bars))
        curves = report['outerEnvelope']['curveShells']
        self.assertEqual(len(curves), 1)
        self.assertEqual(len(curves[0]['designBarIds']), 3)
        _, rejected = classify_floating_zones(np.array([[1, .05, .05], [.97, .05, .06], [.94, .05, .05]]),
                                             report, eligible_mask=[True]*3)
        self.assertEqual(rejected.tolist(), [False, True, False])

    def test_l_corner_has_orthogonal_sidewalls_and_does_not_bridge_the_notch(self):
        report = build_floating_zones(inventory([bar('L', [[0, 0, 0], [2, 0, 0], [2, 1, 0]])]))
        _, forbidden = classify_floating_zones(np.array([[.5, 0, 0], [2, .5, 0], [1.6, .5, 0]]),
                                               report, eligible_mask=[True, True, True])
        self.assertEqual(forbidden.tolist(), [False, False, True])
        body = report['outerEnvelope']['body']
        edges = Counter(tuple(sorted((a, b))) for face in body['trianglesXY']
                        for a, b in zip(face, face[1:]+face[:1]))
        xy = np.asarray(body['xyLocalM'])
        boundary = np.asarray([edge for edge, count in edges.items() if count == 1])
        delta = np.abs(xy[boundary[:, 0]]-xy[boundary[:, 1]])
        self.assertTrue(np.all(np.min(delta, axis=1) < 1.e-9), 'no diagonal footprint boundary may bridge the L corner')

    def test_lateral_clearance_is_seven_mm_from_the_physical_surface(self):
        report = build_floating_zones(inventory([bar('rod', [[0, 0, 0], [2, 0, 0]])]))
        _, forbidden = classify_floating_zones(np.array([[1, .006+.007, 0], [1, .006+.007+.000002, 0]]),
                                               report, eligible_mask=[True, True])
        self.assertEqual(forbidden.tolist(), [False, True])
        self.assertEqual(report['outerEnvelope']['parameters']['lateralAllowanceM'], .007)

    def test_composite_keeps_return_bend_gap_outside_even_above_body_footprint(self):
        hooked = bar('return', [[0, 0, 0], [1, 0, 0], [1, 0, .10], [.94, 0, .10], [.94, 0, .035]])
        hooked['excludedHookRunCount'] = 1
        report = build_floating_zones(inventory([hooked]))
        shell = report['outerEnvelope']
        self.assertIsNotNone(shell['body'])
        self.assertEqual(len(shell['curveShells']), 1)
        _, rejected = classify_floating_zones(np.array([[1, 0, .05], [.97, 0, .06], [.94, 0, .05]]),
                                             report, eligible_mask=[True, True, True])
        self.assertEqual(rejected.tolist(), [False, True, False], 'return-bend cavity must not become an XY height-field volume')

    def test_straight_exterior_end_overrun_keeps_vertical_clearance(self):
        report = build_floating_zones(inventory([bar('straight', [[0, 0, 0], [2, 0, 0]])]))
        _, rejected = classify_floating_zones(np.array([[2.010, 0, .008], [2.010, 0, .10], [2.025, 0, 0], [2.010, .010, .008]]),
                                             report, eligible_mask=[True, True, True, True])
        self.assertEqual(rejected.tolist(), [False, True, True, False])

    def test_cloth_is_closed_and_follows_web_peaks_without_cubes(self):
        inv = inventory([bar('web', [[0, 0, 0], [.5, 0, .2], [1, 0, 0], [1.5, 0, .2], [2, 0, 0]])])
        report = json.loads(json.dumps(build_floating_zones(inv), allow_nan=False))
        shell = report['outerEnvelope']
        self.assertEqual(shell['kind'], 'composite-steel-envelope')
        self.assertEqual(report['allowedEnvelopes'], [])
        self.assertEqual(report['forbiddenCells'], [])
        edges = Counter(tuple(sorted((a, b))) for face in shell['triangles']
                        for a, b in zip(face, face[1:]+face[:1]))
        self.assertTrue(all(n == 2 for n in edges.values()), 'every closed-surface edge must have two incident faces')
        xyz = np.asarray(shell['verticesM'])
        triangles = xyz[np.asarray(shell['triangles'])]
        volume = np.einsum('ij,ij->i', triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum()/6
        self.assertGreater(volume, 0, 'faces must consistently point outward')
        points = np.array([[.5, 0, .20], [1, 0, .16], [1.5, 0, .20]])
        _, rejected = classify_floating_zones(points, report, eligible_mask=np.ones(3, bool))
        self.assertEqual(rejected.tolist(), [False, True, False], 'roof rises at webs and falls in between')
        # A triangle centroid lies exactly on the displayed roof. The same
        # interpolation must accept it and reject a point directly above it.
        body = shell['body']
        face = np.asarray(body['trianglesXY'])[len(body['trianglesXY'])//2]
        centroid = xyz[face].mean(0)
        _, rejected = classify_floating_zones(np.array([centroid, centroid+[0, 0, .002]]), report, eligible_mask=[True, True])
        self.assertEqual(rejected.tolist(), [False, True])

    def test_outer_cloth_keeps_layer_gaps_and_planar_shifts_but_rejects_above_noise(self):
        inv = inventory([bar("lower", [[0, 0, 0], [2, 0, 0]]), bar("upper", [[0, 0, .20], [2, 0, .20]])], [
            {"designBarId": "lower", "ordinal": 0, "layerId": 1}, {"designBarId": "upper", "ordinal": 0, "layerId": 2}])
        report = build_floating_zones(inv)
        points = np.array([[1, .01, .006], [1, -.01, .195], [1, 0, .10], [1, 0, .38], [3, 0, 0]])
        layers, forbidden = classify_floating_zones(points, report, eligible_mask=np.ones(5, bool))
        self.assertEqual(layers[:2].tolist(), [1, 2])
        self.assertEqual(forbidden.tolist(), [False, False, False, True, True])
        self.assertAlmostEqual(report["layers"][0]["halfHeightM"], .010)

    def test_web_and_bent_curve_surface_are_retained_without_endpoint_hoods(self):
        inv = inventory([bar("bent", [[0, 0, 0], [1, 0, 0], [1, 0, .8]])], [
            {"designBarId": "bent", "ordinal": 0, "layerId": 1}, {"designBarId": "bent", "ordinal": 1, "layerId": 1}])
        report = build_floating_zones(inv)
        points = np.array([[1., .003, .45], [1.003, .003, .798], [1.08, .08, .88], [0., 0., .05], [1., .03, .75]])
        _, forbidden = classify_floating_zones(points, report, eligible_mask=np.ones(5, bool))
        self.assertEqual(forbidden.tolist(), [False, False, True, True, True], 'general XY tolerance must not create a wide hook slab')
        shell = report['outerEnvelope']
        self.assertEqual(shell['parameters']['hookProtection'], 'shared-inner-outer-bend-cloth')
        self.assertLessEqual(np.max(np.asarray(shell['verticesM'])[:, 2]), .808 + 1.e-9)

    def test_tight_defaults_reject_previous_unused_headroom(self):
        report = build_floating_zones(inventory([bar('rod', [[0, 0, 0], [2, 0, 0]])]))
        # 6 mm steel radius + 6 mm expanded height allowance; old 16 mm allowance and
        # 85 mm footprint would have retained both clearly separated points.
        _, rejected = classify_floating_zones(np.array([[1, 0, .009], [1, 0, .015], [1, .025, 0]]),
                                             report, eligible_mask=[True, True, True])
        self.assertEqual(rejected.tolist(), [False, True, True])

    def test_rigid_transform_and_unbounded_steel_scope(self):
        source = np.array([[0., 0., 0.], [2., 0., 0.]])
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        shift = np.array([4., -3., .5])
        transformed = source @ rotation.T + shift
        report = build_floating_zones(inventory([bar("x", transformed.tolist())]))
        points = np.array([transformed[0] * .5 + transformed[1] * .5 + np.array([-.01, 0., 0.]), shift + [3, 0, 0]])
        _, forbidden = classify_floating_zones(points, report, eligible_mask=[True, True])
        self.assertEqual(forbidden.tolist(), [False, True])
        self.assertIn("planes", report["reviewDomain"])
        self.assertEqual(report["forbiddenCells"], [])

    def test_partial_steel_is_protection_only_but_unresolved_brep_does_not_disable_complete_steel(self):
        complete = bar("steel", [[0, 0, 0], [1, 0, 0]])
        unresolved_fixture = {"designBarId": "fixture-brep", "boundsM": [[0, 0, 0], [1, 1, 1]], "radiusM": .2, "coverage": "unresolved"}
        report = build_floating_zones(inventory([complete, unresolved_fixture]))
        self.assertTrue(report["enabled"])
        partial = build_floating_zones(inventory([bar("partial", [[0, 1, 0], [1, 1, 0]], "partial")]))
        self.assertFalse(partial["enabled"])
        layers, forbidden = classify_floating_zones(np.array([[.5, 1, 0]]), partial, eligible_mask=[True])
        self.assertEqual((layers.tolist(), forbidden.tolist()), ([0], [False]))
        mixed = build_floating_zones(inventory([complete, bar("partial", [[0, .1, 0], [1, .1, 0]], "partial")]))
        self.assertTrue(mixed["enabled"])
        self.assertEqual(mixed["protectivePartialBarCount"], 1)
        self.assertEqual(len(mixed["designSegments"]), 2)

    def test_invalid_tolerances_fail_open_and_corner_is_protected(self):
        self.assertFalse(build_floating_zones(inventory([bar("x", [[0, 0, 0], [1, 0, 0]])]),
                                               params={"layer_half_height_m": -1})["enabled"])
        self.assertFalse(build_floating_zones(inventory([bar("x", [[0, 0, 0], [1, 0, 0]])]),
                                               params={"end_halo_m": float("nan")})["enabled"])
        report = build_floating_zones(inventory([bar("corner", [[0, 0, 0], [1, 0, 0], [1, 0, .8]])]))
        _, forbidden = classify_floating_zones(np.array([[1.003, .003, .06]]), report, eligible_mask=[True])
        self.assertEqual(forbidden.tolist(), [False])


if __name__ == "__main__":
    unittest.main()
