import unittest

import numpy as np

from algorithms.projection_geometry_classifier import classify_projection, ProjectionParameters


def _horizontal_grid(x0, x1, y0, y1, z, step=.002):
    x = np.arange(x0, x1 + step / 2, step)
    y = np.arange(y0, y1 + step / 2, step)
    xx, yy = np.meshgrid(x, y)
    return np.column_stack((xx.ravel(), yy.ravel(), np.full(xx.size, z)))


def _vertical_wall(x, y0, y1, z0, z1, step=.002):
    y = np.arange(y0, y1 + step / 2, step)
    z = np.arange(z0, z1 + step / 2, step)
    yy, zz = np.meshgrid(y, z)
    return np.column_stack((np.full(yy.size, x), yy.ravel(), zz.ravel()))


def _scene(*parts):
    table = _horizontal_grid(-.24, .24, -.24, .24, 0., step=.004)
    positions = np.vstack((table,) + parts).astype(np.float64)
    normals = np.zeros_like(positions)
    normals[:, 2] = 1.
    valid = np.ones(len(positions), dtype=bool)
    groups = []
    start = len(table)
    for part in parts:
        groups.append(np.arange(start, start + len(part)))
        start += len(part)
    return positions, normals, valid, np.arange(len(table)), groups


def _rod(start, end, radius=.003):
    start, end = np.asarray(start), np.asarray(end)
    axis = (end-start)/np.linalg.norm(end-start)
    u = np.cross(axis, [0., 1., 0.]); u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    t = np.linspace(0, 1, int(np.linalg.norm(end-start)/.001)+1)
    angle = np.linspace(0, 2*np.pi, 16, endpoint=False)
    return (start+t[:, None, None]*(end-start)+radius*(np.cos(angle)[None, :, None]*u+
            np.sin(angle)[None, :, None]*v)).reshape(-1, 3)


