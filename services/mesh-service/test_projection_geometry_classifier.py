import unittest

import numpy as np

from algorithms.projection_geometry_classifier import classify_projection


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


class ProjectionGeometryClassifierTests(unittest.TestCase):
    def test_crossings_rejoin_without_absorbing_a_broad_fixture(self):
        horizontal = _horizontal_grid(-.18, .18, -.005, .005, .040, step=.001)
        vertical = _horizontal_grid(-.005, .005, -.18, .18, .048, step=.001)
        fixture = _horizontal_grid(.05, .12, -.035, .035, .040, step=.001)
        positions, normals, valid, _, groups = _scene(horizontal, vertical, fixture)
        _, _, output = classify_projection(positions, normals, valid)
        crossing = (np.abs(positions[:, 0]) < .01) & (np.abs(positions[:, 1]) < .01) & (positions[:, 2] > .02)
        self.assertGreater(np.mean(output['projection_class'][crossing] == 3), .95)
        self.assertGreater(np.mean(output['projection_class'][groups[2]] == 2), .90)

    def test_broad_table_thin_steel_and_fixture_surfaces_keep_their_classes(self):
        steel = _horizontal_grid(-.20, .20, -.162, -.158, .040)
        fixture_top = _horizontal_grid(.040, .110, .050, .120, .080)
        fixture_side = _vertical_wall(-.120, .050, .140, .025, .095)
        positions, normals, valid, table_ids, groups = _scene(steel, fixture_top, fixture_side)

        report, _, output = classify_projection(positions, normals, valid)
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