class ProjectionGeometryClassifierTests(unittest.TestCase):
    def test_upper_height_keeps_touching_chords_above_a_broad_fixture(self):
        lower = _rod([-.18, -.15, .030], [.18, -.15, .030])
        top = _horizontal_grid(-.18, .18, -.009, .009, .100, step=.001)
        top_seed1 = _rod([-.18, -.10, .100], [.18, -.10, .100])
        top_seed2 = _rod([-.18, .10, .100], [.18, .10, .100])
        fixture = _horizontal_grid(-.08, .08, -.04, .04, .060, step=.002)
        side = _vertical_wall(.074, -.04, .04, .015, .060, step=.002)
        bottom_inside = _rod([-.06, 0., .030], [.06, 0., .030])
        p, n, valid, table, groups = _scene(lower, top, top_seed1, top_seed2, fixture, side, bottom_inside)
        n[len(table):] = [1., 0., 0.]
        old_report, _, before = classify_projection(p, n, valid, params=ProjectionParameters(retain_top_height=False, fixture_footprint_width=0))
        report, cache, after = classify_projection(p, n, valid)
        self.assertLess(np.mean(before['projection_class'][groups[1]] == 3), .2)
        self.assertTrue(np.all(after['projection_class'][groups[1]] == 3))
        bottom = old_report['bottomHeight']
        inside = groups[6][(p[groups[6], 2] >= bottom['lowM']) & (p[groups[6], 2] <= bottom['highM'])]
        self.assertTrue(np.all(before['projection_class'][inside] == 3))
        self.assertGreater(np.mean(after['projection_class'][groups[6]] == 2), .9)
        self.assertGreater(report['fixtureFootprint']['reclaimedPoints'], 0)
        self.assertGreater(report['topHeight']['recoveredPoints'], 0)
        self.assertTrue(np.all(after['projection_class'][cache['source_fixture_footprint']] == 2))
        self.assertTrue(np.all(after['projection_class'][cache['source_top_height_recovered']] == 3))
        self.assertTrue(np.all(cache['source_steel_evidence'][cache['source_top_height_recovered']]))
        self.assertTrue(np.any(cache['source_height_steel_evidence'] & ~cache['source_shape_steel_evidence']))
        np.testing.assert_array_equal(after['projection_layer'], before['projection_layer'])

    def test_bottom_height_overrides_width_and_fixture_edges_only_inside_band(self):
        lower = _rod([-.18, -.15, .030], [.18, -.15, .030])
        lower2 = _rod([-.18, -.10, .030], [.18, -.10, .030])
        broad = _horizontal_grid(-.16, .16, -.04, .04, .030, step=.002)
        rim = _horizontal_grid(-.15, .15, .050, .052, .030, step=.001)
        upper_fixture = _horizontal_grid(-.10, -.04, .05, .11, .080, step=.001)
        p, n, valid, table, groups = _scene(lower, lower2, broad, rim, upper_fixture)
        # Only the tabletop supplies upright normals to its independent fit.
        n[len(table):] = [1., 0., 0.]
        _, _, before = classify_projection(p, n, valid, params=ProjectionParameters(retain_bottom_height=False))
        report, cache, after = classify_projection(p, n, valid)
        band = report['bottomHeight']
        self.assertIsNotNone(band)
        inside = (p[:, 2] >= band['lowM']) & (p[:, 2] <= band['highM'])
        self.assertTrue(np.all(after['projection_class'][inside] == 3))
        self.assertTrue(np.all(before['projection_class'][groups[2]] == 2))
        self.assertTrue(np.all(after['projection_class'][groups[2]] == 3))
        self.assertGreater(band['overriddenFixtureEdgePoints'], 0)
        np.testing.assert_array_equal(after['projection_class'][~inside], before['projection_class'][~inside])
        np.testing.assert_array_equal(after['projection_layer'], before['projection_layer'])
        self.assertTrue(np.all(after['projection_class'][table] == 1))
        np.testing.assert_array_equal(cache['source_bottom_height_recovered'], (before['projection_class'] == 2) & (after['projection_class'] == 3))
        self.assertTrue(np.all(cache['source_steel_evidence'][cache['source_bottom_height_recovered']]))
        self.assertTrue(np.any(cache['source_height_steel_evidence'] & ~cache['source_shape_steel_evidence']))
        self.assertNotIn('subband_images', cache)

    def test_bent_web_apex_is_retained_without_requiring_a_vertical_tangent(self):
        t = np.linspace(-np.pi/2, np.pi/2, 220)
        center = np.column_stack((.07*np.sin(t), np.full(len(t), -.08), .04+.06*np.cos(t)))
        tangent = np.column_stack((.07*np.cos(t), np.zeros(len(t)), -.06*np.sin(t)))
        tangent /= np.linalg.norm(tangent, axis=1)[:, None]
        side = np.cross(tangent, [0., 1., 0.])
        angles = np.linspace(0, 2*np.pi, 16, endpoint=False)
        rod = (center[:, None]+.003*(np.cos(angles)[None, :, None]*side[:, None]+
               np.sin(angles)[None, :, None]*np.array([0., 1., 0.]))).reshape(-1, 3)
        fixture = _horizontal_grid(-.12, .12, -.13, -.03, .15, step=.001)
        p, n, valid, _, groups = _scene(rod, fixture)
        _, _, before = classify_projection(p, n, valid, params=ProjectionParameters(web_bend_reach=0))
        report, cache, after = classify_projection(p, n, valid)
        apex = groups[0][p[groups[0], 2] > .094]
        self.assertLess(np.mean(before['projection_class'][apex] == 3), .8)
        self.assertGreater(np.mean(after['projection_class'][apex] == 3), .95)
        self.assertGreater(np.mean(after['projection_class'][groups[1]] == 2), .99)
        self.assertGreater(report['multiview']['bendRecoveredPoints'], 0)
        self.assertTrue(np.all(after['projection_class'][cache['source_bend_recovered']] == 3))

    def test_fixture_face_claims_its_rim_but_not_a_bar_above_the_face(self):
        top = _horizontal_grid(-.16, .16, -.04, .04, .060, step=.0015)
        rim = _horizontal_grid(-.15, .15, .050, .052, .060, step=.001)
        bar = _rod([-.15, .052, .069], [.15, .052, .069])
        p, n, valid, _, groups = _scene(top, rim, bar)
        _, _, before = classify_projection(p, n, valid, params=ProjectionParameters(fixture_edge_reach=0))
        report, cache, after = classify_projection(p, n, valid)
        self.assertGreater(np.mean(before['projection_class'][groups[1]] == 3), .8)
        self.assertGreater(np.mean(after['projection_class'][groups[1]] == 2), .90)
        self.assertGreater(np.mean(after['projection_class'][groups[2]] == 3), .90)
        self.assertGreater(report['multiview']['fixtureEdgePoints'], 0)
        self.assertTrue(np.all(after['projection_class'][cache['source_fixture_edge']] == 2))

    def test_bend_growth_does_not_jump_to_a_disconnected_nearby_sliver(self):
        web = _rod([-.06, -.08, .025], [-.025, -.08, .13])
        sliver = _rod([-.038, -.058, .084], [-.008, -.058, .084], radius=.002)
        fixture = _horizontal_grid(-.11, .04, -.13, -.03, .16, step=.001)
        p, n, valid, _, groups = _scene(web, sliver, fixture)
        _, cache, result = classify_projection(p, n, valid)
        self.assertGreater(np.mean(result['projection_class'][groups[0]] == 3), .85)
        self.assertFalse(np.any(cache['source_bend_recovered'][groups[1]]))

    def test_inclined_web_hidden_under_broad_xy_projection_is_recovered(self):
        rod = _rod([-.06, -.08, .025], [-.025, -.08, .13])
        fixture = _horizontal_grid(-.11, .04, -.12, -.04, .16, step=.001)
        for angle in (0., 30., 90., 135.):
            with self.subTest(angle=angle):
                a = np.deg2rad(angle)
                rotation = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
                p, n, valid, table, groups = _scene(rod @ rotation.T, fixture @ rotation.T)
                _, _, baseline = classify_projection(p, n, valid, params=ProjectionParameters(side_views_deg=()))
                report, cache, result = classify_projection(p, n, valid, workers=2)
                self.assertLess(np.mean(baseline['projection_class'][groups[0]] == 3), .4)
                self.assertGreater(np.mean(result['projection_class'][groups[0]] == 3), .85)
                self.assertGreater(np.mean(result['projection_class'][groups[1]] == 2), .99)
                np.testing.assert_array_equal(result['projection_class'][table], 1)
                np.testing.assert_array_equal(result['projection_layer'], baseline['projection_layer'])
                self.assertEqual(report['multiview']['recoveredPoints'], int(cache['source_web_recovered'].sum()))

    def test_sliced_inclined_plate_does_not_become_a_web(self):
        # In a depth slab a diagonal plate can resemble a rod. The unsliced
        # neighbourhood must see the broad second dimension and reject it.
        x, y = np.meshgrid(np.arange(-.10, .10, .0015), np.arange(-.09, .09, .0015))
        plate = np.column_stack((x.ravel(), y.ravel(), .10+.5*x.ravel()))
        wall = _vertical_wall(.15, -.12, .12, .02, .16)
        p, n, valid, _, groups = _scene(plate, wall)
        report, _, result = classify_projection(p, n, valid)
        self.assertEqual(report['multiview']['recoveredPoints'], 0)
        for ids in groups:
            self.assertGreater(np.mean(result['projection_class'][ids] == 2), .99)

    def test_web_recovery_is_independent_of_workers_and_source_order(self):
        rod = _rod([-.06, -.08, .025], [-.025, -.08, .13])
        fixture = _horizontal_grid(-.11, .04, -.12, -.04, .16, step=.001)
        p, n, valid, _, _ = _scene(rod, fixture)
        _, cache, first = classify_projection(p, n, valid, workers=1)
        order = np.random.default_rng(11).permutation(len(p))
        _, shuffled_cache, second = classify_projection(p[order], n[order], valid[order], workers=2)
        np.testing.assert_array_equal(first['projection_class'][order], second['projection_class'])
        np.testing.assert_array_equal(cache['source_web_recovered'][order], shuffled_cache['source_web_recovered'])

    def test_crossings_rejoin_before_bottom_height_override(self):
        horizontal = _horizontal_grid(-.18, .18, -.005, .005, .040, step=.001)
        vertical = _horizontal_grid(-.005, .005, -.18, .18, .048, step=.001)
        fixture = _horizontal_grid(.05, .12, -.035, .035, .040, step=.001)
        positions, normals, valid, _, groups = _scene(horizontal, vertical, fixture)
        _, _, output = classify_projection(positions, normals, valid, params=ProjectionParameters(retain_bottom_height=False))
        crossing = (np.abs(positions[:, 0]) < .01) & (np.abs(positions[:, 1]) < .01) & (positions[:, 2] > .02)
        self.assertGreater(np.mean(output['projection_class'][crossing] == 3), .95)
        self.assertGreater(np.mean(output['projection_class'][groups[2]] == 2), .90)

    def test_geometry_classes_before_bottom_height_override(self):
        steel = _horizontal_grid(-.20, .20, -.162, -.158, .040)
        fixture_top = _horizontal_grid(.040, .110, .050, .120, .080)
        fixture_side = _vertical_wall(-.120, .050, .140, .025, .095)
        positions, normals, valid, table_ids, groups = _scene(steel, fixture_top, fixture_side)

        report, _, output = classify_projection(positions, normals, valid, params=ProjectionParameters(retain_bottom_height=False))
        labels = output["projection_class"]

        self.assertGreater(np.mean(labels[table_ids] == 1), .99)
        self.assertGreater(np.mean(labels[groups[0]] == 3), .90)
        self.assertGreater(np.mean(labels[groups[1]] == 2), .90)
        self.assertGreater(np.mean(labels[groups[2]] == 2), .90)
        self.assertEqual(sum(report["counts"].values()), len(positions))

    def test_three_native_height_bands_are_detected_but_never_forced(self):
        low_steel = _horizontal_grid(-.18, .18, -.002, .002, .035)
        high_steel = _horizontal_grid(-.18, .18, -.002, .002, .065)
        fixture_top = _horizontal_grid(-.060, .060, -.025, .025, .100)
        # A lone higher return makes the fixture peak an interior histogram
        # maximum without supplying enough evidence to invent another layer.
        high_outlier = np.array([[.220, .220, .125]])
        positions, normals, valid, _, groups = _scene(
            low_steel, high_steel, fixture_top, high_outlier
        )

        report, _, output = classify_projection(positions, normals, valid)
        heights = [layer["heightM"] for layer in report["layers"]]

        self.assertEqual(len(heights), 3)
        np.testing.assert_allclose(heights, [.035, .065, .100], atol=.001)
        self.assertFalse(report["diagnostics"]["forcedLayerCount"])
        self.assertGreater(np.mean(output["projection_class"][groups[0]] == 3), .85)
        self.assertGreater(np.mean(output["projection_class"][groups[1]] == 3), .85)
        self.assertGreater(np.mean(output["projection_class"][groups[2]] == 2), .90)

        two_band_positions, two_band_normals, two_band_valid, _, _ = _scene(
            low_steel, fixture_top, high_outlier
        )
        two_band_report, _, _ = classify_projection(
            two_band_positions, two_band_normals, two_band_valid
        )
        self.assertEqual(len(two_band_report["layers"]), 2)
        self.assertFalse(two_band_report["diagnostics"]["forcedLayerCount"])

        # The highest real surface remains detectable without an artificial
        # return above it to move its mode away from the histogram boundary.
        boundary_positions, boundary_normals, boundary_valid, _, _ = _scene(low_steel, high_steel, fixture_top)
        boundary_report, _, _ = classify_projection(boundary_positions, boundary_normals, boundary_valid)
        np.testing.assert_allclose([layer["heightM"] for layer in boundary_report["layers"]],
                                   [.035, .065, .100], atol=.001)

    def test_shared_xy_at_discrete_heights_differs_from_a_continuous_wall(self):
        # The wide top overlaps the central portion of both steel layers in XY.
        # Classification therefore has to retain height-band evidence instead of
        # assigning every source row the union projection's fixture class.
        low_steel = _horizontal_grid(-.18, .18, -.002, .002, .035)
        high_steel = _horizontal_grid(-.18, .18, -.002, .002, .065)
        fixture_top = _horizontal_grid(-.060, .060, -.025, .025, .100)
        wall = _vertical_wall(.140, -.060, .060, .025, .105)
        positions, normals, valid, _, groups = _scene(low_steel, high_steel, fixture_top, wall)

        _, cache, output = classify_projection(positions, normals, valid)
        labels = output["projection_class"]
        overlap = np.abs(positions[:, 0]) <= .055
        low_overlap = groups[0][overlap[groups[0]]]
        high_overlap = groups[1][overlap[groups[1]]]

        self.assertGreater(np.mean(labels[low_overlap] == 3), .80)
        self.assertGreater(np.mean(labels[high_overlap] == 3), .80)
        self.assertGreater(np.mean(labels[groups[2]] == 2), .90)
        self.assertGreater(np.mean(labels[groups[3]] == 2), .90)
        wall_pixels = cache["source_to_pixel"][groups[3]]
        self.assertTrue(np.all(cache["vertical"].ravel()[wall_pixels]))

    def test_source_rows_outputs_and_inputs_preserve_the_api_contract(self):
        steel = _horizontal_grid(-.19, .19, -.142, -.138, .045)
        fixture = _horizontal_grid(.050, .115, .040, .105, .085)
        positions, normals, valid, table_ids, groups = _scene(steel, fixture)
        order = np.random.default_rng(7).permutation(len(positions))
        positions = positions[order]
        normals = normals[order]
        valid = valid[order]
        before = (positions.copy(), normals.copy(), valid.copy())
        supplied = {
            "projection_class": np.full(len(positions), 255, np.uint8),
            "projection_layer": np.full(len(positions), 255, np.uint8),
        }

        report, _, returned = classify_projection(
            positions, normals, valid, output=supplied
        )

        self.assertIs(returned, supplied)
        self.assertIs(returned["projection_class"], supplied["projection_class"])
        self.assertIs(returned["projection_layer"], supplied["projection_layer"])
        self.assertFalse(np.any(returned["projection_class"] == 255))
        self.assertFalse(np.any(returned["projection_layer"] == 255))
        self.assertEqual(returned["projection_class"].dtype, np.uint8)
        self.assertEqual(returned["projection_layer"].dtype, np.uint8)
        self.assertTrue(np.all(np.isin(returned["projection_class"], [1, 2, 3])))
        np.testing.assert_array_equal(positions, before[0])
        np.testing.assert_array_equal(normals, before[1])
        np.testing.assert_array_equal(valid, before[2])

        original_kind = np.empty(len(order), np.uint8)
        original_kind[table_ids] = 1
        original_kind[groups[0]] = 3
        original_kind[groups[1]] = 2
        expected = original_kind[order]
        self.assertGreater(np.mean(returned["projection_class"] == expected), .95)
        expected_layer = np.zeros(len(positions), np.uint8)
        for layer in report["layers"]:
            expected_layer[
                (positions[:, 2] >= layer["lowM"])
                & (positions[:, 2] <= layer["highM"])
            ] = layer["id"]
        expected_layer[returned["projection_class"] == 1] = 0
        np.testing.assert_array_equal(returned["projection_layer"], expected_layer)
        self.assertEqual(
            report["independentInputs"],
            ["source XYZ", "Step 1 normals for tabletop fit only"],
        )

    def test_normal_sign_has_no_effect_and_only_table_has_no_layers(self):
        steel = _horizontal_grid(-.18, .18, -.152, -.148, .050)
        positions, normals, valid, _, _ = _scene(steel)
        report, _, positive = classify_projection(positions, normals, valid)
        negative_report, _, negative = classify_projection(positions, -normals, valid)
        np.testing.assert_array_equal(
            positive["projection_class"], negative["projection_class"]
        )
        np.testing.assert_array_equal(
            positive["projection_layer"], negative["projection_layer"]
        )
        self.assertEqual(report["layers"], negative_report["layers"])

        table = _horizontal_grid(-.24, .24, -.24, .24, 0., step=.004)
        table_normals = np.tile([0., 0., 1.], (len(table), 1))
        table_report, _, table_output = classify_projection(
            table, table_normals, np.ones(len(table), bool)
        )
        self.assertEqual(table_report["counts"]["table"], len(table))
        self.assertEqual(table_report["layers"], [])
        np.testing.assert_array_equal(
            table_output["projection_layer"], np.zeros(len(table), np.uint8)
        )


if __name__ == "__main__":
    unittest.main()
